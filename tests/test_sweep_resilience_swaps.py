"""Sweep Build 6 — resilience swaps, only where the package beats the
in-house pattern.

What is tested: the pinned licence file for each of the five packages
(read from the actual sdist AND wheel, byte-identical), the wired /
not-wired verdicts, real routing through each wired package when it is
installed (skipped honestly when it is not), the in-house fallback
working when the package is absent, and that the two NOT-wired
packages are imported by no wired path.

NOT claimed here: that a swap improves a real workload — that needs
measurement on the owner's machine after the BB101 quiz — nor any
field behaviour, wake/capture/voiceprint change, or a runtime
dependency: requirements.txt gains nothing in this build.
"""

import asyncio
import hashlib
import importlib.util
import json
import pathlib
import socket
import tempfile
import unittest

from core.resilience import resilience_swaps as swaps  # INTENTIONAL (Phase 3 R1): moved to core/resilience/
from core.resilience import resilience_wiring as wiring  # INTENTIONAL (Phase 3 R1): moved to core/resilience/

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# name -> (licence file, size bytes, SHA-256, text that must appear)
_LICENCES = {
    "diskcache": ("LICENSE-diskcache.txt", 559,
                  "583546baa3fd93607d845126570677a401f508e228b5044fefbc3949af179672",
                  "Licensed under the Apache License, Version 2.0"),
    "whenever": ("LICENSE-whenever.txt", 1088,
                 "37fe1c54cc76545cc0741edf028c7edb6708881fc5efe26180cecf98213282b8",
                 "The MIT License (MIT)"),
    "aiohappyeyeballs": ("LICENSE-aiohappyeyeballs.txt", 13936,
                         "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf",
                         "Python Software Foundation"),
    "exactly_once": ("LICENSE-exactly-once.txt", 1074,
                     "c116e54abaae86fb06c40771fc6c5e07a46d4bb653bd7026868e0363e8cc2b39",
                     "MIT License"),
    "agent_saga": ("LICENSE-agent-saga.txt", 11358,
                   "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
                   "Apache License"),
}


