# Split from core/desktop/desktop.py (Phase B batch B7) — move only,
# no behaviour change. This module owns the section named below;
# the original file is now a facade re-exporting every name.

# Pilot desktops: licence/pilot tables, pyvda virtual desktops,
# dispatch, easy-uiauto pilot. Override globals live here with
# their readers (_pyvda_override + _pyvda; _easy_uiauto_override
# + easy_uiauto_status/easy_uiauto_find) so the seams resolve
# in one module's globals.

import sys

# ---------------------------------------------------------------------------
# Licence + pilot status table (machine-checkable handoff surface)
# ---------------------------------------------------------------------------

LICENCES = {
    "pyvda": {
        "version_checked": "0.6.0",
        "licence": "MIT",
        "primary_source": "pyvda sdist LICENSE.txt + PKG-INFO classifier",
        "status": "verified — ADOPT (lazy, optional)",
    },
    "pydirectinput": {
        "version_checked": "1.0.4",
        "licence": "MIT (classifier only; sdist ships no LICENSE.txt)",
        "primary_source": "PyDirectInput sdist setup.py classifier",
        "status": "verified-by-classifier — optional engine only, never required",
    },
    "easy-uiauto": {
        "version_checked": "0.9.1",
        "licence": "MIT",
        "primary_source": "easy-uiauto sdist LICENSE + pyproject.toml classifier",
        "status": "verified — PILOT (lazy, optional, fallback only)",
    },
}

# HA-11 / HE-9 are held, not built — see the module docstring.
PILOT_STATUS = {
    "HA-4": "shipped (effect wait inside uia expect path; COM event "
             "subscription evaluated and not shipped — see uia.py)",
    "HA-5": "shipped as staged verbs (pyvda, MIT) — funnel-live once "
             "the window_control enum gains the desktop actions",
    "HA-6": "shipped (clipboard rung inside uia_type_text; OLE "
             "DoDragDrop itself stays STUDY — the drag pilot below "
             "covers the gesture + format discipline, not COM OLE)",
    "HA-2": "shipped (scan-code rung inside uia_type_text; own ctypes "
             "engine primary, PyDirectInput optional)",
    "HA-12/EY-A1": "shipped as ONE pilot (easy-uiauto, MIT, lazy, "
                    "fallback-only inside uia_find_control)",
    "HA-11": "HELD — wave-10 rows naming its package are not in this "
             "tree; no package guessed, no import attempted",
    "PC-C": "shipped as staged cursor pilots (built on cursor.py's "
             "seeded-Bezier planner, never a second planner)",
    "HE-9": "HELD — Everything SDK, app terms owed; no code built",
}


def pilot_status() -> dict:
    """{item: status} for every Wave 4 desktop-track item, plus
    the licence table. Never raises."""
    return {"licences": {k: dict(v) for k, v in LICENCES.items()},
            "items": dict(PILOT_STATUS)}


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# HA-5 — virtual desktops via pyvda (lazy, optional, ADOPT)
# ---------------------------------------------------------------------------
# pyvda's own API (read from the 0.6.0 sdist):
#   get_virtual_desktops() -> [VirtualDesktop] (.number, .name, .go())
#   VirtualDesktop(number=1-based) / VirtualDesktop(current=True)
#   AppView(hwnd=hwnd) — .move(desktop), .pin(), .unpin(),
#     .is_pinned()
# A test seam replaces the module wholesale; production leaves it
# None and the import below runs lazily.

_pyvda_override = None  # test seam: a fake module-like object


def _pyvda():
    """The pyvda module, or None (not Windows / not installed /
    import failed). Never raises."""
    if _pyvda_override is not None:
        return _pyvda_override
    if not _is_windows():
        return None
    try:
        import pyvda  # noqa: PLC0415 — lazy, optional, never required
    except Exception:  # noqa: BLE001
        return None
    return pyvda


def _pyvda_unavailable() -> str:
    if not _is_windows():
        return ("Virtual desktops aren't available on this PC yet — "
                "they only work on Windows, and this system isn't "
                "Windows.")
    return ("Virtual desktops aren't available in this build of "
            "VYUK — the pyvda part (MIT, optional) isn't installed. "
            "Install pyvda to enable them; nothing else changes.")


