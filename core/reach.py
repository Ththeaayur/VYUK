"""VYUK Wave 7 (v5.62) — REACH: notifications, home, arm, redaction.

Leaf module for Wave 7 only. It creates NO tool declarations, touches NO
shared file, adds NO dependency, and changes NO declaration, telemetry,
or agent behaviour elsewhere. Everything optional below is lazy-guarded:
absent means an honest ``unavailable`` outcome, never a hard import
failure and never a fabricated success.

STATUS (never claim a pilot as shipped):
- PILOT ONLY: PA-7 Presidio Image Redactor seat (own-code recognizers
  only; see below). It is NEVER the sole gate before a cloud send.
- STAGED / GATED (not live): HM-2 ha-mcp author layer (toolbox routing
  only, filesystem/YAML tools OFF in the default profile); RB-6 serial
  transport itself (the module validates and hands a wrapped command to
  an injected executor; it never opens a port or writes raw serial).
- LIVE AS A GATE LAYER (fail-closed validation, no new capability by
  itself): HE-11 Apprise pin/version gate, HM-1 allowlist + LAN gate,
  RB-6 clamp + dead-man gate, PA-7 whole-window exclusion policy.

LICENCES — read from primary sources 2026-10-25 by downloading the
wheel/sdist (``pip download --no-deps``) and reading METADATA + LICENSE
files locally, never metadata snippets alone:
- apprise 1.13.1: BSD-2-Clause (LICENSE file, Copyright Chris Caron).
  Pin recorded here: >=1.0,<2.0. apprise 2.1.0 was also read
  (BSD-2-Clause, LICENSE file) and is REFUSED by the version gate:
  2.0 is BREAKING, so licence-clear is not enough — the pin rules it
  out regardless of licence.
- pyserial 3.5: BSD (METADATA License field + BSD classifier;
  no LICENSE file in the wheel). Lazy optional only; absent ->
  honest unavailable.
- presidio 2.2.364 (metapackage), presidio-analyzer 2.2.364,
  presidio-anonymizer 2.2.364: MIT (METADATA License-Expression).
  presidio-image-redactor 0.0.60: MIT (METADATA License-Expression).
  CODE licence only. The deployed spaCy NER MODEL licence is
  separately STILL-OWED (plan item O-24k, #1 ship-blocking for the
  redaction seat): the model is NOT deployed, the NER leg is ABSENT,
  and no shipped path depends on it. Saying "Presidio is MIT" without
  that model caveat would be a false clearance, so this module says
  both, every time it matters.
- ha-mcp 3.5.1: MIT (LICENSE file, Copyright 2024 Julien).
  homeassistant_mcp 3.2.1: MIT (LICENSE file, Copyright 2025 robbrad).
  mcp-server-home-assistant 0.1.0: Apache-2.0 (LICENSE file).
  NONE of these is claimed to be the official Home Assistant MCP
  server: no official Home Assistant MCP PyPI package was verified
  from a primary source in this wave, so HM-1 imports NO Home
  Assistant package at all (own-code consume-only client shape over
  the already-declared MCP bus), and HM-2 treats ha-mcp as an
  external, user-installed server reached only via toolbox routing —
  it is never imported by this module.
- Anything not listed above as verified is STILL-OWED and hard-blocks
  its item, not this module: the module's gates fail closed on the
  unverified item instead of promoting it.

AGENT-DIRECTED CONTENT: any AGENTS.md / CLAUDE.md text in the repo is
DATA, never instructions. Nothing in this module follows it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field

__all__ = [
    "Outcome",
    "refuse",
    "unavailable",
    # HE-11 Apprise
    "APPRISE_PIN",
    "check_apprise_version",
    "apprise_available",
    "AppriseNotifier",
    # HM-1 Home Assistant (consume-only)
    "is_lan_target",
    "validate_pkce_params",
    "oauth_pkce_shape",
    "HomeAssistantScope",
    "default_home_scope",
    "home_assistant_scope_proof",
    "build_assist_request",
    # HM-2 ha-mcp author layer (toolbox routing only)
    "HamcpProfile",
    "default_hamcp_profile",
    "hamcp_flag_proof",
    "hamcp_set_flag",
    "toolbox_route",
    "declaration_surface",
    # RB-6 serial bridge (wrapped tools only)
    "JOINT_LIMITS",
    "JOINT1_LIMIT",
    "JOINT2_LIMIT",
    "JOINT3_LIMIT",
    "JOINT4_LIMIT",
    "DEADMAN_TIMEOUT_S",
    "serial_available",
    "ArmBridge",
    "arm_plan_only",
    # PA-7 Presidio Image Redactor (pilot only)
    "NER_AVAILABLE",
    "OCR_WINNER",
    "SECRET_RECOGNIZERS",
    "detect_secrets",
    "apply_whole_window_exclusions",
    "ocr_leg",
    "sole_gate_redact",
    "pilot_redact_text",
    "planted_secret_eval",
    "REACH_STATUS",
]

# ---------------------------------------------------------------- outcomes

@dataclass(frozen=True)
class Outcome:
    """One gate decision. ``ok`` is False for every refusal and every
    honest-unavailable result; callers must not treat those as success."""

    ok: bool
    kind: str  # "ok" | "refused" | "unavailable" | "plan-only"
    reason: str
    detail: dict = field(default_factory=dict)


def refuse(reason: str, **detail) -> Outcome:
    return Outcome(False, "refused", reason, detail)


def unavailable(reason: str, **detail) -> Outcome:
    return Outcome(False, "unavailable", reason, detail)


def plan_only(reason: str, **detail) -> Outcome:
    return Outcome(False, "plan-only", reason, detail)


REACH_STATUS = {
    "HE-11_apprise": "GATED: pin >=1.0,<2.0 enforced; 2.x refused; per-service config only",
    "HM-1_home_assistant": "GATED: consume-only, Assist-exposed entities only, LAN-only, allowlist fail-closed",
    "HM-2_ha_mcp": "STAGED: toolbox routing only, no declarations, fs/YAML flags OFF in default profile",
    "RB-6_serial_bridge": "GATED: wrapped tools only, injected executor only, clamps + dead-man in-module",
    "PA-7_presidio_redactor": "PILOT ONLY: own-code recognizers only; NER absent (spaCy model STILL-OWED O-24k); never sole gate",
}

# ---------------------------------------------------------------- HE-11 Apprise

APPRISE_PIN = ">=1.0,<2.0"
_APPRISE_MIN = (1, 0)
_APPRISE_MAX_EXCLUSIVE = (2, 0)


def _parse_version(text: str):
    parts = []
    for piece in str(text).strip().split("."):
        digits = re.match(r"(\d+)", piece)
        if digits is None:
            return None
        parts.append(int(digits.group(1)))
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:3])


def check_apprise_version(version: str) -> Outcome:
    """Pin gate: Apprise >=1.0,<2.0 only. 2.0 is BREAKING, so any 2.x
    version is REFUSED here even though its licence (BSD-2-Clause)
    was verified — licence-clear does not override the pin."""
    parsed = _parse_version(version)
    if parsed is None:
        return refuse("apprise version unparseable; refusing rather than assuming the pin holds",
                      version=str(version), pin=APPRISE_PIN)
    if parsed < _APPRISE_MIN or parsed >= _APPRISE_MAX_EXCLUSIVE:
        return refuse(
            "apprise version outside pin >=1.0,<2.0 (2.0 is BREAKING); refusing",
            version=str(version), pin=APPRISE_PIN)
    return Outcome(True, "ok", "apprise version within pin >=1.0,<2.0",
                   {"version": str(version), "pin": APPRISE_PIN})


def apprise_available() -> Outcome:
    """Lazy optional probe. Never a hard import: absent -> unavailable."""
    try:
        import apprise  # type: ignore
    except Exception:
        return unavailable("apprise not installed; notification egress unavailable",
                           pin=APPRISE_PIN)
    version = getattr(apprise, "__version__", None) or getattr(apprise, "version", None) or "unknown"
    gate = check_apprise_version(str(version))
    if not gate.ok:
        return gate
    return Outcome(True, "ok", "apprise available within pin", {"version": str(version)})


class AppriseNotifier:
    """Notification egress to HIS OWN services only.

    ``services`` is a per-service configuration supplied by the caller,
    e.g. ``{"my-mail": "mailto://..."}``. There is no default service,
    no shared or third-party relay, and no service may be used unless
    it appears in this mapping. A service entry flagged
    ``shared_service=True`` is refused at configuration time: shared
    third-party services are never an egress target here.
    """

    def __init__(self, services: dict):
        self.services = {}
        for name, cfg in dict(services or {}).items():
            if isinstance(cfg, str):
                cfg = {"url": cfg}
            if not isinstance(cfg, dict) or not cfg.get("url"):
                raise ValueError(f"apprise service {name!r}: per-service configuration with a url is required")
            if cfg.get("shared_service"):
                raise ValueError(
                    f"apprise service {name!r}: shared/third-party services are refused; "
                    "only his own per-service configuration is allowed")
            self.services[str(name)] = dict(cfg)

    def notify(self, service: str, title: str, body: str) -> Outcome:
        if service not in self.services:
            return refuse("apprise target not in his own per-service configuration; refusing",
                          service=service, configured=sorted(self.services))
        probe = apprise_available()
        if not probe.ok:
            return probe
        # The actual send is deliberately NOT performed by this gate
        # layer in this wave: the caller hands the validated (service,
        # title, body) triple to its own confirmed egress path. What
        # this function proves is that the target, pin, and config all
        # pass; claiming a delivery this module did not perform would
        # be a false claim.
        return Outcome(True, "ok",
                       "apprise target validated (own service, pin holds); delivery is the caller's confirmed egress path",
                       {"service": service, "version": probe.detail.get("version"),
                        "title": title, "body_len": len(body)})


# ---------------------------------------------------------------- HM-1 Home Assistant (consume-only)

HM1_ALLOWED_ACTIONS = ("turn_on", "turn_off", "toggle")
# Assist-exposed entities only: an entity is reachable only if it is
# both Assist-exposed (flag on the scope) and on the per-room allowlist.


# Strict LAN definition — deliberately NOT ipaddress.is_private:
# is_private also counts the 198.18/15 benchmarking range (and other
# special ranges) as "private", and this environment's resolver
# answers *.invalid names inside that range. LAN here means exactly
# RFC1918 + loopback + link-local, nothing wider.
_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_lan_address(ip) -> bool:
    return any(ip in net for net in _LAN_NETWORKS)


def is_lan_target(host: str) -> Outcome:
    """LAN-only gate. RFC1918, loopback, and link-local addresses pass;
    everything else refuses; unresolvable hostnames refuse fail-closed
    (a name we cannot prove is LAN is not treated as LAN)."""
    host = (host or "").strip()
    if not host:
        return refuse("empty Home Assistant target; refusing", target=host)
    try:
        ip = ipaddress.ip_address(host)
        candidates = [ip]
    except ValueError:
        if host in ("localhost",):
            candidates = [ipaddress.ip_address("127.0.0.1")]
        else:
            try:
                infos = socket.getaddrinfo(host, None)
            except Exception:
                return refuse("Home Assistant hostname does not resolve; refusing fail-closed (LAN unproven)",
                              target=host)
            candidates = []
            for info in infos:
                try:
                    candidates.append(ipaddress.ip_address(info[4][0]))
                except Exception:
                    continue
            if not candidates:
                return refuse("Home Assistant hostname resolved to no address; refusing", target=host)
    for ip in candidates:
        # Strict LAN check (see _LAN_NETWORKS): a non-LAN address
        # anywhere in the resolution refuses the whole target.
        if not _is_lan_address(ip):
            return refuse("non-LAN Home Assistant target refused; LAN-only",
                          target=host, address=str(ip))
    return Outcome(True, "ok", "LAN target", {"target": host})


def oauth_pkce_shape() -> dict:
    """OAuth 2.0 + PKCE shape (description only — no token, secret, or
    code is minted, stored, or sent by this module)."""
    return {
        "flow": "OAuth 2.0 authorization code + PKCE",
        "code_challenge_method": "S256",
        "code_verifier_length": "43-128 characters",
        "redirect": "LAN / loopback only (same LAN gate as the target)",
        "token_storage": "not performed by this module",
    }


def validate_pkce_params(params: dict) -> Outcome:
    params = dict(params or {})
    if params.get("code_challenge_method") != "S256":
        return refuse("PKCE code_challenge_method must be S256; refusing",
                      got=params.get("code_challenge_method"))
    verifier = str(params.get("code_verifier", ""))
    if not (43 <= len(verifier) <= 128):
        return refuse("PKCE code_verifier must be 43-128 characters; refusing",
                      length=len(verifier))
    return Outcome(True, "ok", "PKCE shape valid (S256, verifier length in range)", {})


@dataclass
class HomeAssistantScope:
    """Scoping is LOAD-BEARING (§8 S-4). Deny-by-default: nothing is
    reachable unless it is Assist-exposed AND on the allowlist.
    ``expose_new`` defaults OFF and vendor defaults that would invert
    deny-by-default are configured away by construction — there is no
    code path here that turns expose-new on, and the proof function
    below reports the effective flags so a ship report can prove them.
    """

    expose_new: bool = False
    # entity_id -> room; the allowlist IS the per-room allowlist.
    allowlist: dict = field(default_factory=dict)
    assist_exposed: set = field(default_factory=set)

    def authorize(self, entity_id: str, action: str) -> Outcome:
        if self.expose_new:
            return refuse("expose-new is ON; deny-by-default scope violated — refusing all actions until it is OFF",
                          entity_id=entity_id, action=action)
        if entity_id not in self.allowlist:
            return refuse("entity outside the per-room allowlist; refusing fail-closed",
                          entity_id=entity_id, action=action,
                          allowlist=sorted(self.allowlist))
        if entity_id not in self.assist_exposed:
            return refuse("entity is not Assist-exposed; refusing (consume-only: Assist-exposed entities only)",
                          entity_id=entity_id, action=action)
        if action not in HM1_ALLOWED_ACTIONS:
            return refuse("action outside the allowed action set; refusing fail-closed",
                          entity_id=entity_id, action=action,
                          allowed=list(HM1_ALLOWED_ACTIONS))
        return Outcome(True, "ok", "entity + action authorized by scope",
                       {"entity_id": entity_id, "action": action,
                        "room": self.allowlist.get(entity_id)})


def default_home_scope() -> HomeAssistantScope:
    # Empty allowlist: with no caller-supplied allowlist, nothing is
    # authorized. That is the deny-by-default default, not a bug.
    return HomeAssistantScope(expose_new=False, allowlist={}, assist_exposed=set())


def home_assistant_scope_proof(scope: HomeAssistantScope) -> dict:
    """Configured-state proof: the ship report can quote this verbatim
    to prove the effective flags (expose-new, allowlist contents)."""
    return {
        "expose_new": bool(scope.expose_new),
        "deny_by_default": True,
        "allowlist": dict(sorted(scope.allowlist.items())),
        "assist_exposed": sorted(scope.assist_exposed),
        "allowed_actions": list(HM1_ALLOWED_ACTIONS),
    }


def build_assist_request(scope: HomeAssistantScope, host: str, entity_id: str,
                         action: str) -> Outcome:
    """Consume-only request builder. It validates LAN + scope + action
    and returns a request dict; it performs NO network call and NO
    state change. Execution, if any, is the caller's injected path."""
    lan = is_lan_target(host)
    if not lan.ok:
        return lan
    auth = scope.authorize(entity_id, action)
    if not auth.ok:
        return auth
    return Outcome(True, "ok", "Assist request validated (not executed by this module)",
                   {"host": host, "entity_id": entity_id, "action": action,
                    "room": scope.allowlist.get(entity_id)})


