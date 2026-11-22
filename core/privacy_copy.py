"""The privacy copy, in one place (loophole batch 8: L-31, L-32).

Every user-facing sentence about what leaves this PC lives here
as a named constant, and every surface that states it — the
wizard feature card, the sidebar privacy card, the Settings
privacy promise, the Your-world card, the Memory page subtitle,
the wizard name page, the wizard Google-services page, the
Google-connect verdict — reads its sentence from here through
``gui_facade`` (gui never imports core directly). One wording,
one place: the copy cannot drift apart again.

What is actually true (established by reading the code, not by
assertion):

* Stored on this device: settings, memories, notes, milestones,
  tokens, keys, receipts, the cost ledger. They live in VYUK's
  data folder and nowhere else.
* A cloud turn sends your words to the cloud brain to be
  understood, together with the facts it needs to answer
  (``core/voice/live_client_lifecycle.py`` ``_build_live_prompt``
  appends ``memory_store.get_memory_context_prompt()``; the
  cloud text turn re-reads it per turn in
  ``core/voice/cloud_text_turn.py``), the continuity digest,
  and anything you asked her to read in that turn (screen, file,
  mail, calendar, clipboard — tool results reach the brain as
  data). Your name travels the same way whenever she needs it
  to greet you or brief you.
* With the local brain, your voice stays on this PC: speech is
  recognised on-device and no audio or words go to a cloud
  brain to think. (Mail and calendar still live at Google and
  are read from there; anything you ask her to send still goes
  where you send it.)
* Sending mail or adding an event needs your approval each
  time. The approval happens in the popup, or by voice in a
  session you have proved — the batch-4 lock: a spoken yes in
  an unproven session approves nothing (``core/funnel/
  tools_gate.py`` ``voice_confirm_allowed``; the Privacy card
  explains the three ways to prove a session). There is no
  voiceprint; "you have proved" is the honest form of "your OK".

The banned phrases are the wordings this batch removed; the
repro and the suite grep the tree for them so none can return.
"""

# The one approval route, shared by every surface that promises
# an approval (Your-world card, wizard Google-services page,
# Google-connect verdict). One fragment, so the three wordings
# cannot drift apart again.
APPROVAL_ROUTE = "in the popup, or by voice in a session you have proved"

# L-32: the Google-connect verdict (core/google_services_token.py
# _CONNECTED_OK). The connection itself already happens on
# screen — the OAuth flow opens the browser and you approve at
# Google — so the verdict promises nothing about the connection;
# it names the approval route for what comes after it.
CONNECTED_OK = (
    "Connected to Google — VYUK can now read your calendar and mail. "
    "Sending mail or adding events needs your approval each time — "
    + APPROVAL_ROUTE + "."
)

# L-31: the wizard feature card ("Private by design"). True for
# every brain choice: local, Gemini cloud, Groq cloud. The old
# line ("With the built-in brain, your voice and data never leave
# this PC") was shown to cloud-brain users too, and "your data"
# was overbroad even for the local brain (mail and calendar live
# at Google).
FEATURE_CARD_PRIVATE = (
    "With the local brain, your voice stays on this PC. "
    "With a cloud brain, your words go to that cloud to be "
    "understood — and nowhere else."
)

# L-31: the sidebar privacy card and the Settings privacy
# promise (the same sentence in both places). Memory is stored
# locally; the qualification is the part the old line hid.
PRIVACY_PROMISE = (
    "Memory is stored on this device. In a cloud turn, the facts "
    "she needs to answer go to the cloud brain with your words. "
    "The mic is off until you talk to VYUK."
)

# L-31 + L-32: the Settings "Your world" card. Calendar and
# mail live at Google (the old "everything stays on this
# device" was false the moment the account is connected); the
# approval route replaces "approve it in a popup" (a spoken yes
# in a proved session approves too — batch 4).
WORLD_CARD = (
    "Connect your Google account so VYUK knows your day — lectures, "
    "deadlines and mail. She can read your calendar and mail, and "
    "she can send an email or add an event only after you approve "
    "it — " + APPROVAL_ROUTE + ". She can never delete anything. "
    "Sign-in happens in your browser; your calendar and mail live "
    "at Google, and she reads them from there."
)

# L-31: the Memory page subtitle. "Stored only on this device"
# is true of the storage; the qualification says what a cloud
# turn does with the stored facts. LENGTH BUDGET: the subtitle
# QLabel in _page_shell does NOT wrap, so its sizeHint is the
# full unwrapped text width and sets the page's minimum width —
# the 146-char first draft stretched the Memory page past the
# 908px window and clipped the rightmost web-graph labels
# (r3ef202 pixel leg). Keep this under ~90 chars so the page
# keeps its 908px geometry; the full promise lives on the
# sidebar card and the Settings privacy promise.
MEMORY_PAGE_SUBTITLE = (
    "Facts VYUK remembers about you — stored on this device; "
    "cloud turns send what she needs."
)

# L-31: the wizard name page. The name is stored locally, but
# "never shares it anywhere" was false: it rides the memory
# block and the briefing tool result in a cloud turn.
NAME_PAGE_SUBTITLE = (
    "Your name is how she greets you on Home and in your morning "
    "briefing. It is stored on this device — in a cloud turn it "
    "goes to the cloud brain with your words, like everything she "
    "needs to answer."
)

# L-32: the wizard Google-services page. "Only ever on your
# say-so" hid who can say so; the approval route says it.
GOOGLE_SERVICES_SUBTITLE = (
    "Connect Gmail and Calendar and your first briefing knows your "
    "day — today's schedule, what's coming up, what mail is "
    "waiting. Read-only unless you ask her to send or book, and "
    "only ever when you approve it — " + APPROVAL_ROUTE + "."
)

# Phrases no user-facing copy may carry any more — the exact
# false claims this batch removed. (Deliberately the full
# claims, not bare substrings: "Keys and secrets ... never
# leave this PC in a file" in gui/window_shell_settingsio.py is
# TRUE — the export file excludes them — and stays.) The repro
# and the suite grep the tree for these (privacy_copy itself is
# exempt — it names them here so the check can list them).
BANNED_PHRASES = (
    "your voice and data never leave this PC",
    "never shares it anywhere",
    "everything stays on this device",
    "only with your OK each time",
    "only ever on your say-so",
)
