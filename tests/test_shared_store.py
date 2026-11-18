"""GAPS-14 suite: one shared store base, 21 hash-chain copies
migrated onto it.

Mirrors prove_shared_store.py's live checks as unit tests. The
digest vectors below are HARD-CODED: they were computed once
from the pre-migration source's own formulas, so a regression
in any adapter fails here even if the prover's transcription
were wrong in the same way. All stores run on temp dirs, never
the tree's data/.

Determinism notes: the stores take a path, so each test builds
its own temp dir; audit_chain is redirected with configure()
and reset in tearDown. time_tracker / event_ticketing stamp
time.time() into the digest, so those tests recompute the
expected digest from the STORED row (the old formula, inline)
rather than from a fixed ts.
"""

import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from core import store_base as sb

GENESIS = "0" * 64
LEGACY = "GENESIS"

# Hard-coded digest vectors (fixed inputs; computed from the
# pre-migration source's formulas — see prove_shared_store.py).
VECTORS = {
    "canonical": (
        "513964b1d6a3b38daa61aa5e04a6742b28c56d7ab05a3d6dc3b7bcedd0eae830"),
    "json_body_ts": (
        "21dc8c7c1cd34431c53a01115204ee240d7e68255904674a94dac53c22621388"),
    "json_body_no_ts": (
        "4b4a55d737e61d9700d333c35a75eef2fd6b12112364121d56d207496ac0848f"),
    "prev_payload_list": (
        "4d47370762a39ab7b088b6ce00dba499aa97b08b939627951fda451dc1619dbc"),
    "prev_payload_dict": (
        "78d6bdb4586fd4b4d2956f6741231a2d8dbf9d4178aecc6fcea5d29a0ff1b0a5"),
    "pipe_repr": (
        "c5d6e254e45dc946a83c75c78e25a0be67c8cb92c78291b0eb43c8f50fe0a45a"),
    "pipe_fstring": (
        "b010a7b119c53d4c9f97edf1fb7180970154f41cdd28869174d7c8da549d2d5e"),
}


def _rows(db_path, sql):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class BaseConstantsTests(unittest.TestCase):
    def test_genesis_constants(self):
        self.assertEqual(sb.GENESIS, "0" * 64)
        self.assertEqual(sb.LEGACY_GENESIS, "GENESIS")
        self.assertEqual(sb.ADAPTER_GENESIS["canonical"], sb.GENESIS)
        self.assertEqual(sb.ADAPTER_GENESIS["pipe_fstring"],
                         sb.LEGACY_GENESIS)

    def test_seven_adapters_registered(self):
        self.assertEqual(set(sb.ADAPTERS), {
            "canonical", "json_body_ts", "json_body_no_ts",
            "prev_payload_list", "prev_payload_dict", "pipe_repr",
            "pipe_fstring"})

    def test_store_adapters_cover_21_plus_audit_chain(self):
        self.assertEqual(len(sb.STORE_ADAPTERS), 22)
        self.assertIn("audit_chain", sb.STORE_ADAPTERS)
        for name in ("bookings", "lodging", "stage_lighting", "wardrobe",
                     "billing", "mcp_registry", "agent_call",
                     "prompt_tune", "voice_agent", "event_ticketing",
                     "technical_analysis", "time_tracker",
                     "audio_localise", "optimise", "erp", "media_chat",
                     "invoice", "translations", "filament", "deals",
                     "vehicle_routes"):
            self.assertIn(name, sb.STORE_ADAPTERS)
        for adapter in sb.STORE_ADAPTERS.values():
            self.assertIn(adapter, sb.ADAPTERS)

    def test_canonical_json_is_sorted_and_compact(self):
        self.assertEqual(sb.canonical_json({"b": 1, "a": [2, 1]}),
                         '{"a":[2,1],"b":1}')


