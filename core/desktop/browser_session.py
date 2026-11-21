"""browser_session concern extracted from core/browser.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/browser."""

import datetime
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from .browser_cdp import (_real_chrome_attach_allowed, _cdp_port, _cdp_url_for, _REAL_ATTACH_REFUSAL, _chrome_running, _cdp_port_answers, _default_chrome_profile_dir, _read_devtools_active_port, _CHROME_136_WALL, _CHROME_136_SHORT)  # GAPS-18: trimmed to the 10 used names
from .browser_engine import browsers_dir, start_playwright  # GAPS-18: the one engine interface


log = logging.getLogger("nova.browser")


_JOB_TIMEOUT_S = 30.0          # generic jobs: clicks, reads, status…


_LAUNCH_JOB_TIMEOUT_S = 90.0   # session-establishing jobs: a cold


_NAV_JOB_TIMEOUT_S = 45.0      # navigation jobs (goto / history /


_GOTO_TIMEOUT_MS = 20_000


_ELEMENT_TIMEOUT_MS = 8_000


_SETTLE_TIMEOUT_MS = 4_000


_ACTIVITY_LOG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "activity.log"  # Phase 3 D2a: one level deeper in core/desktop/
)


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"  # Phase 3 D2a: one level deeper in core/desktop/


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _short(exc: Exception, limit: int = 220) -> str:
    """One-line, length-capped rendering of an exception for result strings."""
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
    return msg[:limit]


class BrowserWorker(threading.Thread):
    """Daemon thread owning every Playwright object.

    Jobs are (callable, result list, done event) triples. The callable
    runs here, on this thread, and must return a human string.
    """

    def __init__(self):
        super().__init__(name="NovaBrowserWorker", daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        # When the currently running job started (monotonic), or None.
        # _submit reads this to recognise a wedged worker.
        self.job_started_at: float | None = None

    def run(self):  # noqa: D102 — thread entry point
        while True:
            fn, box, done = self.jobs.get()
            self.job_started_at = time.monotonic()
            try:
                box.append(fn())
            except Exception as e:  # noqa: BLE001 — a job must never kill the worker
                if _looks_like_target_closed(e):
                    _reset_state("the browser or page was closed")
                    box.append(
                        "Browser action failed: the browser or page was "
                        f"closed ({_short(e)}). The connection has been "
                        "reset — launch the browser again."
                    )
                else:
                    box.append(f"Browser action failed: {_short(e)}")
            finally:
                self.job_started_at = None
                done.set()


def _looks_like_target_closed(exc: Exception) -> bool:
    msg = str(exc)
    return "Target closed" in msg or (
        "has been closed" in msg
        and ("Target" in msg or "Browser" in msg or "Context" in msg or "Page" in msg)
    )


def _log_browser_event(message: str) -> None:
    """One line about a connection-level event (a reset, an
    abandonment, a profile lock) to the activity log — resets
    used to write NOTHING, which is why the field failure left
    no trace (v5.53, F5). The module logger gets it too. Never
    raises."""
    log.warning("browser: %s", message)
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        _ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_ACTIVITY_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} | browser | {message}\n")
    except Exception:  # noqa: BLE001 — logging must never break a reset
        pass


_worker: BrowserWorker | None = None


_worker_lock = threading.Lock()


_nav_timeout_streak = 0


