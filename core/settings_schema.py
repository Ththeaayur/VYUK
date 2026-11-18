"""Settings architecture (Wave 9 / v5.64, Leaf 3).

Source of truth: a pydantic-settings schema — lazily loaded, never a
hard dependency. :func:`load_pydantic_model` builds the real
``BaseSettings`` subclass when pydantic-settings is importable;
otherwise :class:`NovaSettingsData` (a plain dataclass with the same
fields and defaults) is the working model and
:func:`settings_model` returns it. Both shapes expose
``model_dump()`` / ``model_validate()``-style access so callers do
not branch on which one they got.

PERSISTENCE — READ THIS FIRST:
  The adapter writes QSettings("NovaAI", "Nova"), FULL STOP.
  There is no "(or JSON)" store option in this module: the "(or
  JSON)" wording in the wave brief must NOT move persistence off
  QSettings, and it does not. HDF5 / JSON / dict stores (UI-29 /
  UI-31) are REJECTED — they are not built, not stubbed, not
  offered as options. A caller that wants a different store is
  asking for a different module and a new decision, not a flag here.

SEAM — Settings page AND the app_settings chat tool read the SAME model:
  Both sides already speak the same QSettings keys (the table in
  ``QSETTINGS_KEYS`` is copied from the live keys
  ``core/app_settings.py`` reads and ``gui/window_settings.py``
  writes: "theme", "orb_style", "sounds_enabled",
  "suggestions/enabled"). This module exposes the shared model
  (:func:`model_from_store`, :func:`apply_to_store`) and the key
  table so the two readers cannot drift. GAPS-11 landed the
  closing step: ``core/app_settings.py``'s ``_qs_get`` /
  ``_qs_set`` now delegate to the spec helpers
  (``core/config_spec.qsettings_get`` / ``qsettings_set``), and
  this store reads/writes through the same spec table — the chat
  tool and the Settings page agree because both read one table.
  GAPS-21 (C-V4) closed the last hand copy: the defaults in
  FIELD_SPECS are DERIVED from the spec now (the five QSettings
  defaults from qsettings_defaults(), the five config-backed
  defaults from the spec fields they model), so a default change
  in the spec lands here without a second edit. Config-backed
  fields (voices, language, loudness, auto-switch) are modelled
  here for search/display, but the store REFUSES to write them:
  they persist via the config/.env path that app_settings already
  owns, and this module will not invent a second write path for
  them. ``SettingsStore.write`` raises ValueError for those
  fields, naming the owning path.

Searchable rows: :func:`build_rows` returns one row per field with
``modified`` (current != default), a substring filter over label +
name, and per-row reset (:func:`reset_row` removes the QSettings
key so the default returns — it never writes the default value
back, so a future default change still propagates).

Licence (verified from the sdist primary, not metadata):
  pydantic-settings 2.16.1 — MIT (LICENSE, Samuel Colvin and other
    contributors). Lazy import only; NOT in requirements.txt.

MODULE_VERSION = "5.64.0".
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from .config_spec import (
    field_default,
    qsettings_defaults,
    qsettings_get,
    qsettings_remove,
    qsettings_set,
    qsettings_store,
)

MODULE_VERSION = "5.64.0"

# ---------------------------------------------------------------- schema

# name -> (label, default, kind, qsettings_key | None, owner note)
# qsettings_key None means: modelled + searchable, but NOT written by
# this store (config/.env path owned by core/app_settings.py).
#
# The defaults are DERIVED from the spec (GAPS-21, C-V4), not
# literals: the five QSettings-backed defaults come from
# qsettings_defaults() (the spec's QSETTINGS_FIELDS table), the
# five config-backed defaults from the spec fields they model
# (_CONFIG_FIELD_FOR_SETTING). A default change in the spec lands
# here without a second edit; the prover checks the derivation
# against the old literals.
_CONFIG_FIELD_FOR_SETTING = {
    "live_voice": "voice_name",
    "local_voice": "kokoro_voice",
    "language": "preferred_language",
    "tts_loudness_enabled": "tts_loudness_enabled",
    "voice_auto_switch": "voice_auto_switch",
}

_FIELD_META: dict[str, tuple] = {
    "theme": ("Theme", "choice", "theme", "qsettings"),
    "orb_style": ("Orb style", "choice", "orb_style", "qsettings"),
    "sounds_enabled": ("Earcons (sounds)", "bool", "sounds_enabled",
                       "qsettings"),
    "suggestions_enabled": ("Suggestions", "bool", "suggestions/enabled",
                            "qsettings"),
    "density": ("Density", "choice", "density", "qsettings"),
    "live_voice": ("Live voice", "choice", None,
                   "config/.env via app_settings"),
    "local_voice": ("Local voice", "choice", None,
                    "config/.env via app_settings"),
    "language": ("Language", "choice", None,
                 "config via app_settings"),
    "tts_loudness_enabled": ("TTS loudness", "bool", None,
                             "config via app_settings"),
    "voice_auto_switch": ("Voice auto-switch", "bool", None,
                          "config via app_settings"),
    "telemetry_enabled": ("Telemetry (dev-only, restart required)",
                          "bool", "telemetry/enabled", "qsettings"),
}


def _field_spec_default(name: str):
    """One field's default, from the spec table that owns it."""
    _label, _kind, key, _owner = _FIELD_META[name]
    if key is not None:
        return qsettings_defaults()[key]
    return field_default(_CONFIG_FIELD_FOR_SETTING[name])


