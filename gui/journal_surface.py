"""GUI reconcile surface for the §7 action journal (batch 9).

The journal funnel tools (journal_action list/plan/reconcile/
compensate) existed since batch 2, but no screen showed them — a
user with a killed-mid-task entry had no way to see it or offer
reconcile. This module is that screen, on the Activity surface
(no Home change, no new page): attention-ordered journal rows,
evidence shown verbatim, and a Reconcile action through the
EXISTING authorization dialog.

Boundaries, stated plainly:

* The GUI NEVER rewrites journal history. Rows are evidence
  only: tool, state, outcome, handle id + snapshot stamp (as
  strings, never re-resolved), declared compensations, manual
  steps. Reconcile appends one outcome record through the core
  ``reconcile()`` path; the original attempt line is never
  touched (proven by test: old lines byte-identical + exactly
  one new line). No GUI path re-resolves a handle or takes a
  fresh observation — this module never imports item-1
  resolution or item-2 perception.
* Reconcile is a USER-CONFIRMED action in two steps, and the
  steps are not interchangeable: first an input dialog collects
  the facts reconcile requires (did the effect happen? what
  verified it?) — collecting facts approves nothing; then the
  EXISTING authorization dialog (batch-7 presenter, same
  contract: Allow once / Don't allow, 60 s silence = deny)
  authorizes the write. Denial, close, or silence writes
  nothing. A spoken "yes" never resolves anything here (C-14):
  no voice path into ``reconcile_entry`` exists, pinned by
  test.
* Effectful compensation asks first (D5): the surface DISPLAYS
  the declared plan (reverse order) and the manual steps, and
  the Compensate button runs them through the EXISTING
  journal_action compensate leg — never a fork, never a new
  executor. Authorization dialog first (batch-7 presenter,
  same contract: Allow once / Don't allow, 60 s silence =
  deny), then the leg on a worker thread with ``confirmed``
  set ONLY from the explicit Allow click; each step runs
  through the funnel executor (gate stack + its own receipt),
  first failure stops. No declaration → the button never
  enables and the leg is never called. A spoken "yes" never
  reaches this path (C-14): no voice path into
  ``present_compensate`` exists, pinned by test.
* Corruption is LOUD: a corrupt journal line surfaces as one
  loud row carrying the error + the manual repair path from
  ``check_journal`` — never a partial list, never hidden,
  never auto-repaired, never "fixed" by the GUI.
* No dismiss affordance is built: hiding an entry without a
  reconcile record would destroy evidence by UI. Attention
  rows clear themselves when reconciled (they leave the
  unknown class) — that is the only way out.
"""

from __future__ import annotations

_MAX_ROWS = 50


def journal_attention(store_path=None):
    """Attention-ordered journal rows from a FRESH store read.

    Returns ``{"ok": True, "rows": [...], "counts": {...}}`` or
    ``{"ok": False, "error": ..., "repair_steps": [...]}`` on
    corruption (never a partial list). Order: dispatched-
    without-outcome (unknown) first, then entries with declared
    compensations, then recent entries — newest first within
    each group, capped at ``_MAX_ROWS`` rows. Counts cover ALL
    entries, not just the capped rows.
    """
    from voice_assistant.core.gui_facade import action_journal as aj  # Phase 3 S7a-2: moved to core/desktop/
    from voice_assistant.core.gui_facade import tool_receipts as tr

    store = (tr.ReceiptStore(path=store_path) if store_path is not None
             else tr.ReceiptStore())
    try:
        entries = aj.load_entries(store)
    except ValueError:
        health = aj.check_journal(store)
        return {"ok": False,
                "error": health.get("error", "Journal unreadable."),
                "repair_steps": list(health.get("repair_steps") or [])}
    counts = {aj.CLASS_RETRYABLE: 0, aj.CLASS_DONE: 0,
              aj.CLASS_UNKNOWN: 0}
    rows = []
    for entry in entries:
        counts[aj.classify(entry)] += 1
        rows.append(_row_view(entry, aj))
    unknown = [r for r in rows if r["class"] == aj.CLASS_UNKNOWN]
    with_comp = [r for r in rows
                 if r["class"] != aj.CLASS_UNKNOWN
                 and r["compensation_status"] == "declared"]
    rest = [r for r in rows
            if r["class"] != aj.CLASS_UNKNOWN
            and r["compensation_status"] != "declared"]

    def _newest(row):
        return (row.get("created_at", 0) or 0, row.get("entry_id", ""))

    unknown.sort(key=_newest, reverse=True)
    with_comp.sort(key=_newest, reverse=True)
    rest.sort(key=_newest, reverse=True)
    ordered = (unknown + with_comp + rest)[:_MAX_ROWS]
    return {"ok": True, "rows": ordered, "counts": counts,
            "total": len(entries)}


