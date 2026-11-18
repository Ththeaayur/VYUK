"""PF-21 citizenship surface (GUI split #3)."""

from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pf_surfaces_helpers import _card, _muted




# ================================================================ PF-21
# Desktop citizenship: tray semantics, autostart consent,
# window + monitor memory, clean-uninstall info. The model
# records consent and reports checklist states; it never
# writes autostart state, tray state, or geometry itself.

CONSENT_NOT_ASKED = "not_asked"
CONSENT_GRANTED = "granted"
CONSENT_DECLINED = "declined"


@dataclass
class AutostartConsent:
    """Autostart consent, as a record — not a system
    write. ``applies_autostart`` is always False here:
    VYUK's Windows setup owns the platform write, and
    only it may flip the real autostart state."""
    state: str = CONSENT_NOT_ASKED
    applies_autostart: bool = False

    def set_consent(self, state):
        if state not in (CONSENT_GRANTED, CONSENT_DECLINED,
                         CONSENT_NOT_ASKED):
            raise ValueError(f"unknown consent state: {state!r}")
        self.state = state
        return {
            "consent": state,
            "applies_autostart": False,
            "note": (
                "Consent recorded only. The Windows startup "
                "setting itself is unchanged — it lives in "
                "Presence."),
        }

    def line(self):
        if self.state == CONSENT_GRANTED:
            return ("You have said VYUK may start with "
                    "Windows. The Windows startup setting "
                    "itself lives in Presence — this only "
                    "records your say-so.")
        if self.state == CONSENT_DECLINED:
            return ("You have said VYUK must not start "
                    "with Windows. The Windows startup "
                    "setting itself lives in Presence — "
                    "this only records your say-so.")
        return ("Not asked yet — VYUK has not asked "
                "whether she may start with Windows, and "
                "she does not start with Windows on her "
                "own say-so.")


@dataclass
class CitizenshipCheck:
    check_id: str
    title: str
    detail: str
    status: str  # "on", "off", "unknown"
    source: str

    # B7F4 (Build 7 Finding 4): the checklist line reads in
    # plain words — the ruled status word, the detail, and no
    # source parenthetical. The source stays on the record
    # as the audit trail; it is never rendered.
    def line(self):
        word = {"on": "On", "off": "Off",
                "unknown": "Not confirmed yet"}.get(self.status, "?")
        return f"{word} — {self.detail}"


def citizenship_model(settings_values=None):
    """The desktop-integration checklist. ``settings_values``
    is an injected mapping (the app passes real
    saved-settings reads); with nothing injected, every row that
    needs a live read is unknown. Two rows are grounded in
    the shipped code regardless of injection: the tray
    menu carries a real Quit action, and the window
    restores its saved geometry on launch — both verified
    in the window shell. Close-to-tray and monitor identity
    are NOT claimed: neither was verified as a behaviour
    here, so both stay unknown until they're confirmed
    on this PC."""
    values = settings_values or {}
    checks = [
        CitizenshipCheck(
            "tray_quit", "Quit lives in the tray menu",
            "Quitting from the tray menu closes VYUK for "
            "real — it is not a minimise.",
            "on", "window shell tray menu (Quit action)"),
        CitizenshipCheck(
            "tray_restore", "Clicking the tray icon restores her",
            "The tray icon's Open action brings the window "
            "back.",
            "on", "window shell tray menu (Open action)"),
        CitizenshipCheck(
            "close_to_tray", "Close button minimises to tray",
            "Whether the window's close button tucks VYUK "
            "into the tray instead of quitting.",
            values.get("close_to_tray", "unknown"),
            values.get("close_to_tray_source",
                       "not yet confirmed — VYUK's Windows "
                       "setup confirms how the close button "
                       "behaves")),
        CitizenshipCheck(
            "window_geometry", "Window size and position remembered",
            "VYUK reopens at the size and position she was "
            "left at, from her saved window geometry.",
            "on", "window shell restores saved geometry "
                   "on launch"),
        CitizenshipCheck(
            "monitor", "Monitor remembered",
            "Whether VYUK reopens on the same monitor, "
            "recorded separately from position.",
            values.get("monitor", "unknown"),
            values.get("monitor_source",
                       "not yet confirmed — saved geometry "
                       "restores position, but monitor "
                       "identity is not separately recorded")),
        CitizenshipCheck(
            "mini_orb", "Floating mini orb",
            "The small always-on-top orb, when switched "
            "on in Presence.",
            values.get("mini_orb", "unknown"),
            values.get("mini_orb_source",
                       "Presence setting — the app passes "
                       "the live value")),
    ]
    for check in checks:
        if check.status not in ("on", "off", "unknown"):
            check.status = "unknown"
    return checks


