"""The tool registry core (v5.50, programme item 43).

Split out of core/tools.py: ToolRegistry itself — registration,
declarations and the Gemini fitted-payload machinery, argument
validation, and execute(), the single gated funnel every brain
call passes through — plus the `tools` singleton and its pack
registration tail. The _tool_* handlers live in
core/funnel/tools_handlers.py (ToolHandlersMixin, inherited here); the
gate policy lives in core/funnel/tools_gate.py.

core/tools.py was deleted in Refactor Phase 4 (S1c); the funnel package is now the only home. Bodies moved
verbatim; behaviour is unchanged.
"""

import datetime
import inspect
import math
import threading
import time
from collections.abc import Callable
from typing import Any
from .. import injection  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from .guard_state import FailsafeState  # Phase 3 F2b: guard_state moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

from .tools_handlers import ToolHandlersMixin
from .tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _foreground_process_name,
    _READ_ONLY_TOOLS,
    _FREE_BY_DESIGN,
    _is_egress_read,
    _wrap_producer_result,
    _UNTRUSTED_PRODUCERS,
    _protected_path_hit,
    _protected_destination_hit,
    _install_tree_folder,
    _install_tree_write_hit,
    _handler_error_kind,
    _is_read_class,
    _tool_tier,
    wrap_mcp_content,
    grant_screen_scope,
    screen_scope_active,
    _needs_confirmation,
    _confirmation_args,
)  # Phase 3 F1: tools_gate moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))
from .tools_handlers import (
    article_for,
    deadline_for,
    note_tool_timeout,
    timeout_cooldown_active,
    _budget_refusal,
    _budget_check,
)
# v5.51 (H-1): the failure-script formatter — see
# core/funnel/failure_script.py. Applied once, in _audited below.
from .failure_script import apply_failure_script  # Phase 3 F2b: failure_script moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

from .tools_registry_core import (OPEN_SCHEMA_TOOLS, OVERRIDE_ALLOWLIST, TOOL_KIND_VALUES, TOOL_KIND_TABLE)  # noqa: F401 (GAPS-23 F-V5: tables live in the core module; re-exported here so existing importers keep working)

#: Audit fix Batch 3 (OBS-01) — sticky per-sink observability
#: failure counts for this process (sink -> failures). Read via
#: observability_degraded_state(); a status surface can query
#: it without parsing logs. Empty means no sink has failed yet.
_OBS_SINK_FAILURES: dict[str, int] = {}


def observability_degraded_state() -> dict[str, int]:
    """A copy of the process-level observability failure
    counters (telemetry / record_family / audit / receipts /
    failure_script / milestones / result_contract)."""
    return dict(_OBS_SINK_FAILURES)




from .tools_registry_core import ToolRegistryCoreMixin  # INTENTIONAL (batch 1): registry-core mixin
from .tools_registry_core import _wrap_funnel_handlers  # GAPS-23 (F-V7): capability boundary
from .tools_registry_helpers import ToolRegistryHelpersMixin  # INTENTIONAL (batch 1): registry-helpers mixin
from .tools_registry_validators import (  # INTENTIONAL (batch 1): validators moved to tools_registry_validators
    _VALIDATOR_ANNOTATIONS,
    _VALIDATOR_KEYWORDS,
    _check_value,
    _failsafe_state,
    _json_equal,
    _type_matches,
    _allows_null,
    _validate_args_against_schema,
    _validate_schema,
)
from .tools_registry_exec_setup import ToolRegistryExecSetupMixin, _ExecuteContext  # INTENTIONAL (batch 2): execute stage mixins
from .tools_registry_exec_gates import ToolRegistryExecGatesMixin  # INTENTIONAL (batch 2)
from .tools_registry_exec_dispatch import ToolRegistryExecDispatchMixin  # INTENTIONAL (batch 2)


