"""S1 Format (v5.67, Skills track, plan section 5.19, wave 27 SK-*).

A spec-faithful reader + validator for Agent Skills SKILL.md files,
plus the S-1 funnel-declaration ledger. Standard library only; no
YAML dependency is added (PyYAML is not a declared dependency), so
the frontmatter parser below is hand-written and deliberately
small: it understands ``key: value`` lines, quoted scalars, one
level of indented ``metadata:`` children, and ``- item`` lists
under an empty-valued key. Anything richer is reported as a
finding, never silently accepted or silently dropped.

Spec rules pinned here (Agent Skills spec, SK-1.1):
- A skill is a directory containing SKILL.md: YAML frontmatter
  followed by a Markdown body, with optional scripts/, references/
  and assets/ beside it.
- Required frontmatter: ``name`` (1-64 chars, lowercase
  alphanumerics + hyphens, no leading / trailing / consecutive
  hyphens, and it MUST match the parent directory name) and
  ``description`` (1-1024 chars, what the skill does + when to
  use it).
- Optional frontmatter: ``license``, ``compatibility`` (at most
  500 chars), ``metadata`` (an arbitrary string map) and
  ``allowed-tools`` (space-separated, explicitly Experimental in
  the spec).
- Progressive disclosure: frontmatter metadata is ~100 tokens,
  body instructions <5,000 tokens recommended, resources on
  demand. No official JSON Schema exists; the normative rules
  live in spec prose, which is why this module validates prose
  rules and returns findings instead of schema errors.

FENCE 1 — allowed-tools IS NEUTRALISED (coordinator brief fence 1)
------------------------------------------------------------------
``allowed-tools`` is parsed ONLY into ``allowed_tools_hint``: a
plain list of the declared tool names, for display and audit.
This module provides NO API that treats that list as a grant,
a permission, a capability, or an exemption from any confirm
gate: there is no ``grants`` field on any result, no function
whose name or return value grants anything from allowed-tools,
and a skill can never waive the INV-3 confirm gate by declaring
tools. Callers that need enforcement must go through Leaf 2's
gate, which consults the VYUK capability manifest below, not
allowed-tools. On import, the personal library strips
allowed-tools entirely (see personal_skills).

VYUK EXTENSION — namespaced metadata capability manifest
--------------------------------------------------------
The spec's ``metadata`` map carries VYUK's extension contract
(from SK2-02 / SK4-02): a Deno-style scoped manifest, written
as the ``vyuk-capabilities`` metadata value, a semicolon
separated list of entries::

    read:~/Documents/**; write:~/Documents/skills/**;
    net:*.example.com; deny:read:/etc/**

Scopes: ``read:``, ``write:``, ``net:``, ``env:``, ``run:``,
``tools:``, ``wasm:``. Path/host scoping is part of the entry: read/write
targets are path patterns, net targets are host patterns,
env targets are variable names, run/tools targets are command /
tool names. ``wasm:`` targets are the bare ``.wasm`` module
filename inside the skill directory (B8 contained-runtime
pilot: one module per skill, signature-tier like ``run:``). ``deny:`` entries override allows: the effective
set is allows minus anything a deny pattern matches
(deny-overrides-allow normalisation). Classification of a skill
into a trust class is Leaf 2's job and is NOT done here.

Signature tier: ``run:`` entries, and anything
credential-adjacent (env entries naming credential-like
variables, read/write entries pointing at credential-like
files such as .env / *.pem / *.key / credentials / id_rsa),
are signature-tier: they are reported in
``signature_tier_requests`` and are grantable only to
VYUK-signed skills. An unsigned skill's usable set is
``manifest["grantable_without_signature"]``; this module never
grants the signature tier itself, it only reports the request.

MALFORMED = FINDINGS, NEVER EXCEPTIONS
---------------------------------------
Every public parse/validate entry point catches its own
failures and returns them as findings. A malformed skill is a
result with error findings and ``valid == False``, never a
raised exception from this module.

S-1 LEDGER
----------
``measure_declaration_cost`` prices a skill's funnel-tool
declarations (JSON-serialised, the same shape the shipped
registry uses) against the INV-1 cap of 240,000 B with the
current fitted baseline of 234,091 B, returning the fitted
total, headroom and over-by amount. ``append_overflow_entry``
writes an over-budget skill to the overflow queue in the
established entry format (cost + reason + alternatives) so
an over-budget skill is never silently dropped. This leaf
adds NO funnel tool declarations: the fitted total stays
234,091 B, and the trigger seam for any future skill tool
is a parent-gate decision, not something this module wires.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- constants

NAME_MIN_LEN = 1
NAME_MAX_LEN = 64
DESCRIPTION_MIN_LEN = 1
DESCRIPTION_MAX_LEN = 1024
COMPATIBILITY_MAX_LEN = 500
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "metadata",
     "allowed-tools"}
)
MANIFEST_SCOPES = frozenset({"read", "write", "net", "env", "run", "tools",
                             "wasm"})
SIGNATURE_TIER_SCOPES = frozenset({"run", "wasm"})
_HOST_RE = re.compile(r"^(\*\.)?[A-Za-z0-9.-]+(:[0-9]{1,5})?$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$|\*$")
_CREDENTIAL_NAME_RE = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|PRIVATE|BEARER|"
    r"SESSION|COOKIE",
    re.IGNORECASE,
)
_CREDENTIAL_FILE_RE = re.compile(
    r"(^|/)\.env(\.|$)|(^|/)[^/]*credentials[^/]*$|(^|/)\.pem$|"
    r"\.pem$|\.key$|(^|/)id_rsa",
    re.IGNORECASE,
)
INV1_CAP_BYTES = 240000
FITTED_BASELINE_BYTES = 234091
FITTED_HEADROOM_BYTES = INV1_CAP_BYTES - FITTED_BASELINE_BYTES  # 5909
OVERFLOW_QUEUE_DEFAULT = (
    Path.home() / "workspace" / "code-lab" / "overflow_queue.md"
)
OVERFLOW_QUEUE_HEADER = (
    "# Overflow queue — items that could not be fitted, to be "
    "reviewed and added\n"
)


# ---------------------------------------------------------------- findings

@dataclass
class Finding:
    code: str
    severity: str  # "error" | "warning"
    message: str
    field: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "field": self.field}


def _err(code: str, message: str, field: str = "") -> Finding:
    return Finding(code, "error", message, field)


def _warn(code: str, message: str, field: str = "") -> Finding:
    return Finding(code, "warning", message, field)


# ---------------------------------------------------------------- frontmatter

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str):
    """Split SKILL.md text into (frontmatter dict, body, findings).

    Never raises: unparseable input yields findings and an empty
    frontmatter dict. Unknown frontmatter keys are reported as
    findings (``unknown_frontmatter_key``) and KEPT in the
    returned dict under ``_unknown`` — never silently dropped.
    """
    findings: list[Finding] = []
    if not isinstance(text, str):
        return {}, "", [_err("not_text", "SKILL.md content is not text")]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, [_err(
            "missing_frontmatter",
            "SKILL.md does not start with a '---' frontmatter block")]
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return {}, text, [_err(
            "unterminated_frontmatter",
            "SKILL.md frontmatter block has no closing '---' line")]
    front: dict = {}
    unknown: dict = {}
    current_parent: str | None = None
    current_list_key: str | None = None
    for raw in lines[1:closing]:
        if not raw.strip():
            continue
        indent_match = re.match(r"^(\s+)(.+)$", raw)
        if indent_match and current_parent is not None:
            inner = indent_match.group(2)
            kv = re.match(r"^([A-Za-z0-9_.-]+):\s*(.*)$", inner)
            if kv:
                parent = front.setdefault(current_parent, {})
                if isinstance(parent, dict):
                    parent[kv.group(1)] = _unquote(kv.group(2))
                current_list_key = None
                continue
        stripped = raw.strip()
        if stripped.startswith("- ") and current_list_key is not None:
            front.setdefault(current_list_key, []).append(
                _unquote(stripped[2:]))
            continue
        kv = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if not kv:
            findings.append(_warn(
                "unparsed_frontmatter_line",
                f"Frontmatter line could not be parsed and was kept "
                f"as text, not dropped: {raw.strip()!r}"))
            current_parent = None
            current_list_key = None
            continue
        key, value = kv.group(1), kv.group(2).strip()
        if key not in ALLOWED_FRONTMATTER_KEYS:
            findings.append(_warn(
                "unknown_frontmatter_key",
                f"Unknown frontmatter key {key!r}: kept and reported, "
                f"never silently dropped", field=key))
            unknown[key] = _unquote(value) if value else ""
            current_parent = None
            current_list_key = None
            continue
        if value == "":
            # Either a nested map (metadata:) or a dash list.
            front[key] = {} if key == "metadata" else []
            current_parent = key if key == "metadata" else None
            current_list_key = key if key != "metadata" else None
        else:
            front[key] = _unquote(value)
            current_parent = None
            current_list_key = None
    if unknown:
        front["_unknown"] = unknown
    body = "\n".join(lines[closing + 1:])
    return front, body, findings


def parse_allowed_tools(value) -> list[str]:
    """Parse an allowed-tools declaration into a hint list.

    The result is a HINT only (see fence 1 in the module
    docstring). It is never a grant, and no caller may treat it
    as one. Accepts a space-separated string or a list.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = str(value).split()
    return [item for item in items if item]