def clean_uninstall_info():
    """What VYUK stores on this PC, so uninstalling can be
    verified as clean. Locations are the real ones: settings
    live in the Windows registry under VYUK's entry, and app
    data under the install's data/ directory (memories,
    milestones, activity log). The installer-provided one-click
    uninstaller is still to come — this text does not claim one
    exists; it lists what to remove and check by hand
    until the installer confirms what it removes on its
    own."""
    return {
        "settings_location": "the Windows registry, under "
                             "VYUK's entry there",
        "data_locations": (
            "the install's data/ directory: memories, "
            "milestones log, activity log, downloaded "
            "voice data"),
        "manual_steps": (
            "1) Quit VYUK from the tray menu. 2) Remove the "
            "install folder. 3) Remove VYUK's entry from the "
            "Windows registry. 4) Remove the data/ directory if you "
            "want stored facts gone too — deleting it "
            "deletes everything she remembers."),
        "owed": (
            "A one-click clean uninstaller — we'll update "
            "this line when it's ready."),
    }


class CitizenshipWidget(QWidget):
    """Autostart consent toggle model + integration
    checklist + clean-uninstall info. The toggle records
    consent only; the checklist rows read injected values
    or honest unknown.

    S4 (settings audit): the autostart checkbox duplicated
    the Presence card's real autostart switch (same label,
    "Launch VYUK when Windows starts") while recording only
    a consent model that never drives the mechanism — a
    placebo switch. Pass on_autostart_jump and the row
    becomes a jump button to the one real switch instead;
    without it (tests, reuse) the checkbox stays.
    """

    def __init__(self, consent=None, settings_values=None,
                 parent=None, on_autostart_jump=None):
        super().__init__(parent)
        self.consent = consent if consent is not None \
            else AutostartConsent()
        self.checks = citizenship_model(settings_values)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        card, card_layout = _card(
            layout, "Desktop citizenship",
            "How VYUK lives on this PC — starting by "
            "herself only with your say-so, living in the "
            "tray properly, and leaving cleanly when you "
            "remove her.")
        if on_autostart_jump is not None:
            self.autostart_jump = QPushButton(
                "Change autostart setting…")
            self.autostart_jump.setObjectName("chipBtn")
            self.autostart_jump.clicked.connect(on_autostart_jump)
            card_layout.addWidget(self.autostart_jump)
            card_layout.addWidget(_muted(
                "Autostart lives in Presence — one switch, so "
                "the two can never disagree."))
        else:
            self.autostart_box = QCheckBox(
                "Launch VYUK when Windows starts")
            self.autostart_box.setTristate(True)
            if self.consent.state == CONSENT_GRANTED:
                self.autostart_box.setCheckState(
                    Qt.CheckState.Checked)
            elif self.consent.state == CONSENT_DECLINED:
                self.autostart_box.setCheckState(
                    Qt.CheckState.Unchecked)
            else:
                self.autostart_box.setCheckState(
                    Qt.CheckState.PartiallyChecked)
            self.autostart_box.stateChanged.connect(
                self._on_autostart_changed)
            card_layout.addWidget(self.autostart_box)
            self.consent_label = _muted(self.consent.line())
            card_layout.addWidget(self.consent_label)
        for check in self.checks:
            title = QLabel(check.title)
            title.setStyleSheet("font-weight: 600;")
            title.setWordWrap(True)
            card_layout.addWidget(title)
            card_layout.addWidget(_muted(check.line()))
        info = clean_uninstall_info()
        uninstall_title = QLabel("Clean uninstall")
        uninstall_title.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(uninstall_title)
        card_layout.addWidget(_muted(
            "Settings live in " + info["settings_location"]
            + ". Stored data lives in: " + info["data_locations"]
            + "."))
        card_layout.addWidget(_muted(info["manual_steps"]))
        card_layout.addWidget(_muted(
            "Still to come: " + info["owed"]))

    def _on_autostart_changed(self, state):
        if state == Qt.CheckState.Checked.value:
            self.consent.set_consent(CONSENT_GRANTED)
        elif state == Qt.CheckState.Unchecked.value:
            self.consent.set_consent(CONSENT_DECLINED)
        else:
            self.consent.set_consent(CONSENT_NOT_ASKED)
        self.consent_label.setText(self.consent.line())
