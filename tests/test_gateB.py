"""Gate B (updater correctness) regression suite — master audit
P0-3 (protected paths), P1-4 (owned-file reconciliation), P0-4
(crash-atomic update), wiring audit WIR-07 (manifest in installer
data) and WIR-09 (version agreement).

All apply tests run against throwaway package roots via the
NOVA_APP_ROOT seam, so the real working tree is never touched.
The staged manifests in these tests carry REAL Ed25519 signatures
made by a test-only RFC 8032 signer built on the updater's own
primitives, with core.integrity's pinned key monkeypatched to the
test keypair (the seam integrity.py itself documents). The one
place a seam is stubbed is nowhere: even the success-path apply
runs the genuine staged-manifest verification.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.health import integrity  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from core.health import updater  # Phase 3 H1 INTENTIONAL: module moved to core/health/


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _test_sign(seed: bytes, message: bytes):
    """RFC 8032 signing with the updater's own primitives (test-only;
    no product code signs). Returns (public_key, signature)."""
    h = hashlib.sha512(seed).digest()
    a_bytes = bytearray(h[:32])
    a_bytes[0] &= 248
    a_bytes[31] &= 127
    a_bytes[31] |= 64
    a = int.from_bytes(a_bytes, "little")
    pub = updater._ed_encode(updater._ed_mul(updater._ED_BASE, a))
    r = int.from_bytes(
        hashlib.sha512(h[32:] + message).digest(), "little") % updater._ED_L
    enc_r = updater._ed_encode(updater._ed_mul(updater._ED_BASE, r))
    k = int.from_bytes(
        hashlib.sha512(enc_r + pub + message).digest(),
        "little") % updater._ED_L
    s = (r + k * a) % updater._ED_L
    return pub, enc_r + s.to_bytes(32, "little")


TEST_SEED = bytes(range(32))


class GateBCase(unittest.TestCase):
    """Fake-root apply tests. The updater module's VERSION stays
    the real tree's 6.3.0, so staged versions use 6.4.0."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gateb_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp) / "pkg"
        (self.root / "core").mkdir(parents=True)
        old_env = os.environ.get("NOVA_APP_ROOT")
        os.environ["NOVA_APP_ROOT"] = str(self.root)
        self.addCleanup(self._restore_env, old_env)
        self._write_live()
        self._write_staged()

    def _restore_env(self, old):
        if old is None:
            os.environ.pop("NOVA_APP_ROOT", None)
        else:
            os.environ["NOVA_APP_ROOT"] = old

    # -- fixture ----------------------------------------------------

    def _write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_live(self):
        self._write("core/version.py", 'VERSION = "6.3.0"\n')
        self._write("core/other.py", "OLD-OTHER\n")
        self._write("core/stale.py", "STALE\n")
        # Prev manifest records the ORIGINAL modified.py hash;
        # the live file is then user-edited.
        self.prev_files = {
            "core/version.py": _sha('VERSION = "6.3.0"\n'),
            "core/other.py": _sha("OLD-OTHER\n"),
            "core/stale.py": _sha("STALE\n"),
            "core/modified.py": _sha("MODIFIED-ORIGINAL\n"),
        }
        self._write("install_manifest.json",
                    json.dumps({"format": 1, "files": self.prev_files}))
        self._write("core/modified.py", "MODIFIED-BY-USER\n")
        # User-created: live file the previous manifest never lists.
        self._write("core/userfile.py", "USER-CREATED\n")
        self._write(".env", "USER-SECRET=live\n")

    def _write_staged(self):
        staged = self.root / "data" / "update_staging" / "6.4.0"
        staged.mkdir(parents=True, exist_ok=True)
        self.staged = staged
        staged_files = {
            "core/version.py": 'VERSION = "6.4.0"\n',
            "core/other.py": "NEW-OTHER\n",
            "core/new.py": "BRAND-NEW\n",
            "core/modified.py": "STAGED-MODIFIED\n",
            "core/userfile.py": "STAGED-USERFILE\n",
        }
        for rel, text in staged_files.items():
            path = staged / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        new_map = {rel: _sha(text)
                   for rel, text in staged_files.items()}
        manifest_bytes = json.dumps(
            {"format": 1, "files": new_map}, indent=2).encode("utf-8")
        (staged / "install_manifest.json").write_bytes(manifest_bytes)
        pub, sig = _test_sign(TEST_SEED, manifest_bytes)
        (staged / "install_manifest.json.sig").write_text(
            base64.b64encode(sig).decode("ascii"), encoding="utf-8")
        self.test_pub = pub
        inventory = {}
        for path in staged.rglob("*"):
            if path.is_file():
                rel = path.relative_to(staged).as_posix()
                inventory[rel] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        marker = {"version": "6.4.0", "files": inventory,
                  "staged_at": "2026-10-30T00:00:00+00:00"}
        (self.root / "data" / "update_staging" / "staged.json").write_text(
            json.dumps(marker, indent=2), encoding="utf-8")
        self.inventory = inventory

    def _patch_test_key(self):
        patcher = mock.patch.object(
            integrity, "PINNED_UPDATE_PUBLIC_KEY", self.test_pub)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _read(self, rel: str):
        path = self.root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # -- protected paths --------------------------------------------

    def test_protected_rel_rules(self):
        self.assertTrue(updater._is_protected_rel(".env"))
        self.assertTrue(updater._is_protected_rel("sub/.env"))
        self.assertTrue(updater._is_protected_rel(".env.production"))
        self.assertTrue(updater._is_protected_rel("google_token.json"))
        self.assertTrue(updater._is_protected_rel(".vyuk_secret_key"))
        # The shipped template is explicitly NOT protected.
        self.assertFalse(updater._is_protected_rel(".env.example"))
        self.assertFalse(updater._is_protected_rel("core/version.py"))
        self.assertFalse(updater._is_protected_rel("core/other.py"))
        self.assertEqual(
            updater._protected_in_inventory(
                ["core/a.py", ".env", "data/x.py", ".env.example"]),
            [".env", "data/x.py"])

    def test_extraction_rejects_protected_zip(self):
        zip_path = Path(self.tmp) / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pkg/core/a.py", "A\n")
            zf.writestr("pkg/.env", "STOLEN=1\n")
        result = updater._extract_staged(zip_path, "6.4.0")
        self.assertIn("error", result)
        self.assertIn("protected", result["error"])
        staged_env = self.root / "data" / "update_staging" / "6.4.0" / ".env"
        self.assertFalse(staged_env.exists())

    def test_extraction_allows_env_example_template(self):
        zip_path = Path(self.tmp) / "ok.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("pkg/core/a.py", "A\n")
            zf.writestr("pkg/.env.example", "TEMPLATE=1\n")
        result = updater._extract_staged(zip_path, "6.4.0")
        self.assertNotIn("error", result)
        self.assertIn("core/a.py", result["files"])
        self.assertIn(".env.example", result["files"])

    # -- full apply ---------------------------------------------------

    def test_apply_success_preserves_reconciles_and_orders(self):
        self._patch_test_key()
        copy_order = []
        real_copy2 = shutil.copy2

        def recording_copy2(src, dst, *args, **kwargs):
            copy_order.append(Path(dst))
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch.object(shutil, "copy2", recording_copy2):
            result = updater.apply_staged()
        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["from"], "6.3.0")
        self.assertEqual(result["to"], "6.4.0")
        self.assertEqual(self._read("core/other.py"), "NEW-OTHER\n")
        self.assertEqual(self._read("core/new.py"), "BRAND-NEW\n")
        self.assertEqual(self._read("core/version.py"),
                         'VERSION = "6.4.0"\n')
        # User-modified + user-created files are preserved, reported.
        self.assertEqual(self._read("core/modified.py"),
                         "MODIFIED-BY-USER\n")
        self.assertEqual(self._read("core/userfile.py"), "USER-CREATED\n")
        self.assertEqual(sorted(result["preserved_modified"]),
                         ["core/modified.py", "core/userfile.py"])
        # Stale owned file removed and reported.
        self.assertIsNone(self._read("core/stale.py"))
        self.assertEqual(result["removed"], ["core/stale.py"])
        # Credentials untouched.
        self.assertEqual(self._read(".env"), "USER-SECRET=live\n")
        # Journal + staging cleaned up; backup kept as rollback target.
        self.assertFalse((self.root / "data" / "update_apply.json").exists())
        self.assertFalse((self.root / "data" / "update_staging").exists())
        backup_other = self.root / "data" / "update_backup" / "6.3.0" \
            / "core" / "other.py"
        self.assertEqual(backup_other.read_text(encoding="utf-8"),
                         "OLD-OTHER\n")
        # Version file copied LAST (Gate B, P0-4).
        self.assertTrue(copy_order)
        self.assertEqual(copy_order[-1], self.root / "core" / "version.py")

    def test_apply_rejects_protected_inventory(self):
        # Tampered marker: inventory names .env; staged tree matches
        # it, so staging verification passes and the protected gate
        # is what must stop the apply.
        staged_env = self.staged / ".env"
        staged_env.write_text("STAGED-SECRET=evil\n", encoding="utf-8")
        inventory = dict(self.inventory)
        inventory[".env"] = hashlib.sha256(
            staged_env.read_bytes()).hexdigest()
        marker_path = self.root / "data" / "update_staging" / "staged.json"
        marker_path.write_text(json.dumps(
            {"version": "6.4.0", "files": inventory}), encoding="utf-8")
        result = updater.apply_staged()
        self.assertEqual(result["state"], "rolled_back")
        self.assertIn("protected", result["reason"])
        self.assertEqual(self._read(".env"), "USER-SECRET=live\n")
        self.assertEqual(self._read("core/other.py"), "OLD-OTHER\n")
        self.assertFalse((self.root / "data" / "update_staging").exists())

    def test_apply_aborts_on_bad_staged_manifest(self):
        # Valid staging inventory, but the staged manifest's
        # signature is garbage under the REAL pinned key.
        (self.staged / "install_manifest.json.sig").write_text(
            "not-valid-base64!!!", encoding="utf-8")
        result = updater.apply_staged()
        self.assertEqual(result["state"], "rolled_back")
        self.assertIn("manifest", result["reason"])
        self.assertEqual(self._read("core/other.py"), "OLD-OTHER\n")
        self.assertEqual(self._read("core/version.py"),
                         'VERSION = "6.3.0"\n')
        self.assertFalse((self.root / "data" / "update_staging").exists())

    def test_midswap_failure_rolls_back_and_preserves_env(self):
        self._patch_test_key()
        real_copy2 = shutil.copy2
        new_py = self.root / "core" / "new.py"

        def failing_copy2(src, dst, *args, **kwargs):
            if Path(dst) == new_py:
                raise OSError("simulated disk failure")
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch.object(shutil, "copy2", failing_copy2):
            result = updater.apply_staged()
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(self._read("core/other.py"), "OLD-OTHER\n")
        self.assertEqual(self._read("core/version.py"),
                         'VERSION = "6.3.0"\n')
        self.assertIsNone(self._read("core/new.py"))
        self.assertEqual(self._read(".env"), "USER-SECRET=live\n")
        self.assertFalse((self.root / "data" / "update_apply.json").exists())
        self.assertFalse((self.root / "data" / "update_staging").exists())

    # -- interrupted recovery -----------------------------------------

    def _write_journal_fixture(self, phase, live_files, backup_files):
        for rel, text in live_files.items():
            self._write(rel, text)
        backup_dir = self.root / "data" / "update_backup" / "6.3.0"
        for rel, text in backup_files.items():
            path = backup_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        # A real pre-update backup always contains the old manifest
        # (manifest files are never a backup exclusion).
        (backup_dir / "install_manifest.json").write_text(
            json.dumps({"format": 1, "files": self.prev_files}),
            encoding="utf-8")
        journal = {"version": "6.4.0", "from_version": "6.3.0",
                   "backup_dir": "update_backup/6.3.0",
                   "inventory": sorted(self.inventory), "phase": phase}
        (self.root / "data" / "update_apply.json").write_text(
            json.dumps(journal, indent=2), encoding="utf-8")

    def test_interrupted_swapping_recovers_old_tree(self):
        # Marker says new (version file already new) but the files
        # do NOT match the staged inventory: the brief's case.
        old_tree = {
            "core/version.py": 'VERSION = "6.3.0"\n',
            "core/other.py": "OLD-OTHER\n",
            "core/stale.py": "STALE\n",
            "core/modified.py": "MODIFIED-BY-USER\n",
            "core/userfile.py": "USER-CREATED\n",
        }
        mixed_tree = dict(old_tree)
        mixed_tree["core/version.py"] = 'VERSION = "6.4.0"\n'
        self._write_journal_fixture("swapping", mixed_tree, old_tree)
        result = updater.apply_staged()
        self.assertEqual(result["state"], "rolled_back")
        for rel, text in old_tree.items():
            self.assertEqual(self._read(rel), text)
        self.assertIsNone(self._read("core/new.py"))
        # The old manifest is restored too, not deleted.
        self.assertIn("core/stale.py",
                      json.loads(self._read("install_manifest.json"))["files"])
        self.assertEqual(self._read(".env"), "USER-SECRET=live\n")
        self.assertFalse((self.root / "data" / "update_apply.json").exists())
        self.assertFalse((self.root / "data" / "update_staging").exists())

    def test_completed_update_is_not_rolled_back(self):
        new_tree = {
            "core/version.py": 'VERSION = "6.4.0"\n',
            "core/other.py": "NEW-OTHER\n",
            "core/new.py": "BRAND-NEW\n",
            "core/modified.py": "MODIFIED-BY-USER\n",
            "core/userfile.py": "USER-CREATED\n",
        }
        old_tree = {
            "core/version.py": 'VERSION = "6.3.0"\n',
            "core/other.py": "OLD-OTHER\n",
        }
        self._write_journal_fixture("done", new_tree, old_tree)
        result = updater.apply_staged()
        self.assertEqual(result["state"], "applied")
        for rel, text in new_tree.items():
            self.assertEqual(self._read(rel), text)
        self.assertFalse((self.root / "data" / "update_apply.json").exists())
        self.assertFalse((self.root / "data" / "update_staging").exists())

    def test_missing_backup_is_error_and_touches_nothing(self):
        mixed_tree = {
            "core/version.py": 'VERSION = "6.4.0"\n',
            "core/other.py": "OLD-OTHER\n",
        }
        for rel, text in mixed_tree.items():
            self._write(rel, text)
        journal = {"version": "6.4.0", "from_version": "6.3.0",
                   "backup_dir": "update_backup/6.3.0",
                   "inventory": sorted(self.inventory),
                   "phase": "swapping"}
        (self.root / "data" / "update_apply.json").write_text(
            json.dumps(journal, indent=2), encoding="utf-8")
        result = updater.apply_staged()
        self.assertEqual(result["state"], "error")
        self.assertEqual(self._read("core/version.py"),
                         'VERSION = "6.4.0"\n')
        self.assertEqual(self._read("core/other.py"), "OLD-OTHER\n")
        # Journal stays: the failure must keep reporting loudly.
        self.assertTrue((self.root / "data" / "update_apply.json").exists())

    # -- staged-manifest verifier units --------------------------------

    def test_verify_staged_manifest_units(self):
        tiny = Path(self.tmp) / "tiny"
        (tiny / "core").mkdir(parents=True)
        (tiny / "core" / "a.py").write_text("A\n", encoding="utf-8")
        # Missing manifest -> error.
        self.assertIsNotNone(updater._verify_staged_manifest(tiny))
        # Garbage signature -> error (real pinned key).
        manifest_bytes = json.dumps(
            {"format": 1, "files": {"core/a.py": _sha("A\n")}},
            indent=2).encode("utf-8")
        (tiny / "install_manifest.json").write_bytes(manifest_bytes)
        (tiny / "install_manifest.json.sig").write_text(
            "garbage", encoding="utf-8")
        self.assertIsNotNone(updater._verify_staged_manifest(tiny))
        # Valid test-signed manifest -> None.
        pub, sig = _test_sign(TEST_SEED, manifest_bytes)
        (tiny / "install_manifest.json.sig").write_text(
            base64.b64encode(sig).decode("ascii"), encoding="utf-8")
        with mock.patch.object(
                integrity, "PINNED_UPDATE_PUBLIC_KEY", pub):
            self.assertIsNone(updater._verify_staged_manifest(tiny))