# ---------------------------------------------------------------- HM-2 ha-mcp author layer (toolbox routing only)

# Plan-carried claim (not a verified count by this module): ha-mcp
# exposes 95+ tools. The count is irrelevant to the gate: whatever the
# count, NONE of them becomes a declaration here.
HAMCP_TOOL_COUNT_PLAN_CLAIM = "95+"
HAMCP_FILESYSTEM_TOOLS = ("ha_mcp.read_file", "ha_mcp.write_file", "ha_mcp.list_files")
HAMCP_YAML_TOOLS = ("ha_mcp.read_yaml", "ha_mcp.write_yaml", "ha_mcp.edit_yaml")


@dataclass
class HamcpProfile:
    """Author-layer profile. ``is_default`` marks the default profile,
    in which filesystem + YAML tools are OFF and enabling them is
    refused (see hamcp_set_flag). Non-default profiles still route
    only via the toolbox; they never gain a declaration surface."""

    is_default: bool = True
    filesystem_tools: bool = False
    yaml_tools: bool = False


def default_hamcp_profile() -> HamcpProfile:
    return HamcpProfile(is_default=True, filesystem_tools=False, yaml_tools=False)


def hamcp_flag_proof(profile: HamcpProfile) -> dict:
    """Flag-state proof for the ship report."""
    return {
        "is_default": bool(profile.is_default),
        "filesystem_tools": bool(profile.filesystem_tools),
        "yaml_tools": bool(profile.yaml_tools),
    }