def _row_view(entry, aj):
    """One GUI row from a journal entry view. Evidence only —
    handle ids and stamps are copied as strings, never
    re-resolved, never observed afresh."""
    comp = aj.compensation_plan(entry)
    return {
        "entry_id": str(entry.get("entry_id", "")),
        "tool": str(entry.get("tool", "")),
        "state": str(entry.get("state", "")),
        "outcome": entry.get("outcome"),
        "class": aj.classify(entry),
        "evidence": str(entry.get("evidence", "")),
        "args_preview": str(entry.get("args_preview", "")),
        "handle_id": entry.get("handle_id"),
        "snapshot_stamp": entry.get("snapshot_stamp"),
        "compensation_status": comp["status"],
        "compensations": [dict(s) for s in comp.get("steps") or []],
        "manual_steps": list(comp.get("manual_steps") or []),
        "created_at": entry.get("created_at", 0),
    }


def journal_row_text(row):
    """Compact list text for one attention row."""
    row = row if isinstance(row, dict) else {}
    head = f"{row.get('tool', '')} — {row.get('state', '')}"
    if row.get("outcome"):
        head += f" ({row['outcome']})"
    lines = [head]
    if row.get("evidence"):
        lines.append(f"Evidence: {row['evidence']}")
    handle = row.get("handle_id")
    stamp = row.get("snapshot_stamp")
    if handle or stamp:
        lines.append(f"Handle: {handle or '—'} · "
                     f"Stamp: {stamp or '—'}")
    if row.get("compensation_status") == "declared":
        lines.append(
            f"Undo: {len(row.get('compensations') or [])} declared "
            f"step(s), reverse order")
    else:
        lines.append("Undo: none declared — see manual steps")
    return "\n".join(lines)


def journal_row_details(row):
    """Full details text for one attention row (Details dialog)."""
    row = row if isinstance(row, dict) else {}
    lines = [f"Entry: {row.get('entry_id', '')}",
             f"Tool: {row.get('tool', '')}",
             f"State: {row.get('state', '')} "
             f"(class: {row.get('class', '')})"]
    if row.get("outcome"):
        lines.append(f"Outcome: {row['outcome']}")
    if row.get("evidence"):
        lines.append(f"Evidence: {row['evidence']}")
    if row.get("args_preview"):
        lines.append(f"Args: {row['args_preview']}")
    lines.append(f"Handle id: {row.get('handle_id') or 'none recorded'}")
    lines.append(f"Snapshot stamp: "
                 f"{row.get('snapshot_stamp') or 'none recorded'}")
    if row.get("compensation_status") == "declared":
        lines.append("Compensations (reverse declaration order):")
        for step in row.get("compensations") or []:
            lines.append(f"- {step.get('tool', '')} "
                         f"{step.get('note') or ''}".rstrip())
        lines.append("Compensations run through the journal_action "
                     "compensate leg — each step verified, first "
                     "failure stops. Select the row and use "
                     "Compensate selected to run them — it asks "
                     "first, and shows the leg's outcome.")
    else:
        lines.append("No undo available — manual steps:")
        for step in row.get("manual_steps") or []:
            lines.append(f"- {step}")
    if row.get("class") == "unknown":
        lines.append("Reconcile: this entry was dispatched without "
                     "an outcome (killed mid-task). Say whether "
                     "the effect happened and what verified it, "
                     "then approve — the reconcile record is "
                     "appended, history is never rewritten.")
    return "\n".join(lines)


