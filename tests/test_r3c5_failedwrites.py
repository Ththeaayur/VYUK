"""Round-3 section C fix queue item 5 (C3-F5 + C3-F7, 2026-11-20).

C3-F5: ``write_file`` (``write_file_bytes``) wrote directly — on
ENOSPC mid-write a truncated partial file remained and the next
read presented it as the file. The fix writes atomically (temp
file in the same directory + ``os.replace``, the scheduler /
skills_text pattern), so a failed write leaves the old file
byte-identical (or no file at all for a new path). The same shape
is fixed in ``write_file_with_undo`` (the undo producer's writer).

C3-F7: the receipt-gate refusal said "Repair or rotate
data/tool_receipts.jsonl and retry" when the cause was ENOSPC;
rotating the file cannot help — freeing space is the remedy. The
fix names the cause-specific remedy (``receipt_failure_remedy``)
at both refusal sites (attempt + mark); any other cause keeps
the repair/rotate remedy, which is the right one there.

The mid-write failure is forced for real with RLIMIT_FSIZE in a
subprocess (SIGXFSZ ignored, so the write raises EFBIG partway
— the same code path as ENOSPC, no mounts needed). The snippets
run with PYTHONDONTWRITEBYTECODE=1 and cwd inside the temp dir:
a .pyc write under the 1000 B limit would leave a TORN .pyc in
the tree's __pycache__, and the next import of that module dies
with EOFError (marshal data too short) — the first neighbour run
hit exactly that in its scratch copy. The receipt
legs drive the real dispatch stages (the r3f1 ``_ExecuteContext``
pattern) with the sticky failure seeded, plus the pure remedy
helper. The end-to-end proof on a real full tmpfs lives in the
repro ``r3c5_failedwrites_work/repro_c3f5.py`` (REPRO-EXIT 1
pre-fix with exactly the 3 bug failures, REPRO-EXIT 0 post-fix).
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import tool_receipts as _receipts
from core.funnel.tools_registry import tools
from core.funnel.tools_registry_exec_setup import _ExecuteContext

TREE = pathlib.Path(__file__).resolve().parents[1]
OLD = b"OLD-CONTENT-LINE\n" * 100  # 1700 B, the pre-existing file
BIG = "X" * 200000

_RLIMIT_SNIPPET = """\
import pathlib, resource, signal, sys
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (1000, 1000))
{setup}
from core.funnel.tools_handlers_data_services import write_file_bytes as w
p = pathlib.Path({path!r})
BIG = "X" * 200000
{call}
print("verified:", r.verified)
print("message:", str(r))
print("exists:", p.exists())
print("size:", p.stat().st_size if p.exists() else None)
print("sha:", __import__("hashlib").sha256(p.read_bytes()).hexdigest() if p.exists() else None)
print("temps:", sorted(x.name for x in pathlib.Path({dir!r}).glob("*.tmp")))
"""

_UNDO_SNIPPET = """\
import os, pathlib, sys
from unittest import mock
from core.resilience import resilience_gate as gate
from core import undo_producers_p1 as prod
gate.reset_for_tests()
prod.reset_producers_for_tests()
gate.configure_undo(os.path.join({tmp!r}, "undo.json"))
prod.ensure_producers_registered()
{inject}
p = pathlib.Path({path!r})
r = prod.write_file_with_undo(str(p), "X" * 200000)
print("result:", r)
print("exists:", p.exists())
print("size:", p.stat().st_size if p.exists() else None)
print("sha:", __import__("hashlib").sha256(p.read_bytes()).hexdigest() if p.exists() else None)
print("temps:", sorted(x.name for x in pathlib.Path({dir!r}).glob("*.tmp")))
"""

_INJECT_REPLACE_FAIL = """\
_real_replace = os.replace
def _failing_replace(src, dst):
    raise OSError(28, "No space left on device")
os.replace = _failing_replace
"""

_INJECT_WRITE_FAIL = """\
_real_fdopen = os.fdopen
class _FailingWriter:
    def __init__(self, fh):
        self._fh = fh
    def write(self, data):
        self._fh.write(data[:1000])
        raise OSError(28, "No space left on device")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self._fh.close()
        return False
    def __getattr__(self, name):
        return getattr(self._fh, name)
os.fdopen = lambda fd, *a, **k: _FailingWriter(_real_fdopen(fd, *a, **k))
"""


def _run_snippet(code, cwd=None):
    """Run a snippet in a fresh interpreter against the tree.

    PYTHONDONTWRITEBYTECODE is set: the RLIMIT legs run under a
    1000 B file-size limit, and a .pyc write under that limit
    would leave a TORN .pyc in the tree's __pycache__ — the next
    import of that module then dies with EOFError (marshal data
    too short). That is exactly what the first neighbour run hit
    in its scratch copy. The snippets never need bytecode caches.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{TREE}/.pkgroot:{TREE}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(cwd or TREE), env=env,
        capture_output=True, text=True, timeout=120)
    return proc


