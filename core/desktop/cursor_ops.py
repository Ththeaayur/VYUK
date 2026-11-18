"""Cursor operations for core/desktop/cursor.py (B35 part 2 of 2):
position, move, nudge, click, scroll, drag, beacon, park.
"""

import os
import threading

# B35 INTENTIONAL: names this family reads by bare name, imported
# from their defining modules (same objects as the facade
# re-exports). Dependency order: geometry <- input <- glide.
from .cursor_geometry import (
    _clamp,
    _fence_point,
    _is_windows,
    _routed_backend,
    _screen_size,
)
from .cursor_input import (
    _FAILSAFE_MSG,
    _dispatch_click,
    _failsafe_tripped,
    _mouse_controller,
    _note_input_pressed,
    _note_input_released,
    _read_position,
    _set_position,
)
from .cursor_glide import (
    _follow_path,
    _glide,
    _plan_path,
    _resolve_target,
)


# ---------------------------------------------------------------------------
# CURSOR — position and movement
# ---------------------------------------------------------------------------

def get_cursor_position() -> str:
    """Report the cursor position in true pixels and as a screen fraction."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.get_cursor_position()
    mouse, _button, err = _mouse_controller()
    if mouse is None:
        return err
    pos = _read_position(mouse)
    if pos is None:
        return "Could not read the cursor position on this system."
    size = _screen_size()
    if size is None:
        return f"Cursor is at ({pos[0]}, {pos[1]}). Screen size unknown."
    w, h = size
    fx = pos[0] / (w - 1) * 100 if w > 1 else 0.0
    fy = pos[1] / (h - 1) * 100 if h > 1 else 0.0
    return (f"Cursor is at ({pos[0]}, {pos[1]}) on a {w}x{h} screen "
            f"({fx:.0f}%, {fy:.0f}%).")


def move_cursor(x=None, y=None, fx=None, fy=None, relative=None,
                duration: float = 0.6) -> str:
    """Glide the cursor smoothly to a target.

    Target is absolute pixels (x, y), screen fractions (fx, fy in
    0..1), or a relative offset dict {"dx": …, "dy": …}. The move
    aborts instantly if the user grabs the mouse mid-glide.
    """
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.move_cursor(x=x, y=y, fx=fx, fy=fy,
                                    relative=relative, duration=duration)
    # Validate the combination before touching any input device.
    if relative is None and (fx is not None or fy is not None) \
            and (x is not None or y is not None):
        return ("Ambiguous move: give pixels (x/y), fractions (fx/fy), or a "
                "relative offset — not a mix.")
    if relative is None:
        if fx is not None or fy is not None:
            if fx is None or fy is None:
                return "A fraction move needs both fx and fy (each 0..1)."
        elif x is not None or y is not None:
            if x is None or y is None:
                return "A pixel move needs both x and y."
        else:
            return ("No move target given: pass x/y pixels, fx/fy fractions, "
                    "or a relative {dx, dy}.")
    try:
        duration = _clamp(float(duration), 0.0, 5.0)
    except (TypeError, ValueError):
        return "duration must be a number of seconds (0..5)."

    mouse, _button, err = _mouse_controller()
    if mouse is None:
        return err
    current = _read_position(mouse)
    tx, ty, problem = _resolve_target(_screen_size(), current, x=x, y=y,
                                      fx=fx, fy=fy, relative=relative)
    if problem:
        return problem
    abort = _glide(mouse, tx, ty, duration)
    if abort:
        return abort
    final = _read_position(mouse)
    if final is None:
        # R1b: the glide ran but the position could not be read
        # back — claiming the cursor IS at the target would be a
        # guess, so the outcome is honestly unverified, not a
        # success with the target substituted in.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        return _outcome.unverified(
            "Moved the cursor toward (%d, %d), but the position "
            "could not be read back to confirm it landed there."
            % (tx, ty),
            "cursor position unreadable after the move")
    return f"Moved the cursor to ({final[0]}, {final[1]})."


_NUDGE_VECTORS = {
    "up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
    "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1),
    "north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0),
    "northeast": (1, -1), "northwest": (-1, -1),
    "southeast": (1, 1), "southwest": (-1, 1),
}


def nudge_cursor(direction: str, pixels: int = 40, duration: float = 0.25) -> str:
    """Glide the cursor a short distance in a compass direction."""
    key = (direction or "").strip().lower()
    if key not in _NUDGE_VECTORS:
        return (f"Unknown nudge direction: '{direction}'. Use up, down, left, "
                "right, ne, nw, se or sw.")
    try:
        pixels = int(pixels)
    except (TypeError, ValueError):
        return "pixels must be a whole number."
    pixels = _clamp(pixels, 1, 2000)
    vx, vy = _NUDGE_VECTORS[key]
    result = move_cursor(relative={"dx": vx * pixels, "dy": vy * pixels},
                         duration=duration)
    # R1b: move_cursor may now return an Outcome (unverified when
    # the position could not be read back). Only claim the nudge
    # when the move itself was confirmed; an unverified move
    # passes through as-is, caveat and all.
    msg = str(result)
    if msg.startswith("Moved the cursor"):
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        if not _outcome.is_outcome(result) or result.verified:
            return f"Nudged the cursor {key} by {pixels}px — {msg}"
    return result


# ---------------------------------------------------------------------------
# CURSOR — click and scroll
# ---------------------------------------------------------------------------

_BUTTON_NAMES = ("left", "right", "middle")


def _identify_click_point(x, y):
    """(receipt_suffix, foreground_title) for a click about to fire
    at (x, y) — the v5.8 "cursor gets a receipt" enrichment, shared
    with control.py's mouse_control.

    Asks core/desktop/uia.py what lives at the point and phrases it as a
    sentence fragment to append AFTER the click result's own
    sentence: ' That point is the "Save" (Button) in Notepad.' /
    ' That point is inside Notepad (no named control there).' /
    ' (Couldn't identify what's at that point.)' — the last only on
    Windows machines where pywinauto IS installed but UIA still
    couldn't answer; a machine without pywinauto stays silent rather
    than apologising on every click. Also captures the foreground
    window's title BEFORE the click fires, for _focus_change_note.

    Never raises and never returns a suffix on failure paths it
    can't phrase honestly: identification must never block, move or
    break a click."""
    try:
        from . import uia  # Phase 3 D4d: cursor hub now in core/desktop/; D4b interim closed
    except Exception:  # noqa: BLE001 — a broken uia module changes nothing
        return "", None
    fg_before = None
    try:
        fg_before = uia.foreground_title()
    except Exception:  # noqa: BLE001
        fg_before = None
    try:
        desc = uia.describe_point(int(x), int(y))
    except Exception:  # noqa: BLE001
        desc = None
    if desc:
        window_only = "(no named control) in "
        if desc.startswith(window_only):
            return (f" That point is inside {desc[len(window_only):]} "
                    f"(no named control there).", fg_before)
        return f" That point is the {desc}.", fg_before
    if _is_windows():
        try:
            import pywinauto  # noqa: F401

            return " (Couldn't identify what's at that point.)", fg_before
        except Exception:  # noqa: BLE001 — pywinauto absent: stay silent
            pass
    return "", fg_before


def _focus_change_note(fg_before) -> str:
    """' Focus moved to <title>.' when the foreground window changed
    since `fg_before` was captured (see _identify_click_point), else
    ''. Both titles must be known — an unreadable title is not a
    change. Never raises."""
    if not fg_before:
        return ""
    try:
        from . import uia  # Phase 3 D4d: cursor hub now in core/desktop/; D4b interim closed

        fg_after = uia.foreground_title()
    except Exception:  # noqa: BLE001
        return ""
    if fg_after and fg_after != fg_before:
        return f" Focus moved to {fg_after}."
    return ""


def click_cursor(button: str = "left", count: int = 1, x=None, y=None,
                 fx=None, fy=None) -> str:
    """Click at the current position, or glide to a target first, then click."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.click_cursor(button=button, count=count, x=x, y=y,
                                     fx=fx, fy=fy)
    button_key = (button or "left").strip().lower()
    if button_key not in _BUTTON_NAMES:
        return f"Unknown mouse button: '{button}'. Use left, right or middle."
    try:
        count = int(count)
    except (TypeError, ValueError):
        return "count must be a whole number (1..3)."
    count = _clamp(count, 1, 3)

    mouse, Button, err = _mouse_controller()
    if mouse is None:
        return err
    if x is not None or y is not None or fx is not None or fy is not None:
        current = _read_position(mouse)
        tx, ty, problem = _resolve_target(_screen_size(), current, x=x, y=y,
                                          fx=fx, fy=fy)
        if problem:
            return problem
        abort = _glide(mouse, tx, ty, 0.35)
        if abort:
            return abort  # never click after a takeover abort
    # v5.8: identify what the click is about to land on, at the FINAL
    # (fenced) landing point, so the result can name it — the same
    # receipt discipline the browser point-click learned in v5.7.
    # Identification never blocks or moves the click, and it never
    # runs while the failsafe is tripped (the dispatch below refuses
    # on its own, exactly as before).
    note = ""
    fg_before = None
    if not _failsafe_tripped():
        landing = _read_position(mouse)
        if landing is None and (x is not None or y is not None
                                 or fx is not None or fy is not None):
            landing = (tx, ty)
        if landing is not None:
            note, fg_before = _identify_click_point(landing[0], landing[1])
    # Command Deck Phase F: input held around the dispatch (released
    # in the finally), plus the overlay chip. The chip label is the
    # control the click-point identification named, when it named
    # one (" That point is the \"Save\" (Button) in Notepad."); the
    # button name is the honest fallback. The uia paths emit their
    # own NAMED chip after this returns, so it wins over this one.
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_input_acquired(source="cursor_click")
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    try:
        _dispatch_click(mouse, Button, button_key, count)
    except Exception as e:  # noqa: BLE001
        return f"Could not click (no display / input access on this system?): {e}"
    finally:
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

            _prod.emit_input_released(source="cursor_click")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
    pos = _read_position(mouse)
    where = f" — landed at ({pos[0]}, {pos[1]})" if pos else ""
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        chip_label = button_key
        if note.startswith(' That point is the "'):
            rest = note[len(' That point is the "'):]
            if '"' in rest:
                chip_label = rest.split('"', 1)[0] or button_key
        _prod.emit_agent_action(
            "click", label=chip_label,
            x=int(pos[0]) if pos else 0, y=int(pos[1]) if pos else 0)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    times = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}[count]
    return (f"{times} ({button_key} button){where}."
            + note + _focus_change_note(fg_before))


