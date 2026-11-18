"""Shared budget-isolation helper (L3 standardisation of the
test-isolation guest rule).

The process-global tool-call budget (120 dispatches / 60 s) made
full-run failure sets timing-variant: a suite running late in a
run inherited the earlier suites' dispatches and tripped on
ordering instead of on its own behaviour. The guest rule
(test_testiso.py, routerfinish precedent) fixes this WITHOUT
touching production behaviour:

* setUpModule: SNAPSHOT the budget timestamps, the budget clock
  and the confirm handler, then open a clean window.
* tearDownModule: RESTORE the snapshot byte-for-byte (same list
  object, same clock, same handler), so the rest of the run sees
  exactly the state it would have seen without this suite.

This helper is the one shared location for that rule — suites
must not copy the snapshot/restore lines per file (copies drift;
the testiso docstring already records one such drift). The
snapshot token is a plain dict, so no module-global mutable
state is shared between suites.

Stdlib-only, no core imports at module level (the imports live
inside the functions, so importing this helper never pulls the
tool registry into a suite that does not need it).
"""

from __future__ import annotations


def snapshot_budget():
    """Snapshot budget state, then open a clean window.

    Returns the snapshot token; pass it to restore_budget(). The
    reset uses the production test-harness helper
    reset_tool_budget() only — the budget limits themselves are
    untouched.
    """
    from core import confirm_gate
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

    saved = {
        "budget": list(th._budget_timestamps),
        "now": th._budget_now,
        "handler": confirm_gate._handler,
    }
    th.reset_tool_budget()
    return saved


def restore_budget(saved):
    """Restore a snapshot byte-for-byte.

    The timestamps list is restored in place (same list object,
    so any holder of the list sees the restored window); the
    clock and the confirm handler are restored exactly, so a
    suite that registered a handler and forgot to clear it
    cannot leak it into the next suite.
    """
    from core import confirm_gate
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

    th._budget_timestamps[:] = saved["budget"]
    th._budget_now = saved["now"]
    if saved["handler"] is None:
        confirm_gate.clear_handler()
    else:
        confirm_gate.register_handler(saved["handler"])


def reset():
    """Open a clean window (per-test use; same helper as the
    module fixture, without snapshotting)."""
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

    th.reset_tool_budget()
