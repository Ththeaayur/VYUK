"""Wave 6 integration coordinator — Builds 1-5 as live tools,
without modifying the staging modules themselves (Triage Wire
pattern, as in ``core/decision_leaves_t4.py``: the staging
modules are imported, the coordinator registers tools via
``register_all(registry)``, each handler takes the named schema
fields, forwards one dict to the staging module, and returns
the staging result encoded as a JSON string verbatim; refusal
dicts flow the same way, and module raises become an error
JSON — handlers never raise).

Wiring, per WAVE6_WIRING_SPEC (19 tools, registry 532 -> 551):
Build 1 EXT-HARNESS (7): extension_audit / extension_status
(read-only), extension_register / extension_approve /
extension_enable / extension_revoke (confirm-gated),
extension_disable (free-by-design app-state). Build 2 AGENT
(0 tool rows, deliberate): the production caller is the
session's async goal path (``plan`` parameter on
``cloud_text_client.run_goal`` / ``local_client.run_goal``),
never a synchronous job-running tool. Build 3 WORK (3,
read-only): workflow_distil / workflow_build_plan /
workflow_to_playbook_steps. Build 4 MISS (3, read-only):
miss_compound / miss_capability_map / miss_to_playbook_steps.
Build 5 MISSION (6): mission_build_brief / mission_build_plan
/ mission_evidence_board / mission_journal_entry (read-only),
mission_journal_append (free-by-design app-state, fixed file
only), mission_run_job (confirm-gated, stages only — it never
executes a job).

Stores (own files, never assistant.db, never the vault,
never .env): harness ``data/extensions_w6.json`` (first real
registration on his machine creates it; a normal runtime
file), mission journal ``data/mission_journal_w6.json``
(fixed file only — a caller-chosen journal destination would
be a different, confirm-gated tool, not a silent upgrade).

FUTURE EXTENSION LOADER OBLIGATION (spec §1.6, not built
here): the harness governs extensions; it does not load them.
Any future loader MUST call, in this order, before any
extension code runs: ``validate_manifest`` -> ``register`` ->
human ``approve`` -> ``enable`` -> ``check_action`` on every
extension action, with ``confirm`` wired to
``core/confirm_gate.request_confirmation`` (tool name + args,
deny on anything but a clear yes) — never a lambda returning
True, never a default approval — and with symlink-resolved
path checks. Until that loader exists, no extension code runs
in the app, and the harness tools are the only extension
surface. Marketplace/store is gated on the §14.20 counsel
gate + store rules + his approval, in that order; nothing
here opens it.

This coordinator is NOT a gate owner: it imports no gate
module. Confirm-gated tools are refused by the funnel
(``tools_gate`` classification) before the handler runs, so
a handler calling ``harness.approve(approved=True,
human=True)`` runs only after the funnel's human confirm —
the same pattern as the Triage deletions. A dial level
waiving C-14 is refused by the mission module, never
downgraded. The loop's single playbook store stays core/playbook_store
(``playbook_store`` / ``goal_client_supply``); no
workflow, compound, board, or journal store is added to the
memory DB (frozen schema).

Symlink/junction precondition (spec §1.3, applied in
``core/extension_safety_harness.py`` itself): ``check_path_access``
resolves real paths before the containment check, and
``extension_register`` records the resolved directory — a
symlink inside the declared directory pointing outside it is
refused, never passed on lexical containment alone.
"""


from __future__ import annotations  # INTENTIONAL (B11 split): old module carried it (B1 lesson).


# INTENTIONAL (B11 split): stdlib imports re-exported so the facade
# namespace matches the old module exactly (files3/files2 lesson).
import json  # noqa: F401  (re-export)
import os  # noqa: F401  (re-export)
import re  # noqa: F401  (re-export)
import tempfile  # noqa: F401  (re-export)
from pathlib import Path  # noqa: F401  (re-export)


# ---------------------------------------------------------------------------
# Store seams (tests substitute these, as in study_flow / triage4;
# production uses the app data directory, never a caller path).
# The seam lives in the facade: undo_completion reads
# _shared_harness/_shared_harness_path as facade-module attributes
# (live reads), and tests drive w6.setters — a value-snapshot
# re-export of the cache globals would silently break both.
# ---------------------------------------------------------------------------


_HARNESS_FILENAME = "extensions_w6.json"


_JOURNAL_FILENAME = "mission_journal_w6.json"


_HARNESS_STORE_OVERRIDE = None


