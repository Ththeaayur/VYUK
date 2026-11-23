"""Loophole batch 8 — the privacy copy, and "your OK" (2026-11-22).

L-31  the privacy copy was false for any cloud turn. The wizard
    feature card said "With the built-in brain, your voice and
    data never leave this PC" — shown to cloud-brain users too,
    and "your data" was overbroad even for the local brain (mail
    and calendar live at Google). The sidebar and Settings
    promise said "Memory stays on this device", hiding that a
    cloud turn sends the facts she needs to answer to the cloud
    brain (the memory block rides _build_live_prompt and the
    cloud text turn's per-turn refresh). The Your-world card
    said "everything stays on this device" the moment the
    account is connected. The Memory page said "Stored only on
    this device" with no qualification, and the wizard name page
    said "She never shares it anywhere" — the name rides the
    memory block and the briefing tool result in a cloud turn.
    All six now read from core/privacy_copy.py (one wording,
    one place, through gui_facade) and say what leaves, where
    it goes, and when.
L-32  "only with your OK each time" (core/google_services_token
    _CONNECTED_OK) was answered by whoever was speaking. The
    connection itself already happens on screen — the OAuth flow
    opens the browser and you approve at Google — so no behaviour
    changes there; the verdict now names the approval route
    ("in the popup, or by voice in a session you have proved",
    the batch-4 lock's own vocabulary) instead of promising
    "your OK". The same route replaces "approve it in a popup"
    on the Your-world card and "only ever on your say-so" on the
    wizard Google-services page, via one shared fragment, so the
    three wordings cannot drift apart again.

Reviewed and KEPT (true as written): the sign-in token and the
API keys are stored only on this device; the ambient-light
picture is never stored or sent; continuous display watches
nothing (flag + choice only, no capture); keys and secrets are
never in an exported settings file; the Gemini sign-in message
carries no "your OK" promise; the Privacy Notice already says
cloud-turn words are handled by the brain's service.

Runs under both interpreters (sys + Qt). The sidebar, Memory
page, Settings promise, world card and wizard-page tests are
Qt-only; everything else is stdlib. Pure tests run against
temp dirs only — the live tree and live data/ are never
touched (the Qt legs reuse the WindowBase / _DialogMixin
harnesses, which restore the QSettings keys they touch).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    from PySide6.QtWidgets import QLabel as _QLabel  # noqa: E402
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QT = False

_APP = None
_VA_SNAPSHOT = {}
SettingsGroupsMixin = None
BrainServicesMixin = None
# voice_assistant-identity modules, bound in setUpModule.
pc = None        # core.privacy_copy
gst = None       # core.google_services_token
goauth = None    # core.gemini_oauth
cfg_mod = None   # core.config
cs = None        # core.config_spec
mem_mod = None   # core.memory.memory
va_config = None
config_mod = None


def setUpModule():
    """Synthesise the voice_assistant.* alias (the
    test_settings_redesign / batch-7 shape) and import the
    batch-8 modules under it, so the config singleton the tests
    flip is the same one the code under test reads. The live
    data/ dir is never touched: pure tests use temp dirs; the
    Qt legs restore the QSettings keys they touch."""
    global _APP, _VA_SNAPSHOT, SettingsGroupsMixin, BrainServicesMixin
    global pc, gst, goauth, cfg_mod, cs, mem_mod, va_config, config_mod
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    from voice_assistant.core import privacy_copy as _pc  # noqa: E402
    from voice_assistant.core import (  # noqa: E402
        google_services_token as _gst)
    from voice_assistant.core import gemini_oauth as _go  # noqa: E402
    from voice_assistant.core import config as _cfg  # noqa: E402
    from voice_assistant.core import config_spec as _cs  # noqa: E402
    from voice_assistant.core.memory import memory as _mm  # noqa: E402
    import core.config as _flat_cfg  # noqa: E402
    pc, gst, goauth = _pc, _gst, _go
    cfg_mod, cs, mem_mod = _cfg, _cs, _mm
    va_config, config_mod = _cfg, _flat_cfg
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    try:
        from gui.window_settings_groups import (  # noqa: E402
            SettingsGroupsMixin as _SGM)
        SettingsGroupsMixin = _SGM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        SettingsGroupsMixin = None
    try:
        from gui.window_brain_services import (  # noqa: E402
            BrainServicesMixin as _BSM)
        BrainServicesMixin = _BSM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        BrainServicesMixin = None


def tearDownModule():
    """Restore the voice_assistant.* modules."""
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    for key, mod in _VA_SNAPSHOT.items():
        sys.modules[key] = mod


# ---------------------------------------------------------------- helpers
def _tree_text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class PrivacyCopyTests(unittest.TestCase):
    """L-31 + L-32: the single source of truth is clean."""

    def test_constants_exist_and_carry_no_banned_phrase(self):
        need = ("APPROVAL_ROUTE", "CONNECTED_OK", "FEATURE_CARD_PRIVATE",
                "PRIVACY_PROMISE", "WORLD_CARD", "MEMORY_PAGE_SUBTITLE",
                "NAME_PAGE_SUBTITLE", "GOOGLE_SERVICES_SUBTITLE")
        for key in need:
            self.assertTrue(hasattr(pc, key), f"missing {key}")
            value = getattr(pc, key)
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip(), f"{key} is empty")
            for banned in pc.BANNED_PHRASES:
                self.assertNotIn(banned, value,
                                 f"{key} carries {banned!r}")

    def test_banned_phrases_are_the_five_removed_claims(self):
        self.assertEqual(set(pc.BANNED_PHRASES), {
            "your voice and data never leave this PC",
            "never shares it anywhere",
            "everything stays on this device",
            "only with your OK each time",
            "only ever on your say-so",
        })

    def test_approval_route_is_one_shared_fragment(self):
        for holder in (pc.CONNECTED_OK, pc.WORLD_CARD,
                       pc.GOOGLE_SERVICES_SUBTITLE):
            self.assertIn(pc.APPROVAL_ROUTE, holder)

    def test_feature_card_is_true_for_every_brain(self):
        body = pc.FEATURE_CARD_PRIVATE
        self.assertIn("local brain", body)
        self.assertIn("cloud brain", body)
        self.assertNotIn("never leave", body)

    def test_promise_names_what_leaves(self):
        self.assertIn("stored on this device", pc.PRIVACY_PROMISE)
        self.assertIn("cloud turn", pc.PRIVACY_PROMISE)
        self.assertIn("cloud brain", pc.PRIVACY_PROMISE)


class SurfaceSourceTests(unittest.TestCase):
    """L-31 + L-32: every surface reads the shared constant;
    no old literal survives in the tree."""

    @unittest.skipUnless(_QT, "Qt-only: onboarding_widgets imports PySide6")
    def test_wizard_card_reads_the_constant(self):
        from voice_assistant.gui.onboarding_widgets import _feature_cards
        cards = dict(_feature_cards())
        self.assertEqual(cards["Private by design"],
                         pc.FEATURE_CARD_PRIVATE)

    def test_sidebar_and_settings_read_the_promise(self):
        self.assertIn("PRIVACY_PROMISE",
                      _tree_text("gui/window_shell_sidebar.py"))
        self.assertIn("PRIVACY_PROMISE",
                      _tree_text("gui/window_settings_groups.py"))
        self.assertNotIn("Memory stays on this device. The mic is off",
                         _tree_text("gui/window_shell_sidebar.py"))
        self.assertNotIn("memory stays on this device. The mic is off",
                         _tree_text("gui/window_settings_groups.py"))

    def test_world_card_reads_the_constant(self):
        src = _tree_text("gui/window_brain_services.py")
        self.assertIn("WORLD_CARD", src)
        self.assertNotIn("everything stays on this device", src)
        self.assertNotIn("approve it in a popup", src)

    def test_memory_page_reads_the_constant(self):
        src = _tree_text("gui/window_pages_memory.py")
        self.assertIn("MEMORY_PAGE_SUBTITLE", src)
        self.assertNotIn('Stored only on this device."', src)

    def test_wizard_pages_read_the_constants(self):
        src = _tree_text("gui/onboarding_pages.py")
        self.assertIn("NAME_PAGE_SUBTITLE", src)
        self.assertIn("GOOGLE_SERVICES_SUBTITLE", src)
        self.assertNotIn("never shares it anywhere", src)
        self.assertNotIn("only ever on your say-so", src)

    def test_no_banned_phrase_anywhere_in_user_facing_code(self):
        hits = []
        for path in ROOT.rglob("*.py"):
            rel = str(path.relative_to(ROOT))
            if rel.startswith("tests/"):
                continue
            if rel == "core/privacy_copy.py":
                continue
            if "__pycache__" in rel or "/data/" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for phrase in pc.BANNED_PHRASES:
                if phrase in text:
                    hits.append(f"{rel}: {phrase!r}")
        self.assertEqual(hits, [], f"banned phrases survive: {hits}")


class ConnectedOkTests(unittest.TestCase):
    """L-32: the connect verdict names the approval route."""

    def test_connected_ok_is_the_shared_sentence(self):
        self.assertEqual(gst._CONNECTED_OK, pc.CONNECTED_OK)
        self.assertNotIn("only with your OK each time", gst._CONNECTED_OK)
        self.assertIn(pc.APPROVAL_ROUTE, gst._CONNECTED_OK)

    def test_gemini_signin_message_carries_no_ok_promise(self):
        self.assertNotIn("your OK", goauth._CONNECTED_OK)
        self.assertNotIn("only with", goauth._CONNECTED_OK)

    def test_facade_reaches_privacy_copy(self):
        from voice_assistant.core import gui_facade
        self.assertIn("privacy_copy", gui_facade.__all__)
        self.assertIs(gui_facade.privacy_copy, pc)


class CloudFactTests(unittest.TestCase):
    """L-31: the code fact the copy states — memory facts ride
    the cloud prompt."""

    def test_a_remembered_fact_becomes_prompt_text(self):
        with tempfile.TemporaryDirectory() as td:
            store = mem_mod.MemoryStore(
                db_path=os.path.join(td, "m.db"))
            store.save_fact("general", "batch8_probe_key",
                            "batch8_probe_value_4711")
            block = store.get_memory_context_prompt()
        self.assertIn("batch8_probe_value_4711", block or "")

    def test_both_cloud_paths_call_the_memory_prompt(self):
        for rel in ("core/voice/live_client_lifecycle.py",
                    "core/voice/cloud_text_lifecycle.py"):
            self.assertIn("get_memory_context_prompt", _tree_text(rel),
                          f"{rel} no longer feeds memory to the cloud")


class TrueClaimsTests(unittest.TestCase):
    """L-31: the reviewed-true claims are kept verbatim."""

    def test_signin_and_keys_stay_local(self):
        self.assertIn("is stored only on this device, never anywhere else",
                      _tree_text("gui/onboarding_pages.py"))
        self.assertIn("it stays only on this device",
                      _tree_text("gui/connect_dialog.py"))

    def test_ambient_picture_never_stored_or_sent(self):
        self.assertIn("the picture itself is never stored or sent anywhere",
                      _tree_text("gui/window_settings_groups.py"))

    def test_continuous_display_watches_nothing(self):
        self.assertIn("was watched, captured, or started",
                      _tree_text("core/firstopen_tools.py"))

    def test_exported_settings_exclude_secrets(self):
        self.assertIn("those never leave this PC in a file",
                      _tree_text("gui/window_shell_settingsio.py"))


class CountsTests(unittest.TestCase):
    """Guards: a copy-only batch moves no census, no ConfigSpec."""

    def test_census_unmoved(self):
        ev = json.loads((ROOT / "tests/_classification_evidence.json")
                        .read_text(encoding="utf-8"))
        snap = json.loads((ROOT / "tests/_classification_snapshot.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(ev), 769)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(len(snap), 769)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(set(ev), set(snap))

    def test_configspec_counts_unmoved(self):
        import dataclasses as _dc
        fields = _dc.fields(cs.ConfigSpec)
        env_backed = [f for f in fields if "env" in f.metadata]
        # Stage 0 features batch 5: 111 -> 112 fields, 106 -> 107
        # env-backed (reply_verbosity, VYUK_REPLY_VERBOSITY).
        # Stage 0 features batch 6: 112 -> 113 fields, 107 -> 108
        # env-backed (calendar_quiet_enabled,
        # VYUK_CALENDAR_QUIET_ENABLED).
        self.assertEqual(len(fields), 113)
        self.assertEqual(len(env_backed), 108)


# ------------------------------------------------- Qt: window surfaces
@unittest.skipUnless(_QT, "Qt-only: the window needs PySide6")
class WindowPrivacyTests(unittest.TestCase):
    """L-31 (Qt): the sidebar privacy body and the Memory page
    subtitle render the shared sentences in the real window."""

    def test_sidebar_body_and_memory_subtitle(self):
        from tests.test_chat_ui import WindowBase

        case = WindowBase("setUp")
        case.setUp()
        try:
            body = case.window.findChild(_QLabel, "privacyBody")
            self.assertIsNotNone(body, "no sidebar privacyBody label")
            self.assertEqual(body.text(), pc.PRIVACY_PROMISE)
            page = case.window._build_memory_page()
            self.addCleanup(page.deleteLater)
            texts = [lbl.text() for lbl in page.findChildren(_QLabel)]
            self.assertIn(pc.MEMORY_PAGE_SUBTITLE, texts)
        finally:
            case.tearDown()


@unittest.skipUnless(_QT, "Qt-only: the Settings mixins need PySide6")
class SettingsPrivacyTests(unittest.TestCase):
    """L-31 + L-32 (Qt): the Settings privacy promise and the
    Your-world card render the shared sentences."""

    def setUp(self):
        self._private = bool(
            getattr(va_config.config, "private_mode", False))
        va_config.config.private_mode = False
        config_mod.config.private_mode = False
        self.mixin = SettingsGroupsMixin()
        # The stub returns the layout it was handed, so the
        # labels the builder adds can be read back from it.
        self.mixin._settings_card = (
            lambda layout, _t, _s: layout)
        self.mixin._on_session_provenance_lock_toggled = (
            lambda *a: None)
        self.persisted = []
        self.mixin._persist_config_setting = (
            lambda k, v: self.persisted.append((k, v)))
        self.mixin.show_settings_card = lambda *a: None

    def tearDown(self):
        va_config.config.private_mode = self._private
        config_mod.config.private_mode = self._private
        super().tearDown()

    def _labels_in(self, layout):
        texts = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, _QLabel):
                texts.append(widget.text())
            sub = item.layout() if item is not None else None
            if sub is not None:
                texts.extend(self._labels_in(sub))
        return texts

    def test_privacy_promise_label(self):
        layout = _QVBoxLayout()
        self.mixin._build_privacy_cards(layout)
        texts = self._labels_in(layout)
        self.assertIn("Private by design — " + pc.PRIVACY_PROMISE, texts)

    def test_world_card_hint(self):
        captured = {}

        def _capture(_layout, title, hint=None):
            captured["title"] = title
            captured["hint"] = hint
            return _QVBoxLayout()

        mixin = BrainServicesMixin()
        mixin._settings_card = _capture
        mixin._build_world_card(_QVBoxLayout())
        self.assertEqual(captured.get("title"), "Your world")
        self.assertEqual(captured.get("hint"), pc.WORLD_CARD)


@unittest.skipUnless(_QT, "Qt-only: the wizard dialog needs PySide6")
class WizardCopyTests(unittest.TestCase):
    """L-31 + L-32 (Qt): the wizard name page and Google-services
    page render the shared subtitles."""

    def test_name_and_google_pages(self):
        import tests.test_r3wizard_expand as _wmod
        from tests.test_r3wizard_expand import _DialogMixin

        _wmod.setUpModule()

        class _DlgCase(_DialogMixin, unittest.TestCase):
            pass

        case = _DlgCase()
        dlg = case._make_dialog()
        try:
            name_page = dlg._page_name()
            texts = [lbl.text()
                     for lbl in name_page.findChildren(_QLabel)]
            self.assertIn(pc.NAME_PAGE_SUBTITLE, texts)
            google_page = dlg._page_google_services()
            texts = [lbl.text()
                     for lbl in google_page.findChildren(_QLabel)]
            self.assertIn(pc.GOOGLE_SERVICES_SUBTITLE, texts)
        finally:
            try:
                case.tearDown()
            except Exception:  # noqa: BLE001
                try:
                    dlg.close()
                except Exception:  # noqa: BLE001
                    pass


if __name__ == "__main__":
    unittest.main()
