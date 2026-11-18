"""Audit fix Gate C — MCP transport / policy / plugin entry points
(master audit P1-8 + P1-9 + P2-1 + P2-2 + P2-7).

P1-8: credentials over plain http:// to a non-loopback host are
refused at config validation; loopback http with credentials is
allowed ONLY with a recorded warning (cfg field, surfaced by the
doctor as WARN); https + credentials and http without credentials
are unchanged.
P1-9: "enabled" is parsed strictly — only a genuine JSON boolean
enables/disables; the string "false" (any case), "true", 1, 0 and
None are CONFIG ERRORS (documented choice: reject, never silently
disable — a user who wrote "true" expecting on must not get a
silently-off server).
P2-1: a plugin's manifest (name) and name-collision are validated
from the file's AST BEFORE any plugin code is imported; a refusal
means the file was NOT imported (proven with marker files).
P2-7: private_key_jwt is rejected at config validation as NOT
IMPLEMENTED — no accept-then-fail-later path remains.
P2-2: the power/network policy is enforced at the updater and
asset-download entry points: a positively detected metered
connection refuses check()/download_and_stage()/download_file()
at the entry point with a stated reason.

Stdlib unittest only. Run via tests/run_tests.py with
PYTHONPATH=. (the suites need it). No test here executes a real
tool, opens a real server socket, or downloads anything: policy
states are injected by monkeypatching core.power_policy.
current_policy, and network pass-through cases use reserved /
closed-port targets that fail fast without leaving the machine.
"""

import json
import tempfile
import unittest
from pathlib import Path

from core.voice import local_assets as local_assets_mod  # Phase 3 V2 INTENTIONAL: local_assets moved to core/voice/
from core import plugin_loader as plugin_mod
from core import power_policy as power_policy_mod
from core.health import updater as updater_mod  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from core.mcp_client import parse_enabled_flag as parse_enabled
from core.mcp_client import validate_server_config as validate_server
from core.plugin_loader import discover_plugins, static_plugin_meta


def _cc(url, **extra):
    """A minimal client_credentials remote server entry."""
    entry = {"url": url, "auth": {
        "type": "client_credentials",
        "issuer": "https://issuer.example",
        "client_id": "cid",
        "client_secret_env": "VYUK_TEST_SECRET",
    }}
    entry.update(extra)
    return entry


class TestCredentialedCleartext(unittest.TestCase):
    """P1-8: four cases from the brief, plus header variants."""

    def test_https_with_credentials_ok(self):
        cfg, err = validate_server("s", _cc("https://srv.example/mcp"))
        self.assertIsNone(err)
        self.assertEqual(cfg["transport"], "remote")
        self.assertNotIn("cleartext_loopback_warning", cfg)

    def test_https_with_auth_header_ok(self):
        cfg, err = validate_server(
            "s", {"url": "https://srv.example/mcp",
                  "headers": {"Authorization": "Bearer x"}})
        self.assertIsNone(err)

    def test_http_with_auth_remote_refused(self):
        cfg, err = validate_server("s", _cc("http://srv.example/mcp"))
        self.assertIsNone(cfg)
        self.assertIn("cleartext", err)
        self.assertIn("https", err)

    def test_http_with_auth_header_remote_refused(self):
        cfg, err = validate_server(
            "s", {"url": "http://srv.example/mcp",
                  "headers": {"Authorization": "Bearer x"}})
        self.assertIsNone(cfg)
        self.assertIn("cleartext", err)

    def test_http_with_api_key_header_remote_refused(self):
        cfg, err = validate_server(
            "s", {"url": "http://srv.example/mcp",
                  "headers": {"X-Api-Key": "k"}})
        self.assertIsNone(cfg)

    def test_http_with_credentials_loopback_allowed_with_warning(self):
        for url in ("http://127.0.0.1:8080/mcp",
                    "http://localhost:8080/mcp",
                    "http://[::1]:8080/mcp"):
            with self.subTest(url=url):
                cfg, err = validate_server("s", _cc(url))
                self.assertIsNone(err)
                self.assertIn("cleartext_loopback_warning", cfg)
                self.assertIn("unencrypted", cfg["cleartext_loopback_warning"])

    def test_http_without_credentials_unchanged(self):
        cfg, err = validate_server("s", {"url": "http://srv.example/mcp"})
        self.assertIsNone(err)
        self.assertNotIn("cleartext_loopback_warning", cfg)

    def test_http_with_benign_header_only_unchanged(self):
        cfg, err = validate_server(
            "s", {"url": "http://srv.example/mcp",
                  "headers": {"X-Client-Version": "1", "Accept": "application/json"}})
        self.assertIsNone(err)
        self.assertNotIn("cleartext_loopback_warning", cfg)


