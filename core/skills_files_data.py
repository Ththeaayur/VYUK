"""
Files & Media IV skills pack for VYUK — the read-only data
inspection jobs the earlier packs left on the table: previewing,
querying and summarising CSV files, a text outline of a folder's
shape, the tail end of a log or text file, and a key-by-key diff
of two JSON documents.

Same rules as the packs before it (core/skills_files_inspect.py,
core/skills_files_media_ops.py, core/skills_files_recipes.py): plain functions
returning human-readable strings, never raising; this pack is
stdlib-only, so it imports cleanly on any OS with nothing to
guard. Every read is capped (rows stored, lines returned, nodes
listed, differences reported, characters shown) and every cap is
disclosed in the result when it bites. Nothing here writes,
moves or deletes anything — it only looks.

CSV scope is honest: the first row is treated as the column-name
row, the delimiter is sniffed (comma, semicolon, tab or pipe —
comma when sniffing can't tell), and quoted fields with commas
or newlines inside them are handled by the csv module but shown
with their whitespace collapsed so the listing stays readable.

Deliberately NOT here: reading a whole text file is read_file,
formatting or querying ONE JSON document is json_format /
json_query, whole-file "are these different?" is compare_files,
and folder SIZES are folder_size / folder_size_map — this pack
shows contents and structure, not bytes.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import csv
import json
from collections import Counter
from pathlib import Path

from .skills_files6_helpers import (  # INTENTIONAL: facade re-exports
    _clamp_int,
    _existing_file,
    _existing_folder,
    _fmt_num,
)

from .skills_files6_csv import (  # INTENTIONAL: facade re-exports
    _CSV_CELL_CAP,
    _CSV_PREVIEW_COL_CAP,
    _CSV_ROW_CAP,
    _DELIMITER_NAMES,
    _csv_lead,
    _csv_notes,
    _csv_table,
    _find_column,
    _load_csv,
    csv_column_stats,
    csv_preview,
    csv_query,
)

from .skills_files6_files import (  # INTENTIONAL: facade re-exports
    _DIFF_CHANGE_CAP,
    _OUTLINE_NODE_CAP,
    _TAIL_BLOCK,
    _TAIL_CHAR_CAP,
    _TAIL_SCAN_BYTES,
    _TAIL_SCAN_LINES,
    _VALUE_CAP,
    _diff_into,
    _json_show,
    _json_side,
    _json_type,
    folder_outline,
    json_diff,
    tail_file,
)


def register_all(registry) -> None:
    """Register every Files & Media IV pack tool on a ToolRegistry-like object."""
    registry.register(
        name="csv_preview",
        description="Preview a CSV file's first rows as an aligned listing. Reports the row and column counts, sniffs the delimiter (comma, semicolon, tab or pipe), treats the first row as column names, and shows up to 20 rows. For rows matching a value use csv_query; for a column summary use csv_column_stats.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the CSV file"},
                "rows": {"type": "integer", "description": "How many data rows to show, 1–20 (default 5)"},
            },
            "required": ["path"],
        },
        handler=csv_preview,
    )
    registry.register(
        name="csv_query",
        description="Find the rows in a CSV file where one column equals a value. Matching trims spaces and ignores capitalisation; pass match='contains' to match cells that merely contain the value instead of equalling it. Matching rows are shown as an aligned listing (up to 50) with the total match count, and a column name that doesn't exist gets the real column names back. To just look at a file, csv_preview is quicker.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the CSV file"},
                "column": {"type": "string", "description": "Name of the column to match on"},
                "value": {"type": "string", "description": "The value the column must equal"},
                "limit": {"type": "integer", "description": "Max matching rows to show, 1–50 (default 10)"},
                "match": {"type": "string", "enum": ["equals", "contains"], "description": "'equals' (default): the whole cell must match. 'contains': the cell only needs to contain the value"},
            },
            "required": ["path", "column", "value"],
        },
        handler=csv_query,
    )
    registry.register(
        name="csv_column_stats",
        description="Summarise one column of a CSV file in a sentence or two. If every value in the column is a number it reports the count, minimum, maximum, mean, median and total; otherwise it lists the 5 most common values with their counts. Blank cells are counted and reported either way.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the CSV file"},
                "column": {"type": "string", "description": "Name of the column to summarise"},
            },
            "required": ["path", "column"],
        },
        handler=csv_column_stats,
    )
    registry.register(
        name="folder_outline",
        description="Show a folder's shape as an indented text tree, folders first. Every folder is labelled with how many files sit directly inside it, entries are capped at 200 by default (max_entries raises the cap, up to 2000) with the cut disclosed, and folders at the depth limit (1–4 levels) are counted rather than opened. For folder SIZES use folder_size instead — this one shows structure.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the folder"},
                "depth": {"type": "integer", "description": "How many levels of subfolders to open, 1–4 (default 2)"},
                "max_entries": {"type": "integer", "description": "Max entries to list before stopping, 10–2000 (default 200)"},
            },
            "required": ["path"],
        },
        handler=folder_outline,
    )
    registry.register(
        name="tail_file",
        description="Show the last lines of a text file (up to 200), read backwards from the end so even a huge log opens instantly. Says plainly whether that's the whole file or just its tail, and refuses files that look binary. Pass contains='text' to show only the lines containing that text (case-insensitive; how much of the file was searched is stated, and the scan is capped). To read a whole small file from the top, read_file is the tool.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the text file"},
                "lines": {"type": "integer", "description": "How many lines from the end, 1–200 (default 20)"},
                "contains": {"type": "string", "description": "Optional: only show lines containing this text (case-insensitive)"},
            },
            "required": ["path"],
        },
        handler=tail_file,
    )
    registry.register(
        name="json_diff",
        description="Compare two JSON documents key by key and list what was added, removed or changed, with counts of each kind. Each side can be a file path (path_a / path_b) or JSON text given directly (json_a / json_b — inline wins if both are given); nested objects are reported with dotted paths like 'user.address.city' and arrays are compared by index. Identical content is reported as identical. json_format tidies one document; this one compares two.",
        parameters={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Full path of the first JSON file (side A)"},
                "path_b": {"type": "string", "description": "Full path of the second JSON file (side B)"},
                "json_a": {"type": "string", "description": "Side A as JSON text instead of a file"},
                "json_b": {"type": "string", "description": "Side B as JSON text instead of a file"},
            },
            "required": [],
        },
        handler=json_diff,
    )