class TestLicences(unittest.TestCase):
    def test_each_licence_file_present_size_sha_text(self):
        for name, (filename, size, sha, text) in _LICENCES.items():
            path = _ROOT / "gui" / "assets" / "resilience" / filename
            self.assertTrue(path.is_file(), f"{name} licence file missing")
            self.assertEqual(path.stat().st_size, size, name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             sha, name)
            self.assertIn(text, path.read_text(encoding="utf-8"), name)

    def test_agent_saga_notice_filed(self):
        notice = _ROOT / "gui" / "assets" / "resilience" / "NOTICE-agent-saga.txt"
        self.assertTrue(notice.is_file())
        self.assertEqual(
            hashlib.sha256(notice.read_bytes()).hexdigest(),
            "c310c25fd3afa66767a3c61eed60357ca0f64a795b2690ad77dffeb14c869361")
        self.assertIn("SAGAOPS", notice.read_text(encoding="utf-8"))

    def test_read_document_records_all_five(self):
        read = _ROOT / "gui" / "assets" / "RESILIENCE_LICENCE_READ.md"
        text = read.read_text(encoding="utf-8")
        for token in ("5.6.3", "0.11.0", "2.7.1", "0.2.1", "2.3.11",
                      "NOT WIRED", "WIRED"):
            self.assertIn(token, text, token)

    def test_no_runtime_requirement_added(self):
        # Only requirement lines count: the DOCUMENTED-OPTIONAL
        # section names diskcache/whenever/aiohappyeyeballs in
        # comments (they are dynamic optional loads, not
        # requirements), and a whole-file substring check would
        # trip on that documentation. A real requirement line
        # (diskcache>=... on its own line) still fails here.
        req_lines = []
        for line in (_ROOT / "requirements.txt").read_text(
                encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                req_lines.append(line)
        req = "\n".join(req_lines)
        for pkg in ("diskcache", "whenever", "aiohappyeyeballs",
                    "exactly-once", "exactly_once", "agent-saga", "agent_saga"):
            self.assertNotIn(pkg, req, pkg)


class TestVerdicts(unittest.TestCase):
    def test_wired_flags(self):
        status = swaps.swap_status()
        self.assertTrue(status["diskcache"]["wired"])
        self.assertTrue(status["whenever"]["wired"])
        self.assertTrue(status["aiohappyeyeballs"]["wired"])
        self.assertFalse(status["exactly_once"]["wired"])
        self.assertFalse(status["agent_saga"]["wired"])
        for entry in status.values():
            self.assertFalse(entry["runtime_required"])

    def test_wiring_owed_reads_match_verdicts(self):
        clearance = wiring.owed_read_clearance()
        self.assertEqual(clearance["DiskCache"]["ship_state"], "wired_optional")
        self.assertEqual(clearance["whenever"]["ship_state"], "wired_optional")
        self.assertEqual(clearance["aiohappyeyeballs"]["ship_state"],
                         "wired_optional")
        self.assertEqual(clearance["exactly-once"]["ship_state"], "not_wired")
        self.assertEqual(clearance["agent-saga"]["ship_state"], "not_wired")

    def test_not_wired_packages_never_imported(self):
        import sys
        source = (_ROOT / "core" / "resilience" / "resilience_swaps.py").read_text()  # INTENTIONAL (Phase 3 R1): resilience_swaps.py moved to core/resilience/
        self.assertNotIn("import exactly_once", source)
        self.assertNotIn("import agent_saga", source)
        self.assertNotIn("exactly_once", sys.modules)
        self.assertNotIn("agent_saga", sys.modules)


class _FakeDiskCache:
    """DiskCache-shaped fake: stores the JSON blob, honours expire."""

    def __init__(self):
        self.calls = []
        self._store = {}

    def set(self, key, value, expire=None):
        import time as _time
        self.calls.append((key, expire))
        expiry = None if expire is None else _time.time() + float(expire)
        self._store[key] = (value, expiry)

    def get(self, key):
        import time as _time
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and _time.time() >= expiry:
            del self._store[key]
            return None
        return value

    def delete(self, key):
        self._store.pop(key, None)


class TestTTLCache(unittest.TestCase):
    def test_injected_fake_routes_through_cache_object(self):
        fake = _FakeDiskCache()
        cache = swaps.TTLCache(tempfile.mkdtemp(), cache_obj=fake)
        self.assertEqual(cache.backend, "injected")
        cache.set("k", {"a": 1}, expire_s=60)
        self.assertEqual(cache.get("k"), {"a": 1})
        self.assertEqual(fake.calls, [("k", 60)])
        cache.delete("k")
        self.assertIsNone(cache.get("k"))

    def test_memory_fallback_roundtrip_expiry_delete(self):
        original = swaps._load_optional
        swaps._load_optional = lambda name: None
        try:
            cache = swaps.TTLCache(tempfile.mkdtemp())
            self.assertEqual(cache.backend, "memory")
            cache.set("keep", [1, 2])
            self.assertEqual(cache.get("keep"), [1, 2])
            cache.set("gone", "x", expire_s=-1)
            self.assertIsNone(cache.get("gone"))
            cache.set("del", "y")
            cache.delete("del")
            self.assertIsNone(cache.get("del"))
        finally:
            swaps._load_optional = original

    @unittest.skipUnless(importlib.util.find_spec("diskcache") is not None,
                         "diskcache not installed in this environment")
    def test_real_diskcache_routing(self):
        cache = swaps.TTLCache(tempfile.mkdtemp())
        self.assertEqual(cache.backend, "diskcache")
        try:
            cache.set("real", {"n": 7}, expire_s=60)
            self.assertEqual(cache.get("real"), {"n": 7})
            cache.set("expired", "x", expire_s=-1)
            self.assertIsNone(cache.get("expired"))
        finally:
            cache.close()


class TestDST(unittest.TestCase):
    def _force_stdlib(self):
        original = swaps._load_optional
        swaps._load_optional = lambda name: None
        return original

    def test_stdlib_repeated_hour_earlier_later_raise(self):
        original = self._force_stdlib()
        try:
            earlier = swaps.resolve_local_time(
                2023, 11, 5, 1, 30, tz="America/New_York",
                disambiguation="earlier")
            later = swaps.resolve_local_time(
                2023, 11, 5, 1, 30, tz="America/New_York",
                disambiguation="later")
            refused = swaps.resolve_local_time(
                2023, 11, 5, 1, 30, tz="America/New_York",
                disambiguation="raise")
            self.assertTrue(earlier["ok"] and earlier["repeated"])
            self.assertTrue(later["ok"] and later["repeated"])
            self.assertNotEqual(earlier["utc_iso"], later["utc_iso"])
            self.assertEqual(earlier["backend"], "stdlib")
            self.assertFalse(refused["ok"])
            self.assertTrue(refused["repeated"])
        finally:
            swaps._load_optional = original

    def test_stdlib_gap_hour_refused_never_guessed(self):
        original = self._force_stdlib()
        try:
            result = swaps.resolve_local_time(
                2023, 3, 12, 2, 30, tz="America/New_York",
                disambiguation="earlier")
            self.assertFalse(result["ok"])
            self.assertIn("gap", result["reason"])
        finally:
            swaps._load_optional = original

    @unittest.skipUnless(importlib.util.find_spec("whenever") is not None,
                         "whenever not installed in this environment")
    def test_real_whenever_repeated_and_gap(self):
        earlier = swaps.resolve_local_time(
            2023, 10, 29, 2, 15, tz="Europe/Amsterdam",
            disambiguation="earlier")
        later = swaps.resolve_local_time(
            2023, 10, 29, 2, 15, tz="Europe/Amsterdam",
            disambiguation="later")
        self.assertTrue(earlier["ok"] and earlier["repeated"])
        self.assertEqual(earlier["backend"], "whenever")
        self.assertNotEqual(earlier["utc_iso"], later["utc_iso"])


class TestHappyEyeballs(unittest.TestCase):
    def _server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        return srv, srv.getsockname()[1]

    def test_injected_connector_failure_is_a_verdict(self):
        async def failing(host, port, delay):
            raise OSError("refused")

        result = asyncio.run(swaps.happy_eyeballs_connect(
            "127.0.0.1", 1, connector=failing))
        self.assertFalse(result["ok"])
        self.assertEqual(result["backend"], "injected")

    def test_stdlib_fallback_connects_to_local_server(self):
        original = swaps._load_optional
        swaps._load_optional = lambda name: None
        srv, port = self._server()
        try:
            result = asyncio.run(swaps.happy_eyeballs_connect(
                "127.0.0.1", port))
            self.assertTrue(result["ok"])
            self.assertEqual(result["backend"], "stdlib")
            self.assertIsNone(result["sock"])
        finally:
            swaps._load_optional = original
            srv.close()

    @unittest.skipUnless(
        importlib.util.find_spec("aiohappyeyeballs") is not None,
        "aiohappyeyeballs not installed in this environment")
    def test_real_aiohappyeyeballs_returns_raw_socket(self):
        srv, port = self._server()
        try:
            result = asyncio.run(swaps.happy_eyeballs_connect(
                "127.0.0.1", port))
            self.assertTrue(result["ok"])
            self.assertEqual(result["backend"], "aiohappyeyeballs")
            self.assertIsNotNone(result["sock"])
            result["sock"].close()
        finally:
            srv.close()


class TestWiringMethods(unittest.TestCase):
    def test_wiring_cache_and_run_at(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        self.assertTrue(w.cache_set("k", {"v": 2})["stored"])
        got = w.cache_get("k")
        self.assertTrue(got["found"])
        self.assertEqual(got["value"], {"v": 2})
        refused = w.resolve_run_at(2023, 11, 5, 1, 30,
                                   tz="America/New_York",
                                   disambiguation="raise")
        # Both backends refuse a repeated hour under "raise".
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["repeated"])

    def test_wiring_swap_status(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        status = w.swap_status()
        self.assertTrue(status["diskcache"]["wired"])
        self.assertFalse(status["agent_saga"]["wired"])


if __name__ == "__main__":
    unittest.main()
