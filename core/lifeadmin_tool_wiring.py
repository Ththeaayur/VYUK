"""v5.71 (Life Admin track) — funnel wiring for the ``lifeadmin`` tool.

Parent-gate ruling (discretion grant, 2026-10-25): WIRE, following
the Health precedent — Life Admin has no existing surface, so
library-only would ship something the user cannot talk to. One
tool, three guidance-only actions; deliberately no booking,
payment, 3-DS, CAPTCHA, OTP, password, Duffel, Practo, or Argos
shape (those are gated, study-only, or never-build).

Character, enforced by construction, not by policy text:
  - She prepares, reads, and guides; the human books. Every
    action returns link-outs, checklists, or an assembled plan —
    never a booking, never an automation.
  - A query carrying an Aadhaar-shaped number or an OTP
    reference is refused outright (paperwork.check_forbidden_value),
    never redacted-and-continued, and the value is never echoed.
  - A query asking for a never-automate shape (IRCTC, Tatkal,
    solver farm, queue-jumping) gets the la6 refusal text
    attached AND only guidance in return — the automation is
    refused, the guidance is still served. There is no code path
    here, or anywhere in this track, that could carry out the
    automation itself.
  - trip_prepare assembles via travel.build_trip, which refuses
    unmatched travellers (booking-for-others) and barred shapes
    in its own inputs by construction.
  - No network I/O in this module: NTES/hotel/flight shapes are
    link-out URL builders, and GraphHopper stays a gated
    request-builder behind a caller key (not wired here).
"""

import re

from . import never_automate_guard as _la6
from . import paperwork as _paper
from . import travel_assist as _travel

_ACTIONS = ("trip_status", "paperwork_guide", "trip_prepare")

# Query keywords that name a never-automate shape -> la6 kind.
# (The structured helpers scan their own inputs too; this layer
# catches the free-text query before any helper sees it.)
_AUTOMATION_KEYWORDS = (
    ("irctc", "irctc"),
    ("tatkal", "tatkal"),
    ("solver farm", "solver_farm"),
    ("captcha solver", "solver_farm"),
    ("solve captcha", "solver_farm"),
    ("bypass captcha", "solver_farm"),
    ("2captcha", "solver_farm"),
    ("anti-captcha", "solver_farm"),
    ("anticaptcha", "solver_farm"),
    ("queue jump", "queue_jumping"),
    ("jump the queue", "queue_jumping"),
    ("skip the queue", "queue_jumping"),
    ("queue-jump", "queue_jumping"),
    ("queue-jumping", "queue_jumping"),
    ("tatkal bot", "queue_jumping"),
    ("bot booking", "queue_jumping"),
)

_TRAIN_RE = re.compile(r"\b(\d{1,5})\b")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _automation_refusal(query):
    """la6 refusal text when the query names a never-automate
    shape, else ''. The text names the class, never a value."""
    low = str(query or "").lower()
    for word, kind in _AUTOMATION_KEYWORDS:
        if word in low:
            return _la6.refusal_text(kind) or ""
    return ""


def _forbidden_check(query):
    """Aadhaar/OTP refusal for the query, or None when clean.
    Never echoes the offending value."""
    check = _paper.check_forbidden_value(query)
    if check.get("refused"):
        return {"ok": False, "refused": True,
                "reason": check.get("detail", "refused")}
    return None


def _trip_status(query):
    train_match = _TRAIN_RE.search(query)
    date_match = _DATE_RE.search(query)
    if train_match and date_match:
        link = _travel.ntes_status_link(
            train_match.group(1), date_match.group(1))
        link["ok"] = True
        link["action"] = "trip_status"
        return link
    return {"ok": True, "action": "trip_status",
            "provider": "NTES (National Train Enquiry System)",
            "url": _travel.NTES_URL,
            "human_steps": [
                "Open the official NTES site (link above).",
                "Choose 'Spot your train'.",
                "Enter the train number and journey date.",
            ],
            "human_books": True,
            "automated": False,
            "note": ("Give me the train number and journey date "
                     "and I will pin the steps for that train.")}


def _paperwork_guide(query):
    guide = _paper.digilocker_guidance()
    checklist = _paper.preparation_checklist(query)
    return {"ok": True, "action": "paperwork_guide",
            "digilocker": guide,
            "checklist": checklist,
            "human_books": True,
            "automated": False}


def _trip_prepare(query):
    trip = _travel.build_trip(title=query or None)
    trip["ok"] = True
    trip["action"] = "trip_prepare"
    trip["note"] = ("Assembled plan only — no booking was made "
                    "and none can be made from here; you book on "
                    "the provider's own surface.")
    return trip


def lifeadmin_tool(args) -> dict:
    """Funnel handler for the ``lifeadmin`` tool."""
    args = args or {}
    action = str(args.get("action", "") or "").strip().lower()
    query = str(args.get("query", "") or "").strip()
    if action not in _ACTIONS:
        return {"ok": False, "action": action, "refused": True,
                "reason": ("unknown lifeadmin action %r; known "
                           "actions are trip_status, paperwork_guide, "
                           "trip_prepare") % action}
    forbidden = _forbidden_check(query)
    if forbidden is not None:
        forbidden["action"] = action
        return forbidden
    try:
        if action == "trip_status":
            result = _trip_status(query)
        elif action == "paperwork_guide":
            result = _paperwork_guide(query)
        else:
            result = _trip_prepare(query)
    except _travel.NeverAutomateRefusal as exc:
        return {"ok": False, "action": action, "refused": True,
                "reason": str(exc)}
    automation = _automation_refusal(query)
    if automation:
        result["automation_refused"] = automation
    return result
