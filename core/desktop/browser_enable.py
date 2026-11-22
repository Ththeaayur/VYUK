"""Browser-control enable flow (Batch 3, 2026-11-05).

Owner field report 2026-11-05: "I'm not able to control my
browser." Gap proved from the tree: the ONLY path was manual —
flip ``browser_allow_real_chrome_attach`` in Settings/config (or
set ``VYUK_ALLOW_REAL_CHROME_ATTACH=1``) AND start Chrome
yourself with ``--remote-debugging-port=9222`` AND then
``browser_control`` launch. The Settings toggle only flipped the
flag (``gui/window_brain_services.py``); it never relaunched
Chrome, never verified the attach, and never explained the flag.
No enable helper, launcher, or guided flow existed anywhere
(grep: no ``enable_browser`` / ``launch_chrome`` symbol in
``core/`` or ``gui/``).

LOPHOLE FAMILY — one design closes the class (the elegant
solution; alternatives rejected at the foot of this docstring):

  L1  opt-in OFF: attach refuses without trying (by design), but
      nothing offered a one-click path — the refusal pointed only
      at manual steps.
  L2  opt-in ON, port closed, Chrome NOT running: attach failed
      with "start Chrome yourself" — no launch was offered.
  L3  opt-in ON, port closed, Chrome RUNNING without the flag:
      the same generic failure. Chrome ignores a second launch
      with the flag while an instance lives, so a naive "launch
      Chrome" silently does nothing — the flow must tell the two
      cases apart and never kill his tabs silently.
  L4  port configured to N != 9222, Chrome launched with 9222
      (or the reverse): user-facing texts hardcoded 9222, and the
      attach probed the configured port — the failure misled.
  L5  something else listens on the port (not Chrome): the
      connect failed cryptically; no identity check on the
      endpoint.
  L6  Playwright missing / attach timeout: generic failure.
  L7  opt-in turned OFF again while attached: the toggle only
      flipped a flag — the live session stayed attached.
  L8  enable claiming success on a launch call alone, without
      verifying the attach by a status read.
  L9  the open_url seam: enabling must never imply that open_url
      windows become drivable — they stay fire-and-forget.

THE ONE DESIGN: a small state machine over injectable seams.
Every public function returns a dict ``{"state": ..., "message":
...}`` and NEVER raises; the GUI renders the state. States:

  already_attached / attached — verified by a status read
      (``_is_connected`` + ``_status_line``) AFTER the attach,
      never on a launch call alone (L8).
  needs_launch — Chrome is not running; the launch button is
      offered (L2).
  needs_restart — Chrome IS running without the flag; the
      restart button is offered. Restart closes Chrome ONLY
      after the user presses that button having read the warning
      (tabs come back; unsaved form work may be lost), and only
      on Windows (L3).
  port_in_use — the endpoint answers but is not Chrome (L5).
  attach_failed / launch_failed / launch_timeout /
  restart_failed / chrome_not_found / needs_confirm /
  chrome_state_unknown / failed — each names the exact next
      step; none claims success (L4/L6).
  profile_is_default / profile_locked / profile_unusable /
  profile_state_unknown — the debug profile dir is Chrome's
      default profile (Chrome 136 ignores the port there), is
      locked by a running Chrome, is a file / unwritable, or
      cannot be judged (lock file present, Chrome state unknown).
      The launch is refused BEFORE anything starts, with the
      cause named (GAPS-3).
  disabled / already_off — disable detaches first, then flips
      the flag off (L7).

CHROME 136 (GAPS-3, BLOCKS #4, 2026-11-12): from Chrome 136 the
--remote-debugging-port / --remote-debugging-pipe switches are
ignored on the default Chrome profile — the flag is silently
ignored, no port opens, no warning (Chrome for Developers,
"Changes to remote debugging switches to improve security",
17 March 2025). The launch/restart path therefore passes
--user-data-dir=<VYUK debug profile> (core/desktop/browser_cdp:
_debug_profile_dir, diagnosed before any launch by
_diagnose_profile_dir) alongside the port, and every message
states the honest cost: the debug profile is a SEPARATE profile
— the everyday profile's logins, extensions and open tabs are
not in it. The attach path names the wall when Chrome is
running, and the enable flow reads the default profile's
DevToolsActivePort file as a hint for a port the running Chrome
opened itself (newer Chrome's chrome://inspect toggle) — a hint
only, the probe still decides. The real-profile attach is NOT
pretended to work by flags on Chrome 136+: it works only when
the port is actually open (pre-136 flag launch, or the toggle).

EXPLICITNESS (the privacy boundary, unchanged): the opt-in
default stays OFF in code (``core/config.py``); the enable
actions below are the ONLY callers that flip it on, and they run
only from the Settings "Enable browser control" button (GUI
only — never a voice action, never on a plain browser request,
never at startup). ``browser_control``/``browser_read``/click/
type dispatch never calls this module (pinned by the prover and
the suite). Disabling detaches cleanly and never kills Chrome
or closes tabs — attach-only, per v5.70.

REJECTED ALTERNATIVES:
  * Auto-enable on first browser request — rejected: the
    opt-in exists because attach reaches the signed-in Chrome;
    silently flipping it on a tool call would void the consent
    the opt-in was built to carry.
  * Kill Chrome silently on enable — rejected: tabs, unsaved
    forms and downloads belong to the user; only his explicit
    press of the restart button (after the warning) may close
    it, and only on Windows.
  * Launch a dedicated-profile Chrome instead — rejected: v5.70
    deleted the own-browser launch for a reason (real-Chrome-
    only); resurrecting it would split the browser surface
    again and lose his logins. SUPERSEDED in part by Chrome 136
    (GAPS-3, 2026-11-12): Chrome 136 ignores the debug port on
    the default profile, so the launch/restart path now passes
    --user-data-dir=<VYUK debug profile> — the same real Chrome
    binary, a separate profile, with the honest cost stated in
    every message (no inherited logins/tabs). The own-browser
    launch stays deleted; only the launch FLAG changed, because
    no flag-only launch can work on Chrome 136+.
  * A new funnel tool for enable — rejected: enable is a
    Settings-only human decision (v5.49: "never a voice
    action"); a tool would let the voice brain flip it. The
    registry stays 656.
  * Hardcoding port 9222 anywhere in the new code — rejected
    (L4): every probe, launch and message reads the configured
    ``browser_debug_port``; 9222 appears only as the config
    default.

FIELD LEG OWED: the lab fakes prove the decision/validation/
wiring logic only. Real-Windows proof still owed: Chrome
relaunch with his real tabs (they must come back), real attach
after relaunch, wrong-port and port-in-use cases on his box.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .browser_cdp import (_cdp_handshake, _cdp_port,
                         _cdp_port_answers,
                         _real_chrome_attach_allowed, _DEFAULT_CDP_PORT,
                         _chrome_running, _default_chrome_profile_dir,
                         _debug_profile_dir, _diagnose_profile_dir,
                         _read_devtools_active_port, _launch_argv,
                         _CHROME_136_WALL, note_launched_chrome)
from .browser_toggle import (clear_toggle_flag, note_toggle_attach,
                             toggle_flag, toggle_flag_set)

_HANDSHAKE_WAIT_S = 15.0
_HANDSHAKE_POLL_S = 0.5
_CHROME_EXIT_WAIT_S = 15.0


# GAPS-3 (Chrome 136 profile wall, 2026-11-12): the honest note
# attached to a launch that succeeds on the debug profile, and
# the note for an attach that lands through the chrome://inspect
# toggle on the real profile. Both state which profile the
# session is — the debug profile is NOT the everyday profile,
# and the toggle attach IS the real one.
_DEBUG_PROFILE_NOTE = (
    "This Chrome runs on VYUK's separate debug profile ({dir}) — "
    "your everyday profile's logins, extensions and open tabs are "
    "NOT in it, so sign in inside it if a site needs you logged "
    "in. Chrome 136 and later ignore the debug port on the "
    "default profile, so this separate profile is the only way "
    "the launch can open the port.")

_TOGGLE_NOTE = (
    "Attached through the remote-debugging toggle in your "
    "already-running Chrome (chrome://inspect) — this IS your "
    "real profile, logins and tabs included. The port came from "
    "Chrome's own DevToolsActivePort file in your default profile.")


# GAPS-3: profile-diagnosis states -> enable-flow refusal states.
_PROFILE_REFUSAL_STATE = {
    "default_profile": "profile_is_default",
    "locked": "profile_locked",
    "not_a_directory": "profile_unusable",
    "unwritable": "profile_unusable",
    "missing": "profile_unusable",
    "unknown": "profile_state_unknown",
}


# ---------------------------------------------------------------- seams
# Module-level callables so suites monkeypatch the seams without
# touching a real Chrome, Playwright, or the config store — the
# tree's wallpaper seam pattern (_set_wallpaper_via_spi).

def _set_opt_in(on: bool) -> None:
    """Flip the opt-in: config field in-process + durable .env.

    The ONLY writer the enable/disable actions use. Never
    raises for a missing config (a failed persist is reported
    by the caller, never silently swallowed into a True)."""
    from .. import config as _cfg_mod

    obj = getattr(_cfg_mod, "config", None)
    if obj is not None:
        setattr(obj, "browser_allow_real_chrome_attach", bool(on))
    _cfg_mod.save_env_setting(
        "BROWSER_ALLOW_REAL_CHROME_ATTACH",
        "true" if on else "false")


def _opt_in() -> bool:
    return bool(_real_chrome_attach_allowed())


def _port() -> int:
    return int(_cdp_port())


def _probe_cdp(port: int):
    """CDP handshake on ``port``: dict (version/targets) or None.

    None means nothing answers — port closed OR Chrome not
    listening. A dict whose ``version`` carries no Chrome
    ``Browser`` string means something else answers (L5)."""
    return _cdp_handshake(port)


def _find_chrome_exe():
    """Chrome executable, or None. PATH first, then the standard
    install locations (same candidate style as the Spotify
    install check in core/music_player_spotify.py)."""
    for name in ("chrome", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    if sys.platform.startswith("win"):
        roots = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rels = [
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Google/Chrome/Application/chrome.exe"),
        ]
        for root, rel in zip(roots, rels):
            if not root:
                continue
            cand = Path(root) / rel
            if cand.is_file():
                return cand
    else:
        for cand in ("/usr/bin/google-chrome",
                     "/usr/bin/google-chrome-stable",
                     "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     "/snap/bin/chromium"):
            if Path(cand).is_file():
                return Path(cand)
    return None


def _launch_chrome(exe, port: int) -> bool:
    """Start Chrome with the debug port AND the non-default
    user-data-dir Chrome 136 requires (GAPS-3: the port flag
    alone is silently ignored on the default profile). Detached,
    no console. True when the process started — NOT when it
    attached (L8): callers must still wait for the handshake and
    verify. The profile dir is resolved here, so a test that
    patches _debug_profile_dir controls the argv. R1: the Popen
    is registered with browser_cdp.note_launched_chrome, so
    _chrome_running() can reap it if the launched Chrome is
    later killed or crashes — the handle used to be discarded,
    leaving an unreaped zombie child that read as a running
    browser."""
    profile_dir = _debug_profile_dir()
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(
                subprocess, "DETACHED_PROCESS", 0)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            _launch_argv(exe, port, profile_dir), **kwargs)
        note_launched_chrome(proc)
        return True
    except Exception:  # noqa: BLE001 — launch failure is a state
        return False


def _launch_chrome_checked(exe, port):
    """Diagnose the debug profile, then launch against it.
    Returns (ok, state, message): ok=True means the process
    started (NOT attached — callers still wait for the handshake
    and verify); ok=False carries the refusal state + message and
    NOTHING was launched. The diagnosis runs BEFORE the launch so
    a default / locked / unusable profile is refused with its
    cause instead of a silent no-port launch (GAPS-3)."""
    profile_dir = _debug_profile_dir()
    diag = _diagnose_profile_dir(profile_dir, _chrome_running())
    if diag["state"] != "ok":
        return (False, _PROFILE_REFUSAL_STATE.get(diag["state"],
                                                  "profile_unusable"),
                diag["message"])
    if not _launch_chrome(exe, port):
        return (False, "launch_failed",
                (f"Chrome wouldn't start with the connection port "
                 f"({port}) on the debug profile ({profile_dir}). "
                 "Start Chrome yourself with "
                 f"--remote-debugging-port={port} "
                 f"--user-data-dir={profile_dir}, then press "
                 "'Enable browser control' again."))
    return (True, "launched", diag.get("note", ""))


def _close_chrome() -> bool:
    """Close Chrome (Windows only, taskkill). True when no Chrome
    process remains within the bounded wait. The caller — the
    restart action — runs this ONLY after the user's explicit
    press of the restart button (L3)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        subprocess.run(
            ["taskkill", "/IM", "chrome.exe", "/F"],
            capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 — fall through to the wait
        pass
    deadline = time.monotonic() + _CHROME_EXIT_WAIT_S
    while time.monotonic() < deadline:
        if _chrome_running() is False:
            return True
        time.sleep(_HANDSHAKE_POLL_S)
    return _chrome_running() is False


def _attach(port=None) -> str:
    """Attempt the real attach. Lazy import: the browser worker
    owns the Playwright objects; call from a worker thread,
    never from the browser worker thread itself. ``port`` is the
    CDP port to attach to — None means the configured port; the
    enable flow passes a discovered port (DevToolsActivePort)
    when the running Chrome opened its debugging server itself
    (GAPS-3). Field check B4 (2026-11-17): the attach itself
    must run ON the browser worker thread — Playwright sync
    objects are thread-affine, so attaching on the caller's
    thread builds a session every later worker-thread call
    fails with "Cannot switch to a different thread" while the
    attach-time verification (same wrong thread) still reads
    fine and reports success. Routed through ``_submit`` with
    kind="launch" (the long budget, as browser_dispatch's
    launch action does) exactly as ``_detach`` already does."""
    from . import browser_session as _bs

    if port is None:
        return str(_bs._submit(_bs._impl_attach_cdp, kind="launch"))
    return str(_bs._submit(lambda: _bs._impl_attach_cdp(port),
                           kind="launch"))


def _detach() -> str:
    """Detach from Chrome if attached (attach-only: Chrome and
    its tabs are untouched). No-op string when not attached."""
    from . import browser_session as _bs

    if not _bs._connected():
        return "No browser is connected, so there's nothing to close."
    return str(_bs._submit(_bs._impl_close))


def _is_connected() -> bool:
    from . import browser_session as _bs

    return bool(_bs._connected())


def _status_line() -> str:
    """The status line as the browser worker thread sees it.
    Field check B4 (2026-11-17): _impl_status reads Playwright
    page objects, so it must run on the worker thread too — on
    the caller's thread the titles read '?' (the attach-time
    verification hid this by running on the same wrong thread).
    kind="probe": a status peek is read-only, so a timeout means
    "couldn't look right now" and must never reset the session
    the way an action job would."""
    from . import browser_session as _bs

    return str(_bs._submit(_bs._impl_status, kind="probe"))


# ---------------------------------------------------------------- helpers

def _wait_for_handshake(port: int,
                        timeout_s: float = _HANDSHAKE_WAIT_S) -> bool:
    """Poll the CDP endpoint until it answers or the budget
    runs out. True only on a Chrome handshake."""
    deadline = time.monotonic() + timeout_s
    while True:
        hs = _probe_cdp(port)
        if isinstance(hs, dict):
            version = hs.get("version") or {}
            browser = str(version.get("Browser", "")).lower()
            if "chrome" in browser:
                return True
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(_HANDSHAKE_POLL_S)


def _attach_and_verify(port=None, note="") -> dict:
    """Attach, then VERIFY by a status read (L8). Attached only
    when the session is actually connected afterwards. ``port``
    is passed through to _attach (None = the configured port);
    ``note`` is appended to the attached message when the
    session came from somewhere the user must be told about —
    the debug profile (launch/restart) or the chrome://inspect
    toggle (enable discovery)."""
    try:
        result = _attach() if port is None else _attach(port)
    except Exception as exc:  # noqa: BLE001 — attach failure
        return {"state": "attach_failed",
                "message": ("Couldn't attach to Chrome "
                            f"({exc}). Try Enable browser control "
                            "again, or restart Chrome with the "
                            "connection port and ask again.")}
    if _is_connected():
        message = f"Attached to your Chrome. {_status_line()}"
        if note:
            message = f"{message} {note}"
        if note == _TOGGLE_NOTE:
            # Batch 2, #4: the session came through Chrome's own
            # chrome://inspect toggle, which VYUK cannot flip
            # back — record it, so the toggle does not stay on
            # silently. A record that did not stick is said in the
            # message (the same rule as the paused allowlist: a
            # silent failure here is the hole itself).
            if not note_toggle_attach(port if port is not None
                                      else _port()):
                message = (f"{message} I could not record that the "
                           "toggle is on, so check it yourself in "
                           "chrome://inspect.")
        return {"state": "attached", "message": message}
    return {"state": "attach_failed",
            "message": (f"Couldn't attach to Chrome: {result} "
                        "Try Enable browser control again — if "
                        "Chrome is running without the connection "
                        "port, use Restart Chrome with browser "
                        "control.")}


def _toggle_port_still_answers_chrome() -> bool:
    """True when the toggle's port still answers Chrome. Uses
    _cdp_port_answers — the C2-F1 probe, which tries /json/version
    then /json/list and judges each by L-21's identity rule, so a
    toggle-mode server that 404s /json/version still counts and a
    foreign server on the port does not. False on any failure
    (port closed, no answer, not Chrome): the toggle is not
    provably on, so the flag clears. The DevToolsActivePort file
    outlives the server, so the file alone never counts."""
    flag = toggle_flag()
    if flag is None:
        return False
    try:
        discovered = _read_devtools_active_port(
            _default_chrome_profile_dir())
    except Exception:  # noqa: BLE001 — an unreadable file is off
        return False
    if discovered is None:
        return False
    try:
        return bool(_cdp_port_answers(discovered))
    except Exception:  # noqa: BLE001 — a probe failure reads closed
        return False


def toggle_left_on() -> bool:
    """True when the chrome://inspect toggle is still on — a flag
    recorded at a toggle attach AND the toggle's port still
    answering Chrome. Self-heals: when the flag is set but the
    port no longer answers Chrome, the flag is cleared and this
    returns False (the toggle was flipped off since). Never
    raises; a broken store or probe reads as off."""
    try:
        if not toggle_flag_set():
            return False
        if _toggle_port_still_answers_chrome():
            return True
        clear_toggle_flag()
        return False
    except Exception:  # noqa: BLE001 — status never breaks
        return False


def toggle_notice() -> dict:
    """The toggle's plain-words status for the UI: {toggle_on,
    toggle_line}. toggle_line is empty when the toggle is not
    on; when it is on it names chrome://inspect and says how to
    turn it off, because the toggle is Chrome's own switch and
    VYUK cannot flip it."""
    if toggle_left_on():
        return {
            "toggle_on": True,
            "toggle_line": (
                "The remote-debugging toggle in your Chrome "
                "(chrome://inspect) is still on from your last "
                "browser session — VYUK can't turn it off for you. "
                "Open chrome://inspect in Chrome and turn the "
                "toggle off, then press Check again."),
        }
    return {"toggle_on": False, "toggle_line": ""}


def _fail(message: str) -> dict:
    return {"state": "failed", "message": message}


# ---------------------------------------------------------------- public

def browser_enable_status() -> dict:
    """Read-only status for the Settings card. Never raises."""
    try:
        on = _opt_in()
        port = _port()
        connected = _is_connected()
        line = _status_line() if connected else "Not attached."
        notice = toggle_notice()
        return {"opt_in": bool(on), "port": int(port),
                "connected": bool(connected), "status": str(line),
                "profile_dir": str(_debug_profile_dir()),
                "toggle_on": bool(notice["toggle_on"]),
                "toggle_line": str(notice["toggle_line"])}
    except Exception as exc:  # noqa: BLE001 — status never breaks
        return {"opt_in": False, "port": _DEFAULT_CDP_PORT,
                "connected": False,
                "status": f"Browser status unavailable ({exc}).",
                "profile_dir": str(_debug_profile_dir()),
                "toggle_on": False, "toggle_line": ""}


def browser_connect_path() -> dict:
    """Which browser path this machine takes, read-only.

    Field-check #45: the wizard's browser page must name the
    path for THIS machine instead of describing all three
    conditionally. The same decision tree as
    enable_browser_control(), minus the opt-in flip and the
    attach — a probe only, so calling it never enables,
    launches, or restarts anything. Returns {"path", "line"}:
    "attached" (already connected), "direct" (Chrome answers
    on the port — attach as-is), "toggle_or_restart" (Chrome
    runs without the port — one switch keeps the real profile,
    otherwise restart onto the separate profile), "launch"
    (Chrome is not running — open it on the separate profile),
    "unknown" (cannot tell — the page says so instead of
    guessing; a foreign answer on the port reads unknown too,
    and the tap-time flow names it exactly). Never raises: any
    failure reads "unknown"."""
    _unknown = {"path": "unknown",
                "line": ("VYUK will check how to reach your Chrome "
                         "when you tap Connect my browser.")}
    try:
        if _is_connected():
            return {"path": "attached",
                    "line": "VYUK is already connected to your Chrome."}
        port = _port()
        try:
            hs = _probe_cdp(port)
        except Exception:  # noqa: BLE001 — probe failure reads closed
            hs = None
        if isinstance(hs, dict):
            version = hs.get("version") or {}
            browser = str(version.get("Browser", "")).lower()
            if "chrome" in browser:
                return {"path": "direct",
                        "line": ("On this PC, VYUK will drive your "
                                 "Chrome as it is — the one that's "
                                 "already open.")}
            return dict(_unknown)
        # GAPS-3: the toggle's own port, as enable_browser_control
        # reads it — it counts only when the probe answers Chrome.
        discovered = _read_devtools_active_port(
            _default_chrome_profile_dir())
        if discovered is not None and discovered != port:
            try:
                hs2 = _probe_cdp(discovered)
            except Exception:  # noqa: BLE001 — probe failure reads closed
                hs2 = None
            if isinstance(hs2, dict):
                version2 = hs2.get("version") or {}
                browser2 = str(version2.get("Browser", "")).lower()
                if "chrome" in browser2:
                    return {"path": "direct",
                            "line": ("On this PC, VYUK will drive your "
                                     "Chrome as it is — the one that's "
                                     "already open.")}
        running = _chrome_running()
        if running is True:
            return {"path": "toggle_or_restart",
                    "line": ("On this PC, Chrome is open but VYUK "
                             "can't reach it yet. Turn on one switch "
                             "inside Chrome (chrome://inspect) and "
                             "she'll use your real Chrome — or restart "
                             "it instead, which reopens it on a "
                             "separate VYUK profile without your "
                             "everyday logins and tabs.")}
        if running is False:
            return {"path": "launch",
                    "line": ("On this PC, Chrome isn't running, so "
                             "connecting will open it on a separate "
                             "VYUK profile — your everyday logins and "
                             "tabs aren't in it, so you'd sign in "
                             "again there.")}
        return dict(_unknown)
    except Exception:  # noqa: BLE001 — a failed probe says unknown
        return dict(_unknown)


def enable_browser_control() -> dict:
    """THE one enable action (Settings only, explicit).

    Flips the opt-in on, probes the CDP endpoint on the
    configured port, and either attaches (verified) or reports
    the exact next step. Never auto-enables from a browser
    request; never launches or kills Chrome itself — launch /
    restart are separate explicit actions below. Never raises.
    """
    try:
        if _is_connected():
            return {"state": "already_attached",
                    "message": f"Browser control is already on. {_status_line()}"}
        _set_opt_in(True)
    except Exception as exc:  # noqa: BLE001 — enable must answer
        return _fail(f"Couldn't turn browser control on ({exc}). "
                     "Try again from Settings → Browser control.")
    port = _port()
    try:
        hs = _probe_cdp(port)
    except Exception:  # noqa: BLE001 — probe failure reads closed
        hs = None
    if isinstance(hs, dict):
        version = hs.get("version") or {}
        browser = str(version.get("Browser", "")).lower()
        if "chrome" not in browser:
            return {"state": "port_in_use",
                    "message": (f"Something else is already answering "
                                f"on port {port} — it is not Chrome, so "
                                "I can't attach to it. Pick a different "
                                "Chrome connection port in Settings → "
                                "Browser control, then try Enable again.")}
        return _attach_and_verify()
    # GAPS-3: nothing answers on the configured port. Before
    # concluding Chrome needs a relaunch, check whether the
    # running Chrome opened a debugging server itself — newer
    # Chrome's chrome://inspect remote-debugging toggle writes
    # the port to DevToolsActivePort in the default profile dir.
    # The file outlives the server, so the named port is only a
    # hint: it counts only when the probe answers with Chrome.
    discovered = _read_devtools_active_port(
        _default_chrome_profile_dir())
    if discovered is not None and discovered != port:
        try:
            hs2 = _probe_cdp(discovered)
        except Exception:  # noqa: BLE001 — probe failure reads closed
            hs2 = None
        if isinstance(hs2, dict):
            version2 = hs2.get("version") or {}
            browser2 = str(version2.get("Browser", "")).lower()
            if "chrome" in browser2:
                return _attach_and_verify(port=discovered,
                                          note=_TOGGLE_NOTE)
    running = _chrome_running()
    if running is True:
        return {"state": "needs_restart",
                "message": ("Browser control is on, but Chrome is "
                            "running without the connection port, so I "
                            "can't attach to it — Chrome ignores the "
                            "port flag while an instance is already "
                            "running. On Chrome 136 or later the flag "
                            "alone can't open the port on your everyday "
                            "profile at all, so a plain restart with "
                            "the flag would fail silently — the restart "
                            "below reopens Chrome on VYUK's separate "
                            "debug profile with the port instead. Press "
                            "'Restart Chrome with browser control' to "
                            "close Chrome and reopen it that way. Your "
                            "tabs will come back when you next open "
                            "Chrome normally — they are safe in your "
                            "everyday profile; the relaunched window is "
                            "the debug profile, so your logins and tabs "
                            "are not in it. Unsaved work in web forms "
                            "may be lost.")}
    if running is False:
        return {"state": "needs_launch",
                "message": ("Browser control is on, but Chrome isn't "
                            "running. Press 'Launch Chrome with browser "
                            "control' to start it on VYUK's separate "
                            "debug profile with the connection "
                            f"port ({port}) and attach. The debug "
                            "profile is separate from your everyday "
                            "profile, so your logins and open tabs are "
                            "not in it — sign in inside it if a site "
                            "needs you logged in.")}
    return {"state": "chrome_state_unknown",
            "message": ("Browser control is on, but I can't tell "
                        "whether Chrome is already running, so I can't "
                        "tell whether to launch it or restart it. If "
                        "Chrome is open, close it first, then press "
                        "'Launch Chrome with browser control'. If it "
                        "isn't open, press the same button.")}


def launch_chrome_for_browser_control(confirm: bool = False) -> dict:
    """Launch Chrome with the debug port and attach (verified).

    Refuses without ``confirm`` — the GUI passes True only from
    the explicit launch button. Refuses when Chrome is already
    running (that launch would silently join the flag-less
    instance — use restart instead, L3). Never raises.
    """
    if not confirm:
        return {"state": "needs_confirm",
                "message": ("Press 'Launch Chrome with browser control' "
                            "to start Chrome with the connection port.")}
    try:
        _set_opt_in(True)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't turn browser control on ({exc}).")
    port = _port()
    if _chrome_running() is True:
        return {"state": "needs_restart",
                "message": ("Chrome is already running without the "
                            "connection port, so launching it again "
                            "would do nothing — Chrome ignores the port "
                            "flag while an instance is alive. Press "
                            "'Restart Chrome with browser control' "
                            "instead — it reopens Chrome on VYUK's "
                            "separate debug profile with the port.")}
    exe = _find_chrome_exe()
    if exe is None:
        return {"state": "chrome_not_found",
                "message": ("I couldn't find Chrome on this PC to "
                            "launch it with the connection port. Start "
                            "Chrome yourself with "
                            f"--remote-debugging-port={port} "
                            f"--user-data-dir={_debug_profile_dir()}, "
                            "then press 'Enable browser control' again.")}
    launched, launch_state, launch_message = _launch_chrome_checked(
        exe, port)
    if not launched:
        return {"state": launch_state, "message": launch_message}
    if not _wait_for_handshake(port):
        return {"state": "launch_timeout",
                "message": ("Chrome started but its connection port "
                            f"({port}) never answered. Chrome may have "
                            "failed to start, or another Chrome is "
                            "already running without the port — close "
                            "Chrome and press 'Launch Chrome with "
                            "browser control' again. (If the debug "
                            "profile is locked by another Chrome, the "
                            "launch is refused before it starts — the "
                            "enable status names the profile.)")}
    return _attach_and_verify(note=_DEBUG_PROFILE_NOTE.format(
        dir=_debug_profile_dir()))


def restart_chrome_for_browser_control(confirm: bool = False) -> dict:
    """Close Chrome and reopen it with the debug port, then
    attach (verified). Windows only.

    Refuses without ``confirm`` — the GUI passes True only from
    the explicit restart button, pressed after the warning that
    Chrome will close (tabs come back; unsaved form work may be
    lost). Never raises.
    """
    if not confirm:
        return {"state": "needs_confirm",
                "message": ("Press 'Restart Chrome with browser control' "
                            "to close Chrome and reopen it on VYUK's "
                            "separate debug profile with the connection "
                            "port. Your tabs will come back when you next "
                            "open Chrome normally — they are safe in your "
                            "everyday profile; the relaunched window is "
                            "the debug profile, so your logins and tabs "
                            "are not in it. Unsaved work in web forms "
                            "may be lost.")}
    if not sys.platform.startswith("win"):
        port = _port()
        return {"state": "needs_manual_restart",
                "message": ("Restarting Chrome is only built for "
                            "Windows. Close Chrome yourself, then start "
                            "it with "
                            f"--remote-debugging-port={port} "
                            f"--user-data-dir={_debug_profile_dir()} "
                            "and press 'Enable browser control' again.")}
    try:
        _set_opt_in(True)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't turn browser control on ({exc}).")
    port = _port()
    # GAPS-3: diagnose the debug profile BEFORE closing Chrome.
    # A default / unusable profile must refuse here — closing the
    # user's Chrome and then discovering the profile is unusable
    # would leave them with no browser at all. "locked" is
    # allowed through: the close below ends the lock holder, and
    # the launch step re-diagnoses after the close.
    profile_dir = _debug_profile_dir()
    diag = _diagnose_profile_dir(profile_dir, _chrome_running())
    if diag["state"] not in ("ok", "locked"):
        return {"state": _PROFILE_REFUSAL_STATE.get(diag["state"],
                                                    "profile_unusable"),
                "message": diag["message"]}
    if _chrome_running() is True and not _close_chrome():
        return {"state": "restart_failed",
                "message": ("Chrome didn't close, so I can't reopen it "
                            "with the connection port. Close Chrome "
                            "yourself, then press 'Launch Chrome with "
                            "browser control'.")}
    exe = _find_chrome_exe()
    if exe is None:
        return {"state": "chrome_not_found",
                "message": ("Chrome closed, but I couldn't find it to "
                            "relaunch it. Start Chrome yourself with "
                            f"--remote-debugging-port={port} "
                            f"--user-data-dir={profile_dir}, then press "
                            "'Enable browser control' again.")}
    launched, launch_state, launch_message = _launch_chrome_checked(
        exe, port)
    if not launched:
        return {"state": launch_state, "message": launch_message}
    if not _wait_for_handshake(port):
        return {"state": "launch_timeout",
                "message": ("Chrome reopened but its connection port "
                            f"({port}) never answered. Press 'Enable "
                            "browser control' again, or start Chrome "
                            "yourself with "
                            f"--remote-debugging-port={port} "
                            f"--user-data-dir={profile_dir}.")}
    return _attach_and_verify(note=_DEBUG_PROFILE_NOTE.format(
        dir=profile_dir))


def disable_browser_control() -> dict:
    """Turn the opt-in OFF and detach cleanly (L7). Chrome and
    its tabs are untouched — attach-only, per v5.70. Never
    raises."""
    try:
        was_on = _opt_in()
        was_connected = _is_connected()
        if was_connected:
            try:
                _detach()
            except Exception:  # noqa: BLE001 — detach is best-effort
                pass
        _set_opt_in(False)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Couldn't turn browser control off ({exc}).")
    if not was_on and not was_connected:
        return {"state": "already_off",
                "message": "Browser control is already off."}
    return {"state": "disabled",
            "message": ("Browser control is off. VYUK is detached "
                        "from your Chrome — Chrome itself and your "
                        "tabs are untouched.")}
