"""
Local brain client for VYUK — a fully on-device alternative to
core/voice/live_client.py's Gemini Live session. No API key required.

Two backends (config.local_backend, v1.8):

    "builtin" (default, zero-setup) — the LLM runs IN-PROCESS via
        llama-cpp-python on a GGUF that core/voice/local_assets.py
        auto-downloads on first connect; voice is Kokoro (kokoro-onnx)
        first, then Piper, then SAPI. Tool calls use a JSON-block
        protocol parsed by _extract_tool_call() — an instruct GGUF's
        chat template is not tool-aware, so the protocol is spelled out
        in the system prompt (see _render_tool_protocol()).
    "ollama" (advanced, v1.7 behaviour) — an external Ollama server
        does the thinking via /api/chat with native tool calls;
        voice is Piper → SAPI → text-only, as before.

Pipeline (both backends share the ears and the mic loop):

    mic 16 kHz int16 PCM ──▶ energy VAD (this module)
                         ──▶ faster-whisper speech-to-text (lazy import)
                         ──▶ the selected backend's brain turn
                             (tool calls loop through tools.execute(),
                             exactly like the Gemini engine)
                         ──▶ the backend's speech chain
                         ──▶ 24 kHz int16 PCM ──▶ audio_manager playback

LocalLiveSession presents the IDENTICAL callback surface as
GeminiLiveSession (on_status_change / on_transcript / on_audio_data /
on_tool_call, is_connected, client → None, start/send_text/
send_image_frame/stop) so core/brain/brain.py can swap engines without the
GUI knowing. The status-string protocol is the same one live_client
emits (CONNECTING / LISTENING / SPEAKING / THINKING / ERROR: … /
DISCONNECTED), plus "SETUP: …" progress lines while the built-in
backend downloads its assets on first connect — see ARCHITECTURE.md
landmine 6.

Heavy dependencies (faster-whisper, llama-cpp, kokoro-onnx, piper,
pyttsx3) are imported lazily inside the methods that use them:
importing this module is cheap, and a machine without the local-brain
packages degrades to clear errors/console notes instead of import
crashes at startup.
"""

import asyncio
import datetime
import difflib
import gc
import io
import json
import math
import queue
import re
import sys
import threading
import time
import urllib.request
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..memory import episodes  # Phase 3 V1b: moved to core/voice/
from .. import failsafe, injection, loudness  # Phase 3 V1b: moved to core/voice/
from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed
from ..brain import planner  # Phase 3 V1b: moved to core/voice/


from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..config import DATA_DIR, config  # Phase 3 V1b: moved to core/voice/
from ..provider_identity import Engine  # GAPS-22 (B-V3): engine identity
from ..languages import (  # Phase 3 V1b: moved to core/voice/
    kokoro_voice_for_text,
    reply_language_directive,
    resolve_transcription_profile,
)
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel

# --- Voice-activity detection tuning (int16 PCM at config.sample_rate_in) ---
from .local_client_vad import (_vad_threshold, _barge_in_threshold, _publish_noise_floor_snapshot, _noise_floor_snapshot, _reset_noise_floor_snapshot, NoiseFloorEstimator, _effective_vad_threshold, _barge_in_guard_s, _barge_in_sustain_s, VAD_RMS_THRESHOLD, VAD_MIN_SPEECH_S, VAD_TRAILING_SILENCE_S, MAX_UTTERANCE_S, VAD_NOISE_WINDOW_S, VAD_NOISE_MULTIPLIER, VAD_NOISE_CAP_FACTOR, VAD_NOISE_MIN_SAMPLES, _LAST_NOISE_FLOOR_SNAPSHOT, POST_PLAYBACK_GUARD_MS, BARGE_IN_SUSTAIN_MS)  # refactor P1 step 2 (vad concern, moved verbatim)

