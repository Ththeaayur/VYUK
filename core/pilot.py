"""Workshop, student, and dev PILOT slices, Wave 9 Build 8
(P15 / P16 / P17) — descriptors and records only.

Per build_flow_v6.md: "P15 workshop / P16 student / P17
dev PILOT slices — PILOT, Wave 9." PILOT here means
descriptor and record forms only: nothing is executed,
nothing is read live, and no claim goes beyond what the
caller supplied. Anything needing execution, a live read,
hardware, a paid service, or licence-cleared third-party
code is NOT built here — it is named below, not dropped
and never built on a guess.

WHAT THIS MODULE IS
-------------------
Three descriptor families, all caller-supplied, all
records only:
* WORKSHOP. A project-bench descriptor: a
  caller-supplied project name plus bench notes in, a
  plain-language bench record out (P15-7 lab notebook,
  record form only — the notes are the caller's, nothing
  is probed, photographed, or measured). A build-step
  descriptor: caller-supplied step labels in, a step
  record out, executed False, never run (P15-10
  blueprint mode, plan-record form only — the diagram
  itself is not drawn or exported here; P16-10 worked
  steps, caller-supplied steps plus an assumptions note,
  never generated steps).
* STUDENT. A study-session descriptor: a
  caller-supplied subject plus materials in, a session
  record out, building on but not duplicating the
  shipped Study tools — every material carries its
  source as supplied, and a material without a source,
  or flagged protected, refuses the whole session
  (P16-6 quiet question bank, caller-supplied questions
  riding in the session record — marking during a
  lecture and drafting the email to the teacher are NOT
  built; P16-9 practice exam mode is NOT rebuilt, it
  overlaps the shipped Study practice tool, and the
  session's question list is the only new form here).
  A revision-plan descriptor: caller-supplied topics in
  order in, a plan record out (P16-2 study plan that
  bends, plan-record form only — day-by-day scheduling
  and reshaping on a missed day are NOT built; P16-12
  concept maps, topics record only — the links are not
  drawn here). A revision plan schedules nothing and
  sets no reminder: it is a plan, not a schedule.
* DEV. A dev-workflow descriptor: a caller-supplied
  repo label plus workflow step labels in, a workflow
  record out, executed False, deployed False, signed
  False, never run, never deployed (P17-1 terminal
  dry-run, plan-record form only — the terminal command
  is NOT built; P17-4 policy as code, rule labels
  riding as workflow steps — replaying a rule against
  past confirmations and diffing a changed rule are NOT
  built). A code-review-note descriptor:
  caller-supplied file:line cites plus notes in, a
  review record out, never a code change (P17-11 stack
  trace to cause, cite-and-note form only — the cause
  is never stated, nothing is read from a terminal or
  IDE; P17-12 pre-push guardian, check notes only —
  the checks are never run; P17-14 diff storyteller,
  caller-supplied summary notes plus cites — the
  summary is never generated, nothing is approved or
  merged here).
* A re-validation rule for recording a pilot
  descriptor: the descriptor is re-built from its own
  fields and must match exactly, or it is refused,
  never written on trust.

WHAT THIS MODULE IS NOT — HARD REFUSALS
---------------------------------------
Any call carrying a refused kind of input refuses WHOLE,
never with a partial answer:
* any execute, run, deploy, install, promote, push,
  merge, sign, or apply action — an action parameter
  other than "describe", or a step label shaped as one
  of those actions, refuses the whole call;
* any live file, repo, calendar, terminal, IDE, or
  game read — a field claiming a live read refuses
  whole, and every output carries read_live False;
* any protected or copyrighted material supplied
  without provenance — a study material without a
  source, or flagged protected, refuses the whole
  session (the shipped Study rule: provenance on every
  problem and material);
* any claim a step ran, a build succeeded, a test
  passed, a deployment happened, a push or merge
  happened, or a release was signed — such a claim
  refuses the whole call, and the module's own outputs
  always carry executed False, deployed False, and
  signed False;
* any schedule or reminder — a revision plan is a
  plan, not a schedule, and a "schedule" or "remind"
  action refuses whole.
The module imports no subprocess, eval, exec, network,
or file-reading code — proven by the AST test in
tests/test_pilot_w9.py. It runs nothing, reads nothing
live, deploys nothing, and signs nothing: there is
nothing here to do any of that with, by construction.
Descriptors only, records only.

P15 / P16 / P17 SLICES NOT BUILT HERE, NAMED NOT DROPPED
--------------------------------------------------------
* P15-1 voice-driven 3D workbench — not built; it
  needs a scripted CAD tool with its licence checked
  first, plus model generation.
* P15-2 will it hold — not built; load simulation is
  a safety-sizing claim, and this module never claims
  a part is safe or will hold.
* P15-3 print queue master — not built; slicing, a
  print start, and a printer camera are actions and
  capture, not descriptors.
* P15-4 bench camera — not built; camera capture.
* P15-5 circuit buddy — not built; circuit simulation
  plus sandboxed compile plus upload actions.
* P15-6 instrument whisperer — not built; live
  instrument reads.
* P15-8 systems check, for real — not built; live
  probes (drive health, temperatures, battery, memory,
  network).
* P15-9 predictive maintenance — not built; live
  drive-health reads, and diagnosis-adjacent.
* P15-11 paper to prototype — not built; it reads a
  paper and writes and runs code.
* P15-12 the crew — not built; named helpers as
  parallel goals need goal-loop wiring, not a
  descriptor.
* P15-13 heads-up display on glasses — not built;
  hardware he has not chosen.
* P15-14 hand-tracked 3D manipulation — not built;
  camera plus gesture capture.
* P15-15 spatial audio — not built; audio rendering.
* P15-16 robot arm helper — not built; hardware
  movement needs Tier 2 plus a physical stop
  independent of software, and a bench first.
* P15-17 energy dashboard — not built; live power
  reads, and control only through official local
  integrations.
* P15-18 digital twin of the workshop — not built;
  it needs live or seen updates plus location
  freshness claims.
* P16-1 deadline radar — not built; it reads a
  syllabus, a calendar, and a course portal.
* P16-3 reading buddy — not built; it ingests a PDF
  or chapter and generates a summary.
* P16-4 smart lecture notes — not built; capture and
  transcription, consent-gated with an indicator.
* P16-5 board to notes — not built; photo plus OCR.
* P16-7 explain it back — not built; an interactive
  dialogue loop, not a descriptor.
* P16-8 spaced flashcards — not built here; it
  overlaps the shipped Study edition's FSRS scheduling
  and is not duplicated.
* P16-11 code tutor — not built; it reads errors and
  runs code in a sandbox.
* P16-13 source finder with receipts — not built;
  live search plus citation verification.
* P16-14 citation and style helper — not built; it
  checks each reference against the real record.
* P16-15 draft feedback, not a ghostwriter — not
  built; it reads a draft and generates feedback.
* P16-16 lab report helper — not built; it plots and
  fits caller data and generates report text.
* P16-17 focus mode — not built; silencing
  notifications and blocking sites are system actions.
* P16-18 budget buddy — not built here; spending
  tracking needs a data pipeline the finance tools do
  not supply as a descriptor.
* P16-19 group project coordinator — not built; it
  drafts reminders and merges shared notes, and it
  must not message a group without confirmation.
* P16-20 wellbeing check — not built here; the WELL
  build (Wave 9 Build 1) covers wellbeing descriptors,
  human-first, with no diagnosis.
* P16-21 low-data and offline mode — not built; a
  product mode, not a descriptor.
* P16-22 runs on modest laptops — not built; the RAM
  tiers rule is deferred.
* P16-23 accessible by design — not built here; the
  ACCESS build (Wave 9 Build 5) and the shipped
  Accessibility edition cover it.
* P16-24 your languages — not built here; language
  tiers carry the R5-E licence and bench gates.
* P17-2 speaks MCP both ways — not built; server and
  client protocol wiring.
* P17-3 plugin SDK with a gate simulator — not
  built; an SDK plus a simulator, beyond the harness
  that already governs extensions.
* P17-5 OpenAI-compatible endpoint swap — not built;
  model endpoint switching.
* P17-6 traces for every action — not built; real
  action tracing needs goal-loop wiring, not a
  descriptor.
* P17-7 deterministic replay — not built; it needs
  stored model outputs plus a fake clock, and lives
  in Wave 11.
* P17-8 cost and token meter — not built; live spend
  measurement.
* P17-9 "what left this PC" — not built; a real
  egress ledger needs telemetry wiring, and telemetry
  is OFF (Q3).
* P17-13 voice for the boring parts — not built;
  branch, stash, and test actions are git actions.
* P17-15 standing watches for builds — not built; a
  scheduled watch plus a notify daemon.
* P17-16 rubber duck mode — not built; a dialogue
  loop, not a descriptor.
* P17-17 sandboxed code runner — not built here;
  running code is execution, even in a sandbox.
* P17-18 skill review in plain words — not built;
  it reads a third-party manifest and renders a
  review, beyond the harness's own gate.
* P17-19 reproducible, signed releases — not built
  here; Wave 10 structure only, and signing is
  deferred on $0.
* P17-20 open numbers — not built; publishing bench
  numbers needs a bench-evidence pipeline.
* P17-21 pair-program on a second screen — not built;
  live test following plus patch proposal.
* P17-22 dream mode for tests — not built; overnight
  sandbox replay, Wave 11.
* P17-23 voice-driven dashboards — not built; live
  queries plus charting against a connected source.

FAIL-CLOSED, WHOLE-CALL
-----------------------
A refused action, a banned phrase, a live-read claim,
a success or deployment or signing claim, a protected
material without provenance, a missing field, or an
oversize field refuses the whole call. Nothing is
relabeled, executed, deployed, signed, or quietly
scheduled: a plan Nova did not run is worse than no
plan record.

LICENCE / DEPENDENCIES
----------------------
Own code, standard library only — in fact, no imports
beyond ``__future__`` annotations. No new dependency,
no telemetry write, no database access, no file read,
no network access. Nothing here reads or writes the
install tree's ``data/`` directory. No third-party
tool, model, or dataset is bundled, vendored, linked,
or shipped by this module.
"""

