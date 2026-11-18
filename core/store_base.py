"""The shared store base (GAPS-14, flaw #4).

Before this module, every tool that kept a ledger re-implemented
its own hash chain: 21 per-tool copies in 6 incompatible
encodings (see ``gap_shared_store.py``), two genesis values,
and no single verifier that could check any of them. A fix to
the chain logic had to land in 21 places, and a chain written
by one tool could not be verified by any other tool's code.

This module is the one place the chain logic lives:

* ONE canonical chain: ``GENESIS`` (``"0" * 64``, the same
  constant ``core/audit_chain.py`` publishes), ``canonical_hash``
  (SHA-256 over canonical JSON — the exact computation
  ``audit_chain._entry_hash`` performs), and ``verify_entries``
  (the single verify loop: link check + hash recompute +
  first-break report). New stores use the canonical encoding.
* ONE adapter per legacy encoding family (6 of them, all pure
  functions, registered in ``ADAPTERS``). A legacy store keeps
  its encoding — its stored bytes never change — but the code
  that computes and checks its hashes is now this module's,
  not a 21st copy of the loop. A fix to the verify loop lands
  here, once.
* ONE SQLite connection discipline (``connect``): the plain
  ``sqlite3.connect`` call every store makes today, with the
  caller's timeout / row_factory / PRAGMA settings preserved
  exactly. No behaviour change, one place to add discipline.
* ONE "verify everything" operation (``verify_all``): given the
  tree's ledgers as (name, entries, adapter, genesis), it
  verifies each through the same loop and reports per ledger.

RULES
-----
* A legacy store's bytes NEVER change because of this module:
  its adapter reproduces its existing encoding exactly, so
  every stored hash still verifies. New rows are written in
  the store's own encoding (the adapter), not silently
  re-encoded — re-encoding history would invalidate every
  stored hash, which is the one thing a chain must never do.
* An unknown adapter name is a ``KeyError`` — fail-closed,
  never a silent fallback to another encoding.
* ``verify_entries`` reports the FIRST break (index + reason),
  never a bare boolean without the location; callers wrap the
  result into their own return shape, which is their contract,
  not the chain's.
* Verified-on-read means: any read can be verified through
  this module (``verify_entries`` / ``verify_all``). The base
  does not force verification into every read path — a caller
  that reads a broken chain for display still can; the chain
  is checked where the tool checks it, through this loop.

Stdlib only. No new dependency.
"""

import hashlib
import json
import os
import sqlite3

# The canonical genesis: the prev_hash of the very first entry.
# Published here on purpose — the same constant
# core/audit_chain.py publishes as GENESIS_PREV_HASH, and any
# verifier (including a user's own script) needs it to check
# the first link.
GENESIS = "0" * 64

# The legacy genesis: the literal string 10 of the 21 per-tool
# stores use as their first prev_hash. It is NOT the canonical
# genesis — the two values are incompatible, which is exactly
# why the legacy families need adapters instead of a rewrite.
LEGACY_GENESIS = "GENESIS"


