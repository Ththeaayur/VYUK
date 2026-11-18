# Refactor Phase 1 step 5: moved verbatim from gui/window_widgets.py (split by family).
import re
from .style import ACCENT_2, BG, DANGER, OK, TEXT, TEXT_MUTED
from .style_tokens import MONO_FONT_STACK
from .design_system.palette import parse_color, with_alpha




# ---------------------------------------------------------------------
# Friendly status layer (v2.2): core/ statuses stay precise at the
# source; the GUI translates them for display. The raw technical text
# is always one hover away in the state label's tooltip.
# ---------------------------------------------------------------------

_MISSING_KEY_NOTICE = (
    "To use the cloud brain, add your free Gemini key in Settings. "
    "Or switch her brain to Local — no key, no account."
)


# Watchers pack (v2.3): event kind -> the QSettings suffix of the
# per-kind checkbox that gates it ("watchers/<suffix>"). One Battery
# checkbox covers low/critical/full; Downloads covers finished
# downloads and newly connected drives; Internet covers down/up.
_WATCHER_KIND_TO_SETTING = {
    "battery_low": "battery",
    "battery_critical": "battery",
    "battery_full": "battery",
    "download_done": "downloads",
    "drive_connected": "downloads",
    "network_down": "network",
    "network_up": "network",
    # §14 cluster C: a flapping connection rides the Internet
    # checkbox — it is the same sense, reported separately so
    # the watcher can say "keeps dropping" instead of down/up.
    "network_flap": "network",
    "welcome_back": "welcome_back",
    "disk_low": "disk",
    # §14 cluster C: sustained CPU/memory pressure gets its
    # own checkbox ("watchers/load") — muting the battery
    # notices must not mute a melting machine.
    "system_load": "load",
    # Feels-alive pack (v2.5): calendar heads-ups ride the watcher
    # speech path; their own checkbox gates them ("watchers/calendar").
    "calendar_heads_up": "calendar",
    # NOSE sentinels (v5.20): each has its own Notices checkbox.
    "clipboard": "clipboard",
    "privacy": "privacy",
    "fullscreen": "fullscreen",
    # NOSE Wave 3 (v5.45): the battery foresight heads-up (N9)
    # rides the Battery checkbox; an SSID switch (N5) rides the
    # Internet checkbox (the engine keeps it timeline-only by
    # default regardless — this mapping governs the opt-in
    # telling); hourly light readings (N12) ride their own
    # opt-in toggle.
    "battery_forecast": "battery",
    "network_changed": "network",
    "light": "ambient_light",
}


# NOSE N1 (v5.44): call-app process names for the call-aware
# speech gate. The foreground process NAME (never its title) is
# matched against this set — normalised (lowercased, ".exe"
# stripped) — alongside the privacy watcher's mic holders. Note
# the honest limit: browser-based calls (Meet/Teams/Zoom in a
# browser tab) are out of reach of process names — the browser
# is just the browser — so those are caught only when the
# browser itself shows up as a mic holder in the privacy state.
_CALL_APP_NAMES = frozenset({
    "zoom", "zoom meetings",
    "teams", "ms-teams", "microsoft teams", "msteams",
    "discord", "discordcanary", "discordptb",
    "skype", "skypeapp",
    "slack",
    "whatsapp", "whatsappdesktop",
    "webex", "webexmta", "cisco webex", "ciscocollabhost",
    "gotomeeting", "g2mcomm",
})


# Process names that are VYUK herself — her own mic use (she IS
# the session) must never read as "another app holding the mic".
_OWN_PROCESS_NAMES = frozenset({"vyuk", "python", "pythonw"})


# NOSE N4 (v5.44): watcher kinds that are MATERIAL changes for
# the brain's ambient context — battery level crossings, network
# up/down, the user coming back, a calendar event approaching.
# Anything else (a copy, a drive, a nudge) leaves the packet as-is.
_AMBIENT_MATERIAL_KINDS = frozenset({
    "battery_low", "battery_critical", "battery_full",
    "network_down", "network_up", "network_flap",
    "welcome_back", "calendar_heads_up",
})

def _normalise_process_name(name) -> str:
    """A process name for call-app matching: basename, lowercased,
    trailing ".exe" stripped. "" for anything unusable."""
    try:
        text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
        text = text.strip().lower()
        if text.endswith(".exe"):
            text = text[:-4]
        return text
    except Exception:  # noqa: BLE001
        return ""

def _foreground_process_name() -> str:
    """The foreground window's process name (Windows only), or ""
    when it cannot be determined — off Windows, no foreground
    window, any API failure. Names only, never titles (N13's
    privacy posture): the gate learns "Zoom", never what the
    meeting is called."""
    try:
        import ctypes
        import sys as _sys

        if _sys.platform != "win32":
            return ""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong(0)
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return ""
        if not pid.value:
            return ""
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return ""
            return _normalise_process_name(buffer.value)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — unknown foreground = no signal
        return ""



