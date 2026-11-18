"""
Desktop skills pack 4 for VYUK (v5.12) — tiling, a composed focus
session, and a read on what files the clipboard is holding.

Same rules as the other packs: plain functions returning
human-readable strings; platform-specific calls (ctypes user32 /
shell32) are reached lazily and guarded, so this module imports
cleanly on any OS and the Windows-only skills return a clear
fallback string elsewhere instead of crashing.

What each tool is honest about:

- tile_windows enumerates the open windows with
  core/desktop/control.py's _enum_windows (reused, not reinvented), tiles
  them inside the primary monitor's WORK AREA (which excludes
  the taskbar), restores minimized windows first and says how
  many, moves each window with SetWindowPos and then READS ITS
  RECT BACK — a window only counts as tiled when the read-back
  lands within 8 px of its cell (window borders lie, so exact
  equality would cry wolf). At most 12 windows are tiled; more
  open than that is disclosed, never silently truncated.

- focus_session is a COMPOSITION, not new machinery: Focus
  Assist from core/skills_plus.set_focus_assist, timed
  keep-awake from core/skills_desktop_prefs.keep_awake, the
  end-of-session announcement from core/scheduler.py's reminder
  list (which the GUI shows and reads out), a toast from
  core/skills_desktop_prefs.notify_user, and the restore itself on a
  threading.Timer — the same daemon-thread deadline pattern
  keep_awake's timed mode is built on. The split exists because
  core/scheduler.py can only *announce* (nothing in VYUK lets a
  reminder run an action) and routine_scheduler fires whole
  user routines through a GUI-owned callback — far too heavy,
  and it would plant a fake routine in the user's list. Every
  sub-step's own result is checked and reported individually:
  a step that failed is named, never papered over. The session
  is recorded in data/focus_session.json, and the record is all
  that survives a restart — which is why start's reply always
  carries the limit: if VYUK closes, the timer dies with it.

- clipboard_files is read-only: it classifies the clipboard
  (files / text / empty / something else / can't open) via
  IsClipboardFormatAvailable and lists CF_HDROP paths through
  DragQueryFileW, capped at 20 with the remainder counted.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import datetime
import json
import math
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _fmt_time(moment: datetime.datetime) -> str:
    return moment.strftime("%H:%M")


# ---------------------------------------------------------------------------
# TILE WINDOWS — enumerate, divide the work area, place, read back
# ---------------------------------------------------------------------------

_MAX_TILE = 12
_TILE_GAP = 4            # px inset on every side of every cell
_TILE_TOLERANCE = 8      # px of read-back slack — window borders lie
_SW_RESTORE = 9
_SWP_NOZORDER = 0x0004
_SPI_GETWORKAREA = 0x0030
_LAYOUTS = ("grid", "columns", "rows")


def _grid_dims(n: int) -> tuple[int, int]:
    """(columns, rows) for a near-square grid of *n* cells."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return cols, rows


def _cells(area: dict, n: int, layout: str) -> list[dict]:
    """Divide *area* ({left, top, width, height}) into *n* cells for
    *layout*, each inset by the tile gap. Pure geometry — boundaries
    are shared edges computed from the same fractions, so cells can
    never overlap, and the gap inset keeps them strictly separate.
    Row-major order: grid fills left-to-right, top-to-bottom."""
    if n <= 0:
        return []
    if layout == "columns":
        cols, rows = n, 1
    elif layout == "rows":
        cols, rows = 1, n
    else:
        cols, rows = _grid_dims(n)
    cells = []
    for index in range(n):
        row, col = divmod(index, cols)
        x0 = area["left"] + round(col * area["width"] / cols)
        x1 = area["left"] + round((col + 1) * area["width"] / cols)
        y0 = area["top"] + round(row * area["height"] / rows)
        y1 = area["top"] + round((row + 1) * area["height"] / rows)
        cells.append({
            "left": x0 + _TILE_GAP,
            "top": y0 + _TILE_GAP,
            "width": max(1, (x1 - x0) - 2 * _TILE_GAP),
            "height": max(1, (y1 - y0) - 2 * _TILE_GAP),
        })
    return cells


