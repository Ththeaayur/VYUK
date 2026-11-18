# Part of the core/w7_wiring.py split (skills split B13).
# Family: constants.

WIRING_VERSION = "conversion-w7"

ANTICIPATION_OPT_IN_OWED = (
    "Anticipation capture has no live opt-in/indicator grant yet: "
    "the watcher feed is offered to the CA-1 session on every "
    "tick, but the session refuses it until a live caller grants "
    "a per-session opt-in AND sets an indicator kind — inventing "
    "that grant (e.g. reusing the N13 foreground_enabled seam) "
    "would bypass the S-6 capture posture, so it is not done here."
)

APPRISE_CONFIG_OWED = (
    "Apprise has no per-service configuration in this tree: "
    "notify_via_apprise() validates target + pin, but with no "
    "caller-supplied services mapping there is nothing to send "
    "to — the user's own service URLs are owed, never invented."
)

HA_EXECUTION_OWED = (
    "Home Assistant has no live execution path here: "
    "home_assistant_request() validates LAN + scope + action and "
    "returns the request dict, but it performs no network call "
    "and no state change — the caller's injected execution path "
    "and its per-room allowlist are owed."
)

HAMCP_EXECUTION_OWED = (
    "ha-mcp tools are not in the tool registry and gain no "
    "declaration here: route_hamcp_tool() returns the toolbox "
    "routing decision only — the toolbox's own confirmed "
    "execution path for a routed ha-mcp tool is owed."
)

SOS_SENDER_OWED = (
    "SOS has no configured sender: send_sos_via() with no sender "
    "announces delivery FAILURE honestly — a real sender "
    "(channel + delivery confirmation) is owed before SOS can "
    "ever report success."
)

MEETING_TRANSCRIPT_OWED = (
    "Meeting notes have no live transcript source and no live "
    "opt-in/indicator grant: meeting_notes_for() refuses until "
    "a caller opts in, lights the indicator, and supplies a "
    "transcript — all three are owed."
)

MONEY_ENTRY_OWED = (
    "Money paperwork has no live entry point (no tool, no UI): "
    "the local-only organisation/flagging functions are wired "
    "and tested, but nothing in the live app calls them yet."
)

PRESENCE_ACTIVATION_OWED = (
    "Presence lock is DISABLED by default: the watcher-tick "
    "poller exists and the idle-probe signal exists, but no "
    "live tick evaluates the lock until enable_presence_lock() "
    "is called — default-on locking of a user's workstation is "
    "an owner decision, not something this layer assumes."
)

DAILY_ADD_TRIGGER_OWED = (
    "Daily alarms have a live tick but no live add trigger: "
    "RoutineScheduler.tick() fires due alarms and reports "
    "missed ones, but no voice phrase, tool, or UI creates an "
    "alarm yet — that trigger is owed."
)
