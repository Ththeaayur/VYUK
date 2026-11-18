"""
FUN skills for VYUK — the play pack: a guess-the-tune
game, a story built a paragraph at a time, a word of the day, a
compliment grounded in something real, dice with advantage, a
decision helper that weighs stated pros, generated ambient
soundscapes, and a morning quote from a built-in bank.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise — an exception
becomes an honest "I couldn't…" line; caps and fallbacks are
disclosed in the result. Seams are reused, never rebuilt: the
melody renderer from skills_creative, the dice parser from
skills_smart_chance, memory facts via memory_store, plans via
planner, the journal via episodes, calendar via
google_services.next_events.

Nothing here invents facts about the user. The compliment names
an achievement only when one is actually found in the plans,
the scheduler's fired Pomodoros or the journal; the word of the
day uses the user's name only when a name fact exists; the
quote is matched to a calendar event only when one is really
there. Playback and generation sit behind module-level helpers
(_play_tune, _generate_scene, _start_player, _first_event_today)
so tests can monkeypatch the hardware away.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

# INTENTIONAL: re-exports of the old module's import bindings, so
# the facade's namespace matches the old module's exactly
# (dir-diff-empty bar, §2 P2).
import difflib
import hashlib
import json
import math
import random
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from .memory import episodes  # Phase 3 M1b: moved to core/memory/
from . import google_services
from .brain import planner  # Phase 3 B2-remainder: planner moved to core/brain/
from .config import DATA_DIR
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
from .scheduler import scheduler
from .skills_creative import _SAMPLE_RATE, _render_melody
from .skills_smart_chance import _MAX_DICE, _MAX_SIDES, _parse_dice

# INTENTIONAL: re-exports of every name moved into the impl modules.
from .skills_fun_helpers import (
    _fact_json,
    _save_fact_json,
    _user_first_name,
    _AudioUnavailable,
    _play_float_samples,
)
from .skills_fun_play import (
    _TUNES,
    _TUNE_BEAT_SECONDS,
    _NOTE_NAMES,
    _render_tune,
    _play_tune,
    _note_names,
    _norm_title,
    _title_matches,
    music_trivia,
    _STORY_READ_CAP,
    _STORY_PARA_CAP,
    _HEROES,
    _OBJECTS,
    _PLACES,
    _OPENINGS,
    _CONTINUATIONS,
    _KEYWORD_FALLBACKS,
    _KEYWORD_STOPWORDS,
    _stories_dir,
    _story_path,
    _story_hero,
    _read_story,
    _append_block,
    _story_opening,
    _story_keyword,
    _story_continuation,
    story_chain,
)
from .skills_fun_daily import (
    _WORDS,
    word_of_the_day,
    _GENERIC_COMPLIMENTS,
    _COMPLIMENT_TEMPLATES,
    _recent_achievements,
    compliment_engine,
    _QUOTES,
    _THEME_KEYWORDS,
    _first_event_today,
    _event_theme,
    quote_of_morning,
)
from .skills_fun_chance_calm import (
    _DICE_EXAMPLES,
    _roll_once,
    _dice_label,
    _dice_line,
    dice_roller_plus,
    _POS_WORDS,
    _NEG_WORDS,
    _parse_options,
    _score_pros,
    _flip_coin,
    coin_decision,
    _AMBIENT_RATE,
    _AMBIENT_SECONDS,
    _AMBIENT_MAX_SLEEP,
    _SCENE_LABELS,
    _AMBIENT,
    _AMBIENT_LOCK,
    _lowpass,
    _normalise,
    _loop_ready,
    _rain_buffer,
    _wind_buffer,
    _cafe_buffer,
    _generate_scene,
    _player_main,
    _start_player,
    _halt_playback,
    _stop_ambient_locked,
    _auto_stop,
    ambient_scene,
)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Fun pack tool on a ToolRegistry-like object."""
    registry.register(
        name="music_trivia",
        description="Play a guess-the-tune game: 'start' plays the first phrase of a traditional, public-domain melody (Twinkle Twinkle, Ode to Joy, Jingle Bells and friends) and asks you to name it; 'answer' with your guess checks it and keeps a running score in memory. If the tune can't be played aloud, its first notes are given as note names instead. Use for a quick music quiz.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "answer"], "description": "'start' (play a tune) or 'answer' (guess it)"},
                "answer": {"type": "string", "description": "Your guess at the tune's name (for action 'answer')"},
            },
            "required": ["action"],
        },
        handler=music_trivia,
    )
    registry.register(
        name="story_chain",
        description="Build a story together, a paragraph at a time: 'start' opens today's story with VYUK's first paragraph, 'continue' adds your paragraph (passed as text) plus her next one, 'read' shows the story so far. Saved as a dated text file in her data folder, beside the day journal. Her paragraphs are template-written, not AI-composed — expect charming, not literary.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "continue", "read"], "description": "'start', 'continue' or 'read'"},
                "text": {"type": "string", "description": "Your paragraph (for action 'continue')"},
            },
            "required": ["action"],
        },
        handler=story_chain,
    )
    registry.register(
        name="word_of_the_day",
        description="Give one interesting word for today from a built-in bank of about 40 — the same word all day, a new one tomorrow. Gives the meaning and an example sentence, personalised with your first name when she knows it, and saves the word to memory. Use for a daily vocabulary moment.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=word_of_the_day,
    )
    registry.register(
        name="compliment_engine",
        description="Give a sincere, specific compliment based on something real: a plan you finished, a focus round you completed today, or your recent journal entries — the actual achievement is named. When she can find nothing specific, she says so and gives a plainly-labelled general compliment instead of inventing one. Use when you could use a boost.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=compliment_engine,
    )
    registry.register(
        name="dice_roller_plus",
        description="Roll dice in tabletop notation with extras: '3d6+2', 'd20', '2d8-1' — every die is listed and the modifier applied. Mode 'advantage' rolls the whole set twice and keeps the higher total; 'disadvantage' keeps the lower. Up to 50 dice, up to 1000 sides (the same caps as roll_dice). Use for tabletop games and bigger random numbers.",
        parameters={
            "type": "object",
            "properties": {
                "notation": {"type": "string", "description": "Dice notation, e.g. '3d6+2' (default '1d6')"},
                "mode": {"type": "string", "enum": ["normal", "advantage", "disadvantage"], "description": "'normal' (default), 'advantage' or 'disadvantage'"},
            },
            "required": [],
        },
        handler=dice_roller_plus,
    )
    registry.register(
        name="coin_decision",
        description="Help deciding between options (separate them with ';'): state the pros in the pros text and she weighs what you actually said — positives and negatives counted per option — then recommends with her reasoning shown, admitting the reasoning is only as good as the pros given. On a true tie she says so and settles it with a coin flip instead.",
        parameters={
            "type": "object",
            "properties": {
                "options": {"type": "string", "description": "The options, separated by semicolons, e.g. 'stay home; go out'"},
                "pros": {"type": "string", "description": "The pros and cons in your own words, naming the options, e.g. 'staying is cheap and relaxing, going out is fun but expensive'"},
            },
            "required": ["options"],
        },
        handler=coin_decision,
    )
    registry.register(
        name="ambient_scene",
        description="Play a generated ambient soundscape — rain, wind, café murmur, or the mixes storm, cosy_cafe, windy_cafe and full_mix — synthesised from shaped noise and looped quietly. Actions: play (with an optional sleep_minutes timer that stops it), stop, status. Use for focus, reading or falling asleep. Playback needs the audio library; without it she says so honestly.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["play", "stop", "status"], "description": "'play', 'stop' or 'status' (default 'status')"},
                "scene": {"type": "string", "enum": ["rain", "wind", "cafe", "storm", "cosy_cafe", "windy_cafe", "full_mix"], "description": "Which soundscape (for action 'play', default 'rain')"},
                "sleep_minutes": {"type": "integer", "description": "Stop automatically after this many minutes (0 = no timer, max 600)"},
            },
            "required": [],
        },
        handler=ambient_scene,
    )
    registry.register(
        name="quote_of_morning",
        description="Give one quote for the day from a built-in bank of public-domain classics (Franklin, Thoreau, Dickinson, Shakespeare and friends), matched to the theme of your first calendar event when Google Calendar is connected — work, travel, family, health, study or celebration — or a general quote when there's no event. The bank is built in; nothing is fetched.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=quote_of_morning,
    )
