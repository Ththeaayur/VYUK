"""PF-18 build batches 1–9 — grants store + funnel choke point + mic
(batch 1) and camera (batch 2) bindings, plus mic-at-guard symmetry
(batch 3), the screen binding (batch 4), the browser binding
(batch 5), the web_search fallback gate (batch 6), the files
binding + D9 folder allowlist (batch 7), the writer disposition
+ binding (batch 8) and the home_devices honest-empty hook (batch 9).

Lab leg only (D11 field leg on real Windows is OWED, not this batch).
Every test here runs against a temp store path (permission_grants
test seam) so the real data dir is never touched; funnel tests also
use the consumers-batch _DataGuard pattern for audit/receipt writes.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _DataGuard:
    """Restore data/ after funnel executes (consumers-batch pattern)."""

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


class GrantsStoreTests(unittest.TestCase):
    """Store shape, Settings-only writes, fail-closed reads."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        # Save/restore the process-level teardown hooks.
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def test_store_round_trip(self):
        # INTENTIONAL (PF-18 batch 1): the store envelope and record
        # shape are pinned here, not just the state value.
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        self.assertEqual(self.pg.get_state("microphone"), "granted")
        record = self.pg.get_record("microphone")
        self.assertEqual(record["permission_id"], "microphone")
        self.assertEqual(record["state"], "granted")
        self.assertEqual(record["source"], "user")
        self.assertTrue(record["updated_at"])
        self.assertTrue(record["scope_note"])
        payload = json.loads(self._store.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["changes"]), 1)
        event = payload["changes"][0]
        self.assertEqual(event["permission_id"], "microphone")
        # No store file yet, so mic's effective prior state is the
        # fail-closed "denied" — not "unknown". The event records
        # what the gate actually enforced before the flip.
        self.assertEqual(event["old_state"], "denied")
        self.assertEqual(event["new_state"], "granted")

    def test_settings_only_write(self):
        # D8: voice / plan / session paths can never flip a grant.
        for via in ("voice", "plan", "session", "allow_once", ""):
            self.assertFalse(
                self.pg.set_permission_state("microphone", "denied",
                                             via=via))
        self.assertFalse(self._store.exists())
        self.assertFalse(
            self.pg.set_permission_state("not_a_permission", "denied",
                                         via="settings"))
        self.assertFalse(
            self.pg.set_permission_state("microphone", "maybe",
                                         via="settings"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))

    def test_fail_closed_missing_store(self):
        # D2 carve-out: no file yet reads denied for mic/camera,
        # unknown for the rest — never granted, never an ask trap.
        self.assertEqual(self.pg.get_state("microphone"), "denied")
        self.assertEqual(self.pg.get_state("camera"), "denied")
        for pid in ("screen", "files", "browser", "home_devices"):
            self.assertEqual(self.pg.get_state(pid), "unknown")

    def test_fail_closed_corrupt_store(self):
        for bad in ("not json at all", "[1, 2, 3]", '{"grants": 7}'):
            self._store.write_text(bad, encoding="utf-8")
            self.assertEqual(self.pg.get_state("microphone"), "denied")
            self.assertEqual(self.pg.get_state("camera"), "denied")
            self.assertEqual(self.pg.get_state("screen"), "unknown")
        # An invalid state value in a record fails the same way.
        self._store.write_text(json.dumps({
            "version": 1,
            "grants": {"microphone": {"state": "maybe"},
                       "screen": {"state": "maybe"}},
            "changes": []}), encoding="utf-8")
        self.assertEqual(self.pg.get_state("microphone"), "denied")
        self.assertEqual(self.pg.get_state("screen"), "unknown")

    def test_write_failure_reports_false(self):
        blocker = pathlib.Path(self._tmp.name) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        self.pg.set_path_override(blocker / "permission_grants.json")
        self.assertFalse(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))

    def test_allow_once_never_writes(self):
        # D3: an EXPLICITLY unknown mic goes through the confirm
        # path, and nothing about that path writes a durable
        # grant. (No file at all reads denied for mic — the D2
        # carve-out — so unknown here must be written first.)
        self.assertTrue(
            self.pg.set_permission_state("microphone", "unknown",
                                         via="settings"))
        allowed, pid = self.pg.check_tool("mic_check")
        self.assertTrue(allowed)
        self.assertIsNone(pid)
        self.assertEqual(self.pg.get_state("microphone"), "unknown")
        payload = json.loads(self._store.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["changes"]), 1)

    def test_mic_tool_map(self):
        # INTENTIONAL (PF-18 batch 1): the bound set is pinned —
        # audio INPUT only; enumeration, output, file ops and
        # text heuristics are deliberately not bound TO MICROPHONE
        # (batch 7 binds the file ops / text heuristics to FILES —
        # a different id — so they leave this pin's unbound list
        # for the files map pin, they are not unbound overall).
        for tool in ("mic_check", "room_noise_level", "voice_note",
                     "listen_for_sound", "record_audio", "mic_control",
                     "set_microphone_mute"):
            self.assertEqual(self.pg.permission_for_tool(tool),
                             "microphone")
        for tool in ("list_audio_devices", "speaker_check", "speak_text"):
            # INTENTIONAL (PF-18 batch 2): webcam_snapshot was
            # unbound in batch 1 and is now camera-bound — the
            # camera map pin covers it; it no longer belongs here.
            # INTENTIONAL (PF-18 batch 4): take_screenshot was
            # unbound in batches 1–3 and is now screen-bound — the
            # screen map pin covers it; it no longer belongs here.
            # INTENTIONAL (PF-18 batch 7): audio_fade, audio_trim
            # and passive_voice_check were unbound in batches 1–6
            # and are now files-bound (file ops / file-reading
            # text tools) — the files map pin covers them; they
            # no longer belong here.
            self.assertIsNone(self.pg.permission_for_tool(tool))


