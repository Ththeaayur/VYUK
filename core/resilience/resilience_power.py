"""RL-3.x power-survival kit + RL-7.3/7.6/7.8 gap helpers (wave 25, §5.18).

Stdlib only, except the battery probe, which lazily reuses psutil —
the tree already depends on psutil (requirements.txt), so this is a
reuse of an existing dependency, not a new one. When psutil is absent
or reports no battery, the probe says so honestly; it never invents a
reading. DiskCache is NOT used and NOT depended on: the checkpoint
store below is native sqlite3 (the DiskCache upstream LICENSE-file read
stays owed for any future pilot; its licence conflict blocks ship, not
pilot, and this build ships no pilot).

RL-3.5's ``classify_wake`` lives in ``resilience_clock`` (the dual-clock
classifier is clock discipline, RL-7.1); this module's docstring points
there rather than duplicating the classifier. RL-3.2 Litestream shadow
copy is a DOCUMENTED PATTERN ONLY (see the note below): the daemon is
NOT piloted and its Windows support status stays owed.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time

from .. import store_base

CRASH_COUNTER_FILENAME = "rl_crashes.json"
CHECKPOINT_FILENAME = "rl_checkpoints.sqlite3"
SAFE_MODE_FLAG_FILENAME = "safe_mode.flag"
CRASH_LOG_FILENAME = "rl_crash_loop.log"

# RL-3.2 pattern note (NOT an implementation, NOT a daemon pilot):
# a rolling shadow copy of a SQLite database = periodic SQLite backup
# API copies to a second file on the same disk (see resilience_state's
# snapshot helper), so a torn primary still leaves a recent copy. The
# Litestream daemon itself is not piloted here; its Windows support
# status stays owed.
LITESTREAM_SHADOW_NOTE = {
    "pattern": "rolling SQLite backup-API copies to a shadow file",
    "daemon": "NOT piloted (Litestream Windows status owed)",
    "implementation_in_this_build": "resilience_state.create_snapshot only",
}

ONE_GIBIBYTE = 1 << 30


def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")


# ---------------------------------------------------------------------
# RL-3.1 atomic writes (generalised .env pattern, not a second writer)
# ---------------------------------------------------------------------

def atomic_write(path: str, data) -> str:
    """Write ``data`` (bytes or str) to ``path`` atomically.

    The v5.22 .env pattern, generalised: temp file in the SAME directory,
    flush + fsync, rename over the target, best-effort directory fsync.
    A crash mid-write leaves the old file intact, never a torn one. No
    temp file is left behind on success; on failure the temp is removed
    best-effort. This is not a .env writer — it takes no key/value
    semantics and never touches .env's own writer.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".tmp-%d-%s"
                       % (os.getpid(), os.path.basename(path)))
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------
# RL-3.6 single-instance OS lock (native; no portalocker dep)
# ---------------------------------------------------------------------

