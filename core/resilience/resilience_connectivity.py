"""RL-2.x connectivity truth stack (Resilience track, wave 25, plan §5.18).

Native re-implementation; PyBreaker, stamina, pyresilience and
httpx-retries are NOT depended on (their licence checks stay owed only
if ever taken). This module adds NO retry layer: every probe is a
single attempt, the ladder only gates, and the breaker only queues.
One retry layer per call is a track rule — see :func:`audit_retry_layers`,
which reports the tree's existing layers and any stacking. The one
genuine call-retry layer in the tree at this writing is
``reflection.reflect_and_retry`` (exactly one bounded retry,
counter-capped); the audit must keep reporting it, and any second
layer wrapping the same call path would be a defect, not a feature.

The ladder CONSUMES v5.53's shipped browser-lifetime rules and RS-1's
watchdog — it does not replace them. RL-2.8 Happy Eyeballs is a pattern
note only (explicit DNS + racing connects, documented in the module
docstring below; the aiohappyeyeballs verbatim read is still owed, so
no code ships for it). RL-2.9 Socket.IO is pattern only: she speaks no
Socket.IO, so there is nothing to build.
"""

from __future__ import annotations

import ast
import builtins
import os
import time

PROBE_VERDICTS = ("ok", "portal", "weak", "offline")

# RL-2.8 pattern note (no code): Happy Eyeballs = resolve all address
# families explicitly, race TCP connects across them, take the first
# success, cancel the rest. No aiohappyeyeballs code ships here; its
# verbatim licence read (PSF-2.0 claimed, NOASSERTION) stays owed.
HAPPY_EYEBALLS_PATTERN = (
    "Resolve A + AAAA explicitly, race one TCP connect per address, "
    "take the first success, cancel the rest. Single attempt per "
    "address; no retry loop.")


def probe_truth(results) -> str:
    """NCSI-style verdict from several probe verdicts.

    Rules, in order (never a single-probe claim):
    - no probes at all -> "offline" (no evidence is not online);
    - any verdict string outside the known set -> "offline" (unknown
      evidence poisons the verdict; it is never counted as "ok");
    - any "portal" -> "portal" (a captive portal is named as portal);
    - every probe "ok" -> "online";
    - any "ok" or any "weak" -> "weak" (degraded evidence);
    - otherwise (all "offline") -> "offline".
    """
    if not results:
        return "offline"
    if any(r not in PROBE_VERDICTS for r in results):
        return "offline"
    if "portal" in results:
        return "portal"
    if all(v == "ok" for v in results):
        return "online"
    if "ok" in results or "weak" in results:
        return "weak"
    return "offline"


def multi_probe(probers) -> dict:
    """Run each prober callable ONCE and return the agreed verdict.

    A prober returns one of "ok" / "portal" / "weak" / "offline"; an
    exception counts as "offline" evidence for that prober. There is no
    retry here: a failed probe is evidence, not an invitation to
    re-attempt. Returns {"verdict", "results"} in prober order.
    """
    results = []
    for prober in probers:
        try:
            verdict = prober()
        except Exception:  # noqa: BLE001 — a failed probe is evidence
            verdict = "offline"
        results.append(verdict if verdict in PROBE_VERDICTS else "offline")
    return {"verdict": probe_truth(results), "results": results}


class Ladder:
    """RL-2.2 ladder state machine with hysteresis.

    Rungs from best to worst: online -> degraded -> lite -> offline.
    Demotion is INSTANT on breaker-open (one rung, immediately, streak
    reset). Promotion needs ``promote_after`` consecutive successes
    (default 3) and moves one rung. A plain failure without a
    breaker-open only resets the streak — the breaker is the failure
    detector, so demotion always travels through it.
    """

    RUNGS = ("online", "degraded", "lite", "offline")

    def __init__(self, promote_after: int = 3, start: str = "online"):
        if start not in self.RUNGS:
            raise ValueError("unknown rung: %s" % start)
        self.promote_after = max(1, promote_after)
        self._index = self.RUNGS.index(start)
        self._streak = 0

    @property
    def rung(self) -> str:
        return self.RUNGS[self._index]

    @property
    def streak(self) -> int:
        return self._streak

    def observe(self, success: bool, breaker_open: bool = False) -> dict:
        """Feed one observation. Returns {rung, promoted, demoted}."""
        promoted = demoted = False
        if breaker_open:
            if self._index < len(self.RUNGS) - 1:
                self._index += 1
                demoted = True
            self._streak = 0
        elif success:
            self._streak += 1
            if self._streak >= self.promote_after and self._index > 0:
                self._index -= 1
                self._streak = 0
                promoted = True
        else:
            self._streak = 0
        return {"rung": self.rung, "promoted": promoted,
                "demoted": demoted, "streak": self._streak}


