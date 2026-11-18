"""Split from core/anticipation.py (skills split B19).
Move only: code below is verbatim from the old module apart from
module-level import lines.
"""

from __future__ import annotations


from dataclasses import dataclass, field



# ---------------------------------------------------------------------------
# PF-14 user-defined routines
# ---------------------------------------------------------------------------


@dataclass
class Routine:
    """A user-defined routine: a plain-language ``trigger`` plus
    plain-language ``actions`` (steps, in order).

    The trigger is stored verbatim; this module does NOT evaluate
    triggers semantically (see trigger_matches_text for the only,
    deliberately literal, helper). Actions are descriptions, not
    code — executing them is the injected executor's job.
    """
    name: str
    trigger: str
    actions: list = field(default_factory=list)

    def __post_init__(self):
        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValueError("routine name must not be empty")
        self.trigger = str(self.trigger or "").strip()
        if not self.trigger:
            raise ValueError("routine trigger must not be empty")
        self.actions = [str(a).strip() for a in (self.actions or [])]
        self.actions = [a for a in self.actions if a]
        if not self.actions:
            raise ValueError("routine must have at least one action")


class RoutineStore:
    """In-memory routine store (editable, deletable).

    No file I/O: persistence, if the product ever wants it, is the
    caller's job (a store that silently wrote to the install tree
    would violate this wave's file boundaries). Names are unique;
    creating a duplicate name raises ValueError.
    """

    def __init__(self):
        self._routines: dict = {}

    def create(self, routine: Routine) -> Routine:
        if routine.name in self._routines:
            raise ValueError(f"routine {routine.name!r} already exists")
        self._routines[routine.name] = routine
        return routine

    def get(self, name):
        return self._routines.get(str(name or "").strip())

    def update(self, name, *, trigger=None, actions=None) -> Routine:
        """Edit a routine in place (trigger and/or actions)."""
        routine = self.get(name)
        if routine is None:
            raise KeyError(f"no routine named {name!r}")
        if trigger is not None:
            trigger = str(trigger).strip()
            if not trigger:
                raise ValueError("routine trigger must not be empty")
            routine.trigger = trigger
        if actions is not None:
            cleaned = [str(a).strip() for a in actions]
            cleaned = [a for a in cleaned if a]
            if not cleaned:
                raise ValueError("routine must have at least one action")
            routine.actions = cleaned
        return routine

    def delete(self, name) -> bool:
        """Delete a routine; True when one was removed."""
        return self._routines.pop(str(name or "").strip(), None) is not None

    def list_routines(self) -> list:
        return [self._routines[k] for k in sorted(self._routines)]


@dataclass(frozen=True)
class RoutinePlan:
    """A planned (not executed) routine run."""
    routine_name: str
    steps: tuple
    executor_required: bool = True


@dataclass
class RoutineResult:
    """The outcome of plan() / run().

    ``status`` is one of: "plan-only" (no executor — nothing ran),
    "completed" (every step routed through the executor OK), or
    "failed" (a step failed — see failed_step_index / failed_step
    / error; execution stopped at that step). ``ok`` is True only
    for "completed". A failed routine always names WHICH step
    failed, by index and by its plain-language text.
    """
    routine_name: str
    status: str
    executed_steps: list = field(default_factory=list)
    planned_steps: list = field(default_factory=list)
    failed_step_index: int | None = None
    failed_step: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def trigger_matches_text(routine: Routine, text) -> bool:
    """Literal trigger helper ONLY: True when the routine's trigger
    text appears (case-insensitive) in ``text``. This is substring
    matching, not semantic trigger evaluation — a caller that needs
    time-based or semantic triggers must implement that itself and
    must not claim this helper does it."""
    if routine is None or text is None:
        return False
    return routine.trigger.lower() in str(text).lower()


class RoutineRunner:
    """Runs routines ONLY through an injected executor.

    ``executor(step_text, routine_name)`` is the funnel: in
    production it is the tool funnel that owns the human confirm
    gate, so a routine can never bypass confirmation — this runner
    has no direct execution path at all (no shell, no tool call,
    no GUI action exists in this module). The executor signals a
    step failure by returning False or by raising; either way the
    runner stops at that step and names it in the result.
    """

    def __init__(self, executor=None):
        self._executor = executor

    def plan(self, routine: Routine) -> RoutinePlan:
        """Plan a routine: the steps, in order, never executed."""
        return RoutinePlan(routine_name=routine.name,
                           steps=tuple(routine.actions),
                           executor_required=True)

    def run(self, routine: Routine) -> RoutineResult:
        """Run a routine through the injected executor — or plan
        only when there is none.

        WITHOUT an executor this method REFUSES direct execution:
        it returns a "plan-only" result with zero executed steps
        and never attempts any step. WITH an executor every step
        is routed through it, in order, stopping at the first
        failure and naming that step.
        """
        planned = list(routine.actions)
        if self._executor is None:
            return RoutineResult(
                routine_name=routine.name,
                status="plan-only",
                executed_steps=[],
                planned_steps=planned,
                error=("refused — no executor injected; routine planned "
                       "only and nothing was executed (routines never "
                       "execute directly and never bypass the confirm gate)"),
            )
        executed = []
        for index, step in enumerate(planned):
            try:
                outcome = self._executor(step, routine.name)
            except Exception as exc:  # noqa: BLE001 — executor failure IS the report
                return RoutineResult(
                    routine_name=routine.name,
                    status="failed",
                    executed_steps=executed,
                    planned_steps=planned,
                    failed_step_index=index,
                    failed_step=step,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if outcome is False:
                return RoutineResult(
                    routine_name=routine.name,
                    status="failed",
                    executed_steps=executed,
                    planned_steps=planned,
                    failed_step_index=index,
                    failed_step=step,
                    error="executor declined step (returned False)",
                )
            executed.append(step)
        return RoutineResult(
            routine_name=routine.name,
            status="completed",
            executed_steps=executed,
            planned_steps=planned,
        )
