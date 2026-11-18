"""CR-6.1/6.2 native marking writers (Creative track, wave 29).

Native JPEG EXIF writer (stdlib ``struct`` — no piexif dep) and native
XMP packet writer (stdlib XML escaping — no xmp_toolkit dep).

Fields written:
- EXIF IFD0: Copyright (0x8298) = CopyrightNotice, Artist (0x013B) =
  CreditLine as the credited party (EXIF has no dedicated CreditLine
  tag; the XMP packet carries the full IPTC field set).
- XMP packet: dc:rights (CopyrightNotice), dc:creator (Licensor),
  photoshop:Credit (CreditLine), xmpRights:WebStatement,
  Iptc4xmpExt:DigitalSourceType (controlled vocabulary only).

STUDY NOTE (CR-6.10 — read before quoting this module): No compliance
claim, no certification claim, and no Art. 50 claim is made by this
module or by any marking it writes. It builds marking bytes; whether
any marking satisfies any law is a legal question this code does not
answer. EUR-Lex Art. 50 text and the Code of Practice reads stay owed.
END STUDY NOTE.

SynthID WATCH (CR-6.8, spec only, no code): a detector is a
user-initiated link-out ONLY, disclosed that the file goes to Google;
never promise "SynthID-protected" output; never automate the detector
(no embedder licence exists).

C2PA: this module builds the CLAIM/ASSERTION structure only (actions,
ingredients, hashes, AI-involvement fields) as native data. JUMBF
embedding, X.509 signing, and verification are QUEUED (they need the
transitive-crate SBOM sweep + the signing-key decision). Self-signed
output displays *untrusted* — copy must say so, never imply trusted.
No signing function exists in this module, by design.

ExifTool is the ORACLE, not a dep: code-lab/cr_exif_diff.py diffs this
writer's output against a system ExifTool's output instead of trusting
the writer. ExifTool bundling stays PILOT-gated (Artistic election).
"""

import hashlib
import struct
from xml.sax.saxutils import escape as _xml_escape

DIGITAL_SOURCE_TYPES = (
    "trainedAlgorithmicMedia",
    "compositeWithTrainedAlgorithmicMedia",
    "compositeSynthetic",
    "algorithmicMedia",
    "digitalCapture",
)

_EXIF_HEADER = b"Exif\x00\x00"
_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"


# ---------------------------------------------------------------- EXIF
def _pack_ascii_entry(tag, value, value_offset):
    """One IFD entry for an ASCII value. Returns (entry_bytes, tail_bytes).

    Values of <= 4 bytes (including the NUL) are stored inline; longer
    values go in the tail area at ``value_offset`` (absolute TIFF offset).
    """
    raw = value.encode("utf-8", "replace") + b"\x00"
    count = len(raw)
    if count <= 4:
        inline = raw + b"\x00" * (4 - count)
        return struct.pack("<HHI", tag, 2, count) + inline, b""
    return (struct.pack("<HHII", tag, 2, count, value_offset), raw)


def build_exif_segment(copyright_notice=None, credit_line=None):
    """Build an EXIF APP1 segment (without the FFE1 marker/length).

    Returns the segment payload: ``Exif\\0\\0`` + TIFF data. At least
    one field must be given.
    """
    entries = []
    if credit_line is not None:
        entries.append((0x013B, credit_line))  # Artist
    if copyright_notice is not None:
        entries.append((0x8298, copyright_notice))  # Copyright
    if not entries:
        raise ValueError("build_exif_segment needs at least one field")
    entries.sort()
    n = len(entries)
    ifd_size = 2 + 12 * n + 4
    tail_start = 8 + ifd_size
    tail = b""
    packed = []
    for tag, value in entries:
        entry, extra = _pack_ascii_entry(tag, value, tail_start + len(tail))
        packed.append(entry)
        tail += extra
    tiff = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    tiff += struct.pack("<H", n) + b"".join(packed) + struct.pack("<I", 0)
    tiff += tail
    return _EXIF_HEADER + tiff


def _iter_jpeg_segments(data):
    """Yield (marker, payload_start, payload_end, segment_start) for each
    segment before SOS/EOI. Raises ValueError on non-JPEG input."""
    if len(data) < 2 or data[0] != 0xFF or data[1] != 0xD8:
        raise ValueError("not a JPEG (missing SOI marker)")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xD9:  # EOI
            break
        if marker == 0xDA:  # SOS: entropy data follows; stop here
            break
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        payload_start = pos + 4
        payload_end = pos + 2 + length
        yield marker, payload_start, payload_end, pos
        pos = payload_end


def _replace_or_insert_segment(data, match, new_payload):
    """Replace the first segment matching ``match`` or insert after SOI.

    ``match(marker, payload_bytes)`` selects the segment to replace.
    ``new_payload`` is the full segment payload (without marker/length).
    Returns new JPEG bytes.
    """
    for marker, start, end, seg_start in _iter_jpeg_segments(data):
        if match(marker, data[start:end]):
            seg_len = 2 + (end - seg_start - 2)
            seg = b"\xff" + bytes([marker]) + struct.pack(">H", seg_len)
            seg += new_payload
            return data[:seg_start] + seg + data[end:]
    seg = b"\xff\xe1" + struct.pack(">H", len(new_payload) + 2) + new_payload
    return data[:2] + seg + data[2:]


