"""Study edition — deck model + .apkg writer (ST-1).

Native, stdlib-only writer for real Anki deck files: define a model
(Question / Answer / Source, plus a cloze variant for definitions
and formulae), add notes with stable GUIDs, attach media, and write
a self-contained .apkg the student double-clicks to import.
Behaviour spec and dev-side oracle: genanki 0.13.1 (MIT, Kerrick
Staley; LICENSE re-read at the pin during the Study build — see
the build report). The library itself never ships in this tree;
this module is VYUK's own code, written to produce the same
interchange bytes for the same notes.

Authority: wave-4 scout §KW-3 (genanki ADOPT; AnkiConnect
deliberately NOT used) + v6 LEARN + Study scope §2 (ST-1).

Copy rule (binding): the output is described as ".apkg
(Anki-compatible)" only — descriptive, nominative use, no
endorsement claim, no affiliation claim. Anki itself is never
shipped, driven, or bundled; the user's own Anki install is the
only Anki in the picture. AnkiConnect (GPL-3.0) is rejected and
is not used here.

Source enforcement (binding): every note must carry a non-empty
Source field. A card without Source is refused, not written.
Stable GUIDs: a note's GUID is a deterministic base91 hash of its
fields (the same construction the oracle uses), so regenerating
a deck after Lecture 7 adds L7 cards without resetting L1-L6
review progress — Anki updates notes with matching GUIDs instead
of duplicating them.

No cross-module imports (edition precedent). No pip-time
dependency. Field proof on HIS Anki is owed (field runs), not
claimed here: a .apkg that validates in the suite is not proof
it imports on his Anki.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import sqlite3
import tempfile
import zipfile

FRONT_BACK = 0
CLOZE = 1

DEFAULT_LATEX_PRE = (
    "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n"
    "\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n"
    "\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n"
)
DEFAULT_LATEX_POST = "\\end{document}"

BASE91_TABLE = [
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's',
    't', 'u', 'v', 'w', 'x', 'y', 'z', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L',
    'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4',
    '5', '6', '7', '8', '9', '!', '#', '$', '%', '&', '(', ')', '*', '+', ',', '-', '.', '/', ':',
    ';', '<', '=', '>', '?', '@', '[', ']', '^', '_', '`', '{', '|', '}', '~']


class SourceMissingError(ValueError):
    """A note was offered without a non-empty Source field."""


class DeckError(ValueError):
    """Deck/model/field shape errors (wrong field count, bad id, ...)."""


def guid_for(*values):
    """Stable GUID: base91 of the first 8 bytes of sha256 over the
    '__'-joined field values. Deterministic: the same fields always
    yield the same GUID, so regeneration updates instead of duplicating."""
    hash_str = "__".join(str(v) for v in values)
    digest = hashlib.sha256(hash_str.encode("utf-8")).digest()[:8]
    num = 0
    for b in digest:
        num = (num << 8) + b
    out = []
    while num > 0:
        out.append(BASE91_TABLE[num % len(BASE91_TABLE)])
        num //= len(BASE91_TABLE)
    return "".join(reversed(out))


def _render_qfmt(qfmt, values):
    """Render a question template with plain {{Field}} substitution,
    mirroring the oracle's chevron behaviour for the template subset
    this module supports: plain references substitute; {{cloze:...}}
    references render as EMPTY (chevron looks up the literal key
    "cloze:Text", finds nothing, and renders nothing). That empty
    rendering is load-bearing: it is why a cloze-only template
    counts every model field as required (see required_fields).
    Mustache sections and filters are not supported."""
    def _sub(match):
        token = match.group(1).strip()
        if token.startswith("cloze:"):
            return ""
        return values.get(token, "")

    return re.sub(r"{{([^}]+)}}", _sub, qfmt)


class StudyModel:
    """A card model: fields, templates, css, type. The Study edition
    ships two shapes: Question/Answer/Source (front-back) and the
    cloze variant for definitions and formulae."""

    def __init__(self, model_id, name, fields, templates, css="",
                 model_type=FRONT_BACK, sort_field_index=0,
                 latex_pre=DEFAULT_LATEX_PRE, latex_post=DEFAULT_LATEX_POST):
        if not isinstance(model_id, int):
            raise DeckError("model_id must be an integer, not %r" % (model_id,))
        self.model_id = model_id
        self.name = name
        # fields: list of {"name": ...}; templates: list of {"name","qfmt","afmt"}
        self.fields = [dict(f) for f in fields]
        self.templates = [dict(t) for t in templates]
        self.css = css
        self.model_type = model_type
        self.sort_field_index = sort_field_index
        self.latex_pre = latex_pre
        self.latex_post = latex_post
        self._field_names = [f["name"] for f in self.fields]

    def field_index(self, name):
        try:
            return self._field_names.index(name)
        except ValueError:
            return -1

    def required_fields(self):
        """Per-template required-field rules, computed by the sentinel
        method (mirroring the oracle): a field is required for a
        template if blanking it removes all meaningful content from
        the question side. Returns a list of [template_ord,
        "all"|"any", [field ords]]. Consequence, stated not hidden:
        a cloze-only template renders empty under the substitution
        rule, so EVERY model field counts as required for it — the
        oracle produces the same rule for the same reason."""
        req = []
        for tmpl_ord, tmpl in enumerate(self.templates):
            qfmt = tmpl["qfmt"]
            required = []
            for field_ord, fname in enumerate(self._field_names):
                values = {f: "SeNtInEl" for f in self._field_names}
                values[fname] = ""
                if "SeNtInEl" not in _render_qfmt(qfmt, values):
                    required.append(field_ord)
            if required:
                req.append([tmpl_ord, "all", required])
                continue
            # No "all" field: fall back to "any" (present field gives content).
            any_fields = []
            for field_ord, fname in enumerate(self._field_names):
                values = {f: "" for f in self._field_names}
                values[fname] = "SeNtInEl"
                if "SeNtInEl" in _render_qfmt(qfmt, values):
                    any_fields.append(field_ord)
            if not any_fields:
                raise DeckError(
                    "Could not compute required fields for template %r; "
                    "check qfmt: %r" % (tmpl.get("name"), qfmt))
            req.append([tmpl_ord, "any", any_fields])
        return req

    def to_json(self, timestamp, deck_id):
        tmpls = []
        for ord_, tmpl in enumerate(self.templates):
            t = dict(tmpl)
            t["ord"] = ord_
            t.setdefault("bafmt", "")
            t.setdefault("bqfmt", "")
            t.setdefault("bfont", "")
            t.setdefault("bsize", 0)
            t.setdefault("did", None)
            tmpls.append(t)
        flds = []
        for ord_, field in enumerate(self.fields):
            f = dict(field)
            f["ord"] = ord_
            f.setdefault("font", "Liberation Sans")
            f.setdefault("media", [])
            f.setdefault("rtl", False)
            f.setdefault("size", 20)
            f.setdefault("sticky", False)
            flds.append(f)
        return {
            "css": self.css,
            "did": deck_id,
            "flds": flds,
            "id": str(self.model_id),
            "latexPost": self.latex_post,
            "latexPre": self.latex_pre,
            "latexsvg": False,
            "mod": int(timestamp),
            "name": self.name,
            "req": self.required_fields(),
            "sortf": self.sort_field_index,
            "tags": [],
            "tmpls": tmpls,
            "type": self.model_type,
            "usn": -1,
            "vers": [],
        }


def question_answer_model(model_id, name="VYUK Study"):
    """The standard Study model: Question / Answer / Source."""
    return StudyModel(
        model_id=model_id,
        name=name,
        fields=[{"name": "Question"}, {"name": "Answer"}, {"name": "Source"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Question}}",
            "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Answer}}"
                    "\n\n<p>Source: {{Source}}</p>",
        }],
        model_type=FRONT_BACK,
    )


def cloze_model(model_id, name="VYUK Study Cloze"):
    """Cloze variant for definitions and formulae: Text / Source."""
    return StudyModel(
        model_id=model_id,
        name=name,
        fields=[{"name": "Text"}, {"name": "Source"}],
        templates=[{
            "name": "Cloze",
            "qfmt": "{{cloze:Text}}",
            "afmt": "{{cloze:Text}}\n\n<p>Source: {{Source}}</p>",
        }],
        model_type=CLOZE,
    )


class StudyNote:
    """One note: fields aligned with its model, tags, optional due."""

    def __init__(self, model, fields, tags=(), guid=None, due=0):
        if len(fields) != len(model.fields):
            raise DeckError(
                "Number of fields in model does not match number of fields "
                "in note: model has %d fields, note has %d."
                % (len(model.fields), len(fields)))
        self.model = model
        self.fields = list(fields)
        self.tags = list(tags)
        for tag in self.tags:
            if " " in tag:
                raise DeckError("Tag %r contains a space; not allowed." % tag)
        self._guid = guid
        self.due = due

    @property
    def guid(self):
        if self._guid is None:
            return guid_for(*self.fields)
        return self._guid

    @property
    def sort_field(self):
        return self.fields[self.model.sort_field_index]

    def source(self):
        """The note's Source field value, or '' if the model has none."""
        idx = self.model.field_index("Source")
        return self.fields[idx] if idx >= 0 else ""

    def check_source(self):
        """Refuse notes without a non-empty Source field."""
        if not self.source().strip():
            raise SourceMissingError(
                "Note refused: Source field is empty. Every Study card "
                "must say where it came from.")

    def card_ords(self):
        """Card ordinals this note generates (front-back: one per
        template whose required fields are present; cloze: one per
        unique {{cN::}} reference in the cloze field, else {0})."""
        if self.model.model_type == CLOZE:
            qfmt = self.model.templates[0]["qfmt"]
            cloze_fields = set()
            for token in re.findall(r"{{[^}]*?cloze:(?:[^}]?:)*(.+?)}}", qfmt):
                cloze_fields.add(token.strip())
            ords = set()
            for fname in cloze_fields:
                idx = self.model.field_index(fname)
                value = self.fields[idx] if idx >= 0 else ""
                for m in re.findall(r"{{c(\d+)::.+?}}", value, re.DOTALL):
                    if int(m) > 0:
                        ords.add(int(m) - 1)
            return sorted(ords) if ords else [0]
        req = self.model.required_fields()
        out = []
        for tmpl_ord, any_or_all, req_fields in req:
            op = all if any_or_all == "all" else any
            if op(self.fields[o] for o in req_fields):
                out.append(tmpl_ord)
        return out


