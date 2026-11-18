"""Music web rung, revived (R3, 2026-11-06) — tests.

Gap proved from the files before building:

* `_web_rung` was a silent dead end: `_spotify_web_session()`
  always returned False (retired v5.70), so the ladder skipped
  the rung on every play. The probe read VYUK's own browser
  profile for a Spotify session cookie — a profile that no
  longer exists, and a read that must never be repointed at
  his real Chrome cookie store.
* The revival needs NO session pre-check and NO owner ruling:
  the drive goes through his attached Chrome (Fix 3 explicit
  enable), and the browser layer's own login-wall probe (a
  visible password field, `_login_wall_result`) returns the
  needs-user-login terminal string at the click when he is
  not signed in. The awaited ruling was for a cookie-store
  session check; this revival does not use one.
* Latent false-claim hole found while reviving (not live
  before R3, because the dead probe ran first): with no
  browser attached, goto and click both refuse with "No
  controllable browser session is active." — caught by
  NEITHER of `_spotify_web_play`'s goto prefixes ("Error" /
  "Couldn't") — and the old form returned "spotify-web" on
  ANY click outcome, so the rung's triangulation fell back
  to audio evidence alone and a lingering session could read
  as a confirmed Spotify play that never happened. The fix:
  the track click must LAND ("Clicked", mirroring the YTM
  rung) or the rung returns None.
* `_other_playback_evidence()` classified by a hardcoded
  browser-name allowlist. The OS (WASAPI, via pycaw) gives
  no "is a browser" flag — a session carries State (0
  Inactive / 1 Active / 2 Expired; Active IS "sounding now",
  Microsoft Learn) and ProcessId (system sounds is PID 0).
  The check now classifies by those two properties and never
  reads a name: a sounding session counts whatever its name,
  a silent one never counts whatever its name.

Lab notes: no real browser, no real audio, no real Spotify
here. The browser is faked through core.browser, the audio
sessions through skills_media, the rungs and the continuity
timer through the rungs module's own globals.
"""

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from core import music_player_evidence as evidence  # noqa: E402
from core import music_player_rungs as rungs  # noqa: E402
from core import music_player_spotify as spotify  # noqa: E402

_TREEROOT = ROOT

_NO_BROWSER = (
    "No controllable browser session is active. Tap 'Connect my "
    "browser' and I'll set it up — one tap, about a minute. "
    "(Settings → Browser control → Enable browser control still "
    "works too.)")
_NEEDS_LOGIN = (
    "needs-user-login: Spotify needs you to sign in once in "
    "my browser — after that I can drive it.")


class _FakeSession:
    """A pycaw-shaped audio session: State + ProcessId only
    (the name is carried for realism — no production code
    reads it any more)."""

    def __init__(self, name=None, volume=True, state=1, pid=1234):
        self.SimpleAudioVolume = object() if volume else None
        self.Process = None
        self.State = state
        self.ProcessId = 0 if name is None else pid
        self._name = name


class _WebRungBase(unittest.TestCase):
    """Shared fakes for the revived web rung."""

    def setUp(self):
        rungs._cancel_continuity()

    def tearDown(self):
        rungs._cancel_continuity()

    def _patch_browser(self, goto, click):
        c1 = mock.patch("core.browser.browser_control",
                        return_value=goto)
        c2 = mock.patch("core.browser.browser_click",
                        return_value=click)
        c3 = mock.patch.object(rungs, "_settle", return_value=None)
        for c in (c1, c2, c3):
            c.start()
            self.addCleanup(c.stop)

    def _patch_confirm(self, confirmed):
        c = mock.patch.object(
            rungs, "_browser_playback_confirmed",
            return_value=confirmed)
        c.start()
        self.addCleanup(c.stop)
        return c


