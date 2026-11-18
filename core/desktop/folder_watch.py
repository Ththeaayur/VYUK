"""B-D2 (2026-11-09) watchdog live-folder sense — the ONE wrapper
every folder watch goes through.

THE GAP (proved first, file/line evidence)
------------------------------------------
There is NO live filesystem-event surface in the tree before this
module:
* Zero ``watchdog`` package imports (grep over core/, gui/,
  tests/ — every "watchdog" hit is a LIVENESS watchdog: the GUI
  splash timer (gui/window_widgets_splash.py:314), the page
  watchdog (gui/window_shell_frame.py:299), the failsafe feed
  (core/failsafe.py:173), the visualizer EMA watchdog
  (gui/visualizer_state.py:226), and the v5.56 response-plane
  watchdog (live_client) — none of them watch the filesystem).
* Zero QFileSystemWatcher, zero ReadDirectoryChangesW, zero
  inotify.
* The tree DOES have ``folder_watch_cook``
  (core/skills_files5_watch_blur_notes.py:179) — but it is an
  ARM-AND-RUN-ON-DEMAND registry, not a live watch: ``_WATCHES``
  is an in-memory dict of folder -> recipe, and its own status
  text says "there is no background daemon, nothing runs until
  you say run_once" and "no background daemon watches the folder
  between asks". It runs rename/move/zip recipes on demand; it
  never sees a file land. It is a different thing and is NOT
  changed here.

Genuinely missing — what this module adds:
1. No watchdog library: no filesystem events at all.
2. No settled event stream: nothing that turns a burst of raw
   events for one save into ONE event.
3. No dead-watch honesty: nothing that can say "this watch is
   dead".
4. No single wrapper a driver can call: register / unregister /
   list / drain.

LICENCE (read from the wheel at build time, 2026-11-09)
--------------------------------------------------------
watchdog 7.1.0 — Apache-2.0. METADATA declares
"License: Apache-2.0"; the wheel's LICENSE file carries the
full Apache License 2.0 text (Copyright 2018-2026 Mickael
Schoentgen & contributors; Copyright 2014-2018 Thomas Amland
& contributors; Copyright 2012-2014 Google, Inc.; Copyright
2011-2012 Yesudeep Mangalapilly). Commercial use permitted.
No runtime dependencies (PyYAML is only for the watchmedo
extra, which VYUK does not use). Requires-Python >= 3.11;
the tree is 3.12.

DEPENDENCY DECISION — PROPOSED, parent rules (same shape as
SymPy in U10 and PyWinCtl in B-D1)
--------------------
Use the INSTALLED package as a DECLARED dependency:
``watchdog>=7.1,<8`` in requirements.txt, pinned
``watchdog==7.1.0`` in requirements.lock. Installed in
.venv-qt only — system python3 has NO watchdog: PEP 668
(externally-managed-environment) blocks ``pip install`` there
and --break-system-packages was NOT used (the tree's
established pattern is .venv-qt-only for new deps: pypdf,
pywinctl). The wrapper degrades there to engine_unavailable.
REJECTED: vendoring (a second copy that can drift — same
reason U8 rejected vendoring py-fsrs and B-D1 rejected
vendoring PyWinCtl); re-implementing in-tree (the tree has
NO filesystem-event code at all — a home-grown
inotify/ReadDirectoryChangesW wrapper would be a worse,
platform-split re-implementation of exactly what watchdog
does, and the brief requires the sense to BE watchdog unless
rejected with reasons).

LOPHOLE FAMILY — "a folder event that lies"
-------------------------------------------
* A burst of events for one save (an editor writes temp +
  rename + modify) is reported as five events.
* A half-written file is reported as ready (a download still
  copying is treated as a finished file).
* A moved file is reported as delete + create (the pair is
  one move, but it looks like a loss plus a new file).
* A watch silently dies (the watched folder deleted, the
  observer thread crashed) and nobody knows.
* An event for a file VYUK was never told to care about
  (noise in, noise out) — a watch on everything.

ONE DESIGN (this module, all watches through it)
------------------------------------------------
The wrapper reports the DELTA OF THE SETTLED STATE, not the
raw event stream. One ``Settler`` owns the rule; one
``FolderWatch`` owns the observers:
* Raw events go into a pending table keyed by path. A burst
  collapses to ONE pending entry: the latest kind wins, except
  a path born in this burst stays "created" and a path moved
  in this burst stays "moved" — the birth/move is the burst's
  defining event.
* A file is reported only after it stops changing inside the
  settle window (default 1.0 s): size + mtime unchanged across
  the window. A file still changing is NOT stable and is NOT
  reported yet.
* A move is ONE "moved" event (path = destination,
  src_path = source) — the library's own move event, never
  delete + create. A move-then-modify burst at the destination
  settles to ONE moved event.
* Create-then-delete inside one window emits NOTHING (net
  zero: the settled state never changed — the file was absent
  before and is absent after). Modify-then-delete emits ONE
  "deleted" (the file existed in the settled state and is
  now gone).
* A directory "modified" event is implied by the child event
  that caused it (a file landing inside it) — reporting it too
  would double-report every landing, so the settler drops
  directory-modified events. Directory creation/deletion are
  still reported: they say a subfolder arrived or left.
* A dead watch fails honestly: ``health()`` checks the
  observer thread AND that the folder still exists; a dead
  watch reports alive False with a reason, never silent.
* The watch list is explicit: ``register()`` is the ONLY
  entry, one folder at a time (like U11's CourseRegistry);
  nothing is watched by default; an unregistered folder
  produces no events.
* Events carry paths, kinds and timestamps ONLY — the
  wrapper never reads a file's contents. The settle check
  stats size/mtime (metadata, not content); reading a file's
  contents is a separate, driver-decided act (e.g. U11's
  grounding).
* NO new tool. Folder sense is driver-called machinery (like
  U11's CourseRegistry, which has no tools): a tool would let
  the model watch arbitrary folders without the driver
  deciding. Registry stays 656, schema-lint stays 1493.

REJECTED ALTERNATIVES
---------------------
* Raw event passthrough — a burst of 5 events for one save.
  Rejected: the settler collapses bursts to the settled delta.
* Reporting on first sight — a half-written file treated as
  ready. Rejected: stability (size + mtime quiet for the
  settle window) before any report.
* Delete + create as two events — a move looks like a loss.
  Rejected: the library's own move event, one "moved".
* A watch with no health check — a dead observer that looks
  alive. Rejected: health() checks thread + folder existence.
* Watching everything — noise. Rejected: explicit register()
  only.
* A home-grown polling loop — a worse, platform-split
  re-implementation of watchdog. Rejected: the sense IS
  watchdog.
* A busy-loop settle thread — burns CPU to watch for quiet.
  Rejected: the settle thread sleeps between sweeps; sweep()
  is the deterministic core a caller (or a test) drives.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

# Default settle window in seconds: a file is stable only when
# its size + mtime have not changed for this long. Short enough
# that a landed lecture file is noticed promptly, long enough
# that an editor's save burst (temp + rename + modify) has
# finished. The settle thread wakes four times per window.
SETTLE_SECONDS = 1.0
_SETTLE_TICK_SECONDS = 0.25

# Event kinds the wrapper ever emits.
KINDS = ("created", "modified", "deleted", "moved")


class _EngineUnavailable(Exception):
    """Raised by the real backend when watchdog cannot be
    imported (not installed). Carries the underlying error text
    so the caller can report it honestly instead of guessing."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ----------------------------------------------------------------
