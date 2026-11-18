"""Sweep Build 5 — sherpa code path, KWS/TTS only (code only, no model).

Stdlib only, no Qt, no network. This module deliberately does NOT import
``sherpa_onnx`` and adds no runtime dependency: it is the interface the app
can call, not a working KWS/TTS engine.

Licence basis (see ``gui/assets/SHERPA_LICENCE_READ.md`` for the full read):
sherpa_onnx 2.0.0, Apache-2.0, verified from the LICENSE file inside the
actual wheel (``sherpa_onnx-2.0.0.dist-info/licenses/LICENSE``, 11,358 bytes,
SHA-256 cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30),
NOT from a README quotation. That read covers the CODE only.

Hard limits, enforced here, not just documented:
  * Scope is KWS and TTS only, even when the gate is cleared.
  * Every model is a placeholder card stating "model licence read owed,
    not shipped" until its own card + licence read exists.
  * ``load_model()`` fails closed, always, in this build: without a licence
    read it is refused for that reason; with a licence read it is still
    refused because no model file is present in the tree. No model was
    downloaded, bundled, or shipped, and none is claimed to work.
  * Wake ships OFF, unchanged. This module never enables wake or capture,
    and it never touches a wake module.
  * No model file, no runtime, no GUI change, no sidebar entry, no frozen
    schema/cap change, no globe file is touched by this module.

Gate hook: ``licence_read_record()`` returns the evidence record in the shape
a funnel gate consumer would expect under its ``licence_read`` field. No gate
consumes this record today: Phase 4 deleted ``core/funnel/tools_gates.py``
and no ``sherpa_kws_tts`` gate item exists. Supplying the record to a gate
is the caller's act; this module never clears any gate by itself.
"""

from __future__ import annotations


PINNED_PACKAGE = "sherpa_onnx"
PINNED_VERSION = "2.0.0"
CODE_LICENCE = "Apache-2.0"
LICENCE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
LICENCE_FILE = "gui/assets/sherpa/LICENSE.txt"
LICENCE_READ_FILE = "gui/assets/SHERPA_LICENCE_READ.md"
SCOPE = ("kws", "tts")

# Wake is OFF and this build does not change that, by design.
WAKE_STATUS = "OFF"


def describe_code_path() -> dict:
    """Describe the code path honestly: interface only, no engine, no model."""
    return {
        "package": PINNED_PACKAGE,
        "version": PINNED_VERSION,
        "code_licence": CODE_LICENCE,
        "licence_sha256": LICENCE_SHA256,
        "licence_file": LICENCE_FILE,
        "scope": list(SCOPE),
        "runtime_installed": False,
        "runtime_imported": False,
        "model_loaded": False,
        "model_shipped": False,
        "wake_status": WAKE_STATUS,
        "status": "code_path_only",
        "note": "Interface only. No KWS/TTS end-to-end claim is made here.",
    }


def code_path_status() -> dict:
    """Status a caller (or the Doctor surface, later) can report verbatim."""
    return describe_code_path()


def licence_read_record() -> dict:
    """Evidence record in the shape a funnel gate consumer would expect
    under its ``licence_read`` field.

    This is the record itself, not a gate clearance: a gate consumer would
    check that the field is present and non-empty, and the caller decides
    whether to supply it. No gate consumes this record today (Phase 4
    deleted ``core/funnel/tools_gates.py``). Model licences are explicitly
    NOT part of this record.
    """
    return {
        "item": "sherpa_kws_tts",
        "package": PINNED_PACKAGE,
        "version": PINNED_VERSION,
        "code_licence": CODE_LICENCE,
        "licence_sha256": LICENCE_SHA256,
        "licence_file": LICENCE_FILE,
        "licence_read_file": LICENCE_READ_FILE,
        "source": "LICENSE file inside wheel "
                   "sherpa_onnx-2.0.0-cp312-cp312-manylinux2014_x86_64."
                   "manylinux_2_17_x86_64.whl (inspection only, not shipped)",
        "scope": list(SCOPE),
        "model_licence": "owed_per_model",
        "model_licence_note": "Each model needs its own card + licence read "
                              "before it can ship; none is cleared by this record.",
    }


def model_card(name: str, kind: str) -> dict:
    """Return a placeholder card for a named model. Never a loadable model.

    ``kind`` must be ``kws`` or ``tts`` (scope enforcement); anything else
    is refused at card creation, not at load time. ``licence_read`` is
    always ``None`` here: a placeholder has no read until one is done.
    """
    if kind not in SCOPE:
        return {
            "ok": False,
            "name": name,
            "kind": kind,
            "status": "refused_out_of_scope",
            "reason": "sherpa scope is KWS and TTS only; "
                      f"'{kind}' is out of scope",
            "licence_read": None,
        }
    return {
        "ok": True,
        "name": name,
        "kind": kind,
        "status": "not_shipped",
        "licence_read": None,
        "note": "model licence read owed, not shipped",
    }


def available_models() -> list:
    """Models shipped in the tree. Always empty in this build, by design."""
    return []


def kws_config() -> dict:
    """KWS config template: shape only, model deliberately ``None``."""
    return {
        "kind": "kws",
        "model": None,
        "model_file_present": False,
        "status": "not_loaded",
        "note": "No keyword model is shipped; configure only after a model "
                "card + licence read exists.",
    }


def tts_config() -> dict:
    """TTS config template: shape only, model deliberately ``None``."""
    return {
        "kind": "tts",
        "model": None,
        "model_file_present": False,
        "status": "not_loaded",
        "note": "No TTS model is shipped; configure only after a model "
                "card + licence read exists.",
    }


def load_model(card: dict) -> dict:
    """Attempt to load a model card. Fails closed, always, in this build.

    Refusal reasons are distinct so a caller can tell them apart:
      * no licence read on the card -> refused, licence-owed;
      * licence read present -> still refused, because no model file is
        present in the tree and no runtime is bundled.
    A successful load is impossible here; that is the point of this build.
    """
    if not isinstance(card, dict):
        return {"ok": False, "status": "refused",
                "reason": "model card must be a dict"}
    if card.get("kind") not in SCOPE:
        return {"ok": False, "status": "refused_out_of_scope",
                "reason": "sherpa scope is KWS and TTS only"}
    if not card.get("licence_read"):
        return {"ok": False, "status": "refused",
                "reason": "model licence read owed, not shipped; "
                          "card has no licence read"}
    return {"ok": False, "status": "refused",
            "reason": "no model file is present in the tree; "
                      "no sherpa model was downloaded, bundled, or shipped"}
