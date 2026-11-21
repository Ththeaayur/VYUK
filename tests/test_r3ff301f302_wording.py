"""Round-3 section F fix queue item 5 (F-F301 + F-F302): refusal wording
must name the real cause.

F-F301 (section F, legs perm-02/perm-04): a never-decided mic/camera
refusal claimed the user "switched it off in Settings" — false, the
user never touched the switch; the permission was never granted.
``permission_never_decided()`` exists but the funnel message never
consulted it. The fix branches every surface that renders that
refusal on ``permission_never_decided(pid)``: the funnel message
(``tools_registry_exec_gates._exec_permission``), the Activity line
for the same call (``tools_registry_exec_setup``), and the capture
guard's camera/mic/screen blocks (``capture_guard`` — the direct-call
path). The denied-by-user wording is byte-identical.

Scope is the fail-closed ids only (microphone, camera —
``FAIL_CLOSED_IDS``): screen, browser, files and home_devices read
"unknown" when never decided and never refuse in that state, so
there is no false claim to fix there. The branches are generic over
the pid, so they stay correct if the fail-closed set ever changes.

F-F302 (section F, 8 legs): a wrong-typed arg was refused by folder
scope with a wrong reason — scope judges the raw values before
validation runs. The fix: in ``_exec_folder_scope``, when scope
would refuse AND validation also refuses the call, the refusal
carries the validation reason (naming the mistyped arg); genuinely
out-of-scope VALID paths keep the scope refusal exactly as before,
and calls with no scope hit never reach the branch (the validate
stage judges them in its own turn). Native calls only; any consult
failure falls through to the scope refusal.

Harness: the funnel legs drive ``TOOLS.execute`` through the VA
funnel (F2 shape) with a deny handler on both gate identities;
permission state is set per test through the product's own API
(delete the store file for never-decided, ``set_permission_state``
for denied/granted, ``add_folder`` for the scope probe dir). Data
is snapshotted in setUpClass and restored in tearDownClass; receipt
rows go to a temp store per test (the C2-F2 idiom).
"""
import pathlib
import shutil
import sys
import tempfile
import types
import unittest

TREE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

# Synthesise the voice_assistant package alias (F2 / F-F204 shape)
# BEFORE any voice_assistant.* import.
if "voice_assistant" not in sys.modules:
    _pkg = types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(TREE)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(TREE / "core")]
    sys.modules["voice_assistant.core"] = _core
if "voice_assistant.gui" not in sys.modules:
    _gui = types.ModuleType("voice_assistant.gui")
    _gui.__path__ = [str(TREE / "gui")]
    sys.modules["voice_assistant.gui"] = _gui

from voice_assistant.core import confirm_gate as va_gate  # noqa: E402
from voice_assistant.core import permission_grants as va_pg  # noqa: E402
from voice_assistant.core import tool_receipts as va_receipts  # noqa: E402
from voice_assistant.core import record_family as va_rf  # noqa: E402
from voice_assistant.core import capture_guard as va_capg  # noqa: E402
from voice_assistant.core.funnel import tools_registry as va_reg  # noqa: E402
import core.confirm_gate as bare_gate  # noqa: E402
from core import injection as _inj  # noqa: E402
from core.funnel import rule_of_two as _rot  # noqa: E402

MIC_DENIED_MSG = (
    "Microphone is switched off in Settings, so this capability was "
    "refused — nothing was run. To use it again, switch microphone "
    "back on in Settings.")
MIC_NEVER_MSG = (
    "Microphone has never been granted, so this capability was "
    "refused — nothing was run. To use it, switch microphone on "
    "in Settings.")
CAM_DENIED_MSG = (
    "Camera is switched off in Settings, so this capability was "
    "refused — nothing was run. To use it again, switch camera "
    "back on in Settings.")
CAM_NEVER_MSG = (
    "Camera has never been granted, so this capability was refused "
    "— nothing was run. To use it, switch camera on in Settings.")


def _fresh_state():
    """Clear session taint + Rule-of-Two legs between calls (C2-F2 idiom)."""
    _inj.clear_taint(force=True)
    _rot.reset_for_tests()


def _clear_store():
    """Never-decided state: no grants store file at all."""
    for name in ("permission_grants.json", "permission_folders.json"):
        try:
            (TREE / "data" / name).unlink()
        except FileNotFoundError:
            pass


def _deny(pid):
    assert va_pg.set_permission_state(pid, "denied",
                                      via="settings", how="Settings"), pid


