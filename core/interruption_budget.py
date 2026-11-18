"""J7 interruption + P22 interruption budget, Wave 2 (stdlib only).

A goal interrupted mid-run pauses, reports, and resumes or abandons
cleanly. The interruption budget is the visible rule: a caller-set
maximum number of interruptions for one goal run. Held-back items
are listed, never silently dropped. An "urgent" interruption is
urgent only when the caller marks it urgent under the owner's own
rules -- this module never invents urgency, and exceeding the
budget never auto-resumes a goal: it abandons with a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GoalRun:
    goal: str
    budget: int = 3
    state: str = "running"  # running | paused | resumed | abandoned | done
    interruptions: int = 0
    held_back: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def interrupt(self, reason: str, *, urgent: bool = False,
                  held_item: str = "") -> dict:
        """Pause the run. Returns the pause report dict."""
        if self.state in ("abandoned", "done"):
            return {"state": self.state, "changed": False,
                    "report": f"Goal already {self.state}; interruption ignored."}
        self.interruptions += 1
        if held_item:
            self.held_back.append(held_item)
        if self.interruptions > self.budget and not urgent:
            self.state = "abandoned"
            report = (f"Goal abandoned: interruption budget exceeded "
                      f"({self.interruptions} > {self.budget}); "
                      f"reason: {reason}. Held back: {self.held_back}.")
            self.log.append(report)
            return {"state": "abandoned", "changed": True, "report": report}
        self.state = "paused"
        report = (f"Goal paused (interruption {self.interruptions} of "
                  f"{self.budget}): {reason}. Held back: {self.held_back}.")
        self.log.append(report)
        return {"state": "paused", "changed": True, "report": report}

    def resume(self) -> dict:
        if self.state != "paused":
            return {"state": self.state, "changed": False,
                    "report": f"Cannot resume: goal is {self.state}."}
        self.state = "resumed"
        report = "Goal resumed from pause."
        self.log.append(report)
        return {"state": "resumed", "changed": True, "report": report}

    def abandon(self, reason: str) -> dict:
        if self.state in ("abandoned", "done"):
            return {"state": self.state, "changed": False,
                    "report": f"Goal already {self.state}."}
        self.state = "abandoned"
        report = f"Goal abandoned: {reason}. Held back: {self.held_back}."
        self.log.append(report)
        return {"state": "abandoned", "changed": True, "report": report}

    def finish(self) -> dict:
        if self.state in ("abandoned", "done"):
            return {"state": self.state, "changed": False,
                    "report": f"Goal already {self.state}."}
        self.state = "done"
        report = (f"Goal done. Interruptions: {self.interruptions} "
                  f"(budget {self.budget}). Held back: {self.held_back}.")
        self.log.append(report)
        return {"state": "done", "changed": True, "report": report}


@dataclass
class InterruptionBudget:
    """P22 visible rule: budget + held-back list, per goal run."""

    budget: int = 3

    def new_run(self, goal: str) -> GoalRun:
        return GoalRun(goal=goal, budget=self.budget)
