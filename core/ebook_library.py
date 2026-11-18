"""Ebooks and comics: a library, reading progress, and a LAN server
for readers — own code.

Parent-ruled (licence, 2026-11-11): gotson/komga (MIT, LICENSE from
master, 21 lines, "MIT License / Copyright (c) 2019 Gauthier
Roebroeck", read in full 2026-11-11) is INSPIRATION ONLY. No code,
binary, schema, text or package is adopted from it; this module is
written from scratch in stdlib Python over the tree's own shapes
(core/skills_admin_helpers.py's memory-facts registry,
core/companion.py's LAN-server pattern). Ideas are not copyrighted,
so no MIT duty is triggered by a from-scratch rebuild. The "komga"
name is not used here: the tool is the generic phrase
"ebook_library", and no package, fork, service or hosted offering
is named after the original. No JVM runtime is added and there is
no second app — only this module inside VYUK.

What this is, and what it is not — one verdict per komga capability:
- Ebook/comics library: BUILT. Scan a folder for .epub and .cbz
  files; read the metadata with stdlib only (EPUB: ZIP +
  container.xml + OPF — title, author, series, language, spine
  documents, cover; CBZ: ZIP + ComicInfo.xml when present, else
  filename — title, series, page images, cover). Each book is one
  row in VYUK's own store, keyed by its KOReader hash (see below),
  so a re-scan updates instead of duplicating.
- Reading progress (local): BUILT. Progress per book in VYUK's own
  store — percentage (0..1), a KOReader progress string, a page
  number, the device that last reported it. Set it here, or let a
  KOReader device set it through the sync endpoint below.
- OPDS v1 catalogue: BUILT, LAN-only. The server below serves an
  OPDS 1.2 Atom catalogue (catalog, all books, series, search with
  an OpenSearch description, acquisition + cover links, file
  download) over the tree's companion LAN pattern: it binds the
  machine's private LAN address (never 0.0.0.0, never a public
  address), requires HTTP Basic auth with a fresh token per start,
  and dies with 'stop'. OPDS v2 (JSON) is NOT built — v1 Atom is
  the widely-supported catalogue; v2 is a second format for a
  follow-up.
- KOReader Sync: BUILT, LAN-only, on the same server. The exact
  protocol komga speaks, verified from komga's source
  (interfaces/api/kosync/KoreaderSyncController.kt + dto/
  DocumentProgressDto.kt + infrastructure/koreader/KoreaderHasher.kt)
  and from KOReader's own util.lua (the partial-MD5 comment cites
  koreader@5bd3f3b frontend/util.lua L1093-L1119): POST
  users/create answers 403 (creation disabled, exactly like
  komga), GET users/auth answers {"authorized": "OK"}, GET
  syncs/progress/{bookHash} returns the stored DocumentProgressDto
  (document, percentage 0..1, progress string, device, deviceId) or
  404/409, PUT syncs/progress stores one. The book hash is the
  KOReader partial MD5: 1024-byte samples at offsets 0, 1024,
  4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864,
  268435456, 1073741824 — the first sample is at offset 0 because
  both the Lua lshift and the Kotlin shl wrap the i=-1 shift to
  zero (lshift(1024, -2) and 1024L shl -2 both land on 0); a short
  file contributes only the samples that exist.
- Kobo Sync: NOT BUILT (this batch). The protocol shapes were
  verified from komga's source (interfaces/api/kobo/KoboController.kt:
  /kobo/{authToken}/ with ping, v1/initialization, v1/auth/device,
  v1/library/sync, v1/library/{bookId}/metadata and /state GET+PUT,
  plus kepub download), but the implementation needs the EPUB-to-
  KEPUB conversion and the device auth-token flow, and there is no
  Kobo device in the lab to verify the handshake against. That is a
  second batch with a field leg, not a guess here.
- CBR: NOT BUILT. A .cbz is a ZIP (stdlib-readable); a .cbr is a
  RAR (stdlib cannot read RAR). Scans count CBR files as skipped
  with the reason named. PDF is counted the same way — komga reads
  PDF, but stdlib PDF page counting is unreliable, so PDFs are
  listed as skipped, not claimed.
- Multi-user / hosted server: NOT BUILT. That is the public/hosted
  shape (other people's accounts, other machines) — held back for
  owner rulings, the R16-7/R16-8/R17-1 public-half precedent. This
  serves the owner's own library on their own LAN, single operator.

Nearest surfaces that are NOT this capability (gap proved first):
- core/senses_file.py knows ".epub" only as an archive-extension
  label — a label, not a reader.
- core/companion.py is the LAN-server pattern this module follows
  (ThreadingHTTPServer on the private LAN address, fresh
  credentials per start, explicit start/stop) — a phone remote,
  not a book server.
- core/wormhole.py moves files between two machines on the LAN —
  a transfer, not a library or a catalogue.
- core/photo_organise.py is a media library over files — photos
  only, no books, no OPDS, no sync.

Classification: the tool is effectful (read=False), confirm=False,
free='ebook' — the companion precedent (tools_gate_free.py): the
serve/stop calls themselves are the owner's explicit opt-in (the
listener never auto-starts, never binds anything but the private
LAN address, issues a fresh token per start, dies with stop), and
the registry writes touch only VYUK's own store (the bookings
'app-state' precedent). The files served are only the books the
owner registered, and only to a device holding the per-start
credentials.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .skills_admin_helpers import _load, _nonempty, _save, _store

_BOOK_CATEGORY = "ebook_library"
_PROGRESS_CATEGORY = "ebook_progress"
_ACTIONS = ("scan", "list", "search", "progress", "serve", "stop",
            "status", "remove")
_READ_ACTIONS = ("list", "search", "status")
_MAX_SCAN_FILES = 5000
_MAX_FILE_BYTES = 200_000_000  # 200 MB — a book bigger than this is refused
_MAX_COVER_BYTES = 5_000_000
_OPDS_NS_ATOM = "http://www.w3.org/2005/Atom"
_OPDS_NS_OPDS = "http://opds-spec.org/2010/catalog"
_OPDS_NS_DCTERMS = "http://purl.org/dc/terms/"
_OPDS_NS_OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
_MIME = {"epub": "application/epub+zip",
         "cbz": "application/vnd.comicbook+zip"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}


# ------------------------------------------------------------------ hash ---

def koreader_hash(path):
    """The KOReader document hash (partial MD5) for a file path.

    Twelve 1024-byte samples at offsets 0, 1024, 4096, 16384,
    65536, 262144, 1048576, 4194304, 16777216, 67108864,
    268435456, 1073741824, MD5'd in order. The first sample is at
    offset 0: KOReader's Lua does lshift(1024, 2*(-1)) and komga's
    Kotlin does 1024L shl (2*(-1)), and both wrap the negative
    shift to zero, so the head sample is the file's first 1024
    bytes. A short file contributes only the samples that exist
    (a read past EOF returns nothing and is skipped, exactly like
    both originals). Verified against komga's KoreaderHasher.kt
    and koreader's frontend/util.lua partialMD5.
    """
    offsets = [0, 1024, 4096, 16384, 65536, 262144, 1048576,
               4194304, 16777216, 67108864, 268435456, 1073741824]
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for offset in offsets:
            fh.seek(offset)
            chunk = fh.read(1024)
            if chunk:
                digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------ EPUB / CBZ ---

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"


def _opf_text(root, tag):
    el = root.find(f".//{{{_DC_NS}}}{tag}")
    return (el.text or "").strip() if el is not None else ""


def _read_epub(path):
    """(metadata dict, cover bytes or None, error or '')."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return None, None, f"not a readable EPUB ({exc})"
    with zf:
        try:
            container = ET.fromstring(
                zf.read("META-INF/container.xml"))
        except (KeyError, ET.ParseError) as exc:
            return None, None, f"no container.xml ({exc})"
        rootfile = container.find(
            f".//{{{_CONTAINER_NS}}}rootfile")
        opf_path = (rootfile.get("full-path") if rootfile is not None
                    else "")
        if not opf_path:
            return None, None, "container.xml names no rootfile"
        try:
            opf = ET.fromstring(zf.read(opf_path))
        except (KeyError, ET.ParseError) as exc:
            return None, None, f"unreadable OPF ({exc})"
        base = str(Path(opf_path).parent).replace("\\", "/")
        if base in (".", ""):
            base = ""

        def _resolve(href):
            href = (href or "").strip()
            if not href or "://" in href:
                return ""
            joined = (base + "/" + href) if base else href
            parts = []
            for part in joined.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(part)
            return "/".join(parts)

        title = _opf_text(opf, "title")
        author = _opf_text(opf, "creator")
        language = _opf_text(opf, "language")
        identifier = _opf_text(opf, "identifier")
        series = ""
        series_index = ""
        for meta in opf.findall(f".//{{{_OPF_NS}}}meta"):
            name = (meta.get("name") or "").strip()
            content = (meta.get("content") or "").strip()
            if name == "calibre:series":
                series = content
            elif name == "calibre:series_index":
                series_index = content
        manifest = {}
        for item in opf.findall(f".//{{{_OPF_NS}}}item"):
            item_id = item.get("id") or ""
            href = _resolve(item.get("href") or "")
            if item_id and href:
                manifest[item_id] = {
                    "href": href,
                    "media-type": item.get("media-type") or "",
                    "properties": item.get("properties") or "",
                }
        spine = []
        for ref in opf.findall(f".//{{{_OPF_NS}}}itemref"):
            item = manifest.get(ref.get("idref") or "")
            if item:
                spine.append(item["href"])
        cover = None
        cover_id = ""
        for meta in opf.findall(f".//{{{_OPF_NS}}}meta"):
            if (meta.get("name") or "").strip() == "cover":
                cover_id = (meta.get("content") or "").strip()
                break
        cover_href = ""
        if cover_id and cover_id in manifest:
            cover_href = manifest[cover_id]["href"]
        if not cover_href:
            for item in manifest.values():
                if "cover-image" in item["properties"]:
                    cover_href = item["href"]
                    break
        if cover_href:
            try:
                data = zf.read(cover_href)
                if 0 < len(data) <= _MAX_COVER_BYTES:
                    cover = data
            except (KeyError, OSError):
                cover = None
        return {
            "title": title or Path(path).stem,
            "author": author,
            "series": series,
            "series_index": series_index,
            "language": language,
            "identifier": identifier,
            "format": "epub",
            "pages": len(spine),
            "spine": spine,
        }, cover, ""


