"""Power/network policy gate — split from updater.py (B24)."""
from __future__ import annotations

from .. import power_policy  # Phase 3 H1: target stays flat in core/


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


def _policy_refusal(kind: str) -> str | None:
    """Gate C (master audit P2-2): the power/network policy,
    ENFORCED at the download/update entry points — not flagged
    after the fact. kind "update" gates check() on
    updates_allowed(); kind "download" gates download_and_stage()
    on downloads_allowed(). Returns None when allowed, else the
    plain-words refusal reason. Never raises.

    Two deliberate non-inventions, recorded here because the
    audit brief forbids inventing policy:
    * The policy defines NO battery-based download/update
      refusal — battery only throttles watchers and local
      inference — so battery state never refuses here.
    * An unreadable policy fails OPEN (allowed): power_policy
      itself documents unknown metered state as
      downloads-allowed, because a host without the WinRT
      binding must not lose updates. Only a positively
      detected metered connection refuses. The call goes
      through the power_policy module attribute at call time,
      so suites can monkeypatch core.power_policy.
      current_policy to inject a metered state."""
    try:
        policy = power_policy.current_policy()
    except Exception:  # noqa: BLE001 — unreadable policy: open
        return None
    allowed = (power_policy.updates_allowed(policy) if kind == "update"
               else power_policy.downloads_allowed(policy))
    if allowed:
        return None
    detail = ""
    try:
        detail = str(policy.get("reason") or "")
    except Exception:  # noqa: BLE001 — reason is cosmetic
        detail = ""
    what = "updates" if kind == "update" else "downloads"
    if detail:
        return f"{what} paused by power/network policy ({detail})"
    return f"{what} paused by power/network policy"
