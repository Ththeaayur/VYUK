"""
Desktop skills for VYUK (v5.11, Pack J) — window layouts:
save the current arrangement of windows under a name, put them back
later, list or delete saved layouts. (Locking lived here as lock_pc
until v5.30, when it merged into tools.py's lock_screen.)

Same rules as the other packs: plain functions returning
human-readable strings; platform-specific calls (ctypes user32) are
reached lazily and guarded, so this module imports cleanly on any OS
and the Windows-only skills return a clear fallback string elsewhere
instead of crashing. The window machinery is reused from
core/desktop/control.py (_enum_windows) in the established style of
monitor_control (ShowWindow restore, GetWindowRect, SetWindowPos).

Honesty rules, as everywhere in VYUK:
- Saving a layout records the windows that are actually there
  (visible and titled), capped at 100 with the cap disclosed. A
  minimized window's recorded position is its *normal* position
  (from GetWindowPlacement), not the off-screen spot Windows parks
  minimized windows at — the minimized flag is recorded alongside.
- Restoring never guesses: a recorded window is only moved when
  exactly one current window carries its exact title. Missing and
  multiply-matching titles are reported per window, and every moved
  window's position is read back and confirmed before the receipt
  calls it restored.
- Layouts live in one JSON file (data/window_layouts.json) keyed by
  name — a name is a key in that file, never a filename. A corrupt
  file is reported and treated as empty; invalid entries are dropped
  with a count (load-boundary discipline), never a crash.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


def _run(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if _is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — no console flash
    try:
        completed = subprocess.run(args, **kwargs)
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except FileNotFoundError:
        return -1, "", f"{args[0]} was not found on this PC."
    except subprocess.TimeoutExpired:
        return -2, "", f"{args[0]} took longer than {timeout} seconds and was stopped."
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def _tail(text: str, limit: int = 300) -> str:
    """The last few meaningful lines of a command's output, for honest reports."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-3:])[:limit]


# ---------------------------------------------------------------------------
# LAYOUT STORE — data/window_layouts.json, keyed by layout name
# ---------------------------------------------------------------------------

_MAX_WINDOWS = 100
_NAME_MAX = 40


def _layouts_path() -> Path:
    """Where saved layouts live — the app's data dir (core/config.py's
    DATA_DIR, the established convention), resolved fresh on every call."""
    from .config import DATA_DIR

    return DATA_DIR / "window_layouts.json"


def _name_error(name) -> str | None:
    """None when the layout name is usable, else the refusal string.
    A layout name is a key inside one JSON file — not a filename — so
    path separators are rejected rather than sanitised."""
    key = (name or "").strip() if isinstance(name, str) else ""
    if not key:
        return "A window layout needs a name, e.g. 'Desk' or 'Morning'."
    if len(key) > _NAME_MAX:
        return (f"That layout name is too long ({len(key)} characters) — "
                f"keep it to {_NAME_MAX} or fewer.")
    if "/" in key or "\\" in key:
        return ("A layout name can't contain '/' or '\\' — it's a label "
                "stored inside one data file, not a filename.")
    return None


def _layout_key(name) -> str:
    return (name or "").strip() if isinstance(name, str) else ""


def _as_int(value) -> int | None:
    """A stored coordinate as an int, or None when it isn't a whole number.
    Booleans are rejected even though bool subclasses int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _sanitise_window(raw) -> dict | None:
    """One stored window entry -> {title, left, top, width, height,
    minimized}, or None when it isn't a plausible recorded window."""
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    rect = {}
    for field in ("left", "top", "width", "height"):
        value = _as_int(raw.get(field))
        if value is None:
            return None
        rect[field] = value
    if rect["width"] <= 0 or rect["height"] <= 0:
        return None
    return {"title": title, **rect,
            "minimized": bool(raw.get("minimized", False))}


def _sanitise_layouts(raw) -> tuple[dict, int]:
    """Raw parsed JSON -> (clean layouts dict, dropped count). Every
    malformed layout or window is dropped and counted, never fatal."""
    if not isinstance(raw, dict):
        return {}, 0
    layouts: dict = {}
    dropped = 0
    for key, entry in raw.items():
        if not isinstance(key, str) or not key.strip():
            dropped += 1
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("windows"), list):
            dropped += 1
            continue
        windows = []
        for raw_window in entry["windows"]:
            window = _sanitise_window(raw_window)
            if window is None:
                dropped += 1
            else:
                windows.append(window)
        saved_at = entry.get("saved_at")
        layouts[key] = {
            "saved_at": saved_at if isinstance(saved_at, str) else "",
            "windows": windows,
        }
    return layouts, dropped