# The funnel pipeline (GAPS-23, F-V4): execute() runs these
# stages IN THIS ORDER, first non-None result wins. The order
# is load-bearing — permission before route, staleness before
# confirm, confirm before dispatch — so it is declared once,
# here, and execute() loops over it; reordering is a one-line
# diff in this tuple, visible in review, never a silent
# cut-paste in the body. Each stage is a _exec_<name>(ctx)
# method on the exec mixins; a stage returns None to pass to
# the next stage, or a result dict to short-circuit (refuse /
# ask / run).
EXECUTE_STAGES: tuple[str, ...] = (
    "_exec_setup",
    "_exec_receipt",
    "_exec_mark_cb",
# PF-18 batch 1: the permission choke point — BEFORE route
# (so MCP-routed calls are covered: they return from
# _exec_route and never reach _exec_confirm) and BEFORE
# the confirm ask and its allowlist shortcut (consulted
# inside request_confirmation). See _exec_permission.
    "_exec_permission",
# PF-18 batch 7: the D9 folder-scope stage — AFTER the
# permission stage (denied files is already refused) and
# BEFORE route (so MCP-routed calls are covered
# structurally) and before the confirm ask. See
# _exec_folder_scope.
    "_exec_folder_scope",
    "_exec_route",
    "_exec_toolbox",
# Live staleness stage, P4 (2026-11-05; design:
# LIVE_VERIFIER_COVERAGE_DESIGN.md §3.4): AFTER toolbox
# (inner-tool resolution is complete, so the stage sees
# the TRUE tool name for direct, route_, and use_toolbox
# calls alike) and BEFORE confirm (a stale destructive
# action refuses before any dialog — no wasted approval;
# the C4 principle, applied to the funnel). Runs ONLY
# for live-origin calls; every other caller passes
# through with zero behaviour change.
    "_exec_live_staleness",
# Loophole batch 5 (L-24, #16): the owner-read stage — AFTER
# live-staleness (the true tool name is resolved) and BEFORE
# confirm (a refusal needs no dialog). Runs ONLY for
# live-origin calls in an unproven session under the lock;
# every other caller passes through untouched.
    "_exec_owner_read",
    "_exec_validate",
    "_exec_failsafe",
    "_exec_killchain",
    "_exec_protected_path",
    "_exec_protected_dest",
    "_exec_install",
    "_exec_echo",
    "_exec_confirm",
    "_exec_epoch_recheck",
    "_exec_rule_of_two",
    "_exec_limits",
    "_exec_receipt_check",
    "_exec_mark_dispatched",
    "_exec_deadline_legs",
    "_exec_dispatch",
)


class ToolRegistry(ToolHandlersMixin, ToolRegistryCoreMixin, ToolRegistryHelpersMixin, ToolRegistryExecSetupMixin, ToolRegistryExecGatesMixin, ToolRegistryExecDispatchMixin):
    def execute(self, name: str, args: dict[str, Any], cancel_token=None) -> dict[str, Any]:
        """Executes a registered tool by name with arguments.

        cancel_token (audit fix Batch 3, REL-03): an optional
        core.funnel.tool_cancel.CancellationToken, the opt-in side
        channel for cooperative cancellation. It is passed
        ONLY to handlers that declare a `cancel_token`
        parameter; every other handler is untouched, and on a
        deadline timeout the funnel cancels the token so a
        token-aware worker stops at its next poll point. When
        no token is supplied the funnel creates one per call
        (cancelled on timeout) — callers that need to cancel
        from outside must supply their own.

        Every caller — Gemini Live native calls, the Ollama path and
        the builtin JSON-block protocol — funnels through here, so the
        schema validation lives here too: bad arguments are rejected
        BEFORE the handler runs, with the validator's exact error in
        the message (the local brains feed it back for self-repair).
        v5.43: every error dict returned here carries a `kind`
        (H8 — validation / unavailable / refused / not_confirmed /
        timeout / handler_error), and file-content reads aimed at
        protected paths are refused before the gate (H17).
        v5.48 (programme item 28): the handler run carries a
        per-class deadline (kind "timeout" — also the kind of a
        post-timeout cool-down refusal), and a cross-brain safety
        budget caps how many calls may reach dispatch per minute
        (kind "budget"); see the executor-limits section above
        _TOOL_DEADLINE_CLASS."""
        ctx = _ExecuteContext()
        ctx.name = name
        ctx.args = args
        ctx.cancel_token = cancel_token
        ctx.audit_gate = None
        ctx.audited = None
        ctx.epoch_void = None
        ctx.obs_failures = []
        ctx.obs_sink_failures = _OBS_SINK_FAILURES
        ctx.receipt_attempt = None
        ctx.receipt_error = False
        ctx.receipt_is_read = False
        ctx.mark_dispatched_cb = None
        ctx.human_approved_this_call = False
        ctx.rot_legs = set()
        ctx.deadline = None
        ctx._mcp_entry = None
        ctx._mcp_server = None
        ctx._mcp_tool = None
        ctx._mcp_class = None
        ctx._mcp_bus = None
        for stage in EXECUTE_STAGES:
            result = getattr(self, stage)(ctx)
            if result is not None:
                return result
        return result


# GAPS-23 (F-V7): the capability boundary — wrap every _tool_*
# handler method in ToolRegistry's MRO ONCE, before the singleton
# is created and the packs register (so the registered handlers
# are the guarded bound methods). A direct call outside
# execute()'s dispatch raises; dispatch sets the flag (see
# _exec_dispatch). The wrap is idempotent (marked), so a
# re-import re-wraps nothing.
_wrap_funnel_handlers(ToolRegistry)