# Settler — the pure settle logic (clock-injectable, no threads)
# ----------------------------------------------------------------
# A Settler owns one rule: the settled-state delta. It takes
# raw events, holds them in a pending table, and promotes an
# entry to a settled event only when the path has been quiet
# for the settle window AND (for files) its size + mtime are
# unchanged since the last sighting. Everything here is a pure
# function of (pending table, now) — tests drive it with a fake
# clock and a fake stat, no sleeping, no threads.

class Settler:
    """Collapse raw filesystem events into settled-state deltas.

    ``now`` is a clock callable returning seconds (default
    time.monotonic); ``stat_fn`` maps a path string to an
    object with .st_size and .st_mtime (default os.stat) and
    raises OSError when the path is gone. Both are injectable
    so tests drive the settler deterministically.
    """

    def __init__(self, settle_seconds: float = SETTLE_SECONDS,
                 now=None, stat_fn=None):
        self.settle_seconds = float(settle_seconds)
        self._now = now or time.monotonic
        self._stat = stat_fn or os.stat
        # path -> {"kind", "first_seen", "last_seen", "size",
        #          "mtime", "is_directory", "src_path"}
        self._pending: dict[str, dict] = {}

    def pending_count(self) -> int:
        """Paths currently held, not yet settled."""
        return len(self._pending)

    def drop_under(self, folder: str) -> None:
        """Abandon every pending entry at or under ``folder``
        (unregistering says "I no longer care" — unsettled
        events for that folder are dropped)."""
        import os as _os
        for path in [p for p in self._pending
                    if p == folder or p.startswith(folder + _os.sep)]:
            del self._pending[path]

    def clear(self) -> None:
        """Drop every pending entry (closing the wrapper)."""
        self._pending.clear()

    def note(self, kind: str, path: str, dest_path: str | None = None,
             is_directory: bool = False) -> None:
        """Feed one raw event. ``kind`` is created / modified /
        deleted / moved; for a move, ``path`` is the SOURCE and
        ``dest_path`` the destination. Directories are tracked
        like files except their stability is quiet-time only
        (a directory's mtime moves whenever a child lands, and
        the child events arrive separately — statting it would
        keep resetting the clock for as long as files land)."""
        if kind not in KINDS:
            return
        if kind == "modified" and is_directory:
            # A directory's modification is implied by the child
            # event that caused it (a file landing inside it) —
            # reporting it too would double-report every landing.
            # Directory creation/deletion are still reported:
            # they say a subfolder arrived or left. Metadata-only
            # changes to a directory (a chmod) are not content
            # changes and are not reported.
            return
        now = self._now()
        if kind == "moved":
            self._note_moved(path, dest_path, now, is_directory)
            return
        if kind == "deleted":
            self._note_deleted(path, now, is_directory)
            return
        entry = self._pending.get(path)
        if entry is None:
            size, mtime = self._file_fingerprint(path, is_directory)
            self._pending[path] = {
                "kind": kind, "first_seen": now, "last_seen": now,
                "size": size, "mtime": mtime,
                "is_directory": is_directory, "src_path": None,
            }
            return
        # A path born in this burst stays "created", and a path
        # moved in this burst stays "moved" — the birth/move is
        # the burst's defining event, and later modifies at the
        # same path are part of the same burst, not a new one.
        # A "modified" entry becomes "created" when a created
        # event arrives (the file was recreated in this burst).
        if entry["kind"] not in ("created", "moved"):
            entry["kind"] = kind
        entry["last_seen"] = now
        size, mtime = self._file_fingerprint(path, is_directory)
        if size is not None:
            entry["size"] = size
        if mtime is not None:
            entry["mtime"] = mtime

    def _note_deleted(self, path: str, now: float,
                      is_directory: bool) -> None:
        entry = self._pending.get(path)
        if entry is None:
            self._pending[path] = {
                "kind": "deleted", "first_seen": now,
                "last_seen": now, "size": None, "mtime": None,
                "is_directory": is_directory, "src_path": None,
            }
            return
        if entry["kind"] == "created":
            # Born and died inside one settle window: the settled
            # state never changed (absent before, absent after) —
            # net zero, no event.
            del self._pending[path]
            return
        # It existed in the settled state and is now gone.
        entry["kind"] = "deleted"
        entry["last_seen"] = now
        entry["size"] = None
        entry["mtime"] = None

    def _note_moved(self, src: str, dest: str | None, now: float,
                    is_directory: bool) -> None:
        if not dest:
            return
        # The source's burst moves with it; a pending entry at
        # the destination (a modify that landed just before the
        # move) is absorbed — the move is the one event.
        self._pending.pop(src, None)
        size, mtime = self._file_fingerprint(dest, is_directory)
        self._pending[dest] = {
            "kind": "moved", "first_seen": now, "last_seen": now,
            "size": size, "mtime": mtime,
            "is_directory": is_directory, "src_path": src,
        }

    def _file_fingerprint(self, path: str, is_directory: bool):
        """(size, mtime) for a file, (None, None) for a directory
        (quiet-time only, see note()) or when the path cannot be
        statted (already gone — the sweep handles that)."""
        if is_directory:
            return None, None
        try:
            st = self._stat(path)
        except OSError:
            return None, None
        return st.st_size, st.st_mtime

    def sweep(self, now: float | None = None) -> list[dict]:
        """Promote every entry quiet for the settle window to a
        settled event (stable True). A file whose size/mtime
        moved since its last sighting is still changing: its
        clock resets and it is NOT emitted yet. A pending file
        that is gone at sweep time (its delete event was lost)
        is emitted as "deleted" — the settled state changed
        (the file is gone) and silence would hide it. Returns
        the newly settled events, oldest first."""
        now = self._now() if now is None else now
        settled: list[dict] = []
        for path in sorted(self._pending):
            entry = self._pending[path]
            if now - entry["last_seen"] < self.settle_seconds:
                continue
            if not entry["is_directory"] and entry["kind"] != "deleted":
                try:
                    st = self._stat(path)
                except OSError:
                    # Gone without a delete event: the settled
                    # state changed anyway — report the loss.
                    entry["kind"] = "deleted"
                    entry["size"] = None
                    entry["mtime"] = None
                else:
                    if entry["size"] is None:
                        # Baseline unknown (the file could not be
                        # statted when the event arrived): take
                        # this reading as the baseline and wait
                        # one more window — stability cannot be
                        # claimed from a single sighting.
                        entry["size"] = st.st_size
                        entry["mtime"] = st.st_mtime
                        entry["last_seen"] = now
                        continue
                    if (st.st_size != entry["size"]
                            or st.st_mtime != entry["mtime"]):
                        # Still changing: reset the clock, do not
                        # emit yet.
                        entry["size"] = st.st_size
                        entry["mtime"] = st.st_mtime
                        entry["last_seen"] = now
                        continue
            settled.append({
                "kind": entry["kind"],
                "path": path,
                "src_path": entry["src_path"],
                "stable": True,
                "timestamp": now,
                "is_directory": entry["is_directory"],
                "size": entry["size"],
            })
            del self._pending[path]
        return settled


