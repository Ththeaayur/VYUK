"""OAuth sign-in — "Continue with Google" (round-3 OAuth build).

The first-run wizard no longer asks for a Gemini API key: it offers
one "Continue with Google" button running the standard installed-app
OAuth flow against VYUK's own Desktop-app client, and the live
session calls Gemini with Authorization: Bearer instead of a key.
What this suite proves:

* The scope set is exactly the spike-proven six — no bare
  generative-language, never cloud-platform.
* Token storage: the token lands in the secrets_store envelope
  (never plaintext), the file is 0600, is_signed_in() reads it
  honestly, disconnect() deletes it and attempts the revoke.
* The sign-in email comes from the id_token when present, and
  from the userinfo endpoint when it is not (the real flow's saved
  token carries NO id_token — proven by the spike).
* Auth resolution: OAuth wins over the legacy key; an unusable
  sign-in reports oauth_expired (never "no key"); neither is None.
* Client construction: the Bearer header swap (no x-goog-api-key,
  Authorization present, explicit v1beta) and the key client
  (explicit v1beta) — the swap is what the live session runs.
* validate_token(): REST models.get + the Live-door
  (bidiGenerateContent) check, in validate_api_key's vocabulary.
* The wizard: the sign-in page is reached for cloud/both and
  skipped for local; Continue warns once without a sign-in and
  advances on the second press; a stored sign-in (or legacy key)
  advances at once; the done page is honest about the cloud brain;
  the choice cards name the sign-in, never a key. The sign-in flow
  (mocked browser step, real save + status + buttons) and the
  sign-out path. The E-F303 width shape, kept for the new status
  label: the warning wraps and no page blows out the dialog.
* The Settings row: the Gemini sign-in row exists on the real
  Settings page and reflects the stored sign-in.

Discipline (mirrors test_first_run_onboarding.py): the GUI reads permission_grants / config through
the ``voice_assistant.core`` alias, while the flat test world
imports ``core.*`` — two module instances (the dual-import trap).
Wizard tests therefore drive the ALIASED modules — the ones the
GUI code actually reads — and assert on them. The grants store is
pointed at a temp dir per test; the record store is guarded by
_DataGuard (consumers-batch pattern). First-run progress
(QSettings firstrun/stage/*) is snapshotted and reset per test.
The .env file is snapshotted and restored per test, with
config.api_key and the Gemini OAuth client codes restored too.
Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips. google-genai tests skip the
same way on an interpreter without it.
"""