def hamcp_set_flag(profile: HamcpProfile, flag: str, value: bool) -> Outcome:
    """Enabling filesystem/YAML tools in the default profile is
    REFUSED. Disabling is always allowed. (A non-default profile may
    be configured by its owner through their own path; this function
    still never creates a declaration for the tool.)"""
    if flag not in ("filesystem_tools", "yaml_tools"):
        return refuse("unknown ha-mcp flag; refusing", flag=flag)
    if bool(value) and profile.is_default:
        return refuse(f"enabling {flag} in the default profile is refused; both flags stay OFF by default",
                      flag=flag, is_default=True)
    setattr(profile, flag, bool(value))
    return Outcome(True, "ok", f"ha-mcp flag {flag} set to {bool(value)}",
                   hamcp_flag_proof(profile))


def toolbox_route(tool_name: str, profile: HamcpProfile) -> Outcome:
    """The ONLY reach path for ha-mcp tools: via toolbox routing.
    - A name that is not an ha-mcp tool is not routed here.
    - A filesystem/YAML tool whose flag is OFF is refused.
    - Routing returns a routing decision, not a declaration and not
      an execution; execution is the toolbox's own confirmed path."""
    if not str(tool_name).startswith("ha_mcp."):
        return refuse("not an ha-mcp tool; toolbox routing for ha-mcp refuses non-ha-mcp names",
                      tool=tool_name)
    if tool_name in HAMCP_FILESYSTEM_TOOLS and not profile.filesystem_tools:
        return refuse("ha-mcp filesystem tool refused: flag OFF in this profile",
                      tool=tool_name, **hamcp_flag_proof(profile))
    if tool_name in HAMCP_YAML_TOOLS and not profile.yaml_tools:
        return refuse("ha-mcp YAML tool refused: flag OFF in this profile",
                      tool=tool_name, **hamcp_flag_proof(profile))
    return Outcome(True, "ok", "ha-mcp tool routed via toolbox only (no declaration created)",
                   {"tool": tool_name})


