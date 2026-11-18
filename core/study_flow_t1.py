"""Triage Wire Build 1 — Study top 3 as one live flow (stdlib only).

Wires the three Study leaves the triage report ranked first
(``wiring_triage.md`` Study section, Top 10 #1-#3) into user
actions, without modifying the leaves themselves (edition
precedent: leaves take no cross-module imports; this coordinator
imports them):

* ``st_deck`` — Anki ``.apkg`` export (``write_apkg``).
* ``st_schedule`` — FSRS-6 scheduler (``Scheduler.review_card``).
* ``st_practice`` — original practice from his own materials
  (``generate_set``), with provenance on every problem.

One flow, in order: practice -> schedule -> deck. A practice set
is composed from materials the caller supplies; every practised
card gets a schedule entry keyed by its deck GUID (stable across
regeneration, so a regenerated deck does not reset review
progress); the deck is then exported with its provenance in the
deck description. A card's next review is computed by FSRS-6,
never by a guessed interval.

Honesty limits, stated not hidden:

* Source enforcement is inherited, never weakened: a deck card
  without a non-empty Source is refused (``st_deck`` raises
  ``SourceMissingError``); a practice material without Source is
  refused by ``st_practice``; a protected material is never a
  problem source (``CopyRefusedError``), and a verbatim protected
  passage is refused by ``check_originality``.
* Two-way Anki sync is NOT promised (``st_schedule`` docstring):
  the schedule store here is VYUK-side only. Deck notes are
  exported as new cards (``due=0``); the scheduler state lives
  in ``data/study_schedule.json`` and does not touch Anki's own
  scheduling. Exporting a regenerated deck updates notes with
  matching GUIDs in Anki instead of duplicating them (the leaf's
  stable-GUID rule), but VYUK never reads Anki's database back.
* Scheduling is deterministic: the flow uses a Scheduler with
  fuzzing OFF, so the same card + rating + review time always
  yields the same due date and a test can re-derive it. Anki's
  own fuzz on import is Anki's business, not VYUK's.
* The export tool (``study_export_deck``) and the full-session
  tool (``study_session``) write a caller-chosen file, so both
  are in ``_CONFIRM_GATE_TOOLS`` (write_file parity) with their
  ``output_path`` in ``_WRITE_DESTINATION_ARGS`` — the registry
  gate asks first, and with no confirm handler the call refuses
  without writing anything. ``study_practice`` and
  ``study_review`` write only to the app's own schedule store
  (the playbook-store pattern: a separate JSON file in the data
  directory, never the frozen memory schema), so they are not
  gated the same way; a review of an unknown card is an error,
  never an invented card.
* No Anki binary is required for export, and Anki itself is
  never shipped, driven, or bundled — the output is described
  as ".apkg (Anki-compatible)" only (the leaf's copy rule).
  Field proof on HIS Anki (a deck that actually opens there,
  a review that actually recurs) is owed after the BB101 quiz
  and is not claimed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_FORMAT_VERSION = 1
_DEFAULT_FILENAME = "study_schedule.json"


def _schedule_store_path() -> Path:
    """The schedule file in the app's data directory (separate
    file, following the playbook-store pattern; the frozen memory
    schema/cap is untouched). Tests substitute this seam."""
    try:
        from .config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR) / _DEFAULT_FILENAME
    except Exception:  # noqa: BLE001 — config import must not break the store
        return Path(__file__).resolve().parent.parent / "data" / _DEFAULT_FILENAME


def _stable_id(text: str) -> int:
    """A deterministic positive int id from text (deck/model/card
    ids must be ints; a hash keeps regeneration from minting a
    new identity for the same deck). Never 1 (Anki's Default)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:8]
    value = int.from_bytes(digest, "big") % (2 ** 62)
    return value + 2 if value < 2 else value


def _deck_id_for(deck_name: str) -> int:
    return _stable_id("vyuk-study-deck:" + deck_name)


def _model_id_for(deck_name: str, model_name: str) -> int:
    return _stable_id("vyuk-study-model:%s:%s" % (deck_name, model_name))


def parse_materials(materials_json):
    """Parse a caller-supplied materials JSON string into a list
    of plain dicts. Accepts a JSON list, or a single JSON object.
    Shape errors raise ValueError with a plain message (the tool
    handlers encode these as JSON errors, never raise)."""
    if isinstance(materials_json, (list, tuple)):
        raw = list(materials_json)
    elif isinstance(materials_json, str):
        try:
            parsed = json.loads(materials_json)
        except ValueError as exc:
            raise ValueError("materials_json is not valid JSON: %s" % (exc,))
        raw = parsed if isinstance(parsed, list) else [parsed]
    else:
        raise ValueError("materials_json must be a JSON string or a list.")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("Material %d is not an object." % i)
        out.append(dict(item))
    if not out:
        raise ValueError("No materials supplied.")
    return out


def to_practice_materials(items):
    """Plain material dicts -> st_practice.Material objects.
    Source enforcement lives in the leaf: an empty Source raises
    PracticeError here, before any problem is composed."""
    from . import st_practice as _practice

    materials = []
    for item in items:
        materials.append(_practice.Material(
            key=str(item.get("key", "")),
            prompt=str(item.get("prompt", "")),
            answer=str(item.get("answer", "")),
            syllabus=str(item.get("syllabus", "")),
            source=str(item.get("source", "")),
            prerequisites=list(item.get("prerequisites") or ()),
            protected=bool(item.get("protected", False)),
        ))
    return materials


def practice_set(materials_json, difficulty="medium", syllabus=None,
                 forms=("qa",), protected_passages=()):
    """Compose a practice set from the caller's own materials.
    Every problem carries its source (provenance); the set carries
    its limits statement. Protected materials / verbatim protected
    passages are refused by the leaf, never laundered."""
    from . import st_practice as _practice

    items = parse_materials(materials_json)
    materials = to_practice_materials(items)
    return _practice.generate_set(
        materials, difficulty=difficulty, syllabus=syllabus,
        forms=tuple(forms), protected_passages=tuple(protected_passages))


def to_deck_cards(items):
    """Plain card dicts -> (question, answer, source, tags) tuples.
    Accepts the deck shape (question/answer/source) and the
    practice shape (prompt as question). A missing/empty Source
    is refused by the deck leaf at add time; this function also
    refuses empty question/answer up front with a plain message."""
    cards = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Card %d is not an object." % i)
        question = str(item.get("question", item.get("prompt", ""))).strip()
        answer = str(item.get("answer", "")).strip()
        source = str(item.get("source", ""))
        if not question:
            raise ValueError("Card %d has no question/prompt." % i)
        if not answer:
            raise ValueError("Card %d has no answer." % i)
        tags = [str(t) for t in (item.get("tags") or ())]
        cards.append((question, answer, source, tags))
    if not cards:
        raise ValueError("No cards supplied.")
    return cards


def build_deck(deck_name, cards, exported_date=None):
    """Build a StudyDeck from (question, answer, source, tags)
    tuples. The deck description carries provenance: the sorted
    unique sources plus the export date. Source enforcement is
    the leaf's: a sourceless card raises SourceMissingError."""
    from . import st_deck as _deck

    if exported_date is None:
        exported_date = datetime.now(timezone.utc).date().isoformat()
    sources = sorted({source for _, _, source, _ in cards if source.strip()})
    description = (
        "VYUK Study deck. Provenance — sources: %s; exported %s."
        % ("; ".join(sources) if sources else "(none — refused at add)",
           exported_date)
    )
    deck = _deck.StudyDeck(_deck_id_for(deck_name), deck_name, description)
    qa_model = _deck.question_answer_model(
        _model_id_for(deck_name, "VYUK Study"), "VYUK Study")
    for question, answer, source, tags in cards:
        deck.add_note(_deck.StudyNote(qa_model, [question, answer, source],
                                      tags=tags))
    return deck


def build_deck_from_problems(deck_name, problems, exported_date=None):
    """Build a deck from practice problems. Cloze problems use the
    cloze model (text + source); QA/MCQ/grammar problems use the
    question/answer model. MCQ options stay in the practice set
    only — a deck card carries question, answer, and source, not
    its distractors. Every note carries the problem's source."""
    from . import st_deck as _deck

    if exported_date is None:
        exported_date = datetime.now(timezone.utc).date().isoformat()
    sources = sorted({str(p.get("source", "")) for p in problems
                      if str(p.get("source", "")).strip()})
    description = (
        "VYUK Study deck. Provenance — sources: %s; exported %s."
        % ("; ".join(sources) if sources else "(none — refused at add)",
           exported_date)
    )
    deck = _deck.StudyDeck(_deck_id_for(deck_name), deck_name, description)
    qa_model = _deck.question_answer_model(
        _model_id_for(deck_name, "VYUK Study"), "VYUK Study")
    cloze_model = _deck.cloze_model(
        _model_id_for(deck_name, "VYUK Study Cloze"), "VYUK Study Cloze")
    for problem in problems:
        source = str(problem.get("source", ""))
        form = str(problem.get("form", ""))
        if form in ("cloze", "vocab-cloze"):
            deck.add_note(_deck.StudyNote(
                cloze_model, [str(problem.get("text", "")), source]))
        else:
            question = str(problem.get("question", problem.get("prompt", "")))
            deck.add_note(_deck.StudyNote(
                qa_model, [question, str(problem.get("answer", "")), source]))
    return deck


def deck_card_key(question, answer, source):
    """The schedule key for a deck card: its stable deck GUID,
    so scheduling survives deck regeneration."""
    from . import st_deck as _deck

    return _deck.guid_for(question, answer, source)


def export_deck(deck, output_path):
    """Write a deck to a .apkg file. The path must end in .apkg
    and its parent directory must already exist — the export
    never guesses a name or creates folders. Returns the path."""
    from . import st_deck as _deck

    path = str(output_path or "").strip()
    if not path.lower().endswith(".apkg"):
        raise ValueError("output_path must end in .apkg, got %r." % (output_path,))
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise ValueError("output folder does not exist: %s" % parent)
    return _deck.write_apkg(deck, path)


class StudyScheduleStore:
    """VYUK-side FSRS-6 schedule state (separate JSON file, atomic
    writes, load never raises — mirroring the playbook store).

    File format v1: {"version": 1, "cards": {guid: {"card": {...},
    "logs": [...], "note": {"question","answer","source",
    "material_key"}}}}. A missing or corrupted file loads as an
    empty store with a note, and the file is NOT overwritten on
    a failed load. A review of an unknown key is an error, never
    an invented card."""

    def __init__(self, path=None, scheduler=None):
        from . import st_schedule as _schedule

        # Coerce through Path: the _schedule_store_path seam may
        # return a str (as tests do) or a Path (as production does).
        self._path = Path(path) if path is not None else Path(_schedule_store_path())
        # Deterministic scheduling: fuzzing OFF, so the same card
        # + rating + review time always yields the same due date.
        self._scheduler = scheduler or _schedule.Scheduler(enable_fuzzing=False)
        self._cards: dict = {}
        self.last_load_notes: list[str] = []
        self.last_save_error: str | None = None
        self._load()

    def _load(self):
        if not self._path.exists():
            self._cards = {}
            self.last_load_notes = ["no schedule file; starting empty"]
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — load never raises
            self._cards = {}
            self.last_load_notes = ["schedule file unreadable (%s); "
                                    "starting empty, file preserved" % exc]
            return
        if not isinstance(data, dict) or data.get("version") != _FORMAT_VERSION \
                or not isinstance(data.get("cards"), dict):
            self._cards = {}
            self.last_load_notes = ["schedule file has an unknown format; "
                                    "starting empty, file preserved"]
            return
        self._cards = data["cards"]
        self.last_load_notes = []

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                       prefix=".study_schedule_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"version": _FORMAT_VERSION,
                               "cards": self._cards}, f, indent=1)
                os.replace(tmp, self._path)
            except Exception:  # noqa: BLE001 — a failed save must not lose state
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            self.last_save_error = None
            return True
        except Exception as exc:  # noqa: BLE001 — save failure is recorded, not raised
            self.last_save_error = str(exc)
            return False

    def resolve_key(self, key):
        """A schedule key is a deck GUID; a material key also
        resolves (via the stored note) so a review can address a
        card by the key the caller gave its material."""
        key = str(key)
        if key in self._cards:
            return key
        for guid, entry in self._cards.items():
            if isinstance(entry, dict) \
                    and str((entry.get("note") or {}).get("material_key", "")) == key:
                return guid
        return None

    def ensure_card(self, question, answer, source, material_key=""):
        """Create the schedule entry for a card if it has none
        (new card, due now, learning state), and persist it.
        Returns the GUID key."""
        from . import st_schedule as _schedule

        key = deck_card_key(question, answer, source)
        if key not in self._cards:
            card = _schedule.Card(card_id=_stable_id("vyuk-study-card:" + key))
            self._cards[key] = {
                "card": card.to_dict(),
                "logs": [],
                "note": {"question": question, "answer": answer,
                         "source": source, "material_key": material_key},
            }
            self._save()
        return key

    def review(self, key, rating, review_datetime=None, review_duration=None):
        """Record one FSRS-6 review for a known card. The rating
        must be 1-4 (Again/Hard/Good/Easy); anything else raises
        ValueError before the scheduler runs. Returns a dict with
        the updated card, its due ISO timestamp, and the review
        log. An unknown key raises ValueError — never an invented
        card."""
        from . import st_schedule as _schedule

        if rating not in _schedule.RATINGS:
            raise ValueError(
                "rating must be one of %s (Again=1, Hard=2, Good=3, "
                "Easy=4), got %r." % (list(_schedule.RATINGS), rating))
        guid = self.resolve_key(key)
        if guid is None:
            raise ValueError("No scheduled card for key %r." % (key,))
        entry = self._cards[guid]
        card = _schedule.Card.from_dict(entry["card"])
        if review_datetime is None:
            review_datetime = datetime.now(timezone.utc)
        updated, log = self._scheduler.review_card(
            card, rating, review_datetime, review_duration)
        entry["card"] = updated.to_dict()
        entry["logs"].append(log.to_dict())
        self._save()
        return {"key": guid, "card": updated.to_dict(),
                "due": updated.due.isoformat(), "log": log.to_dict()}

    def due_cards(self, now=None):
        """GUID keys whose due time is at or before `now`
        (default: now), in due order. Cards with no due are due."""
        if now is None:
            now = datetime.now(timezone.utc)
        due = []
        for guid, entry in self._cards.items():
            try:
                from . import st_schedule as _schedule

                card = _schedule.Card.from_dict(entry["card"])
            except Exception:  # noqa: BLE001 — a bad entry never breaks the list
                continue
            if card.due is None or card.due <= now:
                due.append((card.due, guid))
        due.sort(key=lambda t: (t[0] is not None, t[0]))
        return [guid for _, guid in due]

    def summary(self):
        """Plain summary: total cards, due-now count, load notes."""
        return {"cards": len(self._cards),
                "due_now": len(self.due_cards()),
                "load_notes": list(self.last_load_notes),
                "last_save_error": self.last_save_error}


