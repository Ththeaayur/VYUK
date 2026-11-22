"""The single source of truth for tool gate classification (GAPS-23, F-V3).

Before this module the gate classification lived in five parallel
places that had to agree by hand: the three static sets
(_CONFIRM_GATE_TOOLS / _READ_ONLY_TOOLS / _FREE_BY_DESIGN, one file
each), the read-action split (_is_read_action) and the gate-action
branches inside _needs_confirmation — plus the classification
snapshot JSON. They DID agree for every probed call, but nothing in
construction enforced it: the three tools whose default call is a
read while the tool itself is confirm-gated for its mutating
actions (power_plans, task_scheduler_control, vpn_control) were
classified "confirm" by the set and the snapshot and "read" by
_is_read_class for the same empty call, and only a test pinned the
sets' disjointness.

Now: the three sets live HERE, moved verbatim, and CLASSIFICATION
is built from them once. The per-call verdicts come from ONE
function, classify_call(name, args), which consults the same table
plus the two action splits — the read split (_is_read_action) and
the gate split (_gate_action) — both also housed here, adjacent,
consumed by no one else. Every gate consumer derives from this
module: _needs_confirmation's base IS classify_call's
needs_confirmation (its money / plugin / taint / provenance-lock
branches are overlays on top, not a second classification), and
_is_read_class IS classify_call's is_read plus the plugin gate
class (a plugin's own capability declaration, an input, not a
parallel classification of built-in tools). The old set modules
are re-export shims; the old functions remain importable at their
old addresses and delegate here.

Deliberately NOT unified: the hard-taint branch in
_needs_confirmation exempts only _READ_ONLY_TOOLS members (plus
the screen_look / take_screenshot / get_pixel_color special
cases), not read-actions of action-dispatched tools — so a
tainted power_plans list still gates today. Broadening that
exemption to action-aware reads would change taint behaviour for
every read action of every action-dispatched tool; that is a
parent decision, not a refactor. The taint branch still derives
from this table (it reads _READ_ONLY_TOOLS, now defined here),
so it is a narrower slice of the one source, not a second one.

The static class of a tool is what the set says it is: "confirm"
means the tool CAN require confirmation for some call (its
mutating actions); whether THIS call needs it is classify_call's
job. That is why power_plans is "confirm" in the table while
classify_call("power_plans", {}) is (needs_confirmation=False,
is_read=True) — the two answers are different questions, answered
by one function from one table.
"""

from collections import namedtuple
from typing import Any

from .tools_gate_shell import (
    _HOTKEY_SHELL_KEYS,
    _arguments_invoke_interpreter,
    _foreground_is_shell,
    _is_executable_path,
    _is_shell_launch,
    _normalized_hotkey_tokens,
)

