# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: moved verbatim from
# core/skills_media.py (see that facade). Imports travel with the unit
# (plan SS2 P3); cross-part names imported with INTENTIONAL comments.

# INTENTIONAL (media split): shared names live in skills_media_helpers.
import os
import subprocess
from pathlib import Path
from .skills_media_helpers import (
    _NOT_WINDOWS,
    _existing_file,
    _human_size,
    _is_windows,
    _save_screenshot,
)


def create_shortcut(target_path: str, location: str = "desktop", name: str = "") -> str:
    """Create a Windows .lnk shortcut to a file or folder."""
    if not _is_windows():
        return f"Shortcuts (.lnk files) are a Windows feature — {_NOT_WINDOWS}"
    target = _existing_file(target_path, "file or folder")
    if isinstance(target, str):
        # _existing_file rejects folders; shortcuts may point at folders too.
        candidate = Path(str(target_path or "")).expanduser()
        if not candidate.is_dir():
            return target
        target = candidate
    where = (location or "desktop").strip().lower()
    if where == "desktop":
        folder = Path.home() / "Desktop"
        where_label = "the Desktop"
    elif where in ("start_menu", "startmenu", "start menu"):
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        folder = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        where_label = "the Start Menu"
    else:
        folder = Path(location).expanduser()
        where_label = str(folder)
        if not folder.is_dir():
            return (f"Shortcut location '{location}' isn't 'desktop', 'start_menu', "
                    "or an existing folder.")
    link_name = (name or "").strip() or target.name
    if link_name.lower().endswith(".lnk"):
        link_name = link_name[:-4]
    link_path = folder / (link_name + ".lnk")
    if link_path.exists():
        return f"A shortcut named '{link_path.name}' already exists in {where_label} — nothing was overwritten."

    def _ps_escape(value: str) -> str:
        return value.replace("'", "''")

    command = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(link_path))}'); "
        f"$s.TargetPath = '{_ps_escape(str(target))}'; "
        f"$s.WorkingDirectory = '{_ps_escape(str(target.parent))}'; "
        "$s.Save()"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=25,
        )
    except Exception as e:  # noqa: BLE001
        return f"Could not create the shortcut: {e}"
    if completed.returncode != 0 or not link_path.exists():
        detail = (completed.stderr or "").strip()[:200]
        return ("Windows could not create the shortcut"
                + (f" — {detail}" if detail else "")
                + ".")
    # R1b: the .lnk is on disk (checked above) — re-read it through
    # the seam so the success is verified, not assumed.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    ok, evidence = _outcome.file_written(link_path)
    if not ok:
        return (f"Shortcut '{link_path.name}' could not be confirmed "
                f"in {where_label}: {evidence}.")
    return _outcome.verified(
        f"Shortcut '{link_path.name}' created in {where_label}, "
        f"pointing at {target}.",
        evidence)


def screenshot_region(x: int, y: int, width: int, height: int) -> str:
    """Capture a rectangular region of the screen to data/screenshots/.

    Coordinates are virtual-screen pixels; the region must fit
    inside the virtual desktop (a monitor left of the primary has
    a negative origin, and a region there is real pixels — GAPS-19
    measures the virtual desktop, where the old code measured the
    primary monitor only and grabbed primary-relative pixels).
    The grab goes through the capture owner (DXcam or its ImageGrab
    fallback)."""
    try:
        x, y, width, height = int(x), int(y), int(width), int(height)
    except (TypeError, ValueError):
        return "A region screenshot needs whole numbers for x, y, width and height."
    if width < 1 or height < 1:
        return "The region must be at least 1×1 pixels."
    from .desktop.screen_capture import (
        frame_to_pil, grab_virtual, virtual_screen_bounds)

    bounds = virtual_screen_bounds()
    if bounds is None:
        return ("Could not capture the screen: the screen size couldn't "
                "be determined. Screen capture needs a display — it is "
                "unavailable on a headless system.")
    screen_w = bounds[2] - bounds[0]
    screen_h = bounds[3] - bounds[1]
    if x < bounds[0] or y < bounds[1] \
            or x + width > bounds[2] or y + height > bounds[3]:
        return (f"That region ({x}, {y}, {width}×{height}) doesn't fit on the screen, "
                f"which is {screen_w}×{screen_h}. Coordinates start at the top-left corner.")
    got = grab_virtual(bbox=(x, y, x + width, y + height))
    if not got["ok"]:
        return f"Could not capture that region: {got['reason']}"
    try:
        region = frame_to_pil(got["frame"])
        dest = _save_screenshot(region, "region")
    except Exception as e:  # noqa: BLE001
        return f"Could not capture that region: {e}"
    return (f"Captured the region at ({x}, {y}), {width}×{height}: {dest} "
            f"({_human_size(dest.stat().st_size)}).")


# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: handlers keep their public
# module path (core.skills_media) so the override allow-list in
# core/funnel/tools_registry.py — which matches on handler.__module__ —
# keeps working with NO change to that file. The facade is the module's
# public identity; the impl modules are its private home.
create_shortcut.__module__ = "core.skills_media"
screenshot_region.__module__ = "core.skills_media"
