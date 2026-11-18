"""Shipped unit tests (v5.50, programme item 41): the scripted
FakeBackend scenario.

Drives a scripted "open dialog → click Save" scenario through
ACTUAL tool code — ToolRegistry.execute → core/desktop/control.py /
core/desktop/uia.py handlers → core.platform.get_backend() → FakeBackend
— on any OS, including headless Linux: VYUK_PLATFORM=fake selects
the fake, the consumers route to it, and the scenario asserts
the tool receipts, the scripted state transition (dialog closed,
document saved), and the fake's in-order input log.

Stdlib unittest only. Run via tests/run_tests.py. The execute()
audit/activity writes are snapshot-restored so the run leaves
data/ byte-identical.
"""

from __future__ import annotations

import os
import pathlib
import unittest

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent


class _DataGuard:
    """Snapshot/restore the two files execute() appends to."""

    _NAMES = ("activity.log", "audit.jsonl")

    def __enter__(self):
        self._saved = {}
        for name in self._NAMES:
            path = _PACKAGE_ROOT / "data" / name
            self._saved[name] = path.read_bytes() if path.exists() else None
        return self

    def __exit__(self, *exc):
        for name, data in self._saved.items():
            path = _PACKAGE_ROOT / "data" / name
            try:
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class FakeBackendScenarioTests(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("VYUK_PLATFORM")
        os.environ["VYUK_PLATFORM"] = "fake"
        try:
            from core import injection

            clear = getattr(injection, "clear_taint", None)
            if callable(clear):
                clear()
        except Exception:  # noqa: BLE001 — taint hygiene is best-effort
            pass
        from core.platform import get_backend, reset_backend_cache

        reset_backend_cache()
        self.backend = get_backend()
        editor = self.backend.add_window(
            "Untitled - Editor", rect=(0, 0, 900, 700),
            process="editor.exe")
        self.backend.add_control(
            editor, "Document text", control_type="Document",
            rect=(10, 40, 890, 690))

        def _open_dialog(be):
            dlg = be.add_window("Save As", rect=(200, 150, 700, 520),
                                process="editor.exe")
            be.add_control(dlg, "File name", control_type="Edit",
                           rect=(220, 420, 620, 460), is_password=False)
            be.add_control(dlg, "Save", control_type="Button",
                           rect=(520, 470, 620, 505))
            be.add_control(dlg, "Cancel", control_type="Button",
                           rect=(390, 470, 500, 505))
            be.set_foreground(dlg)

        self.backend.script_hotkey("ctrl+s", _open_dialog)
        from core.funnel import tools_registry as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)

        self.registry = tools_mod.tools

    def tearDown(self):
        from core.platform import reset_backend_cache

        reset_backend_cache()
        if self._old_env is None:
            os.environ.pop("VYUK_PLATFORM", None)
        else:
            os.environ["VYUK_PLATFORM"] = self._old_env

    def test_open_dialog_click_save_through_tools(self):
        with _DataGuard():
            opened = self.registry.execute(
                "press_hotkey", {"keys": "ctrl+s"})
            self.assertEqual(opened.get("status"), "success", opened)
            self.assertEqual(self.backend.foreground_title(), "Save As")

            tree = self.registry.execute("uia_read_tree", {})
            self.assertEqual(tree.get("status"), "success", tree)
            self.assertIn("Save", tree.get("result", ""))

            typed = self.registry.execute(
                "uia_type_text",
                {"name": "File name", "text": "report.txt"})
            self.assertEqual(typed.get("status"), "success", typed)
            self.assertIn("Typed 10 character(s)", typed.get("result", ""))
            # The typed text is never echoed in the result.
            self.assertNotIn("report.txt", typed.get("result", ""))

            clicked = self.registry.execute(
                "uia_click_control", {"name": "Save"})
            self.assertEqual(clicked.get("status"), "success", clicked)
            self.assertIn("Clicked 'Save'", clicked.get("result", ""))

        # Scripted state transition: dialog closed, document saved,
        # focus back on the editor, inputs logged in order.
        self.assertTrue(self.backend.state.get("saved"))
        self.assertEqual(self.backend.foreground_title(),
                         "Untitled - Editor")
        methods = [entry["method"] for entry in self.backend.input_log]
        self.assertEqual(
            methods, ["press_hotkey", "uia_type_text", "uia_click_control"])

    def test_missing_control_clicks_nothing(self):
        with _DataGuard():
            result = self.registry.execute(
                "uia_click_control", {"name": "No Such Button"})
        self.assertIn("No control with a name containing",
                      result.get("result", ""))
        self.assertEqual(self.backend.input_log, [])


if __name__ == "__main__":
    unittest.main()
