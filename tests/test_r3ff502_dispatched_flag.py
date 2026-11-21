"""Round-3 section F fix queue item 6 (F-F502): a denied outcome row
must not claim a dispatch that never happened.

The finding (section F, slice 5 legs O5/O10): a denied confirm-gated
call's outcome row reads "refused" per the pinned vocabulary —
honest — but carries "dispatched": true, although no
{"type": "dispatched"} record precedes it in the file and nothing
ran. The attempt row for the same receipt_id says dispatched: false.
Cause: the funnel's single outcome-recording point (the _audited
closure in core/funnel/tools_registry_exec_setup.py) called
try_record_outcome with the store's dispatched=True default for
every outcome, including gate refusals.

The fix (funnel-local, mark-based — NOT a store-default change):
_exec_receipt inits ctx._receipt_dispatched = False;
_exec_mark_dispatched sets it True only when try_mark_dispatched
succeeds; _audited passes dispatched=ctx._receipt_dispatched to
try_record_outcome. The flag means exactly "a {"type":
"dispatched"} record precedes this outcome in the file". Every
gate refusal returns through _audited BEFORE the mark point, so
permission / scope / validation / confirm-deny / no-handler
refusals, unknown tools, and reads (which never mark by design)
record dispatched False; genuinely dispatched calls keep True.

Harness: the funnel legs drive TOOLS.execute through the VA funnel
(F2 shape) with a controllable confirm handler on both gate
identities; permission state is set per test through the product's
own API. Data is snapshotted in setUpClass and restored in
tearDownClass; receipt rows go to a temp store per test (the C2-F2
idiom, as in test_r3ff301f302_wording.py).
"""
import json
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
from voice_assistant.core import permission_grants as va_pg  # noqa: E402
from voice_assistant.core import tool_receipts as va_receipts  # noqa: E402
from voice_assistant.core.funnel import tools_registry as va_reg  # noqa: E402
import core.confirm_gate as bare_gate  # noqa: E402
from core import injection as _inj  # noqa: E402
from core.funnel import rule_of_two as _rot  # noqa: E402

_ANSWER = {"value": True}
_ASKED = []


def _fresh_state():
    """Clear session taint + Rule-of-Two legs between calls (C2-F2 idiom)."""
    _inj.clear_taint(force=True)
    _rot.reset_for_tests()


def _clear_store():
    """Never-decided state: no grants store file at all."""
    for name in ("permission_grants.json", "permission_folders.json"):
        try:
            (TREE / "data" / name).unlink()
        except FileNotFoundError:
            pass


def _deny(pid):
    assert va_pg.set_permission_state(pid, "denied",
                                      via="settings", how="Settings"), pid


