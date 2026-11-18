"""Performance honesty: own-process load, activity-log rotation, load-line text.

Fix 6 (2026-11-05; owner field report: "performance overall is not smooth").

Lab profile, all measured in the lab before any cut (see prove_perf.py):

- Startup integrity: ``verify_install_tree()`` hashes 1,208 files in
  0.74 s on the lab VM, on the launch path before first paint
  (``main.py`` called ``integrity.check_and_log()`` before the
  ``QApplication`` even existed). On a Windows disk with Defender
  scanning that is plausibly 2-5 s of launch the owner feels every
  time. The check is report-only and never blocks startup, so the
  fix is to run it AFTER first paint, not to weaken it.
- Doctor self-inflation: Doctor's own ``check_compile`` py-compiles
  1,216 .py files in 3.36 s of full CPU in Doctor's own process,
  while the load numbers it reports are machine-wide psutil reads
  (``core/watchers_network_system.py::_probe_load``). The field
  "100% CPU / 91% memory" was measured mid-Doctor-run — Doctor
  measuring itself plus the machine, not VYUK's idle load.
- activity.log is unbounded (321,694 B / 4,463 lines at batch
  start) and ``get_activity_log`` reads it whole on every call
  (2-6 ms today, growing linearly with the file).
- Steady state: the four always-on animations (main orb, mini orb,
  wave ribbon, particle field) cost ~11-15% of one core at
  400x400 offscreen, 0.8% with all hidden. The orb paints in
  ~1.4 ms/frame warmed, all states alike; live runs at 16 ms,
  idle at 33 ms.
- NOT hot, measured: tools-registry import 0.26-0.30 s, brain
  import 0.02 s, local model / Whisper lazy, wake-word spotter on
  its own thread, scheduler 1 s tick, watchers seconds-scale poll,
  mic/speaker levels are cached floats. No busy loop, no per-tick
  heavy work found — so there is no single hot bug to excise.

ONE design — "pay nothing before first paint; measure honestly
afterwards":

1. ``main.py`` defers the integrity hash to a daemon thread started
   after ``window.show()``. Same check, same loud logging.
2. This module reports the OWN process's load separately from the
   machine's, so the next field report can tell VYUK's load apart
   from Chrome's and Doctor's own.
3. ``rotate_activity_log`` caps the log at 1 MB (newest 2,000
   lines), called once per launch from the deferred thread.
4. The idle orb rate drops 33 ms -> 50 ms: the breathe is slow and
   dt-corrected, so the motion is identical, just fewer frames
   (~4.5% of a core -> ~2.8% in the lab). Live stays 16 ms.

This module is stdlib-only at import time; psutil is imported
lazily and its absence is reported honestly, never faked.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = [
    "activity_log_path",
    "format_load_line",
    "log_size_bytes",
    "process_load",
    "rotate_activity_log",
]

# Rotation bounds for data/activity.log (Fix 6): the log is a
# human-readable append-only record; trimming its oldest lines
# is standard log rotation. Nothing reads the log back except
# get_activity_log (last-N-lines) and Doctor, so no reader loses
# anything it depends on. The audit chain (core/audit_chain.py)
# is a separate store with its own rotation and is untouched.
LOG_MAX_BYTES = 1_048_576
LOG_KEEP_LINES = 2_000

_PSUTIL = None
_PSUTIL_TRIED = False
_PROCESS_PRIMED = False
_MACHINE_PRIMED = False


def _psutil():
    """psutil module, or None when it is not installed. Cached; never raises."""
    global _PSUTIL, _PSUTIL_TRIED
    if _PSUTIL_TRIED:
        return _PSUTIL
    _PSUTIL_TRIED = True
    try:
        import psutil as _ps  # noqa: PLC0415 — lazy: stdlib-only at import time
    except Exception:  # noqa: BLE001 — psutil is optional
        _PSUTIL = None
    else:
        _PSUTIL = _ps
    return _PSUTIL


def _package_root() -> Path:
    """The install-tree root (this file lives in <root>/core/)."""
    return Path(__file__).resolve().parent.parent


def activity_log_path() -> Path:
    """data/activity.log under the install-tree root."""
    return _package_root() / "data" / "activity.log"


def log_size_bytes(path=None) -> int:
    """Size of the activity log in bytes; 0 when it does not exist. Never raises."""
    try:
        return (Path(path) if path is not None else activity_log_path()).stat().st_size
    except Exception:  # noqa: BLE001 — a missing log is 0, not an error
        return 0


def process_load(cpu_interval=None) -> dict:
    """Own-process load, separated from machine load. Never raises.

    Returns {"psutil_available": bool, "process_rss_mb": float|None,
    "process_cpu_percent": float|None, "machine_cpu_percent": float|None,
    "machine_mem_percent": float|None}.

    Honesty notes (psutil's contract, not ours): ``cpu_percent`` with
    ``interval=None`` is non-blocking and its FIRST call in a process
    returns 0.0 — a warm-up tick, not a measurement. The first
    non-blocking call therefore primes and reports None for both CPU
    numbers; the second and later calls report real values. Pass a
    ``cpu_interval`` (seconds, e.g. 0.1) for one blocking call that
    returns a real process-CPU value immediately (Doctor does this).
    RSS and machine memory are real on every call.
    """
    out = {
        "psutil_available": False,
        "process_rss_mb": None,
        "process_cpu_percent": None,
        "machine_cpu_percent": None,
        "machine_mem_percent": None,
    }
    psutil = _psutil()
    if psutil is None:
        return out
    out["psutil_available"] = True
    global _PROCESS_PRIMED, _MACHINE_PRIMED
    try:
        proc = psutil.Process()
    except Exception:  # noqa: BLE001 — no process handle, no numbers
        return out
    try:
        out["process_rss_mb"] = proc.memory_info().rss / 1_000_000.0
    except Exception:  # noqa: BLE001 — RSS is best-effort
        pass
    try:
        if cpu_interval is not None:
            out["process_cpu_percent"] = float(
                proc.cpu_percent(interval=cpu_interval))
        elif _PROCESS_PRIMED:
            out["process_cpu_percent"] = float(
                proc.cpu_percent(interval=None))
        else:
            proc.cpu_percent(interval=None)  # prime: 0.0, discarded
            _PROCESS_PRIMED = True
    except Exception:  # noqa: BLE001 — CPU% is best-effort
        pass
    try:
        if _MACHINE_PRIMED:
            out["machine_cpu_percent"] = float(
                psutil.cpu_percent(interval=None))
        else:
            psutil.cpu_percent(interval=None)  # prime: 0.0, discarded
            _MACHINE_PRIMED = True
        out["machine_mem_percent"] = float(
            psutil.virtual_memory().percent)
    except Exception:  # noqa: BLE001 — machine numbers are best-effort
        pass
    return out


def rotate_activity_log(path=None, max_bytes=LOG_MAX_BYTES,
                        keep_lines=LOG_KEEP_LINES) -> dict:
    """Cap the activity log: when it exceeds ``max_bytes``, keep only
    the newest ``keep_lines`` lines. Returns {"rotated": bool,
    "before_bytes": int, "after_bytes": int, "kept_lines": int}.
    A file under the cap is left byte-identical. Atomic (temp file
    in the same directory + os.replace). Never raises."""
    log_path = Path(path) if path is not None else activity_log_path()
    result = {"rotated": False, "before_bytes": 0,
              "after_bytes": 0, "kept_lines": 0}
    try:
        before = log_path.stat().st_size
    except Exception:  # noqa: BLE001 — missing log: nothing to rotate
        return result
    result["before_bytes"] = before
    result["after_bytes"] = before
    if before <= max_bytes:
        try:
            result["kept_lines"] = len(
                log_path.read_bytes().splitlines())
        except Exception:  # noqa: BLE001 — count is best-effort
            pass
        return result
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        kept = lines[-keep_lines:] if keep_lines > 0 else []
        body = "\n".join(kept)
        if body:
            body += "\n"
        fd, tmp_name = tempfile.mkstemp(
            dir=str(log_path.parent), prefix=".activity-rotate-",
            suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp_name, log_path)
        except Exception:  # noqa: BLE001 — a failed rotate keeps the old file
            try:
                os.unlink(tmp_name)
            except Exception:  # noqa: BLE001 — temp cleanup is best-effort
                pass
            raise
        result.update({"rotated": True,
                       "after_bytes": log_path.stat().st_size,
                       "kept_lines": len(kept)})
    except Exception:  # noqa: BLE001 — rotation must never break the app
        result.update({"rotated": False, "after_bytes": before})
    return result


def format_load_line(load, frame=None) -> list:
    """Briefing-line parts for the load read (pure function).

    ``load`` is a ``process_load()`` dict; ``frame`` is a
    ``{"ema_ms": float|None, "quality": str}`` orb frame report or
    None. Pieces with no real number are omitted, never invented:
    "VYUK 412 MB" (own-process RSS), "orb 1.4 ms full"
    (GUI-thread frame time + watchdog quality). Returns [] when
    nothing is measurable.
    """
    parts = []
    if isinstance(load, dict):
        rss = load.get("process_rss_mb")
        if isinstance(rss, (int, float)) and rss >= 0:
            parts.append(f"VYUK {rss:.0f} MB")
    if isinstance(frame, dict):
        ema = frame.get("ema_ms")
        quality = frame.get("quality")
        if isinstance(ema, (int, float)):
            if isinstance(quality, str) and quality:
                parts.append(f"orb {ema:.1f} ms {quality}")
            else:
                parts.append(f"orb {ema:.1f} ms")
        elif isinstance(quality, str) and quality:
            parts.append(f"orb {quality}")
    return parts
