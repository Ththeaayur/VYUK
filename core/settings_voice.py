"""Settings redesign (2026-11-16): the voice settings-change
protocol (§5) and the session-grant voice flow (§8/R1).

Core only, stdlib at the top; app_settings / permission_grants /
tool_receipts / live_origin are imported lazily inside the
functions, the codebase's established pattern for core modules
that must never cycle with the funnel.

§5 — one protocol for voice settings changes, no exceptions:
  propose (never silently apply) -> visible confirm (spoken
  "yes" is enough, R2 — the proposal is spoken in plain words
  AND shown on screen; the announcement + one-word/one-tap
  undo is the safety, not a tap gate) -> apply + announce +
  undo (one tap / spoken "undo that", for the next minute) ->
  Activity receipt (what changed, from what to what, how).
  Never from a bare transcript: the change applies to the
  PROPOSED, CONFIRMED change only.
  Blanket GRANTS ("turn everything on") are refused, always.
  "Turn everything OFF" is the one exception — R1 rules deny
  always free by voice, instantly, no confirm — so a deny-all
  revokes every session grant. (§5's "on/off refused, always"
  is overridden for the OFF direction by the later, explicitly
  ruled R1; the ON direction stays refused.)

§8/R1 — session grants (made on screen in Settings), deny
always by voice (his yes). Loophole batch 4 (L-28): voice
grants are REFUSED — a permission grant is an owner act, and
live origin only proves a live session exists, not WHO is
speaking. grant_session_by_voice refuses in words and names
Settings → Permissions; the store-level session grant
(grant_session_only, made on screen) is what the funnel and
the capture guard consult:
  * Standing switches NEVER move by voice (D8 stands). The
    store is never written here — grant_session_only is the
    in-memory layer, and set_permission_state is never called.
  * A session grant widens the fail-closed DEFAULT read — no
    record yet, so mic/camera read "denied" — but never a
    STORED denial (D7/D5: a stored denial is never widened;
    the mic first-use ask is the precedent: "Allow once" opens
    the live mic on a fresh install despite that read). A grant
    is consulted only with live origin (is_live_origin).
  * Every session grant is loud: the GUI strip shows it while
    it lasts with a "keep it on" deep link, and a receipt lands
    in Activity.
  * Deny is always free by voice, instantly, no confirm: it
    clears the session grant. When the standing switch is
    GRANTED, voice cannot flip it (D8) — the deny says so
    plainly and offers the Settings deep link instead of
    pretending the switch moved.
  * The funnel consults a session grant ONLY through
    permission_grants.session_grant_allows (live origin + no
    stored denial), and NEVER for microphone — the mic keeps
    its D2 carve-out: funnel mic tools refuse until Settings
    flips; the mic session grant opens only the live-voice
    stream, as today (audio_devices_mic already consults
    has_session_grant itself).

The funnel handler (core/funnel/tools_handlers_command_url.py)
routes live-origin app_settings calls through here; chat
(non-live) "set" applies directly as it has since v5.52 —
typed, visible, user-initiated — so the existing tool
contract and its tests are untouched.
"""

from __future__ import annotations

import re
import time
import uuid

#: Undo window for a voice-applied change (thinking doc §5).
UNDO_WINDOW_S = 60.0

#: One pending proposal at a time: a new proposal replaces the
#: old one (the old one is then dead — confirming it answers
#: "there is no change waiting").
_PENDING: dict | None = None

#: The last voice-applied change, for undo:
#: {"key", "label", "old", "new", "expires_at"}.
_LAST_CHANGE: dict | None = None

#: One pending GUI action for the Assistant strip to carry out
#: once ("open_connect" | "open_settings" (+ "card")).
_GUI_ACTION: dict = {}

#: The browser was refused for lack of a session — the funnel's
#: guided no-session refusal just fired (first-run onboarding,
#: 2026-11-16). The Assistant strip reads this and offers
#: "Connect my browser". In-memory only, session-scoped — never
#: persisted, never a permission, never a flip: it only notes
#: that a browser call just refused, so the strip can offer the
#: way back.
_BROWSER_NO_SESSION: bool = False


