"""Communications deltas, Wave 9 Build 2 (COMMS) — draft and
confirm only. Nothing here is ever sent.

Per build_flow_v6.md: "COMMS (communications deltas) —
BUILD, Wave 9, Tier 2 plus confirmation for messages."
Tier 2 means confirm-gated for anything message-shaped,
and C-14 approve-first applies. The standing fact this
build respects, not works around, is that no social send
pipeline exists in the tree — so this build invents none.

WHAT THIS MODULE IS
-------------------
* A message-draft composer: caller-supplied recipient,
  body, and channel label in, a plain-language draft out.
  The recipient is never addressed from an inferred
  contact, and the draft is never sent.
* A recipient check: a caller-supplied contact record
  only — name and channel as supplied — returning
  "supplied" or "missing". It never guesses, never
  merges, and never infers a relationship.
* A send-readiness descriptor: it states, honestly, that
  the draft is NOT sent, and names the two steps still
  missing — a human confirmation and a real send
  pipeline — neither of which this build provides.
* A re-validation rule for recording a draft: the draft
  is re-built from its own fields and must match exactly,
  or it is refused, never written on trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any send / transmit / deliver / post action — an
  action parameter naming one refuses the whole call;
* any bulk or recipient-list input — a list of
  recipients refuses the whole call;
* any recipient inferred from context, history, or a
  partial name — a record flagged inferred or partial
  refuses the whole call;
* any message body the caller did not supply — a missing
  or empty body refuses the whole call;
* any claim that a draft was sent — a draft carrying
  sent=True, a sent status, or sent-claim wording in its
  body refuses the whole call.
The module imports no network, messaging, or mail code —
proven by the AST test in tests/test_comms_w9.py. It
opens no connection, authenticates to nothing, and
touches no inbox, outbox, or contact store: there is
nothing here to send with, by construction.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused action, a list recipient, an inferred
recipient, a missing body, or a sent claim refuses the
whole call. Nothing is truncated, addressed from a
guess, or quietly reworded: a draft the caller did not
write, to a recipient the caller did not name, is worse
than no draft.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency, no
telemetry write, no database access, no network access.
Nothing here reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

__all__ = [
    "MAX_RECIPIENT_CHARS",
    "MAX_CHANNEL_CHARS",
    "MAX_BODY_CHARS",
    "SEND_ACTIONS",
    "SENT_CLAIM_PHRASES",
    "build_message_draft",
    "check_recipient",
    "describe_send_readiness",
    "validate_recordable_draft",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different message.
MAX_RECIPIENT_CHARS = 200
MAX_CHANNEL_CHARS = 100
MAX_BODY_CHARS = 10000

#: Action words that refuse a call whole. "draft" is the
#: only action this module performs, and it is the
#: default; anything else names work this module must
#: not do.
SEND_ACTIONS = frozenset({
    "send", "transmit", "deliver", "post", "dispatch",
    "forward", "publish", "broadcast",
})

#: Phrases that claim a draft was already sent. A body
#: carrying one refuses whole: a draft must never be
#: presented as sent, by its own text or by any caller.
SENT_CLAIM_PHRASES = (
    "already sent",
    "was sent",
    "has been sent",
    "successfully sent",
    "message sent",
)


def _check_action(action):
    if action is None:
        return
    if not isinstance(action, str):
        raise ValueError("action must be a string naming the action")
    word = action.strip().lower()
    if word in ("", "draft"):
        return
    if word in SEND_ACTIONS:
        raise ValueError(
            f"refused — action {word!r} is a send action: this "
            f"module drafts only and never sends anything")
    raise ValueError(
        f"refused — unknown action {word!r}: this module "
        f"performs only the draft action")


def _check_text_field(name, value, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty: the caller "
                         f"must supply it, it is never invented")
    if len(stripped) > maximum:
        raise ValueError(f"{name} exceeds {maximum} chars")
    return stripped


def _refuse_sent_claim(body):
    lowered = body.lower()
    for phrase in SENT_CLAIM_PHRASES:
        if phrase in lowered:
            raise ValueError(
                f"refused — body claims the message was sent "
                f"({phrase!r}): a draft is never presented as sent")


def build_message_draft(recipient, body, channel, action=None):
    """A message draft from caller-supplied fields only.

    The recipient is a single caller-supplied string, never
    a list and never an inferred contact; the body is the
    caller's own words, never generated here; the channel
    is a caller-supplied label, never a resolved route.
    The draft is returned with sent=False and status
    DRAFT, and the note says plainly that it is not sent
    and what would still be needed. Any send-shaped
    action, list recipient, or sent claim refuses the
    whole call."""
    if isinstance(recipient, (list, tuple, set, dict)):
        raise ValueError(
            "refused — bulk or recipient-list input: this "
            "module drafts to one caller-supplied recipient "
            "at a time, never to a list")
    _check_action(action)
    name = _check_text_field("recipient", recipient, MAX_RECIPIENT_CHARS)
    text = _check_text_field("body", body, MAX_BODY_CHARS)
    route = _check_text_field("channel", channel, MAX_CHANNEL_CHARS)
    _refuse_sent_claim(text)
    return {
        "kind": "MESSAGE_DRAFT",
        "recipient": name,
        "body": text,
        "channel": route,
        "sent": False,
        "status": "DRAFT",
        "note": ("A draft only — this message has NOT been sent. "
                 "To send it, a human would still need to "
                 "confirm it, and a real send pipeline would "
                 "still need to exist. Neither is provided "
                 "by this tool."),
    }


def check_recipient(record):
    """A recipient check on a caller-supplied record only.

    The record must be an object with a name and a
    channel, both supplied as strings. The result is
    "supplied" when both are present and "missing" when
    either is absent or empty, naming which. It never
    guesses a recipient, never merges records, and never
    infers a relationship — a relationship field, if one
    is present, is not read and not returned. A list
    input refuses whole as bulk input, and a record
    flagged inferred or partial refuses whole as an
    inferred recipient."""
    if isinstance(record, (list, tuple, set)):
        raise ValueError(
            "refused — bulk input: a recipient check takes "
            "one caller-supplied record at a time, never a list")
    if not isinstance(record, dict):
        raise ValueError("record must be an object with name and channel")
    if record.get("inferred") or record.get("partial"):
        raise ValueError(
            "refused — inferred recipient: the recipient must "
            "be supplied in full by the caller, never inferred "
            "from context, history, or a partial name")
    name = record.get("name")
    channel = record.get("channel")
    missing = []
    if not isinstance(name, str) or not name.strip():
        missing.append("name")
    if not isinstance(channel, str) or not channel.strip():
        missing.append("channel")
    if missing:
        return {
            "kind": "RECIPIENT_CHECK",
            "status": "missing",
            "missing": missing,
            "note": ("The recipient record is incomplete: "
                     + ", ".join(missing) + " not supplied. "
                     "Nothing was guessed to fill the gap."),
        }
    return {
        "kind": "RECIPIENT_CHECK",
        "status": "supplied",
        "name": name.strip(),
        "channel": channel.strip(),
        "note": ("Recipient supplied by the caller: name and "
                 "channel as given, nothing inferred, nothing "
                 "merged."),
    }


def describe_send_readiness(draft):
    """A send-readiness descriptor for a draft.

    It validates the draft's own shape — kind
    MESSAGE_DRAFT, caller-supplied recipient, body, and
    channel, sent False, status DRAFT, no sent claim in
    the body — and then states honestly that the draft is
    NOT sent and NOT ready to send on its own: the two
    missing steps are a human confirmation and a real
    send pipeline, neither of which this build provides.
    A draft claiming sent refuses whole. Note the honest
    limit: this function assesses the draft presented to
    it; it cannot know whether that draft was altered
    from some other draft, and it does not claim to.
    Tamper-proofing a record is the record tool's job,
    which writes only the module-rebuilt draft, never a
    caller-supplied object verbatim."""
    if not isinstance(draft, dict):
        raise ValueError("draft must be a message-draft object")
    if draft.get("kind") != "MESSAGE_DRAFT":
        raise ValueError("draft kind must be MESSAGE_DRAFT")
    if draft.get("sent") is not False:
        raise ValueError(
            "refused — draft claims sent: a draft is never "
            "presented as sent")
    if draft.get("status") != "DRAFT":
        raise ValueError("draft status must be DRAFT")
    rebuilt = build_message_draft(
        draft.get("recipient"), draft.get("body"), draft.get("channel"))
    return {
        "kind": "SEND_READINESS",
        "sent": False,
        "ready": False,
        "recipient": rebuilt["recipient"],
        "channel": rebuilt["channel"],
        "missing_steps": [
            "a human confirmation of this exact draft",
            "a real send pipeline (none exists in this build)",
        ],
        "note": ("This draft has NOT been sent and is not "
                 "ready to send on its own. Sending would "
                 "still need a human to confirm this exact "
                 "draft and a real send pipeline to carry "
                 "it — this tool provides neither."),
    }


def validate_recordable_draft(fields):
    """Re-validate a draft a caller wants recorded, by
    re-running the builder that made it and requiring the
    fields to match exactly. A draft that does not
    re-build is refused, never written on trust. Returns
    the rebuilt draft dict, or raises ValueError."""
    if not isinstance(fields, dict):
        raise ValueError("draft fields must be an object")
    allowed = {"recipient", "body", "channel"}
    unexpected = sorted(set(fields) - allowed)
    if unexpected:
        raise ValueError(
            f"draft carries unexpected fields {unexpected}: a "
            f"draft that does not re-build exactly is refused, "
            f"never written on trust")
    rebuilt = build_message_draft(
        fields.get("recipient"), fields.get("body"), fields.get("channel"))
    for name in allowed:
        if fields.get(name, "").strip() != rebuilt[name]:
            raise ValueError(f"draft field {name} does not re-build")
    return rebuilt
