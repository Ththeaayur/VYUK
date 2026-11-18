"""
Developer skills pack for VYUK — ten small, pure, offline
utilities a programmer reaches for mid-thought: testing a regular
expression, converting between number bases, encoding text for a
URL, generating UUIDs, explaining a cron schedule in plain words,
comparing two semantic versions, diffing two blocks of text,
converting between unix timestamps and human dates, converting a
colour between hex / rgb / hsl, and working out the numbers of an
IP subnet.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise — bad input becomes
an honest line saying what's wrong, and every cap and clamp is
disclosed in the result. No I/O at all here: the tools work only
on their arguments, need no files, no network and no hardware,
so they behave identically everywhere.

Deliberately NOT here: formatting or pretty-printing one JSON
document is json_format, hashing text is hash_text, and judging
a password is password_strength — this pack covers the gaps
around them, not the same ground twice.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

# INTENTIONAL: the old module's stdlib imports are re-exported
# so the facade namespace matches the old one exactly
# (dir-diff-empty bar, section 2 P2; files3/files2 lesson).
import colorsys
import difflib
import ipaddress
import re
import urllib.parse
import uuid
from datetime import datetime, timezone

# INTENTIONAL: re-exports of every moved name (bare-name rule:
# register_all's handler names resolve through these imports).
from .skills_dev_helpers import (
    _REGEX_TEXT_CAP,
    _REGEX_SHOWN,
    _REGEX_MATCH_CAP,
    _URL_TEXT_CAP,
    _BASE_DIGITS_CAP,
    _DIFF_LINE_CAP,
)
from .skills_dev_convert import (
    _DIGIT_ALPHABET,
    _BASE_WORDS,
    _parse_base,
    _to_base,
    base_convert,
    url_encode_decode,
    uuid_generate,
    _MS_THRESHOLD,
    _epoch_format,
    epoch_convert,
    _COLOR_NAMES,
    _RGB_RE,
    _percent,
    color_convert,
    ip_subnet_calc,
)
from .skills_dev_explain import (
    _FLAG_MAP,
    regex_test,
    _MONTH_NAMES,
    _WEEKDAY_NAMES,
    _CRON_FIELDS,
    _cron_name,
    _cron_field,
    _cron_values,
    _cron_join,
    _cron_field_phrase,
    cron_explain,
    _SEMVER_RE,
    _semver_parse,
    _semver_pre_cmp,
    semver_compare,
    text_diff,
)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register every Developer pack tool on a ToolRegistry-like object."""
    registry.register(
        name="regex_test",
        description="Test a Python regular expression against a block of text. Reports how many matches there are and shows the first 5 with their character positions and the groups each one captured. The flags letters i (ignore case), m (multiline) and s (dot matches newlines) can be combined; an invalid pattern gets the parser's complaint in plain words. Use when building or debugging a pattern.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regular expression to test"},
                "text": {"type": "string", "description": "The text to search"},
                "flags": {"type": "string", "description": "Optional flag letters: i, m, s (default none)"},
            },
            "required": ["pattern", "text"],
        },
        handler=regex_test,
    )
    registry.register(
        name="base_convert",
        description="Rewrite a whole number from one base into another — bases 2 to 36, given as numbers or as the words binary, octal, decimal or hex. Negative numbers keep their sign, and a digit that doesn't exist in the source base is named rather than silently misread. Use for hex addresses, permissions, and binary puzzles. For everyday unit conversions (length, weight, data sizes) unit_convert is the tool.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "The number to convert, written in the from_base"},
                "from_base": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "The base the number is written in: 2–36, or binary/octal/decimal/hex"},
                "to_base": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "The base to rewrite it in: 2–36, or binary/octal/decimal/hex"},
            },
            "required": ["value", "from_base", "to_base"],
        },
        handler=base_convert,
    )
    registry.register(
        name="url_encode_decode",
        description="Percent-encode text so it is safe inside a URL (spaces become '+', '&' becomes '%26'), or decode a URL-encoded string back into plain text. Input is capped at 2000 characters and the cap is disclosed when it bites. Use when building links or untangling a copied URL.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to encode, or the encoded text to decode"},
                "mode": {"type": "string", "enum": ["encode", "decode"], "description": "'encode' (default) or 'decode'"},
            },
            "required": ["text"],
        },
        handler=url_encode_decode,
    )
    registry.register(
        name="uuid_generate",
        description="Generate UUIDs — one by default, up to 10 at a time. Version 4 is random (the usual choice for new IDs); version 1 is time-based. Use when a database row, a file or a request needs a fresh unique identifier. generate_uuid does the same job — the two are interchangeable.",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "How many to make, 1 to 10 (default 1)"},
                "version": {"type": "integer", "description": "4 for random (default) or 1 for time-based"},
            },
            "required": [],
        },
        handler=uuid_generate,
    )
    registry.register(
        name="cron_explain",
        description="Explain a 5-field cron expression in plain words — '0 9 * * 1-5' becomes 'At 09:00 on weekdays (Monday to Friday), every week'. Common shapes (daily, weekly, monthly, hourly, every N minutes) get one clean sentence; anything more exotic is described field by field, and a field that is out of range or unreadable is named. Weekdays run 0–7 with both 0 and 7 meaning Sunday. Use to check a schedule before trusting it.",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "The cron expression, e.g. '0 9 * * 1-5'"},
            },
            "required": ["expression"],
        },
        handler=cron_explain,
    )
    registry.register(
        name="semver_compare",
        description="Compare two semantic versions (major.minor.patch, with an optional prerelease like -beta.1) and say which is newer — or that they are the same. A prerelease is always called out as older than its own release, and build metadata after a '+' is noted but never changes the ordering. Use when checking an upgrade or a minimum-version requirement.",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "The first version, e.g. '1.2.3'"},
                "b": {"type": "string", "description": "The second version, e.g. '1.3.0-beta.1'"},
            },
            "required": ["a", "b"],
        },
        handler=semver_compare,
    )
    registry.register(
        name="text_diff",
        description="Compare two blocks of text line by line and show a unified diff — lines starting with '-' are only in the first text, '+' only in the second. Identical texts are reported as having no differences, and a long diff is capped at 60 lines with the cut disclosed. For comparing two JSON documents key by key, json_diff is the tool; for files on disk, compare_files.",
        parameters={
            "type": "object",
            "properties": {
                "text_a": {"type": "string", "description": "The first block of text"},
                "text_b": {"type": "string", "description": "The second block of text"},
            },
            "required": ["text_a", "text_b"],
        },
        handler=text_diff,
    )
    registry.register(
        name="epoch_convert",
        description="Convert between unix timestamps and human dates. Give digits and it reads them as seconds — or as milliseconds when the number is big enough (and says which it chose) — and returns the moment in UTC and in local time. Give an ISO date/time like 2026-10-22T08:00:00 instead and it returns the unix seconds. Use when reading logs, APIs or token expiry times.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": "integer"}, {"type": "string"}], "description": "A unix timestamp (seconds or milliseconds), or an ISO date/time string"},
            },
            "required": ["value"],
        },
        handler=epoch_convert,
    )
    registry.register(
        name="color_convert",
        description="Convert a colour between its written forms: give #RRGGBB or #RGB hex, an rgb(r, g, b) triple, or a basic colour name (red, teal, navy, orange and about twenty more) and get the hex, rgb and hsl forms back. Use when moving a colour between a stylesheet, a design tool and a config file.",
        parameters={
            "type": "object",
            "properties": {
                "color": {"type": "string", "description": "The colour: '#FF0000', 'rgb(255, 0, 0)' or a basic name"},
            },
            "required": ["color"],
        },
        handler=color_convert,
    )
    registry.register(
        name="ip_subnet_calc",
        description="Work out the numbers of an IP network in CIDR form like 192.168.1.0/24: the network address, netmask, broadcast address, first and last usable hosts, how many usable hosts there are, and whether the range is private or public. /31 point-to-point links and /32 single hosts are explained rather than given nonsense host counts. Use when planning or checking a network.",
        parameters={
            "type": "object",
            "properties": {
                "cidr": {"type": "string", "description": "The network in CIDR form, e.g. '192.168.1.0/24'"},
            },
            "required": ["cidr"],
        },
        handler=ip_subnet_calc,
    )
