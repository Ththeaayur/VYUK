"""Wave 10 (v5.65) Leaf 2 — PF-19 Terms + Privacy acceptance (STAGED, unwired).

Plain-language Terms and Privacy text to the premium copy
standard: short, readable, no legalese padding. Includes the line
that this is product terms, not legal advice.

Acceptance is an explicit user action, never auto-recorded:
- Nothing in this module records acceptance on import, on read,
  or as a side effect of ``may_proceed``. Only a caller acting on
  the user's explicit Accept control calls ``record_acceptance``.
- The record lives in its own JSON file under data/
  (``terms_acceptance_v565.json``), carrying the terms version +
  timestamp, retrievable with ``get_acceptance``.
- ``may_proceed`` is False until a valid record for the CURRENT
  terms version exists — first run cannot proceed past the gate,
  and a future terms revision re-gates (old-version records do
  not count).

Stdlib only. ``TERMS_VERSION`` is the TERMS document's own
version ("1.0"); it is not the product version, which stays
5.64.1 and is untouched by this module.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

TERMS_VERSION = "1.0"
ACCEPTANCE_FILENAME = "terms_acceptance_v565.json"

TERMS_TEXT = """VYUK — Terms of Use

Welcome to VYUK, your voice assistant. These terms explain, in plain
words, what you can expect from VYUK and what we expect from you.

1. What VYUK is. VYUK is a voice assistant that runs on your computer.
It listens when you ask it to, speaks back, and can carry out tasks you
approve — opening apps, finding information, and controlling parts of
your computer that you allow it to touch.

2. Your responsibility. You decide what VYUK may do. Some actions need
your confirmation first, and some things VYUK will refuse — for example,
anything that would harm your computer, break the law, or act on
someone else's private information without their permission. If you are
unsure whether VYUK should do something, ask it to explain first.

3. Availability. VYUK depends on your computer, your microphone and
speakers, your internet connection, and third-party services it talks
to. Any of those can fail, and when they do, VYUK will say so honestly
rather than pretend it succeeded.

4. Your content stays yours. Anything you say, type, or create with
VYUK belongs to you. VYUK does not sell your content.

5. Changes. If these terms change, you will be asked to accept the new
version before continuing. The version you accepted is recorded with
the date, so there is never any doubt about which terms apply to you.

This is product terms, not legal advice. If you need legal advice
about how these terms apply to you, please speak to a lawyer.
"""

PRIVACY_TEXT = """VYUK — Privacy Notice

Short version: VYUK remembers what it needs to help you, keeps it on
your computer where possible, and never sells it.

1. What VYUK stores. Your settings, your memories and notes, and a log
of milestones (firsts, like your first launch). Voice and text you send
to a cloud brain are handled by that brain's service while it answers
you; VYUK does not keep a second copy beyond what it needs to show you
your conversation.

2. Where it lives. Settings, memories and milestones live in VYUK's
data folder on your computer. You can export them all in one bundle
and take them with you, and you can ask VYUK to forget things.

3. What VYUK never does. VYUK never sells your data, never shares it
with advertisers, and never sends anything anywhere until the action
that sends it has your approval where approval is required.

4. Microphone and screen. VYUK only listens when it is listening for
you, and only looks at your screen when a task you asked for needs it.
It will tell you when it is listening.

5. Questions. If anything here is unclear, ask VYUK to explain it in
plain words, or check the full terms above.

This is product terms, not legal advice.
"""


def acceptance_path(data_dir: str) -> str:
    """Path of the acceptance record file inside a data/ directory."""
    return os.path.join(str(data_dir), ACCEPTANCE_FILENAME)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_acceptance(data_dir: str, accepted_by: str = "user") -> dict:
    """Record an EXPLICIT acceptance. Call only from the Accept control.

    Overwrites any previous record (re-acceptance after a terms
    revision is a fresh record). Returns the record written.
    Raises ValueError for a blank ``accepted_by``.
    """
    if not str(accepted_by or "").strip():
        raise ValueError("accepted_by must name who accepted")
    record = {
        "terms_version": TERMS_VERSION,
        "accepted_at": _utcnow(),
        "accepted_by": str(accepted_by),
        "method": "explicit-accept",
    }
    path = acceptance_path(data_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return record


def get_acceptance(data_dir: str) -> dict | None:
    """Return the stored acceptance record, or None if none/invalid.

    A malformed file, a non-object, or a record missing its version
    or timestamp reads as no acceptance — never as a pass. Reading
    never writes and never records anything.
    """
    path = acceptance_path(data_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if not record.get("terms_version") or not record.get("accepted_at"):
        return None
    return record


def may_proceed(data_dir: str) -> bool:
    """Gate predicate: True only with a valid CURRENT-version record.

    False on first run (no record), on a malformed record, and on a
    record for an older terms version. This function never writes.
    """
    record = get_acceptance(data_dir)
    if record is None:
        return False
    return record.get("terms_version") == TERMS_VERSION
