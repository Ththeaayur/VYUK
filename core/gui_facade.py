"""The GUI boundary: the single GUI-facing surface of core.

GAPS-15 (2026-11-13, architecture-review flaw #6): the GUI
layer had no boundary — 75 of 165 GUI files imported core
internals directly (54 distinct core modules), and core
reached back into GUI. The rule this module enforces is:

* GUI code imports core ONLY through this module
  (`from voice_assistant.core.gui_facade import ...` or
  `from core.gui_facade import ...` — both spellings are
  the same file under the tree's two import spellings).
* Core code NEVER imports GUI — and never imports this
  module either (core code imports its source modules
  directly; this module is the GUI-facing surface).

The exports are LAZY (PEP 562 `__getattr__`): importing
this module imports nothing from core, so it can never
fail and can never drag Qt (or GUI) in. Each name
resolves relative to this module's own package, so the
`core.*` and `voice_assistant.core.*` spelling families
each keep getting exactly the objects they already had.
Names whose source module needs Qt (or sounddevice)
raise the same ModuleNotFoundError the direct import
would raise today, at the same point — the lazy facade
preserves each GUI file's current behaviour exactly,
including on interpreters without Qt.

Two names are renamed from their source spelling because
the flat export namespace collides: `config_module` is
the core.config MODULE (the name `config` is the
AppConfig instance), and `voice_live_session` is the
GeminiLiveSession singleton from core/voice/live_client
(the name `live_session` is the brain BrainSession
singleton).

This module is stateless: pure re-exports, no behaviour.
The enforcement check lives in prove_gui_boundary.py
and tests/test_gui_boundary.py.
"""

from __future__ import annotations

import importlib


