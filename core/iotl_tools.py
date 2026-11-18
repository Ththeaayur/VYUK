"""Local IoT deltas as live tools — descriptors and
records only, nothing ever reached or controlled.

Triage Wire pattern, as in core/comms_tools.py:
this coordinator imports the local-IoT module,
registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

Wiring (Wave 9 Build 3, IOT-L — local IoT deltas,
local and administered networks only):
  * iot_local_device — read-only. It returns a
    local-device descriptor from a caller-supplied
    device name, local address label, and
    administered-network label. The address is a label
    only, never probed or resolved, and the device is
    never reached or controlled.
  * iot_device_state — read-only. It returns a device
    state summary from caller-supplied state words and
    timestamp. The state is as supplied, never polled,
    never measured.
  * iot_network_check — read-only. It returns
    "local-administered as supplied" or "missing" for a
    caller-supplied network label. It never scans
    anything and never reaches outside the supplied
    network.
  * iot_record_device — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the device
    itself (it never trusts a caller-supplied device
    JSON, which could claim anything, including that
    the device was reached or controlled) and writes
    only the device record to a caller-chosen .json file
    in an already-existing folder — no folder creation,
    no core/data path, no .env/.pem/.key target. The
    funnel refuses it without a human confirm handler,
    before the handler runs. Recording a device reaches
    nothing and controls nothing.

This coordinator is NOT an actuation or discovery
system: it imports no socket or network code, opens no
connection, resolves no address, scans nothing, and
authenticates to nothing. No device is reached, polled,
or controlled, and no hardware is moved here; any
actuation, scan, external-network, or reach-claim input
refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import iotl as iotl_mod


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


def iot_local_device_handler(device_name, address_label, network_label,
                             action=None):
    return _handler(iotl_mod.build_device_descriptor, device_name,
                    address_label, network_label, action)


def iot_device_state_handler(device_name, state, timestamp, action=None):
    return _handler(iotl_mod.build_device_state, device_name, state,
                    timestamp, action)


def iot_network_check_handler(network_label=""):
    return _handler(iotl_mod.check_local_network, network_label)


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


def iot_record_device_handler(device_json, output_path):
    fields, err = _parse_json(device_json, "device_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("device_json must be a JSON object")
    try:
        rebuilt = iotl_mod.validate_recordable_device(fields)
    except (ValueError, TypeError) as exc:
        return _error(f"device does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "LOCAL_DEVICE_RECORD",
        "device": rebuilt,
        "reached": False,
        "controlled": False,
        "note": ("A local device descriptor, recorded only. "
                 "Recording reaches nothing and controls "
                 "nothing: the device was not reached, "
                 "not polled, and not controlled."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "LOCAL_DEVICE_RECORD",
                       "executed": False, "reaches_nothing": True,
                       "controls_nothing": True,
                       "report_path": str(target),
                       "reached": False, "controlled": False,
                       "note": "Device recorded; nothing was reached or controlled."})


def register_all(registry) -> None:
    """Register the 4 local IoT tools."""
    registry.register(
        name="iot_local_device",
        description="DESCRIBE ONLY, NOTHING REACHED OR CONTROLLED: return a local-device descriptor from a caller-supplied device name, local address label, and administered-network label — the address as a label only, never probed or resolved. The device is never reached and never controlled. Any actuation action, scan action, external network label, URL-scheme address, or reach claim refuses the whole call.",
        parameters={"type": "object", "properties": {
            "device_name": {"type": "string",
                            "description": "Caller-supplied device name, as given"},
            "address_label": {"type": "string",
                              "description": "Caller-supplied local address label, never probed or resolved"},
            "network_label": {"type": "string",
                              "description": "Caller-supplied local/administered network label"},
            "action": {"type": "string",
                       "description": "Optional; only 'describe' is accepted, any actuation or scan action refuses"},
        }, "required": ["device_name", "address_label", "network_label"]},
        handler=iot_local_device_handler,
    )
    registry.register(
        name="iot_device_state",
        description="STATE AS SUPPLIED ONLY, NOTHING POLLED OR MEASURED: return a device state summary from caller-supplied state words and timestamp. The state is as supplied, never polled, never measured, and the device is never reached. Any actuation or scan action, missing field, or reached/polled/controlled claim refuses whole.",
        parameters={"type": "object", "properties": {
            "device_name": {"type": "string",
                            "description": "Caller-supplied device name, as given"},
            "state": {"type": "string",
                      "description": "Caller-supplied state words, as given"},
            "timestamp": {"type": "string",
                          "description": "Caller-supplied timestamp, as given"},
            "action": {"type": "string",
                       "description": "Optional; only 'describe' is accepted"},
        }, "required": ["device_name", "state", "timestamp"]},
        handler=iot_device_state_handler,
    )
    registry.register(
        name="iot_network_check",
        description="LABEL CHECK ONLY, NOTHING SCANNED: check a caller-supplied network label and return 'local-administered as supplied' or 'missing'. It never scans anything and never reaches outside the supplied network. An external network label refuses whole.",
        parameters={"type": "object", "properties": {
            "network_label": {"type": "string",
                              "description": "Caller-supplied network label, or empty for missing"},
        }, "required": []},
        handler=iot_network_check_handler,
    )
    registry.register(
        name="iot_record_device",
        description="RECORD ONLY, NOTHING REACHED OR CONTROLLED: re-validate a caller-supplied local device by re-running the builder that made it, and write only the device record to a caller-chosen .json file in an already-existing folder. A device that does not re-build — including one claiming it was reached or controlled — is refused, never written on trust. Recording reaches nothing and controls nothing. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "device_json": {"type": "string",
                            "description": "JSON object of the device fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["device_json", "output_path"]},
        handler=iot_record_device_handler,
    )
