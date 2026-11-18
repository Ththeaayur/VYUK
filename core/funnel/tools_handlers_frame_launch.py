"""Frame/launch helpers for the funnel (skills split B48, part 1 of 2).

Moved verbatim from core/funnel/tools_handlers.py: the frame-sink seam
(how a captured frame honestly reaches the model), the browser-controller
and clipboard-secret helpers, the Start-apps inventory, and launch
verification. The ToolHandlersMixin class stays in the facade; its methods
resolve these names through the facade re-exports.
"""

# INTENTIONAL (skills split B48): imports exactly the names the moved code
# resolves by bare name (prover-verified; see prove_tools_handlers_p1.py).
from collections.abc import Callable
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from typing import Any
from .. import injection  # Phase 3 F3: frame code lives in core/funnel/


def article_for(kind: str) -> str:
    """Indefinite article for a schema type name ('an integer', 'a string')."""
    return "an" if kind[:1] in "aeiou" else "a"




# ------------------------------------------------------------------
# Frame sink (v5.42 "Truth", E1) — how a tool-captured frame
# actually reaches the model.
#
# A screenshot tool runs deep inside this module, which is
# GUI-free and session-free on purpose: it cannot know which brain
# is live, or whether that brain can see. So the window — the one
# place that owns the current session — registers a sink here at
# startup (gui/main_window.py) and clears it on close. The sink is
# a plain synchronous callable:
#
#     fn(image_bytes: bytes, mime: str) -> str | None
#
#   * returns None — the frame was consumed as REAL vision (the
#     Gemini Live session received the pixels); only then may a
#     tool result say the model saw the screen.
#   * returns a string — the session is text-only; the string is
#     the frame's OCR/describe text, and the tool result carries
#     it (enveloped as untrusted screen text) so the model answers
#     from text it actually received, never from a picture it
#     never got.
#   * returns FRAME_SINK_DECLINED or raises — no session can
#     receive frames right now; the tool result says exactly that
#     and points at ocr_screen / get_window_controls instead.
#
# No sink registered (headless runs, tests) is the third case too.
_frame_sink: Callable[[bytes, str], Any] | None = None




class _FrameSinkDeclined:
    """Sentinel type for FRAME_SINK_DECLINED (a repr that reads
    honestly in a log beats a bare object())."""

    def __repr__(self) -> str:
        return "FRAME_SINK_DECLINED"




FRAME_SINK_DECLINED = _FrameSinkDeclined()




def set_frame_sink(fn: Callable[[bytes, str], Any] | None) -> None:
    """Registers (or, with None, clears) the frame sink. Called by
    the GUI window, which owns the brain session; this module
    itself never imports the GUI or a session."""
    global _frame_sink
    _frame_sink = fn




def _deliver_frame(data: bytes, mime: str) -> tuple[str, str | None]:
    """Pushes captured bytes through the registered sink.

    Returns ("vision", None) when the sink consumed the frame as
    real vision, ("text", text) when it handed back OCR/describe
    text for a text-only brain, and ("unavailable", None) when
    there is no sink, the sink declined, or the sink raised.
    Never raises — a broken sink must not break the tool; it just
    makes the result honest about the frame going nowhere."""
    sink = _frame_sink
    if sink is None:
        return "unavailable", None
    try:
        outcome = sink(data, mime)
    except Exception:  # noqa: BLE001 — see docstring
        return "unavailable", None
    if outcome is None:
        # v5.45 (H12): the frame was handed to a brain AS AN IMAGE.
        # Pixels are untrusted content the text screen cannot read
        # (visual injection), so delivery marks the session taint
        # with source "image" — the text path below is screened as
        # text instead and does not come through here.
        try:
            injection.note_image_frame()
        except Exception:  # noqa: BLE001 — marking must not break
            pass  # the tool; see core/injection.note_image_frame
        return "vision", None
    if outcome is FRAME_SINK_DECLINED:
        return "unavailable", None
    if isinstance(outcome, str) and outcome.strip():
        return "text", outcome
    return "unavailable", None




