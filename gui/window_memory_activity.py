"""MainWindow memory mixin: Activity tabs, journal reconcile /
compensate, plan / routine rows.

Split from gui/window_memory.py (GUI split #12 batch 2, move only).
"""
import asyncio
import datetime
import threading
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
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
from .design_system.empty import EmptyState
from .design_system.panels import HudPanel
from .design_system.progress import SegmentBar
from .room_activity import (
    error_line,
    headline_counts,
    headline_line,
    strip_absolute_paths,
)
from .style import AMBER
from .task_state import (
    add_task_row,
    build_task_row_widget,
    instrument_row,
    instrument_row_title,
    list_item_size_hint,
    mission_state_for_record,
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
        # The list is exactly its content tall (capped): the
        # minimum stops it stretching to fill the pane's slack,
        # so the pane ends after the last row instead of after
        # a stretched list. The content height is the SUM of
        # the row heights, not the tallest row times the count
        # — with a tall approval card (239 px) among short
        # rows (37 px), max x count over-allocates (229x4=918
        # for 350 px of content) and the list overflows its
        # layout slot, painting over the journal below it
        # (parent by-eye fix, 2026-11-24: the journal collided
        # with the approval card). The cap still binds at N
        # rows: past it the list shows N rows and scrolls.
        _heights = [lst.sizeHintForRow(i) for i in range(count)]
        _shown = min(count, rows)
        lst.setMinimumHeight(sum(_heights[:_shown]) + 2)
        lst.setMaximumHeight(
            sum(_heights[:_shown])
            + (max(_heights) * (rows - _shown) if rows > _shown else 0)
            + 2)
    except Exception:  # noqa: BLE001 — cap is cosmetic
        pass


# Phase 1: the inline approval card's 60 s silence window.
_COUNTDOWN_TOTAL_S = 60


class _CountdownHairline(QWidget):
    """The approval countdown as a depleting 1 px amber hairline
    under the card (B-A02 / round 4).

    A per-second reading update from the same remaining-seconds
    value as the countdown label — never an animation, never a
    whole-card repaint. The width is the fraction that is left.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("approvalCountdownBar")
        self.setFixedHeight(1)
        self._remaining = _COUNTDOWN_TOTAL_S

    def set_remaining(self, seconds):
        """Set the seconds left (clamped to the 60 s window)."""
        self._remaining = max(
            0, min(_COUNTDOWN_TOTAL_S, int(seconds)))
        self.update()

    def countdown_fraction(self):
        """The fraction of the window left (1.0 = full 60 s)."""
        return self._remaining / _COUNTDOWN_TOTAL_S

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        width = int(round(self.width() * self.countdown_fraction()))
        if width > 0:
            painter.fillRect(0, 0, width, 1, QColor(AMBER))
        painter.end()


class _ApprovalCard(HudPanel):
    """The inline approval card (Phase 1, §3/A-01).

    A paused row carrying its own coordinator pending approval
    renders this card INLINE below the row — the dialog's
    contract, not a second one: the tool, the step label, the
    engine sentence verbatim, the confirm-flag name (from
    pending_approval_summary), "Allow once" / "Don't allow",
    the 60 s silence timer as a countdown, the safe default
    (Don't allow), no "Always allow" third button, no voice
    path. Resolution goes ONLY through the window's
    ``_resolve_pending(task_id, approved)`` at click time —
    the card never resolves anything itself, and never holds a
    cached reference to the resolver (the repro spies on the
    method, so the lookup happens on the window at click time).
    The card and the dialog share one presenter state: resolving
    from either clears the other on the next refresh, because
    the refresh re-reads the ledger.
    """

    def __init__(self, window, task_id, pending):
        super().__init__(glass=True, edge="amber")
        self.setObjectName("activityApprovalCard")
        self._window = window
        # Phase 6 (Light sweep, D-16 family): a HudPanel paints
        # its own fill, and the window's appearance pass only
        # recolors the panels that exist when it runs. This card
        # is created dynamically on the Activity refresh — after
        # the pass — so without this it keeps the house-dark
        # SURFACE_2 fill and reads dark-on-dark on Light (the
        # title uses ${TEXT}, which IS theme-dark on Light).
        # Pick up the window's current resolved theme at
        # creation; the appearance pass still recolors it on
        # the next theme change.
        try:
            _th = getattr(window, "_home_theme", None)
            if _th:
                self.set_theme(_th)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass
        self._task_id = str(task_id)
        self._remaining = _COUNTDOWN_TOTAL_S
        self._resolved = False
        from voice_assistant.gui.pending_approval import (
            pending_approval_summary)
        tool, summary = pending_approval_summary(pending)
        layout = self.content_layout()
        title = QLabel(
            f"Approval needed — {tool}" if tool else "Approval needed")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        for line in str(summary).split("\n"):
            if not line.strip():
                continue
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
        self._countdown = QLabel(self._countdown_text())
        self._countdown.setWordWrap(True)
        layout.addWidget(self._countdown)
        # §4: the buttons stack full-width at ≤640, sit side by
        # side above that.
        try:
            narrow = window.width() <= 640
        except Exception:  # noqa: BLE001 — default to side by side
            narrow = False
        btn_row = QVBoxLayout() if narrow else QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        self._allow_btn = QPushButton("Allow once")
        self._allow_btn.setObjectName("approvalAllowBtn")
        self._allow_btn.clicked.connect(
            lambda _=False: self._resolve(True))
        self._deny_btn = QPushButton("Don't allow")
        self._deny_btn.setObjectName("approvalDenyBtn")
        self._deny_btn.clicked.connect(
            lambda _=False: self._resolve(False))
        btn_row.addWidget(self._allow_btn)
        btn_row.addWidget(self._deny_btn)
        if not narrow:
            btn_row.addStretch(1)
        layout.addLayout(btn_row)
        self._hairline = _CountdownHairline(self)
        layout.addWidget(self._hairline)
        # The glass tile from the window's stored theme, when
        # known — without it the glass paints the translucent
        # fill alone (the panel's own fallback). Look-fix
        # 2026-11-24: the tile IS the backdrop's grid, blurred —
        # it only belongs where the backdrop actually shows a
        # grid. Activity is vignette now (no grid), so an
        # unconditional tile painted a checkerboard inside the
        # approval card on a flat page.
        try:
            resolved = getattr(window, "_activity_theme_resolved", None)
            if resolved:
                from .design_system.backdrop import glass_tile
                bd = getattr(window, "_activity_backdrop", None)
                grid_on = (bd is not None
                           and bd.effective_layers()[0])
                self.set_glass_tile(
                    glass_tile(resolved) if grid_on else None)
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        # Named _on_countdown_tick, not _tick: HudPanel already
        # owns self._tick (the accent-colour string for the
        # corner ticks), and an instance attribute shadows the
        # method — connect() would get the string.
        self._timer.timeout.connect(self._on_countdown_tick)
        self._timer.start()

    def _countdown_text(self):
        # Seconds left in the 60 s window, in the plan's shape
        # ("Decides itself in 0:47 — no answer means Don't
        # allow"). At first paint the window is whole, so it
        # reads 0:60 for one second, then 0:59 — the window
        # itself stays exactly 60 s.
        return (f"Decides itself in 0:{self._remaining:02d} — "
                "no answer means Don't allow")

    def set_remaining(self, seconds):
        """Set the seconds left; label + hairline read the same
        value (B-A02 — they can never disagree)."""
        self._remaining = max(
            0, min(_COUNTDOWN_TOTAL_S, int(seconds)))
        self._countdown.setText(self._countdown_text())
        self._hairline.set_remaining(self._remaining)

    def countdown_fraction(self):
        """The fraction of the 60 s window left."""
        return self._hairline.countdown_fraction()

    def _on_countdown_tick(self):
        # A-10: the countdown updates the timer label (and its
        # hairline) only — never the whole card, never moving
        # focus.
        if self._resolved:
            return
        self.set_remaining(self._remaining - 1)
        if self._remaining <= 0:
            self._expire()

    def _resolve(self, approved):
        if self._resolved:
            return
        try:
            # Click-time lookup on the window: _resolve_pending
            # is owned by the Home builder (window_home_hud.py)
            # and may be spied on in tests — never cached.
            self._window._resolve_pending(self._task_id, bool(approved))
        except AttributeError:
            # The resolver is not built yet: leave the card live
            # (its timer keeps running) rather than pretending
            # the answer landed.
            return
        except Exception:  # noqa: BLE001 — a failed resolve
            pass  # still refreshes: the ledger is the truth.
        self._resolved = True
        try:
            self._timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        try:
            # Deferred past the click: the refresh rebuilds the
            # list and destroys this card.
            QTimer.singleShot(0, self._window._refresh_activity_tabs)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _expire(self):
        """60 s of silence = denial (the dialog's contract)."""
        if self._resolved:
            return
        self._resolved = True
        try:
            self._timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass
        self._countdown.setText("Denied — no answer in 60 s")
        self._hairline.set_remaining(0)
        for btn in (self._allow_btn, self._deny_btn):
            try:
                btn.setEnabled(False)
            except Exception:  # noqa: BLE001 — teardown never raises
                pass
        try:
            self._window._resolve_pending(self._task_id, False)
        except Exception:  # noqa: BLE001 — silence still denied
            pass  # on the card; the refresh shows the truth.
        try:
            # The denial stays readable briefly, then the
            # refresh clears the card (the approval is gone).
            QTimer.singleShot(1500, self._window._refresh_activity_tabs)
        except Exception:  # noqa: BLE001 — cosmetic
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
        # A-08: a resolution refreshes every tab — preserve the
        # selected Mission Log mission across the rebuild, by
        # task_id; if it is gone, the pane says so (below).
        _prev_log_task = None
        _ml_prev = getattr(self, "_mission_log_list", None)
        if _ml_prev is not None:
            try:
                _cur = _ml_prev.currentItem()
                if _cur is not None:
                    _d = _cur.data(Qt.ItemDataRole.UserRole)
                    if isinstance(_d, dict):
                        _prev_log_task = _d.get("task_id")
            except Exception:  # noqa: BLE001 — no selection kept
                _prev_log_task = None
        records = None
        load_error = ""
        try:
            from voice_assistant.core.gui_facade import TaskLedger  # Phase 3 B1a-2: moved to core/brain/

            records = TaskLedger().all_records()
        except Exception as exc:  # noqa: BLE001 — the page still builds
            records = None
            load_error = str(exc)
        # UI Phase 6: QListWidget.clear() drops the items but NOT
        # their item widgets — a resolved approval card (or any
        # row) survived every refresh as a hidden orphan parented
        # to the viewport, findable by findChild and accumulating
        # a dead widget per refresh (the D-01 family). Delete the
        # old row widgets BEFORE clear(), while the app is alive,
        # so the rebuild adds fresh ones.
        for _lst in (in_progress, history):
            for _i in range(_lst.count()):
                _w = _lst.itemWidget(_lst.item(_i))
                if _w is not None:
                    _w.deleteLater()
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
                if str(r.get("state", "")) in ("running", "paused",
                                               "verifying")]
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
        # Phase 1 (§3): the MissionRail producer bridge — the
        # ledger's newest live record, from this same read.
        self._publish_mission_rail(live)
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
        # B-A03: when idle, the designed empty state lives in the
        # rail (resting hollow ring + "Nothing in progress right
        # now." + "Ask VYUK to start something."). The list below
        # the rail does NOT repeat it — an EmptyState item here
        # would print the same words twice on the page. The list
        # simply stays empty while idle (no `if not live` branch).
        for r in live:
            # §7 consumers batch 7: a paused row carrying its own
            # coordinator pending approval renders Needs approval
            # from its record and must NOT consume the live gate
            # hint — the hint belongs to a running row positioned
            # at the gated tool, never to a row that already
            # names its own question. UI Phase 2 (P2-02): a
            # VERIFYING row carries one too (resumed after a
            # crash, died mid-verification with its pending step
            # still open) — the Review queue offers it, so the
            # card renders for it as well. A running row is
            # excluded on purpose: the live gate hint owns it.
            coord_pending = (
                str(r.get("state", "")) in ("paused", "verifying")
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
            # Phase 1 (§3/A-01): a paused row carrying its own
            # coordinator pending approval renders its approval
            # card INLINE below the row — not as a button that
            # opens a dialog.
            if coord_pending:
                _card_item = QListWidgetItem()
                in_progress.addItem(_card_item)
                _card = _ApprovalCard(
                    self, r.get("task_id", ""),
                    r.get("pending_approval") or {})
                _card_item.setSizeHint(list_item_size_hint(_card))
                in_progress.setItemWidget(_card_item, _card)
                # The card's sizeHint is measured before its
                # layout has run at the list's width, so its
                # word-wrapped lines mis-measure and the item
                # comes out too short — the journal below then
                # paints over the card's lower half (parent
                # by-eye fix, 2026-11-24: "Journal · 1 ...",
                # "Nothing needs attention right now." and "0
                # reconciled — see History" collided with the
                # approval card text). Once the layout settles,
                # give the card the list's viewport width FIRST
                # (so the wrapped lines measure at the real
                # width), then re-measure and grow the item to
                # fit; the list is AdjustToContents, so it
                # follows and the journal drops below the card.
                try:
                    from PySide6.QtCore import QTimer as _QTimer

                    def _fix_card_height(
                            _item=_card_item, _c=_card,
                            _lst=in_progress):
                        try:
                            _vw = _lst.viewport().width()
                            if _vw > 0:
                                _c.setFixedWidth(_vw)
                            _c.adjustSize()
                            _item.setSizeHint(
                                list_item_size_hint(_c))
                            # Release the fixed width so the card
                            # still follows the list on a resize;
                            # the item keeps the measured height.
                            _c.setMinimumWidth(0)
                            _c.setMaximumWidth(16777215)
                            _lst.updateGeometry()
                            # Re-cap the list at the re-measured
                            # heights: the card can grow once laid
                            # out (its sizeHint settles after the
                            # refresh's cap ran), and the cap is
                            # what pins the list's min/max — a
                            # stale cap leaves the list short and
                            # the journal overlapping again.
                            try:
                                _cap_activity_list(_lst, 6)
                            except Exception:  # noqa: BLE001
                                pass
                            # The list grows when the item does
                            # (AdjustToContents), but the parent
                            # QVBoxLayout does not re-run on its
                            # own — the journal below stays at the
                            # position it had when the list was
                            # short, painting over the card. Force
                            # the parent layout to re-run so the
                            # journal drops below the grown list.
                            try:
                                _parent = _lst.parentWidget()
                                if _parent is not None:
                                    _lay = _parent.layout()
                                    if _lay is not None:
                                        _lay.invalidate()
                                        _lay.activate()
                                    _parent.updateGeometry()
                            except Exception:  # noqa: BLE001
                                pass
                        except Exception:  # noqa: BLE001
                            pass

                    _QTimer.singleShot(0, _fix_card_height)
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
        # §7 consumers batch 7: the recovery offer's visibility
        # + count, refreshed with the rows above. The button
        # itself is built with the Activity page; until then
        # this is a guarded no-op. UI Phase 2 (P2-02): the count
        # is the ONE shared count (pending_approvals_count) —
        # the Review queue's length — never a local sum, so the
        # button label, the sidebar badge, and the Home headline
        # can never disagree again. Before this the button
        # counted paused live records from all_records()
        # (paused goal records included, verifying records
        # excluded) while the Review queue counted
        # list_interrupted() — the two disagreed in both
        # directions.
        try:
            from voice_assistant.gui.pending_approval import (
                pending_approvals_count)
            _n_pending = pending_approvals_count()
        except Exception:  # noqa: BLE001 — a count never
            _n_pending = 0  # breaks the page.
        _pending_btn = getattr(self, "_activity_pending_btn", None)
        if _pending_btn is not None:
            _pending_btn.setVisible(_n_pending > 0)
            _pending_btn.setText(
                f"Review pending approvals ({_n_pending})"
                if _n_pending else "Review pending approvals")
        # B15: the question offer's visibility + count,
        # refreshed with the rows above, same shape as the
        # approvals offer. Hidden while no question is
        # pending.
        _n_questions = sum(
            1 for r in live
            if str(r.get("state", "")) == "paused"
            and isinstance(r.get("pending_question"), dict)
            and bool(r.get("pending_question", {}).get("question")))
        _question_btn = getattr(self, "_activity_question_btn", None)
        if _question_btn is not None:
            _question_btn.setVisible(_n_questions > 0)
            _question_btn.setText(
                f"Answer pending questions ({_n_questions})"
                if _n_questions else "Answer pending questions")
        # B21: the reconcile offer's visibility + count,
        # refreshed with the rows above, same shape as the
        # approvals and questions offers. Hidden while no
        # reconcile decision is pending.
        _n_reconciles = sum(
            1 for r in live
            if str(r.get("state", "")) == "paused"
            and isinstance(r.get("pending_reconcile"), dict)
            and bool(r.get("pending_reconcile", {}).get("question")))
        _reconcile_btn = getattr(self, "_activity_reconcile_btn", None)
        if _reconcile_btn is not None:
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
        _journal_result = None
        _journal_list = getattr(self, "_activity_journal_list", None)
        if _journal_list is not None:
            try:
                from voice_assistant.gui.journal_surface import (
                    refresh_journal_list)
                _journal_result = refresh_journal_list(
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
                _journal_result = {"ok": False, "error": str(exc)}
            # Build 2: the journal rows changed, so the contextual
            # buttons and the hint line re-derive from the fresh
            # list (the selection signal may not fire when nothing
            # was selected before).
            self._update_journal_reconcile_btn()
            self._update_journal_compensate_btn()
            self._update_journal_hint()
            # Phase 1 (B-A06 + A-06): the needs-attention header
            # counts the attention rows shown + the pending
            # approvals waiting, from this same ledger/journal
            # read; the footer names the reconciled count (over
            # ALL journal entries) so a cleared row reads as
            # progress, not loss. On a broken journal read the
            # label keeps refresh_journal_list's own wording
            # ("Journal — unreadable" / "Journal") and the
            # footer hides.
            _header = getattr(self, "_activity_journal_label", None)
            _footer = getattr(self, "_activity_journal_footer", None)
            if (isinstance(_journal_result, dict)
                    and _journal_result.get("ok")):
                _attention_n = len(_journal_result.get("rows") or [])
                if _header is not None:
                    _n_attention = _attention_n + _n_pending
                    # Activity empty-state fix (2026-11-26): no
                    # "(0)" — "needs attention (0)" was one of
                    # the five "nothing"s on the idle page. The
                    # count shows only while something waits.
                    _header.setText(
                        "Journal — needs attention"
                        if _n_attention == 0 else
                        "Journal — needs attention "
                        f"({_n_attention})")
                if _footer is not None:
                    _jcounts = _journal_result.get("counts") or {}
                    _footer.setText(
                        f"{int(_jcounts.get('done', 0))} reconciled "
                        "— see History")
                    _footer.setVisible(True)
            elif _footer is not None:
                _footer.setText("")
                _footer.setVisible(False)
        # Phase 1 (§3, A-11/A-05): History's own filters (state
        # segments + time window) and per-row Undo through the
        # journal's compensations where declared. The filter
        # chips carry their counts from this same read; the
        # journal rows for Undo matching come from the journal
        # read above (attention rows carry the declared
        # compensations).
        _hist_state = getattr(self, "_history_state", "All")
        _hist_days = getattr(self, "_history_days", 0)
        _hist_cutoff = None
        if _hist_days:
            _hist_cutoff = (datetime.datetime.now(datetime.timezone.utc)
                            - datetime.timedelta(days=_hist_days))
        _hist_buckets = {"All": 0, "Done": 0, "Failed": 0,
                         "Partial": 0, "Cancelled": 0}
        for _r in done:
            _hist_buckets["All"] += 1
            _bucket = self._history_bucket(
                _r, render_task(_r))
            if _bucket in _hist_buckets:
                _hist_buckets[_bucket] += 1
        # Hunt fix (c) item 8: a zero bucket shows the bare name
        # (fix (a)'s pattern — the journal header drops "(0)" at
        # zero), so an empty History reads "All  Done  Failed  …"
        # instead of five "(0)" counts.
        for _state in ("All", "Done", "Failed", "Partial", "Cancelled"):
            _btn = self.findChild(QPushButton, f"historyFilter-{_state}")
            if _btn is not None:
                _n = _hist_buckets[_state]
                _btn.setText(_state if _n == 0 else f"{_state} ({_n})")
        _journal_rows = []
        if (isinstance(_journal_result, dict)
                and _journal_result.get("ok")):
            _journal_rows = _journal_result.get("rows") or []
        _shown_history = 0
        for r in done:
            rendered = render_task(r)
            if (_hist_state != "All"
                    and self._history_bucket(r, rendered) != _hist_state):
                continue
            if _hist_cutoff is not None:
                try:
                    _stamp = datetime.datetime.fromisoformat(
                        str(r.get("updated_at", "")))
                    if _stamp.tzinfo is None:
                        _stamp = _stamp.replace(
                            tzinfo=datetime.timezone.utc)
                    if _stamp < _hist_cutoff:
                        continue
                except Exception:  # noqa: BLE001 — include
                    pass  # unparseable stamps, never hidden.
            self._add_history_row(history, r, rendered, _journal_rows)
            _shown_history += 1
        if not _shown_history and done:
            # Phase 0: shared EmptyState, same words, no text row.
            # B-A03: History carries ring + line only, no
            # suggestion. Hunt fix (c) item 8: when there are no
            # terminal tasks at all the headline ("Nothing
            # finished yet") already carries the empty message,
            # so the list's EmptyState is not added — one empty
            # line, not two. When tasks exist but the filter /
            # time window excludes them all, the EmptyState stays
            # (it explains that nothing matches the filter).
            _item = QListWidgetItem()
            history.addItem(_item)
            _empty = EmptyState(
                sentence="No finished tasks yet.")
            _item.setSizeHint(list_item_size_hint(_empty))
            history.setItemWidget(_item, _empty)
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
                    # The detail pane builds its ActionTrace from
                    # the record's steps, so the stored dict
                    # carries them alongside the rendering (the
                    # task_id stays on top for selection +
                    # re-selection by task_id).
                    stored = dict(rendered)
                    stored["steps"] = r.get("steps") or []
                    stored["outcomes"] = r.get("outcomes") or []
                    stored["verification"] = (
                        r.get("verification") or [])
                    item.setData(Qt.ItemDataRole.UserRole, stored)
                shown += 1
            if not shown and records:
                # Phase 0: shared EmptyState, same words, no text
                # row. B-A03: Mission Log carries ring + line
                # only, no suggestion. Hunt fix (c) item 8: when
                # there are no missions at all the headline
                # ("Nothing needs attention") already carries the
                # empty message, so the list's EmptyState is not
                # added — one empty line, not two. When missions
                # exist but the filter / time window excludes them
                # all, the EmptyState stays (it explains that
                # nothing matches the filter). The evidence-trace
                # box below stays either way (it is the tab's
                # purpose, not a second empty state).
                _item = QListWidgetItem()
                mission_log.addItem(_item)
                _empty = EmptyState(
                    sentence="No missions match this filter.")
                _item.setSizeHint(list_item_size_hint(_empty))
                mission_log.setItemWidget(_item, _empty)
            # A-08: preserve the selected mission across the
            # rebuild — re-select by task_id; if it is gone, the
            # pane says so instead of showing a stale trace.
            if _prev_log_task:
                _found = None
                for _i in range(mission_log.count()):
                    _d = mission_log.item(_i).data(
                        Qt.ItemDataRole.UserRole)
                    if (isinstance(_d, dict)
                            and _d.get("task_id") == _prev_log_task):
                        _found = _i
                        break
                if _found is not None:
                    mission_log.setCurrentRow(_found)
                else:
                    _detail = getattr(self, "_mission_log_detail", None)
                    _trace = getattr(self, "_mission_log_trace", None)
                    if _trace is not None:
                        _trace.clear()
                    if _detail is not None:
                        _detail.setPlainText(
                            "This mission finished — see History")
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
        # Activity empty-state fix (2026-11-26): the idle page
        # carries ONE empty statement (the rail's empty label).
        # The In progress headline ("Nothing in progress")
        # duplicates it, so the headline hides while nothing is
        # live; the two lists hide while they have no rows (an
        # empty list is ~68 px of dead space — it was the gap
        # between the rail and the journal, and the reason the
        # footer sat 9 px past the viewport). The idle stretches
        # (recorded at build time) take factor 1 only while the
        # tab is idle — they centre the rail and anchor the
        # journal + footer at the bottom of the filled pane; with
        # live work they are 0 and the layout is exactly as
        # before. Idle here means the rendered tab shows
        # nothing: no live rows and no attention rows (the
        # footer count alone does not make the page busy).
        _idle = (in_progress.count() == 0
                 and self._activity_journal_list.count() == 0)
        _headline_ip = (getattr(self, "_activity_headlines", None)
                        or {}).get("in_progress")
        if _headline_ip is not None:
            _wrap = _headline_ip["line"].parentWidget()
            if _wrap is not None:
                _wrap.setVisible(bool(live))
        in_progress.setVisible(in_progress.count() > 0)
        self._activity_journal_list.setVisible(
            self._activity_journal_list.count() > 0)
        _ip_layout = getattr(self, "_activity_in_progress_layout",
                             None)
        if _ip_layout is not None:
            _factor = 1 if _idle else 0
            for _index in (
                    getattr(self, "_activity_idle_stretch_top",
                            None),
                    getattr(self, "_activity_idle_stretch_mid",
                            None)):
                if _index is not None:
                    _ip_layout.setStretch(_index, _factor)
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

    def _publish_mission_rail(self, live):
        """The MissionRail producer bridge (Phase 1, §3/A-03).

        Reads the ledger's newest live record from the same
        read _refresh_activity_tabs performs — one read, shared
        — and publishes it as MissionState on the state bus AND
        sets it on the rail directly. The bus emit is async
        (queued), so the direct set is what makes the rail
        correct immediately; the bus publish is the display
        channel other consumers read. When nothing is live the
        bridge publishes the idle state and the rail shows its
        honest empty state — never an invented mission. When
        two missions are live the rail shows the newest and its
        goal line names the count (the mapper + rail own that
        wording). Stop / Pause only do anything while a mission
        is live, so they hide while idle — never buttons that
        can never be pressed.
        """
        rail = getattr(self, "_mission_rail", None)
        if rail is None:
            return
        mapped = None
        task_id = None
        if live:
            newest = live[0]
            task_id = str(newest.get("task_id") or "")
            try:
                mapped = mission_state_for_record(
                    newest, live_count=len(live))
            except Exception:  # noqa: BLE001 — idle, not broken
                mapped = None
        self._mission_rail_task_id = task_id
        try:
            from voice_assistant.gui.state_bus import (
                MissionState, get_state_bus)
            if mapped:
                state = MissionState(
                    goal=mapped.get("goal", ""),
                    phase=mapped.get("phase", ""),
                    step=int(mapped.get("step", 0) or 0),
                    steps_total=int(mapped.get("steps_total", 0) or 0),
                    permission=mapped.get("permission", "none"))
            else:
                state = MissionState()
            get_state_bus().emit(state)
        except Exception:  # noqa: BLE001 — the direct set below
            pass  # is the deterministic path; the bus is extra.
        try:
            rail.set_mission_state(mapped)
        except Exception:  # noqa: BLE001 — a broken rail never
            pass  # breaks the refresh.
        # Plan §10: the rail host carries the accent edge while a
        # mission is live, LINE_STRONG when idle (the goal line
        # does the same — MissionRail owns that half).
        try:
            host = getattr(self, "_mission_rail_host", None)
            if host is not None and hasattr(host, "set_edge"):
                host.set_edge("accent" if mapped else "line")
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        try:
            for btn in rail.findChildren(QPushButton):
                if btn.text() in ("Stop", "Pause"):
                    btn.setVisible(bool(mapped))
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _on_mission_rail_stop(self):
        """Stop on the rail: the coordinator's cancel path (A-02).

        The cancelled record lands in History (cancelled is a
        terminal state) and a toast confirms "Mission cancelled"
        — the mission never vanishes with no receipt. When the
        coordinator holds nothing (e.g. after a restart) the
        ledger is the record: abandon it there, which writes
        the same cancelled state.
        """
        task_id = getattr(self, "_mission_rail_task_id", None)
        if not task_id:
            return
        stopped = False
        coord = getattr(self, "_task_coordinator", None)
        if coord is not None:
            try:
                stopped = bool(coord.cancel_task(task_id))
            except Exception:  # noqa: BLE001 — try the ledger
                stopped = False  # below instead.
        if not stopped:
            try:
                from voice_assistant.core.gui_facade import TaskLedger
                stopped = bool(TaskLedger().abandon_record(
                    task_id, reason="cancelled by the user"))
            except Exception:  # noqa: BLE001 — nothing written
                stopped = False
        if stopped:
            try:
                self.show_toast("Mission cancelled")
            except Exception:  # noqa: BLE001 — the toast is a
                try:  # courtesy; the refresh is the receipt.
                    self._notify("Mission cancelled")
                except Exception:  # noqa: BLE001 — never raises
                    pass
        try:
            self._refresh_activity_tabs()
        except Exception:  # noqa: BLE001 — never raises
            pass

    def _on_mission_rail_pause(self):
        """Pause on the rail: only via the coordinator's pause
        seam (A-02). A ledger-only record is already
        interrupted, so there is nothing to pause — the call
        simply does nothing for it."""
        task_id = getattr(self, "_mission_rail_task_id", None)
        if not task_id:
            return
        coord = getattr(self, "_task_coordinator", None)
        if coord is not None:
            try:
                coord.pause_task(task_id)
            except Exception:  # noqa: BLE001 — a failed pause
                pass  # leaves the mission running; refresh shows it.
        try:
            self._refresh_activity_tabs()
        except Exception:  # noqa: BLE001 — never raises
            pass

    @staticmethod
    def _history_bucket(record, rendered):
        """History's own filter bucket for one terminal record.

        Rendered-state buckets, except cancelled records, which
        the renderer folds into Partial (some work done) or
        Failed (nothing done) — History keeps them as their own
        bucket instead: a cancelled record with successful steps
        is Partial, one without is Cancelled. The buckets are
        mutually exclusive and sum to All (the leg pins this).
        """
        raw = str((record or {}).get("state", ""))
        if raw == "cancelled":
            return ("Partial"
                    if (rendered or {}).get("state") == "Partial"
                    else "Cancelled")
        return (rendered or {}).get("state") or "Unknown"

    def _add_history_row(self, list_widget, record, rendered,
                         journal_rows):
        """One History row: the single task-state row plus Undo.

        Undo renders only where a journal entry tied to this
        task declares compensations — tied by handle_id (the
        task's steps and the journal entries both carry it), run
        in reverse order through the existing compensate leg
        (authorization dialog first). Otherwise the row shows
        "No undo available — manual steps" (the journal's own
        wording): a tappable link opening that entry's journal
        details when an entry exists, plain text when no journal
        entry is tied to the task at all.
        """
        item = QListWidgetItem(list_widget)
        widget = build_task_row_widget(rendered, parent=list_widget)
        handles = {
            str(step.get("handle_id"))
            for step in (record.get("steps") or [])
            if isinstance(step, dict) and step.get("handle_id")}
        matched = None
        for jrow in journal_rows or []:
            if (isinstance(jrow, dict) and jrow.get("handle_id")
                    and str(jrow.get("handle_id")) in handles):
                matched = jrow
                break
        if (matched is not None
                and (matched.get("compensations") or [])):
            undo_btn = QPushButton("Undo")
            undo_btn.setObjectName("activityTextAction")
            undo_btn.clicked.connect(
                lambda _=False, row=dict(matched):
                self._compensate_history_entry(row))
            widget.layout().addWidget(undo_btn)
        else:
            link = QPushButton("No undo available — manual steps")
            link.setObjectName("activityTextAction")
            if matched is not None:
                link.clicked.connect(
                    lambda _=False, row=dict(matched):
                    self._show_history_journal_details(row))
            else:
                # No journal entry to open: the words stay as
                # plain text, never a dead link.
                link.setEnabled(False)
            widget.layout().addWidget(link)
        item.setSizeHint(list_item_size_hint(widget))
        list_widget.addItem(item)
        list_widget.setItemWidget(item, widget)

    def _compensate_history_entry(self, row):
        """Undo for one History row: the journal's declared
        compensations through the EXISTING compensate leg
        (authorization dialog first, leg on a worker thread).
        """
        try:
            from voice_assistant.gui.journal_surface import (
                present_compensate)
        except Exception:  # noqa: BLE001 — no Undo without the leg
            return
        present_compensate(self, row,
                           refresh=self._refresh_activity_tabs)

    def _show_history_journal_details(self, row):
        """Journal details for one History row's entry (the same
        details dialog the journal's double-click opens)."""
        try:
            from voice_assistant.gui.journal_surface import (
                journal_row_details)
            try:
                from gui.design_system.dialogs import HudDialog
            except ImportError:
                from voice_assistant.gui.design_system.dialogs import (  # type: ignore
                    HudDialog)
            HudDialog.details(
                self, f"Journal entry — {row.get('tool', '')}",
                f"Journal entry — {row.get('tool', '')}",
                journal_row_details(row))
        except Exception:  # noqa: BLE001 — details are a
            return  # courtesy; never break the page.

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
        # Phase 1 (§3/A-01): on Activity the inline card IS the
        # queue entry point — this button scrolls to / expands
        # the first pending card (it does not open a dialog).
        # The dialog path below stays for surfaces OUTSIDE
        # Activity (Home's attention rail, a notification).
        try:
            card = self.findChild(QWidget, "activityApprovalCard")
            if card is not None:
                try:
                    self._select_activity_tab("In progress")
                except Exception:  # noqa: BLE001 — cosmetic
                    pass
                lst = getattr(self, "_activity_in_progress_list", None)
                if lst is not None:
                    for _i in range(lst.count()):
                        _item = lst.item(_i)
                        if lst.itemWidget(_item) is card:
                            lst.scrollToItem(_item)
                            lst.setCurrentItem(_item)
                            break
                return
        except Exception:  # noqa: BLE001 — fall through to the
            pass  # dialog path when there is no card to scroll to.
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
        # Phase 1 (B-A04): the row is a header line plus, under
        # it, a miniature 2 px SegmentBar — the same SegmentBar
        # (segmented mode, fill-only), fill = done / total steps
        # from the plan record; a plan row with no steps carries
        # no bar. The outer widget carries the planRow-{id} name
        # (the bar lives under the header, inside it).
        outer = QWidget()
        outer.setObjectName(f"planRow-{plan_id}")
        v = QVBoxLayout(outer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        wrap = instrument_row()
        wrap.setObjectName("activityRow")
        v.addWidget(wrap)
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
        if total > 0:
            bar = SegmentBar(mini=True)
            bar.set_label_mode("steps")
            bar.set_progress(done, total)
            bar.setFixedHeight(2)
            # Look-fix 2 (2026-11-24): theme the mini bar from the
            # stored resolved theme now (plan rows build before the
            # first appearance pass); the pass re-drives it on
            # every theme change.
            try:
                _resolved = getattr(self, "_resolved_theme", None)
                if isinstance(_resolved, dict):
                    bar.set_theme(_resolved)
            except Exception:  # noqa: BLE001 — appearance never breaks boot
                pass
            v.addWidget(bar)
        return outer

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