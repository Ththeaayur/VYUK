"""
VYUK's own settings, changeable from chat (v5.52, item 8).

The user (2026-10-23): "allow it to control settings too — if
I want to change the color, or change the voice, etc., I
should be able to do that in chat itself." Until this module,
NONE of the registry's tools mutated the app's own settings;
theme, orb, voices and language were GUI-only.

One tool, `app_settings` (action get/set), over an explicit
ALLOWLIST of cosmetic/behavioural settings. Every write goes
through the SAME store the GUI writes — read-modify-write at
write time, so chat and GUI can never diverge or clobber each
other:
  theme / orb / earcons / suggestions — QSettings("NovaAI",
    "Nova"), the window's own settings object keys ("theme",
    "orb_style" + "orb_style_chosen", "sounds_enabled",
    "suggestions/enabled");
  live_voice / local_voice / language / tts_loudness /
    voice_auto_switch — the config object + save_env_setting
    (.env), the persistence gui/window_brain.py's
    _persist_config_setting uses; language goes through
    core/languages.set_preferred_language, the ONE write path
    onboarding and Settings share (it moves the curated home
    voice with the language).

Apply semantics — verified against the consuming code, and
worded that way in every result:
  live_voice: core/voice/live_client.py builds the session config from
    config.voice_name AT CONNECT -> next Live connect.
  local_voice / tts_loudness / voice_auto_switch: the speech
    path reads the config fields PER UTTERANCE
    (core/voice/local_client.py) -> next utterance.
  language: reply_language_directive() is appended per turn
    by the local brains and at prompt build by cloud/Live ->
    next reply (local) / next connect (cloud, Live).
  theme / appearance/*: the window applies these LIVE. Writing one
    emits ``SettingsChanged`` on the state bus (see ``_qs_set``)
    and the main window subscribes to it, calling
    ``_apply_appearance()`` — a chat-set theme applies at once,
    not on the next build (GAPS-28 change bus, G-V3).
  orb / earcons / suggestions: the window consumes these when it
    BUILDS (gui/window_shell __init__: _apply_appearance +
    _apply_saved_settings; the earcons engine is constructed from
    sounds_enabled there). There is no change bus for them — so
    the honest words are "saved; the window picks it up when it
    next builds (restart)".

HARD EXCLUSIONS (the prompt-injection firewall for
self-modification): nothing security-shaped is settable here —
confirmation policy, the failsafe/panic posture, taint /
injection settings, protected paths, the session provenance
lock, the real-Chrome attach opt-in, screen scope, update
keys, API keys / tokens / passwords. Asking for one returns a
refusal naming that it is Settings-page-only by design. The
allowlist is the whole defence: a key that is not listed
cannot be written, whatever it is called.

The QSettings seam is `_qsettings()` — production returns the
spec's single store (``core/config_spec.qsettings_store()``);
tests inject a temp IniFormat store through it. GAPS-11 landed
the settings_schema closing step here: ``_qs_get`` / ``_qs_set``
delegate to the spec helpers (``qsettings_get`` /
``qsettings_set``), so the chat tool and the Settings page read
the same spec table and cannot drift. Nothing in this module
raises into the tool funnel.
"""

from __future__ import annotations

import re

from .config_spec import qsettings_get, qsettings_set, qsettings_store
from .state_bus import SettingsChanged, get_state_bus
from .tokens import migrate_theme_name

#: Appearance keys whose write is announced on the state bus
#: (GAPS-28 change bus, G-V3) — exactly the keys the window's
#: ``_apply_appearance()`` reads, so a subscriber that re-runs
#: it after the event sees the new values.
_APPEARANCE_KEYS = frozenset({
    "theme",
    "appearance/font_scale",
    "appearance/density",
    "appearance/custom_hex",
    "appearance/follow_windows",
})

#: Orb styles — the roster gui/window_settings.py builds its
#: style buttons from (and _apply_saved_settings validates
#: against). "Aura" is the only style (owner decision 2026-11-01).
#: saved style (guide D3); a saved style, even Aura, is kept.
# Aura-only since 2026-11-01 by owner decision ("keep only the
# aura"): the other styles were deleted. ORB_STYLES stays a tuple
# so every consumer (setter, thumbnail validation, voice choices)
# keeps working off one source of truth.
ORB_STYLES: tuple[str, ...] = ("Aura",)


