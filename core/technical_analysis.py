"""Technical analysis over operator-supplied OHLCV data (R18-8,
bukosabino/ta inspiration).

Parent-ruled (licence, 2026-11-12): bukosabino/ta (MIT, LICENSE from master,
1,096 bytes, 21 lines, "Copyright 2020 Darío López Padial
(Bukosabino)", read in full). Nothing is adopted — no `ta` code,
no pandas, no numpy, no library at all. The build takes only the
indicator shapes (the published formulas for the 25 indicators
below, across ta's five groups: Volume, Volatility, Trend,
Momentum, Others) as inspiration in own stdlib code, and drops
the pandas/numpy stack (the tree does not ship it), the remaining
18 of ta's 43 indicators, and every data source: there is NO live
price feed, NO yfinance fetch, NO market-data download and NO
network call anywhere in this module. The operator supplies the
candles (OHLCV rows they already have, as JSON or as a stored
dataset); VYUK only computes the indicators. The mutual_fund.py
no-fetch boundary is untouched.

A "signal" here is a mechanical reading of the indicator values
(RSI above 70, MACD above its signal line, price above the
Bollinger middle band) — it is never advice, never a
recommendation and never a prediction, and every signals answer
says so. VYUK does not tell the owner what to buy or sell.

One effectful tool, `technical_analysis`, free by design
("app-state", the event_planning / event_tickets precedent): it
writes only to VYUK's own store (datasets + hash-chained event
ledger in `technical_analysis.sqlite3` under DATA_DIR), and
`compute` / `indicators` over inline candles write nothing at
all. Every dataset write is re-read before the result is
claimed (A1-store), and the ledger chain verifies over the full
chain.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from . import store_base

__all__ = ["technical_analysis", "register_all"]

_MAX_CANDLES = 5000
_MAX_NAME = 80
_MAX_LIMIT = 100
_MAX_PARAMS_LEN = 2000

_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Candle parsing / validation
# ---------------------------------------------------------------------------

def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _parse_candles(raw):
    """Parse operator-supplied OHLCV rows into (candles, error).

    Accepts a JSON string or a Python list. Each row is either an
    object with open/high/low/close (+ optional volume, date) or a
    4/5-element array [open, high, low, close] / [..., volume].
    Returns (list_of_dicts, None) or (None, error_string).
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None, "candles is not valid JSON"
    else:
        data = raw
    if not isinstance(data, list):
        return None, "candles must be a JSON list of OHLCV rows"
    if not data:
        return None, "candles is empty — supply at least 2 rows"
    if len(data) > _MAX_CANDLES:
        return None, ("candles has %d rows, at most %d"
                      % (len(data), _MAX_CANDLES))
    candles = []
    for i, row in enumerate(data):
        if isinstance(row, dict):
            try:
                o = row["open"]
                h = row["high"]
                l = row["low"]
                c = row["close"]
            except KeyError:
                return None, ("row %d is missing open/high/low/close"
                              % i)
            v = row.get("volume", 0)
            date = row.get("date", "")
        elif isinstance(row, (list, tuple)) and len(row) in (4, 5):
            o, h, l, c = row[0], row[1], row[2], row[3]
            v = row[4] if len(row) == 5 else 0
            date = ""
        else:
            return None, ("row %d is not an OHLCV object or a "
                          "[open, high, low, close] array" % i)
        for label, value in (("open", o), ("high", h), ("low", l),
                             ("close", c), ("volume", v)):
            if not _is_finite_number(value):
                return None, ("row %d: %s is not a finite number"
                              % (i, label))
        o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)
        if v < 0:
            return None, "row %d: volume is negative" % i
        if h < l or h < o or h < c or l > o or l > c:
            return None, ("row %d: high/low do not bracket open/close "
                          "(high=%g low=%g open=%g close=%g)"
                          % (i, h, l, o, c))
        if not isinstance(date, str):
            return None, "row %d: date must be a string" % i
        candles.append({"open": o, "high": h, "low": l, "close": c,
                        "volume": v, "date": date[:40]})
    return candles, None


def _series(candles, key):
    return [row[key] for row in candles]


# ---------------------------------------------------------------------------
# Pure indicator math (stdlib only; no pandas, no numpy)
# ---------------------------------------------------------------------------

def _sma(values, window):
    out = [None] * len(values)
    if window < 1 or len(values) < window:
        return out
    total = sum(values[:window])
    out[window - 1] = total / window
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        out[i] = total / window
    return out