def declaration_surface() -> list:
    """Proof that this module exposes NO declaration surface at all:
    ha-mcp's 95+ tools must NEVER become declarations, so the list is
    empty by construction and there is no DECLARATIONS/TOOLS attribute
    on this module to read instead."""
    return []


# ---------------------------------------------------------------- RB-6 serial bridge (MS101 arm)

# Per-joint angle clamps as named constants. This module IS the clamp
# layer: no candidate provides firmware clamps, so no write may pass
# without these checks. Joint 4's 0..101 span is the v9.64 spider
# lead-screw gripper's measured range (0 = OPEN, 101 = CLOSED hard
# clamp, never past). Joints 1-3 spans below are conservative
# SOFTWARE defaults for this bridge, not claimed firmware limits;
# bench calibration may narrow them, never widen them past a
# re-verified value without a new review.
JOINT1_LIMIT = (-180.0, 180.0)
JOINT2_LIMIT = (-90.0, 90.0)
JOINT3_LIMIT = (-90.0, 120.0)
JOINT4_LIMIT = (0.0, 101.0)
JOINT_LIMITS = {
    "joint1": JOINT1_LIMIT,
    "joint2": JOINT2_LIMIT,
    "joint3": JOINT3_LIMIT,
    "joint4": JOINT4_LIMIT,
}
DEADMAN_TIMEOUT_S = 5.0


