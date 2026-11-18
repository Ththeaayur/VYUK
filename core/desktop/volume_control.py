"""B-D4 (2026-11-09) volume / mixer control — the ONE dict-returning
wrapper a driver calls for volume actions.

THE GAP (proved first, file/line evidence)
------------------------------------------
Master volume and per-app volume ALREADY exist in the tree as
human-string tools, and B-D4 builds only the remainder:

* Master: ``set_volume_level`` — core/desktop/
  control_plus_volume_network.py:33 (pycaw endpoint, read-back,
  clamp disclosed) — tool at core/desktop/control_plus.py:223.
* Per-app: ``app_volume`` — core/skills_media_audio.py:204 (name
  match, set with read-back) — tool at core/skills_media.py:254.
* Microphone: ``set_microphone_mute`` — core/skills_media_audio.py:160.

What does NOT exist, and is the remainder this module builds:

1. NO dict-returning wrapper a driver can call. The existing
   functions return human sentences ("Per-app volume set —
   chrome.exe: volume 40%."); a driver cannot tell success from
   failure without parsing prose. Every function here returns a
   dict with ok / volume / muted / reason.
2. NO session identity. ``app_volume`` matches by process-name
   SUBSTRING (``needle in stem``, skills_media_audio.py:232-236)
   and then changes EVERY match (the ``for session in matches``
   loop at :247) — two chrome.exe sessions, or two apps sharing
   a stem, are changed together, silently. Its list shows no
   process id, so nothing can disambiguate. Here a session is
   (process id + session index); a name is a search hint only,
   and an ambiguous name REFUSES, listing candidates.
3. NO read-back COMPARISON in ``app_volume``. It reports the
   actual level but never flags a mismatch as failure — asked
   40%, reads back 80%, it still says "Per-app volume set —
   ...: volume 80%." Here a mismatch IS the failure.
4. NO ambiguous-name refusal anywhere in the tree.

The existing tools are NOT changed by this batch (their
behaviour is theirs; a rewire is a separate decision for the
parent). This wrapper is the new driver surface; the tools keep
working as they are. Fix 1's music single-source guard decides
WHICH source plays; this wrapper decides HOW LOUD — the
boundary is: this module never starts, stops, or picks a track.

LOPHOLE FAMILY — "a volume command that changes the wrong sound"
-----------------------------------------------------------------
* A name that matches two apps changes the wrong one (or both).
* A session that dies between list and set reports success.
* A set the device refuses (or half-applies) reports success.
* Master and app volumes crossed (a master call mutes an app).
* An out-of-range value passed through (or silently clamped).
* A no-match app silently does nothing.
* A mute that doesn't stick reports muted.

ONE design closes the class: every volume action goes through
this module; a session is identified by (pid + session index),
never by name alone; every set reads the value back and compares
it to what was asked (a mismatch is an honest failure, never a
success claim); master and app are SEPARATE functions on
SEPARATE backend methods that never share a code path; an
out-of-range level is REFUSED with the reason, never clamped;
a no-match, dead, or ambiguous session returns an honest
failure, never a silent no-op.

Rejected alternatives (recorded):
* Process-name-only identity — two apps share a name; the wrong
  one changes. (What the existing ``app_volume`` tool does.)
* Fire-and-forget sets — no read-back means a failed set
  reports success. (What ``adjust_volume``'s media keys do.)
* Silent clamping — a 150% request quietly becomes 100%.
  (``set_volume_level`` clamps but DISCLOSES it; this wrapper
  refuses instead, so a driver never has to guess.)
* One call for master-or-app — a master call that sometimes
  means an app is a confusion waiting to happen.
* Silent no-match — the user asked for an app that isn't
  playing; say so, with the apps that ARE playing.

DEPENDENCY (no new one — pycaw is already declared)
---------------------------------------------------
pycaw is a declared Windows-only dependency already:
requirements.txt:54 (``pycaw>=20230407; sys_platform ==
'win32'``) and THIRD_PARTY_LICENSES.md:58 (MIT, read from the
20260927 PyPI wheel, 2026-10-24; lock pin still owed from the
Windows build machine). Its transitive deps are comtypes
(requirements.txt:60, THIRD_PARTY:40, MIT) and psutil
(requirements.txt:40, lock 7.2.2, THIRD_PARTY:54, BSD-3) —
both already declared; no GPL anywhere in the set. Weight,
measured 2026-11-09 from the PyPI wheels (pycaw is not
installed in either Linux interpreter — Windows-only marker):
pycaw 20260927 wheel 28,463 B / 156 KB unpacked; comtypes 1.4.17
wheel 298,743 B / 1.4 MB unpacked; psutil already in the tree.
Total new weight on Windows ≈ 1.6 MB — no weight concern, and
nothing new to declare.

The engine is pycaw itself (real Core Audio sessions), never a
home-grown ctypes WASAPI wrapper. On non-Windows, or when
pycaw is absent, every call degrades honestly to
engine_unavailable — the Linux lab runs the logic against an
injected fake backend (see the backend protocol below); the
real-Windows leg (real volumes moving on his machine) is a
field leg, owed, not this batch.

BACKEND PROTOCOL (what a backend must provide)
----------------------------------------------
A backend is any object with these methods; the real one is
``_PycawBackend`` (pycaw, lazy-imported), the tests and the
prover inject a fake with the same shape:

* ``sessions()`` -> list of dicts, one per session that has a
  volume interface: {"key": opaque handle, "pid": int,
  "name": str, "volume": float 0.0-1.0, "muted": bool,
  "state": str ("active"/"inactive"/"expired"/...)}.
* ``session_volume(key)`` -> float 0.0-1.0 (read-back).
* ``session_muted(key)`` -> bool (read-back).
* ``set_session_volume(key, fraction)`` -> None.
* ``set_session_mute(key, muted)`` -> None.
* ``master()`` -> {"volume": float 0.0-1.0, "muted": bool}.
* ``set_master_volume(fraction)`` -> None.
* ``set_master_mute(muted)`` -> None.

A backend raises ``SessionDead`` when the session it is asked
about no longer exists; any other backend failure propagates
as the exception it is, and the wrapper turns it into an
honest failure dict — never a success claim.

Public functions take an optional ``backend`` (None = the real
pycaw backend). An explicitly injected backend IS the engine
under test, so the platform/pycaw availability check is skipped
for it — the suite and the prover run the logic on the fake
while the real backend is honestly unavailable on this machine.
"""

