"""U9 — Progress report panel (tutor programme, 2026-11-08).

The GUI surface for the tutor progress report, in the settled home.
This widget renders an assembled report dict from
``core.brain.progress_report`` only — it holds no store, no model,
no scheduler, no write handle of any kind. Every string it shows
comes from that dict; it never reads the tutor records itself.

Placement: the panel is a child of the settled home's docked chat
panel (``gui/window_shell_assistant.py`` inserts it, hidden until
a report is shown — mirroring the U6 lesson panel). It is NOT a
top-level window, NOT a dialog, and it does not redesign Home or
touch Aura. The premium bar applies: plain stack, themed object
names, no debug-overlay look.

The clear button: the panel exposes the clear, it does not perform
it. The button is disabled until a driver sets a handler with
``set_clear_handler``; pressing it calls the handler exactly once.
The handler (the tutor session driver) owns the confirmation, the
``clear_tutor_records`` call, and the refresh. The panel itself
never deletes anything.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProgressReportPanel(QWidget):
    """Renders an assembled progress report dict, nothing else."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._report = None
        self._clear_handler = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.title_lbl = QLabel("Progress report")
        self.title_lbl.setObjectName("reportTitle")
        layout.addWidget(self.title_lbl)

        self.overall_lbl = QLabel("")
        self.overall_lbl.setObjectName("reportOverall")
        self.overall_lbl.setWordWrap(True)
        layout.addWidget(self.overall_lbl)

        self.speed_lbl = QLabel("")
        self.speed_lbl.setObjectName("reportSpeed")
        self.speed_lbl.setWordWrap(True)
        layout.addWidget(self.speed_lbl)

        self.skills_lbl = QLabel("")
        self.skills_lbl.setObjectName("reportSkills")
        self.skills_lbl.setWordWrap(True)
        layout.addWidget(self.skills_lbl)

        self.due_lbl = QLabel("")
        self.due_lbl.setObjectName("reportDue")
        self.due_lbl.setWordWrap(True)
        layout.addWidget(self.due_lbl)

        self.weakest_lbl = QLabel("")
        self.weakest_lbl.setObjectName("reportWeakest")
        self.weakest_lbl.setWordWrap(True)
        layout.addWidget(self.weakest_lbl)

        self.misconceptions_lbl = QLabel("")
        self.misconceptions_lbl.setObjectName("reportMisconceptions")
        self.misconceptions_lbl.setWordWrap(True)
        layout.addWidget(self.misconceptions_lbl)

        self.clear_btn = QPushButton("Clear tutor record")
        self.clear_btn.setObjectName("reportClearBtn")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self.clear_btn)

    # -- the report ----------------------------------------------------
    def show_report(self, report):
        """Render an assembled report dict. Hidden until shown."""
        if not isinstance(report, dict) or not report.get("ok"):
            self.clear()
            return
        self._report = report
        totals = report["totals"]
        self.overall_lbl.setText(
            f"{totals['skills']} skills, {totals['mastered']} mastered, "
            f"{totals['evidence']} evidence "
            f"({totals['correct']} right, {totals['wrong']} wrong), "
            f"{totals['transfers']} clean transfers.")
        speed = report["learning_speed"]
        if speed["span_days"] is None:
            self.speed_lbl.setText("Learning speed: no record yet.")
        else:
            self.speed_lbl.setText(
                f"Learning speed: {speed['evidence_per_day']} "
                f"evidence/day, {speed['transfers_per_day']} "
                f"transfers/day over {speed['span_days']} day(s) "
                f"({speed['span_start']} to {speed['span_end']}).")
        skill_lines = []
        for skill in report["skills"]:
            mastery = ("mastered" if skill["mastered"]
                       else f"p {skill['p']:.2f}"
                       if isinstance(skill["p"], (int, float)) else "p —")
            source = skill.get("source") or "not recorded"
            skill_lines.append(
                f"{skill['skill_id']} — {mastery}, "
                f"{skill['evidence']} evidence, "
                f"{skill['transfers']} clean transfers, "
                f"source: {source}.")
        self.skills_lbl.setText("\n".join(skill_lines)
                                if skill_lines else "(no skills yet)")
        self.due_lbl.setText(
            f"Due now: {len(report['due_now'])} skill(s). "
            f"Due in the next 24 hours: {len(report['due_24h'])} "
            "skill(s).")
        if report["weakest"]:
            self.weakest_lbl.setText(
                "Weakest: " + ", ".join(
                    f"{w['skill_id']} (p {w['p']:.2f})"
                    for w in report["weakest"]))
        else:
            self.weakest_lbl.setText(
                "Weakest: none — every recorded skill is mastered "
                "or at 0.60 or above.")
        misconceptions = report["misconceptions"]
        parts = []
        if misconceptions["named"]:
            parts.append("Known by name (counts not recorded): " +
                         ", ".join(e["name"]
                                   for e in misconceptions["named"]))
        if misconceptions["unclassified"]:
            parts.append("Not yet named: " + ", ".join(
                f"{g['skill']} ({g['count']})"
                for g in misconceptions["unclassified"]))
        self.misconceptions_lbl.setText(
            " ".join(parts) if parts else "Misconceptions: none recorded.")
        self.setVisible(True)

    def clear(self):
        """Empty the panel and hide it."""
        self._report = None
        for label in (self.overall_lbl, self.speed_lbl, self.skills_lbl,
                      self.due_lbl, self.weakest_lbl,
                      self.misconceptions_lbl):
            label.setText("")
        self.setVisible(False)

    def report(self):
        """The report currently shown, or None."""
        return self._report

    def rendered_texts(self):
        """Every text the panel currently shows (for tests: the GUI
        rendering must agree with the plain-text rendering)."""
        texts = [
            self.title_lbl.text(),
            self.overall_lbl.text(),
            self.speed_lbl.text(),
            self.skills_lbl.text(),
            self.due_lbl.text(),
            self.weakest_lbl.text(),
            self.misconceptions_lbl.text(),
            self.clear_btn.text(),
        ]
        return [t for t in texts if t]

    # -- the clear action (exposed, never performed here) --------------
    def set_clear_handler(self, handler):
        """Set the driver-owned clear handler (or None to unset).

        The button is enabled only while a handler is set. The
        handler owns the confirmation, the clear itself, and the
        refresh; this panel never touches the store.
        """
        self._clear_handler = handler
        self.clear_btn.setEnabled(handler is not None)

    def _on_clear_clicked(self):
        if self._clear_handler is not None:
            self._clear_handler()
