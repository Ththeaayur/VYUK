"""Wave 8 Build 3 — Phase 5 picks: INTERFACES, ENT shelf, PORT,
PLAT-TOOLS, FIN-PLAN, inside the frozen UI core.

This module adds the Phase 5 DELTAS only. It does not rebuild what
already exists in the tree:

* INTERFACES: the tree already ships a Qt UI (frozen v5.57 design:
  chat docked in a 400 px right panel, Aura default orb). This build
  adds DESCRIPTORS of surfaces — data that names a surface, its
  rows, and its actions — never a redesign, never a renumbering,
  never a new sidebar entry or stack page.
* ENT shelf: a catalogue record for entertainment items the caller
  supplies. No asset is bundled, no audio or video is played or
  generated, and no feel veto is entered (see below).
* PORT: the tree already ships a ZIP portability bundle
  (core/portability.py, settings + memories + milestones).
  This build adds the SINGLE-DEVICE record-set round-trip: export
  a caller-supplied record set to one caller-chosen file, import
  it back, and prove the round-trip byte-for-byte on the canonical
  record JSON. It is not a second device, not a cloud sync, and
  it never runs automatically — the caller invokes it.
* PLAT-TOOLS: the tree already ships lifecycle controls
  (core/lifecycle.py: autostart consent, uninstall choice).
  This build adds the programme records beside them: lifecycle
  state, rollout record, governance note — records, never
  enforcement beyond the existing gates.
* FIN-PLAN: money planning + record-keeping only. The tree already
  ships money guards (core/funnel/money_guard.py: the never-automate
  list). This build adds a plan/record object over amounts the
  caller supplies. It performs NO transaction, makes NO payment,
  connects to NO account, and gives NO advice — any
  transaction-shaped input is refused whole.

WHAT IS REFUSED, WHOLE-CALL, IN EVERY CLUSTER
----------------------------------------------
A refused part refuses the whole call; no partial descriptor,
shelf, bundle, record, or plan is ever returned.

* INTERFACES: a descriptor claiming a new sidebar entry, a new
  page, or any renumbering (any stack_index / page_index /
  sidebar_index field, or the words "new sidebar" / "new page")
  is refused. A face is refused: the whole word "face", or
  "portrait", "selfie", "character voice", or "catchphrase" in
  any surface or row text. Fake data is refused: every row's
  value_kind must be "measured" (with a value present) or
  "unknown" (value "unknown" or absent) — "estimated", "sample",
  "demo", "placeholder", and invented values are refused. Row
  actions are limited to navigate / show / confirm / dismiss /
  read / describe; execute / send / purchase / deploy / promote
  / install / run are refused. A confirm row must carry
  c14_required=true, or the descriptor is refused.
* ENT shelf: an item without a source and a licence label is
  refused. A licence that normalises to copyleft (gpl, agpl,
  lgpl, mpl), non-commercial (-nc), no-derivatives (-nd),
  revenue-capped, or territory-restricted is refused, as is an
  unknown or unrecognised licence label (default-deny, as in the
  OpenAQ filter). Any item mentioning a vetoed feel token
  (Charon, M4, earcon, F6 signature, Space Grotesk) is refused.
* PORT: a record set that is not a JSON list of JSON objects is
  refused; a record carrying a credential-shaped key (.env, key,
  token, password, secret) is refused; the export target checks
  live in the tool coordinator (existing folder, .json, no
  core/data path, no credential target).
* PLAT-TOOLS: a record whose kind is not lifecycle / rollout /
  governance, or whose state is not in that kind's state list,
  is refused. A governance record without a note is refused.
  Records are data: every result carries enforced=False.
* FIN-PLAN: any transaction-shaped input is refused whole — a
  top-level or entry key naming transaction / payment /
  purchase / payee / destination_account / account_number /
  card / bank / upi / wallet, or an entry label containing pay /
  send / transfer / purchase / buy, or any "advice" /
  "recommendation" field. Amounts must be numbers >= 0; currency
  must be a 3-letter code. Totals are computed from
  caller-supplied amounts only and are labelled as
  caller-supplied, never as advice.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only (json, hashlib, re). No new
dependency, no asset bundled, no font, no sound pack, no model,
no feed, no network access, no file write except the PORT export
itself (through the tool coordinator, which owns the destination
checks) and no database access. Nothing here reads or writes the
install tree's data/ directory. The Orbitron OFL sighting and the
orb licence sighting are OWED by Wave 8 Build 4 (feel), not by
this build, and nothing here claims them. (B-R1-7, 2026-11-09:
the Orbitron OFL sighting is LANDED — primary-read, font bundled
with Rajdhani + Share Tech Mono; the orb licence sighting stays
as Wave 8 left it. B-R1-7r, 2026-11-09: the body face is Sora now;
Rajdhani is removed — the stack is Orbitron + Sora + Share Tech
Mono.)

CAPS
----
At most 100 rows / items / records / entries per call, at most
2000 characters per text field, at most 1 MB of canonical record
JSON for a PORT round-trip. Oversize calls are refused whole.
"""

