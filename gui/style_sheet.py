"""QSS templates for VYUK (split from gui/style.py, GUI split #6).

The house sheet (PREMIUM_STYLE) and the standalone-dialog sheet
(DIALOG_STYLE), generated from the tokens in style_tokens.
"""

from string import Template

from . import style_tokens

# ------------------------------------------------------------- the stylesheet
# One generated sheet (string.Template over the tokens above). Rule
# order is the cascade: generic widgets first, objectName rules after,
# so a specific rule only has to state what it changes.
_QSS = Template("""
/* ================= base ================= */
QMainWindow { background-color: ${BG}; }
QWidget {
    font-family: 'Sora', 'Segoe UI Variable', 'Segoe UI', sans-serif;
    font-size: 13px;
    color: ${TEXT};
    selection-background-color: ${SELECTION_BG};
    selection-color: #FFFFFF;
}

/* ================= window chrome ================= */
QFrame#appShell {
    background-color: ${BG};
    border: 1px solid ${BORDER};
    border-radius: ${RADIUS_WINDOW}px;
}
QWidget#titleBar { background: transparent; }
QLabel#titleBarName { color: ${TEXT_FAINT}; font-size: 12px; }

/* Stage 0 features batch 5 (Light theme, render-found): the
   first-run setup banner carried a scoped sheet with the house
   SURFACE_2, so on Light it stayed a black bar with theme-dark
   text — unreadable. The strip and its 4 px progress bar live
   here now, so they follow the theme: the strip is the resting
   surface, the bar's track is the hairline, the chunk is the
   theme accent. */
QFrame#setupBanner {
    background: ${SURFACE_2};
    border-bottom: 1px solid ${LINE_SUBTLE};
}
QProgressBar#setupBar {
    background: ${LINE_SUBTLE}; border: none; border-radius: 2px;
}
QProgressBar#setupBar::chunk {
    background: ${ACCENT}; border-radius: 2px;
}
QPushButton#winBtn {
    background: transparent; border: none;
    border-radius: ${RADIUS_SM}px; padding: 0px;
}
QPushButton#winBtn:hover { background-color: rgba(255, 255, 255, 20); }
QPushButton#winBtn:pressed { background-color: rgba(255, 255, 255, 32); }
QPushButton#winBtn:focus {
    border: none; background-color: rgba(53, 214, 255, 48);
}
QPushButton#winClose:hover {
    background-color: rgba(255, 84, 104, 170); color: #FFFFFF;
}

/* ================= sidebar ================= */
QFrame#sidebar {
    background-color: rgba(255, 255, 255, 4);
    border-right: 1px solid ${BORDER_FAINT};
}
QLabel#brandMark {
    background: ${ACCENT};
    border: 1px solid ${PRIMARY_RIM};
    color: ${BG};
    font-size: 15px;
    font-weight: 700;
    border-radius: ${RADIUS_BTN}px;
}
QLabel#brandName { font-size: 16px; font-weight: 600; font-family: ${DISPLAY_FONT_STACK}; }
QPushButton#navBtn {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: ${RADIUS_BTN}px;
    padding: 8px 12px 8px 9px;
    text-align: left;
    color: ${TEXT_MUTED};
    font-size: 13px;
}
QPushButton#navBtn:hover {
    background-color: rgba(255, 255, 255, 9); color: ${TEXT};
}
QPushButton#navBtn:pressed { background-color: rgba(255, 255, 255, 18); }
QPushButton#navBtn:checked {
    background-color: rgba(53, 214, 255, 30); color: ${TEXT};
    border-left: 3px solid ${ACCENT};
}
QPushButton#navBtn:focus {
    /* Command Deck Phase E (render-found): focus must stay
    visible for keyboard users, but it must NOT read as the
    current page — the old 48 wash was identical to :checked,
    so the first-focusable Home row looked selected on every
    page. The faint 14 wash + accent bar marks focus alone;
    :checked keeps the strong 30 wash + white text. */
    border: none; border-left: 3px solid ${ACCENT};
    background-color: rgba(53, 214, 255, 14);
}
QFrame#privacyCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(61, 139, 255, 26), stop:1 rgba(61, 139, 255, 12));
    border: 1px solid rgba(61, 139, 255, 44);
    border-top: 1px solid rgba(61, 139, 255, 90);
    border-radius: ${RADIUS_CARD}px;
}
QLabel#privacyTitle {
    color: ${ACCENT_2}; font-weight: 600; font-size: ${TYPE_CARD_TITLE}px;
}
QLabel#privacyBody { color: ${TEXT_MUTED}; font-size: 11px; }

/* ================= page type ================= */
QLabel#pageTitle { font-size: ${TYPE_TITLE}px; font-weight: 600; font-family: ${DISPLAY_FONT_STACK}; }
/* UI rethink Build 1 (2026-11-16): the greeting is the room's
   display line — 20 in Orbitron, the only Orbitron line on the
   page. The 28 rung read as costume at that size; the face stays,
   the size steps down to the title rung. */
QLabel#homeGreeting { font-size: ${TYPE_TITLE}px; font-weight: 600; font-family: ${DISPLAY_FONT_STACK}; color: ${TEXT}; }
QLabel#sectionTitle {
    font-size: ${TYPE_CARD_TITLE}px; font-weight: 600; color: ${TEXT};
}
QLabel#muted { color: ${TEXT_MUTED}; }
QLabel#smallMuted { color: ${TEXT_FAINT}; font-size: 11px; }
/* Sci-fi Home: the state line steps down to quiet Sora body text —
   it names the state in words; the orb carries the light. */
QLabel#homeStateLine { font-size: ${TYPE_BODY}px; font-weight: 400; color: ${TEXT_MUTED}; }
QLabel#captionLabel { color: ${TEXT_MUTED}; font-size: 13px; }
/* Sci-fi Home: the vitals readout is a mono data line — the
   Share Tech Mono face, muted, one tonal step below the caption. */
QLabel#briefingLine { color: ${TEXT_MUTED}; font-size: ${TYPE_BODY}px; font-family: ${MONO_FONT_STACK}; }
/* UI rethink Build 1: the collapsed briefing card's one line —
   body size, Sora, the text colour (it is the day's headline,
   not a hint). The 19 lead rung made the card shout; the summary
   reads at body size now. */
QLabel#briefingSummary { font-size: ${TYPE_BODY}px; font-weight: 600; color: ${TEXT}; }
/* Screen 3 Activity (2026-11-16, rethink Build 2): the density
   test — one headline LINE per section at 20 Sora semibold (the
   count joins its label; no giant figure), section headings at
   the 16 rung in semibold (weight, not size, separates a heading
   from a row title); row values are the 16 rung; row labels
   and numbers are metadata. The row's hairline is the widget's
   own bottom border, so no separator widget is ever added. The
   state label colours by a dynamic property (rowstate), never by
   a colour literal in code; a fired reminder row dims through
   the fired property on the row widget. */
QLabel#activityHeadline { font-size: ${TYPE_TITLE}px; font-weight: 600; color: ${TEXT}; }
QLabel#activitySection { font-size: ${TYPE_ROW}px; font-weight: 600; color: ${TEXT}; }
QLabel#activityRowLabel { font-size: ${TYPE_CAPTION}px; font-weight: 600; color: ${TEXT_MUTED}; }
QLabel#activityRowLabel[rowstate="danger"] { color: ${DANGER}; }
QLabel#activityRowLabel[rowstate="amber"] { color: ${AMBER}; }
QLabel#activityRowLabel[rowstate="ok"] { color: ${OK}; }
QLabel#activityRowValue { font-size: ${TYPE_ROW}px; color: ${TEXT}; }
QLabel#activityRowNumber { font-size: ${TYPE_BODY}px; font-family: ${MONO_FONT_STACK}; color: ${TEXT_MUTED}; }
QWidget#activityRow { border-bottom: 1px solid ${LINE_SUBTLE}; }
/* The item rule below already draws the hairline under every
   list row, so a row widget inside a list must not draw its
   own — the two would sit 8px apart. Activity lists get the
   house hairline token on their items instead of the legacy
   white one, so list rows and layout rows share one hairline. */
QListWidget QWidget#activityRow { border-bottom: none; }
QListWidget#activityList::item { border-bottom: 1px solid ${LINE_SUBTLE}; }
QWidget#activityRow[fired="true"] QLabel#activityRowValue { color: ${TEXT_FAINT}; }
QWidget#activityRow[fired="true"] QLabel#activityRowNumber { color: ${TEXT_FAINT}; }
QLabel#activityErrorDot { font-size: ${TYPE_BODY}px; color: ${DANGER}; }
QLabel#activityError { font-size: ${TYPE_BODY}px; color: ${TEXT}; }
QPushButton#activityTextAction { background: transparent; border: none; color: ${TEXT_MUTED}; font-size: ${TYPE_BODY}px; padding: 2px 4px; }
QPushButton#activityTextAction:hover { color: ${TEXT}; }
QPushButton#activityTextAction:focus { border: ${FOCUS_BORDER}; }
/* B7F3 (Build 7 Finding 3): the "?" beside the Scheduled
   headline is a circled button, not quiet text — a round
   outline (18 px circle: 16 px content + the 1 px rim each
   side, 9 px radius) so it reads as a button. Its own
   objectName: activityTextAction is shared by the Details /
   pending / Refresh buttons, which stay borderless. */
QPushButton#activityHelpButton { background: transparent; border: 1px solid ${TEXT_MUTED}; border-radius: 9px; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px; padding: 0px; color: ${TEXT_MUTED}; font-size: ${TYPE_BODY}px; }
QPushButton#activityHelpButton:hover { color: ${TEXT}; border-color: ${TEXT}; }
QPushButton#activityHelpButton:focus { border: ${FOCUS_BORDER}; }
QLabel#cardHint { color: ${TEXT_FAINT}; font-size: ${TYPE_CAPTION}px; }
/* UI rethink Build 1: the state word is quiet text, not a chip —
   one word in the role colour, no background, no border, no
   padding. The header holds one state word + Connect + overflow;
   the chip made one fact look like three controls. */
QLabel#statusPill {
    padding: 0px;
    border: none;
    background: transparent;
    color: ${TEXT_MUTED};
    font-size: 12px;
}

/* ================= buttons ================= */
/* Secondary actions (Phase C): solid surfaces — resting surface,
   one step up on hover, an accent wash on press. No glass, no
   lighter top edge: elevation is a brighter hairline. */
QPushButton {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_BTN}px;
    padding: 8px 16px;
    color: ${TEXT};
}
QPushButton:hover {
    background-color: ${SURFACE_3};
}
QPushButton:pressed {
    background-color: ${PRIMARY_WASH};
}
QPushButton:focus { border: ${FOCUS_BORDER}; }
QPushButton:disabled {
    color: ${TEXT_DISABLED};
    background-color: rgba(255, 255, 255, 5);
    border: 1px solid rgba(255, 255, 255, 10);
}

/* Primary actions (Command Deck): a solid cyan outline with a cyan
   wash on hover — no gradients on buttons. */
QPushButton#primaryBtn {
    background: transparent;
    color: ${TEXT};
    border: 1px solid ${ACCENT};
    border-radius: ${RADIUS_PILL}px;
    padding: 8px 20px;
    font-weight: 600;
}
QPushButton#primaryBtn:hover { background: ${PRIMARY_WASH}; }
QPushButton#primaryBtn:pressed { background: ${PRIMARY_WASH_PRESSED}; }
QPushButton#primaryBtn:focus { border: ${FOCUS_BORDER}; }
QPushButton#primaryBtn:disabled {
    background-color: rgba(255, 255, 255, 5);
    color: ${TEXT_DISABLED};
    border: 1px solid rgba(255, 255, 255, 10);
}
QPushButton#dangerBtn {
    background-color: rgba(255, 84, 104, 32);
    color: ${DANGER_TEXT};
    border: 1px solid rgba(255, 84, 104, 64);
    border-top: 1px solid rgba(255, 84, 104, 110);
    border-radius: ${RADIUS_PILL}px;
    padding: 8px 20px;
    font-weight: 600;
}
QPushButton#dangerBtn:hover { background-color: rgba(255, 84, 104, 48); }
QPushButton#dangerBtn:pressed { background-color: rgba(255, 84, 104, 64); }
QPushButton#dangerBtn:focus { border: 1px solid ${DANGER}; }

/* The hero mic wears the same outline treatment as the primary
   action; while live it fills solid accent so the state reads at a
   glance. (Phase D rebuilds the mic as a ringed mic with level
   segments; this is the Phase B palette step, not that rebuild.) */
QPushButton#micBtn {
    background: transparent;
    color: ${TEXT};
    border: 1px solid ${ACCENT};
    border-radius: ${RADIUS_PILL_MIC}px;
    font-size: 21px;
}
QPushButton#micBtn:hover { background: ${PRIMARY_WASH}; }
QPushButton#micBtn[active="true"] {
    background: ${ACCENT};
    color: ${BG};
    border: 1px solid ${ACCENT};
}
QPushButton#micBtn:pressed { background: ${PRIMARY_WASH_PRESSED}; }
/* Phase 2 (chatbox #2): the composer's send button — same accent
   family as the mic; greyed flat while the composer is empty. */
QPushButton#sendBtn {
    background: transparent;
    border: 1px solid ${ACCENT};
    border-radius: 22px;
}
QPushButton#sendBtn:hover { background: ${PRIMARY_WASH}; }
QPushButton#sendBtn:disabled {
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 12);
}
QPushButton#micBtn:focus { border: ${FOCUS_BORDER}; }

/* Chips and pills: glass at rest, the accent family when chosen. */
QPushButton#chipBtn {
    border-radius: ${RADIUS_CHIP}px; padding: 8px 14px; font-size: 12px;
}
QPushButton#chipBtn:hover { background-color: ${SURFACE_3}; }
QPushButton#chipBtn:pressed { background-color: ${PRIMARY_WASH}; }
QPushButton#chipBtn:focus { border: ${FOCUS_BORDER}; }
QPushButton#chipBtn:checked {
    background-color: rgba(61, 139, 255, 44);
    border: 1px solid rgba(61, 139, 255, 90);
    border-top: 1px solid rgba(61, 139, 255, 130);
    color: ${ACCENT_2};
}
QPushButton#chipSuggest {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_CHIP}px;
    color: ${TEXT_MUTED}; font-size: 12.5px; padding: 8px 14px;
}
QPushButton#chipSuggest:hover {
    background-color: rgba(53, 214, 255, 26); color: ${TEXT};
    border: 1px solid rgba(53, 214, 255, 90);
}
QPushButton#chipSuggest:pressed { background-color: rgba(53, 214, 255, 44); }
QPushButton#chipSuggest:focus { border: ${FOCUS_BORDER}; }
QPushButton#cmdPill {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_PILL_SM}px;
    color: ${TEXT_MUTED}; font-size: 12px; padding: 4px 12px;
    text-align: left;
}
QPushButton#cmdPill:hover {
    background-color: ${SURFACE_3}; color: ${TEXT};
}
QPushButton#cmdPill:pressed { background-color: ${PRIMARY_WASH}; }
QPushButton#cmdPill:focus { border: ${FOCUS_BORDER}; }
QPushButton#iconBtn {
    border-radius: ${RADIUS_PILL_ICON}px; padding: 0px; font-size: 15px;
}
QPushButton#iconBtn:pressed { background-color: rgba(255, 255, 255, 28); }
/* Command Deck Phase E (§9.2 Dock): square transport buttons
(Mute, Screen) — radius 0, hairline rim, muted text; hover and
focus mirror the chip buttons. Token substitutions only. */
QPushButton#squareBtn {
    border: 1px solid ${LINE_SUBTLE}; border-radius: 0px;
    color: ${TEXT_MUTED}; font-size: 12px;
}
QPushButton#squareBtn:hover { background-color: ${SURFACE_3}; }
QPushButton#squareBtn:pressed { background-color: ${PRIMARY_WASH}; }
QPushButton#squareBtn:focus { border: ${FOCUS_BORDER}; }
QPushButton#squareBtn:checked {
    background-color: rgba(61, 139, 255, 44);
    border: 1px solid rgba(61, 139, 255, 90);
    color: ${ACCENT_2};
}
QPushButton#themeSwatch {
    border-radius: 2px; padding: 0px; border: 2px solid transparent;
}
QPushButton#themeSwatch:checked { border: 2px solid ${TEXT}; }
QPushButton#themeSwatch:focus { border: 2px solid ${ACCENT}; }

/* ================= inputs ================= */
/* Fields sit in a well: darker than the surface, hairline rim, an
   accent ring (same 1px — nothing shifts) and a faint accent wash
   while focused. */
QLineEdit, QComboBox, QSpinBox, QDateTimeEdit, QTimeEdit, QDateEdit {
    background-color: ${INPUT_BG};
    border: 1px solid ${BORDER};
    border-radius: ${RADIUS_INPUT}px;
    padding: 8px 12px;
    color: ${TEXT};
    selection-background-color: ${SELECTION_BG};
    selection-color: #FFFFFF;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover,
QDateTimeEdit:hover, QTimeEdit:hover, QDateEdit:hover {
    border: 1px solid ${BORDER_STRONG};
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDateTimeEdit:focus, QTimeEdit:focus, QDateEdit:focus {
    border: ${FOCUS_BORDER};
    background-color: ${INPUT_BG_FOCUS};
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDateTimeEdit:disabled, QTimeEdit:disabled, QDateEdit:disabled {
    color: ${TEXT_DISABLED};
    background-color: ${INPUT_BG_DISABLED};
    border: 1px solid ${INPUT_BORDER_DISABLED};
}
#dockInput {
    background-color: rgba(255, 255, 255, 7);
    border: 1px solid ${BORDER_STRONG};
    border-radius: ${RADIUS_DOCK}px; padding: 12px 16px; font-size: 13px;
}
#dockInput:focus {
    border: ${FOCUS_BORDER};
    background-color: ${INPUT_BG_FOCUS};
}
QLineEdit#paletteInput {
    background-color: transparent; border: none;
    font-size: 16px; padding: 16px 16px; font-family: ${DISPLAY_FONT_STACK};
}
QComboBox::drop-down { border: none; width: 20px; }
QSpinBox::up-button, QSpinBox::down-button {
    background: transparent; border: none;
}
QComboBox QAbstractItemView {
    background-color: ${SURFACE_2};
    border: 1px solid ${BORDER_STRONG};
    selection-background-color: ${SELECTION_BG};
    selection-color: #FFFFFF;
    outline: none;
}
QComboBox QAbstractItemView::item { padding: 6px 10px; }

/* ================= cards ================= */
/* One elevation language for every resting surface (Phase C):
   solid surface, one role line. HudPanel paints its own top wash
   for migrated cards; these rules carry the unmigrated ones. */
QFrame#card, QFrame#homeCard, QFrame#settingsCard {
    background: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_CARD}px;
}
QFrame#resultCard {
    background: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-left: 3px solid ${ACCENT};
    border-radius: ${RADIUS_CARD}px;
}
QLabel#resultTitle { color: ${TEXT}; font-size: 11px; font-weight: 600; }
QLabel#resultBody { font-size: ${TYPE_BODY}px; }

/* ================= chat ================= */
QScrollArea { border: none; background: transparent; }
QWidget#chatContent { background: transparent; }
/* UI rethink Build 1 (D-R28): a message is a quiet tonal fill —
   one surface step above the background, no border. The border
   was the problem, not the fill: a stack of bordered boxes reads
   as a stack of boxes, a transcript reads as a transcript. The
   resting border stays 1 px transparent so the focus rule below
   (border colour only) never moves the layout. */
QFrame#bubbleUser {
    background: ${SURFACE_2};
    border: 1px solid transparent;
    border-radius: ${RADIUS_CARD}px;
    border-bottom-right-radius: 4px;
}
QFrame#bubbleNova {
    background: ${SURFACE_2};
    border: 1px solid transparent;
    border-radius: ${RADIUS_CARD}px;
    border-bottom-left-radius: 4px;
}
/* UI a11y foundations: a keyboard-focused bubble shows focus
   the same way buttons do — border colour only, so the 1 px
   width (and therefore the layout) never moves. */
QFrame#bubbleUser:focus, QFrame#bubbleNova:focus {
    border-color: ${ACCENT};
}
/* UI rethink Build 1: the sender label is 10.5 mono caps, muted —
   on the first message of a run only (the grouping is in code). */
QLabel#bubbleWho { color: ${TEXT_FAINT}; font-size: ${TYPE_MICRO}px; font-family: ${MONO_FONT_STACK}; }
/* v5.51 item 12: the hover-actions row is a solid chip in the
   popup step colour, so message text never shows through it. */
QWidget#bubbleActions {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_BTN}px;
}
QLabel#bubbleText { font-size: ${TYPE_BODY}px; }
/* Chat UI (2026-11-16): the quote anchor above a reply is a flat
   button whose text reads left, like the message under it. UI
   rethink Build 1: it sits indented under its message with a
   hairline on the left, so it reads as attached to the message,
   not floating. */
QPushButton#bubbleQuote { text-align: left; padding-left: 8px; border-left: 1px solid ${LINE_SUBTLE}; }
QLabel#chatMsgTime { color: ${TEXT_FAINT}; font-size: ${TYPE_MICRO}px; font-family: ${MONO_FONT_STACK}; }
QLabel#systemLine { color: ${TEXT_FAINT}; font-size: 12px; }
QFrame#codeBlock {
    background: ${CODE_BG};
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 2px;
}
QLabel#codeHead { color: ${TEXT_FAINT}; font-size: 10.5px; font-weight: 600; }
QPlainTextEdit#codeText {
    background: transparent; border: none;
    font-family: ${MONO_FONT_STACK}; font-size: 12.5px;
    color: #D7F0DC;
}

/* ================= lists ================= */
QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 10);
    color: ${TEXT};
}
QListWidget::item:hover { background-color: rgba(255, 255, 255, 7); }
QListWidget::item:selected {
    background-color: rgba(53, 214, 255, 52); color: ${TEXT};
}
QListWidget#paletteList {
    background-color: transparent; border: none; padding: 8px;
}
QListWidget#paletteList::item {
    padding: 8px 12px; border-bottom: none;
    border-radius: ${RADIUS_SM}px; color: ${TEXT_MUTED}; font-size: 13.5px;
}
QListWidget#paletteList::item:hover {
    background-color: rgba(255, 255, 255, 8);
}
QListWidget#paletteList::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(53, 214, 255,60), stop:1 rgba(61, 139, 255,48));
    color: ${TEXT};
}

/* ================= scrollbars ================= */
/* Thumb only — no track, no buttons; the 2px end margins let the
   thumb float inside its gutter instead of touching the edges. */
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 2px 0px;
}
QScrollBar::handle:vertical {
    background: ${SCROLL_REST}; border-radius: 4px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: ${SCROLL_HOVER}; }
QScrollBar::handle:vertical:pressed { background: ${SCROLL_PRESSED}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal {
    background: transparent; height: 8px; margin: 0px 2px;
}
QScrollBar::handle:horizontal {
    background: ${SCROLL_REST}; border-radius: 4px; min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: ${SCROLL_HOVER}; }
QScrollBar::handle:horizontal:pressed { background: ${SCROLL_PRESSED}; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* ================= sliders, switches, radios ================= */
QSlider::groove:horizontal {
    height: 4px; border-radius: 2px; background: rgba(255, 255, 255, 26);
}
/* Look-fix 2 (2026-11-24): the knob is solid white, not the theme
   text — the text colour is near-black on Light, so a text-coloured
   knob read as a black square (the Knowledge Graph timeline thumb).
   White reads as a knob on the dark groove and on the light groove
   alike (the dark border defines it on light); on dark it is one
   step from the old near-white text colour, imperceptible. */
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -6px 0;
    border-radius: ${RADIUS_SM}px;
    background: #FFFFFF; border: 1px solid rgba(0, 0, 0, 90);
}
QSlider::handle:horizontal:hover {
    background: #FFFFFF; border: 1px solid rgba(0, 0, 0, 130);
}
QSlider::handle:horizontal:pressed { background: ${ACCENT}; }
QSlider::sub-page:horizontal { background: ${ACCENT}; border-radius: 2px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 38px; height: 21px; border-radius: ${RADIUS_BTN}px;
    background: rgba(255, 255, 255, 22);
    border: 1px solid ${BORDER_STRONG};
}
QCheckBox::indicator:checked {
    background: ${ACCENT};
    border: 1px solid rgba(255, 255, 255, 40);
}
QCheckBox::indicator:disabled {
    background: rgba(255, 255, 255, 10);
    border: 1px solid rgba(255, 255, 255, 12);
}
/* Grammar matters: the state goes AFTER the subcontrol. The reversed
   form (QCheckBox:focus::indicator) leaks a border onto EVERY
   checkbox's whole frame — proven in the Wave 1B probe renders. */
QCheckBox::indicator:focus { border: ${FOCUS_BORDER}; }
QRadioButton { spacing: 8px; }
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    background: ${INPUT_BG}; border: 1px solid ${BORDER_STRONG};
}
QRadioButton::indicator:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
        fx:0.5, fy:0.5, stop:0 ${ACCENT}, stop:0.45 ${ACCENT},
        stop:0.58 rgba(53, 214, 255, 0), stop:1 rgba(53, 214, 255, 0));
    border: 1px solid ${ACCENT};
}
QRadioButton::indicator:disabled {
    background: rgba(255, 255, 255, 8);
    border: 1px solid rgba(255, 255, 255, 12);
}

/* ================= menus, tooltips, headers ================= */
/* Phase C foundation (guide §8): menus and tooltips on solid
   surfaces with a cyan hairline, 2 px corners, 11 px mono text,
   no shadow; headers in mono with a role line. */
QMenu {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_STRONG};
    border-radius: 2px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 16px;
    font-family: ${MONO_FONT_STACK};
    font-size: 11px;
    color: ${TEXT};
}
QMenu::item:selected {
    background-color: ${SURFACE_3};
    color: ${TEXT};
}
QMenu::separator {
    height: 1px;
    background: ${LINE_SUBTLE};
    margin: 4px 0px;
}
QToolTip {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_STRONG};
    border-radius: 2px;
    color: ${TEXT};
    font-family: ${MONO_FONT_STACK};
    font-size: 11px;
    padding: 6px 8px;
}
QHeaderView::section {
    background-color: ${SURFACE_2};
    border: 1px solid ${LINE_SUBTLE};
    font-family: ${MONO_FONT_STACK};
    font-size: 11px;
    color: ${TEXT_MUTED};
    padding: 6px 8px;
}
/* Migrated cards paint their own chrome: the sheet must not
   double-draw under HudPanel. */
QFrame#hudPanel { background: transparent; border: none; }

/* ================= dialogs ================= */
/* Dialogs float one solid step above the window; the palette floats
   one more, with the raised-surface edge treatment. */
QDialog { background-color: ${SURFACE_1}; }
QDialog#palette {
    background-color: ${SURFACE_2};
    border: 1px solid ${BORDER_STRONG};
    border-top: 1px solid ${BORDER_TOP};
    border-radius: ${RADIUS_DIALOG}px;
}
QLabel#paletteHint { color: ${TEXT_FAINT}; font-size: ${TYPE_CAPTION}px; }
QLabel:disabled { color: ${TEXT_DISABLED}; }
""")