_CONFIRM_GATE_TOOLS = frozenset({
    "power_control",        # shutdown / restart / log off
    "empty_recycle_bin",    # irreversible
    "uninstall_app",        # removes software
    "winget_install",       # installs software
    "winget_upgrade_all",   # upgrades everything
    "close_application",    # can lose unsaved work
    "process_control",      # can kill processes
    "service_control",      # starts/stops system services
    "file_control",         # includes delete (v5.48, programme item 27:
                            # to the central trash root — see the
                            # file_control description)
    "run_command",          # arbitrary shell
    # Round-3 fix #4 (T12): running a saved plan executes its
    # steps — the tool itself is confirm-gated, every run asks
    # first. Membership here ALSO bars the always-allow shortcut
    # structurally: confirm_gate_allowlist._in_allowlistable_base
    # returns False for every _CONFIRM_GATE_TOOLS member, so no
    # standing grant can ever cover a plan run. The steps keep
    # their own gates; this gate is the run itself.
    "run_plan",
    # Fix #32 follow-up (parent ruling, 2026-11-19): running a
    # saved anticipation routine executes its steps, exactly
    # like run_plan — same gate, same reason. Fix #32 added
    # the tool with a snapshot entry claiming "executor
    # dispatch" but never put it in any gate set, so it ran
    # ungated (freedom by omission) and six suites failed on
    # the unclassified tool. Confirm-gated, like run_plan.
    "run_anticipation_routine",
    "clean_temp_files",     # deletes temp files in bulk (v4.4)
    "batch_rename",         # renames many files at once (v4.4)
    "send_email",           # mail leaves the machine (v5.2)
    "create_calendar_event",  # writes to the user's calendar (v5.2)
    # Stage 0 features batch 2 (Group E, 2026-11-22): the
    # calendar actions — cancelling deletes an event, moving
    # rewrites one; both ask first, like creating one.
    "cancel_calendar_event",
    "reschedule_calendar_event",
    # Round-3 section F fix F-F303 (2026-11-21): a toast pops on
    # the user's screen outside the conversation, with no ask and
    # no permission to revoke — the sharp half of F-F303 (the
    # gated-three half is a parent ruling: the confirm ask IS the
    # per-call permission there). Confirm-gated, so every
    # notification asks first; membership here ALSO bars the
    # always-allow shortcut structurally (confirm_gate_allowlist
    # ._in_allowlistable_base returns False for every
    # _CONFIRM_GATE_TOOLS member), so no standing grant can ever
    # cover a notification. The "output to user" siblings stay
    # free: speech is the reply channel itself (gating
    # speak_text would ask before every spoken reply), while a
    # toast is a screen pop the user did not ask to see.
    "notify_user",
    "write_file",           # overwrites/creates files wholesale (v5.3)
    "study_export_deck",    # writes a caller-chosen .apkg file
                            # (Triage Wire Build 1; write_file parity)
    "study_session",        # full study flow incl. .apkg export
                            # (Triage Wire Build 1; write_file parity)
    "creative_rights_add",  # writes the app's own rights ledger
                            # (Triage Wire Build 2; write_file parity)
    "creative_subtitle_export",  # writes a caller-chosen subtitle
                            # file (Triage Wire Build 2; write_file
                            # parity)
    "vcard_export",         # writes a caller-chosen .vcf file
                            # (Triage Wire Build 2; write_file parity)
    "creative_mark_image",  # writes a caller-chosen marked JPEG
                            # (Triage Wire Build 3; write_file parity)
    "study_plan",           # writes plan.json into the workspace
                            # (Triage Wire Build 3; write_file parity)
    "study_progress",       # writes progress.json into the workspace
                            # (Triage Wire Build 3; write_file parity)
    "study_exam_prep",      # writes exam_prep.json into the workspace
                            # (Triage Wire Build 3; write_file parity)
    "finance_playbook_delete",  # deletes a finance playbook bullet
                            # (Triage Wire Build 4; deletion is
                            # consequential, so it asks first)
    "social_entry_delete",  # deletes one ledger entry by kind + id
                            # (Triage Wire Build 4; deletion is
                            # consequential, so it asks first)
    "social_send",          # SOCIAL Phase 1 (approved design,
                            # WhatsApp first): the tool opens the
                            # wa.me chat and arms the owner's voice
                            # send — the human approves the exact
                            # text + the exact recipient before
                            # anything is opened (the voice
                            # instruction alone is not approval).
    "scheduled_agents",     # GAPS-8 (BLOCKS #7): the tool is listed
                            # as a whole, but the args-aware branch
                            # in tools_gate.py narrows the gate to
                            # setup_digest / cancel / run_now /
                            # save_routine (they create, remove,
                            # immediately run, or save a persisted
                            # job/routine); list / status / digest
                            # are free reads and run_routine is free
                            # dispatch (its gated steps still ask at
                            # run time through the funnel).
                            # The send itself is the owner's action
                            # via Windows Voice Access; VYUK types
                            # nothing and presses nothing.
    # Wave 6 integration: the harness transitions + mission
    # staging are state changes a human must approve first —
    # the funnel refuses these without a confirm handler, and
    # the handler never approves on its own.
    "extension_register",   # registers an extension manifest
                            # (Wave 6 Build 1; directory is a
                            # write-destination arg, see below)
    "extension_approve",    # human approval record (Wave 6 Build 1)
    "extension_enable",     # enables an approved extension
                            # (Wave 6 Build 1)
    "extension_revoke",     # terminal revocation (Wave 6 Build 1)
    "mission_run_job",      # stages a mission plan for the next
                            # goal run — staging only, never
                            # execution (Wave 6 Build 5)
    "sandbox_run",          # runs file actions inside a bounded
                            # light sandbox directory (Wave 7
                            # Build 1; sandbox_dir is a
                            # write-destination arg, see below)
    "undo_last_change",     # undoes the last undoable change
                            # (Wave 7 Build 2; the gate skips and
                            # lists non-undoable records, and
                            # reports only verified restorations)
    "sim_predict_report",   # writes a prediction report to a
                            # caller-chosen .json file (Wave 7
                            # Build 3; the plan itself is never
                            # executed by the report tool)
    "improvement_report",   # writes a proposal record to a
                            # caller-chosen .json file (Wave 7
                            # Build 4; the proposal itself is never
                            # applied by the report tool)
    "multiverse_compile_report",  # writes a compiled routine +
                            # dry-run report to a caller-chosen
                            # .json file (Wave 7 Build 5; the
                            # routine itself is never executed
                            # by the report tool)
    "release_checklist_report",  # writes a checklist report to
                            # a caller-chosen .json file (Wave 8
                            # Build 2; the report is not a ship,
                            # and signing/feed/compilation stay
                            # DEFERRED in it)
    "antic_cards_report",   # writes a suggestion-card record to
                            # a caller-chosen .json file (Wave 8
                            # Build 1; the grant itself is NOT
                            # granted by the report tool, and no
                            # card action is executed by it)
    "port_roundtrip",       # writes a port bundle to a
                            # caller-chosen .json file (Wave 8
                            # Build 3; single-device round-trip
                            # only, never automatic, never a sync)
    "phase5_report",        # writes one Phase 5 cluster report to
                            # a caller-chosen .json file (Wave 8
                            # Build 3; nothing is executed by it)
    "feel_report",          # writes a feel validation report to
                            # a caller-chosen .json file (Wave 8
                            # Build 4; the report is not a feel
                            # approval, and no asset is bundled)
    "learnlast_report",     # writes a learning-last spec record
                            # to a caller-chosen .json file
                            # (Wave 8 Build 5; the report is not
                            # an application, and nothing is
                            # deployed, executed, or changed)
    "continuous_record_choice",  # writes a continuous choice
                            # record to a caller-chosen .json
                            # file (first-open opt-in; writing
                            # the record chooses nothing for
                            # the user and starts nothing)
    "continuous_delete_data",  # deletes the regular files
                            # inside a caller-chosen folder
                            # holding kept continuous data
                            # (first-open opt-in; per-feature
                            # delete only, never the PF-3
                            # global rule, which is still open)
    # Wiring item 1 (v6.0.0 post-ship): the four true delete
    # tools — each destroys a record that cannot be undone
    # (no trash path), so each asks first under C-14 like
    # every other delete. Previously ungated; the wiring
    # inventory (wiring_inventory_v600.md) found the gap.
    "delete_plan",          # deletes a plan entirely, no undo
    "delete_routine",       # deletes a saved routine, no undo
    "delete_snippet",       # deletes a saved snippet, no undo
    "delete_window_layout",  # deletes a saved layout, no undo
    "well_record_plan",     # writes a wellbeing plan record
                            # to a caller-chosen .json file
                            # (Wave 9 Build 1; recording
                            # schedules nothing, measures
                            # nothing, prescribes nothing)
    "comms_record_draft",   # writes a message draft record
                            # to a caller-chosen .json file
                            # (Wave 9 Build 2; recording
                            # sends nothing, and no send
                            # pipeline is invented here)
    "iot_record_device",    # writes a local device record
                            # to a caller-chosen .json file
                            # (Wave 9 Build 3; recording
                            # reaches nothing, controls
                            # nothing, moves no hardware)
    "hardware_record_plan", # writes a hardware plan record
                            # to a caller-chosen .json file
                            # (Wave 9 Build 4; recording
                            # moves nothing, performs no
                            # step, claims no safety)
    "access_record_preference", # writes an accessibility
                            # preference record to a
                            # caller-chosen .json file
                            # (Wave 9 Build 5; recording
                            # labels a setting, never a
                            # person, and changes nothing)
    "net_record_network",   # writes a network record to a
                            # caller-chosen .json file
                            # (Wave 9 Build 6; recording
                            # scans nothing, connects to
                            # nothing, reaches nothing)
    "game_record_gaming",   # writes a gaming record to a
                            # caller-chosen .json file
                            # (Wave 9 Build 7; recording
                            # reads no game, controls no
                            # game, automates no input,
                            # bundles no tool)
    "pilot_record_pilot",   # writes a pilot record to a
                            # caller-chosen .json file
                            # (Wave 9 Build 8; recording
                            # runs nothing, reads nothing
                            # live, deploys nothing,
                            # signs nothing)
    "travel_record",        # writes a travel record to a
                            # caller-chosen .json file
                            # (Wave 9 Build 9; recording
                            # books nothing, pays nothing,
                            # routes nothing, drives nothing,
                            # renders nothing, moves nothing)
    "grad_record",          # writes a graduation record to
                            # a caller-chosen .json file
                            # (Wave 9 Build 10; recording
                            # captures nothing, grants
                            # nothing, chooses nothing for
                            # any user, decides nothing;
                            # PF-3 stays open)
    "release_record",       # writes a release record to a
                            # caller-chosen .json file
                            # (Wave 10 Build 1; recording
                            # releases nothing, signs
                            # nothing, compiles nothing,
                            # publishes nothing, decides
                            # nothing; ruling 34 open)
    "trust_record",         # writes a trust record to a
                            # caller-chosen .json file
                            # (Wave 10 Build 2; recording
                            # sends nothing, publishes
                            # nothing, claims no compliance
                            # or conformance, ships no
                            # version; ruling 34 open)
    "firstrun_record",      # writes a first-run record to
                            # a caller-chosen .json file
                            # (Wave 10 Build 3; recording
                            # exits nothing, runs nothing,
                            # fixes nothing, installs
                            # nothing, applies nothing;
                            # ruling 34 open, no
                            # conformance claimed)
    "accessconf_record",    # writes an evidence record to a
                            # caller-chosen .json file
                            # (Wave 10 Build 4; recording
                            # claims no conformance,
                            # performs no audit, runs no
                            # live test, reads no live
                            # screen; ruling 34 open)
    "download_file",        # pulls arbitrary bytes onto disk (v5.3)
    "set_startup_program",  # persistence — survives reboots (v5.3)
    "paste_text",           # types clipboard text into the focused
                            # app, incl. terminals (v5.3)
    "create_restore_point",  # system-level change, admin context (v5.4)
    "shred_file",           # overwrites then deletes — unrecoverable (v5.5)
    "forget_wifi",          # deletes a saved network AND its stored
                            # password — persistent (v5.9, sweep M2)
    "paste_snippet",        # pastes saved text into the focused app,
                            # incl. terminals (v5.11; paste_text parity)
    "type_clipboard",       # types clipboard text into the focused
                            # app as keystrokes, incl. terminals
                            # (v5.42; paste_text parity, H16)
    "set_process_priority",  # reprioritises other processes —
                            # process_control parity (v5.11)
    "replace_in_file",      # rewrites file contents in place —
                            # write_file parity (v5.12)
    # v5.47 (programme item 11, C5): the taint-only writers are
    # gated UNTAINTED too. Before this wave these tools sat in
    # NEITHER classification set: they ran free untainted and only
    # the injection-taint branch ever asked about them, so an
    # ordinary (untainted) model call could write files, change
    # system state or plant persistence with no human in the loop.
    # Every entry below was verified against its handler (not its
    # name). First group: writers to model-chosen paths outside
    # the app's own data store — most are _WRITE_DESTINATION_ARGS
    # entries (item 9's cross-check list), the rest verified by
    # hand (compress_image writes beside its input; audio_fade,
    # image_contact_sheet and screenshot_to_text_note were not in
    # item 9's map but write to model-chosen folders/files all
    # the same; folder_watch_cook's run_once renames / moves /
    # zips files inside a model-chosen folder).
    "convert_image", "resize_image", "crop_image", "rotate_image",
    "adjust_image", "filter_image", "watermark_image", "add_border",
    "make_collage", "kaleidoscope_image", "duotone_image",
    "pixelate_image", "glitch_image", "strip_image_metadata",
    "compress_image", "merge_pdfs", "rotate_pdf", "split_pdf",
    "pdf_extract_images", "pdf_merge_bookmarks", "pdf_split_by_text",
    "make_sprite_sheet", "batch_watermark", "image_contact_sheet",
    "audio_trim",
    "audio_fade", "audio_speed_change", "silence_trim",
    "photo_face_blur", "zip_control", "sync_folder",
    "sync_folders_two_way",  # R9-4 mutagen port: two-way sync can overwrite and delete on BOTH sides
    "organise_photos",  # R9-8 elodie port: sorting a camera dump MOVES every photo into dated folders
    "create_shortcut", "env_backup", "download_email_attachment",
    "screenshot_to_text_note", "folder_watch_cook",
    "disk_speed_test",  # writes a scratch file into a
                        # model-chosen folder (self-deleting, but
                        # the write itself is the side effect)
    # v5.47 (programme item 11): generative writers that save
    # files to a model-chosen output path (the _WRITE_DESTINATION_
    # ARGS generative family).
    "create_art", "fractal_art", "generate_melody", "compose_song",
    "generate_beat", "palette_image", "audio_to_midi",
    # R9-2 rustic adoption: creating a repository, backing files up
    # into it, and restoring from it all read the user's files and
    # write to a destination the operator chose — destructive-adjacent,
    # so each asks first, every time.
    "init_backup_repo", "backup_files", "restore_backup",
    # R11-1 ollama port: pulling a model downloads gigabytes from
    # the model library and deleting one destroys gigabytes — both
    # ask first, every time.
    "ollama_pull", "ollama_delete",
    # v5.47 (programme item 11): persistent SYSTEM state — the
    # handler changes machine configuration that survives the
    # session. The action-dispatched members (firewall_control,
    # fast_startup_control, hibernate_setting, task_scheduler_
    # control, vpn_control, env_var, game_server, stage_lighting,
    # billing) keep their read actions free
    # via the args-aware branches in _needs_confirmation (and
    # _is_read_action) below; membership here is what makes their
    # write actions — and the two new branches for power_plans /
    # network_control — gate even untainted. connect_wifi creates
    # a persistent Wi-Fi profile; ip_renew drops and rebinds the
    # machine's address; restore_point_before_changes plants a
    # system restore point; eject_drive / safe_eject_all remove
    # devices in a way software cannot undo; wake_on_lan powers
    # another machine on; print_file commits a file to paper.
    "firewall_control", "vpn_control", "task_scheduler_control",
    "fast_startup_control", "hibernate_setting", "power_plans",
    "network_control", "env_var", "connect_wifi", "ip_renew",
    "restore_point_before_changes", "eject_drive", "safe_eject_all",
    "wake_on_lan", "print_file", "game_server", "stage_lighting",
    # R18-1 (stripe/stripe-node inspiration): test-mode payment
    # plumbing — creating a test customer/subscription/payment link/
    # Checkout session/refund and cancelling a test subscription
    # write to Stripe, so they gate; the read actions stay free via
    # the args-aware branch in _needs_confirmation. Live mode is
    # refused inside the handler regardless of the gate.
    "billing",
    # R18-3 (IBM/mcp-context-forge inspiration): adding, removing,
    # enabling or disabling an MCP server and recording a review
    # write the server config or the policy store, so they gate;
    # the read actions stay free via the args-aware branch in
    # _needs_confirmation.
    "mcp_registry",
    # R18-4 (a2aproject/A2A inspiration): calling, cancelling or
    # forgetting an external agent sends to (or drops) a third
    # party, so they gate; the read actions stay free via the
    # args-aware branch in _needs_confirmation.
    "agent_call",
    # R18-5 (stanfordnlp/dspy inspiration): evaluating, tuning or
    # proposing sends the owner's prompts and dataset to the
    # owner's own configured Gemini endpoint and spends their LM
    # quota, so they gate; the read actions and the local
    # prompt/dataset edits stay free via the args-aware branch in
    # _needs_confirmation.
    "prompt_tune",
    # R19-1 (Huanshere/VideoLingo inspiration): translating or
    # running the full pipeline sends the owner's audio transcript
    # to the owner's own configured Gemini endpoint and spends their
    # LM quota, so they gate; the local project/transcribe/dub
    # actions and the reads stay free via the args-aware branch in
    # _needs_confirmation.
    "audio_localise",
    # R19-4 (nexmoe/VidBee inspiration): asking a question or
    # requesting a summary sends the owner's transcript to the
    # owner's own configured Gemini endpoint and spends their LM
    # quota, so they gate; the local media registration and the
    # reads stay free via the args-aware branch in
    # _needs_confirmation.
    "media_chat",
    # R19-5 (al1abb/invoify inspiration): sending an invoice SENDS
    # real mail through the owner's Gmail (with the PDF attached), so
    # it gates; the render, the templates, the seller record and the
    # reads stay free via the args-aware branch in
    # _needs_confirmation.
    "invoice",
    # R19-6 (tolgee/tolgee-platform inspiration): translating a key
    # sends the owner's text to the owner's own configured Gemini
    # endpoint and spends their LM quota, so it gates; the local
    # projects, keys, translations, import/export and the reads stay
    # free via the args-aware branch in _needs_confirmation.
    "translations",
    # R18-6 (fonoster/fonoster inspiration): placing a call or
    # answering one through the operator's SIP trunk sends to a
    # third party, so they gate; the local agent/flow edits, the
    # text run and the reads stay free via the args-aware branch
    # in _needs_confirmation.
    "voice_agent",
    # v5.47 (programme item 13, C3): persistence itself confirms —
    # a routine is stored instructions that can fire headless
    # later (routine_scheduler), so SAVING one asks the human even
    # though saving runs nothing today.
    "save_routine",
    # Conversion pass, combined W4/W5 registration (v5.64):
    # replay_programme executes programmes of her own tool
    # calls through the funnel — it must NOT be free or
    # read-only, even though each inner step is re-gated on
    # its own classification. Confirm-class is the honest
    # default: every replay asks the human first.
    "replay_programme",
    # Libwiring sweep (2026-11-01): teach_record_sequence confirms
    # on the save_routine precedent (persistence itself confirms
    # — a taught sequence is stored instructions that can replay
    # headless later, so SAVING one asks the human even though
    # saving runs nothing today); teach_replay_sequence confirms
    # on the replay_programme precedent (every replay asks first,
    # even though each inner step is re-gated on its own
    # classification).
    "teach_record_sequence", "teach_replay_sequence",
    # Wallpaper apply (2026-11-03, code-rulings delegation):
    # applying a wallpaper changes the user's desktop persistently
    # (SPIF_UPDATEINIFILE), so it asks first — the §7 consumers
    # precedent that tools which ACT are confirm-class, even
    # though resolution itself (wallpaper_state) stays read-only.
    "wallpaper_apply",
    # Wallpaper revert (2026-11-05, owner field report): reverting
    # changes the desktop persistently too, so it asks first, in
    # the same confirm class as apply — never silent.
    "wallpaper_revert",
    # §7 consumers batch 1 (2026-11-02): funnel tools for the §7
    # engines. INTENTIONAL — these tools ACT (menu invoke,
    # dialog drive, surface action, adapter resolution of
    # effectful targets), so they are confirm-class even though
    # each has a pure-read leg: the read legs are freed by
    # args-aware branches in _needs_confirmation, not by
    # reclassifying the tools as reads (classification records
    # set membership, and the tools' effectful paths must never
    # read as free).
    "menu_action", "file_dialog_action",
    "system_surface_action", "adapter_action",
    # §7 consumers batch 2 (2026-11-02): INTENTIONAL — 2 tools
    # added (confirm set grows by 2): drag_drop_action is
    # confirm-class for every call (no read leg), journal_action
    # is confirm-class for its effectful legs with its pure-read
    # legs (list, plan) freed by the args-aware branch in
    # _needs_confirmation. monitor_action is a pure read and
    # sits in _READ_ONLY_TOOLS, not here.
    "drag_drop_action", "journal_action",
    # §7 consumers batch 8 (2026-11-02): INTENTIONAL — 1 tool
    # added (confirm set grows by 1): context_menu_action is
    # confirm-class for its trigger/invoke legs, with its
    # pure-read leg (list of an already-open menu) freed by
    # the args-aware branch in _needs_confirmation.
    "context_menu_action",
})


