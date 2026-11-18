"""CR-6.9 rights ledger (Creative track, wave 29) — original VYUK code.

One record per asset: source, licensor, licence name + snapshot/URL +
snapshot date, attribution required?, standalone-sale allowed?, territory,
expiry/review date, permitted uses, generator + plan-tier-at-generation +
download provenance (CR-6.11's lesson: a free-tier generator asset is not
assumed commercial).

PF-3 BOUNDARY (binding): this module NEVER deletes anything on its own.
Recording an expiry/review date and warning on it at pre-export check is
CR-6.9 (built). Automatic per-row TTL enforcement, cascading deletion,
and delete-everywhere are PF-3 (NOT built — PF-3 is the owner's open
decision). There is deliberately no delete/remove/purge function here;
later wiring that needs deletion must go through the PF-3 decision, not
around this module.

Stores rule (INV-8): the ledger lives in its OWN file, never
``assistant.db`` — enforced by a guard on every open.

Export guard matrix (``check_export``; purpose defaults to "commercial"):
- licence unverified (empty/unknown)            -> BLOCK
- licence class NC and purpose == commercial    -> BLOCK
- standalone-sale prohibited + standalone export -> BLOCK
- expiry/review date in the past                -> WARN (re-verify first)
- attribution required but none recorded        -> WARN
- otherwise                                     -> ALLOW

Stdlib only (sqlite3). No Wave 6 schema mutation.
"""

import os
import sqlite3

from . import store_base

LEDGER_FILENAME_GUARD = "assistant.db"

# Licence classes derived from the licence name. Kept deliberately small:
# the ledger records what the licensor SAID, it does not adjudicate law.
COMMERCIAL_LICENCES = {
    "cc-by-4.0", "cc-by-3.0", "ofl-1.1", "apache-2.0", "mit",
    "cc0-1.0", "public-domain", "proprietary-commercial",
}


def guard_path(db_path):
    """Refuse assistant.db: the ledger always lives in its own file.

    Thin back-compat wrapper — the check itself lives in
    store_base (GAPS-27, M-V3); the ValueError message is unchanged."""
    return store_base.refuse_assistant_db(db_path, what="rights ledger")


def classify_licence(licence_name):
    """Return a coarse licence class from the recorded licence name.

    Returns one of: "commercial", "non-commercial", "no-derivatives",
    "share-alike", "unverified". "unverified" covers empty, unknown,
    and unrecognised names — the ledger never upgrades an unknown
    licence to a permissive one.
    """
    name = (licence_name or "").strip().lower()
    if not name or name in ("unknown", "unverified", "none"):
        return "unverified"
    if "non-commercial" in name or "noncommercial" in name:
        return "non-commercial"
    if "no-derivatives" in name or "no derivatives" in name:
        return "no-derivatives"
    if "share-alike" in name or "sharealike" in name:
        return "share-alike"
    parts = name.replace("_", "-").split("-")
    if "nc" in parts:
        return "non-commercial"
    if "nd" in parts:
        return "no-derivatives"
    if "sa" in parts:
        return "share-alike"
    if name in COMMERCIAL_LICENCES:
        return "commercial"
    return "unverified"


def _connect(db_path):
    path = guard_path(db_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS assets ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  source TEXT NOT NULL,"
        "  licensor TEXT NOT NULL,"
        "  licence_name TEXT NOT NULL,"
        "  licence_url TEXT,"
        "  snapshot_date TEXT,"
        "  attribution_required INTEGER NOT NULL DEFAULT 0,"
        "  attribution_text TEXT,"
        "  standalone_sale_allowed INTEGER NOT NULL DEFAULT 0,"
        "  territory TEXT,"
        "  expiry_date TEXT,"
        "  permitted_uses TEXT,"
        "  generator TEXT,"
        "  plan_tier TEXT,"
        "  download_provenance TEXT"
        ")"
    )
    conn.commit()
    return conn