def _rect_text(rect: dict) -> str:
    return (f"({rect['left']}, {rect['top']}) "
            f"{rect['width']}x{rect['height']}")


def _rect_close(actual: dict, wanted: dict,
                tolerance: int = _TILE_TOLERANCE) -> bool:
    """True when every field of the read-back rect is within the
    tolerance of the cell the window was sent to."""
    return all(abs(actual[f] - wanted[f]) <= tolerance
               for f in ("left", "top", "width", "height"))


def _open_windows() -> list[dict]:
    """Every visible, titled window right now: {hwnd, title,
    minimized}. Windows only; [] elsewhere or on failure. Reuses
    core/desktop/control.py's enumerator, like skills_desktop_layouts does."""
    if not _is_windows():
        return []
    try:
        import ctypes

        from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

        user32 = ctypes.windll.user32
        windows = []
        for hwnd, title in _enum_windows():
            try:
                minimized = bool(user32.IsIconic(hwnd))
            except Exception:  # noqa: BLE001 — one bad window skips, not all
                continue
            windows.append({"hwnd": hwnd, "title": title,
                            "minimized": minimized})
        return windows
    except Exception:  # noqa: BLE001
        return []


def _work_area() -> dict | None:
    """The primary monitor's work area (screen minus taskbar) via
    SystemParametersInfo(SPI_GETWORKAREA), or None when it can't be
    read. Windows only."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        if not ok:
            return None
        return {"left": rect.left, "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top}
    except Exception:  # noqa: BLE001
        return None


def _place_window(win: dict, cell: dict) -> dict | None:
    """Move one window into its cell and read the result back: the
    actual rect afterwards, or None when Windows refused the move
    or the read-back failed. A minimized window is restored first
    (SW_RESTORE), then SetWindowPos with SWP_NOZORDER — the same
    seam shape as skills_desktop_layouts's _apply_window_restore."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = win["hwnd"]
        if win.get("minimized"):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        if not user32.SetWindowPos(hwnd, 0, cell["left"], cell["top"],
                                   cell["width"], cell["height"],
                                   _SWP_NOZORDER):
            return None
        actual = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(actual)):
            return None
        return {"left": actual.left, "top": actual.top,
                "width": actual.right - actual.left,
                "height": actual.bottom - actual.top}
    except Exception:  # noqa: BLE001
        return None


