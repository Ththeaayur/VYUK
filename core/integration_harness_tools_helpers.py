"""Wave 6 integration tools — integration_harness_tools_helpers (split B11).

JSON helpers + handler wrapper + normaliser (shared by all parts).
"""

from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it; annotations stay lazy.

import json
import re


def _parse_json(value, what):
    if isinstance(value, str):
        try:
            return json.loads(value) if value.strip() else None
        except ValueError as exc:
            raise ValueError(f"{what} is not valid JSON: {exc}")
    return value


def _parse_list(value, what):
    parsed = _parse_json(value, what)
    if not isinstance(parsed, list):
        raise ValueError(f"{what} must be a JSON list")
    return parsed


def _parse_dict(value, what):
    parsed = _parse_json(value, what)
    if not isinstance(parsed, dict):
        raise ValueError(f"{what} must be a JSON object")
    return parsed


def _handler(fn, **kwargs):
    try:
        return json.dumps(fn(**kwargs))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return json.dumps({"status": "error", "message": str(exc)})


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())
