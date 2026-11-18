"""Routine templates (RET-5) — a gallery of shareable routines.

A template is DATA, never code: a named list of step descriptions
(tool + arg-shape with placeholders), in the same shape as a
routine's saved steps (core/brain/planner_routines.py). A template
is never a recording of a real run.

Format (JSON, versioned)::

    {"format_version": 1, "name": "Morning check",
     "description": "Time and battery, no arguments.",
     "steps": [{"tool": "get_current_time_date", "args": {}},
               {"tool": "get_weather",
                "args": {"location": {"$template": "location"},
                         "units": "celsius"}}]}

A placeholder is exactly {"$template": "<name>"} — a slot the
importer fills. Filled values are validated with the same rules
as exported values.

The validator runs on export AND on import and refuses the whole
template with the reason — never silently stripped:
  * any absolute path;
  * any value that looks like a username / address (an email
    address), a credential, or a secret (the H22 rule from
    core/confirm_gate_scrub — imported, not copied, so the rule
    cannot drift);
  * any value that looks like a person's name in a person-shaped
    slot (a two-capitalized-words value under a name/contact/
    recipient-style key) — a heuristic, stated as one: it catches
    "Aayur Gattani" under "contact" and lets "New York" under
    "location" through;
  * any file content (a template carries arg-shapes, never file
    bytes): a string over 1024 characters, a NUL byte, or a
    data: URI;
  * any code-bearing value (eval/exec/__import__ and friends);
  * any step whose tool is not in the registry, or whose args do
    not validate against the tool's schema — a placeholder in an
    arg is checked against the schema with a shape-correct dummy
    of the slot's declared type, so the placeholder itself never
    fails the schema check;
  * any step that is not READ-class (the funnel's read
    classification — core/funnel/tools_gate_readclass._is_read_class,
    the single source of truth).

v1 RULE (B-119): templates may contain only READ-class steps.
A gated step is refused at export, the refusal explains why, and
the refusal carries the read-only steps so the caller can offer
to export those instead (F-201). Gated steps in templates are a
later decision, not smuggled into RET-5.

Parsing is json.loads only, size-capped (MAX_TEMPLATE_BYTES), no
pickle/eval/exec. Export is confirm-gated at the call site: this
module's export_template() refuses unless confirmed=True — the
gallery shows the exact JSON in a dialog first and only passes
confirmed=True after the user says yes. Importing a template
never pre-approves its steps: the gallery saves the imported
routine through RET-2's confirm-gated save_routine action (the
funnel), and the steps still confirm at run time.

What this module never does: it never writes the routine store
(the gallery saves through the funnel), never touches the funnel
or the confirm gates, never charges anything (ruling 34 is open),
and never scores or ranks a template (no leaderboard — the
report's not-to-chase list is a hard constraint).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .confirm_gate_scrub import _is_sensitive, _looks_like_secret
from .funnel.tools_gate_readclass import _is_read_class

TEMPLATE_FORMAT_VERSION = 1
MAX_TEMPLATE_BYTES = 65536
MAX_VALUE_CHARS = 1024
PLACEHOLDER_KEY = "$template"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})+\b")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_CODE_TOKENS = ("eval(", "exec(", "__import__", "__class__",
                "__dict__", "__subclasses__", "os.system",
                "subprocess", "pickle.loads", "marshal.loads",
                "compile(")
_PERSON_KEYS = frozenset({
    "name", "contact", "recipient", "to", "from", "user", "author",
    "owner", "person", "friend", "contact_json", "username",
    "address",
})


def _is_absolute_path(value):
    return (os.path.isabs(value) or value.startswith("\\\\")
            or bool(_WIN_ABS_RE.match(value)))


def _scan_value(key, value, problems, placeholders):
    """Walk one arg value. `problems` gains refusal reasons;
    `placeholders` gains placeholder names found. A placeholder
    dict is exactly {PLACEHOLDER_KEY: <non-empty str>} — anything
    else under a "$" key is a problem."""
    if isinstance(value, dict):
        if set(value) == {PLACEHOLDER_KEY}:
            name = value[PLACEHOLDER_KEY]
            if isinstance(name, str) and name.strip():
                placeholders.append(name.strip())
            else:
                problems.append("placeholder %r has no name" % (value,))
            return
        for k, v in value.items():
            if isinstance(k, str) and k.startswith("$"):
                problems.append("unknown template marker %r" % k)
                continue
            _scan_value(str(k), v, problems, placeholders)
        return
    if isinstance(value, list):
        for item in value:
            _scan_value(key, item, problems, placeholders)
        return
    if not isinstance(value, str):
        return
    if _is_absolute_path(value):
        problems.append("absolute path %r" % value)
        return
    if _EMAIL_RE.search(value):
        problems.append("looks like an email address / username: %r"
                        % value)
        return
    if any(tok in value for tok in _CODE_TOKENS):
        problems.append("code-bearing value %r" % value)
        return
    if (len(value) > MAX_VALUE_CHARS or "\x00" in value
            or value.startswith("data:")):
        problems.append("file content (over %d characters, a NUL byte, "
                        "or a data: URI)" % MAX_VALUE_CHARS)
        return
    if _looks_like_secret(value):
        problems.append("looks like a secret: %r" % value)
        return
    if _is_sensitive(key):
        problems.append("sensitive key %r carries a value" % key)
        return
    if key.lower() in _PERSON_KEYS and _NAME_RE.search(value):
        problems.append("looks like a person's name: %r" % value)
        return


def _placeholder_dummy(tool, key, registry):
    """A shape-correct stand-in for a placeholder when the schema
    is checked: the slot's declared type, from the registry's
    declarations when it has them (the live registry), else the
    string "x" (a fake registry with no schema). A placeholder is
    a value the importer fills; the schema check must see a value
    of the declared type, not the {"$template": ...} dict."""
    try:
        decls = registry.get_declarations()
    except Exception:  # noqa: BLE001 — no schema, fall back
        return "x"
    for decl in decls or []:
        if decl.get("name") != tool:
            continue
        props = (decl.get("parameters") or {}).get("properties") or {}
        spec = props.get(key) or {}
        kind = spec.get("type")
        if isinstance(kind, list):
            kind = kind[0] if kind else "string"
        return {"string": "x", "integer": 0, "number": 0,
                "boolean": False, "array": [], "object": {}}.get(
                    kind, "x")
    return "x"


def _substitute_placeholders(value, tool, key, registry):
    """The same value with every placeholder replaced by a
    shape-correct dummy (for schema validation only — the
    template itself keeps the placeholders)."""
    if isinstance(value, dict):
        if set(value) == {PLACEHOLDER_KEY}:
            return _placeholder_dummy(tool, key, registry)
        return {k: _substitute_placeholders(v, tool, str(k), registry)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_placeholders(v, tool, key, registry)
                for v in value]
    return value


def _scan_steps(steps, registry, is_read, allow_placeholders=True):
    """Validate a step list. Returns (ok, reason, read_only_steps):
    ok False with a value/registry refusal carries read_only_steps
    None (the refusal is whole, no partial offer); ok False with a
    gated refusal carries the read-only steps so the caller can
    offer to export those instead."""
    if not isinstance(steps, list) or not steps:
        return False, "a template needs at least one step", None
    read_only = []
    gated = []
    for index, step in enumerate(steps):
        where = "step %d" % (index + 1)
        if not isinstance(step, dict) or not isinstance(
                step.get("tool"), str):
            return False, "%s is not a tool call" % where, None
        tool = step["tool"]
        args = step.get("args") or {}
        if not isinstance(args, dict):
            return False, "%s (%s): args must be an object" % (where, tool), None
        check_args = {k: _substitute_placeholders(v, tool, str(k), registry)
                      for k, v in args.items()}
        try:
            err = registry.validate_args(tool, check_args)
        except Exception as exc:  # noqa: BLE001 — no validation, no trust
            return False, "%s (%s): args could not be validated: %s" % (
                where, tool, exc), None
        if err is not None:
            return False, "%s (%s): %s" % (where, tool, err), None
        problems, placeholders = [], []
        for k, v in args.items():
            _scan_value(str(k), v, problems, placeholders)
        if problems:
            return False, "%s (%s): %s" % (where, tool, problems[0]), None
        if placeholders and not allow_placeholders:
            return False, ("%s (%s): a routine step carries a "
                           "placeholder %r — routines hold frozen "
                           "literals, never placeholders"
                           % (where, tool, placeholders[0])), None
        if is_read(tool, args):
            read_only.append(step)
        else:
            gated.append(step)
    if gated:
        names = ", ".join(sorted({s["tool"] for s in gated}))
        return False, ("template contains a gated step (%s) — v1 "
                       "templates are read-only; gated steps in "
                       "templates are a later decision"
                       % names), read_only
    return True, None, None


def _live_registry():
    from .funnel.tools_registry import tools as _tools
    return _tools


def _check_template_shape(template):
    """The structural checks shared by export and import (before
    the step checks): dict, version, name, steps list."""
    if not isinstance(template, dict):
        return "a template must be a JSON object"
    version = template.get("format_version")
    if version != TEMPLATE_FORMAT_VERSION:
        return ("unknown template version %r — this build reads "
                "version %d only" % (version, TEMPLATE_FORMAT_VERSION))
    name = template.get("name")
    if not isinstance(name, str) or not name.strip():
        return "a template needs a name"
    if not isinstance(template.get("steps"), list) \
            or not template["steps"]:
        return "a template needs at least one step"
    return None


def build_template(routine, registry=None, is_read=None,
                   read_only_only=False):
    """Build a template dict from a routine record (the
    planner_routines store shape: {"name", "actions"}). Returns
    (ok, template, reason, read_only_steps). A gated refusal
    carries the read-only steps; pass read_only_only=True to
    build a template from those instead (the F-201 offer)."""
    if registry is None:
        registry = _live_registry()
    if is_read is None:
        is_read = _is_read_class
    name = ""
    steps = None
    if isinstance(routine, dict):
        name = str(routine.get("name") or "").strip()
        steps = routine.get("actions")
    if not name:
        return False, None, "a template needs a routine name", None
    if not isinstance(steps, list) or not steps:
        return False, None, "routine %r has no steps" % name, None
    ok, reason, read_only = _scan_steps(
        steps, registry, is_read, allow_placeholders=False)
    if not ok:
        if (read_only_only and read_only
                and reason is not None and "gated" in reason):
            steps = read_only
        else:
            return False, None, reason, read_only
    template = {"format_version": TEMPLATE_FORMAT_VERSION,
                "name": name,
                "steps": [{"tool": s["tool"],
                           "args": dict(s.get("args") or {})}
                          for s in steps]}
    return True, template, None, None


def template_json(template):
    """The exact JSON for a template dict — canonical form
    (sorted keys, 2-space indent), the string the export dialog
    shows and the export returns."""
    return json.dumps(template, indent=2, sort_keys=True)


def export_template(template, confirmed=False, registry=None,
                    is_read=None):
    """Return the template's exact JSON — but only with
    confirmed=True. The gallery shows the JSON in a dialog first
    and passes confirmed=True only after the user says yes; an
    unconfirmed export is refused (negative check). The template
    is re-validated here, so a hand-built dict cannot skip the
    validator. Returns (ok, json_str, reason)."""
    if not confirmed:
        return False, "", ("export needs confirmation — the exact "
                           "template JSON must be shown first")
    shape_err = _check_template_shape(template)
    if shape_err is not None:
        return False, "", shape_err
    if registry is None:
        registry = _live_registry()
    if is_read is None:
        is_read = _is_read_class
    ok, reason, _ro = _scan_steps(template["steps"], registry, is_read)
    if not ok:
        return False, "", reason
    return True, template_json(template), None


def validate_template_text(text, registry=None, is_read=None):
    """Validate template file text. Returns (ok, template, reason).
    Size-capped, json.loads only, no pickle/eval/exec."""
    if not isinstance(text, str):
        return False, None, "a template must be text"
    if len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        return False, None, ("template exceeds the size cap (%d "
                             "bytes)" % MAX_TEMPLATE_BYTES)
    try:
        template = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — untrusted input
        return False, None, "not valid JSON: %s" % exc
    shape_err = _check_template_shape(template)
    if shape_err is not None:
        return False, None, shape_err
    if registry is None:
        registry = _live_registry()
    if is_read is None:
        is_read = _is_read_class
    ok, reason, _ro = _scan_steps(template["steps"], registry,
                                  is_read)
    if not ok:
        return False, None, reason
    return True, template, None


def _fill_placeholders(value, fill, missing):
    """Recursively replace {"$template": name} with fill[name];
    unfilled names land in `missing`."""
    if isinstance(value, dict):
        if set(value) == {PLACEHOLDER_KEY}:
            name = value[PLACEHOLDER_KEY]
            if name in fill:
                return fill[name]
            missing.append(name)
            return value
        return {k: _fill_placeholders(v, fill, missing)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_placeholders(v, fill, missing) for v in value]
    return value


def extract_placeholders(template):
    """The placeholder names a template needs, in first-seen order."""
    names = []

    def walk(value):
        if isinstance(value, dict):
            if set(value) == {PLACEHOLDER_KEY}:
                name = value[PLACEHOLDER_KEY]
                if isinstance(name, str) and name not in names:
                    names.append(name)
                return
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    if isinstance(template, dict):
        for step in template.get("steps") or []:
            if isinstance(step, dict):
                walk(step.get("args") or {})
    return names


def import_template(text, fill=None, registry=None, is_read=None):
    """Validate template text and fill its placeholders. Returns
    (ok, steps, reason) — the filled steps, ready to be saved as a
    routine through the confirm-gated save_routine action. A
    placeholder with no value in `fill` refuses, naming it;
    filled values are validated with the same rules as exported
    values."""
    ok, template, reason = validate_template_text(
        text, registry=registry, is_read=is_read)
    if not ok:
        return False, [], reason
    fill = dict(fill or {})
    missing = []
    steps = []
    for step in template["steps"]:
        args = _fill_placeholders(step.get("args") or {}, fill, missing)
        steps.append({"tool": step["tool"], "args": args})
    if missing:
        return False, [], ("template needs a value for %s"
                           % ", ".join(sorted(set(missing))))
    problems = []
    for index, step in enumerate(steps):
        for k, v in step["args"].items():
            _scan_value(str(k), v, problems, [])
        if problems:
            return False, [], ("step %d (%s): %s"
                               % (index + 1, step["tool"], problems[0]))
    return True, steps, None


def load_bundled_templates():
    """The bundled templates (routine_templates.json at the tree
    root — the v1 gallery source; no public page, D-RET-2 owed).
    Returns (valid, skipped): valid template dicts, and
    (name, reason) pairs for entries that fail validation."""
    path = Path(__file__).resolve().parent.parent / "routine_templates.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [("(unreadable)", str(exc))]
    try:
        bundle = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return [], [("(unparsable)", str(exc))]
    templates = bundle.get("templates") if isinstance(bundle, dict) else None
    if bundle.get("format_version") != TEMPLATE_FORMAT_VERSION \
            or not isinstance(templates, list):
        return [], [("(bad bundle)", "format_version must be 1 with a "
                                     "templates list")]
    valid, skipped = [], []
    for entry in templates:
        ok, template, reason = validate_template_text(
            json.dumps(entry))
        if ok:
            valid.append(template)
        else:
            skipped.append((str(entry.get("name")
                                if isinstance(entry, dict) else entry),
                            reason or "invalid"))
    return valid, skipped
