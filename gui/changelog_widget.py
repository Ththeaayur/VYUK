"""Wave 10 (v5.65) Leaf 2 — PF-6 changelog presentation (STAGED, unwired).

Renders the pending changelog entries from ``core/changelog.py``.
NOT wired into any live window — the coordinator wires it.

Qt degradation: when PySide6 is unavailable (e.g. system-python
tests), this module still imports cleanly, ``QT_AVAILABLE`` is
False, ``ChangelogWidget`` is None, and the pure helpers
(``format_entries`` / ``pending_text``) still work. It never fails
on import for want of Qt.
"""

from __future__ import annotations

try:
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt absent is a supported state
    QT_AVAILABLE = False
    QLabel = QVBoxLayout = QWidget = None  # type: ignore[assignment]

try:
    from ..core.gui_facade import changelog as _changelog
except Exception:  # noqa: BLE001 — direct-module import fallback
    _changelog = None  # type: ignore[assignment]


def format_entries(entries) -> str:
    """Plain-text rendering of pending entries, one per line."""
    lines = []
    for entry in entries or []:
        version = entry.get("version", "?") if isinstance(entry, dict) else "?"
        text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
        lines.append(f"What's new in {version}: {text}")
    return "\n\n".join(lines)


def pending_text(last_seen_version) -> str:
    """Text of exactly the entries newer than ``last_seen_version``."""
    if _changelog is None:
        return ""
    return format_entries(_changelog.pending_entries(last_seen_version))


if QT_AVAILABLE:
    class ChangelogWidget(QWidget):
        """Read-only list of pending entries; no buttons, no side effects.

        Construct with the pending entries (list of {"version","text"}
        dicts, as returned by ``changelog.pending_entries``).
        The widget never marks anything seen — the coordinator does
        that explicitly after the user has had a chance to read.
        """

        def __init__(self, entries=None, parent=None):
            super().__init__(parent)
            self.entries = list(entries or [])
            layout = QVBoxLayout(self)
            self.heading_label = QLabel("What's new in VYUK")
            layout.addWidget(self.heading_label)
            self.body_label = QLabel(format_entries(self.entries) or
                                     "You're up to date — nothing new to show.")
            self.body_label.setWordWrap(True)
            layout.addWidget(self.body_label)

        def refresh(self, entries=None):
            """Replace the shown entries (still no side effects)."""
            if entries is not None:
                self.entries = list(entries)
            self.body_label.setText(
                format_entries(self.entries) or
                "You're up to date — nothing new to show.")
else:
    ChangelogWidget = None  # type: ignore[assignment]