def migrate_orb_style(value: object) -> str:
    """Resolve any stored orb style to the single live style.

    Saved Apex/Reactor/unknown values (including blank) resolve to
    Aura on load, so a deleted style can never come back at runtime.
    """
    text = str(value or "").strip()
    return text if text in ORB_STYLES else "Aura"

#: Friendly key -> canonical key.
_ALIASES = {
    "color": "theme", "colour": "theme", "colors": "theme",
    "accent": "theme", "appearance": "theme",
    "orb_style": "orb", "orb style": "orb",
    "live voice": "live_voice", "cloud voice": "live_voice",
    "google voice": "live_voice", "voice_name": "live_voice",
    "local voice": "local_voice", "kokoro voice": "local_voice",
    "on-device voice": "local_voice", "kokoro_voice": "local_voice",
    "reply language": "language", "locale": "language",
    "preferred_language": "language", "preferred language": "language",
    "sounds": "earcons", "interface sounds": "earcons",
    "sound effects": "earcons",
    "suggestion chips": "suggestions",
    "tts loudness standard": "tts_loudness",
    "loudness standard": "tts_loudness",
    "loudness": "tts_loudness",
    "voice switching": "voice_auto_switch",
    "auto voice switching": "voice_auto_switch",
}

#: The settable allowlist, in display order:
#: key -> (label, one-line apply note used in results).
SETTINGS: dict[str, tuple[str, str]] = {
    "theme": ("Theme (her colours)",
              "the window applies it at once — no restart needed"),
    "orb": ("Orb style",
            "the window picks it up when it next builds — "
            "restart VYUK to see it"),
    "live_voice": ("Live voice (cloud brain)",
                   "it applies on your next Live connect"),
    "local_voice": ("On-device voice (Kokoro)",
                    "it applies from the next thing she says"),
    "language": ("Reply language",
                 "she replies in it from the next reply on the "
                 "local brain, and from the next connect on the "
                 "cloud brains; her home voice follows to the "
                 "language's curated voice"),
    "earcons": ("Interface sounds (earcons)",
                "the window picks it up when it next builds — "
                "restart VYUK to hear the change"),
    "suggestions": ("Suggestion chips",
                    "the window picks it up when it next builds — "
                    "restart VYUK to see it"),
    "tts_loudness": ("TTS loudness standard",
                     "it applies from the next thing she says"),
    "voice_auto_switch": ("Automatic voice switching",
                          "it applies from the next thing she says"),
}

_TOGGLES = ("earcons", "suggestions", "tts_loudness",
            "voice_auto_switch")

#: Security-shaped names are refused as Settings-page-only,
#: never silently treated as unknown.
_EXCLUSION_WORDS = (
    "confirm", "gate", "failsafe", "panic", "stop button",
    "taint", "injection", "protected", "provenance", "lock",
    "chrome attach", "real chrome", "screen scope", "scope",
    "update", "api key", "apikey", "api_key", "token",
    "password", "secret", "credential", "permission",
    "security", "firewall",
)

#: Colour words -> theme, for "change the color to X"
#: requests. Only UNAMBIGUOUS words live here; anything else
#: lists the choices instead of guessing.
_THEME_COLOUR_WORDS = {
    "blue": "Cyan", "indigo": "Cyan",
    "teal": "Cyan", "cyan": "Cyan", "crimson": "Crimson",
    "sunset": "Sunset",
    "purple": "Violet", "violet": "Violet",
    "grey": "Graphite", "gray": "Graphite",
    "graphite": "Graphite", "aurora": "Aurora",
    "green": "Aurora", "amber": "Ember", "copper": "Ember",
    "orange": "Ember", "ember": "Ember", "pink": "Rose",
    "rose": "Rose", "mono": "Mono", "grayscale": "Mono",
    "greyscale": "Mono", "black and white": "Mono",
    "contrast": "Contrast", "yellow": "Contrast",
}


def _qsettings():
    """The window's own settings store — the spec's single
    construction site. Lazy (Qt lives in the app process);
    tests inject a temp store by replacing this function."""
    return qsettings_store()


def _canonical_key(raw: str) -> str:
    key = (raw or "").strip().lower().replace("-", "_")
    key = re.sub(r"\s+", " ", key.replace("_", " ")).strip()
    if key in SETTINGS:
        return key
    flat = key.replace(" ", "_")
    if flat in SETTINGS:
        return flat
    return _ALIASES.get(key, _ALIASES.get(flat, ""))