_READ_ONLY_TOOLS = frozenset({
    "battery_health", "battery_info", "bluetooth_devices",
    "disk_health", "display_details", "dns_lookup",
    "file_hash", "find_duplicates", "find_files", "find_free_slots",
    "folder_size",
    "get_active_window", "get_activity_log", "get_crypto_price",
    "get_current_time_date", "get_plan", "get_stock_quote",
    "get_system_info", "get_todays_schedule", "get_unread_email",
    "get_upcoming_events", "get_weather", "get_window_controls",
    "get_morning_briefing",  # Round-3 fix Q6: the deterministic briefing tool is a read — it renders the fetched day, never writes
    "largest_files", "meeting_status",  # R7-4 meetily: read-only state report
    "companion_status", "companion_pairing",  # R7-5 kovrel: read-only state/pairing reports
    "list_custom_wake_models", "custom_wake_model_status",  # R7-6 micro-wake-word: read-only model inventory/state reports
    "list_workflows", "workflow_runs",  # R7-7 taskflow: read-only workflow inventory/run history
    "list_proactivity_scores", "proactivity_bench_results",  # R7-8 proactive-agent: read-only scored interventions/bench runs
    # v5.71 Life Admin: all three lifeadmin actions are reads /
    # guidance (link-outs, checklists, assembled plan) — no state
    # change, no capture, no network, no booking shape.
    "lifeadmin",
    # v5.72 Business: all three business actions are reads /
    # checks (state proof, paper render, signature check) — no
    # state change, no capture, no network, no send.
    "business",
    "list_audio_devices",
    "list_installed_apps", "list_plans", "list_routines",
    "list_services", "list_startup_programs", "ocr_file",
    "ocr_screen", "public_ip", "read_clipboard",
    "read_file", "read_pdf_text", "recall_day", "recall_memories",
    "recent_files", "registry_read", "search_email", "speed_test",
    "transcribe_audio",  # R7-4 meetily: reads one saved .wav, returns text
    "summarise_text",  # R8-3 sumy port: pure transform of caller-supplied text, no state change (Triage Wire Build 2 precedent)
    "breathing_guide",  # R8-4 screen-break port: pure timed breathing script, no timer started, nothing measured or stored
    "wormhole_status",  # R8-5 magic-wormhole port: pure read of the in-memory offers and transfer log
    "audio_to_midi_status",  # R8-7 basic-pitch port: pure read of the model file's presence + hash and the runtime's availability
    "list_backup_snapshots",  # R9-2 rustic adoption: pure read of the repository's snapshot list (needs RUSTIC_PASSWORD to open it, reads nothing else)
    "backup_status",  # R9-2 rustic adoption: pure read of the binary's presence + hash, the default repository, and the snapshot count
    "uia_find_control", "uia_read_tree", "watcher_status", "web_search", "wifi_info",
    # Triage Wire Build 1: study_practice composes problems purely
    # from caller-supplied materials — no store, file, or network
    # write of any kind, so it is provably side-effect-free.
    "study_practice",
    # Triage Wire Build 2: the five pure tools — a rights check
    # (ledger read), a subtitle edit (pure transform), a
    # timeline build (pure model), a codebase Q&A (static local
    # scan, no import/exec), and a vCard import (pure parse) —
    # none writes a store, a file, or the network.
    "creative_rights_check", "creative_subtitle_edit",
    "creative_timeline", "dev_codebase_qa", "vcard_import",
    # Triage Wire Build 3: the three pure tools — a Windows
    # identity read, a redacted diagnostics preview (composition
    # only, no send), and a forgiving selection (descriptor only,
    # no click/dispatch) — none writes a store, a file, or the
    # network.
    "session_identity", "diagnostics_preview", "forgiving_select",
    # Triage Wire Build 4: the five pure tools — a finance
    # playbook list, occasion/promises/balances reads, and a
    # nudge decision (suggestion only, never a sent message) —
    # none writes a store, a file, or the network.
    "finance_playbook_list", "social_occasion_upcoming",
    "social_promise_list", "social_balances", "social_nudge_check",
    # Wave 6 integration: the twelve pure tools — a harness
    # audit + status view, three WORK compositions, three MISS
    # compositions, and four mission compositions (brief, plan,
    # board view, journal-entry construction) — none writes a
    # store, a file, or the network; a plan is a proposal,
    # never an execution.
    "extension_audit", "extension_status",
    "workflow_distil", "workflow_build_plan",
    "workflow_to_playbook_steps",
    "miss_compound", "miss_capability_map", "miss_to_playbook_steps",
    "mission_build_brief", "mission_build_plan",
    "mission_evidence_board", "mission_journal_entry",
    # Wave 7 Build 1: listing an existing sandbox directory
    # — it never creates, writes, or deletes anything.
    "sandbox_list",
    # Wave 7 Build 2: the undo disclosure and status are
    # reads over the undo store — they change nothing.
    "undo_cannot_list", "undo_status",
    # Wave 7 Build 3: a plan-effect prediction applies a plan
    # to a throwaway sandbox copy, diffs it, and discards it —
    # it writes nothing the caller keeps, never promotes, and
    # never executes the plan, so it is side-effect-free.
    "sim_predict_plan",
    # Wave 7 Build 4: a proposal inspection validates a
    # caller-supplied diff + guard + evidence and dry-runs it
    # in a throwaway copy that is discarded — it writes nothing
    # the caller keeps, never applies, promotes, or deploys
    # anything, so it is side-effect-free.
    "improvement_propose",
    # Wave 7 Build 5: planning a multiverse and compiling a
    # caller-chosen branch validate proposals and dry-run
    # them in throwaway copies that are discarded — they
    # write nothing the caller keeps, never choose, apply,
    # promote, or deploy anything, so they are side-effect-free.
    "multiverse_plan", "multiverse_compile",
    # Wave 8 Build 1: suggestion cards from caller-supplied
    # context under a caller-supplied grant record, and grant
    # inspection — both read-only: they write nothing, capture
    # nothing, watch nothing, pre-warm nothing, execute
    # nothing, and grant nothing, so they are side-effect-free.
    "antic_suggest_cards", "antic_grant_status",
    # Wave 8 Build 2: checklist evaluation + installer
    # validation — both read-only: they verify nothing
    # themselves, compile nothing, sign nothing, host nothing,
    # and execute nothing, so they are side-effect-free.
    "release_checklist", "installer_validate",
    # Wave 8 Build 3: Phase 5 cluster reads — interface
    # descriptor validation, ENT shelf catalogue, programme
    # record validation, money plan/record validation — all
    # read-only: they write nothing, execute nothing, send
    # nothing, purchase nothing, and transact nothing, so they
    # are side-effect-free. (port_roundtrip and phase5_report
    # write files, so they are confirm-gated, not read-only.)
    "phase5_interface_describe", "ent_shelf_list",
    "plat_tools_record", "fin_plan_record",
    # Wave 8 Build 4: feel validation + orb mapping are
    # read-only: they validate data and map a caller-supplied
    # measured level, write nothing, render nothing, bundle
    # nothing, approve nothing, so they are side-effect-free.
    # (feel_report writes a file, so it is confirm-gated.)
    "feel_validate", "feel_orb_map",
    # Wave 8 Build 5: learning-last spec production is
    # read-only: it validates caller-supplied proven jobs
    # and builds a proposal, writes nothing, executes
    # nothing, deploys nothing, collects nothing, so it
    # is side-effect-free. (learnlast_report writes a
    # file, so it is confirm-gated.)
    "learnlast_spec",
    # First-open continuous opt-in: the prompt descriptor
    # and the choice inspection are read-only: they return
    # data, choose nothing, watch nothing, capture nothing,
    # start nothing, and persist nothing, so they are
    # side-effect-free. (continuous_record_choice writes a
    # file and continuous_delete_data deletes files, so
    # both are confirm-gated, not read-only.)
    "continuous_first_open", "continuous_choice_status",
    # Wave 9 Build 1 wellbeing descriptors are read-only:
    # they return a suggestion, schedule nothing, measure
    # nothing, and persist nothing, so they are
    # side-effect-free. (well_record_plan writes a file, so
    # it is confirm-gated, not read-only.)
    "well_break_plan", "well_reminder", "well_winddown",
    "well_day_summary",
    # Wave 9 Build 2 communications descriptors are
    # read-only: they return a draft, a recipient check,
    # or a readiness statement, send nothing, address
    # nothing from a guess, and persist nothing, so they
    # are side-effect-free. (comms_record_draft writes a
    # file, so it is confirm-gated, not read-only.)
    "comms_draft_message", "comms_check_recipient",
    "comms_send_readiness",
    # Wave 9 Build 3 local IoT descriptors are
    # read-only: they return a device descriptor, a
    # state summary, or a network-label check, reach
    # nothing, poll nothing, control nothing, scan
    # nothing, and persist nothing, so they are
    # side-effect-free. (iot_record_device writes a
    # file, so it is confirm-gated, not read-only.)
    "iot_local_device", "iot_device_state",
    "iot_network_check",
    # Wave 9 Build 4 hardware-adjacent descriptors are
    # read-only: they return an inventory record, a plan
    # descriptor, or a safety disclaimer, move nothing,
    # size nothing, rate nothing, sign nothing off, and
    # persist nothing, so they are side-effect-free.
    # (hardware_record_plan writes a file, so it is
    # confirm-gated, not read-only.)
    "hardware_inventory", "hardware_plan",
    "hardware_safety",
    # Wave 9 Build 5 accessibility descriptors are
    # read-only: they return a text note, a snapshot
    # description, or a routine of chosen steps, read
    # nothing live, infer nothing, label no one, score
    # nothing, and persist nothing, so they are
    # side-effect-free. (access_record_preference writes
    # a file, so it is confirm-gated, not read-only.)
    "access_text_clarity", "access_window_describe",
    "access_voice_first_routine",
    # Wave 9 Build 6 network descriptors are read-only:
    # they return a network descriptor, a status summary,
    # or a read-only check, scan nothing, connect to
    # nothing, resolve nothing, and persist nothing, so
    # they are side-effect-free. (net_record_network
    # writes a file, so it is confirm-gated, not
    # read-only.)
    "net_network_descriptor", "net_network_status",
    "net_readonly_check",
    # Wave 9 Build 7 gaming descriptors are read-only:
    # they return a session descriptor, a macro
    # descriptor, or a drive-only media note, read no
    # game, control no game, automate no input, bundle
    # no tool, and persist nothing, so they are
    # side-effect-free. (game_record_gaming writes a
    # file, so it is confirm-gated, not read-only.)
    "game_session_describe", "game_macro_describe",
    "game_media_note",
    # Wave 9 Build 8 pilot descriptors are read-only:
    # they return a bench, step, session, plan,
    # workflow, or review-note descriptor, run nothing,
    # read nothing live, deploy nothing, sign nothing,
    # and persist nothing, so they are side-effect-free.
    # (pilot_record_pilot writes a file, so it is
    # confirm-gated, not read-only.)
    "pilot_bench", "pilot_build_steps",
    "pilot_study_session", "pilot_revision_plan",
    "pilot_dev_workflow", "pilot_review_note",
    # Wave 9 Build 9 travel descriptors are read-only:
    # they return a trip, door-to-door, transport,
    # loadout, scene, spatial, sonify, recipe, or
    # scoreboard descriptor, book nothing, pay nothing,
    # route nothing commercially, drive nothing, render
    # nothing, move nothing, and persist nothing, so
    # they are side-effect-free. (travel_record writes a
    # file, so it is confirm-gated, not read-only.)
    "travel_trip", "travel_door_to_door",
    "travel_transport", "travel_loadout",
    "travel_scene", "travel_spatial",
    "travel_sonify", "travel_recipe",
    "travel_scoreboard",
    # Wave 9 Build 10 graduation descriptors are
    # read-only: they check a caller-supplied choice
    # record through the first-open gate, state the
    # retention rule, state the indicator requirement,
    # or return a graduation ledger entry — they
    # capture nothing, grant nothing, choose nothing
    # for any user, decide nothing, and persist
    # nothing, so they are side-effect-free.
    # (grad_record writes a file, so it is
    # confirm-gated, not read-only.)
    "grad_describe",
    # Wave 10 Build 1 release-spine descriptors are
    # read-only: they describe caller-supplied install
    # routes, check the shape of a caller-supplied
    # manifest summary plus SHA-256, or return
    # caller-supplied stage gates in order — they
    # release nothing, sign nothing, compile nothing,
    # publish nothing, feed nothing, and decide
    # nothing, so they are side-effect-free.
    # (release_record writes a file, so it is
    # confirm-gated, not read-only.)
    "release_describe",
    # Wave 10 Build 2 trust + help + changelog
    # descriptors are read-only: they return
    # caller-supplied trust facts, transparency notes,
    # help topics plus diagnostic labels, known issues
    # with status as supplied, runbook steps, changelog
    # entries, or a first-audience label — they send
    # nothing, publish nothing, feed nothing, claim no
    # compliance or conformance, and ship nothing, so
    # they are side-effect-free.
    # (trust_record writes a file, so it is
    # confirm-gated, not read-only.)
    "trust_describe",
    # Wave 10 Build 3 first-run descriptors are
    # read-only: they return caller-supplied exit
    # items, a first-command guide, no-setup
    # capabilities, fake playground samples, or a
    # fix-my-setup checklist — they exit nothing, run
    # nothing, fix nothing, install nothing, apply
    # nothing, send nothing, and claim no conformance,
    # so they are side-effect-free.
    # (firstrun_record writes a file, so it is
    # confirm-gated, not read-only.)
    "firstrun_describe",
    # Wave 10 Build 4 conformance-evidence descriptors
    # are read-only: they return caller-supplied
    # evidence items split into evidenced and
    # not-evidenced lists, or a statement listing
    # evidenced and unevidenced checks separately —
    # they claim no conformance, perform no audit,
    # run no live test, and read no live screen, so
    # they are side-effect-free.
    # (accessconf_record writes a file, so it is
    # confirm-gated, not read-only.)
    "accessconf_describe",
    # v5.21 HANDS pure reads (timers' status reads ride countdown's
    # own honesty; pomodoro/stopwatch act, so they stay gated)
    "anagram_finder", "color_from_image", "date_calculator",
    "folder_size_map", "gpu_info", "http_headers", "image_compare",
    "ip_geolocate", "json_query", "link_unshorten", "network_quality",
    "password_strength", "rss_digest", "startup_impact",
    "story_dice", "timezone_convert", "wayback_lookup",
    "windows_update_status",
    # v5.20 NOSE pure reads (sniffs + radar + quality + brightness)
    "network_quality", "room_brightness", "scan_wifi_networks",
    "sniff_file", "sniff_link", "sniff_text",
    # v5.4 "More hands" pure reads
    "compare_files", "list_printers", "quick_math",
    "search_file_contents", "search_indexed_files", "disk_index_status", "two_way_sync_status",  # R9-4 mutagen port: pure read of the sync-pair state files
    "preview_photo_organisation",  # R9-8 elodie port: pure read — the dry-run plan itself, changes nothing
    "system_event_log", "top_processes",
    "unit_convert", "world_clock",
    # v5.5 "More depth" pure reads
    "base64_codec", "check_port", "drive_info",
    "generate_uuid", "hardware_info", "hash_text", "image_info",
    "json_format", "list_connections", "list_drivers", "list_fonts",
    "list_restore_points", "list_users", "network_adapters",
    "power_requests", "saved_wifi_networks", "security_status",
    "sleep_states",
    # v5.11 "Wider reach" pure reads (get_pixel_color REMOVED
    # here by audit fix Batch 1 SEC-01: the tool is dual-mode
    # and its click action is state-changing, so a name-level
    # read-only listing was a classification hole. It is now
    # classified by action in _is_read_action / _needs_confirmation
    # below, and listed in _FREE_BY_DESIGN ("hands") so the
    # classification-completeness invariant still holds.)
    "list_network_shares", "list_snippets",
    "list_window_layouts", "privacy_usage",
    # v5.12 pure reads (text, files, network, smarts, clipboard)
    "case_convert", "clipboard_files", "compound_interest",
    "extract_from_text", "find_process_on_port", "folder_diff",
    "list_local_devices", "loan_calculator", "pdf_info",
    "random_choice", "roll_dice", "text_stats",
    "website_status",
    # v5.13 pure outputs (text-only creative tools)
    "ascii_art", "generate_palette", "writing_prompt",
    # v5.14 pure outputs (text-only creative tools II)
    "haiku", "name_generator", "story_outline",
    # v5.30 section-4 pure reads (sensing looks that keep nothing,
    # log reads, fetch-and-report web tools, pure text outputs;
    # the packs' writers, schedulers and state-planters stay out)
    "app_crash_digest", "battery_drain_detective", "birthday_radar",
    "coin_decision", "compliment_engine", "deadline_radar",
    "describe_camera_view", "dice_roller_plus", "disk_write_hogs",
    "download_eta", "duplicate_photo_finder", "file_age_report",
    "focus_timer_report", "hotkey_cheatsheet", "inbox_zero_assist",
    "link_health_check", "meeting_prep_card", "mic_check",
    "packing_list", "qr_decode_image", "quote_of_morning",
    "read_page_aloud", "room_noise_level", "site_permissions_review",
    "startup_time_report", "tab_census", "unsubscribe_finder",
    "update_restart_planner",
    # R10-1 chrome-devtools-mcp port: read-only CDP inspection
    # of the attached real Chrome (console / network /
    # performance) — loopback reads, gated on the attach opt-in.
    "browser_console", "browser_network", "browser_performance",
    # R10-3 browser-history port: read-only stored history /
    # bookmarks of a named profile — copied-file reads, gated on
    # the dedicated history opt-in.
    "browser_history", "browser_bookmarks",
    # R10-4 python-adblock port: read-only URL matching over a
    # filter list the caller supplies — pure local computation,
    # no network, no browser.
    "check_url_blocked", "filter_list_status",
    # v5.31 wave-2 pure reads (file/web/system reads, pure text
    # and number outputs, timeline summaries; the packs' fact
    # planters — expense/decision/reading logs, riddle and RPS
    # scores — stay out)
    "bmi_calculator", "csv_column_stats", "csv_preview", "csv_query",
    "domain_lookup", "folder_outline", "fortune_cookie",
    "hosts_view", "json_diff", "lorem_text", "mad_libs",
    "meeting_cost", "morse_translate", "nato_spell", "page_word_search",
    "path_audit", "presence_report", "roman_convert",
    # R10-5 python-readability port: article extraction is a pure
    # read of HTML the caller supplies or a page it fetches.
    "extract_article",
    # R11-1 ollama port: ollama_status reads the local Ollama
    # server's version + installed-model list over loopback HTTP —
    # an egress read (not _LOCAL_ONLY_READS), no taint entry: the
    # answers are model-inventory metadata from the operator's own
    # local server, never web content.
    "ollama_status",
    # R16-8 the-wedding-website inspiration: event_budget is a
    # pure computation over the stored plan (tier rate x guests
    # + vendor quotes) — nothing is written or fetched.
    "event_budget",
    # R17-2 trivy inspiration: security_scan is a read; its vuln
    # action fetches the keyless OSV feed over HTTPS, so it is an
    # egress read (not _LOCAL_ONLY_READS) — the ollama_status
    # precedent. Its secrets/licenses/sbom actions are local-only,
    # but the tool as a whole cannot join _LOCAL_ONLY_READS because
    # one action does network I/O; fail-safe by construction.
    "security_scan",
    # R17-8 pvlib inspiration: solar_yield is a pure computation over
    # caller-supplied figures (sun position, clear-sky irradiance, PV
    # yield) — nothing is written, stored or fetched.
    "solar_yield",
    "sense_trends", "split_bill", "tail_file", "time_sync_status",
    "timeline_recap",
    "whats_pending",
    "recipe_scale",
    "oven_temp_convert",
    "cooking_time_per_weight",
    "pantry_check",
    "meal_rotation",
    "coffee_ratio",
    "dough_hydration",
    "egg_timer_math",
    "spice_substitute",
    "flashcard_stats",
    "mnemonic_maker",
    "vocab_word",
    "reading_level",
    "savings_goal",
    "compound_growth",
    "loan_payment",
    "tip_and_tax",
    "hourly_rate_convert",
    "price_per_unit",
    "discount_stack",
    "rent_split",
    "regex_test",
    "base_convert",
    "url_encode_decode",
    "uuid_generate",
    "cron_explain",
    "semver_compare",
    "text_diff",
    "epoch_convert",
    "color_convert",
    "ip_subnet_calc",
    "trip_countdown",
    "journey_time",
    "fuel_cost_split",
    "fund_returns", "mutual_fund_nav",  # R8-8 mftool port (2026-11-10): pure computation / honest refusal, no network, no lookup
    "layover_time",
    "timezone_meeting_planner",
    "sunrise_sunset",
    "moon_phase",
    "compass_bearing",
    "distance_between",
    "speed_distance_time",
    "metronome_bpm",
    "note_frequency",
    "scale_notes",
    "chord_notes",
    "interval_name",
    "tempo_terms",
    "tuning_cents",
    "song_length_beats",
    "rhythm_subdivide",
    "habit_summary",
    "water_intake_math",
    "screen_break_plan",
    "journal_prompt",
    "mood_trend",
    "focus_score",
    "day_review",
    "uv_advice",
    "rain_from_forecast",
    "garden_watering",
    "walk_time",
    "step_goal_math",
    "stargazing_score",
    "daylight_hours",
    "air_quality_advice",
    # v5.32 S9b/S9c pure reads (the words pack + the two
    # admin radars; the loggers and quote_vault write, so
    # they stay gated like every other state write)
    "acronym_finder", "citation_format", "expiry_radar",
    "filler_word_count", "maintenance_due",
    "passive_voice_check", "reading_time", "sentence_stats",
    "signature_block", "slug_maker", "word_frequency",
    # Conversion pass, Wave 6 registration follow-up (v5.64):
    # knowledge_search and overdue_promises read VYUK's own
    # local stores only (knowledge SQLite / promise files) —
    # no network, no writes. research is network-CAPABLE
    # (its entry point sends when a caller supplies a fetcher)
    # so it is read-class here but deliberately NOT in
    # _LOCAL_ONLY_READS: under taint it gates as an egress
    # read (the web_search precedent), even though the funnel
    # wrapper below supplies no fetcher and therefore never
    # sends. The memory-write tools are NOT here: they live
    # in _FREE_BY_DESIGN app-state and gate under taint.
    # Research wiring batch: deep_research joins knowledge_search
    # (not research) — its retriever is the local KnowledgeStore
    # only, no fetcher exists on its path, so it is read-class
    # AND local-only (see _LOCAL_ONLY_READS).
    "knowledge_search", "overdue_promises", "research", "deep_research",
    # R8-1 fastembed port: semantic_search_knowledge and
    # knowledge_embedding_status read VYUK's own local knowledge
    # SQLite only — no network, no writes — so they are read-class
    # AND local-only, like knowledge_search.
    "semantic_search_knowledge", "knowledge_embedding_status",
    # Conversion pass, combined W4/W5 registration (v5.64):
    # curl_cffi_read is an egress read by the same precedent:
    # read-class here, deliberately NOT in _LOCAL_ONLY_READS,
    # so under taint it gates exactly like web_search. The
    # lane itself (curl_cffi_lane_class) is class "egress"
    # and refuses outright under taint; the funnel wrapper
    # supplies no Playwright-context cookies, so an untainted
    # funnel call still refuses honestly before any network
    # I/O. replay_programme is NOT here: it executes
    # programmes through the funnel, so it is confirm-class
    # in _CONFIRM_GATE_TOOLS below, never free or read-only.
    "curl_cffi_read",
    # capture_route reports the ladder walk + bakeoff state
    # (import-availability probes only, no capture, no
    # network) and eyes_fusion reports fusion/evidence
    # records already noted (no new capture, no write) —
    # both provably local reads, so both join
    # _LOCAL_ONLY_READS below as well. occluded_capture is
    # NOT here: a capture is deliberately not read-only
    # (the take_screenshot precedent), so it lives in
    # _FREE_BY_DESIGN under "capture" instead.
    "capture_route", "eyes_fusion",
    # Libwiring sweep (2026-11-01): listing stored taught
    # sequences reads a directory listing only, and resolving
    # the wallpaper pilot's state/art applies nothing — both
    # provably side-effect-free, so both are read-only.
    "teach_list_sequences", "wallpaper_state",
    # §7 consumers batch 2 (2026-11-02): INTENTIONAL —
    # monitor_action is a pure read (resolution and conversion
    # only; display-config changes refuse by definition), so it
    # is classified read-only here, never confirm-gated.
    "monitor_action",
})