def study_flow(materials_json, deck_name, output_path, difficulty="medium",
               syllabus=None, forms=("qa",), reviews=None, store=None,
               exported_date=None):
    """The full flow: practice -> schedule -> deck, in one call.

    1. Compose a practice set from the caller's materials
       (provenance on every problem, protected refused).
    2. Give every practised card a schedule entry (new card, due
       now); apply any supplied reviews ({card key or material
       key: rating 1-4}) through FSRS-6.
    3. Export the deck (provenance description, .apkg) — the
       caller-chosen file write, gated when reached via a tool.

    Returns a dict: the practice set, the deck path + note/card
    counts + sources, and the schedule (key -> due ISO) for every
    card. Nothing here invents a question, a source, or an
    interval."""
    items = parse_materials(materials_json)
    result_set = practice_set(materials_json, difficulty=difficulty,
                              syllabus=syllabus, forms=forms)
    problems = result_set["problems"]
    # Material key per problem, in set order (generate_set keeps
    # the chosen materials' order, so problems align with items
    # filtered by syllabus — map by (prompt, source) instead of
    # position, so a syllabus filter can never mis-key a card).
    by_prompt_source = {}
    for item in items:
        by_prompt_source.setdefault(
            (str(item.get("prompt", "")), str(item.get("source", ""))),
            str(item.get("key", "")))
    store = store if store is not None else StudyScheduleStore()
    schedule = {}
    for problem in problems:
        question = str(problem.get("question", problem.get("prompt", "")))
        if problem.get("form") in ("cloze", "vocab-cloze"):
            # Cloze deck notes carry text, not a Q/A pair; the
            # schedule entry tracks the same text as its question.
            question = str(problem.get("text", ""))
            answer = str(problem.get("answer", ""))
        else:
            answer = str(problem.get("answer", ""))
        source = str(problem.get("source", ""))
        material_key = by_prompt_source.get(
            (str(problem.get("prompt", "")), source), "")
        key = store.ensure_card(question, answer, source,
                                material_key=material_key)
        schedule[key] = store._cards[key]["card"]["due"]
    for review_key, rating in (reviews or {}).items():
        result = store.review(review_key, rating)
        schedule[result["key"]] = result["due"]
    deck = build_deck_from_problems(deck_name, problems,
                                    exported_date=exported_date)
    path = export_deck(deck, output_path)
    summary = _deck_summary(path)
    return {"practice": result_set, "deck_path": str(path),
            "deck": summary, "schedule": schedule,
            "schedule_summary": store.summary()}


