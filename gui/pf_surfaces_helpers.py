"""PF surfaces shared helpers (GUI split #3)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


MODULE_VERSION = "5.64.0"

#: Wired in the v5.64 conversion pass. The wiring step placed
#: each widget; it must not add a sidebar button for any of them.
PLACEMENTS = {
    "FirstRunProofWidget": (
        "First-run / onboarding area (NOT Advanced): the "
        "proof run completes before anything is asked of the "
        "user. Wiring step: hook the proof runner to the "
        "first-launch flow in onboarding; this widget only "
        "renders a run. No sidebar button."
    ),
    "SettingsExplainedWidget": (
        "Settings, under Advanced, as a card "
        "beside the existing Keyboard shortcuts card. It "
        "explains the existing cards; it does not replace "
        "them. No sidebar button."
    ),
    "ShortcutsWidget": (
        "S4: removed from the Settings page — it rendered "
        "the same SHORTCUT_ROWS under the same sectionTitle "
        "as the native Keyboard shortcuts card, so the "
        "palette listed the row twice. Class retained; the "
        "native card + the '?' sheet are the one list now. "
        "No sidebar button."
    ),
    "HelpWidget": (
        "Settings, under Advanced, as a 'Help' "
        "card opening this viewer. No sidebar button."
    ),
    "PermissionsWidget": (
        "Settings, in the Privacy & permissions group, as a "
        "'Permissions' card beside 'Privacy & security'. "
        "No sidebar button."
    ),
    "CitizenshipWidget": (
        "Settings, in the Connections group, as a "
        "'Desktop citizenship' card beside 'Presence'. "
        "No sidebar button."
    ),
    "PrivacyDashboardWidget": (
        "Settings, in the Privacy & permissions group, as a 'Your "
        "stored facts' card beside 'Privacy & security' "
        "(the Doctor precedent: a full view reached from "
        "an Advanced card, no sidebar entry). "
        "No sidebar button."
    ),
}

STAGED_NOTE = (
    "Staged in v5.64: this surface is built but not wired "
    "into any window yet. Nothing here changes a setting, "
    "a permission, or a stored fact until the wiring step "
    "connects it — and the wiring step must say so where "
    "it matters, as this module does."
)


def _card(parent_layout, title, hint):
    """One redo-style card: HudPanel frame, section
    title, muted hint. Matches gui/window_settings.py's
    _settings_card shape (object names, not styling).
    Phase C: the card is a HudPanel (chamfer + role line);
    its content margins uniform to 16/6 (was 16/12 vertical)."""
    # Function-local: pf keeps its light module import (stdlib +
    # PySide6 only); _card runs in GUI context only. Dual-context:
    # package loads (production, gui.* tests) take the relative
    # branch; standalone file-path loads take the absolute
    # fallback — same pattern as orbthumb.
    try:
        from .design_system.panels import HudPanel
    except ImportError:
        from gui.design_system.panels import HudPanel

    card = HudPanel()
    card.setObjectName("hudPanel")
    layout = card.content_layout()
    heading = QLabel(title)
    heading.setObjectName("sectionTitle")
    heading.setStyleSheet("font-weight: 600;")
    layout.addWidget(heading)
    if hint:
        note = QLabel(hint)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
    parent_layout.addWidget(card)
    return card, layout


def _muted(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _status_label(text):
    """A status line that always carries a next step when
    it reports a failure or an empty result. Callers pass
    the full sentence; this helper only guarantees the
    widget shape (word-wrapped, muted)."""
    return _muted(text)
