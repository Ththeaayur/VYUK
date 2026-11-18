"""Wave 10 Build 1: P23 release spine — multiple install
routes, verifiable releases (STRUCTURE ONLY), and a
release train with gates, as descriptors and records
only.

Per build_flow_v6.md Wave 10 and §13 Part 23
(P23-1–P23-27, as received 2026-10-26 ~03:46 IST),
this build covers the release spine ONLY:

* P23 multiple install routes (P23-2): caller-supplied
  route labels in, a plain-language route record out.
  Each route states what it does and does not do.
  Nothing is compiled, nothing is installed, and no
  store listing is claimed to exist.
* P23 verifiable releases (P23-3): caller-supplied
  manifest summary plus SHA-256 as supplied in, a
  structure-check record out. It checks the SHAPE
  only — version supplied, manifest present, hash
  present and well-formed, data baseline stated. It
  never verifies a signature, never claims a release
  is signed or authentic, and never re-verifies
  single-sourcing from files: single-sourcing is
  asserted by the caller-supplied summary, and the
  ship-gate, not this descriptor, proves it from
  files.
* P23 release train with gates (P23-4):
  caller-supplied stage gates in order in, a train
  record out. Each gate's verdict is stated as
  supplied. This descriptor runs no gate and passes
  no gate, ever.

WHAT THIS MODULE IS NOT — NOTHING RELEASED
-------------------------------------------
* NOTHING IS SIGNED. Signing is DEFERRED-SPEND, so
  builds stay unsigned with SmartScreen warnings
  disclosed. Any signing, signed, signature, or
  authentic-release claim refuses whole.
* NOTHING IS COMPILED, PUBLISHED, OR FED. The
  installer is authored, never compiled; the update
  feed has no host (no_feed stands). Any compiled,
  published, shipped, installed-by-this-build, or
  live-feed claim refuses whole.
* NO TRANSACTION SURFACE. Clear tiers, trial and
  refunds, payments and tax MoR, and the honest legal
  pack are GATED on ruling 34 (OPEN) plus
  qualified-professional confirmation. Any payment,
  tier, trial, refund, subscription, or pricing
  content refuses whole here — it is not decided
  here.
* NOT A LEGAL PACK. The batch's own caveat is
  carried on every record: "This is a checklist, not
  legal or tax advice. Confirm licences, privacy law,
  payments and tax with a qualified professional in
  the markets you sell to."
* NOT COMMERCIALLY COMPLETE. The Wave 10 exit
  wording is exact and is carried on every record:
  "shippable to strangers except the transaction
  surface, feed hosting, and signing" — never
  "commercially complete".
* NOT A RE-CLAIM OF SHIPPED WORK. The signed
  manifest plus SHA-256 per ship, the CC-1 licence
  report, the ship-gate battery, and the trust and
  doctor modules already shipped are NOT counted as
  this build's delivery. This build adds the
  release-spine descriptors; it does not re-claim
  shipped work.

FAIL-CLOSED, WHOLE-CALL
-----------------------
Any refused part refuses the whole call: an action
other than "describe"; an unknown descriptor kind;
an unexpected field for the kind; a claim phrase
(signing, signed, signature, authentic, compiled,
published, shipped, installed-by-this-build,
live-feed, purchase, payment, tier, trial, refund,
subscription, pricing) anywhere in a caller-supplied
field; an empty or malformed required field; a
route, gate, or record that would claim a release
was made, signed, compiled, published, installed,
or updated by this build.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (``re``). No new
dependency, no signing library, no installer tool,
no network access, no feed access. Nothing here
reads or writes the install tree's ``data/``
directory.
"""

from __future__ import annotations

import re

__all__ = [
    "WAVE10_EXIT",
    "CAVEAT",
    "describe_route",
    "describe_verify",
    "describe_train",
    "describe",
    "validate_recordable_release",
]

#: The Wave 10 exit wording, exact, carried on every
#: record. Never "commercially complete".
WAVE10_EXIT = ("shippable to strangers except the "
                "transaction surface, feed hosting, "
                "and signing")

#: The batch's own caveat, carried on every record.
CAVEAT = ("This is a checklist, not legal or tax "
          "advice. Confirm licences, privacy law, "
          "payments and tax with a qualified "
          "professional in the markets you sell to.")

