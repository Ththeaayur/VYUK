"""U11 — Course grounding (tutor programme, 2026-11-08).

Lab leg only. The real-files field leg — his real MA 105 slides
grounded, a bite citing L13 p. 4 that is actually on L13 p. 4, a
topic with no slide saying "not in your course files" — is OWED,
not this batch. This module is built against small FIXTURES
(``tests/fixtures/course_grounding/``), never his real course files,
which are never copied into the tree.

Gap proved first (audit T11): the tutor's bites come from the
brain's general knowledge, not from his course files. ``Bite``
(``core/brain/lesson_planner.py:121``) has skill + bite + check +
gold + rubric + requires + reinforce + diagnostic and NO source
field; the progress report renders a source as "not recorded" when
none is supplied (``core/brain/progress_report.py:442``) — the
source is driver-supplied and never comes from a grounded file.
The tree HAS a general PDF-text tool (``read_pdf_text``,
``core/skills_plus.py:92`` — "first pages only", pypdf-backed,
lazy, honest degradation) but no course grounding: no registry,
no per-page extraction, no bite source, no source check. The B5
local vision backend (Qwen3-VL + Granite-Docling) reads IMAGES
(screenshots/scans via OCR), not course PDFs — a different job.

EXTRACTOR DECISION — reuse the tree's existing pypdf path; NO new
dependency. pypdf is already the tree's optional PDF dependency
(``requirements.txt:62-63``, commented ``pypdf>=4.0``;
``THIRD_PARTY_LICENSES.md:29``, BSD-3-Clause, commercial use
permitted; ``core/skills_media_convert.py:81`` lazy loader with
honest degradation). pypdf 6.21.1 is installed in the Qt venv and
absent from system python3; docling and fitz are absent from BOTH
interpreters. Rejected: docling (round 5's find — a heavy new
dependency with a torch-class footprint for a job pypdf already
does; uninstalled in both interpreters); fitz/PyMuPDF (uninstalled,
a NEW dependency for the same job); vendoring a PDF parser (a PDF
parser is a real parser — vendoring one is a new loophole, not a
shortcut). Plain-text files (.txt/.md) are extracted with the
stdlib, always available; PDFs go through the lazy pypdf loader
and degrade honestly (``engine_unavailable``) when pypdf is
absent. The fixture tests assert the degradation explicitly on an
interpreter without pypdf — they never silently skip it.

LOPHOLE FAMILY — "a tutor that teaches from memory instead of
your course": a bite that cites a lecture and page it never read;
a page number invented to look grounded; a bite that teaches
something outside his syllabus (the finals are L1–L13, nothing
else); a bite that quotes the slides but the quote is not in
them; a file read the owner never supplied (D-T2 says ONLY files
he supplies). ONE design closes the class: grounding as a
traceable chain — the tutor reads ONLY files he supplies (an
explicit, listed registry, nothing else — no directory scans, no
"helpful" extra files); each file is extracted to text with its
lecture + page structure kept; each bite carries a source (file +
lecture + page + passage) that POINTS AT the extracted text (the
source is a pointer into the extraction, not a free-text string);
a bite whose source cannot be found in the extracted text is
"not grounded" (never shown with a citation); a topic with no
supplied source is "not in your course files" (never taught from
general knowledge dressed as a citation).
Rejected alternatives (recorded, not carried forward):
- Free-text citation strings (a string can say anything — a
  pointer into the extraction cannot lie).
- Teaching from general knowledge with a citation attached (the
  citation is the lie — if it is not in his files, it says so).
- Reading a whole directory (D-T2 says only files he supplies —
  a scan reads files he did not supply).
- Inventing page numbers (a page that does not exist in the
  extraction is not a source).
- Grounding by keyword match alone (a keyword match is not a
  citation — the bite must point at the passage it came from).

BOUNDARIES (named, not hidden):
- The registry is SESSION-scoped and in-memory: it holds no
  MemoryStore facts-table rows, so U9's one-action clear
  (which deletes exactly the three tutor categories) stays
  complete — nothing escapes it. ``to_data()`` / ``from_data()``
  let the future session driver persist the list as its own
  session state and re-register on start; that driver batch
  must re-supply the files explicitly, never scan for them.
- The planner's shape check (``lesson_planner.validate_bite``)
  validates a source's SHAPE only (types, non-empty, page >= 1);
  this module owns the SEMANTIC check (registered file, page
  exists, passage found). The two cannot drift apart silently:
  a shape-valid source that fails the semantic check is "not
  grounded", never taught with a citation.
- Text files split pages on form feed (``\\f``) — the standard
  text page break, documented here and in the fixture. A text
  file with no page break is one page.
- The passage match is whitespace-normalised substring match on
  the cited page only (case-sensitive — a quote is a quote). A
  passage found on a DIFFERENT page than cited does not ground
  the bite: the citation must be right, not merely nearby.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# The source pointer
# ---------------------------------------------------------------------------

SOURCE_KEYS = ("file", "lecture", "page", "passage")


@dataclass(frozen=True)
class Source:
    """A pointer into an extraction — file + lecture + page + passage.

    Not a free-text citation: ``file`` must be a path in the
    registry, ``page`` a 1-based page that exists in that file's
    extraction, ``passage`` the quoted text that must be found on
    that page. The planner stores this as a plain dict (see
    ``to_dict``); the grounding module owns the semantic check.
    """

    file: str
    lecture: str
    page: int
    passage: str

    def to_dict(self) -> dict:
        return {"file": self.file, "lecture": self.lecture,
                "page": self.page, "passage": self.passage}

    @classmethod
    def from_dict(cls, raw):
        """(Source | None, error): shape only, never the semantic check."""
        if not isinstance(raw, dict):
            return None, "source is not an object"
        for key in SOURCE_KEYS:
            if key not in raw:
                return None, f"source has no {key!r}"
        if not isinstance(raw["file"], str) or not raw["file"].strip():
            return None, "source 'file' is not usable text"
        if not isinstance(raw["lecture"], str) \
                or not raw["lecture"].strip():
            return None, "source 'lecture' is not usable text"
        if not isinstance(raw["page"], int) or isinstance(raw["page"], bool) \
                or raw["page"] < 1:
            return None, "source 'page' is not a page number (>= 1)"
        if not isinstance(raw["passage"], str) \
                or not raw["passage"].strip():
            return None, "source 'passage' is not usable text"
        return cls(file=raw["file"].strip(),
                   lecture=raw["lecture"].strip(), page=raw["page"],
                   passage=raw["passage"].strip()), ""


def format_source(source) -> str:
    """A Source (or its dict) as the U9 report string.

    ``"MA 105 L13 slides, p. 4"`` — lecture + page. The passage
    is never in the rendered string (the report shows where a bite
    came from, not the quote itself).
    """
    if isinstance(source, dict):
        lecture = str(source.get("lecture") or "").strip()
        page = source.get("page")
    else:
        lecture = str(getattr(source, "lecture", "") or "").strip()
        page = getattr(source, "page", None)
    if not lecture or not isinstance(page, int):
        return ""
    return f"{lecture}, p. {page}"


def _norm(text: str) -> str:
    """Collapse whitespace runs to single spaces, strip. Case kept.

    PDF extraction routinely normalises punctuation (a straight
    apostrophe comes back curly, a hyphen comes back an en dash),
    so typographic variants are folded to their ASCII equivalents
    before comparing — a quote is still the whole passage, in
    order, on the cited page; only the punctuation's shape is
    forgiven, never its words.
    """
    folded = str(text or "")
    for curly, straight in (("\u2018", "'"), ("\u2019", "'"),
                            ("\u201c", '"'), ("\u201d", '"'),
                            ("\u2013", "-"), ("\u2014", "-"),
                            ("\u2026", "...")):
        folded = folded.replace(curly, straight)
    return re.sub(r"\s+", " ", folded).strip()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

TEXT_SUFFIXES = (".txt", ".md", ".markdown")
PDF_SUFFIXES = (".pdf",)
# B-D3 (2026-11-09) / B-D3b (2026-11-09): Office formats via
# the direct-libraries wrapper (core/desktop/document_convert.py
# — mammoth / python-pptx / openpyxl, after the parent rejected
# markitdown on weight). The PDF path above is untouched — a
# flat Markdown conversion has no page boundaries, so PDFs stay
# on pypdf.
OFFICE_SUFFIXES = (".docx", ".pptx", ".xlsx")


def pdf_available() -> bool:
    """True when the PDF engine (pypdf) can be imported here."""
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — absent is a state, not a crash.
        return False


def _load_pypdf():
    """(module | None, error): the tree's lazy pypdf loader pattern."""
    try:
        import pypdf
        return pypdf, ""
    except Exception as exc:  # noqa: BLE001
        return None, (f"the PDF engine is not available in this "
                      f"interpreter — install pypdf ({exc})")