def validate_reconcile_input(happened, verification):
    """Pure validation for the reconcile input dialog. Returns
    an error string, or None when the input can reconcile."""
    if not isinstance(happened, bool):
        return ("Reconcile needs a choice: did the effect happen "
                "or not? The tool cannot guess.")
    if not isinstance(verification, str) or not verification.strip():
        return ("Reconcile needs verification text — what "
                "confirmed what actually happened (or did not).")
    return None


def reconcile_entry(entry_id, happened, verification, *,
                    store_path=None):
    """User-confirmed reconcile through the CORE path (no fork).

    Worker-safe, no Qt. ``confirmed_by`` is "user" — the GUI
    approver; the authorization dialog is the approval, the
    name is the record (same convention as the journal_action
    tool's default). Raises ValueError on refusal (unknown id,
    not-unknown entry, missing verification/name) with the
    engine's message verbatim; the caller shows it, never
    hides it. Appends exactly one outcome record.
    """
    from voice_assistant.core.gui_facade import action_journal as aj  # Phase 3 S7a-2: moved to core/desktop/
    from voice_assistant.core.gui_facade import tool_receipts as tr

    error = validate_reconcile_input(happened, verification)
    if error is not None:
        raise ValueError(error)
    store = (tr.ReceiptStore(path=store_path) if store_path is not None
             else tr.ReceiptStore())
    return aj.reconcile(store, receipt_id=str(entry_id),
                        happened=happened,
                        verification=verification.strip(),
                        confirmed_by="user")


def compensate_entry(entry_id, *, executor, store_path=None):
    """Run one entry's declared compensations via the CORE
    engine (no fork, no GUI-side executor invention).

    Worker-safe, no Qt. ``executor`` is the funnel in
    production (each step through the gate stack, its own
    receipt) and a fake in tests. Reverse order, each verified,
    first failure stops; no declaration → executor never
    called and the manual steps return. This helper itself
    writes NO journal records (one truth: the funnel steps'
    own receipts, or nothing when faked). Raises ValueError on
    refusal with the engine's message verbatim.
    """
    from voice_assistant.core.gui_facade import action_journal as aj  # Phase 3 S7a-2: moved to core/desktop/
    from voice_assistant.core.gui_facade import tool_receipts as tr

    store = (tr.ReceiptStore(path=store_path) if store_path is not None
             else tr.ReceiptStore())
    entries = aj.load_entries(store)
    entry = next((e for e in entries
                  if str(e.get("entry_id")) == str(entry_id)), None)
    if entry is None:
        raise ValueError(
            f"compensate refuses: no journal entry for receipt "
            f"{entry_id!r}")
    return aj.run_compensations(store, entry, executor=executor,
                                confirmed_by="user")


def _build_reconcile_input_dialog(window, row):
    """The facts dialog for one reconcile. Qt, GUI thread only.

    Collects facts ONLY — it approves nothing. The authorization
    dialog (existing, batch-7 presenter) is the approval that
    follows. OK stays disabled until a happened choice is made
    AND verification text is non-empty, so the dialog cannot
    produce an invalid reconcile request.
    """
    from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox,
                                   QLabel, QLineEdit, QRadioButton,
                                   QVBoxLayout)

    dlg = QDialog(window)
    dlg.setWindowTitle("Reconcile journal entry")
    layout = QVBoxLayout(dlg)
    tool = str(row.get("tool", ""))
    evidence = str(row.get("evidence", ""))
    head = QLabel(f"{tool} — dispatched without an outcome.\n"
                  f"{evidence}" if evidence else
                  f"{tool} — dispatched without an outcome.")
    head.setWordWrap(True)
    layout.addWidget(head)
    ask = QLabel("Did the effect actually happen?")
    layout.addWidget(ask)
    group = QButtonGroup(dlg)
    happened_btn = QRadioButton("Yes — the effect happened")
    absent_btn = QRadioButton("No — verified absent")
    group.addButton(happened_btn, 1)
    group.addButton(absent_btn, 0)
    layout.addWidget(happened_btn)
    layout.addWidget(absent_btn)
    layout.addWidget(QLabel("What verified it?"))
    verification_edit = QLineEdit()
    verification_edit.setPlaceholderText(
        "e.g. the file is gone / the app shows the change")
    layout.addWidget(verification_edit)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons)
    dlg.happened_group = group
    dlg.verification_edit = verification_edit
    dlg.ok_button = buttons.button(
        QDialogButtonBox.StandardButton.Ok)

    def _update_ok():
        ok = (group.checkedButton() is not None
              and bool(verification_edit.text().strip()))
        dlg.ok_button.setEnabled(ok)

    group.buttonClicked.connect(lambda _b: _update_ok())
    verification_edit.textChanged.connect(lambda _t: _update_ok())
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    _update_ok()
    return dlg


