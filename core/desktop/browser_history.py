"""Browser history + bookmarks reader (R10-3, browser-history port).

Own stdlib code (sqlite3 + json + shutil + tempfile). NOTHING from
browser-history/browser-history 0.5.0 is adopted or vendored: the
port takes its two history SQL queries, its two bookmark readers and
its copy-to-temp discipline, and drops the rest — above all the
profile auto-discovery, which walks the home directory looking for
browser profiles. The tree's browser automation states the discipline
the port follows: a profile dir is never guessed and never defaulted
(core/desktop/browser_automation.py, _launch_persistent_context)
— the caller names the profile folder on every call.

Upstream (read, not adopted): browser-history 0.5.0, Apache-2.0
(Copyright 2020 Samyak Sarnayak, LICENSE read from master), wheel
22,404 B, zero runtime dependencies. The queries below are
upstream-faithful: Chromium History (urls + visits, Chrome-epoch
visit_time, visit_duration > 0) and Firefox places.sqlite
(moz_historyvisits + moz_places); Chromium Bookmarks JSON (recursive
roots walk, Chrome-epoch date_added) and Firefox moz_bookmarks.

PRIVACY DISCIPLINE — reading the user's browser history is the most
privacy-sensitive read in the tree, so this module is the strictest
reader in it:
- Dedicated opt-in, OFF by default: config
  ``browser_allow_history_read`` first, then the
  ``VYUK_ALLOW_BROWSER_HISTORY`` environment variable, else False
  (the same shape as the real-Chrome attach opt-in in
  browser_cdp.py). This is a SEPARATE opt-in from the attach: the
  attach reads the live session; history reaches back weeks and
  months, including browsing from when VYUK was not running. A
  wider read gets its own switch. With it off, no file is touched.
- The profile dir is a REQUIRED argument on every call — never
  guessed, never defaulted, never remembered. There is no roots
  file (unlike disk_index) and no auto-discovery. Point at the
  profile folder itself, the one containing the History file, e.g.
  ``...\\Google\\Chrome\\User Data\\Default``.
- Copy, never touch the live file: the History / places.sqlite /
  Bookmarks file is copied with shutil.copy2 into a fresh
  tempfile.TemporaryDirectory, the COPY is opened read-only
  (``mode=ro&immutable=1&nolock=1``), and the temp dir is deleted
  on exit. The live file is never opened by this module — the
  A4-filestat check re-reads its size + mtime after the read and
  the success line is only written when both are identical. The
  profile is never written into.
- Read-only, local-only, no auto-read, no watcher, no background
  polling, no scoring of what he browsed, no exfiltration. The
  rows are untrusted producers ("web page") and egress reads under
  taint, exactly like browser_console / browser_network (R10-1).
- URLs are returned as stored: the URL IS the answer the user
  asked for (a video's watch id lives in its query string), so
  unlike R10-1's network rows the query string is NOT stripped.
  The protection here is the opt-in + local-only +
  no-exfiltration, not redaction of the user's own data from
  himself.

SCOPE: Chromium (Chrome / Edge / Brave / Vivaldi — one History +
Bookmarks schema) and Firefox (places.sqlite for both history and
bookmarks). Safari has no Windows build, so it is out of scope —
honest, not claimed. The lab has no Chrome and no real browser
profile; the suite drives synthetic History / places.sqlite /
Bookmarks files.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import tempfile

from .. import config_spec

__all__ = [
    "browser_history",
    "browser_bookmarks",
    "register_all",
]

_HISTORY_ENV = "VYUK_ALLOW_BROWSER_HISTORY"

_HISTORY_REFUSAL = (
    "Reading your browser history is an explicit opt-in and it is "
    "OFF by default — I didn't read anything, and no browser "
    "profile was touched. To allow it, turn on "
    "'browser_allow_history_read' in Settings/config (or set "
    "VYUK_ALLOW_BROWSER_HISTORY=1), then ask again with the "
    "profile folder named — I never guess a profile, so the folder "
    "has to come from you every time."
)

# Window and entry caps. History is a one-shot read of a window the
# caller names (default 7 days, hard cap 90); bookmarks are not
# visits, so they carry no window, only an entry cap.
_DEFAULT_DAYS = 7
_MAX_DAYS = 90
_DEFAULT_HISTORY_LIMIT = 50
_DEFAULT_BOOKMARK_LIMIT = 100
_MAX_ENTRIES = 200

# Upstream-faithful queries (browser-history 0.5.0, generic.py /
# browsers.py). Chromium visit_time is Chrome-epoch microseconds
# (1601-01-01); Firefox visit_date is Unix-epoch microseconds.
# Both return (visit_time, url, title) as local-time text.
_CHROMIUM_HISTORY_SQL = """
    SELECT
        datetime(
            visits.visit_time/1000000-11644473600, 'unixepoch', 'localtime'
        ) as 'visit_time',
        urls.url,
        urls.title
    FROM
        visits INNER JOIN urls ON visits.url = urls.id
    WHERE
        visits.visit_duration > 0
    ORDER BY
        visit_time DESC