from .local_client_guards import (
    HISTORY_CAP,
    HISTORY_WINDOW,
    HOUSEKEEPING_TICK_S,
    IDLE_UNLOAD_S,
    LLM_N_CTX,
    MAX_TOOL_REPAIRS,
    MAX_TOOL_ROUNDS,
    OLLAMA_CHAT_TIMEOUT_S,
    OLLAMA_TAGS_TIMEOUT_S,
    TOOL_SHAPE_FALLBACK,
    WHISPER_BEAM_SIZE,
    WHISPER_LOG_PROB_THRESHOLD,
    WHISPER_NO_SPEECH_THRESHOLD,
    _BATTERY_QUESTION_RE,
    _DOWNLOAD_QUESTION_RE,
    _INITIAL_PROMPT_MAX_CHARS,
    _NETWORK_QUESTION_RE,
    _NO_VISION_PROMPT,
    _PROMPT_APP_NAMES,
    _STATE_GUARD_RULES,
    _STATE_QUESTION_RE,
    _ambient_packet_text,
    _bump_epoch,
    _is_lite_tier_model,
    _known_user_name,
    _log_brain_guard,
    _max_tool_rounds,
    _memory_block,
    _state_guard_for,
    _tool_call_signature,
    _tool_progress_report,
    _transcription_initial_prompt,
    _transcription_settings,
    context_pressure_from_history,
    prior_turn_failed_from_trace,
    sampling_profile,
)  # INTENTIONAL (batch 1): facade re-exports
# of the moved group — importers resolve through the facade.
from .local_client_pcm import (
    _BARGE_FADE_SAMPLES,
    _BLEND_CACHE,
    _CROSSFADE_SAMPLES,
    _KOKORO_LANG_BY_PREFIX,
    _LOUDNESS_KNEE,
    _LOUDNESS_MAX_GAIN,
    _LOUDNESS_MIN_GAIN,
    _LOUDNESS_TARGET_RMS,
    _PAUSE_CLAUSE_MS,
    _PAUSE_EDGE_MS,
    _PAUSE_PARAGRAPH_MS,
    _PAUSE_SENTENCE_MS,
    _blend_voice_tensor,
    _configured_blend,
    _fade_edge_pcm,
    _float_to_pcm,
    _join_with_pause,
    _kokoro_speed,
    _loudness_enabled,
    _loudness_target_dbfs,
    _normalize_pcm,
    _normalize_samples,
    _parse_hhmm,
    _pause_pcm,
    _pause_sample_count,
    _pcm_edge_silence,
    _pcm_to_float,
    _trim_chunk_pcm,
    _utterance_loudness_pcm,
    _whisper_factor,
    kokoro_lang_for_voice,
    whisper_active_now,
)  # INTENTIONAL (batch 1): facade re-exports
# of the moved group — importers resolve through the facade.


# --- Adaptive VAD floor (v5.44, EARS A3) ------------------------------
# One fixed RMS number cannot fit every room on Earth: a quiet
# study wants the user's configured floor, a noisy kitchen wants
# a higher one or room tone alone keeps tripping the gate. The
# estimator below tracks chunk RMS while the session is idle-
# listening (never while an utterance is open, never while she
# speaks — both are speech, not room), keeps ~10 s of history,
# and reads the floor as ~the 10th percentile of that window
# (robust: a few loud events in the window barely move it, unlike
# a mean). The EFFECTIVE utterance-start threshold is
#     max(config floor, noise_floor x VAD_NOISE_MULTIPLIER)
# capped at VAD_NOISE_CAP_FACTOR x the config floor, so a broken
# or adversarial room estimate can raise the bar at most 4x past
# the user's own knob — adaptation can never deafen her outright.
                               # trusted and the config floor rules
                               # alone (a speech-first session must
                               # never adapt itself deaf on chunk 1)


# --- Echo discipline timing (v5.44, EARS A4) -------------------------