class TestStrictEnabled(unittest.TestCase):
    """P1-9: truthiness hole closed; non-bools are errors."""

    def test_string_false_does_not_enable(self):
        cfg, err = validate_server(
            "s", {"url": "https://x.example", "enabled": "false"})
        self.assertIsNone(cfg)
        self.assertIn("boolean", err)

    def test_string_variants_all_rejected(self):
        for value in ("False", "FALSE", "true", "True", "yes", "no"):
            with self.subTest(value=value):
                cfg, err = validate_server(
                    "s", {"url": "https://x.example", "enabled": value})
                self.assertIsNone(cfg)

    def test_numbers_and_null_rejected(self):
        for value in (1, 0, None, 1.0, ["true"]):
            with self.subTest(value=value):
                cfg, err = validate_server(
                    "s", {"url": "https://x.example", "enabled": value})
                self.assertIsNone(cfg)

    def test_real_booleans_work(self):
        cfg, _ = validate_server(
            "s", {"url": "https://x.example", "enabled": True})
        self.assertTrue(cfg["enabled"])
        cfg, _ = validate_server(
            "s", {"url": "https://x.example", "enabled": False})
        self.assertFalse(cfg["enabled"])

    def test_absent_enabled_defaults_true(self):
        cfg, err = validate_server("s", {"url": "https://x.example"})
        self.assertIsNone(err)
        self.assertTrue(cfg["enabled"])

    def test_parse_enabled_flag_direct(self):
        self.assertEqual(parse_enabled("s", {}), (True, None))
        self.assertEqual(parse_enabled("s", {"enabled": False}), (False, None))
        enabled, err = parse_enabled("s", {"enabled": "false"})
        self.assertIsNone(enabled)
        self.assertIsNotNone(err)


class TestPrivateKeyJwtRejected(unittest.TestCase):
    """P2-7: no accept-then-fail-later path remains."""

    def test_private_key_jwt_rejected_at_validation(self):
        cfg, err = validate_server(
            "s", {"url": "https://x.example/mcp", "auth": {
                "type": "private_key_jwt",
                "issuer": "https://issuer.example",
                "client_id": "cid"}})
        self.assertIsNone(cfg)
        self.assertIn("NOT IMPLEMENTED", err)

    def test_client_credentials_still_validates(self):
        cfg, err = validate_server("s", _cc("https://x.example/mcp"))
        self.assertIsNone(err)
        self.assertEqual(cfg["auth"]["type"], "client_credentials")

    def test_interactive_auth_still_rejected(self):
        cfg, err = validate_server(
            "s", {"url": "https://x.example/mcp", "auth": {
                "type": "authorization_code",
                "issuer": "https://issuer.example",
                "client_id": "cid"}})
        self.assertIsNone(cfg)


class TestStaticPluginMeta(unittest.TestCase):
    """P2-1, static layer: manifest without execution."""

    def test_literal_ok(self):
        meta, err = static_plugin_meta(
            'PLUGIN = {"name": "foo_tool", "description": "d"}\n')
        self.assertIsNone(err)
        self.assertEqual(meta["name"], "foo_tool")

    def test_annotated_assignment_ok(self):
        meta, err = static_plugin_meta(
            'PLUGIN: dict = {"name": "foo_tool"}\n')
        self.assertIsNone(err)
        self.assertEqual(meta["name"], "foo_tool")

    def test_dynamic_dict_refused(self):
        meta, err = static_plugin_meta('PLUGIN = dict(name="foo")\n')
        self.assertIsNone(meta)
        self.assertIn("static", err)

    def test_multiple_assignments_refused(self):
        meta, err = static_plugin_meta(
            'PLUGIN = {"name": "a"}\nPLUGIN = {"name": "b"}\n')
        self.assertIsNone(meta)

    def test_missing_plugin_refused(self):
        meta, err = static_plugin_meta("x = 1\n")
        self.assertIsNone(meta)

    def test_bad_name_refused(self):
        for src in ('PLUGIN = {"name": ""}\n',
                    'PLUGIN = {"name": "has space"}\n',
                    'PLUGIN = {"name": 42}\n',
                    'PLUGIN = {}\n'):
            with self.subTest(src=src):
                meta, err = static_plugin_meta(src)
                self.assertIsNone(meta)

    def test_syntax_error_refused(self):
        meta, err = static_plugin_meta("PLUGIN = {\n")
        self.assertIsNone(meta)