from __future__ import annotations

import hashlib
import json
import re

MAX_ITEMS = 100
MAX_TEXT = 2000
MAX_PORT_BYTES = 1_000_000

PORT_FORMAT = "vyuk-port-1"

_INTERFACE_ACTIONS = frozenset(
    {"navigate", "show", "confirm", "dismiss", "read", "describe"}
)
_REFUSED_ACTIONS = frozenset(
    {"execute", "send", "purchase", "deploy", "promote", "install", "run"}
)
_FACE_WORDS = ("face", "portrait", "selfie", "character voice", "catchphrase")
_VETOED_FEEL = ("charon", "m4", "earcon", "f6", "space grotesk")
_BLOCKED_LICENCE_BITS = (
    "gpl", "agpl", "lgpl", "mpl", "nc", "nd", "non-commercial",
    "noncommercial", "revenue", "territory", "non-derivative",
)
_ALLOWED_LICENCES = frozenset({
    "cc0", "cc0-1.0", "cc-by", "cc-by-4.0", "cc-by-3.0",
    "cc-by-2.5", "cc-by-2.0", "public-domain", "own-work",
    "public domain", "cc by", "cc by 4.0",
})
_ENT_KINDS = frozenset(
    {"music", "audio", "story", "game", "video", "reading"}
)
_PLAT_STATES = {
    "lifecycle": frozenset(
        {"planned", "in-development", "shipped", "deprecated", "retired"}
    ),
    "rollout": frozenset(
        {"staged", "rolling-out", "complete", "rolled-back", "paused"}
    ),
    "governance": frozenset({"noted", "approved", "rejected", "superseded"}),
}
_FIN_ENTRY_KINDS = frozenset(
    {"budget", "saving-goal", "expense-record", "income-record"}
)
_TX_KEYS = frozenset({
    "transaction", "payment", "purchase", "payee",
    "destination_account", "account_number", "card", "bank",
    "upi", "wallet", "advice", "recommendation",
})
_TX_LABEL_WORDS = ("pay", "send", "transfer", "purchase", "buy")
_CREDENTIAL_KEYS = (".env", "key", "token", "password", "secret")


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _check_text(value, field: str):
    if not isinstance(value, str) or not value.strip():
        return f"{field} must be a non-empty string"
    if len(value) > MAX_TEXT:
        return f"{field} exceeds {MAX_TEXT} characters"
    return None


def _norm_licence(label: str) -> str:
    return " ".join(str(label).strip().lower().replace("-", " ").split())


# ------------------------------------------------------------------
# INTERFACES — surface descriptors inside the frozen core
# ------------------------------------------------------------------

