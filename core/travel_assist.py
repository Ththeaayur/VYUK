"""Assisted-manual travel helpers (VYUK Life Admin & Travel, v5.70.1).

ONE module, ONE job: prepare travel lookups the HUMAN then completes.
She prepares and reads; the human books. Link-out + guidance only.

HARD SHAPE RULES (load-bearing, no exceptions):
  * Pure URL / text builders. NO network I/O in this module: it imports
    no urllib.request, no http.client, no socket, no requests, and it
    opens no browser. The functions return URLs and guidance text;
    the human (or a caller outside this module) opens them.
  * NO tool declarations are added by this module. Library only.
  * NO credentials are shipped, defaulted, or read from the
    environment. The GraphHopper pilot takes an API key ONLY from its
    caller, and the pilot itself is GATED (see below).
  * NO telemetry. NO new dependencies (stdlib only).

NTES HONESTY NOTE: the official National Train Enquiry System is a
single-page app (https://enquiry.indianrail.gov.in/ntes/) with no
documented deep-link query for train + date. Inventing ``?train=...``
parameters would be dishonest -- the site would silently ignore
them. So ``ntes_status_link`` returns the official entry URL plus
the validated train number / date and the exact human steps, and
never pretends the parameters are carried in the URL.

NEVER-AUTOMATE BY CONSTRUCTION (LA-6, extending MN-5.14): every
public helper validates its inputs and RAISES ``NeverAutomateRefusal``
on a barred shape instead of building anything useful from it:
  * IRCTC / Tatkal URLs or booking attempts;
  * solver-farm shapes (CAPTCHA-solving services);
  * booking-for-others on a personal ID (a traveller who is not the
    identified user, with no explicit user-identity match);
  * queue-jumping requests as a category;
  * Aadhaar numbers in any travel input (Aadhaar Act s.29 bar);
  * OTP / PIN / CVV / 3-DS passwords anywhere (MN-5.14: never enter,
    read, relay, store, or submit them).
Refusals are by construction: there is no booking function, no
browser-automation function, and no solver call anywhere in this
module to fall back on.

GRAPHHOPPER PILOT (LA-4.5) -- GATED, NOT CLEARED:
  Primary source, GraphHopper pricing page
  (https://www.graphhopper.com/pricing/, footnote 2, read 2026-10-25):
  "The Free Plan is for non-commercial use only." A commercial
  product therefore CANNOT use the free tier. The builder + parser
  below are pilot-only, take an injected fetcher, and ship no key.
  Closing step before any live use: owner decision on a paid plan
  (pricing page lists paid plans from Basic) + the key supplied by
  the caller at runtime. Until then ``graphhopper_pilot_status()``
  reports GATED and ``fetch_route`` refuses to run without an
  explicitly supplied key (a missing key is a refusal, not a
  fallback to any free endpoint).

Trip dicts carry no data-flag field: NO second flag is invented
here, and reusing ``cleared_only`` or ``commercial_verdict`` as one
would mislabel them. The flag's definition is recorded as a STUDY
item for Wave 7 (which owns ambient travel). See the coordinator
report.
"""

import datetime
import re
from urllib.parse import urlencode

__all__ = [
    "NeverAutomateRefusal",
    "ntes_status_link",
    "booking_hotel_link",
    "cleartrip_flight_link",
    "build_trip",
    "graphhopper_pilot_status",
    "build_route_request",
    "parse_route_response",
    "fetch_route",
    "GRAPHHOPPER_VERDICT",
    "GRAPHHOPPER_TERMS_SOURCE",
]

NTES_URL = "https://enquiry.indianrail.gov.in/ntes/"
BOOKING_SEARCH_URL = "https://www.booking.com/searchresults.html"
CLEARTrip_SEARCH_URL = "https://www.cleartrip.com/flights/results"
GRAPHHOPPER_ROUTE_URL = "https://graphhopper.com/api/1/route"

GRAPHHOPPER_TERMS_SOURCE = "https://www.graphhopper.com/pricing/"
GRAPHHOPPER_VERDICT = "GATED_NON_COMMERCIAL_FREE_TIER"

# Aadhaar: 12 digits, optionally grouped 4-4-4 with spaces/hyphens.
# Applied to every free-text travel input. A 10-digit rail PNR does
# NOT match (12 digits required); a 16-digit card number does not
# match either (word boundaries + exactly 12 digits).
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
# Barred credential / secret field names (MN-5.14).
_BARRED_KEYS = ("otp", "pin", "cvv", "cvc", "password", "3ds",
                "3d_secure", "threedsecure", "card_number",
                "cardnumber")