def _parse_desktop_target(spec, desktops):
    """(desktop, error) for a switch/move target: an int (1-based
    desktop number) or a name substring. `desktops` is the pyvda
    list. Neither form is guessed: an out-of-range number and an
    unmatched name are both refusals naming the desktops that do
    exist."""
    raw = str(spec or "").strip()
    if not raw:
        return None, "No desktop was named — give a desktop number (1, 2, …) or part of its name."
    try:
        number = int(raw)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        for desk in desktops:
            try:
                if int(desk.number) == number:
                    return desk, None
            except Exception:  # noqa: BLE001 — a desk without a
                continue  # readable number can't be this target
        names = ", ".join(_desk_label(d) for d in desktops) or "none"
        return None, (f"There is no virtual desktop {number} "
                      f"(desktops: {names}).")
    needle = raw.lower()
    matches = [d for d in desktops
               if needle in str(_desk_name(d) or "").lower()]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        names = ", ".join(_desk_label(d) for d in matches)
        return None, (f"More than one desktop matches '{raw}': {names} "
                      "— give a more specific name or a number.")
    names = ", ".join(_desk_label(d) for d in desktops) or "none"
    return None, (f"No virtual desktop matches '{raw}' "
                  f"(desktops: {names}).")


def _desk_name(desk) -> str:
    try:
        name = desk.name
        if callable(name):
            name = name()
        return str(name or "")
    except Exception:  # noqa: BLE001
        return ""


def _desk_label(desk) -> str:
    try:
        number = int(desk.number)
    except Exception:  # noqa: BLE001
        number = None
    name = _desk_name(desk)
    if number is not None and name:
        return f"{number} ('{name}')"
    if number is not None:
        return str(number)
    return name or "?"


def virtual_desktop_list() -> str:
    """List the virtual desktops (read-only — no failsafe gate,
    like window_control's list)."""
    mod = _pyvda()
    if mod is None:
        return _pyvda_unavailable()
    try:
        desktops = mod.get_virtual_desktops()
        current = None
        try:
            current = mod.VirtualDesktop(current=True)
        except Exception:  # noqa: BLE001 — current is best-effort
            current = None
        cur_number = None
        if current is not None:
            try:
                cur_number = int(current.number)
            except Exception:  # noqa: BLE001
                cur_number = None
        lines = []
        for desk in desktops:
            try:
                desk_number = int(desk.number)
            except Exception:  # noqa: BLE001
                desk_number = None
            mark = " (current)" if (cur_number is not None
                                    and desk_number == cur_number) else ""
            lines.append(f"• Desktop {_desk_label(desk)}{mark}")
        if not lines:
            return "No virtual desktops were found."
        return f"Virtual desktops ({len(lines)}):\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"Could not list virtual desktops: {e}"


def virtual_desktop_switch(spec) -> str:
    """Switch to one virtual desktop (by number or name)."""
    mod = _pyvda()
    if mod is None:
        return _pyvda_unavailable()
    try:
        desktops = mod.get_virtual_desktops()
        desk, err = _parse_desktop_target(spec, desktops)
        if err:
            return err
        desk.go()
        return f"Switched to virtual desktop {_desk_label(desk)}."
    except Exception as e:  # noqa: BLE001
        return f"Could not switch virtual desktops: {e}"


def virtual_desktop_move(hwnd, spec) -> str:
    """Move one window (by hwnd) to a virtual desktop."""
    mod = _pyvda()
    if mod is None:
        return _pyvda_unavailable()
    try:
        desktops = mod.get_virtual_desktops()
        desk, err = _parse_desktop_target(spec, desktops)
        if err:
            return err
        mod.AppView(hwnd=int(hwnd)).move(desk)
        return (f"Moved the window to virtual desktop "
                f"{_desk_label(desk)}.")
    except Exception as e:  # noqa: BLE001
        return f"Could not move the window to another desktop: {e}"