def _read_reconcile_input_dialog(dlg):
    """Read a (possibly accepted) input dialog. Returns
    (happened, verification) or None when invalid."""
    button = dlg.happened_group.checkedButton()
    if button is None:
        return None
    happened = (dlg.happened_group.id(button) == 1)
    verification = dlg.verification_edit.text().strip()
    if validate_reconcile_input(happened, verification) is not None:
        return None
    return happened, verification


def ask_reconcile_input(window, row):
    """Show the facts dialog. GUI thread only. Returns
    (happened, verification) or None on cancel/invalid."""
    from PySide6.QtWidgets import QDialog

    dlg = _build_reconcile_input_dialog(window, row)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return _read_reconcile_input_dialog(dlg)


def activity_error_row_widget(list_widget, item, text, *,
                              details=False):
    """One §2f error row (Screen 3 Activity): a red dot, one
    line, and — with ``details=True`` — a quiet Details button
    that fires the list's own ``itemDoubleClicked`` signal for
    the item, so it opens the same dialog the double-click
    opens. This module never wires a window handler itself.
    The line re-elides to one line on resize (the instrument
    row's title wiring); the full text is the row's title, so
    nothing is ever cut mid-word. Without Details the row is
    just dot + line: the unavailable-ledger shape, where the
    recovery is an instruction ("Reopen Activity to try
    again"), not a dialog."""
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPushButton,
    )

    from .task_state import instrument_row, instrument_row_title

    wrap = instrument_row()
    wrap.setObjectName("activityRow")
    row = QHBoxLayout(wrap)
    # No vertical margins of its own: the QListWidget::item
    # padding is the row's breathing room (see
    # list_item_size_hint).
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    dot = QLabel("●")
    dot.setObjectName("activityErrorDot")
    row.addWidget(dot)
    line = QLabel(text)
    line.setObjectName("activityError")
    line.setWordWrap(False)
    row.addWidget(line, stretch=1)
    instrument_row_title(wrap, line, text)
    if details:
        details_btn = QPushButton("Details")
        details_btn.setObjectName("activityTextAction")
        details_btn.clicked.connect(
            lambda _=False, it=item: list_widget.itemDoubleClicked.emit(
                it, 0))
        row.addWidget(details_btn)
    return wrap


def _corrupt_journal_row_widget(list_widget, item, error):
    """One corrupt-journal error row (Screen 3 Activity).

    Red dot, one line (the cause), quiet Details beside it.
    Details fires the list's own double-click signal for the
    item, so it opens the same repair dialog the double-click
    opens — this module never wires a window handler itself.

    Field-check fix #8 (U5 #33): the row names the file, not
    the path — "Your journal file is unreadable" plus where
    the repair lives (Details). The raw store error, with its
    absolute path and line number, stays on the item's
    UserRole data for the Details dialog: that dialog IS the
    manual-repair surface (its repair steps name the file to
    open), so the path belongs there, not on the reading row.
    The row stays one line: the short plain-words text never
    needs wrapping, and the instrument row re-elides it on
    resize instead of cutting it mid-word.
    """
    text = ("Your journal file is unreadable. "
            "Details shows how to repair it.")
    return activity_error_row_widget(
        list_widget, item, text, details=True)