class FunnelChokePointTests(unittest.TestCase):
    """Real funnel dispatch: refusal shape, ordering, grant rules."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def _deny_mic(self):
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))

    def test_attempt_after_revoke_refused(self):
        # PF-18 criterion clause (2), lab leg: denied mic, real
        # funnel dispatch, refused naming the permission.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_mic()
            result = self._tools.execute("mic_check", {})
        finally:
            confirm_gate.clear_handler()
        # The funnel's structured-result contract normalises a
        # refusal kind to status "refused" (see _audited).
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "microphone")
        self.assertIn("Microphone", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        # Ordering: the confirm handler was never even asked.
        self.assertEqual(calls, [])

    def test_flip_back_no_longer_permission_refused(self):
        # Flip back through the real funnel: the stage passes and
        # dispatch runs (handler stubbed — the real mic handler
        # would open audio hardware in the lab).
        self._deny_mic()
        refused = self._tools.execute("mic_check", {})
        self.assertEqual(refused.get("kind"), "refused")
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        entry = self._tools._tools["mic_check"]
        orig = entry["handler"]
        entry["handler"] = lambda **kw: "stub-ok"
        try:
            result = self._tools.execute("mic_check", {})
        finally:
            entry["handler"] = orig
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "microphone"))

    def test_grant_never_skips_confirm(self):
        # D4: a granted mic does not free a confirm-gated action's
        # ask — the permission stage passes, the confirm stage
        # still asks, and a decline still refuses.
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        calls = []
        confirm_gate.register_handler(
            lambda n, s: calls.append(n) or False)
        try:
            result = self._tools.execute("clean_temp_files", {})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(calls, ["clean_temp_files"])
        self.assertEqual(result.get("kind"), "not_confirmed")

    def test_unknown_mic_reaches_dispatch(self):
        # D3: an EXPLICITLY unknown mic passes the choke point —
        # the call is NOT refused, it reaches dispatch (handler
        # stubbed so the lab never opens audio hardware), and
        # the stored state stays unknown (no durable grant was
        # written). (No file at all reads denied for mic — the
        # D2 carve-out — so unknown here must be written first.)
        self.assertTrue(
            self.pg.set_permission_state("microphone", "unknown",
                                         via="settings"))
        entry = self._tools._tools["mic_check"]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        try:
            result = self._tools.execute("mic_check", {})
        finally:
            entry["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "microphone"))
        self.assertEqual(self.pg.get_state("microphone"), "unknown")


class MicTeardownTests(unittest.TestCase):
    """Flip → teardown: approval cleared, hooks fired, sessions stop."""

    def setUp(self):
        from core import capture_guard, permission_grants as pg
        self.cg = capture_guard
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._saved_approved = set(capture_guard._approved_kinds)
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        self.cg._approved_kinds.clear()
        self.cg._approved_kinds.update(self._saved_approved)
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def test_flip_denied_clears_approval_and_runs_hooks(self):
        self.cg._approved_kinds.add("microphone")
        calls = []
        self.pg.register_mic_teardown(lambda: calls.append("hook"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        self.assertNotIn("microphone", self.cg._approved_kinds)
        self.assertEqual(calls, ["hook"])

    def test_flip_granted_runs_no_hooks(self):
        calls = []
        self.pg.register_mic_teardown(lambda: calls.append("hook"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        self.assertEqual(calls, [])

    def test_failing_hook_never_breaks_flip_or_others(self):
        calls = []

        def _bad():
            raise RuntimeError("boom")

        self.pg.register_mic_teardown(_bad)
        self.pg.register_mic_teardown(lambda: calls.append("good"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        self.assertEqual(calls, ["good"])
        self.assertEqual(self.pg.get_state("microphone"), "denied")

    def test_sidecar_stop_via_hook(self):
        # Lab proof of the D2 teardown: a running sidecar stops
        # when its stop is registered and mic flips to denied.
        from core.sound_event_sidecar import (
            SoundEventOptIn, SoundEventSidecar)
        opt_in = SoundEventOptIn(
            mic_approval_fn=lambda kind: True,
            session_token_fn=lambda: "test-session")
        opt_in.grant()
        self.assertTrue(opt_in.is_granted())

        class _FakeStream:
            def __init__(self):
                self.stopped = False

            def start(self):
                pass

            def stop(self):
                self.stopped = True

        sidecar = SoundEventSidecar(opt_in=opt_in)
        fake = _FakeStream()
        ok, _msg = sidecar.start(stream_factory=lambda cb: fake)
        self.assertTrue(ok)
        self.assertTrue(sidecar.is_running)
        self.pg.register_mic_teardown(sidecar.stop)
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        self.assertFalse(sidecar.is_running)
        self.assertTrue(fake.stopped)

    def test_voice_start_refused_when_denied(self):
        # The live-voice revival guard: a denied mic leaves the
        # input stream closed (output is untouched by this check).
        # sounddevice/PortAudio are absent in the lab, so the
        # module is imported behind a stub — start() with a
        # denied mic returns before touching any audio API.
        import sys
        import types
        stub = types.ModuleType("sounddevice")
        saved = sys.modules.get("sounddevice")
        sys.modules["sounddevice"] = stub
        try:
            from core.voice.audio_devices_mic import AudioDevicesMicMixin
        finally:
            if saved is None:
                sys.modules.pop("sounddevice", None)
            else:
                sys.modules["sounddevice"] = saved
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        mixin = AudioDevicesMicMixin.__new__(AudioDevicesMicMixin)
        mixin._in_stream = None
        mixin.is_recording = False
        # Output is out of scope here (and needs PortAudio): a
        # dummy non-None output stream skips the output block.
        mixin._out_stream = object()
        mixin.start()
        self.assertIsNone(mixin._in_stream)
        self.assertFalse(mixin.is_recording)


class LedgerKeyTests(unittest.TestCase):
    """permission_changes: additive key, rebuild restore, no bump."""

    def test_round_trip(self):
        from core.brain.task_ledger import TaskLedger, task_to_record

        class _Task:
            id = "t-pf18"
            classification = "routine"
            title = "pf18 test"
            state = "paused"
            steps = []
            outcomes = []
            verification = []
            _retries = []
            _second_opinions = []
            _permission_changes = [{
                "permission_id": "microphone",
                "old_state": "unknown",
                "new_state": "denied",
                "source": "user",
                "at": "2026-11-05T00:00:00+00:00",
            }]

            def receipt(self):
                class _R:
                    reason = ""
                    error_kind = ""
                return _R()

        record = task_to_record(_Task())
        self.assertEqual(len(record["permission_changes"]), 1)
        self.assertEqual(record["permission_changes"][0]["permission_id"],
                         "microphone")
        rebuilt = TaskLedger().rebuild_task(record)
        self.assertEqual(len(rebuilt._permission_changes), 1)
        self.assertEqual(
            rebuilt._permission_changes[0]["new_state"], "denied")

    def test_old_records_load_empty(self):
        from core.brain.task_ledger import TaskLedger
        record = {
            "task_id": "t-old",
            "state": "paused",
            "steps": [],
            "outcomes": [],
        }
        rebuilt = TaskLedger().rebuild_task(record)
        self.assertEqual(rebuilt._permission_changes, [])


class CameraBindingTests(unittest.TestCase):
    """PF-18 batch 2: camera tool map + guard binding + teardown."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_camera_tool_map(self):
        # INTENTIONAL (PF-18 batch 2): the bound set is pinned —
        # every tool that opens the webcam device, including
        # screen_brightness_auto (its effect is screen, but it
        # takes a camera frame to measure room light, so it is
        # camera capture under D2). Screen tools, mic tools and
        # browser site reports are deliberately not bound: they
        # belong to their own ids' batches (or, for the site
        # report, are read-only reporting, not device capture).
        for tool in ("webcam_snapshot", "describe_camera_view",
                     "screen_brightness_auto"):
            self.assertEqual(self.pg.permission_for_tool(tool),
                             "camera")
        for tool in ("site_permissions_review",
                     "list_audio_devices", "speaker_check"):
            # INTENTIONAL (PF-18 batch 4): take_screenshot was
            # unbound in batch 2 and is now screen-bound — the
            # screen map pin covers it; it no longer belongs here.
            self.assertIsNone(self.pg.permission_for_tool(tool))
        # INTENTIONAL (PF-18 batch 5): browser is now bound, so the
        # remaining unbound ids are two, not three.
        # INTENTIONAL (PF-18 batch 7): files is now bound, so the
        # only remaining unbound id is home_devices.
        for pid in ("home_devices",):
            self.assertEqual(self.pg.PERMISSION_TOOL_MAP[pid],
                             frozenset())

    def test_guard_refuses_when_denied(self):
        # PF-18 batch 2: a durable camera denial refuses at the
        # capture guard too, before any ask — even with an
        # approving confirm handler registered, it is never called.
        from core import capture_guard as cg
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("camera", "denied",
                                         via="settings"))
        calls = []
        confirm_gate.register_handler(
            lambda n, s: calls.append(n) or True)
        try:
            allowed, msg = cg.ensure_capture_allowed("camera")
        finally:
            confirm_gate.clear_handler()
        self.assertFalse(allowed)
        self.assertIn("Camera", msg)
        self.assertIn("Settings", msg)
        self.assertEqual(calls, [])

    def test_camera_flip_clears_only_camera_approval(self):
        # PF-18 batch 2: denying camera clears the camera session
        # approval and leaves the microphone approval untouched
        # (one-kind semantics, batch-1 revoke_capture_approval).
        from core import capture_guard as cg
        # The guard's durable pre-check runs before the ask: with no
        # store file, camera reads denied (fail-closed), so the
        # approval can only be recorded once camera is granted.
        # INTENTIONAL (PF-18 batch 3): the same now holds for
        # microphone — batch 3's mic-at-guard pre-check refuses the
        # mic guard call unless mic is granted, so the mic approval
        # below can only be recorded once mic is granted too.
        self.assertTrue(
            self.pg.set_permission_state("camera", "granted",
                                         via="settings"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        orig_block = cg.foreground_capture_block
        orig_policy = cg.capture_policy
        orig_ask = cg._ask_human
        cg.foreground_capture_block = lambda: None
        cg.capture_policy = lambda: "ask-once"
        cg._ask_human = lambda kind: True
        try:
            allowed_cam, _ = cg.ensure_capture_allowed("camera")
            allowed_mic, _ = cg.ensure_capture_allowed("microphone")
        finally:
            cg.foreground_capture_block = orig_block
            cg.capture_policy = orig_policy
            cg._ask_human = orig_ask
        self.assertTrue(allowed_cam)
        self.assertTrue(allowed_mic)
        self.assertTrue(cg.is_capture_approved("camera"))
        self.assertTrue(cg.is_capture_approved("microphone"))
        self.assertTrue(
            self.pg.set_permission_state("camera", "denied",
                                         via="settings"))
        self.assertFalse(cg.is_capture_approved("camera"))
        self.assertTrue(cg.is_capture_approved("microphone"))
        # Leave no approval behind for other suites.
        cg.revoke_capture_approval("microphone")


class CameraFunnelTests(unittest.TestCase):
    """PF-18 batch 2: camera refusal through the real funnel."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _deny_camera(self):
        self.assertTrue(
            self.pg.set_permission_state("camera", "denied",
                                         via="settings"))

    def test_camera_attempt_after_revoke_refused(self):
        # PF-18 criterion clause (2), lab leg, camera: denied
        # camera, real funnel dispatch, refused naming the
        # permission — via describe_camera_view, whose handler
        # makes NO guard call, so this refusal can only come
        # from the funnel choke point.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_camera()
            result = self._tools.execute("describe_camera_view", {})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "camera")
        self.assertIn("Camera", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        # Ordering: the confirm handler was never even asked.
        self.assertEqual(calls, [])

    def test_webcam_snapshot_funnel_refused(self):
        # Same choke point, guard-path tool: denied camera refuses
        # webcam_snapshot before its handler (which would otherwise
        # reach the capture guard's ask).
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_camera()
            result = self._tools.execute("webcam_snapshot", {})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "camera")
        self.assertEqual(calls, [])

    def test_camera_flip_back_no_longer_refused(self):
        # Flip back through the real funnel: the stage passes and
        # dispatch runs (handler stubbed — the real camera handler
        # would open hardware in the lab).
        self._deny_camera()
        refused = self._tools.execute("describe_camera_view", {})
        self.assertEqual(refused.get("kind"), "refused")
        self.assertTrue(
            self.pg.set_permission_state("camera", "granted",
                                         via="settings"))
        entry = self._tools._tools["describe_camera_view"]
        orig = entry["handler"]
        entry["handler"] = lambda **kw: "stub-ok"
        try:
            result = self._tools.execute("describe_camera_view", {})
        finally:
            entry["handler"] = orig
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "camera"))

    def test_unknown_camera_reaches_dispatch(self):
        # D3, camera shape: an EXPLICITLY unknown camera passes the
        # choke point — the call is NOT refused, it reaches
        # dispatch (handler stubbed), and the stored state stays
        # unknown (no durable grant was written).
        self.assertTrue(
            self.pg.set_permission_state("camera", "unknown",
                                         via="settings"))
        entry = self._tools._tools["describe_camera_view"]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        try:
            result = self._tools.execute("describe_camera_view", {})
        finally:
            entry["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "camera"))
        self.assertEqual(self.pg.get_state("camera"), "unknown")


class MicGuardSymmetryTests(unittest.TestCase):
    """PF-18 batch 3: microphone consults the durable store at the
    capture guard too, mirroring camera (batch 2). A direct handler
    call bypassing the funnel (record_audio) must not bypass the
    durable mic grant — D2's "off means no session can exist"
    holds on both paths."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        from core import capture_guard as cg
        # Leave no session approval behind for other suites.
        cg.revoke_capture_approval("microphone")
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _stub_ask(self, cg):
        # Ask machinery stubbed to approve (camera-test shape): the
        # durable check runs before any of this, so a durable
        # refusal is visible as "never reached the ask".
        orig_block = cg.foreground_capture_block
        orig_policy = cg.capture_policy
        orig_ask = cg._ask_human
        calls = []
        cg.foreground_capture_block = lambda: None
        cg.capture_policy = lambda: "ask-once"
        cg._ask_human = lambda kind: calls.append(kind) or True
        return orig_block, orig_policy, orig_ask, calls

    def _unstub_ask(self, cg, saved):
        orig_block, orig_policy, orig_ask, _calls = saved
        cg.foreground_capture_block = orig_block
        cg.capture_policy = orig_policy
        cg._ask_human = orig_ask

    def test_guard_refuses_when_denied(self):
        # INTENTIONAL (PF-18 batch 3): a durable mic denial refuses
        # at the capture guard too, before any ask — even with an
        # approving confirm handler registered, it is never called.
        # This is the direct-call path (record_audio) that batch 1's
        # funnel-only binding left open.
        from core import capture_guard as cg
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        calls = []
        confirm_gate.register_handler(
            lambda n, s: calls.append(n) or True)
        try:
            allowed, msg = cg.ensure_capture_allowed("microphone")
        finally:
            confirm_gate.clear_handler()
        self.assertFalse(allowed)
        self.assertIn("Microphone", msg)
        self.assertIn("Settings", msg)
        self.assertEqual(calls, [])

    def test_guard_granted_not_durable_refused(self):
        # INTENTIONAL (PF-18 batch 3): a granted mic passes the
        # durable check and reaches the normal ask path — the guard
        # does not refuse on durable grounds, and the ask runs.
        from core import capture_guard as cg
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        saved = self._stub_ask(cg)
        try:
            allowed, msg = cg.ensure_capture_allowed("microphone")
        finally:
            self._unstub_ask(cg, saved)
        self.assertTrue(allowed)
        self.assertEqual(msg, "")
        self.assertEqual(saved[3], ["microphone"])

    def test_guard_unknown_not_durable_refused(self):
        # INTENTIONAL (PF-18 batch 3): an EXPLICITLY unknown mic is
        # not durable-refused (D3: unknown asks) — it reaches the
        # ask path and the stored state stays unknown.
        from core import capture_guard as cg
        self.assertTrue(
            self.pg.set_permission_state("microphone", "unknown",
                                         via="settings"))
        saved = self._stub_ask(cg)
        try:
            allowed, msg = cg.ensure_capture_allowed("microphone")
        finally:
            self._unstub_ask(cg, saved)
        self.assertTrue(allowed)
        self.assertEqual(msg, "")
        self.assertEqual(self.pg.get_state("microphone"), "unknown")

    def test_guard_consult_failure_fails_closed(self):
        # INTENTIONAL (PF-18 batch 3): a broken store must never
        # silently open the microphone — corrupt file and consult
        # exception both refuse with the Settings reason.
        from core import capture_guard as cg
        self._store.write_bytes(b"{not json")
        allowed, msg = cg.ensure_capture_allowed("microphone")
        self.assertFalse(allowed)
        self.assertIn("Microphone", msg)
        self.assertIn("Settings", msg)
        orig_get_state = self.pg.get_state
        self.pg.get_state = lambda pid: (_ for _ in ()).throw(
            RuntimeError("consult broken"))
        try:
            allowed, msg = cg.ensure_capture_allowed("microphone")
        finally:
            self.pg.get_state = orig_get_state
        self.assertFalse(allowed)
        self.assertIn("Microphone", msg)
        self.assertIn("Settings", msg)


class ScreenBindingTests(unittest.TestCase):
    """PF-18 batch 4: screen tool map + guard binding + teardown."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        # The screen-watch switchboard is process-wide: save and
        # restore it so no test leaks mode state to other suites.
        from core.desktop import screen_watch as sw
        self._sw = sw
        self._watch_was = sw.watch_enabled()

    def tearDown(self):
        self._sw.set_watch_enabled(self._watch_was, source="test-restore")
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_screen_tool_map(self):
        # INTENTIONAL (PF-18 batch 4): the bound set is pinned —
        # every tool that opens screen frames. screen_diff captures
        # on both actions (save_baseline grabs, compare grabs a
        # fresh frame); occluded_capture is a capture attempt whose
        # handler approval still applies when granted/unknown.
        for tool in ("take_screenshot", "screen_look", "ocr_screen",
                     "screenshot_region", "capture_window",
                     "screen_diff", "occluded_capture"):
            self.assertEqual(self.pg.permission_for_tool(tool),
                             "screen")
        # Deliberately not bound: cursor_control is mouse act only
        # (it consumes coordinates FROM screenshots but never
        # captures — design §5.3's "cursor_control monitor
        # captures" is corrected here: the monitor parameter
        # belongs to take_screenshot, not cursor_control);
        # capture_route reports the route and takes no capture;
        # monitor_action is a pure coordinate read; monitor_control
        # lists/moves windows; access_window_describe reads only a
        # caller-supplied snapshot, never a live screen;
        # window_control and get_active_window are window
        # management/titles, not frames; ocr_file and
        # screenshot_to_text_note read existing files (and are
        # files-bound since batch 7 — the files map pin covers
        # them, so they leave this pin's unbound list);
        # screen_brightness_auto is camera-bound (batch 2);
        # site_permissions_review is a browser report.
        for tool in ("cursor_control", "capture_route",
                     "monitor_action", "monitor_control",
                     "access_window_describe", "window_control",
                     "get_active_window",
                     "site_permissions_review"):
            # INTENTIONAL (PF-18 batch 7): ocr_file and
            # screenshot_to_text_note were unbound in batches 4–6
            # and are now files-bound (they read existing files).
            self.assertIsNone(self.pg.permission_for_tool(tool))
        # screen_brightness_auto is NOT screen-bound, but it IS bound
        # — to camera (batch 2: it opens the webcam). It must map to
        # camera, never to screen.
        self.assertEqual(
            self.pg.permission_for_tool("screen_brightness_auto"),
            "camera")
        # INTENTIONAL (PF-18 batch 5): browser is now bound, so the
        # other two ids stay unbound — no behaviour change.
        # INTENTIONAL (PF-18 batch 7): files is now bound, so the
        # only remaining unbound id is home_devices.
        for pid in ("home_devices",):
            self.assertEqual(self.pg.PERMISSION_TOOL_MAP[pid],
                             frozenset())

    def test_guard_refuses_save_when_denied(self):
        # PF-18 batch 4: a durable screen denial refuses the save
        # path at the capture guard too, before any ask — even with
        # an approving confirm handler registered, it is never
        # called. The plain look has no guard call (Tier-0 observe)
        # and its enforcement is the funnel choke point.
        from core import capture_guard as cg
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("screen", "denied",
                                         via="settings"))
        calls = []
        confirm_gate.register_handler(
            lambda n, s: calls.append(n) or True)
        try:
            allowed, msg = cg.ensure_capture_allowed("screenshot_save")
        finally:
            confirm_gate.clear_handler()
        self.assertFalse(allowed)
        self.assertIn("Screen", msg)
        self.assertIn("Settings", msg)
        self.assertEqual(calls, [])

    def test_guard_asks_when_granted(self):
        # PF-18 batch 4: a granted screen passes the durable
        # pre-check and the normal ask path runs (not refused on
        # durable grounds), mirroring the mic symmetry test.
        from core import capture_guard as cg
        self.assertTrue(
            self.pg.set_permission_state("screen", "granted",
                                         via="settings"))
        orig_block = cg.foreground_capture_block
        orig_policy = cg.capture_policy
        orig_ask = cg._ask_human
        cg.foreground_capture_block = lambda: None
        cg.capture_policy = lambda: "ask-once"
        calls = []
        cg._ask_human = lambda kind: calls.append(kind) or True
        try:
            allowed, msg = cg.ensure_capture_allowed("screenshot_save")
        finally:
            cg.foreground_capture_block = orig_block
            cg.capture_policy = orig_policy
            cg._ask_human = orig_ask
            cg.revoke_capture_approval("screenshot_save")
        self.assertTrue(allowed)
        self.assertEqual(msg, "")
        self.assertEqual(calls, ["screenshot_save"])

    def test_screen_unknown_not_fail_closed(self):
        # PF-18 batch 4: the fail-closed boundary does NOT move —
        # FAIL_CLOSED_IDS stays {microphone, camera}. Screen on a
        # missing store reads unknown (asks via the normal confirm
        # path, D3), and on a CORRUPT store it still reads unknown
        # while camera on that same corrupt store reads denied.
        self.assertEqual(self.pg.get_state("screen"), "unknown")
        self._store.write_bytes(b"{not json")
        self.assertEqual(self.pg.get_state("screen"), "unknown")
        self.assertEqual(self.pg.get_state("camera"), "denied")

    def test_screen_flip_clears_save_approval_and_switch(self):
        # PF-18 batch 4: denying screen clears the screenshot_save
        # session approval AND turns the screen-watch session switch
        # off (design §5.3), while the microphone approval survives
        # (one-kind semantics).
        from core import capture_guard as cg
        self.assertTrue(
            self.pg.set_permission_state("screen", "granted",
                                         via="settings"))
        self.assertTrue(
            self.pg.set_permission_state("microphone", "granted",
                                         via="settings"))
        orig_block = cg.foreground_capture_block
        orig_policy = cg.capture_policy
        orig_ask = cg._ask_human
        cg.foreground_capture_block = lambda: None
        cg.capture_policy = lambda: "ask-once"
        cg._ask_human = lambda kind: True
        try:
            allowed_save, _ = cg.ensure_capture_allowed(
                "screenshot_save")
            allowed_mic, _ = cg.ensure_capture_allowed("microphone")
        finally:
            cg.foreground_capture_block = orig_block
            cg.capture_policy = orig_policy
            cg._ask_human = orig_ask
        self.assertTrue(allowed_save)
        self.assertTrue(allowed_mic)
        self.assertTrue(cg.is_capture_approved("screenshot_save"))
        self.assertTrue(cg.is_capture_approved("microphone"))
        self.assertTrue(
            self._sw.set_watch_enabled(True, source="test"))
        self.assertTrue(self._sw.watch_enabled())
        self.assertTrue(
            self.pg.set_permission_state("screen", "denied",
                                         via="settings"))
        self.assertFalse(cg.is_capture_approved("screenshot_save"))
        self.assertTrue(cg.is_capture_approved("microphone"))
        self.assertFalse(self._sw.watch_enabled())
        # Leave no approval behind for other suites.
        cg.revoke_capture_approval("microphone")


class ScreenFunnelTests(unittest.TestCase):
    """PF-18 batch 4: screen refusal through the real funnel."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _deny_screen(self):
        self.assertTrue(
            self.pg.set_permission_state("screen", "denied",
                                         via="settings"))

    def test_screen_attempt_after_revoke_refused(self):
        # PF-18 criterion clause (2), lab leg, screen: denied
        # screen, real funnel dispatch, refused naming the
        # permission — via take_screenshot (look path), whose
        # handler makes no guard call on the look path, so this
        # refusal can only come from the funnel choke point.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_screen()
            result = self._tools.execute("take_screenshot", {})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "screen")
        self.assertIn("Screen", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        # Ordering: the confirm handler was never even asked.
        self.assertEqual(calls, [])

    def test_screenshot_region_funnel_refused(self):
        # Second screen tool, funnel-only path: screenshot_region's
        # handler makes no guard call, so a denied-screen refusal
        # here can only come from the choke point.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_screen()
            result = self._tools.execute(
                "screenshot_region",
                {"x": 0, "y": 0, "width": 10, "height": 10})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "screen")
        self.assertEqual(calls, [])

    def test_screen_flip_back_no_longer_refused(self):
        # Flip back through the real funnel: the stage passes and
        # dispatch runs (handler stubbed — the real screenshot
        # handler would capture the screen in the lab).
        self._deny_screen()
        refused = self._tools.execute("take_screenshot", {})
        self.assertEqual(refused.get("kind"), "refused")
        self.assertTrue(
            self.pg.set_permission_state("screen", "granted",
                                         via="settings"))
        entry = self._tools._tools["take_screenshot"]
        orig = entry["handler"]
        entry["handler"] = lambda **kw: "stub-ok"
        try:
            result = self._tools.execute("take_screenshot", {})
        finally:
            entry["handler"] = orig
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "screen"))

    def test_unknown_screen_reaches_dispatch(self):
        # D3: an EXPLICITLY unknown screen passes the choke point —
        # the call is NOT refused, it reaches dispatch (handler
        # stubbed so the lab never captures the screen), and the
        # stored state stays unknown (no durable grant was written).
        # (No file at all reads denied for screen — screen is not
        # in FAIL_CLOSED_IDS — so unknown here must be written
        # first.)
        self.assertTrue(
            self.pg.set_permission_state("screen", "unknown",
                                         via="settings"))
        entry = self._tools._tools["take_screenshot"]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        try:
            result = self._tools.execute("take_screenshot", {})
        finally:
            entry["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "screen"))
        self.assertEqual(self.pg.get_state("screen"), "unknown")


class BrowserBindingTests(unittest.TestCase):
    """PF-18 batch 5: browser tool map + fail-closed boundary + teardown."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_browser_tool_map(self):
        # INTENTIONAL (PF-18 batch 5): the bound set is pinned —
        # everything that drives a browser. Deliberately NOT bound:
        # site_permissions_review (a browser-SITE permissions
        # report, read-only reporting, not driving) and web_search
        # (API-first: DuckDuckGo instant-answer API / Google News
        # RSS; a browser tab opens only as a failure fallback —
        # disclosed residual, see the batch-5 report); download_file
        # (HTTP fetch, no browser window); screen/camera/mic tools
        # belong to their own ids.
        for tool in ("browser_control", "browser_read",
                     "browser_click", "browser_type",
                     "browser_select", "browser_press", "open_url"):
            self.assertEqual(self.pg.permission_for_tool(tool),
                             "browser")
        # Cross-id neighbours map to their own ids, never browser.
        self.assertEqual(self.pg.permission_for_tool("take_screenshot"),
                         "screen")
        self.assertEqual(self.pg.permission_for_tool("webcam_snapshot"),
                         "camera")
        self.assertEqual(self.pg.permission_for_tool("mic_check"),
                         "microphone")
        for tool in ("site_permissions_review", "web_search"):
            # INTENTIONAL (PF-18 batch 7): download_file was
            # unbound in batches 5–6 and is now files-bound (it
            # materialises external content as a local file) —
            # the files map pin covers it; it no longer belongs
            # here.
            self.assertIsNone(self.pg.permission_for_tool(tool))
        # INTENTIONAL (PF-18 batch 7): files is now bound, so the
        # only remaining unbound id is home_devices.
        for pid in ("home_devices",):
            self.assertEqual(self.pg.PERMISSION_TOOL_MAP[pid],
                             frozenset())

    def test_browser_not_fail_closed(self):
        # INTENTIONAL (PF-18 batch 5): the fail-closed boundary is
        # NOT moved — FAIL_CLOSED_IDS stays exactly {microphone,
        # camera}. Browser on a missing store reads unknown, and on
        # a CORRUPT store it still reads unknown while camera on
        # that same corrupt store reads denied.
        self.assertEqual(self.pg.get_state("browser"), "unknown")
        self._store.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.pg.get_state("browser"), "unknown")
        self.assertEqual(self.pg.get_state("camera"), "denied")

    def test_browser_flip_closes_session_when_connected(self):
        # INTENTIONAL (PF-18 batch 5): a browser denial closes
        # VYUK's browser session when one is connected — the
        # module-level BrowserWorker is the persistent session,
        # and _impl_close is attach-only (v5.70: it never touches
        # the user's Chrome). Lab has no Playwright session, so
        # the connected case is proven with a monkeypatched
        # module: the flip calls through module attributes, so
        # patching them observes exactly what the flip does.
        from core.desktop import browser_session as bs
        orig_connected = bs._connected
        orig_close = bs._impl_close
        calls = []
        try:
            bs._connected = lambda: True
            bs._impl_close = lambda: calls.append(1) or "closed"
            self.assertTrue(
                self.pg.set_permission_state("browser", "denied",
                                             via="settings"))
            self.assertEqual(calls, [1])
        finally:
            bs._connected = orig_connected
            bs._impl_close = orig_close

    def test_browser_flip_skips_close_when_disconnected(self):
        # INTENTIONAL (PF-18 batch 5): with no session connected,
        # the flip closes nothing — and a granted flip never
        # closes either.
        from core.desktop import browser_session as bs
        orig_connected = bs._connected
        orig_close = bs._impl_close
        calls = []
        try:
            bs._connected = lambda: False
            bs._impl_close = lambda: calls.append(1) or "closed"
            self.assertTrue(
                self.pg.set_permission_state("browser", "denied",
                                             via="settings"))
            self.assertEqual(calls, [])
            self.assertTrue(
                self.pg.set_permission_state("browser", "granted",
                                             via="settings"))
            self.assertEqual(calls, [])
        finally:
            bs._connected = orig_connected
            bs._impl_close = orig_close

    def test_browser_flip_close_failure_never_breaks_flip(self):
        # INTENTIONAL (PF-18 batch 5): a failing close is logged
        # and never fails the flip, because the gate reads the
        # store — same pattern as the mic/camera/screen teardowns.
        from core.desktop import browser_session as bs
        orig_connected = bs._connected
        orig_close = bs._impl_close
        try:
            bs._connected = lambda: True

            def _boom():
                raise RuntimeError("boom")
            bs._impl_close = _boom
            self.assertTrue(
                self.pg.set_permission_state("browser", "denied",
                                             via="settings"))
            self.assertEqual(self.pg.get_state("browser"), "denied")
        finally:
            bs._connected = orig_connected
            bs._impl_close = orig_close


class BrowserFunnelTests(unittest.TestCase):
    """PF-18 batch 5: browser refusal through the real funnel."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _deny_browser(self):
        self.assertTrue(
            self.pg.set_permission_state("browser", "denied",
                                         via="settings"))

    def test_browser_attempt_after_revoke_refused(self):
        # PF-18 criterion clause (2), lab leg, browser: denied
        # browser, real funnel dispatch, refused naming the
        # permission — via browser_control, whose handler drives
        # the browser session, so this refusal can only come
        # from the funnel choke point.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_browser()
            result = self._tools.execute("browser_control", {})
        finally:
            confirm_gate.clear_handler()
        # The funnel's structured-result contract normalises a
        # refusal kind to status "refused" (see _audited).
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "browser")
        self.assertIn("Browser", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        # Ordering: the confirm handler was never even asked.
        self.assertEqual(calls, [])

    def test_browser_type_shortcut_defeated(self):
        # PF-18 batch 5, design §5.3 distinctive part: a denied
        # browser defeats a STORED browser_type always-allow.
        # The shortcut lives in request_confirmation (the confirm
        # stage), which the permission stage precedes — so the
        # proof is: arm the shortcut (predicate True), deny
        # browser, and the funnel still refuses before any ask.
        # The result kind is the discriminator: a shortcut that
        # applied would dispatch (stub result), not refuse.
        from core import confirm_gate
        from core.confirm_gate_allowlist import (
            allow_kind, revoke_kind, _allowlist_shortcut_applies)
        self.assertTrue(allow_kind("browser_type"))
        try:
            self.assertTrue(_allowlist_shortcut_applies("browser_type"))
            calls = []
            confirm_gate.register_handler(
                lambda n, s: calls.append(n) or True)
            entry = self._tools._tools["browser_type"]
            orig = entry["handler"]
            ran = []
            entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
            try:
                self._deny_browser()
                result = self._tools.execute(
                    "browser_type",
                    {"target": "search", "text": "hello"})
            finally:
                entry["handler"] = orig
                confirm_gate.clear_handler()
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(result.get("permission_id"), "browser")
            self.assertEqual(calls, [])
            self.assertEqual(ran, [])
        finally:
            revoke_kind("browser_type")

    def test_browser_type_shortcut_control(self):
        # PF-18 batch 5 control for the defeat test above: with
        # browser GRANTED and browser_type allowlisted, the same
        # call dispatches with no ask — proving the shortcut
        # mechanism is real in this harness, so the defeat test
        # is not vacuous. (The direct predicate assertion in the
        # defeat test is the airtight arm-proof; this control
        # proves the granted path still flows.)
        from core import confirm_gate
        from core.confirm_gate_allowlist import allow_kind, revoke_kind
        self.assertTrue(
            self.pg.set_permission_state("browser", "granted",
                                         via="settings"))
        self.assertTrue(allow_kind("browser_type"))
        try:
            calls = []
            confirm_gate.register_handler(
                lambda n, s: calls.append(n) or True)
            entry = self._tools._tools["browser_type"]
            orig = entry["handler"]
            ran = []
            entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
            try:
                result = self._tools.execute(
                    "browser_type",
                    {"target": "search", "text": "hello"})
            finally:
                entry["handler"] = orig
                confirm_gate.clear_handler()
            self.assertEqual(ran, [1])
            self.assertEqual(calls, [])
            self.assertNotEqual(
                (result.get("kind"), result.get("permission_id")),
                ("refused", "browser"))
        finally:
            revoke_kind("browser_type")

    def test_browser_flip_back(self):
        # Flip back through the real funnel: the stage passes and
        # dispatch runs (handler stubbed — the real browser
        # handler would drive a browser in the lab). browser_control
        # is confirm-gated and requires an action, so the granted
        # leg registers an approving handler and passes a valid
        # action — otherwise the test would pass vacuously on a
        # confirm decline instead of proving dispatch runs.
        from core import confirm_gate
        self._deny_browser()
        refused = self._tools.execute("browser_control", {})
        self.assertEqual(refused.get("kind"), "refused")
        self.assertTrue(
            self.pg.set_permission_state("browser", "granted",
                                         via="settings"))
        entry = self._tools._tools["browser_control"]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        confirm_gate.register_handler(lambda n, s: True)
        try:
            result = self._tools.execute("browser_control",
                                         {"action": "status"})
        finally:
            entry["handler"] = orig
            confirm_gate.clear_handler()
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "browser"))

    def test_browser_unknown_reaches_dispatch(self):
        # D3: an EXPLICITLY unknown browser passes the choke point
        # — the call is NOT refused, it reaches dispatch (handler
        # stubbed so the lab never drives a browser), and the
        # stored state stays unknown (no durable grant was
        # written). (No file at all reads denied for browser — the
        # D2 carve-out covers mic/camera only — so unknown here
        # must be written first.)
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("browser", "unknown",
                                         via="settings"))
        entry = self._tools._tools["browser_control"]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        confirm_gate.register_handler(lambda n, s: True)
        try:
            result = self._tools.execute("browser_control",
                                         {"action": "status"})
        finally:
            entry["handler"] = orig
            confirm_gate.clear_handler()
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "browser"))
        self.assertEqual(self.pg.get_state("browser"), "unknown")


