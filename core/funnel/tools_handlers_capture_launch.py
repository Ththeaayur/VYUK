"""Capture + launch tool handlers (skills split B49, part 2 of 2).

Moved verbatim from core/funnel/tools_handlers.py: the screenshot /
screen-look / launch methods and the shell-metachar class constant
their sole user resolves through self.
"""

# INTENTIONAL (skills split B49): imports exactly the names the moved code
# resolves by bare name (prover-verified; see prove_tools_handlers_p2.py).
import os
import shutil
import subprocess
import sys
import time
from .. import injection
from .tools_handlers_frame_launch import (
    _deliver_frame,
    _frame_result_text,
    _marks_suffix,
    _match_start_app,
    _ocr_captured_frame,
    _start_apps_inventory,
    _verify_launch,
)

class ToolHandlersCaptureLaunchMixin:
    def _tool_take_screenshot(self, save_path: str = "",
                              delay_seconds: float = 0,
                              include_cursor: bool = False,
                              mark_x: int = -1, mark_y: int = -1,
                              scope: str = "foreground",
                              monitor: str = "") -> str:
        """take_screenshot: a file op when save_path is set, a LOOK
        when it isn't (v5.42 "Truth", E1; marker from v5.31 §6 B1).

        save_path set — the pre-v5.42 behaviour byte-for-byte: the
        no-marker case delegates to control.take_screenshot, the
        marker case writes the captured bytes itself, and the
        frame sink is never called — saving a file is not showing
        the model anything, and the result never claimed it was.

        save_path empty — the capture's BYTES are pushed through
        the frame sink (see set_frame_sink at the top of this
        module): real vision for the Live brain, OCR text for
        text-only brains, and an honest "the model has not seen
        it", pointing at ocr_screen / get_window_controls, when no
        session can receive frames. Before v5.42 this path threw
        the pixels away and still claimed the capture was "for
        the assistant to view". With mark_x AND mark_y given, the
        capture goes through vision_manager.capture_screen(mark=…)
        (control's handler has no mark knob); a lone coordinate is
        a malformed request — nothing is captured and the result
        says why.

        v5.43 (E10): the LOOK captures below are grounding captures
        — taken at vision.GROUNDING_MAX_DIM (1568) instead of the
        1024 default so small buttons survive — and every result
        that follows a capture in this method gains the plain
        geometry line from vision.capture_geometry_line() (true
        screen size + sent image size), appended by _geo. The
        delegated plain save keeps control's own capture size;
        its line states that capture's real geometry.

        Desktop programme F-04: scope is "foreground" (default —
        the focused window's crop, as before) or "whole_desktop"
        (the explicit whole-desktop capture through the internal
        full-screen path, on every branch including the save
        branch — a whole-desktop save bypasses control's
        delegation, which has no full-screen knob, and captures
        + writes through vision_manager instead, under the same
        capture-indicator bracketing). Any other scope is refused
        with both valid values named, before any guard prompt or
        capture — nothing is captured for an unknown scope.

        DISPLAY-01: monitor (index, device name, 'main' /
        'primary', 'next') selects one monitor's crop of the
        whole-desktop frame, resolved through
        skills_window_control._resolve_capture_monitor and captured via
        vision's crop_rect; it requires scope "whole_desktop"
        and refuses honestly for an unknown monitor, before any
        guard prompt or capture — never a silent fallback to
        another monitor or to the whole desktop."""
        scope_norm = (scope or "").strip().lower() if isinstance(
            scope, str) else ""
        if scope_norm not in ("foreground", "whole_desktop"):
            return ("take_screenshot: unknown scope — pass scope "
                    "\"foreground\" (the focused window, the default) "
                    "or \"whole_desktop\" (the entire desktop). "
                    "Nothing was captured.")
        whole = scope_norm == "whole_desktop"
        # DISPLAY-01: an explicit monitor selector, resolved BEFORE
        # any guard prompt or capture — a doomed monitor request
        # must not ask the user first. monitor only combines with
        # scope "whole_desktop" (a monitor capture IS a crop of
        # the whole-desktop frame); with "foreground" the two
        # selectors conflict and the call refuses. The resolved
        # rect rides as vision's crop_rect on every capture branch
        # below; None leaves all three branches exactly as before.
        monitor_rect = None
        monitor_note = ""
        if isinstance(monitor, str) and monitor.strip():
            if scope_norm == "foreground":
                return ("take_screenshot: monitor and scope "
                        "\"foreground\" conflict — a monitor capture "
                        "is a crop of the whole desktop, so pass "
                        "scope \"whole_desktop\" with monitor. "
                        "Nothing was captured.")
            try:
                from .. import skills_window_control as _h3  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                monitor_target, _merr = _h3._resolve_capture_monitor(
                    monitor)
            except Exception as e:  # noqa: BLE001
                return (f"take_screenshot: could not resolve monitor "
                        f"'{monitor}': {e}. Nothing was captured.")
            if monitor_target is None:
                return (f"take_screenshot: {_merr or 'unknown monitor'} "
                        "Nothing was captured.")
            monitor_rect = _h3._monitor_rect(monitor_target)
            monitor_note = (f" (monitor {monitor_target['index']}: "
                            f"{monitor_target['device']}, "
                            f"{monitor_target['width']}×"
                            f"{monitor_target['height']})")

        def _geo(result: str) -> str:
            """Append the E10 geometry line for the capture this
            call just made ("" — result untouched — when no capture
            geometry is on record, e.g. a capture stand-in in a
            test that never recorded any)."""
            try:
                from ..desktop.vision import capture_geometry_line  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

                line = capture_geometry_line()
            except Exception:  # noqa: BLE001 — geometry is additive;
                line = ""  # it must never sink the result itself
            return f"{result} {line}" if line else result

        try:
            mx, my = int(mark_x), int(mark_y)
        except (TypeError, ValueError):
            mx = my = -1
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        # v5.48 (programme item 21): a screenshot that is SAVED
        # to a file is a persisting capture, so BEFORE anything
        # is captured the capture guard decides — the
        # sensitive-window denylist first, then the per-session
        # screenshot_save opt-in (capture_policy, asked through
        # core.confirm_gate). The plain LOOK (no save_path) is
        # untouched by this block: Tier-0 observe, denylist only
        # (enforced at the vision layer), no ask — byte-identical
        # to before. The legacy contract this changes:
        # take_screenshot with a save_path used to write the file
        # on a model call alone.
        _cg = None
        if save_path:
            try:
                from .. import capture_guard as _cg  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                _allowed, _why = _cg.ensure_capture_allowed(
                    "screenshot_save")
            except Exception as e:  # noqa: BLE001 — broken guard
                return (f"Screenshot save refused: the capture "
                        f"guard could not run ({e}).")
            if not _allowed:
                return f"Screenshot save refused: {_why}"

        if mx < 0 and my < 0:
            if save_path:
                # A look with no marker: capture the bytes here — a
                # whole-desktop save bypasses control's delegation
                # (F-04: it has no full-screen knob) and captures
                # + writes through vision_manager instead, under
                # the same capture-indicator bracketing.
                _cg.note_capture_started("screenshot_save")
                try:
                    if whole:
                        try:
                            delay = max(0.0, min(float(delay_seconds or 0), 10.0))
                        except (TypeError, ValueError):
                            delay = 0.0
                        waited = ""
                        if delay > 0:
                            import time as _time

                            _time.sleep(delay)
                            waited = f" after waiting {delay:g} second(s)"
                        try:
                            from ..desktop.vision import GROUNDING_MAX_DIM, vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

                            data, mime = vision_manager.capture_screen(
                                max_dim=GROUNDING_MAX_DIM,
                                include_cursor=bool(include_cursor),
                                full_screen=True,
                                crop_rect=monitor_rect)
                        except Exception as e:  # noqa: BLE001
                            return (
                                f"Could not capture a screenshot: {e}. "
                                "Screen capture needs a display — it is unavailable "
                                "on a headless system."
                            )
                        if not data:
                            return "Screenshot capture returned no data."
                        try:
                            from pathlib import Path

                            dest = Path(str(save_path)).expanduser()
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(data)
                            return _geo(f"Screenshot saved to {dest} "
                                        f"({len(data)} bytes, {mime}){waited} "
                                        f"(whole-desktop capture){monitor_note}.")
                        except Exception as e:  # noqa: BLE001
                            return _geo(f"Screenshot was captured ({len(data)} bytes) "
                                        f"but could not be saved: {e}")
                    return _geo(control.take_screenshot(
                        save_path=save_path,
                        delay_seconds=delay_seconds,
                        include_cursor=include_cursor))
                finally:
                    _cg.note_capture_ended("screenshot_save")
            # A look with no marker: capture the bytes here —
            # control's handler returns text only and discards
            # them, which is exactly the lie E1 removes.
            try:
                delay = max(0.0, min(float(delay_seconds or 0), 10.0))
            except (TypeError, ValueError):
                delay = 0.0
            waited = ""
            if delay > 0:
                import time as _time

                _time.sleep(delay)
                waited = f" after waiting {delay:g} second(s)"
            try:
                from ..desktop.vision import GROUNDING_MAX_DIM, vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

                data, mime = vision_manager.capture_screen(
                    max_dim=GROUNDING_MAX_DIM,
                    include_cursor=bool(include_cursor), marks=True,
                    full_screen=whole, crop_rect=monitor_rect)
            except Exception as e:  # noqa: BLE001
                return (
                    f"Could not capture a screenshot: {e}. "
                    "Screen capture needs a display — it is unavailable "
                    "on a headless system."
                )
            if not data:
                return "Screenshot capture returned no data."
            notes = ""
            if include_cursor:
                notes = (" (the cursor's position is marked with "
                         "an arrow)")
            if whole:
                notes += f" (whole-desktop capture){monitor_note}"
            return _geo(_frame_result_text(data, mime, waited, notes)
                        + _marks_suffix())
        if mx < 0 or my < 0:
            return ("mark_x and mark_y must be given together to "
                    "mark a point — nothing was captured.")
        # v5.48 (programme item 21): on the save path the capture
        # indicator brackets the capture+save below (the guard
        # already allowed it above; _cg is bound whenever
        # save_path is set). The look path through this branch
        # starts nothing and _finish is then a plain return.
        _save_bracket = bool(save_path)
        if _save_bracket:
            _cg.note_capture_started("screenshot_save")

        def _finish(result: str) -> str:
            if _save_bracket:
                _cg.note_capture_ended("screenshot_save")
            return result

        try:
            delay = max(0.0, min(float(delay_seconds or 0), 10.0))
        except (TypeError, ValueError):
            delay = 0.0
        waited = ""
        if delay > 0:
            import time as _time

            _time.sleep(delay)
            waited = f" after waiting {delay:g} second(s)"
        try:
            from ..desktop.vision import GROUNDING_MAX_DIM, vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            data, mime = vision_manager.capture_screen(
                max_dim=GROUNDING_MAX_DIM,
                include_cursor=bool(include_cursor), mark=(mx, my),
                marks=not bool(save_path), full_screen=whole,
                crop_rect=monitor_rect)
        except Exception as e:  # noqa: BLE001
            return _finish(
                f"Could not capture a screenshot: {e}. "
                "Screen capture needs a display — it is unavailable "
                "on a headless system."
            )
        if not data:
            return _finish("Screenshot capture returned no data.")
        notes = f" (a ring marks the point ({mx}, {my}))"
        if include_cursor:
            notes += (" (the cursor's position is marked with "
                      "an arrow)")
        if whole:
            notes += f" (whole-desktop capture){monitor_note}"
        if save_path:
            try:
                from pathlib import Path

                dest = Path(str(save_path)).expanduser()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return _finish(_geo(f"Screenshot saved to {dest} "
                            f"({len(data)} bytes, {mime}){waited}{notes}."))
            except Exception as e:  # noqa: BLE001
                return _finish(_geo(f"Screenshot was captured ({len(data)} bytes) "
                            f"but could not be saved: {e}"))
        return _finish(_geo(_frame_result_text(data, mime, waited, notes)
                            + ("" if save_path else _marks_suffix())))

    def _tool_screen_look(self, scope: str = "foreground") -> str:
        """screen_look (v5.42 "Truth", E1): one call that looks at
        the screen and ALWAYS brings back words.

        Captures one frame and pushes it through the frame sink
        (real vision for the Live brain; for every other brain the
        OCR summary below is what the model actually gets), then
        reads that same frame with OCR and names the active
        window. Never raises: each piece that is unavailable is
        reported as unavailable, in plain words, next to the
        pieces that worked.

        Desktop programme F-03: the result gains the same
        capture-geometry line take_screenshot appends (true screen
        size + sent image size + crop origin/size when cropped),
        so coordinates read off this look are interpretable
        against the screen — the line is additive and never sinks
        the result when no geometry is on record.

        Desktop programme F-04: scope is "foreground" (default —
        the focused window's crop, as before) or "whole_desktop"
        (the explicit whole-desktop capture through the internal
        full-screen path, with a plain sentence saying so).
        Any other scope is refused with both valid values named
        — nothing is captured for an unknown scope.
        """
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        scope_norm = (scope or "").strip().lower() if isinstance(
            scope, str) else ""
        if scope_norm not in ("foreground", "whole_desktop"):
            return ("screen_look: unknown scope — pass scope "
                    "\"foreground\" (the focused window, the default) "
                    "or \"whole_desktop\" (the entire desktop). "
                    "Nothing was captured.")
        whole = scope_norm == "whole_desktop"
        try:
            from ..desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            data, mime = vision_manager.capture_screen(
                marks=True, full_screen=whole)
        except Exception as e:  # noqa: BLE001
            return (f"screen_look could not capture the screen: {e}. "
                    "Screen capture needs a display — it is "
                    "unavailable on a headless system.")
        if not data:
            return "screen_look: screenshot capture returned no data."
        kind, _text = _deliver_frame(data, mime)
        try:
            title_line = control.get_active_window()
        except Exception as e:  # noqa: BLE001
            title_line = f"Could not read the active window: {e}"
        ocr_text = _ocr_captured_frame(data)
        ocr_body = None
        if ocr_text:
            ocr_body = ocr_text
            try:
                # Screen text is untrusted content (v5.3 H2) — the
                # same envelope ocr_screen puts around its output.
                ocr_body = injection.wrap_untrusted(
                    "screen text", ocr_body)
            except Exception:  # noqa: BLE001 — wrapping must never
                pass  # break the look
        # v5.49 (programme item 30): the grounding marks table
        # rides alongside the frame ("" when no marks were
        # recorded, keeping the pre-v5.49 result byte-identical).
        # Desktop programme F-03: the capture-geometry line for
        # the frame this look just captured ("" when no geometry
        # is on record — additive only, it never sinks the result).
        try:
            from ..desktop.vision import capture_geometry_line  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            geo_line = capture_geometry_line()
        except Exception:  # noqa: BLE001
            geo_line = ""
        marks_line = _marks_suffix().strip()
        if kind == "vision":
            # v5.53 (F3): on a VISION session the model HAS the
            # picture — the result must not talk it out of that.
            # The old layout led with byte counts and, when OCR
            # was dark (no Tesseract staged — the field case),
            # appended the "OCR summary unavailable" failure
            # line verbatim; the model read its own tool output
            # and disclaimed sight it actually had ("he's not
            # able to look at my screen"). Now: the window line
            # and a plain directive lead, the technical line
            # follows, OCR rides only when it produced text,
            # and a failed OCR is not mentioned at all.
            vlines = [
                str(title_line),
                "Describe what you see in the image.",
                (f"Screen look ({len(data)} bytes, {mime}): "
                 "the frame was shown to the model as an image."),
            ]
            if whole:
                vlines.append(
                    "Whole-desktop capture: this image shows the "
                    "entire desktop, not just the focused window.")
            if ocr_body is not None:
                vlines.append(f"On-screen text:\n{ocr_body}")
            if marks_line:
                vlines.append(marks_line)
            if geo_line:
                vlines.append(geo_line)
            return "\n".join(vlines)
        lines = [f"Screen look ({len(data)} bytes, {mime}):"]
        if kind == "text":
            lines.append(
                "The current session is text-only, so no image was "
                "shown — the model gets this look as the OCR text "
                "below.")
        else:
            lines.append(
                "No active session could receive the frame as an "
                "image, so the model has not seen the picture — "
                "the OCR text below is what it gets.")
        lines.append(str(title_line))
        if whole:
            lines.append(
                "Whole-desktop capture: this image shows the "
                "entire desktop, not just the focused window.")
        if ocr_body is not None:
            lines.append(f"On-screen text:\n{ocr_body}")
        else:
            lines.append(
                "OCR summary unavailable: no readable text could "
                "be extracted from this screen (OCR may not be set "
                "up on this machine, or the screen may hold no "
                "text).")
        if marks_line:
            lines.append(marks_line)
        if geo_line:
            lines.append(geo_line)
        return "\n".join(lines)

    # --- Tool Implementations ---
    # Characters that are shell syntax, never part of an app name.
    # The v4.4 rewrite launches WITHOUT a shell (list-form Popen /
    # os.startfile), and a name carrying these is refused outright —
    # an app name must never become a command line (audit_core MAJOR:
    # the old `start {target}` interpolation executed injected text).
    _SHELL_METACHARS = frozenset('&|;<>^%"\'\n\r')

    def _tool_launch_app(self, app_name: str, arguments: str = "") -> str:
        raw = (app_name or "").strip()
        if not raw:
            return "No application name given — tell me which app to open."
        if any(ch in self._SHELL_METACHARS for ch in raw):
            return (
                f"Refused: '{app_name}' contains characters that are not "
                "valid in an application name, so nothing was launched. "
                "Ask for the app by its plain name (e.g. 'notepad')."
            )
        # Optional command-line arguments (v5.31). They travel as
        # separate list items everywhere below — never through a
        # shell — so they cannot smuggle in a second command.
        # Split with Windows semantics on Windows (posix=False
        # keeps quoted segments the way Windows apps expect them),
        # POSIX rules elsewhere.
        arg_text = str(arguments or "").strip()
        arg_list: list[str] = []
        if arg_text:
            import shlex

            try:
                arg_list = shlex.split(
                    arg_text, posix=not sys.platform.startswith("win"))
            except ValueError:
                return (
                    f"Could not read the arguments '{arguments}' — "
                    "the quoting looks unbalanced, so nothing was "
                    "launched."
                )
        name_lower = raw.lower()
        common_aliases = {
            "notepad": "notepad.exe",
            "calc": "calc.exe",
            "calculator": "calc.exe",
            "cmd": "cmd.exe",
            "terminal": "wt.exe",
            "powershell": "powershell.exe",
            "explorer": "explorer.exe",
            "task manager": "taskmgr.exe",
            "settings": "ms-settings:",
            "chrome": "chrome.exe",
            "edge": "msedge.exe",
            "firefox": "firefox.exe",
            "spotify": "spotify.exe",
            "code": "code.cmd",
            "vscode": "code.cmd",
            "visual studio code": "code.cmd",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
            "whatsapp": "whatsapp:",
            "vlc": "vlc.exe",
            "obs": "obs64.exe",
            "discord": "Discord.exe",
            "telegram": "Telegram.exe",
            "notepad++": "notepad++.exe"
        }
        target = common_aliases.get(name_lower, raw)
        if target.startswith("ms-") or target.endswith(":"):
            # URI-protocol targets (ms-settings:, whatsapp:) have no exe
            # to resolve. Hand them to the OS via os.startfile — the
            # shell's "open this" verb WITHOUT a command line to inject
            # into. Windows-only by construction.
            if not sys.platform.startswith("win"):
                return (
                    f"Opening '{app_name}' uses a Windows app link — app "
                    "launching is Windows-only in this build, so nothing "
                    "was opened."
                )
            try:
                started_at = time.time()
                os.startfile(target)  # noqa: S606 — fixed alias target
                # v5.42 (H1): app links can't be process-verified —
                # window evidence only, and unverifiable keeps an
                # honest middle verdict, never a plain success.
                confirmed = _verify_launch(
                    target, started_at, allow_process=False)
                if arg_list:
                    # App links have no command line to attach
                    # arguments to — launched as always, and the
                    # drop is said out loud, never silent.
                    drop_note = (
                        f"The arguments ('{arg_text}') were not "
                        "passed: Windows app links like this one "
                        "can't take command-line arguments."
                    )
                    if confirmed:
                        return f"Opened {app_name}. {drop_note}"
                    return (
                        f"Opened {app_name}. It went through its "
                        f"Windows app link, but no window could "
                        f"be confirmed yet — it may still be "
                        f"loading. {drop_note}"
                    )
                if confirmed:
                    return f"Opened {app_name}."
                return (
                    f"Opened {app_name}. It went through its "
                    "Windows app link, but no window could be "
                    "confirmed yet — it may still be loading."
                )
            except Exception as e:  # noqa: BLE001
                return f"Could not open {app_name}: {e}"

        # Executable targets: resolve first, launch shell-free.
        resolved = shutil.which(target)
        if resolved:
            try:
                started_at = time.time()
                if resolved.lower().endswith((".cmd", ".bat")):
                    # Batch launchers can't be CreateProcess'd directly;
                    # cmd /c with the RESOLVED path as a single list item
                    # is still shell-injection-free (no user text in it).
                    subprocess.Popen(["cmd", "/c", resolved] + arg_list)
                else:
                    subprocess.Popen([resolved] + arg_list)
            except Exception as e:  # noqa: BLE001
                return f"Could not launch {app_name}: {e}"
            # v5.42 (H1): the spawn succeeded, so an unconfirmed
            # app is "started but not confirmed yet" — never a
            # failure, never a plain success either.
            if _verify_launch(resolved, started_at):
                if arg_list:
                    return (f"Launched {app_name} successfully "
                            f"(arguments: {arg_text}).")
                return f"Launched {app_name} successfully."
            unconfirmed = (
                f"Launched {app_name}: it started, but no process "
                "or window could be confirmed yet — it may still "
                "be loading, so give it a moment."
            )
            if arg_list:
                unconfirmed += f" It was started with arguments: {arg_text}."
            return unconfirmed

        # Not on PATH. Store / UWP apps (v5.38) live one rung down:
        # they register no App Paths key either, but they ARE in the
        # Start apps inventory under the name the user said. A unique
        # match launches through the AppsFolder — the shell's own
        # "open this app" verb, with the AppID from the OS inventory.
        # Several matches at the winning tier launch NOTHING: she
        # names them and asks, rather than guessing. Windows-only.
        if sys.platform.startswith("win"):
            entry, candidates = _match_start_app(
                _start_apps_inventory(), raw)
            if entry is not None:
                try:
                    started_at = time.time()
                    subprocess.Popen(
                        ["explorer.exe",
                         f"shell:AppsFolder\\{entry['appid']}"])
                except Exception as e:  # noqa: BLE001
                    return f"Could not launch {app_name}: {e}"
                # v5.42 (H1): an AppsFolder launch has no process
                # name to match — window evidence only, and
                # unverifiable keeps an honest middle verdict.
                confirmed = _verify_launch(
                    entry["name"], started_at, allow_process=False)
                if arg_list:
                    # The AppsFolder verb has no command line to
                    # attach arguments to — launched anyway, and
                    # the drop is said out loud, never silent
                    # (same pattern as the app-link branch above).
                    drop_note = (
                        f"The arguments ('{arg_text}') were not "
                        "passed: apps opened from the Start menu "
                        "like this one can't take command-line "
                        "arguments."
                    )
                    if confirmed:
                        return f"Opened {app_name}. {drop_note}"
                    return (
                        f"Opened {app_name}. It was launched from "
                        f"the Start menu, but no window could be "
                        f"confirmed yet — it may still be "
                        f"loading. {drop_note}"
                    )
                if confirmed:
                    return f"Opened {app_name}."
                return (
                    f"Opened {app_name}. It was launched from "
                    "the Start menu, but no window could be "
                    "confirmed yet — it may still be loading."
                )
            if candidates:
                names = ", ".join(sorted(
                    {c["name"] for c in candidates},
                    key=str.casefold))
                return (
                    f"More than one app matches '{app_name}': "
                    f"{names} — nothing was launched. Tell me "
                    "which one you meant."
                )

        # Not on PATH. On Windows, os.startfile also consults the App
        # Paths registry (how winword.exe & friends are found); its
        # failure is a REAL failure we report, never a fake success.
        if sys.platform.startswith("win"):
            try:
                started_at = time.time()
                if arg_list:
                    # os.startfile's arguments parameter passes the
                    # string through ShellExecute — Windows only,
                    # which this branch already guarantees.
                    os.startfile(target, arguments=arg_text)  # noqa: S606
                else:
                    os.startfile(target)  # noqa: S606 — metachar-free name
                # v5.42 (H1): startfile proves no spawn, so with no
                # evidence within the window the honest verdict is
                # "did not appear" — never a success claim.
                if _verify_launch(target, started_at):
                    if arg_list:
                        return (f"Launched {app_name} successfully "
                                f"(arguments: {arg_text}).")
                    return f"Launched {app_name} successfully."
                return (
                    f"{app_name} did not appear: no process or "
                    "window showed up after launching, so nothing "
                    "was confirmed to have opened. Check the name "
                    "and that the app is installed, or give the "
                    "full path to the program."
                )
            except Exception:  # noqa: BLE001
                pass
        return (
            f"Could not find an application named '{app_name}' on this "
            "PC — nothing was launched. Check the name, or give the "
            "full path to the program."
        )