def _ema(values, window):
    """Standard EMA, k = 2/(window+1), seeded with the SMA of the
    first window (the `ta` convention)."""
    out = [None] * len(values)
    if window < 1 or len(values) < window:
        return out
    k = 2.0 / (window + 1)
    seed = sum(values[:window]) / window
    out[window - 1] = seed
    prev = seed
    for i in range(window, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _wma(values, window):
    out = [None] * len(values)
    if window < 1 or len(values) < window:
        return out
    denom = window * (window + 1) / 2.0
    for i in range(window - 1, len(values)):
        total = 0.0
        for j in range(window):
            total += values[i - window + 1 + j] * (j + 1)
        out[i] = total / denom
    return out


def _stdev(values, window):
    """Rolling sample standard deviation (ddof=1, the pandas
    rolling-std default `ta` uses)."""
    out = [None] * len(values)
    if window < 2 or len(values) < window:
        return out
    for i in range(window - 1, len(values)):
        window_vals = values[i - window + 1:i + 1]
        mean = sum(window_vals) / window
        var = sum((x - mean) ** 2 for x in window_vals) / (window - 1)
        out[i] = math.sqrt(var)
    return out


def _wilder(values, window):
    """Wilder's smoothing: first value is the SMA of the first
    window, then prev + (value - prev) / window."""
    out = [None] * len(values)
    if window < 1 or len(values) < window:
        return out
    seed = sum(values[:window]) / window
    out[window - 1] = seed
    prev = seed
    for i in range(window, len(values)):
        prev = prev + (values[i] - prev) / window
        out[i] = prev
    return out


def _rolling_extreme(values, window, want_max):
    out = [None] * len(values)
    if window < 1 or len(values) < window:
        return out
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1:i + 1]
        out[i] = max(chunk) if want_max else min(chunk)
    return out


def _rsi(closes, window=14):
    out = [None] * len(closes)
    if len(closes) < window + 1:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains[i] = change if change > 0 else 0.0
        losses[i] = -change if change < 0 else 0.0
    avg_gain = _wilder(gains[1:], window)
    avg_loss = _wilder(losses[1:], window)
    for j in range(len(avg_gain)):
        i = j + 1
        if avg_gain[j] is None or avg_loss[j] is None:
            continue
        if avg_loss[j] == 0:
            out[i] = 100.0 if avg_gain[j] > 0 else 50.0
        else:
            rs = avg_gain[j] / avg_loss[j]
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _true_range(highs, lows, closes):
    tr = [0.0] * len(closes)
    for i in range(len(closes)):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]))
    return tr


def _atr(highs, lows, closes, window=14):
    return _wilder(_true_range(highs, lows, closes), window)


def _macd(closes, fast=12, slow=26, signal=9):
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    line = [None] * len(closes)
    for i in range(len(closes)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            line[i] = fast_ema[i] - slow_ema[i]
    filled = [x if x is not None else 0.0 for x in line]
    sig = _ema(filled, signal)
    hist = [None] * len(closes)
    for i in range(len(closes)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def _bollinger(closes, window=20, mult=2.0):
    mid = _sma(closes, window)
    sd = _stdev(closes, window)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is not None and sd[i] is not None:
            upper[i] = mid[i] + mult * sd[i]
            lower[i] = mid[i] - mult * sd[i]
    return upper, mid, lower


def _keltner(highs, lows, closes, window=20, atr_window=10, mult=2.0):
    mid = _ema(closes, window)
    atr = _atr(highs, lows, closes, atr_window)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is not None and atr[i] is not None:
            upper[i] = mid[i] + mult * atr[i]
            lower[i] = mid[i] - mult * atr[i]
    return upper, mid, lower


def _donchian(highs, lows, window=20):
    upper = _rolling_extreme(highs, window, True)
    lower = _rolling_extreme(lows, window, False)
    mid = [None] * len(highs)
    for i in range(len(highs)):
        if upper[i] is not None and lower[i] is not None:
            mid[i] = (upper[i] + lower[i]) / 2.0
    return upper, mid, lower


def _adx(highs, lows, closes, window=14):
    n = len(closes)
    out = [None] * n
    if n < window + 1:
        return out
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr = _true_range(highs, lows, closes)
    tr_s = _wilder(tr[1:], window)
    plus_s = _wilder(plus_dm[1:], window)
    minus_s = _wilder(minus_dm[1:], window)
    dx = [None] * n
    for j in range(len(tr_s)):
        i = j + 1
        if tr_s[j] is None or tr_s[j] == 0:
            continue
        plus_di = 100.0 * plus_s[j] / tr_s[j]
        minus_di = 100.0 * minus_s[j] / tr_s[j]
        denom = plus_di + minus_di
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom
    dx_filled = [x if x is not None else 0.0 for x in dx]
    adx_s = _wilder(dx_filled, window)
    for i in range(n):
        if dx[i] is not None and adx_s[i] is not None:
            out[i] = adx_s[i]
    return out


def _cci(highs, lows, closes, window=20):
    n = len(closes)
    out = [None] * n
    if n < window:
        return out
    typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    sma_t = _sma(typical, window)
    for i in range(window - 1, n):
        chunk = typical[i - window + 1:i + 1]
        mean_dev = sum(abs(x - sma_t[i]) for x in chunk) / window
        if mean_dev == 0:
            out[i] = 0.0
        else:
            out[i] = (typical[i] - sma_t[i]) / (0.015 * mean_dev)
    return out


def _psar(highs, lows, step=0.02, max_af=0.2):
    """Parabolic SAR (Wilder). Returns the SAR series; the first
    two bars have no SAR."""
    n = len(highs)
    out = [None] * n
    if n < 2:
        return out
    up = _psar_seed_up(highs, lows)
    if up:
        sar = lows[0]
        ep = highs[0]
    else:
        sar = highs[0]
        ep = lows[0]
    af = step
    for i in range(1, n):
        prev_sar = sar
        if up:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, lows[i - 1])
            if i >= 2:
                sar = min(sar, lows[i - 2])
            if lows[i] < sar:
                up = False
                sar = ep
                ep = lows[i]
                af = step
                out[i] = sar
                continue
            if highs[i] > ep:
                ep = highs[i]
                af = min(af + step, max_af)
        else:
            sar = prev_sar + af * (ep - prev_sar)
            sar = max(sar, highs[i - 1])
            if i >= 2:
                sar = max(sar, highs[i - 2])
            if highs[i] > sar:
                up = True
                sar = ep
                ep = highs[i]
                af = step
                out[i] = sar
                continue
            if lows[i] < ep:
                ep = lows[i]
                af = min(af + step, max_af)
        out[i] = sar
    return out


def _psar_seed_up(highs, lows):
    return highs[1] >= highs[0]


def _aroon(highs, lows, window=25):
    n = len(highs)
    up = [None] * n
    down = [None] * n
    if n < window:
        return up, down
    for i in range(window - 1, n):
        chunk_h = highs[i - window + 1:i + 1]
        chunk_l = lows[i - window + 1:i + 1]
        bars_since_high = window - 1 - chunk_h.index(max(chunk_h))
        bars_since_low = window - 1 - chunk_l.index(min(chunk_l))
        up[i] = 100.0 * (window - bars_since_high) / window
        down[i] = 100.0 * (window - bars_since_low) / window
    return up, down


def _mfi(highs, lows, closes, volumes, window=14):
    n = len(closes)
    out = [None] * n
    if n < window + 1:
        return out
    typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    raw_flow = [typical[i] * volumes[i] for i in range(n)]
    pos = [0.0] * n
    neg = [0.0] * n
    for i in range(1, n):
        if typical[i] > typical[i - 1]:
            pos[i] = raw_flow[i]
        elif typical[i] < typical[i - 1]:
            neg[i] = raw_flow[i]
    for i in range(window, n):
        pos_sum = sum(pos[i - window + 1:i + 1])
        neg_sum = sum(neg[i - window + 1:i + 1])
        if neg_sum == 0:
            out[i] = 100.0 if pos_sum > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + pos_sum / neg_sum)
    return out


