"""B-R1-4 (2026-11-09) adewaskar boot sequence — the staged,
honest startup ritual: VYUK wakes, checks its systems one by
one, and greets the user naming what is ready.

THE GAP (proved first, file/line evidence)
------------------------------------------
Today there is NO boot sequence. Launch is a cold window:
``main.py`` sets DPI awareness, builds the Qt app and shows the
window; ``gui/window_shell_frame.py:467`` fires a 700 ms timer
that opens the onboarding modal (Terms gate) when the
``onboarded`` setting is false; a 1200 ms timer runs the
deferred plugin-trust asks; a 1500 ms timer runs the B16
startup recovery check (it re-reads the ledger's interrupted
records and refreshes the Activity offer — never a dialog,
never an auto-run). Nothing checks the systems, nothing
reports their state, and nothing greets the user naming what
is ready. The briefing card (``gui/window_shell_briefing.py``)
fills a greeting + schedule AFTER the window is up, but that
is a card fill, not a startup ritual. No ``boot_sequence`` /
``BootSequence`` / ``startup sequence`` exists anywhere under
``core/`` or ``gui/`` (parent-verified by grep on the backup
tree).

THE PATTERN (adewaskar/jarvis, MIT — read from the repo
itself, 2026-11-09)
-----------------------------------------------
``adewaskar/jarvis`` (MIT License, Copyright (c) 2026 Aditya
Dewaskar — LICENSE file read from the repo + GitHub API
spdx_id MIT) is a browser voice assistant whose four-beat boot
in ``src/ui/Boot.tsx`` is the single best "premium moment" in
round 1: on ONE clock (elapsed ms since boot began,
``T = { rings: 2600, suit: 5200, reactor: 7200 }``),
  1. ``bar`` — an angular status bar, "INITIATING SYSTEM",
     over a scrolling boot log;
  2. ``rings`` — concentric reticle rings assembling inward
     until the assistant name resolves;
  3. ``suit`` — the suit schematic, a wireframe figure with
     component call-outs;
  4. ``reactor`` — the triangular core glow lighting from a
     dim outline to full glow.
It is React + Vite + Three.js + custom GLSL — NOT PySide6.
This module re-implements the PATTERN in Qt/Python: the four
beats as phases, the boot log as lines, the call-outs as
stage verdicts, the reactor as the greeting. No React, no
Three.js, no WebEngine embed, no Claude bridge (the brain is
a paid Claude Code subscription — VYUK takes only the
face/boot pattern, never the brain). No new dependency: the
pattern is a state machine + status reads, and the rendering
is the driver's job (this module returns the beats, the
verdicts and the greeting as data). Re-skinned to VYUK/Aura
blue by the driver, not here — this module carries no colour,
no widget, no window.

LOPHOLE FAMILY — "the boot lies, or the boot blocks"
----------------------------------------------------
* A system reported "online" that was never checked (the
  sequence claims readiness it never verified).
* A failed check silently skipped (the user never learns a
  system is down).
* A boot that blocks the assistant (the sequence must finish
  before VYUK can work — a slow check holds the whole
  product hostage).
* A boot that cannot be skipped (no way past it when it
  wedges).

ONE design closes the class: ONE boot sequence, a state
machine the driver ticks. Every stage is a real check — the
stage's verdict comes from the system's own status call
(``engine_status()`` / ``mic_status()`` / the resilience
verdict / the tutor's read-only summary), never from a
hardcoded "online". Every stage reports its verdict honestly
(ready / degraded / failed / skipped / unchecked, with the
reason); a stage that was never checked is "unchecked", never
"ready". The sequence runs one stage per ``advance()`` call,
so the driver ticks it from a timer or a worker thread and
the assistant stays usable while it runs — the boot never
blocks the first reply. ``skip()`` ends it cleanly and
records that it was skipped, never a wedge. The greeting
names only what was actually checked.

Rejected alternatives (recorded):
* A splash screen that claims readiness without checking (a
  lie — a stage with no check is "unchecked", never "ready").
* A silent skip of a failed check (the user never learns —
  a failed check is reported with its reason, and the
  greeting names it).
* A blocking boot (a slow check holds the product hostage —
  ``advance()`` is one stage per call; ``run_all()`` exists
  only for a non-UI driver and is documented as such).
* An unskippable boot (a wedge — ``skip()`` ends it cleanly
  and the report says so).
* Checking the mic by opening a stream (a side effect at
  boot — the mic stage reads a supplied ``mic_status()``
  dict, or reports "unchecked"; it never opens a stream).
* Reading the tutor by constructing a bare ``StudentModel()``
  (its default store is an EMPTY in-memory store — the stage
  injects the real ``memory_store`` singleton, as
  ``progress_report.py`` does).

STAGES (default list; each via its own status call)
---------------------------------------------------
* ``identity`` (rings beat) — config + the curated language
  table: ``preferred_language`` resolves, ``kokoro_voice`` is
  a non-empty voice name (a manual pick that differs from the
  language's curated voice is fine and is noted, not failed).
* ``mic`` (suit beat) — a driver-supplied ``mic_status()``
  dict (``state`` ok/quiet → ready; silent/no_stream →
  degraded, with the state as the reason); no supplied
  status → "unchecked" (degraded, "no mic session open yet").
* ``voice`` (suit beat) — ``kokoro_voice.engine_status()``
  (the isolated child probe, B-R1-3r): model ready → ready;
  engine present but no assets → degraded; engine absent →
  degraded (Piper/SAPI still speak). The probe is bounded
  at 5 s for the boot (``probe_engine(timeout_s=5)`` first —
  the verdict is cached per interpreter, so the later
  ``engine_status()`` call is free).
* ``vad`` (suit beat) — ``voice_activity.engine_status()``:
  Silero backend → ready; energy fallback → degraded (the
  reason names it); unavailable → degraded.
* ``turn_taking`` (suit beat) — ``turn_taking.engine_status()``:
  neural backend → ready; heuristic fallback → degraded;
  unavailable → degraded.
* ``secrets`` (suit beat) — ``keyring_store.engine_status()``:
  usable backend → ready; unavailable → degraded (the
  machine-key fallback still seals secrets).
* ``scheduler`` (suit beat) — ``job_scheduler.engine_status()``:
  available → ready; unavailable → degraded.
* ``network`` (suit beat) — driver-supplied probe results
  through the resilience ``connectivity()`` verdict
  ("online" → ready; "weak"/"portal"/"offline" → degraded,
  with the verdict as the reason); no supplied probes →
  "unchecked" (degraded, "no connectivity probe has run yet").
* ``calendar`` (suit beat) — a driver-supplied status dict
  (``connected`` true → ready, else degraded with its
  reason); no supplied status → "unchecked" (degraded,
  "calendar status not supplied").
* ``tutor`` (suit beat) — ``StudentModel(memory_store)``
  ``mastery_summary()`` (read-only, never moves mastery):
  reads → ready (the detail carries total/mastered; zero
  skills is a cold start, ready with the note "no skills
  recorded yet"); unreadable → failed.

VERDICT RULES (one rule for every stage)
-----------------------------------------
* ``ready`` — the check ran and the system is up.
* ``degraded`` — the check ran and the system is down or
  unavailable, but a fallback exists or it is non-critical
  (the reason names it); ALSO the verdict for a stage that
  could not be checked at all ("unchecked" — the reason
  names what is missing). A degraded stage is never reported
  as ready.
* ``failed`` — the check itself raised (the exception is the
  reason). A failed check never crashes the sequence and is
  never silently skipped.
* ``skipped`` — the stage was skipped by ``skip()``.
* ``unchecked`` — the stage was never run (pending). The
  report NEVER shows "ready" for an unchecked stage.

DEPENDENCY (proposed — parent rules)
------------------------------------
No new dependency. The pattern is re-implemented in-tree
(this module, stdlib only); every status call it uses is
already in the tree. The adewaskar repo is MIT (LICENSE file
read 2026-11-09: "MIT License, Copyright (c) 2026 Aditya
Dewaskar"; GitHub API spdx_id MIT) and only the PATTERN is
taken — no code, no assets, no bridge. Weight added: 0 bytes
of dependency. This module adds no pip package and no model.
"""

