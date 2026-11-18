"""§7 item 4 — file dialogs (parent draft).

Drives the dialog backend protocol with a fake dialog: proves
open / save / folder-pick, kind-mismatch refusal, overwrite
confirm stop, canonical-path-change refusal (folder retarget
and file retarget, via real symlinks), ambiguous-match listing
(never first-pick), closed-dialog refusal, undriven-dialog
manual steps, password wall, snapshot anchoring, and that no
refusal ever invokes. Real Windows behaviour — real common
dialogs, real Value / Invoke patterns — stays field work and
is not claimed here.

Every test resets the handle store, so it cannot poison later
suites in the same process.
"""

import os
import tempfile
import unittest

from core.desktop import file_dialogs as fd  # INTENTIONAL (Phase 3 S7a): file_dialogs moved to core/desktop/
from core.desktop import perception_adapter as pa  # INTENTIONAL (Phase 3 S7a): perception_adapter moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/

FG = {"hwnd": 111, "pid": 42, "title": "Notes", "proc": "notes.exe"}
BOUNDS = (0, 0, 800, 600)


class FakePerceptionBackend:
    """Minimal item-2 backend: one fake capture, fixed foreground."""

    def __init__(self, fg=None):
        self.fg = dict(fg) if fg is not None else dict(FG)

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def capture_block_reason(self):
        return None

    def capture(self):
        return {"fake_image": True}

    def marks(self, capture, fg, ocr_words):
        return [], ""

    def controls(self, capture, fg):
        return [], ""

    def ocr_words(self, capture):
        return [], ""

    def pixels(self, capture):
        return {}, ""


class FakeDialogBackend:
    """Fake native dialog: state, sets, and invoke are recorded.

    ``close_after_reads`` closes the dialog after that many
    ``dialog_state`` reads (None afterwards). ``on_read`` runs
    on every read, for symlink-swap hooks. ``folders`` limits
    which folders ``set_folder`` accepts (None = any).
    """

    def __init__(self, kind="open", folder="/docs", items=None,
                 exposed=True, password=False, fg=None,
                 close_after_reads=None, on_read=None, folders=None):
        self.kind = kind
        self.current_folder = folder
        self.items = list(items) if items is not None else []
        self.exposed = exposed
        self.password = password
        self.fg = dict(fg) if fg is not None else dict(FG)
        self.close_after_reads = close_after_reads
        self.on_read = on_read
        self.folders = folders
        self.reads = 0
        self.closed = False
        self.filename_set = None
        self.filter_set = None
        self.invoke_calls = []
        self.invoke_result = (True, True, None, "")
        self.app_reported = None

    def foreground_identity(self):
        return dict(self.fg)

    def bounds(self):
        return BOUNDS

    def dialog_state(self):
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads)
        if self.closed or (self.close_after_reads is not None
                           and self.reads > self.close_after_reads):
            return None
        return {"kind": self.kind, "title": "Open",
                "current_folder": self.current_folder,
                "items": list(self.items),
                "filename_field": True, "exposed": self.exposed,
                "has_password_field": self.password,
                "file_type_filter": self.filter_set, "note": ""}

    def set_folder(self, folder):
        if self.folders is not None and folder not in self.folders:
            return False, "folder does not exist"
        self.current_folder = folder
        return True, ""

    def set_filename(self, name):
        self.filename_set = name
        return True, ""

    def set_filter(self, file_type):
        self.filter_set = file_type
        return True, ""

    def invoke(self, button):
        self.invoke_calls.append(button)
        invoked, closed_flag, reported, note = self.invoke_result
        if closed_flag:
            self.closed = True
        if reported is None:
            reported = self.app_reported
        return invoked, closed_flag, reported, note


def _snapshot(fg=None):
    return pa.observe(backend=FakePerceptionBackend(fg))


class _Base(unittest.TestCase):
    def setUp(self):
        th.reset_store()

    def tearDown(self):
        th.reset_store()

    def drive(self, backend, action, **kwargs):
        return fd.drive_dialog(action, snapshot=_snapshot(),
                               backend=backend, **kwargs)