# ---------------------------------------------------------------- manifest

def _is_credential_adjacent(scope: str, target: str) -> bool:
    if scope == "env":
        return bool(_CREDENTIAL_NAME_RE.search(target)) or target == "*"
    if scope in ("read", "write"):
        return bool(_CREDENTIAL_FILE_RE.search(target))
    if scope == "tools":
        return bool(_CREDENTIAL_NAME_RE.search(target))
    return False


def parse_capability_manifest(metadata) -> tuple[dict, list[Finding]]:
    """Parse the VYUK namespaced metadata capability manifest.

    Reads ``vyuk-capabilities`` (also ``vyuk_capabilities`` /
    ``capabilities``) from the metadata map. Returns
    (manifest, findings). ``manifest["effective"]`` is the
    deny-overrides-allow normalisation; ``manifest["signature_
    tier"]`` lists the signature-tier requests inside it, and
    ``manifest["grantable_without_signature"]`` is effective
    minus the signature tier. No entry here is granted by this
    module; granting is Leaf 2's gate.
    """
    findings: list[Finding] = []
    manifest = {"allows": [], "denies": [], "effective": [],
                "signature_tier": [], "grantable_without_signature": [],
                "requires_signature": False, "raw": ""}
    if not isinstance(metadata, dict):
        if metadata is not None:
            findings.append(_warn(
                "metadata_not_a_map",
                "Frontmatter 'metadata' is not a map, so no VYUK "
                "capability manifest could be read from it",
                field="metadata"))
        return manifest, findings
    raw = None
    for key in ("vyuk-capabilities", "vyuk_capabilities", "capabilities"):
        if key in metadata:
            raw = metadata[key]
            break
    if raw is None:
        return manifest, findings
    if not isinstance(raw, str):
        findings.append(_warn(
            "manifest_not_text",
            "VYUK capability manifest value is not text and was "
            "ignored", field="metadata"))
        return manifest, findings
    manifest["raw"] = raw
    for chunk in re.split(r"[;\n]", raw):
        entry_text = chunk.strip()
        if not entry_text:
            continue
        is_deny = entry_text.startswith("deny:")
        rest = entry_text[len("deny:"):] if is_deny else entry_text
        parts = rest.split(":", 1)
        if len(parts) != 2 or not parts[0] or not parts[1].strip():
            findings.append(_err(
                "manifest_malformed_entry",
                f"Capability entry {entry_text!r} is not "
                f"'[deny:]<scope>:<target>'", field="metadata"))
            continue
        scope, target = parts[0].strip(), parts[1].strip()
        if scope not in MANIFEST_SCOPES and scope != "*":
            findings.append(_err(
                "manifest_unknown_scope",
                f"Capability scope {scope!r} is not one of "
                f"{sorted(MANIFEST_SCOPES)}", field="metadata"))
            continue
        if scope == "net" and target != "*":
            if "://" in target or not _HOST_RE.match(target):
                findings.append(_err(
                    "manifest_bad_host_scope",
                    f"net: target {target!r} is not a host pattern",
                    field="metadata"))
                continue
        if scope == "env" and not _ENV_RE.match(target):
            findings.append(_err(
                "manifest_bad_env_scope",
                f"env: target {target!r} is not a variable name",
                field="metadata"))
            continue
        if any(ch.isspace() for ch in target):
            findings.append(_err(
                "manifest_target_has_spaces",
                f"Capability target {target!r} contains whitespace",
                field="metadata"))
            continue
        entry = {"scope": scope, "target": target, "raw": entry_text}
        (manifest["denies"] if is_deny else manifest["allows"]).append(entry)
    # Deny-overrides-allow normalisation.
    effective = []
    for allow in manifest["allows"]:
        blocked = False
        for deny in manifest["denies"]:
            scope_match = (deny["scope"] == allow["scope"]
                           or deny["scope"] == "*")
            if scope_match and fnmatch.fnmatchcase(
                    allow["target"], deny["target"]):
                blocked = True
                break
        if not blocked:
            effective.append(allow)
    manifest["effective"] = effective
    signature = []
    for entry in effective:
        if (entry["scope"] in SIGNATURE_TIER_SCOPES
                or _is_credential_adjacent(entry["scope"],
                                           entry["target"])):
            signature.append(entry)
    manifest["signature_tier"] = signature
    manifest["requires_signature"] = bool(signature)
    manifest["grantable_without_signature"] = [
        e for e in effective if e not in signature]
    return manifest, findings