def write_exif(jpeg_bytes, copyright_notice=None, credit_line=None):
    """Write/replace the EXIF APP1 segment in a JPEG. Returns new bytes."""
    payload = build_exif_segment(copyright_notice, credit_line)
    return _replace_or_insert_segment(
        jpeg_bytes,
        lambda m, p: m == 0xE1 and p.startswith(_EXIF_HEADER),
        payload)


def read_exif(jpeg_bytes):
    """Parse the first EXIF APP1 segment. Returns {copyright, artist}."""
    out = {}
    for marker, start, end, _ in _iter_jpeg_segments(jpeg_bytes):
        if marker == 0xE1 and jpeg_bytes[start:end].startswith(_EXIF_HEADER):
            tiff = jpeg_bytes[start + len(_EXIF_HEADER):end]
            if tiff[:2] != b"II":
                raise ValueError("only little-endian TIFF is supported")
            ifd_off = struct.unpack("<I", tiff[4:8])[0]
            count = struct.unpack("<H", tiff[ifd_off:ifd_off + 2])[0]
            for i in range(count):
                e = ifd_off + 2 + 12 * i
                tag, typ, cnt, val = struct.unpack("<HHII", tiff[e:e + 12])
                if typ != 2:
                    continue
                if cnt <= 4:
                    raw = struct.pack("<I", val)[:cnt]
                else:
                    raw = tiff[val:val + cnt]
                text = raw.rstrip(b"\x00").decode("utf-8", "replace")
                if tag == 0x8298:
                    out["copyright"] = text
                elif tag == 0x013B:
                    out["artist"] = text
            return out
    return out


# ----------------------------------------------------------------- XMP
def build_xmp_packet(copyright_notice=None, credit_line=None,
                     web_statement=None, licensor=None,
                     digital_source_type=None):
    """Build an XMP packet (bytes) for the marking fields."""
    if digital_source_type is not None \
            and digital_source_type not in DIGITAL_SOURCE_TYPES:
        raise ValueError(
            "unknown DigitalSourceType: %r (must be one of %s)"
            % (digital_source_type, ", ".join(DIGITAL_SOURCE_TYPES)))
    esc = _xml_escape
    fields = []
    if copyright_notice is not None:
        fields.append(
            '<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">%s'
            "</rdf:li></rdf:Alt></dc:rights>" % esc(copyright_notice))
    if licensor is not None:
        fields.append(
            "<dc:creator><rdf:Seq><rdf:li>%s</rdf:li></rdf:Seq>"
            "</dc:creator>" % esc(licensor))
    if credit_line is not None:
        fields.append("<photoshop:Credit>%s</photoshop:Credit>"
                      % esc(credit_line))
    if web_statement is not None:
        fields.append("<xmpRights:WebStatement>%s</xmpRights:WebStatement>"
                      % esc(web_statement))
    if digital_source_type is not None:
        fields.append("<Iptc4xmpExt:DigitalSourceType>%s"
                      "</Iptc4xmpExt:DigitalSourceType>"
                      % esc(digital_source_type))
    if not fields:
        raise ValueError("build_xmp_packet needs at least one field")
    packet = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="VYUK cr_marking">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
        ' xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"'
        ' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/">'
        '<rdf:Description rdf:about="">'
        + "".join(fields) +
        "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def embed_xmp(jpeg_bytes, xmp_packet):
    """Write/replace the XMP APP1 segment in a JPEG. Returns new bytes."""
    payload = _XMP_HEADER + xmp_packet
    return _replace_or_insert_segment(
        jpeg_bytes,
        lambda m, p: m == 0xE1 and p.startswith(_XMP_HEADER),
        payload)


def read_xmp(jpeg_bytes):
    """Return the XMP packet bytes, or None when no XMP segment exists."""
    for marker, start, end, _ in _iter_jpeg_segments(jpeg_bytes):
        if marker == 0xE1 and jpeg_bytes[start:end].startswith(_XMP_HEADER):
            return jpeg_bytes[start + len(_XMP_HEADER):end]
    return None


# ---------------------------------------------------------------- C2PA
def build_c2pa_claim(actions, ingredients, ai_involvement=None):
    """Build a C2PA CLAIM/ASSERTION structure as native data only.

    ``actions``: list of action dicts, e.g. {"action": "c2pa.created",
    "softwareAgent": "VYUK"}. ``ingredients``: list of {"title", "data"}
    dicts; each ingredient's SHA-256 is computed here so the claim is
    checkable without any signing step. ``ai_involvement``: free-form
    dict describing AI involvement (e.g. {"trainedAlgorithmicMedia":
    True}); it is recorded, never asserted as verified.

    This function performs NO JUMBF embedding, NO X.509 signing, and
    NO verification — those are queued. A claim built here is an
    unsigned data structure, and any copy describing it must say so.
    """
    hashed = []
    for ing in ingredients or []:
        data = ing.get("data", b"")
        if isinstance(data, str):
            data = data.encode("utf-8")
        hashed.append({
            "title": ing.get("title", ""),
            "relationship": ing.get("relationship", "inputTo"),
            "hash": hashlib.sha256(data).hexdigest(),
            "hash_alg": "sha256",
        })
    return {
        "claim_generator": "VYUK creative_exif_marking (structure only;"
                           " unsigned, unverified)",
        "actions": list(actions or []),
        "ingredients": hashed,
        "ai_involvement": ai_involvement or {},
        "signed": False,
    }