class StudyDeck:
    """A deck: id, name, description, notes."""

    def __init__(self, deck_id, name, description=""):
        if not isinstance(deck_id, int):
            raise DeckError("deck_id must be an integer, not %r" % (deck_id,))
        if not isinstance(name, str):
            raise DeckError("name must be a string, not %r" % (name,))
        self.deck_id = deck_id
        self.name = name
        self.description = description
        self.notes = []

    def add_note(self, note):
        note.check_source()
        self.notes.append(note)

    def to_json(self):
        return {
            "collapsed": False,
            "conf": 1,
            "desc": self.description,
            "dyn": 0,
            "extendNew": 0,
            "extendRev": 50,
            "id": self.deck_id,
            "lrnToday": [0, 0],
            "mod": 1425278051,
            "name": self.name,
            "newToday": [0, 0],
            "revToday": [0, 0],
            "timeToday": [0, 0],
            "usn": -1,
        }


# Anki's interchange schema (schema version 11 layout used by the
# .apkg format). These are Anki's own table definitions, required
# for any writer of the open format.
_APKG_SCHEMA = """
CREATE TABLE col (
    id              integer primary key,
    crt             integer not null,
    mod             integer not null,
    scm             integer not null,
    ver             integer not null,
    dty             integer not null,
    usn             integer not null,
    ls              integer not null,
    conf            text not null,
    models          text not null,
    decks           text not null,
    dconf           text not null,
    tags            text not null
);
CREATE TABLE notes (
    id              integer primary key,
    guid            text not null,
    mid             integer not null,
    mod             integer not null,
    usn             integer not null,
    tags            text not null,
    flds            text not null,
    sfld            integer not null,
    csum            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE cards (
    id              integer primary key,
    nid             integer not null,
    did             integer not null,
    ord             integer not null,
    mod             integer not null,
    usn             integer not null,
    type            integer not null,
    queue           integer not null,
    due             integer not null,
    ivl             integer not null,
    factor          integer not null,
    reps            integer not null,
    lapses          integer not null,
    left            integer not null,
    odue            integer not null,
    odid            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE revlog (
    id              integer primary key,
    cid             integer not null,
    usn             integer not null,
    ease            integer not null,
    ivl             integer not null,
    lastIvl         integer not null,
    factor          integer not null,
    time            integer not null,
    type            integer not null
);
CREATE TABLE graves (
    usn             integer not null,
    oid             integer not null,
    type            integer not null
);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
"""

