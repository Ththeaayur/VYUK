# Split from core/skills_plus.py (Phase A batch A12): moved verbatim.
import threading

# INTENTIONAL: names shared by several impl groups live in the
# helpers module (plan section 2 P3(c)); imported here so each
# moved function resolves its bare names in this module.
from .skills_plus_helpers import _NOT_WINDOWS, _is_windows

# ---------------------------------------------------------------------------
# VOICE — read words aloud on demand (system voice)
# ---------------------------------------------------------------------------


_SPEAK_START_WAIT_S = 2.0


def _speak_worker(text: str, started=None, error=None) -> None:
    """Runs on a daemon thread: pyttsx3 is blocking and not thread-safe
    to share, so each request gets its own engine on its own thread.

    Fix 7 (tools loopholes): the worker reports back. ``started`` is
    set the moment the engine is initialised (speech is queued next,
    so the voice WILL sound barring a mid-speech failure); ``error``
    records the exception when the engine never starts. The old shape
    swallowed every failure and the caller claimed "reading aloud
    now" before the thread had even started — a user heard nothing
    while the model narrated speech.
    """
    try:
        import pyttsx3

        engine = pyttsx3.init()
        if started is not None:
            started.set()
        try:
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 — speech failure is
        # reported to the caller through ``error``; it must never
        # surface as a crash, and it must never read as speech.
        if error is not None:
            error["error"] = exc
        if started is not None:
            started.set()


def speak_text(text: str):
    """Speak a given string aloud right now, in the PC's system voice.

    Fix 7 (tools loopholes): returns an Outcome. The result is
    verified only when the voice engine actually started; an engine
    that never starts is a failed outcome naming the error, and an
    engine still starting after the wait is an unverified outcome
    saying so. The verified message keeps the old wording
    ("Reading aloud now …") so existing callers keep working.
    """
    spoken = (text or "").strip()
    if not spoken:
        return "There's nothing to read aloud — give me the words."
    try:
        import pyttsx3  # noqa: F401 — presence check only; the worker re-imports
    except ImportError:
        return ("Reading aloud isn't available in this build of VYUK — "
                "the read-aloud part didn't make it into the package.")
    truncated = len(spoken) > 1000
    if truncated:
        spoken = spoken[:1000]
    from . import tool_outcome as _outcome  # Fix 7: verify the start
    _started = threading.Event()
    _error: dict = {}
    thread = threading.Thread(
        target=_speak_worker, args=(spoken, _started, _error),
        daemon=True)
    thread.start()
    _confirmed = _started.wait(_SPEAK_START_WAIT_S)
    note = " (only the first 1000 characters — ask again for the rest)" if truncated else ""
    if _error.get("error") is not None:
        return _outcome.failed(
            "Reading aloud failed — the system voice engine could not "
            "start: %s" % _error["error"],
            str(_error["error"]))
    if _confirmed:
        return _outcome.verified(
            f"Reading aloud now in the system voice{note}: \"{spoken[:60]}…\"",
            "system voice engine started")
    return _outcome.unverified(
        f"Reading aloud was started in the background{note}, but the "
        "system voice engine is still starting, so I couldn't confirm "
        "it began.",
        "engine start not confirmed within %.0f s" % _SPEAK_START_WAIT_S)

# ---------------------------------------------------------------------------
# QUIET — Focus Assist (do-not-disturb)
# ---------------------------------------------------------------------------

_FOCUS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
_FOCUS_VALUE = "NOC_GLOBAL_SETTING_TOASTS_ENABLED"


def set_focus_assist(on: bool = True):
    """Turn Windows Focus Assist (toast muting) on or off, verified by read-back.

    R1a (tools loopholes, seam pass 2): the read-back now goes
    through the shared core.tool_outcome.registry_dword_equals
    helper, and the result is an Outcome — verified only when the
    registry value reads back as written. Refusals stay plain
    strings.
    """
    if not _is_windows():
        return f"Focus Assist is a Windows feature — {_NOT_WINDOWS}"
    try:
        import winreg
    except ImportError:
        return "Focus Assist needs the Windows registry module (winreg), which is unavailable."
    # Windows represents this as "toast notifications enabled": Focus
    # Assist ON == toasts muted (0). The write is verified by reading
    # the value back — no claimed success the OS didn't confirm.
    target = 0 if on else 1
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _FOCUS_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            winreg.SetValueEx(key, _FOCUS_VALUE, 0, winreg.REG_DWORD, target)
    except OSError as e:
        return f"Could not change Focus Assist: {e}"
    from . import tool_outcome as _outcome  # R1a: seam wiring

    ok, evidence = _outcome.registry_dword_equals(
        _FOCUS_KEY, _FOCUS_VALUE, target)
    if on:
        message = ("Focus Assist is on — pop-up notifications are muted. "
                   "It takes effect within a few seconds; alarms still "
                   "get through.")
    else:
        message = "Focus Assist is off — pop-up notifications will show again."
    if ok:
        return _outcome.verified(message, evidence)
    return _outcome.failed(
        "Windows didn't keep the Focus Assist change — it may be "
        f"managed by a work/school policy on this PC. ({evidence})",
        evidence)
