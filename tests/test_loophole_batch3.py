"""Loophole batch 3 — secrets in stores: L-33, #10, L-23, L-30, #11.

The five items, one build (2026-11-22, user-approved: close all
32 Stage 0 loopholes):

* L-33 (receipts store tool arguments with no secret scrub): a
  password in a run_command landed in the receipt, the prune
  archive and the backup. The existing secret scrub
  (core/distribution_hardening._scrub_text, re-exported from
  core/confirm_gate_scrub) now also masks `-u user:pass`,
  `--password <value>`, `password is X` and bare `user:pass@host`;
  the receipt store scrubs args, compensations, manual steps and
  outcome summaries at the write boundary, and the guard
  signature is computed over scrubbed args (a hash of a password
  is still a verifier for it).
* #10 (the backup must be encrypted): a backup is an encrypted
  ``VYUKBK1`` bundle (PBKDF2-HMAC-SHA256 + HMAC-CTR +
  encrypt-then-MAC, mirroring core/secrets_store) under a user
  passphrase; without one the backup refuses and says so.
  Verify/restore integrity-check first; a tampered bundle or a
  wrong passphrase is refused and applies nothing; a legacy plain
  .zip is refused, never silently treated as encrypted. The
  ledger and the receipts are never collected.
* L-23 (the backup includes the full chat history): transcripts
  are stripped from the assistant.db snapshot (DELETE + VACUUM,
  so no transcript bytes linger) and the stripped count is
  reported; facts, memories and milestones still back up.
* L-30 (clipboard + learn path = secret capture): the learn path
  refuses a secret-shaped teaching before saving it (and records
  the refusal — key only — for the chat layer to announce), and a
  clipboard read that embeds a credential shape is scrubbed
  before it reaches the conversation.
* #11 (taught facts have no provenance and no secret check):
  every taught fact records its channel ("learned:voice" or
  "learned:text") on the stored row, and the secret guard runs
  before learning anything — in the learn path, in
  remember_user_fact, and in the store itself (save_fact raises
  for user-fact categories; skill categories are untouched).

The suite runs under both interpreters (sys + Qt). The Settings
card legs are Qt-only (skipped under sys, which has no PySide6);
every other leg is interpreter-neutral. The funnel legs go
through the real funnel (tools.execute) with only the clipboard
boundary stubbed (pyperclip.paste) and a temp receipt store;
the backup legs go through the real backup/restore path on temp
dirs; the learn legs go through the real learn path on a temp
MemoryStore. The suite leaves data/ as it found it.
"""

import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import types as _types_mod
import unittest
import zipfile
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Snapshot the live data/ dir HERE — at module import, BEFORE any
# core.* import below. Importing core.memory (via the funnel or
# directly) creates the global MemoryStore, whose __init__ migrates
# the live assistant.db (ALTER TABLE facts ADD COLUMN source) —
# a schema change that leaves the row counts identical but the
# file bytes different, so a snapshot taken in setUpModule (after
# the imports) would capture the migrated file and the suite could
# never restore data/ to the state it found. The snapshot is a
# module global (a strong reference, never garbage-collected) and
# tearDownModule restores from it.
import shutil as _shutil

_DATA_SNAPSHOT = tempfile.TemporaryDirectory(prefix="b3_test_data_")
if (ROOT / "data").is_dir():
    _shutil.copytree(ROOT / "data", _DATA_SNAPSHOT.name, dirs_exist_ok=True)

import core.backup as backup_mod
import core.confirm_gate as confirm_gate
import core.injection as injection
import core.tool_receipts as tool_receipts
from core import w10_wiring as _w10
from core.distribution_hardening import _scrub_text
from core.funnel import rule_of_two as _rot
from core.funnel.tools_registry import tools
from core.memory import learning as learn
from core.memory.memory import MemoryStore
from core.tool_receipts_normalize import args_signature
from core.tool_receipts_store import ReceiptStore

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = _QLabel = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None
SettingsAdvCardsMixin = None

PASS = "b3-test-passphrase"