_BARRED_URL_HOSTS = ("irctc.co.in", "irctc.com")
_BARRED_WORDS = ("irctc", "tatkal")
_SOLVER_WORDS = ("captcha solver", "captcha-solving", "solver farm",
                 "solve captcha", "bypass captcha", "2captcha",
                 "anti-captcha", "anticaptcha")
_QUEUE_JUMP_WORDS = ("queue jump", "jump the queue", "skip the queue",
                     "queue-jump", "queue-jumping", "bot booking",
                     "auto book tatkal", "tatkal bot")


class NeverAutomateRefusal(ValueError):
    """Raised by construction on any never-automate shape (LA-6)."""


def _normalise_text(value):
    if not isinstance(value, str):
        raise NeverAutomateRefusal("travel input must be text, got %r"
                                   % type(value).__name__)
    return value.strip()


def _scan_text(text, where):
    """Refuse barred shapes found in one free-text input."""
    low = text.lower()
    if _AADHAAR_RE.search(text):
        raise NeverAutomateRefusal(
            "Aadhaar number detected in %s: refused (Aadhaar Act bar)" % where)
    for host in _BARRED_URL_HOSTS:
        if host in low:
            raise NeverAutomateRefusal(
                "IRCTC URL/shape in %s: refused, no IRCTC automation" % where)
    for word in _BARRED_WORDS:
        if word in low:
            raise NeverAutomateRefusal(
                "barred booking shape %r in %s: refused" % (word, where))
    for word in _SOLVER_WORDS:
        if word in low:
            raise NeverAutomateRefusal(
                "solver-farm shape %r in %s: refused" % (word, where))
    for word in _QUEUE_JUMP_WORDS:
        if word in low:
            raise NeverAutomateRefusal(
                "queue-jumping request %r in %s: refused" % (word, where))


def _scan_mapping(mapping, where):
    """Refuse barred keys / barred text anywhere in a mapping."""
    if not isinstance(mapping, dict):
        raise NeverAutomateRefusal("%s must be a mapping" % where)
    for key, value in mapping.items():
        if isinstance(key, str) and key.strip().lower() in _BARRED_KEYS:
            raise NeverAutomateRefusal(
                "barred credential field %r in %s: never entered, read, "
                "relayed, stored, or submitted (MN-5.14)" % (key, where))
        if isinstance(value, str):
            _scan_text(value, "%s[%r]" % (where, key))
        elif isinstance(value, dict):
            _scan_mapping(value, "%s[%r]" % (where, key))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    _scan_text(item, "%s[%r][%d]" % (where, key, i))
                elif isinstance(item, dict):
                    _scan_mapping(item, "%s[%r][%d]" % (where, key, i))


def _valid_date(text, where):
    text = _normalise_text(text)
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        raise NeverAutomateRefusal(
            "%s must be a real YYYY-MM-DD date, got %r" % (where, text))
    return text


def _valid_count(value, where, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int):
        raise NeverAutomateRefusal("%s must be an integer" % where)
    if value < minimum:
        raise NeverAutomateRefusal(
            "%s must be >= %d, got %d" % (where, minimum, value))
    return value


# ---------------------------------------------------------------------------
# Link-out builders: the human opens the URL and books. Nothing here
# books, logs in, or fills a form.
# ---------------------------------------------------------------------------

def ntes_status_link(train_number, date, station=None):
    """Official NTES status link-out for one train on one date.

    Returns a dict with the official entry URL, the validated train
    number / date, and the human steps. The train/date are NOT
    embedded in the URL: NTES publishes no deep-link for them, and
    invented parameters would be silently ignored (see module note).
    """
    train = _normalise_text(train_number)
    if not re.fullmatch(r"\d{1,5}", train):
        raise NeverAutomateRefusal(
            "train number must be 1-5 digits, got %r" % train_number)
    day = _valid_date(date, "date")
    steps = ["Open the official NTES site (link below).",
             "Choose 'Spot your train'.",
             "Enter train number %s." % train,
             "Enter the journey date %s." % day]
    if station is not None:
        station = _normalise_text(station)
        if not station:
            raise NeverAutomateRefusal("station must not be empty")
        _scan_text(station, "station")
        steps.append("Enter journey station %s." % station)
    return {"provider": "NTES (National Train Enquiry System)",
            "url": NTES_URL,
            "train_number": train,
            "date": day,
            "station": station,
            "human_steps": steps,
            "human_books": True,
            "automated": False}


