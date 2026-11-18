"""Network deltas as live tools.

Triage Wire pattern, as in core/iotl_tools.py:
this coordinator imports the network module, registers
tools via register_all(registry), each handler takes the
named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim;
refusal dicts flow the same way, and module raises become
an error JSON — handlers never raise.

Wiring (Wave 9 Build 6, NET — network deltas, read-only
and own-network forms):
  * net_network_descriptor — read-only. It returns an
    own-network descriptor from a caller-supplied
    network label and a scope of exactly "own" or
    "administered". The label is a name the caller gave
    it, never probed, never scanned, never measured,
    never resolved.
  * net_network_status — read-only. It returns a
    network-status descriptor from caller-supplied status
    words and a timestamp. The status is as supplied,
    never polled live, never measured.
  * net_readonly_check — read-only. It returns
    "own-network as supplied", "missing", or "outside
    own network — refused" for a caller-supplied target
    label and scope. It never resolves the target, never
    connects to it, and never reaches outside the
    supplied network.
  * net_record_network — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the network
    itself (it never trusts a caller-supplied network
    JSON, which could claim anything) and writes only
    the network record to a caller-chosen .json file in
    an already-existing folder — no folder creation, no
    overwrite of a core/data path, no .env/.pem/.key
    target. The funnel refuses it without a human confirm
    handler, before the handler runs. Recording a network
    scans nothing, connects to nothing, and reaches
    nothing — the record is a descriptor the user chose.

This coordinator is NOT a network tool: it imports no
socket or network-connect code, opens no connection,
resolves no address, listens on no port, and scans
nothing. No scan, probe, ping, connect, discovery, or
external-network action is built or wired here; any
such input refuses whole in the module (or, in the
read-only check, reports "outside own network —
refused").

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import net as net_mod


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


def net_network_descriptor_handler(network_label, scope,
                                   action="describe"):
    return _handler(net_mod.build_network_descriptor,
                    network_label, scope, action)


def net_network_status_handler(status_words, timestamp,
                               action="describe"):
    return _handler(net_mod.build_network_status,
                    status_words, timestamp, action)


def net_readonly_check_handler(target_label, scope,
                               network_label=None,
                               action="describe"):
    return _handler(net_mod.check_readonly_target,
                    target_label, scope, network_label, action)


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


def net_record_network_handler(record_kind, record_json, output_path):
    fields, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("record_json must be a JSON object")
    try:
        rebuilt = net_mod.validate_recordable_network(record_kind, fields)
    except (ValueError, TypeError) as exc:
        return _error(f"network does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "NETWORK_RECORD",
        "record_kind": record_kind.strip(),
        "network": rebuilt,
        "scanned": False,
        "connected": False,
        "reached": False,
        "note": ("A network descriptor the user chose, "
                 "recorded only. Recording scans nothing, "
                 "connects to nothing, and reaches nothing."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "NETWORK_RECORD",
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "scanned": False, "connected": False,
                       "note": "Network recorded; nothing was "
                               "scanned, probed, or connected."})


def register_all(registry) -> None:
    """Register the 4 network tools."""
    registry.register(
        name="net_network_descriptor",
        description="DESCRIPTOR ONLY, NEVER PROBED OR SCANNED: return an own-network descriptor from a caller-supplied network label and a scope of exactly 'own' or 'administered' — a name the caller gave the network, never probed, never scanned, never measured, never resolved. Any scan, probe, ping, connect, or discovery action, any external scope or label, and any URL-scheme address refuse the whole call.",
        parameters={"type": "object", "properties": {
            "network_label": {"type": "string",
                              "description": "Caller-supplied network label"},
            "scope": {"type": "string",
                      "description": "Exactly 'own' or 'administered'"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["network_label", "scope"]},
        handler=net_network_descriptor_handler,
    )
    registry.register(
        name="net_network_status",
        description="STATUS ONLY, NEVER POLLED LIVE: return a network-status descriptor from caller-supplied status words and a timestamp — the status as supplied, never polled live, never measured. A status word claiming a live reach (scanned, probed, reached, measured, connected, discovered) refuses the whole call.",
        parameters={"type": "object", "properties": {
            "status_words": {"type": "string",
                             "description": "Caller-supplied status words"},
            "timestamp": {"type": "string",
                          "description": "Caller-supplied timestamp"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["status_words", "timestamp"]},
        handler=net_network_status_handler,
    )
    registry.register(
        name="net_readonly_check",
        description="READ-ONLY CHECK ONLY, NEVER RESOLVED OR CONNECTED: return 'own-network as supplied', 'missing', or 'outside own network — refused' for a caller-supplied target label and scope. It never resolves the target, never connects to it, and never reaches outside the supplied network. A scan or connect action refuses the whole call.",
        parameters={"type": "object", "properties": {
            "target_label": {"type": "string",
                             "description": "Caller-supplied target label"},
            "scope": {"type": "string",
                      "description": "Exactly 'own' or 'administered'"},
            "network_label": {"type": "string",
                              "description": "Optional caller-supplied network label"},
            "action": {"type": "string",
                       "description": "Only 'describe' is performed"},
        }, "required": ["target_label", "scope"]},
        handler=net_readonly_check_handler,
    )
    registry.register(
        name="net_record_network",
        description="RECORD ONLY, NOTHING SCANNED, NOTHING CONNECTED: re-validate a caller-supplied network (descriptor or status) by re-running the builder that made it, and write only the network record to a caller-chosen .json file in an already-existing folder. A network that does not re-build is refused, never written on trust. Recording scans nothing, connects to nothing, and reaches nothing. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "record_kind": {"type": "string",
                            "description": "One of descriptor, status"},
            "record_json": {"type": "string",
                            "description": "JSON object of the network fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["record_kind", "record_json", "output_path"]},
        handler=net_record_network_handler,
    )