from __future__ import annotations

__all__ = [
    "MAX_NAME_CHARS",
    "MAX_NOTES_CHARS",
    "MAX_STEPS",
    "MAX_STEP_CHARS",
    "MAX_MATERIALS",
    "MAX_TOPICS",
    "MAX_CITES",
    "ACTION_WORDS",
    "CLAIM_PHRASES",
    "build_project_bench",
    "build_build_steps",
    "build_study_session",
    "build_revision_plan",
    "build_dev_workflow",
    "build_review_note",
    "validate_recordable_pilot",
]

#: Caller-supplied bounds. A longer field refuses whole,
#: never truncated into a different pilot record.
MAX_NAME_CHARS = 200
MAX_NOTES_CHARS = 2000
MAX_STEPS = 50
MAX_STEP_CHARS = 200
MAX_MATERIALS = 50
MAX_TOPICS = 100
MAX_CITES = 100

#: Whole words that make a step label an action rather
#: than a description. A step carrying one refuses the
#: whole descriptor: pilot records describe, never do.
ACTION_WORDS = frozenset({
    "execute", "run", "deploy", "install", "promote",
    "push", "merge", "sign", "apply", "compile",
    "upload", "download", "schedule", "remind",
})

#: Phrases claiming something already happened. A note,
#: step, or topic carrying one refuses the whole call:
#: this module's outputs never claim a run, a pass, a
#: deployment, a push, a merge, or a signature.
CLAIM_PHRASES = (
    "build succeeded", "tests passed", "test passed",
    "was executed", "ran successfully", "deployed",
    "already pushed", "already merged", "was signed",
    "signed release", "live read", "read live",
    "read from the repo", "read the file",
    "fetched live", "measured live",
)


