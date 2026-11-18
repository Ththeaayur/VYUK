"""Wave 4 SEC + GOV + REL extensions — policy gate, audit, degraded mode.

Stdlib only, no network, no capture. This is the breadth policy
layer: an action is permitted only if it is on the caller's
allow-list AND the caller's C-14 confirmation (where required) is
present. This gate never grants confirmation itself; it checks
what the caller supplies.

Rules, enforced in code:
  * An action outside the allow-list is refused, with the action
    name in the reason — never silently dropped, never permitted.
  * A Tier 2 (consequential) action without a supplied confirmation
    is refused. Confirmation is caller-supplied evidence, not a
    flag this module sets on its own.
  * Every decision is appended to an audit record (action, verdict,
    reason). The audit record is append-only.
  * Degraded mode is narrated: entering it returns a plain-language
    narration of what is reduced, so the user is never left guessing
    why behaviour changed. This is the Wave 4 REL extension; it
    reuses no executor and dispatches nothing.
"""

from __future__ import annotations


class PolicyGate:
    """Allow-list + Tier 2 confirmation gate, with an audit trail."""

    def __init__(self, allow_list: list, tier2_actions: list | None = None):
        self._allow = set(allow_list)
        self._tier2 = set(tier2_actions or [])
        self.audit = []

    def decide(self, action: str, *, confirmed: bool = False) -> dict:
        if action not in self._allow:
            verdict = {"ok": False, "action": action,
                       "reason": f"action '{action}' is not on the allow-list"}
        elif action in self._tier2 and not confirmed:
            verdict = {"ok": False, "action": action,
                       "reason": f"Tier 2 action '{action}' needs confirmation "
                                 "(C-14) before it may run"}
        else:
            verdict = {"ok": True, "action": action,
                       "reason": "allow-listed and, where Tier 2, confirmed"}
        self.audit.append({"action": action, "ok": verdict["ok"],
                           "reason": verdict["reason"]})
        return verdict

    def audit_trail(self) -> list:
        return list(self.audit)


def degraded_mode_narration(reduced: list) -> dict:
    """Narrate a degraded mode in plain words. Always returned."""
    if not reduced:
        return {"ok": True, "narration": "Running normally; nothing reduced.",
                "reduced": []}
    return {"ok": True,
            "narration": ("Running in degraded mode: " +
                          ", ".join(reduced) +
                          " are reduced until conditions improve."),
            "reduced": list(reduced)}
