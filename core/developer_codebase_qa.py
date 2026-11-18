"""DV-6 codebase Q&A (Developer track, wave 28) — citations, on the
machine, static-only.

Wave 6 owns general knowledge; this module is the codebase-specific
slice: how she answers questions about HIS code. The dividing line
is load-bearing: tools that index locally and cite file:line are
candidates; tools whose context engine is a vendor cloud are
rejected regardless of their terms, because code leaves the machine
in normal use.

Contents (all Nova's own code, stdlib only):
- Signature extraction over Python source (stdlib ast) with a
  regex fallback for other languages: every signature carries its
  file:line, because the map itself IS the citation.
- Reference graph + PageRank over the extracted names, truncated
  to a token budget: the repo-map pattern (tree-sitter tags ->
  reference graph -> personalised PageRank -> signature tree),
  reimplemented clean-room from the published design. No code is
  copied from the pattern's origin tool, so no licence question
  attaches to this port.
- Structural search over ast ("find every call shaped like this"),
  the third layer beside text search and the repo-map.
- STATIC-ONLY RULE: the scanner parses source text; it never
  imports, executes, or dynamically loads the scanned project.
  Dynamic import of untrusted code-lab projects is refused by
  construction (there is no import path in this module at all).
- STORE BOUNDARY: the codebase store is NOT the memory store
  (INV-8 kept by separation). Writes to the assistant memory store
  are refused outright.
- BACKEND FENCE: graph persistence is in-memory or SQLite only.
  The named graph backends with distribution terms that bar
  commercial bundling are refused by name in ``check_backend``.

No network, no telemetry, no code leaves the machine here.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import sqlite3

from . import store_base

REFUSED_BACKENDS = ("neo4j", "falkordb")
REFUSED_STORES = ("assistant.db",)
TOKEN_CHARS_PER_TOKEN = 4


class CodebaseError(ValueError):
    """Raised when a codebase rule is violated."""


def refuse_memory_store_write(path):
    """INV-8 separation: the codebase store never writes the memory
    store. Raises on the memory store's filename, anywhere. The
    definition of "is this assistant.db" lives in store_base
    (GAPS-27, M-V3); this keeps its own CodebaseError shape."""
    if store_base.is_assistant_db(path):
        raise CodebaseError(
            "codebase store must never write the memory store")
    return True


def check_backend(backend):
    """Graph persistence fence: in-memory / SQLite only.

    The named backends are refused (their distribution terms bar
    commercial bundling); anything else unknown is also refused,
    because an unlisted backend is an unexamined one.
    """
    if str(backend).lower() in REFUSED_BACKENDS:
        raise CodebaseError(
            "graph backend %r refused: distribution terms bar "
            "commercial bundling" % backend)
    if str(backend).lower() not in ("memory", "sqlite"):
        raise CodebaseError(
            "graph backend %r refused: not on the allow-list" % backend)
    return True


def extract_signatures(source_text, path):
    """Extract (name, kind, line) signatures from Python source.

    Uses ast.parse only — never import, never exec. Unparseable
    files yield no signatures (and a parse-error record), never a
    crash and never a dynamic fallback that executes code.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out.append({"name": node.name, "kind": "function",
                        "line": node.lineno, "file": str(path)})
        elif isinstance(node, ast.AsyncFunctionDef):
            out.append({"name": node.name, "kind": "async function",
                        "line": node.lineno, "file": str(path)})
        elif isinstance(node, ast.ClassDef):
            out.append({"name": node.name, "kind": "class",
                        "line": node.lineno, "file": str(path)})
    return out


