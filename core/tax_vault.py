"""MN-7.13 Tax-season vault (v5.66, Money edition track, leaf 4).

FY-organised storage + LOCAL parsing ONLY for AIS / 26AS / 16 /
P&L documents, discrepancy FLAGGING, and a one-shot CA handoff
zip. Organisation, NOT tax computation, advice, or filing: this
module never computes a tax liability, never advises on one, and
never files anything.

HARD BOUNDARIES, enforced in code and pinned by
~/workspace/code-lab/test_v566_leaf4_tax.py:

  * LOCAL ONLY — the module imports no network library at all
    (no socket/urllib/requests/http client, no subprocess, no
    logging). Every path argument must be a local filesystem
    path; a URL is rejected with ValueError. The vault operates
    on caller-supplied paths and never touches the tree's
    ``data/`` directory on its own.
  * PAN LOCAL ONLY — PAN-bearing files are copied and zipped
    only as local files. No function here logs, prints, or
    returns a PAN: every returned string passes through
    ``redact_pan`` (a PAN pattern becomes "[PAN-REDACTED]"),
    and the CA-handoff manifest is redacted the same way. The
    zip payload itself is the user's own files, handed locally
    to their CA — it is never uploaded anywhere by this module
    (there is no upload code path at all).
  * PORTAL LOGIN REFUSED BY DESIGN — there is no login
    function in this module. ``refuse_portal_login`` returns a
    refusal for any portal name, including incometax and GST,
    and it takes no credentials: this assistant NEVER logs into
    incometax/GST portals with stored credentials.

Standard library only at import time. No new tool declarations.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import zipfile

__all__ = [
    "SUPPORTED_KINDS",
    "PAN_REDACTED",
    "redact_pan",
    "fy_for_date",
    "parse_document_file",
    "organise_vault",
    "flag_vault_discrepancies",
    "create_ca_handoff",
    "refuse_portal_login",
]

SUPPORTED_KINDS = ("AIS", "26AS", "16", "P&L")

# A PAN pattern, used ONLY to redact — never to collect or log.
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
PAN_REDACTED = "[PAN-REDACTED]"

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def redact_pan(text) -> str:
    """Redact every PAN-shaped run from text. Non-strings pass
    through untouched; None becomes ""."""
    if text is None:
        return ""
    if not isinstance(text, str):
        return text
    return PAN_RE.sub(PAN_REDACTED, text)


def _require_local_path(path) -> str:
    """Reject URLs outright: the vault is local-only by shape,
    not by convention. Returns the path as a string."""
    text = str(path or "")
    if not text or _URL_RE.match(text.strip()):
        raise ValueError(
            "tax vault is local-only: a local filesystem path is "
            "required, never a URL.")
    return text


def _normalise_kind(kind: str) -> str:
    text = str(kind or "").strip().upper()
    if text == "PL":
        text = "P&L"
    return text


def fy_for_date(value) -> str | None:
    """Indian financial year for a YYYY-MM-DD (or YYYY/MM/DD)
    date, or a datetime/date object: April–March, labelled
    "FY2025-26" style. Returns None for an unparseable value."""
    year = month = None
    if isinstance(value, (datetime.datetime, datetime.date)):
        year, month = value.year, value.month
    else:
        match = re.match(r"^\s*(\d{4})[-/](\d{1,2})[-/]\d{1,2}", str(value or ""))
        if match:
            year, month = int(match.group(1)), int(match.group(2))
    if year is None or month is None or not 1 <= month <= 12:
        return None
    if month >= 4:
        return f"FY{year}-{str(year + 1)[-2:]}"
    return f"FY{year - 1}-{str(year)[-2:]}"


def _parse_amount(raw):
    """Parse an amount string/number; Indian formats tolerated
    (commas, Rs/₹ prefixes). Returns None when unparseable."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    text = re.sub(r"^(Rs\.?|₹)\s*", "", text)
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _rows_from_text(text: str, suffix: str) -> list[dict]:
    """Local parsing of JSON / CSV / TSV / plain-text rows into
    {label, section, amount, stated_total} dicts. Unknown lines
    become rows with no amount (flagged missing_amount by the
    caller), never silently dropped."""
    rows: list[dict] = []
    stripped = text.strip()
    if suffix == ".json":
        try:
            data = json.loads(stripped) if stripped else []
        except json.JSONDecodeError:
            return [{"label": "", "section": "default", "amount": None,
                     "_unparseable": True}]
        if isinstance(data, dict):
            data = data.get("rows") or data.get("entries") or []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                rows.append({
                    "label": str(item.get("label", "")).strip(),
                    "section": str(item.get("section", "default")).strip() or "default",
                    "amount": item.get("amount"),
                    "stated_total": item.get("stated_total"),
                })
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append({"label": str(item[0]).strip(),
                             "section": "default", "amount": item[1],
                             "stated_total": None})
        return rows
    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        reader = csv.reader(text.splitlines(), delimiter=delimiter)
        lines = [r for r in reader if any(c.strip() for c in r)]
        if not lines:
            return rows
        header = [c.strip().lower() for c in lines[0]]
        has_header = "amount" in header
        body = lines[1:] if has_header else lines
        label_i = header.index("label") if "label" in header else 0
        amount_i = header.index("amount") if "amount" in header else 1
        section_i = header.index("section") if "section" in header else None
        stated_i = header.index("stated_total") if "stated_total" in header else None
        for parts in body:
            def _at(i):
                return parts[i].strip() if i is not None and i < len(parts) else ""
            rows.append({"label": _at(label_i),
                         "section": _at(section_i) or "default",
                         "amount": _at(amount_i),
                         "stated_total": _at(stated_i) or None})
        return rows
    # Plain text: "Label, 12345" / "Label: 12345" / "Label  12345".
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(.*?)[\s,:;]+(-?[\d,]+(?:\.\d+)?)\s*$", line)
        if match:
            rows.append({"label": match.group(1).strip(),
                         "section": "default",
                         "amount": match.group(2), "stated_total": None})
        else:
            rows.append({"label": line, "section": "default",
                         "amount": None, "stated_total": None})
    return rows