def _frame_result_text(data: bytes, mime: str, waited: str = "",
                       notes: str = "") -> str:
    """The honest take_screenshot result for a look (no save_path):
    the wording follows what the sink actually did with the frame.
    Before v5.42 the look path discarded the pixels and still
    claimed every capture was "for the assistant to view"; that
    claim now survives only on the vision branch below, where a
    sink really consumed the frame."""
    kind, text = _deliver_frame(data, mime)
    size = f"({len(data)} bytes, {mime})"
    if kind == "vision":
        return (f"Screenshot captured for the assistant to view "
                f"— the screen was shown to the model "
                f"{size}{waited}{notes}.")
    if kind == "text":
        body = text or ""
        # v5.46 (programme item 1, A1): the old "[Untrusted" not in
        # body condition let OCR text that merely CONTAINED that
        # string pass unwrapped (and unscreened) — removed. The
        # leading-"[" pass-through for already-bracketed
        # describe-seam prompts is unchanged.
        if not body.lstrip().startswith("["):
            # A plain OCR string gets the same untrusted envelope
            # control.ocr_screen puts around screen text (v5.3 H2);
            # a describe-seam prompt arrives already enveloped (or
            # as a bracketed honest prompt) and passes through.
            try:
                body = injection.wrap_untrusted("screen text", body)
            except Exception:  # noqa: BLE001 — wrapping must never
                pass  # break the tool result
            body = f"On-screen text:\n{body}"
        return (f"Screenshot captured {size}{waited}{notes}. The "
                f"current session cannot view images directly, so "
                f"the frame was delivered to the model as text — "
                f"it has the words, not the picture:\n{body}")
    return (f"Screenshot captured {size}{waited}{notes}, but no "
            f"active session can view images right now, so the "
            f"model has not seen it. Use ocr_screen to read the "
            f"screen's text instead, or get_window_controls to "
            f"inspect the active window.")




def _monitor_flag_note(marks=None, backend=None) -> str:
    """Monitor-validation flags for the grounding marks table.

    §7 consumers batch 11: marks whose centre sits on no monitor
    in a FRESH table, or on an unreadable-scale monitor, are
    flagged here so the model re-observes instead of clicking
    into the void. The note is ADDITIVE ONLY: it never alters
    a mark line, never drops a mark, and is "" whenever there
    is nothing flagged — including when no table can be read
    (unvalidated marks are not flagged; the click-time gate in
    click_by_mark still stands). Never raises.
    """
    try:
        if marks is None:
            from ..desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            marks = vision_manager.last_marks()
        from ..desktop import cursor_monitor as _cm  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        result = _cm.validate_marks(marks or [], backend=backend)
    except Exception:  # noqa: BLE001 — flags are additive
        return ""
    flagged = result.get("flagged") or []
    if not flagged:
        return ""
    parts = []
    for entry in flagged:
        mark = entry.get("mark") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else ""
        mid = mark.get("id") if isinstance(mark, dict) else "?"
        parts.append(f"[{mid}] {reason}")
    return ("Monitor flags — re-observe before clicking these ids: "
            + "; ".join(parts))




def _marks_suffix() -> str:
    """The set-of-marks table for the frame just captured, as a
    result suffix (v5.49, programme item 30): the id->control
    table from core/vision.marks_table_text rides ALONGSIDE the
    image in the look results below, so a brain can pick an id
    for click_by_mark. "" when the capture recorded no marks
    (headless, no controls, a capture stand-in in a test) — the
    look results stay byte-identical to before in that case.
    Never raises."""
    try:
        from ..desktop.vision import marks_table_text  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        text = marks_table_text()
    except Exception:  # noqa: BLE001 — marks are additive
        return ""
    if not text:
        return ""
    # §7 consumers batch 11: monitor flags ride with the marks
    # table (additive only, flagged ids only — see
    # _monitor_flag_note). Unvalidated or clean tables leave
    # the suffix byte-identical.
    note = _monitor_flag_note()
    return f" {text}" + (f"\n{note}" if note else "")




def _ocr_captured_frame(data: bytes) -> str:
    """OCR text read from already-captured frame bytes, or "".

    Goes through core/voice/local_client.py's _ocr_frame_text — the
    exact PIL + pytesseract read the ocr_screen tool performs,
    applied to these bytes instead of a fresh capture — so
    screen_look describes the same frame it pushed to the sink.
    "" on ANY failure (no OCR engine, undecodable bytes, no
    readable text); never raises."""
    if not data:
        return ""
    try:
        from ..voice.local_client import _ocr_frame_text  # Phase 3 V1b: moved to core/voice/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        text = (_ocr_frame_text(data) or "").strip()
    except Exception:  # noqa: BLE001 — OCR is best-effort by design
        return ""
    if text:
        return text
    # E7b (v5.44): a CROPPED frame (the E7 foreground-window
    # default) whose OCR yields nothing may be the wrong window
    # — recapture the FULL screen once and re-read, noting the
    # fallback in the returned text the same way ocr_screen
    # notes it. Only when the capture that produced `data` was
    # actually a crop (vision.last_capture_info says so).
    try:
        from ..desktop import vision as _vision  # Phase 3 D4c: vision hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        info = _vision.last_capture_info()
        if info and info.get("cropped"):
            image2, _meta2 = _vision.capture_native_image(
                full_screen=True)
            import io as _io

            buf = _io.BytesIO()
            image2.save(buf, format="JPEG")
            from ..voice.local_client import _ocr_frame_text as _ocr2  # Phase 3 V1b: moved to core/voice/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            retry = (_ocr2(buf.getvalue()) or "").strip()
            if retry:
                return (retry + "\nThe focused window held no "
                        "readable text, so I read the whole "
                        "screen instead.")
    except Exception:  # noqa: BLE001 — fallback is best-effort
        pass
    return ""