def _tokens_in(text):
    """Lower-cased, word-ish tokens of a free-text input,
    for refusal scanning."""
    lowered = str(text).lower()
    current = []
    for char in lowered:
        if char.isalpha():
            current.append(char)
        else:
            if current:
                yield "".join(current)
                current = []
    if current:
        yield "".join(current)


def _scan_claims(field, value):
    lowered = str(value).lower()
    for phrase in CLAIM_PHRASES:
        if phrase in lowered:
            raise ValueError(
                f"{field} claims something already happened "
                f"({phrase!r}): pilot records describe only, "
                f"nothing ran, passed, deployed, or signed "
                f"here")


def _check_action(action):
    if action is None:
        return
    if not isinstance(action, str):
        raise ValueError("action must be a string")
    cleaned = action.strip().lower()
    if cleaned != "describe":
        raise ValueError(
            f"action {cleaned!r} refused: only 'describe' "
            f"is performed here")


def _check_text(field, value, maximum, required=True):
    if value is None:
        if required:
            raise ValueError(f"{field} must not be empty")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} chars")
    stripped = value.strip()
    if required and not stripped:
        raise ValueError(f"{field} must not be empty")
    _scan_claims(field, stripped)
    return stripped


def _check_steps(field, steps):
    if not isinstance(steps, (list, tuple)):
        raise ValueError(f"{field} must be a list of step labels")
    if not steps:
        raise ValueError(f"{field} must not be empty")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"{field} exceeds {MAX_STEPS} entries")
    cleaned_steps = []
    seen = set()
    duplicates = []
    for step in steps:
        text = _check_text("step", step, MAX_STEP_CHARS)
        for token in _tokens_in(text):
            if token in ACTION_WORDS:
                raise ValueError(
                    f"step {text!r} is shaped as an action "
                    f"({token!r}): pilot records describe, "
                    f"never do")
        if text in seen:
            duplicates.append(text)
            continue
        seen.add(text)
        cleaned_steps.append(text)
    return cleaned_steps, duplicates


