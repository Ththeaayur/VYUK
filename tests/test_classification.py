"""Shipped unit tests (v5.47, programme item 12, A7): tool
classification completeness.

THE INVARIANT: every registered tool is classified in exactly
one primary source — _CONFIRM_GATE_TOOLS (human gate),
_READ_ONLY_TOOLS (provably side-effect-free), _FREE_BY_DESIGN
(deliberately ungated, with a recorded rationale class), or a
plugin capability class (_PLUGIN_GATE_CLASS, populated from
each plugin's declared capabilities by load_plugins).
Untainted execution freedom exists ONLY via those sources,
never by omission: before v5.47, 200 of the 477 declared tools
sat in NO set, and "unclassified" silently meant "free".

This test loads the REAL registry (the module singleton, the
same import path the other shipped tests use) and FAILS if
any declared tool is in none of the classification sources.
Plugin tools count via their capability classification: the
two shipped plugins (get_stock_quote / get_crypto_price) are
also members of _READ_ONLY_TOOLS — that dual listing is the
pre-existing H19b wiring (a static read classification plus a
plugin egress class), not a completeness gap.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import pathlib
import unittest

try:
    from _activity_guard import ActivityLogGuard
except ImportError:  # run as a package module instead of top-level
    from tests._activity_guard import ActivityLogGuard  # type: ignore

try:
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"tools unavailable: {exc}")  # Phase 4 S1c INTENTIONAL: shim deleted; message names the tool surface

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _declared_names() -> list[str]:
    """Every tool name in the live registry's declarations."""
    return [d["name"] for d in tools.get_declarations()]


def _sources_for(name: str) -> list[str]:
    """The classification sources that claim `name` (may be
    more than one for plugin tools — see the module docstring)."""
    sources = []
    if name in tools_mod._CONFIRM_GATE_TOOLS:
        sources.append("confirm_gate")
    if name in tools_mod._READ_ONLY_TOOLS:
        sources.append("read_only")
    if name in tools_mod._FREE_BY_DESIGN:
        sources.append("free_by_design")
    if tools_mod._plugin_gate_class(name):
        sources.append("plugin:" + str(tools_mod._plugin_gate_class(name)))
    return sources


class ClassificationCompletenessTests(unittest.TestCase):
    def setUp(self):
        self._guard = ActivityLogGuard(_PACKAGE_ROOT)
        self._guard.__enter__()

    def tearDown(self):
        self._guard.__exit__(None, None, None)

    def test_every_declared_tool_is_classified_somewhere(self):
        names = _declared_names()
        self.assertTrue(names, "the registry declared no tools")
        unclassified = [n for n in names if not _sources_for(n)]
        self.assertEqual(
            unclassified, [],
            "tools in NO classification source (v5.47 item 12: "
            "freedom by omission is forbidden) — classify each in "
            "_CONFIRM_GATE_TOOLS, _READ_ONLY_TOOLS, _FREE_BY_DESIGN "
            "or a plugin capability class")

    def test_gate_and_free_by_design_are_disjoint(self):
        self.assertEqual(
            tools_mod._CONFIRM_GATE_TOOLS
            & set(tools_mod._FREE_BY_DESIGN),
            frozenset(),
            "a tool cannot be both human-gated and free by design")

    def test_read_only_and_free_by_design_are_disjoint(self):
        self.assertEqual(
            tools_mod._READ_ONLY_TOOLS & set(tools_mod._FREE_BY_DESIGN),
            frozenset(),
            "a tool cannot be both read-only (taint/panic-exempt) "
            "and free-by-design (taint-gated state changer)")

    def test_every_free_by_design_class_has_a_rationale(self):
        for name, label in tools_mod._FREE_BY_DESIGN.items():
            self.assertIn(
                label, tools_mod._FREE_BY_DESIGN_CLASSES,
                f"{name} names class {label!r}, which has no "
                "rationale in _FREE_BY_DESIGN_CLASSES")
        for label, rationale in tools_mod._FREE_BY_DESIGN_CLASSES.items():
            self.assertTrue(
                str(rationale).strip(),
                f"class {label!r} has an empty rationale")

    def test_classification_names_are_real_tools(self):
        # A stale entry (a renamed/removed tool still sitting in
        # a set) is the mirror-image rot: the sets would claim a
        # completeness they do not provide.
        declared = set(_declared_names())
        for source_name, members in (
                ("_CONFIRM_GATE_TOOLS", tools_mod._CONFIRM_GATE_TOOLS),
                ("_READ_ONLY_TOOLS", tools_mod._READ_ONLY_TOOLS),
                ("_FREE_BY_DESIGN", set(tools_mod._FREE_BY_DESIGN))):
            stale = set(members) - declared
            self.assertEqual(
                stale, set(),
                f"{source_name} names tools the registry does not "
                "declare (plugin tools are declared too, via "
                "load_plugins, so they are not an excuse)")

    def test_plugin_tools_carry_a_capability_class(self):
        # The plugin route is a real classification source, not a
        # loophole: every plugin-registered name must have landed
        # in _PLUGIN_GATE_CLASS (load_plugins records "gated" for
        # a plugin with no capability declaration).
        for name, gate_class in tools_mod._PLUGIN_GATE_CLASS.items():
            self.assertIn(gate_class, ("read", "egress", "gated"))
            self.assertIn(name, set(_declared_names()))


if __name__ == "__main__":
    unittest.main()
