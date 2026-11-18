"""Study edition — original-practice generator (ST-3).

Generates original practice problems and quizzes from the student's
OWN materials, at a chosen difficulty, tagged to the syllabus they
belong to. Generation here means composition from structured
material records (terms, definitions, facts the student supplied),
not free-form invention and never reproduction of protected
sources: a problem that would copy protected text verbatim is
refused, and every set carries its limits statement.

Authority: v6 LEARN (items 1437-1446; exit rule: a practice set
matches its stated difficulty and syllabus, verified against the
source material) + Study scope §2 (ST-3).

The three LEARN items the v6 prose omits, with their limits stated
here (scope §2 / §5.5a):
- Course recommendation (pool 1442): scoped DOWN to ranking HIS OWN
  workspace materials by prerequisites and objectives. It cannot
  browse paid catalogues; no catalogue is bundled. See rank_materials.
- Research assistance (pool 1443): NOT built here. It rides the
  Research track; this module contributes only practice forms that
  carry provenance (see st_cite's discipline, mirrored here by
  convention, not by import).
- Language learning (pool 1444): rides this same generator — cloze
  for vocabulary, Q/A for grammar. No separate language engine, no
  translation model bundled.

Free-form generation via the local brain tiers happens at the
surface wave, not here: this library composes from material
records the caller supplies (which may themselves be brain-tier
outputs). That split is stated, not hidden.

No cross-module imports (edition precedent). No pip-time
dependency. No bundled course materials: the workspace is created
at runtime from HIS materials.
"""

from __future__ import annotations

import re

DIFFICULTIES = ("easy", "medium", "hard")
FORMS = ("qa", "cloze", "mcq", "vocab-cloze", "grammar-qa")

LIMITS_STATEMENT = (
    "Limits: this is generated practice, not a past paper and not a "
    "prediction of exam questions. Problems are composed from your own "
    "materials; they do not reproduce protected sources. Check every "
    "answer against your source material."
)

COURSE_RANK_LIMITS = (
    "Limits: this ranking covers only your own workspace materials. "
    "It cannot browse paid catalogues, and no catalogue is bundled."
)


class CopyRefusedError(ValueError):
    """A practice request that would copy protected source text."""


class PracticeError(ValueError):
    """Bad difficulty, form, syllabus, or material shape."""


class Material:
    """One structured material record: a term/fact, its definition
    or answer text, its syllabus tag, its source, and its
    prerequisites (material keys that should come first)."""

    def __init__(self, key, prompt, answer, syllabus, source,
                 prerequisites=(), protected=False):
        if not prompt or not answer:
            raise PracticeError("Material needs a prompt and an answer.")
        if not syllabus:
            raise PracticeError("Material needs a syllabus tag.")
        if not source or not source.strip():
            raise PracticeError("Material needs a non-empty Source.")
        self.key = key
        self.prompt = prompt
        self.answer = answer
        self.syllabus = syllabus
        self.source = source
        self.prerequisites = list(prerequisites)
        self.protected = protected

    def to_dict(self):
        return {
            "key": self.key, "prompt": self.prompt, "answer": self.answer,
            "syllabus": self.syllabus, "source": self.source,
            "prerequisites": self.prerequisites, "protected": self.protected,
        }


def check_originality(problem_text, protected_passages):
    """Refuse a problem whose text reproduces a protected passage
    verbatim (normalised whitespace, case-insensitive). Returns None
    on pass; raises CopyRefusedError on a verbatim hit."""
    norm = re.sub(r"\s+", " ", problem_text).strip().lower()
    for passage in protected_passages:
        p = re.sub(r"\s+", " ", passage).strip().lower()
        if p and p in norm:
            raise CopyRefusedError(
                "Refused: problem text reproduces a protected passage "
                "verbatim. Generated practice never copies protected sources.")
    return None


def refuse_protected_reproduction(material, verbatim=False):
    """Refuse a request to reproduce a protected material verbatim.
    Verbatim reproduction is always refused. Composing a new problem
    FROM a protected material is refused separately by
    generate_problem: a template library cannot paraphrase honestly,
    so protected materials are study references, never problem
    sources. Compose from your own notes instead."""
    if material.protected and verbatim:
        raise CopyRefusedError(
            "Refused: verbatim reproduction of protected material %r "
            "is not generated practice." % (material.key,))
    return None


