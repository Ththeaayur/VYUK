# Canonical CI command and classification report (Batch 5, TEST-04)

This is the SINGLE canonical document for the full-suite run.
The runner docstring (`tests/run_tests.py`) points here; do not
maintain a second copy of the command elsewhere.

## The canonical command

Run from the install root, from a restored data baseline:

```
# 1. Restore data to baseline FIRST (delete-first, never overlay).
#    Any run containing timeout tests starts from restored data —
#    the standing Phase 2 C10 rule: a timeout leg records an
#    UNKNOWN-outcome receipt, and the unknown-outcome guard
#    refuses the identical leg until the state is verified.
rm -rf data/*
cp -r <data-backup>/. data/

# 2. Run the suite under the Qt venv with the syslibs EGL env
#    (Qt suites REQUIRE this env; without it they skip or fail
#    on missing libEGL, which is an environment artefact, not a
#    product failure). CHUNKED (GAPS-30, T-V7): the full suite
#    does NOT run in one process — it OOM-kills there (327
#    modules at review time, 389 now). --modules with no names
#    runs every module in its own subprocess, each under the
#    same data guard as the single-process path; that IS the
#    full-suite path. A bare `tests/run_tests.py` (one process)
#    is for a quick smoke run only, never the full suite.
LD_LIBRARY_PATH=$HOME/workspace/code-lab/.syslibs/root/usr/lib/x86_64-linux-gnu \
__EGL_VENDOR_LIBRARY_DIRS=$HOME/workspace/code-lab/.syslibs/root/usr/share/glvnd/egl_vendor.d \
QT_QPA_PLATFORM=offscreen PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  $HOME/workspace/code-lab/.venv-qt/bin/python tests/run_tests.py --modules \
  > <run-log> 2>&1

# 3. Restore data again afterwards (same delete-first restore
#    as step 1) and verify the byte count + a clean data diff.
```

The system-python3 run (no PySide6) is a SECOND, narrower
shape, not the canonical one: Qt suites skip there by design,
and the audit-env order failure (TEST-03) reproduces there.
Both shapes are worth running; only the venv-qt + syslibs
run is the canonical baseline.

## The baseline log it compares against