# --- Transcription settings (v5.43, EARS A1) -------------------------
# The faster-whisper call used to be a bare transcribe(audio,
# beam_size=1): greedy decode, Whisper's own language auto-detect
# on every utterance, thresholds left implicit, and no biasing —
# on a known user's known language, that gives away accuracy the
# hardware already paid for. _transcription_settings() below is
# the ONE resolver producing the call's kwargs:
#   language          from the transcription profile (core/
#                     languages.resolve_transcription_profile —
#                     preferred_language locked to its Whisper
#                     code; None only for Auto + an unmappable
#                     locale, the explicit auto-detect escape);
#   beam_size         WHISPER_BEAM_SIZE for the final decode;
#   no_speech /       faster-whisper's own defaults (0.6 / -1.0),
#   log_prob          passed BY NAME so the choice is deliberate
#                     and greppable, not inherited silently;
#   initial_prompt    a short biasing snippet (her name, the
#                     user's name when memory knows it, common
#                     command terms + app names). Whisper prompts
#                     are style bias, not instructions — this one
#                     simply makes the vocabulary she is most
#                     likely to hear cheaper to decode.
                               # MEASURED (v5.43, this VM's CPU):
                               # faster-whisper small / int8 on a
                               # 23.4 s real-speech clip, median of
                               # 3 after warm-up — beam 1: 3.94 s,
                               # beam 3: 4.56 s (1.16x), beam 5:
                               # 5.10 s (1.30x beam 1). 1.30x is
                               # far under the ~2.5x gate that
                               # would have forced beam 3, so the
                               # final decode gets the full 5.


# --- Brain tuning ---
                               # core/config.py's local_tool_rounds
                               # field (LOCAL_TOOL_ROUNDS) is the
                               # live value; this constant is the
                               # fallback when the field is absent.
                               # turn (v4.3): a reply that fails to parse
                               # as a tool call, or whose arguments fail
                               # schema validation in tools.execute(),
                               # earns at most this many repair attempts —
                               # each fed the exact error — before the
                               # turn ends with TOOL_SHAPE_FALLBACK.
                               # 8192 — the KV cache at 8192 cost ~1 GB
                               # on the 4B for conversations that never
                               # get near either figure; no n_batch is
                               # set, llama.cpp's default doesn't scale
                               # with n_ctx, so nothing else moves).
                               # The value actually loaded resolves via
                               # local_assets.effective_n_ctx() (v4.1:
                               # explicit LLM_N_CTX wins, ≤10 GB RAM
                               # → 2048, else this 4096)

# --- Idle memory management (v3.2) ---
                               # model (a local VYUK sitting open all
                               # day must not hold gigabytes hostage).
                               # The threshold actually used resolves
                               # via local_assets.effective_idle_
                               # unload_s() (v4.1: 300 s on ≤10 GB PCs)

# Prompt appended by send_image_frame when OCR reads NOTHING from the
# frame (v2.7: frames are OCR-read first — see _describe_frame; this is
# the honest fallback for a frame with no readable text, so the model
# says so instead of hallucinating image contents).

# Brain-quality guard (v5.3): the local model once answered "what
# time is it" with "The time is teatime." — a fabricated live-state
# answer, delivered twice, no tool ever called. The protocol prompt
# now forbids it (see _render_tool_protocol's LIVE STATE rule), and
# the brain turns additionally run this post-hoc guard: a state
# question whose turn used NO tool gets one forced repair through
# the real registry, replacing the invented reply before it is
# spoken, shown, or stored.
#
# v5.43 (B11): the guard's class->tool map is wider than the
# original time/date class. Each class maps to a REGISTERED pure
# read — a setter or a wrong-scope tool must never pose as the
# read, so three candidate classes were checked against the
# registry and DROPPED: volume (set_volume_level / adjust_volume
# mutate; app_volume is per-app, explicitly not the master
# volume), screen brightness (set_brightness /
# screen_brightness_auto mutate; display_details carries no
# brightness; room_brightness measures the ROOM, not the screen),
# and job status (no background-job tool exists at all).
# Download status survives via download_eta, a pure read.
# Classes stay tight (status phrasings, not bare nouns): a false
# positive costs at most one extra read call, because the guard
# only fires when the turn used no tool at all.


# (class label, question pattern, forced read tool, its arguments)


# ---------------------------------------------------------------------------
# Adaptive thinking profiles (v5.45, BRAIN B13)
#
# One fixed sampling profile (512 tokens, temp 0.3) served every
# turn: control utterances paid reasoning prices, and reasoning
# questions starved at 512. Now the turn's shape picks the
# profile from core/config.py's B13 fields:
#   quick — state/control utterances ("what time is it", "turn
#           the volume up", "open notepad"): temp 0.2, 256 tokens.
#   think — reasoning-shaped utterances (why/how/explain/compare/
#           plan/calculate/debug…, long or multi-question turns)
#           and ANY turn after a failed repair: temp 0.55, 1024.
# The classifier is small, deterministic and pure.
# ---------------------------------------------------------------------------

