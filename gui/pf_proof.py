"""PF-1 first-run proof surface (GUI split #3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .pf_surfaces_helpers import _card




# ================================================================ PF-1
# First-run proof: mic meter moving, speaker test, one spoken
# turn, one screen look, one tool call — green ticks, not just
# a key prompt (plan PF-1). The model is the contract; the
# runner that actually probes hardware is the wiring step's
# job, fed in as probe results. With no probes, every check
# reads "not run yet" — never a fabricated pass.

PF1_CHECK_DEFS = (
    ("microphone", "Microphone hears you",
     "VYUK listens through your microphone and the level "
     "meter moves while you speak."),
    ("speaker", "Speaker plays back",
     "VYUK speaks a short test sentence through your "
     "speakers so you can hear her clearly."),
    ("spoken_turn", "One spoken turn works",
     "You speak, VYUK answers out loud, and the turn "
     "completes without an error."),
    ("screen_look", "One screen look works",
     "VYUK looks at the screen once, with your permission, "
     "and describes what she sees."),
    ("tool_call", "One tool call works",
     "VYUK runs one small, safe action end to end — "
     "proof her hands work, not just her voice."),
)

CHECK_NOT_RUN = "not_run"
CHECK_PASSED = "passed"
CHECK_FAILED = "failed"


@dataclass
class ProofCheck:
    """One proof step. ``next_step`` is mandatory on a
    failed check and present on a not-run check: a proof
    that fails silently is not proof."""
    check_id: str
    title: str
    what: str
    status: str = CHECK_NOT_RUN
    detail: str = ""
    next_step: str = ""

    @property
    def is_pass(self):
        return self.status == CHECK_PASSED

    def summary_line(self):
        mark = {"passed": "✓", "failed": "✗",
                "not_run": "○"}.get(self.status, "○")
        line = f"{mark} {self.title}"
        if self.detail:
            line += f" — {self.detail}"
        if self.status == CHECK_FAILED and self.next_step:
            line += f" Next step: {self.next_step}"
        elif self.status == CHECK_NOT_RUN and self.next_step:
            line += f" Next step: {self.next_step}"
        return line


def build_proof_checks(probe_results=None):
    """Build the five proof checks. ``probe_results`` maps
    check_id -> (status, detail, next_step); anything not
    supplied stays not-run with an honest next step. A
    probe result that claims a pass without a detail is
    accepted as a pass — the detail is display text, not
    evidence — but a failed result without a next step is
    completed with a generic one rather than shipped bare:
    the widget never renders a bare failure."""
    probes = probe_results or {}
    checks = []
    for check_id, title, what in PF1_CHECK_DEFS:
        status, detail, next_step = probes.get(
            check_id, (CHECK_NOT_RUN, "Not run yet",
                       "Run the first-run check to prove "
                       "this step."))
        if status not in (CHECK_NOT_RUN, CHECK_PASSED, CHECK_FAILED):
            status = CHECK_NOT_RUN
        if status == CHECK_FAILED and not next_step:
            next_step = ("Open the Doctor from Advanced "
                         "and follow the finding for this step.")
        checks.append(ProofCheck(check_id, title, what, status,
                                 detail, next_step))
    return checks


@dataclass
class ProofRun:
    checks: list = field(default_factory=list)

    @property
    def overall(self):
        if not self.checks:
            return CHECK_NOT_RUN
        if all(check.is_pass for check in self.checks):
            return CHECK_PASSED
        if any(check.status == CHECK_FAILED for check in self.checks):
            return CHECK_FAILED
        return CHECK_NOT_RUN

    def headline(self):
        if self.overall == CHECK_PASSED:
            return ("First-run proof passed — every step "
                    "proved itself before you were asked "
                    "for anything.")
        if self.overall == CHECK_FAILED:
            failed = [c.title for c in self.checks
                      if c.status == CHECK_FAILED]
            return ("First-run proof did not pass: "
                    + ", ".join(failed)
                    + ". VYUK says so plainly instead of "
                      "pretending — follow the next step "
                      "under each failed step.")
        return ("First-run proof has not run yet — no step "
                "is claimed as working until it is proven. "
                "Next step: run the first-run check.")


class FirstRunProofWidget(QWidget):
    """Renders a ProofRun. Construct with an explicit run
    (the wiring step builds it from live probes); the
    default run is all-not-run, which is the honest
    pre-wiring state."""

    def __init__(self, run=None, parent=None):
        super().__init__(parent)
        self.run = run if run is not None else ProofRun(
            checks=build_proof_checks())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "First-run proof",
            "Before VYUK asks anything of you, she proves "
            "each piece works. A tick means the step was "
            "proven, not assumed.")
        self.headline_label = QLabel(self.run.headline())
        self.headline_label.setWordWrap(True)
        self.headline_label.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(self.headline_label)
        self.check_labels = []
        for check in self.run.checks:
            label = QLabel(check.summary_line())
            label.setWordWrap(True)
            if check.status == CHECK_FAILED:
                label.setObjectName("muted")
            card_layout.addWidget(label)
            self.check_labels.append(label)
