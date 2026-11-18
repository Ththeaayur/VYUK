"""Wave 2 rigs: R3-F, R4-B, R4-D, R4-K, R4-A, R4-F (stdlib only).

  * R3-F tool-finder upgrade: deterministic ranking of candidate
    tools by exact token overlap with the query, after the R3-B
    baseline. No model, no invented scores: ties keep input order,
    misses are reported as misses, never padded with a guess.
  * R4-B drift canary: a canary fingerprint set (caller-supplied)
    compared against a current set; any difference is drift data.
  * R4-D failure taxonomy with bug-to-bench ratchet: a failure
    class counts as "fixed" only when a bench case id is linked
    to it; otherwise it stays open, however old.
  * R4-K bench mode: runs caller-supplied cases through a
    caller-supplied handler in dry-run form only -- the handler
    records, it must not take consequential action; this module
    never executes tools itself.
  * R4-A ask-or-act policy: default ASK for everything. A caller
    may supply thresholds, but consequential actions (send,
    delete, purchase, system change) ALWAYS ask under C-14,
    regardless of any supplied threshold. Governed numbers stay
    unset unless the caller supplies them.
  * R4-F taint by provenance: taint follows the SOURCE of data
    (trusted / untrusted / unknown), never a pattern match on
    content. Untrusted or unknown provenance taints the session
    for consequential actions until a gate clears it.
"""

from __future__ import annotations

import hashlib
import re


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def find_tools(query: str, candidates: list) -> dict:
    """Rank candidate tools (dicts with 'name' and 'description')."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return {"query": query, "ranked": [], "miss": True,
                "reason": "empty query"}
    scored = []
    for cand in candidates:
        if not isinstance(cand, dict) or not cand.get("name"):
            continue
        hay = _tokens(cand.get("name", "")) | _tokens(cand.get("description", ""))
        overlap = len(query_tokens & hay)
        scored.append((overlap, cand["name"], cand))
    scored.sort(key=lambda item: (-item[0], candidates.index(item[2])
                                  if item[2] in candidates else 0))
    ranked = [c for overlap, _, c in scored if overlap > 0]
    return {"query": query, "ranked": ranked,
            "miss": not ranked,
            "reason": "" if ranked else "no candidate shares a token with the query"}


def drift_canary(baseline: dict, current: dict) -> dict:
    """Compare caller-supplied canary fingerprint sets."""
    changed = sorted(k for k in set(baseline) | set(current)
                     if baseline.get(k) != current.get(k))
    return {"drifted": bool(changed), "changed_keys": changed,
            "note": "drift data only; no verdict on cause"}


class FailureTaxonomy:
    """R4-D: failure classes; fixed only with a linked bench case."""

    def __init__(self):
        self._classes: dict[str, dict] = {}

    def record(self, failure_class: str, detail: str = "") -> None:
        entry = self._classes.setdefault(
            failure_class, {"count": 0, "bench_case": None, "details": []})
        entry["count"] += 1
        if detail:
            entry["details"].append(detail)

    def link_bench(self, failure_class: str, bench_case_id: str) -> bool:
        if failure_class not in self._classes or not bench_case_id:
            return False
        self._classes[failure_class]["bench_case"] = bench_case_id
        return True

    def is_fixed(self, failure_class: str) -> bool:
        entry = self._classes.get(failure_class)
        return bool(entry and entry["bench_case"])

    def open_classes(self) -> list:
        return sorted(k for k, v in self._classes.items()
                      if not v["bench_case"])


def bench_mode(cases: list, handler) -> dict:
    """R4-K: dry-run cases through a caller handler; records only."""
    results = []
    for case in cases:
        try:
            outcome = handler(case)
        except Exception as exc:  # noqa: BLE001 -- bench records failures
            outcome = {"error": f"{type(exc).__name__}: {exc}"}
        results.append({"case": case, "outcome": outcome})
    return {"cases": len(cases), "results": results,
            "note": "bench mode: no consequential action taken by this module"}


CONSEQUENTIAL = ("send", "delete", "purchase", "system_change",
                 "message", "booking", "payment")


def ask_or_act(action: str, *, caller_thresholds: dict | None = None) -> dict:
    """R4-A: ask by default; consequential actions always ask (C-14)."""
    if action in CONSEQUENTIAL:
        return {"action": action, "decision": "ask",
                "reason": "consequential action: C-14 approve-first, always"}
    if caller_thresholds and caller_thresholds.get(action) == "act":
        return {"action": action, "decision": "act",
                "reason": "caller-supplied threshold permits acting"}
    return {"action": action, "decision": "ask",
            "reason": "no caller threshold supplied; default is ask "
                      "(R4-J governed numbers unset)"}


def taint_by_provenance(provenance: str) -> dict:
    """R4-F: taint from source provenance, never from content patterns."""
    if provenance == "trusted":
        return {"tainted": False, "provenance": provenance}
    if provenance in ("untrusted", "unknown"):
        return {"tainted": True, "provenance": provenance,
                "reason": f"provenance '{provenance}' taints the session "
                          f"for consequential actions"}
    return {"tainted": True, "provenance": "unknown",
            "reason": "unrecognised provenance treated as unknown (fail-closed)"}


def fingerprint_text(text: str) -> str:
    """Fingerprint-only helper (content is never stored, only the hash)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()
