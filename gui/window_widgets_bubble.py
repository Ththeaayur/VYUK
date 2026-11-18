# Refactor Phase 1 step 5: moved verbatim from gui/window_widgets.py (split by family).
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
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
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
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
import datetime

from .window_widgets_chatbits import _CodeBlock
from .window_widgets_helpers import _md_lite
from .window_widgets_helpers import _rendered_plain
from .window_widgets_helpers import _split_segments
from .window_widgets_helpers import strip_markdown_plain
from .style_tokens import MONO_FONT_STACK, first_font_family




# v5.51 item 12: hover-action geometry. The generic QPushButton
# sheet (gui/style.py) pads buttons 8px vertical / 16px horizontal
# with a 1px border; the actions row derives its button sizes from
# those numbers plus each button's OWN font metrics at placement
# time, so a label fits under whatever metrics are current — never
# a size frozen at construction (the v5.16 failure class, this
# time inside the actions row itself).
_ACTION_PAD_X = 16

_ACTION_PAD_Y = 8

_ACTION_BORDER = 1

# The sender line sits at the content layouts' top margin (12px
# on both bubble kinds); the row hangs below it, clear of its
# rect — the old fixed y=6 corner slot always overlapped it, and
# under scaled metrics the row rode up over the bubble's top edge.
_ACTIONS_TOP_MARGIN = 12

_ACTIONS_WHO_GAP = 3

_ACTIONS_RIGHT_INSET = 10

_ACTIONS_BOTTOM_INSET = 4

_ACTIONS_MIN_TOP = 2

# Chat UI (2026-11-16): restored previous-day messages render
# slightly muted — the live reply is the only thing that should
# read at full strength. The fade-in ends at this opacity for a
# dimmed bubble and its finish re-installs the dim effect.
_DIMMED_OPACITY = 0.6

