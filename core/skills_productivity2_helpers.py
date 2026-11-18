import datetime
import time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    """Epoch seconds — the stopwatch's clock (a named indirection
    so tests can drive it deterministically)."""
    return time.time()


def _fmt_duration(seconds: float) -> str:
    """Whole-second plain-words duration: '58 seconds',
    '3 minutes 12 seconds', '1 hour 2 minutes 5 seconds'."""
    total = int(round(seconds))
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


def _fmt_clock(moment: datetime.datetime) -> str:
    return moment.strftime("%H:%M")


def _parse_iso_date(text: str):
    """(date, None) or (None, error string) — ISO dates, with a
    full ISO datetime accepted by taking its date part."""
    raw = str(text or "").strip()
    if not raw:
        return None, "That needs a date, in ISO form like 2026-10-21."
    try:
        return datetime.date.fromisoformat(raw), None
    except ValueError:
        pass
    try:
        return datetime.datetime.fromisoformat(
            raw.replace("Z", "+00:00")).date(), None
    except ValueError:
        return None, (f"'{raw}' isn't a date I can read — use ISO "
                      "form, like 2026-10-21.")
