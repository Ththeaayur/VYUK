from __future__ import annotations

import numpy as np
from dataclasses import dataclass

from .mouth_lexicon_numbers import PERSONAL_LEXICON  # B30 INTENTIONAL: cross-part bare name(s) used by this family in the old module
from .mouth_prosody_render import segment_plan  # B30 INTENTIONAL: cross-part bare name(s) used by this family in the old module
from .mouth_script_hotwords import fuzzy_score  # B30 INTENTIONAL: cross-part bare name(s) used by this family in the old module

# ------------------------------------------------------------------ #
# VP-5.3 + VP-5.4 + VP-5.7: mishear repair (adopted stack only)
#
# Confidence-gated: a segment is a repair CANDIDATE only when
# its avg logprob is below the shipped threshold (-1.0) or its
# no-speech probability is above 0.6 (the shipped faster-whisper
# defaults, named in local_client). Repair re-ranks lexicon
# words by phonetic pre-key + fuzzy match and applies ONLY on
# a clear margin (>= REPAIR_MARGIN over the runner-up);
# otherwise the caller must CLARIFY (return value: None).
# Voice-undo + "repeat that" replay from the transcript log,
# never regenerated. INV-2/3/4: the undo window is replay-only
# and NEVER an execute bypass (undo_action() returns execute
# False unconditionally). Taint carve-out (§8 B-25): a repaired
# tainted action confirms EXACTLY as an unrepaired one — see
# confirm_descriptor(), whose gate fields are identical for
# repaired and unrepaired actions by construction.
# ------------------------------------------------------------------ #

REPAIR_LOGPROB_THRESHOLD = -1.0
REPAIR_NO_SPEECH_THRESHOLD = 0.6
REPAIR_MARGIN = 0.15
REPAIR_FUZZY_THRESHOLD = 0.75


@dataclass
class TranscriptSegment:
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0


def repair_transcript(segments: list[TranscriptSegment],
                      lexicon_words: list[str] | None = None
                      ) -> tuple[str, bool, float]:
    """Repairs low-confidence segments against the lexicon.
    Returns (text, repaired, margin). repaired is True only
    when a candidate segment's best lexicon match beats the
    runner-up by >= REPAIR_MARGIN; otherwise the segment is
    left verbatim and the caller must clarify. High-confidence
    segments are never touched."""
    words = list(lexicon_words) if lexicon_words else [
        e["word"] for e in PERSONAL_LEXICON]
    out: list[str] = []
    repaired_any = False
    best_margin = 0.0
    for segment in segments:
        low_conf = (segment.avg_logprob < REPAIR_LOGPROB_THRESHOLD
                    or segment.no_speech_prob > REPAIR_NO_SPEECH_THRESHOLD)
        if not low_conf:
            out.append(segment.text)
            continue
        scored = sorted(
            ((fuzzy_score(segment.text, w), w) for w in words),
            reverse=True)
        if not scored or scored[0][0] < REPAIR_FUZZY_THRESHOLD:
            out.append(segment.text)
            continue
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        margin = scored[0][0] - runner_up
        if margin >= REPAIR_MARGIN:
            out.append(scored[0][1])
            repaired_any = True
            best_margin = max(best_margin, margin)
        else:
            out.append(segment.text)
    return " ".join(out), repaired_any, best_margin


@dataclass
class TranscriptLogEntry:
    speaker: str  # "user" | "assistant"
    text: str
    action: str | None = None  # tool/action name, if any
    tainted: bool = False


def repeat_last(log: list[TranscriptLogEntry]) -> str | None:
    """'Repeat that': the last assistant utterance, replayed
    from the transcript log. Returns text only — it never
    regenerates speech text and never executes anything."""
    for entry in reversed(log or []):
        if entry.speaker == "assistant" and entry.text:
            return entry.text
    return None


def undo_action(log: list[TranscriptLogEntry]) -> dict | None:
    """Voice-undo descriptor for the most recent assistant
    action in the log. INV-2/3/4: this is a DESCRIPTOR, not
    an execution — 'execute' is False unconditionally, and
    the undo window can only replay/annotate, never run the
    action again or bypass confirmation. Returns None when
    there is no action to undo."""
    for entry in reversed(log or []):
        if entry.action:
            return {"action": entry.action, "execute": False,
                    "replay_text": entry.text,
                    "requires_confirmation": True}
    return None


def confirm_descriptor(action_text: str, repaired: bool,
                       tainted: bool = False) -> dict:
    """The confirmation gate descriptor for an action (§8 B-25
    taint carve-out). The gate fields are IDENTICAL whether or
    not the transcript was repaired: a repaired tainted action
    confirms exactly as an unrepaired one. Only the annotation
    field differs, and it never changes the gate."""
    return {
        "action": action_text,
        "confirm_required": True,
        "tainted": tainted,
        # Annotation only — never read by the gate.
        "transcript_repaired": repaired,
    }


