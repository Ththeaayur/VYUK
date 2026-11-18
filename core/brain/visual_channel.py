"""U6 — Visual channel (tutor programme, 2026-11-08).

Lab leg only. The real-Windows field leg — a real MA 105 lesson on
his machine, the panel showing the bite and the board drawing the
region progressively, never showing the answer — is OWED, not this
batch.

Gap proved first (audit T6): the chat is text-only —
``ChatBubble(who, text, ...)`` (``gui/window_widgets_bubble.py:83``)
builds its body from ``_split_segments`` (``gui/window_widgets_
helpers.py:380``), which returns only ("prose", str) / ("code", str)
segments — no image segment kind exists, and the only QPixmap in the
chat path is the static Aura avatar chip. ``_add_bubble`` (``gui/
window_chat_bubbles.py:368``) takes text only. There is no lesson
component anywhere in ``gui/`` (the word "lesson" appears only in
two comments), no board component (the "board" hits are all the
"onboarding" substring), and no KaTeX/MathJax anywhere in ``gui/``.

LOPHOLE FAMILY — "a tutor that shows the wrong thing, or shows the
answer": a visual channel that renders the gold leaks worse than a
spoken slip, because an image stays on screen — an image of the
answer is still the answer; and a board that redraws from scratch
every step teaches nothing, because the student never sees the
picture build. ONE design closes the class: the visual channel
renders ONLY what the Socratic speaker may say — the panel and the
board are fed by gold-guarded strings (U3's ``_contains_gold``,
imported, not copied), never by the bite or the gold directly; and
the board is PROGRESSIVE — each step adds one element to the picture
and the picture accumulates, never redraws from scratch.

The split, precisely: this module is the ONLY place that touches
the gold in the visual channel, and it touches it only to guard.
``build_panel_state`` reads ``bite.gold`` to guard the bite text,
the check, and the source — then drops it; the returned
``LessonPanelState`` has no gold field. ``ProgressiveBoard.add_step``
guards every label before the step is stored. The GUI widgets
(``gui/lesson_panel.py``) render state dataclasses only — they hold
no bite, no gold, no model, and no write handle of any kind. The
panel takes a plain mastery dict (from ``StudentModel.get_skill``,
whose docstring states "Reading never writes"), never the model.

REJECTED alternatives (recorded, not carried forward):
- Rendering the bite text / worked solution as an image (an image
  of the answer is still the answer — the gold guard must cover
  the visual channel exactly as it covers speech).
- A static image per bite (a single finished diagram teaches the
  end, not the move — the progressive board is what makes the
  diagram a lesson).
- Letting the GUI read the gold to "helpfully" annotate the diagram
  (any path where the rendering context contains the gold is a leak
  path — the split is structural, same as U5).
- A separate board app or window (the lesson lives in the settled
  home — the panel fits the docked chat layout, it does not open a
  second window).
- A plotting dependency (matplotlib / pyqtgraph) for the board (a
  hard dependency for drawing primitives is overkill — QPainter
  primitives are enough, following the overview_panel fallback
  pattern: paint natively, never crash, never ask for installs).

Honest boundary, stated not hidden: an image's PIXELS are not
text-guardable. Board images are safe by construction — every label
on them passes through the guard before it is stored. A figure from
a course file he supplies (D-T2) is his own file; its caption and
its source attribution are guarded, but its pixels are not checked —
the tutor never claims a supplied figure is gold-free, it only
claims the words around it are.
"""

from __future__ import annotations

from dataclasses import dataclass

from .check_loop import _contains_gold
from .student_model import MASTERY_TRANSFERS

# The ladder has exactly three levels (U5); the panel shows them.
LADDER_LEVELS = 3

# Board space: every element lives in a 0–100 square, scaled to the
# widget by the GUI. A step is one move of the lesson: one outline,
# one boundary, one set of arrows — never the whole picture at once.
BOARD_MIN = 0.0
BOARD_MAX = 100.0

ELEMENT_KINDS = frozenset(
    {"rect", "ellipse", "line", "arrow", "point", "label"})

_GENERIC_BITE = "Let's go over this bite, one piece at a time."
_GENERIC_CHECK = "What does this bite ask for?"
_GENERIC_LABEL = "The next step of the picture."
_GENERIC_CAPTION = "A diagram for this bite."
_GENERIC_SOURCE = "Course material."