class HappyTests(_Base):
    def test_open_exact_match(self):
        backend = FakeDialogBackend(
            kind="open", folder="/docs",
            items=["report.txt", "notes.txt"])
        backend.app_reported = "/docs/report.txt"
        result = self.drive(backend, "open", filename="report.txt")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "open")
        self.assertEqual(result["path_canonical"], "/docs/report.txt")
        self.assertTrue(result["invoked"])
        self.assertTrue(result["closed"])
        self.assertIn("app reports", result["verification"])
        self.assertIsNotNone(result["file_handle_id"])
        self.assertEqual(backend.invoke_calls, ["open"])
        self.assertEqual(backend.filename_set, "report.txt")

    def test_open_unverified_without_app_report(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["report.txt"])
        result = self.drive(backend, "open", filename="report.txt")
        self.assertTrue(result["ok"], result)
        self.assertIn("invoked-without-verification",
                      result["verification"])

    def test_save_new_file_needs_no_confirmation(self):
        backend = FakeDialogBackend(kind="save", folder="/docs",
                                    items=["old.txt"])
        result = self.drive(backend, "save", filename="draft.txt")
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["needs_confirmation"])
        self.assertEqual(backend.invoke_calls, ["save"])

    def test_folder_pick(self):
        backend = FakeDialogBackend(kind="folder", folder="/docs",
                                    items=[])
        result = self.drive(backend, "folder")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path_canonical"], "/docs")
        self.assertEqual(backend.invoke_calls, ["select_folder"])

    def test_folder_navigation_and_filter(self):
        backend = FakeDialogBackend(
            kind="open", folder="/docs", items=["a.txt"],
            folders={"/docs", "/docs/sub"})
        result = self.drive(backend, "open", folder="/docs/sub",
                            filename="a.txt", file_type="*.txt")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["folder_canonical"], "/docs/sub")
        self.assertEqual(backend.filter_set, "*.txt")

    def test_case_insensitive_exact_match_keeps_listed_spelling(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["Report.TXT"])
        result = self.drive(backend, "open", filename="report.txt")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["filename"], "Report.TXT")