def _load_layouts(path: Path) -> tuple[dict, int, bool]:
    """Read the layouts file at *path* -> (layouts, dropped, corrupt).
    Missing file: ({}, 0, False). Unparseable or non-object JSON:
    ({}, 0, True) — reported and treated as empty, never a crash."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, 0, False
    except OSError:
        return {}, 0, True
    try:
        raw = json.loads(text)
    except ValueError:
        return {}, 0, True
    if not isinstance(raw, dict):
        return {}, 0, True
    layouts, dropped = _sanitise_layouts(raw)
    return layouts, dropped, False


def _save_layouts(path: Path, layouts: dict) -> str | None:
    """Persist layouts (temp file + os.replace, the house pattern).
    None on success, else the error text."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(layouts, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
        return None
    except OSError as e:
        return str(e)


def _store_note(dropped: int, corrupt: bool) -> str:
    """The disclosure sentence for a store that didn't read back clean."""
    if corrupt:
        return (" Note: the saved layouts file was unreadable (corrupt), "
                "so it was treated as empty.")
    if dropped:
        noun = "entry" if dropped == 1 else "entries"
        return (f" Note: {dropped} invalid saved {noun} were skipped "
                "when reading the layouts file.")
    return ""


def _fmt_saved_at(iso: str) -> str:
    """'2026-10-21T14:03:22' -> '21 Oct 2026, 14:03'; raw text when the
    stored value isn't a recognisable timestamp."""
    if not iso:
        return "an unknown date"
    try:
        parsed = datetime.datetime.fromisoformat(iso)
        return parsed.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return iso


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# WINDOW SEAMS — the only Windows-touching parts (monkeypatched in tests)
# ---------------------------------------------------------------------------

_SW_RESTORE = 9
_SWP_NOZORDER = 0x0004


def _window_rect(user32, hwnd: int, minimized: bool) -> dict | None:
    """A window's rect as {left, top, width, height}. For a minimized
    window this is its normal (restored) position from
    GetWindowPlacement — GetWindowRect would report the off-screen
    spot the minimized icon sits at, which is useless to restore to."""
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if minimized:
        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint),
                        ("flags", ctypes.c_uint),
                        ("showCmd", ctypes.c_uint),
                        ("ptMinPosition", wintypes.POINT),
                        ("ptMaxPosition", wintypes.POINT),
                        ("rcNormalPosition", wintypes.RECT)]

        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            return None
        rect = placement.rcNormalPosition
    elif not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return {"left": rect.left, "top": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top}


def _current_windows() -> list[dict]:
    """Every visible, titled window right now: {hwnd, title, left, top,
    width, height, minimized}. Windows only; [] elsewhere or on failure.
    Reuses core/desktop/control.py's enumerator (which already returns only
    visible windows that have a title)."""
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
                rect = _window_rect(user32, hwnd, minimized)
            except Exception:  # noqa: BLE001 — one bad window skips, not all
                continue
            if rect is None:
                continue
            windows.append({"hwnd": hwnd, "title": title, **rect,
                            "minimized": minimized})
        return windows
    except Exception:  # noqa: BLE001
        return []


