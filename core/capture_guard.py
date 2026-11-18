"""
Capture guard for VYUK (v5.48, programme item 21 — core half).

Before this module, sense captures (screen, camera, microphone)
ran with no regard for WHAT was in front of the user: a screenshot
taken while a password manager or a banking page held the
foreground shipped those pixels to the brain, a webcam snapshot
or a microphone recording started on a model call alone, and
nothing on screen said a capture was live. This module is the
one place the capture rules now live:

  (a) Sensitive-window exclusion — a foreground denylist
      (password managers, authenticators, private-browsing
      windows, banking surfaces). foreground_capture_block()
      returns an honest reason string when the foreground window
      is denylisted, else None. The denylist applies to EVERY
      capture path — plain screenshots, screen_look / OCR frames
      and screen-watch frames included — and it is checked FIRST,
      before any approval below: an approval can never authorise
      capturing a denylisted window.

  (b) Per-session opt-in for captures that persist or sense
      privately — camera, microphone, and screenshots SAVED to a
      file. ensure_capture_allowed(kind) applies the configured
      policy (capture_policy: "ask-once" by default, "always-ask"
      or "pre-allowed"), asking the human through
      core.confirm_gate when the policy says to ask. The plain
      screenshot LOOK (take_screenshot with no save_path) is
      Tier-0 observe under the v5.46 design and deliberately does
      NOT go through here — it gains no ask; only the denylist
      (a) applies to it. Approvals are per kind and per session;
      note_new_session() (alias reset_session(), for the GUI's
      session-end hook) rotates the session token and clears
      them. DENIAL POLICY (a deliberate choice, v5.48): a denial
      is NOT remembered — the next capture of that kind asks
      again. A remembered denial would silently brick the camera
      for the rest of a session after one misclick, while a
      remembered approval is the whole point of "ask-once".

  (c) A capture indicator registry: handlers wrap the live
      capture in note_capture_started / note_capture_ended, and
      the registered listener (the GUI's on-screen "capturing"
      indicator) hears fn(kind, active) on every transition.
      Overlapping captures of one kind are counted, so the
      indicator only clears when the last one ends. Nothing here
      ever raises into a caller.

Foreground probe: the default probe composes the two seams the
codebase already trusts — core.input_guard._foreground_process_name
(the foreground process probe the shell gate uses) and
core.desktop.uia.foreground_title (the foreground title reader) — because
the denylist needs BOTH halves (process names AND title keywords)
and neither seam alone carries both. Suites replace the whole
probe with set_probe(); off Windows (or when neither seam can
answer) the probe yields (None, None) and nothing is blocked —
"unknown foreground" is never treated as "sensitive foreground".

Config is read DYNAMICALLY (this module never edits
core/config.py): capture_policy, capture_denylist_processes and
capture_denylist_title_keywords are read via getattr from
core.config (the module, then its `config` object) on every
call, falling back to the defaults below when unset. A configured
denylist REPLACES the default list for that half.

v5.56 (Wave 2, Stream G — S-6 machinery extension; the v5.48
behaviour above is unchanged):
  (d) Sensitive-CONTENT denylist — the window denylist (a) guards
      WHERE a capture looks; this guards WHAT a capture-derived
      span may carry. contains_sensitive_content() flags a
      Verhoeff-valid Aadhaar number (v5.70, LA-6 — see section (d))
      and an OTP code in keyword context
      (LA-2 brief: the S-6 denylist gains Aadhaar/OTP patterns);
      content_capture_block() phrases the refusal, and check_text()
      is the guided-flow / screen-sight hook returning that
      refusal or None. A match REFUSES — the text is dropped,
      never redacted-and-continued.
  (e) Evidence-span gate — gate_evidence_span() / 
      evidence_span_allowed() are the primitives a span emitter
      calls BEFORE emitting a capture-derived span. The gate is
      prompt-free (it never asks the human mid-pipeline): it checks
      the foreground denylist, then the content denylist on the
      span's text, then — for capture kinds that need opt-in — that
      the session already holds an approval (recorded when the
      capture itself was allowed). On deny the span is DROPPED, not
      redacted-and-emitted, and the caller may record only a
      "blocked-sensitive" counter — never the span text.
      WIRING (coordinator-owned; this module only provides the
      primitives): the OB-1 span emitter must call
      gate_evidence_span() immediately before emitting any
      capture-derived span. The existing wired points are unchanged:
      screen_watch.tick() checks the foreground denylist per poll,
      and vision captures check it before grabbing. Future capture
      surfaces wire through ensure_capture_allowed() with the new
      kinds "occluded_screenshot" (PC-S2 occluded-window capture,
      Wave 5 bake-off — machinery only here) and "activity_stream"
      (CA-1, Wave 7) — same denylist-first + opt-in posture.
      Non-capture spans (tool-funnel spans etc.) NEVER call this
      gate; it is for capture-derived spans only, and an unknown
      source is denied fail-closed.
"""

