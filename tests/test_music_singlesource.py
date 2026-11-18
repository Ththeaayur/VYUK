"""Music single-source guard (v6.2.x field fix, 2026-11-05) — tests.

Field report: "Spotify and YouTube are competing." Gap proved
from the files before building (see the batch report):

* play_music() never stopped what was already playing before
  starting a new rung — a second request started a second
  source on top of the first.
* The ~20 s continuity recheck fell through to another rung
  whenever the browser page read dead, even while the Spotify
  app was still sounding (the page-error verdict is death
  "even if a render session lingers").
* _schedule_continuity() kept no timer handle, so a new play
  could never cancel a pending recheck — a stale timer could
  start a second source after the user asked for something
  else.
* The play_music tool description still said "Spotify app
  first" although the code runs YouTube Music first since
  v5.57.1.
* Load-bearing for the guard: _playback_evidence() bool()ed
  the (sessions, error) tuple _audio_sessions() returns, so
  on Windows it was True whether anything was sounding or
  not — every "audio confirmed" claim built on it was
  vacuous. It now unpacks the tuple.

Lab notes: no real audio, no real browser, no real Spotify
here. Every seam is module-level (the house pattern): the
audio-session probe is faked through skills_media, the media
key through desktop.control, the rungs and the continuity
timer through the rungs module's own globals.
"""

import pathlib
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from core import music_player_evidence as evidence  # noqa: E402
from core import music_player_rungs as rungs  # noqa: E402
from core import music_player_spotify as spotify  # noqa: E402
from core.desktop import control_system as _control_system  # noqa: E402


class _FakeSession:
    """A pycaw-shaped audio session: a process name, a
    volume handle (or None for a session that cannot make
    sound), a WASAPI State (1 Active = sounding, 0
    Inactive, 2 Expired) and a ProcessId (0 = system
    sounds). R3: the name is carried only for realism — no
    production code reads it any more."""

    def __init__(self, name=None, volume=True, state=1, pid=1234):
        self.SimpleAudioVolume = object() if volume else None
        self.Process = (None if name is None else
                        SimpleNamespace(name=lambda: name))
        self.State = state
        self.ProcessId = 0 if name is None else pid


class _FakeTimer:
    """A threading.Timer stand-in: records, never fires on
    its own — the test fires it by calling the stored fn."""

    instances = []

    def __init__(self, delay, fn, args=None, kwargs=None):
        self.delay = delay
        self.fn = fn
        self.args = tuple(args or ())
        self.kwargs = dict(kwargs or {})
        self.started = False
        self.cancelled = False
        self.daemon = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def _audio_sessions_returning(sessions, err=None):
    return (sessions, err)


class TestPlaybackEvidence(unittest.TestCase):
    """_playback_evidence() unpacks the (sessions, error)
    tuple instead of bool()ing it."""

    def test_sessions_present_is_evidence(self):
        with mock.patch(
                "core.skills_media._audio_sessions",
                return_value=_audio_sessions_returning(
                    [_FakeSession("Spotify.exe")])):
            self.assertTrue(evidence._playback_evidence())

    def test_empty_session_list_is_no_evidence(self):
        # The old bug: bool(([], None)) is True, so an empty
        # list read as "audio confirmed" on Windows.
        with mock.patch(
                "core.skills_media._audio_sessions",
                return_value=_audio_sessions_returning([])):
            self.assertFalse(evidence._playback_evidence())

    def test_error_is_no_evidence(self):
        # The old bug: bool((None, "boom")) is True.
        with mock.patch(
                "core.skills_media._audio_sessions",
                return_value=_audio_sessions_returning(
                    None, "Could not read sessions")):
            self.assertFalse(evidence._playback_evidence())

    def test_probe_failure_is_no_evidence(self):
        with mock.patch(
                "core.skills_media._audio_sessions",
                side_effect=RuntimeError("pycaw gone")):
            self.assertFalse(evidence._playback_evidence())


