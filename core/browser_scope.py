"""Browser scope data (click-anywhere Batch 0/1, Plan 9 §4).

The scope guard's lists, stdlib only so the plan-approval gate and
the pickers can all read them without import cycles:

  * ALLOWED_SITES — the sites a browser goal may act on, with a
    per-site scope. ``"full"`` means reads + clicks + navigation;
    ``"detection-only"`` means the host is recognised (login-wall
    detection) but never read or clicked.
  * D18_WORDS — the D-18 always-ask word list (M-34): whole-word,
    case-insensitive, matched against a control's name/text.
  * TIER1_ACTION_CLASSES — the action classes that tier a browser
    click/type/press as Tier 1 (navigation / read-only); anything
    else tiers Tier 2 (fail-safe).

Licence: own code — nothing adopted, no upstream, no third-party
licence text (Parent-ruled (licence, 2026-11-14)).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# §4 scope guard. The proof site is full-scope; the SSO host is
# detection-only ("login host, detection only: no reads, no
# clicks"). The list is deliberately short: a site joins it by
# the owner's decision, never by being visited.
ALLOWED_SITES = {
    "resobin.gymkhana.iitb.ac.in": "full",
    "gymkhana.iitb.ac.in": "detection-only",
}

# D-18 always-ask words (M-34 list): pay / send / delete / submit /
# download / logout — whole-word, case-insensitive, on the control's
# name/text. Like / Follow / Save / Star are state changes too (§4
# classification by effect) but are NOT in the D-18 word list the
# gate backstop matches on; the picker labels them consequential.
D18_WORDS = ("pay", "send", "delete", "submit", "download", "logout")

# Action classes that tier a browser click/type/press as Tier 1.
TIER1_ACTION_CLASSES = frozenset({"navigation", "read-only"})


def host_of(url_or_host) -> str:
    """The lowercase host of a URL or a bare host string, or "".

    A bare host ("resobin.gymkhana.iitb.ac.in") parses as a path,
    so a string without "://" is treated as a host directly. A
    port is stripped; a userinfo prefix is never a host."""
    text = str(url_or_host or "").strip().lower()
    if not text:
        return ""
    if "://" not in text:
        host = text.split("/")[0].split(":")[0]
        return host
    try:
        host = urlparse(text).hostname or ""
    except Exception:  # noqa: BLE001 — unparseable: no host
        return ""
    return host


def site_scope(host: str):
    """The scope for a host ("full" / "detection-only"), or None.

    Exact match first; else the longest listed suffix (a subdomain
    of a listed host inherits that host's scope). The longest
    match wins, so resobin.gymkhana.iitb.ac.in is "full" even
    though it is also a subdomain of gymkhana.iitb.ac.in
    ("detection-only")."""
    host = host_of(host)
    if not host:
        return None
    if host in ALLOWED_SITES:
        return ALLOWED_SITES[host]
    best = None
    for listed, scope in ALLOWED_SITES.items():
        if host.endswith("." + listed):
            if best is None or len(listed) > len(best[0]):
                best = (listed, scope)
    return best[1] if best else None


def site_allowed_for_navigation(host: str) -> bool:
    """True when the host may be navigated to under plan cover."""
    return site_scope(host) == "full"


_D18_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in D18_WORDS) + r")\b",
    re.IGNORECASE)


def matches_d18_words(text) -> bool:
    """True when the text matches a D-18 word (whole-word,
    case-insensitive). An empty/absent text matches nothing —
    an icon-only control with no accessible name/text is the
    picker's problem, not the backstop's (§2.5 residual)."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _D18_RE.search(text) is not None


# -- Click-anywhere Batch 1 (Plan 9 §4): D-17 messaging guard, ----------
# -- composer-context test, login-wall recogniser, game deny-list, ------
# -- click backstop (D-18 words + state-change words). -------------------

# D-17 messaging domains (M-36/M-39/P-16/Q-13/R-12). The list is
# non-exhaustive; the role rule (composer_context_test below) is
# authoritative for unlisted apps. instagram.com is messaging
# ONLY on DM paths (/direct/); the feed and profiles are not.
MESSAGING_DOMAINS = (
    "web.whatsapp.com",
    "messenger.com",
    "instagram.com",
    "web.telegram.org",
    "discord.com",
    "discordapp.com",
    "messages.google.com",
    "app.slack.com",
)

# D-18 always-ask words PLUS the §4 state-change set (Like / Follow /
# Save / Star are state changes to page/account data, M-32). This is
# the CLICK BACKSTOP match list for click_anywhere args: the
# browser_gates regexes (_DANGEROUS_CLICK_RE, _DESTRUCTIVE_SUBMIT_RE)
# contain NEITHER "download" NOR "logout", so the backstop does NOT
# rely on them — it matches on this list, which is complete.
BACKSTOP_WORDS = D18_WORDS + ("like", "follow", "save", "star")

_BACKSTOP_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BACKSTOP_WORDS) + r")\b",
    re.IGNORECASE)


