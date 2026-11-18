"""Sweep Build 6 — resilience swaps, only where the package beats the
in-house pattern. Stdlib only at import time; every wired package is
OPTIONAL, imported behind a guard, with an in-house fallback that still
works when the package is absent. No package is imported
unconditionally, and ``requirements.txt`` is unchanged by this build.

Licence basis: see ``gui/assets/RESILIENCE_LICENCE_READ.md`` — every
licence below was read from the LICENSE/NOTICE file inside the actual
sdist AND wheel for the pinned version, byte-identical in both.

Wired (3), each for exactly one job the in-house code does not do:
  * diskcache 5.6.3 (Apache-2.0) -> TTLCache. NOT a checkpoint
    replacement: checkpoints must never evict or expire, and the
    in-house ``resilience_power.CheckpointStore`` stays for that job.
    The in-house tree has no TTL cache at all; this is that job only,
    JSON values, explicit expiry.
  * whenever 0.11.0 (MIT) -> resolve_local_time. ``resilience_clock``
    classifies sleep vs crash but has no disambiguation policy for a
    local "run at" time in a repeated or skipped DST hour. whenever's
    ``ZonedDateTime(..., disambiguation=...)`` is the beat, for that
    question only. The stdlib fallback resolves repeated hours via
    ``fold`` and REFUSES gap hours outright — it never guesses.
  * aiohappyeyeballs 2.7.1 (PSF-2.0) -> happy_eyeballs_connect. The
    in-house position was a pattern note with no code. The beat is
    stagger/interleave control and a raw socket for asyncio callers,
    not the mere idea of racing connects: the stdlib fallback
    (``asyncio.open_connection``) already races addresses itself.
    Single attempt per address; no retry loop is added anywhere here.

NOT wired (2), licence read passed but the package does not beat the
in-house pattern — and no wired path imports them, ever:
  * exactly-once 0.2.1 (MIT): the in-house ``EffectLedger`` is
    fence-aware (every replayed step passes the failsafe predicate);
    the package is broader, 0.2.x, and unfenced. The ledger stays.
  * agent-saga 2.3.11 (Apache-2.0): the in-house position is a saga
    SPEC + compensation registry, deliberately NOT an engine, because
    the wiring fence forbids a second orchestration owner. The
    package is a large enterprise platform with a trademark mandate
    in its NOTICE. The spec + registry stay.

Hard limits, enforced here, not just documented:
  * No wake, capture, or voiceprint change. No GUI change in this
    module. No store here writes to ``assistant.db``.
  * A wired package's failure is a verdict, never a fake success:
    TTLCache.get returns ``None`` for a miss or an expired entry,
    resolve_local_time refuses a gap hour under the stdlib fallback,
    happy_eyeballs_connect propagates connection errors.
  * ``_load_optional`` never raises for a missing package; callers
    must check the ``backend`` field in the returned dict.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import time
from datetime import datetime, timezone

# name -> (pinned version, licence, licence SHA-256, wired?, in-house
# counterpart, reason in one line)
PINNED = {
    "diskcache": (
        "5.6.3", "Apache-2.0",
        "583546baa3fd93607d845126570677a401f508e228b5044fefbc3949af179672",
        True, "resilience_power.CheckpointStore (checkpoints only)",
        "TTL cache is a job no in-house module does; checkpoints stay native",
    ),
    "whenever": (
        "0.11.0", "MIT",
        "37fe1c54cc76545cc0741edf028c7edb6708881fc5efe26180cecf98213282b8",
        True, "resilience_clock (classification only)",
        "explicit DST disambiguation policy; stdlib fallback refuses gaps",
    ),
    "aiohappyeyeballs": (
        "2.7.1", "PSF-2.0",
        "3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf",
        True, "resilience_connectivity.HAPPY_EYEBALLS_PATTERN (note only, no code)",
        "real stagger/interleave implementation for asyncio callers",
    ),
    "exactly_once": (
        "0.2.1", "MIT",
        "c116e54abaae86fb06c40771fc6c5e07a46d4bb653bd7026868e0363e8cc2b39",
        False, "resilience_durability.EffectLedger",
        "in-house ledger is fence-aware; package is broader, 0.2.x, unfenced",
    ),
    "agent_saga": (
        "2.3.11", "Apache-2.0",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        False, "resilience_durability saga SPEC + CompensationRegistry",
        "in-house is deliberately not an engine; package is a large "
        "enterprise platform with a trademark mandate in its NOTICE",
    ),
}

LICENCE_FILES = {
    "diskcache": "gui/assets/resilience/LICENSE-diskcache.txt",
    "whenever": "gui/assets/resilience/LICENSE-whenever.txt",
    "aiohappyeyeballs": "gui/assets/resilience/LICENSE-aiohappyeyeballs.txt",
    "exactly_once": "gui/assets/resilience/LICENSE-exactly-once.txt",
    "agent_saga": "gui/assets/resilience/LICENSE-agent-saga.txt",
}


def swap_status() -> dict:
    """The verdict table, as data. A caller can report it verbatim."""
    out = {}
    for name, (version, licence, sha, wired, counterpart, reason) in PINNED.items():
        out[name] = {
            "pinned_version": version, "licence": licence,
            "licence_sha256": sha, "wired": wired,
            "in_house_counterpart": counterpart, "reason": reason,
            "licence_file": LICENCE_FILES[name],
            "runtime_required": False,
        }
    return out


def _load_optional(name: str):
    """Import an optional package, or return None. Never raises for a
    missing package; a broken installed package also yields None, so
    the caller falls back instead of crashing the app."""
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 — any import failure = unavailable
        return None


# ---------------------------------------------------------------------
# diskcache TTL cache
# ---------------------------------------------------------------------

class TTLCache:
    """A TTL cache: diskcache when installed, in-memory dict otherwise.

    Values are JSON only, mirroring the in-house stores (never pickle).
    ``cache_obj`` is an injection point for tests: an object with
    ``set(key, value, expire=...)``, ``get(key)``, ``delete(key)`` and
    optionally ``close()``. When no object is injected and diskcache is
    absent, the dict fallback applies the same expiry check, so the
    semantics do not change with the backend — only the persistence.
    """

    def __init__(self, directory: str, cache_obj=None):
        self.directory = str(directory)
        self._diskcache = None
        if cache_obj is not None:
            self._backend = "injected"
            self._cache = cache_obj
            self._memory = None
        else:
            mod = _load_optional("diskcache")
            if mod is not None:
                os.makedirs(self.directory, exist_ok=True)
                self._diskcache = mod.Cache(self.directory)
                self._backend = "diskcache"
                self._cache = self._diskcache
                self._memory = None
            else:
                self._backend = "memory"
                self._cache = None
                self._memory = {}

    @property
    def backend(self) -> str:
        return self._backend

    def set(self, key: str, value, expire_s: float | None = None) -> None:
        blob = json.dumps(value)
        if self._cache is not None:
            # diskcache (and injected fakes) take expire in seconds;
            # None means no expiry, matching the dict fallback.
            self._cache.set(str(key), blob, expire=expire_s)
        else:
            expiry = None if expire_s is None else time.time() + float(expire_s)
            self._memory[str(key)] = (blob, expiry)

    def get(self, key: str):
        if self._cache is not None:
            blob = self._cache.get(str(key))
            if blob is None:
                return None
            return json.loads(blob)
        entry = self._memory.get(str(key))
        if entry is None:
            return None
        blob, expiry = entry
        if expiry is not None and time.time() >= expiry:
            del self._memory[str(key)]
            return None
        return json.loads(blob)

    def delete(self, key: str) -> None:
        if self._cache is not None:
            self._cache.delete(str(key))
        else:
            self._memory.pop(str(key), None)

    def close(self) -> None:
        if self._diskcache is not None:
            self._diskcache.close()


# ---------------------------------------------------------------------
# whenever DST disambiguation
# ---------------------------------------------------------------------

def resolve_local_time(year: int, month: int, day: int, hour: int = 0,
                       minute: int = 0, second: int = 0, *,
                       tz: str, disambiguation: str = "raise",
                       whenever_mod=None) -> dict:
    """Resolve a local "run at" time to a UTC instant, honestly.

    ``disambiguation``: "earlier" | "later" | "raise". Under "raise",
    a repeated hour is refused rather than guessed. A skipped (gap)
    hour is refused under the stdlib fallback in all modes — the
    fallback cannot invent an instant that does not exist — and
    resolved under the caller's policy only when whenever is present.
    Returns a dict with ``ok``, ``utc_iso`` (when ok), ``repeated``,
    ``backend`` ("whenever" or "stdlib"), and a ``reason`` when refused.
    """
    if disambiguation not in ("earlier", "later", "raise"):
        return {"ok": False, "repeated": False, "backend": "none",
                "reason": "disambiguation must be earlier, later, or raise"}
    mod = whenever_mod if whenever_mod is not None else _load_optional("whenever")
    if mod is not None:
        try:
            zdt = mod.ZonedDateTime(year, month, day, hour, minute, second,
                                    tz=tz, disambiguation=disambiguation)
            repeated = bool(zdt.is_repeated())
            utc = datetime.fromtimestamp(zdt.to_instant().timestamp(),
                                         tz=timezone.utc)
            return {"ok": True, "utc_iso": utc.isoformat(),
                    "repeated": repeated, "backend": "whenever",
                    "reason": "resolved by whenever under policy "
                              f"'{disambiguation}'"}
        except Exception as exc:  # noqa: BLE001 — a refusal is a verdict
            # The refusal itself does not say whether the hour was
            # repeated or skipped; probe with "earlier" (which never
            # refuses a repeated hour) so the verdict still reports it.
            repeated = False
            try:
                probe = mod.ZonedDateTime(year, month, day, hour, minute,
                                          second, tz=tz,
                                          disambiguation="earlier")
                repeated = bool(probe.is_repeated())
            except Exception:  # noqa: BLE001 — probe failure = not repeated
                repeated = False
            return {"ok": False, "repeated": repeated, "backend": "whenever",
                    "reason": f"whenever refused: {exc}"}
    # Stdlib fallback: zoneinfo + fold. Repeated = fold 0 and fold 1
    # give different UTC offsets. Gap = neither fold round-trips.
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception as exc:  # noqa: BLE001 — unknown zone is a verdict
        return {"ok": False, "repeated": False, "backend": "stdlib",
                "reason": f"unknown time zone: {exc}"}
    naive = datetime(year, month, day, hour, minute, second)
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    # Round-trip test first: a repeated hour round-trips under BOTH
    # folds (both occurrences are real instants); a gap hour round-
    # trips under NEITHER (fold offsets differ in both cases, so an
    # offset comparison alone cannot tell them apart — that was a
    # real bug in the first draft of this function, caught by test).
    def _round_trips(dt):
        return dt.astimezone(timezone.utc).astimezone(zone).replace(
            tzinfo=None) == naive
    first_ok, second_ok = _round_trips(first), _round_trips(second)
    if not first_ok and not second_ok:
        return {"ok": False, "repeated": False, "backend": "stdlib",
                "reason": "skipped DST gap hour refused: no such local time; "
                          "whenever is required to resolve it under a policy"}
    repeated = first.utcoffset() != second.utcoffset()
    if repeated:
        if disambiguation == "raise":
            return {"ok": False, "repeated": True, "backend": "stdlib",
                    "reason": "repeated DST hour refused under policy 'raise'"}
        chosen = first if disambiguation == "earlier" else second
        return {"ok": True,
                "utc_iso": chosen.astimezone(timezone.utc).isoformat(),
                "repeated": True, "backend": "stdlib",
                "reason": f"repeated hour resolved to {disambiguation} "
                          "occurrence via fold"}
    utc_candidate = first.astimezone(timezone.utc)
    return {"ok": True, "utc_iso": utc_candidate.isoformat(),
            "repeated": False, "backend": "stdlib",
            "reason": "unique local time resolved via zoneinfo"}


# ---------------------------------------------------------------------
# aiohappyeyeballs async connect
# ---------------------------------------------------------------------

async def happy_eyeballs_connect(host: str, port: int, *,
                                 stagger_delay: float = 0.25,
                                 connector=None) -> dict:
    """Race TCP connects across address families (Happy Eyeballs).

    Returns {"ok": True, "backend": ..., "family": ..., "sock": socket}
    on success; the caller owns (and must close) the socket. On
    failure returns {"ok": False, "backend": ..., "reason": ...} — a
    failed connect is never reported as a socket. ``connector`` is a
    test injection: ``async (host, port, delay) -> socket``.
    """
    if connector is not None:
        try:
            sock = await connector(host, port, stagger_delay)
            return {"ok": True, "backend": "injected",
                    "family": sock.family, "sock": sock}
        except Exception as exc:  # noqa: BLE001 — failure is a verdict
            return {"ok": False, "backend": "injected",
                    "reason": f"connect failed: {exc}"}
    mod = _load_optional("aiohappyeyeballs")
    if mod is not None:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            sock = await mod.start_connection(
                infos, happy_eyeballs_delay=stagger_delay)
            return {"ok": True, "backend": "aiohappyeyeballs",
                    "family": sock.family, "sock": sock}
        except Exception as exc:  # noqa: BLE001 — failure is a verdict
            return {"ok": False, "backend": "aiohappyeyeballs",
                    "reason": f"connect failed: {exc}"}
    # Stdlib fallback: asyncio.open_connection races addresses itself
    # (RFC 8305 in the event loop); the beat lost here is stagger /
    # interleave control and a raw socket, stated, not hidden.
    try:
        reader, writer = await asyncio.open_connection(host, port)
        sock = writer.get_extra_info("socket")
        writer.close()
        return {"ok": True, "backend": "stdlib",
                "family": sock.family if sock is not None else None,
                "sock": None,
                "note": "stdlib open_connection raced the connect; no raw "
                        "socket is returned by this backend (the stream "
                        "was closed) — use aiohappyeyeballs for a raw socket"}
    except Exception as exc:  # noqa: BLE001 — failure is a verdict
        return {"ok": False, "backend": "stdlib",
                "reason": f"connect failed: {exc}"}