class ChatBubble(QFrame):
    """One conversation message — user right, VYUK left, fade-in on arrival.

    U6 (visual channel): a bubble may also carry an image — a diagram
    the tutor drew, or a figure from a course file he supplied — with
    its caption and source attribution. The image is keyword-only
    (``image`` a QPixmap/QImage, ``caption``/``source`` already
    gold-guarded by ``visual_channel.build_image_message``); the
    bubble never sees the gold."""

    def __init__(self, who: str, text: str, is_user: bool,
                 time_str: str | None = None,
                 image=None, caption=None, source=None,
                 spoken: bool = False):
        super().__init__()
        self.setObjectName("bubbleUser" if is_user else "bubbleNova")
        # UI a11y foundations (2026-10-30): bubbles are keyboard
        # stops, so their hover actions are reachable without a
        # mouse. Focus shows the row exactly like hover does;
        # focus leaving the bubble subtree hides it again (see
        # focusInEvent/focusOutEvent/_maybe_hide_actions below).
        # A focused bubble also carries its sender as its
        # accessible name; the body text stays readable as labels.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Message from {who}")
        self.setMaximumWidth(560)
        self._plain_text = text
        self._is_user = is_user
        # Chat UI (2026-11-16): the window sets these after
        # construction — the transcript-tree id this bubble
        # renders, and the callback every action reports to.
        # (Actions report a kind string; the window owns the
        # behaviour. Copy stays local: it needs no window.)
        self._chat_msg_id = None
        self._on_chat_action = None
        # Chat UI (2026-11-16): a streaming bubble is a live
        # reply still arriving; a dimmed bubble is a restored
        # previous-day message. Both are set after construction
        # by the window, never by the bubble itself.
        self._stream_lbl = None
        self._chat_dimmed = False
        self._status_row = None
        self._status_lbl = None
        self._status_retry = None
        self._version_row = None
        self._version_lbl = None
        self._version_note = None
        self._quote_btn = None
        self._body_widgets = []
        # UI rethink Build 1: the diamond chip is removed — her
        # bubbles use the same single-column layout as the user's.
        # The chip was a v5.45 F8 avatar column; the sender label
        # (10.5 mono caps, group head only) names the speaker now,
        # and the bubble's width is the text's, not the chip's.
        # Padding is 10 px vertical / 12 px horizontal (§6.2).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        # Chat UI (2026-11-16): the content layout is where body
        # widgets, the quote anchor, and the foot rows live — kept
        # so set_text can rebuild the body in place.
        self._content_layout = layout
        # UI rethink Build 1: the sender label is caps in the mono
        # face (see QLabel#bubbleWho) — the name, not a sentence.
        # The "· spoken" qualifier stays as-is; it is a note, not
        # the name. The accessible name above keeps the plain word.
        who_lbl = QLabel(who.upper() + (" · spoken" if spoken else ""))
        who_lbl.setObjectName("bubbleWho")
        self.who_lbl = who_lbl
        self._spoken = bool(spoken)
        # Chat UI (2026-11-16): the body is rebuilt in place by
        # set_text (version flip / edit), so construction goes
        # through the same builder and the layout is kept for
        # re-insertion. Prose-only messages — the common case —
        # are exactly the old single label.
        segments = _split_segments(text)
        self._msg_label = None
        body_widgets = self._build_body_widgets(segments)
        self._body_widgets = body_widgets
        self.time_lbl = QLabel(
            time_str if time_str is not None
            else datetime.datetime.now().strftime("%H:%M")
        )
        self.time_lbl.setObjectName("bubbleTime")
        self.time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(who_lbl)
        for _bw in body_widgets:
            layout.addWidget(_bw)
        # U6 (visual channel): an image message — a diagram the
        # tutor drew, or a figure from a course file he supplied
        # (D-T2). The pixmap is shown scaled to the bubble width
        # with its guarded caption and source attribution. The
        # caption/source strings arrive already gold-guarded
        # (visual_channel.build_image_message); the bubble never
        # sees the gold and never guards anything itself.
        self.image_lbl = None
        self.caption_lbl = None
        self.source_lbl = None
        self._image_w = 0
        if image is not None:
            pixmap = None
            if isinstance(image, QPixmap):
                pixmap = image
            elif isinstance(image, QImage):
                pixmap = QPixmap.fromImage(image)
            if pixmap is not None and not pixmap.isNull():
                scaled = pixmap.scaledToWidth(
                    520, Qt.TransformationMode.SmoothTransformation)
                self.image_lbl = QLabel()
                self.image_lbl.setObjectName("bubbleImage")
                self.image_lbl.setPixmap(scaled)
                self.image_lbl.setAlignment(
                    Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(self.image_lbl)
                self._image_w = scaled.width()
            if caption:
                self.caption_lbl = QLabel(caption)
                self.caption_lbl.setObjectName("bubbleCaption")
                self.caption_lbl.setWordWrap(True)
                layout.addWidget(self.caption_lbl)
                self._plain_text = self._plain_text + "\n" + caption
            if source:
                self.source_lbl = QLabel(f"Source: {source}")
                self.source_lbl.setObjectName("bubbleSource")
                self.source_lbl.setWordWrap(True)
                layout.addWidget(self.source_lbl)
        # Chat UI (2026-11-16): the hover row earns three
        # actions — Copy on every bubble, Regenerate on VYUK's,
        # Edit on the user's. Everything else (Read aloud, Pin,
        # Delete, Copy as plain text, Quote / Reply) lives behind
        # one overflow ("⋯") menu, not on the row. The row is
        # built from a button list so _fit_actions_row /
        # _place_actions / _tab_tail all follow the same list
        # instead of a fixed copy/speak pair.
        actions = QHBoxLayout()
        actions.setSpacing(6)
        # v5.51 item 12: the row is an overlay chip, not a layout
        # citizen — zero margins so its size is exactly its buttons
        # plus spacing, with no phantom 9px frame inflating it.
        actions.setContentsMargins(0, 0, 0, 0)
        self.time_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        actions.addWidget(self.time_lbl)
        self._time_dot = QLabel("·")
        self._time_dot.setObjectName("muted")
        actions.addWidget(self._time_dot)
        self._action_buttons = []
        # Narrow-bubble hide order (see _place_actions): the
        # earned row action goes first — a one-word reply rarely
        # needs regenerating — the overflow stays, because it
        # gates five actions and is the smaller button.
        self._hideable_buttons = []
        self._btn_copy = self._make_action_button(
            "Copy", "Copy message",
            "Copy this message to the clipboard", self._copy_text)
        actions.addWidget(self._btn_copy)
        self._btn_regen = None
        self._btn_edit = None
        if not is_user:
            self._btn_regen = self._make_action_button(
                "Regenerate", "Regenerate answer",
                "Ask for a different answer to the same message",
                lambda: self._emit_action("regenerate"))
            actions.addWidget(self._btn_regen)
            self._hideable_buttons.append(self._btn_regen)
        else:
            self._btn_edit = self._make_action_button(
                "Edit", "Edit message",
                "Fix this message and get a new answer",
                lambda: self._emit_action("edit"))
            actions.addWidget(self._btn_edit)
            self._hideable_buttons.append(self._btn_edit)
        self._btn_overflow = self._make_action_button(
            "⋯", "More actions",
            "More actions for this message",
            self._show_overflow_menu)
        actions.addWidget(self._btn_overflow)
        self._hideable_buttons.append(self._btn_overflow)
        self._pinned = False
        actions.addStretch()
        self._actions_row = QWidget(self)
        # v5.51 item 12: the QSS chip rule (QWidget#bubbleActions,
        # gui/style.py) gives the row its opaque background.
        self._actions_row.setObjectName("bubbleActions")
        self._actions_row.setLayout(actions)
        # Phase 2 (chatbox #11): the actions OVERLAY the bubble's
        # top-right corner instead of living in its layout — they
        # appear on hover without growing the bubble or shoving
        # the conversation down, and bubbles stay compact.
        self._actions_row.setVisible(False)
        self._actions_row.adjustSize()
        # Focus changes on the row's buttons route through the
        # bubble's event filter so the row hides once focus
        # leaves the bubble subtree (see _maybe_hide_actions).
        for _btn in self._action_buttons:
            _btn.installEventFilter(self)
        # Deterministic width from the text itself — see
        # _refit_width (re-run whenever the body, the quote, or
        # a foot row changes, so a version flip or an edit
        # re-measures instead of keeping a stale width).
        self._refit_width()

    def _make_action_button(self, text, acc_name, acc_desc, slot):
        """One hover-row button — same sheet, cursor, and
        accessible labelling for every row action, so the row
        never grows a one-off button with a missing name."""
        btn = QPushButton(text)
        btn.setObjectName("bubbleAction")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName(acc_name)
        btn.setAccessibleDescription(acc_desc)
        btn.clicked.connect(slot)
        self._action_buttons.append(btn)
        return btn

    def _emit_action(self, kind: str):
        """Report a row/overflow/foot action to the window.
        Copy and Copy-as-plain-text stay local (they need no
        window); everything else is the window's behaviour."""
        try:
            cb = getattr(self, "_on_chat_action", None)
            if cb is not None:
                cb(kind, self)
        except Exception:  # noqa: BLE001
            pass

    def _show_overflow_menu(self):
        """The one overflow menu: Read aloud (VYUK's only),
        Pin / Unpin, Delete, Copy as plain text, Quote / Reply.
        Built fresh on every open so Pin reads the current
        state instead of a stale label."""
        try:
            menu = QMenu(self)
            if not self._is_user:
                menu.addAction("Read aloud",
                               self._read_aloud)
            menu.addAction("Unpin" if self._pinned else "Pin",
                           lambda: self._emit_action("unpin"
                                                     if self._pinned
                                                     else "pin"))
            menu.addAction("Delete",
                           lambda: self._emit_action("delete"))
            menu.addAction("Copy as plain text",
                           self._copy_plain_text)
            menu.addAction("Quote / Reply",
                           lambda: self._emit_action("quote"))
            menu.exec(self._btn_overflow.mapToGlobal(
                QPoint(0, self._btn_overflow.height())))
        except Exception:  # noqa: BLE001
            pass

    def set_pinned_state(self, pinned: bool):
        """The window keeps the pin in the thread model; the
        bubble only mirrors it for the overflow menu label."""
        self._pinned = bool(pinned)

    # ------------------------------------------------- hover actions
    def _fit_actions_row(self):
        """(Re)size the actions row from its buttons' live font
        metrics — v5.51 item 12. The construction-time adjustSize()
        froze whatever metrics were current before the app font /
        theme finished applying; when metrics later grew (the
        user's scaled Windows setup, a Settings font-scale change)
        the frozen row clipped its labels centre-first ("Conv").
        Fitting here — at placement time, fonts final — makes the
        measured size the rendered size."""
        for btn in self._action_buttons:
            if btn is None:
                continue
            fm = QFontMetrics(btn.font())
            need_w = (fm.horizontalAdvance(btn.text())
                      + 2 * (_ACTION_PAD_X + _ACTION_BORDER))
            need_h = (fm.height()
                      + 2 * (_ACTION_PAD_Y + _ACTION_BORDER))
            hint = btn.sizeHint()
            btn.setFixedWidth(max(hint.width(), need_w))
            btn.setFixedHeight(max(hint.height(), need_h))
        row = self._actions_row
        row.adjustSize()
        row.setFixedSize(row.sizeHint())

    def _place_actions(self):
        row = self._actions_row
        self._fit_actions_row()
        # v5.57.1 (item 11a), generalised (chat UI, 2026-11-16):
        # in a narrow bubble the row can exceed the bubble width;
        # x then clamps to 6 and Qt clips the row's right side at
        # the bubble edge. Hide the hideable buttons in priority
        # order (Regenerate / Edit first, then the overflow) and
        # restore them whenever they fit again — timestamp + Copy
        # alone fit inside the 150 px floor. Every caller of this
        # method (resize, enter, font/style change) re-runs the
        # check, so a button never stays hidden after a widen.
        avail = self.width() - 6 - _ACTIONS_RIGHT_INSET
        for btn in self._hideable_buttons:
            if btn is not None and btn.isHidden():
                btn.show()
        self._fit_actions_row()
        for btn in self._hideable_buttons:
            if btn is None or btn.isHidden():
                continue
            if row.width() <= avail:
                break
            btn.hide()
            self._fit_actions_row()
        # v5.51 item 12: hang the row BELOW the sender line, never
        # over it. who_lbl spans the full content width, so the old
        # y=6 slot always intersected its rect, and under scaled
        # metrics the row escaped the bubble's top edge entirely.
        # The sender slot stays reserved even when grouping hides
        # the label, so the row's home is identical on every
        # bubble; the bottom clamp keeps it inside short bubbles.
        who_h = self.who_lbl.height()
        if who_h <= 0:
            who_h = QFontMetrics(self.who_lbl.font()).height()
        who_top = self.who_lbl.y()
        if who_top <= 0:
            who_top = _ACTIONS_TOP_MARGIN
        top = who_top + who_h + _ACTIONS_WHO_GAP
        max_top = (self.height() - row.height()
                   - _ACTIONS_BOTTOM_INSET)
        if max_top >= _ACTIONS_MIN_TOP:
            top = min(top, max_top)
        row.move(max(6, self.width() - row.width()
                     - _ACTIONS_RIGHT_INSET),
                 max(_ACTIONS_MIN_TOP, top))
        row.raise_()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        try:
            self._place_actions()
        except Exception:  # noqa: BLE001
            pass

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        try:
            self._place_actions()
            self._actions_row.setVisible(True)
        except Exception:  # noqa: BLE001
            pass
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        try:
            # The mouse leaving must not hide a row whose button
            # currently holds keyboard focus — that would strand
            # the focused button invisible mid-task.
            fw = QApplication.focusWidget()
            if fw is None or (fw is not self
                              and not self.isAncestorOf(fw)):
                self._actions_row.setVisible(False)
        except Exception:  # noqa: BLE001
            pass
        super().leaveEvent(event)

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if (event.type() == QEvent.Type.FocusOut
                and watched in self._action_buttons):
            self._schedule_hide_actions()
        return super().eventFilter(watched, event)

    def focusInEvent(self, event):  # noqa: N802 (Qt naming)
        try:
            self._place_actions()
            self._actions_row.setVisible(True)
        except Exception:  # noqa: BLE001
            pass
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 (Qt naming)
        self._schedule_hide_actions()
        super().focusOutEvent(event)

    def _tab_tail(self):
        """Last focusable widget of this bubble — the Tab chain's
        anchor for the next bubble / composer. The foot rows
        (status Retry, version flip) come after the hover row in
        tab order when visible, so a visible foot row's last
        button is the tail instead. The hover row itself is NOT
        visibility-scanned: the row is hidden until the bubble
        takes focus, and the chain is built while it is hidden —
        scanning it would return the Copy button and strand the
        Regenerate / Edit / overflow buttons after the composer,
        out of the keyboard user's reach. Qt skips hidden widgets
        while tabbing, so row order is the right tail whether the
        row is showing or not."""
        for row in (getattr(self, "_version_row", None),
                    getattr(self, "_status_row", None)):
            if row is not None and row.isVisible():
                btns = [w for w in row.findChildren(QPushButton)
                        if w.isVisible()]
                if btns:
                    return btns[-1]
        for btn in reversed(self._action_buttons):
            if btn is not None:
                return btn
        return self._btn_copy

    def _schedule_hide_actions(self):
        # Focus moves before FocusOut is delivered, but the new
        # focus widget is not guaranteed readable inside the
        # event itself — re-check on the next event-loop turn.
        QTimer.singleShot(0, self._maybe_hide_actions)

    def _maybe_hide_actions(self):
        try:
            fw = QApplication.focusWidget()
        except Exception:  # noqa: BLE001
            fw = None
        if fw is None or (fw is not self
                          and not self.isAncestorOf(fw)):
            try:
                self._actions_row.setVisible(False)
            except Exception:  # noqa: BLE001
                pass

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        super().changeEvent(event)
        try:
            if (getattr(self, "_actions_row", None) is not None
                    and event.type() in (QEvent.Type.FontChange,
                                         QEvent.Type.StyleChange,
                                         QEvent.Type.PaletteChange)):
                # v5.51 item 12: fonts and themes change under
                # live bubbles (Settings font scale, theme
                # switch) — refit and re-place so the row never
                # keeps metrics from before the change.
                self._place_actions()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- grouping (#7)
    def set_group_head(self, head: bool):
        """Only the first bubble of a same-speaker run carries
        the name row."""
        self.who_lbl.setVisible(bool(head))

    def set_time_visible(self, visible: bool):
        """Only the last bubble of a run carries the timestamp."""
        self.time_lbl.setVisible(bool(visible))
        # v5.57 (redo): the dot separators the timestamp from Copy
        # in the hover row — it hides with the timestamp.
        dot = getattr(self, "_time_dot", None)
        if dot is not None:
            dot.setVisible(bool(visible))

    def _copy_text(self):
        try:
            QGuiApplication.clipboard().setText(self._plain_text)
        except Exception:  # noqa: BLE001
            pass

    def _copy_plain_text(self):
        """Copy as plain text: the words, without the markdown
        markers — a pasted quote should read clean."""
        try:
            QGuiApplication.clipboard().setText(
                strip_markdown_plain(self._plain_text))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- chat UI: body
    def _build_body_widgets(self, segments):
        """Build the body widgets for a text — prose renders
        markdown-lite (escaped first, so model or web text can
        never inject markup of its own; links are clickable),
        fenced code gets a real monospace block with its own
        Copy button. The first prose label becomes _msg_label
        (Read aloud reads a selection from it)."""
        widgets = []
        for kind, seg in segments:
            if kind == "code":
                widgets.append(_CodeBlock(seg))
            else:
                lbl = QLabel()
                lbl.setObjectName("bubbleText")
                lbl.setTextFormat(Qt.TextFormat.RichText)
                # #20: roomier leading for reading.
                lbl.setText('<div style="line-height: 140%">'
                            + _md_lite(seg) + "</div>")
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextBrowserInteraction)
                lbl.setOpenExternalLinks(True)
                widgets.append(lbl)
                if self._msg_label is None:
                    self._msg_label = lbl
        return widgets

    def _body_index(self):
        """Where body widgets sit in the content layout: after
        the sender line, and after the quote anchor when one is
        shown."""
        return 1 + (1 if self._quote_btn is not None else 0)

    def _refit_width(self):
        """Deterministic width from the text itself: a wrapped
        QLabel inside a stretchy row otherwise collapses to its
        minimum width whenever a sibling toggles visibility.
        (Caught by render-check.) Re-run whenever the body, the
        quote, or a foot row changes, so a version flip, an edit,
        or a growing live reply re-measures instead of keeping a
        stale width. The window caps it against the live
        viewport width in _fit_chat_item."""
        segments = _split_segments(self._plain_text)
        body_widgets = self._body_widgets
        _lbl0 = next((x for x in body_widgets
                      if isinstance(x, QLabel)), None)
        fm = QFontMetrics(
            _lbl0.font() if _lbl0 is not None else self.font())
        text_w = 0
        for kind, seg in segments:
            if kind == "code":
                # B-R1-7: code segments render in the mono stack
                # (Share Tech Mono first), so the width calc must
                # measure that face — measuring Consolas (narrower)
                # under-measures the width and the bubble clips
                # the code it claims to show.
                cfm = QFontMetrics(
                    QFont(first_font_family(MONO_FONT_STACK), 12))
                text_w = max(text_w, max(
                    cfm.horizontalAdvance(ln)
                    for ln in seg.split("\n")))
            else:
                text_w = max(
                    text_w, fm.horizontalAdvance(_rendered_plain(seg)))
        # U6: an image bubble hugs the image width too.
        if self._image_w:
            text_w = max(text_w, self._image_w)
        who_w = QFontMetrics(self.who_lbl.font()).horizontalAdvance(
            self.who_lbl.text())
        time_w = QFontMetrics(self.time_lbl.font()).horizontalAdvance(
            self.time_lbl.text()
        )
        # Foot rows (status, versions) must fit too — a short
        # reply with a "1 of 2" footer is as wide as the footer.
        for row in (self._status_row, self._version_row):
            if row is not None and row.isVisible():
                text_w = max(text_w, row.sizeHint().width())
        # UI rethink Build 1: no chip to budget anymore — the width
        # is the text's (plus the 12 + 12 horizontal padding and a
        # little slack), capped at 560. The window's viewport cap
        # in _fit_chat_item still applies on top, unchanged.
        self._content_w = max(150, min(560, max(text_w, who_w,
                                               time_w) + 30))
        self.setProperty("content_w", self._content_w)
        self.setFixedWidth(self._content_w)

    def set_text(self, new_text: str):
        """Replace the body in place (version flip / edit) and
        re-measure. The quote anchor, status row, and version
        footer are untouched — they sit outside the body slot."""
        try:
            self._plain_text = new_text
            for w in list(self._body_widgets):
                self._content_layout.removeWidget(w)
                w.deleteLater()
            self._msg_label = None
            segments = _split_segments(new_text)
            widgets = self._build_body_widgets(segments)
            self._body_widgets = widgets
            idx = self._body_index()
            for w in widgets:
                w.setVisible(True)
                self._content_layout.insertWidget(idx, w)
                idx += 1
            self._refit_width()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- chat UI: live reply
    def start_streaming(self):
        """A live reply begins: the (empty) segment body hides
        and a single streaming label takes its place. Words land
        here as they arrive — update_stream, never a timer."""
        try:
            if self._stream_lbl is not None:
                return
            for w in self._body_widgets:
                w.setVisible(False)
            lbl = QLabel()
            lbl.setObjectName("bubbleText")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction)
            lbl.setOpenExternalLinks(True)
            self._stream_lbl = lbl
            self._content_layout.insertWidget(self._body_index(), lbl)
        except Exception:  # noqa: BLE001
            pass

    def update_stream(self, html: str, plain_text: str):
        """One more real chunk: render the revealed words (the
        current word lit by the caller) and re-measure, so the
        bubble grows with the reply up to its cap."""
        try:
            if self._stream_lbl is None:
                self.start_streaming()
            self._plain_text = plain_text
            self._stream_lbl.setText(
                '<div style="line-height: 140%">' + html + "</div>")
            self._refit_width()
        except Exception:  # noqa: BLE001
            pass

    def finish_stream(self, final_text: str):
        """The reply is done (or stopped): the streaming label
        goes and the full text renders through the normal
        segment pipeline — markdown, code blocks, everything."""
        try:
            if self._stream_lbl is not None:
                self._content_layout.removeWidget(self._stream_lbl)
                self._stream_lbl.deleteLater()
                self._stream_lbl = None
            for w in self._body_widgets:
                w.setVisible(True)
            self.set_text(final_text)
        except Exception:  # noqa: BLE001
            pass

    def set_streaming_done(self):
        """A streaming bubble whose final text never arrives
        (a stopped reply with nothing yet said): drop the
        streaming label and show the empty body again."""
        try:
            if self._stream_lbl is not None:
                self._content_layout.removeWidget(self._stream_lbl)
                self._stream_lbl.deleteLater()
                self._stream_lbl = None
            for w in self._body_widgets:
                w.setVisible(True)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- chat UI: foot rows
    def _foot_row(self):
        """A foot row (status / versions): a quiet row at the
        bubble's foot, appended after the body and any image."""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        self._content_layout.addWidget(row)
        return row, layout

    def set_status(self, kind: str, text: str, show_retry: bool):
        """The stopped / failed line under a reply: plain words
        about what happened, with a Retry beside it when the
        turn can be tried again. ``kind`` is "stopped" or
        "failed"; ``text`` is the plain-words line. Passing an
        empty text clears the row."""
        try:
            if not text:
                if self._status_row is not None:
                    self._status_row.setVisible(False)
                return
            if self._status_row is None:
                row, layout = self._foot_row()
                lbl = QLabel()
                lbl.setObjectName("muted")
                lbl.setWordWrap(True)
                layout.addWidget(lbl, 1)
                retry = QPushButton("Retry")
                retry.setObjectName("bubbleAction")
                retry.setCursor(Qt.CursorShape.PointingHandCursor)
                retry.setAccessibleName("Retry this reply")
                retry.clicked.connect(
                    lambda: self._emit_action("retry"))
                layout.addWidget(retry)
                self._status_row = row
                self._status_lbl = lbl
                self._status_retry = retry
            self._status_lbl.setText(text)
            self._status_retry.setVisible(bool(show_retry))
            self._status_row.setVisible(True)
            # A failed reply has no body to show — hide the
            # empty prose label so the row is the bubble.
            if kind == "failed" and not self._plain_text.strip():
                for w in self._body_widgets:
                    w.setVisible(False)
            self._refit_width()
        except Exception:  # noqa: BLE001
            pass

    def set_versions(self, count: int, active: int, note: str = ""):
        """The flip control: "‹ 1 of 2 ›" with a plain-words
        note ("Edited — new answer", "Regenerated") when the
        active version is not the original. Hidden when there
        is only one version — nothing to flip between."""
        try:
            if count <= 1:
                if self._version_row is not None:
                    self._version_row.setVisible(False)
                return
            if self._version_row is None:
                row, layout = self._foot_row()
                prev = QPushButton("‹")
                prev.setObjectName("bubbleAction")
                prev.setCursor(Qt.CursorShape.PointingHandCursor)
                prev.setAccessibleName("Previous version")
                prev.clicked.connect(
                    lambda: self._emit_action("flip_prev"))
                layout.addWidget(prev)
                lbl = QLabel()
                lbl.setObjectName("muted")
                layout.addWidget(lbl)
                nxt = QPushButton("›")
                nxt.setObjectName("bubbleAction")
                nxt.setCursor(Qt.CursorShape.PointingHandCursor)
                nxt.setAccessibleName("Next version")
                nxt.clicked.connect(
                    lambda: self._emit_action("flip_next"))
                layout.addWidget(nxt)
                note_lbl = QLabel()
                note_lbl.setObjectName("muted")
                layout.addWidget(note_lbl, 1)
                self._version_row = row
                self._version_lbl = lbl
                self._version_note = note_lbl
            self._version_lbl.setText(f"{active + 1} of {count}")
            self._version_note.setText(note or "")
            self._version_note.setVisible(bool(note))
            self._version_row.setVisible(True)
            self._refit_width()
        except Exception:  # noqa: BLE001
            pass

    def set_quote(self, quote_text: str):
        """The quote anchor: one quiet line above the body —
        the first line of the message being answered, in quotes.
        A flat button, so it is keyboard-focusable; tapping it
        jumps back to the quoted message."""
        try:
            if not (quote_text or "").strip():
                return
            if self._quote_btn is None:
                btn = QPushButton()
                btn.setFlat(True)
                # The quote anchor is a button, not a label, so
                # the QLabel#muted rule never coloured it — and
                # its left alignment lives in the QSS template
                # (QPushButton#bubbleQuote), not an inline style
                # call: the audit2s1 cap (144) binds every inline
                # style call in gui/*.py, comments included.
                btn.setObjectName("bubbleQuote")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda: self._emit_action("quote_jump"))
                self._quote_btn = btn
                self._content_layout.insertWidget(1, btn)
            _first = str(quote_text).strip().split("\n")[0]
            if len(_first) > 60:
                _first = _first[:57] + "…"
            self._quote_btn.setText(f"“{_first}”")
            self._quote_btn.setVisible(True)
        except Exception:  # noqa: BLE001
            pass

    def set_dimmed(self, dimmed: bool):
        """A restored previous-day message renders slightly
        muted — the live reply is the only thing that should
        read at full strength. The flag is what _fade_in reads:
        the fade ends at the dimmed opacity and its finish
        re-installs the dim effect, so the dimming survives the
        entrance animation instead of being undone by it."""
        self._chat_dimmed = bool(dimmed)
        try:
            if dimmed:
                effect = self.graphicsEffect()
                if not isinstance(effect, QGraphicsOpacityEffect):
                    effect = QGraphicsOpacityEffect(self)
                    self.setGraphicsEffect(effect)
                effect.setOpacity(_DIMMED_OPACITY)
            else:
                self.setGraphicsEffect(None)
        except Exception:  # noqa: BLE001
            pass

    def _read_aloud(self):
        # v5.17 (Mouth M16): a selection inside the bubble is spoken
        # instead of the whole message — "speak this" for the part
        # the user actually picked out.
        try:
            from ..core.gui_facade import speak_text
            selected = ""
            label = getattr(self, "_msg_label", None)
            if label is not None:
                try:
                    selected = (label.selectedText() or "").strip()
                except Exception:  # noqa: BLE001
                    selected = ""
            speak_text(selected if selected else self._plain_text)
        except Exception:  # noqa: BLE001
            pass