_JOURNAL_PATH_OVERRIDE = None


_shared_harness = None


_shared_harness_path = None


def set_test_harness_store(path):
    """Point the shared harness at a test store (tests only)."""
    global _HARNESS_STORE_OVERRIDE, _shared_harness, _shared_harness_path
    _HARNESS_STORE_OVERRIDE = str(path) if path else None
    _shared_harness = None
    _shared_harness_path = None


def clear_test_harness_store():
    set_test_harness_store(None)


def set_test_journal_path(path):
    """Point the mission journal at a test file (tests only)."""
    global _JOURNAL_PATH_OVERRIDE
    _JOURNAL_PATH_OVERRIDE = str(path) if path else None


def clear_test_journal_path():
    global _JOURNAL_PATH_OVERRIDE
    _JOURNAL_PATH_OVERRIDE = None


def _harness_store_path() -> Path:
    if _HARNESS_STORE_OVERRIDE:
        return Path(_HARNESS_STORE_OVERRIDE)
    try:
        from .config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR) / _HARNESS_FILENAME
    except Exception:  # noqa: BLE001 — config import must not break tools
        return Path(__file__).resolve().parent.parent / "data" / _HARNESS_FILENAME


def _journal_path() -> Path:
    if _JOURNAL_PATH_OVERRIDE:
        return Path(_JOURNAL_PATH_OVERRIDE)
    try:
        from .config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR) / _JOURNAL_FILENAME
    except Exception:  # noqa: BLE001 — config import must not break tools
        return Path(__file__).resolve().parent.parent / "data" / _JOURNAL_FILENAME


def shared_harness():
    """The ONE shared harness instance (tools + Doctor card).

    Same store as the tools, never a second harness, never a
    second store — so the Doctor card and the tool handlers
    can never disagree about an extension's state.
    """
    global _shared_harness, _shared_harness_path
    path = str(_harness_store_path())
    if _shared_harness is None or _shared_harness_path != path:
        from . import extension_safety_harness as _harness_mod

        _shared_harness = _harness_mod.ExtensionHarness(path)
        _shared_harness_path = path
    return _shared_harness


__all__ = [
    "shared_harness", "set_test_harness_store", "clear_test_harness_store",
    "set_test_journal_path", "clear_test_journal_path",
    "extension_audit", "extension_status", "extension_register",
    "extension_approve", "extension_enable", "extension_disable",
    "extension_revoke",
    "workflow_distil", "workflow_build_plan", "workflow_to_playbook_steps",
    "miss_compound", "miss_capability_map", "miss_to_playbook_steps",
    "mission_build_brief", "mission_build_plan", "mission_evidence_board",
    "mission_journal_entry", "mission_journal_append", "mission_run_job",
    "register_all",
]


# INTENTIONAL (B11 split): impl re-exports. ORDER MATTERS — the seam
# section above is defined BEFORE these imports, so impl modules that
# import seam names from this facade resolve them during partial
# initialisation (B2/B5 seam-in-facade pattern). Impl modules are
# never imported directly in-tree; the facade is the only entry point.
from .integration_harness_tools_helpers import _parse_json, _parse_list, _parse_dict, _handler, _norm  # noqa: F401
from .integration_harness_tools_extensions import extension_audit, extension_status, _record_extension_change, _note_irreversible, extension_register, extension_approve, extension_enable, extension_disable, extension_revoke  # noqa: F401
from .integration_harness_tools_workflow import _proven_job_from_dict, _workflow_from_dict, workflow_distil, workflow_build_plan, workflow_to_playbook_steps, miss_compound, miss_capability_map, miss_to_playbook_steps, mission_build_brief, _combined_workflow, mission_build_plan, mission_evidence_board, mission_journal_entry, mission_journal_append, mission_run_job  # noqa: F401
from .integration_harness_tools_handlers import extension_audit_handler, extension_status_handler, extension_register_handler, extension_approve_handler, extension_enable_handler, extension_disable_handler, extension_revoke_handler, workflow_distil_handler, workflow_build_plan_handler, workflow_to_playbook_steps_handler, miss_compound_handler, miss_capability_map_handler, miss_to_playbook_steps_handler, mission_build_brief_handler, mission_build_plan_handler, mission_evidence_board_handler, mission_journal_entry_handler, mission_journal_append_handler, mission_run_job_handler  # noqa: F401
from .integration_harness_tools_register import register_all  # noqa: F401
