from pathlib import Path
import os
import time
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_system2_helpers import (
    _NOT_WINDOWS,
    _clamp_int,
    _human_size,
    _is_windows,
    _ps_structured,
    _tail,
)


# ---------------------------------------------------------------------------
# PROCESSES — what is eating the machine right now (read-only)
# ---------------------------------------------------------------------------


def top_processes(by: str = "memory", count: int = 8) -> str:
    """The heaviest processes right now, by memory (rss) or CPU."""
    mode = (by or "memory").strip().lower()
    if mode not in ("cpu", "memory"):
        return f"Unknown ranking: '{by}'. Use by='cpu' or by='memory'."
    n = _clamp_int(count, 1, 20, 8)
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return ("Couldn't list processes — the psutil package isn't "
                "available in this VYUK install.")
    skipped = 0
    procs = []
    for proc in psutil.process_iter():
        try:
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            skipped += 1
            continue
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        procs.append((proc, name))
    if mode == "cpu":
        # cpu_percent() measures against the PREVIOUS call's baseline:
        # the first call on any process returns a meaningless 0.0 (it is
        # measured from process start / boot). So prime every process,
        # wait a beat, then read — the second reading is a real rate.
        for proc, _name in procs:
            try:
                proc.cpu_percent(None)
            except Exception:  # noqa: BLE001 — vanished already; counted below
                pass
        time.sleep(0.15)
    rows: list[tuple[float, str]] = []
    for proc, name in procs:
        try:
            if mode == "cpu":
                value = float(proc.cpu_percent(None))
                label = (f"{name} (PID {proc.pid}) — "
                         f"{_format_percent(value)} CPU")
            else:
                value = float(proc.memory_info().rss)
                label = (f"{name} (PID {proc.pid}) — "
                         f"{_human_size(int(value))}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            skipped += 1
            continue
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        rows.append((value, label))
    if not rows:
        return ("Couldn't read any process information — every process "
                "was gone or off-limits by the time VYUK looked.")
    rows.sort(key=lambda r: r[0], reverse=True)
    shown = rows[:n]
    if mode == "cpu":
        heading = (f"Top processes by CPU ({len(shown)} of "
                   f"{len(rows)} readable, over a short sampling "
                   "look — a snapshot, not a trend):")
    else:
        total = _human_size(int(sum(v for v, _l in rows)))
        heading = (f"Top processes by memory ({len(shown)} of "
                   f"{len(rows)} readable, using {total} between "
                   "them):")
    lines = [heading]
    lines += [f"• {label}" for _v, label in shown]
    if skipped:
        lines.append(f"…and {skipped} processes couldn't be inspected "
                     "(ended mid-read or access denied).")
    return "\n".join(lines)


def _format_percent(value: float) -> str:
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


# ---------------------------------------------------------------------------
# FILE SEARCH — grep the user's own documents (read-only)
# ---------------------------------------------------------------------------

_SEARCH_DEFAULT_EXTS = {".txt", ".md", ".py", ".log", ".json", ".csv", ".ini"}
_SEARCH_FILE_CAP = 600
_SEARCH_MATCH_CAP = 40
_SEARCH_SIZE_CAP = 2 * 1024 * 1024  # skip files larger than 2 MB


def search_file_contents(query: str, folder: str = "",
                         extensions: str = "", max_matches: int = 40) -> str:
    """Case-insensitive substring search across the user's text files.
    max_matches (default 40, max 200) raises the per-search match cap;
    the scan cap stays at _SEARCH_FILE_CAP either way."""
    needle_raw = (query or "").strip()
    if not needle_raw:
        return ("Tell me what text to look for, e.g. search_file_contents "
                "with query='invoice total'.")
    needle = needle_raw.lower()
    if (folder or "").strip():
        root = Path(folder).expanduser()
    else:
        docs = Path.home() / "Documents"
        root = docs if docs.is_dir() else Path.home()
    if not root.is_dir():
        return f"Couldn't search — that folder doesn't exist: {root}"
    if (extensions or "").strip():
        exts = set()
        for part in extensions.split(","):
            part = part.strip().lower()
            if part:
                exts.add(part if part.startswith(".") else f".{part}")
        if not exts:
            exts = set(_SEARCH_DEFAULT_EXTS)
    else:
        exts = set(_SEARCH_DEFAULT_EXTS)

    match_cap = _clamp_int(max_matches, 1, 200, _SEARCH_MATCH_CAP)
    matches: list[str] = []
    scanned = 0
    skipped_large = 0
    skipped_binary = 0
    skipped_error = 0
    hit_scan_cap = False
    hit_match_cap = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fname in sorted(filenames):
            if hit_match_cap:
                break
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in exts:
                continue
            if scanned >= _SEARCH_FILE_CAP:
                hit_scan_cap = True
                break
            try:
                if fpath.stat().st_size > _SEARCH_SIZE_CAP:
                    skipped_large += 1
                    continue
            except OSError:
                skipped_error += 1
                continue
            scanned += 1
            try:
                with open(fpath, "rb") as fh:
                    head = fh.read(4096)
                    if b"\x00" in head:
                        skipped_binary += 1
                        continue
                    fh.seek(0)
                    raw = fh.read()
            except OSError:
                skipped_error += 1
                continue
            text = raw.decode("utf-8", errors="replace")
            try:
                rel = fpath.relative_to(root)
            except ValueError:
                rel = fpath
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    snippet = " ".join(line.split())[:120]
                    matches.append(f"{rel}:{lineno} — {snippet}")
                    if len(matches) >= match_cap:
                        hit_match_cap = True
                        break
        if hit_scan_cap or hit_match_cap:
            break

    notes = [f"scanned {scanned} file(s) under {root}"]
    if hit_scan_cap:
        notes.append(f"stopped at the {_SEARCH_FILE_CAP}-file scan cap — "
                     "not everything was searched")
    if hit_match_cap:
        notes.append(f"stopped at the {match_cap}-match cap — "
                     "there may be more matches")
    if skipped_large:
        notes.append(f"skipped {skipped_large} file(s) larger than 2 MB")
    if skipped_binary:
        notes.append(f"skipped {skipped_binary} binary-looking file(s)")
    if skipped_error:
        notes.append(f"couldn't open {skipped_error} file(s)")
    summary = "; ".join(notes) + "."
    if not matches:
        return f"No matches for '{needle_raw}' — {summary}"
    head = (f"Found {len(matches)} match(es) for '{needle_raw}' "
            f"({summary}):")
    return head + "\n" + "\n".join(f"• {m}" for m in matches)


# ---------------------------------------------------------------------------
# EVENT LOG — what Windows itself has been complaining about (read-only)
# ---------------------------------------------------------------------------

_EVENT_LEVELS = {"errors": "Critical and error", "warnings": "Warning",
                 "all": "All"}


def _event_log_command(chan: str, level_filter, n: int, hours: int = 0) -> str:
    """Build the Get-WinEvent command for system_event_log. Pure string
    construction — no PowerShell runs here — so the exact query is
    checkable on any platform. hours <= 0 leaves the command
    byte-identical to the unbounded (no time window) query."""
    hashtable = f"@{{LogName='{chan}'"
    if level_filter:
        hashtable += f"; Level={level_filter}"
    if hours:
        hashtable += f"; StartTime=(Get-Date).AddHours(-{hours})"
    hashtable += "}"
    return (
        f"Get-WinEvent -FilterHashtable {hashtable} -MaxEvents {n} | "
        "ForEach-Object { $m = if ($_.Message) { $_.Message } else { '' }; "
        "$m = $m -replace '[\\r\\n]+', ' '; "
        "$_.TimeCreated.ToString('MMM d HH:mm') + [char]0x1f + $_.Level "
        "+ [char]0x1f + $_.ProviderName "
        "+ [char]0x1f + $m }"
    )


def system_event_log(channel: str = "System", level: str = "errors",
                     count: int = 10, hours: int = 0) -> str:
    """Recent entries from the Windows System or Application event log.
    hours (default 0 = no time limit) restricts the query to entries
    from the last N hours via the FilterHashtable's StartTime."""
    chan = (channel or "System").strip().capitalize()
    if chan not in ("System", "Application"):
        return (f"Unknown log channel: '{channel}'. "
                "Use 'System' or 'Application'.")
    lvl = (level or "errors").strip().lower()
    if lvl not in _EVENT_LEVELS:
        return (f"Unknown level filter: '{level}'. "
                "Use 'errors', 'warnings' or 'all'.")
    n = _clamp_int(count, 1, 25, 10)
    h = _clamp_int(hours, 0, 720, 0)
    if not _is_windows():
        return f"The Windows event log only exists on Windows — {_NOT_WINDOWS}"
    level_filter = {"errors": "@(1,2)", "warnings": "@(3)", "all": None}[lvl]
    command = _event_log_command(chan, level_filter, n, h)
    window_note = (f" in the last {h} hour{'s' if h != 1 else ''}"
                   if h else "")
    rc, rows, err = _ps_structured(command, timeout=60)
    if not rows:
        if rc != 0 and "no events were found" in (err or "").lower():
            return (f"No {_EVENT_LEVELS[lvl].lower()} events in the {chan} "
                    f"log{window_note} matched — Windows reported none.")
        if rc != 0:
            return (f"Couldn't read the {chan} event log: "
                    f"{_tail(err) or 'Get-WinEvent failed.'}")
        return (f"No {_EVENT_LEVELS[lvl].lower()} events in the {chan} "
                f"log{window_note} matched.")
    # Severity-first (#44): critical before error before
    # warning, time order kept inside each severity (the
    # rows arrive newest-first and the sort is stable). The
    # header states the time window the rows span.
    level_words = {1: "Critical", 2: "Error", 3: "Warning",
                   4: "Information", 5: "Verbose"}
    rank = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}

    def _lvl(row):
        try:
            return int(row[1])
        except (IndexError, TypeError, ValueError):
            return 0

    times = [r[0] for r in rows if r]
    window = (f"{times[-1]} – {times[0]}" if times else "")
    ordered = sorted(rows, key=lambda r: rank.get(_lvl(r), 5))
    lines = [f"{len(rows)} {_EVENT_LEVELS[lvl].lower()} event(s) "
             f"from the {chan} log{window_note}"
             + (f" ({window})" if window else "")
             + ", worst first:"]
    for row in ordered:
        when = row[0] if len(row) > 0 else "??:??"
        word = level_words.get(_lvl(row), "Event")
        provider = row[2] if len(row) > 2 else "unknown source"
        message = " ".join(row[3:]) if len(row) > 3 else ""
        lines.append(f"• {when} [{word}] {provider}: {message[:200]}")
    return "\n".join(lines)