def describe_interface(descriptor) -> dict:
    """Validate a surface descriptor; return it normalised, or
    refuse the whole descriptor with the reason named."""
    if not isinstance(descriptor, dict):
        return _fail("descriptor must be a JSON object")
    for banned in ("stack_index", "page_index", "sidebar_index"):
        if banned in descriptor:
            return _fail(
                f"renumbering refused: descriptor carries {banned}; "
                "the frozen UI core is never renumbered by a descriptor"
            )
    err = _check_text(descriptor.get("surface"), "surface")
    if err:
        return _fail(err)
    surface = descriptor["surface"]
    if "new sidebar" in surface.lower() or "new page" in surface.lower():
        return _fail(
            "new surface refused: a descriptor never adds a sidebar "
            "entry or a page; it describes a surface inside the "
            "frozen core"
        )
    rows = descriptor.get("rows")
    if not isinstance(rows, list) or not rows:
        return _fail("rows must be a non-empty list")
    if len(rows) > MAX_ITEMS:
        return _fail(f"rows exceed {MAX_ITEMS}")
    out_rows = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return _fail(f"row {i} must be a JSON object")
        err = _check_text(row.get("label"), f"row {i} label")
        if err:
            return _fail(err)
        action = row.get("action")
        if action in _REFUSED_ACTIONS:
            return _fail(
                f"row {i} action {action!r} refused: a descriptor "
                "never executes, sends, purchases, deploys, or runs"
            )
        if action not in _INTERFACE_ACTIONS:
            return _fail(f"row {i} action {action!r} is not allowed")
        if action == "confirm" and row.get("c14_required") is not True:
            return _fail(
                f"row {i} is a confirm row without c14_required=true; "
                "consequential rows are confirm-gated (C-14)"
            )
        kind = row.get("value_kind")
        if kind not in ("measured", "unknown"):
            return _fail(
                f"row {i} value_kind {kind!r} refused: a HUD row "
                "carries a measured value or an explicit unknown, "
                "never an estimate, sample, or placeholder"
            )
        value = row.get("value")
        if kind == "measured" and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            return _fail(f"row {i} is measured but carries no value")
        if kind == "unknown" and value not in (None, "unknown"):
            return _fail(
                f"row {i} is unknown but carries a value; an unknown "
                "row must say unknown, not invent one"
            )
        text = f"{surface} {row['label']}".lower()
        for word in _FACE_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", text):
                return _fail(
                    f"face refused: row {i} mentions {word!r}; "
                    "abstract orb only, no face ever"
                )
        out_rows.append({
            "label": row["label"],
            "action": action,
            "value_kind": kind,
            "value": value if kind == "measured" else "unknown",
            "c14_required": bool(row.get("c14_required", False)),
        })
    return {
        "ok": True,
        "kind": "INTERFACE_DESCRIPTOR",
        "surface": surface,
        "rows": out_rows,
        "row_count": len(out_rows),
        "redesign": False,
        "renumbered": False,
        "note": "Descriptor only: it describes a surface inside the "
                "frozen UI core; it adds no sidebar entry, no page, "
                "and no renumbering.",
    }


# ------------------------------------------------------------------
# ENT shelf — caller-supplied catalogue records, licence-gated
# ------------------------------------------------------------------

def list_ent_shelf(items) -> dict:
    """Catalogue caller-supplied entertainment items, licence-gated.
    No asset is bundled, played, or generated here."""
    if not isinstance(items, list) or not items:
        return _fail("items must be a non-empty list")
    if len(items) > MAX_ITEMS:
        return _fail(f"items exceed {MAX_ITEMS}")
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return _fail(f"item {i} must be a JSON object")
        for field in ("title", "kind", "source", "licence"):
            err = _check_text(item.get(field), f"item {i} {field}")
            if err:
                return _fail(err)
        if item["kind"] not in _ENT_KINDS:
            return _fail(f"item {i} kind {item['kind']!r} is not allowed")
        blob = f"{item['title']} {item['kind']}".lower()
        for token in _VETOED_FEEL:
            if token in blob:
                return _fail(
                    f"item {i} mentions vetoed feel {token!r}; "
                    "vetoed feel never enters"
                )
        norm = _norm_licence(item["licence"])
        compact = norm.replace(" ", "")
        if any(bit in compact for bit in _BLOCKED_LICENCE_BITS):
            return _fail(
                f"item {i} licence {item['licence']!r} refused: "
                "copyleft, non-commercial, no-derivatives, "
                "revenue-capped, or territory-restricted material "
                "is excluded from the distributed product"
            )
        if norm not in _ALLOWED_LICENCES:
            return _fail(
                f"item {i} licence {item['licence']!r} is unknown: "
                "an unrecognised licence is refused, never assumed"
            )
        out.append({
            "title": item["title"],
            "kind": item["kind"],
            "source": item["source"],
            "licence": item["licence"],
        })
    return {
        "ok": True,
        "kind": "ENT_SHELF",
        "items": out,
        "item_count": len(out),
        "assets_bundled": False,
        "note": "Catalogue records only: no asset is bundled, played, "
                "or generated by this shelf; every item carries its "
                "source and a cleared licence label.",
    }


