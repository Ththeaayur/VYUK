"""§7 item 1 — stable target handles (parent draft, session-only).

Lab honesty: resolution takes explicit observations, so every
rule here is drivable headless with fake identities. Real
Windows behaviour — handles against real apps, real foreground
switches — stays field work and is not claimed here.

Every test restores what it patches (addCleanup) and resets
the handle store, so it cannot poison later suites in the same
process.
"""

import os
import tempfile
import unittest
from unittest import mock

from core.desktop import cursor  # Phase 3 D4d INTENTIONAL: cursor hub moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/
from core.desktop import uia  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
from core.desktop import uia_base as _uia_base_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_click as _uia_click_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_discover as _uia_discover_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_marks as uia_marks_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_probe as _uia_probe_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_send as _uia_send_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_type as _uia_type_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/
from core.desktop import uia_verify as _uia_verify_mod  # Phase 3 D2c INTENTIONAL: uia family moved to core/desktop/


# Refactor P1 step 4 INTENTIONAL seam migration: core/desktop/uia.py split into
# uia_base/verify/discover/probe/marks/click/send/type. A fake must land
# in every module that binds the name (a function looks its helpers up
# in its own module's namespace), so patching only core.desktop.uia no longer
# reaches the moved functions. Call sites are unchanged.
_UIA_SEAM_MODULES = (uia, _uia_base_mod, _uia_verify_mod,
                     _uia_discover_mod, _uia_probe_mod, uia_marks_mod,
                     _uia_click_mod, _uia_send_mod, _uia_type_mod)


def _patch(testcase, target, value):
    if target.startswith("core.desktop.uia."):  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        name = target[len("core.desktop.uia."):]  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/; prefix moves with the targets
        for mod in _UIA_SEAM_MODULES:
            if hasattr(mod, name):
                patcher = mock.patch.object(mod, name, value)
                patcher.start()
                testcase.addCleanup(patcher.stop)
        return
    patcher = mock.patch(target, value)
    patcher.start()
    testcase.addCleanup(patcher.stop)


class _Base(unittest.TestCase):
    def setUp(self):
        th.reset_store()

    def tearDown(self):
        th.reset_store()


# ---------------------------------------------------------------------------
# Windows: strongest-first, contradiction refuses, title edits survive
# ---------------------------------------------------------------------------


