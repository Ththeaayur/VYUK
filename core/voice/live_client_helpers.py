from __future__ import annotations

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

# INTENTIONAL (skills split B47): remaining helpers moved from
# live_client.py (_bump_epoch, validate_api_key, _chunk_rms,
# _offered_declarations) + the header constants the class methods
# use. Imports _classify_key_error from the B46 error-cost module
# (validate_api_key resolves it). NOTE: no config import —
# _offered_declarations takes config as a PARAMETER, so the old
# module-level config import is not a requirement of this module.
from .live_client_error_cost import _classify_key_error


# --- Connect budget (v5.44, BRAIN B2) ---------------------------------
# One total wall-clock budget for a whole cloud connect, across
# every candidate AND the proxy retry pass: the first candidate
# keeps the generous 25 s handshake, later candidates get 10 s
# each, and no attempt may start (or run) past the deadline.
CONNECT_BUDGET_S = 45.0


# LIVEFIX: how long a resume attempt that hits "already
# connected" waits before its cold retry, so the server has a
# chance to reap the stuck session. Capped by the shared connect
# deadline at the use site — never unbounded, never blocking.
_ALREADY_CONNECTED_BACKOFF_S = 5.0


# --- Response-plane watchdog (v5.56, Wave 2 RS-1) ------------------
# RS-1 REPLACES v5.54.1's bounded emergency watchdog with ONE
# watchdog — there are not two. What changed, exactly:
#   * silence threshold 45 s -> 7 s (the plan's ~7 s
#     response-silence trip: a Live session whose response
#     plane goes quiet shortly after voice is dead in fact,
#     and waiting 45 s to say so left the user talking to a
#     corpse for most of a minute);
#   * tick 5 s -> 1 s (a 5 s tick on a 7 s threshold would
#     fire anywhere between 7 s and 12 s; the 1 s tick fires
#     at ~7-8 s, which is what "~7 s" means);
#   * everything else UNCHANGED: the trip still requires
#     voice-level audio sent AFTER the server's last
#     response-plane activity (idle sessions never trip — no
#     voice since the last response means nothing is owed),
#     and tool-call messages still count as response-plane
#     activity (long tool executions never trip).
# What did NOT move: CONNECT_BUDGET_S stays 45 s (that is the
# connect budget, a different number that happens to match
# the old watchdog's), and _report_exhaustion's "45s connect
# budget" text still names the connect budget, not this one.
# The field failure this answers (v5.54.1, unchanged): a Live
# session that "works for a few minutes, then stops hearing"
# while the UI still shows connected — the server goes away
# with no close frame (a half-open socket: receive() parks
# forever, the send loop feeds the void, is_connected stays
# True), and Gemini's goAway signal is known-unreliable since
# Aug 2026, so the response-plane silence detector — not
# goAway, not any socket check — is the PRIMARY trip.
# goAway, when it does arrive, is handled opportunistically
# in _receive_loop (it ends the session for a resume
# reconnect); it is never depended on. Round-3 fix Q3
# (section B): the 7 s budget measured the wrong thing — it
# counted from the server's last response, so a single
# uninterrupted monologue over 7 s tripped mid-utterance
# (field: a 7.3 s turn killed twice, a 4.9 s turn works)
# although the session was alive: the server yields nothing
# while the user is still speaking, and only answers once the
# turn ends. The trip now ALSO requires voice quiescence — no
# voice-level audio sent for _WATCHDOG_VOICE_QUIET_S (5.0 s) —
# so the silence clock measures server silence AFTER the user
# finished speaking, not turn length. The 7 s budget itself
# does NOT move: a dead session still dies (after a short
# turn ~10 s; after a long monologue, turn end + 5 s), and a
# genuinely stuck session can never run forever — the bound
# is turn end + 5 s, finite. What the quiescence costs: a dead
# session whose user speaks one continuous monologue takes
# that long to detect (indistinguishable from a live one
# while the user is still speaking — the server's correct
# behaviour in both cases is silence). 5.0 s covers the
# server's turn-end detection (~1 s) + first-response latency
# (typically 1-3 s) with margin; a false trip now needs model
# latency after turn end over 5 s. The same guard applies to
# the audible rule below (same shape, same bug for a quiet
# monologue over 30 s); the send-failure rule is untouched
# (not a silence rule). If field data shows the model
# routinely takes longer than 5 s to first respond AFTER the
# user stops speaking, the QUIET bound — not a second
# watchdog — is what moves. Round-3 fix Q8: the same 7 s
# budget is the typed-turn rule's budget too — a typed message
# unanswered for 7 s ends the session (LIVE_DEAD); a typed
# turn is complete at send time, so it carries no quiescence
# condition, and any yielded response after the text disarms
# it. Bracketed internal injections never stamp the text clock
# (see send_text), so a notice the model is told not to answer
# cannot arm it.
_WATCHDOG_SILENCE_S = 7.0


# Round-3 fix Q3: how long the user must have stopped sending
# voice-level audio before the silence rules may trip (see
# above). While voice is still arriving, the server's silence
# is correct behaviour, not death.
_WATCHDOG_VOICE_QUIET_S = 5.0


_WATCHDOG_TICK_S = 1.0


# v5.95 (micfix): a realtime audio send that never returns
# must not stall the sender forever — the watchdog only
# fires after voice WAS sent, so a hung FIRST send would
# leave a deaf zombie with working text. Sends past this
# bound are dropped with an error line and draining resumes.
_SEND_REALTIME_TIMEOUT_S = 15.0


