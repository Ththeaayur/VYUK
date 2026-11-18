"""Command Deck design system (guide §8, §10).

Shared HUD components live here so ``gui/window_widgets.py`` does not
grow. Pure modules (palette, motion, profiles) are importable
without Qt; widget modules are loaded lazily so importing this
package never pulls Qt into a Qt-free caller.

Rule for everything in this package: NO colour literals. Every colour
comes from ``core.tokens`` (the command-deck palette added in
Phase B) — the literal-colour guard test
(``tests/test_commanddeck_guards.py``) scans this package with an
empty allowlist, so a literal here fails the suite from birth.
"""

_LAZY = {
    "HudPanel": "panels",
    "chamfered_path": "panels",
    "HudDialog": "dialogs",
    "HudToast": "toasts",
    "HudBackdrop": "backdrop",
    "SegmentBar": "progress",
    "HudSwitch": "switches",
    "HudTabs": "tabs",
    "StatusPill": "pills",
    "MissionRail": "mission",
    "ActionTrace": "mission",
    "CommandRadar": "radar",
    "SCOPES": "radar",
    "ContextDrawer": "drawer",
    "TelemetryPanel": "telemetry",
    "EmptyState": "empty",
    "OrbThumbnail": "orbthumb",
    "StatusStrip": "statusstrip",
}

__all__ = sorted(_LAZY)


def __getattr__(name):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __package__)
    value = getattr(module, name)
    globals()[name] = value
    return value
