"""B-D4 (2026-11-09) volume / mixer control.

Runs against a FAKE pycaw backend (in-memory sessions) — the
real pycaw backend is honestly unavailable on the Linux lab
(no pycaw in either interpreter; Windows-only marker), so the
logic is proved here and the real-Windows leg (real volumes
moving on his machine) is a field leg, owed, not this batch.
The engine-availability tests assert the honest degradation
on this machine instead of skipping it: a suite that silently
skips the volume checks on an interpreter without the library
is a lie, not a pass.
"""

import unittest

from core import skills_media_audio as sma
from core.desktop import volume_control as vc
from core.desktop.volume_control import SessionDead


class _FakeSession:
    """One fake audio session: the key IS the session object."""

    def __init__(self, pid, name, volume=0.5, muted=False,
                 state="active"):
        self.pid = pid
        self.name = name
        self.volume = volume
        self.muted = muted
        self.state = state
        self.alive = True


class _FakeBackend:
    """In-memory pycaw stand-in, same protocol as
    ``_PycawBackend`` (see volume_control's docstring). Flags
    simulate the device misbehaving: ``refuse_set`` makes a set
    silently not land (read-back mismatch); ``refuse_mute``
    the same for mute; ``die_on_set`` kills the session during
    the set (SessionDead); ``drop_after_list`` removes a session
    from the listing after the first list call (died between
    the driver's list and its set)."""

    def __init__(self, sessions, master_volume=0.5,
                 master_muted=False):
        self._sessions = list(sessions)
        self._master_volume = master_volume
        self._master_muted = master_muted
        self.refuse_set = False
        self.refuse_mute = False
        self.die_on_set = False
        self.drop_after_list = None
        self._lists = 0

    def sessions(self):
        self._lists += 1
        if self.drop_after_list is not None and self._lists > 1:
            doomed = self.drop_after_list
            self.drop_after_list = None
            doomed.alive = False
        return [{
            "key": s,
            "pid": s.pid,
            "name": s.name,
            "volume": s.volume,
            "muted": s.muted,
            "state": s.state,
        } for s in self._sessions if s.alive]

    def _check(self, key):
        if not key.alive:
            raise SessionDead("session is gone")
        if self.die_on_set:
            self.die_on_set = False
            key.alive = False
            raise SessionDead("session died during the set")

    def session_volume(self, key):
        self._check(key)
        return key.volume

    def session_muted(self, key):
        self._check(key)
        return key.muted

    def set_session_volume(self, key, fraction):
        self._check(key)
        if not self.refuse_set:
            key.volume = float(fraction)

    def set_session_mute(self, key, muted):
        self._check(key)
        if not self.refuse_mute:
            key.muted = bool(muted)

    def master(self):
        return {"volume": self._master_volume,
                "muted": self._master_muted}

    def set_master_volume(self, fraction):
        if not self.refuse_set:
            self._master_volume = float(fraction)

    def set_master_mute(self, muted):
        if not self.refuse_mute:
            self._master_muted = bool(muted)


def _backend():
    """Spotify + Chrome + a second Chrome session (same pid),
    master at 50%."""
    chrome_a = _FakeSession(100, "chrome.exe", 0.8, False)
    chrome_b = _FakeSession(100, "chrome.exe", 0.3, False)
    spotify = _FakeSession(200, "spotify.exe", 0.6, False)
    return _FakeBackend([chrome_a, chrome_b, spotify]), (
        chrome_a, chrome_b, spotify)


