"""Wave 4 MISS breadth — mission contracts, dry runs, evidence binders.

The delta on the Wave 1/2 loop, never a second loop. Stdlib only,
no real actions are performed here: a dry run simulates steps
against a contract and records what would be checked, and an
evidence binder is an append-only log of checks and outcomes.

Rules, enforced in code:
  * A mission contract names its goal, preconditions, postconditions,
    and checkpoints. A step that violates a precondition is refused
    in the dry run, never waved through.
  * A dry run changes nothing: it returns a plan of checks, a risk
    map, and a recovery preview, and records nothing as done.
  * An evidence binder is append-only: entries are never edited in
    place, and a binder entry that claims a check passed must name
    the check. Unnamed "success" is not evidence.
  * Risk map entries are caller-supplied risk names with a severity
    in {"low", "medium", "high"}; anything else is invalid, never
    coerced.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MissionContract:
    goal: str
    preconditions: tuple = ()
    postconditions: tuple = ()
    checkpoints: tuple = ()


@dataclass
class EvidenceBinder:
    """Append-only evidence log for one mission."""

    entries: list = field(default_factory=list)

    def record(self, check: str, outcome: str, detail: str = "") -> dict:
        if not check:
            return {"ok": False,
                    "reason": "evidence entry must name its check"}
        entry = {"check": check, "outcome": outcome, "detail": detail}
        self.entries.append(entry)
        return {"ok": True, "entry": entry}

    def summary(self) -> dict:
        passed = sum(1 for e in self.entries if e["outcome"] == "pass")
        failed = sum(1 for e in self.entries if e["outcome"] == "fail")
        return {"total": len(self.entries), "passed": passed, "failed": failed,
                "entries": list(self.entries)}


def dry_run(contract: MissionContract, steps: list,
            *, risks: dict | None = None) -> dict:
    """Simulate a mission against its contract. Changes nothing.

    Each step is a dict {name, requires: [...], ensures: [...]}.
    A step whose requirements are not in the contract's preconditions
    (or ensured by an earlier step) is flagged, not executed. The
    recovery preview lists, for each flagged step, that it must be
    re-planned before any real run.
    """
    risks = risks or {}
    for name, severity in risks.items():
        if severity not in ("low", "medium", "high"):
            return {"ok": False, "reason": f"risk '{name}' has invalid "
                    f"severity '{severity}' (must be low/medium/high)"}
    known = set(contract.preconditions)
    planned = []
    flagged = []
    for step in steps:
        if not isinstance(step, dict) or not step.get("name"):
            flagged.append({"step": None, "reason": "step has no name"})
            continue
        missing = [r for r in step.get("requires", []) if r not in known]
        if missing:
            flagged.append({"step": step["name"],
                            "reason": f"missing preconditions: {missing}"})
            continue
        planned.append(step["name"])
        known.update(step.get("ensures", []))
    missing_post = [p for p in contract.postconditions if p not in known]
    recovery_preview = [f"re-plan step '{f['step']}': {f['reason']}"
                        for f in flagged]
    if missing_post:
        recovery_preview.append(
            f"postconditions not yet ensured: {missing_post}")
    return {"ok": not flagged and not missing_post,
            "planned": planned, "flagged": flagged,
            "risk_map": dict(risks),
            "recovery_preview": recovery_preview,
            "checkpoints": list(contract.checkpoints),
            "note": "dry run only: nothing was executed or recorded as done"}
