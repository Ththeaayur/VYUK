"""v5.72 (Business track, wave 18, BZ-6) — trust-pack claims +
paper renderer (Wave 2 feed).

Riders enforced by construction:
  - Claims trace to a DLP cell or get deleted: render_paper()
    DROPS any claim without an evidence cell, and reports the
    dropped list. A paper never carries an unevidenced claim.
  - BZ-6.13 provider DPAs block EVERY residency / no-training
    claim until they exist: DPA-gated claims render as
    CONDITIONAL (never as facts) while dpa_status is "owed".
  - A DLP tool with a network path is an incident, not a
    feature: the trust pack records no DLP tool with network
    egress as compliant — see the claim kinds below.
  - Judgment fields are never auto-written: claims carry a
    human verdict field that defaults to blank ("") and is
    never filled by code.

The paper is data + renderer, not prose committed to the tree:
render_paper() emits the markdown on demand (the parent can
emit it at ship). No claim here is a shipped promise — each
carries its evidence cell and its DPA status in the open.

Stdlib only. No network. No funnel registration.
"""

from __future__ import annotations

CLAIM_KINDS = ("residency", "no-training", "dlp", "telemetry",
               "retention", "access")


class TrustPackError(ValueError):
    """Raised for malformed trust-pack inputs (fail-closed)."""


def make_claim(claim_id: str, kind: str, statement: str,
               evidence_cell: str = "", dpa_required: bool = False,
               verdict: str = "") -> dict:
    """One trust-pack claim. ``evidence_cell`` names the DLP /
    audit cell that proves it (empty = unevidenced, dropped at
    render). ``dpa_required`` marks BZ-6.13-gated claims.
    ``verdict`` is a HUMAN field: code never fills it (blank
    beats guessed)."""
    if kind not in CLAIM_KINDS:
        raise TrustPackError("unknown claim kind %r" % kind)
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise TrustPackError("claim_id must be a non-empty string")
    if not isinstance(statement, str) or not statement.strip():
        raise TrustPackError("statement must be a non-empty string")
    if not isinstance(evidence_cell, str):
        raise TrustPackError("evidence_cell must be a string")
    if not isinstance(verdict, str):
        raise TrustPackError("verdict must be a string")
    return {
        "id": claim_id.strip(),
        "kind": kind,
        "statement": statement.strip(),
        "evidence_cell": evidence_cell.strip(),
        "dpa_required": bool(dpa_required),
        "verdict": verdict.strip(),
    }


def render_paper(claims, dpa_status: str = "owed") -> dict:
    """Render the trust-pack paper. Returns {paper, kept, dropped,
    conditional}: kept claims have evidence cells; dropped claims
    had none (deleted, per the rider); DPA-gated claims render
    CONDITIONAL while dpa_status is 'owed' (BZ-6.13) and as
    stated only when dpa_status is 'held'. Any other dpa_status
    value fails closed."""
    if dpa_status not in ("owed", "held"):
        raise TrustPackError("dpa_status must be 'owed' or 'held'")
    if not isinstance(claims, (list, tuple)):
        raise TrustPackError("claims must be a list")
    seen = set()
    kept, dropped = [], []
    for claim in claims:
        if not isinstance(claim, dict) or "id" not in claim:
            raise TrustPackError("each claim must be a make_claim dict")
        if claim["id"] in seen:
            raise TrustPackError("duplicate claim id %r" % claim["id"])
        seen.add(claim["id"])
        if not claim.get("evidence_cell"):
            dropped.append(claim["id"])
        else:
            kept.append(claim)
    lines = ["# VYUK Business Trust Pack (Wave 2 feed)",
             "",
             "Claims trace to a DLP/audit cell or are deleted. "
             "DPA-gated claims are CONDITIONAL until provider DPAs "
             "are held (BZ-6.13, STILL-OWED).",
             ""]
    conditional = []
    for claim in kept:
        if claim.get("dpa_required") and dpa_status == "owed":
            conditional.append(claim["id"])
            lines.append("- [%s] %s — CONDITIONAL (provider DPA owed; "
                         "not a stated fact). Evidence cell: %s."
                         % (claim["id"], claim["statement"],
                            claim["evidence_cell"]))
        else:
            lines.append("- [%s] %s. Evidence cell: %s."
                         % (claim["id"], claim["statement"],
                            claim["evidence_cell"]))
        if claim.get("verdict"):
            lines.append("  Human verdict: %s" % claim["verdict"])
    if dropped:
        lines.append("")
        lines.append("Dropped (no evidence cell, per the rider): %s"
                     % ", ".join(dropped))
    return {"paper": "\n".join(lines) + "\n",
            "kept": [c["id"] for c in kept],
            "dropped": dropped,
            "conditional": conditional}