import datetime

__all__ = [
    "BootSequence",
    "run_boot",
    "default_stages",
    "BEATS",
    "VERDICTS",
    "VOICE_PROBE_TIMEOUT_S",
]

# The four adewaskar beats, in order (Boot.tsx: T = { rings:
# 2600, suit: 5200, reactor: 7200 } — the bar is the header
# that runs from the first millisecond).
BEATS = ("bar", "rings", "suit", "reactor")

VERDICTS = ("ready", "degraded", "failed", "skipped", "unchecked")

# The Kokoro probe is the only subprocess call in the sequence
# (a throwaway child import, ~0.3-1 s on first call, cached
# per interpreter after). Bounded at 5 s for the boot so one
# wedged child never holds a stage hostage.
VOICE_PROBE_TIMEOUT_S = 5.0


# -- verdict helpers -----------------------------------------------------
def _verdict(verdict, reason="", **detail):
    out = {"verdict": verdict, "reason": reason, "checked": True}
    out.update(detail)
    return out


def _unchecked(reason):
    """A stage that could not be checked: degraded, never ready,
    and the reason names what is missing."""
    return {"verdict": "degraded", "reason": reason, "checked": False}


def _failed(exc):
    """A check that raised: failed, with the exception as the
    reason. Never a crash, never silent."""
    return {"verdict": "failed",
            "reason": f"check failed: {type(exc).__name__}: {exc}",
            "checked": True}


