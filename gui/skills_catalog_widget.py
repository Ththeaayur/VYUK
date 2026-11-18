"""
Skills catalogue (v3.2) — the built-in half of the Skills page.

The Skills page used to list only user plugins, so a fresh VYUK —
with well over a hundred built-in tools — looked like she could do
almost nothing. This widget is the catalogue that sits above the
plugins list: every built-in tool, grouped by the pack that
registered it, searchable, with a friendly name and a one-line
summary per skill.

Public API (the window wires exactly this):

    build_skills_catalog(declarations, friendly_names) -> QWidget

* ``declarations`` are ``tools.get_declarations()`` dicts
  (``{name, description, parameters}``).
* ``friendly_names`` is the window's ``_TOOL_FRIENDLY_NAMES`` map.

The returned widget carries ``search_input`` (its QLineEdit),
``skills_list``, ``count_label`` and ``empty_label`` attributes,
plus ``set_filter(text)`` so the page can drive the search from
outside. It deliberately has NO card frame of its own — the page
provides the card; this is the inner content.

Grouping: GROUP_MEMBERS below assigns every known tool name to
exactly one group, derived from the pack that registers it in
``core/funnel/tools_registry.py`` / the pack modules:

* "Hands & eyes"     — core/desktop/control.py + core/desktop/control_plus.py +
                       core/desktop/cursor.py packs (direct manipulation:
                       mouse, keyboard, windows, files, screen).
* "Mind"             — core/brain/planner.py (plans + routines) plus the
                       extended pack's set_reminder and the day
                       journal in core/episodes.py.
* "Web"              — core/browser.py.
* "Your world"       — core/google_services.py.
* "Everyday skills"  — core/skills_plus.py.
* "Play & create"    — core/skills_games.py + core/skills_creative.py
                       (v5.13): games with persistent state and the
                       generative/creative tools.
* "System & apps"    — core/skills_system.py.
* "Files & media"    — core/skills_media.py.
* "Network & display"— core/skills_network.py.
* "Built-in"         — the default + extended tools registered in
                       core/funnel/tools_handlers_pack_core1.py (time, weather, memory
                       facts, system info, clipboard, basic files).
* "More skills"      — fallback for anything unplaced (e.g. plugin
                       declarations the registry also reports), so
                       a name is never dropped and never doubled.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen

# Command Deck Phase E: the delegate paints from the token palette.
from .design_system.palette import parse_color
from .style import ACCENT, TEXT, TEXT_FAINT, TEXT_MUTED
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

# GUI split #2 INTENTIONAL: data half moved verbatim to
# skills_catalog_data.py; the widget imports the tables, roles and
# constants from there (no duplication, no behaviour change).
from .skills_catalog_data import (
    GROUP_ORDER,
    ROLE_FRIENDLY,
    ROLE_GROUP,
    ROLE_KIND,
    ROLE_NAME,
    ROLE_SUMMARY,
    _FALLBACK_GROUP,
    _MAX_SUMMARY,
    _NAME_TO_GROUP,
)

def group_for_name(name: str) -> str:
    """The catalogue group for one raw tool name (fallback-safe)."""
    return _NAME_TO_GROUP.get(name, _FALLBACK_GROUP)


def _humanise(name: str) -> str:
    """Friendly-name fallback for a tool missing from the map."""
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def _summarise(description: str) -> str:
    """First sentence of a description, capped at 95 chars + ellipsis."""
    text = " ".join((description or "").split())
    if not text:
        return ""
    sentence = text
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        # Abbreviations ("e.g.", "i.e.", "Dr.") are not sentence ends.
        before = re.search(r"([A-Za-z]+)$", text[: match.start()])
        if before and (len(before.group(1)) == 1
                       or before.group(1).lower() in
                       {"eg", "ie", "etc", "vs", "dr", "mr", "mrs", "st"}):
            continue
        sentence = text[: match.end()]
        break
    sentence = sentence.rstrip()
    if sentence.endswith("."):
        sentence = sentence[:-1].rstrip()
    if len(sentence) > _MAX_SUMMARY:
        sentence = sentence[: _MAX_SUMMARY - 1].rstrip() + "…"
    return sentence


class _CatalogueDelegate(QStyledItemDelegate):
    """Paints rows: friendly name in demi-bold, summary in muted
    regular, headers as muted caps — the QListWidget keeps plain
    text on every item (name + " — " + summary) for accessibility
    and tests; this delegate only upgrades the rendering."""

    _HEADER_H = 30
    _TOOL_H = 38

    def sizeHint(self, option, index):  # noqa: N802 (Qt naming)
        size = super().sizeHint(option, index)
        kind = index.data(ROLE_KIND)
        size.setHeight(self._HEADER_H if kind == "header" else self._TOOL_H)
        return size

    def paint(self, painter, option, index):  # noqa: N802 (Qt naming)
        kind = index.data(ROLE_KIND)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if option.state & QStyle.StateFlag.State_Selected:
            _ar, _ag, _ab, _aa = parse_color(ACCENT)
            painter.fillRect(rect, QColor(_ar, _ag, _ab, 40))

        if kind == "header":
            font = QFont(option.font)
            font.setPointSizeF(11)
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
            painter.setFont(font)
            painter.setPen(QColor(TEXT_FAINT))
            text_rect = rect.adjusted(12, 0, -12, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                index.data(Qt.ItemDataRole.DisplayRole) or "",
            )
        else:
            friendly = index.data(ROLE_FRIENDLY) or ""
            summary = index.data(ROLE_SUMMARY) or ""
            left = rect.left() + 12
            right = rect.right() - 12
            center_y = rect.center().y()

            bold_font = QFont(option.font)
            bold_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(bold_font)
            painter.setPen(QColor(TEXT))
            bold_metrics = QFontMetrics(bold_font)
            friendly_w = bold_metrics.horizontalAdvance(friendly)
            painter.drawText(
                left,
                center_y + bold_metrics.ascent() // 2 - 1,
                friendly,
            )

            if summary:
                sep = " — "
                regular_font = QFont(option.font)
                regular_metrics = QFontMetrics(regular_font)
                sep_x = left + friendly_w
                painter.setFont(regular_font)
                painter.setPen(QColor(TEXT_MUTED))
                painter.drawText(
                    sep_x,
                    center_y + regular_metrics.ascent() // 2 - 1,
                    sep,
                )
                avail = max(0, right - (sep_x + regular_metrics.horizontalAdvance(sep)))
                elided = regular_metrics.elidedText(
                    summary, Qt.TextElideMode.ElideRight, avail)
                painter.drawText(
                    sep_x + regular_metrics.horizontalAdvance(sep),
                    center_y + regular_metrics.ascent() // 2 - 1,
                    elided,
                )

        # Row hairline, matching the stylesheet's item border.
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


class SkillsCatalogWidget(QWidget):
    """Search + grouped list + count footer for all registered
    tools (GAPS-29, E-V1: plugin tools included — they land in
    "More skills" through the group_for_name fallback)."""

    def __init__(self, declarations, friendly_names, parent=None):
        super().__init__(parent)
        self.setObjectName("skillsCatalog")

        self._friendly_names = dict(friendly_names or {})
        # De-duplicate by raw name (first wins) so a tool can never
        # appear twice, then bucket by group preserving registry order.
        self._tools: list[dict] = []
        seen: set[str] = set()
        for decl in declarations or []:
            name = (decl or {}).get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            self._tools.append(
                {
                    "name": name,
                    "friendly": self._friendly_names.get(name) or _humanise(name),
                    "description": (decl or {}).get("description") or "",
                    "group": group_for_name(name),
                }
            )
        self._total = len(self._tools)
        self._visible = self._total

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search skills…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_input)

        self.skills_list = QListWidget()
        self.skills_list.setObjectName("skillsList")
        self.skills_list.setItemDelegate(_CatalogueDelegate(self.skills_list))
        self.skills_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.skills_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skills_list.setUniformItemSizes(False)
        layout.addWidget(self.skills_list, stretch=1)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        self.count_label = QLabel()
        self.count_label.setObjectName("smallMuted")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.count_label)

        self._rebuild("")

    # -- public surface -------------------------------------------------

    def set_filter(self, text: str) -> None:
        """Drive the search from outside (also updates the field)."""
        text = text or ""
        if self.search_input.text() != text:
            self.search_input.setText(text)  # textChanged -> _apply_filter
        else:
            self._apply_filter(text)

    def group_counts(self) -> dict[str, int]:
        """Tool count per group over the FULL catalogue (unfiltered)."""
        counts: dict[str, int] = {}
        for tool in self._tools:
            counts[tool["group"]] = counts.get(tool["group"], 0) + 1
        return counts

    def tool_names(self) -> list[str]:
        """Every catalogued raw tool name, in display order."""
        names: list[str] = []
        for row in range(self.skills_list.count()):
            item = self.skills_list.item(row)
            if item.data(ROLE_KIND) == "tool":
                names.append(item.data(ROLE_NAME))
        return names

    @property
    def total_count(self) -> int:
        return self._total

    @property
    def visible_count(self) -> int:
        return self._visible

    # -- internals ------------------------------------------------------

    def _matches(self, tool: dict, needle: str) -> bool:
        if not needle:
            return True
        return (
            needle in tool["friendly"].lower()
            or needle in tool["name"].lower()
            or needle in tool["description"].lower()
        )

    def _apply_filter(self, text: str) -> None:
        self._rebuild(text or "")

    def _rebuild(self, query: str) -> None:
        needle = query.strip().lower()
        buckets: dict[str, list[dict]] = {group: [] for group in GROUP_ORDER}
        for tool in self._tools:
            if self._matches(tool, needle):
                buckets[tool["group"]].append(tool)

        self._visible = sum(len(rows) for rows in buckets.values())

        self.skills_list.clear()
        for group in GROUP_ORDER:
            rows = buckets[group]
            if not rows:
                continue  # headers hide when their group empties
            header = QListWidgetItem(f"{group.upper()} — {len(rows)}")
            header.setData(ROLE_KIND, "header")
            header.setData(ROLE_GROUP, group)
            header.setToolTip(f"{len(rows)} skills")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.skills_list.addItem(header)
            for tool in rows:
                summary = _summarise(tool["description"])
                text = (
                    f"{tool['friendly']} — {summary}" if summary
                    else tool["friendly"]
                )
                item = QListWidgetItem(text)
                item.setData(ROLE_NAME, tool["name"])
                item.setData(ROLE_FRIENDLY, tool["friendly"])
                item.setData(ROLE_SUMMARY, summary)
                item.setData(ROLE_KIND, "tool")
                item.setData(ROLE_GROUP, group)
                if tool["description"]:
                    item.setToolTip(tool["description"])
                self.skills_list.addItem(item)

        if needle and self._visible == 0:
            self.empty_label.setText(f"No skills match '{query.strip()}'.")
            self.empty_label.show()
            self.skills_list.hide()
        else:
            self.empty_label.hide()
            self.skills_list.show()

        if needle:
            self.count_label.setText(
                f"{self._visible} of {self._total} skills")
        else:
            self.count_label.setText(f"{self._total} skills")


def build_skills_catalog(declarations: list[dict],
                         friendly_names: dict[str, str]) -> QWidget:
    """Build the skills catalogue widget (see module docstring) —
    all registered tools, plugin tools included (GAPS-29, E-V1)."""
    return SkillsCatalogWidget(declarations, friendly_names)