from .local_client_classifier import (TurnProfileFeatures, features_from_text, profile_escalation_triggers, classify_turn_from_features, classify_turn_profile, escalate_turn_profile, _THINK_SHAPE_RE, _QUICK_SHAPE_RE)  # refactor P1 step 2 (classifier concern, moved verbatim)


# MN-2.8 (v5.66): local cost-ledger hooks. Local turns always
# cost 0 and are never capped — the ledger row exists so the
# degrade path (cloud -> local) is visible in the same ledger,
# and so a local turn is never mistaken for an unrecorded cloud
# turn. Ollama rows are exact when the server returns eval
# counts, estimated otherwise; builtin llama.cpp rows are
# estimated from text (that wrapper returns text only). Guarded:
# a ledger failure never breaks a turn.
from .local_client_cost import (_cost_ledger_local, _cost_prompt_text, _cost_record_local, _COST_LEDGER)  # refactor P1 step 2 (cost concern, moved verbatim)


# ---------------------------------------------------------------------- #
# Sentence chunking for streaming speech (v2.7)
#
# The speech path synthesizes sentence by sentence so the FIRST
# sentence starts playing while the rest are still synthesizing.
# _split_sentences() is the pure splitter: boundaries are sentence-
# ending punctuation followed by whitespace, with an abbreviation
# guard so "Dr. Smith" / "e.g. this" / "3.5 km" never split mid-
# thought. Chunks shorter than two words are merged into a neighbour
# (a lone "Yes." sounds broken synthesized on its own).
# ---------------------------------------------------------------------- #

from .local_client_chunking import (_split_sentences, _speech_token_estimate, _clause_segments, _split_monster_chunk, _merge_runts_in_paragraph, _rechunk_for_speech, _emphasis_chunks, _ssmd_break_strength, _rechunk_with_breaks, _ssmd_rate, _speed_units_for_span, _emphasis_tag_units, _prosody_tag_units, _ABBREV_WORDS, _SENTENCE_END_RE, _BREAK_NONE, _BREAK_SENTENCE, _BREAK_PARAGRAPH, _BREAK_CLAUSE, _BREAK_RANK, _RUNT_TOKENS, _MONSTER_TOKENS, _TARGET_MIN_TOKENS, _TARGET_MAX_TOKENS, _CLAUSE_SPLIT_RE, _EMPHASIS_SPAN_RE, _SSMD_BREAK_RE, _SSMD_PROSODY_RE, _SSMD_EMPHASIS_TAG_RE)  # refactor P1 step 2 (chunking concern, moved verbatim)


# ---------------------------------------------------------------------- #
# Goldilocks re-chunking for speech (v5.43, MOUTH M1 + M2 breaks)
#
# _split_sentences() above is a SENTENCE splitter, and sentence
# length is the wrong unit for synthesis: Kokoro rushes and flattens
# on very short chunks and degrades on very long ones. The speech
# seam (LocalLiveSession._speak) therefore runs the reply through
# _rechunk_for_speech() — a pure pre-pass over the FULL reply text
# (which _speak has up front, so streaming order and first-chunk
# immediacy are untouched: chunk 1 is a pure function of the text,
# produced before any synthesis starts, and it always begins with
# the reply's first sentence).
#
# TOKEN PROXY: tokens are ESTIMATED from words — est_tokens =
# ceil(words x 4/3), the standard English heuristic (1 token is
# roughly 0.75 words). All band edges below are in these estimated
# tokens:
#   * a chunk under _RUNT_TOKENS (15) is a RUNT and merges into a
#     neighbour in the SAME paragraph (forward first, a trailing
#     runt backward — paragraph breaks are hard walls, so a topic
#     break is never merged away);
#   * a chunk over _MONSTER_TOKENS (400) splits at clause
#     boundaries (commas, semicolons, em-dashes, conjunctions),
#     the pieces packed greedily toward the _TARGET band
#     (100–200): a piece closes once it holds >= 100 and the next
#     clause would push it past 200;
#   * a chunk already inside [15, 400] is left exactly as split —
#     a reply whose chunks are all in-band emerges chunk-identical.
#
# Each returned chunk is (text, break_before) where break_before is
# _BREAK_NONE (the reply's very first chunk), _BREAK_CLAUSE (v5.58:
# a clause-split continuation of one monster sentence — "within a
# thought" but with a short clause pause, distinct from the
# crossfade-only emphasis sub-chunks below), _BREAK_SENTENCE, or
# _BREAK_PARAGRAPH (the source text had a blank line before it).
# M2's breathing pauses key off this strength at the consumer join.
# ---------------------------------------------------------------------- #