def build_project_bench(project_name, bench_notes=None,
                        action="describe"):
    """A project-bench descriptor from caller-supplied
    fields only (P15-7 lab notebook, record form).

    The project name and bench notes are as supplied,
    never probed, photographed, or measured. Returns
    the descriptor as data, with read_live False and a
    note saying so."""
    _check_action(action)
    name = _check_text("project_name", project_name,
                       MAX_NAME_CHARS)
    notes = _check_text("bench_notes", bench_notes,
                        MAX_NOTES_CHARS, required=False)
    return {
        "kind": "PROJECT_BENCH",
        "project_name": name,
        "bench_notes": notes,
        "read_live": False,
        "measured": False,
        "note": ("A bench record only, from the project name "
                 "and notes you supplied. Nothing was probed, "
                 "photographed, or measured — this module "
                 "touches no bench, no file, and no device."),
    }


def build_build_steps(steps, assumptions=None, action="describe"):
    """A build-step descriptor from caller-supplied step
    labels only (P15-10 blueprint plan record; P16-10
    worked steps with an assumptions note).

    Steps are labels, in the order given, duplicates
    kept once and reported. An action-shaped step
    refuses the whole descriptor. The steps are never
    run: describing steps does not perform them."""
    _check_action(action)
    cleaned_steps, duplicates = _check_steps("steps", steps)
    note_text = _check_text("assumptions", assumptions,
                            MAX_NOTES_CHARS, required=False)
    return {
        "kind": "BUILD_STEPS",
        "steps": cleaned_steps,
        "assumptions": note_text,
        "duplicates_removed": duplicates,
        "executed": False,
        "read_live": False,
        "note": ("A step record only, from the steps you "
                 "supplied. Nothing was run, no diagram was "
                 "drawn or exported, and no step was "
                 "generated — the steps are yours."),
    }


