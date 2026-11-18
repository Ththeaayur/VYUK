"""PF-8 settings/help surfaces (GUI split #3)."""

from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget

from .pf_surfaces_helpers import _card, _muted, _status_label




# ================================================================ PF-8
# Self-explaining settings + shortcuts. One plain line per
# setting, per-setting reset as a named wiring seam, and a
# shortcuts surface that renders the ONE list the app
# already binds (SHORTCUT_ROWS), never a copied list.

@dataclass
class SettingExplanation:
    key: str
    title: str
    plain_line: str
    where: str
    reset_seam: str = ""


SETTING_EXPLANATIONS = (
    SettingExplanation(
        "how_vyuk_thinks", "How VYUK thinks",
        "Where VYUK does her thinking — in the cloud or on "
        "this PC. The choice applies the next time you "
        "connect.",
        "Settings → How VYUK thinks",
        "wiring: reset brain choice to the default engine"),
    SettingExplanation(
        "voice", "Voice",
        "How VYUK sounds when she speaks, and which voice "
        "she uses for each language.",
        "Settings → Voice",
        "wiring: reset voice to the default voice"),
    SettingExplanation(
        "microphone", "Microphone",
        "Which microphone VYUK listens through, and how "
        "sensitive she is to your voice.",
        "Settings → Microphone",
        "wiring: reset microphone to the default device"),
    SettingExplanation(
        "appearance", "Appearance",
        "How VYUK looks — theme, orb shape, text size, "
        "density, and motion. Changes apply instantly.",
        "Settings → Appearance",
        "wiring: reset appearance to the default theme"),
    SettingExplanation(
        "presence", "Presence",
        "How VYUK lives on this PC — starting by herself, "
        "the floating orb, and speaking first.",
        "Settings → Presence",
        "wiring: reset presence to the shipped defaults"),
    SettingExplanation(
        "privacy_security", "Privacy & security",
        "Who VYUK takes orders from, and what needs your "
        "OK first.",
        "Settings → Privacy & security",
        "wiring: reset confirm-gate allowlist"),
    SettingExplanation(
        "continuous_display", "A choice about watching what you do",
        "Whether VYUK may keep an eye on what is on your "
        "screen while you work, so it can suggest things "
        "without you asking. Off to start with, everything "
        "stays on this computer only, and you can switch "
        "it off here at any time.",
        "Settings → Privacy & permissions → A choice about "
        "watching what you do",
        "wiring: reset continuous-display choice to OFF"),
    SettingExplanation(
        "keyboard_shortcuts", "Keyboard shortcuts",
        "The keys VYUK listens for. Press ? anywhere (not "
        "while typing) to see them.",
        "Settings → Advanced → Keyboard shortcuts",
        ""),
    SettingExplanation(
        "system_status", "System status",
        "What is down, what still works, and what to do — "
        "and how power and network are throttling VYUK "
        "right now.",
        "Settings → Advanced → System status",
        ""),
    SettingExplanation(
        "doctor", "Doctor",
        "Her health, at a glance — what passed, what "
        "needs a look, and what to do next.",
        "Settings → Advanced → Doctor",
        ""),
    SettingExplanation(
        "report_problem", "Report a problem",
        "Bundle diagnostics, preview exactly what is "
        "inside, and export it only with your consent. "
        "Nothing is sent anywhere automatically.",
        "Settings → Advanced → Report a problem",
        ""),
)

SETTING_EXPLANATIONS_BY_KEY = {item.key: item
                               for item in SETTING_EXPLANATIONS}


def explain_setting(key):
    """The plain-line explanation for one setting, or None
    when the key is unknown — callers render the empty
    state, never an invented line."""
    return SETTING_EXPLANATIONS_BY_KEY.get(key)


def load_shortcut_rows():
    """The shortcut list the app actually binds, from
    gui/window_widgets.py's SHORTCUT_ROWS — the same list
    the Settings card and the '?' overlay render from, so
    the three surfaces cannot drift. Lazily imported: this
    module must stay importable without the main window.
    Returns (rows, error): rows is empty and error names
    the problem when the list cannot be loaded, so the
    widget renders an honest empty state with a next step
    instead of a stale copy."""
    try:
        from voice_assistant.gui.window_widgets import (
            SHORTCUT_ROWS,
        )
        rows = [(str(keys), str(what))
                for keys, what in SHORTCUT_ROWS]
        if not rows:
            return [], ("The shortcut list loaded but was "
                        "empty.")
        return rows, ""
    except Exception as exc:  # noqa: BLE001 — the widget says so
        return [], (f"The shortcut list could not be loaded "
                    f"({type(exc).__name__}).")