def _is_excluded(raw: str) -> bool:
    text = (raw or "").strip().lower()
    return any(word in text for word in _EXCLUSION_WORDS)


def _themes() -> list[str]:
    """The real theme names — the DTCG source (core/tokens.py's
    theme_names), the same 11 names in the same order as
    gui/style.py's THEMES (INV-11 proves them identical).
    GAPS-15: core reads the core-owned source now, not the GUI."""
    try:
        from .tokens import theme_names

        return list(theme_names())
    except Exception:  # noqa: BLE001 — fall back to the known roster
        return ["Cyan", "Sunset", "Violet", "Graphite",
                "Aurora", "Ember", "Mono", "Rose", "Contrast",
                "Amber", "Crimson"]


def _kokoro_roster() -> list[tuple[str, str]]:
    """(voice_id, label) pairs Settings offers — the curated roster
    in core/languages.py; the languages table's curated voices are
    always acceptable too.
    GAPS-15: core reads the core-owned roster now, not the GUI."""
    roster: list[tuple[str, str]] = []
    try:
        from .languages import _KOKORO_VOICE_ROSTER

        roster = [(str(v), str(lbl)) for v, lbl in _KOKORO_VOICE_ROSTER]
    except Exception:  # noqa: BLE001 — roster is a nicety
        pass
    try:
        from .languages import LANGUAGES

        known = {v for v, _ in roster}
        for entry in LANGUAGES:
            if entry.voice not in known:
                roster.append((entry.voice,
                               f"{entry.display} (curated home voice)"))
    except Exception:  # noqa: BLE001
        pass
    return roster


def _qs_get(key: str):
    """Read one chat-settable QSettings key through the spec
    (GAPS-11: the closing step — the default lives in the spec
    table, not at the call site)."""
    return qsettings_get(key, settings=_qsettings())


def _qs_set(key: str, value) -> None:
    """Write one chat-settable QSettings key through the spec.

    Appearance keys additionally emit ``SettingsChanged`` on the
    state bus (GAPS-28 change bus, G-V3) so the window applies
    them live. The bus is cosmetic: a bus failure can never make
    a setter fail, so the emit is guarded and swallowed.
    """
    qsettings_set(key, value, settings=_qsettings())
    if key in _APPEARANCE_KEYS:
        try:
            get_state_bus().emit(SettingsChanged(key=key, value=value))
        except Exception:  # noqa: BLE001 — cosmetic channel only
            pass


def _current(key: str) -> str:
    """The current value of one setting, spoken-friendly."""
    from .config import config

    if key == "theme":
        return migrate_theme_name(str(_qs_get("theme")))
    if key == "orb":
        return migrate_orb_style(_qs_get("orb_style"))
    if key == "live_voice":
        return str(getattr(config, "voice_name", "Charon"))
    if key == "local_voice":
        return str(getattr(config, "kokoro_voice", "bm_george"))
    if key == "language":
        from .languages import language_by_code

        entry = language_by_code(
            str(getattr(config, "preferred_language", "en-gb")))
        return entry.label if entry else str(
            getattr(config, "preferred_language", "en-gb"))
    if key == "earcons":
        return "on" if bool(_qs_get("sounds_enabled")) else "off"
    if key == "suggestions":
        return "on" if bool(_qs_get("suggestions/enabled")) else "off"
    if key == "tts_loudness":
        return "on" if bool(getattr(config, "tts_loudness_enabled",
                                    True)) else "off"
    if key == "voice_auto_switch":
        return "on" if bool(getattr(config, "voice_auto_switch",
                                    True)) else "off"
    return ""


def get_settings(setting: str = "") -> str:
    """action=get: one setting, or the whole panel."""
    raw = (setting or "").strip()
    if raw:
        if _is_excluded(raw):
            return _exclusion_refusal(raw)
        key = _canonical_key(raw)
        if not key:
            return _unknown_setting(raw)
        label = SETTINGS[key][0]
        return f"{label}: {_current(key)}."
    lines = ["Her settings right now:"]
    for key, (label, _note) in SETTINGS.items():
        lines.append(f"- {key} ({label}): {_current(key)}")
    return "\n".join(lines)


