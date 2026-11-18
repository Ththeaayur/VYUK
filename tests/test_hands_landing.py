"""B13 — hands-tool landing checks via the B5 vision backend.

Lab leg only. The real-Windows field leg — a real click on his
machine whose landing the vision backend confirms or denies, and
whether his llama_cpp wheel exposes Qwen3VLChatHandler — is OWED,
not this batch.

Gap proved first (audit L10; TOOLS_LOOPHOLES_AUDIT.md §2c): the 16
hands tools are honestly unverifiable, and the coordinator records
them as success because it never reads outcome_verified (B1's
proof). A complex desktop task is built almost entirely of clicks,
typing, pastes and drags that cannot confirm they landed.

What this batch builds (and this suite proves): ONE design — one
helper, one channel. core/tool_outcome.hands_landing_check reads
the screen after the act through the B5 vision backend and the
landing verdict rides the seam Outcome (verified /
unverified-with-reason / landing_failed); the dispatch carries the
kind through (outcome_kind) so the coordinator can tell a failed
landing from an unreadable screen, and _run_step turns a
landing_failed into a StepOutcome failure the B4 jump / halt acts
on. Where vision cannot read, the tools stay honestly
unverified — never upgraded. The check is driven by the DECLARED
expectation (the clicked text/name, the typed/pasted text, or the
file+window for a drop); it never invents what "landed" means in
general — that is the R-C contract (audit D2), still owed, NOT
closed by this batch.

Coverage split (recorded in TOOLS_LOOPHOLES_AUDIT.md §2c B13):
WIRED (7): click_text, click_element, type_text, type_clipboard,
paste_text, paste_snippet, drag_file_onto. STAY HONESTLY
UNVERIFIED (9): click_by_mark (its `expect` arg already verifies
via the UIA engine — the vision leg is not added to the shared
verifier); cursor_control, mouse_control (coordinate-only acts, no
named target); drag_mouse, click_and_drag_path (multi-point paths
— one region cannot stand for a path); press_hotkey,
desktop_control, media_control, browser_press (no visible target).

Stdlib unittest only. Vision is faked throughout — no real
weights, no real inference, no real Windows.
"""

import inspect
import os
import pathlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

from core import tool_outcome as outcome_mod  # noqa: E402
from core import vision_backend as vb  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.brain.task_coordinator_steps import format_receipt  # noqa: E402
from core.funnel import tool_operations as ops_mod  # noqa: E402
from core.funnel import tools_registry as registry_mod  # noqa: E402
from core.funnel import tools_registry_exec_dispatch as dispatch_mod  # noqa: E402

_TREEROOT = Path(__file__).resolve().parent.parent

_BASE = "Did the thing."
_SHOT = (b"fake-jpeg", "image/jpeg")


def _capture_ok(*_args, **_kw):
    return _SHOT


def _capture_raises(*_args, **_kw):
    raise OSError("X connection failed: error 5")


def _capture_none(*_args, **_kw):
    return None


def _describe(text):
    def _fn(image, prompt=""):
        return {"ok": True, "text": text}
    return _fn


def _describe_bad(reason="weights-missing"):
    def _fn(image, prompt=""):
        return {"ok": False, "reason": reason, "detail": "no weights"}
    return _fn


def _read(text):
    def _fn(image):
        return {"ok": True, "text": text}
    return _fn


def _read_bad(reason="weights-missing"):
    def _fn(image):
        return {"ok": False, "reason": reason, "detail": "no weights"}
    return _fn


def _vision_not_ready():
    return {
        "qwen3-vl-2b": {"ready": False, "missing": ["qwen3-vl-2b.gguf"]},
        "granite-docling-258m": {"ready": False,
                                 "missing": ["granite-docling.onnx"]},
    }


def _vision_ready():
    return {
        "qwen3-vl-2b": {"ready": True, "missing": []},
        "granite-docling-258m": {"ready": True, "missing": []},
    }


# ---------------------------------------------------------------------------
# 1. helper unit tests (fake capture + fake vision legs)
# ---------------------------------------------------------------------------

