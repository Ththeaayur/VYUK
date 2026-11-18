"""WhatsApp send via the official wa.me Click to Chat link, with the
owner sending by voice (SOCIAL — Phase 1, approved design).

VYUK never sends anything. It prepares everything — resolves the
recipient from contact cards (never guesses), drafts the exact
text, shows the exact text and the exact recipient for approval —
then opens the platform's own official link to the right chat,
verifies the conversation really is the right person's (and the
pre-filled text really is the approved text), and the OWNER sends
it by voice: he says "press Enter" and Windows Voice Access (the
OS's own accessibility layer) presses Enter. VYUK types nothing
and presses nothing. The send is the owner's action, performed by
the OS accessibility layer at his spoken command.

Narrow re-ruling D-06 (approved 2026-11-13 ~01:34 IST), cited in
full: "SO-3.1 still blocks automated WhatsApp sending. The wa.me
Click to Chat route — VYUK prepares, the owner sends, VYUK types
nothing — is outside the block's scope, because nothing is
automated." The Voice Access refinement (accepted 2026-11-13
~01:52 IST) sits inside it: the send is the owner's action via
the OS accessibility layer, so nothing is automated by VYUK.
Fallback order if the Voice Access field check fails: wa.me +
Enter (VYUK presses Enter — smallest automation, same ToS letter
as the desktop driver), then one-tap wa.me (the floor) — the
owner's call at that point.

Actions (one tool, one platform — WhatsApp first):
  prepare — resolve the recipient + draft the exact text. Returns
    the draft (draft_id, recipient, number, link) for the confirm
    gate. A missing number or an ambiguous name refuses, plainly.
  open — re-validates the draft (the text must hash-match the
    approved draft), opens the wa.me link, and runs the guard: the
    conversation header and the pre-filled text are read back and
    compared to the resolved recipient and the approved text. Both
    match -> "Ready — say 'press Enter' to send". Either fails ->
    `failed`, "don't send", nothing typed anywhere, and NO fallback
    to typing.
  verify — reads the thread back after the send. `done` only when
    the message is seen in the thread AND the send was confirmed
    (the owner said "press Enter" while a send was pending, or the
    owner tapped send on the one-tap path). Anything less is
    `unverified` with the reason, never "sent" on a guess.
  status / events — reads: the pending send (if any), the Voice
    Access state, and recent receipts.

Receipts only (F-07): the ledger (the shared hash chain, via
record_family.append_event) stores who, when, WhatsApp, and the
outcome — NEVER the message text. The draft text exists only in
the confirm dialog, transiently in the link URL, and transiently
within one call; the pending-draft state file holds only a SHA-256
of the text (so open/verify can prove the text matches the
approved draft without storing it). The funnel's own audit log
hashes any argument named "text" (audit.redact_args), so the
parameter is deliberately named `text`.

Voice Access coordination: the tool reports whether Voice Access
is on (registry state, Windows only — see _voice_access_status)
and guides the owner when it is not. While a send is pending,
VYUK must ignore "press Enter" — both VYUK and Voice Access
listen, and the phrase belongs to Voice Access. The voice loop
calls should_ignore_utterance() before dispatching a turn; when
it answers True, note_press_enter_heard() records the owner's
send confirmation and the turn is consumed, not treated as a
VYUK command.

Own code, stdlib only. No new dependency, no new provider, no
paid call, no FatihMakes code (CC BY-NC 4.0, non-commercial —
cannot be adopted); the only thing taken from Fatih is the idea
of honest outcomes (done / unverified / failed) and the guard
idea, which are ideas, not code.

Field check owed (cannot be done from the lab): on his Windows
machine, with real WhatsApp — a real message to a real person,
the guard proven on the right conversation, a deliberate
wrong-conversation test refused, and the Voice Access step proven
(the owner says "press Enter" and the message sends).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLATFORM = "whatsapp"
_ACTIONS = ("prepare", "open", "verify", "status", "events")

_MAX_TEXT_CHARS = 4000
_MAX_NAME_CHARS = 200
_DRAFT_TTL_SECONDS = 30 * 60  # a prepared draft expires after 30 minutes

_STATE_FILE_NAME = "social_send_state.json"
_RECEIPT_KIND = "social_send_receipt"
_RECEIPT_LIMIT = 20

# "press Enter" and its near-variants — the phrases that belong to
# Windows Voice Access while a send is pending, not to VYUK.
_PRESS_ENTER_PHRASES = (
    "press enter",
    "press the enter key",
    "enter",
    "press return",
    "press the return key",
)

# Voice Access state, from the registry (Windows only). The
# AccessibilityTemp signal is forum-sourced (ProcMon diagnosis,
# ElevenForum, Feb 2026 — the key SystemSettings.exe reads when
# toggling Voice Access), NOT Microsoft-documented — so an absent
# or unreadable value answers "unknown", never a guessed on/off.
_VA_TEMP_KEY = (
    r"Software\Microsoft\Windows NT\CurrentVersion\AccessibilityTemp"
)
_VA_TEMP_VALUE = "voiceaccess"
_VA_ON = 3
_VA_OFF = 1
_VA_CONFIG_KEY = (
    r"Software\Microsoft\Windows NT\CurrentVersion\Accessibility"
)
_VA_CONFIG_VALUE = "Configuration"

# Seams (module-level overridables, following the tree's seam
# discipline — cf. connector_sync.sync_state_path). Tests replace
# these; production code never touches them.
screen_reader = None       # callable() -> str | None (screen text)
registry_reader = None     # callable(key, value) -> int | str | None
url_opener = None          # callable(url) -> bool


def _data_dir() -> Path:
    from .config import DATA_DIR

    return Path(DATA_DIR)


def state_path() -> Path:
    """The pending-draft state file. Overridable in tests by
    assigning social_send.state_path = lambda: <tmp path>."""
    return _data_dir() / _STATE_FILE_NAME


# ---------------------------------------------------------------------------
# Pure helpers — phone numbers, links, hashes, the guard
# ---------------------------------------------------------------------------

def normalize_number(raw) -> str | None:
    """Digits-only international number (no '+'), or None when the
    input is not a phone number: 7–15 digits after stripping
    spaces, dashes, parentheses and dots. A leading '+' is allowed
    and dropped (wa.me takes digits only)."""
    if raw is None:
        return None
    text = re.sub(r"[\s\-().]", "", str(raw).strip())
    if text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        return None
    if not 7 <= len(text) <= 15:
        return None
    return text


def looks_like_number(raw) -> bool:
    """True when the recipient was given as a phone number rather
    than a name: an optional '+' followed by digits, spaces,
    dashes, parentheses or dots — and nothing else."""
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    if text.startswith("+"):
        text = text[1:]
    return bool(re.fullmatch(r"[\d\s\-().]+", text)) and any(
        ch.isdigit() for ch in text)


def build_wa_link(number_digits: str, text: str) -> str:
    """The official Click to Chat link. The text is URL-encoded;
    the link pre-fills the message and never sends it."""
    return ("https://wa.me/%s?text=%s"
            % (number_digits, urllib.parse.quote(str(text), safe="")))


def text_sha(text: str) -> str:
    """SHA-256 of the exact text — the pending draft stores this,
    never the text, so open/verify can prove the text matches the
    approved draft without storing it (the same shape the funnel's
    audit redaction uses for `text` arguments)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _norm(text) -> str:
    """Casefolded, whitespace-collapsed text for comparisons."""
    return " ".join(str(text or "").split()).casefold()


