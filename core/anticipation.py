"""Anticipation for VYUK Wave 7 (World & Reach, v5.62) — CA-1 / AN-1 /
AN-2 / AN-3 / AN-4 / PF-14.

Closest-to-assistant item of Wave 7, built to LIVE wherever lawful and
possible — but every live surface below is gated, and every gate
fails closed (refusal / suppression / silence, never a degraded
leak or a hedged guess).

WHAT THIS MODULE IS
-------------------
* CA-1 — an ActivityWatch-style window/app activity stream. The
  stream emits :class:`ActivityEvent` objects (app + title + time)
  from caller-supplied foreground observations. It performs NO
  platform capture itself: no window enumeration, no screenshots,
  no keylogging, no process listing. The caller (a future wired
  watcher) supplies one foreground observation at a time, and this
  module decides whether that observation may become an event.
* AN-1 — "River", an online next-action predictor over that
  stream. It learns app-to-app transitions as events arrive and
  tracks its own honest per-user accuracy.
* AN-2 — PrefixSpan frequent-sequence mining over caller-supplied
  sequences (a memory-DB export or a CA-1 export handed in by the
  caller). It never opens a database and never writes anything.
* AN-3 / AN-4 — DEV-ONLY proactivity benches: when-to-speak gate
  datasets and scoring. They are evaluation helpers only and are
  never wired to runtime suggestions (see ``DEV_ONLY``).
* PF-14 — user-defined routines: trigger + plain-language actions,
  editable and deletable, run only through an injected executor.

GATES (each is tested in test_v562_anticipation.py)
--------------------------------------------------
1. CA-1 capture guard (S-6 posture). ``ActivityCaptureSession``
   REFUSES to emit any event unless ALL three hold at emit time:
   (a) a live per-session opt-in (``grant_opt_in()`` on THIS
   session object — opt-in is never global, never persisted by
   this module, and dies with the session object);
   (b) an indicator kind set (``set_indicator(kind)`` — the GUI
   indicator that must be visible while capture is live);
   (c) the denylist applied (the session always carries its own
   denylist; see below). Missing any one -> ``emit()`` returns
   ``(None, reason)`` and ``emit_strict()`` raises
   :class:`ActivityRefusedError`. Nothing is recorded.
2. CA-1 denylist, fail-closed, suppress-before-capture. A process
   match or a title-keyword match SUPPRESSES the event: the event
   is never constructed with the offending title, never stored,
   and never fed to the predictor. Only an anonymous
   ``suppressed`` counter increments — the suppressed title/process
   text is never retained. A match suppresses; it never
   captures-then-filters.
3. AN-1 progressive validation + silence floor. ``suggestion()``
   returns ``None`` (silence) unless (a) at least
   ``MIN_EVIDENCE_SAMPLES`` predictions have been scored against
   real outcomes (accuracy evidence exists), AND (b) the winning
   transition's confidence is >= ``SUGGESTION_CONFIDENCE_THRESHOLD``.
   Below the threshold the answer is silence — never a hedged
   suggestion ("maybe X", "you might want X"). ``predict_next()``
   still exposes the raw prediction + confidence for evaluation,
   but it never speaks; only ``suggestion()`` is the speakable
   surface, and it enforces both gates.
4. AN-2 own core. The PrefixSpan implementation in this module is
   ORIGINAL code written for this module. It does NOT import
   Extratools (whose licence is unverified) or any other sequence
   mining library; importing such a library would have made the
   shipped item depend on an unverified licence, so the dependency
   is sidestepped entirely by owning the core. ``prefix_span()``
   takes caller-supplied sequences only — it performs no database
   reads or writes.
5. PF-14 plan-never-execute + confirm-gate routing. A
   :class:`RoutineRunner` built WITHOUT an executor PLANS but
   never executes: ``run()`` returns a ``plan-only`` result with
   zero executed steps. There is deliberately NO direct-execution
   entry point in this module — no shell, no tool call, no GUI
   action. With an executor, ``run()`` routes EVERY step through
   that injected callable (the production funnel, which owns the
   human confirm gate); the runner itself never bypasses it, and
   a step the executor rejects or that raises is reported as the
   failed step by index and by text, with execution stopped.

LICENCE / DEPENDENCIES
----------------------
Standard library only (``dataclasses``, ``collections``). No new
dependencies, no new tools, no declaration changes, no telemetry
writes, no file writes, no database access. Nothing in this module
reads or writes the install tree's ``data/`` directory: routine
storage is in-memory only (the caller owns any persistence), and
the predictor/stream keep state in memory only.

STATUS — read before wiring
----------------------------
* LIVE (gated, as above): CA-1 stream gate + denylist, AN-1 River
  predictor + suggestion gate, AN-2 PrefixSpan core, PF-14
  routine store + plan/execute-through-executor runner.
* DEV-ONLY (never wired to runtime suggestions): AN-3/AN-4 bench
  helpers (``DEV_ONLY = True``; every public bench name starts
  with ``dev_``). ``suggestion()`` never calls them.
* NOT SHIPPED / NOT WIRED by this module: any platform watcher
  that enumerates real foreground windows (no such code exists
  here — the caller supplies observations), any voice output for
  suggestions (the caller decides whether a ``suggestion()``
  result is spoken, and must honour its ``None``), and any
  persistence of streams, predictions, or routines to disk.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

__all__ = [
    "SUGGESTION_CONFIDENCE_THRESHOLD",
    "MIN_EVIDENCE_SAMPLES",
    "DEV_ONLY",
    "DEFAULT_DENYLIST_PROCESSES",
    "DEFAULT_DENYLIST_TITLE_KEYWORDS",
    "ActivityEvent",
    "ActivityRefusedError",
    "ActivitySuppressedError",
    "ActivityCaptureSession",
    "is_denylisted",
    "Prediction",
    "NextActionPredictor",
    "FrequentPattern",
    "prefix_span",
    "mine_frequent_sequences",
    "WhenToSpeakCase",
    "dev_when_to_speak_dataset",
    "dev_score_when_to_speak_gate",
    "dev_score_proactivity",
    "Routine",
    "RoutinePlan",
    "RoutineResult",
    "RoutineStore",
    "RoutineRunner",
    "trigger_matches_text",
]

# INTENTIONAL (skills split B19): re-exports of the moved families.
# The old module's namespace carried these names; the facade
# re-exports the identical objects from their new modules.
from collections import Counter, defaultdict  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from .anticipation_gates import (
    SUGGESTION_CONFIDENCE_THRESHOLD,
    MIN_EVIDENCE_SAMPLES,
    DEV_ONLY,
    DEFAULT_DENYLIST_PROCESSES,
    DEFAULT_DENYLIST_TITLE_KEYWORDS,
    _normalise_process,
    is_denylisted,
)
from .anticipation_capture import (
    ActivityRefusedError,
    ActivitySuppressedError,
    ActivityEvent,
    ActivityCaptureSession,
)
from .anticipation_predictor import (
    Prediction,
    NextActionPredictor,
)
from .anticipation_mining import (
    FrequentPattern,
    _projected_database,
    prefix_span,
    mine_frequent_sequences,
)
from .anticipation_dev import (
    WhenToSpeakCase,
    dev_when_to_speak_dataset,
    dev_score_when_to_speak_gate,
    dev_score_proactivity,
)
from .anticipation_routines import (
    Routine,
    RoutineStore,
    RoutinePlan,
    RoutineResult,
    trigger_matches_text,
    RoutineRunner,
)