def _submit(fn, timeout: float | None = None, kind: str = "action") -> str:
    """Run fn() on the browser worker thread and return its string.

    Starts the worker lazily. Never raises: timeouts and worker
    failures come back as explanatory strings.

    Job classes (v5.53, F5 — field forensics Failure A):
      * "launch" — session-establishing jobs, on the long
        budget: a cold browser start is slow, not wedged, and
        no longer eats the budget of the action behind it.
      * "nav" — navigation jobs (goto / back / forward /
        reload / new tab). A nav job that exhausts its budget
        gets a SOFT result — the page is still loading, the
        connection is alive — and the worker is NEVER
        abandoned for it: abandoning a merely-slow navigation
        is what tore healthy sessions down in the field, at a
        suspiciously regular ~42 s. Two consecutive nav
        timeouts declare the session suspect in the result;
        even then, nothing is silently reset.
      * "probe" — read-only peeks (the playback probe): a
        timeout means "couldn't look right now", never a
        reset.
      * anything else ("action") — the original v4.4 contract
        below: a job that outlives the budget is treated as
        wedged; the worker is abandoned and the connection
        reset, so later jobs are not queued behind it forever.
    An explicit timeout always wins over the class budget.
    """
    global _worker, _nav_timeout_streak
    if timeout is None:
        timeout = (
            _LAUNCH_JOB_TIMEOUT_S if kind == "launch"
            else _NAV_JOB_TIMEOUT_S if kind == "nav"
            else _JOB_TIMEOUT_S
        )
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = BrowserWorker()
            _worker.start()
        worker = _worker
    box: list[str] = []
    done = threading.Event()
    worker.jobs.put((fn, box, done))
    if not done.wait(timeout):
        if kind == "nav":
            _nav_timeout_streak += 1
            _log_browser_event(
                "navigation job timed out after "
                f"{timeout:.0f}s (consecutive nav timeout "
                f"{_nav_timeout_streak}); connection kept")
            if _nav_timeout_streak >= 2:
                return (
                    "The page is still loading — the last two "
                    "navigations in a row have timed out, so "
                    "the browser session looks suspect, but "
                    "the connection is still alive and nothing "
                    "was reset. Ask me to read the page, wait, "
                    "or relaunch the browser."
                )
            return (
                "The page is still loading — it didn't "
                f"finish within {timeout:.0f}s, but the "
                "connection is alive and nothing was reset. "
                "Ask me to read the page, wait a moment, or "
                "navigate again."
            )
        if kind == "probe":
            _log_browser_event(
                f"probe job timed out after {timeout:.0f}s; "
                "connection kept")
            return (
                f"Browser action timed out after {timeout:.0f}s "
                "— the browser is busy; nothing was reset."
            )
        # The job is NOT cancellable (Playwright sync calls can't be
        # interrupted), so the worker may stay busy forever. Recovery
        # (v4.4): abandon the wedged worker thread — it is a daemon
        # and dies with the process — and start a fresh worker with
        # reset state, so status/close/launch work again immediately
        # instead of queueing behind the stuck job until VYUK is
        # restarted. The message only promises what this code does.
        _abandon_worker(worker)
        return (
            f"Browser action timed out after {timeout:.0f}s — the page or "
            "browser appears stuck. I abandoned the stuck session and "
            "reset the browser connection, so you can launch a browser "
            "again right away (the previous browser window itself may "
            "still be open — close it yourself if it is frozen)."
        )
    if kind == "nav":
        _nav_timeout_streak = 0
    return box[0] if box else "Browser action failed: no result was returned."


def _abandon_worker(stuck: BrowserWorker) -> bool:
    """Replace a wedged worker with a fresh one; reset browser state.

    Returns True when the swap happened (the stuck worker was still
    the current one). State is reset by a job ON the fresh worker
    thread — _state is worker-thread-only everywhere else for the
    same thread-affinity reason as Playwright itself. Never raises.
    """
    global _worker
    with _worker_lock:
        if _worker is not stuck:
            return False
        fresh = BrowserWorker()
        fresh.start()
        _worker = fresh
    box: list[str] = []
    done = threading.Event()

    def _reset_job() -> str:
        _reset_state("worker abandoned")
        return "reset"

    fresh.jobs.put((_reset_job, box, done))
    done.wait(5)
    _log_browser_event(
        "browser worker abandoned (a job outlived its budget "
        "and could not be interrupted); connection reset on a "
        "fresh worker")
    return True


_state: dict = {
    "pw": None,        # started sync_playwright instance
    "browser": None,   # CDP Browser (None in persistent-context mode)
    "context": None,   # active BrowserContext
    "page": None,      # active Page
    "mode": None,      # "cdp" | None (v5.70: attach-only)
    "snapshot": None,  # latest element snapshot (browser_read 'elements'
                       # or an ambiguous-click listing): {url, title,
                       # seq, refs: {ref: {frame, name, role, line}}}
    "snap_seq": 0,     # monotonic snapshot counter (token uniqueness)
}


