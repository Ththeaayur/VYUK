"""Wave 2 trust slices, §13 (stdlib only, no GUI, no network).

  * P13 track record: job outcomes appended by the caller, counted,
    never invented. No history is fabricated for jobs never run.
  * P13 capability map: capability -> status (proven / pilot /
    gated) with an evidence id; gated capabilities carry a reason.
  * P13 lessons learned: a weekly note built ONLY from supplied
    outcomes; no pattern inference, no habit capture.
  * P13 pre-flight check: failsafe, target resolved, confirm gate,
    scope -- every check must pass or the run is blocked with the
    failing list.
  * P19 delegation dial: levels suggest / confirm / act. The dial
    NEVER changes consequential behaviour: send/delete/purchase/
    system change always ask under C-14, whatever the dial says.
  * P19 escalation ladder: ordered levels with caller-supplied
    triggers; escalation only moves up, never auto-acts.
  * P21 sitrep: lines from real supplied log lines only; with no
    logs, the sitrep says "no data yet", never a guessed summary.
  * P21 blast radius meter: affected windows/files taken ONLY
    from supplied plan steps; unknown steps widen the radius to
    "unknown", never to a guessed number.
  * P21/P22 because-tags: every proactive item must carry a
    reason; an item without one is refused, not tagged by guess.
  * P21 decision journal: decisions appended with reason and
    outcome; entries are never edited in place (append-only).
  * P13-16 / P17-18 skill review in plain words: renders a skill
    summary without engineering codes; unknown fields render as
    "unknown", never invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONSEQUENTIAL = ("send", "delete", "purchase", "system_change",
                 "message", "booking", "payment")


class TrackRecord:
    def __init__(self):
        self.jobs: list[dict] = []

    def append(self, job_id: str, outcome: str, detail: str = "") -> None:
        self.jobs.append({"job_id": job_id, "outcome": outcome,
                          "detail": detail})

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for job in self.jobs:
            counts[job["outcome"]] = counts.get(job["outcome"], 0) + 1
        return {"total": len(self.jobs), "by_outcome": counts,
                "note": "recorded jobs only; no history invented"}


class CapabilityMap:
    def __init__(self):
        self._caps: dict[str, dict] = {}

    def set(self, name: str, status: str, evidence: str = "",
            reason: str = "") -> dict:
        if status not in ("proven", "pilot", "gated"):
            return {"ok": False, "reason": f"unknown status '{status}'"}
        self._caps[name] = {"status": status, "evidence": evidence,
                            "reason": reason}
        return {"ok": True}

    def get(self, name: str) -> dict:
        return self._caps.get(name, {"status": "unknown",
                                     "reason": "capability not recorded"})

    def all(self) -> dict:
        return dict(self._caps)


def lessons_learned(outcomes: list) -> dict:
    """Weekly lessons from supplied outcomes only; no inference."""
    lessons = []
    for outcome in outcomes:
        if isinstance(outcome, dict) and outcome.get("lesson"):
            lessons.append(str(outcome["lesson"]))
    return {"lessons": lessons,
            "note": "lessons are caller-supplied outcomes, not inferred patterns"}


def preflight_check(*, failsafe_ok: bool, target_resolved: bool,
                    confirm_gate_ok: bool, scope_ok: bool) -> dict:
    checks = {"failsafe": failsafe_ok, "target_resolved": target_resolved,
              "confirm_gate": confirm_gate_ok, "scope": scope_ok}
    failing = sorted(k for k, v in checks.items() if not v)
    return {"blocked": bool(failing), "failing": failing, "checks": checks,
            "reason": "" if not failing else
            f"blocked by: {', '.join(failing)}"}


def delegation_decision(dial: str, action: str) -> dict:
    """Delegation dial; consequential actions always ask (C-14)."""
    if action in CONSEQUENTIAL:
        return {"dial": dial, "action": action, "decision": "ask",
                "reason": "consequential: C-14 approve-first overrides the dial"}
    if dial == "act":
        return {"dial": dial, "action": action, "decision": "act",
                "reason": "dial permits acting on non-consequential actions"}
    if dial == "confirm":
        return {"dial": dial, "action": action, "decision": "ask",
                "reason": "dial is confirm"}
    return {"dial": dial, "action": action, "decision": "suggest",
            "reason": "dial is suggest; no action taken"}


ESCALATION_LEVELS = ("self", "ask_owner", "stop_and_report")


def escalation_step(current: str, trigger: str) -> dict:
    """Move one level up the ladder; never auto-act on escalation."""
    if current not in ESCALATION_LEVELS:
        return {"level": "stop_and_report", "decision": "stop_and_report",
                "reason": f"unknown level '{current}' fails closed"}
    idx = ESCALATION_LEVELS.index(current)
    nxt = ESCALATION_LEVELS[min(idx + 1, len(ESCALATION_LEVELS) - 1)]
    return {"level": nxt, "decision": "escalate",
            "reason": f"escalated from {current} on trigger: {trigger}",
            "action_taken": False}


def sitrep(log_lines: list) -> str:
    if not log_lines:
        return "Sitrep: no data yet."
    lines = [str(line) for line in log_lines]
    return "Sitrep:\n" + "\n".join(f"- {line}" for line in lines)


def blast_radius(plan_steps: list) -> dict:
    """Affected surface from supplied steps only; unknown steps -> unknown."""
    windows: set = set()
    files: set = set()
    unknown = False
    for step in plan_steps:
        if not isinstance(step, dict):
            unknown = True
            continue
        if step.get("window"):
            windows.add(str(step["window"]))
        if step.get("file"):
            files.add(str(step["file"]))
        if not step.get("window") and not step.get("file"):
            unknown = True
    return {"windows": sorted(windows), "files": sorted(files),
            "unknown": unknown,
            "summary": ("unknown radius (unscoped steps present)" if unknown
                        else f"{len(windows)} window(s), {len(files)} file(s)")}


def because_tag(item: str, reason: str) -> dict:
    if not reason or not str(reason).strip():
        return {"item": item, "tagged": False,
                "reason": "refused: proactive item without a reason is not tagged"}
    return {"item": item, "tagged": True, "tag": str(reason).strip()}


class DecisionJournal:
    def __init__(self):
        self.entries: list[dict] = []

    def append(self, decision: str, reason: str, outcome: str = "") -> None:
        self.entries.append({"decision": decision, "reason": reason,
                             "outcome": outcome})

    def all(self) -> list:
        return list(self.entries)


def skill_review_plain(skill: dict) -> str:
    """Render a skill in plain words; unknown fields stay unknown."""
    if not isinstance(skill, dict):
        return "Skill review: unknown skill."
    name = skill.get("name", "unknown skill")
    what = skill.get("what_it_does", "unknown")
    status = skill.get("status", "unknown")
    return (f"Skill review: {name}. What it does: {what}. "
            f"Status: {status}.")