# Pause-length rank for the "stronger break wins" rule in
# _rechunk_with_breaks: a paragraph break outranks a sentence
# break outranks a clause break outranks none. (The numeric
# values above are NOT in pause order — _BREAK_CLAUSE was added
# after the shipped values — so the rank is spelled out here
# instead of max()ing the raw values.)


# ---------------------------------------------------------------------- #
# SSMD-subset tags (v5.58, Wave 3, MO-9): brain-emitted markup in
# the reply text. The subset is defined in core/voice/mouth.py
# (parse_ssmd_tags): ONLY <break time="Nms"/>, <prosody
# rate="R">...</prosody> and <emphasis>...</emphasis> are
# honoured; every other tag is stripped, never spoken. The
# patterns below mirror the module's (its parse_ssmd_tags is
# NOT reused as the unit source: it drops <break> values into
# an unused list and returns prosody/emphasis segments out of
# document order across tag types, so the render site walks
# the spans itself, in order, using the module's bands for the
# break mapping — see _ssmd_break_strength).
# ---------------------------------------------------------------------- #

# Any markup-like tag: "<" followed by a letter or "/" and a
# closing ">". Deliberately NOT bare "<" (a literal comparison
# such as "a < b" has a space after the bracket and never
# matches, so comparisons pass through untouched).
from .local_client_verbal import (_int_to_words, _indian_to_words, _indian_comma_pattern, _grouped_int_words, _year_to_words, _number_to_words, _ordinal_word, _ordinal_to_words, _url_spoken, _path_spoken, _currency_spoken, _percent_spoken, _number_spoken, _acronym_spoken, _pronunciation_map, _apply_pronunciations, _speech_text, _ssmd_display, _ONES, _TENS, _ORDINAL_IRREGULAR, _FENCE_RE, _FENCE_OPEN_RE, _IMAGE_RE, _LINK_RE, _INLINE_CODE_RE, _HEADING_RE, _QUOTE_RE, _BULLET_RE, _HR_RE, _BOLD_STAR_RE, _BOLD_UNDER_RE, _STRIKE_RE, _ITALIC_STAR_RE, _ITALIC_UNDER_RE, _URL_RE, _PATH_RES, _CURRENCY_WORDS, _CURRENCY_SINGULAR, _CURRENCY_RE, _PERCENT_RE, _ORDINAL_RE, _NUMBER_RE, _SPELL_OUT, _ACRONYM_RE, _ABBREVIATIONS, _DEFAULT_PRONUNCIATIONS, _MARKUP_TAG_RE)  # refactor P1 step 2 (verbalisation concern, moved verbatim)


# ---------------------------------------------------------------------- #
# Speech-text normalizer (v5.1 "mouth craft")
#
# _speech_text() rewrites a reply chunk into what the TTS engines
# should SAY. It is applied ONLY on the builtin backend's speech path
# (Kokoro / Piper / SAPI inside _speak) — the chat transcript, the
# memory log and the Ollama backend always keep the model's verbatim
# text. Design rule, in order: strip what is pure markup, expand what
# a listener can't see (numbers, symbols, abbreviations), shorten
# what nobody reads aloud (URLs, file paths) — and when in doubt,
# leave the text unchanged rather than mangling it. Pure and
# deterministic, so the whole battery is unit-testable.
# ---------------------------------------------------------------------- #


# Markdown that must never reach the engines. Emphasis patterns anchor
# on non-space content so prose like "2 * 3 * 4" is left alone.


# The lookarounds keep ranges ("3-5"), hyphenates ("covid-19"), dates
# ("10/21") and version strings ("2.5.1") from being half-converted.