_FREE_BY_DESIGN_CLASSES: dict[str, str] = {
    "hands": (
        "Direct input at the user's direction — typing, keys, "
        "clicks and drags into the focused app; the shell-focus, "
        "password-field and taint rules in the gate still apply "
        "to every call."),
    "launch/nav args-gated": (
        "Launching and navigation are already judged "
        "argument-by-argument in _needs_confirmation (shell "
        "hosts, executable paths, interpreter arguments) and by "
        "the browser layer's own rules, so a name-level gate "
        "would only double-ask."),
    "window/display session state": (
        "Instant, reversible session and preference state — "
        "windows, display, volume, theme, wallpaper and "
        "clipboard contents — that changes nothing about "
        "security, networking, scheduling or persistence "
        "mechanisms."),
    "app-state": (
        "Reads and writes confined to VYUK's own data store "
        "and memory (logs, lists, games, plans, snippets, "
        "layouts, reminders) — no file outside it, no system "
        "state, nothing leaves the machine."),
    "capture": (
        "Sense captures (screen, camera, microphone) gated at the capture layer since v5.48 (programme item 21): every capture path honours the sensitive-window denylist in core/capture_guard — a password-manager, authenticator, banking or private-browsing foreground is never captured, on any path, regardless of approvals. Captures that persist or sense privately (camera, microphone, and screenshots saved to a file) additionally require the per-session human opt-in: capture_policy ('ask-once' by default, 'always-ask', 'pre-allowed') asked through core.confirm_gate, approvals per kind per session, denials never remembered. Live captures fire the on-screen capture indicator. The plain screenshot LOOK stays Tier-0 observe — denylist only, no ask. These tools stay ungated at the executor because capture consent lives in the guard, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "companion": (
        "VYUK's own LAN companion listener (R7-5 kovrel pattern): the start/stop calls themselves are the owner's explicit opt-in — the listener never auto-starts, never binds anything but the private LAN address, issues a fresh per-start PIN and per-device session tokens, and dies with stop_companion. Screen sharing inside a companion session is consent-gated twice (capture guard at start, denylist per frame). These tools stay ungated at the executor because the consent lives in the module, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "ebook": (
        "VYUK's own ebook library server (R17-5 komga pattern): the serve/stop calls themselves are the owner's explicit opt-in — the listener never auto-starts, never binds anything but the private LAN address, issues a fresh password per start, serves only the books the owner registered, and dies with stop. The registry writes touch only VYUK's own store (the bookings 'app-state' precedent). These tools stay ungated at the executor because the consent lives in the module, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "wake": (
        "VYUK's own custom wake-word training (R7-6 micro-wake-word pattern): the train call itself is the owner's explicit opt-in — training is a dev-time activity that renders synthetic speech through the on-device voice and writes one tiny model file under data/models/wake, never captures the microphone, never touches the network, and never enables wake. Enabling a model is additionally gated on the microphone consent guard in the module (capture_guard.ensure_capture_allowed(\"microphone\")), and the model only listens when wake itself is enabled by the owner. These tools stay ungated at the executor because the consent lives in the module, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "workflow": (
        "VYUK's own DAG workflow engine (R7-7 taskflow pattern): define_workflow stores a workflow definition (data, never code — a JSON graph of funnel-tool calls) after validating the whole document, and run_workflow executes it node by node through the ONE funnel (core/brain/planner_plans._execute_tool), so every node passes its own gate and confirm rules exactly as a direct call would. The DAG adds no privilege: it cannot grant a tool a permission its own gate would refuse, a node a gate refuses is FAILED (never retried), and a node that needs human confirmation stops for the human. These tools stay ungated at the executor because the trust lives in the per-node dispatch, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "proactivity": (
        "VYUK's own when-to-intervene scorer + benchmark (R7-8 ProactiveAgent pattern), MEASUREMENT MODE ONLY: score_intervention scores one candidate intervention from evidence the tree already collects and records the measurement; run_proactivity_bench runs the 36-case scripted bench and records precision/recall/F1. Neither tool executes anything, senses anything, or wires any intervention to run — the output is a scored suggestion with its reasons, shown to the user. These tools stay ungated at the executor because they change nothing outside VYUK's own measurement store (data/proactivity_runs.db); they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "knowledge": (
        "VYUK's own vector index for the local knowledge store (R8-1 fastembed port): embed_knowledge embeds stored documents with the on-device MiniLM model and writes the vectors to the embeddings table in VYUK's own knowledge SQLite file. The model is operator-supplied (data/embeddings/, pinned sha256), never bundled, and the call never touches the network. These tools stay ungated at the executor because they change nothing outside VYUK's own data store; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "wormhole": (
        "VYUK's own code-phrase file transfer (R8-5 magic-wormhole port): the offer/receive calls themselves are the owner's explicit opt-in — the listener never auto-starts, never binds anything but the private LAN address, issues a fresh single-use code phrase per offer with a 10-minute expiry, cancels the offer after three wrong guesses, and dies after one successful claim, on expiry, or on cancel. No relay, no public server, no account, no cloud: the bytes move encrypted between the two machines on the same local network and are verified against the sender's tag before the file is presented as received. These tools stay ungated at the executor because the consent lives in the module, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "presence": (
        "VYUK's own Discord Rich Presence (R8-9 pypresence adoption): the show/clear calls themselves are the owner's explicit opt-in — presence is never set without an explicit user request in the same session, never auto-starts, never re-applies on boot, talks only to the local Discord client's IPC pipe (named pipe on Windows, Unix socket elsewhere — no cloud, no account, no token), and the Discord application ID is operator-supplied (no VYUK application ID ships). These tools stay ungated at the executor because the consent lives in the module, not the registry gate; they are NOT read-only, so taint gating and the panic refusal apply exactly as for every other free-by-design tool."),
    "output to user": (
        "Output aimed at the user alone — speech, notifications "
        "and ambient audio — with no file, device or system "
        "change beyond producing it."),
    "v5.9 deliberate exemption": (
        "The v5.9 sweep's documented posture (see the exemption "
        "note at _CONFIRM_GATE_TOOLS): user-directed, instantly "
        "reversible, no persistence beyond the setting itself."),
    "fixed-folder generative output": (
        "Generative renders saved only into VYUK's own fixed "
        "~/Music/Nova or ~/Pictures/Nova folders under "
        "non-clobbering, content-derived names — the model "
        "chooses no destination, so there is no write path to "
        "abuse."),
    "executor dispatch": (
        "Dispatchers whose every inner call re-enters "
        "tools.execute and is judged on the inner tool's own "
        "classification (run_routine steps, use_toolbox "
        "'call'), so gating the dispatcher itself would "
        "double-ask without closing anything."),
    "effectful read": (
        "Audit fix Batch 3 (COV-02): a read whose handler "
        "observably touches the network or starts a session "
        "(a caller-chosen network probe, a winget source "
        "query, a browser session bootstrap) — it changes no "
        "local state, so it stays free untainted BY DESIGN, "
        "but because it is NOT in _READ_ONLY_TOOLS it gates "
        "under taint (hard and soft Tier 2), refuses while "
        "the failsafe is tripped, gates under the "
        "session-provenance lock, and carries the receipt "
        "guard like any other non-read tool. The three "
        "probe tools keep their explicit _EGRESS_TOOLS "
        "membership as well; browser_read and "
        "list_app_updates gate under taint via the non-read "
        "branch (they no longer need the _LOCAL_ONLY_READS "
        "inversion)."),
}