def _reset_state(reason: str = ""):
    """Drop every Playwright handle (worker thread only). Never raises.

    v5.70: there is no launched process to terminate any more —
    attach-only sessions never owned the Chrome process, so
    close stays detach-only. Every reset is written to the
    activity log: a connection reset is never silent again."""
    try:
        if _state["pw"] is not None:
            _state["pw"].stop()
    except Exception:  # noqa: BLE001
        pass
    _state.update(pw=None, browser=None, context=None, page=None, mode=None,
                  snapshot=None)
    _log_browser_event(
        "browser state reset" + (f" ({reason})" if reason else ""))


# -- session-state accessors (GAPS-18, D-4) -------------------------------
# The leaves used to poke the global _state dict directly (18 sites
# across browser_click / browser_elements / browser_nav). The dict
# stays the single store — it is worker-thread-only, like everything
# else here — but leaves reach it only through these accessors, so
# the store's shape is owned in this one module and a leaf can never
# invent a new key or read a half-reset field. All of these are
# worker-thread-only, same contract as _state itself.

def _read_snapshot():
    """The stored element snapshot, or None (worker thread only)."""
    return _state.get("snapshot")


def _next_snapshot_seq() -> int:
    """Advance the monotonic snapshot counter and return it."""
    seq = int(_state.get("snap_seq") or 0) + 1
    _state["snap_seq"] = seq
    return seq


def _store_snapshot(snapshot) -> None:
    """Replace the stored element snapshot."""
    _state["snapshot"] = snapshot


def _tracked_page():
    """The tracked active page as stored, without recovery.

    Unlike _active_page() this never creates a page and never picks
    a replacement — it answers "which page is tracked", for callers
    (tab listing) that must not conjure a tab."""
    return _state["page"]


def _set_active_page(page) -> None:
    """Record the tracked active page (None clears it)."""
    _state["page"] = page


def _context_pages():
    """The open pages of the active context (worker thread only)."""
    return _state["context"].pages


def _new_context_page():
    """Open a new page in the active context and track it as active."""
    page = _state["context"].new_page()
    _state["page"] = page
    return page


def _connected() -> bool:
    return _state["context"] is not None


def _start_pw():
    """Start the browser engine (worker thread only). Raises ImportError if absent.

    GAPS-1 (2026-11-12): the engine is stock Playwright, started
    directly — the patched engine was removed per the 2026-11-11 ruling
    (the v5.59 seam + A/B harness are deleted; there is no import
    swap and no second engine). A missing playwright is an honest
    ImportError (the attach path turns it into the "browser driving
    isn't available" refusal). Everything downstream is unchanged:
    the one worker thread, channel="chrome", the persistent profile,
    and the CDP attach (pw.chromium.connect_over_cdp) on the
    started instance.

    GAPS-18 (2026-11-13): the start itself lives in exactly one place —
    core/desktop/browser_engine.start_playwright, the single engine
    start call in the tree. This function is the session's caller of
    it; the automation driver (browser_automation._PlaywrightBackend)
    is the other caller of the same start. The old docstring claimed
    this function was the single live engine start — false while the
    automation start existed beside it; both starts now go through
    the one interface, and there is still no second engine.

    Loophole family this rewire closes — "a rewire that leaves a second
    engine, or a stale declaration": a second engine start path (the
    full-tree grep proves start_playwright is the ONLY engine start
    call site — no seam, no factory, no flag arm remains); a stale
    declaration (the patched engine removed from requirements.txt,
    requirements.lock and THIRD_PARTY_LICENSES.md; stock Playwright
    restored in all three — grep after); a test still asserting the
    patched engine (the browser suites are re-run; no test patches
    the stock import path); a silent fallback to the patched engine
    (there is none — the import names playwright directly); the attach
    path breaking (connect_over_cdp is the same Playwright API surface;
    the live proof is the real-Windows field leg, owed). Rejected:
    leaving the patched-engine declaration "just in case" (two engines
    ship, 278 MB); a second launch path importing anything but
    playwright here (this function is the single place the engine is
    chosen); gating on any engine flag (the parent decision is that
    Playwright IS the engine); renaming the "playwright_context"
    cookie-source label (vocabulary for the cookie source kind, not the
    engine package — tests + allowlist assert it).
    """
    return start_playwright()