class WebSearchFallbackGateTests(unittest.TestCase):
    """PF-18 batch 6: the web_search browser-tab fallback is gated
    on the durable browser grant. web_search itself stays unbound
    (API-first tool); only its failure fallback consults the grant.
    """

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = (
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    @staticmethod
    def _opener():
        from core.funnel.tools_handlers_command_url import (
            ToolHandlersCommandUrlMixin)
        return ToolHandlersCommandUrlMixin._open_search_browser

    def test_denied_browser_skips_fallback_open(self):
        # INTENTIONAL (PF-18 batch 6): denied browser skips the tab
        # open entirely — the opener is never called, so the caller
        # reports the honest API-failure result.
        self.assertTrue(
            self.pg.set_permission_state("browser", "denied",
                                         via="settings"))
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            self.assertFalse(self._opener()("q"))
        self.assertEqual(calls, [])

    def test_granted_browser_opens_fallback_as_before(self):
        # INTENTIONAL (PF-18 batch 6): granted browser opens the
        # fallback exactly as before — same DuckDuckGo URL shape.
        self.assertTrue(
            self.pg.set_permission_state("browser", "granted",
                                         via="settings"))
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            self.assertTrue(self._opener()("hello+world"))
        self.assertEqual(
            calls, ["https://duckduckgo.com/?q=hello+world"])

    def test_unknown_browser_opens_fallback_as_before(self):
        # INTENTIONAL (PF-18 batch 6): unknown does NOT refuse the
        # fallback — it is not a funnel tool call, so it never asks,
        # and unknown allows it as today.
        self.assertTrue(
            self.pg.set_permission_state("browser", "unknown",
                                         via="settings"))
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            self.assertTrue(self._opener()("q"))
        self.assertEqual(len(calls), 1)

    def test_consult_failure_fails_closed(self):
        # INTENTIONAL (PF-18 batch 6): a consult that RAISES fails
        # closed for the fallback only — no tab is opened.
        orig = self.pg.get_state

        def _boom(pid):
            raise RuntimeError("boom")

        self.pg.get_state = _boom
        try:
            calls = []
            with mock.patch("webbrowser.open",
                            lambda url: calls.append(url) or True):
                self.assertFalse(self._opener()("q"))
            self.assertEqual(calls, [])
        finally:
            self.pg.get_state = orig

    def test_corrupt_store_does_not_gate_fallback(self):
        # INTENTIONAL (PF-18 batch 6): a corrupt store file is a
        # DEFINED unknown read for browser (batch-5 boundary pin),
        # not a consult failure — so the fallback stays allowed.
        # Only a consult that raises fails closed (test above).
        self._store.write_text("{not json")
        self.assertEqual(self.pg.get_state("browser"), "unknown")
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            self.assertTrue(self._opener()("q"))
        self.assertEqual(len(calls), 1)

    def test_denied_browser_api_failure_returns_honest_result(self):
        # INTENTIONAL (PF-18 batch 6): end to end through
        # tools.execute("web_search") with a forced API failure —
        # denied browser yields the plain failure string, never a
        # claim that a browser was opened.
        self.assertTrue(
            self.pg.set_permission_state("browser", "denied",
                                         via="settings"))
        # GAPS-23 (F-V7): through the funnel — the handler's own
        # denied fallback is what this pins (the funnel passes a
        # denied-browser read through to it).
        from core.funnel.tools_registry import tools
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            with mock.patch("urllib.request.urlopen",
                            side_effect=RuntimeError("net down")):
                result = tools.execute(
                    "web_search", {"query": "test query"})["result"]
        self.assertEqual(calls, [])
        self.assertIn("Web search failed", result)
        self.assertNotIn("opened your browser", result)

    def test_granted_browser_api_failure_opens_fallback(self):
        # INTENTIONAL (PF-18 batch 6): the granted-path mirror —
        # forced API failure opens the fallback and says so.
        self.assertTrue(
            self.pg.set_permission_state("browser", "granted",
                                         via="settings"))
        # GAPS-23 (F-V7): through the funnel — the handler's own
        # granted fallback is what this pins.
        from core.funnel.tools_registry import tools
        calls = []
        with mock.patch("webbrowser.open",
                        lambda url: calls.append(url) or True):
            with mock.patch("urllib.request.urlopen",
                            side_effect=RuntimeError("net down")):
                result = tools.execute(
                    "web_search", {"query": "test query"})["result"]
        self.assertEqual(
            calls, ["https://duckduckgo.com/?q=test+query"])
        self.assertIn("I opened your browser", result)


class FilesBindingTests(unittest.TestCase):
    """PF-18 batch 7: files tool map + fail-closed boundary."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_files_tool_map(self):
        # INTENTIONAL (PF-18 batch 7): the bound set is pinned —
        # purpose is operating on user files (read, write, manage,
        # inspect, transform existing files). Tools where a file is
        # merely the delivery vehicle for another domain's output
        # are deliberately NOT here (see the map comment in
        # permission_grants.py for the full exclusion list).
        for tool in (
                "read_file", "read_pdf_text", "ocr_file", "tail_file",
                "csv_preview", "csv_query", "csv_column_stats",
                "compare_files", "image_compare", "file_hash",
                "sniff_file", "pdf_info", "image_info",
                "color_from_image", "qr_decode_image",
                "screenshot_to_text_note", "json_query", "json_diff",
                "extract_from_text", "search_file_contents",
                "folder_diff", "find_duplicates",
                "duplicate_photo_finder", "acronym_finder",
                "case_convert", "filler_word_count",
                "passive_voice_check", "reading_time",
                "sentence_stats", "text_stats", "word_frequency",
                "file_control", "open_path", "find_files",
                "largest_files", "folder_size", "folder_outline",
                "folder_size_map", "file_age_report",
                "folder_watch_cook", "batch_rename", "print_file",
                "copy_file_to_clipboard", "shred_file",
                "clean_temp_files", "recent_files",
                "file_dialog_action", "drag_file_onto", "env_backup",
                "continuous_delete_data", "write_file",
                "replace_in_file", "download_file",
                "download_email_attachment", "create_shortcut",
                "zip_control", "sync_folder", "convert_image",
                "resize_image", "compress_image", "crop_image",
                "rotate_image", "adjust_image", "filter_image",
                "watermark_image", "add_border", "make_collage",
                "kaleidoscope_image", "duotone_image",
                "pixelate_image", "glitch_image",
                "strip_image_metadata", "merge_pdfs", "rotate_pdf",
                "split_pdf", "pdf_extract_images",
                "pdf_merge_bookmarks", "pdf_split_by_text",
                "make_sprite_sheet", "batch_watermark", "audio_trim",
                "audio_speed_change", "audio_fade", "silence_trim",
                "photo_face_blur", "creative_mark_image",
                "display_control", "set_wallpaper", "wallpaper_apply",
                "image_contact_sheet",
                # INTENTIONAL (PF-18 batch 8): writer disposition —
                # generative writers' output paths, record / report
                # writers' caller-chosen output paths, the rights
                # ledger tools' ledger paths, and the study
                # workspace tools' workspace folders are all files
                # scope (denied files refusing a writer that would
                # create a user-visible file was a hole, same class
                # as the batch-6 web_search fallback leak).
                "create_art", "generate_melody", "generate_beat",
                "access_record_preference", "antic_cards_report",
                "comms_record_draft", "continuous_record_choice",
                "feel_report", "game_record_gaming",
                "hardware_record_plan", "improvement_report",
                "iot_record_device", "learnlast_report",
                "multiverse_compile_report", "net_record_network",
                "phase5_report", "pilot_record_pilot",
                "release_checklist_report", "sim_predict_report",
                "well_record_plan", "creative_subtitle_export",
                "vcard_export", "study_export_deck",
                "study_session", "creative_rights_add",
                "creative_rights_check", "study_exam_prep",
                "study_plan", "study_progress",
                # Voice-control batch (2026-11-16, C3): the three
                # file-writing tools were bound to Files (the scope
                # map already had them) — files 119 -> 122.
                "audio_to_midi", "organise_photos",
                "sync_folders_two_way"):
            self.assertEqual(self.pg.permission_for_tool(tool), "files")
        # Deliberately not bound (batch 8 dispositions, each with
        # evidence in the batch-8 writer tests): text generators
        # and text transforms with no file output, text-in
        # parsers, internal named stores, internal bookkeeping,
        # execution surfaces, transient benchmarks, query-only
        # tools, UI-path actions, command execution, registry
        # paths. NOTE (PF-18 batch 8 transition): create_art,
        # generate_melody and study_export_deck were in this
        # unbound list through batch 7 and moved to the bound list
        # above — the batch-7 "delivery vehicle" exclusion for
        # writers is superseded for user-visible outputs.
        for tool in ("generate_palette", "generate_password",
                     "generate_uuid", "creative_subtitle_edit",
                     "creative_timeline", "vcard_import",
                     "create_calendar_event", "create_plan",
                     "create_restore_point", "study_practice",
                     "study_review", "focus_timer_report",
                     "presence_report", "startup_time_report",
                     "teach_record_sequence", "sandbox_run",
                     "extension_register", "journal_action",
                     "disk_speed_test", "wallpaper_state",
                     "menu_action", "context_menu_action",
                     "system_surface_action", "run_command",
                     "registry_read", "save_snippet"):
            self.assertIsNone(self.pg.permission_for_tool(tool))
        # Cross-id neighbours map to their own ids, never files.
        self.assertEqual(self.pg.permission_for_tool("record_audio"),
                         "microphone")
        self.assertEqual(self.pg.permission_for_tool("webcam_snapshot"),
                         "camera")
        self.assertEqual(self.pg.permission_for_tool("take_screenshot"),
                         "screen")
        self.assertEqual(self.pg.permission_for_tool("browser_control"),
                         "browser")
        # Only home_devices stays unbound.
        self.assertEqual(self.pg.PERMISSION_TOOL_MAP["home_devices"],
                         frozenset())

    def test_files_not_fail_closed(self):
        # INTENTIONAL (PF-18 batch 7): the fail-closed boundary is
        # NOT moved — FAIL_CLOSED_IDS stays exactly {microphone,
        # camera}. Files on a missing store reads unknown, and on
        # a CORRUPT store it still reads unknown while camera on
        # that same corrupt store reads denied.
        self.assertEqual(self.pg.get_state("files"), "unknown")
        self._store.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.pg.get_state("files"), "unknown")
        self.assertEqual(self.pg.get_state("camera"), "denied")


class FolderListTests(unittest.TestCase):
    """PF-18 batch 7: D9 folder allowlist storage + API."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        self._folder_file = (pathlib.Path(self._tmp.name)
                             / "permission_folders.json")

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def _defaults(self):
        home = pathlib.Path.home()
        return [
            self.pg._normalise_path_for_scope(str(home / "Downloads")),
            self.pg._normalise_path_for_scope(str(home / "Documents")),
        ]

    def test_defaults_are_downloads_and_documents(self):
        # No folder file yet → the evidence-based defaults only
        # (download_file + download_email_attachment default to
        # ~/Downloads; env_backup defaults to ~/Documents). Nothing
        # else is guessed.
        self.assertEqual(self.pg.list_folders(), self._defaults())

    def test_add_remove_settings_only(self):
        # via != settings writes nothing (no file created).
        self.assertFalse(self.pg.add_folder("/tmp/xyz", via="voice"))
        self.assertFalse(self.pg.add_folder("/tmp/xyz", via=""))
        self.assertFalse(self._folder_file.exists())
        self.assertFalse(self.pg.remove_folder("/tmp/xyz", via="voice"))
        # via settings works + is idempotent (no duplicates).
        self.assertTrue(self.pg.add_folder("/tmp/xyz", via="settings"))
        norm = self.pg._normalise_path_for_scope("/tmp/xyz")
        self.assertIn(norm, self.pg.list_folders())
        self.assertTrue(self.pg.add_folder("/tmp/xyz/", via="settings"))
        self.assertEqual(self.pg.list_folders().count(norm), 1)
        self.assertTrue(self.pg.remove_folder("/tmp/xyz", via="settings"))
        self.assertNotIn(norm, self.pg.list_folders())
        self.assertTrue(self.pg.remove_folder("/tmp/xyz", via="settings"))

    def test_explicit_empty_list_sticks(self):
        # Removing both defaults stores an explicit [] — it is NOT
        # collapsed back to defaults (only a missing/corrupt file
        # reads as defaults).
        home = pathlib.Path.home()
        self.assertTrue(self.pg.remove_folder(str(home / "Downloads"),
                                              via="settings"))
        self.assertTrue(self.pg.remove_folder(str(home / "Documents"),
                                              via="settings"))
        self.assertEqual(self.pg.list_folders(), [])

    def test_normalisation(self):
        # ~, trailing slash, .. segments, relative → CWD.
        home = pathlib.Path.home()
        tmp = pathlib.Path(self._tmp.name)
        self.assertTrue(self.pg.add_folder("~/xyz-allowlist-test",
                                           via="settings"))
        self.assertIn(
            self.pg._normalise_path_for_scope(
                str(home / "xyz-allowlist-test")),
            self.pg.list_folders())
        self.assertTrue(self.pg.add_folder(
            str(tmp / ".." / tmp.name / "sub") + "/", via="settings"))
        self.assertIn(
            self.pg._normalise_path_for_scope(str(tmp / "sub")),
            self.pg.list_folders())
        self.assertTrue(self.pg.add_folder("rel-allowlist-test",
                                           via="settings"))
        self.assertIn(
            self.pg._normalise_path_for_scope(
                str(pathlib.Path.cwd() / "rel-allowlist-test")),
            self.pg.list_folders())
        # Empty / unjudgeable input is rejected, never stored.
        self.assertFalse(self.pg.add_folder("", via="settings"))
        self.assertFalse(self.pg.add_folder("   ", via="settings"))

    def test_corrupt_folder_file_reads_defaults(self):
        self._folder_file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.pg.list_folders(), self._defaults())

    def test_folder_list_independent_of_grants_file(self):
        # A corrupt GRANTS file does not affect the folder list
        # (separate durable file, separate lifecycle).
        self._store.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.pg.list_folders(), self._defaults())