def _register_handler(fn):
    """Register a confirm handler on every confirm_gate module
    identity present (the flat core.* one the funnel reads, and
    the voice_assistant.* one the GUI synthesises)."""
    confirm_gate.register_handler(fn)
    va = sys.modules.get("voice_assistant.core.confirm_gate")
    if va is not None and va is not confirm_gate:
        try:
            va.register_handler(fn)
        except Exception:  # noqa: BLE001 — the flat one rules
            pass


def _clear_handler():
    try:
        confirm_gate.clear_handler()
    except Exception:  # noqa: BLE001 — nothing registered
        pass
    va = sys.modules.get("voice_assistant.core.confirm_gate")
    if va is not None and va is not confirm_gate:
        try:
            va.clear_handler()
        except Exception:  # noqa: BLE001 — nothing registered
            pass


def setUpModule():
    """Snapshot the voice_assistant.* modules; under Qt, build
    the alias and the QApplication (mirrors test_loophole_batch2)
    and import the Settings mixin. The live data/ snapshot is
    taken at module import (see _DATA_SNAPSHOT above) — before
    any core import could migrate assistant.db — so setUpModule
    does not snapshot data/."""
    global _APP, _VA_SNAPSHOT, SettingsAdvCardsMixin
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    try:
        from gui.window_settings_advcards import (  # noqa: E402
            SettingsAdvCardsMixin as _SACM)
        SettingsAdvCardsMixin = _SACM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        SettingsAdvCardsMixin = None


def tearDownModule():
    """Restore the voice_assistant.* modules and the live data/
    dir (from the import-time snapshot), so the suite leaves the
    tree as it found it."""
    import shutil
    _live_data = ROOT / "data"
    if _live_data.is_dir():
        shutil.rmtree(_live_data)
    _live_data.mkdir(parents=True)
    shutil.copytree(_DATA_SNAPSHOT.name, _live_data, dirs_exist_ok=True)
    _DATA_SNAPSHOT.cleanup()
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    for key, mod in _VA_SNAPSHOT.items():
        sys.modules[key] = mod


# ------------------------------------------------------------ the scrub
class ScrubTextTests(unittest.TestCase):
    """The extended _scrub_text (L-33): the four new shapes mask,
    ordinary text is untouched."""

    def test_basic_auth_flag_masked(self):
        out = _scrub_text("curl -u alice:MyP@ss123 https://example.com")
        self.assertNotIn("MyP@ss123", out)
        self.assertNotIn("alice:", out)
        self.assertIn("curl -u [redacted]", out)
        self.assertIn("https://example.com", out)

    def test_user_flag_long_form_masked(self):
        out = _scrub_text("curl --user bob:S3cret! https://example.com")
        self.assertNotIn("S3cret!", out)
        self.assertIn("--user [redacted]", out)

    def test_password_flag_masked(self):
        for cmd in ("--password hunter2", "--password=hunter2",
                    "-p hunter2"):
            out = _scrub_text(f"mysql {cmd} -h db")
            self.assertNotIn("hunter2", out, cmd)
            self.assertIn("[redacted]", out, cmd)

    def test_password_is_masked(self):
        out = _scrub_text("my password is hunter2")
        self.assertNotIn("hunter2", out)
        self.assertIn("password is [redacted]", out)

    def test_bare_credential_masked(self):
        out = _scrub_text("login as alice:MyP@ss123@example.com please")
        self.assertNotIn("MyP@ss123", out)
        self.assertIn("alice:[redacted]@example.com", out)

    def test_mailto_untouched(self):
        # A mailto: URI is not a credential — its "user" would be
        # the scheme word.
        self.assertEqual(_scrub_text("mailto:alice@example.com"),
                         "mailto:alice@example.com")

    def test_ordinary_prose_untouched(self):
        for text in ("buy milk", "the meeting is at 3pm",
                     "see section 3 for details",
                     "my favourite food is dosa"):
            self.assertEqual(_scrub_text(text), text, text)

    def test_bare_dash_u_untouched(self):
        self.assertEqual(_scrub_text("python -u script.py"),
                         "python -u script.py")

    def test_existing_shapes_still_masked(self):
        self.assertNotIn("hunter2", _scrub_text("password=hunter2"))
        self.assertNotIn("tok123", _scrub_text("Bearer tok123"))
        self.assertNotIn("hunter2", _scrub_text(
            "https://alice:hunter2@example.com/x"))
        self.assertNotIn("a" * 40, _scrub_text("token " + "a" * 40))

    def test_url_without_credentials_untouched(self):
        self.assertEqual(_scrub_text("https://example.com/path"),
                         "https://example.com/path")

    def test_host_port_untouched(self):
        self.assertEqual(_scrub_text("localhost:8080"), "localhost:8080")
        self.assertEqual(_scrub_text("example.com:443"), "example.com:443")