_BROWSER_LOOKUP = {
    "chrome": (("chrome", "google-chrome"),
               ("chrome.exe", "google-chrome",
                "google-chrome-stable", "chrome")),
    "edge": (("msedge", "edge"),
             ("msedge.exe", "microsoft-edge", "msedge")),
    "firefox": (("firefox",), ("firefox.exe", "firefox")),
}




def _browser_controller(wanted: str):
    """A webbrowser controller for a named browser, or None when
    that browser cannot be found on this PC. Tries the webbrowser
    module's own names first, then locates the executable on PATH."""
    import shutil

    names, exes = _BROWSER_LOOKUP.get(wanted, ((wanted,), (wanted,)))
    for name in names:
        try:
            return webbrowser.get(name)
        except webbrowser.Error:
            continue
    for exe in exes:
        path = shutil.which(exe)
        if path:
            webbrowser.register(
                wanted, None, webbrowser.BackgroundBrowser(path))
            return webbrowser.get(wanted)
    return None




def _clipboard_secret_shown(text: str):
    """The masked form of the clipboard text when the WHOLE
    text is one secret-looking token, else None (#32).
    Conservative on purpose: a single whitespace-free token
    of 12–200 chars mixing letters with digits (or carrying
    a known key prefix) — ordinary words, sentences and
    pasted paragraphs never match."""
    t = text.strip()
    if t != text or any(c.isspace() for c in t):
        return None
    if not 12 <= len(t) <= 200:
        return None
    mixed = (any(c.isdigit() for c in t)
             and any(c.isalpha() for c in t))
    prefixed = t.startswith(
        ("sk-", "ghp_", "gho_", "AIza", "xoxb-", "xoxp-"))
    if not (mixed or prefixed):
        return None
    return f"{t[:2]}…{t[-2:]} (masked — {len(t)} characters)"




# ------------------------------------------------------------------
# Start apps inventory (v5.38) — the resolution source for Store /
# UWP apps in _tool_launch_app. Apps like Camera register no PATH
# entry and no App Paths key; the only place Windows lists them by
# the name a user actually says is the Start apps inventory
# (Get-StartApps). Enumeration is best-effort: any failure (no
# PowerShell, unparseable output, a timeout) yields [] and the
# launch ladder continues exactly as it did before this step
# existed. Cached process-wide with a ~5-minute TTL so a burst of
# launch attempts spawns PowerShell at most once in a while.
_START_APPS_TTL_SECONDS = 300.0


_START_APPS_CACHE: dict[str, Any] = {"fetched_at": None, "apps": []}




def _start_apps_inventory() -> list[dict]:
    """Start apps as [{"name": ..., "appid": ...}, ...]; [] on any
    failure or off Windows. The AppID is the AppsFolder identity
    the shell itself uses — it comes from the OS, never from user
    text."""
    if not sys.platform.startswith("win"):
        return []
    now = time.monotonic()
    fetched_at = _START_APPS_CACHE["fetched_at"]
    if fetched_at is not None and now - fetched_at < _START_APPS_TTL_SECONDS:
        return _START_APPS_CACHE["apps"]
    apps: list[dict] = []
    try:
        kwargs: dict[str, Any] = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15, **kwargs)
        raw = (proc.stdout or "").strip()
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # A machine with exactly one Start app makes
                # ConvertTo-Json emit a bare object, not an array.
                parsed = [parsed]
            for entry in parsed or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("Name") or "").strip()
                appid = str(entry.get("AppID") or "").strip()
                if name and appid:
                    apps.append({"name": name, "appid": appid})
    except Exception:  # noqa: BLE001 — best-effort; see header note
        apps = []
    _START_APPS_CACHE["fetched_at"] = now
    _START_APPS_CACHE["apps"] = apps
    return apps




