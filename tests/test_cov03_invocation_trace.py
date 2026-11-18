"""COV-03 invocation-to-oracle tracing test (audit fix Batch 5).

REPORT-ONLY: this test NEVER fails on coverage. Coverage
growth is a programme, not this batch's gate, so the only
assertions here are on the tracing mechanism itself (the
recorder controls below) and on report sanity (buckets sum
to the registry size). The numbers it prints are the honest
measurement; nothing here may be quoted as "covered" without
the caveats printed alongside it.

What it measures, per registered tool, from the test tree:

* invoked-through-executor (invocation SITE): the tool's name
  appears as the literal first argument of an `.execute(`
  call in some test file. A literal execute call is what a
  test actually dispatches through the funnel when it runs —
  strictly stronger than a name mention, and the audit's
  point: "mentioned = covered" is false.
* mention-only: the name appears in test files but never as
  a literal execute argument (comment, mock, patch target,
  assertion string, router table). These are NOT counted as
  covered. The mock-like subset (a mention line containing
  patch/Mock/mock/assert) is reported separately: a tool
  invoked only via mocks is named as such, never inflated.
* no-mention: the name appears in no test file at all.

HONEST LIMITS, printed with every report (read them before
quoting any number):

* This is a static invocation-SITE trace, not a runtime
  execution trace: it proves a test DISPATCHES the tool, not
  that the dispatch succeeded — a budget refusal, a gate
  refusal, or a validation refusal at runtime still blocks
  it. The invoked count is therefore an UPPER BOUND on
  runtime coverage, never a pass count.
* It does NOT prove oracle linkage: an execute call paired
  with no assertion on the tool's intended effect is still
  counted as invoked here. Per the review, "covered" properly
  means traced invocation PLUS an evaluated task oracle;
  this test measures the first half only, and says so.
* Indirect invocations (tool reached only through a router
  or the meta-tool without its literal name in an execute
  call) land in mention-only or no-mention here even though
  a runtime run may exercise them. The uncovered list is
  therefore a work list for the certification programme,
  not a verdict that a tool is untested.

Controls (the asserted part): a recording wrapper around a
real funnel dispatch captures the tool id and outcome
(positive); merely mentioning a tool name in a string
captures nothing (negative — the audit's discriminating
check); invoking through a wrapper function still attributes
the routed tool, not the wrapper (indirect control).

Stdlib unittest only. Budget-isolated (it dispatches through
the funnel) and receipt-hermetic (temp receipt store, so its
dispatches never touch the real data/tool_receipts.jsonl).
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import unittest

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)

_HERE = pathlib.Path(__file__).resolve().parent

_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None


def _trace():
    """Return (names, invoked, mention_only, mock_like, no_mention).

    names: registered tool names from the live registry.
    invoked: names with a literal `.execute("name"` call site.
    mention_only: names mentioned but never execute-called.
    mock_like: subset of mention_only with a mock/assert-like
      mention line (invoked only via mocks, or only asserted
      about — never counted as covered).
    no_mention: names absent from every test file.
    """
    from core.funnel.tools_registry import tools as _tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

    names = sorted(_tools._tools.keys())
    execute_arg_re = re.compile(
        r"\.execute\(\s*[\"']([A-Za-z0-9_]+)[\"']")
    invoked: set[str] = set()
    sources: dict[str, str] = {}
    for path in sorted(_HERE.glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        sources[path.name] = text
        invoked.update(execute_arg_re.findall(text))
    invoked &= set(names)
    mention_only: set[str] = set()
    mock_like: set[str] = set()
    for name in names:
        if name in invoked:
            continue
        word = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name)
                          + r"(?![A-Za-z0-9_])")
        found = False
        for text in sources.values():
            if word.search(text):
                found = True
                for line in text.splitlines():
                    if word.search(line) and (
                            "patch" in line or "Mock" in line
                            or "mock" in line or "assert" in line):
                        mock_like.add(name)
                        break
                break
        if found:
            mention_only.add(name)
    no_mention = set(names) - invoked - mention_only
    return names, invoked, mention_only, mock_like, no_mention


def _report(names, invoked, mention_only, mock_like, no_mention):
    lines = []
    lines.append("COV-03 INVOCATION-TRACE REPORT (report-only; "
                 "not a coverage gate)")
    lines.append(f"  registered tools (live registry): {len(names)}")
    lines.append(f"  invoked through executor (literal execute "
                 f"call site): {len(invoked)}")
    lines.append(f"  mention-only (never an execute argument): "
                 f"{len(mention_only)}")
    lines.append(f"    of which mock/assert-like mention only: "
                 f"{len(mock_like)}")
    lines.append(f"  no test-file mention: {len(no_mention)}")
    lines.append(f"  uncovered (not invoked: mention-only + "
                 f"no-mention): "
                 f"{len(mention_only) + len(no_mention)}")
    lines.append("  CAVEATS: invocation-site trace only (upper "
                 "bound — runtime refusals can still block a "
                 "dispatch); oracle linkage NOT proven by this "
                 "trace; mock-only tools are NOT counted as "
                 "covered; router-only tools may be exercised "
                 "at runtime without a literal call site.")
    uncovered = sorted(mention_only | no_mention)
    lines.append("  uncovered tools: " + ", ".join(uncovered))
    return "\n".join(lines)


class InvocationTraceTests(unittest.TestCase):
    def test_recorder_controls(self):
        """Positive / negative / indirect recorder controls.

        A recording wrapper around a REAL funnel dispatch
        (isolated probe registry, temp receipt store) must
        capture the tool id + outcome; a mere mention must
        capture nothing; an indirect dispatch must attribute
        the tool, not the wrapper.
        """
        from core.funnel import tools_registry as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
        from core import tool_receipts

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tool_receipts.install_store(
            pathlib.Path(tmp.name) / "receipts.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        reg = object.__new__(tools_mod.ToolRegistry)
        reg._tools = {}
        reg.register("unit_probe", "probe",
                     {"type": "object",
                      "properties": {"count": {"type": "integer"}},
                      "required": ["count"]},
                     lambda count: f"count={count}")
        rows: list[tuple[str, str]] = []
        real_execute = reg.execute

        def recording_execute(name, args):
            out = real_execute(name, args)
            rows.append((name, out.get("status", "")))
            return out

        reg.execute = recording_execute  # type: ignore[method-assign]
        # Negative control: merely mentioning the name.
        _mentioned = "unit_probe"  # noqa: F841 — never executed
        self.assertEqual(rows, [])
        # Positive control: a real funnel dispatch.
        out = reg.execute("unit_probe", {"count": 3})
        self.assertEqual(out["status"], "success")
        self.assertEqual(rows, [("unit_probe", "success")])
        # Indirect control: dispatch through a wrapper.
        rows.clear()

        def _via_wrapper():
            return reg.execute("unit_probe", {"count": 4})

        _via_wrapper()
        self.assertEqual(rows, [("unit_probe", "success")])

    def test_invocation_trace_report(self):
        names, invoked, mention_only, mock_like, no_mention = _trace()
        print(_report(names, invoked, mention_only, mock_like,
                      no_mention))
        # Sanity only — NEVER a coverage threshold. Coverage
        # growth is a programme; this test reports, it does
        # not gate.
        self.assertGreater(len(names), 0)
        self.assertEqual(
            len(invoked) + len(mention_only) + len(no_mention),
            len(names))
        self.assertTrue(mock_like <= mention_only)
        self.assertGreaterEqual(len(invoked), 1)


if __name__ == "__main__":
    unittest.main()