def scroll_cursor(amount, x=None, y=None, horizontal: bool = False) -> str:
    """Scroll at the current position, or at a target point if given."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.scroll_cursor(amount, x=x, y=y, horizontal=horizontal)
    if amount is None:
        return "Scroll needs an amount (-20..20; positive scrolls up)."
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return "Scroll amount must be a whole number (-20..20)."
    amount = _clamp(amount, -20, 20)
    if amount == 0:
        return "Scroll amount was 0 — nothing to scroll."

    mouse, _button, err = _mouse_controller()
    if mouse is None:
        return err
    # Failsafe (v4.4, audit C1): the target-less scroll below is a
    # raw mouse.scroll dispatch that bypassed the guards in
    # _set_position/_dispatch_click. EVERY dispatch in this module
    # now sits behind the failsafe check.
    if _failsafe_tripped():
        return _FAILSAFE_MSG
    if x is not None or y is not None:
        current = _read_position(mouse)
        tx, ty, problem = _resolve_target(_screen_size(), current, x=x, y=y)
        if problem:
            return problem
        try:
            _set_position(mouse, tx, ty)  # instant set is fine for scrolling
        except Exception as e:  # noqa: BLE001
            return f"Could not move the cursor to the scroll point: {e}"
    # Command Deck Phase F: input held around the scroll dispatch,
    # plus the overlay chip (direction only, never content).
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_input_acquired(source="cursor_scroll")
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    try:
        if horizontal:
            mouse.scroll(amount, 0)
        else:
            mouse.scroll(0, amount)
    except Exception as e:  # noqa: BLE001
        return f"Could not scroll (no display / input access on this system?): {e}"
    finally:
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

            _prod.emit_input_released(source="cursor_scroll")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
    axis = "horizontally " if horizontal else ""
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        if horizontal:
            direction = "right" if amount > 0 else "left"
        else:
            direction = "up" if amount > 0 else "down"
        _prod.emit_agent_action("scroll", label=direction)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    return f"Scrolled {axis}by {amount} step(s)."


# ---------------------------------------------------------------------------
# CURSOR — drag with the button held
# ---------------------------------------------------------------------------

def drag_cursor(x1, y1, x2, y2, duration: float = 0.8) -> str:
    """Drag from (x1, y1) to (x2, y2): smooth path, left button held,
    takeover abort active, button always released (finally)."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.drag_cursor(x1, y1, x2, y2, duration=duration)
    try:
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    except (TypeError, ValueError):
        return "Drag needs numeric start and end coordinates (x1, y1, x2, y2)."
    try:
        duration = _clamp(float(duration), 0.0, 5.0)
    except (TypeError, ValueError):
        return "duration must be a number of seconds (0..5)."

    mouse, Button, err = _mouse_controller()
    if mouse is None:
        return err
    x1, y1 = _fence_point(x1, y1)
    x2, y2 = _fence_point(x2, y2)
    try:
        _set_position(mouse, x1, y1)
        mouse.press(Button.left)
        # v5.48 (programme item 26): the held button is registered
        # for the whole drag — a failsafe trip mid-drag releases
        # exactly this button.
        _note_input_pressed("button", Button.left)
    except Exception as e:  # noqa: BLE001
        return f"Could not start the drag (no display / input access on this system?): {e}"
    # Command Deck Phase F: input held for the whole drag, plus the
    # overlay chip (endpoints only).
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_input_acquired(source="cursor_drag")
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    abort = None
    try:
        abort = _follow_path(mouse, _plan_path(x1, y1, x2, y2, duration))
    finally:
        try:
            mouse.release(Button.left)
        except Exception:  # noqa: BLE001
            pass
        else:
            _note_input_released("button", Button.left)
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

            _prod.emit_input_released(source="cursor_drag")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass
    if abort:
        return f"{abort} (Drag released where it stopped.)"
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4d: cursor hub moved to core/desktop/; state_bus_producers stays flat in core/

        _prod.emit_agent_action(
            "drag", label="(%d,%d) to (%d,%d)" % (x1, y1, x2, y2),
            x=x2, y=y2)
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."