class SingleInstanceLock:
    """An OS-held single-instance lock (no portalocker dependency).

    msvcrt.locking on Windows, fcntl.flock elsewhere, non-blocking. The
    lock is held by the OS, so a power cut releases it with the process
    — strictly safer than tendo SingleInstance, whose stale lock files
    survive power cuts (EXCLUDED for exactly that reason). The lock file
    itself is never deleted while held.
    """

    def __init__(self, lock_path: str):
        self.lock_path = str(lock_path)
        self._fd = None
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return True
        directory = os.path.dirname(os.path.abspath(self.lock_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            os.close(fd)
            return False
        self._fd = fd
        self._held = True
        return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        self._held = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("single-instance lock already held: %s"
                               % self.lock_path)
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ---------------------------------------------------------------------
# RL-3.7 crash-loop detection + safe-mode trigger (Wave 10 mechanism)
# ---------------------------------------------------------------------

class CrashCounter:
    """Persistent crash counter in its OWN file, with decay.

    The counter file holds a JSON list of crash timestamps (wall clock;
    crash timestamps are "run at" records, so wall clock is the honest
    choice — see resilience_clock). Entries older than ``decay_window_s``
    are pruned on every read. Only confident "crash" verdicts from the
    dual-clock classifier (resilience_clock) should be recorded: a "sleep"
    misrecorded as a crash would let safe mode cry wolf.
    """

    def __init__(self, counter_path: str,
                 decay_window_s: float = 7 * 86400.0):
        self.counter_path = str(counter_path)
        self.decay_window_s = float(decay_window_s)

    def _load(self) -> list:
        try:
            with open(self.counter_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [float(t) for t in data]
        except (OSError, ValueError):
            pass
        return []

    def _save(self, stamps: list) -> None:
        atomic_write(self.counter_path, json.dumps(stamps))

    def record_crash(self, now: float | None = None) -> int:
        """Record one crash; returns the recent count after decay."""
        stamp = time.time() if now is None else float(now)
        stamps = [t for t in self._load()
                  if stamp - t <= self.decay_window_s]
        stamps.append(stamp)
        self._save(stamps)
        return len(stamps)

    def count_recent(self, now: float | None = None) -> int:
        stamp = time.time() if now is None else float(now)
        return len([t for t in self._load()
                    if stamp - t <= self.decay_window_s])

    def should_enter_safe_mode(self, threshold: int = 3,
                               now: float | None = None) -> bool:
        """True when recent crashes reach ``threshold`` (default 3)."""
        return self.count_recent(now) >= threshold


def engage_safe_mode(data_dir: str, reason: str, log_text: str = "") -> dict:
    """Engage Wave 10 safe mode for a crash loop, with the log attached.

    Writes the ``safe_mode.flag`` file the existing Wave 10 reader
    (``safemode.is_safe_mode``) enables on by mere presence, plus
    a crash-loop log file beside it. Presence-based flagging means a
    truncated write cannot silently disable it. Returns the flag and
    log paths. This engages the flag; the coordinator reads it at
    startup (later wiring restarts nothing from here).
    """
    os.makedirs(data_dir, exist_ok=True)
    flag_path = os.path.join(data_dir, SAFE_MODE_FLAG_FILENAME)
    log_path = os.path.join(data_dir, CRASH_LOG_FILENAME)
    atomic_write(flag_path, "crash-loop: %s\n" % reason)
    entry = "[%s] crash-loop safe-mode trigger: %s\n%s\n" % (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reason, log_text)
    try:
        with open(log_path, encoding="utf-8") as handle:
            previous = handle.read()
    except OSError:
        previous = ""
    atomic_write(log_path, previous + entry)
    return {"engaged": True, "flag": flag_path, "log": log_path,
            "reason": reason}


def is_safe_mode_engaged(data_dir: str) -> bool:
    """Mirror of the Wave 10 flag-file contract (presence enables)."""
    return os.path.isfile(os.path.join(data_dir, SAFE_MODE_FLAG_FILENAME))


# ---------------------------------------------------------------------
# RL-3.3 checkpoint / "where was I" store (native sqlite3, own file)
# ---------------------------------------------------------------------

class CheckpointStore:
    """A native checkpoint store in its OWN file (INV-8). JSON only."""

    def __init__(self, db_path: str):
        store_base.refuse_assistant_db(db_path, what="checkpoint store")
        self.db_path = str(db_path)
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            _apply_pragmas(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "key TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "updated_at REAL NOT NULL)")
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        _apply_pragmas(con)
        return con

    def save_checkpoint(self, key: str, state: dict) -> None:
        try:
            blob = json.dumps(state)
        except (TypeError, ValueError) as exc:
            raise TypeError("checkpoint state must be JSON-serialisable"
                            ) from exc
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO checkpoints (key, state, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "state = excluded.state, updated_at = excluded.updated_at",
                (key, blob, time.time()))
            con.commit()
        finally:
            con.close()

    def load_checkpoint(self, key: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT state FROM checkpoints WHERE key = ?",
                (key,)).fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else None

    def list_checkpoints(self) -> list:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT key, updated_at FROM checkpoints ORDER BY key"
                ).fetchall()
        finally:
            con.close()
        return [{"key": r[0], "updated_at": r[1]} for r in rows]


# ---------------------------------------------------------------------
# RL-3.4 battery / disk awareness (stdlib + reused psutil, never faked)
# ---------------------------------------------------------------------

def disk_free_bytes(path: str) -> int:
    """Free bytes on the volume holding ``path`` (shutil, stdlib)."""
    return shutil.disk_usage(path).free


def battery_state() -> dict:
    """Battery state via psutil, lazily reused (already a tree dep).

    Returns {"available": False, "reason": ...} when psutil is absent or
    the machine reports no battery — never a fake reading.
    """
    try:
        import psutil
    except ImportError:
        return {"available": False, "reason": "psutil unavailable"}
    try:
        battery = psutil.sensors_battery()
    except Exception as exc:  # noqa: BLE001 — probe failure is a verdict
        return {"available": False, "reason": "probe failed: %s" % exc}
    if battery is None:
        return {"available": False, "reason": "no battery reported"}
    return {"available": True, "percent": battery.percent,
            "power_plugged": bool(battery.power_plugged)}


# ---------------------------------------------------------------------
# RL-3.5 sleep/wake library functions (no OS-hook wiring in this build)
# ---------------------------------------------------------------------

def checkpoint_now(flushers) -> list:
    """Run every flush callable (flush all stores). Library function;
    the GUI/OS-hook wiring that calls it on sleep is a later step,
    QUEUED not built. Returns one result dict per flusher."""
    results = []
    for flusher in flushers:
        name = getattr(flusher, "__name__", repr(flusher))
        try:
            flusher()
            results.append({"flusher": name, "ok": True})
        except Exception as exc:  # noqa: BLE001 — one failed flush
            results.append({"flusher": name, "ok": False,
                            "error": str(exc)})
    return results


def pending_reboot_check() -> dict:
    """Pending-reboot verdict (RL-7.5, winreg pattern, Windows-only).

    On Windows, lazily reads the reboot-required registry markers; on
    any other OS the check is honestly not applicable. check-restart's
    licence stays owed (boundary only); this is a native winreg read,
    not that tool.
    """
    if os.name != "nt":
        return {"applicable": False,
                "reason": "winreg is Windows-only"}
    try:
        import winreg
    except ImportError as exc:
        return {"applicable": False, "reason": "winreg unavailable: %s" % exc}
    sources = []
    checks = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager",
         "PendingFileRenameOperations"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate"
         r"\Auto Update",
         "RebootRequired"),
    ]
    for hive, subkey, value in checks:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                winreg.QueryValueEx(key, value)
            sources.append("%s\\%s" % (subkey, value))
        except OSError:
            continue
    return {"applicable": True, "pending": bool(sources),
            "sources": sources}