from __future__ import annotations

import threading
import uuid

from .state_bus_producers import (
    emit_capture_finished,
    emit_capture_started,
)

# ---------------------------------------------------------------------------
# (a) The sensitive-window denylist — defaults
# ---------------------------------------------------------------------------

# Process names (compared lowercased, ".exe" stripped): password
# managers and authenticators, plus standard personal-finance /
# banking desktop apps whose windows hold credentials or account
# data by design.
DEFAULT_DENYLIST_PROCESSES = frozenset({
    "1password", "1password 7", "1password 8",
    "lastpass", "bitwarden", "keepass", "keepassxc",
    "dashlane", "nordpass", "roboform", "authy",
    "enpass", "keeper", "protonpass", "proton pass",
    "sticky password", "true key",
    # personal finance / banking companions
    "quicken", "moneydance", "gnucash",
})

# Title keywords (compared lowercased, substring): private-
# browsing chrome, vault web pages, and banking surfaces.
DEFAULT_DENYLIST_TITLE_KEYWORDS = frozenset({
    "password manager", "incognito", "inprivate",
    "private browsing", "private window",
    # vault / authenticator pages by name (browser titles carry
    # the app name when the vault is a web page)
    "1password", "lastpass", "bitwarden", "keepass", "dashlane",
    "password vault", "authenticator",
    # banking
    "online banking", "internet banking", "bank login",
    "banking login", "bank sign in", "bank sign-in",
    "online bank", "my bank account",
})

_CAPTURE_KIND_LABELS = {
    "camera": "Camera",
    "microphone": "Microphone",
    "screenshot_save": "Screenshot save",
    # GAPS-20 (D-10): the meeting's system-audio track — the other
    # side of the call, captured via the loopback session.
    "system_audio": "System audio",
}

_CAPTURE_KIND_TOOLS = {
    "camera": "webcam_snapshot (camera capture)",
    "microphone": "record_audio (microphone capture)",
    "screenshot_save": "take_screenshot (screenshot save)",
    "system_audio": "start_meeting (system-audio capture)",
}

_POLICIES = frozenset({"ask-once", "always-ask", "pre-allowed"})


class CaptureBlockedError(RuntimeError):
    """Raised by capture entries (core.desktop.vision) when the
    foreground window is denylisted. The message IS the honest
    refusal text from foreground_capture_block(); callers that
    already phrase capture failures as text (ocr_screen,
    screen_look, take_screenshot) surface it verbatim through
    their existing exception paths, so a blocked capture is
    never a silent empty result."""


# ---------------------------------------------------------------------------
# Config — read dynamically, never imported at module load
# ---------------------------------------------------------------------------


def _config_value(name: str, default=None):
    """getattr(name) from core.config — the module first, then
    its `config` AppConfig object — or `default` when neither
    carries it (or config can't be imported at all)."""
    try:
        from . import config as _cfg_mod
    except Exception:  # noqa: BLE001 — the guard must still run
        return default
    value = getattr(_cfg_mod, name, None)
    if value is not None:
        return value
    obj = getattr(_cfg_mod, "config", None)
    if obj is not None:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def capture_policy() -> str:
    """The configured capture policy: "ask-once" (default),
    "always-ask" or "pre-allowed". An unknown configured value
    resolves to "ask-once" — the middle posture, never the
    loosest."""
    raw = str(_config_value("capture_policy", "ask-once")
              or "ask-once").strip().lower()
    return raw if raw in _POLICIES else "ask-once"