# ---------------------------------------------------------------------------
# CURSOR — beacon (a visible "I am pointing here" pulse ring)
# ---------------------------------------------------------------------------

_BEACON_COLOUR = "#4DD8D0"


def show_beacon(x=None, y=None, ms: int = 900) -> str:
    """Flash a pulsing ring at a point so the user can SEE where VYUK
    is pointing. tkinter overlay in a daemon thread: borderless,
    topmost, click-through on Windows, auto-destroys after `ms`.
    Never steals focus and never blocks the caller."""
    # Resolve the point first: explicit pixels, else the live cursor.
    if x is not None and y is not None:
        try:
            cx, cy = int(x), int(y)
        except (TypeError, ValueError):
            return "Beacon coordinates must be whole numbers (pixels)."
    else:
        mouse, _button, _err = _mouse_controller()
        pos = _read_position(mouse) if mouse is not None else None
        if pos is None:
            return ("Beacon unavailable: no point was given and the cursor "
                    "position could not be read on this system.")
        cx, cy = pos
    cx, cy = _fence_point(cx, cy)
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return "Beacon duration (ms) must be a whole number."
    ms = _clamp(ms, 200, 5000)

    # Pre-flight: tkinter importable AND a display plausibly present —
    # otherwise the overlay thread would fail silently and we'd lie.
    try:
        import tkinter  # noqa: F401 — presence probe only
    except Exception as e:  # noqa: BLE001
        return f"Beacon unavailable: tkinter could not be loaded ({e})."
    if not _is_windows() and not (os.environ.get("DISPLAY")
                                  or os.environ.get("WAYLAND_DISPLAY")):
        return ("Beacon unavailable: no display is available on this system "
                "(headless session).")

    def _run_beacon():
        import tkinter as tk

        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001 — display vanished between checks
            return
        try:
            size = 64
            root.overrideredirect(True)
            root.geometry(f"{size}x{size}+{cx - size // 2}+{cy - size // 2}")
            root.configure(bg="black")
            try:
                root.wm_attributes("-transparentcolor", "black")
            except Exception:  # noqa: BLE001 — non-Windows: harmless
                pass
            try:
                root.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass
            canvas = tk.Canvas(root, width=size, height=size, bg="black",
                               highlightthickness=0)
            canvas.pack()
            ring = canvas.create_oval(8, 8, size - 8, size - 8,
                                      outline=_BEACON_COLOUR, width=3)
            canvas.create_oval(size // 2 - 3, size // 2 - 3,
                               size // 2 + 3, size // 2 + 3,
                               fill=_BEACON_COLOUR, outline="")

            # Pulse: breathe the ring radius in and out until destroyed.
            pulse = {"r": 24, "step": -3}

            def _pulse():
                try:
                    if not root.winfo_exists():
                        return
                except Exception:  # noqa: BLE001
                    return
                pulse["r"] += pulse["step"]
                if pulse["r"] <= 14 or pulse["r"] >= 26:
                    pulse["step"] = -pulse["step"]
                r = pulse["r"]
                try:
                    canvas.coords(ring, size // 2 - r, size // 2 - r,
                                  size // 2 + r, size // 2 + r)
                except Exception:  # noqa: BLE001
                    return
                root.after(60, _pulse)

            if _is_windows():
                # Layered + transparent + topmost => truly click-through:
                # clicks pass to the app underneath, the ring never eats one.
                try:
                    import ctypes

                    root.update_idletasks()
                    hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) \
                        or root.winfo_id()
                    GWL_EXSTYLE = -20
                    WS_EX_LAYERED = 0x00080000
                    WS_EX_TRANSPARENT = 0x00000020
                    WS_EX_TOPMOST = 0x00000008
                    user32 = ctypes.windll.user32
                    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    user32.SetWindowLongW(
                        hwnd, GWL_EXSTYLE,
                        style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST)
                except Exception:  # noqa: BLE001 — ring still shows, just not click-through
                    pass

            root.after(60, _pulse)
            root.after(ms, root.destroy)
            root.mainloop()
        except Exception:  # noqa: BLE001 — a beacon must never crash VYUK
            pass

    try:
        thread = threading.Thread(target=_run_beacon, daemon=True,
                                  name="nova-cursor-beacon")
        thread.start()
    except Exception as e:  # noqa: BLE001
        return f"Beacon unavailable: could not start the overlay thread ({e})."
    return f"Beacon shown at ({cx}, {cy}) for {ms} ms."