def canonical_json(obj) -> str:
    """Canonical JSON: sorted keys, compact separators, no ASCII
    escaping. The exact encoding audit_chain._canonical uses,
    so a record written through this module is byte-identical
    to one written through audit_chain."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def canonical_hash(seq, event_id, ts, kind, data, prev_hash) -> str:
    """The canonical chain hash: SHA-256 over canonical JSON of
    (seq, id, ts, kind, data, prev_hash). The exact computation
    audit_chain._entry_hash performs — a digest computed here
    equals a digest computed there, byte for byte."""
    payload = {"seq": seq, "id": event_id, "ts": ts, "kind": kind,
               "data": data, "prev_hash": prev_hash}
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# Adapters: one pure function per encoding family.
#
# Every adapter takes (prev_hash, fields) and returns the row's
# digest. ``fields`` is a dict the caller builds; each adapter
# pulls exactly the keys its encoding needs. The same adapter
# serves the append side (fields built from the live values)
# and the verify side (fields built from the stored row) — one
# function, two call sites, no drift between them.
# ------------------------------------------------------------------

def _adapter_canonical(prev_hash, fields):
    return canonical_hash(fields["seq"], fields["id"], fields["ts"],
                          fields["kind"], fields["data"], prev_hash)


def _adapter_json_body_ts(prev_hash, fields):
    body = json.dumps({"ts": fields["ts"], "kind": fields["kind"],
                       "payload": fields["payload"],
                       "prev": prev_hash}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _adapter_json_body_no_ts(prev_hash, fields):
    body = json.dumps({"kind": fields["kind"],
                       "payload": fields["payload"],
                       "prev": prev_hash}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _adapter_prev_payload_list(prev_hash, fields):
    payload = json.dumps(fields["fields"], sort_keys=True)
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def _adapter_prev_payload_dict(prev_hash, fields):
    # ``body_json`` is the STORED string — already
    # json.dumps(body, sort_keys=True) — exactly what the old
    # verify hashed. The adapter takes the string, never a
    # parsed body: a corrupt (non-JSON) payload must read as a
    # hash mismatch, not raise (the old verify never parsed it).
    payload = fields["body_json"]
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def _adapter_pipe_repr(prev_hash, fields):
    return hashlib.sha256(
        ("%s|%d|%r|%s|%s" % (prev_hash, fields["seq"], fields["ts"],
                             fields["kind"],
                             fields["payload_json"])).encode(
                                 "utf-8")).hexdigest()


def _adapter_pipe_fstring(prev_hash, fields):
    return hashlib.sha256(
        f"{prev_hash}|{fields['kind']}|{fields['detail']}|"
        f"{fields['at']}".encode("utf-8")).hexdigest()


# name -> adapter function. The name is the store's encoding
# family; a store names its adapter once, at its call sites.
ADAPTERS = {
    "canonical": _adapter_canonical,
    "json_body_ts": _adapter_json_body_ts,
    "json_body_no_ts": _adapter_json_body_no_ts,
    "prev_payload_list": _adapter_prev_payload_list,
    "prev_payload_dict": _adapter_prev_payload_dict,
    "pipe_repr": _adapter_pipe_repr,
    "pipe_fstring": _adapter_pipe_fstring,
}

# Which stores use which adapter (kept here so a reader can see
# the whole map in one place; the stores themselves name their
# adapter at their call sites, this table is documentation that
# the prover checks against the code).
STORE_ADAPTERS = {
    # canonical (the audit chain itself)
    "audit_chain": "canonical",
    # json_body_ts — genesis "0"*64, body {ts, kind, payload, prev}
    "technical_analysis": "json_body_ts",
    "time_tracker": "json_body_ts",
    "optimise": "json_body_ts",
    "erp": "json_body_ts",
    "invoice": "json_body_ts",
    "translations": "json_body_ts",
    "filament": "json_body_ts",
    "deals": "json_body_ts",
    "vehicle_routes": "json_body_ts",
    # json_body_no_ts — genesis "0"*64, body {kind, payload, prev}
    "media_chat": "json_body_no_ts",
    "audio_localise": "json_body_no_ts",
    # prev_payload_list — genesis "GENESIS", sha256(prev + json(list))
    "billing": "prev_payload_list",
    "mcp_registry": "prev_payload_list",
    "agent_call": "prev_payload_list",
    "prompt_tune": "prev_payload_list",
    # prev_payload_dict — genesis "GENESIS", sha256(prev + json(dict))
    "lodging": "prev_payload_dict",
    "stage_lighting": "prev_payload_dict",
    "wardrobe": "prev_payload_dict",
    # pipe_repr — genesis "GENESIS", "%s|%d|%r|%s|%s" % (...)
    "voice_agent": "pipe_repr",
    "event_ticketing": "pipe_repr",
    # pipe_fstring — genesis "GENESIS", f"{prev}|{kind}|{detail}|{at}"
    "bookings": "pipe_fstring",
}

# The genesis each adapter family uses (the adapter itself does
# not need it — the digest is computed from the given prev_hash —
# but verify_entries needs to know what the FIRST row's
# prev_hash must be).
ADAPTER_GENESIS = {
    "canonical": GENESIS,
    "json_body_ts": GENESIS,
    "json_body_no_ts": GENESIS,
    "prev_payload_list": LEGACY_GENESIS,
    "prev_payload_dict": LEGACY_GENESIS,
    "pipe_repr": LEGACY_GENESIS,
    "pipe_fstring": LEGACY_GENESIS,
}


def chain_digest(adapter, prev_hash, fields) -> str:
    """Compute one row's digest through the named adapter.
    Unknown adapter -> KeyError (fail-closed, never a silent
    fallback to another encoding)."""
    fn = ADAPTERS[adapter]  # KeyError on unknown — deliberate
    return fn(prev_hash, fields)


# ------------------------------------------------------------------
# The "never in assistant.db" guard (GAPS-27, M-V3).
#
# The rule — a tool store never lives in the assistant memory
# store (``data/assistant.db``) — used to be enforced by
# copy-pasted ``os.path.basename(...) == "assistant.db"``
# checks in eleven places (creative_rights_ledger,
# developer_diagnostics, trust_slices, dv_serial_v577,
# developer_codebase_qa, six resilience/* modules). The check
# itself now lives here, once: call sites keep their own
# failure shape (raise / refusal dict / refusal return) but
# the definition of "is this assistant.db" is this function,
# not a pasted literal. The check is by basename on purpose:
# any path, anywhere, whose final component is assistant.db
# is refused — while "myassistant.db.backup" is not (its
# basename is not assistant.db).
# ------------------------------------------------------------------

ASSISTANT_DB_FILENAME = "assistant.db"


def is_assistant_db(path) -> bool:
    """True when `path` names the assistant memory store itself."""
    return os.path.basename(str(path)) == ASSISTANT_DB_FILENAME


def refuse_assistant_db(path, *, what="store"):
    """Return ``str(path)`` unless it is assistant.db, else raise
    ValueError. Call sites that raise keep raising through this
    helper; call sites that answer with a refusal dict call
    ``is_assistant_db`` directly and shape their own answer."""
    if is_assistant_db(path):
        raise ValueError(f"the {what} never lives in assistant.db")
    return str(path)


class StoreBase:
    """Shared store behaviour (GAPS-27, M-V6).

    The one behaviour every per-tool SQLite store used to
    copy-paste: ``_missing()`` — true when the store file does
    not exist yet, so reads answer empty WITHOUT creating the
    file (the R18-7 eager-creation rule: a read must never
    create a store file). A store class inherits this instead
    of defining its own ``_missing``; the mixin carries no
    ``__init__`` and no state, so it composes with any store
    that sets ``self.path`` to a ``pathlib.Path``.
    """

    def _missing(self):
        """True when the store file does not exist yet — reads then
        answer empty WITHOUT creating the file (the R18-7 eager-
        creation rule)."""
        return not self.path.exists()


# ------------------------------------------------------------------
# Per-tool events tables (GAPS-27, M-V4) — the metadata the
# unified activity read (core/activity_feed) needs to find every
# store's ledger without knowing each store's schema by heart.
# One entry per store that keeps a hash-chained events table
# (the 21 adapters above minus audit_chain, which IS the chain).
#
# Shape: store -> (filename, table, ts_col, ts_kind, kind_col,
#                  text_kind, text_cols)
#   ts_kind "epoch": ts_col is REAL seconds since the epoch.
#   ts_kind "iso":   ts_col is TEXT ISO ("YYYY-MM-DD HH:MM:SS"
#                    for wardrobe's SQLite datetime('now')).
#   kind_col None:   the row has no kind column — the kind lives
#                    inside the JSON payload (wardrobe).
#   text_kind "json": text_cols[0] is a JSON payload column.
#   text_kind "plain": text_cols are plain-text columns, joined.
# Verified against the live schemas by the GAPS-27 gap script.
# ------------------------------------------------------------------

STORE_EVENTS = {
    "technical_analysis": ("technical_analysis.sqlite3", "events",
                           "ts", "epoch", "kind", "json", ("payload",)),
    "time_tracker": ("time_tracker.sqlite3", "events",
                     "ts", "epoch", "kind", "json", ("payload",)),
    "optimise": ("optimise.sqlite3", "events",
                 "ts", "epoch", "kind", "json", ("payload",)),
    "erp": ("erp.sqlite3", "events",
            "ts", "epoch", "kind", "json", ("payload",)),
    "invoice": ("invoice.sqlite3", "events",
                "ts", "epoch", "kind", "json", ("payload",)),
    "translations": ("translations.sqlite3", "events",
                     "ts", "epoch", "kind", "json", ("payload",)),
    "filament": ("filament.sqlite3", "events",
                 "ts", "epoch", "kind", "json", ("payload",)),
    "deals": ("deals.sqlite3", "events",
              "ts", "epoch", "kind", "json", ("payload",)),
    "vehicle_routes": ("vehicle_routes.sqlite3", "events",
                       "ts", "epoch", "kind", "json", ("payload",)),
    "lodging": ("lodging.sqlite3", "events",
                "ts", "epoch", "kind", "json", ("body_json",)),
    "stage_lighting": ("stage_lighting.sqlite3", "events",
                       "ts", "epoch", "kind", "json", ("body_json",)),
    "event_ticketing": ("event_tickets.sqlite3", "events",
                        "ts", "epoch", "kind", "json", ("payload_json",)),
    "voice_agent": ("voice_agent.sqlite3", "events",
                    "ts", "epoch", "kind", "json", ("payload_json",)),
    "mcp_registry": ("mcp_registry.sqlite3", "mcp_registry_events",
                     "ts", "epoch", "kind", "plain",
                     ("subject", "detail")),
    "billing": ("billing.sqlite3", "billing_events",
                "ts", "epoch", "kind", "plain", ("object", "detail")),
    "agent_call": ("agent_call.sqlite3", "agent_call_events",
                   "ts", "epoch", "kind", "plain",
                   ("agent", "object", "detail")),
    "prompt_tune": ("prompt_tune.sqlite3", "prompt_tune_events",
                    "ts", "epoch", "kind", "plain", ("object", "detail")),
    "audio_localise": ("audio_localise.sqlite3", "events",
                       "created", "epoch", "kind", "json", ("payload",)),
    "media_chat": ("media_chat.sqlite3", "events",
                   "created", "epoch", "kind", "json", ("payload",)),
    "bookings": ("bookings.sqlite3", "events",
                 "at_utc", "iso", "kind", "plain", ("detail",)),
    "wardrobe": ("wardrobe.sqlite3", "events",
                 "created", "iso", None, "json", ("payload",)),
}


def verify_entries(entries, adapter, genesis) -> dict:
    """The single verify loop. ``entries`` is an iterable of
    dicts in chain order, each with ``prev_hash`` and ``hash``
    keys plus the adapter's fields. Returns::

        {ok, checked, first_break}

    ``first_break`` is None when intact, else
    {index, reason} naming the FIRST bad link: a recomputed
    hash that does not match (``"hash mismatch"``), or a
    prev_hash that does not equal the previous entry's hash
    (``"link break"``). Callers wrap this into their own
    return shape — the shape is their contract, the loop is
    the base's."""
    fn = ADAPTERS[adapter]  # KeyError on unknown — deliberate
    checked = 0
    expected_prev = genesis
    for i, entry in enumerate(entries):
        checked += 1
        prev_hash = entry["prev_hash"]
        if prev_hash != expected_prev:
            return {"ok": False, "checked": checked,
                    "first_break": {"index": i,
                                    "reason": "link break: prev_hash "
                                    "!= previous hash"}}
        try:
            recomputed = fn(prev_hash, entry)
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "checked": checked,
                    "first_break": {"index": i,
                                    "reason": "malformed entry"}}
        if entry["hash"] != recomputed:
            return {"ok": False, "checked": checked,
                    "first_break": {"index": i,
                                    "reason": "hash mismatch"}}
        expected_prev = entry["hash"]
    return {"ok": True, "checked": checked, "first_break": None}


def connect(path, timeout=5.0, check_same_thread=True,
            row_factory=None, pragmas=()) -> sqlite3.Connection:
    """The single SQLite connection discipline: exactly the
    ``sqlite3.connect`` call every store makes today (default
    timeout 5.0 — the sqlite3 default — so no store waits
    longer or shorter than it did), with the caller's
    row_factory and PRAGMA statements applied in order when
    given. A store that sets WAL, foreign_keys, or Row keeps
    setting them, through this call, in one place."""
    conn = sqlite3.connect(str(path), timeout=timeout,
                           check_same_thread=check_same_thread)
    if row_factory is not None:
        conn.row_factory = row_factory
    for pragma in pragmas:
        conn.execute(pragma)
    return conn


def verify_all(stores) -> dict:
    """Verify every ledger in the tree through the same loop.
    ``stores`` is an iterable of (name, entries, adapter,
    genesis). Returns {name: verify_entries result}. One call
    verifies all ledgers — the operation that did not exist
    before this module, because no two ledgers shared a
    verifier."""
    return {name: verify_entries(entries, adapter, genesis)
            for name, entries, adapter, genesis in stores}