# ----------------------------------------------------------------
# Backend protocol
# ----------------------------------------------------------------
# A backend is any object with:
#   start(folder, handler, recursive) — begin watching; handler
#       is called as handler(kind, path, dest_path, is_directory)
#       on the backend's own thread.
#   stop() — stop watching.
#   is_alive() -> bool — the watch thread is still running.
# The real backend adapts watchdog's Observer; tests inject a
# fake. Nothing else in this module knows which backend it has.


def _load_watchdog():
    """Import watchdog's Observer + handler base (function-level
    static imports, the sympy pattern in
    core/brain/check_working.py). Returns (Observer,
    FileSystemEventHandler). Raises _EngineUnavailable when
    watchdog cannot be imported. Repeated calls are cheap —
    the import system caches the modules."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception as e:  # noqa: BLE001 — ANY import failure
        # (ImportError, ...) is an unavailable engine, not a
        # crash.
        raise _EngineUnavailable(
            f"watchdog unavailable: {type(e).__name__}: {e}"
        ) from e
    return Observer, FileSystemEventHandler


class _WatchdogBackend:
    """The real backend: watchdog's Observer + a
    FileSystemEventHandler that forwards raw events. Imports
    are function-level static imports inside the constructor
    (the sympy pattern) — the packaging checker only derives
    import_module/__import__ targets, so no spec/checker entry
    is needed, and the frozen build still picks watchdog up
    through PyInstaller's static analysis of the import
    statement. Importing this module never touches watchdog."""

    def __init__(self):
        Observer, HandlerBase = _load_watchdog()
        self._Observer = Observer
        self._HandlerBase = HandlerBase
        self._observer = None
        self._handler = None

    def start(self, folder, handler, recursive=True):
        """Begin watching ``folder``; ``handler`` receives raw
        events on watchdog's thread."""

        class _Handler(self._HandlerBase):
            def on_created(self, event):
                handler("created", event.src_path, None,
                        event.is_directory)

            def on_modified(self, event):
                handler("modified", event.src_path, None,
                        event.is_directory)

            def on_deleted(self, event):
                handler("deleted", event.src_path, None,
                        event.is_directory)

            def on_moved(self, event):
                handler("moved", event.src_path, event.dest_path,
                        event.is_directory)

        self._handler = _Handler()
        self._observer = self._Observer()
        self._observer.schedule(self._handler, folder,
                                recursive=recursive)
        self._observer.start()

    def stop(self):
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:  # noqa: BLE001 — stopping must not
                # raise; a dead observer is reported by is_alive.
                pass
            self._observer = None

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()