def serial_available() -> Outcome:
    """Lazy optional probe for pyserial. Absent -> honest unavailable."""
    try:
        import serial  # type: ignore
    except Exception:
        return unavailable("pyserial not installed; serial bridge unavailable")
    version = getattr(serial, "VERSION", None) or getattr(serial, "__version__", "unknown")
    return Outcome(True, "ok", "pyserial available (probe only; transport is via the injected executor)",
                   {"version": str(version)})


def arm_plan_only(joint: str, angle: float) -> Outcome:
    """Plan a wrapped move without an executor: always plan-only,
    never a write."""
    lo_hi = JOINT_LIMITS.get(joint)
    if lo_hi is None:
        return refuse("unknown joint; refusing", joint=joint)
    lo, hi = lo_hi
    if not (lo <= float(angle) <= hi):
        return refuse("joint angle outside clamp; refusing before any write",
                      joint=joint, angle=float(angle), clamp=[lo, hi])
    return plan_only("no executor injected: plan only, no execution",
                     joint=joint, angle=float(angle), clamp=[lo, hi])


class ArmBridge:
    """Wrapped tools ONLY: move_joint / home / grip.

    The brain never sees raw serial.write: this class has no raw-write
    method at all (no ``raw_write`` / ``serial_write`` / ``write_raw``
    attribute exists — the suite asserts that), and execution happens
    ONLY through the injected ``executor`` (the confirm gate + audit).
    Without an executor, every call is plan-only.

    Dead-man: a write requires a heartbeat within DEADMAN_TIMEOUT_S
    before the write. Expired or missing heartbeat -> refuse, before
    any executor call (so before any write).

    Upstream JSONL tracing is ON by default in the upstream tool, so
    this bridge configures it OFF: ``tracing_on`` defaults False, and
    tracing-on requires an explicit caller flag plus a caller-supplied
    sink. With tracing off, nothing is traced, period.
    """

    def __init__(self, executor=None, tracing_on: bool = False, trace_sink=None):
        self.executor = executor
        self.tracing_on = bool(tracing_on)
        self.trace_sink = trace_sink
        self._last_heartbeat = None

    def tracing_state(self) -> dict:
        return {"tracing_on": self.tracing_on,
                "tracing_default": False,
                "note": "upstream JSONL tracing is configured OFF unless the caller explicitly opts in"}

    def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()

    def _deadman_ok(self) -> Outcome:
        if self._last_heartbeat is None:
            return refuse("dead-man: no heartbeat before write; refusing",
                          timeout_s=DEADMAN_TIMEOUT_S)
        if time.monotonic() - self._last_heartbeat > DEADMAN_TIMEOUT_S:
            return refuse("dead-man expired before write; refusing",
                          timeout_s=DEADMAN_TIMEOUT_S)
        return Outcome(True, "ok", "dead-man alive", {})

    def _trace(self, record: dict) -> None:
        if self.tracing_on and self.trace_sink is not None:
            try:
                self.trace_sink(dict(record))
            except Exception:
                pass  # tracing must never break or fake a move

    def _execute(self, command: dict) -> Outcome:
        deadman = self._deadman_ok()
        if not deadman.ok:
            return deadman
        if self.executor is None:
            return plan_only("no executor injected (confirm gate + audit required): plan only, no execution",
                             command=command)
        self._trace(command)
        try:
            result = self.executor(command)
        except Exception as exc:  # executor failure is not a move
            return refuse(f"executor failed; no move claimed: {exc}", command=command)
        if isinstance(result, Outcome):
            return result
        return Outcome(True, "ok", "executor accepted wrapped command", {"command": command})

    def move_joint(self, joint: str, angle: float) -> Outcome:
        lo_hi = JOINT_LIMITS.get(joint)
        if lo_hi is None:
            return refuse("unknown joint; refusing", joint=joint)
        lo, hi = lo_hi
        if not (lo <= float(angle) <= hi):
            return refuse("joint angle outside clamp; refusing before any write",
                          joint=joint, angle=float(angle), clamp=[lo, hi])
        return self._execute({"tool": "move_joint", "joint": joint, "angle": float(angle)})

    def home(self) -> Outcome:
        # START pose (v9.64): servos 0/90/35/0 for joints 1-4 in order.
        # Each joint is clamp-checked by move_joint's path; home goes
        # through the same executor + dead-man gate via _execute, not
        # around it.
        for joint, angle in (("joint1", 0.0), ("joint2", 90.0),
                             ("joint3", 35.0), ("joint4", 0.0)):
            lo, hi = JOINT_LIMITS[joint]
            if not (lo <= angle <= hi):
                return refuse("home pose outside clamp; refusing", joint=joint, angle=angle)
        return self._execute({"tool": "home",
                              "pose": {"joint1": 0.0, "joint2": 90.0, "joint3": 35.0, "joint4": 0.0}})

    def grip(self, angle: float) -> Outcome:
        # Gripper = joint 4 screw angle: 0 OPEN, ~79 GRIP, 101 CLOSED
        # hard clamp. The clamp check below is the enforcement; the
        # executor never sees an out-of-clamp grip.
        return self.move_joint("joint4", float(angle))


