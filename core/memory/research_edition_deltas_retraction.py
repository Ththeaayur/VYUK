"""research_edition_deltas_retraction — part of the research_edition_deltas split (Phase B B10).

Moved verbatim from core/memory/research_edition_deltas.py; see the
facade there for the re-export map.
"""
import csv
import io
import re

# ---------------------------------------------------------------------------
# RS19-C1+G9 — Retraction Watch, LOCAL form, dated + field-exact
# ---------------------------------------------------------------------------
# Wave 6's retraction_check_local scans lines by substring. The
# RS19 delta is a TYPED record parse: the Retraction Watch CSV's
# own columns, a parsed RetractionDate, and a FIELD-EXACT DOI
# match. Substring matching false-positives (DOI "10.1000/abc"
# matching a line for "10.1000/abcdef") — the typed lookup does
# not. Unknown/missing columns degrade to "" / None, never crash.



RETRACTION_CSV_COLUMNS = (
    "Record ID", "Title", "Author", "Journal", "Publisher",
    "ArticleType", "RetractionDate", "RetractionDOI",
    "RetractionNature", "Reason", "OriginalPaperDOI",
)


def parse_retraction_csv(text):
    """Parse Retraction Watch CSV text into typed records.

    Each record: doi (OriginalPaperDOI, lowercased), title,
    journal, retraction_date (ISO YYYY-MM-DD or "" when
    unparseable), retraction_doi, nature, reason. Rows without
    an OriginalPaperDOI are skipped — a record that cannot be
    keyed by DOI cannot support a warn-at-cite decision."""
    records = []
    reader = csv.DictReader(io.StringIO(str(text or "")))
    if reader.fieldnames is None:
        return records
    for row in reader:
        doi = (row.get("OriginalPaperDOI") or "").strip().lower()
        if not doi:
            continue
        raw_date = (row.get("RetractionDate") or "").strip()
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw_date)
        date = match.group(0) if match else ""
        records.append({
            "doi": doi,
            "record_id": (row.get("Record ID") or "").strip(),
            "title": (row.get("Title") or "").strip(),
            "journal": (row.get("Journal") or "").strip(),
            "retraction_date": date,
            "retraction_doi": (row.get("RetractionDOI") or "").strip(),
            "nature": (row.get("RetractionNature") or "").strip(),
            "reason": (row.get("Reason") or "").strip(),
        })
    return records


def lookup_retraction_dated(doi, csv_text):
    """Field-exact dated lookup. A match counts as a retraction
    ONLY when its nature names a retraction — a Correction or
    Expression of Concern match is an established NON-retraction
    (warn False, nature named), never a warning. No match is a
    statement about THIS file only — never a claim of 'not
    retracted'."""
    needle = (doi or "").strip().lower()
    if not needle:
        return {"doi": doi, "retracted": None,
                "basis": "no DOI given", "warn": False}
    for record in parse_retraction_csv(csv_text):
        if record["doi"] == needle:
            if "retract" in record["nature"].lower():
                return {"doi": doi, "retracted": True,
                        "basis": "local Retraction Watch CSV "
                                 "(field-exact DOI match)",
                        "warn": True,
                        "retraction_date": record["retraction_date"],
                        "nature": record["nature"],
                        "reason": record["reason"],
                        "notice": "https://retractionwatch.com/"}
            nature = record["nature"] or "non-retraction notice"
            return {"doi": doi, "retracted": False,
                    "basis": "local Retraction Watch CSV match "
                             f"is a {nature}, not a retraction",
                    "warn": False,
                    "nature": record["nature"]}
    return {"doi": doi, "retracted": False,
            "basis": "local Retraction Watch CSV (no field-exact "
                     "match)",
            "warn": False}


def parse_crossmark_assertions(assertions):
    """Classify Crossref Crossmark assertion strings. Wave 6's
    crossmark() returns the raw list; this types each entry so
    warn-at-cite fires ONLY on a retraction assertion — a
    correction or update is not a retraction."""
    typed = []
    for item in assertions or []:
        text = str(item)
        lowered = text.lower()
        if "retract" in lowered:
            kind = "retraction"
        elif "expression of concern" in lowered or "concern" in lowered:
            kind = "expression_of_concern"
        elif "correct" in lowered:
            kind = "correction"
        elif "update" in lowered:
            kind = "update"
        else:
            kind = "other"
        typed.append({"assertion": text, "kind": kind})
    return typed


def warn_at_cite_typed(doi, crossref_assertions=(), csv_text=""):
    """Warn-at-cite-time on TYPED evidence only: a field-exact
    local CSV hit, or a classified retraction assertion.
    Confirm-before-warn; reason + notice link ALWAYS accompany
    a warning. Anything less than established evidence reports
    the uncertainty and warns about nothing."""
    local = lookup_retraction_dated(doi, csv_text) \
        if csv_text else {"retracted": None, "warn": False,
                          "basis": "local watch absent"}
    typed = parse_crossmark_assertions(crossref_assertions)
    asserted = [entry for entry in typed
                if entry["kind"] == "retraction"]
    if local.get("retracted") is True or asserted:
        reasons = []
        if local.get("retracted") is True:
            reasons.append(
                f"{local['basis']} (dated "
                f"{local.get('retraction_date') or 'undated'})")
        if asserted:
            reasons.append("Crossref Crossmark retraction assertion")
        return {"doi": doi, "warn": True,
                "reason": "; ".join(reasons),
                "notice": "https://retractionwatch.com/",
                "confirm_before_warn": True}
    return {"doi": doi, "warn": False,
            "reason": "no established retraction (local watch: "
                      f"{local['basis']})",
            "notice": ""}
