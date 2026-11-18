"""Wave 9 (v5.64) — state-driven wallpaper PILOT (WinDynamicDesktop-style).

PILOT, not live: this module models a wallpaper that follows the
time of day / user state (day, dusk, night, focus, away) in the
style of WinDynamicDesktop, and can apply it on Windows — it is
wired through the confirm-gated wallpaper_apply funnel tool, and
applying is an explicit human-confirmed decision, never
automatic. Field validation on real Windows is still owed.
Nothing here changes the desktop on import, and nothing
here runs on Linux.

Art rules (hard):

* HER OWN ART ONLY. This module bundles NO third-party art and NO
  image files at all — every schedule entry points at a path the
  user supplies in their own art directory.
* NEVER Apple assets: no macOS dynamic-wallpaper files, no Apple
  artwork, no Apple formats are read, bundled or referenced. The
  schedule format is our own (state -> image path), not Apple's.
* MPL code clean: WinDynamicDesktop itself is MPL-licensed and is
  NOT vendored, copied or depended on here — this is a clean-room
  state machine in the WinDynamicDesktop *style* only (the idea of
  time/state-driven wallpaper), written from scratch.

Windows apply path (stdlib only, ``ctypes`` +
``SystemParametersInfoW`` with ``SPI_SETDESKWALLPAPER``) is
platform-guarded: on non-Windows hosts ``apply_wallpaper`` returns
honest unavailable and changes nothing.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path

MODULE_VERSION = "5.64.0"

# The states a schedule may use. Unknown states are rejected by
# validation rather than silently mapped.
STATES = ("day", "dusk", "night", "focus", "away")

# Default day-part schedule (state -> hour range, 24h clock).
# A schedule entry is (state, start_hour, end_hour); ranges are
# half-open [start, end) and must tile 0..24 without overlap.
DEFAULT_SCHEDULE: tuple[tuple[str, int, int], ...] = (
    ("night", 0, 6),
    ("day", 6, 18),
    ("dusk", 18, 21),
    ("night", 21, 24),
)


@dataclass(frozen=True)
class WallpaperEntry:
    """One state -> user-supplied art mapping."""

    state: str
    art_path: str  # user art only; never a bundled/third-party path


def is_windows() -> bool:
    return platform.system() == "Windows"


def state_for_hour(hour: int,
                   schedule: tuple = DEFAULT_SCHEDULE) -> str:
    """State active at ``hour`` (0..23); ``ValueError`` outside it."""
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"hour must be 0..23, got {hour!r}")
    for state, start, end in schedule:
        if start <= hour < end:
            return state
    raise ValueError(f"schedule does not cover hour {hour}")


def validate_schedule(entries: list[WallpaperEntry],
                      art_dir: str | Path | None = None) -> dict:
    """Validate a state->art schedule.

    Checks: every state known; no state listed twice; every art
    path (when ``art_dir`` is given) exists inside ``art_dir``
    (no escaping via ``..``); at least one entry. Returns
    ``{"valid": bool, "reasons": [...]}``. No art is bundled, so an
    entry pointing at a missing file is invalid, not defaulted.
    """
    reasons: list[str] = []
    seen: set[str] = set()
    root: Path | None = Path(art_dir).resolve() if art_dir else None
    for entry in entries:
        if entry.state not in STATES:
            reasons.append(f"unknown-state: {entry.state}")
        if entry.state in seen:
            reasons.append(f"duplicate-state: {entry.state}")
        seen.add(entry.state)
        if root is not None:
            candidate = (root / entry.art_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                reasons.append(f"art-escapes-art-dir: {entry.art_path}")
                continue
            if not candidate.is_file():
                reasons.append(f"art-missing: {entry.art_path}")
    if not entries:
        reasons.append("empty-schedule")
    return {"valid": bool(not reasons), "reasons": reasons}


def resolve_art(state: str,
                entries: list[WallpaperEntry]) -> str | None:
    """Art path for ``state``, or None when the schedule lacks it."""
    for entry in entries:
        if entry.state == state:
            return entry.art_path
    return None


def pilot_status() -> dict:
    """PILOT status: what this module is, and what closes it.

    Wiring: the confirm-gated wallpaper_apply funnel tool
    (2026-11-03, code-rulings delegation) and the revert half
    (save-previous slot + confirm-gated wallpaper_revert tool,
    built 2026-11-05 in the lab). Per-monitor set + revert
    (IDesktopWallpaper, device-name-keyed records) landed in
    the lab 2026-11-06 (R4). Closing step: Windows field
    validation (apply + revert on a real Windows host with
    user-supplied art, persistence flags, multi-monitor
    behaviour). Until field validation lands it stays a pilot.
    """
    return {
        "disposition": "PILOT",
        "wired": True,
        "applies_on": "windows-only",
        "available_here": is_windows(),
        "bundled_art": False,
        "third_party_art": False,
        "apple_assets": False,
        "closing_step": ("Windows field validation with user-supplied "
                         "art (apply + revert on a real Windows host, "
                         "persistence flags, multi-monitor behaviour)"),
    }


def apply_wallpaper(art_path: str | Path, monitor=None) -> dict:
    """Set the desktop wallpaper to ``art_path`` (Windows only).

    ``monitor`` is None (the legacy path: one image on ALL
    monitors via SPI) or a monitor selector (``"primary"``,
    ``"left"``, ``"second monitor"``, a device name, or
    ``"all"`` — resolved against the CURRENT monitor table, see
    ``core/desktop/monitors.py``). A per-monitor set goes through
    IDesktopWallpaper (``core/wallpaper_monitors.py``) and saves
    that monitor's previous first.

    Returns ``{"applied": bool, "reason": str}`` — plus
    ``"monitor"`` and ``"fell_back"`` on the per-monitor path.
    On non-Windows hosts this is honest unavailable and changes
    nothing. On Windows a missing file refuses before any system
    call. When the per-monitor API is unavailable the set falls
    back to the SPI all-monitors path and says so
    (``reason`` "per-monitor-unavailable-fell-back-to-all") — it
    never silently pretends per-monitor worked. When the API
    exists but refuses, the failure is honest and there is no
    fallback (falling back would change ALL monitors when one
    was asked for).
    """
    if not is_windows():
        return {"applied": False, "reason": "windows-only"}
    path = Path(art_path)
    if not path.is_file():
        return {"applied": False, "reason": "art-missing"}
    if monitor is None:
        # Revert batch (2026-11-05): no wallpaper change without
        # a recorded previous — save the current desktop first,
        # and refuse the change (changing nothing) when the save
        # fails.
        saved = save_previous_wallpaper()
        if not saved["saved"]:
            return {"applied": False, "reason": saved["reason"]}
        try:
            ok = _set_wallpaper_via_spi(str(path.resolve()))
        except Exception as exc:
            return {"applied": False,
                    "reason": f"system-call-failed: {exc.__class__.__name__}"}
        return {"applied": bool(ok),
                "reason": "ok" if ok else "system-call-refused"}
    return set_wallpaper_for_monitors(str(path.resolve()), monitor)


def _set_wallpaper_via_spi(path_str: str) -> bool:
    """The Windows system call itself, isolated as a seam.

    Production behaviour is exactly the ctypes
    SystemParametersInfoW call this module used inline before
    (SPI_SETDESKWALLPAPER with SPIF_UPDATEINIFILE |
    SPIF_SENDCHANGE, so the change persists and the system is
    notified). Tests monkeypatch this module attribute (together
    with is_windows) to prove the apply path without a Windows
    desktop; the lab has no Windows desktop, so the real call is
    field work, not lab work.
    """
    import ctypes
    SPI_SETDESKWALLPAPER = 0x0014
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDCHANGE = 0x02
    return bool(ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, path_str,
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))


# ------------------------------------------------------------ revert
# Revert batch (2026-11-05, owner field report: VYUK changed his
# wallpaper and he could not revert it — no revert existed
# anywhere in the tree and neither set path saved the previous
# wallpaper, so a revert had nothing to restore to).
#
# THE ONE RULE (the elegant solution — one slot closes the whole
# loophole family, stated in the wallpaper_revert tool text and
# pinned in tests/test_wallpaper_revert.py):
#   The slot always holds the desktop (image + style) as it was
#   immediately before the most recent VYUK wallpaper change.
#   Revert restores that slot and NEVER clears it, so reverting
#   twice restores the same wallpaper (idempotent); each new VYUK
#   change overwrites the slot with the then-current desktop, so
#   revert after another VYUK change restores the state before
#   THAT change (the earlier one is gone — stated honestly, not
#   hidden); a change made outside VYUK never touches the slot,
#   so revert still undoes the last VYUK change.
#
# Loopholes this closes, and how:
#   * previous unknown (current wallpaper was set by an older
#     build or by Windows itself — nothing was ever saved):
#     revert refuses "no-previous-saved" and names Windows
#     Settings > Personalization > Background instead of
#     inventing a wallpaper.
#   * previous file deleted / moved / on a removed drive:
#     revert refuses "previous-file-missing" and KEEPS the
#     record, so a later revert works if the file comes back.
#   * previous was a solid colour / slideshow / empty registry
#     value (not a file path): the slot stores the registry
#     Wallpaper value verbatim, including "" — revert applies
#     it via the same SPI call ("" clears to solid colour) and
#     restores the saved style. The saved image is NOT
#     re-validated with is_image_file (it was a valid desktop
#     before); only existence / is-a-file is checked.
#   * style of the previous: the slot stores WallpaperStyle +
#     TileWallpaper alongside the image, and revert writes both
#     back before the SPI call — not just the image.
#   * multi-monitor (R4, 2026-11-06): the slot is version 2 —
#     the flat fields stay the global record (one image on all
#     monitors, the SPI behaviour) and "monitors" holds one
#     per-monitor record per device name (see the per-monitor
#     section below). A per-monitor set saves that monitor's
#     previous first; a per-monitor revert restores only that
#     monitor. Records are keyed by device name, never index,
#     so a reorder never renames a monitor's previous.
#   * every set path saves: apply_wallpaper (here), set_wallpaper
#     (core/skills_desktop_prefs.py) and display_control's
#     set_wallpaper leg (core/desktop/control_plus_desktop.py)
#     all call save_previous_wallpaper() BEFORE any registry or
#     SPI write — one shared seam, not three copies. make_wallpaper
#     is covered through set_wallpaper.
#   * save failure is fail-closed: a change whose previous cannot
#     be recorded refuses and changes nothing — a change without
#     a recorded previous IS the bug being fixed.
#   * durability: the record lives in the runtime data dir
#     (data/wallpaper_previous.json, the same home as the PF-18
#     stores, excluded from the shipped ZIP's file list) and is
#     written atomically (mkstemp + os.replace, the PF-18
#     pattern), so it survives a restart.
#
# Rejected alternatives (with reasons):
#   * multi-slot history / undo stack — more state and more
#     rules for a question the owner never asked; one slot
#     answers the field complaint exactly.
#   * restoring Windows' own "previous" — Windows keeps no
#     previous-wallpaper record; there is nothing to read.
#   * consuming revert (clear the slot on revert) — loses the
#     record if the revert itself is interrupted, and a second
#     revert would refuse even though the desktop is still the
#     reverted one.
#   * swap-on-revert (toggle) — surprising: reverting twice
#     would re-apply the wallpaper the user asked to undo.
#   * copying the previous image file into data/ — copies user
#     art into the app's data dir (storage, ownership and
#     staleness questions); storing the PATH is enough, with
#     the deleted-file refusal above.
#   * reverting through wallpaper_apply with the saved path —
#     wrong gates: the pilot-art rules (opt-in, bundled-art,
#     art-dir containment) would refuse a saved path that is
#     the user's own prior desktop, not pilot art.
#   * making revert free (no confirm) — a desktop change is
#     persistent, so revert is confirm-class like apply.

_PREVIOUS_FILE = "wallpaper_previous.json"

# Tests only: point the record at a temp file (the PF-18
# _PATH_OVERRIDE pattern). Production always uses data/.
_PATH_OVERRIDE = None

_DESKTOP_KEY = r"Control Panel\Desktop"

_SETTINGS_HINT = "Windows Settings > Personalization > Background"


def _previous_path() -> Path:
    """The revert-slot record. __file__-relative (stdlib-only
    module — no config import, so no import cycle for any
    caller); the data/ dir is runtime state, not shipped code."""
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    return (Path(__file__).resolve().parent.parent
            / "data" / _PREVIOUS_FILE)


def _read_current_wallpaper() -> dict | None:
    """Current desktop {image, style, tile} from the registry,
    or None when it cannot be read (incl. non-Windows).
    Injectable seam: tests monkeypatch this module attribute
    together with is_windows. Never raises."""
    if not is_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _DESKTOP_KEY) as key:
            image, _t = winreg.QueryValueEx(key, "Wallpaper")
            style, _t = winreg.QueryValueEx(key, "WallpaperStyle")
            tile, _t = winreg.QueryValueEx(key, "TileWallpaper")
    except Exception:  # noqa: BLE001 — incl. winreg missing on Linux
        return None
    # Missing style values (a fresh Desktop key) read as fill,
    # the style Windows itself writes for a filled wallpaper.
    return {"image": str(image or ""),
            "style": str(style or "10"),
            "tile": str(tile or "0")}


def _write_wallpaper_style(style: str, tile: str) -> bool:
    """Write WallpaperStyle/TileWallpaper; False (never raising)
    on failure. Injectable seam, like _read_current_wallpaper."""
    if not is_windows():
        return False
    try:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                _DESKTOP_KEY) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0,
                              winreg.REG_SZ, str(style))
            winreg.SetValueEx(key, "TileWallpaper", 0,
                              winreg.REG_SZ, str(tile))
    except Exception:  # noqa: BLE001 — incl. winreg missing on Linux
        return False
    return True


def _read_record() -> dict | None:
    """The raw slot payload, or None when missing/corrupt.
    Never raises. Version 1 is the flat record
    {version: 1, image, style, tile}; version 2 keeps those
    flat fields (the global record) and adds "monitors":
    {device_name: {image, style, tile, kind, color,
    slideshow}} — present only when non-empty."""
    try:
        payload = json.loads(
            _previous_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt/unreadable
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_record(payload: dict) -> bool:
    """Write the slot payload atomically (mkstemp + os.replace,
    the PF-18 pattern). False (never raising) on failure."""
    try:
        path = _previous_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=1,
                                        sort_keys=True))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001 — caller reports False
        return False
    return True


def save_previous_wallpaper() -> dict:
    """Record the current desktop as the revert slot.

    Returns {"saved": bool, "reason": str}. Fail-closed:
    Windows-only; a read or write failure refuses. Never
    raises. Callers change nothing when this refuses.
    Version 2: the flat fields stay the global record and any
    per-monitor records already on file are preserved.
    """
    if not is_windows():
        return {"saved": False, "reason": "windows-only"}
    current = _read_current_wallpaper()
    if current is None:
        return {"saved": False, "reason": "could-not-read-current"}
    payload = _read_record() or {}
    payload.update({"version": 2, **current})
    if not _write_record(payload):
        return {"saved": False, "reason": "could-not-save-previous"}
    return {"saved": True, "reason": "ok"}


def load_previous_wallpaper() -> dict | None:
    """The saved revert slot {image, style, tile}, or None when
    the record is missing or corrupt. Version 1 and version 2
    payloads both read (the flat fields ARE the global record
    in both). Never raises."""
    payload = _read_record()
    if payload is None:
        return None
    image = payload.get("image")
    style = payload.get("style")
    tile = payload.get("tile")
    if not isinstance(image, str) \
            or not isinstance(style, str) \
            or not isinstance(tile, str):
        return None
    return {"image": image, "style": style, "tile": tile}


def revert_wallpaper(monitor=None) -> dict:
    """Restore the saved previous desktop.

    ``monitor`` is None (the legacy path: restore the global
    slot on ALL monitors via SPI) or a monitor selector
    (``"primary"``, ``"left"``, ``"second monitor"``, a device
    name, or ``"all"`` — resolved against the CURRENT monitor
    table). A per-monitor revert restores only that monitor's
    record; ``"all"`` reverts every recorded monitor
    independently — a monitor that has vanished since the save
    is reported ("monitor-gone") and skipped, never breaking
    the revert for the monitors that remain. A newly attached
    monitor has no record and is never given another monitor's
    previous: records are keyed by device name, never index.

    Returns {"reverted": bool, "reason": str, "image": str,
    "style": str} on success, {"reverted": False, "reason": str}
    on refusal — plus "per_monitor" on the "all" path. Reasons:
    windows-only, no-previous-saved, previous-file-missing,
    previous-not-a-file, could-not-restore-style,
    system-call-failed, system-call-refused, read-back-mismatch,
    no-previous-saved-for-monitor, monitor-gone,
    unknown-monitor, per-monitor-unavailable, partial. The slot
    is never cleared by a revert (see the ONE RULE above); a
    refusal keeps the record. Never raises."""
    if not is_windows():
        return {"reverted": False, "reason": "windows-only"}
    if monitor is None:
        return _revert_wallpaper_global()
    from . import wallpaper_monitors as _monitors

    selector = str(monitor).strip()
    if selector.lower() == "all":
        return _revert_wallpaper_all(_monitors)
    resolved, refusal, _is_all = _monitors.resolve_wallpaper_monitor(
        selector)
    if resolved is None:
        return {"reverted": False,
                "reason": f"unknown-monitor: {refusal}"}
    device = resolved.get("monitor_id") or resolved.get("device") or ""
    return revert_wallpaper_for(device)


def _revert_wallpaper_global() -> dict:
    """The legacy global revert (Fix 2, unchanged): restore the
    flat slot on ALL monitors via SPI."""
    saved = load_previous_wallpaper()
    if saved is None:
        return {"reverted": False, "reason": "no-previous-saved"}
    image = saved["image"]
    if image:
        candidate = Path(image)
        if not candidate.exists():
            return {"reverted": False,
                    "reason": "previous-file-missing"}
        if not candidate.is_file():
            return {"reverted": False,
                    "reason": "previous-not-a-file"}
    if not _write_wallpaper_style(saved["style"], saved["tile"]):
        return {"reverted": False,
                "reason": "could-not-restore-style"}
    try:
        ok = _set_wallpaper_via_spi(image)
    except Exception as exc:  # noqa: BLE001 — seam failure is data
        return {"reverted": False,
                "reason": f"system-call-failed: {exc.__class__.__name__}"}
    if not ok:
        return {"reverted": False, "reason": "system-call-refused"}
    # Read-back, mirroring set_wallpaper's confirmation: the
    # SPI bool alone is not trusted. A failed read-back (None)
    # accepts the SPI result; a mismatching value refuses.
    current = _read_current_wallpaper()
    if current is not None \
            and current["image"].lower() != image.lower():
        return {"reverted": False, "reason": "read-back-mismatch",
                "image": image, "style": saved["style"]}
    return {"reverted": True, "reason": "ok",
            "image": image, "style": saved["style"]}


def _revert_wallpaper_all(monitors_mod) -> dict:
    """Revert every recorded monitor independently. A monitor
    that has vanished is reported and skipped; the others still
    revert. Returns {"reverted": bool(all succeeded),
    "reason": "ok" | "partial" | <single refusal>,
    "per_monitor": {device: result}}."""
    payload = _read_record()
    monitors = (payload or {}).get("monitors") or {}
    if not isinstance(monitors, dict) or not monitors:
        return {"reverted": False, "reason": "no-previous-saved",
                "per_monitor": {}}
    table, _note = monitors_mod.enumerate_monitors()
    present = {
        str(m.get("monitor_id") or m.get("device") or "")
        for m in table
    }
    results = {}
    for device in sorted(monitors):
        if device not in present:
            results[device] = {"reverted": False,
                               "reason": "monitor-gone"}
            continue
        results[device] = revert_wallpaper_for(device)
    failed = [d for d, r in results.items() if not r.get("reverted")]
    if not failed:
        return {"reverted": True, "reason": "ok",
                "per_monitor": results}
    if len(failed) == len(results):
        return {"reverted": False, "reason": "partial",
                "per_monitor": results}
    return {"reverted": False, "reason": "partial",
            "per_monitor": results}


# ------------------------------------------------- per-monitor (R4)
# Refinement R4 (2026-11-06): SPI sets one wallpaper on ALL
# monitors. This section is the per-monitor half — one slot
# file (version 2), one resolver (core/desktop/monitors.py),
# one API (IDesktopWallpaper via core/wallpaper_monitors.py),
# one fallback (SPI, honest).
#
# Loopholes this closes, and how:
#   * one image on all monitors: set_wallpaper_for_monitors
#     targets ONE monitor (or "all", each saved + set
#     individually) through IDesktopWallpaper.SetWallpaper,
#     leaving the others exactly as they are. The selector is
#     plain language ("primary", "left", "second monitor", a
#     device name) resolved against the CURRENT table — never
#     a bare index the user has to guess.
#   * the revert slot must be per-monitor too: "monitors" in
#     the same record file, one entry per device name, each
#     holding that monitor's image + style + tile + kind +
#     color + slideshow as they were immediately before the
#     most recent VYUK change to THAT monitor. Reverting one
#     monitor restores only its entry.
#   * a monitor that disappears: revert "all" reports it
#     ("monitor-gone") and skips it; the monitors that remain
#     still revert. A single-monitor revert for a vanished
#     monitor refuses "monitor-gone" instead of inventing a
#     target.
#   * a monitor that appears: records are keyed by device
#     name, never by position in a list — a newly attached
#     monitor has no record and gets none, it never inherits
#     another monitor's previous.
#   * solid colour / slideshow per-monitor: the entry stores
#     "kind" verbatim ("image" | "solid" | "slideshow") plus
#     the background colour and the slideshow engine state.
#     Revert restores the kind: an image by path, a solid
#     colour by clearing the wallpaper and restoring the
#     colour, a slideshow by restoring the engine state plus
#     the visible frame. A slideshow revert verifies the
#     ENGINE state, not the frame — the frame advances by
#     design, so a frame read-back would falsely refuse.
#   * single-monitor machines: monitor=None is the legacy SPI
#     path, byte-identical in behaviour; "primary" / "all" on
#     one monitor set that one monitor and save its record.
#   * the old SPI path stays as the fallback: when the
#     per-monitor API cannot be created at all, the set falls
#     back to SPI on all monitors and SAYS so
#     ("per-monitor-unavailable-fell-back-to-all"). When the
#     API exists but refuses, there is no fallback — falling
#     back would change ALL monitors when one was asked for.
#     Revert never falls back to SPI: restoring the GLOBAL
#     slot's image on all monitors is not a per-monitor
#     revert, so it refuses "per-monitor-unavailable" and
#     keeps the record.
#
# Rejected alternatives (with reasons):
#   * a second slot file for per-monitor records — two files,
#     two atomic writes, two failure modes; one file holds
#     one rule (the slot holds the desktop before the most
#     recent VYUK change — globally AND per monitor).
#   * a second resolver in this module — monitors.py already
#     resolves device names, ordinals, positions and "all"
#     with tested refusal wording; a second one would drift.
#   * PowerShell / registry-only per-monitor writes — no
#     per-monitor support in either; Windows ignores
#     per-monitor registry writes, SPI is the only consumer.
#   * index-keyed records — the index is a presentation
#     label that shifts on reorder; the device name is the
#     identity that survives it.
#   * storing only the image per monitor — loses the kind:
#     a solid-colour monitor would revert to a stale image.

def _current_for_monitor(monitor_id: str) -> dict | None:
    """The current per-monitor desktop {image, style, tile,
    kind, color, slideshow}, or None when it cannot be read
    (incl. non-Windows). The style/tile are the GLOBAL
    registry values (Windows keeps one style for all
    monitors); the image, kind, colour and slideshow state
    are per-monitor. Never raises."""
    from . import wallpaper_monitors as _monitors

    image = _monitors.get_wallpaper_for(monitor_id)
    if image is None:
        return None
    current = _read_current_wallpaper()
    if current is None:
        return None
    slideshow = _monitors.get_slideshow_state()
    kind = "solid" if not image else (
        "slideshow" if (slideshow or {}).get("active") else "image")
    return {"image": image, "style": current["style"],
            "tile": current["tile"], "kind": kind,
            "color": _monitors.get_background_color(),
            "slideshow": slideshow}


def save_previous_wallpaper_for(monitor_id: str) -> dict:
    """Record one monitor's current desktop as its per-monitor
    revert record. Returns {"saved": bool, "reason": str}.
    Fail-closed like the global save: Windows-only; a read or
    write failure refuses and the caller changes nothing.
    Preserves the global record and every other monitor's
    record. Never raises."""
    if not is_windows():
        return {"saved": False, "reason": "windows-only"}
    if not monitor_id:
        return {"saved": False, "reason": "unknown-monitor"}
    current = _current_for_monitor(monitor_id)
    if current is None:
        return {"saved": False, "reason": "could-not-read-current"}
    payload = _read_record() or {}
    monitors = payload.get("monitors")
    if not isinstance(monitors, dict):
        monitors = {}
    monitors[str(monitor_id)] = current
    payload["monitors"] = monitors
    if "version" not in payload:
        payload["version"] = 2
    if not _write_record(payload):
        return {"saved": False, "reason": "could-not-save-previous"}
    return {"saved": True, "reason": "ok"}


def load_previous_wallpaper_for(monitor_id: str) -> dict | None:
    """One monitor's saved revert record, or None when there is
    no record for it, or the record is corrupt. Never raises."""
    payload = _read_record()
    if payload is None:
        return None
    monitors = payload.get("monitors")
    if not isinstance(monitors, dict):
        return None
    entry = monitors.get(str(monitor_id))
    if not isinstance(entry, dict):
        return None
    image = entry.get("image")
    style = entry.get("style")
    tile = entry.get("tile")
    kind = entry.get("kind")
    if not isinstance(image, str) \
            or not isinstance(style, str) \
            or not isinstance(tile, str) \
            or kind not in ("image", "solid", "slideshow"):
        return None
    return {"image": image, "style": style, "tile": tile,
            "kind": kind,
            "color": entry.get("color") if isinstance(
                entry.get("color"), str) else "",
            "slideshow": entry.get("slideshow") if isinstance(
                entry.get("slideshow"), dict) else None}


def revert_wallpaper_for(monitor_id: str) -> dict:
    """Restore one monitor's saved previous desktop.

    Returns {"reverted": bool, "reason": str, "image": str,
    "style": str, "monitor": str} on success, {"reverted":
    False, "reason": str} on refusal. Reasons: windows-only,
    unknown-monitor, no-previous-saved-for-monitor,
    previous-file-missing, previous-not-a-file,
    per-monitor-unavailable, system-call-failed,
    system-call-refused, read-back-mismatch. The record is
    never cleared by a revert; a refusal keeps it. Never
    raises."""
    if not is_windows():
        return {"reverted": False, "reason": "windows-only"}
    if not monitor_id:
        return {"reverted": False, "reason": "unknown-monitor"}
    saved = load_previous_wallpaper_for(monitor_id)
    if saved is None:
        return {"reverted": False,
                "reason": "no-previous-saved-for-monitor"}
    from . import wallpaper_monitors as _monitors

    image = saved["image"]
    if saved["kind"] == "image" and image:
        candidate = Path(image)
        if not candidate.exists():
            return {"reverted": False,
                    "reason": "previous-file-missing"}
        if not candidate.is_file():
            return {"reverted": False,
                    "reason": "previous-not-a-file"}
    if not _monitors.per_monitor_available():
        return {"reverted": False,
                "reason": "per-monitor-unavailable"}
    if not _write_wallpaper_style(saved["style"], saved["tile"]):
        return {"reverted": False,
                "reason": "could-not-restore-style"}
    try:
        if saved["kind"] == "slideshow":
            state = saved["slideshow"] or {}
            if not _monitors.set_slideshow_state(state):
                return {"reverted": False,
                        "reason": "could-not-restore-slideshow"}
            ok = _monitors.set_wallpaper_for(monitor_id, image)
        elif saved["kind"] == "solid":
            ok = _monitors.set_wallpaper_for(monitor_id, "")
            if ok and saved.get("color"):
                _monitors.set_background_color(saved["color"])
        else:
            ok = _monitors.set_wallpaper_for(monitor_id, image)
    except Exception as exc:  # noqa: BLE001 — seam failure is data
        return {"reverted": False,
                "reason": f"system-call-failed: {exc.__class__.__name__}"}
    if not ok:
        return {"reverted": False, "reason": "system-call-refused"}
    if saved["kind"] == "slideshow":
        # The frame advances by design — verify the ENGINE
        # state, not the frame. A failed read-back (None)
        # accepts the set result.
        state = _monitors.get_slideshow_state()
        if state is not None and not state.get("active"):
            return {"reverted": False,
                    "reason": "read-back-mismatch",
                    "image": image, "style": saved["style"],
                    "monitor": str(monitor_id)}
    else:
        current = _monitors.get_wallpaper_for(monitor_id)
        if current is not None \
                and current.lower() != image.lower():
            return {"reverted": False,
                    "reason": "read-back-mismatch",
                    "image": image, "style": saved["style"],
                    "monitor": str(monitor_id)}
    return {"reverted": True, "reason": "ok",
            "image": image, "style": saved["style"],
            "monitor": str(monitor_id)}


def set_wallpaper_for_monitor(image: str, monitor_id: str) -> dict:
    """Set ONE monitor's wallpaper to ``image`` — the shared
    seam every set path uses (apply_wallpaper,
    skills_desktop_prefs.set_wallpaper, display_control's
    set_wallpaper leg). Saves that monitor's previous first
    (fail-closed: a failed save changes nothing), then sets via
    IDesktopWallpaper and reads back via GetWallpaper.

    Returns {"applied": bool, "reason": str, "fell_back":
    bool, "monitor": str}. When the per-monitor API cannot be
    created at all, falls back to the SPI all-monitors path
    (saving the GLOBAL previous first) and says so
    ("per-monitor-unavailable-fell-back-to-all",
    fell_back True). When the API exists but refuses, the
    failure is honest with no fallback. Never raises."""
    if not is_windows():
        return {"applied": False, "reason": "windows-only",
                "fell_back": False, "monitor": str(monitor_id)}
    if not monitor_id:
        return {"applied": False, "reason": "unknown-monitor",
                "fell_back": False, "monitor": str(monitor_id)}
    from . import wallpaper_monitors as _monitors

    if not _monitors.per_monitor_available():
        saved = save_previous_wallpaper()
        if not saved["saved"]:
            return {"applied": False, "reason": saved["reason"],
                    "fell_back": False, "monitor": str(monitor_id)}
        try:
            ok = _set_wallpaper_via_spi(image)
        except Exception as exc:  # noqa: BLE001 — seam failure
            return {"applied": False,
                    "reason": f"system-call-failed: {exc.__class__.__name__}",
                    "fell_back": False, "monitor": str(monitor_id)}
        return {"applied": bool(ok),
                "reason": ("per-monitor-unavailable-fell-back-to-all"
                           if ok else "system-call-refused"),
                "fell_back": True, "monitor": str(monitor_id)}
    saved = save_previous_wallpaper_for(monitor_id)
    if not saved["saved"]:
        return {"applied": False, "reason": saved["reason"],
                "fell_back": False, "monitor": str(monitor_id)}
    try:
        ok = _monitors.set_wallpaper_for(monitor_id, image)
    except Exception as exc:  # noqa: BLE001 — seam failure is data
        return {"applied": False,
                "reason": f"system-call-failed: {exc.__class__.__name__}",
                "fell_back": False, "monitor": str(monitor_id)}
    if not ok:
        return {"applied": False, "reason": "system-call-refused",
                "fell_back": False, "monitor": str(monitor_id)}
    current = _monitors.get_wallpaper_for(monitor_id)
    if current is not None and current.lower() != image.lower():
        return {"applied": False, "reason": "read-back-mismatch",
                "fell_back": False, "monitor": str(monitor_id)}
    return {"applied": True, "reason": "ok",
            "fell_back": False, "monitor": str(monitor_id)}


def set_wallpaper_for_monitors(image: str, selector) -> dict:
    """Set ``image`` on the monitor(s) a selector names
    ("primary", "left", "second monitor", a device name, or
    "all"). Each target is saved first, then set
    individually; a failed save or set for one target changes
    nothing for that target and stops the rest (the earlier
    targets keep their new wallpaper — the result says
    exactly which monitors changed).

    Returns {"applied": bool, "reason": str, "fell_back":
    bool, "monitor": str, "per_monitor": {...}}. "applied"
    is True only when every target applied. Never raises."""
    if not is_windows():
        return {"applied": False, "reason": "windows-only",
                "fell_back": False, "monitor": str(selector),
                "per_monitor": {}}
    from . import wallpaper_monitors as _monitors

    resolved, refusal, is_all = _monitors.resolve_wallpaper_monitor(
        selector)
    if resolved is None and not is_all:
        return {"applied": False,
                "reason": f"unknown-monitor: {refusal}",
                "fell_back": False, "monitor": str(selector),
                "per_monitor": {}}
    if is_all:
        table, note = _monitors.enumerate_monitors()
        if not table:
            return {"applied": False,
                    "reason": f"unknown-monitor: {note}",
                    "fell_back": False, "monitor": str(selector),
                    "per_monitor": {}}
        targets = [str(m.get("monitor_id") or m.get("device") or "")
                   for m in table]
    else:
        targets = [str(resolved.get("monitor_id")
                       or resolved.get("device") or "")]
    results = {}
    fell_back = False
    for device in targets:
        result = set_wallpaper_for_monitor(image, device)
        results[device] = result
        if result.get("fell_back"):
            fell_back = True
        if not result.get("applied"):
            return {"applied": False,
                    "reason": result.get("reason", ""),
                    "fell_back": fell_back,
                    "monitor": device, "per_monitor": results}
    return {"applied": True,
            "reason": ("per-monitor-unavailable-fell-back-to-all"
                       if fell_back else "ok"),
            "fell_back": fell_back, "monitor": str(selector),
            "per_monitor": results}


_IMAGE_MAGIC = (
    (".bmp", b"BM"),
    (".jpg", b"\xff\xd8\xff"),
    (".jpeg", b"\xff\xd8\xff"),
    (".png", b"\x89PNG\r\n\x1a\n"),
)
_IMAGE_SUFFIXES = frozenset(suffix for suffix, _ in _IMAGE_MAGIC)


def is_image_file(path: str | Path) -> bool:
    """True when ``path`` is a file with a wallpaper-usable image
    suffix AND matching magic bytes. A renamed text file fails the
    magic check; an unknown suffix fails outright. Stdlib only;
    reads at most 8 bytes."""
    candidate = Path(path)
    suffix = candidate.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        return False
    try:
        with candidate.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    for known_suffix, magic in _IMAGE_MAGIC:
        if known_suffix == suffix:
            return head.startswith(magic)
    return False
