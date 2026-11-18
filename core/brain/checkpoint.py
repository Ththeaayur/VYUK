"""Wave 8 (v5.63) — run_goal checkpoint loop, built to the Wave 1 TC-1 spec.

Source spec: ``TC1_RUN_GOAL_CHECKPOINT_SPEC.md`` in the tree root
(Wave 1, STUDY -> SPEC). This module implements the spec's semantics
in own code (stdlib only). It adopts NO framework: the D-6 framework
pick is still the owner's, and MAF's licence is OWED (O-11) — MAF is
never a dependency until cleared. No framework is imported here.

The ten non-negotiables, as implemented:

1.  Checkpoint-per-step, AFTER — never before. A record is written
    only after a tool execution completes (or after a human decision
    lands). Nothing is checkpointed for a step that has not run.
2.  Never silently re-run a completed world-change. On resume,
    completed steps are replayed from the checkpoint; the executor
    is never called for them again.
3.  Crash mid-tool = UNKNOWN, never assumed. A step interrupted
    mid-execution resumes as ``unknown`` and the HUMAN decides
    (re-run / skip / abort). No auto-retry ever decides this.
4.  The funnel is the only door (INV-2). Tools execute ONLY via
    the injected executor callable. This module never executes a
    tool itself, never imports a tool registry, and never calls
    any tool function directly. Any design executing outside the
    funnel fails by definition — the executor seam is the funnel
    seam, and the coordinator wires the real ``ToolRegistry.execute``
    (or its funnel equivalent) into it at assembly time.
5.  The gate becomes a durable interrupt, not a blocking popup.
    A gated step pauses the run, persists the checkpoint, and the
    loop returns (the process may exit); ``resume()`` delivers the
    human's decision into the exact paused step. Epoch-void
    semantics survive: an approval recorded under a dead session
    epoch is void on resume, and the checkpoint carries the epoch.
6.  Checkpointer vs store, split, never merged. This module holds
    run progress ONLY (steps, results, approvals, budgets,
    verdicts). It never touches long-term memory; the memory store
    (INV-8, 6,000-char cap) is a separate object the coordinator
    owns. Completing a run deletes its checkpoints and leaves
    memories untouched.
7.  Rule of Two travels with the run (SE-3.1). The session legs
    exercised so far are part of every checkpoint record; a resumed
    run keeps its legs — they do not reset on restart.
8.  Redaction travels with the checkpoint. Arguments and results
    pass through :func:`redact_for_checkpoint` before persisting
    (same posture as the audit redaction: secret-shaped fields are
    refused/replaced, never stored). A secret-shaped argument is
    refused by the checkpoint writer, mirroring the protected-path
    check. The coordinator replaces this standalone redactor with
    the real audit redactor at assembly if the field lists differ.
9.  Atomic writes, last-known-good fallback. Each checkpoint write
    rewrites the run file via tmp file + ``os.replace`` and keeps
    the previous good snapshot. A torn/corrupt newest snapshot
    falls back to the last good one and reports the gap.
10. Budgets and verdicts are checkpointed, not recomputed. Step
    budget, repair budget, and expect-verdicts persist with the
    step; resume continues the same counters, never a fresh
    allowance per restart.

Wiring seam (coordinator): construct ``CheckpointRun`` with
``executor=`` (the funnel callable, signature
``executor(step: dict) -> dict`` returning ``{"ok": bool,
"result": ..., "legs": [...], "verdict": ...}``), ``store_dir=``
(a runtime directory — tests use tmp dirs; production checkpoint
state lives under ``data/`` as runtime state, never shipped),
``epoch=``, and budgets. The loop itself is framework-free.

Versioned strings introduced by this module (pre-pinned to 5.63):
``WAVE_VERSION = "5.63"`` (module marker only — NOT the product
version; ``core/version.py`` stays 5.62.0 until coordinator assembly).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

WAVE_VERSION = "5.63"

# Statuses from the TC-1 checkpoint record shape.
STATUS_COMPLETED = "completed"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_UNKNOWN = "unknown"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

# Fields whose values are never persisted in a checkpoint (mirrors the
# audit redaction posture: secrets are refused, never stored).
_REDACTED_KEYS = (
    "password", "passwd", "secret", "api_key", "apikey", "token",
    "auth", "authorization", "private_key", "credential",
)
_REDACTED_VALUE = "[REDACTED]"


class CheckpointRefusal(Exception):
    """A checkpoint write was refused (secret-shaped argument)."""


def redact_for_checkpoint(value: Any) -> Any:
    """Redact secret-shaped fields from args/results before persisting.

    Dicts are walked recursively; a key matching a redacted name has
    its value replaced. Strings that look like bearer tokens
    (``Bearer <...>``) are replaced wholesale. Everything else passes
    through unchanged. Returns the redacted copy; input is untouched.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _REDACTED_KEYS:
                out[key] = _REDACTED_VALUE
            else:
                out[key] = redact_for_checkpoint(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_for_checkpoint(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return _REDACTED_VALUE
    return value


def _contains_secret(value: Any) -> bool:
    """True if any redacted key is present with a non-empty value."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _REDACTED_KEYS \
                    and item not in (None, "", _REDACTED_VALUE):
                return True
            if _contains_secret(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


@dataclass
class CheckpointRecord:
    """One TC-1 checkpoint record, per step, appended in order."""

    run_id: str
    step_index: int
    epoch: str
    step: dict  # tool name + redacted arguments + expect text
    status: str
    result: Any = None
    legs: list = field(default_factory=list)
    approval: Optional[dict] = None  # gate req id + decision + when
    budgets: dict = field(default_factory=dict)  # step/repair after step
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointRecord":
        return cls(
            run_id=data["run_id"],
            step_index=data["step_index"],
            epoch=data["epoch"],
            step=data["step"],
            status=data["status"],
            result=data.get("result"),
            legs=list(data.get("legs") or []),
            approval=data.get("approval"),
            budgets=dict(data.get("budgets") or {}),
            created_at=data.get("created_at", ""),
        )


class CheckpointStore:
    """Atomic per-run checkpoint file with last-good fallback.

    Layout per run: ``<store_dir>/<run_id>.json`` (current snapshot)
    plus ``<store_dir>/<run_id>.prev.json`` (previous good snapshot).
    Every write rewrites the whole snapshot via tmp + os.replace,
    then promotes the old current snapshot to ``.prev``. A corrupt
    current snapshot loads ``.prev`` instead and reports the gap
    (steps present in neither, or the step-index gap) — never a
    half-written state.
    """

    def __init__(self, store_dir: str | Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in run_id)
        return (self.store_dir / f"{safe}.json",
                self.store_dir / f"{safe}.prev.json")

    def write(self, run_id: str, records: list[CheckpointRecord]) -> None:
        current, prev = self._paths(run_id)
        payload = json.dumps([r.to_dict() for r in records],
                             sort_keys=True, indent=1)
        fd, tmp = tempfile.mkstemp(dir=str(self.store_dir),
                                   prefix=".ckpt-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if current.exists():
                os.replace(current, prev)
            os.replace(tmp, current)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, run_id: str) -> tuple[list[CheckpointRecord], dict]:
        """Load a run's records. Returns (records, report).

        ``report`` carries ``fell_back`` (bool) and ``gap`` (str):
        on a corrupt current snapshot the last-good snapshot is
        returned with the gap described. On a clean load both are
        empty/false. Missing run -> ([], report with ``missing``).
        """
        current, prev = self._paths(run_id)
        for path, fell_back in ((current, False), (prev, True)):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records = [CheckpointRecord.from_dict(d) for d in data]
                records.sort(key=lambda r: r.step_index)
                return records, {
                    "fell_back": fell_back,
                    "gap": ("newest snapshot corrupt; fell back to "
                            "previous good snapshot" if fell_back else ""),
                    "missing": False,
                }
            except (ValueError, KeyError, TypeError):
                continue
        return [], {"fell_back": False, "gap": "", "missing": True}

    def delete(self, run_id: str) -> None:
        """Delete a completed run's checkpoints (memories untouched —
        this store never holds memories, per TC-1 non-negotiable 6)."""
        current, prev = self._paths(run_id)
        for path in (current, prev):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


class CheckpointRun:
    """A resumable run_goal loop over an injected executor (the funnel).

    ``plan`` is a list of step dicts: ``{"tool": name, "args": {...},
    "expect": text-or-None, "confirm": bool}``. ``confirm=True``
    steps pause for a durable human decision instead of executing.

    ``run()`` executes from the start (or from a checkpoint when
    ``resume=True``). It returns a run outcome dict; when it pauses
    for approval the outcome is ``{"state": "paused", ...}`` and the
    process may exit — ``resume(decision, epoch)`` continues from
    the exact paused step. ``mark_crashed(step_index)`` records the
    UNKNOWN state for a step interrupted mid-tool; ``resume()``
    refuses to proceed past an unknown step until the human decides
    re-run / skip / abort for it.
    """

    def __init__(self, run_id: str, plan: list[dict],
                 executor: Callable[[dict], dict],
                 store: CheckpointStore, epoch: str,
                 step_budget: int = 50, repair_budget: int = 5):
        self.run_id = run_id
        self.plan = plan
        self.executor = executor
        self.store = store
        self.epoch = epoch
        self.step_budget = step_budget
        self.repair_budget = repair_budget
        self.records: list[CheckpointRecord] = []
        self.legs: list[str] = []
        self._paused_index: Optional[int] = None

    # -- internals ---------------------------------------------------
    def _budgets(self) -> dict:
        return {"step_budget": self.step_budget,
                "repair_budget": self.repair_budget}

    def _record(self, step_index: int, status: str, result: Any = None,
                approval: Optional[dict] = None) -> CheckpointRecord:
        step = self.plan[step_index]
        if _contains_secret(step.get("args") or {}):
            raise CheckpointRefusal(
                f"step {step_index} carries a secret-shaped argument; "
                "checkpoint refused (never persist secrets)")
        record = CheckpointRecord(
            run_id=self.run_id, step_index=step_index, epoch=self.epoch,
            step={"tool": step.get("tool"),
                  "args": redact_for_checkpoint(step.get("args") or {}),
                  "expect": step.get("expect")},
            status=status, result=redact_for_checkpoint(result),
            legs=list(self.legs), approval=approval,
            budgets=self._budgets())
        self.records.append(record)
        self.store.write(self.run_id, self.records)
        return record

    def _execute_step(self, step_index: int) -> dict:
        """Execute exactly one step through the funnel, then checkpoint
        AFTER it completes (TC-1 non-negotiable 1)."""
        step = self.plan[step_index]
        outcome = self.executor(step)
        self.legs.extend(outcome.get("legs") or [])
        if outcome.get("ok"):
            self._record(step_index, STATUS_COMPLETED,
                         result=outcome.get("result"),
                         approval=None)
            # Attach the expect-verdict as part of the record's result
            # envelope when the executor supplies one.
            if outcome.get("verdict") is not None:
                self.records[-1].result = {
                    "result": self.records[-1].result,
                    "verdict": outcome["verdict"],
                }
                self.store.write(self.run_id, self.records)
            return {"state": "step_done", "step_index": step_index}
        self.repair_budget -= 1
        self._record(step_index, STATUS_FAILED,
                     result={"error": outcome.get("error", "tool error")})
        return {"state": "step_failed", "step_index": step_index,
                "error": outcome.get("error", "tool error")}

    # -- public loop -------------------------------------------------
    def run(self, resume: bool = False) -> dict:
        """Run (or resume) the plan. Never re-executes a completed step."""
        if resume:
            records, report = self.store.load(self.run_id)
            self.records = records
            done = {r.step_index for r in records
                    if r.status == STATUS_COMPLETED}
            unknown = [r for r in records if r.status == STATUS_UNKNOWN]
            if unknown:
                return {"state": "needs_human_unknown",
                        "step_index": unknown[0].step_index,
                        "gap": report.get("gap", ""),
                        "detail": "step outcome unknown (crashed mid-tool); "
                                  "human must choose re-run, skip, or abort"}
            paused = [r for r in records
                      if r.status == STATUS_PENDING_APPROVAL]
            if paused:
                self._paused_index = paused[0].step_index
                return {"state": "paused",
                        "step_index": paused[0].step_index,
                        "gap": report.get("gap", "")}
            # Budgets continue from the last checkpoint, never reset.
            if records:
                last = records[-1].budgets
                self.step_budget = last.get("step_budget", self.step_budget)
                self.repair_budget = last.get("repair_budget",
                                              self.repair_budget)
                self.legs = list(records[-1].legs)
            start = (max(done) + 1) if done else 0
        else:
            done, start = set(), 0
        for index in range(start, len(self.plan)):
            step = self.plan[index]
            if step.get("confirm"):
                # Pausing consumes no step budget: the step has not
                # run yet, and resume must not double-charge it.
                self._record(index, STATUS_PENDING_APPROVAL)
                self._paused_index = index
                return {"state": "paused", "step_index": index}
            if self.step_budget <= 0:
                return {"state": "budget_exhausted", "step_index": index}
            self.step_budget -= 1
            outcome = self._execute_step(index)
            if outcome["state"] == "step_failed" and self.repair_budget < 0:
                return {"state": "failed", "step_index": index,
                        "error": outcome.get("error")}
        self.store.delete(self.run_id)
        return {"state": "complete", "steps": len(self.plan),
                "legs": list(self.legs)}

    def mark_crashed(self, step_index: int) -> CheckpointRecord:
        """Record a step interrupted mid-tool as UNKNOWN (never assumed).

        Called by the coordinator when a run restarts after a crash
        with a step in flight. The step is NOT re-executed here.
        """
        return self._record(step_index, STATUS_UNKNOWN,
                            result={"error": "crashed mid-tool; outcome "
                                             "unknown"})

    def request_gate(self, step_index: int, gate_request_id: str) -> dict:
        """Pause at a gated step with a durable interrupt record."""
        self._record(step_index, STATUS_PENDING_APPROVAL,
                     approval={"gate_request_id": gate_request_id,
                               "decision": None})
        self._paused_index = step_index
        return {"state": "paused", "step_index": step_index,
                "gate_request_id": gate_request_id}

    def resume(self, decision: str, epoch: str,
               unknown_decision: Optional[str] = None) -> dict:
        """Deliver a human decision into a paused (or unknown) step.

        Epoch-void: an approval recorded under a dead session epoch
        is void — a resume under a different epoch than the run's
        epoch is refused. ``decision`` for a paused gate step is
        ``"approve"`` or ``"deny"``; for an unknown step,
        ``unknown_decision`` is ``"rerun"``, ``"skip"``, or ``"abort"``.
        """
        if epoch != self.epoch:
            return {"state": "epoch_void",
                    "detail": "approval recorded under a dead session "
                              "epoch is void on resume"}
        records, _ = self.store.load(self.run_id)
        self.records = records
        unknown = [r for r in records if r.status == STATUS_UNKNOWN]
        if unknown:
            idx = unknown[0].step_index
            if unknown_decision == "abort":
                self.store.delete(self.run_id)
                return {"state": "aborted", "step_index": idx}
            if unknown_decision == "skip":
                self._record(idx, STATUS_SKIPPED,
                             result={"skipped": "human decision"})
                return self._continue_after(idx)
            if unknown_decision == "rerun":
                # Human explicitly chose re-run: the ONLY path that
                # re-executes a step whose outcome was unknown.
                self.records = [r for r in records
                                if r.step_index != idx]
                return self._continue_from(idx)
            return {"state": "needs_human_unknown", "step_index": idx,
                    "detail": "choose rerun, skip, or abort"}
        paused = [r for r in records
                  if r.status == STATUS_PENDING_APPROVAL]
        if not paused:
            return {"state": "not_paused"}
        idx = paused[0].step_index
        if decision == "deny":
            self._record(idx, STATUS_SKIPPED,
                         result={"denied": "human decision"})
            return self._continue_after(idx)
        if decision != "approve":
            return {"state": "not_paused",
                    "detail": "decision must be approve or deny"}
        # Stamp the approval onto the paused record, then execute.
        for record in self.records:
            if record.step_index == idx:
                record.approval = {"gate_request_id":
                                   (record.approval or {}).get(
                                       "gate_request_id"),
                                   "decision": "approve"}
        self.store.write(self.run_id, self.records)
        return self._continue_from(idx)

    def _continue_from(self, index: int) -> dict:
        """Continue executing at ``index`` (the paused/approved step)."""
        # Rebuild the run state from the checkpoint, then run forward.
        done = {r.step_index for r in self.records
                if r.status == STATUS_COMPLETED}
        if self.records:
            last = self.records[-1]
            self.legs = list(last.legs)
        for step_index in range(index, len(self.plan)):
            if step_index in done:
                continue
            if self.step_budget <= 0:
                return {"state": "budget_exhausted",
                        "step_index": step_index}
            self.step_budget -= 1
            outcome = self._execute_step(step_index)
            if outcome["state"] == "step_failed" \
                    and self.repair_budget < 0:
                return {"state": "failed", "step_index": step_index}
        self.store.delete(self.run_id)
        return {"state": "complete", "steps": len(self.plan)}

    def _continue_after(self, index: int) -> dict:
        done = {r.step_index for r in self.records
                if r.status == STATUS_COMPLETED}
        for step_index in range(index + 1, len(self.plan)):
            if step_index in done:
                continue
            if self.step_budget <= 0:
                return {"state": "budget_exhausted",
                        "step_index": step_index}
            self.step_budget -= 1
            outcome = self._execute_step(step_index)
            if outcome["state"] == "step_failed" \
                    and self.repair_budget < 0:
                return {"state": "failed", "step_index": step_index}
        self.store.delete(self.run_id)
        return {"state": "complete", "steps": len(self.plan)}
