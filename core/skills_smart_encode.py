"""
Smart skills pack 2 for VYUK (v5.5) — the sibling of skills_smart:
five more small, exact, pure-Python tools that need no OS surface
at all: a base64 codec, a text hasher, a JSON formatter/validator,
a date calculator and a UUID generator.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result — a refusal or an error line says exactly why. Every cap
(base64/hash input lengths, formatted-JSON output length, UUID
count) is enforced in the open and disclosed in the result.

All five tools are READ-ONLY: they compute text and return it,
changing nothing on the machine. Stdlib-only (base64, hashlib,
json, datetime, uuid) and identical on Linux and Windows — no
platform branches anywhere in this module.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# BASE64 CODEC — strict decode, honest about non-text bytes
# ---------------------------------------------------------------------------

_MAX_BASE64_CHARS = 20_000
_HEX_PREVIEW_BYTES = 32


def base64_codec(action: str, text: str, variant: str = "standard") -> str:
    """Encode text to base64, or decode base64 back to text.

    variant='urlsafe' uses the URL-and-token alphabet (- and _
    instead of + and /): encoding emits it, decoding accepts it
    and restores any missing '=' padding, as that dialect usually
    omits it. The default 'standard' path is unchanged."""
    action = (action or "").strip().lower()
    if action not in ("encode", "decode"):
        return ("Tell me whether to 'encode' or 'decode' — those "
                "are the two base64 actions.")
    variant = (variant or "standard").strip().lower()
    if variant not in ("standard", "urlsafe"):
        return ("The base64 variant is 'standard' (the usual "
                "alphabet) or 'urlsafe' (the - and _ alphabet used "
                "in links and tokens).")
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(text) > _MAX_BASE64_CHARS:
        return (f"That input is {len(text)} characters — the base64 "
                f"cap is {_MAX_BASE64_CHARS}. Split it into smaller "
                "pieces and I'll do each one.")
    if action == "encode":
        if not text:
            return "The base64 of an empty string is an empty string."
        if variant == "urlsafe":
            return base64.urlsafe_b64encode(
                text.encode("utf-8")).decode("ascii")
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    cleaned = re.sub(r"\s+", "", text)
    if not cleaned:
        return "There's nothing to decode — the input was empty."
    if variant == "urlsafe":
        cleaned = cleaned.translate(str.maketrans("-_", "+/"))
        cleaned += "=" * (-len(cleaned) % 4)
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError) as e:
        return (f"That isn't valid base64 — {e}. Check for missing "
                "padding ('=' at the end) or characters outside the "
                "base64 alphabet (A–Z, a–z, 0–9, +, /, =).")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        preview = raw[:_HEX_PREVIEW_BYTES].hex()
        more = (f" (the first {_HEX_PREVIEW_BYTES} of {len(raw)} bytes)"
                if len(raw) > _HEX_PREVIEW_BYTES else "")
        return (f"That decodes to {len(raw)} bytes that aren't valid "
                f"UTF-8 text, so I can't show them as text. Hex "
                f"preview{more}: {preview}")


# ---------------------------------------------------------------------------
# HASH TEXT — hex digests, with the weak-algorithm warning kept honest
# ---------------------------------------------------------------------------

_MAX_HASH_CHARS = 100_000
_HASH_ALGORITHMS = ("md5", "sha1", "sha256", "sha512")


def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hex digest of a text (UTF-8), by the named algorithm."""
    algo = (algorithm or "sha256").strip().lower()
    if algo not in _HASH_ALGORITHMS:
        return (f"I don't have a '{algorithm}' hasher — I can do "
                "md5, sha1, sha256 and sha512.")
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(text) > _MAX_HASH_CHARS:
        return (f"That text is {len(text)} characters — the hash "
                f"cap is {_MAX_HASH_CHARS}. Hash a shorter excerpt, "
                "or hash the file instead.")
    data = text.encode("utf-8")
    try:
        digest = hashlib.new(algo, data, usedforsecurity=False).hexdigest()
    except TypeError:  # very old Python without the kwarg
        digest = hashlib.new(algo, data).hexdigest()
    except ValueError as e:
        return f"Couldn't compute the {algo} hash: {e}"
    out = f"{algo}: {digest}"
    if algo in ("md5", "sha1"):
        out += (f"\nNote: {algo} is fine for checksums, not for "
                "passwords — it's fast and collision-prone by "
                "modern standards.")
    return out


# ---------------------------------------------------------------------------
# JSON FORMAT — pretty, validate-only summary, or minify
# ---------------------------------------------------------------------------

_MAX_JSON_OUTPUT_CHARS = 6000


def _json_summary(value) -> str:
    """A 'Valid JSON — …' line of type/size facts, no content dump."""
    if isinstance(value, dict):
        n = len(value)
        return (f"Valid JSON — it's an object with {n} top-level "
                f"key{'s' if n != 1 else ''}.")
    if isinstance(value, list):
        n = len(value)
        return (f"Valid JSON — it's an array with {n} "
                f"item{'s' if n != 1 else ''}.")
    if isinstance(value, str):
        return (f"Valid JSON — it's a string of {len(value)} "
                "characters.")
    if isinstance(value, bool):
        return f"Valid JSON — it's the boolean {str(value).lower()}."
    if value is None:
        return "Valid JSON — it's null."
    return f"Valid JSON — it's the number {value}."


