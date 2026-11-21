from __future__ import annotations

import logging  # INTENTIONAL (B31: log definition below needs logging)
log = logging.getLogger("nova.w9_wiring")  # INTENTIONAL (B31: literal name, same registry object as the facade log)
from . import chat_render as _chat_render  # INTENTIONAL (B31: moved code resolves _chat_render by bare name)
from . import conversation as _conversation  # INTENTIONAL (B31: moved code resolves _conversation by bare name)
from . import theme_packs as _packs  # INTENTIONAL (B31: moved code resolves _packs by bare name)
from .config_spec import (  # GAPS-11: the spec helpers own every QSettings read/write
    qsettings_contains,
    qsettings_get,
    qsettings_remove,
    qsettings_remove_noted,
    qsettings_set,
    qsettings_set_noted,
)

# ------------------------------------------------------------------ #
# 15. Conversation checks / error ladder — diagnostics only
# ------------------------------------------------------------------ #

def diagnose_turn(window, text: str):
    """Run check_turn on a live typed turn. DIAGNOSTIC ONLY: the
    result is recorded on the window and logged; it never blocks,
    edits, or re-routes the turn (blocking needs an owner decision).
    """
    result = _conversation.check_turn(text)
    try:
        window._w9_last_turn_check = result
    except Exception:  # noqa: BLE001
        pass
    if not result.ok:
        log.info("w9 conversation check: %s", result.guidance)
    return result


def get_turn_ladder(window):
    """The window-owned ErrorLadder (created on first use)."""
    ladder = getattr(window, "_w9_error_ladder", None)
    if ladder is None:
        ladder = _conversation.ErrorLadder()
        window._w9_error_ladder = ladder
    return ladder


def note_turn_error(window, understood_so_far: str = ""):
    """Advance the error ladder on a send failure (e.g. the
    not-connected branch). Returns the LadderOutcome; the caller
    decides what, if anything, to show — the ladder itself never
    injects a turn."""
    outcome = get_turn_ladder(window).record_failure(understood_so_far)
    log.info("w9 error ladder: action=%s reask=%d consecutive=%d",
             outcome.action, outcome.reask_number,
             outcome.consecutive_errors)
    return outcome


def note_turn_success(window) -> None:
    """Reset the error ladder after a successful turn."""
    get_turn_ladder(window).record_success()


def lint_error(problem: str, cause: str, solution: str):
    """Lint an error string and log the verdict — the live log sink
    for lint results. Returns the LintResult; callers never speak
    an unlinted error (format_error raises on a failed lint)."""
    result = _conversation.lint_error_string(problem, cause, solution)
    if result.ok:
        log.info("w9 error lint: ok")
    else:
        log.warning("w9 error lint problems: %s", "; ".join(result.problems))
    return result


# ------------------------------------------------------------------ #
# 11. Chat render — parallel document on the live bubble path
# ------------------------------------------------------------------ #

def note_chat_message(window, who: str, text: str, message_id: str = ""):
    """Feed one live bubble through the v564 ChatRenderer.

    Pygments / markdown-it-py stay lazy-optionals inside the staged
    module; the fallback renderer is the no-dependency path. The
    renderer document is stored per message on the window; the
    on-screen bubble keeps its existing widgets (see module
    docstring for why the display swap is owed).
    """
    renderer = getattr(window, "_w9_chat_renderer", None)
    if renderer is None:
        renderer = _chat_render.ChatRenderer()
        window._w9_chat_renderer = renderer
    if not message_id:
        message_id = f"{who}-{len(renderer.message_ids())}"
    doc = renderer.begin_message(
        message_id, "user" if who == "user" else "assistant")
    try:
        doc.build_from_markdown(text)
    except Exception:  # noqa: BLE001 — fall back to plain chunks
        doc.append_text(text)
    return doc


# ------------------------------------------------------------------ #
# 12. Settings schema / store behind the live settings path
# ------------------------------------------------------------------ #

# Live QSettings keys that differ from the shared model's canonical
# keys (see settings_schema.FIELD_SPECS). Migration copies the
# legacy value into the canonical key ONLY when the canonical key
# is absent, and never overwrites a canonical value.
_LEGACY_SETTINGS_KEYS = {
    "density": "appearance/density",
}


