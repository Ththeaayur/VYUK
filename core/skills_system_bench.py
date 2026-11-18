"""
System skills V for VYUK (v5.21, HANDS — System III pack):
a startup-impact synthesis over the sign-in inventory, a redacted
environment backup, a self-cleaning disk speed test, the memory
and CPU leaderboards as dedicated cuts, graphics-card details and
a Windows Update status look.

Same rules as the other packs: plain functions returning
human-readable strings; every platform-specific dependency
(psutil, winreg, PowerShell, nvidia-smi, lspci) is reached lazily
and guarded, so this module imports cleanly on any OS — a missing
piece degrades to an honest line instead of a crash, and nothing
fakes a reading the OS did not give. Handlers never raise.

Read/write split: startup_impact, gpu_info and
windows_update_status only look (READ-ONLY); env_backup writes one
JSON file (secrets redacted, never overwriting); disk_speed_test
writes a scratch file and always deletes it before returning.

Deliberately NOT here: the plain startup list is
list_startup_programs, the combined CPU/memory leaderboard is
top_processes and battery levels are battery_info /
battery_health — this pack never duplicates them.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers (each pack carries its own copies — the house convention)
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


_SEP = "\x1f"  # unit separator: fields in PowerShell output ride on this,
               # never on localised labels or column spacing (v4.7 rule).
               # Defined here to match skills_system_monitor/system_inventory and skills_network_wake_shares;
               # _ps_structured below splits on it (audit fix batch 1: it
               # was used but never defined in this module).


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a command, capturing output. Returns (returncode, stdout, stderr);
    returncode is -1 when the command could not be started at all and -2 on
    timeout, with the reason in stderr. Never raises, never uses a shell,
    stdin is /dev/null so a prompting tool fails visibly instead of hanging."""
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
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