class WindowHandleTests(_Base):
    def test_same_identity_resolves(self):
        handle = th.mint_window({"hwnd": 111, "pid": 42, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "identity": {"hwnd": 111, "pid": 42, "title": "Notes"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved"]["handle_id"], handle["id"])
        self.assertIsNone(result["refusal"])

    def test_title_edit_survives_on_same_hwnd(self):
        # The document renamed mid-task: same window, new title.
        handle = th.mint_window({"hwnd": 111, "pid": 42, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "identity": {"hwnd": 111, "pid": 42,
                         "title": "Notes — modified"}})
        self.assertTrue(result["ok"])

    def test_replaced_window_same_title_refuses_naming_hwnd(self):
        handle = th.mint_window({"hwnd": 111, "pid": 42, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "identity": {"hwnd": 222, "pid": 99, "title": "Notes"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "identity-contradiction")
        self.assertIn("hwnd", result["refusal"]["reason"])
        self.assertIn("Re-observe", result["refusal"]["reason"])

    def test_pid_only_fallback_resolves(self):
        handle = th.mint_window({"pid": 42, "title": "Notes"})
        result = th.resolve(handle["id"], {"identity": {"pid": 42}})
        self.assertTrue(result["ok"])

    def test_title_only_contradiction_refuses(self):
        handle = th.mint_window({"title": "Notes"})
        result = th.resolve(handle["id"], {"identity": {"title": "Browser"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "identity-contradiction")

    def test_no_comparable_evidence_refuses(self):
        handle = th.mint_window({"hwnd": 111, "title": "Notes"})
        result = th.resolve(handle["id"], {"identity": {}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "identity-evidence-missing")

    def test_mint_without_identity_raises(self):
        with self.assertRaises(ValueError):
            th.mint_window({"hwnd": None, "title": "  "})
        with self.assertRaises(ValueError):
            th.mint_window({})


# ---------------------------------------------------------------------------
# Controls: automation id, usability, name as weak evidence only
# ---------------------------------------------------------------------------


class ControlHandleTests(_Base):
    def _save(self, automation_id="btn-save", name="Save"):
        return th.mint_control(
            {"hwnd": 111, "automation_id": automation_id},
            name=name, control_type="Button")

    def test_automation_id_match_survives_rename(self):
        handle = self._save()
        result = th.resolve(handle["id"], {"candidates": [
            {"identity": {"hwnd": 111, "automation_id": "btn-save"},
             "name": "Save As", "control_type": "Button"}]})
        self.assertTrue(result["ok"])

    def test_automation_id_mismatch_refuses(self):
        handle = self._save()
        result = th.resolve(handle["id"], {"candidates": [
            {"identity": {"hwnd": 111, "automation_id": "btn-open"},
             "name": "Save", "control_type": "Button"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "identity-contradiction")

    def test_unusable_best_match_refuses_not_falls_through(self):
        handle = self._save()
        result = th.resolve(handle["id"], {"candidates": [
            {"identity": {"hwnd": 111, "automation_id": "btn-save"},
             "name": "Save", "control_type": "Button",
             "usable": False, "unusable_reason": "disabled"},
            {"identity": {"hwnd": 111, "automation_id": "btn-other"},
             "name": "Save", "control_type": "Button"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "target-unusable")

    def test_no_candidate_refuses(self):
        handle = self._save()
        result = th.resolve(handle["id"], {"candidates": []})
        self.assertFalse(result["ok"])

    def test_name_only_match_resolves(self):
        handle = th.mint_control({"title": "Notes"}, name="Save",
                                 control_type="Button")
        result = th.resolve(handle["id"], {"candidates": [
            {"identity": {"title": "Notes"}, "name": "Save",
             "control_type": "Button"}]})
        self.assertTrue(result["ok"])

    def test_name_only_mismatch_refuses(self):
        handle = th.mint_control({"title": "Notes"}, name="Save",
                                 control_type="Button")
        result = th.resolve(handle["id"], {"candidates": [
            {"identity": {"title": "Notes"}, "name": "Open",
             "control_type": "Button"}]})
        self.assertFalse(result["ok"])


# ---------------------------------------------------------------------------
# Marks: F-07 semantics exactly — agreement with _marks_stale_reason
# ---------------------------------------------------------------------------


class MarkHandleTests(_Base):
    def _row(self):
        return {"id": 1, "name": "Save", "control_type": "Button",
                "rect": (10, 20, 110, 60)}

    def test_same_stamp_resolves(self):
        handle = th.mint_mark(self._row(), {"hwnd": 111, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "foreground": {"hwnd": 111, "title": "Notes"},
            "bounds": (0, 0, 1920, 1080)})
        self.assertTrue(result["ok"])
        self.assertEqual(result["resolved"]["rect"], (10, 20, 110, 60))

    def test_hwnd_change_refuses_even_with_same_title(self):
        handle = th.mint_mark(self._row(), {"hwnd": 111, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "foreground": {"hwnd": 222, "title": "Notes"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "stale-foreground")

    def test_title_change_with_unknown_hwnd_refuses(self):
        handle = th.mint_mark(self._row(), {"hwnd": None, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "foreground": {"hwnd": None, "title": "Browser"}})
        self.assertFalse(result["ok"])

    def test_unknown_both_sides_resolves(self):
        handle = th.mint_mark(self._row(), {"hwnd": None, "title": None})
        result = th.resolve(handle["id"], {
            "foreground": {"hwnd": None, "title": None}})
        self.assertTrue(result["ok"])

    def test_out_of_bounds_refuses(self):
        handle = th.mint_mark(self._row(), {"hwnd": 111, "title": "Notes"})
        result = th.resolve(handle["id"], {
            "foreground": {"hwnd": 111, "title": "Notes"},
            "bounds": (0, 0, 50, 50)})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "out-of-bounds")

    def test_agreement_with_marks_stale_reason(self):
        # The engine must never weaken a refusal the current code
        # would make, nor refuse where it would click: same
        # inputs, same fresh/stale verdict across the matrix.
        # NOTE: a title edit with the SAME hwnd still refuses
        # here — that is F-07, and title-edit survival lives at
        # the window-handle level (fresh grounding table after
        # the rename), not at the mark-snapshot level.
        cases = [
            (111, "Notes", 111, "Notes", False),
            (111, "Notes", 222, "Notes", True),
            (None, "Notes", None, "Browser", True),
            (None, "Notes", None, "Notes", False),
            (None, "Notes", None, None, False),
            (111, "Notes", 111, "Notes — modified", True),
        ]
        # Refactor P1 step 4 INTENTIONAL: marks state lives in
        # core/desktop/uia_marks.py now; save/restore it there.
        saved = (uia_marks_mod._last_marks, uia_marks_mod._last_marks_fg,
                 uia_marks_mod._last_marks_hwnd)
        self.addCleanup(
            lambda: setattr(uia_marks_mod, "_last_marks", saved[0]) or
            setattr(uia_marks_mod, "_last_marks_fg", saved[1]) or
            setattr(uia_marks_mod, "_last_marks_hwnd", saved[2]))
        for stamp_hwnd, stamp_title, cur_hwnd, cur_title, stale in cases:
            uia._set_last_marks([self._row()], stamp_title, stamp_hwnd)
            _patch(self, "core.desktop.uia.foreground_title", lambda: cur_title)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            _patch(self, "core.desktop.uia.foreground_hwnd", lambda: cur_hwnd)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
            old_reason = uia._marks_stale_reason(1)
            handle = th.mint_mark(
                self._row(), {"hwnd": stamp_hwnd, "title": stamp_title})
            result = th.resolve(handle["id"], {
                "foreground": {"hwnd": cur_hwnd, "title": cur_title}})
            self.assertEqual(old_reason is not None, stale,
                             "stale expectation case %r" % (cases,))
            self.assertEqual(result["ok"], not stale,
                             "engine disagrees with F-07 on %r"
                             % ((stamp_hwnd, stamp_title,
                                 cur_hwnd, cur_title),))


# ---------------------------------------------------------------------------
# Files: canonical path, retarget refusal, save-target tolerance
# ---------------------------------------------------------------------------


class FileHandleTests(_Base):
    def test_existing_file_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "doc.txt")
            with open(path, "w") as fh:
                fh.write("hello")
            handle = th.mint_file(path)
            result = th.resolve(handle["id"], {})
            self.assertTrue(result["ok"])
            self.assertEqual(result["resolved"]["path"],
                             os.path.realpath(path))

    def test_renamed_away_file_refuses_as_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "doc.txt")
            with open(path, "w") as fh:
                fh.write("hello")
            handle = th.mint_file(path)
            os.rename(path, os.path.join(tmp, "renamed.txt"))
            result = th.resolve(handle["id"], {})
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"], "target-gone")

    def test_symlink_retarget_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.txt")
            b = os.path.join(tmp, "b.txt")
            with open(a, "w") as fh:
                fh.write("a")
            with open(b, "w") as fh:
                fh.write("b")
            link = os.path.join(tmp, "link.txt")
            os.symlink(a, link)
            handle = th.mint_file(link)
            self.assertTrue(th.resolve(handle["id"], {})["ok"])
            os.unlink(link)
            os.symlink(b, link)
            result = th.resolve(handle["id"], {})
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"], "path-retargeted")

    def test_save_target_nonexistent_at_mint_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "new-doc.txt")
            handle = th.mint_file(path)
            result = th.resolve(handle["id"], {})
            self.assertTrue(result["ok"])


# ---------------------------------------------------------------------------
# Store discipline: session scope, tamper, unknown, cap, revoke
# ---------------------------------------------------------------------------


class StoreDisciplineTests(_Base):
    def test_unknown_handle_refuses(self):
        result = th.resolve("th-window-999999", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_foreign_session_refuses(self):
        handle = th.mint_window({"hwnd": 111, "title": "Notes"})
        th.begin_session()
        result = th.resolve(handle["id"], {"identity": {"hwnd": 111}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "foreign-session")
        # Handles minted under the NEW session still resolve.
        fresh = th.mint_window({"hwnd": 111, "title": "Notes"})
        self.assertTrue(th.resolve(
            fresh["id"], {"identity": {"hwnd": 111}})["ok"])

    def test_tampered_record_refuses_not_repairs(self):
        handle = th.mint_window({"hwnd": 111, "title": "Notes"})
        record = th._store[handle["id"]]
        saved_payload = dict(record["payload"])
        try:
            record["payload"] = {"identity": {"hwnd": 999,
                                              "title": "Notes"}}
            result = th.resolve(handle["id"], {"identity": {"hwnd": 999}})
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"], "tampered-handle")
        finally:
            record["payload"] = saved_payload
        self.assertTrue(th.resolve(
            handle["id"], {"identity": {"hwnd": 111}})["ok"])

    def test_revoke_then_resolve_refuses(self):
        handle = th.mint_window({"hwnd": 111, "title": "Notes"})
        self.assertTrue(th.revoke(handle["id"]))
        self.assertFalse(th.revoke(handle["id"]))
        result = th.resolve(handle["id"], {"identity": {"hwnd": 111}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_store_cap_evicts_oldest(self):
        first = None
        for i in range(th.STORE_CAP + 4):
            handle = th.mint_mark(
                {"id": i, "name": "M", "control_type": "Button",
                 "rect": (0, 0, 10, 10)},
                {"hwnd": None, "title": None})
            if i == 0:
                first = handle["id"]
        self.assertEqual(len(th.list_handles()), th.STORE_CAP)
        result = th.resolve(first, {"foreground": {}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-handle")

    def test_mint_file_empty_path_raises(self):
        with self.assertRaises(ValueError):
            th.mint_file("   ")


# ---------------------------------------------------------------------------
# Live consumer: click_by_mark names the handle id
# ---------------------------------------------------------------------------


class ClickJourneyTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        # Refactor P1 step 4 INTENTIONAL: marks state lives in
        # core/desktop/uia_marks.py now; save/restore it there.
        self._saved = (uia_marks_mod._last_marks,
                       uia_marks_mod._last_marks_fg,
                       uia_marks_mod._last_marks_hwnd)
        self.addCleanup(uia.clear_last_marks)
        self.addCleanup(
            lambda: setattr(uia_marks_mod, "_last_marks",
                            self._saved[0]) or
            setattr(uia_marks_mod, "_last_marks_fg",
                    self._saved[1]) or
            setattr(uia_marks_mod, "_last_marks_hwnd",
                    self._saved[2]))

    def tearDown(self):
        th.reset_store()

    def _stamp(self, title="App", hwnd=None):
        uia._set_last_marks(
            [{"id": 1, "name": "Save", "control_type": "Button",
              "rect": (10, 20, 110, 60)}], title, hwnd)

    def _patch_click(self):
        _patch(self, "core.desktop.uia._failsafe_block", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia._safe_snapshot", lambda: {})  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.probe_suffix", lambda *a, **k: "")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "App")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: None)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.cursor._virtual_bounds",
               lambda: (0, 0, 1920, 1080))  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)
        _patch(self, "core.desktop.cursor.click_cursor",
               lambda **k: "Clicked (left button) — landed at (60, 40).")  # Phase 3 D4d INTENTIONAL: patch target moved where bound (cursor hub -> core/desktop/)

    def test_click_result_names_handle_id(self):
        self._stamp()
        self._patch_click()
        result = uia.click_by_mark(1)
        self.assertIn("Clicked mark 1", result)
        self.assertIn("[target handle th-mark-", result)

    def test_stale_click_refuses_without_handle_note(self):
        self._stamp("Notes", 111)
        self._patch_click()
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 222)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_title", lambda: "Notes")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.click_by_mark(1)
        self.assertIn("stale", result)
        self.assertNotIn("Clicked mark", result)
        self.assertNotIn("target handle", result)

    def test_renamed_document_window_handle_journey(self):
        # The definition's journey at the level it survives:
        # a document window renamed mid-task still resolves by
        # hwnd, so a FRESH grounding table in that window (not
        # the stale snapshot) yields the same Save control.
        window = th.mint_window({"hwnd": 111, "pid": 42,
                                 "title": "Notes"})
        renamed = th.resolve(window["id"], {
            "identity": {"hwnd": 111, "pid": 42,
                         "title": "Notes — modified"}})
        self.assertTrue(renamed["ok"])
        self._stamp("Notes — modified", 111)
        self._patch_click()
        _patch(self, "core.desktop.uia.foreground_title",
               lambda: "Notes — modified")  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        _patch(self, "core.desktop.uia.foreground_hwnd", lambda: 111)  # Phase 3 D4b INTENTIONAL: uia hub moved to core/desktop/
        result = uia.click_by_mark(1)
        self.assertIn("Clicked mark 1", result)
        self.assertIn("[target handle th-mark-", result)


if __name__ == "__main__":
    unittest.main()