def _denylist_processes() -> frozenset:
    configured = _config_value("capture_denylist_processes", None)
    if configured is None:
        return DEFAULT_DENYLIST_PROCESSES
    try:
        return frozenset(
            str(p).strip().lower() for p in configured if str(p).strip())
    except TypeError:
        return frozenset({str(configured).strip().lower()})


def _denylist_title_keywords() -> frozenset:
    configured = _config_value("capture_denylist_title_keywords", None)
    if configured is None:
        return DEFAULT_DENYLIST_TITLE_KEYWORDS
    try:
        return frozenset(
            str(k).strip().lower() for k in configured if str(k).strip())
    except TypeError:
        return frozenset({str(configured).strip().lower()})


# ---------------------------------------------------------------------------
# Foreground probe — injectable seam (input_guard's pattern)
# ---------------------------------------------------------------------------

_probe = None  # () -> (process_name | None, title | None) | None


def set_probe(fn) -> None:
    """Replace the foreground probe (test seam). fn() returns
    (process_name, title) — either half may be None — or a dict
    with "process"/"process_name" and "title" keys, or None when
    the foreground is unknown. Pass None to restore the default
    probe."""
    global _probe
    _probe = fn


def clear_probe() -> None:
    """Restore the default foreground probe."""
    set_probe(None)


# Backwards/forwards-compatible alias: suites in neighbouring
# modules use both spellings for seam resets.
reset_probe = clear_probe


def _default_probe():
    """(process_name, title) of the foreground window from the
    codebase's existing seams: input_guard's process probe +
    uia's title reader, each independently guarded. (None, None)
    off Windows or when neither seam can answer."""
    proc = None
    title = None
    try:
        from . import input_guard

        proc = input_guard._foreground_process_name()
    except Exception:  # noqa: BLE001 — unknown is a normal answer
        proc = None
    try:
        from .desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/

        title = uia.foreground_title()
    except Exception:  # noqa: BLE001
        title = None
    return proc, title


def foreground_info():
    """(process_name | None, title | None) for the foreground
    window via the active probe; (None, None) when unknown.
    Never raises."""
    probe = _probe
    try:
        if probe is not None:
            result = probe()
        else:
            result = _default_probe()
    except Exception:  # noqa: BLE001 — a broken probe is "unknown"
        return None, None
    if result is None:
        return None, None
    if isinstance(result, dict):
        return (result.get("process") or result.get("process_name"),
                result.get("title"))
    try:
        proc, title = result
    except (TypeError, ValueError):
        return None, None
    return proc, title


def _normalise_process(name) -> str:
    text = str(name or "").strip().lower().replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def foreground_capture_block() -> str | None:
    """The denylist gate every capture path calls FIRST: an
    honest reason string when the foreground window is a
    sensitive window, else None (proceed). Never raises; an
    unknown foreground (off-Windows, no seam) is NOT blocked."""
    try:
        proc_raw, title_raw = foreground_info()
    except Exception:  # noqa: BLE001
        return None
    proc = _normalise_process(proc_raw)
    if proc:
        for entry in _denylist_processes():
            if proc == _normalise_process(entry):
                return (
                    f"capture refused — the foreground window belongs "
                    f"to '{proc_raw}', which is on the sensitive-window "
                    f"denylist (password managers, authenticators and "
                    f"banking apps are never captured). Bring a "
                    f"different window to the front and try again.")
    title = str(title_raw or "").strip()
    if title:
        lowered = title.lower()
        for keyword in _denylist_title_keywords():
            if keyword and keyword in lowered:
                return (
                    f"capture refused — the foreground window "
                    f"('{title[:80]}') matches the sensitive-window "
                    f"denylist keyword '{keyword}' (private browsing, "
                    f"password vaults and banking pages are never "
                    f"captured). Bring a different window to the "
                    f"front and try again.")
    return None