class TestPluginValidationBeforeImport(unittest.TestCase):
    """P2-1, behavioural layer: refusal means NOT imported.

    Each fixture plugin's top-level code writes a marker file on
    import. A refused plugin must leave NO marker; the control
    plugin (valid, unique, trust-approved) MUST leave one, so
    the marker mechanism itself is proven to detect imports.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.plugdir = self.dir / "plugins"
        self.plugdir.mkdir()
        self.datadir = self.dir / "data"
        self.datadir.mkdir()
        # An (unsigned, empty) manifest: parses, so the scan
        # proceeds; unlisted files answer to the user store.
        (self.plugdir / "plugins_hashes.json").write_text(
            "{}\n", encoding="utf-8")
        self._old_plugdir = plugin_mod.PLUGINS_DIR
        self._old_datadir = plugin_mod.DATA_DIR
        plugin_mod.PLUGINS_DIR = self.plugdir
        plugin_mod.DATA_DIR = self.datadir
        self.addCleanup(setattr, plugin_mod, "PLUGINS_DIR",
                        self._old_plugdir)
        self.addCleanup(setattr, plugin_mod, "DATA_DIR",
                        self._old_datadir)
        plugin_mod.set_trust_prompt_handler(lambda *a: True)
        self.addCleanup(plugin_mod.clear_trust_prompt_handler)

    def _write_plugin(self, filename, name=None, marker=None,
                      dynamic=False):
        marker_line = ""
        if marker is not None:
            marker_line = (
                f"from pathlib import Path as _P\n"
                f"_P({marker!r}).write_text('imported', encoding='utf-8')\n")
        if dynamic:
            body = 'PLUGIN = dict(name="dyn_tool")\n'
        else:
            body = (f'PLUGIN = {{"name": {name!r}, '
                    f'"description": "fixture"}}\n')
        content = ("# vyuk-plugin: capabilities=read\n" + marker_line
                   + body + "\ndef run(**kw):\n    return 'ok'\n")
        (self.plugdir / filename).write_text(content, encoding="utf-8")

    def test_collision_refused_without_import(self):
        marker = str(self.dir / "collision_marker.txt")
        self._write_plugin("evil.py", name="victim_tool", marker=marker)
        found = discover_plugins(existing_names={"victim_tool"})
        self.assertEqual(found, [])
        self.assertFalse(Path(marker).exists(),
                         "colliding plugin was IMPORTED (marker written)")

    def test_dynamic_manifest_refused_without_import(self):
        marker = str(self.dir / "dynamic_marker.txt")
        self._write_plugin("dyn.py", marker=marker, dynamic=True)
        found = discover_plugins(existing_names=set())
        self.assertEqual(found, [])
        self.assertFalse(Path(marker).exists())

    def test_valid_plugin_still_loads_and_imports(self):
        marker = str(self.dir / "control_marker.txt")
        self._write_plugin("good.py", name="good_tool", marker=marker)
        found = discover_plugins(existing_names=set())
        self.assertEqual([p["name"] for p in found], ["good_tool"])
        self.assertTrue(Path(marker).exists(),
                        "control plugin was NOT imported — the marker "
                        "mechanism itself is broken")


class TestPolicyAtDownloadEntryPoints(unittest.TestCase):
    """P2-2: metered policy refuses at the entry point."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = power_policy_mod.current_policy
        self.addCleanup(setattr, power_policy_mod, "current_policy",
                        self._orig)

    def _set_metered(self, metered):
        power_policy_mod.current_policy = lambda: {
            "power_source": "ac", "battery_pct": None, "charging": None,
            "metered": metered, "throttle_watchers": False,
            "throttle_local_inference": False, "watcher_multiplier": 1,
            "local_inference_factor": 1.0,
            "pause_downloads": metered is True,
            "pause_updates": metered is True,
            "reason": ("metered connection — downloads paused"
                       if metered is True else
                       "unmetered connection — downloads allowed"),
        }

    def test_check_refused_when_metered(self):
        self._set_metered(True)
        result = updater_mod.check("https://example.invalid/feed.json")
        self.assertEqual(result["state"], "error")
        self.assertIn("paused", result["message"])

    def test_check_passes_through_when_unmetered(self):
        self._set_metered(False)
        result = updater_mod.check("https://example.invalid/feed.json")
        # Passes the gate, then fails on the (unreachable) feed —
        # but NOT on policy.
        self.assertEqual(result["state"], "error")
        self.assertNotIn("paused", result["message"])

    def test_download_and_stage_refused_when_metered(self):
        self._set_metered(True)
        # No sha256 at all: if the gate were after validation,
        # the error would be about the sha. The paused error
        # proves the gate sits at the entry point.
        result = updater_mod.download_and_stage({
            "version": "99.0.0", "url": "https://example.invalid/u.zip"})
        self.assertEqual(result["state"], "error")
        self.assertIn("paused", result["message"])

    def test_download_and_stage_passes_through_when_unmetered(self):
        self._set_metered(False)
        result = updater_mod.download_and_stage({
            "version": "99.0.0", "url": "https://example.invalid/u.zip"})
        self.assertEqual(result["state"], "error")
        self.assertNotIn("paused", result["message"])
        self.assertIn("sha256", result["message"])

    def test_download_file_refused_when_metered(self):
        self._set_metered(True)
        dest = Path(self._tmp.name) / "asset.bin"
        err = local_assets_mod.download_file(
            "http://127.0.0.1:9/asset.bin", dest,
            lambda line: None, label="fixture asset")
        self.assertIsNotNone(err)
        self.assertIn("paused", err)
        self.assertFalse(dest.exists())

    def test_download_file_passes_through_when_unmetered(self):
        self._set_metered(False)
        dest = Path(self._tmp.name) / "asset.bin"
        err = local_assets_mod.download_file(
            "http://127.0.0.1:9/asset.bin", dest,
            lambda line: None, label="fixture asset")
        self.assertIsNotNone(err)
        self.assertNotIn("paused", err)


