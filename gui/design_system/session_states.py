"""Command Deck Phase D — the §7 session-state map (pure, no Qt).

The old session pill carried ten free-text labels with hardcoded
hex colours (green Listening/Speaking, amber everything else).
Guide §7 + D2 replace them with one HUD word, one role colour and
one glyph per state. This module is the single source of that map:
``gui/window_services.py:_set_pill`` resolves every pill through it
and emits the same word/role/glyph on the state bus, so the pill,
the bus AppState and the StatusStrip can never disagree.

Roles map to the style tokens (never literals — this package is
scanned with an EMPTY colour allowlist):
* cyan — alive (Listening, Speaking-as-Responding, Local Core)
* ice — in-progress but not acting (Processing, Connecting,
  Calibrating); realised as TEXT, the pale highlight, since the
  token set has no separate ice constant and the guide's pale
  tint family is the highlight
* amber — acting / attention / approval
* red — fault
* neutral — offline / unknown

Unknown labels pass through with the neutral role rather than
raising: a future status must never break the pill. The §7
display word for an unknown label is the label itself, unchanged.
"""

from ..style import ACCENT, AMBER, DANGER, NEUTRAL, OK, TEXT

# Old pill label -> (HUD word, role, glyph). Glyphs are the §7
# geometric language: ring for idle/in-progress, diamond for done,
# triangle for warning, square for fault.
STATUS_MAP = {
    "On-device brain": ("LOCAL CORE", "cyan", "ring"),
    "Reconnecting": ("RECONNECTING", "amber", "ring"),
    "Setting up": ("CALIBRATING", "ice", "ring"),
    "Listening": ("LISTENING", "cyan", "ring"),
    "Speaking": ("RESPONDING", "cyan", "ring"),
    "Thinking": ("PROCESSING", "ice", "ring"),
    "Connecting": ("CONNECTING", "ice", "ring"),
    "Problem": ("FAULT", "red", "square"),
    "Offline": ("LINK OFFLINE", "neutral", "ring"),
    "Setup needed": ("SETUP REQUIRED", "amber", "triangle"),
}

ROLE_COLOURS = {
    "cyan": ACCENT,
    "ice": TEXT,
    "amber": AMBER,
    "green": OK,
    "red": DANGER,
    "neutral": NEUTRAL,
}

# UI rethink Build 1: the header shows one plain state word, not the
# HUD word. The bus still emits the HUD word (LOCAL CORE,
# RECONNECTING, LINK OFFLINE) — the pill's label reads in plain
# words ("Local core", "Reconnecting", "Offline") — so the header
# names the state the way a person would. Keyed on the HUD word;
# an unknown HUD word passes through unchanged.
STATUS_DISPLAY_WORDS = {
    "LOCAL CORE": "Local core",
    "RECONNECTING": "Reconnecting",
    "CALIBRATING": "Setting up",
    "LISTENING": "Listening",
    "RESPONDING": "Responding",
    "PROCESSING": "Thinking",
    "CONNECTING": "Connecting",
    "FAULT": "Problem",
    "LINK OFFLINE": "Offline",
    "SETUP REQUIRED": "Setup needed",
}


def resolve_status(text):
    """Resolve an old pill label to (word, role, glyph, colour).

    Unknown labels pass through as (text, "neutral", "ring",
    NEUTRAL) — never a raise, never a fake role.
    """
    word, role, glyph = STATUS_MAP.get(
        text or "", (text or "", "neutral", "ring"))
    return word, role, glyph, ROLE_COLOURS.get(role, NEUTRAL)


def resolve_mode(fallback_active, engine):
    """Command Deck Phase E — the MODE chip word from the
    brain-mode source of truth.

    ``fallback_active`` is the session's cloud->local fallback
    flag; ``engine`` is ``_brain_engine()`` ("local"/"gemini").
    Fallback overrides everything (the running brain is
    on-device even when the pick is cloud); a picked local
    brain is LOCAL CORE; a picked cloud brain is CLOUD.
    Anything else is "" — the strip hides the chip rather
    than guessing a mode. The Settings brain-truth line uses
    the same derivation, so the strip and the card agree.
    """
    if fallback_active:
        return "LOCAL CORE"
    if engine == "local":
        return "LOCAL CORE"
    if engine == "gemini":
        return "CLOUD"
    return ""
