"""Tokens-search family split out of core/funnel/toolbox.py (B36 part 1 of 2):
tokenizer/stemmer, synonym expansion, keyword scoring, core-tool priority,
plus the negative-pair helpers keyword_scores needs. Pure functions; the
embedding, declaration, activity, and dispatcher families stay in the facade
until B37."""

from __future__ import annotations

import re
from typing import Any


_TOKEN = re.compile(r"[a-z0-9]+")


#: The protocol the model is taught, appended to the system
#: instruction of a routed session.
#: Router-consistency batch (wiring audit WIR-11): the old text
#: described a use_toolbox-only world ("Every other tool is
#: reached through use_toolbox") while the route_* domain
#: routers are live. This version names the real surface:
#: core tools direct, domain routers for everything else,
#: use_toolbox offered directly for discovery.
ROUTER_PROMPT = (
    "TOOL ROUTER: this session loads only the core tools "
    "directly. Every other tool is reached through its domain "
    "router: call route_<domain> with tool set to the tool's "
    "name and args set to that tool's arguments. use_toolbox "
    "is also offered directly in this session for discovery "
    "when you do not know the domain or the tool: call it with "
    "action 'find' and a few words of what you want to do, "
    "then action 'describe' for the chosen tool's exact "
    "arguments if you need them, then action 'call' with the "
    "tool name and its arguments — or call the router directly "
    "when you already know the tool. Both paths run the real "
    "tool with the same confirmations, so a refusal either "
    "returns is final — report it, never retry around it. "
    # v5.50 (programme item 45): the two round-economy shortcuts.
    "If a find result calls its top match a confident match, "
    "that tool's full declaration is already in the result — "
    "call it directly, no describe round needed. If a call "
    "fails argument validation, the result includes the tool's "
    "declaration — correct the arguments to match it and "
    "retry once."
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def stem(token: str) -> str:
    """Conservative English stem: lowercases, then strips ONE
    derivational suffix (longest first) when the remaining stem
    keeps >= 3 chars, collapsing a doubled final letter left by
    -ing/-ed (running -> runn -> run). Also strips the
    comparative -er (louder -> loud) so synonym lookup sees the
    base word. Deliberately shallow — it exists to align
    inflections, not to be Porter. Applied to BOTH the query
    and the tool side, so consistent over-stemming is harmless."""
    t = (token or "").lower()
    for suffix in ("ingly", "edly", "ing", "ed", "ies", "es", "er", "s"):
        if len(t) > len(suffix) + 2 and t.endswith(suffix):
            if suffix == "ies":
                return t[: -len(suffix)] + "y"
            base = t[: -len(suffix)]
            if suffix in ("ing", "ed") and len(base) >= 2 \
                    and base[-1] == base[-2]:
                base = base[:-1]
            return base
    return t


def _stemmed(text: str) -> set[str]:
    return {stem(t) for t in _tokens(text)}


#: Natural phrasing -> tool vocabulary. Applied to the QUERY
#: side (expansion) so a user's word reaches tools described in
#: other words. Values are matched stemmed, like everything else.
WORD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "photo": ("picture", "image", "screenshot"),
    "picture": ("photo", "image", "screenshot"),
    "snap": ("screenshot", "capture", "photo"),
    "capture": ("screenshot", "record"),
    "shut": ("close", "power", "turn off"),
    "kill": ("close", "terminate", "end process"),
    "remove": ("delete", "erase"),
    "erase": ("delete", "remove"),
    "trash": ("delete", "recycle"),
    "find": ("search", "look up", "locate"),
    "search": ("find", "look up"),
    "locate": ("find", "search"),
    "open": ("launch", "start", "run"),
    "launch": ("open", "start", "run"),
    "start": ("open", "launch", "run"),
    "play": ("media", "music", "song"),
    "song": ("music", "play", "track"),
    "tune": ("music", "song"),
    "loud": ("volume", "sound", "audio"),
    "quiet": ("volume", "mute", "sound"),
    "sound": ("volume", "audio"),
    "bright": ("brightness", "display", "screen"),
    "dim": ("brightness", "display"),
    "mail": ("email", "inbox", "gmail"),
    "email": ("mail", "inbox", "gmail"),
    "inbox": ("email", "mail", "unread"),
    "note": ("notes", "remember", "write"),
    "remind": ("reminder", "alarm"),
    "reminder": ("remind", "alarm"),
    "alarm": ("reminder", "timer", "clock"),
    "timer": ("alarm", "countdown", "stopwatch"),
    "web": ("browser", "internet", "url", "website"),
    "website": ("web", "url", "browser", "page"),
    "page": ("website", "browser", "url"),
    "download": ("fetch", "save", "get"),
    "file": ("files", "folder", "document"),
    "folder": ("directory", "file", "path"),
    "document": ("file", "text", "doc"),
    "copy": ("clipboard", "duplicate"),
    "paste": ("clipboard", "insert"),
    "clipboard": ("copy", "paste"),
    "battery": ("power", "charge"),
    "charge": ("battery", "power"),
    "wifi": ("network", "wireless", "internet"),
    "internet": ("network", "wifi", "connection"),
    "network": ("wifi", "internet", "connection"),
    "weather": ("forecast", "temperature", "rain"),
    "forecast": ("weather",),
    "rain": ("weather", "forecast"),
    "time": ("clock", "date"),
    "clock": ("time", "date"),
    "date": ("time", "day", "calendar"),
    "calendar": ("schedule", "events", "day"),
    "schedule": ("calendar", "events", "agenda"),
    "meeting": ("calendar", "schedule", "event"),
    "music": ("song", "play", "spotify", "track"),
    "video": ("media", "play", "youtube"),
    "lock": ("secure", "screen"),
    "sleep": ("power", "suspend"),
    "restart": ("reboot", "power"),
    "reboot": ("restart", "power"),
    "shutdown": ("power", "turn off"),
    "calculate": ("math", "calculator", "compute"),
    "math": ("calculate", "calculator"),
    "convert": ("conversion", "units"),
    "translate": ("language", "translation"),
    "speak": ("say", "voice", "tts", "read aloud"),
    "say": ("speak", "voice"),
    "read": ("open", "view", "speak"),
    "write": ("type", "create", "note"),
    "type": ("write", "keyboard", "text"),
    "text": ("type", "write", "message"),
    "message": ("text", "send", "chat"),
    "send": ("message", "email", "share"),
    "price": ("cost", "quote", "stock", "crypto"),
    "stock": ("price", "quote", "share", "market"),
    "crypto": ("price", "bitcoin", "quote"),
    "news": ("headlines", "current events"),
    "screenshot": ("screen", "capture", "photo", "image"),
    "screen": ("display", "monitor", "screenshot"),
    "display": ("screen", "monitor", "brightness"),
    "monitor": ("screen", "display"),
    "window": ("app", "application"),
    "app": ("application", "program"),
    "program": ("app", "application"),
    "volume": ("sound", "audio", "loud"),
    "mute": ("volume", "silence", "sound"),
    "camera": ("webcam", "photo"),
    "webcam": ("camera",),
    "print": ("printer",),
    "scan": ("ocr", "read", "image"),
    "ocr": ("scan", "text", "image"),
}


