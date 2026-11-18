# Benchmark harness + provers index (GAPS-30, T-V5)

The workflow benchmark and the per-batch provers live OUTSIDE
this tree, in the code-lab (`~/workspace/code-lab/`). They are
not duplicated here: this file is the index that names them,
so the tree never drifts from the harness it cannot see.

## The 78-task workflow benchmark

- Harness: `~/workspace/code-lab/benchmark_workflows.py`
  (1,656 lines). Run it from the code-lab:
  `python3 benchmark_workflows.py`. It writes
  `~/workspace/code-lab/benchmark_report_v1.json` and exits 0
  iff every machinery task passes.
- Tasks: 78, ALL machinery kind (counted from the `@task`
  decorators, 2026-11-14). The "gap" kind still exists in the
  runner (a gap task is recorded as status "gap", never
  pass/fail, and is excluded from the score + exit code) but
  no task currently uses it: the four voice-gap tasks X01–X04
  closed in the voice-adoption batch and are now machinery
  tasks. The old "74 machinery + 4 gap-evidence" count in
  tests/test_benchmark.py's docstring was stale on exactly
  this point and is corrected there.
- What it is: a MACHINERY / CONFORMANCE benchmark only. No
  live model, no real Qt GUI, no live voice session. Leaf
  handlers are stubbed where noted per task; the gate is
  never stubbed. The score is NOT real-world task success,
  NOT a product rating, and NOT a JARVIS-ness score (the
  harness's own HONESTY note says so in full).
- What it is not: a substitute for the neighbour runs
  (tests/CANONICAL_CI.md) or the field proofs (the Windows
  matrix, tests/WINDOWS_MATRIX_RESULTS.md).

## The in-tree benchmark subset

- `tests/test_benchmark.py` pins the benchmark's CORE
  (classification, lifecycle, ledger restart/resume, C-14
  through a fresh funnel registry, deep_research on a temp
  store, contract routing records) so it regresses with the
  suite. It pins the core, NOT the count: the full 78-task
  harness is not duplicated here.

## The per-batch provers

- 332 `prove_*.py` files in `~/workspace/code-lab/` (counted
  2026-11-15, debug pass: prove_ret1..prove_ret5 added after
  the GAPS-30 count of 327). Each batch's
  prover re-runs its gap shapes
  plus the D1 (data identical after restore) and D2 (census
  pinned) checks; the neighbour tally for the batch explains
  every Ran-sum delta against the previous batch's baseline.
  The provers are the batch-evidence scripts, not part of the
  shipped product: they live in the code-lab because they
  need both trees (live + pristine backup) at once.
- Current prover: `prove_gaps30.py` (GAPS-30, the last gaps
  batch — §6.11 T-series + §6.12 D-V-series).
