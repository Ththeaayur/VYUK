"""Social track (v5.73) — SO-1.2 vCard 3.0 import/export, native.

vobject itself (Apache-2.0, verified from its sdist LICENSE-2.0
file) is NOT vendored or depended on: this is a native stdlib
implementation of the import/export primitive, so the shipped
tree gains no new dependency. Direct LICENSE read of vobject
0.9.6.1 closes wave-21 owed check #1 for the record.

Rules, from the wave report:
- Only fields literally present in a vCard HE provides/imports
  are modelled, kept on-device. No web enrichment.
- No relationship is derived from two cards sharing an address,
  company, or surname. No merge of two people on name alone —
  merges go through Wave 6 KN-7 dedupe with his confirmation.
  This module maps ONE card to people-page fields; it never
  merges.
- BDAY values route through so_dates (year-unknown + leap
  policy + 1604-placeholder handling). No age is ever computed
  here.

Line folding follows RFC 2426 (75 octets, UTF-8 safe).
"""

from .social_occasion_dates import parse_vcard_bday

KNOWN_PROPS = ("FN", "N", "TEL", "EMAIL", "ADR", "BDAY", "NOTE",
               "ORG", "TITLE", "URL", "NICKNAME")


def _unfold(text):
    lines = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw)
    return lines


def _unescape(value):
    out = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n" or nxt == "N":
                out.append("\n")
            elif nxt in ("\\", ";", ","):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _escape(value):
    return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def _fold(line):
    """Fold one logical line at 75 UTF-8 octets."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, current, size = [], "", 0
    for char in line:
        encoded = char.encode("utf-8")
        if size + len(encoded) > 75 and current:
            chunks.append(current)
            current, size = "", 0
        current += char
        size += len(encoded)
    chunks.append(current)
    return "\r\n ".join(chunks)


def _split_prop(line):
    """Split 'NAME;PARAM=VAL:value' into (name, params, value)."""
    if ":" not in line:
        raise ValueError(f"not a vCard property line: {line!r}")
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.strip().upper()] = val.strip()
        elif part.strip():
            params[part.strip().upper()] = ""
    return name, params, _unescape(value)


def parse_vcards(text):
    """Parse vCard text into a list of card dicts. Each card:
    {properties: [(name, params, value)], ...} plus convenience
    accessors via card_field(). Raises ValueError on malformed
    input (missing BEGIN/END, property outside a card)."""
    cards, current = [], None
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VCARD":
            if current is not None:
                raise ValueError("nested BEGIN:VCARD")
            current = {"properties": []}
            continue
        if upper == "END:VCARD":
            if current is None:
                raise ValueError("END:VCARD without BEGIN")
            cards.append(current)
            current = None
            continue
        if current is None:
            raise ValueError(f"property outside a card: {line!r}")
        name, params, value = _split_prop(line)
        if name in ("BEGIN", "END", "VERSION"):
            continue
        current["properties"].append((name, params, value))
    if current is not None:
        raise ValueError("unterminated VCARD (missing END:VCARD)")
    return cards


def card_field(card, name, default=""):
    """First value of property `name`, or default."""
    for prop, _params, value in card["properties"]:
        if prop == name:
            return value
    return default


def card_all(card, name):
    """All values of property `name`."""
    return [value for prop, _p, value in card["properties"]
            if prop == name]


def display_name(card):
    """FN if present, else 'Given Family' from N, else ''."""
    fn = card_field(card, "FN")
    if fn:
        return fn
    n_value = card_field(card, "N")
    if n_value:
        parts = n_value.split(";")
        family = parts[0] if len(parts) > 0 else ""
        given = parts[1] if len(parts) > 1 else ""
        return f"{given} {family}".strip()
    return ""


def card_birthday(card):
    """The card's BDAY as an occasion-date dict (so_dates), or
    None when absent. Raises ValueError on a malformed BDAY."""
    raw = card_field(card, "BDAY")
    if not raw:
        return None
    return parse_vcard_bday(raw)


def to_people_fields(card):
    """Map ONE card to Wave 6 people-page fields. No merging, no
    inferred relationship: ORG/TITLE appear only as stated text
    inside the note, never as a relationship claim. Raises
    ValueError when the card names nobody (no FN, no N)."""
    name = display_name(card)
    if not name:
        raise ValueError(
            "vCard names nobody (no FN, no N) — refusing to "
            "create a person record from it")
    note_parts = []
    note = card_field(card, "NOTE")
    if note:
        note_parts.append(note)
    org = card_field(card, "ORG")
    title = card_field(card, "TITLE")
    if org or title:
        note_parts.append("From card: " + " — ".join(
            p for p in (title, org) if p))
    return {"name": name, "relationship": "",
            "note": "\n".join(note_parts)}


def serialise_card(card):
    """Serialise a card dict back to vCard 3.0 text. Round-trip
    preserves unknown properties verbatim (name, params, value).
    """
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    for name, params, value in card["properties"]:
        head = name
        for key, val in params.items():
            head += f";{key}={val}" if val else f";{key}"
        lines.append(_fold(f"{head}:{_escape(value)}"))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"