_BROWSERS_DIR = browsers_dir()  # GAPS-18: single source of truth in browser_engine


def _active_page():
    """Return a live page from the active context (worker thread only).

    Recovers if the tracked page (or all pages) were closed externally
    — e.g. the user closed a tab in their own Chrome window.
    """
    ctx = _state["context"]
    if ctx is None:
        return None
    page = _state["page"]
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:  # noqa: BLE001
        pass
    try:
        pages = [p for p in ctx.pages if not p.is_closed()]
    except Exception:  # noqa: BLE001
        pages = []
    if pages:
        _state["page"] = pages[-1]
        return pages[-1]
    page = ctx.new_page()
    _state["page"] = page
    return page


def _page_label(page) -> str:
    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = "?"
    try:
        url = page.url
    except Exception:  # noqa: BLE001
        url = "?"
    return f"'{title}' — {url}"


def _note_browser_no_session() -> None:
    """Best-effort note to the Assistant strip that a browser
    call just refused for lack of a session (the strip's way
    back). Lazy import — never raises, never breaks a refusal."""
    try:
        from ..settings_voice import note_browser_no_session as _note

        _note()
    except Exception:  # noqa: BLE001 — the note never breaks a refusal
        pass


def _clear_browser_no_session() -> None:
    """Best-effort clear of the strip's no-session note — a
    browser call just succeeded, so the way back is no longer
    needed. Lazy import, never raises."""
    try:
        from ..settings_voice import clear_browser_no_session as _clear

        _clear()
    except Exception:  # noqa: BLE001 — the clear never breaks a call
        pass


def _need_connection() -> str | None:
    """None if connected, else the standard not-connected string."""
    if _connected():
        _clear_browser_no_session()
        return None
    return _guided_no_session()


_BOOTSTRAP_NOTE = "Attached to your Chrome to do this."


_OPEN_URL_BOUNDARY = (
    "open_url opens the system browser, which I can't see or "
    "drive afterwards — a page opened that way is not a "
    "controllable session, so it can't be searched, clicked, "
    "typed on or read here."
)


def _guided_no_session(bootstrap_failure: str = "") -> str:
    """The ONE not-connected error shape (v5.52 item 5).

    Teaches the open_url seam and the recovery. When the
    auto-bootstrap was attempted and failed, the failure is
    named in the second sentence. Short, and it names the
    one-tap way back first (first-run onboarding, 2026-11-16):
    the strip's "Connect my browser" button opens the same
    connect flow as the wizard's browser page; Settings →
    Browser control → Enable browser control stays as the
    second way in, never deleted.

    Side effect (the one choke point): noting the refusal in
    core/settings_voice, so the Assistant strip can offer the
    button. Never raises, never breaks the refusal.
    """
    _note_browser_no_session()
    failure = ""
    if bootstrap_failure:
        failure = (
            " I tried to attach to your Chrome to do this and "
            f"couldn't: {bootstrap_failure}"
        )
    return (
        "No controllable browser session is active."
        + failure
        + " Tap 'Connect my browser' and I'll set it up — one tap, "
        "about a minute. (Settings → Browser control → Enable "
        "browser control still works too.) "
        # GAPS-3 pin (test_chrome136): the no-session error names
        # the Chrome 136 wall, as _impl_status already does — the
        # wall is the usual reason a running Chrome can't be
        # reached, and the guided error is where the user first
        # meets it.
        "On Chrome 136 or later the port is ignored on your "
        "everyday profile, so Enable browser control launches "
        "Chrome on a separate debug profile instead. "
        + _OPEN_URL_BOUNDARY
    )


def _bootstrap_job():
    """Worker-thread job: attach to the user's real Chrome if none.

    Returns ("active", "") when a session already exists,
    ("started", launch_result) when the opt-in attach connected,
    ("failed", launch_result) when it did not. Exactly ONE attach
    attempt — no retries, no loops, and no other browser path
    exists to try (v5.70)."""
    if _connected():
        return ("active", "")
    result = _impl_attach_cdp()
    if _connected():
        return ("started", result)
    return ("failed", result)


