"""B3 platform permission probes: the Windows mic/camera
privacy state read from the ConsentStore, rendered on the
Permissions page next to the VYUK-side grant, plus the mic-arc
three-way diagnosis (denied by you / denied by Windows / no
usable device).

The probe itself is pure (fake winreg); the widget tests need
PySide6 (the .venv-qt interpreter) and skip otherwise,
following the programme's standing optional-dependency skips.
The grants store is pointed at a temp dir per test; data/ is
guarded by _DataGuard (consumers-batch pattern).
"""

import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import permission_grants as pg  # noqa: E402
from core import platform_permission_probe as probe  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QLabel
    from gui import pf_permissions as _pf
    from gui.pf_permissions import (
        PermissionsWidget,
        permissions_model,
    )
    _QT = True
except ImportError:
    _QT = False

_CONSENT = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
            r"\CapabilityAccessManager\ConsentStore")


class FakeWinReg:
    """A winreg-shaped fake backed by a {(path, name): value}
    dict. Records every call. Any access to a write API raises
    — the probe must never even reach for one."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    def __getattr__(self, name):
        if name in ("SetValueEx", "CreateKeyEx", "DeleteValue",
                    "DeleteKey", "SetValue"):
            raise AttributeError(
                "probe must never touch write API %s" % name)
        raise AttributeError(name)

    def OpenKey(self, hive, path, reserved=0, access=0):
        self.calls.append(("OpenKey", hive, path, access))
        if access != self.KEY_READ:
            raise OSError("probe opened a key for writing")
        return _FakeKey(self, path)

    def QueryValueEx(self, key, name):
        self.calls.append(("QueryValueEx", key.path, name))
        if (key.path, name) not in self.values:
            raise OSError("no such value")
        return self.values[(key.path, name)], 1


class _FakeKey:
    def __init__(self, reg, path):
        self._reg = reg
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RaisingReg:
    """Every operation fails — the probe must read unknown."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019

    def OpenKey(self, *args, **kwargs):
        raise OSError("registry unavailable")

    def QueryValueEx(self, *args, **kwargs):
        raise OSError("registry unavailable")


def _consent_values(master=None, desktop=None, cap="microphone"):
    values = {}
    if master is not None:
        values[(_CONSENT + "\\" + cap, "Value")] = master
    if desktop is not None:
        values[(_CONSENT + "\\" + cap + "\\NonPackaged",
                "Value")] = desktop
    return values


def _fake_probe(results):
    """A platform_probe callable returning SimpleNamespace
    results (the page reads them through _os_field)."""
    return lambda: dict(results)