def note_browser_no_session() -> None:
    """Record that a browser call just refused for lack of a
    session (the strip's way back). Never raises."""
    global _BROWSER_NO_SESSION
    try:
        _BROWSER_NO_SESSION = True
    except Exception:  # noqa: BLE001 — the note never breaks a refusal
        pass


def browser_no_session_active() -> bool:
    """True when the strip should offer the browser connect."""
    try:
        return bool(_BROWSER_NO_SESSION)
    except Exception:  # noqa: BLE001 — fail closed, the strip hides
        return False


def clear_browser_no_session() -> None:
    """Drop the no-session note — a browser call succeeded, or
    the user opened the connect flow. Never raises."""
    global _BROWSER_NO_SESSION
    try:
        _BROWSER_NO_SESSION = False
    except Exception:  # noqa: BLE001
        pass


#: The first-launch browser offer is live this session
#: (rethink Build 5, §9.1): the Home strip shows ONE quiet
#: "Connect my browser — one tap, about a minute" offer on the
#: first launch after the update, dismissible, never repeated.
#: In-memory only, session-scoped: True from the first paint of
#: the offer until it is dismissed or the browser connects. The
#: DURABLE half is the QSettings key
#: ``browser/connect_offer_shown`` (config_spec) — True once the
#: offer has been SHOWN, so a later launch never repeats it even
#: if this launch ended without a dismiss or a connect. The
#: refusal + strip button stays the durable way back.
_BROWSER_OFFER_ACTIVE: bool = False


def browser_offer_shown() -> bool:
    """True once the first-launch offer has been shown (durable
    QSettings flag). Never raises — a broken store reads as not
    shown, so the offer can still appear once."""
    try:
        from .config_spec import qsettings_get as _get

        return bool(_get("browser/connect_offer_shown"))
    except Exception:  # noqa: BLE001 — a broken store reads False
        return False


def note_browser_offer_shown() -> None:
    """Record that the first-launch offer just painted: the
    durable flag (never repeated on a later launch) and the
    session flag (keeps it visible within this launch until
    dismissed or connected). Never raises — a failed persist
    still sets the session flag, so this launch behaves."""
    global _BROWSER_OFFER_ACTIVE
    try:
        _BROWSER_OFFER_ACTIVE = True
    except Exception:  # noqa: BLE001 — the note never breaks a paint
        pass
    try:
        from .config_spec import qsettings_set_noted as _set_noted

        # Round-3 item 14: a failed persist is logged + recorded
        # (feed marker) — without it the offer repeats at the next
        # launch with no trace of why. The note never raises, so
        # the session flag above still stands either way.
        _set_noted("browser/connect_offer_shown", True)
    except Exception:  # noqa: BLE001 — a failed persist keeps memory
        pass


def browser_offer_active() -> bool:
    """True while this launch's offer is still live (shown and
    neither dismissed nor connected)."""
    try:
        return bool(_BROWSER_OFFER_ACTIVE)
    except Exception:  # noqa: BLE001 — fail closed, the strip hides
        return False


def clear_browser_offer() -> None:
    """Drop this launch's offer — dismissed, or the browser
    connected. The durable flag is untouched: the offer never
    repeats on a later launch either way. Never raises."""
    global _BROWSER_OFFER_ACTIVE
    try:
        _BROWSER_OFFER_ACTIVE = False
    except Exception:  # noqa: BLE001
        pass

#: Permission words the voice router recognises, in the order
#: the router tries them (longest first, so "home devices"
#: wins over "devices" — there is no bare "devices" id).
_PERMISSION_WORDS: tuple[tuple[str, str], ...] = (
    ("home_devices", "home devices"),
    ("microphone", "microphone"),
    ("microphone", "mic"),
    ("camera", "camera"),
    ("camera", "webcam"),
    ("screen", "screen"),
    ("files", "files"),
    ("browser", "browser"),
    ("browser", "chrome"),
)