def parse_document_file(path, kind: str) -> dict:
    """Parse ONE local document file (AIS / 26AS / 16 / P&L).
    Returns {kind, row_count, total, sections, flags,
    pan_found}. ``pan_found`` is a boolean ONLY — the PAN itself
    is never returned. Flags are discrepancy flags (see
    organise_vault's docstring for the set); nothing here
    computes tax, advises, or files."""
    path = _require_local_path(path)
    norm_kind = _normalise_kind(kind)
    result = {"kind": norm_kind, "row_count": 0, "total": 0.0,
              "sections": {}, "flags": [], "pan_found": False,
              "source": redact_pan(os.path.basename(path))}
    if norm_kind not in SUPPORTED_KINDS:
        result["flags"].append({
            "flag": "unknown_kind",
            "detail": ("unsupported document kind "
                       f"{redact_pan(norm_kind)!r}; supported: "
                       "AIS, 26AS, 16, P&L.")})
        return result
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        result["flags"].append({
            "flag": "unreadable_file",
            "detail": f"could not read {result['source']}: {exc}."})
        return result
    if PAN_RE.search(text):
        result["pan_found"] = True
        result["flags"].append({
            "flag": "pan_present",
            "detail": ("this document contains a PAN pattern; the "
                       "PAN itself is redacted from this report and "
                       "the file stays local-only.")})
    suffix = os.path.splitext(path)[1].lower()
    rows = _rows_from_text(text, suffix)
    seen: set = set()
    section_sums: dict = {}
    section_stated: dict = {}
    for row in rows:
        result["row_count"] += 1
        label = str(row.get("label", "")).strip()
        section = str(row.get("section", "default")).strip() or "default"
        amount = _parse_amount(row.get("amount"))
        if amount is None:
            result["flags"].append({
                "flag": "missing_amount",
                "detail": f"row {redact_pan(label)!r} has no parseable amount."})
            continue
        key = (label.lower(), amount)
        if key in seen:
            result["flags"].append({
                "flag": "duplicate_row",
                "detail": f"duplicate row {redact_pan(label)!r} amount {amount}."})
        seen.add(key)
        section_sums[section] = section_sums.get(section, 0.0) + amount
        result["total"] += amount
        if row.get("stated_total") is not None:
            stated = _parse_amount(row["stated_total"])
            if stated is not None:
                section_stated[section] = stated
    result["sections"] = {s: round(v, 2) for s, v in section_sums.items()}
    result["total"] = round(result["total"], 2)
    for section, stated in section_stated.items():
        summed = round(section_sums.get(section, 0.0), 2)
        if abs(summed - round(stated, 2)) > 0.01:
            result["flags"].append({
                "flag": "total_mismatch",
                "detail": f"section {section!r} sums to {summed} but "
                          f"states {round(stated, 2)} — flagged, not adjusted."})
    return result


