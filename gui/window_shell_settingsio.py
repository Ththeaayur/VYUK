# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import VERSION


class ShellSettingsIOMixin:

    _SETTINGS_SECRET_MARKERS = ("key", "token", "secret",
                                "password", "credential")


    def _settings_exportable(self) -> dict:
        """Every QSettings value EXCEPT anything secret-shaped:
        a settings file is for carrying choices between PCs,
        never for moving keys or credentials."""
        out = {}
        for key in self.settings.allKeys():
            lowered = key.lower()
            if any(m in lowered for m in self._SETTINGS_SECRET_MARKERS):
                continue
            value = self.settings.value(key)
            if isinstance(value, (str, int, float, bool, list, dict)):
                out[key] = value
            else:
                out[key] = str(value)
        return out


    def _settings_file_note(self, text: str):
        self.settings_file_label.setText(text)
        self.settings_file_label.setVisible(True)


    def _export_settings(self):
        import json as _json
        from datetime import datetime as _dt

        path, _sel = QFileDialog.getSaveFileName(
            self, "Export settings", "visor-settings.json",
            "JSON files (*.json)")
        if not path:
            return
        payload = {
            "app": "VYUK",
            "version": VERSION,
            "exported_at": _dt.now().isoformat(timespec="seconds"),
            "settings": self._settings_exportable(),
        }
        try:
            Path(path).write_text(
                _json.dumps(payload, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self._settings_file_note(f"Couldn't write the file: {e}")
            return
        self._settings_file_note(
            f"Saved {len(payload['settings'])} settings to "
            f"{Path(path).name}. Keys and secrets were "
            "not included — those never leave this PC in a file.")


    def _import_settings(self):
        import json as _json

        path, _sel = QFileDialog.getOpenFileName(
            self, "Import settings", "",
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            payload = _json.loads(
                Path(path).read_text(encoding="utf-8"))
            incoming = payload["settings"]
            if not isinstance(incoming, dict):
                raise ValueError("no settings in the file")
        except Exception as e:  # noqa: BLE001
            self._settings_file_note(
                f"That file couldn't be read as a settings file: {e}")
            return
        applied = 0
        for key, value in incoming.items():
            lowered = str(key).lower()
            if any(m in lowered for m in self._SETTINGS_SECRET_MARKERS):
                continue
            self.settings.setValue(str(key), value)
            applied += 1
        self.settings.sync()
        self._settings_file_note(
            f"Imported {applied} settings. Some choices apply "
            "the next time you connect or restart VYUK")