FIELD_SPECS: dict[str, tuple] = {
    name: (label, _field_spec_default(name), kind, key, owner)
    for name, (label, kind, key, owner) in _FIELD_META.items()
}

# The shared key table: model name -> QSettings key (None = not QSettings).
QSETTINGS_KEYS: dict[str, str | None] = {
    name: spec[3] for name, spec in FIELD_SPECS.items()
}

# REJECTED stores (UI-29/UI-31): never build, never offer.
REJECTED_STORES = ("HDF5", "JSON-file", "plain-dict")


def defaults() -> dict:
    return {name: spec[1] for name, spec in FIELD_SPECS.items()}


@dataclass
class NovaSettingsData:
    """Always-available model (used when pydantic-settings is absent).

    Defaults come from FIELD_SPECS (themselves derived from the
    spec, GAPS-21) — one source, not a second copy of the literals.
    """

    theme: str = FIELD_SPECS["theme"][1]
    orb_style: str = FIELD_SPECS["orb_style"][1]
    sounds_enabled: bool = FIELD_SPECS["sounds_enabled"][1]
    suggestions_enabled: bool = FIELD_SPECS["suggestions_enabled"][1]
    density: str = FIELD_SPECS["density"][1]
    live_voice: str = FIELD_SPECS["live_voice"][1]
    local_voice: str = FIELD_SPECS["local_voice"][1]
    language: str = FIELD_SPECS["language"][1]
    tts_loudness_enabled: bool = FIELD_SPECS["tts_loudness_enabled"][1]
    voice_auto_switch: bool = FIELD_SPECS["voice_auto_switch"][1]
    # GAPS-27 (M-V8): dev-only telemetry opt-in. OFF by default;
    # the toggle only ever turns it ON when the user flips it.
    telemetry_enabled: bool = FIELD_SPECS["telemetry_enabled"][1]

    def model_dump(self) -> dict:
        return {name: getattr(self, name) for name in FIELD_SPECS}

    @classmethod
    def model_validate(cls, data: dict) -> "NovaSettingsData":
        known = {name: data[name] for name in FIELD_SPECS if name in data}
        return cls(**known)


def load_pydantic_model():
    """Lazily build the real pydantic-settings model, or return None.

    Returns None (not an exception) when pydantic-settings is absent
    or unusable — graceful degradation is the contract.
    """
    try:
        from pydantic_settings import BaseSettings  # noqa: PLC0415 — lazy

        fields = {
            name: (type(spec[1]), spec[1])
            for name, spec in FIELD_SPECS.items()
        }
        model = type("NovaSettings", (BaseSettings,), {"__annotations__": {
            name: typ for name, (typ, _d) in fields.items()
        }, **{name: default for name, (_t, default) in fields.items()}})
        # Pydantic v2 needs model_config, not env behaviour: this model
        # is a schema, never an env reader — no env prefix, no .env file.
        model.model_config = {"env_prefix": "NOVA_NEVER_"}
        return model
    except Exception:  # noqa: BLE001 — degradation, not failure
        return None


