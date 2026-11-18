"""UI string localization (R19-6, tolgee/tolgee-platform inspiration).

Tolgee is a web platform that manages translation keys, translations
and locales for apps — projects, keys, translations per language,
in-context editing, machine translation, import/export (JSON, PO,
XLIFF). This module builds the honest buildable remainder in own
stdlib code: LOCALIZATION MANAGEMENT — a local store for UI strings
(projects, keys, translations per language), import/export in JSON /
PO / XLIFF, and machine translation of a key's base text into target
languages with the product's own configured Gemini key (the
prompt_tune _lm_call shape). The audio translation/dubbing path is
NOT rebuilt here — audio_localise (R19-1) already translates audio;
this tool is UI STRING localization (keys + translations + locales).

Licence: Apache-2.0 (open core) — "Parent-ruled (licence, 2026-11-12)" (the
Tolgee LICENSE, read in full from main: 11,865 bytes, 211 lines —
Apache License 2.0 for all content outside ee/ and /webapp/src/ee;
the ee/ carve-out is the Tolgee EE License v1, 4,640 bytes, 76 lines,
NON-COMMERCIAL only, read in full — no ee/ code is used, adopted or
shipped). Nothing is adopted — no TypeScript, no Java, no web app, no
wrapper, no port. The build takes only the localization shapes
(projects, keys, translations per language, import/export, machine
translation) and rebuilds them as own stdlib code.

What is built (one effectful tool `translations`, confirm-gated for
translate):
  * project — add / list / get / remove. A project has a name, a
    base language (the language the keys are written in) and an
    optional description. Removing a project removes its keys and
    translations; the refusal names the counts.
  * key — add / list / get / remove. A key belongs to a project
    and has a name (dotted, e.g. "home.title"), an optional
    description and optional tags. Removing a key removes its
    translations; the refusal names the counts.
  * translation — set / get / list / missing. Set a translation
    for a key + language (the text is stored with its source:
    "human" for an operator-supplied text, "machine" for a
    machine translation, "import" for an imported one, and the
    source text's hash when it came from machine translation, so
    every machine translation is attributable to the exact source
    text it was drawn from). `missing` lists the keys with no
    translation in a language.
  * translate — machine-translate one key's base text, or all keys
    missing in a target language, into one or more target
    languages, with the product's own Gemini key. The LM is told
    to return ONLY the translation. Confirm-gated (C-14
    approve-first): this calls the owner's Gemini endpoint and
    spends their LM quota (the audio_localise translate precedent).
  * import — import a JSON / PO / XLIFF file (operator-supplied
    path) into a project: JSON is a flat {key: text} map or a
    nested map flattened with dots; PO is msgid/msgstr pairs
    (msgctxt becomes part of the key); XLIFF is <trans-unit>
    id/source/target. The language comes from the `language`
    argument (JSON/PO) or from the file's target-language (XLIFF),
    and the import reports created / updated / skipped with the
    numbers.
  * export — export a project's translations for one language to
    JSON / PO / XLIFF, written to an operator-supplied path or to
    the default exports folder. PO uses the key as msgid (Tolgee's
    convention); XLIFF carries the base text as <source> and the
    translation as <target>.
  * status — counts of projects, keys, translations, languages,
    ledger state.
  * events — the hash-chained ledger (list / verify).

What is NOT built (STOP, stated on the tin):
  * NO WEB APP, no hosted anything, no public page. The tool is
    local only, single-user by design.
  * NO ee/ CODE — the Tolgee ee/ carve-out is the Tolgee EE License
    v1, NON-COMMERCIAL only (no commercial use, no hosted or
    managed service, license-key functionality must not be
    circumvented). It is EXCLUDED from this commercial product; no
    ee/ code is read, adopted or shipped, and no ee/ feature is
    rebuilt here.
  * NO IN-CONTEXT EDITING — that needs a web UI / browser extension
    and the tree has no web surface for it; it is a follow-up or a
    STOP, not this batch.
  * NO AUDIO translation or dubbing — audio_localise already does
    that; this tool is UI strings only.
  * NO multi-user roles, no staff accounts, no channel sync, no
    network call beyond the Gemini translate call itself.

Classification: `translate` calls the owner's Gemini endpoint and
spends their LM quota, so it is confirm-gated (the audio_localise
translate/localise precedent, C-14 approve-first). `project`,
`key`, `translation`, `import`, `export`, `status` and `events`
are local app-state and reads — those stay free. Unknown or
missing action gates fail-closed (the handler refuses them anyway).

Bounds: at most 100 projects; at most 5,000 keys per project; at
most 50 languages per project; key names at most 200 chars;
descriptions at most 500 chars; tags at most 200 chars; a
translation text at most 5,000 chars; an import file at most 2 MB;
translate at most 20 keys and at most 10 target languages per call;
export at most 5,000 keys; language codes are BCP 47 short codes
(e.g. en, hi, de, pt-BR).
"""

import hashlib
import json
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import DATA_DIR
from . import store_base

_ACTIONS = ("project", "key", "translation", "translate", "import",
            "export", "status", "events")

_FORMATS = ("json", "po", "xliff")

_MAX_PROJECTS = 100
_MAX_KEYS_PER_PROJECT = 5000
_MAX_LANGUAGES_PER_PROJECT = 50
_MAX_KEY = 200
_MAX_DESC = 500
_MAX_TAGS = 200
_MAX_TEXT = 5000
_MAX_IMPORT_BYTES = 2 * 1024 * 1024
_MAX_TRANSLATE_KEYS = 20
_MAX_TRANSLATE_LANGS = 10
_MAX_EXPORT_KEYS = 5000
_MAX_LIMIT = 100
_MAX_PATH = 500
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