class TestOtherPlaybackEvidence(unittest.TestCase):
    """_other_playback_evidence(): a session counts ONLY
    when it is SOUNDING (WASAPI State Active) — no process
    name is read anywhere (R3).

    INTENTIONAL (R3 behaviour change): the old browser-name
    allowlist is gone. An Active session counts whatever its
    name — including chrome.exe — because sounding is
    sounding; an Inactive session never counts whatever its
    name — that is the lingerer the allowlist existed for.
    System sounds is ProcessId 0, a PID fact, not a name."""

    def _check(self, sessions, err=None):
        with mock.patch(
                "core.skills_media._audio_sessions",
                return_value=_audio_sessions_returning(sessions, err)):
            return evidence._other_playback_evidence()

    def test_spotify_app_counts(self):
        self.assertTrue(self._check([_FakeSession("Spotify.exe")]))

    def test_local_player_counts(self):
        self.assertTrue(self._check([_FakeSession("vlc.exe")]))

    def test_inactive_browser_session_does_not_count(self):
        # The lingerer: the browser's session after its page
        # errored — State Inactive, no stream running.
        for name in ("chrome.exe", "msedge.exe", "firefox.exe",
                     "brave.exe", "opera.exe"):
            self.assertFalse(
                self._check([_FakeSession(name, state=0)]),
                f"inactive {name} must not read as a competing source")

    def test_active_browser_session_counts(self):
        # R3 behaviour change: a SOUNDING browser session
        # counts — the old allowlist ignored it and could
        # double-play over another live tab.
        for name in ("chrome.exe", "msedge.exe", "firefox.exe",
                     "brave.exe", "opera.exe"):
            self.assertTrue(
                self._check([_FakeSession(name, state=1)]),
                f"active {name} is sounding and must count")

    def test_unlisted_browser_name_active_counts(self):
        # No name list: an unlisted browser sounding counts.
        self.assertTrue(self._check([_FakeSession("vivaldi.exe")]))
        self.assertTrue(self._check([_FakeSession("arc.exe")]))

    def test_player_under_browser_name_counts(self):
        # Same name, different State: the verdict follows
        # State, never the name.
        self.assertTrue(
            self._check([_FakeSession("chrome.exe", state=1)]))
        self.assertFalse(
            self._check([_FakeSession("chrome.exe", state=0)]))

    def test_expired_session_does_not_count(self):
        self.assertFalse(
            self._check([_FakeSession("Spotify.exe", state=2)]))

    def test_system_sounds_do_not_count(self):
        self.assertFalse(self._check([_FakeSession(None)]))

    def test_browser_plus_spotify_counts(self):
        # The field case: YTM in Chrome errored while the
        # Spotify app kept sounding — the guard must see it.
        # (Chrome here is Inactive — the lingerer — so the
        # count comes from Spotify alone.)
        self.assertTrue(self._check([
            _FakeSession("chrome.exe", state=0),
            _FakeSession("Spotify.exe"),
        ]))

    def test_volume_less_session_does_not_count(self):
        self.assertFalse(
            self._check([_FakeSession("Spotify.exe", volume=False)]))

    def test_unreadable_state_is_competing(self):
        # Fail-closed: a session whose State cannot be read
        # blocks a second source rather than risking overlap.
        class _BadState:
            SimpleAudioVolume = object()
            ProcessId = 1234

            @property
            def State(self):
                raise RuntimeError("COM gone")

        self.assertTrue(self._check([_BadState()]))

    def test_unreadable_process_id_is_competing(self):
        # Fail-closed: a session whose owner cannot be read
        # blocks a second source rather than risking overlap.
        class _BadPid:
            SimpleAudioVolume = object()
            State = 1

            @property
            def ProcessId(self):
                raise RuntimeError("COM gone")

        self.assertTrue(self._check([_BadPid()]))

    def test_empty_or_error_is_no_evidence(self):
        self.assertFalse(self._check([]))
        self.assertFalse(self._check(None, "boom"))


class TestStopCurrentPlayback(unittest.TestCase):
    """_stop_current_playback(): media-stop key only when a
    session exists; never raises; never touches apps/tabs."""

    def test_quiet_machine_sends_no_key(self):
        with mock.patch.object(
                evidence, "_playback_evidence",
                return_value=False), \
                mock.patch.object(_control_system, "media_control") \
                as media:
            self.assertFalse(spotify._stop_current_playback())
            media.assert_not_called()

    def test_live_session_sends_media_stop(self):
        with mock.patch.object(
                evidence, "_playback_evidence",
                return_value=True), \
                mock.patch.object(_control_system, "media_control") \
                as media:
            self.assertTrue(spotify._stop_current_playback())
            media.assert_called_once_with("stop")

    def test_media_key_failure_is_silent(self):
        with mock.patch.object(
                evidence, "_playback_evidence",
                return_value=True), \
                mock.patch.object(_control_system, "media_control",
                                  side_effect=RuntimeError("no keys")):
            self.assertFalse(spotify._stop_current_playback())