class _FunnelBase(unittest.TestCase):
    """VA funnel + controllable confirm handler; data snapshotted."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="r3ff502_")
        tmp = pathlib.Path(cls._tmp.name)
        cls.out = tmp / "out"
        cls.out.mkdir()
        cls.probe = tmp / "probe"
        cls.probe.mkdir()
        cls._data_snap = tmp / "data_snap"
        shutil.copytree(TREE / "data", cls._data_snap)

        def _handler(name, summary=None, *a, **k):
            _ASKED.append({"tool": name, "summary": summary or ""})
            return _ANSWER["value"]

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
        self._rtmp = tempfile.TemporaryDirectory(prefix="r3ff502_rx_")
        self._receipts = pathlib.Path(self._rtmp.name) / "tool_receipts.jsonl"
        va_receipts.install_store(self._receipts)
        va_receipts.reset_receipt_degraded()
        _ASKED.clear()
        _ANSWER["value"] = True
        _fresh_state()

    def tearDown(self):
        va_receipts.reset_default()
        va_receipts.reset_receipt_degraded()
        self._rtmp.cleanup()
        _fresh_state()

    def _execute(self, tool, args):
        return va_reg.tools.execute(tool, dict(args))

    def _grant_files(self):
        _clear_store()
        assert va_pg.set_permission_state(
            "files", "granted", via="settings", how="Settings")
        assert va_pg.add_folder(str(self.probe), via="settings")

    def _rows(self):
        if not self._receipts.exists():
            return []
        return [json.loads(line) for line in
                self._receipts.read_text().splitlines() if line.strip()]

    def _outcome_for(self, tool):
        """Newest outcome row for `tool`, its attempt row, and whether
        a dispatched record precedes the outcome in the file."""
        rows = self._rows()
        attempts = {r["receipt_id"]: r for r in rows
                    if r.get("type") == "attempt"}
        outcome = None
        for r in reversed(rows):
            if r.get("type") != "outcome":
                continue
            if attempts.get(r.get("receipt_id", ""), {}).get("tool") == tool:
                outcome = r
                break
        self.assertIsNotNone(outcome, f"no outcome row for {tool}")
        rid = outcome["receipt_id"]
        has_dispatched = any(
            r.get("type") == "dispatched"
            and r.get("receipt_id") == rid for r in rows)
        return outcome, attempts.get(rid, {}), has_dispatched

    def _assert_outcome(self, tool, status, dispatched):
        """The outcome row, the file evidence, and the live attempt
        must all agree on whether this call dispatched."""
        outcome, attempt, has_dispatched = self._outcome_for(tool)
        self.assertEqual(outcome.get("status"), status,
                         f"{tool}: outcome={outcome!r}")
        self.assertIs(outcome.get("dispatched"), dispatched,
                      f"{tool}: outcome dispatched={outcome.get('dispatched')!r} "
                      f"but no dispatched record precedes it: {outcome!r}")
        self.assertIs(has_dispatched, dispatched,
                      f"{tool}: dispatched record present={has_dispatched} "
                      f"but outcome says dispatched={dispatched}")
        # The live store's attempt: the mark flips it, and
        # record_outcome overwrites it with the outcome's flag —
        # so a refused attempt must read False here too (pre-fix
        # it read True: record_outcome's default flipped it).
        store = va_receipts._get_store()
        live = None
        if store is not None:
            for att in store.attempts():
                if str(att.get("receipt_id", "")) == str(
                        outcome.get("receipt_id", "")):
                    live = att
                    break
        self.assertIsNotNone(live, f"{tool}: no live attempt row")
        self.assertIs(live.get("dispatched"), dispatched,
                      f"{tool}: live attempt dispatched="
                      f"{live.get('dispatched')!r} but outcome says "
                      f"dispatched={dispatched}")


class TestDeniedRowsReadFalse(_FunnelBase):
    """F-F502: every never-dispatched outcome reads dispatched False."""

    def test_deny_delete_plan(self):
        # Filed shape O5: confirm-gated delete_plan, user denies.
        # The funnel result reads status refused / kind
        # not_confirmed; the outcome row reads status refused.
        _clear_store()
        _ANSWER["value"] = False
        r = self._execute("delete_plan", {"plan_id": "f5-plan-1"})
        self.assertEqual(r.get("status"), "refused",
                         f"result={r!r}")
        self.assertEqual(r.get("kind"), "not_confirmed",
                         f"result={r!r}")
        self.assertEqual(len(_ASKED), 1, "the confirm ask never happened")
        self._assert_outcome("delete_plan", "refused", False)

    def test_deny_batch_rename(self):
        # Filed shape O10: confirm-gated batch_rename, user denies.
        self._grant_files()
        _ANSWER["value"] = False
        r = self._execute("batch_rename",
                          {"folder": str(self.probe), "find": "keep",
                           "replace": "kept", "confirm": True})
        self.assertEqual(r.get("status"), "refused",
                         f"result={r!r}")
        self.assertEqual(r.get("kind"), "not_confirmed",
                         f"result={r!r}")
        self.assertEqual(len(_ASKED), 1, "the confirm ask never happened")
        self._assert_outcome("batch_rename", "refused", False)

    def test_no_confirm_handler(self):
        # No handler at all: the confirm stage refuses, nothing runs.
        _clear_store()
        va_gate.clear_handler()
        bare_gate.clear_handler()
        try:
            r = self._execute("delete_plan", {"plan_id": "f5-plan-1"})
        finally:
            va_gate.register_handler(type(self)._handler)
            bare_gate.register_handler(type(self)._handler)
        self.assertEqual(r.get("status"), "refused",
                         f"result={r!r}")
        self.assertEqual(r.get("kind"), "not_confirmed",
                         f"result={r!r}")
        self.assertIn("no confirmation handler", r.get("message", ""),
                      f"message={r.get('message')!r}")
        self.assertEqual(_ASKED, [], "no ask can happen with no handler")
        self._assert_outcome("delete_plan", "refused", False)

    def test_permission_denied_mic(self):
        _clear_store()
        _deny("microphone")
        r = self._execute("record_audio", {"seconds": 5})
        self.assertEqual(r.get("kind"), "refused",
                         f"result={r!r}")
        self.assertEqual(_ASKED, [],
                         "a permission refusal never reaches the ask")
        self._assert_outcome("record_audio", "refused", False)

    def test_scope_outside_folder(self):
        self._grant_files()
        r = self._execute("add_border", {"path": "/etc/outside.txt"})
        self.assertEqual(r.get("kind"), "refused",
                         f"result={r!r}")
        self.assertIn("outside the folders", r.get("message", ""),
                      f"message={r.get('message')!r}")
        self.assertEqual(_ASKED, [],
                         "a scope refusal never reaches the ask")
        self._assert_outcome("add_border", "refused", False)

    def test_validation_wrong_type(self):
        # A mistyped arg refuses with the validation reason (F-F302)
        # and never reaches dispatch.
        self._grant_files()
        r = self._execute("add_border", {"path": 12345})
        self.assertEqual(r.get("kind"), "validation",
                         f"result={r!r}")
        self.assertEqual(_ASKED, [],
                         "a validation refusal never reaches the ask")
        # Validation errors record the refused vocabulary; the pin
        # here is the flag, not the status derivation.
        outcome, _, has_dispatched = self._outcome_for("add_border")
        self.assertIs(outcome.get("dispatched"), False,
                      f"outcome={outcome!r}")
        self.assertIs(has_dispatched, False,
                      f"outcome={outcome!r}")

    def test_unknown_tool(self):
        _clear_store()
        r = self._execute("no_such_tool_xyz", {})
        self.assertEqual(r.get("kind"), "unavailable",
                         f"result={r!r}")
        self.assertEqual(_ASKED, [],
                         "an unknown tool never reaches the ask")
        outcome, _, has_dispatched = self._outcome_for("no_such_tool_xyz")
        self.assertIs(outcome.get("dispatched"), False,
                      f"outcome={outcome!r}")
        self.assertIs(has_dispatched, False,
                      f"outcome={outcome!r}")


class TestDispatchedRowsReadTrue(_FunnelBase):
    """F-F502: a row that DID dispatch keeps dispatched True."""

    def test_approved_rename_succeeds(self):
        self._grant_files()
        (self.probe / "f5_old.txt").write_text("slice5 probe\n")
        r = self._execute("batch_rename",
                          {"folder": str(self.probe), "find": "old",
                           "replace": "new", "confirm": True})
        self.assertEqual(r.get("status"), "success",
                         f"result={r!r}")
        self.assertTrue((self.probe / "f5_new.txt").exists(),
                        "the rename itself must have landed")
        self.assertEqual(len(_ASKED), 1, "the confirm ask never happened")
        self._assert_outcome("batch_rename", "succeeded", True)

    def test_approved_rename_missing_folder_fails(self):
        # No folder allowlist: with one set, the scope stage refuses
        # a folder outside it before dispatch; with none set, the
        # call reaches the handler, which fails on the missing folder.
        _clear_store()
        r = self._execute("batch_rename",
                          {"folder": "/tmp/ff502_no_such_dir", "find": "x",
                           "replace": "y", "confirm": True})
        self.assertEqual(len(_ASKED), 1, "the confirm ask never happened")
        self._assert_outcome("batch_rename", "failed", True)

    def test_timeout_result_stays_unknown_and_dispatched(self):
        # A timeout happens AFTER dispatch: the mark happened, so the
        # outcome is unknown AND dispatched — the guard must keep
        # blocking a re-run of it.
        self._grant_files()
        entry = va_reg.tools._tools["batch_rename"]
        orig_handler = entry["handler"]

        def _timeout_stub(*a, **k):
            return {"status": "error", "kind": "timeout",
                    "operation_id": "ff502-timeout-1",
                    "message": "timed out in probe"}

        entry["handler"] = _timeout_stub
        try:
            self._execute("batch_rename",
                          {"folder": str(self.probe), "find": "old",
                           "replace": "new", "confirm": True})
        finally:
            entry["handler"] = orig_handler
        self._assert_outcome("batch_rename", "unknown", True)


class TestReadRow(_FunnelBase):
    """F-F502: reads never mark dispatched, so their outcome reads False.

    Pinned deliberately: reads never mark by design (their attempts
    are never consulted by the guard), so no dispatched record
    precedes a read outcome — the flag reads False, matching the
    attempt row, which already says dispatched False.
    """

    def test_read_succeeds_with_dispatched_false(self):
        self._grant_files()
        probe_file = self.probe / "f5_read.txt"
        probe_file.write_text("read me\n")
        r = self._execute("read_file", {"filepath": str(probe_file)})
        self.assertEqual(r.get("status"), "success",
                         f"result={r!r}")
        self._assert_outcome("read_file", "succeeded", False)


class TestGuardPriorControl(_FunnelBase):
    """F-F502 boundary: a guard refusal records NO outcome row at all.

    _exec_receipt returns before try_record_attempt when the guard
    fires, so ctx._receipt_attempt stays None and _audited records
    nothing for the call. The pin is the boundary: refused, and the
    file gains no new row — no outcome row carrying a flag, no
    second dispatched record.
    """

    def test_guard_refusal_writes_no_rows(self):
        self._grant_files()
        args = {"folder": str(self.probe), "find": "old",
                "replace": "new", "confirm": True}
        seed = va_receipts.try_record_attempt(tool="batch_rename",
                                              args=dict(args))
        self.assertIsNotNone(seed, "seed attempt failed")
        self.assertTrue(va_receipts.try_mark_dispatched(seed),
                        "seed mark failed")
        rows_before = self._rows()
        self.assertEqual(len(rows_before), 2, f"rows={rows_before!r}")
        r = self._execute("batch_rename", dict(args))
        self.assertEqual(r.get("status"), "refused",
                         f"result={r!r}")
        self.assertIn("unknown outcome", r.get("message", ""),
                      f"message={r.get('message')!r}")
        self.assertEqual(self._rows(), rows_before,
                         "the guard refusal must add no receipt rows")


if __name__ == "__main__":
    unittest.main()