"""

_FIREFOX_HISTORY_SQL = """
    SELECT
        datetime(
            visit_date/1000000, 'unixepoch', 'localtime'
        ) AS 'visit_time',
        url,
        moz_places.title
    FROM
        moz_historyvisits
    INNER JOIN
        moz_places
    ON
        moz_historyvisits.place_id = moz_places.id
    WHERE
        visit_date IS NOT NULL AND url LIKE 'http%' AND title IS NOT NULL
"""

_FIREFOX_BOOKMARKS_SQL = """
    SELECT
        datetime(
            moz_bookmarks.dateAdded/1000000,'unixepoch','localtime'
        ) AS added_time,
        url, moz_bookmarks.title, moz_folder.title
    FROM
        moz_bookmarks JOIN moz_places, moz_bookmarks as moz_folder
    ON
        moz_bookmarks.fk = moz_places.id
        AND moz_bookmarks.parent = moz_folder.id
    WHERE
        moz_bookmarks.dateAdded IS NOT NULL AND url LIKE 'http%'
        AND moz_bookmarks.title IS NOT NULL
"""

_CHROME_EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


def _browser_history_allowed() -> bool:
    """The history-read opt-in. Default OFF: the spec field
    ``browser_allow_history_read`` (env VYUK_ALLOW_BROWSER_HISTORY),
    read live at call time. Anything unrecognised reads as OFF.
    (The config name the reader used to expect never existed as a
    field, so the env var was always the live control; the spec
    field now exists, built from the same var at import.)"""
    return config_spec.env(_HISTORY_ENV)


def _clamp_days(days) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        return _DEFAULT_DAYS
    return max(1, min(_MAX_DAYS, n))


def _clamp_limit(limit, default: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(_MAX_ENTRIES, n))


def _parse_visit_time(text):
    """SQLite datetime(..., 'localtime') text -> naive local datetime."""
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _chrome_epoch_to_local(microseconds) -> datetime.datetime:
    """Chrome-epoch microseconds (1601-01-01) -> naive local datetime."""
    aware = _CHROME_EPOCH + datetime.timedelta(microseconds=int(microseconds))
    return aware.astimezone().replace(tzinfo=None)


def _with_copied_db(src_path, fn):
    """Run fn(conn) against a COPY of src_path, never the live file.

    The copy lives in a fresh TemporaryDirectory (deleted on exit)
    and is opened read-only + immutable, so even the copy cannot be
    written. Returns whatever fn returns.
    """
    with tempfile.TemporaryDirectory(prefix="vyuk-history-") as tmpdir:
        copied = shutil.copy2(src_path, tmpdir)
        conn = sqlite3.connect(
            f"file:{copied}?mode=ro&immutable=1&nolock=1", uri=True
        )
        try:
            return fn(conn)
        finally:
            conn.close()


def _read_chromium_history(copy_path):
    def _run(conn):
        cursor = conn.cursor()
        cursor.execute(_CHROMIUM_HISTORY_SQL)
        rows = []
        for when_text, url, title in cursor.fetchall():
            when = _parse_visit_time(when_text)
            if when is None or not url:
                continue
            rows.append((when, url, title or ""))
        return rows

    return _with_copied_db(copy_path, _run)


def _read_firefox_history(copy_path):
    def _run(conn):
        cursor = conn.cursor()
        cursor.execute(_FIREFOX_HISTORY_SQL)
        rows = []
        for when_text, url, title in cursor.fetchall():
            when = _parse_visit_time(when_text)
            if when is None or not url:
                continue
            rows.append((when, url, title or ""))
        return rows

    return _with_copied_db(copy_path, _run)


def _read_chromium_bookmarks(copy_path):
    """Upstream-faithful recursive roots walk over the Bookmarks JSON."""
    with open(copy_path, "rb") as handle:
        data = json.load(handle)

    found = []

    def _deeper(node, folder):
        if not isinstance(node, dict):
            return
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                if child.get("type") == "url":
                    try:
                        when = _chrome_epoch_to_local(child["date_added"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    url = child.get("url") or ""
                    if not url:
                        continue
                    found.append(
                        (when, url, child.get("name") or "", folder)
                    )
                elif child.get("type") == "folder":
                    _deeper(
                        child, folder + os.sep + (child.get("name") or "")
                    )
            return
        for value in node.values():
            _deeper(value, folder)

    roots = data.get("roots") or {}
    for root_name, root in roots.items():
        if isinstance(root, dict):
            _deeper(root, root_name)
    found.sort(key=lambda row: row[0], reverse=True)
    return found


def _read_firefox_bookmarks(copy_path):
    def _run(conn):
        cursor = conn.cursor()
        cursor.execute(_FIREFOX_BOOKMARKS_SQL)
        rows = []
        for when_text, url, title, folder in cursor.fetchall():
            when = _parse_visit_time(when_text)
            if when is None or not url:
                continue
            rows.append((when, url, title or "", folder or ""))
        rows.sort(key=lambda row: row[0], reverse=True)
        return rows

    return _with_copied_db(copy_path, _run)


def _resolve_file(profile_dir, browser, kind):
    """The history / bookmarks file inside the named profile dir.

    Returns (path, label) or (None, reason). The profile dir is
    required and never guessed; the file name is fixed per browser.
    """
    if browser == "chromium":
        name = "History" if kind == "history" else "Bookmarks"
    elif browser == "firefox":
        name = "places.sqlite"
    else:
        return None, f"Unknown browser {browser!r} — use 'chromium' or 'firefox'."
    path = os.path.join(profile_dir, name)
    if not os.path.isfile(path):
        return None, (
            f"No {name} file in that profile folder ({profile_dir}) — "
            f"point at the profile folder itself, the one containing "
            f"the {name} file."
        )
    return path, name


def _filestat(path):
    """(size, mtime_ns) for the A4-filestat live-file check."""
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


def browser_history(profile_dir=None, browser="chromium", days=7, limit=50) -> str:
    """Read the named browser profile's history: when, title, URL.

    One-shot, read-only, local-only. The profile folder must be
    named on every call (never guessed, never remembered); the
    history opt-in must be on. The History file is copied to a
    temp dir and the copy is read — the live file is never opened
    and its size + mtime are verified unchanged before the answer
    is given. Returns the newest visits in the last ``days`` days
    (default 7, max 90), at most ``limit`` rows (default 50, max
    200). Read-only.
    """
    if not _browser_history_allowed():
        return _HISTORY_REFUSAL
    if not profile_dir or not isinstance(profile_dir, str):
        return (
            "A browser history read needs the profile folder named — "
            "the folder containing the History file, e.g. "
            "...\\Google\\Chrome\\User Data\\Default. I never guess "
            "a profile, so it has to come from you."
        )
    if not os.path.isdir(profile_dir):
        return f"No such profile folder: {profile_dir}"
    browser = (browser or "chromium").strip().lower()
    if browser not in ("chromium", "firefox"):
        return "Unknown browser — use 'chromium' (Chrome / Edge / Brave / Vivaldi) or 'firefox'."
    days = _clamp_days(days)
    limit = _clamp_limit(limit, _DEFAULT_HISTORY_LIMIT)
    src, reason = _resolve_file(profile_dir, browser, "history")
    if src is None:
        return reason
    before = _filestat(src)
    if browser == "chromium":
        rows = _read_chromium_history(src)
    else:
        rows = _read_firefox_history(src)
    after = _filestat(src)
    if before != after:
        return (
            "The history file changed while I was reading it, so I "
            "am not reporting anything from that read — ask again."
        )
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    rows = [row for row in rows if row[0] >= cutoff]
    rows.sort(key=lambda row: row[0], reverse=True)
    rows = rows[:limit]
    if not rows:
        return f"No visits in the last {days} day(s) in that profile."
    lines = [f"{len(rows)} visit(s) in the last {days} day(s):"]
    for when, url, title in rows:
        shown = title.strip() if title and title.strip() else "(no title)"
        lines.append(f"{when:%Y-%m-%d %H:%M} — {shown} — {url}")
    return "\n".join(lines)


def browser_bookmarks(profile_dir=None, browser="chromium", limit=100) -> str:
    """Read the named browser profile's bookmarks: when added, title,
    URL, folder.

    One-shot, read-only, local-only, same discipline as
    browser_history: the profile folder must be named on every
    call, the history opt-in must be on, the bookmarks file is
    copied to a temp dir and the copy is read — the live file is
    never opened and its size + mtime are verified unchanged
    before the answer is given. Returns the newest bookmarks, at
    most ``limit`` rows (default 100, max 200). Read-only.
    """
    if not _browser_history_allowed():
        return _HISTORY_REFUSAL
    if not profile_dir or not isinstance(profile_dir, str):
        return (
            "A bookmark read needs the profile folder named — the "
            "folder containing the Bookmarks file, e.g. "
            "...\\Google\\Chrome\\User Data\\Default. I never guess "
            "a profile, so it has to come from you."
        )
    if not os.path.isdir(profile_dir):
        return f"No such profile folder: {profile_dir}"
    browser = (browser or "chromium").strip().lower()
    if browser not in ("chromium", "firefox"):
        return "Unknown browser — use 'chromium' (Chrome / Edge / Brave / Vivaldi) or 'firefox'."
    limit = _clamp_limit(limit, _DEFAULT_BOOKMARK_LIMIT)
    src, reason = _resolve_file(profile_dir, browser, "bookmarks")
    if src is None:
        return reason
    before = _filestat(src)
    if browser == "chromium":
        with tempfile.TemporaryDirectory(prefix="vyuk-history-") as tmpdir:
            copied = shutil.copy2(src, tmpdir)
            rows = _read_chromium_bookmarks(copied)
    else:
        rows = _read_firefox_bookmarks(src)
    after = _filestat(src)
    if before != after:
        return (
            "The bookmarks file changed while I was reading it, so I "
            "am not reporting anything from that read — ask again."
        )
    rows = rows[:limit]
    if not rows:
        return "No bookmarks in that profile."
    lines = [f"{len(rows)} bookmark(s), newest first:"]
    for when, url, title, folder in rows:
        shown = title.strip() if title and title.strip() else "(no title)"
        lines.append(f"{when:%Y-%m-%d} — {shown} — {url} ({folder})")
    return "\n".join(lines)


def register_all(registry) -> None:
    """Register the browser-history pack (R10-3, browser-history
    port): browser_history / browser_bookmarks — two read-only
    tools over a named browser profile's stored history and
    bookmarks. The module owns its catalogue via register_all
    (same pattern as the other packs)."""
    registry.register(
        name="browser_history",
        description="Read the browsing history of a browser profile you name: when each page was visited, its title and URL, newest first. Needs the history opt-in on and the profile folder named on every call — I never guess a profile. The History file is copied and the copy is read, so the live file is never touched. Read-only, local-only.",
        parameters={
            "type": "object",
            "properties": {
                "profile_dir": {"type": "string",
                                "description": "The profile folder "
                                "itself, the one containing the "
                                "History file — e.g. ...\\Google\\"
                                "Chrome\\User Data\\Default. "
                                "Required, never guessed."},
                "browser": {"type": "string",
                            "description": "Which browser the profile "
                            "belongs to: 'chromium' (Chrome / Edge / "
                            "Brave / Vivaldi) or 'firefox'"},
                "days": {"type": "integer",
                         "description": "How many days back to look "
                         "(1-90, default 7)"},
                "limit": {"type": "integer",
                          "description": "How many visits to report "
                          "at most (1-200, default 50)"},
            },
            "required": ["profile_dir"],
        },
        handler=browser_history,
    )
    registry.register(
        name="browser_bookmarks",
        description="Read the bookmarks of a browser profile you name: when each was added, its title, URL and folder, newest first. Needs the history opt-in on and the profile folder named on every call — I never guess a profile. The bookmarks file is copied and the copy is read, so the live file is never touched. Read-only, local-only.",
        parameters={
            "type": "object",
            "properties": {
                "profile_dir": {"type": "string",
                                "description": "The profile folder "
                                "itself, the one containing the "
                                "Bookmarks file — e.g. ...\\Google\\"
                                "Chrome\\User Data\\Default. "
                                "Required, never guessed."},
                "browser": {"type": "string",
                            "description": "Which browser the profile "
                            "belongs to: 'chromium' (Chrome / Edge / "
                            "Brave / Vivaldi) or 'firefox'"},
                "limit": {"type": "integer",
                          "description": "How many bookmarks to "
                          "report at most (1-200, default 100)"},
            },
            "required": ["profile_dir"],
        },
        handler=browser_bookmarks,
    )