def _from_engine(status, *, ready_when, degraded_reason=None):
    """Map an honest engine_status() dict to a boot verdict.
    ``ready_when(status)`` says when the engine counts as up;
    anything else that ran is degraded with the engine's own
    reason (a fallback exists for every engine here — voice
    falls back to Piper/SAPI, VAD to energy, turn-taking to
    the fixed silence timeout, secrets to the machine key)."""
    if not isinstance(status, dict):
        return _verdict("failed", "engine_status() returned no dict")
    if not status.get("ok", False):
        return _verdict("degraded", str(status.get("reason") or
                                        "engine unavailable"))
    if ready_when(status):
        return _verdict("ready", "")
    return _verdict("degraded", str(status.get("reason") or
                                    (degraded_reason or
                                     "engine degraded")))


def _greeting_for_hour(hour: int) -> str:
    """The time-of-day greeting, mirroring the onboarding
    ``_greeting()`` (gui/window_shell_onboarding.py:250):
    before noon → morning, before 17:00 → afternoon, else
    evening."""
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


# -- default stage checks (lazy imports: importing this module
#    never imports an engine) -------------------------------------------
def _check_identity():
    """The home language + voice resolve (config + the curated
    table). No engine import."""
    try:
        from ..config import config as _config
        from ..languages import language_by_code as _by_code
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        lang = str(getattr(_config, "preferred_language", "") or "")
        voice = str(getattr(_config, "kokoro_voice", "") or "")
        entry = _by_code(lang)
        if entry is None:
            return _verdict(
                "failed",
                f"home language {lang!r} is not in the curated table")
        if not voice.strip():
            return _verdict("failed", "home voice is empty")
        note = ""
        if voice != entry.voice:
            note = (f" (manual pick {voice!r}, not the curated "
                    f"{entry.voice!r} for {entry.code})")
        return _verdict(
            "ready", "",
            language=entry.code, voice=voice, note=note.strip())
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_mic(supplied):
    """The mic, from a driver-supplied mic_status() dict. No
    supplied status → unchecked: at boot no stream is open, so
    claiming "mic ready" from nothing would be a lie. This
    stage never opens a stream."""
    if supplied is None:
        return _unchecked(
            "no mic session open yet — the mic is checked when a "
            "stream opens")
    if not isinstance(supplied, dict):
        return _verdict("failed", "supplied mic status is not a dict")
    state = str(supplied.get("state") or "")
    device = supplied.get("device")
    if state in ("ok", "quiet"):
        return _verdict("ready", "", state=state, device=device)
    if state == "silent":
        return _verdict(
            "degraded",
            "mic is silent (privacy-blocked, muted, or wrong device)",
            state=state, device=device)
    if state == "no_stream":
        return _verdict(
            "degraded", "no mic stream open yet",
            state=state, device=device)
    return _verdict("degraded", f"mic state {state!r}", state=state,
                    device=device)


