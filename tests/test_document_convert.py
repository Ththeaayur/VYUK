"""B-D3b (2026-11-09) document -> Markdown on DIRECT libraries.

Runs against the REAL direct libraries where they are installed
(.venv-qt: mammoth + markdownify, python-pptx, openpyxl). On
system python3 (no mammoth / python-pptx / markdownify — PEP
668 blocks pip there) the docx/pptx conversion tests assert the
honest engine_unavailable result instead of skipping, while
xlsx tests RUN there (system python3 ships openpyxl 3.1.2): a
suite that silently skips the conversion checks on an
interpreter without the library is a lie, not a pass. Fixtures
are committed files under tests/fixtures/document_convert/
(hand-built minimal .docx; .pptx built with python-pptx and
.xlsx built with openpyxl in the Qt venv at fixture time) — no
writer library is needed at test time, and no real user files
are copied into the tree.

The real-Windows leg — real lecture files converting on his
machine — is a field leg, owed, not this batch.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import unittest

from core.desktop import document_convert as dc

FIXTURES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures",
    "document_convert")


def _fixture(name):
    return os.path.join(FIXTURES, name)


def _hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class _EngineGate(unittest.TestCase):
    """The engine gate, stated once, PER FORMAT: on an
    interpreter without a format's library, convert() returns
    engine_unavailable honestly for that format only.
    Real-conversion tests call _format_or_unavailable() first —
    it asserts that shape and returns False when the format's
    engine is absent, so the test passes by asserting the
    degradation instead of skipping it."""

    def _format_or_unavailable(self, path):
        found = dc.detect_format(path)
        fmt = found["format"] if found["ok"] else None
        if fmt is None:
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            fmt = ext if ext in dc.SUPPORTED_FORMATS else None
        if fmt is not None and dc.engine_available(fmt):
            return True
        result = dc.convert(path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["markdown"], "")
        self.assertIn("engine_unavailable", result["reason"])
        return False

    # Back-compat name for the old gate (B-D3); the per-format
    # gate above is the one the tests call.
    def _engine_or_unavailable(self, path):
        return self._format_or_unavailable(path)


class TestDetectFormat(_EngineGate):
    def test_docx_detected_from_bytes(self):
        found = dc.detect_format(_fixture("sample.docx"))
        self.assertTrue(found["ok"])
        self.assertEqual(found["format"], "docx")
        self.assertEqual(found["media_count"], 0)

    def test_pptx_detected_from_bytes(self):
        found = dc.detect_format(_fixture("sample.pptx"))
        self.assertTrue(found["ok"])
        self.assertEqual(found["format"], "pptx")

    def test_xlsx_detected_from_bytes(self):
        found = dc.detect_format(_fixture("sample.xlsx"))
        self.assertTrue(found["ok"])
        self.assertEqual(found["format"], "xlsx")

    def test_plain_text_is_not_an_office_document(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".docx", delete=False) as fh:
            fh.write("this is plain text, not a zip")
            name = fh.name
        try:
            found = dc.detect_format(name)
            self.assertFalse(found["ok"])
            self.assertIn("not a ZIP container", found["reason"])
        finally:
            os.unlink(name)

    def test_empty_file_is_not_an_office_document(self):
        with tempfile.NamedTemporaryFile(
                "wb", suffix=".docx", delete=False) as fh:
            name = fh.name
        try:
            found = dc.detect_format(name)
            self.assertFalse(found["ok"])
            self.assertIn("empty", found["reason"])
        finally:
            os.unlink(name)

    def test_oversize_file_refused_before_reading(self):
        with tempfile.NamedTemporaryFile(
                "wb", suffix=".docx", delete=False) as fh:
            fh.truncate(dc.MAX_BYTES + 1)
            name = fh.name
        try:
            found = dc.detect_format(name)
            self.assertFalse(found["ok"])
            self.assertIn("over", found["reason"])
            self.assertIn("conversion limit", found["reason"])
        finally:
            os.unlink(name)

    def test_missing_file(self):
        found = dc.detect_format(
            os.path.join(FIXTURES, "does-not-exist.docx"))
        self.assertFalse(found["ok"])
        self.assertIn("no such file", found["reason"])


class TestConvert(_EngineGate):
    def test_docx_converts_with_heading_and_table(self):
        path = _fixture("sample.docx")
        if not self._format_or_unavailable(path):
            return
        result = dc.convert(path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["format"], "docx")
        self.assertIn("# Integration by Parts", result["markdown"])
        self.assertIn("e to the x times x minus one",
                      result["markdown"])
        # The table's cells survive as a Markdown table.
        self.assertIn("| u | dv |", result["markdown"])
        self.assertIn("| x | e^x dx |", result["markdown"])
        self.assertIn("Final line after the table.",
                      result["markdown"])
        self.assertEqual(result["report"]["units"],
                         [{"page": 1, "unit": "document"}])
        self.assertEqual(result["report"]["warnings"], [])
        self.assertEqual(result["source"]["file"],
                         os.path.realpath(path))

    def test_pptx_converts_per_slide(self):
        path = _fixture("sample.pptx")
        if not self._format_or_unavailable(path):
            return
        result = dc.convert(path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["format"], "pptx")
        self.assertIn("Green Theorem", result["markdown"])
        self.assertIn("Stokes Theorem", result["markdown"])
        # Speaker notes are included, inside their slide's chunk.
        self.assertIn("remind them about orientation",
                      result["markdown"])
        self.assertEqual(result["report"]["units"], [
            {"page": 1, "unit": "slide 1"},
            {"page": 2, "unit": "slide 2"},
        ])
        pages = dc.extract_pages(path)["pages"]
        self.assertEqual([p["page"] for p in pages], [1, 2])
        self.assertIn("Green Theorem", pages[0]["text"])
        self.assertNotIn("Stokes Theorem", pages[0]["text"])
        self.assertIn("Stokes Theorem", pages[1]["text"])
        self.assertNotIn("Green Theorem", pages[1]["text"])

    def test_xlsx_converts_per_sheet(self):
        path = _fixture("sample.xlsx")
        if not self._format_or_unavailable(path):
            return
        result = dc.convert(path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["format"], "xlsx")
        self.assertIn("Aayur", result["markdown"])
        self.assertIn("28", result["markdown"])
        self.assertIn("30", result["markdown"])
        self.assertEqual(result["report"]["units"], [
            {"page": 1, "unit": "sheet 'Marks'"},
            {"page": 2, "unit": "sheet 'Totals'"},
        ])
        pages = dc.extract_pages(path)["pages"]
        self.assertIn("Aayur", pages[0]["text"])
        self.assertNotIn("Aayur", pages[1]["text"])
        self.assertIn("Total", pages[1]["text"])

    def test_image_docx_reports_embedded_data_uri(self):
        path = _fixture("sample_with_image.docx")
        if not self._format_or_unavailable(path):
            return
        result = dc.convert(path)
        self.assertTrue(result["ok"], result["reason"])
        self.assertIn("data:image/", result["markdown"])
        self.assertEqual(result["report"]["warnings"], [
            "1 image(s) embedded as data URIs in the Markdown"])

    def test_corrupt_file_fails_honestly_never_empty_success(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".docx", delete=False) as fh:
            fh.write("this is plain text, not a zip")
            name = fh.name
        try:
            result = dc.convert(name)
            self.assertFalse(result["ok"])
            self.assertEqual(result["markdown"], "")
            self.assertIn("not a ZIP container", result["reason"])
        finally:
            os.unlink(name)

    def test_truncated_zip_fails_honestly(self):
        with tempfile.NamedTemporaryFile(
                "wb", suffix=".docx", delete=False) as fh:
            fh.write(b"PK\x03\x04garbagegarbage")
            name = fh.name
        try:
            result = dc.convert(name)
            self.assertFalse(result["ok"])
            self.assertEqual(result["markdown"], "")
        finally:
            os.unlink(name)

    def test_mislabelled_file_converts_as_its_bytes(self):
        # A .pptx renamed .docx: the bytes win, with a warning.
        src = _fixture("sample.pptx")
        tmpdir = tempfile.mkdtemp()
        try:
            renamed = os.path.join(tmpdir, "deck.docx")
            shutil.copy(src, renamed)
            if not self._format_or_unavailable(renamed):
                return
            result = dc.convert(renamed)
            self.assertTrue(result["ok"], result["reason"])
            self.assertEqual(result["format"], "pptx")
            self.assertTrue(result["report"]["extension_mismatch"])
            self.assertEqual(result["report"]["warnings"], [
                "extension '.docx' disagrees with the file's bytes "
                "(a pptx document) — converted as pptx"])
            self.assertIn("Green Theorem", result["markdown"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pdf_refused_with_the_pypdf_reason(self):
        pdf = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures",
            "course_grounding", "ma105_sample.pdf")
        result = dc.convert(pdf)
        self.assertFalse(result["ok"])
        self.assertEqual(result["markdown"], "")
        self.assertIn("pypdf path", result["reason"])

    def test_legacy_doc_refused_honestly(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".doc", delete=False) as fh:
            fh.write("pretend this is a legacy Word file")
            name = fh.name
        try:
            result = dc.convert(name)
            self.assertFalse(result["ok"])
            self.assertIn("legacy .doc", result["reason"])
        finally:
            os.unlink(name)

    def test_source_never_rewritten(self):
        for name in ("sample.docx", "sample.pptx", "sample.xlsx"):
            path = _fixture(name)
            before = _hash(path)
            if not self._format_or_unavailable(path):
                return
            result = dc.convert(path)
            self.assertTrue(result["ok"], result["reason"])
            self.assertEqual(_hash(path), before)

    def test_source_pointer_names_the_original(self):
        path = _fixture("sample.pptx")
        if not self._format_or_unavailable(path):
            return
        result = dc.convert(path)
        self.assertEqual(result["source"]["file"],
                         os.path.realpath(path))
        self.assertEqual(result["source"]["format"], "pptx")

    def test_convert_to_file_writes_markdown(self):
        path = _fixture("sample.docx")
        if not self._format_or_unavailable(path):
            return
        tmpdir = tempfile.mkdtemp()
        try:
            out = os.path.join(tmpdir, "out.md")
            result = dc.convert_to_file(path, out)
            self.assertTrue(result["ok"], result["reason"])
            self.assertEqual(result["out"], os.path.realpath(out))
            with open(out, encoding="utf-8") as fh:
                self.assertIn("# Integration by Parts", fh.read())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_convert_to_file_refuses_to_overwrite_source(self):
        path = _fixture("sample.docx")
        result = dc.convert_to_file(path, path)
        self.assertFalse(result["ok"])
        self.assertIn("overwrite", result["reason"])

    def test_convert_to_file_requires_an_output_path(self):
        path = _fixture("sample.docx")
        result = dc.convert_to_file(path, "")
        self.assertFalse(result["ok"])
        self.assertIn("output path is required", result["reason"])

    def test_supported_formats(self):
        self.assertEqual(dc.supported_formats(),
                         ("docx", "pptx", "xlsx"))
        self.assertNotIn("pdf", dc.supported_formats())

    def test_engine_status_is_honest(self):
        status = dc.engine_status()
        self.assertEqual(status["available"], dc.engine_available())
        self.assertEqual(set(status["formats"]),
                         {"docx", "pptx", "xlsx"})
        if status["available"]:
            self.assertEqual(status["backend"], "direct")
            self.assertIsNone(status["error"])
        else:
            self.assertIsNone(status["backend"])
            self.assertIn("unavailable", status["error"])
        # Per-format status is honest too: each format's flag
        # matches its own availability.
        for fmt in dc.SUPPORTED_FORMATS:
            one = dc.engine_status(fmt)
            self.assertEqual(one["available"],
                             dc.engine_available(fmt))
            self.assertEqual(set(one["formats"]),
                             {"docx", "pptx", "xlsx"})

    def test_engine_status_is_per_format(self):
        # A missing library degrades ONLY its format: on an
        # interpreter with openpyxl but no mammoth, xlsx is
        # available while docx is not — never all-or-nothing.
        self.assertEqual(dc.engine_available("xlsx"),
                         dc.engine_status("xlsx")["available"])
        self.assertEqual(dc.engine_available("docx"),
                         dc.engine_status("docx")["available"])
        self.assertEqual(dc.engine_available("pptx"),
                         dc.engine_status("pptx")["available"])


class TestU11Seam(_EngineGate):
    """The U11 seam: office files register, extract, and ground
    through CourseRegistry exactly like text/PDF files — the
    registry records the ORIGINAL file, and a bite cites the
    original file + slide/sheet."""

    def test_docx_registers_and_grounds(self):
        from core.brain.course_grounding import (
            CourseRegistry, check_bite, ground_bite)
        from core.brain.lesson_planner import Bite
        path = _fixture("sample.docx")
        if not self._format_or_unavailable(path):
            return
        registry = CourseRegistry()
        entry = registry.register(path, "MA 105 L1")
        self.assertTrue(entry["ok"], entry["reason"])
        self.assertEqual(entry["pages"], 1)
        extracted = registry.extraction(path)
        self.assertTrue(extracted["ok"])
        self.assertEqual(len(extracted["pages"]), 1)
        bite = Bite(skill="ma105-test", bite="t", check="c",
                    gold="g", rubric="r")
        grounded = ground_bite(bite, {
            "file": path, "lecture": "MA 105 L1", "page": 1,
            "passage": "e to the x times x minus one"}, registry)
        result = check_bite(grounded, registry)
        self.assertTrue(result["grounded"], result["reason"])
        # The same passage cited as page 2 does not ground.
        wrong = ground_bite(bite, {
            "file": path, "lecture": "MA 105 L1", "page": 2,
            "passage": "e to the x times x minus one"}, registry)
        self.assertFalse(check_bite(wrong, registry)["grounded"])

    def test_pptx_grounds_per_slide(self):
        from core.brain.course_grounding import (
            CourseRegistry, check_bite, ground_bite)
        from core.brain.lesson_planner import Bite
        path = _fixture("sample.pptx")
        if not self._format_or_unavailable(path):
            return
        registry = CourseRegistry()
        entry = registry.register(path, "MA 105 L2")
        self.assertTrue(entry["ok"], entry["reason"])
        self.assertEqual(entry["pages"], 2)
        bite = Bite(skill="ma105-test", bite="t", check="c",
                    gold="g", rubric="r")
        slide2 = ground_bite(bite, {
            "file": path, "lecture": "MA 105 L2", "page": 2,
            "passage": "Circulation equals surface integral of "
                       "curl."}, registry)
        self.assertTrue(check_bite(slide2, registry)["grounded"])
        # Slide 2's passage cited as slide 1 does not ground.
        slide1 = ground_bite(bite, {
            "file": path, "lecture": "MA 105 L2", "page": 1,
            "passage": "Circulation equals surface integral of "
                       "curl."}, registry)
        self.assertFalse(check_bite(slide1, registry)["grounded"])

    def test_xlsx_registers_and_grounds(self):
        from core.brain.course_grounding import (
            CourseRegistry, check_bite, ground_bite)
        from core.brain.lesson_planner import Bite
        path = _fixture("sample.xlsx")
        if not self._format_or_unavailable(path):
            return
        registry = CourseRegistry()
        entry = registry.register(path, "MA 105 L3")
        self.assertTrue(entry["ok"], entry["reason"])
        self.assertEqual(entry["pages"], 2)
        bite = Bite(skill="ma105-test", bite="t", check="c",
                    gold="g", rubric="r")
        grounded = ground_bite(bite, {
            "file": path, "lecture": "MA 105 L3", "page": 1,
            "passage": "Aayur"}, registry)
        self.assertTrue(check_bite(grounded, registry)["grounded"])

    def test_pdf_path_untouched(self):
        # U11's existing PDF fixture still grounds through pypdf.
        # On system python3 (no pypdf) the honest degradation is
        # asserted instead — the same shape U11's own suite pins.
        from core.brain.course_grounding import (
            CourseRegistry, pdf_available)
        pdf = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures",
            "course_grounding", "ma105_sample.pdf")
        registry = CourseRegistry()
        entry = registry.register(pdf, "MA 105 L13")
        self.assertTrue(entry["ok"], entry["reason"])
        if not pdf_available():
            self.assertIsNone(entry["pages"])
            self.assertIn("engine", entry["reason"])
            extracted = registry.extraction(pdf)
            self.assertFalse(extracted["ok"])
            self.assertEqual(extracted["reason"],
                             "engine_unavailable")
            return
        extracted = registry.extraction(pdf)
        self.assertTrue(extracted["ok"])
        self.assertEqual(len(extracted["pages"]), 2)

    def test_unsupported_type_registers_with_a_named_reason(self):
        from core.brain.course_grounding import CourseRegistry
        with tempfile.NamedTemporaryFile(
                "w", suffix=".doc", delete=False) as fh:
            fh.write("pretend this is a legacy Word file")
            name = fh.name
        try:
            registry = CourseRegistry()
            entry = registry.register(name, "MA 105 L9")
            self.assertTrue(entry["ok"])
            self.assertIsNone(entry["pages"])
            self.assertIn("unsupported", entry["reason"])
            self.assertFalse(registry.extraction(name)["ok"])
        finally:
            os.unlink(name)


if __name__ == "__main__":
    unittest.main()
