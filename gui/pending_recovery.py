"""Startup recovery offers (B16, 2026-11-07): interrupted tasks and goals
offered back after a restart, on the existing Activity surface.

Loophole family — "survived but invisible": an interrupted
task's record exists in the ledger (B11 wrote it: coordinator
tasks paused, budget-paused, crash-interrupted goal records
with state ``running``), but nothing ever showed it to the
user after a restart. The Activity offer covered pending
approvals only (§7 consumers batch 7) and pending questions
only (B15); a task paused for hours, a budget-paused task, a
monitor-gone pause, or a goal interrupted by a crash sat in
the ledger, invisible, until the user happened to dig it
out — a long goal could be silently lost even though its
record survived. The persistence was built; the surfacing
was not.

ONE design: on startup the app reads the ledger's
interrupted records and offers them back — one surface, the
same Activity offer the pending-approval tasks already use,
extended to every interrupted kind. The offer is NEVER
auto-run (a restart is not consent to re-run a half-done
goal, and a goal interrupted mid-destructive-step must never
re-fire on its own); each offered record names what it is
(goal text or task title, where it stopped, why it stopped,
what it spent — the B14 spend line where it exists);
dismissing the offer never deletes the record (dismissal is
"not now", not "abandon" — a dismissed record is offered
again next startup until it is resumed or explicitly
abandoned); resuming goes through the existing resume paths
(``resume_ledger_task`` for coordinator tasks,
``resume_goal`` for goals) with the recorded index and
history intact. The offer is recomputed fresh from the
ledger at every read, so there is no dismissal list to go
stale and no second store — the ledger is the one home.

Rejected alternatives (recorded here, do not re-open):
auto-resuming on startup (a restart is not consent — and a
half-done destructive step re-firing on its own is the exact
failure; the audit's §(b) one-liner about auto-resume for
all-read remainders is NOT followed: the brief rules never
auto-run, and the ledger's ``remaining_all_reads`` hint is
not a consent signal); a separate recovery screen (the
Activity offer already exists and is where interrupted work
is shown — one surface, not two); deleting dismissed
records (a dismissal is not an abandonment); surfacing only
coordinator tasks and not goal records (B11's goal records
are the ones most likely to be long — they must be offered
too).

BOUNDARY — GUI goal resume (D1): resuming a goal needs the
brain session's picker (``resume_goal`` takes a caller-
supplied ``pick_action``; the production picker is a
closure over a cloud text session, not constructible from
the GUI). Wiring a GUI button to the goal loop would be a
new goal-loop entry — the same class as the typed-entry
decision D1, which the owner has not ruled. So a goal
offer in this batch carries its line plus Not now /
Abandon, and says so plainly; the module-level resume
path for goals exists (``resume_recovery_offer`` with a
supplied picker) and is tested, so the GUI wiring is a
one-line change once D1 is ruled — it is not built here.
"""

import asyncio

from voice_assistant.core.gui_facade import INTERRUPTED_STATES

_MAX_LINE = 400


def _as_str(value) -> str:
    return value if isinstance(value, str) else ""


def _cap(text: str, limit: int = _MAX_LINE) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _fmt_elapsed(seconds) -> str:
    """Elapsed seconds as plain language ("4m 12s", "38s")."""
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def spend_line(spend, *, elapsed_s=None) -> str:
    """The B14 spend as one plain-language fragment, or "".

    Elapsed time always (from the spend dict, else the
    caller's elapsed_s); cost only where measured — a
    measured zero (no cloud calls) says nothing, an
    unmeasured cost says "cost unknown", never a fabricated
    number. A partial cost sum is never presented as the
    total (the spend dict carries cost_known False then).
    """
    spend = spend if isinstance(spend, dict) else {}
    elapsed = _fmt_elapsed(spend.get("elapsed_s") or elapsed_s)
    cost = ""
    try:
        cost_usd = spend.get("cost_usd")
        cost_known = bool(spend.get("cost_known", True))
        if not cost_known:
            cost = "cost unknown"
        elif cost_usd is not None and float(cost_usd) > 0:
            cost = f"cost ${float(cost_usd):.2f}"
    except (TypeError, ValueError):
        cost = ""
    parts = []
    if elapsed:
        parts.append(f"spent {elapsed}")
    if cost:
        parts.append(cost)
    return " · ".join(parts)


