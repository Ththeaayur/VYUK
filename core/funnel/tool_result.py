"""Structured tool-result contract (audit fix A5, master P1-10).

Every tools.execute() result is a dict carrying an explicit status
from a closed vocabulary — never a bare string, never a success
wrapper around a failure payload:

    success  — the handler ran and returned a result.
    failure  — the handler raised, or the call failed for a reason
               other than a policy refusal or a deadline (kinds
               unavailable, handler_error).
    refused  — a gate prevented execution: confirmation declined,
               budget, validation, kill-chain/failsafe refusal, or
               a timeout cool-down refusal (kind preserved, so
               brains that branch on kind keep working).
    timeout  — the call exceeded its deadline. Its outcome is
               UNKNOWN: the result always also carries
               outcome="unknown" plus the durable operation_id
               from core.funnel.tool_operations, and the message says
               plainly not to auto-retry until reconciled.
    unknown  — reserved for results whose execution outcome cannot
               be determined for a non-timeout reason; currently
               produced only as the recorded state of a timed-out
               operation before its finalizer reconciles it.

Legacy note: pre-A5 funnel results used status "error" for every
non-success. Consumers that must also accept direct legacy dicts
(failure_script, spoken_errors) treat "error" as non-success too;
the funnel itself never emits "error" anymore — _audited() in
core.funnel.tools_registry normalizes it at the boundary.

A handler-returned refusal sentence must never leave the funnel
marked success: REFUSAL_MARKERS is the established set (moved
here from core.brain.planner, which keeps _REFUSAL_MARKERS as an alias)
and the native success path converts a marker-bearing string to
a refused result carrying the sentence as its message. A handler
returning a funnel-shaped dict (status + kind/message) is likewise
surfaced as that outcome, never re-wrapped as success — so a
nested dispatch failure cannot masquerade as a success payload.

Stdlib-only, no core imports: every module may import this one
without a cycle.
"""

from __future__ import annotations

from typing import Any

STATUSES = ("success", "failure", "refused", "timeout", "unknown",
            "cancelled")
# B20 (2026-11-07): "cancelled" is a non-success status — a handler
# returning {"status": "cancelled", ...} is surfaced as that
# outcome, never re-wrapped as a success payload. Before this,
# "cancelled" was absent here, so the token docstring's suggested
# return shape silently became SUCCESS (the B1-class hole: a
# cancelled call recorded as a success). A cancelled step is a
# third outcome — never success, never failure.
NON_SUCCESS_STATUSES = ("failure", "refused", "timeout", "unknown",
                        "cancelled")

# Pre-A5 wire value for every non-success. The funnel no longer
# emits it; direct legacy dicts still count as non-success.
LEGACY_ERROR_STATUS = "error"

# Refusal sentences VYUK's own handlers return as prose (the
# skills packs' shape). Specific phrases only — a success string
# merely mentioning refusal in passing does not match these.
REFUSAL_MARKERS = (
    "Blocked by VYUK's trust layer",
    "Refused:",
    "needs confirm=true",
    "Ask again with confirmation",
    "needs the user's OK in VYUK's window",
)

# Refusal openers (round-3 fix Q1) — the first words of the refusal
# sentences the skills packs actually return ("I can't evaluate
# that ...", "Can't convert ...", "Could not ...", "I can't read
# ... as dice."). A result whose WHOLE text starts with one of
# these is a refusal, matched by prefix, never by substring: a
# sentence that merely CONTAINS "I can't" mid-text (a quote, a
# translation, a summary) is data, not a refusal. "I don't know"
# is deliberately NOT an opener — it is ambiguous (an admission of
# ignorance, not necessarily a refusal to act). Handlers that need
# certainty should return a funnel-shaped dict (failure_result /
# refused_result) instead of a sentence; the opener rule is the
# backstop for the sentence-shaped refusals the packs already
# return. Residual risk, stated plainly: a legitimate success
# string that OPENS with a refusal opener (a quoted sentence, a
# translation) will be read as refused and halt the plan/routine
# that called it — a graceful halt with an honest message, never
# a false success.
REFUSAL_OPENERS = (
    "I can't",
    "I cannot",
    "Can't",
    "Could not",
    "Couldn't",
)

# Kinds whose "error" maps to a refusal (the call never ran) as
# opposed to a failure (the call ran and failed, or could not
# run for a systemic reason). The timeout kind maps to refused
# here because the only "error" + timeout-kind sites are cool-down
# refusals; genuine deadline timeouts pass status "timeout"
# directly and never reach this mapping.
REFUSAL_KINDS = frozenset(
    # INTENTIONAL (live staleness stage, P4, 2026-11-05):
    # "stale-view" is a refusal kind — the call never ran
    # (nothing was clicked), so the audited result must read
    # status "refused", never "failure" (which would claim the
    # click ran and failed). See LIVE_VERIFIER_COVERAGE_DESIGN.md.
    {"refused", "not_confirmed", "validation", "budget", "timeout",
     "stale-view"}
)
FAILURE_KINDS = frozenset({"unavailable", "handler_error"})


def is_non_success(result: Any) -> bool:
    """True for a funnel result that is not a success.

    Accepts the legacy "error" value too, so direct legacy dicts
    (tests, stored results) are never mistaken for successes.
    """
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    return status in NON_SUCCESS_STATUSES or status == LEGACY_ERROR_STATUS