def organise_vault(entries, vault_root) -> dict:
    """Organise caller-supplied local files into
    ``vault_root/<FY>/<KIND>/`` copies and parse each one
    locally. ``entries`` are dicts {path, kind, fy? , date?}:
    an explicit ``fy`` wins, else ``fy_for_date(date)``, else
    the file lands in "FY-UNKNOWN" with a "missing_fy" flag.
    Returns {vault_root, files: [...], flags: [...]} where each
    file entry carries kind, fy, vault_path, and its parse
    result — every returned string PAN-redacted. Name clashes
    inside one FY/KIND folder get a deterministic "_2" suffix.
    This copies files locally; it never uploads, never files,
    never computes tax."""
    root = _require_local_path(vault_root)
    os.makedirs(root, exist_ok=True)
    files: list[dict] = []
    flags: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        source = _require_local_path(entry.get("path"))
        kind = _normalise_kind(entry.get("kind"))
        fy = entry.get("fy") or fy_for_date(entry.get("date")) or "FY-UNKNOWN"
        parsed = parse_document_file(source, kind)
        if fy == "FY-UNKNOWN":
            parsed["flags"].append({
                "flag": "missing_fy",
                "detail": ("no FY or parseable date supplied; filed "
                           "under FY-UNKNOWN for the user to sort.")})
        dest_dir = os.path.join(root, fy, kind)
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(source)
        dest = os.path.join(dest_dir, base)
        counter = 2
        while os.path.exists(dest):
            stem, ext = os.path.splitext(base)
            dest = os.path.join(dest_dir, f"{stem}_{counter}{ext}")
            counter += 1
        shutil.copyfile(source, dest)
        record = {"kind": kind, "fy": fy,
                  "source": redact_pan(base),
                  "vault_path": redact_pan(dest),
                  "parsed": parsed}
        files.append(record)
        for flag in parsed["flags"]:
            flags.append({"fy": fy, "kind": kind,
                          "source": redact_pan(base), **flag})
    return {"vault_root": redact_pan(root), "files": files, "flags": flags}


def flag_vault_discrepancies(vault_result: dict) -> list[dict]:
    """Cross-document discrepancy FLAGGING only, within one FY:
    the same section totalling differently across documents in
    the same FY is flagged for the user to reconcile. Never
    computes tax, never advises, never files."""
    by_key: dict = {}
    for record in (vault_result or {}).get("files", []):
        parsed = record.get("parsed") or {}
        for section, total in (parsed.get("sections") or {}).items():
            by_key.setdefault((record.get("fy"), section), []).append(
                (record.get("kind"), round(float(total), 2)))
    flags = []
    for (fy, section), entries in by_key.items():
        totals = {t for _, t in entries}
        if len(totals) > 1:
            flags.append({
                "flag": "cross_document_mismatch",
                "detail": f"FY {fy} section {section!r} differs across "
                          "documents: "
                          + ", ".join(f"{k}={t}" for k, t in entries)
                          + " — flagged for the user to reconcile."})
    return flags


def create_ca_handoff(vault_root, fy: str, out_zip, overwrite: bool = False) -> dict:
    """One-shot CA handoff: zip ONE financial year's vault
    folder plus a redacted manifest.json into a LOCAL zip file
    the user hands to their CA themselves. Default
    ``overwrite=False`` makes it one-shot: an existing zip is
    refused, never silently replaced. No upload, no portal, no
    filing — the zip is a local file and nothing here sends it
    anywhere."""
    root = _require_local_path(vault_root)
    out = _require_local_path(out_zip)
    fy_dir = os.path.join(root, fy)
    if not os.path.isdir(fy_dir):
        return {"ok": False, "refused": True,
                "detail": f"no vault folder for {redact_pan(fy)}; nothing zipped."}
    if os.path.exists(out) and not overwrite:
        return {"ok": False, "refused": True,
                "detail": ("a handoff zip already exists at that path; "
                           "one-shot handoff refuses to overwrite it — "
                           "choose a new path or pass overwrite=True.")}
    names: list[str] = []
    manifest = {"fy": redact_pan(fy),
                "note": ("local CA handoff only: this zip is handed "
                         "to the user's CA by the user. It is never "
                         "uploaded, filed, or sent by the assistant."),
                "files": []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _dirnames, filenames in os.walk(fy_dir):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                arc = os.path.relpath(full, root)
                archive.write(full, arc)
                digest = hashlib.sha256()
                with open(full, "rb") as handle:
                    digest.update(handle.read())
                manifest["files"].append(
                    {"path": redact_pan(arc),
                     "sha256": digest.hexdigest()})
                names.append(redact_pan(arc))
        archive.writestr("manifest.json",
                         json.dumps(manifest, indent=2))
    return {"ok": True, "zip": redact_pan(out), "fy": redact_pan(fy),
            "files": names,
            "detail": ("CA handoff zip created locally. Hand it to "
                       "your CA yourself; nothing was uploaded or filed.")}


def refuse_portal_login(portal: str) -> dict:
    """Portal login is REFUSED by design, for ANY portal name.
    This function takes NO credentials and there is no login
    function in this module: this assistant NEVER logs into
    incometax or GST portals with stored credentials. Sign in
    yourself in your own browser; the vault only organises
    documents you provide."""
    name = str(portal or "").strip() or "tax portal"
    return {
        "refused": True,
        "reason": (f"portal login to {redact_pan(name)} is refused by "
                   "design: this assistant never logs into "
                   "incometax or GST portals with stored "
                   "credentials, and holds no portal login path. "
                   "Sign in yourself in your own browser; the "
                   "vault organises documents you provide, "
                   "nothing more."),
    }