# ------------------------------------------------------------------
# PORT — single-device record-set round-trip, byte-for-byte
# ------------------------------------------------------------------

def _canonical(records) -> str:
    return json.dumps(records, sort_keys=True, separators=(",", ":"))


def export_port_bundle(records, bundle_text: str | None = None) -> dict:
    """Build the export bundle dict for a caller-supplied record
    set, or verify a bundle dict. No file is touched here; the
    tool coordinator owns the destination checks and the write."""
    if not isinstance(records, list) or not records:
        return _fail("records must be a non-empty list")
    if len(records) > MAX_ITEMS * 2:
        return _fail(f"records exceed {MAX_ITEMS * 2}")
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            return _fail(f"record {i} must be a JSON object")
        for key in record:
            low = str(key).lower()
            if any(bit in low for bit in _CREDENTIAL_KEYS):
                return _fail(
                    f"record {i} key {key!r} refused: credential-shaped "
                    "keys never leave the device in a port bundle"
                )
    try:
        canonical = _canonical(records)
    except (TypeError, ValueError) as exc:
        return _fail(f"records are not JSON-serialisable: {exc}")
    if len(canonical.encode("utf-8")) > MAX_PORT_BYTES:
        return _fail("records exceed 1 MB of canonical JSON")
    bundle = {
        "format": PORT_FORMAT,
        "count": len(records),
        "records": records,
        "manifest": {
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "canonical_bytes": len(canonical.encode("utf-8")),
        },
    }
    return {"ok": True, "kind": "PORT_BUNDLE", "bundle": bundle,
            "canonical": canonical}


def import_port_bundle(bundle) -> dict:
    """Import a bundle dict, verifying the manifest hash BEFORE
    returning anything: a single hash failure returns nothing."""
    if not isinstance(bundle, dict):
        return _fail("bundle must be a JSON object")
    if bundle.get("format") != PORT_FORMAT:
        return _fail(f"bundle format {bundle.get('format')!r} is not "
                     f"{PORT_FORMAT!r}")
    records = bundle.get("records")
    manifest = bundle.get("manifest")
    if not isinstance(records, list) or not isinstance(manifest, dict):
        return _fail("bundle is missing records or manifest")
    if bundle.get("count") != len(records):
        return _fail("bundle count does not match its records")
    try:
        canonical = _canonical(records)
    except (TypeError, ValueError) as exc:
        return _fail(f"bundle records are not JSON-serialisable: {exc}")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != manifest.get("sha256"):
        return _fail("bundle manifest hash mismatch: nothing imported")
    return {"ok": True, "kind": "PORT_IMPORT", "records": records,
            "count": len(records), "sha256": digest}


def port_roundtrip(records) -> dict:
    """Export a caller-supplied record set and import it back,
    proving the round-trip byte-for-byte on the canonical JSON.
    This function touches no file; the tool coordinator writes
    the bundle to the caller-chosen file and re-reads it, so the
    proof covers the file, not just memory."""
    built = export_port_bundle(records)
    if not built["ok"]:
        return built
    imported = import_port_bundle(built["bundle"])
    if not imported["ok"]:
        return imported
    same = _canonical(imported["records"]) == built["canonical"]
    return {
        "ok": True,
        "kind": "PORT_ROUNDTRIP",
        "count": built["bundle"]["count"],
        "sha256": built["bundle"]["manifest"]["sha256"],
        "roundtrip_ok": same,
        "byte_for_byte": same,
        "note": "Single-device round-trip only: the same record set "
                "exported and imported back, byte-for-byte on its "
                "canonical JSON. This is not a second device and "
                "not a cloud sync; the caller invokes it.",
    }


