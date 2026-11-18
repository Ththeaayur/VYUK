"""Wave 9 (v5.64) — superqt widgets behind a lazy import + probe.

Exposes superqt's range sliders, collapsibles and searchable combos
(QRangeSlider, QCollapsible, QSearchableComboBox) WITHOUT making
superqt a hard dependency:

* superqt is never imported at module import time — only inside
  :func:`superqt_available` / the factories, via ``importlib``;
* when superqt is absent (or unimportable), the factories return
  native-Qt fallback widgets with the same small API surface
  (``value()`` / ``setValue()`` for sliders, ``setExpanded`` /
  ``isExpanded`` + ``addWidget`` for collapsibles, and a plain
  editable ``QComboBox`` for searchable combos), so callers never
  branch on availability themselves;
* superqt is NOT in requirements.txt and must not be added there by
  this wave — it is an optional enhancement, probed at runtime.

Licence (verified from the wheel primary, superqt 0.9.0):
BSD-3-Clause — ``LICENSE`` + ``METADATA`` read from the downloaded
wheel locally; see the Wave 9 Leaf 1 handoff for the verdict.
Permissive, commercial-compatible as an OPTIONAL dependency.
"""

from __future__ import annotations

import importlib

MODULE_VERSION = "5.64.0"

_probe_cache: dict | None = None


def superqt_available() -> dict:
    """Probe for superqt without importing it at module load.

    Cached after the first probe. Returns ``{"available": bool,
    "version": str | None, "reason": str}``.
    """
    global _probe_cache
    if _probe_cache is not None:
        return dict(_probe_cache)
    try:
        mod = importlib.import_module("superqt")
        version = getattr(mod, "__version__", None)
        result = {"available": True, "version": version,
                  "reason": "ok"}
    except Exception as exc:  # missing, broken, or no Qt backend
        result = {"available": False, "version": None,
                  "reason": f"superqt-unavailable: {exc.__class__.__name__}"}
    _probe_cache = result
    return dict(result)


def _reset_probe_cache() -> None:  # tests only
    global _probe_cache
    _probe_cache = None


# ---------------------------------------------------------------------------
# Qt widgets (only defined when Qt is importable)
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QComboBox, QFrame, QLabel, QPushButton,
                                   QSlider, QVBoxLayout, QWidget)
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - Qt absent
    _QT_AVAILABLE = False


if _QT_AVAILABLE:
    class FallbackRangeSlider(QWidget):
        """Two sliders standing in for a range slider.

        API shared with the superqt path used here: ``value()``
        returns ``(low, high)`` and ``setValue((low, high))`` sets
        both ends (clamped so low <= high).
        """

        def __init__(self, minimum: int = 0, maximum: int = 100,
                     parent=None):
            super().__init__(parent)
            self._min, self._max = minimum, maximum
            layout = QVBoxLayout(self)
            self.low_slider = QSlider(Qt.Orientation.Horizontal, self)
            self.high_slider = QSlider(Qt.Orientation.Horizontal, self)
            for slider in (self.low_slider, self.high_slider):
                slider.setRange(minimum, maximum)
            self.low_slider.setValue(minimum)
            self.high_slider.setValue(maximum)
            layout.addWidget(QLabel("Low", self))
            layout.addWidget(self.low_slider)
            layout.addWidget(QLabel("High", self))
            layout.addWidget(self.high_slider)

        def value(self) -> tuple[int, int]:
            low = min(self.low_slider.value(), self.high_slider.value())
            high = max(self.low_slider.value(), self.high_slider.value())
            return (low, high)

        def setValue(self, value: tuple[int, int]) -> None:
            low, high = value
            low = max(self._min, min(low, self._max))
            high = max(self._min, min(high, self._max))
            if low > high:
                low, high = high, low
            self.low_slider.setValue(low)
            self.high_slider.setValue(high)

    class FallbackCollapsible(QWidget):
        """Header button + toggleable content area."""

        def __init__(self, title: str = "", parent=None):
            super().__init__(parent)
            layout = QVBoxLayout(self)
            self.toggle_button = QPushButton(title, self)
            self.toggle_button.setCheckable(True)
            self.toggle_button.setChecked(True)
            self.content = QFrame(self)
            self.content_layout = QVBoxLayout(self.content)
            self.toggle_button.toggled.connect(self.content.setVisible)
            layout.addWidget(self.toggle_button)
            layout.addWidget(self.content)

        def addWidget(self, widget) -> None:
            self.content_layout.addWidget(widget)

        def setExpanded(self, expanded: bool) -> None:
            self.toggle_button.setChecked(expanded)

        def expand(self) -> None:
            self.setExpanded(True)

        def collapse(self) -> None:
            self.setExpanded(False)

        def isExpanded(self) -> bool:
            return self.toggle_button.isChecked()

    def make_range_slider(minimum: int = 0, maximum: int = 100,
                          parent=None):
        """Range slider: superqt QRangeSlider when present, else fallback."""
        if superqt_available()["available"]:
            from superqt import QRangeSlider
            slider = QRangeSlider(parent)
            slider.setRange(minimum, maximum)
            slider.setValue((minimum, maximum))
            return slider
        return FallbackRangeSlider(minimum, maximum, parent)

    def make_collapsible(title: str = "", parent=None):
        """Collapsible: superqt QCollapsible when present, else fallback."""
        if superqt_available()["available"]:
            from superqt import QCollapsible
            return QCollapsible(title, parent)
        return FallbackCollapsible(title, parent)

    def make_searchable_combo(items: list[str] | None = None,
                              parent=None):
        """Searchable combo: superqt QSearchableComboBox, else editable combo.

        The fallback is an editable QComboBox, which gives the same
        type-to-filter behaviour for short lists; it is not a
        superqt widget and is never claimed to be one.
        """
        if superqt_available()["available"]:
            from superqt import QSearchableComboBox
            combo = QSearchableComboBox(parent)
        else:
            combo = QComboBox(parent)
            combo.setEditable(True)
        if items:
            combo.addItems(items)
        return combo
else:  # pragma: no cover - Qt absent
    def make_range_slider(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError("PySide6 is required for superqt_widgets")

    def make_collapsible(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError("PySide6 is required for superqt_widgets")

    def make_searchable_combo(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError("PySide6 is required for superqt_widgets")