def signature_tier_requests(manifest: dict) -> list[dict]:
    """Signature-tier requests in a parsed manifest (run: and
    credential-adjacent entries). Empty for a manifest without
    them. Reporting only — granting is Leaf 2's gate."""
    if not isinstance(manifest, dict):
        return []
    return list(manifest.get("signature_tier", []))


# ---------------------------------------------------------------- validation

@dataclass
class SkillValidation:
    dir_name: str = ""
    name: str = ""
    description: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict = field(default_factory=dict)
    allowed_tools_hint: list = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    unknown_keys: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    body_chars: int = 0
    valid: bool = False

    def to_dict(self) -> dict:
        # NOTE: deliberately no "grants" key (fence 1).
        return {
            "dir_name": self.dir_name,
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": self.metadata,
            "allowed_tools_hint": list(self.allowed_tools_hint),
            "manifest": self.manifest,
            "signature_tier_requests":
                signature_tier_requests(self.manifest),
            "unknown_keys": self.unknown_keys,
            "findings": [f.to_dict() if isinstance(f, Finding) else f
                         for f in self.findings],
            "body_chars": self.body_chars,
            "valid": self.valid,
        }


def validate_name(name, dir_name: str = "") -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(name, str) or not name:
        return [_err("missing_name", "Frontmatter 'name' is missing "
                     "or empty", field="name")]
    if not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
        findings.append(_err(
            "name_length",
            f"Skill name is {len(name)} chars; the spec allows "
            f"{NAME_MIN_LEN}-{NAME_MAX_LEN}", field="name"))
    if not NAME_RE.match(name):
        findings.append(_err(
            "name_charset",
            "Skill name must be lowercase alphanumerics + hyphens, "
            "with no leading, trailing or consecutive hyphens",
            field="name"))
    if dir_name and name != dir_name:
        findings.append(_err(
            "name_dir_mismatch",
            f"Skill name {name!r} does not match its parent "
            f"directory {dir_name!r}; the spec requires a match",
            field="name"))
    return findings