def build_study_session(subject, materials, questions=None,
                        action="describe"):
    """A study-session descriptor from caller-supplied
    fields only (P16-6 quiet question bank, record form).

    Every material is an object with a "title" and a
    "source" as supplied: provenance on every material.
    A material without a source, or flagged protected,
    refuses the whole session — protected material
    without provenance is never recorded. Questions are
    caller-supplied question strings, in order. The
    session generates nothing: no summary, no question,
    and no answer is produced here."""
    _check_action(action)
    subject_text = _check_text("subject", subject,
                               MAX_NAME_CHARS)
    if not isinstance(materials, (list, tuple)):
        raise ValueError("materials must be a list of objects")
    if not materials:
        raise ValueError("materials must not be empty")
    if len(materials) > MAX_MATERIALS:
        raise ValueError(
            f"materials exceeds {MAX_MATERIALS} entries")
    cleaned_materials = []
    for material in materials:
        if not isinstance(material, dict):
            raise ValueError(
                "each material must be an object with "
                "'title' and 'source'")
        unexpected = sorted(set(material)
                            - {"title", "source", "protected"})
        if unexpected:
            raise ValueError(
                f"material carries unexpected fields "
                f"{unexpected}: a material that does not "
                f"re-build exactly is refused")
        if material.get("protected") is True:
            raise ValueError(
                "a material flagged protected refuses the "
                "whole session: protected material without "
                "provenance is never recorded here")
        title = _check_text("material title",
                            material.get("title"),
                            MAX_NAME_CHARS)
        source = _check_text("material source",
                             material.get("source"),
                             MAX_NAME_CHARS)
        cleaned_materials.append({"title": title,
                                  "source": source})
    cleaned_questions = []
    if questions is not None:
        if not isinstance(questions, (list, tuple)):
            raise ValueError("questions must be a list of strings")
        if len(questions) > MAX_MATERIALS:
            raise ValueError(
                f"questions exceeds {MAX_MATERIALS} entries")
        for question in questions:
            cleaned_questions.append(
                _check_text("question", question,
                            MAX_STEP_CHARS))
    return {
        "kind": "STUDY_SESSION",
        "subject": subject_text,
        "materials": cleaned_materials,
        "questions": cleaned_questions,
        "generated": False,
        "read_live": False,
        "note": ("A session record only, from the subject, "
                 "materials, and questions you supplied. "
                 "Every material carries its source as you "
                 "gave it; nothing was summarised, no "
                 "question or answer was generated, and "
                 "nothing was read from a file or portal."),
    }


def build_revision_plan(topics, action="describe"):
    """A revision-plan descriptor from caller-supplied
    topics in order (P16-2 study plan that bends, plan
    record only; P16-12 concept maps, topics record
    only).

    Topics are labels, in the order given, duplicates
    kept once and reported. A plan schedules nothing
    and sets no reminder: day-by-day scheduling and
    reshaping on a missed day are NOT built here."""
    _check_action(action)
    if not isinstance(topics, (list, tuple)):
        raise ValueError("topics must be a list of topic labels")
    if not topics:
        raise ValueError("topics must not be empty")
    if len(topics) > MAX_TOPICS:
        raise ValueError(f"topics exceeds {MAX_TOPICS} entries")
    cleaned_topics = []
    seen = set()
    duplicates = []
    for topic in topics:
        text = _check_text("topic", topic, MAX_STEP_CHARS)
        if text in seen:
            duplicates.append(text)
            continue
        seen.add(text)
        cleaned_topics.append(text)
    return {
        "kind": "REVISION_PLAN",
        "topics": cleaned_topics,
        "duplicates_removed": duplicates,
        "scheduled": False,
        "reminder_set": False,
        "note": ("A plan record only, from the topics you "
                 "supplied, in the order you gave them. "
                 "Nothing is scheduled, no reminder is set, "
                 "and no day-by-day plan is generated — "
                 "this is the plan you described."),
    }


