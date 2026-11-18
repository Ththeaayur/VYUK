"""One shared browser-engine interface (GAPS-18: review flaws D-4 + D-5, §6.6).

The engine is stock Playwright, and this module is the ONE place that
choice is made:

* ``start_playwright()`` is the single sync_playwright start call in
  the tree. Two callers start through it and nothing else starts an
  engine: the session (``browser_session._start_pw`` — the CDP attach
  to the user's real Chrome) and the automation driver
  (``browser_automation._PlaywrightBackend.start`` — the launched
  persistent context on VYUK's own profile). GAPS-1 removed the
  patched engine; GAPS-18 removes the second start site, so the
  "which engine" question has one answer in one file.
* ``engine_available()`` / ``engine_status()`` are the one availability
  probe and report (moved here from browser_automation, behaviour
  unchanged).
* ``ENGINES`` names every browser engine in the tree. Playwright is
  the engine; Camoufox is a registered LANE, not an engine: it has
  no driver and ``start_engine("camoufox")`` refuses instead of
  launching — the lane stays pilot-only and options-only, its
  isolation guarantees untouched (see browser_camoufox_lane.py).
  Registering the lane here is what makes "no shared interface"
  false: every browser surface in the tree is named in this one
  registry, with exactly one start function.

What this module is NOT: it is not the session store. The worker
thread and the global ``_state`` dict stay in browser_session —
Playwright's sync API is thread-affine, so every Playwright object
lives on the one worker thread there, and that constraint is real.
This module answers "which engine, and how is it started"; the
session answers "what is open right now".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ENGINE = "playwright"


def _data_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data"


def browsers_dir() -> Path:
    """The Playwright driver browsers root (data/ms-playwright).

    Single source of truth: browser_session._BROWSERS_DIR is this
    value. A user's own explicit PLAYWRIGHT_BROWSERS_PATH always
    wins over it (see start_playwright)."""
    return _data_dir() / "ms-playwright"


def start_playwright():
    """Start the browser engine. Raises ImportError if absent.

    The ONE sync_playwright start call in the tree (GAPS-18): the
    session's _start_pw and the automation driver's backend both
    start through here, so there is no second engine start path,
    no seam, no factory, and no flag arm. The engine is stock
    Playwright (GAPS-1): the import names playwright directly.

    v5.35: when the app has provisioned Playwright browsers under
    its data root, the driver must look there — its default location
    is a per-user cache nothing fills on a fresh install. A user's
    own explicit setting always wins.
    """
    import os

    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ \
            and browsers_dir().exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def engine_available() -> bool:
    """True when the playwright package is importable. Probe only:
    this never imports playwright, so importing this module cannot
    pull the driver into a process."""
    return importlib.util.find_spec("playwright") is not None


def engine_status() -> dict:
    """Honest availability report. Chrome presence is NOT knowable
    without launching, so it is reported as unknown until launch —
    never guessed."""
    available = engine_available()
    version = None
    if available:
        try:
            from importlib import metadata as _md
            version = _md.version("playwright")
        except Exception:  # noqa: BLE001 — version is a nicety
            version = None
    return {
        "ok": available,
        "engine": ENGINE,
        "available": available,
        "version": version,
        "chrome": "unknown until launch",
        "reason": (None if available else
                   "engine_unavailable: playwright is not installed "
                   "in this interpreter"),
    }


def camoufox_lane_options(profile_dir, *, geoip: bool = False,
                          tree_root=None) -> dict:
    """Launch options for the Camoufox lane, through the one interface.

    Thin delegate to browser_camoufox_lane.lane_launch_options — the
    lane module is imported lazily so this module never pulls it (or
    its config read) into a process that only wants the Playwright
    engine. The lane's rules are the lane module's: isolated profile,
    geoip permanently off, never the default path."""
    from .browser_camoufox_lane import lane_launch_options

    return lane_launch_options(profile_dir, geoip=geoip,
                               tree_root=tree_root)


ENGINES = {
    "playwright": {
        "name": "playwright",
        "kind": "engine",
        "channel": "chrome",
        "profile": "VYUK's own managed profile (data/browser_profile) "
                   "or the real-Chrome CDP attach — never a guessed profile",
        "start": start_playwright,
        "options": None,
    },
    "camoufox": {
        "name": "camoufox",
        "kind": "lane",
        "channel": None,  # Firefox lane: no Chrome channel, ever.
        "profile": "data/camoufox_lane_profile (isolated Firefox profile)",
        "start": None,  # Pilot-only lane: no driver, never started here.
        "options": camoufox_lane_options,
    },
}


def get_engine(name: str) -> dict:
    """The registry entry for a named engine/lane. Unknown names
    raise KeyError naming the known engines — never a guess."""
    try:
        return ENGINES[name]
    except KeyError:
        raise KeyError(
            f"unknown browser engine {name!r} — known engines: "
            f"{sorted(ENGINES)}") from None


def start_engine(name: str):
    """Start a named engine through the one interface.

    Playwright starts. The Camoufox lane refuses: it is a
    pilot-only, options-only lane with no driver, and starting it
    would be a second browser the product never ships — the
    refusal names that instead of launching anything."""
    info = get_engine(name)
    start = info["start"]
    if start is None:
        raise RuntimeError(
            f"browser engine {name!r} cannot be started: it is a "
            "pilot-only lane (options-only, no driver) — use "
            "camoufox_lane_options() for its launch options, never "
            "a launch")
    return start()