def _exclusion_refusal(raw: str) -> str:
    return (
        f"'{raw}' is not changeable from chat — security-shaped "
        "settings (confirmation policy, the failsafe, injection "
        "and protected-path rules, attach opt-ins, keys) are "
        "Settings-page-only by design, so an injected page or a "
        "persuaded model can never re-posture her from inside a "
        "conversation. Open Settings to change it.")


def _unknown_setting(raw: str) -> str:
    names = ", ".join(SETTINGS.keys())
    return (f"I don't have a chat-settable setting called "
            f"'{raw}'. The ones I can get or set are: {names}. "
            "Security-shaped settings are Settings-page-only "
            "by design.")


def _parse_bool(value: str) -> bool | None:
    text = (value or "").strip().lower()
    if text in ("on", "true", "yes", "enable", "enabled", "1"):
        return True
    if text in ("off", "false", "no", "disable", "disabled", "0"):
        return False
    return None


def _save_env(key: str, value: str) -> None:
    from . import config as _config_module

    _config_module.save_env_setting(key, value)


def _confirm_set(key: str, expected: str, message: str):
    """R1b: read a just-written setting back through _current —
    the write succeeding does not prove the value took. Returns
    a verified Outcome, an unverified Outcome, or a plain honest
    failure when the read-back disagrees."""
    from . import tool_outcome as _outcome  # R1b: seam wiring

    try:
        now = _current(key)
    except Exception as exc:  # noqa: BLE001 — unreadable setting
        return _outcome.unverified(
            message, "could not re-read the setting: %s" % exc)
    if now != expected:
        return ("%s was asked to change to %s, but it still reads "
                "%s — nothing was changed." % (key, expected, now))
    return _outcome.verified(
        message, "setting reads back as %s" % expected)


def set_setting(setting: str, value: str) -> str:
    """action=set: validate against the allowlist, write
    through the GUI's own store, and answer with the verified
    apply semantics. Ambiguity lists the choices — no guess."""
    raw = (setting or "").strip()
    if not raw:
        return ("Set which setting? The chat-settable ones are: "
                + ", ".join(SETTINGS.keys()) + ".")
    if _is_excluded(raw):
        return _exclusion_refusal(raw)
    key = _canonical_key(raw)
    if not key:
        return _unknown_setting(raw)
    label, note = SETTINGS[key]
    value = (value or "").strip()
    if not value:
        choices = _choices_for(key)
        return (f"{label} is {_current(key)} right now. "
                f"Tell me the new value — choices: {choices}.")

    from .config import config

    if key == "theme":
        return _set_theme(value, label, note)
    if key == "orb":
        wanted = _match_name(value, ORB_STYLES)
        if wanted is None:
            return (f"'{value}' isn't an orb style I know. "
                    f"Choices: {', '.join(ORB_STYLES)}. "
                    f"The orb is {_current('orb')} — unchanged.")
        _qs_set("orb_style", wanted)
        _qs_set("orb_style_chosen", True)
        return _confirm_set("orb", migrate_orb_style(wanted),
                            f"Orb style set to {wanted} — {note}.")
    if key == "live_voice":
        offered = [str(v) for v in getattr(
            config, "available_voices", [])]
        wanted = _match_name(value, offered)
        if wanted is None:
            return (f"'{value}' isn't one of the Live voices "
                    f"Google offers her ({', '.join(offered)}). "
                    f"The Live voice is {_current('live_voice')} "
                    "— unchanged.")
        config.voice_name = wanted
        _save_env("VOICE_NAME", wanted)
        return _confirm_set(
            "live_voice", wanted,
            f"Live voice set to {wanted} — {note}. "
            "(This is the cloud brain's Google voice; her "
            "on-device voice is local_voice.)")
    if key == "local_voice":
        roster = _kokoro_roster()
        wanted = None
        for voice_id, voice_label in roster:
            if value.lower() in (voice_id.lower(),
                                 voice_label.lower()) \
                    or value.lower() == voice_label.split(" — ")[0].lower():
                wanted = voice_id
                break
        if wanted is None and re.fullmatch(r"[a-z]{2}_[a-z]+",
                                           value.lower()):
            # Settings itself accepts a typed Kokoro id; so do we.
            wanted = value.lower()
        if wanted is None:
            names = ", ".join(v for v, _ in roster)
            return (f"'{value}' isn't a Kokoro voice I know. "
                    f"Choices: {names}. Her on-device voice is "
                    f"{_current('local_voice')} — unchanged.")
        config.kokoro_voice = wanted
        _save_env("KOKORO_VOICE", wanted)
        return _confirm_set("local_voice", wanted,
                            f"On-device voice set to {wanted} — {note}.")
    if key == "language":
        return _set_language(value, label, note)
    if key in _TOGGLES:
        wanted = _parse_bool(value)
        if wanted is None:
            return (f"{label} is {_current(key)} — tell me 'on' "
                    "or 'off' to change it.")
        if key == "earcons":
            _qs_set("sounds_enabled", bool(wanted))
        elif key == "suggestions":
            _qs_set("suggestions/enabled", bool(wanted))
        elif key == "tts_loudness":
            config.tts_loudness_enabled = bool(wanted)
            _save_env("TTS_LOUDNESS_ENABLED",
                      "true" if wanted else "false")
        elif key == "voice_auto_switch":
            config.voice_auto_switch = bool(wanted)
            _save_env("VOICE_AUTO_SWITCH",
                      "true" if wanted else "false")
        return _confirm_set(
            key, "on" if wanted else "off",
            f"{label} turned {'on' if wanted else 'off'} — {note}.")
    return _unknown_setting(raw)