class Breaker:
    """RL-2.3 circuit breaker: closed / open / half-open, per instance.

    Instances are independent: one Breaker per brain (see
    :func:`breakers_for`). ``allow()`` gates a call; ``record_success``
    / ``record_failure`` move the state. Open means QUEUE for the
    outbox (``resilience_outbox.deliver_due``), never drop. Half-open
    grants exactly one trial: ``allow()`` consumes the trial until a
    record resolves it. No retries live here — the breaker only gates.
    """

    def __init__(self, failure_threshold: int = 5,
                 reset_after_s: float = 60.0, clock=None):
        self.failure_threshold = max(1, failure_threshold)
        self.reset_after_s = float(reset_after_s)
        self._clock = clock or time.monotonic
        self.state = "closed"
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_available = False

    def _maybe_half_open(self) -> None:
        if (self.state == "open" and self._opened_at is not None
                and self._clock() - self._opened_at >= self.reset_after_s):
            self.state = "half-open"
            self._trial_available = True

    def allow(self) -> bool:
        self._maybe_half_open()
        if self.state == "closed":
            return True
        if self.state == "half-open" and self._trial_available:
            self._trial_available = False
            return True
        return False

    def is_open(self) -> bool:
        """True only while the breaker refuses calls (open, window live)."""
        self._maybe_half_open()
        return self.state == "open"

    def record_success(self) -> str:
        if self.state in ("half-open", "open"):
            self.state = "closed"
        self._failures = 0
        self._opened_at = None
        self._trial_available = False
        return self.state

    def record_failure(self) -> str:
        if self.state == "half-open":
            self.state = "open"
            self._opened_at = self._clock()
            self._trial_available = False
            return self.state
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = self._clock()
        return self.state


def breakers_for(brain_names, **kwargs) -> dict:
    """One independent Breaker per brain name."""
    return {name: Breaker(**kwargs) for name in brain_names}


class LocalTierProbe:
    """RL-7.7 local-tier probe + demotion rule.

    The probe is independent of process liveness: a trivial local call
    with a timeout plus fault-signal inputs (a dead local tier is a
    tier fault, not a process crash). On a fault streak reaching
    ``demote_after``, the tier demotes to "lite" AUTOMATICALLY — the
    dead local tier never blocks the cloud/lite chain; the caller
    routes around it. A healthy probe promotes back to "local".
    ``timeout_s`` bounds a real probe call; the fake probes in tests
    return immediately, so it is honoured by contract, not enforced
    here (enforcing it would need a thread per probe — out of scope
    for a library rule, and a thread-per-probe is not smuggled in).
    """

    def __init__(self, probe, demote_after: int = 2,
                 timeout_s: float = 5.0):
        self._probe = probe
        self.demote_after = max(1, demote_after)
        self.timeout_s = timeout_s
        self.tier = "local"
        self.fail_streak = 0

    def probe(self) -> str:
        """One probe. Returns the tier to use: "local" or "lite"."""
        try:
            result = self._probe()
            ok = result.get("ok") if isinstance(result, dict) else bool(
                result)
        except Exception:  # noqa: BLE001 — a probe fault is a fault
            ok = False
        if ok:
            self.fail_streak = 0
            self.tier = "local"
        else:
            self.fail_streak += 1
            if self.fail_streak >= self.demote_after:
                self.tier = "lite"
        return self.tier


# ---------------------------------------------------------------------
# One-retry-layer audit (RL-2.6 under RL-2.4: stacking is a defect)
# ---------------------------------------------------------------------

_RETRY_LIBRARY_IMPORTS = {"tenacity", "backoff", "stamina", "pybreaker"}


