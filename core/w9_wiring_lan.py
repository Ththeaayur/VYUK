from __future__ import annotations

from . import state_mirrors as _mirrors  # INTENTIONAL (B31: moved code resolves _mirrors by bare name)
from . import soundscape_v564 as _soundscape  # INTENTIONAL (B31: moved code resolves _soundscape by bare name)

# ------------------------------------------------------------------ #
# 18. State mirrors — LAN-only, terms-gated (no reader configured)
# ------------------------------------------------------------------ #

def build_lan_state_map(specs) -> dict:
    """Build a StateMap from caller-supplied mirror specs.

    Each spec: {"source", "device", "reader", "host"?, "terms_cleared"?}.
    Hue without terms_cleared=True raises HueTermsNotCleared (this
    module never clears it — the terms check is OWED); any spec with
    a non-LAN host raises RemoteHueRejected. Mirrors are read-only
    snapshots; nothing here writes to a device.
    """
    classes = {
        "openrgb": _mirrors.OpenRGBMirror,
        "wled": _mirrors.WLEDMirror,
        "busylight": _mirrors.BusylightMirror,
    }
    built = []
    for spec in specs or []:
        source = spec.get("source", "")
        host = spec.get("host")
        if host is not None and not _mirrors.is_lan_address(host):
            raise _mirrors.RemoteHueRejected(
                f"mirror host {host!r} is not LAN; remote mirrors "
                "are rejected")
        if source == "hue":
            built.append(_mirrors.HueMirror(
                spec.get("device", "hue"),
                host if host is not None else "localhost",
                reader=spec.get("reader"),
                terms_cleared=bool(spec.get("terms_cleared", False))))
        elif source in classes:
            built.append(classes[source](
                spec.get("device", source),
                reader=spec.get("reader")))
        else:
            raise ValueError(f"unknown mirror source: {source!r}")
    state_map = _mirrors.StateMap()
    return state_map.build(built)


def read_home_mirrors(specs=None) -> dict:
    """Product read path for state mirrors (ruled 2026-11-05:
    display-only, device-gated, terms-gated, freshness-stamped).

    Order: PF-18 home_devices hook FIRST — a denied grant
    blinds mirrors before any reader runs, so no device is
    contacted and no device state leaks. Then device gating:
    no specs means nothing is connected (wording matches the
    Permissions page). Then the builder for caller-supplied
    specs (Hue terms + LAN rules enforced by the builder;
    freshness stamped by the mirrors).

    Direct use of build_lan_state_map is library-level;
    product callers (GUI/automation) must read through this
    entry so the hook gate cannot be bypassed. Returns
    {"connected", "blinded", "reason", "snapshots"}; snapshots
    map "source:device" -> {"state", "reachable", "freshness"}.
    """
    from . import permission_grants as _grants  # local: keeps
    # this module importable without the grants store.
    allowed, _pid = _grants.check_home_device()
    if not allowed:
        return {
            "connected": False,
            "blinded": True,
            "reason": ("Home devices are switched off in Settings, "
                       "so mirrors are blinded."),
            "snapshots": {},
        }
    if not specs:
        return {
            "connected": False,
            "blinded": False,
            "reason": ("Nothing connected yet — no home devices "
                       "have been connected."),
            "snapshots": {},
        }
    built = build_lan_state_map(specs)
    return {
        "connected": True,
        "blinded": False,
        "reason": "",
        "snapshots": built,
    }


def lan_mirrors_snapshot() -> dict:
    """Live-callable mirrors entry point with NO device configured.

    Returns {} — there is no configured LAN reader today, and
    inventing one (a fake device, a guessed host) would be
    fabrication. The builder above is the wired path once a
    caller supplies real LAN readers + Hue terms clearance.
    """
    return {}


# ------------------------------------------------------------------ #
# 19. Soundscape bed — generated + normalised, NO playback sink
# ------------------------------------------------------------------ #

def soundscape_bed(seconds: float = 1.0, seed: int = 0) -> dict:
    """A focus-room bed, normalised to the bed target BEFORE any
    playback — except there is no playback sink, so the samples
    are returned, never played. The sink decision is OWED; this
    function invents none (no QSoundEffect, no file write).
    """
    bed = _soundscape.generate_bed(seconds, seed=seed)
    normalised = _soundscape.normalise_to_dbfs(
        bed, _soundscape.BED_TARGET_DBFS)
    return {
        "samples": normalised,
        "sample_rate": _soundscape.SAMPLE_RATE,
        "rms_dbfs": _soundscape.rms_dbfs(normalised),
        "target_dbfs": _soundscape.BED_TARGET_DBFS,
        "playback_sink": None,
        "playback_sink_owed": True,
    }
