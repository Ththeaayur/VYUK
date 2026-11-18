# Sweep Build 6 — resilience-swap licence reads

Primary reads only: every licence below was read from the LICENSE/NOTICE
file inside the actual sdist AND the actual wheel for the pinned version,
and the two copies were checked byte-for-byte identical (same size, same
SHA-256). No README quotation was relied on. This corrects the Sweep
Phase 1 position, which was metadata/README-level only for all five, and
the Sweep 5 lesson, where a summary claimed a clearance the tree still
recorded as OWED. Licence files are filed under `gui/assets/resilience/`.

No package below is a runtime requirement: `requirements.txt` is
unchanged. Wired packages are imported only behind an import guard, and
every wired path has an in-house fallback that still works when the
package is absent. A package that is NOT wired is not imported by any
wired path, even though its licence file is filed here as evidence.

## diskcache 5.6.3 — Apache-2.0 — WIRED (optional TTL cache only)

- Source: `diskcache-5.6.3.tar.gz` and `diskcache-5.6.3-py3-none-any.whl`,
  `LICENSE`, 559 bytes,
  SHA-256 `583546baa3fd93607d845126570677a401f508e228b5044fefbc3949af179672`.
- The file is the short-form Apache notice (copyright Grant Jenks
  2016-2022, "Licensed under the Apache License, Version 2.0", full text
  by URL reference), not the full Apache text. That short form IS the
  licence file upstream ships in both distributions, and it resolves
  the Phase 1 conflict (PyPI Apache vs GitHub NOASSERTION) in favour of
  Apache-2.0 for the distributed files. The full Apache text is NOT
  filed here because upstream does not ship it; anyone redistributing
  diskcache itself must take the full text from apache.org.
- Filed: `gui/assets/resilience/LICENSE-diskcache.txt`.
- Wiring verdict: WIRED, but NOT as a checkpoint replacement. The
  in-house `resilience_power.CheckpointStore` must never evict or expire:
  a checkpoint that silently disappears is a lost "where was I". What
  diskcache beats is a different job — a TTL cache, which no in-house
  module provides. `core/resilience/resilience_swaps.TTLCache` is that job
  only: JSON values, explicit expiry, diskcache when installed,
  in-memory dict with the same expiry check when it is not.

## whenever 0.11.0 — MIT — WIRED (optional DST disambiguation only)

- Source: `whenever-0.11.0.tar.gz` and the cp312 manylinux wheel,
  `LICENSE`, 1,088 bytes,
  SHA-256 `37fe1c54cc76545cc0741edf028c7edb6708881fc5efe26180cecf98213282b8`
  (copyright Arie Bovenberg 2022-2026, MIT).
- The wheel additionally carries `LICENSE-THIRD-PARTY` (208,971 bytes,
  SHA-256 `3c291221b17a395492e7d9e7467bcd3c4c26ddc44dc1b6b54157db3f582a56e5`),
  a list of Rust build dependencies, each stated MIT OR Apache-2.0. That
  file is NOT copied into the tree (size); its SHA is recorded here and
  its permissive character is the reason no third-party licence blocks
  the optional use. whenever is a compiled (Rust) extension: the wired
  path never requires it, so a platform without a wheel simply falls
  back to stdlib.
- Filed: `gui/assets/resilience/LICENSE-whenever.txt`.
- Wiring verdict: WIRED, optionally. `resilience_clock` classifies sleep
  vs crash but offers no disambiguation policy for a local "run at"
  time that falls in a repeated DST hour or a skipped gap hour.
  `whenever.ZonedDateTime(..., disambiguation=...)` with
  `is_repeated()` is a real beat for that one question, and only that
  question. The stdlib fallback resolves repeated hours via `fold`
  and REFUSES gap hours outright (never guesses); whenever, when
  present, resolves both under the caller's stated policy.

## aiohappyeyeballs 2.7.1 — PSF-2.0 — WIRED (optional async connect only)