#: Claim phrases a caller-supplied field must never
#: carry. A field carrying one is a claim this build
#: refuses to make, not a description — refused whole.
#: "store" alone is NOT in this list: a route label
#: such as "store listing" is a label, not a claim;
#: only a claim that a listing EXISTS is refused.
_CLAIM_PHRASES = (
    "signed", "signing", "signature", "signatures",
    "authentic", "authenticated",
    "compiled", "compiling", "compile",
    "published", "publishing", "publish",
    "shipped", "has shipped", "already shipped",
    "was installed", "has been installed",
    "already installed", "installed by",
    "was updated", "has been updated",
    "live feed", "feed hosted", "update feed",
    "store listing exists", "listed on the store",
    "available on the store",
    "purchase", "purchasing", "payment", "payments",
    "paid tier", "tier", "trial", "refund",
    "subscription", "pricing", "price",
)

#: A well-formed SHA-256: exactly 64 hex characters.
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _refuse(reason):
    return {"ok": False, "reason": reason}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-]*", text.lower()))


def _check_text(text, field):
    """Refuse whole on a claim phrase anywhere in one
    free-text field. Single-word phrases match on whole
    words only — "frontier" must not trip on "tier" —
    and multi-word phrases match as phrases. Outputs
    of this module are never scanned — only
    caller-supplied inputs are."""
    if not isinstance(text, str):
        return f"{field} must be a string"
    lowered = text.lower()
    words = _words(text)
    for phrase in _CLAIM_PHRASES:
        if " " in phrase:
            if phrase in lowered:
                return (f"{field} carries a refused claim: "
                        f"{phrase}")
        elif phrase in words:
            return (f"{field} carries a refused claim: "
                    f"{phrase}")
    return None


def _check_action(action):
    if action != "describe":
        return f"only 'describe' is performed, not {action!r}"
    return None


def _check_fields(fields, allowed, kind):
    if set(fields) - set(allowed):
        return (f"{kind} descriptor carries unexpected fields: "
                f"{sorted(set(fields) - set(allowed))}")
    return None


def describe_route(routes=None, action="describe"):
    """Install-route descriptor: caller-supplied route
    labels in, a plain-language route record out. Each
    route states what it does and does not do. Nothing
    is compiled, nothing is installed, and no store
    listing is claimed to exist. routes defaults to
    None so a missing argument is refused by the
    validation below ("routes must be a non-empty
    list"), never raised as a TypeError (round-2
    N1 BUG 2 — describe_verify already defaults
    its fields this way)."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(routes, list) or not routes:
        return _refuse("routes must be a non-empty list")
    entries = []
    for label in routes:
        if not isinstance(label, str) or not label.strip():
            return _refuse(
                "route labels must be non-empty strings")
        err = _check_text(label, "route label")
        if err:
            return _refuse(err)
        entries.append({
            "label": label.strip(),
            "described_only": True,
            "installer_compiled": False,
            "store_listing_exists": False,
            "installed_by_this_build": False,
            "note": ("Described only: no installer was "
                     "compiled, no store listing is "
                     "claimed to exist, and nothing was "
                     "installed by this build."),
        })
    return {
        "ok": True,
        "kind": "INSTALL_ROUTES",
        "routes": entries,
        "released": False,
        "signed": False,
        "compiled": False,
        "published": False,
        "exit": WAVE10_EXIT,
        "caveat": CAVEAT,
        "note": ("Install routes as supplied by the "
                 "caller, described only. This record "
                 "is not a release, not a signature, "
                 "not a compiled installer, and not a "
                 "store listing."),
    }


def describe_verify(version="", manifest_present=False,
                    sha256="", data_baseline_bytes=None,
                    action="describe"):
    """Release-verify descriptor: caller-supplied
    manifest summary plus SHA-256 as supplied in, a
    structure-check record out. It checks the SHAPE
    only and never verifies a signature, never claims
    a release is signed or authentic, and never
    re-verifies single-sourcing from files."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(version, str) or not version.strip():
        return _refuse("version must be a non-empty string")
    err = _check_text(version, "version")
    if err:
        return _refuse(err)
    if not isinstance(manifest_present, bool):
        return _refuse("manifest_present must be a boolean")
    if sha256 is None:
        sha256 = ""
    if not isinstance(sha256, str):
        return _refuse("sha256 must be a string")
    if sha256:
        err = _check_text(sha256, "sha256")
        if err:
            return _refuse(err)
    if data_baseline_bytes is not None and (
            isinstance(data_baseline_bytes, bool)
            or not isinstance(data_baseline_bytes, int)
            or data_baseline_bytes < 0):
        return _refuse(
            "data_baseline_bytes must be a non-negative "
            "whole number of bytes, or omitted")
    hash_present = bool(sha256.strip())
    hash_well_formed = bool(_SHA256_RE.match(sha256.strip())) \
        if hash_present else False
    checks = {
        "version_supplied": True,
        "manifest_present": bool(manifest_present),
        "hash_present": hash_present,
        "hash_well_formed": hash_well_formed,
        "data_baseline_stated": data_baseline_bytes is not None,
    }
    failed = [name for name, passed in checks.items()
              if not passed]
    return {
        "ok": True,
        "kind": "RELEASE_STRUCTURE_CHECK",
        "version": version.strip(),
        "manifest_present": bool(manifest_present),
        "sha256_present": hash_present,
        "sha256_well_formed": hash_well_formed,
        "data_baseline_bytes": data_baseline_bytes,
        "checks": checks,
        "failed_checks": failed,
        "shape_ok": not failed,
        "signature_verified": False,
        "signed": False,
        "authentic": False,
        "released": False,
        "exit": WAVE10_EXIT,
        "caveat": CAVEAT,
        "note": ("A structure check only: version "
                 "supplied, manifest present, hash "
                 "present and well-formed, data "
                 "baseline stated. A shape that passes "
                 "is still not a signature, not an "
                 "authenticity verdict, and not a "
                 "release — single-sourcing is asserted "
                 "by the caller-supplied summary, and "
                 "the ship-gate, not this descriptor, "
                 "proves it from files."),
    }