def _check_voice():
    """Kokoro, through the isolated child probe (B-R1-3r). The
    probe is bounded at 5 s and cached per interpreter, so the
    later engine_status() call is free."""
    try:
        from . import kokoro_isolate as _isolate
        from . import kokoro_voice as _voice
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        probe = _isolate.probe_engine(timeout_s=VOICE_PROBE_TIMEOUT_S)
        if not probe.get("available", False):
            return _verdict(
                "degraded",
                f"Kokoro engine unavailable: {probe.get('reason') or ''}"
                .strip())
        status = _voice.engine_status()
        return _from_engine(
            status,
            ready_when=lambda s: bool(s.get("model_ready", False)),
            degraded_reason="Kokoro assets not on disk")
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_vad():
    try:
        from . import voice_activity as _vad
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        return _from_engine(
            _vad.engine_status(),
            ready_when=lambda s: bool(s.get("silero_available", False)),
            degraded_reason="energy fallback (no Silero weights)")
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_turn_taking():
    try:
        from . import turn_taking as _tt
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        return _from_engine(
            _tt.engine_status(),
            ready_when=lambda s: str(s.get("backend") or "") ==
            # GAPS-16: the old string here named a backend nothing
            # emits, so this beat could never report ready — its
            # backends are "smartturn-onnx" (weights present) and
            # "heuristic-end-of-turn" (no weights). Ready now means
            # the neural backend is actually live; the heuristic
            # fallback reports degraded with its own honest reason.
            "smartturn-onnx",
            degraded_reason="heuristic fallback (no SmartTurn weights)")
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_secrets():
    try:
        from . import keyring_store as _ks
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        return _from_engine(
            _ks.engine_status(),
            ready_when=lambda s: bool(s.get("available", False)),
            degraded_reason="keyring unavailable")
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_scheduler():
    try:
        from . import job_scheduler as _js
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        return _from_engine(
            _js.engine_status(),
            ready_when=lambda s: bool(s.get("available", False)),
            degraded_reason="scheduler unavailable")
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_network(supplied):
    """Connectivity, from driver-supplied probe results through
    the resilience verdict. No supplied probes → unchecked:
    no evidence is not online (probe_truth's own rule)."""
    if supplied is None:
        return _unchecked("no connectivity probe has run yet")
    try:
        from ..resilience.resilience_connectivity import (
            probe_truth as _truth,
        )
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        results = supplied if isinstance(supplied, (list, tuple)) \
            else supplied.get("probes")
        if not results:
            return _unchecked("no connectivity probe has run yet")
        # probe_truth is the pure verdict function — no wiring
        # object (ResilienceWiring.__init__ makedirs its base_dir,
        # so constructing one here would be a side effect).
        verdict = _truth(list(results))
        if verdict == "online":
            return _verdict("ready", "", connectivity="online")
        return _verdict(
            "degraded", f"connectivity is {verdict}",
            connectivity=verdict)
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


def _check_calendar(supplied):
    """Calendar, from a driver-supplied status dict (the
    connector's ``status`` — this module never signs in, never
    opens a browser, never touches credentials). No supplied
    status → unchecked."""
    if supplied is None:
        return _unchecked("calendar status not supplied")
    if not isinstance(supplied, dict):
        return _verdict("failed", "supplied calendar status is not a dict")
    if supplied.get("connected") is True:
        return _verdict("ready", "", connected=True)
    return _verdict(
        "degraded",
        str(supplied.get("reason") or "calendar not connected"),
        connected=False)


def _check_tutor():
    """The tutor's student model, read-only (never moves
    mastery). The real store is injected — a bare
    StudentModel() would read an EMPTY in-memory store."""
    try:
        from ..brain.student_model import StudentModel as _Model
        from ..memory.memory import memory_store as _store
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)
    try:
        summary = _Model(store=_store).mastery_summary()
        total = int(summary.get("total", 0))
        mastered = int(summary.get("mastered_count", 0))
        if total == 0:
            return _verdict(
                "ready", "no skills recorded yet",
                total=0, mastered=0)
        return _verdict(
            "ready", "",
            total=total, mastered=mastered)
    except Exception as exc:  # noqa: BLE001 — the reason IS the report
        return _failed(exc)


