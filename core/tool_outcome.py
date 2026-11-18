"""Outcome verification seam (Fix 7 — tools loopholes, 2026-11-05).

THE LOOPHOLE (proved by audit, TOOLS_LOOPHOLES_AUDIT.md): the funnel's
dispatch marks ANY non-raising handler return as ``"success"``
(``core/funnel/tools_registry_exec_dispatch.py``: the final
``ctx._audited({"status": "success", "result": result})``). A handler
that says "Done" when nothing happened still reads success — the
model narrates a success that never landed, and the Activity log
records it. Some handlers already verify internally (launch
verification, Focus Assist read-back, volume read-back, lock-screen
return check, uninstall "launched" honesty), but each did it ad hoc,
in its own wording, with no machine-readable record of whether the
world actually changed.

THE ONE DESIGN — "Verified, or honestly unverified": one shared type
plus one dispatch recognition, not per-tool ad-hoc checks.

* Handlers that act on the world return an :class:`Outcome` instead
  of a bare string: ``verified`` (the world was re-read and confirms
  it), ``unverified`` (the act was attempted but the world could not
  be re-read — the message says so in plain words), or ``failed``
  (the world was re-read and contradicts the claim, or the probe
  itself failed — the message says so, never a success sentence).
* The funnel unwraps an Outcome to its plain message string before
  any other handling, so the brain-visible text, the A5
  refusal-sentence rules, and the dropped-args notes all work exactly
  as today. The verification itself is carried in the audited dict
  (``outcome_verified`` / ``outcome_evidence``) and the Activity
  line (``success (verified: …)`` / ``success (unverified: …)``).
  Handlers that still return plain strings behave exactly as before
  — no ``outcome_verified`` key appears for them, and no behaviour
  changes for the ~650 tools not yet wired through the seam.
* A failed probe NEVER reads as verified: every helper below returns
  ``(False, reason)`` when it cannot tell, never raises, and never
  guesses. An unverifiable world passes through labelled, never as
  a fake success — the same posture as the live-staleness stage.
* ``Outcome`` compares equal to its message string and formats as
  it, so the few direct (non-funnel) callers of wired functions
  keep working; call sites that do string operations unwrap with
  ``str(...)`` explicitly (see ``core/skills_senses_checks.py``).

HELPERS (world read-backs; each never raises):
* :func:`file_written` — re-read a file just written (size, and
  optionally byte equality). Wires write_file, create_art,
  generate_melody, generate_beat.
* :func:`print_job_queued` — win32print EnumJobs probe for a job
  whose document matches the file just sent to the print verb.
  Wires print_file. Non-Windows, or no win32print, reads as
  "no read-back available", never as a queued job.
* :func:`clipboard_equals` (R1a) — paste-back read of a text
  clipboard write. Wires write_clipboard (whose in-handler
  paste-back silently assumed success when the paste itself
  failed — the hole this closes).
* :func:`clipboard_files_equal` (R1a) — read back the file list
  on the clipboard (Windows CF_HDROP, else xclip text/uri-list).
  Wires copy_file_to_clipboard.
* :func:`file_copied` / :func:`hash_file` /
  :func:`file_matches_hash` / :func:`file_absent` (R1a) — chunked
  byte comparison for copies, hash capture before a move (the
  source is gone afterwards), and absence after a delete/shred.
  Wire file_control copy/move/rename/delete, sync_folder,
  shred_file.
* :func:`process_running` (R1a) — psutil first, tasklist / /proc
  fallback; name and/or pid, with an optional created-after
  bound. Backs launch_application's process leg.
* :func:`registry_dword_equals` (R1a) — winreg read-back of one
  REG_DWORD. Backs set_focus_assist and the display_control
  theme legs (both previously did the read inline).
* :func:`wifi_adapter_state` / :func:`wifi_connected_ssid` /
  :func:`wifi_profile_exists` (R1b) — netsh read-backs for the
  Wi-Fi legs (adapter enabled/disabled, associated SSID, saved
  profile listed). Back network_control and forget_wifi.
* :func:`drive_present` (R1b) — the drive letter still exists
  (or not) after an eject request. Backs eject_drive.
* :func:`wallpaper_current` (R1b) — HKCU Desktop Wallpaper
  REG_SZ read-back. Backs wallpaper_apply.
* :func:`window_topmost` / :func:`window_opacity_value` (R1b) —
  GetWindowLong / GetLayeredWindowAttributes read-backs. Back
  window_always_on_top and window_opacity.

NEXT-PASS HELPERS (R1b — 2026-11-06): the sent-not-confirmed
class is closed. Tools with a world read-back are wired through
the seam (network_control wifi legs, eject_drive, forget_wifi,
window_always_on_top, window_opacity, wallpaper_apply, browser_type
fill, play_music marker, create_shortcut / download_email_attachment
/ vcard_export / env_backup file re-reads, move_cursor position
read-back). Tools with NO read-back at all are marked honestly
unverified instead (media keys, type_text / type_clipboard,
press_hotkey, paste legs, open_path, notify_user, speaker_check,
wake_on_lan, click/scroll/drag legs, browser_press, menu/adapter/
surface/context-menu actions) — the honest mark IS the fix for
those; no helper can be built on a read-back that does not exist.
The audit (TOOLS_LOOPHOLES_AUDIT.md §2c) closes the pool to 0:
every one of the 309 effectful tools now has a disposition.

REJECTED ALTERNATIVES (do not re-open):
* A funnel-side generic verifier that re-reads the world for every
  tool — REJECTED: "done" means something different per tool, and a
  generic re-read cannot know it; it would be theatre (the C4
  lesson: calling a verifier with no observation supplier proves
  nothing). Verification lives in the handler, which knows what
  "done" means; the funnel only carries the verdict.
* Rewiring all 655 tools in one batch — REJECTED as unsafe: most
  tools are reads or pure computations, where the result IS the
  evidence; the audit wires the highest-value effectful families
  and lists the rest as the next pass with counts.
* Changing the success status for unverified outcomes — REJECTED:
  the brain contract is sentence-based ("Could not …" is read as
  failure by every brain path); changing statuses would break
  routerfinish, firstrun, and the live FunctionResponse mapping.
  The message carries the honesty; the keys carry the record.
* Verifying by re-running the action — REJECTED: a second act is a
  second side effect (a second print, a second key press). All
  helpers here are READ-BACKS, never re-acts.
* "Verifying" a sent key / media key / paste by assuming the app
  heard it, or by reading the app's own state as proof the user
  saw it — REJECTED (R1b): an assumption is not an observation,
  and a toast API's "shown" is not a user seeing it. Where no
  OS read-back exists the tool is marked honestly unverified
  instead of inventing one — the honest mark is the fix.
* Verifying a click by re-reading the cursor position — REJECTED
  (R1b): the cursor being at the point proves the pointer moved,
  not that the click landed on anything; only an element/landing
  observation (the UIA expectation path, the browser page probe)
  counts as click verification.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

__all__ = [
    "Outcome",
    "verified",
    "unverified",
    "failed",
    "is_outcome",
    "unwrap_outcome",
    "file_written",
    "print_job_queued",
    "clipboard_equals",
    "clipboard_files_equal",
    "file_copied",
    "hash_file",
    "file_matches_hash",
    "file_absent",
    "process_running",
    "registry_dword_equals",
    "wifi_adapter_state",
    "wifi_connected_ssid",
    "wifi_profile_exists",
    "drive_present",
    "wallpaper_current",
    "window_topmost",
    "window_opacity_value",
]


class Outcome:
    """One tool outcome with its verification verdict attached.

    ``verified`` is True only when the world was re-read and confirms
    the claim. ``message`` is the plain-words sentence the brain
    speaks from — it must carry the honesty itself (an unverified
    outcome's message says it could not be confirmed; a failed
    outcome's message says what went wrong), because brains do not
    read the audited keys. ``evidence`` is the machine-readable
    record: what confirmed it, or why it could not be confirmed.
    """

    __slots__ = ("verified", "message", "evidence", "kind")

    def __init__(self, verified: bool, message: str,
                 evidence: str | None = None,
                 kind: str | None = None) -> None:
        self.verified = bool(verified)
        self.message = str(message)
        self.evidence = evidence
        self.kind = kind or ("verified" if verified else "unverified")

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ("Outcome(verified=%r, kind=%r, message=%r, "
                "evidence=%r)" % (self.verified, self.kind,
                                  self.message, self.evidence))

    def __format__(self, spec: str) -> str:
        return format(self.message, spec)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Outcome):
            return (self.verified == other.verified
                    and self.message == other.message
                    and self.evidence == other.evidence)
        if isinstance(other, str):
            return self.message == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.verified, self.message, self.evidence))

    def __bool__(self) -> bool:
        return True

    def __contains__(self, item: object) -> bool:
        # An Outcome reads like its message everywhere else
        # (str, format, == str), so membership reads the message
        # too: `"x" in outcome` means the spoken sentence
        # contains x, exactly as for a plain str return.
        return item in self.message


def verified(message: str, evidence: str) -> Outcome:
    """The world was re-read and confirms the claim."""
    return Outcome(True, message, evidence, "verified")


def unverified(message: str, reason: str) -> Outcome:
    """The act was attempted but the world could not be re-read.

    The message must say so in plain words — the brain speaks from
    the message, not from the keys.
    """
    return Outcome(False, message, reason, "unverified")


def failed(message: str, cause: str) -> Outcome:
    """The world was re-read and contradicts the claim (or the probe
    itself failed). The message says what went wrong; it is never a
    success sentence."""
    return Outcome(False, message, cause, "failed")


def outcome_kind(value: object) -> str | None:
    """The Outcome kind of a handler return, or None for plain values.

    The dispatch uses this to carry the kind (not just the boolean)
    into the audited dict, so a reader downstream can tell a
    "failed" landing from an "unverified" one — the boolean alone
    collapses both to False (B1's proof). Plain strings get None
    and no keys at all, exactly as before.
    """
    if isinstance(value, Outcome):
        return value.kind
    return None


# ---------------------------------------------------------------------------
# Hands landing check (B13, L10 lab half)
# ---------------------------------------------------------------------------
# LOPHOLE FAMILY — "success by send": a hands step recorded as
# success because the keystrokes were sent, the click dispatched,
# the paste key pressed — not because anything landed. The 16
# hands tools a desktop task is made of returned `unverified`
# and the coordinator recorded them as success because it never
# read outcome_verified (B1's proof). A complex task could
# "complete" while the clicks never landed and the text never
# arrived, and nothing in the loop could tell.
#
# ONE DESIGN — one helper, one channel: after the act, the B5
# vision backend reads the screen and the landing verdict rides
# the seam Outcome (verified / unverified-with-reason /
# landing_failed), the same Outcome shape Fix 7/R1a/R1b
# established. The dispatch carries the kind through
# (outcome_kind) so the coordinator can tell a failed landing
# from an unreadable screen, and _run_step turns a
# landing_failed into a StepOutcome failure the B4 jump / halt
# acts on. Where vision cannot read, the tool stays honestly
# unverified — the check never fakes a landing, never upgrades
# an honest unknown to a pass.
#
# The check is driven by the DECLARED expectation: for type/paste
# the expectation is the text itself (Granite read_page must show
# it on screen); for click/drag_file it is the named target (the
# Qwen3-VL describe leg answers one strict yes/no question about
# it). The helper never invents what "landed" means in general —
# that is the R-C contract, the owner's half (audit D2, still
# OWED), and this batch does not close it. What the helper does
# is execute a check against whatever expectation the caller
# passes; a caller that cannot name one must not call it.
#
# REJECTED ALTERNATIVES (do not re-open):
# - Trusting the OS input event: the event fires whether or not
#   the app received it — the exact hole this closes.
# - A second verification phase for hands tools: the seam
#   Outcome already carries verdicts — one channel, not two.
# - Marking all 16 hands tools verified once vision exists:
#   vision reads the screen; it does not guarantee the app's
#   state. Where it cannot read, honest-unverified stands.
# - Judging free-text replies loosely: only a strict leading
#   yes/no counts; anything else is inconclusive, never a guess.
# - A vision leg in the shared UIA expect engine: it would
#   change the coordinator's step verification and the B10 goal
#   check globally. The seam Outcome is the scoped channel.
# - A Granite-missing fallback to describe for type/paste: the
#   structured read is the crisp leg for text presence; a
#   describe yes/no on exact text is a weaker judgment. If
#   Granite cannot read, the honest answer is unverified.
#   (Named follow-up, not built here.)
#
# READINESS FIRST: the production path checks the vision leg's
# readiness (weights on disk) BEFORE capturing — in the lab, and
# on any machine without weights, the check costs nothing and
# the act's own receipt stands byte-identical. Injected legs
# (tests) speak for themselves and skip the readiness check.
# The check never raises: any failure degrades to unverified,
# never breaks the act that already happened.
_HANDS_LANDING_KINDS = frozenset({"click", "type", "paste",
                                  "drag_file"})
_LANDING_FAILED_KIND = "landing_failed"


def _landing_capture_bytes(shot) -> bytes | None:
    """A capture result normalised to image bytes, or None.

    Accepts bare bytes or a (bytes, mime) tuple (the shape
    core.desktop.control_system._capture_screen_bytes returns).
    """
    if isinstance(shot, bytes):
        return shot or None
    if isinstance(shot, tuple) and shot and isinstance(shot[0], bytes):
        return shot[0] or None
    return None


def _landing_default_capture():
    """The production screen capture, resolved lazily (never at
    import time): core.desktop.control_system._capture_screen_bytes
    via vision_manager. Raises on a headless system — the caller
    turns that into unverified, never a break."""
    from .desktop.control_system import _capture_screen_bytes

    return _capture_screen_bytes()


def _landing_normalised_contains(text: str, target: str) -> bool:
    """True when the target text appears in the vision read.

    Normalised, not exact: casefolded, whitespace collapsed —
    a model transcription that differs only in case or spacing
    still counts. The target itself is never shortened: a
    partial match is not a landing.
    """
    norm = " ".join(str(text or "").split()).casefold()
    want = " ".join(str(target or "").split()).casefold()
    return bool(want) and want in norm


def _landing_leading_yes_no(text: str):
    """A strict leading yes/no from a vision reply, or None.

    Only the FIRST word counts, stripped of quotes and trailing
    punctuation: "yes" / "no" (case-insensitive). Anything else
    — "maybe", "the screen shows…", an empty reply — is
    inconclusive, never a guess.
    """
    first = (str(text or "").strip().strip("\"'").split() or [""])[0]
    first = first.strip(".,;:!?").casefold()
    if first == "yes":
        return "yes"
    if first == "no":
        return "no"
    return None


def _landing_question(kind: str, target: str) -> str:
    """The one yes/no question the describe leg is asked."""
    if kind == "drag_file":
        return (f"Is {target}? Answer yes or no first, then one "
                "short sentence.")
    return (f"Did clicking '{target}' open, activate, or select it? "
            "Answer yes or no first, then one short sentence.")


def hands_landing_check(kind: str, target: str, base_message: str, *,
                        capture=None, describe=None,
                        read_page=None) -> Outcome:
    """A vision landing check for one hands act. Returns an Outcome.

    ``kind`` is "click" | "type" | "paste" | "drag_file" and
    ``target`` is the declared expectation the landing is judged
    against: the clicked text/name, the typed/pasted text, or for
    a file drop the sentence "the file '<name>' open in the
    '<window>' window". ``base_message`` is the act's own receipt
    sentence — it is preserved verbatim (pins assert on it), with
    a short landing sentence appended for the verified/failed
    verdicts. The unverified return keeps the base message
    exactly as the handler wrote it (it already says the landing
    was not confirmed); the reason rides in the evidence.

    ``capture`` / ``describe`` / ``read_page`` are injectable
    seams (tests): capture() -> bytes | (bytes, mime) | None,
    describe(image_bytes, prompt) -> the vision_backend.describe
    dict, read_page(image_bytes) -> the vision_backend.read_page
    dict. None means the production legs, resolved lazily.

    Readiness first: the production path checks the vision leg's
    readiness (weights on disk) BEFORE capturing — no weights, no
    capture, no cost, and the honestly-unverified return stands.
    Injected legs (tests) skip the readiness check.

    Never raises: every failure of the check itself — no backend,
    no weights, capture failure, unreadable screen, inconclusive
    model answer — degrades to unverified with the reason in the
    evidence. Only a read that succeeds AND shows the target
    absent is a landing failure.

    The check is driven by the DECLARED expectation (the clicked
    text/name, the typed/pasted text, or the file+window for a
    drop); it never invents what "landed" means in general — that
    is the R-C contract (audit D2), still owed, NOT closed by
    this batch.

    Rejected alternatives (recorded here, per the standing
    instruction): trusting the OS input event (the event fires
    whether or not the app received it — the exact hole); a second
    verification phase for hands tools (the seam Outcome already
    carries verdicts — one channel, not two); marking all 16 tools
    verified by default once vision exists (vision reads the
    screen, it does not guarantee the app's state — where it
    cannot read, honest-unverified stands); judging free-text
    replies loosely (only a strict leading yes/no counts); a
    vision leg in the shared UIA expect engine (that would change
    step verification + goal check globally); a Granite-missing
    fallback to describe for type/paste (named follow-up, not
    built).
    """
    target = str(target or "").strip()
    base = str(base_message or "")
    if kind not in _HANDS_LANDING_KINDS:
        return unverified(
            base, "no landing check exists for this action kind")
    if not target:
        return unverified(
            base, "no landing target was given to check against")
    injected = describe is not None or read_page is not None
    backend = None
    if not injected:
        # Production path: resolve the backend and check the leg's
        # readiness BEFORE capturing — no weights, no capture.
        try:
            from . import vision_backend as backend
        except Exception as exc:  # noqa: BLE001 — no backend, no check
            return unverified(
                base, f"vision backend unavailable ({exc})")
        leg = ("granite-docling-258m" if kind in ("type", "paste")
               else "qwen3-vl-2b")
        try:
            status = backend.vision_status()
        except Exception as exc:  # noqa: BLE001 — status is a record
            return unverified(
                base, f"vision status could not be read ({exc})")
        leg_status = (status or {}).get(leg) or {}
        if not leg_status.get("ready"):
            missing = "; ".join(leg_status.get("missing")
                                or ["weights not on disk"])
            return unverified(
                base, f"{leg} not ready — {missing}")
        describe_fn = backend.describe
        read_fn = backend.read_page
    else:
        describe_fn = describe
        read_fn = read_page
    try:
        shot = capture() if capture is not None \
            else _landing_default_capture()
    except Exception as exc:  # noqa: BLE001 — capture is a record
        return unverified(
            base, f"screen could not be captured ({exc})")
    image = _landing_capture_bytes(shot)
    if not image:
        return unverified(base, "screen capture returned no image")
    if kind in ("type", "paste"):
        if read_fn is None:
            return unverified(
                base, "no text-reading leg was supplied")
        try:
            result = read_fn(image)
        except Exception as exc:  # noqa: BLE001 — read is a record
            return unverified(base, f"vision read failed ({exc})")
        if not isinstance(result, dict) or not result.get("ok"):
            reason = (result or {}).get("reason") \
                or "vision read failed"
            detail = (result or {}).get("detail") or ""
            return unverified(
                base, "vision could not read the screen "
                f"({reason}{': ' + detail if detail else ''})")
        if _landing_normalised_contains(result.get("text") or "",
                                        target):
            return verified(
                base + " Vision check: the text is on screen.",
                f"vision read confirms {target!r} present")
        verb = "typing" if kind == "type" else "paste"
        return Outcome(
            False,
            base + " Vision check: the text is not on screen — "
            f"the {verb} did not land (looked for {target!r}).",
            "landing: text absent from vision read",
            _LANDING_FAILED_KIND)
    if describe_fn is None:
        return unverified(
            base, "no screen-description leg was supplied")
    try:
        result = describe_fn(image, _landing_question(kind, target))
    except Exception as exc:  # noqa: BLE001 — describe is a record
        return unverified(base, f"vision describe failed ({exc})")
    if not isinstance(result, dict) or not result.get("ok"):
        reason = (result or {}).get("reason") or "vision read failed"
        detail = (result or {}).get("detail") or ""
        return unverified(
            base, "vision could not read the screen "
            f"({reason}{': ' + detail if detail else ''})")
    verdict = _landing_leading_yes_no(result.get("text") or "")
    if verdict == "yes":
        return verified(
            base + " Vision check: the landing is confirmed "
            "on screen.",
            f"vision confirms {target!r}")
    if verdict == "no":
        return Outcome(
            False,
            base + " Vision check: the screen shows it did not "
            f"land (looked for {target!r}).",
            "landing: vision says not landed",
            _LANDING_FAILED_KIND)
    return unverified(
        base, "vision read was inconclusive — no clear yes or no")


def is_outcome(value: object) -> bool:
    """True when a handler returned an Outcome (not a plain string)."""
    return isinstance(value, Outcome)


def unwrap_outcome(value: object) -> tuple:
    """Split a handler return into (message, verified, evidence).

    Plain strings (and every other value) pass through with
    ``verified=None`` — the funnel treats them exactly as before.
    The dispatch calls this once, immediately after the handler
    returns, before any other handling.
    """
    if isinstance(value, Outcome):
        return value.message, value.verified, value.evidence
    return value, None, None


def file_written(path, expected_bytes: bytes | None = None):
    """Re-read a file just written.

    Returns ``(True, evidence)`` only when the file exists on disk
    and (when ``expected_bytes`` is given) its bytes match exactly.
    Returns ``(False, reason)`` when the file is missing, the bytes
    differ, or the re-read itself fails. Never raises.
    """
    try:
        target = Path(str(path)).expanduser()
        if not target.is_file():
            return False, "no file at %s after the write" % target
        data = target.read_bytes()
        if expected_bytes is not None and data != expected_bytes:
            return (False,
                    "%s holds %d bytes, not the %d written"
                    % (target, len(data), len(expected_bytes)))
        return True, "%d bytes on disk at %s" % (len(data), target)
    except Exception as exc:  # noqa: BLE001 — a broken probe reads
        return False, "could not re-read %s: %s" % (path, exc)


def print_job_queued(doc_name: str, printer: str | None = None,
                     wait_s: float = 2.0, poll_s: float = 0.25):
    """Probe the Windows print queue for a job for ``doc_name``.

    Returns ``(True, evidence)`` when a job whose document name
    contains the file's name is in the queue, ``(False, reason)``
    when no such job appears within ``wait_s``, and ``(False,
    reason)`` when there is no read-back available at all
    (non-Windows, or win32print missing). Never raises, never
    guesses: "no read-back" and "no job" are different reasons.
    """
    if not doc_name:
        return False, "no document name to look for in the queue"
    try:
        import win32print  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — no read-back on this system
        return (False,
                "no print-job read-back available on this system "
                "(win32print is not installed)")
    try:
        import sys as _sys
        if not _sys.platform.startswith("win"):
            return (False,
                    "no print-job read-back available on this system "
                    "(not Windows)")
        if printer is None:
            printer = win32print.GetDefaultPrinter()
        stem = Path(str(doc_name)).stem.lower()
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            try:
                jobs = win32print.EnumJobs(
                    win32print.OpenPrinter(printer), 0, 100, 1)
            except Exception as exc:  # noqa: BLE001 — queue unreadable
                return False, "could not read the print queue: %s" % exc
            for job in jobs or []:
                document = str((job or {}).get("Document") or "")
                if stem and stem in document.lower():
                    job_id = (job or {}).get("JobId")
                    return (True,
                            "print job %s for %r queued on %s"
                            % (job_id, document, printer))
            if time.monotonic() >= deadline:
                return (False,
                        "no job for %r appeared in the queue on %s "
                        "within %.0f s" % (doc_name, printer, wait_s))
            time.sleep(max(0.05, poll_s))
    except Exception as exc:  # noqa: BLE001 — a broken probe reads
        return False, "could not probe the print queue: %s" % exc


# ---------------------------------------------------------------------------
# R1a helpers (2026-11-06) — clipboard / file-copy / process / registry
# ---------------------------------------------------------------------------

_COPY_CHUNK = 65536


def clipboard_equals(expected: str):
    """Paste-back read of a text clipboard write.

    Returns ``(True, evidence)`` only when the clipboard holds
    exactly ``expected``. Returns ``(False, reason)`` when the
    clipboard holds different text, holds something that isn't
    text, or cannot be read back at all — including when the
    paste itself raises. The old in-handler check in
    ``control.write_clipboard`` silently treated a failed paste as
    success (``current = text`` on exception); this helper never
    does. Never raises.
    """
    if expected is None:
        expected = ""
    expected = str(expected)
    try:
        import pyperclip
    except Exception:  # noqa: BLE001 — no read-back on this system
        return (False,
                "no clipboard read-back available (pyperclip is not "
                "installed)")
    try:
        current = pyperclip.paste()
    except Exception as exc:  # noqa: BLE001 — a failed paste is no proof
        return False, "could not read the clipboard back: %s" % exc
    if not isinstance(current, str):
        return (False,
                "the clipboard holds something that isn't text, not "
                "the %d characters written" % len(expected))
    if current != expected:
        return (False,
                "the clipboard holds %d character(s), not the %d "
                "written" % (len(current), len(expected)))
    return (True,
            "%d characters on the clipboard match what was written"
            % len(expected))


def _clipboard_files_windows():
    """Read the file list off a Windows clipboard (CF_HDROP).

    Returns the list of paths, ``[]`` when the clipboard holds no
    files, or ``None`` when there is no read-back available (not
    Windows, or the clipboard cannot be opened). Never raises.
    """
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        user32 = windll.user32
        shell32 = windll.shell32
    except Exception:  # noqa: BLE001 — no read-back on this system
        return None
    _CF_HDROP = 15
    try:
        if not user32.OpenClipboard(None):
            return None
        try:
            if not user32.IsClipboardFormatAvailable(_CF_HDROP):
                return []
            handle = user32.GetClipboardData(_CF_HDROP)
            if not handle:
                return []
            count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
            paths = []
            for index in range(count):
                buf = ctypes.create_unicode_buffer(32768)
                got = shell32.DragQueryFileW(handle, index, buf, 32768)
                if got:
                    paths.append(buf.value)
            return paths
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001 — a broken probe reads no files
        return None


def _clipboard_files_xclip():
    """Read the file list off an X11 clipboard (xclip, uri-list).

    Returns the list of paths, ``[]`` when the clipboard holds no
    file URIs, or ``None`` when xclip is unavailable or the read
    fails. Never raises.
    """
    try:
        import shutil

        if not shutil.which("xclip"):
            return None
        completed = subprocess.run(
            ["xclip", "-o", "-selection", "clipboard",
             "-t", "text/uri-list"],
            capture_output=True, text=True, timeout=10)
        if completed.returncode != 0:
            return None
        from urllib.parse import unquote, urlparse

        paths = []
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = urlparse(line)
            if parsed.scheme and parsed.scheme != "file":
                continue
            raw = unquote(parsed.path or line)
            if raw:
                paths.append(raw)
        return paths
    except Exception:  # noqa: BLE001 — a broken probe reads no files
        return None


def clipboard_files_equal(expected_path):
    """Read back the file list on the clipboard and compare it.

    Returns ``(True, evidence)`` only when the clipboard holds
    exactly the one expected file. Returns ``(False, reason)``
    when it holds different files, no files, or cannot be read
    back at all. Never raises, never re-acts.
    """
    if expected_path is None or str(expected_path) == "":
        return False, "no file path to look for on the clipboard"
    try:
        wanted = str(Path(str(expected_path)).expanduser().resolve())
    except Exception as exc:  # noqa: BLE001 — unresolvable path
        return False, "could not resolve %s: %s" % (expected_path, exc)
    import sys as _sys

    if _sys.platform.startswith("win"):
        found = _clipboard_files_windows()
    else:
        found = _clipboard_files_xclip()
    if found is None:
        return (False,
                "no file-list read-back available on this system")
    try:
        have = [str(Path(p).expanduser().resolve()) for p in found]
    except Exception as exc:  # noqa: BLE001 — unreadable entry
        return False, "could not read the clipboard file list: %s" % exc
    if have == [wanted]:
        return True, "%s is on the clipboard as a file" % wanted
    if not have:
        return False, "the clipboard holds no files"
    return (False,
            "the clipboard holds %s, not %s"
            % (", ".join(have[:3]), wanted))


def file_copied(src, dst):
    """Byte-compare a copied file against its source.

    Returns ``(True, evidence)`` only when the destination exists
    and its bytes match the source's exactly (chunked reads — no
    whole file in memory). Returns ``(False, reason)`` when the
    destination is missing, the sizes differ, the bytes differ,
    or the comparison itself fails. Never raises.
    """
    try:
        s = Path(str(src)).expanduser()
        d = Path(str(dst)).expanduser()
        if not d.is_file():
            return False, "no file at %s after the copy" % d
        if not s.is_file():
            return False, "no source file at %s to compare against" % s
        s_size = s.stat().st_size
        d_size = d.stat().st_size
        if s_size != d_size:
            return (False,
                    "%s holds %d bytes, not the %d copied"
                    % (d, d_size, s_size))
        with open(s, "rb") as fs, open(d, "rb") as fd:
            while True:
                a = fs.read(_COPY_CHUNK)
                b = fd.read(_COPY_CHUNK)
                if a != b:
                    return (False,
                            "bytes differ between %s and %s" % (s, d))
                if not a:
                    break
        return True, "%d bytes match at %s" % (s_size, d)
    except Exception as exc:  # noqa: BLE001 — a broken probe reads
        return False, "could not compare %s and %s: %s" % (src, dst, exc)


def hash_file(path):
    """SHA-256 a file (streaming), for capture-before-a-move.

    Returns ``(hexdigest, evidence)`` or ``(None, reason)`` when
    the file cannot be read. Never raises.
    """
    try:
        import hashlib

        target = Path(str(path)).expanduser()
        digest = hashlib.sha256()
        with open(target, "rb") as fh:
            while True:
                chunk = fh.read(_COPY_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest(), "%d bytes hashed at %s" % (
            target.stat().st_size, target)
    except Exception as exc:  # noqa: BLE001 — a broken probe reads
        return None, "could not hash %s: %s" % (path, exc)


def file_matches_hash(path, hexdigest):
    """Re-hash a file and compare against a captured digest.

    Returns ``(True, evidence)`` only when the bytes match.
    Returns ``(False, reason)`` otherwise. Never raises.
    """
    if not hexdigest:
        return False, "no digest to compare %s against" % path
    actual, _reason = hash_file(path)
    if actual is None:
        return False, "could not re-read %s to verify it" % path
    if actual != hexdigest:
        return False, "bytes at %s differ from the captured copy" % path
    return True, "bytes at %s match the captured digest" % path


def file_absent(path):
    """True only when nothing exists at ``path`` anymore.

    Returns ``(True, evidence)`` when the path is gone,
    ``(False, reason)`` when something is still there or the
    check itself fails. Never raises.
    """
    try:
        target = Path(str(path)).expanduser()
        if target.exists() or target.is_symlink():
            return False, "%s is still there" % target
        return True, "nothing exists at %s" % target
    except Exception as exc:  # noqa: BLE001 — a broken probe reads
        return False, "could not check %s: %s" % (path, exc)


def _process_iter_psutil():
    """List (name, create_time) pairs via psutil, or None.

    A plain function returning a list (not a generator): the
    caller must be able to tell "no psutil" apart from "no
    matching process", and a generator never returns None.
    """
    try:
        import psutil
    except Exception:  # noqa: BLE001 — no psutil, no process probe
        return None
    out = []
    try:
        for proc in psutil.process_iter(["name", "create_time"]):
            try:
                info = proc.info or {}
                out.append((str(info.get("name") or ""),
                            info.get("create_time")))
            except Exception:  # noqa: BLE001 — process died mid-scan
                continue
    except Exception:  # noqa: BLE001 — a broken scan reads no names
        return out
    return out


def _process_names_fallback():
    """List (name, started) pairs without psutil (tasklist / /proc).

    ``started`` is the process start time in epoch seconds, or
    None when the fallback cannot read one (tasklist carries no
    start times — an unreadable start counts as a match, as
    documented; /proc reads the process directory's mtime).
    Returns a list of pairs, or None when no fallback works.
    Never raises.
    """
    import sys as _sys

    if _sys.platform.startswith("win"):
        try:
            completed = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15)
            if completed.returncode != 0:
                return None
            names = []
            for line in (completed.stdout or "").splitlines():
                parts = line.split('","')
                if parts:
                    names.append((parts[0].strip('"'), None))
            return names
        except Exception:  # noqa: BLE001 — no fallback probe
            return None
    try:
        proc_root = Path("/proc")
        if not proc_root.is_dir():
            return None
        names = []
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                name = (entry / "comm").read_text(
                    encoding="utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001 — process died mid-scan
                continue
            try:
                started = entry.stat().st_mtime
            except Exception:  # noqa: BLE001 — unreadable start
                started = None
            names.append((name, started))
        return names
    except Exception:  # noqa: BLE001 — no fallback probe
        return None


def _stem(name):
    """Lowercase process-name stem (``.exe`` suffix ignored)."""
    stem = str(name or "").strip().lower()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    return stem


def _process_name_by_pid(pid):
    """Read one process's name without psutil (/proc or tasklist).

    Returns the name, or None when it cannot be read. Never raises.
    """
    import sys as _sys

    if not _sys.platform.startswith("win"):
        try:
            return (Path("/proc") / str(pid) / "comm").read_text(
                encoding="utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001 — process gone or unreadable
            return None
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15)
        if completed.returncode != 0:
            return None
        for line in (completed.stdout or "").splitlines():
            parts = line.split('","')
            if parts:
                return parts[0].strip('"')
        return None
    except Exception:  # noqa: BLE001 — no fallback probe
        return None


def process_running(name=None, pid=None, created_after=None):
    """Check a process exists in the world (read-back, never an act).

    ``name`` matches a process name (case-insensitive, ``.exe``
    suffix ignored); ``pid`` matches one process id; either may
    be given, both must match when both are given. ``created_after``
    (epoch seconds) requires the match to have started at/after
    that time — a process whose start time cannot be read counts
    as a match, exactly as launch verification already did.
    Returns ``(True, evidence)`` / ``(False, reason)``. Never
    raises, never guesses.
    """
    if name is None and pid is None:
        return False, "no process name or id to look for"
    want_name = None
    if name is not None:
        want_name = _stem(name)
        if not want_name:
            return False, "no process name to look for"
    if pid is not None:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False, "%r isn't a process id" % (pid,)
    try:
        import psutil as _psutil

        has_psutil = True
    except Exception:  # noqa: BLE001 — fall back below
        has_psutil = False
        _psutil = None
    if pid is not None:
        if has_psutil:
            try:
                if not _psutil.pid_exists(pid):
                    return False, "no process with id %d" % pid
                proc = _psutil.Process(pid)
                try:
                    proc_name = proc.name()
                except Exception:  # noqa: BLE001 — died mid-check
                    proc_name = ""
                try:
                    created = proc.create_time()
                except Exception:  # noqa: BLE001 — unreadable start
                    created = None
            except Exception as exc:  # noqa: BLE001 — unreadable pid
                return False, "could not check process %d: %s" % (pid, exc)
            if want_name is not None and _stem(proc_name) != want_name:
                return (False,
                        "process %d exists but is not named %s"
                        % (pid, name))
            if (created_after is not None and created is not None
                    and float(created) < float(created_after)):
                return (False,
                        "process %d started before the launch"
                        % pid)
            label = proc_name or str(pid)
            return True, "process %s (pid %d) is running" % (label, pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, "no process with id %d" % pid
        except PermissionError:
            pass  # exists, just not inspectable
        except Exception as exc:  # noqa: BLE001 — unreadable pid
            return False, "could not check process %d: %s" % (pid, exc)
        if created_after is not None:
            # No psutil: a /proc/<pid> directory's mtime is the
            # process start time on Linux. Unreadable (or Windows)
            # counts as a match, as documented.
            try:
                started = os.stat("/proc/%d" % pid).st_mtime
                if started < float(created_after):
                    return (False,
                            "process %d started before the launch"
                            % pid)
            except Exception:  # noqa: BLE001 — no start time to read
                pass
        if want_name is None:
            return True, "process %d exists" % pid
        proc_name = _process_name_by_pid(pid)
        if proc_name is None:
            return (False,
                    "could not read the name of process %d" % pid)
        if _stem(proc_name) != want_name:
            return (False,
                    "process %d exists but is not named %s" % (pid, name))
        return True, "process %s (pid %d) is running" % (proc_name, pid)
    seen = _process_iter_psutil()
    if seen is None:
        names = _process_names_fallback()
        if names is None:
            return (False,
                    "no process read-back available on this system")
        for proc_name, started in names:
            if _stem(proc_name) != want_name:
                continue
            # R1a: the fallback must honour created_after too — a
            # long-running namesake is not launch evidence. An
            # unreadable start time counts as a match, as
            # documented (same rule as the psutil path).
            if (created_after is not None and started is not None
                    and float(started) < float(created_after)):
                continue
            return True, "process %s is running" % proc_name
        return False, "no running process named %s" % (name,)
    for proc_name, created in seen:
        if _stem(proc_name) != want_name:
            continue
        if created_after is not None and created is not None:
            try:
                if float(created) < float(created_after):
                    continue
            except (TypeError, ValueError):
                pass
        return True, "process %s is running" % proc_name
    return False, "no running process named %s" % (name,)


def registry_dword_equals(key_path, value_name, expected):
    """Read one REG_DWORD back and compare it (Windows only).

    Returns ``(True, evidence)`` only when the value exists and
    equals ``expected``. Returns ``(False, reason)`` when the
    value differs, is missing, is not a DWORD, or there is no
    registry read-back available at all (non-Windows, or winreg
    missing). Never raises, never guesses.
    """
    try:
        expected_int = int(expected)
    except (TypeError, ValueError):
        return False, "%r isn't a whole number" % (expected,)
    try:
        import winreg
    except Exception:  # noqa: BLE001 — no read-back on this system
        return (False,
                "no registry read-back available on this system "
                "(winreg is not installed)")
    try:
        import sys as _sys

        if not _sys.platform.startswith("win"):
            return (False,
                    "no registry read-back available on this system "
                    "(not Windows)")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            current, value_type = winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return (False,
                "registry value %s\\%s is not set"
                % (key_path, value_name))
    except Exception as exc:  # noqa: BLE001 — unreadable key
        return False, "could not read %s\\%s: %s" % (
            key_path, value_name, exc)
    if value_type != winreg.REG_DWORD:
        return (False,
                "registry value %s\\%s is not a DWORD (type %s)"
                % (key_path, value_name, value_type))
    try:
        current_int = int(current)
    except (TypeError, ValueError):
        return (False,
                "registry value %s\\%s is not a number: %r"
                % (key_path, value_name, current))
    if current_int != expected_int:
        return (False,
                "registry value %s\\%s reads %d, not %d"
                % (key_path, value_name, current_int, expected_int))
    return (True,
            "registry value %s\\%s reads %d as expected"
            % (key_path, value_name, expected_int))


# ---------------------------------------------------------------------------
# R1b helpers (2026-11-06) — Wi-Fi / drive / wallpaper / window read-backs
# ---------------------------------------------------------------------------

def _run_netsh(args, timeout=15):
    """Run netsh and return (returncode, stdout+stderr) or (None, reason).

    Never raises. Windows only; anything else reads as no read-back.
    """
    import sys as _sys

    if not _sys.platform.startswith("win"):
        return None, "not Windows (netsh is a Windows tool)"
    try:
        completed = subprocess.run(
            ["netsh"] + list(args), capture_output=True, text=True,
            timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — no read-back
        return None, "could not run netsh: %s" % exc
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _netsh_field(output, label):
    """The value after ``label :`` in netsh output, or None."""
    for line in (output or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == label.lower():
            return value.strip()
    return None


def wifi_adapter_state(adapter="Wi-Fi"):
    """Read a network adapter's enabled/disabled state (netsh).

    Returns ``(True, evidence)`` when the adapter reads Enabled,
    ``(False, evidence)`` when it reads Disabled, and ``(None,
    reason)`` when the state cannot be read (not Windows, netsh
    failed, or the adapter is not listed). Never raises.
    """
    rc, out = _run_netsh(
        ["interface", "show", "interface", str(adapter or "Wi-Fi")])
    if rc is None:
        return None, out
    if rc != 0:
        return (None,
                "netsh could not show interface %r (exit %d)"
                % (adapter, rc))
    admin = _netsh_field(out, "Admin state")
    if admin is None:
        return (None,
                "netsh did not report an Admin state for %r" % adapter)
    lowered = admin.strip().lower()
    if lowered.startswith("enabled"):
        return True, "adapter %r reads Enabled" % adapter
    if lowered.startswith("disabled"):
        return False, "adapter %r reads Disabled" % adapter
    return None, "adapter %r reads an unknown state: %r" % (adapter, admin)


def wifi_connected_ssid():
    """The SSID this PC is currently associated with (netsh).

    Returns ``(ssid, None)`` when Wi-Fi is connected, ``(None,
    reason)`` when it is not connected or the state cannot be
    read. Never raises.
    """
    rc, out = _run_netsh(["wlan", "show", "interfaces"])
    if rc is None:
        return None, out
    if rc != 0:
        return (None,
                "netsh could not show Wi-Fi interfaces (exit %d)" % rc)
    state = _netsh_field(out, "State")
    ssid = _netsh_field(out, "SSID")
    if state is not None and state.strip().lower() == "connected" and ssid:
        return ssid, None
    if state is not None:
        return (None,
                "Wi-Fi state reads %r, not connected" % state.strip())
    return None, "netsh did not report a Wi-Fi state"


def wifi_profile_exists(ssid):
    """Whether a saved Wi-Fi profile with this name is listed.

    Returns ``(True, evidence)`` / ``(False, evidence)`` when the
    profile list could be read, ``(None, reason)`` when it could
    not. Never raises.
    """
    name = str(ssid or "").strip()
    if not name:
        return None, "no profile name to look for"
    rc, out = _run_netsh(["wlan", "show", "profiles"])
    if rc is None:
        return None, out
    if rc != 0:
        return (None,
                "netsh could not list Wi-Fi profiles (exit %d)" % rc)
    wanted = name.lower()
    for line in (out or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "all user profile":
            if value.strip().lower() == wanted:
                return True, "profile %r is listed" % name
    return False, "profile %r is not listed" % name


def drive_present(letter):
    """True only when the drive letter exists right now.

    Windows drive letters only; anything else reads as absent.
    Never raises.
    """
    try:
        raw = str(letter or "").strip()
        if not raw or not raw[0].isalpha():
            return False
        return Path("%s:\\" % raw[0].upper()).exists()
    except Exception:  # noqa: BLE001 — an unreadable letter is absent
        return False


def wallpaper_current():
    """The HKCU Desktop Wallpaper REG_SZ value (Windows only).

    Returns ``(value, None)`` when it could be read, ``(None,
    reason)`` otherwise. Never raises.
    """
    try:
        import winreg
    except Exception:  # noqa: BLE001 — no read-back on this system
        return (None,
                "no registry read-back available on this system "
                "(winreg is not installed)")
    try:
        import sys as _sys

        if not _sys.platform.startswith("win"):
            return (None,
                    "no registry read-back available on this system "
                    "(not Windows)")
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop", 0,
                winreg.KEY_QUERY_VALUE) as key:
            current, _t = winreg.QueryValueEx(key, "Wallpaper")
    except FileNotFoundError:
        return None, "registry Wallpaper value is not set"
    except Exception as exc:  # noqa: BLE001 — unreadable key
        return None, "could not read the Wallpaper value: %s" % exc
    return str(current), "desktop Wallpaper reads %r" % (current,)


def window_topmost(hwnd):
    """Whether a window has the WS_EX_TOPMOST style (Windows only).

    Returns ``(True/False, evidence)`` when the style could be
    read, ``(None, reason)`` otherwise. Never raises.
    """
    try:
        import ctypes
        import sys as _sys

        if not _sys.platform.startswith("win"):
            return (None,
                    "no window read-back available on this system "
                    "(not Windows)")
        user32 = ctypes.windll.user32
        _GWL_EXSTYLE = -20
        _WS_EX_TOPMOST = 0x00000008
        style = user32.GetWindowLongW(int(hwnd), _GWL_EXSTYLE)
        return (bool(style & _WS_EX_TOPMOST),
                "window style reads %s topmost"
                % ("with" if style & _WS_EX_TOPMOST else "without"))
    except Exception as exc:  # noqa: BLE001 — unreadable window
        return None, "could not read the window style: %s" % exc


def window_opacity_value(hwnd):
    """A layered window's opacity as a 0-100 percent (Windows only).

    Returns ``(percent, evidence)`` when it could be read,
    ``(None, reason)`` otherwise (not Windows, not a layered
    window, or the call failed). Never raises.
    """
    try:
        import ctypes
        import sys as _sys

        if not _sys.platform.startswith("win"):
            return (None,
                    "no window read-back available on this system "
                    "(not Windows)")
        user32 = ctypes.windll.user32
        alpha = ctypes.c_ubyte(0)
        key = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)
        if not user32.GetLayeredWindowAttributes(
                int(hwnd), ctypes.byref(key), ctypes.byref(alpha),
                ctypes.byref(flags)):
            return (None,
                    "GetLayeredWindowAttributes failed — the window "
                    "may not be a layered window")
        pct = int(round(alpha.value * 100 / 255))
        return pct, "window opacity reads %d%%" % pct
    except Exception as exc:  # noqa: BLE001 — unreadable window
        return None, "could not read the window opacity: %s" % exc
