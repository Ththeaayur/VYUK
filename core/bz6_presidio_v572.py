"""v5.72 (Business track, wave 18, BZ-6) — Presidio PII pilot
(Wave 2 feed). Lazy import, fail-closed: when presidio-analyzer
is absent, every call raises PilotUnavailable — the pilot never
pretends to scan.

Licence (verified from the PRIMARY source, wheel METADATA):
presidio-analyzer 2.2.364, License-Expression: MIT (wheel
METADATA, downloaded for verification only — NOT vendored,
NOT added to requirements, NOT shipped). MIT permits
commercial use. The pilot ships no Presidio code and no model.

What the pilot does: analyze text for PII entity types and
redact them (replaced with <TYPE> markers). It returns spans
+ types to the caller; the caller owns what happens to them.
It never logs, never stores, never sends: no network, no
persistence in this module.

Stdlib only until the lazy import. No funnel registration.
"""

from __future__ import annotations


class PilotUnavailable(RuntimeError):
    """The pilot library is absent (fail-closed, never a stub)."""


def _load_analyzer():
    try:
        from presidio_analyzer import AnalyzerEngine
    except Exception as exc:  # noqa: BLE001 — any import failure
        raise PilotUnavailable(
            "presidio-analyzer is not installed; the Presidio "
            "pilot cannot run (fail-closed, no stub results)"
            ) from exc
    return AnalyzerEngine()


def scan_pii(text: str, entities=None, analyzer=None) -> dict:
    """Scan text for PII. Returns {findings: [{type, start, end,
    score}], count}. Findings carry spans + types only — the
    caller must not log raw PII spans. ``entities`` narrows the
    recognizer set; None scans the defaults."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    engine = analyzer if analyzer is not None else _load_analyzer()
    results = engine.analyze(text=text, entities=entities, language="en")
    findings = [{"type": r.entity_type, "start": r.start, "end": r.end,
                 "score": round(float(r.score), 3)} for r in results]
    return {"findings": findings, "count": len(findings)}


def redact_pii(text: str, entities=None, analyzer=None) -> dict:
    """Redact PII spans to <TYPE> markers. Returns {redacted,
    count}. The redaction is mechanical (span replacement);
    judgment about what counts as PII stays with the recognizer
    configuration, never with a guess here."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    scan = scan_pii(text, entities=entities, analyzer=analyzer)
    out = []
    cursor = 0
    for finding in sorted(scan["findings"], key=lambda f: f["start"]):
        if finding["start"] < cursor:
            continue
        out.append(text[cursor:finding["start"]])
        out.append("<%s>" % finding["type"])
        cursor = finding["end"]
    out.append(text[cursor:])
    return {"redacted": "".join(out), "count": scan["count"]}