def _ensure_session() -> tuple[bool, bool, str]:
    """Auto-bootstrap gate for the public tool functions.

    Returns (ready, started, failure): ready=False means no
    session could be established and failure carries the launch
    result for the guided error; started=True means THIS call
    bootstrapped, so the caller prefixes _BOOTSTRAP_NOTE."""
    outcome = _submit(_bootstrap_job, kind="launch")
    if isinstance(outcome, tuple) and len(outcome) == 2:
        state, detail = outcome
        if state == "active":
            return (True, False, "")
        if state == "started":
            return (True, True, "")
        return (False, False, str(detail))
    # A non-tuple outcome is _submit's own failure string
    # (timeout / worker failure) — the bootstrap did not land.
    return (False, False, str(outcome))


def _with_bootstrap(dispatch, *args, **kwargs) -> str:
    """Run a session-requiring dispatch behind the gate."""
    ready, started, failure = _ensure_session()
    if not ready:
        return _guided_no_session(failure)
    _clear_browser_no_session()
    result = dispatch(*args, **kwargs)
    if started:
        return f"{_BOOTSTRAP_NOTE} {result}"
    return result


def _attach_failure(port: int, cdp_url: str, exc: Exception) -> str:
    """The honest attach-failure message (GAPS-3, C2-F1). The old
    text blamed a missing --remote-debugging-port flag, which is
    false on Chrome 136+ — the flag is silently ignored on the
    default profile even when it WAS used. So: name the wall when
    Chrome is running, name the flag+user-data-dir pairing when it
    is not, and name the DevToolsActivePort hint when the running
    Chrome opened a debugging server on another port (the file
    outlives the server, so it is reported as a hint, never as a
    claim). Never raises.
    C2-F1: the CDP endpoint is probed FIRST — a process name is
    not a live browser. SIGKILLing Chrome leaves its helpers
    alive for a moment, all still named chrome, so the old
    process-name scan read True for a dead browser and took the
    "running" branch. When the port answers but the attach failed,
    Chrome is listening and the failure is something else; when it
    does not answer, the (browser-shaped) process scan decides
    between the 136 wall (a browser running with no port) and a
    dead browser — never "running" on a dead port. R1: the scan
    also treats a zombie (a SIGKILLed Chrome its parent never
    reaped) as dead, so the "running" branch needs a live browser,
    not a leftover process-table entry."""
    base = f"Couldn't attach to Chrome at {cdp_url} ({_short(exc)}). "
    try:
        port_answers = _cdp_port_answers(port)
    except Exception:  # noqa: BLE001 — a probe failure reads as no
        port_answers = False
    if port_answers:
        wall = (
            f"Chrome is answering on port {port} — it is running and "
            "its debugging server is listening — but the attach "
            f"itself failed ({_short(exc)}). Try again, or use "
            "Settings → Browser control → Enable browser control to "
            "reconnect.")
        return (base + wall + " Your real Chrome is the only "
                "browser I drive, so there is nothing else to fall "
                "back to.")
    try:
        running = _chrome_running()
    except Exception:  # noqa: BLE001 — a probe failure reads unknown
        running = None
    if running is True:
        wall = (
            "Chrome is running, and this is most likely the Chrome 136 "
            f"wall: {_CHROME_136_WALL} Use Settings → Browser control "
            "→ Enable browser control — it launches Chrome on VYUK's "
            "separate debug profile with the port — or, on newer "
            "Chrome, turn on the remote-debugging toggle in "
            "chrome://inspect in your running Chrome and press Enable "
            "again.")
    elif running is False:
        wall = (
            "Chrome isn't running — nothing is answering on port "
            f"{port}, and no Chrome browser process is running (it may "
            "have closed or crashed). Start it with the connection "
            "port AND a non-default --user-data-dir — "
            f"{_CHROME_136_SHORT} — or use Settings → Browser control "
            "→ Enable browser control, which does both for you.")
    else:
        wall = (
            "I can't tell whether Chrome is running. "
            f"{_CHROME_136_SHORT.capitalize()} — use Settings → "
            "Browser control → Enable browser control, which launches "
            "Chrome on the debug profile with the port.")
    hint = ""
    try:
        discovered = _read_devtools_active_port(
            _default_chrome_profile_dir())
    except Exception:  # noqa: BLE001 — the hint is best-effort
        discovered = None
    if discovered is not None and discovered != port:
        hint = (f" Chrome's own DevToolsActivePort file in your default "
                f"profile names port {discovered} — if you turned on "
                "the remote-debugging toggle in chrome://inspect, "
                "that is the port Chrome is listening on; press "
                "Enable browser control in Settings and it will try "
                "that port.")
    return (base + wall + hint + " Your real Chrome is the only "
            "browser I drive, so there is nothing else to fall back "
            "to.")