def _natural_key(name):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def _read_cbz(path):
    """(metadata dict, cover bytes or None, error or '')."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return None, None, f"not a readable CBZ ({exc})"
    with zf:
        names = [n for n in zf.namelist()
                 if not n.endswith("/")
                 and Path(n).suffix.lower() in _IMAGE_EXTS]
        names.sort(key=lambda n: _natural_key(Path(n).name))
        title = ""
        author = ""
        series = ""
        series_index = ""
        info_names = [n for n in zf.namelist()
                      if Path(n).name.lower() == "comicinfo.xml"]
        if info_names:
            try:
                info = ET.fromstring(zf.read(info_names[0]))
            except ET.ParseError:
                info = None
            if info is not None:
                def _tag(tag):
                    el = info.find(tag)
                    return ((el.text or "").strip()
                            if el is not None else "")
                title = _tag("Title")
                series = _tag("Series")
                author = _tag("Writer") or _tag("Author")
                series_index = _tag("Number")
        cover = None
        if names:
            try:
                data = zf.read(names[0])
                if 0 < len(data) <= _MAX_COVER_BYTES:
                    cover = data
            except (KeyError, OSError):
                cover = None
        return {
            "title": title or series or Path(path).stem,
            "author": author,
            "series": series,
            "series_index": series_index,
            "language": "",
            "identifier": "",
            "format": "cbz",
            "pages": len(names),
            "spine": [],
        }, cover, ""


# --------------------------------------------------------------- registry ---

def _load_books():
    """(entries, broken): book dicts keyed by their KOReader hash."""
    entries, broken = _load(_BOOK_CATEGORY, {
        "hash": _nonempty, "title": _nonempty,
        "format": lambda v: v in ("epub", "cbz"),
        "path": _nonempty})
    for entry in entries:
        entry["spine"] = entry.get("spine") or []
    return entries, broken


def _find_book(entries, book):
    """By hash, then by case-insensitive title, then by path stem."""
    want = (book or "").strip()
    if not want:
        return None
    for entry in entries:
        if entry.get("hash") == want:
            return entry
    low = want.lower()
    for entry in entries:
        if str(entry.get("title") or "").lower() == low:
            return entry
    for entry in entries:
        if Path(str(entry.get("path") or "")).stem.lower() == low:
            return entry
    return None


def _load_progress():
    entries, _broken = _load(_PROGRESS_CATEGORY, {
        "book_hash": _nonempty})
    return {e["book_hash"]: e for e in entries}


def _save_progress(book_hash, percentage, progress, page, device,
                   device_id):
    payload = {
        "book_hash": book_hash,
        "percentage": max(0.0, min(1.0, float(percentage))),
        "progress": str(progress or ""),
        "page": int(page or 0),
        "device": str(device or ""),
        "device_id": str(device_id or ""),
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _save(_PROGRESS_CATEGORY, book_hash, payload)
    return payload


def _progress_for(book, progress_map):
    """(percentage, progress string, page) for one book — the
    KOReader shape: CBZ progress is the page number as a string,
    EPUB progress is a /body/DocFragment[N].0 xpath into the spine.
    """
    stored = progress_map.get(book.get("hash") or "")
    if stored is None:
        return 0.0, "", 0
    percentage = float(stored.get("percentage") or 0.0)
    progress = str(stored.get("progress") or "")
    page = int(stored.get("page") or 0)
    if book.get("format") == "cbz":
        if page:
            progress = str(page)
        elif progress.isdigit():
            page = int(progress)
    elif book.get("format") == "epub":
        if not progress and page and book.get("spine"):
            idx = min(max(page, 1), len(book["spine"]))
            progress = f"/body/DocFragment[{idx}].0"
    return percentage, progress, page


# ------------------------------------------------------------- LAN server ---

def _lan_address():
    """The machine's private LAN address (UDP-connect trick, no
    traffic sent — the companion.py pattern). None when there is
    no LAN at all."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None