# ---------------------------------------------------------------------------
# CURSOR — park
# ---------------------------------------------------------------------------

_PARK_TARGETS = (
    "top_left", "top_right", "bottom_left", "bottom_right",
    "top_center", "bottom_center", "left_center", "right_center", "center",
)


def park_cursor(corner: str = "bottom_right") -> str:
    """Glide the cursor to a screen corner / edge centre and leave it there."""
    key = (corner or "bottom_right").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"topright": "top_right", "topleft": "top_left",
               "bottomright": "bottom_right", "bottomleft": "bottom_left",
               "middle": "center", "centre": "center"}
    key = aliases.get(key, key)
    if key not in _PARK_TARGETS:
        return (f"Unknown park target: '{corner}'. Use one of: "
                f"{', '.join(_PARK_TARGETS)}.")
    size = _screen_size()
    if size is None:
        return "Could not determine the screen size, so there is nowhere to park."
    w, h = size
    targets = {
        "top_left": (0, 0), "top_right": (w - 1, 0),
        "bottom_left": (0, h - 1), "bottom_right": (w - 1, h - 1),
        "top_center": (w // 2, 0), "bottom_center": (w // 2, h - 1),
        "left_center": (0, h // 2), "right_center": (w - 1, h // 2),
        "center": (w // 2, h // 2),
    }
    tx, ty = targets[key]
    result = move_cursor(x=tx, y=ty, duration=0.7)
    if result.startswith("Moved the cursor"):
        return f"Parked the cursor at the {key.replace('_', ' ')} ({tx}, {ty})."
    return result
