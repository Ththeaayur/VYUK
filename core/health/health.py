"""Health edition track (v5.69, plan §5.14, wave 20) — tracking,
noticing, reminding, organising. NEVER diagnosing, interpreting,
or presenting a score as a medical finding.

Head boundary (standing rule, outranks every item here): she
tracks, notices, reminds, and organises. Every user-facing string
this module returns was checked against that boundary: no
diagnosis words, no interpretation verbs, no vendor scores, no
recovery scores, no clinical staging, no screening labels, no
emotion/stress inference, no recommendations. A suite
(``test_v569_boundary.py``) re-checks every returned string on
every run — copy discipline is a gate, not polish.

What this module builds (plan §5.14 ADOPT/PILOT lines):
- A2≡E8 (built ONCE, §6 D-19): health-record file parsing. The
  parser EXTRACTS values; it never phrases them as interpretation.
- A4: Home-Assistant-mediated wearable readings, riding Wave 7's
  settled HM-1 route (consume-only, LAN-only, allowlist
  fail-closed — ``reach.is_lan_target`` is reused, not
  reimplemented). No new transport; the fetcher is injected, and
  with none the reading reports honestly unavailable.
- B4/B7/B8: sleep/training arithmetic on published formulas,
  computed LOCALLY over his own recorded numbers. Totals,
  averages, and counts only. B8's shortfall is a MINUTES figure
  against a USER-SUPPLIED target — never a score, never
  "recovery". No clinical staging anywhere.
- C2/C5: desk-body reminders. C5's output is a *reminder*, never
  a readiness fact. Camera-adjacent paths carry the S-6 posture
  (per-session opt-in + indicator + denylist, fail-closed,
  mirroring Wave 7's CA-1 session shape). C8's images are NOT
  built (provenance EXCLUDED until it clears) — the refusal is
  explicit and testable.
- D1/D5: reminder/organisation patterns. Streaks are COUNTS
  ("recorded N days in a row"), never verdicts. Reminders respect
  Wave 7 quiet hours (``daily.can_notify`` reused).
- D6: MediaPipe-Tasks posture, PILOT ONLY. Shipped MediaPipe
  sends metrics to Google with no documented opt-out (INV-19),
  so the pilot runs ONLY on caller-supplied proof of a legacy
  off-path + packet audit; otherwise it is refused outright.
  Copy ceiling: "posture reminder", nothing beyond. MediaPipe is
  never imported here.
- E1: FHIR-shaped records store, PARALLEL (own JSON file under
  DATA_DIR by default) — never an INV-8 schema mutation. The
  FHIR specification is CC0 (HL7's own licence page); the
  terminologies (SNOMED/CPT/ICD/LOINC) are EXCLUDED until
  separately licensed and are REFUSED at write time.
- E6/F2/F7: tracking/journal items. Journal text is
  USER-RECORDED, never inferred. F2's fork is MIT-verified, but
  its brand, logo, and name are trademark-EXCLUDED: no brand
  word appears in any user-facing string here.
- B6: sound-event notices ride Wave 3's EA-20 classes
  (``sound_event_sidecar``) — a notice only ("a sound was
  noticed"), never a screening label. The apnoea/snoring
  screening marketing shape does NOT ride along.
- D8: rides CA-1 (Wave 7, S-6 gated) — no new build; the suite
  proves the CA-1 gate rather than reimplementing it. D9's job
  is already done by Windows Night Light — sequencing only.

NEVER built here (rejected/boundary, joins §9 by reference):
F8 emotion/stress inference (science + RAVDESS CC BY-NC-SA +
EU AI Act Art. 5(1)(f)); A6 Oura as a route; cloud-mandatory
A5/A7/A8/C7 routes; Terra's custody model; actipy's Academic
Use Licence; BatesPosture (PolyForm Noncommercial); GPL/AGPL
shelves; dead Fitbit/Google Fit APIs; A3 layer-2 scoring /
coaching; E4 recommendation roadmap; E7 threshold judgements.

Library, not a tool: stdlib only, NO network I/O (no urllib,
requests, or HTTP client imports — a suite asserts that), NO
subprocess, NO executor import. Network reads accept an
injected ``fetcher`` callable; with none, they report
unavailable instead of sending a request.

Licence notes (plan §5.14 states; primary sources in
``health_track_studies.md``):
- GGIR: Apache-2.0 CONFIRMED (CRAN record: "License: Apache
  License (== 2.0) | file LICENSE"). Only published-formula
  arithmetic is reimplemented here in own code; no GGIR code
  is vendored or imported.
- FHIR specification: CC0 CONFIRMED (HL7's own licence page:
  "We dedicate the FHIR specification to the public domain
  using CC0 1.0"). The store shape follows the spec; no
  fhir.resources code is used (its BSD variant is EXCLUDED
  until separately licensed, as are the terminologies).
- C2 Apache / C5 MIT / D1-D5 BSD-2-or-pattern / F7 method /
  F2 fork MIT: plan-VERIFIED; this module reimplements the
  patterns in own code and vendors nothing.
- STILL-OWED, hard-blocking their sub-parts (none built):
  B8's target definition (so targets are user-supplied only);
  B5's licence text (MIT vs BSD-style); E6 MedTimer LICENSE
  (code reuse only — no reuse before it clears); C8 image
  provenance; D6 packet audit (the pilot demands it as input);
  G-series and PASTA-style items belong to the Research track,
  not this one.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import DATA_DIR  # Phase 3 H1: target stays flat in core/
from .. import daily as _daily  # Phase 3 H1: target stays flat in core/
from .. import reach as _reach  # Phase 3 H1: target stays flat in core/

__all__ = [
    "parse_health_record",
    "render_record",
    "wearable_reading",
    "summarise_nights",
    "sleep_shortfall",
    "summarise_sessions",
    "recorded_streak",
    "desk_reminder",
    "CameraReminderSession",
    "refuse_c8_images",
    "mediapipe_posture_pilot",
    "FhirStore",
    "JournalEntry",
    "notice_sound_event",
    "BoundaryRefused",
]

FHIR_STORE_FILENAME = "health_fhir_store.json"

# Terminology systems EXCLUDED until separately licensed (plan
# §5.14, E1). A write carrying any of these is refused outright.
EXCLUDED_CODE_SYSTEMS = (
    "snomed",
    "cpt",
    "icd",
    "loinc",
)

# Phrases that may never appear in a user-facing string from this
# module. The boundary suite re-checks every returned string
# against this list on every run. (Docstrings and comments may
# NAME the boundary; returned strings may not cross it.)
_BANNED_PHRASES = (
    "diagnos",
    "you have",
    "indicates",
    "indicating",
    "indication",
    "risk",
    "readiness",
    "score",
    "disorder",
    "condition",
    "treatment",
    "abnormal",
    "healthy",
    "unhealthy",
    "should see",
    "recommend",
    "apnoea",
    "apnea",
    "snoring",
    "stress",
    "emotion",
    "clinical",
    "staging",
    "stage ",
)


class BoundaryRefused(ValueError):
    """A value or request that would cross the head boundary."""


def _check_copy(text: str) -> str:
    """Refuse any user-facing string that crosses the boundary."""
    lowered = text.lower()
    for phrase in _BANNED_PHRASES:
        if phrase in lowered:
            raise BoundaryRefused(
                f"User-facing text crosses the head boundary "
                f"(banned phrase {phrase!r}); refusing.")
    return text


# ------------------------------------------------------------------
# A2≡E8 — health-record file parsing (built ONCE, §6 D-19).
# The parser EXTRACTS; it never interprets.
# ------------------------------------------------------------------

def parse_health_record(text: str) -> dict:
    """Extract records from a health-record file.

    Accepts a JSON object (or list of objects) with date/metric/
    value/unit keys, or ``DATE | METRIC | VALUE | UNIT`` lines.
    Unknown lines are counted as skipped, never interpreted.
    Returns extracted records plus a plain count note.
    """
    records: list[dict] = []
    skipped = 0
    stripped = (text or "").strip()
    if not stripped:
        return {"records": [], "skipped": 0,
                "note": _check_copy("No values found in the file.")}
    parsed = None
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            parsed = None
    if parsed is not None:
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                continue
            try:
                record = _coerce_record(item)
            except (KeyError, ValueError, TypeError):
                skipped += 1
                continue
            records.append(record)
    else:
        for line in stripped.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                skipped += 1
                continue
            try:
                records.append(_coerce_record({
                    "date": parts[0], "metric": parts[1],
                    "value": float(parts[2]), "unit": parts[3],
                    "source": parts[4] if len(parts) > 4 else "",
                }))
            except (ValueError, TypeError):
                skipped += 1
                continue
    return {"records": records, "skipped": skipped,
            "note": _check_copy(
                f"{len(records)} values extracted from the file, "
                f"as recorded.")}


def _coerce_record(item: dict) -> dict:
    date = str(item["date"]).strip()
    metric = str(item["metric"]).strip()
    value = float(item["value"])
    unit = str(item.get("unit", "")).strip()
    source = str(item.get("source", "")).strip()
    if not date or not metric:
        raise ValueError("record needs a date and a metric")
    return {"date": date, "metric": metric, "value": value,
            "unit": unit, "source": source}


def render_record(record: dict) -> str:
    """Phrase one extracted record as an observation, nothing more."""
    text = (f"On {record['date']}, {record['metric']} was recorded "
            f"as {record['value']} {record['unit']}.".replace(" .", "."))
    if record.get("source"):
        text += f" Source: {record['source']}."
    return _check_copy(text)


# ------------------------------------------------------------------
# A4 — Home-Assistant-mediated wearable readings (rides HM-1).
# Consume-only, LAN-only, allowlist fail-closed. No new transport.
# ------------------------------------------------------------------

def wearable_reading(entity_id: str, allowlist, host: str,
                    fetcher=None) -> dict:
    """Read one Assist-exposed wearable entity, as recorded.

    Gates, in order: the entity must be on the caller-supplied
    allowlist (fail-closed); the host must pass Wave 7's LAN gate
    (``reach.is_lan_target`` — reused, not reimplemented).
    With no injected fetcher, the reading reports honestly
    unavailable instead of sending a request.
    """
    allow = set(allowlist or ())
    if entity_id not in allow:
        return {"ok": False, "entity": entity_id,
                "error": _check_copy(
                    "Entity is not on the allowlist; reading refused.")}
    lan = _reach.is_lan_target(host)
    if not lan.ok:
        return {"ok": False, "entity": entity_id,
                "error": _check_copy(
                    "Home Assistant target is not on the local "
                    "network; reading refused.")}
    if fetcher is None:
        return {"ok": False, "entity": entity_id,
                "error": _check_copy(
                    "No reading source supplied; value unavailable.")}
    raw = fetcher(entity_id)
    if not isinstance(raw, dict) or "value" not in raw:
        return {"ok": False, "entity": entity_id,
                "error": _check_copy(
                    "Reading source returned no value.")}
    return {"ok": True, "entity": entity_id,
            "value": raw["value"],
            "unit": str(raw.get("unit", "")),
            "time": str(raw.get("time", "")),
            "note": _check_copy("Value as recorded by the device.")}


# ------------------------------------------------------------------
# B4/B7/B8 — sleep/training arithmetic, local, over his numbers.
# Totals, averages, counts only. No staging, no scores.
# ------------------------------------------------------------------

def summarise_nights(nights) -> dict:
    """Arithmetic over user-recorded nights.

    Each night: {date, in_bed_minutes, asleep_minutes}. Returns
    totals and averages plus one plain ratio (asleep minutes
    divided by in-bed minutes, as recorded) — arithmetic, not a
    finding. Nights with non-positive in-bed minutes are skipped
    and counted, never reinterpreted.
    """
    nights = list(nights or [])
    total_bed = 0
    total_asleep = 0
    counted = 0
    skipped = 0
    for night in nights:
        try:
            bed = float(night["in_bed_minutes"])
            asleep = float(night["asleep_minutes"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if bed <= 0 or asleep < 0:
            skipped += 1
            continue
        total_bed += bed
        total_asleep += asleep
        counted += 1
    ratio = (total_asleep / total_bed) if total_bed > 0 else None
    return {"nights_counted": counted, "nights_skipped": skipped,
            "total_in_bed_minutes": total_bed,
            "total_asleep_minutes": total_asleep,
            "average_asleep_minutes":
                (total_asleep / counted) if counted else None,
            "asleep_over_in_bed": ratio,
            "note": _check_copy(
                "Totals and averages from the nights you recorded.")}


def sleep_shortfall(nights, user_target_minutes) -> dict:
    """Minutes short of a USER-SUPPLIED nightly target, or unavailable.

    B8's target definition is STILL-OWED, so no target is ever
    invented here: with no user target, the shortfall is
    unavailable (not guessed). With one, the figure is minutes
    only — never a score, never "recovery".
    """
    if user_target_minutes is None:
        return {"ok": False,
                "error": _check_copy(
                    "No target set; shortfall needs a target "
                    "number you supply.")}
    try:
        target = float(user_target_minutes)
    except (TypeError, ValueError):
        return {"ok": False,
                "error": _check_copy("Target must be a number.")}
    summary = summarise_nights(nights)
    counted = summary["nights_counted"]
    if counted == 0:
        return {"ok": False,
                "error": _check_copy("No nights recorded.")}
    shortfall = max(0.0, target * counted - summary["total_asleep_minutes"])
    return {"ok": True, "nights": counted,
            "target_minutes_per_night": target,
            "shortfall_minutes": shortfall,
            "note": _check_copy(
                f"{shortfall} minutes short of your own target "
                f"across {counted} nights.")}


def summarise_sessions(sessions) -> dict:
    """Training arithmetic: counts and minutes only, no load scores."""
    sessions = list(sessions or [])
    total = 0.0
    counted = 0
    skipped = 0
    for session in sessions:
        try:
            minutes = float(session["minutes"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if minutes < 0:
            skipped += 1
            continue
        total += minutes
        counted += 1
    return {"sessions_counted": counted, "sessions_skipped": skipped,
            "total_minutes": total,
            "note": _check_copy(
                "Counts and minutes from the sessions you recorded.")}


# ------------------------------------------------------------------
# D1/D5 — organisation. Streaks are COUNTS, never verdicts.
# Reminders respect Wave 7 quiet hours.
# ------------------------------------------------------------------

def recorded_streak(days_recorded: int) -> str:
    """A streak as a count: 'Recorded N days in a row.'"""
    try:
        days = int(days_recorded)
    except (TypeError, ValueError):
        raise BoundaryRefused("Days recorded must be a whole number.")
    if days < 0:
        raise BoundaryRefused("Days recorded cannot be negative.")
    return _check_copy(f"Recorded {days} days in a row.")


def desk_reminder(quiet_hours=None, now=None) -> dict:
    """A desk-body reminder, gated by quiet hours when given.

    C5's output is a *reminder*, never a readiness fact. When a
    Wave 7 QuietHours window is supplied, a reminder inside it is
    held (alarms stay exempt by the daily module's own rule, but
    this is a reminder, not an alarm).
    """
    if quiet_hours is not None and not _daily.can_notify(
            quiet_hours, now):
        return {"ok": False, "held": True,
                "note": _check_copy(
                    "Reminder held during quiet hours.")}
    return {"ok": True, "held": False,
            "text": _check_copy(
                "Reminder: time to stand and stretch.")}


# ------------------------------------------------------------------
# C2 — camera-adjacent posture, S-6 shape (opt-in + indicator +
# denylist, fail-closed). Mirrors Wave 7's CA-1 session posture;
# the denylist here covers camera contexts, not process names.
# ------------------------------------------------------------------

class CameraReminderSession:
    """One per-session camera-reminder session (S-6 posture).

    Starts REFUSING: no opt-in, no indicator kind. The caller
    must call ``grant_opt_in()`` AND ``set_indicator(kind)``
    before any reminder can be issued. A denylisted context
    refuses the reminder outright. Nothing is captured, stored,
    or sent by this class — it gates the reminder text only.
    """

    _DEFAULT_DENYLIST = ("bedroom", "bathroom")

    def __init__(self, denylist=None):
        self._opt_in = False
        self._indicator = ""
        self._denylist = tuple(
            d.lower() for d in
            (denylist if denylist is not None
             else self._DEFAULT_DENYLIST))

    def grant_opt_in(self) -> None:
        self._opt_in = True

    def revoke_opt_in(self) -> None:
        self._opt_in = False

    def set_indicator(self, kind: str) -> None:
        self._indicator = str(kind or "")

    def reminder(self, context: str = "") -> dict:
        if not self._opt_in:
            return {"ok": False,
                    "error": _check_copy(
                        "Camera reminder needs your opt-in first.")}
        if not self._indicator:
            return {"ok": False,
                    "error": _check_copy(
                        "Camera reminder needs a visible indicator "
                        "first.")}
        if (context or "").strip().lower() in self._denylist:
            return {"ok": False,
                    "error": _check_copy(
                        "Camera reminder refused for this context.")}
        return {"ok": True,
                "text": _check_copy("Posture reminder.")}


def refuse_c8_images(reason: str = "") -> dict:
    """C8's image path is EXCLUDED until provenance clears.

    There is no image function in this module. This refusal
    exists so the exclusion is explicit and testable, not
    silent.
    """
    return {"ok": False, "built": False,
            "error": _check_copy(
                "Image path not built: provenance not cleared."
                + (f" {reason}" if reason else ""))}


# ------------------------------------------------------------------
# D6 — MediaPipe-Tasks posture, PILOT ONLY. Metrics flow to
# Google as shipped with no documented opt-out (INV-19), so the
# pilot runs ONLY on caller-supplied proof of a legacy off-path
# + packet audit. MediaPipe is never imported here.
# ------------------------------------------------------------------

def mediapipe_posture_pilot(legacy_path_proven: bool,
                            packet_audit_passed: bool) -> dict:
    """Pilot gate: both proofs required, else the pilot is dropped."""
    if not legacy_path_proven or not packet_audit_passed:
        return {"ok": False, "pilot": False,
                "error": _check_copy(
                    "Posture pilot dropped: telemetry-off path "
                    "not proven.")}
    return {"ok": True, "pilot": True,
            "text": _check_copy(
                "Posture reminder (pilot: legacy path, "
                "telemetry-off proven by packet audit).")}


# ------------------------------------------------------------------
# E1 — FHIR-shaped records store, PARALLEL. Own JSON file, never
# an INV-8 schema mutation. Terminologies refused at write time.
# ------------------------------------------------------------------

class FhirStore:
    """A parallel FHIR-shaped store (resourceType + fields only).

    The FHIR specification is CC0. No fhir.resources code is
    used, and no SNOMED/CPT/ICD/LOINC terminology is accepted:
    a write carrying an excluded code system is refused.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else (
            Path(DATA_DIR) / FHIR_STORE_FILENAME)

    def _load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, records: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2),
                             encoding="utf-8")

    def add(self, resource: dict) -> dict:
        """Store one FHIR-shaped resource, or refuse it."""
        if not isinstance(resource, dict):
            return {"ok": False,
                    "error": _check_copy("Resource must be an object.")}
        resource_type = str(resource.get("resourceType", "")).strip()
        resource_id = str(resource.get("id", "")).strip()
        if not resource_type or not resource_id:
            return {"ok": False,
                    "error": _check_copy(
                        "Resource needs a type and an id.")}
        for coding in resource.get("code", []) if isinstance(
                resource.get("code"), list) else []:
            system = str(coding.get("system", "")).strip().lower()
            # Fail-closed: versioned systems (ICD-10, ICD-11)
            # match their excluded stem.
            if any(system == stem or system.startswith(stem)
                   for stem in EXCLUDED_CODE_SYSTEMS):
                return {"ok": False,
                        "error": _check_copy(
                            "Code system not accepted in this "
                            "store.")}
        records = self._load()
        records = [r for r in records if r.get("id") != resource_id]
        records.append(dict(resource))
        self._save(records)
        return {"ok": True, "id": resource_id,
                "note": _check_copy(
                    f"{resource_type} record stored.")}

    def get(self, resource_id: str):
        for record in self._load():
            if record.get("id") == str(resource_id):
                return record
        return None

    def list(self) -> list:
        return self._load()


# ------------------------------------------------------------------
# E6/F2/F7 — tracking/journal. Text is USER-RECORDED, never
# inferred. No brand words in any user-facing string.
# ------------------------------------------------------------------

class JournalEntry:
    """One dated journal entry, as the user recorded it."""

    def __init__(self, date: str, text: str, tags=()):
        self.date = str(date or "").strip()
        self.text = str(text or "")
        self.tags = [str(t) for t in (tags or ())]
        if not self.date:
            raise BoundaryRefused("Journal entry needs a date.")

    def render(self) -> str:
        tags = f" Tags: {', '.join(self.tags)}." if self.tags else ""
        return _check_copy(
            f"On {self.date}, you recorded: {self.text}.{tags}")

    def to_dict(self) -> dict:
        return {"date": self.date, "text": self.text,
                "tags": list(self.tags)}


# ------------------------------------------------------------------
# B6 — sound-event notices ride EA-20's classes. A notice only,
# never a screening label. No classification is performed here.
# ------------------------------------------------------------------

def notice_sound_event(when: str = "") -> str:
    """Notice that a sound was noticed. No label, no screening."""
    suffix = f" at {when}" if str(when or "").strip() else ""
    return _check_copy(f"A sound was noticed{suffix}.")
