"""w8_wiring part module (skills split B14): w8_wiring_gates."""
from __future__ import annotations


WIRING_VERSION = "conversion-w8"



# --- Owed decisions, stated in code so they cannot drift -----------
# Teach trigger decision 2026-11-03 (code-rulings delegation):
# the VOICE trigger exists and is the Live router path — the
# route_intelligence domain router carries all three teach tools, and
# use_toolbox keyword ranking surfaces them for canonical
# phrases ("record a sequence" ranks teach_record_sequence
# first). The Skills catalogue lists them (discovery-only, as
# for every tool — it invokes nothing). No GUI button invokes
# them: a record dialog would need a full step editor (tool
# picker + args + checks), which is a showcase, not the
# minimal surface, and the voice path is the product's
# primary trigger pattern. What remains owed is ONLY live
# demonstration capture, gated below — not a trigger gap.
TEACH_LIVE_CAPTURE_GATE = (
    "Live demonstration capture (watching the user act) is "
    "GATED, not built: it needs (a) a user-action observer "
    "(a Windows input + window-event watcher — none exists "
    "in-tree: input release is SendInput-only, senses "
    "watchers log timeline notices, perception is snapshots, "
    "not a continuous event stream), (b) an event-to-funnel-"
    "tool mapper (none exists), and (c) field validation on "
    "real Windows. The action journal cannot substitute: its "
    "entries carry an args hash + a sanitized truncated "
    "preview, never raw args, and teach steps need exact args "
    "plus a verify check — converting would invent both. "
    "Until the gate clears, spell-out record/replay is the "
    "shipped teach path."
)


SECOND_OPINION_TRIGGER_OWED = (
    "Second-opinion trigger RULED 2026-11-05 (owner, elegant design "
    "approved): a step triggers second opinion ONLY when BOTH hold — "
    "a destructive word AND a side-effecting tool (coordinator "
    "_second_opinion_check: tool not in _READ_ONLY_TOOLS, plus a "
    "destructive menu leaf or a destructive first word in the label; "
    "read-only tools never qualify). The coordinator auto-invoke is "
    "wired pre-dispatch and tested (tests/test_second_opinion_"
    "autoinvoke.py); the explicit opt-in request_second_opinion() path "
    "is preserved; questions come from the step label/menu leaf only, "
    "never args; disagreement/unavailable pauses with both views and "
    "never auto-resolves (resolved_answer always None). PRODUCTION "
    "STATUS (companion-session capability, 2026-11-05): the live "
    "session starts a provider-blind companion holder alongside "
    "the primary (core/brain/companion_sessions.py) holding the "
    "(Groq, local) pair, and the TurnCoordinator construction site "
    "supplies it as second_opinion_brains when both legs are real "
    "— so the check runs by default for the ruled conjunction. "
    "The pair exists only when both legs are real: the Groq leg "
    "needs a configured Groq key (BYOK — no key means no Groq "
    "leg), and the local leg needs the landed local model "
    "preloaded in a separate session (no model means no local "
    "leg). Either leg missing means the pair is None and the "
    "check stays OFF in production exactly as before — "
    "fail-closed, no silent degradation. The holder never "
    "disturbs the primary inner, its fallback state, or the warm "
    "standby. The owner ruling (cloud + local pair, default-on, "
    "2026-11-05) stands; it is gated, not re-decided. Name kept "
    "for grep stability."
)


SLEEPTIME_IDLE_TRIGGER_OWED = (
    "Sleeptime idle trigger BUILT 2026-11-03 (name kept for "
    "grep stability): the watcher away episode (idle >= 20 "
    "min, WatcherEngine._watch_idle) fires one sleeptime pass "
    "through the engine's sleeptime_hook, wired in "
    "gui/window_memory._init_watchers to "
    "w8_wiring.maybe_sleeptime_on_idle. What remains owed is "
    "the candidate/heat source — see SLEEPTIME_CANDIDATE_GATE."
)


SLEEPTIME_CANDIDATE_GATE = (
    "Sleeptime passes write 0 until a candidate/heat source "
    "exists: nothing in the tree bumps heat "
    "(sleeptime.bump_heat has no callers) and no caller "
    "supplies candidates to run_sleeptime_pass, so idle_pass "
    "finds nothing heat-eligible to promote. Unblocker: a "
    "recall/heat path (what counts as a recall hit, decided "
    "as its own item) plus a feed-to-candidate bridge. "
    "Shipped path until then: the trigger fires the pass on "
    "every away episode; the pass scans the store, reports "
    "expired counts, and honestly writes 0."
)


LEARNING_EXPIRY_OWED = (
    "Per-row expiry dates are owed from the learning-feed API: "
    "feed records carry timestamp + kind only, and only inferred "
    "records expire (90-day kind TTL). No expiry date is invented "
    "for any other kind."
)



#: Refusal markers, mirroring planner._REFUSAL_MARKERS (a
#: success-shaped refusal is NOT a success).
_REFUSAL_MARKERS = (
    "Blocked by VYUK's trust layer",
    "Refused:",
    "needs confirm=true",
    "Ask again with confirmation",
    "needs the user's OK in VYUK's window",
)



#: Supplement caps: learning + sleeptime lines ride inside the
#: prompt's memory budget, never as a second budget.
_MAX_SUPPLEMENT_CHARS = 2000


_MAX_LEARNING_LINES = 20