def booking_hotel_link(destination, checkin, checkout, adults=2, rooms=1):
    """Booking.com hotel search link-out. Human books on the site."""
    dest = _normalise_text(destination)
    if not dest:
        raise NeverAutomateRefusal("destination must not be empty")
    _scan_text(dest, "destination")
    cin = _valid_date(checkin, "checkin")
    cout = _valid_date(checkout, "checkout")
    if cout <= cin:
        raise NeverAutomateRefusal(
            "checkout (%s) must be after checkin (%s)" % (cout, cin))
    adults = _valid_count(adults, "adults")
    rooms = _valid_count(rooms, "rooms")
    params = {"ss": dest, "checkin": cin, "checkout": cout,
              "group_adults": adults, "no_rooms": rooms}
    return {"provider": "Booking.com",
            "url": BOOKING_SEARCH_URL + "?" + urlencode(params),
            "destination": dest, "checkin": cin, "checkout": cout,
            "adults": adults, "rooms": rooms,
            "human_books": True, "automated": False}


def cleartrip_flight_link(origin, destination, depart, adults=1,
                          return_date=None):
    """Cleartrip-style flight search link-out. Human books on the site."""
    org = _normalise_text(origin).upper()
    dst = _normalise_text(destination).upper()
    for code, where in ((org, "origin"), (dst, "destination")):
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise NeverAutomateRefusal(
                "%s must be a 3-letter airport code, got %r" % (where, code))
    if org == dst:
        raise NeverAutomateRefusal("origin and destination must differ")
    dep = _valid_date(depart, "depart")
    adults = _valid_count(adults, "adults")
    params = {"from": org, "to": dst, "depart": dep, "adults": adults}
    if return_date is not None:
        ret = _valid_date(return_date, "return_date")
        if ret < dep:
            raise NeverAutomateRefusal(
                "return_date (%s) must not precede depart (%s)" % (ret, dep))
        params["return"] = ret
    return {"provider": "Cleartrip",
            "url": CLEARTrip_SEARCH_URL + "?" + urlencode(params),
            "origin": org, "destination": dst, "depart": dep,
            "return_date": params.get("return"),
            "adults": adults, "human_books": True, "automated": False}


# ---------------------------------------------------------------------------
# Trip assembly: parsed itinerary + user-supplied inputs only.
# ---------------------------------------------------------------------------

def build_trip(parsed_itinerary=None, dates=None, travellers=None,
              user_identity=None, title=None, notes=None):
    """Assemble a trip dict. No booking, no automation, no flag invention.

    ``parsed_itinerary`` is the output of itinerary_parse
    (extractive only, PNR sealed in its ``secrets`` section -- that
    section is NEVER copied into the trip dict). ``travellers`` must
    all match ``user_identity``: booking-for-others on a personal ID
    is refused by construction, so an unmatched traveller, or
    travellers with no user identity to match against, raises.
    """
    trip = {"title": None, "legs": [], "dates": {},
            "travellers": [], "notes": None, "human_books": True,
            "automated": False}
    if title is not None:
        title = _normalise_text(title)
        if not title:
            raise NeverAutomateRefusal("title must not be empty")
        _scan_text(title, "title")
        trip["title"] = title
    if notes is not None:
        notes = _normalise_text(notes)
        _scan_text(notes, "notes")
        trip["notes"] = notes
    if dates is not None:
        if not isinstance(dates, dict):
            raise NeverAutomateRefusal("dates must be a mapping")
        _scan_mapping(dates, "dates")
        cleaned = {}
        for key in ("start", "end"):
            if key in dates and dates[key] is not None:
                cleaned[key] = _valid_date(dates[key], "dates[%r]" % key)
        trip["dates"] = cleaned
    if trip["dates"].get("start") and trip["dates"].get("end"):
        if trip["dates"]["end"] < trip["dates"]["start"]:
            raise NeverAutomateRefusal("trip end must not precede start")
    if travellers is not None:
        if not isinstance(travellers, (list, tuple)) or not travellers:
            raise NeverAutomateRefusal(
                "travellers must be a non-empty list")
        if user_identity is None or not _normalise_text(user_identity):
            raise NeverAutomateRefusal(
                "travellers supplied without a user identity to match "
                "against: booking-for-others refused by construction")
        user = _normalise_text(user_identity).lower()
        for traveller in travellers:
            name = _normalise_text(traveller)
            if not name:
                raise NeverAutomateRefusal("traveller name must not be empty")
            _scan_text(name, "travellers")
            if name.lower() != user:
                raise NeverAutomateRefusal(
                    "traveller %r does not match user identity %r: "
                    "booking-for-others on a personal ID refused"
                    % (name, _normalise_text(user_identity)))
            trip["travellers"].append(name)
    if parsed_itinerary is not None:
        if not isinstance(parsed_itinerary, dict):
            raise NeverAutomateRefusal("parsed_itinerary must be a mapping")
        # Only the public, extractive fields are assembled. The
        # ``secrets`` section (PNR) is deliberately never read here.
        fields = parsed_itinerary.get("fields") or {}
        if not isinstance(fields, dict):
            raise NeverAutomateRefusal("parsed_itinerary fields must map")
        _scan_mapping(fields, "parsed_itinerary.fields")
        trip["legs"] = [{"kind": "parsed_itinerary",
                         "fields": dict(fields)}]
        if parsed_itinerary.get("untrusted") is not True:
            raise NeverAutomateRefusal(
                "parsed itinerary lacks the untrusted marker: refused")
    return trip


