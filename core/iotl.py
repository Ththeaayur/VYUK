"""Local IoT deltas, Wave 9 Build 3 (IOT-L) — descriptors
and records only. Nothing here ever reaches, polls, or
controls a device.

Per build_flow_v6.md: "IOT-L (local IoT deltas) —
BUILD, Wave 9, local and administered networks only."
The standing hardware rule is respected, not worked
around: any item that later moves hardware needs Tier 2
plus a physical stop independent of software — so this
build moves nothing. Actuation is not a deferred feature
here; it is refused in code.

WHAT THIS MODULE IS
-------------------
* A local-device descriptor: caller-supplied device
  name, local address label, and administered-network
  label in, a plain-language device record out. The
  address is a caller-supplied label, never probed,
  never resolved, never used to reach anything.
* A device-state descriptor: caller-supplied state
  words and timestamp in, a state summary out. The
  state is as supplied, never polled, never measured.
* A local-network check descriptor: a caller-supplied
  network label in, "local-administered as supplied"
  or "missing" out. It never scans anything and never
  reaches outside the supplied network.
* A re-validation rule for recording a device: the
  descriptor is re-built from its own fields and must
  match exactly, or it is refused, never written on
  trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any actuate / turn-on / turn-off / set-state /
  control command to a device — an action parameter
  naming one refuses the whole call;
* any network scan, discovery, probe, or ping — an
  action parameter naming one refuses the whole call;
* any cloud or external device or address outside a
  local/administered label — a network label carrying
  a whole-word external marker (cloud, internet,
  public, external, remote, wan, cellular) refuses
  the whole call, as does an address label carrying a
  URL scheme (anything with "://");
* any device, address, network, or state the caller
  did not supply — an empty or missing field refuses
  the whole call;
* any claim a device was reached, polled, or
  controlled — a reached / polled / controlled flag
  set true refuses the whole call, as does a status
  word claiming so.
The module imports no socket or network code — proven
by the AST test in tests/test_iotl_w9.py. It opens no
connection, resolves no address, and authenticates to
nothing: there is nothing here to reach a device with,
by construction. Descriptors only, records only.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused action, a scan, an external label, a missing
field, or a reached/polled/controlled claim refuses
the whole call. Nothing is probed, resolved, or quietly
relabelled: a device the caller did not describe, on a
network the caller did not name as local, is worse
than no device record.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency, no
telemetry write, no database access, no network access.
Nothing here reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

__all__ = [
    "MAX_NAME_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_STATE_CHARS",
    "MAX_TIMESTAMP_CHARS",
    "ACTUATE_ACTIONS",
    "SCAN_ACTIONS",
    "EXTERNAL_NETWORK_WORDS",
    "build_device_descriptor",
    "build_device_state",
    "check_local_network",
    "validate_recordable_device",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different device record.
MAX_NAME_CHARS = 200
MAX_LABEL_CHARS = 200
MAX_STATE_CHARS = 200
MAX_TIMESTAMP_CHARS = 100

#: Action words that refuse a call whole. "describe" is
#: the only action this module performs, and it is the
#: default; anything else names work this module must
#: not do.
ACTUATE_ACTIONS = frozenset({
    "actuate", "turn-on", "turn-off", "switch-on",
    "switch-off", "toggle", "set", "set-state",
    "control", "open", "close", "lock", "unlock",
    "start", "stop", "restart", "reboot", "dim",
    "brighten", "power-on", "power-off",
})

#: Scan / discovery words that refuse a call whole.
SCAN_ACTIONS = frozenset({
    "scan", "discover", "probe", "ping", "sweep",
    "enumerate",
})

#: Whole-word markers that make a network label
#: external rather than local/administered. A label
#: carrying one refuses whole: this build is local and
#: administered networks only.
EXTERNAL_NETWORK_WORDS = frozenset({
    "cloud", "internet", "public", "external",
    "remote", "wan", "cellular",
})


def _check_action(action):
    if action is None:
        return
    if not isinstance(action, str):
        raise ValueError("action must be a string naming the action")
    word = action.strip().lower()
    if word in ("", "describe"):
        return
    if word in ACTUATE_ACTIONS:
        raise ValueError(
            f"refused — action {word!r} is a device control "
            f"action: this module describes only and never "
            f"actuates, turns on or off, or sets a device state")
    if word in SCAN_ACTIONS:
        raise ValueError(
            f"refused — action {word!r} is a network scan or "
            f"discovery action: this module never scans, "
            f"discovers, probes, or pings anything")
    raise ValueError(
        f"refused — unknown action {word!r}: this module "
        f"performs only the describe action")


def _check_text_field(name, value, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty: the caller "
                         f"must supply it, it is never invented")
    if len(stripped) > maximum:
        raise ValueError(f"{name} exceeds {maximum} chars")
    return stripped


def _words(text):
    word = []
    for char in text.lower():
        if char.isalnum():
            word.append(char)
        elif word:
            yield "".join(word)
            word = []
    if word:
        yield "".join(word)


def _check_network_label(label):
    for word in _words(label):
        if word in EXTERNAL_NETWORK_WORDS:
            raise ValueError(
                f"refused — network label carries external "
                f"marker {word!r}: this build is local and "
                f"administered networks only, never cloud or "
                f"external networks")


def _check_address_label(label):
    if "://" in label:
        raise ValueError(
            "refused — address label carries a URL scheme: "
            "an address here is a caller-supplied local "
            "label, never a URL to reach")


def _check_no_reach_claim(reached=False, polled=False, controlled=False):
    for name, flag in (("reached", reached), ("polled", polled),
                       ("controlled", controlled)):
        if flag:
            raise ValueError(
                f"refused — {name} claim: this module never "
                f"reaches, polls, or controls a device")


def build_device_descriptor(device_name, address_label, network_label,
                            action=None):
    """A local-device descriptor from caller-supplied
    fields only.

    The device name, address label, and network label
    are the caller's own words. The address is a label,
    never probed or resolved; the network must be local
    or administered, never cloud or external. The record
    is returned with reached=False and controlled=False,
    and the note says plainly that the device was not
    reached and not controlled. Any actuation or scan
    action, external label, URL-scheme address, missing
    field, or reach claim refuses the whole call."""
    _check_action(action)
    name = _check_text_field("device_name", device_name, MAX_NAME_CHARS)
    address = _check_text_field("address_label", address_label,
                                MAX_LABEL_CHARS)
    network = _check_text_field("network_label", network_label,
                                MAX_LABEL_CHARS)
    _check_network_label(network)
    _check_address_label(address)
    return {
        "kind": "LOCAL_DEVICE_DESCRIPTOR",
        "device_name": name,
        "address_label": address,
        "network_label": network,
        "reached": False,
        "controlled": False,
        "note": ("A descriptor only — this device was NOT "
                 "reached and NOT controlled. The address "
                 "is a caller-supplied local label, never "
                 "probed or resolved, and the network is "
                 "local/administered as supplied."),
    }


def build_device_state(device_name, state, timestamp, action=None,
                       reached=False, polled=False, controlled=False):
    """A device-state descriptor from caller-supplied
    words only.

    The state is the caller's own words and the
    timestamp is caller-supplied; neither is polled or
    measured here. The summary is returned with
    polled=False and measured=False, and the note says
    plainly that nothing was polled or measured. Any
    actuation or scan action, missing field, or
    reached/polled/controlled claim refuses the whole
    call."""
    _check_action(action)
    _check_no_reach_claim(reached, polled, controlled)
    name = _check_text_field("device_name", device_name, MAX_NAME_CHARS)
    words = _check_text_field("state", state, MAX_STATE_CHARS)
    when = _check_text_field("timestamp", timestamp, MAX_TIMESTAMP_CHARS)
    return {
        "kind": "LOCAL_DEVICE_STATE",
        "device_name": name,
        "state": words,
        "timestamp": when,
        "polled": False,
        "measured": False,
        "note": ("A state summary only, as supplied by the "
                 "caller — nothing was polled and nothing "
                 "was measured. The device was not reached."),
    }


def check_local_network(network_label):
    """A local-network check on a caller-supplied label
    only.

    The result is "local-administered as supplied" when
    the label is present and carries no external marker,
    and "missing" when it is absent or empty, naming
    what is missing. It never scans anything and never
    reaches outside the supplied network. An external
    label refuses whole, as a cloud/external network is
    outside this build's scope."""
    if network_label is None or (isinstance(network_label, str)
                                 and not network_label.strip()):
        return {
            "kind": "LOCAL_NETWORK_CHECK",
            "status": "missing",
            "missing": ["network_label"],
            "note": ("No network label was supplied. Nothing "
                     "was scanned to fill the gap."),
        }
    if not isinstance(network_label, str):
        raise ValueError("network_label must be a string")
    label = network_label.strip()
    _check_network_label(label)
    return {
        "kind": "LOCAL_NETWORK_CHECK",
        "status": "local-administered as supplied",
        "network_label": label,
        "note": ("Local/administered network, as supplied "
                 "by the caller — nothing was scanned and "
                 "nothing outside this network was reached."),
    }


def validate_recordable_device(fields):
    """Re-validate a device a caller wants recorded, by
    re-running the builder that made it and requiring the
    fields to match exactly. A device that does not
    re-build is refused, never written on trust. Returns
    the rebuilt descriptor dict, or raises ValueError."""
    if not isinstance(fields, dict):
        raise ValueError("device fields must be an object")
    allowed = {"device_name", "address_label", "network_label"}
    unexpected = sorted(set(fields) - allowed)
    if unexpected:
        raise ValueError(
            f"device carries unexpected fields {unexpected}: a "
            f"device that does not re-build exactly is refused, "
            f"never written on trust")
    rebuilt = build_device_descriptor(
        fields.get("device_name"), fields.get("address_label"),
        fields.get("network_label"))
    for name in allowed:
        if fields.get(name, "").strip() != rebuilt[name]:
            raise ValueError(f"device field {name} does not re-build")
    return rebuilt
