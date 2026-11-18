"""Forgiving-selection engine (Accessibility track, wave 31, §5.23) — Nova's own code.

Covers AC1-9 (forgiving-selection engine), AC1-10 (voice-grid pattern)
and AC1-6 (opentrack head patterns). These are new SOURCES for HA's
existing funnel discipline, not a parallel input stack.

THE INV-2/3 RULE, stated once and enforced everywhere in this module:
a selection engine with its own click path is a side-channel executor
for the users least able to catch its errors. So this module NEVER
clicks, NEVER dispatches, and NEVER executes. It turns noisy input
(voice grids, head poses, switch presses) into a selection descriptor
shaped for ``tools.execute`` — and the product dispatches that
descriptor through the funnel, with its gates, or not at all.
``direct_click`` exists only to refuse.

SUPERSESSIONS (D-29, counted once):
- Wave 15's dwell shelf is SUPERSEDED by AC1-9. Dwell-time alone
  never confirms a selection here: ``dwell_confirm`` refuses.
- PC-C3's One Euro filter is KEPT: noisy head/pointer streams are
  smoothed with the $1 filter before they are ranked.

STANDALONE RULE (edition precedent): this module imports nothing from
any other ac_* module, and no ac_* module imports it. All inputs are
plain data supplied by the caller; this library reads no device, no
camera, and no microphone by itself.
"""

from __future__ import annotations

import math

__all__ = [
    "SelectionRefusal",
    "build_grid",
    "cell_for_point",
    "head_to_cell",
    "OneEuroFilter",
    "rank_candidates",
    "confirm_selection",
    "dwell_confirm",
    "to_execute_descriptor",
    "direct_click",
]


class SelectionRefusal(Exception):
    """Raised when a selection path would bypass the funnel or a rule."""


def build_grid(rows, cols):
    """Build a voice-grid: rows x cols cells, each a plain descriptor.

    Cell ids are stable ("r1c2" style) so a voice command can name a
    cell ("row two column three") without pointing at anything.
    """
    if rows < 1 or cols < 1:
        raise ValueError("grid needs at least 1 row and 1 column")
    cells = []
    for r in range(1, rows + 1):
        for cc in range(1, cols + 1):
            cells.append({"id": "r%dc%d" % (r, cc), "row": r, "col": cc})
    return {"rows": rows, "cols": cols, "cells": cells}


def cell_for_point(x, y, grid, width, height):
    """Map a normalised point (0..1, 0..1) to its grid cell.

    Pure geometry: no device reads, no side effects. Points outside
    the unit square are clamped to the nearest edge cell rather than
    dropped, because a user with shaky input should not lose a
    selection to a rounding edge.
    """
    if width <= 0 or height <= 0:
        raise ValueError("grid area must be positive")
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    col = min(int(x * grid["cols"]) + 1, grid["cols"])
    row = min(int(y * grid["rows"]) + 1, grid["rows"])
    for cell in grid["cells"]:
        if cell["row"] == row and cell["col"] == col:
            return cell
    raise AssertionError("grid cell missing for a clamped point")


def head_to_cell(yaw, pitch, grid):
    """Map an opentrack-style head pose to a voice-grid cell.

    Yaw (left/right, degrees, negative = left) spans the columns;
    pitch (up/down, degrees, negative = up) spans the rows. The span
    is deliberately wide (+/-40 degrees) so a user with limited head
    range still reaches every cell; poses beyond the span clamp to
    the edge cell instead of failing.
    """
    yaw = min(max(yaw, -40.0), 40.0)
    pitch = min(max(pitch, -40.0), 40.0)
    x = (yaw + 40.0) / 80.0
    y = (pitch + 40.0) / 80.0
    return cell_for_point(x, y, grid, 1.0, 1.0)


