"""Wardrobe / virtual closet for the owner's own clothes — own code.

Parent-ruled (licence, 2026-11-12): tandpfun/wardrobe
(MIT, "MIT License / Copyright (c) 2026 Open Wardrobe contributors";
the LICENSE file from the default branch main was read in full
2026-11-12) is INSPIRATION ONLY. No code, schema, SQL, text or
package is adopted from it; this module is written from scratch in
stdlib Python over the tree's own SQLite-store shape (core/bookings.py,
core/lodging.py) and the tree's own config DATA_DIR. Ideas are not
copyrighted, so no MIT duty is triggered by a from-scratch rebuild.
The "wardrobe" name here is the generic word for the capability, and
no package, fork, service or hosted offering is named after the
original.

What this is: the owner photographs their own clothes and keeps a
catalogue of garments (category, colour, seasons, warmth, formality,
photo), builds outfits from the garments they own, gets a daily
outfit suggestion from the weather they supply plus wear history,
schedules an outfit for a date, and records what was worn. That is
the wardrobe half the original adds on top of a photo library — and
the photo-library half is NOT rebuilt here: core/photo_organise.py
(R9-8) already sorts a camera dump into dated folders.

What is NOT built, stated plainly:
- AI outfit visualisations (the original's "100 new outfit
  visualisations"). They are generated images from a cloud model
  (gpt-image, which needs an OpenAI API key). No deployed-use-
  permitted image-generation model has been verified for this tree,
  and a cloud model is not a build — so the visualisation half is
  STOP, not deferred. Outfits here are lists of owned garments, not
  generated pictures.
- Garment extraction / segmentation from photos. No torch-free,
  licence-verified segmentation model exists in the tree, so a photo
  does not become garments by itself: the owner adds each garment
  (name, category, colour, seasons, warmth, formality, photo path)
  and the photo is a path to an existing file — recorded, never
  copied, never opened, never sent anywhere (the home_inventory
  photo precedent). A background-removal or auto-tag step would need
  its own verified model and is not claimed here.
- No camera-roll scan: nothing reads the owner's photo library on
  its own. Every garment enters through an explicit add.
- No weather fetch: the suggestion takes the day's temperature and
  condition from the caller (the buyer knows today's weather, or
  another tool supplies it). There is no weather tool in the tree
  to read from, and inventing a fetch would be a new network
  surface, not a wardrobe feature.
- No sharing, no public gallery, no hosted anything, no multi-user
  accounts. A wardrobe is the owner's own clothes.

The store: its own SQLite file (wardrobe.sqlite3) in DATA_DIR, in
the lodging/bookings shape — single-writer serialisation, every
write re-read before it is claimed (A1-store), hash-chained events
(sha256(prev + payload)) with full-chain verification. One outfit
per date (UNIQUE date on the schedule table); one wear record per
outfit per date (UNIQUE outfit + date). An outfit refuses a second
garment in the same single slot (top, bottom, dress, shoes) and
refuses a dress together with a top or a bottom (a dress IS the
top+bottom); outerwear and accessories may repeat (layering).
An incomplete outfit still builds — it warns about the missing
slot instead of claiming to be complete. A garment in an outfit
cannot be removed; an outfit scheduled for today or later cannot
be removed — the caller unschedules first. Nothing here moves
money and nothing is sent anywhere.
"""

import json
import os
import sqlite3
from datetime import date, timedelta
from datetime import date as _date_cls  # the handler's `date` parameter shadows the class; use this alias inside it
from pathlib import Path

from .config import DATA_DIR  # noqa: F401  (kept for the house import shape; the store below uses it)
from . import store_base

DEFAULT_DB_FILENAME = "wardrobe.sqlite3"

CATEGORIES = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
SEASONS = ("spring", "summer", "autumn", "winter")
SINGLE_SLOTS = ("top", "bottom", "dress", "shoes")
# Warmth bands by temperature (Celsius): (low, high, warmth).
_WARMTH_BANDS = (
    (28, 99, 1),
    (22, 27, 2),
    (15, 21, 3),
    (8, 14, 4),
    (-99, 7, 5),
)


def _season_of(day):
    """Meteorological season for a date."""
    month = day.month
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _warmth_for(temp_c):
    for low, high, warmth in _WARMTH_BANDS:
        if low <= temp_c <= high:
            return warmth
    return 3