class _FilesFunnelBase(unittest.TestCase):
    """Shared funnel harness for the files + scope tests."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def _deny_files(self):
        self.assertTrue(
            self.pg.set_permission_state("files", "denied",
                                         via="settings"))

    def _grant_files(self):
        self.assertTrue(
            self.pg.set_permission_state("files", "granted",
                                         via="settings"))

    def _unknown_files(self):
        self.assertTrue(
            self.pg.set_permission_state("files", "unknown",
                                         via="settings"))

    def _allow_tmp(self):
        self.assertTrue(
            self.pg.add_folder(self._tmp.name, via="settings"))

    def _in_path(self):
        return str(pathlib.Path(self._tmp.name) / "notes.txt")

    def _out_path(self):
        # Sibling of the temp dir: never added, never a default.
        return str(pathlib.Path(self._tmp.name).parent
                   / "pf18-b7-not-allowed" / "notes.txt")

    def _stub(self, name):
        entry = self._tools._tools[name]
        orig = entry["handler"]
        ran = []
        entry["handler"] = lambda **kw: ran.append(1) or "stub-ok"
        return orig, ran


class FilesFunnelTests(_FilesFunnelBase):
    """PF-18 batch 7: files funnel refusal (criterion clause 2)."""

    def test_attempt_after_revoke_refused(self):
        # PF-18 criterion clause (2), lab leg: denied files, real
        # funnel dispatch, refused naming the permission.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_files()
            result = self._tools.execute(
                "read_file", {"filepath": self._in_path()})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "files")
        self.assertIn("Files", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        # Ordering: the confirm handler was never even asked.
        self.assertEqual(calls, [])

    def test_flip_back_no_longer_permission_refused(self):
        # Flip back through the real funnel: the stage passes and
        # dispatch runs (handler stubbed — the real read handler
        # would read a file in the lab).
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._deny_files()
            refused = self._tools.execute(
                "read_file", {"filepath": self._in_path()})
            self.assertEqual(refused.get("kind"), "refused")
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("read_file")
            try:
                result = self._tools.execute(
                    "read_file", {"filepath": self._in_path()})
            finally:
                self._tools._tools["read_file"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(
                (result.get("kind"), result.get("permission_id")),
                ("refused", "files"))
        finally:
            confirm_gate.clear_handler()

    def test_unknown_files_reaches_dispatch(self):
        # D3: an EXPLICITLY unknown files grant passes the choke
        # point — the call is NOT refused, it reaches dispatch
        # (handler stubbed), and the stored state stays unknown
        # (no durable grant was written).
        self._unknown_files()
        self._allow_tmp()
        orig, ran = self._stub("read_file")
        try:
            result = self._tools.execute(
                "read_file", {"filepath": self._in_path()})
        finally:
            self._tools._tools["read_file"]["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "files"))
        self.assertEqual(self.pg.get_state("files"), "unknown")


class FolderScopeTests(_FilesFunnelBase):
    """PF-18 batch 7: D9 folder-scope stage, real dispatch + units."""

    def test_in_list_path_runs(self):
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("read_file")
            try:
                result = self._tools.execute(
                    "read_file", {"filepath": self._in_path()})
            finally:
                self._tools._tools["read_file"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
        finally:
            confirm_gate.clear_handler()

    def test_out_of_list_path_refused_before_ask(self):
        # Scope refuses BEFORE the confirm ask (standing boundary,
        # not something to ask about) — the confirm handler is
        # never called and the tool handler never runs.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("read_file")
            try:
                result = self._tools.execute(
                    "read_file", {"filepath": self._out_path()})
            finally:
                self._tools._tools["read_file"]["handler"] = orig
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(result.get("permission_id"), "files")
            self.assertIn("outside the folders",
                          result.get("message", ""))
            self.assertIn("Settings", result.get("message", ""))
            self.assertEqual(calls, [])
            self.assertEqual(ran, [])
        finally:
            confirm_gate.clear_handler()

    def test_unknown_out_of_list_reaches_dispatch(self):
        # Choice B pin (batch 7, D3-consistent): scope binds the
        # GRANTED state only, so an UNKNOWN grant + out-of-list
        # path passes scope and reaches dispatch — unknown goes
        # through the normal confirm path, and read_file is not
        # confirm-gated, so the stub runs; the folder list
        # constrains the STANDING grant, it does not override
        # explicit per-call consent (an allow-once confirms that
        # call including its path). State stays unknown.
        self._unknown_files()
        self._allow_tmp()
        orig, ran = self._stub("read_file")
        try:
            result = self._tools.execute(
                "read_file", {"filepath": self._out_path()})
        finally:
            self._tools._tools["read_file"]["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(result.get("kind"), "refused")
        from core import permission_grants as pg
        self.assertEqual(pg.get_state("files"), "unknown")

    def test_unknown_in_list_reaches_dispatch(self):
        # Unknown + in-scope: scope passes, the call dispatches
        # (read_file is not confirm-gated), state stays unknown.
        self._unknown_files()
        self._allow_tmp()
        orig, ran = self._stub("read_file")
        try:
            result = self._tools.execute(
                "read_file", {"filepath": self._in_path()})
        finally:
            self._tools._tools["read_file"]["handler"] = orig
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "files"))
        self.assertEqual(self.pg.get_state("files"), "unknown")

    def test_no_path_arg_passes_scope(self):
        # clean_temp_files / recent_files are bound but take no
        # path arg — scope has nothing to judge, so the call runs
        # (clean_temp_files is confirm-gated, hence the approving
        # handler).
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            orig, ran = self._stub("clean_temp_files")
            try:
                result = self._tools.execute("clean_temp_files", {})
            finally:
                self._tools._tools["clean_temp_files"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
        finally:
            confirm_gate.clear_handler()

    def test_file_control_both_sides(self):
        # file_control judges path AND destination: an out-of-list
        # destination refuses even with an in-list source; both
        # in-list runs.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("file_control")
            try:
                result = self._tools.execute("file_control", {
                    "action": "copy", "path": self._in_path(),
                    "destination": self._out_path()})
                self.assertEqual(result.get("kind"), "refused")
                self.assertEqual(result.get("permission_id"), "files")
                self.assertEqual(calls, [])
                self.assertEqual(ran, [])
                result = self._tools.execute("file_control", {
                    "action": "copy", "path": self._in_path(),
                    "destination": self._in_path() + ".copy"})
                self.assertEqual(ran, [1])
                self.assertNotEqual(result.get("kind"), "refused")
            finally:
                self._tools._tools["file_control"]["handler"] = orig
        finally:
            confirm_gate.clear_handler()

    def test_display_control_action_gate(self):
        # display_control judges "path" ONLY for set_wallpaper;
        # info ignores path, so an out-of-list path runs for info
        # and refuses for set_wallpaper.
        self._grant_files()
        self._allow_tmp()
        orig, ran = self._stub("display_control")
        try:
            result = self._tools.execute("display_control", {
                "action": "info", "path": self._out_path()})
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
            result = self._tools.execute("display_control", {
                "action": "set_wallpaper", "path": self._out_path()})
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(result.get("permission_id"), "files")
            self.assertEqual(ran, [1])
        finally:
            self._tools._tools["display_control"]["handler"] = orig

    def test_wallpaper_entries_judged(self):
        # wallpaper_apply judges art_dir plus each entry's art_path;
        # an out-of-list art_path refuses before the ask (the tool
        # is confirm-gated, hence the approving handler).
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("wallpaper_apply")
            try:
                result = self._tools.execute("wallpaper_apply", {
                    "entries": [{"state": "focus",
                                 "art_path": self._out_path()}]})
                self.assertEqual(result.get("kind"), "refused")
                self.assertEqual(result.get("permission_id"), "files")
                self.assertEqual(calls, [])
                self.assertEqual(ran, [])
            finally:
                self._tools._tools["wallpaper_apply"]["handler"] = orig
        finally:
            confirm_gate.clear_handler()

    def test_scope_hit_units(self):
        # Direct _folder_scope_hit rules: non-files tool passes;
        # omitted optional output passes; multi-path judges every
        # element; json_query skips raw JSON text but judges paths.
        # The hit gates on the GRANTED state, so the judging
        # assertions run under a granted files grant; the final
        # assertion pins Choice B at unit level: unknown passes
        # scope even for an out-of-list path.
        from core.funnel.tools_gate_paths import _folder_scope_hit
        self._grant_files()
        self._allow_tmp()
        self.assertIsNone(_folder_scope_hit("mic_check", {}))
        self.assertIsNone(_folder_scope_hit(
            "convert_image", {"path": self._in_path()}))
        self.assertIsNone(_folder_scope_hit(
            "read_file", {"filepath": self._in_path()}))
        self.assertEqual(_folder_scope_hit(
            "read_file", {"filepath": self._out_path()}),
            self._out_path())
        self.assertEqual(_folder_scope_hit(
            "merge_pdfs",
            {"paths": [self._in_path(), self._out_path()],
             "output": self._in_path()}),
            self._out_path())
        self.assertIsNone(_folder_scope_hit(
            "json_query", {"source": '{"a": 1}', "query": "a"}))
        self.assertEqual(_folder_scope_hit(
            "json_query", {"source": self._out_path(), "query": "a"}),
            self._out_path())
        self.assertIsNone(_folder_scope_hit(
            "wallpaper_apply",
            {"entries": [{"state": "focus",
                          "art_path": self._in_path()}]}))
        self._unknown_files()
        self.assertIsNone(_folder_scope_hit(
            "read_file", {"filepath": self._out_path()}))


class WriterBindingTests(unittest.TestCase):
    """PF-18 batch 8: writer disposition + binding (map + scope)."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)

    def tearDown(self):
        self.pg.clear_path_override()
        self._tmp.cleanup()

    def test_writer_map_count_and_scope_closure(self):
        # INTENTIONAL (PF-18 batch 8): files grows 90 -> 119, and
        # the scope map covers exactly the files map — no bound
        # tool left unscoped, no orphan scope entry.
        # Voice-control batch (2026-11-16, C3): 119 -> 122 —
        # audio_to_midi, organise_photos and sync_folders_two_way
        # already had scope entries but were never bound to files,
        # so a denied Files grant let them write and the scope
        # stage never judged their paths. Bound now.
        from core.funnel.tools_gate_paths import _FOLDER_SCOPE_PATH_ARGS
        files = set(self.pg.PERMISSION_TOOL_MAP["files"])
        self.assertEqual(len(files), 122)
        self.assertEqual(set(_FOLDER_SCOPE_PATH_ARGS), files)
        # Fail-closed boundary NOT moved.
        self.assertEqual(set(self.pg.FAIL_CLOSED_IDS),
                         {"microphone", "camera"})
        self.assertEqual(self.pg.PERMISSION_TOOL_MAP["home_devices"],
                         frozenset())

    def test_writer_scope_args(self):
        # INTENTIONAL (PF-18 batch 8): per-tool scope arguments —
        # generative writers judge "output", record / report /
        # export writers judge "output_path", rights tools judge
        # "ledger_path", study workspace tools judge "workspace_dir".
        from core.funnel.tools_gate_paths import _FOLDER_SCOPE_PATH_ARGS
        for tool in ("create_art", "generate_melody", "generate_beat"):
            self.assertEqual(_FOLDER_SCOPE_PATH_ARGS[tool], ("output",))
        for tool in (
                "access_record_preference", "antic_cards_report",
                "comms_record_draft", "continuous_record_choice",
                "feel_report", "game_record_gaming",
                "hardware_record_plan", "improvement_report",
                "iot_record_device", "learnlast_report",
                "multiverse_compile_report", "net_record_network",
                "phase5_report", "pilot_record_pilot",
                "release_checklist_report", "sim_predict_report",
                "well_record_plan", "creative_subtitle_export",
                "vcard_export", "study_export_deck", "study_session"):
            self.assertEqual(_FOLDER_SCOPE_PATH_ARGS[tool],
                             ("output_path",))
        for tool in ("creative_rights_add", "creative_rights_check"):
            self.assertEqual(_FOLDER_SCOPE_PATH_ARGS[tool],
                             ("ledger_path",))
        for tool in ("study_exam_prep", "study_plan", "study_progress"):
            self.assertEqual(_FOLDER_SCOPE_PATH_ARGS[tool],
                             ("workspace_dir",))

    def test_left_unbound_with_evidence(self):
        # INTENTIONAL (PF-18 batch 8): each tool left unbound, with
        # its evidence — no blanket exclusion. A tool writes no
        # user-visible file, or its only file is VYUK-internal
        # state the permission system itself needs.
        reasons = {
            # Text generators: return values, no file output.
            "generate_palette": "returns palette values, no file",
            "generate_password": "returns a string; clipboard only",
            "generate_uuid": "returns UUID string(s), no file",
            # Text transforms / parsers: text in, text out.
            "creative_subtitle_edit": "edits subtitle text, no path",
            "creative_timeline": "returns an OTIO object, no file",
            "vcard_import": "parses vcard TEXT, not a path",
            # No user file: calendar API, internal plan store,
            # system restore point.
            "create_calendar_event": "calendar API, no file",
            "create_plan": "internal plan store, no path arg",
            "create_restore_point": "system restore point, no file",
            # Internal named stores / internal bookkeeping.
            "study_review": "internal schedule-store object, no path",
            "study_practice": "returns questions as text, no file",
            "teach_record_sequence": "internal named store, no path",
            "journal_action": "funnel receipt store; explicit path "
                               "is a lab/test override only",
            "extension_register": "harness registration + internal "
                                  "undo record; directory is a "
                                  "reference, not a file opened",
            # Execution / benchmark / query / live-state readers.
            "sandbox_run": "execution surface, own containment",
            "disk_speed_test": "transient scratch, deleted always",
            "wallpaper_state": "query-only, no write",
            "focus_timer_report": "reads live reminder list, no path",
            "presence_report": "reads in-memory events, no path",
            "startup_time_report": "reads Windows event log, no file",
        }
        for tool in reasons:
            self.assertIsNone(self.pg.permission_for_tool(tool),
                              tool)