def _adi(highs, lows, closes, volumes):
    out = [0.0] * len(closes)
    running = 0.0
    for i in range(len(closes)):
        if highs[i] == lows[i]:
            mfm = 0.0
        else:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) \
                / (highs[i] - lows[i])
        running += mfm * volumes[i]
        out[i] = running
    return out


def _obv(closes, volumes):
    out = [0.0] * len(closes)
    running = 0.0
    for i in range(len(closes)):
        if i > 0:
            if closes[i] > closes[i - 1]:
                running += volumes[i]
            elif closes[i] < closes[i - 1]:
                running -= volumes[i]
        out[i] = running
    return out


def _cmf(highs, lows, closes, volumes, window=20):
    n = len(closes)
    out = [None] * n
    if n < window:
        return out
    mfv = [0.0] * n
    for i in range(n):
        if highs[i] == lows[i]:
            mfm = 0.0
        else:
            mfm = ((closes[i] - lows[i]) - (highs[i] - closes[i])) \
                / (highs[i] - lows[i])
        mfv[i] = mfm * volumes[i]
    for i in range(window - 1, n):
        vol_sum = sum(volumes[i - window + 1:i + 1])
        out[i] = 0.0 if vol_sum == 0 else \
            sum(mfv[i - window + 1:i + 1]) / vol_sum
    return out


def _vwap(highs, lows, closes, volumes):
    out = [None] * len(closes)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(len(closes)):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        cum_pv += typical * volumes[i]
        cum_v += volumes[i]
        out[i] = None if cum_v == 0 else cum_pv / cum_v
    return out