def _impl_attach_cdp(port=None) -> str:
    # v5.49 (programme item 33): attaching to the user's REAL
    # Chrome is an explicit opt-in, default OFF. This gate sits
    # before ANY Playwright/connection work, so with the opt-in
    # off this path is unreachable, not merely discouraged.
    # The refusal names the configured port (L4: never a
    # hardcoded 9222) and points at the one-click enable path.
    # GAPS-3: ``port`` is the CDP port to attach to — None means
    # the configured port; the enable flow passes a discovered
    # port (DevToolsActivePort) when the running Chrome opened
    # its debugging server itself.
    if not _real_chrome_attach_allowed():
        return _REAL_ATTACH_REFUSAL.format(port=_cdp_port())
    if _connected():
        return f"Already connected ({_state['mode']}). {_impl_status()}"
    if port is None:
        port = _cdp_port()
    try:
        if _state["pw"] is None:
            _state["pw"] = _start_pw()
    except ImportError:
        return (
            "Browser driving isn't available in this build of "
            "VYUK — the browser part didn't make it into "
            "the package."
        )
    pw = _state["pw"]
    cdp_url = _cdp_url_for(port)
    try:
        # Explicit timeout: a half-alive endpoint must not hold the
        # single worker thread indefinitely (audit B1).
        browser = pw.chromium.connect_over_cdp(cdp_url, timeout=10_000)
    except Exception as e:  # noqa: BLE001
        return _attach_failure(port, cdp_url, e)
    _adopt_browser(browser, mode="cdp")
    page = _active_page()
    return (
        "Attached to your Chrome (your real session — logins and open "
        f"tabs included). Active tab: {_page_label(page)}. "
        f"{len(_state['context'].pages)} tab(s) open."
    )


def _adopt_browser(browser, mode: str):
    _state["browser"] = browser
    _state["mode"] = mode
    contexts = browser.contexts
    _state["context"] = contexts[0] if contexts else browser.new_context()
    _state["page"] = None
    _active_page()


def _impl_close() -> str:
    if not _connected():
        return "No browser is connected, so there's nothing to close."
    # v5.70: attach-only — close NEVER touches the user's Chrome.
    # Detaching disconnects us; it does not kill their Chrome or
    # close their tabs, because VYUK never started it.
    try:
        if _state["browser"] is not None:
            _state["browser"].close()
        elif _state["context"] is not None:
            _state["context"].close()
    except Exception:  # noqa: BLE001
        pass
    _reset_state("browser closed")
    return (
        "Detached from your Chrome. Your Chrome itself is untouched "
        "and still running — I only let go of the controls."
    )


def _impl_status() -> str:
    if not _connected():
        return (
            "Not connected to any browser. Tap 'Connect my browser' "
            "to set it up in one tap — or use Settings → Browser "
            "control → Enable browser control, or browser_control "
            "action 'launch' to attach to your Chrome (opt-in only — "
            "Chrome must run with "
            f"--remote-debugging-port={_cdp_port()}; on Chrome 136 or "
            "later the port is ignored on your everyday profile, so "
            "Enable browser control launches Chrome on a separate "
            "debug profile instead)."
        )
    page = _active_page()
    n_tabs = len(_state["context"].pages)
    return (
        "Connected to your Chrome (CDP attach). "
        f"{n_tabs} tab(s) open. "
        f"Active tab: {_page_label(page)}."
    )


def _settle(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=_SETTLE_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        pass