class ProbeReadTests(unittest.TestCase):
    """The ConsentStore read: allowed / denied / unknown."""

    def test_master_allow_desktop_allow_is_allowed(self):
        reg = FakeWinReg(_consent_values("Allow", "Allow"))
        got = probe.probe_platform_permissions(registry=reg)
        mic = got["microphone"]
        self.assertEqual(mic.state, "allowed")
        self.assertEqual(mic.master, "allow")
        self.assertEqual(mic.desktop_apps, "allow")
        self.assertEqual(mic.source, "windows-consent-store")
        self.assertFalse(mic.per_app)

    def test_camera_uses_webcam_key(self):
        reg = FakeWinReg(_consent_values("Allow", "Allow",
                                         cap="webcam"))
        got = probe.probe_platform_permissions(registry=reg)
        self.assertEqual(got["camera"].state, "allowed")
        # The microphone key was never written: unknown, not allowed.
        self.assertEqual(got["microphone"].state, "unknown")

    def test_master_deny_is_denied_even_if_desktop_allow(self):
        reg = FakeWinReg(_consent_values("Deny", "Allow"))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "denied")
        self.assertIn("all apps", mic.reason)

    def test_desktop_deny_is_denied_with_shared_note(self):
        reg = FakeWinReg(_consent_values("Allow", "Deny"))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "denied")
        self.assertIn("desktop apps", mic.reason)
        self.assertIn("shared by every desktop app", mic.reason)
        self.assertIn("not VYUK-specific", mic.reason)

    def test_missing_desktop_value_defaults_to_allowed(self):
        reg = FakeWinReg(_consent_values("Allow", None))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "allowed")
        self.assertEqual(mic.desktop_apps, "allow")
        self.assertIn("default", mic.reason)

    def test_missing_master_is_unknown_never_a_guess(self):
        reg = FakeWinReg(_consent_values(None, "Allow"))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "unknown")
        self.assertNotEqual(mic.state, "denied")
        self.assertNotEqual(mic.state, "allowed")

    def test_garbage_value_is_unknown(self):
        reg = FakeWinReg(_consent_values("Maybe", "Allow"))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "unknown")

    def test_case_and_whitespace_tolerant(self):
        reg = FakeWinReg(_consent_values("  ALLOW ", "deny"))
        mic = probe.probe_platform_permissions(
            registry=reg)["microphone"]
        self.assertEqual(mic.state, "denied")

    def test_registry_failure_is_unknown(self):
        got = probe.probe_platform_permissions(
            registry=_RaisingReg())
        for pid in ("microphone", "camera"):
            self.assertEqual(got[pid].state, "unknown")
            self.assertNotEqual(got[pid].state, "denied")

    def test_off_windows_is_unknown(self):
        # The lab is Linux: the real path (no injection) reads
        # unknown without touching winreg.
        got = probe.probe_platform_permissions()
        for pid in ("microphone", "camera"):
            self.assertEqual(got[pid].state, "unknown")
            self.assertIn("not Windows", got[pid].source)

    def test_probe_never_writes(self):
        reg = FakeWinReg(_consent_values("Allow", "Allow"))
        probe.probe_platform_permissions(registry=reg)
        # Only reads happened, and every key open was KEY_READ.
        # The fake raises AttributeError on any write API
        # access; reaching this line means none was touched.
        for call in reg.calls:
            self.assertIn(call[0], ("OpenKey", "QueryValueEx"))
            if call[0] == "OpenKey":
                self.assertEqual(call[3], FakeWinReg.KEY_READ)
        self.assertTrue(reg.calls)

    def test_module_contains_no_write_calls(self):
        # Source-level proof: no code in the probe module ever
        # ACCESSES a registry write API (AST attribute walk —
        # the module docstring names the banned APIs in prose,
        # which is not a call). The fake above proves behaviour;
        # this proves no second write path exists.
        import ast
        text = (ROOT / "core" / "platform_permission_probe.py"
                ).read_text()
        tree = ast.parse(text)
        attrs = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)}
        for banned in ("SetValueEx", "CreateKeyEx", "DeleteValue",
                       "DeleteKey", "SetValue"):
            self.assertNotIn(banned, attrs)

    def test_probe_never_raises(self):
        class Boom:
            HKEY_CURRENT_USER = "HKCU"
            KEY_READ = 0x20019

            def __getattr__(self, name):
                raise RuntimeError("boom")

        got = probe.probe_platform_permissions(registry=Boom())
        self.assertEqual(got["microphone"].state, "unknown")
        self.assertEqual(got["camera"].state, "unknown")


class DeviceProbeTests(unittest.TestCase):
    """Microphone device presence: present / absent / unknown."""

    def test_active_default_is_present(self):
        got = probe.probe_microphone_device(
            device_reader=lambda: {"state": 1,
                                   "state_name": "active"})
        self.assertEqual(got["device"], "present")

    def test_disabled_default_is_absent(self):
        got = probe.probe_microphone_device(
            device_reader=lambda: {"state": 2,
                                   "state_name": "disabled"})
        self.assertEqual(got["device"], "absent")
        self.assertIn("disabled", got["detail"])

    def test_unplugged_default_is_absent(self):
        got = probe.probe_microphone_device(
            device_reader=lambda: {"state": 8,
                                   "state_name": "unplugged"})
        self.assertEqual(got["device"], "absent")
        self.assertIn("unplugged", got["detail"])

    def test_no_endpoint_is_unknown_never_absent(self):
        got = probe.probe_microphone_device(
            device_reader=lambda: None)
        self.assertEqual(got["device"], "unknown")
        self.assertNotEqual(got["device"], "absent")

    def test_reader_failure_is_unknown(self):
        def _boom():
            raise OSError("COM failed")

        got = probe.probe_microphone_device(device_reader=_boom)
        self.assertEqual(got["device"], "unknown")

    def test_off_windows_is_unknown(self):
        got = probe.probe_microphone_device()
        self.assertEqual(got["device"], "unknown")