def why_stopped(kind: str, summary: dict) -> str:
    """Why this record stopped, in plain language.

    Reads stored fields only, never invents: a
    crash-interrupted record (state "running" — the app
    stopped while it was running) names the crash first,
    because its stored reason predates the crash; a paused
    record names its stored reason verbatim (the receipt
    reason: pause reason first, then the monitor sentence,
    then the question, then the approval); a monitor-gone
    pause names its sentence when the reason is empty.
    """
    summary = summary if isinstance(summary, dict) else {}
    state = _as_str(summary.get("state", ""))
    reason = _as_str(summary.get("reason", "")).strip()
    if state == "running":
        return "the app stopped while it was running"
    if reason:
        return _cap(reason)
    monitor = summary.get("pending_monitor")
    if isinstance(monitor, dict):
        sentence = _as_str(monitor.get("sentence", "")).strip()
        if sentence:
            return _cap(sentence)
        if _as_str(monitor.get("tool", "")):
            return "the screen it was watching went away"
    return "paused before finishing"


def replan_line(summary: dict) -> str:
    """The B22 re-plan history as one plain-language fragment, or "".

    A task interrupted mid-re-plan names its re-plan count
    (and the latest re-plan's new sub-goals, when stored),
    so the human sees the plan changed, not just that the
    task stopped. A task that never re-planned names
    nothing — no fragment, never a guess.
    """
    summary = summary if isinstance(summary, dict) else {}
    replans = summary.get("replans") or []
    if not isinstance(replans, list) or not replans:
        return ""
    count = len(replans)
    latest = replans[-1] if isinstance(replans[-1], dict) else {}
    subgoals = [str(s or "") for s in (latest.get("subgoals") or [])]
    if subgoals:
        names = ", ".join(subgoals)
        return f"re-planned {count} time(s) — latest plan: {names}"
    return f"re-planned {count} time(s)"


def recovery_offer_line(kind: str, summary: dict) -> str:
    """One plain-language line per offered record.

    What it is, where it stopped, why it stopped, what it
    spent — the four things the human needs to decide
    Resume / Not now / Abandon. Nothing is invented: a
    missing field drops its fragment, never a guess.
    """
    summary = summary if isinstance(summary, dict) else {}
    if kind == "goal":
        title = (_as_str(summary.get("goal_text", "")).strip()
                 or _as_str(summary.get("title", "")).strip()
                 or "Untitled goal")
        steps_used = int(summary.get("steps_used", 0) or 0)
        windows = int(summary.get("budget_windows_used", 1) or 1)
        where = f"{steps_used} step(s) in, budget window {windows}"
        resume_count = int(summary.get("resume_count", 0) or 0)
        if resume_count:
            where += f", resumed {resume_count} time(s) before"
        spent = spend_line(summary.get("spend") or {},
                           elapsed_s=summary.get("elapsed_s"))
    else:
        title = (_as_str(summary.get("title", "")).strip()
                 or _as_str(summary.get("classification", "")).strip()
                 or _as_str(summary.get("task_id", "")).strip()
                 or "Untitled task")
        completed = int(summary.get("completed", 0) or 0)
        remaining = summary.get("remaining_labels") or []
        total = completed + len(remaining)
        where = f"{completed} of {total} step(s) done"
        if remaining:
            where += f" — next: {str(remaining[0])}"
        spent = spend_line(summary.get("spend") or {})
    parts = [title, where, why_stopped(kind, summary)]
    # B22: a task interrupted mid-re-plan names its re-plan
    # history in the offer line, so the human sees the plan
    # changed — the fragment drops when the task never
    # re-planned, never a guess.
    replanned = replan_line(summary)
    if replanned:
        parts.append(replanned)
    if spent:
        parts.append(spent)
    return " · ".join(_cap(p) for p in parts if p)


