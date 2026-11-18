"""Study edition — study-workspace records (ST-4).

The study planner and progress book: learning plans that adapt to
goals, prerequisites, time, and shown progress; exam-prep
organisation (syllabi, revision sessions, practice papers,
progress); skill assessment; progress tracking (completed
material, assessment results, topics to revisit).

Authority: v6 LEARN (items 1437-1446) + Study scope §2 (ST-4).

Storage rule (binding): records are LOCAL FILES in the study
workspace directory the caller supplies — files BESIDE the memory
DB, never new memory-DB columns. The frozen schema and the C-1
ruling therefore do not gate this line. ST-4 records are
study-workspace files, NOT memory-store fact records, and the two
must not be conflated: the fact-retention layer (ST-6) stays
DEFERRED behind C-1 and is not smuggled in here — no
scheduler-state columns, no fact due-dates in these files.

Limits (binding, asserted by suite):
- An assessment is a local quiz result, never a credential, never
  a grade prediction. Every assessment output carries that limit.
- Exam-prep organisation never includes leaked papers or answer
  keys for real exams: adding either is refused.
- No medical/regulated advice smuggled in as study help. BB101
  biology coursework Q&A is education, not medical advice, and
  these records never present themselves otherwise; an item
  flagged as medical/regulated advice is refused.

No cross-module imports (edition precedent). No pip-time
dependency. No shipped user data: the workspace is created at
runtime from HIS materials.
"""

from __future__ import annotations

import json
import os

ASSESSMENT_LIMITS = (
    "Limits: this assessment is a local quiz result only. It is not "
    "a credential, not a grade prediction, and not a judgement of "
    "ability beyond the material tested."
)

EXAM_PREP_LIMITS = (
    "Limits: this exam preparation uses only your own syllabi, "
    "revision sessions, and generated practice. It never uses "
    "leaked papers or answer keys for real exams."
)


class WorkspaceError(ValueError):
    """Bad plan/assessment/exam-prep shape or a refused record."""


class AdviceRefusedError(ValueError):
    """An item flagged as medical/regulated advice, refused as
    study help."""


MEDICAL_ADVICE_TAGS = (
    "medical-advice", "diagnosis", "treatment", "prescription",
    "regulated-advice",
)


def check_advice_boundary(tags, text=""):
    """Refuse items flagged as medical/regulated advice. BB101
    biology coursework (education) passes: only the explicit
    advice tags refuse, never the subject matter itself."""
    for tag in tags:
        if tag.lower() in MEDICAL_ADVICE_TAGS:
            raise AdviceRefusedError(
                "Refused: item tagged %r is medical/regulated advice, "
                "not study help. Study records never present "
                "themselves as medical advice." % (tag,))
    return None


class LearningPlan:
    """A learning plan: goals, topics with prerequisites, a time
    budget, and progress. adapt() reorders topics when progress
    changes: unfinished topics whose prerequisites are complete
    come first; completed topics move to done; blocked topics
    (prerequisites outstanding) stay last. Deterministic."""

    def __init__(self, goals, topics, time_budget_minutes=60):
        # topics: list of {"key", "title", "prerequisites": [...]}
        self.goals = list(goals)
        self.topics = [dict(t) for t in topics]
        for t in self.topics:
            t.setdefault("prerequisites", [])
            t.setdefault("done", False)
        self.time_budget_minutes = time_budget_minutes

    def mark_done(self, key):
        for t in self.topics:
            if t["key"] == key:
                t["done"] = True
                return
        raise WorkspaceError("Unknown topic %r." % (key,))

    def adapt(self):
        """Reorder topics by readiness. Returns the ordered key list
        (done topics last, in original order)."""
        done_keys = {t["key"] for t in self.topics if t["done"]}
        ready, blocked, done = [], [], []
        for t in self.topics:
            if t["done"]:
                done.append(t["key"])
            elif all(p in done_keys for p in t["prerequisites"]):
                ready.append(t["key"])
            else:
                blocked.append(t["key"])
        order = ready + blocked + done
        by_key = {t["key"]: t for t in self.topics}
        self.topics = [by_key[k] for k in order]
        return order

    def to_dict(self):
        return {"goals": self.goals, "topics": self.topics,
                "time_budget_minutes": self.time_budget_minutes}

    @classmethod
    def from_dict(cls, d):
        plan = cls(d["goals"], d["topics"], d["time_budget_minutes"])
        return plan


