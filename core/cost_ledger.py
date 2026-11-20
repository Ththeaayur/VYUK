"""MN-2.8 (v5.66, Money track): in-product cost ledger.

An append-only LOCAL SQLite ledger of per-turn brain spend. It
watches the USER's money; it is not build observability.

TELEMETRY-OFF BY CONSTRUCTION
------------------------------
This module performs no network I/O and imports no network
module (no socket, urllib, http, or requests at module level —
the suite asserts this from the module's own source). The
ledger file never leaves the machine: there is no upload, no
sync, and no export function here. Rows carry no prompt or
reply text — only brain/model/token counts/a rate snapshot/a
cost — so the ledger cannot leak conversation content even if
the file itself is copied.

STORAGE
-------
Default: ``<tree>/data/cost_ledger.sqlite3`` (the app's DATA_DIR),
overridable per ledger with ``db_path`` or the
``VYUK_COST_LEDGER_PATH`` environment variable. Tests MUST pass
an explicit tmp-dir path; nothing in this module writes to the
tree on import.

SCHEMA (one table, append-only)
--------------------------------
``turns``: id, ts (epoch seconds), day (YYYY-MM-DD, local),
month (YYYY-MM), brain ("live" | "cloud_text" | "local"),
model, prompt_tokens (NULL when unknown), completion_tokens
(NULL when unknown), measurement ("exact" | "estimated"),
rate_in_per_m, rate_out_per_m, rate_source, cost_usd (NULL
when no rate applies — e.g. a Live audio turn with no
usageMetadata — never a fabricated zero-cost claim).
Append-only is enforced in SQLite itself: BEFORE UPDATE and
BEFORE DELETE triggers abort. There is intentionally no
update/delete API.

EXACT vs ESTIMATED (house honesty rule)
---------------------------------------
A row is "exact" ONLY when the provider returned token counts
(Groq/OpenAI ``usage``, Gemini ``usageMetadata``, Ollama eval
counts). Otherwise the row is "estimated": token counts are
reconstructed with the chars/4 heuristic and the row SAYS SO.
Live audio turns with no usageMetadata carry NULL tokens and
NULL cost rather than a made-up number.

CAP LAYERS + 80%/100% LADDER
----------------------------
Three independent caps, all opt-in (None = uncapped, so the
default install behaves exactly as before): per-turn, daily,
monthly — all in USD. ``preflight()`` is the gate every cloud
call passes through:

* projected spend < 80% of the binding cap: allow.
* 80% <= spend < 100%: allow + warn (the caller surfaces the
  warning; the ledger only reports it).
* spend >= 100%: DEGRADE — the caller should switch to the
  cheap/local brain — and FAIL-CLOSED confirm: further cloud
  spend is refused unless ``confirm_callback`` (a caller-owned
  yes/no prompt, e.g. the GUI confirm gate) explicitly approves
  that spend. With no callback configured the answer is NO:
  refusing is the safe default, never an assumed yes.

The local brain always costs 0 and is never capped: degrade
always has somewhere safe to go.

RATES
-----
Rates are USD per million tokens, per model, set by the caller
via ``set_rate()`` (or the ledger's constructor). The built-in
``DEFAULT_RATES`` are FALLBACK PLACEHOLDERS, recorded with
``rate_source="default"`` — they exist so the ledger works
out of the box, and must be replaced with the provider's
current price sheet for real billing. Any row whose rate came
from a placeholder says so in its rate snapshot, so a
placeholder is never mistaken for a vendor quote.

Stdlib only. No new dependency, no requirements.txt change,
no new tool declaration: this is internal instrumentation.
"""

from __future__ import annotations

import json
import math
import os
from . import config_spec
import sqlite3
import time
from pathlib import Path
from typing import Any

__all__ = [
    "CostLedger",
    "DEFAULT_DB_FILENAME",
    "WARN_FRACTION",
    "CAP_FRACTION",
    "DEFAULT_RATES",
    "estimate_tokens",
    "extract_usage",
    "live_record_degraded_state",
    "note_live_record_failure",
    "note_record_failure",
    "record_cloud_text_usage",
    "record_live_usage",
    "record_local_usage",
    "reset_live_record_degraded",
]

DEFAULT_DB_FILENAME = "cost_ledger.sqlite3"
WARN_FRACTION = 0.80
CAP_FRACTION = 1.00

