"""Wave 3 Resilience wiring (3a) — the 7 v5.75 libraries wired to real
triggers, under the proven fences. Stdlib only, no GUI, no network,
no new collection of any kind (telemetry OFF is not touched here).

Libraries wired, by their v5.75 module:
  resilience_state    crash-consistent state / SQLite audit
  resilience_outbox   durable outbox + Fence-1 delivery
  resilience_power    crash counter, safe mode, checkpoints
  resilience_connectivity  connectivity truth ladder + breaker
  resilience_clock    dual-clock sleep-vs-crash classifier
  resilience_offline  offline knowledge pack (FTS5 floor)
  resilience_durability  exactly-once effect ledger + saga spec store

Fences, enforced here, not just documented:
  * Tripped failsafe means ZERO dispatches, including mid-replay:
    delivery re-checks the trip predicate before every dispatch,
    and a trip mid-replay stops the pass at once.
  * NO executor engine: there is no workflow loop, no scheduler,
    no retry loop in this module. Delivery is one pass over due
    entries; failures requeue for a LATER pass by the caller.
  * NO auto-resume without human re-arm: a trip latches until
    :meth:`ResilienceWiring.rearm` is called explicitly by a human
    (a Settings/tray action in the GUI). Beats, time passing, or a
    recovered hang never un-trip on their own.
  * Durability lives OUTSIDE the executor: every store this module
    opens lives in its own file under the given base directory,
    never in ``assistant.db`` (the rl modules refuse that filename
    outright, and this module passes its own filenames).

Owed-read clearance (Wave 3, §6 item 6): Sweep Build 6 performed the
primary licence reads for five of these and wired three of them as
OPTIONAL imports only (see :func:`owed_read_clearance` and
``core/resilience/resilience_swaps.py``): DiskCache (TTL cache only, never a
checkpoint replacement), whenever (DST disambiguation only),
aiohappyeyeballs (async connect only). exactly-once and agent-saga
had their licences read and were NOT wired — they do not beat the
in-house ledger / saga SPEC, and no wired path imports them. APSW,
Litestream, check-restart, and pycaw remain as recorded below; the
in-house patterns (native sqlite3 stores, the saga SPEC registry)
are what the v5.75 libraries ship, and this wiring uses those
patterns wherever a package was not wired.

Kill drill: :meth:`ResilienceWiring.record_kill_drill` records a
drill result as DATA in a JSON ledger. A lab drill never counts as
field proof — the Wave 3 exit needs a kill drill on the owner's
machine, his run, his verdict. This module never claims that.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

RL_MODULES = (
    "resilience_state",
    "resilience_outbox",
    "resilience_power",
    "resilience_connectivity",
    "resilience_clock",
    "resilience_offline",
    "resilience_durability",
)

# name -> (what it would unlock, why it is not cleared, ship state)
OWED_READS = {
    "DiskCache": (
        "TTL cache via the DiskCache package (Sweep Build 6, optional)",
        "Licence read filed (Apache-2.0 short-form, sdist = wheel); "
        "wired as TTL cache only, never a checkpoint replacement",
        "wired_optional",
    ),
    "exactly-once": (
        "PyPI exactly-once effect ledger",
        "Licence read filed (MIT) but NOT wired: in-house ledger is "
        "fence-aware, package is broader, 0.2.x, unfenced",
        "not_wired",
    ),
    "agent-saga": (
        "agent-saga package for saga execution",
        "Licence read filed (Apache-2.0) but NOT wired: in-house is a "
        "SPEC + registry, deliberately not an engine; package carries "
        "a trademark mandate in its NOTICE",
        "not_wired",
    ),
    "whenever": (
        "whenever datetime library (Sweep Build 6, optional)",
        "Licence read filed (MIT, sdist = wheel); wired for DST "
        "disambiguation only, stdlib fallback refuses gap hours",
        "wired_optional",
    ),
    "aiohappyeyeballs": (
        "aiohappyeyeballs Happy Eyeballs code (Sweep Build 6, optional)",
        "Licence read filed (PSF-2.0, sdist = wheel); wired for async "
        "connect only, stdlib fallback races via open_connection",
        "wired_optional",
    ),
    "APSW": (
        "APSW SQLite wrapper",
        "licence check still owed; native sqlite3 patterns ship instead",
        "pilot-only",
    ),
    "Litestream": (
        "Litestream shadow-copy daemon",
        "Windows support status still owed; SQLite backup-API pattern ships instead",
        "pilot-only",
    ),
    "check-restart": (
        "check-restart / pycaw process-audio helpers",
        "licence / Windows-build reads still owed",
        "pilot-only",
    ),
    "pycaw": (
        "pycaw Windows audio session control",
        "Windows-only lock pin owed from the Windows build machine",
        "pilot-only",
    ),
}


def owed_read_clearance() -> dict:
    """The Wave 3 owed-read ledger, as data. Nothing here is cleared.

    Returns a dict of name -> {unlocks, reason, ship_state}.
    A caller that needs one of these must treat ship_state
    'parked' as do-not-build and 'pilot-only' as do-not-ship.
    """
    return {
        name: {"unlocks": unlocks, "reason": reason, "ship_state": state}
        for name, (unlocks, reason, state) in OWED_READS.items()
    }


def _load_rl(name: str):
    """Lazily load one rl_* module, relative first, then top-level."""
    try:
        import importlib

        return importlib.import_module("." + name, __package__)
    except Exception:  # noqa: BLE001 — fall back to top-level core import
        import importlib

        return importlib.import_module("core.resilience." + name)  # Phase 3 R1: moved to core/resilience/


class ResilienceWiring:
    """Wiring for the 7 Resilience libraries, with the fences live.

    ``base_dir`` is where this wiring's own stores live (the caller's
    data directory in the app, a temp directory in tests). It is
    never ``assistant.db`` and no store here writes to it.
    ``failsafe_tripped`` is an injected predicate (the live
    ``core.failsafe.tripped`` in the app, a fake in tests). The
    trip latch in this class is separate from the failsafe module's
    own latch: both must be clear before any dispatch happens.
    """

    def __init__(self, base_dir: str, failsafe_tripped=None):
        self.base_dir = str(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self._failsafe_tripped = failsafe_tripped or (lambda: False)
        self._tripped = False
        self._trip_reason = ""
        self._armed = True
        self._ttl_cache = None

    # -- trip / re-arm -------------------------------------------------

    def trip(self, reason: str) -> dict:
        """Latch a trip. Dispatches stop at once and stay stopped."""
        self._tripped = True
        self._trip_reason = str(reason)
        self._armed = False
        return {"tripped": True, "reason": self._trip_reason,
                "armed": False}

    def rearm(self, *, human: bool) -> dict:
        """Human re-arm ONLY. ``human=False`` (a timer, a beat, a
        recovered hang) never re-arms — that is the whole point."""
        if not human:
            return {"rearmed": False,
                    "reason": "re-arm requires an explicit human action"}
        self._tripped = False
        self._trip_reason = ""
        self._armed = True
        return {"rearmed": True, "armed": True}

    def is_tripped(self) -> bool:
        return bool(self._tripped or self._failsafe_tripped())

    # -- outbox delivery (Fence 1, mid-replay stop) --------------------

    def deliver_outbox(self, funnel, breaker=None) -> dict:
        """One delivery pass over due outbox entries.

        Returns the rl_outbox deliver_due verdict, with zero
        dispatches whenever either trip latch is set. A trip that
        fires mid-replay stops the pass at once (proved by
        rl_outbox itself, re-checked here before the pass starts).
        This is one pass, not a loop: failures requeue for later.
        """
        if self.is_tripped():
            return {"dispatched": 0, "acked": 0, "nacked": 0,
                    "reason": "failsafe-tripped"}
        outbox_mod = _load_rl("resilience_outbox")
        outbox = outbox_mod.Outbox(
            os.path.join(self.base_dir, outbox_mod.OUTBOX_FILENAME))
        return outbox_mod.deliver_due(
            outbox, funnel, self.is_tripped, breaker=breaker)

    def enqueue_outbox(self, payload: dict, **kwargs) -> int:
        outbox_mod = _load_rl("resilience_outbox")
        outbox = outbox_mod.Outbox(
            os.path.join(self.base_dir, outbox_mod.OUTBOX_FILENAME))
        return outbox.enqueue(payload, **kwargs)

    # -- crash / power / clock ----------------------------------------

    def classify_wake(self, last: dict, now: dict) -> dict:
        """Sleep vs crash, via the dual-clock classifier. Only a
        confident 'crash' verdict may feed crash-loop counting."""
        clock_mod = _load_rl("resilience_clock")
        return clock_mod.classify_dual_clock(last, now)

    def record_crash(self, *, verdict: str, now: float | None = None) -> dict:
        """Record a crash ONLY on a confident 'crash' verdict.
        'sleep' and 'unknown' never feed safe-mode counting."""
        if verdict != "crash":
            return {"recorded": False,
                    "reason": f"verdict '{verdict}' never feeds crash counting"}
        power_mod = _load_rl("resilience_power")
        counter = power_mod.CrashCounter(
            os.path.join(self.base_dir, power_mod.CRASH_COUNTER_FILENAME))
        count = counter.record_crash(now=now)
        return {"recorded": True, "recent_crashes": count,
                "safe_mode_due": counter.should_enter_safe_mode(now=now)}

    def checkpoint(self, flushers) -> list:
        power_mod = _load_rl("resilience_power")
        return power_mod.checkpoint_now(flushers)

    def safe_mode_status(self) -> dict:
        power_mod = _load_rl("resilience_power")
        return {
            "engaged": power_mod.is_safe_mode_engaged(self.base_dir),
            "note": "flag-file presence enables safe mode; "
                    "engagement itself is a human/flag action, "
                    "never an auto-restart from this module",
        }

    # -- connectivity / offline ---------------------------------------

    def connectivity(self, probe_results) -> dict:
        """One truth verdict from supplied probe results (single
        attempt per probe by the rl ladder; no retry layer here)."""
        connect_mod = _load_rl("resilience_connectivity")
        return {"verdict": connect_mod.probe_truth(probe_results),
                "note": "single-attempt probes only; no retry layer added"}

    def offline_serve(self, doc_id: str) -> dict | None:
        """Serve a cached knowledge-pack answer, with its cached-at
        label intact. A served answer is never mistakable for a
        fresh fact — the label travels with it."""
        offline_mod = _load_rl("resilience_offline")
        pack = offline_mod.KnowledgePack(
            os.path.join(self.base_dir, offline_mod.FTS5_FILENAME))
        return pack.serve(doc_id)

    # -- durability / crash-consistency -------------------------------

    def sqlite_audit(self, version_text: str | None = None) -> dict:
        state_mod = _load_rl("resilience_state")
        return state_mod.audit_sqlite_version(version_text)

    def effect_once(self, key: str, outcome: dict) -> dict:
        """Exactly-once by key, via the in-house ledger (never the
        PyPI package). A replayed key returns the ORIGINAL outcome
        and records nothing new, so a replay can never double-act."""
        durable_mod = _load_rl("resilience_durability")
        ledger = durable_mod.EffectLedger(
            os.path.join(self.base_dir, durable_mod.EFFECT_LEDGER_FILENAME))
        return ledger.record_effect(key, outcome)

    def replay_fenced(self, entries, funnel) -> dict:
        """Replay steps through the rl_durable fence: every step
        passes the failsafe predicate, and a trip mid-replay stops
        it. This is a replay pass, not an engine."""
        if self.is_tripped():
            return {"dispatched": 0, "reason": "failsafe-tripped"}
        durable_mod = _load_rl("resilience_durability")
        return durable_mod.replay(entries, funnel, self.is_tripped)

    # -- Sweep Build 6 optional swaps (never a second engine) --------

    def swap_status(self) -> dict:
        """The Build 6 verdict table, via the swaps module."""
        from . import resilience_swaps as _swaps
        return _swaps.swap_status()

    def _ttl_cache_obj(self):
        """One cache instance per wiring object: the in-memory
        fallback must persist across set/get calls, and a diskcache
        Cache must not be reopened per call."""
        if self._ttl_cache is None:
            from . import resilience_swaps as _swaps
            self._ttl_cache = _swaps.TTLCache(
                os.path.join(self.base_dir, "rl_ttl_cache"))
        return self._ttl_cache

    def cache_set(self, key: str, value, expire_s=None) -> dict:
        """Store a JSON value in the optional TTL cache (diskcache
        when installed, in-memory otherwise). Never a checkpoint:
        entries here may expire, checkpoints may not."""
        cache = self._ttl_cache_obj()
        cache.set(key, value, expire_s=expire_s)
        return {"stored": True, "backend": cache.backend}

    def cache_get(self, key: str) -> dict:
        cache = self._ttl_cache_obj()
        value = cache.get(key)
        return {"value": value, "backend": cache.backend, "found": value is not None}

    def resolve_run_at(self, year: int, month: int, day: int, hour: int = 0,
                       minute: int = 0, *, tz: str,
                       disambiguation: str = "raise") -> dict:
        """Resolve a local "run at" time via the Build 6 DST helper."""
        from . import resilience_swaps as _swaps
        return _swaps.resolve_local_time(year, month, day, hour, minute,
                                         tz=tz, disambiguation=disambiguation)

    async def happy_eyeballs_connect(self, host: str, port: int, *,
                                     stagger_delay: float = 0.25) -> dict:
        """Race TCP connects via the Build 6 helper. The caller owns
        and must close any returned socket."""
        from . import resilience_swaps as _swaps
        return await _swaps.happy_eyeballs_connect(
            host, port, stagger_delay=stagger_delay)

    # -- status payload (the GUI surface) ------------------------------

    def status(self) -> dict:
        """The full wiring status, as data, for GUI rendering.

        No side effects: this opens no store, dispatches nothing,
        and collects nothing. The GUI renders this payload; it does
        not re-derive it from live internals."""
        return {
            "libraries": list(RL_MODULES),
            "tripped": self.is_tripped(),
            "trip_reason": self._trip_reason,
            "armed": self._armed and not self.is_tripped(),
            "safe_mode": self.safe_mode_status()["engaged"],
            "owed_reads": {name: info["ship_state"]
                           for name, info in owed_read_clearance().items()},
            "fences": [
                "tripped failsafe = zero dispatches, including mid-replay",
                "no executor engine, no scheduler, no retry loop here",
                "no auto-resume without explicit human re-arm",
                "durability outside the executor, never in assistant.db",
            ],
            # Fix round (F-04, parent wording 2026-11-26): plain
            # words for the same owed drill.
            "field_proof": "Still owed on your machine: a kill drill "
                           "— your run, your verdict",
        }

    def record_kill_drill(self, *, passed: bool, steps: list,
                          run_by: str = "lab") -> dict:
        """Record a kill-drill result as DATA. A lab drill is
        recorded as a lab drill; it never upgrades itself to
        field proof. ``run_by`` must say who actually ran it."""
        path = os.path.join(self.base_dir, "rl_w3_kill_drill.json")
        entry = {
            "passed": bool(passed), "steps": list(steps),
            "run_by": run_by, "at": time.time(),
            "counts_as_field_proof": run_by == "owner",
        }
        try:
            with open(path, encoding="utf-8") as handle:
                ledger = json.load(handle)
            if not isinstance(ledger, list):
                ledger = []
        except (OSError, ValueError):
            ledger = []
        ledger.append(entry)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle)
        return entry
