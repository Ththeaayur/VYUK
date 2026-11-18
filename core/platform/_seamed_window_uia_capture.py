from __future__ import annotations

import io  # INTENTIONAL (B29: capture methods use io by bare name)
from . import _common  # INTENTIONAL (B29: mixin methods resolve _common.* by bare name)
from .base import PlatformBackend  # INTENTIONAL (B29: mixin base class)
from ._seamed_helpers import (  # INTENTIONAL (B29: mixin methods resolve these by bare name)
    _PASSWORD_NAME_RE,
    _TEXT_TYPES,
)


class SeamedBackendWindowUiaCaptureMixin(PlatformBackend):
    """SeamedBackend methods: window/uia/capture (old _seamed.py L413-906)."""

    # ------------------------------------------------------------------
    # Area 2 — windows
    # ------------------------------------------------------------------

    def _fg(self):
        """Foreground window dict or None (seam → OS hook)."""
        handled, fg = self._call("windows", "foreground")
        if handled:
            return fg
        handled, fg = self._os_windows("foreground")
        return fg if handled else None

    def foreground_title(self):
        fg = self._fg()
        if not fg or not fg.get("title"):
            return None
        return str(fg["title"])

    def get_active_window(self) -> str:
        title = self.foreground_title()
        if title is None:
            return "No foreground window found."
        return f"Active window: {title}"

    def window_control(self, action: str, title_substring: str = "") -> str:
        action = (action or "").strip().lower()
        valid = ("list", "focus", "minimize", "maximize", "close")
        if action not in valid:
            return (f"Unknown window action: '{action}'. Use one of: "
                    f"{', '.join(valid)}.")
        handled, windows = self._call("windows", "list_windows")
        if not handled:
            handled, windows = self._os_windows("list_windows")
        if not handled:
            return self._session_block("windows") or self._windows_unavailable
        windows = list(windows or [])
        if action == "list":
            if not windows:
                return "No visible windows with titles were found."
            lines = [f"• {t}" for _h, t in windows[:50]]
            header = f"Open windows ({len(windows)}):"
            if len(windows) > 50:
                header = f"Open windows ({len(windows)}, showing first 50):"
            return header + "\n" + "\n".join(lines)
        needle = (title_substring or "").strip().lower()
        if not needle:
            return f"Window '{action}' needs a title_substring to match a window."
        exact = [(h, t) for h, t in windows if str(t).lower() == needle]
        partial = [(h, t) for h, t in windows if needle in str(t).lower()]
        candidates = exact if exact else partial
        if not candidates:
            return f"No window found with title containing '{title_substring}'."
        if len(candidates) > 1:
            listing = ", ".join(f"'{t}'" for _h, t in candidates)
            return (f"More than one window matches '{title_substring}' "
                    f"({listing}) — give a more specific title. "
                    "Nothing was changed.")
        hwnd, title = candidates[0]
        handled2, result = self._call("windows", action, hwnd)
        if not handled2:
            handled2, result = self._os_windows(action, hwnd)
        if handled2:
            return result
        return self._session_block("windows") or self._windows_unavailable

    def enum_windows(self):
        handled, windows = self._call("windows", "list_windows")
        if not handled:
            handled, windows = self._os_windows("list_windows")
        if not handled:
            # The one method allowed to raise off its home
            # platform (PORTING.md) — its pre-seam original did.
            raise RuntimeError(self._windows_unavailable)
        return [(h, t) for h, t in (windows or []) if t]

    def foreground_window_rect(self):
        fg = self._fg()
        if not fg or not fg.get("rect"):
            return None
        try:
            return tuple(int(v) for v in fg["rect"])
        except (TypeError, ValueError):
            return None

    def foreground_process_name(self):
        fg = self._fg()
        if not fg:
            return None
        return fg.get("process")

    def close_application(self, name: str, confirm: bool = False) -> str:
        if not name or not str(name).strip():
            return "No application name given."
        if not confirm:
            return (f"Blocked by VYUK's trust layer: closing '{name}' "
                    "needs confirm=true. Ask again with confirmation "
                    "and VYUK will terminate it gracefully.")
        handled, count = self._call("windows", "terminate", str(name))
        if not handled:
            handled, count = self._os_windows("terminate", str(name))
        if not handled:
            return self._session_block("windows") or self._windows_unavailable
        if not count:
            return f"No running process matches '{name}'."
        return (f"Closed {int(count)} process(es) matching '{name}'. "
                f"Confirmed closed: all {int(count)} terminated "
                "process(es) are gone.")

    # ------------------------------------------------------------------
    # Area 3 — accessibility (element-dict seams)
    # ------------------------------------------------------------------

    def _a11y_find(self, window_title, name, control_type=""):
        handled, found = self._call("accessibility", "find",
                                    window_title, name, control_type)
        if handled:
            return found
        handled, found = self._os_a11y("find", window_title, name,
                                       control_type)
        return found if handled else None

    def _desc_for(self, window_title):
        if window_title:
            return f"window matching '{window_title}'"
        return "the foreground window"

    def uia_read_tree(self, window_title: str = "", max_depth: int = 3,
                      max_nodes: int = 80) -> str:
        try:
            max_depth = max(0, min(int(max_depth), 8))
        except (TypeError, ValueError):
            max_depth = 3
        try:
            max_nodes = max(1, min(int(max_nodes), 300))
        except (TypeError, ValueError):
            max_nodes = 80
        handled, root = self._call("accessibility", "tree",
                                   str(window_title or ""))
        if not handled:
            handled, root = self._os_a11y("tree", str(window_title or ""))
        if not handled:
            return self._a11y_unavailable
        if root is None:
            return (f"Could not connect to the target window: no window "
                    f"matching '{window_title}' could be read.")
        desc = self._desc_for(window_title)
        lines: list[str] = []
        state = {"shown": 0, "truncated": False}

        def _walk(node, depth):
            if state["shown"] >= max_nodes:
                state["truncated"] = True
                return
            state["shown"] += 1
            label = node.get("name") or "(unnamed)"
            lines.append("  " * depth + "• "
                         + f"{label} [{node.get('control_type', '')}]")
            children = node.get("children") or []
            if depth >= max_depth:
                if children:
                    state["truncated"] = True
                return
            for child in children:
                _walk(child, depth + 1)

        _walk(root, 0)
        if not lines:
            return f"Could not read any controls from {desc}."
        header = (f"Control tree of {desc} — {state['shown']} node(s) "
                  f"shown (depth ≤ {max_depth}, cap {max_nodes}):")
        if state["truncated"]:
            header += ("\n⚠ Partial tree — the window has more controls "
                       "than shown here. Don't conclude a control is "
                       "absent from this window: raise max_nodes / "
                       "max_depth, or read a specific sub-window.")
        text = header + "\n" + "\n".join(lines)
        return text[:3000] + (f"\n... [truncated, {len(text) - 3000} more chars]"
                              if len(text) > 3000 else "")

    def uia_find_control(self, name: str, window_title: str = "",
                         control_type: str = "") -> str:
        if not name or not str(name).strip():
            return "No control name given. Tell me the label of the control to look for."
        found = self._a11y_find(str(window_title or ""), name, control_type)
        if found is None:
            return self._a11y_unavailable
        desc = self._desc_for(window_title)
        if not found:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc}.")
        header = (f"Found {len(found)} control(s) matching '{name}' "
                  f"in {desc}:")
        body = "\n".join(_common.format_facts(el) for el in found)
        text = header + "\n" + body
        return text[:1500] + (f"\n... [truncated, {len(text) - 1500} more chars]"
                              if len(text) > 1500 else "")

    def uia_click_control(self, name: str, window_title: str = "",
                          control_type: str = "") -> str:
        if not name or not str(name).strip():
            return "No control name given. Tell me the label of the control to click."
        found = self._a11y_find(str(window_title or ""), name, control_type)
        if found is None:
            return self._a11y_unavailable
        desc = self._desc_for(window_title)
        if not found:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc}.")
        total = len(found)
        skipped: list[str] = []
        chosen = None
        for el in found:
            rect = el.get("rect")
            if el.get("enabled") is False:
                skipped.append(f"'{el.get('name')}' (disabled)")
            elif el.get("visible") is False:
                skipped.append(f"'{el.get('name')}' (hidden)")
            elif rect and (rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0):
                skipped.append(f"'{el.get('name')}' (off-screen (zero-size rectangle))")
            else:
                chosen = el
                break
        if chosen is None:
            return (f"Found {total} control(s) matching '{name}' in "
                    f"{desc}, but none could be clicked: "
                    + "; ".join(skipped) + ". Nothing was clicked.")
        handled, _r = self._call("accessibility", "click", chosen)
        if not handled:
            handled, _r = self._os_a11y("click", chosen)
        if not handled:
            return self._a11y_unavailable
        note = ""
        if total > 1:
            note = f" ({total} controls matched — clicked the first usable one"
            if skipped:
                note += f"; skipped {len(skipped)} unusable: {'; '.join(skipped)}"
            note += ")"
        return (f"Clicked '{chosen.get('name')}' "
                f"({chosen.get('control_type', '')}) at "
                f"{_common.rect_text(chosen.get('rect'))} in {desc}.{note}")

    def uia_type_text(self, name: str, text: str, window_title: str = "",
                      clear_first: bool = True) -> str:
        if not name or not str(name).strip():
            return "No control name given. Tell me which field to type into."
        if text is None:
            text = ""
        text = str(text)
        found = self._a11y_find(str(window_title or ""), name)
        if found is None:
            return self._a11y_unavailable
        desc = self._desc_for(window_title)
        if not found:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc}.")
        chosen = None
        for el in found:
            if el.get("control_type") in _TEXT_TYPES:
                chosen = el
                break
        if chosen is None:
            first = found[0]
            return (f"'{first.get('name')}' is a "
                    f"{first.get('control_type') or 'non-text'} control, "
                    "not a text field — I won't type into it. Name the "
                    "field you mean, or use uia_find_control to see the "
                    "text fields in the window.")
        verdict = self.is_password_control(chosen)
        if verdict is True:
            return ("That's a password field — passwords are yours to "
                    "type, I won't. Everything else in the window I can "
                    "fill for you.")
        if verdict is None and _PASSWORD_NAME_RE.search(
                str(chosen.get("name") or "")):
            return (f"'{chosen.get('name')}' looks like a password field "
                    "and I can't verify that it isn't — I won't type "
                    "into it. Passwords are yours to type.")
        payload = text
        if not clear_first:
            payload = str(chosen.get("value") or "") + text
        handled, _r = self._call("accessibility", "set_value",
                                 chosen, payload)
        if not handled:
            handled, _r = self._os_a11y("set_value", chosen, payload)
        if not handled:
            return self._a11y_unavailable
        return (f"Typed {len(text)} character(s) into "
                f"'{chosen.get('name')}' "
                f"({chosen.get('control_type', '')}) in {desc}.")

    def describe_point(self, x: int, y: int):
        try:
            handled, el = self._call("accessibility", "element_at",
                                     int(x), int(y))
            if not handled:
                handled, el = self._os_a11y("element_at", int(x), int(y))
        except Exception:  # noqa: BLE001 — never raises
            return None
        if not handled or el is None:
            return None
        label = _common.element_label(el)
        title = self.foreground_title() or ""
        if label:
            return f"{label} in {title}" if title else label
        if title:
            return f"(no named control) in {title}"
        return None

    def focused_element(self):
        try:
            handled, el = self._call("accessibility", "focused")
            if not handled:
                handled, el = self._os_a11y("focused")
        except Exception:  # noqa: BLE001 — never raises
            return None
        return el if handled else None

    def is_password_control(self, el):
        """Subclass hooks decide the platform verdict; the shared
        default reads the element dict's scripted/read verdict —
        None stays UNKNOWN, never coerced to False."""
        if el is None:
            return None
        if isinstance(el, dict):
            verdict = el.get("is_password")
            return verdict if isinstance(verdict, bool) else None
        hook = getattr(self, "_password_verdict", None)
        if hook is not None:
            try:
                return hook(el)
            except Exception:  # noqa: BLE001
                return None
        return None

    def focused_control_label(self):
        el = self.focused_element()
        if el is None:
            return None
        if isinstance(el, dict):
            return _common.element_label(el)
        return None

    def desktop_snapshot(self):
        try:
            fg = self.foreground_title()
        except Exception:  # noqa: BLE001
            fg = None
        try:
            focus = self.focused_control_label()
        except Exception:  # noqa: BLE001
            focus = None
        if fg is None and focus is None:
            return None
        return {"fg": fg, "focus": focus}

    # ------------------------------------------------------------------
    # Area 4 — screenshot
    # ------------------------------------------------------------------

    def _grab(self, full_screen: bool = False):
        rect = None if full_screen else self.foreground_window_rect()
        handled, image = self._call("screenshot", "grab",
                                    full_screen, rect)
        if handled:
            return image
        handled, image = self._os_grab(full_screen=full_screen)
        if handled:
            return image
        return None

    def capture_screen(self, max_dim: int = 1024, quality: int = 80,
                       include_cursor: bool = False,
                       mark: tuple[int, int] | None = None,
                       full_screen: bool = False):
        blocked = self._session_block("screenshot")
        image = self._grab(full_screen=full_screen)
        if image is None:
            # Per the ABC contract, capture failure surfaces to
            # the caller (take_screenshot phrases it) — honestly,
            # with the session reason when there is one.
            raise RuntimeError(blocked or (
                f"Screen capture isn't available on this "
                f"{self.name} system yet — no capture seam or OS "
                "grab could run."))
        size = self.screen_size() or (int(image.width), int(image.height))
        fg_rect = None if full_screen else self.foreground_window_rect()
        cropped = fg_rect is not None and tuple(image.size) != tuple(size)
        if fg_rect is not None and cropped:
            cl, ct, cr, cb = fg_rect
        else:
            cl, ct, cr, cb = 0, 0, size[0], size[1]
            cropped = False
        if mark is not None:
            try:
                from PIL import ImageDraw

                draw = ImageDraw.Draw(image)
                mx, my = int(mark[0]) - cl, int(mark[1]) - ct
                r = max(10, image.width // 48)
                draw.ellipse((mx - r, my - r, mx + r, my + r),
                             outline=(255, 0, 0), width=max(3, r // 4))
            except Exception:  # noqa: BLE001 — marker never sinks it
                pass
        image.thumbnail((max_dim, max_dim))
        if image.mode != "RGB":
            image = image.convert("RGB")
        self._last_capture_info = {
            "screen_width": int(size[0]), "screen_height": int(size[1]),
            "image_width": int(image.width),
            "image_height": int(image.height),
            "max_dim": int(max_dim), "cropped": bool(cropped),
            "crop_left": int(cl), "crop_top": int(ct),
            "crop_width": int(cr - cl), "crop_height": int(cb - ct),
        }
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"

    def capture_native_image(self, full_screen: bool = False):
        image = self._grab(full_screen=full_screen)
        if image is None:
            raise RuntimeError(
                f"Screen capture isn't available on this "
                f"{self.name} system yet — no capture seam or OS "
                "grab could run.")
        size = self.screen_size() or (int(image.width), int(image.height))
        fg_rect = None if full_screen else self.foreground_window_rect()
        cropped = fg_rect is not None and tuple(image.size) != tuple(size)
        if fg_rect is not None and cropped:
            cl, ct, cr, cb = fg_rect
        else:
            cl, ct, cr, cb = 0, 0, size[0], size[1]
            cropped = False
        meta = {"screen_width": int(size[0]),
                "screen_height": int(size[1]),
                "image_width": int(image.width),
                "image_height": int(image.height),
                "cropped": bool(cropped),
                "crop_left": int(cl), "crop_top": int(ct),
                "crop_width": int(cr - cl), "crop_height": int(cb - ct)}
        return image, meta

    def last_capture_info(self):
        if self._last_capture_info is None:
            return None
        return dict(self._last_capture_info)

    def capture_geometry_line(self, info: dict | None = None) -> str:
        if info is None:
            info = self._last_capture_info
        return _common.capture_geometry_line(info)

    def take_screenshot(self, save_path: str = "", delay_seconds: float = 0,
                        include_cursor: bool = False) -> str:
        try:
            delay = max(0.0, min(float(delay_seconds or 0), 10.0))
        except (TypeError, ValueError):
            delay = 0.0
        waited = ""
        if delay > 0:
            import time as _time

            _time.sleep(delay)
            waited = f" after waiting {delay:g} second(s)"
        cursor_note = ""
        try:
            data, mime = self.capture_screen(
                include_cursor=bool(include_cursor))
            if include_cursor:
                cursor_note = (" (the cursor's position is marked "
                               "with an arrow)")
        except Exception as e:  # noqa: BLE001
            return (f"Could not capture a screenshot: {e}. Screen "
                    "capture needs a display — it is unavailable on "
                    "a headless system.")
        if not data:
            return "Screenshot capture returned no data."
        if save_path:
            try:
                from pathlib import Path

                dest = Path(str(save_path)).expanduser()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return (f"Screenshot saved to {dest} ({len(data)} bytes, "
                        f"{mime}){waited}{cursor_note}.")
            except Exception as e:  # noqa: BLE001
                return (f"Screenshot was captured ({len(data)} bytes) "
                        f"but could not be saved: {e}")
        return (f"Screenshot captured for the assistant to view "
                f"({len(data)} bytes, {mime}){waited}{cursor_note}.")

    def virtual_screen_bounds(self):
        bounds = self.virtual_bounds()
        if bounds is None:
            return None
        min_x, min_y, max_x, max_y = bounds
        return (min_x, min_y, max_x + 1, max_y + 1)