# Fallback placeholder rates (USD per million tokens), recorded
# with rate_source="default" — see the module docstring. These
# are NOT vendor quotes; replace them via set_rate() with the
# provider's current price sheet for real billing.
DEFAULT_RATES: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "gemini-live": (0.0, 0.0),
}

_BRAIN_CLOUD = ("live", "cloud_text")
_MEASUREMENTS = ("exact", "estimated")

# Field-check fix #2 (T7 #20): sticky record of cost rows
# lost to a ledger failure — same shape as the receipt degraded
# state (core/tool_receipts.py). A lost row means the cap totals
# undercount, so the loss must be visible (Activity feed marker,
# readable by the cap check) instead of swallowed by a bare
# `except: pass`. One record per brain ("live", "cloud_text",
# "local") — the Live hook noted its losses here first; C3-F6
# extends the same record to the local and cloud-text record
# sites. (The name keeps the original "live" — a test touches
# this dict directly.)
_LIVE_RECORD_FAILURES: dict[str, dict[str, Any]] = {}


def note_record_failure(brain: str, exc: BaseException) -> None:
    """Record one lost cost row for `brain`. Never raises: the
    note itself must not break the turn."""
    try:
        now = time.time()
        rec = _LIVE_RECORD_FAILURES.get(brain)
        if rec is None:
            rec = {"count": 0, "last_error": "", "first_at": now,
                   "last_at": now}
            _LIVE_RECORD_FAILURES[brain] = rec
        rec["count"] += 1
        rec["last_error"] = str(exc)[:200]
        rec["last_at"] = now
    except Exception:  # noqa: BLE001 - the note must never raise
        pass


def note_live_record_failure(exc: BaseException) -> None:
    """Record one lost Live cost row (field-check fix #2, T7 #20).
    Called by the Live cost hook when record_live_turn raises.
    Never raises: the note itself must not break the session."""
    note_record_failure("live", exc)


def live_record_degraded_state() -> dict[str, dict[str, Any]]:
    """A COPY of the sticky lost-cost-row record, keyed by
    brain ("live", "cloud_text", "local"). Empty when no cost
    row has ever been lost in this process — the Activity feed
    reads this, and the cap check can read it before trusting
    its totals."""
    return {kind: dict(rec)
            for kind, rec in _LIVE_RECORD_FAILURES.items()}


def reset_live_record_degraded() -> None:
    """Clear the sticky lost-Live-cost-row record. Tests only:
    production code never resets it (a lost row stays lost)."""
    _LIVE_RECORD_FAILURES.clear()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    month TEXT NOT NULL,
    brain TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    measurement TEXT NOT NULL,
    rate_in_per_m REAL,
    rate_out_per_m REAL,
    rate_source TEXT NOT NULL,
    cost_usd REAL
);
CREATE TRIGGER IF NOT EXISTS turns_no_update
BEFORE UPDATE ON turns BEGIN
    SELECT RAISE(ABORT, 'cost ledger is append-only');
END;
CREATE TRIGGER IF NOT EXISTS turns_no_delete
BEFORE DELETE ON turns BEGIN
    SELECT RAISE(ABORT, 'cost ledger is append-only');
END;
"""


def estimate_tokens(text: str) -> int:
    """Chars/4 token heuristic for reconstructed counts.

    Deliberately crude and always labelled "estimated" by its
    callers — never presented as a provider count."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _get(obj: Any, *names: str) -> Any:
    """One field off a dict or an object (SDK shapes vary)."""
    for name in names:
        if isinstance(obj, dict):
            if obj.get(name) is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return None


def extract_usage(usage: Any) -> tuple[int | None, int | None, str]:
    """Token counts out of a provider usage payload.

    Accepts Groq/OpenAI ``usage`` dicts (prompt_tokens /
    completion_tokens), Gemini ``usageMetadata`` dicts or
    objects (prompt_token_count / candidates_token_count /
    total_token_count), and Ollama chat dicts
    (prompt_eval_count / eval_count). Returns
    (prompt, completion, measurement); measurement is "exact"
    only when at least one real count was present, else the
    counts are None and measurement is "estimated" — the caller
    then fills estimates from text and keeps the label.
    """
    if usage is None:
        return None, None, "estimated"
    prompt = _get(usage, "prompt_tokens", "prompt_token_count",
                  "prompt_eval_count")
    completion = _get(usage, "completion_tokens",
                      "candidates_token_count", "eval_count")
    if prompt is None and completion is None:
        total = _get(usage, "total_tokens", "total_token_count")
        if total is None:
            return None, None, "estimated"
        # A total alone cannot be split honestly: keep both
        # counts NULL and let the caller estimate from text.
        return None, None, "estimated"
    try:
        prompt_i = int(prompt) if prompt is not None else None
    except (TypeError, ValueError):
        prompt_i = None
    try:
        completion_i = int(completion) if completion is not None else None
    except (TypeError, ValueError):
        completion_i = None
    if prompt_i is None and completion_i is None:
        return None, None, "estimated"
    return prompt_i, completion_i, "exact"