def json_format(text: str, action: str = "format") -> str:
    """Format, validate or minify a JSON document."""
    action = (action or "format").strip().lower()
    if action not in ("format", "validate", "minify"):
        return ("JSON actions are 'format' (pretty-print), "
                "'validate' (check and summarise) and 'minify' "
                "(squash to one line).")
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    if not text.strip():
        return "There's no JSON to work on — the input was empty."
    try:
        value = json.loads(text)
    except json.JSONDecodeError as e:
        return (f"Invalid JSON — line {e.lineno} column {e.colno}: "
                f"{e.msg}.")
    if action == "validate":
        return _json_summary(value)
    if action == "minify":
        return ("Valid JSON — minified:\n"
                + json.dumps(value, ensure_ascii=False,
                             separators=(",", ":")))
    pretty = json.dumps(value, indent=2, ensure_ascii=False)
    if len(pretty) > _MAX_JSON_OUTPUT_CHARS:
        return ("Valid JSON — formatted:\n"
                + pretty[:_MAX_JSON_OUTPUT_CHARS]
                + f"\n[output truncated: showing "
                  f"{_MAX_JSON_OUTPUT_CHARS} of {len(pretty)} "
                  f"characters]")
    return "Valid JSON — formatted:\n" + pretty


# ---------------------------------------------------------------------------
# GENERATE UUID — random v4 by default, time-based v1 on request
# ---------------------------------------------------------------------------

_MAX_UUID_COUNT = 10


def generate_uuid(version: int = 4, count: int = 1) -> str:
    """One or more UUIDs (v4 random, or v1 with its caveat stated)."""
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 4
    if version not in (1, 4):
        return ("I generate version 4 (random, the usual choice) "
                "or version 1 (time-based) UUIDs.")
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(_MAX_UUID_COUNT, n))
    maker = uuid.uuid1 if version == 1 else uuid.uuid4
    lines = [str(maker()) for _ in range(n)]
    out = (f"Here {'is' if n == 1 else 'are'} {n} version-{version} "
           f"UUID{'s' if n != 1 else ''}:\n" + "\n".join(lines))
    if version == 1:
        out += ("\nNote: version-1 UUIDs embed the current time "
                "and this machine's MAC address — handy for "
                "ordering, but don't use them where anonymity "
                "matters.")
    return out


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Smart-2 pack tool on a ToolRegistry-like object."""
    registry.register(
        name="base64_codec",
        description="Encode text to base64, or decode base64 back to text. Decoding is strict: invalid base64 gets an honest error, and decoded bytes that aren't valid UTF-8 text are reported as a byte count with a hex preview instead of gibberish. Input is capped at 20000 characters. Use for quick base64 conversions (tokens, payloads, data URIs). Pass variant='urlsafe' for the URL-and-token alphabet (- and _ instead of + and /, padding optional when decoding) — the form JWTs and web tokens use.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["encode", "decode"], "description": "'encode' (text to base64) or 'decode' (base64 to text)"},
                "text": {"type": "string", "description": "The text to encode, or the base64 to decode"},
                "variant": {"type": "string", "enum": ["standard", "urlsafe"], "description": "'standard' (default) or 'urlsafe' (the - and _ alphabet)"},
            },
            "required": ["action", "text"],
        },
        handler=base64_codec,
    )
    registry.register(
        name="hash_text",
        description="Compute the hex digest of a text (hashed as UTF-8) with md5, sha1, sha256 (default) or sha512. For md5/sha1 the result carries the honest warning that they suit checksums, not passwords. Input is capped at 100000 characters. Use to fingerprint or compare text exactly.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to hash"},
                "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"], "description": "Hash algorithm (default sha256)"},
            },
            "required": ["text"],
        },
        handler=hash_text,
    )
    registry.register(
        name="json_format",
        description="Work on a JSON document: 'format' pretty-prints it (2-space indent, output capped at 6000 characters with any truncation disclosed), 'validate' checks it and summarises what it is (type and size, no content dump), 'minify' compacts it to one line. Invalid JSON returns the parser's own error with its line and column — the part that actually helps fix it. Results lead with the verdict (valid or invalid). For working with pasted JSON data.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The JSON document as text"},
                "action": {"type": "string", "enum": ["format", "validate", "minify"], "description": "'format' (default), 'validate' or 'minify'"},
            },
            "required": ["text"],
        },
        handler=json_format,
    )
    registry.register(
        name="generate_uuid",
        description="Generate one or more UUIDs: version 4 (random, the default) or version 1 (time-based — the result notes that v1 embeds the time and this machine's MAC address). Count is clamped to 1–10. Its dev-pack twin uuid_generate offers the same UUIDs with explicit version selection.",
        parameters={
            "type": "object",
            "properties": {
                "version": {"type": "integer", "enum": [1, 4], "description": "UUID version: 4 (random, default) or 1 (time-based)"},
                "count": {"type": "integer", "description": "How many to generate (default 1, clamped to 1–10)"},
            },
            "required": [],
        },
        handler=generate_uuid,
    )