def settings_store(settings=None):
    """SettingsStore bound to the given (or production) QSettings."""
    from voice_assistant.core import settings_schema as _schema

    return _schema.SettingsStore(settings)


def migrate_settings_store(store) -> list[str]:
    """Copy legacy QSettings values into canonical model keys.

    Returns the migrated model names. A canonical key that already
    exists is never overwritten; a missing legacy value migrates
    nothing. This is the migration from the existing settings shape.
    """
    migrated = []
    for name, legacy_key in _LEGACY_SETTINGS_KEYS.items():
        canonical_key = _schema_key(store, name)
        try:
            has_canonical = qsettings_contains(
                canonical_key, settings=store.settings)
        except Exception:  # noqa: BLE001
            has_canonical = False
        if has_canonical:
            continue
        try:
            has_legacy = qsettings_contains(
                legacy_key, settings=store.settings)
        except Exception:  # noqa: BLE001
            has_legacy = False
        if not has_legacy:
            continue
        try:
            legacy = qsettings_get(legacy_key, settings=store.settings)
            qsettings_set(canonical_key, legacy, settings=store.settings)
            migrated.append(name)
        except Exception:  # noqa: BLE001
            continue
    return migrated


def _schema_key(store, name: str):
    from voice_assistant.core import settings_schema as _schema

    return _schema.FIELD_SPECS[name][3]


def reset_settings_row(store, name: str):
    """Reset one shared-model row: removes the canonical key AND its
    known legacy key, so the live reader and the model agree on the
    default afterwards. Returns the reset row."""
    from voice_assistant.core import settings_schema as _schema

    row = _schema.reset_row(store, name)
    legacy_key = _LEGACY_SETTINGS_KEYS.get(name)
    if legacy_key:
        # Round-3 item 14: a failed legacy remove is NOT harmless —
        # the migration copies legacy -> canonical whenever the
        # canonical key is absent, so a remove that never persisted
        # resurrects the reset value at the next boot. A failure is
        # logged + recorded (feed marker), never swallowed.
        qsettings_remove_noted(legacy_key, settings=store.settings)
    return row


def settings_schema_rows(store, filter_text: str = ""):
    """Searchable shared-model rows for the Settings-store card."""
    from voice_assistant.core import settings_schema as _schema

    return _schema.build_rows(store, filter_text)


def settings_snapshot(settings=None) -> dict:
    """Shared-model snapshot after migration (load path proof)."""
    store = settings_store(settings)
    migrate_settings_store(store)
    return store.snapshot()


# ------------------------------------------------------------------ #
# 13. Theme packs -> live theme-picker card
# ------------------------------------------------------------------ #

def apply_theme_pack_text(text: str, settings=None):
    """Load -> validate -> apply one pasted theme-pack JSON.

    Invalid packs raise ThemePackError and change NOTHING (the
    staged module rejects whole, never partially). A valid pack's
    tokens are stored under theme_pack/*, and its accent becomes
    the live Custom accent so the pack visibly applies; the full
    token set is recorded, not silently reduced to the accent.
    Returns {"name", "tokens", "accent"}.
    """
    pack = _packs.load_pack(text)  # raises ThemePackError when invalid
    tokens = _packs.apply_pack(pack)
    accent = tokens.get("accent")
    if settings is not None:
        # Round-3 item 14: a valid pack that applies in-session but
        # never persists is a silent loss — the theme is back to the
        # old one at the next launch. Each write is noted on failure
        # (log line + feed marker), never swallowed.
        import json as _json

        qsettings_set_noted("theme_pack/name", pack.get("name", ""),
                            settings=settings)
        qsettings_set_noted("theme_pack/tokens", _json.dumps(tokens),
                            settings=settings)
        if accent:
            qsettings_set_noted("theme", "Custom", settings=settings)
            qsettings_set_noted("appearance/custom_hex", accent,
                                settings=settings)
    return {"name": pack.get("name", ""), "tokens": tokens,
            "accent": accent}


def saved_theme_pack_names(settings) -> list[str]:
    """Names of packs stored by apply_theme_pack_text (at most the
    latest one — packs are stored singly, not as a library)."""
    try:
        name = qsettings_get("theme_pack/name", settings=settings)
    except Exception:  # noqa: BLE001
        name = ""
    return [str(name)] if name else []
