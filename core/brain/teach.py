"""Wave 8 (v5.63) — Teach mode (A8 #1).

Demonstrate once -> she records, generalises, replays with
per-step verification.

HARD RULES, enforced by construction and tested in
test_v563_teach_secondopinion_feed.py:

* Replay executes ONLY through an injected executor callable.
  This module never calls a tool, a brain, or the registry
  directly, and it declares no new tool. The executor is the
  funnel: every replay step goes through it, and nothing else
  in this module can cause an effect.
* Every replay step is verified BEFORE the next step runs.
  A failed verification halts the replay at that step with a
  plain-words reason. There is no silent skip and no faked
  success: a halted replay reports status ``"halted"`` and the
  number of steps actually completed.
* Generalisation is conservative. A taught sequence replays
  only on inputs matching its recorded pattern: every
  ``{placeholder}`` in a step's arguments must be declared in
  ``pattern`` as a full-match regular expression, and every
  replay input for that placeholder must full-match it.
  Anything else is refused BEFORE any step executes, with a
  reason naming the offending input and the pattern it missed.
  Non-placeholder argument values are fixed: they replay
  exactly as recorded, never generalised.
* No new dependencies (stdlib only), no weights, no
  telemetry, no lifelog, no third-party material — so no
  third-party licence is involved in this module at all.

Versioned string introduced by this module (pre-pinned for
the 5.63.0 assembly; core/version.py is NOT touched here and
still reads 5.62.0 until the coordinator assembles the wave):
``VERSION = "5.63.0"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

VERSION = "5.63.0"

__all__ = [
    "VERSION",
    "TaughtStep",
    "TaughtSequence",
    "ReplayResult",
    "record",
    "verify_result",
    "substitute",
]


# ---------------------------------------------------------------------------
# Verification specs (serialisable dicts, so a taught sequence can be
# stored and reloaded without pickling callables).
# ---------------------------------------------------------------------------
#   {"kind": "ok"}
#       The executor result must be a dict with a truthy "ok" entry.
#   {"kind": "field_equals", "field": <name>, "value": <value>}
#       result[field] must equal value.
#   {"kind": "field_contains", "field": <name>, "substring": <text>}
#       result[field] must be a string containing substring.
# ---------------------------------------------------------------------------

_VERIFY_KINDS = ("ok", "field_equals", "field_contains")


def verify_result(spec, result):
    """Check one executor result against one verification spec.

    Returns ``(passed, reason)``; ``reason`` is "" when passed and a
    plain-words explanation otherwise. A non-dict result, an unknown
    spec kind, or a malformed spec all FAIL — never pass silently.
    """
    if not isinstance(spec, dict) or spec.get("kind") not in _VERIFY_KINDS:
        return False, "the step's verification check itself is malformed, so it cannot pass"
    if not isinstance(result, dict):
        return False, "the step did not return a usable result to check"
    kind = spec["kind"]
    if kind == "ok":
        if result.get("ok"):
            return True, ""
        return False, "the step did not report success"
    field_name = spec.get("field")
    if not isinstance(field_name, str) or not field_name:
        return False, "the step's verification check names no field to check"
    if field_name not in result:
        return False, f"the step's result has no field named {field_name!r} to check"
    value = result[field_name]
    if kind == "field_equals":
        if value == spec.get("value"):
            return True, ""
        return False, (
            f"the step's {field_name} was {value!r}, "
            f"not the expected {spec.get('value')!r}"
        )
    # field_contains
    substring = spec.get("substring")
    if not isinstance(value, str) or not isinstance(substring, str):
        return False, f"the step's {field_name} is not text that can be checked"
    if substring in value:
        return True, ""
    return False, f"the step's {field_name} did not contain {substring!r}"


_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _placeholders_in(value):
    """Placeholder names occurring in a step-argument value tree."""
    found = set()
    if isinstance(value, str):
        found.update(_PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.update(_placeholders_in(key))
            found.update(_placeholders_in(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_placeholders_in(item))
    return found


def _substitute_value(value, inputs):
    if isinstance(value, str):
        def _replace(match):
            name = match.group(1)
            return str(inputs[name]) if name in inputs else match.group(0)
        return _PLACEHOLDER_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {_substitute_value(k, inputs): _substitute_value(v, inputs)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_value(v, inputs) for v in value]
    if isinstance(value, tuple):
        return tuple(_substitute_value(v, inputs) for v in value)
    return value


def substitute(args, inputs):
    """Return ``args`` with every ``{placeholder}`` replaced from ``inputs``."""
    if not isinstance(args, dict):
        raise ValueError("step arguments must be a dict")
    return _substitute_value(args, inputs)


@dataclass(frozen=True)
class TaughtStep:
    """One recorded step: an existing tool name, its arguments, and
    the verification spec its executor result must satisfy."""

    tool: str
    args: dict = field(default_factory=dict)
    verify: dict = field(default_factory=lambda: {"kind": "ok"})

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("a taught step needs a non-empty tool name")
        if not isinstance(self.args, dict):
            raise ValueError("a taught step's arguments must be a dict")
        if not isinstance(self.verify, dict):
            raise ValueError("a taught step's verification spec must be a dict")

    def placeholders(self):
        return _placeholders_in(self.args)

    def instantiated(self, inputs):
        return TaughtStep(
            tool=self.tool,
            args=substitute(self.args, inputs),
            verify=dict(self.verify),
        )

    def to_dict(self):
        return {"tool": self.tool, "args": self.args, "verify": self.verify}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("a taught step record must be a dict")
        return cls(tool=data["tool"], args=dict(data.get("args") or {}),
                   verify=dict(data.get("verify") or {"kind": "ok"}))


@dataclass(frozen=True)
class TaughtSequence:
    """A recorded, conservatively generalised sequence.

    ``pattern`` maps each placeholder name used in the steps to a
    regular-expression string. Replay inputs must full-match their
    pattern; the pattern is deliberately tight (full-match, not
    search), so a near-miss input refuses instead of replaying
    against the wrong thing.
    """

    name: str
    steps: tuple
    pattern: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a taught sequence needs a non-empty name")
        if not self.steps:
            raise ValueError("a taught sequence needs at least one step")
        steps = tuple(
            s if isinstance(s, TaughtStep) else TaughtStep.from_dict(s)
            for s in self.steps
        )
        object.__setattr__(self, "steps", steps)
        if not isinstance(self.pattern, dict):
            raise ValueError("a taught sequence's pattern must be a dict")
        used = set()
        for step in steps:
            used.update(step.placeholders())
        declared = set(self.pattern)
        if used - declared:
            raise ValueError(
                "placeholders used but not declared in the pattern: "
                + ", ".join(sorted(used - declared))
            )
        if declared - used:
            raise ValueError(
                "pattern entries declared but never used in any step: "
                + ", ".join(sorted(declared - used))
            )
        for param, regex in self.pattern.items():
            try:
                re.compile(regex)
            except re.error as exc:
                raise ValueError(
                    f"pattern for {param!r} is not a valid pattern: {exc}"
                ) from exc

    def match_inputs(self, inputs):
        """Return ``(ok, reason)`` for a replay-input dict.

        ``ok`` is False (with a plain-words reason naming the input
        and the pattern) when an input is missing or fails its
        pattern. Extra inputs are ignored: they match nothing and
        change nothing.
        """
        if not isinstance(inputs, dict):
            return False, "replay inputs must be a dict of named values"
        for param, regex in self.pattern.items():
            if param not in inputs:
                return False, (
                    f"replay refused: input {param!r} is missing — "
                    f"this taught sequence only replays when every "
                    f"recorded input is supplied"
                )
            value = str(inputs[param])
            if re.fullmatch(regex, value) is None:
                return False, (
                    f"replay refused: input {param!r} was {value!r}, "
                    f"which does not match the recorded pattern "
                    f"{regex!r} — this sequence only replays on "
                    f"inputs matching what was demonstrated"
                )
        return True, ""

    def to_dict(self):
        return {
            "name": self.name,
            "pattern": dict(self.pattern),
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("a taught sequence record must be a dict")
        return cls(
            name=data["name"],
            steps=[TaughtStep.from_dict(s) for s in data.get("steps") or []],
            pattern=dict(data.get("pattern") or {}),
        )


def record(name, raw_steps, pattern=None):
    """Record a demonstration as a :class:`TaughtSequence`.

    ``raw_steps`` is a list of ``TaughtStep`` objects or of dicts
    with ``tool`` / ``args`` / ``verify`` keys. Recording performs
    no effects and calls no executor — it only stores what was
    demonstrated, plus the conservative pattern it may replay on.
    """
    steps = [
        s if isinstance(s, TaughtStep) else TaughtStep.from_dict(s)
        for s in raw_steps
    ]
    return TaughtSequence(name=name, steps=tuple(steps),
                          pattern=dict(pattern or {}))


@dataclass
class ReplayResult:
    """The outcome of one replay. ``status`` is exactly one of:

    * ``"completed"`` — every step ran and verified.
    * ``"halted"`` — a step failed or its verification failed;
      no later step ran. ``reason`` says which step and why.
    * ``"refused"`` — inputs did not match the recorded pattern
      (or a tool was unknown); no step ran at all.
    """

    status: str
    steps_completed: int
    reason: str = ""
    step_results: list = field(default_factory=list)

    @property
    def ok(self):
        return self.status == "completed"


def replay(sequence, executor, inputs, available_tools=None):
    """Replay ``sequence`` through the injected ``executor`` only.

    ``executor(tool, args)`` must return a dict (with an ``"ok"``
    entry for the default check). When ``available_tools`` is given,
    a step naming a tool outside it is refused before anything
    runs; the executor's own tool set is otherwise authoritative
    and its failure halts the replay honestly.

    Every step is verified before the next runs. A failed step
    halts the replay: later steps never execute, and the result
    never claims success for work that did not verify.
    """
    if not isinstance(sequence, TaughtSequence):
        raise ValueError("replay needs a TaughtSequence")
    if not callable(executor):
        raise ValueError("replay needs an injected executor callable")
    inputs = inputs or {}
    ok, reason = sequence.match_inputs(inputs)
    if not ok:
        return ReplayResult(status="refused", steps_completed=0, reason=reason)
    if available_tools is not None:
        known = set(available_tools)
        for index, step in enumerate(sequence.steps, start=1):
            if step.tool not in known:
                return ReplayResult(
                    status="refused", steps_completed=0,
                    reason=(
                        f"replay refused: step {index} names tool "
                        f"{step.tool!r}, which is not an existing tool "
                        f"this replay may use — taught sequences replay "
                        f"only through existing tools"
                    ),
                )
    results = []
    for index, step in enumerate(sequence.steps, start=1):
        concrete = step.instantiated(inputs)
        try:
            outcome = executor(concrete.tool, concrete.args)
        except Exception as exc:  # noqa: BLE001 - any executor failure halts
            return ReplayResult(
                status="halted", steps_completed=index - 1,
                reason=(
                    f"replay stopped at step {index} ({step.tool}): "
                    f"the step raised an error ({exc}) — no later step ran"
                ),
                step_results=results,
            )
        passed, why = verify_result(step.verify, outcome)
        results.append({"step": index, "tool": step.tool,
                        "verified": passed, "reason": why})
        if not passed:
            return ReplayResult(
                status="halted", steps_completed=index - 1,
                reason=(
                    f"replay stopped at step {index} ({step.tool}): "
                    f"{why} — no later step ran"
                ),
                step_results=results,
            )
    return ReplayResult(status="completed",
                        steps_completed=len(sequence.steps),
                        reason="", step_results=results)
