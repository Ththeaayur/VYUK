"""
Health-smell rules (v5.56, Wave 2 NO-33): per-signal baselines +
named rules for VYUK's proven silent-failure classes.

Detection layer on OB-1's plumbing — ONE item, not two (plan
§6 dedup D-8): these rules consume the telemetry funnel's
delivery accounting (``feed_funnel_result`` in core/telemetry
forwards funnel returns here) and watcher history; they are NOT
a second telemetry system, they emit no spans, and they open no
exporter or socket.

Rules (each returns findings, never user pages):
  * ``heartbeat-gap`` — gaps between watcher heartbeats that
    blow past the signal's own baseline (rolling median/MAD).
  * ``mic-silence-mid-session`` — a silence streak DURING an
    active session that blows past its baseline. Silence outside
    a session is normal and never fires.
  * ``claim-without-delivery`` — a success claim ("done",
    "sent", "saved", ...) in a turn where the funnel delivered
    NO successful tool result. A delivered refusal does NOT
    satisfy a claim: the action did not happen.

Findings are AUDIT EVENTS, not user pages: ``append_findings``
writes them as JSON lines to a local findings file (default
``data/health_findings.jsonl``) for the doctor/dev surfaces to
read. Nothing here notifies, pages, or interrupts the user.

Baselines: rolling median + MAD (median absolute deviation)
over each signal's recent history (default window 100). A
value is anomalous only when it exceeds BOTH
``median + 5*MAD`` AND ``2*median`` — the double bar keeps one
noisy sample from firing and keeps a flat baseline (MAD = 0)
from firing on trivial drift. With fewer than 5 samples there
is no baseline and NO finding is raised: an unbaselined guess
is worse than silence, and the finding file says so by its
absence (the monitor reports sample counts via ``status()``).

Zero dependencies. Pure standard library. Every public
function tolerates bad input (non-numeric samples are ignored,
never raised) because the funnel must never break on health.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import threading

# Minimum samples before a baseline exists (see module docstring).
_MIN_BASELINE_SAMPLES = 5

# Words that claim an action COMPLETED (word-boundary matched,
# lowercased). Kept deliberately narrow: a claim must assert
# completion, not intention ("will send", "trying to save" do
# not match — "will" is not in the set and the match is on the
# claim word itself, checked by the caller passing claim text).
_CLAIM_WORDS = (
    "done", "sent", "saved", "booked", "deleted", "ordered",
    "completed", "finished", "uploaded", "downloaded",
    "installed", "turned on", "turned off", "enabled",
    "disabled", "created", "removed", "moved", "copied",
)

_CLAIM_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _CLAIM_WORDS) + r")\b")

_lock = threading.Lock()
_monitor = None


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def median(values) -> float | None:
    """Median of the numeric values, or None when empty."""
    nums = sorted(v for v in values
                  if isinstance(v, (int, float)) and v == v)
    if not nums:
        return None
    n = len(nums)
    mid = n // 2
    if n % 2:
        return float(nums[mid])
    return (nums[mid - 1] + nums[mid]) / 2.0


def mad(values, med: float | None) -> float | None:
    """Median absolute deviation around ``med``, or None."""
    if med is None:
        return None
    devs = [abs(v - med) for v in values
            if isinstance(v, (int, float)) and v == v]
    if not devs:
        return None
    return median(devs)


class SignalHistory:
    """Rolling window of one signal's samples + its baseline."""

    def __init__(self, window: int = 100) -> None:
        self._window = max(1, int(window))
        self._samples: list[float] = []

    def append(self, value) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if value != value:  # NaN never enters a baseline
            return
        self._samples.append(float(value))
        del self._samples[: -self._window]

    def __len__(self) -> int:
        return len(self._samples)

    def baseline(self):
        """(median, mad, n) when baselined, else None.

        None means "not enough history to judge" — callers must
        NOT fire on None; they return no finding.
        """
        if len(self._samples) < _MIN_BASELINE_SAMPLES:
            return None
        med = median(self._samples)
        return med, mad(self._samples, med), len(self._samples)


def _anomalous(value: float, baseline) -> bool:
    """Double-bar anomaly test (see module docstring)."""
    if baseline is None:
        return False
    med, dev, _n = baseline
    if med is None or dev is None:
        return False
    return value > med + 5.0 * dev and value > 2.0 * med


def finding(rule: str, signal: str, observed, baseline,
            detail: str, severity: str = "warning") -> dict:
    """One audit-event finding (plain dict, JSON-serialisable)."""
    med, dev, n = baseline if baseline is not None else (None, None, 0)
    return {
        "ts": _utcnow_iso(),
        "rule": rule,
        "signal": signal,
        "observed": observed,
        "baseline_median": med,
        "baseline_mad": dev,
        "baseline_n": n,
        "severity": severity,
        "detail": detail,
    }


def check_heartbeat_gap(history: SignalHistory,
                        gap_seconds) -> dict | None:
    """Heartbeat-gap rule: ``gap_seconds`` since the last beat.

    The gap is APPENDED to history only by the caller after the
    check (a gap under test must not baseline itself). Fires
    when the gap is anomalous against past gaps.
    """
    if not isinstance(gap_seconds, (int, float)) or gap_seconds != gap_seconds:
        return None
    base = history.baseline()
    if not _anomalous(float(gap_seconds), base):
        return None
    return finding(
        "heartbeat-gap", "heartbeat_gap_seconds", float(gap_seconds),
        base,
        "Heartbeat gap exceeds its own baseline: the watchdog "
        "may have stalled or the session may have hung. Audit "
        "event only — no user page.")


