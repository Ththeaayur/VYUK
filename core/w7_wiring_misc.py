# Part of the core/w7_wiring.py split (skills split B13).
# Family: misc.

from __future__ import annotations

from . import daily  # sibling module used by this family (SOS/meeting/breach/money)
from .w7_wiring_daily import daily_store_path  # INTENTIONAL (B13 split):
  # record_money_event_local resolves the store through this function;
  # a function import is state-safe (the override global lives in the
  # daily module, and the function reads it in its defining module).

BREACH_TERMS_GATE = (
    "Breach live checking is GATED on source terms (currently "
    "REFUSED): only the local hash-prefix machinery is wired; "
    "no live breach check is built, named, or contacted here."
)

def send_sos_via(message, channels, sender=None) -> daily.SosVerdict:
    """PF-26 SOS with a caller-supplied sender(channel, message).
    With no sender — or when no channel confirms — the verdict's
    announcement says delivery FAILED and confirmed is False:
    unknown status is announced failure, never success. A real
    sender is owed (SOS_SENDER_OWED) before SOS can ever
    report success."""
    return daily.send_sos(message, channels, sender)

def meeting_notes_for(transcript, meeting_title: str = "",
                      opt_in: bool = False, indicator_on: bool = False,
                      foreground_probe=None):
    """Meeting notes ONLY behind MeetingGate: (notes_dict|None,
    refusal|None). Without opt-in, without a live indicator, or
    against a denylisted foreground, NOTHING is produced and
    the transcript is not processed — no exceptions. The
    transcript source + the live opt-in/indicator grants are
    owed (MEETING_TRANSCRIPT_OWED)."""
    gate = daily.MeetingGate(opt_in=opt_in,
                                  indicator_on=indicator_on,
                                  foreground_probe=foreground_probe)
    return daily.produce_meeting_notes(transcript, gate,
                                            meeting_title=meeting_title)

def detect_meeting_for(calendar_events, mic_active: bool = False):
    """Detect a meeting from caller-supplied calendar events +
    mic state. Mic activity alone never declares a meeting."""
    return daily.detect_meeting(calendar_events,
                                     mic_active=mic_active)

def breach_prefix_for(password: str) -> str:
    """Local k-anonymity prefix ONLY (first 5 hex chars of the
    password's SHA-1, uppercased). Returns the prefix and
    nothing else — never the full hash, never the password.
    This is the whole breach wiring: the live check is GATED
    (see breach_live_check_refused)."""
    return daily.breach_prefix(password)

def breach_live_check_refused(prefix: str = "00000") -> dict:
    """Proof that no live breach check happens here: the
    module's own source refuses every lookup while terms are
    unverified (BreachRefused), and even with verified terms it
    performs no lookup. No source is named, contacted, or
    implied — naming one would be the gated step itself."""
    source = daily.BreachSource(terms_verified=False)
    try:
        source.lookup(prefix)
        performed = True
        detail = "unexpected: lookup did not refuse"
    except daily.BreachRefused as exc:
        performed = False
        detail = str(exc)
    return {"performed": performed, "detail": detail,
            "terms_gate": BREACH_TERMS_GATE}

def record_money_event_local(event: dict, store_path=None) -> dict:
    """Local-only money-paperwork record: PNRs are redacted in
    every string field BEFORE writing (a PNR is a secret — it
    is never logged and never egressed). Returns the stored
    (redacted) record."""
    return daily.record_money_event(
        str(daily_store_path(store_path)), event)

def organise_money_document(kind: str, rows) -> dict:
    """Local-only paperwork organisation: classify rows, total
    amounts, flag discrepancies (total_mismatch, duplicate_row,
    missing_amount, unknown_kind). No tax computation, no
    advice, no filing."""
    return daily.organise_document(kind, rows)

def flag_money_discrepancies(documents: list) -> list[dict]:
    """Cross-document discrepancy FLAGGING only — never tax
    computation, never advice, never filing."""
    return daily.flag_discrepancies(documents)

def refuse_portal_login_for(portal: str) -> dict:
    """Portal-login automation is REFUSED BY DESIGN (INV-20):
    always a refusal, never a login. Exposed so callers get the
    refusal instead of building the automation."""
    return daily.refuse_portal_login(portal)

def refuse_rail_booking_for(details: str = "") -> dict:
    """Self-serve rail booking is REFUSED BY DESIGN: always a
    refusal stating the three-tier doctrine, with any PNR in
    the details redacted. Never a booking."""
    return daily.refuse_rail_booking(details)
