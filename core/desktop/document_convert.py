"""B-D3b (2026-11-09) document -> Markdown on DIRECT libraries —
the ONE wrapper every Office-document conversion goes through.

History: B-D3 built this wrapper on markitdown (MIT). The parent
REJECTED markitdown as a runtime dependency on weight grounds
(2026-11-09 ~07:40 IST) and re-scoped the batch to direct
libraries — the wrapper's design, the U11 seam, the fixtures,
the 28 tests and the prover all carry over; ONLY the conversion
engine changed. The rejection, in the parent's words: markitdown
measured ~170 MB new installed weight (markitdown 1 + magika 4 +
onnxruntime 67 + pandas 80 + mammoth 1 + python-pptx 3 +
openpyxl 3 + lxml 12 + misc) vs the worker's "~40 MB"; 71 MB of
it (magika + onnxruntime) is NEVER invoked by VYUK's path (the
wrapper never calls the MarkItDown dispatcher) but IS loaded
into the process on first conversion, because ``import
markitdown`` runs ``__init__`` -> ``_markitdown`` ->
``import magika`` (module level) -> ``import onnxruntime``;
pandas 80 MB is required only by markitdown's xlsx converter
(``pd.read_excel(..., engine="openpyxl")``) while openpyxl (3
MB) reads sheets alone. The direct-library set below measures
~25 MB installed. A spec exclusion for magika/onnxruntime is
NOT viable (``import markitdown`` would fail at runtime); a
sys.modules stub for magika was considered and rejected as
fragile.

THE GAP (proved first in B-D3, file/line evidence)
-------------------------------------------------
There is NO document-to-Markdown conversion in the tree before
this module:
* Zero ``markitdown`` / ``mammoth`` / ``pptx`` / ``openpyxl``
  imports anywhere in the tree (grep over core/, gui/, tests/ —
  no hits at all before B-D3).
* U11's ``core/brain/course_grounding.py:192-193`` reads exactly
  two kinds of files: TEXT_SUFFIXES (".txt", ".md", ".markdown")
  and PDF_SUFFIXES (".pdf", via the lazy pypdf loader). Its
  ``extract_file`` returns "unsupported file type '.docx' (text
  .txt/.md and PDF only)" for a Word handout, a PowerPoint deck,
  or a spreadsheet.
* ``core/senses_file.py:16`` only LISTS ".docx"/".xlsx"/".pptx"
  as ZIP-container extensions for magic detection — it never
  reads their contents. No python-docx, no python-pptx, no
  openpyxl anywhere in core/ or gui/ (the only hits are
  extension lists in toolbox_tokens.py, gmail attachment
  handling, and a GUI confirm string).

Genuinely missing — what this module adds:
1. No Office readers: no Word/PowerPoint/Excel reading at all.
2. No format detection from bytes: nothing that can tell a .docx
   from a .pptx when the extension lies.
3. No conversion report: nothing that says what was converted,
   what was dropped, or what went wrong.
4. No single wrapper a driver (or U11's registry) can call.

LICENCE (read from the wheels at build time, 2026-11-09)
--------------------------------------------------------
Direct libraries, all read from their installed wheels'
METADATA in the Qt venv (no GPL anywhere in the set):
* docx: mammoth 1.13.0 (BSD-2-Clause, Copyright (c) 2013
  Michael Williamson — LICENSE file in the wheel) +
  markdownify 1.2.3 (MIT, Copyright 2012-2018 Matthew Tretter
  — LICENSE file in the wheel) + cobble 0.1.4 (BSD — METADATA
  classifier "License :: OSI Approved :: BSD License"; no full
  text in the wheel) + lxml 6.1.3 (BSD-3-Clause) +
  beautifulsoup4 4.15.0 (MIT) + six 1.17.0 (MIT).
* pptx: python-pptx 1.0.2 (MIT) + Pillow 13.0.0 (MIT-CMU,
  already in the tree) + XlsxWriter 3.3.1 (BSD-2-Clause) +
  lxml 6.1.3 (BSD-3-Clause) + typing-extensions (already in
  the tree).
* xlsx: openpyxl 3.1.5 (MIT) + et-xmlfile 2.0.0 (MIT).
WEIGHT (measured in the Qt venv, not estimated): the direct
set is ~25 MB installed (mammoth 1 + markdownify 1 +
beautifulsoup4 1 + lxml 12 + python-pptx 3 + openpyxl 3 +
et-xmlfile 1 + cobble 1 + XlsxWriter 2 + six ~0; Pillow and
typing-extensions already in the tree). The markitdown set it
replaces measured ~170 MB. Commercial use permitted for all.

DEPENDENCY DECISION — PROPOSED, parent rules (same shape as
SymPy in U10, PyWinCtl in B-D1, watchdog in B-D2)
--------------------
Use the INSTALLED packages as DECLARED dependencies:
``mammoth>=1.13,<2`` + ``markdownify>=1.2.3,<2`` +
``python-pptx>=1.0.2,<2`` + ``openpyxl>=3.1.5,<4`` +
``lxml>=6.1.3,<7`` in requirements.txt, pinned
``mammoth==1.13.0`` + ``markdownify==1.2.3`` +
``python-pptx==1.0.2`` + ``openpyxl==3.1.5`` +
``lxml==6.1.3`` in requirements.lock. Installed in .venv-qt
only — system python3 has openpyxl 3.1.2 and lxml 5.2.1 (and
pandas 2.1.4, unused here) but NO mammoth / python-pptx /
markdownify / beautifulsoup4, so docx and pptx degrade
per-format there while xlsx still converts. PEP 668 blocks
pip on system python3 and --break-system-packages was NOT
used. The wrapper degrades per format to engine_unavailable.
REJECTED: markitdown (weight — see above; the parent's ruling,
not a licence question — its licences were clean); vendoring
the converters (a second copy that drifts); re-implementing
the Office readers in-tree (the tree has NO Office readers —
a home-grown docx/pptx/xlsx reader would be a worse,
format-split re-implementation of exactly what mammoth /
python-pptx / openpyxl do).

LOPHOLE FAMILY — "a document that converts wrong and nobody knows"
-------------------------------------------------------------------
* A file whose format is mis-detected (a .docx that is really
  a zip of something else; a .pptx renamed .docx).
* A conversion that silently drops content (a table that becomes
  a paragraph, a slide's notes lost, images dropped with no word).
* A conversion that succeeds on garbage (a corrupt file that
  yields empty Markdown reported as a success).
* A huge file that blocks the machine (a 500-page document
  converted synchronously in the main thread).
* A converted document that is then grounded as if it were the
  original (U11's source attribution naming a derived Markdown
  file that does not exist on his machine).

ONE DESIGN (this module, all conversions through it)
---------------------------------------------------
* The format is detected from the file's own bytes, never from
  the extension alone: the first four bytes must be the ZIP
  magic (PK 03 04), then the zip's own part list decides —
  word/document.xml is Word, ppt/presentation.xml is
  PowerPoint, xl/workbook.xml is Excel. A file whose extension
  disagrees with its bytes is converted AS ITS BYTES SAY, and
  the mismatch is a WARNING in the report, never silent.
* Every conversion returns the Markdown PLUS a report: format
  detected, units converted (1 document / N slides / N sheets),
  warnings (extension mismatch, images embedded as data URIs
  or not converted to viewable content, no slide or sheet
  markers found so the output came back as one page).
  Empty Markdown is a FAILURE ("conversion produced no text"),
  never a success. A corrupt or unreadable file is an honest
  failure with the reason (the converters raise BadZipFile /
  ValueError / KeyError on garbage — probed 2026-11-09 — and the
  wrapper reports the exception type, never an empty success).
* A size guard (MAX_BYTES, 50 MB — a lecture handout is KBs;
  the cap bounds the synchronous work) refuses oversize files
  BEFORE reading them. The wrapper is synchronous and owns no
  thread: the driver runs it off the main thread when it matters.
* The wrapper NEVER rewrites the source: it opens the file
  read-only, and the returned source pointer names the ORIGINAL
  resolved file + the unit inside it (page 1 for a document,
  slide N for a deck, sheet 'Name' for a workbook) — never a
  derived file. U11's registry records the original file, and
  ground_bite's source pointer still names the original.
* PDF is REFUSED here, on purpose: PDFs stay on U11's pypdf
  path — per-page grounding — because a flat Markdown
  conversion has no page boundaries, which would break the
  per-page source check. Legacy .doc / .xls (OLE, not OOXML)
  are refused as unsupported — there is no .doc converter in
  the direct set and .xls needs xlrd, which is not declared.
* Each format calls its library DIRECTLY — mammoth (+
  markdownify) for docx, the in-tree slide walker over
  python-pptx for pptx, openpyxl for xlsx. Availability is
  PER FORMAT: a missing mammoth degrades ONLY docx to
  engine_unavailable, never pptx/xlsx (better than the
  markitdown design, which was all-or-nothing). Pages are
  split on markers the converters themselves emit:
  "<!-- Slide number: N -->" for decks, "## <SheetName>"
  headings for workbooks; a document is one page. A
  marker-less output falls back to one page WITH a warning,
  never an invented split.

KNOWN CONVERTER QUIRKS (documented, not hidden)
-----------------------------------------------
* mammoth renders a docx table with an EMPTY header row
  ("|  |  |" above the real header). The text is all present;
  the table shape has one empty first row. Probed 2026-11-09:
  mammoth's HTML table has plain <td> cells and markdownify
  emits an empty header row above them — the direct
  mammoth-HTML -> markdownify path reproduces the B-D3
  (markitdown) output byte-for-byte on the fixture.
* Images: mammoth EMBEDS docx images as base64 data URIs in
  the Markdown (probed 2026-11-09 — the image is in the output,
  not dropped). The pptx walker only REFERENCES slide images
  by filename ("![img.png](Picture3.jpg)") — a dangling link,
  the image itself is not in the output (same as markitdown's
  PptxConverter with keep_data_uris off). xlsx drawings are
  ignored entirely. The wrapper counts media parts in the zip
  against data-URI embeddings in the output and reports per
  conversion which of the two happened — it never claims an
  image was dropped when it was embedded, or vice versa.
* Speaker notes ARE included by the pptx walker, under
  "### Notes:" inside their slide's chunk (same as
  markitdown's PptxConverter).
* The pptx walker reproduces markitdown's PptxConverter
  OBSERVABLE behavior — slide markers, title as "# ...",
  shape text in (top, left) order, tables as Markdown tables
  (first row = header), pictures as filename references,
  charts as "### Chart ..." or "[unsupported chart]", groups
  recursed in order, notes under "### Notes:" — not its code.
  One documented simplification: a table cell containing "|"
  or a newline is escaped (``\\|`` / space) so the table stays
  one line per row; the text is all present.

REJECTED ALTERNATIVES
---------------------
* Extension-only detection — a renamed file converts wrong.
  Rejected: magic bytes + part list.
* Silent drops — a table or image lost with no warning.
  Rejected: the report names every detected drop.
* Empty Markdown as success — a corrupt file looks converted.
  Rejected: empty output is a failure.
* Synchronous conversion of unbounded files — the machine
  blocks. Rejected: the 50 MB guard + the driver owns threads.
* Grounding against the conversion — the citation points at a
  derived file. Rejected: the source pointer names the original.
* markitdown as the engine — ~170 MB for three formats the
  direct libraries serve for ~25 MB (parent ruling, B-D3b).
  Rejected: direct libraries.
* The MarkItDown dispatcher — its magika detection would decide
  the format. Rejected: the wrapper detects, the libraries
  convert. (There is no dispatcher in the direct set at all.)
* Vendoring the converters / an old puremagic-era pin — see the
  weight note above.
* A sys.modules stub for magika — fragile, breaks silently if
  markitdown ever touches magika at import time. Rejected.
* A spec exclusion for magika/onnxruntime — ``import markitdown``
  would fail at runtime and the wrapper would degrade to
  engine_unavailable on the shipped product. Rejected.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

# Maximum file size converted: 50 MB. A lecture handout is KBs;
# the cap bounds the synchronous converter work. Refused BEFORE
# the file is read.
MAX_BYTES = 50 * 1024 * 1024

# The formats this wrapper converts (OOXML only). PDF stays on
# U11's pypdf path; legacy OLE .doc/.xls are refused.
SUPPORTED_SUFFIXES = (".docx", ".pptx", ".xlsx")
SUPPORTED_FORMATS = ("docx", "pptx", "xlsx")

_ZIP_MAGIC = b"PK\x03\x04"

# zip part -> format. The part list is the file's own truth about
# what it is; the extension is only a hint.
_PART_MARKERS = (
    ("word/document.xml", "docx"),
    ("ppt/presentation.xml", "pptx"),
    ("xl/workbook.xml", "xlsx"),
)

# Media part prefixes — an image part means the converter dropped
# an image (text only); the report must say so.
_MEDIA_PREFIXES = ("word/media/", "ppt/media/", "xl/media/")

# Markers the converters themselves emit, which the page split
# reads back. pptx: "<!-- Slide number: N -->". xlsx: a "## Name"
# heading per sheet.
_SLIDE_MARKER = re.compile(r"^<!-- Slide number: (\d+) -->\s*$")
_SHEET_HEADING = re.compile(r"^## (.+?)\s*$")

# Per-format libraries (import names). docx needs mammoth AND
# markdownify (mammoth renders HTML; markdownify turns it into
# Markdown — the same observable shape markitdown's DocxConverter
# produced). pptx needs python-pptx only (the slide walker is
# in-tree). xlsx needs openpyxl only.
_FORMAT_LIBS = {
    "docx": ("mammoth", "markdownify"),
    "pptx": ("pptx",),
    "xlsx": ("openpyxl",),
}

# Human names for the report's "converter" field.
_FORMAT_LABEL = {
    "docx": "mammoth",
    "pptx": "python-pptx",
    "xlsx": "openpyxl",
}


class _EngineUnavailable(Exception):
    """Raised when a format's library cannot be imported.
    Carries the underlying error text so the caller can report
    it honestly instead of guessing."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ----------------------------------------------------------------