def _expand(tokens: set[str]) -> set[str]:
    """Query tokens plus their synonym tokens (all stemmed)."""
    out = set(tokens)
    for tok in list(tokens):
        for phrase in WORD_SYNONYMS.get(tok, ()):
            out |= _stemmed(phrase)
    return out


#: Per-tool synonym phrases (B6a): extra natural phrasing a tool
#: answers to, beyond its name + description. Data-driven — add
#: a row, the ranker and the local promotion scorer both see it.
TOOL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "take_screenshot": ("snap my screen", "capture the display", "show me my screen", "screen grab"),
    "launch_application": ("open an app", "start a program", "run an application"),
    "close_application": ("shut the app", "kill the program", "quit",
                          "close chrome", "shut down chrome",
                          "close the app"),
    "get_current_time_date": ("what time is it", "tell me the time", "current clock"),
    "world_clock": ("time in another city", "time zone",
                    "what time is it in", "time in tokyo",
                    "time in london", "time in new york"),
    "get_weather": ("will it rain", "forecast today", "temperature outside"),
    "battery_info": ("how much charge", "battery level", "power left"),
    "adjust_volume": ("louder", "quieter", "turn the sound up", "volume down"),
    "set_volume_level": ("set the loudness", "volume to a level"),
    "media_control": ("pause the music", "next song", "play pause",
                      "resume the music", "resume music",
                      "unpause the music", "resume playback"),
    # v5.52 (item 4): starting music had NO tool before this.
    "play_music": ("play music", "play some music", "play a song",
                   "put on some music", "play some jazz",
                   "play despacito", "start music"),
    "set_brightness": ("dim the screen", "brighter display"),
    "web_search": ("google this", "look it up online", "search the internet"),
    "open_url": ("visit a website", "go to a page", "open a link"),
    "remember_user_fact": ("remember this", "don't forget", "save this fact"),
    "recall_memories": ("what do you remember", "what do you know about me"),
    "set_reminder": ("remind me", "set an alarm for"),
    "read_clipboard": ("what did i copy", "clipboard contents"),
    "write_clipboard": ("copy this text", "put this on the clipboard"),
    "window_control": ("move the window", "move this window",
                       "move window", "maximize", "minimize"),
    "get_active_window": ("what app is open", "current window"),
    "type_text": ("type this out", "write these words", "keyboard input"),
    "press_hotkey": ("keyboard shortcut", "key combination"),
    "mouse_control": ("move the mouse", "click"),
    "get_system_info": ("computer specs", "how is my pc", "system status"),
    # Audit fix Batch 2 (ROUTE-01): ocr_file and set_resolution
    # had no synonym rows, so their exact capability phrases
    # ("extract text from an image", "change display to …")
    # ranked below generic tools sharing one stem. Their own
    # descriptions state these capabilities verbatim.
    "ocr_file": ("extract text from an image", "read text from an image",
                 "ocr an image", "text in a photo"),
    "set_resolution": ("change the resolution", "display resolution",
                       "set display resolution", "screen resolution"),
    # v5.52 (item 8): her own settings live in chat now.
    "app_settings": ("change the theme", "change the color",
                     "change the colour", "change color",
                     "new theme", "change your voice",
                     "make your voice charon",
                     "switch to hindi", "change the orb",
                     "your settings", "vyuk settings"),
    "lock_screen": ("lock my pc", "secure the computer"),
    "power_control": ("shutdown", "restart the computer", "turn off the pc", "reboot"),
    "wifi_info": ("which wifi", "network details", "internet connection"),
    "get_todays_schedule": ("my day today", "meetings today", "agenda"),
    # Round-3 fix Q6: the briefing tool owns the morning-briefing
    # ask ("give me my morning briefing", "what's on today") — the
    # bare schedule ask ("my day today") stays with
    # get_todays_schedule, so the two never fight over one phrase.
    "get_morning_briefing": ("morning briefing", "what's on today",
                            "my briefing today", "brief me"),
    "get_upcoming_events": ("what's coming up", "future meetings"),
    # Stage 0 features batch 2 (Group E, 2026-11-22): the mail
    # and calendar actions — ROUTE-01 precedent, one row each.
    "find_free_slots": ("find a free slot", "when am I free",
                        "free time today", "find me time for a meeting"),
    "snooze_email": ("snooze this email", "remind me about this email",
                     "snooze that mail", "bring that email back later"),
    "cancel_calendar_event": ("cancel that meeting", "delete that event",
                              "cancel my meeting", "remove that event"),
    "reschedule_calendar_event": ("move that meeting", "reschedule that event",
                                  "push that meeting", "move my meeting"),
    # Stage 0 features batch 3 (Group F, 2026-11-22): the money
    # actions — ROUTE-01 precedent, one row each.
    "check_money_alerts": ("check my money alerts", "any money alerts",
                           "am I over budget", "what's renewing soon"),
    "portfolio_tracker": ("my portfolio", "what's my portfolio worth",
                          "my holdings", "what do I hold"),
    # Stage 0 features batch 4 (Group G, 2026-11-22): notes,
    # lists, goals and voice memos — ROUTE-01 precedent, one row
    # each.
    "notes": ("take a note", "show me my notes", "read my notes",
              "what did I note down"),
    "wishlist": ("my wishlist", "what's on my wishlist",
                 "add that to my wishlist"),
    "watchlist": ("my watchlist", "what should I watch",
                  "add that to my watchlist", "what have I watched"),
    "goal_tracker": ("my goals", "how is my goal going",
                     "track my goal", "what are my goals"),
    "voice_memos": ("my voice memos", "play my voice memo",
                    "what voice memos do I have", "play that memo"),
    "get_unread_email": ("new mail", "unread messages", "check my inbox"),
    "search_email": ("find an email", "look through my mail"),
    "find_files": ("locate a file", "search my documents"),
    "file_control": ("delete a file", "move a file", "rename"),
    "quick_math": ("calculate", "what is the sum", "arithmetic"),
    # v5.52 (item 5): "search youtube for …" is a browser-driving
    # request (open the site, use its search) — web_search kept
    # outranking browser_control for it, which is how the model
    # ended up chaining open_url into a session-less search.
    "browser_control": ("control the browser", "web automation",
                        "search youtube", "search on youtube",
                        "search youtube for",
                        "open youtube and search for a video",
                        "watch a video on youtube"),
    "speak_text": ("say this aloud", "read this out"),
    "create_plan": ("make a plan", "plan my day", "steps to"),
    "run_routine": ("run my routine", "do the routine"),
    # Round-3 fix #4 (T12): the plan runner — named distinctly
    # from run_routine so a plain "run my routine" still routes
    # to the planner's tool, and from create_plan so "make a
    # plan" still creates one instead of running it.
    "run_plan": ("run my plan", "run a plan"),
    # Field-check fix #32 (round-2 N5): the anticipation
    # routine runner — named distinctly from run_routine so a
    # plain "run my routine" still routes to the planner's
    # tool.
    "run_anticipation_routine": ("run my anticipation routine",
                                "run an anticipation routine"),
    # Fix #32 follow-up (2026-11-19): the three teach tools
    # had no synonym rows, so when fix #32 added
    # run_anticipation_routine (24 points for "teach me a
    # routine") the list/replay tools fell out of the top 8
    # for teach phrases — teach_list_sequences scored only
    # on its name (12) and teach_replay_sequence on name +
    # one description token (14). Same mechanism as the
    # ROUTE-01 precedent above (ocr_file / set_resolution):
    # a tool's own capability phrases belong in its synonym
    # row. "The routine I taught" is the list phrase and
    # "replay the routine I taught" the replay phrase; both
    # keep their generic rivals first — "list my routines"
    # still routes to list_routines (28 > 14) and "replay
    # my routine" still routes to run_routine. (Singular
    # "routine", not "routines": this stemmer maps the
    # plural to "routin" and the singular to "routine",
    # so the plural form would miss the singular query
    # token entirely — and would over-match "list my
    # routines" instead.)
    "teach_list_sequences": ("the routine I taught",
                            "show my taught sequences"),
    "teach_replay_sequence": ("replay the routine I taught",),
    "speed_test": ("how fast is my internet", "check connection speed"),
    "public_ip": ("my ip address", "what is my ip"),
    "open_path": ("open this folder", "open the file"),
    "unit_convert": ("convert units", "how many in"),
    "date_calculator": ("days between dates", "date difference",
                        "how many days until", "days until",
                        "days from today"),
    "get_crypto_price": ("bitcoin price", "crypto cost"),
    "get_stock_quote": ("share price", "stock cost", "market quote"),
    "use_toolbox": ("find a tool",),
}