def _extract_text_file(path: Path) -> dict:
    """Text files: stdlib read, pages split on form feed (``\\f``)."""
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "pages": [], "failed_pages": [],
                    "reason": f"could not read {path.name}: {exc}"}
    except OSError as exc:
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": f"could not read {path.name}: {exc}"}
    parts = text.split("\f")
    pages = [{"page": i + 1, "text": part}
             for i, part in enumerate(parts)]
    return {"ok": True, "pages": pages, "failed_pages": [], "reason": ""}


def _extract_pdf_file(path: Path) -> dict:
    """PDFs: one entry per page, via the lazy pypdf loader.

    A page whose text cannot be read is named in ``failed_pages``
    and the extraction is NOT ok — never silently skipped.
    """
    pypdf, error = _load_pypdf()
    if pypdf is None:
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": "engine_unavailable", "detail": error}
    try:
        reader = pypdf.PdfReader(str(path))
        total = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": f"could not read {path.name} as a PDF: {exc}"}
    if total == 0:
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": f"{path.name} has no pages to read"}
    pages = []
    failed = []
    for index in range(total):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            failed.append({"page": index + 1, "reason": str(exc)})
            continue
        pages.append({"page": index + 1, "text": text})
    if failed:
        return {"ok": False, "pages": pages, "failed_pages": failed,
                "reason": "could not read page(s) "
                          + ", ".join(str(f["page"]) for f in failed)}
    return {"ok": True, "pages": pages, "failed_pages": [], "reason": ""}