class _FunnelBase(unittest.TestCase):
    """VA funnel + deny handler; data snapshotted and restored."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="r3ff301f302_")
        tmp = pathlib.Path(cls._tmp.name)
        cls.out = tmp / "out"
        cls.out.mkdir()
        cls._data_snap = tmp / "data_snap"
        shutil.copytree(TREE / "data", cls._data_snap)
        cls.asked = []

        def _handler(name, summary=None, *a, **k):
            cls.asked.append({"tool": name, "summary": summary or ""})
            return False  # DENY — nothing runs; the ask is the assertion.

        cls._handler = _handler
        va_gate.register_handler(_handler)
        bare_gate.register_handler(_handler)

    @classmethod
    def tearDownClass(cls):
        va_gate.clear_handler()
        bare_gate.clear_handler()
        va_receipts.reset_default()
        data = TREE / "data"
        shutil.rmtree(data, ignore_errors=True)
        shutil.copytree(cls._data_snap, data)
        cls._tmp.cleanup()

    def setUp(self):
        self._rtmp = tempfile.TemporaryDirectory(prefix="r3ff301f302_rx_")
        self._receipts = pathlib.Path(self._rtmp.name) / "tool_receipts.jsonl"
        va_receipts.install_store(self._receipts)
        va_receipts.reset_receipt_degraded()
        type(self).asked.clear()
        _fresh_state()

    def tearDown(self):
        va_receipts.reset_default()
        va_receipts.reset_receipt_degraded()
        self._rtmp.cleanup()
        _fresh_state()

    def _execute(self, tool, args):
        return va_reg.tools.execute(tool, dict(args))

    def _activity_line(self, tool_name):
        """Newest Activity line for this tool call, or None."""
        for ev in va_rf.read_activity(limit=50):
            data = ev.get("data") or {} if isinstance(ev, dict) else {}
            text = data.get("text", "")
            if text.startswith(f"Tool {tool_name}:"):
                return text
        return None


class TestNeverDecidedWording(_FunnelBase):
    """F-F301: the funnel refusal names the real permission state."""

    def test_mic_never_says_never_granted(self):
        _clear_store()
        r = self._execute("record_audio", {"seconds": 5})
        self.assertEqual(r.get("kind"), "refused")
        self.assertEqual(r.get("message"), MIC_NEVER_MSG,
                         f"message={r.get('message')!r}")

    def test_mic_never_activity_line_agrees(self):
        _clear_store()
        self._execute("record_audio", {"seconds": 5})
        line = self._activity_line("record_audio")
        self.assertIsNotNone(line, "no Activity line for the refusal")
        self.assertIn("has never been granted", line, f"line={line!r}")
        self.assertNotIn("switched off", line, f"line={line!r}")

    def test_mic_denied_keeps_switched_off(self):
        _clear_store()
        _deny("microphone")
        r = self._execute("record_audio", {"seconds": 5})
        self.assertEqual(r.get("kind"), "refused")
        self.assertEqual(r.get("message"), MIC_DENIED_MSG,
                         f"message={r.get('message')!r}")

    def test_mic_denied_activity_line_agrees(self):
        _clear_store()
        _deny("microphone")
        self._execute("record_audio", {"seconds": 5})
        line = self._activity_line("record_audio")
        self.assertIsNotNone(line, "no Activity line for the refusal")
        self.assertIn("switched off", line, f"line={line!r}")

    def test_camera_never_says_never_granted(self):
        _clear_store()
        r = self._execute("webcam_snapshot", {})
        self.assertEqual(r.get("kind"), "refused")
        self.assertEqual(r.get("message"), CAM_NEVER_MSG,
                         f"message={r.get('message')!r}")

    def test_camera_denied_keeps_switched_off(self):
        _clear_store()
        _deny("camera")
        r = self._execute("webcam_snapshot", {})
        self.assertEqual(r.get("kind"), "refused")
        self.assertEqual(r.get("message"), CAM_DENIED_MSG,
                         f"message={r.get('message')!r}")

    def test_store_with_other_record_still_never_decided(self):
        # The second never-decided shape: the store exists (a mic
        # answer created it) but holds no camera record — the
        # fail-closed "denied" is still not a decision.
        _clear_store()
        assert va_pg.set_permission_state(
            "microphone", "granted", via="settings", how="Settings")
        self.assertEqual(va_pg.get_state("camera"), "denied")
        r = self._execute("webcam_snapshot", {})
        self.assertEqual(r.get("kind"), "refused")
        self.assertEqual(r.get("message"), CAM_NEVER_MSG,
                         f"message={r.get('message')!r}")

    def test_screen_denied_keeps_switched_off(self):
        # Screen is not fail-closed: never-decided reads "unknown"
        # and passes, so only the denied case pins wording here.
        _clear_store()
        _deny("screen")
        r = self._execute("take_screenshot", {})
        self.assertEqual(r.get("kind"), "refused")
        self.assertIn("switched off", r.get("message", ""),
                      f"message={r.get('message')!r}")


class TestGuardWording(_FunnelBase):
    """F-F301: the capture guard (direct-call path) names the state."""

    def _guard(self, kind, pid, never):
        _clear_store()
        if not never:
            _deny(pid)
        return va_capg.ensure_capture_allowed(kind)

    def test_guard_mic_never(self):
        ok_flag, msg = self._guard("microphone", "microphone", True)
        self.assertFalse(ok_flag)
        self.assertEqual(
            msg,
            "Microphone has never been granted, so nothing was "
            "captured. To use it, switch microphone on in Settings.",
            f"msg={msg!r}")

    def test_guard_mic_denied(self):
        ok_flag, msg = self._guard("microphone", "microphone", False)
        self.assertFalse(ok_flag)
        self.assertEqual(
            msg,
            "Microphone is switched off in Settings, so nothing was "
            "captured. To use it again, switch microphone back on in "
            "Settings.",
            f"msg={msg!r}")

    def test_guard_camera_never(self):
        ok_flag, msg = self._guard("camera", "camera", True)
        self.assertFalse(ok_flag)
        self.assertEqual(
            msg,
            "Camera has never been granted, so nothing was captured. "
            "To use it, switch camera on in Settings.",
            f"msg={msg!r}")

    def test_guard_camera_denied(self):
        ok_flag, msg = self._guard("camera", "camera", False)
        self.assertFalse(ok_flag)
        self.assertEqual(
            msg,
            "Camera is switched off in Settings, so nothing was "
            "captured. To use it again, switch camera back on in "
            "Settings.",
            f"msg={msg!r}")

    def test_guard_screen_denied(self):
        ok_flag, msg = self._guard("screenshot_save", "screen", False)
        self.assertFalse(ok_flag)
        self.assertEqual(
            msg,
            "Screen is switched off in Settings, so nothing was "
            "captured. To use it again, switch screen back on in "
            "Settings.",
            f"msg={msg!r}")


class TestScopeValidationOrder(_FunnelBase):
    """F-F302: a mistyped arg refuses with the validation reason."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.probe = pathlib.Path(cls._tmp.name) / "probe"
        cls.probe.mkdir()

    def _grant_files(self):
        _clear_store()
        assert va_pg.set_permission_state(
            "files", "granted", via="settings", how="Settings")
        assert va_pg.add_folder(str(self.probe), via="settings")

    def _f302(self, tool, args, want_arg):
        self._grant_files()
        r = self._execute(tool, args)
        want = f"'{want_arg}' must be a string (got int)"
        self.assertEqual(r.get("kind"), "validation",
                         f"{tool}: kind={r.get('kind')!r} "
                         f"message={r.get('message')!r}")
        self.assertIn(want, r.get("message", ""),
                      f"{tool}: message={r.get('message')!r}")
        self.assertNotIn("outside the folders", r.get("message", ""),
                         f"{tool}: message={r.get('message')!r}")

    def test_add_border_path_int(self):
        self._f302("add_border", {"path": 12345}, "path")

    def test_audio_fade_path_int(self):
        self._f302("audio_fade", {"path": 12345}, "path")

    def test_batch_rename_folder_int(self):
        self._f302("batch_rename",
                   {"folder": 12345, "find": "x", "replace": "x"},
                   "folder")

    def test_color_from_image_path_int(self):
        self._f302("color_from_image", {"path": 12345}, "path")

    def test_copy_file_to_clipboard_path_int(self):
        self._f302("copy_file_to_clipboard", {"path": 12345}, "path")

    def test_comms_record_draft_json_int(self):
        self._f302("comms_record_draft",
                   {"draft_json": 12345, "output_path": "x"},
                   "draft_json")

    def test_access_record_preference_json_int(self):
        self._f302("access_record_preference",
                   {"preference_json": 12345, "output_path": "x"},
                   "preference_json")

    def test_antic_cards_report_json_int(self):
        self._f302("antic_cards_report",
                   {"context_json": 12345, "grant_json": "x",
                    "output_path": "x"}, "context_json")

    def test_genuinely_out_of_scope_keeps_scope_refusal(self):
        self._grant_files()
        r = self._execute("add_border", {"path": "/etc/outside.txt"})
        self.assertEqual(r.get("kind"), "refused",
                         f"kind={r.get('kind')!r} "
                         f"message={r.get('message')!r}")
        self.assertIn("outside the folders", r.get("message", ""),
                      f"message={r.get('message')!r}")
        self.assertNotIn("must be a string", r.get("message", ""),
                         f"message={r.get('message')!r}")

    def test_valid_in_scope_call_dispatches(self):
        self._grant_files()
        entry = va_reg.tools._tools["add_border"]
        orig_handler = entry["handler"]

        def _approve(name, summary=None, *a, **k):
            return True

        ran = []

        def _recording_stub(*a, **k):
            ran.append("add_border")
            return "stub-ok"

        entry["handler"] = _recording_stub
        va_gate.register_handler(_approve)
        bare_gate.register_handler(_approve)
        try:
            r = self._execute(
                "add_border", {"path": str(self.probe / "img.png")})
        finally:
            entry["handler"] = orig_handler
            va_gate.register_handler(type(self)._handler)
            bare_gate.register_handler(type(self)._handler)
        self.assertEqual(ran, ["add_border"], f"result={r!r}")
        self.assertNotEqual(r.get("kind"), "refused",
                            f"result={r!r}")
        self.assertNotEqual(r.get("kind"), "validation",
                            f"result={r!r}")


if __name__ == "__main__":
    unittest.main()