def is_recoverable_task(entry: dict) -> bool:
    """A coordinator record the recovery offer covers.

    Interrupted (running / paused / verifying) AND
    carrying neither a pending approval nor a pending
    question nor a pending reconcile — those already have
    their own offers (the Review-approvals, Answer-questions
    and Decide-what-happened buttons), and the queues never
    mix, so this offer never double-offers a record the
    others already cover. Works on ledger summaries and on
    raw records alike (same field names).
    """
    if not isinstance(entry, dict):
        return False
    if _as_str(entry.get("state", "")) not in INTERRUPTED_STATES:
        return False
    if _as_str(entry.get("kind", "")) == "goal":
        return False
    pending = entry.get("pending_approval")
    if isinstance(pending, dict) and _as_str(pending.get("tool", "")):
        return False
    question = entry.get("pending_question")
    if isinstance(question, dict) and _as_str(question.get("question", "")):
        return False
    # B21: a pending reconcile decision already has its own
    # offer (the Decide-what-happened button) — the recovery
    # offer never double-offers it.
    reconcile = entry.get("pending_reconcile")
    if isinstance(reconcile, dict) and _as_str(reconcile.get("question", "")):
        return False
    return bool(_as_str(entry.get("task_id", "")))


def is_recoverable_goal(entry: dict) -> bool:
    """A goal record the recovery offer covers.

    Interrupted (running / paused) and kind "goal".
    Goal records carry no pending approval or question
    (those are coordinator shapes), so there is no queue
    to mix with — every interrupted goal is offered here.
    """
    if not isinstance(entry, dict):
        return False
    if _as_str(entry.get("kind", "")) != "goal":
        return False
    if _as_str(entry.get("state", "")) not in INTERRUPTED_STATES:
        return False
    return bool(_as_str(entry.get("task_id", ""))
                or _as_str(entry.get("goal_id", "")))


def interrupted_recoverable(task_summaries, goal_summaries) -> list:
    """Recovery offers inside ``list_interrupted`` + ``list_interrupted_goals``.

    Pure, stdlib-only. Returns [{kind, id, title, line,
    summary}] — one per interrupted record the recovery
    offer covers, newest first by updated_at (the
    attention order the journal uses). Records already
    covered by the approvals/questions offers are
    excluded, never double-offered; malformed entries and
    entries without an id are dropped, never offered.
    """
    offers = []
    for summary in task_summaries or []:
        if not is_recoverable_task(summary):
            continue
        title = (_as_str(summary.get("title", "")).strip()
                 or _as_str(summary.get("classification", "")).strip()
                 or _as_str(summary.get("task_id", "")).strip())
        offers.append({
            "kind": "task",
            "id": str(summary.get("task_id", "")),
            "title": title or "Untitled task",
            "line": recovery_offer_line("task", summary),
            "summary": dict(summary),
        })
    for summary in goal_summaries or []:
        if not is_recoverable_goal(summary):
            continue
        goal_id = (_as_str(summary.get("goal_id", "")).strip()
                   or _as_str(summary.get("task_id", "")).strip())
        title = (_as_str(summary.get("goal_text", "")).strip()
                 or _as_str(summary.get("title", "")).strip())
        offers.append({
            "kind": "goal",
            "id": goal_id,
            "title": title or "Untitled goal",
            "line": recovery_offer_line("goal", summary),
            "summary": dict(summary),
        })
    offers.sort(key=lambda o: str(
        (o.get("summary") or {}).get("updated_at", "")), reverse=True)
    return offers


def startup_recovery_check(ledger) -> list:
    """The startup recovery read: fresh offers, never auto-run.

    Reads the ledger's interrupted records (coordinator
    tasks + goals) and returns the recovery offers. A
    broken ledger returns [] — the offer is a courtesy;
    a broken ledger never breaks startup. This function
    reads ONLY: nothing here resumes, cancels, or writes
    anything, so calling it twice offers the same records
    twice (a dismissed record is offered again next
    startup until resumed or explicitly abandoned).
    """
    try:
        tasks = ledger.list_interrupted()
        goals = ledger.list_interrupted_goals()
    except Exception:  # noqa: BLE001 — a broken ledger
        return []  # offers nothing, never breaks startup.
    try:
        return interrupted_recoverable(tasks, goals)
    except Exception:  # noqa: BLE001 — a broken summary
        return []  # offers nothing, never breaks startup.


