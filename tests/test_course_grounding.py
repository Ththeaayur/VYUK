"""U11 — course grounding: the tutor reads ONLY files he supplies,
and every bite carries a source that points at the extracted text.

Lab leg only. The real-files field leg — his real MA 105 slides
grounded, a bite citing L13 p. 4 that is actually on L13 p. 4, a
topic with no slide saying "not in your course files" — is OWED,
not this batch. Built against small FIXTURES
(``tests/fixtures/course_grounding/``), never his real course files.

Gap proved first (audit T11): ``Bite``
(``core/brain/lesson_planner.py:121``) has skill + bite + check +
gold + rubric + requires + reinforce + diagnostic and NO source
field; the progress report renders a source as "not recorded" when
none is supplied (``core/brain/progress_report.py:442``). The tree
HAS a general PDF-text tool (``read_pdf_text``,
``core/skills_plus.py:92`` — "first pages only", pypdf-backed,
lazy, honest degradation) but no course grounding: no registry, no
per-page extraction, no bite source, no source check. The B5 local
vision backend reads IMAGES, not course PDFs.

What this suite proves: a supplied file is registered explicitly
and a file outside the registry is never read; extraction keeps
page boundaries (a passage on page 2 is cited as page 2, not page
1); a bite built from a passage carries that passage's source and
the source check finds it on that page; a fake source (a page that
does not exist, a passage not in the text, a passage on a different
page) fails the source check and is rendered "not grounded", never
with a citation; a topic with no supplied source is "not in your
course files"; an extraction failure is honest (named, never
silently skipped); the planner's ordering and approval are
unchanged by the source field; the grounded source appears in the
U9 report rendering (the same assembled data, now with a real
source instead of "not recorded").

Engine availability, stated per interpreter (never silently
skipped): plain-text extraction works everywhere (stdlib); PDF
extraction works only where pypdf is installed (Qt venv: pypdf
6.21.1; system python3: absent). On an interpreter without pypdf
the PDF tests assert the honest ``engine_unavailable`` verdict —
an assertion about the degradation, not a skip.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from core.brain import course_grounding as cg
from core.brain import lesson_planner as lp
from core.brain import progress_report as pr
from core.brain import student_model as sm

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "course_grounding"
TXT = FIXTURES / "ma105_sample.txt"
PDF = FIXTURES / "ma105_sample.pdf"
LECTURE = "MA 105 L13 slides"

PDF_AVAILABLE = importlib.util.find_spec("pypdf") is not None

FLUX_PASSAGE = ("The flux of a vector field F through an oriented "
                "surface S is the surface integral of F dot n over S.")
GREEN_PASSAGE = ("Green's theorem relates a line integral around a "
                 "closed curve to a double integral over the region "
                 "it encloses.")


def _bite_dict(skill, source=None):
    raw = {"skill": skill,
           "bite": f"Teach {skill}.",
           "check": f"Check {skill}?",
           "gold": f"The answer for {skill}.",
           "rubric": f"Correct when the {skill} idea is used.",
           "requires": []}
    if source is not None:
        raw["source"] = source
    return raw


def _brain(bites):
    reply = json.dumps({"bites": bites})

    def ask(prompt):
        return reply

    return ask


class EngineAvailabilityTests(unittest.TestCase):
    def test_availability_flag_matches_import(self):
        # The suite states what this interpreter can do; a test
        # that silently skipped the PDF checks would be a lie.
        self.assertEqual(cg.pdf_available(), PDF_AVAILABLE)

    def test_text_extraction_needs_no_engine(self):
        result = cg.extract_file(TXT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["pages"]), 3)


class RegistryTests(unittest.TestCase):
    def test_register_records_file_lecture_and_pages(self):
        registry = cg.CourseRegistry()
        entry = registry.register(TXT, LECTURE)
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["lecture"], LECTURE)
        self.assertEqual(entry["pages"], 3)
        self.assertEqual(len(registry.list_files()), 1)

    def test_register_missing_file_is_refused(self):
        registry = cg.CourseRegistry()
        entry = registry.register(FIXTURES / "no_such_file.txt", LECTURE)
        self.assertFalse(entry["ok"])
        self.assertEqual(registry.list_files(), [])

    def test_register_needs_a_lecture(self):
        registry = cg.CourseRegistry()
        entry = registry.register(TXT, "   ")
        self.assertFalse(entry["ok"])
        self.assertEqual(registry.list_files(), [])

    def test_reregister_same_lecture_is_idempotent(self):
        registry = cg.CourseRegistry()
        first = registry.register(TXT, LECTURE)
        second = registry.register(TXT, LECTURE)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["file"], second["file"])
        self.assertEqual(len(registry.list_files()), 1)

    def test_reregister_other_lecture_is_refused(self):
        registry = cg.CourseRegistry()
        registry.register(TXT, LECTURE)
        entry = registry.register(TXT, "MA 105 L12 slides")
        self.assertFalse(entry["ok"])
        self.assertEqual(len(registry.list_files()), 1)

    def test_unregistered_file_is_never_read(self):
        registry = cg.CourseRegistry()
        result = registry.extraction(TXT)
        self.assertFalse(result["ok"])
        self.assertIn("not in the supplied course files", result["reason"])
        # The refusal names the file, never its contents.
        self.assertNotIn("flux", result["reason"])

    def test_same_file_by_another_path_is_the_same_entry(self):
        registry = cg.CourseRegistry()
        registry.register(TXT, LECTURE)
        # The same file reached by a different (relative) path.
        tree_root = Path(__file__).resolve().parents[1]
        relative = tree_root / "tests" / "fixtures" / "course_grounding" \
            / "ma105_sample.txt"
        self.assertTrue(registry.is_registered(relative))

    def test_registry_round_trips_through_plain_data(self):
        registry = cg.CourseRegistry()
        registry.register(TXT, LECTURE)
        rebuilt, skipped = cg.CourseRegistry.from_data(registry.to_data())
        self.assertEqual(skipped, [])
        self.assertTrue(rebuilt.is_registered(TXT))
        self.assertEqual(rebuilt.get(TXT)["lecture"], LECTURE)

    def test_from_data_names_a_file_that_vanished(self):
        rebuilt, skipped = cg.CourseRegistry.from_data(
            [{"file": str(FIXTURES / "gone.txt"), "lecture": LECTURE}])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(rebuilt.list_files(), [])


class ExtractionTests(unittest.TestCase):
    def test_text_extraction_keeps_page_boundaries(self):
        result = cg.extract_file(TXT)
        self.assertTrue(result["ok"], result)
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2, 3])
        self.assertIn("flux", result["pages"][0]["text"])
        self.assertIn("Green's theorem", result["pages"][1]["text"])
        self.assertIn("Stokes' theorem", result["pages"][2]["text"])

    def test_pdf_extraction_keeps_page_boundaries(self):
        result = cg.extract_file(PDF)
        if not PDF_AVAILABLE:
            # Honest degradation, asserted — not a skip.
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "engine_unavailable")
            return
        self.assertTrue(result["ok"], result)
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2])
        self.assertIn("flux", result["pages"][0]["text"])
        # pypdf returns the straight apostrophe as a curly one;
        # the matcher normalises punctuation, so the assertion
        # goes through the same normalisation (not the raw text).
        self.assertIn("Green's theorem",
                      cg._norm(result["pages"][1]["text"]))

    def test_corrupt_pdf_fails_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.pdf"
            bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf\n")
            result = cg.extract_file(bad)
            if not PDF_AVAILABLE:
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "engine_unavailable")
                return
            self.assertFalse(result["ok"])
            # The failure is named — a failed page, or a file that
            # could not be read as a PDF at all. Never an empty
            # extraction passed off as success.
            self.assertTrue(result["failed_pages"]
                            or "could not read" in result["reason"])

    def test_unsupported_type_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "notes.docx"
            other.write_text("not a course file")
            result = cg.extract_file(other)
            self.assertFalse(result["ok"])
            self.assertIn("unsupported", result["reason"])

    def test_missing_file_is_refused(self):
        result = cg.extract_file(FIXTURES / "no_such_file.txt")
        self.assertFalse(result["ok"])
        self.assertIn("no such file", result["reason"])


class SourceCheckTests(unittest.TestCase):
    def setUp(self):
        self.registry = cg.CourseRegistry()
        self.registry.register(TXT, LECTURE)

    def _source(self, page, passage, lecture=LECTURE):
        return {"file": str(TXT), "lecture": lecture,
                "page": page, "passage": passage}

    def test_grounded_bite_carries_its_source_and_verifies(self):
        source = self._source(1, FLUX_PASSAGE)
        bite, error = lp.validate_bite(
            _bite_dict("ma105-flux", source), 0)
        self.assertEqual(error, "")
        self.assertEqual(bite.source, source)
        checked = cg.check_bite(bite, self.registry)
        self.assertTrue(checked["grounded"], checked)
        self.assertIn("p. 1", checked["reason"])

    def test_passage_on_page_two_is_cited_as_page_two(self):
        source = self._source(2, GREEN_PASSAGE)
        result = cg.verify_source(source, self.registry)
        self.assertTrue(result["grounded"], result)
        self.assertIn("p. 2", result["reason"])

    def test_page_that_does_not_exist_is_not_grounded(self):
        source = self._source(99, FLUX_PASSAGE)
        result = cg.verify_source(source, self.registry)
        self.assertFalse(result["grounded"])
        self.assertIn("does not exist", result["reason"])

    def test_passage_not_in_the_text_is_not_grounded(self):
        source = self._source(1, "The divergence theorem is proved "
                                 "by sausages.")
        result = cg.verify_source(source, self.registry)
        self.assertFalse(result["grounded"])
        self.assertIn("not on page 1", result["reason"])

    def test_passage_on_a_different_page_does_not_ground(self):
        # The Green's passage IS in the file — but on page 2, not 1.
        source = self._source(1, GREEN_PASSAGE)
        result = cg.verify_source(source, self.registry)
        self.assertFalse(result["grounded"])
        self.assertIn("not on page 1", result["reason"])

    def test_bite_with_no_source_is_not_in_your_course_files(self):
        bite, error = lp.validate_bite(_bite_dict("ma105-flux"), 0)
        self.assertEqual(error, "")
        self.assertIsNone(bite.source)
        checked = cg.check_bite(bite, self.registry)
        self.assertFalse(checked["grounded"])
        self.assertEqual(checked["reason"], "not in your course files")

    def test_source_pointing_outside_the_registry_is_not_grounded(self):
        source = {"file": str(FIXTURES / "other.txt"),
                  "lecture": LECTURE, "page": 1, "passage": FLUX_PASSAGE}
        result = cg.verify_source(source, self.registry)
        self.assertFalse(result["grounded"])
        self.assertIn("not in the supplied course files",
                      result["reason"])

    def test_ground_bite_refuses_an_unregistered_file(self):
        bite, _ = lp.validate_bite(_bite_dict("ma105-flux"), 0)
        with self.assertRaises(ValueError):
            cg.ground_bite(bite, {"file": str(FIXTURES / "other.txt"),
                                  "lecture": LECTURE, "page": 1,
                                  "passage": FLUX_PASSAGE},
                           self.registry)

    def test_ground_bite_returns_a_new_bite_with_the_source(self):
        bite, _ = lp.validate_bite(_bite_dict("ma105-flux"), 0)
        grounded = cg.ground_bite(
            bite, self._source(1, FLUX_PASSAGE), self.registry)
        self.assertIsNone(bite.source)
        self.assertEqual(grounded.source, self._source(1, FLUX_PASSAGE))
        self.assertEqual(grounded.skill, bite.skill)

    def test_malformed_source_is_refused_by_the_planner(self):
        for bad in ({"file": str(TXT), "lecture": LECTURE, "page": 0,
                     "passage": FLUX_PASSAGE},
                    {"file": str(TXT), "lecture": LECTURE, "page": 1,
                     "passage": "   "},
                    {"file": str(TXT), "lecture": LECTURE, "page": 1},
                    "MA 105 L13 slides, p. 1"):
            parsed, error = lp.parse_topic_plan_text(json.dumps(
                {"bites": [_bite_dict("ma105-flux", bad)]}))
            self.assertIsNone(parsed, bad)
            self.assertIn("source", error, bad)

    def test_source_round_trips_through_to_dict(self):
        bite, _ = lp.validate_bite(
            _bite_dict("ma105-flux", self._source(1, FLUX_PASSAGE)), 0)
        self.assertEqual(bite.to_dict()["source"],
                         self._source(1, FLUX_PASSAGE))

    def test_format_source_is_lecture_and_page_only(self):
        self.assertEqual(cg.format_source(self._source(2, GREEN_PASSAGE)),
                         "MA 105 L13 slides, p. 2")
        # The passage is never in the rendered string.
        self.assertNotIn("Green", cg.format_source(
            self._source(2, GREEN_PASSAGE)))


class PlannerUnchangedTests(unittest.TestCase):
    def test_grounded_plan_still_orders_lowest_mastery_first(self):
        tmp = tempfile.mkdtemp()
        from core.memory import MemoryStore
        model = sm.StudentModel(MemoryStore(Path(tmp) / "assistant.db"))
        # One correct answer: p rises above the cold-start prior
        # without mastering the skill (two clean transfers WOULD
        # master it, and the planner would skip it — U1's bar).
        model.record_graded("ma105-flux", correct=True, source="check",
                            novel=True, hinted=False, confidence=0.9)
        low = _bite_dict("ma105-green", self._source(2, GREEN_PASSAGE))
        high = _bite_dict("ma105-flux", self._source(1, FLUX_PASSAGE))
        plan, error = lp.plan_topic(
            "Flux and Green", student_model=model,
            ask_brain=_brain([high, low]), brain_name="fake")
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in plan.bites],
                         ["ma105-green", "ma105-flux"])
        self.assertEqual(plan.bites[0].source["page"], 2)

    def _source(self, page, passage):
        return {"file": str(TXT), "lecture": LECTURE,
                "page": page, "passage": passage}

    def test_grounded_plan_still_requires_approval(self):
        plan, error = lp.plan_topic(
            "Flux", student_model=None,
            ask_brain=_brain([_bite_dict("ma105-flux")]),
            brain_name="fake")
        self.assertEqual(error, "")
        started, reason = plan.start()
        self.assertEqual(started, [])
        self.assertIn("never approved", reason)
        plan.approve([0], outcome="approved")
        started, reason = plan.start()
        self.assertEqual(len(started), 1)
        self.assertEqual(reason, "")


class ReportTests(unittest.TestCase):
    def test_grounded_source_appears_in_the_report(self):
        tmp = tempfile.mkdtemp()
        from core.memory import MemoryStore
        store = MemoryStore(Path(tmp) / "assistant.db")
        model = sm.StudentModel(store)
        model.record_graded("ma105-flux", correct=True, source="check",
                            novel=True, hinted=False, confidence=0.9)
        sources = {"ma105-flux": cg.format_source(
            {"file": str(TXT), "lecture": LECTURE, "page": 1,
             "passage": FLUX_PASSAGE})}
        report = pr.assemble_report(store, sources=sources)
        self.assertTrue(report["ok"], report)
        row = [r for r in report["skills"]
               if r["skill_id"] == "ma105-flux"][0]
        self.assertEqual(row["source"], "MA 105 L13 slides, p. 1")
        text = pr.render_text(report)
        self.assertIn("MA 105 L13 slides, p. 1", text)
        self.assertNotIn("not recorded", text)


if __name__ == "__main__":
    unittest.main()
