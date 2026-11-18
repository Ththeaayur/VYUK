"""Wave 3 GUI surface for Resilience wiring — pure text rendering.
No Qt import here, so this module is testable headlessly and can
be imported by any GUI page without pulling in a window. It
renders a status payload (from core/resilience/resilience_wiring.py) as
plain lines; it never re-derives status from live internals, and
it changes no existing GUI file and no sidebar index (frozen
core). A GUI page that wants this surface imports these two
functions and renders their output; nothing here wires itself
into a window on import.
"""

from __future__ import annotations


def render_resilience_status(status: dict) -> list:
    """Status payload -> plain display lines, in plain words."""
    lines = []
    lines.append("Resilience wiring: "
                 + ("TRIPPED — control paused until a human re-arms it"
                    if status.get("tripped") else "healthy"))
    if status.get("trip_reason"):
        lines.append(f"Trip reason: {status['trip_reason']}")
    lines.append("Safe mode: "
                 + ("engaged" if status.get("safe_mode") else "not engaged"))
    lines.append("Libraries wired: " + ", ".join(status.get("libraries", [])))
    for fence in status.get("fences", []):
        lines.append(f"Fence: {fence}")
    owed = status.get("owed_reads", {})
    parked = [name for name, state in owed.items() if state == "parked"]
    pilot = [name for name, state in owed.items() if state == "pilot-only"]
    if parked:
        lines.append("Parked (owed licence reads): " + ", ".join(parked))
    if pilot:
        lines.append("Pilot-only (owed licence reads): " + ", ".join(pilot))
    if status.get("field_proof"):
        lines.append(status["field_proof"])
    return lines


def render_kill_drill(entry: dict) -> str:
    """One kill-drill ledger entry, in plain words, with its
    provenance stated — a lab drill never reads as field proof."""
    who = "the owner" if entry.get("run_by") == "owner" else "the lab"
    verdict = "passed" if entry.get("passed") else "did not pass"
    proof = ("counts as field proof" if entry.get("counts_as_field_proof")
             else "does NOT count as field proof")
    return (f"Kill drill run by {who} {verdict} "
            f"({len(entry.get('steps', []))} steps) — {proof}.")
