"""§7 item 5 — true drag-and-drop (parent draft).

Drives the drag backend protocol with a fake OLE backend: proves
OLE file/text/URL drops with the mechanism named in the receipt,
format negotiation, target-change refusal, source-vanished and
source-payload-mismatch refusals, destructive confirm stop with
zero invokes, failed-drop source-untouched (real file bytes on
disk), no-OLE refusal naming the target, proven-gesture receipt
naming the gesture (never a silent downgrade), auto-run refusal,
by-design walls, credential-field refusal, per-monitor drop-point
conversion at execution time, and honest default-backend
degradation. Live Windows behaviour — real DoDragDrop, real
IDataObject formats, real drop effects — stays field work and
is not claimed here.

Every test resets the handle store, so it cannot poison later
suites in the same process.
"""

import os
import tempfile
import unittest

from core.desktop import drag_drop as dd  # INTENTIONAL (Phase 3 S7a): drag_drop moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}


class FakeDragBackend:
    """Minimal drag protocol: knobs for every negotiation outcome."""

    ole_available = True

    def __init__(self, advertised=("CF_HDROP", "CF_UNICODETEXT",
                                   "UniformResourceLocator"),
                 profile=None, effect="copy", verification="file present",
                 gesture_proven=False, gesture_ok=True):
        self.advertised = list(advertised)
        self.profile = profile
        self.effect = effect
        self.verification = verification
        self._gesture_proven = gesture_proven
        self._gesture_ok = gesture_ok
        self.build_calls = 0
        self.do_calls = 0
        self.gesture_calls = 0

    def advertised_formats(self, target):
        return list(self.advertised)

    def current_target_profile(self, target):
        return dict(self.profile) if self.profile else None

    def build_data_object(self, payload, format_name):
        self.build_calls += 1
        return {"payload": payload, "format": format_name}

    def do_drag_drop(self, data_object, source, target, point):
        self.do_calls += 1
        return {"effect": self.effect}

    def verify_drop(self, target, payload):
        return self.verification

    def gesture_proven(self, target):
        return self._gesture_proven

    def gesture_drag(self, source, target, point):
        self.gesture_calls += 1
        return self._gesture_ok


class FakeMonitorBackend:
    """Monitor protocol: enumerate() returns the current table."""

    def __init__(self, table):
        self.table = table

    def enumerate(self):
        return self.table


def _monitor(device, left, width, scale_x):
    return {
        "monitor_id": device,
        "device": device,
        "left": left,
        "top": 0,
        "width": width,
        "height": 1080,
        "work_left": left,
        "work_top": 0,
        "work_right": left + width - 1,
        "work_bottom": 1079,
        "primary": left == 0,
        "scale_x": scale_x,
        "scale_y": scale_x,
    }


