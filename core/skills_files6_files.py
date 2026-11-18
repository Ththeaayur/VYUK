"""Folder-outline, tail-file and JSON-diff tools of the Files & Media IV
pack (skills_files_data).

Moved verbatim from core/skills_files_data.py in the skills split (Phase A).
"""

import json
from pathlib import Path

from .skills_files6_helpers import (  # INTENTIONAL: shared house helpers
    _clamp_int,
    _existing_file,
    _existing_folder,
)


_TAIL_BLOCK = 8192          # bytes per backwards read in tail_file
_TAIL_CHAR_CAP = 6000       # characters of tail text returned
_TAIL_SCAN_LINES = 5000     # lines read while hunting filter matches
_TAIL_SCAN_BYTES = 1024 * 1024  # bytes read while hunting matches
_OUTLINE_NODE_CAP = 200     # entries listed by folder_outline
_DIFF_CHANGE_CAP = 50       # differences listed by json_diff
_VALUE_CAP = 60             # characters per value shown in a diff

# ---------------------------------------------------------------------------
# FOLDER OUTLINE — the folder's shape as an indented text tree
# ---------------------------------------------------------------------------

def folder_outline(path: str, depth: int = 2,
                   max_entries: int = 200) -> str:
    """Show a folder's shape as an indented tree with file counts."""
    try:
        root = _existing_folder(path, "folder")
        if isinstance(root, str):
            return root
        max_depth = _clamp_int(depth, 1, 4, 2)
        node_cap = _clamp_int(max_entries, 10, 2000, _OUTLINE_NODE_CAP)
        state = {"nodes": 0, "capped": False, "unopened": 0}

        def direct_file_count(folder: Path) -> int | None:
            try:
                return sum(1 for e in folder.iterdir() if e.is_file())
            except OSError:
                return None

        def walk(folder: Path, level: int, lines: list[str]) -> None:
            try:
                entries = list(folder.iterdir())
            except OSError as e:
                lines.append("  " * level + f"(couldn't be read: {e})")
                return
            folders = sorted(
                (e for e in entries
                 if e.is_dir() and not e.is_symlink()),
                key=lambda p: p.name.lower())
            files = sorted(
                (e for e in entries
                 if e.is_file() or e.is_symlink()),
                key=lambda p: p.name.lower())
            for sub in folders:
                if state["nodes"] >= node_cap:
                    state["capped"] = True
                    return
                state["nodes"] += 1
                count = direct_file_count(sub)
                count_text = (f" ({count} file(s))"
                              if count is not None else
                              " (couldn't be read)")
                lines.append("  " * level + f"{sub.name}/{count_text}")
                if level < max_depth:
                    walk(sub, level + 1, lines)
                elif count is not None:
                    # Has contents we are choosing not to show.
                    try:
                        if any(True for _ in sub.iterdir()):
                            state["unopened"] += 1
                    except OSError:
                        pass
            for entry in files:
                if state["nodes"] >= node_cap:
                    state["capped"] = True
                    return
                state["nodes"] += 1
                suffix = " (link)" if entry.is_symlink() else ""
                lines.append("  " * level + entry.name + suffix)

        root_count = direct_file_count(root)
        if root_count is None:
            return f"Couldn't read the folder '{root}'."
        try:
            has_any = any(True for _ in root.iterdir())
        except OSError as e:
            return f"Couldn't read the folder '{root}': {e}"
        if not has_any:
            return f"The folder '{root}' is empty."
        lines = [f"Outline of '{root}' (depth up to {max_depth}):",
                 f"{root.name}/ ({root_count} file(s))"]
        walk(root, 1, lines)
        if state["capped"]:
            lines.append(
                f"…stopped at {node_cap} entries — the "
                "outline is partial.")
        if state["unopened"]:
            lines.append(
                f"{state['unopened']} folder(s) at the depth limit "
                "weren't opened — raise depth (up to 4) to see "
                "inside them.")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Outlining that folder failed: {e}"

# ---------------------------------------------------------------------------
# TAIL FILE — the last lines of a text file, read from the end
# ---------------------------------------------------------------------------

