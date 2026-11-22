"""One typed, versioned spec for VYUK's configuration (GAPS-10,
architecture-review flaw #2).

Before this module there was no single place that said what VYUK's
configuration IS: three stores (.env + ``AppConfig`` in
``core/config.py``, ``QSettings("NovaAI", "Nova")`` read from 46
files, and the ``settings.json`` snapshot exported by
``core/portability.py``), 154 scattered ``os.getenv`` /
``os.environ`` reads across 59 product files, 45 of the 76 distinct
env keys with no ``AppConfig`` home at all, and no version field —
migrations were ad-hoc one-off functions (``_migrate_live_voice_
default``, the v5.35 Aura orb migration) with no registry and no
record of what had migrated. The same default lived in two places
(``GROQ_MODEL``: ``config.py`` says ``llama-3.3-70b-versatile``,
``cloud_text_helpers`` read it with ``""`` and fell back to a second
copy of the same string) and nothing enforced that they agree.

This module is the single home. It holds:

* ``CONFIG_VERSION`` — the current version of the configuration
  shape. ``ConfigSpec.version`` records the version the loaded
  config was migrated FROM; ``MIGRATIONS`` maps each old version
  to the step that brings it forward (v0 -> v1 moves the old
  untouched Live-voice default ``Aoede`` to ``Charon`` — the
  ``_migrate_live_voice_default`` rule, now a registered step).
  The stored version lives in ``.env`` as ``VYUK_CONFIG_VERSION``
  (written only when an existing ``.env`` was actually migrated;
  a fresh install with no ``.env`` is definitionally current).
* ``ConfigSpec`` — one dataclass with a typed field per setting.
  Every field's default lives HERE and nowhere else; the env key
  (when the setting has one) is in the field's metadata, so the
  field table and the dataclass cannot drift. Secret fields
  (``api_key``, ``groq_api_key``, ``google_client_secret``,
  ``rustic_password``) are ``repr=False`` — a config repr must
  never leak a credential into a log.
* The read path. ``build_spec()`` builds a frozen spec from a
  mapping (the ``AppConfig`` singleton is built from it at
  import, exactly as before). ``env(key)`` is the live read for
  call-time consumers — it consults ``os.environ`` at call time
  and coerces by the field's declared type, so a function-body
  read behaves exactly as the old ``os.getenv`` call did.
  ``explicit(key)`` returns the raw env value or ``None`` — the
  probe for "did the user explicitly set this?" (the
  ``explicit_model_file`` / ``explicit_whisper_model`` pattern),
  which a defaulted read would destroy. Both refuse unknown keys
  (``KeyError``) — a typo'd key is caught, not silently defaulted.
* ``QSETTINGS_FIELDS`` — the QSettings keys' defaults, in the
  same single home. GAPS-11 migrated every QSettings read/write
  in product code onto the helpers below (``qsettings_get`` /
  ``qsettings_set`` / ``qsettings_contains`` / ``qsettings_remove``,
  construction through the single ``qsettings_store()`` site), so
  the spec and ``core/settings_schema.py`` cannot drift silently —
  the prover checks this table against
  ``settings_schema.FIELD_SPECS`` for the five shared fields.
  The table covers all 54 literal keys the GAPS-11 gap proof
  counted (158 literal-key calls in 34 product files) plus the
  dynamic families (``sounds/cue_<cue>``, ``suggestions/auto_act_
  <class>``, ``watchers/<kind>``, ``firstrun/stage/<stage>``) and
  the keys only ever reached through a variable (``density``,
  ``coach_marks/g_shown``, the first-open prompt keys) — 90
  entries. Bulk/dynamic operations that cannot go through the
  per-key helpers (settings export/import over ``allKeys()``,
  backup restore of an arbitrary ``settings.json``) stay on the
  store object by design — the helpers refuse unknown keys, so
  they cannot express "every key in the file".

What is NOT in this module (deliberately, listed in
``gaps10_remainder.md``): OS facts (``APPDATA``, ``PATH``,
``DISPLAY`` ... — environment facts, not app configuration),
and upstream tools' env vars (``HF_ENDPOINT``, ``HTTP_PROXY``
... — another tool's configuration, which VYUK only steers).
The QSettings reads themselves are IN this module now (GAPS-11).
"""
from __future__ import annotations

import dataclasses
import datetime
import logging
import os
import time
import typing
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Mapping

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
CONFIG_VERSION_KEY = "VYUK_CONFIG_VERSION"


