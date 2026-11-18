"""v5.72 Business track wiring — funnel tool ``business`` (parent gate).

Three actions, all reads/checks, all pure computation (no file
capture, no network, no send, no memory write, no booking or
payment shape):

- telemetry_proof: the live telemetry state flag plus the BZ-1.2
  admin-enforcement proof when an admin policy is supplied.
  Without a policy on file the result is "unproven", never a
  proof — an unlocked OFF is not enforcement.
- trust_pack: the BZ-6 trust-pack paper rendered from the
  track's claim set. DPA-gated claims render CONDITIONAL while
  BZ-6.13 is owed; unevidenced claims are dropped, per the rider.
- entitlement_check: a signed fleet-seat entitlement supplied
  as JSON is verified locally (Ed25519 over the canonical
  payload, org edition only, MN-1.3 grace semantics).
  Reconciliation (buyer vs enrolled counts) runs ONLY over a
  signature-verified result; anything else reports its failure
  reason and no counts.

Deliberately NOT wired: BZ-4.8 consent machinery (library-only
until a capture lane exists to gate — a consent surface with no
lane would be theatre), BZ-3 workplace servers (bus citizens
only; ~100-120+ tools each against 3,814 B of fitted headroom),
and the BZ-1.5 fleet report builder (it needs an admin-supplied
report; the funnel invents no counts).
"""

import json

_ACTIONS = ("telemetry_proof", "trust_pack", "entitlement_check")


def _trust_pack_claims():
    from . import business_trust_pack as _tp
    return [
        _tp.make_claim(
            "BZ-6.21", "telemetry",
            "Vendor-receives-nothing counters stay at zero vendor calls.",
            evidence_cell="cell-vendor-counters"),
        _tp.make_claim(
            "BZ-6.2", "dlp",
            "PII is redacted before any egress path (Presidio pilot, "
            "Wave 2 feed).",
            evidence_cell="cell-presidio-pilot"),
        _tp.make_claim(
            "BZ-6.3", "dlp",
            "Encrypted store holds business data at rest (SQLCipher "
            "pilot, Wave 2 feed).",
            evidence_cell="cell-sqlcipher-pilot"),
        _tp.make_claim(
            "BZ-6.13-R", "residency",
            "Business data resides in the customer region.",
            evidence_cell="cell-residency", dpa_required=True),
        _tp.make_claim(
            "BZ-6.13-NT", "no-training",
            "Customer data is never used for model training.",
            evidence_cell="cell-no-training", dpa_required=True),
    ]


def _telemetry_proof(query):
    try:
        from . import telemetry as _tm
        enabled = _tm.is_enabled()
    except Exception:
        return {"action": "telemetry_proof", "status": "unproven",
                "evidence": "telemetry state could not be read; "
                            "nothing is proven"}
    policy = None
    try:
        parsed = json.loads(query) if query.strip().startswith("{") else None
        if isinstance(parsed, dict):
            policy = parsed.get("admin_policy")
    except Exception:
        policy = None
    if not isinstance(policy, dict):
        if enabled:
            return {"action": "telemetry_proof", "status": "fail",
                    "telemetry_enabled": True,
                    "evidence": "telemetry is ENABLED; admin-enforced "
                                "OFF cannot be proven while it is on"}
        return {"action": "telemetry_proof", "status": "unproven",
                "telemetry_enabled": False,
                "evidence": "telemetry is OFF by state, but no admin "
                            "policy is on file, so admin enforcement "
                            "cannot be proven; supply an admin policy "
                            "to run the BZ-1.2 proof"}
    from . import business_deployment as _bz1
    result = _bz1.prove_telemetry_off(enabled, policy)
    result["action"] = "telemetry_proof"
    return result


def _trust_pack(query):
    from . import business_trust_pack as _tp
    rendered = _tp.render_paper(_trust_pack_claims(), dpa_status="owed")
    return {"action": "trust_pack",
            "paper": rendered["paper"],
            "kept": rendered["kept"],
            "dropped": rendered["dropped"],
            "conditional": rendered["conditional"]}


def _entitlement_check(query):
    from . import business_fleet_entitlement as _bz19
    key = None
    try:
        parsed = json.loads(query) if query.strip() else None
        if isinstance(parsed, dict):
            key = parsed.get("public_key_b64")
    except Exception:
        key = None
    result = _bz19.check_fleet_entitlement_text(query, public_key_b64=key)
    out = {"action": "entitlement_check",
           "status": result.status,
           "reason": result.reason,
           "entitled": result.entitled,
           "message": result.message}
    if result.status in ("valid", "in_grace", "expired"):
        try:
            out["reconciliation"] = _bz19.reconcile(result)
        except Exception:
            out["reconciliation"] = None
    return out


def business_tool(args):
    """Funnel entry: {"action": ..., "query": ...} -> result dict."""
    if not isinstance(args, dict):
        return {"action": "", "status": "refused",
                "evidence": "business_tool takes an arguments mapping"}
    action = (args.get("action") or "").strip()
    query = args.get("query") or ""
    if not isinstance(query, str):
        query = ""
    if action == "telemetry_proof":
        return _telemetry_proof(query)
    if action == "trust_pack":
        return _trust_pack(query)
    if action == "entitlement_check":
        return _entitlement_check(query)
    return {"action": action, "status": "refused",
            "evidence": "unknown business action; known actions: "
                        "telemetry_proof, trust_pack, entitlement_check"}
