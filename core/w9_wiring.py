"""Conversion pass — Wave 9 (v5.64, "Premium Surface") wiring coordinator.

Wires the staged Wave 9 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, and WITHOUT touching the locked files (tools_handlers /
tools_gate / tools_registry) or any earlier wiring module. Every seam
here keeps the staged module's own safety shape:

* Docking is NOT wired here at all (GATED: LGPL-2.1 owner clearance
  ruling owed AND a reproducible Windows wheel owed). This module
  never imports gui/docking; the fixed-layout overview panel
  is the shipped surface.
* Earcon assets are NOT wired here: the sonic canon gate sits in
  front of the existing Earcons emission path, and the sound-pool
  quarry is consulted only through that gate. attempt_layer()
  still refuses while the earcon veto stands — this module never
  layers audio.
* Wallpaper applies NOTHING on its own: resolution is gated on an
  explicit owner opt-in AND the pilot's own status, and the pilot
  is still PILOT (Windows validation + owner decision owed).
* State mirrors are LAN-only with terms gating intact: Hue
  construction without explicit terms clearance raises, and any
  non-LAN host raises, exactly as the staged module enforces.
  No device reader is configured by this module — a reader is a
  caller-supplied LAN fetch, never invented here.
* The soundscape bed is generated + loudness-normalised here but
  has NO playback sink: the sink decision is OWED, so this module
  returns samples and never plays them.
* The chat renderer runs on the live bubble path as a parallel
  document (fallback renderer, lazy-optionals); the on-screen
  bubble keeps its existing markdown-lite / code-block widgets,
  because the v564 fallback renders task lists as literal text
  and passes raw inline HTML through, unlike the bubble's
  escaped rendering. The display swap is OWED, not silently done.
* The PF-3 delete-everywhere gap is NOT closed by this wiring:
  the Privacy Dashboard widget is live over the Wave 6 API, whose
  delete_one deletes the file record only; DB rows are rollback
  material. Per-row expiry dates are still OWED from that API.
  Wipe stays fail-closed (exact "WIPE ALL" confirm).

GUI helpers in this module import Qt lazily, inside the function
that needs them, so importing this module stays headless-safe and
creates no core->gui import cycle at module scope.
"""

from __future__ import annotations

import datetime
import logging
import math
import wave
from pathlib import Path

from . import chat_render as _chat_render
from . import conversation as _conversation
from .memory import on_this_day as _on_this_day
from . import sonic_canon as _canon
from . import sound_pools_v564 as _pools
from . import soundscape_v564 as _soundscape
from . import state_mirrors as _mirrors
from . import theme_packs as _packs
from . import theme_sync as _theme_sync
from . import tokens as _tokens
from . import wallpaper as _wallpaper

log = logging.getLogger("nova.w9_wiring")

MODULE_VERSION = "5.64.0"

# ------------------------------------------------------------------ #
# 17. Icon manifest — FEEDS live gui/icons.py, never replaces it
# ------------------------------------------------------------------ #

_icon_manifest: dict = {}
_icon_manifest_problems: list = []
_icon_manifest_validated: bool = False
# True only while the SHIPPED bundle (gui/assets/icons/phosphor/)
# is installed cleanly — a manual manifest reset clears it, and
# a corrupt shipped manifest never sets it.
_shipped_manifest_loaded: bool = False


def _shipped_manifest_path():
    """Path of the shipped Phosphor bundle manifest, if it ships."""  # B-R1-6: Lucide -> Phosphor
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "gui" / "assets" / "icons" / "phosphor" / "manifest.json")


def _load_shipped_manifest() -> bool:
    """Install the shipped bundle manifest, if the bundle ships.

    Returns True when the manifest file was found (even when some
    entries were dropped with problems — those stay reported via
    icon_manifest_status). Returns False when no bundle ships, in
    which case the caller falls back to the empty manifest —
    today's behaviour exactly. A corrupt manifest file installs
    empty AND reports the problem, never a silent fallback.
    Manifest paths are relative to the manifest's directory and
    are resolved to absolute paths at load, so the bundle works
    in the lab layout and the installed layout alike.
    """
    import json

    from . import icon_manifest as _icons  # GAPS-15: manifest logic moved to core/icon_manifest.py

    global _shipped_manifest_loaded
    path = _shipped_manifest_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ValueError("shipped manifest 'entries' is not a list")
    except Exception as exc:  # noqa: BLE001 — a corrupt bundle is data
        set_icon_manifest([])
        _icon_manifest_problems.append(_icons.ManifestProblem(
            str(path), "shipped manifest unreadable: %s" % (exc,)))
        return True
    entries = []
    dropped = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            dropped.append(_icons.ManifestProblem(
                str(path), "shipped manifest entry is not an object"))
            continue
        rel = entry.get("path", "")
        entries.append({"name": entry.get("name", ""),
                        "system": entry.get("system", ""),
                        "path": str(path.parent / rel) if rel else ""})
    result = set_icon_manifest(entries)
    _icon_manifest_problems.extend(dropped)
    _shipped_manifest_loaded = (
        result["problems"] == [] and dropped == [])
    return True


