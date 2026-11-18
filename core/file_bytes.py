"""Byte-exact file writes (click-anywhere Batch 0, L-60).

The write path used to go through text mode (``Path.write_text`` /
``open(path, "w")``), which on Windows translates every ``\\n`` to
``\\r\\n`` — so a multi-line write failed its own byte-exact verify
(213 bytes in, 219 on disk) and the append branch verified
existence-only. This module is the fix's encoding half: detect the
file's encoding from its bytes, and encode text to the exact bytes
that must land on disk. The write itself is bytes (``"wb"``), never
text mode, so no platform newline translation can touch it.

Rules (Plan 9 §7.1 / §6.2):
  * BOM sniff first: UTF-8 BOM, UTF-16 LE BOM, UTF-16 BE BOM.
  * Else UTF-8 when the file decodes as UTF-8, else refuse —
    never silently re-encode.
  * A file that does not exist defaults to UTF-8.
  * The BOM is part of the file's bytes: an overwrite of a
    BOM-carrying file re-emits the BOM; an append never emits a
    second one.

Licence: own code — nothing adopted, no upstream, no third-party
licence text (Parent-ruled (licence, 2026-11-14)).
"""

from __future__ import annotations

UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"

UTF8 = "utf-8"
UTF8_BOMMED = "utf-8-sig"
UTF16_LE = "utf-16-le"
UTF16_BE = "utf-16-be"

_BOM_ENCODINGS = (
    (UTF8_BOM, UTF8_BOMMED),
    (UTF16_LE_BOM, UTF16_LE),
    (UTF16_BE_BOM, UTF16_BE),
)

_FILE_ENCODINGS = (UTF8, UTF8_BOMMED, UTF16_LE, UTF16_BE)


class UndetectableEncoding(ValueError):
    """The file's encoding cannot be detected — refuse, never guess."""


def detect_encoding(raw: bytes | None) -> str:
    """The encoding a file's bytes are in.

    ``raw`` is the file's current bytes, or None when the file does
    not exist (defaults to UTF-8). BOM sniff first; else UTF-8 when
    the bytes decode as UTF-8; else raise
    :class:`UndetectableEncoding`. Never returns a guess."""
    if raw is None:
        return UTF8
    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            return encoding
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UndetectableEncoding(
            "the file does not decode as UTF-8 and carries no "
            "recognised BOM — refusing to guess its encoding")
    return UTF8


def bom_for(encoding: str) -> bytes:
    """The BOM bytes for an encoding (empty for plain UTF-8)."""
    if encoding == UTF8_BOMMED:
        return UTF8_BOM
    if encoding == UTF16_LE:
        return UTF16_LE_BOM
    if encoding == UTF16_BE:
        return UTF16_BE_BOM
    return b""


def encode_text(text: str, encoding: str, *, include_bom: bool) -> bytes:
    """Encode ``text`` in ``encoding``, with the BOM when asked.

    ``include_bom`` is True only for an overwrite of a file that
    already carries that BOM (the BOM is preserved); an append
    never re-emits it, and a new file never gains one."""
    if encoding == UTF8_BOMMED:
        body = text.encode("utf-8")
    elif encoding == UTF16_LE:
        body = text.encode("utf-16-le")
    elif encoding == UTF16_BE:
        body = text.encode("utf-16-be")
    elif encoding == UTF8:
        body = text.encode("utf-8")
    else:
        raise UndetectableEncoding(
            "unknown encoding %r — refusing to encode" % (encoding,))
    if include_bom:
        return bom_for(encoding) + body
    return body


def has_bom(raw: bytes) -> bool:
    """True when the bytes start with a recognised BOM."""
    return any(raw.startswith(bom) for bom, _ in _BOM_ENCODINGS)