def _stoch(highs, lows, closes, window=14, smooth=3):
    n = len(closes)
    k = [None] * n
    if n < window:
        return k, [None] * n
    for i in range(window - 1, n):
        hh = max(highs[i - window + 1:i + 1])
        ll = min(lows[i - window + 1:i + 1])
        k[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
    filled = [x if x is not None else 50.0 for x in k]
    d = _sma(filled, smooth)
    for i in range(n):
        if k[i] is None:
            d[i] = None
    return k, d


def _stochrsi(closes, window=14, smooth=3):
    n = len(closes)
    rsi = _rsi(closes, window)
    k = [None] * n
    if n < window * 2:
        return k, [None] * n
    for i in range(window, n):
        chunk = [x for x in rsi[i - window + 1:i + 1] if x is not None]
        if len(chunk) < 2 or rsi[i] is None:
            continue
        lo = min(chunk)
        hi = max(chunk)
        k[i] = 50.0 if hi == lo else 100.0 * (rsi[i] - lo) / (hi - lo)
    filled = [x if x is not None else 50.0 for x in k]
    d = _sma(filled, smooth)
    for i in range(n):
        if k[i] is None:
            d[i] = None
    return k, d


def _williams_r(highs, lows, closes, window=14):
    n = len(closes)
    out = [None] * n
    if n < window:
        return out
    for i in range(window - 1, n):
        hh = max(highs[i - window + 1:i + 1])
        ll = min(lows[i - window + 1:i + 1])
        out[i] = -50.0 if hh == ll else -100.0 * (hh - closes[i]) / (hh - ll)
    return out


def _roc(closes, window=12):
    out = [None] * len(closes)
    for i in range(window, len(closes)):
        prev = closes[i - window]
        out[i] = 0.0 if prev == 0 else 100.0 * (closes[i] / prev - 1.0)
    return out


def _tsi(closes, long=25, short=13):
    n = len(closes)
    out = [None] * n
    if n < 2:
        return out
    mom = [0.0] * n
    abs_mom = [0.0] * n
    for i in range(1, n):
        mom[i] = closes[i] - closes[i - 1]
        abs_mom[i] = abs(mom[i])
    e1 = _ema(mom, long)
    e2 = _ema([x if x is not None else 0.0 for x in e1], short)
    a1 = _ema(abs_mom, long)
    a2 = _ema([x if x is not None else 0.0 for x in a1], short)
    for i in range(n):
        if e2[i] is not None and a2[i] is not None and a2[i] != 0:
            out[i] = 100.0 * e2[i] / a2[i]
    return out


def _daily_return(closes):
    out = [None] * len(closes)
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out[i] = 0.0 if prev == 0 else closes[i] / prev - 1.0
    return out


def _cumulative_return(closes):
    out = [None] * len(closes)
    if not closes or closes[0] == 0:
        return out
    for i in range(len(closes)):
        out[i] = closes[i] / closes[0] - 1.0
    return out


# Indicator catalogue: name -> (group, description, fn, defaults)
# fn takes (highs, lows, closes, volumes, params) and returns a
# dict of named series.
def _fn_sma(h, l, c, v, p):
    return {"value": _sma(c, p["window"])}


def _fn_ema(h, l, c, v, p):
    return {"value": _ema(c, p["window"])}


def _fn_wma(h, l, c, v, p):
    return {"value": _wma(c, p["window"])}


def _fn_macd(h, l, c, v, p):
    line, sig, hist = _macd(c, p["fast"], p["slow"], p["signal"])
    return {"macd": line, "signal": sig, "histogram": hist}


def _fn_adx(h, l, c, v, p):
    return {"value": _adx(h, l, c, p["window"])}


def _fn_cci(h, l, c, v, p):
    return {"value": _cci(h, l, c, p["window"])}


def _fn_psar(h, l, c, v, p):
    return {"value": _psar(h, l, p["step"], p["max_af"])}


def _fn_aroon(h, l, c, v, p):
    up, down = _aroon(h, l, p["window"])
    return {"aroon_up": up, "aroon_down": down}


def _fn_atr(h, l, c, v, p):
    return {"value": _atr(h, l, c, p["window"])}


def _fn_bollinger(h, l, c, v, p):
    upper, mid, lower = _bollinger(c, p["window"], p["mult"])
    return {"upper": upper, "middle": mid, "lower": lower}


def _fn_keltner(h, l, c, v, p):
    upper, mid, lower = _keltner(h, l, c, p["window"],
                                 p["atr_window"], p["mult"])
    return {"upper": upper, "middle": mid, "lower": lower}


def _fn_donchian(h, l, c, v, p):
    upper, mid, lower = _donchian(h, l, p["window"])
    return {"upper": upper, "middle": mid, "lower": lower}


def _fn_mfi(h, l, c, v, p):
    return {"value": _mfi(h, l, c, v, p["window"])}


def _fn_adi(h, l, c, v, p):
    return {"value": _adi(h, l, c, v)}


def _fn_obv(h, l, c, v, p):
    return {"value": _obv(c, v)}


def _fn_cmf(h, l, c, v, p):
    return {"value": _cmf(h, l, c, v, p["window"])}


def _fn_vwap(h, l, c, v, p):
    return {"value": _vwap(h, l, c, v)}


def _fn_rsi(h, l, c, v, p):
    return {"value": _rsi(c, p["window"])}


def _fn_stochrsi(h, l, c, v, p):
    k, d = _stochrsi(c, p["window"], p["smooth"])
    return {"k": k, "d": d}


def _fn_stoch(h, l, c, v, p):
    k, d = _stoch(h, l, c, p["window"], p["smooth"])
    return {"k": k, "d": d}


def _fn_williams_r(h, l, c, v, p):
    return {"value": _williams_r(h, l, c, p["window"])}


def _fn_roc(h, l, c, v, p):
    return {"value": _roc(c, p["window"])}


def _fn_tsi(h, l, c, v, p):
    return {"value": _tsi(c, p["long"], p["short"])}


def _fn_daily_return(h, l, c, v, p):
    return {"value": _daily_return(c)}


def _fn_cumulative_return(h, l, c, v, p):
    return {"value": _cumulative_return(c)}


INDICATORS = {
    # Volume (ta ids 1-9; 5 of 9 rebuilt)
    "mfi": ("Volume", "Money Flow Index — volume-weighted RSI; needs volume",
            _fn_mfi, {"window": 14}),
    "adi": ("Volume", "Accumulation/Distribution Index — cumulative money flow; needs volume",
            _fn_adi, {}),
    "obv": ("Volume", "On-Balance Volume — cumulative volume by up/down closes; needs volume",
            _fn_obv, {}),
    "cmf": ("Volume", "Chaikin Money Flow — money-flow volume over volume; needs volume",
            _fn_cmf, {"window": 20}),
    "vwap": ("Volume", "Volume Weighted Average Price — cumulative typical price weighted by volume; needs volume",
             _fn_vwap, {}),
    # Volatility (ta ids 10-14; 4 of 5 rebuilt)
    "atr": ("Volatility", "Average True Range — Wilder-smoothed true range",
            _fn_atr, {"window": 14}),
    "bollinger": ("Volatility", "Bollinger Bands — SMA middle with +/- mult * sample std bands",
                  _fn_bollinger, {"window": 20, "mult": 2.0}),
    "keltner": ("Volatility", "Keltner Channel — EMA middle with +/- mult * ATR bands",
                _fn_keltner, {"window": 20, "atr_window": 10, "mult": 2.0}),
    "donchian": ("Volatility", "Donchian Channel — rolling highest high / lowest low bands",
                 _fn_donchian, {"window": 20}),
    # Trend (ta ids 15-29; 8 of 15 rebuilt)
    "sma": ("Trend", "Simple Moving Average",
            _fn_sma, {"window": 20}),
    "ema": ("Trend", "Exponential Moving Average (seeded with the SMA of the first window)",
            _fn_ema, {"window": 20}),
    "wma": ("Trend", "Weighted Moving Average (linear weights)",
            _fn_wma, {"window": 20}),
    "macd": ("Trend", "Moving Average Convergence Divergence — fast EMA minus slow EMA, with signal line and histogram",
             _fn_macd, {"fast": 12, "slow": 26, "signal": 9}),
    "adx": ("Trend", "Average Directional Movement Index — Wilder-smoothed trend strength (0-100)",
            _fn_adx, {"window": 14}),
    "cci": ("Trend", "Commodity Channel Index — typical price vs its SMA, scaled by mean deviation",
            _fn_cci, {"window": 20}),
    "psar": ("Trend", "Parabolic Stop And Reverse — Wilder's trailing stop dots",
             _fn_psar, {"step": 0.02, "max_af": 0.2}),
    "aroon": ("Trend", "Aroon — bars since the rolling high / low, as 0-100",
              _fn_aroon, {"window": 25}),
    # Momentum (ta ids 30-40; 6 of 11 rebuilt)
    "rsi": ("Momentum", "Relative Strength Index — Wilder-smoothed gains vs losses (0-100)",
            _fn_rsi, {"window": 14}),
    "stochrsi": ("Momentum", "Stochastic RSI — RSI scaled into its own rolling range (0-100)",
                 _fn_stochrsi, {"window": 14, "smooth": 3}),
    "stoch": ("Momentum", "Stochastic Oscillator — close within its rolling high/low range, %K and %D",
              _fn_stoch, {"window": 14, "smooth": 3}),
    "williams_r": ("Momentum", "Williams %R — close within its rolling high/low range (-100 to 0)",
                   _fn_williams_r, {"window": 14}),
    "roc": ("Momentum", "Rate of Change — close vs close N bars ago, in percent",
            _fn_roc, {"window": 12}),
    "tsi": ("Momentum", "True Strength Index — double-smoothed momentum over double-smoothed absolute momentum",
            _fn_tsi, {"long": 25, "short": 13}),
    # Others (ta ids 41-43; 2 of 3 rebuilt)
    "daily_return": ("Others", "Daily Return — close vs previous close, as a fraction",
                    _fn_daily_return, {}),
    "cumulative_return": ("Others", "Cumulative Return — close vs first close, as a fraction",
                         _fn_cumulative_return, {}),
}


def _check_params(name, supplied):
    """Validate caller-supplied indicator params against the
    indicator's defaults. Returns (params, None) or (None, error)."""
    _group, _desc, _fn, defaults = INDICATORS[name]
    params = dict(defaults)
    if supplied is None or supplied == "":
        return params, None
    if isinstance(supplied, str):
        try:
            supplied = json.loads(supplied)
        except (ValueError, TypeError):
            return None, "params is not valid JSON"
    if not isinstance(supplied, dict):
        return None, "params must be a JSON object"
    for key, value in supplied.items():
        if key not in defaults:
            return None, ("unknown param %r for %s (known: %s)"
                          % (key, name, ", ".join(sorted(defaults))))
        default = defaults[key]
        if isinstance(default, float):
            if not _is_finite_number(value):
                return None, "param %r must be a number" % key
            value = float(value)
            if key == "mult" and not 0.1 <= value <= 10.0:
                return None, "param 'mult' must be between 0.1 and 10"
            if key in ("step", "max_af") and not 0.001 <= value <= 1.0:
                return None, ("param %r must be between 0.001 and 1"
                              % key)
        else:
            if not isinstance(value, int) or isinstance(value, bool):
                return None, "param %r must be a whole number" % key
            if not 2 <= value <= 500:
                return None, ("param %r must be between 2 and 500"
                              % key)
        params[key] = value
    if "fast" in params and "slow" in params \
            and params["fast"] >= params["slow"]:
        return None, "macd 'fast' must be smaller than 'slow'"
    return params, None


def _fmt(value):
    if value is None:
        return "—"
    return "%.4f" % float(value)


def _last(values):
    for value in reversed(values):
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Store: datasets + hash-chained ledger (one SQLite file, DATA_DIR)
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "technical_analysis.sqlite3"


class _TaStore(store_base.StoreBase):
    """Datasets + hash-chained event ledger. Open-per-operation;
    every write is followed by a re-read before the caller claims
    it (A1-store)."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()

    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS datasets ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT UNIQUE NOT NULL,"
            " candles TEXT NOT NULL,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL,"
            " kind TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " prev_hash TEXT NOT NULL,"
            " hash TEXT NOT NULL)")
        conn.commit()
        return conn


    # -- datasets ------------------------------------------------------
    def dataset_add(self, name, candles):
        with _LOCK:
            conn = self._connect()
            try:
                try:
                    conn.execute(
                        "INSERT INTO datasets (name, candles, created)"
                        " VALUES (?, ?, ?)",
                        (name, json.dumps(candles), time.time()))
                except sqlite3.IntegrityError:
                    # Duplicate name (UNIQUE) — the handler reports it.
                    conn.rollback()
                    return None
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, candles FROM datasets"
                    " WHERE name = ?", (name,)).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        stored = json.loads(row[2])
        if stored != candles:
            return None
        return {"id": row[0], "name": row[1], "rows": len(stored)}

    def dataset_list(self):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, candles FROM datasets"
                    " ORDER BY id").fetchall()
            finally:
                conn.close()
        return [{"id": r[0], "name": r[1], "rows": len(json.loads(r[2]))}
                for r in rows]

    def dataset_get(self, dataset_id):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, name, candles FROM datasets WHERE id = ?",
                    (dataset_id,)).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return {"id": row[0], "name": row[1],
                "candles": json.loads(row[2])}

    def dataset_remove(self, dataset_id):
        if self._missing():
            return False
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id FROM datasets WHERE id = ?",
                    (dataset_id,)).fetchone()
                if row is None:
                    return False
                conn.execute("DELETE FROM datasets WHERE id = ?",
                             (dataset_id,))
                conn.commit()
                gone = conn.execute(
                    "SELECT id FROM datasets WHERE id = ?",
                    (dataset_id,)).fetchone() is None
            finally:
                conn.close()
        return gone

    # -- ledger --------------------------------------------------------
    def append_event(self, kind, payload):
        with _LOCK:
            conn = self._connect()
            try:
                last = conn.execute(
                    "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                prev = last[0] if last else "0" * 64
                ts = time.time()
                digest = store_base.chain_digest(
                    "json_body_ts", prev,
                    {"ts": ts, "kind": kind, "payload": payload})
                conn.execute(
                    "INSERT INTO events (ts, kind, payload, prev_hash,"
                    " hash) VALUES (?, ?, ?, ?, ?)",
                    (ts, kind, json.dumps(payload), prev, digest))
                conn.commit()
                row = conn.execute(
                    "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
                    ).fetchone()
            finally:
                conn.close()
        return row is not None and row[0] == digest

    def list_events(self, limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload FROM events"
                    " ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            finally:
                conn.close()
        return [{"id": r[0], "ts": r[1], "kind": r[2],
                 "payload": json.loads(r[3])} for r in rows]

    def verify_chain(self):
        if self._missing():
            return True
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash FROM"
                    " events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        return store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)["ok"]


def _get_store():
    return _TaStore()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _clean_name(text, limit=_MAX_NAME):
    if not isinstance(text, str):
        return ""
    return " ".join(text.strip().split())[:limit]


def _coerce_int(value, default=0):
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_candles(dataset_id, candles_raw, store):
    """Candles come from a stored dataset (dataset_id) or inline
    JSON (candles). Returns (candles, source, error)."""
    if dataset_id:
        row = store.dataset_get(dataset_id)
        if row is None:
            return None, "", "no dataset %s" % dataset_id
        return row["candles"], "dataset %d (%s)" % (row["id"], row["name"]), None
    if candles_raw is None or candles_raw == "":
        return None, "", ("supply 'candles' (OHLCV rows as JSON) or "
                          "a stored 'dataset_id'")
    candles, error = _parse_candles(candles_raw)
    if error:
        return None, "", error
    return candles, "inline candles", None


def _compute_lines(name, series, candles, params, limit):
    lines = ["%s (%d candles, params %s):"
             % (name, len(candles),
                json.dumps(params, sort_keys=True))]
    for label, values in series.items():
        shown = [v for v in values if v is not None][-limit:]
        latest = _last(values)
        lines.append("  %s: latest %s; last %d: %s"
                     % (label, _fmt(latest), len(shown),
                        ", ".join(_fmt(v) for v in shown)))
    return lines


def technical_analysis(action: str = "", mode: str = "",
                       dataset_id: int = 0, name: str = "",
                       candles: str = "", indicator: str = "",
                       params: str = "", limit: int = 10,
                       detail: int = 0) -> str:
    """Technical analysis over operator-supplied OHLCV data.

    Actions: 'dataset' (add/list/get/remove a stored candle set),
    'indicators' (list the 25 indicators), 'compute' (run one
    indicator over a dataset or inline candles), 'signals'
    (mechanical readings of the indicator values — not advice),
    'status' (counts), 'events' (the hash-chained ledger).
    """
    store = _get_store()
    action = (action or "").strip().lower()
    mode = (mode or "").strip().lower()
    limit = _coerce_int(limit, 10)
    limit = max(1, min(_MAX_LIMIT, limit))

    if action == "indicators":
        lines = ["25 indicators (own stdlib code; the other 18 of "
                 "ta's 43 are not built):"]
        for group in ("Volume", "Volatility", "Trend", "Momentum",
                      "Others"):
            names = [n for n, (g, _d, _f, _p) in INDICATORS.items()
                     if g == group]
            lines.append("  %s (%d): %s"
                         % (group, len(names), ", ".join(sorted(names))))
        lines.append("Each takes optional 'params' JSON, e.g. "
                     "{\"window\": 14}. No live feed, no fetch, no "
                     "network — candles are always operator-supplied.")
        return "\n".join(lines)

    if action == "status":
        datasets = store.dataset_list()
        events = store.list_events(1)
        chain_ok = store.verify_chain()
        return ("technical_analysis: %d dataset(s), %d indicator(s), "
                "ledger %s (chain %s). No live feed — candles are "
                "always operator-supplied."
                % (len(datasets), len(INDICATORS),
                   "has entries" if events else "empty",
                   "OK" if chain_ok else "BROKEN"))

    if action == "events":
        events = store.list_events(limit)
        if not events:
            return "technical_analysis ledger is empty."
        lines = ["technical_analysis ledger (latest %d, chain %s):"
                 % (len(events),
                    "OK" if store.verify_chain() else "BROKEN")]
        for event in events:
            lines.append("  #%d %s %s"
                         % (event["id"], event["kind"],
                            json.dumps(event["payload"], sort_keys=True)))
        return "\n".join(lines)

    if action == "dataset":
        if mode == "add":
            clean = _clean_name(name)
            if not clean:
                return "dataset add needs a 'name'."
            candles, error = _parse_candles(candles)
            if error:
                return "dataset add refused: %s." % error
            added = store.dataset_add(clean, candles)
            if added is None:
                return ("dataset add failed: a dataset named %r may "
                        "already exist, or the write did not verify."
                        % clean)
            store.append_event("dataset_add",
                               {"id": added["id"], "name": clean,
                                "rows": added["rows"]})
            return ("dataset %d %r stored: %d candles (re-read "
                    "verified)."
                    % (added["id"], clean, added["rows"]))
        if mode == "list":
            datasets = store.dataset_list()
            if not datasets:
                return "no datasets stored yet."
            return "\n".join("  #%d %s (%d candles)"
                             % (d["id"], d["name"], d["rows"])
                             for d in datasets)
        if mode == "get":
            row = store.dataset_get(_coerce_int(dataset_id, 0))
            if row is None:
                return "no dataset %s." % dataset_id
            rows = row["candles"]
            first = rows[0]["date"] or "row 0"
            last = rows[-1]["date"] or "row %d" % (len(rows) - 1)
            return ("dataset #%d %r: %d candles, %s to %s, last close %s."
                    % (row["id"], row["name"], len(rows), first, last,
                       _fmt(rows[-1]["close"])))
        if mode == "remove":
            removed = store.dataset_remove(_coerce_int(dataset_id, 0))
            if not removed:
                return "no dataset %s to remove." % dataset_id
            store.append_event("dataset_remove",
                               {"id": _coerce_int(dataset_id, 0)})
            return "dataset %s removed." % dataset_id
        return ("dataset needs a mode: 'add' (name + candles JSON), "
                "'list', 'get' (dataset_id) or 'remove' (dataset_id).")

    if action == "compute":
        ind = (indicator or "").strip().lower()
        if ind not in INDICATORS:
            return ("compute needs a known 'indicator' — see the "
                    "'indicators' action (25 available, e.g. 'rsi', "
                    "'macd', 'bollinger').")
        params, error = _check_params(ind, params)
        if error:
            return "compute refused: %s." % error
        candles, source, error = _resolve_candles(
            _coerce_int(dataset_id, 0), candles, store)
        if error:
            return "compute refused: %s." % error
        group, _desc, fn, _defaults = INDICATORS[ind]
        try:
            series = fn(_series(candles, "high"), _series(candles, "low"),
                        _series(candles, "close"),
                        _series(candles, "volume"), params)
        except (ValueError, ZeroDivisionError, IndexError) as exc:
            return "compute failed on these candles: %s." % exc
        if all(_last(v) is None for v in series.values()):
            return ("%s needs more candles than the %d supplied "
                    "(warm-up with params %s)."
                    % (ind, len(candles),
                       json.dumps(params, sort_keys=True)))
        lines = _compute_lines(ind, series, candles, params, limit)
        lines.append("Source: %s. %s indicator."
                     % (source, group))
        return "\n".join(lines)

    if action == "signals":
        candles, source, error = _resolve_candles(
            _coerce_int(dataset_id, 0), candles, store)
        if error:
            return "signals refused: %s." % error
        if len(candles) < 30:
            return ("signals needs at least 30 candles, got %d."
                    % len(candles))
        highs = _series(candles, "high")
        lows = _series(candles, "low")
        closes = _series(candles, "close")
        volumes = _series(candles, "volume")
        rsi = _last(_rsi(closes, 14))
        macd_line, macd_sig, macd_hist = _macd(closes)
        macd_v, sig_v, hist_v = (_last(macd_line), _last(macd_sig),
                                 _last(macd_hist))
        upper, mid, lower = _bollinger(closes, 20, 2.0)
        last_close = closes[-1]
        band = None
        if _last(upper) is not None and _last(lower) is not None \
                and _last(upper) != _last(lower):
            band = 100.0 * (last_close - _last(lower)) \
                / (_last(upper) - _last(lower))
        sma20 = _last(_sma(closes, 20))
        sma50 = _last(_sma(closes, 50))
        atr = _last(_atr(highs, lows, closes, 14))
        vwap = _last(_vwap(highs, lows, closes, volumes))
        lines = ["Mechanical readings over %d candles (%s) — these "
                 "describe the indicator values only. They are NOT "
                 "advice, NOT a recommendation and NOT a prediction."
                 % (len(candles), source)]
        if rsi is None:
            lines.append("  RSI(14): not enough candles yet.")
        elif rsi > 70:
            lines.append("  RSI(14) is %.2f — above the usual 70 "
                         "overbought line." % rsi)
        elif rsi < 30:
            lines.append("  RSI(14) is %.2f — below the usual 30 "
                         "oversold line." % rsi)
        else:
            lines.append("  RSI(14) is %.2f — between 30 and 70."
                         % rsi)
        if macd_v is not None and sig_v is not None:
            side = "above" if macd_v > sig_v else "below"
            lines.append("  MACD is %s its signal line (MACD %s, "
                         "signal %s, histogram %s)."
                         % (side, _fmt(macd_v), _fmt(sig_v),
                            _fmt(hist_v)))
        if band is not None:
            where = ("above the upper band" if band > 100 else
                     "below the lower band" if band < 0 else
                     "inside the bands")
            lines.append("  Close is %s (%%B %.1f%%, middle band %s)."
                         % (where, band, _fmt(_last(mid))))
        if sma20 is not None and sma50 is not None:
            side = "above" if sma20 > sma50 else "below"
            lines.append("  SMA(20) is %s SMA(50) (%s vs %s)."
                         % (side, _fmt(sma20), _fmt(sma50)))
        if atr is not None:
            lines.append("  ATR(14) is %s (average true range)."
                         % _fmt(atr))
        if vwap is not None:
            side = "above" if last_close > vwap else "below"
            lines.append("  Close %s is %s VWAP (%s)."
                         % (_fmt(last_close), side, _fmt(vwap)))
        return "\n".join(lines)

    return ("technical_analysis needs an action: 'dataset', "
            "'indicators', 'compute', 'signals', 'status' or "
            "'events'.")


def register_all(registry) -> None:
    """Register the technical-analysis pack (R18-8, bukosabino/ta
    inspiration): technical_analysis — 25 indicators over
    operator-supplied OHLCV candles (free by design, "app-state"
    — it writes only to VYUK's own dataset + ledger store, the
    event_planning / event_tickets precedent). Own stdlib code
    over the published indicator formulas; nothing adopted — no
    `ta` code, no pandas, no numpy, no live feed, no yfinance,
    no network. The module owns its catalogue via register_all
    (same pattern as the other packs)."""
    registry.register(
        name="technical_analysis",
        description=(
            "Technical analysis over OHLCV candles the operator "
            "supplies: 25 indicators (RSI, MACD, Bollinger Bands, "
            "ATR, SMA/EMA/WMA, ADX, CCI, PSAR, Aroon, MFI, OBV, "
            "CMF, VWAP, Stochastic, Williams %R, ROC, TSI and "
            "returns) computed in own stdlib code, plus mechanical "
            "readings of the values. Store candle sets as datasets, "
            "compute any indicator over a dataset or inline candles, "
            "and read the signals action for plain descriptions of "
            "what the indicators say. There is no live price feed "
            "and no market-data download — the candles always come "
            "from the operator — and nothing here is advice: the "
            "readings describe indicator values only, never what to "
            "buy or sell. Use when the user wants indicator values "
            "for price data they already have."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'dataset' (stored candle sets), 'indicators' (list the 25 indicators), 'compute' (run one indicator), 'signals' (mechanical readings of the indicator values — not advice), 'status' (counts) or 'events' (the ledger)"},
                "mode": {"type": "string",
                         "description": "With 'dataset', one of 'add', 'list', 'get' or 'remove'"},
                "dataset_id": {"type": "integer",
                               "description": "With 'dataset' get/remove or 'compute'/'signals', the stored dataset's number"},
                "name": {"type": "string",
                         "description": "With 'dataset' add, the dataset's name (at most 80 chars)"},
                "candles": {"type": "string",
                            "description": "With 'dataset' add or 'compute'/'signals', the OHLCV rows as JSON — objects with open/high/low/close (+ optional volume, date) or [open, high, low, close] arrays, at most 5000 rows"},
                "indicator": {"type": "string",
                              "description": "With 'compute', the indicator name, e.g. 'rsi', 'macd', 'bollinger' (see 'indicators')"},
                "params": {"type": "string",
                           "description": "With 'compute', indicator params as JSON, e.g. '{\"window\": 14}' or '{\"fast\": 12, \"slow\": 26, \"signal\": 9}'"},
                "limit": {"type": "integer",
                          "description": "With 'compute' or 'events', most values/events shown (10 if left out, max 100)"},
                "detail": {"type": "integer",
                           "description": "Unused, kept for the house parameter shape"},
            },
            "required": ["action"],
        },
        handler=technical_analysis,
    )