# GUI split #6 INTENTIONAL: _TOKENS was built from this module's
# globals() when the tokens lived in style.py; the tokens now live
# in style_tokens, so it is built from that module's namespace with
# the identical predicate — the resulting dict is identical, since
# style_tokens holds exactly the UPPER scalar constants this file
# held above (proven by runtime comparison in prove_style_split.py).
_TOKENS = {
    name: value for name, value in vars(style_tokens).items()
    if name.isupper() and isinstance(value, (str, int, float))
}
PREMIUM_STYLE = _QSS.substitute(_TOKENS)


# v5.57 (redo): the standalone dialogs' chrome. The main window
# applies PREMIUM_STYLE to itself only, so top-level dialogs
# (confirm, activity, trust) used to render in the default light
# palette; this sheet brings them into the same dark language
# without touching PREMIUM_STYLE itself (its golden hash is
# pinned, and this sheet is a separate constant). Buttons keep
# equal weight here on purpose: the confirm dialog must not nudge
# toward Allow — both actions stay plain glass.
DIALOG_STYLE = Template("""
QDialog { background-color: ${BG}; }
QDialog QLabel { color: ${TEXT}; }
QDialog QLabel#muted { color: ${TEXT_MUTED}; }
QDialog QListWidget, QDialog QPlainTextEdit {
    background-color: ${BG}; color: ${TEXT};
    border: 1px solid ${LINE_SUBTLE}; border-radius: 2px;
    selection-background-color: ${SELECTION_BG};
}
QDialog QPlainTextEdit { font-family: ${MONO_FONT_STACK}; }
QDialog QPushButton {
    background-color: ${SURFACE_2}; border: 1px solid ${LINE_SUBTLE};
    border-radius: ${RADIUS_BTN}px; padding: 8px 16px; color: ${TEXT};
}
QDialog QPushButton:hover { background-color: ${SURFACE_3}; }
QDialog QPushButton:pressed { background-color: ${PRIMARY_WASH}; }
QDialog QPushButton:disabled {
    color: ${TEXT_DISABLED}; background-color: rgba(255, 255, 255, 5);
    border: 1px solid rgba(255, 255, 255, 10);
}
QDialog QPushButton#chipBtn {
    border-radius: ${RADIUS_CHIP}px; padding: 8px 14px; font-size: 12px;
}
QDialog QCheckBox { color: ${TEXT}; }
""").substitute(_TOKENS)