tools = ToolRegistry()
# GAPS-23 (F-V8): the pack registrations are DECLARATIVE.
# One table, one loop — the 92 sequential import-time
# tools._register_*_tools() calls are gone. The ORDER is
# the table order (identical to the old call order);
# tests/test_gaps23.py pins the table against the literal
# 92-name tuple, so a reorder breaks the suite loudly
# instead of silently changing which handler wins an
# override. Each entry's provenance comment block moved
# verbatim from above its old call site. A pack is a
# (name, registrar-method) pair; the loop below is the
# only place packs are driven.
PACKS: tuple[tuple[str, str], ...] = (
    # Register the extended commercial tool set
    ('extended', '_register_extended_tools'),
    # Register the Hands + Eyes control pack
    ('control', '_register_control_tools'),
    # Register the Mind pack (plans + routines)
    ('mind', '_register_mind_tools'),
    # Register the episodic-memory tools (day journal)
    ('episode', '_register_episode_tools'),
    # Register the Control Complete pack
    ('complete', '_register_complete_tools'),
    # Register pro cursor control
    ('cursor', '_register_cursor_tools'),
    # Register the UI Automation pack (v4.3)
    ('uia', '_register_uia_tools'),
    # Register full web control
    ('browser', '_register_browser_tools'),
    # Register Your World (Google Calendar + Gmail; reads + gated writes)
    ('world', '_register_world_tools'),
    # Register the Skills pack (everyday gaps, v2.7)
    ('skills', '_register_skills_tools'),
    # Register the Mega skills expansion (absolute control, v2.8)
    ('mega', '_register_mega_tools'),
    # Register the meeting pack (live capture -> transcribe -> notes, R7-4)
    ('meeting', '_register_meeting_tools'),
    # Register the summarisation pack (extractive TextRank, R8-3 sumy port)
    ('summary', '_register_summary_tools'),
    # Register the CDP inspection pack (read-only console / network /
    # performance over the tree's own CDP path, R10-1 chrome-devtools-mcp port)
    ('browser_cdp_inspect', '_register_browser_cdp_inspect_tools'),
    # Register the browser-history pack (read-only stored history /
    # bookmarks of a named profile, R10-3 browser-history port)
    ('browser_history', '_register_browser_history_tools'),
    # Register the python-adblock pack (read-only URL matching over a
    # caller-supplied filter list, R10-4 python-adblock port)
    ('browser_adblock', '_register_browser_adblock_tools'),
    # Register the python-readability pack (read-only article-body
    # extraction over a page's HTML, R10-5 python-readability port)
    ('article_extract', '_register_article_extract_tools'),
    # Register the ollama pack (local Ollama model management —
    # status read + confirm-gated pull/delete, R11-1 ollama port)
    ('ollama_models', '_register_ollama_models_tools'),
    # Register the event-planning pack (wedding/event planning
    # dashboard — plan/guests/vendors/budget/milestones/seating/
    # run-of-show, R16-8 the-wedding-website inspiration)
    ('event_planning', '_register_event_planning_tools'),
    # Register the event-ticketing pack (ticket types, sale records
    # and QR check-in for one event plan, R18-7 Attendize inspiration)
    ('event_ticketing', '_register_event_ticketing_tools'),
    # Register the technical-analysis pack (25 indicators over
    # operator-supplied OHLCV candles, no live feed, R18-8
    # bukosabino/ta inspiration)
    ('technical_analysis', '_register_technical_analysis_tools'),
    # Register the working-time tracker pack (projects, start/stop,
    # records, reports in decimal hours, R18-10
    # dominikbraun/timetrace inspiration)
    ('time_tracking', '_register_time_tracking_tools'),
    # Register the eye-rest pack (20-20-20 + breathing + glass counter, R8-4 screen-break port)
    ('eye_rest', '_register_eye_rest_tools'),
    # Register the wormhole pack (code-phrase encrypted file transfer, LAN-direct, R8-5 magic-wormhole port)
    ('wormhole', '_register_wormhole_tools'),
    # Register the status-strip pack (always-on live vitals bar, R8-6 yasb port)
    ('status_strip', '_register_status_strip_tools'),
    # Register the basic-pitch pack (audio-to-MIDI transcription, R8-7 basic-pitch port)
    ('basic_pitch', '_register_basic_pitch_tools'),
    # Register the Discord presence pack (Rich Presence via pypresence, R8-9)
    ('discord_presence', '_register_discord_presence_tools'),
    # Register the rustic backup pack (encrypted user-file backup, R9-2)
    ('rustic_backup', '_register_rustic_backup_tools'),
    # Register the companion pack (phone browser as a remote, LAN-only, R7-5)
    ('companion', '_register_companion_tools'),
    # Register the wake-training pack (train-your-own wake word, R7-6)
    ('wake_training', '_register_wake_training_tools'),
    # Register the workflow pack (DAG of funnel-tool calls, R7-7)
    ('dagflow', '_register_dagflow_tools'),
    # Register the proactivity pack (when-to-intervene scoring + bench, R7-8)
    ('proactivity', '_register_proactivity_tools'),
    # Register the "More hands" expansion (v5.4)
    ('hands2', '_register_hands2_tools'),
    # Register the "More depth" expansion (v5.5)
    ('hands3', '_register_hands3_tools'),
    # Register the "Wider reach" expansion (v5.11) — runs after
    # skills_media so screen_capture's screenshot_region replaces the older one
    ('hands4', '_register_hands4_tools'),
    # Register the v5.12 expansion
    ('hands5', '_register_hands5_tools'),
    # Register the "Edit, play, create" wave (v5.13)
    ('play', '_register_play_tools'),
    # Register the "Creativity, deepened" wave (v5.14)
    ('create2', '_register_create2_tools'),
    # Register the NOSE sensing wave (v5.20)
    ('nose', '_register_nose_tools'),
    # Register the "Hands II" wave (v5.21)
    ('hands6', '_register_hands6_tools'),
    # Register the Phase-2 section-4 wave (v5.30, 66 new tools)
    ('s4', '_register_s4_tools'),
    # Register the Phase-2 wave-2 packs (v5.31, 28 new tools)
    ('wave2', '_register_wave2_tools'),
    # Register the Phase-2 wave-3 packs (v5.32, 80 new tools)
    ('wave3', '_register_wave3_tools'),
    # Register the tool router's dispatcher (v5.33)
    ('toolbox', '_register_toolbox_tools'),
    # Conversion pass, Wave 6 registration tidy move: the Wave 6
    # pack now rides this call-site line beside the other packs,
    # instead of a hook at the end of _register_toolbox_tools.
    ('wave6', '_register_wave6_tools'),
    # Conversion pass, combined W4/W5 registration tidy move: the
    # W4/W5 pack likewise rides this call-site line beside the
    # other packs, instead of a hook at the end of
    # _register_toolbox_tools.
    ('conv_w45', '_register_conv_w45_tools'),
    # Triage Wire Build 1: the Study top 3 (st_deck / st_schedule /
    # st_practice) as live tools, beside the other packs.
    ('study_t1', '_register_study_t1_tools'),
    # Triage Wire Build 2: Creative/Developer/Social candidates #4-#7
    # (cr_ledger / cr_edit / dv_codebase / so_vcard) as live tools,
    # beside the other packs.
    ('triage2', '_register_triage2_tools'),
    # R19-1 (Huanshere/VideoLingo inspiration): the audio half of the
    # video-localisation pipeline (transcribe -> translate -> dub) as a
    # live tool, beside the subtitle tools.
    ('audio_localise', '_register_audio_localise_tools'),
    # R19-2 (google/or-tools inspiration): a combinatorial optimisation
    # engine in own stdlib code (TSP/VRP, bin packing, knapsack, shortest
    # path, assignment, max flow, LP, constraint solving) as a live tool,
    # beside the planning tools.
    ('optimise', '_register_optimise_tools'),
    # R19-3 (aureuserp/aureuserp inspiration): a unified local business
    # record in own stdlib code (customers, leads, products, stock,
    # sales/purchase orders, invoices, payments, employees, projects)
    # as a live tool, beside the planning tools.
    ('erp', '_register_erp_tools'),
    # R19-4 (nexmoe/VidBee inspiration): chat with your media — Q&A and
    # summary over a transcript (from an audio_localise project or an
    # operator-supplied SRT/VTT/plain-text file), answered by the
    # product's own Gemini key, as a live tool, beside the subtitle
    # tools. No downloader, no diarization, no video processing.
    ('media_chat', '_register_media_chat_tools'),
    # R19-5 (al1abb/invoify inspiration): an invoice generator — render
    # an erp invoice as a PDF (three templates: classic, modern,
    # minimal) with a minimal stdlib PDF writer, and send it by email
    # with the PDF attached (the send_email tool, extended with an
    # optional `attachments` property), as a live tool, beside the
    # planning tools. The invoice record itself lives in erp; this tool
    # renders and sends it. No web app, no tax computation.
    ('invoice', '_register_invoice_tools'),
    # R19-6 (tolgee/tolgee-platform inspiration): a local UI string
    # localization manager — projects, keys, translations per language,
    # import/export in JSON/PO/XLIFF, and machine translation with the
    # product's own Gemini key, as a live tool, beside the planning
    # tools. No web app, no ee/ code (the Tolgee ee/ carve-out is the
    # non-commercial Tolgee EE License v1 and is EXCLUDED), no
    # in-context editing, no audio translation (audio_localise already
    # does that).
    ('translations', '_register_translations_tools'),
    # R19-7 (Donkie/Spoolman inspiration): a local 3D-printer filament
    # spool inventory — vendors, filament types, spools with remaining
    # weight and length, usage recording and low-stock warnings, over a
    # hash-chained ledger, as a live tool, beside the planning tools.
    # Free by design (app-state). No web app, no server, no printer
    # control (OctoPrint / Klipper / Moonraker), no SpoolmanDB, no QR
    # labels.
    ('filament', '_register_filament_tools'),
    # R19-8 (Django-CRM/Django-CRM inspiration): a deal pipeline and
    # support tickets on erp customers — pipelines with seeded stages,
    # deals with probability and aging, tickets with priority and SLA
    # targets, over a hash-chained ledger, as a live tool, beside the
    # planning tools. Free by design (app-state). No web app, no server,
    # no multi-user, no customer/lead/invoice records (erp keeps those).
    ('deals', '_register_deals_tools'),
    # R19-10 (VROOM-Project/vroom inspiration): advanced vehicle routing
    # in own stdlib Python — time-windowed routes (VRPTW),
    # pickup-and-delivery shipments, multi-depot heterogeneous fleets and
    # vehicle skills, over a hash-chained ledger, as a live tool, beside
    # the planning tools. Free by design (app-state). No Vroom binary, no
    # routing engine, no network call.
    ('vehicle_routes', '_register_vehicle_routes_tools'),
    # SOCIAL Phase 1 (approved design, WhatsApp first): WhatsApp send
    # via the official wa.me Click to Chat link, the owner sending by
    # voice (Windows Voice Access presses Enter at his spoken command).
    # VYUK never sends, types nothing and presses nothing. Confirm-
    # gated; receipts only. D-06: outside SO-3.1's scope, because
    # nothing is automated.
    ('social_send', '_register_social_send_tools'),
    # Click-anywhere Batch 1 (Plan 9): one tool, click_anywhere —
    # click, type, press, navigate, read, or observe anywhere, in a
    # browser (extension, then CDP, then sight+click) or a desktop
    # app (UIA + sight+click as the primary route). Registry record
    # carries playbook_opt_out: True (M-24).
    ('click_anywhere', '_register_click_anywhere_tools'),
    # GAPS-8 (BLOCKS #7): scheduled agents — the OperatorManifest +
    # OperativeAgent layer over the existing APScheduler engine, plus
    # the morning digest (7am, Asia/Calcutta, setup-only, reads +
    # speaks, no effectful action). setup_digest / cancel / run_now
    # are confirm-gated; list / status / digest are free reads.
    ('scheduled_agents', '_register_scheduled_agents_tools'),
    # Triage Wire Build 3: the six remaining non-decision-gated WIRE
    # leaves (bz76_identity / fl_redact / ac_select / cr_marking /
    # dv_doctor row is a page surface, not a tool / st_workspace)
    # as live tools, beside the other packs.
    ('triage3', '_register_triage3_tools'),
    # Triage Wire Build 4: the two decision-gated WIRE leaves
    # (fl_playbook / so_ledger) as live tools, beside the other
    # packs. Decisions: loop playbook store is J9 (finance playbook
    # is finance-tool only); social ledger is the interim single
    # store until the Wave 6 vault arrives.
    ('triage4', '_register_triage4_tools'),
    # Wave 6 integration: Builds 1-5 as live tools (harness 7 +
    # WORK 3 + MISS 3 + mission 6 = 19; AGENT deliberately has no
    # tool row — its caller is the session async goal path).
    # Registry 532 (v5.88 gate) -> 551.
    ('wave6_integration', '_register_wave6_integration_tools'),
    # Wave 7 Build 1: SANDBOX-light completion as live tools
    # (sandbox_run confirm-gated + sandbox_list read-only).
    # Registry 551 (v5.89 gate) -> 553.
    ('sandbox_w7', '_register_sandbox_w7_tools'),
    # Wave 7 Build 2: UNDO completion as live tools
    # (undo_cannot_list + undo_status read-only,
    # undo_last_change confirm-gated).
    # Registry 553 -> 556.
    ('undo_w7', '_register_undo_w7_tools'),
    # Wave 7 Build 3: SIM plan-effect prediction as live tools
    # (sim_predict_plan read-only + sim_predict_report
    # confirm-gated). Registry 556 -> 558.
    ('sim_w7', '_register_sim_w7_tools'),
    # Wave 7 Build 4: self-improvement PROPOSALS as live tools
    # (improvement_propose read-only + improvement_report
    # confirm-gated; proposals are never applied by either tool).
    # Registry 558 -> 560.
    ('proposals_w7', '_register_proposals_w7_tools'),
    # Wave 7 Build 5: P18 multiverse planner + compiler as live tools
    # (multiverse_plan + multiverse_compile read-only,
    # multiverse_compile_report confirm-gated; a compiled routine
    # is never executed by any tool).
    # Registry 560 -> 563.
    ('multiverse_w7', '_register_multiverse_w7_tools'),
    # Wave 8 Build 1: anticipation-grant PATH as live tools
    # (antic_suggest_cards + antic_grant_status read-only,
    # antic_cards_report confirm-gated; suggestion cards only —
    # the grant itself is NOT granted by any tool here).
    # Registry 563 -> 566.
    ('antic_w8', '_register_antic_w8_tools'),
    # Wave 8 Build 2: Track L remainder as live tools
    # (release_checklist + installer_validate read-only,
    # release_checklist_report confirm-gated; the report is not a
    # ship, and signing, feed hosting, and installer compilation
    # stay DEFERRED in every report).
    # Registry 566 -> 569.
    ('release_w8', '_register_release_w8_tools'),
    # Wave 8 Build 3: Phase 5 picks as live tools
    # (phase5_interface_describe + ent_shelf_list + plat_tools_record
    # + fin_plan_record read-only, port_roundtrip + phase5_report
    # confirm-gated; no transaction, no sync, no face, no fake data).
    # Registry 569 -> 575.
    ('phase5_w8', '_register_phase5_w8_tools'),
    # Wave 8 Build 4: feel, non-veto only, as live tools
    # (feel_validate + feel_orb_map read-only,
    # feel_report confirm-gated; a report is not an approval,
    # and the orb + Orbitron sightings stay OWED).
    # Registry 575 -> 578.
    ('feel_w8', '_register_feel_w8_tools'),
    # Wave 8 Build 5: learning-last continuation as live tools
    # (learnlast_spec read-only, learnlast_report confirm-gated;
    # a spec changes nothing and is never applied by these
    # tools, and unproven or mixed input refuses whole).
    # Registry 578 -> 580.
    ('learnlast_w8', '_register_learnlast_w8_tools'),
    # First-open continuous opt-in as live tools
    # (continuous_first_open + continuous_choice_status
    # read-only, continuous_record_choice + continuous_delete_data
    # confirm-gated; the choice mechanism only — no continuous
    # capture, screen memory, or clipboard history is wired here,
    # and the delete is per-feature only, never the PF-3 global
    # rule, which is still open).
    # Registry 580 -> 584.
    ('firstopen_w9', '_register_firstopen_w9_tools'),
    # Wave 9 Build 1: wellbeing, human-first, sensing-free
    # suggestions only (well_break_plan + well_reminder +
    # well_winddown + well_day_summary read-only,
    # well_record_plan confirm-gated; a record schedules
    # nothing, measures nothing, and prescribes nothing, and
    # no diagnosis, mood inference, biometric, or treatment
    # claim is wired here).
    # Registry 584 -> 589.
    ('well_w9', '_register_well_w9_tools'),
    # Wave 9 Build 2: communications deltas, draft and confirm
    # only (comms_draft_message + comms_check_recipient +
    # comms_send_readiness read-only, comms_record_draft
    # confirm-gated; nothing is sent, no bulk message is
    # built, no send pipeline is invented here).
    # Registry 589 -> 593.
    ('comms_w9', '_register_comms_w9_tools'),
    # Wave 9 Build 3: local IoT deltas, descriptors and
    # records only (iot_local_device + iot_device_state +
    # iot_network_check read-only, iot_record_device
    # confirm-gated; nothing is reached, polled, or
    # controlled, no hardware is moved, no network is
    # scanned, local and administered networks only).
    # Registry 593 -> 597.
    ('iotl_w9', '_register_iotl_w9_tools'),
    # Wave 9 Build 4: hardware-adjacent deltas, descriptors
    # and records only (hardware_inventory + hardware_plan +
    # hardware_safety read-only, hardware_record_plan
    # confirm-gated; nothing moves, nothing is sized, rated,
    # or signed off, and a move step without a named
    # independent physical stop refuses whole).
    # Registry 597 -> 601.
    ('hardware_w9', '_register_hardware_w9_tools'),
    # Wave 9 Build 5: accessibility deltas beyond the
    # shipped Accessibility edition, under the dignity bar
    # and S-6 where capture is involved (access_text_clarity
    # + access_window_describe + access_voice_first_routine
    # read-only, access_record_preference confirm-gated;
    # nothing read live, nothing inferred, no disability or
    # health label, no score, and no capture built here).
    # Registry 601 -> 605.
    ('access_w9', '_register_access_w9_tools'),
    # Wave 9 Build 6: network deltas, read-only and
    # own-network forms (net_network_descriptor +
    # net_network_status + net_readonly_check read-only,
    # net_record_network confirm-gated; nothing is scanned,
    # probed, connected, resolved, or reached, and no network
    # outside an own/administered label is described).
    # Registry 605 -> 609.
    ('net_w9', '_register_net_w9_tools'),
    # Wave 9 Build 7: P14 gaming batch, descriptors and
    # records only under the shipped v5.78 Gaming wall
    # (game_session_describe + game_macro_describe +
    # game_media_note read-only, game_record_gaming
    # confirm-gated; no game read, controlled, or
    # automated, no memory-reading, injection, cheat, or
    # anti-cheat evasion, macros single-player/co-op only,
    # GPL tools OBS/FFmpeg-GPL/LosslessCut drive-only).
    # Registry 609 -> 613.
    ('gaming_w9', '_register_gaming_w9_tools'),
    # Wave 9 Build 8: P15 workshop / P16 student / P17 dev
    # PILOT slices, descriptors and records only
    # (pilot_bench + pilot_build_steps +
    # pilot_study_session + pilot_revision_plan +
    # pilot_dev_workflow + pilot_review_note read-only,
    # pilot_record_pilot confirm-gated; nothing executed,
    # nothing read live, nothing deployed, nothing signed).
    # Registry 613 -> 620.
    ('pilot_w9', '_register_pilot_w9_tools'),
    # Wave 9 Build 9: P19 travel + P20 PILOT + P23
    # recipes/scoreboard, descriptors and records only
    # (travel_trip + travel_door_to_door + travel_transport
    # + travel_loadout + travel_scene + travel_spatial +
    # travel_sonify + travel_recipe + travel_scoreboard
    # read-only, travel_record confirm-gated; nothing
    # booked, paid, routed commercially, driven, rendered,
    # or moved, and no numeric score invented).
    # Registry 620 -> 630.
    ('travel_w9', '_register_travel_w9_tools'),
    # Wave 9 Build 10: queued graduations 1, TENSION
    # graduation step 1 + graduation ledger, descriptors
    # and records only (grad_describe read-only, carrying
    # the gate, retention, indicator, and ledger
    # descriptors by kind — toolbox indirection, because
    # the fitted budget stood at 239,334 B after Build 9
    # with about 666 B of headroom, so five separate tools
    # do not fit; grad_record confirm-gated; nothing
    # captured, granted, chosen for any user, or decided;
    # PF-3 stays open).
    # Registry 630 -> 632.
    ('grad_w9', '_register_grad_w9_tools'),
    # Wave 10 Build 1: P23 release spine — multiple
    # install routes, verifiable releases (structure
    # only), release train with gates, descriptors and
    # records only (release_describe read-only, carrying
    # the route, verify, and train descriptors by kind —
    # toolbox indirection, because the fitted budget
    # stood at 239,624 B on stage after Wave 9 with only
    # about 376 B of headroom, so separate tools do not
    # fit; release_record confirm-gated; nothing
    # released, signed, compiled, published, fed, or
    # decided; ruling 34 stays open, signing stays
    # deferred, the feed still has no host).
    # Registry 632 -> 634.
    ('release_w10', '_register_release_w10_tools'),
    # Wave 10 Build 2: P23 trust + help + changelog
    # descriptors and records only (trust_describe
    # read-only, carrying the trust, transparency, help,
    # issues, runbook, changelog, and audience descriptors
    # by kind — toolbox indirection, because the fitted
    # budget is already over its 240,000 B hard cap after
    # Wave 10 Build 1 (240,286 B), so separate tools would
    # only add to the breach and no schema is gutted here
    # to chase bytes; trust_record confirm-gated; nothing
    # sent, published, fed, claimed compliant, or shipped;
    # ruling 34 stays open).
    # Registry 634 -> 636.
    ('trust_w10', '_register_trust_w10_tools'),
    # Wave 10 Build 3: P23 first-run cluster descriptors
    # and records only (firstrun_describe read-only,
    # carrying the exit, first_command, nosetup,
    # playground, and fix descriptors by kind — toolbox
    # indirection, because the fitted budget is already
    # over its 240,000 B hard cap after Wave 10 Build 1
    # (240,286 B, grown by Build 2 to 241,046 B), so
    # separate tools would only add to the breach and no
    # schema is gutted here to chase bytes;
    # firstrun_record confirm-gated; nothing exited, run,
    # fixed, installed, applied, sent, or published;
    # ruling 34 stays open; no conformance claim).
    # Registry 636 -> 638.
    ('firstrun_w10', '_register_firstrun_w10_tools'),
    # Wave 10 Build 4: P23 accessibility conformance
    # evidence pipeline, descriptors and records only
    # (accessconf_describe read-only, carrying the
    # evidence-ledger and conformance-statement
    # descriptors by kind — toolbox indirection, because
    # the fitted budget is already over its 240,000 B
    # hard cap after Wave 10 Build 1 (240,286 B, grown by
    # Build 2 to 241,046 B and by Build 3 to 241,768 B),
    # so separate tools would only add to the breach and
    # no schema is gutted here to chase bytes;
    # accessconf_record confirm-gated; nothing claimed
    # conformant, no audit performed, no live test run,
    # no live screen read; ruling 34 stays open).
    # Registry 638 -> 640.
    ('accessconf_w10', '_register_accessconf_w10_tools'),
    # Libwiring sweep (2026-11-01): teach + wallpaper funnel tools.
    # Registry 641 -> 645.
    ('libwire', '_register_libwire_tools'),
    # §7 consumers batch 1 (2026-11-02): funnel tools for the §7
    # engines — menus (item 3), file dialogs (item 4), system
    # surfaces (item 7), app adapters (item 9).
    # Registry 645 -> 649.
    ('s7consumers', '_register_s7consumers_tools'),
    # §7 consumers batch 2 (2026-11-02): funnel tools for the
    # remaining §7 engines — drag-drop (item 5), monitors
    # (item 6), action journal (item 8). Registry 649 -> 652.
    ('s7consumers2', '_register_s7consumers2_tools'),
    # §7 consumers batch 8 (2026-11-02): funnel tool for the
    # context-menu trigger (item-3 follow-up). Registry 652 -> 653.
    ('s7consumers3', '_register_s7consumers3_tools'),
)

