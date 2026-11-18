"""The Agents Rule of Two, session-level budget (v5.55, Wave 1
SE-3.1 — ADOPT pattern, from Meta's agent-security writing,
2025, paraphrased here in VYUK's own words; see
THREAT_MODEL.md for the doctrine).

The law: an agent session operating without human oversight
may possess at most TWO of these three legs at once —

  [A] processing untrusted input,
  [B] access to sensitive systems or data,
  [C] the ability to change state or communicate externally.

Holding all three unsupervised is the forbidden
configuration: it is the shape of an exfiltration (untrusted
content + private data + a way out) and of an
injection-driven state change. The budget is enforced in
``ToolRegistry.execute`` (native and MCP alike), through the
existing taint tiers and the existing confirmation gate — no
new dependency, no new popup kind.

Semantics, exactly:

* Session legs are legs EXERCISED by calls that actually
  RAN in this session (recorded at dispatch, after every
  gate passes). Refused calls confer nothing — nothing
  happened. A leg, once exercised, stays for the session:
  one approval satisfies ONE call, never launders the
  session (a later third-leg call asks again).
* The check for one call is: session legs UNION this
  call's legs. If the union is all three AND this call has
  not already been human-approved inside this execute()
  (the confirmation gate just approved it), the call is
  forced through the confirmation gate with a message that
  names the Rule of Two. An already-approved call skips the
  extra ask — asking twice for one call is noise, and noise
  trains humans to click yes.
* Untainted sessions can never trip: leg A is absent
  unless the session is tainted, so the union caps at two.
  The false-positive rate is therefore bounded by taint
  accuracy, not by call volume.
* Leg A is read live from core.injection at call time
  (tainted or not — any kind counts; soft provenance-only
  taint still counts, because the session DID process
  untrusted input).
* Leg B (native, v1): reads of the user's own stored
  private data — fact-memory recall, day-journal recall,
  calendar reads, email reads. The secrets store has no
  model-facing read tool, so it cannot confer B in v1
  (recorded, not assumed). MCP tools never auto-confer B
  in v1: an MCP server's data is untrusted input (A when
  tainted), not VYUK's sensitive store — a later wave may
  promote specific MCP servers to B with an explicit list.
* Leg C (native): the tool changes state (not a read) or
  communicates externally (an egress read). Reuses the
  gate module's own read/egress classification, so the
  budget and the gate can never drift apart.
* Leg C (MCP): the tool's class is not "read" (egress
  and confirm classes both communicate or change).
* A voice factor may ADD a restriction (a low speaker
  score could force-confirm a third-leg call that would
  otherwise run), and may never REMOVE one. No speaker
  verifier exists in the product, so this is doctrine
  only in v1 — recorded here so a later voice build
  cannot "fix" the budget by waiving it.
* Wave 8 inherits this budget as run_goal run state
  (checkpointed, resumable). That half is NOT built here.

Qt-free, stdlib-only (INV-12). The tracker is module
state keyed by the session epoch: when the epoch moves
(session disconnect / swap), the budget resets with it —
a new session starts with no legs.
"""

from __future__ import annotations

from typing import Any

#: Native tools whose reads touch the user's own stored
#: private data (leg B). Each name was verified against its
#: handler, not its spelling: recall_memories reads the
#: fact store, recall_day the day journal, the get_*
#: calendar tools the user's calendar, the email tools the
#: user's mailbox (download_email_attachment reads private
#: email content onto disk — it is a private-data read even
#: though it is also an untrusted-content producer).
SENSITIVE_READ_TOOLS = frozenset({
    "recall_memories",
    "recall_day",
    "get_todays_schedule",
    "get_upcoming_events",
    "get_unread_email",
    "search_email",
    "download_email_attachment",
})

_session_legs: set[str] = set()
_session_epoch: Any = None


def _current_epoch() -> Any:
    """The session epoch, or None when the epoch module
    cannot be consulted (then legs accumulate for the
    process lifetime — the safe direction is to keep
    counting, never to reset on ignorance)."""
    try:
        from .. import session_epoch  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

        return session_epoch.current_epoch()
    except Exception:  # noqa: BLE001 — see the docstring
        return None


def session_legs() -> set[str]:
    """Legs exercised so far this session (a copy). Resets
    when the epoch moved since the last call."""
    global _session_legs, _session_epoch
    epoch = _current_epoch()
    if epoch is not None and _session_epoch is not None \
            and epoch != _session_epoch:
        _session_legs = set()
    _session_epoch = epoch
    return set(_session_legs)


def record_legs(legs: set[str]) -> None:
    """Union exercised legs into the session budget. Called
    at dispatch — after every gate passes, before the
    handler runs — so only calls that actually run count."""
    global _session_legs
    session_legs()  # applies any pending epoch reset first
    _session_legs |= set(legs)


def legs_for_native_call(name: str, args: dict[str, Any] | None) -> set[str]:
    """Legs one native call would exercise, from the gate
    module's own classification (never a second copy):
    _is_read_class for the read boundary (it knows the
    action-dispatched read actions), _is_egress_read for the
    external-communication half of C."""
    from .. import injection  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level
    from .tools_gate import _is_egress_read, _is_read_class  # Phase 3 F1: tools_gate moved to core/funnel/ (Phase 3 F2b: rule_of_two now in core/funnel/, intra-package)

    legs: set[str] = set()
    try:
        if injection.tainted():
            legs.add("A")
    except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
        # reads TAINTED (leg A added), mirroring the C-leg
        # fail-closed convention below — an unprovable-clean call
        # counts toward the forbidden union, so it asks.
        legs.add("A")
    if name in SENSITIVE_READ_TOOLS:
        legs.add("B")
    try:
        is_read = _is_read_class(name, args) \
            and not _is_egress_read(name)
    except Exception:  # noqa: BLE001 — fail-closed on C
        is_read = False
    if not is_read:
        legs.add("C")
    return legs


def legs_for_mcp_call(tool_class: str) -> set[str]:
    """Legs one MCP call would exercise. MCP tools never
    auto-confer B in v1 (see the module docstring); C is
    every class but "read"."""
    from .. import injection  # Phase 3 F2b: file now in core/funnel/; flat sibling climbs one more level

    legs: set[str] = set()
    try:
        if injection.tainted():
            legs.add("A")
    except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
        # reads TAINTED (leg A added) — an unprovable-clean MCP
        # call counts toward the forbidden union, so it asks.
        legs.add("A")
    if tool_class != "read":
        legs.add("C")
    return legs


def completes_forbidden_union(call_legs: set[str]) -> bool:
    """True when session legs + this call's legs hold all
    three — the configuration that must ask a human."""
    return (session_legs() | set(call_legs)) == {"A", "B", "C"}


def reset_for_tests() -> None:
    """Test seam only: clear the session budget."""
    global _session_legs, _session_epoch
    _session_legs = set()
    _session_epoch = None