#: Words that name a secret / connection request.
_SECRET_WORDS = (
    "api key", "apikey", "api_key", "gemini key", "groq key",
    "cloud key", "add a key", "add my key", "paste a key",
    "paste my key", "connect to the cloud", "connect to cloud",
    "fix the connection", "fix my connection", "cloud",
    "connect",
)

#: Words that name a destructive request (Settings only, typed).
_DESTRUCTIVE_WORDS = (
    "factory reset", "wipe", "erase everything", "erase all",
    "delete everything", "delete all my data", "delete all",
    "revoke all", "revoke everything", "reset everything",
    "reset all settings",
)

#: Grant-direction words / deny-direction words in a value.
_GRANT_WORDS = ("on", "enable", "enabled", "allow", "grant",
                "granted", "yes", "turn on")
_DENY_WORDS = ("off", "disable", "disabled", "deny", "denied",
               "revoke", "revoked", "no", "turn off")


# ------------------------------------------------------------------ receipts

def _record(tool: str, args: dict, status: str, summary: str,
            *, dispatched: bool = True) -> None:
    """Best-effort Activity receipt. Never raises, never breaks
    the call it records — a failed receipt is a degraded
    observability flag, not a refusal."""
    try:
        from . import tool_receipts as _tr

        receipt = _tr.try_record_attempt(tool=tool, args=args)
        _tr.try_record_outcome(receipt, status=status,
                               summary=summary, dispatched=dispatched)
    except Exception:  # noqa: BLE001 — receipts never break a call
        pass


# ------------------------------------------------------------------ proposal

def _validate_value(key: str, value: str):
    """Resolve a proposed value to its canonical form, or an
    error string. Mirrors core/app_settings.set_setting's
    validation without applying anything (the same helpers,
    so the two can never disagree about what is valid)."""
    from . import app_settings as _as

    value = (value or "").strip()
    if key in ("earcons", "suggestions", "tts_loudness",
               "voice_auto_switch"):
        wanted = _as._parse_bool(value)
        if wanted is None:
            return None, (f"'{value}' isn't on or off — tell me "
                          "'on' or 'off' to change it.")
        return ("on" if wanted else "off"), None
    if key == "theme":
        wanted = _as._match_name(_as.migrate_theme_name(value),
                                 _as._themes())
        if wanted is None:
            colour = _as._THEME_COLOUR_WORDS.get(value.lower())
            if colour and colour in _as._themes():
                wanted = colour
        if wanted is None:
            return None, (f"'{value}' doesn't name one theme "
                          "unambiguously. Choices: "
                          + ", ".join(_as._themes()) + ".")
        return _as.migrate_theme_name(wanted), None
    if key == "orb":
        wanted = _as._match_name(value, _as.ORB_STYLES)
        if wanted is None:
            return None, (f"'{value}' isn't an orb style I know. "
                          f"Choices: {', '.join(_as.ORB_STYLES)}.")
        return wanted, None
    if key == "live_voice":
        from .config import config as _config

        offered = [str(v) for v in getattr(
            _config, "available_voices", [])]
        wanted = _as._match_name(value, offered)
        if wanted is None:
            return None, (f"'{value}' isn't one of the Live voices. "
                          f"Choices: {', '.join(offered)}.")
        return wanted, None
    if key == "local_voice":
        roster = _as._kokoro_roster()
        for voice_id, voice_label in roster:
            if value.lower() in (voice_id.lower(),
                                 voice_label.lower()) \
                    or value.lower() == \
                    voice_label.split(" — ")[0].lower():
                return voice_id, None
        if re.fullmatch(r"[a-z]{2}_[a-z]+", value.lower()):
            return value.lower(), None
        return None, (f"'{value}' isn't a Kokoro voice I know. "
                      f"Choices: "
                      + ", ".join(v for v, _ in roster) + ".")
    if key == "language":
        from .languages import LANGUAGES, language_by_code

        entry = language_by_code(value)
        if entry is None:
            text = value.strip().lower()
            hits = [e for e in LANGUAGES
                    if text == e.display.lower()
                    or text in e.label.lower()]
            entry = hits[0] if len(hits) == 1 else None
        if entry is None:
            return None, (f"'{value}' doesn't name one language "
                          "unambiguously. Choices: "
                          + ", ".join(e.label for e in LANGUAGES)
                          + ".")
        return entry.label, None
    return None, f"I don't have a chat-settable setting called '{key}'."