def settings_model():
    """The source-of-truth model class: pydantic when available, else dataclass."""
    model = load_pydantic_model()
    return model if model is not None else NovaSettingsData


def probe_pydantic_settings() -> dict:
    import importlib.util

    if importlib.util.find_spec("pydantic_settings") is None:
        return {"available": False, "version": None}
    try:
        import pydantic_settings  # noqa: PLC0415 — lazy by design

        return {"available": True,
                "version": getattr(pydantic_settings, "__version__", None)}
    except Exception as exc:  # noqa: BLE001 — probe never raises
        return {"available": False, "version": None, "error": str(exc)}


# ---------------------------------------------------------------- store


class SettingsStore:
    """QSettings adapter. Writes QSettings, full stop.

    ``settings`` may be an injected QSettings instance (tests pass a
    temp INI-backed one); production constructs the spec's single
    store (``config_spec.qsettings_store()``) — the exact store
    the window and app_settings use. Reads/writes go through the
    spec helpers, so the defaults and coercion live in the spec
    table, not here.
    """

    def __init__(self, settings=None):
        if settings is not None:
            self.settings = settings
        else:
            self.settings = qsettings_store()

    def read(self, name: str):
        spec = FIELD_SPECS[name]
        key = spec[3]
        if key is None:
            # Config-backed: this store has no value to report; the
            # model default stands until the owning path supplies one.
            return spec[1]
        return qsettings_get(key, settings=self.settings)

    def write(self, name: str, value) -> None:
        spec = FIELD_SPECS[name]
        if spec[3] is None:
            raise ValueError(
                f"{name!r} is not QSettings-backed; it persists via "
                f"{spec[4]}. This store writes QSettings only and will "
                "not invent a second write path."
            )
        qsettings_set(spec[3], value, settings=self.settings)

    def reset(self, name: str) -> None:
        """Remove the key so the default returns (never writes default)."""
        spec = FIELD_SPECS[name]
        if spec[3] is None:
            raise ValueError(
                f"{name!r} has no QSettings key to reset; it is owned by {spec[4]}."
            )
        qsettings_remove(spec[3], settings=self.settings)

    def snapshot(self) -> dict:
        return {name: self.read(name) for name in FIELD_SPECS}


def model_from_store(store: SettingsStore):
    """Build the shared model from the store (Settings page + chat tool seam)."""
    model_cls = settings_model()
    data = store.snapshot()
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls(**data)


def apply_to_store(store: SettingsStore, model) -> list[str]:
    """Write every QSettings-backed field of a model. Returns written names.

    Config-backed fields are skipped (not written, not errored here —
    the seam documents that they travel via app_settings).
    """
    dump = model.model_dump() if hasattr(model, "model_dump") else dict(model)
    written = []
    for name, value in dump.items():
        if QSETTINGS_KEYS.get(name) is not None:
            store.write(name, value)
            written.append(name)
    return written


# ---------------------------------------------------------------- rows


@dataclass
class SettingRow:
    name: str
    label: str
    value: object
    default: object
    modified: bool
    kind: str
    qsettings_key: str | None


def build_rows(store: SettingsStore, filter_text: str = "") -> list[SettingRow]:
    """Searchable settings rows: substring filter over label + name."""
    needle = (filter_text or "").strip().lower()
    rows = []
    for name, spec in FIELD_SPECS.items():
        label, default, kind, key, _owner = spec
        value = store.read(name)
        if needle and needle not in label.lower() and needle not in name.lower():
            continue
        rows.append(SettingRow(
            name=name, label=label, value=value, default=default,
            modified=(value != default), kind=kind, qsettings_key=key,
        ))
    return rows


def reset_row(store: SettingsStore, name: str) -> SettingRow:
    """Per-row reset: remove the key, then return the refreshed row."""
    store.reset(name)
    rows = build_rows(store)
    for row in rows:
        if row.name == name:
            return row
    raise KeyError(name)