# Lazy per-format loaders (the sympy pattern: function-level
# static imports)
# ----------------------------------------------------------------
# The packaging checker only derives import_module/__import__
# targets, so no spec/checker entry is needed for these static
# imports — the frozen build picks the libraries up through
# PyInstaller's static analysis of the import statements.
# Importing THIS module never touches any of them. Availability
# is PER FORMAT: a missing mammoth degrades ONLY docx, never
# pptx/xlsx.

_LOADED = {}   # fmt -> (converter callable, label)
_LOAD_ERROR = {}   # fmt -> error detail string


def _dist_version(dist_name: str):
    """Installed version of a distribution, or None when the
    metadata cannot be read (the label falls back to the bare
    library name — the version is decoration, never a gate)."""
    try:
        from importlib.metadata import version
        return version(dist_name)
    except Exception:  # noqa: BLE001
        return None


def _load_format(fmt: str):
    """(converter, label) for one format, or raise
    _EngineUnavailable naming the missing library. Cached per
    format — a failed load is remembered too, so a second call
    does not re-import."""
    if fmt in _LOADED:
        return _LOADED[fmt]
    if fmt in _LOAD_ERROR:
        raise _EngineUnavailable(_LOAD_ERROR[fmt])
    try:
        if fmt == "docx":
            import mammoth
            import markdownify
            converter = _convert_docx
            ver_m = _dist_version("mammoth")
            ver_d = _dist_version("markdownify")
            label = ("mammoth"
                     + (f" {ver_m}" if ver_m else "")
                     + " + markdownify"
                     + (f" {ver_d}" if ver_d else ""))
        elif fmt == "pptx":
            import pptx  # noqa: F401 — the walker imports it too
            converter = _convert_pptx
            ver = _dist_version("python-pptx")
            label = "python-pptx" + (f" {ver}" if ver else "")
        elif fmt == "xlsx":
            import openpyxl  # noqa: F401
            converter = _convert_xlsx
            ver = _dist_version("openpyxl")
            label = "openpyxl" + (f" {ver}" if ver else "")
        else:
            raise _EngineUnavailable(
                f"unknown format {fmt!r}")
    except _EngineUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — ANY import failure
        # (ImportError, ...) is an unavailable engine, not a
        # crash. The missing library is named in the detail.
        missing = ", ".join(_FORMAT_LIBS.get(fmt, (fmt,)))
        _LOAD_ERROR[fmt] = (
            f"{fmt} engine unavailable: could not import "
            f"{missing} ({type(e).__name__}: {e})")
        raise _EngineUnavailable(_LOAD_ERROR[fmt]) from e
    _LOADED[fmt] = (converter, label)
    return _LOADED[fmt]