def tail_file(path: str, lines: int = 20, contains: str = "") -> str:
    """Show the last lines of a text file, read backwards from its end."""
    try:
        target = _existing_file(path, "text file")
        if isinstance(target, str):
            return target
        count = _clamp_int(lines, 1, 200, 20)
        needle = str(contains or "").strip().lower()
        try:
            size = target.stat().st_size
        except OSError as e:
            return f"Couldn't read '{target.name}': {e}"
        if size == 0:
            return f"'{target.name}' is empty."
        try:
            handle = open(target, "rb")
        except OSError as e:
            return f"Couldn't open '{target.name}': {e}"
        chunks: list[bytes] = []
        newlines = 0
        read_bytes = 0
        offset = size
        reached_start = False
        # With a filter, keep reading backwards until the scan
        # caps bite — matching lines can be sparse in a long log.
        line_budget = _TAIL_SCAN_LINES if needle else count
        with handle:
            while offset > 0:
                step = min(_TAIL_BLOCK, offset)
                offset -= step
                handle.seek(offset)
                chunk = handle.read(step)
                chunks.append(chunk)
                read_bytes += len(chunk)
                newlines += chunk.count(b"\n")
                if offset == 0:
                    reached_start = True
                if newlines > line_budget:
                    break
                if needle and read_bytes >= _TAIL_SCAN_BYTES:
                    break
        raw = b"".join(reversed(chunks))
        if b"\x00" in raw:
            return (f"'{target.name}' looks like a binary file, not "
                    "text (it contains NUL bytes) — tail_file only "
                    "reads text files.")
        text = raw.decode("utf-8", errors="replace")
        if not reached_start:
            # The first line in the buffer is a partial line — drop it.
            cut = text.find("\n")
            text = text[cut + 1:] if cut >= 0 else ""
        found = text.splitlines()
        if needle:
            matches = [ln for ln in found if needle in ln.lower()]
            shown_matches = matches[-count:]
            body = "\n".join(shown_matches)
            cut_matches = 0
            if len(body) > _TAIL_CHAR_CAP:
                body = body[-_TAIL_CHAR_CAP:]
                cut_matches = 1
                nl = body.find("\n")
                if nl >= 0:
                    body = body[nl + 1:]
            scope = ("the whole file was searched" if reached_start
                     else f"the last {max(read_bytes // 1024, 1)} KB "
                          "were searched")
            shown_text = str(contains).strip()
            if not matches:
                return (f"No lines in '{target.name}' contain "
                        f"'{shown_text}' (case-insensitive; "
                        f"{scope}).")
            lead = (f"{len(matches)} line(s) in '{target.name}' "
                    f"contain '{shown_text}' (case-insensitive; "
                    f"{scope}) — here are the last "
                    f"{len(shown_matches)}:")
            out = [lead, body]
            if cut_matches:
                out.append(
                    f"(The oldest lines of this tail were cut so "
                    f"the result fits {_TAIL_CHAR_CAP} characters.)")
            return "\n".join(out)
        shown = found[-count:]
        body = "\n".join(shown)
        cut_chars = 0
        if len(body) > _TAIL_CHAR_CAP:
            body = body[-_TAIL_CHAR_CAP:]
            cut_chars = 1     # flag only; exact count isn't knowable post-cut
            # Don't start mid-line after the character cut.
            nl = body.find("\n")
            if nl >= 0:
                body = body[nl + 1:]
        if reached_start and len(shown) == len(found):
            lead = (f"'{target.name}' has {len(found)} line(s) — "
                    "here it is in full:")
        elif reached_start:
            lead = (f"Last {len(shown)} line(s) of '{target.name}' "
                    f"(the file has {len(found)} line(s) in total):")
        else:
            lead = (f"Last {len(shown)} line(s) of '{target.name}' "
                    "(the file has more lines above these):")
        out = [lead, body]
        if cut_chars:
            out.append(
                f"(The oldest lines of this tail were cut so the "
                f"result fits {_TAIL_CHAR_CAP} characters.)")
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Reading the end of that file failed: {e}"

# ---------------------------------------------------------------------------
# JSON DIFF — two JSON documents, key by key
# ---------------------------------------------------------------------------