class HelperTypePasteTests(unittest.TestCase):
    def test_type_text_present_is_verified(self):
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=_capture_ok,
            read_page=_read("Welcome to Notepad — hello world"))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")
        self.assertTrue(str(out).startswith(_BASE))
        self.assertIn("Vision check: the text is on screen", str(out))

    def test_type_text_absent_is_landing_failed(self):
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=_capture_ok,
            read_page=_read("Welcome to Notepad — empty document"))
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")
        self.assertTrue(str(out).startswith(_BASE))
        self.assertIn("did not land", str(out))
        self.assertIn("hello", str(out))

    def test_paste_present_is_verified(self):
        out = outcome_mod.hands_landing_check(
            "paste", "greet", _BASE, capture=_capture_ok,
            read_page=_read("greet — pasted line one"))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")

    def test_paste_absent_is_landing_failed(self):
        out = outcome_mod.hands_landing_check(
            "paste", "greet", _BASE, capture=_capture_ok,
            read_page=_read("nothing here yet"))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")

    def test_type_read_page_failure_is_unverified(self):
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=_capture_ok,
            read_page=_read_bad())
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("weights-missing", out.evidence)

    def test_type_capture_failure_is_unverified(self):
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=_capture_raises,
            read_page=_read("hello"))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("capture", out.evidence)

    def test_type_capture_none_is_unverified(self):
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=_capture_none,
            read_page=_read("hello"))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")

    def test_type_bare_bytes_capture_works(self):
        # _capture_screen_bytes returns a tuple, but a bare-bytes
        # capture is accepted too — the helper normalises both.
        out = outcome_mod.hands_landing_check(
            "type", "hello", _BASE, capture=lambda: b"fake-jpeg",
            read_page=_read("hello"))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")

    def test_empty_target_is_unverified_without_capturing(self):
        seen = []

        def _recording(*_a, **_k):
            seen.append(1)
            return _SHOT

        out = outcome_mod.hands_landing_check(
            "type", "   ", _BASE, capture=_recording,
            read_page=_read("hello"))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertEqual(seen, [])


class HelperClickDragTests(unittest.TestCase):
    def test_click_yes_is_verified(self):
        out = outcome_mod.hands_landing_check(
            "click", "Settings", _BASE, capture=_capture_ok,
            describe=_describe("Yes, the Settings window is open."))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")
        self.assertIn("Vision check: the landing is confirmed",
                      str(out))

    def test_click_no_is_landing_failed(self):
        out = outcome_mod.hands_landing_check(
            "click", "Settings", _BASE, capture=_capture_ok,
            describe=_describe("No, nothing opened."))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")
        self.assertIn("did not land", str(out))

    def test_click_inconclusive_is_unverified(self):
        # A model answer that is neither a leading yes nor a
        # leading no is never guessed at — honestly unverified.
        out = outcome_mod.hands_landing_check(
            "click", "Settings", _BASE, capture=_capture_ok,
            describe=_describe("The screen shows a window."))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("inconclusive", out.evidence)

    def test_click_backend_unavailable_is_unverified(self):
        out = outcome_mod.hands_landing_check(
            "click", "Settings", _BASE, capture=_capture_ok,
            describe=_describe_bad())
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("weights-missing", out.evidence)

    def test_drag_file_yes_is_verified(self):
        out = outcome_mod.hands_landing_check(
            "drag_file", "the file 'a.txt' open in the 'Word' window",
            _BASE, capture=_capture_ok,
            describe=_describe("Yes, a.txt is open in Word."))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")

    def test_drag_file_no_is_landing_failed(self):
        out = outcome_mod.hands_landing_check(
            "drag_file", "the file 'a.txt' open in the 'Word' window",
            _BASE, capture=_capture_ok,
            describe=_describe("No, Word shows no document."))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")

    def test_unknown_kind_is_unverified_without_capturing(self):
        seen = []

        def _recording(*_a, **_k):
            seen.append(1)
            return _SHOT

        out = outcome_mod.hands_landing_check(
            "teleport", "Settings", _BASE, capture=_recording,
            describe=_describe("Yes"))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertEqual(seen, [])


class HelperLeadingYesNoTests(unittest.TestCase):
    def test_edges(self):
        fn = outcome_mod._landing_leading_yes_no
        self.assertEqual(fn("YES!"), "yes")
        self.assertEqual(fn(" Yes, it is"), "yes")
        self.assertEqual(fn("no."), "no")
        self.assertEqual(fn("No, nothing."), "no")
        self.assertIsNone(fn("Nope"))
        self.assertIsNone(fn(""))
        self.assertIsNone(fn("Not yet"))
        self.assertIsNone(fn("The screen shows a window."))