class InstallerDataCase(unittest.TestCase):
    """Source-level checks against the REAL tree (read-only)."""

    @property
    def tree(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_spec_and_iss_reference_manifest(self):
        spec = (self.tree / "installer" / "vyuk.spec").read_text(
            encoding="utf-8")
        self.assertIn("install_manifest.json", spec)
        self.assertIn("install_manifest.json.sig", spec)
        iss = (self.tree / "installer" / "vyuk.iss").read_text(
            encoding="utf-8")
        self.assertIn("install_manifest.json", iss)
        self.assertIn("install_manifest.json.sig", iss)

    def test_version_agreement(self):
        self.assertEqual(updater.VERSION, "6.3.0")
        iss = (self.tree / "installer" / "vyuk.iss").read_text(
            encoding="utf-8")
        app_version = re.search(r"^AppVersion=(.+)$", iss, re.M).group(1)
        self.assertEqual(app_version, "6.3.0")
        app_ver_name = re.search(r"^AppVerName=VYUK (.+)$", iss, re.M).group(1)
        self.assertEqual(app_ver_name, "6.3.0")
        out_name = re.search(r"^OutputBaseFilename=VYUK-Setup-(.+)$",
                             iss, re.M).group(1)
        self.assertEqual(out_name, "6.3.0")
        exe_manifest = (self.tree / "installer" / "vyuk.exe.manifest").read_text(
            encoding="utf-8")
        exe_version = re.search(
            r'<assemblyIdentity version="([^"]+)"', exe_manifest).group(1)
        self.assertEqual(exe_version, "6.3.0.0")
        # The checklist is a Wave 10 record, bannered as historical.
        checklist = (self.tree / "installer" / "WINDOWS_BUILD_CHECKLIST.md").read_text(
            encoding="utf-8")
        self.assertIn("HISTORICAL RECORD", checklist)
        self.assertIn("5.99.0", checklist)


if __name__ == "__main__":
    unittest.main()