# The house voice floor (core/audio.mic_status): RMS above
# this counts as voice, not room noise.
_VOICE_RMS_FLOOR = 0.008

# Mic remake (v6.3.x): the AUDIBLE floor — RMS above this
# is sound worth noticing even when it never crosses the
# voice floor (a half-open session's quiet voice, a
# low-gain mic). The response-plane watchdog's second rule
# keys on it: audible audio sent after the last response
# with 30 s of silence ends the session, so a quiet voice
# that the 7 s voice rule can never see still gets a
# reconnect instead of an indefinite "connected" corpse.
_AUDIBLE_RMS_FLOOR = 0.002
_WATCHDOG_AUDIBLE_SILENCE_S = 30.0

# Voice-control batch (2026-11-16, B1): the SEND-FAILURE
# bound — the response-plane watchdog's third rule. A send
# that fails continuously for this long (streak start, cleared
# by any successful send) ends the session, so a broken send
# path can never leave the user talking to a corpse that
# claims to listen: the old rules only watched successful
# sends, so a failing send stamped nothing and neither rule
# could ever fire.
_WATCHDOG_SEND_FAIL_S = 15.0


def _bump_epoch(reason: str) -> None:
    """v5.48 (programme item 20): advance the session epoch at a
    session end/start seam, voiding any confirmation approval
    still in flight from the previous session (core/session_epoch).
    Guarded: epoch bookkeeping never breaks a session."""
    try:
        from .. import session_epoch  # Phase 3 V1b: moved to core/voice/

        session_epoch.bump_epoch(reason)
    except Exception:  # noqa: BLE001
        pass


def validate_api_key(key: str, model: str) -> tuple[bool, str, str]:
    """Cheapest real validation of a Gemini key + Live model name.

    One metadata GET (client.models.get) proves the key authenticates
    AND the model exists, without opening a Live session. Returns
    (ok, kind, message) with kind one of:
    "ok" | "auth" | "model" | "quota" | "network" | "library" | "unknown".
    (Fix 5: "quota" is its own bucket — a 429 / RESOURCE_EXHAUSTED
    refusal used to read as "unknown" or "auth".) Sync — call it off
    the UI thread."""
    key = (key or "").strip()
    if not key:
        return (False, "auth", "No API key to test.")
    if genai is None:
        return (False, "library",
                "The Google library (google-genai) isn't installed in this build.")
    try:
        client = genai.Client(api_key=key)
        client.models.get(model=model)
        return (True, "ok", f"Model '{model}' is available.")
    except Exception as exc:  # noqa: BLE001
        return (False, _classify_key_error(exc), str(exc)[:300])


def _chunk_rms(data: bytes) -> float:
    """RMS of an int16 PCM chunk on the 0.0–1.0 scale (the
    house voice floor in _VOICE_RMS_FLOOR reads this scale).
    Stdlib only — the send loop stamps voice activity per
    chunk and must not drag numpy into the Live path."""
    import array

    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0


def _offered_declarations(tools_obj, config):
    """The declarations a Live connect offers, plus whether
    the session is routed and the profile label for the log
    (domain-router redefinition, 2026-10-27 — supersedes the
    INV-1 fallback split's fitted shapes):

      * router-off mode (TOOL_ROUTER=0): the configured
        profile's resident set + the domain routers,
        WITHOUT the use_toolbox declaration. TOOL_ROUTER
        now controls only the use_toolbox meta-tool;
        domain routing is structural and present in every
        profile. No fitted full list is flown anywhere, so
        router-off can no longer refuse for budget reasons.
      * every profile (assistant, desktop, full): the
        profile's declarations from profile_declarations()
        — resident + domain routers + use_toolbox. "Full"
        means complete coverage through routers, not direct
        declarations for every tool.
      * router failure (any profile): the desktop set —
        built without the fitted list, and therefore the
        most robust set available in exactly the failure
        case — routed, with the router prompt.

    Raises ValueError only for a registry so broken that
    even the desktop set cannot build (propagates to
    connect's ERROR status). The fitted full list is never
    consulted here."""
    from ..funnel import tool_profiles as _profiles_mod  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: tool_profiles moved to core/funnel/
    from ..funnel import toolbox as _toolbox_mod  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

    if not getattr(config, "tool_router", True):
        profile = _profiles_mod.current_profile_name(config)
        declarations = _profiles_mod.profile_declarations(
            tools_obj, profile, include_toolbox=False)
        return declarations, False, "router-off"
    profile = _profiles_mod.current_profile_name(config)
    try:
        usage = None
        if profile == "assistant":
            try:
                usage = _toolbox_mod.activity_usage_counts()
            except Exception:  # noqa: BLE001
                usage = None
        declarations = _profiles_mod.profile_declarations(
            tools_obj, profile, usage_counts=usage)
        return declarations, True, profile
    except ValueError as e:
        if profile == "full":
            raise ValueError(
                "Tool offer refused for profile 'full': "
                f"{e}") from e
        error = e
    except Exception as e:  # noqa: BLE001
        if profile == "full":
            raise
        error = e
    # Router failure fallback (any non-full profile): the
    # desktop set, which needs no fitted list.
    print(f"[Live] Router unavailable ({error}); offering "
          f"the desktop set instead.")
    declarations = _profiles_mod.desktop_declarations(tools_obj)
    return declarations, True, "desktop (router fallback)"