def set_icon_manifest(entries) -> dict:
    """Install a bundling-step manifest (validated at load).

    Invalid entries are dropped AND reported (the staged builder's
    contract); the live loader keeps working from its templates
    for every name the manifest does not bundle. Installing any
    manifest directly clears the shipped-bundle flag — the flag
    means the SHIPPED bundle is installed, and only the shipped
    loader sets it.
    """
    from . import icon_manifest as _icons  # GAPS-15: manifest logic moved to core/icon_manifest.py

    global _icon_manifest, _icon_manifest_problems
    global _icon_manifest_validated, _shipped_manifest_loaded
    _shipped_manifest_loaded = False

    built = _icons.build_manifest(list(entries))
    problems = list(built["problems"])
    problems.extend(_icons.validate_manifest(built["manifest"]))
    _icon_manifest = dict(built["manifest"])
    _icon_manifest_problems = problems
    _icon_manifest_validated = True
    return {"manifest": dict(_icon_manifest),
            "problems": list(problems)}


def resolve_icon(name: str):
    """Resolve a name through the manifest (validated at first load).

    First load installs the shipped bundle manifest when the
    bundle ships; with no bundle the manifest is empty and every
    name resolves to missing — honestly, and the live loader falls
    back to its templates. A bundled name resolves to its manifest
    entry.
    """
    from . import icon_manifest as _icons  # GAPS-15: manifest logic moved to core/icon_manifest.py

    global _icon_manifest_validated
    if not _icon_manifest_validated:
        if not _load_shipped_manifest():
            set_icon_manifest([])
    return _icons.resolve(name, _icon_manifest)


def icon_manifest_status() -> dict:
    """Load-time manifest validation state (problems, size).

    bundling_step_owed is True while no shipped bundle manifest
    is installed (a tree without the bundle, or a manual manifest
    reset); False once the shipped bundle loads cleanly.
    """
    if not _icon_manifest_validated:
        if not _load_shipped_manifest():
            set_icon_manifest([])
    return {"entries": len(_icon_manifest),
            "problems": [{"path": p.path, "reason": p.reason}
                         for p in _icon_manifest_problems],
            "bundling_step_owed": not _shipped_manifest_loaded}

# GAPS-15 (2026-11-13): the theme/summon/proof re-exports are
# GONE — w9_wiring_theme, w9_wiring_summon and w9_wiring_proof
# moved to gui/ (they build Qt widgets / stylesheets / panels).
# GUI callers import them from gui/ now; core/w9_wiring.py keeps
# the icon-manifest state and the chat/wallpaper/canon/lan
# re-exports only.
from .w9_wiring_chat import (_LEGACY_SETTINGS_KEYS, _schema_key, apply_theme_pack_text, diagnose_turn, get_turn_ladder, lint_error, migrate_settings_store, note_chat_message, note_turn_error, note_turn_success, reset_settings_row, saved_theme_pack_names, settings_schema_rows, settings_snapshot, settings_store)  # INTENTIONAL (B31: facade re-export; moved code lives in w9_wiring_chat)
from .w9_wiring_wallpaper import (wallpaper_for_now)  # INTENTIONAL (B31: facade re-export; moved code lives in w9_wiring_wallpaper)
from .w9_wiring_canon import (_CUE_MOMENTS, _canon_uses, _cue_loudness_dbfs, _last_emission, canon_gate_for_cue, gate_sound_emission, last_emission_record, reset_canon_uses_for_tests)  # INTENTIONAL (B31: facade re-export; moved code lives in w9_wiring_canon)
from .w9_wiring_lan import (build_lan_state_map, lan_mirrors_snapshot, read_home_mirrors, soundscape_bed)  # INTENTIONAL (B31: facade re-export; moved code lives in w9_wiring_lan)
