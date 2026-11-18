"""Regression batch G2 — classification snapshot.

`test_classification.py` pins COMPLETENESS (every tool classified
exactly once) but not VALUES — a tool silently moving from read to
gated, or from gated to free, ships without failing anything. That
is exactly the COV-02 / SEC-01 class from window 3 (5 tools left
`_READ_ONLY_TOOLS` for "effectful read"; `get_pixel_color` split
into read/click legs). This suite pins every registered tool's
classification VALUES in `tests/_classification_snapshot.json`
(generated from this tree) and fails on ANY drift, with a message
demanding a deliberate snapshot update + reason — never a silent
edit to make the test pass.

Explicit pins (not just snapshot values, so the intent survives a
snapshot regeneration):
- the 5 COV-02 effectful reads are NOT in `_READ_ONLY_TOOLS` and
  carry the "effectful read" free class;
- `get_pixel_color` legs agree: read/omitted action is read-class
  and unconfirmed; click/unknown action is non-read-class and
  confirmed (fail-closed on unknown).

Stdlib unittest only. Gate-leg checks run with taint cleared and a
user-started, unlocked session (clean-session semantics).
"""

from __future__ import annotations

import json
import pathlib
import unittest

try:
    from core import injection
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.config import config as app_config
    from core.funnel.tools_registry import tools as _registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel.tools_gate import (
        _CONFIRM_GATE_TOOLS,
        _FREE_BY_DESIGN,
        _READ_ONLY_TOOLS,
        _is_read_action,
        _needs_confirmation,
    )  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

_SNAPSHOT = pathlib.Path(__file__).resolve().parent / "_classification_snapshot.json"
_EFFECTFUL_READS = (
    "browser_read", "list_app_updates", "ping_host", "ssl_cert_info", "trace_route",
)


class TestClassificationSnapshot(unittest.TestCase):
    def test_snapshot_matches_current_classification(self):
        snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        current = {}
        declared = []
        for decl in _registry.get_declarations():
            name = decl["name"]
            declared.append(name)
            current[name] = {
                "read": name in _READ_ONLY_TOOLS,
                "confirm": name in _CONFIRM_GATE_TOOLS,
                "free": _FREE_BY_DESIGN.get(name),
            }
        missing = [n for n in declared if n not in snap]
        extra = [n for n in snap if n not in current]
        changed = [(n, snap[n], current[n]) for n in declared
                   if n in snap and snap[n] != current[n]]
        detail = []
        if missing:
            detail.append(f"new tools without a snapshot entry: {missing[:10]}")
        if extra:
            detail.append(f"snapshot entries with no declaration: {extra[:10]}")
        if changed:
            shown = "; ".join(
                f"{n}: snapshot={old} current={new}"
                for n, old, new in changed[:10])
            detail.append(f"classification changed: {shown}")
        self.assertEqual(
            detail, [],
            "Classification drift — if this is deliberate, update "
            "tests/_classification_snapshot.json AND record the "
            "reason (which tools moved, why, and what blast radius "
            "was checked). If it is not deliberate, the move is a "
            "regression. Details: " + " | ".join(detail))

    def test_effectful_reads_not_in_read_set(self):
        for name in _EFFECTFUL_READS:
            self.assertNotIn(
                name, _READ_ONLY_TOOLS,
                f"{name} is back in _READ_ONLY_TOOLS — COV-02 "
                f"reclassification lost")
            self.assertEqual(_FREE_BY_DESIGN.get(name), "effectful read",
                             f"{name} lost its 'effectful read' class")

    def test_pixel_color_legs_agree(self):
        injection.clear_taint(force=True)
        orig_prov = tools_mod.session_provenance()
        orig_lock = getattr(app_config, "session_provenance_lock", False)
        tools_mod.set_session_provenance("user")
        app_config.session_provenance_lock = False
        try:
            self.assertTrue(_is_read_action("get_pixel_color", {"action": "read"}))
            self.assertTrue(_is_read_action("get_pixel_color", {}))
            self.assertFalse(_is_read_action("get_pixel_color", {"action": "click"}))
            self.assertFalse(_is_read_action("get_pixel_color", {"action": "bogus"}))
            self.assertFalse(_needs_confirmation("get_pixel_color", {"action": "read"}))
            self.assertFalse(_needs_confirmation("get_pixel_color", {}))
            self.assertTrue(_needs_confirmation("get_pixel_color", {"action": "click"}))
            self.assertTrue(_needs_confirmation("get_pixel_color", {"action": "bogus"}))
        finally:
            tools_mod.set_session_provenance(orig_prov)
            app_config.session_provenance_lock = orig_lock
            injection.clear_taint(force=True)


if __name__ == "__main__":
    unittest.main()