class _Refusal(Exception):
    """A refusal with the numbers — never a silent wrong answer."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _clean_str(v, what, limit=_MAX_DESC, required=True):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return None
    if not isinstance(v, str) or not v.strip():
        if required:
            raise _Refusal("%s must be a non-empty string" % what)
        return None
    v = v.strip()
    if len(v) > limit:
        raise _Refusal("%s must be at most %d chars" % (what, limit))
    return v


def _clean_lang(v, what="language"):
    v = _clean_str(v, what, limit=10)
    if not _LANG_RE.match(v):
        raise _Refusal("%s must be a language code like en, hi, de"
                       " or pt-BR" % what)
    return v


def _clean_key_name(v, what="key"):
    v = _clean_str(v, what, limit=_MAX_KEY)
    if v.startswith(".") or v.endswith(".") or ".." in v:
        raise _Refusal("%s must be a dotted key name without leading,"
                       " trailing or double dots" % what)
    if any(ch.isspace() for ch in v):
        raise _Refusal("%s must not contain spaces" % what)
    return v


def _clean_limit(v):
    if v is None:
        return 20
    if not _is_int(v) or v < 1 or v > _MAX_LIMIT:
        raise _Refusal("limit must be between 1 and %d" % _MAX_LIMIT)
    return v


def _clean_lang_list(v, what="target_languages"):
    if v is None:
        raise _Refusal("%s is required" % what)
    if isinstance(v, str):
        v = [x.strip() for x in v.split(",") if x.strip()]
    if not isinstance(v, list) or not v:
        raise _Refusal("%s must be a non-empty list of language"
                       " codes" % what)
    if len(v) > _MAX_TRANSLATE_LANGS:
        raise _Refusal("at most %d target languages per call"
                       % _MAX_TRANSLATE_LANGS)
    seen = []
    for lang in v:
        lang = _clean_lang(lang, what)
        if lang not in seen:
            seen.append(lang)
    return seen


# ---------------------------------------------------------------------------
# Store: one SQLite file (DATA_DIR) — projects + keys + translations
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "translations.sqlite3"


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS projects ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL,"
    " base_language TEXT NOT NULL, description TEXT,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS keys ("
    " id TEXT PRIMARY KEY, project_id TEXT NOT NULL,"
    " name TEXT NOT NULL, description TEXT, tags TEXT,"
    " created REAL NOT NULL,"
    " UNIQUE(project_id, name))",
    "CREATE TABLE IF NOT EXISTS translations ("
    " project_id TEXT NOT NULL, key_id TEXT NOT NULL,"
    " language TEXT NOT NULL, text TEXT NOT NULL,"
    " source TEXT NOT NULL, source_hash TEXT,"
    " updated REAL NOT NULL,"
    " PRIMARY KEY(project_id, key_id, language))",
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " prev_hash TEXT NOT NULL, hash TEXT NOT NULL)",
)


class _TranslationsStore(store_base.StoreBase):
    """Projects + keys + translations + a hash-chained ledger.
    Open-per-operation; every write is followed by a re-read before
    the caller claims it (A1-store). Reads answer empty WITHOUT
    creating the file."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()


    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
        return conn

    def _append_event_locked(self, conn, kind, payload):
        ts = time.time()
        prev = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        prev_hash = prev[0] if prev else "0" * 64
        digest = store_base.chain_digest(
            "json_body_ts", prev_hash,
            {"ts": ts, "kind": kind, "payload": payload})
        conn.execute(
            "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts, kind, json.dumps(payload), prev_hash, digest))
        return digest

    def append_event(self, kind, payload):
        with _LOCK:
            conn = self._connect()
            try:
                digest = self._append_event_locked(conn, kind, payload)
                conn.commit()
                row = conn.execute(
                    "SELECT hash, payload FROM events"
                    " ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
        if row is None or row[0] != digest:
            return False
        return json.loads(row[1]) == payload

    def list_events(self, limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload, prev_hash, hash"
                    " FROM events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            finally:
                conn.close()
        return rows

    def verify_chain(self):
        if self._missing():
            return True, 0
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash"
                    " FROM events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        result = store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)
        return result["ok"], len(rows)

    def count(self, table, where="", args=()):
        if self._missing():
            return 0
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM %s %s" % (table, where),
                    args).fetchone()
            finally:
                conn.close()
        return row[0] if row else 0

    def get_row(self, table, rid):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM %s WHERE id = ?" % table,
                    (rid,)).fetchone()
            finally:
                conn.close()
        return row

    def next_id(self, conn, table, prefix):
        row = conn.execute(
            "SELECT id FROM %s WHERE id LIKE ?"
            " ORDER BY id DESC LIMIT 1" % table,
            (prefix + "-%",)).fetchone()
        n = int(row[0].split("-")[1]) + 1 if row else 1
        return "%s-%04d" % (prefix, n)


# Reentrant: the action handlers hold the lock while calling
# store.count() / verify_chain(), which take it again.
_LOCK = threading.RLock()


def _last_event_payload(conn):
    row = conn.execute(
        "SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None

# ---------------------------------------------------------------------------
# Import parsers: JSON / PO / XLIFF (own stdlib code, no new dependency)
# ---------------------------------------------------------------------------

def _flatten_json(obj, prefix=""):
    """Flatten a nested JSON map into {dotted.key: text}. Values
    must be strings; anything else is refused with the key name."""
    out = {}
    if not isinstance(obj, dict):
        raise _Refusal("the JSON file must hold an object mapping"
                       " keys to texts")
    for key, value in obj.items():
        if not isinstance(key, str) or not key:
            raise _Refusal("JSON keys must be non-empty strings")
        full = (prefix + "." + key) if prefix else key
        if isinstance(value, dict):
            out.update(_flatten_json(value, full))
        elif isinstance(value, str):
            out[full] = value
        else:
            raise _Refusal("JSON value for key %s must be a string,"
                           " not %s" % (full, type(value).__name__))
    return out


def _parse_json_bytes(raw):
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _Refusal("the JSON file does not parse (%s)" % exc)
    pairs = _flatten_json(obj)
    if not pairs:
        raise _Refusal("the JSON file holds no keys")
    return pairs


def _po_unescape(text):
    """Unescape a PO string literal (\" \\n \\t \\\" \\\\)."""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _po_parse_quoted(line):
    """Parse one PO quoted string ("...") — returns the inner text
    or None when the line is not a quoted string."""
    line = line.strip()
    if len(line) < 2 or not line.startswith('"'):
        return None
    if not line.endswith('"'):
        raise _Refusal("a PO string is not closed on its line: %s"
                       % line[:60])
    return _po_unescape(line[1:-1])


def _parse_po_bytes(raw):
    """Parse a gettext PO file into {key: text}. msgid is the key
    (Tolgee's convention); msgctxt becomes part of the key
    (ctxt + "|" + msgid). The header (empty msgid) is skipped.
    Multiline strings are joined. Returns the pairs."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Refusal("the PO file is not valid UTF-8 (%s)" % exc)
    pairs = {}
    msgid = None
    msgstr = None
    ctxt = None
    field = None  # "msgid", "msgstr", "msgctxt" or None

    def flush():
        nonlocal msgid, msgstr, ctxt, field
        if msgid is not None and msgid != "":
            key = (ctxt + "|" + msgid) if ctxt else msgid
            pairs[key] = msgstr if msgstr is not None else ""
        msgid = None
        msgstr = None
        ctxt = None
        field = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid_plural"):
            raise _Refusal("plural PO entries (msgid_plural) are not"
                           " supported — use one key per form")
        if line.startswith("msgid"):
            # A new entry starts here — flush the previous one, but
            # only when a msgid is pending: a msgctxt belongs to the
            # entry its msgid is about to start, so a flush here
            # must not wipe a ctxt just seen.
            if msgid is not None:
                flush()
            field = "msgid"
            msgid = _po_parse_quoted(line[5:].strip()) or ""
            continue
        if line.startswith("msgctxt"):
            # A new entry starts at its msgctxt — flush the previous
            # one when it has a msgid.
            if msgid is not None:
                flush()
            field = "msgctxt"
            ctxt = _po_parse_quoted(line[7:].strip()) or ""
            continue
        if line.startswith("msgstr"):
            field = "msgstr"
            msgstr = _po_parse_quoted(line[6:].strip()) or ""
            continue
        quoted = _po_parse_quoted(line)
        if quoted is None:
            raise _Refusal("an unrecognised PO line: %s" % line[:60])
        if field == "msgid":
            msgid = (msgid or "") + quoted
        elif field == "msgstr":
            msgstr = (msgstr or "") + quoted
        elif field == "msgctxt":
            ctxt = (ctxt or "") + quoted
        else:
            raise _Refusal("a PO continuation string with no field"
                           " to continue")
    flush()
    if not pairs:
        raise _Refusal("the PO file holds no entries")
    return pairs


def _local_name(tag):
    """Strip an XML namespace from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xliff_bytes(raw):
    """Parse an XLIFF 1.2 file into ({key: text}, target_language).
    Each <trans-unit id="key"> contributes its <target> text; a
    unit with no <target> is skipped (not an error). The language
    comes from the <file> element's target-language attribute when
    present. ElementTree does not resolve external entities, so no
    external DTD content is ever fetched. Returns (pairs, lang)."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise _Refusal("the XLIFF file does not parse (%s)" % exc)
    if _local_name(root.tag) != "xliff":
        raise _Refusal("the XLIFF file's root element is not <xliff>")
    pairs = {}
    lang = None
    for file_el in root.iter():
        if _local_name(file_el.tag) != "file":
            continue
        target_lang = file_el.get("target-language")
        if target_lang and lang is None:
            lang = target_lang
        for unit in file_el.iter():
            if _local_name(unit.tag) != "trans-unit":
                continue
            key = unit.get("id")
            if not key:
                raise _Refusal("an XLIFF <trans-unit> has no id")
            target = None
            for child in unit:
                if _local_name(child.tag) == "target":
                    target = child.text or ""
            if target is None:
                continue
            pairs[key] = target
    if not pairs:
        raise _Refusal("the XLIFF file holds no translated units")
    return pairs, lang


def _read_import_file(path):
    """Read an operator-supplied import file (at most 2 MB) and
    return (format, raw_bytes). The format comes from the file
    extension (.json / .po / .xliff / .xlf)."""
    path = _clean_str(path, "file", limit=_MAX_PATH)
    suffix = Path(path).suffix.lower()
    fmt = {".json": "json", ".po": "po",
           ".xliff": "xliff", ".xlf": "xliff"}.get(suffix)
    if fmt is None:
        raise _Refusal("the import file must end in .json, .po,"
                       " .xliff or .xlf")
    try:
        size = Path(path).stat().st_size
    except OSError as exc:
        raise _Refusal("cannot read the import file %s (%s)"
                       % (path, exc))
    if size > _MAX_IMPORT_BYTES:
        raise _Refusal("the import file is %d bytes — at most 2 MB"
                       % size)
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise _Refusal("cannot read the import file %s (%s)"
                       % (path, exc))
    return fmt, raw


# ---------------------------------------------------------------------------
# Export writers: JSON / PO / XLIFF (own stdlib code)
# ---------------------------------------------------------------------------

def _po_escape(text):
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t")
            .replace("\r", "\\r"))


def _export_json(pairs):
    return (json.dumps(pairs, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def _export_po(pairs, project_name):
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: %s\\n"' % project_name,
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for key in sorted(pairs):
        lines.append('msgid "%s"' % _po_escape(key))
        lines.append('msgstr "%s"' % _po_escape(pairs[key]))
        lines.append("")
    return ("\n".join(lines)).encode("utf-8")


def _xml_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _export_xliff(pairs, base_pairs, base_lang, lang):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:'
           'document:1.2">',
           '  <file source-language="%s" target-language="%s"'
           ' datatype="plaintext">' % (_xml_escape(base_lang),
                                       _xml_escape(lang)),
           '    <body>']
    for key in sorted(pairs):
        source = base_pairs.get(key, key)
        out.append('      <trans-unit id="%s">'
                   % _xml_escape(key))
        out.append('        <source>%s</source>'
                   % _xml_escape(source))
        out.append('        <target>%s</target>'
                   % _xml_escape(pairs[key]))
        out.append('      </trans-unit>')
    out.append('    </body>')
    out.append('  </file>')
    out.append('</xliff>')
    return ("\n".join(out) + "\n").encode("utf-8")

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _resolve_project(store, conn, project):
    """Resolve a project by id (PRJ-0001) or by name."""
    project = _clean_str(project, "project", limit=_MAX_DESC)
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project,)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = ?",
            (project,)).fetchone()
    if row is None:
        raise _Refusal("no project %r — list projects to see the"
                       " ones that exist" % project)
    return row


def _resolve_key(store, conn, project_id, key):
    """Resolve a key by id (KEY-0001) or by name within a project."""
    key = _clean_str(key, "key", limit=_MAX_KEY)
    row = conn.execute(
        "SELECT * FROM keys WHERE project_id = ? AND id = ?",
        (project_id, key)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM keys WHERE project_id = ? AND name = ?",
            (project_id, key)).fetchone()
    if row is None:
        raise _Refusal("no key %r in that project — list the"
                       " project's keys to see the ones that exist"
                       % key)
    return row


def _project_dict(row, store, conn):
    keys = conn.execute(
        "SELECT COUNT(*) FROM keys WHERE project_id = ?",
        (row[0],)).fetchone()[0]
    langs = conn.execute(
        "SELECT COUNT(DISTINCT language) FROM translations"
        " WHERE project_id = ?", (row[0],)).fetchone()[0]
    return {"id": row[0], "name": row[1], "base_language": row[2],
            "description": row[3], "keys": keys,
            "languages": langs, "created": row[4]}


def _do_project(store, kind, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    kind = (kind or "list").strip().lower()
    with _LOCK:
        conn = store._connect()
        try:
            if kind == "add":
                name = _clean_str(args.get("name"), "name",
                                  limit=_MAX_DESC)
                base = _clean_lang(args.get("base_language"),
                                   "base_language")
                desc = _clean_str(args.get("description"),
                                  "description", limit=_MAX_DESC,
                                  required=False)
                if store.count("projects") >= _MAX_PROJECTS:
                    raise _Refusal("at most %d projects"
                                   % _MAX_PROJECTS)
                if conn.execute(
                        "SELECT 1 FROM projects WHERE name = ?",
                        (name,)).fetchone():
                    raise _Refusal("a project named %r already"
                                   " exists" % name)
                pid = store.next_id(conn, "projects", "PRJ")
                conn.execute(
                    "INSERT INTO projects (id, name, base_language,"
                    " description, created) VALUES (?, ?, ?, ?, ?)",
                    (pid, name, base, desc, time.time()))
                store._append_event_locked(
                    conn, "project.add",
                    {"project": pid, "name": name,
                     "base_language": base})
                conn.commit()
                row = _project_dict(_resolve_project(store, conn, pid),
                                    store, conn)
                return ("ok", "added project %s (%s, base language"
                        " %s)" % (pid, name, base), {"project": row})
            if kind == "list":
                limit = _clean_limit(args.get("limit"))
                rows = conn.execute(
                    "SELECT * FROM projects ORDER BY name LIMIT ?",
                    (limit,)).fetchall()
                return ("ok", "%d project(s)" % len(rows),
                        {"projects": [_project_dict(r, store, conn)
                                      for r in rows]})
            if kind == "get":
                row = _resolve_project(store, conn, args.get("project"))
                return ("ok", "project %s" % row[0],
                        {"project": _project_dict(row, store, conn)})
            if kind == "remove":
                row = _resolve_project(store, conn, args.get("project"))
                n_keys = conn.execute(
                    "SELECT COUNT(*) FROM keys WHERE project_id = ?",
                    (row[0],)).fetchone()[0]
                n_trans = conn.execute(
                    "SELECT COUNT(*) FROM translations"
                    " WHERE project_id = ?", (row[0],)).fetchone()[0]
                conn.execute("DELETE FROM translations"
                             " WHERE project_id = ?", (row[0],))
                conn.execute("DELETE FROM keys WHERE project_id = ?",
                             (row[0],))
                conn.execute("DELETE FROM projects WHERE id = ?",
                             (row[0],))
                store._append_event_locked(
                    conn, "project.remove",
                    {"project": row[0], "name": row[1],
                     "keys": n_keys, "translations": n_trans})
                conn.commit()
                return ("ok", "removed project %s (%s) with %d key(s)"
                        " and %d translation(s)"
                        % (row[0], row[1], n_keys, n_trans), None)
            raise _Refusal("unknown project kind %r — add, list, get"
                           " or remove" % kind)
        finally:
            conn.close()


def _key_dict(row, store, conn):
    n_trans = conn.execute(
        "SELECT COUNT(*) FROM translations WHERE key_id = ?",
        (row[0],)).fetchone()[0]
    return {"id": row[0], "name": row[2], "description": row[3],
            "tags": row[4], "translations": n_trans,
            "created": row[5]}


def _do_key(store, kind, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    kind = (kind or "list").strip().lower()
    with _LOCK:
        conn = store._connect()
        try:
            project = _resolve_project(store, conn, args.get("project"))
            if kind == "add":
                name = _clean_key_name(args.get("name"))
                desc = _clean_str(args.get("description"),
                                  "description", limit=_MAX_DESC,
                                  required=False)
                tags = _clean_str(args.get("tags"), "tags",
                                  limit=_MAX_TAGS, required=False)
                if store.count("keys",
                               "WHERE project_id = ?",
                               (project[0],)) >= _MAX_KEYS_PER_PROJECT:
                    raise _Refusal("at most %d keys per project"
                                   % _MAX_KEYS_PER_PROJECT)
                if conn.execute(
                        "SELECT 1 FROM keys WHERE project_id = ?"
                        " AND name = ?", (project[0], name)).fetchone():
                    raise _Refusal("key %r already exists in project"
                                   " %s" % (name, project[1]))
                kid = store.next_id(conn, "keys", "KEY")
                conn.execute(
                    "INSERT INTO keys (id, project_id, name,"
                    " description, tags, created)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (kid, project[0], name, desc, tags, time.time()))
                store._append_event_locked(
                    conn, "key.add",
                    {"project": project[0], "key": kid, "name": name})
                conn.commit()
                row = _resolve_key(store, conn, project[0], kid)
                return ("ok", "added key %s (%s) to project %s"
                        % (kid, name, project[1]),
                        {"key": _key_dict(row, store, conn)})
            if kind == "list":
                limit = _clean_limit(args.get("limit"))
                rows = conn.execute(
                    "SELECT * FROM keys WHERE project_id = ?"
                    " ORDER BY name LIMIT ?", (project[0], limit)
                    ).fetchall()
                return ("ok", "%d key(s) in project %s"
                        % (len(rows), project[1]),
                        {"keys": [_key_dict(r, store, conn)
                                  for r in rows]})
            if kind == "get":
                row = _resolve_key(store, conn, project[0],
                                   args.get("key"))
                trans = conn.execute(
                    "SELECT language, text, source FROM translations"
                    " WHERE key_id = ? ORDER BY language",
                    (row[0],)).fetchall()
                out = _key_dict(row, store, conn)
                out["translations"] = {
                    lang: {"text": text, "source": src}
                    for lang, text, src in trans}
                return ("ok", "key %s" % row[0], {"key": out})
            if kind == "remove":
                row = _resolve_key(store, conn, project[0],
                                   args.get("key"))
                n_trans = conn.execute(
                    "SELECT COUNT(*) FROM translations WHERE key_id = ?",
                    (row[0],)).fetchone()[0]
                conn.execute("DELETE FROM translations"
                             " WHERE key_id = ?", (row[0],))
                conn.execute("DELETE FROM keys WHERE id = ?",
                             (row[0],))
                store._append_event_locked(
                    conn, "key.remove",
                    {"project": project[0], "key": row[0],
                     "name": row[2], "translations": n_trans})
                conn.commit()
                return ("ok", "removed key %s (%s) with %d"
                        " translation(s)"
                        % (row[0], row[2], n_trans), None)
            raise _Refusal("unknown key kind %r — add, list, get or"
                           " remove" % kind)
        finally:
            conn.close()


def _do_translation(store, kind, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    kind = (kind or "get").strip().lower()
    with _LOCK:
        conn = store._connect()
        try:
            project = _resolve_project(store, conn, args.get("project"))
            if kind == "set":
                row = _resolve_key(store, conn, project[0],
                                   args.get("key"))
                lang = _clean_lang(args.get("language"))
                text = _clean_str(args.get("text"), "text",
                                  limit=_MAX_TEXT)
                if (lang != project[2]
                        and conn.execute(
                            "SELECT COUNT(DISTINCT language)"
                            " FROM translations WHERE project_id = ?",
                            (project[0],)).fetchone()[0]
                        >= _MAX_LANGUAGES_PER_PROJECT):
                    raise _Refusal("at most %d languages per project"
                                   % _MAX_LANGUAGES_PER_PROJECT)
                conn.execute(
                    "INSERT INTO translations (project_id, key_id,"
                    " language, text, source, source_hash, updated)"
                    " VALUES (?, ?, ?, ?, 'human', NULL, ?)"
                    " ON CONFLICT(project_id, key_id, language)"
                    " DO UPDATE SET text = excluded.text,"
                    " source = 'human', source_hash = NULL,"
                    " updated = excluded.updated",
                    (project[0], row[0], lang, text, time.time()))
                store._append_event_locked(
                    conn, "translation.set",
                    {"project": project[0], "key": row[0],
                     "language": lang, "source": "human"})
                conn.commit()
                return ("ok", "set %s translation of key %s in"
                        " project %s" % (lang, row[2], project[1]),
                        None)
            if kind == "get":
                row = _resolve_key(store, conn, project[0],
                                   args.get("key"))
                lang = _clean_lang(args.get("language"))
                trow = conn.execute(
                    "SELECT text, source, source_hash, updated"
                    " FROM translations WHERE project_id = ?"
                    " AND key_id = ? AND language = ?",
                    (project[0], row[0], lang)).fetchone()
                if trow is None:
                    raise _Refusal("no %s translation for key %s in"
                                   " project %s — set it, or use the"
                                   " missing kind to find gaps"
                                   % (lang, row[2], project[1]))
                return ("ok", "%s translation of %s"
                        % (lang, row[2]),
                        {"translation": {"key": row[2],
                                         "language": lang,
                                         "text": trow[0],
                                         "source": trow[1],
                                         "source_hash": trow[2],
                                         "updated": trow[3]}})
            if kind == "list":
                limit = _clean_limit(args.get("limit"))
                lang = args.get("language")
                if lang is not None:
                    lang = _clean_lang(lang)
                    rows = conn.execute(
                        "SELECT k.name, t.text, t.source, t.updated"
                        " FROM translations t JOIN keys k"
                        " ON k.id = t.key_id"
                        " WHERE t.project_id = ? AND t.language = ?"
                        " ORDER BY k.name LIMIT ?",
                        (project[0], lang, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT k.name, t.language, t.text, t.source,"
                        " t.updated FROM translations t JOIN keys k"
                        " ON k.id = t.key_id"
                        " WHERE t.project_id = ?"
                        " ORDER BY k.name, t.language LIMIT ?",
                        (project[0], limit)).fetchall()
                out = []
                for r in rows:
                    if lang is not None:
                        out.append({"key": r[0], "language": lang,
                                    "text": r[1], "source": r[2],
                                    "updated": r[3]})
                    else:
                        out.append({"key": r[0], "language": r[1],
                                    "text": r[2], "source": r[3],
                                    "updated": r[4]})
                return ("ok", "%d translation(s)" % len(out),
                        {"translations": out})
            if kind == "missing":
                lang = _clean_lang(args.get("language"))
                limit = _clean_limit(args.get("limit"))
                rows = conn.execute(
                    "SELECT k.name FROM keys k WHERE k.project_id = ?"
                    " AND NOT EXISTS (SELECT 1 FROM translations t"
                    " WHERE t.key_id = k.id AND t.language = ?)"
                    " ORDER BY k.name LIMIT ?",
                    (project[0], lang, limit)).fetchall()
                return ("ok", "%d key(s) missing a %s translation"
                        % (len(rows), lang),
                        {"missing": [r[0] for r in rows]})
            raise _Refusal("unknown translation kind %r — set, get,"
                           " list or missing" % kind)
        finally:
            conn.close()


def _translate_one(store, conn, project, row, source_lang, target,
                   _opener):
    """Machine-translate one key's source-language text into one
    target language. Returns (text, source_hash, error)."""
    src = conn.execute(
        "SELECT text FROM translations WHERE project_id = ?"
        " AND key_id = ? AND language = ?",
        (project[0], row[0], source_lang)).fetchone()
    if src is None:
        return None, None, ("no %s text for key %s — set the"
                            " %s translation first"
                            % (source_lang, row[2], source_lang))
    source_text = src[0]
    prompt = ("Translate this UI string from %s to %s. Return ONLY"
              " the translation, with no explanation, no quotes and"
              " no markdown.\n\n%s"
              % (source_lang, target, source_text))
    from .prompt_tune import _lm_call as _gemini_call
    answer, err = _gemini_call(prompt, None, _opener=_opener)
    if err:
        return None, None, err
    if not answer or not answer.strip():
        return None, None, "the LM returned no translation"
    text = answer.strip()
    if len(text) > _MAX_TEXT:
        return None, None, ("the LM returned %d chars — at most %d"
                            % (len(text), _MAX_TEXT))
    return text, hashlib.sha256(
        source_text.encode("utf-8")).hexdigest(), None


def _do_translate(store, args, _opener=None, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    with _LOCK:
        conn = store._connect()
        try:
            project = _resolve_project(store, conn, args.get("project"))
            source_lang = args.get("source_language")
            source_lang = (_clean_lang(source_lang, "source_language")
                           if source_lang else project[2])
            targets = _clean_lang_list(args.get("target_languages"))
            if source_lang in targets:
                raise _Refusal("the source language %s is also a"
                               " target — it is already translated"
                               % source_lang)
            if args.get("key"):
                rows = [_resolve_key(store, conn, project[0],
                                     args.get("key"))]
            else:
                limit = _MAX_TRANSLATE_KEYS
                rows = conn.execute(
                    "SELECT * FROM keys WHERE project_id = ?"
                    " AND id IN (SELECT DISTINCT key_id FROM"
                    " translations WHERE project_id = ?"
                    " AND language = ?)"
                    " AND NOT EXISTS (SELECT 1 FROM translations t"
                    " WHERE t.key_id = keys.id AND t.language IN"
                    " (%s)) ORDER BY name LIMIT ?"
                    % ",".join("?" * len(targets)),
                    (project[0], project[0], source_lang,
                     *targets, limit)).fetchall()
            if len(rows) > _MAX_TRANSLATE_KEYS:
                raise _Refusal("at most %d keys per translate call"
                               % _MAX_TRANSLATE_KEYS)
            if not rows:
                return ("ok", "nothing to translate — every key"
                        " already has a translation in %s"
                        % ", ".join(targets),
                        {"translated": {}, "skipped": [], "failed": []})
            translated = {lang: [] for lang in targets}
            skipped = []
            failed = []
            for row in rows:
                for target in targets:
                    exists = conn.execute(
                        "SELECT 1 FROM translations"
                        " WHERE project_id = ? AND key_id = ?"
                        " AND language = ?",
                        (project[0], row[0], target)).fetchone()
                    if exists:
                        skipped.append("%s:%s" % (row[2], target))
                        continue
                    text, source_hash, err = _translate_one(
                        store, conn, project, row, source_lang,
                        target, _opener)
                    if err:
                        failed.append("%s:%s (%s)"
                                      % (row[2], target, err))
                        continue
                    conn.execute(
                        "INSERT INTO translations (project_id, key_id,"
                        " language, text, source, source_hash,"
                        " updated) VALUES (?, ?, ?, ?, 'machine', ?, ?)",
                        (project[0], row[0], target, text,
                         source_hash, time.time()))
                    store._append_event_locked(
                        conn, "translation.translate",
                        {"project": project[0], "key": row[0],
                         "language": target, "source": "machine",
                         "source_hash": source_hash})
                    translated[target].append(row[2])
            conn.commit()
            n_done = sum(len(v) for v in translated.values())
            return ("ok", "translated %d key(s) into %s (%d skipped"
                    " already translated, %d failed)"
                    % (n_done, ", ".join(targets), len(skipped),
                       len(failed)),
                    {"translated": translated, "skipped": skipped,
                     "failed": failed})
        finally:
            conn.close()


def _do_import(store, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    with _LOCK:
        conn = store._connect()
        try:
            project = _resolve_project(store, conn, args.get("project"))
            fmt, raw = _read_import_file(args.get("file"))
            lang = args.get("language")
            if fmt == "json":
                pairs = _parse_json_bytes(raw)
                lang = _clean_lang(lang)
            elif fmt == "po":
                pairs = _parse_po_bytes(raw)
                lang = _clean_lang(lang)
            else:
                pairs, file_lang = _parse_xliff_bytes(raw)
                if lang:
                    lang = _clean_lang(lang)
                elif file_lang:
                    lang = _clean_lang(file_lang, "target-language")
                else:
                    raise _Refusal("the XLIFF file has no"
                                   " target-language and no language"
                                   " was given")
            created = updated = skipped = 0
            for key_name, text in sorted(pairs.items()):
                key_name = _clean_key_name(key_name)
                if len(text) > _MAX_TEXT:
                    skipped += 1
                    continue
                row = conn.execute(
                    "SELECT id FROM keys WHERE project_id = ?"
                    " AND name = ?", (project[0], key_name)).fetchone()
                if row is None:
                    if store.count("keys", "WHERE project_id = ?",
                                   (project[0],)) >= _MAX_KEYS_PER_PROJECT:
                        skipped += 1
                        continue
                    kid = store.next_id(conn, "keys", "KEY")
                    conn.execute(
                        "INSERT INTO keys (id, project_id, name,"
                        " description, tags, created)"
                        " VALUES (?, ?, ?, NULL, NULL, ?)",
                        (kid, project[0], key_name, time.time()))
                    created += 1
                else:
                    kid = row[0]
                existing = conn.execute(
                    "SELECT text FROM translations"
                    " WHERE project_id = ? AND key_id = ?"
                    " AND language = ?",
                    (project[0], kid, lang)).fetchone()
                if existing and existing[0] == text:
                    skipped += 1
                    continue
                conn.execute(
                    "INSERT INTO translations (project_id, key_id,"
                    " language, text, source, source_hash, updated)"
                    " VALUES (?, ?, ?, ?, 'import', NULL, ?)"
                    " ON CONFLICT(project_id, key_id, language)"
                    " DO UPDATE SET text = excluded.text,"
                    " source = 'import', source_hash = NULL,"
                    " updated = excluded.updated",
                    (project[0], kid, lang, text, time.time()))
                updated += 1
            store._append_event_locked(
                conn, "import",
                {"project": project[0], "language": lang,
                 "format": fmt, "created": created,
                 "updated": updated, "skipped": skipped})
            conn.commit()
            return ("ok", "imported %s (%s): %d key(s) created,"
                    " %d translation(s) written, %d skipped"
                    % (fmt, lang, created, updated, skipped),
                    {"format": fmt, "language": lang,
                     "created": created, "updated": updated,
                     "skipped": skipped})
        finally:
            conn.close()


def _do_export(store, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    with _LOCK:
        conn = store._connect()
        try:
            project = _resolve_project(store, conn, args.get("project"))
            lang = _clean_lang(args.get("language"))
            fmt = (args.get("format") or "json").strip().lower()
            if fmt not in _FORMATS:
                raise _Refusal("format must be one of %s"
                               % ", ".join(_FORMATS))
            rows = conn.execute(
                "SELECT k.name, t.text FROM translations t"
                " JOIN keys k ON k.id = t.key_id"
                " WHERE t.project_id = ? AND t.language = ?"
                " ORDER BY k.name LIMIT ?",
                (project[0], lang, _MAX_EXPORT_KEYS + 1)).fetchall()
            if len(rows) > _MAX_EXPORT_KEYS:
                raise _Refusal("at most %d keys per export"
                               % _MAX_EXPORT_KEYS)
            pairs = {name: text for name, text in rows}
            if fmt == "json":
                data = _export_json(pairs)
            elif fmt == "po":
                data = _export_po(pairs, project[1])
            else:
                base_rows = conn.execute(
                    "SELECT k.name, t.text FROM translations t"
                    " JOIN keys k ON k.id = t.key_id"
                    " WHERE t.project_id = ? AND t.language = ?"
                    " ORDER BY k.name",
                    (project[0], project[2])).fetchall()
                data = _export_xliff(pairs, dict(base_rows),
                                     project[2], lang)
            path = args.get("file")
            if path is None:
                folder = Path(DATA_DIR) / "exports"
                folder.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", project[1])
                ext = {"json": "json", "po": "po",
                       "xliff": "xlf"}[fmt]
                path = str(folder / ("%s_%s.%s" % (safe, lang, ext)))
            else:
                path = _clean_str(path, "file", limit=_MAX_PATH)
                if Path(path).suffix.lower() not in (
                        ".json", ".po", ".xliff", ".xlf"):
                    raise _Refusal("the export file must end in"
                                   " .json, .po, .xliff or .xlf")
            try:
                Path(path).write_bytes(data)
            except OSError as exc:
                raise _Refusal("cannot write the export file %s"
                               " (%s)" % (path, exc))
            store._append_event_locked(
                conn, "export",
                {"project": project[0], "language": lang,
                 "format": fmt, "keys": len(pairs), "file": path})
            conn.commit()
            return ("ok", "exported %d %s translation(s) (%s) to %s"
                    % (len(pairs), lang, fmt, path),
                    {"file": path, "format": fmt, "language": lang,
                     "keys": len(pairs)})
        finally:
            conn.close()


def _do_status(store, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    if store._missing():
        return ("ok", "no localization projects yet — add a project"
                " to start",
                {"projects": 0, "keys": 0, "translations": 0,
                 "languages": 0, "chain_ok": True, "events": 0,
                 "chain_tip": "the store file does not exist yet"})
    ok, n_events = store.verify_chain()
    with _LOCK:
        conn = store._connect()
        try:
            n_langs = conn.execute(
                "SELECT COUNT(DISTINCT language)"
                " FROM translations").fetchone()[0]
        finally:
            conn.close()
    return ("ok", "%d project(s), %d key(s), %d translation(s)"
            % (store.count("projects"), store.count("keys"),
               store.count("translations")),
            {"projects": store.count("projects"),
             "keys": store.count("keys"),
             "translations": store.count("translations"),
             "languages": n_langs,
             "chain_ok": ok, "events": n_events})


def _do_events(store, args, _db_path=None):
    store = store or _TranslationsStore(_db_path)
    kind = (args.get("kind") or "list").strip().lower()
    if kind == "verify":
        ok, n = store.verify_chain()
        if not ok:
            return ("error", "the ledger chain is BROKEN (%d event(s)"
                    " — re-check before trusting any of it)" % n,
                    {"chain_ok": False, "events": n})
        return ("ok", "the ledger chain is intact (%d event(s))"
                % n, {"chain_ok": True, "events": n})
    if kind == "list":
        limit = _clean_limit(args.get("limit"))
        rows = store.list_events(limit)
        return ("ok", "%d event(s)" % len(rows),
                {"events": [{"id": r[0], "ts": r[1], "kind": r[2],
                            "payload": json.loads(r[3]),
                            "prev_hash": r[4][:12],
                            "hash": r[5][:12]} for r in rows]})
    raise _Refusal("unknown events kind %r — list or verify" % kind)


def _handler(action=None, kind=None, project=None, name=None, key=None,
             language=None, text=None, description=None, tags=None,
             base_language=None, target_languages=None,
             source_language=None, file=None, format=None, limit=None,
             detail=None, _store=None, _opener=None, _db_path=None):
    """The funnel entry point for `translations`.

    action: project | key | translation | translate | import |
    export | status | events. kind: the sub-action (add/list/get/
    remove/set/missing/verify). The rest are the named arguments.
    """
    store = _store or _TranslationsStore(_db_path)
    args = {"kind": kind, "project": project, "name": name,
            "key": key, "language": language, "text": text,
            "description": description, "tags": tags,
            "base_language": base_language,
            "target_languages": target_languages,
            "source_language": source_language, "file": file,
            "format": format, "limit": limit}
    try:
        action = _clean_str(action, "action", limit=20)
        if action not in _ACTIONS:
            raise _Refusal("unknown action %r — %s"
                           % (action, ", ".join(_ACTIONS)))
        if action == "project":
            status, msg, data = _do_project(store, kind, args, _db_path)
        elif action == "key":
            status, msg, data = _do_key(store, kind, args, _db_path)
        elif action == "translation":
            status, msg, data = _do_translation(store, kind, args,
                                                _db_path)
        elif action == "translate":
            status, msg, data = _do_translate(store, args,
                                              _opener=_opener,
                                              _db_path=_db_path)
        elif action == "import":
            status, msg, data = _do_import(store, args, _db_path)
        elif action == "export":
            status, msg, data = _do_export(store, args, _db_path)
        elif action == "status":
            status, msg, data = _do_status(store, args, _db_path)
        elif action == "events":
            status, msg, data = _do_events(store, args, _db_path)
        else:  # pragma: no cover - guarded by the check above
            raise _Refusal("unknown action %r" % action)
    except _Refusal as exc:
        status, msg, data = "error", str(exc), None
    except sqlite3.Error as exc:
        status, msg, data = "error", "the store failed: %s" % exc, None
    if status == "error":
        return {"status": "error", "message": msg}
    out = {"status": "ok", "message": msg}
    if data is not None:
        out.update(data)
    return out


def register_all(registry):
    registry.register(
        name="translations",
        description=("Manage UI string localization (keys, "
                     "translations and locales): projects, keys, "
                     "translations per language, import/export in "
                     "JSON/PO/XLIFF, and machine translation with "
                     "the product's own Gemini key."),
        handler=_handler,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "project, key, translation,"
                                          " translate, import, export,"
                                          " status or events",
                           "enum": list(_ACTIONS)},
                "kind": {"type": "string",
                         "description": "the sub-action: add, list,"
                                        " get, remove, set, missing"
                                        " or verify"},
                "project": {"type": "string",
                            "description": "a project id (PRJ-0001)"
                                           " or a project name"},
                "name": {"type": "string",
                         "description": "a project or key name"},
                "key": {"type": "string",
                        "description": "a key id (KEY-0001) or a"
                                       " dotted key name"
                                       " (e.g. home.title)"},
                "language": {"type": "string",
                             "description": "a language code"
                                            " (en, hi, de, pt-BR)"},
                "text": {"type": "string",
                         "description": "a translation text"},
                "description": {"type": "string",
                                "description": "a project or key"
                                               " description"},
                "tags": {"type": "string",
                         "description": "comma-separated tags for"
                                        " a key"},
                "base_language": {"type": "string",
                                  "description": "the base language"
                                                 " of a project"},
                "target_languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "the languages to translate"
                                   " into (at most 10)"},
                "source_language": {"type": "string",
                                    "description": "the language to"
                                                   " translate from"
                                                   " (defaults to"
                                                   " the project's"
                                                   " base language)"},
                "file": {"type": "string",
                         "description": "the import file to read or"
                                        " the export file to write"},
                "format": {"type": "string",
                           "description": "json, po or xliff"},
                "limit": {"type": "integer",
                          "description": "the maximum rows to return"
                                         " (1-100)"},
            },
            "required": ["action"],
        },
    )
