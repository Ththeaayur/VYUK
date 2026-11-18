"""Routine-template gallery (RET-5).

One panel that lists the bundled templates, imports one as a
routine, and exports one of the user's routines as a template.
Distribution, not in-product social: there is no feed, no
sharing inside the app, no score, no leaderboard.

Rules this panel enforces (the validator in
core/routine_templates.py is the guarantee; this panel is the
thin renderer over it — the window_trust.py pattern: pure
functions first, dialog builder at the bottom; no Qt at the top):

- v1 templates are READ-class only. A template with a gated step
  is refused, whole, with the reason.
- No user data in any template by construction: the validator
  refuses absolute paths, email-shaped values, secret-looking
  values, person-name-shaped values in person slots, and file
  content — at export AND at import. A template is a named list
  of step descriptions with placeholders, never a recording.
- Import goes through the funnel: the panel saves the imported
  routine through the confirm-gated save routine action, so the
  user names it and confirms the full step list in the funnel's
  own popup. Import never pre-approves the steps — they still
  confirm at run time through the funnel (RET-2).
- Export is confirm-gated in this panel: the exact template JSON
  is shown in a dialog first, and the export only proceeds after
  the user says yes. The JSON is copied to the clipboard — nothing
  leaves the machine except through the user's explicit copy.
- The gallery source for v1 is the bundled file
  (routine_templates.json at the tree root). No public page —
  where the gallery lives is D-RET-2, owed, not decided here.

What this module never does: it never writes the routine store
directly (import goes through the funnel), never touches the
funnel or the confirm gates, never scores or ranks a template,
never charges anything (ruling 34 is open).
"""

from __future__ import annotations

__all__ = [
    "render_template_steps",
    "render_template_list",
    "build_templates_dialog",
]

# The bundled gallery source, named on the panel — the prover
# checks the panel names it.
GALLERY_SOURCE_TEXT = (
    "Bundled templates (routine_templates.json). No public page — "
    "where the gallery lives is a decision owed (D-RET-2)."
)


def render_template_steps(template):
    """One line per step: tool + masked args. Args render through
    the confirm popup's own masking (summarize_args), so a
    secret-shaped value can never be shown here even if the
    validator missed it."""
    from voice_assistant.core.gui_facade import confirm_gate_scrub
    lines = []
    steps = template.get("steps") or [] if isinstance(template, dict) else []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            lines.append("%d. (not a tool call)" % (index + 1))
            continue
        args = step.get("args") or {}
        summary = confirm_gate_scrub.summarize_args(
            args, limit=1000) if isinstance(args, dict) else repr(args)
        lines.append("%d. %s %s" % (index + 1, step.get("tool"),
                                    summary))
    return "\n".join(lines)


def render_template_list(templates):
    """The gallery listing as text: one line per template — name,
    step count, and the first step's tool. Pure, testable without Qt."""
    lines = []
    for template in templates or []:
        steps = template.get("steps") or []
        first = ""
        if steps and isinstance(steps[0], dict):
            first = " — starts with %s" % steps[0].get("tool")
        lines.append("%s (%d step%s%s)" % (
            template.get("name") or "(unnamed)", len(steps),
            "" if len(steps) == 1 else "s", first))
    return "\n".join(lines)