class HelperProductionPathTests(unittest.TestCase):
    def test_no_weights_is_unverified_and_capture_never_runs(self):
        # Readiness first: with no weights on disk the check costs
        # nothing — the capture is never attempted and the receipt
        # is honestly unverified, never upgraded. Only the capture
        # is injected here, so the readiness check still runs (an
        # injected vision leg would skip it).
        seen = []

        def _recording(*_a, **_k):
            seen.append(1)
            return _SHOT

        with mock.patch.object(vb, "vision_status",
                               return_value=_vision_not_ready()):
            out = outcome_mod.hands_landing_check(
                "type", "hello", _BASE, capture=_recording)
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertEqual(seen, [])
        self.assertIn("not ready", out.evidence)

    def test_ready_backend_runs_the_production_path(self):
        # No injectables at all: the helper resolves the real
        # vision legs + the real capture through the module it
        # imports — both patched here, so the verdict is verified.
        from core.desktop import control_system as cs

        with mock.patch.object(vb, "vision_status",
                               return_value=_vision_ready()):
            with mock.patch.object(
                    vb, "read_page",
                    return_value={"ok": True, "text": "hello world"}):
                with mock.patch.object(
                        cs, "_capture_screen_bytes",
                        return_value=_SHOT):
                    out = outcome_mod.hands_landing_check(
                        "type", "hello", _BASE)
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")


# ---------------------------------------------------------------------------
# 2. handler drives (paste_text + type_text) and source pins
# ---------------------------------------------------------------------------

class _FakeSendType:
    """Fake ``uia_send._sendinput_type`` (GAPS-2): records the
    batches it was asked to type and reports them all sent."""

    def __init__(self):
        from core.desktop.uia_send import _SendOutcome
        self._outcome_cls = _SendOutcome
        self.typed = []

    def __call__(self, text, clear_first=False):
        self.typed.append(text)
        return self._outcome_cls(completed=True, aborted=False,
                                 chars_sent=len(text),
                                 chars_total=len(text))


class _VisionPatches:
    """Context manager patching the production vision legs."""

    def __init__(self, read_text=None, ready=True):
        self.read_text = read_text
        self.ready = ready
        self._patches = []

    def __enter__(self):
        from core.desktop import control_system as cs

        self._patches = [
            mock.patch.object(
                vb, "vision_status",
                return_value=_vision_ready() if self.ready
                else _vision_not_ready()),
            mock.patch.object(
                cs, "_capture_screen_bytes", return_value=_SHOT),
        ]
        if self.read_text is not None:
            self._patches.append(mock.patch.object(
                vb, "read_page",
                return_value={"ok": True, "text": self.read_text}))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _paste_patches():
    from core.desktop import control as hub

    return [
        mock.patch.dict(sys.modules, {
            "pyperclip": types.SimpleNamespace(copy=lambda t: None)}),
        mock.patch.object(hub, "press_hotkey",
                          return_value="Pressed hotkey: ctrl+v"),
        mock.patch("core.input_guard.typing_blocked_reason",
                   return_value=None),
    ]