# Feels-alive pack (v2.5): auto-reconnect backoff, in seconds — four
# attempts, then she stops trying and waits for a manual Connect.
_RECONNECT_DELAYS = (2, 5, 15, 30)

def _is_fatal_error(raw: str) -> bool:
    """True when a session error must never auto-reconnect: a human
    has to fix something first (a bad/missing API key, the local
    brain being offline, a paused setup, a missing model). Anything
    else — a dropped socket, a timeout — is worth quietly retrying."""
    low = (raw or "").lower()
    if any(marker in low for marker in (
        "api key", "api_key", "missing_api_key",
        "ollama", "local brain offline", "setup paused",
        "model_rejected",
        # Fix 5: a quota refusal must never burn the auto-reconnect
        # episode (2/5/15/30 s) against a server-side limit — the
        # human waits (or turns on billing), the app does not retry.
        "live_quota", "quota", "429", "resource_exhausted",
        "resource exhausted", "rate limit", "rate_limit",
    )):
        return True
    return "model" in low and "not" in low

def _friendly_error(raw: str) -> str:
    """Translate a raw session error into first-timer language."""
    low = (raw or "").lower()
    if "local brain offline" in low or "ollama" in low:
        return (
            "Her offline brain isn't running. Open Settings → Brain and press "
            "“Download her on-PC brain”, or switch her brain to Cloud."
        )
    if "setup paused" in low:
        return "Setup paused — it will resume next time you connect."
    if "bidigeneratecontent" in low:
        # Fix 5: the Live door itself refused — the model/key
        # combination cannot do bidiGenerateContent. Named
        # separately from a generic model rejection so the fix
        # (another model, or a key whose project enables Live)
        # is visible instead of "something went wrong". The
        # trigger is the bidi marker alone: a bare "not
        # supported" from another subsystem must not be
        # relabelled as a Live-door refusal.
        return (
            "Google said this model can't do live voice for this key "
            "(not supported for bidiGenerateContent). Try another "
            "voice model, or a key from a project with the Gemini "
            "API enabled — hover here for Google's exact reply."
        )
    if "missing_api_key" in low or "missing api key" in low:
        return _MISSING_KEY_NOTICE
    if "model_rejected" in low:
        # v5.17: two honest causes, matching live_client's wording —
        # a stale model list OR a key that can't use Live models.
        # The old text named only the first and misled exactly the
        # users whose keys were the problem.
        return (
            "Google refused every voice model VYUK tried. Either "
            "this build's model list is out of date, or your API key "
            "can't use Gemini Live models — hover here for Google's "
            "exact reply."
        )
    if "live_quota" in low or "resource_exhausted" in low \
            or "resource exhausted" in low or "quota" in low \
            or "429" in low or "rate limit" in low:
        # Fix 5: a quota refusal is its own cause — it used to fall
        # through to the generic "Something went wrong" line (or
        # read as a bad key), sending users hunting the wrong fix.
        return (
            "Google refused with a quota / rate limit — this key has "
            "hit its Live usage limit for now. Wait a while, or turn "
            "on billing for the key's project in Google AI Studio. "
            "Hover here for Google's exact reply."
        )
    if (
        "api key" in low or "api_key" in low or "401" in low
        or "403" in low or "permission" in low
    ):
        return (
            "That key was rejected by Google — check it in AI Studio "
            "and paste it again."
        )
    if (
        "timed out" in low or "timeout" in low or "connection" in low
        or "network" in low or "unreachable" in low
        or "name or service" in low or "getaddrinfo" in low
        or "ssl" in low or "dns" in low
    ):
        return "VYUK couldn't reach Google — check this PC's internet connection."
    if "kokoro" in low or "voice engine" in low or "voice bank" in low:
        # v5.25: a voice-download failure is not a brain failure —
        # she speaks with the backup voice and the brain still works.
        return (
            "Her premium voice couldn't download — she'll use the "
            "backup voice for now. Her brain still works."
        )
    if "model" in low and ("not found" in low or "no such" in low):
        return (
            "Her brain model isn't downloaded yet. Open Settings → Brain "
            "and press “Download her on-PC brain”."
        )
    return (
        "Something went wrong while connecting. Hover here for the "
        "technical details — or just press Connect to try again."
    )

