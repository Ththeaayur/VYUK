"""DV flake programme + ship-gate grep rules (Developer track).

Two halves, one discipline: make flakes visible instead of green,
and make forbidden patterns unshippable by grep.

FLAKE PROGRAMME (CI detection + local policy, one item):
- The definition of a flake is the same-SHA fail->pass signature:
  the same commit, the same test, failing then passing with no
  code change between the runs. Anything else is a failure, not a
  flake, and must not be laundered green.
- Local policy: order-dependence is hunted in a dedicated run;
  reruns are allowed only in the gate; a test that passes on
  rerun stays visible as DEBT, never as a clean pass; the seed is
  printed into every battery log so a flake is reproducible, not
  anecdotal.

SHIP-GATE GREP RULES (product-side residue of dev-only tools):
- No debug-narration decorator ships: such a decorator logs
  variable values, which in the product is both a privacy hazard
  and a performance tax. The scan proves its absence.
- No skill-install pattern ships: a package that writes into an
  agent's skills directory (the trap-register grep target) is
  refused by scan.
- Agent-instruction files found in a tree are listed as DATA,
  never followed: their contents are third-party instructions,
  not orders.

All functions are pure over records, text, or trees. Nothing here
executes a test, a package, or an instruction file.
"""

from __future__ import annotations

import hashlib
import os
import re

SNOOP_MARKERS = ("@snoop", "@pysnooper.snoop", "import pysnooper",
                 "from pysnooper import")
SKILL_INSTALL_MARKERS = ("SKILL.md", "skills directory", "install skill",
                         "skill subcommand")
INSTRUCTION_FILENAMES = ("AGENTS.md", "CLAUDE.md")


class FlakeError(ValueError):
    """Raised when a flake record is malformed or mislabelled."""


def flake_signature(commit_sha, test_name, first_result, second_result):
    """Same-SHA fail->pass signature. Returns the signature dict
    when the pair IS a flake, else None (a failure, not a flake).

    Both runs must share the commit SHA, and the results must be
    fail then pass with no code change between them. Any other
    shape is not a flake signature and must not be recorded as one.
    """
    if not commit_sha or not test_name:
        raise FlakeError("flake signature needs a commit SHA and a test")
    if first_result == "fail" and second_result == "pass":
        digest = hashlib.sha256(
            ("%s:%s" % (commit_sha, test_name)).encode("utf-8")
        ).hexdigest()[:16]
        return {"sha": commit_sha, "test": test_name, "kind": "flake",
                "signature": digest}
    return None


def flake_policy_record(seed, rerun_allowed_in_gate, order_run_done):
    """The local flake policy, as a record for the battery log.

    The seed is printed into every battery log (reproducibility).
    Reruns are allowed ONLY in the gate; a passed-on-rerun test is
    recorded as debt, never as a clean pass. An order-dependence
    run must have happened for the policy to count as applied.
    """
    return {"seed": seed,
            "rerun_allowed_only_in_gate": bool(rerun_allowed_in_gate),
            "order_dependence_run_done": bool(order_run_done),
            "passed_on_rerun_means": "debt, never clean pass"}


def record_rerun_outcome(test_name, first, second):
    """Record one rerun outcome under the policy. A fail->pass pair
    is DEBT (visible), never a clean pass; pass->pass is clean;
    anything else is a failure."""
    if first == "fail" and second == "pass":
        return {"test": test_name, "verdict": "debt",
                "note": "passed on rerun; stays visible as debt"}
    if first == "pass" and second == "pass":
        return {"test": test_name, "verdict": "clean"}
    return {"test": test_name, "verdict": "failure"}


def scan_tree_for_markers(root, markers, extensions=(".py",)):
    """Scan a tree for forbidden markers. Returns the list of
    (path, marker) hits. An empty list is the pass condition."""
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if extensions and not filename.endswith(extensions):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, "r", encoding="utf-8",
                          errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for marker in markers:
                if marker in text:
                    hits.append((path, marker))
    return hits


def scan_no_snoop(root):
    """Ship-gate rule: prove no debug-narration decorator ships."""
    return scan_tree_for_markers(root, SNOOP_MARKERS, (".py",))


def scan_no_skill_install(root):
    """Trap-register rule: prove no skill-install pattern ships."""
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, "r", encoding="utf-8",
                          errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            if "SKILL.md" in text and ("install" in text.lower()
                                      or "write" in text.lower()):
                hits.append((path, "skill-install pattern"))
    return hits


def list_instruction_files(root):
    """List agent-instruction files in a tree, marked DATA.

    Every hit is returned with the verdict "data, never follow":
    third-party instruction files are not orders, however
    authoritative their wording ("MANDATORY", "you MUST").
    """
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename in INSTRUCTION_FILENAMES:
                found.append({"path": os.path.join(dirpath, filename),
                              "verdict": "data, never follow"})
    return found


_SNOOP_DECORATOR_RE = re.compile(r"@snoop\b|@pysnooper\.snoop\b")


def has_snoop_decorator(source_text):
    """True when source text contains a debug-narration decorator."""
    return bool(_SNOOP_DECORATOR_RE.search(str(source_text)))