class TestLoopbackWarningSurfacedByDoctor(unittest.TestCase):
    """P1-8, display half: the recorded warning reaches the user
    through the doctor's MCP listing as WARN."""

    def test_doctor_warns_on_loopback_cleartext(self):
        import importlib
        import doctor as doctor_mod
        doctor_mod._register_package()
        importlib.import_module("voice_assistant.core.mcp_client")
        mcp_mod = __import__("sys").modules[
            "voice_assistant.core.mcp_client"]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "mcp_servers.json").write_text(json.dumps({
            "mcpServers": {
                "localcreds": {
                    "url": "http://127.0.0.1:8080/mcp",
                    "auth": {
                        "type": "client_credentials",
                        "issuer": "https://issuer.example",
                        "client_id": "cid",
                        "client_secret_env": "VYUK_TEST_SECRET",
                    },
                },
            },
        }), encoding="utf-8")
        old_root = mcp_mod.PROJECT_ROOT
        mcp_mod.PROJECT_ROOT = root
        self.addCleanup(setattr, mcp_mod, "PROJECT_ROOT", old_root)
        rows = []
        old_report = doctor_mod.report
        doctor_mod.report = lambda *a, **k: rows.append(a)
        self.addCleanup(setattr, doctor_mod, "report", old_report)
        doctor_mod.check_mcp_servers()
        warns = [r for r in rows if r and r[0] == "WARN"
                 and "localcreds" in (r[1] if len(r) > 1 else "")]
        self.assertTrue(warns, f"no WARN row for localcreds in {rows}")
        self.assertIn("unencrypted", " ".join(str(r) for r in warns))


if __name__ == "__main__":
    unittest.main()