def _apply_window_restore(win: dict, rect: dict) -> dict | None:
    """Move one current window to the recorded rect and read the result
    back: the actual rect afterwards, or None when Windows refused the
    move or the read-back failed. Un-minimizes first (SW_RESTORE),
    then SetWindowPos with SWP_NOZORDER, like monitor_control."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = win["hwnd"]
        if win.get("minimized"):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        if not user32.SetWindowPos(hwnd, 0, rect["left"], rect["top"],
                                   rect["width"], rect["height"],
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


# ---------------------------------------------------------------------------
# RESTORE PLANNING — pure matching + receipt wording
# ---------------------------------------------------------------------------


def _plan_restore(recorded: list[dict], current: list[dict]) -> list[dict]:
    """Match every recorded window to at most one current window by
    EXACT title. Pure — no OS calls. Each plan item is one of:
      {"status": "restore",   "hwnd", "current", "rect"}  move this one
      {"status": "missing",   }  no unused current window has the title
      {"status": "ambiguous", "count"}  several current windows do
    A current window is consumed by the first recorded entry that
    claims it, so two identical saved titles can never both be aimed
    at the one window that happens to be open."""
    used: set = set()
    plan = []
    for entry in recorded:
        matches = [w for w in current
                   if w["title"] == entry["title"] and w["hwnd"] not in used]
        if len(matches) == 1:
            win = matches[0]
            used.add(win["hwnd"])
            plan.append({
                "title": entry["title"], "status": "restore",
                "hwnd": win["hwnd"], "current": win,
                "rect": {f: entry[f] for f in ("left", "top", "width", "height")},
            })
        elif len(matches) > 1:
            plan.append({"title": entry["title"], "status": "ambiguous",
                         "count": len(matches)})
        else:
            plan.append({"title": entry["title"], "status": "missing"})
    return plan


def _rect_text(rect: dict) -> str:
    return (f"({rect['left']}, {rect['top']}) "
            f"{rect['width']}x{rect['height']}")


def _confirm_restore(title: str, wanted: dict,
                     actual: dict | None) -> tuple[bool, str]:
    """Judge one moved window by its read-back rect -> (restored?, line).
    A window only counts as restored when it reads back exactly where
    it was saved."""
    if actual is None:
        return False, (f"• '{title}' couldn't be moved — Windows refused "
                       "the move or its new position couldn't be read back.")
    if all(actual[f] == wanted[f] for f in ("left", "top", "width", "height")):
        return True, f"• Restored '{title}' to {_rect_text(wanted)}."
    return False, (f"• '{title}' was moved, but it now sits at "
                   f"{_rect_text(actual)} — not exactly the saved spot "
                   f"({_rect_text(wanted)}).")


def _summarise_restore(name: str, results: list[dict]) -> str:
    """Render the per-item honest receipt for a restore attempt.
    *results* covers every recorded window, in recorded order, each
    {"title", "status", "line"? , "count"?} with status one of
    restored / mismatch / failed / missing / ambiguous."""
    total = len(results)
    restored = sum(1 for r in results if r["status"] == "restored")
    lines = [f"Restored {restored} of {total} windows from layout '{name}'."]
    for r in results:
        if r["status"] in ("restored", "mismatch", "failed"):
            lines.append(r["line"])
    missing = [r["title"] for r in results if r["status"] == "missing"]
    if missing:
        listed = ", ".join(f"'{t}'" for t in missing)
        lines.append(f"Not open: {listed}.")
    for r in results:
        if r["status"] == "ambiguous":
            lines.append(
                f"Ambiguous: '{r['title']}' ({r['count']} windows share "
                "that title) — skipped, so the wrong window is never moved.")
    return "\n".join(lines)


def _available_line(layouts: dict) -> str:
    """'Saved layouts: 'Desk', 'Morning'.' for unknown-name replies."""
    if not layouts:
        return "No layouts are saved yet."
    names = ", ".join(f"'{k}'" for k in sorted(layouts, key=str.lower))
    return f"Saved layouts: {names}."


# ---------------------------------------------------------------------------
# SAVE / RESTORE / LIST / DELETE
# ---------------------------------------------------------------------------


def save_window_layout(name: str) -> str:
    """Record the current window arrangement under a layout name."""
    if (error := _name_error(name)) is not None:
        return error
    if not _is_windows():
        return f"Saving a window layout needs Windows — {_NOT_WINDOWS}"
    key = _layout_key(name)
    current = _current_windows()
    if not current:
        return "No visible windows with titles are open right now, so there is nothing to save."
    path = _layouts_path()
    layouts, dropped, corrupt = _load_layouts(path)
    windows = [{
        "title": w["title"], "left": w["left"], "top": w["top"],
        "width": w["width"], "height": w["height"],
        "minimized": bool(w["minimized"]),
    } for w in current[:_MAX_WINDOWS]]
    previous = layouts.get(key)
    layouts[key] = {"saved_at": _now_iso(), "windows": windows}
    if (write_error := _save_layouts(path, layouts)) is not None:
        return f"Could not save the layout file: {write_error}"
    count = len(windows)
    if previous is not None:
        result = (f"Updated layout '{key}' (was saved "
                  f"{_fmt_saved_at(previous.get('saved_at', ''))}) — "
                  f"{count} window{'s' if count != 1 else ''} recorded.")
    else:
        result = (f"Saved layout '{key}' — "
                  f"{count} window{'s' if count != 1 else ''} recorded.")
    if len(current) > _MAX_WINDOWS:
        result += (f" Only the first {_MAX_WINDOWS} windows were saved; "
                   f"{len(current) - _MAX_WINDOWS} were left out.")
    return result + _store_note(dropped, corrupt)


def restore_window_layout(name: str) -> str:
    """Put the windows recorded in a saved layout back where they were."""
    if (error := _name_error(name)) is not None:
        return error
    if not _is_windows():
        return f"Restoring a window layout needs Windows — {_NOT_WINDOWS}"
    key = _layout_key(name)
    path = _layouts_path()
    layouts, dropped, corrupt = _load_layouts(path)
    entry = layouts.get(key)
    if entry is None:
        return (f"No layout called '{key}'. "
                + _available_line(layouts) + _store_note(dropped, corrupt))
    recorded = entry["windows"]
    if not recorded:
        return (f"Layout '{key}' has no windows recorded in it, "
                "so there is nothing to restore.")
    current = _current_windows()
    results = []
    for item in _plan_restore(recorded, current):
        if item["status"] == "restore":
            actual = _apply_window_restore(item["current"], item["rect"])
            ok, line = _confirm_restore(item["title"], item["rect"], actual)
            if actual is None:
                status = "failed"
            elif ok:
                status = "restored"
            else:
                status = "mismatch"
            results.append({"title": item["title"], "status": status,
                            "line": line})
        else:
            results.append({"title": item["title"],
                            "status": item["status"],
                            "count": item.get("count", 0)})
    return _summarise_restore(key, results) + _store_note(dropped, corrupt)


def list_window_layouts() -> str:
    """List the saved window layouts: name, when saved, window count."""
    path = _layouts_path()
    layouts, dropped, corrupt = _load_layouts(path)
    if not layouts:
        return ("No window layouts saved yet — ask me to save the "
                "current layout under a name first."
                + _store_note(dropped, corrupt))
    lines = [f"Saved window layouts ({len(layouts)}):"]
    for key in sorted(layouts, key=str.lower):
        entry = layouts[key]
        count = len(entry["windows"])
        lines.append(
            f"• '{key}' — saved {_fmt_saved_at(entry.get('saved_at', ''))} "
            f"— {count} window{'s' if count != 1 else ''}")
    return "\n".join(lines) + _store_note(dropped, corrupt)


def delete_window_layout(name: str) -> str:
    """Delete one saved window layout by name."""
    if (error := _name_error(name)) is not None:
        return error
    key = _layout_key(name)
    path = _layouts_path()
    layouts, dropped, corrupt = _load_layouts(path)
    entry = layouts.get(key)
    if entry is None:
        return (f"No layout called '{key}' to delete. "
                + _available_line(layouts) + _store_note(dropped, corrupt))
    count = len(entry["windows"])
    saved = _fmt_saved_at(entry.get("saved_at", ""))
    del layouts[key]
    if (write_error := _save_layouts(path, layouts)) is not None:
        return f"Could not update the layout file: {write_error}"
    return (f"Deleted layout '{key}' — it held {count} "
            f"window{'s' if count != 1 else ''}, saved {saved}."
            + _store_note(dropped, corrupt))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Window Layouts (Pack J) tool on a
    ToolRegistry-like object."""
    registry.register(
        name="save_window_layout",
        description="Save the current arrangement of open windows (each window's title and position, including minimized windows' normal positions) as a named layout, so it can be restored later with restore_window_layout. Saving over an existing name replaces it, and the result says so. At most 100 windows are recorded.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the layout, e.g. 'Desk' or 'Morning' (max 40 characters, no slashes)"},
            },
            "required": ["name"],
        },
        handler=save_window_layout,
    )
    registry.register(
        name="restore_window_layout",
        description="Restore a saved window layout: every recorded window that is open under exactly the same title is moved back to its saved position and the result is confirmed by reading the position back. Windows that aren't open, or whose title is shared by several open windows, are reported and skipped — never guessed at.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the layout to restore, e.g. 'Desk'"},
            },
            "required": ["name"],
        },
        handler=restore_window_layout,
    )
    registry.register(
        name="list_window_layouts",
        description="List the saved window layouts: each layout's name, when it was saved, and how many windows it holds. Put one back with restore_window_layout; capture the current arrangement with save_window_layout.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=list_window_layouts,
    )
    registry.register(
        name="delete_window_layout",
        description="Delete one saved window layout by name. The windows themselves are not touched — only the saved record is removed. See the saved ones with list_window_layouts.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the layout to delete, e.g. 'Desk'"},
            },
            "required": ["name"],
        },
        handler=delete_window_layout,
    )