class DiagnoseTests(unittest.TestCase):
    """The mic-arc three-way distinction."""

    def _os(self, state, master="allow"):
        return SimpleNamespace(state=state, master=master,
                               desktop_apps="allow")

    def test_vyuk_denied_wins_over_os_allowed(self):
        headline, fix = probe.diagnose_microphone(
            "denied", self._os("allowed"), {"device": "present"})
        self.assertIn("you switched VYUK", headline)
        self.assertIn("VYUK's switch", fix)

    def test_vyuk_denied_and_os_denied_names_both(self):
        headline, fix = probe.diagnose_microphone(
            "denied", self._os("denied", master="deny"),
            {"device": "present"})
        self.assertIn("you switched VYUK", headline)
        self.assertIn("Windows has it off too", headline)
        self.assertIn("Windows privacy settings", fix)

    def test_os_master_denied_points_at_master_switch(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("denied", master="deny"),
            {"device": "present"})
        self.assertIn("for all apps", headline)
        self.assertIn("Let apps access your microphone", fix)

    def test_os_desktop_denied_points_at_shared_toggle(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("denied", master="allow"),
            {"device": "present"})
        self.assertIn("desktop apps", headline)
        self.assertIn("Let desktop apps access your microphone",
                      fix)
        self.assertIn("shared by every desktop app", fix)

    def test_no_usable_device_is_the_9996_case(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("allowed"),
            {"device": "absent",
             "detail": "Default microphone is disabled."})
        self.assertIn("no microphone is usable", headline)
        self.assertIn("disabled", fix)
        self.assertIn("Sound settings", fix)
        self.assertIn("-9996", fix)

    def test_everything_on_says_should_work(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("allowed"), {"device": "present"})
        self.assertIn("should work", headline)
        self.assertIn("elsewhere", fix)

    def test_os_unknown_is_unknown_not_denial(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("unknown", master="unknown"),
            {"device": "present"})
        self.assertIn("couldn't be read", headline)
        self.assertIn("not a denial", headline)

    def test_device_unknown_says_couldnt_tell(self):
        headline, fix = probe.diagnose_microphone(
            "granted", self._os("allowed"), {"device": "unknown"})
        self.assertIn("couldn't tell whether a microphone is "
                      "connected", headline)
        self.assertIn("Sound settings", fix)


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


def _os_ns(state, reason=""):
    return SimpleNamespace(state=state, master="allow",
                           desktop_apps="allow", reason=reason)