_CONF_JSON = json.dumps({
    "activeDecks": [1], "addToCur": True, "collapseTime": 1200,
    "curDeck": 1, "curModel": "1425279151691", "dueCounts": True,
    "estTimes": True, "newBury": True, "newSpread": 0, "nextPos": 1,
    "sortBackwards": False, "sortType": "noteFld", "timeLim": 0,
})
_DEFAULT_DECK_JSON = json.dumps({
    "1": {
        "collapsed": False, "conf": 1, "desc": "", "dyn": 0,
        "extendNew": 10, "extendRev": 50, "id": 1,
        "lrnToday": [0, 0], "mod": 1425279151, "name": "Default",
        "newToday": [0, 0], "revToday": [0, 0], "timeToday": [0, 0],
        "usn": 0,
    }
})
_DCONF_JSON = json.dumps({
    "1": {
        "autoplay": True, "id": 1,
        "lapse": {"delays": [10], "leechAction": 0, "leechFails": 8,
                  "minInt": 1, "mult": 0},
        "maxTaken": 60, "mod": 0, "name": "Default",
        "new": {"bury": True, "delays": [1, 10], "initialFactor": 2500,
                "ints": [1, 4, 7], "order": 1, "perDay": 20, "separate": True},
        "replayq": True,
        "rev": {"bury": True, "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1,
                "maxIvl": 36500, "minSpace": 1, "perDay": 100},
        "timer": 0, "usn": 0,
    }
})


