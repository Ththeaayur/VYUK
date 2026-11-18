"""Health trigger wiring (v5.69, parent-gate wiring for the Health track).

One funnel tool, ``health``, over the track's tested library:

* ``extract`` — pull values out of pasted record text (JSON or
  ``DATE | METRIC | VALUE | UNIT`` lines). Unknown lines are
  counted as skipped, never interpreted. Read-only.
* ``record`` — CONFIRM-GATED (args-aware rule in tools_gate):
  store one user-supplied observation (date/metric/value/unit)
  as a FHIR-shaped Observation in the track's parallel store.
  No terminology codes are accepted through this tool (the
  library refuses excluded code systems at write time anyway).
* ``summary`` — list the stored records, rendered as recorded
  ("On <date>, <metric> was recorded as <value> <unit>.").
  Read-only.
* ``remind`` — a desk-body reminder, held during the user's
  quiet-hours window when one is set. Read-only in effect (it
  writes nothing; the reminder text is the result).

Deliberately NOT in v1: the D6 MediaPipe posture pilot (enable
decision owed — default-on never assumed), any camera-session
start (S-6 grant machinery needs its own design), live wearable
reads (the funnel supplies no fetcher, so they would only report
honest-unavailable), C8 images (refused by the library), and any
diagnosis-side shape (scoring, coaching, thresholds — parked by
the plan, never exposed).

The head boundary is enforced inside the library itself
(``_check_copy`` gates every user-facing string); this wiring
adds no copy of its own beyond the module's notes.

Stdlib + the v569 track module + the Wave 7 quiet-hours accessor
only. No network, no subprocess, no execution of anything, no
diagnosis, no interpretation.
"""

from __future__ import annotations


def health_tool(args, base=None, fhir_path=None,
                quiet_store_path=None) -> dict:
    """Funnel handler for the ``health`` tool."""
    from . import health as _h

    args = args or {}
    action = str(args.get("action", "") or "").strip().lower()

    if action == "extract":
        text = str(args.get("text", "") or "")
        parsed = _h.parse_health_record(text)
        lines = []
        for record in parsed.get("records", []):
            try:
                lines.append(_h.render_record(record))
            except Exception:
                continue
        return {"ok": True, "action": "extract",
                "records": parsed.get("records", []),
                "rendered": lines,
                "skipped": parsed.get("skipped", 0),
                "note": parsed.get("note", "")}

    if action == "record":
        date = str(args.get("date", "") or "").strip()
        metric = str(args.get("metric", "") or "").strip()
        unit = str(args.get("unit", "") or "").strip()
        try:
            value = float(args.get("value"))
        except (TypeError, ValueError):
            return {"ok": False, "action": "record",
                    "error": "A record needs a numeric value."}
        if not date or not metric:
            return {"ok": False, "action": "record",
                    "error": "A record needs a date and a metric."}
        store = _h.FhirStore(path=fhir_path)
        result = store.add({
            "resourceType": "Observation",
            "id": f"{date}|{metric}",
            "date": date, "metric": metric,
            "value": value, "unit": unit,
        })
        return dict(result, action="record")

    if action == "summary":
        store = _h.FhirStore(path=fhir_path)
        lines = []
        for record in store.list():
            if all(k in record for k in
                   ("date", "metric", "value", "unit")):
                try:
                    lines.append(_h.render_record(record))
                except Exception:
                    continue
        return {"ok": True, "action": "summary",
                "count": len(lines), "records": lines,
                "note": _h._check_copy(
                    f"{len(lines)} health records stored.")}

    if action == "remind":
        from .. import w7_wiring as _w7  # Phase 3 H1: target stays flat in core/
        quiet = _w7.quiet_hours(quiet_store_path)
        return dict(_h.desk_reminder(quiet_hours=quiet),
                     action="remind")

    return {"ok": False, "action": action,
            "error": "Unknown action — use extract, record, summary, "
                     "or remind."}
