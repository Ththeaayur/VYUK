"""Network deltas, Wave 9 Build 6 (NET) — descriptors and
records only, read-only, own-network forms.

Per build_flow_v6.md: "NET (network deltas, read-only
and own-network forms) — BUILD, Waves 4 and 9." The
standing network rules are respected, not worked around:
read-only, own and administered networks only, never
scans others' networks, never an open port, never a
reach outside the supplied network.

WHAT THIS MODULE IS
-------------------
* An own-network descriptor: a caller-supplied network
  label and a scope ("own" or "administered") in, a
  plain-language network record out. The label is a name
  the caller gave it, never probed, never scanned, never
  measured, never resolved.
* A network-status descriptor: caller-supplied status
  words and a timestamp in, a status summary out. The
  status is as supplied, never polled live, never
  measured.
* A read-only check descriptor: a caller-supplied target
  label and scope in, "own-network as supplied",
  "missing", or "outside own network — refused" out.
  It never resolves the target, never connects to it,
  and never reaches outside the supplied network.
* A re-validation rule for recording a network: the
  descriptor is re-built from its own fields and must
  match exactly, or it is refused, never written on
  trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any scan, port scan, probe, ping, connect, discovery,
  sweep, enumeration, resolution, or fetch action — an
  action parameter naming one refuses the whole call;
* any target, network, or scope outside an own or
  administered label — a scope other than "own" or
  "administered", or a label carrying a whole-word
  external marker (cloud, internet, public, external,
  remote, wan, cellular), refuses the whole call, as
  does an address carrying a URL scheme (anything
  with "://"), which is an address, not a label;
* any network, target, or status the caller did not
  supply — an empty or missing field refuses the whole
  call (the read-only check reports "missing" instead,
  because that is its job);
* any claim a network was scanned, probed, reached,
  measured, connected, or discovered — a status word
  claiming so refuses the whole call, and the module's
  own outputs always carry scanned False, probed
  False, reached False, measured False, and connected
  False.
The module imports no socket or network-connect code —
proven by the AST test in tests/test_net_w9.py. It
opens no connection, resolves no address, listens on no
port, and authenticates to nothing: there is nothing
here to reach a network with, by construction.
Descriptors only, records only.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused action, an external scope or label, a missing
field, or a reach claim refuses the whole call. Nothing
is probed, resolved, relabelled, or quietly connected:
a network the caller did not name as their own is
worse than no network record.

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
    "MAX_LABEL_CHARS",
    "MAX_STATUS_CHARS",
    "MAX_TIMESTAMP_CHARS",
    "SCAN_ACTIONS",
    "EXTERNAL_NETWORK_WORDS",
    "REACH_CLAIM_TOKENS",
    "OWN_SCOPES",
    "build_network_descriptor",
    "build_network_status",
    "check_readonly_target",
    "validate_recordable_network",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different network record.
MAX_LABEL_CHARS = 200
MAX_STATUS_CHARS = 300
MAX_TIMESTAMP_CHARS = 100

#: Action words that refuse a call whole. "describe" is
#: the only action this module performs, and it is the
#: default; anything else names work this module must
#: not do.
SCAN_ACTIONS = frozenset({
    "scan", "port-scan", "portscan", "probe", "ping",
    "connect", "discover", "discovery", "sweep",
    "enumerate", "resolve", "fetch", "download",
    "upload", "listen", "bind", "open-port",
})

#: Whole-word markers that make a network label external
#: rather than own/administered. A label carrying one
#: refuses the whole call (or, in the read-only check,
#: reports "outside own network — refused").
EXTERNAL_NETWORK_WORDS = frozenset({
    "cloud", "internet", "public", "external", "remote",
    "wan", "cellular",
})

#: Status words claiming a live reach. A status carrying
#: one refuses the whole call: this module never scans,
#: probes, reaches, measures, connects, or discovers.
REACH_CLAIM_TOKENS = frozenset({
    "scanned", "probed", "reached", "measured",
    "connected", "discovered", "pinged",
})

#: The only scopes this module accepts. Anything else is
#: outside the caller's own network.
OWN_SCOPES = frozenset({"own", "administered"})


def _tokens_in(text):
    """Lower-cased, word-ish tokens of a free-text input,
    for refusal scanning."""
    lowered = str(text).lower()
    current = []
    for char in lowered:
        if char.isalpha() or char == "-":
            current.append(char)
        else:
            if current:
                yield "".join(current)
                current = []
    if current:
        yield "".join(current)


def _check_label(name, value, maximum):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} chars")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    if "://" in stripped:
        raise ValueError(
            f"{name} carries a URL scheme: an address is not "
            f"a label, and this module resolves nothing")
    for token in _tokens_in(stripped):
        if token in EXTERNAL_NETWORK_WORDS:
            raise ValueError(
                f"{name} carries external marker {token!r}: "
                f"only own or administered networks are "
                f"described here")
    return stripped


def _check_scope(scope):
    if not isinstance(scope, str):
        raise ValueError("scope must be a string")
    cleaned = scope.strip().lower()
    if cleaned not in OWN_SCOPES:
        raise ValueError(
            f"scope {scope!r} is outside the caller's own "
            f"network: only 'own' or 'administered' are "
            f"accepted")
    return cleaned


def _check_action(action):
    if action is None:
        return "describe"
    if not isinstance(action, str):
        raise ValueError("action must be a string")
    cleaned = action.strip().lower()
    if cleaned == "describe":
        return cleaned
    if cleaned in SCAN_ACTIONS:
        raise ValueError(
            f"network action {cleaned!r} refused: this "
            f"module never scans, probes, connects, or "
            f"discovers anything")
    raise ValueError(
        f"action {cleaned!r} refused: only 'describe' is "
        f"performed here")


def _check_status_words(status_words):
    if not isinstance(status_words, str):
        raise ValueError("status_words must be a string")
    if len(status_words) > MAX_STATUS_CHARS:
        raise ValueError(
            f"status_words exceeds {MAX_STATUS_CHARS} chars")
    stripped = status_words.strip()
    if not stripped:
        raise ValueError("status_words must not be empty")
    for token in _tokens_in(stripped):
        if token in REACH_CLAIM_TOKENS \
                or any(token.startswith(claim)
                       for claim in REACH_CLAIM_TOKENS):
            raise ValueError(
                f"status claims a live reach ({token!r}): "
                f"this module never scans, probes, reaches, "
                f"measures, connects, or discovers anything")
    return stripped


def build_network_descriptor(network_label, scope, action="describe"):
    """An own-network descriptor from caller-supplied
    fields only.

    The label is a name the caller gave the network,
    never probed, never scanned, never measured, never
    resolved. The scope must be exactly "own" or
    "administered". Returns the descriptor as data, with
    every reach flag False and a note saying so."""
    _check_action(action)
    label = _check_label("network_label", network_label,
                         MAX_LABEL_CHARS)
    cleaned_scope = _check_scope(scope)
    return {
        "kind": "NETWORK_DESCRIPTOR",
        "network_label": label,
        "scope": cleaned_scope,
        "probed": False,
        "scanned": False,
        "reached": False,
        "measured": False,
        "connected": False,
        "note": ("A network descriptor only, from the "
                 "label and scope you supplied. Nothing "
                 "was probed, scanned, reached, measured, "
                 "or connected — this module opens no "
                 "connection and resolves nothing."),
    }


def build_network_status(status_words, timestamp, action="describe"):
    """A network-status descriptor from caller-supplied
    words and timestamp only.

    The status is as supplied, never polled live, never
    measured. A status word claiming a live reach
    refuses the whole call."""
    _check_action(action)
    status = _check_status_words(status_words)
    when = _check_label("timestamp", timestamp,
                        MAX_TIMESTAMP_CHARS)
    return {
        "kind": "NETWORK_STATUS",
        "status": status,
        "timestamp": when,
        "polled": False,
        "measured": False,
        "scanned": False,
        "connected": False,
        "note": ("A status summary only, from the words "
                 "and timestamp you supplied. Nothing was "
                 "polled live and nothing was measured."),
    }


def check_readonly_target(target_label, scope, network_label=None,
                          action="describe"):
    """A read-only check descriptor for a caller-supplied
    target label.

    Returns "own-network as supplied" when the target and
    an own/administered scope are supplied, "missing"
    when the target or the scope is missing, and
    "outside own network — refused" when the scope is
    outside the caller's own network, the network label
    carries an external marker, or the target carries a
    URL scheme. It never resolves the target, never
    connects to it, and never reaches outside the
    supplied network. A scan or connect action refuses
    the whole call."""
    _check_action(action)
    base = {
        "kind": "READONLY_CHECK",
        "probed": False,
        "scanned": False,
        "reached": False,
        "measured": False,
        "connected": False,
        "resolved": False,
        "note": ("A read-only check only. Nothing was "
                 "resolved, probed, scanned, reached, or "
                 "connected."),
    }
    if target_label is None \
            or not isinstance(target_label, str) \
            or not target_label.strip():
        return dict(base, target_label=None, result="missing",
                    reason="target_label was not supplied")
    target = target_label.strip()
    if len(target) > MAX_LABEL_CHARS:
        return dict(base, target_label=target,
                    result="outside own network — refused",
                    reason="target_label exceeds the label "
                           "length bound")
    if "://" in target:
        return dict(base, target_label=target,
                    result="outside own network — refused",
                    reason="target_label carries a URL scheme: "
                           "an address is not a label, and "
                           "this module resolves nothing")
    if scope is None or not isinstance(scope, str) \
            or not scope.strip():
        return dict(base, target_label=target, result="missing",
                    reason="scope was not supplied")
    cleaned_scope = scope.strip().lower()
    if cleaned_scope not in OWN_SCOPES:
        return dict(base, target_label=target,
                    result="outside own network — refused",
                    reason=f"scope {scope!r} is outside the "
                           f"caller's own network")
    if network_label is not None:
        if not isinstance(network_label, str) \
                or not network_label.strip():
            return dict(base, target_label=target,
                        result="missing",
                        reason="network_label was supplied "
                               "but empty")
        for token in _tokens_in(network_label):
            if token in EXTERNAL_NETWORK_WORDS:
                return dict(base, target_label=target,
                            result="outside own network — "
                                   "refused",
                            reason=f"network_label carries "
                                   f"external marker {token!r}")
        base["network_label"] = network_label.strip()
    return dict(base, target_label=target, scope=cleaned_scope,
                result="own-network as supplied")


def validate_recordable_network(kind, fields):
    """Re-validate a network a caller wants recorded, by
    re-running the builder that made it and requiring the
    core fields to match exactly. A record that does not
    re-build is refused, never written on trust. Returns
    the rebuilt descriptor dict, or raises ValueError."""
    if not isinstance(kind, str) or not isinstance(fields, dict):
        raise ValueError("kind must be a string and fields an object")
    kind = kind.strip()
    allowed_fields = {
        "descriptor": {"network_label", "scope"},
        "status": {"status_words", "timestamp"},
    }
    if kind not in allowed_fields:
        raise ValueError(f"unknown recordable network kind {kind!r}")
    unexpected = sorted(set(fields) - allowed_fields[kind])
    if unexpected:
        raise ValueError(
            f"network carries unexpected fields {unexpected}: "
            f"a record that does not re-build exactly is "
            f"refused, never written on trust")
    if kind == "descriptor":
        rebuilt = build_network_descriptor(
            fields.get("network_label"), fields.get("scope"))
        if fields.get("network_label", "").strip() \
                != rebuilt["network_label"] \
                or fields.get("scope", "").strip().lower() \
                != rebuilt["scope"]:
            raise ValueError("network descriptor fields do "
                             "not re-build")
        return rebuilt
    if kind == "status":
        rebuilt = build_network_status(
            fields.get("status_words"), fields.get("timestamp"))
        if fields.get("status_words", "").strip() \
                != rebuilt["status"] \
                or fields.get("timestamp", "").strip() \
                != rebuilt["timestamp"]:
            raise ValueError("network status fields do not "
                             "re-build")
        return rebuilt
    raise ValueError(f"unknown recordable network kind {kind!r}")