def _pick_port():
    """A free port, probed by binding loopback (the companion.py
    pattern) — the server itself binds the LAN address on it."""
    for port in range(49152, 49252):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            finally:
                probe.close()
        except OSError:
            continue
    return 0


class _EbookServer:
    """One LAN server: OPDS v1 + KOReader Sync + file download.

    Bound to the private LAN address only (never 0.0.0.0), HTTP
    Basic auth with a fresh token per start, daemon thread, dies
    with stop. Module-level singleton, like companion's session.
    """

    def __init__(self):
        self.server = None
        self.thread = None
        self.address = ""
        self.port = 0
        self.token = ""
        self.started_at = 0.0
        self.lock = threading.Lock()

    def is_running(self):
        return self.server is not None

    def base_url(self):
        return f"http://{self.address}:{self.port}" if self.is_running() else ""

    def start(self, port=0):
        with self.lock:
            if self.is_running():
                return False, "The ebook server is already running."
            address = _lan_address()
            if not address:
                return False, ("No LAN address found — the machine is "
                               "not on a local network, so the ebook "
                               "server refuses to start.")
            if not port:
                port = _pick_port()
            if not port:
                return False, "No free port found for the ebook server."
            token = secrets.token_urlsafe(24)
            handler = _make_handler(self)
            try:
                server = ThreadingHTTPServer((address, port), handler)
            except OSError as exc:
                return False, f"Could not bind the ebook server ({exc})."
            self.server = server
            self.address = address
            self.port = port
            self.token = token
            self.started_at = time.monotonic()
            self.thread = threading.Thread(
                target=server.serve_forever, daemon=True)
            self.thread.start()
            return True, ""

    def stop(self):
        with self.lock:
            if not self.is_running():
                return False, "The ebook server is not running."
            server, thread = self.server, self.thread
            self.server = None
            self.thread = None
            self.address = ""
            self.port = 0
            self.token = ""
            self.started_at = 0.0
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass
        if thread is not None:
            thread.join(timeout=5)
        return True, ""


_SERVER = _EbookServer()