def _normalize_start_app_name(text: str) -> str:
    """Casefold + collapse whitespace, for display-name matching."""
    return " ".join(str(text or "").casefold().split())




def _match_start_app(
        apps: list[dict], query: str) -> tuple[dict | None, list[dict]]:
    """Tiered match of the user's words against Start display names.

    Returns (entry, []) on a unique win, (None, candidates) when the
    winning tier holds more than one app (the caller must ask, not
    guess), and (None, []) when nothing matches at any tier. Exact
    beats prefix beats substring.
    """
    q = _normalize_start_app_name(query)
    if not q:
        return None, []
    tiers = (
        lambda n: n == q,
        lambda n: n.startswith(q),
        lambda n: q in n,
    )
    for test in tiers:
        hits = [a for a in apps if test(_normalize_start_app_name(a["name"]))]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, hits
    return None, []




# ------------------------------------------------------------------
# Launch verification (v5.42, H1) — a launch attempt is not proof
# the app appeared. After _tool_launch_app spawns/opens a target it
# polls briefly for evidence: a matching process started after the
# launch (psutil), or a window/foreground title belonging to the
# app (core.control's seams). Three honest verdicts follow: the
# evidence confirms it (today's success wording), the spawn
# succeeded but nothing could be confirmed yet (a slow app), or —
# on the os.startfile path, which proves no spawn — nothing
# appeared at all (no success claim). Verification NEVER raises
# and never blocks longer than the poll window. The evidence probe
# and the poll clock/sleep are module-level seams so suites can
# fake them (no real processes, no real 3 s sleeps).
_LAUNCH_VERIFY_TIMEOUT_S = 3.0


_LAUNCH_VERIFY_INTERVAL_S = 0.15


_launch_clock = time.monotonic


_launch_sleep = time.sleep




def _launch_evidence(target: Any, started_at: float | None) -> str | None:
    """One probe for launch evidence: "process", "window" or None.

    Process: a psutil process whose name matches the launched exe
    basename and whose create_time is at/after the launch (a
    long-running namesake does not count). Window: a visible or
    foreground window title containing the app stem. Best-effort
    on every axis — any failure reads as no evidence, never raises.

    R1a (tools loopholes, seam pass 2): the process leg now uses
    the shared core.tool_outcome.process_running helper (same
    name + created_after rule, with its own no-psutil fallback)
    instead of its own inline psutil scan — one probe, one rule.
    The window leg is unchanged.
    """
    try:
        base = str(target or "").replace("\\", "/").rsplit("/", 1)[-1]
        base = base.strip().lower()
        if base and ":" not in base:
            # R1a: shared helper — name match + created at/after
            # the launch (the helper's created_after is a lower
            # bound, so pass started_at - 1.0, as before).
            from .. import tool_outcome as _outcome  # R1a

            _running, _reason = _outcome.process_running(
                name=base,
                created_after=(None if started_at is None
                               else float(started_at) - 1.0))
            if _running:
                return "process"
        stem = base[:-4] if base.endswith(".exe") else base
        stem = stem.split(":")[0].strip()
        if len(stem) >= 3:
            try:
                from ..desktop import control as _control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

                titles: list[str] = []
                foreground = _control._foreground_title()
                if foreground:
                    titles.append(foreground)
                for _hwnd, title in _control._enum_windows():
                    if title:
                        titles.append(title)
                if any(stem in title.lower() for title in titles):
                    return "window"
            except Exception:  # noqa: BLE001 — no window probe here
                pass
    except Exception:  # noqa: BLE001 — evidence probing never raises
        return None
    return None




def _verify_launch(target: Any, started_at: float | None,
                   allow_process: bool = True) -> str | None:
    """Poll _launch_evidence for up to ~3 s; return the evidence
    kind or None. URI/app-link and Start-apps launches pass
    allow_process=False: no process name can be matched to them,
    so only window evidence confirms those. Never raises."""
    try:
        deadline = _launch_clock() + _LAUNCH_VERIFY_TIMEOUT_S
        while True:
            evidence = None
            try:
                evidence = _launch_evidence(target, started_at)
            except Exception:  # noqa: BLE001 — a broken probe is no evidence
                evidence = None
            if evidence == "window":
                return "window"
            if evidence == "process" and allow_process:
                return "process"
            if _launch_clock() >= deadline:
                return None
            _launch_sleep(_LAUNCH_VERIFY_INTERVAL_S)
    except Exception:  # noqa: BLE001 — verification never raises
        return None
