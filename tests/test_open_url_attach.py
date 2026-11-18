"""B2 — open_url through the Fix 3 verified attach.

Covers: when the browser opt-in is on and the URL is a page,
open_url routes through browser_control's goto (verified attach +
page-load probe) and returns a seam Outcome — verified on a loaded
page, unverified while still loading, failed on a failed load or a
failed attach, with NO silent fallback to webbrowser.open. When
the opt-in is off, or the URL is not a page (file://, custom
scheme), or a browser was named, or the URL is empty, the
webbrowser.open fallback runs exactly as before, with the honest
"can't see or drive afterwards" wording. The opt-in is read, never
written — open_url never enables the browser.

Calls go through the REAL tools.execute funnel, with the same
isolation the defect-batch URL suite uses (temp data guard, user
provenance, lock off, taint cleared, confirm cleared). The browser
session is faked at the browser_control boundary (no real Chrome,
no CDP, no network); the opt-in is faked at the canonical
browser_cdp binding.
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
    from core.desktop import browser_cdp as browser_cdp_mod
    from core.desktop import browser_enable as browser_enable_mod
    from core.funnel import tools_gate as tools_mod
    from core.funnel.tools_registry import tools
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
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="b2_open_url_"))
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


def _attach_on():
    """Patch the opt-in consult ON (read-only).

    Guards rule (test_refactor_phase6_guards): patch-target
    STRINGS naming a packaged stem are old-path targets, so the
    patch goes through mock.patch.object on the canonical module
    object, not a dotted string (R1 precedent).
    """
    return mock.patch.object(
        browser_cdp_mod, "_real_chrome_attach_allowed",
        return_value=True)


def _attach_off():
    return mock.patch.object(
        browser_cdp_mod, "_real_chrome_attach_allowed",
        return_value=False)


class TestAttachedOpen(_Base):
    """Opt-in on: the goto path, with a seam Outcome, no fallback."""

    def test_opened_page_is_verified_and_drivable(self):
        fake_goto = mock.Mock(
            return_value="Opened 'Example' — https://example.com.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened, \
                mock.patch.object(browser_enable_mod,
                                "_set_opt_in") as setter:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertTrue(out.get("outcome_verified"), str(out))
        self.assertIn("Example", out.get("outcome_evidence", ""))
        self.assertIn("Opened 'Example' — https://example.com.",
                      _body(out))
        opened.assert_not_called()
        setter.assert_not_called()
        fake_goto.assert_called_once_with("goto", url="https://example.com")

    def test_bootstrap_note_kept_and_still_verified(self):
        fake_goto = mock.Mock(
            return_value="Attached to your Chrome to do this. "
            "Opened 'Example' — https://example.com.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertTrue(out.get("outcome_verified"), str(out))
        self.assertIn("Attached to your Chrome to do this.", _body(out))
        opened.assert_not_called()

    def test_bare_host_goes_through_goto_unnormalised(self):
        fake_goto = mock.Mock(
            return_value="Opened 'Example' — https://example.com.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "example.com"})
        self.assert_success(out)
        self.assertTrue(out.get("outcome_verified"), str(out))
        fake_goto.assert_called_once_with("goto", url="example.com")
        opened.assert_not_called()

    def test_still_loading_is_unverified_not_claimed(self):
        fake_goto = mock.Mock(
            return_value="Still loading https://example.com — the page "
            "didn't finish within 20s, but the navigation is under "
            "way and the connection is fine. Ask me to wait, or to "
            "read the page.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertFalse(out.get("outcome_verified"), str(out))
        self.assertIn("Still loading", _body(out))
        self.assertNotIn("Opened 'Example'", _body(out))
        opened.assert_not_called()

    def test_failed_load_is_failed_with_no_silent_fallback(self):
        fake_goto = mock.Mock(
            return_value="Browser action failed: couldn't open "
            "https://example.com (net::ERR_NAME_NOT_RESOLVED).")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertFalse(out.get("outcome_verified"), str(out))
        self.assertIn("couldn't open", _body(out))
        self.assertNotIn("Opened https://example.com", _body(out))
        opened.assert_not_called()

    def test_failed_attach_is_failed_with_no_silent_fallback(self):
        fake_goto = mock.Mock(
            return_value="No controllable browser session is active. "
            "I tried to attach to your Chrome to do this and "
            "couldn't: Couldn't attach to Chrome at "
            "http://127.0.0.1:9222. open_url opens the system "
            "browser, which I can't see or drive afterwards.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertFalse(out.get("outcome_verified"), str(out))
        self.assertIn("No controllable browser session", _body(out))
        opened.assert_not_called()

    def test_unrecognised_goto_shape_is_unverified(self):
        fake_goto = mock.Mock(return_value="Something entirely new.")
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open") as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertFalse(out.get("outcome_verified"), str(out))
        opened.assert_not_called()


class TestFallbackWhenNotEnabled(_Base):
    """Opt-in off: the webbrowser.open fallback, exactly as before."""

    def test_fallback_success_keeps_old_sentence(self):
        fake_goto = mock.Mock()
        with _attach_off(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertNotIn("outcome_verified", out)
        self.assertIn("Opened https://example.com in your browser.",
                      _body(out))
        self.assertIn("can't see or drive afterwards", _body(out))
        fake_goto.assert_not_called()
        opened.assert_called_once_with("https://example.com")

    def test_fallback_failure_is_honest(self):
        with _attach_off(), \
                mock.patch("webbrowser.open", return_value=False):
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertIn("didn't open it", _body(out))

    def test_opt_in_consult_failure_fails_closed_to_fallback(self):
        fake_goto = mock.Mock()
        with mock.patch.object(
                browser_cdp_mod, "_real_chrome_attach_allowed",
                side_effect=RuntimeError("config unreadable")), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "https://example.com"})
        self.assert_success(out)
        self.assertIn("Opened https://example.com in your browser.",
                      _body(out))
        fake_goto.assert_not_called()
        opened.assert_called_once()


class TestFallbackKeptForNonPageUrls(_Base):
    """Non-page schemes, named browsers, empty URLs: fallback as today."""

    def test_file_url_keeps_todays_fallback(self):
        fake_goto = mock.Mock()
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "file:///etc/passwd"})
        self.assert_success(out)
        self.assertNotIn("outcome_verified", out)
        self.assertIn("Opened https://file:///etc/passwd",
                      _body(out))
        fake_goto.assert_not_called()
        opened.assert_called_once_with("https://file:///etc/passwd")

    def test_custom_scheme_keeps_todays_fallback(self):
        fake_goto = mock.Mock()
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "spotify:track:123"})
        self.assert_success(out)
        fake_goto.assert_not_called()
        opened.assert_called_once_with("https://spotify:track:123")

    def test_named_browser_keeps_todays_path(self):
        fake_goto = mock.Mock()
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("core.funnel.tools_handlers._browser_controller",
                           return_value=None), \
                mock.patch("core.funnel.tools_handlers_command_url._browser_controller",
                           return_value=None), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "https://example.com",
                                         "browser": "chrome"})
        self.assert_success(out)
        self.assertIn("default browser instead", _body(out))
        fake_goto.assert_not_called()
        opened.assert_called_once_with("https://example.com")

    def test_empty_url_keeps_todays_fallback(self):
        fake_goto = mock.Mock()
        with _attach_on(), \
                mock.patch("core.browser_dispatch.browser_control",
                           fake_goto), \
                mock.patch("webbrowser.open",
                           return_value=True) as opened:
            out = self.call("open_url", {"url": "   "})
        self.assert_success(out)
        fake_goto.assert_not_called()
        opened.assert_called_once_with("https://")


if __name__ == "__main__":
    unittest.main()