def _clamp_coord(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return BOARD_MIN
    if number != number:  # NaN: no coordinate at all.
        return BOARD_MIN
    return min(BOARD_MAX, max(BOARD_MIN, number))


def _guarded(text, fallback, gold):
    """A rendered string, gold-guarded; the generic line when the
    text would reveal the gold (U3's rule, U5's shape)."""
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return fallback
    if gold is not None and _contains_gold(text, gold):
        return fallback
    return text


# ------------------------------------------------------------------
# Board elements and steps (pure data — the GUI only paints them)
# ------------------------------------------------------------------

@dataclass(frozen=True)
class BoardElement:
    """One drawable primitive in 0–100 board space.

    ``kind`` is one of ELEMENT_KINDS. ``x0, y0, x1, y1`` are the
    bounding box (a point uses x0, y0 only; a label is anchored at
    x0, y0). ``label`` is an optional short text drawn with the
    element — guarded at the step level, never raw.
    """

    kind: str
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    label: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "x0", _clamp_coord(self.x0))
        object.__setattr__(self, "y0", _clamp_coord(self.y0))
        object.__setattr__(self, "x1", _clamp_coord(self.x1))
        object.__setattr__(self, "y1", _clamp_coord(self.y1))


@dataclass(frozen=True)
class BoardStep:
    """One step of the lesson's picture: a guarded label and the
    elements this step adds. Steps accumulate — a step never erases
    an earlier step."""

    label: str
    elements: tuple = ()


class ProgressiveBoard:
    """The progressive board as code state: an ordered step list
    and a position in it. ``current_elements()`` is the picture as
    it stands — every element from step 0 through the current step,
    in order. Stepping back shows the earlier picture; no step ever
    removes an element."""

    def __init__(self):
        self._steps: list[BoardStep] = []
        self._index = -1  # -1: no step shown yet.

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def position(self) -> int:
        """Steps shown so far (0 when nothing is shown yet)."""
        return self._index + 1

    def add_step(self, label, elements, *, gold=None):
        """Append one step. Every label is gold-guarded before the
        step is stored; an element of unknown kind is dropped, never
        fatal. Returns the step's index."""
        safe_label = _guarded(label, _GENERIC_LABEL, gold)
        safe_elements = []
        for element in elements or ():
            if not isinstance(element, BoardElement):
                continue
            if element.kind not in ELEMENT_KINDS:
                continue
            if element.label is not None:
                safe = _guarded(element.label, "", gold)
                if safe != element.label:
                    element = BoardElement(
                        element.kind, element.x0, element.y0,
                        element.x1, element.y1, None)
            safe_elements.append(element)
        self._steps.append(BoardStep(safe_label, tuple(safe_elements)))
        # Adding a step shows it: the picture grows as the lesson
        # advances. Stepping back is how the student revisits an
        # earlier picture — a new step is never hidden on arrival.
        self._index = len(self._steps) - 1
        return self._index

    def step_forward(self) -> bool:
        """Show the next step. False when already at the last."""
        if self._index < len(self._steps) - 1:
            self._index += 1
            return True
        return False

    def step_back(self) -> bool:
        """Step back to the earlier picture. False at the start."""
        if self._index > 0:
            self._index -= 1
            return True
        return False

    def current_step(self):
        """The step on screen, or None before the first step."""
        if 0 <= self._index < len(self._steps):
            return self._steps[self._index]
        return None

    def current_elements(self) -> tuple:
        """The picture as it stands: every element from step 0
        through the current step, in order. Never a redraw from
        scratch — the earlier steps are still in it."""
        if self._index < 0:
            return ()
        out = []
        for step in self._steps[: self._index + 1]:
            out.extend(step.elements)
        return tuple(out)

    def current_labels(self) -> tuple:
        """Every label on screen: the step labels so far plus the
        labels of the elements in the picture."""
        if self._index < 0:
            return ()
        labels = [step.label for step in self._steps[: self._index + 1]]
        for element in self.current_elements():
            if element.label:
                labels.append(element.label)
        return tuple(labels)

    def snapshot(self) -> dict:
        """Plain-data snapshot (B21 seam shape). Carries no gold —
        every label in it was guarded when the step was added."""
        return {
            "steps": [
                {
                    "label": step.label,
                    "elements": [
                        {
                            "kind": e.kind, "x0": e.x0, "y0": e.y0,
                            "x1": e.x1, "y1": e.y1, "label": e.label,
                        }
                        for e in step.elements
                    ],
                }
                for step in self._steps
            ],
            "index": self._index,
        }

    @classmethod
    def from_snapshot(cls, data):
        """Restore a snapshot. A malformed snapshot restores an
        empty board, never a crash."""
        board = cls()
        try:
            steps = (data or {}).get("steps") or []
            for raw in steps:
                elements = []
                for item in (raw or {}).get("elements") or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("kind") not in ELEMENT_KINDS:
                        continue
                    elements.append(BoardElement(
                        item.get("kind"), item.get("x0", 0.0),
                        item.get("y0", 0.0), item.get("x1", 0.0),
                        item.get("y1", 0.0), item.get("label")))
                board._steps.append(BoardStep(
                    str((raw or {}).get("label") or _GENERIC_LABEL),
                    tuple(elements)))
            index = (data or {}).get("index", -1)
            board._index = index if isinstance(index, int) else -1
            if board._steps and board._index < 0:
                board._index = 0
            if board._index >= len(board._steps):
                board._index = len(board._steps) - 1
        except Exception:  # noqa: BLE001 — a bad snapshot is an
            # empty board, never a crash in the lesson.
            return cls()
        return board