class HandlerPasteTextTests(unittest.TestCase):
    def test_paste_text_vision_confirms_is_verified(self):
        from core import skills_network_display_input as mod

        patches = _paste_patches()
        for p in patches:
            p.start()
        self.addCleanup(
            lambda: [pp.stop() for pp in reversed(patches)])
        with _VisionPatches(read_text="hello world, pasted line"):
            out = mod.paste_text("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")
        self.assertIn("Pasted", str(out))
        self.assertIn("Vision check: the text is on screen", str(out))

    def test_paste_text_vision_absent_is_landing_failed(self):
        from core import skills_network_display_input as mod

        patches = _paste_patches()
        for p in patches:
            p.start()
        self.addCleanup(
            lambda: [pp.stop() for pp in reversed(patches)])
        with _VisionPatches(read_text="empty document, nothing here"):
            out = mod.paste_text("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")
        self.assertIn("Pasted", str(out))
        self.assertIn("did not land", str(out))

    def test_paste_text_vision_unavailable_stays_unverified(self):
        # Lab: no weights on disk. The receipt is exactly the
        # pre-B13 honestly-unverified one — same message, same kind.
        from core import skills_network_display_input as mod

        patches = _paste_patches()
        for p in patches:
            p.start()
        self.addCleanup(
            lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.paste_text("hello")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Pasted", str(out))


class HandlerTypeTextTests(unittest.TestCase):
    def _type(self, text, read_text=None):
        from core.desktop import control_input as ci
        from core.desktop import uia_send

        fake_type = _FakeSendType()
        patches = [
            mock.patch.object(uia_send, "_sendinput_type", fake_type),
            mock.patch.object(ci, "_routed_backend", return_value=None),
            mock.patch.object(ci, "_input_blocked", return_value=None),
            mock.patch.object(ci, "_password_block", return_value=None),
            mock.patch.object(ci, "_expectation_refusal",
                              return_value=None),
            mock.patch.object(ci, "_foreground_title",
                              return_value="Notepad"),
            mock.patch.object(ci, "_foreground_is_shell",
                              return_value=False),
            mock.patch.object(ci, "_last_input_tick", return_value=None),
            mock.patch.object(ci, "_is_windows", return_value=True),
        ]
        for p in patches:
            p.start()
        self.addCleanup(
            lambda: [pp.stop() for pp in reversed(patches)])
        if read_text is None:
            out = ci.type_text(text)
        else:
            with _VisionPatches(read_text=read_text):
                out = ci.type_text(text)
        return out, fake_type

    def test_type_text_vision_confirms_is_verified(self):
        out, fake_type = self._type("hi", read_text="hi there")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertTrue(out.verified)
        self.assertEqual(out.kind, "verified")
        self.assertIn("Typed 2 character(s)", str(out))
        self.assertIn("Notepad", str(out))
        self.assertEqual(fake_type.typed, ["hi"])

    def test_type_text_vision_absent_is_landing_failed(self):
        out, fake_type = self._type(
            "hi", read_text="empty document")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "landing_failed")
        self.assertEqual(fake_type.typed, ["hi"])

    def test_type_text_vision_unavailable_stays_unverified(self):
        out, fake_type = self._type("hi")
        self.assertTrue(outcome_mod.is_outcome(out))
        self.assertFalse(out.verified)
        self.assertEqual(out.kind, "unverified")
        self.assertIn("Typed 2 character(s)", str(out))
        self.assertEqual(fake_type.typed, ["hi"])


class HandlerTypeClipboardPassthroughTests(unittest.TestCase):
    def test_outcome_from_inner_type_text_is_returned_as_is(self):
        # B13 nesting rule: the inner type_text already ran the
        # landing check on this same text and screen — an Outcome
        # from it carries that verdict, so type_clipboard returns
        # it as-is and never checks twice.
        from core import skills_hands3_capture_scroll as mod
        from core.desktop import control as hub

        verdict = outcome_mod.verified("Typed 5 character(s) into "
                                       "the focused app.",
                                       "vision: text on screen")
        patches = [
            mock.patch.dict(sys.modules, {
                "pyperclip": types.SimpleNamespace(paste=lambda: "hello")}),
            mock.patch.object(hub, "type_text", return_value=verdict),
            mock.patch.object(mod, "_input_blocked", return_value=None),
        ]
        for p in patches:
            p.start()
        self.addCleanup(
            lambda: [pp.stop() for pp in reversed(patches)])
        out = mod.type_clipboard()
        self.assertIs(out, verdict)


