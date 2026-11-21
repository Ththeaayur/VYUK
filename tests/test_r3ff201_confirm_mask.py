"""Round-3 section F fix queue item 3 (F-F201): confirm ask masks the
decision-critical path.

The bug (section F, parent-confirmed from the live code):
``summarize_args`` in ``core/confirm_gate_scrub.py`` checked
``_looks_like_secret(value)`` BEFORE the FULL_FIDELITY_KEYS branch,
and the entropy branch (no spaces, 20-512 chars, Shannon >= 4.2
bits/char, charset >= 3) fires on ordinary dated/versioned
filenames — ``/home/hatch/Downloads/report-final-v2.docx``,
``IMG_20261121_153022.jpg`` and
``C:\\Users\\Aayur\\Downloads\\photo-20261121-153022.jpg`` all read
secret-like, so the ask showed ``filepath=•••`` and the human
approved file writes blind. The docstring claim ("Ordinary prose,
paths and short values never match") was false for exactly those
filenames.

The fix (three parts, one file — ``core/confirm_gate_scrub.py``):
1. Precedence in ``summarize_args`` now mirrors the audit module's
   (``core/audit.py`` ``_PATH_KEYS``): an exact sensitive key name
   masks first, then decision-critical keys (FULL_FIDELITY_KEYS +
   caller ``verbatim_keys``) show IN FULL even when their shape
   reads secret-like, and only then does the secret shape mask.
2. FULL_FIDELITY_KEYS gains the registry's path/URL-valued arg
   keys (``output_path`` alone is used by 36 tools) — derived from
   ``tools.get_declarations()``; the non-path lookalikes
   (``direction``, ``file_type``, ``hourly``, ``target_price``,
   ``target_norm1000``, ``source_lang``, ...) are deliberately NOT
   in the set.
3. ``_looks_like_secret``'s entropy branch exempts values that are
   plainly paths or URLs (``_looks_like_path``) — the known-prefix,
   hex-run and base64-run branches still fire first, so a genuine
   32+ char token still masks.

Harness: the pure tests run on both interpreters; the funnel legs
drive ``TOOLS.execute`` through the VA funnel (F2 shape) with a
deny handler that captures the ask summary — nothing runs. The
headline tests need Qt (``window_services_confirm`` imports
PySide6) and are skipped on sys. Data is snapshotted in
setUpClass and restored in tearDownClass; receipt rows go to a
temp store per test (the C2-F2 idiom).
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
from voice_assistant.core import confirm_gate_scrub as va_scrub  # noqa: E402
from voice_assistant.core import tool_receipts as va_receipts  # noqa: E402
from voice_assistant.core.funnel import tools_registry as va_reg  # noqa: E402
import core.confirm_gate as bare_gate  # noqa: E402
from core import injection as _inj  # noqa: E402
from core.funnel import rule_of_two as _rot  # noqa: E402

try:
    from voice_assistant.gui.window_services_confirm import (
        _confirm_headline, _confirm_target_phrase)  # noqa: E402
    _HEADLINE_OK = True
except Exception:  # noqa: BLE001 — no Qt under sys python
    _HEADLINE_OK = False

DATED_DOCX = "/home/hatch/Downloads/report-final-v2.docx"
DATED_JPG = "/home/hatch/Downloads/IMG_20261121_153022.jpg"
WIN_PATH = r"C:\Users\Aayur\Downloads\photo-20261121-153022.jpg"
DATED_JSON = "/home/hatch/Downloads/draft-final-v2.json"

# The full decision-critical key set, hardcoded (not read off the
# module): on the pre-fix tree the 33 F-F201 keys are absent, so
# this test must fail there rather than silently testing fewer keys.
EXPECTED_FULL_FIDELITY_KEYS = {
    "path", "filepath", "file", "filename", "file_name",
    "target", "destination", "dest", "source", "folder",
    "directory", "dir", "command", "url", "app", "app_name",
    "application", "link", "address", "to", "recipient",
    "output_path", "input_path", "image_path", "audio_path",
    "save_path", "target_path", "repo_path", "store_path",
    "ledger_path", "list_file", "output_dir", "output_folder",
    "save_dir", "working_dir", "working_directory",
    "workspace_dir", "data_dir", "profile_dir", "sandbox_dir",
    "art_dir", "wav_dir", "files_root", "folder_a", "folder_b",
    "path_a", "path_b", "cancel_url", "licence_url",
    "snapshot_url", "success_url", "paths", "folders", "urls",
}


def _fresh_state():
    """Clear session taint + Rule-of-Two legs between calls (C2-F2 idiom)."""
    _inj.clear_taint(force=True)
    _rot.reset_for_tests()


class TestSummarizePrecedence(unittest.TestCase):
    """Pure summarize_args / _looks_like_secret behaviour."""

    def test_all_full_fidelity_keys_show_dated_path_in_full(self):
        missing = EXPECTED_FULL_FIDELITY_KEYS - set(va_scrub.FULL_FIDELITY_KEYS)
        self.assertEqual(missing, set(),
                         f"keys missing from FULL_FIDELITY_KEYS: {sorted(missing)}")
        for key in sorted(EXPECTED_FULL_FIDELITY_KEYS):
            with self.subTest(key=key):
                out = va_scrub.summarize_args({key: DATED_DOCX})
                self.assertIn("report-final-v2.docx", out,
                              f"{key}: dated path not shown in full: {out!r}")
                self.assertNotIn("•••", out,
                                 f"{key}: dated path masked: {out!r}")

    def test_windows_dated_path_shows_full(self):
        out = va_scrub.summarize_args({"filepath": WIN_PATH})
        self.assertIn("photo-20261121-153022.jpg", out, f"out={out!r}")
        self.assertNotIn("•••", out, f"out={out!r}")

    def test_sensitive_key_outranks_critical_shape(self):
        # A value under an exact sensitive key name masks whatever it
        # looks like — even a value that is itself a path.
        out = va_scrub.summarize_args({"token": DATED_DOCX})
        self.assertIn("token=•••", out, f"out={out!r}")
        self.assertNotIn("report-final-v2.docx", out, f"out={out!r}")
        out = va_scrub.summarize_args(
            {"api_key": "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"})
        self.assertIn("api_key=•••", out, f"out={out!r}")
        self.assertNotIn("sk-proj-", out, f"out={out!r}")

    def test_genuine_secrets_still_mask(self):
        cases = [
            ({"password": "hunter2"}, "hunter2"),
            ({"token": "xoxb-1234567890-abcdefghij"}, "xoxb-"),
            ({"content": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW"}, "aB3dE5fG"),
            ({"note": "9f86d081884c7d659a2feaa0c55ad015"}, "9f86d081"),
            ({"blob": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vWxyz1234567890"},
             "aB3dE5fG"),
        ]
        for args, secret in cases:
            with self.subTest(args=args):
                out = va_scrub.summarize_args(args)
                key = next(iter(args))
                self.assertIn(f"{key}=•••", out, f"out={out!r}")
                self.assertNotIn(secret, out, f"out={out!r}")

    def test_path_shape_exemption_under_generic_key(self):
        # A dated path under a non-listed key is not MASKED (the
        # entropy exemption) — it still clips at 30 chars as a
        # non-critical field; full fidelity belongs to the
        # decision-critical keys, not to generic keys.
        out = va_scrub.summarize_args({"value": DATED_DOCX})
        self.assertNotIn("value=•••", out, f"out={out!r}")
        self.assertTrue(out.startswith("value=/home/hatch/"),
                        f"out={out!r}")

    def test_non_path_high_entropy_still_masks(self):
        out = va_scrub.summarize_args(
            {"note": "Xk9mQ2vR7tY4wZ1pL8sN3dF6hJ0aB5c"})
        self.assertIn("note=•••", out, f"out={out!r}")

    def test_looks_like_path_shapes(self):
        fn = getattr(va_scrub, "_looks_like_path", None)
        self.assertIsNotNone(fn, "_looks_like_path missing (F-F201)")
        cases = [
            ("/home/hatch/Downloads/report-final-v2.docx", True),
            ("/tmp/x.txt", True),
            (r"C:\Users\Aayur\Downloads\photo-20261121-153022.jpg", True),
            (r"\\server\share\report-final-v2.docx", True),
            ("~/Pictures/code-20261121.png", True),
            ("https://example.com/reports/annual-report-2026-final-v2.pdf",
             True),
            ("/abc", False),  # lone leading slash: token fragment
            ("sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", False),
            ("aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW", False),
            ("hello world", False),
            ("", False),
        ]
        for value, want in cases:
            with self.subTest(value=value):
                self.assertEqual(fn(value), want, f"value={value!r}")

    def test_verbatim_keys_win_over_secret_shape(self):
        out = va_scrub.summarize_args(
            {"custom": DATED_DOCX}, verbatim_keys={"custom"})
        self.assertIn("report-final-v2.docx", out, f"out={out!r}")
        self.assertNotIn("•••", out, f"out={out!r}")

    def test_empty_and_none(self):
        self.assertEqual(va_scrub.summarize_args(None), "(no arguments)")
        self.assertEqual(va_scrub.summarize_args({}), "(no arguments)")


class _FunnelBase(unittest.TestCase):
    """VA funnel + deny handler that captures the ask summary."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="r3ff201_")
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
        self._rtmp = tempfile.TemporaryDirectory(prefix="r3ff201_rx_")
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

    def _ask(self, tool, args):
        before = len(type(self).asked)
        va_reg.tools.execute(tool, args)
        asks = type(self).asked[before:]
        self.assertEqual(len(asks), 1,
                         f"{tool}: expected exactly one ask, got {asks!r}")
        return asks[0]["summary"]