class AdapterVectorTests(unittest.TestCase):
    def test_canonical_vector(self):
        self.assertEqual(
            sb.canonical_hash(0, "id-1", "2026-11-13T06:00:00", "prove",
                              {"a": 1}, GENESIS),
            VECTORS["canonical"])

    def test_json_body_ts_vector(self):
        self.assertEqual(
            sb.chain_digest("json_body_ts", GENESIS,
                            {"ts": 1700000000.5, "kind": "prove",
                             "payload": {"n": 1}}),
            VECTORS["json_body_ts"])

    def test_json_body_no_ts_vector(self):
        self.assertEqual(
            sb.chain_digest("json_body_no_ts", GENESIS,
                            {"kind": "prove", "payload": {"n": 1}}),
            VECTORS["json_body_no_ts"])

    def test_prev_payload_list_vector(self):
        self.assertEqual(
            sb.chain_digest("prev_payload_list", LEGACY,
                            {"fields": [1700000000, "prove", "stripe-1",
                                        "obj", "detail-1"]}),
            VECTORS["prev_payload_list"])

    def test_prev_payload_dict_vector(self):
        self.assertEqual(
            sb.chain_digest("prev_payload_dict", LEGACY,
                            {"body_json": '{"label": "row-1", "n": 1}'}),
            VECTORS["prev_payload_dict"])

    def test_pipe_repr_vector(self):
        self.assertEqual(
            sb.chain_digest("pipe_repr", LEGACY,
                            {"seq": 1, "ts": 1700000000.5, "kind": "prove",
                             "payload_json": '{"n": 1}'}),
            VECTORS["pipe_repr"])

    def test_pipe_fstring_vector(self):
        self.assertEqual(
            sb.chain_digest("pipe_fstring", LEGACY,
                            {"kind": "prove", "detail": "detail-1",
                             "at": "2026-11-13 06:00:00"}),
            VECTORS["pipe_fstring"])


class VerifyLoopTests(unittest.TestCase):
    def _chain(self, n=3):
        entries = []
        prev = GENESIS
        for i in range(n):
            digest = sb.chain_digest(
                "json_body_ts", prev,
                {"ts": 1700000000.0 + i, "kind": "prove",
                 "payload": {"n": i}})
            entries.append({"ts": 1700000000.0 + i, "kind": "prove",
                            "payload": {"n": i}, "prev_hash": prev,
                            "hash": digest})
            prev = digest
        return entries

    def test_valid_chain_verifies(self):
        res = sb.verify_entries(self._chain(), "json_body_ts", GENESIS)
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], 3)
        self.assertIsNone(res["first_break"])

    def test_empty_chain_verifies(self):
        res = sb.verify_entries([], "json_body_ts", GENESIS)
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], 0)

    def test_hash_mismatch_names_first_break(self):
        entries = self._chain()
        entries[1]["hash"] = "0" * 64
        res = sb.verify_entries(entries, "json_body_ts", GENESIS)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_break"],
                         {"index": 1, "reason": "hash mismatch"})

    def test_link_break_is_a_different_reason(self):
        entries = self._chain()
        entries[2]["prev_hash"] = "0" * 64
        res = sb.verify_entries(entries, "json_body_ts", GENESIS)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_break"]["index"], 2)
        self.assertTrue(res["first_break"]["reason"].startswith("link break"))

    def test_malformed_entry_is_a_break_not_a_crash(self):
        entries = self._chain()
        del entries[1]["payload"]
        res = sb.verify_entries(entries, "json_body_ts", GENESIS)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_break"],
                         {"index": 1, "reason": "malformed entry"})

    def test_wrong_genesis_fails_the_first_link(self):
        res = sb.verify_entries(self._chain(), "json_body_ts", LEGACY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["first_break"]["index"], 0)

    def test_unknown_adapter_is_fail_closed(self):
        with self.assertRaises(KeyError):
            sb.chain_digest("no_such_adapter", GENESIS, {})
        with self.assertRaises(KeyError):
            sb.verify_entries([], "no_such_adapter", GENESIS)