Every full run is compared against the programme baseline log
recorded at ship-gate time (currently
`globefix_v599_parent_run1.log`: 2,484 tests, 3 failures +
1 error, 8 skips, classified clean — see the programme record
for the exact file). The suite has grown since: 9,891 test methods in 453 files at round-3 fix Q8 (dead-on-arrival Live sessions — typed turns stamp _last_text_at and the watchdog gains a typed-turn rule ending the session with LIVE_DEAD; GUI posts the plain-words notice and records the no_reply drop cause, 2026-11-20) — 9,874 test methods in 452 files at round-3 fix Q4 (create_plan steps accept plain strings — schema items anyOf string/object, description/schema/parser agree, 2026-11-20) — 9,854 test methods in 451 files at round-3 fix Q3 (RS-1 watchdog gains a voice-quiescence trip condition — a long spoken turn no longer trips mid-utterance, dead sessions still die, 2026-11-20) — 9,846 test methods in 450 files at f32-exec follow-up (anticipation-executor suite gains a routine-only confirm handler after the f32 confirm-gating + one gate-mechanism pin, 2026-11-20) — 9,845 test methods in 450 files at round-3 fix Q5+Q7 (memory tools + routine pair join the desktop profile — resident 48 -> 53, offered 60 -> 65, 2026-11-19) — 9,809 test methods in 449 files at round-3 fix Q6 (deterministic morning briefing tool — calendar read + rendered briefing with free blocks and overlaps, 2026-11-19) — 9,788 test methods in 448 files at fix #32 follow-up (run_anticipation_routine classified confirm-gated per parent ruling + teach ranking synonym rows, 2026-11-19) — 9,778 test methods in 447 files at round-3 fix Q2 (click_anywhere navigate false-failure on first Chrome attach fixed — CDP outcome translators classify on the body after the bootstrap note, 2026-11-19) — 9,752 test methods in 446 files at round-3 fix Q1 (bad-args plan/routine steps recorded as done, 2026-11-19) — 9,726 test methods in 445 files at round-3 fix #9 (plan-step auto-complete now matches arguments, 2026-11-19) — 9,712 test methods in 444 files at round-3 fix #8 (starter-chip cut fixed — wheel scroll + follow-up chips visible after the first reply, 2026-11-19) — 9,701 test methods in 443 files at round-3 fix #7 (dead browser debug-port env var removed, 2026-11-19) — 9,692 test methods in 442 files at round-3 fix #6 (citizenship card rephrased for the user, 2026-11-18) — 9,632 test methods in 435 files at Build 7 fix #3 (circled "?" help button, 2026-11-18) — 9,627 test methods in 434 files at Build 7 fix #2 (Scheduled tab reorder, 2026-11-18) — 9,622 test methods in 433 files at Build 7 fix #1 (briefing card auto-scroll on expand, 2026-11-18) — 9,617 test methods in 432 files at rethink Build 6 (wizard permissions status list + Memory, Doctor, System Pulse, Senses, 2026-11-18) — 9,588 test methods in 431 files at rethink Build 5 (Chrome connect end-to-end, 2026-11-18) — 9,571 test methods in 430 files at stale-glow-timer follow-up (fix #31 queue, 2026-11-18) — 9,569 test methods in 430 files at plugin_loader dataclass fix (field fix #24 follow-up, 2026-11-18) — 9,563 test methods in 429 files at field-check fix 34 (V3 harness, round-2 item 14, 2026-11-18) — 9,556 test methods in 428 files at field-check fix 33 (find_files speed, round-2 item 13, 2026-11-18) — 9,549 test methods in 427 files at field-check fix 32 (anticipation executor, round-2 item 12, 2026-11-18) — 9,529 test methods in 426 files at field-check fix 31 (stale glow timer, round-2 item 11, 2026-11-18) — 9,525 test methods in 425 files at field-check fix 30 (Home strip session-only, round-2 item 10, 2026-11-18) — 9,516 test methods in 424 files at field-check fix 29 (permissions-page mic row, round-2 item 9, 2026-11-18) — 9,510 test methods in 423 files at field-check fix 28 (Memory header count, round-2 item 8, 2026-11-18) — 9,502 test methods in 422 files at field-check fix 27 (learning truncates abbreviations, round-2 item 7, 2026-11-18) — 9,489 test methods in 421 files at field-check fix 26 (T8 #24 marker truncation, round-2 item 6, 2026-11-18) — 9,478 test methods in 420 files at field-check fix 25 (release_describe TypeError payload, round-2 N1 BUG 2, 2026-11-18) — 9,468 test methods in 419 files at field-check fix 24 (personal_skills handler_error, round-2 N1 BUG 1, 2026-11-18) — 9,462 test methods in 418 files at field-check fix 23 (Settings search ranking + Language top task, round-2 N3, 2026-11-18) — 9,444 test methods in 417 files at field-check fix 22 (cloud brain finishes with no key, round-2 N4 FAIL 2, 2026-11-18) — 9,432 test methods in 416 files at field-check fix 21 (wizard camera ask never-decided, round-2 N4 FAIL 1, 2026-11-17) — 9,418 test methods in 415 files at field-check fix 20 (plan-announcement on_transcript guard F2, 2026-11-17) — 9,412 test methods in 414 files at field-check fix 19 (receive-loop on_transcript_chunk guard F2, 2026-11-17) — 9,406 test methods in 413 files at field-check fix 18 (M10c STT hub download bound, 2026-11-17) — 9,399 test methods in 412 files at field-check fix 17 (receive-loop on_transcript guard F2, 2026-11-17) — 9,395 test methods in 411 files at field-check fix 16 (silent-loss T8, 2026-11-17) — 9,378 test methods in 410 files at field-check fix 15 (anticipation routine persistence T10, 2026-11-17) — 9,371 test methods in 409 files at field-check fix 14 (memory cap T9, 2026-11-17) — 9,363 test methods in 408 files at field-check fix 13 (interrupt eats queued audio M13, 2026-11-17) — 9,360 test methods in 408 files at field-check fix 12 (output-device picker M11, 2026-11-17) — 9,349 test methods in 408 files at field-check fix 11 (hallucinated turns M10, 2026-11-17) — 9,343 test methods in 408 files at field-check fix 10 (named-tool demand V2, 2026-11-17) — 9,342 test methods in 408 files at field-check fix 9 (wizard first-run asks U6, 2026-11-17) — 9,326 test methods in 408 files at field-check fix 8 (Activity/briefing U5, 2026-11-17) — 9,321 test methods in 408 files at field-check fix 7 (chat history U4, 2026-11-17) — 9,315 test methods in 408 files at field-check fix 6 (Dictate cluster U3, 2026-11-17) — 9,308 test methods in 408 files at field-check fix 5 (PTT focus-loss leak M9, 2026-11-17) — 9,302 test methods in 408 files at field-check fix 4 (play music T11, 2026-11-17) — 9,296 test methods in 408 files at field-check fix 3 (Doctor-close crash U7, 2026-11-17) — 9,286 test methods in 408 files at field-check fix 2 (silent-loss cluster T5/T6/T7, 2026-11-17) — 9,274 test methods in 408 files at field-check fix 1 (browser control B2/B3/B4, 2026-11-17) — 9,265 test methods in 408 files at chat pass (sidebar + chat back to v6.3, 2026-11-17) — 9,256 test methods in 407 files at rethink Build 4 (Always-allow with T&Cs, 2026-11-17) — at rethink Build 3 (Settings + sidebar + strip, 2026-11-17) it was 9,200 test methods in 405 files — at rethink Build 2 (Activity density, 2026-11-16) it was 9,176 test methods in 404 files — at rethink Build 1 (2026-11-16) it was 9,123 test methods in 403 files — at first-run onboarding (2026-11-16) it was 9,119 test methods in 402 files — at sci-fi Activity (2026-11-16) it was 9,074 test methods in 401 files, at sci-fi Home (2026-11-16) it was 9,047 test methods in 400 files, at flaw-sweep (2026-11-16) it was 8,848 test methods in 396 files, the 199 since then being the three builds that landed without a baseline update (Settings redesign 67, P2 speed pass 34, Chat UI 68) plus this build's sci-fi Home suite (30) — at RET-5 (2026-11-15) it was 8,757 test methods in 394 files, at GAPS-30 (2026-11-14) it was 8,629 test methods in 389 files, that count including the four test files that batch added (test_gaps30, test_import_graph, test_packaging_drift, test_dependencies_declared; 32 methods between them, the last three being the timer + backup pins), and RET-1 adds test_ret1 (20 methods), and RET-2 adds test_ret2 (21 methods), and RET-3 adds test_ret3 (29 methods), and RET-4 adds test_ret4 (20 methods), and RET-5 adds test_ret5 (38 methods), so the v5.99 counts are history, not the current baseline. The current baseline record is the latest neighbour-run tally in the
code-lab: `gaps29_r1_tally.log` — 1,080 logs (270 suites x 4
combos), Ran-sum 25,754, 0 live-only failures, 2 _FailedTest
lines (the new suite on the backup tree, by design). A new
baseline tally supersedes it each batch; the v5.99 log stays
as the ship-gate-time record. The comparison is BY
CLASSIFICATION, not by count: timing variance moves the count
on the same tree (the v5.99 gate record shows 3F+1E in the
slower parent run vs 16F+1E in the faster extract run, same
classification), so chasing a zero count is not the criterion
and must never be bought by deleting, skipping, or weakening
a test.

