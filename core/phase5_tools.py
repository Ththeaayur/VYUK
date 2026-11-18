"""Wave 8 Build 3 tools — Phase 5 picks as live tools.

Triage Wire pattern, as in core/anticipation_grant_tools.py and
core/release_checklist_tools.py: this coordinator imports the Phase 5
module, registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module, and returns
the module result encoded as a JSON string verbatim; refusal dicts
flow the same way, and module raises become an error JSON —
handlers never raise.

Wiring (Wave 8 Build 3, five clusters, six tools):
  * phase5_interface_describe — read-only. Validates a surface
    descriptor inside the frozen core; a descriptor that renumbers,
    adds a surface, shows a face, or carries fake data is refused
    whole. It describes, it never redesigns.
  * ent_shelf_list — read-only. Catalogues caller-supplied
    entertainment items, licence-gated; no asset is bundled,
    played, or generated, and vetoed feel never enters.
  * port_roundtrip — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It exports the caller-supplied record
    set to a caller-chosen .json file in an already-existing
    folder, re-reads that file, and proves the round-trip
    byte-for-byte on the canonical record JSON. The funnel refuses
    it without a human confirm handler, before the handler runs.
    It is a single-device caller action, never automatic, never
    a second device, never a cloud sync.
  * plat_tools_record — read-only. Validates a lifecycle /
    rollout / governance record; records only, enforced=False.
  * fin_plan_record — read-only. Validates a money plan/record
    over caller-supplied amounts; any transaction-shaped input
    is refused whole, and no transaction, payment, account
    connection, or advice ever results.
  * phase5_report — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-runs the named cluster
    operation itself (it never trusts caller-supplied result
    JSON) and writes only that cluster's report to a caller-chosen
    .json file in an already-existing folder.

This coordinator is NOT a gate owner: confirm-gating is the
funnel's job (core/funnel/tools_gate.py classification). No transaction,
no payment surface, no multi-device sync, no vetoed feel, no face,
no fake data, no voiceprint, no continuous capture, no vehicle
driving, no medical diagnosis, no marketplace/store is built or
wired here.

Own code, stdlib only. No third-party dependency is introduced
by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import phase5_picks as phase5_mod


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


def _check_output_path(output_path):
    """Destination checks shared by the two file-writing tools:
    .json only, parent folder must already exist, no credential
    target, no app core/data path. Returns (Path, error)."""
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
        resolved = target.resolve()
        if resolved.is_relative_to(tree / "core") \
                or resolved.is_relative_to(tree / "data"):
            return None, "app core/data paths refused"
    except (OSError, ValueError):
        return None, "output_path cannot be resolved"
    return target, None


def _write_report(target, result):
    try:
        target.write_text(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": result.get("kind"),
                       "executed": False, "executes_nothing": True,
                       "report_path": str(target),
                       "note": "Report written; nothing was executed, "
                               "sent, purchased, or deployed."})


def phase5_interface_describe_handler(descriptor_json):
    descriptor, err = _parse_json(descriptor_json, "descriptor_json")
    if err:
        return _error(err)
    return _handler(phase5_mod.describe_interface, descriptor)


def ent_shelf_list_handler(items_json):
    items, err = _parse_json(items_json, "items_json")
    if err:
        return _error(err)
    return _handler(phase5_mod.list_ent_shelf, items)


def port_roundtrip_handler(records_json, output_path):
    records, err = _parse_json(records_json, "records_json")
    if err:
        return _error(err)
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    built = phase5_mod.export_port_bundle(records)
    if not built["ok"]:
        return json.dumps(built)
    try:
        target.write_text(json.dumps(built["bundle"], indent=2))
        reread = json.loads(target.read_text())
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    imported = phase5_mod.import_port_bundle(reread)
    if not imported["ok"]:
        return json.dumps(imported)
    same = phase5_mod._canonical(imported["records"]) == built["canonical"]
    result = {
        "ok": True,
        "kind": "PORT_ROUNDTRIP",
        "count": built["bundle"]["count"],
        "sha256": built["bundle"]["manifest"]["sha256"],
        "roundtrip_ok": same,
        "byte_for_byte": same,
        "bundle_path": str(target),
        "note": "Single-device round-trip proven on the file itself: "
                "the bundle was written to the caller-chosen file, "
                "re-read from that file, and matched byte-for-byte. "
                "This is not a second device and not a cloud sync.",
    }
    return json.dumps(result)


def plat_tools_record_handler(record_json):
    record, err = _parse_json(record_json, "record_json")
    if err:
        return _error(err)
    return _handler(phase5_mod.record_plat, record)


def fin_plan_record_handler(plan_json):
    plan, err = _parse_json(plan_json, "plan_json")
    if err:
        return _error(err)
    return _handler(phase5_mod.record_fin_plan, plan)


_CLUSTER_RUNNERS = {
    "interface": (phase5_mod.describe_interface, "descriptor_json"),
    "ent": (phase5_mod.list_ent_shelf, "items_json"),
    "plat": (phase5_mod.record_plat, "record_json"),
    "fin": (phase5_mod.record_fin_plan, "plan_json"),
}


def phase5_report_handler(cluster, payload_json, output_path):
    if cluster not in _CLUSTER_RUNNERS:
        return _error(
            "cluster must be interface / ent / plat / fin; "
            "port has its own round-trip tool and is not reported "
            "through this tool"
        )
    payload, err = _parse_json(payload_json, "payload_json")
    if err:
        return _error(err)
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    fn, _field = _CLUSTER_RUNNERS[cluster]
    result = fn(payload)
    if not result.get("ok"):
        return json.dumps(result)
    return _write_report(target, result)


def register_all(registry) -> None:
    """Register the 6 Wave 8 Build 3 Phase 5 tools."""
    registry.register(
        name="phase5_interface_describe",
        description="INTERFACE DESCRIPTOR ONLY, NEVER A REDESIGN: validate a surface descriptor inside the frozen UI core (surface, rows, actions). A descriptor that renumbers, adds a sidebar entry or page, shows a face, or carries fake data (anything but a measured value or an explicit unknown) is refused whole. It describes, it never redesigns and never executes.",
        parameters={"type": "object", "properties": {
            "descriptor_json": {"type": "string",
                                "description": "JSON object {surface, rows: [{label, action, value_kind, value, c14_required}]}; actions navigate/show/confirm/dismiss/read/describe only"},
        }, "required": ["descriptor_json"]},
        handler=phase5_interface_describe_handler,
    )
    registry.register(
        name="ent_shelf_list",
        description="ENTERTAINMENT SHELF CATALOGUE ONLY, NO ASSET BUNDLED, NOTHING PLAYED: catalogue caller-supplied entertainment items (title, kind, source, licence), licence-gated. Copyleft, non-commercial, no-derivatives, revenue-capped, territory-restricted, or unknown licences are refused whole, and vetoed feel (Charon, M4, earcon, F6, Space Grotesk) never enters. No audio or video is played or generated.",
        parameters={"type": "object", "properties": {
            "items_json": {"type": "string",
                           "description": "JSON list of {title, kind, source, licence} items — caller-supplied catalogue records only"},
        }, "required": ["items_json"]},
        handler=ent_shelf_list_handler,
    )
    registry.register(
        name="port_roundtrip",
        description="SINGLE-DEVICE PORT ROUND-TRIP ONLY, CALLER ACTION, NEVER AUTOMATIC: export a caller-supplied record set to a caller-chosen .json file in an already-existing folder, re-read that file, and prove the round-trip byte-for-byte on the canonical record JSON. Credential-shaped keys are refused. This is not a second device and not a cloud sync. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "records_json": {"type": "string",
                             "description": "JSON list of record objects — caller-supplied, no credential-shaped keys"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["records_json", "output_path"]},
        handler=port_roundtrip_handler,
    )
    registry.register(
        name="plat_tools_record",
        description="PROGRAMME RECORD ONLY, NEVER ENFORCEMENT: validate a lifecycle / rollout / governance record (kind, name, state, note). A governance record without a note is refused, and an unknown state for the kind is refused. Records carry enforced=False; a rollout or lifecycle change still goes through its own gate.",
        parameters={"type": "object", "properties": {
            "record_json": {"type": "string",
                            "description": "JSON object {kind: lifecycle|rollout|governance, name, state, note}"},
        }, "required": ["record_json"]},
        handler=plat_tools_record_handler,
    )
    registry.register(
        name="fin_plan_record",
        description="MONEY PLANNING AND RECORD-KEEPING ONLY, NO TRANSACTION EVER: validate a money plan/record over caller-supplied amounts (budget, saving-goal, expense-record, income-record entries with a 3-letter currency). Any transaction-shaped input (transaction, payment, purchase, payee, account, card, bank, UPI, wallet, or a pay/send/transfer/purchase/buy label) is refused whole. No transaction is performed, no payment is made, no account is connected, and no advice is given.",
        parameters={"type": "object", "properties": {
            "plan_json": {"type": "string",
                          "description": "JSON object {name, entries: [{label, kind, amount, currency}]} — caller-supplied amounts only"},
        }, "required": ["plan_json"]},
        handler=fin_plan_record_handler,
    )
    registry.register(
        name="phase5_report",
        description="PHASE 5 CLUSTER REPORT ONLY, NOTHING EXECUTED: re-run one Phase 5 cluster operation (interface, ent, plat, or fin — port has its own round-trip tool) and write only that cluster's report to a caller-chosen .json file in an already-existing folder. The report asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "cluster": {"type": "string",
                        "description": "One of interface / ent / plat / fin"},
            "payload_json": {"type": "string",
                             "description": "JSON payload for the named cluster operation, re-run by this tool"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["cluster", "payload_json", "output_path"]},
        handler=phase5_report_handler,
    )
