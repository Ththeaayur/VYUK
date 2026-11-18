"""
Web skills VI for VYUK (Phase 2 §4): watch a price, say what
changed on a page, read a page aloud, batch-check links, estimate
a download's finish, review site permissions, census the open
tabs and decode a QR code from an image.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result. Page fetches reuse the Web II pack's proxy-aware fetch
(core/skills_web_research._fetch, at most 10 seconds, byte caps disclosed)
and state files live in the same data folder, beside page_watch's.
Read/write split: page_change_summary, read_page_aloud,
link_health_check, download_eta, site_permissions_review,
tab_census and qr_decode_image only look (page_change_summary
keeps a headline snapshot of its own — never full page bodies);
price_watch keeps a small watch list.

Deliberately NOT here: reachability of one page is
website_status, the deep single-link inspection is sniff_link /
link_unshorten, THAT a page changed is page_watch (this pack says
WHAT changed), and tab control is browser_control (this pack only
counts and lists).

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""


# INTENTIONAL: the old module's namespace carried these stdlib imports;
# re-exported so the facade's dir() matches the old module exactly
# (files3/files2 leaked-import lesson, plan §2 P2 dir-diff-empty bar).
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from . import senses
from . import skills_web_research as _web5
from .config import DATA_DIR


# INTENTIONAL: every moved name re-exported with identity, so the
# facade's namespace is the old module's namespace (plan §2 P2).
from .skills_web6_helpers import (_UA, _TIMEOUT, _PAGE_CAP, _LINK_CAP, _READ_CAP, _PRICE_FILE, _SNAP_FILE, _fetch, _normalise_url, _size_words, _SKIP_TAGS, _BLOCK_TAGS, _PageText, _parse_page, _visible_text, _headline_lines)
from .skills_web6_watch import (_PRICE_BEFORE, _PRICE_AFTER, _first_price, _price_path, _load_prices, _save_prices, _check_one_price, price_watch, _snap_path, _load_snaps, _save_snaps, page_change_summary, read_page_aloud, _StopRedirect, _probe, link_health_check)
from .skills_web6_local import (_PARTIAL_SUFFIXES, _downloads_dir, download_eta, _PERM_LABELS, _profile_pref_files, site_permissions_review, _tab_rows, tab_census, qr_decode_image)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Web III pack tool on a ToolRegistry-like object."""
    registry.register(
        name="price_watch",
        description="Watch a product page's price. action 'add' saves the page (with a target price if one is given), 'check' fetches it now and reports the first price found in its visible text — and whether that's at or under the target — 'list' shows the saved watches, 'remove' drops one. A page that hides prices behind a login or scripts honestly comes back 'no price found'. Watches live in VYUK's data folder.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["add", "check", "list", "remove"],
                           "description": "add (save a watch), check (default; fetch and compare), list, or remove"},
                "url": {"type": "string",
                        "description": "The product page (not needed for action 'list')"},
                "target_price": {"type": "number",
                                 "description": "The price at or under which it's a deal (optional, for 'add')"},
            },
            "required": [],
        },
        handler=price_watch,
    )
    registry.register(
        name="page_change_summary",
        description="Say what changed on a page since the last check, in plain words: fetches the page, compares its headline lines with the saved snapshot, lists the new and removed lines, then saves the new snapshot. The first check on a page just saves the snapshot. Works alongside page_watch, which only notices THAT a page changed; snapshots live in VYUK's data folder.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to check for changes, e.g. 'https://example.com/news'"},
            },
            "required": ["url"],
        },
        handler=page_change_summary,
    )
    registry.register(
        name="read_page_aloud",
        description="Fetch a web page and return its article text ready to be read aloud: the title and word count, then the main text with menus, ads and scripts stripped out. Long articles stop at a paragraph boundary with an honest note about how much was left unread; max_words stops the reading early, at about that many words. Use when the user asks to have a page, article or blog post read to them. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to read, e.g. 'https://example.com/article'"},
                "max_words": {"type": "integer",
                              "description": "Optional: stop after about this many words (0 = read the whole page, default)"},
            },
            "required": ["url"],
        },
        handler=read_page_aloud,
    )
    registry.register(
        name="link_health_check",
        description="Check a batch of links (up to 15, separated by commas or semicolons) and report each in plain words: alive with its status, redirecting (and where it finally lands), or dead with the reason — closing with alive / redirecting / dead totals. Each link gets at most 8 seconds. Use when the user pastes several links and asks which still work. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "urls": {"type": "string",
                         "description": "The links to check, separated by commas or semicolons"},
            },
            "required": ["urls"],
        },
        handler=link_health_check,
    )
    registry.register(
        name="download_eta",
        description="Look in the Downloads folder for a download still in progress (a .part, .crdownload or similar partial file), watch it for a couple of seconds, and report its size and how fast it's growing in plain words — with a finish estimate only when the total size is actually knowable. Says honestly when nothing is downloading or no progress can be measured. Read-only.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=download_eta,
    )
    registry.register(
        name="site_permissions_review",
        description="List which websites the browser has been allowed to use the camera, microphone, location or notifications for, by reading the Chrome/Edge profile settings files. Strictly read-only — the profiles are never written to. Use when the user asks which sites can reach their camera or mic, or wants a privacy review of browser permissions.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=site_permissions_review,
    )
    registry.register(
        name="tab_census",
        description="Count and list the open browser tabs through VYUK's connection to your real Chrome, grouping tabs that show the same page ('7 tabs, 3 on the same page'). Needs the browser connected first (attach to your Chrome); says so honestly when it isn't. Use when the user asks how many tabs they have open or what is open where. Read-only.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=tab_census,
    )
    registry.register(
        name="qr_decode_image",
        description="Read a QR code from an image file and report exactly what it says — a link or plain text. Uses OpenCV when it is installed on this PC; when it isn't, or no code in the image can be read, it says so honestly and never guesses at a code's contents. Use when the user points at a screenshot or photo and asks what its QR code says. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "image_path": {"type": "string",
                               "description": "Path to the image file holding the QR code, e.g. '~/Pictures/code.png'"},
            },
            "required": ["image_path"],
        },
        handler=qr_decode_image,
    )
