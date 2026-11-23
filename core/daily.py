"""Wave 7 (v5.62) — Daily layer + Presence Lock.

One module, one owner, no shipped GUI wiring. Everything here is
caller-driven: persistence lives ONLY in a store file whose path
the caller supplies (this module never hardcodes the tree's
``data/`` directory and never writes anywhere else), presence
comes ONLY from a caller-supplied signal (never stored — the
in-memory dwell state below is timestamps, not presence history),
and every gate below is fail-closed: a missing gate refuses
outright rather than degrading to an unguarded action.

Contents (plan §5.7 + PF acceptance criteria + A8):

  PF-20 quiet hours — user-settable, persisted in the caller's
      store, survive restarts. During quiet hours she does NOT
      speak or notify EXCEPT alarms. ``can_speak`` /
      ``can_notify`` enforce this; ``can_alarm`` is always True.
  PF-22 alarms / timers / reminders — fire at the set time
      INCLUDING across restarts (persisted state) and quiet
      hours. A fired alarm can be dismissed or snoozed. A MISSED
      alarm (its time passed while the assistant was down) is
      reported on start via ``check_missed`` — never silently
      dropped.
  PF-26 SOS notify — a send either CONFIRMS delivery or
      announces failure plainly. Unknown delivery status is
      treated as FAILURE, never success. ``send_sos`` returns
      delivery verdicts only; with no confirmed delivery channel
      it announces failure. No code path reports success without
      confirmation — silent non-delivery is a ship-blocker.
  Meeting notes — calendar + mic detect a meeting; notes and
      action items are produced. Capture-guard-gated WITHOUT
      exception (§8 S-6): opt-in + indicator + denylist. If ANY
      gate is missing, capture is REFUSED outright.
  Breach monitor — hash-prefix machinery ONLY (k-anonymity
      prefix computation, fully local — no password or full hash
      ever leaves the machine). The breach-data source terms are
      STILL-OWED and hard-block the item: ``BreachSource``
      REFUSES until terms are verified. No source is contacted
      by this module, and no live breach checking is claimed.
  Money paperwork engine — local parsing ONLY for AIS / 26AS /
      16 / P&L organisation + discrepancy FLAGGING (organisation,
      NOT tax computation, advice, or filing). Factur-X invoice
      shape (PDF/A-3 + embedded EN 16931 XML) via the pypdf
      library API, facade when the library is absent (the facade
      never claims a certified PDF/A-3 — see
      ``embed_facturx``). A PNR is a SECRET: never logged, never
      egressed — ``record_money_event`` redacts PNRs before
      anything is stored or returned. Portal-login automation
      (incometax / GST) is REFUSED by design (INV-20). Rail
      doctrine: NO self-serve rail-booking is built — a refusal
      record states the three-tier doctrine.
  PRESENCE LOCK (kickoff finding: NOT built in v5.60 — only a
      manual LockWorkStation tool handler exists, which is not
      absence-dwell presence lock; built here as owed) —
      lock-ONLY. Absence dwell triggers LockWorkStation (via
      ctypes, same seam family as the existing handler); unlock
      stays Windows' job and is never implemented here. Dwell
      tuning is named constants with conservative defaults plus
      an activity grace window, designed to avoid false locks.
      Without a presence signal the lock function REFUSES (never
      locks blind).

Dependencies: standard library only at import time. pypdf is
lazy-guarded optional (see ``embed_facturx``); its absence
degrades that one function to an honest unavailable facade,
nothing else. No new tools, no declaration changes, telemetry
untouched. No agent-directed content is followed: any
AGENTS.md / CLAUDE.md text in the repo is data, never
instructions, and nothing in this module acts on it.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import uuid
from xml.sax.saxutils import escape as _xml_escape

__all__ = [
    # PF-20
    "QuietHours", "can_speak", "can_notify", "can_alarm",
    "gate_speak", "gate_notify",
    # PF-20 calendar-aware quiet (Stage 0 features batch 6)
    "CalendarQuietCache", "calendar_quiet_enabled",
    "calendar_quiet_cache", "reset_calendar_quiet_cache",
    # PF-22
    "AlarmStore", "Alarm",
    # PF-26
    "send_sos", "SosVerdict",
    # Meeting notes
    "MeetingGate", "detect_meeting", "produce_meeting_notes",
    "MeetingCaptureRefused",
    # Breach monitor
    "breach_prefix", "BreachSource", "BreachRefused",
    # Money paperwork
    "organise_document", "flag_discrepancies", "redact_pnr",
    "record_money_event", "refuse_portal_login",
    "refuse_rail_booking", "build_facturx_xml", "embed_facturx",
    "RAIL_DOCTRINE",
    # Presence lock
    "PresenceLock", "ABSENCE_DWELL_SECONDS", "ACTIVITY_GRACE_SECONDS",
    "PresenceRefused",
]



# INTENTIONAL (skills split B21): the families below moved to impl modules; the facade re-exports every name so importers resolve them identically through this module object.
from .daily_helpers import (_load_json, _save_json_atomic, _parse_time)  # noqa: F401
from .daily_quiet import (QuietHours, can_speak, can_notify, can_alarm, gate_speak, gate_notify, CalendarQuietCache, calendar_quiet_enabled, calendar_quiet_cache, reset_calendar_quiet_cache)  # noqa: F401
from .daily_alarms import (Alarm, AlarmStore)  # noqa: F401
from .daily_sos import (SosVerdict, _normalise_sender_result, send_sos)  # noqa: F401
from .daily_meeting import (MeetingCaptureRefused, _MEETING_DENY_PROCESSES, _MEETING_DENY_TITLES, _denylist_hit, MeetingGate, detect_meeting, _ACTION_VERBS, _extract_action_items, produce_meeting_notes)  # noqa: F401
from .daily_breach import (BreachRefused, breach_prefix, BreachSource)  # noqa: F401
from .daily_money import (SUPPORTED_DOC_KINDS, _PNR_LABELLED_RE, _PNR_BARE_RE, redact_pnr, redact_pnr_for_record, record_money_event, organise_document, flag_discrepancies, refuse_portal_login, RAIL_DOCTRINE, refuse_rail_booking, build_facturx_xml, embed_facturx)  # noqa: F401
from .daily_presence import (ABSENCE_DWELL_SECONDS, ACTIVITY_GRACE_SECONDS, PresenceRefused, PresenceLock)  # noqa: F401