_FREE_BY_DESIGN: dict[str, str] = {
    # hands (v5.47, programme item 11)
    "type_text": "hands",
    # effectful reads (audit fix Batch 3, COV-02 — see the
    # "effectful read" class above; NOT in _READ_ONLY_TOOLS)
    "browser_read": "effectful read",
    "list_app_updates": "effectful read",
    "ping_host": "effectful read",
    "ssl_cert_info": "effectful read",
    "trace_route": "effectful read",
    "press_hotkey": "hands",
    "mouse_control": "hands",
    "cursor_control": "hands",
    "click_element": "hands",
    "click_text": "hands",
    "click_and_drag_path": "hands",
    "drag_mouse": "hands",
    "drag_file_onto": "hands",
    "uia_click_control": "hands",
    "click_by_mark": "hands",  # v5.49 (programme item 30)
    "click_anywhere": "hands",  # click-anywhere (Plan 9): DOM/UIA/mark/OCR/coordinate clicks at the user's direction; D-17/D-18/scope-guard/tier rules still apply to every call
    # Audit fix Batch 1 (SEC-01): get_pixel_color's click action
    # is a hand action; its read action stays read-class via
    # _is_read_action. Membership here is the classification
    # home that keeps the completeness invariant (it can no
    # longer sit in _READ_ONLY_TOOLS) — the click gate itself
    # lives in _needs_confirmation's args-aware branch above,
    # exactly like window_control close.
    "get_pixel_color": "hands",
    "uia_type_text": "hands",
    "scroll_until_found": "hands",
    # launch/nav args-gated (v5.47, programme item 11)
    "launch_application": "launch/nav args-gated",
    "open_path": "launch/nav args-gated",
    "open_url": "launch/nav args-gated",
    "open_settings_page": "launch/nav args-gated",
    "browser_click": "launch/nav args-gated",
    "browser_control": "launch/nav args-gated",
    "browser_press": "launch/nav args-gated",
    "browser_select": "launch/nav args-gated",
    "browser_type": "launch/nav args-gated",
    # window/display session state (v5.47, programme item 11)
    "adjust_volume": "window/display session state",
    "app_volume": "window/display session state",
    "set_volume_level": "window/display session state",
    "set_brightness": "window/display session state",
    "media_control": "window/display session state",
    # v5.52 (item 4): starting playback is the media_control
    # family — local, free, reversible, no egress.
    "play_music": "window/display session state",
    # v5.52 (item 8): her OWN settings — local, free, reversible,
    # no egress. (Its `set` action is still state-changing for
    # the taint/lock splits: see _is_read_action.)
    "app_settings": "window/display session state",
    "mic_control": "window/display session state",
    "set_microphone_mute": "window/display session state",
    "window_control": "window/display session state",
    "window_snap": "window/display session state",
    "window_place": "window/display session state",
    "window_opacity": "window/display session state",
    "window_always_on_top": "window/display session state",
    "window_pin_toggle_all": "window/display session state",
    "tile_windows": "window/display session state",
    "move_window_to_monitor": "window/display session state",
    "monitor_control": "window/display session state",
    "restore_window_layout": "window/display session state",
    "focus_window_by_text": "window/display session state",
    "desktop_control": "window/display session state",
    "display_control": "window/display session state",
    "virtual_desktop": "window/display session state",
    "set_wallpaper": "window/display session state",
    "toggle_dark_mode": "window/display session state",
    "set_refresh_rate": "window/display session state",
    "set_resolution": "window/display session state",
    "set_screen_rotation": "window/display session state",
    "set_focus_assist": "window/display session state",
    "mouse_settings": "window/display session state",
    "keep_awake": "window/display session state",
    "lock_screen": "window/display session state",
    "make_wallpaper": "window/display session state",
    "screen_brightness_auto": "window/display session state",
    "copy_file_to_clipboard": "window/display session state",
    "write_clipboard": "window/display session state",
    "generate_password": "window/display session state",
    # app-state (v5.47, programme item 11) — every handler here
    # was verified to keep its writes inside the app's own data
    # store / memory, including the two whose names mislead:
    # service_schedule is an appliance-servicing log in the data
    # store, NOT Windows services, and peripheral_roll_call's
    # only write is its own snapshot file there.
    "asset_inventory": "app-state",
    "bill_calendar": "app-state",
    "borrow_log": "app-state",
    "budget_envelope": "app-state",
    # Stage 0 features batch 3 (Group F, 2026-11-22): the money
    # alerts check writes only its own dedupe ledger
    # (data/money_alerts.json) plus the price watches' own
    # last-seen fields — app-state, never money movement.
    "check_money_alerts": "app-state",
    "contact_card": "app-state",
    "countdown": "app-state",
    "create_plan": "app-state",
    "decision_log": "app-state",
    # Wiring item 1 (v6.0.0 post-ship): delete_plan, delete_routine,
    # delete_snippet and delete_window_layout are NOT here anymore.
    # They used to sit in this "app-state" class, but that class
    # justifies free WRITES to VYUK's own store — not irreversible
    # deletes with no trash path. Those four are human-gated now
    # (see _CONFIRM_GATE_TOOLS); a tool cannot be both gated and
    # free by design (test_classification pins the disjointness).
    "document_vault": "app-state",
    "emergency_card": "app-state",
    "erp": "app-state",
    # R16-8 the-wedding-website inspiration: the event-planning
    # pack writes only to VYUK's own plan store (one memory fact
    # per plan, the budget_envelope shape) — app-state, never
    # asking, never moving money; event_budget is a pure read
    # and lives in _READ_ONLY_TOOLS instead.
    "event_guest": "app-state",
    "event_milestone": "app-state",
    "event_plan": "app-state",
    "event_schedule": "app-state",
    # R18-7 Attendize inspiration: ticket types, sale records and
    # QR check-in write only to VYUK's own ticketing store.
    "event_tickets": "app-state",
    "event_vendor": "app-state",
    "expense_log": "app-state",
    # R19-7 Donkie/Spoolman inspiration: the filament spool
    # inventory writes only to VYUK's own store
    # (filament.sqlite3) — no network, no printer, no money
    # movement — app-state, never asking.
    "filament": "app-state",
    # R19-8 Django-CRM/Django-CRM inspiration: the deal pipeline
    # and support tickets write only to VYUK's own store
    # (deals.sqlite3) — no network, no web app, no money movement
    # (a deal amount is a record, the money moved outside VYUK) —
    # app-state, never asking.
    "deals": "app-state",
    "flashcard_add": "app-state",
    # Triage Wire Build 1: study_review writes only to VYUK's own
    # study_schedule.json (flashcard_review precedent).
    "study_review": "app-state",
    # Triage Wire Build 4: finance playbook add/vote/curate and
    # social ledger add/complete/settle/cadence/snooze/pause
    # write only to VYUK's own store files (finance_playbook.json
    # / social_ledger_v573.json) — no file outside them, no
    # message sent, no memory-DB or vault write. The two
    # deletions are NOT here; they are confirm-gated above.
    "finance_playbook_add": "app-state",
    "finance_playbook_vote": "app-state",
    "finance_playbook_curate": "app-state",
    "social_occasion_add": "app-state",
    "social_promise_add": "app-state",
    "social_promise_complete": "app-state",
    "social_gift_add": "app-state",
    "social_debt_add": "app-state",
    "social_debt_settle": "app-state",
    "social_stay_in_touch": "app-state",
    "social_nudge_snooze": "app-state",
    "social_nudge_pause": "app-state",
    # Stage 0 features batch 2 (Group E, 2026-11-22): snooze_email
    # writes only VYUK's own snooze store (data/email_snoozed.json)
    # — the social_nudge_snooze precedent: local, reversible,
    # nothing leaves the machine.
    "snooze_email": "app-state",
    # GAPS-7 (BLOCKS #6): sync_updates reads Google and writes only
    # its own watermark file (data/connector_sync_state.json) — no
    # message sent, no event created, nothing leaves the machine.
    # The state save is re-read verified (A1-store).
    "sync_updates": "app-state",
    # Wave 6 integration: disabling is a reversible safety
    # action confined to the app's own harness store file;
    # journal append writes only the app's own fixed journal
    # file (no caller-chosen destination).
    "extension_disable": "app-state",
    "eye_rest_timer": "app-state",  # R8-4 screen-break port: 20-20-20 nudges via the water_break chain shape, notices only, never locks input
    "mission_journal_append": "app-state",
    "flashcard_answer": "app-state",
    "flashcard_review": "app-state",
    "focus_session": "app-state",
    "gift_log": "app-state",
    "gratitude_log": "app-state",
    "grocery_list": "app-state",
    "habit_checkin": "app-state",
    "habit_track": "app-state",
    "hangman": "app-state",
    "log_glass": "app-state",  # R8-4 screen-break port: today's water-glass count in the app's own memory, like habit_checkin
    "mood_log": "app-state",
    "music_trivia": "app-state",
    "number_guess": "app-state",
    "page_change_summary": "app-state",
    "page_watch": "app-state",
    "peripheral_roll_call": "app-state",
    "pet_feeding_log": "app-state",
    "plant_care_log": "app-state",
    "pomodoro": "app-state",
    # Stage 0 features batch 3 (Group F, 2026-11-22): the
    # portfolio writes only to VYUK's own memory facts (category
    # `portfolio`) — app-state, never money movement.
    "portfolio_tracker": "app-state",
    "practice_log": "app-state",
    "price_watch": "app-state",
    "quiz_me": "app-state",
    "quote_vault": "app-state",
    "reading_list": "app-state",
    "remember_person": "app-state",
    "remember_promise": "app-state",
    # v5.67: personal_skills reads/writes are confined to VYUK's
    # own skill library; admit is args-gated, never free.
    "personal_skills": "app-state",
    # v5.69: health reads/writes are confined to VYUK's own health
    # store; record is args-gated, never free.
    "health": "app-state",
    "home_inventory": "app-state",
    "warehouse_stock": "app-state",
    "bookings": "app-state",
    "lodging": "app-state",
    "wardrobe": "app-state",
    "remember_today": "app-state",
    "remember_user_fact": "app-state",
    "riddle_time": "app-state",
    "rock_paper_scissors": "app-state",
    "save_snippet": "app-state",
    "save_window_layout": "app-state",
    "screen_time_today": "app-state",
    "service_schedule": "app-state",
    "set_reminder": "app-state",
    "shopping_list": "app-state",
    "spelling_bee": "app-state",
    "stand_up_nudge": "app-state",
    "status_strip": "app-state",  # R8-6 yasb port: show/hide only moves VYUK's own strip window, notices only, never locks input
    "stopwatch": "app-state",
    "story_chain": "app-state",
    "subscription_audit": "app-state",
    # R18-8 bukosabino/ta inspiration: datasets + indicator
    # computation write only to VYUK's own store.
    "technical_analysis": "app-state",
    "tic_tac_toe": "app-state",
    # R18-10 dominikbraun/timetrace inspiration: projects + records
    # write only to VYUK's own tracker store.
    "time_tracker": "app-state",
    "optimise": "app-state",
    # R19-10 VROOM-Project/vroom inspiration: advanced vehicle
    # routing writes only to VYUK's own plan store.
    "vehicle_routes": "app-state",
    "trivia_quiz": "app-state",
    "typing_test": "app-state",
    "update_plan_step": "app-state",
    "warranty_log": "app-state",
    "water_break": "app-state",
    "wifi_dead_spot_map": "app-state",
    "word_game": "app-state",
    "word_of_the_day": "app-state",
    "wormhole_offer": "wormhole",  # R8-5 magic-wormhole port: LAN-direct code-phrase offer, explicit start
    "wormhole_receive": "wormhole",  # R8-5 magic-wormhole port: claim an offered file, phrase + address
    "discord_presence": "presence",  # R8-9 pypresence adoption: show/clear/status, explicit request only, local IPC
    "set_discord_client_id": "app-state",  # R8-9 pypresence adoption: stores the operator's Discord application ID in VYUK's own data dir
    # capture (v5.47, programme item 11; Phase 3 item 21 revisits)
    "capture_window": "capture",
    "listen_for_sound": "capture",
    "record_audio": "capture",
    "screen_diff": "capture",
    "screen_look": "capture",
    "screenshot_region": "capture",
    "start_meeting": "capture",  # R7-4 meetily: mic capture, guard-gated
    "stop_meeting": "capture",  # R7-4 meetily: ends the mic capture
    "start_companion": "companion",  # R7-5 kovrel: LAN listener, PIN-gated, explicit start
    "stop_companion": "companion",  # R7-5 kovrel: stops the listener, kills PIN + tokens
    "train_custom_wake_word": "wake",  # R7-6 micro-wake-word: dev-time training, explicit start, no mic capture
    "enable_custom_wake_model": "wake",  # R7-6 micro-wake-word: arms a trained model, mic-consent-gated in the module
    "define_workflow": "workflow",  # R7-7 taskflow: stores a validated workflow definition (data, never code)
    "run_workflow": "workflow",  # R7-7 taskflow: runs a workflow; every node passes its own gate, the DAG adds no privilege
    "score_intervention": "proactivity",  # R7-8 proactive-agent: scores one candidate, records the measurement, never acts
    "run_proactivity_bench": "proactivity",  # R7-8 proactive-agent: runs the 36-case bench, records precision/recall/F1
    "embed_knowledge": "knowledge",  # R8-1 fastembed port: embeds stored docs into the local embeddings table, operator-supplied model
    "index_files": "app-state",  # R9-3 whoosh port: builds the local FTS5 disk index in VYUK's own data store (data/disk_index.sqlite3), user-chosen roots only, never the network
    "take_screenshot": "capture",
    # Conversion pass, combined W4/W5 registration (v5.64):
    # occluded_capture is a capture, so it is NOT read-only
    # (take_screenshot precedent) — executor-ungated, but
    # taint-gated and panic-refused like every other
    # free-by-design tool, and its own staged guard still
    # requires per-session approval and returns
    # engine-unavailable with no WGC engine wired.
    "occluded_capture": "capture",
    "voice_note": "capture",
    "webcam_snapshot": "capture",
    # R17-3 go2rtc rebuild: an IP camera is a camera — bounded,
    # guard-gated, consent per session, executor-ungated like
    # webcam_snapshot; discovery/add/list/remove touch only VYUK's
    # own registry and are not gated.
    "ip_camera": "capture",
    # R17-5 komga rebuild: the ebook library server follows the
    # companion pattern — serve/stop are the explicit opt-in, the
    # listener binds the LAN address only with a fresh password
    # per start, and the registry writes touch only VYUK's own
    # store; executor-ungated, taint-gated and panic-refused like
    # every other free-by-design tool.
    "ebook_library": "ebook",
    # B-R1-8: the gesture watch is a camera capture (bounded, guard-gated,
    # consent per session) — executor-ungated, taint-gated and
    # panic-refused like webcam_snapshot; the guard inside the session
    # refuses a denied camera before any frame is read.
    "watch_gestures": "capture",
    # output to user (v5.47, programme item 11) — notify_user
    # left this class in round-3 section F fix F-F303
    # (2026-11-21): a toast is a screen pop, not the reply
    # channel, so it is confirm-gated (see _CONFIRM_GATE_TOOLS).
    "ambient_scene": "output to user",
    "read_selection_aloud": "output to user",
    "speak_text": "output to user",
    "speaker_check": "output to user",
    # v5.9 deliberate exemption (v5.47, programme item 11)
    "clear_clipboard": "v5.9 deliberate exemption",
    "dns_flush": "v5.9 deliberate exemption",
    "set_default_printer": "v5.9 deliberate exemption",
    "set_screen_timeout": "v5.9 deliberate exemption",
    # fixed-folder generative output (v5.47, programme item 11)
    "binaural_tone": "fixed-folder generative output",
    "gradient_css": "fixed-folder generative output",
    "metronome": "fixed-folder generative output",
    # executor dispatch (v5.47, programme item 11)
    "run_routine": "executor dispatch",
    "use_toolbox": "executor dispatch",
}