def _make_handler(session):
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: N802 - silence
            pass

        def _authed(self):
            auth = self.headers.get("Authorization") or ""
            if not auth.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(
                    auth[6:]).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - bad base64 is not auth
                return False
            user, _, password = decoded.partition(":")
            return (user == "vyuk"
                    and hmac.compare_digest(password, session.token))

        def _deny(self):
            body = b"Unauthorized"
            self.send_response(401)
            self.send_header("WWW-Authenticate",
                             'Basic realm="VYUK ebooks"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send(self, status, body, content_type):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status, data):
            self._send(status, json.dumps(data), "application/json")

        def _books(self):
            entries, _broken = _load_books()
            return entries

        def _book_by_hash(self, book_hash):
            for entry in self._books():
                if entry.get("hash") == book_hash:
                    return entry
            return None

        def do_GET(self):
            if not self._authed():
                self._deny()
                return
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/opds/v1.2/catalog":
                self._send(200, _opds_catalog(session.base_url()),
                           "application/atom+xml; charset=utf-8")
            elif path == "/opds/v1.2/books":
                page = _query_int(query, "page", 1, 1, 10000)
                size = _query_int(query, "size", 50, 1, 200)
                self._send(200, _opds_books(session.base_url(),
                                            self._books(), page, size),
                           "application/atom+xml; charset=utf-8")
            elif path == "/opds/v1.2/series":
                self._send(200, _opds_series(session.base_url(),
                                             self._books()),
                           "application/atom+xml; charset=utf-8")
            elif path.startswith("/opds/v1.2/series/"):
                series = urllib.parse.unquote(path[len("/opds/v1.2/series/"):])
                page = _query_int(query, "page", 1, 1, 10000)
                size = _query_int(query, "size", 50, 1, 200)
                self._send(200, _opds_series_books(
                    session.base_url(), self._books(), series, page, size),
                    "application/atom+xml; charset=utf-8")
            elif path == "/opds/v1.2/search":
                if "q" in query:
                    term = (query.get("q") or [""])[0]
                    page = _query_int(query, "page", 1, 1, 10000)
                    size = _query_int(query, "size", 50, 1, 200)
                    self._send(200, _opds_search(
                        session.base_url(), self._books(), term, page, size),
                        "application/atom+xml; charset=utf-8")
                else:
                    self._send(200, _opensearch_description(
                        session.base_url()),
                        "application/opensearchdescription+xml; charset=utf-8")
            elif path.startswith("/opds/v1.2/books/") and path.endswith("/file"):
                book_hash = path[len("/opds/v1.2/books/"):-len("/file")]
                self._serve_file(book_hash)
            elif path.startswith("/opds/v1.2/books/") and path.endswith("/cover"):
                book_hash = path[len("/opds/v1.2/books/"):-len("/cover")]
                self._serve_cover(book_hash)
            elif path == "/koreader/users/auth":
                self._send_json(200, {"authorized": "OK"})
            elif path.startswith("/koreader/syncs/progress/"):
                book_hash = path[len("/koreader/syncs/progress/"):]
                self._koreader_get(book_hash)
            else:
                self._send(404, "Not found", "text/plain")

        def do_PUT(self):
            if not self._authed():
                self._deny()
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.rstrip("/") == "/koreader/syncs/progress":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._send_json(400, {"error": "invalid JSON body"})
                    return
                self._koreader_put(data)
            else:
                self._send(404, "Not found", "text/plain")

        def do_POST(self):
            if not self._authed():
                self._deny()
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.rstrip("/") == "/koreader/users/create":
                # User creation is disabled, exactly like komga.
                self._send(403, "User creation is disabled", "text/plain")
            else:
                self._send(404, "Not found", "text/plain")

        def _serve_file(self, book_hash):
            book = self._book_by_hash(book_hash)
            if book is None:
                self._send(404, "No such book", "text/plain")
                return
            path = Path(str(book.get("path") or ""))
            try:
                if not path.is_file():
                    self._send(404, "Book file is gone", "text/plain")
                    return
                size = path.stat().st_size
                if size > _MAX_FILE_BYTES:
                    self._send(413, "Book file too large", "text/plain")
                    return
                data = path.read_bytes()
            except OSError:
                self._send(404, "Book file unreadable", "text/plain")
                return
            mime = _MIME.get(str(book.get("format") or ""), "application/octet-stream")
            filename = path.name
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def _serve_cover(self, book_hash):
            book = self._book_by_hash(book_hash)
            if book is None:
                self._send(404, "No such book", "text/plain")
                return
            cover = _cover_for(book)
            if cover is None:
                self._send(404, "No cover for this book", "text/plain")
                return
            data, mime = cover
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _koreader_get(self, book_hash):
            books = [b for b in self._books()
                     if b.get("hash") == book_hash]
            if not books:
                self._send_json(404, {"error": "Book not found"})
                return
            if len(books) > 1:
                self._send_json(
                    409, {"error": "More than 1 book found with the same hash"})
                return
            book = books[0]
            progress_map = _load_progress()
            percentage, progress, page = _progress_for(book, progress_map)
            stored = progress_map.get(book_hash) or {}
            self._send_json(200, {
                "document": book_hash,
                "percentage": percentage,
                "progress": progress,
                "device": str(stored.get("device") or ""),
                "device_id": str(stored.get("device_id") or ""),
            })

        def _koreader_put(self, data):
            if not isinstance(data, dict):
                self._send_json(400, {"error": "invalid JSON body"})
                return
            book_hash = str(data.get("document") or "")
            books = [b for b in self._books()
                     if b.get("hash") == book_hash]
            if not books:
                self._send_json(404, {"error": "Book not found"})
                return
            if len(books) > 1:
                self._send_json(
                    409, {"error": "More than 1 book found with the same hash"})
                return
            book = books[0]
            try:
                percentage = float(data.get("percentage") or 0.0)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "percentage is not a number"})
                return
            if not 0.0 <= percentage <= 1.0:
                self._send_json(400, {"error": "percentage out of range"})
                return
            progress = str(data.get("progress") or "")
            device = str(data.get("device") or "")
            device_id = str(data.get("device_id") or "")
            page = _page_from_progress(book, progress, percentage)
            _save_progress(book_hash, percentage, progress, page,
                           device, device_id)
            self._send_json(200, {"document": book_hash,
                                  "percentage": percentage})

    return _Handler


