"""Network-system probes, split from core/watchers.py (skills split B45).

Home of the G1 probe family plus the shared header constants POLL_SECONDS,
DISK_SCAN_BUDGET_SECONDS and DISK_SCAN_TOP_COUNT (lowest module in the
watchers dependency order, so cross-family users import them from here).
"""

# INTENTIONAL (skills split B45): stdlib names the moved code resolves
# at module level, found by the import-completeness prover.
import os
import socket
import sys
import time
from pathlib import Path

POLL_SECONDS = 30

DISK_SCAN_BUDGET_SECONDS = 1.5  # N2: hard cap for the disk-low folder scan

DISK_SCAN_TOP_COUNT = 3         # N2: folders named in the disk-low notice

def _probe_battery():
    """(percent:int, charging:bool, minutes_left:int|None) or
    None (no battery / no psutil). minutes_left comes from the
    OS estimate (psutil secsleft) when it reports a sane value;
    unlimited/unknown estimates read as None."""
    try:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return None
        minutes = None
        secs = getattr(battery, "secsleft", None)
        if isinstance(secs, (int, float)) and secs > 0:
            minutes = int(round(secs / 60))
        return (int(round(battery.percent)),
                bool(battery.power_plugged), minutes)
    except Exception:  # noqa: BLE001 — a broken probe disables its watcher
        return None

def _probe_load():
    """(cpu_percent, mem_percent) floats or None (no psutil).

    cpu_percent(interval=None) is non-blocking; its very first
    call in a process reads 0.0 by psutil's contract — a warm-up
    tick, harmless for a sustained-load watcher."""
    try:
        import psutil

        return (float(psutil.cpu_percent(interval=None)),
                float(psutil.virtual_memory().percent))
    except Exception:  # noqa: BLE001 — a broken probe disables its watcher
        return None

def _human_bytes(num_bytes) -> str:
    """Bytes as a short human size: 512 B, 1.5 KB, 9.1 GB."""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "0 B"
    if n < 0:
        n = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"  # unreachable — the loop always returns

def _display_process_name(name) -> str:
    """A psutil process name as a person would say it: drop a
    trailing ".exe"; capitalise an all-lowercase name
    ("chrome.exe" → "Chrome"). Anything else is kept as-is."""
    text = str(name or "").strip()
    if text.lower().endswith(".exe"):
        text = text[:-4]
    if text and text == text.lower():
        text = text.capitalize()
    return text

def _scan_top_cpu_once(psutil):
    """One pass over processes: (raw_name, cpu_percent) for the
    highest reading, or None when nothing reads above zero."""
    best_name, best_val = None, None
    for proc in psutil.process_iter(["name", "cpu_percent"]):
        try:
            info = getattr(proc, "info", None) or {}
            name = info.get("name")
            val = info.get("cpu_percent")
            if val is None:
                val = proc.cpu_percent()
            if not name:
                name = proc.name()
            val = float(val)
        except Exception:  # noqa: BLE001 — one dead process skips
            continue
        if best_val is None or val > best_val:
            best_name, best_val = str(name), val
    if best_name is None or not best_val or best_val <= 0:
        return None
    return (best_name, best_val)

def _probe_top_cpu_process():
    """(display_name, cpu_percent) for the busiest process, or
    None (no psutil / nothing measurable / any failure).

    Per-process cpu_percent reads 0.0 on a process object's first
    call by psutil's contract, so when the first pass measures
    nothing the probe primes every process, waits briefly, and
    measures again (process_iter caches the process objects, so
    the second pass reports the delta since priming)."""
    try:
        import psutil

        found = _scan_top_cpu_once(psutil)
        if found is not None:
            return (_display_process_name(found[0]), float(found[1]))
        # Prime, then re-measure.
        primed = []
        for proc in psutil.process_iter(["name"]):
            try:
                proc.cpu_percent()
                primed.append(proc)
            except Exception:  # noqa: BLE001
                continue
        if not primed:
            return None
        time.sleep(0.25)
        best_name, best_val = None, None
        for proc in primed:
            try:
                val = float(proc.cpu_percent())
                info = getattr(proc, "info", None) or {}
                name = info.get("name") or proc.name()
            except Exception:  # noqa: BLE001
                continue
            if best_val is None or val > best_val:
                best_name, best_val = str(name), val
        if best_name is None or not best_val or best_val <= 0:
            return None
        return (_display_process_name(best_name), float(best_val))
    except Exception:  # noqa: BLE001 — degrade to the plain message
        return None