# ---------------------------------------------------------------------------
# (b) Per-session opt-in state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_session_token = uuid.uuid4().hex
_approved_kinds: set = set()


def current_session_token() -> str:
    """The current capture-session token (rotated by
    note_new_session)."""
    with _lock:
        return _session_token


def note_new_session() -> str:
    """Start a new capture session: rotate the session token and
    clear every per-kind approval, so the next camera / mic /
    screenshot-save capture asks afresh under "ask-once".
    Returns the new token."""
    global _session_token
    with _lock:
        _session_token = uuid.uuid4().hex
        _approved_kinds.clear()
        return _session_token


# The GUI's session-end hook name for the same operation (the
# coordinator's wiring calls reset_session()).
reset_session = note_new_session


def is_capture_approved(kind: str) -> bool:
    """True when `kind` already holds a session approval."""
    with _lock:
        return str(kind) in _approved_kinds


def revoke_capture_approval(kind: str) -> bool:
    """Drop one kind's session approval (PF-18 batch 1).

    Called when a durable permission flips to denied, so no ask-once
    approval survives the flip — the next capture of that kind asks
    afresh (and, for a denied PF-18 id, the funnel refuses first).
    Unlike note_new_session, this clears ONE kind, not the whole
    session: a mic denial must not lapse unrelated approvals.
    Returns True when an approval was actually present.
    """
    with _lock:
        kind = str(kind)
        if kind in _approved_kinds:
            _approved_kinds.discard(kind)
            return True
        return False


def _ask_human(kind: str) -> bool:
    """One confirm_gate ask for `kind`; False on any failure
    (no handler, denial, gate error) — the gate itself is
    default-deny, and this wrapper keeps that posture."""
    tool_name = _CAPTURE_KIND_TOOLS.get(
        str(kind), f"capture ({kind} capture)")
    try:
        from . import confirm_gate

        return bool(confirm_gate.request_confirmation(
            tool_name, {"kind": str(kind)}))
    except Exception:  # noqa: BLE001 — a broken gate denies
        return False