from __future__ import annotations

import sys

SUPPORTED = ("app", "master")

# A set that lands within one point of the request counts as
# landed (pycaw scalars are floats; the public API is integer
# percent). Anything further off is a mismatch, i.e. a failure.
_READBACK_TOLERANCE_PCT = 1


class _EngineUnavailable(Exception):
    """pycaw (or Windows) is not available for real volume work."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class SessionDead(Exception):
    """The audio session a call targeted no longer exists.

    Raised by a backend that KNOWS the session died (the fake
    backend raises it explicitly; the real pycaw backend
    surfaces the COM failure verbatim instead — see
    ``_PycawBackend``).
    """


# ----------------------------------------------------------------
# Engine availability (real backend only)
# ----------------------------------------------------------------

def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _real_backend():
    """The real pycaw backend, or raise _EngineUnavailable
    naming what is missing. pycaw is imported lazily, here —
    importing THIS module never touches it (the sympy pattern)."""
    if not _is_windows():
        raise _EngineUnavailable(
            "volume control needs Windows audio endpoints (pycaw) — "
            "this system is not Windows")
    try:
        import pycaw  # noqa: F401 — the import IS the probe
    except ImportError as e:
        raise _EngineUnavailable(
            "pycaw is not available in this build of VYUK — "
            f"the volume part didn't make it into the package ({e})"
        ) from e
    return _PycawBackend()


def engine_available() -> bool:
    """True when the real pycaw backend can be built (Windows +
    pycaw importable). False on the Linux lab — and that is an
    honest answer, not a skip."""
    try:
        _real_backend()
    except _EngineUnavailable:
        return False
    return True


def engine_status() -> dict:
    """Honest engine report: {"available", "backend", "error"}."""
    try:
        _real_backend()
    except _EngineUnavailable as e:
        return {"available": False, "backend": None,
                "error": e.detail}
    return {"available": True, "backend": "pycaw", "error": None}


# ----------------------------------------------------------------
# The real backend — pycaw, and nothing else
# ----------------------------------------------------------------

class _PycawBackend:
    """Core Audio sessions + the master endpoint, via pycaw.

    Master and app go through SEPARATE pycaw calls that never
    share a code path: the master endpoint comes from
    AudioUtilities.GetSpeakers(); app sessions come from
    AudioUtilities.GetAllSessions(). (The existing
    ``set_volume_level`` tool keeps its own endpoint seam in
    core/desktop/control_plus_volume_network.py; this backend
    carries its own so the driver surface is self-contained —
    the tool path is untouched.)
    """

    def sessions(self) -> list:
        from pycaw.pycaw import AudioUtilities

        out = []
        for session in AudioUtilities.GetAllSessions():
            vol = getattr(session, "SimpleAudioVolume", None)
            if vol is None:
                continue
            process = getattr(session, "Process", None)
            pid = None
            name = "System sounds"
            if process is not None:
                try:
                    pid = int(process.pid)
                except Exception:  # noqa: BLE001
                    pid = None
                try:
                    name = process.name()
                except Exception:  # noqa: BLE001
                    name = "Unknown app"
            try:
                state = str(session.State)
            except Exception:  # noqa: BLE001
                state = "unknown"
            out.append({
                "key": session,
                "pid": pid,
                "name": name,
                "volume": float(vol.GetMasterVolume()),
                "muted": bool(vol.GetMute()),
                "state": state,
            })
        return out

    @staticmethod
    def _session_volume(session) -> float:
        return float(session.SimpleAudioVolume.GetMasterVolume())

    def session_volume(self, key) -> float:
        return self._session_volume(key)

    def session_muted(self, key) -> bool:
        return bool(key.SimpleAudioVolume.GetMute())

    def set_session_volume(self, key, fraction: float) -> None:
        key.SimpleAudioVolume.SetMasterVolume(float(fraction), None)

    def set_session_mute(self, key, muted: bool) -> None:
        key.SimpleAudioVolume.SetMute(1 if muted else 0, None)

    @staticmethod
    def _master_endpoint():
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def master(self) -> dict:
        endpoint = self._master_endpoint()
        return {
            "volume": float(endpoint.GetMasterVolumeLevelScalar()),
            "muted": bool(endpoint.GetMute()),
        }

    def set_master_volume(self, fraction: float) -> None:
        endpoint = self._master_endpoint()
        endpoint.SetMasterVolumeLevelScalar(float(fraction), None)

    def set_master_mute(self, muted: bool) -> None:
        endpoint = self._master_endpoint()
        endpoint.SetMute(1 if muted else 0, None)


# ----------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------

def _backend_or_real(backend):
    """An injected backend is used as-is; None builds the real
    one (and raises _EngineUnavailable when it cannot)."""
    if backend is not None:
        return backend
    return _real_backend()


def _validate_level(level):
    """A level must be a whole number 0-100. Anything else is
    REFUSED with the reason — never clamped, never coerced.
    (Booleans are ints in Python; True/False are refused too.)"""
    if isinstance(level, bool) or not isinstance(level, int):
        return None, (
            "Volume level must be a whole number between 0 and 100 "
            f"(got {level!r}).")
    if not 0 <= level <= 100:
        return None, (
            "Volume level must be between 0 and 100 "
            f"(got {level}).")
    return level, None


def _pct(fraction: float) -> int:
    """A 0.0-1.0 scalar as integer percent."""
    return int(round(float(fraction) * 100))


def _stem(name: str) -> str:
    """'chrome.exe' -> 'chrome'; 'System sounds' unchanged."""
    lowered = (name or "").strip().lower()
    return lowered[:-4] if lowered.endswith(".exe") else lowered


def _name_matches(needle: str, name: str) -> bool:
    """The same matching the existing app_volume tool uses
    (exact name, stem, or substring of the stem) — so a driver
    that learned names from that tool keeps working. A name is
    a SEARCH HINT: it never identifies a session on its own."""
    needle = (needle or "").strip().lower()
    label = (name or "").strip().lower()
    stem = _stem(name)
    return needle == label or needle == stem or needle in stem


def _resolve(sessions: list, pid, name, session_index):
    """Pick ONE session from a listing, or return (None, reason).

    Identity is (pid + session index). A name alone is a hint:
    zero matches is a no-match failure, more than one is an
    ambiguous failure listing the candidates — never a silent
    pick of the first, never a change to all of them.
    ``session_index`` is the position among THAT pid's sessions
    in this listing (None = not specified); it is only stable
    within one list-then-set step, which is how a driver uses
    it — list, pick, set, immediately.
    """
    if pid is None and (name is None or not str(name).strip()):
        return None, ("Name one app or give a process id — "
                      "a volume change needs a session to target.")
    if pid is not None:
        if isinstance(pid, bool):
            return None, f"Process id must be a whole number (got {pid!r})."
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None, f"Process id must be a whole number (got {pid!r})."
        candidates = [s for s in sessions if s.get("pid") == pid]
        if name is not None and str(name).strip():
            named = [s for s in candidates
                     if _name_matches(str(name), s.get("name", ""))]
            if not named:
                return None, (
                    f"No audio session for process {pid} matches "
                    f"'{name}' — the process id and the name disagree.")
            candidates = named
        if not candidates:
            return None, (
                f"No app with process id {pid} is running with sound.")
        if len(candidates) == 1:
            return candidates[0], None
        if session_index is None:
            lines = ", ".join(
                f"{c.get('name')} (session {i}, "
                f"{_pct(c.get('volume', 0))}%, "
                f"{'muted' if c.get('muted') else 'live'})"
                for i, c in enumerate(candidates))
            return None, (
                f"Process {pid} has {len(candidates)} audio sessions — "
                f"say which one by session number: {lines}.")
        try:
            idx = int(session_index)
        except (TypeError, ValueError):
            return None, (
                f"Session number must be a whole number "
                f"(got {session_index!r}).")
        if isinstance(session_index, bool) or not 0 <= idx < len(candidates):
            return None, (
                f"Process {pid} has {len(candidates)} audio session(s) — "
                f"session number {session_index} does not exist.")
        return candidates[idx], None
    # Name only.
    matches = [s for s in sessions
               if _name_matches(str(name), s.get("name", ""))]
    if not matches:
        running = ", ".join(sorted({str(s.get("name")) for s in sessions}))
        return None, (
            f"No app matching '{name}' is running with sound. "
            f"Apps with audio right now: {running}.")
    if len(matches) == 1:
        return matches[0], None
    lines = ", ".join(
        f"{s.get('name')} (pid {s.get('pid')}, "
        f"{_pct(s.get('volume', 0))}%, "
        f"{'muted' if s.get('muted') else 'live'})"
        for s in matches)
    return None, (
        f"'{name}' matches {len(matches)} apps with sound — "
        f"say which one by process id: {lines}.")


def _session_public(entry: dict, index: int) -> dict:
    """One listing entry as the driver sees it: pid, name,
    volume, muted, state, and the session index within its pid."""
    return {
        "pid": entry.get("pid"),
        "name": entry.get("name"),
        "volume": _pct(entry.get("volume", 0)),
        "muted": bool(entry.get("muted")),
        "state": entry.get("state", "unknown"),
        "session": index,
    }


# ----------------------------------------------------------------
# App sessions
# ----------------------------------------------------------------

def list_sessions(backend=None) -> dict:
    """Every app that can make sound, with its process id.

    Returns {"ok", "sessions": [{pid, name, volume, muted,
    state, session}], "reason"}. ``session`` is the index of
    the session among its pid's sessions in THIS listing — the
    handle a set call uses together with the pid.
    """
    try:
        be = _backend_or_real(backend)
        raw = be.sessions()
    except _EngineUnavailable as e:
        return {"ok": False, "sessions": [], "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "sessions": [],
                "reason": f"Could not read the running audio sessions: {e}"}
    per_pid: dict = {}
    sessions = []
    for entry in raw:
        pid = entry.get("pid")
        index = per_pid.get(pid, 0)
        per_pid[pid] = index + 1
        sessions.append(_session_public(entry, index))
    return {"ok": True, "sessions": sessions, "reason": ""}


def set_app_volume(*, pid=None, name=None, session=None, level=None,
                   mute=None, backend=None) -> dict:
    """Set ONE app's volume and/or mute, by session identity.

    Identity is (pid + session index from ``list_sessions``);
    a name alone is a hint that must resolve to exactly one
    session. ``level`` is integer percent 0-100 (refused
    outside it, never clamped); ``mute`` is a bool. At least
    one of level/mute is required. The set is READ BACK and
    compared: a mismatch is an honest failure naming what was
    asked and what the app reads back. A session that died
    between list and set fails honestly — nothing is claimed
    changed that did not change.

    Returns {"ok", "pid", "name", "volume" (actual read-back),
    "muted" (actual read-back), "reason"}.
    """
    if level is None and mute is None:
        return {"ok": False, "pid": pid, "name": name, "volume": None,
                "muted": None,
                "reason": "Nothing to change — give a level (0-100) "
                          "and/or a mute state."}
    wanted_level, err = (None, None)
    if level is not None:
        wanted_level, err = _validate_level(level)
        if err:
            return {"ok": False, "pid": pid, "name": name,
                    "volume": None, "muted": None, "reason": err}
    if mute is not None and not isinstance(mute, bool):
        return {"ok": False, "pid": pid, "name": name, "volume": None,
                "muted": None,
                "reason": f"Mute must be true or false (got {mute!r})."}
    try:
        be = _backend_or_real(backend)
        raw = be.sessions()
    except _EngineUnavailable as e:
        return {"ok": False, "pid": pid, "name": name, "volume": None,
                "muted": None, "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "pid": pid, "name": name, "volume": None,
                "muted": None,
                "reason": f"Could not read the running audio sessions: {e}"}
    target, err = _resolve(raw, pid, name, session)
    if err:
        return {"ok": False, "pid": pid, "name": name, "volume": None,
                "muted": None, "reason": err}
    key = target["key"]
    try:
        if wanted_level is not None:
            be.set_session_volume(key, wanted_level / 100.0)
        if mute is not None:
            be.set_session_mute(key, mute)
        now_level = _pct(be.session_volume(key))
        now_muted = bool(be.session_muted(key))
    except SessionDead:
        return {"ok": False, "pid": target.get("pid"),
                "name": target.get("name"), "volume": None,
                "muted": None,
                "reason": f"{target.get('name')} (pid {target.get('pid')}) "
                          "stopped making sound before the change "
                          "could be confirmed."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "pid": target.get("pid"),
                "name": target.get("name"), "volume": None,
                "muted": None,
                "reason": f"Could not change {target.get('name')}'s "
                          f"volume: {e}"}
    if wanted_level is not None and abs(now_level - wanted_level) > _READBACK_TOLERANCE_PCT:
        return {"ok": False, "pid": target.get("pid"),
                "name": target.get("name"), "volume": now_level,
                "muted": now_muted,
                "reason": f"Asked for {wanted_level}% but "
                          f"{target.get('name')} reads back "
                          f"{now_level}% — the change did not land."}
    if mute is not None and now_muted != mute:
        return {"ok": False, "pid": target.get("pid"),
                "name": target.get("name"), "volume": now_level,
                "muted": now_muted,
                "reason": f"Asked for {'muted' if mute else 'unmuted'} "
                          f"but {target.get('name')} reads back "
                          f"{'muted' if now_muted else 'unmuted'} — "
                          "the change did not land."}
    return {"ok": True, "pid": target.get("pid"),
            "name": target.get("name"), "volume": now_level,
            "muted": now_muted, "reason": ""}


def app_mute(*, pid=None, name=None, session=None, mute, backend=None) -> dict:
    """Mute / unmute ONE app, by session identity. A thin
    convenience over ``set_app_volume`` — same identity, same
    read-back comparison, same honest failures."""
    return set_app_volume(pid=pid, name=name, session=session,
                          level=None, mute=mute, backend=backend)


# ----------------------------------------------------------------
# Master volume — a SEPARATE path that never touches sessions
# ----------------------------------------------------------------

def master_volume(backend=None) -> dict:
    """The master (speaker) volume and mute state.

    Returns {"ok", "volume", "muted", "reason"}. This reads the
    endpoint only — it never lists or touches app sessions.
    """
    try:
        be = _backend_or_real(backend)
        current = be.master()
    except _EngineUnavailable as e:
        return {"ok": False, "volume": None, "muted": None,
                "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "volume": None, "muted": None,
                "reason": f"Could not read the master volume: {e}"}
    return {"ok": True, "volume": _pct(current["volume"]),
            "muted": bool(current["muted"]), "reason": ""}


def set_master_volume(level, backend=None) -> dict:
    """Set the master volume to an exact percent (0-100).

    Refused outside 0-100, never clamped. The set is READ BACK
    and compared — a mismatch is an honest failure. This touches
    the endpoint only; no app session is read or changed.
    """
    wanted, err = _validate_level(level)
    if err:
        return {"ok": False, "volume": None, "muted": None,
                "reason": err}
    try:
        be = _backend_or_real(backend)
        be.set_master_volume(wanted / 100.0)
        current = be.master()
    except _EngineUnavailable as e:
        return {"ok": False, "volume": None, "muted": None,
                "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "volume": None, "muted": None,
                "reason": f"Could not set the master volume: {e}"}
    now_level = _pct(current["volume"])
    now_muted = bool(current["muted"])
    if abs(now_level - wanted) > _READBACK_TOLERANCE_PCT:
        return {"ok": False, "volume": now_level, "muted": now_muted,
                "reason": f"Asked for {wanted}% master volume but the "
                          f"speakers read back {now_level}% — the "
                          "change did not land."}
    return {"ok": True, "volume": now_level, "muted": now_muted,
            "reason": ""}


def master_mute(mute, backend=None) -> dict:
    """Mute / unmute the master (speakers). Endpoint only — no
    app session is read or changed."""
    if not isinstance(mute, bool):
        return {"ok": False, "volume": None, "muted": None,
                "reason": f"Mute must be true or false (got {mute!r})."}
    try:
        be = _backend_or_real(backend)
        be.set_master_mute(mute)
        current = be.master()
    except _EngineUnavailable as e:
        return {"ok": False, "volume": None, "muted": None,
                "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "volume": None, "muted": None,
                "reason": f"Could not change the master mute: {e}"}
    now_level = _pct(current["volume"])
    now_muted = bool(current["muted"])
    if now_muted != mute:
        return {"ok": False, "volume": now_level, "muted": now_muted,
                "reason": f"Asked for master "
                          f"{'muted' if mute else 'unmuted'} but the "
                          f"speakers read back "
                          f"{'muted' if now_muted else 'unmuted'} — "
                          "the change did not land."}
    return {"ok": True, "volume": now_level, "muted": now_muted,
            "reason": ""}
