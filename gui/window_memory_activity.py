"""MainWindow memory mixin: Activity tabs, journal reconcile /
compensate, plan / routine rows.

Split from gui/window_memory.py (GUI split #12 batch 2, move only).
"""
import asyncio
import datetime
import threading
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import confirm_gate
from voice_assistant.core.gui_facade import live_session
from .room_activity import (
    error_line,
    headline_counts,
    headline_line,
    strip_absolute_paths,
)
from .task_state import (
    add_task_row,
    instrument_row,
    instrument_row_title,
    list_item_size_hint,
    render_task,
)


def _cap_activity_list(lst, rows):
    """Cap a content-sized Activity list at N rows (Build 2).

    The lists size to their rows so the pane hugs the content;
    without a cap a long ledger would push the pane past the
    window. Past the cap the list scrolls inside it. A broken
    row hint never breaks the refresh — the list just keeps
    its previous cap.
    """
    if lst is None:
        return
    try:
        count = lst.count()
        if not count:
            # Empty: release any earlier cap, or the empty list
            # keeps a stale height from its last content.
            lst.setMinimumHeight(0)
            lst.setMaximumHeight(16777215)
            return
        row_h = max(lst.sizeHintForRow(i) for i in range(count))
        # The list is exactly its content tall (capped): the
        # minimum stops it stretching to fill the pane's slack,
        # so the pane ends after the last row instead of after
        # a stretched list.
        lst.setMinimumHeight(row_h * min(count, rows) + 2)
        lst.setMaximumHeight(row_h * rows + 2)
    except Exception:  # noqa: BLE001 — cap is cosmetic
        pass


