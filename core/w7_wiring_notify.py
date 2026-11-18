# Part of the core/w7_wiring.py split (skills split B13).
# Family: notify.

from __future__ import annotations

from typing import Any, Optional

from . import reach  # sibling module used by this family

RB6_CALIBRATION_OWED = (
    "RB-6 bench calibration is owed before ANY live arm "
    "movement: J1-J3 spans are conservative worker defaults, "
    "only J4 0..101 is grounded in the v9.64 spider gripper, "
    "and no live serial transport tool exists in the registry — "
    "this layer wires validation (clamps + dead-man + plan-only) "
    "only and never moves an arm."
)

def notify_via_apprise(service: str, title: str, body: str,
                       services: dict = None) -> reach.Outcome:
    """Apprise notification validation through the pin gate.

    ``services`` is the caller's OWN per-service configuration
    ({"my-mail": "mailto://..."}); with none supplied this
    returns unavailable, never a fabricated send. Even on
    validation success the module performs NO send itself —
    delivery is the caller's confirmed egress path (the module
    says so in its own outcome reason). The pin
    >=1.0,<2.0 is enforced by apprise_available() inside the
    notifier; a 2.x install is refused, licence-clear or not."""
    if not services:
        return reach.unavailable(
            "no per-service Apprise configuration supplied; "
            "notification egress unavailable (see APPRISE_CONFIG_OWED)",
            pin=reach.APPRISE_PIN)
    try:
        notifier = reach.AppriseNotifier(dict(services))
    except ValueError as exc:
        return reach.refuse(str(exc))
    return notifier.notify(service, title, body)

def home_assistant_request(host: str, entity_id: str, action: str,
                           scope: reach.HomeAssistantScope = None
                           ) -> reach.Outcome:
    """Home Assistant consume-only request validation: strict
    LAN gate + deny-by-default scope (Assist-exposed AND on the
    per-room allowlist, expose-new OFF) + allowed action set.
    Returns the validated request dict on success — it performs
    NO network call and NO state change. Execution is the
    caller's injected path (HA_EXECUTION_OWED). With no scope
    supplied the default scope has an EMPTY allowlist, so
    nothing is authorized — deny-by-default, not a bug."""
    return reach.build_assist_request(
        scope or reach.default_home_scope(), host, entity_id, action)

def home_assistant_scope_proof(scope: reach.HomeAssistantScope) -> dict:
    """Configured-state proof: expose-new flag + allowlist, so
    a ship report can prove deny-by-default holds."""
    return reach.home_assistant_scope_proof(scope)

def route_hamcp_tool(tool_name: str,
                     profile: reach.HamcpProfile = None
                     ) -> reach.Outcome:
    """HM-2 toolbox routing ONLY: a ha-mcp tool name is routed
    via the toolbox or refused (non-ha-mcp names refused;
    filesystem/YAML tools refused while their flags are OFF in
    the profile). Routing is a decision, not a declaration and
    not an execution — declaration_surface() stays empty by
    construction, and the default profile keeps both flags OFF
    (enabling them in the default profile is itself refused)."""
    return reach.toolbox_route(
        tool_name, profile or reach.default_hamcp_profile())

def hamcp_declaration_surface() -> list:
    """Proof that HM-2 exposes NO declaration surface: the 95+
    ha-mcp tools never become declarations."""
    return reach.declaration_surface()

def validate_arm_move(joint: str, angle: float) -> reach.Outcome:
    """RB-6 validation path: clamp-check a wrapped move WITHOUT
    an executor — always plan-only, never a write. J4 0..101 is
    the grounded v9.64 span; J1-J3 spans are conservative
    worker defaults pending bench calibration (see
    RB6_CALIBRATION_OWED)."""
    return reach.arm_plan_only(joint, angle)

def arm_bridge(executor=None, tracing_on: bool = False,
               trace_sink=None) -> reach.ArmBridge:
    """The RB-6 bridge with an injected executor (or none, in
    which case every call is plan-only). The bridge has NO
    raw-write method and opens NO port: execution happens only
    through the injected executor, behind the 5.0 s dead-man
    (heartbeat required before any write). No live serial
    executor exists in this tree — wiring one is gated on bench
    calibration, so today this bridge validates and plans."""
    return reach.ArmBridge(executor=executor,
                                tracing_on=tracing_on,
                                trace_sink=trace_sink)

def arm_status() -> dict:
    """RB-6 gate record: clamps, dead-man, transport probe, and
    the calibration debt — quoted so no caller can mistake a
    plan-only validation for a live move."""
    return {
        "joint_limits": dict(reach.JOINT_LIMITS),
        "joint4_grounded": "v9.64 spider gripper 0..101",
        "joint1_3": "conservative worker defaults, bench calibration owed",
        "deadman_timeout_s": reach.DEADMAN_TIMEOUT_S,
        "transport_opens_port": False,
        "raw_write_method": False,
        "serial_probe": reach.serial_available(),
        "calibration_owed": RB6_CALIBRATION_OWED,
    }

def redaction_gate_status() -> dict:
    """PA-7 status, stated so it cannot be mistaken for a live
    gate: the redactor is PILOT ONLY, never the sole gate
    before a cloud send; the NER leg is ABSENT (spaCy model
    licence STILL-OWED) and the OCR leg has no winner. This
    layer wires NO redaction gate — it only reports the pilot
    state and the module's own sole-gate refusal."""
    return {
        "wired_as_gate": False,
        "sole_gate": reach.sole_gate_redact(),
        "ner_available": reach.NER_AVAILABLE,
        "ocr_winner": reach.OCR_WINNER,
        "pilot_only": True,
    }