def validate_skill_text(text: str, dir_name: str = "") -> SkillValidation:
    """Validate SKILL.md text. Never raises on malformed input."""
    try:
        front, body, findings = parse_frontmatter(text)
    except Exception as exc:  # pragma: no cover - defensive
        return SkillValidation(
            dir_name=dir_name,
            findings=[_err("parse_failed",
                           f"SKILL.md could not be parsed: {exc}")])
    name = front.get("name", "") if isinstance(front.get("name"), str) \
        else ""
    description = front.get("description", "") \
        if isinstance(front.get("description"), str) else ""
    license_text = front.get("license", "") \
        if isinstance(front.get("license"), str) else ""
    compatibility = front.get("compatibility", "") \
        if isinstance(front.get("compatibility"), str) else ""
    metadata = front.get("metadata", {}) \
        if isinstance(front.get("metadata"), dict) else {}
    if front.get("metadata") is not None \
            and not isinstance(front.get("metadata"), dict):
        findings.append(_warn("metadata_not_a_map",
                              "Frontmatter 'metadata' is not a map",
                              field="metadata"))
    findings.extend(validate_name(name, dir_name))
    if not description:
        findings.append(_err("missing_description",
                             "Frontmatter 'description' is missing "
                             "or empty", field="description"))
    elif not (DESCRIPTION_MIN_LEN <= len(description)
              <= DESCRIPTION_MAX_LEN):
        findings.append(_err(
            "description_length",
            f"Description is {len(description)} chars; the spec "
            f"allows {DESCRIPTION_MIN_LEN}-{DESCRIPTION_MAX_LEN}",
            field="description"))
    if len(compatibility) > COMPATIBILITY_MAX_LEN:
        findings.append(_err(
            "compatibility_length",
            f"Compatibility is {len(compatibility)} chars; the "
            f"spec allows at most {COMPATIBILITY_MAX_LEN}",
            field="compatibility"))
    manifest, manifest_findings = parse_capability_manifest(metadata)
    findings.extend(manifest_findings)
    result = SkillValidation(
        dir_name=dir_name,
        name=name,
        description=description,
        license=license_text,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools_hint=parse_allowed_tools(front.get("allowed-tools")),
        manifest=manifest,
        unknown_keys=front.get("_unknown", {}),
        findings=findings,
        body_chars=len(body),
        valid=False,
    )
    result.valid = not any(f.severity == "error" for f in findings)
    return result


