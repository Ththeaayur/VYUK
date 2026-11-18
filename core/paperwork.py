"""LA-2 shelf -- Indian paperwork guidance (v5.70, Life Admin
track, plan §5.15): DigiLocker guidance (LA-2.1 half) and
form-filling PREPARATION (LA-2 shelf).

Guidance text + structured checklists ONLY -- preparation FOR
THE HUMAN, never automation into a portal. This module:

  * returns checklists (documents to carry, steps in order,
    things to check before you go) for common Indian paperwork
    tasks, and a generic checklist for anything unlisted;
  * NEVER types, fills, submits, or signs anything in a portal:
    there is no automation function here, no keystroke, no
    form-field entry, no portal login. Steps are written for
    the user to do themselves ("carry", "check", "open", "keep
    ready") -- never "type", "press", or "submit for you";
  * NEVER handles Aadhaar numbers or OTPs: guidance never asks
    for, accepts, or repeats one, and the guided flows break
    (refuse) rather than continue if such a value is supplied
    (see ``check_forbidden_value`` -- the S-6 denylist shape
    for this track);
  * NEVER absorbs portal passwords: see ``refuse_password`` --
    passwords belong in the user's own password manager;
  * makes NO network I/O and sends NO telemetry: pure text
    and data structures, standard library only at import time.

No new tool declarations.
"""

from __future__ import annotations

import re

__all__ = [
    "FORBIDDEN_PATTERNS",
    "check_forbidden_value",
    "digilocker_guidance",
    "preparation_checklist",
    "what_to_carry",
    "refuse_password",
    "refuse_portal_automation",
]

# S-6 denylist shape for this track: Aadhaar numbers (12 digits,
# with or without spaces) and OTP-shaped values (4-8 digits
# offered as a one-time code). Used ONLY to detect-and-refuse --
# never to collect, store, or repeat such a value.
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_OTP_WORD_RE = re.compile(r"\b(otp|one[- ]time password)\b", re.IGNORECASE)

FORBIDDEN_PATTERNS = ("aadhaar_number", "otp_value")


def check_forbidden_value(text) -> dict:
    """Detect-and-REFUSE, never redact-and-continue: if the
    supplied text contains an Aadhaar-shaped number or an OTP
    reference, the guided flow must break with this refusal.
    The offending value itself is never returned or echoed."""
    value = str(text or "")
    if _AADHAAR_RE.search(value):
        return {"ok": False, "refused": True, "pattern": "aadhaar_number",
                "detail": ("refused: an Aadhaar-shaped number was "
                           "supplied. This assistant never handles "
                           "Aadhaar numbers -- remove it and continue "
                           "without it.")}
    if _OTP_WORD_RE.search(value):
        return {"ok": False, "refused": True, "pattern": "otp_value",
                "detail": ("refused: an OTP reference was supplied. "
                           "This assistant never handles OTPs -- "
                           "enter any code yourself, in your own "
                           "browser or app, and never share it here.")}
    return {"ok": True, "refused": False, "pattern": None, "detail": ""}


def digilocker_guidance() -> dict:
    """DigiLocker guidance (LA-2.1 half): what DigiLocker is,
    what to keep ready, and the steps IN ORDER for the user to
    fetch a document themselves. No step here enters anything
    into any portal on the user's behalf."""
    return {
        "service": "DigiLocker",
        "what_it_is": ("DigiLocker is the Government of India's "
                       "digital locker for issued documents "
                       "(driving licence, vehicle registration, "
                       "education certificates, and more). "
                       "Documents fetched here are digitally "
                       "issued copies, accepted wherever the "
                       "original would be."),
        "keep_ready": [
            "Your mobile number linked to Aadhaar (for your own "
            "sign-in on your own device -- never share it here).",
            "The name of the document you need and the issuing "
            "authority (for example, your state transport "
            "department for a driving licence).",
        ],
        "steps_in_order": [
            "Open the DigiLocker app or website yourself, on "
            "your own device.",
            "Sign in with your own credentials, in your own "
            "browser or app.",
            "Search for the issuing authority and the document "
            "you need.",
            "Fetch the document and check that the name, number, "
            "and dates on it match your records.",
            "Download or share it from DigiLocker only to the "
            "office or person who asked for it.",
        ],
        "check_before_you_go": [
            "The document's details match your other records.",
            "The fetched copy is the latest issued version.",
            "You know whether the office wants a digital copy, "
            "a printout, or both.",
        ],
        "never": [
            "Never share an OTP with anyone, including this "
            "assistant.",
            "Never share your DigiLocker password with anyone, "
            "including this assistant.",
            "This assistant never signs in to DigiLocker for you.",
        ],
    }