def _json_side(label: str, file_path: str, inline: str):
    """Resolve one side of the diff: (value, source description) or an error."""
    if inline and str(inline).strip():
        try:
            return json.loads(str(inline)), "inline JSON"
        except json.JSONDecodeError as e:
            return (f"Side {label}'s inline JSON couldn't be parsed: "
                    f"{e.msg} (line {e.lineno}, column {e.colno}).")
    if file_path and str(file_path).strip():
        target = _existing_file(file_path, "JSON file")
        if isinstance(target, str):
            return f"Side {label}: {target}"
        try:
            text = target.read_text(encoding="utf-8-sig",
                                    errors="replace")
        except OSError as e:
            return f"Side {label}: couldn't read '{target.name}': {e}"
        try:
            return json.loads(text), f"'{target.name}'"
        except json.JSONDecodeError as e:
            return (f"Side {label} ('{target.name}') couldn't be "
                    f"parsed as JSON: {e.msg} (line {e.lineno}, "
                    f"column {e.colno}).")
    return (f"Side {label} needs either a file path "
            f"(path_{label.lower()}) or JSON text "
            f"(json_{label.lower()}).")

def _json_type(value) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return "number"
    return "string"

def _json_show(value) -> str:
    """A compact one-line rendering of a JSON value, capped."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    if len(text) > _VALUE_CAP:
        text = text[:_VALUE_CAP - 1] + "…"
    return text

def _diff_into(a, b, prefix: str, out: list[str]) -> None:
    """Append 'added/removed/changed' lines for a vs b to `out`."""
    if len(out) > _DIFF_CHANGE_CAP * 4:
        return            # plenty collected; the cap note will say so
    type_a, type_b = _json_type(a), _json_type(b)
    if type_a != type_b:
        out.append(f"changed {prefix}: {_json_show(a)} → {_json_show(b)}"
                   f" ({type_a} → {type_b})")
        return
    if isinstance(a, dict):
        for key in a:
            here = f"{prefix}.{key}" if prefix else str(key)
            if key not in b:
                out.append(f"removed {here}: {_json_show(a[key])}")
            else:
                _diff_into(a[key], b[key], here, out)
        for key in b:
            if key not in a:
                here = f"{prefix}.{key}" if prefix else str(key)
                out.append(f"added {here}: {_json_show(b[key])}")
        return
    if isinstance(a, list):
        for index in range(max(len(a), len(b))):
            here = f"{prefix}[{index}]"
            if index >= len(a):
                out.append(f"added {here}: {_json_show(b[index])}")
            elif index >= len(b):
                out.append(f"removed {here}: {_json_show(a[index])}")
            else:
                _diff_into(a[index], b[index], here, out)
        return
    if a != b:
        out.append(f"changed {prefix or '(root)'}: "
                   f"{_json_show(a)} → {_json_show(b)}")

def json_diff(path_a: str = "", path_b: str = "",
              json_a: str = "", json_b: str = "") -> str:
    """Compare two JSON documents key by key and list the differences."""
    try:
        side_a = _json_side("A", path_a, json_a)
        if isinstance(side_a, str):
            return side_a
        side_b = _json_side("B", path_b, json_b)
        if isinstance(side_b, str):
            return side_b
        value_a, source_a = side_a
        value_b, source_b = side_b
        changes: list[str] = []
        _diff_into(value_a, value_b, "", changes)
        if not changes:
            return (f"{source_a} and {source_b} are identical in "
                    "content — no added, removed or changed keys.")
        n_added = sum(1 for c in changes if c.startswith("added "))
        n_removed = sum(1 for c in changes if c.startswith("removed "))
        n_changed = len(changes) - n_added - n_removed
        lines = [f"Comparing {source_a} (A) with {source_b} (B): "
                 f"{len(changes)} difference(s) ({n_added} added, "
                 f"{n_removed} removed, {n_changed} changed)"]
        lines += [f"• {change}" for change in changes[:_DIFF_CHANGE_CAP]]
        if len(changes) > _DIFF_CHANGE_CAP:
            lines.append(
                f"…and {len(changes) - _DIFF_CHANGE_CAP} more "
                f"difference(s) not listed (the listing caps at "
                f"{_DIFF_CHANGE_CAP}).")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — a handler never raises
        return f"Comparing those JSON documents failed: {e}"
