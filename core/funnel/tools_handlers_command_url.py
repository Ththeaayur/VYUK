"""Command + URL tool handlers (skills split B49, part 2 of 2).

Moved verbatim from core/funnel/tools_handlers.py: the command /
mouse / window / clipboard methods, the URL / music / settings /
time / volume / lock / memory methods, and the web-search trio,
plus the module constants their sole users resolve by bare name.
"""

# INTENTIONAL (skills split B49): imports exactly the names the moved code
# resolves by bare name (prover-verified; see prove_tools_handlers_p2.py).
import ctypes
import datetime
import re
import shutil
import subprocess
import sys
import webbrowser
from .. import injection
from ..memory.memory import (
    as_user_note,
    instruction_shaped,
    looks_like_credential,
    memory_store,
)
from .tools_gate import _send_key_event
from .tools_handlers_frame_launch import _browser_controller

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF


_RECALL_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for",
    "me", "my", "i", "you", "it", "is", "are", "can", "could",
    "please", "what", "whats", "that", "this", "with", "from",
    "your", "yours", "her", "she", "he", "we", "they", "them",
    "do", "does", "did", "will", "would", "should", "about",
    "into", "over", "under", "than", "then", "now", "just",
})


_BROWSER_LABELS = {"chrome": "Google Chrome",
                   "edge": "Microsoft Edge",
                   "firefox": "Firefox"}


def _open_url_attachable(raw_url: str) -> bool:
    """B2: True only when open_url may take the attached route.

    Both conditions must hold: the URL is a page — the ONE scheme
    guard (_goto_target) raises no refusal, so a file:// or
    custom-scheme URL keeps the webbrowser.open fallback below —
    AND the Fix 3 real-Chrome attach opt-in is on. The opt-in is
    READ here, never written: with it off this branch is
    unreachable and the fallback runs exactly as before. A consult
    failure fails closed (no attach attempt)."""
    from ..desktop.browser_cdp import _real_chrome_attach_allowed
    from ..desktop.browser_nav import _goto_target
    _, refusal = _goto_target(raw_url)
    if refusal is not None:
        return False
    try:
        return bool(_real_chrome_attach_allowed())
    except Exception:  # noqa: BLE001 — consult failure fails closed
        return False


def _classify_attached_open(result: str):
    """B2: turn a browser_control goto return into a seam Outcome.

    "Opened '<title>' — <url>." is verified by the page-label
    read-back (title + URL re-read after DOMContentLoaded);
    "Still loading ..." is unverified (navigation under way, load
    not confirmed); failures and refusals are failed, never a
    success sentence. The bootstrap-note prefix ("Attached to your
    Chrome to do this. ") is kept in the message and skipped for
    classification. Any unrecognised shape is unverified — never
    claimed verified."""
    from .. import tool_outcome as _outcome
    from ..desktop.browser_session import _BOOTSTRAP_NOTE
    text = str(result)
    body = (text[len(_BOOTSTRAP_NOTE) + 1:]
            if text.startswith(_BOOTSTRAP_NOTE + " ") else text)
    if body.startswith("Opened "):
        label = body[len("Opened "):]
        if label.endswith("."):
            label = label[:-1]
        return _outcome.verified(
            text, "page loaded in the attached session: " + label)
    if body.startswith("Still loading "):
        return _outcome.unverified(
            text, "navigation under way; the page had not finished "
            "loading within the goto budget")
    if body.startswith("Browser action failed:"):
        return _outcome.failed(
            text, "the attached browser reported the navigation failed")
    if body.startswith("Refused:"):
        return _outcome.failed(
            text, "the URL scheme is refused for the attached browser")
    if body.startswith("No controllable browser session"):
        return _outcome.failed(
            text, "the attach could not be established; nothing was "
            "opened")
    return _outcome.unverified(
        text, "the attached open returned an unrecognised shape; "
        "the page load was not confirmed")