def _journal_attention_row_widget(row):
    """One attention row as a severity-coded card (Phase 1, A-06/A-16).

    Severity is the journal class, carried by the edge + the
    StatusPill glyph — never a tinted fill, never coloured row
    text. Unknown (dispatched without an outcome) = high / amber
    edge + warning pill; retryable with declared compensations =
    medium / LINE_STRONG edge + neutral pill. The evidence text
    is the same verbatim journal_row_text lines, split across
    labels; the head line re-elides like every other row.
    """
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

    from .design_system.pills import StatusPill
    from .style import AMBER, LINE_STRONG
    from .task_state import (
        instrument_row,
        instrument_row_title,
    )

    unknown = row.get("class") == "unknown"
    wrap = instrument_row()
    wrap.setObjectName("activityRow")
    edge = AMBER if unknown else LINE_STRONG
    wrap.setStyleSheet(f"border: 1px solid {edge};")
    outer = QVBoxLayout(wrap)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(2)
    lines = journal_row_text(row).split("\n")
    head = QLabel(lines[0] if lines else "")
    head.setObjectName("activityRowValue")
    head.setWordWrap(False)
    instrument_row_title(wrap, head, lines[0] if lines else "")
    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(8)
    pill = StatusPill(text="Unknown" if unknown else "Retryable",
                      severity="warning" if unknown else "neutral")
    top.addWidget(pill)
    top.addWidget(head, stretch=1)
    outer.addLayout(top)
    for line in lines[1:]:
        if not line:
            continue
        lbl = QLabel(line)
        lbl.setObjectName("muted")
        lbl.setWordWrap(True)
        outer.addWidget(lbl)
    return wrap