for _pack_name, _registrar in PACKS:
    getattr(tools, _registrar)()
# §7 consumers batch 1 (2026-11-02): the funnel's adapter tool
# resolves through the three landed adapters — register them
# here, next to the tool registrations, so adapter_action
# resolves real phrases in production instead of falling back
# to generic controls for every app (the registry is empty
# until something registers; nothing did until this batch).
# Idempotent: a duplicate app_id refuses without storing, so
# re-registration is a no-op. Shippability stays False until
# field version pins land — registration alone ships nothing.
from ..desktop.chrome_adapter import register_chrome_adapter  # Phase 3 S7b: adapter moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop.explorer_adapter import register_explorer_adapter  # Phase 3 S7b: adapter moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
from ..desktop.vscode_adapter import register_vscode_adapter  # Phase 3 S7b: adapter moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
register_chrome_adapter()
register_explorer_adapter()
register_vscode_adapter()
tools.load_plugins()


# GAPS-23 (F-V7): import-time agreement between the registry and
# the single catalogue source (core/catalogue_groups_data.py).
# Runs after the pack tail AND tools.load_plugins(), so plugin
# tools are registered before the check. Every registered tool
# must be grouped, or explicitly excused (playbook_opt_out, or a
# plugin-loaded tool recognised by the plugin gate class); every
# grouped name must be registered. A drift raises loudly at
# import instead of silently dropping a tool from the routers
# (the "More skills" fallback) or from the catalogue. This is
# the check the old AST re-parse could never be — the re-parse
# read the same file the tables lived in, so it agreed by
# construction with nothing.
def _check_catalogue_agreement() -> None:
    from ..catalogue_groups_data import GROUP_MEMBERS
    from .tools_gate_egress import _plugin_gate_class

    grouped: set[str] = set()
    for _names in GROUP_MEMBERS.values():
        grouped.update(_names)
    registered = set(tools._tools)
    ungrouped = registered - grouped
    excused = {n for n in ungrouped
               if tools._tools[n].get("playbook_opt_out")
               or _plugin_gate_class(n) is not None}
    if ungrouped - excused:
        raise ValueError(
            "tools_registry: registered tools with no catalogue "
            f"group and no excuse: {sorted(ungrouped - excused)} — "
            "add them to core/catalogue_groups_data.py or mark "
            "playbook_opt_out")
    if grouped - registered:
        raise ValueError(
            "tools_registry: catalogue groups name tools that are "
            f"not registered: {sorted(grouped - registered)}")


_check_catalogue_agreement()