def _probe_top_memory_process():
    """(display_name, rss_bytes) for the process holding the most
    memory, or None on any failure."""
    try:
        import psutil

        best_name, best_rss = None, None
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                info = getattr(proc, "info", None) or {}
                name = info.get("name")
                mem = info.get("memory_info")
                rss = None
                if mem is not None:
                    rss = mem.get("rss") if isinstance(mem, dict) \
                        else getattr(mem, "rss", None)
                if rss is None:
                    rss = proc.memory_info().rss
                if not name:
                    name = proc.name()
                rss = float(rss)
            except Exception:  # noqa: BLE001 — one dead process skips
                continue
            if best_rss is None or rss > best_rss:
                best_name, best_rss = str(name), rss
        if best_name is None or not best_rss or best_rss <= 0:
            return None
        return (_display_process_name(best_name), int(best_rss))
    except Exception:  # noqa: BLE001 — degrade to the plain message
        return None

def _probe_disk_top_folders(root=None, budget_seconds=None,
                            _clock=None):
    """Largest top-level folders of the system drive, biggest
    first: [(folder_name, bytes), ...] with at most
    DISK_SCAN_TOP_COUNT entries — or None when the scan fails or
    blows its time budget (the caller then offers the scan in
    words instead of naming folders).

    Budget design (N2): ONE pass over the drive's top-level
    entries; each folder is sized with a single os.walk that
    never follows symlinks (symlinked dirs are pruned, symlinked
    files count at their own lstat size — nothing is chased off
    the drive). A monotonic deadline of DISK_SCAN_BUDGET_SECONDS
    (~1.5 s) is checked before every folder and inside the walk;
    passing it aborts the WHOLE scan (None) rather than naming
    folders from a half-finished pass. Any error anywhere —
    unreadable drive root, a vanishing folder — is guarded per
    entry, and a total failure returns None. Never raises.
    `_clock` is injectable so tests drive the deadline with a
    fake clock instead of sleeping.
    """
    clock = _clock if _clock is not None else time.monotonic
    if budget_seconds is None:
        budget_seconds = DISK_SCAN_BUDGET_SECONDS
    try:
        root_path = Path(root) if root else Path(Path.home().anchor)
        deadline = clock() + float(budget_seconds)
        try:
            entries = sorted(root_path.iterdir(),
                             key=lambda p: p.name.lower())
        except OSError:
            return None
        sizes: list = []
        for entry in entries:
            if clock() > deadline:
                return None  # budget blown — no half-finished claims
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue  # vanished mid-listing — skip it
            total = 0
            blown = False
            for dirpath, dirnames, filenames in os.walk(
                    entry, followlinks=False):
                dirnames[:] = [
                    d for d in dirnames
                    if not os.path.islink(os.path.join(dirpath, d))]
                for fname in filenames:
                    if clock() > deadline:
                        blown = True
                        break
                    try:
                        total += os.lstat(
                            os.path.join(dirpath, fname)).st_size
                    except OSError:
                        continue  # vanished mid-walk — skip the file
                if blown:
                    break
            if blown or clock() > deadline:
                return None
            if total > 0:
                sizes.append((entry.name, int(total)))
        if not sizes:
            return None
        sizes.sort(key=lambda kv: kv[1], reverse=True)
        return sizes[:DISK_SCAN_TOP_COUNT]
    except Exception:  # noqa: BLE001 — degrade to the offer message
        return None

def _folder_phrase(folders) -> str:
    """"Users (9.1 GB), Windows (2.3 GB) and Temp (512 MB)" —
    the disk-low folder list as one speakable phrase."""
    parts = [f"{name} ({_human_bytes(size)})" for name, size in folders]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"

def _probe_downloads():
    """{filename: size} for files in ~/Downloads; {} on any failure."""
    try:
        folder = Path.home() / "Downloads"
        snapshot = {}
        for entry in folder.iterdir():
            try:
                if entry.is_file():
                    snapshot[entry.name] = entry.stat().st_size
            except OSError:
                continue  # vanished mid-listing — skip it this tick
        return snapshot
    except Exception:  # noqa: BLE001
        return {}