# ---------------------------------------------------------------------------
# GraphHopper PILOT half (LA-4.5): request builder + response parser.
# Injected fetcher only; no key shipped; pilot GATED (module note).
# ---------------------------------------------------------------------------

def graphhopper_pilot_status():
    """Pilot gate record. GATED until the paid-plan decision is made."""
    return {"pilot": "graphhopper_route",
            "status": "GATED",
            "verdict": GRAPHHOPPER_VERDICT,
            "terms_source": GRAPHHOPPER_TERMS_SOURCE,
            "terms_quote": "The Free Plan is for non-commercial use only.",
            "closing_step": "Owner decision on a paid GraphHopper plan "
                            "(see pricing page) + API key supplied by "
                            "the caller at runtime. No live use before "
                            "both. Free-tier use in this commercial "
                            "product is refused.",
            "live_use_permitted": False}


def build_route_request(origin, destination, profile="car", api_key=None):
    """Build a GraphHopper route request dict. No network, no key default.

    ``origin`` / ``destination`` are (latitude, longitude) pairs.
    ``api_key`` must come from the caller; when absent the request
    is still built (for dry-run / test) but flagged
    ``key_supplied=False`` and ``fetch_route`` will refuse it.
    """
    for point, where in ((origin, "origin"), (destination, "destination")):
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(v, (int, float)) for v in point)):
            raise NeverAutomateRefusal(
                "%s must be a (latitude, longitude) pair" % where)
        lat, lon = point
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise NeverAutomateRefusal(
                "%s out of range: %r" % (where, point))
    if profile not in ("car", "foot", "bike"):
        raise NeverAutomateRefusal(
            "profile must be car, foot, or bike, got %r" % profile)
    if api_key is not None and not isinstance(api_key, str):
        raise NeverAutomateRefusal("api_key must be text")
    if api_key is not None and not api_key.strip():
        raise NeverAutomateRefusal("api_key must not be blank")
    return {"url": GRAPHHOPPER_ROUTE_URL,
            "params": {"point": ["%s,%s" % (origin[0], origin[1]),
                                 "%s,%s" % (destination[0], destination[1])],
                       "profile": profile,
                       "points_encoded": True},
            "api_key_supplied": api_key is not None,
            "api_key": api_key,
            "pilot_status": "GATED",
            "pilot_terms": GRAPHHOPPER_TERMS_SOURCE}


def parse_route_response(payload):
    """Parse a GraphHopper route payload. Copies values, infers nothing.

    Returns an ok record with distance / time / encoded points copied
    verbatim from paths[0], or an error record when the shape is not
    a usable route. Never invents a route.
    """
    if not isinstance(payload, dict):
        return {"status": "error",
                "reason": "route payload must be a mapping"}
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        return {"status": "error",
                "reason": "route payload carries no paths",
                "hint": payload.get("message")}
    first = paths[0]
    if not isinstance(first, dict):
        return {"status": "error", "reason": "first path is not a mapping"}
    if "distance" not in first or "time" not in first:
        return {"status": "error",
                "reason": "first path lacks distance/time"}
    return {"status": "ok",
            "distance_m": first["distance"],
            "time_ms": first["time"],
            "points_encoded": first.get("points"),
            "points": first.get("points"),
            "transfers": first.get("transfers")}


def fetch_route(fetcher, request):
    """Run a built route request through the caller's injected fetcher.

    Refuses when the pilot gate is unmet: no fetcher, or no caller
    key. The fetcher owns transport; this module never touches the
    network and never falls back to a keyless / free endpoint.
    """
    if fetcher is None:
        return {"status": "unavailable",
                "reason": "no fetcher supplied; pilot is GATED and "
                          "no live call is made"}
    if not isinstance(request, dict) or not request.get("api_key_supplied"):
        return {"status": "refused",
                "reason": "GraphHopper pilot GATED: caller API key "
                          "required (paid plan); free tier is "
                          "non-commercial only"}
    payload = fetcher(request)
    return parse_route_response(payload)