class RefusalTests(_Base):
    def test_kind_mismatch_both_directions(self):
        save_dialog = FakeDialogBackend(kind="save", folder="/docs",
                                        items=[])
        result = self.drive(save_dialog, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "kind-mismatch")
        self.assertIn("save", result["refusal"]["reason"])
        self.assertIn("open", result["refusal"]["reason"])
        self.assertEqual(save_dialog.invoke_calls, [])
        open_dialog = FakeDialogBackend(kind="open", folder="/docs",
                                        items=["x.txt"])
        result = self.drive(open_dialog, "save", filename="x.txt")
        self.assertEqual(result["refusal"]["code"], "kind-mismatch")
        self.assertEqual(open_dialog.invoke_calls, [])

    def test_folder_missing_names_path(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=[], folders={"/docs"})
        result = self.drive(backend, "open", folder="/gone",
                            filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "folder-missing")
        self.assertIn("/gone", result["refusal"]["reason"])
        self.assertEqual(backend.invoke_calls, [])

    def test_file_not_found_names_path(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["other.txt"])
        result = self.drive(backend, "open", filename="missing.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "file-not-found")
        self.assertIn("missing.txt", result["refusal"]["reason"])
        self.assertIn("/docs", result["refusal"]["reason"])
        self.assertEqual(backend.invoke_calls, [])

    def test_ambiguous_substring_lists_never_picks_first(self):
        backend = FakeDialogBackend(
            kind="open", folder="/docs",
            items=["report.txt", "report-final.txt"])
        result = self.drive(backend, "open", filename="report")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "filename-ambiguous")
        self.assertEqual(result["candidates"],
                         ["report.txt", "report-final.txt"])
        self.assertEqual(backend.invoke_calls, [])
        self.assertIsNone(backend.filename_set)

    def test_ambiguous_exact_case_variants(self):
        backend = FakeDialogBackend(
            kind="open", folder="/docs",
            items=["Report.txt", "report.txt"])
        result = self.drive(backend, "open", filename="REPORT.TXT")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "filename-ambiguous")
        self.assertEqual(backend.invoke_calls, [])

    def test_dialog_absent(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=[])
        backend.closed = True
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "dialog-absent")
        self.assertEqual(backend.invoke_calls, [])

    def test_undriven_dialog_gives_manual_steps(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=[], exposed=False)
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "dialog-not-drivable")
        self.assertIn("by hand", result["refusal"]["reason"])
        self.assertEqual(backend.invoke_calls, [])
        self.assertIsNone(backend.filename_set)

    def test_password_wall_never_types(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"], password=True)
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "password-wall")
        self.assertEqual(backend.invoke_calls, [])
        self.assertIsNone(backend.filename_set)

    def test_unknown_action_and_missing_filename(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"])
        result = self.drive(backend, "fly", filename="x.txt")
        self.assertEqual(result["refusal"]["code"], "unknown-action")
        result = self.drive(backend, "open")
        self.assertEqual(result["refusal"]["code"],
                         "filename-required")
        result = self.drive(backend, "folder", filename="x.txt")
        self.assertEqual(result["refusal"]["code"],
                         "filename-not-applicable")
        self.assertEqual(backend.invoke_calls, [])

    def test_filter_unsupported_refuses_not_ignores(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["a.txt"])
        backend.set_filter = None
        result = self.drive(backend, "open", filename="a.txt",
                            file_type="*.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "filter-unsupported")
        self.assertEqual(backend.invoke_calls, [])

    def test_snapshot_stale(self):
        backend = FakeDialogBackend(
            kind="open", folder="/docs", items=["x.txt"],
            fg={"hwnd": 999, "pid": 42, "title": "Notes",
                "proc": "notes.exe"})
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "snapshot-stale")
        self.assertEqual(backend.invoke_calls, [])

    def test_window_handle_refused(self):
        handle = th.mint_window(FG)
        moved_fg = {"hwnd": 999, "pid": 42, "title": "Notes",
                    "proc": "notes.exe"}
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"], fg=moved_fg)
        result = fd.drive_dialog(
            "open", filename="x.txt", snapshot=_snapshot(moved_fg),
            backend=backend, window_handle_id=handle["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "window-handle-refused")
        self.assertEqual(backend.invoke_calls, [])

    def test_dialog_closed_before_execution(self):
        # Read 1 anchors; read 2 is the execution re-check.
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"],
                                    close_after_reads=1)
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "dialog-closed")
        self.assertEqual(backend.invoke_calls, [])

    def test_dialog_still_open_after_invoke_not_claimed(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"])
        backend.invoke_result = (True, False, None, "")
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "dialog-still-open")
        self.assertTrue(result["invoked"])

    def test_invoke_failure(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"])
        backend.invoke_result = (False, False, None, "no pattern")
        result = self.drive(backend, "open", filename="x.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "invoke-failed")


class ConfirmTests(_Base):
    def test_overwrite_stops_for_confirmation(self):
        backend = FakeDialogBackend(kind="save", folder="/docs",
                                    items=["draft.txt"])
        result = self.drive(backend, "save", filename="draft.txt")
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["refusal"]["code"],
                         "overwrite-confirm-required")
        self.assertIn("/docs/draft.txt", result["refusal"]["reason"])
        self.assertEqual(result["path_canonical"], "/docs/draft.txt")
        self.assertEqual(backend.invoke_calls, [])
        self.assertIsNone(backend.filename_set)

    def test_overwrite_with_confirmation_proceeds(self):
        backend = FakeDialogBackend(kind="save", folder="/docs",
                                    items=["draft.txt"])
        result = self.drive(backend, "save", filename="draft.txt",
                            confirm_overwrite=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(backend.invoke_calls, ["save"])


class CanonicalChangeTests(_Base):
    def test_folder_retargeted_between_consent_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            other = os.path.join(tmp, "other")
            os.mkdir(real)
            os.mkdir(other)
            link = os.path.join(tmp, "link")
            os.symlink(real, link)

            def swap_on_third_read(reads):
                # Reads: 1 anchor, 2 folder-landing check,
                # 3 execution re-check.
                if reads == 3:
                    os.unlink(link)
                    os.symlink(other, link)

            backend = FakeDialogBackend(
                kind="open", folder=link, items=["note.txt"],
                folders={link}, on_read=swap_on_third_read)
            result = self.drive(backend, "open", folder=link,
                                filename="note.txt")
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"],
                             "folder-retargeted")
            self.assertEqual(backend.invoke_calls, [])

    def test_file_retargeted_between_consent_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_a = os.path.join(tmp, "a.txt")
            target_b = os.path.join(tmp, "b.txt")
            open(target_a, "w").write("a")
            open(target_b, "w").write("b")
            doc = os.path.join(tmp, "doc.txt")
            os.symlink(target_a, doc)

            def swap_on_second_read(reads):
                # Reads: 1 anchor, 2 execution re-check. The
                # file handle was minted at consent (target A);
                # swapping before resolution must refuse.
                if reads == 2:
                    os.unlink(doc)
                    os.symlink(target_b, doc)

            backend = FakeDialogBackend(
                kind="open", folder=tmp, items=["doc.txt"],
                on_read=swap_on_second_read)
            result = self.drive(backend, "open", filename="doc.txt")
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"],
                             "path-retargeted")
            self.assertEqual(backend.invoke_calls, [])


class DescribeAndBackendTests(_Base):
    def test_describe_lines(self):
        backend = FakeDialogBackend(kind="open", folder="/docs",
                                    items=["x.txt"])
        ok_result = self.drive(backend, "open", filename="x.txt")
        self.assertIn("File dialog open /docs/x.txt",
                      fd.describe(ok_result))
        bad = self.drive(FakeDialogBackend(kind="save", folder="/docs",
                                           items=[]),
                         "open", filename="x.txt")
        self.assertIn("refused", fd.describe(bad))
        stopped = self.drive(
            FakeDialogBackend(kind="save", folder="/docs",
                              items=["d.txt"]),
            "save", filename="d.txt")
        self.assertIn("confirmation", fd.describe(stopped))

    def test_default_backend_degrades_honestly_off_windows(self):
        # This lab has no Windows UI: the default backend must
        # refuse honestly, never raise, never fabricate a dialog.
        result = fd.drive_dialog("open", filename="x.txt",
                                 snapshot=_snapshot(),
                                 backend=fd.default_backend())
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["refusal"])
        self.assertIn(result["refusal"]["code"],
                      ("dialog-not-drivable", "dialog-absent",
                       "snapshot-unavailable"))


if __name__ == "__main__":
    unittest.main()