class HandlerSourcePinTests(unittest.TestCase):
    def _src(self, *parts):
        return (_TREEROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_seven_wired_handlers_call_the_helper(self):
        # Counts per file: type_clipboard and paste_text each call
        # the helper at two return sites (full + capped/truncated);
        # the other five call it once.
        cases = [
            (("core", "desktop", "control_plus_click.py"), 1),
            (("core", "desktop", "control_plus.py"), 1),
            (("core", "desktop", "control_input.py"), 1),
            (("core", "skills_hands3_capture_scroll.py"), 2),
            (("core", "skills_network_display_input.py"), 2),
            (("core", "skills_productivity.py"), 1),
            (("core", "skills_hands3_window_manip.py"), 1),
        ]
        for parts, expected in cases:
            with self.subTest(handler=parts[-1]):
                src = self._src(*parts)
                self.assertEqual(
                    src.count("hands_landing_check"), expected,
                    f"{parts[-1]} must call the helper {expected} "
                    f"time(s)")

    def test_wired_handlers_pass_the_declared_expectation(self):
        src = self._src("core", "desktop", "control_plus_click.py")
        self.assertIn('hands_landing_check("click", text', src)
        src = self._src("core", "desktop", "control_plus.py")
        self.assertIn('hands_landing_check(\n        "click", '
                      'facts["name"]', src)
        src = self._src("core", "desktop", "control_input.py")
        self.assertIn('hands_landing_check(\n            "type", text,',
                      src)
        src = self._src("core", "skills_network_display_input.py")
        self.assertIn('hands_landing_check(\n            "paste", content,',
                      src)
        src = self._src("core", "skills_productivity.py")
        self.assertIn('hands_landing_check(\n        "paste", text,', src)
        src = self._src("core", "skills_hands3_window_manip.py")
        self.assertIn('hands_landing_check(\n            "drag_file",',
                      src)

    def test_nine_unwired_hands_tools_have_no_landing_leg(self):
        # Per-file counts pin the unwired ones too: control_input
        # holds type_text (1) and nothing else; the capture/scroll
        # file holds type_clipboard (1) and not click_and_drag_path.
        cases = [
            (("core", "desktop", "cursor_control.py"), 0),
            (("core", "desktop", "uia.py"), 0),
            (("core", "desktop", "control_plus_desktop.py"), 0),
            (("core", "desktop", "control_system.py"), 0),
            (("core", "desktop", "browser_interact.py"), 0),
            (("core", "desktop", "control_input.py"), 1),
            (("core", "skills_hands3_capture_scroll.py"), 2),
        ]
        for parts, expected in cases:
            with self.subTest(handler=parts[-1]):
                src = self._src(*parts)
                self.assertEqual(src.count("hands_landing_check"),
                                 expected)
        # press_hotkey / mouse_control live in control_input.py —
        # their own function segments must not call the helper.
        src = self._src("core", "desktop", "control_input.py")
        for name in ("def press_hotkey", "def mouse_control"):
            seg = src[src.index(name):]
            try:
                seg = seg[:seg.index("\ndef ", 1)]
            except ValueError:
                pass  # last function in the file — runs to EOF
            self.assertNotIn("hands_landing_check", seg)


# ---------------------------------------------------------------------------
# 3. dispatch carries outcome_kind
# ---------------------------------------------------------------------------

class DispatchOutcomeKindTests(unittest.TestCase):
    def setUp(self):
        ops_mod.set_store({})
        self._bp = mock.patch.object(
            registry_mod, "_budget_check", lambda name, is_read: None)
        self._bp2 = mock.patch.object(
            dispatch_mod, "_budget_check", lambda name, is_read: None)
        self._bp.start()
        self._bp2.start()

    def tearDown(self):
        self._bp2.stop()
        self._bp.stop()
        ops_mod.reset_store()

    def _register(self, name, handler):
        reg = registry_mod.ToolRegistry()
        reg.register(name, "probe",
                     {"type": "object", "properties": {}}, handler)
        return reg

    def test_landing_failed_outcome_carries_kind(self):
        reg = self._register(
            "b13_probe_landing_failed", lambda **kw:
            outcome_mod.Outcome(
                kind="landing_failed", verified=False,
                message="Clicked 'Save' but it did not land.",
                evidence="vision read: no save dialog"))
        result = reg.execute("b13_probe_landing_failed", {})
        self.assertEqual(result.get("status"), "success")
        self.assertFalse(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_kind"), "landing_failed")
        self.assertIn("did not land", result.get("result", ""))

    def test_verified_outcome_carries_kind_verified(self):
        reg = self._register(
            "b13_probe_verified", lambda **kw: outcome_mod.verified(
                "did it", "vision: text on screen"))
        result = reg.execute("b13_probe_verified", {})
        self.assertTrue(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_kind"), "verified")

    def test_unverified_outcome_carries_kind_unverified(self):
        reg = self._register(
            "b13_probe_unverified", lambda **kw: outcome_mod.unverified(
                "tried, unconfirmed", "vision backend not ready"))
        result = reg.execute("b13_probe_unverified", {})
        self.assertFalse(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_kind"), "unverified")

    def test_failed_outcome_carries_kind_failed(self):
        reg = self._register(
            "b13_probe_failed", lambda **kw: outcome_mod.failed(
                "Could not confirm: no file", "no file"))
        result = reg.execute("b13_probe_failed", {})
        self.assertFalse(result.get("outcome_verified"))
        self.assertEqual(result.get("outcome_kind"), "failed")

    def test_plain_string_handler_gets_no_outcome_kind(self):
        reg = self._register("b13_probe_plain",
                             lambda **kw: "plain old answer")
        result = reg.execute("b13_probe_plain", {})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"), "plain old answer")
        self.assertNotIn("outcome_verified", result)
        self.assertNotIn("outcome_kind", result)


# ---------------------------------------------------------------------------
# 4. coordinator: the landing verdict rides the step
# ---------------------------------------------------------------------------

def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(message="simulated failure"):
    return {"status": "failure", "message": message}


def _landing_failed(message="Clicked 'Save' but it did not land."):
    return {"status": "success", "result": message,
            "outcome_verified": False,
            "outcome_evidence": "vision read: no save dialog",
            "outcome_kind": "landing_failed"}


def _landing_unverified(message="Clicked 'Save' (landing not "
                                "confirmed — vision not ready)."):
    return {"status": "success", "result": message,
            "outcome_verified": False,
            "outcome_evidence": "vision backend is not ready",
            "outcome_kind": "unverified"}


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append(tool)
        results = self.script.get(tool)
        if not results:
            return _ok()
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _RecordingVerifier:
    """Per-expect scripted verdicts; records every full call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, expect_text, before, after, result_text):
        self.calls.append((expect_text, before, after, result_text))
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


class _Snapshot:
    def __call__(self):
        return {"fg": "fake", "windows": ["fake"], "texts": []}


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


class CoordinatorLandingTests(unittest.TestCase):
    def _coord(self, tmp, executor, verifier=None):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        coord = tc.TurnCoordinator(executor=executor, ledger=ledger,
                                   verifier=verifier,
                                   snapshot=_Snapshot())
        return coord, ledger

    def test_landing_failed_step_fails_the_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_failed()]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "click save", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(len(task.outcomes), 1)
            outcome = task.outcomes[0]
            self.assertEqual(outcome.status, "failure")
            self.assertEqual(outcome.kind, "landing_failed")
            self.assertIn("did not land", outcome.message)
            text = format_receipt(receipt)
            self.assertIn("did not land", text)

    def test_landing_failed_step_with_skip_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_failed()]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "click save", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save",
                        "on_fail": "skip"},
                       {"tool": "notify", "args": {},
                        "label": "tell the user"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(task.outcomes[0].status, "skipped")
            self.assertEqual(task.outcomes[0].kind, "landing_failed")
            self.assertEqual(executor.calls, ["click_save", "notify"])

    def test_landing_failed_step_jumps_to_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_failed()]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "click save", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save",
                        "on_fail": {"goto": 2}},
                       {"tool": "retry_click", "args": {},
                        "label": "click Save again"},
                       {"tool": "notify", "args": {},
                        "label": "tell the user"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls,
                             ["click_save", "retry_click", "notify"])
            self.assertEqual(len(receipt.jumps), 1)
            self.assertEqual(receipt.jumps[0]["from_index"], 0)
            self.assertEqual(receipt.jumps[0]["to_index"], 1)
            self.assertIn("did not land", receipt.jumps[0]["reason"])

    def test_unverified_landing_step_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_unverified()]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "click save", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(task.outcomes[0].status, "success")
            self.assertEqual(task.outcomes[0].kind, "unverified")

    def test_task_of_landing_failed_clicks_has_no_goal_met(self):
        # B10: the goal check runs only while the task is
        # completed. A task whose clicks never landed fails
        # first — it never gets a goal-met verdict.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_failed()]})
            coord, _ledger = self._coord(tmp, executor)
            task = coord.new_task(
                "file saved", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertIsNone(task._goal_verdict)
            self.assertEqual(task.outcomes[0].kind, "landing_failed")

    def test_skip_path_still_gets_the_goal_check(self):
        # The skipped click does not silently complete the goal:
        # the B10 goal check still judges the task and fails it
        # when the goal is not met.
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {"click_save": [_landing_failed()]})
            verifier = _RecordingVerifier(
                {"file saved": [("not-met", "no save observed")]})
            coord, _ledger = self._coord(tmp, executor, verifier)
            task = coord.new_task(
                "file saved", "routine",
                _steps({"tool": "click_save", "args": {},
                        "label": "click Save",
                        "on_fail": "skip"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(task._goal_verdict["verdict"], "not-met")
            self.assertIn("file saved", task._verify_error)


# ---------------------------------------------------------------------------
# 5. the R-C contract is recorded as still owed, not closed
# ---------------------------------------------------------------------------

class RCContractOwedTests(unittest.TestCase):
    def test_audit_records_rc_as_still_owed(self):
        doc = (_TREEROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        self.assertIn("R-C contract", doc)
        self.assertIn("still owed", doc)
        self.assertIn("B13 is the lab half only", doc)

    def test_helper_docstring_records_rc_as_still_owed(self):
        doc = inspect.getdoc(outcome_mod.hands_landing_check)
        self.assertIn("R-C", doc)
        self.assertIn("still owed", doc)


if __name__ == "__main__":
    unittest.main()
