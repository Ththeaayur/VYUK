"""The live session's quota check (loophole batch 7, #7).

Text turns go through the cost preflight (fail-closed since the
section-C fix). Live voice turns were record-only by design (MN-2.8:
a Live stream cannot be preflight-gated mid-audio) — so a long voice
session burned the free-tier quota with no check at all. This module
is the missing *before*: a quota check at session start, and a
plain-words warning when the quota is nearly gone.

The check reads the ledger's OWN preflight with a zero-token
estimate for the live model: the estimate is 0, so the verdict
reads the already-spent fraction against the configured caps
(WARN_FRACTION 0.80 / CAP_FRACTION 1.00). With no caps configured
the preflight always allows and this check is a no-op — the
default install is unchanged. A ledger that cannot be READ fails
closed (the section-C item-10 rule): the session does not start
without a cap check.

Callers:
* live_client_lifecycle.start() gates on live_quota_check() right
  after auth resolution, before CONNECTING.
* live_client_receive.py calls live_quota_after_turn() at each
  turn end; it returns the warning string exactly once per
  session when the fraction newly crosses the warn line (or the
  cap is hit mid-session), else None.

Qt-free. The ledger factory is a module attribute so tests (and
the repro) can inject a fake ledger without touching live data.
"""

from __future__ import annotations

from typing import Any

_LEDGER_FACTORY = None


def _ledger_factory():
    global _LEDGER_FACTORY
    if _LEDGER_FACTORY is None:
        from ..cost_ledger import CostLedger

        _LEDGER_FACTORY = CostLedger.from_env
    return _LEDGER_FACTORY


def _default_model() -> str:
    try:
        from ..config import config

        return str(config.model_name or "gemini-3.8-live")
    except Exception:  # noqa: BLE001 — the pin is the fallback
        return "gemini-3.8-live"


def live_quota_check(*, ledger: Any = None,
                     model: str | None = None) -> dict[str, Any]:
    """The session-start quota check: the ledger's preflight for
    the live model with a zero-token estimate (reads the
    already-spent fraction). Returns the preflight verdict dict
    (allowed / action / requires_confirm / warning /
    spend_today_usd / spend_month_usd / binding_cap_usd).
    Fail-closed: a ledger that cannot be read returns allowed
    False with a warning naming the failure — the session must
    not start without a cap check. Never raises."""
    try:
        led = ledger if ledger is not None else _ledger_factory()()
        verdict = led.preflight("live",
                                model if model is not None
                                else _default_model(),
                                prompt_tokens=0, completion_tokens=0)
        return {
            "allowed": bool(verdict.get("allowed")),
            "action": str(verdict.get("action") or "allow"),
            "degraded": bool(verdict.get("degraded")),
            "requires_confirm": bool(verdict.get("requires_confirm")),
            "warning": verdict.get("warning"),
            "spend_today_usd": verdict.get("spend_today_usd"),
            "spend_month_usd": verdict.get("spend_month_usd"),
            "estimated_cost_usd": verdict.get("estimated_cost_usd"),
            "binding_cap_usd": verdict.get("binding_cap_usd"),
        }
    except Exception as exc:  # noqa: BLE001 — fail closed, never raise
        return {
            "allowed": False,
            "action": "blocked",
            "degraded": True,
            "requires_confirm": False,
            "warning": ("Cost cap check unavailable — the cost "
                        "ledger could not be read "
                        f"({type(exc).__name__}: {exc}); live "
                        "sessions are paused until the ledger is "
                        "repaired."),
            "spend_today_usd": None,
            "spend_month_usd": None,
            "estimated_cost_usd": None,
            "binding_cap_usd": None,
        }


def live_quota_after_turn(*, ledger: Any = None,
                          model: str | None = None,
                          warned: bool = False) -> str | None:
    """The mid-session check, called at each completed live turn:
    the plain-words warning to emit, or None. Warns exactly once
    per session (``warned`` is the session's already-warned flag):
    when the fraction newly crosses the warn line, or when the cap
    is hit mid-session (the session keeps running — the spend is
    already spent — but the user is told the next session will
    not start). Returns None while the spend is comfortably
    under the cap, and None when already warned. Never raises."""
    if warned:
        return None
    try:
        verdict = live_quota_check(ledger=ledger, model=model)
        if verdict["action"] in ("warn", "confirm"):
            warning = verdict.get("warning")
            return str(warning) if warning else None
        return None
    except Exception:  # noqa: BLE001 — a failed check warns nothing
        return None
