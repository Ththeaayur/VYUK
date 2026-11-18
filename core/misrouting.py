"""Misrouting baseline measurement, Wave 2 (stdlib only).

Records, per attempted action, the intended target vs the actual
target (window/control_id, caller-supplied). Computes the misroute
rate and wrong-window count as DATA only -- no verdict, no winner,
no D-14 decision. A baseline is "complete" only when the caller
supplies a minimum case count and that count is reached; the
minimum is never invented here, so without it the baseline stays
incomplete by construction. This baseline is the prerequisite for
the D-14 decision point in Wave 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MisroutingBaseline:
    cases: list = field(default_factory=list)

    def record(self, intended_window: str, actual_window: str,
               intended_control: str = "", actual_control: str = "") -> dict:
        case = {"intended_window": intended_window,
                "actual_window": actual_window,
                "intended_control": intended_control,
                "actual_control": actual_control,
                "misrouted": actual_window != intended_window
                or (intended_control and actual_control != intended_control)}
        self.cases.append(case)
        return case

    def summary(self, *, min_cases: int | None = None) -> dict:
        total = len(self.cases)
        misrouted = sum(1 for c in self.cases if c["misrouted"])
        wrong_window = sum(1 for c in self.cases
                           if c["actual_window"] != c["intended_window"])
        rate = (misrouted / total) if total else 0.0
        complete = min_cases is not None and total >= min_cases
        return {"total": total, "misrouted": misrouted,
                "wrong_window": wrong_window, "misroute_rate": rate,
                "baseline_complete": complete,
                "min_cases": min_cases,
                "note": "data only; no verdict, no D-14 decision made here"}