def _non_clobbering(dest: Path) -> Path:
    """Return `dest`, or 'stem (2).suffix', 'stem (3).suffix', … —
    the house rule: a created file never silently overwrites."""
    if not dest.exists():
        return dest
    counter = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({counter}){dest.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _suffix_note(dest: Path, final: Path) -> str:
    """The disclosure sentence when a taken name was suffixed."""
    if final == dest:
        return ""
    return (f" '{dest.name}' already existed, so the suffixed name "
            f"'{final.name}' was used instead.")


def _ps_structured(command: str, timeout: int = 60) -> tuple[int, list[list[str]], str]:
    """Run a PowerShell command whose output lines are fields joined by
    _SEP, and return (returncode, parsed rows, stderr). Parsing is purely
    structural — no localised header or label is ever matched."""
    rc, out, err = _run(["powershell", "-NoProfile", "-Command", command],
                        timeout=timeout)
    rows: list[list[str]] = []
    for line in (out or "").splitlines():
        if line.strip():
            rows.append(line.split(_SEP))
    return rc, rows, err


# ---------------------------------------------------------------------------
# STARTUP IMPACT — the sign-in inventory, weighed (read-only)
# ---------------------------------------------------------------------------

# Well-known sign-in heavyweights: matched case-insensitively
# against the entry's name AND its command line, so 'Microsoft
# Teams' and a bare 'teams.exe' path are both caught.
_HEAVYWEIGHTS = (
    "teams", "onedrive", "adobe", "steam", "discord", "spotify",
    "zoom", "skype", "slack", "dropbox", "itunes", "origin",
    "epic games", "battle.net", "gog galaxy", "google drive",
)


def _is_heavyweight(item: dict) -> bool:
    haystack = f"{item.get('name', '')} {item.get('detail', '')}".lower()
    return any(word in haystack for word in _HEAVYWEIGHTS)


def startup_impact() -> str:
    """Weigh what starts at sign-in: counts by source, heavyweights flagged."""
    if not _is_windows():
        return ("Startup impact is a Windows sign-in report — this "
                "system is not Windows, so there is no sign-in "
                "inventory to weigh.")
    try:
        from . import skills_system

        items = skills_system._startup_inventory()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the startup list: {e}"
    enabled = [i for i in items if i.get("enabled", True)]
    disabled = [i for i in items if not i.get("enabled", True)]
    if not enabled and not disabled:
        return ("Nothing is set to run automatically at sign-in — "
                "the lightest possible startup.")
    groups: dict[str, list[dict]] = {}
    for item in enabled:
        groups.setdefault(str(item.get("source", "unknown")), []).append(item)
    heavy = [i for i in enabled if _is_heavyweight(i)]
    lines = [
        f"{len(enabled)} program(s) start with Windows"
        + (f", {len(heavy)} of them heavyweight." if heavy
           else ", none of them known heavyweights.")
    ]
    for source in sorted(groups):
        names = sorted(i["name"] for i in groups[source])
        lines.append(f"• From {source} ({len(names)}): {', '.join(names)}")
    if heavy:
        flagged = sorted({i["name"] for i in heavy})
        lines.append(
            "Heavyweight — the ones that most often make sign-in "
            f"feel slow: {', '.join(flagged)}. If sign-in drags, "
            "these are the first candidates to switch off with "
            "set_startup_program (it's reversible).")
    if disabled:
        lines.append(f"({len(disabled)} more entr(ies) sit disabled "
                     "by VYUK and don't start.)")
    if len(enabled) <= 3 and not heavy:
        lines.append("That's a light sign-in — nothing to trim.")
    elif len(enabled) >= 10:
        lines.append("That's a crowded sign-in — trimming the "
                     "heavyweights is the easiest speed-up there is.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ENVIRONMENT BACKUP — the environment, secrets redacted (writes a file)
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|AUTH")
_REDACTED = "•••"


def env_backup(folder: str = "") -> str:
    """Write the environment variables to a JSON backup, secrets redacted.
    folder (optional) chooses where the backup lands; the default is
    the user's Documents folder, exactly as before."""
    try:
        snapshot = dict(os.environ)
        redacted = 0
        for key in list(snapshot):
            if _SECRET_KEY_RE.search(key.upper()):
                snapshot[key] = _REDACTED
                redacted += 1
        custom_folder = bool((folder or "").strip())
        if custom_folder:
            documents = Path(folder).expanduser()
        else:
            documents = Path.home() / "Documents"
        try:
            documents.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if custom_folder:
                return f"Couldn't create the folder for the backup: {e}"
            return f"Couldn't create the Documents folder for the backup: {e}"
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        dest = documents / f"visor_env_backup_{stamp}.json"
        final = _non_clobbering(dest)
        payload = {
            "created_by": "VYUK",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "variable_count": len(snapshot),
            "redacted_count": redacted,
            "variables": snapshot,
        }
        try:
            final.write_text(json.dumps(payload, indent=2, sort_keys=True),
                             encoding="utf-8")
        except OSError as e:
            return f"Couldn't write the environment backup to {dest}: {e}"
        lines = [
            f"Environment backed up to {final} — "
            f"{len(snapshot)} variable(s)."
            f"{_suffix_note(dest, final)}",
        ]
        if redacted:
            lines.append(
                f"{redacted} value(s) whose names look like secrets "
                "(matching KEY, TOKEN, SECRET, PASSWORD or AUTH) "
                f"were redacted to '{_REDACTED}' — the backup is "
                "safe to share for troubleshooting.")
        else:
            lines.append("No variable names looked like secrets, so "
                         "nothing needed redacting.")
        # R1b: re-read the backup file — write_text returning does
        # not prove it landed intact.
        from . import tool_outcome as _outcome  # R1b: seam wiring

        ok, evidence = _outcome.file_written(final)
        if not ok:
            return (f"Couldn't confirm the environment backup: "
                    f"{evidence}.")
        return _outcome.verified("\n".join(lines), evidence)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"Couldn't back up the environment: {e}"


# ---------------------------------------------------------------------------
# DISK SPEED TEST — write, read, delete (writes a scratch file, removes it)
# ---------------------------------------------------------------------------

_DISK_TEST_CAP_MB = 256
_DISK_CHUNK = 1024 * 1024  # 1 MiB


def disk_speed_test(folder: str = "", megabytes: int = 64) -> str:
    """Time a scratch file's write and read, then delete it — always."""
    try:
        mb = int(megabytes)
    except (TypeError, ValueError):
        return (f"'{megabytes}' isn't a number of megabytes — give "
                f"a whole number from 1 to {_DISK_TEST_CAP_MB}.")
    if not 1 <= mb <= _DISK_TEST_CAP_MB:
        return (f"Disk tests run from 1 to {_DISK_TEST_CAP_MB} MB — "
                f"{mb} is outside that.")
    if folder:
        target_dir = Path(str(folder)).expanduser()
        if not target_dir.is_dir():
            return f"'{folder}' isn't a folder I can test in."
    else:
        target_dir = Path(tempfile.gettempdir())
    scratch = target_dir / f"visor_speedtest_{os.getpid()}.tmp"
    total = mb * _DISK_CHUNK
    block = os.urandom(_DISK_CHUNK)
    try:
        started = time.perf_counter()
        with open(scratch, "wb") as fh:
            for _ in range(mb):
                fh.write(block)
            fh.flush()
            os.fsync(fh.fileno())
        write_s = max(time.perf_counter() - started, 1e-6)
        started = time.perf_counter()
        read_back = 0
        with open(scratch, "rb") as fh:
            while True:
                chunk = fh.read(_DISK_CHUNK)
                if not chunk:
                    break
                read_back += len(chunk)
        read_s = max(time.perf_counter() - started, 1e-6)
    except OSError as e:
        return f"Couldn't run the disk test in {target_dir}: {e}"
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass
    if read_back != total:
        return (f"The disk test read back {read_back} of {total} "
                "bytes — the disk didn't return everything written, "
                "so the numbers can't be trusted. The scratch file "
                "was deleted.")
    write_rate = mb / write_s
    read_rate = mb / read_s
    if write_rate >= 400:
        verdict = "That's fast — solid-state territory."
    elif write_rate >= 100:
        verdict = "Respectable — a decent SSD or fast storage."
    elif write_rate >= 30:
        verdict = "Modest — an older SSD, a hard drive or a busy disk."
    else:
        verdict = ("Slow — a spinning hard drive, a nearly-full "
                   "disk, or something else is hammering storage.")
    return (f"Disk speed in {target_dir} ({mb} MB test file, "
            f"deleted afterwards): write {write_rate:.0f} MB/s, "
            f"read {read_rate:.0f} MB/s. {verdict} (One quick pass — "
            "numbers move with whatever else the machine is doing.)")


# ---------------------------------------------------------------------------
# GPU INFO — name, memory, driver, from whichever source answers (read-only)
# ---------------------------------------------------------------------------


def _gpu_from_nvidia_smi() -> list[str]:
    """['name — X MB VRAM, driver Y', ...] from nvidia-smi, or []."""
    if not shutil.which("nvidia-smi"):
        return []
    rc, out, _err = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader"], timeout=15)
    if rc != 0:
        return []
    found = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        bits = [parts[0]]
        if len(parts) > 1 and parts[1]:
            bits.append(parts[1].replace("MiB", "MB") + " VRAM"
                        if "MiB" in parts[1] else parts[1])
        if len(parts) > 2 and parts[2]:
            bits.append(f"driver {parts[2]}")
        found.append(" — ".join([bits[0], ", ".join(bits[1:])])
                     if len(bits) > 1 else bits[0])
    return found


def _gpu_from_windows() -> list[str]:
    """Win32_VideoController rows via PowerShell, or []."""
    command = (
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        "[string]::Join([char]31, @($_.Name, $_.AdapterRAM, "
        "$_.DriverVersion)) }")
    rc, rows, _err = _ps_structured(command, timeout=10)
    if rc != 0:
        return []
    found = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        bits = [row[0].strip()]
        if len(row) > 1 and row[1].strip():
            try:
                vram = int(row[1])
                if vram > 0:
                    bits.append(f"{_human_size(vram)} VRAM")
            except ValueError:
                pass
        if len(row) > 2 and row[2].strip():
            bits.append(f"driver {row[2].strip()}")
        found.append(" — ".join([bits[0], ", ".join(bits[1:])])
                     if len(bits) > 1 else bits[0])
    return found


def _gpu_from_lspci() -> list[str]:
    """VGA/3D lines from lspci, or []."""
    if not shutil.which("lspci"):
        return []
    rc, out, _err = _run(["lspci"], timeout=10)
    if rc != 0:
        return []
    found = []
    for line in (out or "").splitlines():
        if "VGA compatible controller:" in line or \
                "3D controller:" in line:
            found.append(line.split(":", 2)[-1].strip())
    return found


def gpu_info() -> str:
    """Graphics card name, VRAM and driver — from whichever source answers."""
    try:
        for probe in (_gpu_from_nvidia_smi,
                      _gpu_from_windows if _is_windows() else None,
                      _gpu_from_lspci):
            if probe is None:
                continue
            try:
                found = probe()
            except Exception:  # noqa: BLE001 — try the next source
                found = []
            if found:
                if len(found) == 1:
                    return f"Graphics card: {found[0]}."
                lines = [f"{len(found)} graphics adapters:"]
                lines += [f"• {entry}" for entry in found]
                return "\n".join(lines)
        return ("I couldn't read the graphics card on this system — "
                "none of the usual sources (nvidia-smi, the Windows "
                "video-controller record, lspci) had an answer. "
                "Device Manager (Windows) or System Information "
                "will know.")
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't read the graphics card: {e}"


# ---------------------------------------------------------------------------
# WINDOWS UPDATE STATUS — last check, pending restart (read-only)
# ---------------------------------------------------------------------------


def _winreg_key_exists(winreg_module, root, path: str) -> bool:
    try:
        with winreg_module.OpenKey(root, path):
            return True
    except OSError:
        return False


def _winreg_value(winreg_module, root, path: str, name: str):
    try:
        with winreg_module.OpenKey(root, path) as key:
            value, _type = winreg_module.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def windows_update_status() -> str:
    """Last successful update search + whether a restart is pending.

    B20 — HONEST NO-YIELD-POINT NOTE: this tool has no
    cancellation yield point by design. It is a "long" tool
    only by its deadline class (900 s); in fact it performs
    two fast registry reads and returns in milliseconds, so
    there is no long-running act to interrupt — a Stop
    pressed while it runs ends the task at the next step
    boundary, exactly as today. It takes no cancel_token
    deliberately: adding a token to a call that cannot block
    would be a fake yield point. If a future version ever
    gains a slow call (a live update search), that call is
    where the token belongs.
    """
    if not _is_windows():
        return ("Windows Update is a Windows feature — this system "
                "is not Windows, so there is no update status to "
                "read. (Linux updates live with its package "
                "manager instead.)")
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return "Couldn't reach the Windows registry to read update status."
    lines = []
    try:
        detect_key = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                      r"\WindowsUpdate\Auto Update\Results\Detect")
        when = None
        for value_name in ("LastSuccessTime", "SuccessTime"):
            raw = _winreg_value(winreg, winreg.HKEY_LOCAL_MACHINE,
                                detect_key, value_name)
            if raw:
                when = str(raw)
                break
        if when:
            lines.append(f"Windows Update last successfully checked "
                         f"for updates at {when}.")
        else:
            lines.append("No record of a successful update check "
                         "was found — Windows may never have "
                         "finished one, or the record is kept "
                         "elsewhere on this build.")
        pending_reasons = []
        if _winreg_key_exists(
                winreg, winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\Component Based Servicing\RebootPending"):
            pending_reasons.append("component servicing")
        if _winreg_key_exists(
                winreg, winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\WindowsUpdate\Auto Update\RebootRequired"):
            pending_reasons.append("Windows Update itself")
        if pending_reasons:
            lines.append("A restart IS pending (flagged by "
                         + " and ".join(pending_reasons)
                         + ") — some updates won't finish installing "
                           "until this PC restarts.")
        else:
            lines.append("No restart is pending for updates.")
    except Exception as e:  # noqa: BLE001 — honest partial, never a crash
        if not lines:
            return (f"Couldn't read the Windows Update status: {e}")
        lines.append(f"(Part of the status couldn't be read: {e})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every System III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="startup_impact",
        description="Weigh the sign-in load: everything that starts with Windows (the same inventory list_startup_programs reads — registry Run keys for this user and all users, and both Startup folders), counted and grouped by source, with the well-known heavyweights flagged by name (Teams, OneDrive, Adobe updaters, Steam, Discord, Spotify and friends) and a plain verdict on whether sign-in is light or crowded. Disabled entries VYUK is keeping are counted separately since they don't start. Windows only. Use when the user says sign-in is slow or asks what's dragging startup. Read-only — switching things off is set_startup_program.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=startup_impact,
    )
    registry.register(
        name="env_backup",
        description="Back up this PC's environment variables to a JSON file in ~/Documents (visor_env_backup_<date-time>.json, never overwriting an earlier backup — a ' (2)' suffix is added and reported). Values whose variable NAMES look like secrets — matching KEY, TOKEN, SECRET, PASSWORD or AUTH — are redacted to '•••' and the result says how many were hidden, so the file is safe to share for troubleshooting. Use when the user wants their environment recorded, compared later, or sent to someone helping them debug. Pass folder to write the backup somewhere other than Documents. Writes one file; changes nothing else.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string",
                           "description": "Folder to write the backup into (optional; default is the user's Documents folder)"},
            },
            "required": [],
        },
        handler=env_backup,
    )
    registry.register(
        name="disk_speed_test",
        description="A quick storage speed check: writes a scratch file of random data (64 MB by default, up to 256 MB) into a folder (the system temp folder unless one is given), timing the write with a flush to disk, then times reading it back, and reports write/read MB/s with a plain verdict (fast SSD, modest, slow). The scratch file is ALWAYS deleted before the tool returns, pass or fail. Use when the user asks how fast their disk is or whether storage is the bottleneck. Writes and removes one temp file; nothing else changes.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string",
                           "description": "Folder to test in (optional; default is the system temp folder — test a specific drive by naming a folder on it)"},
                "megabytes": {"type": "integer",
                              "description": "Test size in MB, 1–256 (default 64)"},
            },
            "required": [],
        },
        handler=disk_speed_test,
    )
    registry.register(
        name="gpu_info",
        description="Graphics card details: name, video memory and driver version, from whichever source answers first — nvidia-smi for NVIDIA cards, the Windows video-controller record (Win32_VideoController) on Windows, or lspci on Linux. Multiple adapters are all listed. Use when the user asks what GPU they have, how much VRAM, or whether a game/app's requirements are met. If no source answers it says so honestly instead of guessing. Read-only. For the whole-PC inventory use hardware_info; for live usage use get_system_info.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=gpu_info,
    )
    registry.register(
        name="windows_update_status",
        description="Windows Update status from the registry: when Windows last successfully checked for updates, and whether a restart is pending to finish installing them (the component-servicing and Windows Update reboot flags). Windows only — on anything else it says so plainly. Use when the user asks whether updates are stuck, pending, or waiting on a restart. Read-only: it checks nothing in, installs nothing, and never restarts anything.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=windows_update_status,
    )