# ---------------------------------------------------------------------------
# Coercion (moved from core/config.py — the defensive contract is the
# spec's, not the store's: an unset or garbled value falls back to
# the field's default, so a typo in .env must never take the
# assistant down at import time).
# ---------------------------------------------------------------------------
def _env_bool_value(raw: str | None, default: bool) -> bool:
    """1/true/yes/on is True, 0/false/no/off is False, anything else
    (unset or a typo) falls back to `default`."""
    text = (raw or "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _env_int_value(raw: str | None, default: int | None) -> int | None:
    """An integer value, or `default` when unset or unparseable."""
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_float_value(raw: str | None, default: float | None) -> float | None:
    """A float value, or `default` when unset or unparseable."""
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _coerce(raw: str | None, typ: Any, default: Any) -> Any:
    """Coerce a raw env string to a field's declared type."""
    if typ is bool:
        return _env_bool_value(raw, default)
    if typ is int:
        return _env_int_value(raw, default)
    if typ is float:
        return _env_float_value(raw, default)
    if typ is str:
        return default if raw is None else raw
    # Optional[int] / Optional[str] fields (probe keys): None stays None.
    args = getattr(typ, "__args__", ())
    if type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if not non_none:
            return default
        if raw is None or not str(raw).strip():
            return None
        return _coerce(raw, non_none[0], default)
    return default if raw is None else raw


def _provenance_lock_value(environ: Mapping[str, str]) -> bool:
    """Session-provenance lock default (v5.48, programme item 24;
    loophole batch 4, L-27: the default is ON): the canonical env
    name is SESSION_PROVENANCE_LOCK; SPEAKER_LOCK is the legacy
    alias from v5.45. Precedence: a recognisably-set
    SESSION_PROVENANCE_LOCK wins; otherwise the legacy SPEAKER_LOCK
    value decides; otherwise ON (the field default — a garbled or
    absent line must never silently drop the lock). A garbled
    new-name line falls back to the legacy setting instead of
    silently dropping the lock."""
    return _env_bool_value(
        environ.get("SESSION_PROVENANCE_LOCK"),
        _env_bool_value(environ.get("SPEAKER_LOCK"), True))


# ---------------------------------------------------------------------------
# The spec. One dataclass, one field per setting. The env key lives in
# the field's metadata, so the table IS the dataclass.
# ---------------------------------------------------------------------------
@dataclass
class ConfigSpec:
    """VYUK's configuration, versioned (``version`` records the
    shape the loaded config was migrated from; a fresh install is
    the current version). Plain construction gives the defaults;
    :func:`build_spec` overlays the environment."""
    version: int = CONFIG_VERSION

    # --- identity / cloud (moved from AppConfig, core/config.py) ---
    # The Gemini API key. SECRET: never logged, never in a repr.
    api_key: str = field(default="", metadata={"env": "GEMINI_API_KEY", "secret": True}, repr=False)
    assistant_name: str = field(default="VYUK", metadata={"env": "ASSISTANT_NAME"})
    # The LIVE (cloud) voice. Charon — the deep, calm, informative
    # male voice of the offered set — is the closest persona match
    # to the on-device George. The previous default, Aoede, was a
    # breezy female voice left over from before the George identity;
    # installs still storing it as their untouched default migrate
    # once (migration v0 -> v1 below).
    voice_name: str = field(default="Charon", metadata={"env": "VOICE_NAME"})
    # MODEL_NAME is the Gemini LIVE (voice) model — it must be a model
    # the Live API offers. The default is the GA successor,
    # gemini-3.8-live; Google retires the 2.5 native-audio PREVIEW
    # ids on 17 Nov 2026 with no redirect.
    model_name: str = field(default="gemini-3.8-live", metadata={"env": "MODEL_NAME"})
    # Tool router (v5.33): the Gemini Live session offers the core
    # tool set plus the use_toolbox dispatcher instead of the whole
    # registry. TOOL_ROUTER=0 restores the fitted full list.
    tool_router: bool = field(default=True, metadata={"env": "TOOL_ROUTER"})
    # Tool profile (v5.45): which tool set a session offers as
    # resident. "assistant" is the resident core set plus the
    # use_toolbox router; "desktop" offers the curated computer-use
    # set; "full" offers the fitted full registry list. Default
    # "desktop" (v5.49, programme item 34).
    tool_profile: str = field(default="desktop", metadata={"env": "TOOL_PROFILE"})
    # Browser attach (GAPS-3, Chrome 136): real-Chrome CDP attach.
    browser_allow_real_chrome_attach: bool = field(default=False, metadata={"env": "BROWSER_ALLOW_REAL_CHROME_ATTACH", "env_alias": "VYUK_ALLOW_REAL_CHROME_ATTACH"})
    browser_debug_port: int = field(default=9222, metadata={"env": "BROWSER_DEBUG_PORT"})
    browser_debug_profile_dir: str = field(default="", metadata={"env": "BROWSER_DEBUG_PROFILE_DIR", "env_alias": "VYUK_BROWSER_DEBUG_PROFILE_DIR"})
    # Tool-call budgets (v5.33+).
    cloud_tool_rounds: int = field(default=8, metadata={"env": "CLOUD_TOOL_ROUNDS"})
    local_tool_rounds: int = field(default=6, metadata={"env": "LOCAL_TOOL_ROUNDS"})
    # Round-3 fix #2 (#55): the LIVE loop's hard round cap — one
    # batch is one round, 8 rounds per model turn (parity with the
    # cloud text loop). Enforced in _run_tool_batch; see
    # core/voice/live_client_receive.py LIVE_MAX_TOOL_ROUNDS.
    live_tool_rounds: int = field(default=8, metadata={"env": "LIVE_TOOL_ROUNDS"})
    tool_deadline_input_s: float = field(default=30.0, metadata={"env": "TOOL_DEADLINE_INPUT_S"})
    tool_deadline_standard_s: float = field(default=120.0, metadata={"env": "TOOL_DEADLINE_STANDARD_S"})
    tool_deadline_long_s: float = field(default=900.0, metadata={"env": "TOOL_DEADLINE_LONG_S"})
    tool_timeout_cooldown_s: float = field(default=30.0, metadata={"env": "TOOL_TIMEOUT_COOLDOWN_S"})
    tool_call_budget_per_minute: int = field(default=120, metadata={"env": "TOOL_CALL_BUDGET_PER_MINUTE"})
    # Local-model sampling.
    local_quick_temperature: float = field(default=0.2, metadata={"env": "LOCAL_QUICK_TEMPERATURE"})
    local_quick_max_tokens: int = field(default=256, metadata={"env": "LOCAL_QUICK_MAX_TOKENS"})
    local_think_temperature: float = field(default=0.55, metadata={"env": "LOCAL_THINK_TEMPERATURE"})
    local_think_max_tokens: int = field(default=1024, metadata={"env": "LOCAL_THINK_MAX_TOKENS"})
    # Brain engine (default "local" — a fresh install does NOT bind
    # a Gemini Live session; the cloud brain is opt-in).
    brain_engine: str = field(default="local", metadata={"env": "BRAIN_ENGINE"})
    # Per-query model routing (GAPS-6, BLOCKS #5).
    query_routing: bool = field(default=True, metadata={"env": "QUERY_ROUTING"})
    cloud_provider: str = field(default="gemini", metadata={"env": "CLOUD_PROVIDER"})
    # Groq (second cloud provider). SECRET key: never logged.
    groq_api_key: str = field(default="", metadata={"env": "GROQ_API_KEY", "secret": True}, repr=False)
    groq_model: str = field(default="llama-3.3-70b-versatile", metadata={"env": "GROQ_MODEL"})
    groq_temperature: float = field(default=0.6, metadata={"env": "GROQ_TEMPERATURE"})
    ollama_model: str = field(default="qwen2.5:7b", metadata={"env": "OLLAMA_MODEL"})
    ollama_host: str = field(default="http://127.0.0.1:11434", metadata={"env": "OLLAMA_HOST"})
    whisper_model: str = field(default="small", metadata={"env": "WHISPER_MODEL"})
    whisper_tier: str = field(default="auto", metadata={"env": "WHISPER_TIER"})
    local_tts_voice: str = field(default="en_US-amy-medium.onnx", metadata={"env": "LOCAL_TTS_VOICE"})
    local_backend: str = field(default="builtin", metadata={"env": "LOCAL_BACKEND"})
    local_model_file: str = field(default="Qwen3-4B-Instruct-2507-Q4_K_M.gguf", metadata={"env": "LOCAL_MODEL_FILE"})
    kokoro_voice: str = field(default="bm_george", metadata={"env": "KOKORO_VOICE"})
    preferred_language: str = field(default="en-gb", metadata={"env": "PREFERRED_LANGUAGE"})
    # Round-3 wizard expansion: what she calls the user — asked
    # once in the first-run wizard (the name page), changeable in
    # Settings. Empty means she never learned it: the Home
    # greeting and the briefing header read the plain form.
    # The name is the DECLARED identity (config); the memory
    # web's learned name keys (core/memory PINNED_KEYS) are a
    # separate, learned record and are not consulted here.
    user_name: str = field(default="", metadata={"env": "USER_NAME"})
    transcription_profile: str = field(default="auto", metadata={"env": "TRANSCRIPTION_PROFILE"})
    voice_auto_switch: bool = field(default=True, metadata={"env": "VOICE_AUTO_SWITCH"})
    kokoro_speed: float = field(default=0.95, metadata={"env": "KOKORO_SPEED"})
    kokoro_blend_voice: str = field(default="", metadata={"env": "KOKORO_BLEND_VOICE"})
    kokoro_blend_ratio: float = field(default=0.0, metadata={"env": "KOKORO_BLEND_RATIO"})
    tts_loudness_enabled: bool = field(default=True, metadata={"env": "TTS_LOUDNESS_ENABLED"})
    tts_loudness_target_dbfs: float = field(default=-18.0, metadata={"env": "TTS_LOUDNESS_TARGET_DBFS"})
    ack_enabled: bool = field(default=True, metadata={"env": "ACK_ENABLED"})
    mic_gain: float = field(default=1.0, metadata={"env": "MIC_GAIN"})
    vad_rms_threshold: int = field(default=400, metadata={"env": "VAD_RMS_THRESHOLD"})
    barge_in_factor: float = field(default=1.6, metadata={"env": "BARGE_IN_FACTOR"})
    barge_in_guard_ms: int = field(default=250, metadata={"env": "BARGE_IN_GUARD_MS"})
    barge_in_sustain_ms: int = field(default=150, metadata={"env": "BARGE_IN_SUSTAIN_MS"})
    listen_timeout_s: float = field(default=0.0, metadata={"env": "LISTEN_TIMEOUT_S"})
    wake_sensitivity: float = field(default=1.0, metadata={"env": "WAKE_SENSITIVITY"})
    wake_phrase_alt: str = field(default="", metadata={"env": "WAKE_PHRASE_ALT"})
    wake_partial_fire: bool = field(default=False, metadata={"env": "WAKE_PARTIAL_FIRE"})
    # Session-provenance lock (v5.48; loophole batch 4): default
    # ON — a voice session starts unproven, and a spoken yes/no
    # cannot resolve a pending confirmation until the owner proves
    # the session (on screen, or with the voice PIN). Env
    # SESSION_PROVENANCE_LOCK, legacy alias SPEAKER_LOCK (see
    # _provenance_lock_value above).
    session_provenance_lock: bool = field(default=True, metadata={"env": "SESSION_PROVENANCE_LOCK", "env_alias": "SPEAKER_LOCK"})
    # Private mode (loophole batch 5, #5): when ON, a turn writes
    # NOTHING anywhere — no transcript, no receipt, no audit
    # record, no Activity line, no cost-ledger row, no learning.
    # The mode is a per-turn store gate, not a memory flag: the
    # stores consult it at write time (core/private_mode.py).
    # Default OFF (the owner turns it on for a private turn).
    # Env PRIVATE_MODE.
    private_mode: bool = field(default=False, metadata={"env": "PRIVATE_MODE"})
    # Feedback & crash reporting (loophole batch 7, #6): when ON,
    # the app may record crash signatures (type + app code
    # location only, core/feedback_report.py) and the user may
    # file answer feedback (label + turn hash only). When OFF —
    # the default — the crash hook records nothing at all and no
    # feedback report is built. The opt-in is SEPARATE from the
    # first-run sign-in: no first-run or sign-in module sets or
    # reads it (pinned by a suite scan). Env
    # FEEDBACK_REPORTING_ENABLED.
    feedback_reporting_enabled: bool = field(default=False, metadata={"env": "FEEDBACK_REPORTING_ENABLED"})
    pronunciations_json: str = field(default="", metadata={"env": "PRONUNCIATIONS_JSON"})
    whisper_hours_enabled: bool = field(default=False, metadata={"env": "WHISPER_HOURS_ENABLED"})
    whisper_start: str = field(default="22:00", metadata={"env": "WHISPER_START"})
    whisper_end: str = field(default="07:00", metadata={"env": "WHISPER_END"})
    whisper_factor: float = field(default=0.5, metadata={"env": "WHISPER_FACTOR"})
    briefing_speed: float = field(default=1.0, metadata={"env": "BRIEFING_SPEED"})
    update_feed_url: str = field(default="", metadata={"env": "UPDATE_FEED_URL"})
    google_client_id: str = field(default="", metadata={"env": "GOOGLE_CLIENT_ID"})
    # Google OAuth client secret. SECRET: never logged, never in a repr.
    google_client_secret: str = field(default="", metadata={"env": "GOOGLE_CLIENT_SECRET", "secret": True}, repr=False)
    # Gemini sign-in ("Continue with Google", round-3 OAuth build):
    # VYUK's OWN Desktop-app OAuth client (project vyuk-515320) —
    # the release packaging injects these; the repo must NEVER
    # contain the secret. The id is public; the secret is SECRET.
    gemini_oauth_client_id: str = field(default="", metadata={"env": "GEMINI_OAUTH_CLIENT_ID"})
    gemini_oauth_client_secret: str = field(default="", metadata={"env": "GEMINI_OAUTH_CLIENT_SECRET", "secret": True}, repr=False)
    sample_rate_in: int = field(default=16000, metadata={"env": "SAMPLE_RATE_IN"})
    sample_rate_out: int = field(default=24000, metadata={"env": "SAMPLE_RATE_OUT"})
    # No env key: plain constants of the voice path.
    channels: int = 1
    chunk_size: int = 1024
    # Available Gemini Live voices.
    available_voices: list = field(default_factory=lambda: [
        "Aoede", "Charon", "Fenrir", "Kore", "Puck",
    ])
    # The system instruction (the butler persona).
    system_instruction: str = field(default_factory=lambda: (
        "You are VYUK (Virtual Yet Unusually Knowledgeable) — a highly capable desktop assistant in the manner of a first-class British butler: unflappable, precise, and quietly witty. "
        "You can hear the user's voice in real-time, speak back instantly, see the screen or webcam when asked, and control the computer through functions. "
        "Keep your spoken answers brief and polished, and let any humour be dry and occasional — a raised eyebrow, never a clown act. "
        "Be candid when something cannot be done; never pretend otherwise. "
        "Anticipate what the user needs, handle details without fuss, and address the user by name, with an occasional 'sir' only where it falls naturally."
    ))

    # --- keys that had no AppConfig home (GAPS-10): settings the tree
    # read straight from the environment, now spec fields with the
    # same defaults the call sites used. Where a call site read with
    # "" as an absence sentinel and the real default lived
    # downstream, the field default is "" and the downstream default
    # is named here — the spec does not move a default it does not
    # own. Where only an exact spelling matters (a "0" disable, a
    # "1" enable), the field stays a str so the spelling is exact.
    # --- directories / paths (override or "" = the built-in location)
    cost_ledger_path: str = field(default="", metadata={"env": "VYUK_COST_LEDGER_PATH"})
    trash_dir: str = field(default="", metadata={"env": "VYUK_TRASH_DIR"})
    kokoro_python: str = field(default="", metadata={"env": "VYUK_KOKORO_PYTHON"})
    vision_models_dir: str = field(default="", metadata={"env": "VYUK_VISION_MODELS_DIR"})
    nova_app_root: str = field(default="", metadata={"env": "NOVA_APP_ROOT"})
    learning_feed_dir: str = field(default="", metadata={"env": "VYUK_LEARNING_FEED_DIR"})
    mcp_config_path: str = field(default="", metadata={"env": "VYUK_MCP_CONFIG_PATH"})
    mcp_hashes_path: str = field(default="", metadata={"env": "VYUK_MCP_HASHES_PATH"})
    knowledge_dir: str = field(default="", metadata={"env": "VYUK_KNOWLEDGE_DIR"})
    memories_dir: str = field(default="", metadata={"env": "VYUK_MEMORIES_DIR"})
    nova_assets_dir: str = field(default="", metadata={"env": "NOVA_ASSETS_DIR"})
    secret_key_dir: str = field(default="", metadata={"env": "VYUK_SECRET_KEY_DIR"})
    silero_vad_onnx: str = field(default="", metadata={"env": "VYUK_SILERO_VAD_ONNX"})
    smart_turn_onnx: str = field(default="", metadata={"env": "VYUK_SMART_TURN_ONNX"})
    # Noise-suppression asset overrides (noise_suppress._find_asset):
    # a path, or "" = the bundled asset in the tree.
    vyuk_rnnoise_lib: str = field(default="", metadata={"env": "VYUK_RNNOISE_LIB"})
    vyuk_rnnoise_weights: str = field(default="", metadata={"env": "VYUK_RNNOISE_WEIGHTS"})
    vyuk_gtcrn_onnx: str = field(default="", metadata={"env": "VYUK_GTCRN_ONNX"})
    # --- model / provider knobs
    # Sentinel: the real default lives downstream in
    # DEFAULT_GROQ_VISION_MODEL (cloud_text_helpers).
    groq_vision_model: str = field(default="", metadata={"env": "GROQ_VISION_MODEL"})
    groq_vision: bool = field(default=True, metadata={"env": "GROQ_VISION"})
    groq_tool_router: bool = field(default=True, metadata={"env": "GROQ_TOOL_ROUTER"})
    local_vlm: bool = field(default=False, metadata={"env": "LOCAL_VLM"})
    local_vlm_describe: str = field(default="auto", metadata={"env": "LOCAL_VLM_DESCRIBE"})
    # Probe key: None when unset means "no explicit tuning — use the
    # RAM rules" (explicit_n_ctx). env() returns the int when set.
    llm_n_ctx: int | None = field(default=None, metadata={"env": "LLM_N_CTX"})
    session_summary_every_turns: int = field(default=12, metadata={"env": "SESSION_SUMMARY_EVERY_TURNS"})
    # --- flags where only an exact spelling matters (str, not bool)
    # NOVA_YTDLP_STREAM: only "1" enables (any other spelling,
    # including "true", does not). VYUK_GLOBE_PROXY: only "0"
    # disables the proxy (any other spelling, including "false",
    # does not). These stay str so the spelling is exact.
    nova_ytdlp_stream: str = field(default="", metadata={"env": "NOVA_YTDLP_STREAM"})
    globe_proxy: str = field(default="1", metadata={"env": "VYUK_GLOBE_PROXY"})
    # VYUK_CAMOUFOX_LANE: only "1" enables the Camoufox lane (any
    # other spelling, including "true", does not).
    vyuk_camoufox_lane: str = field(default="", metadata={"env": "VYUK_CAMOUFOX_LANE"})
    # --- enable flags (bool; "" / unset = the default below)
    vyuk_platform: str = field(default="", metadata={"env": "VYUK_PLATFORM"})
    grammar_shortlist: bool = field(default=False, metadata={"env": "VYUK_GRAMMAR_SHORTLIST"})
    nova_start_minimized: bool = field(default=False, metadata={"env": "NOVA_START_MINIMIZED"})
    # The injection second pass is ON unless the var carries a falsy
    # spelling (0/false/off/no) — the _env_second_pass_disabled rule.
    injection_second_pass: bool = field(default=True, metadata={"env": "VYUK_INJECTION_SECOND_PASS"})
    # --- secrets
    # Restic backup password. SECRET: never logged, never in a repr.
    rustic_password: str = field(default="", metadata={"env": "RUSTIC_PASSWORD", "secret": True}, repr=False)
    # Browser-history opt-in (browser_history): the config name the
    # reader expected, ``browser_allow_history_read``, never existed
    # as a field — the env var was always the live control. The
    # field now exists (built from the same var at import); the
    # reader takes the live spec read, so a post-import set still
    # takes effect.
    browser_allow_history_read: bool = field(default=False, metadata={"env": "VYUK_ALLOW_BROWSER_HISTORY"})
    # Cost caps (cost_ledger._env_cap): a USD cap, or None = no cap.
    # An unparseable value is no cap, the _env_cap rule — env()
    # returns the float, the > 0 check stays at the call site.
    vyuk_cost_daily_cap_usd: float | None = field(default=None, metadata={"env": "VYUK_COST_DAILY_CAP_USD"})
    vyuk_cost_monthly_cap_usd: float | None = field(default=None, metadata={"env": "VYUK_COST_MONTHLY_CAP_USD"})
    vyuk_cost_per_turn_cap_usd: float | None = field(default=None, metadata={"env": "VYUK_COST_PER_TURN_CAP_USD"})


# ---------------------------------------------------------------------------
# The field tables, derived from the dataclass — the table IS the
# dataclass, so the two cannot drift.
# ---------------------------------------------------------------------------
def _field_table() -> dict[str, Any]:
    """field name -> dataclass field (only fields with an env key)."""
    return {f.name: f for f in fields(ConfigSpec) if "env" in f.metadata}


ENV_KEY_FOR_FIELD: dict[str, str] = {
    f.name: f.metadata["env"] for f in fields(ConfigSpec) if "env" in f.metadata
}
FIELD_FOR_ENV_KEY: dict[str, str] = {v: k for k, v in ENV_KEY_FOR_FIELD.items()}
SECRET_FIELDS: frozenset = frozenset(
    f.name for f in fields(ConfigSpec) if f.metadata.get("secret")
)
# Legacy alias names, derived from the dataclass metadata
# ("env_alias" on the field they name). An alias is not a field of
# its own, and env() does not take one (it raises, naming the
# canonical key): an alias is only ever read raw, through
# explicit(), by the legacy fallback legs that predate the spec.
# Whether the frozen field folds the alias is per-field:
# session_provenance_lock does (_provenance_lock_value); the
# browser aliases do not. GAPS-11 REMOVED the two browser alias
# legs (core/desktop/browser_cdp.py: _real_chrome_attach_allowed
# and _debug_profile_dir read the legacy VYUK_ALLOW_REAL_CHROME_
# ATTACH / VYUK_BROWSER_DEBUG_PROFILE_DIR vars when the config
# field was stubbed to None) — they were dead in production (the
# config field is always non-None: False / ""), reviving them
# would let a legacy env var enable real-Chrome attach despite
# the field being False, and the canonical env vars
# (BROWSER_ALLOW_REAL_CHROME_ATTACH / BROWSER_DEBUG_PROFILE_DIR)
# already control the fields. The alias names stay in this table
# so explicit() still answers the raw probe and the legacy names
# are documented, but no production code reads them anymore.
ENV_ALIAS: dict[str, str] = {
    f.metadata["env_alias"]: f.metadata["env"]
    for f in fields(ConfigSpec)
    if "env_alias" in f.metadata
}


# ---------------------------------------------------------------------------
# Diagnostics (GAPS-21, C-V8). Before this, an unparseable env value
# fell back to the field default with no record anywhere — a typo'd
# SAMPLE_RATE_IN=abc or a garbled SESSION_PROVENANCE_LOCK was
# invisible, and the next launch silently ran the default. These
# helpers record every such fallback, inspectably:
# build_spec() records the diagnostics of the build (the import-time
# build is the diagnostic moment for the shipped .env), and env()
# records any it meets on a live read (deduped by key + raw value).
# Nothing is refused — a diagnostic is not a gate. Secrets can
# never appear here: every secret field is a str, str values never
# fail coercion, and _record_spec_diagnostic redacts a secret
# field's raw value anyway.
# ---------------------------------------------------------------------------
_SPEC_DIAGNOSTICS: list[dict[str, str]] = []


def _record_spec_diagnostic(env_key: str, field_name: str, raw: str,
                            reason: str) -> None:
    """Append one unparseable-value diagnostic (deduped)."""
    if field_name in SECRET_FIELDS:
        raw = "<redacted>"
    entry = {"kind": "unparseable-value", "env_key": env_key,
             "field": field_name, "raw": str(raw), "reason": reason}
    if entry not in _SPEC_DIAGNOSTICS:
        _SPEC_DIAGNOSTICS.append(entry)


def _coercion_failure(raw: str | None, typ: Any) -> str | None:
    """Why `raw` cannot be coerced to `typ`, or None when it can.
    Unset/empty is not a failure — it means 'use the default'."""
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    if typ is bool:
        if text.lower() in ("1", "true", "yes", "on",
                            "0", "false", "no", "off"):
            return None
        return f"{raw!r} is not a recognised true/false spelling"
    if typ is int:
        try:
            int(text)
            return None
        except ValueError:
            return f"{raw!r} is not an integer"
    if typ is float:
        try:
            float(text)
            return None
        except ValueError:
            return f"{raw!r} is not a number"
    if typ is str:
        return None
    args = getattr(typ, "__args__", ())
    if type(None) in args:
        non_none = [a for a in args if a is not type(None)]
        if not non_none:
            return None
        return _coercion_failure(raw, non_none[0])
    return None


def _diagnose_provenance(source: Mapping[str, str]) -> None:
    """The provenance lock's own diagnostic: a garbled
    SESSION_PROVENANCE_LOCK falls back to the legacy SPEAKER_LOCK
    by design (_provenance_lock_value) — but the garble itself is
    recorded, naming the fallback it took."""
    raw = source.get("SESSION_PROVENANCE_LOCK")
    failure = _coercion_failure(raw, bool)
    if failure is not None:
        _record_spec_diagnostic(
            "SESSION_PROVENANCE_LOCK", "session_provenance_lock", raw,
            f"{failure}; fell back to SPEAKER_LOCK")


def spec_diagnostics() -> list[dict[str, str]]:
    """The unparseable-value diagnostics recorded by the last
    build_spec() (plus any since recorded by env() live reads) —
    a list of {kind, env_key, field, raw, reason} dicts. Empty
    means every set value parsed."""
    return [dict(d) for d in _SPEC_DIAGNOSTICS]


def field_default(name: str) -> Any:
    """A field's default — the single home for every default."""
    for f in fields(ConfigSpec):
        if f.name == name:
            if f.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
                return f.default_factory()
            return f.default
    raise KeyError(f"unknown config field: {name!r}")


def _field_type(name: str) -> Any:
    """A field's declared type, resolved (the module uses
    ``from __future__ import annotations``, so the raw
    ``field.type`` is a string — ``get_type_hints`` resolves it)."""
    hints = typing.get_type_hints(ConfigSpec)
    return hints[name]


def env(key: str, environ: Mapping[str, str] | None = None) -> Any:
    """The live read for a spec key: consults `environ` (default
    os.environ) at call time and coerces by the field's declared
    type, falling back to the field's default when unset or
    garbled. Unknown keys raise KeyError — a typo'd key is caught,
    not silently defaulted. This is the replacement for a
    call-time ``os.getenv`` / ``os.environ.get`` in product code.
    An alias name raises KeyError naming the canonical key — env()
    takes spec keys only; explicit() is the raw probe for an
    alias."""
    if key in ENV_ALIAS:
        raise KeyError(
            f"{key!r} is a legacy alias for {ENV_ALIAS[key]!r} — "
            "read the canonical key (env() takes spec keys only; "
            "explicit() is the raw probe for an alias)")
    if key not in FIELD_FOR_ENV_KEY:
        raise KeyError(f"unknown config key: {key!r} (not in the spec)")
    name = FIELD_FOR_ENV_KEY[key]
    raw = (os.environ if environ is None else environ).get(key)
    if name == "session_provenance_lock":
        source = os.environ if environ is None else environ
        _diagnose_provenance(source)
        return _provenance_lock_value(source)
    failure = _coercion_failure(raw, _field_type(name))
    if failure is not None:
        _record_spec_diagnostic(key, name, raw, failure)
    return _coerce(raw, _field_type(name), field_default(name))


def explicit(key: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The raw env value for a spec key, or None when unset — the
    probe for "did the user explicitly set this?" (the
    ``explicit_model_file`` / ``explicit_whisper_model`` pattern).
    A defaulted read would destroy the distinction, so this one
    never applies a default. Unknown keys raise KeyError. A legacy
    alias name is also accepted — it returns the alias's raw value
    (or None), never the field's resolved value."""
    if key in ENV_ALIAS:
        source = os.environ if environ is None else environ
        value = source.get(key)
        return None if value is None else str(value)
    if key not in FIELD_FOR_ENV_KEY:
        raise KeyError(f"unknown config key: {key!r} (not in the spec)")
    source = os.environ if environ is None else environ
    value = source.get(key)
    return None if value is None else str(value)


def build_spec(environ: Mapping[str, str] | None = None) -> ConfigSpec:
    """Build a frozen spec from a mapping (default os.environ):
    every env-backed field takes the mapping's value (coerced by
    its declared type) when set, else the field's default. This is
    what the ``AppConfig`` singleton is built from at import — the
    same timing and the same values as the old
    ``default_factory`` lambdas, with the defaults now in one
    place. The `version` field takes the stored version key when
    present, else the current version (a fresh install is current).
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    _SPEC_DIAGNOSTICS.clear()
    values: dict[str, Any] = {}
    for name, spec_field in _field_table().items():
        key = spec_field.metadata["env"]
        raw = source.get(key)
        if name == "session_provenance_lock":
            _diagnose_provenance(source)
            values[name] = _provenance_lock_value(source)
        else:
            failure = _coercion_failure(raw, _field_type(name))
            if failure is not None:
                _record_spec_diagnostic(key, name, raw, failure)
            values[name] = _coerce(raw, _field_type(name),
                                   field_default(name))
    version_raw = source.get(CONFIG_VERSION_KEY)
    try:
        version = int(str(version_raw).strip()) if version_raw is not None else CONFIG_VERSION
    except (TypeError, ValueError):
        version = CONFIG_VERSION
        if version_raw is not None and str(version_raw).strip():
            _record_spec_diagnostic(CONFIG_VERSION_KEY, "version",
                                    str(version_raw),
                                    f"{version_raw!r} is not an integer")
    values["version"] = version
    return ConfigSpec(**values)


def spec_as_dict(spec: ConfigSpec, *, include_secrets: bool = False) -> dict[str, Any]:
    """A spec as a plain dict (field name -> value). Secrets are
    redacted unless `include_secrets` is set — a dump must never
    leak a credential by default."""
    out = {}
    for f in fields(spec):
        if f.name in SECRET_FIELDS and not include_secrets:
            out[f.name] = "<redacted>"
        else:
            out[f.name] = getattr(spec, f.name)
    return out


# ---------------------------------------------------------------------------
# Migrations. A migration step takes the stored .env dict (plus the
# set of keys that were in the real process environment before the
# .env file was read — a real env override is never migrated) and
# returns the migrated dict. Steps are pure: the runner in
# core/config.py owns the file write.
# ---------------------------------------------------------------------------
def _migrate_v0_to_v1(stored: dict[str, str], environ_keys: set[str]) -> dict[str, str]:
    """v0 -> v1 (v5.52, item 2): the old untouched Live-voice
    default ``Aoede`` moves to ``Charon``. A stored old default is
    the auto-written kind, not a pick: any other stored voice is a
    deliberate choice and is respected, and a real VOICE_NAME env
    override wins over the file entirely (no migration runs at
    all). This is the old ``_migrate_live_voice_default`` rule,
    now a registered step."""
    if "VOICE_NAME" in environ_keys:
        return stored
    if stored.get("VOICE_NAME") == "Aoede":
        stored = dict(stored)
        stored["VOICE_NAME"] = "Charon"
    return stored


MIGRATIONS: dict[int, Callable[[dict[str, str], set[str]], dict[str, str]]] = {
    0: _migrate_v0_to_v1,
}


def migrate_stored(
    stored: dict[str, str], from_version: int, environ_keys: set[str]
) -> tuple[dict[str, str], int]:
    """Apply every migration step from `from_version` up to
    CONFIG_VERSION, in order. Returns (migrated dict, new version).
    Raises KeyError when a version has no registered step — a spec
    bug, never a silent skip; the runner catches it and leaves the
    file as it was."""
    current = dict(stored)
    version = from_version
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise KeyError(f"no migration step registered for config version {version}")
        current = step(current, environ_keys)
        version += 1
    return current, version


# ---------------------------------------------------------------------------
# QSettings defaults, in the same single home. GAPS-11 migrated every
# QSettings read/write in product code onto the helpers below, so
# this table IS the default for every key — the prover checks it
# against settings_schema.FIELD_SPECS for the five shared fields.
# (name, QSettings key, type, default)
#
# Defaults were taken from the call sites (the GAPS-11 gap proof
# re-verified all 158 literal-key calls in 34 product files and
# the dynamic families). Where a call site read with "" as an
# absence sentinel and the real default lived downstream, the
# table carries the EFFECTIVE default and the downstream rule is
# named here (motion/level: "" -> "full" via _motion_level).
# Where only an exact spelling matters the type stays str.
# object = returned as stored (QPoint / QByteArray geometry).
# list = returned as stored; the caller normalises legacy forms
# (learning/seen_ids wraps a legacy str/int into a set).
# ---------------------------------------------------------------------------
QSETTINGS_FIELDS: tuple[tuple[str, str, type, Any], ...] = (
    # The five shared fields (settings_schema.FIELD_SPECS) — the
    # prover checks key + default agree on both sides.
    ("theme", "theme", str, "Cyan"),
    ("orb_style", "orb_style", str, "Aura"),
    ("sounds_enabled", "sounds_enabled", bool, True),
    ("suggestions_enabled", "suggestions/enabled", bool, True),
    ("density", "density", str, "comfortable"),
    # Appearance (gui/window_settings_appearance.py, gui/style_tokens).
    ("orb_style_chosen", "orb_style_chosen", bool, False),
    ("theme_pack_name", "theme_pack/name", str, ""),
    ("theme_pack_tokens", "theme_pack/tokens", str, ""),
    ("appearance_custom_hex", "appearance/custom_hex", str, "#35D6FF"),
    ("appearance_font_scale", "appearance/font_scale", float, 1.0),
    ("appearance_density", "appearance/density", str, "comfortable"),
    ("appearance_glass", "appearance/glass", bool, False),
    ("appearance_particles", "appearance/particles", bool, True),
    ("appearance_follow_windows", "appearance/follow_windows", bool, False),
    # Motion: the read default was "" but _motion_level maps unset
    # to "full" (or "off" when legacy motion/reduced is True), so
    # the effective default is "full". motion/reduced is the legacy
    # key; a direct write to it wins over the migrated level.
    ("motion_level", "motion/level", str, "full"),
    ("motion_reduced", "motion/reduced", bool, False),
    # Onboarding / first run.
    ("onboarded", "onboarded", bool, False),
    ("setup_brain_choice", "setup/brain_choice", str, None),
    ("setup_local_pack_wanted", "setup/local_pack_wanted", bool, False),
    ("setup_choice_prompt_seen_v525", "setup/choice_prompt_seen_v525", bool, False),
    ("advanced_open", "advanced/open", bool, False),
    ("firstrun_stage_interaction", "firstrun/stage/interaction", str, "open"),
    ("firstrun_stage_brain", "firstrun/stage/brain", str, "open"),
    ("firstrun_stage_permissions", "firstrun/stage/permissions", str, "open"),
    ("firstrun_stage_first_mission", "firstrun/stage/first_mission", str, "open"),
    # Round-3 wizard expansion: the self-test fix-it pin — the
    # failing check names, ";"-joined, cleared on an all-pass run.
    ("firstrun_selftest_faults", "firstrun/selftest_faults", str, ""),
    # Loophole batch 7 (#8 + #18): the setup card's per-piece
    # state — the explicitly declined pieces (";"-joined ids)
    # and the snooze deadline (ISO timestamp, "" = not snoozed).
    # The card shows only while an open piece exists and the
    # snooze has expired (core/setup_pieces.py).
    ("firstrun_pieces_declined", "firstrun/pieces_declined", str, ""),
    ("firstrun_setup_card_snoozed_until", "firstrun/setup_card_snoozed_until", str, ""),
    ("coach_marks_g_shown", "coach_marks/g_shown", bool, False),
    # Audio / voice.
    ("audio_input_mode", "audio/input_mode", str, "auto"),
    ("audio_input_device", "audio/input_device", int, None),
    ("audio_input_device_name", "audio/input_device_name", str, None),
    ("audio_output_device", "audio/output_device", int, None),
    ("audio_output_device_name", "audio/output_device_name", str, None),
    ("ears_ptt_mode", "ears/ptt_mode", str, "hold"),
    ("ears_pause_while_media", "ears/pause_while_media", bool, False),
    ("voice_briefing_preset", "voice/briefing_preset", bool, True),
    ("voice_briefing_speed", "voice/briefing_speed", float, 1.0),
    ("sounds_family", "sounds/family", str, "classic"),
    ("sounds_volume", "sounds_volume", float, 0.5),
    # Per-cue earcon toggles (gui/sounds.py CUE_NAMES, 15 cues).
    ("sounds_cue_wake", "sounds/cue_wake", bool, True),
    ("sounds_cue_ack", "sounds/cue_ack", bool, True),
    ("sounds_cue_sent", "sounds/cue_sent", bool, True),
    ("sounds_cue_done", "sounds/cue_done", bool, True),
    ("sounds_cue_error", "sounds/cue_error", bool, True),
    ("sounds_cue_connect", "sounds/cue_connect", bool, True),
    ("sounds_cue_disconnect", "sounds/cue_disconnect", bool, True),
    ("sounds_cue_ready", "sounds/cue_ready", bool, True),
    ("sounds_cue_capture", "sounds/cue_capture", bool, True),
    ("sounds_cue_alert", "sounds/cue_alert", bool, True),
    ("sounds_cue_lock", "sounds/cue_lock", bool, True),
    ("sounds_cue_start", "sounds/cue_start", bool, True),
    ("sounds_cue_step", "sounds/cue_step", bool, True),
    ("sounds_cue_handback", "sounds/cue_handback", bool, True),
    ("sounds_cue_toast", "sounds/cue_toast", bool, True),
    # Learning / suggestions.
    ("learning_enabled", "learning/enabled", bool, True),
    ("learning_seen_ids", "learning/seen_ids", list, []),
    ("learning_announced_once", "learning/announced_once", bool, False),
    ("suggestions_auto_act", "suggestions/auto_act", bool, False),
    ("suggestions_auto_act_meeting_open", "suggestions/auto_act_meeting_open", bool, False),
    ("suggestions_auto_act_battery_saver", "suggestions/auto_act_battery_saver", bool, False),
    ("suggestions_auto_act_download_open", "suggestions/auto_act_download_open", bool, False),
    # Presence.
    ("presence_connect_on_launch", "presence/connect_on_launch", bool, False),
    ("presence_auto_reconnect", "presence/auto_reconnect", bool, True),
    ("presence_mini_orb", "presence/mini_orb", bool, True),
    # UI rethink Build 3 (2026-11-16, §6.11): the status strip is the
    # docked bottom row, ON by default — the checkbox / tray item /
    # funnel tool hide it. (Before Build 3 the floating bar was
    # opt-in and off by default.)
    ("presence_status_strip", "presence/status_strip", bool, True),
    ("presence_summon_hotkey", "presence/summon_hotkey", bool, True),
    ("presence_briefing_enabled", "presence/briefing_enabled", bool, False),
    ("presence_briefing_time", "presence/briefing_time", str, "07:30"),
    ("presence_briefing_last_date", "presence/briefing_last_date", str, ""),
    # Watchers (gui/window_memory_presence.py kinds + the
    # _WATCHER_KIND_TO_SETTING suffixes; each defaults True except
    # ambient_light, which is opt-in).
    ("watchers_enabled", "watchers/enabled", bool, True),
    ("watchers_ambient_light", "watchers/ambient_light", bool, False),
    ("watchers_foreground_app", "watchers/foreground_app", bool, True),
    ("watchers_calendar", "watchers/calendar", bool, True),
    ("watchers_battery", "watchers/battery", bool, True),
    ("watchers_downloads", "watchers/downloads", bool, True),
    ("watchers_network", "watchers/network", bool, True),
    ("watchers_welcome_back", "watchers/welcome_back", bool, True),
    ("watchers_disk", "watchers/disk", bool, True),
    ("watchers_load", "watchers/load", bool, True),
    ("watchers_clipboard", "watchers/clipboard", bool, True),
    ("watchers_privacy", "watchers/privacy", bool, True),
    ("watchers_fullscreen", "watchers/fullscreen", bool, True),
    # Window geometry (QPoint / QByteArray — object, returned as stored).
    ("status_strip_pos", "status_strip/pos", object, None),
    ("mini_orb_pos", "mini_orb/pos", object, None),
    ("geometry", "geometry", object, None),
    # Memory / skills views.
    ("memory_view", "memory/view", str, "list"),
    ("skills_view", "skills/view", str, "list"),
    ("memory_dimension", "memory/dimension", str, "2d"),
    ("skills_dimension", "skills/dimension", str, "2d"),
    # First-open continuous-display prompt (core/firstopen_surface).
    ("privacy_firstopen_prompt_seen", "privacy/firstopen_prompt_seen", bool, False),
    ("privacy_continuous_display_choice", "privacy/continuous_display_choice", str, ""),
    # Diagnostics.
    ("doctor_last_run", "doctor/last_run", str, ""),
    ("changelog_last_seen_version", "changelog/last_seen_version", str, None),
    # Telemetry (GAPS-27, M-V8): the enable path for the dev-only
    # local-file telemetry (core/telemetry). OFF by default behind
    # explicit opt-in — the setting only ever turns it ON when the
    # user flips it; nothing reads this key except
    # telemetry.apply_settings().
    ("telemetry_enabled", "telemetry/enabled", bool, False),
    # Chat transcript (chat UI build, 2026-11-16): the composer's
    # draft and the last-seen store id. The draft is the half-typed
    # message that survives a page switch or a restart; the
    # last-seen id is where the "New since" hairline goes on
    # restore. Both were written through qsettings_set/get before
    # they were registered here, so the spec raised KeyError and
    # both features silently did nothing — the suite caught it.
    ("chat_composer_draft", "chat/composer_draft", str, ""),
    ("chat_last_seen_id", "chat/last_seen_id", int, None),
    ("chat_last_seen_store_id", "chat/last_seen_store_id", int, None),
    # Browser connect offer (rethink Build 5, §9.1): the first
    # launch after the update shows ONE quiet Home-strip offer
    # ("Connect my browser — one tap, about a minute"),
    # dismissible, never repeated. True once the offer has been
    # SHOWN (not only dismissed) — the offer is a single nudge;
    # the refusal + strip button is the durable way back.
    ("browser_connect_offer_shown", "browser/connect_offer_shown",
     bool, False),
)


def qsettings_defaults() -> dict[str, Any]:
    """QSettings key -> default (the single home for these defaults)."""
    return {key: default for _, key, _, default in QSETTINGS_FIELDS}


_QSETTINGS_BY_KEY: dict[str, tuple[str, type, Any]] = {
    key: (name, typ, default) for name, key, typ, default in QSETTINGS_FIELDS
}

# Field name -> QSettings key (GAPS-21, C-V4): the inverse of the
# table above, so modules that name a setting by its field name
# (w10_wiring's portability export) derive the key instead of
# hand-copying the "sounds/family" spelling.
QSETTINGS_KEY_FOR_NAME: dict[str, str] = {
    name: key for name, key, _, _ in QSETTINGS_FIELDS
}


def qsettings_store(path=None):
    """The single QSettings construction site.

    Lazy import — this module must never import PySide6 at module
    top (system python3 has no PySide6). Raises ImportError when
    Qt is absent; the helpers degrade instead of raising (get
    returns the table default, set/remove raise RuntimeError).
    Every product-code construction goes through here.

    `path` is the persistence probe's explicit INI file only:
    the probe reads a temp copy of the conf, so its store must
    be built on a different path — same-path stores share the
    writer's in-process cache and would report the unflushed
    value. Production always calls this with no argument."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415 — lazy Qt

    args = ("NovaAI", "Nova") if path is None else (
        path, QSettings.Format.IniFormat)
    return QSettings(*args)


def _qsettings_resolve(settings):
    """The store for a helper call: the injected store when given,
    else the production store. Returns None when Qt is absent and
    no store was injected (the no-Qt degradation)."""
    if settings is not None:
        return settings
    try:
        return qsettings_store()
    except ImportError:
        return None


def _qsettings_coerce(raw, typ, default):
    """Coerce a stored QSettings value to the table type.

    bool: a real bool passes through; anything else is read by
    the spec's spelling rules ("1/true/yes/on" True,
    "0/false/no/off" False, else the table default) — the same
    rules SettingsStore.read used, and the same answer Qt's
    type=bool conversion gives for every value any writer stores
    ("true"/"false"/"1"/"0"). int/float: Python int()/float()
    semantics, default on an unparseable value. str: str(). list /
    object: returned as stored — the caller normalises legacy
    forms (learning/seen_ids wraps a legacy str/int into a set;
    QPoint/QByteArray geometry is isinstance-checked)."""
    if raw is None:
        return default
    if typ is bool:
        if isinstance(raw, bool):
            return raw
        return _env_bool_value(str(raw), default)
    if typ is int:
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if typ is float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    if typ is str:
        return str(raw)
    return raw


def _qsettings_coerce_for_write(value, typ, key):
    """Coerce a value to the table type before storing it.

    bool/int/float: the read coercion, but unparseable input
    raises ValueError naming the key (a write that cannot be
    read back is a bug, not a silent default). str: str().
    list / object: stored as-is. A None value never reaches
    here — qsettings_set turns it into a remove."""
    if typ is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(
            f"cannot store {value!r} for QSettings key {key!r}: not a bool")
    if typ is int:
        if isinstance(value, bool):
            return int(value)
        try:
            return int(value.strip()) if isinstance(value, str) else int(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"cannot store {value!r} for QSettings key {key!r}: not an int")
    if typ is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"cannot store {value!r} for QSettings key {key!r}: not a float")
    if typ is str:
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Corrupt-conf disclosure + quarantine (round-3 C3-F3)
#
# QSettings returns table defaults on a corrupt conf with no
# signal — and a set silently replaced the file. Qt's own status()
# does NOT catch every corrupt file (garbage with no '[' parses
# as NoError; only an unclosed section header trips FormatError),
# so the detector reads the file itself: a configparser probe
# (dummy section prepended, so top-level keys parse) fails on
# garbage and on a truncated mid-line file, and passes a healthy
# QSettings-written conf (verified against real Qt output,
# including % values and escaped quotes). The probe is pure
# Python — no Qt needed to judge the bytes. A genuinely MISSING
# conf is still a healthy empty store (missing != corrupt) and
# the happy path is untouched: the verdict is cached per
# (path, mtime, size), so a healthy file is probed once.
# ---------------------------------------------------------------------------
_QSETTINGS_CORRUPTION: dict[str, dict] = {}
_QSETTINGS_CORRUPTION_LOGGED: set[str] = set()
_QSETTINGS_HEALTHY: dict[str, tuple[int, int]] = {}


def _qsettings_file_problem(path: str) -> str | None:
    """Why the conf file on disk is not a usable store, or None.

    None means healthy-or-missing. A file that does not parse
    as INI is a problem string. Read-only: never writes. The
    healthy verdict is cached per (mtime_ns, size) so repeated
    reads do not re-parse the file."""
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"unreadable ({exc})"
    if _QSETTINGS_HEALTHY.get(path) == (stat.st_mtime_ns, stat.st_size):
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"unreadable ({exc})"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"not valid UTF-8 ({exc})"
    import configparser  # noqa: PLC0415 — stdlib, local to the probe

    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read_string("[__top__]\n" + text)
    except configparser.Error as exc:
        return f"not a valid settings file ({type(exc).__name__})"
    _QSETTINGS_HEALTHY[path] = (stat.st_mtime_ns, stat.st_size)
    return None


def _qsettings_note_corrupt(path: str, problem: str) -> None:
    """Record a corrupt conf file (first detection only).

    The log line names the file and the problem; the sticky
    record is what qsettings_corruption_state() /
    qsettings_corruption_notice() read."""
    if path not in _QSETTINGS_CORRUPTION:
        _QSETTINGS_CORRUPTION[path] = {
            "path": path,
            "error": problem,
            "detected_at": datetime.datetime.now().isoformat(
                timespec="seconds"),
        }
    if path not in _QSETTINGS_CORRUPTION_LOGGED:
        _QSETTINGS_CORRUPTION_LOGGED.add(path)
        log.warning("settings file %s looks damaged (%s); settings "
                    "read as their defaults until repaired",
                    path, problem)


def _qsettings_check_corrupt(store) -> dict | None:
    """Record a corrupt conf file for `store`, or None.

    The verdict comes from the file bytes, never from the
    store object's status() — a stale FormatError on a store
    whose file was already replaced must not re-flag a healthy
    file. A store with no file path (in-memory) has nothing on
    disk to be corrupt. Returns the sticky record, or None."""
    if store is None:
        return None
    try:
        path = str(store.fileName() or "")
    except Exception:  # noqa: BLE001 — a store without a path
        return None
    if not path:
        return None
    record = _QSETTINGS_CORRUPTION.get(path)
    if record is not None:
        return record
    problem = _qsettings_file_problem(path)
    if problem is None:
        return None
    _qsettings_note_corrupt(path, problem)
    return _QSETTINGS_CORRUPTION.get(path)


def qsettings_corruption_state(settings=None) -> dict | None:
    """A copy of the corruption record for the store, or None.

    Probes the file when nothing is recorded yet, so a caller
    that never read a setting still gets a live answer. A
    missing or healthy conf reads None (healthy empty). No Qt
    and no injected store: None (nothing to check)."""
    store = _qsettings_resolve(settings)
    if store is None:
        return None
    record = _qsettings_check_corrupt(store)
    return dict(record) if record is not None else None


def qsettings_corruption_notice(settings=None) -> str:
    """Plain-words notice for a corrupt conf file, or "".

    Names the file and the choice (set it aside and start
    fresh, or leave it). A healthy or missing conf has no
    notice. Settings keep reading as their defaults either
    way — the file is never silently replaced."""
    record = qsettings_corruption_state(settings)
    if record is None:
        return ""
    name = os.path.basename(record["path"])
    return (f"Your settings file ({name}) looks damaged and could "
            f"not be read ({record['error']}), so your settings are "
            f"showing their defaults. Nothing was deleted: the "
            f"damaged file is kept as-is. Set it aside and start "
            f"fresh?")


def qsettings_quarantine_corrupt(settings=None) -> str | None:
    """Move a corrupt conf file aside, keeping its exact bytes.

    The name is `<name>.corrupt-<YYYYMMDD-HHMMSS>` next to the
    file (counter suffix on a same-second repeat), the C3-F2
    shape. Returns the quarantine name, or None when there is
    nothing corrupt to set aside or the rename itself fails. The
    store then reads as a healthy empty one."""
    record = qsettings_corruption_state(settings)
    if record is None:
        return None
    path = record["path"]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = f"{path}.corrupt-{stamp}"
    n = 2
    while os.path.exists(target):
        target = f"{path}.corrupt-{stamp}-{n}"
        n += 1
    try:
        os.replace(path, target)
    except OSError as exc:
        log.warning("could not set aside damaged settings file %s "
                    "(%s)", path, exc)
        return None
    _QSETTINGS_CORRUPTION.pop(path, None)
    _QSETTINGS_CORRUPTION_LOGGED.discard(path)
    _QSETTINGS_HEALTHY.pop(path, None)
    log.warning("damaged settings file %s set aside as %s",
                path, target)
    return os.path.basename(target)


def reset_qsettings_corruption() -> None:
    """Clear the sticky corruption records + healthy cache.
    Tests only: production never forgets a corrupt file."""
    _QSETTINGS_CORRUPTION.clear()
    _QSETTINGS_CORRUPTION_LOGGED.clear()
    _QSETTINGS_HEALTHY.clear()


# Sentinel for "the key must be absent after this write" (a
# remove), passed to _qsettings_persisted below.
_QSETTINGS_ABSENT = object()


def _qsettings_status_name(store) -> str | None:
    """The store's status() as a plain name, or None.

    Real QSettings always has status(); the dict-backed test
    doubles have no status and no on-disk failure to report, so
    they report nothing. The name comparison keeps this module
    free of a top-level PySide6 import (Qt is lazy here):
    "NoError" / "AccessError" / "FormatError"."""
    status = getattr(store, "status", None)
    if status is None:
        return None
    try:
        result = status()
    except Exception:  # noqa: BLE001 — a store that cannot
        return None  # report its status cannot have failed loudly
    return getattr(result, "name", None) or str(result)


def _qsettings_persisted(store, key: str, expected) -> bool:
    """Whether the key's post-write state is on disk, read back
    from the FILE BYTES.

    status() is sticky for the writing store's lifetime: a
    store that once read a corrupt file still reports
    FormatError after a successful sync, and one that once hit
    a directory still reports AccessError (proven by probe —
    the file is written correctly either way, and a second
    sync never clears the status). Only the bytes on disk can
    tell a stale error from a failed write — and a fresh
    QSettings on the SAME path cannot be the reader, because
    Qt shares the settings cache per path within a process
    (proven by probe: a fresh store returns the unflushed
    value while the file does not even exist). So the bytes
    are read back through a QSettings on a TEMP COPY of the
    file — a different path, so no shared cache — and Qt does
    its own unescaping, so exotic strings compare correctly.
    True when the copy agrees with what was just written (or
    the key is absent after a remove), False when it disagrees
    or the file is missing after a set. True when the store has
    no file to read back (an in-memory store) — its sync
    cannot fail the way a file's can."""
    try:
        path = str(store.fileName() or "")
    except Exception:  # noqa: BLE001 — a store without a path
        return True
    if not path:
        return True
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        # No file: a set persisted nothing; a remove left
        # nothing behind.
        return expected is _QSETTINGS_ABSENT
    except OSError:
        return False
    import tempfile  # noqa: PLC0415 — stdlib, local to the probe

    try:
        fd, tmp = tempfile.mkstemp(prefix="nova_qs_check_",
                                   suffix=".conf")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        try:
            probe = qsettings_store(tmp)
        except ImportError:
            # No Qt on this interpreter: the bytes cannot be
            # re-read, so the errored write is treated as not
            # persisted — the same loud outcome.
            return False
        if expected is _QSETTINGS_ABSENT:
            return not probe.contains(key)
        got = probe.value(key, None)
        if got is None:
            return False
        _name, typ, default = _QSETTINGS_BY_KEY[key]
        return _qsettings_coerce(got, typ, default) == expected
    except Exception:  # noqa: BLE001 — an unreadable copy is
        return False  # not proof the write persisted
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _qsettings_sync_or_raise(store, key: str, action: str,
                             old, expected) -> None:
    """Flush the store and honour the loud-write contract.

    `action` is "write" / "remove" for the error sentence;
    `old` is the value the key held BEFORE the write (captured
    by the caller); `expected` is the value the key must hold
    after it (or _QSETTINGS_ABSENT for a remove). A sync that
    cannot persist (read-only HOME) leaves the in-memory value
    looking set while the file on disk is unchanged — so the
    pre-write value is put back in the store (a removed key is
    re-set, a set key is restored to what it was) and a
    RuntimeError names the key and the QSettings status. A
    healthy sync changes nothing: no extra write, no extra
    sync, no fresh-store read (the read-back runs only when
    the writing store reports an error)."""
    store.sync()
    status = _qsettings_status_name(store)
    if status is None or status == "NoError":
        return
    if _qsettings_persisted(store, key, expected):
        return
    if old is None:
        store.remove(key)
    else:
        store.setValue(key, old)
    raise RuntimeError(
        f"cannot {action} QSettings key {key!r}: the settings "
        f"file could not be saved (QSettings status {status})")


def qsettings_get(key: str, settings=None):
    """Read a QSettings key through the spec table.

    Unknown keys raise KeyError — a typo'd key is caught, not
    silently defaulted (same contract as env()). No Qt and no
    injected store: returns the table default (a read that cannot
    reach a store still has a defined answer). The `settings`
    parameter is the injection seam: tests pass a temp store, and
    call sites that hold a store (the shell frame's self.settings,
    SettingsStore, the w9 store) pass it through so a replaced
    store is honoured.

    Round-3 C3-F3: a corrupt conf file still reads as the table
    default (QSettings gives nothing better), but the corruption
    is now disclosed — _qsettings_check_corrupt records it (log
    line + qsettings_corruption_state) instead of silence."""
    if key not in _QSETTINGS_BY_KEY:
        raise KeyError(f"unknown QSettings key: {key!r} (not in the spec)")
    _name, typ, default = _QSETTINGS_BY_KEY[key]
    store = _qsettings_resolve(settings)
    if store is None:
        return default
    _qsettings_check_corrupt(store)
    raw = store.value(key, None)
    if raw is None:
        return default
    return _qsettings_coerce(raw, typ, default)


def qsettings_set(key: str, value, settings=None) -> None:
    """Write a QSettings key through the spec table.

    Unknown keys raise KeyError. A None value removes the key
    (QSettings semantics: setting an invalid variant clears it),
    so the table default returns afterwards. No Qt and no
    injected store: raises RuntimeError — a write that cannot
    persist must be loud. Syncs after the write (the old _qs_set
    / SettingsStore.write contract).

    Round-3 C3-F4: the sync is checked — a status other than
    NoError (read-only HOME) raises RuntimeError naming the key
    and the status, and the store is rolled back to the value
    the key held before the write, so get() never presents a
    value that never persisted."""
    if key not in _QSETTINGS_BY_KEY:
        raise KeyError(f"unknown QSettings key: {key!r} (not in the spec)")
    _name, typ, _default = _QSETTINGS_BY_KEY[key]
    store = _qsettings_resolve(settings)
    if store is None:
        raise RuntimeError(
            f"cannot write QSettings key {key!r}: Qt is not available "
            "(no injected store)")
    # Round-3 C3-F3: a corrupt conf file is quarantined first
    # (original bytes kept alongside) — a set never silently
    # replaces it. When the quarantine itself fails, the write is
    # refused loudly rather than destroying the old file.
    if _qsettings_check_corrupt(store) is not None:
        if qsettings_quarantine_corrupt(settings) is None:
            raise RuntimeError(
                f"cannot write QSettings key {key!r}: the settings "
                f"file looks damaged and could not be set aside")
    old = store.value(key, None)
    if value is None:
        store.remove(key)
        expected = _QSETTINGS_ABSENT
    else:
        written = _qsettings_coerce_for_write(value, typ, key)
        store.setValue(key, written)
        expected = written
    _qsettings_sync_or_raise(store, key, "write", old, expected)


def qsettings_contains(key: str, settings=None) -> bool:
    """Whether a QSettings key is present — the unset probe.

    A read with the table default cannot tell "set" from
    "unset", and several call sites need exactly that
    distinction (the onboarding sounds probe, the w9 density
    migration, the motion/reduced legacy probe, the
    setup/brain_choice probe). Unknown keys raise KeyError.
    No Qt and no injected store: returns False (nothing is set
    when there is no store)."""
    if key not in _QSETTINGS_BY_KEY:
        raise KeyError(f"unknown QSettings key: {key!r} (not in the spec)")
    store = _qsettings_resolve(settings)
    if store is None:
        return False
    _qsettings_check_corrupt(store)
    return bool(store.contains(key))


def qsettings_remove(key: str, settings=None) -> None:
    """Remove a QSettings key so the table default returns.

    Unknown keys raise KeyError. No Qt and no injected store:
    raises RuntimeError (same contract as set). Syncs after the
    remove. Round-3 C3-F4: the sync is checked the same way as
    set — a status other than NoError raises RuntimeError
    naming the key and the status, and the removed value is put
    back in the store, so the key never looks removed when the
    removal never persisted."""
    if key not in _QSETTINGS_BY_KEY:
        raise KeyError(f"unknown QSettings key: {key!r} (not in the spec)")
    store = _qsettings_resolve(settings)
    if store is None:
        raise RuntimeError(
            f"cannot remove QSettings key {key!r}: Qt is not available "
            "(no injected store)")
    # Round-3 C3-F3: same quarantine-first rule as set — a
    # remove never silently replaces a corrupt conf file.
    if _qsettings_check_corrupt(store) is not None:
        if qsettings_quarantine_corrupt(settings) is None:
            raise RuntimeError(
                f"cannot remove QSettings key {key!r}: the settings "
                f"file looks damaged and could not be set aside")
    old = store.value(key, None)
    store.remove(key)
    _qsettings_sync_or_raise(store, key, "remove", old,
                             _QSETTINGS_ABSENT)


# ---------------------------------------------------------------------------
# Round-3 section C fix queue item 14: settings-write degradation record.
#
# C3-F4 made qsettings_set/remove LOUD (RuntimeError on a genuine write
# failure), but ~50 call sites sat inside broad `except Exception: pass`
# guards, so the loud failure was swallowed one layer up and the setting
# still silently lost. The fix is per-site (see the call sites), and
# every fixed site funnels through the helpers below, which share one
# shape with the cost-ledger degraded record (core/cost_ledger.py):
# a log line naming the key, plus a sticky per-key record the Activity
# feed renders as a "Settings degraded" marker — so a settings write
# that could not persist is never silent again, and the user learns
# in one place that their changes in this session may not survive a
# restart. The record is process-local (a read-only HOME is a property
# of this launch, not of the settings file) and is never written to
# disk — a degraded record must not itself need the broken store.
# ---------------------------------------------------------------------------
_SETTINGS_WRITE_FAILURES: dict[str, dict[str, Any]] = {}


def note_qsettings_write_failure(key: str, exc: BaseException) -> None:
    """Record one settings write that could not persist. Never raises:
    the note itself must not break the caller (it runs inside UI
    handlers and migrations). Logs the key + cause and keeps a sticky
    per-key count, so the Activity feed can say how much was lost."""
    try:
        now = time.time()
        rec = _SETTINGS_WRITE_FAILURES.get(key)
        if rec is None:
            rec = {"count": 0, "last_error": "", "first_at": now,
                   "last_at": now}
            _SETTINGS_WRITE_FAILURES[key] = rec
        rec["count"] += 1
        rec["last_error"] = str(exc)[:200]
        rec["last_at"] = now
        print(f"[settings] could not save {key!r}: {exc}")
    except Exception:  # noqa: BLE001 - the note must never raise
        pass


def qsettings_write_degraded_state() -> dict[str, dict[str, Any]]:
    """A COPY of the sticky failed-write record, keyed by settings
    key. Empty when no settings write has ever failed in this
    process — the Activity feed reads this. Never raises."""
    try:
        return {key: dict(rec)
                for key, rec in _SETTINGS_WRITE_FAILURES.items()}
    except Exception:  # noqa: BLE001 - the feed must never break
        return {}


def reset_qsettings_write_degraded() -> None:
    """Clear the sticky failed-write record. Tests only: production
    code never resets it (a lost write stays lost)."""
    _SETTINGS_WRITE_FAILURES.clear()


def qsettings_set_noted(key: str, value, settings=None) -> bool:
    """Best-effort qsettings_set that never hides a write failure:
    returns True when the value persisted, and on a genuine write
    failure (the C3-F4 RuntimeError) logs the key + cause, records
    the sticky degraded state, and returns False. A KeyError for
    an unknown key, and any other programmer error (a bad value),
    still propagates — a bug must stay loud, only a failed write
    is degraded. Use at call sites whose old `except: pass` guard
    swallowed the C3-F4 raise."""
    try:
        qsettings_set(key, value, settings=settings)
        return True
    except RuntimeError as exc:
        note_qsettings_write_failure(key, exc)
        return False


def qsettings_remove_noted(key: str, settings=None) -> bool:
    """Best-effort qsettings_remove, same contract as
    qsettings_set_noted: True when the removal persisted, False +
    logged + recorded on a genuine write failure. Unknown keys
    still raise KeyError."""
    try:
        qsettings_remove(key, settings=settings)
        return True
    except RuntimeError as exc:
        note_qsettings_write_failure(key, exc)
        return False