class TestPlayMusicStopBeforePlay(unittest.TestCase):
    """play_music() cancels the stale recheck and stops the
    old source before any rung starts."""

    def setUp(self):
        rungs._cancel_continuity()
        _FakeTimer.instances.clear()

    def tearDown(self):
        rungs._cancel_continuity()
        _FakeTimer.instances.clear()

    def test_new_play_stops_old_source_first(self):
        order = []
        with mock.patch.object(
                rungs, "_stop_current_playback",
                side_effect=lambda: order.append("stop") or True), \
                mock.patch.object(
                    rungs, "_cancel_continuity",
                    side_effect=lambda: order.append("cancel")), \
                mock.patch.object(
                    rungs, "_ytm_rung",
                    side_effect=lambda q, t, schedule=False: (
                        order.append("ytm"),
                        "Playing on YouTube Music: 'x' "
                        "(playback confirmed).")[1]):
            out = rungs.play_music("x")
        self.assertEqual(order, ["cancel", "stop", "ytm"])
        self.assertIn("YouTube Music", out)

    def test_failed_play_reports_stopped_old(self):
        with mock.patch.object(
                rungs, "_stop_current_playback",
                return_value=True), \
                mock.patch.object(rungs, "_ytm_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_spotify_app_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_web_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_local_rung",
                                  return_value=None):
            out = rungs.play_music("x")
        self.assertIn("couldn't start any music", out)
        self.assertIn("The previous playback was stopped.", out)

    def test_failed_play_without_stop_says_nothing_extra(self):
        with mock.patch.object(
                rungs, "_stop_current_playback",
                return_value=False), \
                mock.patch.object(rungs, "_ytm_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_spotify_app_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_web_rung",
                                  return_value=None), \
                mock.patch.object(rungs, "_local_rung",
                                  return_value=None):
            out = rungs.play_music("x")
        self.assertNotIn("previous playback", out)


class TestContinuityRecheckGuard(unittest.TestCase):
    """The recheck never starts a second rung over live
    audio, falls through when the stream is truly dead, and
    ignores stale generations."""

    def setUp(self):
        rungs._cancel_continuity()
        _FakeTimer.instances.clear()

    def tearDown(self):
        rungs._cancel_continuity()
        _FakeTimer.instances.clear()

    def _patch_recheck(self, stream_alive, other_evidence):
        """Patch the recheck's judges; return the announce
        recorder and the rung mocks."""
        announced = []
        ctx = mock.patch.object(
            rungs, "_stream_alive", return_value=stream_alive)
        ctx2 = mock.patch.object(
            rungs, "_other_playback_evidence",
            return_value=other_evidence)
        ctx3 = mock.patch.object(
            rungs, "_announce", side_effect=announced.append)
        ctx4 = mock.patch.object(
            rungs, "_page_playback_state", return_value="playing")
        web = mock.Mock(return_value=None)
        local = mock.Mock(
            return_value="Playing 'song.mp3' from your Music "
                         "folder (audio confirmed).")
        ctx5 = mock.patch.object(rungs, "_web_rung", web)
        ctx6 = mock.patch.object(rungs, "_local_rung", local)
        for c in (ctx, ctx2, ctx3, ctx4, ctx5, ctx6):
            c.start()
            self.addCleanup(c.stop)
        return announced, web, local

    def test_recheck_blocked_by_other_source(self):
        announced, web, local = self._patch_recheck(
            stream_alive=False, other_evidence=True)
        rungs._continuity_recheck("ytm", "lofi")
        web.assert_not_called()
        local.assert_not_called()
        self.assertTrue(
            any("didn't start another source" in a
                for a in announced),
            announced)

    def test_recheck_falls_through_when_truly_dead(self):
        announced, web, local = self._patch_recheck(
            stream_alive=False, other_evidence=False)
        rungs._continuity_recheck("ytm", "lofi")
        web.assert_called_once()
        local.assert_called_once()
        self.assertTrue(
            any("cut off" in a and "song.mp3" in a
                for a in announced),
            announced)

    def test_recheck_silent_when_stream_alive(self):
        announced, web, local = self._patch_recheck(
            stream_alive=True, other_evidence=True)
        rungs._continuity_recheck("ytm", "lofi")
        web.assert_not_called()
        local.assert_not_called()
        self.assertEqual(announced, [])

    def test_schedule_replaces_pending_timer(self):
        with mock.patch("threading.Timer", _FakeTimer):
            rungs._schedule_continuity("ytm", "first")
            first = _FakeTimer.instances[-1]
            rungs._schedule_continuity("ytm", "second")
            second = _FakeTimer.instances[-1]
        self.assertTrue(first.cancelled)
        self.assertTrue(first.started)
        self.assertTrue(second.started)
        self.assertFalse(second.cancelled)

    def test_stale_timer_does_nothing(self):
        announced, web, local = self._patch_recheck(
            stream_alive=False, other_evidence=False)
        with mock.patch("threading.Timer", _FakeTimer):
            rungs._schedule_continuity("ytm", "old song")
            stale = _FakeTimer.instances[-1]
            # A new play supersedes the old timer.
            with mock.patch.object(
                    rungs, "_stop_current_playback",
                    return_value=False), \
                    mock.patch.object(
                        rungs, "_ytm_rung",
                        return_value="Playing on YouTube Music: "
                                     "'new' (playback confirmed)."):
                rungs.play_music("new")
        self.assertTrue(stale.cancelled)
        # Firing the stale timer anyway must start nothing.
        stale.fn(*stale.args)
        web.assert_not_called()
        local.assert_not_called()
        self.assertEqual(announced, [])

    def test_cancel_is_safe_when_nothing_armed(self):
        rungs._cancel_continuity()  # must not raise
        rungs._cancel_continuity()


