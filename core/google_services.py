"""
Your world (v2.4): Google Calendar + Gmail for VYUK — reads since
v2.4, two narrowly-scoped writes since v5.2.

VYUK can know the user's day — today's schedule, what's coming up, and
what mail is waiting — through Google's official APIs, behind the
user's own OAuth consent:

- **Flow:** the standard Google OAuth "Desktop app" flow
  (``InstalledAppFlow.run_local_server``) — the consent page opens in
  the user's own browser, Google hands the token back to a localhost
  redirect, and the token lands at ``data/google_token.json``.
- **Scopes:** ``gmail.readonly`` + ``calendar.readonly`` for the
  reads, plus exactly two write scopes since v5.2 — ``gmail.send``
  (send only: no read, no delete) and ``calendar.events`` (create
  events). There is still NO delete anywhere: no delete scope is
  requested and no function here deletes mail or events. Both write
  tools are additionally behind the executor's human confirm gate
  (core/funnel/tools_registry.py) — a send or a create never happens on the model's
  word alone. A token granted before v5.2 carries only the read
  scopes; the write path detects that and asks for one reconnect
  instead of misreporting it as "not connected" (see
  ``_scope_missing_message``).
- **Client codes are the user's own:** the OAuth client id/secret come
  from ``config.google_client_id`` / ``config.google_client_secret``
  (``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET`` in ``.env``, saved
  from Settings via ``config.save_env_setting``). A verified bundled
  client shipped with the product is the commercial path; until then
  every user brings their own Google Cloud "Desktop app" client.
- **Token at rest is protected (v5.47, programme item 14):** the
  token file's CONTENT is a core/secrets_store envelope, not the
  raw token JSON — every read and write goes through
  ``_read_token_text`` / ``_save_token_text`` below, and code
  outside this module only ever sees the unprotected JSON text.
  A legacy plaintext token file (pre-v5.47) is used as-is on first
  read AND rewritten protected (first-run migration). A protected
  file that cannot be unprotected — corrupt, tampered, or its
  machine key is gone — behaves exactly like a missing token (the
  not-connected path); it never crashes a caller.
- **Lazy imports, string results:** every google library is imported
  inside the function that needs it, all public functions return
  human strings, and none of them raise — a missing library, a missing
  token, or a Google-side failure all come back as a plain sentence
  the brain can speak (the pattern the rest of ``core/`` follows).
"""

import datetime
import html
import json
import os
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path

from . import injection

from .google_services_helpers import (
    GMAIL_SEND_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    _LIBS_MISSING,
    _WEEKDAYS,
    _MONTHS,
    _short,
)

from .google_services_token import (
    SCOPES,
    _CONNECTED_OK,
    _DISCONNECTED_OK,
    _NO_CODES,
    _token_path,
    _save_token_text,
    _read_token_text,
    _save_credentials,
    is_connected,
    _run_flow,
    connect,
    disconnect,
    _credential_failure_logged,
    _log_credential_failure,
    _credentials_from_token_text,
    _load_credentials_detailed,
    _load_credentials,
)

from .google_services_services import (
    NOT_CONNECTED,
    _AUTH_EXPIRED,
    _resolve_service,
    _get_service,
    _last_service_state,
    _calendar_service_or_none,
    _gmail_service_or_none,
    _failure_message,
    _service_or_failure,
    _scope_missing_message,
    _granted_scopes,
    _resolve_write_service,
    _write_service_or_failure,
    _SCOPE_ERROR_MARKERS,
    _is_scope_error,
)

from .google_services_calendar import (
    _local_tz,
    _parse_event_dt,
    _event_start,
    _format_event,
    _calendar_ids,
    _event_sort_key,
    _fetch_events,
    _SCOPE_NOTE,
    todays_schedule,
    upcoming_events,
    next_events,
    create_event,
)

from .google_services_gmail import (
    _header,
    _decode_mime,
    _sender_name,
    _message_time,
    _render_message,
    _list_messages,
    unread_email,
    _message_body_text,
    search_email,
    _ATTACHMENT_MAX_BYTES,
    _MIME_EXTENSIONS,
    _human_size,
    _collect_attachments,
    _sanitise_filename,
    _attachment_listing,
    download_attachment,
    send_email,
)

from .google_services_sync import sync_updates  # GAPS-7: the sync_updates tool

__all__ = [
    "SCOPES",
    "NOT_CONNECTED",
    "is_connected",
    "connect",
    "disconnect",
    "todays_schedule",
    "upcoming_events",
    "next_events",
    "unread_email",
    "search_email",
    "download_attachment",
    "send_email",
    "create_event",
    "sync_updates",
]
