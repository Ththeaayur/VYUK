"""Wave 3 "Voice Perfected" (v5.58.0) mouth/TTS workstream.

Pure, deterministic helpers for the TTS text-prep path and the
render pipeline. Stdlib + numpy only; NO new pip dependencies.
Nothing here is imported by the app at module-import time except
through core/voice/local_client.py's _speech_text / trim hooks, and
nothing here changes a shipped default unless the caller opts in
(see the trim-exemption note and the _speech_text hook notes).

LICENCE POSTURE (checked 2026-10-24, see the handoff for sources):
  * The IPA/respelling tables in this file are ORIGINAL entries
    authored for this build. No gruut lexicon database rows, no
    CMUdict rows, and no copied num2words/indic-num2words code
    are shipped here — so no third-party lexicon licence rides
    on this file. gruut's own lexicon licence was NOT verified
    from its primary LICENSE file in this environment, so the
    "gruut archived-MIT lexicon model" is NOT claimed as shipped;
    it stays STILL-OWED (closing step: read rhasspy/gruut's
    LICENSE + lexicon provenance from the repo itself).
  * num2words (savoirfairelinux) is LGPL-2.1-only per its package
    metadata — copyleft, so under the commercial standing law it
    is NOT added as a dependency and NO host-call adapter ships
    for it (the Wave 3 num2words_adapter was removed at the
    v5.58 CC-1 gate: a host-only LGPL call path is not worth a
    gate FAIL, and the in-tree layer covers the need).
    indic-num2words' licence was VERIFIED Apache-2.0 at the
    v5.58 CC-1 gate (1.3.2 sdist LICENSE + PKG-INFO), closing
    the Wave 3 STILL-OWED item. The en_IN / Hindi number layer
    in this file is therefore an ORIGINAL in-tree implementation
    (Indian grouping + the NeMo-TN-style context categories),
    with one lazy, opt-in adapter (indic_adapter) that calls
    that library ONLY if the host already has it installed. It
    is never imported by the shipped path and never a default.
  * pyloudnorm is NOT installed here and is NOT a dependency.
    pyloudnorm_lufs() is a MEASUREMENT probe only (BS.1770 via
    pyloudnorm when present, else None); it is never the
    loudness standard. The standard stays the shipped RMS
    target, -18 dBFS (core/loudness.py). MO-21 dynamic loudnorm
    for pre-rendered replies is BARRED and is not implemented
    anywhere in this file.
  * phrasplit / NeMo text-normalisation are DESIGN references
    only (clause chunking; currency/date/phone/ordinal/decimal
    context categories). No code from either is copied here.
  * fastText / langdetect are NOT used: code-switch routing is
    SCRIPT-split (Devanagari vs Latin), because LID-first
    mislabels Roman Hindi. No LID import exists in this file.
  * VP-5.1 stable-ts is dev/eval ONLY: no runtime function for
    it exists in this file, and no runtime path depends on it.
  * IndicConformer is a DICTATION side-engine bake-off seat
    only; it is REJECTED as the code-switch answer (see
    indic_conformer_status()).
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .mouth_lexicon_numbers import (  # B30 INTENTIONAL: facade re-export; moved code lives in mouth_lexicon_numbers
    _IPA_OK,
    PERSONAL_LEXICON,
    PERSONAL_RESPELLINGS,
    _IPA_MAP,
    validate_ipa,
    render_verify_lexicon,
    ipa_markup,
    _ONES,
    _TENS,
    _ORDINAL_IRREGULAR,
    _en_int,
    _en_indian,
    _en_ordinal,
    _ordinal_word,
    _HI_ONES,
    _hi_int,
    _hi_number,
    _MONTHS,
    _MONTH_NUM,
    _year_words,
    indian_amount_words,
    phone_digit_words,
    day_first_date_words,
    _RUPEE_RE,
    _PHONE_RE,
    _DATE_NUM_RE,
    _DATE_WORD_RE,
    indian_context_text,
    indic_adapter,
)

from .mouth_prosody_render import (  # B30 INTENTIONAL: facade re-export; moved code lives in mouth_prosody_render
    CLAUSE_PAUSE_MS,
    CLAUSE_PAUSE_MIN_MS,
    CLAUSE_PAUSE_MAX_MS,
    SENTENCE_PAUSE_MS,
    SENTENCE_PAUSE_MIN_MS,
    SENTENCE_PAUSE_MAX_MS,
    PARAGRAPH_PAUSE_MS,
    PARAGRAPH_PAUSE_MIN_MS,
    PARAGRAPH_PAUSE_MAX_MS,
    EMPHASIS_SPEED,
    DEFAULT_SPEED,
    LOUDNESS_TARGET_DBFS,
    _CLAUSE_RE,
    _SENTENCE_END_RE,
    _SSMD_BREAK_RE,
    _SSMD_PROSODY_RE,
    _SSMD_EMPHASIS_RE,
    _TAG_RE,
    prosody_pause_ms,
    clause_chunks,
    ProsodySegment,
    segment_plan,
    parse_ssmd_tags,
    strip_ssmd_to_plan,
    rms_dbfs,
    match_to_standard,
    crossfade_join,
    clause_boundary_join,
    silence_pcm,
    pyloudnorm_lufs,
)

from .mouth_script_hotwords import (  # B30 INTENTIONAL: facade re-export; moved code lives in mouth_script_hotwords
    DEVANAGARI_RE,
    MIN_SWITCH_WORDS,
    script_of_char,
    ScriptSpan,
    script_spans,
    route_spans,
    HOTWORD_CAP,
    HOTWORD_FUZZY_THRESHOLD,
    phonetic_key,
    fuzzy_score,
    HotwordEntry,
    hotwords_from_sources,
    HotwordCase,
    evaluate_hotwords,
    seed_frozen_hotword_set,
    propose_from_correction,
)

from .mouth_transcript_wer import (  # B30 INTENTIONAL: facade re-export; moved code lives in mouth_transcript_wer
    REPAIR_LOGPROB_THRESHOLD,
    REPAIR_NO_SPEECH_THRESHOLD,
    REPAIR_MARGIN,
    REPAIR_FUZZY_THRESHOLD,
    TranscriptSegment,
    repair_transcript,
    TranscriptLogEntry,
    repeat_last,
    undo_action,
    confirm_descriptor,
    EVAL_DATASETS,
    indic_conformer_status,
    WerSplit,
    wer,
    seed_hinglish_set,
    field_recording_owed,
    first_fragment,
    yield_first_fragment,
    leading_silence_ms,
)