import base64
import json
import os
import pathlib
import sys
import tempfile
import time
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lab has no PortAudio: stub sounddevice (mirrors
# test_mic_first_use_ask.py / test_micfix.py).
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd


class _FakeStream:
    """Stand-in for sd.InputStream/OutputStream."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FakeDefault:
    device = (0, 0)


class _FakeSD:
    InputStream = _FakeStream
    OutputStream = _FakeStream
    default = _FakeDefault()

    @staticmethod
    def query_devices(kind=None):
        devs = [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]
        if kind == "input":
            return {"name": "Fake Mic"}
        return devs


if "sounddevice" in sys.modules and not hasattr(
        sys.modules["sounddevice"], "InputStream"):
    sys.modules["sounddevice"].__dict__.update(_FakeSD.__dict__)

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QLabel = None
    _QT = False

try:
    import google.genai  # noqa: F401
    _GENAI = True
except Exception:  # noqa: BLE001 — sys interpreter has no genai
    _GENAI = False

try:
    import google.oauth2.credentials  # noqa: F401
    _GOOGLE_AUTH = True
except Exception:  # noqa: BLE001 — sys interpreter has no google-auth
    _GOOGLE_AUTH = False

try:
    from tests.test_chat_ui import WindowBase as _WindowBase
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _WindowBase = None

_APP = None
_VA_SNAPSHOT = None
_VA_PKG = None
_VA_CORE = None
_VA_CONFIG_MOD = None
_VA_FACADE_MOD = None
_VA_OAUTH_MOD = None
_VA_GST_MOD = None
OnboardingDialog = None
config = None
permission_grants = None
settings_voice = None
gemini_oauth = None
gst = None


def _ensure_alias():
    """Re-register the voice_assistant alias in sys.modules.

    WindowBase._install_shim (test_chat_ui) replaces the alias
    packages and its _teardown_shim pops them — and unittest loads
    test classes alphabetically, so OAuthSettingsRowTests runs
    BEFORE OAuthWizardTests and wipes the alias my setUpModule
    installed. The aliased submodule objects survive (they are
    still referenced by this module's globals), but their parent
    packages are gone, so any relative import inside them
    (``from . import secrets_store``) raises ModuleNotFoundError
    — silently, inside a Qt slot — and the sign-in page's warning
    never fires. Re-registering the ORIGINAL package/module
    objects (same identities, not fresh ones) before each wizard
    test restores the alias exactly. No-op when it is intact."""
    import importlib as _importlib

    if _VA_PKG is not None and "voice_assistant" not in sys.modules:
        sys.modules["voice_assistant"] = _VA_PKG
    if _VA_CORE is not None and "voice_assistant.core" not in sys.modules:
        sys.modules["voice_assistant.core"] = _VA_CORE
    for name, mod in (
            ("voice_assistant.core.config", _VA_CONFIG_MOD),
            ("voice_assistant.core.gui_facade", _VA_FACADE_MOD),
            ("voice_assistant.core.gemini_oauth", _VA_OAUTH_MOD),
            ("voice_assistant.core.google_services_token", _VA_GST_MOD)):
        if mod is not None and name not in sys.modules:
            sys.modules[name] = mod

STAGES = ("interaction", "brain", "permissions", "first_mission")

GEMINI_SCOPES = [
    "https://www.googleapis.com/auth/generative-language.retriever",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
    "email",
    "profile",
]

WARNING_TEXT = (
    "You haven't signed in yet — you picked the cloud brain, so "
    "she'll think on this PC until you sign in with Google in "
    "Settings. Press Continue again to go on without signing in, "
    "or press Continue with Google above.")

NO_SIGNIN_CLOUD = (
    "You picked the cloud brain, but you haven't signed in with "
    "Google yet — she'll think on this PC until you sign in, in "
    "Settings.")

NO_SIGNIN_BOTH = (
    "She'll think on this PC for now — sign in with Google in "
    "Settings and she'll think in the cloud first, with the on-PC "
    "brain as her safety net.")

SIGNED_IN_CLOUD = (
    "She'll think in the cloud with your Google sign-in — her "
    "sharpest mind.")


def _fake_token_json(email=None, refresh="fake-refresh-token",
                     expiry="2999-01-01T00:00:00Z"):
    """A token JSON for the store (fake values, real shape)."""
    data = {
        "token": "fake-access-token",
        "refresh_token": refresh,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake-client-id",
        "client_secret": "fake-secret",
        "scopes": GEMINI_SCOPES,
        "expiry": expiry,
    }
    if email is not None:
        payload = base64.urlsafe_b64encode(
            json.dumps({"email": email}).encode()).decode().rstrip("=")
        data["id_token"] = f"header.{payload}.signature"
    return json.dumps(data)


class _FakeCreds:
    """Stand-in credentials for the mocked browser step."""

    token = "fake-access-token"
    refresh_token = "fake-refresh-token"
    expired = False
    valid = True

    def to_json(self):
        return _fake_token_json()


def setUpModule():
    """Mirror test_first_run_onboarding's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT, _QT
    global _VA_PKG, _VA_CORE, _VA_CONFIG_MOD
    global _VA_FACADE_MOD, _VA_OAUTH_MOD, _VA_GST_MOD
    global OnboardingDialog, config
    global permission_grants, settings_voice, gemini_oauth, gst
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    _VA_PKG = pkg
    _VA_CORE = core
    try:
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        from voice_assistant.core.gui_facade import (  # noqa: E402
            config as _cfg,
            permission_grants as _pg,
            settings_voice as _sv)
        from voice_assistant.core import (  # noqa: E402
            gemini_oauth as _oauth,
            google_services_token as _gst)
        OnboardingDialog = _OD
        config = _cfg
        permission_grants = _pg
        settings_voice = _sv
        gemini_oauth = _oauth
        gst = _gst
        _VA_CONFIG_MOD = sys.modules.get(
            "voice_assistant.core.config")
        _VA_FACADE_MOD = sys.modules.get(
            "voice_assistant.core.gui_facade")
        _VA_OAUTH_MOD = _oauth
        _VA_GST_MOD = _gst
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        _QT = False


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


class _DataGuard:
    """Restore data/ after a test (consumers-batch pattern)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        self._dirs = {p for p in (ROOT / "data").rglob("*")
                      if p.is_dir()}
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        for d in sorted(self._dirs, key=lambda p: len(p.parts)):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        for dirpath in sorted((ROOT / "data").rglob("*"), reverse=True):
            if dirpath.is_dir() and dirpath not in self._dirs:
                try:
                    dirpath.rmdir()
                except OSError:
                    pass
        return False


class _ProgressGuard:
    """Snapshot + reset the first-run progress (QSettings
    firstrun/stage/*): walking marks stages done, so a second
    dialog in this process would otherwise resume mid-flow."""

    def __init__(self):
        self._saved = {}

    def reset(self):
        try:
            from PySide6.QtCore import QSettings
        except ImportError:
            return  # no Qt, no dialog, nothing to reset
        s = QSettings("NovaAI", "Nova")
        for stage in STAGES:
            key = f"firstrun/stage/{stage}"
            self._saved[key] = s.value(key)
            s.remove(key)
        s.sync()

    def restore(self):
        try:
            from PySide6.QtCore import QSettings
        except ImportError:
            return  # no Qt, no dialog, nothing to restore
        s = QSettings("NovaAI", "Nova")
        for key, value in self._saved.items():
            if value is None:
                s.remove(key)
            else:
                s.setValue(key, value)
        s.sync()


class _EnvGuard:
    """Snapshot + restore .env, config.api_key, the Gemini OAuth
    client codes and their env vars."""

    def __enter__(self):
        self._env_path = ROOT / ".env"
        self._had = self._env_path.exists()
        self._saved = (self._env_path.read_bytes()
                       if self._had else None)
        cfg = config
        self._orig_api_key = (str(getattr(cfg, "api_key", "") or "")
                              if cfg is not None else "")
        self._orig_oauth_id = (str(getattr(
            cfg, "gemini_oauth_client_id", "") or "")
            if cfg is not None else "")
        self._orig_oauth_secret = (str(getattr(
            cfg, "gemini_oauth_client_secret", "") or "")
            if cfg is not None else "")
        self._orig_env = {k: os.environ.get(k) for k in (
            "GEMINI_API_KEY", "GEMINI_OAUTH_CLIENT_ID",
            "GEMINI_OAUTH_CLIENT_SECRET")}
        if cfg is not None:
            cfg.api_key = ""
            cfg.gemini_oauth_client_id = "test-client-id"
            cfg.gemini_oauth_client_secret = "test-client-secret"
        os.environ.pop("GEMINI_API_KEY", None)
        return self

    def __exit__(self, *exc):
        if self._had:
            self._env_path.write_bytes(self._saved)
        else:
            try:
                self._env_path.unlink()
            except OSError:
                pass
            try:
                (ROOT / ".env.tmp").unlink()
            except OSError:
                pass
        if config is not None:
            config.api_key = self._orig_api_key
            config.gemini_oauth_client_id = self._orig_oauth_id
            config.gemini_oauth_client_secret = self._orig_oauth_secret
        for key, value in self._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def _flat_oauth():
    """The flat (non-aliased) gemini_oauth module — for the
    sys-runnable tests that never touch the GUI."""
    import core.gemini_oauth as _flat

    return _flat


def _flat_gst():
    import core.google_services_token as _flat

    return _flat


class _TokenMixin(unittest.TestCase):
    """data/ guard + a clean token file per test."""

    def setUp(self):
        super().setUp()
        self._guard = _DataGuard()
        self._guard.__enter__()
        self._token_path = ROOT / "data" / "gemini_token.json"
        if self._token_path.exists():
            self._token_path.unlink()

    def tearDown(self):
        self._guard.__exit__()
        super().tearDown()

    def _write_token(self, text):
        _flat_gst()._save_token_text(
            text, _flat_oauth().GEMINI_TOKEN_FILENAME)


class OAuthScopesTests(_TokenMixin):
    """The scope set is exactly the spike-proven six."""

    def test_scopes_exact(self):
        oauth = _flat_oauth()
        self.assertEqual(list(oauth.GEMINI_SCOPES), GEMINI_SCOPES)

    def test_no_cloud_platform_scope(self):
        oauth = _flat_oauth()
        for scope in oauth.GEMINI_SCOPES:
            self.assertNotIn("cloud-platform", scope)

    def test_no_bare_generative_language_scope(self):
        oauth = _flat_oauth()
        self.assertNotIn(
            "https://www.googleapis.com/auth/generative-language",
            oauth.GEMINI_SCOPES)

    def test_token_filename(self):
        self.assertEqual(_flat_oauth().GEMINI_TOKEN_FILENAME,
                         "gemini_token.json")

    def test_live_api_version_pinned(self):
        self.assertEqual(_flat_oauth().LIVE_API_VERSION, "v1beta")


class OAuthTokenStoreTests(_TokenMixin):
    """Token storage: envelope, 0600, honest connected state."""

    def test_save_is_envelope_not_plaintext(self):
        self._write_token(_fake_token_json())
        raw = self._token_path.read_bytes()
        self.assertNotIn(b"fake-refresh-token", raw)
        self.assertNotIn(b"fake-access-token", raw)

    def test_save_is_0600(self):
        import stat

        self._write_token(_fake_token_json())
        mode = stat.S_IMODE(self._token_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_not_signed_in_without_file(self):
        self.assertFalse(_flat_oauth().is_signed_in())

    def test_signed_in_with_refresh_token(self):
        self._write_token(_fake_token_json())
        self.assertTrue(_flat_oauth().is_signed_in())

    def test_not_signed_in_with_corrupt_file(self):
        self._write_token("{not json")
        self.assertFalse(_flat_oauth().is_signed_in())

    def test_not_signed_in_with_expired_token_no_refresh(self):
        data = json.loads(_fake_token_json(expiry="2000-01-01T00:00:00Z"))
        del data["refresh_token"]
        self._write_token(json.dumps(data))
        self.assertFalse(_flat_oauth().is_signed_in())

    def test_signed_in_email_from_id_token(self):
        self._write_token(_fake_token_json(email="phoenixaayu05@gmail.com"))
        self.assertEqual(_flat_oauth().signed_in_email(),
                         "phoenixaayu05@gmail.com")

    def test_signed_in_email_none_without_id_token_or_network(self):
        import urllib.request

        self._write_token(_fake_token_json())
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            OSError("no network in this test"))
        try:
            self.assertIsNone(_flat_oauth().signed_in_email())
        finally:
            urllib.request.urlopen = real

    def test_disconnect_removes_file_and_revokes(self):
        import urllib.request

        self._write_token(_fake_token_json())
        posted = {}

        def _fake_urlopen(req, timeout=None):
            posted["url"] = req.full_url
            posted["body"] = req.data.decode()
            class _Resp:
                def close(self):
                    pass
            return _Resp()

        real = urllib.request.urlopen
        urllib.request.urlopen = _fake_urlopen
        try:
            message = _flat_oauth().disconnect()
        finally:
            urllib.request.urlopen = real
        self.assertFalse(self._token_path.exists())
        self.assertFalse(_flat_oauth().is_signed_in())
        self.assertIn("Signed out", message)
        self.assertEqual(posted["url"],
                         "https://oauth2.googleapis.com/revoke")
        self.assertIn("fake-refresh-token", posted["body"])

    def test_connect_without_codes(self):
        oauth = _flat_oauth()
        # Flat-config path: blank the codes on the flat config.
        import core.config as _flat_config

        saved = (_flat_config.config.gemini_oauth_client_id,
                 _flat_config.config.gemini_oauth_client_secret)
        _flat_config.config.gemini_oauth_client_id = ""
        _flat_config.config.gemini_oauth_client_secret = ""
        try:
            message = oauth.connect()
        finally:
            (_flat_config.config.gemini_oauth_client_id,
             _flat_config.config.gemini_oauth_client_secret) = saved
        self.assertIn("isn't set up", message)
        self.assertFalse(self._token_path.exists())


@unittest.skipUnless(_GOOGLE_AUTH, "google-auth unavailable")
class OAuthResolveTests(_TokenMixin):
    """Auth resolution: OAuth wins; expired is its own state."""

    def _fake_creds(self):
        creds = _FakeCreds()
        return creds

    def test_resolve_oauth_when_signed_in(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        import core.config as _flat_config

        _flat_config.config.api_key = ""
        kind, value = oauth.resolve_auth()
        self.assertEqual(kind, "oauth")
        self.assertIsNotNone(value)

    def test_resolve_oauth_expired_when_token_unusable(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        real_load = oauth.get_credentials
        oauth.get_credentials = lambda: None
        try:
            kind, value = oauth.resolve_auth()
        finally:
            oauth.get_credentials = real_load
        self.assertEqual(kind, "oauth_expired")
        self.assertIsNone(value)

    def test_resolve_key_when_no_sign_in(self):
        oauth = _flat_oauth()
        import core.config as _flat_config

        _flat_config.config.api_key = "AIzaSyFAKEKEY"
        try:
            kind, value = oauth.resolve_auth()
        finally:
            _flat_config.config.api_key = ""
        self.assertEqual(kind, "key")
        self.assertEqual(value, "AIzaSyFAKEKEY")

    def test_resolve_none_when_neither(self):
        oauth = _flat_oauth()
        import core.config as _flat_config

        _flat_config.config.api_key = ""
        kind, value = oauth.resolve_auth()
        self.assertIsNone(kind)
        self.assertIsNone(value)

    def test_resolve_oauth_wins_over_key(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        import core.config as _flat_config

        _flat_config.config.api_key = "AIzaSyFAKEKEY"
        try:
            kind, _value = oauth.resolve_auth()
        finally:
            _flat_config.config.api_key = ""
        self.assertEqual(kind, "oauth")


@unittest.skipUnless(_GENAI, "google-genai unavailable")
class OAuthClientTests(_TokenMixin):
    """Client construction: the Bearer header swap, explicit v1beta."""

    _PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY",
                   "HTTPS_PROXY", "all_proxy", "ALL_PROXY")

    def setUp(self):
        super().setUp()
        # The lab shell exports proxy vars (an egress proxy with
        # credentials) and a no_proxy list whose bracketed-IPv6
        # entries httpx 0.28 cannot parse as URLPatterns
        # (``all://[::1]`` dies in normalize_port with
        # ``invalid literal for int(): ':1]'``). The live-session
        # harness sidesteps this the same way: pop the proxy vars
        # and shrink no_proxy to localhost. The user's machine
        # has no such env.
        self._saved_proxy = {k: os.environ.get(k)
                             for k in self._PROXY_VARS
                             + ("no_proxy", "NO_PROXY")}
        for key in self._PROXY_VARS:
            os.environ.pop(key, None)
        os.environ["no_proxy"] = "localhost,127.0.0.1"
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"

    def tearDown(self):
        for key, value in self._saved_proxy.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def test_bearer_client_swaps_headers(self):
        oauth = _flat_oauth()
        client = oauth.build_bearer_client(_FakeCreds())
        headers = client._api_client._http_options.headers
        self.assertNotIn("x-goog-api-key", headers)
        self.assertTrue(
            str(headers.get("Authorization", "")).startswith("Bearer "))
        self.assertEqual(
            str(client._api_client._http_options.api_version), "v1beta")

    def test_bearer_client_needs_credentials(self):
        oauth = _flat_oauth()
        with self.assertRaises(ValueError):
            oauth.build_bearer_client(None)

    def test_key_client_carries_key_and_v1beta(self):
        oauth = _flat_oauth()
        client = oauth.build_key_client("AIzaSyFAKEKEY")
        headers = client._api_client._http_options.headers
        self.assertIn("x-goog-api-key", headers)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(
            str(client._api_client._http_options.api_version), "v1beta")

    def test_build_client_for_auth_dispatch(self):
        oauth = _flat_oauth()
        bearer = oauth.build_client_for_auth("oauth", _FakeCreds())
        self.assertIn("Authorization",
                      bearer._api_client._http_options.headers)
        keyed = oauth.build_client_for_auth("key", "AIzaSyFAKEKEY")
        self.assertIn("x-goog-api-key",
                      keyed._api_client._http_options.headers)
        with self.assertRaises(ValueError):
            oauth.build_client_for_auth("bogus", None)


@unittest.skipUnless(_GOOGLE_AUTH, "google-auth unavailable")
class OAuthValidateTests(_TokenMixin):
    """validate_token(): REST + Live-door, in the key vocabulary."""

    def _fake_model(self, actions):
        class _Model:
            supported_actions = list(actions)
        return _Model()

    def _fake_client(self, model_obj=None, error=None):
        class _Models:
            def get(self, model=None):
                if error is not None:
                    raise error
                return model_obj

        class _Client:
            models = _Models()
        return _Client()

    def test_validate_ok_when_live_supported(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        real = oauth.build_bearer_client
        oauth.build_bearer_client = lambda creds=None: self._fake_client(
            model_obj=self._fake_model(
                ["bidiGenerateContent", "generateContent"]))
        try:
            ok, kind, message = oauth.validate_token()
        finally:
            oauth.build_bearer_client = real
        self.assertTrue(ok)
        self.assertEqual(kind, "ok")
        self.assertIn("gemini-3.8-live", message)

    def test_validate_model_when_live_not_supported(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        real = oauth.build_bearer_client
        oauth.build_bearer_client = lambda creds=None: self._fake_client(
            model_obj=self._fake_model(["generateContent"]))
        try:
            ok, kind, message = oauth.validate_token()
        finally:
            oauth.build_bearer_client = real
        self.assertFalse(ok)
        self.assertEqual(kind, "model")
        self.assertIn("bidiGenerateContent", message)

    def test_validate_auth_when_not_signed_in(self):
        oauth = _flat_oauth()
        ok, kind, message = oauth.validate_token()
        self.assertFalse(ok)
        self.assertEqual(kind, "auth")
        self.assertIn("Not signed in", message)

    def test_validate_auth_when_api_refuses(self):
        oauth = _flat_oauth()
        self._write_token(_fake_token_json())
        real = oauth.build_bearer_client

        class _ApiError(Exception):
            # _classify_key_error reads .code (the SDK's APIError
            # shape) — a bare Exception has no code and classifies
            # "unknown", so the refusal must carry one.
            code = 401

        oauth.build_bearer_client = lambda creds=None: self._fake_client(
            error=_ApiError("401 UNAUTHENTICATED: bad credentials"))
        try:
            ok, kind, message = oauth.validate_token()
        finally:
            oauth.build_bearer_client = real
        self.assertFalse(ok)
        self.assertEqual(kind, "auth")


class _StoreMixin(unittest.TestCase):
    """Temp-dir grants store + data/ guard + settings_voice
    reset + progress reset + .env guard (the wizard _StoreMixin;
    the pattern test_cloudkey_wizard.py used before the OAuth
    build removed the key page)."""

    def setUp(self):
        super().setUp()
        # The Settings-row tests (alphabetically first) run a
        # WindowBase whose teardown pops the voice_assistant alias
        # — re-register it before every wizard test or the
        # sign-in page's relative imports fail inside Qt slots.
        _ensure_alias()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pg = permission_grants
        if pg is None:
            from core import permission_grants as _flat_pg
            pg = _flat_pg
        self._pg = pg
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        self._guard = _DataGuard()
        self._guard.__enter__()
        if settings_voice is not None:
            settings_voice.reset_for_tests()
        self._progress = _ProgressGuard()
        self._progress.reset()
        self._env = _EnvGuard()
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__()
        self._progress.restore()
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()

    # -- wizard driving -------------------------------------------
    def _pump(self, seconds=0.1):
        end = time.time() + seconds
        while time.time() < end:
            _QA.instance().processEvents()
            time.sleep(0.02)

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self._pump()
        dlg.resize(780, 560)
        self._pump()
        return dlg

    def _cur(self, dlg):
        return dlg.pages.currentWidget()

    def _go_next(self, dlg):
        dlg.btn_next.click()
        self._pump()

    def _walk_to_signin(self, dlg, choice="cloud"):
        # Round-3 wizard expansion: language is first, name +
        # voice are main flow, and profile sits between choice
        # and sign-in — seven steps from open (was welcome ->
        # mic -> choice -> sign-in).
        self.assertIs(self._cur(dlg), dlg._language_page)
        self._go_next(dlg)  # language -> welcome
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> name
        self._go_next(dlg)  # name -> voice
        self._go_next(dlg)  # voice -> choice
        dlg._select_choice(choice)
        self._pump()
        self._go_next(dlg)  # choice -> profile
        self._go_next(dlg)  # profile -> sign-in (cloud/both)
        self.assertIs(self._cur(dlg), dlg._signin_page)

    def _warn(self, dlg):
        """First Continue with no sign-in: warns, stays."""
        self._go_next(dlg)
        self.assertIs(self._cur(dlg), dlg._signin_page,
                      "first Continue with no sign-in must stay")
        self.assertEqual(dlg.ob_signin_status.text(), WARNING_TEXT)

    def _viewport(self, dlg):
        return dlg._pages_scroll.viewport().width()

    def _right_edge(self, widget, page):
        from PySide6.QtCore import QPoint as _QPoint

        return widget.mapTo(page, _QPoint(widget.width(), 0)).x()

    def _walk_to_done(self, dlg, max_clicks=16):
        for _ in range(max_clicks):
            if self._cur(dlg) is dlg._done_page:
                return True
            if self._cur(dlg) is dlg._signin_page:
                self._go_next(dlg)
                if self._cur(dlg) is dlg._signin_page:
                    self._go_next(dlg)  # second, deliberate press
                continue
            if self._cur(dlg) is dlg._selftest_page:
                self._pump(0.6)  # self-test auto-runs on show
            self._go_next(dlg)
        return self._cur(dlg) is dlg._done_page

    def _write_token(self, text):
        gst._save_token_text(text, gemini_oauth.GEMINI_TOKEN_FILENAME)


@unittest.skipUnless(_QT, "Qt unavailable")
class OAuthWizardTests(_StoreMixin):
    """The sign-in page, its flow, and the honest done page."""

    def test_signin_page_reached_for_cloud(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self.assertEqual(dlg.ob_btn_signin.text(), "Continue with Google")
        self.assertEqual(dlg.ob_signin_status.text(), "Not signed in yet.")
        self.assertTrue(dlg.ob_signin_status.wordWrap())
        self.assertFalse(dlg.ob_btn_signout.isVisible())
        self.assertFalse(dlg.ob_btn_test_signin.isVisible())

    def test_signin_page_reached_for_both(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "both")
        self.assertIs(self._cur(dlg), dlg._signin_page)

    def test_no_signin_first_continue_warns(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self._warn(dlg)

    def test_no_signin_second_continue_advances(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self._warn(dlg)
        self._go_next(dlg)
        # Round-3 wizard expansion: the Google-services page
        # now sits between sign-in and the browser ask.
        self.assertIs(self._cur(dlg), dlg._google_page)

    def test_back_and_forth_rewarns(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self._warn(dlg)
        dlg.btn_back.click()
        self._pump()
        # Round-3 wizard expansion: profile sits between choice
        # and sign-in, so Back lands on profile now.
        self.assertIs(self._cur(dlg), dlg._profile_page)
        self._go_next(dlg)  # profile -> sign-in again (warning re-armed)
        self.assertIs(self._cur(dlg), dlg._signin_page)
        self._warn(dlg)

    def test_local_choice_skips_signin_page(self):
        dlg = self._dialog()
        # Round-3 wizard expansion: language is first and name
        # + voice are main flow — five steps to choice now.
        self._go_next(dlg)  # language -> welcome
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> name
        self._go_next(dlg)  # name -> voice
        self._go_next(dlg)  # voice -> choice
        dlg._select_choice("local")
        self._pump()
        self._go_next(dlg)  # choice -> onward (never the sign-in page)
        self.assertIsNot(self._cur(dlg), dlg._signin_page)
        self.assertTrue(self._walk_to_done(dlg))
        self.assertIn("think on this PC", dlg.done_brain_line.text())

    def test_done_page_no_signin_honest(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self.assertTrue(self._walk_to_done(dlg))
        self.assertEqual(dlg.done_brain_line.text(), NO_SIGNIN_CLOUD)

    def test_done_page_both_no_signin_honest(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "both")
        self.assertTrue(self._walk_to_done(dlg))
        line = dlg.done_brain_line.text()
        self.assertEqual(line, NO_SIGNIN_BOTH)
        self.assertFalse(line.startswith("She'll think in the cloud"),
                         "without a sign-in the cloud-first claim must not lead")

    def test_done_page_signed_in_claims_cloud(self):
        self._write_token(_fake_token_json())
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self._go_next(dlg)  # signed in: advances at once
        # Round-3 wizard expansion: the Google-services page
        # now sits between sign-in and the browser ask.
        self.assertIs(self._cur(dlg), dlg._google_page)
        self.assertTrue(self._walk_to_done(dlg))
        self.assertEqual(dlg.done_brain_line.text(), SIGNED_IN_CLOUD)

    def test_done_page_legacy_key_claims_cloud(self):
        config.api_key = "AIzaSyFAKEKEY"
        os.environ["GEMINI_API_KEY"] = "AIzaSyFAKEKEY"
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self._go_next(dlg)  # legacy key: advances at once
        # Round-3 wizard expansion: the Google-services page
        # now sits between sign-in and the browser ask.
        self.assertIs(self._cur(dlg), dlg._google_page)
        self.assertTrue(self._walk_to_done(dlg))
        self.assertIn("your Google sign-in", dlg.done_brain_line.text())

    def test_choice_page_names_signin_not_key(self):
        dlg = self._dialog()
        # Round-3 wizard expansion: language is first and name
        # + voice are main flow — five steps to choice now.
        self._go_next(dlg)  # language -> welcome
        self._go_next(dlg)  # welcome -> mic
        self._go_next(dlg)  # mic -> name
        self._go_next(dlg)  # name -> voice
        self._go_next(dlg)  # voice -> choice
        labels = [lbl.text() for lbl in
                  dlg._choice_page.findChildren(_QLabel)]
        self.assertTrue(
            any("Sign in with Google" in t for t in labels),
            "the cloud card must name the sign-in")
        self.assertFalse(
            any("pasted in on the next step" in t for t in labels),
            "no card may still ask for a pasted key")

    def test_signin_flow_mocked_browser_step(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        real_flow = gemini_oauth._run_flow
        real_validate = gemini_oauth.validate_token
        gemini_oauth._run_flow = lambda cfg: _FakeCreds()
        gemini_oauth.validate_token = lambda model=None: (
            True, "ok", "Model 'gemini-3.8-live' is available.")
        try:
            dlg.ob_btn_signin.click()
            deadline = time.time() + 30
            while time.time() < deadline:
                self._pump(0.2)
                if not getattr(dlg, "_ob_signin_running", False):
                    break
        finally:
            gemini_oauth._run_flow = real_flow
            gemini_oauth.validate_token = real_validate
        self.assertFalse(getattr(dlg, "_ob_signin_running", False),
                         "the sign-in worker must finish")
        self.assertTrue(gemini_oauth.is_signed_in())
        self.assertTrue((ROOT / "data" / "gemini_token.json").exists())
        self.assertEqual(dlg.ob_signin_status.text(),
                         "Signed in with Google.")
        self.assertTrue(dlg.ob_btn_signout.isVisible())
        self.assertTrue(dlg.ob_btn_test_signin.isVisible())
        self._go_next(dlg)
        # Round-3 wizard expansion: the Google-services page
        # now sits between sign-in and the browser ask.
        self.assertIs(self._cur(dlg), dlg._google_page)

    def test_signout_clears_token(self):
        self._write_token(_fake_token_json())
        dlg = self._dialog()
        self._walk_to_signin(dlg, "cloud")
        self.assertTrue(dlg.ob_btn_signout.isVisible())
        dlg.ob_btn_signout.click()
        self._pump()
        self.assertFalse(gemini_oauth.is_signed_in())
        self.assertFalse((ROOT / "data" / "gemini_token.json").exists())
        self.assertTrue(
            dlg.ob_signin_status.text().startswith("Signed out"))

    def test_signin_status_label_wraps(self):
        dlg = self._dialog()
        self.assertTrue(dlg.ob_signin_status.wordWrap(),
                        "ob_signin_status must wrap like its neighbours")

    def test_signin_page_minimum_width_fits_after_warning(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg)
        self._warn(dlg)
        vw = self._viewport(dlg)
        self.assertLessEqual(
            dlg._signin_page.minimumSizeHint().width(), vw,
            "the sign-in page must fit the dialog once warned")

    def test_stacked_minimum_width_fits_after_warning(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg)
        self._warn(dlg)
        vw = self._viewport(dlg)
        self.assertLessEqual(
            dlg.pages.minimumSizeHint().width(), vw,
            "no page may blow out the shared stacked widget")

    def test_setup_waiting_labels_visible_after_warning(self):
        dlg = self._dialog()
        self._walk_to_signin(dlg, "both")
        self._warn(dlg)
        self._go_next(dlg)  # second, deliberate press -> setup
        if dlg._setup_page is None:
            self.skipTest("pack complete, no setup page")
        self.assertIs(self._cur(dlg), dlg._setup_page)
        self._pump(0.3)
        vw = self._viewport(dlg)
        for stage, lbl in dlg._setup_rows.items():
            self.assertLessEqual(
                self._right_edge(lbl, dlg._setup_page), vw,
                f"setup {stage} state label is cut off")


@unittest.skipUnless(_QT, "Qt unavailable")
class OAuthSettingsRowTests(unittest.TestCase):
    """The Gemini sign-in row on the real Settings page."""

    def setUp(self):
        # Harness trap (see test_r3ef201_theme_box): synthesize
        # the voice_assistant package and pre-import the w9 chain
        # BEFORE WindowBase installs its shim, or the cards are
        # skipped silently and the row never exists.
        import types as _types

        _pkg = _types.ModuleType("voice_assistant")
        _pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = _pkg
        _core_pkg = _types.ModuleType("voice_assistant.core")
        _core_pkg.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = _core_pkg
        from voice_assistant.core.gui_facade import w9_wiring  # noqa: F401

        WindowBase = _WindowBase
        self._guard = _DataGuard()
        self._guard.__enter__()
        # The engine pick below persists BRAIN_ENGINE to .env —
        # snapshot it (and the key/codes) like the wizard tests do.
        self._env = _EnvGuard()
        self._env.__enter__()
        self._base = WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump_app(self.app, 0.4)
        self.window.resize(1280, 750)
        self.window.show()
        try:
            self.window._show_page(4)
        except Exception:  # noqa: BLE001 — page switch best-effort
            pass
        _pump_app(self.app, 0.6)
        # The sign-in row lives in think_cloud_box, which
        # _sync_think_card shows only for the cloud (gemini)
        # engine — the lab window boots on the local engine, so
        # pick the cloud engine first, exactly as a user who
        # wants the cloud brain would. (Without this the row's
        # ancestors stay hidden and isVisible() is False even
        # when the row itself is shown.)
        combo = getattr(self.window, "combo_brain_engine", None)
        if combo is not None:
            idx = combo.findData("gemini")
            if idx >= 0:
                combo.setCurrentIndex(idx)
        try:
            self.window._sync_think_card()
        except Exception:  # noqa: BLE001 — sync best-effort
            pass
        _pump_app(self.app, 0.3)

    def tearDown(self):
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass
        self._env.__exit__()
        self._guard.__exit__()

    def test_signin_row_present_and_signed_out(self):
        btn = getattr(self.window, "btn_gemini_signin", None)
        status = getattr(self.window, "gemini_signin_status", None)
        signout = getattr(self.window, "btn_gemini_signout", None)
        self.assertIsNotNone(btn, "the Settings sign-in button must exist")
        self.assertIsNotNone(status)
        self.assertIsNotNone(signout)
        self.assertEqual(btn.text(), "Continue with Google")
        self.assertEqual(status.text(), "Not signed in yet.")
        self.assertFalse(signout.isVisible())

    def test_signin_row_reflects_stored_signin(self):
        from voice_assistant.core import gemini_oauth as _oauth
        from voice_assistant.core import google_services_token as _gst

        _gst._save_token_text(_fake_token_json(),
                              _oauth.GEMINI_TOKEN_FILENAME)
        self.window._refresh_gemini_signin_row()
        _pump_app(self.app, 0.2)
        self.assertEqual(self.window.gemini_signin_status.text(),
                         "Signed in with Google.")
        self.assertEqual(self.window.btn_gemini_signin.text(),
                         "Test sign-in")
        self.assertTrue(self.window.btn_gemini_signout.isVisible())


def _pump_app(app, seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
