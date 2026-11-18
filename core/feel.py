"""Wave 8 Build 4 — Feel, non-veto only, inside the frozen UI core.

This module adds FEEL DATA AND RULES only. It adds no UI redesign,
no sidebar entry, no page, no renumbering, no asset file, no font
file, and no sound file. Feel proof is the owner's eye/ear verdict
on his machine; nothing here claims a lab render or a listening
test.

WHAT EACH PART IS, AND WHAT IT IS NOT
---------------------------------------
* Orb mapping: a pure function from a MEASURED audio level
  (0.0–1.0, supplied by the caller — this module measures no
  audio, captures no microphone, and watches nothing) to Aura
  orb parameters (scale, glow, pulse rate). Mapping only: no
  orb asset is bundled, no orb code is changed, and no orb
  licence is claimed. The Aura orb licence sighting is OWED
  (see FEEL_LICENCE_READ_W8.md); until it is sighted, the
  mapping is data waiting on that sighting, not a shipped orb.
* HUD rules: a validator for HUD descriptors. Colour rule —
  one blue accent, amber for waiting/confirm, red ONLY for
  stop/failure. Honesty rule — REUSED from Wave 8 Build 3
  (core/phase5_picks.describe_interface): a row carries a measured
  value or an explicit "unknown", never an estimate, sample,
  demo, or placeholder. This validator delegates row honesty
  to that function instead of inventing a second honesty rule.
* Procedural own sounds, sfxr method: a parameter set
  (waveform, frequency sweep, envelope, volume) plus a
  deterministic in-memory synthesiser (stdlib math/wave only).
  The sounds are GENERATED from parameters — no sample, no
  ripped audio, no bundled .wav is introduced by this build,
  and no pack is bundled at all. A sound PACK is only ever a
  ledger record, and only when the caller supplies a PRIMARY
  licence read (source + licence text); a pack without one is
  refused, never bundled, never claimed.
* HUD language: own copy (short status phrases). No font asset:
  B-R1-7 (2026-11-09) LANDED the OFL stack — Orbitron, Rajdhani
  and Share Tech Mono are primary-read per family and bundled in
  gui/assets/fonts/ with their OFL.txt files — so the font builds
  now. B-R1-7r (2026-11-09): the body face is Sora, not Rajdhani —
  the owner picked Sora ("whatever brings us close to scifi"). Space Grotesk pairing is VETOED and never used, and the
  Space Grotesk file was removed with the old stack in the same
  batch — presence in the tree was never clearance, and this
  build does not touch the veto.

VETOED, REFUSED WHOLE-CALL (any vetoed token anywhere in the
feel input refuses the whole call, no partial feel returned):
Charon, M4, earcon family, F6 signatures, Space Grotesk
pairing, any paid asset, ripped movie/game audio, Marvel
assets/trademarks/copied HUD art, character voice, catchphrase,
pretending to be the fictional assistant, any face (face,
portrait, selfie), any fake data.

CAPS: at most 100 rows / phrases / pack entries per call, at
most 2000 characters per text field, sound duration at most
3 seconds, sample rate 22050 Hz. Oversize calls refuse whole.

LICENCE / DEPENDENCIES: own code, standard library only
(math, hashlib, io, wave, re, json via callers). No new
dependency, no asset, no font, no sample, no feed, no network,
no file write, no database access. Nothing here reads or writes
the install tree's data/ directory.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import wave

from . import phase5_picks as _phase5

MAX_ITEMS = 100
MAX_TEXT = 2000
MAX_SOUND_SECONDS = 3.0
SAMPLE_RATE = 22050

# One blue accent, amber for waiting/confirm, red ONLY for
# stop/failure. These are this build's own HUD values, not
# copied HUD art from any film, game, or product.
BLUE_ACCENT = "#3A86FF"
AMBER = "#FFB020"
RED = "#E5484D"
_NEUTRALS = frozenset({"#000000", "#FFFFFF", "#1A1A1A", "#2B2B2B",
                        "#6B7280", "#9CA3AF", "#E5E7EB", "#F3F4F6"})

_STATE_ACCENT = {
    "idle": "blue",
    "active": "blue",
    "info": "blue",
    "waiting": "amber",
    "confirm": "amber",
    "stop": "red",
    "failure": "red",
}

_VETOED = ("charon", "m4", "earcon", "f6", "space grotesk",
           "marvel", "character voice", "catchphrase")
_FACE_WORDS = ("face", "portrait", "selfie")

# Own HUD copy — short status phrases, no character voice, no
# catchphrase, no claim to be the fictional assistant.
HUD_PHRASES = (
    "Ready.",
    "Listening.",
    "Working.",
    "Waiting for you.",
    "Confirm to continue.",
    "Done.",
    "Stopped.",
    "Something failed. Nothing was changed.",
    "Unknown. No value was measured.",
)

# Native-Qt particle budget for the 8 GB target, with graceful
# degradation stated, not silently applied: full -> reduced ->
# minimal -> off, each step narrated by the caller that applies
# it. Counts are budgets for the particle layer only, never a
# render claim — the lab cannot render Qt here.
PARTICLE_BUDGET = {
    "full": {"max_particles": 400, "degrades_to": "reduced",
             "note": "Full particle layer, 60 fps target."},
    "reduced": {"max_particles": 150, "degrades_to": "minimal",
                "note": "Reduced layer when a frame takes over 33 ms."},
    "minimal": {"max_particles": 40, "degrades_to": "off",
                "note": "Minimal layer on low-memory conditions."},
    "off": {"max_particles": 0, "degrades_to": None,
            "note": "Particles off; the orb and HUD still work."},
}

_SOUND_WAVES = frozenset({"sine", "square", "sawtooth", "noise"})
_ALLOWED_PACK_LICENCES = frozenset(
    {"cc0", "cc0-1.0", "cc-by", "cc-by-4.0", "cc-by-3.0"})
_BLOCKED_PACK_BITS = (
    "gpl", "agpl", "lgpl", "mpl", "nc", "nd", "non-commercial",
    "noncommercial", "revenue", "territory", "non-derivative")


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _check_text(value, field: str):
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string"
    if len(value) > MAX_TEXT:
        return f"{field} exceeds {MAX_TEXT} characters"
    return None


def _veto_scan(text: str):
    """Return the vetoed/face token found in text, or None."""
    lowered = str(text).lower()
    for token in _VETOED:
        if token in lowered:
            return token
    for word in _FACE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return word
    if "i am jarvis" in lowered or "i'm jarvis" in lowered:
        return "pretending to be the fictional assistant"
    return None


def orb_map(level) -> dict:
    """Map a MEASURED audio level (0.0–1.0) to orb parameters.

    Mapping only: no asset, no measurement, no capture. A level
    that is not a real number in range is refused whole.
    """
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return _fail("orb level must be a number, not a boolean or string")
    if math.isnan(level) or math.isinf(level):
        return _fail("orb level must be a finite number")
    if level < 0.0 or level > 1.0:
        return _fail("orb level must be between 0.0 and 1.0")
    return {
        "ok": True,
        "kind": "ORB_MAPPING",
        "level": float(level),
        "level_source": "measured (caller-supplied; this module measures nothing)",
        "scale": round(1.0 + 0.35 * level, 4),
        "glow": round(0.25 + 0.75 * level, 4),
        "pulse_hz": round(0.5 + 2.5 * level, 4),
        "accent": BLUE_ACCENT,
        "asset": "none — mapping only, no orb asset bundled",
        "orb_licence": "OWED: Aura orb licence sighting (see FEEL_LICENCE_READ_W8.md)",
    }


def particle_budget(tier) -> dict:
    """Return the particle budget + degradation step for a tier."""
    if tier not in PARTICLE_BUDGET:
        return _fail(
            f"particle tier {tier!r} unknown; tiers are "
            "full, reduced, minimal, off")
    entry = PARTICLE_BUDGET[tier]
    return {"ok": True, "kind": "PARTICLE_BUDGET", "tier": tier,
            "max_particles": entry["max_particles"],
            "degrades_to": entry["degrades_to"],
            "note": entry["note"],
            "render_claim": "none — budget only; no lab render is claimed"}


# B-R1-7 (2026-11-09): the three stack families, each primary-read
# in this build from its own OFL.txt in the google/fonts repo at
# pinned commit 5bb28733cec313b63fbd415b574b5ae35c8aa8b8 —
# Orbitron (OFL-Orbitron.txt), Rajdhani (OFL-Rajdhani.txt),
# Share Tech Mono (OFL-ShareTechMono.txt). B-R1-7r (2026-11-09):
# the body face is Sora, not Rajdhani — the owner picked Sora
# ("whatever brings us close to scifi"); Rajdhani is removed.
# The sighting is per family, never a blanket clearance: a family
# outside this table is refused rather than cleared by analogy,
# exactly as before.
_LANDED_FONTS = {
    "Orbitron": ("Orbitron-Variable.ttf", "OFL-Orbitron.txt",
                 "display / headings / HUD words"),
    "Sora": ("Sora-Variable.ttf", "OFL-Sora.txt",
             "body text"),
    "Share Tech Mono": ("ShareTechMono-Regular.ttf",
                        "OFL-ShareTechMono.txt", "code / numbers"),
}


def font_status(family: str = "Orbitron") -> dict:
    """Font status: the three stack families are LANDED and bundled.

    A family outside the landed table is refused rather than
    cleared by analogy — the OFL sighting is per family, never a
    blanket clearance. No font file is read here; the bundled
    files are asserted by the font suite (tests/test_google_fonts.py)
    and the prover, not by this status call.
    """
    if family not in _LANDED_FONTS:
        return _fail(
            f"font family {family!r} refused: only the landed "
            "stack families (Orbitron, Sora, Share Tech Mono) "
            "are in scope, each with its own primary-read OFL "
            "sighting — never cleared by analogy to another family")
    ttf, ofl, role = _LANDED_FONTS[family]
    return {"ok": True, "kind": "FONT_STATUS", "family": family,
            "status": "LANDED", "bundled": True, "role": role,
            "file": f"gui/assets/fonts/{ttf}",
            "licence": f"gui/assets/fonts/{ofl} (SIL OFL 1.1)",
            "source": "google/fonts 5bb28733cec313b63fbd415b574b5ae35c8aa8b8",
            "reason": "Primary-read OFL sighting in B-R1-7; the "
                      "font file is bundled in the tree."}


def validate_hud(descriptor) -> dict:
    """Validate a HUD descriptor against the colour + honesty rules.

    Row honesty is delegated to Build 3's describe_interface
    (measured value or explicit unknown, never fake data);
    this function adds only the colour/state rule, the veto
    scan, and the frozen-core checks that describe_interface
    already enforces (renumbering, new surfaces, faces).
    """
    if not isinstance(descriptor, dict):
        return _fail("HUD descriptor must be a JSON object")
    veto = _veto_scan(
        f"{descriptor.get('surface', '')} "
        f"{descriptor.get('language', '')} "
        " ".join(str(r.get('label', '')) for r in descriptor.get('rows', [])
                 if isinstance(r, dict)))
    if veto:
        return _fail(f"vetoed feel refused: {veto!r} never enters a HUD")
    accent = descriptor.get("accent")
    state = descriptor.get("state")
    if accent not in ("blue", "amber", "red"):
        return _fail(
            "HUD accent must be blue, amber, or red — one blue "
            "accent, amber for waiting/confirm, red only for "
            "stop/failure")
    if state not in _STATE_ACCENT:
        return _fail(
            f"HUD state {state!r} unknown; states are "
            + ", ".join(sorted(_STATE_ACCENT)))
    if _STATE_ACCENT[state] != accent:
        return _fail(
            f"HUD colour rule refused: state {state!r} takes "
            f"{_STATE_ACCENT[state]} accent, not {accent}; red is "
            "only for stop/failure, amber only for waiting/confirm")
    colour = {"blue": BLUE_ACCENT, "amber": AMBER, "red": RED}[accent]
    # Reuse Build 3's honesty shape for the rows (measured or
    # unknown, face refused, renumbering refused, confirm gated).
    base = _phase5.describe_interface(descriptor)
    if not base.get("ok"):
        return base
    language = descriptor.get("language")
    if language is not None:
        err = _check_text(language, "language")
        if err:
            return _fail(err)
        if language not in HUD_PHRASES:
            return _fail(
                "HUD language must be one of this build's own "
                "phrases; invented copy is refused so no "
                "catchphrase or character voice can enter")
    return {"ok": True, "kind": "HUD_DESCRIPTOR",
            "surface": base["surface"], "rows": base["rows"],
            "row_count": base["row_count"], "state": state,
            "accent": accent, "colour": colour,
            "language": language,
            "redesign": False, "renumbered": False,
            "honesty": "measured value or explicit unknown, per Build 3"}


def validate_sound_spec(spec) -> dict:
    """Validate an sfxr-method procedural sound parameter set."""
    if not isinstance(spec, dict):
        return _fail("sound spec must be a JSON object")
    veto = _veto_scan(f"{spec.get('name', '')} {spec.get('note', '')}")
    if veto:
        return _fail(f"vetoed feel refused: {veto!r} never enters a sound")
    err = _check_text(spec.get("name"), "sound name")
    if err:
        return _fail(err)
    wave_kind = spec.get("wave")
    if wave_kind not in _SOUND_WAVES:
        return _fail(
            f"sound wave {wave_kind!r} unknown; waves are "
            + ", ".join(sorted(_SOUND_WAVES)))
    for field in ("freq_start", "freq_end"):
        value = spec.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or math.isnan(value) or math.isinf(value) \
                or value < 20 or value > 20000:
            return _fail(f"sound {field} must be a frequency 20–20000 Hz")
    for field in ("attack", "sustain", "decay"):
        value = spec.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or math.isnan(value) or value < 0:
            return _fail(f"sound {field} must be seconds >= 0")
    duration = spec["attack"] + spec["sustain"] + spec["decay"]
    if duration <= 0 or duration > MAX_SOUND_SECONDS:
        return _fail(
            f"sound duration must be over 0 and at most "
            f"{MAX_SOUND_SECONDS} seconds")
    volume = spec.get("volume", 0.5)
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) \
            or math.isnan(volume) or volume < 0 or volume > 1:
        return _fail("sound volume must be between 0.0 and 1.0")
    return {"ok": True, "kind": "SOUND_SPEC",
            "name": spec["name"], "wave": wave_kind,
            "freq_start": spec["freq_start"], "freq_end": spec["freq_end"],
            "attack": spec["attack"], "sustain": spec["sustain"],
            "decay": spec["decay"], "duration": duration,
            "volume": volume, "method": "sfxr-style procedural synthesis",
            "asset": "none — generated from parameters, no sample bundled"}


def render_sound_wav(spec) -> dict:
    """Synthesise a validated spec to WAV bytes, in memory only.

    No file is written and no sample is used: samples are
    computed from the spec (sine/square/sawtooth/noise with a
    linear frequency sweep and an attack/sustain/decay
    envelope). The caller decides whether to keep the bytes;
    this module stores nothing. Noise uses a deterministic
    hash-based generator so the same spec renders the same
    bytes every time.
    """
    checked = validate_sound_spec(spec)
    if not checked.get("ok"):
        return checked
    count = int(checked["duration"] * SAMPLE_RATE)
    samples = []
    for i in range(count):
        t = i / SAMPLE_RATE
        if t < checked["attack"]:
            env = t / checked["attack"] if checked["attack"] else 1.0
        elif t < checked["attack"] + checked["sustain"]:
            env = 1.0
        else:
            tail = checked["decay"]
            env = max(0.0, 1.0 - (t - checked["attack"]
                                  - checked["sustain"]) / tail) if tail else 0.0
        freq = checked["freq_start"] + (
            checked["freq_end"] - checked["freq_start"]) * (t / checked["duration"])
        phase = 2.0 * math.pi * freq * t
        wave_kind = checked["wave"]
        if wave_kind == "sine":
            value = math.sin(phase)
        elif wave_kind == "square":
            value = 1.0 if math.sin(phase) >= 0 else -1.0
        elif wave_kind == "sawtooth":
            value = 2.0 * ((freq * t) % 1.0) - 1.0
        else:  # deterministic noise, same bytes for the same spec
            seed = hashlib.sha256(f"{checked['name']}:{i}".encode()).digest()
            value = (seed[0] / 127.5) - 1.0
        samples.append(int(max(-1.0, min(1.0, value * env * checked["volume"])) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        import struct as _struct
        wav_file.writeframes(b"".join(
            _struct.pack("<h", s) for s in samples))
    return {"ok": True, "kind": "SOUND_RENDER",
            "name": checked["name"], "sample_count": count,
            "peak": max((abs(s) for s in samples), default=0),
            "wav_sha256": hashlib.sha256(buf.getvalue()).hexdigest(),
            "wav_bytes": len(buf.getvalue()),
            "note": "WAV bytes are returned to the caller in memory; "
                    "no file is written and no sample is bundled"}


def register_sound_pack(pack) -> dict:
    """Ledger a sound pack ONLY on a caller-supplied primary read.

    A pack without primary_read=true, a source, and licence
    text is refused — never bundled, never claimed. A pack is
    a ledger record only; this function stores nothing and
    bundles nothing, so a future build that actually bundles a
    pack still needs its own primary read before it builds.
    """
    if not isinstance(pack, dict):
        return _fail("sound pack must be a JSON object")
    veto = _veto_scan(f"{pack.get('name', '')} {pack.get('note', '')}")
    if veto:
        return _fail(f"vetoed feel refused: {veto!r} never enters a pack")
    err = _check_text(pack.get("name"), "pack name")
    if err:
        return _fail(err)
    licence = str(pack.get("licence", "")).strip().lower()
    if licence not in _ALLOWED_PACK_LICENCES:
        return _fail(
            f"sound pack licence {licence!r} refused: only CC0 / "
            "CC-BY packs are ledgered, and copyleft, "
            "non-commercial, no-derivatives, revenue-capped, "
            "and territory-restricted labels are refused")
    if pack.get("primary_read") is not True:
        return _fail(
            "sound pack refused: no primary licence read — a "
            "pack is ledgered only with a caller-supplied "
            "primary read (source + licence text), never on a "
            "secondary claim")
    err = _check_text(pack.get("source"), "pack source")
    if err:
        return _fail(err)
    err = _check_text(pack.get("licence_text"), "pack licence_text")
    if err:
        return _fail(err)
    if licence.split("-")[0] not in pack["licence_text"].lower():
        return _fail(
            "sound pack refused: licence_text does not mention "
            "its own licence; the primary read must show the "
            "licence, not merely name it")
    return {"ok": True, "kind": "SOUND_PACK_LEDGER",
            "name": pack["name"], "licence": licence,
            "source": pack["source"],
            "bundled": False,
            "note": "Ledger record only: no pack file is bundled, "
                    "played, or distributed by this build"}


def validate_feel(feel) -> dict:
    """Validate a whole feel input; any refused part refuses all."""
    if not isinstance(feel, dict) or not feel:
        return _fail("feel input must be a non-empty JSON object")
    keys = set(feel)
    allowed = {"orb_level", "hud", "sound", "sound_pack",
               "particle_tier", "hud_language", "font"}
    unknown = keys - allowed
    if unknown:
        return _fail(f"feel input carries unknown parts: {sorted(unknown)}")
    out = {"ok": True, "kind": "FEEL_VALIDATION"}
    if "orb_level" in feel:
        result = orb_map(feel["orb_level"])
        if not result.get("ok"):
            return result
        out["orb"] = result
    if "hud" in feel:
        result = validate_hud(feel["hud"])
        if not result.get("ok"):
            return result
        out["hud"] = result
    if "sound" in feel:
        result = validate_sound_spec(feel["sound"])
        if not result.get("ok"):
            return result
        out["sound"] = result
    if "sound_pack" in feel:
        result = register_sound_pack(feel["sound_pack"])
        if not result.get("ok"):
            return result
        out["sound_pack"] = result
    if "particle_tier" in feel:
        result = particle_budget(feel["particle_tier"])
        if not result.get("ok"):
            return result
        out["particle_budget"] = result
    if "hud_language" in feel:
        phrases = feel["hud_language"]
        if not isinstance(phrases, list) or not phrases \
                or len(phrases) > MAX_ITEMS:
            return _fail("hud_language must be a non-empty list, at most 100")
        for phrase in phrases:
            if phrase not in HUD_PHRASES:
                return _fail(
                    f"HUD phrase {phrase!r} refused: only this "
                    "build's own phrases are allowed")
        out["hud_language"] = list(phrases)
    if "font" in feel:
        result = font_status(feel["font"])
        if not result.get("ok"):
            return result
        out["font"] = result
    out["licences"] = {
        "orb": "OWED (Aura orb licence sighting)",
        "font_orbitron": "LANDED (OFL 1.1, primary-read B-R1-7)",
        "font_rajdhani": "LANDED (OFL 1.1, primary-read B-R1-7)",
        "font_share_tech_mono": "LANDED (OFL 1.1, primary-read B-R1-7)",
        "sound_packs": "per-pack primary read, or refused",
    }
    out["feel_verdict"] = ("pending — feel proof is the owner's "
                           "eye/ear verdict on his machine, never "
                           "a lab claim")
    return out