class MemoryActivityMixin:
    """Part-mixin (GUI split #12): method bodies moved verbatim
    from gui/window_memory.py; behaviour is unchanged.
    """

    def _update_activity_headline(self, section, line):
        """Fill one Activity headline line (Build 2: a single
        label, not a figure + comparison)."""
        headlines = getattr(self, "_activity_headlines", None) or {}
        entry = headlines.get(section)
        if entry is None:
            return
        entry["line"].setText(line or "")

    def _refresh_activity_tabs(self):
        """Fill the Activity page's In progress / History tabs
        from the durable task ledger (core/brain/task_ledger.py — the
        same store the coordinator writes). In progress = stored
        running/paused tasks; History = terminal tasks
        (completed/failed/cancelled), newest first. No-op until
        the Activity page has been built; an unreadable ledger
        shows an honest unavailable row, never a fake list."""
        in_progress = getattr(self, "_activity_in_progress_list", None)
        history = getattr(self, "_activity_history_list", None)
        if in_progress is None or history is None:
            return
        records = None
        load_error = ""
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/

            records = TaskLedger().all_records()
        except Exception as exc:  # noqa: BLE001 — the page still builds
            records = None
            load_error = str(exc)
        in_progress.clear()
        history.clear()
        if records is None:
            # Screen 3 Activity: one line, one cause, one
            # recovery — as §2f error-line WIDGETS (red dot +
            # one line), like the corrupt journal row.
            # Field-check fix #8 (U5 #32): the uis3 allowlist
            # now permits widget rows built from an empty item
            # (see tests/test_uis3.py) — the reading lives on
            # the widget, so the item itself carries no text.
            # The cause never carries the absolute ledger path
            # the store error holds (strip_absolute_paths).
            from voice_assistant.gui.journal_surface import (
                activity_error_row_widget)
            why = strip_absolute_paths(load_error) or None
            for _lst, _what in (
                    (in_progress,
                     "Task history is unavailable right now."),
                    (history,
                     "Task history is unavailable right now.")):
                _item = QListWidgetItem()
                _lst.addItem(_item)
                _widget = activity_error_row_widget(
                    _lst, _item, error_line(
                        _what, why, "Reopen Activity to try again"))
                _item.setSizeHint(list_item_size_hint(_widget))
                _lst.setItemWidget(_item, _widget)
            _ml_unavail = getattr(self, "_mission_log_list", None)
            if _ml_unavail is not None:
                _ml_unavail.clear()
                _item = QListWidgetItem()
                _ml_unavail.addItem(_item)
                _widget = activity_error_row_widget(
                    _ml_unavail, _item, error_line(
                        "Mission log is unavailable right now.",
                        why, "Reopen Activity to try again"))
                _item.setSizeHint(list_item_size_hint(_widget))
                _ml_unavail.setItemWidget(_item, _widget)
            return
        live = [r for r in records
                if str(r.get("state", "")) in ("running", "paused")]
        done = [r for r in records
                if str(r.get("state", "")) in ("completed", "failed",
                                                "cancelled")]
        live.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        done.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        # Screen 3 Activity: the headline lines, from the same
        # ledger read — one line per section, no second read.
        # The counts need newest-first order for the latest
        # finished title, so they read the sorted combined list.
        _ordered = sorted(records,
                          key=lambda r: str(r.get("updated_at", "")),
                          reverse=True)
        _counts = headline_counts(_ordered)
        for _section in ("in_progress", "history", "mission_log"):
            self._update_activity_headline(
                _section, headline_line(_section, _counts))
        # UI Slice 3 (complexity doc CX-05): rows render through
        # the single task-state component (gui/task_state.py) —
        # no per-tab state text anymore. The live pending
        # confirmation (if any) is attributed to the FIRST live
        # task positioned at its tool; every other row renders
        # without the hint, so one dialog is never claimed by
        # two rows. A pending request for a non-task execution
        # matches no row and claims nothing.
        pending_tool = pending_summary = None
        try:
            _pend = confirm_gate.pending_request()
            if _pend is not None:
                _, pending_tool, pending_summary, _ = _pend
        except Exception:  # noqa: BLE001 — gate unreadable: no hint
            pending_tool = pending_summary = None
        pending_used = False
        if not live:
            in_progress.addItem("Nothing in progress right now.")
        for r in live:
            # §7 consumers batch 7: a paused row carrying its own
            # coordinator pending approval renders Needs approval
            # from its record and must NOT consume the live gate
            # hint — the hint belongs to a running row positioned
            # at the gated tool, never to a row that already
            # names its own question.
            coord_pending = (
                str(r.get("state", "")) == "paused"
                and isinstance(r.get("pending_approval"), dict)
                and bool(r.get("pending_approval", {}).get("tool")))
            hint_tool = hint_summary = None
            if (pending_tool is not None and not pending_used
                    and not coord_pending):
                hint_tool, hint_summary = pending_tool, pending_summary
            rendered = render_task(r, pending_tool=hint_tool,
                                   pending_summary=hint_summary)
            if (hint_tool is not None
                    and rendered["state"] == "Needs approval"):
                pending_used = True
            add_task_row(in_progress, rendered, parent=in_progress)
        # §7 consumers batch 7: the recovery offer's visibility
        # + count, refreshed with the rows above. The button
        # itself is built with the Activity page; until then
        # this is a guarded no-op.
        _pending_btn = getattr(self, "_activity_pending_btn", None)
        if _pending_btn is not None:
            _n_pending = sum(
                1 for r in live
                if str(r.get("state", "")) == "paused"
                and isinstance(r.get("pending_approval"), dict)
                and bool(r.get("pending_approval", {}).get("tool")))
            _pending_btn.setVisible(_n_pending > 0)
            _pending_btn.setText(
                f"Review pending approvals ({_n_pending})"
                if _n_pending else "Review pending approvals")
        # B15: the question offer's visibility + count,
        # refreshed with the rows above, same shape as the
        # approvals offer. Hidden while no question is
        # pending.
        _question_btn = getattr(self, "_activity_question_btn", None)
        if _question_btn is not None:
            _n_questions = sum(
                1 for r in live
                if str(r.get("state", "")) == "paused"
                and isinstance(r.get("pending_question"), dict)
                and bool(r.get("pending_question", {}).get("question")))
            _question_btn.setVisible(_n_questions > 0)
            _question_btn.setText(
                f"Answer pending questions ({_n_questions})"
                if _n_questions else "Answer pending questions")
        # B21: the reconcile offer's visibility + count,
        # refreshed with the rows above, same shape as the
        # approvals and questions offers. Hidden while no
        # reconcile decision is pending.
        _reconcile_btn = getattr(self, "_activity_reconcile_btn", None)
        if _reconcile_btn is not None:
            _n_reconciles = sum(
                1 for r in live
                if str(r.get("state", "")) == "paused"
                and isinstance(r.get("pending_reconcile"), dict)
                and bool(r.get("pending_reconcile", {}).get("question")))
            _reconcile_btn.setVisible(_n_reconciles > 0)
            _reconcile_btn.setText(
                f"Decide what happened ({_n_reconciles})"
                if _n_reconciles else "Decide what happened")
        # B16: the recovery offer's visibility + count,
        # refreshed with the rows above, same shape as the
        # two offers. It covers every interrupted record
        # the other two offers do not: plain paused,
        # budget-paused, monitor-paused, crash-interrupted
        # (running), and goal records — computed from the
        # same ledger read, so no second read is needed.
        # A record with its own approval/question offer is
        # never counted here (the queues never mix).
        self._update_activity_recovery_offer(records)
        # UI rethink Build 2: the pending row is quiet text —
        # every visible queue is the same kind of thing, so none
        # shouts. The first queue in the fixed order (approval,
        # question, reconcile, recovery) comes first; hidden
        # queues keep their chip weight so nothing else changes.
        # isHidden (not isVisible) is the check: the refresh also
        # runs while the window is still hidden, and isVisible
        # would read every button as hidden then.
        _pending_order = [
            _pending_btn, _question_btn, _reconcile_btn,
            getattr(self, "_activity_recovery_btn", None)]
        for _btn in _pending_order:
            if _btn is None:
                continue
            if not _btn.isHidden():
                _btn.setObjectName("activityTextAction")
            else:
                _btn.setObjectName("chipBtn")
            _btn.style().unpolish(_btn)
            _btn.style().polish(_btn)
        # §7 consumers batch 9: the journal reconcile surface
        # fills from the receipt store on every refresh (fresh
        # read, never a stale table). Guarded like the pending
        # button: a broken journal never breaks the Activity
        # page — it renders its own loud row instead.
        _journal_list = getattr(self, "_activity_journal_list", None)
        if _journal_list is not None:
            try:
                from voice_assistant.gui.journal_surface import (
                    refresh_journal_list)
                refresh_journal_list(
                    _journal_list,
                    label_widget=getattr(
                        self, "_activity_journal_label", None))
            except Exception as exc:  # noqa: BLE001 — the page still
                try:  # builds; the journal shows unavailable.
                    _journal_list.clear()
                    _journal_list.addItem("● " + error_line(
                        "Journal is unavailable right now.",
                        str(exc) or None,
                        "Reopen Activity to try again"))
                except Exception:  # noqa: BLE001 — never raises
                    pass  # into the refresh.
            # Build 2: the journal rows changed, so the contextual
            # buttons and the hint line re-derive from the fresh
            # list (the selection signal may not fire when nothing
            # was selected before).
            self._update_journal_reconcile_btn()
            self._update_journal_compensate_btn()
            self._update_journal_hint()
        if not done:
            history.addItem("No finished tasks yet.")
        for r in done:
            add_task_row(history, render_task(r), parent=history)
        # Mission Log (restore 2026-11-01): the same records
        # through the same task-state component, filtered by the
        # tab's state segments + time window, newest first. The
        # rendered dict rides on each row (UserRole) so
        # selection can show its evidence trace. "Running"
        # covers the Needs approval variant (a live task
        # awaiting approval); Queued / Unknown rows show under
        # All only. Unparseable / missing updated_at stamps are
        # included, never hidden by the time filter.
        mission_log = getattr(self, "_mission_log_list", None)
        if mission_log is not None:
            mission_log.clear()
            state_filter = getattr(self, "_mission_log_state", "All")
            days = getattr(self, "_mission_log_days", 0)
            cutoff = None
            if days:
                cutoff = (datetime.datetime.now(datetime.timezone.utc)
                          - datetime.timedelta(days=days))
            shown = 0
            ordered = sorted(records,
                             key=lambda r: str(r.get("updated_at", "")),
                             reverse=True)
            for r in ordered:
                rendered = render_task(r)
                state = rendered.get("state") or "Unknown"
                if state_filter == "Running":
                    if state not in ("Running", "Needs approval"):
                        continue
                elif state_filter != "All" and state != state_filter:
                    continue
                if cutoff is not None:
                    try:
                        stamp = datetime.datetime.fromisoformat(
                            str(r.get("updated_at", "")))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(
                                tzinfo=datetime.timezone.utc)
                        if stamp < cutoff:
                            continue
                    except Exception:  # noqa: BLE001 — include
                        pass
                add_task_row(mission_log, rendered, parent=mission_log)
                item = mission_log.item(mission_log.count() - 1)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, rendered)
                shown += 1
            if not shown:
                mission_log.addItem("No missions match this filter.")
        # Screen 3 Activity: the Scheduled headline reads the
        # reminder store, not the ledger — refresh it on every
        # Activity show so the line is never stale. The ledger
        # tabs above are already filled; a broken reminder read
        # never clears them.
        try:
            self._refresh_reminders()
        except Exception:  # noqa: BLE001
            pass
        # UI rethink Build 2: the lists cap at a few rows so a
        # long ledger cannot push the pane past the window, and
        # the pane re-hugs the current tab's content now that the
        # row counts have changed.
        _cap_activity_list(in_progress, 6)
        _cap_activity_list(history, 6)
        _cap_activity_list(mission_log, 5)
        _cap_activity_list(self._activity_journal_list, 6)
        try:
            self._fit_activity_tabs_height()
            # The lists' size hints settle on the next event-loop
            # turn (a freshly filled list reports a stale hint
            # until its layout runs); re-fit then so the pane
            # measures the settled height, not the stale one.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0, self._fit_activity_tabs_height_later)
        except Exception:  # noqa: BLE001 — the fit is cosmetic;
            pass  # the pane just keeps its previous height.

    def _review_pending_approvals(self):
        """Offer the authorization dialog for each interrupted task
        carrying a coordinator pending approval (§7 consumers
        batch 7 — the startup-recovery + live-session offer).

        The pending list is re-read fresh from the ledger at
        click time (never the last refresh's snapshot, so an
        answer given elsewhere cannot be re-offered). The
        queue itself lives in gui/pending_approval (dialog on
        the GUI thread, resolve on a worker thread, refresh
        when done); this method only gathers the offers.
        """
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/
            from voice_assistant.gui.pending_approval import (
                interrupted_pending_approvals, review_pending_queue)
            queue = interrupted_pending_approvals(
                TaskLedger().list_interrupted())
        except Exception:  # noqa: BLE001 — the offer is a
            return  # courtesy; a broken ledger never breaks chat.
        if not queue:
            return
        review_pending_queue(
            self, queue,
            live_coordinator=getattr(self, "_task_coordinator", None),
            ledger=TaskLedger(),
            refresh=self._refresh_activity_tabs)

    def _review_pending_questions(self):
        """Offer the question dialog for each interrupted task
        carrying a coordinator pending question (B15 — the
        startup-recovery + live-session offer).

        The pending list is re-read fresh from the ledger at
        click time (never the last refresh's snapshot, so an
        answer given elsewhere cannot be re-offered). The
        queue itself lives in gui/pending_question (dialog on
        the GUI thread, resolve on a worker thread, refresh
        when done); this method only gathers the offers. A
        question is not an approval, so this is a separate
        queue from _review_pending_approvals — the two never
        mix.
        """
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/
            from voice_assistant.gui.pending_question import (
                interrupted_pending_questions, review_question_queue)
            queue = interrupted_pending_questions(
                TaskLedger().list_interrupted())
        except Exception:  # noqa: BLE001 — the offer is a
            return  # courtesy; a broken ledger never breaks chat.
        if not queue:
            return
        review_question_queue(
            self, queue,
            live_coordinator=getattr(self, "_task_coordinator", None),
            ledger=TaskLedger(),
            refresh=self._refresh_activity_tabs)

    def _review_pending_reconciles(self):
        """Offer the reconcile dialog for each interrupted task
        carrying a coordinator pending reconcile (B21 — the
        crash-safe re-run offer).

        The pending list is re-read fresh from the ledger at
        click time (never the last refresh's snapshot, so a
        decision made elsewhere cannot be re-offered). The
        queue itself lives in gui/pending_reconcile (dialog
        on the GUI thread, resolve on a worker thread,
        refresh when done); this method only gathers the
        offers. A reconcile decision is not an answer and
        not an approval, so this is a separate queue from
        _review_pending_approvals and _review_pending_
        questions — the three never mix.
        """
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/
            from voice_assistant.gui.pending_reconcile import (
                interrupted_pending_reconciles,
                review_reconcile_queue)
            queue = interrupted_pending_reconciles(
                TaskLedger().list_interrupted())
        except Exception:  # noqa: BLE001 — the offer is a
            return  # courtesy; a broken ledger never breaks chat.
        if not queue:
            return
        review_reconcile_queue(
            self, queue,
            live_coordinator=getattr(self, "_task_coordinator", None),
            ledger=TaskLedger(),
            refresh=self._refresh_activity_tabs)

    def _review_recovery(self):
        """Offer Resume / Not now / Abandon for each interrupted
        task or goal the other two offers do not cover (B16).

        The offer list is re-read fresh from the ledger at
        click time (never the last refresh's snapshot, so a
        task resumed or abandoned elsewhere cannot be
        re-offered). The queue itself lives in
        gui/pending_recovery (dialog on the GUI thread,
        resume/abandon on a worker thread, refresh when
        done); this method only gathers the offers. A
        recovery offer is neither an approval nor a
        question, so this is a separate queue from both —
        the three never mix.
        """
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/
            from voice_assistant.gui.pending_recovery import (
                review_recovery_queue, startup_recovery_check)
            ledger = TaskLedger()
            queue = startup_recovery_check(ledger)
        except Exception:  # noqa: BLE001 — the offer is a
            return  # courtesy; a broken ledger never breaks chat.
        if not queue:
            return
        review_recovery_queue(
            self, queue,
            live_coordinator=getattr(self, "_task_coordinator", None),
            ledger=ledger,
            refresh=self._refresh_activity_tabs)

    def _update_activity_recovery_offer(self, records) -> None:
        """Recovery offer button + count from one ledger read.

        B16: the offer's visibility + count, same shape as the
        other queues. It covers every interrupted record the
        other two offers do not: plain paused, budget-paused,
        monitor-paused, crash-interrupted (running), and goal
        records — computed from the same ledger read, so no
        second read is needed. A record with its own
        approval/question offer is never counted here (the
        queues never mix). Guarded no-op until the button
        exists; a broken count hides the offer, never the page.
        """
        _recovery_btn = getattr(self, "_activity_recovery_btn", None)
        if _recovery_btn is None:
            return
        try:
            from voice_assistant.gui.pending_recovery import (
                is_recoverable_goal, is_recoverable_task)
            _n_recovery = sum(
                1 for r in records
                if is_recoverable_task(r) or is_recoverable_goal(r))
        except Exception:  # noqa: BLE001 — a broken count
            _n_recovery = 0  # hides the offer, never the page.
        _recovery_btn.setVisible(_n_recovery > 0)
        _recovery_btn.setText(
            f"Resume interrupted tasks ({_n_recovery})"
            if _n_recovery else "Resume interrupted tasks")

    def _run_startup_recovery_check(self) -> int:
        """Startup recovery check (B16): read the ledger's
        interrupted records fresh after launch and refresh
        the Activity offer state. Returns the number of
        recovery offers (0 when none, or when the ledger
        cannot be read).

        Never pops a dialog, never navigates, never
        auto-runs anything — a restart is not consent to
        re-run a half-done goal. The offer lives on
        Activity (its button + count are refreshed here,
        so the state is live from launch, not only from
        the first Activity visit) until the user takes it.
        A broken ledger is silent here: the Activity
        refresh already shows its own unavailable row.

        R3F1: this updates the recovery OFFER only. It
        used to call the full _refresh_activity_tabs(),
        which rebuilds every Activity row — hiding the
        old rows and scheduling their deletion, and
        building new ones whose labels read the default
        9pt font until the next event-loop turn. That
        rebuild fired 1.5 s after launch, in the middle
        of whatever the user — or a test — was doing
        with those rows: a held row reference died on
        the next pump, and a freshly built row measured
        a fifth font size (the scifi_activity Qt flake).
        The rows are already built from the same ledger
        at page construction and rebuilt on every
        Activity show, so the offer is the only state
        this check owns.
        """
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/
            from voice_assistant.gui.pending_recovery import (
                startup_recovery_check)
            ledger = TaskLedger()
            offers = startup_recovery_check(ledger)
        except Exception:  # noqa: BLE001 — the offer is a
            return 0  # courtesy; never breaks startup.
        try:
            self._update_activity_recovery_offer(
                ledger.all_records())
        except Exception:  # noqa: BLE001 — the offer is
            pass  # a courtesy; never breaks startup.
        return len(offers)

    def _update_journal_reconcile_btn(self):
        """Show Reconcile only for a selected dispatched-
        without-outcome journal row (§7 consumers batch 9, Build 2:
        the button appears on selection instead of sitting dimmed).

        The engine refuses anything else, but the button must
        not invite it: any other selection (done entry, retry
        candidate, corrupt row, plain-text row) hides it.
        Guarded no-op until the journal list exists.
        """
        btn = getattr(self, "_activity_journal_reconcile_btn", None)
        lst = getattr(self, "_activity_journal_list", None)
        if btn is None or lst is None:
            return
        from PySide6.QtCore import Qt

        show = False
        try:
            items = lst.selectedItems()
            if items:
                row = items[0].data(Qt.ItemDataRole.UserRole)
                show = (isinstance(row, dict)
                        and row.get("class") == "unknown"
                        and bool(row.get("entry_id")))
        except Exception:  # noqa: BLE001 — a broken selection
            show = False  # never shows the button.
        btn.setVisible(show)
        self._update_journal_hint()

    def _update_journal_hint(self):
        """The one hint line naming the journal actions (Build 2).

        Shows while an actionable journal row exists and nothing
        is selected — the capability is never hidden, but no
        dimmed button pretends it is available. Hidden when there
        is nothing to act on, or while a selection (and its
        button) is showing. Guarded no-op until the journal
        section exists.
        """
        hint = getattr(self, "_activity_journal_hint", None)
        lst = getattr(self, "_activity_journal_list", None)
        if hint is None or lst is None:
            return
        from PySide6.QtCore import Qt

        from voice_assistant.gui.journal_surface import (
            compensable_row)
        actionable = False
        try:
            for i in range(lst.count()):
                row = lst.item(i).data(Qt.ItemDataRole.UserRole)
                if not isinstance(row, dict):
                    continue
                if ((row.get("class") == "unknown"
                     and row.get("entry_id"))
                        or compensable_row(row)):
                    actionable = True
                    break
        except Exception:  # noqa: BLE001 — a broken list shows
            actionable = False  # no hint, never the page.
        reconcile = getattr(
            self, "_activity_journal_reconcile_btn", None)
        compensate = getattr(
            self, "_activity_journal_compensate_btn", None)
        any_button = (
            (reconcile is not None and not reconcile.isHidden())
            or (compensate is not None
                and not compensate.isHidden()))
        try:
            selected = bool(lst.selectedItems())
        except Exception:  # noqa: BLE001 — no selection read
            selected = False
        hint.setVisible(actionable and not selected and not any_button)

    def _show_journal_row_details(self, item):
        """Details dialog for one journal row (§7 batch 9).

        Evidence verbatim; a corrupt row shows the error +
        the manual repair path, never a fake detail. Guarded:
        a broken dialog never breaks the Activity page.
        """
        try:
            from PySide6.QtCore import Qt

            from voice_assistant.gui.journal_surface import (
                journal_row_details)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("corrupt"):
                title = "Journal unreadable"
                details = (f"{data.get('error', '')}\n\n"
                           "Manual repair path (no automatic "
                           "repair):\n"
                           + "\n".join(
                               f"- {s}"
                               for s in data.get("repair_steps")
                               or []))
            elif isinstance(data, dict) and data.get("entry_id"):
                title = (f"Journal entry — "
                         f"{data.get('tool', '')}")
                details = journal_row_details(data)
            else:
                return
            try:
                from gui.design_system.dialogs import HudDialog
            except ImportError:
                from voice_assistant.gui.design_system.dialogs import (  # type: ignore
                    HudDialog)
            HudDialog.details(self, title, title, details)
        except Exception:  # noqa: BLE001 — details are a
            return  # courtesy; never break the page.

    def _reconcile_selected_journal_entry(self):
        """Reconcile the selected journal row (§7 batch 9).

        Facts dialog, then the EXISTING authorization dialog,
        then reconcile on a worker thread (gui/journal_surface
        owns the flow; this method only gathers the selection).
        A broken selection or a non-unknown row does nothing —
        the button enables only for unknown rows, and the
        engine refuses anything else anyway.
        """
        lst = getattr(self, "_activity_journal_list", None)
        if lst is None:
            return
        try:
            from PySide6.QtCore import Qt

            from voice_assistant.gui.journal_surface import (
                present_reconcile)
            items = lst.selectedItems()
            if not items:
                return
            row = items[0].data(Qt.ItemDataRole.UserRole)
            if (not isinstance(row, dict)
                    or row.get("class") != "unknown"
                    or not row.get("entry_id")):
                return
        except Exception:  # noqa: BLE001 — a broken selection
            return  # reconciles nothing.
        present_reconcile(self, row,
                          refresh=self._refresh_activity_tabs)

    def _update_journal_compensate_btn(self):
        """Show Compensate only for a selected row with
        declared compensations (§7 follow-up: journal
        Compensate button, Build 2: the button appears on
        selection instead of sitting dimmed).

        The engine refuses anything else, but the button must
        not invite it: any other selection (no declaration,
        corrupt row, plain-text row) hides it.
        Guarded no-op until the journal list exists.
        """
        btn = getattr(self, "_activity_journal_compensate_btn", None)
        lst = getattr(self, "_activity_journal_list", None)
        if btn is None or lst is None:
            return
        from PySide6.QtCore import Qt

        from voice_assistant.gui.journal_surface import (
            compensable_row)
        show = False
        try:
            items = lst.selectedItems()
            if items:
                row = items[0].data(Qt.ItemDataRole.UserRole)
                show = compensable_row(row)
        except Exception:  # noqa: BLE001 — a broken selection
            show = False  # never shows the button.
        btn.setVisible(show)
        self._update_journal_hint()

    def _compensate_selected_journal_entry(self):
        """Compensate the selected journal row (§7 follow-up).

        Authorization dialog, then the EXISTING funnel
        compensate leg on a worker thread (gui/journal_surface
        owns the flow; this method only gathers the selection).
        A broken selection or a row without declared
        compensations does nothing — the button enables only
        for declared rows, and the leg refuses anything else
        anyway.
        """
        lst = getattr(self, "_activity_journal_list", None)
        if lst is None:
            return
        try:
            from PySide6.QtCore import Qt

            from voice_assistant.gui.journal_surface import (
                compensable_row, present_compensate)
            items = lst.selectedItems()
            if not items:
                return
            row = items[0].data(Qt.ItemDataRole.UserRole)
            if not compensable_row(row):
                return
        except Exception:  # noqa: BLE001 — a broken selection
            return  # compensates nothing.
        present_compensate(self, row,
                           refresh=self._refresh_activity_tabs)

    def _plan_row(self, prog: dict) -> QWidget:
        """One Plan row (Screen 3 Activity): an instrument — the
        label, the goal as the one reading, done/total
        right-aligned in the mono face, and quiet text actions.
        The cursor, the interrupted flag, and the per-step status
        live in the Details dialog (one tap away), never on the
        row's face. Resume / Run keep their existing behaviour;
        a complete plan gets no Resume button. The plan id is
        not on the face either — it rides in the Details dialog
        and in the resume announcement.
        """
        plan_id = str(prog.get("plan_id") or "")
        goal = str(prog.get("goal") or "Untitled plan")
        done = int(prog.get("done") or 0)
        total = int(prog.get("total") or 0)
        cursor = prog.get("cursor")
        complete = bool(prog.get("complete"))
        steps = prog.get("steps") or []
        interrupted = any(
            isinstance(s, dict) and s.get("resumable") for s in steps)
        wrap = instrument_row()
        wrap.setObjectName("activityRow")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 8, 0, 8)
        h.setSpacing(12)
        label = QLabel("PLAN · INTERRUPTED"
                       if interrupted and not complete else "PLAN")
        label.setObjectName("activityRowLabel")
        h.addWidget(label)
        name_lbl = QLabel(goal)
        name_lbl.setObjectName("activityRowValue")
        name_lbl.setWordWrap(False)
        h.addWidget(name_lbl, stretch=1)
        instrument_row_title(wrap, name_lbl, goal)
        num_lbl = QLabel(f"{done}/{total}")
        num_lbl.setObjectName("activityRowNumber")
        num_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(num_lbl)
        if not complete:
            btn = QPushButton(
                "Resume" if (interrupted or done > 0) else "Run")
            btn.setObjectName("activityTextAction")
            btn.clicked.connect(
                lambda _=False, p=plan_id: self._resume_plan(p))
            self._plan_resume_buttons[plan_id] = btn
            h.addWidget(btn)
        details_btn = QPushButton("Details")
        details_btn.setObjectName("activityTextAction")
        details_btn.clicked.connect(
            lambda _=False, p=prog: self._show_plan_details(p))
        h.addWidget(details_btn)
        return wrap

    def _show_plan_details(self, prog: dict) -> None:
        """Plan details dialog: the steps with their status, plus
        the cursor and the plan id — the detail the row's face
        no longer carries.
        """
        try:
            from gui.design_system.dialogs import HudDialog
        except ImportError:
            from voice_assistant.gui.design_system.dialogs import HudDialog  # type: ignore
        plan_id = str(prog.get("plan_id") or "")
        goal = str(prog.get("goal") or "Untitled plan")
        lines = [f"Plan id: {plan_id}"]
        cursor = prog.get("cursor")
        if cursor is not None:
            lines.append(
                f"Next: step {cursor} of {prog.get('total') or 0}")
        for step in prog.get("steps") or []:
            if not isinstance(step, dict):
                continue
            mark = {"done": "✓", "skipped": "–"}.get(
                str(step.get("status") or ""), "·")
            lines.append(
                f"{mark} step {step.get('index')}: "
                f"{step.get('text') or ''}"
                f" ({step.get('status') or 'pending'})")
        HudDialog.details(
            self, f"Plan details — {goal}", f"{goal}\n{plan_id}",
            "\n".join(lines))

    def _resume_plan(self, plan_id: str):
        """v5.49 (programme item 32): run a plan through the
        EXISTING run path. Connected: the live session's inner
        client owns run_plan (each client's B15 driver entry —
        its emit already lands on _on_transcript, so the goal
        pill follows it). Not connected: planner.run_plan
        headless on a thread, exactly like _run_routine_headless,
        with emit bridged to the same pill."""
        if self.worker and self.worker.loop and live_session.is_connected:
            try:
                client = live_session.client()
            except Exception:  # noqa: BLE001
                client = None
            run = getattr(client, "run_plan", None)
            if callable(run):
                asyncio.run_coroutine_threadsafe(
                    run(str(plan_id)), self.worker.loop)
                self._add_system(
                    f"Resuming plan {plan_id} — finished steps "
                    "won't run again.")
                return
        threading.Thread(
            target=self._run_plan_headless, args=(str(plan_id),),
            daemon=True).start()
        self._add_system(
            f"Resuming plan {plan_id} — finished steps won't run "
            "again.")

    def _run_plan_headless(self, plan_id: str):
        try:
            from voice_assistant.core.gui_facade import planner as _planner  # Phase 3 B2-remainder: planner moved to core/brain/

            report = asyncio.run(_planner.run_plan(
                plan_id,
                emit=lambda line: self.signals.plan_progress_line.emit(
                    line)))
            summary = str(report or "")
        except Exception as exc:  # noqa: BLE001
            summary = f"Could not resume plan {plan_id}: {exc}"
        lines = summary.splitlines()
        last = lines[-1] if lines else summary
        self.signals.routine_result.emit(f"Plan {plan_id}: {last}")

    def _routine_row(self, routine: dict) -> QWidget:
        """One Routine row (Screen 3 Activity): an instrument —
        the label, the name as the one reading, the next run
        right-aligned in the mono face, and quiet text actions.
        The step count and the schedule text live in the schedule
        editor behind the Schedule toggle (one tap away), never
        on the row's face. Delete keeps its existing behaviour
        (one tap, no confirm) — only its weight changes.
        """
        name = str(routine.get("name", "") or "Untitled")
        actions = routine.get("actions") or routine.get("steps") or []
        count = len(actions) if isinstance(actions, (list, tuple)) else 0
        schedule_text = str(routine.get("schedule_text", "") or "").strip()

        wrap = QWidget()
        wrap.setObjectName("activityRow")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Line 1: label · name · next run · Run now / Schedule / Delete.
        header = instrument_row()
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 8, 0, 8)
        h.setSpacing(12)
        label = QLabel("ROUTINE")
        label.setObjectName("activityRowLabel")
        h.addWidget(label)
        name_lbl = QLabel(name)
        name_lbl.setObjectName("activityRowValue")
        name_lbl.setWordWrap(False)
        h.addWidget(name_lbl, stretch=1)
        instrument_row_title(header, name_lbl, name)
        nxt = self._routine_next_text(routine)
        if nxt:
            num_lbl = QLabel(nxt)
            num_lbl.setObjectName("activityRowNumber")
            num_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(num_lbl)
        btn_run = QPushButton("Run now")
        btn_run.setObjectName("activityTextAction")
        btn_run.clicked.connect(
            lambda _=False, r=dict(routine): self._on_routine_run_now(r))
        h.addWidget(btn_run)
        btn_schedule = QPushButton("Schedule")
        btn_schedule.setObjectName("activityTextAction")
        h.addWidget(btn_schedule)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("activityTextAction")
        btn_del.clicked.connect(
            lambda _=False, n=name: self._delete_routine_from_ui(n))
        h.addWidget(btn_del)
        v.addWidget(header)

        # Line 2: schedule editor — hidden until the Schedule
        # toggle opens it. The step count and schedule text live
        # here as a muted line above the edit controls, so the
        # face stays one reading. The actions handed to
        # save_routine are this record's own, captured from the
        # get_routines() dict the row was built from, so a
        # schedule-only edit never alters the routine's steps.
        saved_actions = routine.get("actions")
        saved_actions = list(saved_actions) if isinstance(saved_actions, list) else []
        editor = QWidget()
        e = QVBoxLayout(editor)
        e.setContentsMargins(0, 0, 0, 8)
        e.setSpacing(6)
        meta_lbl = QLabel(
            f"{count} step{'s' if count != 1 else ''} · "
            f"{schedule_text or 'on demand'}")
        meta_lbl.setObjectName("muted")
        e.addWidget(meta_lbl)
        edit_row = QHBoxLayout()
        edit_row.setContentsMargins(0, 0, 0, 0)
        edit_row.setSpacing(8)
        sched_edit = QLineEdit()
        sched_edit.setPlaceholderText(
            "daily 07:30 · interval 2h · once 2026-10-25 08:00")
        if schedule_text:
            sched_edit.setText(schedule_text)
        edit_row.addWidget(sched_edit, 1)
        btn_set = QPushButton("Set")
        btn_set.setObjectName("chipBtn")
        edit_row.addWidget(btn_set)
        btn_clear = None
        if schedule_text:
            btn_clear = QPushButton("Clear")
            btn_clear.setObjectName("chipBtn")
            edit_row.addWidget(btn_clear)
        e.addLayout(edit_row)

        # Hidden until a save is refused: the refusal shows here
        # and the row is NOT rebuilt, so the typed text survives
        # for fixing. It lives inside the editor, so a refusal
        # opens the editor itself — the refusal is never hidden
        # behind a closed toggle.
        feedback = QLabel("")
        feedback.setObjectName("muted")
        feedback.setWordWrap(True)
        feedback.setVisible(False)
        e.addWidget(feedback)
        editor.setVisible(False)
        v.addWidget(editor)

        btn_schedule.clicked.connect(
            lambda _=False, ed=editor: ed.setVisible(not ed.isVisible()))
        btn_set.clicked.connect(
            lambda _=False, n=name, a=saved_actions, ed=sched_edit,
            fb=feedback, editor=editor:
            self._set_routine_schedule_from_ui(n, a, ed, fb, editor))
        if btn_clear is not None:
            btn_clear.clicked.connect(
                lambda _=False, n=name: self._clear_routine_schedule_from_ui(n))
        return wrap

    def _set_routine_schedule_from_ui(self, name, actions, edit, feedback,
                                      editor=None):
        """Persist the schedule typed into a routine row's editor.

        Goes through planner.save_routine with the routine's existing
        actions, so the schedule is parsed/validated exactly like a
        voice-saved one. A refusal ("Could not save routine…") is
        shown in the row's feedback label, the editor is opened so
        the refusal is visible, and the rows are NOT refreshed —
        the user's input stays put for correction. An empty field
        means Clear.
        """
        text = edit.text().strip() if edit is not None else ""
        if not text:
            self._clear_routine_schedule_from_ui(name)
            return
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            result = str(planner.save_routine(name, list(actions or []), schedule=text))
        except Exception as exc:  # noqa: BLE001
            result = f"Could not save routine '{name}': {exc}"
        if result.startswith("Could not save"):
            if feedback is not None:
                feedback.setText(result)
                feedback.setVisible(True)
            if editor is not None:
                editor.setVisible(True)
            return
        self._add_system(result)
        self._refresh_routines()

    def _clear_routine_schedule_from_ui(self, name):
        """Remove a routine's schedule so it runs on demand only.

        planner.save_routine has no clear path by design: re-saving
        without a schedule PRESERVES the stored one, and its own
        docstring prescribes removing the schedule fields from
        storage to clear one. planner exposes no public clear API,
        so this loads the records via the public get_routines(),
        drops the "schedule"/"schedule_text" keys, and writes back
        through planner's own storage helper (_save_list +
        _ROUTINES_PATH, both private) so the write keeps planner's
        atomic, corruption-tolerant format rather than the GUI
        inventing its own file handling.
        """
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            routines = planner.get_routines()
            target = None
            for record in routines:
                if isinstance(record, dict) and \
                        str(record.get("name", "")).strip().lower() == str(name).strip().lower():
                    target = record
                    break
            if target is None:
                self._add_system(f"Routine '{name}' no longer exists.")
                self._refresh_routines()
                return
            target.pop("schedule", None)
            target.pop("schedule_text", None)
            if not planner._save_list(planner._ROUTINES_PATH, routines):
                self._add_system(
                    f"Could not clear the schedule for '{name}': "
                    "failed to write data/routines.json.")
                return
        except Exception as exc:  # noqa: BLE001
            self._add_system(f"Could not clear the schedule for '{name}': {exc}")
            return
        self._add_system(f"Schedule cleared for '{name}' — it now runs on demand.")
        self._refresh_routines()

    @staticmethod
    def _routine_next_text(routine: dict) -> str:
        """Short 'next …' label from planner.next_run, when the
        routine carries a schedule. Anything odd → no label."""
        if not routine.get("schedule"):
            return ""
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            now = datetime.datetime.now()
            nxt = planner.next_run(routine, now)
        except Exception:  # noqa: BLE001
            return ""
        if nxt is None:
            return ""
        if nxt <= now:
            return "due now"
        if nxt.date() == now.date():
            return f"next today {nxt.strftime('%H:%M')}"
        if nxt.date() == (now + datetime.timedelta(days=1)).date():
            return f"next tomorrow {nxt.strftime('%H:%M')}"
        return f"next {nxt.strftime('%d %b %H:%M')}"

    def _delete_routine_from_ui(self, name: str):
        try:
            from voice_assistant.core.gui_facade import planner  # Phase 3 B2-remainder: planner moved to core/brain/

            planner.delete_routine(name)
        except Exception as exc:  # noqa: BLE001
            self._add_system(f"Could not delete routine '{name}': {exc}")
            return
        self._refresh_routines()