# ------------------------------------------------------------------
# Image messages (chat image rendering — the logic half)
# ------------------------------------------------------------------

@dataclass(frozen=True)
class ImageMessage:
    """An image message for the chat stream: the caption and the
    source attribution are guarded text; ``image_ref`` is opaque
    (bytes or a path) — the pixels are not text-guardable (see the
    module boundary note above)."""

    caption: str
    source: str
    image_ref: object = None


def build_image_message(caption, source, image_ref, *, gold=None):
    """Build an image message with guarded caption + source. The
    image itself passes through unchanged — only the words around
    it are guarded."""
    return ImageMessage(
        caption=_guarded(caption, _GENERIC_CAPTION, gold),
        source=_guarded(source, _GENERIC_SOURCE, gold),
        image_ref=image_ref,
    )


# ------------------------------------------------------------------
# Lesson panel state (the panel renders this, nothing else)
# ------------------------------------------------------------------

@dataclass(frozen=True)
class LessonPanelState:
    """Everything the lesson panel renders, and nothing it must
    not see: no gold field, no bite object, no model. The mastery
    bar reads ``mastery_p`` / ``transfers`` / ``mastered`` from a
    plain record — the panel never holds the model, so it cannot
    write mastery."""

    bite_text: str
    check: str
    ladder_level: int
    ladder_max: int
    mastery_p: float
    transfers: int
    mastered: bool
    source: str


def build_panel_state(bite, *, ladder_level=0, mastery=None,
                      source="", gold=None):
    """Build the panel state from a bite. The gold is used only to
    guard the rendered strings, then dropped — the state carries no
    gold. ``mastery`` is a plain record dict (``StudentModel.
    get_skill`` output); the panel path never receives the model
    itself, so it holds no write handle."""
    if gold is None:
        gold = getattr(bite, "gold", "") or ""
    record = mastery or {}
    try:
        level = int(ladder_level)
    except (TypeError, ValueError):
        level = 0
    level = min(LADDER_LEVELS, max(0, level))
    try:
        p = float(record.get("p", 0.30))
    except (TypeError, ValueError):
        p = 0.30
    p = min(1.0, max(0.0, p))
    try:
        transfers = int(record.get("transfers", 0))
    except (TypeError, ValueError):
        transfers = 0
    transfers = max(0, transfers)
    return LessonPanelState(
        bite_text=_guarded(
            getattr(bite, "bite", ""), _GENERIC_BITE, gold),
        check=_guarded(
            getattr(bite, "check", ""), _GENERIC_CHECK, gold),
        ladder_level=level,
        ladder_max=LADDER_LEVELS,
        mastery_p=p,
        transfers=transfers,
        mastered=transfers >= MASTERY_TRANSFERS,
        source=_guarded(source, "", gold) if source else "",
    )


def panel_state_from_session(bite, session, model, *, source=""):
    """Driver convenience: read the ladder level from a U5
    SocraticSession and the mastery record from a U1 StudentModel.
    Read-only on both — ``get_skill`` never writes, and this
    function calls nothing else on the model."""
    try:
        level = session.ladder_level()
    except Exception:  # noqa: BLE001 — no session state is level 0.
        level = 0
    try:
        record = model.get_skill(
            getattr(bite, "skill", "") or "") or {}
    except Exception:  # noqa: BLE001 — no record is cold start.
        record = {}
    return build_panel_state(
        bite, ladder_level=level, mastery=record, source=source)