def _cloze_from_definition(answer, difficulty):
    """Cloze deletion over a definition: blank the longest content
    word (easy: one blank; medium: two; hard: three, or all content
    words up to three). Deterministic: word order decides."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9']*", answer)
    content = [w for w in words if len(w) > 3]
    n = {"easy": 1, "medium": 2, "hard": 3}[difficulty]
    blanks = content[:n]
    text = answer
    for i, w in enumerate(blanks):
        text = re.sub(r"\b%s\b" % re.escape(w), "{{c%d::%s}}" % (i + 1, w),
                      text, count=1)
    return text, blanks


def _mcq_options(correct, siblings, difficulty):
    """Distractors drawn from sibling materials' answers (his own
    materials, never an external bank). Easy: 2 options; medium: 3;
    hard: 4. Order is deterministic (correct first, then siblings
    in given order) so sets are reproducible."""
    n = {"easy": 2, "medium": 3, "hard": 4}[difficulty]
    options = [correct]
    for sib in siblings:
        if sib.answer != correct and sib.answer not in options:
            options.append(sib.answer)
        if len(options) >= n:
            break
    while len(options) < n:
        options.append("(none of the above)")
    return options


def generate_problem(material, siblings, difficulty="medium", form="qa"):
    """Compose ONE original problem from one material record.
    siblings: other materials from the same set (distractor pool).
    Returns a problem dict carrying its limits via the set, not the
    problem — see generate_set, which always attaches them."""
    if difficulty not in DIFFICULTIES:
        raise PracticeError("Unknown difficulty %r." % (difficulty,))
    if form not in FORMS:
        raise PracticeError("Unknown form %r." % (form,))
    if material.protected:
        raise CopyRefusedError(
            "Refused: material %r is flagged protected. Protected "
            "materials are study references, never problem sources — "
            "a template library cannot paraphrase honestly, so it "
            "refuses rather than laundering a copy. Compose from "
            "your own notes instead." % (material.key,))
    if form == "cloze" or form == "vocab-cloze":
        text, blanks = _cloze_from_definition(material.answer, difficulty)
        return {"form": form, "prompt": material.prompt, "text": text,
                "blanks": blanks, "answer": material.answer,
                "syllabus": material.syllabus, "source": material.source}
    if form == "mcq":
        return {"form": form, "prompt": material.prompt,
                "question": material.prompt,
                "options": _mcq_options(material.answer, siblings, difficulty),
                "answer": material.answer,
                "syllabus": material.syllabus, "source": material.source}
    # qa / grammar-qa: direct question from the material's prompt.
    return {"form": form, "prompt": material.prompt,
            "question": material.prompt, "answer": material.answer,
            "syllabus": material.syllabus, "source": material.source}


def generate_set(materials, difficulty="medium", syllabus=None,
                 forms=("qa",), protected_passages=()):
    """Generate a practice set from material records. Every problem
    is original composition; every set carries its limits statement
    and its stated difficulty + syllabus (the v6 LEARN exit rule).
    A protected-source copy attempt is refused, not laundered."""
    if difficulty not in DIFFICULTIES:
        raise PracticeError("Unknown difficulty %r." % (difficulty,))
    if syllabus is None:
        syllabus = sorted({m.syllabus for m in materials})
    if isinstance(syllabus, str):
        syllabus = [syllabus]
    chosen = [m for m in materials if m.syllabus in syllabus]
    if not chosen:
        raise PracticeError("No materials match syllabus %r." % (syllabus,))
    problems = []
    for i, material in enumerate(chosen):
        form = forms[i % len(forms)]
        siblings = [m for m in chosen if m is not material]
        problem = generate_problem(material, siblings, difficulty, form)
        check_originality(problem.get("text", problem.get("question", "")),
                          protected_passages)
        problems.append(problem)
    return {
        "difficulty": difficulty,
        "syllabus": list(syllabus),
        "problems": problems,
        "limits": LIMITS_STATEMENT,
        "count": len(problems),
    }


def rank_materials(materials, objectives, completed=()):
    """Course recommendation, scoped DOWN (pool 1442): rank HIS OWN
    materials by prerequisite order and objective coverage. Materials
    whose prerequisites are completed (or empty) rank first; then
    by how many stated objectives they serve; then by key order
    (deterministic). No catalogue is consulted; no catalogue exists
    here. Returns (ranked_keys, limits_statement)."""
    completed = set(completed)
    scored = []
    for m in materials:
        prereqs_met = all(p in completed for p in m.prerequisites)
        coverage = sum(1 for o in objectives
                       if o.lower() in (m.prompt + " " + m.answer).lower()
                       or o.lower() in m.syllabus.lower())
        scored.append(((0 if prereqs_met else 1), -coverage, m.key, m))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [m.key for _, _, _, m in scored], COURSE_RANK_LIMITS


__all__ = [
    "DIFFICULTIES", "FORMS", "LIMITS_STATEMENT", "COURSE_RANK_LIMITS",
    "CopyRefusedError", "PracticeError", "Material", "check_originality",
    "refuse_protected_reproduction", "generate_problem", "generate_set",
    "rank_materials",
]