def _friendly_fallback(reason: str) -> str:
    """v5.17: one plain sentence for the brain proxy's cloud→local
    fallback — why the cloud brain didn't connect, and what she did
    about it (switched to the on-device brain instead of dying)."""
    low = (reason or "").lower()
    if "missing_api_key" in low or "missing api key" in low:
        why = "no API key is saved for the cloud brain"
    elif "model_rejected" in low:
        why = "Google refused the voice models for that key"
    elif "api key" in low or "401" in low or "403" in low or "permission" in low:
        why = "Google rejected that API key"
    elif any(w in low for w in (
            "timed out", "timeout", "network", "connection",
            "unreachable", "dns", "ssl", "websocket")):
        why = "the cloud couldn't be reached from this PC"
    elif reason:
        why = f"the cloud brain didn't connect ({reason})"
    else:
        why = "the cloud brain didn't connect"
    return (
        f"The cloud brain didn't connect — {why}. I've switched to the "
        "on-device brain so you can keep talking. Settings → Brain has "
        "the details if you'd like to fix the cloud connection."
    )

def _friendly_setup(line: str) -> str:
    """Translate a raw first-run setup/download line into plain words."""
    low = (line or "").lower()
    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", line or "")
    pct = f" — {pct_match.group(1)}%" if pct_match else "…"
    fetching = "download" in low or "resum" in low
    if (
        "downloading brain model" in low
        or "downloading local brain" in low
        or ".gguf" in low
        or (fetching and "brain model" in low)
    ):
        return f"Downloading her brain{pct}"
    if fetching and ("voice" in low or "kokoro" in low):
        return f"Downloading her voice{pct}"
    if "whisper" in low or "ears" in low:
        return "Setting up her ears… (first time only)"
    if "ready" in low or "loading" in low:
        return "Warming up…"
    return "Setting up VYUK on this PC…"

def _md_lite(text: str) -> str:
    """Markdown-lite for chat bubbles (SENSES v5.16, E18).

    The text is HTML-escaped BEFORE any markup is applied, so nothing
    a model, a web page, or a pasted page produced can inject tags —
    the only tags in the output are inserted here. Supports **bold**,
    *italic*, `code`, bullet lines ("- " / "• "), headings ("# ",
    "## ", "### " — rendered as a bold line of their own, same size:
    the transcript stays one quiet size), and numbered lists
    ("1. " — the number stays, indented with the bullets). Links
    are deliberately NOT made clickable.
    """
    import html as _html
    import re as _re

    # quote=True matters (Phase 2, chatbox #14): linkified URLs
    # land inside an href attribute below, so a raw " in the text
    # must never survive escaping — it would close the attribute.
    esc = _html.escape(text or "", quote=True)
    # Phase 2 (chatbox #14): bare URLs become clickable links. This
    # runs on the ESCAPED text before any tags of our own exist, so
    # a URL can never break out of its href — quotes in it are
    # already &quot;. Trailing sentence punctuation stays outside.
    def _link(m):
        url = m.group(0)
        trail = ""
        while url and url[-1] in ".,!?":
            trail = url[-1] + trail
            url = url[:-1]
        if url.endswith(")") and "(" not in url:
            trail = ")" + trail
            url = url[:-1]
        return f'<a href="{url}">{url}</a>{trail}'

    esc = _re.sub(r"https?://[^\s<]+", _link, esc)
    # Emphasis content must hug its markers (start/end non-space), so
    # arithmetic like "2 * 3 * 4" never turns into italics.
    esc = _re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"<b>\1</b>", esc)
    esc = _re.sub(r"(?<!\*)\*(\S(?:[^*\n]*?\S)?)\*(?!\*)", r"<i>\1</i>", esc)
    esc = _re.sub(
        r"`([^`\n]+)`",
        # B-R1-7: inline code renders in the mono stack (Share Tech
        # Mono first), never a hardcoded Consolas — the span must
        # name the same face the code surfaces use.
        r'<span style="font-family:' + MONO_FONT_STACK + r';'
        f'background-color:{with_alpha(TEXT, 20)}">'
        r'\1</span>',
        esc)
    lines = []
    for ln in esc.split("\n"):
        s = ln.lstrip()
        if s.startswith("- ") or s.startswith("• "):
            lines.append("&nbsp;&nbsp;• " + s[2:])
        elif s.startswith("#"):
            # Heading: strip the markers, bold the whole line.
            # (A "#" that is not a heading — mid-line, or "#tag"
            # with no space — falls through untouched below.)
            _head = s.lstrip("#")
            if _head.startswith(" ") and _head.strip():
                lines.append("<b>" + _head[1:] + "</b>")
            else:
                lines.append(ln)
        elif _re.match(r"\d+\.\s", s):
            # Numbered list: the number is the structure, keep
            # it, indented to line up with the bullets.
            lines.append("&nbsp;&nbsp;" + s)
        else:
            lines.append(ln)
    return "<br/>".join(lines)