def engine_available() -> bool:
    """True when watchdog imports cleanly (False on system
    python3 — not installed there — and on a machine without
    the install)."""
    try:
        _load_watchdog()
    except _EngineUnavailable:
        return False
    return True


def engine_status() -> dict:
    """Honest engine report: {"available": bool, "backend":
    "watchdog" | None, "error": str | None}."""
    try:
        _load_watchdog()
    except _EngineUnavailable as e:
        return {"available": False, "backend": None, "error": e.detail}
    return {"available": True, "backend": "watchdog", "error": None}


# ----------------------------------------------------------------
# Public wrapper
# ----------------------------------------------------------------

class FolderWatch:
    """One wrapper for live-folder sense. A driver registers
    folders explicitly (one at a time); raw watchdog events
    collapse through the Settler into settled events; ``drain()``
    returns what has settled. Nothing is watched by default.
    ``clock`` is a seconds callable (default time.monotonic);
    when a fake clock is injected, NO settle thread starts and
    the caller drives ``sweep()`` explicitly — that is the
    deterministic core the tests use. ``backend_factory`` builds
    a backend (default the real watchdog backend);
    ``stat_fn`` is the settler's stat (default os.stat).
    ``settle_seconds`` is the quiet window (default 1.0 s)."""

    def __init__(self, *, settle_seconds: float = SETTLE_SECONDS,
                 clock=None, backend_factory=None, stat_fn=None):
        self.settle_seconds = float(settle_seconds)
        self._now = clock or time.monotonic
        self._backend_factory = backend_factory or _WatchdogBackend
        self._settler = Settler(settle_seconds=self.settle_seconds,
                                now=self._now, stat_fn=stat_fn)
        self._lock = threading.Lock()
        self._settled: list[dict] = []
        # folder (resolved string) -> {"backend", "recursive"}
        self._watches: dict[str, dict] = {}
        self._settle_thread = None
        self._settle_stop = threading.Event()
        self._closed = False

    # -- internal ------------------------------------------------

    def _on_raw(self, kind, path, dest_path, is_directory):
        """A raw event from a backend thread: feed the settler
        only. Never blocks, never reads file contents."""
        with self._lock:
            self._settler.note(kind, path, dest_path, is_directory)

    def sweep(self) -> list[dict]:
        """One deterministic settle pass (uses the injected
        clock): promote quiet entries to the settled queue and
        return them. The settle thread calls this; a caller
        with a fake clock calls it directly."""
        with self._lock:
            newly = self._settler.sweep(self._now())
            self._settled.extend(newly)
            return list(newly)

    def _settle_loop(self):
        while not self._settle_stop.wait(_SETTLE_TICK_SECONDS):
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 — the settle thread
                # must never die on a bad event; the next tick
                # retries. (A dead settle thread would silently
                # stop all reports — health() checks it.)
                continue

    def _ensure_settle_thread(self):
        # With a fake clock the caller drives sweep() — no thread
        # (a thread sleeping on wall-clock time while the clock
        # is fake would spin without ever settling).
        if self._now is not time.monotonic:
            return
        if self._settle_thread is not None and \
                self._settle_thread.is_alive():
            return
        self._settle_stop.clear()
        self._settle_thread = threading.Thread(
            target=self._settle_loop, name="folder-watch-settle",
            daemon=True)
        self._settle_thread.start()

    # -- public --------------------------------------------------

    def register(self, folder: str, *, recursive: bool = True) -> dict:
        """Watch one folder: {"ok", "folder", "reason"}. The
        folder must exist and be a directory; registering the
        same resolved folder twice is an honest failure, not a
        second observer. Nothing is watched until this is
        called."""
        if self._closed:
            return {"ok": False, "folder": None,
                    "reason": "closed: this FolderWatch is closed"}
        if not folder or not str(folder).strip():
            return {"ok": False, "folder": None,
                    "reason": "no_match: no folder given"}
        root = Path(str(folder)).expanduser()
        try:
            resolved = str(root.resolve())
        except OSError as e:
            return {"ok": False, "folder": None,
                    "reason": f"no_match: cannot resolve {folder!r}: {e}"}
        if not os.path.isdir(resolved):
            return {"ok": False, "folder": None,
                    "reason": f"no_match: {resolved!r} is not an "
                              "existing folder"}
        if resolved in self._watches:
            return {"ok": False, "folder": resolved,
                    "reason": f"already_watched: {resolved!r} is "
                              "already registered"}
        try:
            # Building the real backend imports watchdog (raises
            # _EngineUnavailable when it is missing); a fake
            # backend factory builds without touching watchdog.
            backend = self._backend_factory()
        except _EngineUnavailable as e:
            return {"ok": False, "folder": resolved,
                    "reason": f"engine_unavailable: {e.detail}"}
        except Exception as e:  # noqa: BLE001 — a backend that
            # cannot be built is an honest failure, not a crash.
            return {"ok": False, "folder": resolved,
                    "reason": f"backend_failed: {type(e).__name__}: {e}"}
        try:
            backend.start(resolved, self._on_raw, recursive=recursive)
        except Exception as e:  # noqa: BLE001
            try:
                backend.stop()
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "folder": resolved,
                    "reason": f"backend_failed: {type(e).__name__}: {e}"}
        with self._lock:
            self._watches[resolved] = {"backend": backend,
                                       "recursive": bool(recursive)}
        self._ensure_settle_thread()
        return {"ok": True, "folder": resolved, "reason": None}

    def unregister(self, folder: str) -> dict:
        """Stop watching one folder: {"ok", "folder", "reason"}.
        Settled-but-undrained events stay in the queue;
        unsettled (still pending) events for that folder are
        abandoned — unregistering says "I no longer care".
        Unregistering a folder that was never registered is an
        honest failure, not a silent no-op."""
        if not folder or not str(folder).strip():
            return {"ok": False, "folder": None,
                    "reason": "no_match: no folder given"}
        try:
            resolved = str(Path(str(folder)).expanduser().resolve())
        except OSError as e:
            return {"ok": False, "folder": None,
                    "reason": f"no_match: cannot resolve {folder!r}: {e}"}
        with self._lock:
            entry = self._watches.pop(resolved, None)
            if entry is None:
                return {"ok": False, "folder": resolved,
                        "reason": f"not_watched: {resolved!r} was "
                                  "never registered"}
            # Drop this folder's pending (unsettled) entries;
            # settled events already promoted stay drainable.
            self._settler.drop_under(resolved)
        try:
            entry["backend"].stop()
        except Exception:  # noqa: BLE001 — stopping must not raise.
            pass
        return {"ok": True, "folder": resolved, "reason": None}

    def list_watches(self) -> dict:
        """Registered folders with health: {"ok", "watches":
        [{folder, recursive, alive, reason}], "reason"}. A watch
        whose observer thread died OR whose folder no longer
        exists reports alive False with a reason — never
        silent."""
        out = []
        with self._lock:
            items = list(self._watches.items())
        for folder, entry in sorted(items):
            alive, reason = self._watch_health(folder, entry)
            out.append({"folder": folder,
                        "recursive": entry["recursive"],
                        "alive": alive, "reason": reason})
        return {"ok": True, "watches": out, "reason": None}

    def _watch_health(self, folder: str, entry: dict):
        """(alive, reason) for one watch: the observer thread
        must be running AND the folder must still exist."""
        try:
            running = entry["backend"].is_alive()
        except Exception as e:  # noqa: BLE001
            return False, f"dead: backend health check failed: {e}"
        if not running:
            return False, "dead: the observer thread is not running"
        if not os.path.isdir(folder):
            return False, (f"dead: the folder {folder!r} no longer "
                           "exists")
        return True, None

    def health(self) -> dict:
        """The dead-watch check: {"ok", "alive", "watches",
        "reason"}. ``ok`` is False when ANY watch is dead (the
        reason names it); with no watches registered the wrapper
        is trivially alive."""
        listed = self.list_watches()
        dead = [w for w in listed["watches"] if not w["alive"]]
        if dead:
            names = ", ".join(
                f"{w['folder']} ({w['reason']})" for w in dead)
            return {"ok": False, "alive": False,
                    "watches": listed["watches"],
                    "reason": f"dead_watch: {names}"}
        if self._now is time.monotonic and self._watches:
            thread = self._settle_thread
            if thread is None or not thread.is_alive():
                return {"ok": False, "alive": False,
                        "watches": listed["watches"],
                        "reason": "dead: the settle thread is not "
                                  "running"}
        return {"ok": True, "alive": True,
                "watches": listed["watches"], "reason": None}

    def drain(self) -> dict:
        """Settled events since the last drain, oldest first:
        {"ok", "events": [{kind, path, src_path, stable,
        timestamp, is_directory, size}], "reason"}. Non-blocking;
        an empty queue is an honest empty list, never an error.
        A sweep is NOT run here — the settle thread (or the
        caller's explicit sweep() with a fake clock) promotes
        events; drain() only reports what has settled."""
        with self._lock:
            events = self._settled
            self._settled = []
        events.sort(key=lambda e: (e["timestamp"], e["path"]))
        return {"ok": True, "events": events, "reason": None}

    def pending_count(self) -> int:
        """Paths held, not yet settled (diagnostics)."""
        with self._lock:
            return self._settler.pending_count()

    def close(self) -> dict:
        """Stop every watch and the settle thread. Idempotent.
        Settled-but-undrained events are discarded with the
        wrapper — drain before closing when they matter."""
        if self._closed:
            return {"ok": True, "reason": None}
        self._closed = True
        self._settle_stop.set()
        with self._lock:
            entries = list(self._watches.values())
            self._watches = {}
            self._settler.clear()
            self._settled = []
        for entry in entries:
            try:
                entry["backend"].stop()
            except Exception:  # noqa: BLE001
                pass
        thread = self._settle_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        return {"ok": True, "reason": None}