def _format_available(fmt: str) -> bool:
    """True when one format's libraries import cleanly."""
    try:
        _load_format(fmt)
    except _EngineUnavailable:
        return False
    return True


def engine_available(fmt=None) -> bool:
    """True when a format's libraries import cleanly (``fmt``
    given), or when ANY format is available (``fmt`` None).
    False on system python3 for docx/pptx (no mammoth /
    python-pptx there) — and on a machine without the install.
    xlsx stays available there: system python3 ships openpyxl."""
    if fmt is not None:
        return _format_available(fmt)
    return any(_format_available(f) for f in SUPPORTED_FORMATS)


def engine_status(fmt=None) -> dict:
    """Honest engine report: {"available": bool, "backend":
    "direct" | None, "formats": {fmt: bool}, "error": str |
    None}. ``available`` is the named format's availability
    when ``fmt`` is given, else whether ANY format works."""
    formats = {f: _format_available(f) for f in SUPPORTED_FORMATS}
    if fmt is not None:
        available = formats.get(fmt, False)
    else:
        available = any(formats.values())
    if available:
        return {"available": True, "backend": "direct",
                "formats": formats, "error": None}
    missing = [f for f, ok in formats.items() if not ok]
    return {"available": False, "backend": None,
            "formats": formats,
            "error": "no direct document library available: "
                     + ", ".join(
                         f"{f} needs "
                         + "+".join(_FORMAT_LIBS[f])
                         for f in missing)}


