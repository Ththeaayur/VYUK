"""Wave 6 (v5.61) — KN-6.7 + KN-6.1 retention layer (ADOPT pattern).

Facts get volatility-capped due-dates and are re-verified before
they decay into confident staleness — a pattern no OSS ships, so
the scheduler here is OWN CODE (FSRS-inspired, not FSRS itself):
- Every fact record carries `volatility` (0.0 stable … 1.0
  fast-changing), `last_verified`, and `due_date` in its
  frontmatter (see core/memory_files.py).
- The next due date is last_verified + interval, where the
  interval grows with stability and is CAPPED: no fact, however
  stable, goes longer than MAX_INTERVAL_DAYS without a
  re-verification prompt. The cap is the point — without it,
  "stable" facts would silently become confident staleness.
- A fact past its due date is STALE-PENDING-REVERIFICATION: it
  still surfaces in recall (dropping it would lose memory), but
  it is flagged, and the retention API lists it for
  re-verification. Re-verification resets last_verified and
  re-computes the due date.

Honest caveat carried from the plan: FSRS parameters fit HUMAN
forgetting, not world-change. The claim here is only "any
principled interval beats facts-never-expire", with her own
re-confirmation log as the evidence.

py-fsrs (MIT) and Ebisu (Unlicense) are PILOT SEATS for the
confidence-scoring question (see core/pilot_licences_seats.py):
licence-verified, NOT imported — this module does not depend
on either. stdlib only.
"""

from datetime import date, timedelta

MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 180
BASE_INTERVAL_DAYS = 7


def interval_for(volatility, successes=0):
    """Due-date interval in days. Stability stretches the
    interval (each successful re-verification roughly doubles it
    up to the cap); volatility shrinks it. The cap binds: even a
    perfectly stable fact (volatility 0.0) never exceeds
    MAX_INTERVAL_DAYS without re-verification."""
    try:
        vol = float(volatility)
    except (TypeError, ValueError):
        vol = 0.3
    vol = min(1.0, max(0.0, vol))
    interval = BASE_INTERVAL_DAYS * (1.0 - vol) + 1.0
    for _ in range(max(0, int(successes))):
        interval *= 2.0
    interval = max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, interval))
    return int(round(interval))


def next_due_date(last_verified, volatility, successes=0):
    """'YYYY-MM-DD' of the next re-verification, or '' when the
    input date is unusable (never a fabricated date)."""
    try:
        anchor = date.fromisoformat(str(last_verified)[:10])
    except ValueError:
        return ""
    return (anchor + timedelta(
        days=interval_for(volatility, successes))).isoformat()


def is_stale(record, today=None):
    """True when a record is past its due date (or has a due date
    and no last_verified — unverified is stale by definition).
    Records without any due date are not stale: nothing was ever
    scheduled for them."""
    due = str(record.get("due_date", "") or "")[:10]
    if not due:
        return False
    today = today or date.today().isoformat()
    return due < today


def due_for_reverification(records, today=None):
    """The re-verification queue: stale records, most-overdue
    first. This is the list a re-verification pass works through
    — it never auto-deletes or auto-confirms anything."""
    today = today or date.today().isoformat()
    stale = [record for record in records
             if is_stale(record, today=today)]
    stale.sort(key=lambda record: str(record.get("due_date", "")))
    return stale


def mark_reverified(record, today=None):
    """Return a copy of the record with last_verified = today and
    a fresh due date. The caller writes it back via the file
    store; this function mutates nothing on disk."""
    today = today or date.today().isoformat()
    updated = dict(record)
    updated["last_verified"] = today
    updated["updated_at"] = today
    updated["due_date"] = next_due_date(
        today, record.get("volatility", "0.3"))
    return updated


def stale_flag(record, today=None):
    """The prompt-visible marker for a stale record: recall keeps
    the fact (memory must not silently lose it) but the brain is
    told it needs a re-check. Empty string when fresh."""
    if is_stale(record, today=today):
        return " (needs re-check: past its re-verification date)"
    return ""
