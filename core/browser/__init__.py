"""Browser leg package (click-anywhere Batch 1, Plan 9 §1.2).

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

Holds the native-messaging host, the VYUK<->host localhost channel,
the browser denylist store and the install registration helper.
The viewport conversion itself lives in ``viewport_convert`` (a
separate Batch 1 piece) and is imported by the host only when present.

NOTE (Batch 1 shadowing fix): this package shadows the pre-existing
``core/browser.py`` module, which now lives at ``core/browser/browser.py``.
The legacy module's public names are re-exported lazily below (PEP 562
``__getattr__``) so existing importers — ``from core import browser`` in
``core/tools_handlers_pack_hands.py`` (``register_all``), the music-player
rungs (``browser_control`` / ``browser_click`` / ``page_playback_*``) and
the tests that patch ``core.browser.browser_control`` — keep working
without edits. The lazy form matters: importing this package must NOT
pull in the legacy browser worker (Playwright thread-affine, heavy)
when a caller only wants ``viewport_convert`` or ``denylist``.
"""

__all__ = [
    "bridge_host",
    "browser_host_channel",
    "denylist",
    "install_register",
    "viewport_convert",
    # Legacy re-exports (resolved lazily via __getattr__ below).
    "browser",
]


def __getattr__(name):
    """Lazily re-export the legacy ``core/browser.py`` module's names.

    Only called for attributes not found on this package itself, so
    submodule imports (``core.browser.viewport_convert`` etc.) are
    unaffected. ``mock.patch("core.browser.browser_control")`` works:
    patch reads the real function through here, then sets (and later
    deletes) the attribute on this module, shadowing the lazy lookup.

    Uses ``importlib.import_module`` (not ``from . import browser``):
    the latter re-enters this ``__getattr__`` via the package-attribute
    lookup and recurses.
    """
    import importlib

    try:
        _legacy = importlib.import_module(".browser", __package__)
    except ImportError as exc:  # pragma: no cover - legacy module missing
        raise AttributeError(
            "module %r has no attribute %r" % (__name__, name)
        ) from exc
    if name == "browser":
        return _legacy
    try:
        return getattr(_legacy, name)
    except AttributeError:
        raise AttributeError(
            "module %r has no attribute %r" % (__name__, name)
        ) from None


def __dir__():
    import importlib

    try:
        _legacy = importlib.import_module(".browser", __package__)
        legacy_names = [n for n in dir(_legacy) if not n.startswith("__")]
    except ImportError:  # pragma: no cover - legacy module missing
        legacy_names = []
    return sorted(set(globals()) | set(__all__) | set(legacy_names))
