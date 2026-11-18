"""R9-2 rustic — encrypted, deduplicated user-file backup.

ADOPT, not port: rustic is a Rust BINARY (v0.11.4), not a library —
there is nothing to port, and a pure-Python re-implementation of a
deduplicated encrypted backup engine would be a large job for no
gain. VYUK adopts the released binary behind a thin wrapper
(core/rustic_backup.py) and never bundles it: the operator places
rustic 0.11.4 at data/bin/rustic[.exe], and the wrapper verifies its
sha256 against the pinned release hashes before EVERY run.

Pins: the password discipline (RUSTIC_PASSWORD at call time only —
never stored, never logged, never a tool argument, never a
--password flag), the local-repositories-only rule, the never
auto-start rule (every write is confirm-gated), the hash-before-run
rule, the read-back-before-claim rule (init/backup verified by a
snapshots probe, restore by a destination re-listing), the funnel
registration + gates + catalogue grouping, and the registry /
schema-lint pins (699 / 698 / 1572 / 697 / 366 / 333 / 267 / 147).

The real-binary tests run only where the operator placed the binary
— this lab places it for the run and removes it afterwards, so on a
machine without it they skip honestly with a plain message, never a
claimed success. The refusal tests need no binary at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import rustic_backup
from core.rustic_backup import (
    EXPECTED_BINARY_SHA256,
    RUSTIC_VERSION,
    backup_files,
    backup_status,
    binary_name,
    binary_path,
    init_backup_repo,
    list_backup_snapshots,
    restore_backup,
)
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A distinctive password used only in this suite, so the tests can
# assert it never appears in any returned message.
_TEST_PASSWORD = "rustic-test-password-7f3a-not-a-real-secret"
_WRONG_PASSWORD = "rustic-wrong-password-9b1c"

_NEW_TOOLS = (
    "init_backup_repo",
    "backup_files",
    "list_backup_snapshots",
    "restore_backup",
    "backup_status",
)
_EFFECTFUL = ("init_backup_repo", "backup_files", "restore_backup")
_READ_ONLY = ("list_backup_snapshots", "backup_status")


def _binary_placed_and_pinned() -> bool:
    """True only if the operator placed the exact pinned binary."""
    path = binary_path()
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == EXPECTED_BINARY_SHA256[binary_name()]


class _RusticTestCase(unittest.TestCase):
    """Base: the default-repo file always points at a temp file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self._orig_repo_file = rustic_backup._REPO_FILE
        rustic_backup._REPO_FILE = self.tmp / "rustic_repo.json"
        self.addCleanup(
            setattr, rustic_backup, "_REPO_FILE", self._orig_repo_file
        )
        self._orig_bin_dir = rustic_backup._BIN_DIR
        self.addCleanup(
            setattr, rustic_backup, "_BIN_DIR", self._orig_bin_dir
        )
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop("RUSTIC_PASSWORD", None)
        os.environ.pop("RUSTIC_REPOSITORY", None)

    def _place_binary(self) -> pathlib.Path:
        """Copy the operator-placed binary into a temp bin dir."""
        # R9-2 parent fix: skip honestly when the operator has not
        # placed the binary — it is operator-supplied, never bundled,
        # so the delivered tree has no binary and these tests cannot
        # run there. The assert failed the whole refusal class
        # (10 tests) on every binary-less run.
        if not _binary_placed_and_pinned():
            raise unittest.SkipTest(
                "operator has not placed the pinned rustic binary"
            )
        bindir = self.tmp / "bin"
        bindir.mkdir()
        dest = bindir / binary_name()
        shutil.copy2(binary_path(), dest)
        rustic_backup._BIN_DIR = bindir
        return dest

    def _make_repo(self, password=_TEST_PASSWORD):
        """Init a fresh repository in a temp dir; return its path."""
        self._place_binary()
        os.environ["RUSTIC_PASSWORD"] = password
        repo = str(self.tmp / "repo")
        msg = init_backup_repo(repo_path=repo)
        self.assertIn("Backup repository created", msg)
        return repo

    def _make_source(self, name="src"):
        src = self.tmp / name
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("hello rustic\n", encoding="utf-8")
        (src / "sub" / "b.txt").write_text(
            "second file\n", encoding="utf-8"
        )
        return src


