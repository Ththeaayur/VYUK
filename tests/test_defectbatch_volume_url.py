"""Post-triage defect batch, item C — volume + URL honesty.

Regression tests for the two test-leg defects (found by wiring
item 5, pinned not fixed):

1. adjust_volume raised on Linux (ctypes.windll, unguarded in
   _send_key_event). It now returns the Windows-only sentence
   like its siblings (lock_screen's pattern) on any non-Windows
   system. Linux-branch tests only — on Windows the handler
   really does send key events, so it is skipped there.
2. open_url reported "Opened ..." without checking
   webbrowser.open's result. It now checks the return value:
   False yields an honest failure sentence instead of a success
   claim, in both the default-browser branch and the
   named-browser-fallback branch. webbrowser.open (and the named
   browser controller lookup) are stubbed; no real browser opens.

Calls go through the REAL tools.execute funnel, with the same
isolation the testleg suite uses (temp data guard, user
provenance, lock off, taint cleared, confirm cleared).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    from core import confirm_gate, injection
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.config import config as app_config
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

try:
    from core.platform import reset_backend_cache
except Exception:  # noqa: BLE001 — optional seam
    reset_backend_cache = None

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"


def _body(out):
    if not isinstance(out, dict):
        return str(out)
    value = out.get("result")
    if value is None:
        value = out.get("message", "")
    return str(value)


class _Base(unittest.TestCase):
    """Funnel isolation + whole-data-tree guard (testleg pattern)."""

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="defectbatch_vol_url_"))
        self._saved = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    self._saved[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass
        self._orig_env = os.environ.pop("VYUK_PLATFORM", None)
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:  # noqa: BLE001 — best effort only
                pass
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        tools_mod.revoke_screen_scope()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config, "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.revoke_screen_scope()
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        if self._orig_env is None:
            os.environ.pop("VYUK_PLATFORM", None)
        else:
            os.environ["VYUK_PLATFORM"] = self._orig_env
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:  # noqa: BLE001 — best effort only
                pass
        current = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    current[path.relative_to(_DATA_DIR)] = path
                except OSError:
                    pass
        for rel, path in current.items():
            if rel not in self._saved:
                try:
                    path.unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name, args):
        confirm_gate.clear_handler()
        confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def assert_success(self, out):
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "success", str(out))


@unittest.skipIf(sys.platform.startswith("win"),
                 "Linux-branch test: on Windows the handler really sends keys")
class TestAdjustVolumeLinux(_Base):
    """adjust_volume on Linux: Windows-only sentence, never a raise."""

    def test_up_returns_windows_only_sentence(self):
        out = self.call("adjust_volume", {"action": "up", "steps": 3})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("Windows", body)
        self.assertIn("media keys", body)
        self.assertNotIn("Increased volume", body)

    def test_mute_returns_windows_only_sentence(self):
        out = self.call("adjust_volume", {"action": "mute"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("Windows", body)
        self.assertNotIn("Toggled master audio mute", body)


class TestOpenUrlResultChecked(_Base):
    """open_url: the reported outcome matches webbrowser.open's result."""

    def test_default_browser_success_still_claims_opened(self):
        with mock.patch("webbrowser.open", return_value=True) as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertIn("Opened https://example.com", _body(out))
        opened.assert_called_once()

    def test_default_browser_failure_is_honest(self):
        with mock.patch("webbrowser.open", return_value=False):
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("didn't open it", body)
        self.assertNotIn("Opened https://example.com", body)

    def test_named_fallback_failure_is_honest(self):
        # INTENTIONAL (skills split B49): _tool_open_url moved to the
        # command_url mixin and resolves _browser_controller from the
        # mixin module's globals, so the facade patch alone no longer
        # steers it — patch BOTH bindings (B16/B47 pattern), same
        # fake, both restored.
        with mock.patch("core.funnel.tools_handlers._browser_controller",
                        return_value=None), \
                mock.patch("core.funnel.tools_handlers_command_url._browser_controller",
                        return_value=None), \
                mock.patch("webbrowser.open", return_value=False):  # Phase 3 F3 INTENTIONAL: patch target moved where bound (tools_handlers -> core/funnel/)
            out = self.call("open_url", {"url": "https://example.com",
                                         "browser": "chrome"})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("didn't open it", body)
        self.assertNotIn("default browser instead", body)

    def test_named_fallback_success_keeps_old_sentence(self):
        # INTENTIONAL (skills split B49): _tool_open_url moved to the
        # command_url mixin and resolves _browser_controller from the
        # mixin module's globals, so the facade patch alone no longer
        # steers it — patch BOTH bindings (B16/B47 pattern), same
        # fake, both restored.
        with mock.patch("core.funnel.tools_handlers._browser_controller",
                        return_value=None), \
                mock.patch("core.funnel.tools_handlers_command_url._browser_controller",
                        return_value=None), \
                mock.patch("webbrowser.open", return_value=True):  # Phase 3 F3 INTENTIONAL: patch target moved where bound (tools_handlers -> core/funnel/)
            out = self.call("open_url", {"url": "https://example.com",
                                         "browser": "chrome"})
        self.assert_success(out)
        self.assertIn("default browser instead", _body(out))


if __name__ == "__main__":
    unittest.main()