def _make_ctx(name, args):
    """A real _ExecuteContext, built the way execute() builds it
    (the r3f1 pattern)."""
    ctx = _ExecuteContext()
    ctx.name = name
    ctx.args = args
    ctx.cancel_token = None
    ctx.audit_gate = None
    ctx.audited = None
    ctx.epoch_void = None
    ctx.obs_failures = []
    ctx.obs_sink_failures = {}
    ctx.receipt_attempt = None
    ctx.receipt_error = False
    ctx.receipt_is_read = False
    ctx.mark_dispatched_cb = None
    ctx._human_approved_this_call = False
    ctx.rot_legs = set()
    ctx.deadline = None
    ctx._mcp_entry = None
    ctx._mcp_server = None
    ctx._mcp_tool = None
    ctx._mcp_class = None
    ctx._mcp_bus = None
    return ctx


class TestWriteFileAtomic(unittest.TestCase):
    """write_file_bytes under a real mid-write failure."""

    def test_overwrite_failure_leaves_old_file_intact(self):
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            p = pathlib.Path(tmp) / "big.txt"
            p.write_bytes(OLD)
            import hashlib
            old_sha = hashlib.sha256(OLD).hexdigest()
            code = _RLIMIT_SNIPPET.format(
                setup="", path=str(p), big=BIG, dir=tmp,
                call="r = w(str(p), BIG)")
            proc = _run_snippet(code, cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("verified: False", proc.stdout, proc.stdout)
            self.assertIn("size: 1700", proc.stdout, proc.stdout)
            self.assertIn(f"sha: {old_sha}", proc.stdout, proc.stdout)
            self.assertIn("temps: []", proc.stdout, proc.stdout)

    def test_new_file_failure_leaves_no_file(self):
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            p = pathlib.Path(tmp) / "new.txt"
            code = _RLIMIT_SNIPPET.format(
                setup="", path=str(p), big=BIG, dir=tmp,
                call="r = w(str(p), BIG)")
            proc = _run_snippet(code, cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("verified: False", proc.stdout, proc.stdout)
            self.assertIn("exists: False", proc.stdout, proc.stdout)
            self.assertIn("temps: []", proc.stdout, proc.stdout)

    def test_append_failure_leaves_old_content_intact(self):
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            p = pathlib.Path(tmp) / "log.txt"
            p.write_bytes(OLD)
            import hashlib
            old_sha = hashlib.sha256(OLD).hexdigest()
            code = _RLIMIT_SNIPPET.format(
                setup="", path=str(p), big=BIG, dir=tmp,
                call="r = w(str(p), BIG, append=True)")
            proc = _run_snippet(code, cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("verified: False", proc.stdout, proc.stdout)
            self.assertIn(f"sha: {old_sha}", proc.stdout, proc.stdout)
            self.assertIn("temps: []", proc.stdout, proc.stdout)

    def test_healthy_write_lands_complete(self):
        # Control: no limit, the write lands whole and verified.
        from core.funnel.tools_handlers_data_services import (
            write_file_bytes as w)
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            p = pathlib.Path(tmp) / "big.txt"
            r = w(str(p), BIG)
            self.assertTrue(r.verified, str(r))
            self.assertEqual(p.read_bytes(), b"X" * 200000)
            self.assertEqual(
                [x.name for x in pathlib.Path(tmp).glob("*.tmp")], [])

    def test_failure_is_loud_and_names_the_cause(self):
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            p = pathlib.Path(tmp) / "big.txt"
            p.write_bytes(OLD)
            code = _RLIMIT_SNIPPET.format(
                setup="", path=str(p), big=BIG, dir=tmp,
                call="r = w(str(p), BIG)")
            proc = _run_snippet(code, cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("Could not write file", proc.stdout, proc.stdout)
            self.assertIn("File too large", proc.stdout, proc.stdout)


class TestWriteFileWithUndoAtomic(unittest.TestCase):
    """The undo producer's writer has the same shape."""

    def test_overwrite_failure_leaves_old_file_intact(self):
        # Two failure points, both injected: the temp write dying
        # mid-way, and the replace itself failing. (RLIMIT_FSIZE
        # cannot be used here — the undo record holds the full new
        # bytes, so the limit would break the record before the
        # write it guards.)
        for inject in (_INJECT_WRITE_FAIL, _INJECT_REPLACE_FAIL):
            with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
                p = pathlib.Path(tmp) / "big.txt"
                p.write_bytes(OLD)
                import hashlib
                old_sha = hashlib.sha256(OLD).hexdigest()
                code = _UNDO_SNIPPET.format(
                    tmp=tmp, path=str(p), dir=tmp, inject=inject)
                proc = _run_snippet(code, cwd=tmp)
                self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
                self.assertIn("'ok': False", proc.stdout, proc.stdout)
                self.assertIn("file write failed", proc.stdout,
                              proc.stdout)
                self.assertIn(f"sha: {old_sha}", proc.stdout,
                              proc.stdout)
                self.assertIn("temps: []", proc.stdout, proc.stdout)

    def test_healthy_undo_write_still_lands(self):
        # Control: the atomic path did not break the producer.
        from core.resilience import resilience_gate as gate
        from core import undo_producers_p1 as prod
        with tempfile.TemporaryDirectory(prefix="c3f5_") as tmp:
            gate.reset_for_tests()
            prod.reset_producers_for_tests()
            try:
                gate.configure_undo(os.path.join(tmp, "undo.json"))
                prod.ensure_producers_registered()
                p = pathlib.Path(tmp) / "note.txt"
                r = prod.write_file_with_undo(str(p), "hello\n")
                self.assertTrue(r["ok"], r)
                self.assertEqual(p.read_bytes(), b"hello\n")
            finally:
                gate.reset_for_tests()
                prod.reset_producers_for_tests()


class TestReceiptRemedy(unittest.TestCase):
    """C3-F7: the refusal names the remedy for its own cause."""

    def setUp(self):
        _receipts.reset_receipt_degraded()

    def tearDown(self):
        _receipts.reset_receipt_degraded()

    def test_enospc_names_freeing_disk_space(self):
        _receipts._note_failure(
            "attempt", OSError(28, "No space left on device"))
        remedy = _receipts.receipt_failure_remedy("attempt")
        self.assertIn("disk space", remedy.lower())
        self.assertNotIn("Repair or rotate", remedy)

    def test_mark_enospc_names_freeing_disk_space(self):
        _receipts._note_failure(
            "mark", OSError(28, "No space left on device"))
        remedy = _receipts.receipt_failure_remedy("mark")
        self.assertIn("disk space", remedy.lower())
        self.assertNotIn("Repair or rotate", remedy)

    def test_corrupt_receipts_keep_repair_or_rotate(self):
        _receipts._note_failure(
            "attempt", ValueError("not a valid receipts file"))
        remedy = _receipts.receipt_failure_remedy("attempt")
        self.assertIn("Repair or rotate", remedy)
        self.assertNotIn("disk space", remedy.lower())

    def test_unknown_cause_keeps_repair_or_rotate(self):
        remedy = _receipts.receipt_failure_remedy("attempt")
        self.assertIn("Repair or rotate", remedy)

    def test_attempt_refusal_names_disk_space_on_enospc(self):
        ctx = _make_ctx("remember_user_fact",
                        {"key": "c3f5", "value": "v"})
        tools._exec_setup(ctx)
        _receipts._note_failure(
            "attempt", OSError(28, "No space left on device"))
        ctx._receipt_error = True
        ctx._receipt_is_read = False
        result = tools._exec_receipt_check(ctx)
        self.assertIsNotNone(result, "expected a refusal")
        msg = str(result.get("message", ""))
        self.assertIn("Free up disk space", msg, msg)
        self.assertNotIn("Repair or rotate", msg, msg)
        self.assertEqual(ctx._audit_gate["value"],
                         "refused:receipt-unavailable")

    def test_attempt_refusal_keeps_repair_or_rotate_on_corruption(self):
        ctx = _make_ctx("remember_user_fact",
                        {"key": "c3f5", "value": "v"})
        tools._exec_setup(ctx)
        _receipts._note_failure(
            "attempt", ValueError("not a valid receipts file"))
        ctx._receipt_error = True
        ctx._receipt_is_read = False
        result = tools._exec_receipt_check(ctx)
        self.assertIsNotNone(result, "expected a refusal")
        msg = str(result.get("message", ""))
        self.assertIn("Repair or rotate", msg, msg)
        self.assertNotIn("disk space", msg.lower(), msg)

    def test_read_call_is_not_refused_by_receipt_error(self):
        ctx = _make_ctx("recall_memories", {"query": "c3f5"})
        tools._exec_setup(ctx)
        ctx._receipt_error = True
        ctx._receipt_is_read = True
        self.assertIsNone(tools._exec_receipt_check(ctx))

    def test_mark_refusal_names_disk_space_on_enospc(self):
        ctx = _make_ctx("remember_user_fact",
                        {"key": "c3f5", "value": "v"})
        tools._exec_setup(ctx)
        ctx._receipt_is_read = False
        ctx._receipt_attempt = {"receipt_id": "r-c3f5-test"}
        _receipts._note_failure(
            "mark", OSError(28, "No space left on device"))
        with mock.patch.object(
                _receipts, "try_mark_dispatched",
                return_value=False):
            result = tools._exec_mark_dispatched(ctx)
        self.assertIsNotNone(result, "expected a refusal")
        msg = str(result.get("message", ""))
        self.assertIn("Free up disk space", msg, msg)
        self.assertNotIn("Repair or rotate", msg, msg)

    def test_mark_refusal_keeps_repair_or_rotate_otherwise(self):
        ctx = _make_ctx("remember_user_fact",
                        {"key": "c3f5", "value": "v"})
        tools._exec_setup(ctx)
        ctx._receipt_is_read = False
        ctx._receipt_attempt = {"receipt_id": "r-c3f5-test"}
        _receipts._note_failure(
            "mark", ValueError("receipt store unreadable"))
        with mock.patch.object(
                _receipts, "try_mark_dispatched",
                return_value=False):
            result = tools._exec_mark_dispatched(ctx)
        self.assertIsNotNone(result, "expected a refusal")
        msg = str(result.get("message", ""))
        self.assertIn("Repair or rotate", msg, msg)


if __name__ == "__main__":
    unittest.main()