class TestFunnelAskShowsPath(_FunnelBase):

    def test_write_file_dated_docx_ask_shows_path(self):
        summary = self._ask("write_file",
                            {"filepath": str(self.out / "report-final-v2.docx"),
                             "content": "F-F201 probe"})
        self.assertIn("report-final-v2.docx", summary, f"summary={summary!r}")
        self.assertNotIn("filepath=•••", summary, f"summary={summary!r}")

    def test_write_file_dated_jpg_and_windows_path_ask_shows_path(self):
        summary = self._ask(
            "write_file",
            {"filepath": str(self.out / "IMG_20261121_153022.jpg"),
             "content": "F-F201 probe"})
        self.assertIn("IMG_20261121_153022.jpg", summary,
                      f"summary={summary!r}")
        self.assertNotIn("filepath=•••", summary, f"summary={summary!r}")
        summary = self._ask("write_file",
                            {"filepath": WIN_PATH, "content": "F-F201 probe"})
        self.assertIn("photo-20261121-153022.jpg", summary,
                      f"summary={summary!r}")
        self.assertNotIn("filepath=•••", summary, f"summary={summary!r}")

    def test_download_destination_ask_shows_path_and_url(self):
        summary = self._ask(
            "download_file",
            {"url": "https://example.com/report-final-v2.docx",
             "destination": str(self.out / "report-final-v2.docx")})
        self.assertIn("report-final-v2.docx", summary, f"summary={summary!r}")
        self.assertNotIn("destination=•••", summary, f"summary={summary!r}")
        self.assertNotIn("url=•••", summary, f"summary={summary!r}")

    def test_file_control_delete_and_copy_ask_shows_paths(self):
        summary = self._ask(
            "file_control",
            {"action": "delete",
             "path": str(self.out / "report-final-v2.docx")})
        self.assertIn("report-final-v2.docx", summary, f"summary={summary!r}")
        self.assertNotIn("path=•••", summary, f"summary={summary!r}")
        summary = self._ask(
            "file_control",
            {"action": "copy",
             "path": str(self.out / "report-final-v2.docx"),
             "destination": str(self.out / "IMG_20261121_153022.jpg")})
        self.assertIn("report-final-v2.docx", summary, f"summary={summary!r}")
        self.assertIn("IMG_20261121_153022.jpg", summary,
                      f"summary={summary!r}")

    def test_plain_short_path_control(self):
        summary = self._ask("write_file",
                            {"filepath": "/home/hatch/Desktop/notes.txt",
                             "content": "F-F201 probe"})
        self.assertIn("notes.txt", summary, f"summary={summary!r}")