# name -> (relative module, attribute or None).
# attribute None means the name IS the module itself.
_EXPORTS = {
    "AdvisorySpeakerVerifier": (".brain.turn_machine", "AdvisorySpeakerVerifier"),
    "AudioManager": (".voice.audio", "AudioManager"),
    "DATA_DIR": (".config", "DATA_DIR"),
    "DICTATION_BANNER_TEXT": (".brain.turn_machine", "DICTATION_BANNER_TEXT"),
    "DICTATION_ENTER_ANNOUNCEMENT": (".brain.turn_machine", "DICTATION_ENTER_ANNOUNCEMENT"),
    "DICTATION_EXIT_ANNOUNCEMENT": (".brain.turn_machine", "DICTATION_EXIT_ANNOUNCEMENT"),
    "DictationMode": (".brain.turn_machine", "DictationMode"),
    "DirectednessGate": (".brain.turn_machine", "DirectednessGate"),
    "FLUENT_WINDOWS_ACTIONS": (".icon_manifest", "FLUENT_WINDOWS_ACTIONS"),
    "INTERRUPTED_STATES": (".brain.task_ledger", "INTERRUPTED_STATES"),
    "IconRef": (".icon_manifest", "IconRef"),
    "LADDER_LEVELS": (".brain.visual_channel", "LADDER_LEVELS"),
    "LANGUAGES": (".languages", "LANGUAGES"),
    "LessonPanelState": (".brain.visual_channel", "LessonPanelState"),
    "LocalLiveSession": (".voice.local_client", "LocalLiveSession"),
    "ManifestProblem": (".icon_manifest", "ManifestProblem"),
    "MediaDucking": (".brain.turn_machine", "MediaDucking"),
    "MemoryStore": (".memory.memory", "MemoryStore"),
    "ORB_STYLES": (".app_settings", "ORB_STYLES"),
    "PRIMARY_SYSTEM": (".icon_manifest", "PRIMARY_SYSTEM"),
    "PTT_BUTTON_LABEL": (".brain.turn_machine", "PTT_BUTTON_LABEL"),
    "PTT_BUTTON_TOOLTIP": (".brain.turn_machine", "PTT_BUTTON_TOOLTIP"),
    "PushToTalk": (".brain.turn_machine", "PushToTalk"),
    "RoutineScheduler": (".brain.routine_scheduler", "RoutineScheduler"),
    "ScreenWatcher": (".desktop.screen_watch", "ScreenWatcher"),
    "TRANSCRIPTION_PROFILES": (".languages", "TRANSCRIPTION_PROFILES"),
    "TaskLedger": (".brain.task_ledger", "TaskLedger"),
    "TurnCoordinator": (".brain.task_coordinator", "TurnCoordinator"),
    "VERSION": (".version", "VERSION"),
    "VoiceFailoverGate": (".brain.turn_machine", "VoiceFailoverGate"),
    "WatcherEngine": (".watchers", "WatcherEngine"),
    "_KOKORO_VOICE_ROSTER": (".languages", "_KOKORO_VOICE_ROSTER"),
    "_stored_env_file_value": (".config", "_stored_env_file_value"),
    "action_journal": (".desktop.action_journal", None),
    "activity_feed": (".activity_feed", None),
    "always_allow": (".always_allow", None),
    "ambient": (".brain.ambient", None),
    "asset_repair": (".health.asset_repair", None),
    "audio": (".voice.audio", None),
    "audio_manager": (".voice.audio", "audio_manager"),
    "audit": (".audit", None),
    "boot_sequence": (".desktop.boot_sequence", None),
    "browser_enable": (".desktop.browser_enable", None),
    "build_manifest": (".icon_manifest", "build_manifest"),
    "cancel_waking": (".taskwake", "cancel_waking"),
    "capture_guard": (".capture_guard", None),
    "catalogue_groups_data": (".catalogue_groups_data", None),
    "changelog": (".changelog", None),
    "chat_thread": (".chat_thread", None),
    "clipboard_monitor": (".desktop.clipboard_monitor", "clipboard_monitor"),
    "clipboard_notice": (".desktop.clipboard_monitor", "clipboard_notice"),
    "cloud_diag": (".voice.cloud_diag", None),
    "cloud_text_client": (".voice.cloud_text_client", None),
    "config": (".config", "config"),
    "config_module": (".config", None),
    "config_spec": (".config_spec", None),
    "confirm_gate": (".confirm_gate", None),
    "confirm_gate_scrub": (".confirm_gate_scrub", None),
    "confirmation_shape": (".brain.turn_machine", "confirmation_shape"),
    "cost_ledger": (".cost_ledger", None),
    "current_profile_name": (".funnel.tool_profiles", "current_profile_name"),
    "cursor": (".desktop.cursor", None),
    "daily_count": (".daily_count", None),
    "developer_telemetry_doctor": (".developer_telemetry_doctor", None),
    "diagnose_downloads": (".voice.local_assets", "diagnose_downloads"),
    "discover_plugins": (".plugin_loader", "discover_plugins"),
    "downloads": (".voice.local_assets", "downloads"),
    "evaluate_spoken_diagnostic_notice": (".brain.turn_machine", "evaluate_spoken_diagnostic_notice"),
    "explicit_confirmation_prompt": (".brain.turn_machine", "explicit_confirmation_prompt"),
    "failsafe": (".failsafe", None),
    "failure_script": (".funnel.failure_script", None),
    "filter_entries": (".command_palette", "filter_entries"),
    "first_open_prompt": (".firstopen_optin", "first_open_prompt"),
    "first_run_asks": (".first_run_asks", None),
    "firstopen_surface": (".firstopen_surface", None),
    "format_bytes": (".voice.local_assets", "format_bytes"),
    "gate_custom_accent": (".tokens", "gate_custom_accent"),
    "generate_session_summary": (".memory.session_summary", "generate_session_summary"),
    "google_services": (".google_services", None),
    "hotkey_daemon": (".desktop.hotkeys", "hotkey_daemon"),
    "integration_harness_tools": (".integration_harness_tools", None),
    "is_non_success": (".funnel.tool_result", "is_non_success"),
    "kill_chain": (".funnel.kill_chain", None),
    "last_download_error": (".voice.local_assets", "last_download_error"),
    "learning": (".memory.learning", None),
    "lifecycle": (".lifecycle", None),
    "live_client": (".voice.live_client", None),
    "live_session": (".brain.brain", "live_session"),
    "local_assets": (".voice.local_assets", None),
    "loop_action_entries": (".command_palette", "loop_action_entries"),
    "memory_store": (".memory.memory", "memory_store"),
    "migrate_legacy_briefing_records": (".scheduler", "migrate_legacy_briefing_records"),
    "migrate_orb_style": (".app_settings", "migrate_orb_style"),
    "migrate_theme_name": (".tokens", "migrate_theme_name"),
    "model_manager": (".model_manager", None),
    "music_player": (".music_player", None),
    "note_image_frame": (".injection", "note_image_frame"),
    "on_this_day": (".memory.on_this_day", None),
    "perf": (".perf", None),
    "permission_grants": (".permission_grants", None),
    "plan_approval": (".plan_approval", None),
    "planner": (".brain.planner", None),
    "platform_permission_probe": (".platform_permission_probe", None),
    "plugin_loader": (".plugin_loader", None),
    "portability": (".portability", None),
    "power_policy": (".power_policy", None),
    "presence": (".presence", None),
    "privacy": (".memory.privacy", None),
    "qsettings_contains": (".config_spec", "qsettings_contains"),
    "qsettings_get": (".config_spec", "qsettings_get"),
    "qsettings_remove": (".config_spec", "qsettings_remove"),
    "qsettings_set": (".config_spec", "qsettings_set"),
    "qsettings_store": (".config_spec", "qsettings_store"),
    "read_memory": (".memory.read_memory", "read_memory"),
    "record_family": (".record_family", None),
    "remove_offline_pack": (".voice.local_assets", "remove_offline_pack"),
    "resilience_gate": (".resilience.resilience_gate", None),
    "resolve": (".icon_manifest", "resolve"),
    "resolve_provider": (".provider_identity", "resolve_provider"),
    "resume_goal": (".brain.planner_rungoal", "resume_goal"),
    "routine_templates": (".routine_templates", None),
    "run_routine": (".brain.planner", "run_routine"),
    "save_env_setting": (".config", "save_env_setting"),
    "save_session_summary": (".memory.session_summary", "save_session_summary"),
    "scheduled_agents": (".scheduled_agents", None),
    "scheduler": (".scheduler", "scheduler"),
    "screen_watch": (".desktop.screen_watch", None),
    "senses": (".senses", None),
    "senses_log": (".senses_log", None),
    "session_epoch": (".session_epoch", None),
    "service_host": (".service_host", "ServiceHost"),
    "set_frame_sink": (".funnel.tools_handlers", "set_frame_sink"),
    "set_preferred_language": (".languages", "set_preferred_language"),
    "set_session_provenance": (".funnel.tools_gate", "set_session_provenance"),
    "set_status_strip_sink": (".status_strip", "set_status_strip_sink"),
    "settings_search": (".settings_search", None),
    "settings_voice": (".settings_voice", None),
    "sound_pools_v564": (".sound_pools_v564", None),
    "speak_text": (".skills_plus", "speak_text"),
    "state_bus": (".state_bus", None),
    "storage_report": (".voice.local_assets", "storage_report"),
    "suggestions": (".brain.suggestions", None),
    "system_audio_active": (".voice.audio", "system_audio_active"),
    "task_coordinator": (".brain.task_coordinator", None),
    "terms": (".terms", None),
    "theme_sync": (".theme_sync", None),
    "tokens": (".tokens", None),
    "tool_labels": (".tool_labels", None),
    "tool_receipts": (".tool_receipts", None),
    "tools": (".funnel.tools_registry", "tools"),
    "undo_completion": (".undo_completion", None),
    "undo_producers_p1": (".undo_producers_p1", None),
    "updater": (".health.updater", None),
    "validate_icon_name": (".icon_manifest", "validate_icon_name"),
    "validate_manifest": (".icon_manifest", "validate_manifest"),
    "vision_manager": (".desktop.vision", "vision_manager"),
    "voice_live_session": (".voice.live_client", "live_session"),
    "voice_text_bridge": (".voice.voice_text_bridge", None),
    "w10_wiring": (".w10_wiring", None),
    "w8_wiring": (".w8_wiring", None),
    "w9_wiring": (".w9_wiring", None),
    "w9_wiring_lan": (".w9_wiring_lan", None),
    "wake_word_detector": (".voice.wake_word", "wake_word_detector"),
    "watchers": (".watchers", None),
}


