"""Wave 3 (v5.58) wake licence / training-data verdicts — DATA, not code
that loads a model.

Every verdict below is a record of a check against its primary source
(the upstream licence or README text), made 2026-10-24. Nothing here
downloads, loads, or ships a model, and nothing here overrides the
Wave 2 CC-1 ship-gate audit: a verdict of "STILL-OWED" means the item
does not ship until the coordinator re-verifies it from the primary
source named.

O-3 (ACAV / CC-BY-NC-SA-4.0) summary
-------------------------------------
* PR-12 incumbent (Vosk small en-us 0.15, the head already shipping):
  NO CC-BY-NC-SA-4.0 exposure established. The tree's Wave 2 CC-1
  record (THIRD_PARTY_LICENSES.md) lists the Vosk package and this
  model as Apache-2.0, commercial use permitted, and the upstream
  Vosk project is Apache-2.0. No upstream text found ties this
  model to a CC-BY-NC-SA dataset. Caveat, stated plainly: the
  model's published README benchmarks it on TED-LIUM / LibriSpeech
  test sets; that is evaluation data, not a training-data manifest,
  and Alpha Cephei does not publish a full training manifest for
  0.15 in the sources checked. The incumbent is NOT newly shipped
  by Wave 3 — it is the already-shipping baseline — so this caveat
  does not block the bake-off harness; it is recorded for CC-1.
* EA-14 LiveKit WakeWord head: EXPOSED, does NOT ship. The
  livekit-wakeword code is Apache-2.0, but its default training
  pipeline downloads the ACAV100M negative-feature set from
  davidscripka/openwakeword_features — openWakeWord's negative
  data — and its inference path uses frozen ONNX feature
  extractors derived from openWakeWord. openWakeWord's own README
  states, verbatim in substance: all code Apache-2.0, but ALL
  included pre-trained models are CC-BY-NC-SA-4.0 "due to the
  inclusion of datasets with unknown or restrictive licensing".
  A head trained on the default pipeline therefore inherits that
  exposure, and the task's own retraining escape hatch (clean
  self-owned negatives, including a >=25 h negative set for
  "Hey VYUK" itself) is out of scope for this build and has not
  been collected. No EA-14 head ships in v5.58.
* EA-15 WakeForge (alternate trainer): code Apache-2.0 (PyPI
  listing, wakeforge 0.9.0a1). It is a TRAINER, not a head: it
  ships no "Hey VYUK" head and no negative corpus, so it changes
  no verdict by itself. Any head it trained would need its own
  manifest check (see core/wake_trainer.py, which refuses any
  manifest containing a CC-BY-NC-SA or unknown-licence source).
* PF-16 voice lock engines: no engine closes. SpeechBrain's CODE
  is Apache-2.0, but that does not clear its pre-trained
  speaker-embedding WEIGHTS (ECAPA-TDNN, trained on VoxCeleb
  family data) for commercial redistribution, and Resemblyzer's
  pre-trained encoder carries the same weights-licence gap. Both
  would also be NEW pip dependencies (torch et al.), which Wave 3
  forbids. PF-16 therefore does NOT ship; see core/voice_lock.py,
  which is an on-device-only store gated to "unavailable" until a
  CC-1-cleared engine is injected. No hosted verifier is ever
  used: the module contains no network code at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenceVerdict:
    """One licence / training-data verdict, with its primary source."""

    item: str
    verdict: str  # "CLEAR", "EXPOSED", "STILL-OWED"
    licence: str
    ships_in_v558: bool
    primary_source: str
    note: str


WAKE_LICENCE_VERDICTS: tuple[LicenceVerdict, ...] = (
    LicenceVerdict(
        item="PR-12 incumbent head (Vosk vosk-model-small-en-us-0.15)",
        verdict="CLEAR",
        licence="Apache-2.0",
        ships_in_v558=True,
        primary_source=(
            "Tree THIRD_PARTY_LICENSES.md (Wave 2 CC-1 record: Vosk "
            "package + model Apache-2.0, commercial use permitted); "
            "upstream Vosk project, Apache-2.0 (alphacep/vosk-api)"),
        note=(
            "No CC-BY-NC-SA-4.0 exposure established. Caveat: no full "
            "training manifest for 0.15 is published in the sources "
            "checked; TED-LIUM / LibriSpeech are its benchmark sets, "
            "not a claimed training manifest. Incumbent already "
            "shipping; Wave 3 adds no new Vosk weights."),
    ),
    LicenceVerdict(
        item="EA-14 LiveKit WakeWord head (default pipeline)",
        verdict="EXPOSED",
        licence="Code Apache-2.0; default training data / frozen "
        "extractors inherit CC-BY-NC-SA-4.0",
        ships_in_v558=False,
        primary_source=(
            "livekit-wakeword PyPI listing (code Apache-2.0); "
            "livekit-wakeword training pipeline (setup downloads "
            "openwakeword_features_ACAV100M_2000_hrs_16bit.npy from "
            "davidscripka/openwakeword_features); openWakeWord "
            "README (all pre-trained models CC-BY-NC-SA-4.0 due to "
            "datasets with unknown or restrictive licensing)"),
        note=(
            "Default-pipeline head is tainted by ACAV100M negatives "
            "and openWakeWord-derived frozen extractors. Retraining "
            "on clean self-owned negatives (>=25 h for 'Hey VYUK': "
            "TV/dialogue/far-field) is out of scope and uncollected, "
            "so no EA-14 head ships in v5.58."),
    ),
    LicenceVerdict(
        item="EA-14 LiveKit WakeWord head (hypothetical clean retrain)",
        verdict="STILL-OWED",
        licence="Unproven until retrained + re-audited",
        ships_in_v558=False,
        primary_source=(
            "None yet: no clean-trained 'Hey VYUK' head exists in "
            "this tree, and no >=25 h self-owned negative set exists"),
        note=(
            "Closing step: collect the >=25 h clean negative set, "
            "retrain with --skip-acav (no ACAV100M, no openWakeWord "
            "pre-trained extractors), then re-run CC-1 on the "
            "resulting head before any ship decision."),
    ),
    LicenceVerdict(
        item="EA-15 WakeForge alternate trainer",
        verdict="CLEAR",
        licence="Apache-2.0 (code only)",
        ships_in_v558=False,
        primary_source=(
            "wakeforge 0.9.0a1 PyPI listing (License: Apache 2.0)"),
        note=(
            "Trainer code is licence-clean, but it is not a head: "
            "it ships no 'Hey VYUK' model and no corpus. Recorded "
            "as the alternate-trainer interface only "
            "(core/wake_trainer.py); no head ships on its behalf."),
    ),
    LicenceVerdict(
        item="PF-16 voice lock: SpeechBrain ECAPA-TDNN weights",
        verdict="STILL-OWED",
        licence="Code Apache-2.0; pre-trained weights licence "
        "unverified for commercial redistribution",
        ships_in_v558=False,
        primary_source=(
            "SpeechBrain PyPI/docs (code Apache-2.0, commercial "
            "redistribution permitted for the CODE); model card "
            "notes VoxCeleb-family training data with no commercial "
            "redistribution clearance found in the sources checked"),
        note=(
            "Closing step: CC-1 clearance of one specific weights "
            "file (not the toolkit) + approval of its new "
            "dependencies. Until then voice lock stays unavailable; "
            "no weaker substitute ships."),
    ),
    LicenceVerdict(
        item="PF-16 voice lock: Resemblyzer pre-trained encoder",
        verdict="STILL-OWED",
        licence="Weights licence unverified for commercial use",
        ships_in_v558=False,
        primary_source=(
            "Resemblyzer PyPI listing (encoder description; no "
            "commercial weights clearance stated in sources checked)"),
        note=(
            "Same closing step as SpeechBrain. A hosted Resemble "
            "verifier is excluded outright by the task's "
            "on-device-only condition, regardless of licence."),
    ),
)


def verdict_for(item_substring: str) -> LicenceVerdict | None:
    """The verdict whose item contains `item_substring`, or None."""
    needle = item_substring.lower()
    for verdict in WAKE_LICENCE_VERDICTS:
        if needle in verdict.item.lower():
            return verdict
    return None


def shipping_heads() -> list[str]:
    """Heads cleared to ship in v5.58 by the verdict table."""
    return [v.item for v in WAKE_LICENCE_VERDICTS if v.ships_in_v558]