def build_dev_workflow(repo_label, steps, action="describe"):
    """A dev-workflow descriptor from caller-supplied
    fields only (P17-1 terminal dry-run, plan record;
    P17-4 policy as code, rule labels as steps).

    The repo label is a name the caller gave it, never
    read from a repo. Steps are labels, in the order
    given, duplicates kept once and reported. An
    action-shaped step refuses the whole workflow. The
    workflow is never run, never deployed, and never
    signed: describing steps does not perform them."""
    _check_action(action)
    label = _check_text("repo_label", repo_label,
                        MAX_NAME_CHARS)
    cleaned_steps, duplicates = _check_steps("steps", steps)
    return {
        "kind": "DEV_WORKFLOW",
        "repo_label": label,
        "steps": cleaned_steps,
        "duplicates_removed": duplicates,
        "executed": False,
        "deployed": False,
        "signed": False,
        "read_live": False,
        "note": ("A workflow record only, from the repo label "
                 "and steps you supplied. Nothing was run, "
                 "no terminal command was built, nothing was "
                 "deployed, and nothing was signed — the "
                 "repo was never read."),
    }


def build_review_note(cites, notes, action="describe"):
    """A code-review-note descriptor from caller-supplied
    file:line cites plus notes (P17-11 stack trace to
    cause, cite-and-note form; P17-12 pre-push
    guardian, check notes only; P17-14 diff storyteller,
    caller-supplied summary notes plus cites).

    Each cite is exactly "file:line" with a numeric
    line. The cause is never stated, the checks are
    never run, and the summary is never generated:
    the notes are the caller's, and nothing is approved
    or merged here."""
    _check_action(action)
    if not isinstance(cites, (list, tuple)):
        raise ValueError("cites must be a list of 'file:line' cites")
    if not cites:
        raise ValueError("cites must not be empty")
    if len(cites) > MAX_CITES:
        raise ValueError(f"cites exceeds {MAX_CITES} entries")
    cleaned_cites = []
    for cite in cites:
        if not isinstance(cite, str) or ":" not in cite:
            raise ValueError(
                f"cite {cite!r} must be 'file:line'")
        path, _, line = cite.rpartition(":")
        if not path.strip() or not line.strip().isdigit():
            raise ValueError(
                f"cite {cite!r} must be 'file:line' with a "
                f"numeric line")
        cleaned_cites.append(f"{path.strip()}:{line.strip()}")
    note_text = _check_text("notes", notes, MAX_NOTES_CHARS)
    return {
        "kind": "REVIEW_NOTE",
        "cites": cleaned_cites,
        "notes": note_text,
        "approved": False,
        "merged": False,
        "read_live": False,
        "note": ("A review record only, from the cites and "
                 "notes you supplied. No cause was stated, "
                 "no check was run, no summary was "
                 "generated, and nothing was approved or "
                 "merged — the notes are yours."),
    }