def build_templates_dialog(parent=None):
    """The gallery dialog: bundled templates with Import, and the
    user's routines with Export. Qt imports live inside this
    builder (the window_today.py pattern); the pure renderers
    above stay Qt-free."""
    from PySide6.QtWidgets import (
        QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
        QPushButton, QTextEdit, QVBoxLayout, QWidget,
    )
    from voice_assistant.core.gui_facade import planner
    from voice_assistant.core.gui_facade import routine_templates as rt
    from voice_assistant.core.gui_facade import tools
    from voice_assistant.gui.design_system.dialogs import HudDialog

    dlg = QDialog(parent)
    dlg.setWindowTitle("Routine templates")
    dlg.setMinimumWidth(560)
    lay = QVBoxLayout(dlg)
    feedback = QLabel("")
    feedback.setWordWrap(True)

    def _set_feedback(text):
        feedback.setText(text)

    def _import_template(template):
        """Import one bundled template: name it, fill its
        placeholders, then save it through the funnel's
        confirm-gated save routine action — the funnel shows the
        full step list and the user confirms it there."""
        placeholders = rt.extract_placeholders(template)
        form = QDialog(dlg)
        form.setWindowTitle("Import template")
        form_lay = QVBoxLayout(form)
        form_lay.addWidget(QLabel(
            "Import %r as a routine. The steps are read-only; "
            "they still confirm at run time." % template.get("name")))
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Routine name")
        name_edit.setText(str(template.get("name") or ""))
        edits = {"name": name_edit}
        inner = QFormLayout()
        inner.addRow("Name:", name_edit)
        for ph in placeholders:
            edit = QLineEdit()
            edit.setPlaceholderText(ph)
            edits[ph] = edit
            inner.addRow("%s:" % ph, edit)
        form_lay.addLayout(inner)
        row = QHBoxLayout()
        btn_ok = QPushButton("Import")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(form.accept)
        btn_cancel.clicked.connect(form.reject)
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        form_lay.addLayout(row)
        if form.exec() != QDialog.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            _set_feedback("A routine needs a name.")
            return
        fill = {ph: edits[ph].text().strip() for ph in placeholders}
        text = rt.template_json(template)
        ok, steps, reason = rt.import_template(text, fill=fill)
        if not ok:
            _set_feedback("Could not import: %s" % reason)
            return
        try:
            result = tools.execute("scheduled_agents", {
                "action": "save_routine", "name": name, "steps": steps})
        except Exception as exc:  # noqa: BLE001
            _set_feedback("Could not import: %s" % exc)
            return
        if isinstance(result, dict) and result.get("ok"):
            _set_feedback("Imported %r as routine %r."
                          % (template.get("name"), name))
        else:
            reason = ""
            if isinstance(result, dict):
                reason = result.get("reason") or result.get("message") or ""
            _set_feedback("Import refused: %s" % (reason or result))

    def _export_routine(routine):
        """Export one routine: build the template, show the exact
        JSON in a dialog, and only on yes copy it out. A gated
        refusal offers the read-only steps instead (F-201)."""
        ok, template, reason, read_only = rt.build_template(routine)
        if not ok and read_only:
            if not HudDialog.confirm(
                    dlg, "Template has a gated step",
                    "%s\n\nExport the read-only steps instead?"
                    % reason, default=False):
                return
            ok, template, reason, _ro = rt.build_template(
                routine, read_only_only=True)
        if not ok:
            _set_feedback("Could not export: %s" % reason)
            return
        json_text = rt.template_json(template)
        confirm = HudDialog(dlg, title="Export template",
                            eyebrow="Routine templates")
        confirm.add_title("Export template")
        confirm.add_body(
            "This is the exact JSON that will be copied. It holds "
            "no user data — the validator refused any path, name, "
            "secret, or file content. Nothing leaves the machine "
            "except through your copy.")
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json_text)
        view.setMinimumHeight(220)
        confirm.content_layout().addWidget(view)
        confirm.add_buttons([
            ("export", "Export", "accept", True, False),
            ("cancel", "Cancel", "reject", False, False),
        ])
        if confirm.exec() != QDialog.Accepted:
            return
        ok2, out, reason2 = rt.export_template(template, confirmed=True)
        if not ok2:
            _set_feedback("Could not export: %s" % reason2)
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(out)
        _set_feedback("Template %r exported — JSON copied to the "
                      "clipboard." % template.get("name"))

    lay.addWidget(QLabel(GALLERY_SOURCE_TEXT))
    lay.addWidget(QLabel("Bundled templates:"))
    valid, skipped = rt.load_bundled_templates()
    for template in valid:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        label = QLabel(render_template_list([template]))
        label.setWordWrap(True)
        h.addWidget(label, 1)
        btn = QPushButton("Import")
        btn.setObjectName("chipBtn")
        btn.clicked.connect(
            lambda _=False, t=dict(template): _import_template(t))
        h.addWidget(btn)
        lay.addWidget(row)
    if skipped:
        lay.addWidget(QLabel(
            "%d bundled template(s) failed validation and are not "
            "listed." % len(skipped)))
    lay.addWidget(QLabel("Your routines:"))
    try:
        routines = planner.get_routines()
    except Exception:  # noqa: BLE001
        routines = []
    if not routines:
        lay.addWidget(QLabel("No routines saved yet."))
    for routine in routines:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        name = routine.get("name") or "(unnamed)"
        count = len(routine.get("actions") or [])
        label = QLabel("%s (%d step%s)" % (
            name, count, "" if count == 1 else "s"))
        label.setWordWrap(True)
        h.addWidget(label, 1)
        btn = QPushButton("Export")
        btn.setObjectName("chipBtn")
        btn.clicked.connect(
            lambda _=False, r=dict(routine): _export_routine(r))
        h.addWidget(btn)
        lay.addWidget(row)
    lay.addWidget(feedback)
    close_row = QHBoxLayout()
    btn_close = QPushButton("Close")
    btn_close.clicked.connect(dlg.accept)
    close_row.addStretch(1)
    close_row.addWidget(btn_close)
    lay.addLayout(close_row)
    return dlg, {"feedback": feedback}