class Assessment:
    """A skill assessment: local quiz results with stated limits.
    Never a credential, never a grade prediction."""

    def __init__(self, subject, results, tags=()):
        # results: list of {"topic", "score", "total"}
        check_advice_boundary(tags)
        self.subject = subject
        self.results = [dict(r) for r in results]
        self.tags = list(tags)

    def summary(self):
        total = sum(r["total"] for r in self.results)
        scored = sum(r["score"] for r in self.results)
        weak = [r["topic"] for r in self.results
                if r["total"] and r["score"] / r["total"] < 0.6]
        return {"subject": self.subject, "scored": scored, "total": total,
                "topics_to_revisit": weak, "limits": ASSESSMENT_LIMITS}

    def to_dict(self):
        return {"subject": self.subject, "results": self.results,
                "tags": self.tags, "limits": ASSESSMENT_LIMITS}

    @classmethod
    def from_dict(cls, d):
        return cls(d["subject"], d["results"], d.get("tags", ()))


class ExamPrep:
    """Exam-prep organisation: syllabi, revision sessions, practice
    papers, progress. Leaked papers and real-exam answer keys are
    refused, not stored."""

    def __init__(self, exam, syllabi=()):
        self.exam = exam
        self.syllabi = list(syllabi)
        self.sessions = []
        self.practice_papers = []

    def add_session(self, topic, minutes):
        self.sessions.append({"topic": topic, "minutes": minutes})

    def add_practice_paper(self, title, leaked=False, real_exam_answer_key=False):
        if leaked:
            raise WorkspaceError(
                "Refused: %r is flagged as a leaked paper. Exam prep "
                "never uses leaked papers." % (title,))
        if real_exam_answer_key:
            raise WorkspaceError(
                "Refused: %r is flagged as an answer key for a real "
                "exam. Exam prep never uses real-exam answer keys."
                % (title,))
        self.practice_papers.append({"title": title})

    def to_dict(self):
        return {"exam": self.exam, "syllabi": self.syllabi,
                "sessions": self.sessions,
                "practice_papers": self.practice_papers,
                "limits": EXAM_PREP_LIMITS}

    @classmethod
    def from_dict(cls, d):
        ep = cls(d["exam"], d["syllabi"])
        ep.sessions = list(d["sessions"])
        ep.practice_papers = list(d["practice_papers"])
        return ep


class Progress:
    """Progress tracking: completed material, assessment results,
    topics to revisit. Round-trips through workspace files."""

    def __init__(self):
        self.completed = []
        self.assessments = []
        self.topics_to_revisit = []

    def complete(self, key):
        if key not in self.completed:
            self.completed.append(key)

    def record_assessment(self, assessment):
        self.assessments.append(assessment.summary())
        for topic in assessment.summary()["topics_to_revisit"]:
            if topic not in self.topics_to_revisit:
                self.topics_to_revisit.append(topic)

    def to_dict(self):
        return {"completed": self.completed,
                "assessments": self.assessments,
                "topics_to_revisit": self.topics_to_revisit}

    @classmethod
    def from_dict(cls, d):
        p = cls()
        p.completed = list(d["completed"])
        p.assessments = list(d["assessments"])
        p.topics_to_revisit = list(d["topics_to_revisit"])
        return p


class StudyWorkspace:
    """A study workspace directory: plans, assessments, exam preps,
    and progress as JSON files BESIDE (never inside) the memory DB.
    The caller supplies the directory; nothing here knows the
    memory DB's path or schema."""

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, name):
        base = os.path.basename(name)
        if base != name or not name.endswith(".json"):
            raise WorkspaceError("Unsafe workspace file name %r." % (name,))
        return os.path.join(self.directory, name)

    def save(self, name, record):
        path = self._path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, sort_keys=True)
        return path

    def load(self, name):
        path = self._path(name)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


__all__ = [
    "ASSESSMENT_LIMITS", "EXAM_PREP_LIMITS", "WorkspaceError",
    "AdviceRefusedError", "check_advice_boundary", "LearningPlan",
    "Assessment", "ExamPrep", "Progress", "StudyWorkspace",
]