def write_apkg(deck, path, media_files=None, timestamp=None):
    """Write a StudyDeck to a self-contained .apkg file. Every note
    is Source-checked before writing; media_files is a list of file
    paths attached by index (Anki's media mapping). Returns path."""
    import time as _time
    if timestamp is None:
        timestamp = _time.time()
    media_files = list(media_files or [])
    for note in deck.notes:
        note.check_source()

    fd, dbfilename = tempfile.mkstemp()
    os.close(fd)
    try:
        conn = sqlite3.connect(dbfilename)
        cur = conn.cursor()
        cur.executescript(_APKG_SCHEMA)
        cur.execute(
            "INSERT INTO col VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, 1411124400, 1425279151694, 1425279151690, 11, 0, 0, 0,
             _CONF_JSON, "{}", _DEFAULT_DECK_JSON, _DCONF_JSON, "{}"))
        # Deck row.
        decks = json.loads(_DEFAULT_DECK_JSON)
        decks[str(deck.deck_id)] = deck.to_json()
        cur.execute("UPDATE col SET decks = ?", (json.dumps(decks),))
        # Models row.
        models = {}
        for note in deck.notes:
            models[note.model.model_id] = note.model.to_json(timestamp, deck.deck_id)
        cur.execute("UPDATE col SET models = ?", (json.dumps(models),))
        # Notes + cards.
        id_gen = itertools.count(int(timestamp * 1000))
        for note in deck.notes:
            note_id = next(id_gen)
            cur.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                note_id, note.guid, note.model.model_id, int(timestamp), -1,
                " " + " ".join(note.tags) + " " if note.tags else " ",
                "\x1f".join(note.fields), note.sort_field, 0, 0, ""))
            for ord_ in note.card_ords():
                cur.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    next(id_gen), note_id, deck.deck_id, ord_, int(timestamp),
                    -1, 0, 0, note.due, 0, 0, 0, 0, 0, 0, 0, 0, ""))
        conn.commit()
        conn.close()
        with zipfile.ZipFile(path, "w") as outzip:
            outzip.write(dbfilename, "collection.anki2")
            media_json = {i: os.path.basename(p) for i, p in enumerate(media_files)}
            outzip.writestr("media", json.dumps(media_json))
            for i, p in enumerate(media_files):
                outzip.write(p, str(i))
    finally:
        if os.path.exists(dbfilename):
            os.unlink(dbfilename)
    return path


def read_apkg_summary(path):
    """Inspect a .apkg without importing it: returns note/card rows
    (guid, fields, tags; note id, deck id, ord) plus the models and
    media mapping. Used by suites and by callers verifying a write."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "collection.anki2" not in names:
            raise DeckError("Not an .apkg: no collection.anki2 inside.")
        blob = zf.read("collection.anki2")
        media = json.loads(zf.read("media")) if "media" in names else {}
    fd, dbfilename = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(dbfilename, "wb") as f:
            f.write(blob)
        conn = sqlite3.connect(dbfilename)
        cur = conn.cursor()
        notes = cur.execute(
            "SELECT id, guid, mid, tags, flds FROM notes ORDER BY id").fetchall()
        cards = cur.execute(
            "SELECT id, nid, did, ord FROM cards ORDER BY id").fetchall()
        models_row = cur.execute("SELECT models FROM col").fetchone()[0]
        decks_row = cur.execute("SELECT decks FROM col").fetchone()[0]
        conn.close()
    finally:
        if os.path.exists(dbfilename):
            os.unlink(dbfilename)
    return {
        "notes": [{"id": n[0], "guid": n[1], "mid": n[2],
                   "tags": n[3], "fields": n[4].split("\x1f")} for n in notes],
        "cards": [{"id": c[0], "nid": c[1], "did": c[2], "ord": c[3]}
                  for c in cards],
        "models": json.loads(models_row),
        "decks": json.loads(decks_row),
        "media": media,
    }


__all__ = [
    "FRONT_BACK", "CLOZE", "SourceMissingError", "DeckError", "guid_for",
    "StudyModel", "question_answer_model", "cloze_model", "StudyNote",
    "StudyDeck", "write_apkg", "read_apkg_summary",
]
