"""v5.72 (Business track, wave 18, BZ-1) — deployment / admin
criteria. DEDUP BY REFERENCE: BZ-1 is mostly Wave 10 acceptance
criteria restated, so this module does NOT rebuild them. Each
Wave 10 item appears here as a checklist entry pointing at the
Wave 10 module that already implements it; only the Business
deltas are new checks.

Business deltas (new here):
  - BZ-1.2 admin-enforced telemetry OFF, provable: the check
    takes the telemetry state (enabled flag, from telemetry.py's
    configure() shape) plus an admin policy, and returns proof
    (OFF + admin-locked + evidence) or a refusal. Telemetry is
    OFF BY SHAPE in this tree (telemetry.configure() defaults
    to disabled, local JSONL only, no exporter); the admin lock
    is what makes it enforceable rather than accidental.
  - BZ-1.5 reporting to the CUSTOMER'S Fleet server: a report
    payload builder for the customer-tenant shape ONLY. The
    consumer shape is refused (INV-19 holds only in the
    customer-tenant shape). The module never sends anything:
    the caller supplies an injected sender, or no sending
    happens at all.
  - INV-19 shape note: no customer data leaves the machine
    except to the customer's own Fleet server, and only under
    the tenant shape.

Stdlib only. No network. No funnel registration. No writes.
"""

from __future__ import annotations

# Wave 10 acceptance criteria, by reference (implemented in the
# named module; this track does not rebuild them).
WAVE10_CRITERIA = (
    # (criterion id, what it is, where it already lives)
    ("BZ-1/W10-safe-mode", "Safe mode (Wave 10)",
     "core/w10_wiring.py: apply_safe_mode"),
    ("BZ-1/W10-terms-gate", "Terms gate (Wave 10)",
     "core/w10_wiring.py + GUI Terms gate"),
    ("BZ-1/W10-changelog", "Changelog on update (Wave 10)",
     "core/w10_wiring.py: changelog_action"),
    ("BZ-1/W10-autostart", "Working autostart toggle (Wave 10)",
     "core/w10_wiring.py + app_settings"),
    ("BZ-1/W10-backup", "Backup tick + status (Wave 10)",
     "core/w10_wiring.py: backup_tick / backup_status"),
    ("BZ-1/W10-portability", "Portability settings (Wave 10)",
     "core/w10_wiring.py: collect/apply_portability_settings"),
    ("BZ-1/W10-installer", "Installer authored, Windows-compiled "
     "(Wave 10; Inno Setup is Windows-only)",
     "installer/ + installer/WINDOWS_BUILD_CHECKLIST.md"),
)


class DeployError(ValueError):
    """Raised for malformed deployment inputs (fail-closed)."""


def _clean(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeployError("%s must be a non-empty string" % where)
    return value.strip()


def prove_telemetry_off(telemetry_enabled, admin_policy) -> dict:
    """Prove BZ-1.2: telemetry OFF and admin-enforced.

    ``telemetry_enabled`` is the telemetry state flag (False =
    OFF, from telemetry.py's configure() shape).
    ``admin_policy`` is the deployer's admin policy mapping, which
    must carry telemetry='off' AND locked=True. Anything less is
    a refusal, not a proof: an unlocked OFF is accidental, and an
    ON state is a failure, full stop.
    """
    if not isinstance(admin_policy, dict):
        raise DeployError("admin_policy must be a mapping")
    policy_off = str(admin_policy.get("telemetry", "")).strip().lower() == "off"
    locked = admin_policy.get("locked") is True
    admin = admin_policy.get("admin", "")
    if telemetry_enabled:
        return {"criterion": "BZ-1.2", "status": "fail",
                "evidence": "telemetry is ENABLED; admin-enforced OFF "
                            "cannot be proven while it is on",
                "locked": locked}
    if not policy_off:
        return {"criterion": "BZ-1.2", "status": "fail",
                "evidence": "admin policy does not set telemetry='off'",
                "locked": locked}
    if not locked:
        return {"criterion": "BZ-1.2", "status": "fail",
                "evidence": "telemetry is OFF but the policy is not "
                            "admin-locked; an unlocked OFF is not "
                            "enforcement",
                "locked": False}
    return {"criterion": "BZ-1.2", "status": "pass",
            "evidence": ("telemetry OFF by state and admin-locked "
                         "(admin=%s); OFF is enforced, not accidental"
                         % (admin or "unnamed")),
            "locked": True}


def build_fleet_report(tenant_shape: str, report: dict,
                       fleet_server: str = "") -> dict:
    """Build a BZ-1.5 Fleet-server report payload for the
    CUSTOMER-TENANT shape only.

    ``tenant_shape`` must be exactly "customer-tenant". Any other
    shape (including "consumer") is refused: INV-19 holds only
    in the customer-tenant shape, and a consumer report to a
    Fleet server would be customer data leaving the machine
    under no tenant agreement. The payload carries counts and
    statuses only — never content, never speech, never memory
    text. This function never sends: the caller owns transport
    (injected sender) or nothing leaves at all.
    """
    shape = _clean(tenant_shape, "tenant_shape")
    if shape != "customer-tenant":
        raise DeployError(
            "Fleet reporting is customer-tenant only (got %r); "
            "INV-19 holds only in the customer-tenant shape" % shape)
    if not isinstance(report, dict):
        raise DeployError("report must be a mapping")
    server = _clean(fleet_server, "fleet_server") if fleet_server else ""
    payload = {
        "criterion": "BZ-1.5",
        "tenant_shape": "customer-tenant",
        "fleet_server": server,
        "counts": {},
        "statuses": {},
    }
    for key, value in report.items():
        if not isinstance(key, str):
            raise DeployError("report keys must be strings")
        if isinstance(value, bool):
            payload["statuses"][key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            payload["counts"][key] = value
        elif isinstance(value, str) and value.strip().lower() in (
                "ok", "pass", "fail", "on", "off"):
            payload["statuses"][key] = value.strip().lower()
        else:
            raise DeployError(
                "Fleet reports carry counts and statuses only; "
                "key %r carries content, refused" % key)
    return payload


def deployment_checklist() -> list:
    """The BZ-1 checklist: Wave 10 criteria by reference plus the
    two Business deltas. Statuses here are 'inherited' (already
    implemented in Wave 10) or 'check' (this track's new gates);
    nothing here claims a pass it did not measure."""
    items = []
    for cid, what, where in WAVE10_CRITERIA:
        items.append({"id": cid, "what": what, "lives_in": where,
                      "status": "inherited",
                      "note": "Wave 10 acceptance criterion, restated "
                              "not rebuilt (D-21 dedup)"})
    items.append({"id": "BZ-1.2", "what": "Admin-enforced telemetry OFF, provable",
                  "lives_in": "core/business_deployment.py: prove_telemetry_off",
                  "status": "check",
                  "note": "New in this track; proof requires OFF state "
                          "AND an admin lock"})
    items.append({"id": "BZ-1.5", "what": "Reporting to the customer's Fleet server",
                  "lives_in": "core/business_deployment.py: build_fleet_report",
                  "status": "check",
                  "note": "Customer-tenant shape only; consumer shape "
                          "refused (INV-19)"})
    items.append({"id": "BZ-1.9", "what": "Fleet-seat entitlement, org-mode only",
                  "lives_in": "core/business_fleet_entitlement.py",
                  "status": "check",
                  "note": "Reuses MN-1.3 offline signed-entitlement/grace; "
                          "hard per-seat DRM for the consumer shape "
                          "is rejected by the report itself"})
    return items