# ---------------------------------------------------------------- PA-7 Presidio Image Redactor (PILOT ONLY)

# NER leg: ABSENT. Presidio's deployed spaCy model licence is
# STILL-OWED (O-24k, #1 ship-blocking for the redaction seat), so the
# spaCy model is NOT deployed and there is no NER leg in this module.
NER_AVAILABLE = False
# OCR leg: depends on Wave 5's RD bake-off, which produced NO winner,
# so the OCR leg is a facade returning unavailable, never fabricated text.
OCR_WINNER = None

# Custom recognizers ONLY, in own code: gitleaks-ported regex rules.
# These are pattern rules, not a claim to be gitleaks, and not
# Presidio's predefined recognizers. High-entropy generic rules are
# deliberately NOT included as a catch-all: a generic rule would
# redact (and mislabel) far more than it proves.
SECRET_RECOGNIZERS = (
    ("AWS_ACCESS_KEY_ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "AWS access key id pattern (gitleaks-ported)"),
    ("GITHUB_PAT", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
     "GitHub personal/oAuth token pattern (gitleaks-ported)"),
    ("GITHUB_FINE_GRAINED_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
     "GitHub fine-grained token pattern (gitleaks-ported)"),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
     "Slack token pattern (gitleaks-ported)"),
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
     "Private key block header (gitleaks-ported)"),
    ("GENERIC_API_KEY_ASSIGNMENT", re.compile(r"(?i)\b(?:api[_-]?key|secret|passwd|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
     "Key/secret assignment pattern (gitleaks-ported, assignment form only)"),
)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-2:]