def propose_setting_change(setting: str, value: str):
    """Propose a Class B change WITHOUT applying it.

    Returns (proposal, message). The proposal carries a stable
    id the confirm step takes; the message is the plain-words
    proposal the voice speaks AND the strip shows. A failed
    validation returns (None, the honest refusal) — nothing is
    proposed, nothing is stored.
    """
    global _PENDING

    from . import app_settings as _as

    raw = (setting or "").strip()
    if not raw:
        return None, ("Change which setting? The ones I can change "
                      "by voice are: "
                      + ", ".join(_as.SETTINGS.keys()) + ".")
    if _as._is_excluded(raw):
        return None, _as._exclusion_refusal(raw)
    key = _as._canonical_key(raw)
    if not key:
        return None, _as._unknown_setting(raw)
    value = (value or "").strip()
    if not value:
        return None, (f"{_as.SETTINGS[key][0]} is "
                      f"{_as._current(key)} right now. Tell me the "
                      f"new value — choices: "
                      f"{_as._choices_for(key)}.")
    wanted, error = _validate_value(key, value)
    if error is not None:
        return None, error
    old = _as._current(key)
    if old == wanted:
        return None, (f"{_as.SETTINGS[key][0]} is already {old} — "
                      "nothing to change.")
    proposal = {
        "id": uuid.uuid4().hex[:12],
        "key": key,
        "label": _as.SETTINGS[key][0],
        "old": old,
        "new": wanted,
    }
    _PENDING = proposal
    message = (f"Change {proposal['label']} from {old} to {wanted}? "
               "Say yes to apply it, or no to leave it as it is. "
               f"(Proposal #{proposal['id']} — quote it to confirm.)")
    # The proposal carries its own spoken message, so the Home
    # strip can show exactly what the voice said (pending_
    # proposal returns the dict, not the return tuple).
    proposal["message"] = message
    return dict(proposal), message


def pending_proposal() -> dict | None:
    """The pending proposal, or None. A copy — the store is
    never handed out."""
    return dict(_PENDING) if _PENDING is not None else None


def confirm_proposal(proposal_id: str) -> str:
    """Apply the pending proposal. The id must match the live
    proposal — a stale id (a replaced proposal) confirms
    nothing. Returns the announcement (applied + undo offer)
    or an honest failure."""
    global _PENDING, _LAST_CHANGE

    if _PENDING is None or str(proposal_id or "") != _PENDING["id"]:
        return ("There's no change waiting to confirm — tell me "
                "what to change and I'll propose it first.")
    proposal = _PENDING
    _PENDING = None
    from . import app_settings as _as

    result = _as.set_setting(proposal["key"], proposal["new"])
    text = str(result or "")
    if "unchanged" in text or "Couldn't" in text \
            or "isn't" in text or "doesn't" in text:
        return (f"I couldn't apply it — {text} Nothing was "
                "changed.")
    _LAST_CHANGE = {
        "key": proposal["key"],
        "label": proposal["label"],
        "old": proposal["old"],
        "new": proposal["new"],
        "expires_at": time.monotonic() + UNDO_WINDOW_S,
    }
    _record("app_settings",
            {"action": "set", "setting": proposal["key"],
             "value": proposal["new"], "via": "voice-confirm"},
            "succeeded",
            f"{proposal['label']}: {proposal['old']} → "
            f"{proposal['new']} (voice, confirmed)")
    return (f"{proposal['label']} is now {proposal['new']}. Say "
            "'undo that' or tap Undo — you've got a minute.")


