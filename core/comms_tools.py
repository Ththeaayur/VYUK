"""Communications deltas as live tools — draft and confirm
only, nothing ever sent.

Triage Wire pattern, as in core/well_tools.py:
this coordinator imports the communications module,
registers tools via register_all(registry), each handler
takes the named schema fields, forwards to the module,
and returns the module result encoded as a JSON string
verbatim; refusal dicts flow the same way, and module
raises become an error JSON — handlers never raise.

Wiring (Wave 9 Build 2, COMMS — communications deltas,
Tier 2 plus confirmation for messages):
  * comms_draft_message — read-only. It returns a
    message draft from caller-supplied recipient, body,
    and channel label. The draft is never addressed from
    an inferred contact and never sent.
  * comms_check_recipient — read-only. It checks a
    caller-supplied contact record only — name and
    channel as supplied — and returns "supplied" or
    "missing". It never guesses, never merges, and never
    infers a relationship.
  * comms_send_readiness — read-only. It states honestly
    that the draft is NOT sent and names the two steps
    still missing: a human confirmation and a real send
    pipeline, neither of which this build provides.
  * comms_record_draft — confirm-gated, output_path in
    _WRITE_DESTINATION_ARGS. It re-validates the draft
    itself (it never trusts a caller-supplied draft JSON,
    which could claim anything, including that it was
    sent) and writes only the draft record to a
    caller-chosen .json file in an already-existing
    folder — no folder creation, no overwrite of a
    core/data path, no .env/.pem/.key target. The funnel
    refuses it without a human confirm handler, before
    the handler runs. Recording a draft sends nothing.

This coordinator is NOT a send pipeline: it imports no
network, messaging, or mail code, opens no connection,
and authenticates to nothing. No message is sent, no
bulk message is built, no contact is merged or
inferred, and no send pipeline is invented here; any
send-shaped input refuses whole in the module.

Own code, stdlib only. No third-party dependency is
introduced by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import comms as comms_mod


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


def comms_draft_message_handler(recipient, body, channel, action=None):
    return _handler(comms_mod.build_message_draft, recipient,
                    body, channel, action)


def comms_check_recipient_handler(contact_json):
    record, err = _parse_json(contact_json, "contact_json")
    if err:
        return _error(err)
    return _handler(comms_mod.check_recipient, record)


def comms_send_readiness_handler(draft_json):
    draft, err = _parse_json(draft_json, "draft_json")
    if err:
        return _error(err)
    return _handler(comms_mod.describe_send_readiness, draft)


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


def comms_record_draft_handler(draft_json, output_path):
    fields, err = _parse_json(draft_json, "draft_json")
    if err:
        return _error(err)
    if not isinstance(fields, dict):
        return _error("draft_json must be a JSON object")
    try:
        rebuilt = comms_mod.validate_recordable_draft(fields)
    except (ValueError, TypeError) as exc:
        return _error(f"draft does not re-build: {exc}")
    target, err = _check_output_path(output_path)
    if err:
        return _error(err)
    record = {
        "kind": "MESSAGE_DRAFT_RECORD",
        "draft": rebuilt,
        "sent": False,
        "note": ("A message draft, recorded only. Recording "
                 "sends nothing: the draft still needs a "
                 "human confirmation and a real send "
                 "pipeline, neither of which this tool "
                 "provides."),
    }
    try:
        target.write_text(json.dumps(record, indent=2))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}")
    return json.dumps({"ok": True, "kind": "MESSAGE_DRAFT_RECORD",
                       "executed": False, "sends_nothing": True,
                       "report_path": str(target),
                       "sent": False,
                       "note": "Draft recorded; nothing was sent."})


def register_all(registry) -> None:
    """Register the 4 communications tools."""
    registry.register(
        name="comms_draft_message",
        description="DRAFT ONLY, NEVER SENT: return a message draft from caller-supplied recipient, body, and channel label — the recipient as given, the body as given, the channel as a label only. The draft is never addressed from an inferred contact, never sent, and never presented as sent. Any send action, list recipient, or sent claim refuses the whole call.",
        parameters={"type": "object", "properties": {
            "recipient": {"type": "string",
                          "description": "Caller-supplied recipient, one, as given"},
            "body": {"type": "string",
                     "description": "Caller-supplied message body, as given"},
            "channel": {"type": "string",
                        "description": "Caller-supplied channel label, never a resolved route"},
            "action": {"type": "string",
                       "description": "Optional; only 'draft' is accepted, any send action refuses"},
        }, "required": ["recipient", "body", "channel"]},
        handler=comms_draft_message_handler,
    )
    registry.register(
        name="comms_check_recipient",
        description="CHECK ONLY, NOTHING GUESSED: check a caller-supplied contact record — name and channel as supplied — and return 'supplied' or 'missing', naming what is missing. It never guesses a recipient, never merges records, and never infers a relationship. A list input or an inferred/partial record refuses whole.",
        parameters={"type": "object", "properties": {
            "contact_json": {"type": "string",
                             "description": "JSON object with caller-supplied name and channel"},
        }, "required": ["contact_json"]},
        handler=comms_check_recipient_handler,
    )
    registry.register(
        name="comms_send_readiness",
        description="HONEST READINESS ONLY, NOTHING SENT: validate a message draft's own shape and state plainly that it is NOT sent and NOT ready to send on its own — the two missing steps are a human confirmation of this exact draft and a real send pipeline, neither of which this build provides. A draft claiming sent refuses whole.",
        parameters={"type": "object", "properties": {
            "draft_json": {"type": "string",
                           "description": "JSON object of the message draft to assess"},
        }, "required": ["draft_json"]},
        handler=comms_send_readiness_handler,
    )
    registry.register(
        name="comms_record_draft",
        description="RECORD ONLY, NOTHING SENT: re-validate a caller-supplied message draft by re-running the builder that made it, and write only the draft record to a caller-chosen .json file in an already-existing folder. A draft that does not re-build — including one claiming it was sent — is refused, never written on trust. Recording sends nothing. The record asks for confirmation first.",
        parameters={"type": "object", "properties": {
            "draft_json": {"type": "string",
                           "description": "JSON object of the draft fields to re-validate"},
            "output_path": {"type": "string",
                            "description": "Caller-chosen .json file in an already-existing folder"},
        }, "required": ["draft_json", "output_path"]},
        handler=comms_record_draft_handler,
    )