def virtual_desktop_pin(hwnd, pin: bool = True) -> str:
    """Pin (show on all desktops) or unpin one window (by hwnd)."""
    mod = _pyvda()
    if mod is None:
        return _pyvda_unavailable()
    try:
        view = mod.AppView(hwnd=int(hwnd))
        if pin:
            view.pin()
            return "Pinned the window so it shows on all virtual desktops."
        view.unpin()
        return "Unpinned the window — it now shows only on its own desktop."
    except Exception as e:  # noqa: BLE001
        return f"Could not {'pin' if pin else 'unpin'} the window: {e}"


# ---------------------------------------------------------------------------
# HA-5 dispatch — the window_control branch (core/desktop/control.py)
# ---------------------------------------------------------------------------
# Identify-first window matching, mirroring control.window_control's
# own discipline (exact title beats substring; several candidates
# refuse listing them). `windows` is control._enum_windows()'s
# [(hwnd, title)] list, injected so the suite can fake it.
# desktop_move's target rides in the title argument as a suffix:
#   "Notepad @2"  — window "Notepad", desktop 2
#   "Notepad @Work" — window "Notepad", desktop named like "Work"
# The suffix exists ONLY because the shipped window_control schema
# has no desktop parameter (see the module docstring); a later
# declaration-growing wave should replace it with a real one.


def _split_move_target(title_substring: str):
    """(window_title, desktop_spec) splitting a trailing " @X"
    suffix, or (title, None) when there is none."""
    raw = str(title_substring or "")
    if " @" in raw:
        head, tail = raw.rsplit(" @", 1)
        if head.strip() and tail.strip():
            return head.strip(), tail.strip()
    return raw.strip(), None


def _match_window(windows, needle):
    """(hwnd, title, refusal) — identify-first, like
    control.window_control. refusal is None on a unique match."""
    needle = (needle or "").strip().lower()
    if not needle:
        return None, None, "A window title is needed to match a window."
    exact = [(h, t) for h, t in windows if t.lower() == needle]
    partial = [(h, t) for h, t in windows if needle in t.lower()]
    candidates = exact if exact else partial
    if not candidates:
        return None, None, f"No window found with title containing '{needle}'."
    if len(candidates) > 1:
        lines = [f"{i}. {t}" for i, (_h, t) in enumerate(candidates, 1)]
        kind = "with that exact title" if exact else "containing that text"
        return None, None, (
            f"{len(candidates)} windows match '{needle}' ({kind}) — "
            "I won't guess which one to act on.\n" + "\n".join(lines)
            + "\nAsk again with the exact full title of the right window.")
    hwnd, title = candidates[0]
    return hwnd, title, None


def desktop_action(action: str, title_substring: str = "",
                   windows=None) -> str:
    """HA-5 dispatch for control.window_control's desktop actions:
    desktop_list, desktop_switch, desktop_move, desktop_pin,
    desktop_unpin. `windows` is the [(hwnd, title)] enumeration
    (control._enum_windows in production). Never raises."""
    act = (action or "").strip().lower()
    if act == "desktop_list":
        return virtual_desktop_list()
    if act == "desktop_switch":
        # The switch target rides in title_substring (a number or
        # a desktop name) because the schema has no desktop field.
        return virtual_desktop_switch(title_substring)
    if act not in ("desktop_move", "desktop_pin", "desktop_unpin"):
        return (f"Unknown desktop action: '{action}'. Use desktop_list, "
                "desktop_switch, desktop_move, desktop_pin or "
                "desktop_unpin.")
    if windows is None:
        try:
            from .control import _enum_windows  # Phase 3 D4a: control hub now in core/desktop/; S7a interim closed

            windows = _enum_windows()
        except Exception:  # noqa: BLE001
            windows = []
    if act == "desktop_move":
        title, spec = _split_move_target(title_substring)
        if spec is None:
            return ("desktop_move needs a desktop: give the window as "
                    "'Window title @2' (desktop number) or "
                    "'Window title @Name' (desktop name).")
    else:
        title, spec = str(title_substring or "").strip(), None
    hwnd, window_title, refusal = _match_window(windows, title)
    if refusal:
        return refusal
    if act == "desktop_move":
        result = virtual_desktop_move(hwnd, spec)
        if result.startswith("Moved the window"):
            return f"Moved '{window_title}' to virtual desktop {spec}."
        return result
    if act == "desktop_pin":
        result = virtual_desktop_pin(hwnd, True)
        if result.startswith("Pinned the window"):
            return f"Pinned '{window_title}' so it shows on all virtual desktops."
        return result
    result = virtual_desktop_pin(hwnd, False)
    if result.startswith("Unpinned the window"):
        return f"Unpinned '{window_title}' — it now shows only on its own desktop."
    return result