# ALL-CAPS tokens of 2–4 letters: a small always-spell set plus any
# token with no vowel (initialisms like TTS / IIT) are spelled out;
# pronounceable ones (NASA, RAM) pass through for the engine to say.


# Shipped default pronunciations (v5.43, MOUTH M5). Every entry
# was checked against the installed kokoro-onnx phonemizer
# (bm_george / en-gb) before seeding: an entry is here only when
# the default G2P reading is wrong, mangled by the acronym pass,
# or worth pinning because the word is said constantly (her own
# name, the user's name, the home cities). "Pin" entries respell
# to the SAME phonemes the default already produces — they cost
# nothing today and keep the reading stable across voices and
# synthesis languages. The user's own entries (below) override
# or extend this table case-insensitively.


# ---------------------------------------------------------------------- #
# Speech DSP (v5.1 "mouth craft"): loudness, joins, fades, pace
#
# Synthesized sentences arrive at slightly different volumes and butt
# against each other at hard sample boundaries — the two things that
# make a local voice sound cheap. The helpers below work in the float
# domain at config.sample_rate_out (24 kHz): each chunk is normalized
# toward one target loudness with a soft ceiling, consecutive chunks
# in the streaming path are joined with an equal-power crossfade, and
# a barge-in cut is faded instead of clipped.
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------- #
# Breathing pauses (v5.43, MOUTH M2): shaped silence at chunk joins.
#
# The PCM rate here is the rate the consumer already handles:
# Kokoro emits 24 kHz natively (kokoro-onnx's SAMPLE_RATE is
# 24000) and _kokoro_synthesize resamples anything else to
# config.sample_rate_out (24000) before the consumer ever sees
# it — so pauses are built at config.sample_rate_out.
#
# Shape: the pause is PREPENDED to the FOLLOWING chunk's PCM (one
# _play_pcm call: [previous withheld tail][pause][chunk body]),
# never appended after the previous chunk. A queue drained by
# barge-in therefore takes the pending pause with it — an
# interrupted reply can never trail silence. Within a thought
# (break _BREAK_NONE: emphasis sub-chunks) there is no pause at
# all and the v5.1 equal-power crossfade in _join_chunk rules,
# exactly as before. v5.58 adds the clause pause: clause-split
# continuations (break _BREAK_CLAUSE) get the short clause
# silence instead of the crossfade. Click discipline: the
# withheld tail is faded OUT over its last ~5 ms before the
# silence and the chunk body is faded IN over its first ~5 ms, so
# neither silence edge is a sample cliff.
# ---------------------------------------------------------------------- #

# v5.58 (Wave 3, MO-12+11+16): the pause lengths are sourced
# from core/voice/mouth.py — the single source of truth for the
# prosody bands — not copied here. (mouth imports stdlib +
# numpy only and never imports this module, so this import
# cannot cycle.) Clause 200 ms sits in the 150–250 band,
# sentence 375 ms in 300–450, paragraph 750 ms in 600–900;
# the v5.43 values (280 / 500) sat OUTSIDE their bands.
from . import mouth as _mouth_v558  # Phase 3 V1b: local_client moved into core/voice/; mouth now intra-package; alias _mouth_v558 RESTORED (V1a-2 dropped it, breaking import with NameError at old line 878)


# ---------------------------------------------------------------------- #
# Output loudness standard (v5.51, M-2) + silence-edge trim (v5.51,
# M-3) — the maths lives in core/loudness.py (pure numpy; an honest
# RMS loudness standard, not LUFS); this section is only the config
# plumbing and the PCM-byte wrappers the speech paths call.
#
# M-2's rule: ONE gain per assembled utterance, decided AFTER
# assembly over the whole utterance and applied across all its
# chunks, so sentence boundaries cannot pump. While the standard
# is enabled, the legacy per-chunk v5.1 pass (_normalize_samples /
# _normalize_pcm at synthesis) is skipped at every render site —
# the post-assembly pass is the one and only gain. With
# config.tts_loudness_enabled off, _utterance_loudness_pcm is a
# strict pass-through and every render site runs its legacy v5.1
# pass exactly as before, so the audio path is byte-identical to
# pre-v5.51.
# ---------------------------------------------------------------------- #