# ---------------------------------------------------------------------
# RL-7.3 disk-full behaviour + RL-7.6 audio re-resolve + RL-7.8 post-wake
# ---------------------------------------------------------------------

class DiskFullError(Exception):
    """ENOSPC as its own error class (RL-7.3): disk-full is never a
    generic OSError in this track's handling."""


def preflight_free_space(path: str, required_bytes: int) -> dict:
    """Free-space preflight before model/pack downloads (stdlib)."""
    free = disk_free_bytes(path)
    return {"ok": free >= required_bytes, "free": free,
            "required": int(required_bytes)}


def reserve_before_write(path: str, data_len: int,
                         reserve_bytes: int = 0) -> dict:
    """Reserve-before-write: refuse unless free covers data + reserve.

    Raises DiskFullError when the write would not fit; the caller then
    degrades (see :func:`degrade_to_read_only`) instead of dying
    mid-write.
    """
    free = disk_free_bytes(path)
    if free < data_len + reserve_bytes:
        raise DiskFullError(
            "need %d bytes (+%d reserve), only %d free on %s"
            % (data_len, reserve_bytes, free, path))
    return {"ok": True, "free": free, "data_len": int(data_len),
            "reserve": int(reserve_bytes)}


def degrade_to_read_only(reason: str) -> dict:
    """Read-only degradation VERDICT (RL-7.3).

    The library returns the verdict; the spoken surface is later wiring.
    Nothing here speaks, and nothing here deletes user data to make room.
    """
    return {"mode": "read-only", "reason": reason,
            "spoken_verdict": "Disk full: %s. Switched to read-only "
            "so nothing is lost." % reason}


def ensure_log_headroom(log_dir: str,
                        min_free_bytes: int = ONE_GIBIBYTE) -> dict:
    """Log-rotation guard: never let logs eat the last gigabyte.

    If free space is below ``min_free_bytes``, prunes the oldest files
    in ``log_dir`` until headroom returns or the directory is empty.
    Returns the pruned list and whether headroom was achieved.
    """
    pruned = []
    if not os.path.isdir(log_dir):
        return {"pruned": pruned, "ok": True, "reason": "no log dir"}
    while disk_free_bytes(log_dir) < min_free_bytes:
        entries = [os.path.join(log_dir, name)
                   for name in os.listdir(log_dir)
                   if os.path.isfile(os.path.join(log_dir, name))]
        if not entries:
            break
        victim = min(entries, key=lambda p: os.path.getmtime(p))
        os.remove(victim)
        pruned.append(victim)
    return {"pruned": pruned,
            "ok": disk_free_bytes(log_dir) >= min_free_bytes}


def audio_reresolve(current_id, list_devices, default_device) -> dict:
    """RL-7.6 re-resolve rule: never cache an endpoint id across a
    session. Takes a LIVE enumeration on every call; if the current
    device is no longer the OS default, re-resolves to the default and
    reports the switch ONCE as a verdict (the announce itself is later
    wiring — this function does not speak).
    """
    devices = list(list_devices())
    default = default_device()
    if current_id in devices and current_id == default:
        return {"device": current_id, "switched": False,
                "verdict": "audio device unchanged"}
    return {"device": default, "switched": True,
            "verdict": "audio device switched once to the OS default "
            "(was %r, now %r)" % (current_id, default)}


def post_wake_network_state() -> dict:
    """RL-7.8 post-wake rule (rides the RL-3.5 resume handler, later
    wiring): after wake, never declare a provider down without an
    RL-2.1 multi-probe re-probe. "Unreachable immediately after wake"
    is a distinct state from "offline", not a synonym for it.
    """
    return {"state": "re-probe-required",
            "rule": "After wake, re-probe (RL-2.1) before declaring any "
            "provider down; 'unreachable immediately after wake' is "
            "distinct from 'offline'."}
