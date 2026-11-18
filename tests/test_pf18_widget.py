"""PF-18 widget batch tests: Settings Apply wiring, folder UI,
and Activity lines for flips.

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. The grants store is
pointed at a temp dir per test; the record-family store is
guarded by _DataGuard (consumers-batch pattern) so Activity
lines written during a test are restored after it.
"""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import permission_grants as pg  # noqa: E402

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    import gui.pf_permissions as pf_mod
    from gui.pf_permissions import (
        PermissionsWidget,
        apply_permission_changes,
        permissions_model,
    )
    _QT = True
except ImportError:
    _QT = False


class _DataGuard:
    """Restore data/ after a test (consumers-batch pattern)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


def _activity_lines():
    from core import record_family as rf
    try:
        entries = rf.read_activity(limit=50)
    except Exception:  # noqa: BLE001 — no line is a test failure, not a crash
        return []
    lines = []
    for entry in entries:
        data = entry.get("data") if isinstance(entry, dict) else None
        if isinstance(data, dict) and data.get("text"):
            lines.append(str(data["text"]))
        elif isinstance(entry, dict) and entry.get("title"):
            lines.append(str(entry["title"]))
    return lines


@unittest.skipUnless(_QT, "Qt unavailable")
class WidgetApplyTests(unittest.TestCase):
    """PF-18 widget batch: Apply writes real grants."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        self._guard.__exit__()
        pg.clear_path_override()
        pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def test_apply_writes_grants(self):
        # INTENTIONAL (PF-18 widget batch): Apply is wired —
        # a grant change lands in the durable store.
        entries = permissions_model()
        record = apply_permission_changes(
            entries, {"browser": "denied", "screen": "granted"})
        self.assertEqual(record["failed"], {})
        self.assertIn("browser", record["changed"])
        self.assertIn("screen", record["changed"])
        self.assertEqual(pg.get_state("browser"), "denied")
        self.assertEqual(pg.get_state("screen"), "granted")

    def test_apply_failure_surfaces(self):
        # INTENTIONAL (PF-18 widget batch): a failed write is
        # reported as failed, never as a silent success.
        entries = permissions_model()
        with mock.patch.object(
                pg, "set_permission_state", return_value=False):
            record = apply_permission_changes(
                entries, {"browser": "denied"})
        self.assertEqual(record["changed"], [])
        self.assertIn("browser", record["failed"])
        self.assertNotEqual(pg.get_state("browser"), "denied")

    def test_flip_back_works(self):
        # INTENTIONAL (PF-18 widget batch): deny then grant
        # through Apply lands both ways.
        entries = permissions_model()
        apply_permission_changes(entries, {"browser": "denied"})
        self.assertEqual(pg.get_state("browser"), "denied")
        apply_permission_changes(entries, {"browser": "granted"})
        self.assertEqual(pg.get_state("browser"), "granted")

    def test_apply_registers_teardown(self):
        # INTENTIONAL (PF-18 widget batch): Apply registers
        # the live voice stop as a mic teardown hook before
        # writing, so a mic denial tears a session down.
        # T-10 (debug pass): the old version faked the module
        # chain voice_assistant.core.voice.audio.audio_manager,
        # but _load_audio_manager() reads the manager from
        # voice_assistant.core.gui_facade (fallback
        # core.gui_facade), so the fake was never consulted and
        # the real AudioDevicesMicMixin.stop got registered.
        # Patch _load_audio_manager itself — the seam the
        # product actually reads. It lives in gui/pf_permissions
        # (the module that defines apply_permission_changes),
        # not in core.permission_grants (pg).
        fake_manager = mock.Mock()
        calls = []
        fake_manager.stop = lambda: calls.append("stop")
        with mock.patch.object(
                pg, "register_mic_teardown",
                wraps=pg.register_mic_teardown) as reg:
            with mock.patch.object(
                    pf_mod, "_load_audio_manager",
                    return_value=fake_manager):
                apply_permission_changes(
                    permissions_model(), {"browser": "denied"})
        self.assertTrue(reg.called)
        self.assertEqual(reg.call_args[0][0], fake_manager.stop)

    def test_widget_apply_end_to_end(self):
        # INTENTIONAL (PF-18 widget batch): the widget's
        # Apply button writes the flipped switches.
        widget = PermissionsWidget()
        widget._switches["browser"].setCheckState(
            Qt.CheckState.Unchecked)
        widget._switches["screen"].setCheckState(
            Qt.CheckState.Checked)
        widget.apply_btn.click()
        self.assertEqual(pg.get_state("browser"), "denied")
        self.assertEqual(pg.get_state("screen"), "granted")
        self.assertIn("Applied", widget.result_label.text())

    def test_widget_no_changes(self):
        # INTENTIONAL (PF-18 widget batch): Apply with no
        # moved switches writes nothing and says so.
        widget = PermissionsWidget()
        widget.apply_btn.click()
        self.assertIn("No changes", widget.result_label.text())
        self.assertFalse(
            (pathlib.Path(self._tmp.name)
             / "permission_grants.json").exists())

    def test_mic_off_notice_shown_when_denied(self):
        # INTENTIONAL (PF-18 widget batch): first-run
        # surfacing — a fresh install reads mic denied, and
        # the page says so plainly instead of auto-granting.
        widget = PermissionsWidget()
        self.assertEqual(pg.get_state("microphone"), "denied")
        self.assertFalse(widget.mic_notice.isHidden())
        widget._switches["microphone"].setCheckState(
            Qt.CheckState.Checked)
        widget.apply_btn.click()
        self.assertEqual(pg.get_state("microphone"), "granted")
        self.assertTrue(widget.mic_notice.isHidden())

    def test_home_devices_nothing_connected(self):
        # INTENTIONAL (PF-18 widget batch, D10): with no
        # devices connected, the home-devices row says so
        # instead of implying an ask-per-use flow.
        entries = {e.permission_id: e for e in permissions_model()}
        self.assertIn("Nothing connected yet",
                      entries["home_devices"].state_line())
        pg.set_permission_state("home_devices", "denied",
                                via="settings")
        denied = {e.permission_id: e for e in permissions_model()}
        self.assertIn("Off", denied["home_devices"].state_line())

    def test_folder_ui_add_remove(self):
        # INTENTIONAL (PF-18 widget batch, D9): the folder
        # section edits the allowlist through the API.
        widget = PermissionsWidget()
        listed = [widget.folder_list.item(i).text()
                  for i in range(widget.folder_list.count())]
        self.assertIn(str(pathlib.Path.home() / "Downloads"), listed)
        new_folder = str(pathlib.Path(self._tmp.name) / "notes")
        widget.folder_input.setText(new_folder)
        widget.folder_add_btn.click()
        listed = [widget.folder_list.item(i).text()
                  for i in range(widget.folder_list.count())]
        self.assertIn(new_folder, listed)
        self.assertIn("Added", widget.folder_feedback.text())
        widget.folder_list.setCurrentRow(listed.index(new_folder))
        widget.folder_remove_btn.click()
        listed = [widget.folder_list.item(i).text()
                  for i in range(widget.folder_list.count())]
        self.assertNotIn(new_folder, listed)
        self.assertIn("Removed", widget.folder_feedback.text())

    def test_folder_ui_empty_input(self):
        # INTENTIONAL (PF-18 widget batch): unjudgeable
        # input errors honestly, never silently skips.
        widget = PermissionsWidget()
        before = [widget.folder_list.item(i).text()
                  for i in range(widget.folder_list.count())]
        widget.folder_input.setText("   ")
        widget.folder_add_btn.click()
        self.assertIn("Type a folder path",
                      widget.folder_feedback.text())
        after = [widget.folder_list.item(i).text()
                 for i in range(widget.folder_list.count())]
        self.assertEqual(before, after)

    def test_flip_activity_line(self):
        # INTENTIONAL (PF-18 widget batch): a flip renders
        # in Activity naming the permission + old -> new.
        apply_permission_changes(
            permissions_model(), {"browser": "denied"})
        lines = _activity_lines()
        self.assertTrue(
            any("Browser control" in line
                and "not set" in line
                and "off" in line
                and "Settings" in line for line in lines),
            f"no flip line in Activity; lines={lines[:5]}")


if __name__ == "__main__":
    unittest.main()