def supported_formats() -> tuple:
    """The formats this wrapper converts: ("docx", "pptx",
    "xlsx"). PDF is deliberately absent (pypdf path)."""
    return SUPPORTED_FORMATS


# ----------------------------------------------------------------
# Per-format converters (direct libraries, no dispatcher)
# ----------------------------------------------------------------

def _convert_docx(fh) -> str:
    """A .docx file object -> Markdown, via mammoth HTML +
    markdownify (ATX headings). Probed 2026-11-09: this path
    reproduces the B-D3 (markitdown) output byte-for-byte on
    the fixture, including the empty-header-row table quirk
    and the data-URI image embedding."""
    import mammoth
    import markdownify
    html = mammoth.convert_to_html(fh).value
    return markdownify.markdownify(html, heading_style="ATX")


def _pptx_is_picture(shape) -> bool:
    """Picture shapes: a PICTURE type, or a PLACEHOLDER carrying
    an image (guarded — ``shape.image`` raises ValueError for
    SVG placeholders without a raster fallback), or a shape
    carrying an embedded SVG blip."""
    import pptx
    if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE:
        return True
    if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.PLACEHOLDER:
        try:
            if shape.image is not None:
                return True
        except Exception:  # noqa: BLE001
            if _pptx_find_svg_blip_part(shape) is not None:
                return True
    return False


