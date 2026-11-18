"""Deliberation guard (v5.52, items 4 + 9).

THE INCIDENT CLASS: a text brain, faced with a task it cannot
immediately map to a tool (or that feels too big), narrates
its own process in first person — "I'm working through a bit
of a contradiction…", "I realized I need to clarify that
open_url is just a system-level command…", "Honestly, solving
these is beyond my current capabilities… I need to let the
user know I can't 'solve' the problems…" — and that narration
is delivered to the user as the reply. Deliberation is
thinking, not an answer; it must NEVER be deliverable.

This module is the shared detector + corrective used by the
text clients (lite/local/Groq — Live is untouched: its turns
are spoken streaming, not delivered text blocks). The rule
the clients enforce:

  * a turn that used NO tool yet and whose text matches the
    deliberation markers is WITHHELD;
  * the model retries ONCE with CORRECTIVE (plus the
    reasoning branch of item 9 when the task is heavy
    reasoning);
  * if the retry is still deliberation, the client delivers
    PERSONA_FAILURE — one honest persona line — never the raw
    deliberation.

FIXTURE CORPUS (verbatim leaks from the 2026-10-23 field
session; the suite pins every one as detected):

  L1 (item 5) "I'm working through a bit of a contradiction.
      It seems I opened youtube.com successfully, but then
      failed to search using a browser-specific command.
      I realized I need to clarify that open_url is just a
      system-level command…"
  L2 (item 5 ext) "I'm now zeroing in on the fundamental
      problem… establishing a connection with a specific
      browser instance, like attaching to your real Chrome… The
      previous attempt failed because of the disconnect"
  L3 (item 4 ext) "I am able to interact to a certain extent,
      sir, but dynamic and complex web pages like Spotify's
      web player can present challenges for identifying and
      clicking specific controls, especially without precise
      element or coordinate information, and without being
      able to handle logins."
  L4 (item 6) deliberation citing "the 'Network offline'
      status detected in the ambient context" as fact.
  L5 (item 9) "Honestly, solving these is beyond my current
      capabilities. Without a symbolic computation engine or
      detailed context, I can't derive or analyze complex
      equations like these directly. I need to let the user
      know I can't 'solve' the problems in the way they
      probably intend."
  plus the original item-4 shape: first-person narration
  about hunting the toolbox for a play tool that didn't
  exist.

POST-HOC SUMMARIES (v5.53, F4): the guard above watches
PRE-action turns. The 2026-10-23 evening session on v5.52
produced the sibling shape AFTER the tools ran — a headed
process essay delivered as the reply, verbatim:

  L6 (F4) "**Addressing Automation Hiccups** I encountered
      several unexpected hurdles with the browser
      automation, leading to the pauses you observed.
      Re-establishing the connection and refining commands
      became necessary after repeated errors. Validation,
      timeout, and unexpected browser closures contributed
      to significant latency as I worked through these
      challenges, but I've now re-established a successful
      connection."

That is process reported instead of outcome: what the user
needed was "the song played 42 seconds, then stopped".
looks_like_process_summary + OUTCOME_CORRECTIVE cover the
post-tool turns of the same clients; the ambient packet
carries the same doctrine as steering for the brains the
guard cannot intercept (Live).

FALSE-POSITIVE DISCIPLINE: markers target process narration
(toolbox hunts, approach narration, capability surrender,
meta "I need to let the user know"), never content. A joke,
a fact, a plain refusal ("I can't delete system files")
match nothing — the suite pins several.
"""
from __future__ import annotations

import re

_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    # L1 — self-correction narration (item 5)
    r"i'?m working through",
    r"i realized i need to clarify",
    r"i realise i need to clarify",
    # L2 — diagnosis narration instead of acting (item 5 ext)
    r"zeroing in on",
    r"establishing a connection with a specific browser instance",
    r"the previous attempt failed because of the disconnect",
    # L3 — capability hedging about the page (item 4 ext)
    r"i am able to interact to a certain extent",
    r"dynamic and complex web pages",
    r"can present challenges for identifying and clicking",
    # L4 — ambient colour cited as fact (item 6)
    r"status detected in the ambient",
    r"the '(network offline|network)' status",
    # L5 — capability surrender + meta voice (item 9)
    r"beyond my (current )?capabilit",
    r"i need to let the user know",
    r"without a symbolic computation engine",
    r"i can'?t derive or analyze",
    r"i can'?t derive or analyse",
    # Original item-4 shape — toolbox hunts / approach talk
    r"\btoolbox\b",
    r"my current approach",
    r"no obvious \w{0,20}(tool|option|way)\b",
    r"(search|searching|look|looking|hunt|hunting) (through|for|in) "
    r"(my|the) (tools|tool list)",
    r"let me think about (how|which tool)",
))