@unittest.skipUnless(_QT, "Qt unavailable")
class PageRenderTests(unittest.TestCase):
    """The page shows VYUK-side and OS-side separately."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Voice-control batch (2026-11-16): the grants override
        # MUST be applied through the module identity the WIDGET
        # actually reads — pf_permissions._load_grants(), which
        # prefers the voice_assistant spelling — not the
        # directly-imported core.permission_grants. The two
        # spellings are separate module objects (the shim-planting
        # suites load permission_grants.py twice); an override set
        # on the core identity alone left the widget reading the
        # real store, which is why these three tests failed only
        # in combined Qt runs (after a suite that plants the
        # voice_assistant identity). The grant writes go through
        # the same identity, so the widget sees them.
        self._widget_pg = _pf._load_grants()
        self._widget_pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        self._widget_pg.set_permission_state("microphone", "granted",
                                             via="settings")
        self._widget_pg.set_permission_state("camera", "granted",
                                             via="settings")

    def tearDown(self):
        self._guard.__exit__()
        # Clear the override on BOTH identities: the widget's
        # (via _load_grants) and the directly-imported core one —
        # a leftover override on either leaks into the next suite.
        for _mod in (getattr(self, "_widget_pg", None), pg):
            if _mod is None:
                continue
            try:
                _mod.clear_path_override()
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
        self._tmp.cleanup()

    def _model(self, mic_os="allowed", cam_os="allowed",
               device=None):
        fake = _fake_probe({
            "microphone": _os_ns(
                mic_os, "Windows has the microphone off for "
                "desktop apps. This Windows switch is shared by "
                "every desktop app — it is not VYUK-specific, and "
                "Windows cannot say whether VYUK in particular "
                "is allowed."),
            "camera": _os_ns(cam_os),
        })
        return permissions_model(
            platform_probe=fake,
            device_reader=lambda: device or {"device": "present"})

    def test_os_line_separate_from_vyuk_line(self):
        entries = {e.permission_id: e
                   for e in self._model(mic_os="denied")}
        mic = entries["microphone"]
        self.assertEqual(mic.state, "granted")
        self.assertNotIn("Windows", mic.state_line())
        self.assertIn("Windows: off", mic.os_state_line())
        self.assertIn("shared by every desktop app",
                      mic.os_state_line())

    def test_os_allowed_renders_on(self):
        entries = {e.permission_id: e
                   for e in self._model(cam_os="allowed")}
        self.assertIn("Windows: on",
                      entries["camera"].os_state_line())

    def test_other_ids_have_no_os_line(self):
        for e in self._model():
            if e.permission_id in ("microphone", "camera"):
                continue
            self.assertIsNone(e.os_state)
            self.assertEqual(e.os_state_line(), "")

    def test_probe_failure_renders_unknown(self):
        def _boom():
            raise OSError("registry gone")

        entries = {e.permission_id: e
                   for e in permissions_model(platform_probe=_boom)}
        self.assertEqual(entries["microphone"].os_state, "unknown")
        self.assertIn("couldn't be read",
                      entries["microphone"].os_state_line())
        self.assertNotIn("Windows: off",
                         entries["microphone"].os_state_line())

    def test_default_probe_on_linux_is_unknown(self):
        entries = {e.permission_id: e
                   for e in permissions_model()}
        self.assertEqual(entries["microphone"].os_state, "unknown")
        self.assertEqual(entries["camera"].os_state, "unknown")

    def test_diagnosis_attached_for_microphone(self):
        entries = {e.permission_id: e
                   for e in self._model(
                       mic_os="allowed",
                       device={"device": "absent",
                               "detail": "Default microphone is "
                               "disabled."})}
        self.assertIn("no microphone is usable",
                      entries["microphone"].diagnosis)
        self.assertIn("-9996", entries["microphone"].diagnosis)

    def test_widget_renders_os_labels_and_buttons(self):
        widget = PermissionsWidget(
            platform_probe=_fake_probe({
                "microphone": _os_ns("denied", "Windows has the "
                                     "microphone off for desktop "
                                     "apps."),
                "camera": _os_ns("allowed"),
            }),
            device_reader=lambda: {"device": "present"})
        self.assertIn("Windows: off",
                      widget._os_labels["microphone"].text())
        self.assertIn("Windows: on",
                      widget._os_labels["camera"].text())
        self.assertNotIn("screen", widget._os_labels)
        self.assertIn("Open Windows microphone settings",
                      widget._os_settings_buttons["microphone"]
                      .text())
        self.assertIn("Open Windows camera settings",
                      widget._os_settings_buttons["camera"].text())
        # The injected mic OS state is denied, so the diagnosis
        # names the Windows side, not "should work".
        self.assertIn("desktop apps",
                      widget._diagnosis_labels["microphone"].text())

    def test_widget_d2_copy_present_on_mic_and_camera(self):
        widget = PermissionsWidget(
            platform_probe=_fake_probe({
                "microphone": _os_ns("allowed"),
                "camera": _os_ns("allowed"),
            }),
            device_reader=lambda: {"device": "present"})
        labels = [w.text() for w in
                  widget.findChildren(QLabel)]
        d2 = [t for t in labels if
              "Switching this off stops VYUK using it" in t]
        self.assertEqual(len(d2), 2)

    def test_widget_jump_button_click_never_raises(self):
        widget = PermissionsWidget(
            platform_probe=_fake_probe({
                "microphone": _os_ns("allowed"),
                "camera": _os_ns("allowed"),
            }),
            device_reader=lambda: {"device": "present"})
        # Offscreen Linux: the ms-settings: URI does not
        # resolve, but the click must not crash Settings, and
        # the page must still show its OS state afterwards.
        widget._os_settings_buttons["microphone"].click()
        widget._os_settings_buttons["camera"].click()
        self.assertIn("Windows: on",
                      widget._os_labels["microphone"].text())

    def test_widget_refresh_reprobes_os_state(self):
        holder = {"mic": "allowed"}

        def _mutable_probe():
            return {
                "microphone": _os_ns(holder["mic"]),
                "camera": _os_ns("allowed"),
            }

        widget = PermissionsWidget(
            platform_probe=_mutable_probe,
            device_reader=lambda: {"device": "present"})
        self.assertIn("Windows: on",
                      widget._os_labels["microphone"].text())
        holder["mic"] = "denied"
        widget._refresh_from_store()
        self.assertIn("Windows: off",
                      widget._os_labels["microphone"].text())

    def test_page_probe_never_writes(self):
        reg = FakeWinReg(_consent_values("Allow", "Allow"))

        def _recording_probe():
            return probe.probe_platform_permissions(registry=reg)

        widget = PermissionsWidget(
            platform_probe=_recording_probe,
            device_reader=lambda: {"device": "present"})
        widget._refresh_from_store()
        for call in reg.calls:
            self.assertIn(call[0], ("OpenKey", "QueryValueEx"))
            if call[0] == "OpenKey":
                self.assertEqual(call[3], FakeWinReg.KEY_READ)
        self.assertTrue(reg.calls)


if __name__ == "__main__":
    unittest.main()