@unittest.skipUnless(_HEADLINE_OK, "headline needs Qt (PySide6)")
class TestHeadline(unittest.TestCase):
    """E-F304 headline still reads the point from the summary."""

    def test_headline_click_names_point(self):
        summary = va_scrub.summarize_args(
            {"action": "click", "x": 640, "y": 400})
        self.assertEqual(_confirm_headline("mouse_control", summary),
                         "This will click at 640, 400.")

    def test_headline_scroll_names_point(self):
        summary = va_scrub.summarize_args(
            {"action": "scroll", "x": 640, "y": 400, "amount": 3})
        self.assertEqual(_confirm_headline("mouse_control", summary),
                         "This will scroll up at 640, 400.")

    def test_headline_click_nocoord_unchanged(self):
        summary = va_scrub.summarize_args({"action": "click"})
        self.assertEqual(
            _confirm_headline("mouse_control", summary),
            "This will click where the mouse pointer is.")

    def test_delete_target_phrase_names_file(self):
        # Pre-fix the masked path made this None and the headline
        # fell back to generic; post-fix it names the file.
        summary = va_scrub.summarize_args(
            {"action": "delete", "path": DATED_DOCX})
        self.assertEqual(_confirm_target_phrase(summary),
                         "the file report-final-v2.docx")


if __name__ == "__main__":
    unittest.main()