# ------------------------------------------------------------ receipts
class ReceiptScrubTests(unittest.TestCase):
    """L-33 through the real funnel into a temp receipt store."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="b3_test_receipts_")
        self.addCleanup(self._tmp.cleanup)
        self.receipts = pathlib.Path(self._tmp.name) / "tool_receipts.jsonl"
        tool_receipts.install_store(self.receipts)
        self.addCleanup(tool_receipts.reset_default)
        self._asks = []
        self._decision = {"ok": True}
        _lock = threading.Lock()

        def _handler(name, summary=None, *a, **k):
            with _lock:
                self._asks.append((name, summary))
            return self._decision["ok"]

        self._handler = _handler
        _register_handler(_handler)
        self.addCleanup(_clear_handler)
        injection.clear_taint(force=True)
        _rot.reset_for_tests()

    def _read(self):
        return self.receipts.read_text(
            encoding="utf-8") if self.receipts.exists() else ""

    def test_run_command_receipt_carries_no_password(self):
        result = tools.execute("run_command", {
            "command": "echo curl -u alice:MyP@ss123 https://example.com"})
        self.assertEqual(result.get("status"), "success")
        self.assertNotIn("MyP@ss123", self._read())

    def test_outcome_summary_carries_no_password(self):
        tools.execute("run_command", {
            "command": "echo curl -u alice:MyP@ss123 https://example.com"})
        summaries = [json.loads(line).get("summary", "")
                     for line in self._read().splitlines() if line.strip()]
        self.assertTrue(summaries)
        for summary in summaries:
            self.assertNotIn("MyP@ss123", summary)

    def test_compensations_carry_no_password(self):
        store = ReceiptStore(self.receipts)
        store.record_attempt(
            tool="file_control", args={"action": "copy"},
            compensations=[{"tool": "run_command",
                            "args": {"command":
                                     "curl -u bob:S3cret! https://x"}}],
            manual_steps=["run curl -u carol:MyP@ss123 https://x"])
        self.assertNotIn("S3cret!", self._read())
        self.assertNotIn("MyP@ss123", self._read())

    def test_prune_archive_carries_no_password(self):
        small = pathlib.Path(self._tmp.name) / "small.jsonl"
        store = ReceiptStore(small, max_lines=3)
        for i in range(5):
            store.record_attempt(
                tool="run_command",
                args={"command": f"echo turn{i} curl -u carol:MyP@ss123"})
        ReceiptStore(small, max_lines=3).attempts()  # reload -> prune
        arch = pathlib.Path(self._tmp.name) / "tool_receipts_archive"
        self.assertTrue(arch.is_dir())
        archived = "".join(
            f.read_text(encoding="utf-8", errors="replace")
            for f in arch.iterdir())
        self.assertTrue(archived)
        self.assertNotIn("MyP@ss123", archived)

    def test_plain_command_still_recorded_in_full(self):
        tools.execute("run_command", {"command": "echo hello world"})
        self.assertIn("echo hello world", self._read())

    def test_signature_uses_scrubbed_args(self):
        sig1 = args_signature(
            "run_command",
            {"command": "curl -u alice:pass-one https://x"})
        sig2 = args_signature(
            "run_command",
            {"command": "curl -u alice:pass-two https://x"})
        self.assertEqual(sig1, sig2)
        store = ReceiptStore(self.receipts)
        attempt = store.record_attempt(
            tool="run_command",
            args={"command": "curl -u alice:pass-one https://x"})
        store.mark_dispatched(attempt["receipt_id"])
        found = store.find_unknown(
            tool="run_command",
            args={"command": "curl -u alice:pass-two https://x"})
        self.assertIsNotNone(found)
        self.assertEqual(found["receipt_id"], attempt["receipt_id"])


# -------------------------------------------------------------- backup
class BackupEncryptionTests(unittest.TestCase):
    """#10 / L-23 through the real backup/restore path on temp dirs."""

    def _fixture_data(self, tmp):
        data = pathlib.Path(tmp) / "data"
        (data / "memories").mkdir(parents=True)
        (data / "memories" / "note.md").write_text("# note\n",
                                                   encoding="utf-8")
        (data / "milestones.jsonl").write_text(
            '{"event": "b3_fixture"}\n', encoding="utf-8")
        (data / "tool_receipts.jsonl").write_text(
            '{"type": "attempt", "tool": "run_command"}\n',
            encoding="utf-8")
        (data / "cost_ledger.jsonl").write_text(
            '{"amount": 1}\n', encoding="utf-8")
        store = MemoryStore(db_path=data / "assistant.db")
        store.save_fact("general", "b3_fact", "b3_value")
        store.log_message("user", "remember this turn MARKER_ALPHA please")
        store.log_message("assistant", "noted MARKER_ALPHA for you")
        return data

    def test_create_without_passphrase_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            with self.assertRaises(ValueError) as ctx:
                backup_mod.create_backup(str(data), {"k": "v"}, str(out))
            self.assertIn("passphrase", str(ctx.exception).lower())
            self.assertFalse(out.exists())

    def test_create_with_passphrase_encrypts_and_hides_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            report = backup_mod.create_backup(
                str(data), {"k": "v"}, str(out), passphrase=PASS)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"VYUKBK1"))
            self.assertNotIn(b"PK\x03\x04", raw[:7])
            self.assertNotIn(b"MARKER_ALPHA", raw)
            self.assertTrue(report.get("encrypted"))
            self.assertEqual(report.get("transcripts_stripped"), 2)

    def test_verify_right_wrong_tampered(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            backup_mod.create_backup(str(data), {"k": "v"}, str(out),
                                     passphrase=PASS)
            self.assertTrue(
                backup_mod.verify_bundle(str(out), passphrase=PASS
                                         ).get("ok"))
            wrong = backup_mod.verify_bundle(str(out), passphrase="nope")
            self.assertFalse(wrong.get("ok"))
            raw = bytearray(out.read_bytes())
            raw[len(raw) // 2] ^= 0x01
            tpath = pathlib.Path(tmp) / "tampered.vyukbak"
            tpath.write_bytes(bytes(raw))
            tamp = backup_mod.verify_bundle(str(tpath), passphrase=PASS)
            self.assertFalse(tamp.get("ok"))
            self.assertTrue(
                any("integrity" in str(f).lower()
                    for f in tamp.get("failed", [])))

    def test_restore_keeps_facts_drops_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            backup_mod.create_backup(str(data), {"k": "v"}, str(out),
                                     passphrase=PASS)
            target = pathlib.Path(tmp) / "restored"
            target.mkdir()
            report = backup_mod.restore_bundle(
                str(out), str(target), passphrase=PASS)
            self.assertEqual(report.get("settings"), {"k": "v"})
            conn = sqlite3.connect(str(target / "assistant.db"))
            try:
                n_logs = conn.execute(
                    "SELECT COUNT(*) FROM conversation_logs"
                    ).fetchone()[0]
                row = conn.execute(
                    "SELECT value FROM facts WHERE key='b3_fact'"
                    ).fetchone()
            finally:
                conn.close()
            self.assertEqual(n_logs, 0)
            self.assertEqual(row[0], "b3_value")

    def test_restore_wrong_passphrase_applies_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            backup_mod.create_backup(str(data), {"k": "v"}, str(out),
                                     passphrase=PASS)
            target = pathlib.Path(tmp) / "restored"
            target.mkdir()
            report = backup_mod.restore_bundle(
                str(out), str(target), passphrase="nope")
            self.assertTrue(report.get("failed"))
            self.assertFalse((target / "assistant.db").exists())

    def test_legacy_plain_zip_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "legacy.zip"
            with zipfile.ZipFile(out, "w") as zf:
                zf.writestr("manifest.json", '{"format": "old"}')
            check = backup_mod.verify_bundle(str(out), passphrase=PASS)
            self.assertFalse(check.get("ok"))
            self.assertTrue(any("legacy" in str(f).lower()
                                for f in check.get("failed", [])))
            target = pathlib.Path(tmp) / "restored"
            target.mkdir()
            report = backup_mod.restore_bundle(
                str(out), str(target), passphrase=PASS)
            self.assertTrue(report.get("failed"))
            self.assertFalse((target / "assistant.db").exists())

    def test_bundle_excludes_receipts_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            backup_mod.create_backup(str(data), {"k": "v"}, str(out),
                                     passphrase=PASS)
            inner = backup_mod.decrypt_backup_blob(out.read_bytes(), PASS)
            names = zipfile.ZipFile(io.BytesIO(inner)).namelist()
            self.assertFalse(any("receipt" in n or "ledger" in n
                                 for n in names))

    def test_passphrase_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp) / "data"
            data.mkdir()
            self.assertFalse(backup_mod.has_backup_passphrase(str(data)))
            backup_mod.set_backup_passphrase(str(data), PASS)
            self.assertTrue(backup_mod.has_backup_passphrase(str(data)))
            self.assertEqual(backup_mod.get_backup_passphrase(str(data)),
                             PASS)
            # The passphrase file never enters a backup.
            (data / "memories").mkdir()
            out = pathlib.Path(tmp) / "bundle.vyukbak"
            backup_mod.create_backup(str(data), {}, str(out),
                                     passphrase=PASS)
            inner = backup_mod.decrypt_backup_blob(out.read_bytes(), PASS)
            names = zipfile.ZipFile(io.BytesIO(inner)).namelist()
            self.assertNotIn("backup_passphrase.bin", names)
            backup_mod.set_backup_passphrase(str(data), "")
            self.assertFalse(backup_mod.has_backup_passphrase(str(data)))

    def test_backup_tick_refuses_without_runs_with_passphrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick_data = pathlib.Path(tmp) / "tickdata"
            tick_data.mkdir()
            r1 = _w10.backup_tick(datetime.now(timezone.utc),
                                  str(tick_data), {})
            self.assertEqual(r1.get("record", {}).get("last_status"),
                             "failed")
            self.assertIn("passphrase", str(
                r1.get("record", {}).get("last_failure") or "").lower())
            backup_mod.set_backup_passphrase(str(tick_data), PASS)
            r2 = _w10.backup_tick(datetime.now(timezone.utc),
                                  str(tick_data), {})
            self.assertTrue(r2.get("ok"))
            self.assertTrue(r2.get("ran"))
            bundles = list((tick_data / "backups").glob("*.vyukbak"))
            self.assertEqual(len(bundles), 1)

    def test_portability_export_import_round_trip(self):
        from core import portability as _port

        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture_data(tmp)
            out = pathlib.Path(tmp) / "export.vyukbak"
            with self.assertRaises(ValueError):
                _port.export_bundle(str(data), {"k": "v"}, str(out))
            self.assertFalse(out.exists())
            _port.export_bundle(str(data), {"k": "v"}, str(out),
                                passphrase=PASS)
            self.assertTrue(out.read_bytes().startswith(b"VYUKBK1"))
            target = pathlib.Path(tmp) / "imported"
            target.mkdir()
            failed = _port.import_bundle(str(out), str(target))
            self.assertTrue(failed.get("failed"))
            report = _port.import_bundle(str(out), str(target),
                                         passphrase=PASS)
            self.assertEqual(report.get("settings"), {"k": "v"})
            self.assertIn("memories/", report.get("applied", []))


# --------------------------------------------------------------- learn
class LearnGuardTests(unittest.TestCase):
    """L-30 / #11 through the real learn path on a temp store."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="b3_test_learn_")
        self.addCleanup(self._tmp.cleanup)
        self.store = MemoryStore(
            db_path=pathlib.Path(self._tmp.name) / "assistant.db")
        import core.funnel.tools_handlers_command_url as cmd_mod
        import core.memory.memory as mem_mod
        self._saved = (learn.memory_store, mem_mod.memory_store,
                       cmd_mod.memory_store)
        learn.memory_store = self.store
        mem_mod.memory_store = self.store
        cmd_mod.memory_store = self.store
        self.addCleanup(self._restore_stores)
        self._decision = {"ok": True}
        _lock = threading.Lock()
        self._asks = []

        def _handler(name, summary=None, *a, **k):
            with _lock:
                self._asks.append((name, summary))
            return self._decision["ok"]

        _register_handler(_handler)
        self.addCleanup(_clear_handler)
        injection.clear_taint(force=True)
        _rot.reset_for_tests()

    def _restore_stores(self):
        import core.funnel.tools_handlers_command_url as cmd_mod
        import core.memory.memory as mem_mod
        learn.memory_store, mem_mod.memory_store, \
            cmd_mod.memory_store = self._saved

    def test_password_fact_refused(self):
        got = learn.learn_from_text("my password is MyP@ss123")
        self.assertEqual(got, [])
        self.assertIsNone(self.store.get_fact("learned", "my_password"))
        refused = learn.take_refused_secrets()
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["key"], "my_password")
        self.assertNotIn("MyP@ss123", json.dumps(refused))
        self.assertEqual(learn.take_refused_secrets(), [])

    def test_token_and_pin_refused(self):
        got = learn.learn_from_text(
            "my token is sk-proj-abcdefghij1234567890")
        self.assertEqual(got, [])
        self.assertIsNone(self.store.get_fact("learned", "my_token"))
        got2 = learn.learn_from_text("my pin is 4829")
        self.assertEqual(got2, [])
        self.assertIsNone(self.store.get_fact("learned", "my_pin"))

    def test_ordinary_fact_stores_with_voice_provenance(self):
        got = learn.learn_from_text("my name is Aayur",
                                    source="learned:voice")
        self.assertTrue(got)
        row = self.store.get_fact("learned", "name")
        self.assertEqual(row.get("value"), "Aayur")
        self.assertEqual(row.get("source"), "learned:voice")

    def test_typed_fact_stores_with_text_provenance(self):
        got = learn.learn_from_text("my city is Mumbai",
                                    source="learned:text")
        self.assertTrue(got)
        row = self.store.get_fact("learned", "my_city")
        self.assertEqual(row.get("value"), "Mumbai")
        self.assertEqual(row.get("source"), "learned:text")

    def test_save_fact_raises_for_credential_in_user_category(self):
        with self.assertRaises(ValueError):
            self.store.save_fact("general", "wifi_password", "hunter2")
        self.assertIsNone(self.store.get_fact("general", "wifi_password"))
        with self.assertRaises(ValueError):
            self.store.save_fact("preferences", "my_token",
                                 "sk-proj-abcdefghij1234567890")
        self.assertIsNone(self.store.get_fact("preferences", "my_token"))

    def test_save_fact_allows_skill_category_with_credential_word(self):
        # A flashcard whose front says "password" is content, not
        # a credential — only the user's own fact categories are
        # guarded.
        self.assertTrue(self.store.save_fact(
            "flashcards", "password", "what keeps an account safe?"))
        row = self.store.get_fact("flashcards", "password")
        self.assertEqual(row.get("value"),
                         "what keeps an account safe?")

    def test_remember_user_fact_refuses_credential(self):
        result = tools.execute("remember_user_fact", {
            "key": "my_password", "value": "MyP@ss123"})
        self.assertEqual(result.get("status"), "failure")
        msg = str(result.get("message", "")) + str(result.get("result", ""))
        self.assertTrue("password" in msg.lower()
                        or "credential" in msg.lower()
                        or "secret" in msg.lower())
        self.assertNotIn("MyP@ss123", msg)
        self.assertIsNone(self.store.get_fact("general", "my_password"))
        self.assertIsNone(self.store.get_fact("learned", "my_password"))

    def test_remember_user_fact_stores_ordinary_fact(self):
        result = tools.execute("remember_user_fact", {
            "key": "favourite_food", "value": "dosa"})
        self.assertEqual(result.get("status"), "success")
        row = self.store.get_fact("general", "favourite_food")
        self.assertEqual(row.get("value"), "dosa")

    def test_refused_fact_receipt_carries_no_password(self):
        # The refusal is recorded BEFORE the handler runs, so the
        # receipt attempt itself must not carry the secret — the
        # args name a credential, so the sibling value masks.
        receipts = pathlib.Path(self._tmp.name) / "tool_receipts.jsonl"
        tool_receipts.install_store(receipts)
        self.addCleanup(tool_receipts.reset_default)
        result = tools.execute("remember_user_fact", {
            "key": "my_password", "value": "MyP@ss123"})
        self.assertEqual(result.get("status"), "failure")
        lines = receipts.read_text(
            encoding="utf-8") if receipts.exists() else ""
        self.assertTrue(lines)
        self.assertNotIn("MyP@ss123", lines)
        # The fact key itself is kept (it says what was refused),
        # the secret value is not.
        self.assertIn("my_password", lines)

    def test_clipboard_masks_embedded_secret_keeps_ordinary_text(self):
        import types as _types

        orig = sys.modules.get("pyperclip")

        def _fake(text):
            mod = _types.ModuleType("pyperclip")
            mod.paste = lambda: text
            return mod

        def _clip(text):
            sys.modules["pyperclip"] = _fake(text)
            res = tools.execute("read_clipboard", {})
            return str(res.get("result", res.get("message", "")))

        try:
            r1 = _clip("my password is hunter2")
            r2 = _clip("xK9mQ2vR7tY4wN8pL3sD6fG1hJ0k")
            r3 = _clip("buy milk")
        finally:
            if orig is None:
                sys.modules.pop("pyperclip", None)
            else:
                sys.modules["pyperclip"] = orig
        self.assertNotIn("hunter2", r1)
        self.assertIn("masked", r1.lower())
        self.assertNotIn("xK9mQ2vR7tY4wN8pL3sD6fG1hJ0k", r2)
        self.assertIn("buy milk", r3)


# ------------------------------------------------------- settings card
@unittest.skipUnless(_QT, "Qt-only: the Settings card needs PySide6")
class TestSettingsPassphraseCard(unittest.TestCase):
    """The Backup passphrase card's status line (Qt only): set or
    not set, never the passphrase itself."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(SettingsAdvCardsMixin,
                             "Settings mixin did not import under Qt")
        self.mixin = SettingsAdvCardsMixin()
        self.mixin.backup_passphrase_label = _QLabel()

    def _patch_has(self, value):
        import voice_assistant.core.backup as va_backup
        patch = mock.patch.object(va_backup, "has_backup_passphrase",
                                  lambda data_dir: value)
        patch.start()
        self.addCleanup(patch.stop)

    def test_card_status_set(self):
        self._patch_has(True)
        self.mixin._refresh_backup_passphrase_status()
        text = self.mixin.backup_passphrase_label.text()
        self.assertIn("is set", text)
        self.assertIn("encrypted", text.lower())

    def test_card_status_not_set(self):
        self._patch_has(False)
        self.mixin._refresh_backup_passphrase_status()
        text = self.mixin.backup_passphrase_label.text()
        self.assertIn("No backup passphrase", text)
        self.assertIn("refused", text.lower())


if __name__ == "__main__":
    unittest.main()
