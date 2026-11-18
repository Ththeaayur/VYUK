"""Wave 9 (v5.64) — external sound pools: quarry registry,
per-asset licence log, and the audition gate.

THE GATE IS THE POINT. Every pool below ships as audition
material + licence log ONLY. Nothing in this module layers
an asset into product audio, enables an always-on bed, or
downloads anything — and no licence clearance in this
module can change that. Layering waits for the owner's
earcon veto ruling (the v5.51 veto, still pending). Until
he rules, attempt_layer() and enable_always_on_bed()
refuse fail-closed for EVERY asset, including CC0-cleared
ones: a cleared licence answers "may we use this at all",
the veto answers "may it sound like VYUK", and only the
owner can answer the second question. Pre-empting the veto
either way — layering a texture under a shipped earcon, or
switching on a bed — is exactly what this gate exists to
prevent.

No downloads: this module performs zero network I/O (no
urllib, requests, socket, or subprocess imports anywhere
in it) and writes only where the caller tells it to
(write_licence_log takes a caller-chosen path). Quarry
entries are REGISTRY entries — names, licence summaries,
and primary-source URLs — not audio files. No audio bytes
enter the tree through this module.

Licence verdicts, each from a primary source read in this
session (2026-10-25), never from metadata or snippets
alone — except Sonniss, where the primary page itself
blocked the read (see below), so Sonniss stays OWED:
- Kenney — VERIFIED CC0 (publisher's own bundle page,
  kenney.itch.io/kenney-game-assets-3: "Public domain
  license (suited for unlimited commercial projects)" and
  FAQ "all included game assets are public domain
  (Creative Commons Zero) licensed"). Caveat, kept in the
  record: the kenney.nl assets index fetch itself carried
  no licence text, so the clearance covers Kenney-authored
  packs and every log entry must still name its pack.
- Wyzowl SFX Kit — VERIFIED CC0 (publisher's own repo,
  github.com/wyzowl/sfx-kit: licence badge "Creative
  Commons Zero v1.0 Universal (CC0-1.0)" + README licence
  section "CC0 1.0 Universal: no rights reserved. Use the
  sounds and the data for anything, commercial or not";
  corroborated by wyzowl.com/sfx-kit FAQ "released under
  CC0 1.0"). Loudness caveat, kept in the record: the kit
  is matched on its own LUFS scale (per-sound levels such
  as -17.0 / -20.5 / -24.0 LUFS), NOT the shipped
  -18 dBFS standard — any A/B against shipped audio is
  invalid until re-matched (the same scale-mismatch
  warning the Wave 9 brief carries for kit loudness).
- Freesound — CC0-ONLY RULE, verified as a rule from the
  primary FAQ (freesound.org/help/faq, read in full):
  sounds carry exactly three licences (Creative Commons
  0, Attribution, Attribution NonCommercial) plus legacy
  Sampling+; the FAQ's own CC0 summary is "you can do
  pretty much what you want with the sound". The rule is
  therefore per-asset: an asset is quarry-eligible ONLY
  when its own sound-page/API record reads "Creative
  Commons 0". A search ranking, a pack name, or a
  uploader's word is never enough, and the validator
  rejects anything else.
- Sonniss — OWED PER PACK, not cleared. The GDC bundle
  licence is a royalty-free SYNC licence, not CC0, and it
  restricts supply: sounds may be synchronised inside a
  project (games, films, apps, interactive projects) but
  the right "does not extend to supplying the sound
  effects themselves" as files or as a device/service
  output — a live question for a product that ships UI
  audio files. The primary licence page
  (sonniss.com/gdc-bundle-license) returned a bot-check
  page in this session, so the EULA text was NOT read
  from the primary in this session and is NOT counted as
  clearance; the sync-licence shape above is recorded
  from the brief's own terms summary as an open question,
  not a verdict. Closing step per pack: read that pack's
  bundled licence file, confirm it permits embedding in
  a commercial app's UI audio, and only then log it.
- Generative beds — own DSP, no external licence owed.
  Still audition-gated: a bed that is always on is the
  most veto-sensitive sound of all, so it refuses exactly
  like every external asset until the owner rules.

Log validator rules (validate_record / validate_log):
- Copyleft (GPL/LGPL/AGPL, any share-alike) is ALWAYS
  rejected — the commercial standing law excludes
  copyleft from the distributed product, and no owner
  decision recorded here overrides that.
- BY / BY-NC (attribution, attribution-noncommercial)
  without a separate explicit owner decision is
  hard-rejected. With owner_decision=True the entry is
  recorded as decision-recorded-but-NOT-shippable: the
  decision is preserved in the log, the asset still cannot
  layer (veto gate), and non-commercial material still
  cannot ship in a commercial product without a
  commercial re-licence — the decision alone never makes
  it shippable.
- A record claimed "cleared" without verified_from_primary
  is rejected: clearance without a primary read is not
  clearance.
- Sonniss records are never "cleared" without a named
  pack licence file (pack_licence_file non-empty) plus a
  primary verification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"

#: The owner's v5.51 earcon veto is still pending. While
#: this is True, layering is refused for every asset.
#: Only an explicit owner ruling may flip it, and that
#: flip lives outside this module (a code change with
#: his decision recorded), never as a runtime toggle.
EARCON_VETO_PENDING = True

VETO_REFUSAL_REASON = (
    "Refused: the owner's earcon veto is still pending. "
    "No external sound — and no always-on bed — may be "
    "layered into product audio until he rules. This "
    "refusal is fail-closed: it applies to CC0-cleared "
    "assets exactly as it applies to unverified ones. "
    "Next step: the owner rules on the earcon family; "
    "only then may a layering path be opened, and only "
    "for licence-cleared assets."
)


# ================================================================ quarry
@dataclass
class Quarry:
    quarry_id: str
    name: str
    licence_summary: str
    source_url: str
    primary_evidence: str
    status: str  # verified_cc0 | cc0_only_rule | owed_per_pack | own_dsp_gated
    caveats: tuple = ()


QUARRIES = (
    Quarry(
        "kenney", "Kenney",
        "CC0 1.0 Universal (public domain)",
        "https://kenney.itch.io/kenney-game-assets-3",
        "Publisher's own bundle page (read 2026-10-25): "
        "'Public domain license (suited for unlimited "
        "commercial projects)'; FAQ: 'all included game "
        "assets are public domain (Creative Commons Zero) "
        "licensed'.",
        "verified_cc0",
        ("Clearance covers Kenney-authored packs; every "
         "log entry must still name its pack, because the "
         "kenney.nl assets index fetch itself carried no "
         "licence text.",
         "Audition-gated like every pool: CC0 clearance "
         "does not open the layering gate.")),
    Quarry(
        "sonniss", "Sonniss textures (GDC bundles)",
        "Royalty-free sync licence — NOT CC0",
        "https://sonniss.com/gdc-bundle-license/",
        "NOT verified from the primary in this session: "
        "the licence page returned a bot-check page. No "
        "EULA text from this session is counted as "
        "clearance. Recorded sync-licence shape (open "
        "question, not a verdict): synchronisation inside "
        "a project is granted; supplying the sounds "
        "themselves as files or device/service output is "
        "not.",
        "owed_per_pack",
        ("Per-pack closing step: read that pack's bundled "
         "licence file and confirm it permits embedding "
         "in a commercial app's UI audio.",
         "A royalty-free sync licence is not CC0 and is "
         "never logged as CC0.")),
    Quarry(
        "freesound", "Freesound (CC0-only quarry)",
        "Per-asset: Creative Commons 0 only",
        "https://freesound.org/help/faq/",
        "Primary FAQ read in full (2026-10-25): sounds "
        "carry Creative Commons 0, Attribution, or "
        "Attribution NonCommercial (plus legacy "
        "Sampling+); CC0 summary: 'you can do pretty much "
        "what you want with the sound'.",
        "cc0_only_rule",
        ("Eligibility is per-asset, from the asset's own "
         "sound-page/API record reading 'Creative Commons "
         "0' — never from search ranking or pack name.",
         "User-uploaded content carries a provenance "
         "caveat: the FAQ notes uploads may (unknowingly) "
         "include material the uploader did not own; "
         "flag-and-check stays part of audition.")),
    Quarry(
        "wyzowl", "Wyzowl SFX Kit",
        "CC0 1.0 Universal (public domain)",
        "https://github.com/wyzowl/sfx-kit",
        "Publisher's own repo (read 2026-10-25): licence "
        "badge 'Creative Commons Zero v1.0 Universal "
        "(CC0-1.0)'; README licence section: 'CC0 1.0 "
        "Universal: no rights reserved. Use the sounds "
        "and the data for anything, commercial or not'. "
        "Corroborated by wyzowl.com/sfx-kit FAQ: "
        "'released under CC0 1.0'.",
        "verified_cc0",
        ("Loudness mismatch: the kit is matched on its own "
         "LUFS scale (per-sound -17.0 / -20.5 / -24.0 "
         "LUFS), not the shipped -18 dBFS standard — any "
         "A/B against shipped audio is invalid until "
         "re-matched.",
         "Audition-gated like every pool: CC0 clearance "
         "does not open the layering gate.")),
    Quarry(
        "generative", "Generative beds (own DSP)",
        "Own work — no external licence",
        "",
        "No external source: beds generated by VYUK's own "
        "DSP (the Wave 9 procedural-soundscape seat). No "
        "third-party licence is involved.",
        "own_dsp_gated",
        ("Still audition-gated: an always-on bed is the "
         "most veto-sensitive sound, so it refuses like "
         "every external asset until the owner rules.",
         "Ducking under speech and the loudness standard "
         "are owed design decisions, not assumed here.")),
)

QUARRIES_BY_ID = {quarry.quarry_id: quarry for quarry in QUARRIES}


def quarry_summary():
    """One line per quarry: id, licence, status. For the
    handoff table — never a clearance on its own."""
    lines = []
    for quarry in QUARRIES:
        lines.append(f"{quarry.quarry_id}: {quarry.licence_summary} "
                     f"[{quarry.status}]")
    return lines


# ================================================================ log
@dataclass
class AssetLicenceRecord:
    """One per-asset log entry. ``licence`` is the licence
    string from the asset's own record (sound page, pack
    licence file, or repo LICENSE), normalised by the
    validator — never inferred from the quarry name.
    ``owner_decision`` records a separate explicit owner
    decision for BY/BY-NC entries; it defaults to False
    and is never assumed."""
    quarry: str
    asset: str
    licence: str
    source_url: str = ""
    verified_from_primary: bool = False
    owner_decision: bool = False
    pack_licence_file: str = ""
    loudness_matched: bool = False

    def to_dict(self):
        return {
            "quarry": self.quarry,
            "asset": self.asset,
            "licence": self.licence,
            "source_url": self.source_url,
            "verified_from_primary": self.verified_from_primary,
            "owner_decision": self.owner_decision,
            "pack_licence_file": self.pack_licence_file,
            "loudness_matched": self.loudness_matched,
        }


_COPYLEFT_MARKERS = ("gpl", "lgpl", "agpl", "copyleft",
                     "share-alike", "sharealike", "cc-by-sa",
                     "cc by-sa")
_NC_MARKERS = ("by-nc", "by_nc", "noncommercial",
               "non-commercial", "nc-")
_BY_MARKERS = ("cc-by", "cc by", "attribution")


def _normalised_licence(licence):
    text = (licence or "").strip().lower().replace("_", "-")
    text = " ".join(text.split())
    return text


def validate_record(record):
    """Validate one log entry. Returns (verdict, reason)
    where verdict is one of: 'cleared', 'owed',
    'decision_recorded_not_shippable', 'rejected'.
    - 'cleared' means the log entry is licence-clean for
      AUDITION material. It never means "may layer": the
      veto gate still refuses layering for cleared assets.
    - 'owed' means the entry is recorded but its licence
      is not closed (per-pack Sonniss, unverified claims).
    - 'decision_recorded_not_shippable' means a BY/BY-NC
      entry with an explicit owner decision: preserved in
      the log, flagged, never shippable as-is.
    - 'rejected' means the entry must not be logged as
      usable at all (copyleft; BY/BY-NC without a
      decision; a clearance claimed without a primary
      read)."""
    licence = _normalised_licence(record.licence)
    quarry = QUARRIES_BY_ID.get(record.quarry)
    if quarry is None:
        return ("rejected",
                f"unknown quarry: {record.quarry!r} — log "
                "entries must name a registered quarry.")
    if not record.asset.strip():
        return ("rejected", "asset name is empty.")
    if any(marker in licence for marker in _COPYLEFT_MARKERS):
        return ("rejected",
                f"copyleft licence ({record.licence!r}) — "
                "excluded from the distributed product by "
                "the commercial standing law; no owner "
                "decision recorded here overrides that.")
    is_nc = ("noncommercial" in licence
             or "non-commercial" in licence
             or "by-nc" in licence)
    is_by = (is_nc or "cc-by" in licence
             or licence.startswith("by ")
             or licence == "by"
             or "attribution" in licence) \
        and "cc0" not in licence and "zero" not in licence
    if is_by:
        if not record.owner_decision:
            return ("rejected",
                    f"BY/BY-NC licence ({record.licence!r}) "
                    "without a separate explicit owner "
                    "decision — hard-rejected. Next step: "
                    "record the owner's explicit decision "
                    "for this asset, or drop the asset.")
        return ("decision_recorded_not_shippable",
                f"BY/BY-NC licence ({record.licence!r}) with "
                "an explicit owner decision recorded. The "
                "entry is preserved, but the asset is NOT "
                "shippable in a commercial product without "
                "a commercial re-licence, and it still "
                "cannot layer while the earcon veto is "
                "pending.")
    if quarry.quarry_id == "sonniss":
        if not record.pack_licence_file.strip() \
                or not record.verified_from_primary:
            return ("owed",
                    "Sonniss: per-pack licence owed. Next "
                    "step: read that pack's bundled licence "
                    "file, confirm it permits embedding in "
                    "a commercial app's UI audio, and "
                    "record the file name here.")
    if quarry.quarry_id == "freesound":
        if licence not in ("cc0", "creative commons 0",
                           "cc0 1.0", "cc0 1.0 universal",
                           "creative commons zero",
                           "creative commons 0 1.0"):
            return ("rejected",
                    f"Freesound CC0-only quarry: licence "
                    f"({record.licence!r}) is not Creative "
                    "Commons 0. Only CC0 assets are "
                    "eligible; BY and BY-NC are rejected "
                    "by the quarry rule itself.")
        if not record.verified_from_primary:
            return ("owed",
                    "Freesound CC0 claim without a primary "
                    "read of the asset's own record. Next "
                    "step: read the sound page/API record "
                    "and confirm it reads 'Creative "
                    "Commons 0'.")
        return ("cleared",
                "Freesound CC0 asset, verified from the "
                "asset's own record. Audition material "
                "only — layering still refused while the "
                "earcon veto is pending.")
    if licence in ("cc0", "cc0 1.0", "cc0 1.0 universal",
                   "creative commons 0",
                   "creative commons zero",
                   "creative commons 0 1.0",
                   "public domain"):
        if not record.verified_from_primary:
            return ("owed",
                    "CC0 claim without a primary read. "
                    "Next step: read the pack/repo licence "
                    "text and confirm CC0 before logging "
                    "this asset as cleared.")
        return ("cleared",
                "CC0 asset, verified from a primary "
                "source. Audition material only — "
                "layering still refused while the earcon "
                "veto is pending.")
    if quarry.quarry_id == "generative":
        return ("cleared",
                "Own DSP bed: no external licence. "
                "Audition material only — always-on beds "
                "still refuse while the earcon veto is "
                "pending.")
    if not licence:
        return ("owed",
                "Licence not recorded. Next step: read the "
                "asset's own licence text and record it "
                "here before any use.")
    return ("owed",
            f"Licence ({record.licence!r}) is not closed "
            "by this validator. Next step: verify it from "
            "a primary source and re-log.")


def validate_log(records):
    """Validate a whole log. Returns a dict with per-asset
    verdicts plus accepted/rejected lists. 'Accepted' here
    means 'may sit in the audition log' (cleared +
    decision-recorded + owed entries are all preserved,
    each with its verdict); 'rejected' means 'must not be
    logged as usable'. Nothing in this result opens the
    layering gate."""
    results = []
    accepted = []
    rejected = []
    for record in records:
        verdict, reason = validate_record(record)
        entry = {"record": record.to_dict(),
                 "verdict": verdict, "reason": reason}
        results.append(entry)
        if verdict == "rejected":
            rejected.append(entry)
        else:
            accepted.append(entry)
    return {"results": results, "accepted": accepted,
            "rejected": rejected}


def write_licence_log(path, records):
    """Write the validated log as JSON to a caller-chosen
    path (tests use temp dirs; this module never picks a
    tree path itself). Returns (path, validation). The
    file is a log, not a clearance: rejected entries are
    written with their rejection, never silently dropped,
    so the log stays truthful about what was refused."""
    validation = validate_log(records)
    payload = {
        "module_version": MODULE_VERSION,
        "earcon_veto_pending": EARCON_VETO_PENDING,
        "entries": validation["results"],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return {"path": str(path), "validation": validation}


# ================================================================ gate
def audition_gate_status():
    """The gate, as data. Layering is allowed only when
    the veto is ruled AND the asset is licence-cleared —
    and since the veto is pending, the answer today is
    always no, for every asset."""
    return {
        "earcon_veto_pending": EARCON_VETO_PENDING,
        "layering_allowed": False,
        "always_on_beds_allowed": False,
        "reason": VETO_REFUSAL_REASON if EARCON_VETO_PENDING
        else "Veto ruled — layering is gated on licence "
             "clearance per asset (see validate_record).",
    }


def attempt_layer(record, cue=None):
    """Attempt to layer an asset into product audio.
    ALWAYS refuses while the veto is pending — this is
    the gate, not a playback function. There is no audio
    path behind it: no bytes are produced, mixed, or
    played, and no file is written. ``record`` may be an
    AssetLicenceRecord or a plain dict with at least an
    asset name; licence clearance is checked for the
    refusal note, but clearance never overrides the veto."""
    if isinstance(record, dict):
        asset = record.get("asset", "")
        licence = record.get("licence", "")
    else:
        asset = getattr(record, "asset", "")
        licence = getattr(record, "licence", "")
    verdict = ""
    if record is not None and not isinstance(record, dict):
        try:
            verdict, _reason = validate_record(record)
        except Exception:  # noqa: BLE001 — veto refusal stands
            verdict = "unvalidated"
    return {
        "layered": False,
        "refused": True,
        "produced_audio": None,
        "asset": asset,
        "cue": cue,
        "licence": licence,
        "licence_verdict": verdict,
        "reason": VETO_REFUSAL_REASON,
    }


def enable_always_on_bed(record=None):
    """Attempt to switch on an always-on bed. Always
    refuses while the veto is pending — beds are the most
    veto-sensitive sound, and 'always on' is the use the
    veto exists to rule on. No bed is started, no mixer
    state changes, no file is written."""
    asset = ""
    if isinstance(record, dict):
        asset = record.get("asset", "")
    elif record is not None:
        asset = getattr(record, "asset", "")
    return {
        "enabled": False,
        "refused": True,
        "produced_audio": None,
        "asset": asset,
        "reason": VETO_REFUSAL_REASON,
    }


__all__ = [
    "MODULE_VERSION",
    "EARCON_VETO_PENDING",
    "VETO_REFUSAL_REASON",
    "Quarry",
    "QUARRIES",
    "QUARRIES_BY_ID",
    "quarry_summary",
    "AssetLicenceRecord",
    "validate_record",
    "validate_log",
    "write_licence_log",
    "audition_gate_status",
    "attempt_layer",
    "enable_always_on_bed",
]