__all__ = ['AdvisorySpeakerVerifier', 'AudioManager', 'DATA_DIR', 'DICTATION_BANNER_TEXT', 'DICTATION_ENTER_ANNOUNCEMENT', 'DICTATION_EXIT_ANNOUNCEMENT', 'DictationMode', 'DirectednessGate', 'FLUENT_WINDOWS_ACTIONS', 'INTERRUPTED_STATES', 'IconRef', 'LADDER_LEVELS', 'LANGUAGES', 'LessonPanelState', 'LocalLiveSession', 'ManifestProblem', 'MediaDucking', 'MemoryStore', 'ORB_STYLES', 'PRIMARY_SYSTEM', 'PTT_BUTTON_LABEL', 'PTT_BUTTON_TOOLTIP', 'PushToTalk', 'RoutineScheduler', 'ScreenWatcher', 'TRANSCRIPTION_PROFILES', 'TaskLedger', 'TurnCoordinator', 'VERSION', 'VoiceFailoverGate', 'WatcherEngine', '_KOKORO_VOICE_ROSTER', '_stored_env_file_value', 'action_journal', 'activity_feed', 'always_allow', 'ambient', 'asset_repair', 'audio', 'audio_manager', 'audit', 'boot_sequence', 'browser_enable', 'build_manifest', 'cancel_waking', 'capture_guard', 'catalogue_groups_data', 'changelog', 'chat_thread', 'clipboard_monitor', 'clipboard_notice', 'cloud_diag', 'cloud_text_client', 'config', 'config_module', 'config_spec', 'confirm_gate', 'confirm_gate_scrub', 'confirmation_shape', 'cost_ledger', 'current_profile_name', 'cursor', 'daily_count', 'developer_telemetry_doctor', 'diagnose_downloads', 'discover_plugins', 'downloads', 'evaluate_spoken_diagnostic_notice', 'explicit_confirmation_prompt', 'failsafe', 'failure_script', 'filter_entries', 'first_open_prompt', 'first_run_asks', 'firstopen_surface', 'format_bytes', 'gate_custom_accent', 'generate_session_summary', 'google_services', 'hotkey_daemon', 'integration_harness_tools', 'is_non_success', 'kill_chain', 'last_download_error', 'learning', 'lifecycle', 'live_client', 'live_session', 'local_assets', 'loop_action_entries', 'memory_store', 'migrate_legacy_briefing_records', 'migrate_orb_style', 'migrate_theme_name', 'model_manager', 'music_player', 'note_image_frame', 'on_this_day', 'perf', 'permission_grants', 'plan_approval', 'planner', 'platform_permission_probe', 'plugin_loader', 'portability', 'power_policy', 'presence', 'privacy', 'qsettings_contains', 'qsettings_get', 'qsettings_remove', 'qsettings_set', 'qsettings_store', 'read_memory', 'record_family', 'remove_offline_pack', 'resilience_gate', 'resolve', 'resolve_provider', 'resume_goal', 'routine_templates', 'run_routine', 'save_env_setting', 'save_session_summary', 'scheduled_agents', 'scheduler', 'screen_watch', 'senses', 'senses_log', 'session_epoch', 'service_host', 'set_frame_sink', 'set_preferred_language', 'set_session_provenance', 'set_status_strip_sink', 'settings_search', 'settings_voice', 'sound_pools_v564', 'speak_text', 'state_bus', 'storage_report', 'suggestions', 'system_audio_active', 'task_coordinator', 'terms', 'theme_sync', 'tokens', 'tool_labels', 'tool_receipts', 'tools', 'undo_completion', 'undo_producers_p1', 'updater', 'validate_icon_name', 'validate_manifest', 'vision_manager', 'voice_live_session', 'voice_text_bridge', 'w10_wiring', 'w8_wiring', 'w9_wiring', 'w9_wiring_lan', 'wake_word_detector', 'watchers']


def __getattr__(name):
    """Lazily resolve one exported name from its source module."""
    try:
        rel, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(rel or __package__, __package__)
    # rel "" means the core package itself; import_module
    # with an empty first argument is invalid, so the
    # empty case imports the package by its own name.
    value = module if attr is None else getattr(module, attr)
    # NO cache: every access re-resolves from the source module.
    # Caching (globals()[name] = value) would snapshot the value
    # at first access — a test that patches the source module's
    # attribute afterwards (e.g. voice_assistant.core.config
    # .DATA_DIR in the voice-drop suites) would be invisible to
    # the facade, while a direct import would see the patch.
    # The facade must be transparent, not a snapshot: a fresh
    # lookup per access is exactly what `from <source> import X`
    # does at the same point.
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