# Kokoro voice ids encode their language in the first letter
# (v5.40): a* American English, b* British English, e* Spanish,
# f* French, h* Hindi, i* Italian, j* Japanese, p* Portuguese,
# z* Chinese. The synthesis language must follow the VOICE — a
# British voice fed "en-us" phonemizes with American rules, and a
# Hindi voice fed "en-us" cannot speak Hindi at all. Unknown or
# malformed ids fall back to "en-us", the historical behaviour.


# ---------------------------------------------------------------------- #
# Voice blending (v5.43, MOUTH M4 — machinery only; no blend ships
# configured and the default voice is untouched).
#
# The installed kokoro-onnx genuinely accepts a raw voice tensor:
# Kokoro.create()'s `voice` parameter is typed
# `str | NDArray[np.float32]`, and the wrapper resolves a string
# through its voices table (the voices .bin is an .npz of
# {name: (510, 1, 256) float32 style arrays}) or uses an array
# directly — verified against the installed package, whose
# _prepare() does exactly `if isinstance(voice, str): voice =
# self.get_voice_style(voice)`. So the blend is a weighted mix of
# two style arrays passed straight to create(); no generated
# voices file is needed.
#
# _blend_voice_tensor() is the pure core: a {name: array} voices
# mapping in, one blended float32 array out (or None when the
# blend cannot be honoured — unknown name, shape mismatch, a
# ratio outside (0, 1] — and the caller falls back to the plain
# named voice rather than failing the reply). ratio is the
# PARTNER's share: blended = (1 - ratio) * base + ratio * partner.
# NOTE (B-R1-3s, 2026-11-09): the blend tensor cannot cross the
# isolated child protocol (voice is a name there, not a tensor),
# so a configured blend renders the named base voice through the
# child and the dropped blend is recorded honestly (once per
# session) — _blend_voice_tensor / _BLEND_CACHE are now unused
# by the session path (dead code, owed cleanup).
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------- #
# Whisper hours (v5.17, M15): late-night politeness — inside a
# configurable window her voice is scaled down by
# config.whisper_factor at the enqueue funnel (_play_pcm), after
# loudness normalization, so she never booms through a quiet house.
# ---------------------------------------------------------------------- #


# ---------------------------------------------------------------------- #
# Local VLM frame description (v5.45, EYES E5b) — the built-in
# brain's real eyes, when the Quality-tier vision pair is on disk
# (core/voice/local_assets' VLM section: Qwen2.5-VL-3B GGUF + mmproj).
#
# The runtime seam is llama-cpp-python's mtmd chat handler for the
# model family (llama_cpp.llama_chat_format.Qwen25VLChatHandler):
# a DEDICATED Llama instance is built with the handler + the
# mmproj as its clip model, and the frame goes in as an OpenAI
# image_url data-URI content part. The brain's own Llama (text
# only, no handler) is never touched. The VLM instance is cached
# process-wide (it is ~2.8 GB on disk; Quality-tier machines are
# the >20 GB RAM ones by the tier rules) and loads lazily on the
# first describe.
#
# DEGRADE CONTRACT: describe_frame_vlm() returns None on EVERY
# failure — pair not provisioned, the installed llama-cpp build
# lacking the handler, a load failure, an inference failure, an
# empty reply — and _describe_frame then takes the OCR path
# exactly as before. Detection is by import + attribute probe,
# never by version-string guessing.
# ---------------------------------------------------------------------- #