def _choices_for(key: str) -> str:
    if key == "theme":
        return ", ".join(_themes())
    if key == "orb":
        return ", ".join(ORB_STYLES)
    if key == "live_voice":
        from .config import config

        return ", ".join(str(v) for v in config.available_voices)
    if key == "local_voice":
        return ", ".join(v for v, _ in _kokoro_roster())
    if key == "language":
        from .languages import LANGUAGES

        return ", ".join(e.label for e in LANGUAGES)
    return "on or off"


def _match_name(value: str, options) -> str | None:
    """Exact (case-insensitive) match, else a UNIQUE
    prefix/substring match; ambiguity and no-match both -> None
    (the caller lists the choices; nothing is guessed)."""
    text = (value or "").strip().lower()
    if not text:
        return None
    for option in options:
        if option.lower() == text:
            return option
    hits = [o for o in options
            if o.lower().startswith(text) or text in o.lower()]
    return hits[0] if len(hits) == 1 else None


def _set_theme(value: str, label: str, note: str) -> str:
    themes = _themes()
    # D3: a stored/spoken legacy name heals before matching, so
    # "Indigo Teal" still lands on Cyan instead of erroring.
    value = migrate_theme_name(value)
    wanted = _match_name(value, themes)
    if wanted is None:
        colour = _THEME_COLOUR_WORDS.get(value.strip().lower())
        if colour and colour in themes:
            wanted = colour
    if wanted is None:
        return (f"'{value}' doesn't name one theme unambiguously. "
                f"Choices: {', '.join(themes)}. The theme is "
                f"{_current('theme')} — unchanged; I won't guess "
                "a colour.")
    _qs_set("theme", wanted)
    return _confirm_set("theme", migrate_theme_name(wanted),
                        f"Theme set to {wanted} — {note}.")


def _set_language(value: str, label: str, note: str) -> str:
    from .languages import LANGUAGES, language_by_code, \
        set_preferred_language

    entry = language_by_code(value)
    if entry is None:
        text = value.strip().lower()
        hits = [e for e in LANGUAGES
                if text == e.display.lower()
                or text in e.label.lower()]
        entry = hits[0] if len(hits) == 1 else None
    if entry is None:
        names = ", ".join(e.label for e in LANGUAGES)
        return (f"'{value}' doesn't name one language "
                f"unambiguously. Choices: {names}. Her reply "
                f"language is {_current('language')} — unchanged.")
    applied = set_preferred_language(entry.code)
    if applied is None:
        return (f"Couldn't set the language to {entry.label} — "
                "nothing was changed.")
    return _confirm_set(
        "language", entry.label,
        f"Reply language set to {entry.label} — {note}. "
        f"(Her home voice is now {entry.voice}, the curated "
        f"{entry.display} voice.)")


def app_settings(action: str, setting: str = "",
                 value: str = "") -> str:
    """The tool handler: get/set over the allowlist above."""
    act = (action or "").strip().lower()
    if act == "get":
        return get_settings(setting)
    if act == "set":
        return set_setting(setting, value)
    return (f"Unknown app_settings action: '{action}'. Use "
            "'get' (one setting, or all when setting is empty) "
            "or 'set' with a setting and a value.")
