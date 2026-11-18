"""Lesson planner — turn a topic into dependency-ordered bites (U2, tutor programme).

Loophole family: "a tutor with no curriculum" — every lesson improvised
in the moment; nothing orders the bites by dependency, nothing knows
what comes first, nothing re-orders from evidence, so the tutor can
teach a surface integral before the parametrisation it depends on.
The evidence (audit T1): B22 decomposes GOALS into tool steps
(``core/brain/task_decompose.py: build_decompose_prompt`` — "Break
this goal into sub-goals, and each sub-goal into a small list of
tool steps"; ``run_decomposed_goal``, task_coordinator_execution.py:964)
— never a TOPIC into teachable bites; the flashcard deck is an
alphabetically sorted pile (``_cards()``, skills_study_helpers.py:29)
and ``quiz_me`` picks one card at random (skills_study_drills.py:33-40).
No topic -> bites path exists anywhere in the tree.

ONE design: the planner turns a topic into dependency-ordered bites
(bite + check + gold + rubric) via the B22 decomposition machinery —
the plan brain (the companion-session pair, supplied here as an
injected ``ask_brain`` callable so this module stays stdlib-only
and testable without a live brain) decomposes the topic; the planner
validates the decomposition strictly, orders it by dependency
(topological, deterministic), re-orders the ready set
lowest-mastery-first by READING the U1 student model (read-only —
this module never writes mastery), skips mastered bites instead of
re-teaching them, and the lesson plan goes through the B23
approve-plan-once rules before it runs. Bites are executable data
(the Edukors pattern: a bite sequence with check nodes + reinforce
edges), not a free-form LLM plan. A lesson plan is a plan, and
plans are approved.

Ordering rule (deterministic): dependency first, mastery second.
Kahn's algorithm over the bites that still need teaching; among the
bites whose prerequisites are all satisfied (the ready set), the
lowest mastery probability goes first (the OATutor pattern from the
research — lowest-mastery-first selection), ties broken by the
brain's listed order. Same topic + same mastery state -> the same
plan, every time. No randomness in ordering (``quiz_me``'s
date-seeded randomness is the anti-pattern here).

Cold start: a topic with no mastery history at all still plans — the
first bite is marked diagnostic (U3 will run its check BEFORE
teaching it, to place the student), never a guess at his level.
Until ~10 interactions per skill the U1 cold-start priors stand
(p 0.30 for an unknown skill), so unknown skills order first among
the ready set — the planner teaches what it knows least about.

Approval (B23 shape): the plan carries its approval state
(``approved_indices`` + ``approval_outcome``); an unapproved plan
never runs (``start()`` refuses with a plain-words reason). The
typed-plan path presents the plan ONCE through the B23 dialog
(``plan_approval.request_plan_decision_ex`` on ``as_dialog_task()``)
and records the human's answer with ``approve()``. One approval,
once, GUI-only, human-only — never spoken, never self-approved.
A timeout / "ask each time" / no handler / error approves NOTHING
(the B23 silent-third-answer rule: a timeout is not an approval).
A second approval replaces the first. The approval covers exactly
the approved bites; an unapproved bite is never taught.

One deliberate boundary, recorded here so nobody "fixes" it:
``plan_approval.approve_plan`` is NOT used for bites. That store
records approvals for confirm-gated TOOL calls (its eligibility
check, ``is_plan_approvable``, is defined over tool calls that
would otherwise pop) — bites are not tool calls and never pop, so
the eligibility check correctly refuses them. Forcing bites
through it would either be refused or require weakening the check,
which would be a loophole. The planner reuses the B23 DIALOG and
the B23 RULES and records the approval ON THE PLAN, where the
lesson runner (U3) reads it. Likewise the bite shape has its own
strict parser — B22's ``parse_plan_text`` requires tool steps, and
a bite is not a tool step.

Rejected alternatives (recorded here, not just in the audit):
- A free-form LLM lesson outline (no check nodes, no gold, no
  rubric, nothing verifiable — the tutor would improvise and the
  check loop would have nothing to grade against).
- A fixed topic order with no mastery input (ignores what he
  already knows — re-teaches mastered bites, skips weak ones).
- Random card order (today's ``quiz_me`` — no pedagogy at all).
- Planning without approval (B23 exists for plans; a lesson plan
  that runs unapproved is a plan that bypasses the gate).
- A second planner for topics (one decomposition machinery — the
  B22 brain pair; a new brain is new capability, not wiring).
- Inventing bites when no brain is available (B22's rule: no
  brain -> fail honestly, never a guessed plan).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from types import SimpleNamespace

from .student_model import DEFAULT_PRIOR, StudentModel, norm_skill_id

# U2: the most bites one lesson may carry — aligned with B22's
# MAX_DECOMPOSED_STEPS (25): one lesson is one goal's worth of
# bites, not an unbounded list; a bigger topic re-plans instead
# of listing fifty micro-bites up front. A plan over this cap is
# malformed, never silently truncated (dropping bites would lose
# teaching the brain thought was needed).
MAX_BITES_PER_PLAN = 25

# The approval outcomes, exactly the B23 dialog's taxonomy
# (plan_approval.request_plan_decision_ex). Only "approved"
# with at least one index approves anything.
APPROVAL_OUTCOMES = ("approved", "ask_each_time", "timeout",
                     "no_handler", "error")

_PLAN_SHAPE = (
    '{"bites": [{"skill": "<skill id>", '
    '"bite": "<the teaching unit, plain words>", '
    '"check": "<its check question>", '
    '"gold": "<the gold answer>", '
    '"rubric": "<the grading rubric>", '
    '"requires": ["<skill id>", ...]}]}'
)


@dataclass(frozen=True)
class Bite:
    """One teachable unit, as executable data.

    ``skill`` is the U1 skill id this bite teaches. ``bite`` is
    the teaching unit itself; ``check`` its check question;
    ``gold`` the gold answer; ``rubric`` the grading rubric —
    no bite without a check, no check without a gold + rubric.
    ``requires`` lists the prerequisite skill ids (from this
    same plan, or already mastered). ``reinforce`` names the
    skill to fall back to when this bite's check fails (the
    Edukors reinforce edge; defaults to this bite's own skill
    — re-teach the bite). ``diagnostic`` is set by the planner
    for a cold-start first bite: U3 runs its check BEFORE
    teaching it, to place the student. ``source`` (U11, course
    grounding) is the bite's source pointer — a plain dict with
    file + lecture + page + passage — or None when the bite has
    no grounded source. The planner validates its SHAPE only;
    the semantic check (registered file, page exists, passage
    found) belongs to ``core/brain/course_grounding.py`` and is
    run before the bite is taught. A bite with no source is
    "not in your course files", never a fake citation.
    """
    skill: str
    bite: str
    check: str
    gold: str
    rubric: str
    requires: tuple = ()
    reinforce: str | None = None
    diagnostic: bool = False
    source: dict | None = None

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "bite": self.bite,
            "check": self.check,
            "gold": self.gold,
            "rubric": self.rubric,
            "requires": list(self.requires),
            "reinforce": self.reinforce,
            "diagnostic": self.diagnostic,
            "source": dict(self.source) if self.source else None,
        }


def _source_field(raw, pos):
    """An optional source pointer: shape only, never the semantic check.

    Returns (dict | None, error). Absent is None (the bite has no
    grounded source); present must be an object with file + lecture
    + page + passage of the right types. Whether the file is
    registered, the page exists, and the passage is found is
    ``course_grounding.verify_source``'s job, run before teaching.
    """
    source = raw.get("source")
    if source is None:
        return None, ""
    if not isinstance(source, dict):
        return None, f"bite {pos} 'source' is not an object"
    for key in ("file", "lecture", "page", "passage"):
        if key not in source:
            return None, f"bite {pos} 'source' has no {key!r}"
    if not isinstance(source["file"], str) or not source["file"].strip():
        return None, f"bite {pos} 'source' file is not usable text"
    if not isinstance(source["lecture"], str) \
            or not source["lecture"].strip():
        return None, f"bite {pos} 'source' lecture is not usable text"
    if not isinstance(source["page"], int) \
            or isinstance(source["page"], bool) or source["page"] < 1:
        return None, f"bite {pos} 'source' page is not a page number"
    if not isinstance(source["passage"], str) \
            or not source["passage"].strip():
        return None, f"bite {pos} 'source' passage is not usable text"
    return {"file": source["file"].strip(),
            "lecture": source["lecture"].strip(),
            "page": source["page"],
            "passage": source["passage"].strip()}, ""


def _text_field(raw, name, pos):
    """A required text field: a non-empty string, stripped."""
    if not isinstance(raw, str) or not raw.strip():
        return None, (f"bite {pos} has no usable {name!r} "
                      f"(every bite needs bite + check + gold + rubric)")
    return raw.strip(), ""


def validate_bite(raw, pos):
    """Validate one raw bite mapping. (Bite | None, error)."""
    if not isinstance(raw, dict):
        return None, f"bite {pos} is not an object"
    skill = norm_skill_id(raw.get("skill"))
    if skill is None:
        return None, f"bite {pos} has no usable 'skill'"
    fields = {}
    for name in ("bite", "check", "gold", "rubric"):
        value, error = _text_field(raw.get(name), name, pos)
        if error:
            return None, error
        fields[name] = value
    requires_raw = raw.get("requires", [])
    if requires_raw is None:
        requires_raw = []
    if not isinstance(requires_raw, list):
        return None, (f"bite {skill!r} 'requires' is not a list")
    requires = []
    for req in requires_raw:
        req_id = norm_skill_id(req)
        if req_id is None:
            return None, (f"bite {skill!r} has an unusable "
                          f"prerequisite {req!r}")
        if req_id not in requires:
            requires.append(req_id)
    reinforce = raw.get("reinforce")
    if reinforce is None:
        # No edge given: fall back to this bite itself (re-teach
        # the bite). Every bite always carries a concrete edge.
        reinforce = skill
    else:
        reinforce = norm_skill_id(reinforce)
        if reinforce is None:
            return None, (f"bite {skill!r} has an unusable "
                          f"'reinforce' skill")
    source, error = _source_field(raw, pos)
    if error:
        return None, error
    return Bite(skill=skill, bite=fields["bite"], check=fields["check"],
                gold=fields["gold"], rubric=fields["rubric"],
                requires=tuple(requires), reinforce=reinforce,
                source=source), ""


def parse_topic_plan_text(text):
    """Parse a brain reply into bites, or (None, error).

    Strict about the SHAPE, tolerant of surrounding prose (the
    B22 parser's own discipline — the JSON object is extracted
    from the first "{" to the last "}"; a reply with no JSON
    object at all is a failed decomposition). Returns
    ({"bites": [Bite, ...]}, "") on success. Every failure names
    itself — a malformed plan is a failed decomposition, never
    a guessed one. Reference checks (``requires`` naming a skill
    that is neither in this plan nor mastered) live in
    :func:`plan_topic`, which knows the student model — the
    parser checks shape only.
    """
    raw = str(text or "")
    if not raw.strip():
        return None, "the brain returned no plan text"
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
    except ValueError:
        return None, "the brain's reply contains no JSON object"
    try:
        data = json.loads(raw[start:end])
    except ValueError as exc:
        return None, f"the brain's plan is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, "the brain's plan is not a JSON object"
    bites_raw = data.get("bites")
    if not isinstance(bites_raw, list) or not bites_raw:
        return None, ("the brain's plan has no 'bites' list "
                      "(expected {\"bites\": [...]})")
    if len(bites_raw) > MAX_BITES_PER_PLAN:
        return None, (f"the brain's plan has {len(bites_raw)} bites — "
                      f"at most {MAX_BITES_PER_PLAN} per lesson "
                      f"(a bigger topic re-plans, it does not list "
                      f"them all up front)")
    bites = []
    seen = set()
    for pos, raw_bite in enumerate(bites_raw, start=1):
        bite, error = validate_bite(raw_bite, pos)
        if error:
            return None, error
        if bite.skill in seen:
            return None, (f"the brain's plan teaches skill "
                          f"{bite.skill!r} twice")
        seen.add(bite.skill)
        bites.append(bite)
    return {"bites": bites}, ""


def build_topic_prompt(topic: str) -> str:
    """The topic-decomposition prompt for the plan brain.

    Asks the brain to break the topic into teachable bites in
    the plan shape above. The topic text is quoted verbatim —
    never paraphrased by the caller (a paraphrase could change
    the topic — the B22 rule, applied to topics).
    """
    topic_text = str(topic or "").strip()
    return (
        "Break this topic into teachable bites, in dependency "
        "order — a bite never comes before its prerequisite.\n\n"
        "Rules:\n"
        "- Reply with ONLY JSON in exactly this shape, no prose, "
        "no markdown:\n"
        f"{_PLAN_SHAPE}\n"
        "- Every bite carries all five text fields: bite (the "
        "teaching unit, plain words), check (its check question), "
        "gold (the gold answer), rubric (the grading rubric). "
        "A bite without a check, or a check without a gold + "
        "rubric, is unusable.\n"
        "- \"requires\" lists the prerequisite skill ids, defined "
        "in this same plan — a bite's prerequisites must be "
        "bites in this plan, never skills from outside it.\n"
        "- Teach first, in small bites — one idea per bite, a few "
        "bites, not a dozen.\n"
        "- Keep the plan small: at most "
        f"{MAX_BITES_PER_PLAN} bites.\n\n"
        f"Topic: {topic_text}\n"
    )


def _order_bites(bites, model):
    """Dependency order, lowest-mastery-first in the ready set.

    Kahn's algorithm over the bites that still need teaching.
    ``requires`` pointing at a mastered (skipped) skill is
    already satisfied and carries no edge. Among the ready
    bites — prerequisites all placed — the lowest mastery
    probability goes first (unknown skills read at the U1
    cold-start prior, 0.30, so the planner teaches what it
    knows least about first); ties break by the brain's listed
    order, so the order is deterministic. Returns
    ([Bite, ...], "") or ([], cycle error naming the skills
    that could never become ready).
    """
    index = {bite.skill: pos for pos, bite in enumerate(bites)}
    remaining = {bite.skill: bite for bite in bites}
    placed = []
    while remaining:
        ready = []
        for skill, bite in remaining.items():
            pending = [req for req in bite.requires
                       if req in remaining]
            if not pending:
                ready.append(bite)
        if not ready:
            stuck = sorted(remaining)
            return [], (f"the plan's prerequisites form a cycle "
                        f"among {stuck} — no bite can come first")
        def _key(bite):
            record = model.get_skill(bite.skill)
            p = record["p"] if record is not None else DEFAULT_PRIOR
            return (p, index[bite.skill])
        ready.sort(key=_key)
        first = ready[0]
        placed.append(first)
        del remaining[first.skill]
    return placed, ""


class LessonPlan:
    """A planned lesson: dependency-ordered bites + approval state.

    Built only by :func:`plan_topic`. ``bites`` are in teaching
    order; ``skipped_mastered`` names the bites the student
    already mastered (skipped, never re-taught as a full bite).
    ``diagnostic`` is True when the first bite is a cold-start
    diagnostic (its check runs before the teaching). The plan
    carries its approval state: nothing runs until a human
    approves it once through the B23 dialog (see
    :meth:`approve` and :meth:`start`).
    """

    def __init__(self, plan_id, topic, bites, skipped_mastered,
                 diagnostic, planned_by=""):
        self.plan_id = plan_id
        self.topic = topic
        self.bites = list(bites)
        self.skipped_mastered = list(skipped_mastered)
        self.diagnostic = bool(diagnostic)
        self.planned_by = planned_by
        self.approved_indices = set()
        self.approval_outcome = None

    # -- approval (B23 shape) --------------------------------------
    def approve(self, indices, outcome="approved"):
        """Record a human's answer to the plan dialog once.

        Returns {"approved": [...], "refused": [{"index",
        "reason"}]}. Only outcome "approved" with at least one
        in-range index approves anything: a timeout, "ask each
        time", no handler, or an error approves NOTHING (the B23
        silent-third-answer rule), and "approved" with no
        indices reads as "ask each time", never as an approval
        (the request_plan_decision_ex rule). A second approval
        replaces the first. Never raises.
        """
        refused = []
        try:
            indices = list(indices or [])
        except Exception:  # noqa: BLE001 — no indices to approve
            indices = []
        if outcome not in APPROVAL_OUTCOMES:
            self.approved_indices = set()
            self.approval_outcome = None
            return {"approved": [],
                    "refused": [{"index": i,
                                 "reason": "unknown approval outcome — "
                                           "nothing is approved"}
                                for i in indices]}
        if outcome != "approved" or not indices:
            self.approved_indices = set()
            self.approval_outcome = ("ask_each_time"
                                     if outcome == "approved"
                                     else outcome)
            reason = (f"approval outcome was "
                      f"{self.approval_outcome} — nothing is approved")
            return {"approved": [],
                    "refused": [{"index": i, "reason": reason}
                                for i in indices]}
        approved = []
        for index in indices:
            if (not isinstance(index, int)
                    or isinstance(index, bool)
                    or index < 0 or index >= len(self.bites)):
                refused.append({"index": index,
                                "reason": "not a bite of this lesson"})
                continue
            approved.append(index)
        self.approved_indices = set(approved)
        self.approval_outcome = "approved"
        return {"approved": approved, "refused": refused}

    def approved_bites(self):
        """The approved bites, in teaching order."""
        return [self.bites[i] for i in sorted(self.approved_indices)]

    def start(self):
        """The run guard: (approved bites, "") or ([], reason).

        An unapproved plan never runs — the reason names the
        approval outcome (never approved, timed out, asked each
        time, no dialog available, error). An approved plan
        runs exactly its approved bites. A plan with nothing
        left to teach (every bite already mastered) returns
        ([], "") without needing approval — zero bites means
        zero teaching, and there is nothing to approve.
        """
        if not self.bites:
            return [], ""
        if self.approval_outcome != "approved":
            outcome = self.approval_outcome
            if outcome is None:
                reason = ("this lesson plan was never approved — "
                          "it cannot run")
            elif outcome == "timeout":
                reason = ("the approval timed out — nothing was "
                          "approved, so this lesson plan cannot run")
            elif outcome == "ask_each_time":
                reason = ("the plan was not approved — it was left "
                          "to ask each time, so this lesson plan "
                          "cannot run")
            elif outcome == "no_handler":
                reason = ("no approval dialog was available — "
                          "nothing was approved, so this lesson plan "
                          "cannot run")
            else:
                reason = ("the approval failed — nothing was "
                          "approved, so this lesson plan cannot run")
            return [], reason
        return self.approved_bites(), ""

    # -- presentation --------------------------------------------------
    def summary(self) -> str:
        """The plan in plain words, for the typed-plan path."""
        lines = [f"Lesson: {self.topic}",
                 f"{len(self.bites)} bite(s)"]
        if self.skipped_mastered:
            lines.append(f"{len(self.skipped_mastered)} skipped as "
                         f"already mastered: "
                         f"{', '.join(self.skipped_mastered)}")
        if self.diagnostic and self.bites:
            lines.append("First bite is a diagnostic — its check "
                         "runs before the teaching, to place the "
                         "student.")
        if self.planned_by:
            lines.append(f"Planned by: {self.planned_by}")
        if self.approval_outcome is None:
            lines.append("Not yet approved.")
        elif self.approval_outcome == "approved":
            lines.append(f"Approved: {len(self.approved_indices)} "
                         f"bite(s).")
        else:
            lines.append(f"Approval outcome: {self.approval_outcome} "
                         f"— nothing approved.")
        return " ".join(lines)

    def as_dialog_task(self):
        """A lightweight task-like for the B23 plan dialog.

        The typed-plan path presents this to
        ``plan_approval.request_plan_decision_ex`` — one dialog,
        once, GUI-only — and records the human's answer with
        :meth:`approve`. Each step is one bite (tool
        "lesson_bite", label the skill); bites are not tool
        calls, so the dialog reads the plan's own bite list,
        not ``preview``'s tool-centric approvable list (preview
        correctly classes bites as not-tool-approvable — they
        never pop a confirmation).
        """
        steps = []
        for pos, bite in enumerate(self.bites, start=1):
            steps.append(SimpleNamespace(
                tool="lesson_bite",
                args={"skill": bite.skill, "bite": bite.bite},
                label=f"{pos}. {bite.skill}"))
        return SimpleNamespace(id=self.plan_id,
                              title=f"Lesson: {self.topic}",
                              steps=steps)

    def to_dict(self) -> dict:
        """The plan as plain data (deterministic — same topic +
        same mastery state -> the same dict)."""
        return {
            "plan_id": self.plan_id,
            "topic": self.topic,
            "planned_by": self.planned_by,
            "bites": [bite.to_dict() for bite in self.bites],
            "skipped_mastered": list(self.skipped_mastered),
            "diagnostic": self.diagnostic,
            "approved_indices": sorted(self.approved_indices),
            "approval_outcome": self.approval_outcome,
        }


def plan_topic(topic, *, student_model=None, ask_brain,
               brain_name=""):
    """Plan a topic into dependency-ordered bites.

    (LessonPlan | None, error). ``ask_brain`` is the plan-brain
    call — a callable taking the topic prompt and returning the
    brain's reply text. In production the typed-plan path binds
    the B22 companion pair (the coordinator's ``_ask_plan_brain``
    — ``lambda prompt: coordinator._ask_plan_brain(task,
    prompt)[0]``, with ``brain_name`` the leg that answered:
    "cloud" or "local"). The planner never imports the
    coordinator and never builds a brain of its own.

    ``student_model`` is the U1 model (read-only — this function
    never writes mastery; a test may pass None for an empty
    model, which reads as a full cold start). Every failure
    names itself: no topic, no brain, a brain that returns
    nothing, a malformed plan, an unknown prerequisite, a
    dependency cycle — a failed plan is a failed plan, never a
    guessed one, never silently re-ordered.
    """
    topic_text = str(topic or "").strip()
    if not topic_text:
        return None, "no topic given — nothing to plan"
    if not callable(ask_brain):
        return None, ("the planner was given no brain to ask — "
                      "a topic cannot be planned without one")
    model = student_model if student_model is not None \
        else StudentModel()
    try:
        reply = ask_brain(build_topic_prompt(topic_text))
    except Exception as exc:  # noqa: BLE001 — an erroring brain
        # is unavailable, never a fake plan (the B22 rule).
        return None, f"the plan brain is unavailable ({exc})"
    if not isinstance(reply, str) or not reply.strip():
        return None, "the plan brain returned no plan text"
    parsed, error = parse_topic_plan_text(reply)
    if error:
        return None, error
    bites = parsed["bites"]
    known = {bite.skill for bite in bites}
    # A prerequisite must be a bite in this plan or a skill the
    # student already mastered. Anything else is unknown — the
    # brain should have included it as a bite. Fail honestly,
    # never plan around a missing prerequisite silently.
    for bite in bites:
        for req in bite.requires:
            if req not in known and not model.is_mastered(req):
                return None, (f"bite {bite.skill!r} requires "
                              f"{req!r}, which is neither a bite "
                              f"in this plan nor a mastered skill")
        if bite.reinforce is not None \
                and bite.reinforce not in known \
                and not model.is_mastered(bite.reinforce):
            return None, (f"bite {bite.skill!r} reinforces "
                          f"{bite.reinforce!r}, which is neither "
                          f"a bite in this plan nor a mastered skill")
    skipped = [bite.skill for bite in bites
               if model.is_mastered(bite.skill)]
    teach = [bite for bite in bites
             if bite.skill not in set(skipped)]
    ordered, error = _order_bites(teach, model)
    if error:
        return None, error
    # Cold start: no mastery history at all for the bites still
    # to teach — the first bite is a diagnostic, never a guess
    # at his level.
    diagnostic = False
    if ordered:
        has_history = any(
            (model.get_skill(bite.skill) or {}).get("evidence", 0) > 0
            for bite in ordered)
        if not has_history:
            diagnostic = True
            ordered = [replace(ordered[0], diagnostic=True)] \
                + ordered[1:]
    digest = hashlib.sha256(
        ("\n".join([topic_text] + [bite.skill for bite in ordered])
         ).encode("utf-8")).hexdigest()[:16]
    return LessonPlan(plan_id=f"lesson-{digest}", topic=topic_text,
                      bites=ordered, skipped_mastered=skipped,
                      diagnostic=diagnostic,
                      planned_by=str(brain_name or "")), ""