# ---------------------------------------------------------------------------
# HA-12 / EY-A1 — easy-uiauto pilot (lazy, optional, fallback only)
# ---------------------------------------------------------------------------
# easy-uiauto 0.9.1's own surface (read from its sdist):
#   easy_uiauto.utils.package_location(WindowName, Name, ClassName,
#       ControlType, foundIndex, AutomationId, Xpath, Img, PARAMETERS)
#   easy_uiauto.utils.find_control(LOCATION) -> control | None-ish
#   easy_uiauto.ctrl.locate_and_prepare(LOCATION, PARAMETERS,
#       timeout, interval) -> (control, x, y), (None, None, None)
#       on timeout. The pilot uses locate_and_prepare: it is the
#       package's own wait-and-locate, and its (control, x, y)
#       triple is exactly what a fallback report needs. It is
#       called ONLY after pywinauto found nothing (uia.py), so it
#       can never shadow a pywinauto match, and its result is
#       labelled "easy-uiauto pilot" wherever it appears.

_easy_uiauto_override = None  # test seam: a fake locate function


def easy_uiauto_status() -> str:
    """Availability of the easy-uiauto pilot (MIT, optional)."""
    if _easy_uiauto_override is not None:
        return "easy-uiauto pilot: test seam active."
    if not _is_windows():
        return ("easy-uiauto pilot isn't available on this PC yet — "
                "it only works on Windows, and this system isn't "
                "Windows.")
    try:
        import easy_uiauto  # noqa: PLC0415, F401 — presence probe
    except ImportError:
        return ("easy-uiauto pilot isn't installed in this build of "
                "VYUK — the pilot part didn't make it into the package.")
    except Exception as e:  # noqa: BLE001
        return f"easy-uiauto pilot failed to load ({e})."
    return "easy-uiauto pilot is available."


def easy_uiauto_find(name: str, window_title: str = "",
                     control_type: str = "") -> str | None:
    """Fallback find via the easy-uiauto pilot, or None.

    Returns a pilot-labelled report string when the pilot locates
    a control, else None (pilot absent, or pilot found nothing) —
    so callers keep their own not-found wording untouched. Never
    raises, never echoes anything but the control's name/type/
    coordinates (the caller already knows the name it searched)."""
    if _easy_uiauto_override is not None:
        try:
            return _easy_uiauto_override(name, window_title, control_type)
        except Exception:  # noqa: BLE001
            return None
    if not _is_windows():
        return None
    try:
        from easy_uiauto.ctrl import locate_and_prepare  # noqa: PLC0415
        from easy_uiauto.utils import package_location  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — pilot absent
        return None
    try:
        location = package_location(
            window_title or "", name, None,
            control_type or "", 0, "", [], "", {})
        control, x, y = locate_and_prepare(location, {}, timeout=3.0,
                                           interval=0.2)
    except Exception:  # noqa: BLE001 — pilot failure is not a find
        return None
    if control is None:
        return None
    label = ""
    try:
        ctrl_name = getattr(control, "Name", "") or ""
        ctrl_type = getattr(control, "ControlTypeName", "") or ""
        if ctrl_name:
            label = f"'{ctrl_name}'"
            if ctrl_type:
                label += f" ({ctrl_type})"
    except Exception:  # noqa: BLE001
        label = ""
    where = f" at ({x}, {y})" if x is not None and y is not None else ""
    found = label or "a control"
    return (f"pywinauto found no match, but the easy-uiauto pilot "
            f"(HA-12/EY-A1, MIT, fallback only) located {found}"
            f"{where} — it was not clicked or typed into, this is "
            f"a find report only.")
