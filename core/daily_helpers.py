from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime
import json
import os

# ===========================================================================
# Shared store plumbing (caller-provided path only)
# ===========================================================================

def _load_json(path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.loads(handle.read())
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — corrupt/missing store is empty
        return {}


def _save_json_atomic(path, data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def _parse_time(value: str) -> datetime.time | None:
    try:
        hour, minute = str(value).strip().split(":")[:2]
        return datetime.time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


