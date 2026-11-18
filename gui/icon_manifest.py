"""Wave 9 (v5.64) — icon system: Phosphor Duotone primary, Fluent subset.

ONE primary icon system: Phosphor Duotone. A single, narrow Fluent
subset covers Windows-native actions ONLY (window chrome and
OS affordances where a platform-native glyph is the honest
choice). Mixing two full systems is rejected inside this
module: ``validate_manifest`` fails any manifest whose Fluent
entries escape the subset, and any manifest that treats
Fluent as a second primary.

B-R1-6 (2026-11-09): Phosphor Duotone REPLACED Lucide as the
primary system (owner-picked round-1 best bet). Lucide was
primary 2026-11-02 → 2026-11-09 (15 SVGs, ISC, commit
a630cec7…); the Lucide bundle is removed by B-R1-6 — keeping
it alongside Phosphor would be two full systems, which this
module rejects. The one-primary rule is unchanged; only the
primary changed. The inline templates in ``gui/icons.py``
stay as the honest fallback for names the bundle does not
cover (``senses`` — no Phosphor stem exists for the
radar/sonar glyph; see the Phosphor bundle record).

LICENCES, verified from repo LICENSE primaries:
* Phosphor — MIT License, phosphor-icons/core repo LICENSE
  ("MIT License, Copyright (c) 2023 Phosphor Icons", read
  2026-11-09 from the repo at pinned commit 2b75f3ad…).
* Fluent UI System Icons — MIT License, microsoft/
  fluentui-system-icons repo LICENSE (verified 2026-10-25).
Both are allowlisted as permissive; neither is copyleft,
non-commercial, revenue-capped, or territory-restricted.

NO BUNDLED SVGS ARE CLAIMED BY THIS MODULE. No icon file ships
in this module and none is fabricated: the bundling step —
fetching the named icons at build/packaging time from the
Phosphor release (and the Fluent subset from its release) —
LANDED 2026-11-09 in ``gui/assets/icons/phosphor/`` (Phosphor
repo commit 2b75f3ad…, MIT licence text beside the SVGs,
manifest + bundle record in the same directory; the record is
``BUNDLING_OWED`` below, kept under its old name). The resolver
still reports a missing icon honestly as ``status="missing"``
instead of substituting a lookalike, and the live loader falls
back to its templates for every name the bundle does not cover.

Stdlib only. No Qt import: the resolver is pure data, so it
is testable offscreen and cannot drag GUI state into tests.
"""

from __future__ import annotations

# GAPS-15 (2026-11-13): the pure resolver logic (IconRef,
# ManifestProblem, validate_icon_name, resolve, build_manifest,
# validate_manifest, PRIMARY_SYSTEM, FLUENT_WINDOWS_ACTIONS)
# moved verbatim to core/icon_manifest.py — it lived in gui/
# while core/w9_wiring.py needed it, a core -> GUI import.
# This module is still the GUI-facing home: it re-exports the
# moved names through the GUI facade, so existing importers
# work unchanged. The try/except is the tree's established
# dual-spelling fallback (shipped `voice_assistant.*` first,
# lab `core.*` second) so this module keeps working with or
# without the package shim.
try:
    from voice_assistant.core.gui_facade import (
        FLUENT_WINDOWS_ACTIONS,
        PRIMARY_SYSTEM,
        IconRef,
        ManifestProblem,
        build_manifest,
        resolve,
        validate_icon_name,
        validate_manifest,
    )
except ImportError:
    from core.gui_facade import (
        FLUENT_WINDOWS_ACTIONS,
        PRIMARY_SYSTEM,
        IconRef,
        ManifestProblem,
        build_manifest,
        resolve,
        validate_icon_name,
        validate_manifest,
    )

MODULE_VERSION = "5.64.0"

__all__ = [
    "MODULE_VERSION",
    "PRIMARY_SYSTEM",
    "PHOSPHOR_LICENCE",
    "PHOSPHOR_LICENCE_SOURCE",
    "FLUENT_LICENCE",
    "FLUENT_LICENCE_SOURCE",
    "FLUENT_WINDOWS_ACTIONS",
    "BUNDLING_OWED",
    "IconRef",
    "validate_icon_name",
    "resolve",
    "build_manifest",
    "validate_manifest",
    "ManifestProblem",
]

# B-R1-6 (2026-11-09): Phosphor Duotone is the primary system.
# LUCIDE_LICENCE is gone with the Lucide bundle — nothing in
# the tree imports it (parent-verified by grep at build time).
# (PRIMARY_SYSTEM itself now lives in core/icon_manifest.py and
# is re-exported above.)
PHOSPHOR_LICENCE = "MIT"
PHOSPHOR_LICENCE_SOURCE = (
    "https://github.com/phosphor-icons/core/blob/main/LICENSE")
FLUENT_LICENCE = "MIT"
FLUENT_LICENCE_SOURCE = (
    "https://github.com/microsoft/fluentui-system-icons/blob/master/LICENSE")

# The bundling step, documented rather than faked (kept under
# its old name): LANDED 2026-11-09 — 19 Phosphor Duotone SVGs
# in gui/assets/icons/phosphor/ (commit 2b75f3ad…, MIT),
# manifest + bundle record beside them, Fluent count 0. Names
# the bundle does not cover still resolve missing, honestly.
# (Supersedes the Lucide bundle, LANDED 2026-11-02, removed
# by B-R1-6 — see the module docstring.)
BUNDLING_OWED = {
    "step": "fetch at build/packaging time from the upstream releases",
    "phosphor_source": "Phosphor release assets (MIT), primary system",
    "fluent_source": "Fluent UI System Icons release assets (MIT), "
                     "subset only: " + ", ".join(sorted(FLUENT_WINDOWS_ACTIONS)),
    "status": "LANDED 2026-11-09 — gui/assets/icons/phosphor/, "
               "Phosphor commit 2b75f3ad12b420c9504ef05df8d2564a28f8500e",
}