def tool_search_text(decl: dict) -> str:
    """The text a tool is ranked/embedded by: name words +
    description + its TOOL_SYNONYMS phrases."""
    name = decl.get("name", "")
    parts = [name.replace("_", " "), decl.get("description", "")]
    parts.extend(TOOL_SYNONYMS.get(name, ()))
    return " ".join(p for p in parts if p)


def keyword_scores(registry: Any, query: str) -> dict[str, int]:
    """B6(a) scores for every tool with a non-zero score:
    stemmed + synonym-expanded token overlap. Exact tool-name
    match wins outright; name tokens weigh 12, synonym-phrase
    tokens 8, description tokens 2 (the pre-B6 weights)."""
    q_raw = _tokens(query)
    if not q_raw:
        return {}
    q = _expand(_stemmed(query))
    # Audit fix Batch 2 (ROUTE-01): the negative-pair table
    # (below) fires BEFORE scoring, so a known confusable
    # tool is pushed to the bottom of this ranking instead of
    # winning it on a shared stem ("convert" must not hand
    # document conversion to unit_convert). An exact-name
    # query is exempt: naming a tool is intent, not confusion.
    forbidden, _reasons = _negative_forbidden(query)
    exact_name = (query or "").strip().lower()
    scores: dict[str, int] = {}
    for decl in registry.get_declarations():
        name = decl["name"]
        if name == "use_toolbox":
            continue
        score = 0
        if exact_name == name:
            score += 100
        score += 12 * len(q & _stemmed(name.replace("_", " ")))
        syn_text = " ".join(TOOL_SYNONYMS.get(name, ()))
        if syn_text:
            score += 8 * len(q & _stemmed(syn_text))
        score += 2 * len(q & _stemmed(decl.get("description", "")))
        if name in forbidden and exact_name != name:
            score -= NEGATIVE_PAIR_PENALTY
        if score:
            scores[name] = score
    return scores