def _pptx_find_svg_blip_part(shape):
    """The image part referenced by an ``<asvg:svgBlip>``, if
    any. PowerPoint stores SVG pictures as a blip whose main
    ``r:embed`` points to a rasterized PNG fallback, plus an
    ``<asvg:svgBlip>`` extension pointing to the SVG. When
    there is no raster fallback the ``<a:blip>`` has no
    ``r:embed`` at all, so python-pptx's ``shape.image`` fails.
    This resolves the SVG part directly."""
    try:
        nsmap = {
            "asvg": "http://schemas.microsoft.com/office/drawing"
                    "/2016/SVG/main",
        }
        r_embed = ("{http://schemas.openxmlformats.org/"
                   "officeDocument/2006/relationships}embed")
        for svg_blip in shape._element.findall(
                ".//asvg:svgBlip", nsmap):
            embed_rid = svg_blip.get(r_embed)
            if not embed_rid:
                continue
            return shape.part.related_part(embed_rid)
    except Exception:  # noqa: BLE001
        pass
    return None


def _pptx_picture_markdown(shape) -> str:
    """A picture shape -> a filename reference
    ("![alt](Name.jpg)") — a dangling link, the image itself
    is not in the output (same as markitdown's PptxConverter
    with keep_data_uris off). Alt text comes from the shape's
    descr attribute, else the shape name."""
    alt_text = ""
    try:
        alt_text = shape._element._nvXxPr.cNvPr.attrib.get(
            "descr", "")
    except Exception:  # noqa: BLE001
        pass
    alt_text = ("\n".join(
        text for text in [alt_text] if text and text.strip())
        or shape.name)
    alt_text = re.sub(r"[\r\n\[\]]", " ", alt_text)
    alt_text = re.sub(r"\s+", " ", alt_text).strip()
    filename = re.sub(r"\W", "", shape.name) + ".jpg"
    return f"\n![{alt_text}]({filename})\n"