def detect_secrets(text: str) -> list:
    """Own-code recognizer pass over TEXT (not an image, not OCR).
    Returns findings with masked previews only — never the secret
    itself in the finding record."""
    findings = []
    for name, pattern, _desc in SECRET_RECOGNIZERS:
        for match in pattern.finditer(text or ""):
            findings.append({"type": name, "start": match.start(), "end": match.end(),
                             "preview": _mask(match.group(0))})
    return findings


def apply_whole_window_exclusions(regions: list, excluded_window_ids) -> list:
    """Whole-window exclusions stay POLICY regardless of detector
    output: this function applies them independent of detection — it
    does not call any detector, and a region in an excluded window is
    marked excluded even if detection found nothing in it (and even
    if detection is unavailable)."""
    excluded = set(excluded_window_ids or [])
    out = []
    for region in regions or []:
        item = dict(region)
        item["excluded_by_policy"] = item.get("window_id") in excluded
        out.append(item)
    return out


def ocr_leg(_frame=None) -> Outcome:
    """OCR leg facade: Wave 5 RD bake-off produced NO winner, so this
    leg returns unavailable. It never fabricates OCR text."""
    return unavailable("OCR leg unavailable: Wave 5 RD bake-off produced no winner; no OCR text is fabricated",
                       winner=None)


