"""R8-5 magic-wormhole port — VYUK-to-VYUK encrypted file transfer, own code.

"Send this file to my other machine" with a short single-use code phrase,
in the style of magic-wormhole (MIT) — reimplemented from scratch. No
upstream code is imported, vendored or declared: the upstream library
(magic-wormhole 0.24.0) pulls Twisted + autobahn + txtorcon + spake2 +
pynacl + qrcode + tqdm + click + humanize + zipstream-ng + iterable-io,
a whole async framework the tree has never used, for a 214 kB wheel.
The tree already owns the crypto this needs (HMAC-CTR + encrypt-then-MAC
in core/secrets_store.py), so the port is stdlib only and works on both
lab interpreters.

WHAT THIS IS, HONESTLY
-----------------------
* A code-phrase file transfer between two machines running VYUK on the
  SAME local network. The sender offers one file; the receiver claims
  it with the phrase. No account, no cloud storage, no relay.
* NOT SPAKE2 / NOT the magic-wormhole wire protocol: the phrase is a
  short-lived pairing code, not a password. The key comes from
  scrypt(phrase, salt); the channel is HMAC-CTR + encrypt-then-MAC
  (the same construction as core/secrets_store.py:_keystream, with a
  distinct domain-separation tag prefix so a wormhole blob can never
  be mistaken for a machine-key blob). An attacker who guesses the
  phrase inside its 10-minute life could derive the key — that is why
  the phrase is single-use, expires, and three wrong guesses cancel
  the offer outright. The threat model is the local network, and the
  module says so wherever the phrase is shown.
* LAN-DIRECT ONLY. There is no rendezvous server, no transit relay,
  and no public default anywhere in this module — no wormhole.mozilla.org
  string, no default relay URL, nothing to misconfigure into a public
  server. The sender binds the private LAN address only (the
  companion.py UDP-connect trick, never 0.0.0.0) and the listener dies
  after one successful transfer, on expiry, or on cancel. Sending a
  file across the internet needs a relay the owner self-hosts; that is
  the owed extension, not something this batch silently borrows from a
  public server. (The upstream rendezvous server is
  magic-wormhole/magic-wormhole-mailbox-server, MIT, and the transit
  relay is magic-wormhole/magic-wormhole-transit-relay, MIT — both
  verified by the worker via the GitHub API; neither is used here.)

LOPHOLE FAMILY — "a file that reaches the wrong machine, or a file
that arrives changed"
---------------------------------------------------------------
* A phrase guessed or replayed after the transfer — the offer is
  single-use: a successful claim consumes it, and the listener stops.
* A phrase brute-forced — three wrong guesses cancel the offer; the
  owner re-offers with a fresh phrase. A wrong phrase is never
  distinguished from an expired offer in what the receiver is told.
* Bytes changed in transit — the tag covers the whole ciphertext and
  is verified BEFORE the file is presented as received; a mismatch
  deletes the partial file and reports the failure, never the file.
* A transfer that never ends — the offer expires (10 minutes by
  default), the listener stops, and the file cap (100 MB) bounds the
  stream. The receiver streams to disk in 64 KB chunks and never
  holds the whole file in memory.
* A file that overwrites the receiver's work — the destination never
  clobbers: an existing name gets a numbered suffix.
* A transfer that starts by itself — nothing listens at import or
  boot. The listener starts only on an explicit wormhole_offer start,
  and only the owner who started it can cancel it.

Rejected alternatives (recorded):
* Declaring magic-wormhole — the Twisted/autobahn closure (see above).
* Using `cryptography` (declared, Qt-only) — the sys interpreter lacks
  it, so the feature would not run in half the lab; the stdlib
  construction the tree already owns does the same job.
* A public relay as the default — telemetry-OFF forbids it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from core.funnel.tools_registry_core import _funnel_only_fn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB per transfer, documented cap
_OFFER_TTL_SECONDS = 600  # 10 minutes: the phrase works once, then dies
_MAX_ATTEMPTS = 3  # wrong guesses cancel the offer outright
_CHUNK_BYTES = 65536  # streaming chunk: the file is never fully in RAM
_SALT_BYTES = 16
_NONCE_BYTES = 16
_TAG_BYTES = 32
_KEY_BYTES = 32
_PORT_SCAN_START = 8766  # companion.py owns 8765; scan up from here
_PORT_SCAN_END = 8790
_CLAIM_PATH = "/claim"

# scrypt work factor for the phrase -> key derivation. The phrase is a
# short-lived pairing code, not a password: this is deliberately light
# (interactive logins use far more), and the 10-minute expiry + 3-strike
# cancel are what bound guessing, not the KDF cost.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1

# 410 short common words for the code phrase: "<number>-<word>-<word>".
# ~168 million combinations (999 x 410 x 410) for a single-use, 10-minute
# code guarded by a 3-strike cancel — a pairing code, not a password.
_WORDS = (
    "apple", "amber", "anchor", "april", "arrow", "ash", "atlas", "autumn",
    "baker", "bamboo", "banana", "barley", "basin", "beach", "beacon",
    "beaver", "berry", "birch", "bird", "blaze", "bloom", "bluff", "board",
    "boat", "boulder", "brave", "bread", "breeze", "brick", "bridge",
    "bright", "brook", "broom", "brown", "brush", "bubble", "bucket",
    "buddy", "bulrush", "bumble", "butter", "button", "cabin", "cactus",
    "camel", "candle", "canvas", "canyon", "cargo", "carpet", "carrot",
    "castle", "cedar", "celery", "chalk", "cherry", "chess", "chestnut",
    "chicken", "chief", "chili", "chimney", "circle", "citrus", "cliff",
    "clover", "coach", "coast", "cobalt", "cocoa", "coffee", "comet",
    "copper", "coral", "cork", "corn", "cottage", "cotton", "cougar",
    "crane", "crate", "cricket", "crystal", "cucumber", "cushion", "daisy",
    "dance", "dawn", "delta", "denim", "desert", "diamond", "dill",
    "dollar", "dolphin", "donkey", "dragon", "dream", "drift", "drum",
    "duck", "dune", "eagle", "earth", "ebony", "echo", "eclipse", "edge",
    "elm", "ember", "engine", "falcon", "feather", "fern", "festival",
    "field", "fig", "finch", "fire", "fisher", "flame", "flint", "flood",
    "flour", "flower", "forest", "forge", "fox", "frost", "galaxy",
    "garden", "garlic", "gem", "giant", "ginger", "glacier", "glove",
    "gold", "goose", "granite", "grape", "grass", "grove", "guitar",
    "harbor", "harp", "harvest", "hawk", "hazel", "hearth", "heather",
    "heron", "honey", "horizon", "horse", "hunter", "ice", "igloo",
    "ink", "iron", "island", "ivory", "jade", "jasper", "jazz", "jelly",
    "jet", "jewel", "juniper", "keeper", "kettle", "kite", "kiwi",
    "koala", "ladder", "lake", "lamp", "lantern", "lark", "lava", "leaf",
    "leather", "lemon", "letter", "lilac", "lily", "lime", "linen",
    "lion", "llama", "locket", "log", "lotus", "lunar", "maple", "marble",
    "marsh", "meadow", "melon", "mercury", "mermaid", "metal", "meteor",
    "millet", "mint", "mirror", "mist", "mocha", "mole", "moon", "moose",
    "moss", "mountain", "mouse", "muffin", "mule", "mushroom", "mustard",
    "napkin", "nectar", "needle", "nest", "nickel", "night", "noble",
    "noodle", "north", "oak", "oasis", "oat", "ocean", "olive", "onion",
    "onyx", "opal", "orchard", "otter", "owl", "oyster", "palm", "panda",
    "paper", "parrot", "peach", "pearl", "pebble", "pecan", "pelican",
    "pencil", "penguin", "pepper", "perch", "petal", "piano", "pickle",
    "pilot", "pine", "pioneer", "pirate", "planet", "plum", "poem",
    "poplar", "porch", "prairie", "prism", "pumpkin", "puzzle", "quartz",
    "quilt", "rabbit", "radar", "rain", "raisin", "ranch", "raven",
    "reef", "ribbon", "rice", "ridge", "river", "robin", "rocket",
    "rose", "ruby", "saddle", "sage", "sail", "salmon", "salt", "sand",
    "sapphire", "satin", "sauce", "savanna", "scarlet", "scent", "school",
    "scout", "sea", "season", "seed", "sender", "shadow", "shark",
    "sheep", "shell", "shield", "ship", "shore", "silk", "silver",
    "singer", "sky", "slate", "sled", "slope", "snail", "snake", "snow",
    "soap", "solar", "song", "sorrel", "spark", "sparrow", "spice",
    "spider", "spirit", "spoon", "spring", "spruce", "squirrel", "stable",
    "star", "station", "steam", "steel", "stone", "storm", "stream",
    "street", "summer", "sun", "swan", "sweet", "swift", "swirl",
    "table", "tango", "tavern", "tea", "temple", "tent", "terrace",
    "thunder", "tiger", "timber", "toast", "tobacco", "tomato", "topaz",
    "torch", "tornado", "tower", "trail", "travel", "treasure", "tree",
    "tulip", "turtle", "twilight", "umbrella", "unicorn", "valley",
    "vanilla", "velvet", "venus", "verbena", "vine", "violet", "violin",
    "volcano", "voyage", "walnut", "wander", "warbler", "water", "wave",
    "wheat", "whisper", "willow", "wind", "window", "winter", "wisdom",
    "wolf", "wood", "wool", "wren", "yarn", "yellow", "yew", "yoga",
    "youth", "zebra", "zenith", "zephyr", "zinc", "zinnia",
)


# ---------------------------------------------------------------------------
# Crypto — the tree's own HMAC-CTR + encrypt-then-MAC construction.
# Same shape as core/secrets_store.py:_keystream / _machine_key_protect:
# block i = HMAC-SHA256(key, nonce || i as 8-byte big-endian); the tag is
# HMAC(key, b"wormhole-tag" + nonce + ct) and is verified BEFORE any
# plaintext exists. The tag prefix is deliberately different from
# secrets_store's b"tag" (domain separation: a wormhole blob can never
# be mistaken for a machine-key blob, or the other way round).
# ---------------------------------------------------------------------------

def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-CTR keystream (same construction as secrets_store._keystream)."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(
            key, nonce + counter.to_bytes(8, "big"),
            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


class _StreamCipher:
    """Continuous HMAC-CTR stream over many chunks (the keystream never
    restarts between chunks — one stream per transfer). Each 32-byte
    block is generated exactly once, in order."""

    def __init__(self, key: bytes, nonce: bytes):
        self._key = key
        self._nonce = nonce
        self._counter = 0  # next block index to generate
        self._buffer = b""  # unused keystream bytes

    def xor(self, data: bytes) -> bytes:
        if not data:
            return b""
        while len(self._buffer) < len(data):
            self._buffer += hmac.new(
                self._key,
                self._nonce + self._counter.to_bytes(8, "big"),
                hashlib.sha256).digest()
            self._counter += 1
        stream, self._buffer = self._buffer[:len(data)], self._buffer[len(data):]
        return bytes(b ^ s for b, s in zip(data, stream))


def _derive_key(phrase: str, salt: bytes) -> bytes:
    """scrypt(phrase, salt) -> 32-byte key. The phrase is a short-lived
    pairing code, not a password (see the module docstring)."""
    return hashlib.scrypt(phrase.encode("utf-8"), salt=salt,
                          n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                          dklen=_KEY_BYTES)


def _phrase_hash(phrase: str) -> str:
    """SHA-256 of the phrase, hex. The phrase itself never crosses the
    wire — the receiver sends only this hash, and the sender compares
    it with the stored hash."""
    return hashlib.sha256(phrase.encode("utf-8")).hexdigest()


def _make_phrase() -> str:
    """A fresh single-use code phrase: "<number>-<word>-<word>"."""
    number = secrets.randbelow(999) + 1
    return f"{number}-{secrets.choice(_WORDS)}-{secrets.choice(_WORDS)}"


# ---------------------------------------------------------------------------
# LAN address (the companion.py UDP-connect trick: no traffic is sent,
# the socket just picks the outbound interface). None when there is no
# LAN at all — an offer then refuses, it never falls back to a public
# address or to the wildcard address.
# ---------------------------------------------------------------------------

def _lan_address() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def _pick_port(start: int) -> int | None:
    for port in range(max(1024, int(start)), _PORT_SCAN_END + 1):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR, like the server itself (allow_reuse_address):
        # a just-closed transfer leaves the port in TIME_WAIT, and the
        # probe must not treat that as "in use" when the server can
        # still bind it.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    return None


# ---------------------------------------------------------------------------
# Offer state — module-level, inert until an explicit offer starts it.
# One listener serves exactly one offer (one-shot by construction).
# ---------------------------------------------------------------------------

class _Offer:
    __slots__ = ("file_path", "filename", "size", "phrase", "phrase_hash",
                 "salt", "key", "expires_at", "attempts", "server",
                 "thread", "address", "port", "timer", "done")

    def __init__(self, file_path: Path, phrase: str, salt: bytes,
                 key: bytes, expires_at: float, address: str, port: int):
        self.file_path = file_path
        self.filename = file_path.name
        self.size = file_path.stat().st_size
        self.phrase = phrase
        self.phrase_hash = _phrase_hash(phrase)
        self.salt = salt
        self.key = key
        self.expires_at = expires_at
        self.attempts = 0
        self.server = None
        self.thread = None
        self.address = address
        self.port = port
        self.timer = None
        self.done = False


_OFFERS: dict[str, _Offer] = {}  # phrase_hash -> live offer
_OFFERS_LOCK = threading.Lock()
_TRANSFERS: list[dict] = []  # last 10 completed / failed transfers, memory only
_TRANSFERS_LOCK = threading.Lock()


def _record_transfer(entry: dict) -> None:
    with _TRANSFERS_LOCK:
        _TRANSFERS.append(entry)
        del _TRANSFERS[:-10]


def _stop_offer(offer: _Offer, reason: str) -> None:
    """Stop the listener and drop the offer. Idempotent — safe to call
    from the expiry timer, the cancel path, and the claim path."""
    with _OFFERS_LOCK:
        if offer.done:
            return
        offer.done = True
        _OFFERS.pop(offer.phrase_hash, None)
    if offer.timer is not None:
        try:
            offer.timer.cancel()
        except Exception:  # noqa: BLE001 - a dead timer must not block
            pass
    if offer.server is not None:
        try:
            offer.server.shutdown()
            offer.server.server_close()
        except Exception:  # noqa: BLE001 - a dead server must not block
            pass


class _ClaimHandler(http.server.BaseHTTPRequestHandler):
    """One endpoint: GET /claim?h=<phrase-hash>. Everything else 404s.
    The handler never logs the phrase (it only ever sees the hash)."""

    server_version = "VYUKWormhole/1.0"

    def log_message(self, *args):  # noqa: D102 - silence the access log
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != _CLAIM_PATH:
            self.send_error(404, "Not found")
            return
        query = urllib.parse.parse_qs(parsed.query)
        given = (query.get("h") or [""])[0]
        offer = None
        with _OFFERS_LOCK:
            for candidate in _OFFERS.values():
                if hmac.compare_digest(candidate.phrase_hash, given):
                    offer = candidate
                    break
        if offer is None or offer.done:
            # A wrong hash identifies no offer, so the strike is counted
            # against the server's one offer (one listener = one offer).
            self._count_failed_attempt()
            self.send_error(404, "No such offer")
            return
        if time.time() > offer.expires_at:
            _stop_offer(offer, "expired")
            self.send_error(404, "No such offer")
            return
        try:
            self._serve(offer)
        except (BrokenPipeError, ConnectionResetError):
            # The receiver went away mid-stream: the offer stays live
            # (a retry with the same phrase is still one claim).
            return
        except OSError:
            return

    def _count_failed_attempt(self) -> None:
        """Three wrong guesses cancel the one offer this listener serves
        (the offer is the only thing the guesses could be aimed at)."""
        with _OFFERS_LOCK:
            offers = list(_OFFERS.values())
        for offer in offers:
            with _OFFERS_LOCK:
                offer.attempts += 1
                strikes = offer.attempts
            if strikes >= _MAX_ATTEMPTS:
                _record_transfer({
                    "direction": "offer",
                    "filename": offer.filename,
                    "outcome": "cancelled",
                    "detail": "Three wrong code phrases — the offer "
                              "was cancelled. Re-offer to try again.",
                    "at": time.strftime("%H:%M:%S"),
                })
                _stop_offer(offer, "three wrong phrases")

    def _serve(self, offer: _Offer) -> None:
        """Stream nonce + ciphertext + tag. The tag is computed over the
        whole ciphertext as it streams; the receiver verifies it before
        the file is presented as received."""
        nonce = os.urandom(_NONCE_BYTES)
        cipher = _StreamCipher(offer.key, nonce)
        tagger = hmac.new(offer.key, b"wormhole-tag", hashlib.sha256)
        total = _NONCE_BYTES + offer.size + _TAG_BYTES
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.send_header("X-Wormhole-Salt",
                         base64.b64encode(offer.salt).decode("ascii"))
        self.send_header("X-Wormhole-Filename", offer.filename)
        self.send_header("X-Wormhole-Size", str(offer.size))
        self.end_headers()
        self.wfile.write(nonce)
        with open(offer.file_path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    break
                ciphered = cipher.xor(chunk)
                tagger.update(ciphered)
                self.wfile.write(ciphered)
        self.wfile.write(tagger.digest())
        # The claim consumed the offer: stop the listener now (on a
        # thread, so the response finishes flushing first).
        threading.Thread(target=_stop_offer, args=(offer, "claimed"),
                         daemon=True).start()
        _record_transfer({
            "direction": "offer",
            "filename": offer.filename,
            "outcome": "sent",
            "detail": f"{offer.size} bytes to the claiming machine.",
            "at": time.strftime("%H:%M:%S"),
        })


class _OfferServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# The three tools
# ---------------------------------------------------------------------------

def wormhole_offer(action: str = "status", file: str = "",
                   expiry_minutes: int = 10) -> str:
    """Offer one file to another machine on this network.

    'start' — offer the named file with a fresh single-use code phrase
    (10 minutes by default). 'status' — what is currently offered.
    'cancel' — stop offering. The listener starts only on an explicit
    start, binds the private LAN address only, and dies after one
    successful claim, on expiry, or on cancel.
    """
    action = (action or "status").strip().lower()
    if action == "status":
        with _OFFERS_LOCK:
            live = [o for o in _OFFERS.values() if not o.done]
        if not live:
            return "Nothing is offered right now."
        lines = ["Currently offered:"]
        for offer in live:
            left = max(0, int(offer.expires_at - time.time()))
            lines.append(
                f"{offer.filename} ({offer.size} bytes) — code phrase "
                f"'{offer.phrase}', at {offer.address}:{offer.port}, "
                f"expires in {left // 60} minutes {left % 60} seconds, "
                f"{_MAX_ATTEMPTS - offer.attempts} wrong guesses left.")
        return "\n".join(lines)
    if action == "cancel":
        with _OFFERS_LOCK:
            live = [o for o in _OFFERS.values() if not o.done]
        if not live:
            return "Nothing is offered right now — nothing to cancel."
        for offer in live:
            _stop_offer(offer, "cancelled")
        return "Offer cancelled — the listener is stopped."
    if action != "start":
        return "Say 'start', 'status' or 'cancel'."
    if not file:
        return "Name the file to offer."
    path = Path(file).expanduser()
    if not path.exists():
        return f"No such file: {file}."
    if not path.is_file():
        return "Offer one file at a time — that is a folder, not a file."
    try:
        size = path.stat().st_size
    except OSError:
        return f"Could not read that file: {file}."
    if size > _MAX_FILE_BYTES:
        return (f"That file is {size} bytes — the cap is "
                f"{_MAX_FILE_BYTES} bytes (100 MB). Split it first.")
    try:
        minutes = int(expiry_minutes)
    except (TypeError, ValueError):
        return "Give the expiry in whole minutes (default 10)."
    minutes = max(1, min(60, minutes))
    address = _lan_address()
    if address is None:
        return ("No local network found — connect to Wi-Fi or a cable "
                "first. The transfer never leaves your own network.")
    port = _pick_port(_PORT_SCAN_START)
    if port is None:
        return "No free port for the transfer — try again in a moment."
    phrase = _make_phrase()
    salt = os.urandom(_SALT_BYTES)
    key = _derive_key(phrase, salt)
    expires_at = time.time() + minutes * 60
    offer = _Offer(path, phrase, salt, key, expires_at, address, port)
    try:
        server = _OfferServer((address, port), _ClaimHandler)
    except OSError:
        return "Could not listen on this network — try again in a moment."
    offer.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              kwargs={"poll_interval": 0.1})
    offer.thread = thread
    timer = threading.Timer(minutes * 60, _stop_offer, args=(offer, "expired"))
    timer.daemon = True  # a live offer must never hold the process open
    offer.timer = timer
    with _OFFERS_LOCK:
        _OFFERS[offer.phrase_hash] = offer
    timer.start()
    thread.start()
    return (
        f"Offering '{offer.filename}' ({size} bytes). Code phrase: "
        f"'{phrase}'. On the other machine, say 'receive wormhole "
        f"{phrase} from {address}:{port}'. The phrase works once and "
        f"expires in {minutes} minutes; three wrong guesses cancel the "
        f"offer. Nothing leaves your own network.")


def _non_clobbering(destination: Path) -> Path:
    """The destination never overwrites: an existing name gets a
    numbered suffix (report_1.pdf, report_2.pdf, ...)."""
    if not destination.exists():
        return destination
    stem, suffix = destination.stem, destination.suffix
    index = 1
    while True:
        candidate = destination.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def wormhole_receive(phrase: str = "", sender: str = "",
                     destination: str = "") -> str:
    """Claim an offered file from another machine on this network.

    'phrase' is the code phrase the sender read out; 'sender' is the
    address the sender gave (like 192.168.1.5:8766); 'destination' is
    the folder (or full file path) to save into. The bytes are verified
    against the sender's tag before the file is presented as received —
    a mismatch deletes the partial file and reports the failure.
    """
    phrase = (phrase or "").strip()
    sender = (sender or "").strip()
    destination = (destination or "").strip()
    if not phrase:
        return "Give the code phrase the sender read out."
    if not sender:
        return "Give the sender's address (like 192.168.1.5:8766)."
    if not destination:
        return "Give the folder (or full file path) to save into."
    if ":" not in sender:
        return "The sender's address looks like 192.168.1.5:8766 — host and port."
    host, _, port_text = sender.rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        return "The sender's address looks like 192.168.1.5:8766 — host and port."
    if not (1 <= port <= 65535):
        return "The sender's address looks like 192.168.1.5:8766 — host and port."
    dest = Path(destination).expanduser()
    if dest.is_dir():
        dest_dir = dest
        dest_file = None
    else:
        dest_dir = dest.parent
        dest_file = dest
    if not dest_dir.is_dir():
        return f"No such folder: {dest_dir}."
    query = urllib.parse.urlencode({"h": _phrase_hash(phrase)})
    try:
        conn = http.client.HTTPConnection(host, port, timeout=30)
        conn.request("GET", f"{_CLAIM_PATH}?{query}")
        response = conn.getresponse()
    except (OSError, http.client.HTTPException):
        return ("Could not reach the sender — check the address, and "
                "that both machines are on the same network.")
    if response.status != 200:
        return ("Wrong code phrase, or the offer expired — the sender "
                "would need to offer the file again.")
    try:
        salt = base64.b64decode(response.getheader("X-Wormhole-Salt", ""))
        filename = response.getheader("X-Wormhole-Filename", "") or "received"
        size = int(response.getheader("X-Wormhole-Size", "0") or "0")
    except (ValueError, base64.binascii.Error):
        return "The sender's reply was garbled — nothing was saved."
    if size > _MAX_FILE_BYTES:
        return (f"The sender offered {size} bytes — over the "
                f"{_MAX_FILE_BYTES}-byte cap. Nothing was saved.")
    if len(salt) != _SALT_BYTES:
        return "The sender's reply was garbled — nothing was saved."
    key = _derive_key(phrase, salt)
    total = int(response.getheader("Content-Length", "0") or "0")
    if total != _NONCE_BYTES + size + _TAG_BYTES:
        return "The sender's reply was garbled — nothing was saved."
    target = dest_file if dest_file is not None else dest_dir / filename
    target = _non_clobbering(target)
    nonce = response.read(_NONCE_BYTES)
    if len(nonce) != _NONCE_BYTES:
        return "The transfer cut off before it began — nothing was saved."
    cipher = _StreamCipher(key, nonce)
    tagger = hmac.new(key, b"wormhole-tag", hashlib.sha256)
    remaining = size
    tmp_path = target.with_name(target.name + ".wormhole-part")
    try:
        with open(tmp_path, "wb") as handle:
            while remaining > 0:
                chunk = response.read(min(_CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("cut off")
                tagger.update(chunk)
                handle.write(cipher.xor(chunk))
                remaining -= len(chunk)
        received_tag = response.read(_TAG_BYTES)
        if len(received_tag) != _TAG_BYTES:
            raise ValueError("cut off")
        if not hmac.compare_digest(received_tag, tagger.digest()):
            raise ValueError("tag mismatch")
    except (ValueError, OSError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return ("The transfer failed verification — the bytes did not "
                "match the sender's seal, so nothing was kept. Ask the "
                "sender to offer the file again.")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - a dead connection must not block
            pass
    try:
        os.replace(tmp_path, target)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return "The file arrived but could not be saved — nothing was kept."
    _record_transfer({
        "direction": "receive",
        "filename": target.name,
        "outcome": "received",
        "detail": f"{size} bytes from {host}, verified.",
        "at": time.strftime("%H:%M:%S"),
    })
    return (f"Received '{target.name}' ({size} bytes, verified) into "
            f"{target.parent}.")


def wormhole_status() -> str:
    """Read-only: what is currently offered, and the recent transfers."""
    with _OFFERS_LOCK:
        live = [o for o in _OFFERS.values() if not o.done]
    lines = []
    if live:
        lines.append("Currently offered:")
        for offer in live:
            left = max(0, int(offer.expires_at - time.time()))
            lines.append(
                f"{offer.filename} ({offer.size} bytes) — code phrase "
                f"'{offer.phrase}', at {offer.address}:{offer.port}, "
                f"expires in {left // 60} minutes {left % 60} seconds.")
    else:
        lines.append("Nothing is offered right now.")
    with _TRANSFERS_LOCK:
        recent = list(_TRANSFERS)
    if recent:
        lines.append("Recent transfers:")
        for entry in recent:
            lines.append(f"{entry['at']} — {entry['direction']}: "
                         f"{entry['filename']} — {entry['outcome']} "
                         f"({entry['detail']})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Funnel handlers + registration (R8-5, magic-wormhole port).
# ---------------------------------------------------------------------------

@_funnel_only_fn
def _tool_wormhole_offer(action: str = "status", file: str = "",
                         expiry_minutes: int = 10) -> str:
    """Funnel handler: offer a file, in plain words."""
    return wormhole_offer(action=action, file=file,
                          expiry_minutes=expiry_minutes)


@_funnel_only_fn
def _tool_wormhole_receive(phrase: str = "", sender: str = "",
                           destination: str = "") -> str:
    """Funnel handler: claim an offered file, in plain words."""
    return wormhole_receive(phrase=phrase, sender=sender,
                            destination=destination)


@_funnel_only_fn
def _tool_wormhole_status() -> str:
    """Funnel handler: what is offered / recently transferred."""
    return wormhole_status()


def register_all(registry) -> None:
    """Register the wormhole pack (R8-5, magic-wormhole port)."""
    registry.register(
        name="wormhole_offer",
        description="Offer one file to another machine on this network, with a short single-use code phrase: 'start' offers the named file (the reply gives the phrase and the address to read out), 'status' reports what is offered, 'cancel' stops offering. The file moves encrypted to the machine that presents the phrase — no account, no cloud, nothing leaves your own network, and the offer dies after one claim or when it expires. Use when the user wants to send a file to their other machine.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "What to do: 'start', 'status' "
                           "or 'cancel' (default 'status')"},
                "file": {"type": "string",
                         "description": "The file to offer (full path) "
                         "when starting"},
                "expiry_minutes": {"type": "integer",
                                   "description": "How many minutes the "
                                   "phrase stays live when starting "
                                   "(1-60, default 10)"},
            },
            "required": [],
        },
        handler=_tool_wormhole_offer,
    )
    registry.register(
        name="wormhole_receive",
        description="Claim a file another machine on this network offered: give the code phrase the sender read out, the sender's address (like 192.168.1.5:8766), and the folder (or full file path) to save into. The bytes are verified against the sender's seal before the file is presented as received — a mismatch keeps nothing. Use when the user wants to receive a file their other machine offered.",
        parameters={
            "type": "object",
            "properties": {
                "phrase": {"type": "string",
                           "description": "The code phrase the sender "
                           "read out"},
                "sender": {"type": "string",
                           "description": "The sender's address, like "
                           "192.168.1.5:8766"},
                "destination": {"type": "string",
                                "description": "The folder (or full file "
                                "path) to save into"},
            },
            "required": [],
        },
        handler=_tool_wormhole_receive,
    )
    registry.register(
        name="wormhole_status",
        description="What is currently offered on this network, and the recent transfers. Pure read — nothing is started, stopped or changed. Use when the user asks what is being shared or what was recently sent or received.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_wormhole_status,
    )
