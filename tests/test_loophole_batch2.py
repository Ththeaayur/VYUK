"""Loophole batch 2 — browser cluster: #3, L-29, #4.

The three items, one build (2026-11-21, user-approved: close all
32 Stage 0 loopholes):

* #3 (browser_click is NOT confirm-gated): a commit click — "Confirm
  purchase", "Delete account" — used to land in real Chrome without
  asking. The handler-level trust gate now judges more than the
  old pay/delete words: upload, attach, share, save, subscribe,
  cancel (account/subscription/order/membership/booking), close
  account, deactivate, agree, accept, book, reserve, publish,
  transfer, withdraw, sign up, log in, sign in, post a
  comment/reply/message/update. Navigation and ordinary clicks stay
  free — the gate is at the handler level, where the click is
  judged against the resolved element, so a funnel-level gate
  would only double-ask in clean sessions.
* L-29 (the browser is an unguarded exfiltration channel): there is
  no programmatic upload mechanism (no set_input_files anywhere in
  core/), so the upload flow's commit steps are the clicks, which
  the #3 gate covers — plus a file-input check (the attach step
  asks first) and a submit-control form-intent check (a benign
  "Next" in a checkout form asks, a benign "Search" stays free).
* #4 (the CDP toggle is user-flipped and sticky): the toggle is
  Chrome's own switch and VYUK cannot flip it, so it is now
  recorded when VYUK attaches through it (a durable flag file,
  data/browser_toggle_left_on.json), named on disconnect, and
  shown plainly in Settings and in the status — and the flag
  self-heals when a probe verifies the toggle is off.

The suite runs under both interpreters (sys + Qt). The Settings
card leg is Qt-only (skipped under sys, which has no PySide6);
every other leg is interpreter-neutral. The funnel legs go
through the real funnel (tools.execute) with only the
Chrome/Playwright boundary stubbed (the session seams
_ensure_session, _need_connection, _active_page,
_login_wall_result, _scoped_locator) and a fake Playwright
page/locator standing in for the real tab; the confirm gate and
its handler are real. The #4 legs go through the real enable
flow and the real disconnect path with only the CDP/Chrome probe
boundary stubbed. The toggle flag, the activity log (the
disconnect leg appends a reset line) and the tree's .env (the
enable leg flips the opt-in, which persists it) are snapshotted
in setUpModule and restored in tearDownModule; the funnel legs
run against a temporary receipt store, so the suite leaves
data/ as it found it.
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import types as _types_mod
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.confirm_gate as confirm_gate
import core.injection as injection
import core.tool_receipts as tool_receipts
from core.desktop import browser_enable as be_mod
from core.desktop import browser_gates as gates_mod
from core.desktop import browser_session as bs_mod
from core.desktop import browser_toggle as toggle_mod
from core.funnel import rule_of_two as _rot
from core.funnel import tools_gate as _tools_gate
from core.funnel.tools_registry import tools

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = _QLabel = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
BrainServicesMixin = None

_FLAG = "browser_toggle_left_on.json"


def _data_path(name):
    return ROOT / "data" / name


def _env_path():
    return ROOT / ".env"


def _register_handler(fn):
    """Register a confirm handler on every confirm_gate module
    identity present (the flat core.* one the funnel reads, and
    the voice_assistant.* one the GUI synthesises)."""
    confirm_gate.register_handler(fn)
    va = sys.modules.get("voice_assistant.core.confirm_gate")
    if va is not None and va is not confirm_gate:
        try:
            va.register_handler(fn)
        except Exception:  # noqa: BLE001 — the flat one rules
            pass


def _clear_handler():
    try:
        confirm_gate.clear_handler()
    except Exception:  # noqa: BLE001 — nothing registered
        pass
    va = sys.modules.get("voice_assistant.core.confirm_gate")
    if va is not None and va is not confirm_gate:
        try:
            va.clear_handler()
        except Exception:  # noqa: BLE001 — nothing registered
            pass


def setUpModule():
    """Snapshot the toggle flag, the activity log (the disconnect
    leg appends a reset line to it) and the tree's .env; under Qt,
    build the voice_assistant.* alias and the QApplication
    (mirrors test_loophole_batch1)."""
    global _APP, _VA_SNAPSHOT, _QT, BrainServicesMixin
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    setUpModule._flag_snapshot = (
        _data_path(_FLAG).read_bytes()
        if _data_path(_FLAG).exists() else None)
    setUpModule._activity_snapshot = (
        _data_path("activity.log").read_bytes()
        if _data_path("activity.log").exists() else None)
    setUpModule._env_snapshot = (
        _env_path().read_bytes()
        if _env_path().exists() else None)
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    try:
        from gui.window_brain_services import (  # noqa: E402
            BrainServicesMixin as _BSM)
        BrainServicesMixin = _BSM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        BrainServicesMixin = None


def tearDownModule():
    """Restore the toggle flag, the activity log and the tree's
    .env, and the voice_assistant.* modules, so the suite leaves
    the tree as it found it."""
    flag = _data_path(_FLAG)
    if setUpModule._flag_snapshot is None:
        try:
            flag.unlink()
        except OSError:
            pass
    else:
        flag.write_bytes(setUpModule._flag_snapshot)
    activity = _data_path("activity.log")
    if setUpModule._activity_snapshot is None:
        try:
            activity.unlink()
        except OSError:
            pass
    else:
        activity.write_bytes(setUpModule._activity_snapshot)
    env = _env_path()
    if setUpModule._env_snapshot is None:
        try:
            env.unlink()
        except OSError:
            pass
    else:
        env.write_bytes(setUpModule._env_snapshot)
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)


# ------------------------------------------------------------ fakes
class _FakeMouse:
    def __init__(self):
        self.calls = []

    def click(self, x, y, button="left"):
        self.calls.append(("click", x, y, button))

    def dblclick(self, x, y):
        self.calls.append(("dblclick", x, y))

    def move(self, x, y):
        self.calls.append(("move", x, y))


class _FakePage:
    def __init__(self, url="https://example.test/", title="Example"):
        self.url = url
        self._title = title
        self.mouse = _FakeMouse()

    def title(self):
        return self._title

    @property
    def main_frame(self):
        return self

    def evaluate(self, js):
        return {}

    def wait_for_load_state(self, state, timeout=None):
        return None


class _FakeLoc:
    """A resolved element: visible text + per-attribute values,
    plus a kind dict (file/submit) and a form-intent dict for the
    evaluate() probes."""

    def __init__(self, text="", attrs=None, kind=None, form=None):
        self._text = text
        self._attrs = dict(attrs or {})
        self._kind = kind or {"file": False, "submit": False}
        self._form = form
        self.clicked = []

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def is_visible(self):
        return True

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None, button="left"):
        self.clicked.append("click")

    def dblclick(self, timeout=None):
        self.clicked.append("double")

    def hover(self, timeout=None):
        self.clicked.append("hover")

    def evaluate(self, js):
        if "VYUK-ELEMENT-KIND" in js:
            return dict(self._kind)
        return self._form


class _FakeBase:
    """A locator-set standing in for one resolved element."""

    def __init__(self, loc):
        self._loc = loc

    @property
    def first(self):
        return self._loc

    def nth(self, i):
        return self._loc

    def count(self):
        return 1


# ------------------------------------------------------------ #3 regex
class TestDangerousClickRegex(unittest.TestCase):
    """The extended gate words match; the skipped words don't
    (bare post/apply/close/cancel/submit stay free — they are
    navigation and ordinary clicks)."""

    COMMIT = (
        "Upload", "Attach file", "Share", "Save", "Subscribe",
        "Cancel account", "Cancel subscription", "Cancel my order",
        "Cancel booking", "Cancel a membership", "Close account",
        "Close your account", "Deactivate", "I agree", "Accept",
        "Book now", "Reserve a table", "Publish", "Transfer",
        "Withdraw", "Sign up", "Log in", "Sign in",
        "Post a comment", "Post reply", "Post a message",
        "Post an update",
    )
    FREE = (
        "Home", "Next", "Search", "Back", "Saved items",
        "Shared with me", "My bookings", "My uploads", "Agreement",
        "Published posts", "Cancel", "Close", "Post", "Apply",
        "Settings", "Profile",
    )

    def test_commit_words_match(self):
        for word in self.COMMIT:
            with self.subTest(word=word):
                self.assertIsNotNone(
                    gates_mod._DANGEROUS_CLICK_RE.search(word),
                    f"{word!r} should be gated")

    def test_navigation_words_do_not_match(self):
        for word in self.FREE:
            with self.subTest(word=word):
                self.assertIsNone(
                    gates_mod._DANGEROUS_CLICK_RE.search(word),
                    f"{word!r} should stay free")

    def test_destructive_submit_regex_matches_new_words(self):
        for word in ("Upload", "Cancel booking", "Close account",
                     "Deactivate", "Agree", "Book now", "Publish",
                     "Sign up", "Post a comment"):
            with self.subTest(word=word):
                self.assertIsNotNone(
                    gates_mod._DESTRUCTIVE_SUBMIT_RE.search(word),
                    f"{word!r} should be gated on submit")


class TestElementKind(unittest.TestCase):
    """_element_kind reads the probe dict: file, submit, neither,
    and a failed probe reads as neither."""

    def test_file_input(self):
        loc = _FakeLoc(kind={"file": True, "submit": False})
        self.assertEqual(gates_mod._element_kind(loc),
                         {"file": True, "submit": False})

    def test_submit_control(self):
        loc = _FakeLoc(kind={"file": False, "submit": True})
        self.assertEqual(gates_mod._element_kind(loc),
                         {"file": False, "submit": True})

    def test_plain_element_is_neither(self):
        loc = _FakeLoc()
        self.assertEqual(gates_mod._element_kind(loc),
                         {"file": False, "submit": False})

    def test_non_dict_probe_is_neither(self):
        loc = _FakeLoc()
        loc.evaluate = lambda js: "not a dict"
        self.assertEqual(gates_mod._element_kind(loc),
                         {"file": False, "submit": False})

    def test_failed_probe_is_neither(self):
        loc = _FakeLoc()

        def _boom(js):
            raise RuntimeError("page gone")
        loc.evaluate = _boom
        self.assertEqual(gates_mod._element_kind(loc),
                         {"file": False, "submit": False})


# ------------------------------------------------- #3 / L-29 funnel
class _ClickBase(unittest.TestCase):
    """One real-funnel browser_click with the session stubbed
    and a temp receipt store; the confirm gate is real."""

    def setUp(self):
        super().setUp()
        # Loophole batch 4: the speaker lock defaults ON, so a
        # session with no provenance ("unknown") now gates every
        # state-changing tool — a plain click these legs measure
        # as free would ask once for provenance, not for the
        # commit. These legs run in a user-started session (the
        # shape where the commit gate alone decides).
        _tools_gate.set_session_provenance("user")
        self._tmp = tempfile.TemporaryDirectory(prefix="b2_receipts_")
        self._receipts = (pathlib.Path(self._tmp.name)
                          / "tool_receipts.jsonl")
        tool_receipts.install_store(self._receipts)
        tool_receipts.reset_receipt_degraded()
        injection.clear_taint(force=True)
        _rot.reset_for_tests()
        self.asks = []
        self.decision = {"ok": False}
        self._lock = threading.Lock()

        def _handler(name, summary=None, *a, **k):
            with self._lock:
                self.asks.append((name, summary))
            return self.decision["ok"]

        self._handler = _handler
        _register_handler(_handler)
        self.page = _FakePage()

    def tearDown(self):
        _tools_gate.set_session_provenance("unknown")
        _clear_handler()
        tool_receipts.reset_default()
        tool_receipts.reset_receipt_degraded()
        self._tmp.cleanup()
        injection.clear_taint(force=True)
        _rot.reset_for_tests()
        super().tearDown()

    def _click(self, target, loc, approve=False):
        import core.desktop.browser_click as click_mod
        before = len(self.asks)
        self.decision["ok"] = approve
        saved = {}
        for mod, name, val in (
                (bs_mod, "_ensure_session", lambda: (True, False, "")),
                (click_mod, "_need_connection", lambda: None),
                (click_mod, "_active_page", lambda: self.page),
                (click_mod, "_login_wall_result", lambda p: None),
                (click_mod, "_scoped_locator",
                 lambda p, by, t: (_FakeBase(loc), None)),
        ):
            saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, val)
        try:
            result = tools.execute("browser_click", {
                "target": target, "by": "text", "click": "click"})
        finally:
            for (mod, name), val in saved.items():
                setattr(mod, name, val)
        return result, len(self.asks) - before, list(loc.clicked)


class TestBrowserClickGate(_ClickBase):
    """The funnel legs: commit clicks ask, plain clicks stay
    free, an approved commit click lands."""

    def test_commit_click_asks_first(self):
        loc = _FakeLoc(text="Confirm purchase")
        r, n, clicked = self._click("Confirm purchase", loc)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, [])
        self.assertEqual(r.get("status"), "refused")

    def test_upload_click_asks_first(self):
        loc = _FakeLoc(text="Upload")
        r, n, clicked = self._click("Upload", loc)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, [])

    def test_cancel_account_click_asks_first(self):
        loc = _FakeLoc(text="Cancel account")
        r, n, clicked = self._click("Cancel account", loc)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, [])

    def test_home_click_stays_free(self):
        loc = _FakeLoc(text="Home")
        r, n, clicked = self._click("Home", loc)
        self.assertEqual(n, 0)
        self.assertEqual(clicked, ["click"])
        self.assertEqual(r.get("status"), "success")

    def test_file_input_asks_first(self):
        loc = _FakeLoc(text="", attrs={"type": "file"},
                       kind={"file": True, "submit": False})
        r, n, clicked = self._click("Choose file", loc)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, [])

    def test_next_submit_in_checkout_form_asks(self):
        form = {"found": True, "has_form": True,
                "action": "/checkout", "buttons": "Next"}
        loc = _FakeLoc(text="Next", attrs={"type": "submit"},
                       kind={"file": False, "submit": True}, form=form)
        r, n, clicked = self._click("Next", loc)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, [])

    def test_search_submit_stays_free(self):
        form = {"found": True, "has_form": True,
                "action": "/search", "buttons": "Search"}
        loc = _FakeLoc(text="Search", attrs={"type": "submit"},
                       kind={"file": False, "submit": True}, form=form)
        r, n, clicked = self._click("Search", loc)
        self.assertEqual(n, 0)
        self.assertEqual(clicked, ["click"])

    def test_approved_commit_click_lands(self):
        loc = _FakeLoc(text="Confirm purchase")
        r, n, clicked = self._click("Confirm purchase", loc,
                                    approve=True)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(clicked, ["click"])
        self.assertEqual(r.get("status"), "success")


# ------------------------------------------------------------ #4 store
class TestToggleFlagStore(unittest.TestCase):
    """The flag store: note/clear round-trip, and a broken store
    reads as not set (never as on)."""

    def setUp(self):
        super().setUp()
        self._flag = _data_path(_FLAG)
        self._had = self._flag.read_bytes() if self._flag.exists() else None
        if self._flag.exists():
            self._flag.unlink()

    def tearDown(self):
        if self._had is None:
            try:
                self._flag.unlink()
            except OSError:
                pass
        else:
            self._flag.write_bytes(self._had)
        super().tearDown()

    def test_note_clear_round_trip(self):
        self.assertFalse(toggle_mod.toggle_flag_set())
        self.assertTrue(toggle_mod.note_toggle_attach(9333))
        self.assertTrue(toggle_mod.toggle_flag_set())
        flag = toggle_mod.toggle_flag()
        self.assertEqual(flag.get("port"), 9333)
        self.assertTrue(toggle_mod.clear_toggle_flag())
        self.assertFalse(toggle_mod.toggle_flag_set())

    def test_broken_store_reads_as_not_set(self):
        self._flag.write_text("{not json", encoding="utf-8")
        self.assertIsNone(toggle_mod.toggle_flag())
        self.assertFalse(toggle_mod.toggle_flag_set())

    def test_clear_when_absent_is_true(self):
        self.assertTrue(toggle_mod.clear_toggle_flag())


# ------------------------------------------------------------ #4 flow
def _chrome_handshake(port):
    return {"version": {"Browser": "Chrome/136.0.0.0",
                        "webSocketDebuggerUrl":
                            f"ws://127.0.0.1:{port}/x"},
            "targets": [{"type": "page",
                         "url": "https://example.test/"}]}


class _ToggleBase(unittest.TestCase):
    """The #4 legs run the real enable/status/disconnect paths
    with only the CDP/Chrome probe boundary stubbed. The enable
    leg flips the opt-in (which persists a .env), so each leg
    removes the .env it creates and resets the in-process flag."""

    def tearDown(self):
        flag = _data_path(_FLAG)
        try:
            flag.unlink()
        except OSError:
            pass
        env = _env_path()
        try:
            env.unlink()
        except OSError:
            pass
        try:
            from core import config as _cfg_mod
            obj = getattr(_cfg_mod, "config", None)
            if obj is not None:
                setattr(obj, "browser_allow_real_chrome_attach", False)
            os.environ.pop("BROWSER_ALLOW_REAL_CHROME_ATTACH", None)
        except Exception:  # noqa: BLE001 — best effort
            pass
        super().tearDown()

    def _enable_stubs(self):
        connected = {"v": False}

        def _attach_stub(port=None):
            connected["v"] = True
            return "attached"

        patches = [
            mock.patch.object(
                be_mod, "_probe_cdp",
                lambda port: (_chrome_handshake(port)
                              if port == 9333 else None)),
            mock.patch.object(be_mod, "_read_devtools_active_port",
                              lambda profile: 9333),
            mock.patch.object(be_mod, "_is_connected",
                              lambda: connected["v"]),
            mock.patch.object(be_mod, "_status_line",
                              lambda: "1 tab open."),
            mock.patch.object(be_mod, "_chrome_running",
                              lambda: True),
            mock.patch.object(be_mod, "_attach", _attach_stub),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return connected


class TestToggleFlow(_ToggleBase):
    """Toggle attach records the flag; disconnect warns while it
    is on; status verifies it off and clears it, and names it in
    plain words while it is on."""

    def test_toggle_attach_records_flag(self):
        self._enable_stubs()
        result = be_mod.enable_browser_control()
        self.assertEqual(result.get("state"), "attached")
        self.assertTrue(_data_path(_FLAG).exists())

    def test_disconnect_warns_while_toggle_on(self):
        self.assertTrue(toggle_mod.note_toggle_attach(9333))
        fake_browser = mock.Mock()
        with mock.patch.object(bs_mod, "_connected",
                               lambda: True), \
                mock.patch.dict(bs_mod._state,
                                {"browser": fake_browser}, clear=False):
            result = bs_mod._impl_close()
        self.assertIn("toggle", result.lower())
        self.assertIn("chrome://inspect", result)
        # The flag stays: disconnect cannot verify the toggle off.
        self.assertTrue(_data_path(_FLAG).exists())

    def test_status_clears_flag_when_verified_off(self):
        self.assertTrue(toggle_mod.note_toggle_attach(9333))
        with mock.patch.object(be_mod, "_cdp_port_answers",
                               lambda port: False), \
                mock.patch.object(be_mod, "_read_devtools_active_port",
                                  lambda profile: 9333), \
                mock.patch.object(be_mod, "_is_connected",
                                  lambda: False):
            status = be_mod.browser_enable_status()
        self.assertFalse(status.get("toggle_on"))
        self.assertFalse(_data_path(_FLAG).exists())

    def test_status_names_toggle_in_plain_words(self):
        self.assertTrue(toggle_mod.note_toggle_attach(9333))
        with mock.patch.object(be_mod, "_cdp_port_answers",
                               lambda port: True), \
                mock.patch.object(be_mod, "_read_devtools_active_port",
                                  lambda profile: 9333), \
                mock.patch.object(be_mod, "_is_connected",
                                  lambda: False):
            status = be_mod.browser_enable_status()
        self.assertTrue(status.get("toggle_on"))
        line = str(status.get("toggle_line") or "")
        self.assertIn("chrome://inspect", line)
        self.assertIn("off", line.lower())


# ------------------------------------------------- #4 settings card
@unittest.skipUnless(_QT, "Qt-only: the Settings card needs PySide6")
class TestSettingsToggleLine(unittest.TestCase):
    """The Settings browser card names the toggle in plain words
    while it is on, and stays quiet when it is off (Qt only).

    The refresh reads the toggle through the voice_assistant.*
    module identity (the GUI's import path), which is a DIFFERENT
    module object from the flat core.* one the other legs patch —
    the dual-module-identity trap. So these legs patch BOTH
    identities for every seam they stub.
    """

    def setUp(self):
        super().setUp()
        self.addCleanup(self._clean)
        self.mixin = BrainServicesMixin()
        self.mixin.browser_enable_status_label = _QLabel()

    def _clean(self):
        flag = _data_path(_FLAG)
        try:
            flag.unlink()
        except OSError:
            pass

    def _patch_both(self, name, value):
        """Patch a browser_enable seam on both module identities
        and register the stops as cleanups."""
        import voice_assistant.core.desktop.browser_enable as va_be
        patches = [mock.patch.object(be_mod, name, value),
                   mock.patch.object(va_be, name, value)]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_card_names_toggle_while_on(self):
        self.assertTrue(toggle_mod.note_toggle_attach(9333))
        self._patch_both("_cdp_port_answers", lambda port: True)
        self._patch_both("_read_devtools_active_port",
                         lambda profile: 9333)
        self._patch_both("_is_connected", lambda: False)
        self.mixin._refresh_browser_enable_status()
        text = self.mixin.browser_enable_status_label.text()
        self.assertIn("chrome://inspect", text)
        self.assertIn("off", text.lower())

    def test_card_quiet_when_toggle_off(self):
        self.assertFalse(toggle_mod.toggle_flag_set())
        self._patch_both("_is_connected", lambda: False)
        self.mixin._refresh_browser_enable_status()
        text = self.mixin.browser_enable_status_label.text()
        self.assertNotIn("chrome://inspect", text)


if __name__ == "__main__":
    unittest.main()