def describe_train(gates=None, action="describe"):
    """Release-train descriptor: caller-supplied stage
    gates in order in, a train record out. Each gate's
    verdict is stated as supplied. This descriptor runs
    no gate and passes no gate, ever. gates defaults
    to None so a missing argument is refused by the
    validation below ("gates must be a non-empty
    list"), never raised as a TypeError (round-2
    N1 BUG 2 — same shape as describe_route)."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if not isinstance(gates, list) or not gates:
        return _refuse("gates must be a non-empty list")
    entries = []
    for gate in gates:
        if not isinstance(gate, dict):
            return _refuse("each gate must be an object")
        if set(gate) - {"name", "verdict"}:
            return _refuse(
                "each gate carries only name and verdict")
        name = gate.get("name")
        verdict = gate.get("verdict")
        if not isinstance(name, str) or not name.strip():
            return _refuse(
                "gate names must be non-empty strings")
        if not isinstance(verdict, str) or not verdict.strip():
            return _refuse(
                "gate verdicts must be non-empty strings")
        err = _check_text(name, "gate name")
        if err:
            return _refuse(err)
        err = _check_text(verdict, "gate verdict")
        if err:
            return _refuse(err)
        entries.append({
            "name": name.strip(),
            "verdict_as_supplied": verdict.strip(),
        })
    return {
        "ok": True,
        "kind": "RELEASE_TRAIN",
        "gates": entries,
        "train_verdict": "AS_SUPPLIED",
        "gates_run_by_this_descriptor": False,
        "gates_passed_by_this_descriptor": False,
        "released": False,
        "signed": False,
        "published": False,
        "exit": WAVE10_EXIT,
        "caveat": CAVEAT,
        "note": ("A release train as supplied by the "
                 "caller, in order. Verdicts are as "
                 "supplied, never run and never passed "
                 "by this descriptor. This record is "
                 "not a release."),
    }


_KIND_FIELDS = {
    "route": ("routes",),
    "verify": ("version", "manifest_present", "sha256",
               "data_baseline_bytes"),
    "train": ("gates",),
}


def describe(kind, action="describe", **fields):
    """Dispatch to one descriptor kind. Unknown kinds
    and unexpected fields for the kind refuse whole —
    a descriptor never silently ignores an input it
    was handed."""
    err = _check_action(action)
    if err:
        return _refuse(err)
    if kind not in _KIND_FIELDS:
        return _refuse(
            "kind must be one of route, verify, train")
    err = _check_fields(fields, _KIND_FIELDS[kind], kind)
    if err:
        return _refuse(err)
    if kind == "route":
        return describe_route(action=action, **fields)
    if kind == "verify":
        return describe_verify(action=action, **fields)
    return describe_train(action=action, **fields)


def validate_recordable_release(record_kind, fields):
    """Re-run the descriptor builder for a release
    record and return the rebuilt descriptor. A record
    is never written on trust: a caller-supplied JSON
    could claim anything, so the descriptor is rebuilt
    from its own fields and the rebuilt form is what
    gets recorded. A verify descriptor whose shape
    fails is still recordable — the failed checks are
    the record — but a refused descriptor is not."""
    if record_kind not in _KIND_FIELDS:
        raise ValueError(
            "record_kind must be one of route, verify, train")
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    allowed = set(_KIND_FIELDS[record_kind])
    if set(fields) - allowed:
        raise ValueError("record carries unexpected fields")
    rebuilt = describe(record_kind, **fields)
    if not rebuilt.get("ok"):
        raise ValueError(rebuilt.get("reason", "rebuild refused"))
    return rebuilt