def _probe_network():
    """True when 1.1.1.1:53 accepts a TCP connection, else False."""
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=3):
            return True
    except Exception:  # noqa: BLE001 — unreachable IS the answer here
        return False

def _default_gateway_ip():
    """This machine's default-gateway IP, or None. Windows parses
    `ipconfig`, Linux `ip route`; anything else / any failure is
    None. Never raises."""
    try:
        import subprocess

        kwargs = {"capture_output": True, "timeout": 3,
                  "stdin": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            out = subprocess.run(["ipconfig"], **kwargs).stdout or b""
            for line in out.decode("utf-8", "replace").splitlines():
                if "Default Gateway" in line and ":" in line:
                    value = line.rsplit(":", 1)[-1].strip()
                    if value:
                        return value
            return None
        out = subprocess.run(["ip", "route", "show", "default"],
                             **kwargs).stdout or b""
        parts = out.decode("utf-8", "replace").split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
        return None
    except Exception:  # noqa: BLE001
        return None

def _gateway_reachable() -> bool | None:
    """Ping the default gateway once (1.5 s budget). None when no
    gateway is known or ping cannot run at all."""
    gateway = _default_gateway_ip()
    if not gateway:
        return None
    try:
        import subprocess

        args = (["ping", "-n", "1", "-w", "1500", gateway]
                if sys.platform.startswith("win")
                else ["ping", "-c", "1", "-W", "2", gateway])
        kwargs = {"capture_output": True, "timeout": 3,
                  "stdin": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000
        return subprocess.run(args, **kwargs).returncode == 0
    except Exception:  # noqa: BLE001
        return None

def _dns_resolves() -> bool | None:
    """Resolve a well-known name, time-boxed on a worker thread —
    gethostbyname has no timeout knob of its own and a sick DNS
    can block for many seconds without one."""
    try:
        import concurrent.futures

        def _resolve():
            return socket.gethostbyname("one.one.one.one")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return bool(pool.submit(_resolve).result(timeout=2.5))
    except Exception:  # noqa: BLE001 — timeout / failure IS "not resolving"
        return False

def _https_fetch_ok() -> bool | None:
    """Fetch an HTTPS page (2.5 s budget). ANY HTTP response —
    even an error status — proves 443 + TLS work; only a
    connection-level failure is False."""
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            "https://example.com/", method="HEAD",
            headers={"User-Agent": "VYUK/5.45 diagnosis"})
        try:
            with urllib.request.urlopen(req, timeout=2.5):
                return True
        except urllib.error.HTTPError:
            return True  # the server answered — HTTPS works
    except Exception:  # noqa: BLE001
        return False

def _probe_network_diag():
    """The N5 diagnosis ladder as one probe: {"gateway_ok",
    "dns_ok", "https_ok"} with True/False/None per step. Runs
    the steps in ladder order and stops descending at the first
    hard False (deeper steps are meaningless below a dead rung —
    and skipping them keeps the offline path fast)."""
    diag = {"gateway_ok": None, "dns_ok": None, "https_ok": None}
    try:
        diag["gateway_ok"] = _gateway_reachable()
        if diag["gateway_ok"] is False:
            return diag
        diag["dns_ok"] = _dns_resolves()
        if diag["dns_ok"] is False:
            return diag
        diag["https_ok"] = _https_fetch_ok()
    except Exception:  # noqa: BLE001 — a broken ladder is "unknown"
        pass
    return diag

def diagnose_network_verdict(diag):
    """The plain-words verdict for one diagnosis result, or None
    when the ladder produced nothing usable (the caller then
    keeps the plain pre-N5 offline sentence). Distinct rungs say
    distinct things — that is the whole point of the ladder."""
    if not isinstance(diag, dict):
        return None
    gateway, dns, https = (diag.get("gateway_ok"), diag.get("dns_ok"),
                           diag.get("https_ok"))
    if gateway is False:
        return "your router isn't answering"
    if dns is False:
        return "DNS isn't resolving"
    if https is False:
        return "Wi-Fi is up but the internet isn't answering"
    if gateway is True or dns is True or https is True:
        return ("though quick checks look fine, so it may be a "
                "brief blip")
    return None
