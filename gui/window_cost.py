"""Cost ledger surface (GAPS-27, M-V9).

The cost ledger (core/cost_ledger) was wired into all three
brains but had no reader in gui/ — spend was recorded and
invisible. This module is the read side: cost_summary() (the
ledger's summary + recent turns, as plain data) and the
read-only dialog that renders it. Surfaced from Settings
(gui/window_settings_advcards, next to the Activity feed
button). The surface never writes, never records a turn, and
never changes a cap — it only shows what the ledger already
holds.

Like gui/window_activity, this module imports no Qt at the
top: pure functions first, dialog builders (lazy Qt) at the
bottom.
"""

from __future__ import annotations

__all__ = ["cost_summary", "render_cost_text", "build_cost_dialog"]


def _cost_ledger_mod():
    """The core/cost_ledger module, or None when absent /
    unimportable. Never raises. Reached through the GUI facade
    (core/gui_facade — GUI files never import core directly)."""
    try:
        from voice_assistant.core.gui_facade import cost_ledger

        return cost_ledger
    except Exception:  # noqa: BLE001
        return None


def cost_summary(db_path=None, limit=20):
    """{"available", "summary", "recent_turns", "error"}: the
    ledger's summary() (spend today/month, caps, by-brain) plus
    the newest turns (CostLedger.recent_turns). `db_path` None
    means the ledger's default location. Never raises."""
    mod = _cost_ledger_mod()
    if mod is None:
        return {"available": False, "summary": {}, "recent_turns": [],
                "error": "cost ledger not available yet"}
    try:
        ledger = mod.CostLedger(db_path=db_path) \
            if db_path is not None else mod.CostLedger()
        try:
            summary = ledger.summary()
            turns = ledger.recent_turns(int(limit))
        finally:
            ledger.close()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "summary": {}, "recent_turns": [],
                "error": str(exc)}
    return {"available": True, "summary": summary,
            "recent_turns": turns, "error": None}


def render_cost_text(view):
    """A cost summary as plain display text. Never raises."""
    try:
        if not view.get("available"):
            return ("Not yet available: "
                    + str(view.get("error") or "cost ledger "
                                              "not available yet"))
        summary = view.get("summary") or {}
        today = summary.get("spend_today_usd") or 0
        month = summary.get("spend_month_usd") or 0
        day_cap = summary.get("daily_cap_usd") or 0
        month_cap = summary.get("monthly_cap_usd") or 0
        lines = [
            f"Spend today: ${today:.4f} (cap ${day_cap:.2f})",
            f"Spend this month: ${month:.4f} (cap ${month_cap:.2f})",
        ]
        for row in summary.get("by_brain") or []:
            lines.append(
                f"{row.get('brain', '')} "
                f"[{row.get('measurement', '')}]: "
                f"{row.get('turns', 0)} turn(s), "
                f"${row.get('cost_usd') or 0:.4f}")
        turns = view.get("recent_turns") or []
        if not turns:
            lines.append("No turns recorded yet.")
        for turn in turns:
            cost = turn.get("cost_usd")
            cost_text = (f"${cost:.4f}"
                         if isinstance(cost, (int, float))
                         else "cost n/a")
            lines.append(
                f"{turn.get('day', '')} {turn.get('brain', '')} · "
                f"{turn.get('model', '')} · "
                f"{turn.get('prompt_tokens', 0)}+"
                f"{turn.get('completion_tokens', 0)} tokens · "
                f"{cost_text} [{turn.get('measurement', '')}]")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return "Not yet available."


# ---------------------------------------------------------------------------
# Dialog builder — Qt imported lazily here (see window_activity
# for the pattern). Returns (dialog, widgets) without exec()ing.
# ---------------------------------------------------------------------------

def build_cost_dialog(parent=None, db_path=None):
    """The cost ledger dialog: spend today/month with caps,
    by-brain totals, and the recent turns. Read-only. Returns
    (dialog, widgets)."""
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QPushButton,
        QVBoxLayout,
    )
    from voice_assistant.gui.style import DIALOG_STYLE

    dlg = QDialog(parent)
    dlg.setWindowTitle("Cost ledger")
    dlg.setStyleSheet(DIALOG_STYLE)
    dlg.resize(720, 480)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 20, 20, 16)
    lay.setSpacing(10)
    status = QLabel("")
    status.setObjectName("muted")
    status.setWordWrap(True)
    lay.addWidget(status)
    view = QListWidget()
    lay.addWidget(view, stretch=1)
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_refresh = QPushButton("Refresh")
    btn_close = QPushButton("Close")
    btn_row.addWidget(btn_refresh)
    btn_row.addWidget(btn_close)
    btn_close.setDefault(True)
    lay.addLayout(btn_row)

    def _fill():
        result = cost_summary(db_path=db_path)
        if not result.get("available"):
            status.setText("Cost ledger: unavailable.")
            view.clear()
            view.addItem(render_cost_text(result))
            return
        summary = result.get("summary") or {}
        status.setText(
            f"Spend today ${summary.get('spend_today_usd', 0):.4f} / "
            f"month ${summary.get('spend_month_usd', 0):.4f}.")
        view.clear()
        for line in render_cost_text(result).splitlines():
            view.addItem(line)

    btn_refresh.clicked.connect(_fill)
    btn_close.clicked.connect(dlg.accept)
    _fill()
    widgets = {"status": status, "view": view,
               "refresh": btn_refresh, "close": btn_close,
               "fill": _fill}
    return dlg, widgets