def is_press_enter_phrase(text) -> bool:
    """True when the utterance is a press-Enter phrase — the words
    that belong to Windows Voice Access while a send is pending."""
    return _norm(text) in _PRESS_ENTER_PHRASES


def guard_check(screen_text, recipient_name, number_digits, text):
    """The conversation guard (pure): does the screen read-back
    prove the open conversation is the right person's AND the
    pre-filled text is the approved text?

    Returns (ok, reason). The header proves the conversation when
    it shows the recipient's name OR the resolved number (WhatsApp
    shows the bare number when the number is not in contacts — the
    guard compares the number, which is what was resolved, so it
    still works). The box proves the text when the approved text
    appears, normalised. An empty read-back is a failure, never a
    pass: no eyes means no proof.
    """
    if screen_text is None or not str(screen_text).strip():
        return False, "could_not_read_screen"
    corpus = _norm(screen_text)
    # The header evidence must come from text that is NOT the
    # message itself: the message is removed from the corpus
    # before the header is checked, or the message text mentioning
    # the recipient ("hello Mum") would satisfy the header check
    # for a wrong conversation. (Residual limit, stated honestly:
    # the read-back is the whole screen, so a name visible
    # elsewhere on it — e.g. another row in the chat list —
    # could still satisfy the header check; the box check must
    # also pass, and the field check's deliberate
    # wrong-conversation test must prove the guard refuses in
    # practice.)
    header_corpus = corpus.replace(_norm(text), "")
    # The name must appear as whole words (a bare substring would
    # let "Al" match "Alice"). The number may appear spaced, dashed
    # or parenthesised in the header, so at most one non-digit is
    # allowed between consecutive digits.
    name_ok = False
    if recipient_name:
        name_ok = re.search(
            r"\b" + re.escape(_norm(recipient_name)) + r"\b",
            header_corpus) is not None
    number_ok = False
    if number_digits:
        spaced = r"\D?".join(re.escape(d) for d in number_digits)
        number_ok = re.search(spaced, header_corpus) is not None
    if not (name_ok or number_ok):
        return False, "header_mismatch"
    if _norm(text) not in corpus:
        return False, "box_mismatch"
    return True, "match"