def deny_proposal(proposal_id: str) -> str:
    """Decline the pending proposal. Nothing is applied, nothing
    is stored — the decline itself gets a receipt so Activity
    can tell a declined proposal from a never-proposed one."""
    global _PENDING

    if _PENDING is None or str(proposal_id or "") != _PENDING["id"]:
        return "There's no change waiting — nothing to decline."
    proposal = _PENDING
    _PENDING = None
    _record("app_settings",
            {"action": "set", "setting": proposal["key"],
             "value": proposal["new"], "via": "voice-decline"},
            "cancelled",
            f"{proposal['label']}: proposed "
            f"{proposal['old']} → {proposal['new']} (voice), "
            "declined — nothing changed",
            dispatched=False)
    return (f"Left as it is — {proposal['label']} stays "
            f"{proposal['old']}.")


def undo_available() -> dict | None:
    """The undoable change, with seconds left, or None when the
    minute has passed (an expired undo reads as no undo, never
    as a stale one)."""
    if _LAST_CHANGE is None:
        return None
    left = _LAST_CHANGE["expires_at"] - time.monotonic()
    if left <= 0:
        return None
    out = dict(_LAST_CHANGE)
    out["seconds_left"] = int(left)
    return out


def undo_last_change() -> str:
    """Restore the last voice-applied change, inside its minute.
    Returns the announcement or an honest nothing-to-undo."""
    global _LAST_CHANGE

    change = undo_available()
    if change is None:
        _LAST_CHANGE = None
        return ("Nothing to undo — either nothing changed by voice "
                "yet, or the minute has passed.")
    _LAST_CHANGE = None
    from . import app_settings as _as

    result = _as.set_setting(change["key"], change["old"])
    text = str(result or "")
    if "unchanged" in text or "Couldn't" in text \
            or "isn't" in text or "doesn't" in text:
        return (f"I couldn't undo it — {text}")
    _record("app_settings",
            {"action": "set", "setting": change["key"],
             "value": change["old"], "via": "voice-undo"},
            "succeeded",
            f"{change['label']}: {change['new']} → {change['old']} "
            "(voice undo)")
    return (f"Undone — {change['label']} is back to "
            f"{change['old']}.")


# ------------------------------------------------------------------ routing

def permission_named(text: str) -> str | None:
    """The permission id named in text, or None."""
    low = (text or "").strip().lower()
    for pid, word in _PERMISSION_WORDS:
        if word in low:
            return pid
    return None


def _direction(value: str) -> str | None:
    """'grant' | 'deny' | None from a value's words."""
    low = (value or "").strip().lower()
    if any(w in low for w in _DENY_WORDS):
        return "deny"
    if any(w in low for w in _GRANT_WORDS):
        return "grant"
    return None