def _ast_retry_report(source: str) -> tuple:
    """Return (layer_defs, layer_calls, def_calls) for one source file.

    AST-based, so string literals never count (a vocabulary word like
    "tenacity" in prose is not a retry layer) and poll loops, scroll
    loops and game-attempt loops do not match (they are not retry
    calls). A layer DEFINITION is:
    - a function decorated with a retry decorator, or
    - a function whose own name contains "retry" AND which re-invokes
      the same bare-name, non-builtin callable at two DISTINCT source
      lines (the re-invocation that makes it a retry — this is exactly
      ``reflect_and_retry``'s shape: ``executor(step)`` then
      ``executor(retry_step)``). Distinct lines matter: one call site
      inside a loop is one attempt per iteration, not a retry; and
      builtins (isinstance, len, sorted) plus attribute calls (ast
      traversal, list appends in two branches) are not retried actions,
      so they are excluded. This deliberately does NOT detect
      attribute-call retries (``self.attempt()`` twice); the tree's
      known layer uses a bare-name executor, which is caught.
    - a retry-library import (tenacity / backoff / stamina / pybreaker).
    A layer CALL is any call to a function whose name contains "retry"
    (e.g. a call site of ``reflect_and_retry``). Stacks are computed
    from defs, not calls: a def that calls another layer def.
    """
    tree = ast.parse(source)
    decorated = set()
    layer_calls = []
    def_calls = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in _RETRY_LIBRARY_IMPORTS:
                    layer_calls.append({"kind": "retry-library-import",
                                        "name": name, "line": node.lineno})
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                text = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                if "retry" in text.lower():
                    decorated.add(node.name)
        if isinstance(node, ast.Call):
            func = node.func
            called = ""
            if isinstance(func, ast.Name):
                called = func.id
            elif isinstance(func, ast.Attribute):
                called = func.attr
            if "retry" in called.lower():
                layer_calls.append({"kind": "retry-call", "name": called,
                                    "line": node.lineno})
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Bare-name, non-builtin calls with their distinct lines.
            sites = {}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func,
                                                           ast.Name):
                    name = sub.func.id
                    # Builtins are not retried actions. (dir(builtins),
                    # not dir(__builtins__): the latter is loader-
                    # dependent and silently missed builtins here.)
                    if name in dir(builtins):
                        continue
                    sites.setdefault(name, set()).add(sub.lineno)
            called_names = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    if isinstance(func, ast.Name):
                        called_names.append(func.id)
                    elif isinstance(func, ast.Attribute):
                        called_names.append(func.attr)
            def_calls[node.name] = set(called_names)
            if ("retry" in node.name.lower()
                    and any(len(lines) >= 2 for lines in sites.values())):
                decorated.add(node.name)
    layer_defs = set(decorated)
    return layer_defs, layer_calls, def_calls


def audit_retry_layers(source_roots) -> dict:
    """Audit source trees for retry layers and stacking.

    Returns {"files_scanned", "layers", "stacks"}. A layer is a
    function definition that performs a retry (retry decorator, a
    retry-named function re-invoking the same bare-name callable at two
    distinct lines, or a retry-library import). A STACK is a layer
    definition that calls another layer definition — two retry layers
    on one call path, which the track treats as a defect. The audit
    reports; it never fixes. New Resilience modules must contribute
    zero layer defs and zero stacks (their own audit plumbing is meta,
    not a retry layer).
    """
    layers = []
    stacks = []
    scanned = 0
    for root in source_roots:
        for directory, _dirs, files in os.walk(root):
            if "__pycache__" in directory:
                continue
            for filename in sorted(files):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                try:
                    with open(path, encoding="utf-8") as handle:
                        source = handle.read()
                except (OSError, UnicodeDecodeError):
                    continue
                scanned += 1
                try:
                    layer_defs, layer_calls, def_calls = _ast_retry_report(
                        source)
                except SyntaxError:
                    continue
                for entry in layer_calls:
                    layers.append({"file": path, **entry})
                for name in sorted(layer_defs):
                    layers.append({"file": path, "kind": "retry-layer-def",
                                   "name": name, "line": 0})
                for caller, calls in def_calls.items():
                    for callee in sorted(calls):
                        if callee in layer_defs and callee != caller:
                            stacks.append({"file": path, "caller": caller,
                                           "callee": callee})
    return {"files_scanned": scanned, "layers": layers, "stacks": stacks}
