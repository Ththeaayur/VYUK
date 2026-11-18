"""Journey 5 — doctor path (whole-path phase).

The full doctor runs clean on UTF-8 and on a simulated cp1252
console: it completes, prints an honest SUMMARY tally, and never
crashes on an unencodable character. The defect-batch suite proved
report() + check_watchers/check_world under cp1252; this journey
proves the WHOLE run (every check in _CHECKS, in order, through
main()) on both consoles.

What "clean" means here, honestly: the run COMPLETES and its
SUMMARY tally is parseable. It does NOT mean zero FAILs — on this
Linux lab several checks fail or warn legitimately (Windows-only
features, no mic, optional services), exactly as the field run on
the owner's Windows machine reported its own mix. A FAIL verdict is
an honest report, not a crash; a crash (an exception escaping
main()) is what fails this journey.

Stdlib unittest only. Doctor checks read the tree; the whole-data
guard snapshots/restores data/ regardless.
"""

from __future__ import annotations

import contextlib
import io
import re
import unittest

try:
    import doctor as doctor_mod
    from tests._journey_base import DataGuard
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"doctor unavailable: {exc}")


class _StrictCp1252Stream:
    """A cp1252 console that CANNOT reconfigure (the field console).

    write() encodes strict, so '→' raises UnicodeEncodeError and
    doctor._emit's replace-fallback must engage. No reconfigure
    method: doctor._make_output_encoding_safe tolerates that.
    """

    encoding = "cp1252"
    errors = "strict"

    def __init__(self):
        self.raw = io.BytesIO()

    def write(self, text):
        self.raw.write(text.encode("cp1252", errors="strict"))
        return len(text)

    def flush(self):
        pass


class TestDoctorJourney(unittest.TestCase):
    def setUp(self):
        self._guard = DataGuard()
        self._guard.__enter__()
        self.addCleanup(self._guard.__exit__, None, None, None)

    def _run_main(self, stdout):
        err = io.StringIO()
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(err):
            return doctor_mod.main()

    def _assert_summary(self, text):
        match = re.search(
            r"SUMMARY: (\d+) PASS · (\d+) WARN · (\d+) FAIL", text)
        self.assertIsNotNone(match, f"no honest SUMMARY tally in:\n{text[-500:]}")
        total = sum(int(g) for g in match.groups())
        self.assertGreater(total, 0, "empty tally")

    def test_full_run_completes_utf8(self):
        buf = io.StringIO()
        rc = self._run_main(buf)
        self.assertIsInstance(rc, int)
        self._assert_summary(buf.getvalue())

    def test_full_run_completes_strict_cp1252(self):
        stream = _StrictCp1252Stream()
        rc = self._run_main(stream)
        self.assertIsInstance(rc, int)
        raw = stream.raw.getvalue()
        self.assertNotIn("→".encode("utf-8"), raw,
                         "a UTF-8 arrow leaked into the cp1252 stream")
        self._assert_summary(raw.decode("cp1252"))


if __name__ == "__main__":
    unittest.main()
