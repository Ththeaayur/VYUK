"""Wave 10 (v5.65) wiring — the coordinator's integration layer.

Every Wave 10 leaf module ships stdlib-only and unwired; this module is
the single place that connects them to live paths:

- PF-6 changelog: the startup decision (show / baseline / none) and the
  settings merge for PF-4 export / PF-27 scheduled backups.
- PF-11 safe mode: applying the minimal configuration at startup and
  the watcher-start guard's predicate.
- PF-24 autostart: the Settings toggle's handler (consent + mechanism).
- PF-27: the scheduled-backup tick the live RoutineScheduler calls.
- A8 machine doctor: Windows CIM readers (PowerShell, guarded) feeding
  the leaf's conservative report; unknown on any failure, never alarm.
- MN-1.3 licence status + EY-H asset verify: one distribution report
  for the Doctor card.

Pure functions take injected callables/paths so the integration suite
can drive them without Qt, without Windows, and without touching the
tree's data/. Nothing here registers a funnel tool (INV-1 headroom is
5,909 B and this wave needs none of it). Product VERSION is untouched
(still 5.64.1); this module carries no product-version constant.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import backup as _backup
    from . import changelog as _changelog
    from . import licence as _licence
    from . import lifecycle as _lifecycle
    from . import machine_doctor as _doctor
    from . import safemode as _safemode
    from .config_spec import (
        ENV_KEY_FOR_FIELD as _ENV_KEY_FOR_FIELD,
        QSETTINGS_KEY_FOR_NAME as _QSETTINGS_KEY_FOR_NAME,
    )
except Exception:  # noqa: BLE001 — direct-by-path import fallback
    from voice_assistant.core import backup as _backup
    from voice_assistant.core import changelog as _changelog
    from voice_assistant.core import licence as _licence
    from voice_assistant.core import lifecycle as _lifecycle
    from voice_assistant.core import machine_doctor as _doctor
    from voice_assistant.core import safemode as _safemode
    from voice_assistant.core.config_spec import (
        ENV_KEY_FOR_FIELD as _ENV_KEY_FOR_FIELD,
        QSETTINGS_KEY_FOR_NAME as _QSETTINGS_KEY_FOR_NAME,
    )

# Settings keys carried by a PF-4 export / PF-27 backup. Config-backed
# keys go through save_env_setting on import; QSettings keys go through
# the QSettings-like store. Anything else is reported as skipped, never
# silently dropped (Leaf 2's caveat, closed here by stating the set).
#
# GAPS-21 (C-V4): these tables are DERIVED from the spec now, not
# hand-copied literals. The field lists below are the export's
# scope (which settings travel); the env names / QSettings keys
# come from the spec tables, so a rename in the spec breaks loudly
# at import (KeyError) instead of silently exporting a dead key.
_PORTABILITY_CONFIG_FIELDS = (
    "voice_name", "brain_engine", "preferred_language",
    "voice_auto_switch", "tts_loudness_enabled",
    "tts_loudness_target_dbfs", "kokoro_voice", "local_backend",
)
_PORTABILITY_QSETTINGS_FIELDS = (
    "theme", "orb_style", "sounds_family", "watchers_enabled",
)
CONFIG_BACKED_KEYS = tuple(
    _ENV_KEY_FOR_FIELD[f] for f in _PORTABILITY_CONFIG_FIELDS)
QSETTINGS_KEYS = tuple(
    _QSETTINGS_KEY_FOR_NAME[f] for f in _PORTABILITY_QSETTINGS_FIELDS)

_CONFIG_ATTR_TO_ENV = {
    f: _ENV_KEY_FOR_FIELD[f] for f in _PORTABILITY_CONFIG_FIELDS
}

LICENCE_FILENAME = "licence.json"
BACKUP_DIRNAME = "backups"
BACKUP_RETENTION_COUNT = 8
BACKUP_RETENTION_BYTES = 512 * 1024 * 1024


# ---------------------------------------------------------------- PF-6
def changelog_action(onboarded: bool, last_seen_version,
                     current_version: str) -> dict:
    """The startup changelog decision (pure).

    Fresh install (not onboarded) -> "baseline": onboarding completion
    marks the current version seen, so a new user is never spammed
    with 14 backlogged entries. Upgrader with no record -> "show"
    every entry once (PF-6: no version skipped or batched silently).
    Upgrader with a record -> "show" exactly the newer entries, or
    "none" when nothing is owed. A garbled record -> "show" all once
    (no skip) rather than crashing startup or hiding entries.
    """
    if not onboarded:
        return {"action": "baseline", "entries": [],
                "reason": "fresh install; onboarding marks baseline"}
    if last_seen_version is None:
        entries = _changelog.pending_entries(None)
        return {"action": "show", "entries": entries,
                "reason": "first v5.65 launch; full backlog shown once"}
    try:
        entries = _changelog.pending_entries(last_seen_version)
    except ValueError:
        entries = _changelog.pending_entries(None)
        return {"action": "show", "entries": entries,
                "reason": "garbled last-seen record; full list shown once"}
    if not entries:
        return {"action": "none", "entries": [],
                "reason": "no newer entries"}
    return {"action": "show", "entries": entries,
            "reason": f"{len(entries)} newer entries"}


def mark_changelog_seen(record: dict | None = None) -> dict:
    """Build the record marking everything up to LATEST as seen."""
    return _changelog.mark_seen(record)


# ------------------------------------------------------- PF-4 / PF-27
def collect_portability_settings(config_obj, qsettings_get=None) -> dict:
    """Merge live settings into one exportable dict (Leaf 2 caveat).

    Config-backed values come from the live config object (not the
    settings-model snapshot, which returns defaults for these fields);
    UI prefs come from the QSettings-like getter when supplied. Keys
    are the env names / QSettings keys the import side understands.
    """
    out: dict = {}
    for attr, env_key in _CONFIG_ATTR_TO_ENV.items():
        value = getattr(config_obj, attr, None)
        if value is not None:
            out[env_key] = value
    if callable(qsettings_get):
        for key in QSETTINGS_KEYS:
            try:
                value = qsettings_get(key, None)
            except Exception:  # noqa: BLE001 — a missing key is skipped
                value = None
            if value is not None:
                out[key] = value
    return out


def apply_portability_settings(payload: dict, config_save_fn=None,
                               qsettings_set=None) -> dict:
    """Apply an imported settings dict; report applied + skipped.

    Config-backed keys go through the save function (save_env_setting
    in production); QSettings keys through the store setter. Unknown
    keys land in "skipped" — reported, never silently dropped. Values
    are stringified for the env file, as save_env_setting expects.
    """
    applied: list[str] = []
    skipped: list[str] = []
    for key, value in (payload or {}).items():
        if key in CONFIG_BACKED_KEYS:
            if callable(config_save_fn):
                try:
                    config_save_fn(key, str(value))
                    applied.append(key)
                except Exception:  # noqa: BLE001 — report, don't raise
                    skipped.append(key)
            else:
                skipped.append(key)
        elif key in QSETTINGS_KEYS:
            if callable(qsettings_set):
                try:
                    qsettings_set(key, value)
                    applied.append(key)
                except Exception:  # noqa: BLE001
                    skipped.append(key)
            else:
                skipped.append(key)
        else:
            skipped.append(key)
    return {"applied": applied, "skipped": skipped}


# ---------------------------------------------------------------- PF-11
def apply_safe_mode(config_obj, data_dir=None, env=None) -> dict:
    """Apply the safe-mode minimal configuration, if requested.

    Safe mode is requested by env (VYUK_SAFE_MODE) or by the flag file
    in data/. When active: the live brain is forced local/builtin on
    the config singleton (the singleton already exists by the time
    main() runs, so env alone would not reach it), and an active
    marker is set for surfaces to display. Watcher suppression lives
    in the window's _init_watchers guard, which reads
    is_safe_mode_active(). Returns {"active": bool, "sources": {...}}.
    """
    sources = _safemode.safe_mode_sources(data_dir=data_dir, env=env)
    active = _safemode.is_safe_mode(data_dir=data_dir, env=env)
    if not active:
        return {"active": False, "sources": sources}
    try:
        config_obj.brain_engine = "local"
    except Exception:  # noqa: BLE001 — best effort on the singleton
        pass
    try:
        config_obj.local_backend = "builtin"
    except Exception:  # noqa: BLE001
        pass
    os.environ["VYUK_SAFE_MODE_ACTIVE"] = "1"
    return {"active": True, "sources": sources}


def is_safe_mode_active(env=None, data_dir=None) -> bool:
    """True when this process is running in safe mode."""
    mapping = os.environ if env is None else env
    if str(mapping.get("VYUK_SAFE_MODE_ACTIVE", "")).strip() == "1":
        return True
    return _safemode.is_safe_mode(data_dir=data_dir, env=env)


# ---------------------------------------------------------------- PF-27
def backup_tick(now: datetime, data_dir: str, settings: dict) -> dict:
    """One scheduled-backup tick for the live RoutineScheduler tick.

    Due -> create a bundle under data/backups/, prune to retention
    (8 bundles / 512 MB, newest never pruned), advance the schedule
    record. Not due -> {"ran": False}. A failure is RECORDED on the
    schedule record (PF-27: reported, never silent) and returned.
    """
    record = _backup.load_schedule_record(data_dir)
    backups_dir = os.path.join(data_dir, BACKUP_DIRNAME)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _run():
        out_path = os.path.join(backups_dir, f"vyuk-backup-{stamp}.zip")
        report = _backup.create_backup(data_dir, settings, out_path)
        _backup.prune_backups(backups_dir, BACKUP_RETENTION_COUNT,
                              BACKUP_RETENTION_BYTES)
        return report

    result = _backup.schedule_tick(record, now, _run)
    _backup.save_schedule_record(data_dir, result["record"])
    return result


def backup_status(data_dir: str) -> dict:
    """The scheduler status a UI/doctor surface shows."""
    return _backup.get_status(data_dir)


# ------------------------------------------------- A8 machine doctor
def _run_cim(class_name: str, properties: str, runner=None) -> str | None:
    """Run one Get-CimInstance query; None on any failure (unknown)."""
    command = ["powershell", "-NoProfile", "-NonInteractive",
               "-Command",
               f"Get-CimInstance {class_name} | "
               f"Select-Object {properties} | Format-List"]
    try:
        if runner is not None:
            out = runner(command)
        else:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=30)
            out = proc.stdout if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001 — a failed probe is unknown
        return None
    return out


def parse_cim_list(text: str | None) -> list[dict]:
    """Parse Format-List output (blank-line-separated records)."""
    if not text:
        return []
    records: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    if current:
        records.append(current)
    return records


def _to_number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def windows_machine_readers(runner=None) -> dict:
    """CIM readers for run_machine_doctor(); Windows-only, guarded.

    Battery: Win32_Battery DesignCapacity/FullChargeCapacity (mWh in
    the CIM values used here — the doctor computes wear as a ratio,
    so the unit cancels). Disk: Win32_DiskDrive Status + predictive
    failure is not exposed by CIM, so only an explicit failing Status
    warns; anything else is ok/unknown, never an alarm. GPU:
    Win32_VideoController DriverVersion, reported only. Temps: no
    standard CIM temperature — returned as unknown, never guessed.
    Every reader failure yields None (unknown), never an alarm.
    """
    def _battery():
        text = _run_cim("Win32_Battery",
                        "DesignCapacity,FullChargeCapacity", runner)
        for rec in parse_cim_list(text):
            design = _to_number(rec.get("DesignCapacity"))
            full = _to_number(rec.get("FullChargeCapacity"))
            if design and design > 0 and full is not None:
                return {"design_capacity_mwh": design,
                        "full_charge_mwh": full,
                        "source": "Win32_Battery"}
        return None

    def _disk():
        text = _run_cim("Win32_DiskDrive", "Status", runner)
        for rec in parse_cim_list(text):
            status = rec.get("Status")
            if status:
                return {"smart_status": status.lower(),
                        "predictive_failure": None,
                        "source": "Win32_DiskDrive"}
        return None

    def _gpu():
        text = _run_cim("Win32_VideoController",
                        "Name,DriverVersion", runner)
        out = []
        for rec in parse_cim_list(text):
            out.append({"name": rec.get("Name"),
                        "driver_version": rec.get("DriverVersion"),
                        "source": "Win32_VideoController"})
        return out or None

    return {"battery": _battery, "disk": _disk, "gpu": _gpu,
            "temperatures": lambda: None}


def machine_report(readers: dict | None = None) -> dict:
    """Run the machine doctor with the given readers (or unknowns)."""
    return _doctor.run_machine_doctor(readers=readers)


# ------------------------------------------------- MN-1.3 + EY-H
def licence_status(data_dir: str) -> dict:
    """The licence state for the Doctor card (mechanism only).

    No file -> "none" (no licence installed; the app is not gated by
    it in this wave). A file under the shipped TEST key -> "test-key"
    (verifies nothing — stated plainly, never presented as valid).
    Otherwise the check result's status + plain message.
    """
    path = os.path.join(data_dir, LICENCE_FILENAME)
    if not os.path.isfile(path):
        return {"state": "none",
                "message": "No licence file installed (licensing is "
                           "mechanism-only in this wave)."}
    # The key the product ships is the placeholder until packaging
    # replaces it (LICENCE_CUSTODY.md): with it, verification is
    # inoperative, so no file content can count — this branch is
    # always taken in this build, and the check below only runs
    # once a real release key ships.
    if _licence.is_placeholder_key(_licence.TEST_PUBLIC_KEY_B64):
        return {"state": "test-key",
                "message": "Licence file present, but the shipped key "
                           "is the placeholder test key — it verifies "
                           "nothing. A release key is required before "
                           "any sale."}
    try:
        result = _licence.check_licence_file(path)
    except Exception as exc:  # noqa: BLE001 — a broken file is invalid
        return {"state": "invalid",
                "message": f"Licence file could not be read: {exc}"}
    return {"state": result.status, "message": result.message,
            "entitled": bool(result),
            "perpetual": bool(result.perpetual)}


def distribution_report(tree_root: str, data_dir: str,
                        machine_readers: dict | None = None) -> dict:
    """One report for the Doctor's Distribution & repair card."""
    try:
        from .health import asset_repair as _repair  # Phase 3 H1: moved to core/health/
    except Exception:  # noqa: BLE001
        from voice_assistant.core.health import asset_repair as _repair  # Phase 3 H1: moved to core/health/
    manifest = os.path.join(tree_root, "install_manifest.json")
    try:
        verify = _repair.verify_tree(tree_root, manifest)
        verify = {
            "checked": getattr(verify, "checked", None),
            "ok": getattr(verify, "ok", None),
            "mismatches": [
                {"file": getattr(m, "rel", getattr(m, "file", "?")),
                 "expected": getattr(m, "expected", None),
                 "actual": getattr(m, "actual", None)}
                for m in getattr(verify, "mismatches", [])
            ],
        }
    except Exception as exc:  # noqa: BLE001 — verify never breaks Doctor
        verify = {"ok": False, "error": str(exc), "mismatches": []}
    return {
        "install_verify": verify,
        "machine": machine_report(machine_readers),
        "licence": licence_status(data_dir),
        "backup": backup_status(data_dir),
        "safe_mode": is_safe_mode_active(),
    }