def refresh_journal_list(list_widget, *, label_widget=None,
                         store_path=None):
    """Fill a QListWidget with attention rows (Activity fill).

    Qt caller; returns the ``journal_attention`` result (or an
    unavailable marker on unexpected failure — never raises
    into the Activity refresh). Corruption renders ONE error
    line (red dot, one line, quiet Details); its UserRole data
    carries the repair steps for the Details dialog. Rows carry
    the row dict on UserRole for selection + reconcile.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem

    from .task_state import list_item_size_hint

    try:
        result = journal_attention(store_path=store_path)
    except Exception as exc:  # noqa: BLE001 — the Activity
        list_widget.clear()  # refresh must never break on
        # Field-check fix #8 (U5 #32): the unexpected-failure
        # row is a §2f error-line widget like the corrupt row,
        # not plain text — and its cause never carries the
        # absolute path the store error may hold.
        from .room_activity import error_line, strip_absolute_paths

        _unavail_item = QListWidgetItem()
        list_widget.addItem(_unavail_item)
        _unavail_widget = activity_error_row_widget(
            list_widget, _unavail_item, error_line(
                "Journal is unavailable right now.",
                strip_absolute_paths(str(exc)) or None,
                "Reopen Activity to try again"))
        _unavail_item.setSizeHint(list_item_size_hint(_unavail_widget))
        list_widget.setItemWidget(_unavail_item, _unavail_widget)
        if label_widget is not None:
            try:
                label_widget.setText("Journal")
            except Exception:  # noqa: BLE001 — label is cosmetic
                pass
        return {"ok": False, "error": str(exc), "repair_steps": [],
                "unexpected": True}
    list_widget.clear()
    if not result.get("ok"):
        # The row is a corrupt-store marker. The item carries NO
        # text: QListWidget paints an item's text behind its
        # widget, and the widget's line sits a dot-width to the
        # right of where the delegate paints — so text on the
        # item double-paints as a smeared line (caught by the
        # Screen 3 render check). The text lives on the widget
        # (elided to one line); the item still carries the
        # corrupt marker + repair steps on UserRole.
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole,
                     {"corrupt": True,
                      "error": result.get("error", ""),
                      "repair_steps": result.get("repair_steps", [])})
        list_widget.addItem(item)
        # Screen 3 Activity: the corrupt row is an error line,
        # not a loud paragraph — a red dot, one line, and a quiet
        # Details beside it that opens the repair dialog (the
        # list's own double-click handler). The item carries no
        # text (see refresh_journal_list), so the widget's line
        # is the row's only text.
        widget = _corrupt_journal_row_widget(
            list_widget, item, str(result.get("error", "")))
        item.setSizeHint(list_item_size_hint(widget))
        list_widget.setItemWidget(item, widget)
        if label_widget is not None:
            label_widget.setText("Journal — unreadable")
        return result
    rows = result.get("rows") or []
    # Phase 1 (A-06): the section shows attention rows only —
    # unknown first, then retryable with declared compensations.
    # Done rows leave the section (they live in History / Mission
    # Log); the reconciled count is the footer, set by the
    # Activity refresh from counts (which cover ALL entries).
    # The header count is owned by the Activity refresh too
    # (attention rows + pending approvals, one read) — this
    # function no longer writes the counted header.
    attention = [row for row in rows
                 if row.get("class") == "unknown"
                 or row.get("compensation_status") == "declared"]
    result["rows"] = attention
    if not attention:
        # Activity empty-state fix (2026-11-26): no empty
        # sentence here. "Nothing needs attention right now."
        # was the fifth "nothing" on the Activity page (headline
        # + "No active mission" + the rail's line + the "(0)"
        # count + this one); the journal header + footer carry
        # the state instead, and the list simply stays empty
        # (the Activity refresh hides it while it has no rows).
        return result
    for row in attention:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, row)
        list_widget.addItem(item)
        widget = _journal_attention_row_widget(row)
        item.setSizeHint(list_item_size_hint(widget))
        list_widget.setItemWidget(item, widget)
    return result


def present_reconcile(window, row, *, store_path=None, refresh=None):
    """Reconcile one row: facts dialog, then EXISTING
    authorization dialog, then reconcile on a worker thread.

    GUI thread only for the dialogs. Returns True when the
    flow reached the worker (approval given); False on input
    cancel, authorization denial/close/silence, or an unusable
    row — all of which write NOTHING. The worker never breaks
    the GUI; ``refresh`` (the Activity refresh) runs when it
    lands, so the row clears itself once reconciled.
    """
    import threading

    from PySide6.QtCore import QTimer

    from voice_assistant.gui.pending_approval import (
        present_pending_dialog)

    row = row if isinstance(row, dict) else {}
    entry_id = str(row.get("entry_id") or "")
    if not entry_id or row.get("class") != "unknown":
        return False
    answers = ask_reconcile_input(window, row)
    if answers is None:
        return False
    happened, verification = answers
    tool = str(row.get("tool", ""))
    evidence = str(row.get("evidence", ""))
    sentence = (f"Reconcile: {tool} — {evidence}. Effect "
                f"{'confirmed happened' if happened else 'verified absent'}; "
                f"verified by user: {verification}")
    pending = {"tool": "journal_action",
               "label": f"Reconcile journal entry {entry_id[:8]}",
               "sentence": sentence,
               "confirm_arg": "confirmed"}
    try:
        approved = present_pending_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken popup denies,
        approved = False  # never allows.
    if not approved:
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001 — refresh never
                pass  # breaks the reconcile flow.
        return False
    done = threading.Event()

    def _work():
        try:
            reconcile_entry(entry_id, happened, verification,
                            store_path=store_path)
        except Exception:  # noqa: BLE001 — the worker never
            pass  # breaks the GUI; refresh shows truth.
        finally:
            done.set()

    threading.Thread(target=_work, daemon=True).start()
    timer = QTimer(window)
    timer.setSingleShot(False)

    def _tick():
        if done.is_set():
            try:
                timer.stop()
            except Exception:  # noqa: BLE001 — teardown never
                pass  # raises.
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001 — refresh
                    pass  # never breaks the flow.

    timer.timeout.connect(_tick)
    timer.start(500)
    return True


def compensable_row(row):
    """Pure predicate for the Compensate button: a row with an
    entry id and declared compensations. The window updater
    and present_compensate share this gate, so the button
    never invites what the flow refuses."""
    row = row if isinstance(row, dict) else {}
    return (bool(row.get("entry_id"))
            and row.get("compensation_status") == "declared")


def _funnel_compensate_leg():
    """The EXISTING journal_action compensate leg, bound.

    Lazy import (GUI convention): the registry singleton owns
    the leg, so each step runs through the funnel executor
    (gate stack + its own receipt per step). No new executor
    is invented here — that was batch 9's scoping cut, and
    this wiring closes it by using the leg itself.
    """
    from voice_assistant.core.gui_facade import tools

    return tools.journal_action


def show_compensate_outcome(window, result):
    """Show the compensate leg's outcome string verbatim.

    GUI thread only. The leg's string IS the outcome record
    (complete / stopped / no-undo / refusal); the surface
    never rewords it into a success, and the Activity refresh
    that follows shows the store truth (each step's own
    receipt as fresh rows). A broken dialog never breaks the
    flow — refresh still runs.
    """
    try:
        try:
            from gui.design_system.dialogs import HudDialog
        except ImportError:
            from voice_assistant.gui.design_system.dialogs import (  # type: ignore
                HudDialog)
        HudDialog.details(window, "Compensation result",
                          "Compensation result", str(result))
    except Exception:  # noqa: BLE001 — outcome display is a
        pass  # courtesy; refresh still shows truth.


def present_compensate(window, row, *, store_path=None, refresh=None,
                       leg=None):
    """Compensate one row: EXISTING authorization dialog, then
    the funnel compensate leg on a worker thread.

    GUI thread only for the dialog. Returns True when the
    flow reached the worker (approval given); False on a
    non-compensable row, authorization denial/close/silence,
    or a broken dialog — all of which run NOTHING. The
    worker never breaks the GUI; when it lands, the leg's
    outcome string is shown verbatim and ``refresh`` (the
    Activity refresh) runs, so the fresh store read shows
    each step's own receipt.

    Gate, stated plainly: the authorization dialog IS the
    confirm for this user-initiated action (batch-7
    presenter, same S-1 contract: Allow once / Don't allow,
    60 s silence = deny) — ``confirmed`` is set ONLY from
    its explicit Allow click, mirroring present_reconcile.
    That is not a bypass: the dialog is the same S-1 builder
    the funnel gate handler shows, and each compensation
    step then runs through the funnel executor, so a
    confirm-gate step pops its own dialog independently.
    A spoken "yes" never reaches this path (C-14): no voice
    caller exists, pinned by test. ``leg`` is a test seam
    only (a recording fake); production always resolves the
    funnel leg, after approval, never before.
    """
    row = row if isinstance(row, dict) else {}
    entry_id = str(row.get("entry_id") or "")
    if not entry_id or not compensable_row(row):
        return False
    from voice_assistant.gui.pending_approval import (
        present_pending_dialog)

    tool = str(row.get("tool", ""))
    evidence = str(row.get("evidence", ""))
    steps = row.get("compensations") or []
    plan_lines = "; ".join(
        f"{s.get('tool', '')} {s.get('note') or ''}".rstrip()
        for s in steps) or "no steps listed"
    sentence = (f"Compensate: {tool} — {evidence}. Undo in "
                f"reverse order: {plan_lines}. Each step is "
                f"verified; the first failure stops the run.")
    pending = {"tool": "journal_action",
               "label": f"Compensate journal entry {entry_id[:8]}",
               "sentence": sentence,
               "confirm_arg": "confirmed"}
    try:
        approved = present_pending_dialog(window, pending)
    except Exception:  # noqa: BLE001 — a broken popup denies,
        approved = False  # never allows.
    if not approved:
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001 — refresh never
                pass  # breaks the compensate flow.
        return False
    if leg is None:
        leg = _funnel_compensate_leg()
    import threading

    from PySide6.QtCore import QTimer

    done = threading.Event()
    outcome = {}

    def _work():
        try:
            outcome["text"] = leg(
                "compensate", receipt_id=entry_id,
                store_path=store_path, confirmed=True,
                confirmed_by="user")
        except Exception as exc:  # noqa: BLE001 — the worker
            outcome["text"] = (  # never breaks the GUI; the
                f"Compensation could not run: {exc}")
        finally:
            done.set()

    threading.Thread(target=_work, daemon=True).start()
    timer = QTimer(window)
    timer.setSingleShot(False)

    def _tick():
        if done.is_set():
            try:
                timer.stop()
            except Exception:  # noqa: BLE001 — teardown never
                pass  # raises.
            show_compensate_outcome(window,
                                    outcome.get("text", ""))
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001 — refresh
                    pass  # never breaks the flow.

    timer.timeout.connect(_tick)
    timer.start(500)
    return True