class SettingsExplainedWidget(QWidget):
    """Searchable plain-line explanations for the existing
    Settings cards. Search filters by title or plain line;
    no match renders what happened and a next step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "What each setting does",
            "One plain line per setting. Search to find one "
            "by what it does, not by its name.")
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search settings by what they do…")
        self.search.textChanged.connect(self._on_search)
        card_layout.addWidget(self.search)
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        card_layout.addWidget(self.rows_widget)
        self.empty_label = _muted("")
        self.empty_label.setVisible(False)
        card_layout.addWidget(self.empty_label)
        self._render("")

    def _clear_rows(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render(self, query):
        self._clear_rows()
        query = (query or "").strip().lower()
        shown = [item for item in SETTING_EXPLANATIONS
                 if not query or query in item.title.lower()
                 or query in item.plain_line.lower()]
        for item in shown:
            title = QLabel(f"{item.title} — {item.where}")
            title.setStyleSheet("font-weight: 600;")
            title.setWordWrap(True)
            self.rows_layout.addWidget(title)
            self.rows_layout.addWidget(_muted(item.plain_line))
            if item.reset_seam:
                self.rows_layout.addWidget(_muted(
                    f"Reset: {item.reset_seam} (wiring step "
                    "adds the working reset button)."))
        self.empty_label.setVisible(not shown)
        if not shown:
            self.empty_label.setText(
                f"No setting matches “{query}”. Next step: "
                "clear the search to see every setting, or "
                "open Settings and look under Advanced.")

    def _on_search(self, text):
        self._render(text)


class ShortcutsWidget(QWidget):
    """Renders the bound shortcut list. ``rows`` may be
    injected (tests, renders); otherwise it loads the live
    list. A failed load renders the failure and a next
    step — never an empty panel, never a copied list."""

    def __init__(self, rows=None, load_error="", parent=None):
        super().__init__(parent)
        if rows is None:
            rows, load_error = load_shortcut_rows()
        self.rows = rows
        self.load_error = load_error
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "Keyboard shortcuts",
            "The keys VYUK listens for — the same list as "
            "Settings → Keyboard shortcuts and the ? sheet.")
        if not rows:
            problem = load_error or "The shortcut list is empty."
            card_layout.addWidget(_status_label(
                f"{problem} Next step: press ? (outside a "
                "text box) to open the shortcuts sheet, or "
                "open Settings → Advanced → Keyboard "
                "shortcuts."))
        else:
            for keys, what in rows:
                line = QLabel(f"<b>{keys}</b> — {what}")
                line.setObjectName("cardHint")
                line.setWordWrap(True)
                card_layout.addWidget(line)


# ================================================================ PF-9
# In-app help: a searchable guide with example commands per
# capability, plus "why did she refuse that" explanations
# tied to her honest states. All copy is original to this
# module. No external links by construction — every index
# entry is an in-app topic, so there is no dead link to
# fail the item (validated by validate_help_index).

@dataclass
class HelpTopic:
    topic_id: str
    title: str
    body: str
    keywords: tuple = ()


HELP_INDEX = (
    HelpTopic(
        "talk_to_vyuk", "Talk to VYUK",
        "Say what you want in plain words, or type it. "
        "For example: “Remind me to drink water in an "
        "hour”, “What is on my calendar tomorrow?”, or "
        "“Play something calm while I study”. VYUK "
        "answers out loud when she is connected, and in "
        "writing when she is not. If she did not hear you, "
        "check the microphone level first — that is the "
        "most common cause, and the Doctor checks it too.",
        ("voice", "speak", "talk", "command", "example")),
    HelpTopic(
        "mic_not_hearing", "VYUK cannot hear me",
        "First, speak and watch the microphone meter: if "
        "it stays flat, VYUK is not receiving sound. Next "
        "step: open Settings → Microphone and pick a "
        "different microphone, then try again. If the "
        "meter moves but she still does not respond, open "
        "the Doctor from Advanced — it checks the "
        "whole voice path and names the failing step.",
        ("microphone", "mic", "hear", "level", "sound")),
    HelpTopic(
        "no_sound", "VYUK speaks but I hear nothing",
        "Check that your speakers or headphones are the "
        "playback device Windows is using, and that the "
        "volume is up. Next step: open Settings → Voice "
        "and try a different voice, which re-tests "
        "playback. If nothing plays at all, the Doctor "
        "from Advanced checks the speaker path.",
        ("speaker", "sound", "volume", "voice", "hear")),
    HelpTopic(
        "why_refused_confirmation", "Why did she ask first?",
        "Some actions need your OK before VYUK does them "
        "— anything destructive, anything that sends "
        "something out, and anything irreversible. She "
        "asks, you confirm, and only then does she act. "
        "That is the confirm gate doing its job, not a "
        "refusal: say yes and she proceeds, say no and "
        "she stops, and nothing changes either way until "
        "you answer.",
        ("refuse", "confirm", "ask", "permission", "why")),
    HelpTopic(
        "why_refused_hands_paused",
        "Why did she refuse to use her hands?",
        "If VYUK's hands were paused by her failsafe — "
        "because something looked unsafe — she will not "
        "act until a person re-arms them. Next step: open "
        "the tray menu and use “Re-arm VYUK's hands "
        "(failsafe)” once you have checked what tripped "
        "it. Until then, talking, looking, and answering "
        "still work; only acting stays paused.",
        ("refuse", "hands", "failsafe", "paused", "why")),
    HelpTopic(
        "why_refused_capability_down",
        "Why did she say a capability is down?",
        "When something she needs is unavailable — her "
        "cloud brain, a local model, or a watcher — VYUK "
        "says which part is down and what still works, "
        "instead of pretending. Next step: open Settings "
        "→ Advanced → System status for the current "
        "picture, and the Doctor for the fix for each "
        "failing part.",
        ("refuse", "down", "broken", "status", "why")),
    HelpTopic(
        "why_refused_permission",
        "Why did she say she cannot access something?",
        "VYUK only touches what she has permission for: "
        "microphone, screen, files, browser, and home "
        "devices each have their own permission. If one "
        "is missing or unknown, she says so instead of "
        "guessing. Next step: open Settings → More "
        "settings → Permissions to see each permission "
        "and what it is used for.",
        ("refuse", "permission", "access", "why", "denied")),
    HelpTopic(
        "why_feature_off", "Why is a feature switched off?",
        "Some features stay off until their licence is "
        "confirmed for commercial use, or until you "
        "switch them on yourself. VYUK does not ship a "
        "feature on an unverified licence, and she does "
        "not switch things on behind your back. Next "
        "step: the feature's own Settings card says "
        "whether it is waiting on a licence, on you, or "
        "on both.",
        ("refuse", "licence", "off", "disabled", "why")),
    HelpTopic(
        "what_she_can_do", "What can VYUK do?",
        "VYUK talks, listens, remembers what you tell "
        "her, watches for things you ask her to notice, "
        "looks at the screen when you ask, and acts "
        "through her tools — reminders, calendar, music, "
        "files, and more. The Skills catalogue in the "
        "sidebar lists every capability with an example. "
        "If a capability is missing from the list, VYUK "
        "does not have it yet — she will say so rather "
        "than pretend.",
        ("tools", "skills", "capabilities", "do", "can")),
    HelpTopic(
        "find_a_setting", "Find a setting",
        "Open Settings and type in the search box at the "
        "top: matching cards stay visible and the rest "
        "hide, including cards behind Advanced. "
        "Next step: if search finds nothing, clear it and "
        "browse Advanced — every card lives there "
        "or on the main Settings page.",
        ("settings", "find", "search", "where")),
    HelpTopic(
        "what_she_remembers", "What does VYUK remember?",
        "VYUK remembers facts you tell her, people you "
        "mention, and promises you make — stored on this "
        "PC. Opinions she forms are labelled as opinions, "
        "never as facts, and inferred traits expire after "
        "90 days. Next step: open Settings → More "
        "settings → Your stored facts to see everything "
        "she stores, delete one fact, or wipe them all.",
        ("memory", "remember", "facts", "stored", "privacy")),
    HelpTopic(
        "report_a_problem", "Report a problem",
        "Open Settings → Advanced → Report a "
        "problem. VYUK bundles diagnostics and shows you "
        "exactly what is inside before anything leaves "
        "this PC — nothing sends without your consent, "
        "and there is no support backend: the export is "
        "a file you can attach to a message yourself.",
        ("problem", "report", "bug", "diagnostics", "help")),
    HelpTopic(
        "check_her_health", "Check her health",
        "Open Settings → Advanced → Doctor. The "
        "Doctor runs her checks and shows what passed, "
        "what needs a look, and what to do next for each "
        "finding. If a check itself fails to run, the "
        "Doctor says so plainly instead of showing a "
        "blank page.",
        ("doctor", "health", "checks", "status")),
)

HELP_INDEX_BY_ID = {topic.topic_id: topic for topic in HELP_INDEX}


def validate_help_index():
    """Every index entry must have a unique id, a title,
    and a non-empty body, and no body may carry an
    external link (all topics are in-app, so no link can
    go dead). Returns a list of problems; empty is valid."""
    problems = []
    seen = set()
    for topic in HELP_INDEX:
        if topic.topic_id in seen:
            problems.append(f"duplicate help id: {topic.topic_id}")
        seen.add(topic.topic_id)
        if not topic.title.strip():
            problems.append(f"help topic without title: {topic.topic_id}")
        if not topic.body.strip():
            problems.append(f"help topic without body: {topic.topic_id}")
        if "http://" in topic.body or "https://" in topic.body:
            problems.append(
                f"help topic carries an external link: {topic.topic_id}")
    return problems


def help_search(query):
    """Substring search over titles, bodies, and keywords.
    An empty query returns the whole index; a query with
    no match returns an empty list, and the widget renders
    the empty state with a next step."""
    query = (query or "").strip().lower()
    if not query:
        return list(HELP_INDEX)
    found = []
    for topic in HELP_INDEX:
        hay = " ".join([topic.title, topic.body,
                        " ".join(topic.keywords)]).lower()
        if query in hay:
            found.append(topic)
    return found


class HelpWidget(QWidget):
    """Searchable in-app help viewer. ``topics`` may be
    injected; otherwise the module's own index renders."""

    def __init__(self, topics=None, parent=None):
        super().__init__(parent)
        self._topics = list(topics) if topics is not None else list(HELP_INDEX)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "Help",
            "Answers in plain language, inside the app — "
            "no leaving, no dead links. Search for a task, "
            "or for why VYUK refused something.")
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Search help — try “why did she refuse”…")
        self.search.textChanged.connect(self._on_search)
        card_layout.addWidget(self.search)
        # UI settings slice: this inner scroll area nested inside
        # the settings page's own outer scroll area (the
        # Doctor-pattern violation this slice removes). Help
        # results now flow into the page scroll, like every other
        # settings surface; the page scroll is the one scroller.
        inner = QWidget()
        self.results_layout = QVBoxLayout(inner)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(10)
        inner.setAutoFillBackground(False)
        card_layout.addWidget(inner)
        self.empty_label = _muted("")
        self.empty_label.setVisible(False)
        card_layout.addWidget(self.empty_label)
        self._render("")

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render(self, query):
        self._clear_results()
        query = (query or "").strip().lower()
        if not query:
            shown = self._topics
        else:
            shown = [t for t in self._topics
                     if query in " ".join(
                         [t.title, t.body,
                          " ".join(t.keywords)]).lower()]
        for topic in shown:
            title = QLabel(topic.title)
            title.setStyleSheet("font-weight: 600;")
            title.setWordWrap(True)
            self.results_layout.addWidget(title)
            body = QLabel(topic.body)
            body.setObjectName("muted")
            body.setWordWrap(True)
            self.results_layout.addWidget(body)
        self.results_layout.addStretch()
        self.empty_label.setVisible(not shown)
        if not shown:
            self.empty_label.setText(
                f"No help topic matches “{query}”. Next "
                "step: try a shorter search — “microphone”, "
                "“refuse”, or “settings” — or open the "
                "Doctor from Advanced for a health "
                "check.")

    def _on_search(self, text):
        self._render(text)
