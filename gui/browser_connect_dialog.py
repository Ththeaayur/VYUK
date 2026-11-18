"""Standalone browser-connect dialog (first-run onboarding, 2026-11-16).

The wizard's Browser page, reused outside the wizard: the same
BrowserConnectWidget, in a HudDialog. Opened from the Assistant
strip's "Connect my browser" button, from the Home strip, and
from Settings → Browser control. The Settings card keeps its
own inline Enable flow as the second way in — this dialog is
the one-tap way back, never a replacement.

Double-open guard (same shape as the Connect dialog): a second
request while it is open just raises the open one.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QVBoxLayout

from .browser_connect_widget import BrowserConnectWidget
from .design_system.dialogs import HudDialog


class BrowserConnectDialog(HudDialog):
    """A modal dialog around the browser-connect widget."""

    def __init__(self, parent=None):
        super().__init__(parent, title="Connect my browser")
        self.setAccessibleName("Connect my browser dialog")
        layout = self.content_layout()
        self.widget = BrowserConnectWidget(self)
        layout.addWidget(self.widget)
        # Build 5 (§9.1 step 0): "Not now" in the standalone
        # dialog closes it — the same skip as the wizard page
        # (records nothing, browser stays unknown).
        self.widget.not_now_requested.connect(self.close)
        close_row = QVBoxLayout()
        close_row.setContentsMargins(0, 0, 0, 0)
        self.btn_done = QPushButton("Done")
        self.btn_done.setObjectName("chipBtn")
        self.btn_done.setAccessibleName("Close browser connect dialog")
        self.btn_done.clicked.connect(self.close)
        close_row.addWidget(self.btn_done)
        layout.addLayout(close_row)


def open_browser_connect_dialog(owner) -> None:
    """Open the standalone browser-connect dialog for ``owner``
    (the main window or the Settings window), or raise the open
    one. The double-open guard lives on the owner as
    ``_browser_connect_dialog``. Opening clears the strip's
    no-session flag, so the strip hides while the user is
    connecting (the connect flow clears it on success; a
    refused browser call re-sets it). Never raises."""
    dlg = getattr(owner, "_browser_connect_dialog", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return
        except Exception:  # noqa: BLE001 — rebuild below
            pass
    try:
        from voice_assistant.core.gui_facade import (
            settings_voice as _sv)

        try:
            _sv.clear_browser_no_session()
        except Exception:  # noqa: BLE001 — the dialog still opens
            pass
        dlg = BrowserConnectDialog(owner)
        dlg.finished.connect(
            lambda *_: setattr(owner, "_browser_connect_dialog", None))
        owner._browser_connect_dialog = dlg
        dlg.show()
    except Exception:  # noqa: BLE001 — the offer stands
        pass