def strip_markdown_plain(text: str) -> str:
    """The message as plain text — for Copy as plain text (chat
    UI, 2026-11-16). Markers come off, words stay: **bold** and
    *italic* lose only their markers, `code` keeps its text,
    headings lose their #'s, bullets read "• ". A literal "*"
    that is not a marker (arithmetic "2 * 3") survives — the
    same hug-its-markers rule _md_lite uses. Nothing is
    re-flowed: line breaks and list numbers stay as written."""
    import re as _re

    out = []
    for ln in str(text or "").split("\n"):
        s = ln.lstrip()
        if s.startswith("- ") or s.startswith("• "):
            s = "• " + s[2:]
        elif s.startswith("#"):
            _head = s.lstrip("#")
            if _head.startswith(" ") and _head.strip():
                s = _head[1:]
        out.append(s)
    text = "\n".join(out)
    text = _re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", text)
    text = _re.sub(r"(?<!\*)\*(\S(?:[^*\n]*?\S)?)\*(?!\*)", r"\1", text)
    text = _re.sub(r"`([^`\n]+)`", r"\1", text)
    return text

def _rendered_plain(text: str) -> str:
    """The text as a bubble actually shows it — markdown-lite
    markers removed — so width can be measured on the rendered
    form instead of the raw source (Phase 2, chatbox #5).
    Headings lose their #'s, numbered lists keep their numbers
    (they are content, not markers)."""
    out = []
    for ln in str(text or "").split("\n"):
        s = ln.lstrip()
        if s.startswith("- ") or s.startswith("• "):
            s = "• " + s[2:]
        elif s.startswith("#"):
            _head = s.lstrip("#")
            if _head.startswith(" ") and _head.strip():
                s = _head[1:]
        out.append(s.replace("**", "").replace("`", "").replace("*", ""))
    return "\n".join(out)

def _title_case(title: str) -> str:
    """Title-case a SHOUTED multi-word card title (Phase 2,
    chatbox #15); short all-caps words (IP, DNS, CPU) stay as-is,
    and anything not fully upper-case is returned untouched."""
    t = str(title or "")
    if " " not in t or t != t.upper():
        return t
    return " ".join(
        w if (len(w) <= 3 and w.isupper()) else w.capitalize()
        for w in t.split(" "))

def _split_segments(text: str):
    """Split a message into ("prose", str) / ("code", str) segments
    on ``` fences (Phase 2, chatbox #14). An unclosed fence treats
    the rest of the message as code — the honest rendering of
    what was written."""
    parts = str(text or "").split("```")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            code = part
            if "\n" in code:
                first, rest = code.split("\n", 1)
                # A language tag on the fence line is not code.
                if first.strip() and " " not in first.strip() \
                        and len(first.strip()) <= 12:
                    code = rest
            out.append(("code", code.strip("\n")))
        elif part.strip():
            out.append(("prose", part.strip("\n")))
    return out or [("prose", str(text or ""))]

def _summarise_tool(description: str) -> str:
    """First sentence of a description, capped at 95 chars +
    ellipsis — mirrors skills_catalog._summarise, so the palette
    and the Skills catalogue show the same words for a tool.
    (A local copy: skills_catalog already imports window_widgets,
    so importing it here would cycle. _humanise is duplicated
    across the two files the same way.)"""
    text = " ".join((description or "").split())
    if not text:
        return ""
    sentence = text
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        # Abbreviations ("e.g.", "i.e.", "Dr.") are not sentence ends.
        before = re.search(r"([A-Za-z]+)$", text[: match.start()])
        if before and (len(before.group(1)) == 1
                       or before.group(1).lower() in
                       {"eg", "ie", "etc", "vs", "dr", "mr", "mrs", "st"}):
            continue
        sentence = text[: match.end()]
        break
    sentence = sentence.rstrip()
    if sentence.endswith("."):
        sentence = sentence[:-1].rstrip()
    if len(sentence) > 95:
        sentence = sentence[:94].rstrip() + "…"
    return sentence

def _humanise(name: str) -> str:
    """Friendly-name fallback for a tool missing from the map.

    Audit fix batch 1: _build_commands called this but it was defined
    only in gui/skills_catalog.py, so the first tool without a friendly
    name raised NameError inside the broad "tools rows are optional"
    except and silently aborted the remaining tool rows. Copied here
    (same body as skills_catalog._humanise) rather than imported:
    skills_catalog already imports window_widgets, so importing it
    here would cycle — the same reason _summarise_tool above is a
    local copy.
    """
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name



# GAPS-15: the roster is voice data — it lives in core/languages.py
# now; this module re-exports it from the facade so the GUI files
# that import it from here keep working unchanged.
try:
    from voice_assistant.core.gui_facade import _KOKORO_VOICE_ROSTER
except ImportError:  # tree without the package shim
    from core.gui_facade import _KOKORO_VOICE_ROSTER