def thread_contains(screen_text, text) -> bool:
    """True when the approved text appears in the thread read-back
    (normalised). Used by verify; an empty read-back is False."""
    if screen_text is None or not str(screen_text).strip():
        return False
    return _norm(text) in _norm(screen_text)


# ---------------------------------------------------------------------------
# Pending-draft state (A1-store: atomic tmp+replace, re-read verified)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    """The pending draft, or {} when there is none / the file is
    missing or corrupt (a corrupt file reads as empty — the draft
    is re-prepared, never trusted on a guess)."""
    try:
        raw = Path(state_path()).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — missing/corrupt reads as empty
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — corrupt JSON reads as empty
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_state(state: dict) -> tuple[bool, str]:
    """Atomic save + re-read verify. Returns (ok, error). The file
    holds NO message text — only the draft_id, recipient, number,
    text hash, mode and flags."""
    try:
        path = Path(state_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
        back = json.loads(path.read_text(encoding="utf-8"))
        if back != state:
            return False, "state re-read mismatch"
        return True, ""
    except Exception as exc:  # noqa: BLE001 — a failed save is reported
        return False, str(exc)


def _clear_state() -> None:
    try:
        Path(state_path()).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — clearing is best-effort
        pass


def _draft_expired(state: dict) -> bool:
    try:
        created = float(state.get("created_ts") or 0)
    except (TypeError, ValueError):
        return True
    return (time.time() - created) > _DRAFT_TTL_SECONDS


# ---------------------------------------------------------------------------
# Contacts — the contact-card store is the contacts source
# ---------------------------------------------------------------------------

def _load_contact_cards():
    """[(person, detail)] from the contact-card store
    (core/skills_admin_schedules._load_contacts — the same store
    contact_card reads and writes). Raises on failure; the caller
    turns that into an honest refusal."""
    from .skills_admin_schedules import _load_contacts

    entries, _broken = _load_contacts()
    return [(str(e.get("person") or ""), str(e.get("detail") or ""))
            for e in entries]


def _phone_from_detail(detail: str) -> str | None:
    """The phone number in one contact detail, or None. Accepts
    the labelled form ('phone: +91 …') and a bare number-shaped
    detail; anything else is not a phone number."""
    text = str(detail or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("phone:"):
        text = text[len("phone:"):].strip()
    return normalize_number(text)


def resolve_recipient(recipient, contacts_loader=None):
    """Resolve the recipient to (display_name, number_digits).

    A number-shaped recipient is used directly (the name is the
    number itself). A name is matched case-insensitively and
    EXACTLY against contact-card persons: zero matches, several
    distinct persons, or several distinct numbers for one person
    all refuse — never guess, never pick the first, never fall
    back to a group. Returns (name, number) or (None, reason).
    """
    given = str(recipient or "").strip()
    if not given:
        return None, "a recipient is needed — a name or a phone number"
    if len(given) > _MAX_NAME_CHARS:
        return None, "that recipient is too long to use"
    if looks_like_number(given):
        number = normalize_number(given)
        if number is None:
            return None, ("that number does not look like a phone "
                          "number (7–15 digits, optional leading +)")
        return given, number
    try:
        cards = contacts_loader() if contacts_loader is not None \
            else _load_contact_cards()
    except Exception as exc:  # noqa: BLE001 — contacts unreadable
        return None, "could not read the contact cards: %s" % exc
    persons: dict[str, str] = {}
    for person, _detail in cards:
        key = person.strip().casefold()
        if key:
            persons.setdefault(key, person.strip())
    if given.casefold() not in persons:
        return None, ("no contact called '%s' — save their number "
                      "first with contact_card" % given)
    name = persons[given.casefold()]
    # Ambiguity is caught by the numbers below: two cards for the
    # same name with different numbers refuse (a name is not a
    # unique key in the contact store).
    numbers = set()
    for person, detail in cards:
        if person.strip().casefold() != given.casefold():
            continue
        number = _phone_from_detail(detail)
        if number is not None:
            numbers.add(number)
    if not numbers:
        return None, ("'%s' has no phone number on file — save one "
                      "first with contact_card ('phone: …')" % name)
    if len(numbers) > 1:
        return None, ("'%s' has more than one phone number on file "
                      "— say which one to use" % name)
    return name, next(iter(numbers))


# ---------------------------------------------------------------------------
# Screen read-back (seam) — the guard's eyes
# ---------------------------------------------------------------------------

def _default_screen_text() -> str | None:
    """The screen as text: the foreground window title + focused
    control (the UIA probe, desktop_snapshot) plus the OCR text
    of a screenshot (the tree's native OCR pipeline). Best-effort
    and never raising: None means "no eyes here" (the lab, a
    headless run, a missing OCR engine) — the guard treats that
    as a failure, never a pass."""
    parts: list[str] = []
    try:
        from .desktop.uia_discover import desktop_snapshot
        snap = desktop_snapshot()
        if isinstance(snap, dict):
            for key in ("fg", "focus"):
                value = snap.get(key)
                if value:
                    parts.append(str(value))
    except Exception:  # noqa: BLE001 — the probe is best-effort
        pass
    try:
        from .desktop import vision_capture as _vc
        image = _vc._grab_image()
        meta = {"screen_width": getattr(image, "width", 0),
                "screen_height": getattr(image, "height", 0)}
        words = _vc._ocr_words_for_image(image, meta)
        if words:
            texts = []
            for word in words:
                if isinstance(word, dict):
                    text = word.get("text") or word.get("word") or ""
                else:
                    text = str(word or "")
                if text.strip():
                    texts.append(text.strip())
            if texts:
                parts.append(" ".join(texts))
    except Exception:  # noqa: BLE001 — OCR is a fallback, not a gate
        pass
    joined = " ".join(p for p in parts if p.strip())
    return joined or None


def read_screen_text(reader=None) -> str | None:
    """The screen as text, via the injected reader (tests) or the
    real acquisition (production). Never raises; None means no
    eyes here."""
    fn = reader if reader is not None else screen_reader
    try:
        if fn is not None:
            return fn()
        return _default_screen_text()
    except Exception:  # noqa: BLE001 — read-back never breaks a call
        return None


# ---------------------------------------------------------------------------
# Voice Access state (Windows only) + pending-send coordination
# ---------------------------------------------------------------------------

def _default_registry_read(key: str, value: str):
    """One registry value (Windows only): int/str, or None when
    the key/value is absent or unreadable. Returns the sentinel
    \"not-windows\" off Windows so the caller can say so honestly."""
    if os.name != "nt":
        return "not-windows"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            data, _kind = winreg.QueryValueEx(handle, value)
            return data
    except Exception:  # noqa: BLE001 — absent/unreadable is None
        return None


def voice_access_status(reader=None) -> dict:
    """Whether Windows Voice Access is on: {"state": "on" | "off" |
    "unknown", "reason": ..., "guidance": ...}.

    The on/off signal is HKCU\\...\\AccessibilityTemp\\voiceaccess
    (3 = ON, 1 = OFF) — forum-sourced via ProcMon, NOT
    Microsoft-documented, so anything else (absent key, another
    value, a non-Windows host) answers "unknown" with the reason,
    never a guessed on/off. The guidance always names the one-time
    setup: Settings → Accessibility → Speech → Voice Access.
    """
    guidance = ("Turn Voice Access on once: Settings → Accessibility "
                "→ Speech → Voice Access. It downloads an on-device "
                "speech model on first run; its commands are English "
                "— say \"press Enter\" in English.")
    read = reader if reader is not None else registry_reader
    try:
        value = read(_VA_TEMP_KEY, _VA_TEMP_VALUE) \
            if read is not None else _default_registry_read(
                _VA_TEMP_KEY, _VA_TEMP_VALUE)
    except Exception as exc:  # noqa: BLE001 — the check never raises
        return {"state": "unknown",
                "reason": "could not read the Voice Access state: %s"
                          % exc,
                "guidance": guidance}
    if value == "not-windows":
        return {"state": "unknown",
                "reason": "not Windows — Voice Access is a Windows "
                          "11 feature, so its state cannot be "
                          "checked here",
                "guidance": guidance}
    if value == _VA_ON:
        return {"state": "on",
                "reason": "Voice Access is on",
                "guidance": ""}
    if value == _VA_OFF:
        return {"state": "off",
                "reason": "Voice Access is off",
                "guidance": guidance}
    return {"state": "unknown",
            "reason": ("no Voice Access state was found in the "
                       "registry — it may never have been turned on"),
            "guidance": guidance}


def is_send_pending(state: dict | None = None) -> bool:
    """True when a WhatsApp send is open and waiting for the
    owner's 'press Enter' — the state in which VYUK must ignore
    that phrase (it belongs to Voice Access)."""
    current = state if state is not None else _load_state()
    if not current or current.get("state") != "opened":
        return False
    return not _draft_expired(current)


def should_ignore_utterance(text, state: dict | None = None) -> bool:
    """True when this utterance is a press-Enter phrase AND a send
    is pending — the voice loop calls this before dispatching a
    turn, and when it answers True the turn is consumed (the
    phrase is recorded as the send confirmation) rather than
    treated as a VYUK command."""
    if not is_press_enter_phrase(text):
        return False
    return is_send_pending(state)


def note_press_enter_heard() -> bool:
    """Record that the owner said 'press Enter' while a send was
    pending — the send confirmation verify() needs. Returns True
    when a pending send was marked, False otherwise."""
    state = _load_state()
    if not is_send_pending(state):
        return False
    state["press_enter_heard"] = True
    state["press_enter_ts"] = time.time()
    ok, _err = _save_state(state)
    return ok


# ---------------------------------------------------------------------------
# Receipts — the shared hash chain, receipts only, never the text
# ---------------------------------------------------------------------------

def _record_receipt(recipient, number, outcome, reason, draft_id):
    """Append one receipt: who, when, WhatsApp, the outcome.
    NEVER the message text. Returns (recorded, error)."""
    try:
        from . import record_family as _rf
        event_id = _rf.append_event(_RECEIPT_KIND, {
            "recipient": recipient,
            "number": number,
            "platform": _PLATFORM,
            "outcome": outcome,
            "reason": reason,
            "draft_id": draft_id,
            "ts": time.time(),
        })
        return bool(event_id), "" if event_id else "receipt not recorded"
    except Exception as exc:  # noqa: BLE001 — the receipt never breaks a call
        return False, str(exc)


def _receipts(limit: int = _RECEIPT_LIMIT) -> list:
    try:
        from . import record_family as _rf
        return _rf.read_events(_RECEIPT_KIND, limit=limit)
    except Exception:  # noqa: BLE001 — reads degrade to []
        return []


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

_TOOL_DESCRIPTION = (
    "WhatsApp send via the official wa.me Click to Chat link, with "
    "the owner sending by voice (SOCIAL — Phase 1, approved "
    "design): prepare resolves the recipient from contact cards "
    "(never guesses — a missing number or an ambiguous name "
    "refuses) and drafts the exact text for approval; open opens "
    "wa.me/<number>?text=... and verifies the conversation header "
    "and the pre-filled text before saying 'Ready — say press "
    "Enter to send'; verify reads the thread back and reports done "
    "only when the message is seen in the thread. VYUK never sends, "
    "types nothing and presses nothing — the owner says 'press "
    "Enter' and Windows Voice Access presses it. Confirm-gated; "
    "receipts only (who, when, WhatsApp, done/unverified/failed — "
    "never the message text). No calls, no groups, no automatic "
    "replies, no Telegram/Messenger/Instagram yet."
)


def _ok(data: dict) -> dict:
    out = {"ok": True}
    out.update(data)
    return out


def _fail(reason: str, **extra) -> dict:
    out = {"ok": False, "reason": reason}
    out.update(extra)
    return out


def social_send(action, recipient="", text="", platform="whatsapp",
                draft_id="", _reader=None, _opener=None,
                _va_reader=None, _contacts_loader=None):
    """The tool. Confirm-gated (the confirm gate asks the human
    before this runs for any action — prepare included: the exact
    text and the exact recipient are approved before anything is
    opened). Returns a dict; never raises.

    The underscore parameters are test seams only (they let the
    prover and the suite inject a screen reader, a URL opener, a
    Voice Access reader and a contact loader); production calls
    pass none of them.
    """
    try:
        return _social_send_impl(
            action, recipient, text, platform, draft_id,
            _reader=_reader, _opener=_opener, _va_reader=_va_reader,
            _contacts_loader=_contacts_loader)
    except Exception as exc:  # noqa: BLE001 — module raises become an error JSON
        return _fail("internal_error: %s" % exc)


def _social_send_impl(action, recipient, text, platform, draft_id,
                      _reader=None, _opener=None, _va_reader=None,
                      _contacts_loader=None):
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        return _fail("unknown action — actions are: "
                     + ", ".join(_ACTIONS))
    if platform not in (None, "", _PLATFORM):
        return _fail("only WhatsApp is available in Phase 1 — "
                     "Telegram, Messenger and Instagram are not built")

    if action == "status":
        return _action_status(_va_reader=_va_reader)

    if action == "events":
        return _ok({"action": "events",
                    "platform": _PLATFORM,
                    "receipts": _receipts(),
                    "receipt_note": "receipts store who, when and "
                                    "the outcome — never the message "
                                    "text"})

    if action == "prepare":
        return _action_prepare(
            recipient, text,
            _contacts_loader=_contacts_loader)

    if action == "open":
        return _action_open(
            draft_id, text,
            _reader=_reader, _opener=_opener, _va_reader=_va_reader)

    if action == "verify":
        return _action_verify(
            draft_id, text,
            _reader=_reader)

    return _fail("unknown action")


def _action_status(_va_reader=None):
    state = _load_state()
    pending = None
    if state and not _draft_expired(state):
        pending = {"draft_id": state.get("draft_id"),
                   "recipient": state.get("recipient"),
                   "number": state.get("number"),
                   "state": state.get("state"),
                   "press_enter_heard": bool(
                       state.get("press_enter_heard")),
                   "send_mode": state.get("send_mode"),
                   "text_sha256": state.get("text_sha256"),
                   "note": "the message text is never stored — "
                           "only its SHA-256"}
    va = voice_access_status(reader=_va_reader)
    return _ok({"action": "status",
                "platform": _PLATFORM,
                "pending": pending,
                "voice_access": va,
                "recent_receipts": len(_receipts(limit=5))})


def _action_prepare(recipient, text, _contacts_loader=None):
    given_text = str(text or "")
    if not given_text.strip():
        return _fail("a message is needed — say the exact text to "
                     "send")
    if len(given_text) > _MAX_TEXT_CHARS:
        return _fail("that message is too long — %d characters "
                     "maximum" % _MAX_TEXT_CHARS)
    name, number_or_reason = resolve_recipient(
        recipient, contacts_loader=_contacts_loader)
    if name is None:
        return _fail(number_or_reason)
    number = number_or_reason
    draft_id = "ss-" + secrets.token_hex(6)
    state = {"draft_id": draft_id,
             "recipient": name,
             "number": number,
             "text_sha256": text_sha(given_text),
             "state": "prepared",
             "press_enter_heard": False,
             "send_mode": None,
             "created_ts": time.time()}
    ok, err = _save_state(state)
    if not ok:
        return _fail("could not save the draft: %s" % err)
    return _ok({"action": "prepare",
                "platform": _PLATFORM,
                "draft_id": draft_id,
                "recipient": name,
                "number": number,
                "link": build_wa_link(number, given_text),
                "text": given_text,
                "confirm": "approve this exact message to this "
                           "exact recipient before anything is "
                           "opened — the voice instruction alone is "
                           "not approval",
                "note": "the draft expires after 30 minutes"})


def _action_open(draft_id, text, _reader=None, _opener=None,
                 _va_reader=None):
    state = _load_state()
    if not state or state.get("state") != "prepared":
        return _fail("no prepared draft — call prepare first")
    if str(draft_id or "").strip() != str(state.get("draft_id") or ""):
        return _fail("that draft_id does not match the prepared "
                     "draft — call prepare again")
    if _draft_expired(state):
        _clear_state()
        return _fail("that draft has expired — call prepare again")
    given_text = str(text or "")
    if not given_text.strip():
        return _fail("the exact message text is needed to open — "
                     "it must match the approved draft")
    if text_sha(given_text) != state.get("text_sha256"):
        return _fail("that text does not match the approved draft "
                     "— open the exact approved text, or prepare "
                     "a new draft")
    number = state.get("number")
    name = state.get("recipient")
    link = build_wa_link(number, given_text)
    opener = _opener if _opener is not None else url_opener
    try:
        opened = opener(link) if opener is not None \
            else webbrowser.open(link)
    except Exception as exc:  # noqa: BLE001 — the opener never raises
        opened = False
        open_error = str(exc)
    else:
        open_error = ""
    if not opened:
        _record_receipt(name, number, "failed",
                        "link_open_failed", state.get("draft_id"))
        _clear_state()
        return _fail("could not open the WhatsApp chat — "
                     + (open_error or "the browser reported the "
                                      "link did not open"),
                     platform=_PLATFORM, draft_id=state.get("draft_id"))
    # The guard: read the conversation back and prove it is the
    # right one BEFORE anyone sends anything.
    screen = read_screen_text(reader=_reader)
    guard_ok, guard_reason = guard_check(
        screen, name, number, given_text)
    if not guard_ok:
        _record_receipt(name, number, "failed",
                        "guard_" + guard_reason, state.get("draft_id"))
        _clear_state()
        return _fail("the conversation could not be verified — "
                     "don't send. " + {
                         "could_not_read_screen": "the screen could "
                             "not be read back, so the open chat "
                             "was not proven to be the right one",
                         "header_mismatch": "the open conversation "
                             "does not show the recipient's name or "
                             "number",
                         "box_mismatch": "the pre-filled message "
                             "does not match the approved text",
                     }.get(guard_reason, guard_reason),
                     platform=_PLATFORM,
                     draft_id=state.get("draft_id"),
                     guard=guard_reason)
    va = voice_access_status(reader=_va_reader)
    mode = "voice" if va.get("state") == "on" else "tap"
    state["state"] = "opened"
    state["send_mode"] = mode
    state["press_enter_heard"] = False
    ok, err = _save_state(state)
    if not ok:
        _record_receipt(name, number, "failed",
                        "state_save_failed", state.get("draft_id"))
        _clear_state()
        return _fail("the conversation was verified but the send "
                     "could not be armed: %s" % err)
    if mode == "voice":
        instruction = ("Ready — say 'press Enter' to send. "
                       "Windows Voice Access will press Enter and "
                       "WhatsApp will send the message.")
    else:
        instruction = ("Ready — the conversation is verified. "
                       "Voice Access is %s (%s), so tap send "
                       "yourself in WhatsApp. %s"
                       % (va.get("state"), va.get("reason"),
                          va.get("guidance") or ""))
    return _ok({"action": "open",
                "platform": _PLATFORM,
                "draft_id": state.get("draft_id"),
                "recipient": name,
                "number": number,
                "guard": "verified",
                "voice_access": va,
                "send_mode": mode,
                "instruction": instruction})


def _action_verify(draft_id, text, _reader=None):
    state = _load_state()
    if not state or state.get("state") != "opened":
        return _fail("no send is open — call prepare, then open, "
                     "first")
    if str(draft_id or "").strip() != str(state.get("draft_id") or ""):
        return _fail("that draft_id does not match the open send")
    if _draft_expired(state):
        _record_receipt(state.get("recipient"), state.get("number"),
                        "unverified", "draft_expired",
                        state.get("draft_id"))
        _clear_state()
        return _fail("that send has expired — prepare a new draft")
    given_text = str(text or "")
    if not given_text.strip():
        return _fail("the exact message text is needed to verify")
    if text_sha(given_text) != state.get("text_sha256"):
        return _fail("that text does not match the approved draft")
    name = state.get("recipient")
    number = state.get("number")
    mode = state.get("send_mode") or "voice"
    heard = bool(state.get("press_enter_heard"))
    screen = read_screen_text(reader=_reader)
    if screen is None:
        if heard:
            # The owner said "press Enter" but the thread cannot be
            # read back — the send is not observed, so it is
            # unverified, never "sent" on a guess.
            _record_receipt(name, number, "unverified",
                            "could_not_read_thread",
                            state.get("draft_id"))
            _clear_state()
            return _fail("the message could not be seen in the "
                         "thread — the screen could not be read "
                         "back. Outcome: unverified",
                         outcome="unverified",
                         platform=_PLATFORM,
                         draft_id=state.get("draft_id"))
        return _ok({"action": "verify",
                    "platform": _PLATFORM,
                    "draft_id": state.get("draft_id"),
                    "outcome": "pending",
                    "reason": "the thread could not be read yet "
                              "and no send has been confirmed — "
                              "say 'press Enter' to send, then "
                              "verify again"})
    in_thread = thread_contains(screen, given_text)
    if mode == "tap":
        # The one-tap path: the owner tapped send; the thread is
        # the only proof, and it is the proof — VYUK types nothing,
        # so a message in the thread can only be the owner's send.
        if in_thread:
            _record_receipt(name, number, "done", "observed_in_thread",
                            state.get("draft_id"))
            _clear_state()
            return _ok({"action": "verify",
                        "platform": _PLATFORM,
                        "draft_id": state.get("draft_id"),
                        "outcome": "done",
                        "recipient": name})
        _record_receipt(name, number, "unverified",
                        "message_not_in_thread", state.get("draft_id"))
        _clear_state()
        return _fail("the message was not seen in the thread — "
                     "the send did not land, or it has not been "
                     "tapped yet. Outcome: unverified",
                     outcome="unverified",
                     platform=_PLATFORM,
                     draft_id=state.get("draft_id"))
    # The Voice Access path: done needs BOTH the owner's spoken
    # send confirmation AND the message seen in the thread.
    if heard and in_thread:
        _record_receipt(name, number, "done",
                        "observed_in_thread", state.get("draft_id"))
        _clear_state()
        return _ok({"action": "verify",
                    "platform": _PLATFORM,
                    "draft_id": state.get("draft_id"),
                    "outcome": "done",
                    "recipient": name})
    if heard and not in_thread:
        _record_receipt(name, number, "unverified",
                        "send_confirmed_but_not_observed",
                        state.get("draft_id"))
        _clear_state()
        return _fail("you said 'press Enter', but the message was "
                     "not seen in the thread — the send did not "
                     "land. Outcome: unverified",
                     outcome="unverified",
                     platform=_PLATFORM,
                     draft_id=state.get("draft_id"))
    if not heard and in_thread:
        _record_receipt(name, number, "unverified",
                        "message_seen_but_no_send_confirmation",
                        state.get("draft_id"))
        _clear_state()
        return _fail("the message is in the thread, but no send "
                     "was confirmed — it may be an earlier message. "
                     "Outcome: unverified",
                     outcome="unverified",
                     platform=_PLATFORM,
                     draft_id=state.get("draft_id"))
    return _ok({"action": "verify",
                "platform": _PLATFORM,
                "draft_id": state.get("draft_id"),
                "outcome": "pending",
                "reason": "nothing sent yet — say 'press Enter' "
                          "to send, then verify again"})


# ---------------------------------------------------------------------------
# Funnel registration (Triage Wire pattern)
# ---------------------------------------------------------------------------

def register_all(registry):
    """Register social_send with the funnel. The parameter is
    deliberately named `text` — the funnel's audit log hashes any
    argument under that name (audit.redact_args), so receipts stay
    receipts-only at the funnel level too (F-07)."""
    registry.register(
        name="social_send",
        description=_TOOL_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": ("prepare: resolve the recipient "
                                    "and draft the exact text. open: "
                                    "open the wa.me link and verify "
                                    "the conversation. verify: read "
                                    "the thread back after the send. "
                                    "status / events: reads."),
                },
                "recipient": {
                    "type": "string",
                    "description": ("The recipient: a contact name "
                                    "or a phone number. A missing "
                                    "number or an ambiguous name "
                                    "refuses — the tool never "
                                    "guesses."),
                },
                "text": {
                    "type": "string",
                    "description": ("The exact message text. The "
                                    "text is never stored and never "
                                    "appears in the receipts ledger "
                                    "— only its SHA-256 is kept, so "
                                    "open/verify can prove it "
                                    "matches the approved draft."),
                },
                "platform": {
                    "type": "string",
                    "enum": [_PLATFORM],
                    "description": ("WhatsApp only in Phase 1."),
                },
                "draft_id": {
                    "type": "string",
                    "description": ("The draft_id returned by "
                                    "prepare — needed for open and "
                                    "verify."),
                },
            },
            "required": ["action"],
        },
        handler=social_send,
    )