def add_asset(db_path, source, licensor, licence_name, licence_url=None,
              snapshot_date=None, attribution_required=False,
              attribution_text=None, standalone_sale_allowed=False,
              territory=None, expiry_date=None, permitted_uses=None,
              generator=None, plan_tier=None, download_provenance=None):
    """Write one ledger record. Returns the record id.

    Looperman rule (CR-6.4): a Looperman-sourced asset WITHOUT a saved
    per-loop licence text defaults to non-commercial in the ledger —
    callers must pass licence_name explicitly to record anything else,
    and ``record_looperman_asset`` enforces the default.
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO assets (source, licensor, licence_name,"
            " licence_url, snapshot_date, attribution_required,"
            " attribution_text, standalone_sale_allowed, territory,"
            " expiry_date, permitted_uses, generator, plan_tier,"
            " download_provenance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, licensor, licence_name, licence_url, snapshot_date,
             1 if attribution_required else 0, attribution_text,
             1 if standalone_sale_allowed else 0, territory, expiry_date,
             permitted_uses, generator, plan_tier, download_provenance),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def record_generated_asset(db_path, source, generator, plan_tier,
                           licence_name=None, **kwargs):
    """Generator-rule record (CR-6.11): a free-tier generated asset
    defaults to non-commercial unless a licence is explicitly
    recorded. A paid-tier asset with no licence recorded stays
    unverified (BLOCK at export) — the ledger never upgrades an
    unknown licence. No Suno integration exists and no Suno terms
    are quoted here (direct read owed)."""
    kwargs.setdefault("generator", generator)
    kwargs.setdefault("plan_tier", plan_tier)
    if licence_name:
        kwargs.setdefault("licence_name", licence_name)
    elif str(plan_tier).lower() in ("free", "free-tier"):
        kwargs.setdefault("licence_name",
                          "Generated asset: free-tier default"
                          " non-commercial")
        kwargs.setdefault("download_provenance",
                          "Generator %s on a free tier without an"
                          " explicitly recorded licence: defaulted to"
                          " non-commercial" % generator)
    else:
        kwargs.setdefault("licence_name", "unverified")
    return add_asset(db_path, source, generator, **kwargs)


def record_looperman_asset(db_path, source, licensor,
                           per_loop_licence_text=None, **kwargs):
    """Looperman-sourced asset: no saved per-loop text -> non-commercial.

    The per-loop lottery is the risk: without the loop's own licence
    text saved, the ledger defaults the asset to non-commercial and
    records that the default applied. A caller holding the text passes
    it in ``per_loop_licence_text`` and the recorded licence reflects it.
    """
    if per_loop_licence_text:
        kwargs.setdefault("licence_name", "Looperman per-loop licence")
        kwargs.setdefault("download_provenance",
                          "Looperman loop with per-loop licence text saved")
    else:
        kwargs.setdefault("licence_name", "Looperman default non-commercial")
        kwargs.setdefault("download_provenance",
                          "Looperman loop WITHOUT saved per-loop text:"
                          " defaulted to non-commercial")
    return add_asset(db_path, source, licensor, **kwargs)


def get_asset(db_path, asset_id):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM assets WHERE id = ?",
                           (asset_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_assets(db_path):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in
                conn.execute("SELECT * FROM assets ORDER BY id")]
    finally:
        conn.close()


def update_asset(db_path, asset_id, **fields):
    """Update record fields (corrections, re-verification). Not deletion:
    updating a record is a correction; removing one is PF-3's job."""
    allowed = {"source", "licensor", "licence_name", "licence_url",
               "snapshot_date", "attribution_required", "attribution_text",
               "standalone_sale_allowed", "territory", "expiry_date",
               "permitted_uses", "generator", "plan_tier",
               "download_provenance"}
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError("unknown ledger field: %s" % key)
        sets.append("%s = ?" % key)
        values.append(value)
    if not sets:
        return False
    values.append(asset_id)
    conn = _connect(db_path)
    try:
        cur = conn.execute("UPDATE assets SET %s WHERE id = ?"
                           % ", ".join(sets), values)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def check_export(db_path, asset_id, purpose="commercial",
                 export_kind="composite", today=None):
    """Pre-export guard. Returns a verdict dict; never deletes anything.

    ``today`` is an ISO date string ("YYYY-MM-DD") for tests; defaults
    to the real current date. ``export_kind`` is "standalone" (the asset
    sold/shipped on its own) or "composite" (inside a larger work).
    """
    import datetime
    asset = get_asset(db_path, asset_id)
    if asset is None:
        return {"verdict": "BLOCK", "reason": "asset not in ledger"}
    licence_class = classify_licence(asset["licence_name"])
    if licence_class == "unverified":
        return {"verdict": "BLOCK",
                "reason": "licence unverified: %s"
                          % (asset["licence_name"] or "(empty)",)}
    if licence_class == "non-commercial" and purpose == "commercial":
        return {"verdict": "BLOCK",
                "reason": "non-commercial licence in a commercial export"}
    if licence_class == "no-derivatives" and export_kind == "composite":
        return {"verdict": "BLOCK",
                "reason": "no-derivatives licence in a composite export"}
    if (not asset["standalone_sale_allowed"]
            and export_kind == "standalone"):
        return {"verdict": "BLOCK",
                "reason": "standalone sale not allowed for this asset"}
    if asset["expiry_date"]:
        now = today or datetime.date.today().isoformat()
        if asset["expiry_date"] < now:
            return {"verdict": "WARN",
                    "reason": "licence expiry/review date passed"
                             " (%s) — re-verify before export"
                             % asset["expiry_date"]}
    if asset["attribution_required"] and not asset["attribution_text"]:
        return {"verdict": "WARN",
                "reason": "attribution required but no attribution"
                          " text recorded"}
    return {"verdict": "ALLOW", "reason": "ledger clear for export"}
