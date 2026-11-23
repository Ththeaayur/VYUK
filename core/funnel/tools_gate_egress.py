"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from .tools_gate_readonly import (_READ_ONLY_TOOLS)


# Egress tools (v5.43, H17): reads that nevertheless SEND data to a
# host the model chose — a DNS name, a URL, a host:port. They sit in
# _READ_ONLY_TOOLS because they change nothing locally, but under
# injection taint "read-only" is the wrong question: a tainted
# session could read a secret with one tool and exfiltrate it inside
# the hostname or query string of the next. While the session is
# tainted, egress tools therefore require confirmation exactly like
# state-changing tools (see _needs_confirmation). Untainted, nothing
# about them changes. The set is EXACTLY these thirteen — a tool
# joins it only by being able to put model-chosen bytes on the wire.
# v5.46 (A2): this explicit set is kept, but it no longer BOUNDS
# the egress class — see _LOCAL_ONLY_READS / _is_egress_read below.
_EGRESS_TOOLS = frozenset({
    "dns_lookup", "http_headers", "wayback_lookup", "website_status",
    "ip_geolocate", "ping_host", "trace_route", "check_port",
    "ssl_cert_info", "domain_lookup", "link_health_check",
    "sniff_link", "rss_digest",
})

# Local-only reads (v5.46, programme item 4, A2) — the INVERSION
# of the egress list above. The hand-maintained 13-tool egress set
# kept missing network readers (web_search, link_unshorten,
# read_page_aloud, page_word_search and get_weather all measured
# FREE under taint in the round-3 review): a list of egress tools
# fails open every time a new network tool ships and nobody adds
# it. So the egress test for a built-in read-only tool is now
# `name in _READ_ONLY_TOOLS and name not in _LOCAL_ONLY_READS` —
# a read gates under taint UNLESS it is provably local. A new
# read-only tool joins _LOCAL_ONLY_READS only when its handler
# (followed one delegation level into the module function it
# calls) provably does NO network I/O: no requests / urllib /
# urlopen / socket connections / httpx / ftplib / smtplib /
# imaplib / poplib / playwright / webbrowser / DNS resolution,
# and no delegation to a network service (Google APIs, the
# browser engine, a weather/search/feed fetch helper). The
# v5.46 audit classified all 252 read-only tools this way:
# 213 provably local (below) and 39 egress — the 13 above plus
# browser_read, deadline_radar, get_todays_schedule,
# get_unread_email, get_upcoming_events, get_weather,
# inbox_zero_assist, link_unshorten, list_app_updates,
# meeting_prep_card, network_quality, packing_list,
# page_word_search, public_ip, quote_of_morning, read_page_aloud,
# scan_wifi_networks, search_email, speed_test, tab_census,
# unsubscribe_finder, update_restart_planner, web_search and
# wifi_info, and the two network plugins get_stock_quote /
# get_crypto_price (which ALSO carry the plugin egress class,
# H19b — that path is unchanged). Manually cleared false
# positives from the network-API grep: base64_codec /
# extract_from_text / url_encode_decode / signature_block (URL
# strings are data/regex, never fetched), find_process_on_port /
# network_adapters / list_connections (socket is only the
# address-family constants over psutil's local tables),
# power_requests (the word is powercfg's, not the library's),
# and sniff_file / sniff_text (local bytes/text only — unlike
# sniff_link, which fetches and stays egress). Tools the audit
# could not prove local (wifi_info's UDP probe to 8.8.8.8,
# scan_wifi_networks' radio scan, list_app_updates' winget
# source query, update_restart_planner's Windows Update search,
# the Google-backed calendar/mail readers) stay OUT and gate
# under taint — fail-safe by construction. The egress concept
# is consulted ONLY under taint (in _needs_confirmation's taint
# branch and in execute()'s taint-gated log labelling, both
# behind injection.tainted()), so untainted behaviour is
# unchanged for every tool.
_LOCAL_ONLY_READS = frozenset({
    "acronym_finder", "air_quality_advice", "anagram_finder", "app_crash_digest",
    "ascii_art", "base64_codec", "base_convert", "battery_drain_detective",
    "battery_health", "battery_info", "birthday_radar", "bluetooth_devices",
    "bmi_calculator", "case_convert", "chord_notes", "citation_format",
    "clipboard_files", "coffee_ratio", "coin_decision", "color_convert",
    "color_from_image", "compare_files", "compass_bearing", "compliment_engine",
    "compound_growth", "compound_interest", "cooking_time_per_weight", "cron_explain",
    "csv_column_stats", "csv_preview", "csv_query", "date_calculator",
    "day_review", "daylight_hours", "describe_camera_view", "dice_roller_plus",
    "discount_stack", "disk_health", "disk_index_status", "two_way_sync_status",  # R9-4 mutagen port: pure read of local state files "disk_write_hogs",
    "preview_photo_organisation",  # R9-8 elodie port: pure read of local folders, the dry-run plan
    "display_details",
    "distance_between", "dough_hydration", "download_eta", "drive_info",
    "duplicate_photo_finder", "egg_timer_math", "epoch_convert", "expiry_radar",
    "extract_from_text", "file_age_report", "file_hash", "filler_word_count",
    "find_duplicates", "find_files", "find_process_on_port", "flashcard_stats",
    "focus_score", "focus_timer_report", "folder_diff", "folder_outline",
    "folder_size", "folder_size_map", "fortune_cookie", "fuel_cost_split",
    "garden_watering", "generate_palette", "generate_uuid", "get_active_window",
    "get_activity_log", "get_current_time_date", "get_pixel_color", "get_plan",
    "get_system_info", "get_window_controls", "gpu_info", "habit_summary",
    "haiku", "hardware_info", "hash_text", "hosts_view",
    "hotkey_cheatsheet", "hourly_rate_convert", "image_compare", "image_info",
    "interval_name", "ip_subnet_calc", "journal_prompt", "journey_time",
    "json_diff", "json_format", "json_query", "knowledge_search",
    # R8-1 fastembed port: semantic_search_knowledge and
    # knowledge_embedding_status are provably local (local
    # KnowledgeStore only, no fetcher on their path) — they MUST
    # sit here, or _is_egress_read would misclassify them as
    # egress (any read-only tool outside this set reads as
    # egress), the deep_research precedent.
    "semantic_search_knowledge", "knowledge_embedding_status",
    # Darwin D1: search_documents and list_documents read only
    # the local KnowledgeStore (disk-bridged passages) — they
    # MUST sit here for the same reason.
    "search_documents", "list_documents",
    # Research wiring batch: deep_research is provably local
    # (local KnowledgeStore only, no fetcher on its path) — it
    # MUST sit here, or _is_egress_read would misclassify it as
    # egress (any read-only tool outside this set reads as
    # egress) and rung 2 would refuse a purely local read.
    "deep_research",
    "largest_files",
    "layover_time",
    # v5.71 Life Admin: pure computation — URL builders, static
    # checklists, trip assembly; no fetch, probe, file, or capture.
    "lifeadmin",
    # v5.72 Business: pure computation — state-flag proof, paper
    # render, local signature check; no fetch, probe, file, or
    # capture.
    "business",
    "list_audio_devices", "list_connections", "list_drivers",
    "list_fonts", "list_installed_apps", "list_local_devices", "list_network_shares",
    "list_plans", "list_printers", "list_restore_points", "list_routines",
    "list_services", "list_snippets", "list_startup_programs", "list_users",
    "list_window_layouts", "loan_calculator", "loan_payment", "lorem_text",
    "mad_libs", "maintenance_due", "meal_rotation", "meeting_cost",
    "metronome_bpm", "mic_check", "mnemonic_maker", "mood_trend",
    "moon_phase", "morse_translate", "name_generator", "nato_spell",
    "network_adapters", "note_frequency", "ocr_file", "ocr_screen",
    "oven_temp_convert", "overdue_promises", "pantry_check", "passive_voice_check", "password_strength",
    "path_audit", "pdf_info", "power_requests", "presence_report",
    "price_per_unit", "privacy_usage", "qr_decode_image", "quick_math",
    "rain_from_forecast", "random_choice", "read_clipboard", "read_file",
    "read_pdf_text", "reading_level", "reading_time", "recall_day",
    "recall_memories", "recent_files", "recipe_scale", "regex_test",
    "registry_read", "rent_split", "rhythm_subdivide", "roll_dice",
    "roman_convert", "room_brightness", "room_noise_level", "saved_wifi_networks",
    "savings_goal", "scale_notes", "screen_break_plan", "search_file_contents",
    # R9-3 whoosh port: search_indexed_files reads only the local
    # FTS5 index (sqlite3, no network) — it MUST sit here, or
    # _is_egress_read would misclassify it as egress.
    "search_indexed_files",
    # R10-4 python-adblock port: check_url_blocked and
    # filter_list_status read only a caller-supplied list file
    # (stdlib file I/O, no network) — they MUST sit here, or
    # _is_egress_read would misclassify them as egress.
    "check_url_blocked", "filter_list_status",
    # R16-8 the-wedding-website inspiration: event_budget is a
    # pure computation over the stored plan (tier rate x guests
    # + vendor quotes) — no network of any kind — so it MUST sit
    # here, or _is_egress_read would misclassify it as egress.
    "event_budget",
    "security_status", "semver_compare", "sense_trends", "sentence_stats",
    "signature_block", "site_permissions_review", "sleep_states", "slug_maker",
    "sniff_file", "sniff_text", "song_length_beats", "speed_distance_time",
    "spice_substitute", "split_bill", "stargazing_score", "startup_impact",
    "startup_time_report", "step_goal_math", "story_dice", "story_outline",
    "sunrise_sunset", "system_event_log", "tail_file", "tempo_terms",
    "text_diff", "text_stats", "time_sync_status", "timeline_recap",
    "timezone_convert", "timezone_meeting_planner", "tip_and_tax", "top_processes",
    "trip_countdown", "tuning_cents", "uia_find_control", "uia_read_tree",
    "unit_convert", "url_encode_decode", "uuid_generate", "uv_advice",
    "vocab_word", "walk_time", "watcher_status", "water_intake_math",
    "whats_pending", "windows_update_status", "word_frequency", "world_clock",
    "writing_prompt",
    # Conversion pass, combined W4/W5 registration (v5.64):
    # capture_route + eyes_fusion are provably local reads
    # (see _READ_ONLY_TOOLS) — no network, no writes.
    "capture_route", "eyes_fusion",
})

# Plugin gate classes (v5.45, H19b): name -> "read" / "egress" /
# "gated", populated by ToolRegistry.load_plugins from each
# plugin's declared capabilities (core/plugin_loader). A plugin
# with no declaration lands here as "gated" — the conservative
# default. Consulted by _needs_confirmation alongside the static
# _READ_ONLY_TOOLS / _EGRESS_TOOLS sets.
_PLUGIN_GATE_CLASS: dict[str, str] = {}


def _plugin_gate_class(name: str) -> str | None:
    return _PLUGIN_GATE_CLASS.get(name)


def _is_egress_read(name: str) -> bool:
    """True when a read can put model-chosen bytes on the wire
    (v5.46, programme item 4, A2): the explicit H17 egress set,
    a plugin in the egress gate class (H19b, unchanged), or —
    the inversion — ANY built-in read-only tool that is not in
    the provably-local _LOCAL_ONLY_READS set. Consulted only
    under injection taint."""
    if name in _EGRESS_TOOLS or _plugin_gate_class(name) == "egress":
        return True
    return name in _READ_ONLY_TOOLS and name not in _LOCAL_ONLY_READS