def route_voice_settings_request(setting: str, value: str) -> dict:
    """Route one structured voice settings request.

    Returns {"kind", ...} — kind is one of:
      "class_b"  — a settable preference: {"proposal", "message"}
                   when a proposal was made, else {"message"}.
      "class_a"  — a permission flip: the grant/deny was carried
                   out, or {"action": "open_settings", "card",
                   "permission", "direction"} when voice may not
                   do it (stored denial, or no direction given).
      "class_c"  — a secret / connection: {"action":
                   "open_connect"} (also queued for the strip).
      "class_d"  — destructive: {"message"} refusal.
      "blanket"  — "turn everything on": {"message"} refusal.
      "always_allow" — a spoken "always allow": {"message"}
                   refusal naming the Always allow screen
                   (Build 4 §4.7, D8 — voice never sets one).
      "unknown"  — nothing recognised: {"message"}.
    Never raises, never applies a Class A/C/D change, never
    writes the durable store.
    """
    raw = (setting or "").strip()
    val = (value or "").strip()
    low = (raw + " " + val).lower()
    # Deny-all is free (R1 overrides §5 for the OFF direction);
    # a grant-all is refused, always.
    if "everything" in low or low.strip() in ("all", "all settings"):
        if _direction(val) == "deny" or "off" in low:
            return {"kind": "deny_all",
                    "message": revoke_all_session_grants()}
        return {"kind": "blanket",
                "message": ("I can't turn everything on at once — "
                            "a grant is per thing, not per "
                            "everything. Tell me one thing, like "
                            "“turn the camera on”.")}
    if any(w in low for w in _DESTRUCTIVE_WORDS):
        return {"kind": "class_d",
                "message": ("That one is Settings only, with a typed "
                            "confirmation — voice can't touch it. "
                            "Open Settings to do it.")}
    if any(w in low for w in _SECRET_WORDS):
        request_gui_action("open_connect")
        return {"kind": "class_c",
                "action": "open_connect",
                "message": ("I'll open the Connect screen — paste "
                            "the key there. I can't take a key by "
                            "voice; spoken keys leak into the room "
                            "and the transcript.")}
    # UI rethink Build 4 §4.7 (D8): a spoken "always allow"
    # is refused in words, naming the screen — voice can
    # never set, widen or restore an always-allow. This
    # check comes BEFORE the permission branch: "allow" is
    # a grant word, so without it "always allow the camera"
    # would silently become a session-only grant — a yes to
    # something the user did not ask for. The predicate is
    # always_allow.is_always_allow_utterance — the same one
    # the confirm dialog's voice hook uses, so both surfaces
    # refuse the same phrasings.
    from . import always_allow as _aa

    if _aa.is_always_allow_utterance(low):
        return {"kind": "always_allow",
                "message": _aa.ALWAYS_ALLOW_VOICE_REFUSAL}
    pid = permission_named(raw) or permission_named(val)
    pid = permission_named(raw) or permission_named(val)
    if pid is not None:
        direction = _direction(val) or _direction(raw)
        if direction == "grant":
            return {"kind": "class_a",
                    "message": grant_session_by_voice(pid)}
        if direction == "deny":
            return {"kind": "class_a",
                    "message": revoke_session_by_voice(pid)}
        request_gui_action("open_settings", card="Permissions",
                           permission=pid)
        return {"kind": "class_a",
                "action": "open_settings",
                "card": "Permissions",
                "permission": pid,
                "message": ("Voice can't flip that switch — say "
                            "“off” to revoke it for this session, "
                            "or open Settings → Permissions for the "
                            "permanent switch. Voice can't grant "
                            "it.")}
    if not raw:
        return {"kind": "unknown",
                "message": ("Tell me what to change — a setting "
                            "like the theme or the voice, a "
                            "permission like the camera, or the "
                            "connection.")}
    proposal, message = propose_setting_change(raw, val)
    if proposal is None:
        return {"kind": "class_b", "message": message}
    return {"kind": "class_b", "proposal": proposal,
            "message": message}


# ------------------------------------------------------------------ session grants

def grant_session_by_voice(permission_id: str) -> str:
    """Permission grants by voice are REFUSED (loophole batch 4,
    L-28 — the gate that grants itself).

    The old shape granted a permission "for this session only"
    when called from a live session — but live origin only proves
    a live session exists, not WHO is speaking. Any voice in the
    room could say "grant files" and then read and send. A
    permission grant is an owner act, so it is made on screen
    only (Settings → Permissions), never by voice — joining the
    safety-shaped settings, which were already Settings-page-only
    by design. The refusal names the screen and offers the
    Settings deep link. Revoking by voice stays allowed (it
    removes access — the safe direction, not a grant).
    """
    from . import permission_grants as _pg

    pid = str(permission_id or "").strip().lower()
    if pid not in _pg.PERMISSION_IDS:
        return f"There's no permission called '{permission_id}'."
    request_gui_action("open_settings", card="Permissions",
                       permission=pid)
    return (f"I can't grant {pid} by voice — permission grants "
            "are made on screen only, in Settings → Permissions. "
            "Open Settings to grant it there.")