def _pptx_table_cell(text: str) -> str:
    """One table cell as Markdown: pipes and newlines are
    escaped so the table stays one line per row; the text is
    all present."""
    return (str(text).replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def _pptx_table_markdown(table) -> str:
    """A slide table -> a Markdown table (first row = header).
    Same observable shape as markdownify's rendering of
    markitdown's HTML table for plain-text cells."""
    rows = [[cell.text for cell in row.cells]
            for row in table.rows]
    if not rows:
        return ""
    header = ("| " + " | ".join(
        _pptx_table_cell(c) for c in rows[0]) + " |")
    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    lines = [header, sep]
    for row in rows[1:]:
        lines.append("| " + " | ".join(
            _pptx_table_cell(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


def _pptx_chart_markdown(chart) -> str:
    """A slide chart -> "### Chart" + a category x series
    table, or "[unsupported chart]" when the chart cannot be
    read (same as markitdown's PptxConverter)."""
    try:
        md = "\n\n### Chart"
        # ChartTitle.text_frame is documented as destructive —
        # it creates a text frame if one isn't already present,
        # so it never returns None. has_text_frame is the
        # property that actually reflects whether a text frame
        # exists.
        if chart.has_title and chart.chart_title.has_text_frame:
            md += f": {chart.chart_title.text_frame.text}"
        md += "\n\n"
        data = []
        category_names = [c.label for c in chart.plots[0].categories]
        series_list = list(chart.series)
        series_names = [s.name for s in series_list]
        data.append(["Category"] + series_names)
        # Materialize each series' values once. Accessing
        # series.values[idx] inside the nested loop is O(n^2)
        # in python-pptx (each lookup does an XPath scan of all
        # points), which is extremely slow on large charts.
        series_values = [list(s.values) for s in series_list]
        for idx, category in enumerate(category_names):
            row = [category]
            for sv in series_values:
                row.append(sv[idx] if idx < len(sv) else None)
            data.append(row)
        markdown_table = []
        for row in data:
            markdown_table.append(
                "| " + " | ".join(map(str, row)) + " |")
        header = markdown_table[0]
        separator = "|" + "|".join(["---"] * len(data[0])) + "|"
        return md + "\n".join(
            [header, separator] + markdown_table[1:])
    except ValueError as e:
        # Handle the specific error for unsupported chart types.
        if "unsupported plot type" in str(e):
            return "\n\n[unsupported chart]\n\n"
    except Exception:  # noqa: BLE001
        # Catch any other exceptions that might occur.
        return "\n\n[unsupported chart]\n\n"
    return "\n\n[unsupported chart]\n\n"


def _pptx_sorted_shapes(shapes):
    """Shapes in (top, left) order, None sorting first — the
    order markitdown's PptxConverter walked them in."""
    return sorted(
        shapes,
        key=lambda x: (
            float("-inf") if x.top is None else x.top,
            float("-inf") if x.left is None else x.left,
        ),
    )


def _pptx_shape_text(shape, title_shape) -> str:
    """One shape's Markdown ("" when the shape carries no
    text). Mirrors markitdown's PptxConverter: pictures and
    tables are independent ``if``s, charts are an ``if`` with
    text frames as the ``elif``, groups recurse in order."""
    import pptx
    out = ""
    # Pictures
    if _pptx_is_picture(shape):
        out += _pptx_picture_markdown(shape)
    # Tables
    if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.TABLE:
        out += _pptx_table_markdown(shape.table)
    # Charts
    if shape.has_chart:
        out += _pptx_chart_markdown(shape.chart)
    # Text areas
    elif shape.has_text_frame:
        text = shape.text or ""
        if shape == title_shape:
            if text.strip():
                out += "# " + text.lstrip() + "\n"
        else:
            out += text + "\n"
    # Group shapes
    if shape.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP:
        for subshape in _pptx_sorted_shapes(shape.shapes):
            out += _pptx_shape_text(subshape, title_shape)
    return out


def _convert_pptx(fh) -> str:
    """A .pptx file object -> Markdown, via the in-tree slide
    walker over python-pptx. Reproduces markitdown's
    PptxConverter OBSERVABLE behavior: "<!-- Slide number: N
    -->" markers, title as "# ...", shape text in (top, left)
    order, tables as Markdown tables, pictures as filename
    references, charts as "### Chart ..." or "[unsupported
    chart]", groups recursed, speaker notes under
    "### Notes:" inside their slide's chunk."""
    import pptx
    presentation = pptx.Presentation(fh)
    slide_texts = []
    slide_num = 0
    for slide in presentation.slides:
        slide_num += 1
        md = f"<!-- Slide number: {slide_num} -->\n"
        title = slide.shapes.title
        for shape in _pptx_sorted_shapes(slide.shapes):
            md += _pptx_shape_text(shape, title)
        md = md.strip()
        if slide.has_notes_slide:
            # PowerPoint attaches a notes slide to a slide whose
            # notes pane has merely been opened, so having one
            # says nothing about there being notes to read. Only
            # head a section that has content.
            notes_frame = slide.notes_slide.notes_text_frame
            notes_text = ((notes_frame.text or "")
                          if notes_frame is not None else "")
            if notes_text.strip():
                md += "\n\n### Notes:\n" + notes_text
                md = md.strip()
        slide_texts.append(md)
    return "\n\n".join(slide_texts)


def _xlsx_cell(value) -> str:
    """One spreadsheet cell as Markdown text: empty stays
    empty; numbers and dates render as str() (an integer cell
    reads 28, not 28.0); pipes and newlines are escaped so the
    table stays one line per row."""
    if value is None:
        return ""
    text = str(value)
    return (text.replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def _convert_xlsx(fh) -> str:
    """An .xlsx file object -> Markdown, via openpyxl directly
    (NO pandas — markitdown's xlsx converter read sheets
    through pd.read_excel, which is where its 80 MB came from).
    One "## <SheetName>" heading per sheet, then a Markdown
    table (first row = header). Fully-empty rows are skipped
    and trailing empty cells are trimmed, the way a reader
    drops all-NaN rows and columns."""
    import openpyxl
    wb = openpyxl.load_workbook(fh, data_only=True, read_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [_xlsx_cell(v) for v in row]
            while cells and cells[-1] == "":
                cells.pop()
            if cells:
                rows.append(cells)
        block = ["## " + ws.title]
        if rows:
            header = rows[0]
            block.append("| " + " | ".join(header) + " |")
            block.append("| " + " | ".join(
                ["---"] * len(header)) + " |")
            for row in rows[1:]:
                row = row + [""] * (len(header) - len(row))
                block.append("| " + " | ".join(row) + " |")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


# ----------------------------------------------------------------
# Format detection from the file's own bytes
# ----------------------------------------------------------------

def detect_format(path) -> dict:
    """What a file IS, from its bytes — never from its extension
    alone. Returns {"ok", "format" ("docx"|"pptx"|"xlsx"),
    "extension", "warnings", "media_count", "reason"}. A file
    whose extension disagrees with its bytes is still detected
    (the bytes win) with a mismatch warning. ``media_count`` is
    the number of image parts in the zip — the caller decides
    from the converted output whether they were embedded or
    dropped. ok False with a named reason for: a missing file,
    an oversize file, a non-ZIP file, a ZIP that is none of the
    three Office formats, or an unreadable zip."""
    try:
        target = Path(str(path or "")).expanduser()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "format": None, "extension": None,
                "warnings": [],
                "reason": f"not a usable path: {exc}"}
    if not target.is_file():
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"no such file: {target}"}
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"could not stat {target.name}: {exc}"}
    if size > MAX_BYTES:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"{target.name} is {size} bytes — over "
                          f"the {MAX_BYTES}-byte conversion limit"}
    if size == 0:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"{target.name} is empty"}
    try:
        with open(target, "rb") as fh:
            head = fh.read(4)
    except OSError as exc:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"could not read {target.name}: {exc}"}
    if head != _ZIP_MAGIC:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"{target.name} is not an Office document "
                          f"(not a ZIP container)"}
    try:
        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"{target.name} is not a readable ZIP "
                          f"archive: {exc}"}
    detected = None
    for part, fmt in _PART_MARKERS:
        if part in names:
            detected = fmt
            break
    if detected is None:
        return {"ok": False, "format": None,
                "extension": target.suffix.lower() or None,
                "warnings": [],
                "reason": f"{target.name} is a ZIP archive but not "
                          f"a Word, PowerPoint, or Excel document"}
    warnings = []
    ext = target.suffix.lower()
    if ext and ext != f".{detected}":
        warnings.append(
            f"extension {ext!r} disagrees with the file's bytes "
            f"(a {detected} document) — converted as {detected}")
    media = [n for n in names
             if n.startswith(_MEDIA_PREFIXES)]
    return {"ok": True, "format": detected, "extension": ext or None,
            "warnings": warnings,
            "media_count": len(media), "reason": ""}