def ensure_capture_allowed(kind: str) -> tuple[bool, str]:
    """(allowed, message) for one capture of `kind` ("camera",
    "microphone", "screenshot_save" or "system_audio").

    GAPS-20 (D-10): "system_audio" has NO durable pre-check below
    — there is no "system_audio" id in permission_grants'
    PERMISSION_IDS, so no durable Settings toggle exists for it
    yet; adding one is a grants-schema + Settings change, a
    permission-boundary decision (the PF-18 ruling is still owed
    for the same shape). The ask flow + the denylist are its gate
    until then: a "system_audio" capture always asks per the
    configured policy, and a denylisted foreground refuses it
    like any other kind.

    PF-18 batch 2: before all of that, a durable camera denial
    (data/permission_grants.json) refuses with the Settings reason
    — the standing posture outranks the session ask, and the
    refusal is deterministic (no foreground dependency). PF-18
    batch 3 supersedes the batch-2 scope note: "microphone" now
    consults the durable store here too (mic-at-guard symmetry —
    a direct handler call bypassing the funnel must not bypass
    the durable grant). PF-18 batch 4 supersedes the batch-3 scope
    note: "screenshot_save" now consults the durable store here
    too, for the screen id — the save path is the only guard-gated
    screen path (the plain look is Tier-0 observe, denylist only,
    and its enforcement is the funnel choke point). A consult
    failure fails closed (refuses).
    Order is fixed: the sensitive-window denylist (a) is honoured
    FIRST — a denylisted foreground refuses regardless of policy
    or prior approvals. Then the configured policy decides:
      * "pre-allowed" — never asks;
      * "always-ask"  — asks every time, remembers nothing;
      * "ask-once"    — asks once per kind per session; an
        approval is remembered for the session, a DENIAL IS NOT
        (the next capture asks again — see the module docstring).
    `message` is "" when allowed, else the honest refusal text in
    the caller's own voice."""
    kind = str(kind or "").strip().lower()
    if kind == "camera":
        # PF-18 batch 2: durable camera denial refuses at the guard
        # too, consistent with the funnel refusal. Lazy import: the
        # grants module is stdlib-only, so this cannot cycle; a
        # consult failure fails closed (a broken store must never
        # silently open the camera).
        # Settings redesign (2026-11-16, R1): a voice session
        # grant widens ONLY the fail-closed default read, live
        # origin only, never a stored denial — the same rule as
        # the funnel's check_tool (see
        # permission_grants.session_grant_allows). A stored
        # denial still refuses here.
        try:
            from . import permission_grants as _pg

            _cam_denied = _pg.get_state("camera") == "denied" \
                and not _pg.session_grant_allows("camera")
        except Exception:  # noqa: BLE001 — fail closed, never open
            _cam_denied = True
        if _cam_denied:
            return False, (
                "Camera is switched off in Settings, so nothing "
                "was captured. To use it again, switch camera back "
                "on in Settings.")
    if kind == "microphone":
        # PF-18 batch 3: mic-at-guard symmetry, mirroring the camera
        # pre-check above. A direct handler call bypassing the funnel
        # (record_audio) must not bypass the durable mic grant — the
        # funnel covers tool calls, the guard covers direct calls, and
        # D2's "off means no session can exist" holds on both paths.
        # The sidecar's guard call refuses here too, intended: a
        # denied mic means no capture session can exist. Lazy import:
        # the grants module is stdlib-only, so this cannot cycle; a
        # consult failure fails closed (a broken store must never
        # silently open the microphone).
        try:
            from . import permission_grants as _pg

            _mic_denied = _pg.get_state("microphone") == "denied"
        except Exception:  # noqa: BLE001 — fail closed, never open
            _mic_denied = True
        if _mic_denied:
            return False, (
                "Microphone is switched off in Settings, so nothing "
                "was captured. To use it again, switch microphone "
                "back on in Settings.")
    if kind == "screenshot_save":
        # PF-18 batch 4: durable screen denial refuses at the guard
        # too, mirroring camera/mic — but ONLY for the save path,
        # which is the one screen path gated by this guard (the
        # plain look is Tier-0 observe with no guard call, and its
        # enforcement is the funnel choke point). A direct handler
        # call saving a screenshot must not bypass the durable
        # screen grant. Lazy import: the grants module is
        # stdlib-only, so this cannot cycle; a consult failure
        # fails closed (a broken store must never silently save a
        # screenshot).
        try:
            from . import permission_grants as _pg

            _screen_denied = _pg.get_state("screen") == "denied"
        except Exception:  # noqa: BLE001 — fail closed, never open
            _screen_denied = True
        if _screen_denied:
            return False, (
                "Screen is switched off in Settings, so nothing "
                "was captured. To use it again, switch screen back "
                "on in Settings.")
    blocked = foreground_capture_block()
    if blocked is not None:
        return False, blocked
    policy = capture_policy()
    if policy == "pre-allowed":
        return True, ""
    if policy == "ask-once":
        with _lock:
            if kind in _approved_kinds:
                return True, ""
    if _ask_human(kind):
        if policy == "ask-once":
            with _lock:
                _approved_kinds.add(kind)
        return True, ""
    label = _CAPTURE_KIND_LABELS.get(kind, kind.capitalize())
    return False, (
        f"{label} capture was not confirmed, so nothing was "
        f"captured — the confirmation prompt was declined, timed "
        f"out, or no confirmation handler is available.")


# ---------------------------------------------------------------------------
# (c) Capture indicator registry
# ---------------------------------------------------------------------------

_indicator_listener = None  # fn(kind: str, active: bool) | None
_active_counts: dict = {}


def set_indicator_listener(fn) -> None:
    """Register the capture-indicator listener (the GUI's
    on-screen indicator). fn(kind, active) is called on every
    note_capture_started / note_capture_ended with the kind's
    resulting active state. Pass None to clear. A raising
    listener never breaks a capture."""
    global _indicator_listener
    _indicator_listener = fn


def clear_indicator_listener() -> None:
    """Unregister the capture-indicator listener."""
    set_indicator_listener(None)