# Form-filling PREPARATION: document lists + step order for the
# human. Keys are normalised (lowercase, spaces) form names.
_FORMS = {
    "passport": {
        "documents": ["Proof of address", "Proof of date of birth",
                      "Recent photographs as specified on the form",
                      "Old passport, if renewing",
                      "Supporting certificates, if the form asks for any"],
        "steps_in_order": [
            "Read the full form once before writing anything.",
            "Gather every document on the list below.",
            "Fill the form in the order its sections appear.",
            "Check every name, number, and date against the documents.",
            "Keep a copy of the filled form and the receipt.",
        ],
    },
    "pan card": {
        "documents": ["Proof of identity", "Proof of address",
                      "Proof of date of birth",
                      "Recent photograph as specified on the form"],
        "steps_in_order": [
            "Read the full form once before writing anything.",
            "Gather every document on the list below.",
            "Fill the form in the order its sections appear.",
            "Check every name, number, and date against the documents.",
            "Keep a copy of the filled form and the acknowledgement.",
        ],
    },
    "driving licence": {
        "documents": ["Proof of address", "Proof of age",
                      "Learner's licence, if applying for a permanent licence",
                      "Medical certificate, if the form asks for one",
                      "Recent photographs as specified on the form"],
        "steps_in_order": [
            "Read the full form once before writing anything.",
            "Gather every document on the list below.",
            "Fill the form in the order its sections appear.",
            "Check every name, number, and date against the documents.",
            "Book and attend any test or visit the form requires.",
            "Keep a copy of the filled form and the receipt.",
        ],
    },
    "voter id": {
        "documents": ["Proof of identity", "Proof of address",
                      "Proof of age",
                      "Recent photograph as specified on the form"],
        "steps_in_order": [
            "Read the full form once before writing anything.",
            "Gather every document on the list below.",
            "Fill the form in the order its sections appear.",
            "Check every name, number, and date against the documents.",
            "Keep a copy of the filled form and the acknowledgement.",
        ],
    },
}

_GENERIC = {
    "documents": ["Proof of identity", "Proof of address",
                  "Proof of date of birth, if the form asks for it",
                  "Any certificate the form names explicitly"],
    "steps_in_order": [
        "Read the full form once before writing anything.",
        "Gather every document on the list below.",
        "Fill the form in the order its sections appear.",
        "Check every name, number, and date against the documents.",
        "Keep a copy of the filled form and any receipt.",
    ],
}

_CARRY = [
    "Originals of every document on the checklist.",
    "One photocopy set of each document.",
    "A pen, and the filled form if you filled it at home.",
    "The acknowledgement or receipt from any earlier step.",
]


def preparation_checklist(form_name: str) -> dict:
    """Form-filling PREPARATION for one form: its document list
    and step order, for the user to work through themselves.
    Unknown forms get the generic checklist -- never a refusal
    to help prepare, and never automation. Returns
    {form, known, documents, steps_in_order, what_to_carry}."""
    key = str(form_name or "").strip().lower()
    spec = _FORMS.get(key)
    if spec is None:
        return {"form": str(form_name or "").strip(),
                "known": False,
                "documents": list(_GENERIC["documents"]),
                "steps_in_order": list(_GENERIC["steps_in_order"]),
                "what_to_carry": list(_CARRY),
                "note": ("No specific checklist for this form yet; "
                         "use the generic list and read the form "
                         "itself for anything extra it names.")}
    return {"form": key, "known": True,
            "documents": list(spec["documents"]),
            "steps_in_order": list(spec["steps_in_order"]),
            "what_to_carry": list(_CARRY),
            "note": ""}


def what_to_carry(form_name: str = "") -> list[str]:
    """What to carry to the office for a form: the generic carry
    list plus that form's documents, de-duplicated in order.
    Preparation only -- nothing here goes into any portal."""
    checklist = preparation_checklist(form_name)
    seen: set = set()
    combined: list[str] = []
    for item in checklist["documents"] + checklist["what_to_carry"]:
        if item not in seen:
            seen.add(item)
            combined.append(item)
    return combined


def refuse_password() -> dict:
    """Portal passwords are refused by design: the paperwork
    guidance never asks for, accepts, or stores one. Keep
    passwords in your own password manager and sign in
    yourself."""
    return {
        "refused": True,
        "reason": ("portal passwords are refused by design: this "
                   "guidance never asks for, accepts, or stores a "
                   "portal password. Keep passwords in your own "
                   "password manager and sign in yourself in "
                   "your own browser or app."),
    }


def refuse_portal_automation(portal: str) -> dict:
    """Portal automation is REFUSED by design, for ANY portal
    name. Guidance prepares the human; the human does the
    portal steps themselves. No function here fills, submits,
    or signs anything in a portal."""
    name = str(portal or "").strip() or "portal"
    return {
        "refused": True,
        "reason": (f"automation into {name} is refused by design: "
                   "this module only prepares checklists and "
                   "guidance. You do the portal steps yourself, "
                   "in your own browser or app."),
    }