# ----------------------------------------------------------------
# Page splitting on markers the converters themselves emit
# ----------------------------------------------------------------

def _split_pages(markdown: str, fmt: str) -> list:
    """The converted Markdown -> [{"page", "text", "unit"}].
    docx: one page ("document"). pptx: one page per
    "<!-- Slide number: N -->" marker (page = N, unit
    "slide N"). xlsx: one page per "## <SheetName>" heading
    (page = sheet order 1-based, unit "sheet '<name>'"). A
    marker-less output falls back to ONE page with the reason
    recorded by the caller — the split is never invented."""
    if fmt == "docx":
        return [{"page": 1, "text": markdown, "unit": "document"}]
    if fmt == "pptx":
        pages = []
        current_no = None
        current_lines = []
        for line in markdown.splitlines():
            m = _SLIDE_MARKER.match(line)
            if m:
                if current_no is not None:
                    pages.append({"page": current_no,
                                  "text": "\n".join(current_lines)
                                  .strip(),
                                  "unit": f"slide {current_no}"})
                current_no = int(m.group(1))
                current_lines = []
                continue
            current_lines.append(line)
        if current_no is not None:
            pages.append({"page": current_no,
                          "text": "\n".join(current_lines).strip(),
                          "unit": f"slide {current_no}"})
        elif markdown.strip():
            # No slide markers at all — one page, flagged by the
            # caller. (The converter normally always emits them.)
            pages.append({"page": 1, "text": markdown.strip(),
                         "unit": "document"})
        return pages
    # xlsx: one page per "## <SheetName>" heading.
    pages = []
    current_name = None
    current_lines = []
    for line in markdown.splitlines():
        m = _SHEET_HEADING.match(line)
        if m:
            if current_name is not None:
                pages.append({"page": len(pages) + 1,
                              "text": "\n".join(current_lines).strip(),
                              "unit": f"sheet {current_name!r}"})
            current_name = m.group(1)
            current_lines = []
            continue
        current_lines.append(line)
    if current_name is not None:
        pages.append({"page": len(pages) + 1,
                      "text": "\n".join(current_lines).strip(),
                      "unit": f"sheet {current_name!r}"})
    elif markdown.strip():
        pages.append({"page": 1, "text": markdown.strip(),
                     "unit": "document"})
    return pages


# ----------------------------------------------------------------
# Public API
# ----------------------------------------------------------------