def check_mic_silence(history: SignalHistory, silence_seconds,
                      session_active: bool) -> dict | None:
    """Mic-silence-mid-session rule.

    ``session_active`` False means silence is expected (idle
    mic) and the rule NEVER fires, whatever the streak. When a
    session is active, a silence streak anomalous against past
    streaks fires — the classic silent-mic failure: the user is
    talking to a session that hears nothing.
    """
    if not session_active:
        return None
    if not isinstance(silence_seconds, (int, float)) \
            or silence_seconds != silence_seconds:
        return None
    base = history.baseline()
    if not _anomalous(float(silence_seconds), base):
        return None
    return finding(
        "mic-silence-mid-session", "mic_silence_seconds",
        float(silence_seconds), base,
        "Microphone silent well past its baseline DURING an "
        "active session. Audit event only — no user page.")


def is_claim(text: str) -> bool:
    """True when ``text`` asserts a completed action."""
    if not isinstance(text, str):
        return False
    return _CLAIM_RE.search(text.lower()) is not None


class ClaimTracker:
    """Claim-without-delivery within one turn window.

    The funnel feeds deliveries (``note_delivery``); the
    claim source (brain output text — wired by a later wave,
    see the coverage seam in the handoff) feeds claims
    (``note_claim``). ``close_turn`` evaluates the window: any
    claim in a turn with ZERO successful deliveries is a
    finding, and the window resets. Delivered refusals/errors
    do not satisfy claims — only ``ok=True`` deliveries do.
    """

    def __init__(self) -> None:
        self._claims: list[str] = []
        self._ok_deliveries = 0
        self._any_delivery = False

    def note_claim(self, text: str) -> None:
        if is_claim(text):
            self._claims.append(text[:200])

    def note_delivery(self, ok: bool) -> None:
        self._any_delivery = True
        if ok:
            self._ok_deliveries += 1

    def close_turn(self) -> list[dict]:
        claims, ok = self._claims, self._ok_deliveries
        self._claims, self._ok_deliveries = [], 0
        self._any_delivery = False
        if not claims or ok > 0:
            return []
        return [finding(
            "claim-without-delivery", "claimed_actions", len(claims),
            None,
            "Success claimed with no successful tool result "
            f"delivered this turn: {claims[0]!r} "
            f"({len(claims)} claim(s)). Audit event only.",
            severity="warning")]


class HealthMonitor:
    """Process-wide monitor: histories + claim tracker + findings.

    ``get_monitor()`` returns the singleton the funnel hook
    feeds. ``drain_findings()`` returns accumulated findings
    and clears them (the doctor surface drains, it never polls
    a growing list). Thread-safe for funnel-thread feeding.
    """

    def __init__(self) -> None:
        self.heartbeat_gaps = SignalHistory()
        self.mic_silences = SignalHistory()
        self.claims = ClaimTracker()
        self._findings: list[dict] = []
        self._last_heartbeat_at: float | None = None

    # -- feeds (called from the funnel hook / watchers) --------
    def note_heartbeat(self, now: float | None = None) -> None:
        """Record a heartbeat; a gap finding fires on the gap."""
        import time as _time
        now = _time.monotonic() if now is None else now
        if self._last_heartbeat_at is not None:
            gap = now - self._last_heartbeat_at
            hit = check_heartbeat_gap(self.heartbeat_gaps, gap)
            if hit is not None:
                with _lock:
                    self._findings.append(hit)
            self.heartbeat_gaps.append(gap)
        self._last_heartbeat_at = now

    def note_mic_silence(self, silence_seconds,
                         session_active: bool) -> None:
        hit = check_mic_silence(self.mic_silences, silence_seconds,
                                session_active)
        if hit is not None:
            with _lock:
                self._findings.append(hit)
        self.mic_silences.append(silence_seconds)

    def note_claim(self, text: str) -> None:
        self.claims.note_claim(text)

    def note_delivery(self, ok: bool) -> None:
        self.claims.note_delivery(ok)

    def close_turn(self) -> list[dict]:
        hits = self.claims.close_turn()
        if hits:
            with _lock:
                self._findings.extend(hits)
        return hits

    # -- reads ---------------------------------------------------
    def findings(self) -> list[dict]:
        with _lock:
            return list(self._findings)

    def drain_findings(self) -> list[dict]:
        with _lock:
            out, self._findings = self._findings, []
            return out

    def status(self) -> dict:
        return {
            "heartbeat_gap_samples": len(self.heartbeat_gaps),
            "heartbeat_gap_baselined":
                self.heartbeat_gaps.baseline() is not None,
            "mic_silence_samples": len(self.mic_silences),
            "mic_silence_baselined":
                self.mic_silences.baseline() is not None,
            "pending_findings": len(self._findings),
        }


def get_monitor() -> HealthMonitor:
    """Process-wide singleton (funnel hook + doctor share it)."""
    global _monitor
    with _lock:
        if _monitor is None:
            _monitor = HealthMonitor()
        return _monitor


def reset_monitor() -> None:
    """Replace the singleton (test seam; never used in product)."""
    global _monitor
    with _lock:
        _monitor = None


def _default_findings_path() -> pathlib.Path:
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / "health_findings.jsonl")


def append_findings(findings, path=None) -> int:
    """Append findings as JSON lines to the findings file.

    Returns how many lines were written. Never raises — a
    failed findings write must never touch the funnel that
    produced the finding. The findings file is LOCAL ONLY.
    """
    items = list(findings or [])
    if not items:
        return 0
    target = pathlib.Path(path) if path is not None \
        else _default_findings_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            for item in items:
                fh.write(json.dumps(item, sort_keys=True) + "\n")
            fh.flush()
        return len(items)
    except Exception:  # noqa: BLE001 — findings never raise
        return 0
