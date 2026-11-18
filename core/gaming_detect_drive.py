"""GM detect-and-drive framing (Gaming Pack framing, §5.22).

The whole Gaming Pack is an ENTHUSIAST TIER, never core: plugin
installs, Streamer.bot, a user's own Steam key, and external
servers all erode zero-setup across the shelf. So the pack is
framed as detect-and-drive, DEGRADING TO THE ZERO-SETUP CORE.
This module is that framing as code: given a capability set, it
resolves the tier and the fallback ladder. Nothing here probes
anything — capabilities are caller-supplied facts (a detector
elsewhere reports what is installed; this module decides what
that means for the tier).

TIERS:
- "enthusiast": everything detected (OBS, Steam, own key where
  needed). Full Gaming Pack surfaces are available.
- "degraded": some capability missing — the ladder names the
  fallback for each gap (OBS absent -> WF-31 ddagrab -> core).
- "core": no gaming capability detected — the zero-setup core
  stands alone, and no Gaming surface pretends otherwise.

EXTERNAL-SERVICE EROSION (§5.22, binding): a service that needs
its own key, a Patreon gate, or an external server erodes
zero-setup and can never be presented as core. Streamer.bot is
closed freeware whose core licence is STILL-OWED — drive a user's
copy only, never bundle, never present as core. Firebot is
GPL-3.0 — study the pattern, never ship it. Owncast (MIT) and
Restreamer (Apache-2.0) are the licence-clean self-host patterns,
but self-hosting is itself an external server, so they still
erode zero-setup and stay enthusiast-tier.

TELEMETRY: none. Pure data in, data out.
"""

from __future__ import annotations

TIERS = ("enthusiast", "degraded", "core")

# External services: licence/terms posture + whether they erode
# zero-setup. "drive" means: drive a user-installed copy only.
EXTERNAL_SERVICES = {
    "streamer_bot": {
        "licence": "STILL-OWED (closed freeware; no core EULA found)",
        "erodes_zero_setup": True,
        "form": "drive user copy only; never bundle; never core",
    },
    "firebot": {
        "licence": "GPL-3.0 (verified primary) — copyleft",
        "erodes_zero_setup": True,
        "form": "STUDY the pattern; never ship",
    },
    "owncast": {
        "licence": "MIT (verified primary)",
        "erodes_zero_setup": True,
        "form": "self-host pattern; external server, so enthusiast-tier",
    },
    "restreamer": {
        "licence": "Apache-2.0 (verified primary)",
        "erodes_zero_setup": True,
        "form": "self-host pattern; external server, so enthusiast-tier",
    },
    "obs": {
        "licence": "GPL-2.0 (drive-only; never bundle)",
        "erodes_zero_setup": True,
        "form": "drive user copy via obs-websocket; WF-31 fallback covers absence",
    },
    "steam_key": {
        "licence": "user's own key only — never a shipped VYUK key",
        "erodes_zero_setup": True,
        "form": "user pastes their own key once, stored locally, or no key at all",
    },
}

# Fallback ladder, in order, for each missing capability.
FALLBACKS = {
    "obs": ["WF-31 ddagrab rolling capture", "core (no capture)"],
    "steam": ["manual wishlist (no Steam API)", "core (no library)"],
    "own_steam_key": ["local Steam files (loginusers.vdf, appmanifest)",
                      "no API features"],
    "game_library": ["local process-session tracking (launch -> exit)",
                     "core (no library)"],
}


def service_as_core(service):
    """Can this service be presented as core? Verdict dict.

    Any service that erodes zero-setup returns allowed=False.
    Unknown services fail closed — they are not core until
    someone proves otherwise.
    """
    info = EXTERNAL_SERVICES.get(service)
    if info is None:
        return {"allowed": False,
                "reason": "Unknown service %r — not core until proven." % (service,)}
    if info["erodes_zero_setup"]:
        return {"allowed": False,
                "reason": "%s erodes zero-setup (%s) — enthusiast-tier "
                "only, never core." % (service, info["form"])}
    return {"allowed": True, "service": service}


def detect_tier(capabilities):
    """Resolve the Gaming tier from a capability set.

    ``capabilities`` is a dict of bools, e.g. {"obs": True,
    "steam": True, "own_steam_key": False}. Missing keys read as
    absent. Returns {"tier", "missing", "fallbacks"}:
    - all of (obs, steam) present -> "enthusiast" (own key is
      optional: its absence degrades only API features);
    - some present -> "degraded", with the ladder per gap;
    - none present -> "core" (the zero-setup core stands alone).
    """
    if not isinstance(capabilities, dict):
        raise ValueError("detect_tier: capabilities must be a dict")
    present = [k for k in ("obs", "steam") if capabilities.get(k)]
    missing = [k for k in ("obs", "steam") if not capabilities.get(k)]
    fallbacks = {}
    for gap in missing:
        fallbacks[gap] = FALLBACKS.get(gap, ["core"])
    if not capabilities.get("own_steam_key"):
        fallbacks.setdefault("own_steam_key",
                             FALLBACKS["own_steam_key"])
    if len(present) == 2:
        tier = "enthusiast"
    elif present:
        tier = "degraded"
    else:
        tier = "core"
    return {"tier": tier, "missing": missing, "fallbacks": fallbacks}


def degradation_ladder(missing_capability):
    """The fallback ladder for one missing capability, in order."""
    return list(FALLBACKS.get(missing_capability, ["core"]))
