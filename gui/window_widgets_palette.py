# Refactor Phase 1 step 5: moved verbatim from gui/window_widgets.py (split by family).
from .motion import (
    DURATION_DISMISS_MS,
    DURATION_IGNITE_MS,
    DURATION_PALETTE_FADE_MS,
    DURATION_QUICK_MS,
    DURATION_TAGLINE_MS,
    DURATION_WORDMARK_MS,
    EASE_DIALOG,
)
from .design_system.dialogs import HudDialog
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtCore import (
    Property,
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from .style import ACCENT_2, BG, DANGER, OK, TEXT, TEXT_MUTED
from .style import ACCENT, BG, LINE_STRONG, TYPE_CARD_TITLE

from .window_widgets_toolnames import _TOOL_FRIENDLY_NAMES
from .window_widgets_helpers import _humanise
from .window_widgets_helpers import _summarise_tool




# v5.57 (redo, A9.2): the one shortcut list — the Settings card
# and the "?" overlay both render from it, so they cannot drift.
SHORTCUT_ROWS: tuple[tuple[str, str], ...] = (
    ("Ctrl+K", "Open the command palette"),
    ("?", "Show keyboard shortcuts"),
    ("Ctrl+F", "Find a word in this conversation"),
    ("Enter / Shift+Enter", "Next / previous match, while finding"),
    ("Esc", "Close the find bar"),
    ("Ctrl+Shift+Space", "Show or hide VYUK"),
    ("Enter", "Send a typed message (Shift+Enter starts a new line)"),
    ("↑ / ↓", "Recall a message you sent, in the typing box"),
)

def palette_list_height_for(available_h: int, chrome: int) -> int:
    """Audit-2 slice 1 (UI-07): list height for a given work-area
    height. Normal screens keep the full 486 (13 rows visible
    without scrolling); small work areas shrink the list so the
    whole dialog fits instead of clipping its footer. ``chrome`` is
    the dialog height minus the list height (margins, input, footer
    at the current font), so the cap stays right at any text scale.
    Pure arithmetic — unit-tested without a screen."""
    return max(120, min(486, int(available_h) - int(chrome)))

class CommandPalette(QDialog):
    """Phase 3 command palette — Ctrl+K / titlebar pill.

    Lists real actions wired to the main window's existing methods only.
    Filter-as-you-type, Enter/click activates, Esc closes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("palette")
        self.setWindowTitle("Command palette")
        # Frameless-ish: no native title bar, styled by QDialog#palette.
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(True)
        self.setFixedWidth(560)
        self._commands: list[dict] = self._build_commands()
        self._visible_actions: list = []
        self._visible_labels: list[str] = []
        self._visible_consequential: list[bool] = []
        # UI interaction slice: the last palette action's outcome.
        # None when the last run succeeded (or nothing ran yet);
        # "label: error" when it threw. The palette never reports
        # a failed action as done — see _run_row.
        self._last_action_error: str | None = None
        # UI interaction slice: the widget that had focus when the
        # palette opened, so closeEvent can hand focus back to it.
        self._focus_return = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setObjectName("paletteInput")
        self.input.setAccessibleName("Command palette search")
        self.input.setPlaceholderText("Type a command or search\u2026")
        self.input.textChanged.connect(self._apply_filter)
        self.input.returnPressed.connect(self._activate_current)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setAccessibleName("Matching commands")
        # v5.57 (redo): tall enough for the whole capped page —
        # 12 rows + the cap line — so the "keep typing" line is
        # visible without scrolling (13 × 36 rows + 16 list padding,
        # with 2 px slack so no scrollbar appears on an exact fit).
        self.list.setFixedHeight(486)
        self.list.itemClicked.connect(self._activate_item)
        self.list.itemActivated.connect(self._activate_item)
        layout.addWidget(self.list)
        # v5.57 (redo, A9.2): the footer, as mocked.
        self.footer = QLabel("↑ ↓ to move · ↵ to run · Esc to close")
        self.footer.setObjectName("paletteHint")
        layout.addWidget(self.footer)
        self._apply_filter("")

    # ------------------------------------------------------------ commands
    def _build_commands(self) -> list:
        """Command rows as dicts — label, action, kind, description,
        action_id, consequential. Pages, then Settings cards, then
        setting actions, then tools. Only real window methods /
        navigation / composer prefill: no row invents behaviour.
        Tool rows prefill the composer ("Use X to …", the Galaxy's
        own pattern) so the brain's argument-filling and
        confirmation still run them. Sweep Build 4: rows are dicts
        so the Qt-free logic module (core/command_palette.py)
        can rank them, and consequential rows are flagged here —
        connect/disconnect (session/egress change) and wake-word
        toggle (capture state change) need a second confirmation
        in _run_row; everything else runs as before. (The
        world-view page was removed 2026-11-05: its palette row
        is gone with it.)
        S1
        (2026-10-30): page rows follow the new IA labels
        (Home/Activity); pre-S1 labels survive as aliases
        (Assistant; Reminders lands on Activity/Scheduled);
        Skills stays a page row — its palette discovery route."""
        win = self.parent()
        commands: list[dict] = []
        if win is None:
            return commands

        def go(idx, label):
            return {"label": label,
                    "action": (lambda i=idx: win._show_page(i)),
                    "kind": "PAGE", "description": "",
                    "action_id": f"page_{idx}", "consequential": False}

        def _go_reminders_scheduled():
            # S1 alias: the pre-S1 "Reminders" route lands on
            # Activity with the Scheduled tab selected.
            win._show_page(1)
            select = getattr(win, "_select_activity_tab", None)
            if callable(select):
                select("Scheduled")

        commands.append(go(0, "Go to Home"))
        # S1 alias: the pre-S1 "Assistant" label still finds the
        # same page (stack 0) — no route is lost in the rename.
        commands.append(go(0, "Go to Assistant"))
        commands.append(go(1, "Go to Activity"))
        commands.append({"label": "Go to Reminders",
                         "action": _go_reminders_scheduled,
                         "kind": "PAGE", "description": "",
                         "action_id": "page_1_scheduled",
                         "consequential": False})
        commands.append(go(2, "Go to Memory"))
        commands.append(go(3, "Go to Skills"))
        commands.append(go(4, "Go to Settings"))
        commands.append(go(5, "Go to Pulse"))
        commands.append(go(6, "Go to Senses"))
        commands.append(go(7, "Go to Doctor"))
        # Settings cards, in page order (derived, not hardcoded,
        # so a rename can never strand a palette row).
        try:
            content = getattr(win, "settings_content", None)
            if content is not None:
                for lbl in content.findChildren(QLabel, "sectionTitle"):
                    title = lbl.text()
                    commands.append(
                        {"label": title,
                         "action": (lambda t=title: win.show_settings_card(t)),
                         "kind": "SETTING", "description": "",
                         "action_id": "setting_card",
                         "consequential": False})
        except Exception:  # noqa: BLE001
            pass
        # Setting actions — the existing toggles, via real clicks
        # / methods (checkable buttons read their checked state).
        if hasattr(win, "btn_wake"):
            commands.append(
                {"label": "Toggle wake word",
                 "action": (lambda: win.btn_wake.click()),
                 "kind": "SETTING", "description": "",
                 "action_id": "toggle_wake", "consequential": True})
        if hasattr(win, "_play_briefing"):
            commands.append(
                {"label": "Play briefing", "action": win._play_briefing,
                 "kind": "SETTING", "description": "",
                 "action_id": "play_briefing", "consequential": False})
        if hasattr(win, "btn_mute"):
            commands.append(
                {"label": "Toggle mute",
                 "action": (lambda: win.btn_mute.click()),
                 "kind": "SETTING", "description": "",
                 "action_id": "toggle_mute", "consequential": False})
        if hasattr(win, "_toggle_connection"):
            commands.append(
                {"label": "Connect / Disconnect",
                 "action": win._toggle_connection,
                 "kind": "SETTING", "description": "",
                 "action_id": "toggle_connection",
                 "consequential": True})
        if hasattr(win, "_cycle_theme"):
            commands.append(
                {"label": "Switch theme", "action": win._cycle_theme,
                 "kind": "SETTING", "description": "",
                 "action_id": "switch_theme", "consequential": False})
        # Loop actions (Wiring Build 6) — rows from the Qt-free
        # logic module, mapped here to surfaces that already
        # exist. Navigation rows open a page (re-arm and undo
        # still confirm on the Doctor page itself); the Stop row
        # clicks the existing goal-pill Stop button and, being
        # consequential, gets the palette's second confirmation
        # in _run_row first. A loop row whose target is absent is
        # omitted, never given a dead action. Tool rows below are
        # prefill-only, as before.
        try:
            from voice_assistant.core.gui_facade import (
                loop_action_entries as _loop_entries)
            for entry in _loop_entries():
                aid = entry.get("action_id") or ""
                if aid == "loop_stop_goal":
                    if hasattr(win, "btn_goal_stop"):
                        commands.append(
                            {"label": entry["label"],
                             "action": (lambda: win.btn_goal_stop.click()),
                             "kind": "LOOP",
                             "description": entry.get("description", ""),
                             "action_id": aid,
                             "consequential": True})
                    continue
                page = entry.get("target_page")
                if isinstance(page, int):
                    commands.append(
                        {"label": entry["label"],
                         "action": (lambda p=page: win._show_page(p)),
                         "kind": "LOOP",
                         "description": entry.get("description", ""),
                         "action_id": aid,
                         "consequential": False})
        except Exception:  # noqa: BLE001 — loop rows are optional
            pass
        # Tools — friendly name + the catalogue's own summary, so
        # palette and Skills wording match. Prefill, never direct
        # execution (the brain fills arguments + confirms).
        try:
            from voice_assistant.core.gui_facade import tools as _tools
            for decl in _tools.get_declarations():
                name = decl.get("name") or ""
                if not name:
                    continue
                friendly = _TOOL_FRIENDLY_NAMES.get(name) or _humanise(name)
                summary = _summarise_tool(decl.get("description") or "")
                commands.append(
                    {"label": friendly,
                     "action": (lambda n=name, f=friendly: win._on_galaxy_ask(n, f)),
                     "kind": "TOOL", "description": summary,
                     "action_id": "tool_" + name,
                     "consequential": False})
        except Exception:  # noqa: BLE001 — tools rows are optional
            pass
        return commands

    # ------------------------------------------------------------ behaviour
    @staticmethod
    def _is_subsequence(needle: str, hay: str) -> bool:
        it = iter(hay)
        return all(ch in it for ch in needle)

    _PALETTE_ROW_CAP = 12

    def _apply_filter(self, text: str):
        # Sweep Build 4: rank through the Qt-free logic module when
        # it imports (scored: exact > prefix > word-boundary >
        # substring > description > subsequence); fall back to the
        # shipped substring-then-subsequence order if it does not,
        # so a logic-module problem can never break the palette.
        # Visible rows cap at 12, with an honest line for the rest
        # — the full list is never silently hidden.
        self.list.clear()
        self._visible_actions = []
        self._visible_labels = []
        self._visible_consequential = []
        shown: list[dict] = []
        hidden = 0
        try:
            from voice_assistant.core.gui_facade import (
                filter_entries as _filter_entries)
            shown, hidden = _filter_entries(self._commands, text or "")
        except Exception:  # noqa: BLE001 — fallback below
            needle = (text or "").strip().lower()
            hits: list[dict] = []
            fuzzy: list[dict] = []
            for entry in self._commands:
                low = str(entry.get("label") or "").lower()
                desc = str(entry.get("description") or "").lower()
                if not needle or needle in low or needle in desc:
                    hits.append(entry)
                elif self._is_subsequence(needle, low):
                    fuzzy.append(entry)
            ranked = hits + fuzzy
            shown, hidden = ranked[:self._PALETTE_ROW_CAP], max(
                0, len(ranked) - self._PALETTE_ROW_CAP)
        for entry in shown:
            label = entry.get("label") or ""
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 36))
            self.list.addItem(item)
            self.list.setItemWidget(
                item, self._row_widget(
                    label, entry.get("description") or "",
                    entry.get("kind") or ""))
            self._visible_actions.append(entry.get("action"))
            self._visible_labels.append(label)
            self._visible_consequential.append(
                bool(entry.get("consequential")))
        if hidden:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 36))
            self.list.addItem(item)
            cap_lbl = QLabel(f"… and {hidden} more — keep typing to narrow")
            cap_lbl.setObjectName("paletteHint")
            cap_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            cap_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.list.setItemWidget(item, cap_lbl)
        if not shown:
            # UI interaction slice: an honest empty state. The row
            # is NOT in _visible_actions, so Enter/click on it can
            # never run anything, and no stale row survives a
            # filter that matches nothing.
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 36))
            self.list.addItem(item)
            empty_lbl = QLabel(
                "No matching commands — try a different search")
            empty_lbl.setObjectName("paletteHint")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            empty_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.list.setItemWidget(item, empty_lbl)
        if self._visible_actions:
            self.list.setCurrentRow(0)

    def _row_widget(self, label: str, description: str, kind: str) -> QWidget:
        """One palette row: label [+ " — description"] + kind tag
        (TOOL / PAGE / SETTING, as mocked — no icons).

        Transparent for mouse events so clicks land on the list item
        itself (selection, itemClicked and itemActivated keep working).
        """
        row = QWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(9)
        if description:
            text = f"{label} — {description}"
        else:
            text = label
        # The row is one line in a 560 px dialog — truncate with an
        # ellipsis rather than letting Qt clip mid-word.
        if len(text) > 72:
            text = text[:71].rstrip() + "…"
        text_lbl = QLabel(text)
        layout.addWidget(text_lbl)
        layout.addStretch()
        kind_lbl = QLabel(kind)
        kind_lbl.setObjectName("paletteHint")
        layout.addWidget(kind_lbl)
        return row

    def _activate_item(self, item):
        row = self.list.row(item)
        self._run_row(row)

    def _activate_current(self):
        self._run_row(self.list.currentRow())

    def _run_row(self, row: int):
        if 0 <= row < len(self._visible_actions):
            # Sweep Build 4 (C-14): consequential rows need a second,
            # explicit confirmation here, before the palette closes
            # or the action runs. Default button is No; anything
            # other than Yes leaves the palette open and runs
            # nothing. Tool rows are prefill-only and are never
            # consequential, so the brain's confirmation still runs
            # them; page and cosmetic rows run as before.
            if row < len(self._visible_consequential) \
                    and self._visible_consequential[row]:
                label = self._visible_labels[row] \
                    if row < len(self._visible_labels) else "this action"
                if not HudDialog.confirm(
                    self, "Confirm",
                    f"Run '{label}'?\nThis changes connection or "
                    "capture state."):
                    return
            action = self._visible_actions[row]
            self.close()
            self._last_action_error = None
            try:
                action()  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001
                # UI interaction slice (UI-05): a palette action
                # that throws must NOT vanish. Record it and show
                # an honest failure over the main window — never a
                # swallowed exception, never a fake success. The
                # palette stays closed (it already closed above);
                # the error dialog is the surface, and
                # _last_action_error is the testable record.
                label = self._visible_labels[row] \
                    if row < len(self._visible_labels) else "this action"
                self._last_action_error = f"{label}: {exc}"
                try:
                    parent = self.parent()
                    HudDialog.critical(
                        parent if isinstance(parent, QWidget) else None,
                        "Action failed",
                        f"“{label}” didn’t run: {exc}\n\n"
                        "Nothing was changed by that action. "
                        "Try again, or pick a different command.")
                except Exception:  # noqa: BLE001 — the record above
                    pass  # survives even if the dialog cannot show

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if obj is self.input and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                # UI interaction slice: navigation stays on
                # ACTIONABLE rows only. The cap line and the empty
                # state row are display-only — landing on one and
                # pressing Enter used to do nothing silently.
                n = len(self._visible_actions)
                if n:
                    row = self.list.currentRow()
                    row += 1 if key == Qt.Key.Key_Down else -1
                    row = max(0, min(n - 1, row))
                    self.list.setCurrentRow(row)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        # UI interaction slice: hand focus back to the widget that
        # had it when the palette opened (the invoking control).
        # The restore is DEFERRED past the close: setting focus
        # mid-close does not stick (the palette is still the active
        # modal window, and Qt clears the request when it hides).
        # A dead, hidden, or disabled invoker is left alone, never
        # crashed on.
        target = self._focus_return
        self._focus_return = None
        if target is not None:
            def _restore():
                try:
                    if target.isVisible() and target.isEnabled():
                        target.setFocus()
                except Exception:  # noqa: BLE001 — deleted widget
                    pass

            try:
                QTimer.singleShot(0, _restore)
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    def open_centered(self):
        """Reset, centre over the parent window, show and focus."""
        self._commands = self._build_commands()
        self.input.clear()
        self._apply_filter("")
        # Audit-2 slice 1 (UI-07): keep the dialog inside the work
        # area. Cap the list so the whole dialog fits (no-op on
        # normal screens), then clamp the centred position.
        try:
            scr = self.screen()
            if scr is None:
                scr = QGuiApplication.primaryScreen()
            if scr is not None:
                avail = scr.availableGeometry()
                chrome = self.sizeHint().height() - self.list.height()
                if chrome <= 0:
                    chrome = 62
                self.list.setFixedHeight(
                    palette_list_height_for(avail.height(), chrome))
                self.adjustSize()
        except Exception:  # noqa: BLE001
            pass
        win = self.parent()
        try:
            if win is not None:
                geo = win.geometry()
                x = geo.x() + (geo.width() - self.width()) // 2
                y = geo.y() + max(40, (geo.height() - self.height()) // 4)
                self.move(x, y)
        except Exception:  # noqa: BLE001
            pass
        try:
            scr = self.screen()
            if scr is None:
                scr = QGuiApplication.primaryScreen()
            if scr is not None:
                avail = scr.availableGeometry()
                nx = min(max(self.x(), avail.x()),
                         max(avail.x(), avail.right() - self.width() + 1))
                ny = min(max(self.y(), avail.y()),
                         max(avail.y(), avail.bottom() - self.height() + 1))
                self.move(nx, ny)
        except Exception:  # noqa: BLE001
            pass
        # UI interaction slice: remember who had focus, so
        # closeEvent can hand it back to the invoking control.
        try:
            self._focus_return = QApplication.focusWidget()
        except Exception:  # noqa: BLE001
            self._focus_return = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        # Refinement A: short fade-in so the palette lands softly.
        # UX-safe batch: Motion Off skips it entirely. The palette
        # is a standalone dialog, so it reads the gate off its
        # parent window; an unreachable gate keeps the existing
        # fail-open behaviour (the gate itself returns True on
        # error). Final state is set immediately, no fade.
        try:
            _gate = getattr(self.parent(), "_animate_enabled", None)
            _palette_animate = _gate() if callable(_gate) else True
        except Exception:  # noqa: BLE001
            _palette_animate = True
        if not _palette_animate:
            try:
                self.setWindowOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                anim = QPropertyAnimation(self, b"windowOpacity", self)
                anim.setDuration(DURATION_PALETTE_FADE_MS)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(EASE_DIALOG)
                anim.finished.connect(lambda: self.setWindowOpacity(1.0))
                anim.start()
                self._enter_anim = anim
            except Exception:  # noqa: BLE001
                try:
                    self.setWindowOpacity(1.0)
                except Exception:  # noqa: BLE001
                    pass



# ---------------------------------------------------------------------
# Premium Wave 2 — motion helpers + splash ignition.
#
# _SlideOffset is a tiny QObject whose float ``offset`` property is
# animated instead of the widget's ``pos`` directly: the base position
# is captured lazily on the first animated tick (by then the layout
# has assigned the widget its real geometry), so a fade+rise can
# never strand a widget at a pre-layout (0, 0) origin. ``snap()``
# restores the exact base position — used by finished handlers and
# by the watchdog timers that guarantee a final state even when an
# animation is interrupted (offscreen harness, rapid switching).
# ---------------------------------------------------------------------


class ShortcutsSheet(QDialog):
    """v5.57 (redo, A9.2): the "?" sheet — the essential shortcuts,
    with the full list still living in Settings. Rows come from
    SHORTCUT_ROWS (essentials only); the footer points at Settings.
    Frameless + modal like the palette; Esc closes."""

    _ESSENTIALS = ("Ctrl+K", "?", "Ctrl+F", "Enter", "Esc",
                   "Ctrl+Shift+Space")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(True)
        self.setFixedWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        title = QLabel("KEYBOARD SHORTCUTS")
        title.setStyleSheet(f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;"
                            f" color: {TEXT};")
        layout.addWidget(title)
        for keys, what in SHORTCUT_ROWS:
            if keys not in self._ESSENTIALS:
                continue
            row = QHBoxLayout()
            row.setSpacing(12)
            keys_lbl = QLabel(f"<b>{keys}</b>")
            keys_lbl.setFixedWidth(150)
            row.addWidget(keys_lbl)
            what_lbl = QLabel(what)
            what_lbl.setObjectName("muted")
            what_lbl.setWordWrap(True)
            row.addWidget(what_lbl, stretch=1)
            layout.addLayout(row)
        footer = QLabel(
            "Full list lives in Settings → Keyboard shortcuts.")
        footer.setObjectName("paletteHint")
        layout.addWidget(footer)

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def open_centered(self):
        """Centre over the parent window and show (reused, like
        the palette — no state to reset)."""
        win = self.parent()
        try:
            if win is not None:
                geo = win.geometry()
                x = geo.x() + (geo.width() - self.width()) // 2
                y = geo.y() + max(40, (geo.height() - self.height()) // 4)
                self.move(x, y)
        except Exception:  # noqa: BLE001
            pass
        self.show()
        self.raise_()
        self.activateWindow()
