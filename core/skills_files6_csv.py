"""CSV tools of the Files & Media IV pack (skills_files_data).

Moved verbatim from core/skills_files_data.py in the skills split (Phase A).
"""

import csv
from collections import Counter

from .skills_files6_helpers import (  # INTENTIONAL: shared house helpers
    _clamp_int,
    _existing_file,
    _fmt_num,
)


_CSV_ROW_CAP = 20_000       # data rows loaded into memory per CSV
_CSV_PREVIEW_COL_CAP = 12   # columns shown in an aligned listing
_CSV_CELL_CAP = 30          # characters per cell in a listing

# ---------------------------------------------------------------------------
# CSV LOADING — shared by the three CSV tools. Returns
# (headers, rows, info) or an honest error string. Rows are
# normalised to the header width (padded with "" / truncated) and
# the normalisation is counted in info["ragged"].
# ---------------------------------------------------------------------------

_DELIMITER_NAMES = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}

def _load_csv(path: str):
    """Read a CSV file once; return (headers, rows, info) or an error."""
    target = _existing_file(path, "CSV file")
    if isinstance(target, str):
        return target
    try:
        handle = open(target, "r", encoding="utf-8-sig",
                      errors="replace", newline="")
    except OSError as e:
        return f"Couldn't open '{target.name}': {e}"
    with handle:
        try:
            sample = handle.read(8192)
            handle.seek(0)
        except OSError as e:
            return f"Couldn't read '{target.name}': {e}"
        if not sample.strip():
            return f"'{target.name}' is empty — there's no CSV data in it."
        try:
            delimiter = csv.Sniffer().sniff(
                sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            headers = next(reader)
        except StopIteration:
            return f"'{target.name}' is empty — there's no CSV data in it."
        except csv.Error as e:
            return f"'{target.name}' couldn't be parsed as CSV: {e}"
        headers = [str(h).strip() for h in headers]
        width = len(headers)
        if width == 0:
            return f"'{target.name}' has no header row to read."
        rows: list[list[str]] = []
        total = 0
        ragged = 0
        try:
            for raw in reader:
                if not raw:
                    continue            # blank line — not a data row
                total += 1
                if len(raw) != width:
                    ragged += 1
                if len(rows) < _CSV_ROW_CAP:
                    rows.append(
                        (list(raw) + [""] * width)[:width])
        except csv.Error as e:
            return f"'{target.name}' couldn't be parsed as CSV: {e}"
    info = {
        "name": target.name,
        "delimiter": delimiter,
        "total": total,
        "ragged": ragged,
        "truncated": total > len(rows),
    }
    return headers, rows, info

def _csv_lead(info: dict, width: int) -> str:
    """The shared '"name": N rows × M columns' header line."""
    delim = _DELIMITER_NAMES.get(info["delimiter"], info["delimiter"])
    return (f"\"{info['name']}\": {info['total']} rows × {width} "
            f"columns (delimiter: {delim})")

def _csv_notes(info: dict) -> list[str]:
    """Disclosure lines for whatever the loader had to smooth over."""
    notes = []
    if info["ragged"]:
        notes.append(
            f"Note: {info['ragged']} row(s) don't have exactly the "
            "header's column count — short rows were padded with "
            "blanks and long ones cut to fit.")
    if info["truncated"]:
        notes.append(
            f"Note: only the first {_CSV_ROW_CAP} rows were loaded "
            f"(the file has {info['total']}) — results cover those.")
    return notes

def _csv_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """An aligned text listing of rows under their headers."""
    shown_cols = min(len(headers), _CSV_PREVIEW_COL_CAP)

    def cell(text: str) -> str:
        flat = " ".join(str(text).split())
        if len(flat) > _CSV_CELL_CAP:
            flat = flat[:_CSV_CELL_CAP - 1] + "…"
        return flat

    head = [cell(h) for h in headers[:shown_cols]]
    body = [[cell(c) for c in row[:shown_cols]] for row in rows]
    widths = [len(h) for h in head]
    for row in body:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(head)).rstrip(),
             "  ".join("-" * w for w in widths)]
    for row in body:
        lines.append(
            "  ".join(v.ljust(widths[i]) for i, v in enumerate(row)).rstrip())
    if len(headers) > shown_cols:
        lines.append(
            f"(+{len(headers) - shown_cols} more column(s) not shown — "
            f"the listing caps at {_CSV_PREVIEW_COL_CAP} columns.)")
    return lines

def _find_column(headers: list[str], column: str):
    """(index, None) or (None, honest error) — trimmed, case-insensitive."""
    wanted = str(column or "").strip().lower()
    if not wanted:
        return None, ("That needs the name of a column to look at. "
                      f"The columns are: {', '.join(headers)}")
    for index, header in enumerate(headers):
        if header.strip().lower() == wanted:
            return index, None
    return None, (f"There's no column called '{column}' here. "
                  f"The columns are: {', '.join(headers)}")