def _extract_office_file(path: Path) -> dict:
    """Office documents (.docx/.pptx/.xlsx): one entry per unit
    (1 page for a document, one per slide, one per sheet) via
    the direct-libraries wrapper (core/desktop/document_convert.py).

    The wrapper's page number IS the unit number inside the
    ORIGINAL file, so a grounded bite cites the original file +
    slide/sheet — never a derived file. An unavailable engine
    (the format's library not installed on this interpreter)
    is the same honest shape as the PDF path's
    engine_unavailable.
    """
    try:
        from core.desktop import document_convert
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": "engine_unavailable",
                "detail": f"the document engine is not available "
                          f"in this interpreter ({exc})"}
    return document_convert.extract_pages(path)


def extract_file(path) -> dict:
    """Extract one file to per-page text.

    Returns ``{"ok", "pages": [{"page", "text"}], "failed_pages",
    "reason", "detail"}``. ``ok`` is False with a named reason for
    an unsupported type, a missing file, an unreadable file, an
    unavailable PDF engine, or any page that could not be read.
    A failure is never an empty extraction passed off as success.
    """
    try:
        target = Path(str(path or "")).expanduser()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": f"not a usable path: {exc}"}
    if not target.is_file():
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": f"no such file: {target}"}
    suffix = target.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _extract_text_file(target)
    if suffix in PDF_SUFFIXES:
        return _extract_pdf_file(target)
    if suffix in OFFICE_SUFFIXES:
        return _extract_office_file(target)
    return {"ok": False, "pages": [], "failed_pages": [],
            "reason": f"unsupported file type {suffix!r} "
                      f"(text .txt/.md, PDF, and Office "
                      f".docx/.pptx/.xlsx only)"}


# ---------------------------------------------------------------------------
# The supplied-file registry
# ---------------------------------------------------------------------------