def convert(path) -> dict:
    """Convert one Office document to Markdown.

    Returns {"ok", "markdown", "format", "report", "source",
    "reason"}. ``report`` is {"format", "extension",
    "extension_mismatch", "units": [{"page", "unit"}], "warnings",
    "converter"}. ``source`` is {"file": the ORIGINAL resolved
    path, "format", "units"} — the pointer a grounded bite cites,
    never a derived file. ok False with a named reason for: a
    missing/empty/oversize file, a non-Office file, an unsupported
    type (PDF, legacy .doc/.xls), an unavailable engine, a
    converter that raises, or an empty conversion. The source
    file is opened read-only and never rewritten."""
    try:
        resolved = str(Path(str(path or "")).expanduser().resolve())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "markdown": "", "format": None,
                "report": None, "source": None,
                "reason": f"not a usable path: {exc}"}
    detected = detect_format(resolved)
    if not detected["ok"]:
        # Bytes first: a file NAMED .pdf that IS a docx is
        # detected above and converts as docx. Only when the
        # bytes are NOT an Office document does the extension
        # decide which named refusal applies.
        ext0 = Path(resolved).suffix.lower()
        if ext0 == ".pdf":
            return {"ok": False, "markdown": "", "format": None,
                    "report": None, "source": None,
                    "reason": "PDFs stay on the pypdf path — "
                              "per-page grounding (a flat Markdown "
                              "conversion has no page boundaries)"}
        if ext0 in (".doc", ".xls"):
            return {"ok": False, "markdown": "", "format": None,
                    "report": None, "source": None,
                    "reason": f"legacy {ext0} (OLE) documents are "
                              f"not supported — save as {ext0}x "
                              f"first"}
        if ext0 in SUPPORTED_SUFFIXES:
            # An Office suffix whose bytes are NOT an Office
            # document is an unsupported type — the reason must
            # say so (U11's contract: extract_file names an
            # unsupported type with "unsupported"), with the
            # byte-level detail kept in parentheses.
            return {"ok": False, "markdown": "",
                    "format": None, "report": None, "source": None,
                    "reason": f"unsupported file type {ext0!r} "
                              f"({detected['reason']})"}
        return {"ok": False, "markdown": "",
                "format": detected["format"],
                "report": None, "source": None,
                "reason": detected["reason"]}
    fmt = detected["format"]
    ext = detected["extension"]
    try:
        converter, label = _load_format(fmt)
    except _EngineUnavailable as e:
        return {"ok": False, "markdown": "", "format": fmt,
                "report": None, "source": None,
                "reason": f"engine_unavailable: {e.detail}"}
    try:
        with open(resolved, "rb") as fh:
            markdown = converter(fh).strip()
    except Exception as exc:  # noqa: BLE001 — the converters
        # raise BadZipFile / ValueError / KeyError on garbage; any
        # of those is an honest failure, never an empty success.
        return {"ok": False, "markdown": "", "format": fmt,
                "report": None, "source": None,
                "reason": f"could not convert {Path(resolved).name} "
                          f"as {fmt}: {type(exc).__name__}: {exc}"}
    if not markdown:
        return {"ok": False, "markdown": "", "format": fmt,
                "report": None, "source": None,
                "reason": f"conversion of {Path(resolved).name} "
                          f"produced no text"}
    pages = _split_pages(markdown, fmt)
    warnings = list(detected["warnings"])
    # Images, decided from the OUTPUT, not the namelist alone:
    # mammoth embeds docx images as data URIs (they ARE in the
    # output); the pptx walker only references slide images
    # by filename and xlsx drawings are ignored. Count data-URI
    # embeddings against the media parts the zip actually holds.
    media_count = detected.get("media_count", 0)
    if media_count:
        embedded = markdown.count("data:image/")
        if embedded >= media_count:
            warnings.append(
                f"{media_count} image(s) embedded as data URIs "
                f"in the Markdown")
        else:
            warnings.append(
                f"{media_count} image(s) in the document are not "
                f"converted to viewable content (text only)")
    if fmt in ("pptx", "xlsx") and len(pages) == 1 \
            and pages[0]["unit"] == "document":
        warnings.append(
            f"no {'slide' if fmt == 'pptx' else 'sheet'} markers "
            f"in the converted output — returned as one page")
    units = [{"page": p["page"], "unit": p["unit"]} for p in pages]
    report = {
        "format": fmt,
        "extension": ext,
        "extension_mismatch": bool(
            ext and ext != f".{fmt}"),
        "units": units,
        "warnings": warnings,
        "converter": label,
    }
    source = {"file": resolved, "format": fmt, "units": units}
    return {"ok": True, "markdown": markdown, "format": fmt,
            "report": report, "source": source, "reason": ""}


def convert_to_file(path, out_path) -> dict:
    """Convert one Office document and write the Markdown to a
    file the DRIVER names (``out_path`` is required — the wrapper
    never invents a sibling file next to the user's original).
    Returns {"ok", "out", "format", "report", "reason"}. Refuses
    to overwrite the source file, and refuses when the
    conversion itself fails (no empty file is ever written)."""
    try:
        resolved = str(Path(str(path or "")).expanduser().resolve())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "out": None, "format": None,
                "report": None, "reason": f"not a usable path: {exc}"}
    try:
        out = str(Path(str(out_path or "")).expanduser())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "out": None, "format": None,
                "report": None,
                "reason": f"not a usable output path: {exc}"}
    if not str(out_path or "").strip():
        return {"ok": False, "out": None, "format": None,
                "report": None,
                "reason": "an output path is required — the wrapper "
                          "never invents one next to the source"}
    try:
        out_resolved = str(Path(out).resolve())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "out": None, "format": None,
                "report": None,
                "reason": f"not a usable output path: {exc}"}
    if out_resolved == resolved:
        return {"ok": False, "out": None, "format": None,
                "report": None,
                "reason": "the output path is the source file — "
                          "refusing to overwrite it"}
    converted = convert(resolved)
    if not converted["ok"]:
        return {"ok": False, "out": None,
                "format": converted["format"], "report": None,
                "reason": converted["reason"]}
    try:
        Path(out_resolved).write_text(converted["markdown"],
                                      encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "out": None,
                "format": converted["format"],
                "report": converted["report"],
                "reason": f"could not write {out_resolved}: {exc}"}
    return {"ok": True, "out": out_resolved,
            "format": converted["format"],
            "report": converted["report"], "reason": ""}


def extract_pages(path) -> dict:
    """Convert one Office document to U11's per-page extraction
    shape: {"ok", "pages": [{"page", "text", "unit"}],
    "failed_pages", "reason", "detail"}. The page number is the
    unit number inside the ORIGINAL file (slide N, sheet order,
    1 for a document) — the source pointer a grounded bite cites.
    A failure is never an empty extraction passed off as success
    (same contract as U11's extract_file)."""
    converted = convert(path)
    if not converted["ok"]:
        return {"ok": False, "pages": [], "failed_pages": [],
                "reason": converted["reason"], "detail": ""}
    pages = _split_pages(converted["markdown"], converted["format"])
    return {"ok": True,
            "pages": [{"page": p["page"], "text": p["text"],
                       "unit": p["unit"]} for p in pages],
            "failed_pages": [], "reason": "",
            "detail": converted["report"]}