class WriterFunnelTests(_FilesFunnelBase):
    """PF-18 batch 8: writers through the real funnel + scope."""

    def test_denied_writer_refused(self):
        # Denied files refuses a generative writer before any
        # confirm ask — the batch-8 hole, closed.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_files()
            result = self._tools.execute(
                "create_art", {"output": self._in_path()})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "files")
        self.assertIn("Files", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        self.assertEqual(calls, [])

    def test_granted_writer_in_list_runs(self):
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("create_art")
            try:
                result = self._tools.execute(
                    "create_art", {"output": self._in_path()})
            finally:
                self._tools._tools["create_art"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
        finally:
            confirm_gate.clear_handler()

    def test_granted_writer_out_of_list_refused(self):
        # Scope refuses a writer's out-of-list output BEFORE the
        # confirm ask, same as batch 7's read_file scope test.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("access_record_preference")
            try:
                result = self._tools.execute(
                    "access_record_preference",
                    {"preference_json": "{}",
                     "output_path": self._out_path()})
            finally:
                self._tools._tools["access_record_preference"]["handler"] = orig
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(result.get("permission_id"), "files")
            self.assertIn("outside the folders",
                          result.get("message", ""))
            self.assertIn("Settings", result.get("message", ""))
            self.assertEqual(calls, [])
            self.assertEqual(ran, [])
        finally:
            confirm_gate.clear_handler()

    def test_unknown_writer_out_of_list_reaches_dispatch(self):
        # Choice B holds for writers too: unknown passes scope to
        # the normal confirm path — an allow-once confirms that
        # call including its output path. (create_art IS
        # confirm-gated, unlike batch 7's read_file, so the test
        # registers the approving handler that the allow-once
        # represents; scope passing is what the stub run proves —
        # a scope refusal would refuse regardless of the handler.)
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._unknown_files()
            self._allow_tmp()
            orig, ran = self._stub("create_art")
            try:
                result = self._tools.execute(
                    "create_art", {"output": self._out_path()})
            finally:
                self._tools._tools["create_art"]["handler"] = orig
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "files"))
        self.assertEqual(self.pg.get_state("files"), "unknown")

    def test_workspace_dir_judged(self):
        # Study workspace tools judge the workspace folder itself:
        # in-list runs, out-of-list refuses before the ask.
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("study_plan")
            try:
                result = self._tools.execute(
                    "study_plan",
                    {"workspace_dir": self._tmp.name,
                     "goals_json": "[]", "topics_json": "[]"})
            finally:
                self._tools._tools["study_plan"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
            out_dir = str(pathlib.Path(self._tmp.name).parent
                          / "pf18-b8-not-allowed")
            orig, ran = self._stub("study_plan")
            try:
                result = self._tools.execute(
                    "study_plan",
                    {"workspace_dir": out_dir,
                     "goals_json": "[]", "topics_json": "[]"})
            finally:
                self._tools._tools["study_plan"]["handler"] = orig
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(result.get("permission_id"), "files")
            self.assertEqual(ran, [])
        finally:
            confirm_gate.clear_handler()

    def test_ledger_path_judged_and_omitted_passes(self):
        # A given ledger_path is judged; an omitted ledger_path
        # passes scope (nothing to judge) and lands in the
        # internal data/rights_ledger.db default.
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("creative_rights_add")
            try:
                result = self._tools.execute(
                    "creative_rights_add",
                    {"ledger_path": self._out_path(),
                     "source": "s", "licensor": "l",
                     "licence_name": "n"})
            finally:
                self._tools._tools["creative_rights_add"]["handler"] = orig
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(ran, [])
            orig, ran = self._stub("creative_rights_add")
            try:
                result = self._tools.execute(
                    "creative_rights_add",
                    {"source": "s", "licensor": "l",
                     "licence_name": "n"})
            finally:
                self._tools._tools["creative_rights_add"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
        finally:
            confirm_gate.clear_handler()

    def test_omitted_writer_output_passes_scope(self):
        # Nothing-to-judge rule (batch 7) for writers: an omitted
        # output passes scope and lands in the handler's fixed
        # default (~/Pictures/Nova for art), named in the result.
        # Disclosed, not hidden: the default is fixed and visible,
        # not a user-chosen arbitrary path.
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self._grant_files()
            self._allow_tmp()
            orig, ran = self._stub("create_art")
            try:
                result = self._tools.execute("create_art", {})
            finally:
                self._tools._tools["create_art"]["handler"] = orig
            self.assertEqual(ran, [1])
            self.assertNotEqual(result.get("kind"), "refused")
        finally:
            confirm_gate.clear_handler()


class HomeDevicesBindingTests(unittest.TestCase):
    """PF-18 batch 9: home_devices honest-empty hook (D10)."""

    def setUp(self):
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def test_map_pin(self):
        # INTENTIONAL (PF-18 batch 9): home_devices maps to an empty
        # set still — no device tool exists yet (D10 honest-empty) —
        # but the id is present in the map machinery and the hook
        # below is real plumbing, not a comment. Other maps pin
        # their batch-8 state so a silent drift fails loudly.
        self.assertIn("home_devices", self.pg.PERMISSION_TOOL_MAP)
        self.assertEqual(self.pg.PERMISSION_TOOL_MAP["home_devices"],
                         frozenset())
        self.assertEqual(len(self.pg.PERMISSION_TOOL_MAP["microphone"]), 7)
        self.assertEqual(len(self.pg.PERMISSION_TOOL_MAP["camera"]), 3)
        self.assertEqual(len(self.pg.PERMISSION_TOOL_MAP["screen"]), 7)
        self.assertEqual(len(self.pg.PERMISSION_TOOL_MAP["browser"]), 7)
        self.assertEqual(len(self.pg.PERMISSION_TOOL_MAP["files"]), 122)
        # INTENTIONAL (PF-18 batch 9): device-flavoured tools that
        # reach/control NOTHING stay unbound — binding them would
        # over-refuse pure text transforms and listings, the batch-6
        # web_search lesson. iot_* descriptors take caller-supplied
        # labels and return summaries (no connection, no scan, no
        # control); list_local_devices reads the ARP cache only
        # (explicitly not a scan); bluetooth_devices lists only
        # (cannot pair/toggle); list_audio_devices enumerates.
        for tool in ("iot_local_device", "iot_device_state",
                     "iot_network_check", "list_local_devices",
                     "bluetooth_devices", "list_audio_devices"):
            self.assertIsNone(self.pg.permission_for_tool(tool))
        self.assertTrue(callable(self.pg.check_home_device))

    def test_hook_refuses_when_denied(self):
        self.assertTrue(
            self.pg.set_permission_state("home_devices", "denied",
                                         via="settings"))
        self.assertEqual(self.pg.check_home_device(),
                         (False, "home_devices"))

    def test_hook_passes_when_granted(self):
        self.assertTrue(
            self.pg.set_permission_state("home_devices", "granted",
                                         via="settings"))
        self.assertEqual(self.pg.check_home_device(), (True, None))

    def test_hook_passes_when_unknown(self):
        # D3: unknown asks per call — the hook must not refuse it.
        self.assertTrue(
            self.pg.set_permission_state("home_devices", "unknown",
                                         via="settings"))
        self.assertEqual(self.pg.check_home_device(), (True, None))
        self.assertEqual(self.pg.get_state("home_devices"), "unknown")

    def test_hook_fails_closed_on_consult_failure(self):
        orig = self.pg.get_state
        try:
            self.pg.get_state = lambda pid: (_ for _ in ()).throw(
                RuntimeError("boom"))
            self.assertEqual(self.pg.check_home_device(),
                             (False, "home_devices"))
        finally:
            self.pg.get_state = orig

    def test_boundary_missing_store(self):
        # No store file at all: home_devices reads unknown (D3 asks),
        # never denied — FAIL_CLOSED_IDS stays exactly mic + camera.
        self.assertEqual(self.pg.get_state("home_devices"), "unknown")

    def test_boundary_corrupt_store(self):
        # INTENTIONAL (PF-18 batch 9): the fail-closed boundary pin
        # for the last id — corrupt store reads home_devices unknown
        # while camera on that SAME corrupt store reads denied.
        self._store.write_text("{not json")
        self.assertEqual(self.pg.get_state("home_devices"), "unknown")
        self.assertEqual(self.pg.get_state("camera"), "denied")


class HomeDevicesFunnelTests(unittest.TestCase):
    """Real funnel dispatch for a home_devices-bound tool.

    No product device tool exists (D10 honest-empty), so the funnel
    tests use a TEST-ONLY stub registration, explicitly labelled as
    such: the stub proves the generic choke point refuses a
    home_devices-bound tool, so a future real device tool needs no
    further funnel work. The stub is added and removed inside each
    test and never ships as a product tool.
    """

    _STUB = "_pf18_test_home_device_stub"

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod = self._tools_mod
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._guard.__exit__()
        self._tmp.cleanup()

    def _register_stub(self):
        # TEST-ONLY scaffolding (PF-18 batch 9, see class docstring).
        ran = []
        self._tools._tools[self._STUB] = {
            "name": self._STUB,
            "description": "TEST-ONLY stub: proves home_devices funnel plumbing.",
            "parameters": {"type": "object", "properties": {},
                           "required": [], "additionalProperties": False},
            "handler": lambda **kw: ran.append(1) or "stub-ok",
            "kind": None,
        }
        self.pg._TOOL_TO_PERMISSION[self._STUB] = "home_devices"
        return ran

    def _remove_stub(self):
        self._tools._tools.pop(self._STUB, None)
        self.pg._TOOL_TO_PERMISSION.pop(self._STUB, None)

    def _deny_home_devices(self):
        self.assertTrue(
            self.pg.set_permission_state("home_devices", "denied",
                                         via="settings"))

    def test_funnel_refuses_stub_when_denied(self):
        # PF-18 criterion clause (2), lab leg, for the last id:
        # denied home_devices + approving confirm handler → refused
        # naming Home devices, handler never called, never asked.
        from core import confirm_gate
        calls = []
        confirm_gate.register_handler(lambda n, s: calls.append(n) or True)
        try:
            self._deny_home_devices()
            ran = self._register_stub()
            try:
                result = self._tools.execute(self._STUB, {})
            finally:
                self._remove_stub()
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "home_devices")
        self.assertIn("Home devices", result.get("message", ""))
        self.assertIn("Settings", result.get("message", ""))
        self.assertIn("nothing was run", result.get("message", ""))
        self.assertEqual(calls, [])
        self.assertEqual(ran, [])

    def test_funnel_stub_runs_when_granted(self):
        from core import confirm_gate
        confirm_gate.register_handler(lambda n, s: True)
        try:
            self.assertTrue(
                self.pg.set_permission_state("home_devices", "granted",
                                             via="settings"))
            ran = self._register_stub()
            try:
                result = self._tools.execute(self._STUB, {})
            finally:
                self._remove_stub()
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(ran, [1])
        self.assertNotEqual(result.get("kind"), "refused")

    def test_unknown_stub_reaches_dispatch(self):
        # D3: an EXPLICITLY unknown home_devices passes the choke
        # point — the call is NOT refused, it reaches dispatch, and
        # the stored state stays unknown.
        self.assertTrue(
            self.pg.set_permission_state("home_devices", "unknown",
                                         via="settings"))
        ran = self._register_stub()
        try:
            result = self._tools.execute(self._STUB, {})
        finally:
            self._remove_stub()
        self.assertEqual(ran, [1])
        self.assertNotEqual(
            (result.get("kind"), result.get("permission_id")),
            ("refused", "home_devices"))
        self.assertEqual(self.pg.get_state("home_devices"), "unknown")


class ActivityRefusalTests(unittest.TestCase):
    """PF-18 widget batch: refusal lines in Activity name the
    permission + Settings direction (GUI/funnel/ledger/
    Activity agreement point)."""

    def setUp(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        from core import permission_grants as pg
        self.pg = pg
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._tools_mod = tools_mod
        self._tools = tools
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        from core import confirm_gate, injection
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        self.pg.clear_path_override()
        self.pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()

    def _activity_texts(self):
        from core import record_family as rf
        texts = []
        try:
            entries = rf.read_activity(limit=50)
        except Exception:  # noqa: BLE001 — no line is a failure, not a crash
            return texts
        for entry in entries:
            data = entry.get("data") if isinstance(entry, dict) else None
            if isinstance(data, dict) and data.get("text"):
                texts.append(str(data["text"]))
        return texts

    def test_permission_refusal_activity_line(self):
        # INTENTIONAL (PF-18 widget batch): the Activity line
        # for a permission refusal names the permission and
        # the Settings direction — it does not stay generic.
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("microphone", "denied",
                                         via="settings"))
        confirm_gate.register_handler(lambda n, s: True)
        try:
            result = self._tools.execute("mic_check", {})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("kind"), "refused")
        lines = self._activity_texts()
        self.assertTrue(
            any("Microphone" in line and "Settings" in line
                and "nothing was run" in line for line in lines),
            f"no named refusal line in Activity; lines={lines[:5]}")
        self.assertNotIn("Tool mic_check: refused", lines)

    def test_folder_scope_refusal_activity_line(self):
        # INTENTIONAL (PF-18 widget batch): a folder-scope
        # refusal line names the folders rule + Settings.
        from core import confirm_gate
        self.assertTrue(
            self.pg.set_permission_state("files", "granted",
                                         via="settings"))
        confirm_gate.register_handler(lambda n, s: True)
        try:
            result = self._tools.execute(
                "read_file", {"filepath": "/tmp/outside_scope_probe.txt"})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(result.get("permission_id"), "files")
        lines = self._activity_texts()
        self.assertTrue(
            any("folders you allow" in line
                and "Settings" in line for line in lines),
            f"no folder-scope line in Activity; lines={lines[:5]}")


if __name__ == "__main__":
    unittest.main()