# -- the default stage list ----------------------------------------------
def default_stages(supplies=None):
    """The ten default stages, in beat order. ``supplies`` is
    an optional dict of stage id → driver-supplied status
    (mic / network / calendar). A stage is a dict: id, label,
    beat, and a zero-arg ``check`` returning a verdict dict.
    A non-dict ``supplies`` is refused (ValueError) — the
    driver supplies statuses or nothing, never a string."""
    if supplies is not None and not isinstance(supplies, dict):
        raise ValueError("supplies must be a dict of stage id -> status")
    supplies = supplies or {}

    def _supplied(stage_id):
        return supplies.get(stage_id)

    return [
        {"id": "identity", "label": "Identity — home voice",
         "beat": "rings", "check": _check_identity},
        {"id": "mic", "label": "Microphone",
         "beat": "suit",
         "check": lambda: _check_mic(_supplied("mic"))},
        {"id": "voice", "label": "Voice — Kokoro",
         "beat": "suit", "check": _check_voice},
        {"id": "vad", "label": "Voice activity — Silero",
         "beat": "suit", "check": _check_vad},
        {"id": "turn_taking", "label": "Turn-taking — SmartTurn",
         "beat": "suit", "check": _check_turn_taking},
        {"id": "secrets", "label": "Secrets — keyring",
         "beat": "suit", "check": _check_secrets},
        {"id": "scheduler", "label": "Scheduler — timed jobs",
         "beat": "suit", "check": _check_scheduler},
        {"id": "network", "label": "Network",
         "beat": "suit",
         "check": lambda: _check_network(_supplied("network"))},
        {"id": "calendar", "label": "Calendar",
         "beat": "suit",
         "check": lambda: _check_calendar(_supplied("calendar"))},
        {"id": "tutor", "label": "Tutor — student model",
         "beat": "suit", "check": _check_tutor},
    ]