def tile_windows(layout: str = "grid") -> str:
    """Arrange every open window into a grid, columns, or rows."""
    wanted = (layout or "grid").strip().lower()
    if wanted not in _LAYOUTS:
        return (f"Unknown tiling layout: '{layout}'. "
                "Use one of: grid, columns, rows.")
    if not _is_windows():
        return f"Tiling windows needs Windows — {_NOT_WINDOWS}"
    windows = _open_windows()
    if not windows:
        return ("No open windows to tile — nothing visible with a "
                "title is open right now.")
    area = _work_area()
    if area is None:
        return ("I couldn't read the primary monitor's work area, "
                "so no windows were moved.")
    total = len(windows)
    chosen = windows[:_MAX_TILE]
    count = len(chosen)
    cells = _cells(area, count, wanted)
    restored = sum(1 for w in chosen if w.get("minimized"))
    placed = 0
    problems = []
    for win, cell in zip(chosen, cells):
        actual = _place_window(win, cell)
        if actual is not None and _rect_close(actual, cell):
            placed += 1
        elif actual is None:
            problems.append(
                f"• '{win['title']}' couldn't be moved — Windows "
                "refused it or its new position couldn't be read back.")
        else:
            problems.append(
                f"• '{win['title']}' was moved, but it now sits at "
                f"{_rect_text(actual)} — not exactly its tile "
                f"({_rect_text(cell)}).")
    if wanted == "grid":
        cols, rows = _grid_dims(count)
        shape = f"a grid ({cols}×{rows})"
    elif wanted == "columns":
        shape = f"{count} columns"
    else:
        shape = f"{count} rows"
    if placed == count:
        lines = [f"Tiled {count} window{'s' if count != 1 else ''} "
                 f"in {shape}."]
    else:
        lines = [f"Tiled {placed} of {count} windows in {shape}."]
    lines.extend(problems)
    if restored:
        lines.append(
            f"{restored} of them were minimized and had to be "
            "restored to be tiled.")
    if total > _MAX_TILE:
        lines.append(
            f"{total} windows are open; only the first {_MAX_TILE} "
            "were tiled.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FOCUS SESSION — composed from existing machinery (see module docstring)
#
# Sub-steps, all reused, all reached by call-time imports so their
# own modules stay the single source of truth (and so tests can
# fake them at those seams):
#   core.skills_plus.set_focus_assist(on)     — the mute itself
#   core.skills_desktop_prefs.keep_awake(...)     — timed awake thread
#   core.skills_desktop_prefs.notify_user(...)    — the end toast
#   core.scheduler.scheduler.add_reminder    — the end announcement
#   core.scheduler.scheduler.cancel_reminder — disarming it
# The restore (Focus Assist back off) rides _arm_end_timer: a
# daemon threading.Timer, cancelled by stop.
# ---------------------------------------------------------------------------

_MIN_FOCUS_MINUTES = 1
_MAX_FOCUS_MINUTES = 480     # 8 hours — a focus session, not a lifestyle
_REMINDER_LABEL = "focus_session_end"
_END_MESSAGE = "Focus session over — time to resurface."

_end_timer = None            # the live Timer handle, or None


def _focus_path() -> Path:
    """Where the session record lives — the app's data dir
    (core/config.py's DATA_DIR), resolved fresh on every call."""
    from .config import DATA_DIR

    return DATA_DIR / "focus_session.json"


def _load_focus(path: Path):
    """(record, corrupt): the stored session dict, or None. A
    missing file is (None, False); an unreadable or wrong-shaped
    one is (None, True) — reported, never a crash (load-boundary
    discipline)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    try:
        record = json.loads(raw)
    except ValueError:
        return None, True
    if not isinstance(record, dict):
        return None, True
    try:
        ends_at = datetime.datetime.fromisoformat(
            str(record.get("ends_at", "")))
    except ValueError:
        return None, True
    record["ends_at_dt"] = ends_at
    return record, False


def _save_focus(path: Path, record: dict) -> str | None:
    """Write the record (atomically: temp file + os.replace, like
    routine_scheduler). An error string on failure, else None."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        import os

        os.replace(tmp, path)
        return None
    except OSError as e:
        return str(e)


def _clear_focus_record() -> None:
    try:
        _focus_path().unlink(missing_ok=True)
    except OSError:
        pass


def _arm_end_timer(delay_seconds: float, fn):
    """Arm the restore leg: a daemon Timer that fires *fn* after
    *delay_seconds*. Daemon, so it never keeps VYUK alive — which
    is exactly the honest limit start's reply states. Factored so
    tests can capture the handle instead of waiting."""
    timer = threading.Timer(delay_seconds, fn)
    timer.daemon = True
    timer.start()
    return timer


def _disarm_end_timer() -> bool:
    """Cancel the pending restore, if one is armed. True when a
    live timer was actually cancelled."""
    global _end_timer
    timer, _end_timer = _end_timer, None
    if timer is None:
        return False
    timer.cancel()
    return True


def _finish_session() -> None:
    """The scheduled end (runs on the Timer thread): Focus Assist
    back off, the 'Focus session over' toast, record cleared.
    Keep-awake needs nothing here — its own timed thread releases
    at the same deadline. Never raises: a timer thread has nobody
    to report an exception to, so failures are printed."""
    global _end_timer
    _end_timer = None
    try:
        from .skills_plus import set_focus_assist

        print(f"[Focus] session end: {set_focus_assist(False)}")
    except Exception as e:  # noqa: BLE001
        print(f"[Focus] could not switch Focus Assist off at the end: {e}")
    try:
        from .skills_desktop_prefs import notify_user

        notify_user("Focus session over",
                    "Focus Assist is off again — notifications "
                    "are back on.")
    except Exception as e:  # noqa: BLE001
        print(f"[Focus] end-of-session notification failed: {e}")
    _clear_focus_record()


def _minutes_left(ends_at: datetime.datetime) -> int:
    seconds = (ends_at - _now()).total_seconds()
    return max(1, math.ceil(seconds / 60))


def focus_session(action: str = "start", minutes: int = 25) -> str:
    """Run, check, or end a timed focus mode."""
    act = (action or "start").strip().lower()
    if act not in ("start", "status", "stop"):
        return (f"Unknown focus session action: '{action}'. "
                "Use one of: start, status, stop.")
    if act == "status":
        return _focus_status()
    if act == "stop":
        return _focus_stop()
    return _focus_start(minutes)


def _focus_start(minutes) -> str:
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return f"Focus minutes must be a whole number — '{minutes}' isn't."
    if mins < _MIN_FOCUS_MINUTES or mins > _MAX_FOCUS_MINUTES:
        return (f"A focus session runs {_MIN_FOCUS_MINUTES} to "
                f"{_MAX_FOCUS_MINUTES} minutes — {mins} is outside "
                "that.")
    path = _focus_path()
    record, corrupt = _load_focus(path)
    notes = []
    if record is not None and record["ends_at_dt"] > _now():
        left = _minutes_left(record["ends_at_dt"])
        return (f"A focus session is already running — about "
                f"{left} minute{'s' if left != 1 else ''} left "
                f"(it ends at {_fmt_time(record['ends_at_dt'])}). "
                "I haven't stacked another one on top; stop it "
                "first if you want a different length.")
    if record is not None or corrupt:
        # A record whose end has passed without finishing: its
        # finish only runs while VYUK is open, so VYUK must have
        # closed. Retire it — and its possibly-still-pending
        # reminder — before starting fresh.
        notes.append(
            "The previous session's record never finished (VYUK "
            "must have closed before its end), so I'm starting "
            "fresh.")
        old_label = (record or {}).get("reminder_label") or _REMINDER_LABEL
        try:
            from .scheduler import scheduler

            scheduler.cancel_reminder(old_label)
        except Exception:  # noqa: BLE001 — best-effort retirement
            pass
        _disarm_end_timer()
    # Step 1: Focus Assist — the session's whole point. If this
    # fails there is no session, and nothing else is engaged.
    from .skills_plus import set_focus_assist

    focus_result = set_focus_assist(True)
    if not str(focus_result).startswith("Focus Assist is on"):
        return (f"Focus session not started — Focus Assist "
                f"couldn't be turned on: {focus_result} Nothing "
                "else was engaged.")
    lines = []
    # Step 2: keep-awake for the duration (its own timed thread
    # releases at the deadline on its own).
    from .skills_desktop_prefs import keep_awake

    awake_result = keep_awake("start", mins)
    awake_ok = awake_result.startswith("Keep-awake is ON")
    # Step 3: schedule the end — Timer for the restore, the app's
    # reminder list for the announcement.
    ends_at = _now() + datetime.timedelta(minutes=mins)
    global _end_timer
    _end_timer = _arm_end_timer(mins * 60, _finish_session)
    reminder_label = None
    try:
        from .scheduler import scheduler

        reminder_label = scheduler.add_reminder(
            label=_REMINDER_LABEL, fire_at=ends_at,
            message=_END_MESSAGE, repeat=False)
    except Exception:  # noqa: BLE001 — the restore Timer still stands
        reminder_label = None
    # Step 4: record the session.
    record = {
        "started_at": _now().isoformat(timespec="seconds"),
        "ends_at": ends_at.isoformat(timespec="seconds"),
        "minutes": mins,
        "reminder_label": reminder_label or _REMINDER_LABEL,
    }
    write_error = _save_focus(path, record)
    lines.append(
        f"Focus session started — {mins} minutes, ending at "
        f"{_fmt_time(ends_at)}. Focus Assist is on, so pop-up "
        "notifications are muted.")
    if awake_ok:
        lines.append(
            f"Keep-awake is on for {mins} minutes, so the PC won't "
            "sleep mid-session.")
    else:
        lines.append(
            f"Keep-awake did NOT engage: {awake_result} The PC may "
            "still sleep on its normal schedule.")
    if reminder_label is not None:
        lines.append(
            f"At {_fmt_time(ends_at)} Focus Assist switches back "
            "off and you'll get a 'Focus session over' "
            "notification.")
    else:
        lines.append(
            f"At {_fmt_time(ends_at)} Focus Assist switches back "
            "off (the in-app end reminder couldn't be set, but the "
            "restore timer is armed).")
    if write_error is not None:
        lines.append(
            f"Note: the session record couldn't be saved "
            f"({write_error}), so status/stop won't see this "
            "session after a restart of VYUK")
    lines.extend(notes)
    lines.append(
        "If VYUK closes, the timer dies with it — Focus Assist "
        "would stay on; just ask me to turn it off.")
    return "\n".join(lines)


def _focus_status() -> str:
    record, corrupt = _load_focus(_focus_path())
    if corrupt:
        return ("The focus session record is damaged, so I can't "
                "tell whether a session is running. Stopping the "
                "session will clear the record and switch Focus "
                "Assist off.")
    if record is None:
        return "No focus session is active."
    ends_at = record["ends_at_dt"]
    if ends_at > _now():
        left = _minutes_left(ends_at)
        return (f"Focus session is active — about {left} "
                f"minute{'s' if left != 1 else ''} left "
                f"(it ends at {_fmt_time(ends_at)}).")
    return (f"A focus session was recorded as ending at "
            f"{_fmt_time(ends_at)}, but its automatic finish never "
            "ran — that only happens while VYUK stays open, so "
            "VYUK must have closed or restarted. Focus Assist may "
            "still be on; say 'stop the focus session' and I'll "
            "turn it off and clear the record.")


def _focus_stop() -> str:
    path = _focus_path()
    record, corrupt = _load_focus(path)
    if record is None and not corrupt:
        return ("No focus session is active — nothing to stop, "
                "and Focus Assist was left as it is.")
    lines = []
    if corrupt:
        lines.append(
            "The session record was damaged, so I'm treating this "
            "as a stop anyway: everything focus-related is being "
            "switched off and the record cleared.")
    elif record["ends_at_dt"] > _now():
        left = _minutes_left(record["ends_at_dt"])
        lines.append(
            f"Focus session stopped with about {left} "
            f"minute{'s' if left != 1 else ''} to spare.")
    else:
        lines.append(
            "Focus session stopped — its recorded end had already "
            "passed without the automatic finish running.")
    # Disarm both end legs, and say how each actually went.
    if _disarm_end_timer():
        lines.append(
            "The scheduled finish was cancelled, so nothing will "
            "fire later.")
    label = (record or {}).get("reminder_label") or _REMINDER_LABEL
    try:
        from .scheduler import scheduler

        if scheduler.cancel_reminder(label):
            lines.append("The end-of-session reminder was removed.")
    except Exception:  # noqa: BLE001 — disarm attempt reported by omission
        pass
    # Keep-awake off (its own wording decides whether it was on).
    from .skills_desktop_prefs import keep_awake

    if keep_awake("stop").startswith("Keep-awake is OFF"):
        lines.append("Keep-awake is off.")
    # Focus Assist off NOW — checked, like everything else.
    from .skills_plus import set_focus_assist

    off_result = set_focus_assist(False)
    if str(off_result).startswith("Focus Assist is off"):
        lines.append(
            "Focus Assist is off — notifications will show again.")
    else:
        lines.append(
            f"Focus Assist did NOT turn off: {off_result}")
    _clear_focus_record()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLIPBOARD FILES — what's copied, when what's copied is files
# ---------------------------------------------------------------------------

_CF_HDROP = 15
_CF_TEXT = 1
_CF_UNICODETEXT = 13
_MAX_CLIP_PATHS = 20


def _detect_clipboard_kind(user32) -> str:
    """Classify the clipboard via a user32-like object (the real
    ctypes.windll.user32 in production, a fake in tests) so the
    branches can be exercised anywhere. Returns one of:
    files / text / empty / other / unavailable. The clipboard is
    opened and closed here; nothing is read or changed."""
    if not user32.OpenClipboard(None):
        return "unavailable"
    try:
        if user32.IsClipboardFormatAvailable(_CF_HDROP):
            return "files"
        if (user32.IsClipboardFormatAvailable(_CF_UNICODETEXT)
                or user32.IsClipboardFormatAvailable(_CF_TEXT)):
            return "text"
        if user32.CountClipboardFormats() == 0:
            return "empty"
        return "other"
    finally:
        user32.CloseClipboard()


def _clipboard_file_paths():
    """The CF_HDROP path list, or None when it can't be read.
    Windows only; the caller has already classified the clipboard
    as holding files. The HDROP handle belongs to the clipboard —
    it is read, never freed."""
    import ctypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    user32.GetClipboardData.restype = ctypes.c_void_p
    shell32.DragQueryFileW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p,
        ctypes.c_uint]
    shell32.DragQueryFileW.restype = ctypes.c_uint
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(_CF_HDROP)
        if not handle:
            return None
        count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(count):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            if length <= 0:
                continue
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buffer, length + 1)
            if buffer.value:
                paths.append(buffer.value)
        return paths
    finally:
        user32.CloseClipboard()


