from __future__ import annotations

# GAPS-15 (2026-11-13): moved from core/ to gui/ — this module
# builds Qt widgets / stylesheets / panels, which is GUI work
# that lived in core (core -> GUI imports). Code unchanged;
# only the imports changed: GUI imports are now relative,
# core imports go through the GUI facade (core/gui_facade.py).

from pathlib import Path  # INTENTIONAL (B31: moved code resolves Path by bare name)
from voice_assistant.core.gui_facade import on_this_day as _on_this_day  # GAPS-15: core import via the GUI facade

# ------------------------------------------------------------------ #
# 6. First-run proof runner (onboarding done page)
# ------------------------------------------------------------------ #

def build_first_run_proof_run(evidence=None):
    """ProofRun from evidence only — a check passes only on evidence.

    ``evidence`` maps check_id -> (status, detail, next_step).
    Checks without evidence stay not-run with an honest next step;
    nothing is claimed as proven by construction. Callers pass
    real observations (e.g. the onboarding mic meter's peak).
    """
    from . import pf_surfaces as _pf

    probes = {}
    evidence = evidence or {}
    mic_peak = evidence.get("mic_peak")
    if mic_peak is not None:
        if mic_peak > 0:
            probes["microphone"] = (
                "passed",
                f"Microphone meter moved (peak {int(mic_peak)}%).",
                "")
        else:
            probes["microphone"] = (
                "not_run",
                "Microphone meter did not move yet.",
                "Say something on the microphone step and watch "
                "the bar move, then return here.")
    for check_id, value in evidence.items():
        if check_id == "mic_peak":
            continue
        if isinstance(value, tuple) and len(value) == 3:
            probes[check_id] = value
    return _pf.ProofRun(checks=_pf.build_proof_checks(probes))


def refresh_first_run_proof(widget, evidence=None) -> None:
    """Rebuild a FirstRunProofWidget's run from current evidence and
    repaint its headline + check lines in place."""
    run = build_first_run_proof_run(evidence)
    widget.run = run
    try:
        widget.headline_label.setText(run.headline())
        for label, check in zip(widget.check_labels, run.checks):
            label.setText(check.summary_line())
    except Exception:  # noqa: BLE001 — widget keeps its old run
        pass


# ------------------------------------------------------------------ #
# 9. Overview panel (fixed layout) fed from the live activity log
# ------------------------------------------------------------------ #

def overview_from_activity_lines(lines):
    """(series, events) from activity-log lines.

    Lines are "timestamp | name | status" (see core/audit.py); a
    line that does not parse is skipped, never fatal. The series
    is the cumulative activity count so the plot always has a
    shape when the log is non-empty; empty input is an empty
    panel, honestly.
    """
    from . import overview_panel as _overview

    events = []
    for line in lines or []:
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) < 2:
            continue
        time_label = parts[0][:16]
        kind = parts[1] or "activity"
        text = parts[2] if len(parts) > 2 else kind
        events.append(_overview.ActivityEvent(
            time_label=time_label, kind=kind, text=text))
    series = []
    if events:
        series = [_overview.PlotSeries(
            label="Activity",
            xs=list(range(len(events))),
            ys=[float(i + 1) for i in range(len(events))])]
    return series, events


def build_overview_panel(parent=None, activity_path=None):
    """OverviewPanel fed from the live activity log (or a given path).

    The panel is the fixed-layout surface; pyqtgraph stays
    lazy-optional inside the staged module, with FallbackPlotWidget
    as the no-dependency path.
    """
    from . import overview_panel as _overview

    panel = _overview.OverviewPanel(parent)
    path = (Path(activity_path) if activity_path is not None
            else Path(__file__).resolve().parent.parent / "data"
            / "activity.log")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace"
                               ).splitlines()[-100:]
    except Exception:  # noqa: BLE001 — no log yet is a normal state
        lines = []
    series, events = overview_from_activity_lines(lines)
    if series:
        panel.set_series(series)
    for event in events[-20:]:
        panel.add_activity(event)
    panel._w9_activity_events = events  # noqa: SLF001 — wiring record
    return panel


# ------------------------------------------------------------------ #
# 14. On-this-day resurfacing -> day-briefing card
# ------------------------------------------------------------------ #

def on_this_day_lines(today=None) -> list[str]:
    """Formatted on-this-day lines from the LIVE milestone log
    (core/milestones), never a fixture path. [] is normal."""
    events = _on_this_day.read_events()
    return _on_this_day.summarise_day(events, today=today)


def update_briefing_on_this_day(window) -> list[str]:
    """Fill (or hide) the briefing card's on-this-day label."""
    label = getattr(window, "briefing_on_this_day", None)
    if label is None:
        return []
    try:
        lines = on_this_day_lines()
    except Exception:  # noqa: BLE001 — briefing must not break
        lines = []
    try:
        if lines:
            label.setText("On this day: " + " · ".join(lines))
            label.setVisible(True)
        else:
            label.setText("")
            label.setVisible(False)
    except Exception:  # noqa: BLE001
        pass
    return lines