def active_captures() -> frozenset:
    """The kinds with a capture currently live (count > 0)."""
    with _lock:
        return frozenset(k for k, n in _active_counts.items() if n > 0)


def is_capture_active(kind: str) -> bool:
    with _lock:
        return int(_active_counts.get(str(kind), 0)) > 0


def _fire_indicator(kind: str, active: bool) -> None:
    listener = _indicator_listener
    if listener is None:
        return
    try:
        listener(str(kind), bool(active))
    except Exception:  # noqa: BLE001 — the indicator is a display;
        pass  # it must never sink the capture it reports


def note_capture_started(kind: str) -> None:
    """Mark one capture of `kind` live and notify the indicator.
    Nested/overlapping captures of the same kind are counted —
    the kind stays active until the matching number of
    note_capture_ended calls. Never raises."""
    kind = str(kind)
    with _lock:
        _was_active = int(_active_counts.get(kind, 0)) > 0
        _active_counts[kind] = int(_active_counts.get(kind, 0)) + 1
    _fire_indicator(kind, True)
    # Command Deck Phase D: exactly one Started per 0→1 transition
    # (overlapping captures of one kind share it).
    if not _was_active:
        emit_capture_started(kind)


def note_capture_ended(kind: str) -> None:
    """Mark one capture of `kind` finished and notify the
    indicator with the kind's resulting state (still active when
    another overlapping capture of the same kind is live).
    Never raises; an unmatched end is a no-op beyond the
    notification floor at zero."""
    kind = str(kind)
    with _lock:
        _was_active = int(_active_counts.get(kind, 0)) > 0
        remaining = int(_active_counts.get(kind, 0)) - 1
        if remaining > 0:
            _active_counts[kind] = remaining
        else:
            _active_counts.pop(kind, None)
            remaining = 0
    _fire_indicator(kind, remaining > 0)
    # Command Deck Phase D: exactly one Finished per 1→0 transition.
    # An unmatched end (nothing live) emits nothing — it is a
    # no-op, not a finished capture.
    if _was_active and remaining == 0:
        emit_capture_finished(kind)


# ---------------------------------------------------------------------------
# (d) Sensitive-content denylist — v5.56 (Wave 2, Stream G),
#     v5.70 (Life Admin track, LA-6 / S-6)
# ---------------------------------------------------------------------------
# The window denylist guards WHERE a capture looks; this guards WHAT
# a capture-derived span may carry (OCR text, transcripts, watcher
# summaries). Two patterns, both conservative by design:
#   * Aadhaar number: a 12-digit run (12 continuous digits, or
#     4-4-4 groups joined by single spaces and/or hyphens) that
#     ALSO passes the Verhoeff checksum Aadhaar numbers carry
#     (verhoeff_valid(), written here from the published tables —
#     no dependency). v5.56 flagged ANY 12-digit run; v5.70
#     narrows it, because a bare 12-digit run that fails the
#     checksum cannot be an Aadhaar number and flagging it dropped
#     innocent spans (order IDs, counts). A 10-digit phone number
#     does NOT match; a 16-digit card number does not match as a
#     whole (\b anchors the run, and its 12-digit substrings are
#     not separately matched either). MASKED FORMS ARE NOT
#     SECRETS AND MUST NOT MATCH: "XXXX XXXX 0123",
#     "**** **** 0123" and "ending in 0123" carry only the last 4
#     digits, which are not enough to identify anyone — the
#     pattern needs 12 digits, so masked forms pass by
#     construction, not by special-casing.
#   * OTP code: a 4-8 digit code within 40 non-digit characters
#     AFTER an OTP keyword. The keyword list is deliberately
#     tight (documented in _OTP_KEYWORDS): "otp", "one-time /
#     one time password", "one-time / one time code", and
#     "verification code" — the phrasings OTP messages arrive
#     in. A bare 6-digit number does NOT match — without the
#     keyword it could be a count, a year fragment, or anything
#     else, and blocking every short number would gut watcher
#     spans. The keyword window is one-directional (keyword
#     before code, the shape OTP messages arrive in); a code
#     printed BEFORE the word "otp" does not match.
# REFUSAL, NOT REDACTION: a match refuses the whole text —
# check_text() returns the refusal string, gate_evidence_span()
# drops the span — and there is NO redacted-output path for
# these classes anywhere in this module (no mask/redact helper
# exists for them). A dropped span is recoverable; a leaked or
# half-masked ID is not.

