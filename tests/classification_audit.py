"""Classification-correctness audit (loophole batch 5, L-22).

The hole: the enumeration tests catch a MISSING classification,
not a WRONG one. A tool wrongly classified read (or free) sails
through the gate and the tests never notice — and the values
snapshot (tests/_classification_snapshot.json) is GENERATED from
the tree, so a flip + regeneration passes silently. This module
closes it with two independent checks, both pure functions so a
mutation test can call them with mutated gate sets:

1. THE EVIDENCE TABLE (tests/_classification_evidence.json).
   One entry per tool: {"class": "read" | "confirm" | "free",
   "basis": "<first sentence of the tool description> + handler
   module"}. The table is generated from the current
   classification and HAND-REVIEWED for the owner-only families
   (the L-24 boundary) and the confirm-gated families. The test
   asserts the live gate sets agree with the table — so a flip
   disagrees with the table and fails, and regenerating the
   snapshot alone cannot hide it (the evidence table is a
   separate, reviewed artifact, not a regeneration of the same
   sets).

2. THE AST EFFECT SCAN. For every READ-classified tool, the
   checker's AST scan reads the handler's own source and flags
   effect-indicating operations (writes, deletes, sends, process
   launches, clicks, SQL executes, commits). A read tool whose
   handler performs an effect is a wrong classification — the
   scan names the tool and the operation. Benign hits are pinned
   in the allowlist below with a reason (logging, caching, the
   app's own audit trail); the allowlist is part of the check,
   so a new benign pattern must be reviewed and pinned, not
   silently absorbed.

Both checks run in the suite (tests/test_loophole_batch5.py)
and in the repro's mutation leg: flip one tool's classification
in a scratch import (send_email into _READ_ONLY_TOOLS) and the
checker fails naming it.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import re

# ------------------------------------------------------------------
# The effect scan.
#
# An effect-indicating operation, by name. These are AST-level
# signals (a Call to an attribute / name with this spelling) —
# the scan reads the handler's own source, so a tool that
# performs an effect through a helper it calls directly is
# caught; an effect hidden two helpers down is not (stated limit
# — the evidence table is the second leg, and the two together
# catch a flip at either layer).
#
# DELIBERATELY NARROW. The first draft of this list flagged 224
# hits, almost all of them list/set/dict building (append, add,
# insert, set, update), cleanup (close), and in-memory work
# (hashlib update, threading lock, creating an object). Those
# spellings are NOT in this list: a read handler builds its
# answer in memory, and the scan must not cry wolf on it. What
# remains is the high-signal set — a call with one of these
# spellings in a READ handler is either a real effect (the
# classification is wrong) or a pinned benign pattern (the
# allowlist, with its reason).
_EFFECT_ATTRS = frozenset({
    # Persistence — a write to a store, a file, or the disk.
    "save", "delete", "remove", "write",
    "write_text", "write_bytes", "commit", "unlink", "rename",
    "mkdir", "makedirs", "rmdir", "touch", "chmod", "truncate",
    # Sending / egress — bytes leaving the machine.
    "send", "sendto", "sendall", "post", "put", "patch",
    # Processes / the machine.
    "launch", "kill", "terminate", "system",
    "startfile", "spawn", "popen",
    # Input / the desktop.
    "click", "press", "hotkey", "move", "scroll",
    "drag", "double_click", "right_click",
    # State — a registry / service / account change.
    "register", "unregister", "enable", "disable",
    # Machine power / the session.
    "quit", "restart", "shutdown", "reboot",
})

# Bare-name calls that are effects (no attribute — a direct
# function call).
_EFFECT_NAMES = frozenset({
    "eval", "exec", "input",
})

# Module-level effect signals, matched on the dotted call
# spelling in the source (the AST walk records the dotted name
# of every Call's function; these are the effectful ones).
# "socket." is deliberately NOT here: a port check / DNS lookup
# is a read (create_connection, getaddrinfo), and a socket SEND
# is caught by the "send" attribute above. "urllib.request.
# urlopen" is not here either: a GET is a read, and a POST is
# caught by the "post" attribute on the request call.
_EFFECT_MODULES = (
    "os.remove", "os.rename", "os.mkdir", "os.makedirs",
    "os.rmdir", "os.system", "os.startfile", "os.exec",
    "os.kill", "os.chmod", "os.utime", "os.link", "os.symlink",
    "shutil.", "subprocess.", "smtplib.",
    "requests.post", "requests.put", "requests.patch",
    "requests.delete", "webbrowser.",
    "ctypes.", "pyautogui.", "keyboard.", "mouse.",
    "win32api.", "win32gui.", "win32con.",
)

# SQL write keywords: an "execute" call is an effect only when
# its SQL writes (INSERT / UPDATE / DELETE / DROP / CREATE /
# ALTER / REPLACE). A SELECT-only execute is a read and is not
# flagged. Matched against the call's source text.
_SQL_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|REPLACE|TRUNCATE"
    r"|VACUUM|ATTACH|DETACH)\b", re.IGNORECASE)

# Benign hits, pinned with a reason. Format: (tool, token) ->
# reason. A hit is ignored ONLY when the tool AND the token
# both match — a new tool hitting the same token still fails.
# Reviewed by hand against the handler source (batch 5).
_EFFECT_ALLOWLIST: dict[tuple[str, str], str] = {
    # battery_health deletes the temp battery-report file the
    # handler itself generated (powercfg output in the temp
    # dir) — cleanup of its own scratch, not the user's data.
    ("battery_health", "unlink"):
        "deletes its own temp powercfg report file",
    # browser_bookmarks copies the locked bookmarks file into
    # a TemporaryDirectory and reads the copy; the live file is
    # never opened and its size + mtime are verified unchanged.
    ("browser_bookmarks", "shutil.copy2"):
        "temp copy of a locked browser file, read-only query",
    # get_active_window allocates a buffer to RECEIVE the window
    # title (a read) — ctypes buffer allocation, not a call.
    ("get_active_window", "ctypes.create_unicode_buffer"):
        "buffer allocation for a window-title read",
}


def _dotted_name(node: ast.AST) -> str:
    """The dotted spelling of a Call's function, e.g.
    "os.remove", "svc.users().messages().send" (a fluent API
    chain — intermediate calls are walked through, so the
    Gmail send chain spells as svc.users.messages.send), or
    "self._store.save_fact". Returns "" when it cannot be
    spelled."""
    parts: list[str] = []
    cur = node
    while True:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Call):
            # A fluent chain (svc.users().messages().send):
            # walk through the intermediate call into its
            # function — the attribute chain continues there.
            cur = cur.func
        elif isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        else:
            return ""


def _attr_is_effect(attr: str) -> bool:
    """True when an attribute name is an effect verb — the verb
    itself ("send", "save") or a verb-prefixed spelling
    ("save_fact", "send_email", "delete_row"). A read handler
    calling save_fact writes a fact; a read handler calling
    send_email sends one."""
    if attr in _EFFECT_ATTRS:
        return True
    return any(attr.startswith(verb + "_") for verb in _EFFECT_ATTRS)


def _scan_handler_source(name: str, source: str) -> list[str]:
    """Effect-indicating operations found in one handler's
    source. Returns a list of "token" strings (the dotted call
    spelling or the attribute name). Never raises — an
    unparseable handler is reported as a scan failure, not
    silently passed."""
    import textwrap

    try:
        # inspect.getsource on a bound method returns the source
        # WITH its class indentation — dedent before parsing, or
        # every method handler reads as "unparseable".
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        return [f"unparseable ({exc})"]
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func)
        if not dotted:
            continue
        # The module-level signals first (most specific).
        hit = None
        for prefix in _EFFECT_MODULES:
            if dotted.startswith(prefix) or dotted == prefix.rstrip("."):
                hit = dotted
                break
        if hit is None:
            # The attribute name (the last segment).
            attr = dotted.rsplit(".", 1)[-1]
            if attr in ("execute", "executemany"):
                # SQL is an effect only when it writes — a
                # SELECT-only execute is a read. Check the
                # call's source text for a write keyword.
                call_text = ast.get_source_segment(source, node) or ""
                if _SQL_WRITE_RE.search(call_text):
                    hit = attr
            elif _attr_is_effect(attr):
                hit = attr
            elif dotted in _EFFECT_NAMES:
                hit = dotted
        if hit is None:
            continue
        if (name, hit) in _EFFECT_ALLOWLIST:
            continue
        found.append(hit)
    return sorted(set(found))


def _handler_source(name: str) -> str | None:
    """One tool's handler source, or None when it cannot be
    read (a plugin tool, a bound builtin, a missing handler)."""
    try:
        from core.funnel.tools_registry import tools as _tools

        handler = _tools._tools[name]["handler"]
        return inspect.getsource(handler)
    except Exception:  # noqa: BLE001 — unresolvable: the caller decides
        return None


# ------------------------------------------------------------------
# The evidence table.

def load_evidence(tests_dir: str | pathlib.Path) -> dict:
    """The evidence table (tests/_classification_evidence.json).
    Raises FileNotFoundError when the table is missing — a
    missing table is a failure, not a pass."""
    path = pathlib.Path(tests_dir) / "_classification_evidence.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _class_of(name: str, read_names, confirm_names) -> str:
    if name in read_names:
        return "read"
    if name in confirm_names:
        return "confirm"
    return "free"


def check_evidence_and_handlers(
        read_names, confirm_names=None, tests_dir=None,
        handler_source=None) -> list[str]:
    """The pure check (the mutation leg calls this with mutated
    gate sets). Returns a list of failure strings; empty means
    the classification agrees with the evidence table AND no
    read-classified tool's handler performs an effect.

    read_names: the read set to check (a set of tool names).
    confirm_names: the confirm set (defaults to the live one).
    tests_dir: the tree's tests/ dir (for the evidence table).
    handler_source: an override for reading a handler's source
    (defaults to the live registry) — the mutation test uses
    the live one.
    """
    failures: list[str] = []
    if confirm_names is None:
        from core.funnel.tool_classification import (
            _CONFIRM_GATE_TOOLS)
        confirm_names = set(_CONFIRM_GATE_TOOLS)
    if tests_dir is None:
        tests_dir = pathlib.Path(__file__).resolve().parent
    try:
        evidence = load_evidence(tests_dir)
    except FileNotFoundError:
        return ["evidence table missing: "
                "tests/_classification_evidence.json"]
    except Exception as exc:  # noqa: BLE001 — a broken table fails
        return [f"evidence table unreadable: {exc}"]
    read_set = set(read_names)
    confirm_set = set(confirm_names)
    # 1. The evidence table agrees with the gate sets.
    for name in sorted(evidence):
        want = str(evidence[name].get("class", ""))
        got = _class_of(name, read_set, confirm_set)
        if want and want != got:
            failures.append(
                f"{name}: evidence says {want}, gate says {got}")
    for name in sorted(read_set):
        if name not in evidence:
            failures.append(f"{name}: read-classified but no "
                            "evidence-table entry")
    # 2. The AST effect scan over every read-classified tool.
    source_fn = handler_source or _handler_source
    for name in sorted(read_set):
        source = source_fn(name)
        if source is None:
            failures.append(f"{name}: handler source unreadable")
            continue
        for token in _scan_handler_source(name, source):
            failures.append(
                f"{name}: read-classified but handler performs "
                f"an effect ({token})")
    return failures


def check_all(tests_dir: str | pathlib.Path) -> list[str]:
    """The check against the LIVE gate sets. Returns a list of
    failure strings; empty means the live classification agrees
    with the evidence table and no read tool's handler performs
    an effect."""
    from core.funnel.tool_classification import (
        _CONFIRM_GATE_TOOLS, _READ_ONLY_TOOLS)
    return check_evidence_and_handlers(
        read_names=set(_READ_ONLY_TOOLS),
        confirm_names=set(_CONFIRM_GATE_TOOLS),
        tests_dir=tests_dir)