class TestPlayMusicDescription(unittest.TestCase):
    """The registered play_music description matches the
    real ladder order (YouTube Music first since v5.57.1)
    and keeps the browser-control rule and the first
    sentence the router purposes derive from."""

    def test_description_order_matches_code(self):
        from core.funnel.tools_registry import tools

        decls = {d["name"]: d.get("description", "")
                 for d in tools.get_declarations()}
        desc = decls["play_music"]
        self.assertTrue(
            desc.startswith("Play music — starts actual playback."))
        self.assertIn(
            "YouTube Music in VYUK's browser first, then the "
            "Spotify app", desc)
        self.assertNotIn("Spotify app first", desc)
        self.assertIn(
            "never browser_control on YouTube/Spotify", desc)
        self.assertIn("Stops whatever is playing first", desc)


class TestOpenLocalHonesty(unittest.TestCase):
    """T11 (field check 2026-11-17): _open_local used to return
    True on Popen success alone, so play music claimed
    "Opened ... in your default player" while xdg-open exited
    3 and nothing opened. The opener's exit code is now the
    verdict: a fast non-zero exit is a failed open, a zero
    exit is an open, and a launcher that stays alive (it
    execed the player) is an open too."""

    def _run(self, fake):
        with mock.patch.object(spotify.subprocess, "Popen",
                               return_value=fake) as popen:
            return (spotify._open_local(pathlib.Path("/tmp/x.wav")),
                    popen)

    def test_failed_opener_is_not_an_open(self):
        opened, popen = self._run(_FakeProc(3))
        self.assertFalse(opened)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], "xdg-open")

    def test_successful_opener_is_an_open(self):
        opened, _ = self._run(_FakeProc(0))
        self.assertTrue(opened)

    def test_opener_still_running_counts_as_opened(self):
        # A launcher that execs the player stays alive; the
        # player did start, so this is an open, not a failure.
        opened, _ = self._run(_FakeProc(None))
        self.assertTrue(opened)

    def test_missing_opener_binary_is_not_an_open(self):
        with mock.patch.object(
                spotify.subprocess, "Popen",
                side_effect=FileNotFoundError("xdg-open")):
            self.assertFalse(
                spotify._open_local(pathlib.Path("/tmp/x.wav")))

    def test_local_rung_names_the_failed_player(self):
        with mock.patch.object(
                rungs, "_local_tracks",
                return_value=[pathlib.Path("/tmp/x.wav")]), \
                mock.patch.object(rungs, "_open_local",
                                  return_value=False):
            tried = []
            self.assertIsNone(rungs._local_rung("", tried))
            self.assertEqual(tried, ["the local player wouldn't open"])

    def test_local_rung_claims_open_only_when_opener_succeeds(self):
        with mock.patch.object(
                rungs, "_local_tracks",
                return_value=[pathlib.Path("/tmp/alpha.wav")]), \
                mock.patch.object(rungs, "_open_local",
                                  return_value=True), \
                mock.patch.object(rungs, "_playback_evidence",
                                  return_value=False), \
                mock.patch.object(rungs, "_settle"):
            tried = []
            out = rungs._local_rung("", tried)
            self.assertIn("Opened 'alpha.wav' in your default player",
                          out)
            self.assertEqual(tried, [])


class _FakeProc:
    """A Popen-shaped launcher: wait() returns the exit code,
    or raises TimeoutExpired when the launcher stays alive
    (it execed the player)."""

    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        if self.code is None:
            raise spotify.subprocess.TimeoutExpired("xdg-open",
                                                    timeout)
        return self.code


if __name__ == "__main__":
    unittest.main()