def matches_backstop_words(text) -> bool:
    """True when the text matches a click-backstop word
    (whole-word, case-insensitive). Empty/absent matches nothing."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _BACKSTOP_RE.search(text) is not None


def is_messaging_domain(host: str, path: str = "") -> bool:
    """True when (host, path) is a D-17 messaging destination.

    instagram.com counts only on DM paths (/direct/...); the feed
    and profiles are not messaging. Every other listed domain
    counts on any path. Unlisted hosts are NOT messaging by
    domain — the composer-context role test decides those."""
    host = host_of(host)
    if not host:
        return False
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return str(path or "").startswith("/direct/")
    for listed in MESSAGING_DOMAINS:
        if host == listed or host.endswith("." + listed):
            return True
    return False


# -- Composer-context test (P-16/Q-13/R-12, EXACT) ----------------------
# A Send button is a messaging send -> refuse when BOTH hold:
#   (a) the Send button and a text input or contenteditable share
#       a nearest common ancestor within 3 levels (each element
#       <= 3 ancestors below the NCA), OR sit in the same ARIA
#       region/landmark; AND
#   (b) a thread signal is present — within 3 levels of the NCA,
#       or in the same landmark as the NCA. A thread signal is an
#       element with role="log", OR role="list" containing >= 1
#       descendant with role="listitem".
# A DOM node is a dict: {tag, role, attrs, children, text}.
# "Within 3 levels" is defined: each element is at most 3
# ancestors below the nearest common ancestor.

def _node_role(node) -> str:
    if not isinstance(node, dict):
        return ""
    role = node.get("role")
    if role:
        return str(role).strip().lower()
    attrs = node.get("attrs") or {}
    if isinstance(attrs, dict):
        return str(attrs.get("role") or "").strip().lower()
    return ""


def _node_tag(node) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("tag") or "").strip().lower()


def _node_attrs(node) -> dict:
    if not isinstance(node, dict):
        return {}
    attrs = node.get("attrs") or {}
    return attrs if isinstance(attrs, dict) else {}


def _node_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("text") or "")


def _walk(node):
    """Yield (node, path) with path = ancestor list, root first."""
    if not isinstance(node, dict):
        return
    yield node, []
    for child in node.get("children") or []:
        for sub, subpath in _walk(child):
            yield sub, [node] + subpath


def _is_text_input(node) -> bool:
    tag = _node_tag(node)
    attrs = _node_attrs(node)
    if tag == "textarea":
        return True
    if tag == "input":
        itype = str(attrs.get("type") or "text").strip().lower()
        return itype in ("text", "search", "email", "tel", "url", "")
    return str(attrs.get("contenteditable") or "").strip().lower() in (
        "true", "")


def _is_send_button(node) -> bool:
    tag = _node_tag(node)
    if tag not in ("button", "input"):
        return False
    if tag == "input":
        itype = str(_node_attrs(node).get("type") or "").strip().lower()
        if itype not in ("submit", "button"):
            return False
    text = (_node_text(node) + " "
            + str(_node_attrs(node).get("aria-label") or "")).strip().lower()
    return bool(re.search(r"\bsend\b", text))


def _is_thread_signal(node) -> bool:
    if _node_role(node) == "log":
        return True
    if _node_role(node) == "list":
        for child, _ in _walk(node):
            if child is not node and _node_role(child) == "listitem":
                return True
    return False


def _is_landmark(node) -> bool:
    return _node_role(node) in (
        "region", "main", "navigation", "complementary", "banner",
        "contentinfo", "form", "search", "dialog", "alertdialog")


def _nearest_common_ancestor(path_a, path_b):
    """Deepest node shared by both ancestor paths (root first)."""
    common = None
    for a, b in zip(path_a, path_b):
        if a is b:
            common = a
        else:
            break
    return common


def _depth_below(node_path, ancestor):
    """Ancestors between ancestor (exclusive) and the node."""
    try:
        idx = next(i for i, n in enumerate(node_path) if n is ancestor)
    except StopIteration:
        return None
    return len(node_path) - 1 - idx


def composer_context_test(root, *, send_node=None, input_node=None):
    """The D-17 composer-context test over a DOM-node dict tree.

    Returns {"messaging_send": bool, "reason": str}. When no Send
    button + text input pair is found the test cannot fire and the
    result is {"messaging_send": False, reason: "no send composer
    found — ask under D-18 if consequential"}. A contact form
    (inputs + Send, no thread signal) is a SUBMIT, not a message:
    messaging_send False with reason "submit — ask under D-18".
    Uncertain -> messaging_send False (ask, never silently refuse).
    """
    nodes = list(_walk(root))
    if send_node is None:
        send_node = next((n for n, _ in nodes if _is_send_button(n)),
                         None)
    if input_node is None:
        input_node = next((n for n, _ in nodes if _is_text_input(n)),
                          None)
    if send_node is None or input_node is None:
        return {"messaging_send": False,
                "reason": ("no send composer found — ask under D-18 "
                           "if consequential")}
    paths = {id(n): p for n, p in nodes}
    send_path = paths.get(id(send_node), [])
    input_path = paths.get(id(input_node), [])
    # Ancestor chains, root first, including the node itself
    # (_walk yields ancestor paths root-first already).
    send_chain = send_path + [send_node]
    input_chain = input_path + [input_node]
    nca = _nearest_common_ancestor(send_chain, input_chain)
    within_three = False
    same_landmark = False
    if nca is not None:
        d_send = _depth_below(send_chain, nca)
        d_input = _depth_below(input_chain, nca)
        within_three = (d_send is not None and d_input is not None
                        and d_send <= 3 and d_input <= 3)
    # Same ARIA region/landmark: the nearest landmark ancestor of
    # each element is the same node.
    def _landmark_of(chain):
        for node in reversed(chain):
            if _is_landmark(node):
                return node
        return None
    lm_send = _landmark_of(send_chain)
    lm_input = _landmark_of(input_chain)
    same_landmark = lm_send is not None and lm_send is lm_input
    part_a = within_three or same_landmark
    if not part_a:
        return {"messaging_send": False,
                "reason": ("send button and text input share no "
                           "composer context — ask under D-18 if "
                           "consequential")}
    # (b) thread signal within 3 levels of the NCA, or in the
    # same landmark as the NCA (the NCA's nearest landmark
    # ancestor, the NCA itself included).
    def _landmark_of(chain):
        for node in reversed(chain):
            if _is_landmark(node):
                return node
        return None
    nca_landmark = None
    if nca is not None:
        nca_chain = [n for n, _ in nodes if n is nca]
        # Rebuild the NCA's own chain from the send chain: the
        # NCA sits on both chains, so its chain is the shared
        # prefix of the send chain.
        shared = []
        for a, b in zip(send_chain, input_chain):
            if a is b:
                shared.append(a)
            else:
                break
        nca_landmark = _landmark_of(shared)
    part_b = False
    for node, path in nodes:
        if not _is_thread_signal(node):
            continue
        chain = path + [node]
        if nca is not None:
            depth = _depth_below(chain, nca)
            if depth is not None and depth <= 3:
                part_b = True
                break
        if (nca_landmark is not None
                and _landmark_of(chain) is nca_landmark):
            part_b = True
            break
    if part_b:
        return {"messaging_send": True,
                "reason": ("D-17 messaging send: Send button and text "
                           "input share a composer context and a "
                           "message thread is present — refused")}
    return {"messaging_send": False,
            "reason": ("no thread signal — a submit, not a message: "
                       "ask under D-18, never silently refuse")}


# -- Login-wall recogniser + captcha (§4 scope guard) --------------------

# The SSO host is detection-only in ALLOWED_SITES; a URL on it is a
# login wall by host alone.
SSO_HOST = "gymkhana.iitb.ac.in"


def is_login_wall(*, url="", title="", has_password_field=False,
                  has_sso_button=False) -> bool:
    """True when the page is a login wall: the URL host is the SSO
    host, OR a password field + SSO button are present, OR the
    page title contains "Sign in". A wall means stop — never type
    a password."""
    if host_of(url) == SSO_HOST or host_of(url).endswith("." + SSO_HOST):
        return True
    if has_password_field and has_sso_button:
        return True
    return "sign in" in str(title or "").lower()


def is_captcha_text(text) -> bool:
    """True when page text names a captcha challenge. A captcha
    means stop — never solve."""
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return ("captcha" in lowered or "i'm not a robot" in lowered
            or "verify you are human" in lowered)


# -- Password rule (M-38): DOM type path ---------------------------------

def dom_input_is_password(node) -> bool:
    """True when the DOM node is a password input (type=password).
    Refuse before any DOM-set or type on it."""
    if not isinstance(node, dict):
        return False
    if _node_tag(node) != "input":
        return False
    return str(_node_attrs(node).get("type") or "").strip().lower() \
        == "password"


# -- Game deny-list (M-37) ------------------------------------------------
# A named, versioned static file: core/browser/game_denylist.json.
# Re-checked before EVERY act from the foreground identity in the
# observation; a game appearing mid-goal is caught at the next
# observe. The parent adds a game on his report.

import json as _json  # noqa: E402
import pathlib as _pathlib  # noqa: E402

_GAME_DENYLIST_PATH = (
    _pathlib.Path(__file__).resolve().parent
    / "browser" / "game_denylist.json")


def load_game_denylist():
    """The versioned game deny-list ({version, games:[...]}),
    or {"version": 0, "games": []} when the file is absent or
    unreadable (fail-open on the LIST, never on the act: a
    missing list means no game is recognised, and the caller
    still refuses unknown foregrounds it cannot identify)."""
    try:
        data = _json.loads(_GAME_DENYLIST_PATH.read_text(
            encoding="utf-8"))
    except Exception:  # noqa: BLE001 — absent/unreadable: empty list
        return {"version": 0, "games": []}
    if not isinstance(data, dict):
        return {"version": 0, "games": []}
    games = data.get("games") or []
    if not isinstance(games, list):
        games = []
    return {"version": data.get("version", 0),
            "games": [str(g) for g in games]}


def is_game_foreground(identity, *, denylist=None) -> str | None:
    """The matched game name when the foreground identity names a
    denylisted game (process name or window title, whole-word,
    case-insensitive), else None. The identity is a dict with
    "proc"/"process"/"title" keys, or a plain string."""
    if denylist is None:
        denylist = load_game_denylist()
    games = [g for g in (denylist.get("games") or []) if str(g).strip()]
    if not games:
        return None
    if isinstance(identity, dict):
        hay = " ".join(str(identity.get(k) or "")
                       for k in ("proc", "process", "title", "app"))
    else:
        hay = str(identity or "")
    for game in games:
        if re.search(r"\b" + re.escape(game) + r"\b", hay,
                     re.IGNORECASE):
            return game
    return None