import re as _re

_AADHAAR_RE = _re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")

# OTP context keywords — tight by design (see the header note).
# Word-boundaried, so "otp" inside another word never fires.
_OTP_KEYWORDS = (
    r"otp",
    r"one[\s\-]time password",
    r"one[\s\-]time code",
    r"verification code",
)

_OTP_RE = _re.compile(
    r"\b(?:" + "|".join(_OTP_KEYWORDS) + r")"
    r"[^\d]{0,40}?"
    r"\b(\d{4,8})\b",
    _re.IGNORECASE)

# Verhoeff checksum tables (the standard published tables).
# d: multiplication; p: permutation (period 8); inv: inverse.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 0, 1, 3, 2, 4, 9, 6, 7, 5),
    (9, 1, 3, 2, 4, 0, 6, 7, 5, 8),
    (4, 9, 2, 5, 0, 6, 7, 8, 1, 3),
    (2, 7, 6, 1, 9, 0, 8, 4, 3, 5),
    (7, 4, 8, 0, 6, 3, 5, 9, 2, 1),
)
_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_valid(number) -> bool:
    """Pure Verhoeff checksum check (stdlib only, no I/O).

    True when `number` is all digits and its Verhoeff checksum
    is zero — the property real Aadhaar numbers carry. Non-digit,
    empty, or unprintable input is False, never an exception."""
    try:
        digits = str(number)
    except Exception:  # noqa: BLE001 — unprintable input is not valid
        return False
    if not digits or not digits.isdigit():
        return False
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][ord(ch) - 48]]
    return check == 0


def _aadhaar_match_valid(match) -> bool:
    """True when one _AADHAAR_RE match is a real Aadhaar shape:
    exactly 12 digits once its space/hyphen separators are
    stripped, passing the Verhoeff checksum."""
    digits = _re.sub(r"[\s\-]", "", match.group(0))
    return len(digits) == 12 and verhoeff_valid(digits)


def contains_sensitive_content(text) -> str | None:
    """The sensitive-content label in `text`, or None.

    Returns "aadhaar-like number" (Aadhaar checked first — the
    label is kept from v5.56 so existing callers' wording is
    unchanged; since v5.70 it means Verhoeff-valid only) or
    "otp code". Never raises; non-string input is treated as
    empty.
    """
    try:
        content = "" if text is None else str(text)
    except Exception:  # noqa: BLE001 — unprintable input is not content
        return None
    for match in _AADHAAR_RE.finditer(content):
        if _aadhaar_match_valid(match):
            return "aadhaar-like number"
    if _OTP_RE.search(content):
        return "otp code"
    return None


def check_text(text) -> str | None:
    """Guided-flow / screen-sight hook (v5.70, LA-6): the honest
    refusal string when `text` carries sensitive content, else
    None. Callers REFUSE on a non-None return — they drop the
    text and surface this string; they never redact-and-continue
    (no redacted-output path exists for these classes).

    WIRING: capture-derived SPANS already reach this through
    gate_evidence_span() / evidence_span_allowed() below. The
    raw OCR return path (core/desktop/control.ocr_screen) does NOT yet
    call it — that is a coordinator-owned one-line hook in
    control.py (outside this worker's file allowance): after the
    OCR text is produced and before it is wrapped/returned,
    `refusal = capture_guard.check_text(text); if refusal is
    not None: return refusal`. Until that line lands, OCR text
    is screened only once it becomes a span, not at return."""
    label = contains_sensitive_content(text)
    if label is None:
        return None
    return (
        f"capture-derived content refused — the text contains an "
        f"{label}, which the sensitive-content denylist never lets "
        f"a span carry. Nothing from this capture was emitted.")


def content_capture_block(text) -> str | None:
    """Honest refusal string when `text` carries sensitive content
    (v5.56 name, kept; since v5.70 it delegates to check_text)."""
    return check_text(text)


