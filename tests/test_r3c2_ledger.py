"""Round-3 section C fix item 2 (C3-F1): a destroyed ledger chain
must read as broken, never as a healthy empty chain, and an append
must refuse rather than silently fork a fresh chain after garbage.

Rework (2026-11-20): the refusal is narrowed to a destroyed
chain only (zero parseable entries). A partial tear — some valid
entries plus torn lines — still verifies not-ok with the break
named, but the append continues from the last valid entry, so a
crash mid-append can never leave the chain stuck.

All tests use temp dirs, never the shipped data/ dir. The chain
module (and the record family, which the feed marker reads
through) is redirected with configure() and reset in tearDown,
following tests/test_shared_store.py.
"""

import pathlib
import tempfile
import unittest

from core import activity_feed as af
from core import audit_chain as ac
from core import record_family as rf

GARBAGE = ("GARBAGE-NOT-JSON\n{not json at all}\n"
           "[1, 2, 3]\n\"just a string\"\n")


class _ChainBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        ac.configure(data_dir=self.tmp)
        rf.configure(data_dir=self.tmp)
        self.addCleanup(rf.reset_configuration)
        self.addCleanup(ac.reset_configuration)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, n=3):
        return [ac.append("c3f1-seed", {"n": i, "marker": "C3F1-SEED"})
                for i in range(n)]

    def _corrupt(self, text=GARBAGE):
        ac.store_path().write_text(text, encoding="utf-8")


class PureGarbageTests(_ChainBase):
    def test_verify_pure_garbage_not_ok_checked_0_named_reason(self):
        self._seed()
        self._corrupt()
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertEqual(detail["checked"], 0)
        self.assertIsNotNone(detail["first_break"])
        self.assertEqual(detail["first_break"]["segment"], "live")
        self.assertIn("unparseable line",
                      detail["first_break"]["reason"])
        self.assertIn("none parseable",
                      detail["first_break"]["reason"])
        self.assertFalse(ac.verify_chain())

    def test_append_after_garbage_refuses_and_bytes_intact(self):
        self._seed()
        self._corrupt()
        before = ac.store_path().read_bytes()
        self.assertEqual(ac.append("c3f1-after", {"marker": "AFTER"}), "")
        self.assertEqual(ac.store_path().read_bytes(), before)

    def test_refusal_names_file_and_state(self):
        self._seed()
        self._corrupt()
        ac.append("c3f1-after", {"marker": "AFTER"})
        refusal = ac.last_append_refusal()
        self.assertIsNotNone(refusal)
        self.assertIn(str(ac.store_path()), refusal["path"])
        self.assertIn("unparseable", refusal["reason"].lower())
        self.assertEqual(refusal["nonblank_lines"], 4)
        self.assertEqual(refusal["unparseable_lines"], 4)
        self.assertEqual(refusal["first_bad_line"], 0)

    def test_verify_still_not_ok_after_append_attempt(self):
        self._seed()
        self._corrupt()
        ac.append("c3f1-after", {"marker": "AFTER"})
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertEqual(detail["checked"], 0)
        self.assertIsNotNone(detail["first_break"])

    def test_feed_marker_present_after_append_attempt(self):
        self._seed()
        self._corrupt()
        ac.append("c3f1-after", {"marker": "AFTER"})
        rows = af._read_chain(50)
        markers = [r for r in rows if r.get("kind") == "unavailable"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].get("source"), "activity")

    def test_rotate_refuses_on_garbage(self):
        self._seed()
        self._corrupt()
        before = ac.store_path().read_bytes()
        self.assertEqual(ac.rotate_now(), "")
        self.assertEqual(ac.store_path().read_bytes(), before)
        self.assertFalse(ac.verify_chain())


class HealthyChainTests(_ChainBase):
    def test_healthy_chain_verifies_and_appends(self):
        ids = self._seed()
        self.assertTrue(all(ids))
        detail = ac.verify_chain_detail()
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["checked"], 3)
        self.assertIsNone(detail["first_break"])
        self.assertTrue(ac.verify_chain())
        eid = ac.append("c3f1-more", {"n": 3})
        self.assertTrue(eid)
        entries = ac.read(limit=0)
        self.assertEqual([e["seq"] for e in reversed(entries)],
                         [0, 1, 2, 3])
        self.assertTrue(ac.verify_chain_detail()["ok"])

    def test_empty_file_verifies_and_appends(self):
        detail = ac.verify_chain_detail()
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["checked"], 0)
        self.assertIsNone(detail["first_break"])
        self.assertTrue(ac.append("c3f1-first", {"n": 0}))
        self.assertTrue(ac.verify_chain_detail()["ok"])

    def test_blank_only_file_verifies(self):
        ac.store_path().parent.mkdir(parents=True, exist_ok=True)
        ac.store_path().write_text("\n\n   \n", encoding="utf-8")
        detail = ac.verify_chain_detail()
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["checked"], 0)
        self.assertTrue(ac.append("c3f1-first", {"n": 0}))