def is_refusal_marker_text(text: Any) -> bool:
    """True when a handler-returned string contains a refusal marker.

    Markers only — the pre-Q1 rule. Markers apply to every string
    the funnel sees, including a message unwrapped from an explicit
    Outcome verdict (audit fix A5's design: the refusal-sentence
    rules see exactly the unwrapped string).
    """
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in REFUSAL_MARKERS)


def is_refusal_opener_text(text: Any) -> bool:
    """True when a handler-returned string OPENS with a refusal opener.

    Openers only — the round-3 fix Q1 rule. A prefix match on the
    stripped text: a refusal sentence is read as refused, while a
    success sentence that merely contains an opener mid-text is not
    misread. Unlike markers, openers do NOT apply to a message
    unwrapped from an explicit Outcome verdict (see the dispatch):
    a handler that returned outcome.failed/unverified already said
    exactly what happened, and the string heuristic must not
    override that structured verdict.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return any(stripped.startswith(opener) for opener in REFUSAL_OPENERS)


def is_refusal_text(text: Any) -> bool:
    """True when a handler-returned string is a refusal sentence.

    Either it contains one of the REFUSAL_MARKERS, or the whole
    text starts with one of the REFUSAL_OPENERS. The opener rule
    is a prefix match on the stripped text, so a refusal sentence
    is read as refused wherever it appears (funnel dispatch, plan
    walk, routine loop, flow classifier) while a success sentence
    that happens to contain a refusal opener mid-text is not
    misread. Consumers that must respect an explicit Outcome
    verdict use the marker/opener helpers separately instead.
    """
    if not isinstance(text, str):
        return False
    return is_refusal_marker_text(text) or is_refusal_opener_text(text)


def is_funnel_shaped(value: Any) -> bool:
    """True when a handler return value already looks like a
    funnel result: a dict with a non-success status plus a kind
    and/or message. Such a value must be surfaced, never wrapped
    as a success payload. A data dict that merely happens to
    carry a "status" key (no kind/message) is NOT funnel-shaped
    and stays a success payload."""
    if not isinstance(value, dict):
        return False
    status = value.get("status")
    if status not in NON_SUCCESS_STATUSES and status != LEGACY_ERROR_STATUS:
        return False
    return "kind" in value or "message" in value


# B1 (2026-11-06): the one funnel-shaped failure constructor for a
# handler whose act failed. A handler returning this dict is surfaced
# as that outcome, never re-wrapped as a success payload (see
# is_funnel_shaped). Build it through this helper, never by hand: a
# typo in the status string would NOT be funnel-shaped and would
# silently re-wrap as success — exactly the hole this closes. The
# kind names the failure family so the coordinator's detectors can
# match it structurally instead of sniffing sentences; a new reason
# inside the family needs no new detector case. The message is the
# human sentence, unchanged.
WALLPAPER_NOT_SET_KIND = "wallpaper_not_set"


def failure_result(kind: str, message: str) -> dict:
    """Build a funnel-shaped failure dict (status + kind + message).

    Never raises; the message is carried verbatim.
    """
    return {"status": "failure", "kind": kind, "message": message}


CANCELLED_KIND = "cancelled"


def cancelled_result(message: str) -> dict:
    """Build a funnel-shaped cancelled dict (status + kind + message).

    B20 (2026-11-07): the ONE constructor for a handler whose act
    stopped at the user's request. Build it through this helper,
    never by hand: a typo in the status string would NOT be
    funnel-shaped (see is_funnel_shaped) and would silently
    re-wrap as success — exactly the hole this closes. The
    message names what was stopped and what partial progress
    stands (files already copied, steps already run) — a
    cancelled step is a third outcome, never success, never
    failure, and never retried (B12: "cancelled" is not a
    transient kind).
    """
    return {"status": "cancelled", "kind": CANCELLED_KIND,
            "message": message}


def normalized_error_status(kind: Any) -> str:
    """Map a legacy "error" kind to the contract status.

    Refusal kinds (the call never ran) -> "refused"; failure
    kinds and anything unrecognized -> "failure". Never "success".
    """
    if isinstance(kind, str) and kind in REFUSAL_KINDS:
        return "refused"
    return "failure"


def normalize_result(result: Any) -> Any:
    """Enforce the contract on one funnel-boundary dict.

    Successes pass through untouched. Legacy "error" results map
    by kind (see normalized_error_status). Results already in the
    vocabulary pass through. Non-dicts and dicts without a status
    are returned untouched — the funnel builds its own dicts, and
    this function must never invent a status for foreign data.

    Deliberately NOT added (test-isolation batch, with evidence):
    a blanket "result" field on non-success results. Key-presence
    is the ecosystem's success signal — result-style helpers across
    the wiring suites unwrap "result" only when present and take
    the dict as-is otherwise; giving refusals an empty "result"
    broke routerfinish's tripped-payload test and firstrun's
    _result helper in the first testiso full run (pass -> ERROR).
    The contract is therefore: "result" present ⟺ success payload
    present; refusals carry status/kind/message and NO "result".
    Consumers must use .get() and branch on status/kind, never
    index ["result"] on a possibly-refused result.
    """
    if not isinstance(result, dict):
        return result
    status = result.get("status")
    if status == LEGACY_ERROR_STATUS:
        out = dict(result)
        out["status"] = normalized_error_status(result.get("kind"))
        return out
    return result
