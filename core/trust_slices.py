"""Wave 3 trust slices (§13) + Track K + Phase 3 discriminators.
Stdlib only, no GUI, no network, no collection, no inference.

P13 guardian angel: a pre-flight + watch list built ONLY from
supplied checks (failsafe, target resolved, confirm gate, disk
headroom, connectivity verdict). A check that was not supplied
renders 'unknown' and blocks, never a guessed pass.
P13 pre-flight check: same rule, one function, failing list out.
P13 crash-consistent: the packaged SQLite version audit plus a
store-integrity check on a GIVEN file (never assistant.db by
assumption — the caller names the file; this module refuses
assistant.db outright, matching the rl rule).
P13 self-attack at idle: a READ-ONLY self-test runner. It runs
supplied check callables, records findings, and changes
nothing. A finding is a finding, never an auto-fix, never a
deployment — self-improvement stays proposals-only (T3).
P21 tripwires (detect only): patterns registered by the caller
are matched against supplied text (a log excerpt, a command
line). A match is REPORTED with its pattern and excerpt. A
tripwire never blocks, never enforces, and never replaces a
real security tool — the report says so, every time.
Track K capability, except vetoed items: a capability registry
where 'proven' requires an evidence id, 'gated' requires a
reason, and vetoed items are refused outright. Vetoed: face
avatar, Charon live voice, M4 self-recovery, earcon family,
F6 signatures, Space Grotesk pairing, and any character voice
or catchphrase pretending to be the fictional assistant.
Phase 3 discriminators: verdicts that tell states apart before
acting — crash vs sleep (dual clock), online vs portal vs weak
vs offline (probe truth), tripped vs healthy failsafe, degraded
vs normal, safe-mode vs normal. Every discriminator returns
'unknown' on insufficient data, never a guessed verdict.
"""

from __future__ import annotations

import os
import sqlite3

from . import store_base

VETOED_CAPABILITIES = (
    "face_avatar",
    "charon_live_voice",
    "m4_self_recovery",
    "earcon_family",
    "f6_signatures",
    "space_grotesk_pairing",
    "character_voice_or_catchphrase",
)


class TrackK:
    """Track K capability registry, except vetoed items."""

    def __init__(self):
        self._caps: dict[str, dict] = {}

    def set(self, name: str, status: str, *, evidence: str = "",
            reason: str = "") -> dict:
        if name in VETOED_CAPABILITIES:
            return {"ok": False, "reason": f"vetoed capability '{name}'"}
        if status not in ("proven", "pilot", "gated"):
            return {"ok": False, "reason": f"unknown status '{status}'"}
        if status == "proven" and not evidence:
            return {"ok": False,
                    "reason": "proven requires an evidence id"}
        if status == "gated" and not reason:
            return {"ok": False, "reason": "gated requires a reason"}
        self._caps[name] = {"status": status, "evidence": evidence,
                            "reason": reason}
        return {"ok": True}

    def get(self, name: str) -> dict:
        if name in VETOED_CAPABILITIES:
            return {"status": "vetoed", "reason": "vetoed capability"}
        return self._caps.get(name, {"status": "unknown",
                                     "reason": "capability not recorded"})

    def all(self) -> dict:
        return dict(self._caps)


def guardian_preflight(*, failsafe_ok: bool | None,
                       target_resolved: bool | None,
                       confirm_gate_ok: bool | None,
                       disk_ok: bool | None,
                       connectivity: str | None) -> dict:
    """Guardian-angel pre-flight: every check must be a supplied
    True, or the run is blocked with the failing/unknown list.
    'unknown' (None) blocks exactly like False — a missing check
    is never a pass."""
    checks = {
        "failsafe": failsafe_ok,
        "target_resolved": target_resolved,
        "confirm_gate": confirm_gate_ok,
        "disk": disk_ok,
        "connectivity": (connectivity in ("ok", "weak")
                         if connectivity is not None else None),
    }
    failing = [name for name, value in checks.items() if value is not True]
    return {"blocked": bool(failing), "failing": failing,
            "checks": checks,
            "note": "unknown checks block; nothing here is guessed"}