## Classification procedure

For each failure/error in the run log, in order:

1. **Genuine budget refusal** — the failure body contains
   `'kind': 'budget'` (or the "Tool-call safety budget
   tripped" message). Known harness artefact: the
   process-global 120-calls-per-60-seconds window tripped on
   ordering, not on the code under test. Count it, do not
   chase it — unless it appears in a suite that carries the
   L3 budget-isolation guest rule, in which case it is NEW
   (the isolation should have prevented it) and blocks.
2. **Budget-masked** — the failure body mentions budget, or
   the failure is in a suite known to trip on budget, but the
   body does not prove it. Prove it by an alone-rerun of
   that single test on freshly restored data: green alone
   confirms budget-masked; still red alone is NEW and
   blocks.
3. **Known stable identity** — the failure matches a
   baseline-log entry by test name AND failure signature
   (e.g. the carried `test_release_w10` `KeyError: 'reason'`
   leaver). Cite the baseline log line. A same-name failure
   with a DIFFERENT signature is NEW, not stable.
4. **Receipt-repeat** — a timeout-leg failure of the form
   `'refused' != 'timeout'` (the TEST-01 signature) in a
   suite that restores receipts before running. If it
   appears, the restore discipline regressed: NEW, blocks.
5. **Order contamination** — a chatfix ladder failure
   (`_w9_error_ladder` missing / `ladder` None, TEST-03
   signature) in a combined run while chatfix is green
   alone. If it appears, a snapshot/restore teardown
   regressed (or a new delete-all appeared): NEW, blocks.
6. **Anything else = NEW and blocks.** A NEW failure is
   never reclassified by analogy ("looks like budget") —
   it needs its own alone-rerun proof or its own fix.

Skips are legitimate only when explained: Qt-absent skips
on the system interpreter, and dependency skips the test
itself declares. Unexplained skips are reported, not hidden.

## Report tool (report-only by default, --gate when asked)

`tests/classify_run.py <run-log>` implements buckets 1–6
as a text scan and prints counts plus the per-failure bucket.
By default it NEVER gates: it always exits 0, it never edits
a log, and a "NEW: 0" line from it is not a ship decision —
the coordinator's classification review is. `--gate` is the
one loud mode: `tests/classify_run.py --gate <run-log>` exits
1 when the classification contains a NEW failure or a
budget-mention (both block until proven or fixed), and 0
otherwise. The gate reports the classification; it does not
replace the review. Do not wire it into `run_tests.py` as an
exit-code condition — never wire the gate into the runner:
the runner stays a pure test run, and the gate is run by hand
(or by CI) over the finished log.

## Coverage (GAPS-30, T-V2)

`tests/run_tests.py --coverage` measures line coverage over
core/ + gui/ + main.py + doctor.py with stdlib trace only
(one module per subprocess, like --modules) and writes
tests/coverage_report.json. The floor it is judged against
lives in tests/coverage_floor.json: the measured percent, the
floor (a minimum to hold, set below the measured value), the
method, and the scope. --coverage reports the percent; it
never gates on the floor itself. A full run takes hours (the
first GAPS-30 attempt was SIGTERM-killed at 146/389 modules
after ~2h), so use `--coverage-parts <dir>`: per-module trace
parts persist there and a killed run resumes from them
(counts are per-module and additive, so a resumed merge
equals a single uninterrupted run's merge). Parts are valid
only while core/ + gui/ are unchanged — after a product-code
change, delete the parts dir and re-run from scratch.

## What this document does NOT claim

The suite is NOT claimed deterministic beyond what the
classification rule supports. A run is "classified clean"
when every failure lands in buckets 1–3 with proof (bucket
2 by alone-rerun), never merely because the count looks
small. Long suites have no per-test timeouts in the runner;
a run killed by an external command cap is a partial log,
not a result — rerun it, do not classify the partial tail.