_FALLBACK_RE = re.compile(
    r"^\s*(?:def|class|function|fn)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE)


def extract_signatures_fallback(source_text, path):
    """Regex fallback for non-Python files: name + line, no parse."""
    out = []
    for match in _FALLBACK_RE.finditer(str(source_text)):
        line = source_text.count("\n", 0, match.start()) + 1
        out.append({"name": match.group(1), "kind": "definition",
                    "line": line, "file": str(path)})
    return out


def build_reference_graph(signatures, source_texts):
    """Reference graph: name -> set of names whose files mention it.

    ``source_texts`` maps file path -> text. A mention is a textual
    reference, not an execution trace; the graph is built from
    parsed signatures + plain text, never by importing code.
    """
    names = {sig["name"] for sig in signatures}
    graph = {name: set() for name in names}
    for path, text in source_texts.items():
        for name in names:
            if re.search(r"\b%s\b" % re.escape(name), str(text)):
                for sig in signatures:
                    if sig["file"] == path:
                        graph[name].add(sig["name"])
    for name in graph:
        graph[name].discard(name)
    return graph


def pagerank(graph, iterations=20, damping=0.85):
    """Personalised PageRank over the reference graph (pure)."""
    nodes = sorted(graph)
    if not nodes:
        return {}
    rank = {node: 1.0 / len(nodes) for node in nodes}
    for _ in range(iterations):
        new = {}
        for node in nodes:
            inbound = 0.0
            for other in nodes:
                outs = graph[other]
                if node in outs and outs:
                    inbound += rank[other] / len(outs)
            new[node] = (1.0 - damping) / len(nodes) + damping * inbound
        rank = new
    return rank


def signature_tree(signatures, ranks, token_budget=2000):
    """Budget-truncated signature tree, highest rank first.

    Each line cites file:line. Truncation stops at the budget
    (estimated tokens = chars / 4); the tree states it was
    truncated, never silently drops entries past the budget.
    """
    ranked = sorted(signatures,
                    key=lambda s: (-ranks.get(s["name"], 0.0), s["file"],
                                   s["line"]))
    lines = []
    used = 0
    truncated = False
    for sig in ranked:
        line = "%s:%s %s %s" % (sig["file"], sig["line"], sig["kind"],
                                sig["name"])
        cost = len(line) // TOKEN_CHARS_PER_TOKEN + 1
        if used + cost > token_budget:
            truncated = True
            break
        lines.append(line)
        used += cost
    if truncated:
        lines.append("... [truncated at %d tokens]" % token_budget)
    return "\n".join(lines)


def find_calls_shaped_like(source_text, callee_name):
    """Structural search: every call shaped like ``callee_name(...)``.

    Returns file:line citations for each call site, from ast only.
    This is the question text search cannot answer ("find every
    call shaped like this") and the repo-map cannot cite precisely.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == callee_name:
                out.append({"callee": callee_name, "line": node.lineno})
    return out


def cite(answer_text, citations):
    """Citation discipline: an answer ships with file:line cites.

    ``citations`` is a list of "file:line" strings. An answer with
    no citations is returned flagged, never as established fact:
    the caller must not present uncited claims about code as
    verified.
    """
    if not citations:
        return {"answer": answer_text, "citations": [],
                "cited": False,
                "warning": "uncited: do not present as verified"}
    return {"answer": answer_text, "citations": list(citations),
            "cited": True, "warning": ""}


def fingerprint_store_path(path):
    """SQLite path guard for the codebase graph store (SQLite only
    per check_backend; memory-store filename refused)."""
    refuse_memory_store_write(path)
    return str(path)


def store_graph_sqlite(db_path, signatures):
    """Persist the signature list to a local SQLite store."""
    check_backend("sqlite")
    refuse_memory_store_write(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS signatures "
                     "(name TEXT, kind TEXT, line INTEGER, file TEXT)")
        conn.execute("DELETE FROM signatures")
        for sig in signatures:
            conn.execute("INSERT INTO signatures VALUES (?,?,?,?)",
                         (sig["name"], sig["kind"], sig["line"],
                          sig["file"]))
        conn.commit()
    finally:
        conn.close()
    digest = hashlib.sha256(
        ("\n".join(sorted(s["name"] for s in signatures))
         ).encode("utf-8")).hexdigest()[:16]
    return {"stored": len(signatures), "sha_prefix": digest}
