"""First-run ask copy (first-run onboarding, 2026-11-16).

The plain-words text of every first-run ask, in one Qt-free
place — the wizard's pages and the standalone dialogs render
from here, and the suite jargon-scans it without building Qt.

Copy rules, from the first-open precedent (core/firstopen_optin):
no JARGON_TERMS word in anything the user reads; every ask
states what it is for, that it stays on this PC, that it is off
until yes, and how to switch it off. The one exception is the
browser page's honest-cost line, which must name the separate
profile in plain words.

Wording changes from the scope drafts (§2, D4) — each recorded
with its reason, none of them a meaning change:

* Screen: the draft said "take a screenshot" — "screenshot" is
  a JARGON_TERMS word, so the ask says "save a picture of your
  screen" instead. The honesty note (plain screen_look needs no
  ask) is kept verbatim in meaning.
* Microphone explainer: the first-use dialog's explainer says
  "VYUK will still connect, but it won't hear you" — true at
  Connect, wrong in the wizard, where nothing is connecting.
  The wizard's explainer says the same thing for the wizard:
  she still won't hear you, and Settings is the way back. The
  detail text is the dialog's, verbatim. Field-check #9: the
  wizard's "Not now" is a durable off (the mic stays off until
  Settings flips it), so the explainer says so — "she won't
  ask again" — instead of "skips it this time".
"""

from __future__ import annotations

#: Every ask, keyed by permission id. Each entry: the page
#: title, the body in plain words, and the button labels in
#: order. The microphone entry is the first-use dialog's copy
#: (gui/window_services_confirm._build_mic_first_use_dialog),
#: verbatim except the explainer (see the module docstring).
ASK_COPY = {
    "microphone": {
        "title": "VYUK can't hear you yet",
        "body": (
            "Your microphone is switched off — this is a fresh "
            "install and VYUK has never asked before.\n\n"
            "Always allow switches the microphone on for good, so "
            "you won't be asked again. You can turn it off any time "
            "in Settings → Permissions.\n\n"
            "Allow once lets VYUK listen for this session only, and "
            "it'll ask again next time you start the app."),
        "explainer": (
            "Not now switches the microphone off — she still "
            "won't hear you, and she won't ask again. You can "
            "switch it on any time in Settings → Permissions."),
        "buttons": ("Always allow", "Allow once", "Open Settings",
                    "Not now"),
    },
    "browser": {
        "title": "Can VYUK work in your browser?",
        "body": (
            "If you say yes, she can open pages, click things and "
            "read what's on the page in your Chrome — the one "
            "you're already signed in to. She only drives it when "
            "you ask her to, and she never types a password or "
            "fills in a sign-in. You can switch it off any time in "
            "Settings → Browser control."),
        "cost_line": (
            "This opens Chrome so VYUK can drive it. If Chrome has "
            "to restart, it reopens on a separate VYUK profile — "
            "your everyday logins and tabs aren't in it, so you'd "
            "sign in again there. If your Chrome can do the "
            "one-switch way instead, she'll use your real Chrome "
            "with everything already signed in."),
        "direct_line": (
            "For now VYUK drives your Chrome directly. The browser "
            "add-on comes later."),
        "buttons": ("Connect my browser", "Not now"),
    },
    "camera": {
        "title": "Can VYUK use your camera?",
        "body": (
            "Only when you ask her to look — to describe what she "
            "sees, or to take a photo you asked for. She never "
            "opens it on her own. You can switch it off any time "
            "in Settings → Permissions."),
        "buttons": ("Yes", "Not now"),
    },
    "screen": {
        "title": "Can VYUK keep what she sees on your screen?",
        "body": (
            "When you ask her to save a picture of your screen or "
            "act on what's on it, she needs this on. Just looking "
            "once when you ask doesn't need it. You can switch it "
            "off any time in Settings → Permissions."),
        "buttons": ("Yes", "Not now"),
    },
    "files": {
        "title": "Which folders can VYUK use?",
        "body": (
            "She needs to know where your files live before she "
            "can read, write or organise them. Pick a folder — you "
            "can add more later in Settings → Permissions. If you "
            "skip, she'll ask you each time instead."),
        "buttons": ("Choose a folder", "Not now"),
    },
    "home_devices": {
        # Round-3 wizard expansion: the sixth permission id,
        # asked up front like the other five. D10 honest-empty —
        # no home-device tools exist yet, so the copy says so:
        # yes today changes nothing until a device is connected.
        "title": "Can VYUK talk to devices in your home?",
        "body": (
            "When you connect a device at home — a light, a "
            "speaker, anything on your home network — she needs "
            "this on before she can talk to it. She only talks to "
            "devices you have connected, and nothing else. Nothing "
            "is connected yet, so saying yes changes nothing today. "
            "You can switch it off any time in Settings → "
            "Permissions."),
        "buttons": ("Yes", "Not now"),
    },
}

#: The toggle-first lines for the browser page's needs-restart
#: state (scope §3): the one-switch way is offered first because
#: it is the only path that keeps the real profile.
TOGGLE_FIRST_LINES = {
    "headline": (
        "Chrome is open, but VYUK can't reach it yet. Keep your "
        "Chrome as it is — turn on one switch inside Chrome, then "
        "tap 'Check again'."),
    "steps": (
        "In Chrome, type chrome://inspect in the address bar, turn "
        "on the remote-debugging switch, then tap Check again. "
        "That keeps your real Chrome — logins and tabs included."),
    "restart_instead": "Restart Chrome instead",
    "restart_warning": (
        "Chrome will close and reopen on a separate VYUK profile — "
        "your everyday logins and tabs aren't in it, so you'd sign "
        "in again there. Your tabs will come back when you next "
        "open Chrome normally. Unsaved work in web forms may be "
        "lost."),
}

#: The short refusal the funnel returns when a browser tool
#: refuses for lack of a session (kept here so the strip, the
#: wizard and the suite all read the same words). The full
#: guided refusal lives in core/desktop/browser_session.py
#: (_guided_no_session) — this is its first line, the way back.
BROWSER_WAY_BACK = "Tap 'Connect my browser' and I'll set it up."


def all_ask_text() -> list[str]:
    """Every user-facing ask string, for the jargon scan."""
    texts = []
    for entry in ASK_COPY.values():
        texts.append(str(entry.get("title", "")))
        texts.append(str(entry.get("body", "")))
        texts.append(str(entry.get("explainer", "")))
        texts.append(str(entry.get("cost_line", "")))
        texts.append(str(entry.get("direct_line", "")))
        texts.extend(str(b) for b in entry.get("buttons", ()))
    for value in TOGGLE_FIRST_LINES.values():
        texts.append(str(value))
    texts.append(BROWSER_WAY_BACK)
    return [t for t in texts if t]