class TestListSessions(unittest.TestCase):
    def test_list_returns_sessions_with_pids(self):
        be, _ = _backend()
        result = vc.list_sessions(backend=be)
        self.assertTrue(result["ok"], result["reason"])
        by_pid = {s["pid"]: s for s in result["sessions"]}
        self.assertEqual(by_pid[200]["name"], "spotify.exe")
        self.assertEqual(by_pid[200]["volume"], 60)
        self.assertFalse(by_pid[200]["muted"])
        self.assertEqual(by_pid[200]["state"], "active")

    def test_list_session_index_within_pid(self):
        be, _ = _backend()
        result = vc.list_sessions(backend=be)
        chromes = [s for s in result["sessions"]
                   if s["pid"] == 100]
        self.assertEqual([s["session"] for s in chromes], [0, 1])
        self.assertEqual([s["volume"] for s in chromes], [80, 30])

    def test_engine_unavailable_without_a_backend(self):
        # The real backend on this Linux machine: pycaw absent.
        self.assertFalse(vc.engine_available())
        status = vc.engine_status()
        self.assertFalse(status["available"])
        self.assertIsNone(status["backend"])
        self.assertIn("Windows", status["error"])
        result = vc.list_sessions()
        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["reason"])


class TestSetAppVolume(unittest.TestCase):
    def test_set_by_pid_reads_back_actual(self):
        be, (_, _, spotify) = _backend()
        result = vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["volume"], 40)
        self.assertEqual(result["pid"], 200)
        self.assertEqual(spotify.volume, 0.4)

    def test_set_by_unique_name(self):
        be, (_, _, spotify) = _backend()
        result = vc.set_app_volume(name="spotify", level=25,
                                   backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["volume"], 25)
        self.assertEqual(spotify.volume, 0.25)

    def test_set_by_pid_and_session_index_picks_one(self):
        be, (chrome_a, chrome_b, _) = _backend()
        result = vc.set_app_volume(pid=100, session=1, level=10,
                                   backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(chrome_b.volume, 0.1)
        self.assertEqual(chrome_a.volume, 0.8)

    def test_other_apps_untouched_by_a_set(self):
        be, (chrome_a, chrome_b, spotify) = _backend()
        vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertEqual(chrome_a.volume, 0.8)
        self.assertEqual(chrome_b.volume, 0.3)
        self.assertEqual(spotify.volume, 0.4)

    def test_readback_mismatch_is_a_failure(self):
        be, (_, _, spotify) = _backend()
        be.refuse_set = True
        result = vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertFalse(result["ok"])
        self.assertEqual(result["volume"], 60)
        self.assertIn("40%", result["reason"])
        self.assertIn("60%", result["reason"])
        self.assertEqual(spotify.volume, 0.6)

    def test_dead_session_between_list_and_set(self):
        be, (_, _, spotify) = _backend()
        listed = vc.list_sessions(backend=be)
        self.assertTrue(listed["ok"])
        be.drop_after_list = spotify
        result = vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("200", result["reason"])
        self.assertEqual(spotify.volume, 0.6)

    def test_session_dying_during_the_set(self):
        be, (_, _, spotify) = _backend()
        be.die_on_set = True
        result = vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("stopped making sound", result["reason"])
        self.assertFalse(spotify.alive)

    def test_ambiguous_name_refuses_and_changes_nothing(self):
        be, (chrome_a, chrome_b, _) = _backend()
        result = vc.set_app_volume(name="chrome", level=10,
                                   backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("2", result["reason"])
        self.assertIn("100", result["reason"])
        self.assertEqual(chrome_a.volume, 0.8)
        self.assertEqual(chrome_b.volume, 0.3)

    def test_pid_with_two_sessions_needs_a_session_number(self):
        be, (chrome_a, chrome_b, _) = _backend()
        result = vc.set_app_volume(pid=100, level=10, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("2 audio sessions", result["reason"])
        self.assertEqual(chrome_a.volume, 0.8)
        self.assertEqual(chrome_b.volume, 0.3)

    def test_session_number_out_of_range(self):
        be, _ = _backend()
        result = vc.set_app_volume(pid=100, session=5, level=10,
                                   backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["reason"])

    def test_pid_and_name_disagreeing(self):
        be, _ = _backend()
        result = vc.set_app_volume(pid=200, name="chrome", level=10,
                                   backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("disagree", result["reason"])

    def test_no_match_name_names_running_apps(self):
        be, _ = _backend()
        result = vc.set_app_volume(name="vlc", level=10, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("vlc", result["reason"])
        self.assertIn("spotify.exe", result["reason"])

    def test_no_match_pid(self):
        be, _ = _backend()
        result = vc.set_app_volume(pid=999, level=10, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("999", result["reason"])

    def test_neither_pid_nor_name(self):
        be, _ = _backend()
        result = vc.set_app_volume(level=10, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("process id", result["reason"])

    def test_nothing_to_change(self):
        be, _ = _backend()
        result = vc.set_app_volume(pid=200, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("Nothing to change", result["reason"])


class TestOutOfRange(unittest.TestCase):
    def test_out_of_range_refused_never_clamped(self):
        be, (_, _, spotify) = _backend()
        for bad in (150, -5, 101, "40", 40.5, True):
            result = vc.set_app_volume(pid=200, level=bad,
                                       backend=be)
            self.assertFalse(result["ok"], bad)
            self.assertIn("between 0 and 100", result["reason"])
        self.assertEqual(spotify.volume, 0.6)

    def test_out_of_range_master_refused(self):
        be, _ = _backend()
        result = vc.set_master_volume(150, backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("between 0 and 100", result["reason"])
        self.assertEqual(be.master()["volume"], 0.5)


class TestMute(unittest.TestCase):
    def test_app_mute_reads_back(self):
        be, (_, _, spotify) = _backend()
        result = vc.app_mute(pid=200, mute=True, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertTrue(result["muted"])
        self.assertTrue(spotify.muted)
        result = vc.app_mute(pid=200, mute=False, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertFalse(result["muted"])

    def test_mute_refusal_is_a_failure(self):
        be, (_, _, spotify) = _backend()
        be.refuse_mute = True
        result = vc.app_mute(pid=200, mute=True, backend=be)
        self.assertFalse(result["ok"])
        self.assertFalse(result["muted"])
        self.assertIn("unmuted", result["reason"])
        self.assertFalse(spotify.muted)

    def test_mute_must_be_bool(self):
        be, _ = _backend()
        result = vc.app_mute(pid=200, mute="yes", backend=be)
        self.assertFalse(result["ok"])
        self.assertIn("true or false", result["reason"])


class TestMasterSeparation(unittest.TestCase):
    def test_master_set_does_not_touch_apps(self):
        be, (chrome_a, chrome_b, spotify) = _backend()
        result = vc.set_master_volume(40, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["volume"], 40)
        self.assertEqual(chrome_a.volume, 0.8)
        self.assertEqual(chrome_b.volume, 0.3)
        self.assertEqual(spotify.volume, 0.6)

    def test_app_set_does_not_touch_master(self):
        be, _ = _backend()
        result = vc.set_app_volume(pid=200, level=40, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(be.master()["volume"], 0.5)

    def test_master_mute_does_not_mute_apps(self):
        be, (_, _, spotify) = _backend()
        result = vc.master_mute(True, backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertTrue(result["muted"])
        self.assertFalse(spotify.muted)

    def test_master_readback_mismatch_is_a_failure(self):
        be, _ = _backend()
        be.refuse_set = True
        result = vc.set_master_volume(40, backend=be)
        self.assertFalse(result["ok"])
        self.assertEqual(result["volume"], 50)
        self.assertIn("40%", result["reason"])
        self.assertIn("50%", result["reason"])

    def test_master_volume_read(self):
        be, _ = _backend()
        result = vc.master_volume(backend=be)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["volume"], 50)
        self.assertFalse(result["muted"])


class TestAppVolumeTool(unittest.TestCase):
    """The REWIRED app_volume tool (B-D4r): the same fake backend
    is injected through the tool's test seam, so the tool's prose
    replies are proved against the wrapper's honest verdicts —
    the real-Windows leg (a real "turn Spotify down" on his
    machine) is a field leg, owed, not this batch."""

    def setUp(self):
        self.be, self.sessions = _backend()
        sma.set_volume_backend(self.be)

    def tearDown(self):
        sma.set_volume_backend(None)

    def test_tool_ambiguous_name_refuses_with_candidates(self):
        chrome_a, chrome_b, _ = self.sessions
        reply = sma.app_volume("chrome", level=40)
        self.assertNotIn("Per-app volume set", reply)
        self.assertIn("matches 2 apps", reply)
        self.assertIn("pid 100", reply)
        self.assertEqual(chrome_a.volume, 0.8)
        self.assertEqual(chrome_b.volume, 0.3)

    def test_tool_single_match_sets_and_reports_readback(self):
        _, _, spotify = self.sessions
        reply = sma.app_volume("spotify", level=40)
        self.assertEqual(
            reply, "Per-app volume set — spotify.exe: volume 40%.")
        self.assertEqual(spotify.volume, 0.4)

    def test_tool_readback_mismatch_reports_failure(self):
        _, _, spotify = self.sessions
        self.be.refuse_set = True
        reply = sma.app_volume("spotify", level=40)
        self.assertNotIn("Per-app volume set", reply)
        self.assertIn("40%", reply)
        self.assertIn("60%", reply)
        self.assertEqual(spotify.volume, 0.6)

    def test_tool_no_match_names_apps_playing(self):
        reply = sma.app_volume("vlc", level=40)
        self.assertIn("No app matching 'vlc'", reply)
        self.assertIn("spotify.exe", reply)
        self.assertIn("chrome.exe", reply)

    def test_tool_list_mode_lists(self):
        reply = sma.app_volume()
        self.assertEqual(
            reply,
            "Per-app volume right now:\n"
            "• chrome.exe: 80%\n"
            "• chrome.exe: 30%\n"
            "• spotify.exe: 60%")

    def test_tool_out_of_range_refused(self):
        _, _, spotify = self.sessions
        for bad in (150, -5):
            reply = sma.app_volume("spotify", level=bad)
            self.assertIn("between 0 and 100", reply, bad)
            self.assertNotIn("Per-app volume set", reply)
        self.assertEqual(spotify.volume, 0.6)

    def test_tool_mute_reports_readback(self):
        _, _, spotify = self.sessions
        reply = sma.app_volume("spotify", mute=True)
        self.assertEqual(
            reply, "Per-app volume set — spotify.exe: muted.")
        self.assertTrue(spotify.muted)
        reply = sma.app_volume("spotify", mute=False)
        self.assertEqual(
            reply, "Per-app volume set — spotify.exe: unmuted.")
        self.assertFalse(spotify.muted)

    def test_tool_read_mode_single(self):
        self.assertEqual(sma.app_volume("spotify"),
                         "spotify.exe is at 60%.")

    def test_tool_read_mode_ambiguous_refuses(self):
        reply = sma.app_volume("chrome")
        self.assertIn("matches 2 apps", reply)
        self.assertNotIn("is at", reply)

    def test_tool_read_mode_no_match(self):
        reply = sma.app_volume("vlc")
        self.assertIn("No app matching 'vlc'", reply)
        self.assertIn("spotify.exe", reply)

    def test_tool_list_mode_with_level_names_one_app(self):
        reply = sma.app_volume(level=40)
        self.assertIn("Name one app to change its volume", reply)

    def test_tool_session_dead_names_unconfirmed(self):
        _, _, spotify = self.sessions
        self.be.die_on_set = True
        reply = sma.app_volume("spotify", level=40)
        self.assertIn("could be confirmed", reply)
        self.assertNotIn("nothing was changed", reply)
        self.assertFalse(spotify.alive)

    def test_tool_non_windows_gate(self):
        # No backend injected: on this Linux machine the tool's
        # own gate answers, before the wrapper is ever called.
        sma.set_volume_backend(None)
        reply = sma.app_volume("spotify", level=40)
        self.assertEqual(
            reply,
            "Per-app volume uses Windows audio sessions — "
            "this system is not Windows, so nothing was changed.")


if __name__ == "__main__":
    unittest.main()