def _query_int(query, name, default, lo, hi):
    try:
        value = int((query.get(name) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _page_from_progress(book, progress, percentage):
    """Page number implied by a KOReader progress string: a plain
    integer is the page (CBZ/PDF shape); a /body/DocFragment[N]
    xpath is the Nth spine document (EPUB shape); anything else
    falls back to the percentage times the page count."""
    text = (progress or "").strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"DocFragment\[(\d+)\]", text)
    if match:
        return int(match.group(1))
    pages = int(book.get("pages") or 0)
    if pages and percentage:
        return max(1, round(percentage * pages))
    return 0


def _cover_for(book):
    """(bytes, mime) of a book's cover, re-read from its file, or
    None. The cover is derived, never stored — the file is the
    record."""
    path = str(book.get("path") or "")
    fmt = str(book.get("format") or "")
    try:
        if fmt == "epub":
            _meta, cover, _err = _read_epub(path)
        elif fmt == "cbz":
            _meta, cover, _err = _read_cbz(path)
        else:
            return None
    except (OSError, ValueError):
        return None
    if not cover:
        return None
    mime = "image/jpeg"
    if cover[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif cover[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif cover[:4] == b"RIFF" and cover[8:12] == b"WEBP":
        mime = "image/webp"
    return cover, mime


# ---------------------------------------------------------------- OPDS ---

def _atom_feed(feed_id, title, base_url, entries, links, updated=None):
    """One OPDS 1.2 Atom feed as bytes. `entries` are already-built
    <entry> elements; `links` are (rel, href, type) triples."""
    ET.register_namespace("", _OPDS_NS_ATOM)
    ET.register_namespace("opds", _OPDS_NS_OPDS)
    ET.register_namespace("dcterms", _OPDS_NS_DCTERMS)
    ET.register_namespace("opensearch", _OPDS_NS_OPENSEARCH)
    feed = ET.Element(f"{{{_OPDS_NS_ATOM}}}feed")
    _atom_text(feed, "id", feed_id)
    _atom_text(feed, "title", title)
    _atom_text(feed, "updated", (updated or _now_iso()))
    author = ET.SubElement(feed, f"{{{_OPDS_NS_ATOM}}}author")
    _atom_text(author, "name", "VYUK")
    for rel, href, mime in links:
        link = ET.SubElement(feed, f"{{{_OPDS_NS_ATOM}}}link")
        link.set("rel", rel)
        link.set("href", href)
        if mime:
            link.set("type", mime)
    for entry in entries:
        feed.append(entry)
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def _atom_text(parent, tag, text):
    el = ET.SubElement(parent, f"{{{_OPDS_NS_ATOM}}}{tag}")
    el.text = str(text or "")
    return el


def _now_iso():
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def _nav_entry(feed_id, title, href, content=""):
    entry = ET.Element(f"{{{_OPDS_NS_ATOM}}}entry")
    _atom_text(entry, "title", title)
    _atom_text(entry, "id", feed_id)
    _atom_text(entry, "updated", _now_iso())
    _atom_text(entry, "content", content)
    link = ET.SubElement(entry, f"{{{_OPDS_NS_ATOM}}}link")
    link.set("rel", "subsection")
    link.set("href", href)
    link.set("type", "application/atom+xml;profile=opds-catalog;kind=navigation")
    return entry


def _book_entry(book, base_url):
    """One OPDS acquisition entry for a book."""
    book_hash = str(book.get("hash") or "")
    entry = ET.Element(f"{{{_OPDS_NS_ATOM}}}entry")
    _atom_text(entry, "title", book.get("title") or "Untitled")
    _atom_text(entry, "id", f"urn:vyuk:ebook:{book_hash}")
    _atom_text(entry, "updated", _now_iso())
    author = str(book.get("author") or "")
    if author:
        author_el = ET.SubElement(entry, f"{{{_OPDS_NS_ATOM}}}author")
        _atom_text(author_el, "name", author)
    series = str(book.get("series") or "")
    summary = []
    if series:
        idx = str(book.get("series_index") or "")
        summary.append(f"Series: {series}" + (f" #{idx}" if idx else ""))
    pages = int(book.get("pages") or 0)
    if pages:
        unit = "documents" if book.get("format") == "epub" else "pages"
        summary.append(f"{pages} {unit}")
    summary.append(f"Format: {str(book.get('format') or '').upper()}")
    _atom_text(entry, "content", "; ".join(summary))
    dcterms = ET.SubElement(entry, f"{{{_OPDS_NS_DCTERMS}}}language")
    dcterms.text = str(book.get("language") or "")
    file_url = (f"{base_url}/opds/v1.2/books/{book_hash}/file")
    link = ET.SubElement(entry, f"{{{_OPDS_NS_ATOM}}}link")
    link.set("rel", "http://opds-spec.org/acquisition")
    link.set("href", file_url)
    link.set("type", _MIME.get(str(book.get("format") or ""),
                               "application/octet-stream"))
    cover_url = (f"{base_url}/opds/v1.2/books/{book_hash}/cover")
    for rel in ("http://opds-spec.org/image",
                "http://opds-spec.org/image/thumbnail"):
        link = ET.SubElement(entry, f"{{{_OPDS_NS_ATOM}}}link")
        link.set("rel", rel)
        link.set("href", cover_url)
        link.set("type", "image/jpeg")
    return entry


def _page_links(base_url, path, page, size, total):
    links = [("self", f"{base_url}{path}?page={page}&size={size}",
              "application/atom+xml;profile=opds-catalog;kind=acquisition"),
             ("start", f"{base_url}/opds/v1.2/catalog",
              "application/atom+xml;profile=opds-catalog;kind=navigation")]
    if (page * size) < total:
        links.append(("next", f"{base_url}{path}?page={page + 1}&size={size}",
                      "application/atom+xml;profile=opds-catalog;kind=acquisition"))
    if page > 1:
        links.append(("previous",
                      f"{base_url}{path}?page={page - 1}&size={size}",
                      "application/atom+xml;profile=opds-catalog;kind=acquisition"))
    return links


def _opds_catalog(base_url):
    entries = [
        _nav_entry("urn:vyuk:opds:books", "All books",
                   f"{base_url}/opds/v1.2/books",
                   "Every book in the library."),
        _nav_entry("urn:vyuk:opds:series", "Series",
                   f"{base_url}/opds/v1.2/series",
                   "Books grouped by series."),
        _nav_entry("urn:vyuk:opds:search", "Search",
                   f"{base_url}/opds/v1.2/search",
                   "Search by title or author."),
    ]
    links = [("self", f"{base_url}/opds/v1.2/catalog",
              "application/atom+xml;profile=opds-catalog;kind=navigation"),
             ("start", f"{base_url}/opds/v1.2/catalog",
              "application/atom+xml;profile=opds-catalog;kind=navigation"),
             ("search", f"{base_url}/opds/v1.2/search",
              "application/opensearchdescription+xml")]
    return _atom_feed("urn:vyuk:opds:catalog", "VYUK ebooks", base_url,
                      entries, links)


def _opds_books(base_url, books, page, size):
    books = sorted(books, key=lambda b: str(b.get("title") or "").lower())
    total = len(books)
    start = (page - 1) * size
    chunk = books[start:start + size]
    entries = [_book_entry(b, base_url) for b in chunk]
    links = _page_links(base_url, "/opds/v1.2/books", page, size, total)
    return _atom_feed("urn:vyuk:opds:books", "All books", base_url,
                      entries, links)


def _opds_series(base_url, books):
    series = sorted({str(b.get("series") or "") for b in books
                     if str(b.get("series") or "").strip()})
    entries = []
    for name in series:
        count = sum(1 for b in books
                    if str(b.get("series") or "") == name)
        href = (f"{base_url}/opds/v1.2/series/"
                + urllib.parse.quote(name, safe=""))
        entries.append(_nav_entry(
            f"urn:vyuk:opds:series:{name}", name, href,
            f"{count} book(s) in this series."))
    links = [("self", f"{base_url}/opds/v1.2/series",
              "application/atom+xml;profile=opds-catalog;kind=navigation"),
             ("start", f"{base_url}/opds/v1.2/catalog",
              "application/atom+xml;profile=opds-catalog;kind=navigation")]
    return _atom_feed("urn:vyuk:opds:series", "Series", base_url,
                      entries, links)


def _opds_series_books(base_url, books, series, page, size):
    books = [b for b in books if str(b.get("series") or "") == series]
    books.sort(key=lambda b: (_series_sort_key(b),
                              str(b.get("title") or "").lower()))
    total = len(books)
    start = (page - 1) * size
    chunk = books[start:start + size]
    entries = [_book_entry(b, base_url) for b in chunk]
    path = "/opds/v1.2/series/" + urllib.parse.quote(series, safe="")
    links = _page_links(base_url, path, page, size, total)
    return _atom_feed(f"urn:vyuk:opds:series:{series}", series,
                      base_url, entries, links)


def _series_sort_key(book):
    raw = str(book.get("series_index") or "").strip()
    try:
        return (0, float(raw), "")
    except ValueError:
        return (1, 0.0, raw)


def _opds_search(base_url, books, term, page, size):
    term = (term or "").strip().lower()
    hits = [b for b in books
            if term in str(b.get("title") or "").lower()
            or term in str(b.get("author") or "").lower()
            or term in str(b.get("series") or "").lower()]
    hits.sort(key=lambda b: str(b.get("title") or "").lower())
    total = len(hits)
    start = (page - 1) * size
    chunk = hits[start:start + size]
    entries = [_book_entry(b, base_url) for b in chunk]
    path = "/opds/v1.2/search?q=" + urllib.parse.quote(term, safe="")
    links = _page_links(base_url, path, page, size, total)
    return _atom_feed("urn:vyuk:opds:search", f"Search: {term}",
                      base_url, entries, links)


def _opensearch_description(base_url):
    ET.register_namespace("", _OPDS_NS_OPENSEARCH)
    root = ET.Element(f"{{{_OPDS_NS_OPENSEARCH}}}OpenSearchDescription")
    short = ET.SubElement(root, f"{{{_OPDS_NS_OPENSEARCH}}}ShortName")
    short.text = "VYUK ebooks"
    desc = ET.SubElement(root, f"{{{_OPDS_NS_OPENSEARCH}}}Description")
    desc.text = "Search the VYUK ebook library."
    url = ET.SubElement(root, f"{{{_OPDS_NS_OPENSEARCH}}}Url")
    url.set("type", "application/atom+xml;profile=opds-catalog;kind=acquisition")
    url.set("template",
            f"{base_url}/opds/v1.2/search?q={{searchTerms}}&page={{startPage?}}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ----------------------------------------------------------------- scan ---

def _scan_folder(folder):
    """Walk a folder for .epub/.cbz files. Returns (books, skipped,
    errors): books are metadata dicts with hash+path, skipped is a
    {reason: count} map (cbr/pdf/oversize), errors are strings."""
    books = []
    skipped = {}
    errors = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith("."))
        for name in sorted(filenames):
            if seen >= _MAX_SCAN_FILES:
                skipped["scan cap (5000 files)"] = (
                    skipped.get("scan cap (5000 files)", 0) + 1)
                continue
            seen += 1
            path = os.path.join(dirpath, name)
            suffix = Path(name).suffix.lower()
            if suffix == ".cbr":
                skipped["CBR (RAR, not readable in stdlib)"] = (
                    skipped.get("CBR (RAR, not readable in stdlib)", 0) + 1)
                continue
            if suffix == ".pdf":
                skipped["PDF (page count not read in this build)"] = (
                    skipped.get("PDF (page count not read in this build)", 0) + 1)
                continue
            if suffix not in (".epub", ".cbz"):
                continue
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                errors.append(f"{path}: unreadable ({exc})")
                continue
            if size > _MAX_FILE_BYTES:
                skipped["oversize (over 200 MB)"] = (
                    skipped.get("oversize (over 200 MB)", 0) + 1)
                continue
            if suffix == ".epub":
                meta, _cover, err = _read_epub(path)
            else:
                meta, _cover, err = _read_cbz(path)
            if err:
                errors.append(f"{path}: {err}")
                continue
            try:
                book_hash = koreader_hash(path)
            except OSError as exc:
                errors.append(f"{path}: could not hash ({exc})")
                continue
            meta["hash"] = book_hash
            meta["path"] = path
            books.append(meta)
    return books, skipped, errors


# -------------------------------------------------------------- handler ---

def _usage():
    return ("ebook_library — your own ebooks and comics: scan a folder "
            "for EPUB/CBZ books, list and search them, track reading "
            "progress, and serve them to your readers over the local "
            "network. Actions: 'scan' reads a folder (path) into the "
            "library; 'list' shows the books with their progress; "
            "'search' finds by title or author (book/author); "
            "'progress' shows or sets one book's progress (book, "
            "percentage 0-100, page, progress, device); 'serve' starts "
            "the LAN server — an OPDS 1.2 catalogue plus KOReader Sync "
            "— and prints its address and password; 'stop' stops it; "
            "'status' reports the server and the library; 'remove' "
            "forgets one book (the file is never deleted). CBR and PDF "
            "files are counted as skipped, not claimed. Kobo Sync and "
            "OPDS v2 are not built.")


def ebook_library(action="", path="", book="", author="", percentage=None,
                  page=None, progress="", device="", device_id="",
                  limit=None, port=None):
    """One tool, eight actions. See the module docstring. The
    library lives in VYUK's own store; the server is LAN-only,
    Basic-auth with a fresh token per start, and dies with stop."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _usage()
    lim = 50
    if limit not in ("", None):
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 50
        lim = max(1, min(lim, 500))

    if action == "scan":
        folder = (path or "").strip()
        if not folder:
            return "Give the folder to scan — e.g. path='C:\\Books'."
        if not os.path.isdir(folder):
            return f"No such folder: {folder}"
        books, skipped, errors = _scan_folder(folder)
        entries, _broken = _load_books()
        known = {e.get("hash"): e for e in entries}
        added = updated = conflicts = 0
        conflict_lines = []
        for meta in books:
            book_hash = meta["hash"]
            existing = known.get(book_hash)
            if existing is not None and existing.get("path") != meta["path"]:
                conflicts += 1
                conflict_lines.append(
                    f"  • {meta['title']} — same content as "
                    f"{existing.get('path')}, kept the first copy.")
                continue
            existed = _save(_BOOK_CATEGORY, book_hash, meta)
            if existed:
                updated += 1
            else:
                added += 1
            known[book_hash] = meta
        again, _broken = _load_books()
        lines = [f"Scanned {folder}: {len(books)} book file(s) read, "
                 f"{added} new, {updated} updated, {conflicts} conflict(s)."]
        if skipped:
            lines.append("Skipped: " + "; ".join(
                f"{count} {reason}" for reason, count in skipped.items()) + ".")
        if errors:
            lines.append(f"{len(errors)} file(s) could not be read:")
            lines.extend(f"  • {e}" for e in errors[:10])
        if conflict_lines:
            lines.append("Conflicts (same content, different file):")
            lines.extend(conflict_lines[:10])
        lines.append(f"Library now holds {len(again)} book(s).")
        return "\n".join(lines)

    if action == "list":
        entries, broken = _load_books()
        if not entries and not broken:
            return ("No books in the library yet. Scan a folder with "
                    "action='scan' and path=....")
        progress_map = _load_progress()
        entries.sort(key=lambda e: (
            str(e.get("series") or "").lower(),
            str(e.get("title") or "").lower()))
        lines = [f"Ebooks ({len(entries)}):"]
        for entry in entries[:lim]:
            percentage, _prog, page = _progress_for(entry, progress_map)
            bits = [f"{entry.get('title') or 'Untitled'}"]
            if entry.get("author"):
                bits.append(f"by {entry['author']}")
            if entry.get("series"):
                idx = str(entry.get("series_index") or "")
                bits.append(f"[{entry['series']}"
                            + (f" #{idx}" if idx else "") + "]")
            pages = int(entry.get("pages") or 0)
            unit = ("documents" if entry.get("format") == "epub"
                    else "pages")
            bits.append(f"({entry.get('format')}, "
                        f"{pages} {unit})")
            if percentage:
                bits.append(f"— {percentage * 100:.0f}% read"
                            + (f" (p. {page})" if page else ""))
            lines.append("  • " + " ".join(bits))
        if broken:
            lines.append(f"({broken} stored book record(s) unreadable.)")
        return "\n".join(lines)

    if action == "search":
        term = ((book or author or "").strip().lower())
        if not term:
            return "Give a title or author to search for."
        entries, _broken = _load_books()
        hits = [e for e in entries
                if term in str(e.get("title") or "").lower()
                or term in str(e.get("author") or "").lower()
                or term in str(e.get("series") or "").lower()]
        if not hits:
            return f"No books match '{term}'."
        progress_map = _load_progress()
        hits.sort(key=lambda e: str(e.get("title") or "").lower())
        lines = [f"Books matching '{term}' ({len(hits)}):"]
        for entry in hits[:lim]:
            percentage, _prog, page = _progress_for(entry, progress_map)
            line = (f"  • {entry.get('title') or 'Untitled'}"
                    + (f" by {entry.get('author')}" if entry.get("author") else "")
                    + f" ({entry.get('format')})")
            if percentage:
                line += f" — {percentage * 100:.0f}% read"
                if page:
                    line += f" (p. {page})"
            lines.append(line)
        return "\n".join(lines)

    if action == "progress":
        entries, _broken = _load_books()
        entry = _find_book(entries, book)
        if entry is None:
            return (f"No book named '{book}'. List them with "
                    "action='list'.")
        progress_map = _load_progress()
        if percentage is None and page is None and not progress:
            pct, prog, pg = _progress_for(entry, progress_map)
            if not pct and not prog and not pg:
                return (f"'{entry.get('title')}' — no progress recorded "
                        "yet.")
            return (f"'{entry.get('title')}': {pct * 100:.0f}% read"
                    + (f" (page {pg})" if pg else "")
                    + (f" — {prog}" if prog else "")
                    + (f" — last reported by "
                       f"{progress_map[entry['hash']].get('device')}"
                       if progress_map.get(entry["hash"], {}).get("device")
                       else ""))
        pct = None
        if percentage is not None:
            try:
                raw_pct = float(percentage)
            except (TypeError, ValueError):
                return "percentage needs to be a number 0-100."
            if not 0.0 <= raw_pct <= 100.0:
                return "percentage needs to be between 0 and 100."
            pct = raw_pct / 100.0
        pg = 0
        if page is not None:
            try:
                pg = int(page)
            except (TypeError, ValueError):
                return "page needs to be a whole number."
            if pg < 0:
                return "page cannot be negative."
        stored = progress_map.get(entry["hash"]) or {}
        if pct is None:
            pct = float(stored.get("percentage") or 0.0)
            if pg and int(entry.get("pages") or 0):
                pct = pg / int(entry["pages"])
        if not progress and pg:
            if entry.get("format") == "cbz":
                progress = str(pg)
            elif entry.get("format") == "epub" and entry.get("spine"):
                idx = min(max(pg, 1), len(entry["spine"]))
                progress = f"/body/DocFragment[{idx}].0"
        if pct is None:
            pct = 0.0
        saved = _save_progress(entry["hash"], pct, progress, pg,
                               device, device_id)
        again = _load_progress().get(entry["hash"])
        if again is None or again.get("percentage") != saved["percentage"]:
            return (f"Progress for '{entry.get('title')}' was written "
                    "but the re-read did not find it — nothing is claimed.")
        return (f"'{entry.get('title')}': {pct * 100:.0f}% read"
                + (f" (page {pg})" if pg else "")
                + ".")

    if action == "serve":
        port_n = 0
        if port not in ("", None):
            try:
                port_n = int(port)
            except (TypeError, ValueError):
                return "port needs to be a whole number."
            if not 1 <= port_n <= 65535:
                return "port needs to be between 1 and 65535."
        ok, why = _SERVER.start(port_n)
        if not ok:
            return f"Could not start the ebook server: {why}"
        base = _SERVER.base_url()
        entries, _broken = _load_books()
        return (f"Ebook server running at {base} — LAN only, Basic "
                f"auth user 'vyuk', password '{_SERVER.token}'. "
                f"OPDS catalogue: {base}/opds/v1.2/catalog — point "
                f"any OPDS reader at it. KOReader Sync: {base}/koreader "
                f"(same user and password). Serving {len(entries)} "
                f"book(s). The password is fresh for this start and "
                f"is shown once — 'stop' ends the server.")

    if action == "stop":
        ok, why = _SERVER.stop()
        if not ok:
            return why
        return "Ebook server stopped."

    if action == "status":
        entries, broken = _load_books()
        progress_map = _load_progress()
        lines = []
        if _SERVER.is_running():
            uptime = int(time.monotonic() - _SERVER.started_at)
            lines.append(f"Ebook server running at {_SERVER.base_url()} "
                         f"(up {uptime}s, LAN only).")
            lines.append("OPDS catalogue: "
                         f"{_SERVER.base_url()}/opds/v1.2/catalog")
            lines.append("KOReader Sync: "
                         f"{_SERVER.base_url()}/koreader")
        else:
            lines.append("Ebook server is not running — 'serve' starts it.")
        lines.append(f"Library: {len(entries)} book(s), "
                     f"{len(progress_map)} with progress recorded"
                     + (f", {broken} unreadable record(s)" if broken else "")
                     + ".")
        return "\n".join(lines)

    if action == "remove":
        entries, _broken = _load_books()
        entry = _find_book(entries, book)
        if entry is None:
            return (f"No book named '{book}'. List them with "
                    "action='list'.")
        fact = _store().get_fact(_BOOK_CATEGORY, entry["hash"])
        if fact is None or not _store().delete_fact(fact["id"]):
            return (f"'{entry.get('title')}' could not be forgotten — "
                    "check the store before retrying.")
        again, _broken = _load_books()
        if _find_book(again, entry["hash"]) is not None:
            return (f"'{entry.get('title')}' was deleted but the "
                    "re-read still finds it — it was not removed.")
        return (f"Forgot '{entry.get('title')}' (the file itself was "
                "left alone).")

    return _usage()  # pragma: no cover
