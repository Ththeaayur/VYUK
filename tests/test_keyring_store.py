"""B-D10 (2026-11-09) keyring secrets store.

Runs against a FAKE backend (in-memory (service, username) ->
secret) on BOTH interpreters — no real keyring, no real
Windows Credential Manager, no real secrets — so the wrapper's
rules are proved everywhere: set reads back, rotation leaves
no old value, delete verifies removal, cross-scope isolation,
list returns usernames only, and the fake secret appears in NO
reason string, NO error message, and NO file the wrapper
touches. The Windows enumeration path (win32cred.CredEnumerate)
is proved with a FAKE win32cred module injected into
sys.modules, including the compound-name form keyring's
Windows backend uses and a neighbouring service that must
never leak into the list.

The REAL engine is proved where it exists: this Linux lab
machine has NO usable backend on either interpreter — system
python3 has no keyring at all, and .venv-qt has keyring 25.7.0
installed but get_keyring() returns the fail backend (no D-Bus
session) — so the real-engine tests assert the honest
engine_unavailable degradation instead of skipping silently.
A suite that silently skips the secrets checks on an
interpreter without a usable backend is a lie, not a pass.
(If a usable backend ever appears, those tests skip with the
reason named, per the B-D9 pattern.)

The real-Windows leg (a real secret in Windows Credential
Manager, surviving a restart, deleted cleanly, listed by
CredEnumerate) is a field leg, owed, not this batch.

Fake secrets only: every secret value in this file is a
made-up test string (e.g. "fake-secret-..."); no real API key
or token appears anywhere here.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from core.desktop import keyring_store as ks

try:
    import keyring as _real_keyring  # noqa: F401
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False


FAKE_SECRET = "fake-secret-7f3a9c1e-not-a-real-key"
FAKE_SECRET_OLD = "fake-secret-old-11111111-not-a-real-key"
FAKE_SECRET_NEW = "fake-secret-new-22222222-not-a-real-key"


class PasswordDeleteError(Exception):
    """Fake PasswordDeleteError — the wrapper matches on the
    type NAME, exactly as it does for keyring's real one, so
    the fake must carry the exact name (no underscore)."""


class NoKeyringError(Exception):
    """Fake NoKeyringError — same name-matching contract."""


class _FakeBackend:
    """In-memory (service, username) -> secret, plus a record
    of every service string the wrapper ever touched."""

    def __init__(self):
        self.store = {}
        self.services_touched = []

    def set_password(self, service, username, secret):
        self.services_touched.append(service)
        self.store[(service, username)] = secret

    def get_password(self, service, username):
        self.services_touched.append(service)
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.services_touched.append(service)
        if (service, username) not in self.store:
            raise PasswordDeleteError(service)
        del self.store[(service, username)]

    def list_usernames(self, service):
        self.services_touched.append(service)
        return sorted(u for (s, u) in self.store if s == service)


class _FakeWin32Cred:
    """Fake win32cred module: CredEnumerate returns canned
    credential dicts, including the compound-name form and a
    neighbouring service that must never leak into the list."""

    CRED_TYPE_GENERIC = 1

    def __init__(self, creds):
        self._creds = creds
        self.calls = []

    def CredEnumerate(self, Filter=None, Flags=0):
        self.calls.append(Filter)
        prefix = Filter or ""
        return [c for c in self._creds
                if (c.get("TargetName") or "").startswith(prefix)]


class KeyringStoreTest(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeBackend()
        ks.set_backend(self.fake)
        self.reasons = []

    def tearDown(self):
        ks.reset_backend()
        sys.modules.pop("win32cred", None)

    # -- helpers ------------------------------------------------------
    def _collect(self, result):
        """Record every reason/detail string a call produced,
        so the no-leak tests can assert the fake secret is in
        none of them."""
        for key in ("reason", "detail"):
            value = result.get(key)
            if isinstance(value, str):
                self.reasons.append(value)

    def _assert_secret_nowhere_leaked(self):
        for text in self.reasons:
            self.assertNotIn(FAKE_SECRET, text)
            self.assertNotIn(FAKE_SECRET_OLD, text)
            self.assertNotIn(FAKE_SECRET_NEW, text)

    # -- set / get ------------------------------------------------------
    def test_set_then_get_roundtrip(self):
        result = ks.set_secret("gemini", "aayur", FAKE_SECRET)
        self._collect(result)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["service"], "gemini")
        self.assertEqual(result["username"], "aayur")
        self.assertNotIn("secret", result)
        got = ks.get_secret("gemini", "aayur")
        self._collect(got)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["secret"], FAKE_SECRET)
        self._assert_secret_nowhere_leaked()

    def test_get_missing_is_honest_refusal_not_default(self):
        got = ks.get_secret("gemini", "nobody")
        self._collect(got)
        self.assertFalse(got["ok"])
        self.assertNotIn("secret", got)
        self.assertIn("no secret is stored", got["reason"])

    def test_set_twice_rotates_atomically(self):
        self.assertTrue(ks.set_secret("groq", "aayur", FAKE_SECRET_OLD)["ok"])
        self.assertTrue(ks.set_secret("groq", "aayur", FAKE_SECRET_NEW)["ok"])
        got = ks.get_secret("groq", "aayur")
        self.assertEqual(got["secret"], FAKE_SECRET_NEW)
        self.assertNotEqual(got["secret"], FAKE_SECRET_OLD)
        self._assert_secret_nowhere_leaked()

    def test_set_verifies_read_back(self):
        class _LosingBackend(_FakeBackend):
            def get_password(self, service, username):
                return None  # the write never lands

        ks.set_backend(_LosingBackend())
        result = ks.set_secret("gemini", "aayur", FAKE_SECRET)
        self._collect(result)
        self.assertFalse(result["ok"])
        self.assertIn("did not read back", result["reason"])
        self._assert_secret_nowhere_leaked()

    # -- delete -----------------------------------------------------------
    def test_delete_verifies_removal(self):
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        deleted = ks.delete_secret("gemini", "aayur")
        self._collect(deleted)
        self.assertTrue(deleted["ok"], deleted)
        self.assertNotIn("secret", deleted)
        got = ks.get_secret("gemini", "aayur")
        self.assertFalse(got["ok"])
        self.assertIn("no secret is stored", got["reason"])
        self._assert_secret_nowhere_leaked()

    def test_delete_missing_is_honest_refusal_not_silent_noop(self):
        deleted = ks.delete_secret("gemini", "nobody")
        self._collect(deleted)
        self.assertFalse(deleted["ok"])
        self.assertIn("no secret is stored", deleted["reason"])

    def test_delete_that_does_not_remove_is_failure(self):
        class _StubbornBackend(_FakeBackend):
            def delete_password(self, service, username):
                pass  # pretends to delete, removes nothing

        ks.set_backend(_StubbornBackend())
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        deleted = ks.delete_secret("gemini", "aayur")
        self.assertFalse(deleted["ok"])
        self.assertIn("still stored", deleted["reason"])

    # -- scoping ------------------------------------------------------------
    def test_cross_scope_isolation(self):
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        self.assertTrue(ks.set_secret("groq", "aayur", FAKE_SECRET_NEW)["ok"])
        self.assertTrue(ks.set_secret("gemini", "other", FAKE_SECRET_OLD)["ok"])
        self.assertEqual(
            ks.get_secret("gemini", "aayur")["secret"], FAKE_SECRET)
        self.assertEqual(
            ks.get_secret("groq", "aayur")["secret"], FAKE_SECRET_NEW)
        self.assertEqual(
            ks.get_secret("gemini", "other")["secret"], FAKE_SECRET_OLD)
        self.assertFalse(ks.get_secret("groq", "other")["ok"])
        self.assertFalse(ks.get_secret("other", "aayur")["ok"])
        self._assert_secret_nowhere_leaked()

    def test_services_are_namespaced_vyuk_prefix(self):
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        for touched in self.fake.services_touched:
            self.assertTrue(touched.startswith("VYUK-"), touched)
            self.assertNotEqual(touched, "VYUK-secrets")
        # The envelope module's service is never touched by this wrapper.
        self.assertNotIn("VYUK-secrets", self.fake.services_touched)

    # -- list ---------------------------------------------------------------
    def test_list_returns_usernames_never_secrets(self):
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        self.assertTrue(ks.set_secret("gemini", "other", FAKE_SECRET_OLD)["ok"])
        self.assertTrue(ks.set_secret("groq", "aayur", FAKE_SECRET_NEW)["ok"])
        listed = ks.list_secrets("gemini")
        self._collect(listed)
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["usernames"], ["aayur", "other"])
        self.assertNotIn("secret", listed)
        self.assertNotIn("secrets", listed)
        self._assert_secret_nowhere_leaked()

    def test_list_empty_service_is_empty_not_failure(self):
        listed = ks.list_secrets("nothing-here")
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["usernames"], [])

    def test_list_exact_match_filtering_via_win32cred(self):
        """The Windows enumeration path, proved with a fake
        win32cred: plain + compound targets count, a
        neighbouring service never leaks in."""
        creds = [
            {"TargetName": "VYUK-gemini", "UserName": "aayur",
             "Type": 1, "CredentialBlob": b"fake-blob-1"},
            {"TargetName": "alice@VYUK-gemini", "UserName": "alice",
             "Type": 1, "CredentialBlob": b"fake-blob-2"},
            {"TargetName": "VYUK-gemini2", "UserName": "mallory",
             "Type": 1, "CredentialBlob": b"fake-blob-3"},
            {"TargetName": "bob@VYUK-gemini2", "UserName": "bob",
             "Type": 1, "CredentialBlob": b"fake-blob-4"},
            {"TargetName": "VYUK-other", "UserName": "carol",
             "Type": 1, "CredentialBlob": b"fake-blob-5"},
        ]
        fake_cred = _FakeWin32Cred(creds)
        sys.modules["win32cred"] = fake_cred
        # The real backend, but with the enumeration call faked:
        # build it directly so the keyring import is not needed
        # for list_usernames (the win32cred import is what counts).
        backend = ks._RealBackend.__new__(ks._RealBackend)
        names = backend.list_usernames("VYUK-gemini")
        self.assertEqual(names, ["aayur", "alice"])
        # No filter is passed: a "VYUK-gemini" prefix filter
        # would never match the compound "alice@VYUK-gemini".
        self.assertEqual(fake_cred.calls, [None])

    def test_list_refuses_without_win32cred(self):
        """On this Linux lab machine win32cred cannot be
        imported at all (its cffi parse fails) — list() must
        refuse honestly, never guess."""
        sys.modules.pop("win32cred", None)
        backend = ks._RealBackend.__new__(ks._RealBackend)
        with self.assertRaises(ks._EngineUnavailable):
            backend.list_usernames("VYUK-gemini")

    # -- validation -----------------------------------------------------------
    def test_validation_refusals(self):
        cases = [
            (ks.set_secret, ("", "aayur", FAKE_SECRET)),
            (ks.set_secret, ("gemini", "", FAKE_SECRET)),
            (ks.set_secret, ("gemini", "aayur", "")),
            (ks.set_secret, ("gemini", "aayur", b"bytes-not-str")),
            (ks.set_secret, (123, "aayur", FAKE_SECRET)),
            (ks.set_secret, ("gemini", "aayur", FAKE_SECRET + "\x00")),
            (ks.get_secret, ("", "aayur")),
            (ks.get_secret, ("gemini", "")),
            (ks.delete_secret, ("", "aayur")),
            (ks.list_secrets, ("",)),
        ]
        for fn, args in cases:
            result = fn(*args)
            self._collect(result)
            self.assertFalse(result["ok"], (fn.__name__, args))
        self._assert_secret_nowhere_leaked()

    # -- no-leak pins -----------------------------------------------------------
    def test_secret_never_in_any_reason_or_error(self):
        # Drive every error path with the fake secret in play.
        self.assertTrue(ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
        for result in (
            ks.get_secret("gemini", "nobody"),
            ks.delete_secret("gemini", "nobody"),
            ks.set_secret("", "aayur", FAKE_SECRET),
            ks.set_secret("gemini", "aayur", ""),
            ks.get_secret("", "aayur"),
            ks.list_secrets(""),
        ):
            self._collect(result)
        self._assert_secret_nowhere_leaked()
        # And the secret is in no returned dict except get's field.
        got = ks.get_secret("gemini", "aayur")
        self.assertEqual(
            [k for k, v in got.items()
             if isinstance(v, str) and FAKE_SECRET in v],
            ["secret"])

    def test_module_has_no_logging_print_or_file_io(self):
        """Pin: the wrapper has nowhere for a secret to leak to.
        No logging call, no print, no file open in its source."""
        path = Path(ks.__file__)
        source = path.read_text(encoding="utf-8")
        # "logging" the word appears in the docstring's own
        # no-leak statement, so pin the CALLS, not the word.
        for token in ("import logging", "logging.", "print(",
                      "open(", "Path(", "write_text", "write_bytes"):
            self.assertNotIn(token, source, token)

    def test_no_file_written_by_fake_backend_operations(self):
        """The wrapper never touches the filesystem: run every
        operation with the cwd in an empty temp dir and assert
        it stays empty."""
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            try:
                os.chdir(tmp)
                self.assertTrue(
                    ks.set_secret("gemini", "aayur", FAKE_SECRET)["ok"])
                self.assertTrue(
                    ks.get_secret("gemini", "aayur")["ok"])
                self.assertTrue(ks.list_secrets("gemini")["ok"])
                self.assertTrue(
                    ks.delete_secret("gemini", "aayur")["ok"])
            finally:
                os.chdir(old)
            self.assertEqual(os.listdir(tmp), [])

    # -- real engine: honest degradation in the lab ----------------------------
    def test_real_engine_degrades_honestly_in_this_lab(self):
        """No fake backend: on BOTH lab interpreters there is no
        usable backend (sys: no keyring; qt: keyring installed
        but the fail backend — no D-Bus session). Every call
        must answer engine_unavailable, never a silent pass.
        If a usable backend ever appears, this test skips with
        the reason named (B-D9 pattern)."""
        if ks.engine_available():
            self.skipTest("usable keyring backend present — "
                          "lab degradation test not applicable")
        ks.reset_backend()
        status = ks.engine_status()
        self.assertFalse(status["available"])
        self.assertIsInstance(status["reason"], str)
        self.assertTrue(status["reason"])
        for result in (
            ks.set_secret("gemini", "aayur", FAKE_SECRET),
            ks.get_secret("gemini", "aayur"),
            ks.delete_secret("gemini", "aayur"),
            ks.list_secrets("gemini"),
        ):
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["reason"], "engine_unavailable")
        self._assert_secret_nowhere_leaked()

    def test_engine_status_names_the_backend_state(self):
        status = ks.engine_status()
        self.assertIn("available", status)
        self.assertIn("backend", status)
        self.assertIn("reason", status)
        if not status["available"]:
            self.assertIsNone(status["backend"])
            self.assertTrue(status["reason"])


class SecretsStoreUsabilityTest(unittest.TestCase):
    """The B-D10 fix in core/secrets_store.py: the reported
    backend name must never lie about the backend in use."""

    def test_reported_name_agrees_with_envelope_backend(self):
        from core import secrets_store as ss
        name = ss.active_backend_name()
        envelope = ss.protect_text("fake-blob-for-name-check")
        backend_in_envelope = envelope.split(":")[1]
        self.assertEqual(name, backend_in_envelope,
                         (name, envelope))
        self.assertEqual(ss.unprotect_text(envelope),
                         "fake-blob-for-name-check")

    def test_name_is_machine_key_in_this_lab(self):
        """Both lab interpreters have no usable keyring backend
        (sys: absent; qt: installed but the fail backend), so
        the honest name is machine-key — not keyring."""
        from core import secrets_store as ss
        self.assertEqual(ss.active_backend_name(), "machine-key")

    def test_usable_keyring_backend_reports_keyring(self):
        """With a fake keyring module whose get_keyring returns
        a usable (non-fail) backend, the name becomes keyring
        and blobs seal under it. Needs the real keyring package
        present so `keyring.backends.fail` imports (Qt venv);
        skipped with the reason named on system python3."""
        if not _HAS_KEYRING:
            self.skipTest("real keyring package absent — "
                          "backends.fail cannot be imported here")
        from core import secrets_store as ss

        class _UsableBackend:
            pass

        class _FakeKeyringModule:
            def __init__(self):
                self._store = {}

            def get_keyring(self):
                return _UsableBackend()

            def set_password(self, service, username, secret):
                self._store[(service, username)] = secret

            def get_password(self, service, username):
                return self._store.get((service, username))

        fake = _FakeKeyringModule()
        real = sys.modules.get("keyring")
        sys.modules["keyring"] = fake
        try:
            self.assertEqual(ss.active_backend_name(), "keyring")
            envelope = ss.protect_text("fake-blob-via-keyring")
            self.assertEqual(envelope.split(":")[1], "keyring")
            self.assertEqual(ss.unprotect_text(envelope),
                             "fake-blob-via-keyring")
        finally:
            if real is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = real


if __name__ == "__main__":
    unittest.main()