# -- dialog (GUI thread only for present; build is testable) -----------

def build_recovery_dialog(window, offer):
    """Build the recovery dialog (no exec — testable).

    ``offer`` is one ``interrupted_recoverable`` entry.
    Returns the dialog; the caller execs it (see
    :func:`present_recovery_dialog`) or drives its buttons
    directly in tests. The dialog carries
    ``_recovery_outcome`` ("resume" | "not_now" |
    "abandon" | None), ``_recovery_btn_resume`` (tasks
    only), ``_recovery_btn_not_now``,
    ``_recovery_btn_abandon`` and ``_recovery_timed_out``
    for tests.

    Behaviour: Resume continues from the next step —
    finished steps are never re-run — and runs nothing
    until pressed. Not now leaves the record exactly as
    it is (the next startup offers it again). Abandon
    ends the record for good (cancelled history, never
    offered again). The safe default + initial focus is
    Not now: resuming is an explicit click, never the
    default, and abandoning is never the default either.
    A goal offer has no Resume button (see the module
    docstring — D1 boundary) and says so in its about
    line. Closing the dialog decides nothing (Not now).
    Raises on a build failure (the caller treats that as
    "error", never as a decision).
    """
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    )

    from .design_system.dialogs import HudDialog
    from .style import TEXT_MUTED

    offer = offer if isinstance(offer, dict) else {}
    kind = _as_str(offer.get("kind", ""))
    line = _as_str(offer.get("line", "")).strip()
    title = _as_str(offer.get("title", "")).strip()

    dlg = HudDialog(window, title="VYUK — an interrupted task")
    layout = dlg.content_layout()

    headline = QLabel(line or "An interrupted task is waiting.")
    headline.setStyleSheet("font-size: 16px; font-weight: 600;")
    headline.setWordWrap(True)
    layout.addWidget(headline)

    if kind == "goal":
        about_text = (
            "This goal can be picked up where it stopped, but "
            "resuming a goal needs the goal loop, which this "
            "button can't reach yet — the session that started "
            "it resumes it. Not now leaves it exactly as it "
            "is; Abandon ends it for good.")
    else:
        about_text = (
            "Resume continues from the next step — finished "
            "steps are never re-run. Nothing runs until you "
            "press Resume. Not now leaves it exactly as it "
            "is; Abandon ends it for good.")
    about = QLabel(about_text)
    about.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
    about.setWordWrap(True)
    layout.addWidget(about)

    row = QHBoxLayout()
    row.addStretch(1)
    btn_resume = None
    if kind != "goal":
        btn_resume = QPushButton("Resume")
        btn_resume.clicked.connect(
            lambda: _finish(dlg, "resume"))
        row.addWidget(btn_resume)
    btn_not_now = QPushButton("Not now")
    btn_not_now.setDefault(True)
    btn_not_now.clicked.connect(
        lambda: _finish(dlg, "not_now"))
    row.addWidget(btn_not_now)
    btn_abandon = QPushButton("Abandon")
    btn_abandon.clicked.connect(
        lambda: _finish(dlg, "abandon"))
    row.addWidget(btn_abandon)
    layout.addLayout(row)
    btn_not_now.setFocus()

    dlg._recovery_outcome = None
    dlg._recovery_btn_resume = btn_resume
    dlg._recovery_btn_not_now = btn_not_now
    dlg._recovery_btn_abandon = btn_abandon
    dlg._recovery_timed_out = False
    return dlg


def _finish(dlg, outcome: str) -> None:
    """Record the outcome and close the dialog."""
    try:
        dlg._recovery_outcome = outcome
    except Exception:  # noqa: BLE001 — teardown never raises
        pass
    try:
        dlg.accept()
    except Exception:  # noqa: BLE001 — teardown never raises
        pass