_MATH_SIGNALS = re.compile(
    r"\b(solve|equation|integral|integrate|derivative|differentiate|"
    r"gradient|divergence|curl|calculus|vector field|line integral|"
    r"surface integral|differential|theorem|proof|matrix|matrices|"
    r"maths?|homework|problem set|work out|derive)\b",
    re.IGNORECASE)


def looks_like_deliberation(text: str) -> bool:
    """True when *text* is process narration, not an answer."""
    if not text:
        return False
    return any(p.search(text) for p in _PATTERNS)


# v5.53 (F4): the POST-HOC shape — a closing reply whose
# substance is the automation's own process (connection
# struggles, validation errors, retries, closures) instead
# of the task's outcome. Kept as its own list: these markers
# describe what HAPPENED to the tooling, and a legitimate
# outcome report ("the song is playing", "the file is open")
# contains none of them. One accepted trade-off, on record:
# "re-established the connection" is pinned even though a
# device-repair outcome could phrase itself that way — the
# guard's cost there is one corrective retry (whose clean
# answer then delivers), never a wrong delivery.
_PROCESS_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    # L6 — the "Automation Hiccups" essay, its own phrases
    r"browser automation",
    r"unexpected hurdles",
    r"hurdles with the browser",
    r"the pauses you observed",
    r"re-?establish\w* (the |a successful |my )?connection",
    r"refining (my |the )?commands became necessary",
    r"unexpected browser closures",
    r"worked through these challenges",
    r"contributed to significant latency",
))


def looks_like_process_summary(text: str) -> bool:
    """True when *text* reports the automation's process
    instead of the task's outcome (the post-tool shape)."""
    if not text:
        return False
    return any(p.search(text) for p in _PROCESS_PATTERNS)


def is_reasoning_task(user_text: str, reply_text: str = "") -> bool:
    """Item 9: the refused/narrated task is heavy reasoning
    (maths, analysis, multi-step derivation). The USER's words
    are the primary signal; the reply is a fallback."""
    return bool(_MATH_SIGNALS.search(user_text or "")
                or _MATH_SIGNALS.search(reply_text or ""))


#: The one corrective retry instruction (plain form).
CORRECTIVE = (
    "[Deliberation guard] Your previous reply narrated your own "
    "process instead of answering, so it was withheld. Reply "
    "again now: if a tool can do the task, call it; if you can "
    "answer directly, answer directly. Never describe searching "
    "for tools, your approach, or what you are about to tell "
    "the user — just do it or say it.")

#: Item 9's reasoning branch, appended for maths-shaped tasks:
#: work it, verify it with the tools that exist, and only then
#: escalate — in one sentence, the lite-tier honesty form.
REASONING_SUFFIX = (
    " This is a reasoning task: work it step by step and give "
    "the worked answer. Verify the computation with the tools "
    "you actually have — quick_math for arithmetic, run_command "
    "with Python/sympy for symbolic checks (their normal "
    "confirmations still apply). If — and only if — you "
    "genuinely cannot carry it, say exactly one sentence naming "
    "the limit and the remedy (switching to a stronger brain); "
    "never a paragraph about your capabilities, and never "
    "'beyond my capabilities' as a first move.")

#: Delivered when the retry is STILL deliberation: one honest
#: persona line. The raw deliberation never reaches the user.
PERSONA_FAILURE = (
    "I don't have a clean answer for that just now, sir — put "
    "it to me once more, or another way, and I'll take it on "
    "directly.")


def corrective_for(user_text: str, reply_text: str = "") -> str:
    """The corrective for this turn: the base instruction, plus
    item 9's reasoning branch when the task is maths-shaped."""
    if is_reasoning_task(user_text, reply_text):
        return CORRECTIVE + REASONING_SUFFIX
    return CORRECTIVE


#: v5.53 (F4): the post-tool corrective — outcome only, at
#: most two sentences, with a hard ban on narrating the
#: tooling. The shape the user actually needs after a task:
#: what was asked, what state it is in now, what they can do.
OUTCOME_CORRECTIVE = (
    "[Deliberation guard] Your previous reply reported the "
    "PROCESS — the automation's own struggles, connections "
    "and retries — instead of the outcome, so it was withheld. "
    "Reply again now with the outcome only, in at most two "
    "sentences: what was asked, what state it is in now (done "
    "or not done, playing or not playing), and what the user "
    "can do next if anything. Never narrate the tooling, the "
    "connection, validation errors or retries.")


def outcome_corrective_for(user_text: str = "",
                           reply_text: str = "") -> str:
    """The corrective for a post-tool process summary: the
    outcome form, always (signature mirrors corrective_for
    so the client call sites stay parallel)."""
    return OUTCOME_CORRECTIVE