class TestWebRungRevived(_WebRungBase):
    """The rung plays through the attached browser, and
    nothing else."""

    def test_click_landed_and_confirmed_plays(self):
        self._patch_browser("Opened https://open.spotify.com/",
                            "Clicked tracklist-row 'Believer'.")
        self._patch_confirm(True)
        sched = mock.Mock()
        with mock.patch.object(rungs, "_schedule_continuity", sched):
            out = rungs._web_rung("Believer", [], schedule=True)
        self.assertIn("Playing on Spotify in my browser", out)
        self.assertIn("(playback confirmed)", out)
        sched.assert_called_once_with("spotify-web", "Believer")

    def test_no_browser_returns_none_even_with_lingering_audio(self):
        # The latent-hole regression test: goto and click both
        # refuse, audio evidence says a session lingers — and
        # still no claim, because no click ever landed.
        self._patch_browser(_NO_BROWSER, _NO_BROWSER)
        self._patch_confirm(True)
        with mock.patch.object(evidence, "_playback_evidence",
                               return_value=True):
            tried = []
            out = rungs._web_rung("Believer", tried)
        self.assertIsNone(out)
        self.assertTrue(tried)

    def test_spotify_web_play_no_browser_is_none(self):
        self._patch_browser(_NO_BROWSER, _NO_BROWSER)
        self.assertIsNone(spotify._spotify_web_play("Believer"))

    def test_login_wall_is_terminal(self):
        self._patch_browser("Opened https://open.spotify.com/",
                            _NEEDS_LOGIN)
        self._patch_confirm(True)
        out = rungs._web_rung("Believer", [])
        self.assertEqual(out, _NEEDS_LOGIN)

    def test_missed_click_returns_none_not_a_claim(self):
        self._patch_browser(
            "Opened https://open.spotify.com/",
            "I couldn't find anything matching "
            "'[data-testid=\"tracklist-row\"]' on Spotify.")
        self._patch_confirm(True)
        with mock.patch.object(evidence, "_playback_evidence",
                               return_value=True):
            tried = []
            out = rungs._web_rung("Believer", tried)
        self.assertIsNone(out)
        self.assertIn("Spotify web didn't play", tried)

    def test_click_landed_but_unconfirmed_is_honest(self):
        self._patch_browser("Opened https://open.spotify.com/",
                            "Clicked tracklist-row 'Believer'.")
        self._patch_confirm(False)
        out = rungs._web_rung("Believer", [])
        self.assertIn("couldn't confirm", out)
        self.assertNotIn("(playback confirmed)", out)

    def test_session_probe_is_gone(self):
        self.assertFalse(hasattr(spotify, "_spotify_web_session"))
        for path in ("core/music_player_spotify.py",
                     "core/music_player.py",
                     "core/music_player_rungs.py"):
            src = (_TREEROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("_spotify_web_session", src)


class TestWebRungUnderTheGuard(_WebRungBase):
    """The revived rung runs UNDER the single-source guard:
    stop-before-play, and the recheck never replays it."""

    def test_play_music_reaches_web_rung_after_stop(self):
        order = []
        with mock.patch.object(
                rungs, "_cancel_continuity",
                side_effect=lambda: order.append("cancel")), \
                mock.patch.object(
                    rungs, "_stop_current_playback",
                    side_effect=lambda: order.append("stop") or True), \
                mock.patch.object(
                    rungs, "_ytm_rung", return_value=None), \
                mock.patch.object(
                    rungs, "_spotify_app_rung", return_value=None), \
                mock.patch.object(
                    rungs, "_web_rung",
                    side_effect=lambda q, t, schedule=False: (
                        order.append("web"),
                        "Playing on Spotify in my browser: 'x' "
                        "(playback confirmed).")[1]), \
                mock.patch.object(
                    rungs, "_local_rung", return_value=None):
            out = rungs.play_music("x")
        self.assertEqual(order, ["cancel", "stop", "web"])
        self.assertIn("Playing on Spotify", out)

    def test_recheck_for_spotify_web_falls_to_local_only(self):
        announced = []
        web = mock.Mock(return_value=None)
        local = mock.Mock(
            return_value="Playing 'song.mp3' from your Music "
                         "folder (audio confirmed).")
        with mock.patch.object(rungs, "_stream_alive",
                               return_value=False), \
                mock.patch.object(rungs, "_other_playback_evidence",
                                  return_value=False), \
                mock.patch.object(rungs, "_announce",
                                  side_effect=announced.append), \
                mock.patch.object(rungs, "_page_playback_state",
                                  return_value="playing"), \
                mock.patch.object(rungs, "_web_rung", web), \
                mock.patch.object(rungs, "_local_rung", local):
            rungs._continuity_recheck("spotify-web", "lofi")
        web.assert_not_called()
        local.assert_called_once()
        self.assertTrue(any("song.mp3" in a for a in announced),
                        announced)


class TestEvidenceByProperty(unittest.TestCase):
    """_other_playback_evidence() classifies by State +
    ProcessId — the verdict follows State, never the name."""

    def _check(self, sessions, err=None):
        with mock.patch(
                "core.skills_media._audio_sessions",
                return_value=(sessions, err)):
            return evidence._other_playback_evidence()

    def test_active_chrome_counts(self):
        self.assertTrue(
            self._check([_FakeSession("chrome.exe", state=1)]))

    def test_inactive_chrome_does_not_count(self):
        self.assertFalse(
            self._check([_FakeSession("chrome.exe", state=0)]))

    def test_same_name_different_state(self):
        # One name, two verdicts: State decides, name never
        # even consulted.
        self.assertTrue(
            self._check([_FakeSession("chrome.exe", state=1)]))
        self.assertFalse(
            self._check([_FakeSession("chrome.exe", state=0)]))

    def test_silent_spotify_does_not_block(self):
        self.assertFalse(
            self._check([_FakeSession("Spotify.exe", state=0)]))

    def test_unlisted_browser_active_counts(self):
        self.assertTrue(
            self._check([_FakeSession("vivaldi.exe", state=1)]))
        self.assertTrue(
            self._check([_FakeSession("arc.exe", state=1)]))

    def test_system_sounds_pid_zero_ignored(self):
        self.assertFalse(self._check([_FakeSession(None, state=1)]))

    def test_expired_session_ignored(self):
        self.assertFalse(
            self._check([_FakeSession("vlc.exe", state=2)]))


class TestNoAutoEnable(unittest.TestCase):
    """The revival never flips the browser opt-in: the rung
    only drives a browser that is already attached."""

    def test_music_modules_never_touch_browser_enable(self):
        for path in ("core/music_player_rungs.py",
                     "core/music_player_spotify.py",
                     "core/music_player_evidence.py",
                     "core/music_player.py"):
            src = (_TREEROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("enable_browser_control", src)
            self.assertNotIn("browser_allow_real_chrome_attach", src)

    def test_no_hardcoded_browser_name_list_remains(self):
        src = (_TREEROOT / "core" / "music_player_evidence.py").read_text(
            encoding="utf-8")
        self.assertNotIn("_BROWSER_SESSION_NAMES", src)
        for name in ("chrome.exe", "msedge.exe", "firefox.exe",
                     "brave.exe", "opera.exe"):
            self.assertNotIn(name, src)


if __name__ == "__main__":
    unittest.main()