def _summarise_paths(paths) -> str:
    """The clipboard-files receipt: every path up to the cap, with
    the remainder counted. Pure."""
    shown = paths[:_MAX_CLIP_PATHS]
    lines = [f"The clipboard holds {len(paths)} "
             f"file{'s' if len(paths) != 1 else ''}:"]
    lines.extend(f"• {p}" for p in shown)
    if len(paths) > _MAX_CLIP_PATHS:
        lines.append(f"…and {len(paths) - _MAX_CLIP_PATHS} more.")
    return "\n".join(lines)


def clipboard_files() -> str:
    """List the files on the clipboard, if files are what's copied."""
    if not _is_windows():
        return ("Reading the clipboard's file list this way needs "
                "Windows — this system is not Windows, so I can't "
                "check what's copied.")
    import ctypes

    kind = _detect_clipboard_kind(ctypes.windll.user32)
    if kind == "unavailable":
        return ("I couldn't open the clipboard — another app is "
                "holding it right now, so I can't say what's on it.")
    if kind == "text":
        return "The clipboard holds text, not files."
    if kind == "empty":
        return ("The clipboard is empty — nothing is copied "
                "right now.")
    if kind == "other":
        return ("The clipboard holds something that isn't files "
                "or text — an image, most likely.")
    paths = _clipboard_file_paths()
    if paths is None:
        return ("Windows says the clipboard holds files, but the "
                "file list couldn't be read.")
    if not paths:
        return ("Windows says the clipboard holds files, but the "
                "file list read back empty.")
    return _summarise_paths(paths)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Desktop-4 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="tile_windows",
        description="Arrange all open windows neatly on the primary monitor's work area (the screen minus the taskbar): 'grid' (default, near-square), 'columns' (side by side, full height) or 'rows' (stacked, full width). Minimized windows are restored first and counted in the reply; every window's position is read back after moving and any that didn't land in their tile are named. At most 12 windows are tiled — if more are open, the reply says so.",
        parameters={
            "type": "object",
            "properties": {
                "layout": {"type": "string", "enum": ["grid", "columns", "rows"], "description": "'grid' (default), 'columns' or 'rows'"},
            },
            "required": [],
        },
        handler=tile_windows,
    )
    registry.register(
        name="focus_session",
        description="A timed focus mode built from VYUK's existing parts: Focus Assist on (pop-up notifications muted), keep-awake for the duration, and at the end Focus Assist switches back off with a 'Focus session over' notification. action 'start' begins one (minutes, default 25, 1–480; starting while one runs reports the time left instead of stacking), 'status' reports whether one is active and how long is left, 'stop' ends it now and cancels the scheduled finish. Honest limit, stated in start's reply: the end timer lives in VYUK — if VYUK closes, Focus Assist would stay on until you ask for it off.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "status", "stop"], "description": "'start' (default), 'status' or 'stop'"},
                "minutes": {"type": "integer", "description": "For 'start': session length in minutes (default 25, 1–480)"},
            },
            "required": [],
        },
        handler=focus_session,
    )
    registry.register(
        name="clipboard_files",
        description="Say what files are on the clipboard, when files are what's copied (e.g. after Ctrl+C on files in Explorer): the paths, up to 20 with any remainder counted. If the clipboard holds text, is empty, or holds something else, the reply says which — it never pretends text is files. Read-only: the clipboard is never changed.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=clipboard_files,
    )