def crash_consistent_check(db_path: str) -> dict:
    """Crash-consistency for a GIVEN store file. Refuses
    assistant.db outright. Runs the SQLite version audit and a
    read-only integrity check; a failure is a plain verdict."""
    if store_base.is_assistant_db(db_path):
        return {"ok": False,
                "reason": "assistant.db is never audited in place by this check"}
    if not os.path.isfile(db_path):
        return {"ok": False, "reason": "store file not found"}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            version = sqlite3.sqlite_version
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — integrity failure is a verdict
        return {"ok": False, "reason": f"integrity check failed: {exc}"}
    integrity_ok = bool(row and str(row[0]).lower() == "ok")
    return {"ok": integrity_ok, "integrity": row[0] if row else "unknown",
            "sqlite_version": version,
            "note": "read-only check; it repairs nothing"}


def self_attack_at_idle(checks: dict) -> dict:
    """READ-ONLY idle self-test. ``checks`` maps a check name to a
    callable returning (passed: bool, detail: str). Nothing here
    writes, fixes, or deploys anything — findings are returned
    for a human to act on."""
    findings = []
    for name, check in checks.items():
        try:
            passed, detail = check()
        except Exception as exc:  # noqa: BLE001 — a crashing check is a finding
            passed, detail = False, f"check raised: {exc}"
        findings.append({"check": name, "passed": bool(passed),
                         "detail": str(detail)})
    return {"findings": findings,
            "failed": [f["check"] for f in findings if not f["passed"]],
            "note": "detect and report only; no auto-fix, no deployment"}


class Tripwires:
    """Detect-only tripwires (P21). Patterns are caller-supplied
    plain substrings. ``scan`` reports matches; it never blocks,
    never enforces, and never claims to be a security tool."""

    def __init__(self):
        self._patterns: dict[str, str] = {}

    def register(self, name: str, pattern: str) -> dict:
        if not pattern:
            return {"ok": False, "reason": "empty pattern refused"}
        self._patterns[name] = pattern
        return {"ok": True}

    def scan(self, text: str) -> dict:
        matches = [{"tripwire": name, "pattern": pattern}
                   for name, pattern in self._patterns.items()
                   if pattern in text]
        return {"matches": matches, "count": len(matches),
                "note": "detect only: tripwires never replace real security tools"}


def discriminate(*, crash_verdict: str | None = None,
                 connectivity_verdict: str | None = None,
                 failsafe_tripped: bool | None = None,
                 safe_mode: bool | None = None,
                 degraded: bool | None = None) -> dict:
    """Phase 3 discriminators, as one verdict dict. Any input left
    as None renders 'unknown' for that axis — never guessed."""
    return {
        "crash_vs_sleep": crash_verdict if crash_verdict in (
            "crash", "sleep", "unknown") else "unknown",
        "connectivity": connectivity_verdict if connectivity_verdict in (
            "ok", "online", "portal", "weak", "offline") else "unknown",
        "failsafe": ("tripped" if failsafe_tripped else "healthy")
                    if failsafe_tripped is not None else "unknown",
        "safe_mode": ("engaged" if safe_mode else "normal")
                     if safe_mode is not None else "unknown",
        "mode": ("degraded" if degraded else "normal")
                if degraded is not None else "unknown",
    }


def degraded_mode_drill(*, offline: bool, failsafe_tripped: bool,
                        disk_full: bool) -> dict:
    """R4-E degraded-mode drill, as a recorded verdict (not a
    field run). Degraded when offline, tripped, or disk-full;
    the drill result names its trigger, and a lab drill never
    counts as the owner's drill. R4-E does NOT green M10."""
    triggers = []
    if offline:
        triggers.append("offline")
    if failsafe_tripped:
        triggers.append("failsafe-tripped")
    if disk_full:
        triggers.append("disk-full")
    return {"degraded": bool(triggers), "triggers": triggers,
            "field_proof": "NOT claimed: the owner's drill, his run, is still owed",
            "m10": "NOT greened by this drill"}