# ------------------------------------------------------------------ #
# VP-4 frame (eval only) + VP-1.1/1.2 first-fragment (LOCAL only)
# ------------------------------------------------------------------ #

EVAL_DATASETS = ("Vistaar", "Kathbath", "MUCS")


def indic_conformer_status() -> dict:
    """IndicConformer: a dictation side-engine bake-off seat
    ONLY. It is REJECTED as the code-switch answer — the answer
    is SCRIPT-split routing (route_spans above)."""
    return {"engine": "IndicConformer",
            "role": "dictation side-engine bake-off seat only",
            "codeswitch_answer": False}


@dataclass
class WerSplit:
    """Hindi WER is ALWAYS reported split, never one number:
    separate WERs for Hindi, Hinglish and English. There is
    deliberately no combined-WER accessor on this class."""
    hindi: float
    hinglish: float
    english: float
    n_hindi: int = 0
    n_hinglish: int = 0
    n_english: int = 0

    def as_dict(self) -> dict:
        return {"hindi_wer": self.hindi, "hinglish_wer": self.hinglish,
                "english_wer": self.english, "n_hindi": self.n_hindi,
                "n_hinglish": self.n_hinglish,
                "n_english": self.n_english}


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate for one utterance (sub+del+ins)/N, the
    standard Levenshtein word-level definition."""
    ref = reference.split()
    hyp = hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        cur = [i]
        for j, hw in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if rw == hw else 1)))
        prev = cur
    return prev[-1] / len(ref)


def seed_hinglish_set() -> list[dict]:
    """Frozen Hinglish eval-set SCAFFOLDING with seeded prompt
    text only (§10 T13/T14). No audio exists in this
    environment: the prompts are here, the recordings are
    OWED (see field_recording_owed()). A suite must never
    claim these prompts are recordings."""
    prompts = [
        "Mujhe kal subah ka weather batao",
        "Play some lo-fi music please",
        "Mera calendar check karo for tomorrow",
        "Yeh file kahan save hui hai",
        "Set a reminder for chai time",
        "Bhai, thoda volume kam karo",
        "What is the time right now, batao na",
        "Mujhe ek accha sa joke sunao",
    ]
    return [{"id": f"hinglish-seed-{i:03d}", "prompt": p,
             "audio": None, "recorded": False}
            for i, p in enumerate(prompts)]


def field_recording_owed() -> dict:
    """The owed field step for §10 T13/T14, stated plainly:
    the Hinglish set must be recorded on HIS mic in HIS room.
    Until that happens, eval numbers are scaffold-only."""
    return {
        "owed": True,
        "step": ("Record the frozen Hinglish set on the user's "
                 "own microphone in his own room; re-score "
                 "Hindi/Hinglish/English WER split (WerSplit) "
                 "on those recordings."),
        "recordings_exist_here": False,
        "datasets": list(EVAL_DATASETS),
        "eval_only": True,
    }


def first_fragment(text: str) -> str:
    """VP-1.1/1.2 first-fragment yielding, stream2sentence
    style: the FIRST CLAUSE (not the first full sentence),
    including Devanagari danda delimiters (। ॥) as clause
    ends. The LOCAL path only — see yield_first_fragment()
    for the backend guard."""
    plan = segment_plan(text or "")
    if plan:
        return plan[0].text
    return (text or "").strip()


def yield_first_fragment(text: str, backend: str = "local") -> str:
    """First-fragment yielding for the LOCAL path ONLY.
    Explicitly NOT Gemini Live's native audio: a Live backend
    raises, because its first-audio timing is the provider's,
    not this yielding logic's."""
    if str(backend or "").lower() in ("live", "gemini-live",
                                      "gemini_live"):
        raise ValueError(
            "first-fragment yielding is LOCAL-only, not Live audio")
    return first_fragment(text)


def leading_silence_ms(pcm: bytes, sample_rate: int = 24000,
                       threshold_dbfs: float = -45.0) -> float:
    """Leading silence of a first fragment in ms. §10 T3
    needs leading silence <= 20 ms; this is the measurement
    for it (threshold = the shipped M-3 trim threshold)."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    thr = float(10.0 ** (threshold_dbfs / 20.0)) * 32768.0
    quiet = np.abs(samples) <= thr
    if bool(np.all(quiet)):
        return len(samples) / sample_rate * 1000.0
    return float(np.argmax(~quiet)) / sample_rate * 1000.0
