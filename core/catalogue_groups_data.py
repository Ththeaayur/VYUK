"""The skills catalogue's group tables — the SINGLE source of truth.

GAPS-23 (F-V7): GROUP_ORDER + GROUP_MEMBERS moved verbatim here
from gui/skills_catalog_data.py. That file is a Qt module (it
defines the ItemDataRole constants the catalogue widget needs),
and the Qt import is what forced every non-GUI reader —
core/brain/domain_routers.catalogue_groups(), the packaging
checker, the snapshot generator, and the w9/w10 test helpers —
to AST-parse the GUI file instead of importing the tables.
A re-parse is a second source: it can drift, it can silently
read a stale copy, and it cannot be checked at import time.

This module is pure data (stdlib only, no Qt, no core imports),
so every reader imports it directly. gui/skills_catalog_data.py
is now a thin re-export shim (it keeps the Qt roles); the
snapshot (core/catalogue_snapshot.py) stays the fallback for a
frozen layout that failed to bundle this module.

Invariants, checked at import (disagreement is impossible by
construction, not by a test that agrees by hand):
- every group in GROUP_ORDER appears in GROUP_MEMBERS, and
  every GROUP_MEMBERS key is in GROUP_ORDER;
- every tool name appears in exactly ONE group (the catalogue
  comment's rule — a duplicate would route one tool twice).
"""

from __future__ import annotations


GROUP_ORDER = [
    "Hands & eyes",
    "Mind",
    "Web",
    "Your world",
    "Everyday skills",
    "Play & create",
    "System & apps",
    "Files & media",
    "Network & display",
    "Built-in",
    "More skills",
]


GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    # core/desktop/control.py (via _register_control_tools) — 16
    # core/desktop/control_plus.py register_all — 14
    # core/desktop/cursor.py register_all — 1
    # core/desktop/uia.py register_all — 4 (v4.3) + clear_clipboard (v5.4)
    # + eyes II (v5.11)                                              = 38
    "Hands & eyes": (
        "type_text", "press_hotkey", "mouse_control", "run_command",
        "window_control", "file_control", "close_application",
        "media_control", "set_brightness", "power_control",
        "write_clipboard", "open_path", "take_screenshot",
        "screen_look",  # v5.42 — E1 (core/tools.py): take_screenshot's sibling
        "get_active_window", "ocr_screen", "get_activity_log",
        "click_text", "get_window_controls", "click_element",
        "set_volume_level", "network_control", "display_control",
        "desktop_control", "process_control", "battery_info",
        "zip_control", "webcam_snapshot", "ip_camera", "game_server", "stage_lighting", "drag_mouse",
        "empty_recycle_bin", "download_file",
        "cursor_control",
        "uia_find_control", "uia_click_control", "uia_type_text",
        "uia_read_tree",
        "click_by_mark",  # v5.49 — item 30 (core/tools.py): set-of-marks click
        "clear_clipboard",  # v5.4 — clipboard family
        # v5.11 eyes II (core/skills_screen_capture.py); screenshot_region
        # itself is listed under Files & media (its original home)
        "capture_window", "get_pixel_color",
        # v5.64.1 conversion — Wave 5 capture/eyes tools
        "capture_route", "occluded_capture", "eyes_fusion",
        # B-R1-8 (2026-11-09) — MediaPipe gesture watching
        # (control_plus.py); grouped by the wave8_feel
        # catalogue-pin fix (2026-11-10).
        "watch_gestures",
        # v5.30 section 4 (core/skills_window_control.py)
        "window_snap", "window_place", "move_window_to_monitor", "type_clipboard",
        "click_and_drag_path", "scroll_until_found", "screen_diff",
        "focus_window_by_text", "hotkey_cheatsheet",
        "window_pin_toggle_all", "drag_file_onto",
        # v5.88 triage wire build 3 — forgiving selection (descriptor only)
        "forgiving_select",
        # §7 consumers batch 1 (2026-11-02) — funnel tools for the
        # §7 engines: menus (item 3), file dialogs (item 4),
        # system surfaces (item 7), app adapters (item 9).
        "menu_action", "file_dialog_action", "system_surface_action",
        "adapter_action",
        # §7 consumers batch 2 (2026-11-02): INTENTIONAL — 3
        # tools added (drag_drop_action, monitor_action,
        # journal_action) with the batch-1 §7 consumer tools.
        "drag_drop_action", "monitor_action", "journal_action",
        # §7 consumers batch 8 (2026-11-02): INTENTIONAL — 1
        # tool added (context_menu_action) with the §7
        # consumer tools.
        "context_menu_action",
    ),
    # core/brain/planner.py (via _register_mind_tools) — 9
    # + set_reminder from the extended pack in core/funnel/tools_handlers_pack_core1.py
    # + the day-journal pair from core/episodes.py (v5.2)
    # + quick smarts from core/skills_smart.py (v5.4) + II (v5.5)
    # + text toolkit + smarts III (v5.12)                            = 29
    "Mind": (
        "set_reminder",
        "create_plan", "list_plans", "get_plan", "update_plan_step",
        "delete_plan", "save_routine", "list_routines", "run_routine",
        # Field-check fix #32 (round-2 N5): the anticipation
        # routine runner — same Mind group as run_routine.
        "run_anticipation_routine",
        # Round-3 fix #4 (T12): the plan runner — same Mind
        # group as run_routine / run_anticipation_routine.
        "run_plan",
        "delete_routine",
        # v5.64.1 conversion — Wave 6 retrieval/people tools + Wave 4 replay
        "knowledge_search", "research",
        # R8-1 fastembed port: semantic search + index tools join
        # the knowledge family — same local store, same Mind group.
        "semantic_search_knowledge", "embed_knowledge",
        "knowledge_embedding_status",
        # Research wiring batch: deep_research joins its family —
        # the A6 loop over the local store, via the funnel.
        "deep_research",
        "remember_person", "remember_promise", "overdue_promises",
        "replay_programme",
        # v5.67 Skills track — personal skills trigger tool
        "personal_skills",
        # v5.69 Health track — health trigger tool
        "health",
        # v5.71 Life Admin track — lifeadmin trigger tool
        "lifeadmin",
        # v5.72 Business track — business trigger tool
        "business",
        "recall_day", "remember_today",
        # v5.4 quick smarts (core/skills_smart.py)
        "quick_math", "unit_convert", "world_clock",
        "generate_password",
        # v5.5 quick smarts II (core/skills_smart_encode.py)
        "base64_codec", "hash_text", "json_format",
        "generate_uuid",
        # v5.12 text toolkit + smarts III
        # (core/skills_text.py + core/skills_smart_chance.py)
        "text_stats", "case_convert", "extract_from_text",
        "replace_in_file",
        # v5.33 router — the toolbox dispatcher
        "use_toolbox",
        # R18-3 (IBM/mcp-context-forge inspiration) — the local
        # MCP server registry joins the Mind group with
        # use_toolbox: it manages the servers the toolbox calls.
        "mcp_registry",
        # R18-4 (a2aproject/A2A inspiration) — the A2A client joins
        # the Mind group with use_toolbox and mcp_registry: it calls
        # the external agents the owner discovers.
        "agent_call",
        # R18-5 (stanfordnlp/dspy inspiration) — the prompt
        # optimiser joins the Mind group with use_toolbox,
        # mcp_registry and agent_call: it tunes the prompts the
        # toolbox sends to the LM.
        "prompt_tune",
        # v5.32 S9c (core/skills_words.py) — words & writing
        "reading_time", "acronym_finder", "citation_format",
        "signature_block", "slug_maker", "word_frequency",
        "sentence_stats", "filler_word_count",
        "passive_voice_check", "quote_vault",
        "loan_calculator", "compound_interest", "roll_dice",
        "random_choice",
        # v5.31 wave 2 (core/skills_smart_convert.py) — word and number play
        "lorem_text", "morse_translate", "nato_spell", "roman_convert",
        "flashcard_add",
        "flashcard_review",
        "flashcard_answer",
        "flashcard_stats",
        "quiz_me",
        "mnemonic_maker",
        "spelling_bee",
        "vocab_word",
        "typing_test",
        "reading_level",
        # v5.88 triage wire builds 1-4 — study + finance playbook + codebase Q&A
        "study_practice", "study_review", "study_export_deck", "study_session",
        "study_plan", "study_progress", "study_exam_prep",
        "finance_playbook_add", "finance_playbook_list", "finance_playbook_vote",
        "finance_playbook_delete", "finance_playbook_curate",
        "dev_codebase_qa",
        # v5.89 Wave 6 — workflow / mission tools (Mind: planning + learning)
        "workflow_distil", "workflow_build_plan", "workflow_to_playbook_steps",
        "miss_compound", "miss_capability_map", "miss_to_playbook_steps",
        "mission_build_brief", "mission_build_plan", "mission_evidence_board",
        "mission_journal_entry", "mission_journal_append", "mission_run_job",
        # v5.90 Wave 7 — prediction / proposals / multiverse (Mind: planning)
        "sim_predict_plan", "sim_predict_report",
        "improvement_propose", "improvement_report",
        "multiverse_plan", "multiverse_compile", "multiverse_compile_report",
        # Libwiring sweep — teach mode (Mind: learned routines, beside
        # save_routine / replay_programme).
        "teach_record_sequence", "teach_list_sequences",
        "teach_replay_sequence",
    ),
    # core/browser.py register_all                                   = 6
    "Web": (
        "browser_control", "browser_read", "browser_click",
        "browser_type", "browser_select", "browser_press",
        # v5.64.1 conversion — Wave 4 browser-grade TLS read lane
        "curl_cffi_read",
        # v5.20 NOSE (core/skills_nose.py)
        "sniff_link",
        # v5.21 Web II (core/skills_web_research.py)
        "rss_digest", "link_unshorten", "page_watch", "http_headers",
        "ip_geolocate", "wayback_lookup",
        # v5.30 section 4 (core/skills_web_watch.py)
        "price_watch", "page_change_summary", "read_page_aloud",
        "link_health_check", "download_eta",
        "site_permissions_review", "tab_census",
        # v5.31 wave 2 (core/skills_web_lookup.py)
        "page_word_search", "domain_lookup",
        # R10-1 chrome-devtools-mcp port (core/desktop/browser_cdp_inspect.py)
        "browser_console", "browser_network", "browser_performance",
        # R10-3 browser-history port (core/desktop/browser_history.py)
        "browser_history", "browser_bookmarks",
        # R10-4 python-adblock port (core/desktop/browser_adblock.py)
        "check_url_blocked", "filter_list_status",
        # R10-5 python-readability port (core/article_extract.py)
        "extract_article",
    ),
    # core/google_services.py (via _register_world_tools)             = 12
    # (7 at v5.5 + sync_updates + 4 Group E actions, Stage 0 features
    # batch 2: find_free_slots, snooze_email, cancel_calendar_event,
    # reschedule_calendar_event)
    "Your world": (
        "get_todays_schedule", "get_morning_briefing",  # Round-3 fix Q6: the deterministic briefing tool reads the same calendar
        "get_upcoming_events", "find_free_slots",  # Stage 0 features batch 2: the free-slot finder reads the same calendar
        "get_unread_email", "search_email",
        "send_email", "snooze_email", "create_calendar_event",
        "cancel_calendar_event", "reschedule_calendar_event",  # Stage 0 features batch 2: the calendar actions
        "download_email_attachment",  # v5.5
        "sync_updates",  # GAPS-7 (BLOCKS #6): what's new since the last sync
        "room_brightness",  # v5.20 NOSE — the room, sensed on demand
        # v5.30 section 4 (core/skills_senses_checks.py) — room sensing
        "mic_check", "room_noise_level", "describe_camera_view",
        "screen_brightness_auto",
        # v5.30 section 4 (core/skills_productivity_day.py) — mail and
        # calendar driven
        "meeting_prep_card", "inbox_zero_assist",
        "unsubscribe_finder",
        # v5.31 wave 2 (core/skills_senses_reports.py) — timeline summaries
        "sense_trends", "presence_report",
        # v5.32 section 14 — timeline recap + watcher health
        "timeline_recap", "watcher_status",
        # v5.32 S9b (core/skills_admin.py) — life admin
        "document_vault", "warranty_log", "expiry_radar",
        "contact_card", "service_schedule", "maintenance_due",
        "emergency_card", "gift_log", "borrow_log",
        "asset_inventory",
        "home_inventory",
        "warehouse_stock",
        "bookings",
        "lodging",
        "trip_countdown",
        "journey_time",
        "fuel_cost_split",
        "layover_time",
        "timezone_meeting_planner",
        "sunrise_sunset",
        "solar_yield",
        "wardrobe",
        "billing",
        # R18-8 (bukosabino/ta inspiration) — the indicator engine
        # over operator-supplied OHLCV candles joins the Your world
        # group with billing: no live feed, no market-data download.
        "technical_analysis",
        # R18-6 (fonoster/fonoster inspiration) — the local
        # voice-agent / call-flow engine joins the Your world group
        # with wardrobe and billing: it answers phone calls with a
        # script the owner writes, through the owner's own SIP trunk.
        "voice_agent",
        "moon_phase",
        "compass_bearing",
        "distance_between",
        "speed_distance_time",
        "uv_advice",
        "rain_from_forecast",
        "garden_watering",
        "plant_care_log",
        "pet_feeding_log",
        "walk_time",
        "step_goal_math",
        "stargazing_score",
        "daylight_hours",
        "air_quality_advice",
        # v5.88 triage wire builds 2 + 4 — contacts + social ledger
        "vcard_import", "vcard_export",
        "social_occasion_add", "social_occasion_upcoming",
        "social_promise_add", "social_promise_list", "social_promise_complete",
        "social_gift_add", "social_debt_add", "social_debt_settle",
        "social_balances", "social_stay_in_touch", "social_send",
        # SOCIAL Phase 1 (approved design, WhatsApp first):
        # wa.me + Voice Access
        "scheduled_agents",  # GAPS-8 (BLOCKS #7): scheduled
                             # agents + morning digest
        "social_nudge_check",
        "social_nudge_snooze", "social_nudge_pause", "social_entry_delete",
    ),
    # core/skills_plus.py register_all + desktop life (v5.4)
    # + layouts/snippets (v5.11) + desktop4 (v5.12)                  = 25
    "Everyday skills": (
        "list_installed_apps", "uninstall_app", "print_file",
        "read_pdf_text", "speak_text", "eject_drive",
        "set_focus_assist", "find_files", "monitor_control",
        # v5.4 desktop life (core/skills_desktop_prefs.py)
        "set_wallpaper", "toggle_dark_mode", "keep_awake",
        "notify_user",
        # v5.11 (core/skills_desktop_layouts.py + core/skills_productivity.py)
        "save_window_layout", "restore_window_layout",
        "list_window_layouts", "delete_window_layout",
        "save_snippet", "paste_snippet", "list_snippets",
        "delete_snippet",
        # v5.12 (core/skills_desktop_tiling.py)
        "tile_windows", "focus_session", "clipboard_files",
        # v5.20 NOSE (core/skills_nose.py)
        "sniff_text",
        # v5.21 Productivity II (core/skills_productivity_tools.py)
        "pomodoro", "stopwatch", "countdown", "timezone_convert",
        "date_calculator", "password_strength", "json_query",
        # v5.30 section 4 (core/skills_senses_checks.py)
        "speaker_check", "voice_note", "read_selection_aloud",
        "listen_for_sound",
        # v5.30 section 4 (core/skills_productivity_day.py)
        "focus_timer_report", "deadline_radar", "habit_checkin",
        "water_break", "stand_up_nudge", "bill_calendar",
        "eye_rest_timer", "breathing_guide", "log_glass",  # R8-4 screen-break port
        "shopping_list", "packing_list", "birthday_radar",
        # v5.31 wave 2 (core/skills_productivity_logs.py + smart5 pair)
        "expense_log", "decision_log", "reading_list", "whats_pending",
        "meeting_cost", "split_bill", "bmi_calculator",
        "recipe_scale",
        "oven_temp_convert",
        "cooking_time_per_weight",
        "pantry_check",
        "meal_rotation",
        "coffee_ratio",
        "dough_hydration",
        "egg_timer_math",
        "spice_substitute",
        "grocery_list",
        "savings_goal",
        "compound_growth",
        "loan_payment",
        "tip_and_tax",
        "hourly_rate_convert",
        "budget_envelope",
        # R16-8 the-wedding-website inspiration
        # (core/event_planning.py)
        "event_plan", "event_guest", "event_vendor",
        "event_budget", "event_milestone", "event_schedule",
        # R18-7 Attendize inspiration (core/event_ticketing.py)
        "event_tickets",
        # R18-10 dominikbraun/timetrace inspiration (core/time_tracker.py)
        "time_tracker",
        # R19-2 google/or-tools inspiration (core/optimise.py)
        "optimise",
        # R19-3 aureuserp/aureuserp inspiration (core/erp.py)
        "erp",
        # R19-5 al1abb/invoify inspiration (core/invoice.py)
        "invoice",
        # R19-6 tolgee/tolgee-platform inspiration
        # (core/translations.py)
        "translations",
        # R19-7 Donkie/Spoolman inspiration (core/filament.py)
        "filament",
        # R19-8 Django-CRM/Django-CRM inspiration (core/deals.py)
        "deals",
        # R19-10 VROOM-Project/vroom inspiration (core/vehicle_routes.py)
        "vehicle_routes",
        "price_per_unit",
        "discount_stack",
        "rent_split",
        "subscription_audit",
        # Stage 0 features batch 3 (Group F, 2026-11-22): the money
        # alerts check and the portfolio tracker, with the other
        # money tools.
        "check_money_alerts", "portfolio_tracker",
        "fund_returns", "mutual_fund_nav",  # R8-8 mftool port
        "habit_track",
        "habit_summary",
        "water_intake_math",
        "screen_break_plan",
        "journal_prompt",
        "mood_log",
        "mood_trend",
        "gratitude_log",
        "focus_score",
        "day_review",
    ),
    # v5.13 games + creative (core/skills_games.py +
    # core/skills_creative.py) + creative II + studio (v5.14)       = 18
    "Play & create": (
        "play_music",  # v5.52 (item 4) — actually starting music
        "tic_tac_toe", "hangman", "number_guess", "trivia_quiz",
        "word_game",
        "ascii_art", "generate_palette", "create_art",
        "generate_melody", "writing_prompt",
        # v5.14 (core/skills_creative_generate.py + core/skills_studio.py)
        "fractal_art", "compose_song", "generate_beat",
        "name_generator", "story_outline", "haiku",
        "make_wallpaper", "palette_image",
        # v5.21 Creative III (core/skills_creative_play.py)
        "color_from_image", "gradient_css", "story_dice",
        "anagram_finder", "metronome", "binaural_tone",
        # v5.30 section 4 (core/skills_fun.py)
        "music_trivia", "story_chain", "word_of_the_day",
        "compliment_engine", "dice_roller_plus", "coin_decision",
        "ambient_scene", "quote_of_morning",
        # v5.31 wave 2 (core/skills_fun_party.py)
        "riddle_time", "rock_paper_scissors", "mad_libs",
        "fortune_cookie",
        # v5.90 Wave 8 Build 3 — ENT shelf catalogue (Play &
        # create: entertainment records, no asset bundled).
        "ent_shelf_list",
        "metronome_bpm",
        "note_frequency",
        "practice_log",
        "scale_notes",
        "chord_notes",
        "interval_name",
        "tempo_terms",
        "tuning_cents",
        "song_length_beats",
        "rhythm_subdivide",
        # v5.88 triage wire builds 2-3 — creative rights/subtitles/timeline/marking
        "creative_rights_check", "creative_rights_add",
        "creative_subtitle_edit", "creative_subtitle_export",
        # R19-1 Huanshere/VideoLingo inspiration (core/audio_localise.py)
        "audio_localise",
        # R19-4 nexmoe/VidBee inspiration (core/media_chat.py)
        "media_chat",
        "creative_timeline", "creative_mark_image",
        # R8-7 basic-pitch port — audio-to-MIDI transcription.
        "audio_to_midi", "audio_to_midi_status",
    ),
    # core/skills_system.py register_all + depth (v5.4) + II (v5.5)
    # + system IV (v5.11)                                            = 37
    "System & apps": (
        # Wave 7 Build 2 — undo disclosure, status, and execution
        # (system safety surface, same card as the Doctor undo).
        "undo_cannot_list", "undo_status", "undo_last_change",
        # R8-6 yasb port — the always-on live-vitals bar (system
        # surface, same card as the Doctor undo).
        "status_strip",
        # R8-9 pypresence adoption — Discord Rich Presence (system
        # surface, same card as the Doctor undo).
        "discord_presence", "set_discord_client_id",
        # R11-1 ollama port (core/ollama_models.py)
        "ollama_status", "ollama_pull", "ollama_delete",
        # R17-2 trivy inspiration (core/security_scan.py)
        "security_scan",
        # v5.90 Wave 7 Build 1 — filesystem sandbox (system safety surface).
        "sandbox_run", "sandbox_list",
        # v5.21 System III (core/skills_system_bench.py)
        "startup_impact", "env_backup", "disk_speed_test",
        "gpu_info", "windows_update_status",
        "winget_install", "list_app_updates", "winget_upgrade_all",
        "list_startup_programs", "set_startup_program", "list_services",
        "service_control", "power_plans", "battery_health", "env_var",
        "registry_read", "disk_health", "clean_temp_files",
        "largest_files", "find_duplicates",
        # v5.4 system depth (core/skills_system_monitor.py)
        "top_processes", "system_event_log", "create_restore_point",
        "task_scheduler_control", "list_printers",
        "set_default_printer", "set_screen_timeout",
        # v5.5 system depth II (core/skills_system_inventory.py)
        "hardware_info", "list_drivers", "drive_info",
        "list_restore_points", "security_status", "firewall_control",
        "list_users", "list_fonts", "power_requests", "sleep_states",
        "hibernate_setting", "fast_startup_control",
        # v5.11 system IV (core/skills_system_tuning.py)
        "mic_control", "set_process_priority", "privacy_usage",
        # v5.30 section 4 (core/skills_system_health.py)
        "startup_time_report", "battery_drain_detective",
        "disk_write_hogs", "update_restart_planner",
        "restore_point_before_changes", "app_crash_digest",
        "peripheral_roll_call", "screen_time_today",
        "safe_eject_all",
        # v5.31 wave 2 (core/skills_system_net_admin.py)
        "hosts_view", "path_audit", "time_sync_status",
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
        # v5.88 triage wire build 3 — session identity + diagnostics preview
        "session_identity", "diagnostics_preview",
        # v5.89 Wave 6 — extension harness (System & apps: governance)
        "extension_audit", "extension_status", "extension_register",
        "extension_approve", "extension_enable", "extension_disable",
        "extension_revoke",
        # v5.90 Wave 8 Build 3 — Phase 5 picks (System & apps:
        # interface descriptors, port round-trip, programme
        # records, money plans, and the cluster report).
        "phase5_interface_describe", "port_roundtrip",
        "plat_tools_record", "fin_plan_record", "phase5_report",
        # v5.90 Wave 8 Build 4 — CATALOGUE FIX (Part 0): the
        # Wave 8 Build 1 anticipation-grant path and Build 2
        # Track L tools were registered but never grouped,
        # so they fell back to "More skills" — contradicting
        # the Track L checklist's registry_total ==
        # catalogue_total and More skills 0 check. They
        # belong here (System & apps: governance + release),
        # alongside the Wave 6 harness and Phase 5 entries.
        "antic_suggest_cards", "antic_grant_status",
        "antic_cards_report",
        "release_checklist", "installer_validate",
        "release_checklist_report",
        # v5.90 Wave 8 Build 4 — feel, non-veto only
        # (System & apps: feel validation + orb mapping +
        # the feel report; a report is not an approval).
        "feel_validate", "feel_orb_map", "feel_report",
        # v5.90 Wave 8 Build 5 — learning-last continuation
        # (System & apps: proven-only spec production + the
        # spec report; a spec changes nothing and is never
        # applied by these tools).
        "learnlast_spec", "learnlast_report",
        # v5.91 first-open continuous opt-in (System & apps:
        # the choice mechanism only — prompt, choice status,
        # choice record, per-feature delete; no continuous
        # capture is wired here).
        "continuous_first_open", "continuous_choice_status",
        "continuous_record_choice", "continuous_delete_data",
        # Wave 9 Build 1 wellbeing (System & apps:
        # suggestions only — break plan, reminder,
        # wind-down, day summary, plan record; nothing
        # scheduled, nothing measured, nothing prescribed).
        "well_break_plan", "well_reminder", "well_winddown",
        "well_day_summary", "well_record_plan",
        # Wave 9 Build 2 communications (System & apps:
        # draft, recipient check, send readiness, draft
        # record; nothing is sent, no send pipeline here).
        "comms_draft_message", "comms_check_recipient",
        "comms_send_readiness", "comms_record_draft",
        # Wave 9 Build 3 local IoT (System & apps:
        # device descriptor, state summary, network
        # check, device record; nothing reached, polled,
        # controlled, scanned, or moved).
        "iot_local_device", "iot_device_state",
        "iot_network_check", "iot_record_device",
        # Wave 9 Build 4 hardware-adjacent deltas
        # (System & apps: inventory record, plan
        # descriptor, safety disclaimer, plan record;
        # nothing moves, nothing is sized, rated, or
        # signed off).
        "hardware_inventory", "hardware_plan",
        "hardware_safety", "hardware_record_plan",
        # Wave 9 Build 5 accessibility deltas
        # (System & apps: text note, snapshot
        # description, voice-first routine, preference
        # record; nothing read live, nothing inferred,
        # no one labelled, nothing scored).
        "access_text_clarity", "access_window_describe",
        "access_voice_first_routine",
        "access_record_preference",
        # Wave 9 Build 6 network deltas (System & apps:
        # network descriptor, status summary, read-only
        # check, network record; nothing scanned, probed,
        # connected, resolved, or reached).
        "net_network_descriptor", "net_network_status",
        "net_readonly_check", "net_record_network",
        # Wave 9 Build 7 P14 gaming deltas (System &
        # apps: session descriptor, macro descriptor,
        # drive-only media note, gaming record; no game
        # read, controlled, or automated, no GPL tool
        # bundled).
        "game_session_describe", "game_macro_describe",
        "game_media_note", "game_record_gaming",
        # Wave 9 Build 8 P15 / P16 / P17 PILOT slices
        # (System & apps: bench, steps, session, plan,
        # workflow, review note, pilot record; nothing
        # executed, read live, deployed, or signed).
        "pilot_bench", "pilot_build_steps",
        "pilot_study_session", "pilot_revision_plan",
        "pilot_dev_workflow", "pilot_review_note",
        "pilot_record_pilot",
        # Wave 9 Build 9 P19 / P20 / P23
        # (System & apps: trip, door-to-door, transport,
        # loadout, scene, spatial, sonify, recipe,
        # scoreboard, travel record; nothing booked,
        # paid, routed, driven, rendered, or moved).
        "travel_trip", "travel_door_to_door",
        "travel_transport", "travel_loadout",
        "travel_scene", "travel_spatial",
        "travel_sonify", "travel_recipe",
        "travel_scoreboard", "travel_record",
        # Wave 9 Build 10 queued graduations 1
        # (System & apps: gate, retention, indicator,
        # and ledger descriptors by kind, plus the
        # graduation record; nothing captured, granted,
        # chosen for any user, or decided; PF-3 open).
        "grad_describe", "grad_record",
        # Wave 10 Build 1 P23 release spine
        # (System & apps: route, verify, and train
        # descriptors by kind, plus the release
        # record; nothing released, signed, compiled,
        # published, fed, or decided; ruling 34 open).
        "release_describe", "release_record",
        # Wave 10 Build 2 P23 trust + help + changelog
        # (System & apps: trust, transparency, help,
        # issues, runbook, changelog, and audience
        # descriptors by kind, plus the trust record;
        # nothing sent, published, fed, claimed
        # compliant, or shipped; ruling 34 open).
        "trust_describe", "trust_record",
        # Wave 10 Build 3 P23 first-run cluster
        # (System & apps: exit, first_command, nosetup,
        # playground, and fix descriptors by kind, plus
        # the first-run record; nothing exited, run,
        # fixed, installed, applied, or sent; ruling 34
        # open; no conformance claimed).
        "firstrun_describe", "firstrun_record",
        # Wave 10 Build 4 P23 conformance evidence
        # (System & apps: evidence-ledger and
        # conformance-statement descriptors by kind,
        # plus the evidence record; nothing claimed
        # conformant, no audit performed, no live test
        # run, no live screen read; ruling 34 open).
        "accessconf_describe", "accessconf_record",
        # R7-5 kovrel (core/companion.py): the phone browser as a
        # remote for the laptop — LAN-only server, QR+PIN pairing.
        "start_companion", "stop_companion", "companion_status",
        "companion_pairing",
        # R7-6 micro-wake-word (core/voice/wake_training.py):
        # train-your-own wake word from synthetic speech, tiny
        # streaming model, model slot armed in the detector.
        "train_custom_wake_word", "enable_custom_wake_model",
        "list_custom_wake_models", "custom_wake_model_status",
        # R7-7 taskflow (core/dagflow.py): a DAG of funnel-tool
        # calls with dependencies, trigger rules, retries and
        # SQLite run history — the "when X, do Y" engine.
        "define_workflow", "run_workflow",
        "list_workflows", "workflow_runs",
        # R7-8 proactive-agent (core/proactivity.py): a
        # when-to-intervene scorer + 36-case benchmark,
        # MEASUREMENT MODE ONLY — scored suggestions, never actions.
        "score_intervention", "run_proactivity_bench",
        "list_proactivity_scores", "proactivity_bench_results",
    ),
    # core/skills_media.py register_all + 2 (v5.4) + 5 (v5.5)
    # + split_pdf (v5.11) + files III (v5.12) + photo lab (v5.13)
    # + photo effects (v5.14)                                        = 38
    "Files & media": (
        "sniff_file",  # v5.20 NOSE — first, so it leads its group
        # v5.21 Files & media II (core/skills_files_media_ops.py)
        "image_compare", "make_sprite_sheet", "batch_watermark",
        "pdf_extract_images", "audio_trim", "audio_fade",
        "folder_size_map",
        "convert_image", "resize_image", "compress_image", "ocr_file",
        "file_hash", "recent_files", "screenshot_region",
        "batch_rename", "create_shortcut",
        "folder_size", "record_audio", "list_audio_devices",
        # R7-4 meetily (core/meeting.py): live meeting capture ->
        # on-device transcription -> notes; same group as record_audio.
        "start_meeting", "stop_meeting", "meeting_status",
        "transcribe_audio",
        # R8-3 sumy port (core/summary.py): extractive TextRank
        # summary of caller-supplied text; same group as the
        # meeting notes it now feeds.
        "summarise_text",
        "set_microphone_mute", "app_volume",
        # v5.4 (core/skills_system_monitor.py + core/skills_smart.py)
        "search_file_contents", "compare_files",
        # R9-3 whoosh port (core/disk_index.py): persistent ranked
        # FTS5 content index over user-chosen folders; same group as
        # the live scan it complements (search_file_contents).
        "index_files", "search_indexed_files", "disk_index_status",
        # v5.5 (core/skills_files_ops.py)
        "merge_pdfs", "image_info", "shred_file", "sync_folder", "sync_folders_two_way", "two_way_sync_status",
        # R9-8 elodie port (core/photo_organise.py): EXIF photo
        # organiser — dated, location-aware folders, own code.
        "organise_photos", "preview_photo_organisation",
        # R8-5 magic-wormhole port (core/wormhole.py): code-phrase
        # encrypted file transfer between two machines on this network.
        "wormhole_offer", "wormhole_receive", "wormhole_status",
        # R9-2 rustic adoption (core/rustic_backup.py): encrypted,
        # deduplicated user-file backup through the operator-supplied
        # rustic binary; password via RUSTIC_PASSWORD, never stored.
        "init_backup_repo", "backup_files", "list_backup_snapshots",
        "restore_backup", "backup_status",
        # R17-5 komga port (core/ebook_library.py): the owner's own
        # EPUB/CBZ library with reading progress and a LAN-only OPDS
        # 1.2 catalogue + KOReader Sync server; same group as the
        # file libraries it complements (index_files, organise_photos).
        "ebook_library",
        "copy_file_to_clipboard",
        # v5.11 (core/skills_media_convert.py) — resize_image/convert_image
        # above are the same entries; v5.11 replaced their handlers
        "split_pdf",
        # v5.12 (core/skills_files_inspect.py)
        "folder_diff", "strip_image_metadata", "pdf_info",
        "rotate_pdf",
        # v5.13 photo lab (core/skills_photo.py)
        "crop_image", "rotate_image", "adjust_image",
        "filter_image", "watermark_image", "add_border",
        "make_collage",
        # v5.14 photo effects (core/skills_photo_effects.py)
        "kaleidoscope_image", "duotone_image", "pixelate_image",
        "glitch_image",
        # v5.30 section 4 (core/skills_files_recipes.py)
        "folder_watch_cook", "photo_face_blur",
        "screenshot_to_text_note", "pdf_merge_bookmarks",
        "pdf_split_by_text", "image_contact_sheet",
        "audio_speed_change", "silence_trim",
        "duplicate_photo_finder", "file_age_report",
        # v5.30 section 4 (core/skills_web_watch.py) — reads an image
        "qr_decode_image",
        # v5.31 wave 2 (core/skills_files_data.py)
        "csv_preview", "csv_query", "csv_column_stats",
        "folder_outline", "tail_file", "json_diff",
    ),
    # core/skills_network.py register_all + 3 (v5.4) + 5 (v5.5)
    # + network III (v5.11) + network IV + display II (v5.12)
    # + NOSE radar/quality (v5.20; wifi_networks retired into the radar) = 35
    "Network & display": (
        "scan_wifi_networks", "network_quality", "wifi_info",
        "connect_wifi", "forget_wifi",
        "public_ip", "speed_test", "ping_host", "dns_lookup",
        "dns_flush", "ip_renew", "bluetooth_devices", "display_details",
        "set_resolution", "window_always_on_top", "window_opacity",
        "mouse_settings", "paste_text",
        # v5.4 (core/skills_system_monitor.py + core/skills_desktop_prefs.py)
        "vpn_control", "virtual_desktop", "open_settings_page",
        # v5.5 (core/skills_network_diag.py)
        "list_connections", "trace_route", "saved_wifi_networks",
        "network_adapters", "check_port",
        # v5.11 (core/skills_network_wake_shares.py)
        "wake_on_lan", "list_network_shares",
        # v5.12 (core/skills_network_web_diag.py + core/skills_display_settings.py)
        "website_status", "ssl_cert_info", "list_local_devices",
        "find_process_on_port",
        "set_refresh_rate", "set_screen_rotation",
        # v5.30 section 4 (core/skills_system_health.py) — Wi-Fi by room
        "wifi_dead_spot_map",
        # Libwiring sweep — wallpaper pilot state resolution (display
        # family, beside set_refresh_rate / set_screen_rotation).
        "wallpaper_state",
        # Wallpaper apply (2026-11-03) — same display family as
        # wallpaper_state; it applies what wallpaper_state resolves.
        "wallpaper_apply",
        # Wallpaper revert (2026-11-05) — same display family;
        # it restores the desktop from before the last change.
        "wallpaper_revert",
    ),
    # core/funnel/tools_handlers_pack_core1.py: _register_default_tools (7) +
    # _register_extended_tools (6 more here; set_reminder is Mind)   = 13
    "Built-in": (
        "launch_application", "open_url", "get_current_time_date",
        "adjust_volume", "lock_screen", "remember_user_fact",
        "recall_memories",
        "web_search", "get_weather", "read_clipboard", "write_file",
        "read_file", "get_system_info",
        "app_settings",  # v5.52 (item 8) — her own settings, from chat
    ),
    # Fallback only — membership is computed, never listed, so a new
    # or plugin-declared tool lands here instead of vanishing.
    "More skills": (),
}


def _check_invariants() -> None:
    """Fail loudly at import if the tables disagree with each other."""
    order_set = set(GROUP_ORDER)
    if len(order_set) != len(GROUP_ORDER):
        raise ValueError(
            "catalogue_groups_data: GROUP_ORDER has a duplicate group")
    member_keys = set(GROUP_MEMBERS)
    if member_keys != order_set:
        raise ValueError(
            "catalogue_groups_data: GROUP_MEMBERS keys "
            f"{sorted(member_keys ^ order_set)} are not GROUP_ORDER")
    seen: dict[str, str] = {}
    for group, names in GROUP_MEMBERS.items():
        for name in names:
            if name in seen:
                raise ValueError(
                    "catalogue_groups_data: tool %r is in two groups "
                    "(%r and %r)" % (name, seen[name], group))
            seen[name] = group


_check_invariants()
