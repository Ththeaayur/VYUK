"""Shipped unit tests (v5.45, P-1): the speaker lock gate matrix.

Lock off = pre-v5.45 decisions, unchanged. Lock on + a session
that was NOT started by the user from the app (wake word /
autostart / unknown provenance) = state-changing tools that
normally run free now need confirmation, while reads stay free.
Lock on + a user-started session = unchanged. Unknown provenance
fails closed (gated).

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import unittest

try:
    from core import injection
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel import tool_classification as _class_mod  # GAPS-23 (F-V3): the gate branches moved here — the foreground-shell seam lives on this module now
    from core.config import config as app_config
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"tools unavailable: {exc}")  # Phase 4 S1c INTENTIONAL: shim deleted; message names the tool surface

nc = tools_mod._needs_confirmation
FREE_WRITES = (
    ("type_text", {"text": "hello"}),
    ("launch_application", {"app_name": "notepad"}),
    ("open_path", {"path": "C:/docs/notes.txt"}),
)
READS = (
    ("read_file", {"filepath": "a.txt"}),
    ("get_weather", {"city": "Pune"}),
    ("env_var", {"action": "get", "name": "PATH"}),
)


class SpeakerLockTests(unittest.TestCase):
    def setUp(self):
        injection.clear_taint()
        self._orig_fg = _class_mod._foreground_is_shell
        _class_mod._foreground_is_shell = lambda: False
        self._orig_lock = getattr(app_config, "speaker_lock", False)
        self._orig_prov = tools_mod.session_provenance()

    def tearDown(self):
        _class_mod._foreground_is_shell = self._orig_fg
        app_config.speaker_lock = self._orig_lock
        tools_mod.set_session_provenance(self._orig_prov)
        injection.clear_taint()

    def test_lock_off_is_unchanged_for_every_provenance(self):
        app_config.speaker_lock = False
        for prov in ("user", "auto", "unknown"):
            tools_mod.set_session_provenance(prov)
            for name, args in FREE_WRITES:
                self.assertFalse(nc(name, args), f"{prov}: {name}")
            self.assertTrue(nc("run_command", {"command": "echo hi"}))

    def test_lock_on_auto_session_gates_free_writes_not_reads(self):
        app_config.speaker_lock = True
        tools_mod.set_session_provenance("auto")
        for name, args in FREE_WRITES:
            self.assertTrue(nc(name, args), name)
        for name, args in READS:
            self.assertFalse(nc(name, args), name)

    def test_lock_on_user_session_is_unchanged(self):
        app_config.speaker_lock = True
        tools_mod.set_session_provenance("user")
        for name, args in FREE_WRITES:
            self.assertFalse(nc(name, args), name)
        for name, args in READS:
            self.assertFalse(nc(name, args), name)

    def test_lock_on_unknown_provenance_fails_closed(self):
        app_config.speaker_lock = True
        tools_mod.set_session_provenance("unknown")
        for name, args in FREE_WRITES:
            self.assertTrue(nc(name, args), name)
        # A garbage provenance value can never read as user-started.
        tools_mod.set_session_provenance("haxxored")
        self.assertEqual(tools_mod.session_provenance(), "unknown")
        self.assertTrue(nc("type_text", {"text": "x"}))

    def test_provenance_roundtrip(self):
        tools_mod.set_session_provenance("user")
        self.assertEqual(tools_mod.session_provenance(), "user")
        tools_mod.set_session_provenance("auto")
        self.assertEqual(tools_mod.session_provenance(), "auto")


if __name__ == "__main__":
    unittest.main()
