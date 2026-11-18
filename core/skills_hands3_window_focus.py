"""Window-focus implementations for the hands3 skills pack.

Split out of core/skills_window_control.py by SKILLS SPLIT PHASE A BATCH 1
(window-manip + window-focus). Same functions and tables, verbatim —
only the module-level import blocks are new. The capture-scroll helpers
this module calls live in the capture-scroll module, and the window
helpers in the window-manip module (SKILLS SPLIT PHASE A BATCH 2); the
cross-imports sit at the bottom, after all definitions, so this module
imports cleanly in any order — facade-first or standalone.
"""

from pathlib import Path

# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (moved constant):
# _MAX_FOCUS_WINDOWS lived in the facade (core/skills_window_control.py) through
# batch 1; it is used only by this module's focus search, so batch 2
# defines it here.
_MAX_FOCUS_WINDOWS = 12


def focus_window_by_text(text: str, title_hint: str = "") -> str:
    """Bring forward the window whose on-screen text contains `text`.

    DESK-03/DESK-04 rewrite: the search inspects candidates
    WITHOUT bringing any of them forward — one all-screens frame
    is grabbed once and OCR'd per candidate rect
    (_ocr_words_in_rect), so the user's foreground is undisturbed
    while searching. A match centre is trusted only when no
    window in front of the candidate covers that point (the
    composited frame shows the front window's pixels there, not
    the candidate's); centres hidden behind a front window are
    distrusted and counted, never attributed to the window
    behind. Only after the search decides is anything activated,
    exactly once, for a single unambiguous match — and that
    activation is still verified with the fg == hwnd check plus
    the v5.48 focus-expectation recording. Tie policy (decided,
    owner-vetoable): several matching windows = NO focus at all;
    the result names every candidate and asks for the exact
    title or a more specific phrase. A single window with the
    phrase in several places is still one window, so it IS
    focused — but the result lists every place, never a silent
    'first of N' pick. Minimised windows are never candidates
    (checking one would itself disturb the foreground) and are
    reported as unchecked. On every non-success path — no match,
    ambiguity, refusal, OCR error — the original foreground is
    restored if it moved at all during the search."""
    phrase = (text or "").strip()
    if not phrase:
        return "Tell me what text to look for inside a window."
    if not _is_windows():
        return (f"Searching windows by their contents uses Windows "
                f"APIs — {_NOT_WINDOWS}")
    blocked = _input_blocked()
    if blocked:
        return blocked
    user32 = None
    original = None
    activated = False
    try:
        from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

        user32 = _user32()
        original = _foreground_hwnd()
        windows = _enum_windows()
        hint = (title_hint or "").strip().lower()
        monitors = _monitors()
        frame, frame_err = _grab_desktop_frame()
        if frame_err:
            return frame_err
        checked = skipped_minimised = skipped_no_rect = 0
        occluded_total = 0
        matches = []  # (hwnd, title, trusted centres)
        front_rects = []  # rects of non-minimised windows in front
        for hwnd, title in windows:
            if checked >= _MAX_FOCUS_WINDOWS:
                break
            try:
                if user32.IsIconic(hwnd):
                    skipped_minimised += 1
                    continue
            except Exception:  # noqa: BLE001 — as before, an
                pass  # unreadable iconic state is not minimised
            rect = _window_rect(hwnd)
            if rect is None:
                skipped_no_rect += 1
                continue
            if hint and hint not in (title or "").lower():
                # Scoped out by title, but still in front: its
                # pixels can hide a later candidate's text.
                front_rects.append(rect)
                continue
            checked += 1
            words, err = _ocr_words_in_rect(frame, rect, monitors)
            if err:
                return err
            centres = _phrase_centres(words, phrase)
            left, top, right, bottom = rect
            trusted = [(x, y) for x, y in centres
                       if not any(l <= x < r and t <= y < b
                                  for l, t, r, b in front_rects)]
            occluded_total += len(centres) - len(trusted)
            if trusted:
                matches.append((hwnd, title, trusted))
            front_rects.append(rect)
        tail = ""
        if skipped_minimised:
            tail += (f" {skipped_minimised} minimised window(s) weren't "
                     "checked.")
        if skipped_no_rect:
            tail += (f" {skipped_no_rect} window(s) couldn't be "
                     "measured and weren't checked.")
        if hint:
            tail += (f" Only windows with '{title_hint.strip()}' in "
                     "the title were checked.")
        if occluded_total:
            tail += (f" {occluded_total} match(es) hidden behind a "
                     "window in front weren't trusted.")
        if len(windows) > _MAX_FOCUS_WINDOWS and \
                checked >= _MAX_FOCUS_WINDOWS:
            tail += (f" Only the first {_MAX_FOCUS_WINDOWS} windows "
                     "were checked.")
        if not matches:
            return (f"Checked {checked} window(s) by their on-screen "
                    f"text — none shows '{phrase}'.{tail} OCR can "
                    "also miss small or stylised text.")
        if len(matches) > 1:
            listing = "; ".join(
                f"'{title}' at about ({centres[0][0]}, {centres[0][1]})"
                for _hwnd, title, centres in matches)
            return (f"'{phrase}' matches {len(matches)} windows: "
                    f"{listing}. I didn't bring any of them forward "
                    "— say the exact window title, or a more "
                    "specific phrase, so the right window is used.")
        hwnd, title, centres = matches[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE — the ONLY activation
        user32.SetForegroundWindow(hwnd)
        try:
            fg = user32.GetForegroundWindow()
        except Exception:  # noqa: BLE001
            fg = None
        if fg != hwnd:
            holder = _foreground_hwnd()
            holder_name = ((_title_of(holder) if holder
                            else "") or "another window")
            return (f"'{phrase}' is visible in '{title}' at about "
                    f"({centres[0][0]}, {centres[0][1]}), but Windows "
                    "refused to bring that window forward — focus "
                    f"stayed on '{holder_name}'. Click it or its "
                    "taskbar button to switch.")
        activated = True
        # v5.48 (programme item 22): this focus was verified
        # (fg == hwnd) — record the focus expectation, as
        # window_control's focus path does, so a later
        # type_text/press_hotkey can refuse if focus has since
        # moved elsewhere.
        try:
            from .desktop.control import note_expected_foreground  # Phase 3 D4a: control hub moved to core/desktop/

            note_expected_foreground()
        except Exception:  # noqa: BLE001 — best effort
            pass
        x, y = centres[0]
        if len(centres) > 1:
            rest = "; ".join(f"({cx}, {cy})" for cx, cy in centres[1:])
            more = (f" (and {len(centres) - 1} more place(s) in the "
                    f"same window: {rest})")
        else:
            more = ""
        return (f"Brought '{title}' to the front — '{phrase}' is on "
                f"its screen at about ({x}, {y}){more}.")
    except Exception as e:  # noqa: BLE001
        return f"Could not search the windows: {e}"
    finally:
        if not activated and user32 is not None:
            _restore_foreground(user32, original)


# ---------------------------------------------------------------------------
# HOTKEY CHEATSHEET — a static table for the common apps, keyed by
# the focused app's process name, with a general Windows set as the
# honest fallback
# ---------------------------------------------------------------------------

_GENERIC_SHORTCUTS = (
    ("Win+D", "show the desktop"),
    ("Win+E", "open File Explorer"),
    ("Win+I", "open Settings"),
    ("Win+L", "lock the PC"),
    ("Alt+Tab", "switch between apps"),
    ("Win+Tab", "task view — all windows and desktops"),
    ("Ctrl+Shift+Esc", "open Task Manager"),
    ("Win+← / Win+→", "snap the window to a screen half"),
    ("Win+Shift+S", "take a screenshot of an area"),
    ("Win+V", "clipboard history"),
    ("Win+.", "emoji and symbols panel"),
    ("Alt+F4", "close the current app"),
)

_APP_SHORTCUTS = {
    "chrome.exe": ("Google Chrome", (
        ("Ctrl+T", "new tab"), ("Ctrl+W", "close tab"),
        ("Ctrl+Shift+T", "reopen the last closed tab"),
        ("Ctrl+Tab", "next tab"), ("Ctrl+L", "jump to the address bar"),
        ("Ctrl+Shift+N", "new incognito window"),
        ("Ctrl+D", "bookmark this page"), ("Ctrl+H", "history"),
        ("Ctrl+J", "downloads"),
        ("Ctrl+Shift+Delete", "clear browsing data"),
    )),
    "msedge.exe": ("Microsoft Edge", (
        ("Ctrl+T", "new tab"), ("Ctrl+W", "close tab"),
        ("Ctrl+Shift+T", "reopen the last closed tab"),
        ("Ctrl+L", "jump to the address bar"),
        ("Ctrl+Shift+N", "new InPrivate window"),
        ("Ctrl+D", "add this page to favourites"),
        ("Ctrl+H", "history"), ("Ctrl+J", "downloads"),
        ("Ctrl+Shift+B", "show or hide the favourites bar"),
    )),
    "firefox.exe": ("Firefox", (
        ("Ctrl+T", "new tab"), ("Ctrl+W", "close tab"),
        ("Ctrl+Shift+T", "reopen the last closed tab"),
        ("Ctrl+L", "jump to the address bar"),
        ("Ctrl+Shift+P", "new private window"),
        ("Ctrl+D", "bookmark this page"),
        ("Ctrl+H", "history sidebar"), ("Ctrl+J", "downloads"),
        ("Ctrl+Shift+A", "add-ons manager"),
    )),
    "code.exe": ("Visual Studio Code", (
        ("Ctrl+P", "quick-open a file by name"),
        ("Ctrl+Shift+P", "command palette"),
        ("Ctrl+`", "toggle the terminal"),
        ("Ctrl+B", "toggle the sidebar"),
        ("Ctrl+/", "comment or uncomment the line"),
        ("Alt+↑ / Alt+↓", "move the line up or down"),
        ("Ctrl+D", "select the next occurrence of the word"),
        ("Ctrl+Shift+F", "search across all files"),
        ("F5", "start debugging"), ("Ctrl+S", "save"),
    )),
    "notepad.exe": ("Notepad", (
        ("Ctrl+N", "new document"), ("Ctrl+O", "open a file"),
        ("Ctrl+S", "save"), ("Ctrl+F", "find"),
        ("Ctrl+H", "find and replace"), ("Ctrl+A", "select all"),
        ("Ctrl+Z", "undo"), ("Ctrl+scroll", "zoom in or out"),
    )),
    "winword.exe": ("Microsoft Word", (
        ("Ctrl+S", "save"), ("Ctrl+F", "find"),
        ("Ctrl+H", "find and replace"), ("Ctrl+K", "insert a hyperlink"),
        ("Ctrl+B / Ctrl+I / Ctrl+U", "bold / italic / underline"),
        ("Ctrl+Enter", "insert a page break"),
        ("F7", "spelling and grammar check"), ("Ctrl+Z", "undo"),
    )),
    "excel.exe": ("Microsoft Excel", (
        ("Ctrl+S", "save"), ("Ctrl+F", "find"),
        ("Ctrl+;", "insert today's date"),
        ("Ctrl+Shift+L", "toggle filters"),
        ("Alt+=", "AutoSum the cells above"),
        ("F2", "edit the current cell"),
        ("Ctrl+←/→/↑/↓", "jump to the edge of the data"),
        ("Ctrl+PageUp / Ctrl+PageDown", "switch sheets"),
        ("Ctrl+1", "format cells dialog"),
    )),
    "powerpnt.exe": ("Microsoft PowerPoint", (
        ("F5", "start the slideshow from the beginning"),
        ("Shift+F5", "start the slideshow from this slide"),
        ("Ctrl+M", "new slide"), ("Ctrl+D", "duplicate the slide"),
        ("Ctrl+K", "insert a hyperlink"),
        ("Ctrl+P", "pen tool during a show"), ("Esc", "end the show"),
        ("Ctrl+S", "save"),
    )),
    "outlook.exe": ("Microsoft Outlook", (
        ("Ctrl+N", "new email (from the mail view)"),
        ("Ctrl+R", "reply"), ("Ctrl+Shift+R", "reply all"),
        ("Ctrl+F", "forward"), ("Ctrl+E", "search"),
        ("Ctrl+Shift+V", "move to a folder"),
        ("Ctrl+1 / Ctrl+2 / Ctrl+3", "mail / calendar / people"),
    )),
    "explorer.exe": ("File Explorer", (
        ("F2", "rename the selected item"),
        ("Ctrl+Shift+N", "new folder"),
        ("Ctrl+N", "new Explorer window"),
        ("Alt+←", "back"), ("Alt+↑", "up one folder level"),
        ("Ctrl+L", "jump to the address bar"),
        ("Ctrl+A", "select all"),
        ("Shift+Delete", "delete permanently, skipping the bin"),
        ("F11", "full screen"),
    )),
    "windowsterminal.exe": ("Windows Terminal", (
        ("Ctrl+Shift+T", "new tab"), ("Ctrl+Tab", "next tab"),
        ("Ctrl+W", "close the current pane or tab"),
        ("Alt+Shift++", "split the pane"),
        ("Ctrl+Shift+F", "find in the output"),
        ("Ctrl+C", "copy the selection (or interrupt)"),
        ("Ctrl+V", "paste"),
    )),
    "powershell.exe": ("Windows PowerShell", (
        ("Ctrl+R", "search the command history backwards"),
        ("Ctrl+C", "stop the running command"),
        ("Ctrl+L", "clear the screen"),
        ("Tab", "complete the command or path"),
        ("F7", "show the command history"),
        ("Ctrl+V", "paste"),
    )),
    "slack.exe": ("Slack", (
        ("Ctrl+K", "jump to a conversation"),
        ("Ctrl+F", "search in this channel"),
        ("Ctrl+Shift+A", "all unread messages"),
        ("Alt+↑ / Alt+↓", "move between channels"),
        ("Ctrl+U", "mark this channel unread"),
        ("Ctrl+Shift+M", "activity and mentions"),
    )),
    "discord.exe": ("Discord", (
        ("Ctrl+K", "quick switcher"),
        ("Ctrl+F", "search in this channel"),
        ("Alt+↑ / Alt+↓", "move between channels"),
        ("Ctrl+Shift+M", "toggle mute"),
        ("Ctrl+Shift+D", "toggle deafen"),
        ("Ctrl+/", "show the full shortcut list"),
    )),
    "zoom.exe": ("Zoom", (
        ("Alt+A", "mute or unmute yourself"),
        ("Alt+V", "camera on or off"),
        ("Alt+S", "share or stop sharing the screen"),
        ("Alt+R", "start or stop recording"),
        ("Alt+H", "show or hide the chat panel"),
        ("Alt+F", "full screen"), ("Alt+Q", "end or leave the meeting"),
    )),
    "spotify.exe": ("Spotify", (
        ("Space", "play or pause"), ("Ctrl+→", "next track"),
        ("Ctrl+←", "previous track"),
        ("Ctrl+↑ / Ctrl+↓", "volume up or down"),
        ("Ctrl+L", "search"), ("Ctrl+N", "new playlist"),
    )),
    "vlc.exe": ("VLC media player", (
        ("Space", "play or pause"), ("F", "full screen"),
        ("M", "mute"), ("S", "stop"),
        ("Ctrl+O", "open a file"), ("Ctrl+↑ / Ctrl+↓", "volume"),
        ("T", "show the time and position"),
        ("Ctrl+P", "preferences"),
    )),
}

_TITLE_HINTS = (
    ("visual studio code", "code.exe"),
    ("google chrome", "chrome.exe"),
    ("mozilla firefox", "firefox.exe"),
    ("microsoft word", "winword.exe"),
    ("microsoft excel", "excel.exe"),
    ("microsoft powerpoint", "powerpnt.exe"),
    ("windows terminal", "windowsterminal.exe"),
    ("vlc media player", "vlc.exe"),
)

_CHEATSHEET_APPS_LINE = ("Chrome, Edge, Firefox, VS Code, Notepad, "
                         "Word, Excel, PowerPoint, Outlook, File "
                         "Explorer, Windows Terminal, PowerShell, "
                         "Slack, Discord, Zoom, Spotify and VLC")


def _foreground_process_name():
    """Exe basename (lowercase) of the foreground window's process,
    or None. Windows only; best-effort, never raises."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)):
                return None
            return Path(buf.value).name.lower() or None
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def _format_shortcuts(pairs) -> str:
    return "\n".join(f"• {keys} — {what}" for keys, what in pairs)


def hotkey_cheatsheet() -> str:
    """Useful shortcuts for the focused app, from a built-in table."""
    exe = _foreground_process_name()
    title = ""
    if not _is_windows():
        return ("I can't tell which app is in focus on this system "
                "(reading the focused app needs Windows), so here are "
                "the general Windows shortcuts instead:\n"
                + _format_shortcuts(_GENERIC_SHORTCUTS))
    try:
        from .desktop.control import _foreground_title  # Phase 3 D4a: control hub moved to core/desktop/

        title = _foreground_title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    key = exe if exe in _APP_SHORTCUTS else None
    if key is None and title:
        lowered = title.lower()
        for fragment, candidate in _TITLE_HINTS:
            if fragment in lowered:
                key = candidate
                break
    if key is not None:
        display, pairs = _APP_SHORTCUTS[key]
        return (f"Shortcuts for {display} (the app in focus), from "
                "VYUK's built-in cheat sheet — most apps let "
                "you remap these:\n" + _format_shortcuts(pairs))
    who = title or exe or "that app"
    return (f"The app in focus ('{who}') isn't in my built-in cheat "
            f"sheet, which covers {_CHEATSHEET_APPS_LINE} — so here "
            "are the general Windows shortcuts instead:\n"
            + _format_shortcuts(_GENERIC_SHORTCUTS))


# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (cycle-free cross-imports):
# this module imports capture-scroll and window-manip helpers; every
# module's own definitions precede its sibling imports — these imports sit
# at the bottom, after all definitions, so importing in any order works.
from .skills_hands3_capture_scroll import (
    _NOT_WINDOWS,
    _grab_desktop_frame,
    _input_blocked,
    _ocr_words_in_rect,
    _phrase_centres,
    _restore_foreground,
)
from .skills_hands3_window_manip import (
    _foreground_hwnd,
    _is_windows,
    _monitors,
    _title_of,
    _user32,
    _window_rect,
)
