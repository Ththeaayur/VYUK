"""Wave 10 Build 1 tools: P23 release spine — multiple
install routes, verifiable releases (structure only),
and a release train with gates, as live tools.

Triage Wire pattern, as in core/grad_tools.py:
this coordinator imports the release module,
registers tools via register_all(registry), each
handler takes the named schema fields, forwards to
the module, and returns the module result encoded as
a JSON string verbatim; refusal dicts flow the same
way, and module raises become an error JSON —
handlers never raise.

TOOLBOX INDIRECTION (why two tools, not four)
---------------------------------------------
The fitted Gemini declaration budget is a HARD cap
(240,000 B) and stood at 239,624 B on stage after
Wave 9 — headroom only about 376 B, with
descriptions already capped to 55 chars by the
fitter. Separate tools with their own parameter
schemas do not fit that headroom: their schemas
alone would push the fitted total over the cap, and
the fitted full list would then have been further over
it (the domain-router build later routed around the
fitted list entirely; DECL-01 retired it 2026-11-01).
The overflow protocol
(~/workspace/code-lab/overflow_queue.md) names
toolbox indirection as an alternative to dropping
items, so this build uses it: ONE read-only
descriptor tool with a "kind" parameter (route,
verify, train) carries all three descriptors, and
ONE confirm-gated record tool carries the release
record. Nothing is dropped and nothing is queued:
every descriptor the brief names is built, wired,
grouped, and tested — they share one tool each by
kind, not one tool each by name. Parameter schemas
here carry no per-property descriptions, for the
same fitted-cap reason: at the 55-char cap the
fitter would truncate them anyway, and each one
costs fitted bytes the budget no longer has. Tool
descriptions lead with their safety clause, so the
55-char fitted copy still carries it.

Wiring (Wave 10 Build 1, P23 release spine):
  * release_describe — read-only. kind "route"
    describes caller-supplied install routes, each
    stating what it does and does not do — nothing
    compiled, nothing installed, no store listing
    claimed to exist. kind "verify" checks the SHAPE
    of a caller-supplied manifest summary plus
    SHA-256 — version supplied, manifest present,
    hash present and well-formed, data baseline
    stated — and never verifies a signature, never
    claims a release is signed or authentic. kind
    "train" returns caller-supplied stage gates in
    order, verdicts as supplied, running no gate and
    passing no gate. Nothing is released, signed,
    compiled, published, fed, or decided by any kind.
  * release_record — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the
    release descriptor itself (it never trusts a
    caller-supplied release JSON, which could claim
    anything) and writes only the release record to
    a caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm
    handler, before the handler runs. Recording
    releases nothing, signs nothing, compiles
    nothing, publishes nothing, and decides nothing
    — ruling 34 stays open, signing stays deferred,
    and the feed still has no host.

This coordinator is NOT a signing tool, a compiler,
a publisher, a feed tool, or a payment tool: it
imports no signing, installer, network, or feed
code, releases nothing, and never claims the Wave
10 exit as "commercially complete" — the exit is
"shippable to strangers except the transaction
surface, feed hosting, and signing", in exactly
those words.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import release_spine as release_mod


def _error(reason: str) -> str:
    return json.dumps({"ok": False, "reason": reason})


def _handler(fn, *args, **kwargs):
    try:
        return json.dumps(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")


def _parse_json(text, field):
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"{field} is not valid JSON: {exc}"
    return value, None


# Round-2 N1 BUG 2: what each kind needs in record_json. A call with
# no record_json used to reach describe() with no fields, and the
# route/train builders raised TypeError for their missing required
# argument — leaked into the payload as "TypeError: describe_route()
# missing 1 required positional argument: 'routes'". The missing
# argument is validated here, up front, in plain words.
_REQUIRED_FOR_KIND = {
    "route": "a JSON object with a non-empty 'routes' list",
    "verify": "a JSON object with a non-empty 'version' string",
    "train": "a JSON object with a non-empty 'gates' list",
}


def release_describe_handler(kind, action="describe",
                             record_json=None):
    """Dispatch one descriptor kind. The record_json
    object must carry only the kind's own fields —
    the module refuses any field belonging to another
    kind whole, before describing anything — and it
    must not carry its own action: only 'describe'
    is performed."""
    if kind not in ("route", "verify", "train"):
        return _error(
            "kind must be one of route, verify, train")
    if record_json is None:
        return _error(
            "record_json is required: kind "
            f"'{kind}' needs {_REQUIRED_FOR_KIND[kind]}")
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    if "action" in fields:
        return _error(
            "record_json must not carry its own action; "
            "only 'describe' is performed")
    return _handler(release_mod.describe, kind,
                    action=action, **fields)


def _check_output_path(output_path):
    try:
        target = Path(output_path).expanduser()
        if target.suffix != ".json":
            return None, "output_path must end in .json"
        if not target.parent.is_dir():
            return None, "output_path parent folder must already exist"
        if target.name in (".env", "assistant.db") \
                or target.name.endswith((".pem", ".key")):
            return None, "credential target refused"
        tree = Path(__file__).resolve().parent.parent
        try:
            resolved = target.resolve()
            if resolved.is_relative_to(tree / "core") \
                    or resolved.is_relative_to(tree / "data"):
                return None, "app core/data paths refused"
        except (OSError, ValueError):
            return None, "output_path cannot be resolved"
    except Exception as exc:  # noqa: BLE001
        return None, f"output_path cannot be resolved: {exc}"
    return target, None


def release_record_handler(record_kind, record_json,
                           output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    if "action" in fields:
        return _error(
            "record_json must not carry its own action; "
            "only 'describe' is performed")
    try:
        rebuilt = release_mod.validate_recordable_release(
            record_kind, fields)
    except (ValueError, TypeError) as exc:
        return _error(
            f"release descriptor does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "RELEASE_RECORD",
        "record_kind": record_kind,
        "release": rebuilt,
        "released": False, "signed": False,
        "compiled": False, "published": False,
        "exit": release_mod.WAVE10_EXIT,
        "caveat": release_mod.CAVEAT,
        "note": ("A release-spine descriptor the user "
                 "chose, recorded only. Recording "
                 "releases nothing, signs nothing, "
                 "compiles nothing, publishes nothing, "
                 "and decides nothing — ruling 34 "
                 "stays open, signing stays deferred, "
                 "and the feed still has no host."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "RELEASE_RECORD",
                       "released": False, "signed": False,
                       "compiled": False, "published": False,
                       "report_path": str(target),
                       "note": "Release descriptor recorded; nothing "
                               "was released, signed, compiled, "
                               "or published."})


def register_all(registry) -> None:
    """Register the 2 release-spine tools (toolbox
    indirection: release_describe carries the route,
    verify, and train descriptors by kind;
    release_record carries the release record)."""
    registry.register(
        name="release_describe",
        description="DESCRIBE ONLY, NOTHING RELEASED OR SIGNED: describe install routes, a release structure check, or a release train with gates, from caller-supplied fields only. kind 'route' describes install routes (nothing compiled, nothing installed, no store listing claimed); kind 'verify' checks the SHAPE of a manifest summary plus SHA-256 (never a signature, never an authenticity verdict); kind 'train' returns stage gates in order, verdicts as supplied, running and passing no gate. Exit is shippable to strangers except the transaction surface, feed hosting, and signing — never commercially complete.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "Which descriptor kind to describe","type": "string",
                     "enum": ["route", "verify", "train"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "action": {"description": "Only 'describe' is performed", "type": "string"},
        }, "required": ["kind"]},
        handler=release_describe_handler,
    )
    registry.register(
        name="release_record",
        description="RECORD ONLY, NOTHING RELEASED OR SIGNED: re-validate a caller-supplied release descriptor (route, verify, or train) by re-running the builder that made it, and write only the release record to a caller-chosen .json file in an already-existing folder. A descriptor that does not re-build is refused, never written on trust. The record asks for confirmation first, and it is not a release, a signature, or a publication; ruling 34 stays open.",
        parameters={"type": "object", "properties": {
            "record_kind": {"description": "Which descriptor kind the record is","type": "string",
                            "enum": ["route", "verify",
                                     "train"]},
            "record_json": {"description": "JSON object of the descriptor fields to re-validate", "type": "string"},
            "output_path": {"description": "Caller-chosen .json file in an already-existing folder", "type": "string"},
        }, "required": ["record_kind", "record_json",
                        "output_path"]},
        handler=release_record_handler,
    )