def validate_skill_dir(skill_dir) -> SkillValidation:
    """Validate the SKILL.md inside a skill directory. Never
    raises: a missing directory or file is an error finding."""
    path = Path(skill_dir)
    dir_name = path.name
    try:
        if not path.is_dir():
            return SkillValidation(
                dir_name=dir_name,
                findings=[_err("missing_directory",
                               f"Skill directory {str(path)!r} does "
                               f"not exist")])
        skill_file = path / "SKILL.md"
        if not skill_file.is_file():
            return SkillValidation(
                dir_name=dir_name,
                findings=[_err("missing_skill_md",
                               f"Skill directory {str(path)!r} has "
                               f"no SKILL.md")])
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return SkillValidation(
            dir_name=dir_name,
            findings=[_err("read_failed",
                           f"Skill could not be read: {exc}")])
    return validate_skill_text(text, dir_name=dir_name)


def validate_skill_file(skill_file, dir_name: str = "") -> SkillValidation:
    """Validate a SKILL.md file directly. Never raises."""
    path = Path(skill_file)
    if not dir_name:
        dir_name = path.parent.name
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return SkillValidation(
            dir_name=dir_name,
            findings=[_err("read_failed",
                           f"SKILL.md could not be read: {exc}")])
    return validate_skill_text(text, dir_name=dir_name)


# ---------------------------------------------------------------- S-1 ledger

def measure_declaration_cost(declarations,
                             baseline_bytes: int = FITTED_BASELINE_BYTES,
                             cap_bytes: int = INV1_CAP_BYTES) -> dict:
    """Price funnel-tool declarations against the INV-1 cap.

    ``declarations`` is a JSON-serialisable list in the same
    shape the shipped registry uses (see tools.get_gemini_
    declarations). Returns the skill's own cost, the fitted
    total against the current baseline, headroom, and over-by
    amount. Never raises on unserialisable input: that is
    reported as a cost of None with an error field.
    """
    try:
        cost = len(json.dumps(declarations))
        error = ""
    except (TypeError, ValueError) as exc:
        return {"declarations_count": None, "cost_bytes": None,
                "baseline_bytes": baseline_bytes,
                "cap_bytes": cap_bytes, "fitted_bytes": None,
                "headroom_bytes": cap_bytes - baseline_bytes,
                "over_by_bytes": None, "fits": False,
                "error": f"declarations are not JSON-serialisable: {exc}"}
    count = len(declarations) if isinstance(declarations, (list, tuple)) \
        else None
    fitted = baseline_bytes + cost
    return {"declarations_count": count, "cost_bytes": cost,
            "baseline_bytes": baseline_bytes, "cap_bytes": cap_bytes,
            "fitted_bytes": fitted,
            "headroom_bytes": cap_bytes - fitted,
            "over_by_bytes": max(0, fitted - cap_bytes),
            "fits": fitted <= cap_bytes, "error": error}


def measure_skill_declaration_cost(declarations) -> dict:
    """Alias of measure_declaration_cost with the shipped
    baseline/cap pinned (see measure_declaration_cost)."""
    return measure_declaration_cost(declarations)


def append_overflow_entry(item: str, cost_bytes, reason: str,
                          alternatives, queue_path=None) -> dict:
    """Append an over-budget item to the overflow queue.

    Entry format (established by the nonstop protocol): Item,
    Cost, Why it does not fit, Alternatives, Status. Appends
    only — existing entries are never rewritten or dropped.
    Returns the entry as written plus the queue path.
    """
    path = Path(queue_path) if queue_path is not None \
        else OVERFLOW_QUEUE_DEFAULT
    if isinstance(alternatives, str):
        alternatives_text = alternatives
    else:
        alternatives_text = "; ".join(str(a) for a in alternatives)
    entry = (
        f"\n- Item: {item}\n"
        f"- Cost: {cost_bytes} B\n"
        f"- Why it does not fit: {reason}\n"
        f"- Alternatives: {alternatives_text}\n"
        f"- Status: QUEUED\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(OVERFLOW_QUEUE_HEADER
                        + "\n## Queued items\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    return {"item": item, "cost_bytes": cost_bytes, "reason": reason,
            "alternatives": alternatives_text, "status": "QUEUED",
            "queue_path": str(path), "entry": entry}
