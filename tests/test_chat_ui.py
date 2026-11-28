"""Chat UI build tests (2026-11-16): the transcript, not a form.

Covers the build per move, in the research doc's order:

- move 1 — the live reply streams in real time (StreamingReveal
  holds the trailing partial word; the lit word is the last whole
  word, soft accent only) and finalises through the segment
  pipeline; the window-level stream/stop/finalise path is in
  WindowStreamTests.
- move 2 — stop / retry / regenerate with the old version kept
  and flippable (ChatThread versions + WindowStreamTests).
- move 3 — structured replies: headings + numbered lists in
  _md_lite; the answer first on result cards (WindowCardTests).
- move 4 — edit forks the conversation; the old branch returns
  on flip back (ChatThread + WindowEditTests).
- move 5 — spoken marking + dictation staging (WindowVoiceTests).
- move 6 — the composer: draft persists, type-while-speaking
  queues (WindowComposerTests).
- move 7 — quote / reply (ChatThread + WindowEditTests).
- move 8 — find searches the store too (MemoryTests +
  WindowFindTests).
- move 9 — history: ids on recent rows, windows around a match,
  the last-seen hairline (MemoryTests + WindowHistoryTests).
- move 10 — the new-messages pill counts (WindowPillTests).
- move 11 — the hover row is Copy / Regenerate / Edit, the rest
  behind one overflow; pin + delete + undo (BubbleTests +
  WindowActionTests).
- move 13 — the voice-state line (WindowVoiceTests).
- move 14 — the empty chat teaches with exactly three starters
  (WindowComposerTests).
- move 15 — the quiet rule: chips do not rebuild mid-stream
  (WindowStreamTests).

Plus the long-chat prototype (PrototypeTests): a 50+ message
thread, headless and on a real offscreen window — the deliverable
the sci-fi work judges bubbles vs transcript on.

Harness: headless tests run on both interpreters; Qt tests are
skipped without PySide6. Window tests build the REAL MainWindow
offscreen (the test_journey_gui_path shim: singletons stubbed,
QSettings onboarded, animation timers stopped) with the GUI's
memory_store patched to a temp store, so no test touches the
live data/ — and DataGuard still snapshots/restores it.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QT = False

from core.chat_thread import (  # noqa: E402
    ChatThread,
    StreamingReveal,
    lit_word_html,
)
from core.memory.memory import MemoryStore  # noqa: E402
from gui.window_widgets_helpers import (  # noqa: E402
    _md_lite,
    strip_markdown_plain,
)

ACCENT = "#35D6FF"


# ------------------------------------------------------------------ model
class ModelTests(unittest.TestCase):
    """The conversation is a tree, not a flat list (moves 2/4/7)."""

    def test_linear_add_and_visible_thread(self):
        t = ChatThread()
        u = t.add_user("hello")
        a = t.add_assistant("hi there")
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "hello"), ("assistant", "hi there")])
        self.assertEqual(t.leaf().id, a.id)
        self.assertEqual(u.parent_id, 0)
        self.assertEqual(a.parent_id, u.id)

    def test_regenerate_keeps_old_version_and_flips(self):
        t = ChatThread()
        t.add_user("q")
        a = t.add_assistant("first answer")
        t.add_version(a.id, "second answer")
        self.assertEqual(a.version_count, 2)
        self.assertEqual(a.branch_label(), "2 of 2")
        self.assertEqual(t.visible_thread()[-1].text, "second answer")
        self.assertTrue(t.set_active_version(a.id, 0))
        self.assertEqual(a.branch_label(), "1 of 2")
        self.assertEqual(t.visible_thread()[-1].text, "first answer")

    def test_edit_forks_and_old_branch_returns(self):
        t = ChatThread()
        u = t.add_user("What is 2+2?")
        t.add_assistant("4")
        t.edit_user(u.id, "What is 3+3?")
        # The fork: the new branch holds the edited question and
        # no answer yet; the old answer is not deleted.
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "What is 3+3?")])
        t.add_assistant("6")
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "What is 3+3?"), ("assistant", "6")])
        # Flip back to the old branch: the old question + answer.
        self.assertTrue(t.set_active_version(u.id, 0))
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "What is 2+2?"), ("assistant", "4")])

    def test_edit_unchanged_is_a_noop(self):
        # The unchanged guard lives in the GUI (_commit_edit
        # compares before calling edit_user); the model forks
        # on any edit_user call, changed or not.
        t = ChatThread()
        u = t.add_user("same")
        t.add_assistant("a")
        self.assertTrue(t.edit_user(u.id, "same"))
        self.assertEqual(u.version_count, 2)

    def test_delete_and_undo_restore(self):
        t = ChatThread()
        u = t.add_user("q")
        a = t.add_assistant("a")
        t.delete_message(a.id)
        self.assertEqual(
            [m.role for m in t.visible_thread()], ["user"])
        self.assertTrue(t.restore_message(a.id))
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "q"), ("assistant", "a")])
        # Deleting a user message hides its subtree.
        t.delete_message(u.id)
        self.assertEqual(t.visible_thread(), [])

    def test_pin_and_quote(self):
        t = ChatThread()
        u = t.add_user("pin me")
        a = t.add_assistant("quoted answer")
        t.set_pinned(a.id, True)
        self.assertEqual([m.id for m in t.pinned_messages()], [a.id])
        t.set_pinned(a.id, False)
        self.assertEqual(t.pinned_messages(), [])
        t.set_quote(a.id, u.id, "pin me")
        node = t.get(a.id)
        self.assertEqual(node.quote_id, u.id)
        self.assertEqual(node.quote_text, "pin me")

    def test_make_visible_steers_to_a_branched_node(self):
        t = ChatThread()
        u = t.add_user("v1")
        t.add_assistant("a1")
        t.edit_user(u.id, "v2")
        a2 = t.add_assistant("a2")
        self.assertTrue(t.set_active_version(u.id, 0))
        self.assertEqual(t.visible_thread()[-1].text, "a1")
        self.assertTrue(t.make_visible(a2.id))
        self.assertEqual(
            [(m.role, m.text) for m in t.visible_thread()],
            [("user", "v2"), ("assistant", "a2")])
        self.assertFalse(t.make_visible(999999))

    def test_store_id_card_and_system_nodes(self):
        t = ChatThread()
        u = t.add_user("q", store_id=41)
        self.assertEqual(u.store_id, 41)
        c = t.add_card("Files", "body", answer="Done — 3 files")
        self.assertEqual(c.kind, "card")
        self.assertEqual(c.card_answer, "Done — 3 files")
        s = t.add_system("Stopped.")
        self.assertEqual(s.kind, "system")
        self.assertEqual(len(t.visible_thread()), 3)
        t.clear()
        self.assertEqual(t.visible_thread(), [])

    def test_set_text_updates_the_active_version(self):
        t = ChatThread()
        a = t.add_assistant("")
        self.assertTrue(t.set_text(a.id, "streamed in full"))
        self.assertEqual(a.text, "streamed in full")
        self.assertFalse(t.set_text(999999, "nope"))

    def test_set_status_and_spoken(self):
        t = ChatThread()
        a = t.add_assistant("partial")
        t.set_status(a.id, "stopped")
        self.assertEqual(a.status, "stopped")
        t.set_spoken(a.id, True)
        self.assertTrue(a.spoken)


# ------------------------------------------------------------------ reveal
class RevealTests(unittest.TestCase):
    """Streaming reveals whole words only (move 1): the trailing
    partial word is held until the next chunk completes it, so the
    eye never reads a half-word."""

    def test_partial_word_held_until_completed(self):
        r = StreamingReveal()
        self.assertEqual(r.feed("hel"), "")
        self.assertEqual(r.revealed, "")
        self.assertEqual(r.pending, "hel")
        self.assertEqual(r.feed("lo world"), "hello ")
        self.assertEqual(r.revealed, "hello")
        self.assertEqual(r.pending, "world")
        self.assertEqual(r.feed("!"), "")
        self.assertEqual(r.revealed, "hello")
        self.assertEqual(r.finalize(), "hello world!")

    def test_finalize_flushes_the_pending_word(self):
        r = StreamingReveal()
        r.feed("one two")
        self.assertEqual(r.finalize(), "one two")

    def test_empty_chunks_change_nothing(self):
        r = StreamingReveal()
        self.assertEqual(r.feed(""), "")
        r.feed("word ")
        self.assertEqual(r.feed(""), "")
        self.assertEqual(r.finalize(), "word ")

    def test_full_accumulates_everything_fed(self):
        r = StreamingReveal()
        r.feed("a b ")
        r.feed("c")
        self.assertEqual(r.full, "a b c")


class LitWordTests(unittest.TestCase):
    """The lit word is a soft accent on the current word only —
    never a full highlight wash (parent ruling, move 1)."""

    def test_last_word_lit_and_only_it(self):
        got = lit_word_html("hello world", ACCENT)
        self.assertEqual(
            got, 'hello <span style="color:#35D6FF">world</span>')
        self.assertEqual(got.count("<span"), 1)

    def test_single_word_is_lit_whole(self):
        self.assertEqual(
            lit_word_html("hello", ACCENT),
            '<span style="color:#35D6FF">hello</span>')

    def test_empty_text_stays_empty(self):
        self.assertEqual(lit_word_html("", ACCENT), "")

    def test_html_is_escaped_before_lighting(self):
        got = lit_word_html("a <b> c", ACCENT)
        self.assertIn("&lt;b&gt;", got)
        self.assertNotIn("<b>", got)


# ------------------------------------------------------------------ markdown
class MarkdownTests(unittest.TestCase):
    """Structured replies (move 3): headings + numbered lists."""

    def test_headings_render_bold_on_their_own_line(self):
        got = _md_lite("# Big\n## Small\n### Tiny")
        self.assertIn("<b>Big</b><br/>", got)
        self.assertIn("<b>Small</b><br/>", got)
        self.assertIn("<b>Tiny</b>", got)

    def test_numbered_lists_keep_their_numbers(self):
        got = _md_lite("1. first\n2. second\n10. tenth")
        self.assertIn("1. first<br/>", got)
        self.assertIn("2. second<br/>", got)
        self.assertIn("10. tenth", got)

    def test_bold_italic_code_and_bullets_still_work(self):
        got = _md_lite("**bold** *it* `code`\n- one")
        self.assertIn("<b>bold</b>", got)
        self.assertIn("<i>it</i>", got)
        # Inline code renders as a styled span, not <tt>.
        self.assertIn(">code</span>", got)
        self.assertIn("• one", got)

    def test_strip_markdown_plain(self):
        self.assertEqual(
            strip_markdown_plain("**bold** and *it* and `code`"),
            "bold and it and code")
        self.assertEqual(
            strip_markdown_plain("# Head\n- one\n• two"),
            "Head\n• one\n• two")
        # A literal asterisk that is not a marker survives.
        self.assertEqual(
            strip_markdown_plain("2 * 3 = 6"), "2 * 3 = 6")


# ------------------------------------------------------------------ memory
class MemoryTests(unittest.TestCase):
    """Find that finds + history (moves 8/9): the store searches
    and windows, and recent rows carry their id."""

    def setUp(self):
        self._tmp = pathlib.Path(
            tempfile.mkdtemp(prefix="chat_ui_mem_"))
        self.store = MemoryStore(self._tmp / "assistant.db")
        for i in range(5):
            self.store.log_message("user", f"question {i} about python")
            self.store.log_message("assistant", f"answer {i}")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_recent_history_carries_ids(self):
        rows = self.store.get_recent_history(3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("id" in r for r in rows))
        self.assertEqual([r["id"] for r in rows], [8, 9, 10])

    def test_search_is_case_insensitive_and_newest_first(self):
        hits = self.store.search_history("python")
        self.assertEqual(len(hits), 5)
        self.assertEqual(len(self.store.search_history("PYTHON")), 5)
        self.assertTrue(hits[0]["id"] > hits[-1]["id"])

    def test_search_escapes_like_wildcards(self):
        self.assertEqual(self.store.search_history("%"), [])
        self.assertEqual(self.store.search_history("_"), [])
        self.assertEqual(self.store.search_history(""), [])
        self.assertEqual(
            len(self.store.search_history("question", limit=2)), 2)

    def test_history_around_returns_the_window_in_time_order(self):
        mid = self.store.get_recent_history(10)[3]["id"]
        rows = self.store.get_history_around(mid, 2, 2)
        self.assertEqual(len(rows), 5)
        self.assertEqual([r["id"] for r in rows],
                         [mid - 2, mid - 1, mid, mid + 1, mid + 2])
        self.assertEqual(self.store.get_history_around(999999), [])


# ------------------------------------------------------------------ guards
class GuardTests(unittest.TestCase):
    """The build adds no new colour literals, no modal dialogs,
    no Qt in the tree model — and the facade exports it."""

    def test_facade_exports_chat_thread(self):
        import core.gui_facade as fac
        self.assertIsNotNone(fac.chat_thread)
        self.assertIn("chat_thread", fac.__all__)
        self.assertIs(fac.chat_thread.ChatThread, ChatThread)

    def test_no_new_colour_literals_in_new_gui_files(self):
        for name in ("window_chat_stream.py", "window_chat_thread.py",
                     "window_chat_conversations.py"):
            path = ROOT / "gui" / name
            text = path.read_text(encoding="utf-8")
            hits = re.findall(r"#[0-9A-Fa-f]{6}\b", text)
            self.assertEqual(hits, [], f"{name}: {hits}")

    def test_no_modal_dialogs_in_new_gui_files(self):
        for name in ("window_chat_stream.py", "window_chat_thread.py",
                     "window_chat_conversations.py"):
            text = (ROOT / "gui" / name).read_text(encoding="utf-8")
            self.assertNotIn("QMessageBox", text, name)
            self.assertNotIn("QInputDialog", text, name)

    def test_tree_model_is_qt_free(self):
        text = (ROOT / "core" / "chat_thread.py").read_text(
            encoding="utf-8")
        self.assertNotIn("PySide6", text)
        self.assertNotIn("QWidget", text)


# ------------------------------------------------------------------ Qt: bubble
@unittest.skipUnless(_QT, "Qt unavailable")
class BubbleTests(unittest.TestCase):
    """One message, on its own (moves 1/2/11): streaming, the
    stopped/failed foot rows, versions, dimming, the hover row."""

    @classmethod
    def setUpClass(cls):
        cls._app = _QA.instance() or _QA([])

    def test_spoken_label_on_the_sender_line(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        spoken = ChatBubble("You", "hello", True, spoken=True)
        self.assertIn("spoken", spoken.who_lbl.text())
        plain = ChatBubble("You", "hello", True, spoken=False)
        self.assertNotIn("spoken", plain.who_lbl.text())
        parent.deleteLater()

    def test_set_text_rebuilds_the_body_in_place(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "first", False)
        b.set_text("second **bold** version")
        self.assertEqual(b._plain_text, "second **bold** version")
        parent.deleteLater()

    def test_streaming_lifecycle(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "", False)
        b.start_streaming()
        self.assertIsNotNone(b._stream_lbl)
        b.update_stream("hello <span>world</span>", "hello world")
        self.assertEqual(b._plain_text, "hello world")
        b.finish_stream("hello world!")
        self.assertEqual(b._plain_text, "hello world!")
        self.assertIsNone(b._stream_lbl)
        parent.deleteLater()

    def test_stopped_and_failed_status_rows(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "partial words", False)
        # A top-level bubble reports its children visible only
        # once it is shown (offscreen is fine).
        b.show()
        b.set_status("stopped", "Stopped — kept what was said",
                     show_retry=True)
        self.assertTrue(b._status_row.isVisible())
        self.assertIn("Stopped", b._status_lbl.text())
        self.assertIsNotNone(b._status_retry)
        self.assertTrue(b._status_retry.isVisible())
        b.set_status("failed", "That reply didn't arrive.",
                     show_retry=True)
        self.assertIn("didn't arrive", b._status_lbl.text())
        self.assertTrue(b._status_retry.isVisible())
        # A failed bubble with no text hides its empty body —
        # the row is the bubble.
        empty = ChatBubble("VYUK", "", False)
        empty.show()
        empty.set_status("failed", "That reply didn't arrive.",
                         show_retry=True)
        self.assertTrue(empty._body_widgets)
        self.assertTrue(all(not w.isVisible()
                            for w in empty._body_widgets))
        parent.deleteLater()

    def test_version_row_shows_the_flip_control(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "v2", False)
        b.show()
        b.set_versions(2, 1, note="Edited — new answer")
        self.assertTrue(b._version_row.isVisible())
        self.assertEqual(b._version_lbl.text(), "2 of 2")
        self.assertIn("Edited", b._version_note.text())
        b.set_versions(1, 0)
        self.assertFalse(b._version_row.isVisible())
        parent.deleteLater()

    def test_dimmed_bubble_fades_to_sixty_percent(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "yesterday", False)
        b.set_dimmed(True)
        self.assertTrue(b._chat_dimmed)
        b.set_dimmed(False)
        self.assertFalse(b._chat_dimmed)
        parent.deleteLater()

    def test_hover_row_is_copy_regenerate_edit(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        # The three earned actions, split by owner: VYUK's
        # bubble regenerates, the user's edits. Everything else
        # lives behind the one overflow.
        assistant = ChatBubble("VYUK", "answer", False)
        texts = [btn.text() for btn in assistant._action_buttons]
        self.assertEqual(texts, ["Copy", "Regenerate", "⋯"])
        user = ChatBubble("You", "question", True)
        texts = [btn.text() for btn in user._action_buttons]
        self.assertEqual(texts, ["Copy", "Edit", "⋯"])
        parent.deleteLater()

    def test_quote_anchor_and_pin_state(self):
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets_bubble import ChatBubble
        parent = QWidget()
        b = ChatBubble("VYUK", "answer", False)
        b.set_quote("What is 2+2?")
        self.assertIsNotNone(b._quote_btn)
        self.assertIn("What is 2+2?", b._quote_btn.text())
        b.set_pinned_state(True)
        self.assertTrue(b._pinned)
        parent.deleteLater()


# ------------------------------------------------------- Qt: window harness
class _Fake:
    """Permissive stand-in (test_journey_gui_path): any attribute
    is another _Fake, any call returns one."""

    is_playing = False
    speaker_level = 0.0
    mic_level = 0.0
    is_muted = True
    spectrum_bands = [0.0] * 16
    is_connected = False
    client = None
    enabled = False

    def __call__(self, *a, **k):
        return _Fake()

    def __getattr__(self, _name):
        return _Fake()

    def start(self, *a, **k):
        pass

    def stop(self, *a, **k):
        pass

    def get_devices(self, *a, **k):
        return {"inputs": [], "outputs": []}

    def get_all_facts(self, *a, **k):
        return []

    def search_facts(self, *a, **k):
        return []

    def get_all_reminders(self, *a, **k):
        return []


class _AudioFake(_Fake):
    """Audio stand-in with a REAL telemetry shape, so the tick
    stays silent (test_journey_gui_path)."""

    def display_bands(self, _playing):
        return [0.0] * 16


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    sys.modules[name] = mod


@unittest.skipUnless(_QT, "Qt unavailable")
class WindowBase(unittest.TestCase):
    """The REAL MainWindow offscreen, singletons stubbed, the
    GUI's memory_store pointed at a temp store — so the window
    opens on an empty conversation and no test touches live
    data/ (DataGuard still snapshots/restores it)."""

    def setUp(self):
        try:
            self.app = _QA.instance() or _QA([])
        except Exception as exc:  # noqa: BLE001 — no Qt platform
            raise unittest.SkipTest(f"QApplication unavailable: {exc}")
        from tests._journey_base import DataGuard
        self._guard = DataGuard()
        self._guard.__enter__()
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="chat_ui_win_"))
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self._tmp)
        self._shimmed = []
        try:
            self._install_shim()
        except Exception:
            self._teardown_shim()
            raise
        # The window opens on the chat page with an empty
        # conversation (the temp store has no rows).
        self.window = self._build_window()
        self._window = self.window
        # The composer's draft persists in QSettings, whose file
        # path Qt caches per process — the per-test XDG_CONFIG_HOME
        # above does NOT isolate it, so a draft staged by one test
        # is restored into the next test's window when the chat page
        # is shown (parent runs of fixes #14/#15: the dictation
        # "yes" test failed twice on the previous test's
        # "stop dictating the letter"). Clear it here so every
        # test starts with an empty composer; tearDown clears it
        # again so no test pollutes the next.
        try:
            self.window.settings.remove("chat/composer_draft")
            self.window.settings.sync()
            self.window.input_cmd.setText("")
        except Exception:  # noqa: BLE001 — isolation is best-effort
            pass

    def _install_shim(self):
        import importlib
        tree = str(ROOT)
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [tree]
        sys.modules["voice_assistant"] = pkg
        core_pkg = types.ModuleType("voice_assistant.core")
        core_pkg.__path__ = [os.path.join(tree, "core")]
        sys.modules["voice_assistant.core"] = core_pkg
        real_config = importlib.import_module(
            "voice_assistant.core.config")
        sys.modules["voice_assistant.core.config"] = real_config
        _stub("voice_assistant.core.voice.audio",
              audio_manager=_AudioFake())
        _stub("voice_assistant.core.desktop.clipboard_monitor",
              clipboard_monitor=_Fake())
        _stub("voice_assistant.core.desktop.hotkeys",
              hotkey_daemon=_Fake())
        _stub("voice_assistant.core.live_client",
              live_session=_Fake())
        _stub("voice_assistant.core.memory.memory",
              memory_store=_Fake())
        _stub("voice_assistant.core.plugin_loader",
              discover_plugins=lambda *a, **k: [])
        _stub("voice_assistant.core.scheduler", scheduler=_Fake())
        # D-03 (UI Phase 6): enrich the two stubs with the real
        # modules' public names (bare core.* path) — the P2WindowBase
        # pattern (tests/test_ui_p2.py), moved to the base so every
        # WindowBase suite stops printing the three boot import
        # warnings. The base stubs exposed only memory_store /
        # scheduler, so the tools handlers could not import
        # as_user_note (Skills catalogue unavailable), the facade
        # could not resolve migrate_legacy_briefing_records
        # (briefing migration skipped), and the memory package
        # __init__ could not re-export MemoryStore (retention tick
        # failed) — all three are the stub, not the product. The
        # fakes stay for the isolated names (memory_store, scheduler);
        # the plugin_loader stub is popped so the real loader runs
        # (the 2 plugin tools are part of the 772 census).
        from core.memory import memory as _real_mem
        _mem_stub = sys.modules["voice_assistant.core.memory.memory"]
        for _name in dir(_real_mem):
            if _name.startswith("_") or _name == "memory_store":
                continue
            setattr(_mem_stub, _name, getattr(_real_mem, _name))
        from core import scheduler as _real_sched
        _sched_stub = sys.modules["voice_assistant.core.scheduler"]
        for _name in dir(_real_sched):
            if _name == "scheduler":
                continue
            setattr(_sched_stub, _name, getattr(_real_sched, _name))
        sys.modules.pop("voice_assistant.core.plugin_loader", None)
        _stub("voice_assistant.core.memory.session_summary",
              generate_session_summary=lambda *a, **k: None,
              save_session_summary=lambda *a, **k: None)
        _stub("voice_assistant.core.desktop.vision",
              vision_manager=_Fake())
        _stub("voice_assistant.core.voice.wake_word",
              wake_word_detector=_Fake())
        gui_pkg = types.ModuleType("voice_assistant.gui")
        gui_pkg.__path__ = [os.path.join(tree, "gui")]
        sys.modules["voice_assistant.gui"] = gui_pkg
        self._shimmed = [
            "voice_assistant", "voice_assistant.core",
            "voice_assistant.core.config",
            "voice_assistant.core.voice.audio",
            "voice_assistant.core.desktop.clipboard_monitor",
            "voice_assistant.core.desktop.hotkeys",
            "voice_assistant.core.live_client",
            "voice_assistant.core.memory.memory",
            "voice_assistant.core.plugin_loader",
            "voice_assistant.core.scheduler",
            "voice_assistant.core.memory.session_summary",
            "voice_assistant.core.desktop.vision",
            "voice_assistant.core.voice.wake_word",
            "voice_assistant.gui",
        ]
        # The chat GUI reads the store through the facade, not
        # through the stubbed module — point the three chat
        # modules at a real temp store before the window is
        # built, so restore/find/history run against it.
        self._store = MemoryStore(self._tmp / "assistant.db")
        import voice_assistant.gui.window_chat_bubbles as _bub
        import voice_assistant.gui.window_chat_history as _hist
        import voice_assistant.gui.window_chat_thread as _thr
        _bub.memory_store = self._store
        _hist.memory_store = self._store
        _thr.memory_store = self._store

    def _teardown_shim(self):
        for name in getattr(self, "_shimmed", []):
            sys.modules.pop(name, None)
        for name in [m for m in list(sys.modules)
                     if m.startswith("voice_assistant.gui.main_window")]:
            sys.modules.pop(name, None)

    def _build_window(self):
        import importlib
        from PySide6.QtCore import QSettings as _QSettings
        _QSettings("NovaAI", "Nova").setValue("onboarded", True)
        mw = importlib.import_module("voice_assistant.gui.main_window")
        window = mw.MainWindow()
        _audio_timer = getattr(window, "audio_timer", None)
        if _audio_timer is not None:
            _audio_timer.stop()
        _mini = getattr(window, "mini_orb", None)
        if _mini is not None:
            _mini_timer = getattr(_mini, "_timer", None)
            if _mini_timer is not None:
                _mini_timer.stop()
        window.show()
        self.app.processEvents()
        return window

    def tearDown(self):
        window = getattr(self, "_window", None)
        if window is not None:
            try:
                window.close()
            except Exception:  # noqa: BLE001
                pass
            self.app.processEvents()
            # See setUp: the composer draft lives in QSettings,
            # whose path Qt caches per process, so clear it here
            # (after the close pump, so a pending draft-save timer
            # has already fired) or the next test's window restores
            # this test's staged words.
            try:
                window.settings.remove("chat/composer_draft")
                window.settings.sync()
            except Exception:  # noqa: BLE001 — isolation is best-effort
                pass
        self._disconnect_fake_live()
        self._teardown_shim()
        # D-01 (Phase 6): the state bus is a process-wide singleton
        # and the window's strips/rails subscribe to it — reset it
        # here so a dead window's subscriptions cannot leak into
        # the next test's window (the contract
        # core/state_bus.reset_state_bus documents). The window is
        # already closed and pumped above, so no in-flight drain
        # can be using the old bus.
        try:
            from voice_assistant.core.state_bus import reset_state_bus
            reset_state_bus()
        except Exception:  # noqa: BLE001 — isolation is best-effort
            pass
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg
        self._guard.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- helpers ------------------------------------------------------
    def _connect_fake_live(self):
        """Give the window a connected fake live session + a worker
        loop, so regenerate / retry / edit / queue resends take
        the connected path instead of the 'not connected'
        fallback. The stream and thread mixins read the
        module-global live_session (imported from the facade), so
        both modules are patched; the fake's send_text is a real
        coroutine returning the accepted shape."""
        import asyncio
        import threading
        loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        self._loop = loop
        self._loop_thread = thread

        async def _send_text(text):
            return {"status": "accepted"}

        fake = _Fake()
        fake.is_connected = True
        fake.send_text = _send_text
        fake.stop_reply = lambda: True
        import voice_assistant.gui.window_chat_stream as _stream
        import voice_assistant.gui.window_chat_thread as _thread
        self._orig_stream_live = _stream.live_session
        self._orig_thread_live = _thread.live_session
        _stream.live_session = fake
        _thread.live_session = fake
        self.window.worker = types.SimpleNamespace(loop=loop)
        return fake

    def _disconnect_fake_live(self):
        try:
            import voice_assistant.gui.window_chat_stream as _stream
            import voice_assistant.gui.window_chat_thread as _thread
            _stream.live_session = getattr(
                self, "_orig_stream_live", _stream.live_session)
            _thread.live_session = getattr(
                self, "_orig_thread_live", _thread.live_session)
        except Exception:  # noqa: BLE001
            pass
        loop = getattr(self, "_loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass

    def _bubbles(self):
        """ChatBubbles currently in the chat column, in order
        (the window's own walk — bubbles live inside per-row
        layouts, not as direct children)."""
        return list(self.window._chat_bubbles())

    def _thread_texts(self):
        return [(m.role, m.text)
                for m in self.window._chat_thread().visible_thread()]

    def _stream_reply(self, chunks, final):
        """Drive a live reply the way the bridge does: chunks in
        real order, then the final transcript."""
        for chunk in chunks:
            self.window._on_transcript_chunk("assistant", chunk)
            self.app.processEvents()
        self.window._on_transcript("assistant", final)
        self.app.processEvents()

    def _seed_exchange(self, question="What is 2+2?",
                       answer="4"):
        """One user question + one assistant answer, through the
        same paths a real turn takes."""
        self.window._add_bubble("You", question, True)
        self.app.processEvents()
        self.window._on_transcript("assistant", answer)
        self.app.processEvents()


# ------------------------------------------------------------------ Qt: stream
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowStreamTests(WindowBase):
    """The live reply, finished (moves 1/2/15)."""

    def test_chunks_stream_then_finalise_with_the_full_text(self):
        self._stream_reply(["Hel", "lo world", "!"], "Hello world!")
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 1)
        self.assertEqual(bubbles[0]._plain_text, "Hello world!")
        self.assertIsNone(bubbles[0]._stream_lbl)
        self.assertEqual(self._thread_texts(),
                         [("assistant", "Hello world!")])
        # The tree node holds the streamed text — not "".
        node = self.window._chat_thread().visible_thread()[-1]
        self.assertEqual(node.text, "Hello world!")
        self.assertEqual(node.status, "done")

    def test_new_bubbles_land_in_their_own_rows_not_on_top(self):
        """Hunt #1 (2026-11-16): a new bubble used to land at the
        panel's top-left, on top of the first message, because the
        slide base was captured before the layout positioned the
        widget. Two bubbles seeded like test_render_grabs must end
        in their own rows — divider above You above VYUK — once
        the entrance has finished."""
        import time as _time
        from PySide6.QtWidgets import QLabel
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the seeding works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()
        self.window._show_page(0)
        self.app.processEvents()
        self.window._add_bubble("You", "What's on today?", True)
        self.window._add_bubble(
            "VYUK", "Three things — standup at 09:00 is first.", False)
        # Past the 400 ms fade watchdog, pumping events so the
        # deferred entrance starts and finishes.
        deadline = _time.monotonic() + 0.8
        while _time.monotonic() < deadline:
            self.app.processEvents()
            _time.sleep(0.02)
        self.app.processEvents()
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 2)
        you, vyuk = bubbles
        divider = self.window.findChild(QLabel, "chatDivider")
        self.assertIsNotNone(divider, "no day divider")
        self.assertLess(divider.y(), you.y())
        self.assertLess(you.y(), vyuk.y())
        self.assertNotEqual((you.x(), you.y()), (vyuk.x(), vyuk.y()))

    def test_partial_word_is_held_while_streaming(self):
        self.window._on_transcript_chunk("assistant", "hel")
        self.app.processEvents()
        bubble = self._bubbles()[-1]
        # Nothing revealed yet: "hel" is a partial word.
        self.assertEqual(bubble._plain_text, "")
        self.window._on_transcript_chunk("assistant", "lo ")
        self.app.processEvents()
        self.assertEqual(bubble._plain_text, "hello")
        self.window._on_transcript("assistant", "hello")
        self.app.processEvents()
        self.assertEqual(bubble._plain_text, "hello")

    def test_send_button_becomes_stop_while_streaming(self):
        self.window._on_transcript_chunk("assistant", "Hel")
        self.app.processEvents()
        self.assertEqual(self.window.btn_send.text(), "■")
        self.assertEqual(
            self.window.btn_send.accessibleName(), "Stop reply")
        self.assertTrue(self.window.btn_send.isEnabled())
        self.window._on_transcript("assistant", "Hel")
        self.app.processEvents()
        self.assertEqual(self.window.btn_send.text(), "")
        self.assertEqual(
            self.window.btn_send.accessibleName(), "Send message")

    def test_stop_keeps_the_words_already_read(self):
        self.window._on_transcript_chunk("assistant", "hel")
        self.window._on_transcript_chunk("assistant", "lo world")
        self.app.processEvents()
        self.window._stop_live_reply()
        self.app.processEvents()
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 1)
        self.assertEqual(bubbles[0]._plain_text, "hello world")
        self.assertIn("Stopped", bubbles[0]._status_lbl.text())
        self.assertTrue(bubbles[0]._status_retry.isVisible())
        node = self.window._chat_thread().visible_thread()[-1]
        self.assertEqual(node.status, "stopped")
        self.assertEqual(node.text, "hello world")

    def test_planner_lines_never_append_to_a_live_bubble(self):
        self.window._on_transcript_chunk("assistant", "hel")
        self.window._on_transcript_chunk("assistant", "lo ")
        self.app.processEvents()
        self.window._on_transcript(
            "assistant", "Running goal: tidy files (budget: 5 steps)")
        self.app.processEvents()
        # The planner line gets its OWN bubble; the live bubble
        # still holds only the streamed words.
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 2)
        self.assertEqual(bubbles[0]._plain_text, "hello")
        self.assertEqual(
            bubbles[1]._plain_text,
            "Running goal: tidy files (budget: 5 steps)")
        self.window._on_transcript("assistant", "hello")
        self.app.processEvents()
        self.assertEqual(self._bubbles()[0]._plain_text, "hello")

    def test_quiet_rule_freezes_the_chip_row_mid_stream(self):
        # Before anything streams, the empty chat shows the three
        # starters (move 14).
        starters = [self.window._chip_layout.itemAt(i).widget().text()
                    for i in range(self.window._chip_layout.count())
                    if self.window._chip_layout.itemAt(i).widget()
                    is not None]
        self.assertEqual(starters, ["What can you do?",
                                    "Remind me in 10 min",
                                    "What's on my screen?"])
        # The first chunk lands a message — but while the reply
        # streams the chip row must NOT rebuild (move 15): the
        # first-message switch to follow-ups waits for the turn.
        self.window._on_transcript_chunk("assistant", "Hel")
        self.app.processEvents()
        self.window._refresh_chip_row()
        frozen = [self.window._chip_layout.itemAt(i).widget().text()
                  for i in range(self.window._chip_layout.count())
                  if self.window._chip_layout.itemAt(i).widget()
                  is not None]
        self.assertEqual(frozen, starters)
        self.window._on_transcript("assistant", "Hel")
        self.app.processEvents()
        # Streaming ended: the row rebuilds once, to follow-ups.
        chips = [self.window._chip_layout.itemAt(i).widget().text()
                 for i in range(self.window._chip_layout.count())
                 if self.window._chip_layout.itemAt(i).widget()
                 is not None]
        self.assertIn("What's on my screen?", chips)

    def test_regenerate_keeps_the_old_version_flippable(self):
        self._connect_fake_live()
        self._seed_exchange()
        assistant = self._bubbles()[-1]
        msg_id = assistant._chat_msg_id
        self.assertIsNotNone(msg_id)
        self.window._regenerate_message(msg_id)
        self.app.processEvents()
        self._stream_reply(["Fo", "ur"], "Four")
        node = self.window._chat_thread().get(msg_id)
        self.assertEqual(node.version_count, 2)
        self.assertEqual(node.branch_label(), "2 of 2")
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?"),
                          ("assistant", "Four")])
        # Flip back: the old answer is one tap away.
        self.window._flip_version(msg_id, -1)
        self.app.processEvents()
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?"),
                          ("assistant", "4")])

    def test_late_answer_after_stop_is_dropped(self):
        # Field-check fix #7 (U4 #29): stop a typed send in
        # flight, then let the late answer arrive — it must be
        # dropped, not land as its own bubble after "Stopped.".
        import asyncio
        from PySide6.QtTest import QTest
        self._seed_exchange()
        fake = self._connect_fake_live()
        import voice_assistant.gui.window_session_typed as _typed
        _orig_typed_live = _typed.live_session
        _typed.live_session = fake
        try:
            async def _slow_send(text):
                await asyncio.sleep(1.5)
                return {"status": "accepted"}

            fake.send_text = _slow_send
            self.window.input_cmd.setText("What is 3+3?")
            self.window._send_typed_message()
            self.app.processEvents()
            self.assertIsNotNone(
                getattr(self.window, "_typed_send_future", None))
            self.assertTrue(self.window._stop_live_reply())
            QTest.qWait(1800)
            self.app.processEvents()
            self.window._on_transcript("assistant", "6")
            self.app.processEvents()
            # The late answer never lands: the thread ends at
            # the "Stopped." system line, with no "6" bubble.
            texts = self._thread_texts()
            self.assertNotIn(("assistant", "6"), texts)
            self.assertIn(("", "Stopped."), texts)
        finally:
            _typed.live_session = _orig_typed_live

    def test_typed_send_failure_does_not_blame_regenerate_target(self):
        # Field-check fix #7 (U4 #30): a typed-send failure
        # while a regenerate resend is pending is the typed
        # send's own — a system line, never a failed marker
        # on the regenerate's message.
        self._seed_exchange()
        target = self._bubbles()[-1]._chat_msg_id
        self.assertIsNotNone(target)
        self.window._pending_version_target = target
        try:
            self.window._on_typed_send_failed(
                "typed send failed (simulated)")
            self.app.processEvents()
        finally:
            self.window._pending_version_target = None
        node = self.window._chat_thread().get(target)
        self.assertEqual(node.status, "done")
        self.assertIsNone(node.error)
        texts = [t for r, t in self._thread_texts() if not r]
        self.assertIn("typed send failed (simulated)", texts)

    def test_send_failure_kind_routes_to_the_right_message(self):
        # Field-check fix #7 (U4 #30): the kind rides the
        # done callback — a resend failure marks the version
        # target failed, a typed failure is a system line.
        import concurrent.futures
        self._seed_exchange()
        target = self._bubbles()[-1]._chat_msg_id
        self.assertIsNotNone(target)
        self.window._pending_version_target = target
        failing = concurrent.futures.Future()
        failing.set_exception(RuntimeError("resend boom"))
        self.window._on_typed_send_done(failing, kind="resend")
        self.app.processEvents()
        node = self.window._chat_thread().get(target)
        self.assertEqual(node.status, "failed")
        self.assertIn("resend boom", node.error or "")
        self.assertIsNone(
            getattr(self.window, "_pending_version_target", None))
        # The same failure as a typed send: the target is
        # untouched and the failure is a system line.
        self.window._pending_version_target = target
        failing2 = concurrent.futures.Future()
        failing2.set_exception(RuntimeError("typed boom"))
        try:
            self.window._on_typed_send_done(failing2, kind="typed")
            self.app.processEvents()
        finally:
            self.window._pending_version_target = None
        node = self.window._chat_thread().get(target)
        self.assertEqual(node.status, "failed")  # from the resend
        self.assertIn("resend boom", node.error or "")
        texts = [t for r, t in self._thread_texts() if not r]
        self.assertTrue(any("typed boom" in t for t in texts))


# ------------------------------------------------------------------ Qt: edit/quote
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowEditTests(WindowBase):
    """Edit and branch; quote and reply (moves 4/7)."""

    def test_edit_replaces_in_place_and_forks(self):
        self._connect_fake_live()
        self._seed_exchange()
        user_bubble = self._bubbles()[0]
        user_id = user_bubble._chat_msg_id
        self.window._edit_message(user_id)
        self.app.processEvents()
        self.assertEqual(self.window.input_cmd.text(), "What is 2+2?")
        self.assertTrue(
            self.window._composer_context_row.isVisible())
        self.assertIn("Editing",
                      self.window._composer_context_lbl.text())
        self.window.input_cmd.setText("What is 3+3?")
        self.window._commit_edit()
        self.app.processEvents()
        # The fork: the edited question, no answer yet.
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 3+3?")])
        self._stream_reply(["Si", "x"], "Six")
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 3+3?"),
                          ("assistant", "Six")])
        # The old branch is still there, one flip away.
        self.window._flip_version(user_id, -1)
        self.app.processEvents()
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?"),
                          ("assistant", "4")])

    def test_edit_unchanged_text_does_not_fork(self):
        self._seed_exchange()
        user_id = self._bubbles()[0]._chat_msg_id
        self.window._edit_message(user_id)
        self.window._commit_edit()
        self.app.processEvents()
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?"),
                          ("assistant", "4")])
        node = self.window._chat_thread().get(user_id)
        self.assertEqual(node.version_count, 1)

    def test_quote_stages_a_reply_in_the_composer(self):
        self._seed_exchange()
        assistant_id = self._bubbles()[-1]._chat_msg_id
        self.window._quote_message(assistant_id)
        self.app.processEvents()
        self.assertEqual(
            self.window._quoting_msg_id, assistant_id)
        self.assertTrue(
            self.window._composer_context_row.isVisible())
        self.assertIn("Replying",
                      self.window._composer_context_lbl.text())
        # Cancelling clears the staged quote.
        self.window._cancel_composer_context()
        self.assertIsNone(self.window._quoting_msg_id)
        self.assertFalse(
            self.window._composer_context_row.isVisible())

    def test_delete_then_undo_restores_the_message(self):
        self._seed_exchange()
        assistant_id = self._bubbles()[-1]._chat_msg_id
        self.window._delete_message(assistant_id)
        self.app.processEvents()
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?")])
        self.window._undo_delete(assistant_id)
        self.app.processEvents()
        self.assertEqual(self._thread_texts(),
                         [("user", "What is 2+2?"),
                          ("assistant", "4")])


# ------------------------------------------------------------------ Qt: voice
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowVoiceTests(WindowBase):
    """Spoken words land as text; dictation stages a draft;
    the voice state is visible (moves 5/13)."""

    def test_mic_message_is_marked_spoken(self):
        self.window._on_transcript("user", "hello from the mic")
        self.app.processEvents()
        bubble = self._bubbles()[-1]
        self.assertIn("spoken", bubble.who_lbl.text())
        node = self.window._chat_thread().visible_thread()[-1]
        self.assertTrue(node.spoken)

    def test_typed_echo_is_not_marked_spoken(self):
        self.window._note_typed_echo("typed words")
        self.window._on_transcript("user", "typed words")
        self.app.processEvents()
        bubble = self._bubbles()[-1]
        self.assertNotIn("spoken", bubble.who_lbl.text())

    def test_dictation_stages_a_draft_never_a_bubble(self):
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        self.assertTrue(self.window._dictation_mode)
        self.assertEqual(self.window._btn_dictate.text(), "Dictating…")
        self.assertEqual(
            self.window._voice_state_lbl.text(), "Dictating")
        self.window._on_transcript("user", "take a note")
        self.app.processEvents()
        self.assertEqual(self._bubbles(), [])
        self.assertEqual(self.window.input_cmd.text(), "take a note")
        self.window._chat_toggle_dictation()
        self.assertFalse(self.window._dictation_mode)

    def test_dictation_refuses_while_disconnected(self):
        # Field-check fix #6 (U3 #14): with no live session the
        # toggle refuses instead of faking "Dictating…".
        import voice_assistant.gui.window_chat_thread as _thread
        orig = _thread.live_session
        fake = _Fake()
        fake.is_connected = False
        _thread.live_session = fake
        self.addCleanup(
            lambda: setattr(_thread, "live_session", orig))
        self.window._chat_toggle_dictation()
        self.app.processEvents()
        # The flag is never even set: the refusal returns before
        # the toggle assigns it (the product reads it with a
        # getattr default for exactly this reason).
        self.assertFalse(
            getattr(self.window, "_dictation_mode", False))
        self.assertNotEqual(
            self.window._btn_dictate.text(), "Dictating…")
        self.assertFalse(self.window._btn_dictate.isChecked())
        self.assertNotEqual(
            self.window._voice_state_lbl.text(), "Dictating")
        from PySide6.QtWidgets import QLabel
        lines = [lbl.text() for lbl in
                 self.window.findChildren(QLabel, "systemLine")]
        self.assertTrue(any("Connect first" in t for t in lines))

    def test_dictation_turns_on_when_connected(self):
        # The #14 refusal is connection-gated, not a blanket
        # refusal: with a live session the toggle works as before.
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        self.assertTrue(self.window._dictation_mode)
        self.assertEqual(self.window._btn_dictate.text(), "Dictating…")
        self.assertEqual(
            self.window._voice_state_lbl.text(), "Dictating")

    def test_stop_dictating_turns_dictation_off(self):
        # Field-check fix #6 (U3 #15): a whole "stop dictating"
        # turns Dictation off instead of staging into the
        # composer as draft text.
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        self.assertTrue(self.window._dictation_mode)
        self.window._on_transcript("user", "stop dictating")
        self.app.processEvents()
        self.assertFalse(self.window._dictation_mode)
        self.assertEqual(self.window.input_cmd.text(), "")
        self.assertEqual(self._bubbles(), [])

    def test_stop_dictating_inside_a_sentence_still_dictates(self):
        # Whole-utterance rule: "stop dictating the letter" is
        # dictation, not the command.
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        self.window._on_transcript(
            "user", "stop dictating the letter")
        self.app.processEvents()
        self.assertTrue(self.window._dictation_mode)
        self.assertEqual(
            self.window.input_cmd.text(),
            "stop dictating the letter")

    def test_yes_answers_pending_confirmation_while_dictating(self):
        # Field-check fix #6 (U3 #15): a whole "yes" answering a
        # pending confirmation resolves it instead of staging
        # into the composer — the confirmation can be answered
        # by voice while Dictation is on.
        import threading
        import time
        from voice_assistant.core import confirm_gate
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        entry = {
            "tool_name": "mouse_control",
            "summary": "mouse_control (test)",
            "expires_at": time.time() + 60.0,
            "event": threading.Event(), "resolved": False,
            "approved": None, "error": None, "via": None,
            "folder": None,
        }
        with confirm_gate._pending_lock:
            confirm_gate._pending[9001] = entry
        self.addCleanup(
            lambda: confirm_gate._pending.pop(9001, None))
        self.window._on_transcript("user", "yes")
        self.app.processEvents()
        self.assertIsNone(confirm_gate.pending_request())
        self.assertEqual(self.window.input_cmd.text(), "")
        from PySide6.QtWidgets import QLabel
        lines = [lbl.text() for lbl in
                 self.window.findChildren(QLabel, "systemLine")]
        self.assertIn("Confirmed by voice.", lines)

    def test_yes_with_no_pending_confirmation_still_dictates(self):
        # No pending confirmation: "yes" is ordinary dictation.
        self._connect_fake_live()
        self.window._chat_toggle_dictation()
        self.window._on_transcript("user", "yes")
        self.app.processEvents()
        self.assertTrue(self.window._dictation_mode)
        self.assertEqual(self.window.input_cmd.text(), "yes")

    def test_dictate_and_mic_icons_are_visibly_different(self):
        # Field-check fix #6 (U3 #47): at the shipped 300 px
        # panel width the Dictate label collapses away, so the
        # glyphs must differ — the same mic shape measured 0.924
        # mask overlap pre-fix; pencil-vs-mic measures ~0.27.
        from PySide6.QtGui import QImage
        self.window.resize(1280, 800)
        self.app.processEvents()
        self.assertLess(self.window.chat_panel.width(), 420)
        self.assertEqual(self.window._btn_dictate.text(), "")

        def _mask(icon, size=32):
            img = icon.pixmap(size, size).toImage().convertToFormat(
                QImage.Format.Format_ARGB32)
            ink = set()
            for y in range(img.height()):
                for x in range(img.width()):
                    if (img.pixel(x, y) >> 24) & 0xFF:
                        ink.add((x, y))
            return ink

        m1 = _mask(self.window._btn_dictate.icon())
        m2 = _mask(self.window.btn_mic.icon())
        overlap = len(m1 & m2) / len(m1 | m2)
        self.assertLess(overlap, 0.90)

    def test_voice_state_line_follows_status(self):
        self.window._chat_voice_state_update("SPEAKING")
        self.assertEqual(
            self.window._voice_state_lbl.text(), "Speaking")
        self.assertTrue(self.window._voice_state_lbl.isVisible())
        self.window._chat_voice_state_update("LISTENING")
        self.assertEqual(
            self.window._voice_state_lbl.text(), "Listening")
        self.window._chat_voice_state_update("IDLE")
        self.assertFalse(self.window._voice_state_lbl.isVisible())


# ------------------------------------------------------------------ Qt: composer
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowComposerTests(WindowBase):
    """The composer (move 6) and the empty chat (move 14)."""

    def test_empty_chat_teaches_with_exactly_three_starters(self):
        chips = [self.window._chip_layout.itemAt(i).widget().text()
                 for i in range(self.window._chip_layout.count())
                 if self.window._chip_layout.itemAt(i).widget()
                 is not None]
        self.assertEqual(chips, ["What can you do?",
                                 "Remind me in 10 min",
                                 "What's on my screen?"])

    def test_draft_persists_and_restores(self):
        from PySide6.QtCore import QSettings as _QSettings
        self.window.input_cmd.setText("half a thought")
        self.window._chat_save_composer_draft()
        self.assertEqual(
            _QSettings("NovaAI", "Nova").value("chat/composer_draft"),
            "half a thought")
        self.window.input_cmd.setText("")
        self.window._chat_restore_composer_draft()
        self.assertEqual(self.window.input_cmd.text(), "half a thought")

    def test_type_while_speaking_queues_and_flushes(self):
        self._connect_fake_live()
        # A reply is streaming: typing queues instead of sending.
        self.window._on_transcript_chunk("assistant", "Hel")
        self.app.processEvents()
        self.assertTrue(self.window._chat_is_mid_reply())
        self.window._chat_queue_typed("second question")
        self.assertEqual(len(self.window._chat_queue), 1)
        queued = self._bubbles()[-1]
        self.assertIn("Queued", queued._status_lbl.text())
        # The queue caps at three; the fourth stays in the composer.
        self.window._chat_queue_typed("third")
        self.window._chat_queue_typed("fourth")
        self.window._chat_queue_typed("fifth")
        self.assertEqual(len(self.window._chat_queue), 3)
        # The turn ends: the first queued message sends.
        self.window._on_transcript("assistant", "Hello")
        self.app.processEvents()
        self.assertEqual(len(self.window._chat_queue), 2)

    def test_composer_placeholder_invites_typing_or_the_mic(self):
        # Step 2 F3 (parent-ruled 2026-11-28): the steady state
        # is "Ask anything", pinned by Phase 1 leg-57
        # (tests/test_scifi_home.py::test_transcript_did_not_regress
        # — "the old 'Type, or hold mic' copy is gone"); the
        # R6 sync (_sync_composer_placeholder) deliberately
        # overwrites it on build/resize/telemetry. The E-F103
        # copy ("Type, or hold mic", shortened 2026-11-20 from
        # "Type, or hold the mic to speak" so it read whole at
        # every width) survives only as a transient build value
        # in window_shell_assistant.py — the chat_ui test pinned
        # that transient, not the steady state. Product unchanged.
        self.assertEqual(
            self.window.input_cmd.placeholderText(),
            "Ask anything")


# ------------------------------------------------------------------ Qt: find/history
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowFindTests(WindowBase):
    """Find that finds (move 8): loaded bubbles and older history."""

    def test_find_counts_loaded_and_older_matches(self):
        self._seed_exchange(question="What is the capital of France?",
                            answer="Paris")
        self._store.log_message(
            "user", "an older question about the capital")
        self._store.log_message(
            "assistant", "an older answer about the capital")
        self.window._chat_find_reset("capital")
        # Two matches live only in the store — and the reset
        # steps straight to the first match, which is a stored
        # one: the history window opens on its surroundings and
        # the find state is spent by the jump.
        self.assertEqual(self.window._find_store_total, 2)
        self.assertTrue(self.window._btn_back_latest.isVisible())
        texts = [t for _r, t in self._thread_texts()]
        self.assertIn("an older question about the capital", texts)
        self.assertIn("an older answer about the capital", texts)
        # Back to latest restores the live exchange.
        self.window._chat_back_to_latest()
        self.app.processEvents()
        self.assertFalse(self.window._btn_back_latest.isVisible())
        self.assertEqual(
            [(r, t) for r, t in self._thread_texts() if r],
            [("user", "What is the capital of France?"),
             ("assistant", "Paris")])

    def test_find_loaded_only_match_stays_in_the_thread(self):
        self._seed_exchange(question="What is the capital of France?",
                            answer="Paris")
        self._store.log_message(
            "user", "an older question about the capital")
        # "france" matches only the loaded bubble — no stored
        # row — so the find state survives the step.
        self.window._chat_find_reset("france")
        self.assertEqual(len(self.window._find_matches), 1)
        self.assertEqual(self.window._find_matches[0][0], "bubble")
        self.assertEqual(self.window.find_count.text(), "1 of 1")

    def test_find_with_no_match_says_so(self):
        self.window._chat_find_reset("zzzznothingmatches")
        self.assertEqual(self.window._find_matches, [])
        self.assertIn("No matches", self.window.find_count.text())


@unittest.skipUnless(_QT, "Qt unavailable")
class WindowHistoryTests(WindowBase):
    """History (move 9): restore, the last-seen hairline, the
    conversation list, pinned jumps."""

    def test_restore_loads_the_last_thirty_and_dims_old_days(self):
        import datetime
        self._store.log_message("user", "an old question")
        self._store.log_message("assistant", "an old answer")
        self._store.log_message("user", "today's question")
        self._store.log_message("assistant", "today's answer")
        # Backdate the first two rows two days (UTC): their local
        # date is certainly not today, so restore must dim them.
        old_ts = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=2)).strftime(
                      "%Y-%m-%d %H:%M:%S")
        self._store._execute(
            "UPDATE conversation_logs SET timestamp = ? WHERE id <= 2",
            (old_ts,))
        # Rebuild the window so restore runs against the seeded store.
        self.window.close()
        self.app.processEvents()
        self.window = self._build_window()
        self._window = self.window
        self.assertEqual(len(self._bubbles()), 4)
        # Day dividers are system nodes in the tree — the message
        # texts, in order, are the four seeded lines.
        texts = [(r, t) for r, t in self._thread_texts() if r]
        self.assertEqual(texts,
                         [("user", "an old question"),
                          ("assistant", "an old answer"),
                          ("user", "today's question"),
                          ("assistant", "today's answer")])
        dividers = [t for r, t in self._thread_texts() if not r]
        self.assertEqual(len(dividers), 2)
        bubbles = self._bubbles()
        self.assertTrue(bubbles[0]._chat_dimmed)
        self.assertTrue(bubbles[1]._chat_dimmed)
        self.assertFalse(bubbles[2]._chat_dimmed)
        self.assertFalse(bubbles[3]._chat_dimmed)

    def test_last_seen_hairline_appears_after_the_seen_message(self):
        rows = []
        for i in range(3):
            self._store.log_message("user", f"q{i}")
            self._store.log_message("assistant", f"a{i}")
        newest = self._store.get_recent_history(1)[0]["id"]
        from PySide6.QtCore import QSettings as _QSettings
        # Mark the second-newest as last seen: one message is new.
        _QSettings("NovaAI", "Nova").setValue(
            "chat/last_seen_id", newest - 1)
        self.window.close()
        self.app.processEvents()
        self.window = self._build_window()
        self._window = self.window
        self.assertIsNotNone(self.window._chat_new_since_lbl)
        self.assertIn("New since",
                      self.window._chat_new_since_lbl.text())

    def test_regenerate_in_history_window_survives_back_to_latest(self):
        # Field-check fix #7 (U4 #28): regenerate inside a
        # history window, then Back to latest — the new branch
        # merges into the live tree instead of dying with the
        # window thread.
        self._store.log_message("user", "What is 2+2?")
        self._store.log_message("assistant", "4")
        rows = self._store.get_recent_history(10)
        self.window._add_bubble("You", "What is 2+2?", True,
                                animate=False)
        self.window._add_bubble("VYUK", "4", False, animate=False)
        self.app.processEvents()
        self.window._chat_open_history_window(rows)
        self.app.processEvents()
        hist_thread = self.window._chat_thread()
        asst_id = next((m.id for m in hist_thread.visible_thread()
                        if m.role == "assistant"), None)
        self.assertIsNotNone(asst_id)
        self._connect_fake_live()
        self.window._regenerate_message(asst_id)
        self.app.processEvents()
        self._stream_reply(["Fo", "ur"], "Four")
        node = hist_thread.get(asst_id)
        self.assertEqual(node.version_count, 2)
        self.window._chat_back_to_latest()
        self.app.processEvents()
        live = [(m.role, m.text, m.version_count)
                for m in self.window._chat_thread().visible_thread()]
        self.assertEqual(live, [("user", "What is 2+2?", 1),
                                ("assistant", "Four", 2)])

    def test_last_seen_mark_covers_voice_turns(self):
        # Field-check fix #7 (U4 #31): two voice turns (never
        # logged to the store) then mark last seen — the mark
        # is the live-tree leaf, not the newest stored id.
        self._store.log_message("user", "What is 2+2?")
        self._store.log_message("assistant", "4")
        self.window._add_bubble("You", "What is 2+2?", True,
                                animate=False)
        self.window._add_bubble("VYUK", "4", False, animate=False)
        self.app.processEvents()
        self.window._on_transcript("user", "spoken question here")
        self.window._on_transcript("assistant", "spoken answer here")
        self.app.processEvents()
        self.window._chat_mark_last_seen()
        leaf = self.window._chat_thread().leaf()
        self.assertEqual(self.window._chat_last_seen_id(), leaf.id)
        self.assertEqual(leaf.role, "assistant")

    def test_last_seen_store_mark_still_places_the_hairline(self):
        # Field-check fix #7 (U4 #31): the durable store mark
        # is what the restore-time hairline reads — mark, log
        # newer rows after it, rebuild: the hairline sits after
        # the marked message.
        for i in range(2):
            self._store.log_message("user", f"q{i}")
            self._store.log_message("assistant", f"a{i}")
        self.window._add_bubble("You", "q0", True, animate=False)
        self.window._add_bubble("VYUK", "a0", False, animate=False)
        self.window._add_bubble("You", "q1", True, animate=False)
        self.window._add_bubble("VYUK", "a1", False, animate=False)
        self.app.processEvents()
        self.window._chat_mark_last_seen()
        self._store.log_message("user", "q2")
        self._store.log_message("assistant", "a2")
        self.window.close()
        self.app.processEvents()
        self.window = self._build_window()
        self._window = self.window
        self.assertIsNotNone(self.window._chat_new_since_lbl)
        self.assertIn("New since",
                      self.window._chat_new_since_lbl.text())

    def test_pin_shows_in_the_header_and_jumps(self):
        self._seed_exchange()
        assistant_id = self._bubbles()[-1]._chat_msg_id
        self.window._toggle_pin(assistant_id, True)
        self.app.processEvents()
        self.assertTrue(self.window._btn_pinned.isVisible())
        self.assertEqual(self.window._btn_pinned.text(), "Pinned (1)")
        self.window._toggle_pin(assistant_id, False)
        self.assertFalse(self.window._btn_pinned.isVisible())

    def test_conversations_dialog_lists_days(self):
        from gui.window_chat_conversations import ConversationsDialog
        days = [
            {"label": "Today", "title": "What is 2+2?",
             "count": 2, "rows": [{"id": 1}, {"id": 2}]},
            {"label": "Yesterday", "title": "Remind me",
             "count": 4, "rows": [{"id": 3}]},
        ]
        opened = []
        dlg = ConversationsDialog(self.window, days, opened.append)
        self.assertEqual(dlg._list.count(), 2)
        self.assertEqual(dlg._list.item(0).text(),
                         "Today — What is 2+2?")
        self.assertIn("2 messages", dlg._list.item(0).toolTip())
        dlg.close()


# ------------------------------------------------------------------ Qt: pill/actions
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowPillTests(WindowBase):
    """The new-messages pill counts (move 10)."""

    def test_pill_counts_new_messages_while_away(self):
        # Scroll away from the bottom, then a message lands.
        bar = self.window.chat_scroll.verticalScrollBar()
        self.window._chat_near_bottom = False
        self.window._on_transcript("assistant", "a new message")
        self.app.processEvents()
        self.assertEqual(self.window._new_msgs_since, 1)
        self.assertIn("1 new message",
                      self.window._btn_new_msgs.text())
        self.window._jump_to_latest()
        self.app.processEvents()
        self.assertEqual(self.window._new_msgs_since, 0)


@unittest.skipUnless(_QT, "Qt unavailable")
class WindowCardTests(WindowBase):
    """Cards in the conversation (moves 3/12): the answer first."""

    def test_result_card_leads_with_the_answer(self):
        self.window._add_result_card(
            "Files", "found 3 files\n- a.py\n- b.py", tool_name="files",
            answer="Done — found 3 files")
        self.app.processEvents()
        node = self.window._chat_thread().visible_thread()[-1]
        self.assertEqual(node.kind, "card")
        self.assertEqual(node.card_answer, "Done — found 3 files")


# ------------------------------------------------------------------ PTT deactivation
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowPttDeactivateTests(WindowBase):
    """Field-check fix #5 (M9): a Space-key PTT hold must not
    survive a window activation change — the OS delivers the
    Space release to the window the user switched to, so without
    a release the mic streams indefinitely."""

    def _audio_manager(self):
        # The object _ptt_set_active actually mutates: the mixin's
        # module-level import, bound at first import. (Re-importing
        # from gui_facade would re-resolve to the current setUp's
        # fresh stub, which the window never sees.)
        from voice_assistant.gui.window_session_connection import (
            audio_manager,
        )

        return audio_manager

    def _start_button_hold(self):
        """Hold Space on the mic button (the field-check m9b shape);
        returns (other window, its edit box)."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLineEdit, QWidget

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        other = QWidget()
        other_edit = QLineEdit(other)
        other.setWindowTitle("OtherApp")
        other.show()
        self.app.processEvents()
        w.btn_mic.setFocus()
        self.app.processEvents()
        audio = self._audio_manager()
        audio.is_muted = True
        w._ptt_hold_active = False
        w.settings.setValue("ears/ptt_mode", "hold")
        QTest.keyPress(w.btn_mic, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(w._ptt_hold_active)
        self.assertFalse(audio.is_muted)
        return other, other_edit

    def test_hold_on_button_releases_when_returning(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        other, other_edit = self._start_button_hold()
        w = self.window
        audio = self._audio_manager()
        # The switch itself is a no-op here (the other window
        # already stole activation when it was shown) — the leak
        # is that the hold survives the release landing elsewhere
        # and the return to VYUK.
        self.app.setActiveWindow(other)
        other.activateWindow()
        self.app.processEvents()
        QTest.keyClick(other_edit, Qt.Key.Key_Space)
        self.app.processEvents()
        self.app.setActiveWindow(w)
        w.activateWindow()
        self.app.processEvents()
        self.assertFalse(w._ptt_hold_active)
        self.assertTrue(audio.is_muted)

    def test_window_space_hold_releases_at_switch(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QWidget

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        other = QWidget()
        other.setWindowTitle("OtherApp")
        other.show()
        self.app.processEvents()
        w.activateWindow()
        self.app.setActiveWindow(w)
        self.app.processEvents()
        audio = self._audio_manager()
        audio.is_muted = True
        w._ptt_hold_active = False
        w.settings.setValue("ears/ptt_mode", "hold")
        w.setFocus()
        self.app.processEvents()
        # Space with focus on the window (not the mic button, not
        # a text field): the window-level keyPressEvent starts the
        # hold and the mic button is never pressed.
        QTest.keyPress(w, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(w._ptt_hold_active)
        self.assertFalse(audio.is_muted)
        self.assertFalse(w.btn_mic.isDown())
        self.app.setActiveWindow(other)
        other.activateWindow()
        self.app.processEvents()
        self.assertFalse(w.isActiveWindow())
        self.assertFalse(w._ptt_hold_active)
        self.assertTrue(audio.is_muted)

    def test_deactivate_with_no_hold_changes_nothing(self):
        from PySide6.QtWidgets import QWidget

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        other = QWidget()
        other.show()
        self.app.processEvents()
        audio = self._audio_manager()
        audio.is_muted = True
        w._ptt_hold_active = False
        self.app.setActiveWindow(other)
        other.activateWindow()
        self.app.processEvents()
        self.app.setActiveWindow(w)
        w.activateWindow()
        self.app.processEvents()
        self.assertFalse(w._ptt_hold_active)
        self.assertTrue(audio.is_muted)

    def test_normal_hold_and_release_still_works(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        audio = self._audio_manager()
        audio.is_muted = True
        w._ptt_hold_active = False
        w.settings.setValue("ears/ptt_mode", "hold")
        w.btn_mic.setFocus()
        self.app.processEvents()
        QTest.keyPress(w.btn_mic, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(w._ptt_hold_active)
        self.assertFalse(audio.is_muted)
        QTest.keyRelease(w.btn_mic, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertFalse(w._ptt_hold_active)
        self.assertTrue(audio.is_muted)

    def test_global_hotkey_hold_survives_activation_change(self):
        from PySide6.QtWidgets import QWidget
        from voice_assistant.core.gui_facade import hotkey_daemon

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        other = QWidget()
        other.show()
        self.app.processEvents()
        # The OS-level daemon physically holds PTT (its own key
        # state); the window must not cut it on activation change.
        hotkey_daemon._ptt_active = True
        try:
            audio = self._audio_manager()
            audio.is_muted = True
            w.settings.setValue("ears/ptt_mode", "hold")
            w._on_ptt_pressed()
            self.app.processEvents()
            self.assertTrue(w._ptt_hold_active)
            self.assertFalse(audio.is_muted)
            self.app.setActiveWindow(other)
            other.activateWindow()
            self.app.processEvents()
            self.assertTrue(w._ptt_hold_active)
            self.assertFalse(audio.is_muted)
            self.app.setActiveWindow(w)
            w.activateWindow()
            self.app.processEvents()
            self.assertTrue(w._ptt_hold_active)
            self.assertFalse(audio.is_muted)
        finally:
            hotkey_daemon._ptt_active = False
            w._on_ptt_released()
            self.app.processEvents()

    def test_toggle_mode_survives_activation_change(self):
        from PySide6.QtWidgets import QWidget

        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        other = QWidget()
        other.show()
        self.app.processEvents()
        w.settings.setValue("ears/ptt_mode", "toggle")
        w._ptt_toggled_on = False
        w._ptt_hold_active = False
        w._on_ptt_pressed()
        self.app.processEvents()
        self.assertTrue(w._ptt_toggled_on)
        self.assertFalse(w._ptt_hold_active)
        self.app.setActiveWindow(other)
        other.activateWindow()
        self.app.processEvents()
        self.app.setActiveWindow(w)
        w.activateWindow()
        self.app.processEvents()
        self.assertTrue(w._ptt_toggled_on)
        self.assertFalse(w._ptt_hold_active)
        # Clean up: toggle off again.
        w._on_ptt_pressed()


# ------------------------------------------------------------------ prototype
@unittest.skipUnless(_QT, "Qt unavailable")
class PrototypeTests(WindowBase):
    """The long-chat prototype: fifty messages, still readable —
    the deliverable that judges bubbles vs transcript."""

    def test_fifty_message_thread_on_the_real_window(self):
        for i in range(25):
            self.window._add_bubble(
                "You", f"Question {i}: what is {i} + {i}?", True)
            self.window._add_bubble(
                "VYUK",
                f"**{i} + {i} = {2 * i}**\n\n1. Add the two numbers.\n"
                f"2. The answer is {2 * i}.",
                False)
            self.app.processEvents()
        thread = self.window._chat_thread().visible_thread()
        self.assertEqual(len(thread), 50)
        bubbles = self._bubbles()
        self.assertEqual(len(bubbles), 50)
        # Every bubble is still in the layout, in order, and the
        # last one is the newest answer.
        self.assertEqual(bubbles[-1]._plain_text.split("\n")[0],
                         f"**24 + 24 = 48**")
        self.assertEqual(self.window._chat_has_messages, True)

    def test_jump_to_latest_keeps_the_latest_message_whole(self):
        # UI rethink Build 1: the jump snaps the first visible
        # message whole — but only when the latest message stays
        # whole too. On a long thread the span from the first
        # visible bubble to the last exceeds the viewport, so the
        # two cannot both be whole; the snap must not cut the
        # latest message (the one the jump exists to show) to
        # straighten the top one. Invariant: the last bubble is
        # always fully visible after the jump; and if the snap
        # moved the scroll up off the maximum, no bubble straddles
        # the viewport top.
        for i in range(25):
            self.window._add_bubble(
                "You", f"Question {i}: what is {i} + {i}?", True)
            self.window._add_bubble(
                "VYUK",
                f"**{i} + {i} = {2 * i}**\n\n1. Add the two numbers.\n"
                f"2. The answer is {2 * i}.",
                False)
            self.app.processEvents()
        bar = self.window.chat_scroll.verticalScrollBar()
        bar.setValue(int(bar.maximum() * 0.5))
        self.app.processEvents()
        self.window._jump_to_latest()
        # The snap fires one event-loop turn after the jump.
        self.app.processEvents()
        self.app.processEvents()
        value = bar.value()
        viewport_h = self.window.chat_scroll.viewport().height()
        bubbles = self._bubbles()
        last_bottom = bubbles[-1].geometry().bottom()
        self.assertLessEqual(last_bottom, value + viewport_h)
        if value < bar.maximum():
            cut = [b for b in bubbles
                   if b.geometry().top() < value
                   < b.geometry().bottom()]
            self.assertEqual(cut, [])

    def test_fifty_message_thread_headless_with_branches(self):
        t = ChatThread()
        for i in range(25):
            t.add_user(f"Question {i}")
            t.add_assistant(f"Answer {i}")
        self.assertEqual(len(t.visible_thread()), 50)
        # A regenerate mid-thread forks: the new version starts
        # a fresh branch, so the visible thread ends at it (26
        # messages) — the old branch is not deleted, it is one
        # flip away.
        mid = t.visible_thread()[25]
        t.add_version(mid.id, "Answer 12, revised")
        self.assertEqual(len(t.visible_thread()), 26)
        self.assertEqual(t.visible_thread()[25].text,
                         "Answer 12, revised")
        t.set_active_version(mid.id, 0)
        self.assertEqual(len(t.visible_thread()), 50)
        self.assertEqual(t.visible_thread()[25].text, "Answer 12")


# ------------------------------------------------- Qt: output picker
@unittest.skipUnless(_QT, "Qt unavailable")
class WindowOutputPickerTests(WindowBase):
    """Field-check fix #12 (M11a): the speaker chooser mirrors
    the microphone combo — it lists the real outputs, a pick
    persists to QSettings and lands on the audio manager, and
    the label names the picked speaker.

    The harness stubs the audio module, so each test swaps the
    REAL manager in (and the fake device list) before
    re-populating, then restores the stub — the window's own
    mixin resolves the services-audio module global at call
    time, so the swap steers the real populate path."""

    _FAKE_DEVS = [
        {"name": "Fake Mic", "max_input_channels": 2,
         "max_output_channels": 0},
        {"name": "Speakers A", "max_input_channels": 0,
         "max_output_channels": 2},
        {"name": "Speakers B", "max_input_channels": 0,
         "max_output_channels": 2},
    ]

    class _FakeDefault:
        device = (0, 1)

    @staticmethod
    def _fake_query_devices(kind=None):
        if kind == "input":
            return {"name": "Fake Mic"}
        if kind == "output":
            return {"name": "Speakers A"}
        return [dict(d) for d in WindowOutputPickerTests._FAKE_DEVS]

    def setUp(self):
        super().setUp()
        from core.voice.audio import AudioManager  # noqa: E402
        import core.voice.audio_devices_mic as _mic  # noqa: E402
        import voice_assistant.gui.window_services_audio as _wsa  # noqa: E402
        self._real = AudioManager()
        self._wsa = _wsa
        self._orig_manager = _wsa.audio_manager
        self._orig_sd = (_mic.sd.query_devices, _mic.sd.default)
        _mic.sd.query_devices = self._fake_query_devices
        _mic.sd.default = self._FakeDefault()
        _wsa.audio_manager = self._real
        self.window._populate_audio_devices()
        self.app.processEvents()

    def tearDown(self):
        import core.voice.audio_devices_mic as _mic  # noqa: E402
        _mic.sd.query_devices, _mic.sd.default = self._orig_sd
        self._wsa.audio_manager = self._orig_manager
        super().tearDown()

    def test_speaker_combo_lists_outputs_and_pick_persists(self):
        from voice_assistant.core.gui_facade import (  # noqa: E402
            qsettings_get)
        combo = getattr(self.window, "combo_out_dev", None)
        self.assertIsNotNone(combo, "no speaker chooser on the page")
        items = [combo.itemText(i) for i in range(combo.count())]
        self.assertIn("Speakers A", items)
        self.assertIn("Speakers B", items)
        combo.setCurrentIndex(items.index("Speakers B"))
        self.app.processEvents()
        self.assertEqual(
            qsettings_get("audio/output_device",
                          settings=self.window.settings), 2)
        self.assertEqual(
            qsettings_get("audio/output_device_name",
                          settings=self.window.settings), "Speakers B")
        self.assertEqual(self._real.output_device, 2)
        self.assertEqual(self._real.output_device_name, "Speakers B")
        self.assertIn("Speakers B", self.window.lbl_output_dev.text())

    def test_automatic_clears_the_stored_pick(self):
        from voice_assistant.core.gui_facade import (  # noqa: E402
            qsettings_contains, qsettings_get)
        combo = self.window.combo_out_dev
        items = [combo.itemText(i) for i in range(combo.count())]
        combo.setCurrentIndex(items.index("Speakers B"))
        self.app.processEvents()
        self.assertEqual(
            qsettings_get("audio/output_device",
                          settings=self.window.settings), 2)
        combo.setCurrentIndex(0)  # Automatic
        self.app.processEvents()
        self.assertFalse(qsettings_contains(
            "audio/output_device", settings=self.window.settings))
        self.assertIsNone(self._real.output_device)
        self.assertIsNone(self._real.output_device_name)
        self.assertIn("Automatic", self.window.lbl_output_dev.text())


class WindowCapNoticeTests(WindowBase):
    """Field-check fix #14 (T9): a fact dropped at the 100-fact cap
    is announced in chat instead of vanishing silently."""

    def _system_lines(self):
        from PySide6.QtWidgets import QLabel
        return [lbl.text() for lbl in
                self.window.findChildren(QLabel, "systemLine")]

    def _fill_store_to_cap(self, module):
        import itertools
        import string
        words = ["f" + "".join(c) for c in itertools.islice(
            itertools.product(string.ascii_lowercase, repeat=3), 100)]
        for n, w in enumerate(words, start=1):
            module.learn_from_text(f"My {w} is value {n}.")
        self.assertEqual(len(module.get_learned_facts()), 100)

    def test_101st_fact_is_announced_in_chat(self):
        # The harness stubs voice_assistant.core.memory.memory, so the
        # facade cannot resolve the learning module here — drive the
        # window with the flat core.memory.learning module instead
        # (the same seam other GUI tests use for fakes).
        from core.memory import learning as module
        orig_store = module.memory_store
        orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        self.window._learning_module = lambda: module
        try:
            module.memory_store = self._store
            module.take_dropped_at_cap()
            self._fill_store_to_cap(module)
            self.window._learn_from_utterance(
                "My zzzq is value onezerothree.")
            self.app.processEvents()
            lines = self._system_lines()
            self.assertTrue(
                any("Memory is full" in t and "onezerothree" in t
                    for t in lines),
                f"no cap notice in system lines: {lines}")
            self.assertFalse(any("Noted" in t and "onezerothree" in t
                                 for t in lines))
        finally:
            module.memory_store = orig_store
            if orig_feed is None:
                os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
            else:
                os.environ["VYUK_LEARNING_FEED_DIR"] = orig_feed

    def test_cap_notice_fires_again_for_the_next_dropped_fact(self):
        from core.memory import learning as module
        orig_store = module.memory_store
        orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        self.window._learning_module = lambda: module
        try:
            module.memory_store = self._store
            module.take_dropped_at_cap()
            self._fill_store_to_cap(module)
            self.window._learn_from_utterance(
                "My zzzq is value onezerothree.")
            self.window._learn_from_utterance(
                "My zzzr is value onezerofour.")
            self.app.processEvents()
            lines = self._system_lines()
            self.assertTrue(
                any("Memory is full" in t and "onezerothree" in t
                    for t in lines),
                f"first cap notice missing: {lines}")
            self.assertTrue(
                any("Memory is full" in t and "onezerofour" in t
                    for t in lines),
                f"second cap notice missing: {lines}")
        finally:
            module.memory_store = orig_store
            if orig_feed is None:
                os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
            else:
                os.environ["VYUK_LEARNING_FEED_DIR"] = orig_feed


if __name__ == "__main__":
    unittest.main()
