"""Qt Advanced Docking pilot (Wave 9 / v5.64, Leaf 3) — GATED, NOT LIVE.

This module is a SEPARATE pilot sitting UNDER the overview panel. It
is deliberately not part of the shipped panel:

* ``gui/overview_panel.py`` NEVER imports this module — not at load,
  not lazily, not behind a default. Grep the overview module: there
  is no reference to docking in it. Docking is reached only if a
  caller imports this module explicitly AND passes an explicit flag
  AND a ruling (see :func:`docking_status`).
* Nothing in this module imports any Advanced Docking binding, links
  any docking library, or constructs a dock manager. There is no
  binding in the tree to import: Qt Advanced Docking is a C++ library
  and its Python availability on Windows is itself an open item.

GATE RECORD — why this is GATED and not cleared by this leaf:
  licence: LGPL-2.1 (project README: "This project uses the
    LGPLv2.1 license", githubuser0xFFFF/Qt-Advanced-Docking-System;
    LGPL text at gnu-lgpl-v2.1.md in the same repo). LGPL-2.1 is
    dynamic-link-only for this product's purposes.
  owner's commercial directive (2026-10-24 03:20 IST, verbatim
    substance): copyleft material is EXCLUDED from the distributed
    product; nothing ships on an unverified licence. An LGPL
    component therefore needs an EXPLICIT clearance ruling from the
    owner — this leaf does not grant it to itself.
  owed item 1: the explicit clearance ruling (owner decision).
  owed item 2: a reproducible Windows wheel / binding that the
    installer can actually ship (no such wheel is in the tree, and
    none was downloaded here — a bulk/native build is out of this
    leaf's scope and would itself need the ruling first).

If EITHER owed item fails, docking dies and the panel ships
fixed-layout — which is exactly what v5.64 does: the overview panel
in ``overview_panel.py`` is fixed-layout and complete without this
module. The pilot's only job is to hold the gate honestly so a
future wave can pick it up without re-litigating the licence.

Closing steps, in order:
  1. Owner rules explicitly on LGPL-2.1 dynamic-link use (or rejects
     docking outright — rejection is a valid, terminal outcome).
  2. Only then, produce a reproducible Windows wheel/binding and
     verify it end to end on Windows (import + dock + undock +
     restore).
  3. Only then, wire the overview panel behind the flag, with the
     fixed layout remaining the default.

MODULE_VERSION = "5.64.0".
"""

from __future__ import annotations

MODULE_VERSION = "5.64.0"

# The panel's default. Nothing in the shipped tree flips this; only an
# explicit caller-side decision (flag + ruling + wheel) may.
DOCKING_ENABLED = False

LICENCE = "LGPL-2.1"
LICENCE_SOURCE = (
    "githubuser0xFFFF/Qt-Advanced-Docking-System README: "
    "'This project uses the LGPLv2.1 license'; "
    "licence text gnu-lgpl-v2.1.md in the same repo"
)

GATE = {
    "status": "GATED",
    "licence": LICENCE,
    "licence_source": LICENCE_SOURCE,
    "dynamic_link_only": True,
    "clearance_ruling_owed": True,
    "windows_wheel_owed": True,
    "shipped_in_v564": False,
    "fallback": "fixed-layout overview panel (gui/overview_panel.py)",
}


def docking_status(explicit_flag: bool = False, ruling: str | None = None) -> dict:
    """Report the gate. ``ready`` is False until BOTH owed items exist.

    ``explicit_flag`` + ``ruling`` record intent; they do NOT satisfy
    the gate on their own, because the Windows wheel does not exist
    yet and no ruling has been given. A caller that treats a
    non-ready status as permission is misusing this API.
    """
    status = dict(GATE)
    status["explicit_flag"] = bool(explicit_flag)
    status["ruling_supplied"] = bool(ruling and ruling.strip())
    # Both owed items must be real artefacts, not arguments. Neither
    # exists in v5.64, so ready is unconditionally False here.
    status["ready"] = False
    return status


def build_dock_manager(*_args, **_kwargs):
    """Always refuses in v5.64: the gate is not satisfied.

    Raises RuntimeError naming both owed items. This is the honest
    shape of a gated pilot: a loud refusal, not a silent stub that
    pretends docking works.
    """
    raise RuntimeError(
        "Qt Advanced Docking is GATED in v5.64 (LGPL-2.1, dynamic-link-only; "
        "explicit owner clearance ruling owed AND reproducible Windows "
        "wheel owed). The overview panel ships fixed-layout. "
        "See gui/docking.py GATE for closing steps."
    )