def revoke_session_by_voice(permission_id: str) -> str:
    """Deny by voice: clear one session grant, instantly, no
    confirm. Always works. When the standing switch is
    GRANTED, voice cannot flip it (D8) — the message says so
    plainly and offers the Settings deep link."""
    from . import permission_grants as _pg

    pid = str(permission_id or "").strip().lower()
    if pid not in _pg.PERMISSION_IDS:
        return f"There's no permission called '{permission_id}'."
    _pg.revoke_session_grant(pid)
    _record("settings_voice",
            {"action": "revoke", "permission": pid, "via": "voice"},
            "succeeded",
            f"{pid} session grant cleared (voice deny)")
    if _pg.get_state(pid) == "granted":
        request_gui_action("open_settings", card="Permissions",
                           permission=pid)
        return (f"{pid} is off for this session — but it's still "
                "on in Settings, and voice can't flip that "
                "switch. Open Settings → Permissions to switch "
                "it off for good.")
    return f"{pid} is off for this session."


def revoke_all_session_grants() -> str:
    """Deny-all by voice ("turn everything off"): clear every
    session grant, instantly, no confirm (R1). Standing
    switches are untouched and the message says which ones
    are still on in Settings, if any."""
    from . import permission_grants as _pg

    held = active_session_grants()
    for pid in held:
        _pg.revoke_session_grant(pid)
    _record("settings_voice",
            {"action": "revoke_all", "via": "voice"},
            "succeeded",
            "All session grants cleared (voice deny-all)")
    still_on = [pid for pid in _pg.PERMISSION_IDS
                if _pg.get_state(pid) == "granted"]
    if held:
        tail = (" The Settings switches are untouched"
                + (" — still on there: " + ", ".join(still_on)
                   if still_on else "") + ".")
    else:
        tail = (" Nothing was granted for this session."
                + (" Still on in Settings: " + ", ".join(still_on)
                   if still_on else "") + ".")
    return "Everything is off for this session." + tail


def active_session_grants() -> list[str]:
    """Permission ids holding a session grant right now, in
    PERMISSION_IDS order. The GUI strip reads this."""
    from . import permission_grants as _pg

    return [pid for pid in _pg.PERMISSION_IDS
            if _pg.has_session_grant(pid)]


def session_only_grants() -> list[str]:
    """Session grants the strip may honestly call session-only,
    in PERMISSION_IDS order: active session grants whose
    durable state is NOT granted. A durable grant (e.g. via
    apply_mic_first_use_choice("always"), which also holds a
    session grant so the live mic can open) is not
    session-only — the Settings switch HAS moved — so the
    strip must not claim it. Read-only: the durable store is
    never written here (D8). The GUI strip reads this."""
    from . import permission_grants as _pg

    return [pid for pid in active_session_grants()
            if _pg.get_state(pid) != "granted"]


# ------------------------------------------------------------------ GUI actions

def pending_gui_action() -> dict | None:
    """The queued GUI action (open_connect / open_settings),
    or None. A copy — the store is never handed out."""
    return dict(_GUI_ACTION) if _GUI_ACTION else None


def clear_gui_action() -> None:
    """Drop the queued GUI action (the strip calls this after
    carrying it out once)."""
    _GUI_ACTION.clear()


def request_gui_action(action: str, card: str | None = None,
                       permission: str | None = None) -> None:
    """Queue a GUI action (the strip consumes it once)."""
    _GUI_ACTION.clear()
    _GUI_ACTION["action"] = str(action or "")
    if card:
        _GUI_ACTION["card"] = str(card)
    if permission:
        _GUI_ACTION["permission"] = str(permission)


def reset_for_tests() -> None:
    """Drop all in-memory protocol state (tests only)."""
    global _PENDING, _LAST_CHANGE

    _PENDING = None
    _LAST_CHANGE = None
    _GUI_ACTION.clear()
    clear_browser_no_session()
    clear_browser_offer()