CLASSIFICATION: dict[str, str] = (
    {name: "confirm" for name in _CONFIRM_GATE_TOOLS}
    | {name: "read" for name in _READ_ONLY_TOOLS}
    | {name: "free" for name in _FREE_BY_DESIGN}
)
# The sets are literals; this documents the partition invariant
# loudly at import rather than leaving it to a test.
assert not (
    _CONFIRM_GATE_TOOLS & _READ_ONLY_TOOLS
    or _CONFIRM_GATE_TOOLS & set(_FREE_BY_DESIGN)
    or _READ_ONLY_TOOLS & set(_FREE_BY_DESIGN)
), "classification sets must be disjoint"


def classify(name: str) -> str:
    """Static class of a tool: "confirm" | "read" | "free", or
    "unclassified" for a name in no set (a completeness bug —
    tests/test_classification.py fails on it, never a silent
    fourth freedom). "confirm" means the tool CAN require
    confirmation for some call; whether THIS call needs it is
    classify_call's job."""
    return CLASSIFICATION.get(name, "unclassified")


def _is_read_action(name: str, args: dict[str, Any] | None) -> bool:
    """True when this call of an action-dispatched tool is the READ
    action — the same read/write split _needs_confirmation's own
    branches draw below (status/list/get reads vs state changes).
    Used by the speaker-lock branch and by _is_read_class (the
    v5.46 A3 failsafe refusal) so both reuse this module's
    classification instead of inventing a second one."""
    action = str((args or {}).get("action", "")).strip().lower()
    if name == "task_scheduler_control":
        return action not in ("run", "enable", "disable")
    if name == "game_server":
        # R17-4 (MCSManager inspiration): query/status/console/list
        # are reads; add/remove only touch VYUK's own server
        # registry (the bookings 'app-state' precedent) — the
        # acting actions are not read-class.
        return action in ("query", "status", "console", "list")
    if name == "ebook_library":
        # R17-5 (komga inspiration): list/search/status are reads
        # of VYUK's own library; scan/progress/serve/stop/remove
        # write the store, start/stop a server, or forget a book —
        # none of those is read-class.
        return action in ("list", "search", "status")
    if name == "lodging":
        # R17-6 (openstays inspiration): availability/list/guest/day/
        # events are reads of VYUK's own lodging store; property/unit/
        # rate/book/cancel/checkin/checkout/no-show/housekeeping write
        # the store or move a stay — none of those is read-class.
        return action in ("availability", "list", "guest", "day",
                          "events")
    if name == "stage_lighting":
        # R17-7 (qlcplus inspiration): status/events/discover and
        # the list modes are reads of VYUK's own lighting store or
        # a LAN probe; the registry writes and the sending actions
        # (output/blackout/scene run/chase step) are not read-class.
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action in ("status", "events", "discover"):
            return True
        # GAPS-23 (F-V3): mode=="list" is a read ONLY for the
        # actions whose handler dispatches on mode (node/patch/
        # fixture/scene/chase — core/stage_lighting.py:718-726).
        # The old bare `return mode == "list"` also read True for
        # output/blackout/foreign actions with mode list — a DMX
        # send (or an unknown action) counted as a read, so the
        # failsafe would have let it run during a trip. Narrowed:
        # identical for every valid call, fail-closed otherwise.
        return action in ("node", "patch", "fixture", "scene",
                          "chase") and mode == "list"
    if name == "billing":
        # R18-1 (stripe/stripe-node inspiration): customer list/get,
        # subscription status, payment-link get, invoice list/get,
        # webhook verify (pure local HMAC computation), status and
        # events are reads; creating a test customer/subscription/
        # payment link/Checkout session/refund and cancelling a test
        # subscription write to Stripe — none of those is read-class.
        if action in ("status", "events", "webhook"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action == "customer":
            return mode in ("list", "get")
        if action == "subscription":
            return mode == "status"
        if action == "payment_link":
            return mode == "get"
        if action == "invoice":
            return mode in ("list", "get")
        return False
    if name == "mcp_registry":
        # R18-3 (IBM/mcp-context-forge inspiration): discover,
        # policy, status, events and the server list mode are
        # reads of the server config, the bus snapshot and the
        # review store; adding, removing, enabling or disabling
        # a server and recording a review write — none of those
        # is read-class.
        if action in ("discover", "policy", "status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action == "server" and mode == "list"
    if name == "agent_call":
        # R18-4 (a2aproject/A2A inspiration): discovering a card,
        # listing known agents, reading a task, returning VYUK's own
        # static card, and reading the status/events ledger are
        # reads of the registry or the ledger; calling, cancelling
        # or forgetting an agent sends to a third party — none of
        # those is read-class.
        if action in ("discover", "list", "card", "status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action == "task" and mode in ("get", "list")
    if name == "prompt_tune":
        # R18-5 (stanfordnlp/dspy inspiration): prompt/dataset
        # lists and gets, the variant bank, the status and the
        # ledger are reads of VYUK's own store; evaluating, tuning
        # or proposing calls the LM — none of those is read-class.
        if action in ("variants", "status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action in ("prompt", "dataset") and mode in ("list", "get")
    if name == "audio_localise":
        # R19-1 (Huanshere/VideoLingo inspiration): the status and
        # the ledger are reads of VYUK's own store; translating or
        # dubbing calls the LM / renders audio — none of those is
        # read-class. The project get is a read of the store.
        if action in ("status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action == "project" and mode == "get"
    if name == "media_chat":
        # R19-4 (nexmoe/VidBee inspiration): the list, the get, the
        # status and the ledger are reads of VYUK's own store; asking
        # or summarizing calls the LM — neither is read-class.
        return action in ("list", "get", "status", "events")
    if name == "invoice":
        # R19-5 (al1abb/invoify inspiration): the list, the get, the
        # status and the ledger are reads of VYUK's own store; the
        # template list/get is a read of the built-in templates.
        # Rendering writes a PDF file and sending mails — neither is
        # read-class.
        return action in ("list", "get", "status", "events",
                          "template")
    if name == "translations":
        # R19-6 (tolgee/tolgee-platform inspiration): the status and
        # the ledger are reads of VYUK's own store; the project/key
        # list/get and the translation get/list/missing are reads of
        # the store. Adding, removing, setting, translating,
        # importing or exporting writes the store, calls the LM or
        # writes a file — none of those is read-class.
        if action in ("status", "events"):
            return True
        kind = str((args or {}).get("kind", "")).strip().lower()
        if action == "project":
            return kind in ("list", "get")
        if action == "key":
            return kind in ("list", "get")
        if action == "translation":
            return kind in ("get", "list", "missing")
        return False
    if name == "filament":
        # R19-7 (Donkie/Spoolman inspiration): the status and the
        # ledger are reads of VYUK's own store; the vendor/filament/
        # spool list/get are reads of the store. Adding, removing
        # or recording use writes the store — none of those is
        # read-class.
        if action in ("status", "events"):
            return True
        kind = str((args or {}).get("kind", "")).strip().lower()
        return action in ("vendor", "filament", "spool") \
            and kind in ("list", "get")
    if name == "deals":
        # R19-8 (Django-CRM/Django-CRM inspiration): the status and
        # the ledger are reads of VYUK's own store; the pipeline/
        # deal/ticket list/get are reads of the store. Adding,
        # removing, moving, closing, responding or changing a status
        # writes the store — none of those is read-class.
        if action in ("status", "events"):
            return True
        kind = str((args or {}).get("kind", "")).strip().lower()
        return action in ("pipeline", "deal", "ticket") \
            and kind in ("list", "get")
    if name == "vehicle_routes":
        # R19-10 (VROOM-Project/vroom inspiration): the status and
        # the ledger are reads of VYUK's own store; planning a route
        # writes the store (it records the plan) — it is not
        # read-class.
        return action in ("status", "events")
    if name == "voice_agent":
        # R18-6 (fonoster/fonoster inspiration): agent/flow lists
        # and gets, the status and the ledger are reads of VYUK's
        # own store, and 'run' is a local text simulation that
        # writes nothing; placing or answering a call, stopping the
        # listener, and adding/removing an agent or a flow write or
        # send — none of those is read-class.
        if action in ("run", "status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        return action in ("agent", "flow") and mode in ("list", "get")
    if name == "wardrobe":
        # R17-9 (wardrobe inspiration): suggest/status/events and
        # the list modes are reads of VYUK's own wardrobe store;
        # garment add/remove, outfit build/remove, schedule add/
        # remove and wear write the store — none of those is
        # read-class.
        if action in ("suggest", "status", "events"):
            return True
        mode = str((args or {}).get("mode", "")).strip().lower()
        # GAPS-23 (F-V3): mode=="list" is a read ONLY for the
        # actions whose handler dispatches on mode (garment/
        # outfit/schedule — core/wardrobe.py:499/542/671). The old
        # bare `return mode == "list"` also read True for wear (or
        # a foreign action) with mode list — marking an outfit worn
        # counted as a read, so the failsafe would have let it run
        # during a trip. Narrowed: identical for every valid call,
        # fail-closed otherwise.
        return action in ("garment", "outfit", "schedule") \
            and mode == "list"
    if name in ("firewall_control", "fast_startup_control",
                "hibernate_setting"):
        return action == "status"
    if name == "env_var":
        return action in ("list", "get")
    if name == "vpn_control":
        return action not in ("connect", "disconnect")
    if name == "window_control":
        return action in ("list", "status")
    # v5.47 (programme item 11): the two newly gate-classified
    # dispatched tools keep their read actions read-class, like
    # the firewall/vpn splits above — power_plans only lists,
    # network_control's status / wifi_list only report and its
    # bluetooth_toggle is a no-op refusal message (the handler
    # changes nothing; Windows has no reliable radio switch).
    if name == "power_plans":
        return action in ("", "list")
    if name == "network_control":
        return action in ("status", "wifi_list", "bluetooth_toggle")
    # v5.52 (item 8): app_settings' get is a pure read of her own
    # stores; set is state-changing, so under a HARD-tainted
    # session (or the provenance lock) it gates like every other
    # state-changing local tool — an injected page must not be
    # able to retheme/revoice her mid-attack.
    if name == "app_settings":
        return action in ("", "get")
    # v5.67: personal_skills list/inspect are reads of her own
    # library; admit changes library state and is never read-class.
    if name == "personal_skills":
        return action in ("list", "inspect")
    # v5.69: health extract/summary/remind are reads or a held
    # reminder; record writes to his health store and is never
    # read-class.
    if name == "health":
        return action in ("extract", "summary", "remind")
    # Audit fix Batch 1 (SEC-01): get_pixel_color is dual-mode.
    # A bare read (or an omitted action, which the handler
    # defaults to read) is a pure probe and stays read-class;
    # click — and any unknown action, fail-closed — is
    # state-changing and is never read-class. This branch is
    # what _is_read_class falls through to, so BOTH legs of the
    # classification (read-class and the confirm gate) agree:
    # fixing only one would leave the other leg open.
    if name == "get_pixel_color":
        return action in ("", "read")
    # Loophole batch 4 (L-27): these read actions were missing
    # from this table — invisible while the speaker lock
    # defaulted OFF, but the lock (now ON by default) gates
    # every call this table does not name a read, so a wake
    # session's list/status/digest asked for confirmation
    # despite _gate_action below naming them free reads. Each
    # branch mirrors _gate_action's own free-read list for the
    # tool; the acting actions stay non-read (fail-closed).
    # adapter_action is deliberately NOT mirrored here: its
    # read-ness depends on running the phrase resolution, so it
    # stays gated under the lock (conservative, not a hole).
    if name == "scheduled_agents":
        # GAPS-8: list / status / digest are free reads of VYUK's
        # own agent store; setup_digest / cancel / run_now /
        # save_routine / run_routine act.
        return action in ("list", "status", "digest")
    if name == "context_menu_action":
        # §7 batch 8: listing an already-open menu is a read.
        return action == "list"
    if name == "journal_action":
        # §7 batch 2: list and plan are pure re-reads of the
        # journal file; reconcile / compensate are effectful.
        return action in ("list", "plan")
    if name == "menu_action":
        # §7 batch 1: listing menus is a read.
        return action == "list"
    if name == "system_surface_action":
        # §7 batch 1: surface listings and Start search are reads.
        surface = str((args or {}).get("surface", "") or "").strip().lower()
        if action == "list" and surface in (
                "tray", "taskbar", "notification", "settings"):
            return True
        return surface == "start" and action == "search"
    if name == "run_command":
        # v5.45 (H3): polling, waiting on or listing a background
        # job reads its output — the command was trust-checked
        # and approved once at launch. Cancelling a job is an
        # effect, not a read, so it stays gated under the lock.
        return action in ("poll", "status", "wait", "list")
    # Loophole batch 5 (L-24): the four app-state tools whose
    # read actions were missing from this table — their list /
    # total reads return the owner's stored content (expenses,
    # bills, subscriptions, books), so the owner-read gate
    # needs to recognise them as reads. Behaviour change,
    # stated: under the provenance lock these reads move from
    # "confirm ask" (state-changing by classification) to the
    # owner-read refusal in an unproven live session, and to a
    # free read in a proven session or a typed turn. The write
    # actions stay non-read (add/remove/mark_read act, and reach
    # their own confirm gate).
    if name == "expense_log":
        return action in ("list", "total")
    if name == "bill_calendar":
        return action == "list"
    if name == "subscription_audit":
        return action == "list"
    # Stage 0 features batch 3 (Group F, 2026-11-22): the money
    # alerts check reads the owner's budgets, watches and
    # subscriptions on every call (no action param) — owner-only
    # by construction, the subscription_audit precedent; the
    # portfolio's list/total read the owner's holdings.
    if name == "check_money_alerts":
        return True
    if name == "portfolio_tracker":
        return action in ("list", "total")
    if name == "reading_list":
        return action == "list"
    return False


def _gate_action(name: str, args: dict[str, Any] | None) \
        -> bool | None:
    """The per-tool gate-action split, moved verbatim from
    _needs_confirmation (core/funnel/tools_gate.py): True when THIS
    call of the tool needs the human's OK, False when it does not,
    None when the tool has no per-tool branch (the caller falls
    through to the static class). The branches are the gate leg of
    the same read/write split _is_read_action is the read leg of —
    they live in this module, adjacent, so the two legs cannot
    drift apart unnoticed. The money / plugin / taint /
    provenance-lock overlays are NOT here: they stay in
    _needs_confirmation, on top of classify_call."""
    if name == "window_control":
        action = str((args or {}).get("action", "")).strip().lower()
        return action == "close"
    if name == "get_pixel_color":
        # Audit fix Batch 1 (SEC-01): a pixel click is a desktop
        # click and needs the human's OK in a clean session,
        # exactly like window_control close above — the tool is
        # NOT in _CONFIRM_GATE_TOOLS, so this args-aware branch
        # is its clean-session gate. Reads ("" / "read") stay
        # free; click — and any unknown action, fail-closed —
        # gates. Taint, provenance-lock, failsafe and C10
        # handling for clicks follow from _is_read_action
        # returning False for them, not from this branch.
        action = str((args or {}).get("action", "") or "").strip().lower()
        return action not in ("", "read")
    if name == "task_scheduler_control":
        # Listing scheduled tasks is a read; running, enabling or
        # disabling one changes the machine (v5.4).
        action = str((args or {}).get("action", "")).strip().lower()
        return action in ("run", "enable", "disable")
    if name == "scheduled_agents":
        # GAPS-8 (BLOCKS #7): setup_digest creates a persisted job,
        # cancel removes one, run_now runs one immediately,
        # save_routine saves a persisted routine — those gate.
        # list / status / digest are free reads, and run_routine is
        # free dispatch (its gated steps still ask at run time
        # through the funnel). Unknown or missing action gates
        # fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        return action in ("setup_digest", "cancel", "run_now",
                          "save_routine")
    if name == "game_server":
        # R17-4 (MCSManager inspiration): querying, status, console
        # and listing are reads, and add/remove only touch VYUK's
        # own server registry — starting, stopping, restarting,
        # sending a console line or running an RCON command changes
        # the machine / the server, so those gate. Unknown or
        # missing action gates fail-closed (the handler refuses
        # them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        return action not in ("query", "status", "console", "list",
                              "add", "remove")
    if name == "stage_lighting":
        # R17-7 (qlcplus inspiration): listing/status/events/discover
        # are reads or a LAN probe, and the registry writes (node/
        # patch/fixture/scene-save/chase-save/remove/delete) only
        # touch VYUK's own lighting store — sending DMX to the
        # venue's lights (output, blackout, scene run, chase step)
        # changes the physical world, so those gate. Unknown or
        # missing action gates fail-closed (the handler refuses
        # them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action in ("output", "blackout"):
            return True
        if action == "scene":
            return mode == "run"
        if action == "chase":
            return mode == "step"
        return action not in ("node", "patch", "fixture", "scene",
                              "chase", "discover", "status", "events")
    if name == "billing":
        # R18-1 (stripe/stripe-node inspiration): reads of Stripe
        # test-mode records (customer list/get, subscription status,
        # payment-link get, invoice list/get, webhook verify, status,
        # events) change nothing; creating a test customer, a test
        # subscription, a test payment link, a test Checkout session
        # or a test refund, and cancelling a test subscription, write
        # to Stripe — even in test mode they move test money or
        # create billable state, so those gate (C-14 approve-first).
        # Unknown or missing action gates fail-closed (the handler
        # refuses them anyway). Live mode is refused inside the
        # handler regardless of the gate.
        action = str((args or {}).get("action", "")).strip().lower()
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action in ("status", "events"):
            return False
        if action == "webhook":
            return False
        if action in ("customer", "subscription", "payment_link",
                      "checkout", "invoice", "refund"):
            return (action, mode) not in (
                ("customer", "list"), ("customer", "get"),
                ("subscription", "status"),
                ("payment_link", "get"),
                ("invoice", "list"), ("invoice", "get"))
        return True
    if name == "mcp_registry":
        # R18-3 (IBM/mcp-context-forge inspiration): reads of the
        # server directory and the review store (server list,
        # discover, policy, status, events) change nothing;
        # adding, removing, enabling or disabling a server edits
        # mcp_servers.json and re-reads the live bus, and
        # recording a review writes the policy store — those gate
        # (C-14 approve-first). Unknown or missing action gates
        # fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action in ("discover", "policy", "status", "events"):
            return False
        if action == "server":
            return mode != "list"
        if action == "review":
            return True
        return True
    if name == "agent_call":
        # R18-4 (a2aproject/A2A inspiration): calling, cancelling
        # or forgetting an agent sends to (or drops) a third party,
        # so those gate (C-14 approve-first). Discovering a card,
        # listing known agents, reading a task, returning VYUK's own
        # static card, and reading the status/events ledger change
        # nothing — those stay free. Unknown or missing action gates
        # fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        mode = str((args or {}).get("mode", "")).strip().lower()
        if action in ("discover", "list", "card", "status", "events"):
            return False
        if action == "call":
            return True
        if action == "task":
            return mode == "cancel"
        if action == "remove":
            return True
        return True
    if name == "prompt_tune":
        # R18-5 (stanfordnlp/dspy inspiration): evaluating, tuning
        # or proposing sends the owner's dataset and prompts to the
        # owner's own configured Gemini endpoint and spends their
        # LM quota, so those gate (C-14 approve-first). Prompt and
        # dataset edits are local app-state (the wardrobe/lodging
        # precedent) and the reads change nothing — those stay free.
        # Unknown or missing action gates fail-closed (the handler
        # refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action in ("evaluate", "tune", "propose"):
            return True
        if action in ("prompt", "dataset", "variants", "status",
                      "events"):
            return False
        return True
    if name == "audio_localise":
        # R19-1 (Huanshere/VideoLingo inspiration): translating or
        # running the full pipeline sends the owner's audio transcript
        # to the owner's own configured Gemini endpoint and spends
        # their LM quota, so those gate (C-14 approve-first). The
        # project edits, the local transcription (faster-whisper)
        # and the local dub (Kokoro) are local app-state, and the
        # reads change nothing — those stay free. Unknown or missing
        # action gates fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action in ("translate", "localise"):
            return True
        if action in ("project", "transcribe", "dub", "status",
                      "events"):
            return False
        return True
    if name == "media_chat":
        # R19-4 (nexmoe/VidBee inspiration): asking a question or
        # requesting a summary sends the owner's transcript to the
        # owner's own configured Gemini endpoint and spends their LM
        # quota, so those gate (C-14 approve-first). The media
        # registration, the local transcript reads and the reads
        # change nothing — those stay free. Unknown or missing action
        # gates fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action in ("ask", "summarize"):
            return True
        if action in ("add", "list", "get", "remove", "status",
                      "events"):
            return False
        return True
    if name == "invoice":
        # R19-5 (al1abb/invoify inspiration): sending an invoice
        # SENDS real mail through the owner's Gmail (with the PDF
        # attached), so it gates (C-14 approve-first). Rendering a
        # PDF, listing/getting a template, setting/getting the seller
        # record and the reads are local app-state — those stay free.
        # Unknown or missing action gates fail-closed (the handler
        # refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action == "send":
            return True
        if action in ("render", "template", "seller", "list", "get",
                      "status", "events"):
            return False
        return True
    if name == "translations":
        # R19-6 (tolgee/tolgee-platform inspiration): translating a
        # key sends the owner's text to the owner's own configured
        # Gemini endpoint and spends their LM quota, so it gates
        # (C-14 approve-first, the audio_localise translate
        # precedent). Projects, keys, translations, import/export
        # and the reads are local app-state — those stay free.
        # Unknown or missing action gates fail-closed (the handler
        # refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action == "translate":
            return True
        if action in ("project", "key", "translation", "import",
                      "export", "status", "events"):
            return False
        return True
    if name == "voice_agent":
        # R18-6 (fonoster/fonoster inspiration): 'call' places a
        # real phone call through the operator's SIP trunk and
        # 'serve' registers to the trunk and answers inbound calls,
        # so both gate (C-14 approve-first). Agent and flow edits
        # are local app-state (the wardrobe/lodging precedent), the
        # local text run changes nothing, and the reads change
        # nothing — those stay free. Unknown or missing action
        # gates fail-closed (the handler refuses them anyway).
        action = str((args or {}).get("action", "")).strip().lower()
        if action in ("call", "serve"):
            return True
        if action in ("agent", "flow", "run", "stop", "status",
                      "events"):
            return False
        return True
    if name == "personal_skills":
        # v5.67: list/inspect are reads; admit changes library state
        # (a draft becomes admitted) and always asks first.
        # B8: run executes contained skill code and always asks first.
        action = str((args or {}).get("action", "")).strip().lower()
        return action in ("admit", "run")
    if name == "menu_action":
        # §7 consumers batch 1: listing menus is a read; invoking
        # one changes the app. Unknown or missing action gates
        # fail-closed.
        action = str((args or {}).get("action", "") or "").strip().lower()
        return action != "list"
    if name == "context_menu_action":
        # §7 consumers batch 8: listing an already-open menu is
        # a read; triggering opens a menu and invoking one
        # changes the app. Unknown or missing action gates
        # fail-closed.
        action = str((args or {}).get("action", "") or "").strip().lower()
        return action != "list"
    if name == "system_surface_action":
        # §7 consumers batch 1: surface listings and Start search
        # are reads; every other surface action changes the
        # machine. Unknown pairs gate fail-closed (the handler
        # refuses them anyway).
        surface = str((args or {}).get("surface", "")
                      or "").strip().lower()
        action = str((args or {}).get("action", "")
                     or "").strip().lower()
        if action == "list" and surface in (
                "tray", "taskbar", "notification", "settings"):
            return False
        if surface == "start" and action == "search":
            return False
        return True
    if name == "adapter_action":
        # §7 consumers batch 1: resolution itself changes nothing
        # — the gate keys on whether the resolution is
        # confirm-gated. The lookup is pure (registry only, no
        # observation, no dispatch): with an explicit foreground
        # the phrase resolves here. Without one the gate cannot
        # know without observing, so it gates fail-closed and the
        # handler observes + resolves (its own confirm stop
        # still applies). A phrase that cannot resolve (unknown,
        # quirk failure, app mismatch) needs no popup — the
        # handler refuses it honestly.
        _a = args or {}
        _fg = _a.get("foreground")
        if not isinstance(_fg, dict):
            return True
        try:
            from ..desktop import app_adapters as _aa  # Phase 3 S7a-2: moved to core/desktop/ (Phase 3 F1: ..desktop from inside core/funnel/)
            if _a.get("app_id") is not None:
                _identified = _aa.identify(_fg)
                if _identified.get("matched") != str(
                        _a.get("app_id")).strip().lower():
                    return False
            _resolved = _aa.resolve(
                str(_a.get("phrase", "") or ""), foreground=_fg,
                app_version=_a.get("app_version"), confirmed=False)
        except Exception:  # noqa: BLE001 — gate fail-closed
            return True
        return bool(_resolved.get("needs_confirmation"))
    if name == "drag_drop_action":
        # §7 consumers batch 2: every drag acts — there is no
        # read leg. A caller-supplied confirmed flag is NOT
        # approval (the popup is); the gate always pops and the
        # handler + engine apply their own confirm stops.
        return True
    if name == "journal_action":
        # §7 consumers batch 2: list and plan are pure reads
        # (re-reads of the journal file, no writes, no dispatch);
        # reconcile and compensate are effectful and always pop.
        # Unknown or missing actions gate fail-closed (the
        # handler refuses them anyway).
        action = str((args or {}).get("action", "")
                     or "").strip().lower()
        if action in ("list", "plan"):
            return False
        return True
    if name == "health":
        # v5.69: extract/summary/remind are reads or a held
        # reminder; record writes to his health store and always
        # asks first.
        action = str((args or {}).get("action", "")).strip().lower()
        return action == "record"
    if name in ("firewall_control", "fast_startup_control",
                "hibernate_setting"):
        # Status is a read; changing the setting is not (v5.5).
        action = str((args or {}).get("action", "")).strip().lower()
        return action != "status"
    if name == "env_var":
        # list/get are reads; set rewrites a persistent, reboot-
        # surviving variable (PATH class) — human gate (v5.9, M3).
        action = str((args or {}).get("action", "")).strip().lower()
        return action == "set"
    if name == "vpn_control":
        # status is a read; connecting/disconnecting reroutes the
        # machine's traffic — human gate (v5.9, sweep m15).
        action = str((args or {}).get("action", "")).strip().lower()
        return action in ("connect", "disconnect")
    if name == "power_plans":
        # v5.47 (programme item 11): listing plans is a read;
        # switching the ACTIVE plan changes persistent system
        # power policy — human gate. (The handler ALSO keeps its
        # own confirm=true flag for 'set'; the executor gate is
        # the fail-closed layer on top, as elsewhere.)
        action = str((args or {}).get("action", "")).strip().lower()
        return action == "set"
    if name == "network_control":
        # v5.47 (programme item 11): status / wifi_list only
        # report, and bluetooth_toggle is a no-op message (see
        # _is_read_action); turning the Wi-Fi adapter on/off or
        # connecting it to a network changes the machine's
        # network state — human gate.
        action = str((args or {}).get("action", "")).strip().lower()
        return action in ("wifi_on", "wifi_off", "wifi_connect")
    if name == "press_hotkey":
        # Tokens are normalized through control's alias map first
        # (v5.42, H15): "windows+r" must gate like "win+r".
        tokens = _normalized_hotkey_tokens((args or {}).get("keys", ""))
        if tokens & _HOTKEY_SHELL_KEYS:
            return True
        has_ctrl = bool(tokens & {"ctrl", "control"})
        has_alt = "alt" in tokens
        has_shift = "shift" in tokens
        if has_ctrl and has_shift and bool(tokens & {"esc", "escape"}):
            return True  # Ctrl+Shift+Esc (Task Manager), as before
        # Destructive / session combos (v5.42, H15).
        if has_alt and "f4" in tokens:
            return True  # Alt+F4 closes the focused app
        if has_ctrl and "w" in tokens:
            return True  # Ctrl+W closes the tab/window
        if has_shift and "delete" in tokens:
            return True  # Shift+Delete: permanent delete (also
            # covers Ctrl+Shift+Delete, browser-data wipe)
        if has_ctrl and has_alt and "delete" in tokens:
            return True  # Ctrl+Alt+Delete: the secure-attention screen
        # Any keystroke — Enter included, and the paste chords
        # Ctrl+V / Shift+Insert (H16) — aimed at a shell that owns
        # the foreground is one Enter away from running it (H11).
        if _foreground_is_shell():
            return True
        return False
    if name == "type_text":
        # Typing is free — unless the text lands in a shell (H11).
        return _foreground_is_shell()
    if name == "launch_application":
        # A shell/script host by name is run_command with extra
        # steps (v5.42, H11); ordinary apps stay ungated.
        # v5.46 (programme item 3, C1): the name class now also
        # covers interpreters/code-exec hosts (python, node,
        # regedit, mmc, msiexec, rundll32, regsvr32 — the shared
        # _SHELL_LAUNCH_NAMES set), AND the arguments are
        # scrutinised: a launch whose arguments invoke an
        # interpreter or carry a standalone host flag (-c,
        # -command, -encodedcommand, -file) gates no matter what
        # the app is called. The gate keys on NAME CLASS, never
        # on Start-apps inventory resolution — Camera, notepad,
        # chrome (incl. a URL argument) stay free. The handler
        # itself is unchanged; this is the executor-level gate,
        # fail-closed when no confirm handler is registered,
        # exactly like H11.
        _a = args or {}
        return (_is_shell_launch(_a.get("app_name", ""))
                or _arguments_invoke_interpreter(
                    _a.get("arguments", "")))
    if name == "open_path":
        # Double-clicking an executable/script is a launch (H11).
        return _is_executable_path((args or {}).get("path", ""))
    if name == "run_command":
        # Job management (v5.45, H3): polling, waiting on, listing
        # or cancelling a background job is not a new command — the
        # command itself was trust-checked and human-approved once,
        # at launch, before its job id existed. Gating every poll
        # would pop a confirmation per output chunk and teach the
        # human to wave them through. Launches (no action) stay
        # gated exactly as before.
        action = str((args or {}).get("action", "")).strip().lower()
        if action in ("poll", "status", "wait", "cancel", "list"):
            return False
        return True
    return None


CallClassification = namedtuple(
    "CallClassification", ["needs_confirmation", "is_read"])


def classify_call(name: str, args: dict[str, Any] | None) \
        -> CallClassification:
    """THE per-call gate verdict, from the one table. Returns
    (needs_confirmation, is_read) for this call:

    * is_read — the call observes only: the tool is statically
      "read", or this action is the tool's read action.
    * needs_confirmation — the tool's gate-action branch says so,
      or (no branch) the tool is statically "confirm".

    The two verdicts never both hold for the same call: a
    gate-action branch that gates a call never also reads it (the
    read split is the same table's other leg), and a statically
    "confirm" tool with a read action is exempted by the read leg
    (power_plans list, task_scheduler_control list, vpn_control
    status). tests/test_gaps23.py pins the invariant across the
    registry. Overlays (money guard, plugin gate class, taint,
    provenance lock) are applied by _needs_confirmation on top of
    this verdict, never by re-deriving it."""
    is_read = classify(name) == "read" or _is_read_action(name, args)
    gate = _gate_action(name, args)
    needs = gate if gate is not None else classify(name) == "confirm"
    return CallClassification(needs_confirmation=needs, is_read=is_read)
