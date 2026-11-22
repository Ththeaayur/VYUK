"""browser_cdp concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


_CDP_URL = "http://localhost:9222"


_DEFAULT_CDP_PORT = 9222


_REAL_ATTACH_CONFIG = "browser_allow_real_chrome_attach"


_DEBUG_PORT_CONFIG = "browser_debug_port"


_REAL_ATTACH_REFUSAL = (
    "Attaching to your real Chrome is an explicit opt-in and it is "
    "OFF by default (v5.49, programme item 33) — I didn't try to "
    "attach, and no browser path reaches your real profile while "
    "it's off. To allow it in one tap, tap 'Connect my browser' — "
    "it turns this on, restarts Chrome with the connection port if "
    "needed, and checks the attach. Or use Settings → Browser "
    "control → Enable browser control. Or turn on "
    "'browser_allow_real_chrome_attach' in Settings/config (or set "
    "BROWSER_ALLOW_REAL_CHROME_ATTACH=1), then start Chrome with "
    "--remote-debugging-port={port} and ask again. With it off "
    "there is no browser I can drive at all (v5.70: your real "
    "Chrome is the only browser)."
)


def _config_value(name: str, default=None):
    """getattr(name) from core.config — the module first, then its
    `config` AppConfig object — or `default` when neither carries
    it (or config can't be imported at all). This is the
    capture_guard pattern used elsewhere in the codebase;
    browser.py reads settings ONLY through here, dynamically, so
    a field added to core/config.py later is picked up with no
    import-time coupling."""
    try:
        from .. import config as _cfg_mod  # Phase 3 D2a: config stays flat in core/
    except Exception:  # noqa: BLE001 — browser must still run
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


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _real_chrome_attach_allowed() -> bool:
    """v5.49 (programme item 33): the real-profile attach opt-in.
    Default OFF: the config field (BROWSER_ALLOW_REAL_CHROME_ATTACH)
    decides. GAPS-11 removed the legacy VYUK_ALLOW_REAL_CHROME_ATTACH
    fallback: it was dead in production (the field is always
    non-None, so the leg only ever fired under a stubbed config),
    and reviving it would let a legacy env var enable attach
    despite the field being False. Anything unrecognised reads as
    OFF."""
    value = _config_value(_REAL_ATTACH_CONFIG, None)
    if value is not None:
        return _truthy(value)
    return False


def _cdp_port() -> int:
    """The debug port for the dedicated-profile launch and the
    (opt-in) real-Chrome attach: config browser_debug_port when it
    carries a sane port, else the defined default 9222."""
    # Round-3 fix #7 (A-UI4): the VYUK_-prefixed debug-port
    # fallback is gone — it was dead in production (the field
    # always carries its default, so the `raw is None` leg only
    # ever fired under a stubbed config; GAPS-11 removed the same
    # shape for the VYUK_-prefixed real-Chrome-attach var). The
    # working override is BROWSER_DEBUG_PORT, via the field.
    raw = _config_value(_DEBUG_PORT_CONFIG, None)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CDP_PORT
    return port if 1024 <= port <= 65535 else _DEFAULT_CDP_PORT


def _cdp_url_for(port: int) -> str:
    return _CDP_URL if port == _DEFAULT_CDP_PORT else f"http://localhost:{port}"


def _cdp_fetch_json(port: int, path: str, timeout: float = 1.5):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"User-Agent": "VYUK-CDP-Probe"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# Chrome target types a /json/list reply may carry (L-21): the
# identity check below accepts a list only when at least one entry
# carries one of these. The set is the DevTools target taxonomy
# (browser / page / worker / service_worker / background_page /
# shared_worker / other) — a foreign JSON server answering a
# generic list has none of them.
_CDP_TARGET_TYPES = frozenset({
    "browser", "page", "worker", "service_worker",
    "background_page", "shared_worker", "other",
})


def _cdp_response_is_chrome(data) -> bool:
    """True when a /json/version or /json/list reply is shaped
    like Chrome's own (L-21): a version dict must name Chrome in
    its Browser field ("Chrome/..." — headless Chrome reports
    "HeadlessChrome/...", which contains it), and a target list
    must carry at least one entry with a Chrome target type (see
    _CDP_TARGET_TYPES). A generic JSON dict or list — a foreign
    local server that merely answers JSON — is NOT Chrome,
    however well-formed. A server that deliberately mimics
    Chrome's whole shape is outside this check's threat model (a
    local attacker crafting a fake Chrome is not "any JSON
    server"); the check's job is to stop a foreign server being
    accepted as the user's browser on the strength of JSON alone.
    The list check deliberately does NOT require a
    webSocketDebuggerUrl: the toggle-mode server's list shape is
    unrecorded (N-9 recording OWED) and the lab fake carries a
    bare {"id", "type": "page"} entry, so requiring the socket
    URL could reject a real toggle server."""
    if isinstance(data, dict):
        browser = data.get("Browser")
        return isinstance(browser, str) and "Chrome/" in browser
    if isinstance(data, list):
        return any(isinstance(t, dict)
                   and t.get("type") in _CDP_TARGET_TYPES
                   for t in data)
    return False


def _cdp_port_answers(port: int, timeout: float = 1.0) -> bool:
    """True when a DevTools endpoint answers on ``port`` — a
    /json/version or /json/list reply (a toggle-mode server 404s
    /json/version, so the list is tried too) that is shaped like
    Chrome's own (see _cdp_response_is_chrome). False when nothing
    answers (port closed / not yet up), when the reply is not
    JSON, or when the reply is JSON but not Chrome-shaped (L-21:
    a foreign local server answering generic JSON must not be
    accepted as the user's browser). This is the C2-F1 probe:
    whether the endpoint answers is the authority on whether a
    Chrome is reachable — a process name is not. Never raises."""
    for path in ("/json/version", "/json/list"):
        try:
            data = _cdp_fetch_json(port, path, timeout=timeout)
        except Exception:  # noqa: BLE001 — no answer on this path
            continue
        if _cdp_response_is_chrome(data):
            return True
    return False


def _cdp_handshake(port: int) -> dict | None:
    """The endpoint's identity papers: {version, targets} from
    /json/version + /json/list, or None when nothing answers
    (port closed / not yet up — a retryable state, NOT proof of
    a foreign browser)."""
    try:
        version = _cdp_fetch_json(port, "/json/version")
    except Exception:  # noqa: BLE001 — no answer yet
        return None
    try:
        targets = _cdp_fetch_json(port, "/json/list")
    except Exception:  # noqa: BLE001 — version alone still identifies
        targets = None
    return {"version": version, "targets": targets}


# ---------------------------------------------------------------------------
# Toggle-mode handshake bypass (click-anywhere Batch 1, Plan 9 §1.3, N-8/P-7)
#
# Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
#
# The existing _cdp_handshake fetches /json/version FIRST and returns
# None on failure — on a toggle server that 404s /json/version it
# returns None before ever trying /json/list, so it must NOT be used
# for toggle ports. This bypass skips /json/version entirely and
# fetches /json/list directly (same 1.5 s timeout as _cdp_fetch_json).
#
# It takes the BROWSER-type target's webSocketDebuggerUrl
# (ws://.../devtools/browser/<id>) — NEVER a page target's socket
# (/devtools/page/<id> does not speak the Browser/Target domains
# Playwright needs). If /json/list lists only page targets the bypass
# has NO input: the CDP leg is launch-only and the fallback is
# sight+click. If the toggle server serves neither /json/version nor
# /json/list, the same launch-only outcome applies.
#
# UNVERIFIED PREMISE (N-9 recording OWED, unrun): whether a real
# toggle-mode server lists a browser-type target at all is settled by
# the N-9 recording from his machine, not by assumption. The
# browser-target check below is DATA-DRIVEN: a page-targets-only
# recording flips the bypass to launch-only with no code change.
# The lab fake is built from the plan's stated shape, not from a
# recording — do not cite it as proof of the real toggle server.
# ---------------------------------------------------------------------------

_TOGGLE_LAUNCH_ONLY_REASON = (
    "launch-only, fallback is sight+click"
)

_LAST_TOGGLE_REASON = None


def _cdp_handshake_toggle_reason():
    """The reason the last _cdp_handshake_toggle call returned None
    (None when the last call succeeded or none has run)."""
    return _LAST_TOGGLE_REASON


def _cdp_handshake_toggle(port: int) -> dict | None:
    """Toggle-mode probe: fetch /json/list directly (skipping
    /json/version) and take the BROWSER-type target's
    webSocketDebuggerUrl. Returns ``{"webSocketDebuggerUrl": ...,
    "target": {...}}`` on success, else None — with the reason in
    :func:`_cdp_handshake_toggle_reason`."""
    global _LAST_TOGGLE_REASON
    _LAST_TOGGLE_REASON = None
    try:
        targets = _cdp_fetch_json(port, "/json/list")
    except Exception:  # noqa: BLE001 — neither endpoint answers
        _LAST_TOGGLE_REASON = (
            "toggle server serves neither /json/version nor /json/list — "
            + _TOGGLE_LAUNCH_ONLY_REASON)
        return None
    if not isinstance(targets, list):
        _LAST_TOGGLE_REASON = (
            "/json/list did not return a target list — "
            + _TOGGLE_LAUNCH_ONLY_REASON)
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") == "browser" and target.get("webSocketDebuggerUrl"):
            return {"webSocketDebuggerUrl": target["webSocketDebuggerUrl"],
                    "target": target}
    _LAST_TOGGLE_REASON = (
        "/json/list lists only page targets (no browser-type target) — "
        + _TOGGLE_LAUNCH_ONLY_REASON)
    return None


_PROFILE_HINT_KEYS = frozenset({
    "user-data-dir", "userdata-dir", "userdatadir", "user_data_dir",
    "profile", "profiledir", "profile_dir",
})


def _norm_profile(value) -> str:
    return os.path.normcase(str(value or "")).strip().rstrip("\\/")


# ---------------------------------------------------------------------------
# Chrome 136 profile wall (GAPS-3, BLOCKS #4, 2026-11-12)
#
# WHAT CHANGED (primary source — Will Harris, Chrome for Developers,
# "Changes to remote debugging switches to improve security",
# 17 March 2025, https://developer.chrome.com/blog/remote-debugging-port):
#   "from Chrome 136 we're making changes to the behavior of
#   --remote-debugging-port and --remote-debugging-pipe. These
#   switches will no longer be respected if attempting to debug
#   the default Chrome data directory. These switches must now be
#   accompanied by the --user-data-dir switch to point to a
#   non-standard directory. A non-standard data directory uses a
#   different encryption key meaning Chrome's data is now
#   protected from attackers."
# The reason: infostealers were launching Chrome with a debugging
# port and reading cookies out of the user's real profile, which
# bypassed App-Bound Encryption. The failure is SILENT — Chrome
# prints no warning, the flag is simply ignored, and no port
# opens — which is why "remote-debugging-port not working" is
# such a popular search.
#
# WHAT IT MEANS FOR VYUK (the BLOCKS #4 hole, proved on the
# backup): the launch path built [chrome, --remote-debugging-port=N]
# with no --user-data-dir, and the attach failure blamed a missing
# flag — false on Chrome 136+ even when the flag WAS used. The
# one-click enable flow could never open the port against a
# normally-running Chrome 136+. The handling in this module:
#   * the launch command is built in exactly one place
#     (_launch_argv) and ALWAYS carries a non-default
#     --user-data-dir (_debug_profile_dir);
#   * the dir is diagnosed BEFORE any launch (_diagnose_profile_dir):
#     the default profile dir, a live profile lock, a file where a
#     folder should be, and an unwritable dir are each refused with
#     the named cause — never a silent no-port launch;
#   * the attach failure names the wall when Chrome is running,
#     and the enable flow reads DevToolsActivePort (the file Chrome
#     writes when a debugging server starts — via the flag launch
#     or the chrome://inspect remote-debugging toggle on newer
#     Chrome) as a HINT for a port the running Chrome opened itself.
# The honest cost, stated wherever the launch is described: a
# non-default user-data-dir is a SEPARATE profile — no logins, no
# extensions, no tabs from the everyday profile. The user signs in
# inside it if a site needs them logged in. That is Chrome's rule,
# not VYUK's choice, and no message here pretends otherwise.

_CHROME_136_BLOG_URL = (
    "https://developer.chrome.com/blog/remote-debugging-port")

_CHROME_136_WALL = (
    "Chrome 136 and later ignore --remote-debugging-port (and "
    "--remote-debugging-pipe) on the default Chrome profile — the "
    "flag is silently ignored, no port opens, and no warning is "
    "printed (Chrome for Developers, 'Changes to remote debugging "
    "switches to improve security', 17 March 2025: the switches "
    "must be accompanied by --user-data-dir pointing to a "
    "non-standard directory, because infostealers were using the "
    "port to pull cookies out of real profiles). So the debug port "
    "can only be opened on a separate profile, never on your "
    "everyday logged-in Chrome.")

_CHROME_136_SHORT = (
    "on Chrome 136 or later the debug port is ignored on your "
    "everyday profile — the flag must be paired with "
    "--user-data-dir pointing at a non-default folder")

_DEBUG_PROFILE_CONFIG = "browser_debug_profile_dir"


_CHROME_PROC_NAMES = frozenset({
    "chrome.exe", "chrome", "google-chrome",
    "google-chrome-stable", "chromium", "chromium.exe",
    # Field check B7 (2026-11-17): the headless shell's process
    # name is "chrome-headless-shell" (psutil's comm truncates
    # it to "chrome-headless") — without these the running-Chrome
    # probe misses a headless Chrome entirely and the enable
    # flow reports needs_launch instead of needs_restart.
    "chrome-headless-shell", "chrome-headless",
})


def _home_dir():
    """The user's home dir, or None when it cannot be told."""
    try:
        return Path.home()
    except Exception:  # noqa: BLE001 — home is not guaranteed
        return None


def _default_chrome_profile_dir():
    """The platform default Chrome 'User Data' dir — the dir
    Chrome 136 refuses to debug. None when the home directory
    cannot be determined. This is a PATH FACT about Chrome's
    install layout, not a guess at the user's profile: Windows
    %LOCALAPPDATA%\\Google\\Chrome\\User Data, macOS
    ~/Library/Application Support/Google/Chrome, Linux
    ~/.config/google-chrome."""
    home = _home_dir()
    if home is None:
        return None
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / "Google" / "Chrome" / "User Data"
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support"
                / "Google" / "Chrome")
    return home / ".config" / "google-chrome"


def _debug_profile_dir():
    """VYUK's debug profile dir for the launch path: config
    browser_debug_profile_dir (env BROWSER_DEBUG_PROFILE_DIR)
    when set, else the per-user default — %LOCALAPPDATA%\\VYUK\\
    chrome-debug-profile on Windows, ~/Library/Application
    Support/VYUK/chrome-debug-profile on macOS,
    $XDG_DATA_HOME/vyuk/chrome-debug-profile (else
    ~/.local/share/vyuk/chrome-debug-profile) elsewhere. A stable
    per-user dir, not a temp dir: logins made inside the debug
    profile persist across restarts. NEVER the default Chrome
    profile dir — callers diagnose the result before launching,
    and _diagnose_profile_dir refuses the default dir outright.
    GAPS-11 removed the legacy VYUK_BROWSER_DEBUG_PROFILE_DIR
    fallback (dead in production; the canonical env var above
    controls the field)."""
    raw = _config_value(_DEBUG_PROFILE_CONFIG, None)
    if raw:
        return Path(str(raw)).expanduser()
    home = _home_dir()
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local)
        elif home is not None:
            base = home / "AppData" / "Local"
        else:
            base = Path(".")
        return base / "VYUK" / "chrome-debug-profile"
    if sys.platform == "darwin":
        base = (home / "Library" / "Application Support"
                if home is not None else Path("."))
        return base / "VYUK" / "chrome-debug-profile"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "vyuk" / "chrome-debug-profile"
    base = home / ".local" / "share" if home is not None else Path(".")
    return base / "vyuk" / "chrome-debug-profile"


def _is_default_profile_dir(path) -> bool:
    """True when ``path`` IS Chrome's default profile dir
    (normcase/normpath compare, so separators and case on
    Windows cannot smuggle it past)."""
    if path is None:
        return False
    default = _default_chrome_profile_dir()
    if default is None:
        return False
    return (os.path.normcase(os.path.normpath(str(path)))
            == os.path.normcase(os.path.normpath(str(default))))


def _read_devtools_active_port(profile_dir):
    """Port named by <profile_dir>/DevToolsActivePort, or None.
    Chrome writes this two-line file (port, then the browser
    path) when a debugging server starts — via the flag launch
    or the chrome://inspect remote-debugging toggle on newer
    Chrome. The file OUTLIVES the server, so a named port is a
    HINT, never proof: callers must still probe it. A first line
    of "127.0.0.1:9222" parses to 9222 as well as a bare "9222".
    None when the file is missing, unreadable, or unparsable."""
    if profile_dir is None:
        return None
    try:
        text = (Path(profile_dir) / "DevToolsActivePort").read_text(
            encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — missing/unreadable is None
        return None
    lines = text.splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    if ":" in first:
        first = first.rsplit(":", 1)[-1].strip()
    try:
        port = int(first)
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def _is_browser_cmdline(cmdline, status=None) -> bool:
    """True when ``cmdline`` looks like a Chrome BROWSER process's
    launch — as opposed to a helper's, or a stray namesake's.
    A helper (renderer / gpu / zygote / network / utility / ...) is
    identified positively by a ``--type=`` flag. A positional
    argument must look like something Chrome accepts — a flag, a
    URL, or a file path; a bare token (a sleep duration, a stray
    word) is not a browser launch. "Looks like a URL or a path"
    means it carries a colon (a scheme or a host:port — so
    about:blank and localhost:9334 count), a slash or backslash,
    or a dot (a domain or a file extension). Residual risks,
    stated plainly:
    a real Chrome launched with a bare-word search query
    (``chrome hello``) reads as not-a-browser, and a process named
    chrome with no arguments at all reads as one — both are vanishingly
    rare, and the remedy the diagnosis prescribes (Enable browser
    control, which launches a fresh debug Chrome) still works
    either way.
    R1 (2026-11-20): a process whose status is "zombie" is DEAD,
    whatever its cmdline reads as. A SIGKILLed Chrome whose parent
    never reaps it stays a zombie with cmdline None (measured: the
    zombie reports cmdline None for at least 20 s), and the None
    rule below would otherwise count it as a browser — the exact
    misdiagnosis this function exists to prevent. The zombie case
    is distinguished from the B7 seam case by STATUS, not by
    cmdline: a seam/mock process carries no status info at all
    (``status`` None), so the None rule still counts it as a
    browser. An empty cmdline (a defunct process with no
    arguments) is dead, not running.
    ``cmdline`` None (no cmdline info — a seam/mock, or psutil
    could not read it) counts as a browser: absence of evidence is
    not evidence of being a helper."""
    if status == "zombie":
        return False
    if cmdline is None:
        return True
    if not cmdline:
        return False
    for arg in cmdline[1:]:
        if arg == "--type" or arg.startswith("--type="):
            return False
    for arg in cmdline[1:]:
        if arg.startswith("-"):
            continue
        if (":" in arg or "/" in arg or "\\" in arg
                or "." in arg):
            continue
        return False
    return True


# R1 (2026-11-20): Popen handles of Chromes THIS app launched
# (registered by browser_enable._launch_chrome). A launched Chrome
# that is later killed or crashes stays a zombie child of this
# process until somebody wait()s on it — _chrome_running() reaps
# the registered handles before it scans, so a VYUK-launched
# Chrome never leaves an unreaped zombie behind.
_LAUNCHED_CHROME_PROCS: list = []


def note_launched_chrome(proc) -> None:
    """Register a Chrome Popen this app launched, so
    _chrome_running() can reap it if it dies (R1). Never raises."""
    try:
        _LAUNCHED_CHROME_PROCS.append(proc)
    except Exception:  # noqa: BLE001 — registration is best-effort
        pass


def _reap_launched_chrome() -> None:
    """Reap registered launched-Chrome processes that have exited.
    poll() alone does not reap; wait() does. Never raises."""
    try:
        alive = []
        for proc in _LAUNCHED_CHROME_PROCS:
            try:
                if proc.poll() is None:
                    alive.append(proc)
                else:
                    try:
                        proc.wait(timeout=0)
                    except Exception:  # noqa: BLE001 — already reaped
                        pass
            except Exception:  # noqa: BLE001 — one dead handle
                continue
        _LAUNCHED_CHROME_PROCS[:] = alive
    except Exception:  # noqa: BLE001 — reaping never breaks a scan
        pass


def _chrome_running():
    """True/False whether a Chrome BROWSER process is alive, None
    when it cannot be told (no psutil AND no tasklist answer).
    Moved here from browser_enable (GAPS-3) so the attach path
    can use it too; browser_enable re-exports the name, so its
    seam patches keep working. psutil is a declared tree
    dependency, imported lazily; tasklist is the Windows stdlib
    fallback. Never raises.
    C2-F1: "a Chrome process is alive" means the BROWSER process,
    not any process with a Chrome name. SIGKILLing the browser
    leaves its helpers (renderer / gpu / zygote / ...) alive for a
    moment, all still named chrome — a name-only scan reads True
    for a dead browser and the attach failure blames the Chrome 136
    wall. So the psutil path requires a browser-shaped command
    line (_is_browser_cmdline): no --type= helper flag, and every
    positional argument a URL or a path. The tasklist fallback
    (Windows, psutil missing) can only see names, so it may still
    read True for leftover helpers — a known weak fallback, kept
    only so a psutil-less install degrades to the old answer
    instead of no answer.
    R1: a SIGKILLed Chrome whose parent never reaps it stays a
    zombie (status "zombie", cmdline None) indefinitely, and the
    old scan counted it as a browser — the diagnosis still read
    "running / 136 wall" for a dead browser. Two defences, in
    order: (1) registered launched-Chrome handles are reaped
    first (_reap_launched_chrome), so a VYUK-launched Chrome
    never leaves a zombie behind; (2) any process whose status
    is "zombie" is skipped outright (_is_browser_cmdline's
    status rule), so an unreaped zombie from any parent reads
    as dead. The B7 seam case (a name-only mock, no status
    info) still counts as a browser — the zombie case is
    distinguished by status, not by cmdline."""
    _reap_launched_chrome()
    try:
        import psutil

        for proc in psutil.process_iter(["name", "cmdline", "status"]):
            try:
                info = proc.info or {}
                name = (info.get("name") or "").strip().lower()
                cmdline = info.get("cmdline")
                status = info.get("status")
            except Exception:  # noqa: BLE001 — one dead proc
                continue
            if (name in _CHROME_PROC_NAMES
                    and _is_browser_cmdline(cmdline, status)):
                return True
        return False
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — psutil failure reads unknown
        return None
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True, text=True, timeout=10)
            if out.returncode != 0:
                return None
            return "chrome.exe" in (out.stdout or "").lower()
        except Exception:  # noqa: BLE001 — tasklist failure unknown
            return None
    return None


def _diagnose_profile_dir(path, chrome_running=None):
    """Diagnose a Chrome user-data-dir BEFORE launching against
    it. Returns {"state", "message", "dir", "note"}; never raises.
    States:
      ok — the dir exists (created if needed), is writable, and
        no live Chrome holds its SingletonLock. "note" names a
        stale lock (lock file present, no Chrome running — a crash
        leftover Chrome recovers; safe to proceed).
      default_profile — path IS the default Chrome profile dir:
        Chrome 136 ignores the debug port there, so launching
        against it can never open the port.
      locked — SingletonLock is held AND a Chrome process is
        running: a second launch on the same dir would be
        swallowed by the running instance (no new window, no
        debug port, no error).
      not_a_directory — the path exists as a file.
      unwritable — the dir cannot be created or written to.
      missing — no path given.
      unknown — a lock file exists but Chrome's state cannot be
        told: fail closed, the launch must not guess.
    The diagnosis creates the dir when it is missing (the launch
    would create it anyway); it never deletes or repairs a lock.
    """
    if path is None or not str(path).strip():
        return {"state": "missing", "dir": "",
                "message": ("No debug profile dir is configured and "
                            "no default could be determined, so there "
                            "is nowhere to launch Chrome with the "
                            "debug port. Set browser_debug_profile_dir "
                            "in Settings/config (or "
                            "BROWSER_DEBUG_PROFILE_DIR) to a "
                            "non-default folder and try again."),
                "note": ""}
    p = Path(str(path)).expanduser()
    if _is_default_profile_dir(p):
        return {"state": "default_profile", "dir": str(p),
                "message": ("The debug profile dir is Chrome's "
                            f"default profile dir ({p}). Chrome 136 "
                            "and later ignore --remote-debugging-port "
                            "on the default profile, so launching "
                            "against it can never open the port. Use "
                            "VYUK's separate debug profile instead — "
                            "leave browser_debug_profile_dir unset, or "
                            "point it at a non-default folder."),
                "note": ""}
    if p.exists() and not p.is_dir():
        return {"state": "not_a_directory", "dir": str(p),
                "message": (f"The debug profile path ({p}) exists but "
                            "is a file, not a folder — Chrome cannot "
                            "use it as a profile. Point "
                            "browser_debug_profile_dir at a folder and "
                            "try again."),
                "note": ""}
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — creation failure
        return {"state": "unwritable", "dir": str(p),
                "message": (f"The debug profile dir ({p}) cannot be "
                            f"created ({exc}). Chrome cannot start a "
                            "profile there. Point "
                            "browser_debug_profile_dir at a writable "
                            "folder and try again."),
                "note": ""}
    try:
        writable = os.access(p, os.W_OK)
        lock_present = (p / "SingletonLock").exists()
    except Exception as exc:  # noqa: BLE001 — a path Chrome cannot
        # even stat (a null byte, a broken mount) is unusable, and
        # the diagnosis never raises.
        return {"state": "unwritable", "dir": str(p),
                "message": (f"The debug profile dir ({p}) cannot be "
                            f"examined ({exc}). Chrome cannot start a "
                            "profile there. Point "
                            "browser_debug_profile_dir at a writable "
                            "folder and try again."),
                "note": ""}
    if not writable:
        return {"state": "unwritable", "dir": str(p),
                "message": (f"The debug profile dir ({p}) is not "
                            "writable. Chrome cannot start a profile "
                            "there. Point browser_debug_profile_dir at "
                            "a writable folder and try again."),
                "note": ""}
    if lock_present:
        if chrome_running is True:
            return {"state": "locked", "dir": str(p),
                    "message": (f"The debug profile dir ({p}) is "
                                "locked by a Chrome that is already "
                                "running (its SingletonLock is held). "
                                "A second Chrome launched on the same "
                                "profile would be swallowed by the "
                                "running instance — no new window, no "
                                "debug port, no error. Close that "
                                "Chrome first, then launch again."),
                    "note": ""}
        if chrome_running is False:
            return {"state": "ok", "dir": str(p), "message": "",
                    "note": ("The debug profile has a leftover lock "
                             "file from a Chrome that is no longer "
                             "running (a crash leftover); Chrome "
                             "recovers it on launch.")}
        return {"state": "unknown", "dir": str(p),
                "message": (f"The debug profile dir ({p}) has a lock "
                            "file (SingletonLock) but I can't tell "
                            "whether a Chrome process is running, so "
                            "I can't tell whether the profile is "
                            "locked. Close Chrome yourself, then try "
                            "again."),
                "note": ""}
    return {"state": "ok", "dir": str(p), "message": "", "note": ""}


def _launch_argv(exe, port, profile_dir):
    """The ONE place the Chrome launch command is built: the
    debug port AND the non-default --user-data-dir Chrome 136
    requires. Callers diagnose the dir first
    (_diagnose_profile_dir) — this builder never receives the
    default profile dir from a diagnosed path."""
    return [str(exe), "--remote-debugging-port=%d" % int(port),
            "--user-data-dir=%s" % str(profile_dir)]