# -- the sequence ----------------------------------------------------------
class BootSequence:
    """One boot sequence: a state machine the driver ticks.

    ``advance()`` runs ONE stage and returns its verdict — the
    driver calls it from a timer or a worker thread, so the
    boot never blocks the first reply. ``run_all()`` runs every
    remaining stage in order (for a non-UI driver only).
    ``skip()`` ends the sequence cleanly and records that it
    was skipped. ``report()`` returns the full dict: the beats,
    every stage with its verdict, the boot log, and the
    greeting. A stage that was never run is "unchecked" in the
    report — never "ready".
    """

    def __init__(self, stages=None, supplies=None, *, name=None,
                 clock=None):
        self._name = name
        self._clock = clock or datetime.datetime.now
        self._stages = []
        for stage in (stages if stages is not None
                      else default_stages(supplies)):
            self._stages.append({
                "id": stage["id"],
                "label": stage["label"],
                "beat": stage.get("beat", "suit"),
                "check": stage["check"],
                "verdict": "unchecked",
                "reason": "",
                "checked": False,
                "detail": {},
            })
        self._index = 0
        self._skipped = False
        self._log = []
        try:
            from ..version import VERSION as _version
        except Exception:  # noqa: BLE001 — the version is a label
            _version = "unknown"
        self._version = _version
        self._log.append(
            f"INITIATING SYSTEM — VYUK {_version}")

    # -- state ------------------------------------------------------------
    @property
    def complete(self) -> bool:
        return self._skipped or self._index >= len(self._stages)

    @property
    def skipped(self) -> bool:
        return self._skipped

    @property
    def pending(self) -> int:
        return 0 if self._skipped else len(self._stages) - self._index

    # -- the run ------------------------------------------------------------
    def advance(self):
        """Run the next pending stage and return its verdict
        dict (id, label, beat, verdict, reason, checked, detail).
        Returns None when the sequence is complete or skipped.
        A stage that raises is reported as failed, never a
        crash."""
        if self.complete:
            return None
        stage = self._stages[self._index]
        self._index += 1
        try:
            result = stage["check"]()
        except Exception as exc:  # noqa: BLE001 — the reason IS the report
            result = _failed(exc)
        if not isinstance(result, dict) or "verdict" not in result:
            result = _verdict(
                "failed", "check returned no verdict dict")
        verdict = result.get("verdict")
        if verdict not in VERDICTS:
            verdict = "failed"
            result = _verdict(
                "failed", f"check returned unknown verdict {verdict!r}")
        stage["verdict"] = verdict
        stage["reason"] = str(result.get("reason") or "")
        stage["checked"] = bool(result.get("checked", True))
        stage["detail"] = {k: v for k, v in result.items()
                           if k not in ("verdict", "reason", "checked")}
        line = f"[{stage['id']}] {verdict}"
        if stage["reason"]:
            line += f" — {stage['reason']}"
        self._log.append(line)
        return {
            "id": stage["id"],
            "label": stage["label"],
            "beat": stage["beat"],
            "verdict": verdict,
            "reason": stage["reason"],
            "checked": stage["checked"],
            "detail": stage["detail"],
        }

    def run_all(self):
        """Run every remaining stage in order and return the
        report. For a non-UI driver only: the UI driver should
        call ``advance()`` per tick so the boot never blocks the
        first reply."""
        while not self.complete:
            self.advance()
        return self.report()

    def skip(self):
        """End the sequence cleanly: every remaining stage is
        marked skipped, the report records that the boot was
        skipped, and the greeting names only what was checked
        before the skip. Returns the report."""
        for stage in self._stages[self._index:]:
            stage["verdict"] = "skipped"
            stage["reason"] = "boot skipped"
            stage["checked"] = False
            stage["detail"] = {}
            self._log.append(f"[{stage['id']}] skipped — boot skipped")
        self._index = len(self._stages)
        self._skipped = True
        return self.report()

    # -- the report ---------------------------------------------------------
    def greeting(self) -> str:
        """The reactor beat: the greeting, naming only what was
        actually checked. Never claims a system is ready that
        was not checked or that failed/degraded."""
        hour = self._clock().hour
        head = _greeting_for_hour(hour)
        if self._name:
            head += f", {self._name}"
        ready = [s["label"] for s in self._stages
                 if s["verdict"] == "ready"]
        down = [(s["label"], s["reason"]) for s in self._stages
                if s["verdict"] in ("degraded", "failed")]
        unchecked = [s["label"] for s in self._stages
                     if s["verdict"] == "unchecked"]
        if self._skipped:
            checked = [s["label"] for s in self._stages
                       if s["checked"]]
            if checked:
                tail = f"boot skipped — checked: {', '.join(checked)}"
            else:
                tail = "boot skipped before any check"
            return f"{head} — {tail}."
        if not any(s["checked"] for s in self._stages):
            return f"{head} — boot has not run yet."
        if ready and not down and not unchecked:
            return f"{head} — all systems ready."
        parts = []
        if ready:
            parts.append(f"{', '.join(ready)} {'is' if len(ready) == 1 else 'are'} ready")
        for label, reason in down:
            parts.append(f"{label} is down"
                         + (f" ({reason})" if reason else ""))
        if unchecked:
            parts.append(
                f"{', '.join(unchecked)} {'was' if len(unchecked) == 1 else 'were'} not checked")
        return f"{head} — {'; '.join(parts)}."

    def report(self) -> dict:
        """The full boot report: beats, every stage with its
        verdict, the boot log, the greeting, and whether the
        sequence is complete / skipped / all-ready."""
        stages = [{
            "id": s["id"],
            "label": s["label"],
            "beat": s["beat"],
            "verdict": s["verdict"],
            "reason": s["reason"],
            "checked": s["checked"],
            "detail": s["detail"],
        } for s in self._stages]
        return {
            "ok": True,
            "complete": self.complete,
            "skipped": self._skipped,
            "all_ready": bool(self._stages) and all(
                s["verdict"] == "ready" for s in self._stages),
            "beats": list(BEATS),
            "version": self._version,
            "stages": stages,
            "log": list(self._log),
            "greeting": self.greeting(),
        }


def run_boot(supplies=None, *, name=None, clock=None) -> dict:
    """Run the whole boot sequence (all ten default stages) and
    return the report. For a non-UI driver only: the UI driver
    should construct ``BootSequence`` and call ``advance()``
    per tick so the boot never blocks the first reply."""
    return BootSequence(supplies=supplies, name=name,
                        clock=clock).run_all()