class OneEuroFilter:
    """The $1 filter (PC-C3, kept): smooths a noisy scalar stream.

    Standard formulation: adaptive cutoff from the signal's own
    derivative, so slow deliberate motion stays precise while fast
    tremor is damped. ``frequency`` is the sample rate in Hz.
    """

    def __init__(self, frequency=60.0, min_cutoff=1.0, beta=0.0,
                 d_cutoff=1.0):
        if frequency <= 0:
            raise ValueError("frequency must be positive")
        self.frequency = frequency
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    @staticmethod
    def _alpha(cutoff, frequency):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / frequency
        return 1.0 / (1.0 + tau / te)

    def __call__(self, value, timestamp=None):
        if timestamp is None:
            timestamp = 0.0 if self._t_prev is None else self._t_prev + 1.0
        if self._x_prev is None:
            self._x_prev = value
            self._t_prev = timestamp
            return value
        dt = timestamp - self._t_prev
        frequency = 1.0 / dt if dt > 0 else self.frequency
        self._t_prev = timestamp
        dx = (value - self._x_prev) * frequency
        dx_hat = (self._alpha(self.d_cutoff, frequency) * dx
                  + (1.0 - self._alpha(self.d_cutoff, frequency))
                  * self._dx_prev)
        self._dx_prev = dx_hat
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        value_hat = (self._alpha(cutoff, frequency) * value
                     + (1.0 - self._alpha(cutoff, frequency))
                     * self._x_prev)
        self._x_prev = value_hat
        return value_hat


def rank_candidates(point, candidates, tolerance):
    """Forgiving selection: rank candidates by distance to the point.

    ``candidates`` is a list of {"id": ..., "x": ..., "y": ...}
    descriptors in the same coordinate space as ``point``. Every
    candidate within ``tolerance`` is returned nearest-first with a
    confidence in (0, 1]; candidates outside tolerance are dropped,
    never silently snapped to. An empty result means "no confident
    target" — the product must ask, not guess.
    """
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    ranked = []
    for cand in candidates:
        dist = math.hypot(cand["x"] - point[0], cand["y"] - point[1])
        if dist <= tolerance:
            ranked.append({"id": cand["id"], "distance": dist,
                           "confidence": 1.0 - (dist / (tolerance + dist))})
    ranked.sort(key=lambda r: r["distance"])
    return ranked


CONFIRM_EVENTS = frozenset(["voice_select", "switch_press", "head_nod",
                            "typed_confirm"])


def confirm_selection(candidate_id, confirm_event):
    """Confirm a ranked candidate with an EXPLICIT confirm event.

    Dwell-time alone never confirms (see ``dwell_confirm``). The
    confirm event must be one of the explicit events: a spoken
    "select", a switch press, a head-nod pattern, or a typed confirm.
    """
    if confirm_event not in CONFIRM_EVENTS:
        raise SelectionRefusal(
            "confirm event %r is not an explicit confirm; dwell-time "
            "alone never confirms a selection (AC1-9)" % (confirm_event,))
    if not candidate_id:
        raise ValueError("candidate id required")
    return {"candidate_id": candidate_id, "confirmed_by": confirm_event}


def dwell_confirm(candidate_id, dwell_seconds):
    """Refused: wave 15's dwell shelf is superseded by AC1-9."""
    raise SelectionRefusal(
        "dwell confirmation (%.1fs on %r) is refused: the dwell shelf "
        "is superseded by AC1-9; selections need an explicit confirm "
        "event" % (dwell_seconds, candidate_id))


def to_execute_descriptor(selection):
    """Shape a confirmed selection for tools.execute (INV-2/3).

    Returns a plain descriptor the product dispatches through the
    funnel. This function performs no action itself.
    """
    if "confirmed_by" not in selection:
        raise SelectionRefusal(
            "unconfirmed selection cannot become an execute descriptor")
    return {"action": "activate",
            "target_id": selection["candidate_id"],
            "confirmed_by": selection["confirmed_by"],
            "via": "tools.execute"}


def direct_click(target_id):
    """Refused, always: this engine has no click path (INV-2/3)."""
    raise SelectionRefusal(
        "direct click on %r refused: selections dispatch through "
        "tools.execute only, never through the selection engine"
        % (target_id,))