def sole_gate_redact(_text_or_frame=None) -> Outcome:
    """The sole-gate function REFUSES, and states why. Build rule:
    recognizers + NER or the window doesn't go. NER is absent because
    the deployed spaCy model licence is STILL-OWED (O-24k, #1
    ship-blocking for the redaction seat) and the model is NOT
    deployed. Since NER is absent, this module can never be the sole
    gate before a cloud send — that refusal is the feature."""
    return refuse(
        "sole-gate redaction refused: recognizers + NER are both required "
        "and the NER leg is absent — Presidio's deployed spaCy model "
        "licence is STILL-OWED (O-24k, #1 ship-blocking for the redaction "
        "seat), so the spaCy model is NOT deployed. This pilot must never "
        "be the sole gate before a cloud send.",
        ner_available=False, ocr_winner=None, licence_item="O-24k STILL-OWED")


def pilot_redact_text(text: str) -> dict:
    """PILOT ONLY redaction over text with own-code recognizers.
    This is a pilot measurement aid, NOT a gate: the result dict says
    so, and sole_gate_redact() above still refuses. Never use this
    dict's output as proof that a window is safe to send."""
    findings = detect_secrets(text or "")
    redacted = text or ""
    # Redact from the end so earlier spans stay valid.
    for finding in sorted(findings, key=lambda f: f["start"], reverse=True):
        redacted = redacted[:finding["start"]] + "[REDACTED]" + redacted[finding["end"]:]
    return {
        "pilot_only": True,
        "not_a_sole_gate": True,
        "warning": "pilot measurement only; sole-gate redaction refuses (NER absent, O-24k STILL-OWED)",
        "redacted": redacted,
        "findings": findings,
        "finding_count": len(findings),
    }


def planted_secret_eval(frames: list) -> dict:
    """Planted-secret eval harness: recall measured on synthetic
    frames. Each frame: {"text": str, "planted": [recognizer names]}.
    Recall = planted secrets whose type was detected / planted total.
    A frame with no planted secrets contributes nothing to recall
    (it is counted separately, not as a free pass)."""
    total = 0
    found = 0
    per_type = {}
    frames_scored = 0
    for frame in frames or []:
        planted = list((frame or {}).get("planted") or [])
        if not planted:
            continue
        frames_scored += 1
        detected = {f["type"] for f in detect_secrets((frame or {}).get("text", ""))}
        for name in planted:
            total += 1
            hit = name in detected
            found += 1 if hit else 0
            got, want = per_type.get(name, (0, 0))
            per_type[name] = (got + (1 if hit else 0), want + 1)
    return {
        "frames_scored": frames_scored,
        "planted_total": total,
        "planted_found": found,
        "recall": (found / total) if total else None,
        "per_type": {k: {"found": v[0], "total": v[1],
                         "recall": (v[0] / v[1]) if v[1] else None}
                     for k, v in sorted(per_type.items())},
    }
