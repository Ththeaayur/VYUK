"""Part of the core/google_services.py split (B25): google_services_gmail. Move only — see core/google_services.py for the facade."""

import datetime
import html
import json
import os
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from .google_services_helpers import GMAIL_SEND_SCOPE, _MONTHS, _short  # INTENTIONAL (B25): shared names
from .google_services_services import _is_scope_error, _scope_missing_message, _service_or_failure, _write_service_or_failure  # INTENTIONAL (B25): service family
from .google_services_calendar import _local_tz  # INTENTIONAL (B25): _message_time converts to local time; calendar does not import gmail, so no cycle
from . import injection  # INTENTIONAL (B25): module-level injection, as in the old module
from .connector_sync import (  # GAPS-7: the shared sync engine
    DEFAULT_PAGE_SIZE, MAX_LIST_ITEMS, describe_partial, paginate)


def _header(headers: list, name: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value") or ""
    return ""


def _decode_mime(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _sender_name(from_value: str) -> str:
    name, addr = parseaddr(from_value or "")
    name = _decode_mime(name).strip()
    return name or addr or "Unknown sender"


def _message_time(date_value: str, today: datetime.date) -> str:
    try:
        when = parsedate_to_datetime(date_value)
    except Exception:  # noqa: BLE001
        return ""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    when = when.astimezone(_local_tz())
    if when.date() == today:
        return f"{when:%H:%M}"
    return f"{when.day} {_MONTHS[when.month - 1]} {when:%H:%M}"


def _render_message(svc, stub: dict, today: datetime.date,
                    stub_id: bool = False,
                    body_text=None) -> list[str]:
    try:
        msg = (
            svc.users()
            .messages()
            .get(userId="me", id=stub.get("id"), format="metadata")
            .execute()
        )
    except Exception:  # noqa: BLE001 — one bad message shouldn't sink the list
        return []
    headers = (msg.get("payload") or {}).get("headers") or []
    sender = _sender_name(_header(headers, "From"))
    subject = _decode_mime(_header(headers, "Subject")).strip() or "(no subject)"
    when = _message_time(_header(headers, "Date"), today)
    lines = [f"• {sender} — {subject}" + (f" ({when})" if when else "")]
    if stub_id and stub.get("id"):
        # The id is how the brain names THIS message later
        # (e.g. download_email_attachment) — #25.
        lines[0] += f" [id: {stub['id']}]"
    snippet = html.unescape(" ".join((msg.get("snippet") or "").split()))
    if snippet:
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "…"
        lines.append(f"  {snippet}")
    if body_text:
        lines.append(f"  {body_text}")
    return lines


def _list_messages(svc, query: str, limit: int):
    """Lists messages through the shared sync engine (GAPS-7): pages
    of at most 100 until ``limit`` items or exhaustion, each page
    retried on transient failures. Returns the old listing shape
    ({"messages": [...], "resultSizeEstimate": ... — the estimate
    rides on the first page only) plus the engine's completeness
    report ("complete", "pages", "retries", "stopped", "error") —
    callers disclose a partial set with the reason instead of
    presenting it as the whole inbox. Before GAPS-7 this was one
    list call capped at 25, the nextPageToken never followed."""
    # ``limit`` None means exhaust every page (the sync's full
    # incremental fetch); an int caps the walk at that many items.
    if limit is None:
        page_size = DEFAULT_PAGE_SIZE
    else:
        page_size = max(1, min(int(limit), DEFAULT_PAGE_SIZE))
    estimate = {"value": 0}

    def fetch_page(token):
        kwargs = dict(userId="me", q=query, labelIds=["INBOX"],
                      maxResults=page_size)
        if token:
            kwargs["pageToken"] = token
        result = svc.users().messages().list(**kwargs).execute()
        if token is None:
            estimate["value"] = result.get("resultSizeEstimate") or 0
        return (result.get("messages") or [],
                result.get("nextPageToken"))

    walked = paginate(fetch_page, page_size=page_size, limit=limit)
    return {
        "messages": walked["items"],
        "resultSizeEstimate": estimate["value"],
        "complete": walked["complete"],
        "pages": walked["pages"],
        "retries": walked["retries"],
        "stopped": walked["stopped"],
        "error": walked["error"],
    }


def unread_email(limit: int = 8) -> str:
    """The unread mail waiting in the inbox, newest-listed first."""
    svc, failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return failure
    try:
        limit = max(1, min(int(limit), MAX_LIST_ITEMS))
    except Exception:  # noqa: BLE001
        limit = 8
    try:
        listing = _list_messages(svc, "is:unread", limit)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read your mail just now ({_short(e)})."
    stubs = listing.get("messages") or []
    if not stubs:
        # A failed first page is a read failure, never "inbox
        # zero" — the engine reports it instead of raising.
        if not listing.get("complete", True):
            return ("Couldn't read your mail just now"
                    f"{describe_partial(listing)}.")
        return "No unread email — inbox zero."
    # Snoozed mail stays in the inbox on Google's side but is
    # hidden from VYUK's own inbox surfaces while the snooze is
    # active (Stage 0 features batch 2, item 20). The count
    # below names what was hidden, so a fully-snoozed inbox is
    # never reported as inbox zero.
    hidden = 0
    snoozed = _active_snooze_ids()
    if snoozed:
        kept = [s for s in stubs
                if str(s.get("id") or "") not in snoozed]
        hidden = len(stubs) - len(kept)
        stubs = kept
    if not stubs:
        return (f"No unread email showing — {hidden} snoozed "
                f"{'mail' if hidden == 1 else 'mails'} will be "
                "back in your inbox when the time comes.")
    today = datetime.datetime.now().astimezone().date()
    lines: list[str] = []
    total = listing.get("resultSizeEstimate") or 0
    if total:
        # resultSizeEstimate is exactly what its name says — an
        # ESTIMATE (Gmail documents it as approximate). Never present
        # it as an exact count.
        lines.append(
            f"You have about {total} unread email{'s' if total != 1 else ''}:")
    else:
        # No estimate from Gmail: the headline still leads
        # with a count (#36) — of exactly what is listed.
        lines.append(
            f"{len(stubs)} unread message"
            f"{'s' if len(stubs) != 1 else ''} "
            "(the latest ones):")
    failed = 0
    body_lines: list[str] = []
    for stub in stubs:
        rendered = _render_message(svc, stub, today)
        if rendered:
            body_lines.extend(rendered)
        else:
            failed += 1
    # Senders/subjects/snippets are untrusted content (v5.3): the
    # rendered messages go to the brain inside the envelope; VYUK's
    # own count header and load-failure trailer stay outside it.
    if body_lines:
        lines.append(injection.wrap_untrusted("email", "\n".join(body_lines)))
    if failed:
        lines.append(
            f"({failed} message{'s' if failed != 1 else ''} couldn't be loaded.)")
    if hidden:
        lines.append(
            f"({hidden} snoozed — "
            f"{'it is' if hidden == 1 else 'they are'} back in your "
            "inbox when the time comes.)")
    if not listing.get("complete", True):
        # GAPS-7: a partial set is disclosed with the reason, never
        # silently presented as the whole inbox.
        lines.append(
            f"(Couldn't load the rest of your mail — showing "
            f"{len(stubs)} of them{describe_partial(listing)}.)")
    return "\n".join(lines)


def unread_count(limit: int = 50) -> int:
    """The number of unread messages in the inbox (an exact count
    of what is listed, never the resultSizeEstimate — Gmail
    documents that as approximate). Snoozed mail is excluded,
    mirroring unread_email. Returns 0 when not connected or on
    any read failure (the caller omits the clause, never fakes
    a number). UI Phase 1 (2026-11-24): the Home greeting's
    unread clause needs a count, and unread_email returns prose.
    """
    svc, _failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return 0
    try:
        listing = _list_messages(svc, "is:unread", max(1, int(limit)))
    except Exception:  # noqa: BLE001
        return 0
    stubs = listing.get("messages") or []
    snoozed = _active_snooze_ids()
    if snoozed:
        stubs = [s for s in stubs
                 if str(s.get("id") or "") not in snoozed]
    return len(stubs)


def _message_body_text(svc, message_id: str) -> str:
    """A message's body as plain text (#25): the first
    text/plain part of the full payload, base64-decoded,
    whitespace-collapsed and capped at 600 characters.
    HTML-only mail falls back to its text/html part with
    the tags stripped. '' when nothing readable is there
    or the fetch fails."""
    import base64
    import re

    try:
        msg = (
            svc.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except Exception:  # noqa: BLE001
        return ""
    queue = [(msg or {}).get("payload") or {}]
    html_part = ""
    while queue:
        part = queue.pop(0)
        mime = (part.get("mimeType") or "").lower()
        data_field = ((part.get("body") or {}).get("data") or "")
        if data_field and mime == "text/plain":
            try:
                raw = base64.urlsafe_b64decode(
                    data_field + "=" * (-len(data_field) % 4))
                text = " ".join(raw.decode("utf-8", "replace").split())
            except Exception:  # noqa: BLE001
                return ""
            return text[:600] + ("…" if len(text) > 600 else "")
        if data_field and mime == "text/html" and not html_part:
            html_part = data_field
        queue.extend(part.get("parts") or [])
    if html_part:
        try:
            raw = base64.urlsafe_b64decode(
                html_part + "=" * (-len(html_part) % 4))
            text = raw.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""
        text = html.unescape(
            " ".join(re.sub(r"<[^>]+>", " ", text).split()))
        return text[:600] + ("…" if len(text) > 600 else "")
    return ""


def search_email(query: str, limit: int = 6,
                 include_body: bool = False) -> str:
    """Searches the inbox with a Gmail query (from:, subject:, words…).

    Every result carries its message id; include_body also
    reads each message's full text (#25)."""
    svc, failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return failure
    query = (query or "").strip()
    if not query:
        return "Give me something to search for — a name, a subject, or a word."
    try:
        limit = max(1, min(int(limit), MAX_LIST_ITEMS))
    except Exception:  # noqa: BLE001
        limit = 6
    try:
        listing = _list_messages(svc, query, limit)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't search your mail just now ({_short(e)})."
    stubs = listing.get("messages") or []
    if not stubs:
        # A failed first page is a read failure, never "no emails
        # found" — the engine reports it instead of raising.
        if not listing.get("complete", True):
            return ("Couldn't search your mail just now"
                    f"{describe_partial(listing)}.")
        return f"No emails found for '{query}'."
    today = datetime.datetime.now().astimezone().date()
    lines: list[str] = []
    failed = 0
    body_lines: list[str] = []
    for stub in stubs:
        rendered = _render_message(
            svc, stub, today, stub_id=True,
            body_text=(_message_body_text(svc, stub.get("id"))
                       if include_body else None))
        if rendered:
            body_lines.extend(rendered)
        else:
            failed += 1
    if body_lines:
        lines.append(injection.wrap_untrusted("email", "\n".join(body_lines)))
    if failed:
        lines.append(
            f"({failed} message{'s' if failed != 1 else ''} couldn't be loaded.)")
    if not listing.get("complete", True):
        # GAPS-7: a partial set is disclosed with the reason, never
        # silently presented as the whole result.
        lines.append(
            f"(Couldn't load the rest — showing "
            f"{len(stubs)} of them{describe_partial(listing)}.)")
    return "\n".join(lines)


def recent_messages(limit: int = 20) -> list[dict]:
    """The inbox as structured data — NOT a tool.

    Powers the Inbox view (Stage 0 features batch 1): the newest
    messages as {"id", "sender", "subject", "when", "snippet"}
    dicts, newest first. Same never-raises contract as the other
    reads: not connected, missing libraries, or any Google-side
    failure all come back as an empty list, which callers read as
    "nothing to show". A partial page walk still returns whatever
    messages did land.
    """
    svc, _failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return []
    try:
        limit = max(1, min(int(limit), 50))
    except Exception:  # noqa: BLE001
        limit = 20
    try:
        listing = _list_messages(svc, "", limit)
    except Exception:  # noqa: BLE001
        return []
    stubs = listing.get("messages") or []
    today = datetime.datetime.now().astimezone().date()
    out: list[dict] = []
    # Snoozed mail is hidden from the inbox surface while the
    # snooze is active (Stage 0 features batch 2, item 20) —
    # explicit search still finds it.
    snoozed = _active_snooze_ids()
    if snoozed:
        stubs = [s for s in stubs
                 if str(s.get("id") or "") not in snoozed]
    for stub in stubs:
        try:
            msg = (
                svc.users()
                .messages()
                .get(userId="me", id=stub.get("id"), format="metadata")
                .execute()
            )
        except Exception:  # noqa: BLE001 — one bad message shouldn't sink the list
            continue
        headers = (msg.get("payload") or {}).get("headers") or []
        snippet = html.unescape(" ".join((msg.get("snippet") or "").split()))
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "…"
        out.append(
            {
                "id": str(stub.get("id") or ""),
                "sender": _sender_name(_header(headers, "From")),
                "subject": _decode_mime(_header(headers, "Subject")).strip()
                or "(no subject)",
                "when": _message_time(_header(headers, "Date"), today),
                "snippet": snippet,
            }
        )
    return out


def message_body(message_id: str) -> str:
    """One message's body as plain text — NOT a tool.

    Powers the Inbox view's reading pane (Stage 0 features batch
    1): the first text/plain part, whitespace-collapsed and capped
    at 600 characters (see ``_message_body_text``). "" when the id
    is empty, the message can't be read, or nothing readable is
    there — never raises.
    """
    message_id = str(message_id or "").strip()
    if not message_id:
        return ""
    svc, _failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return ""
    try:
        return _message_body_text(svc, message_id)
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------
# Email snooze (Stage 0 features batch 2, item 20).
#
# Where snoozed mail lives: NOWHERE in Gmail. Snooze is a local
# record in data/email_snoozed.json — the message stays in the
# inbox on Google's side, unread and untouched, and only VYUK's
# own inbox surfaces (get_unread_email, recent_messages / the
# Inbox view) hide it while the snooze is active. How it comes
# back: expiry is checked at READ time, so a snooze whose time
# has passed is simply not hidden any more — no daemon, no
# re-delivery, nothing to go wrong while VYUK is closed. The
# snooze tool itself lists what's snoozed (empty message id) and
# brings one back early (message id, no until). Explicit search
# (search_email) still finds snoozed mail — searching for a mail
# is not the inbox, and hiding a search hit would be a lie.
# ----------------------------------------------------------------------

def _snooze_path(path=None):
    """The local snooze file (data/email_snoozed.json). Resolved
    lazily so importing this module never touches data/ — the
    feedback_report precedent."""
    if path is not None:
        return Path(path)
    from .config import DATA_DIR  # noqa: E402
    return DATA_DIR / "email_snoozed.json"


def _load_snoozed(path=None):
    """Snooze rows as a list — [] when the file is absent or
    unreadable. Never raises: a broken snooze file must not sink
    the inbox."""
    try:
        rows = json.loads(
            Path(_snooze_path(path)).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows
            if isinstance(r, dict) and r.get("message_id")]


def _save_snoozed(rows, path=None):
    """Atomic whole-store write (A3-atomic): mkstemp + os.replace,
    True only when the bytes landed. Callers check the return and
    report failure honestly."""
    target = Path(_snooze_path(path))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except Exception:  # noqa: BLE001
        return False


def _parse_snooze_until(value: str):
    """\"tomorrow\" / ISO date / ISO datetime → an aware local
    datetime, or None. A bare date means 09:00 that day; an empty
    value means tomorrow 09:00 (the default)."""
    now = datetime.datetime.now().astimezone()
    text = (value or "").strip().lower()
    if not text or text == "tomorrow":
        day = (now + datetime.timedelta(days=1)).date()
        return datetime.datetime(day.year, day.month, day.day,
                                 9, 0, tzinfo=_local_tz())
    try:
        day = datetime.date.fromisoformat(text[:10])
    except Exception:  # noqa: BLE001
        return None
    if len(text) <= 10:
        return datetime.datetime(day.year, day.month, day.day,
                                 9, 0, tzinfo=_local_tz())
    try:
        when = datetime.datetime.fromisoformat(
            text.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_local_tz())
    return when.astimezone(_local_tz())


def _active_snooze_ids(path=None):
    """Message ids whose snooze has not expired yet. Never raises
    — a broken store hides nothing, it never breaks the inbox."""
    try:
        now = datetime.datetime.now().astimezone()
        active = set()
        for row in _load_snoozed(path=path):
            until = _parse_snooze_until(str(row.get("snooze_until") or ""))
            if until is not None and until > now:
                active.add(str(row.get("message_id")))
        return active
    except Exception:  # noqa: BLE001
        return set()


def _list_snoozed(path=None):
    """Snoozed mail as plain text — what is snoozed, from whom,
    and when it comes back. Expired rows are shown as due back
    (they are already visible in the inbox again)."""
    rows = _load_snoozed(path=path)
    if not rows:
        return "No mail snoozed."
    now = datetime.datetime.now().astimezone()
    lines = [f"Snoozed mail ({len(rows)}):"]
    for row in rows:
        until = _parse_snooze_until(str(row.get("snooze_until") or ""))
        when = (f"{until:%d %b %H:%M}" if until is not None
                else "some time")
        due = "" if (until is not None and until > now) else " — due back"
        lines.append(
            f"• \"{row.get('subject') or '(no subject)'}\" from "
            f"{row.get('sender') or 'Unknown sender'} — back {when}{due}")
    return "\n".join(lines)


def _unsnooze(message_id: str, path=None):
    """Removes one snooze record. True when a row was removed and
    the store saved; False when there was nothing to remove or the
    save failed."""
    rows = _load_snoozed(path=path)
    kept = [r for r in rows
            if str(r.get("message_id")) != str(message_id)]
    if len(kept) == len(rows):
        return False
    return _save_snoozed(kept, path=path)


def _fetch_message_meta(svc, message_id: str):
    """{subject, sender, thread_id} for one message, or None when
    Gmail can't show it."""
    try:
        msg = (svc.users().messages().get(
            userId="me", id=message_id, format="metadata").execute())
    except Exception:  # noqa: BLE001
        return None
    headers = (msg.get("payload") or {}).get("headers") or []
    return {
        "subject": _decode_mime(_header(headers, "Subject")).strip()
        or "(no subject)",
        "sender": _sender_name(_header(headers, "From")),
        "thread_id": str(msg.get("threadId") or ""),
    }


def snooze_email(message_id: str = "", until: str = "") -> str:
    """Snoozes one mail, lists snoozed mail, or brings one back
    early. With no message id it lists what's snoozed; with a
    message id and no ``until`` it snoozes until tomorrow 09:00 —
    unless the mail is already snoozed, in which case it brings
    it back now. ``until`` takes \"tomorrow\", an ISO date (09:00
    that day) or an ISO datetime. Free by design (app-state):
    the record lives only in VYUK's own data store, nothing is
    sent, deleted or changed in Gmail. Every failure is an
    _outcome.failed — a plain string would be read as success
    by the receipts while the mail was never snoozed (F-F501).
    The write is verified by re-read (A3-atomic + the record
    present after the save)."""
    from . import tool_outcome as _outcome  # R1b: seam wiring
    message_id = str(message_id or "").strip()
    if not message_id:
        return _list_snoozed()
    if not (until or "").strip():
        rows = _load_snoozed()
        if any(str(r.get("message_id")) == message_id for r in rows):
            if _unsnooze(message_id):
                return ("That mail is back in your inbox now — "
                        "the snooze is off.")
            return _outcome.failed(
                "I couldn't lift that snooze just now — the "
                "snooze file wouldn't save.",
                "snooze store write failed")
    when = _parse_snooze_until(until)
    if when is None:
        return _outcome.failed(
            "I couldn't understand that time — say \"tomorrow\", a "
            "date like 2026-11-23, or an ISO time.",
            "snooze time unparseable")
    svc, failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return _outcome.failed(failure, "gmail service unavailable")
    meta = _fetch_message_meta(svc, message_id)
    if meta is None:
        return _outcome.failed(
            "I couldn't find that message — check the message id.",
            "message not found")
    rows = _load_snoozed()
    now = datetime.datetime.now().astimezone()

    def _live(row):
        until = _parse_snooze_until(str(row.get("snooze_until") or ""))
        return until is not None and until > now

    # Prune expired rows and drop any old record for this message;
    # the new record below replaces it.
    rows = [r for r in rows
            if str(r.get("message_id")) != message_id and _live(r)]
    rows.append({
        "message_id": message_id,
        "thread_id": meta["thread_id"],
        "subject": meta["subject"],
        "sender": meta["sender"],
        "snoozed_at": now.isoformat(),
        "snooze_until": when.isoformat(),
    })
    if not _save_snoozed(rows):
        return _outcome.failed(
            "I couldn't save that snooze just now.",
            "snooze store write failed")
    check = _load_snoozed()
    if not any(str(r.get("message_id")) == message_id for r in check):
        return _outcome.failed(
            "The snooze didn't stick — the record isn't there.",
            "snooze unverified")
    return (f"Snoozed \"{meta['subject']}\" from {meta['sender']} "
            f"until {when:%d %b %H:%M}. It will be back in your "
            f"inbox then.")


_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # Gmail's own attachment ceiling


_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        ".xlsx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def _human_size(num_bytes) -> str:
    """84 KB / 1.5 MB style sizes for result strings."""
    try:
        n = float(num_bytes or 0)
    except Exception:  # noqa: BLE001
        n = 0.0
    if n < 1024:
        return f"{int(n)} B"
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= factor:
            text = f"{n / factor:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return f"{int(n)} B"


def _collect_attachments(part: dict, out: list) -> None:
    """Walks a message payload's parts tree (multipart nesting can
    bury the real attachment several levels down) collecting every
    part that carries a filename and a downloadable body."""
    if not isinstance(part, dict):
        return
    body = part.get("body") or {}
    filename = (part.get("filename") or "").strip()
    if filename and body.get("attachmentId"):
        out.append({
            "filename": filename,
            "attachment_id": body["attachmentId"],
            "size": body.get("size") or 0,
            "mime_type": (part.get("mimeType") or "").split(";")[0].strip(),
        })
    for child in part.get("parts") or []:
        _collect_attachments(child, out)


def _sanitise_filename(raw_name: str, mime_type: str = "") -> str:
    """Reduces an attachment's claimed filename to a safe plain name:
    no directories, no drive, no leading dots, no characters Windows
    or a shell would treat specially, at most 120 characters. A name
    that sanitises away to nothing becomes "attachment" plus the
    mime type's usual extension when that's known — the file the
    sender named is never allowed to choose where it lands."""
    import re

    name = (raw_name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"^[A-Za-z]:", "", name)
    name = name.lstrip(". ").rstrip(". ")
    name = "".join(
        "_" if (ch in '<>:"/\\|?*' or ord(ch) < 32 or ord(ch) == 127)
        else ch
        for ch in name
    ).strip()
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[:120 - len(ext) - 1] + "." + ext
        else:
            name = name[:120]
        name = name.rstrip(". ")
    if not name:
        name = "attachment" + _MIME_EXTENSIONS.get(
            (mime_type or "").lower(), "")
    return name


def _attachment_listing(attachments: list) -> str:
    return ", ".join(
        f"'{a['filename']}' ({_human_size(a['size'])})"
        for a in attachments)


def download_attachment(message_id: str = "", filename: str = "",
                        save_dir: str = "") -> str:
    """Downloads one attachment from one Gmail message to disk
    (v5.5). A READ — it resolves through the plain read path, so a
    read-only sign-in is all it needs, and its failures phrase
    through the same taxonomy as the other mail tools.

    ``filename`` picks among several attachments (case-insensitive;
    an exact match wins, a single substring match is enough);
    without one, a lone attachment is taken and several are listed
    back so the user can choose. Files land in ``save_dir`` (default
    ~/Downloads, created when missing) under a sanitised version of
    the sender's filename, never overwriting an existing file."""
    import base64

    message_id = (message_id or "").strip()
    if not message_id:
        return ("I need to know which email the attachment is on — "
                "give me its message id.")
    svc, failure = _service_or_failure("gmail", "your mail")
    if svc is None:
        return failure
    try:
        msg = (
            svc.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read your mail just now ({_short(e)})."
    attachments: list[dict] = []
    _collect_attachments((msg or {}).get("payload") or {}, attachments)
    if not attachments:
        return "That email doesn't have any attachments."
    wanted = (filename or "").strip()
    chosen = None
    if wanted:
        exact = [a for a in attachments
                 if a["filename"].lower() == wanted.lower()]
        partial = [a for a in attachments
                   if wanted.lower() in a["filename"].lower()]
        if len(exact) == 1:
            chosen = exact[0]
        elif len(exact) > 1 or len(partial) > 1:
            return (
                f"'{wanted}' matches more than one attachment on "
                f"that email: {_attachment_listing(attachments)}. "
                "Which one — give me the full name."
            )
        elif len(partial) == 1:
            chosen = partial[0]
        else:
            return (
                f"There's no attachment called '{wanted}' on that "
                f"email — it has {_attachment_listing(attachments)}."
            )
    elif len(attachments) == 1:
        chosen = attachments[0]
    else:
        return (
            f"That email has {len(attachments)} attachments: "
            f"{_attachment_listing(attachments)}. "
            "Which one should I download?"
        )
    declared = chosen["size"]
    if declared and declared > _ATTACHMENT_MAX_BYTES:
        return (
            f"'{chosen['filename']}' is {_human_size(declared)} — "
            "over the 25 MB attachment limit, so I didn't "
            "download it."
        )
    try:
        blob = (
            svc.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id,
                 id=chosen["attachment_id"])
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        return f"Couldn't download that attachment just now ({_short(e)})."
    data_field = (blob or {}).get("data") or ""
    try:
        raw = base64.urlsafe_b64decode(
            data_field + "=" * (-len(data_field) % 4))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't download that attachment just now ({_short(e)})."
    if len(raw) > _ATTACHMENT_MAX_BYTES:
        return (
            f"'{chosen['filename']}' is {_human_size(len(raw))} — "
            "over the 25 MB attachment limit, so I didn't save it."
        )
    if save_dir:
        directory = Path(save_dir).expanduser()
    else:
        directory = Path.home() / "Downloads"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory = directory.resolve()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the attachment ({_short(e)})."
    safe_name = _sanitise_filename(chosen["filename"], chosen["mime_type"])
    target = directory / safe_name
    renamed = False
    if target.exists():
        renamed = True
        stem, dot, ext = safe_name.rpartition(".")
        if dot:
            suffix = "." + ext
        else:
            stem, suffix = safe_name, ""
        counter = 2
        while target.exists():
            target = directory / f"{stem} ({counter}){suffix}"
            counter += 1
    try:
        target.write_bytes(raw)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the attachment ({_short(e)})."
    result = (
        f"Saved '{chosen['filename']}' "
        f"({_human_size(len(raw))}) to {target}"
    )
    if renamed:
        result += (
            f" — a file with that name was already there, so this "
            f"one is '{target.name}'"
        )
    # R1b: re-read the saved bytes — write_bytes returning does
    # not prove the file landed intact.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    ok, evidence = _outcome.file_written(target, expected_bytes=raw)
    if not ok:
        return (f"Couldn't confirm the saved attachment: {evidence}.")
    return _outcome.verified(result + ".", evidence)


def send_email(to: str = "", subject: str = "", body: str = "",
               thread_id: str = "", in_reply_to: str = "",
               attachments=None) -> str:
    """Sends an email through the user's Gmail (v5.2 — one of the
    two write tools).

    Gated twice, by design: the executor's human confirm gate
    (core/funnel/tools_registry.py) must approve the call, and the stored sign-in
    must carry the gmail.send scope — a read-only sign-in gets the
    scope sentence, never a false "not connected". ``thread_id`` +
    ``in_reply_to`` (the RFC message id being answered) make the
    send a threaded reply: the raw message carries the In-Reply-To /
    References headers and the request carries Gmail's threadId.
    ``attachments`` (R19-5, al1abb/invoify inspiration) is an
    optional list of file paths to attach (at most 5 files, each at
    most 25 MB — Gmail's own ceiling); each file is read and attached
    with its MIME type guessed from its extension (a .pdf attaches as
    application/pdf). A missing or unreadable attachment refuses the
    send before anything is mailed."""
    import base64
    import mimetypes
    from email.message import EmailMessage
    from pathlib import Path as _Path

    to = (to or "").strip()
    subject = (subject or "").strip()
    body = body if isinstance(body, str) else str(body or "")
    if not to:
        return "I need an address to send that to — who should the email go to?"
    attach_paths = []
    if attachments is not None:
        if isinstance(attachments, str):
            attachments = [attachments]
        if not isinstance(attachments, list):
            return "attachments must be a list of file paths."
        if len(attachments) > 5:
            return "at most 5 attachments per email."
        for item in attachments:
            if not isinstance(item, str) or not item.strip():
                return "each attachment must be a file path."
            path = _Path(item.strip())
            if not path.is_file():
                return ("could not attach %s — no such file."
                        % item.strip())
            if path.stat().st_size > _ATTACHMENT_MAX_BYTES:
                return ("could not attach %s — it is larger than"
                        " Gmail's 25 MB attachment ceiling."
                        % item.strip())
            attach_paths.append(path)
    svc, failure = _write_service_or_failure(
        "gmail", "your mail", GMAIL_SEND_SCOPE, "sending")
    if svc is None:
        return failure
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    for path in attach_paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            return ("could not attach %s (%s)." % (path, exc))
        mime, _encoding = mimetypes.guess_type(str(path))
        if mime is None:
            mime = "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(data, maintype=maintype or "application",
                               subtype=subtype or "octet-stream",
                               filename=path.name)
    payload = {
        "raw": base64.urlsafe_b64encode(
            message.as_bytes()).decode("ascii")
    }
    if thread_id:
        payload["threadId"] = thread_id
    try:
        sent = svc.users().messages().send(
            userId="me", body=payload).execute()
    except Exception as e:  # noqa: BLE001
        if _is_scope_error(e):
            return _scope_missing_message("sending")
        return f"Couldn't send that email just now ({_short(e)})."
    mid = (sent or {}).get("id") or ""
    result = f"Email sent to {to} — '{subject or '(no subject)'}'"
    if mid:
        result += f" (Gmail message id {mid})"
    return result + "."
