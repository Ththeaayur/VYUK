"""Reply verbosity (Stage 0 features batch 5, item 11).

One setting, three levels — concise / balanced / detailed — that
steers how long her replies run. The mechanism is the standard
one: a length instruction in the system prompt (every brain
appends it next to the reply-language directive) plus a hard
output cap where the API takes one (Live `max_output_tokens`,
Groq `max_tokens`, the local sampling cap). Balanced is the
absence of steering: an empty directive and no cap, so a
balanced session's prompt and request are byte-identical to
before this batch. Unknown values normalise to balanced —
a bad stored value can never shout at the model or starve it.
"""
from __future__ import annotations

VERBOSITY_LEVELS: tuple[str, ...] = ("concise", "balanced", "detailed")
DEFAULT_VERBOSITY = "balanced"

_CONCISE_DIRECTIVE = (
    "Keep replies short: one or two sentences unless the user "
    "asks for more.")
_DETAILED_DIRECTIVE = (
    "Give full, thorough replies with helpful detail.")

# Live / Groq output cap for a concise reply: 256 tokens leaves
# room for a short answer plus one tool call; balanced and
# detailed set no cap (None), so their requests are unchanged.
_CONCISE_MAX_OUTPUT_TOKENS = 256

# Local sampling scale: the turn profiles' max_tokens are scaled
# by this factor — concise quarters the cap, detailed doubles it.
# The default (summary / non-turn) profile is never scaled.
_MAX_TOKENS_FACTORS = {"concise": 0.25, "balanced": 1.0, "detailed": 2.0}


def normalize_verbosity(value) -> str:
    """A stored verbosity value, healed: unknown, empty or
    non-string values are balanced."""
    text = str(value or "").strip().lower()
    return text if text in VERBOSITY_LEVELS else DEFAULT_VERBOSITY


def reply_verbosity_directive(level) -> str:
    """The length instruction for a level, as a prompt block
    (leading blank line, matching the builders' block style).
    Balanced is "" — no steering, byte-identical prompt."""
    level = normalize_verbosity(level)
    if level == "concise":
        return f"\n\n{_CONCISE_DIRECTIVE}"
    if level == "detailed":
        return f"\n\n{_DETAILED_DIRECTIVE}"
    return ""


def verbosity_max_output_tokens(level):
    """The hard output cap for a level, or None (no cap)."""
    if normalize_verbosity(level) == "concise":
        return _CONCISE_MAX_OUTPUT_TOKENS
    return None


def verbosity_max_tokens_factor(level) -> float:
    """The local-sampling max_tokens scale for a level."""
    return _MAX_TOKENS_FACTORS[normalize_verbosity(level)]


def current_verbosity() -> str:
    """The live setting, read from the config singleton and
    normalised. The GUI writes it through the config + .env
    (the live_voice pattern); the default is balanced."""
    try:
        from .config import config
    except Exception:  # noqa: BLE001 — no config, no steering
        return DEFAULT_VERBOSITY
    return normalize_verbosity(getattr(config, "reply_verbosity", None))