def present_recovery_dialog(window, offer, *, timeout_s=None):
    """Show the recovery dialog for one offer.

    GUI thread only. Returns "resume" | "not_now" |
    "abandon" | "timeout" | "error". "resume" only for an
    explicit Resume click (tasks only — a goal offer has
    no Resume button); "abandon" only for an explicit
    Abandon click; "not_now" for the Not now button or a
    window close; "timeout" when the 60 s silence timer
    fires; "error" for any dialog failure. Anything but
    "resume"/"abandon" decides nothing — the record stays
    interrupted and the next startup offers it again.

    This function presents ONLY — it never resumes or
    abandons anything. The caller acts on the returned
    outcome (on a worker thread, via
    :func:`resume_recovery_offer` /
    :func:`abandon_recovery_offer`).
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    if timeout_s is None:
        try:
            from voice_assistant.core.gui_facade import confirm_gate
            timeout_s = float(confirm_gate.DEFAULT_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — one timeout source
            timeout_s = 60.0
    try:
        dlg = build_recovery_dialog(window, offer)
    except Exception:  # noqa: BLE001 — a broken popup
        return "error"  # decides nothing.
    timer = QTimer(dlg)
    timer.setSingleShot(True)

    def _on_timeout():
        # The timer firing means the human did not answer
        # at all — recorded as "timeout", never as a
        # decision, so the caller can tell it apart from an
        # explicit Not now (the record stays interrupted
        # either way).
        dlg._recovery_timed_out = True
        dlg.reject()

    timer.timeout.connect(_on_timeout)
    try:
        timer.start(int(float(timeout_s) * 1000))
    except Exception:  # noqa: BLE001 — a timer failure must
        pass  # not break the dialog itself.
    try:
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
    except Exception:  # noqa: BLE001 — a broken popup
        accepted = False  # decides nothing.
    finally:
        try:
            timer.stop()
        except Exception:  # noqa: BLE001 — teardown never raises.
            pass
    if getattr(dlg, "_recovery_timed_out", False):
        return "timeout"
    if not accepted:
        return "not_now"
    outcome = getattr(dlg, "_recovery_outcome", None)
    if outcome in ("resume", "abandon"):
        return outcome
    return "not_now"


# -- worker-safe actions (no Qt) ----------------------------------------

def resume_recovery_offer(offer, *, live_coordinator=None, ledger=None,
                          pick_action=None):
    """Resume one recovery offer, worker-safe (no Qt).

    Task — live first, mirroring resolve_pending_answer:
    when the live coordinator holds the paused task it is
    the truth (a live-held task in any other state means
    a stale offer, which resumes nothing); otherwise the
    rebuild path: a fresh coordinator bound to the ledger
    resumes the interrupted record
    (``resume_ledger_task`` — completed steps are never
    re-executed, the rebuild index points past them,
    gated steps re-gate). None only when nothing was
    found or resumable.

    Goal — the existing ``resume_goal`` path, with the
    picker supplied by the caller. pick_action is None
    (the GUI never passes one — see the module docstring,
    D1 boundary) then this returns None: a goal is not
    resumable from here, and no resume is invented. With
    a picker, the goal resumes with its history intact
    and a fresh budget window (B11).
    """
    offer = offer if isinstance(offer, dict) else {}
    kind = _as_str(offer.get("kind", ""))
    offer_id = str(offer.get("id", "") or "").strip()
    if not offer_id:
        return None
    if kind == "goal":
        if pick_action is None:
            return None
        if ledger is None:
            return None
        from voice_assistant.core.gui_facade import resume_goal
        try:
            return asyncio.run(
                resume_goal(offer_id, ledger=ledger,
                            pick_action=pick_action))
        except Exception:  # noqa: BLE001 — a failed resume
            return None  # resumes nothing.
    if live_coordinator is not None:
        try:
            held = live_coordinator.get_task(offer_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to rebuild over it.
        if held is not None:
            if getattr(held, "state", None) != "paused":
                return None
            try:
                return live_coordinator.resume_task(offer_id)
            except Exception:  # noqa: BLE001 — a failed resume
                return None  # resumes nothing.
    if ledger is None:
        return None
    record = ledger.get(offer_id)
    if not isinstance(record, dict):
        return None
    if str(record.get("state", "")) not in INTERRUPTED_STATES:
        return None
    from voice_assistant.core.gui_facade import TurnCoordinator
    try:
        coordinator = TurnCoordinator(ledger=ledger)
        return coordinator.resume_ledger_task(record, ledger=ledger)
    except Exception:  # noqa: BLE001 — a failed resume
        return None  # resumes nothing.


def abandon_recovery_offer(offer, *, live_coordinator=None, ledger=None):
    """Abandon one recovery offer, worker-safe (no Qt).

    Live first, mirroring cancel_pending_answer: when the
    live coordinator holds the task, ``cancel_task`` ends
    it (and writes the ledger state "cancelled"). Otherwise
    the ledger path: ``abandon_record`` marks the stored
    record cancelled without running anything. True when
    the record was abandoned; None only when nothing was
    found, the offer was stale, or the record had already
    moved on (terminal records are history, never
    abandoned twice).
    """
    offer = offer if isinstance(offer, dict) else {}
    offer_id = str(offer.get("id", "") or "").strip()
    if not offer_id:
        return None
    if _as_str(offer.get("kind", "")) != "goal" \
            and live_coordinator is not None:
        try:
            held = live_coordinator.get_task(offer_id)
        except Exception:  # noqa: BLE001 — a broken live lookup
            return None  # is not a licence to abandon over it.
        if held is not None:
            from .pending_approval import cancel_held_task

            if cancel_held_task(offer_id,
                              live_coordinator=live_coordinator):
                return True
            return None
    if ledger is None:
        return None
    try:
        if bool(ledger.abandon_record(offer_id)):
            return True
    except Exception:  # noqa: BLE001 — a failed abandon
        return None  # abandons nothing.
    return None


def review_recovery_queue(window, queue, *, live_coordinator=None,
                          ledger=None, refresh=None, poll_ms=500):
    """Offer each recovery record in turn, then refresh.

    GUI thread only for the dialog half: each queue head
    gets :func:`present_recovery_dialog` (modal, so the
    user answers before anything else runs), then its
    outcome lands on a worker thread: "resume" resumes
    via :func:`resume_recovery_offer` (which may run task
    steps and re-pop the funnel gate — the GUI thread is
    free throughout, so no deadlock), "abandon" abandons
    via :func:`abandon_recovery_offer` (the record ends
    as cancelled history). "not_now", "timeout" and
    "error" advance the queue with no action at all — a
    dismissal decides nothing, and the record stays
    interrupted for the next startup. A poll timer
    advances the queue only when the worker lands; when
    the queue empties, ``refresh`` (the Activity refresh)
    runs. A stale offer resumes or abandons nothing and
    the refresh simply shows the new state.
    ``window`` need only be a dialog parent.
    """
    import threading

    from PySide6.QtCore import QTimer

    if not queue:
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001 — refresh never
                pass  # breaks the review flow.
        return
    offer = queue[0]
    try:
        outcome = present_recovery_dialog(window, offer)
    except Exception:  # noqa: BLE001 — a broken Review dialog
        outcome = "error"  # decides nothing.
    done = threading.Event()

    def _work():
        try:
            if outcome == "resume":
                resume_recovery_offer(
                    offer,
                    live_coordinator=live_coordinator, ledger=ledger)
            elif outcome == "abandon":
                abandon_recovery_offer(
                    offer,
                    live_coordinator=live_coordinator, ledger=ledger)
        except Exception:  # noqa: BLE001 — the worker never
            pass  # breaks the GUI; the refresh shows truth.
        finally:
            done.set()

    threading.Thread(target=_work, daemon=True).start()
    timer = QTimer(window)
    timer.setSingleShot(False)

    def _tick():
        if done.is_set():
            try:
                timer.stop()
            except Exception:  # noqa: BLE001 — teardown
                pass  # never raises.
            review_recovery_queue(
                window, queue[1:], live_coordinator=live_coordinator,
                ledger=ledger, refresh=refresh, poll_ms=poll_ms)

    timer.timeout.connect(_tick)
    timer.start(int(poll_ms))