from .local_client_vlm import (_VLM_DESCRIBE_PROMPT, _VLM_MAX_TOKENS, _vlm_llama, _vlm_llama_failed, _vlm_lock, vlm_handler_available, _vlm_mode, _ensure_vlm_llama, describe_frame_vlm, _VLM_OCR_SPARSE_CHARS, _vlm_prompt_for_frame, _ocr_frame_text, _frame_window_title, _result_to_text, _spoken_fallback, _FEED_TEXT_LIMIT, _feed_text, _executed_tool_succeeded, _format_action_trace)  # INTENTIONAL (batch 2): moved to local_client_vlm.py verbatim.
from .local_client_toolparse import (_TYPE_MAP, _convert_schema, _OLLAMA_TOOLS_BUDGET, _OLLAMA_FILL_DESC_CAP, _OLLAMA_CORE_DESC_CAPS, _to_ollama_tool, _cap_ollama_descriptions, _ollama_list_size, _to_ollama_tools, _balanced_json_object, _parse_tool_candidate, _extract_tool_call, _balanced_json_array, _parse_goal_action_reply, _extract_tool_calls, _TOOL_NAME_PROBE_RE, _tool_match_score, _closest_tool_hints, _invented_tool_name, _THINK_BLOCK_RE, _strip_think, _looks_like_tool_attempt)  # INTENTIONAL (batch 2): moved to local_client_toolparse.py verbatim.
from .local_client_budgets import (_RepairBudget, _DeliberationBudget, _ACTIVE_TOKENIZER_MODEL, _set_active_tokenizer, _clear_active_tokenizer, _active_tokenizer, _est_tokens, _tool_profile_name, _resident_priority_names, _format_tool_line, _tool_line, _INDEX_LEAD, _INDEX_LINE_WIDTH, _index_lines, _QUERY_STOPWORDS, _query_tokens)  # INTENTIONAL (batch 2): moved to local_client_budgets.py verbatim.
from .local_client_render import (_render_tool_protocol, _HALLUCINATION_PHRASES, _is_probable_hallucination, PRE_ROLL_S, _ring_pre_roll_pcm, _pre_roll_before_onset, _llama_load_state_path, _write_llama_load_state, _gpu_load_suspect)  # INTENTIONAL (batch 2): moved to local_client_render.py verbatim.
from .local_client_budgets import CORE_TOOL_PRIORITY  # INTENTIONAL (batch 2): re-export of the traveled toolbox import; keeps the facade namespace identical.


from .local_client_lifecycle import LocalLiveSessionLifecycleMixin  # INTENTIONAL (batch 3): lifecycle-prep mixin.
from .local_client_mic import LocalLiveSessionMicMixin  # INTENTIONAL (batch 3): mic-utterance mixin.
# BATCH 4 INTENTIONAL: batch-4 mixins (plan-goal, ollama-llama,
# builtin-turn) composed below; batch 5 closes the facade.
from .local_client_plangoal import LocalLiveSessionPlanGoalMixin
from .local_client_ollama import LocalLiveSessionOllamaMixin
from .local_client_builtin import LocalLiveSessionBuiltinMixin

from .local_client_speech import LocalLiveSessionSpeechMixin
from .local_client_voices import LocalLiveSessionVoicesMixin
from .local_client_frames import LocalLiveSessionFramesMixin

class LocalLiveSession(
    LocalLiveSessionLifecycleMixin,
    LocalLiveSessionMicMixin,
    LocalLiveSessionPlanGoalMixin,
    LocalLiveSessionOllamaMixin,
    LocalLiveSessionBuiltinMixin,
    LocalLiveSessionSpeechMixin,
    LocalLiveSessionVoicesMixin,
    LocalLiveSessionFramesMixin,
):
    """Local-brain session with GeminiLiveSession's public surface.

    The backend (config.local_backend) is fixed at start(): "builtin"
    runs the model in-process (llama.cpp + auto-downloaded assets);
    "ollama" talks to an external Ollama server (the v1.7 path).
    """

    #: GAPS-22 (B-V3/B-V4/B-V5): engine identity and the connected
    #: model (set at start in the lifecycle mixin — the effective
    #: GGUF file for builtin, the Ollama model name for ollama).
    #: ``release_model`` lives on the lifecycle mixin, next to the
    #: idle unload it reuses. ``_active_profile`` is initialised
    #: to None in that mixin's ``__init__`` (B-V8: explicit
    #: "undecided", never a silent "quick").
    engine = Engine.LOCAL
    connected_model = None

    # ------------------------------------------------------------------ #
    # Lite-tier multi-step gate (v5.49, programme item 36)
    # ------------------------------------------------------------------ #
    #: The one-breath warning (v5.49, programme item 36), worded
    #: once here and used for both run_plan and run_goal.
    LITE_PLAN_WARNING = (
        "The small on-device brain is unreliable at multi-step "
        "tasks — switch to a cloud brain or a bigger model for this.")
