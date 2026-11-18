from __future__ import annotations

import io  # INTENTIONAL (B28: _render_image/capture_screen use io by bare name)
from . import _common  # INTENTIONAL (B28: mixin methods resolve _common.* by bare name)
from .base import PlatformBackend  # INTENTIONAL (B28: mixin base class)
from .fake_helpers import (  # INTENTIONAL (B28: mixin methods resolve these by bare name)
    FakeElement,
    _PASSWORD_NAME_RE,
    _SCREEN_H,
    _SCREEN_W,
    _TEXT_TYPES,
)


class FakeBackendWindowUiaCaptureMixin(PlatformBackend):
    def _element_at(self, x, y):
        win = self._foreground_window()
        if win is None:
            return None
        for el in reversed(self._all_elements(win)):
            left, top, right, bottom = el.rect
            if left <= x < right and top <= y < bottom:
                return el
        return None

    # ------------------------------------------------------------------
    # Area 2 — windows
    # ------------------------------------------------------------------

    def foreground_title(self):
        win = self._foreground_window()
        if win is None or not win.title:
            return None
        return win.title

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
        if action == "list":
            if not self.windows:
                return "No visible windows with titles were found."
            lines = [f"• {w.title}" for w in self.windows[:50]]
            header = f"Open windows ({len(self.windows)}):"
            if len(self.windows) > 50:
                header = f"Open windows ({len(self.windows)}, showing first 50):"
            return header + "\n" + "\n".join(lines)
        needle = (title_substring or "").strip().lower()
        if not needle:
            return f"Window '{action}' needs a title_substring to match a window."
        exact = [w for w in self.windows if w.title.lower() == needle]
        partial = [w for w in self.windows if needle in w.title.lower()]
        candidates = exact if exact else partial
        if not candidates:
            return f"No window found with title containing '{title_substring}'."
        if len(candidates) > 1:
            listing = ", ".join(f"'{w.title}'" for w in candidates)
            return (f"More than one window matches '{title_substring}' "
                    f"({listing}) — give a more specific title. "
                    "Nothing was changed.")
        win = candidates[0]
        self._record("window_control", action=action, title=win.title)
        if action == "focus":
            self._foreground_hwnd = win.hwnd
            return f"Focused window: {win.title}"
        if action == "close":
            title = win.title
            self.close_window(win.hwnd)
            return f"Closed window: {title}"
        verb = "Minimized" if action == "minimize" else "Maximized"
        return f"{verb} window: {win.title}"

    def enum_windows(self):
        return [(w.hwnd, w.title) for w in self.windows if w.title]

    def foreground_window_rect(self):
        win = self._foreground_window()
        return win.rect if win is not None else None

    def foreground_process_name(self):
        win = self._foreground_window()
        return win.process if win is not None else None

    def close_application(self, name: str, confirm: bool = False) -> str:
        if not name or not str(name).strip():
            return "No application name given."
        if not confirm:
            return (f"Blocked by VYUK's trust layer: closing '{name}' "
                    "needs confirm=true. Ask again with confirmation "
                    "and VYUK will terminate it gracefully.")
        needle = str(name).strip().lower()
        matched = [w for w in self.windows
                   if needle in w.process.lower()
                   or w.process.lower().replace(".exe", "") == needle]
        if not matched:
            return f"No running process matches '{name}'."
        for win in list(matched):
            self.close_window(win.hwnd)
        self._record("close_application", name=name, closed=len(matched))
        return (f"Closed {len(matched)} process(es) matching '{name}'. "
                f"Confirmed closed: all {len(matched)} terminated "
                "process(es) are gone.")

    # ------------------------------------------------------------------
    # Area 3 — accessibility
    # ------------------------------------------------------------------

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
        try:
            win, desc = self._resolve_window(window_title)
        except LookupError as e:
            return f"Could not connect to the target window: {e}"
        lines: list[str] = []
        state = {"shown": 0, "truncated": False}

        def _emit(label, depth):
            if state["shown"] >= max_nodes:
                state["truncated"] = True
                return False
            state["shown"] += 1
            lines.append("  " * depth + "• " + label)
            return True

        def _walk(el, depth):
            name = el.name or "(unnamed)"
            if not _emit(f"{name} [{el.control_type}]", depth):
                return
            if depth >= max_depth:
                if el.children:
                    state["truncated"] = True
                return
            for child in el.children:
                _walk(child, depth + 1)

        _emit(f"{win.title} [Window]", 0)
        for el in win.controls:
            _walk(el, 1)
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
        try:
            win, desc = self._resolve_window(window_title)
        except LookupError as e:
            return f"Could not connect to the target window: {e}"
        matches = self._find(win, name, control_type)
        scanned = len(self._all_elements(win))
        if not matches:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc} (scanned {scanned} elements).")
        header = (f"Found {len(matches)} control(s) matching '{name}' "
                  f"in {desc}:")
        body = "\n".join(_common.format_facts(el.facts()) for el in matches)
        text = header + "\n" + body
        return text[:1500] + (f"\n... [truncated, {len(text) - 1500} more chars]"
                              if len(text) > 1500 else "")

    def uia_click_control(self, name: str, window_title: str = "",
                          control_type: str = "") -> str:
        if not name or not str(name).strip():
            return "No control name given. Tell me the label of the control to click."
        try:
            win, desc = self._resolve_window(window_title)
        except LookupError as e:
            return f"Could not connect to the target window: {e}"
        matches = self._find(win, name, control_type)
        if not matches:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc}.")
        total = len(matches)
        skipped: list[str] = []
        chosen = None
        for el in matches:
            if not el.enabled:
                skipped.append(f"'{el.name}' (disabled)")
            elif not el.visible:
                skipped.append(f"'{el.name}' (hidden)")
            elif el.rect[2] - el.rect[0] <= 0 or el.rect[3] - el.rect[1] <= 0:
                skipped.append(f"'{el.name}' (off-screen (zero-size rectangle))")
            else:
                chosen = el
                break
        if chosen is None:
            return (f"Found {total} control(s) matching '{name}' in "
                    f"{desc}, but none could be clicked: "
                    + "; ".join(skipped) + ". Nothing was clicked.")
        note = ""
        if total > 1:
            note = f" ({total} controls matched — clicked the first usable one"
            if skipped:
                note += f"; skipped {len(skipped)} unusable: {'; '.join(skipped)}"
            note += ")"
        fg_before = self.foreground_title()
        self._record("uia_click_control", name=chosen.name,
                     window=win.title)
        self._click_element(chosen)
        base = (f"Clicked '{chosen.name}' ({chosen.control_type}) at "
                f"{_common.rect_text(chosen.rect)} in {desc}.{note}")
        fg_after = self.foreground_title()
        if fg_after and fg_before and fg_after != fg_before:
            base += f" The {fg_after} window came to front."
        return base

    def uia_type_text(self, name: str, text: str, window_title: str = "",
                      clear_first: bool = True) -> str:
        if not name or not str(name).strip():
            return "No control name given. Tell me which field to type into."
        if text is None:
            text = ""
        text = str(text)
        try:
            win, desc = self._resolve_window(window_title)
        except LookupError as e:
            return f"Could not connect to the target window: {e}"
        matches = self._find(win, name)
        if not matches:
            return (f"No control with a name containing '{name}' was "
                    f"found in {desc}.")
        chosen = None
        for el in matches:
            if el.control_type in _TEXT_TYPES:
                chosen = el
                break
        if chosen is None:
            first = matches[0]
            return (f"'{first.name}' is a {first.control_type or 'non-text'} "
                    "control, not a text field — I won't type into it. "
                    "Name the field you mean, or use uia_find_control to "
                    "see the text fields in the window.")
        verdict = self.is_password_control(chosen)
        if verdict is True:
            return ("That's a password field — passwords are yours to "
                    "type, I won't. Everything else in the window I can "
                    "fill for you.")
        if verdict is None and chosen.control_type == "Edit" \
                and _PASSWORD_NAME_RE.search(chosen.name):
            return (f"'{chosen.name}' looks like a password field and I "
                    "can't verify that it isn't — I won't type into it. "
                    "Passwords are yours to type.")
        self._focused = chosen
        payload = text if clear_first else (chosen.value + text)
        chosen.value = payload
        # The log records the call (method + target + length) —
        # the typed text itself stays out of every result string,
        # exactly like the real backends; the full text is only in
        # the in-memory log entry's "text" field for test asserts.
        self._record("uia_type_text", name=chosen.name, text=text,
                     window=win.title)
        return (f"Typed {len(text)} character(s) into '{chosen.name}' "
                f"({chosen.control_type}) in {desc}.")

    def describe_point(self, x: int, y: int):
        try:
            hit = self._element_at(int(x), int(y))
        except Exception:  # noqa: BLE001 — never raises
            return None
        win = self._foreground_window()
        if hit is None:
            return None
        label = _common.element_label(hit.facts())
        title = win.title if win is not None else ""
        if label:
            return f"{label} in {title}" if title else label
        if title:
            return f"(no named control) in {title}"
        return None

    def focused_element(self):
        return self._focused

    def is_password_control(self, el):
        if el is None:
            return None
        # Scripted elements carry the verdict directly; foreign
        # element shapes are read through the UIA property seam,
        # and anything unreadable is UNKNOWN (None), never False.
        if isinstance(el, FakeElement):
            return el.is_password
        if isinstance(el, dict):
            return el.get("is_password")
        try:
            raw = el.element_info.element.GetCurrentPropertyValue(30019)
            return bool(raw)
        except Exception:  # noqa: BLE001
            return None

    def focused_control_label(self):
        if self._focused is None:
            return None
        return _common.element_label(self._focused.facts())

    def desktop_snapshot(self):
        fg = self.foreground_title()
        focus = self.focused_control_label()
        if fg is None and focus is None:
            return None
        return {"fg": fg, "focus": focus}

    # ------------------------------------------------------------------
    # Area 4 — screenshot (deterministic generated images)
    # ------------------------------------------------------------------

    def _render_image(self, full_screen: bool = False):
        """Render the scripted desktop to a PIL Image,
        deterministically: window rectangles in script order,
        control rectangles inside them, colours derived from a
        stable hash of the titles/names. Same state → same image
        → same JPEG bytes."""
        from PIL import Image, ImageDraw

        fg = self._foreground_window()
        if full_screen or fg is None:
            image = Image.new("RGB", (_SCREEN_W, _SCREEN_H), (24, 24, 32))
            meta = {"screen_width": _SCREEN_W, "screen_height": _SCREEN_H,
                    "cropped": False, "crop_left": 0, "crop_top": 0,
                    "crop_width": _SCREEN_W, "crop_height": _SCREEN_H}
            draw = ImageDraw.Draw(image)
            for win in self.windows:
                self._draw_window(draw, win)
            return image, meta
        left, top, right, bottom = fg.rect
        image = Image.new("RGB", (right - left, bottom - top), (24, 24, 32))
        draw = ImageDraw.Draw(image)
        self._draw_window(draw, fg, origin=(left, top))
        meta = {"screen_width": _SCREEN_W, "screen_height": _SCREEN_H,
                "cropped": True, "crop_left": left, "crop_top": top,
                "crop_width": right - left, "crop_height": bottom - top}
        return image, meta

    @staticmethod
    def _colour(seed_text: str):
        import hashlib

        digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
        return (64 + digest[0] % 160, 64 + digest[1] % 160,
                64 + digest[2] % 160)

    def _draw_window(self, draw, win, origin=(0, 0)):
        ox, oy = origin
        left, top, right, bottom = win.rect
        draw.rectangle((left - ox, top - oy, right - ox, bottom - oy),
                       fill=self._colour("win:" + win.title),
                       outline=(230, 230, 230), width=2)
        for el in self._all_elements(win):
            l2, t2, r2, b2 = el.rect
            draw.rectangle((l2 - ox, t2 - oy, r2 - ox, b2 - oy),
                           fill=self._colour("ctl:" + el.name),
                           outline=(255, 255, 255), width=1)

    def capture_screen(self, max_dim: int = 1024, quality: int = 80,
                       include_cursor: bool = False,
                       mark: tuple[int, int] | None = None,
                       full_screen: bool = False):
        image, meta = self._render_image(full_screen=full_screen)
        if mark is not None:
            from PIL import ImageDraw

            draw = ImageDraw.Draw(image)
            mx = int(mark[0]) - meta["crop_left"]
            my = int(mark[1]) - meta["crop_top"]
            r = max(10, image.width // 48)
            draw.ellipse((mx - r, my - r, mx + r, my + r),
                         outline=(255, 0, 0), width=max(3, r // 4))
        if include_cursor:
            from PIL import ImageDraw

            draw = ImageDraw.Draw(image)
            cx = self._cursor[0] - meta["crop_left"]
            cy = self._cursor[1] - meta["crop_top"]
            draw.polygon([(cx, cy), (cx, cy + 14), (cx + 5, cy + 10),
                          (cx + 8, cy + 15), (cx + 11, cy + 13),
                          (cx + 7, cy + 9), (cx + 12, cy + 9)],
                         fill=(255, 255, 255), outline=(0, 0, 0))
        image.thumbnail((max_dim, max_dim))
        if image.mode != "RGB":
            image = image.convert("RGB")
        self._last_capture_info = {
            "screen_width": meta["screen_width"],
            "screen_height": meta["screen_height"],
            "image_width": int(image.width),
            "image_height": int(image.height),
            "max_dim": int(max_dim),
            "cropped": bool(meta["cropped"]),
            "crop_left": int(meta["crop_left"]),
            "crop_top": int(meta["crop_top"]),
            "crop_width": int(meta["crop_width"]),
            "crop_height": int(meta["crop_height"]),
        }
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"

    def capture_native_image(self, full_screen: bool = False):
        image, meta = self._render_image(full_screen=full_screen)
        meta = dict(meta, image_width=int(image.width),
                    image_height=int(image.height))
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
            data, mime = self.capture_screen(include_cursor=bool(include_cursor))
            if include_cursor:
                cursor_note = (" (the cursor's position is marked with "
                               "an arrow)")
        except Exception as e:  # noqa: BLE001
            return (f"Could not capture a screenshot: {e}. Screen "
                    "capture needs a display — it is unavailable on a "
                    "headless system.")
        if not data:
            return "Screenshot capture returned no data."
        self._record("take_screenshot", save_path=save_path)
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
        return (0, 0, _SCREEN_W, _SCREEN_H)
