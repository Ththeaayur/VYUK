# Windows Validation Matrix — VYUK (v5.45)

A runnable checklist for validating VYUK's desktop control on a
real Windows machine. This is a human-run instrument: VYUK's
cursor, UIA and launch paths depend on real DPI scaling, real
monitors and real apps, which no Linux CI can reproduce.

## How to run it

1. Install / update VYUK to the build under test and note the
   version here: **Build:** ____________  **Date:** __________
   **Tester:** ____________  **Machine / GPU:** __________________
2. Work through every task row under **each** display
   configuration column. Change scaling in Windows Settings →
   System → Display (sign out/in if Windows asks), and run the
   second-monitor pass with the second display connected and set
   to "Extend".
3. Drive every task BY VOICE (or typed commands) through VYUK —
   never by hand. If you have to touch the mouse/keyboard yourself
   to rescue a task, that counts as a human intervention (below).
4. Score each cell:
   - **Success** — `PASS` (VYUK completed the task unaided),
     `PARTIAL` (completed with a wrong first attempt VYUK recovered
     from), or `FAIL` (task not completed / wrong result).
   - **Steps** — number of VYUK tool actions the task took
     (count them in `data/activity.log` after the run).
   - **Human interventions** — how many times YOU had to click,
     type, dismiss a dialog, or re-issue a command.
5. A configuration column **passes** when: every task is `PASS`
   or `PARTIAL`, no task has more than **1 human intervention**,
   and at least **6 of 7 tasks are full `PASS`**. The build
   **passes the matrix** when all four columns pass.

## Task list (fixed — do not substitute tasks)

| # | Task (say / type exactly this class of request) | What success looks like |
|---|-------------------------------------------------|--------------------------|
| 1 | **Notepad** — "Open Notepad and type: The quick brown fox jumps over the lazy dog." | Notepad is focused, the exact sentence is in the document, nothing typed anywhere else. |
| 2 | **File Explorer** — "Open File Explorer in my Documents folder and tell me the largest file there." | Explorer opens at Documents; the spoken answer names the actual largest file. |
| 3 | **Settings** — "Open Settings and tell me whether Bluetooth is on." | Settings opens; the answer matches the real Bluetooth state (verify by eye). |
| 4 | **Chrome** — "Open Chrome, go to example.com, and read me the heading on the page." | Chrome loads example.com; the spoken heading matches the page's real heading. |
| 5 | **Excel** — "Open Excel, put 2 in A1 and 3 in B1, and =SUM(A1:B1) in C1." | The workbook shows 5 in C1; values land in the right cells, not in the formula bar or another window. |
| 6 | **Electron app (Slack or VS Code)** — "Open Slack (or VS Code) and tell me what is on screen." | The app opens; the description matches what is visibly on screen (window title / main pane). |
| 7 | **Canvas app** — "Open Paint (or another canvas app), draw a rough circle in the middle of the canvas." | A recognisable closed-ish curve appears near the canvas centre — not in a corner, not off-canvas. |

## Scoring grid

Fill one cell per task per configuration as
`Success / Steps / Human interventions` (e.g. `PASS / 4 / 0`).

| # | Task | 100% scaling, single monitor | 125% scaling, single monitor | 150% scaling, single monitor | 100% scaling, second monitor (Extend) |
|---|------|------------------------------|------------------------------|------------------------------|----------------------------------------|
| 1 | Notepad |  /  /  |  /  /  |  /  /  |  /  /  |
| 2 | File Explorer |  /  /  |  /  /  |  /  /  |  /  /  |
| 3 | Settings |  /  /  |  /  /  |  /  /  |  /  /  |
| 4 | Chrome |  /  /  |  /  /  |  /  /  |  /  /  |
| 5 | Excel |  /  /  |  /  /  |  /  /  |  /  /  |
| 6 | Electron app (Slack / VS Code) |  /  /  |  /  /  |  /  /  |  /  /  |
| 7 | Canvas app |  /  /  |  /  /  |  /  /  |  /  /  |
|   | **Column verdict (pass thresholds above)** |  |  |  |  |

## Extra checks on the second-monitor pass

- [ ] At least one task (1 or 7) was performed with the target app
      on the **second** monitor, and clicks/drawing landed on that
      monitor — not mirrored onto the primary.
- [ ] Ask VYUK to move the cursor to the far corner of the second
      monitor; it arrives there (negative-coordinate fencing).

## Failure notes

Record every `PARTIAL`/`FAIL`/intervention here with the task #,
configuration, what VYUK did instead, and the matching
`data/activity.log` timestamp — that log line is what a fix gets
built from.

| Task # | Config | What happened | activity.log time |
|--------|--------|---------------|-------------------|
|        |        |               |                   |
|        |        |               |                   |
|        |        |               |                   |

## Runbook — executing one row and recording it (v5.50, item 40)

Results live in `WINDOWS_MATRIX_RESULTS.md` (same folder): one
row per brain × scaling × monitor combination. To execute and
record exactly one row:

1. Pick ONE unfilled row in `WINDOWS_MATRIX_RESULTS.md` and note
   its brain, scaling and monitor setup — that row is the whole
   job; do not run or record any other row in the same pass.
2. Configure the machine to match the row: Windows Settings →
   System → Display → scaling to the row's value (sign out/in if
   Windows asks); for a second-monitor row, connect the display
   and set it to "Extend" (for a single-monitor row, disconnect
   or disable the second display).
3. Install / update VYUK to the build under test, restart it,
   and select the row's brain in Settings before connecting.
4. Run the fixed 7-task list above BY VOICE (or typed commands)
   through VYUK, scoring each task and counting steps and human
   interventions from `data/activity.log` exactly as "How to run
   it" and the scoring grid above prescribe — including the
   extra second-monitor checks on a second-monitor row.
5. Record the run in that row — and only that row — of
   `WINDOWS_MATRIX_RESULTS.md`: **Date** (the day the run
   happened), **Build** (the exact VYUK version tested),
   **Machine** (machine / GPU), **Result** (the column verdict
   from the pass thresholds above), **Notes** (the failure-notes
   reference and `data/activity.log` timestamps for every
   `PARTIAL`/`FAIL`/intervention; leave empty only when there
   were none).
6. Leave every other row untouched. A row that was not actually
   run stays blank — never fill a row from memory, from another
   row's run, or from a different build.