def _deck_summary(path):
    from . import st_deck as _deck

    info = _deck.read_apkg_summary(path)
    return {"notes": len(info["notes"]), "cards": len(info["cards"]),
            # Source is the last field in both models (QA: index 2,
            # cloze: index 1), so take the last field, not index 2.
            "sources": sorted({n["fields"][-1] for n in info["notes"]
                               if n["fields"]})}


# ---------------------------------------------------------------------------
# Tool handlers — plain JSON strings, never raising (skills-pack rule).
# Refusals are JSON error objects, so a brain caller gets the reason
# instead of a crash. The gated tools' confirmation lives in the
# registry funnel (_CONFIRM_GATE_TOOLS), not in these handlers.
# ---------------------------------------------------------------------------

def _ok(payload):
    return json.dumps({"status": "ok", **payload})


def _err(message):
    return json.dumps({"status": "error", "message": str(message)})


def study_practice_handler(materials_json, difficulty="medium",
                           syllabus=None, forms="qa"):
    try:
        form_list = tuple(f.strip() for f in str(forms).split(",") if f.strip())
        result = practice_set(materials_json, difficulty=difficulty,
                              syllabus=syllabus, forms=form_list or ("qa",))
        return _ok({"practice": result})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def study_review_handler(card_key, rating, store=None):
    try:
        store = store if store is not None else StudyScheduleStore()
        return _ok({"review": store.review(str(card_key), int(rating))})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def study_export_deck_handler(materials_json, deck_name, output_path,
                              exported_date=None):
    try:
        items = parse_materials(materials_json)
        cards = to_deck_cards(items)
        deck = build_deck(deck_name, cards, exported_date=exported_date)
        path = export_deck(deck, output_path)
        return _ok({"deck_path": str(path), "deck": _deck_summary(path)})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def study_session_handler(materials_json, deck_name, output_path,
                          difficulty="medium", syllabus=None, forms="qa",
                          reviews_json=None, store=None,
                          exported_date=None):
    try:
        form_list = tuple(f.strip() for f in str(forms).split(",") if f.strip())
        reviews = {}
        if reviews_json:
            parsed = json.loads(reviews_json)
            if not isinstance(parsed, dict):
                raise ValueError("reviews_json must be a JSON object.")
            reviews = {str(k): int(v) for k, v in parsed.items()}
        result = study_flow(materials_json, deck_name, output_path,
                            difficulty=difficulty, syllabus=syllabus,
                            forms=form_list or ("qa",), reviews=reviews,
                            store=store, exported_date=exported_date)
        return _ok(result)
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def register_all(registry) -> None:
    """Register the four Study flow tools on a ToolRegistry-like
    object. study_export_deck / study_session write a
    caller-chosen file and are confirm-gated (see module
    docstring); study_practice / study_review touch only the
    app's own schedule store and the caller's materials."""
    registry.register(
        name="study_practice",
        description="Compose an original practice set from the user's own materials (a JSON list of {key, prompt, answer, syllabus, source} records). Every problem carries its source as provenance and the set carries its limits statement; protected materials and verbatim protected passages are refused, never laundered. No question is invented without a source. Use when the user wants practice questions from their own notes.",
        parameters={"type": "object", "properties": {
            "materials_json": {"type": "string", "description": "JSON list of material records: key, prompt, answer, syllabus, source (non-empty), optional prerequisites and protected flag"},
            "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"], "description": "Practice difficulty"},
            "syllabus": {"type": "string", "description": "Optional syllabus tag to filter materials by; omit to use all"},
            "forms": {"type": "string", "description": "Comma-separated problem forms: qa, cloze, mcq, vocab-cloze, grammar-qa"},
        }, "required": ["materials_json"]},
        handler=study_practice_handler,
    )
    registry.register(
        name="study_review",
        description="Record one FSRS-6 review for a scheduled study card (card_key is the deck GUID, or the material key given when the card was practised) with rating 1=Again, 2=Hard, 3=Good, 4=Easy. Returns the card's next due time, computed by FSRS-6, never guessed. An unknown card key is an error, never an invented card. Use after the user answers a practised card.",
        parameters={"type": "object", "properties": {
            "card_key": {"type": "string", "description": "Deck GUID or material key of the scheduled card"},
            "rating": {"type": "integer", "description": "1=Again, 2=Hard, 3=Good, 4=Easy"},
        }, "required": ["card_key", "rating"]},
        handler=study_review_handler,
    )
    registry.register(
        name="study_export_deck",
        description="Export study cards to a real Anki .apkg file (Anki-compatible, stdlib only, no Anki binary needed). Cards are a JSON list of {question (or prompt), answer, source (non-empty), optional tags}; a card without a source is refused, never written. The deck description carries the sources and export date as provenance. Writes the caller-chosen output_path (must end in .apkg) and asks for confirmation first. Use when the user wants a deck to open in their own Anki.",
        parameters={"type": "object", "properties": {
            "materials_json": {"type": "string", "description": "JSON list of card records: question (or prompt), answer, source (non-empty), optional tags"},
            "deck_name": {"type": "string", "description": "Deck name"},
            "output_path": {"type": "string", "description": "File path to write, ending in .apkg, in an existing folder"},
        }, "required": ["materials_json", "deck_name", "output_path"]},
        handler=study_export_deck_handler,
    )
    registry.register(
        name="study_session",
        description="One full study flow: compose a practice set from the user's own materials, schedule every practised card with FSRS-6, apply any supplied reviews ({card key or material key: rating 1-4}), and export the deck as a .apkg (Anki-compatible) with provenance in its description. Protected materials are refused; sourceless cards are refused; the file write asks for confirmation first. Use when the user wants to practise, schedule, and export in one go.",
        parameters={"type": "object", "properties": {
            "materials_json": {"type": "string", "description": "JSON list of material records: key, prompt, answer, syllabus, source (non-empty)"},
            "deck_name": {"type": "string", "description": "Deck name"},
            "output_path": {"type": "string", "description": "File path to write, ending in .apkg, in an existing folder"},
            "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"], "description": "Practice difficulty"},
            "syllabus": {"type": "string", "description": "Optional syllabus tag to filter materials by"},
            "forms": {"type": "string", "description": "Comma-separated problem forms"},
            "reviews_json": {"type": "string", "description": "Optional JSON object of {card key or material key: rating 1-4} reviews to apply"},
        }, "required": ["materials_json", "deck_name", "output_path"]},
        handler=study_session_handler,
    )


__all__ = [
    "StudyScheduleStore", "parse_materials", "to_practice_materials",
    "practice_set", "to_deck_cards", "build_deck",
    "build_deck_from_problems", "deck_card_key", "export_deck",
    "study_flow", "study_practice_handler", "study_review_handler",
    "study_export_deck_handler", "study_session_handler", "register_all",
]
