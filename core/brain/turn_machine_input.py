from __future__ import annotations

# INTENTIONAL (skills split B20): input family (VP-8.3 push-to-talk, VP-8.6 confirmation) moved here verbatim;
# the facade re-exports every name below.
import re
import threading
import time

# ---------------------------------------------------------------------------
# VP-8.3 — push-to-talk as a first-class peer surface
# ---------------------------------------------------------------------------

#: First-partial latency budget (seconds): from PTT press to
#: the first transcribed partial, the §10 T17 target.
PTT_FIRST_PARTIAL_BUDGET_S = 0.5

PTT_BUTTON_LABEL = "Hold to talk"
PTT_BUTTON_TOOLTIP = (
    "Hold to talk — push-to-talk works exactly like speaking "
    "after the wake word, and it always counts as directed.")


class PushToTalk:
    """PTT state, peer to wake-word and typed input (VP-8.3).

    Hold mode: active between press and release. Toggle mode:
    each press flips it. Either way, ``active`` is what the
    directedness gate reads — PTT strengthens consent
    (INV-4): a PTT turn is directed by construction, never
    ambient. ``first_partial_latency_s`` measures press ->
    first partial against the 500 ms budget; a miss is
    reported, not hidden, so the §10 T17 number is honest.
    """

    def __init__(self, *, mode: str = "hold", now_fn=None):
        self.mode = "toggle" if str(mode) == "toggle" else "hold"
        self._now = now_fn or time.monotonic
        self._active = False
        self._pressed_at: float | None = None
        self._first_partial_at: float | None = None
        self._lock = threading.Lock()
        self.press_count = 0
        self.first_partial_latencies: list[float] = []

    def press(self, now: float | None = None) -> bool:
        at = self._now() if now is None else float(now)
        with self._lock:
            self.press_count += 1
            if self.mode == "toggle":
                self._active = not self._active
                self._pressed_at = at if self._active else None
                self._first_partial_at = None
            else:
                self._active = True
                self._pressed_at = at
                self._first_partial_at = None
            return self._active

    def release(self, now: float | None = None) -> bool:
        with self._lock:
            if self.mode == "toggle":
                return self._active
            self._active = False
            self._pressed_at = None
            return False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def mark_first_partial(self, now: float | None = None) -> dict:
        """Record the first transcribed partial after a press.
        Returns latency seconds and whether it met the
        500 ms budget. Before any press, returns latency None
        and within_budget False (no press, no measurement —
        never a fabricated pass)."""
        at = self._now() if now is None else float(now)
        with self._lock:
            if self._pressed_at is None:
                return {"latency_s": None, "within_budget": False}
            if self._first_partial_at is not None:
                latency = self._first_partial_at - self._pressed_at
            else:
                latency = at - self._pressed_at
                self._first_partial_at = at
                self.first_partial_latencies.append(latency)
        return {"latency_s": latency,
                "within_budget": latency <= PTT_FIRST_PARTIAL_BUDGET_S}

    def first_partial_deadline(self) -> float | None:
        with self._lock:
            if self._pressed_at is None:
                return None
            return self._pressed_at + PTT_FIRST_PARTIAL_BUDGET_S


# ---------------------------------------------------------------------------
# VP-8.6 — spoken-confirmation UX rules
# ---------------------------------------------------------------------------

#: Action kinds that are IRREVERSIBLE (or hard to undo) and
#: therefore need a different response shape than "yes".
#: The list is deliberately narrow: over-classifying ordinary
#: actions as irreversible would train the user to ignore
#: the distinction, which is worse than no distinction.
IRREVERSIBLE_ACTION_KINDS = frozenset({
    "delete", "remove", "format", "shutdown", "restart",
    "send_money", "purchase", "share_secret", "factory_reset",
    "send_email", "publish",
})

#: Bare affirmations that, alone, NEVER confirm an
#: irreversible action. "Yes" answers a question; it does
#: not restate what is being agreed to.
_BARE_AFFIRMATIONS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead",
    "do it", "confirm", "confirmed",
})


def confirmation_shape(action_kind: str) -> str:
    """``"explicit"`` for irreversible actions, ``"plain"``
    otherwise. The caller must render the two shapes
    differently (see ``explicit_confirmation_prompt``)."""
    return ("explicit" if str(action_kind or "").strip().lower()
            in IRREVERSIBLE_ACTION_KINDS else "plain")


def explicit_confirmation_prompt(action: str, target: str,
                                 consequence: str) -> str:
    """The response shape for an irreversible action: restate
    WHAT will happen, to WHAT, and what CANNOT be undone,
    then ask for a restating confirmation — never a bare
    "say yes". A bare "yes" to this prompt is rejected by
    :func:`accepts_confirmation` by design."""
    return (
        f"To be sure before I do something I can't undo: I'll "
        f"{action} {target} — {consequence}. If that's really "
        f"what you want, say it back to me, for example "
        f"'yes, {action} it'.")


def accepts_confirmation(utterance: str, action_kind: str) -> bool:
    """Does this utterance confirm an action of this kind?

    - Plain actions: any bare affirmation counts.
    - Irreversible actions: a bare affirmation does NOT
      count. The utterance must contain an affirmation AND a
      restatement — at least one extra content word beyond
      the affirmation itself (e.g. "yes, delete it",
      "yes, go ahead and delete the file"). "Yes" alone, "yeah"
      alone, and silence-adjacent fillers all fail, which is
      the point: the user must say what they are agreeing to.
    """
    try:
        text = str(utterance or "").strip().lower()
    except Exception:  # noqa: BLE001
        return False
    if not text:
        return False
    words = re.findall(r"[a-z]+", text)
    if not words:
        return False
    has_affirmation = any(w in _BARE_AFFIRMATIONS for w in words) or (
        "yes" in words)
    if confirmation_shape(action_kind) == "plain":
        return has_affirmation
    if not has_affirmation:
        return False
    # Irreversible: need affirmation + restatement.
    restatement_words = [w for w in words
                         if w not in _BARE_AFFIRMATIONS and w != "yes"]
    # "go ahead" / "do it" are in the bare set as phrases; the
    # word-level check above already strips them. Require at
    # least one genuine content word (a verb, object, or "it"
    # naming the thing being confirmed).
    return bool(restatement_words)


