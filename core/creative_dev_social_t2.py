"""Triage Wire Build 2 — Creative / Developer / Social top candidates
(#4-#7) as live tools, without modifying the leaves themselves
(edition precedent: leaves take no cross-module imports; this
coordinator imports them, the same split as ``core/study_flow_t1.py``):

* ``creative_rights_ledger`` — rights ledger for creative outputs. Live
  path: a check + record tool. There is NO creative export
  pipeline in the tree to gate (verified by grep: no handler
  calls ``check_export``), so this build does NOT invent one.
  Instead the ledger is wired as the gate itself: any future
  export path must call ``gate_export`` first, and a verdict
  other than ALLOW refuses the export. A BLOCK is a refusal,
  a WARN is "re-verify first" — also not cleared.
* ``creative_subtitle_edit`` — native SRT/VTT parse + retime / restyle /
  sync, plus the timeline spine exported as OTIO JSON. Stdlib
  only; pysubs2 is NOT depended on. No media is touched and no
  renderer is driven (``cr_render_v576`` stays PARKED).
* ``developer_codebase_qa`` — static, local-only codebase Q&A.
  The scanner parses source text; it never imports, executes,
  or dynamically loads the scanned project. Every factual
  answer carries file:line citations from the leaf's own
  signature/call extraction; an answer with no citation is
  refused as verified (never a citation invented).
* ``social_vcard`` — native vCard 3.0 import/export, stdlib
  only; vobject is explicitly NOT vendored or depended on.
  One card maps to one person record; no merging, no inferred
  relationship (the leaf's rules).

Honesty limits, stated not hidden:

* The rights ledger lives in its OWN file (the leaf's guard
  refuses ``assistant.db``); the default here is
  ``data/rights_ledger.db`` (separate file, frozen memory
  schema/cap untouched). Tests pass an explicit ledger path.
* ``creative_rights_add`` writes that ledger file, so it is
  confirm-gated (write parity) with ``ledger_path`` as its
  write destination; ``creative_subtitle_export`` and
  ``vcard_export`` write caller-chosen files and are
  confirm-gated the same way, with ``output_path``. With no
  confirm handler the registry gate refuses without writing
  anything. The pure tools (check, subtitle edit, timeline,
  codebase Q&A, vCard import) write nothing and are read-only.
* Subtitle timings are never guessed: retime/sync apply the
  caller's shift/scale/target exactly (leaf clamps at zero);
  a malformed subtitle block raises, never silently drops a
  cue. VTT output is formatted here from the parsed cues
  (the leaf formats SRT only); the times are the parsed /
  retimed times, never re-derived.
* Codebase Q&A scans only ``.py`` (ast) and, for other
  languages, the leaf's regex fallback — never an import.
  Hidden dirs, ``__pycache__``, ``.venv`` and ``node_modules``
  are skipped; at most 500 files are scanned per call, so a
  huge tree cannot hang a goal. A question that names no
  scanned symbol yields no citations and is refused as
  verified, not answered from general knowledge.
* vCard round-trip fidelity is name + phones + emails (+ the
  other properties verbatim, since the leaf preserves unknown
  properties). ``N`` is built from the full name by splitting
  on the last space (documented heuristic); ``FN`` always
  carries the full name, so a round-trip through ``FN`` is
  lossless even when the split is imperfect.
* Field proof on HIS machine (a real export refused on a
  missing licence, a real retimed subtitle he can play, a
  real codebase answer checked against its files, a real
  contact round-tripping in his contacts) is owed after the
  BB101 quiz and is not claimed here.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_DEFAULT_LEDGER_FILENAME = "rights_ledger.db"
_MAX_SCAN_FILES = 500
_SCAN_EXTENSIONS = (".py", ".js", ".ts", ".go", ".rs", ".java", ".c",
                    ".cpp", ".h")
_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".git"}


def _data_dir() -> Path:
    try:
        from .config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR)
    except Exception:  # noqa: BLE001 — config import must not break the store
        return Path(__file__).resolve().parent.parent / "data"


def _default_ledger_path() -> str:
    return str(_data_dir() / _DEFAULT_LEDGER_FILENAME)


# ---------------------------------------------------------------------------
# Rights ledger (creative_rights_ledger) — check + record, gate for exports.
# ---------------------------------------------------------------------------

def check_rights(ledger_path, asset_id, purpose="commercial",
                 export_kind="composite", today=None):
    """The leaf's pre-export verdict, verbatim. Does not export
    anything and does not upgrade any licence."""
    from . import creative_rights_ledger as _ledger

    path = str(ledger_path or _default_ledger_path())
    return _ledger.check_export(path, int(asset_id), purpose=purpose,
                                export_kind=export_kind, today=today)


def gate_export(ledger_path, asset_id, purpose="commercial",
                export_kind="composite", today=None):
    """The export gate itself: returns the verdict only when it
    is ALLOW, otherwise raises ValueError with the leaf's
    reason. Any future export path must call this first; a
    BLOCK or a WARN is not a cleared export."""
    verdict = check_rights(ledger_path, asset_id, purpose=purpose,
                           export_kind=export_kind, today=today)
    if verdict.get("verdict") != "ALLOW":
        raise ValueError(
            "export refused: %s (%s)"
            % (verdict.get("verdict"), verdict.get("reason")))
    return verdict


def add_asset(ledger_path, source, licensor, licence_name, **kwargs):
    """Record one asset in the ledger (leaf's add_asset). The
    ledger records what the licensor SAID; it never upgrades an
    unknown licence to a permissive one."""
    from . import creative_rights_ledger as _ledger

    path = str(ledger_path or _default_ledger_path())
    return _ledger.add_asset(path, str(source), str(licensor),
                             str(licence_name), **kwargs)


def record_generated_asset(ledger_path, source, generator, plan_tier,
                           licence_name=None, **kwargs):
    """Generator-rule record (leaf): a free-tier asset without an
    explicitly recorded licence defaults to non-commercial; a
    paid-tier asset without one stays unverified (BLOCK)."""
    from . import creative_rights_ledger as _ledger

    path = str(ledger_path or _default_ledger_path())
    return _ledger.record_generated_asset(path, str(source), str(generator),
                                          str(plan_tier),
                                          licence_name=licence_name, **kwargs)


# ---------------------------------------------------------------------------
# Subtitles + timeline (creative_subtitle_edit) — pure transforms, one file writer.
# ---------------------------------------------------------------------------

def edit_subtitles(subtitles_text, shift=0.0, scale=1.0, strip_tags=False,
                   max_line_len=None, sync_first_start=None,
                   output_format="srt"):
    """Parse SRT/VTT text, retime / restyle / sync it, and format
    it back. Cue count and cue text are preserved by the leaf;
    only times (and, if asked, tags/line length) change. A
    malformed block raises — no cue is silently dropped."""
    from . import creative_subtitle_edit as _edit

    cues = _edit.parse_subtitles(str(subtitles_text))
    cues = _edit.retime(cues, shift=float(shift), scale=float(scale))
    if sync_first_start is not None:
        cues = _edit.sync_to(cues, float(sync_first_start))
    if strip_tags or max_line_len is not None:
        cues = _edit.restyle(cues, strip_tags=bool(strip_tags),
                             max_line_len=max_line_len)
    fmt = str(output_format or "srt").lower()
    if fmt == "srt":
        text = _edit.format_srt(cues)
    elif fmt == "vtt":
        text = _format_vtt(cues)
    else:
        raise ValueError("output_format must be 'srt' or 'vtt', got %r."
                         % (output_format,))
    return {"cues": cues, "text": text, "format": fmt}


def _format_vtt(cues):
    """WEBVTT text from parsed cues, using the leaf's VTT time
    shape (HH:MM:SS.mmm). Times are the cues' own times."""
    from . import creative_subtitle_edit as _edit

    parts = ["WEBVTT", ""]
    for cue in cues:
        parts.append("%d" % cue["index"])
        parts.append("%s --> %s"
                     % (_edit._format_time(cue["start"], vtt=True),
                        _edit._format_time(cue["end"], vtt=True)))
        parts.append(cue["text"])
        parts.append("")
    return "\n".join(parts)


def write_subtitles(subtitles_text, output_path, **kwargs):
    """Edit subtitles (as edit_subtitles) and write the result
    to a caller-chosen file. The parent directory must already
    exist — the export never guesses a name or creates folders."""
    result = edit_subtitles(subtitles_text, **kwargs)
    path = str(output_path or "").strip()
    if not path:
        raise ValueError("output_path must not be empty.")
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise ValueError("output folder does not exist: %s" % parent)
    with open(path, "w", encoding="utf-8") as f:
        f.write(result["text"])
    return {"output_path": path, **result}


def build_timeline(name, fps=30, tracks_json=None):
    """Build a timeline spine from caller-supplied tracks and
    export it as an OTIO JSON string (leaf). No media is
    touched; the spine is a model and rendering stays PARKED
    (cr_render)."""
    from . import creative_subtitle_edit as _edit

    timeline = _edit.make_timeline(str(name), fps=int(fps))
    tracks = []
    if tracks_json:
        tracks = json.loads(tracks_json) if isinstance(tracks_json, str) \
            else list(tracks_json)
    if not isinstance(tracks, list):
        raise ValueError("tracks_json must be a JSON list of tracks.")
    for track_spec in tracks:
        if not isinstance(track_spec, dict):
            raise ValueError("each track must be an object.")
        track = _edit.add_track(timeline, str(track_spec.get("name", "")))
        for clip in (track_spec.get("clips") or []):
            if not isinstance(clip, dict):
                raise ValueError("each clip must be an object.")
            start = float(clip.get("start", 0))
            duration = float(clip.get("duration", 0))
            if start < 0 or duration < 0:
                raise ValueError("clip start/duration must not be negative.")
            _edit.add_clip(track, str(clip.get("id", "")),
                           str(clip.get("source", "")), start, duration,
                           transition=clip.get("transition"))
    return _edit.export_otio_json(timeline)


# ---------------------------------------------------------------------------
# Codebase Q&A (developer_codebase_qa) — static, local, cited or refused.
# ---------------------------------------------------------------------------

def _scan_codebase(root):
    """Walk a local directory for source files (skipping hidden
    dirs, caches, venvs, node_modules; at most 500 files) and
    extract signatures with the leaf. Returns (signatures,
    source_texts) where source_texts maps path -> text. Never
    imports or executes anything scanned."""
    from . import developer_codebase_qa as _code

    root_path = Path(str(root))
    if not root_path.is_dir():
        raise ValueError("codebase_root is not a directory: %s" % (root,))
    signatures = []
    source_texts = {}
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in sorted(filenames):
            if len(source_texts) >= _MAX_SCAN_FILES:
                break
            if not filename.lower().endswith(_SCAN_EXTENSIONS):
                continue
            path = os.path.join(dirpath, filename)
            try:
                text = Path(path).read_text(encoding="utf-8",
                                            errors="ignore")
            except OSError:
                continue
            source_texts[path] = text
            if filename.lower().endswith(".py"):
                signatures.extend(_code.extract_signatures(text, path))
            else:
                signatures.extend(
                    _code.extract_signatures_fallback(text, path))
        if len(source_texts) >= _MAX_SCAN_FILES:
            break
    return signatures, source_texts


def answer_codebase_question(codebase_root, question):
    """Answer a question about a local codebase with file:line
    citations. Symbols named in the question are located in the
    extracted signatures; their call sites are found with the
    leaf's structural search. If nothing in the question matches
    a scanned symbol, the answer is refused as verified (the
    leaf's cite() flagged shape) — general knowledge is never
    presented as a codebase fact, and no citation is invented."""
    from . import developer_codebase_qa as _code

    question = str(question or "").strip()
    if not question:
        raise ValueError("question must not be empty.")
    signatures, source_texts = _scan_codebase(codebase_root)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
    matched = []
    for sig in signatures:
        if sig["name"] in tokens:
            matched.append(sig)
    citations = []
    lines = []
    for sig in matched:
        cite = "%s:%s" % (sig["file"], sig["line"])
        citations.append(cite)
        lines.append("%s:%s %s %s"
                     % (sig["file"], sig["line"], sig["kind"],
                        sig["name"]))
        for path, text in source_texts.items():
            for call in _code.find_calls_shaped_like(text, sig["name"]):
                call_cite = "%s:%s" % (path, call["line"])
                if call_cite not in citations:
                    citations.append(call_cite)
    if not citations:
        flagged = _code.cite(question, [])
        return {"answer": "", "citations": [], "cited": False,
                "warning": flagged["warning"],
                "note": "no scanned symbol matched the question; "
                        "refused as verified rather than answered "
                        "from general knowledge"}
    answer = "Matched symbols:\n" + "\n".join(lines)
    cited = _code.cite(answer, citations)
    return {"answer": cited["answer"], "citations": cited["citations"],
            "cited": True, "warning": ""}


# ---------------------------------------------------------------------------
# vCard (social_vcard) — native import/export, one card = one person.
# ---------------------------------------------------------------------------

def import_vcards(vcard_text):
    """Parse vCard text into per-card people fields (leaf). A
    card naming nobody raises (leaf) — no person record is
    invented from it. No merging, no inferred relationship."""
    from . import social_vcard as _vcard

    cards = _vcard.parse_vcards(str(vcard_text))
    people = []
    for card in cards:
        fields = _vcard.to_people_fields(card)
        fields["phones"] = _vcard.card_all(card, "TEL")
        fields["emails"] = _vcard.card_all(card, "EMAIL")
        people.append(fields)
    return {"cards": len(cards), "people": people}


def _card_from_person(person):
    """A contact dict -> a leaf card dict. FN always carries the
    full name; N is a last-space split (documented heuristic).
    Only fields the caller supplied are written — nothing is
    enriched from the web or inferred."""
    if not isinstance(person, dict):
        raise ValueError("each contact must be an object.")
    name = str(person.get("name", "")).strip()
    if not name:
        raise ValueError("each contact needs a non-empty name.")
    if " " in name:
        given, family = name.rsplit(" ", 1)
    else:
        given, family = name, ""
    props = [("FN", {}, name),
             ("N", {}, "%s;%s;;;" % (family, given))]
    for phone in (person.get("phones") or []):
        props.append(("TEL", {}, str(phone)))
    for email in (person.get("emails") or []):
        props.append(("EMAIL", {}, str(email)))
    if person.get("note"):
        props.append(("NOTE", {}, str(person["note"])))
    if person.get("org"):
        props.append(("ORG", {}, str(person["org"])))
    if person.get("title"):
        props.append(("TITLE", {}, str(person["title"])))
    return {"properties": props}


def export_vcards(contacts_json, output_path):
    """Serialise caller-supplied contacts to a vCard file
    (leaf serialiser, VERSION:3.0). The parent directory must
    already exist. Returns the path and a re-import check:
    the written file is parsed back and its names/phones/
    emails must match the input, or the export raises."""
    from . import social_vcard as _vcard

    contacts = json.loads(contacts_json) if isinstance(contacts_json, str) \
        else list(contacts_json)
    if not isinstance(contacts, list) or not contacts:
        raise ValueError("contacts_json must be a non-empty JSON list.")
    cards = [_card_from_person(c) for c in contacts]
    text = "".join(_vcard.serialise_card(card) for card in cards)
    path = str(output_path or "").strip()
    if not path:
        raise ValueError("output_path must not be empty.")
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise ValueError("output folder does not exist: %s" % parent)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    # Re-import check: the written file must round-trip.
    check = import_vcards(text)
    for person_in, person_out in zip(contacts, check["people"]):
        if person_out["name"] != str(person_in.get("name", "")).strip():
            raise ValueError("vCard round-trip lost a name.")
        if person_out["phones"] != [str(p) for p in
                                    (person_in.get("phones") or [])]:
            raise ValueError("vCard round-trip lost a phone.")
        if person_out["emails"] != [str(e) for e in
                                    (person_in.get("emails") or [])]:
            raise ValueError("vCard round-trip lost an email.")
    return {"output_path": path, "cards": len(cards),
            "people": check["people"]}


# ---------------------------------------------------------------------------
# Tool handlers — plain JSON strings, never raising (skills-pack rule).
# ---------------------------------------------------------------------------

def _ok(payload):
    return json.dumps({"status": "ok", **payload})


def _err(message):
    return json.dumps({"status": "error", "message": str(message)})


def creative_rights_check_handler(ledger_path="", asset_id=0,
                                  purpose="commercial",
                                  export_kind="composite"):
    try:
        verdict = check_rights(ledger_path or None, asset_id,
                               purpose=purpose, export_kind=export_kind)
        return _ok({"verdict": verdict,
                    "cleared": verdict.get("verdict") == "ALLOW"})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def creative_rights_add_handler(ledger_path="", source="", licensor="",
                                licence_name="", licence_url=None,
                                attribution_required=False,
                                attribution_text=None,
                                standalone_sale_allowed=False,
                                territory=None, expiry_date=None,
                                permitted_uses=None, generator=None,
                                plan_tier=None):
    try:
        if not str(source).strip() or not str(licensor).strip() \
                or not str(licence_name).strip():
            raise ValueError(
                "source, licensor and licence_name must all be "
                "non-empty; an unknown licence is never upgraded.")
        asset_id = add_asset(
            ledger_path or None, source, licensor, licence_name,
            licence_url=licence_url,
            attribution_required=bool(attribution_required),
            attribution_text=attribution_text,
            standalone_sale_allowed=bool(standalone_sale_allowed),
            territory=territory, expiry_date=expiry_date,
            permitted_uses=permitted_uses, generator=generator,
            plan_tier=plan_tier)
        return _ok({"asset_id": asset_id})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def creative_subtitle_edit_handler(subtitles_text, shift=0.0, scale=1.0,
                                   strip_tags=False, max_line_len=None,
                                   sync_first_start=None,
                                   output_format="srt"):
    try:
        return _ok(edit_subtitles(
            subtitles_text, shift=shift, scale=scale,
            strip_tags=strip_tags, max_line_len=max_line_len,
            sync_first_start=sync_first_start,
            output_format=output_format))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def creative_subtitle_export_handler(subtitles_text, output_path,
                                     shift=0.0, scale=1.0,
                                     strip_tags=False, max_line_len=None,
                                     sync_first_start=None,
                                     output_format="srt"):
    try:
        return _ok(write_subtitles(
            subtitles_text, output_path, shift=shift, scale=scale,
            strip_tags=strip_tags, max_line_len=max_line_len,
            sync_first_start=sync_first_start,
            output_format=output_format))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def creative_timeline_handler(name, fps=30, tracks_json=None):
    try:
        return _ok({"otio": build_timeline(name, fps=fps,
                                           tracks_json=tracks_json)})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def dev_codebase_qa_handler(codebase_root, question):
    try:
        result = answer_codebase_question(codebase_root, question)
        if not result["cited"]:
            return _err(result["warning"] + ": " + result["note"])
        return _ok(result)
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def vcard_import_handler(vcard_text):
    try:
        return _ok(import_vcards(vcard_text))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def vcard_export_handler(contacts_json, output_path):
    try:
        return _ok(export_vcards(contacts_json, output_path))
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def register_all(registry) -> None:
    """Register the eight Triage Wire Build 2 tools. The three
    file/ledger writers are confirm-gated (see tools_gate.py);
    the five pure tools are read-only."""
    registry.register(
        name="creative_rights_check",
        description="Check a creative asset's rights in the rights ledger before any export or sale (ledger lives in its own file, never the memory store). Returns the ledger verdict verbatim (ALLOW, WARN, or BLOCK) plus cleared=true only for ALLOW. A BLOCK (unverified licence, non-commercial licence in a commercial export, standalone sale not allowed, no-derivatives in a composite) or a WARN (expiry/review passed, attribution text missing) is NOT a cleared export — do not export on it. No export pipeline exists in the app yet, so this check IS the gate: a future export path must call it first. Use before exporting or selling any creative asset.",
        parameters={"type": "object", "properties": {
            "ledger_path": {"type": "string", "description": "Optional ledger file path; omit to use the app's own rights ledger file"},
            "asset_id": {"type": "integer", "description": "Ledger asset id to check"},
            "purpose": {"type": "string", "enum": ["commercial", "non-commercial"], "description": "Export purpose (default commercial)"},
            "export_kind": {"type": "string", "enum": ["composite", "standalone"], "description": "Composite (inside a larger work) or standalone (sold/shipped on its own)"},
        }, "required": ["asset_id"]},
        handler=creative_rights_check_handler,
    )
    registry.register(
        name="creative_rights_add",
        description="Record a creative asset in the rights ledger with its source, licensor, and licence name exactly as the licensor stated it. The ledger never upgrades an unknown licence to a permissive one, and a free-tier generated asset without an explicitly recorded licence defaults to non-commercial. Writes the app's own ledger file (or the ledger_path given) and asks for confirmation first. Use when the user acquires or generates a creative asset and wants its rights recorded before any export.",
        parameters={"type": "object", "properties": {
            "ledger_path": {"type": "string", "description": "Optional ledger file path; omit to use the app's own rights ledger file"},
            "source": {"type": "string", "description": "Asset source (file, URL, or generator output description)"},
            "licensor": {"type": "string", "description": "Licensor name"},
            "licence_name": {"type": "string", "description": "Licence name exactly as stated by the licensor (e.g. cc-by-4.0, mit, apache-2.0, cc0-1.0); empty or unknown stays unverified and blocks export"},
            "licence_url": {"type": "string", "description": "Optional licence text URL"},
            "attribution_required": {"type": "boolean", "description": "Whether attribution is required"},
            "attribution_text": {"type": "string", "description": "Attribution text, when required"},
            "standalone_sale_allowed": {"type": "boolean", "description": "Whether the asset may be sold/shipped on its own"},
            "territory": {"type": "string", "description": "Optional territory restriction"},
            "expiry_date": {"type": "string", "description": "Optional expiry/review date (YYYY-MM-DD)"},
            "generator": {"type": "string", "description": "Optional generator name for generated assets"},
            "plan_tier": {"type": "string", "description": "Optional generator plan tier (free/paid) for generated assets"},
        }, "required": ["source", "licensor", "licence_name"]},
        handler=creative_rights_add_handler,
    )
    registry.register(
        name="creative_subtitle_edit",
        description="Edit subtitles natively (stdlib only, no pysubs2): parse SRT or VTT text, shift and/or scale cue times, sync the first cue to a target start, strip tags and/or cap line length, and format back to SRT or VTT. Cue count and cue text are preserved; times are the caller's shift/scale only, never guessed; a malformed block is an error, never a silently dropped cue. No file is written and no media is touched. Use when the user supplies subtitle text to retime or restyle.",
        parameters={"type": "object", "properties": {
            "subtitles_text": {"type": "string", "description": "SRT or VTT subtitle text"},
            "shift": {"type": "number", "description": "Seconds to shift all cues by (default 0)"},
            "scale": {"type": "number", "description": "Factor to scale all cue times by (default 1)"},
            "strip_tags": {"type": "boolean", "description": "Strip HTML-style tags from cue text"},
            "max_line_len": {"type": "integer", "description": "Optional maximum line length for restyling"},
            "sync_first_start": {"type": "number", "description": "Optional target start time (seconds) for the first cue"},
            "output_format": {"type": "string", "enum": ["srt", "vtt"], "description": "Output format (default srt)"},
        }, "required": ["subtitles_text"]},
        handler=creative_subtitle_edit_handler,
    )
    registry.register(
        name="creative_subtitle_export",
        description="Edit subtitles (same as creative_subtitle_edit) and write the result to a caller-chosen file in an existing folder. The file write asks for confirmation first. No media is touched and no renderer is driven. Use when the user wants an edited subtitle file saved.",
        parameters={"type": "object", "properties": {
            "subtitles_text": {"type": "string", "description": "SRT or VTT subtitle text"},
            "output_path": {"type": "string", "description": "File path to write, in an existing folder"},
            "shift": {"type": "number", "description": "Seconds to shift all cues by (default 0)"},
            "scale": {"type": "number", "description": "Factor to scale all cue times by (default 1)"},
            "strip_tags": {"type": "boolean", "description": "Strip HTML-style tags from cue text"},
            "max_line_len": {"type": "integer", "description": "Optional maximum line length for restyling"},
            "sync_first_start": {"type": "number", "description": "Optional target start time (seconds) for the first cue"},
            "output_format": {"type": "string", "enum": ["srt", "vtt"], "description": "Output format (default srt)"},
        }, "required": ["subtitles_text", "output_path"]},
        handler=creative_subtitle_export_handler,
    )
    registry.register(
        name="creative_timeline",
        description="Build a native timeline spine (clips, cuts, transitions as a JSON model) and export it as OTIO JSON, the neutral spine a video editor imports as a file. Takes a timeline name, frame rate, and tracks as a JSON list of {name, clips: [{id, source, start, duration, optional transition}]}. No media is touched and nothing is rendered — rendering is a separate, parked step. Use when the user wants a timeline model exported for an editor.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "Timeline name"},
            "fps": {"type": "integer", "description": "Frames per second (default 30)"},
            "tracks_json": {"type": "string", "description": "JSON list of tracks: [{name, clips: [{id, source, start, duration, transition}]}]"},
        }, "required": ["name"]},
        handler=creative_timeline_handler,
    )
    registry.register(
        name="dev_codebase_qa",
        description="Answer a question about a local codebase the user points at, statically and on-device only: the scanner parses source text and never imports, executes, or dynamically loads the scanned project, and no code leaves the machine. Every factual answer carries real file:line citations from the codebase itself; a question matching no scanned symbol is refused as verified rather than answered from general knowledge, and no citation is ever invented. Use when the user asks about their own code (a function, a class, or where something is called).",
        parameters={"type": "object", "properties": {
            "codebase_root": {"type": "string", "description": "Local directory containing the codebase to scan (existing directory; hidden dirs, caches, venvs and node_modules are skipped; at most 500 files scanned)"},
            "question": {"type": "string", "description": "Question naming the symbol(s) to locate, e.g. 'Where is parse_vcards defined and called?'"},
        }, "required": ["codebase_root", "question"]},
        handler=dev_codebase_qa_handler,
    )
    registry.register(
        name="vcard_import",
        description="Import vCard 3.0 text natively (stdlib only, vobject NOT used) into per-card people fields: name, phones, emails, and note (organisation/title as stated text). One card maps to one person; no cards are merged and no relationship is inferred. A card naming nobody is an error, never an invented person. No web enrichment. Use when the user supplies vCard text to import.",
        parameters={"type": "object", "properties": {
            "vcard_text": {"type": "string", "description": "vCard text (one or more BEGIN:VCARD blocks)"},
        }, "required": ["vcard_text"]},
        handler=vcard_import_handler,
    )
    registry.register(
        name="vcard_export",
        description="Export contacts to a vCard 3.0 file natively (stdlib only, vobject NOT used). Contacts are a JSON list of {name (required), phones, emails, note, org, title}; only fields supplied are written, nothing is enriched or inferred. The written file is parsed back and must round-trip every name, phone, and email, or the export fails. The file write asks for confirmation first. Use when the user wants contacts saved as a .vcf file.",
        parameters={"type": "object", "properties": {
            "contacts_json": {"type": "string", "description": "JSON list of contacts: {name, phones, emails, note, org, title}"},
            "output_path": {"type": "string", "description": "File path to write, in an existing folder"},
        }, "required": ["contacts_json", "output_path"]},
        handler=vcard_export_handler,
    )


__all__ = [
    "check_rights", "gate_export", "add_asset",
    "record_generated_asset", "edit_subtitles", "write_subtitles",
    "build_timeline", "answer_codebase_question", "import_vcards",
    "export_vcards", "register_all",
]