class ToolHandlersCommandUrlMixin:
    def _tool_run_command(self, command: str = "", confirm: bool = False,
                          tail: bool = False, max_lines: int = 0,
                          working_directory: str = "",
                          background: bool = False, action: str = "",
                          job_id: str = "", timeout: int = 30,
                          cancel_token=None) -> str:
        """run_command: synchronous shell, managed background jobs
        (v5.45, H3) and a working_directory option (v5.31 §6 B1).

        B20: ``cancel_token`` is threaded to control.run_command
        for the synchronous run, so a Stop pressed while the
        command runs ends it at the next poll (the child tree
        is killed, the step's thread never touched). The job
        surface (background / poll / wait / cancel / list) is
        untouched — a background job is cancelled by its own
        action="cancel", never by this token.
        """
        """run_command: synchronous shell, managed background jobs
        (v5.45, H3) and a working_directory option (v5.31 §6 B1).

        The trust layer lives entirely in core/desktop/control.py; this
        wrapper adds the directory and the job surface:

        * working_directory — a VALIDATED directory is passed to
          the subprocess as its cwd (v5.45, H21c). Pre-v5.45 the
          wrapper applied it with a process-wide os.chdir for the
          call's duration, which raced every other thread's
          relative paths; nothing chdirs any more. A path that
          isn't an existing folder is refused before anything
          runs — the command never executes.
        * background=true — launch as a managed background job and
          return its job id at once. The trust decision happens in
          control.start_background_command, at launch, before the
          id exists.
        * action — the job surface on this same tool (no new tool;
          the registry count is pinned): "poll"/"status" (output
          since the last poll + state), "wait" (block up to
          `timeout` seconds for the final tail + exit code),
          "cancel" (kill the job's process tree), "list". Job
          actions ignore `command` and never re-run the trust
          layer."""
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        act = str(action or "").strip().lower()
        if act:
            if act in ("poll", "status"):
                return control.poll_background_job(job_id)
            if act == "wait":
                return control.wait_background_job(job_id, timeout=timeout)
            if act == "cancel":
                return control.cancel_background_job(job_id)
            if act == "list":
                return control.list_background_jobs()
            return (
                f"Unknown run_command action: '{action}'. Use "
                "'poll', 'wait', 'cancel' or 'list' with a job_id, "
                "or leave action off to run a command."
            )
        wd = str(working_directory or "").strip()
        cwd: str | None = None
        if wd:
            from pathlib import Path

            target = Path(wd).expanduser()
            if not target.is_dir():
                return (
                    f"Not a directory: '{working_directory}' — the "
                    "command was not run. Give an existing folder "
                    "path, or leave working_directory off to run in "
                    "the current folder."
                )
            cwd = str(target)
        if background:
            return control.start_background_command(
                command, confirm=confirm, cwd=cwd)
        return control.run_command(
            command, confirm=confirm, tail=tail,
            max_lines=max_lines, cwd=cwd, cancel_token=cancel_token)

    # ------------------------------------------------------------------
    # H5 post-action probes (v5.44) for the two desktop action tools
    # whose handlers live in core/desktop/control.py (mouse_control clicks,
    # window_control actions). The probe machinery itself is
    # core/desktop/uia.py's (desktop_snapshot / probe_suffix — shared with
    # uia_click_control and click_element, which probe in-handler);
    # these wrappers only take the BEFORE snapshot, delegate to the
    # untouched control.py handler, and append the change sentence
    # to a SUCCESS receipt. Refusals and failures keep their exact
    # pre-H5 wording, a probe that could observe nothing appends
    # nothing, and execute()'s 4000-char cap still applies to the
    # combined result. Direct calls to control.mouse_control /
    # control.window_control are unchanged.
    # ------------------------------------------------------------------

    def _safe_window_titles(self):
        """The set of open window titles via control._enum_windows,
        or None when they can't be enumerated here. Never raises."""
        try:
            from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            return {title for _hwnd, title in control._enum_windows()}
        except Exception:  # noqa: BLE001 — titles are best-effort
            return None

    def _tool_mouse_control(self, action: str, x: int = None,
                            y: int = None, amount: int = 3) -> str:
        """mouse_control + H5 probe for the click family only —
        move/scroll delegate untouched (their receipts are pinned
        byte-identical by the v5.8 suites). A coordinate click has
        no named target, so the probe is foreground/focus plus the
        screen_diff visual comparison (core/desktop/uia.py reuses
        core/skills_window_control.py's machinery)."""
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        act = (action or "").strip().lower()
        if act not in ("click", "double_click", "right_click"):
            return control.mouse_control(action, x, y, amount)
        from ..desktop import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        snap_before = _uia._safe_snapshot()
        img_before = _uia._screen_image()
        result = control.mouse_control(action, x, y, amount)
        if not result.startswith(
                ("Clicked (left button)", "Double-clicked (left button)",
                 "Right-clicked")):
            return result
        suffix = _uia.probe_suffix(
            snap_before, _uia._safe_snapshot(), img_before,
            _uia._screen_image(), "coords")
        # mouse_control's own v5.8 receipt already names a focus
        # move ("Focus moved to X.") — don't say the same change
        # twice when the probe's sentence is that same move.
        if "Focus moved to" in result and "came to front" in suffix:
            suffix = ""
        return result + suffix

    def _tool_window_control(self, action: str,
                             title_substring: str = "") -> str:
        """window_control + H5 probe for the acting actions —
        'list' (a read) delegates untouched. A verified close whose
        title vanishes from the window set is reported as closed;
        other actions report the foreground/focus/screen change,
        or an honest no-change (window actions aren't a retry-
        ladder rung, so no ladder is suggested for them)."""
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        act = (action or "").strip().lower()
        if act not in ("focus", "minimize", "maximize", "close"):
            return control.window_control(action, title_substring)
        from ..desktop import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        titles_before = self._safe_window_titles()
        snap_before = _uia._safe_snapshot()
        img_before = _uia._screen_image()
        result = control.window_control(action, title_substring)
        if not result.startswith(
                ("Focused window:", "Minimized window:",
                 "Maximized window:", "Closed window:",
                 "Sent close request to window:")):
            return result
        titles_after = self._safe_window_titles()
        if (act == "close" and result.startswith("Closed window:")
                and titles_before is not None
                and titles_after is not None):
            gone = sorted(titles_before - titles_after)
            if gone:
                return result + f" The {gone[0]} window closed."
        return result + _uia.probe_suffix(
            snap_before, _uia._safe_snapshot(), img_before,
            _uia._screen_image(), "window")

    def _tool_write_clipboard(self, text: str,
                              append: bool = False):
        """GAPS-23 (F-V7): delegates to the module-level write_clipboard_core() at the end of this file — the funnel-guarded entry point; the logic lives in the core function so white-box tests use the same code without calling a handler outside the funnel."""
        return write_clipboard_core(text=text, append=append)

    def _tool_open_url(self, url: str, browser: str = "") -> str:
        raw_url = url.strip()
        clean_url = raw_url
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url
        # v5.52 (item 5): every success return carries the
        # two-browser boundary reminder — this window is in the
        # system browser, which VYUK cannot see or drive
        # afterwards; page interaction belongs to browser_control.
        reminder = (
            "That window is in your system browser, which I "
            "can't see or drive afterwards — to search, click "
            "or type on a page, I use browser_control instead.")
        wanted = str(browser or "").strip().lower()
        if wanted and wanted != "default":
            controller = _browser_controller(wanted)
            if controller is not None:
                controller.open(clean_url)
                return (f"Opened {clean_url} in "
                        f"{_BROWSER_LABELS.get(wanted, wanted)}. "
                        f"{reminder}")
            if webbrowser.open(clean_url):
                # INTENTIONAL — round-3 fix Q1 (2026-11-19): the
                # sentence used to open "Couldn't find ...", which
                # the funnel now reads as a refusal — but the URL
                # DID open (webbrowser.open returned True), so the
                # honest lead is the outcome, with the missing
                # browser named after it. The information is
                # unchanged, only the order.
                return (f"Opened {clean_url} in your default browser "
                        f"instead — I couldn't find "
                        f"{_BROWSER_LABELS.get(wanted, wanted)} "
                        f"on this PC. {reminder}")
            return (f"I tried to open {clean_url} in your browser, "
                    "but the browser didn't open it.")
        # B2: the attached route. When the Fix 3 opt-in is on and
        # the URL is a page, open through browser_control's goto —
        # the same verified attach + page-load probe as
        # browser_control — so the session that opened the page is
        # the session that can drive it afterwards, and the outcome
        # is verified by the page label instead of claimed on a
        # launch return. A failed attach or a failed load reports
        # failure honestly: there is NO silent fallback to
        # webbrowser.open on this path. With the opt-in off, an
        # empty URL, or a non-page scheme, this branch is skipped
        # and the fallback below runs exactly as before.
        if raw_url and _open_url_attachable(raw_url):
            from ..browser_dispatch import browser_control as _goto
            return _classify_attached_open(_goto("goto", url=raw_url))
        if webbrowser.open(clean_url):
            return f"Opened {clean_url} in your browser. {reminder}"
        return (f"I tried to open {clean_url} in your browser, "
                "but the browser didn't open it.")

    def _tool_play_music(self, query: str = "") -> str:
        """GAPS-23 (F-V7): delegates to the module-level play_music_wrapped() at the end of this file — the funnel-guarded entry point; the logic lives in the core function so white-box tests use the same code without calling a handler outside the funnel."""
        return play_music_wrapped(query=query)

    def _tool_app_settings(self, action: str, setting: str = "",
                           value: str = "") -> str:
        """v5.52 (item 8): the app_settings tool's handler — all
        logic lives in core/app_settings.py (allowlist, stores,
        apply semantics, security exclusions).

        Settings redesign (2026-11-16, §5/§8): a LIVE-origin
        "set" proposes instead of applying (core/settings_voice
        — propose -> confirm -> announce + undo -> receipt),
        and the protocol actions (confirm/deny/undo/grant/
        revoke/revoke_all) run the voice protocol. Chat/plan
        "set" applies directly as before — typed, visible,
        user-initiated — except a permission name, which gets
        the directed Settings refusal instead of "unknown
        setting" (chat can't flip a switch either).
        """
        from .. import app_settings as _app_settings  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        from .. import settings_voice as _sv
        from .live_origin import is_live_origin as _live

        act = (action or "").strip().lower()
        if act == "confirm":
            return _sv.confirm_proposal(value)
        if act == "deny":
            return _sv.deny_proposal(value)
        if act == "undo":
            return _sv.undo_last_change()
        if act == "grant":
            return _sv.grant_session_by_voice(setting)
        if act == "revoke":
            return _sv.revoke_session_by_voice(setting)
        if act == "revoke_all":
            return _sv.revoke_all_session_grants()
        if act == "set" and _live():
            routed = _sv.route_voice_settings_request(setting, value)
            return routed["message"]
        if act == "set":
            pid = _sv.permission_named(setting) \
                or _sv.permission_named(value)
            if pid is not None:
                return (f"The {pid} switch lives in Settings → "
                        "Permissions — chat can't flip it, and "
                        "neither can voice. Voice can grant it "
                        "for this session only, while we're "
                        "talking; open Settings for the permanent "
                        "switch.")
        return _app_settings.app_settings(action, setting, value)

    def _tool_get_time_date(self) -> str:
        now = datetime.datetime.now()
        return now.strftime("Current date and time: %A, %B %d, %Y at %I:%M %p")

    def _tool_adjust_volume(self, action: str, steps: int = 3) -> str:
        # Volume steps go through the Windows media keys
        # (_send_key_event is ctypes.windll, unguarded), so on
        # any non-Windows system this used to raise instead of
        # answering. Siblings (lock_screen) answer with an
        # honest Windows-only sentence; do the same here.
        if not sys.platform.startswith("win"):
            return ("I can't change the volume from here — volume "
                    "steps use the Windows media keys, which "
                    "aren't available on this system.")
        steps = max(1, min(steps or 3, 10))
        if action == "mute":
            _send_key_event(VK_VOLUME_MUTE)
            msg = "Toggled master audio mute."
        elif action == "up":
            for _ in range(steps):
                _send_key_event(VK_VOLUME_UP)
            msg = f"Increased volume by {steps} steps."
        elif action == "down":
            for _ in range(steps):
                _send_key_event(VK_VOLUME_DOWN)
            msg = f"Decreased volume by {steps} steps."
        else:
            return f"Unknown volume action: {action}"
        # #34: read the level back, the way set_volume_level
        # does, whenever a read-back source exists (pycaw on
        # Windows) — "turn it up a bit" deserves a number.
        try:
            from ..desktop.control_plus_volume_network import (  # INTENTIONAL (Fix 7: read from the canonical defining module, not the B26 facade re-export, so the test can patch the seam the handler actually reads)
                _pycaw_volume)

            volume = _pycaw_volume()
            level = round(
                float(volume.GetMasterVolumeLevelScalar()) * 100)
            msg += f" The volume is now at {level}%"
            msg += " (muted)." if volume.GetMute() else "."
        except ImportError:
            if sys.platform.startswith("win"):
                msg += (" (The exact level couldn't be read "
                        "back on this PC.)")
        except Exception:  # noqa: BLE001 — read-back never sinks
            # the action it reports on (Fix 7): but a failed read-back
            # gets the same honest hedge as the missing-pycaw case,
            # so the "increased by N steps" line is never the whole
            # story when the level itself is unconfirmed.
            if sys.platform.startswith("win"):
                msg += (" (The exact level couldn't be read "
                        "back on this PC.)")
        return msg

    def _tool_lock_screen(self) -> str:
        """Lock the workstation now (merged with the retired lock_pc
        in v5.30: the Windows call's result is checked and reported
        honestly, with the Linux screensaver fallback)."""
        if sys.platform.startswith("win"):
            try:
                if ctypes.windll.user32.LockWorkStation():
                    return ("Lock requested — the PC is now at the "
                            "sign-in screen.")
                return ("I asked Windows to lock the PC, but the call "
                        "reported failure, so it may still be unlocked.")
            except Exception as e:  # noqa: BLE001
                return f"Could not lock the PC: {e}"
        if sys.platform.startswith("linux") and shutil.which(
                "xdg-screensaver"):
            try:
                proc = subprocess.run(
                    ["xdg-screensaver", "lock"], capture_output=True,
                    text=True, timeout=15)
                if proc.returncode == 0:
                    return ("Lock requested — the PC is now at the "
                            "sign-in screen.")
                detail = (proc.stderr or proc.stdout or "").strip()
                return ("I tried to lock this PC with xdg-screensaver, "
                        "but it failed"
                        + (f": {detail}" if detail else
                           f" (exit code {proc.returncode})."))
            except Exception as e:  # noqa: BLE001
                return f"Could not lock the PC: {e}"
        return ("I can't lock this PC from here — locking uses the "
                "Windows LockWorkStation call, and no lock command is "
                "available on this system.")

    # A memory fact is a fact, not a document (v4.6): anything
    # longer belongs in a file. The cap is enforced HERE, in the
    # tool, with an honest refusal — never a silent truncation.
    _FACT_VALUE_MAX = 500

    def _tool_remember_fact(self, key: str, value: str, category: str = "general") -> str:
        """Stores one fact, honestly (v4.6, audit_core + audit_mind F5).

        - Empty-after-strip key/value stores NOTHING (the old path
          happily filed an empty fact and claimed success).
        - Over-length values are refused with the limit stated.
        - Instruction-shaped values ("you should always …") are
          stored framed as a note about something the user said —
          memories are user data, never standing instructions.
        - Replacing an existing fact says so, with the old value.
        - Credential-looking values are stored (that's the user's
          call) but the result warns they sit in a plain-text DB.
        """
        key = (key or "").strip()
        value = (value or "").strip()
        category = (category or "general").strip() or "general"
        if not key or not value:
            return (
                "Nothing to save — a memory fact needs both a key "
                "and a value, and one of them was empty."
            )
        if len(value) > self._FACT_VALUE_MAX:
            return (
                f"That value is too long to store as a memory fact "
                f"({len(value)} characters; the limit is "
                f"{self._FACT_VALUE_MAX}). Shorten it, or save the "
                "full text as a file instead — nothing was stored."
            )
        notes: list[str] = []
        stored_value = value
        if instruction_shaped(value):
            stored_value = as_user_note(value)
            notes.append(
                "It reads as an instruction, so it was saved as a "
                "note about something you said — VYUK never treats "
                "memories as instructions to follow."
            )
        existing = memory_store.get_fact(category, key)
        saved = memory_store.save_fact(category=category, key=key,
                                       value=stored_value)
        if not saved:
            # RAISED, not returned: the funnel maps any handler string
            # to status success, so a returned failure sentence would
            # still report success. Raising surfaces it as a funnel
            # handler_error, the same honest shape a raising store
            # already produces.
            raise RuntimeError(
                "I tried to save that to memory, but the write did not "
                "land — nothing was stored. Please try again, and if it "
                "keeps failing, run the doctor check.")
        if existing is not None and str(existing.get("value", "")) != stored_value:
            notes.append(
                f"This replaces what was stored before "
                f"('{existing.get('value', '')}')."
            )
        if looks_like_credential(key, value):
            notes.append(
                "Heads-up: this looks like a credential — VYUK "
                "stores memories as plain text in a local database "
                "(data/assistant.db), so anyone who can read that "
                "file can read this too."
            )
        result = f"Saved to memory: '{key}' = '{stored_value}'"
        if notes:
            result += " (" + " ".join(notes) + ")"
        return result

    def _tool_recall_memories(self, query: str) -> str:
        """Searches stored facts with a LITERAL substring match.

        v4.5: the old path fed the query straight into SQL LIKE, so
        "%", "_" or an empty query acted as wildcards and dumped every
        stored fact into the conversation. Matching now happens in
        Python over get_all_facts() — % and _ are ordinary characters
        — a wildcard-only or empty query is refused as too broad, and
        every answer is bounded (first 25) and counted against the
        total number of stored facts."""
        q = (query or "").strip()
        facts = memory_store.get_all_facts()
        total = len(facts)
        if not q or not q.strip("%_").strip():
            return (
                "That search is too broad — it would match every memory "
                f"VYUK has. She has {total} fact{'s' if total != 1 else ''} "
                "stored; give me a word or topic to search for instead."
            )
        needle = q.lower()
        matches = [
            f for f in facts
            if needle in str(f.get("key", "")).lower()
            or needle in str(f.get("value", "")).lower()
            or needle in str(f.get("category", "")).lower()
        ]
        by_words = False
        if not matches:
            # Word-overlap recall (v5.29): a natural-language
            # question is never a substring of a stored fact, so the
            # phrase-only match reported "No memories found" for a
            # fact stored seconds earlier when the brain asked
            # "What is my favorite color?" (real-model proof,
            # 2026-10-22). Fall back to content words: a fact
            # matches when any content word of the query appears in
            # its key, value or category; rank by distinct words
            # hit, then most recently updated. The v4.5 safety shape
            # is unchanged — matching stays in Python (no LIKE
            # wildcards), the answer stays bounded and counted.
            tokens = {t for t in re.findall(r"[a-z0-9]+", needle)
                      if len(t) >= 3 and t not in _RECALL_STOPWORDS}
            scored: list[tuple[int, dict]] = []
            for f in facts:
                hay = (f"{f.get('key', '')} {f.get('value', '')} "
                       f"{f.get('category', '')}").lower()
                hits = sum(1 for t in tokens if t in hay)
                if hits:
                    scored.append((hits, f))
            # Stable two-pass sort: recency first, then hit count
            # descending on top of it (ties keep recency order).
            scored.sort(key=lambda p: str(p[1].get("updated_at", "")),
                        reverse=True)
            scored.sort(key=lambda p: -p[0])
            matches = [f for _, f in scored]
            by_words = bool(matches)
        if not matches:
            return f"No memories found matching '{query}'."
        shown = matches[:25]
        how = ("mentioning words from" if by_words else "matching")
        header = (
            f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} "
            f"{how} '{query}' (of {total} stored)"
            + (f" — showing the first {len(shown)}:" if len(matches) > len(shown) else ":")
        )
        results = [f"• [{m['category']}] {m['key']}: {m['value']}" for m in shown]
        return header + "\n" + "\n".join(results)

    def _tool_web_search(self, query: str, max_results: int = 5,
                         mode: str = "web") -> str:
        """Performs a live DuckDuckGo web search and returns a summary.

        v4.5 truthfulness pass: the result is built from what the API
        actually returned — the direct Answer first, then the
        Abstract, then the RelatedTopics texts (the data the old code
        discarded). A browser tab is opened only when the API
        genuinely has nothing (or the request itself failed), and
        the result always says plainly which of those happened —
        it never claims a browser was opened when it wasn't.

        mode="news" (v5.31): the DuckDuckGo instant-answer backend
        has no news vertical and no recency filter, so news mode
        queries the Google News RSS feed instead — real headlines
        WITH publication dates — and only falls back to this web
        path (saying so in the result) when the feed yields
        nothing."""
        import json
        import urllib.parse
        import urllib.request

        try:
            cap = max(1, min(int(max_results or 5), 10))
        except (TypeError, ValueError):
            cap = 5

        news_note = ""
        if str(mode or "web").strip().lower() == "news":
            news_result = self._news_search(query, cap)
            if news_result is not None:
                return news_result
            news_note = (
                f"The news search didn't return anything for "
                f"'{query}', so here are the regular web results "
                "instead. ")

        encoded = urllib.parse.quote_plus(query)
        try:
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(url, headers={"User-Agent": "NovaAI/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001
            if self._open_search_browser(encoded):
                return (
                    f"Web search failed: {e}. I opened your browser "
                    f"with the search '{query}' instead."
                )
            return f"Web search failed: {e}."

        def clipped(text: str, cap: int = 1200) -> str:
            text = " ".join(text.split())
            if len(text) > cap:
                return text[:cap].rstrip() + " […shortened]"
            return text

        # v5.3 (H2): search results are untrusted web content — they
        # get the injection envelope (and the screen) like any page.
        # §3 #13: the Answer/Abstract lead the reply but no longer
        # return early — the sourced results list below still follows
        # so every claim can be cited.
        answer = (data.get("Answer") or "").strip()
        abstract = (data.get("AbstractText") or "").strip()

        def _domain(url: str) -> str:
            try:
                return urllib.parse.urlparse(url).netloc or url
            except Exception:  # noqa: BLE001
                return url

        # Sourced results (#13): every entry carries its title,
        # source and snippet so an answer can be cited, not
        # just summarised.
        results: list[tuple[str, str, str]] = []
        abstract_url = (data.get("AbstractURL") or "").strip()
        if abstract:
            title = (data.get("Heading") or "").strip() or query
            # When an Answer already leads, the abstract rides as
            # this entry's snippet; otherwise the lead carries it.
            results.append((title, abstract_url,
                            abstract if answer else ""))

        topics: list[tuple[str, str]] = []

        def _collect(items) -> None:
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                text = (item.get("Text") or "").strip()
                if text:
                    topics.append(
                        (text, (item.get("FirstURL") or "").strip()))
                _collect(item.get("Topics"))

        _collect(data.get("RelatedTopics"))
        for text, url in topics:
            head = text.split(" - ", 1)[0].strip()
            title = head if 0 < len(head) <= 80 else _domain(url)
            results.append((title, url, text))
        if results or answer or abstract:
            lines = []
            if answer:
                lines.append(clipped(answer))
            elif abstract:
                lines.append(clipped(abstract))
            if results:
                lines.append("Results:")
                for i, (title, url, snippet) in enumerate(
                        results[:cap], start=1):
                    src = _domain(url) if url else ""
                    lines.append(
                        f"{i}. {title}" + (f" ({src})" if src else ""))
                    if snippet:
                        lines.append(f"   {clipped(snippet, 300)}")
                    if url:
                        lines.append(f"   {url}")
            return (news_note + f"[Search: {query}] "
                    + injection.wrap_untrusted(
                        "web search", "\n".join(lines)))

        # The API genuinely had nothing for this query.
        if self._open_search_browser(encoded):
            return (
                f"The search didn't return an instant answer for "
                f"'{query}', so I opened DuckDuckGo search for "
                f"'{query}' in your browser."
            )
        return (
            f"The search returned no instant answer for '{query}', "
            "and I couldn't open a browser either."
        )

    def _news_search(self, query: str, cap: int) -> str | None:
        """News mode for web_search: dated headlines from the
        Google News RSS feed. Returns the finished, enveloped
        result string, or None when the feed fails or carries no
        items — the caller then falls back to the web path and
        says so. Feed content is untrusted web content and gets
        the injection envelope like any other search result."""
        import re as _re
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime

        encoded = urllib.parse.quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded}"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "NovaAI/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                payload = resp.read()
            root = ET.fromstring(payload)
            items = root.findall("./channel/item")
        except Exception:  # noqa: BLE001 — any feed failure falls back
            return None
        if not items:
            return None

        def clipped(text: str, cap_chars: int = 300) -> str:
            text = " ".join(_re.sub(r"<[^>]+>", " ", text or "").split())
            if len(text) > cap_chars:
                return text[:cap_chars].rstrip() + " […shortened]"
            return text

        lines = ["Results:"]
        shown = 0
        for item in items:
            if shown >= cap:
                break
            title = clipped(item.findtext("title") or "", 200)
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            source_el = item.find("source")
            source = (source_el.text or "").strip() if source_el is not None else ""
            if source and title.endswith(f" - {source}"):
                title = title[: -len(f" - {source}")].strip()
            lines.append(f"{shown + 1}. {title}"
                         + (f" ({source})" if source else ""))
            when = ""
            raw_date = (item.findtext("pubDate") or "").strip()
            if raw_date:
                try:
                    when = parsedate_to_datetime(raw_date).strftime(
                        "%d %b %Y, %H:%M GMT")
                except (TypeError, ValueError):
                    when = raw_date
            snippet = clipped(item.findtext("description") or "")
            detail = " · ".join(p for p in (when, snippet) if p)
            if detail:
                lines.append(f"   {detail}")
            lines.append(f"   {link}")
            shown += 1
        if not shown:
            return None
        return (f"[News search: {query}] "
                + injection.wrap_untrusted(
                    "news search", "\n".join(lines)))

    @staticmethod
    def _open_search_browser(encoded_query: str) -> bool:
        """Opens the DuckDuckGo results page; True only when the
        browser really accepted the open (never claimed otherwise).

        PF-18 batch 6: a denied browser grant gates this fallback —
        when browser reads denied, no tab is opened and False is
        returned, so the caller reports the honest API-failure
        result (the same shape as when the open itself fails).
        Granted/unknown behave exactly as before: unknown is NOT
        a refusal here — the fallback is not a funnel tool call,
        so it never asks, and unknown allows it as today. A
        consult failure fails closed for the fallback only (skip
        opening); web_search itself stays unbound (API-first tool),
        this gate covers only its browser-tab fallback.
        """
        try:
            from .. import permission_grants as _pg
            _browser_denied = _pg.get_state("browser") == "denied"
        except Exception:  # noqa: BLE001 — fail closed, never open
            _browser_denied = True
        if _browser_denied:
            return False
        try:
            return bool(webbrowser.open(f"https://duckduckgo.com/?q={encoded_query}"))
        except Exception:  # noqa: BLE001
            return False


def play_music_wrapped(query: str = "") -> str:
    """v5.52 (item 4): the play_music handler — the ladder
    lives in core/music_player.py."""
    from .. import music_player  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    # R1b: marker wrap on the rung evidence — the rungs
    # already triangulate playback (2-of-3 votes) and say
    # "(audio/playback confirmed)" only when the world
    # confirms it, "couldn't confirm" otherwise. The wrap
    # lives here, not in play_music itself: the singlesource
    # tests call play_music directly and assertIn on its
    # plain-string return.
    out = music_player.play_music(query)
    if "(audio confirmed)" in out or "(playback confirmed)" in out:
        return _outcome.verified(
            out, "audio-session / in-page votes confirm playback")
    if "couldn't confirm" in out:
        return _outcome.unverified(
            out, "a source started but playback could not be confirmed")
    return out


def write_clipboard_core(text: str,
                              append: bool = False):
    """write_clipboard with an append option (v5.31 §6 B1).

    A plain write delegates to control.write_clipboard
    untouched, so its verified receipt stays byte-identical.
    append=True reads the current clipboard first — pyperclip,
    the same clipboard seam control and read_clipboard use —
    and writes old + newline + new through the same
    copy-then-read-back verification control performs; the
    receipt says it was an append and gives both counts. An
    empty or non-text clipboard makes append a plain write,
    and the result says there was nothing to append to.

    R1a (tools loopholes, seam pass 2): the result is an
    Outcome — the handler re-reads the clipboard itself
    (core.tool_outcome.clipboard_equals) instead of trusting
    control's receipt alone. That closes the hole where
    control's paste-back raised and the write was silently
    assumed to have stuck: a paste the handler cannot read
    back is now "unverified", never a success claim. Refusal
    and validation returns stay plain strings.
    """
    from .. import tool_outcome as _outcome  # R1a: seam wiring
    from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

    def _as_outcome(result, expected):
        """Map control.write_clipboard's receipt to an Outcome."""
        if result.startswith("Nothing to copy:"):
            return result  # validation — no act was attempted
        if (result.startswith("The copy did not stick:")
                or result.startswith(
                    "Could not write to the clipboard:")
                or "isn't available" in result):
            return _outcome.failed(result, result)
        ok, evidence = _outcome.clipboard_equals(expected)
        if ok:
            return _outcome.verified(result, evidence)
        if evidence.startswith("the clipboard holds"):
            return _outcome.failed(
                "The copy did not stick: after writing, "
                + evidence + " — another app may have changed "
                "it, so the text was NOT copied.", evidence)
        return _outcome.unverified(
            result + " (I couldn't read the clipboard back to "
            "confirm it stayed.)", evidence)

    if not append:
        return _as_outcome(control.write_clipboard(text),
                           "" if text is None else str(text))
    if text is None:
        return _as_outcome(control.write_clipboard(text), "")
    text = str(text)
    try:
        import pyperclip

        current = pyperclip.paste()
    except ImportError:
        return ("Clipboard writing isn't available in this build of "
                "VYUK — the clipboard part didn't make it into the package.")
    except Exception as e:  # noqa: BLE001
        return (f"Could not read the current clipboard to "
                f"append to it: {e}")
    if not isinstance(current, str) or not current:
        result = control.write_clipboard(text)
        note = ("The clipboard was empty or held something "
                "that isn't text, so there was nothing to "
                f"append to. {result}")
        if result.startswith("Nothing to copy:"):
            return note
        if (result.startswith("The copy did not stick:")
                or result.startswith(
                    "Could not write to the clipboard:")
                or "isn't available" in result):
            return _outcome.failed(note, result)
        ok, evidence = _outcome.clipboard_equals(text)
        if ok:
            return _outcome.verified(note, evidence)
        if evidence.startswith("the clipboard holds"):
            return _outcome.failed(
                "The copy did not stick: after writing, "
                + evidence + " — another app may have changed "
                "it, so the text was NOT copied.", evidence)
        return _outcome.unverified(
            note + " (I couldn't read the clipboard back to "
            "confirm it stayed.)", evidence)
    combined = current + "\n" + text
    try:
        pyperclip.copy(combined)
    except Exception as e:  # noqa: BLE001
        return f"Could not write to the clipboard: {e}"
    collapsed = " ".join(combined.split())
    preview = collapsed[:60]
    suffix = ""
    if preview:
        tail = "…" if len(collapsed) > 60 else ""
        suffix = f": '{preview}{tail}'"
    appended = (f"Appended {len(text)} character(s) to the "
                f"clipboard — it now holds {len(combined)} "
                f"character(s){suffix}.")
    # R1a: paste-back verification that never assumes success.
    # (The old code set back = combined on a paste failure,
    # which read as a confirmed append.)
    ok, evidence = _outcome.clipboard_equals(combined)
    if ok:
        return _outcome.verified(appended, evidence)
    if evidence.startswith("the clipboard holds"):
        return _outcome.failed(
            "The append did not stick: after writing, "
            + evidence + " — another app may have changed it, "
            "so the text was NOT appended.", evidence)
    return _outcome.unverified(
        appended + " (I couldn't read the clipboard back to "
        "confirm it stayed.)", evidence)