def validate_recordable_pilot(kind, fields):
    """Re-validate a pilot descriptor a caller wants
    recorded, by re-running the builder that made it and
    requiring the core fields to match exactly. A record
    that does not re-build is refused, never written on
    trust. Returns the rebuilt descriptor dict, or raises
    ValueError."""
    if not isinstance(kind, str) or not isinstance(fields, dict):
        raise ValueError("kind must be a string and fields an object")
    kind = kind.strip()
    allowed_fields = {
        "bench": {"project_name", "bench_notes"},
        "build-steps": {"steps", "assumptions"},
        "study-session": {"subject", "materials", "questions"},
        "revision-plan": {"topics"},
        "dev-workflow": {"repo_label", "steps"},
        "review-note": {"cites", "notes"},
    }
    if kind not in allowed_fields:
        raise ValueError(f"unknown recordable pilot kind {kind!r}")
    unexpected = sorted(set(fields) - allowed_fields[kind])
    if unexpected:
        raise ValueError(
            f"pilot descriptor carries unexpected fields "
            f"{unexpected}: a record that does not re-build "
            f"exactly is refused, never written on trust")
    if kind == "bench":
        rebuilt = build_project_bench(
            fields.get("project_name"), fields.get("bench_notes"))
        if fields.get("project_name", "").strip() \
                != rebuilt["project_name"] \
                or (fields.get("bench_notes") or "").strip() \
                != rebuilt["bench_notes"]:
            raise ValueError("project bench fields do not re-build")
        return rebuilt
    if kind == "build-steps":
        rebuilt = build_build_steps(fields.get("steps"),
                                    fields.get("assumptions"))
        if _deduped(fields.get("steps")) != rebuilt["steps"] \
                or (fields.get("assumptions") or "").strip() \
                != rebuilt["assumptions"]:
            raise ValueError("build steps fields do not re-build")
        return rebuilt
    if kind == "study-session":
        rebuilt = build_study_session(
            fields.get("subject"), fields.get("materials"),
            fields.get("questions"))
        if fields.get("subject", "").strip() != rebuilt["subject"]:
            raise ValueError("study session subject does not "
                             "re-build")
        supplied = fields.get("materials")
        if not isinstance(supplied, (list, tuple)):
            raise ValueError("study session materials must be "
                             "a list")
        rebuilt_materials = []
        for material in supplied:
            if not isinstance(material, dict):
                raise ValueError("study session materials must "
                                 "be objects")
            rebuilt_materials.append({
                "title": (material.get("title") or "").strip(),
                "source": (material.get("source") or "").strip(),
            })
        if rebuilt_materials != rebuilt["materials"]:
            raise ValueError("study session materials do not "
                             "re-build")
        supplied_questions = fields.get("questions") or []
        if [q.strip() for q in supplied_questions] \
                != rebuilt["questions"]:
            raise ValueError("study session questions do not "
                             "re-build")
        return rebuilt
    if kind == "revision-plan":
        rebuilt = build_revision_plan(fields.get("topics"))
        if _deduped(fields.get("topics")) != rebuilt["topics"]:
            raise ValueError("revision plan topics do not "
                             "re-build")
        return rebuilt
    if kind == "dev-workflow":
        rebuilt = build_dev_workflow(fields.get("repo_label"),
                                     fields.get("steps"))
        if fields.get("repo_label", "").strip() \
                != rebuilt["repo_label"] \
                or _deduped(fields.get("steps")) \
                != rebuilt["steps"]:
            raise ValueError("dev workflow fields do not "
                             "re-build")
        return rebuilt
    if kind == "review-note":
        rebuilt = build_review_note(fields.get("cites"),
                                    fields.get("notes"))
        supplied_cites = []
        for cite in fields.get("cites") or []:
            path, _, line = cite.rpartition(":")
            supplied_cites.append(
                f"{path.strip()}:{line.strip()}")
        if supplied_cites != rebuilt["cites"] \
                or fields.get("notes", "").strip() \
                != rebuilt["notes"]:
            raise ValueError("review note fields do not "
                             "re-build")
        return rebuilt
    raise ValueError(f"unknown recordable pilot kind {kind!r}")


def _deduped(values):
    """Ordered de-duplication of caller-supplied string
    labels, mirroring the builders' duplicate rule."""
    seen = []
    if not isinstance(values, (list, tuple)):
        return seen
    for value in values:
        text = value.strip() if isinstance(value, str) else ""
        if text not in seen:
            seen.append(text)
    return seen
