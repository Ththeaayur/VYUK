"""Typed guard states for safety consults (audit fix A2, P0-2/P1-1).

Every safety consult in the funnel used to answer a bare boolean
(or None), so a consult that ERRORED was indistinguishable from a
consult that CLEARED the action — and every call site read the
error as "allowed". This module makes the distinction typed:

* GuardState.ALLOWED — the safeguard was consulted and cleared.
* GuardState.DENIED — the safeguard was consulted and refused.
* GuardState.UNAVAILABLE — the safeguard could NOT be consulted
  (import error, call error, corrupt state). UNAVAILABLE is never
  "disabled" and never "allowed": no explicit disabled state is
  reachable by an exception anywhere. A safeguard counts as
  disabled only where the tree already defines an explicit,
  declared off-switch (today: the tool-call budget, whose
  configured per-minute limit of 0 means "no brake" — a declared
  config value, not an error). The money guard, the failsafe and
  the kill-chain have no such switch: for them, every error is
  UNAVAILABLE, and enforcement denies sensitive actions on it.

FailsafeState is separate on purpose: the failsafe is a state
sensor (tripped / clear), not a permission verdict. Enforcement
translates it: TRIPPED refuses non-read tools, CLEAR proceeds,
UNAVAILABLE refuses non-read tools exactly like a trip (reads
stay exempt, as they are while tripped — R5: reads run so a
human triaging keeps her eyes).

Stdlib only, no tree imports: every consult module may import
this without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuardState(str, Enum):
    """Outcome of a permission-like safety consult."""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


class FailsafeState(str, Enum):
    """Outcome of a failsafe sensor consult."""

    CLEAR = "clear"
    TRIPPED = "tripped"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GuardVerdict:
    """A consult outcome plus its machine-readable reason.

    `reason` is empty for ALLOWED. For DENIED it carries the
    guard's refusal message. For UNAVAILABLE it names the
    failure (exception type, never a value) so the caller can
    surface it without inventing a permission.
    """

    state: GuardState
    reason: str = ""

    @staticmethod
    def allowed() -> "GuardVerdict":
        return GuardVerdict(GuardState.ALLOWED, "")

    @staticmethod
    def denied(reason: str) -> "GuardVerdict":
        return GuardVerdict(GuardState.DENIED, reason)

    @staticmethod
    def unavailable(reason: str) -> "GuardVerdict":
        return GuardVerdict(GuardState.UNAVAILABLE, reason)