# ---------------------------------------------------------------------------
# (e) Evidence-span gate — v5.56 (Wave 2, Stream G)
# ---------------------------------------------------------------------------
# Source -> capture kind needing a session approval, or None for
# Tier-0 observe sources (denylist + content only, no approval
# re-check — consistent with the v5.48 Tier-0 screenshot look).
# "screenshot_save" maps to its approval because a saved screenshot
# was opt-in at save time; the span gate re-checks that approval
# non-interactively rather than asking again.

_SPAN_SOURCE_TO_KIND = {
    "screen_watch": None,
    "screenshot": None,
    "screenshot_save": "screenshot_save",
    "camera": "camera",
    "microphone": "microphone",
    "occluded_screenshot": "occluded_screenshot",
    "activity_stream": "activity_stream",
}

# New opt-in kinds for future capture surfaces (PC-S2 occluded
# capture, CA-1 activity stream). Additive only: existing kinds and
# their labels/tools are untouched.
_CAPTURE_KIND_LABELS["occluded_screenshot"] = "Occluded-window screenshot"
_CAPTURE_KIND_LABELS["activity_stream"] = "Activity stream"
_CAPTURE_KIND_TOOLS["occluded_screenshot"] = (
    "occluded-window capture (occluded screenshot)")
_CAPTURE_KIND_TOOLS["activity_stream"] = (
    "activity stream capture (activity stream)")


def evidence_span_allowed(source: str, text=None) -> tuple[bool, str]:
    """(allowed, reason) for emitting one capture-derived span.

    Order is fixed and prompt-free (this function NEVER asks the
    human — a span emitter must not block the pipeline on a prompt):
      1. foreground denylist (a) — a denylisted foreground refuses
         regardless of approvals;
      2. sensitive-content denylist (d) on the span text;
      3. for opt-in capture kinds, the session must ALREADY hold an
         approval for that kind (recorded by ensure_capture_allowed
         when the capture itself was allowed); "pre-allowed" policy
         passes step 3 without approvals. A missing approval denies
         — the span is dropped, and the surface must re-allow the
         capture itself, not the span.
    Unknown sources deny fail-closed. Never raises.
    """
    try:
        source = str(source or "").strip().lower()
        if source not in _SPAN_SOURCE_TO_KIND:
            return False, (
                f"capture-derived span refused — unknown evidence "
                f"source {source!r}; only known capture sources may "
                f"emit spans.")
        blocked = foreground_capture_block()
        if blocked is not None:
            return False, blocked
        content_block = content_capture_block(text)
        if content_block is not None:
            return False, content_block
        kind = _SPAN_SOURCE_TO_KIND[source]
        if kind is None:
            return True, ""
        if capture_policy() == "pre-allowed":
            return True, ""
        if is_capture_approved(kind):
            return True, ""
        label = _CAPTURE_KIND_LABELS.get(kind, kind)
        return False, (
            f"capture-derived span refused — no session approval "
            f"for {label} capture, so nothing from this capture "
            f"was emitted.")
    except Exception:  # noqa: BLE001 — a broken gate denies
        return False, "capture-derived span refused — guard error."


def gate_evidence_span(span: dict) -> tuple[dict | None, str | None]:
    """Gate one capture-derived span dict before emission.

    `span` carries "source" plus its text under "text", "content"
    or "summary" (first present wins). Returns (span, None) when
    allowed, (None, reason) when denied — the caller drops the span
    and may record only a "blocked-sensitive" counter, never the
    span text. A non-dict span is denied fail-closed. Never raises.
    """
    try:
        if not isinstance(span, dict):
            return None, (
                "capture-derived span refused — span is not an object.")
        source = span.get("source")
        text = None
        for key in ("text", "content", "summary"):
            if span.get(key) is not None:
                text = span.get(key)
                break
        allowed, reason = evidence_span_allowed(source, text)
        if allowed:
            return span, None
        return None, reason
    except Exception:  # noqa: BLE001 — a broken gate denies
        return None, "capture-derived span refused — guard error."
