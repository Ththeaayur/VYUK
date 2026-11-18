"""Wave 4 (v5.59) BR-6 Camoufox lane — a SEPARATE scraping /
challenge lane, never the Chrome replacement.

Lane isolation is mandatory and permanent in code, not a
comment:
* ISOLATED Firefox profile: the lane's profile lives in
  ``data/camoufox_lane_profile`` — its own directory, created
  by no function in this module (paths are computed, never
  mkdir'd here). It CANNOT reuse her Chrome profile
  (``data/browser_profile``), the debug profile
  (``data/chrome_debug_profile``), or any real-Chrome user-data
  dir: ``assert_lane_isolation`` raises on any of those.
* SEPARATE lane flag: ``VYUK_CAMOUFOX_LANE``, default OFF.
  The lane is never the default path and is never reached from
  core/browser.py's worker: this module is imported by no
  shipped default path, and the worker's channel stays
  ``"chrome"`` with her persistent profile.
* NO Chrome logins: the lane carries no cookies, logins, or
  session state from her Chrome profile — a fresh Firefox
  profile means signed-in work stays on the worker's browser,
  and the lane never signs in as her.
* GEOIP PERMANENTLY OFF: ``lane_launch_options(geoip=True)``
  raises. The [geoip] extra was audited, not bundled (see
  core/desktop/browser_pilot_licences.py): its databases are
  unpinned third-party downloads with unverified licences, so
  the lane runs without geoip, always. There is no flag, env
  var, or parameter that turns geoip on through this module.

Camoufox itself is lazy-only: ``camoufox_available`` is a
``find_spec`` probe, never an import. The patched Firefox
binary is fetched by Camoufox's package manager at trial time
from GitHub releases — it is not in the tree, not in
requirements.txt, and never ships with the product. The lane
is therefore pilot-only: a trial-time scraping/challenge lane,
not a product browser.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from .. import config_spec

CAMOUFOX_LANE_ENV = "VYUK_CAMOUFOX_LANE"
LANE_PROFILE_DIRNAME = "camoufox_lane_profile"

# Profile directories the lane must NEVER resolve to. These are
# the worker's own profiles (core/browser.py) plus the generic
# real-Chrome user-data locations; matching is by resolved path
# and by directory name, so a symlink or a renamed parent cannot
# smuggle a forbidden profile past the check.
_FORBIDDEN_PROFILE_NAMES = ("browser_profile", "chrome_debug_profile",
                            "Chrome", "Google Chrome", "Chromium")


def lane_enabled(explicit: bool | None = None) -> bool:
    """OFF by default. An explicit parameter wins; otherwise the
    VYUK_CAMOUFOX_LANE environment flag (``"1"`` = on). The lane
    is opt-in, never the default path, and enabling it enables
    ONLY this lane — it changes nothing about the worker."""
    if explicit is not None:
        return bool(explicit)
    return config_spec.env(CAMOUFOX_LANE_ENV).strip() == "1"


def lane_profile_dir(tree_root: str | Path | None = None) -> Path:
    """The lane's isolated Firefox profile directory. ``tree_root``
    defaults to this file's tree root (its parent's parent's parent);
    tests pass a scratch root so no test ever touches the real
    data/ directory. This function computes a path; it creates
    nothing and launches nothing."""
    root = Path(tree_root) if tree_root is not None else \
        Path(__file__).resolve().parent.parent.parent  # Phase 3 D2a: one level deeper in core/desktop/
    return root / "data" / LANE_PROFILE_DIRNAME


def assert_lane_isolation(profile_dir: str | Path) -> Path:
    """Raise ValueError unless ``profile_dir`` is an isolated
    lane profile: it must be named exactly
    ``camoufox_lane_profile`` and must not be, or sit inside,
    any forbidden profile directory. Returns the resolved path
    on success. Call this before ANY lane launch, every time —
    the check is the isolation, not a comment about it."""
    resolved = Path(profile_dir).expanduser().resolve()
    if resolved.name != LANE_PROFILE_DIRNAME:
        raise ValueError(
            f"Camoufox lane profile must be named "
            f"{LANE_PROFILE_DIRNAME!r}; got {resolved.name!r} "
            f"({resolved}). The lane never reuses a Chrome "
            f"profile.")
    for forbidden in _FORBIDDEN_PROFILE_NAMES:
        if forbidden in resolved.parts:
            raise ValueError(
                f"Camoufox lane profile resolves inside "
                f"forbidden profile {forbidden!r}: {resolved}")
    return resolved


def lane_launch_options(profile_dir: str | Path, *,
                        geoip: bool = False,
                        tree_root: str | Path | None = None) -> dict:
    """Launch options for the lane, after isolation is proven.

    ``geoip`` must stay False: passing True raises, permanently
    — the [geoip] databases are audit-only (unverified licences,
    unpinned downloads) and are never bundled or used by the
    lane. The returned dict carries no Chrome profile, no
    Chrome channel, and no login state: the lane is a fresh
    Firefox profile, and signed-in work stays on the worker's
    browser. Options are data for the trial runner; this
    function launches nothing and imports nothing."""
    if geoip:
        raise ValueError(
            "Camoufox lane geoip is permanently OFF: the [geoip] "
            "extra was audited, not bundled — its databases carry "
            "unverified licences. The lane runs without geoip.")
    resolved = assert_lane_isolation(profile_dir)
    if tree_root is not None:
        expected = lane_profile_dir(tree_root).resolve()
        if resolved != expected:
            raise ValueError(
                f"Camoufox lane profile {resolved} is not the "
                f"lane profile for this tree root ({expected})")
    return {
        "lane": "camoufox",
        "browser": "firefox-patched",
        "profile_dir": str(resolved),
        "channel": None,  # Firefox lane: no Chrome channel, ever.
        "chrome_profile_reused": False,
        "chrome_logins_reused": False,
        "geoip": False,
        "is_default_path": False,
    }


def camoufox_available() -> bool:
    """True when the camoufox package is importable. Probe only:
    this never imports camoufox, so importing this lane module
    cannot pull the package (or its Firefox fetcher) into a
    shipped process."""
    return importlib.util.find_spec("camoufox") is not None


def lane_forbidden() -> tuple[str, ...]:
    """The lane's permanent prohibitions, as data (for the trial
    runner's checklist and for tests)."""
    return (
        "reuse her Chrome profile or logins",
        "run as the default browser path",
        "run with geoip enabled",
        "sign in as her (the lane profile starts fresh)",
        "ship the patched Firefox binary in the tree",
        "reach the worker's Chrome session",
    )