class ConnectTests(unittest.TestCase):
    def test_connect_applies_pragmas_and_row_factory(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        conn = sb.connect(tmp / "c.sqlite3",
                          pragmas=("PRAGMA journal_mode=WAL",),
                          row_factory=sqlite3.Row)
        try:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            conn.execute("CREATE TABLE t (a INTEGER)")
            conn.execute("INSERT INTO t VALUES (7)")
            row = conn.execute("SELECT a FROM t").fetchone()
            self.assertIsInstance(row, sqlite3.Row)
            self.assertEqual(row["a"], 7)
        finally:
            conn.close()

    def test_connect_default_timeout_is_sqlite3_default(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        conn = sb.connect(tmp / "c.sqlite3")
        try:
            self.assertEqual(
                conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        finally:
            conn.close()


class VerifyAllTests(unittest.TestCase):
    def test_verify_all_reports_each_ledger(self):
        good = [{
            "ts": 1700000000.0, "kind": "prove", "payload": {"n": 0},
            "prev_hash": GENESIS,
            "hash": sb.chain_digest("json_body_ts", GENESIS,
                                    {"ts": 1700000000.0, "kind": "prove",
                                     "payload": {"n": 0}})}]
        bad = [dict(good[0], hash="0" * 64)]
        res = sb.verify_all([
            ("good", good, "json_body_ts", GENESIS),
            ("bad", bad, "json_body_ts", GENESIS),
            ("empty", [], "pipe_fstring", LEGACY)])
        self.assertTrue(res["good"]["ok"])
        self.assertFalse(res["bad"]["ok"])
        self.assertTrue(res["empty"]["ok"])


class MigratedStoresTests(unittest.TestCase):
    """One representative store per legacy family, driven through
    the migrated code: the stored hashes must equal the old
    formula recomputed from the stored row, and the store's own
    verify (now base-backed) must pass."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def test_json_body_ts_time_tracker(self):
        from core.time_tracker import _TimeStore
        db = self.tmp / "time_tracker.sqlite3"
        st = _TimeStore(db)
        for i in range(3):
            st.append_event("prove", {"n": i, "label": f"row-{i}"})
        rows = _rows(db, "SELECT ts, kind, payload, prev_hash, hash"
                          " FROM events ORDER BY id")
        self.assertEqual(len(rows), 3)
        for ts, kind, payload, prev_hash, digest in rows:
            body = json.dumps({"ts": ts, "kind": kind,
                               "payload": json.loads(payload),
                               "prev": prev_hash}, sort_keys=True)
            self.assertEqual(
                hashlib.sha256(body.encode("utf-8")).hexdigest(), digest)
        self.assertTrue(st.verify_chain())

    def test_json_body_no_ts_audio_localise(self):
        from core.audio_localise import _AlStore
        db = self.tmp / "audio_localise.sqlite3"
        st = _AlStore(db)
        conn = st._connect()
        try:
            for i in range(3):
                st._append_event_locked(conn, "prove", {"n": i})
            conn.commit()
        finally:
            conn.close()
        rows = _rows(db, "SELECT id, prev_hash, hash, kind, payload"
                          " FROM events ORDER BY id")
        self.assertEqual(len(rows), 3)
        for _i, prev_hash, digest, kind, payload in rows:
            body = json.dumps({"kind": kind,
                               "payload": json.loads(payload),
                               "prev": prev_hash}, sort_keys=True)
            self.assertEqual(
                hashlib.sha256(body.encode("utf-8")).hexdigest(), digest)
        self.assertEqual(st.verify_chain(), (True, 3, None))

    def test_prev_payload_list_billing(self):
        from core.billing import _BillingDB
        db = self.tmp / "billing.sqlite3"
        st = _BillingDB(db)
        try:
            for i in range(3):
                self.assertIsNotNone(
                    st.record("prove", f"stripe-{i}", "obj", f"detail-{i}"))
            rows = _rows(db, "SELECT id, ts, kind, stripe_id, object,"
                              " detail, prev_hash, hash"
                              " FROM billing_events ORDER BY id")
            self.assertEqual(len(rows), 3)
            for _i, ts, kind, sid, obj, det, prev_hash, digest in rows:
                payload = json.dumps([ts, kind, sid, obj, det],
                                     sort_keys=True)
                self.assertEqual(
                    hashlib.sha256((prev_hash + payload).encode("utf-8")
                                   ).hexdigest(), digest)
            ok, msg = st.verify_chain()
            self.assertTrue(ok, msg)
        finally:
            st.close()

    def test_prev_payload_dict_lodging(self):
        from core.lodging import _LodgingDB
        db = self.tmp / "lodging.sqlite3"
        st = _LodgingDB(db)
        for i in range(3):
            st._log_event("prove", {"n": i, "label": f"row-{i}"})
        st._connect().commit()
        st.close()
        rows = _rows(db, "SELECT body_json, prev_hash, hash"
                          " FROM events ORDER BY id")
        self.assertEqual(len(rows), 3)
        for body_json, prev_hash, digest in rows:
            self.assertEqual(
                hashlib.sha256((prev_hash + body_json).encode("utf-8")
                               ).hexdigest(), digest)
        st = _LodgingDB(db)
        ok, err = st.verify_chain()
        self.assertTrue(ok, str(err))
        st.close()

    def test_pipe_repr_event_ticketing(self):
        from core.event_ticketing import _Ledger
        db = self.tmp / "event_tickets.sqlite3"
        st = _Ledger(db)
        for i in range(3):
            st.append_event("prove", {"n": i})
        rows = _rows(db, "SELECT seq, ts, kind, payload_json, prev_hash,"
                          " hash FROM events ORDER BY seq")
        self.assertEqual(len(rows), 3)
        for seq, ts, kind, payload_json, prev_hash, digest in rows:
            expect = hashlib.sha256(
                ("%s|%d|%r|%s|%s" % (prev_hash, seq, ts, kind,
                                     payload_json)).encode("utf-8")
            ).hexdigest()
            self.assertEqual(expect, digest)
        ok, reason = st.verify_chain()
        self.assertTrue(ok, reason)

    def test_pipe_fstring_bookings(self):
        from core.bookings import _BookingsDB, bookings
        db = self.tmp / "bookings.sqlite3"
        st = _BookingsDB(db)
        conn = st._connect()
        try:
            for i in range(3):
                st._append_event(conn, f"BK-{i}", "prove", f"detail-{i}")
            conn.commit()
        finally:
            conn.close()
        st.close()
        rows = _rows(db, "SELECT seq, kind, detail, at_utc, prev_hash,"
                          " hash FROM events ORDER BY seq")
        self.assertEqual(len(rows), 3)
        for _s, kind, detail, at, prev_hash, digest in rows:
            expect = hashlib.sha256(
                f"{prev_hash}|{kind}|{detail}|{at}".encode("utf-8")
            ).hexdigest()
            self.assertEqual(expect, digest)
        out = bookings(action="events", _db_path=db)
        self.assertIn("Chain intact.", out)

    def test_tampered_row_fails_the_store_verify(self):
        from core.time_tracker import _TimeStore
        db = self.tmp / "time_tracker.sqlite3"
        st = _TimeStore(db)
        for i in range(3):
            st.append_event("prove", {"n": i})
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE events SET hash = ? WHERE id = ?",
                     ("0" * 64, 2))
        conn.commit()
        conn.close()
        self.assertFalse(st.verify_chain())


class AuditChainThroughBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        import core.audit_chain as ac
        self.ac = ac
        ac.configure(data_dir=self.tmp / "audit")

    def tearDown(self):
        self.ac.reset_configuration()

    def test_audit_chain_verifies_through_the_base(self):
        for kind, data in (("prove.one", {"a": 1}),
                           ("prove.two", {"b": [1, 2]})):
            self.ac.append(kind, data)
        detail = self.ac.verify_chain_detail()
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["checked"], 2)
        entries = self.ac.read(limit=0)
        res = sb.verify_entries(
            [{"seq": e["seq"], "id": e["id"], "ts": e["ts"],
              "kind": e["kind"], "data": e["data"],
              "prev_hash": e["prev_hash"], "hash": e["hash"]}
             for e in reversed(entries)], "canonical", GENESIS)
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked"], 2)


if __name__ == "__main__":
    unittest.main()