class RefusalTests(_RusticTestCase):
    """Nothing runs, nothing changes: the honest refusals."""

    def test_no_binary_no_run(self):
        rustic_backup._BIN_DIR = self.tmp / "empty-bin"
        (self.tmp / "empty-bin").mkdir()
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        for fn, kwargs in (
            (init_backup_repo, {"repo_path": str(self.tmp / "r")}),
            (backup_files, {"sources": [str(self.tmp)]}),
            (list_backup_snapshots, {}),
            (restore_backup, {"snapshot": "latest",
                              "destination": str(self.tmp / "d")}),
            (backup_status, {}),
        ):
            msg = fn(**kwargs)
            self.assertIn("not placed yet", msg, fn.__name__)

    def test_wrong_hash_binary_refused(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / binary_name()).write_bytes(b"not the real rustic")
        rustic_backup._BIN_DIR = bindir
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        msg = backup_status()
        self.assertIn("does not match the pinned sha256", msg)
        self.assertIn("will not be run", msg)
        msg = list_backup_snapshots()
        self.assertIn("does not match the pinned sha256", msg)

    def test_no_password_refuses(self):
        self._place_binary()
        repo = str(self.tmp / "repo")
        msg = init_backup_repo(repo_path=repo)
        self.assertIn("RUSTIC_PASSWORD", msg)
        self.assertIn("never stores", msg)

    def test_remote_repo_refused(self):
        self._place_binary()
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        for bad in (
            "s3:my-bucket/backups",
            "sftp:user@host:/backups",
            "rest:http://localhost:8000",
            "https://example.com/repo",
        ):
            msg = init_backup_repo(repo_path=bad)
            self.assertIn("not a local folder", msg, bad)

    def test_relative_repo_refused(self):
        self._place_binary()
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        msg = init_backup_repo(repo_path="backups/vyuk")
        self.assertIn("full path", msg)

    def test_no_default_repo_refuses(self):
        self._place_binary()
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        msg = backup_files(sources=[str(self.tmp)])
        self.assertIn("No backup repository is set yet", msg)
        msg = list_backup_snapshots()
        self.assertIn("No backup repository is set yet", msg)

    def test_missing_source_refuses(self):
        repo = self._make_repo()
        msg = backup_files(
            sources=[str(self.tmp / "does-not-exist")], repo_path=repo
        )
        self.assertIn("does not exist", msg)

    def test_backing_up_the_repo_itself_refuses(self):
        repo = self._make_repo()
        msg = backup_files(sources=[repo], repo_path=repo)
        self.assertIn("will not back up the repository itself", msg)

    def test_bad_label_refuses(self):
        repo = self._make_repo()
        src = self._make_source()
        msg = backup_files(
            sources=[str(src)], repo_path=repo, label="bad label!"
        )
        self.assertIn("not usable", msg)

    def test_bad_snapshot_refuses(self):
        repo = self._make_repo()
        msg = restore_backup(
            snapshot="not-a-snapshot",
            destination=str(self.tmp / "out"),
            repo_path=repo,
        )
        self.assertIn("does not look like one", msg)

    def test_restore_into_repo_refuses(self):
        repo = self._make_repo()
        msg = restore_backup(
            snapshot="latest", destination=repo, repo_path=repo
        )
        self.assertIn("will not restore into the repository", msg)

    def test_password_never_in_messages(self):
        repo = self._make_repo()
        src = self._make_source()
        msgs = [
            backup_files(sources=[str(src)], repo_path=repo),
            list_backup_snapshots(repo_path=repo),
            backup_status(repo_path=repo),
        ]
        for msg in msgs:
            self.assertNotIn(_TEST_PASSWORD, msg)

    def test_no_password_flag_anywhere(self):
        # The docstring legitimately NAMES the flag in prose ("never
        # passed as a --password flag"); what must never appear is the
        # flag as an argument literal the wrapper could pass.
        src = (ROOT / "core" / "rustic_backup.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"--password"', src)
        self.assertNotIn("'--password'", src)

    def test_no_network_on_import(self):
        src = (ROOT / "core" / "rustic_backup.py").read_text(
            encoding="utf-8"
        )
        for banned in ("import socket", "import urllib", "import requests",
                       "urlopen"):
            self.assertNotIn(banned, src)


@unittest.skipUnless(
    _binary_placed_and_pinned(),
    "operator has not placed the pinned rustic binary at data/bin",
)
class RealBinaryTests(_RusticTestCase):
    """The real binary, end to end, in temp dirs only."""

    def test_full_cycle_init_backup_list_restore_status(self):
        repo = self._make_repo()
        src = self._make_source()
        msg = backup_files(sources=[str(src)], repo_path=repo)
        self.assertIn("Backed up 2 file(s)", msg)
        self.assertIn("verified in the repository's snapshot list", msg)
        listing = list_backup_snapshots(repo_path=repo)
        self.assertIn("holds 1 snapshot(s)", listing)
        self.assertIn("src", listing)
        status = backup_status(repo_path=repo)
        self.assertIn("sha256 matches the pinned release hash", status)
        self.assertIn("holds 1 snapshot(s)", status)
        dest = self.tmp / "restored"
        msg = restore_backup(
            snapshot="latest", destination=str(dest), repo_path=repo
        )
        self.assertIn("Restored snapshot latest", msg)
        # The restored layout keeps the backed-up path: for an
        # absolute source that means dest/<tmp-path>/src/a.txt, for
        # a relative one dest/src/a.txt — find the files, don't
        # assume the prefix.
        found_a = list(dest.rglob("a.txt"))
        found_b = list(dest.rglob("b.txt"))
        self.assertEqual(len(found_a), 1)
        self.assertEqual(len(found_b), 1)
        self.assertEqual(
            found_a[0].read_text(encoding="utf-8"), "hello rustic\n"
        )
        self.assertEqual(
            found_b[0].read_text(encoding="utf-8"), "second file\n"
        )

    def test_label_and_default_repo(self):
        repo = self._make_repo()
        src = self._make_source()
        # init wrote the default repo file: no repo_path needed now.
        msg = backup_files(sources=[str(src)], label="weekly")
        self.assertIn("Backed up 2 file(s)", msg)
        self.assertIn("verified in the repository's snapshot list", msg)
        listing = list_backup_snapshots()
        self.assertIn("holds 1 snapshot(s)", listing)

    def test_subpath_restore(self):
        repo = self._make_repo()
        src = self._make_source()
        backup_files(sources=[str(src)], repo_path=repo)
        listing = list_backup_snapshots(repo_path=repo)
        snap_id = listing.splitlines()[1].split()[0]
        # The subpath must be exactly as the snapshot stored it: this
        # backup used absolute sources, so the stored path is absolute
        # too (a relative subpath fails in rustic with "Node ... not
        # found in tree" — measured 2026-11-10).
        dest = self.tmp / "one-file"
        msg = restore_backup(
            snapshot="%s:%s" % (snap_id, src / "sub" / "b.txt"),
            destination=str(dest),
            repo_path=repo,
        )
        self.assertIn("Restored snapshot", msg)
        found = list(dest.rglob("b.txt"))
        self.assertEqual(len(found), 1)
        self.assertEqual(
            found[0].read_text(encoding="utf-8"), "second file\n"
        )

    def test_init_existing_repo_refuses(self):
        repo = self._make_repo()
        msg = init_backup_repo(repo_path=repo)
        self.assertIn("already holds a repository", msg)

    def test_relative_subpath_refuses_plainly(self):
        # The snapshot stored absolute paths, so a relative subpath
        # is not in the tree — the wrapper must say so plainly, not
        # pass through rustic's misleading "internal operations" error.
        repo = self._make_repo()
        src = self._make_source()
        backup_files(sources=[str(src)], repo_path=repo)
        listing = list_backup_snapshots(repo_path=repo)
        snap_id = listing.splitlines()[1].split()[0]
        msg = restore_backup(
            snapshot="%s:src/sub/b.txt" % snap_id,
            destination=str(self.tmp / "out"),
            repo_path=repo,
        )
        self.assertIn("is not in snapshot", msg)
        self.assertIn(str(src), msg)
        self.assertNotIn("internal operations", msg)

    def test_wrong_password_refuses(self):
        repo = self._make_repo()
        os.environ["RUSTIC_PASSWORD"] = _WRONG_PASSWORD
        msg = list_backup_snapshots(repo_path=repo)
        self.assertIn("could not open the repository", msg)
        self.assertNotIn(_WRONG_PASSWORD, msg)
        msg = backup_files(
            sources=[str(self._make_source("src2"))], repo_path=repo
        )
        self.assertIn("backup failed", msg.lower())
        self.assertNotIn(_WRONG_PASSWORD, msg)

    def test_status_without_password_says_so(self):
        repo = self._make_repo()
        os.environ.pop("RUSTIC_PASSWORD", None)
        msg = backup_status(repo_path=repo)
        self.assertIn("sha256 matches the pinned release hash", msg)
        self.assertIn("password is not set in this session", msg)

    def test_unknown_snapshot_restore_refuses(self):
        repo = self._make_repo()
        src = self._make_source()
        backup_files(sources=[str(src)], repo_path=repo)
        msg = restore_backup(
            snapshot="0123456789abcdef0123456789abcdef"
                     "0123456789abcdef0123456789abcdef",
            destination=str(self.tmp / "out"),
            repo_path=repo,
        )
        self.assertIn("restore failed", msg.lower())

    def test_tampered_binary_refuses(self):
        bindir = self.tmp / "bin2"
        bindir.mkdir()
        tampered = bindir / binary_name()
        shutil.copy2(binary_path(), tampered)
        data = bytearray(tampered.read_bytes())
        data[len(data) // 2] ^= 0xFF
        tampered.write_bytes(bytes(data))
        rustic_backup._BIN_DIR = bindir
        os.environ["RUSTIC_PASSWORD"] = _TEST_PASSWORD
        msg = backup_status()
        self.assertIn("does not match the pinned sha256", msg)


class RegistrationTests(unittest.TestCase):
    """The five tools are registered, gated, grouped, and counted."""

    def test_all_five_registered(self):
        names = [d["name"] for d in _registry.get_declarations()]
        for name in _NEW_TOOLS:
            self.assertIn(name, names)

    def test_registry_total_pin(self):
        names = [d["name"] for d in _registry.get_declarations()]
        self.assertEqual(len(names), 756)  # INTENTIONAL — R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_effectful_tools_confirm_gated(self):
        for name in _EFFECTFUL:
            self.assertIn(name, _CONFIRM_GATE_TOOLS)

    def test_read_tools_read_only(self):
        for name in _READ_ONLY:
            self.assertIn(name, _READ_ONLY_TOOLS)
            self.assertNotIn(name, _CONFIRM_GATE_TOOLS)

    def test_no_password_in_any_schema(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        for name in _NEW_TOOLS:
            params = json.dumps(decls[name]["parameters"])
            self.assertNotIn("password", params.lower(), name)
            self.assertNotIn("RUSTIC_PASSWORD", params, name)

    def test_schema_property_count(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        total = sum(
            len(decls[name]["parameters"]["properties"])
            for name in _NEW_TOOLS
        )
        # init 1 + backup 3 + list 1 + restore 3 + status 1 = 9.
        self.assertEqual(total, 9)  # INTENTIONAL — R9-2 rustic adoption (2026-11-10): schema-lint 1563 -> 1572 (+9)

    def test_grouped_in_files_and_media(self):
        from core.catalogue_snapshot import GROUP_MEMBERS

        total = sum(len(v) for v in GROUP_MEMBERS.values())
        self.assertEqual(total, 753)  # INTENTIONAL — R9-2 rustic adoption (2026-11-10): grouped 692 -> 697 — five tools grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine
        group = GROUP_MEMBERS["Files & media"]
        for name in _NEW_TOOLS:
            self.assertIn(name, group)

    def test_classification_snapshot_entries(self):
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(len(snap), 756)  # INTENTIONAL — R9-2 rustic adoption (2026-11-10): 694 -> 699 — five rustic tools added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        for name in _EFFECTFUL:
            self.assertEqual(
                snap[name],
                {"confirm": True, "free": None, "read": False},
                name,
            )
        for name in _READ_ONLY:
            self.assertEqual(
                snap[name],
                {"confirm": False, "free": None, "read": True},
                name,
            )

    def test_third_party_row_is_ruled(self):
        # R9-2 parent edit: the parent read LICENSE-APACHE itself
        # (full Apache 2.0 text, main branch) and ruled the row; the
        # test now asserts the ruled state, not the proposed state.
        text = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("rustic 0.11.4", text)
        self.assertIn("PARENT-RULED", text)
        self.assertNotIn("PROPOSED", text)
        self.assertIn("Apache-2.0", text)

    def test_pinned_hashes(self):
        self.assertEqual(RUSTIC_VERSION, "0.11.4")
        self.assertEqual(
            EXPECTED_BINARY_SHA256["rustic"],
            "3cf6e3d3e1e9de1528b07dcaee220d9882c5921f2a01e510360a1c6be8e9fc13",
        )
        self.assertEqual(
            EXPECTED_BINARY_SHA256["rustic.exe"],
            "3d4bc16c35b9813ff006576dce01e54d947111d16bdfdfe6742d90d0a2fddc5d",
        )


if __name__ == "__main__":
    unittest.main()