#: The resident core set: the tools a routed session loads
#: directly (the same set the local brain leads with). The
#: canonical list lives HERE, in this dependency-light
#: module, because the router must never be hostage to the
#: local brain's import chain — v5.33's first live proof
#: found sessions silently unrouting when local_client's
#: numpy import failed in a minimal environment.
#: core/voice/local_client.py imports this tuple as its own
#: CORE_TOOL_PRIORITY, so there is still exactly one list.
CORE_TOOL_PRIORITY: tuple[str, ...] = (
    "get_current_time_date", "world_clock", "get_weather",
    "battery_info", "adjust_volume", "set_volume_level",
    "media_control", "set_brightness", "take_screenshot",
    "launch_application", "close_application", "web_search",
    "open_url", "remember_user_fact", "recall_memories",
    "recall_day", "remember_today", "set_reminder",
    "read_clipboard", "write_clipboard", "window_control",
    "get_active_window", "type_text", "press_hotkey",
    "mouse_control", "get_system_info", "app_settings", "lock_screen",
    "power_control", "network_control", "wifi_info",
    "get_todays_schedule", "get_morning_briefing", "get_upcoming_events",
    # Stage 0 features batch 2 (Group E): find_free_slots is a
    # read, so it joins the assistant profile — the effectful
    # three (snooze/cancel/reschedule) do not, the
    # send_email/create_calendar_event precedent.
    "find_free_slots",
    "get_unread_email",
    "search_email", "find_files", "file_control", "quick_math",
    "browser_control", "browser_read", "browser_click",
    "browser_type", "speak_text", "create_plan", "list_plans",
    "run_routine", "run_anticipation_routine", "run_plan", "list_routines", "keep_awake", "set_focus_assist",
    "get_activity_log", "date_calculator", "unit_convert", "speed_test",
    "public_ip", "notify_user", "open_path", "focus_session",
)


