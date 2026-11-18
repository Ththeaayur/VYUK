"""Post-triage defect batch, item B — doctor output encoding safety.

Regression test for the field crash (shipped v6.0.0, owner's
Windows machine): doctor.report() printed '→' (U+2192) to a cp1252
console, raising UnicodeEncodeError and aborting check_watchers
(the detail line: Settings → Notices) and check_world (the fix:
line: Settings → Your world). The fix has two layers: report()
falls back to replace-on-error output (_emit), and main()
reconfigures stdout/stderr to UTF-8 with errors='replace' where
the stream allows it (_make_output_encoding_safe).

The hostile console is simulated with a cp1252-strict
TextIOWrapper over BytesIO: printing '→' to it raises, exactly
like the field console. Stdlib only, no GUI, no network.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import sys
import unittest

_DOCTOR_PATH = pathlib.Path(__file__).resolve().parent.parent / "doctor.py"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("doctor_under_test", _DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _cp1252_stdout():
    """Swap sys.stdout for a strict cp1252 stream; yield its buffer.

    The wrapper is detached (not closed) on exit: closing it
    would close the underlying BytesIO, and the tests read the
    bytes after the block."""
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    old = sys.stdout
    sys.stdout = wrapper
    try:
        yield raw
    finally:
        try:
            wrapper.flush()
        except Exception:  # noqa: BLE001 — restore stdout regardless
            pass
        try:
            wrapper.detach()
        except Exception:  # noqa: BLE001 — restore stdout regardless
            pass
        sys.stdout = old


class TestDoctorUnicode(unittest.TestCase):
    """report() and both crashed checks complete on a cp1252 console."""

    @classmethod
    def setUpClass(cls):
        cls.doctor = _load_doctor()

    def test_report_with_arrows_under_cp1252(self):
        """The two exact field lines: detail line + fix: line."""
        with _cp1252_stdout() as raw:
            self.doctor.report(
                "PASS", "Ambient light (opt-in)",
                "off by default — when switched on in Settings → Notices, "
                "the camera opens briefly once an hour, only a brightness "
                "number is logged, and the frame is never stored or sent anywhere")
            self.doctor.report(
                "WARN", "Your world",
                "Google not connected yet (optional) — calendar and mail "
                "tools will say how to connect",
                "open Settings → Your world, paste your Google Cloud "
                "client ID/secret, and press Connect Google")
        text = raw.getvalue().decode("cp1252")
        # The arrows cannot survive cp1252; they must become '?',
        # and the lines themselves must still print.
        self.assertIn("Ambient light", text)
        self.assertIn("Your world", text)
        self.assertIn("Settings ? Notices", text)
        self.assertIn("Settings ? Your world", text)

    def test_checks_complete_under_cp1252(self):
        """check_watchers + check_world: no UnicodeEncodeError, real output."""
        with _cp1252_stdout() as raw:
            self.doctor.check_watchers()
            self.doctor.check_world()
        text = raw.getvalue().decode("cp1252")
        self.assertIn("Watchers", text)
        self.assertIn("Your world", text)
        self.assertTrue("[PASS]" in text or "[WARN]" in text or "[FAIL]" in text,
                        text)

    def test_make_output_encoding_safe(self):
        """Reconfigure is requested (UTF-8 + replace) and best-effort."""
        seen = {}

        class _FakeStream:
            def reconfigure(self, **kwargs):
                seen.update(kwargs)

        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = _FakeStream(), _FakeStream()
            self.doctor._make_output_encoding_safe()
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(seen.get("encoding"), "utf-8")
        self.assertEqual(seen.get("errors"), "replace")

        class _NoReconfigure:
            pass

        try:
            sys.stdout, sys.stderr = _NoReconfigure(), _NoReconfigure()
            self.doctor._make_output_encoding_safe()  # must not raise
        finally:
            sys.stdout, sys.stderr = old_out, old_err


if __name__ == "__main__":
    unittest.main()
