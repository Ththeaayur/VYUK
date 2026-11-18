"""Icon-manifest resolver (pure data).

Moved from gui/icon_manifest.py by GAPS-15 (2026-11-13): the
resolver is stdlib-only pure data (no Qt import), but it lived
in gui/ while core/w9_wiring.py needed it — a core -> GUI
import. The logic moved here verbatim; gui/icon_manifest.py
keeps the GUI-facing home (licence record, bundling record,
MODULE_VERSION) and re-exports these names through the GUI
facade (core/gui_facade.py), so existing importers work
unchanged.

Licences (full record in gui/icon_manifest.py): Phosphor
Duotone — MIT (phosphor-icons/core); Fluent UI System Icons
subset — MIT (microsoft/fluentui-system-icons). Both
allowlisted as permissive.
"""

from __future__ import annotations

from dataclasses import dataclass

# B-R1-6 (2026-11-09): Phosphor Duotone is the primary system.
# LUCIDE_LICENCE is gone with the Lucide bundle — nothing in
# the tree imports it (parent-verified by grep at build time).
PRIMARY_SYSTEM = "phosphor"

# The ENTIRE Fluent allowance: Windows-native actions only.
# Anything else requested from Fluent is a manifest error, not
# a fallback — the primary system covers everything else.
FLUENT_WINDOWS_ACTIONS = frozenset({
    "window-close",
    "window-minimize",
    "window-maximize",
    "window-restore",
    "window-snap-left",
    "window-snap-right",
    "taskbar-pin",
    "system-tray",
})

@dataclass
class IconRef:
    """A resolution result. ``status`` is 'bundled' only when
    the icon is in the supplied manifest; otherwise 'missing'
    — never a fabricated substitute."""

    name: str
    system: str
    status: str  # "bundled" | "missing"
    path: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "bundled"

@dataclass
class ManifestProblem:
    path: str
    reason: str

def validate_icon_name(name: str) -> bool:
    """Icon names are lowercase kebab-case: letters, digits,
    hyphens, starting with a letter. Anything else is not an
    icon name and is rejected before resolution."""
    if not name or not name[0].isalpha():
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in name) \
        and "--" not in name and not name.endswith("-")

def resolve(name: str, manifest: dict | None = None) -> IconRef:
    """Resolve ``name`` against a bundling manifest
    (``{name: {"system": ..., "path": ...}}``). An invalid name
    resolves to missing under the primary system; an unknown
    name resolves to missing, honestly. The manifest is data
    supplied by the bundling step — this module ships none,
    so a default call reports every real icon as missing.

    Underscore loader names (mapping decision 2026-11-03, NOT
    a loader-name rename — callers keep asking for
    ``volume_off``): when the exact key misses and the name
    contains underscores, the hyphenated stem is tried
    as an exact manifest key. This is aliasing to a bundled
    entry, never a lookalike substitution: the fallback hits
    only when the hyphenated stem is itself a bundled entry
    with a path, and exact-key hits always win, so no existing
    resolution changes. (B-R1-6: the stem is now Phosphor —
    ``volume-off`` maps to ``speaker-x-duotone.svg`` — the
    mechanism is unchanged.)"""
    manifest = manifest or {}
    # Exact-key hits always win, for any name — the validity
    # gate below only decides whether the fallback may run.
    entry = manifest.get(name)
    if isinstance(entry, dict) and entry.get("path"):
        system = entry.get("system", PRIMARY_SYSTEM)
        return IconRef(name, system, "bundled", entry["path"])
    if validate_icon_name(name):
        return IconRef(name, PRIMARY_SYSTEM, "missing")
    if "_" in name:
        hyphenated = name.replace("_", "-")
        if validate_icon_name(hyphenated):
            entry = manifest.get(hyphenated)
            if isinstance(entry, dict) and entry.get("path"):
                system = entry.get("system", PRIMARY_SYSTEM)
                return IconRef(name, system, "bundled", entry["path"])
    return IconRef(name, PRIMARY_SYSTEM, "missing")

def build_manifest(entries: list[dict]) -> dict:
    """Build a manifest dict from bundling-step entries, each
    ``{"name": ..., "system": ..., "path": ...}``. Entries that
    fail validation are dropped AND reported via the returned
    ``problems`` — the manifest never silently contains a bad
    entry, and the caller can see what was dropped."""
    manifest: dict = {}
    problems: list[ManifestProblem] = []
    for entry in entries:
        name = entry.get("name", "")
        system = entry.get("system", "")
        path = entry.get("path", "")
        if not validate_icon_name(str(name)):
            problems.append(ManifestProblem(str(name), "invalid icon name"))
            continue
        if system not in ("phosphor", "fluent"):
            problems.append(ManifestProblem(
                str(name), f"unknown icon system: {system}"))
            continue
        if system == "fluent" and name not in FLUENT_WINDOWS_ACTIONS:
            problems.append(ManifestProblem(
                str(name),
                "Fluent is Windows-native actions only; "
                "use Phosphor for this icon"))
            continue
        if not path:
            problems.append(ManifestProblem(str(name), "missing path"))
            continue
        manifest[str(name)] = {"system": system, "path": str(path)}
    return {"manifest": manifest, "problems": problems}

def validate_manifest(manifest: dict) -> list[ManifestProblem]:
    """Structural validation of a manifest: no Fluent entry
    outside the Windows subset, no second primary, no entry
    without a path. Mixing two full systems is rejected here:
    a manifest where Fluent entries outnumber Phosphor entries
    is Fluent-as-primary, and fails."""
    problems: list[ManifestProblem] = []
    phosphor_count = 0
    fluent_count = 0
    for name, entry in manifest.items():
        if not isinstance(entry, dict):
            problems.append(ManifestProblem(str(name), "entry is not an object"))
            continue
        system = entry.get("system")
        if system == "phosphor":
            phosphor_count += 1
        elif system == "fluent":
            fluent_count += 1
            if name not in FLUENT_WINDOWS_ACTIONS:
                problems.append(ManifestProblem(
                    str(name),
                    "Fluent entry outside the Windows-native subset"))
        else:
            problems.append(ManifestProblem(
                str(name), f"unknown icon system: {system}"))
        if not entry.get("path"):
            problems.append(ManifestProblem(str(name), "missing path"))
    if fluent_count and phosphor_count and fluent_count > phosphor_count:
        problems.append(ManifestProblem(
            "$", "Fluent entries must stay a subset of Phosphor entries — "
                 "two full systems are not allowed"))
    return problems
