"""Wave 6 integration tools — integration_harness_tools_extensions (split B11).

Extension-harness tools (audit/status/register/approve/enable/disable/revoke). Imports seam names from the facade (see facade ordering note).
"""

from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it; annotations stay lazy.

import json
import os
from .integration_harness_tools import _harness_store_path, shared_harness
from .integration_harness_tools_helpers import _parse_dict


def extension_audit(manifest_json):
    """Validate a manifest + audit its licence (pure composition)."""
    from . import extension_safety_harness as _h

    manifest = _parse_dict(manifest_json, "manifest_json")
    validation = _h.validate_manifest(manifest)
    lic_ok, lic_reason = _h.audit_licence(manifest.get("licence"))
    return {
        "ok": bool(validation["ok"]),
        "errors": list(validation["errors"]),
        "licence_ok": lic_ok,
        "licence_reason": lic_reason,
        "manifest": validation["manifest"],
    }


def extension_status(name):
    """One extension's state, or 'unknown extension' — never invented."""
    status = shared_harness().status(name)
    if status is None:
        return {"name": name, "state": "unknown extension"}
    return status


def _record_extension_change(name, prior_entry, after_state):
    """Wave 7 Build 2 hook: after a successful harness
    transition, record the prior entry for undo. When no
    undo store is configured the transition stands exactly
    as it ran, unrecorded — recording never breaks it."""
    try:
        from . import undo_completion as _undo
    except Exception:  # noqa: BLE001
        try:
            from core import undo_completion as _undo
        except Exception:  # noqa: BLE001
            return
    try:
        _undo.record_extension_transition(
            str(_harness_store_path()), name, prior_entry, after_state)
    except Exception:  # noqa: BLE001 — recording never breaks a transition
        pass


def _note_irreversible(name, change_class):
    """Wave 7 Build 2 hook: record a terminal/audit change
    as explicitly non-undoable, so undo skips and lists it."""
    try:
        from . import undo_completion as _undo
    except Exception:  # noqa: BLE001
        try:
            from core import undo_completion as _undo
        except Exception:  # noqa: BLE001
            return
    try:
        _undo.note_irreversible_change(
            "undo-" + name, change_class=change_class)
    except Exception:  # noqa: BLE001
        pass


def extension_register(manifest_json, directory):
    """Register a validated manifest (inert until human approval).

    The directory is resolved (symlinks/junctions) before it is
    recorded, so the harness's resolved-path checks and the
    recorded directory can never disagree about where the
    extension lives. Registration does NOT approve or enable.
    """
    from . import extension_safety_harness as _h  # noqa: F401 — see docstring

    manifest = _parse_dict(manifest_json, "manifest_json")
    if not directory or not str(directory).strip():
        raise ValueError("directory is required, not supplied")
    resolved = os.path.realpath(str(directory))
    harness = shared_harness()
    prior = json.loads(json.dumps(harness.entries.get(manifest.get("name")))) \
        if harness.entries.get(manifest.get("name")) is not None else None
    result = harness.register(manifest, directory=resolved)
    if isinstance(result, dict) and result.get("ok"):
        _record_extension_change(manifest.get("name"), prior, "registered")
    return result


def extension_approve(name):
    """Approve an extension (funnel confirm-gate runs first).

    The handler trusts the funnel: this tool is confirm-gated,
    so the funnel refuses it without a human confirm handler
    before this code runs. The handler never approves on its own.
    """
    harness = shared_harness()
    prior = json.loads(json.dumps(harness.entries.get(name))) \
        if harness.entries.get(name) is not None else None
    result = harness.approve(name, approved=True, human=True)
    if isinstance(result, dict) and result.get("ok"):
        _record_extension_change(name, prior, "approved")
    return result


def extension_enable(name):
    """Enable an already-approved extension (funnel confirm first)."""
    harness = shared_harness()
    prior = json.loads(json.dumps(harness.entries.get(name))) \
        if harness.entries.get(name) is not None else None
    result = harness.enable(name)
    if isinstance(result, dict) and result.get("ok"):
        _record_extension_change(name, prior, "enabled")
    return result


def extension_disable(name, reason=""):
    """Disable an extension (reversible safety action, app-state)."""
    harness = shared_harness()
    prior = json.loads(json.dumps(harness.entries.get(name))) \
        if harness.entries.get(name) is not None else None
    result = harness.disable(name, reason=reason or "")
    if isinstance(result, dict) and result.get("ok"):
        _record_extension_change(name, prior, "disabled")
    return result


def extension_revoke(name, reason=""):
    """Revoke an extension (terminal; funnel confirm first)."""
    result = shared_harness().revoke(name, reason=reason or "")
    if isinstance(result, dict) and result.get("ok"):
        # Terminal: recorded non-undoable (Wave 7 Build 2),
        # so undo skips and lists it instead of pretending.
        _note_irreversible(name, "extension revoked")
    return result
