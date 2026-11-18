"""Wave 9 (v5.64) — state mirrors (PILOTs).

Mirrors of external device state, for display only. The safety
property is DIRECTION, and it is structural, not a promise in
a docstring:

* State flows OUT of devices and INTO the mirror. Never the
  reverse. This module contains NO method that sends a command,
  sets a value, toggles a device, or otherwise drives anything
  — there is deliberately no set/control/write path to audit.
  A reader that wants to drive a device cannot do it through
  this module; it would have to go elsewhere, visibly.
* Each snapshot carries ``freshness`` — wall-clock seconds
  (time.time()) taken when the read was made, so a display
  can show how stale a reading is. Mirrors always stamp it.
* Each mirror reads through an injected ``reader`` callable
  supplied by the caller. The module itself performs no network
  I/O and bundles no vendor SDK: OpenRGB is client-only (the
  SDK is never bundled), WLED and busylights are read via the
  caller's reader, and Hue speaks local CLIP only.
* The shared state map is built ONCE per registry: a second
  build raises, so two competing maps can never disagree about
  what the devices reported.

HUE GATE: the Hue mirror is GATED behind a flag defaulting
OFF that requires explicit terms clearance — the Hue
Developer Terms check is OWED and NOT cleared by this module.
Do not use Hue until cleared: constructing a Hue mirror
without ``terms_cleared=True`` raises, and remote mode is
explicitly NOT implemented (a non-LAN address is rejected,
not routed).

PILOTs, not live: mirrors are built and readable, and the
product read path is ``read_home_mirrors`` in w9_wiring_lan
(PF-18 home_devices hook first, device-gated, freshness-
stamped). No GUI surface renders mirror snapshots yet — that
display is follow-up work. Stdlib only. No telemetry.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field

MODULE_VERSION = "5.64.0"

__all__ = [
    "MODULE_VERSION",
    "HUE_TERMS_CLEARED_DEFAULT",
    "HueTermsNotCleared",
    "RemoteHueRejected",
    "StateMapAlreadyBuilt",
    "DeviceSnapshot",
    "OpenRGBMirror",
    "WLEDMirror",
    "HueMirror",
    "BusylightMirror",
    "StateMap",
    "is_lan_address",
]

# Hue terms clearance: OFF by default, always. The OWED terms
# check is the only thing that may ever flip a real deployment
# to True, explicitly, by the owner.
HUE_TERMS_CLEARED_DEFAULT = False


class HueTermsNotCleared(RuntimeError):
    """Hue mirror requested without explicit terms clearance."""


class RemoteHueRejected(ValueError):
    """Hue remote mode is not implemented; only LAN CLIP."""


class StateMapAlreadyBuilt(RuntimeError):
    """The shared state map is built once; it cannot be rebuilt."""


_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def is_lan_address(host: str) -> bool:
    """True for loopback / RFC1918 addresses only, checked
    against explicit networks — ``is_private`` alone is too
    broad (it also covers documentation ranges like TEST-NET).
    Hostnames other than 'localhost' are NOT assumed LAN: an
    unresolvable name must not become a remote endpoint by
    optimism."""
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _LAN_NETWORKS)


@dataclass(frozen=True)
class DeviceSnapshot:
    """One read of one device's state. ``state`` is opaque
    device data, carried through for display and never
    interpreted as a command. ``freshness`` is wall-clock
    seconds (time.time()) when the read was taken — mirrors
    always stamp it, so every snapshot carries its age."""

    source: str  # "openrgb" | "wled" | "hue" | "busylight"
    device: str
    state: dict
    reachable: bool = True
    freshness: float = field(default_factory=time.time)


class _BaseMirror:
    """A read-only mirror: ``snapshot()`` is the only surface.
    Subclasses supply ``source`` and a reader; none of them
    gains a write path by inheritance because none exists."""

    source: str = "unknown"

    def __init__(self, device: str, reader=None):
        self.device = device
        # A reader with no I/O of its own (a dict-returning
        # callable in tests, a LAN fetch in production) keeps
        # this module free of network code and vendor SDKs.
        self._reader = reader or (lambda: {})

    def snapshot(self) -> DeviceSnapshot:
        try:
            state = self._reader()
        except Exception:  # noqa: BLE001 — an unreadable device is
            # offline, not fatal: the mirror reports unreachable
            return DeviceSnapshot(self.source, self.device, {},
                                  reachable=False)
        if not isinstance(state, dict):
            state = {"value": state}
        return DeviceSnapshot(self.source, self.device, dict(state))


class OpenRGBMirror(_BaseMirror):
    """OpenRGB mirror, client only. The OpenRGB SDK is NEVER
    bundled by this module — the caller supplies the reader,
    and this mirror only ever reads through it."""

    source = "openrgb"


class WLEDMirror(_BaseMirror):
    """WLED mirror, read-only."""

    source = "wled"


class BusylightMirror(_BaseMirror):
    """Busylight (presence-light) mirror, read-only."""

    source = "busylight"


class HueMirror(_BaseMirror):
    """Hue mirror, local CLIP only, gated on terms clearance.

    ``terms_cleared`` defaults to False and the Hue Developer
    Terms check is OWED — until the owner explicitly clears it,
    construction raises. ``host`` must be a LAN address:
    remote mode is explicitly NOT implemented and a remote
    host raises instead of being contacted."""

    source = "hue"

    def __init__(self, device: str, host: str, reader=None,
                 terms_cleared: bool = HUE_TERMS_CLEARED_DEFAULT):
        if not terms_cleared:
            raise HueTermsNotCleared(
                "Hue Developer Terms check is OWED and not cleared; "
                "do not use Hue until cleared explicitly")
        if not is_lan_address(host):
            raise RemoteHueRejected(
                f"Hue remote mode is not implemented; {host!r} is not LAN")
        super().__init__(device, reader)
        self.host = host
        self.terms_cleared = True


@dataclass
class StateMap:
    """The shared state map, built ONCE from mirrors' snapshots.
    ``build()`` freezes the map; a second build raises so no
    caller can quietly replace the shared view. Fresh data
    means a fresh registry, not a mutated map."""

    entries: dict = field(default_factory=dict)
    _built: bool = False

    def build(self, mirrors: list) -> dict:
        if self._built:
            raise StateMapAlreadyBuilt(
                "state map is built once; create a new StateMap "
                "for a fresh view")
        merged: dict = {}
        for mirror in mirrors:
            snap = mirror.snapshot()
            merged[f"{snap.source}:{snap.device}"] = {
                "state": snap.state,
                "reachable": snap.reachable,
                "freshness": snap.freshness,
            }
        self.entries = merged
        self._built = True
        return dict(merged)

    @property
    def built(self) -> bool:
        return self._built
