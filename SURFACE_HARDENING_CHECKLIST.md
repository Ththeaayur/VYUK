# Per-integration hardening checklist (RET-4, 2026-11-15)

**Status: a standing checklist, not a build.** It picks no surface, orders
no surface, adds no tool and changes no integration. It is applied to each
daily-job surface in turn, by that surface's own single-surface batch, once
the owner has picked the surfaces (D-RET-1, owed — see the standing
restraint note in `TOOLS_LOOPHOLES_AUDIT.md`, marker
`RET-4-STANDING-RESTRAINT`). Until the pick lands, no surface work starts.

**Candidate set (not a pick):** Gmail / Calendar / files / browser — the
surfaces the morning briefing and the routines actually need, per the
retention report. The owner picks 3–5 from this set (plus whatever the
briefing and routines actually need). **Sequencing: one surface at a time,
reliability before the next.** The order is the owner's pick; this document
does not choose it, and neither does any batch.

**Existing integrations outside the pick are FROZEN** — not removed, no new
work on them, no new tools for them (see the standing note). A surface batch
works on exactly one picked surface and nothing else.

Each item below states what proof looks like — which script, test, or prover
check shows it. An item is done only when its proof exists and passes; an
aspiration with no proof is not done.

## (a) The surface's read path and write path are proven separately

**What proof looks like:** the surface batch's gap script (`gap_<surface>.py`,
tree as a required argv, no default) carries separate probes for reads and
for writes — a read probe that can pass while the write probe fails, and
vice versa — run against temp dirs / fake registries only, the way RET-2's
and RET-3's gap probes run. The suite (`tests/test_<surface>.py`) pins both
paths with their own tests. A batch that proves "the surface works" with a
single end-to-end probe has not proven (a).

## (b) Every failure mode is an honest refusal with the reason — never a silent wrong result

**What proof looks like:** the prover (`prove_<surface>.py`, all four
combos) carries a negative check per failure mode the surface has — service
unreachable, auth missing or expired, malformed input, quota or rate limit,
empty result where a result is owed — and for each one asserts the answer is
a refusal carrying the reason (the reason string is pinned in the suite).
A failure mode with no prover check is an unproven item. A silent wrong
result (a "done" that did nothing, a stale value presented as fresh) fails
the batch outright.

## (c) The surface's confirm-gated actions stay gated

**What proof looks like:** the prover checks the funnel's classification
source (`core/funnel/tool_classification.py` — the single source of truth)
lists the surface's gated actions as gated, and a real-funnel probe (a deny
handler, RET-2's pattern) shows the funnel is asked at run time and the
refusal stands. The suite pins the gated set. A surface batch that moves a
gated action to the free set, or that routes around the funnel, fails the
batch outright.

## (d) The surface is proven against the live service — or a faithful local stand-in the batch names

**What proof looks like:** the surface batch names its proof route in its
own record: either a live run against the real service, or a faithful local
stand-in — a recorded real record from the live service, verified in the lab
through an injectable transport (the trivy/OSV precedent: the live fetch is
recorded once, the lab verifies against that recorded real record). A
stand-in that is not a recorded real record is not faithful and does not
count. The field leg — a real run on the owner's Windows machine — stays
owed in the batch record until it happens, and lab green is never presented
as field proof.

## (e) The surface's cost (API calls, tokens) is visible in the cost ledger

**What proof looks like:** the prover checks the surface's calls appear in
the cost ledger exactly — `CostLedger.summary()` / `spend_day()` totals
match the surface's recorded calls (RET-3's spend-totals-match pattern),
read through `CostLedger` methods only, never raw SQL. If the surface's calls
are not yet ledger-recorded, adding that recording is part of the surface
batch, not assumed: the batch wires the recording, proves it with a probe
that makes a call and reads it back from the ledger, and only then counts
(e) as done.

## Per-surface exit

A surface counts as done only when all five items above are proven with the
proofs named, the neighbour run shows zero live-only failures, and the
census is unchanged (or re-baselined by the batch's own declared change, the
GAPS-20 / RET-2 precedent). Only then may the next surface's batch start.