class CourseRegistry:
    """The ONLY source of course files: an explicit, listed set.

    A file enters the registry only through ``register()`` — an
    explicit action by the driver on a file he supplied. There is
    no directory scan anywhere in this module: ``register`` takes
    one path, and ``extract`` refuses any path that is not
    registered. Paths are resolved at registration, so the same
    file reached by a different relative path is the same entry.
    Session-scoped and in-memory (see the module docstring):
    ``to_data()`` / ``from_data()`` let the session driver persist
    the list as its own session state.
    """

    def __init__(self):
        self._entries: dict[str, dict] = {}
        self._extractions: dict[str, dict] = {}

    def register(self, path, lecture: str) -> dict:
        """Register one supplied file. Returns the entry (a dict).

        ``{"ok", "file", "lecture", "pages", "reason"}`` — ``pages``
        is the page count when the file could be extracted at
        registration, else None with the reason named (e.g. the PDF
        engine is unavailable on this interpreter — the file is
        still registered; extraction is retried on first use).
        A missing file, a directory, or an unsupported type is a
        refused registration, never a silent entry. Re-registering
        the same file with the same lecture is idempotent; with a
        different lecture it is refused (one file, one lecture).
        """
        if not isinstance(lecture, str) or not lecture.strip():
            return {"ok": False, "file": "", "lecture": "",
                    "pages": None,
                    "reason": "a lecture label is required"}
        try:
            target = Path(str(path or "")).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "file": "", "lecture": "",
                    "pages": None, "reason": f"not a usable path: {exc}"}
        key = str(target)
        if key in self._entries:
            if self._entries[key]["lecture"] != lecture.strip():
                return {"ok": False, "file": key,
                        "lecture": lecture.strip(), "pages": None,
                        "reason": f"{target.name} is already registered "
                                  f"under "
                                  f"{self._entries[key]['lecture']!r}"}
            return dict(self._entries[key])
        if not target.is_file():
            return {"ok": False, "file": key, "lecture": lecture.strip(),
                    "pages": None, "reason": f"no such file: {target}"}
        extracted = extract_file(target)
        entry = {"ok": True, "file": key, "lecture": lecture.strip(),
                 "pages": (len(extracted["pages"])
                           if extracted["ok"] else None),
                 "reason": ("" if extracted["ok"]
                            else extracted["reason"])}
        self._entries[key] = entry
        if extracted["ok"]:
            self._extractions[key] = extracted
        return dict(entry)

    def is_registered(self, path) -> bool:
        try:
            return str(Path(str(path or "")).expanduser().resolve()) \
                in self._entries
        except Exception:  # noqa: BLE001
            return False

    def get(self, path):
        """The registry entry for a path, or None when unregistered."""
        try:
            return dict(self._entries[str(
                Path(str(path or "")).expanduser().resolve())])
        except Exception:  # noqa: BLE001
            return None

    def list_files(self) -> list:
        """Every registered file, in registration order."""
        return [dict(entry) for entry in self._entries.values()]

    def extraction(self, path) -> dict:
        """The per-page extraction for a registered file.

        Refuses any path outside the registry — the file is never
        read. A registered file whose extraction failed (or was
        never attempted, e.g. the PDF engine was unavailable at
        registration) is extracted on first use and the result is
        named honestly, never an empty success.
        """
        try:
            key = str(Path(str(path or "")).expanduser().resolve())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "pages": [], "failed_pages": [],
                    "reason": f"not a usable path: {exc}"}
        if key not in self._entries:
            return {"ok": False, "pages": [], "failed_pages": [],
                    "reason": f"not in the supplied course files: "
                              f"{Path(key).name} (register it first)"}
        if key in self._extractions:
            return self._extractions[key]
        extracted = extract_file(key)
        if extracted["ok"]:
            self._extractions[key] = extracted
        return extracted

    def to_data(self) -> list:
        """The registry as plain data (file + lecture per entry)."""
        return [{"file": e["file"], "lecture": e["lecture"]}
                for e in self._entries.values()]

    @classmethod
    def from_data(cls, data) -> "CourseRegistry":
        """Rebuild a registry from ``to_data()`` output.

        Re-registration re-reads each file: a file that no longer
        exists is skipped and named in the returned ``(registry,
        skipped)`` — never silently dropped, never invented.
        """
        registry = cls()
        skipped = []
        for item in (data or []):
            if not isinstance(item, dict):
                skipped.append({"item": item, "reason": "not an object"})
                continue
            entry = registry.register(item.get("file"),
                                      item.get("lecture") or "")
            if not entry["ok"]:
                skipped.append({"file": item.get("file"),
                                "reason": entry["reason"]})
        return registry, skipped


