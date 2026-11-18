"""Wave 3 (v5.58) pilot / sound-event / translation licence verdicts — DATA,
not code that loads a model.

Follow the core/wake_licences.py precedent: every verdict below is a
record of a check against its PRIMARY source, made 2026-10-24. Nothing
here downloads, loads, or ships a model, and nothing here overrides the
Wave 2 CC-1 ship-gate audit. A verdict of "STILL-OWED" means the item
does NOT ship until the coordinator re-verifies it from the primary
source named in the verdict's closing step.

MOSS-TTS-Nano (VA-3) — STILL-OWED, pilot does NOT ship
--------------------------------------------------------
The primary sources CONFLICT, and under the commercial standing law
("nothing ships on an unverified licence") a conflict is not a
verification:

* GitHub repo page (github.com/OpenMOSS/MOSS-TTS-Nano, current commit
  8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb, read 2026-10-24): the
  sidebar badges the repository "Apache License 2.0 (Apache-2.0)" and
  the root file listing includes a `LICENSE` file.
* The SAME repo's README "License" section, and the Hugging Face model
  card (huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M), still carry
  the caveat verbatim: "This repository will follow the license
  specified in the root `LICENSE` file. If you are reading this before
  that file is published, please treat the repository as **not yet
  licensed for redistribution**."

The badge suggests a LICENSE file now exists, but its TEXT was not
directly readable through the fetch used for this check, and the
caveat text itself is still live on both primary pages. That leaves
two open questions a commercial ship cannot wave through: (1) does
the root LICENSE file actually contain Apache-2.0 text, and (2) does
it cover the 100M WEIGHTS and the ONNX export, or only the code?
Until both are answered from the file itself, O-6 is NOT resolved.
No MOSS inference seat is built in this workstream (see
core/tts_pilot_seats.py, where MOSS is a BLOCKED record only, with
no model-loading code, no download, and no weights).

Closing step: read the root LICENSE file text from
github.com/OpenMOSS/MOSS-TTS-Nano (or the HF card's licence field
once it is populated), confirm Apache-2.0, and confirm in writing
that it covers the MOSS-TTS-Nano-100M weights + ONNX export. If the
file is anything else, or weights are carved out, the seat stays
blocked and the coordinator re-records the verdict here.

Chatterbox (PR-9) — CLEAR (MIT), watermark disclosure implemented
------------------------------------------------------------------
* Primary source, code + project licence: github.com/resemble-ai/
  chatterbox — sidebar "License: MIT License (MIT)", root file
  listing includes a `LICENSE` file, current commit
  5de7a54aa4e5e2baadb0182dde554908b48b85c2 (read 2026-10-24).
* Primary source, vendor terms: Resemble AI's own model pages state
  Chatterbox Turbo "ships under the MIT license. You can use it in
  personal, research, and commercial projects — including closed-
  source products" and Chatterbox Nano is "MIT licensed ... Weights
  on GitHub and Hugging Face. No commercial restrictions."
* PerTh watermark: the same README has a "Built-in PerTh
  Watermarking for Responsible AI" section — every generated file
  carries an imperceptible neural watermark that survives MP3
  compression and editing. That is a BEHAVIOUR to disclose, not a
  licence problem, and no watermark stripper is built anywhere in
  this workstream. The disclosure string lives in
  core/tts_pilot_seats.py (CHATTERBOX_WATERMARK_DISCLOSURE) for
  docs/config text and for the GUI worker to surface; this
  workstream edits no GUI file.

Chatterbox therefore has a bake-off seat (core/tts_pilot_seats.py):
pilot-only, disabled by default, backend injectable, never wired
into the resident TTS pipeline, never promoted by code.

YAMNet v1 (EA-20/EA-26 sound events) — MACHINERY CLEAR, weights BYO
--------------------------------------------------------------------
* The sidecar (core/sound_event_sidecar.py) bundles NO weights, NO
  TensorFlow, and no model download: its classifier is an injected
  callable, and with no classifier injected it fires nothing.
* YAMNet's code lives in the tensorflow/models repository
  (research/audioset/yamnet), which is Apache-2.0; the TensorFlow
  Hub model page (tfhub.dev/google/yamnet/1) documents the 521-class
  AudioSet model but states no separate weight licence in the text
  fetched for this check. A YAMNet weight file (TFHub, TFLite, or
  ONNX export) therefore remains a bring-your-own backend: the
  coordinator must CC-1-clear the exact weight artefact before any
  real YAMNet backend is injected, exactly as Piper voices are
  BYO in the existing tree. The sidecar machinery itself ships
  behind the opt-in in core/sound_event_sidecar.py; it never
  claims a YAMNet weight is cleared.

Live translation (A8) — STILL-OWED, feature WAITS
--------------------------------------------------
No translation engine is named in the Wave 3 brief / kickoff
material available to this workstream, and no translation code
exists in the tree (the only "translate" hits are Morse/NATO
translators and coordinate translation). With no engine named
there is no primary source to verify against, so no licence
verdict can be given — and under the hard-block rule the feature
is NOT built on an unverified licence. No translation module,
tool, or wiring is added by this workstream.

Closing step: the coordinator names the candidate engine (project
+ repo/model-card URL). This workstream's successor then verifies
that engine's licence from that primary source (code licence AND
weights licence separately, plus any non-commercial / revenue-cap /
territory clause), records it here, and only then builds the
feature. If the named engine turns out non-commercial (the usual
trap for speech-translation models), translation stays waiting.

KittenTTS — UNTOUCHED (frozen seat)
-------------------------------------
Deliberately absent from every module in this workstream: no seat,
no licence read, no code. Its Wave 4 comparison freeze stands.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenceVerdict:
    """One licence verdict, with its primary source."""

    item: str
    verdict: str  # "CLEAR", "STILL-OWED", "MACHINERY-CLEAR"
    licence: str
    ships_in_v558: bool
    primary_source: str
    note: str
    closing_step: str


PILOT_LICENCE_VERDICTS: tuple[LicenceVerdict, ...] = (
    LicenceVerdict(
        item="VA-3 MOSS-TTS-Nano (100M, ONNX CPU TTS + voice cloning)",
        verdict="STILL-OWED",
        licence="Unresolved: GitHub badge Apache-2.0 vs live "
        "'not yet licensed for redistribution' caveat",
        ships_in_v558=False,
        primary_source=(
            "github.com/OpenMOSS/MOSS-TTS-Nano (commit "
            "8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb): sidebar "
            "Apache-2.0 badge + root LICENSE file listed, BUT README "
            "'License' section still says 'will follow the license "
            "specified in the root LICENSE file. If you are reading "
            "this before that file is published, please treat the "
            "repository as not yet licensed for redistribution'; "
            "huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M carries "
            "the same caveat verbatim"),
        note=(
            "O-6 is NOT resolved: the caveat text is still live on "
            "both primary pages and the LICENSE file's text was not "
            "directly readable in this check, so weights coverage "
            "(100M weights + ONNX export) is unproven. No MOSS "
            "inference seat is built; MOSS is a BLOCKED record only."),
        closing_step=(
            "Read the root LICENSE file text from the GitHub repo, "
            "confirm Apache-2.0, and confirm it covers the "
            "MOSS-TTS-Nano-100M weights + ONNX export; re-record "
            "the verdict here before any seat is built."),
    ),
    LicenceVerdict(
        item="PR-9 Chatterbox (expressive TTS + cloning seat)",
        verdict="CLEAR",
        licence="MIT",
        ships_in_v558=True,
        primary_source=(
            "github.com/resemble-ai/chatterbox (commit "
            "5de7a54aa4e5e2baadb0182dde554908b48b85c2): sidebar "
            "'License: MIT License (MIT)' + root LICENSE file; "
            "Resemble AI model pages (resemble.ai/learn/models/"
            "chatterbox-turbo, chatterbox-nano): MIT, commercial "
            "use including closed-source products, weights on "
            "GitHub/Hugging Face, no commercial restrictions; "
            "same README: 'Built-in PerTh Watermarking' section"),
        note=(
            "Weights MIT verified from the vendor's primary "
            "sources. PerTh watermark is a behaviour, disclosed "
            "in core/tts_pilot_seats.py, never stripped."),
        closing_step="None — seat ships as pilot-only (see tts_pilot_seats).",
    ),
    LicenceVerdict(
        item="EA-20/EA-26 YAMNet v1 sound-event sidecar machinery",
        verdict="MACHINERY-CLEAR",
        licence="Sidecar code: this tree's licence. YAMNet code: "
        "Apache-2.0 (tensorflow/models). Weights: BYO, not cleared",
        ships_in_v558=True,
        primary_source=(
            "tensorflow/models research/audioset/yamnet (Apache-2.0 "
            "repository); tfhub.dev/google/yamnet/1 (521 AudioSet "
            "classes; no separate weight licence stated in the "
            "fetched page text)"),
        note=(
            "The sidecar bundles no weights and no TensorFlow; "
            "its classifier is injected. A real YAMNet weight "
            "artefact needs its own CC-1 clearance before "
            "injection. With no classifier, the sidecar fires "
            "nothing — it never silently degrades to a guess."),
        closing_step=(
            "Coordinator CC-1-clears the exact YAMNet weight "
            "artefact (TFHub/TFLite/ONNX) before any backend is "
            "injected; record that artefact + verdict here."),
    ),
    LicenceVerdict(
        item="A8 live translation engine",
        verdict="STILL-OWED",
        licence="Unknown — no engine named",
        ships_in_v558=False,
        primary_source=(
            "None: no engine is named in the Wave 3 brief/kickoff "
            "material available to this workstream, and no "
            "translation code exists in the tree"),
        note=(
            "Translation WAITS while the rest of the wave ships. "
            "No translation module, tool, or wiring is added by "
            "this workstream."),
        closing_step=(
            "Coordinator names the candidate engine (project + "
            "repo/model-card URL); successor verifies code AND "
            "weights licences from that primary source, checks "
            "for non-commercial / revenue-cap / territory "
            "clauses, and records the verdict here before "
            "building the feature."),
    ),
    LicenceVerdict(
        item="KittenTTS seat",
        verdict="STILL-OWED",
        licence="Unread — seat FROZEN",
        ships_in_v558=False,
        primary_source=(
            "None read by this workstream, deliberately: the seat "
            "is frozen until Wave 4 (§11.4 comparison freeze)"),
        note="No work at all: no seat, no licence read, no code.",
        closing_step=(
            "Wave 4 comparison freeze lifts the seat; only then "
            "is its licence read from its primary source."),
    ),
)


def verdict_for(item_substring: str) -> LicenceVerdict | None:
    """The verdict whose item contains `item_substring`, or None."""
    needle = str(item_substring).lower()
    for verdict in PILOT_LICENCE_VERDICTS:
        if needle in verdict.item.lower():
            return verdict
    return None