def _default_db_path() -> Path:
    """Ledger home: env override, else the app DATA_DIR, else a
    per-user local dir. Lazy (never at import) so importing
    this module never touches the tree or the config layer."""
    env = config_spec.env("VYUK_COST_LEDGER_PATH").strip()
    if env:
        return Path(env)
    try:
        from .config import DATA_DIR as _data_dir  # type: ignore

        return Path(_data_dir) / DEFAULT_DB_FILENAME
    except Exception:  # noqa: BLE001 — standalone use, no config
        return Path.home() / ".local" / "share" / "vyuk" / DEFAULT_DB_FILENAME


def _env_cap(name: str) -> float | None:
    raw = (config_spec.explicit(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class CostLedger:
    """Append-only per-turn cost ledger over one SQLite file."""

    def __init__(self, db_path: str | Path | None = None,
                 rates: dict[str, tuple[float, float]] | None = None,
                 daily_cap_usd: float | None = None,
                 monthly_cap_usd: float | None = None,
                 per_turn_cap_usd: float | None = None,
                 confirm_callback: Any = None):
        self.db_path = Path(db_path) if db_path is not None \
            else _default_db_path()
        self._rates: dict[str, tuple[float, float]] = dict(DEFAULT_RATES)
        if rates:
            self._rates.update(rates)
        self._rate_sources: dict[str, str] = {
            name: "default" for name in self._rates}
        if rates:
            for name in rates:
                self._rate_sources[name] = "caller"
        self.daily_cap_usd = daily_cap_usd
        self.monthly_cap_usd = monthly_cap_usd
        self.per_turn_cap_usd = per_turn_cap_usd
        # Caller-owned yes/no prompt for post-cap cloud spend.
        # None = fail closed (no approval ever assumed).
        self.confirm_callback = confirm_callback
        self._warned_days: set[str] = set()
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_env(cls, db_path: str | Path | None = None,
                 confirm_callback: Any = None) -> "CostLedger":
        """Ledger configured from VYUK_COST_* env vars (the seam
        the client hooks use; unset caps mean uncapped)."""
        return cls(
            db_path=db_path,
            daily_cap_usd=_env_cap("VYUK_COST_DAILY_CAP_USD"),
            monthly_cap_usd=_env_cap("VYUK_COST_MONTHLY_CAP_USD"),
            per_turn_cap_usd=_env_cap("VYUK_COST_PER_TURN_CAP_USD"),
            confirm_callback=confirm_callback,
        )

    # -- rates ---------------------------------------------------

    def set_rate(self, model: str, rate_in_per_m: float,
                 rate_out_per_m: float) -> None:
        """Record a caller-supplied (price-sheet) rate for a model."""
        self._rates[model] = (float(rate_in_per_m), float(rate_out_per_m))
        self._rate_sources[model] = "caller"

    def rate_for(self, model: str) -> tuple[float, float, str]:
        """(input rate, output rate, source) for a model; unknown
        models fall back to (0, 0, "unknown") — cost NULL, never
        a fabricated rate."""
        if model in self._rates:
            rate_in, rate_out = self._rates[model]
            return rate_in, rate_out, self._rate_sources.get(model, "default")
        return 0.0, 0.0, "unknown"

    # -- storage -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- recording ----------------------------------------------

    def record_turn(self, brain: str, model: str,
                    prompt_tokens: int | None = None,
                    completion_tokens: int | None = None,
                    measurement: str = "estimated",
                    cost_usd: float | None = None,
                    rate_in_per_m: float | None = None,
                    rate_out_per_m: float | None = None,
                    rate_source: str = "unknown",
                    ts: float | None = None) -> int:
        """Append one turn row; returns its id. No update or
        delete path exists (the triggers enforce it too)."""
        if brain not in ("live", "cloud_text", "local"):
            raise ValueError(f"unknown brain: {brain!r}")
        if measurement not in _MEASUREMENTS:
            raise ValueError(f"unknown measurement: {measurement!r}")
        stamp = time.time() if ts is None else float(ts)
        local = time.localtime(stamp)
        day = time.strftime("%Y-%m-%d", local)
        month = time.strftime("%Y-%m", local)
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO turns (ts, day, month, brain, model,"
            " prompt_tokens, completion_tokens, measurement,"
            " rate_in_per_m, rate_out_per_m, rate_source, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (stamp, day, month, brain, model, prompt_tokens,
             completion_tokens, measurement, rate_in_per_m,
             rate_out_per_m, rate_source, cost_usd))
        conn.commit()
        return int(cur.lastrowid)

    def record_from_usage(self, brain: str, model: str, usage: Any,
                          prompt_text: str = "",
                          completion_text: str = "") -> int:
        """Record a turn from a provider usage payload, or from
        text estimates when the provider gave no counts. The
        measurement label follows the payload, never the wish:
        no counts -> "estimated", even when the estimate looks
        precise. Local turns always cost 0."""
        prompt, completion, measurement = extract_usage(usage)
        if measurement == "estimated":
            prompt = estimate_tokens(prompt_text) if prompt_text else None
            completion = estimate_tokens(completion_text) \
                if completion_text else None
        if brain == "local":
            return self.record_turn(
                brain, model, prompt, completion, measurement,
                cost_usd=0.0, rate_in_per_m=0.0, rate_out_per_m=0.0,
                rate_source="local")
        rate_in, rate_out, source = self.rate_for(model)
        if source == "unknown" or prompt is None and completion is None:
            cost = None
        else:
            cost = ((prompt or 0) * rate_in
                    + (completion or 0) * rate_out) / 1_000_000.0
        return self.record_turn(
            brain, model, prompt, completion, measurement,
            cost_usd=cost, rate_in_per_m=rate_in,
            rate_out_per_m=rate_out, rate_source=source)

    def record_live_turn(self, model: str, usage: Any = None) -> int:
        """A Live audio turn: exact only when the server sent
        usageMetadata; otherwise NULL tokens / NULL cost, labelled
        estimated — audio is billed per minute, and this ledger
        invents no per-minute rate."""
        prompt, completion, measurement = extract_usage(usage)
        if measurement == "estimated":
            return self.record_turn(
                "live", model, None, None, "estimated",
                cost_usd=None, rate_in_per_m=None,
                rate_out_per_m=None, rate_source="unknown")
        rate_in, rate_out, source = self.rate_for(model)
        cost = ((prompt or 0) * rate_in
                + (completion or 0) * rate_out) / 1_000_000.0 \
            if source != "unknown" else None
        return self.record_turn(
            "live", model, prompt, completion, "exact",
            cost_usd=cost, rate_in_per_m=rate_in,
            rate_out_per_m=rate_out, rate_source=source)

    # -- totals ---------------------------------------------------

    def spend_day(self, day: str | None = None) -> float:
        """Cloud spend (live + cloud_text) for one local day."""
        if day is None:
            day = time.strftime("%Y-%m-%d", time.localtime())
        conn = self._connect()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM turns"
            " WHERE day = ? AND brain IN ('live', 'cloud_text')",
            (day,)).fetchone()
        return float(row[0])

    def spend_by_brain(self, day: str | None = None) -> list[dict[str, Any]]:
        """Cloud spend for one day, grouped by brain + model.

        RET-3: the Today surface's per-brain aggregates. Read-only
        (one SELECT, no write path touched). The filter matches
        spend_day() exactly — brain IN ('live', 'cloud_text') —
        so the rows always sum to spend_day(day); a brain with no
        turns that day simply has no row. `day` is an explicit
        "YYYY-MM-DD" (the caller computes the Asia/Kolkata day —
        the machine's localtime is UTC, so the no-arg default is
        the UTC day, not the digest's day). Returns a list of
        {brain, model, turns, cost_usd}, most expensive first.
        Never raises on a missing table (empty list)."""
        if day is None:
            day = time.strftime("%Y-%m-%d", time.localtime())
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT brain, model, COUNT(*),"
                " COALESCE(SUM(cost_usd), 0) FROM turns"
                " WHERE day = ? AND brain IN ('live', 'cloud_text')"
                " GROUP BY brain, model"
                " ORDER BY COALESCE(SUM(cost_usd), 0) DESC",
                (day,)).fetchall()
        except Exception:  # noqa: BLE001 — a missing table reads as empty
            return []
        return [{"brain": b, "model": m, "turns": int(n),
                 "cost_usd": float(c)}
                for b, m, n, c in rows]

    def spend_month(self, month: str | None = None) -> float:
        """Cloud spend for one local month (YYYY-MM)."""
        if month is None:
            month = time.strftime("%Y-%m", time.localtime())
        conn = self._connect()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM turns"
            " WHERE month = ? AND brain IN ('live', 'cloud_text')",
            (month,)).fetchone()
        return float(row[0])

    def recent_turns(self, limit=20) -> list[dict[str, Any]]:
        """The newest turns, newest-first, as plain dicts — the
        read side the cost GUI and the unified activity feed
        (core/activity_feed) render. Empty when the ledger has
        no turns yet; never raises on a missing table."""
        try:
            count = int(limit)
        except (TypeError, ValueError):
            count = 20
        if count <= 0:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, ts, day, month, brain, model,"
                " prompt_tokens, completion_tokens, measurement,"
                " rate_in_per_m, rate_out_per_m, rate_source,"
                " cost_usd FROM turns ORDER BY id DESC LIMIT ?",
                (count,)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{
            "id": r[0], "ts": r[1], "day": r[2], "month": r[3],
            "brain": r[4], "model": r[5],
            "prompt_tokens": r[6], "completion_tokens": r[7],
            "measurement": r[8], "rate_in_per_m": r[9],
            "rate_out_per_m": r[10], "rate_source": r[11],
            "cost_usd": r[12],
        } for r in rows]

    def summary(self) -> dict[str, Any]:
        """Spend + row counts, for display only (never leaves
        the machine through this module — it only returns it)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT brain, measurement, COUNT(*),"
            " COALESCE(SUM(cost_usd), 0) FROM turns"
            " GROUP BY brain, measurement").fetchall()
        return {
            "db_path": str(self.db_path),
            "spend_today_usd": self.spend_day(),
            "spend_month_usd": self.spend_month(),
            "daily_cap_usd": self.daily_cap_usd,
            "monthly_cap_usd": self.monthly_cap_usd,
            "by_brain": [
                {"brain": b, "measurement": m, "turns": n, "cost_usd": c}
                for b, m, n, c in rows],
        }

    # -- pre-flight + ladder --------------------------------------

    def estimate_cost(self, model: str, prompt_tokens: int,
                      completion_tokens: int) -> tuple[float, dict[str, Any]]:
        """Pre-flight estimate for a planned cloud turn: (cost,
        rate snapshot). Snapshot records the rate source, so a
        placeholder rate is visible as one."""
        rate_in, rate_out, source = self.rate_for(model)
        cost = (prompt_tokens * rate_in
                + completion_tokens * rate_out) / 1_000_000.0
        return cost, {
            "model": model,
            "rate_in_per_m": rate_in,
            "rate_out_per_m": rate_out,
            "rate_source": source,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    def preflight(self, brain: str, model: str,
                  prompt_tokens: int = 0,
                  completion_tokens: int = 0) -> dict[str, Any]:
        """The cap gate. Returns a dict with:

        * allowed (bool), action: "allow" | "warn" | "degrade" |
          "confirm" | "blocked"
        * degraded (bool): True once a cap is hit — the caller
          should route this turn to the cheap/local brain.
        * requires_confirm (bool): True at/over a cap for cloud
          brains — further cloud spend needs an explicit
          approval (see confirm_cloud_spend()).
        * warning (str | None), spend_today_usd,
          spend_month_usd, estimated_cost_usd, binding_cap_usd.

        Local brains bypass caps entirely (cost 0). With no
        caps configured this always allows — the default install
        is unchanged.
        """
        if brain == "local":
            return {
                "allowed": True, "action": "allow", "degraded": False,
                "requires_confirm": False, "warning": None,
                "spend_today_usd": self.spend_day(),
                "spend_month_usd": self.spend_month(),
                "estimated_cost_usd": 0.0, "binding_cap_usd": None,
            }
        est_cost, _snapshot = self.estimate_cost(
            model, prompt_tokens, completion_tokens)
        if self.per_turn_cap_usd is not None \
                and est_cost > self.per_turn_cap_usd:
            return {
                "allowed": False, "action": "blocked",
                "degraded": True, "requires_confirm": False,
                "warning": ("Estimated turn cost $%.4f exceeds the"
                            " per-turn cap $%.4f." % (
                                est_cost, self.per_turn_cap_usd)),
                "spend_today_usd": self.spend_day(),
                "spend_month_usd": self.spend_month(),
                "estimated_cost_usd": est_cost,
                "binding_cap_usd": self.per_turn_cap_usd,
            }
        today = self.spend_day()
        month = self.spend_month()
        # The tighter of the two caps (by fraction of cap the
        # projected spend reaches) binds; daily wins ties (it
        # is the nearer wall).
        binding: float | None = None
        projected = today
        if self.daily_cap_usd is not None:
            binding = self.daily_cap_usd
            projected = today + est_cost
        if self.monthly_cap_usd is not None:
            monthly_fraction = (month + est_cost) / self.monthly_cap_usd
            daily_fraction = projected / binding if binding else 0.0
            if binding is None or monthly_fraction > daily_fraction:
                binding = self.monthly_cap_usd
                projected = month + est_cost
        if binding is None:
            return {
                "allowed": True, "action": "allow", "degraded": False,
                "requires_confirm": False, "warning": None,
                "spend_today_usd": today,
                "spend_month_usd": month,
                "estimated_cost_usd": est_cost,
                "binding_cap_usd": None,
            }
        fraction = projected / binding
        if fraction >= CAP_FRACTION:
            return {
                "allowed": False, "action": "confirm",
                "degraded": True, "requires_confirm": True,
                "warning": ("Cost cap reached: projected spend"
                            " $%.4f meets the $%.4f cap — further"
                            " cloud spend needs explicit"
                            " confirmation." % (projected, binding)),
                "spend_today_usd": today,
                "spend_month_usd": month,
                "estimated_cost_usd": est_cost,
                "binding_cap_usd": binding,
            }
        if fraction >= WARN_FRACTION:
            return {
                "allowed": True, "action": "warn",
                "degraded": False, "requires_confirm": False,
                "warning": ("Cost warning: projected spend $%.4f"
                            " is %.0f%% of the $%.4f cap."
                            % (projected, fraction * 100, binding)),
                "spend_today_usd": today,
                "spend_month_usd": month,
                "estimated_cost_usd": est_cost,
                "binding_cap_usd": binding,
            }
        return {
            "allowed": True, "action": "allow", "degraded": False,
            "requires_confirm": False, "warning": None,
            "spend_today_usd": today,
            "spend_month_usd": month,
            "estimated_cost_usd": est_cost,
            "binding_cap_usd": binding,
        }

    def confirm_cloud_spend(self, warning: str = "") -> bool:
        """FAIL-CLOSED approval for post-cap cloud spend: True
        ONLY when a caller-owned confirm callback was configured
        AND it returns truthy for this warning. No callback, no
        approval — the spend stays refused. Exceptions from the
        callback are also a refusal (never an assumed yes)."""
        if self.confirm_callback is None:
            return False
        try:
            return bool(self.confirm_callback(warning))
        except Exception:  # noqa: BLE001 — a broken prompt refuses
            return False


# -- thin hook helpers (the three clients call these) -----------

def _hook_ledger(db_path: str | Path | None = None) -> CostLedger:
    if db_path is not None:
        return CostLedger(db_path=db_path)
    return CostLedger.from_env()


def record_cloud_text_usage(usage: Any, model: str,
                            prompt_text: str = "",
                            completion_text: str = "",
                            db_path: str | Path | None = None) -> int | None:
    """Cloud-text hook: one row per completed API call. Returns
    the row id, or None if recording itself failed (the ledger
    must never break a turn)."""
    try:
        ledger = _hook_ledger(db_path)
        row_id = ledger.record_from_usage(
            "cloud_text", model, usage, prompt_text, completion_text)
        ledger.close()
        return row_id
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        return None


def record_live_usage(usage: Any, model: str,
                      db_path: str | Path | None = None) -> int | None:
    """Live hook: one row per completed Live turn. No
    usageMetadata -> an estimated row with NULL tokens/cost."""
    try:
        ledger = _hook_ledger(db_path)
        row_id = ledger.record_live_turn(model, usage)
        ledger.close()
        return row_id
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        return None


def record_local_usage(usage: Any, model: str,
                       prompt_text: str = "",
                       completion_text: str = "",
                       db_path: str | Path | None = None) -> int | None:
    """Local hook: one zero-cost row per local turn."""
    try:
        ledger = _hook_ledger(db_path)
        row_id = ledger.record_from_usage(
            "local", model, usage, prompt_text, completion_text)
        ledger.close()
        return row_id
    except Exception:  # noqa: BLE001 — ledger failure never breaks a turn
        return None
