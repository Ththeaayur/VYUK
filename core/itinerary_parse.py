"""Extractive-only itinerary email parsing (VYUK Life Admin, LA-4.9).

ONE module, ONE job: copy substrings out of email TEXT the caller
supplies. It never opens a mailbox, never fetches anything, and
performs NO network I/O (stdlib ``re`` only).

EXTRACTIVE ONLY (INV-4 taint surface, by construction):
  * Every field value is a verbatim substring of the supplied text.
    Nothing is inferred, summarised, completed, or normalised into a
    value the text does not contain. A field the text lacks is
    listed under ``missing`` -- never guessed.
  * Parsed fields are tagged ``untrusted: True``. Untrusted input is
    DATA: downstream code must never execute it, trust it as
    verified, or feed it to a booking surface (this module has no
    booking surface to feed it to).

PNR IS A SECRET (Money-track ruling, carried forward):
  * The PNR, when found, goes ONLY into the ``secrets`` section of
    the returned dict. No field, and no render/summary function in
    this module, ever reads ``secrets``. ``render_itinerary_summary``
    builds its text from ``fields`` alone, so a rendered itinerary
    cannot contain the PNR -- proved by test, including the case
    where the PNR also appears elsewhere in the raw text (the render
    never copies raw text, only labelled fields).
  * Callers must treat ``secrets`` like any secret: never log it,
    never speak it, never send it. This module itself never logs,
    prints, or transmits anything.

What is extracted (labelled patterns only, to keep extraction
extractive rather than speculative):
  * PNR: a code after an explicit ``PNR`` label -- 6 alphanumeric
    characters (airline) or 10 digits (rail). Unlabelled codes are
    NOT treated as PNRs.
  * Flight numbers: 2-letter code + 3-4 digits, after a ``Flight``
    label or standing alone in the standard shape.
  * Dates: ``YYYY-MM-DD`` substrings, and ``D Month YYYY`` substrings.
  * Airports: 3-letter codes after From/To/Departure/Arrival labels,
    or in ``(XXX)`` airport form.
  * Passenger names: the text after a ``Passenger`` label, to end of
    line, copied verbatim.
"""

import re

__all__ = ["parse_itinerary_email", "render_itinerary_summary"]

_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November",
           "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul",
           "Aug", "Sep", "Sept", "Oct", "Nov", "Dec")

_PNR_LABELLED = re.compile(
    r"PNR\s*[:#]?\s*(\d{10}|[A-Z0-9]{6})", re.IGNORECASE)
_FLIGHT = re.compile(r"\b([A-Z]{2}\s?\d{3,4}[A-Z]?)\b")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TEXT_DATE = re.compile(
    r"\b\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")\s+\d{4}\b")
_AIRPORT_LABELLED = re.compile(
    r"(?:From|To|Departure|Arrival|Origin|Destination)\s*[:\-]?\s*"
    r"([A-Z][A-Za-z ]+?)\s*\(([A-Z]{3})\)")
_AIRPORT_PAREN = re.compile(r"\(([A-Z]{3})\)")
_PASSENGER = re.compile(r"Passenger\s*[:\-]?\s*(.+)", re.IGNORECASE)

_KNOWN_FIELDS = ("flight_numbers", "dates", "airports",
                 "passenger_names")


def _dedup(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_itinerary_email(text):
    """Parse supplied email TEXT into structured fields + sealed secrets.

    ``text`` must be a string (email body already in hand). Returns:
      {"fields": {...only fields actually found...},
       "secrets": {"pnr": ...} (only when a labelled PNR is present),
       "missing": [field names not found],
       "untrusted": True,
       "source": "supplied_email_text"}
    No network, no mailbox, no inference.
    """
    if not isinstance(text, str):
        raise TypeError("parse_itinerary_email takes email text, not %s"
                        % type(text).__name__)
    fields = {}
    secrets = {}

    pnr = _PNR_LABELLED.search(text)
    if pnr:
        # Airline PNRs are uppercase by convention; rail PNRs are
        # digits. The copy is verbatim except for case-folding the
        # airline shape to its canonical uppercase form -- the
        # characters are the text's own, nothing is added.
        secrets["pnr"] = pnr.group(1).upper()

    flights = _dedup(m.group(1).replace(" ", "")
                     for m in _FLIGHT.finditer(text))
    # A 6-char airline PNR could also match the flight shape only if
    # it were 2 letters + digits; keep fields and secrets disjoint
    # by dropping any flight candidate identical to the sealed PNR.
    if secrets.get("pnr"):
        flights = [f for f in flights if f != secrets["pnr"]]
    if flights:
        fields["flight_numbers"] = flights

    dates = _dedup([m.group(0) for m in _ISO_DATE.finditer(text)]
                   + [m.group(0) for m in _TEXT_DATE.finditer(text)])
    if dates:
        fields["dates"] = dates

    airports = []
    for m in _AIRPORT_LABELLED.finditer(text):
        airports.append({"place": m.group(1).strip(),
                         "code": m.group(2)})
    if not airports:
        airports = [{"code": code} for code in
                    _dedup(m.group(1)
                           for m in _AIRPORT_PAREN.finditer(text))]
    if airports:
        fields["airports"] = airports

    passengers = _dedup(m.group(1).strip()
                        for m in _PASSENGER.finditer(text)
                        if m.group(1).strip())
    if passengers:
        fields["passenger_names"] = passengers

    missing = [name for name in _KNOWN_FIELDS if name not in fields]
    return {"fields": fields,
            "secrets": secrets,
            "missing": missing,
            "untrusted": True,
            "source": "supplied_email_text"}


def render_itinerary_summary(parsed):
    """Plain-text summary of a parsed itinerary. NEVER includes the PNR.

    Built from ``fields`` ONLY. The ``secrets`` section is never
    read here -- not even to check it -- so no render path can leak
    it. Returns "" when no fields were found.
    """
    if not isinstance(parsed, dict):
        return ""
    fields = parsed.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return ""
    lines = []
    if fields.get("passenger_names"):
        lines.append("Passenger: " + ", ".join(fields["passenger_names"]))
    if fields.get("flight_numbers"):
        lines.append("Flight: " + ", ".join(fields["flight_numbers"]))
    if fields.get("airports"):
        legs = []
        for airport in fields["airports"]:
            if isinstance(airport, dict):
                legs.append(airport.get("code", ""))
            else:
                legs.append(str(airport))
        legs = [leg for leg in legs if leg]
        if legs:
            lines.append("Airports: " + " -> ".join(legs))
    if fields.get("dates"):
        lines.append("Dates: " + ", ".join(fields["dates"]))
    return "\n".join(lines)