- Source: `aiohappyeyeballs-2.7.1.tar.gz` and
  `aiohappyeyeballs-2.7.1-py3-none-any.whl`, `LICENSE`, 13,936 bytes,
  SHA-256 `3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf`,
  the Python Software Foundation licence text; wheel METADATA confirms
  `License: PSF-2.0`.
- Filed: `gui/assets/resilience/LICENSE-aiohappyeyeballs.txt`.
- Wiring verdict: WIRED, optionally. The in-house position was a
  pattern note only (`resilience_connectivity.HAPPY_EYEBALLS_PATTERN`) — no
  code at all. `aiohappyeyeballs.start_connection` with a stagger
  delay and family interleave is a real implementation of that
  pattern for asyncio callers, returning a raw socket. The fallback
  is `asyncio.open_connection`, which itself races addresses in
  stdlib; the beat is stagger/interleave control and a raw socket,
  not the mere idea of racing connects, and that is stated honestly
  in the module. Single attempt per address; no retry loop is added.

## exactly-once 0.2.1 — MIT — NOT WIRED

- Source: `exactly_once-0.2.1.tar.gz` and
  `exactly_once-0.2.1-py3-none-any.whl`, `LICENSE`, 1,074 bytes,
  SHA-256 `c116e54abaae86fb06c40771fc6c5e07a46d4bb653bd7026868e0363e8cc2b39`
  (copyright Maroua Boudoukha 2026, MIT). Licence read PASSES.
- Filed: `gui/assets/resilience/LICENSE-exactly-once.txt` (evidence
  only; filing is not wiring).
- Wiring verdict: NOT WIRED, because it does not beat the in-house
  pattern. `resilience_durability.EffectLedger` is already exactly-once by
  key, WAL-backed, JSON-only, and — decisively — fence-aware: every
  replayed step passes the failsafe predicate. The package (0.2.x,
  maturity risk as flagged in Phase 1) is broader — claim/lease/
  quarantine policies, framework integrations, on-chain helpers — and
  none of it is fence-aware. Swapping would trade a tailored,
  fence-checked ledger for a larger, younger, unfenced one. The
  in-house ledger stays, and no wired path imports `exactly_once`.

## agent-saga 2.3.11 — Apache-2.0 — NOT WIRED

- Source: `agent_saga-2.3.11.tar.gz` and
  `agent_saga-2.3.11-py3-none-any.whl`, `LICENSE`, 11,358 bytes,
  SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`
  (the standard Apache-2.0 text), plus `NOTICE`, 987 bytes,
  SHA-256 `c310c25fd3afa66767a3c61eed60357ca0f64a795b2690ad77dffeb14c869361`.
  Licence read PASSES on the code licence.
- Filed: `gui/assets/resilience/LICENSE-agent-saga.txt` and
  `gui/assets/resilience/NOTICE-agent-saga.txt` (evidence only).
- Wiring verdict: NOT WIRED, for two independent reasons. First, the
  in-house position is deliberately NOT an engine: `resilience_durability`
  ships a saga SPEC and a compensation registry for reversible tool
  classes only, because the resilience wiring fence forbids a second
  orchestration owner ("no executor engine"). Wiring a saga engine
  would break that fence. Second, the package itself is a large
  enterprise platform (SAGAOPS, ~1 MB sdist, ZKP/UI-compiler/ultra-
  engine tests), and its NOTICE imposes a trademark/attribution
  mandate ("agent-saga", "SAGAOPS", "BeastEngine", "ContextBroker"
  are trademarks; Apache §6 does not licence the name). The spec +
  registry stay, and no wired path imports `agent_saga`.

## What this build does not change

Wake, capture, and voiceprint are untouched. No new sidebar entry, no
stack renumbering, no frozen schema/cap change, no globe file, no
VERSION change, no data change. No package is imported unconditionally
anywhere in this build. Field proof — that a swap improves a real
workload on the owner's machine — is NOT claimed; it is owed after
the BB101 quiz, like every other field item.
