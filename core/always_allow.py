"""Always-allow with T&Cs (UI rethink Build 4, plan §4).

The single home for every remembered grant in the product:
Settings → Privacy & permissions → "Always allow". This module
is the screen's data model — stdlib only, so the GUI, the
confirm gate and the permission store can all read it without
a cycle. It owns:

  * the plain-words terms (per permission from plan §4.5's
    table, per confirm-kind written here) and the full T&Cs
    (plan §4.6), all jargon-scanned by the suite against
    core/firstopen_optin.JARGON_TERMS plus "CDP" and
    "allowlist" — no jargon word in anything the user reads;
  * the grant-event ledger (data/always_allow_events.jsonl,
    append-only): every grant and every revocation, with what,
    when, how and the terms version accepted — the §4.8
    receipt record. The Activity line with its Undo action is
    written through log_grant_receipt() (record_family, lazy
    import — the same PF-2 line the Settings Apply path
    already writes for permission flips);
  * list_grants(): the screen's rows — permission grants from
    core/permission_grants (state granted, with when + how
    from the store's change events) plus remembered confirm
    kinds from core/confirm_gate_allowlist (when + how from
    this ledger, since the allowlist store itself is a plain
    list with no history);
  * drop_stale_allowlist_entries(): the §4.6 terms-change
    migration — any stored kind that no longer passes the
    safe-shape test is dropped and the drop recorded the
    same way (event + Activity line). The screen runs it on
    open; a build that changes the gate taxonomy runs it
    at migration time.

Two deliberate boundaries, stated here so no reader mistakes
them for oversights:

  * D-R24: there is NO "allow everything" — rows are per
    permission and per kind, each with its own terms and its
    own Remove. Forget-all covers remembered confirm-kinds
    ONLY ("Forget all remembered kinds"); permission grants
    are revoked per-row here or from the Permissions page.
  * The "how" of a permission grant defaults to "Settings":
    every durable grant goes through the Settings-only write
    path (D8), so "in Settings" is true unless a caller
    passes a more precise how ("first-run setup" from the
    wizard, "confirm dialog" from the mic ask, "files ask"
    from the per-call files button).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone

# Terms version accepted by every grant this build records.
# A future build that changes what a row covers bumps this
# and drops the rows whose kind no longer passes (§4.6).
TERMS_VERSION = 1

_EVENTS_FILE = "always_allow_events.jsonl"

# The screen's first line (§4.9) and the never-list sentence
# (§4.5, kept verbatim — the Remembered approvals card says
# it the same way, so the user is never left wondering why
# one thing asks and another does not).
SCREEN_FIRST_LINE = "Some things will always ask."
NEVER_LIST_SENTENCE = ("deleting, sending, purchases and system "
                       "changes always ask, every time.")

# The full T&Cs (§4.6), shown the first time the owner opens
# the screen. One wording change from the plan's draft: the
# Screen row's terms say "a picture of your screen", not
# "a screenshot" — "screenshot" is a JARGON_TERMS word and
# the wizard already says it the plain way.
TERMS_TITLE = "Always allow — the deal, in plain words."
TERMS_PARAGRAPHS = (
    "When you tap “Always allow” for something, VYUK stops "
    "asking you about that one thing. It remembers your yes "
    "on this computer only — nothing about it leaves this PC, "
    "and nobody else's VYUK is affected. You can take any of "
    "them back in one tap, right here, and taking it back "
    "works straight away — the next time VYUK would have done "
    "that thing, it asks again.",
    "What this does NOT cover. Some things will always ask, "
    "every time, no matter what you allow here: anything that "
    "moves money or buys anything; anything that sends a "
    "message, an email or a post to someone else; anything "
    "that deletes something; anything that runs a command or "
    "changes a system setting; anything that gives a new tool "
    "your trust. Those ask because getting them wrong cannot "
    "be undone, and no always-allow can cover them.",
    "What saying no means. If you switch something off in "
    "Settings, that no beats every always-allow — including "
    "one you set earlier. A no is always stronger than a yes. "
    "If you take back an always-allow here, VYUK asks again "
    "from the next time, straight away.",
    "What voice can and cannot do. You can say “yes” to one "
    "thing VYUK is asking about right now. You cannot set, "
    "widen or bring back an always-allow by voice — that only "
    "ever happens by your tap on this screen, in Settings, "
    "or in first-run setup. If you say “always allow” out "
    "loud, VYUK will say so and point you here instead of "
    "doing it.",
    "If these terms change. If a future version changes what "
    "one of these rows covers, that row is switched off and "
    "you are told — your old yes does not silently stretch "
    "to cover a new thing. You are told this way: the switch-off "
    "is written down with the reason — in the Activity feed and "
    "in this screen's own history — and the next time you open "
    "this screen it says so on its status line. The mechanism is a "
    "migration, not a hope: any build that changes the gate "
    "taxonomy must drop the always-allow entries whose kind "
    "no longer passes the safe-shape test, and the drop is "
    "recorded the same way.",
    "What this does NOT cover that you might assume it does. "
    "Always allow does not mean VYUK acts without being "
    "asked — she still only acts when you ask, or when a "
    "standing watch you set fires. It does not cover a "
    "different computer, a different install, or a "
    "reinstall. It does not cover the browser extension's "
    "host permissions. It does not stop the Activity ledger "
    "recording what VYUK did. It does not make a denied "
    "permission work. It does not let voice set anything. "
    "It is per-kind, not per-family: always allow for typing "
    "does not cover typing in the browser — each kind is its "
    "own row, its own yes. And approving a plan approves "
    "just this once, just these steps.",
)

# Layer 1 — the six permissions (§4.5 table). Home devices has
# no row: no device tool exists yet (honest-empty, W7), and a
# row for a capability that does not exist would be a lie.
# thing, one-line terms.
PERMISSION_ROWS = {
    "microphone": (
        "Microphone",
        "Only when you ask her to listen. Nothing is recorded "
        "unless you ask. Stays on this PC. Turn it off any time "
        "in Settings → Permissions."),
    "camera": (
        "Camera",
        "Only when you ask her to look. She never opens it on "
        "her own. Stays on this PC. Turn it off any time in "
        "Settings → Permissions."),
    "screen": (
        "Screen",
        "Only when you ask for a picture of your screen or an "
        "action on screen. Just looking once when you ask needs "
        "nothing. Stays on this PC. Turn it off any time in "
        "Settings → Permissions."),
    "files": (
        "Files",
        "Only the folders you picked. New folders ask first. "
        "Stays on this PC. Change the folders any time in "
        "Settings → Permissions."),
    "browser": (
        "Browser",
        "Only when you ask her to. She never types a password "
        "or fills in a sign-in. Stays on this PC. Turn it off "
        "any time in Settings → Browser control."),
}

# Layer 2 — the 8 allowlistable confirm-kinds (§4.5). The
# kind's plain-words name and its one-line terms. The terms
# name the kind's real reach: mouse_control clicks by
# position (its description says so), so its terms say so —
# a click by position can land on any button at that spot,
# and no name check can catch it (the icon-only residual
# the plan accepts; covering_call documents the same).
KIND_ROWS = {
    "mouse_control": (
        "Move and click the mouse",
        "Moves the pointer and clicks the spot you asked for. "
        "It clicks by position, so it can land on any button "
        "at that spot. Take it back here any time."),
    "cursor_control": (
        "Move the mouse pointer",
        "Only moves the pointer smoothly to the spot you asked "
        "for. It never clicks. Take it back here any time."),
    "click_element": (
        "Click a button by its name",
        "Only clicks the named button you asked for. A click "
        "on anything that sends, deletes, pays or closes "
        "still asks. Take it back here any time."),
    "uia_click_control": (
        "Click a control by its name",
        "Only clicks the named control you asked for. A click "
        "on anything that sends, deletes, pays or closes "
        "still asks. Take it back here any time."),
    "click_by_mark": (
        "Click a numbered mark on screen",
        "Only clicks the numbered mark you asked for, from "
        "the last look at the screen. A click on anything "
        "that sends, deletes, pays or closes still asks. "
        "Take it back here any time."),
    "type_text": (
        "Type what you asked",
        "Only types the words you asked for, into the app in "
        "front. Typing into a command window still asks. "
        "Take it back here any time."),
    "uia_type_text": (
        "Type into a named field",
        "Only types the words you asked for, into the named "
        "box. Typing into a command window still asks. Take "
        "it back here any time."),
    "browser_type": (
        "Type in the browser",
        "Only types into the page you asked about. Anything "
        "from a page you didn't write still asks. Take it "
        "back here any time."),
}

# "how" labels — the three ways a grant is made (§4.4), plus
# the per-call files ask (§4.5). Stored on the event; the
# screen renders "Allowed 17 Nov 2026 · tap in Settings".
HOW_SETTINGS = "Settings"
HOW_WIZARD = "first-run setup"
HOW_DIALOG = "confirm dialog"
HOW_FILES_ASK = "files ask"
HOW_MIGRATION = "terms changed"

# Spoken always-allow (§4.7): the phrases voice must refuse
# in words, naming the screen — never set, widen or restore
# by voice, and never resolve a pending confirmation as a
# yes. Matched on the normalised utterance (lowercased,
# trailing punctuation stripped) in settings_voice and in
# the confirm hook.
ALWAYS_ALLOW_VOICE_PHRASES = frozenset({
    "always allow",
    "always allow this",
    "always allow this kind",
    "always allow that",
    "remember this",
    "remember this kind",
    "never ask again",
    "don't ask again",
    "dont ask again",
    "do not ask again",
    "always allow it",
})

ALWAYS_ALLOW_VOICE_REFUSAL = (
    "I can't set that by voice — always allow lives on the "
    "Always allow screen in Settings, under Privacy & "
    "permissions. Tap it there and it sticks; voice can only "
    "say yes to the one thing I'm asking about right now.")


def is_always_allow_utterance(text: str) -> bool:
    """True when a spoken utterance asks for an always-allow
    (§4.7, D8) — the ONE predicate both voice surfaces use,
    so no phrasing is refused in one place and missed in the
    other: the exact generic phrases ("always allow",
    "remember this", "never ask again", ...) OR "always" +
    allow/remember/never-ask ("always allow the camera" —
    the per-permission shape the router already caught by
    substring while the confirm hook's exact set missed).
    A plain "allow the camera" is NOT one (no "always") —
    that stays a permission ask. Never raises."""
    try:
        low = str(text or "").strip().lower()
        while low and low[-1] in ".,!?;:":
            low = low[:-1].rstrip()
        if low in ALWAYS_ALLOW_VOICE_PHRASES:
            return True
        return "always" in low and any(
            w in low for w in ("allow", "remember", "never ask",
                               "don't ask", "dont ask", "do not ask"))
    except Exception:  # noqa: BLE001 — undecidable: not one
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _events_path() -> pathlib.Path:
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / _EVENTS_FILE)


def format_granted_when(iso_at: str, how: str) -> str:
    """The row's granted-when line (11.5 mono): "Allowed
    17 Nov 2026 · tap in Settings". Never raises; an
    unparseable timestamp reads as "Allowed · <how>"."""
    when = ""
    try:
        dt = datetime.fromisoformat(str(iso_at or ""))
        when = dt.strftime("%d %b %Y")
    except Exception:  # noqa: BLE001 — the how still shows
        when = ""
    how_word = {"Settings": "tap in Settings",
                "first-run setup": "in first-run setup",
                "confirm dialog": "tap in the confirm dialog",
                "files ask": "tap in the files ask",
                "terms changed": "terms changed"}.get(
                    str(how or ""), str(how or ""))
    if when:
        return f"Allowed {when} · {how_word}"
    return f"Allowed · {how_word}" if how_word else "Allowed"


def record_event(*, subject: str, action: str, how: str,
                 terms_version: int = TERMS_VERSION) -> bool:
    """Append one grant/revocation event to the ledger.
    subject is "permission:<id>" or "kind:<tool>"; action is
    "granted", "revoked", "paused" or "resumed" (the last two
    are the F-F402-reversal pause/resume events). Best-effort:
    False (never raising) when the ledger cannot be written —
    a failed receipt never fails the grant itself."""
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"event_id": uuid.uuid4().hex[:12],
                 "subject": str(subject), "action": str(action),
                 "how": str(how), "terms_version": int(terms_version),
                 "at": _now_iso()}
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        return True
    except Exception:  # noqa: BLE001 — receipt never blocks
        return False


def read_events() -> list:
    """All grant events, oldest first. [] on any failure —
    a broken ledger reads as no history, never as a grant."""
    try:
        text = _events_path().read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("subject"):
            events.append(event)
    return events


def latest_event(subject: str) -> dict | None:
    """The newest event for one subject, or None."""
    found = None
    for event in read_events():
        if str(event.get("subject", "")) == subject:
            found = event
    return found


def _permission_how(pid: str) -> tuple[str, str]:
    """(granted_at, how) for a permission, from the store's
    own change events — the newest change that set it to
    granted. Defaults: now + Settings (every durable grant
    goes through the Settings-only path, D8)."""
    try:
        from . import permission_grants as _pg

        _grants, changes = _pg._read_store() or ({}, [])
        for event in reversed(changes or []):
            if str(event.get("permission_id", "")) != pid:
                continue
            if str(event.get("new_state", "")) != "granted":
                continue
            return (str(event.get("at", "") or ""),
                    str(event.get("how", "") or HOW_SETTINGS))
    except Exception:  # noqa: BLE001 — defaults below
        pass
    return ("", HOW_SETTINGS)


def files_folder_offer(tool_name: str, folder) -> str | None:
    """The folder the per-call files ask may offer (§4.5),
    or None: the tool must be files-bound, files must be
    UNKNOWN (granted goes through Settings — its folders
    are already allowed — and denied refuses before any
    ask), and the folder must be judgable. The folder comes
    from the gate's pending entry (raw args), never from
    the masked summary."""
    try:
        from . import permission_grants as _pg

        if _pg.permission_for_tool(tool_name) != "files":
            return None
        if str(_pg.get_state("files") or "") != "unknown":
            return None
    except Exception:  # noqa: BLE001 — no offer, no button
        return None
    if not isinstance(folder, str) or not folder.strip():
        return None
    return folder.strip()


def list_grants() -> list:
    """The screen's rows, permissions first then kinds:
    {subject, layer ("permission"|"kind"), thing, terms,
    granted_at (ISO or ""), how, granted_when (the mono
    line)}. Only live grants: a permission whose state is
    not granted, and a kind no longer stored, have no row.
    Never raises — a broken store reads as no rows."""
    rows: list = []
    try:
        from . import permission_grants as _pg

        for pid in _pg.PERMISSION_IDS:
            if pid not in PERMISSION_ROWS:
                continue
            try:
                if str(_pg.get_state(pid) or "") != "granted":
                    continue
            except Exception:  # noqa: BLE001 — skip this row
                continue
            thing, terms = PERMISSION_ROWS[pid]
            at, how = _permission_how(pid)
            rows.append({
                "subject": f"permission:{pid}",
                "layer": "permission",
                "thing": thing, "terms": terms,
                "granted_at": at, "how": how,
                "granted_when": format_granted_when(at, how),
            })
    except Exception:  # noqa: BLE001 — permissions unreadable
        pass
    try:
        from .confirm_gate_allowlist import list_allowlisted as _listed

        for kind in _listed():
            if kind not in KIND_ROWS:
                continue
            thing, terms = KIND_ROWS[kind]
            event = latest_event(f"kind:{kind}")
            at = str((event or {}).get("at", "") or "")
            how = str((event or {}).get("how", "") or HOW_DIALOG)
            rows.append({
                "subject": f"kind:{kind}",
                "layer": "kind",
                "thing": thing, "terms": terms,
                "granted_at": at, "how": how,
                "granted_when": format_granted_when(at, how),
            })
    except Exception:  # noqa: BLE001 — kinds unreadable
        pass
    return rows


def log_grant_receipt(*, subject: str, action: str, how: str,
                      undo=None, note: str = "") -> str:
    """The §4.8 Activity receipt: one human-form line naming
    what, when and how, with an Undo action when `undo` (a
    plain callable) is given. The line is the record; the
    undo is one-shot and process-local by the record-family
    design (a restart drops pending undos). `note` is an
    extra plain-words clause about the grant (e.g. which
    folder). Returns the event id, or "" when the receipt
    could not be written — never raises, never blocks the
    grant."""
    thing = ""
    try:
        if subject.startswith("permission:"):
            thing = PERMISSION_ROWS.get(
                subject.split(":", 1)[1], (subject, ""))[0]
        elif subject.startswith("kind:"):
            thing = KIND_ROWS.get(
                subject.split(":", 1)[1], (subject, ""))[0]
    except Exception:  # noqa: BLE001
        thing = ""
    thing = thing or subject
    # F-F402 reversal (loophole #2): the pause/resume events
    # name themselves — a pause reads "paused", never "took
    # back" (the grant still stands), and a resume reads as a
    # resumed always-allow (it will not ask again, like a grant).
    verb = {"granted": "always allowed", "paused": "paused",
            "resumed": "resumed always-allow for"}.get(
                action, "took back")
    how_word = {"Settings": "in Settings",
                "first-run setup": "in first-run setup",
                "confirm dialog": "in the confirm dialog",
                "files ask": "in the files ask",
                "Activity": "from the Activity line",
                "terms changed": "because the terms changed"}.get(
                    str(how or ""), str(how or ""))
    text = (f"You {verb} {thing}"
            f"{' ' + str(note).strip() if str(note or '').strip() else ''} — "
            f"{'it will not ask again' if action in ('granted', 'resumed') else 'it will ask again next time'} "
            f"({how_word}; terms v{TERMS_VERSION}).")
    action_id = ""
    try:
        from . import record_family as _rf

        if undo is not None:
            action_id = f"always_allow_revoke:{subject}:{uuid.uuid4().hex[:8]}"
            try:
                _rf.register_undo(action_id, undo, kind="setting")
            except Exception:  # noqa: BLE001 — no undo, line only
                action_id = ""
        return str(_rf.log_activity_human(
            text, reversible=True if action_id else None,
            undo_action_id=action_id or None))
    except Exception:  # noqa: BLE001 — receipt never blocks
        return ""


def revoke_subject(subject: str) -> bool:
    """Revoke one row from the screen: a permission returns
    to asking (state "unknown" via the Settings-only path —
    NOT denied: Remove means "ask me again", and a denial
    would also refuse the first ask), a kind is forgotten
    from the allowlist. Records the revocation event +
    receipt. True when the store now lacks the grant."""
    try:
        if subject.startswith("permission:"):
            pid = subject.split(":", 1)[1]
            from . import permission_grants as _pg

            if str(_pg.get_state(pid) or "") == "granted":
                ok = _pg.set_permission_state(
                    pid, "unknown", via="settings",
                    how=HOW_SETTINGS)
                if not ok:
                    return False
            record_event(subject=subject, action="revoked",
                         how=HOW_SETTINGS)
            log_grant_receipt(subject=subject, action="revoked",
                              how=HOW_SETTINGS,
                              undo=lambda: _regrant_permission(pid))
            return True
        if subject.startswith("kind:"):
            kind = subject.split(":", 1)[1]
            from .confirm_gate_allowlist import revoke_kind as _revoke

            ok = _revoke(kind)
            if not ok:
                return False
            return True
    except Exception:  # noqa: BLE001 — revoke never raises
        return False
    return False


def _regrant_permission(pid: str) -> bool:
    """The revocation receipt's Undo: restore the permission
    grant the screen just took back. Settings-only path."""
    try:
        from . import permission_grants as _pg

        return bool(_pg.set_permission_state(
            pid, "granted", via="settings", how=HOW_SETTINGS))
    except Exception:  # noqa: BLE001
        return False


def forget_all_kinds() -> list:
    """Forget every remembered confirm-kind (§4.4: the
    bottom button is labelled "Forget all remembered kinds"
    so its scope is visible — permission grants are NOT
    touched: a bulk button that could deafen VYUK with one
    tap and no warning would be a trap, not a convenience).
    Returns the kinds forgotten."""
    forgotten: list = []
    try:
        from .confirm_gate_allowlist import list_allowlisted as _listed
        from .confirm_gate_allowlist import revoke_kind as _revoke

        for kind in _listed():
            if _revoke(kind):
                forgotten.append(kind)
    except Exception:  # noqa: BLE001
        pass
    return forgotten


def drop_stale_allowlist_entries() -> list:
    """The §4.6 terms-change migration: any stored kind that
    no longer passes the safe-shape test is dropped, and
    the drop is recorded (event + Activity line) the same
    way a revocation is. Returns the kinds dropped. The
    screen runs this on open; a build that changes the gate
    taxonomy runs it at migration time. This build changes
    no taxonomy, so in practice it drops nothing — the
    suite proves it drops a hand-planted destructive kind."""
    dropped: list = []
    try:
        from .confirm_gate_allowlist import (
            _load_allowlist as _load, is_allowlistable as _ok,
            revoke_kind as _revoke)

        for kind in sorted(_load()):
            if _ok(kind):
                continue
            if _revoke(kind):
                dropped.append(kind)
                record_event(subject=f"kind:{kind}",
                             action="revoked", how=HOW_MIGRATION)
                log_grant_receipt(subject=f"kind:{kind}",
                                  action="revoked",
                                  how="terms changed")
    except Exception:  # noqa: BLE001 — migration never breaks open
        pass
    return dropped


def user_facing_strings() -> list:
    """Every string the screen, the T&Cs and the receipts
    show the user — the jargon scan's input. Code words
    (function names, comments) are not user-facing and are
    not listed here."""
    strings = [SCREEN_FIRST_LINE, NEVER_LIST_SENTENCE,
               TERMS_TITLE, ALWAYS_ALLOW_VOICE_REFUSAL]
    strings.extend(TERMS_PARAGRAPHS)
    for thing, terms in list(PERMISSION_ROWS.values()) + \
            list(KIND_ROWS.values()):
        strings.append(thing)
        strings.append(terms)
    strings.append("Forget all remembered kinds")
    strings.append("Nothing remembered — VYUK asks before every "
                   "action that needs it.")
    strings.append("Take it back here any time.")
    return strings