def _parse_date(text, name):
    """ISO 'YYYY-MM-DD' or '' (empty means 'not given')."""
    text = (text or "").strip()
    if not text:
        return None, None
    try:
        return date.fromisoformat(text), None
    except ValueError:
        return None, f"{name} must be YYYY-MM-DD, got {text!r}"


def _num(value, name, low, high, default=None):
    if value is None or value == "":
        if default is None:
            return None, f"{name} is required"
        return default, None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a whole number, got {value!r}"
    if not low <= number <= high:
        return None, f"{name} must be {low}..{high}, got {number}"
    return number, None


def _parse_seasons(text):
    """'summer;autumn' -> sorted list; '' means all seasons."""
    text = (text or "").strip()
    if not text:
        return [], None
    parts = [p.strip().lower() for p in text.replace(",", ";").split(";") if p.strip()]
    bad = [p for p in parts if p not in SEASONS]
    if bad:
        return None, f"unknown season(s): {', '.join(bad)} (use spring/summer/autumn/winter)"
    return sorted(set(parts)), None


def _parse_ids(text):
    """'1;2;3' -> [1, 2, 3]; '' means none."""
    text = (text or "").strip()
    if not text:
        return [], None
    ids = []
    for part in text.replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            return None, f"garment ids must be whole numbers, got {part!r}"
    return ids, None