# ------------------------------------------------------------------
# PLAT-TOOLS — programme records, never enforcement
# ------------------------------------------------------------------

def record_plat(record) -> dict:
    """Validate a programme record: lifecycle state, rollout
    record, or governance note. Records only, enforced=False."""
    if not isinstance(record, dict):
        return _fail("record must be a JSON object")
    kind = record.get("kind")
    if kind not in _PLAT_STATES:
        return _fail(
            f"record kind {kind!r} is not lifecycle / rollout / "
            "governance"
        )
    err = _check_text(record.get("name"), "name")
    if err:
        return _fail(err)
    state = record.get("state")
    if state not in _PLAT_STATES[kind]:
        return _fail(
            f"state {state!r} is not a {kind} state "
            f"({sorted(_PLAT_STATES[kind])})"
        )
    note = record.get("note", "")
    if kind == "governance":
        err = _check_text(note, "governance note")
        if err:
            return _fail(err)
    elif note and len(note) > MAX_TEXT:
        return _fail("note exceeds 2000 characters")
    return {
        "ok": True,
        "kind": "PLAT_RECORD",
        "record_kind": kind,
        "name": record["name"],
        "state": state,
        "note": note,
        "enforced": False,
        "note_text": "Record only: this does not enforce anything beyond "
                     "the existing gates; a rollout or lifecycle "
                     "change still goes through its own gate.",
    }


# ------------------------------------------------------------------
# FIN-PLAN — money planning + record-keeping, NO transactions
# ------------------------------------------------------------------

def record_fin_plan(plan) -> dict:
    """Validate a money plan/record over caller-supplied amounts.
    Any transaction-shaped input refuses the whole plan."""
    if not isinstance(plan, dict):
        return _fail("plan must be a JSON object")
    for key in plan:
        if str(key).lower() in _TX_KEYS:
            return _fail(
                f"plan key {key!r} refused: this is a planning and "
                "record-keeping object, never a transaction"
            )
    err = _check_text(plan.get("name"), "name")
    if err:
        return _fail(err)
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        return _fail("entries must be a non-empty list")
    if len(entries) > MAX_ITEMS:
        return _fail(f"entries exceed {MAX_ITEMS}")
    totals: dict[str, float] = {}
    out_entries = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return _fail(f"entry {i} must be a JSON object")
        for key in entry:
            if str(key).lower() in _TX_KEYS:
                return _fail(
                    f"entry {i} key {key!r} refused: transaction-shaped "
                    "input never enters a plan"
                )
        err = _check_text(entry.get("label"), f"entry {i} label")
        if err:
            return _fail(err)
        label_low = entry["label"].lower()
        if any(re.search(rf"\b{re.escape(w)}\b", label_low)
               for w in _TX_LABEL_WORDS):
            return _fail(
                f"entry {i} label {entry['label']!r} is "
                "transaction-shaped; a plan records amounts, "
                "it never pays them"
            )
        if entry.get("kind") not in _FIN_ENTRY_KINDS:
            return _fail(
                f"entry {i} kind {entry.get('kind')!r} is not budget / "
                "saving-goal / expense-record / income-record"
            )
        amount = entry.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return _fail(f"entry {i} amount must be a number")
        if amount < 0:
            return _fail(f"entry {i} amount must be >= 0")
        currency = entry.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(
                r"[A-Za-z]{3}", currency or ""):
            return _fail(f"entry {i} currency must be a 3-letter code")
        currency = currency.upper()
        totals[currency] = totals.get(currency, 0.0) + float(amount)
        out_entries.append({
            "label": entry["label"],
            "kind": entry["kind"],
            "amount": amount,
            "currency": currency,
        })
    return {
        "ok": True,
        "kind": "FIN_PLAN",
        "name": plan["name"],
        "entries": out_entries,
        "totals": totals,
        "transaction": False,
        "payment": False,
        "advice": False,
        "note": "Planning and record-keeping only, over caller-supplied "
                "amounts: no transaction is performed, no payment "
                "is made, no account is connected, and no advice "
                "is given.",
    }