def core_tool_names() -> tuple[str, ...]:
    """The resident set (see CORE_TOOL_PRIORITY above)."""
    return CORE_TOOL_PRIORITY


def rank_tools_keyword(registry: Any, query: str, limit: int = 8) -> list[str]:
    """B6(a)-only ranking: stemmed, synonym-expanded token
    overlap. Deterministic; ties keep registry order."""
    scores = keyword_scores(registry, query)
    if not scores:
        return []
    order = {d["name"]: i for i, d in enumerate(registry.get_declarations())}
    ranked = sorted(scores, key=lambda n: (-scores[n], order.get(n, 0)))
    return ranked[:limit]


#: Penalty applied to a negative-pair tool's keyword score.
#: Keyword scores top out near 100 (exact-name bonus) and the
#: embedding blend adds at most 100, so 1000 dominates both.
NEGATIVE_PAIR_PENALTY = 1000


#: Each entry: (groups, forbidden, reason, unsupported_message).
#: groups is a tuple of prefix-tuples; the entry fires only
#: when EVERY group has at least one query word starting with
#: one of its prefixes, or a listed phrase appears verbatim.
#: "phrases" and "exclude_phrases" are raw lowercased substrings.
NEGATIVE_PAIRS: tuple[dict[str, Any], ...] = (
    {
        "id": "translate-vs-type",
        "groups": (("translat",),),
        "forbidden": ("type_text", "uia_type_text", "browser_type"),
        "reason": "typing text is not translating it",
        "unsupported": ("No tool in the catalogue translates "
                        "between languages. (morse_translate converts "
                        "text to Morse code only; it is not language "
                        "translation.)"),
        "exclude_phrases": ("morse",),
    },
    {
        "id": "document-conversion-vs-unit",
        "groups": (("convert",),
                    ("pdf", "word", "docx", "document", "ppt",
                     "powerpoint", "spreadsheet", "excel", "format")),
        "forbidden": ("unit_convert", "base_convert", "case_convert",
                      "convert_image", "timezone_convert",
                      "roman_convert", "epoch_convert", "color_convert",
                      "oven_temp_convert", "hourly_rate_convert",
                      "file_control"),
        "reason": ("unit/base/case conversion — and generic file "
                   "control — is not document conversion"),
        "unsupported": ("No tool in the catalogue converts document "
                        "formats (PDF to Word, Word to PDF, "
                        "presentations, spreadsheets). read_pdf_text "
                        "reads a PDF's text layer; it does not "
                        "rewrite it in another format."),
    },
    {
        "id": "screen-recording-vs-screenshot",
        "groups": (("record",), ("screen", "video", "display")),
        "forbidden": ("take_screenshot", "screenshot_region",
                      "capture_window", "ocr_screen"),
        "reason": "a still screenshot is not a video recording",
        "unsupported": ("No tool in the catalogue records video of "
                        "the screen. take_screenshot captures one "
                        "still image only."),
    },
    {
        "id": "monitor-capture-vs-screenshot",
        "groups": (("screenshot", "capture", "snap", "photo"),
                    ("second", "other", "external", "dual")),
        "forbidden": ("take_screenshot", "screenshot_region",
                      "capture_window"),
        "reason": "no capture tool targets a monitor by number",
        "unsupported": ("No tool in the catalogue captures a specific "
                        "monitor by number. take_screenshot captures "
                        "the screen; screenshot_region captures a "
                        "rectangle you give coordinates for."),
    },
    {
        "id": "webpage-reading-vs-open-path",
        "groups": (("read", "open", "view", "show"),
                    ("webpage", "website")),
        "phrases": ("web page",),
        "forbidden": ("open_path",),
        "reason": "opening a local file/folder is not reading a web page",
        "unsupported": None,
    },
    {
        "id": "transcription-vs-audio-edit",
        "groups": (("transcrib",),),
        "forbidden": ("audio_fade", "audio_trim",
                      "audio_speed_change", "record_audio",
                      "list_audio_devices"),
        "reason": "editing or recording audio is not transcribing it",
        "unsupported": ("No tool in the catalogue transcribes audio "
                        "to text. record_audio records; it does not "
                        "transcribe."),
    },
    {
        "id": "email-summary-vs-mail-read",
        "groups": (("summar",), ("email", "mail", "inbox", "thread")),
        "forbidden": ("get_unread_email", "search_email", "send_email"),
        "reason": "reading/searching mail is not summarizing it",
        "unsupported": ("No tool in the catalogue summarizes an email "
                        "thread. search_email and get_unread_email "
                        "only read and search mail."),
    },
    {
        "id": "presentation-spreadsheet-creation",
        "groups": (("make", "create", "build"),
                    ("powerpoint", "presentation", "spreadsheet",
                     "excel")),
        "forbidden": ("create_plan", "make_sprite_sheet",
                      "make_collage", "make_wallpaper"),
        "reason": "a plan or an image is not a presentation/spreadsheet",
        "unsupported": ("No tool in the catalogue creates PowerPoint "
                        "presentations or spreadsheets."),
    },
    {
        "id": "lights-actuation-vs-pc-power",
        "groups": (("turn", "switch", "light"),
                    ("light", "lamp", "bedroom")),
        "forbidden": ("power_control",),
        "reason": "this computer's power is not a room light",
        "unsupported": ("No tool in the catalogue switches lights or "
                        "home devices on or off. The iot_* tools "
                        "describe caller-supplied device labels only; "
                        "power_control is this computer's power."),
    },
    {
        "id": "file-version-recovery",
        "groups": (("recover", "restore"),
                    ("version", "previous", "deleted")),
        "forbidden": ("find_files", "open_path", "file_control"),
        "reason": "finding/opening a file is not recovering its old version",
        "unsupported": ("No tool in the catalogue recovers a previous "
                        "version of a file."),
    },
    {
        # file_control operates on files (delete/move/rename);
        # it neither reads a document for summary nor converts
        # one, so a summarization query must not rank it as the
        # answer. (Read tools stay ranked: reading is a step.)
        "id": "document-summary-vs-file-control",
        "groups": (("summar",), ("document", "file", "pdf")),
        "forbidden": ("file_control",),
        "reason": "operating on a file is not summarizing it",
        "unsupported": None,
    },
    {
        # battery_info's own description draws this line: it
        # reports charge level and time left, and says "For
        # battery wear, lost capacity and charge cycles, use
        # battery_health." A health question must not inline
        # the charge tool as if it answered it.
        "id": "battery-health-vs-charge",
        "groups": (("health", "wear", "cycle"),
                    ("batter",)),
        "forbidden": ("battery_info",),
        "reason": "charge level is not battery health",
        "unsupported": None,
    },
)


def _negative_forbidden(query: str) -> tuple[set[str], list[str]]:
    """(forbidden tool names, reasons) for this query's signals.

    A group hits when any query word starts with any of the
    group's prefixes; a phrase entry hits on a raw substring.
    An entry fires only when every group hits (or a phrase
    hits) and none of its exclude phrases appear. Pure: no
    registry access, no model, no I/O — safe for the rig to
    call on every gold case.
    """
    raw = (query or "").lower()
    words = _tokens(query)
    forbidden: set[str] = set()
    reasons: list[str] = []
    for entry in NEGATIVE_PAIRS:
        if any(p in raw for p in entry.get("exclude_phrases", ())):
            continue
        groups = entry.get("groups", ())
        groups_hit = all(
            any(w.startswith(prefix) for w in words for prefix in group)
            for group in groups)
        phrase_hit = any(p in raw for p in entry.get("phrases", ()))
        if groups_hit or phrase_hit:
            forbidden.update(entry["forbidden"])
            reasons.append(entry["id"])
    return forbidden, reasons