# ---------------------------------------------------------------------------
# CSV PREVIEW — the first rows, aligned
# ---------------------------------------------------------------------------

def csv_preview(path: str, rows: int = 5) -> str:
    """Show the first rows of a CSV as an aligned listing."""
    try:
        loaded = _load_csv(path)
        if isinstance(loaded, str):
            return loaded
        headers, data, info = loaded
        count = _clamp_int(rows, 1, 20, 5)
        lines = [_csv_lead(info, len(headers))]
        if not data:
            lines.append("(The file has a header row but no data rows.)")
        else:
            lines += _csv_table(headers, data[:count])
            if len(data) > count:
                lines.append(
                    f"Showing the first {count} of {info['total']} "
                    "row(s).")
        lines += _csv_notes(info)
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Previewing that CSV failed: {e}"

# ---------------------------------------------------------------------------
# CSV QUERY — the rows where one column equals a value
# ---------------------------------------------------------------------------

def csv_query(path: str, column: str, value: str, limit: int = 10,
              match: str = "equals") -> str:
    """Show the rows where a column equals (or contains) a value."""
    try:
        loaded = _load_csv(path)
        if isinstance(loaded, str):
            return loaded
        headers, data, info = loaded
        index, err = _find_column(headers, column)
        if err:
            return err
        mode = str(match or "equals").strip().lower()
        if mode not in ("equals", "contains"):
            mode = "equals"
        wanted = str(value or "").strip().lower()
        if mode == "contains":
            matches = [row for row in data
                       if wanted in row[index].strip().lower()]
        else:
            matches = [row for row in data
                       if row[index].strip().lower() == wanted]
        relation = (f"containing '{value}'" if mode == "contains"
                    else f"= '{value}'")
        header_name = headers[index]
        if not matches:
            line = (f"No rows in \"{info['name']}\" have "
                    f"{header_name} {relation}.")
            distinct = []
            for row in data:
                cell = row[index].strip()
                if cell and cell not in distinct:
                    distinct.append(cell)
                if len(distinct) >= 5:
                    break
            if distinct:
                line += (" The column does hold values like: "
                         + ", ".join(distinct) + ".")
            notes = _csv_notes(info)
            return "\n".join([line] + notes)
        cap = _clamp_int(limit, 1, 50, 10)
        lines = [f"{len(matches)} row(s) in \"{info['name']}\" have "
                 f"{header_name} {relation}:"]
        lines += _csv_table(headers, matches[:cap])
        if len(matches) > cap:
            lines.append(
                f"Showing the first {cap} of {len(matches)} "
                "matching row(s).")
        lines += _csv_notes(info)
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Querying that CSV failed: {e}"

# ---------------------------------------------------------------------------
# CSV COLUMN STATS — numbers summarised, words counted
# ---------------------------------------------------------------------------

def csv_column_stats(path: str, column: str) -> str:
    """Summarise one CSV column: numeric stats or top values."""
    try:
        loaded = _load_csv(path)
        if isinstance(loaded, str):
            return loaded
        headers, data, info = loaded
        index, err = _find_column(headers, column)
        if err:
            return err
        header_name = headers[index]
        values = [row[index].strip() for row in data]
        missing = sum(1 for v in values if not v)
        present = [v for v in values if v]
        if not present:
            return (f"Every value in the '{header_name}' column of "
                    f"\"{info['name']}\" is blank — there's nothing "
                    "to summarise.")
        numbers: list[float] = []
        all_numeric = True
        for value in present:
            try:
                numbers.append(float(value))
            except ValueError:
                all_numeric = False
                break
        lines = []
        if all_numeric:
            mean = sum(numbers) / len(numbers)
            ordered = sorted(numbers)
            mid = len(ordered) // 2
            median = (ordered[mid] if len(ordered) % 2
                      else (ordered[mid - 1] + ordered[mid]) / 2)
            lines.append(
                f"The '{header_name}' column of \"{info['name']}\" "
                f"is numeric — {len(numbers)} value(s): minimum "
                f"{_fmt_num(min(numbers))}, maximum "
                f"{_fmt_num(max(numbers))}, mean {_fmt_num(mean)}, "
                f"median {_fmt_num(median)}, "
                f"total {_fmt_num(sum(numbers))}.")
        else:
            counts = Counter(present)
            top = counts.most_common(5)
            listing = "; ".join(
                f"'{value}' — {n} time(s)" for value, n in top)
            lines.append(
                f"The '{header_name}' column of \"{info['name']}\" "
                f"holds text — {len(counts)} different value(s) "
                f"across {len(present)} row(s). Most common: "
                f"{listing}.")
            if len(counts) > 5:
                lines.append(
                    f"(Only the top 5 of {len(counts)} different "
                    "values are listed.)")
        if missing:
            lines.append(
                f"{missing} of {len(values)} row(s) leave this "
                "column blank.")
        lines += _csv_notes(info)
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Summarising that column failed: {e}"