class TestDragDrop(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source_path = os.path.join(self._tmp.name, "note.txt")
        with open(self.source_path, "wb") as fh:
            fh.write(b"drag me")
        self.source = th.mint_file(self.source_path)
        self.target = th.mint_control(FG, name="Editor", control_type="edit")
        self.target_obs = {"identity": dict(FG)}
        self.snapshot = {"stamp": "snap-1"}

    def _drag(self, backend, payload=None, **kw):
        kw.setdefault("source_observation", {})
        kw.setdefault("target_observation", self.target_obs)
        kw.setdefault("snapshot", self.snapshot)
        kw.setdefault("backend", backend)
        kw.setdefault("target_kind", "editor")
        if payload is None:
            payload = {"kind": "files", "files": [self.source_path]}
        return dd.drag(self.source["id"], self.target["id"], payload, **kw)

    def test_file_drop_ole_success_names_mechanism(self):
        backend = FakeDragBackend()
        result = self._drag(backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mechanism"], "ole")
        self.assertEqual(result["effect"], "copy")
        self.assertEqual(result["verification"], "file present")
        self.assertTrue(result["invoked"])
        self.assertTrue(result["source_untouched"])
        self.assertIn("via ole", dd.describe(result))

    def test_text_drop_success(self):
        source = th.mint_control(FG, name="Selection",
                                 control_type="text")
        backend = FakeDragBackend(verification="text inserted")
        result = dd.drag(source["id"], self.target["id"],
                         {"kind": "text", "text": "hello"},
                         source_observation={"identity": dict(FG)},
                         target_observation=self.target_obs,
                         snapshot=self.snapshot, backend=backend,
                         target_kind="editor")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mechanism"], "ole")
        self.assertEqual(result["verification"], "text inserted")

    def test_url_drop_success(self):
        backend = FakeDragBackend()
        result = self._drag(
            backend, {"kind": "url", "url": "https://example.com"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload_kind"], "url")

    def test_format_not_advertised_refuses_and_never_builds(self):
        backend = FakeDragBackend(advertised=("CF_UNICODETEXT",))
        result = self._drag(backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "format-not-advertised")
        self.assertIn("CF_HDROP", result["refusal"]["reason"])
        self.assertEqual(backend.build_calls, 0)
        self.assertEqual(backend.do_calls, 0)
        self.assertFalse(result["invoked"])

    def test_target_changed_kind_refuses(self):
        backend = FakeDragBackend(profile={"kind": "folder"})
        result = self._drag(backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-changed-kind")
        self.assertIn("folder", result["refusal"]["reason"])
        self.assertIn("editor", result["refusal"]["reason"])
        self.assertEqual(backend.do_calls, 0)

    def test_source_vanished_refuses(self):
        os.unlink(self.source_path)
        backend = FakeDragBackend()
        # Payload-level existence check fires first.
        result = self._drag(backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "source-vanished")
        self.assertFalse(result["invoked"])
        self.assertEqual(backend.do_calls, 0)
        # And the item-1 pass-through: a vanished file source with
        # an otherwise-valid text payload still refuses as gone.
        result2 = dd.drag(self.source["id"], self.target["id"],
                          {"kind": "text", "text": "hi"},
                          source_observation={},
                          target_observation=self.target_obs,
                          snapshot=self.snapshot, backend=backend,
                          target_kind="editor")
        self.assertFalse(result2["ok"])
        self.assertEqual(result2["refusal"]["code"], "target-gone")

    def test_source_payload_mismatch_refuses(self):
        other = os.path.join(self._tmp.name, "other.txt")
        with open(other, "wb") as fh:
            fh.write(b"not consented")
        backend = FakeDragBackend()
        result = self._drag(backend, {"kind": "files", "files": [other]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "source-payload-mismatch")
        self.assertEqual(backend.do_calls, 0)

    def test_destructive_confirm_stop_then_confirmed_run(self):
        backend = FakeDragBackend()
        stopped = self._drag(backend, target_kind="trash")
        self.assertFalse(stopped["ok"])
        self.assertTrue(stopped["needs_confirmation"])
        self.assertEqual(stopped["refusal"]["code"],
                         "destructive-needs-confirmation")
        self.assertFalse(stopped["invoked"])
        self.assertEqual(backend.build_calls, 0)
        self.assertEqual(backend.do_calls, 0)
        confirmed = self._drag(backend, target_kind="trash",
                               confirm_destructive=True)
        self.assertTrue(confirmed["ok"])
        self.assertEqual(backend.do_calls, 1)

    def test_failed_drop_source_untouched_on_disk(self):
        backend = FakeDragBackend(effect="none")
        result = self._drag(backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "drop-failed")
        self.assertTrue(result["invoked"])
        self.assertTrue(result["source_untouched"])
        with open(self.source_path, "rb") as fh:
            self.assertEqual(fh.read(), b"drag me")

    def test_no_ole_target_refuses_naming_target(self):
        backend = FakeDragBackend()
        backend.ole_available = False
        refused = self._drag(backend)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["refusal"]["code"],
                         "target-no-ole-support")
        self.assertFalse(refused["invoked"])
        # Even explicitly allowed, an unproven gesture is no fallback.
        refused2 = self._drag(backend, allow_gesture=True)
        self.assertEqual(refused2["refusal"]["code"],
                         "target-no-ole-support")
        self.assertEqual(backend.gesture_calls, 0)

    def test_proven_gesture_receipt_names_gesture(self):
        backend = FakeDragBackend(gesture_proven=True)
        backend.ole_available = False
        result = self._drag(backend, allow_gesture=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["mechanism"], "gesture")
        self.assertIn("via gesture", dd.describe(result))
        self.assertEqual(backend.do_calls, 0)
        self.assertEqual(backend.gesture_calls, 1)

    def test_auto_run_location_refused(self):
        startup = os.path.join(self._tmp.name, "startup")
        os.makedirs(startup)
        folder = th.mint_file(startup)
        backend = FakeDragBackend()
        result = dd.drag(self.source["id"], folder["id"],
                         {"kind": "files", "files": [self.source_path]},
                         source_observation={},
                         target_observation={},
                         snapshot=self.snapshot, backend=backend,
                         target_kind="folder",
                         blocked_locations=[startup])
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "auto-run-location")
        self.assertEqual(backend.do_calls, 0)

    def test_windows_startup_fragment_rule(self):
        path = ("C:\\Users\\phoen\\AppData\\Roaming\\Microsoft\\Windows\\"
                "Start Menu\\Programs\\Startup\\app.exe")
        self.assertTrue(dd._is_auto_run_path(path, ()))
        self.assertFalse(dd._is_auto_run_path(
            "C:\\Users\\phoen\\Documents\\app.exe", ()))

    def test_wall_refused(self):
        backend = FakeDragBackend()
        result = self._drag(backend, target_kind="password-field")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "by-design-wall")
        self.assertFalse(result["invoked"])
        self.assertEqual(backend.do_calls, 0)

    def test_credential_field_refused(self):
        backend = FakeDragBackend()
        result = self._drag(backend, {"kind": "text", "text": "hi",
                                      "api_key": "secret"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "unsupported-payload-field")
        self.assertEqual(backend.build_calls, 0)

    def test_monitor_conversion_at_execution_time(self):
        table = [_monitor("\\\\.\\DISPLAY1", 0, 1920, 1.0)]
        backend = FakeDragBackend()
        result = self._drag(
            backend, monitor_reference="\\\\.\\DISPLAY1",
            target_norm1000=(500, 500),
            monitor_backend=FakeMonitorBackend(table))
        self.assertTrue(result["ok"])
        self.assertEqual(result["drop_point"], (960, 540))
        self.assertIn("DISPLAY1", result["monitor_id"])

    def test_monitor_unreadable_scale_refuses(self):
        table = [_monitor("\\\\.\\DISPLAY2", 0, 1920, None)]
        backend = FakeDragBackend()
        result = self._drag(
            backend, monitor_reference="\\\\.\\DISPLAY2",
            target_norm1000=(500, 500),
            monitor_backend=FakeMonitorBackend(table))
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "monitor-unresolved")
        self.assertIn("unreadable scale", result["refusal"]["reason"])
        self.assertEqual(backend.do_calls, 0)

    def test_default_backend_degrades_honestly(self):
        result = self._drag(None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "target-no-ole-support")
        self.assertFalse(result["invoked"])

    def test_dropped_without_verification_is_honest(self):
        backend = FakeDragBackend(verification=None)
        result = self._drag(backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"],
                         "dropped-without-verification")

    def test_unknown_handle_refuses(self):
        backend = FakeDragBackend()
        result = dd.drag("no-such-handle", self.target["id"],
                         {"kind": "files", "files": [self.source_path]},
                         target_observation=self.target_obs,
                         snapshot=self.snapshot, backend=backend,
                         target_kind="editor")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")
        self.assertEqual(backend.do_calls, 0)


if __name__ == "__main__":
    unittest.main()