class _WardrobeDB:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = DATA_DIR / DEFAULT_DB_FILENAME
        self.db_path = Path(db_path)
        self._conn = None

    def _connect(self):
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = store_base.connect(
                str(self.db_path), row_factory=sqlite3.Row,
                pragmas=("PRAGMA foreign_keys = ON",))
            self._init()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS garments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                colour TEXT NOT NULL DEFAULT '',
                seasons TEXT NOT NULL DEFAULT '',
                warmth INTEGER NOT NULL DEFAULT 3,
                formality INTEGER NOT NULL DEFAULT 3,
                photo TEXT NOT NULL DEFAULT '',
                last_worn TEXT,
                created TEXT NOT NULL DEFAULT (date('now'))
            );
            CREATE TABLE IF NOT EXISTS outfits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                garment_ids TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL DEFAULT (date('now'))
            );
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outfit_id INTEGER NOT NULL REFERENCES outfits(id),
                date TEXT NOT NULL UNIQUE,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS wear_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outfit_id INTEGER NOT NULL REFERENCES outfits(id),
                date TEXT NOT NULL,
                UNIQUE(outfit_id, date)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prev_hash TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        self._conn.commit()

    # -- events (hash-chained, lodging/bookings shape) ----------------

    def _event(self, payload):
        conn = self._connect()
        row = conn.execute(
            "SELECT payload_hash FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev = row["payload_hash"] if row else "GENESIS"
        text = json.dumps(payload, sort_keys=True)
        digest = store_base.chain_digest(
            "prev_payload_dict", prev, {"body_json": text})
        conn.execute(
            "INSERT INTO events (prev_hash, payload_hash, payload) VALUES (?, ?, ?)",
            (prev, digest, text),
        )
        conn.commit()
        return digest

    def verify_chain(self):
        """Recompute the full ledger; returns (ok, checked, first_bad)."""
        conn = self._connect()
        rows = conn.execute("SELECT prev_hash, payload_hash, payload FROM events ORDER BY id").fetchall()
        entries = [{"body_json": row["payload"],
                    "prev_hash": row["prev_hash"],
                    "hash": row["payload_hash"]} for row in rows]
        result = store_base.verify_entries(
            entries, "prev_payload_dict", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            return False, len(rows), result["first_break"]["index"] + 1
        return True, len(rows), None

    # -- garments -----------------------------------------------------

    def add_garment(self, name, category, colour, seasons, warmth, formality, photo):
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO garments (name, category, colour, seasons, warmth, formality, photo)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, category, colour, ";".join(seasons), warmth, formality, photo),
        )
        conn.commit()
        self._event({"kind": "garment_added", "id": cur.lastrowid, "name": name, "category": category})
        row = conn.execute("SELECT * FROM garments WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def get_garment(self, garment_id):
        conn = self._connect()
        row = conn.execute("SELECT * FROM garments WHERE id = ?", (garment_id,)).fetchone()
        return dict(row) if row else None

    def list_garments(self, category=None, season=None):
        conn = self._connect()
        rows = conn.execute("SELECT * FROM garments ORDER BY category, name").fetchall()
        out = []
        for row in rows:
            g = dict(row)
            if category and g["category"] != category:
                continue
            if season:
                listed = [s for s in g["seasons"].split(";") if s]
                if listed and season not in listed:
                    continue
            out.append(g)
        return out

    def remove_garment(self, garment_id):
        conn = self._connect()
        used = conn.execute("SELECT id, name, garment_ids FROM outfits").fetchall()
        for row in used:
            if garment_id in json.loads(row["garment_ids"]):
                return None, f"garment {garment_id} is in outfit {row['id']} ({row['name']}) — remove it from the outfit first"
        cur = conn.execute("DELETE FROM garments WHERE id = ?", (garment_id,))
        conn.commit()
        if cur.rowcount == 0:
            return None, f"no garment {garment_id}"
        self._event({"kind": "garment_removed", "id": garment_id})
        return garment_id, None

    # -- outfits ------------------------------------------------------

    def _outfit_slots(self, garment_ids):
        """(conflict, missing) for a list of garment ids; conflict is '' when fine."""
        conn = self._connect()
        cats = []
        for gid in garment_ids:
            row = conn.execute("SELECT category FROM garments WHERE id = ?", (gid,)).fetchone()
            if row is None:
                return f"no garment {gid}", None
            cats.append(row["category"])
        seen = {}
        for cat in cats:
            if cat in SINGLE_SLOTS:
                if cat in seen:
                    return f"two {cat}s — pick one", None
                seen[cat] = True
        if "dress" in seen and ("top" in seen or "bottom" in seen):
            return "a dress is the top and the bottom — drop the top/bottom or the dress", None
        missing = []
        if "dress" not in seen and "top" not in seen:
            missing.append("top")
        if "dress" not in seen and "bottom" not in seen:
            missing.append("bottom")
        if "shoes" not in seen:
            missing.append("shoes")
        return "", missing

    def add_outfit(self, name, garment_ids, note):
        conn = self._connect()
        cur = conn.execute(
            "INSERT INTO outfits (name, garment_ids, note) VALUES (?, ?, ?)",
            (name, json.dumps(garment_ids), note),
        )
        conn.commit()
        self._event({"kind": "outfit_built", "id": cur.lastrowid, "name": name, "garments": garment_ids})
        row = conn.execute("SELECT * FROM outfits WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def get_outfit(self, outfit_id):
        conn = self._connect()
        row = conn.execute("SELECT * FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
        return dict(row) if row else None

    def list_outfits(self):
        conn = self._connect()
        rows = conn.execute("SELECT * FROM outfits ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def remove_outfit(self, outfit_id):
        conn = self._connect()
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT date FROM schedule WHERE outfit_id = ? AND date >= ? ORDER BY date LIMIT 1",
            (outfit_id, today),
        ).fetchone()
        if row:
            return None, f"outfit {outfit_id} is scheduled for {row['date']} — unschedule it first"
        cur = conn.execute("DELETE FROM outfits WHERE id = ?", (outfit_id,))
        conn.commit()
        if cur.rowcount == 0:
            return None, f"no outfit {outfit_id}"
        self._event({"kind": "outfit_removed", "id": outfit_id})
        return outfit_id, None

    # -- schedule + wear ----------------------------------------------

    def schedule_outfit(self, outfit_id, day, note):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO schedule (outfit_id, date, note) VALUES (?, ?, ?)",
                (outfit_id, day.isoformat(), note),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None, f"{day.isoformat()} already has an outfit scheduled — unschedule it first"
        conn.commit()
        self._event({"kind": "scheduled", "id": cur.lastrowid, "outfit_id": outfit_id, "date": day.isoformat()})
        row = conn.execute("SELECT * FROM schedule WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row), None

    def list_schedule(self, day=None):
        conn = self._connect()
        if day is None:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE date >= date('now') ORDER BY date"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM schedule WHERE date = ?", (day.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

    def unschedule(self, schedule_id):
        conn = self._connect()
        cur = conn.execute("DELETE FROM schedule WHERE id = ?", (schedule_id,))
        conn.commit()
        if cur.rowcount == 0:
            return None, f"no scheduled entry {schedule_id}"
        self._event({"kind": "unscheduled", "id": schedule_id})
        return schedule_id, None

    def mark_worn(self, outfit_id, day):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO wear_log (outfit_id, date) VALUES (?, ?)",
                (outfit_id, day.isoformat()),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None, f"outfit {outfit_id} is already marked worn on {day.isoformat()}"
        row = conn.execute("SELECT garment_ids FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
        if row:
            for gid in json.loads(row["garment_ids"]):
                conn.execute(
                    "UPDATE garments SET last_worn = ? WHERE id = ? AND (last_worn IS NULL OR last_worn < ?)",
                    (day.isoformat(), gid, day.isoformat()),
                )
        conn.commit()
        self._event({"kind": "worn", "id": cur.lastrowid, "outfit_id": outfit_id, "date": day.isoformat()})
        return cur.lastrowid, None

    def outfit_worn_dates(self, outfit_id):
        conn = self._connect()
        rows = conn.execute(
            "SELECT date FROM wear_log WHERE outfit_id = ? ORDER BY date DESC", (outfit_id,)
        ).fetchall()
        return [r["date"] for r in rows]

    def list_events(self, limit=20):
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, payload, created FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def _fmt_garment(g, detail=False):
    seasons = g["seasons"].replace(";", ", ") if g["seasons"] else "all seasons"
    line = (
        f"#{g['id']} {g['name']} ({g['category']}, {g['colour'] or 'no colour'}, "
        f"{seasons}, warmth {g['warmth']}/5, formality {g['formality']}/5"
        f"{', last worn ' + g['last_worn'] if g['last_worn'] else ', never worn'})"
    )
    if detail and g["photo"]:
        line += f" — photo: {g['photo']}"
    return line


def _fmt_outfit(db, o, detail=False):
    conn = db._connect()
    names = []
    for gid in json.loads(o["garment_ids"]):
        row = conn.execute("SELECT name, category FROM garments WHERE id = ?", (gid,)).fetchone()
        names.append(f"{row['name']} ({row['category']})" if row else f"#{gid} (gone)")
    _, missing = db._outfit_slots(json.loads(o["garment_ids"]))
    state = "complete" if not missing else "missing: " + ", ".join(missing)
    line = f"#{o['id']} {o['name']}: " + "; ".join(names) + f" — {state}"
    if detail and o["note"]:
        line += f" — {o['note']}"
    return line


def _wardrobe_inner(action="", mode="", name="", category="", colour="", seasons="",
                    warmth=None, formality=None, photo="", garment_id=None,
                    garment_ids="", outfit_id=None, schedule_id=None, date="",
                    temp_c=None, condition="", note="", limit=None, detail=None,
                    _db=None) -> str:
    """Keep the owner's own wardrobe: garments, outfits, suggestions, schedule, wear history.

    Actions: 'garment' adds/lists/removes a garment; 'outfit' builds/lists/removes an
    outfit from owned garments; 'suggest' picks an outfit for a date from the day's
    temperature and condition plus wear history; 'schedule' plans an outfit for a date;
    'wear' marks an outfit worn; 'status' counts the wardrobe; 'events' shows the
    hash-chained event ledger. A photo is a path to an existing file — recorded, never
    copied, never opened, never sent anywhere. No AI visualisation, no garment
    extraction, no camera-roll scan, no weather fetch, no sharing, no hosted anything.
    """
    action = (action or "").strip().lower()
    if action not in ("garment", "outfit", "suggest", "schedule", "wear", "status", "events"):
        return ("wardrobe: give an action — 'garment' to add/list/remove a garment, "
                "'outfit' to build/list/remove an outfit, 'suggest' for a daily outfit "
                "suggestion, 'schedule' to plan an outfit for a date, 'wear' to mark an "
                "outfit worn, 'status' for counts, 'events' for the ledger.")

    db = _db  # the public wrapper owns the connection and closes it
    lim, err = _num(limit, "limit", 1, 200, default=20)
    if err:
        return f"wardrobe: {err}"
    detail_v = bool(detail) if detail is not None else False

    if action == "garment":
        mode_v = (mode or "list").strip().lower()
        if mode_v == "add":
            if not (name or "").strip():
                return "wardrobe: 'garment' add needs a name"
            cat = (category or "").strip().lower()
            if cat not in CATEGORIES:
                return f"wardrobe: category must be one of {', '.join(CATEGORIES)}, got {category!r}"
            warm, err = _num(warmth, "warmth", 1, 5, default=3)
            if err:
                return f"wardrobe: {err}"
            form, err = _num(formality, "formality", 1, 5, default=3)
            if err:
                return f"wardrobe: {err}"
            seas, err = _parse_seasons(seasons)
            if err:
                return f"wardrobe: {err}"
            photo_v = (photo or "").strip()
            if photo_v and not os.path.isfile(photo_v):
                return f"wardrobe: photo file does not exist: {photo_v} (give a path to an existing file)"
            g = db.add_garment((name or "").strip(), cat, (colour or "").strip(),
                               seas, warm, form, photo_v)
            return f"Garment #{g['id']} added: " + _fmt_garment(g, detail=True)
        if mode_v == "list":
            cat = (category or "").strip().lower() or None
            if cat and cat not in CATEGORIES:
                return f"wardrobe: category must be one of {', '.join(CATEGORIES)}, got {category!r}"
            day, err = _parse_date(date, "date")
            season = _season_of(day) if day else None
            items = db.list_garments(category=cat, season=season)[:lim]
            if not items:
                return "Wardrobe is empty" + (f" for {cat}" if cat else "") + " — add a garment first."
            return "Garments:\n" + "\n".join(_fmt_garment(g, detail_v) for g in items)
        if mode_v == "remove":
            gid, err = _num(garment_id, "garment_id", 1, 10 ** 9)
            if err:
                return f"wardrobe: {err}"
            removed, err = db.remove_garment(gid)
            if err:
                return f"wardrobe: {err}"
            return f"Garment #{removed} removed."
        return "wardrobe: 'garment' mode must be 'add', 'list' or 'remove'"

    if action == "outfit":
        mode_v = (mode or "list").strip().lower()
        if mode_v == "build":
            if not (name or "").strip():
                return "wardrobe: 'outfit' build needs a name"
            ids, err = _parse_ids(garment_ids)
            if err:
                return f"wardrobe: {err}"
            if not ids:
                return "wardrobe: 'outfit' build needs garment_ids, e.g. '1;2;3'"
            conflict, missing = db._outfit_slots(ids)
            if conflict:
                return f"wardrobe: cannot build that outfit — {conflict}"
            o = db.add_outfit((name or "").strip(), ids, (note or "").strip())
            line = "Outfit #" + str(o["id"]) + " built: " + _fmt_outfit(db, o, detail_v)
            if missing:
                line += f" — not complete yet, still missing: {', '.join(missing)}"
            return line
        if mode_v == "list":
            items = db.list_outfits()[:lim]
            if not items:
                return "No outfits yet — build one from garments you own."
            return "Outfits:\n" + "\n".join(_fmt_outfit(db, o, detail_v) for o in items)
        if mode_v == "remove":
            oid, err = _num(outfit_id, "outfit_id", 1, 10 ** 9)
            if err:
                return f"wardrobe: {err}"
            removed, err = db.remove_outfit(oid)
            if err:
                return f"wardrobe: {err}"
            return f"Outfit #{removed} removed."
        return "wardrobe: 'outfit' mode must be 'build', 'list' or 'remove'"

    if action == "suggest":
        day, err = _parse_date(date, "date")
        if err:
            return f"wardrobe: {err}"
        day = day or _date_cls.today()
        season = _season_of(day)
        temp, err = _num(temp_c, "temp_c", -40, 45, default=20)
        if err:
            return f"wardrobe: {err}"
        form = None
        if formality is not None and formality != "":
            form, err = _num(formality, "formality", 1, 5)
            if err:
                return f"wardrobe: {err}"
        cond = (condition or "").strip().lower()
        raining = any(word in cond for word in ("rain", "drizzle", "storm", "shower", "wet"))
        target_warmth = _warmth_for(temp)
        garments = db.list_garments(season=season)
        if not garments:
            return ("Wardrobe is empty for " + season + " — add garments first "
                    "(a garment with no seasons listed counts for every season).")
        if form is not None:
            garments = [g for g in garments if g["formality"] >= form]
            if not garments:
                return f"No garments at formality {form}/5 for {season} — add one or lower the formality."

        def _pick(slot_cats, want_warmth):
            """Least-recently-worn garment in the slot nearest the warmth band."""
            cands = [g for g in garments if g["category"] in slot_cats]
            if not cands:
                return None
            def _key(g):
                last = g["last_worn"] or "0000-00-00"
                return (last, abs(g["warmth"] - want_warmth))
            return sorted(cands, key=_key)[0]

        yesterday = (day - timedelta(days=1)).isoformat()
        week_ago = (day - timedelta(days=7)).isoformat()
        recent_outfits = set()
        for o in db.list_outfits():
            worn = db.outfit_worn_dates(o["id"])
            if worn and worn[0] >= week_ago:
                recent_outfits.add(o["id"])
        # A saved outfit whose garments all suit today wins over a fresh combination.
        for o in db.list_outfits():
            if o["id"] in recent_outfits:
                continue
            ids = json.loads(o["garment_ids"])
            gs = [db.get_garment(i) for i in ids]
            if any(g is None for g in gs):
                continue
            if any(g["formality"] < (form or 1) for g in gs):
                continue
            if any(g["seasons"] and season not in g["seasons"].split(";") for g in gs):
                continue
            if any(abs(g["warmth"] - target_warmth) > 2 for g in gs):
                continue
            return ("Suggested outfit for " + day.isoformat() + f" ({season}, {temp}°C"
                    + (f", {cond}" if cond else "") + "): saved outfit "
                    + _fmt_outfit(db, o) + f" — warmth suits {temp}°C, not worn in the last 7 days.")

        dress = _pick(("dress",), target_warmth)
        top = None if dress else _pick(("top",), target_warmth)
        bottom = None if dress else _pick(("bottom",), target_warmth)
        shoes = _pick(("shoes",), target_warmth)
        outer = _pick(("outerwear",), target_warmth + 1) if (temp < 20 or raining) else None
        picked = [g for g in (dress, top, bottom, shoes, outer) if g]
        if not picked:
            return "No garments suit that day — add garments for " + season + "."
        missing = []
        if not dress and not top:
            missing.append("top")
        if not dress and not bottom:
            missing.append("bottom")
        if not shoes:
            missing.append("shoes")
        lines = ["Suggested outfit for " + day.isoformat() + f" ({season}, {temp}°C"
                 + (f", {cond}" if cond else "") + "):"]
        for g in picked:
            why = f"warmth {g['warmth']}/5 suits {temp}°C"
            if g["last_worn"]:
                why += f", last worn {g['last_worn']}"
            else:
                why += ", never worn"
            lines.append(f"  - {g['name']} ({g['category']}, {g['colour'] or 'no colour'}) — {why}")
        if missing:
            lines.append("Missing from your wardrobe: " + ", ".join(missing) + " — add one to complete outfits.")
        if raining and not outer:
            lines.append("It may rain and you own no outerwear — take an umbrella.")
        if yesterday:
            worn_yesterday = [g for g in picked if g["last_worn"] == yesterday]
            if worn_yesterday:
                lines.append("Note: " + ", ".join(g["name"] for g in worn_yesterday)
                             + " was worn yesterday — swap it if you would rather not repeat.")
        return "\n".join(lines)

    if action == "schedule":
        mode_v = (mode or "list").strip().lower()
        if mode_v == "add":
            oid, err = _num(outfit_id, "outfit_id", 1, 10 ** 9)
            if err:
                return f"wardrobe: {err}"
            if db.get_outfit(oid) is None:
                return f"wardrobe: no outfit {oid}"
            day, err = _parse_date(date, "date")
            if err:
                return f"wardrobe: {err}"
            if day is None:
                return "wardrobe: 'schedule' add needs a date (YYYY-MM-DD)"
            if day < _date_cls.today():
                return f"wardrobe: cannot schedule the past ({day.isoformat()})"
            row, err = db.schedule_outfit(oid, day, (note or "").strip())
            if err:
                return f"wardrobe: {err}"
            o = db.get_outfit(oid)
            return f"Scheduled outfit #{oid} ({o['name']}) for {day.isoformat()}."
        if mode_v == "list":
            day, err = _parse_date(date, "date")
            if err:
                return f"wardrobe: {err}"
            rows = db.list_schedule(day)[:lim]
            if not rows:
                return "Nothing scheduled" + (f" for {day.isoformat()}" if day else " yet") + "."
            lines = []
            for r in rows:
                o = db.get_outfit(r["outfit_id"])
                lines.append(f"#{r['id']} {r['date']}: outfit #{r['outfit_id']} "
                             f"({o['name'] if o else 'gone'})"
                             + (f" — {r['note']}" if r["note"] else ""))
            return "Scheduled:\n" + "\n".join(lines)
        if mode_v == "remove":
            sid, err = _num(schedule_id, "schedule_id", 1, 10 ** 9)
            if err:
                return f"wardrobe: {err}"
            removed, err = db.unschedule(sid)
            if err:
                return f"wardrobe: {err}"
            return f"Unscheduled entry #{removed}."
        return "wardrobe: 'schedule' mode must be 'add', 'list' or 'remove'"

    if action == "wear":
        oid, err = _num(outfit_id, "outfit_id", 1, 10 ** 9)
        if err:
            return f"wardrobe: {err}"
        if db.get_outfit(oid) is None:
            return f"wardrobe: no outfit {oid}"
        day, err = _parse_date(date, "date")
        if err:
            return f"wardrobe: {err}"
        day = day or _date_cls.today()
        if day > _date_cls.today():
            return "wardrobe: cannot mark a future date as worn"
        row, err = db.mark_worn(oid, day)
        if err:
            return f"wardrobe: {err}"
        o = db.get_outfit(oid)
        return f"Marked outfit #{oid} ({o['name']}) worn on {day.isoformat()}."

    if action == "status":
        garments = db.list_garments()
        outfits = db.list_outfits()
        sched = db.list_schedule()
        by_cat = {c: 0 for c in CATEGORIES}
        for g in garments:
            by_cat[g["category"]] += 1
        worn = [g for g in garments if g["last_worn"]]
        never = len(garments) - len(worn)
        lines = [f"Wardrobe: {len(garments)} garments, {len(outfits)} outfits, "
                 f"{len(sched)} scheduled."]
        lines.append("By category: " + ", ".join(f"{c} {n}" for c, n in by_cat.items() if n)
                     or "none yet")
        lines.append(f"Worn at least once: {len(worn)}; never worn: {never}.")
        if sched:
            nxt = sched[0]
            o = db.get_outfit(nxt["outfit_id"])
            lines.append(f"Next up: {nxt['date']} — outfit #{nxt['outfit_id']} "
                         f"({o['name'] if o else 'gone'}).")
        return "\n".join(lines)

    if action == "events":
        ok, checked, first_bad = db.verify_chain()
        rows = db.list_events(lim)
        lines = [f"Event ledger: {checked} events, chain "
                 + ("OK" if ok else f"BROKEN at #{first_bad}")]
        for r in rows:
            try:
                payload = json.loads(r["payload"])
                lines.append(f"#{r['id']} {payload.get('kind', '?')} — {r['created']}")
            except (ValueError, TypeError):
                lines.append(f"#{r['id']} (unreadable) — {r['created']}")
        return "\n".join(lines)

    return "wardrobe: unknown action"


def wardrobe(action="", mode="", name="", category="", colour="", seasons="",
             warmth=None, formality=None, photo="", garment_id=None,
             garment_ids="", outfit_id=None, schedule_id=None, date="",
             temp_c=None, condition="", note="", limit=None, detail=None,
             _db_path=None) -> str:
    """Public entry: one call, one connection, closed before returning.

    Each funnel call opens the wardrobe store, works, and closes it —
    a leaked connection would hold a SQLite lock across calls and
    block the next write (a duplicate schedule left an open
    transaction behind exactly this way before the close was added).
    """
    db = _WardrobeDB(_db_path)
    try:
        return _wardrobe_inner(
            action=action, mode=mode, name=name, category=category,
            colour=colour, seasons=seasons, warmth=warmth,
            formality=formality, photo=photo, garment_id=garment_id,
            garment_ids=garment_ids, outfit_id=outfit_id,
            schedule_id=schedule_id, date=date, temp_c=temp_c,
            condition=condition, note=note, limit=limit, detail=detail,
            _db=db)
    finally:
        db.close()
