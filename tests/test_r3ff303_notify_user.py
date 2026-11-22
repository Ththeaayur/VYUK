"""Round-3 section F fix F-F303 — notify_user is confirm-gated.

A toast pops on the user's screen outside the conversation, so
every notify_user call asks first. Membership in
_CONFIRM_GATE_TOOLS also bars the always-allow shortcut
structurally. The "output to user" siblings stay free — speech
is the reply channel itself.

Stdlib unittest only. Gate-leg checks run with taint cleared and
a user-started, unlocked session (clean-session semantics), the
same shape as the section-F audit's slice-3 harness: the REAL
funnel with a controllable confirm handler registered on the
voice_assistant.core.confirm_gate identity (the module identity
the live session uses).
"""
from __future__ import annotations

import json
import pathlib
import types
import unittest

try:
    from core import confirm_gate, injection
    from core.config import config as app_config
    from core.confirm_gate_allowlist import (
        _in_allowlistable_base, is_allowlistable)
    from core.funnel import tools_gate as tools_mod
    from core.funnel import tools_registry as registry_mod
    from core.funnel.tool_classification import (
        _CONFIRM_GATE_TOOLS, _FREE_BY_DESIGN, classify)
    from core.funnel.tools_gate import _needs_confirmation
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SNAPSHOT = ROOT / "tests" / "_classification_snapshot.json"
ARGS = {"title": "probe", "message": "probe notification xyz"}


def _clean_session(testcase):
    tools_mod.set_session_provenance("user")
    app_config.session_provenance_lock = False
    injection.clear_taint(force=True)
    confirm_gate.clear_handler()
    testcase.addCleanup(confirm_gate.clear_handler)


class TestNotifyUserGate(unittest.TestCase):
    def test_notify_user_is_confirm_gated(self):
        _clean_session(self)
        self.assertIn("notify_user", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("notify_user", _FREE_BY_DESIGN)
        self.assertEqual(classify("notify_user"), "confirm")
        self.assertTrue(_needs_confirmation("notify_user", ARGS))

    def test_gate_set_has_169(self):
        _clean_session(self)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 169)  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169

    def test_snapshot_entry_is_confirm_gated(self):
        snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(
            snap["notify_user"],
            {"confirm": True, "free": None, "read": False})

    def test_no_handler_means_not_confirmed(self):
        _clean_session(self)
        out = registry_mod.tools.execute("notify_user", dict(ARGS))
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))
        self.assertIn(out.get("status"), ("refused", "error"))

    def test_explicit_deny_means_not_confirmed(self):
        _clean_session(self)
        consulted = []
        confirm_gate.register_handler(
            lambda n, s: consulted.append((n, s)) or False)
        out = registry_mod.tools.execute("notify_user", dict(ARGS))
        self.assertTrue(consulted, "the confirm ask never happened")
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))

    def test_approved_call_dispatches_to_real_handler(self):
        _clean_session(self)
        consulted = []
        confirm_gate.register_handler(
            lambda n, s: consulted.append((n, s)) or True)
        out = registry_mod.tools.execute("notify_user", dict(ARGS))
        self.assertTrue(consulted, "the confirm ask never happened")
        self.assertEqual(out.get("status"), "success", str(out))
        text = str(out.get("result") or out.get("message") or "")
        # On Linux the real handler runs and honestly reports the
        # platform limit — the dispatch is proven by its own text.
        self.assertIn("Toast notifications need Windows", text)

    def test_no_standing_grant_can_cover_it(self):
        _clean_session(self)
        self.assertFalse(_in_allowlistable_base("notify_user"))
        self.assertFalse(is_allowlistable("notify_user"))

    def test_sibling_speech_stays_free(self):
        _clean_session(self)
        self.assertFalse(
            _needs_confirmation("speak_text", {"text": "hello"}))
        self.assertEqual(classify("speak_text"), "free")

    def test_read_still_asks_nothing(self):
        _clean_session(self)
        self.assertEqual(classify("get_current_time_date"), "read")
        self.assertFalse(
            _needs_confirmation("get_current_time_date", {}))

    def test_whole_class_pin(self):
        # Every tool that classifies confirm is exactly the gate
        # set: no second, silent gate list can drift off it.
        _clean_session(self)
        names = [d["name"]
                 for d in registry_mod.tools.get_declarations()]
        confirmed = {n for n in names if classify(n) == "confirm"}
        self.assertEqual(confirmed, set(_CONFIRM_GATE_TOOLS))
        self.assertEqual(len(confirmed), 169)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 167 -> 169 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added


if __name__ == "__main__":
    unittest.main()