class PartiallyTornTests(_ChainBase):
    def _torn(self):
        self._seed(3)
        with open(ac.store_path(), "a", encoding="utf-8") as f:
            f.write("GARBAGE-TORN-LINE\n")

    def test_partially_torn_verify_not_ok_break_named(self):
        self._torn()
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        # The three good entries before the torn line still verify.
        self.assertEqual(detail["checked"], 3)
        self.assertIsNotNone(detail["first_break"])
        self.assertEqual(detail["first_break"]["segment"], "live")
        self.assertEqual(detail["first_break"]["index"], 3)
        self.assertIn("unparseable line 3",
                      detail["first_break"]["reason"])

    def test_append_after_partial_torn_continues_from_last_valid(self):
        # Round-3 C3-F1 rework: a partial tear is NOT a destroyed
        # chain — there is no fork, so the append continues from
        # the last valid entry (seq continues, prev_hash chains
        # from the last valid entry's hash) and the torn bytes
        # stay in the file untouched; verify still names the break.
        self._torn()
        before = ac.store_path().read_bytes()
        entries_before = ac.read(limit=0)
        last_hash = [e for e in entries_before
                     if e["seq"] == 2][0]["hash"]
        eid = ac.append("c3f1-after", {"n": 9})
        self.assertTrue(eid)
        after = ac.store_path().read_bytes()
        self.assertIn(b"GARBAGE-TORN-LINE", after)
        self.assertTrue(after.startswith(before))
        entries = ac.read(limit=0)
        self.assertEqual([e["seq"] for e in reversed(entries)],
                         [0, 1, 2, 3])
        new = [e for e in entries if e["seq"] == 3][0]
        self.assertEqual(new["prev_hash"], last_hash)
        self.assertEqual(new["id"], eid)
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertIn("unparseable line 3",
                      detail["first_break"]["reason"])

    def test_torn_last_line_crash_mid_append(self):
        # The key rework case: a crash mid-append leaves a torn
        # LAST line (a truncated record, no trailing newline).
        # The next append must continue the chain correctly AND
        # verify must still report the break — both at once.
        self._seed(3)
        entries_before = ac.read(limit=0)
        last_hash = [e for e in entries_before
                     if e["seq"] == 2][0]["hash"]
        with open(ac.store_path(), "a", encoding="utf-8") as f:
            f.write('{"seq": 3, "id": "abc')
        eid = ac.append("c3f1-after", {"n": 9})
        self.assertTrue(eid)
        entries = ac.read(limit=0)
        self.assertEqual([e["seq"] for e in reversed(entries)],
                         [0, 1, 2, 3])
        new = [e for e in entries if e["seq"] == 3][0]
        self.assertEqual(new["prev_hash"], last_hash)
        self.assertEqual(new["id"], eid)
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertEqual(detail["first_break"]["index"], 3)
        self.assertIn("unparseable line 3",
                      detail["first_break"]["reason"])
        # The torn bytes are still in the file, untouched.
        self.assertIn(b'{"seq": 3, "id": "abc',
                      ac.store_path().read_bytes())

    def test_torn_middle_line_append_continues(self):
        # A tear in the middle of the file: the append still
        # chains from the last valid entry, and verify names the
        # middle break by line number.
        self._seed(3)
        lines = ac.store_path().read_text(
            encoding="utf-8").splitlines()
        lines.insert(1, "GARBAGE-TORN-LINE")
        ac.store_path().write_text("\n".join(lines) + "\n",
                                   encoding="utf-8")
        entries_before = ac.read(limit=0)
        last_hash = [e for e in entries_before
                     if e["seq"] == 2][0]["hash"]
        eid = ac.append("c3f1-after", {"n": 9})
        self.assertTrue(eid)
        entries = ac.read(limit=0)
        self.assertEqual([e["seq"] for e in reversed(entries)],
                         [0, 1, 2, 3])
        new = [e for e in entries if e["seq"] == 3][0]
        self.assertEqual(new["prev_hash"], last_hash)
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertEqual(detail["first_break"]["index"], 1)
        self.assertIn("unparseable line 1",
                      detail["first_break"]["reason"])

    def test_valid_entries_still_readable(self):
        # Read tolerance is unchanged: the good entries survive.
        self._torn()
        entries = ac.read(limit=0)
        self.assertEqual(len(entries), 3)
        self.assertEqual(sorted(e["seq"] for e in entries), [0, 1, 2])


class ArchiveCorruptionTests(_ChainBase):
    def test_corrupt_archive_segment_is_not_ok(self):
        self._seed()
        ac.rotate_now()
        arch_files = sorted((self.tmp / "records_archive").glob("*.jsonl"))
        self.assertEqual(len(arch_files), 1)
        arch_files[0].write_text("GARBAGE-NOT-JSON\n", encoding="utf-8")
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertTrue(detail["first_break"]["segment"].startswith(
            "archive:"))
        self.assertIn("unparseable line",
                      detail["first_break"]["reason"])


class OldBreaksStillNamedTests(_ChainBase):
    def test_hash_mismatch_still_named(self):
        self._seed()
        lines = ac.store_path().read_text(encoding="utf-8").splitlines()
        import json as _json
        entry = _json.loads(lines[1])
        entry["hash"] = "0" * 64
        lines[1] = _json.dumps(entry, sort_keys=True,
                               separators=(",", ":"))
        ac.store_path().write_text("\n".join(lines) + "\n",
                                   encoding="utf-8")
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertEqual(detail["first_break"]["reason"],
                         "hash mismatch")
        self.assertEqual(detail["first_break"]["index"], 1)

    def test_link_break_still_named(self):
        self._seed()
        lines = ac.store_path().read_text(encoding="utf-8").splitlines()
        import json as _json
        entry = _json.loads(lines[2])
        entry["prev_hash"] = "0" * 64
        # Recompute the hash so only the link is broken.
        from core import store_base as sb
        entry["hash"] = sb.canonical_hash(
            entry["seq"], entry["id"], entry["ts"], entry["kind"],
            entry["data"], entry["prev_hash"])
        lines[2] = _json.dumps(entry, sort_keys=True,
                               separators=(",", ":"))
        ac.store_path().write_text("\n".join(lines) + "\n",
                                   encoding="utf-8")
        detail = ac.verify_chain_detail()
        self.assertFalse(detail["ok"])
        self.assertTrue(detail["first_break"]["reason"].startswith(
            "link break"))
        self.assertEqual(detail["first_break"]["index"], 2)


if __name__ == "__main__":
    unittest.main()