# ---------------------------------------------------------------------------
# The grounded bite + the source check
# ---------------------------------------------------------------------------

def ground_bite(bite, source, registry: CourseRegistry) -> object:
    """Attach a source pointer to a bite. Returns the new bite.

    ``bite`` is a U2 ``Bite`` (frozen — a new bite is returned, the
    old one untouched); ``source`` is a ``Source`` or its dict;
    ``registry`` is the supplied-file registry the source must
    point into. The source must point at a REGISTERED file — an
    unregistered file raises ValueError, never attaches. The
    passage is NOT verified here: verification is ``check_bite``'s
    job, run before the bite is taught. A bite whose source fails
    the check is "not grounded", never shown with a citation.
    """
    from core.brain.lesson_planner import Bite  # local: no import cycle
    from dataclasses import replace as _replace

    if isinstance(source, dict):
        parsed, error = Source.from_dict(source)
        if parsed is None:
            raise ValueError(f"unusable source: {error}")
        source = parsed
    if not isinstance(source, Source):
        raise ValueError("source must be a Source or its dict")
    if not registry.is_registered(source.file):
        raise ValueError("source file is not in the supplied course "
                         "files — register it first")
    return _replace(bite, source=source.to_dict())


def verify_source(source, registry: CourseRegistry) -> dict:
    """Does this source point at real extracted text?

    Returns ``{"grounded", "reason"}``. Grounded only when the
    file is registered, the cited page exists in its extraction,
    and the passage (whitespace-normalised) is found ON THAT PAGE.
    A passage found on a different page does not ground the bite.
    """
    if isinstance(source, dict):
        parsed, error = Source.from_dict(source)
        if parsed is None:
            return {"grounded": False,
                    "reason": f"not grounded: {error}"}
        source = parsed
    if not isinstance(source, Source):
        return {"grounded": False,
                "reason": "not grounded: no usable source"}
    if not registry.is_registered(source.file):
        return {"grounded": False,
                "reason": "not grounded: the cited file is not in "
                          "the supplied course files"}
    extracted = registry.extraction(source.file)
    if not extracted["ok"]:
        return {"grounded": False,
                "reason": f"not grounded: the cited file could not "
                          f"be read ({extracted['reason']})"}
    pages = {p["page"]: p["text"] for p in extracted["pages"]}
    if source.page not in pages:
        return {"grounded": False,
                "reason": f"not grounded: page {source.page} does "
                          f"not exist in {Path(source.file).name} "
                          f"({len(pages)} page(s) read)"}
    if _norm(source.passage) not in _norm(pages[source.page]):
        return {"grounded": False,
                "reason": f"not grounded: the cited passage is not "
                          f"on page {source.page} of "
                          f"{Path(source.file).name}"}
    return {"grounded": True,
            "reason": f"grounded: {format_source(source)}"}


def check_bite(bite, registry: CourseRegistry) -> dict:
    """The pre-teach source check for one bite.

    Returns ``{"grounded", "reason", "source"}``. A bite with no
    source is "not in your course files" — never taught from
    general knowledge dressed as a citation. A bite whose source
    fails verification is "not grounded" — never shown with a
    citation. Only a verified source is grounded.
    """
    source = getattr(bite, "source", None)
    if source is None:
        return {"grounded": False,
                "reason": "not in your course files",
                "source": None}
    result = verify_source(source, registry)
    return {"grounded": result["grounded"], "reason": result["reason"],
            "source": source}
