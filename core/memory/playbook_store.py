"""Q10 single playbook store — ONE store for goal + finance playbooks (stdlib only).

Supersedes (Q10 ruling, owner-approved for this version build), with
NO transition shims (Q1 clean break — callers moved, duplicates removed):

* core/j9_playbook_w2.py — goal entries: proven steps only, deletion
  refusal, proof job ids (J9, NON-DELETION core).
* core/fl_playbook_v574.py — finance bullets: helpful/harmful counters,
  dedupe by normalised text, curation from caller-supplied traces.
* core/playbook_store_p2.py — J9-only persistence.

One file, two namespaces, and the lookup rule that made the old
single-store decision safe is PRESERVED under Q10: the merge unifies
storage, not lookup. planner.run_goal's playbook lookup pulls goal
entries ONLY, never finance bullets, so one goal can never pull two
playbooks' steps; the finance tools use bullets ONLY, never goal
entries. A goal entry and a finance bullet may share a name/text
without colliding — they live in different namespaces.

Goal side (J9 rules, moved verbatim):
* An entry is reusable only when it carries a field-proof job id
  (proof supplied by the caller, never asserted here).
* Any step whose tool or description is a deletion (delete, remove,
  trash, purge, wipe, erase, uninstall) REFUSES the whole entry at
  add time, and the same check runs again on load — a hand-edited
  file cannot smuggle a deletion step past the gate.
* The store never executes anything; it returns step lists for the
  caller to run through the normal gate stack (C-14 included).

Finance side (FL-4-1 rules, moved verbatim):
* Bullets are local-only learning for THIS user, zero export:
  per-bullet helpful/harmful counters, human-readable and deletable.
  Bullet identity is its normalised text; duplicates merge counters.
* Curation runs offline against CALLER-SUPPLIED frozen traces only;
  this module never captures, watches, or reads any trace store.
* Bullet ids are POSITIONAL (1-based index into the stored list),
  as the finance tools have always used them: callers re-list
  before deleting, the same as any list tool. The leaf's
  list_bullets sorts best-first for display; the stored list
  itself stays in insertion order so ids stay stable across reloads.
* The only shippable claim for the finance side is "no VYUK vendor
  server". ("Nothing leaves by architecture" was REJECTED as written
  for cloud brains and appears nowhere as a claim.)

File format (JSON, versioned, in the app's data directory,
following the existing data-file pattern; the frozen memory
schema/cap is untouched — this is a separate file, not a
memory store):

    {"version": 2, "entries": {name: {"steps": [...],
     "proof_job_id": "...", "proven": true}},
     "bullets": [{"text": "...", "source": "...",
     "helpful": 0, "harmful": 0}]}

Version 1 files (goal entries only, as written by the old
playbook_store_p2) still load: entries load identically,
bullets come back empty. A version 1 file never gains bullets
from the loader itself — bullets arrive only from a finance
write or from the legacy finance file (below).

Legacy finance file: an older install kept finance bullets in
data/finance_playbook.json (version 1, {"bullets": [...]}).
When the single-store file has NO bullets section (missing
file, or a version 1 file) and that legacy file exists, its
bullets are imported on load (same sanitisation the old
finance loader applied) with a note. The legacy file is left
untouched — never overwritten, never deleted — so the import
is re-runnable and no user data is destroyed by the merge.
A corrupt or unsupported single-store file degrades to EMPTY
with a note and is left untouched (no legacy import there:
a corrupt file is evidence, not an invitation to guess).

Persistence rules, inherited from both predecessors:
* A corrupted, missing, or malformed file fails to an EMPTY
  store with an honest note, never to a guessed entry and
  never to a crash. The file is not overwritten on a failed
  load (the evidence is preserved for inspection); the next
  successful save replaces it with valid content.
* Writes are atomic (temporary file + os.replace) so a crash
  mid-save cannot leave a half-written store.
* Goal adds and proof-setting save atomically; reuse never
  writes. Saving never breaks adding: if a save fails, the
  entry is still in memory and the failure is recorded on
  ``last_save_error`` (and returned in the add result's
  ``persisted`` flag) instead of raising.
* Finance mutations do NOT auto-save (the finance tools save
  explicitly per call, as they always did, via ``save()``);
  goal mutations do auto-save (as the old persistent store
  did). During bulk load, saving is suppressed so one load
  does not rewrite the file once per entry.

B17 (learning across tasks, 2026-11-07): the store gains
two additive, backward-compatible extensions — the file
format stays version 2, old files load identically:

* A goal entry may carry a ``failure`` note: a task that
  failed records its lesson as an UNPROVEN entry naming what
  failed (``add(..., failure=reason)``). An unproven entry is
  never reusable, so a failure lesson is recorded, never
  silently dropped, and never re-run. A later success for
  the same goal overwrites it with a proven entry.
* A lesson that fails on reuse is STRUCK (``strike(name,
  reason)``): the entry is demoted to unproven, its proof
  cleared, and the struck (name, proof_job_id) pair is
  persisted in a top-level ``struck`` list. ``add()`` and
  ``set_proof()`` refuse to re-prove a struck pair — a
  lesson is never trusted again on the SAME evidence (a new
  proof, from a new task run, may prove it again). The J9
  deletion refusal and the exact/case-insensitive reuse
  matching are unchanged.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_FORMAT_VERSION = 2
_LEGACY_FORMAT_VERSION = 1
_DEFAULT_FILENAME = "playbooks.json"
_LEGACY_FILENAME = "finance_playbook.json"

_DELETION_WORDS = ("delete", "remove", "trash", "purge", "wipe",
                   "erase", "uninstall")


def _normalise(text):
    return re.sub(r"\s+", " ", text.strip().casefold())


def default_playbook_path() -> Path:
    """The store file in the app's data directory."""
    try:
        from ..config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR) / _DEFAULT_FILENAME
    except Exception:  # noqa: BLE001 — config import must not break the store
        return Path(__file__).resolve().parent.parent.parent / "data" / _DEFAULT_FILENAME  # Phase 3 M1b: module moved one level deeper (core/memory/); store stays at tree-root data/


def legacy_finance_path() -> Path:
    """The pre-Q10 finance-bullets file, if an older install wrote one."""
    try:
        from ..config import DATA_DIR as _DATA_DIR

        return Path(_DATA_DIR) / _LEGACY_FILENAME
    except Exception:  # noqa: BLE001 — config import must not break the store
        return Path(__file__).resolve().parent.parent.parent / "data" / _LEGACY_FILENAME  # Phase 3 M1b: module moved one level deeper (core/memory/); store stays at tree-root data/


@dataclass(frozen=True)
class PlaybookEntry:
    name: str
    steps: tuple
    proof_job_id: str = ""
    proven: bool = False
    # B17: the failure a lesson records, or "". A failure
    # lesson is an unproven entry whose failure names what
    # failed; a struck lesson carries the strike reason here.
    failure: str = ""


class PlaybookStore:
    """ONE store: J9 goal entries + finance bullets, two namespaces."""

    def __init__(self, path=None):
        self._goal_entries: dict[str, PlaybookEntry] = {}
        self.refusals: list[str] = []
        self._bullets: list[dict] = []
        # B17: struck (name, proof_job_id, reason) records —
        # a lesson demoted after failing on reuse. Persisted
        # in the store file's top-level "struck" list, so a
        # struck proof can never re-prove the lesson, even
        # across a restart (see strike()).
        self._struck: list[dict] = []
        self._path = Path(path) if path is not None else default_playbook_path()
        self.last_save_error: str | None = None
        self.last_load_notes: list[str] = []
        self._saving_suppressed = False

    # -- goal namespace (J9, verbatim rules) --

    @property
    def _entries(self):
        """Goal entries dict (the old store exposed this name)."""
        return self._goal_entries

    @staticmethod
    def _is_deletion(step: dict) -> bool:
        tool = str(step.get("tool", "")).lower()
        desc = str(step.get("description", "")).lower()
        return any(w in tool or w in desc for w in _DELETION_WORDS)

    def _is_struck(self, name: str, proof_job_id: str) -> bool:
        """B17: was this (name, proof) pair struck after a
        failed reuse? A struck pair is never trusted again
        on the same evidence."""
        if not proof_job_id:
            return False
        return any(s.get("name") == name
                   and s.get("proof_job_id") == proof_job_id
                   for s in self._struck)

    def add(self, name: str, steps: list, *,
            proof_job_id: str = "", failure: str = "") -> dict:
        """Add a goal entry. Deletion steps refuse the whole entry.

        B17: ``failure`` records a failure lesson (an unproven
        entry naming what failed). A struck proof_job_id is
        stored unproven, never re-trusted on the same evidence.
        """
        if not name or not steps:
            result = {"added": False, "reason": "name and steps are required"}
            return dict(result, persisted=False)
        for step in steps:
            if not isinstance(step, dict):
                result = {"added": False, "reason": "steps must be dicts"}
                return dict(result, persisted=False)
            if self._is_deletion(step):
                reason = (f"refused: deletion step in entry '{name}' "
                          f"({step}); J9 deletion behaviour is NOT built "
                          f"in Wave 2 (waits on PF-3).")
                self.refusals.append(reason)
                return dict({"added": False, "reason": reason},
                            persisted=False)
        struck = self._is_struck(name, proof_job_id)
        entry = PlaybookEntry(name=name, steps=tuple(steps),
                              proof_job_id="" if struck else proof_job_id,
                              proven=bool(proof_job_id) and not struck,
                              failure=str(failure or ""))
        self._goal_entries[name] = entry
        saved = self._save_quietly()
        if struck:
            reason = (f"stored unproven: proof {proof_job_id!r} was struck "
                      f"for {name!r} — never trusted again on that evidence")
        else:
            reason = ("" if entry.proven else
                      "stored unproven: reusable only after a proof job id is set")
        return dict({"added": True, "proven": entry.proven,
                     "reason": reason},
                    persisted=saved)

    def set_proof(self, name: str, proof_job_id: str) -> bool:
        entry = self._goal_entries.get(name)
        if entry is None or not proof_job_id:
            return False
        # B17: a struck proof never re-proves, not even by hand.
        if self._is_struck(name, proof_job_id):
            return False
        self._goal_entries[name] = PlaybookEntry(
            name=entry.name, steps=entry.steps,
            proof_job_id=proof_job_id, proven=True,
            failure=entry.failure)
        self._save_quietly()
        return True

    @property
    def struck(self) -> list:
        """B17: struck (name, proof_job_id, reason) records, oldest first."""
        return [dict(s) for s in self._struck]

    def strike(self, name: str, reason: str = "") -> dict:
        """B17: demote a lesson that failed on reuse.

        The entry is demoted to unproven, its proof cleared,
        and the struck (name, proof_job_id) pair is recorded
        persistently — ``add()``/``set_proof()`` refuse to
        re-prove that pair, so the lesson is never trusted
        again on the SAME evidence (a new proof, from a new
        task run, may prove it again). The steps stay recorded
        (what was tried is not erased); the failure note names
        why it was struck. Idempotent: striking an already
        unproven entry keeps it untrusted and records the
        reason. Never raises; returns {"struck", "proof_job_id",
        "reason", "persisted"}.
        """
        entry = self._goal_entries.get(name)
        if entry is None:
            return {"struck": False, "proof_job_id": "",
                    "reason": f"no playbook entry {name!r}",
                    "persisted": False}
        old_proof = entry.proof_job_id
        if old_proof and not self._is_struck(name, old_proof):
            self._struck.append({"name": name, "proof_job_id": old_proof,
                                 "reason": str(reason or "")})
        self._goal_entries[name] = PlaybookEntry(
            name=entry.name, steps=entry.steps,
            proof_job_id="", proven=False,
            failure=str(reason or entry.failure or ""))
        saved = self._save_quietly()
        return {"struck": True, "proof_job_id": old_proof,
                "reason": "", "persisted": saved}

    def note_failure(self, name: str, reason: str) -> bool:
        """B17: record a failure note on an entry WITHOUT
        touching its steps, proof, or proven state.

        Used when a goal fails but a PROVEN lesson already
        stands for it: the failure was not in the lesson's
        steps (a lesson-step failure strikes it first), so the
        proof survives and the failure is recorded as a note,
        never by overwriting the lesson. False when there is
        no entry or no reason.
        """
        entry = self._goal_entries.get(name)
        if entry is None or not str(reason or "").strip():
            return False
        self._goal_entries[name] = PlaybookEntry(
            name=entry.name, steps=entry.steps,
            proof_job_id=entry.proof_job_id, proven=entry.proven,
            failure=str(reason))
        self._save_quietly()
        return True

    def reuse(self, name: str) -> dict:
        """Return proven steps only; unproven entries are refused."""
        entry = self._goal_entries.get(name)
        if entry is None:
            return {"ok": False, "steps": [],
                    "reason": f"no playbook entry '{name}'"}
        if not entry.proven:
            return {"ok": False, "steps": [],
                    "reason": f"entry '{name}' is unproven; not reusable "
                             f"until a field-proof job id is recorded"}
        return {"ok": True, "steps": list(entry.steps),
                "proof_job_id": entry.proof_job_id, "reason": ""}

    def names(self) -> list:
        return sorted(self._goal_entries)

    # -- finance namespace (FL-4-1, positional ids) --

    @property
    def bullets(self) -> list:
        """Stored bullets, insertion order (the live list)."""
        return self._bullets

    def _bullet_position(self, text: str) -> int | None:
        norm = _normalise(text)
        for index, bullet in enumerate(self._bullets):
            if _normalise(bullet["text"]) == norm:
                return index + 1
        return None

    def add_bullet(self, text, source="user"):
        """Add a bullet; returns its positional id. Duplicates merge."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("bullet text must be a non-empty string")
        existing = self._bullet_position(text)
        if existing is not None:
            return existing
        self._bullets.append({
            "text": text.strip(), "source": source,
            "helpful": 0, "harmful": 0})
        return len(self._bullets)

    def _bullet_at(self, bullet_id):
        index = int(bullet_id) - 1
        if index < 0 or index >= len(self._bullets):
            raise ValueError(f"no finance bullet {bullet_id!r}")
        return self._bullets[index]

    def helpful(self, bullet_id):
        self._bullet_at(bullet_id)["helpful"] += 1

    def harmful(self, bullet_id):
        self._bullet_at(bullet_id)["harmful"] += 1

    def delete_bullet(self, bullet_id):
        """Delete a bullet by positional id (False when absent)."""
        try:
            index = int(bullet_id) - 1
        except (TypeError, ValueError):
            return False
        if index < 0 or index >= len(self._bullets):
            return False
        del self._bullets[index]
        return True

    def list_bullets(self):
        """Human-readable bullets, best first by net score."""
        ordered = sorted(
            enumerate(self._bullets, start=1),
            key=lambda pair: (pair[1]["helpful"] - pair[1]["harmful"]),
            reverse=True)
        return [{"id": bullet_id, "text": b["text"],
                 "helpful": b["helpful"], "harmful": b["harmful"]}
                for bullet_id, b in ordered]

    def score(self, bullet_id):
        bullet = self._bullet_at(bullet_id)
        return bullet["helpful"] - bullet["harmful"]

    def merge(self, other):
        """Merge another bullet source's bullets in (dedupe by text)."""
        for bullet in other.list_bullets():
            mine = self.add_bullet(bullet["text"], source="merge")
            target = self._bullet_at(mine)
            target["helpful"] += bullet["helpful"]
            target["harmful"] += bullet["harmful"]

    def curate_from_traces(self, traces):
        """Curate from CALLER-SUPPLIED frozen traces (offline first).

        Each trace is {"text": str, "outcome": "helpful"|"harmful"|
        "neutral"}. Helpful traces add/confirm bullets; harmful
        traces add a bullet and count against it (the playbook
        records what NOT to repeat, it does not delete on one
        failure). Neutral traces are ignored. Returns ids touched.
        """
        touched = []
        for trace in traces:
            text = trace.get("text")
            outcome = trace.get("outcome")
            if not isinstance(text, str) or not text.strip():
                continue
            if outcome not in ("helpful", "harmful"):
                continue
            bullet_id = self.add_bullet(text, source="trace")
            if outcome == "helpful":
                self.helpful(bullet_id)
            else:
                self.harmful(bullet_id)
            touched.append(bullet_id)
        return touched

    # -- persistence --

    @property
    def path(self) -> Path:
        return self._path

    def _save_quietly(self) -> bool:
        if self._saving_suppressed:
            return True
        try:
            return save_playbook(self, self._path)["saved"]
        except Exception as exc:  # noqa: BLE001 — save failure must not break add
            self.last_save_error = str(exc)
            return False

    def save(self) -> dict:
        """Atomically write the whole store; returns {saved, reason}."""
        result = save_playbook(self, self._path)
        if not result["saved"]:
            self.last_save_error = result["reason"]
        return result

    def load(self, path=None) -> list[str]:
        """Load a store file into this store; returns notes.

        Goal entries are added (same name overwrites); bullets
        merge by normalised text (existing bullets keep their
        counters, so a repeated load is idempotent). A fresh
        store plus one load therefore equals the file exactly.
        """
        notes, book = load_playbook(path if path is not None else self._path)
        self._path = Path(path) if path is not None else self._path
        self.last_load_notes = notes
        self._saving_suppressed = True
        try:
            for name in book.names():
                entry = book._goal_entries[name]
                self.add(name, list(entry.steps),
                         proof_job_id=entry.proof_job_id,
                         failure=entry.failure)
            for bullet in book._bullets:
                if self._bullet_position(bullet["text"]) is None:
                    self._bullets.append(dict(bullet))
            # B17: struck pairs travel with the load, so a
            # struck proof stays struck after a restart.
            self._struck = [dict(s) for s in book._struck]
        finally:
            self._saving_suppressed = False
        return notes


def _sanitise_bullets(raw) -> tuple[list, str | None]:
    """Old finance-loader sanitisation: insertion order, clean counters."""
    if not isinstance(raw, list):
        return [], "persisted playbook file has no bullets list; bullets dropped"
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not str(entry.get("text") or "").strip():
            continue
        out.append({
            "text": str(entry["text"]).strip(),
            "source": str(entry.get("source") or "user"),
            "helpful": max(0, int(entry.get("helpful") or 0)),
            "harmful": max(0, int(entry.get("harmful") or 0)),
        })
    return out, None


def _load_legacy_bullets() -> tuple[list, str | None]:
    """Bullets from the pre-Q10 finance file, if it exists."""
    path = legacy_finance_path()
    if not path.exists():
        return [], None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], None
    if not isinstance(raw, dict) or raw.get("version") != _LEGACY_FORMAT_VERSION:
        return [], None
    bullets, _note = _sanitise_bullets(raw.get("bullets"))
    if not bullets:
        return [], None
    return bullets, (f"imported {len(bullets)} finance bullets from legacy "
                     f"{_LEGACY_FILENAME} (file left untouched)")


def load_playbook(path=None) -> tuple[list[str], PlaybookStore]:
    """(notes, PlaybookStore) from a store file; never raises.

    A missing file is an empty store with a note. A corrupt file
    is an empty store with a note, and the file is left
    untouched. Goal entries that fail J9 validation (deletion
    step, non-dict step, empty name) are dropped with a note
    each. A bullets section that is not a list is dropped with
    a note; goal entries still load.
    """
    path = Path(path) if path is not None else default_playbook_path()
    book = PlaybookStore(path)
    if not path.exists():
        notes = [f"no persisted playbook file at {path}; starting empty"]
        bullets, legacy_note = _load_legacy_bullets()
        book._bullets = bullets
        if legacy_note is not None:
            notes.append(legacy_note)
        return notes, book
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — corrupt file degrades, never crashes
        return [f"persisted playbook file is unreadable ({exc}); "
                f"starting empty; file left untouched at {path}"], book
    if not isinstance(raw, dict) or raw.get("version") not in (1, _FORMAT_VERSION):
        return ["persisted playbook file has an unsupported format; "
                "starting empty; file left untouched"], book
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return ["persisted playbook file has no entries mapping; "
                "starting empty"], book
    notes: list[str] = []
    book._saving_suppressed = True
    try:
        for name, record in entries.items():
            if not isinstance(name, str) or not name:
                notes.append("dropped an entry with no name")
                continue
            if not isinstance(record, dict) or not isinstance(record.get("steps"), list):
                notes.append(f"dropped entry '{name}': steps are not a list of dicts")
                continue
            result = book.add(name, list(record["steps"]),
                              proof_job_id=str(record.get("proof_job_id") or ""),
                              failure=str(record.get("failure") or ""))
            if not result.get("added"):
                notes.append(f"dropped entry '{name}': {result.get('reason', '')}")
        # B17: struck pairs persist in a top-level "struck"
        # list (additive key — old files simply have none).
        # A malformed list degrades to empty, never a crash.
        struck_raw = raw.get("struck")
        if isinstance(struck_raw, list):
            for item in struck_raw:
                if not isinstance(item, dict):
                    continue
                s_name = str(item.get("name") or "")
                s_proof = str(item.get("proof_job_id") or "")
                if s_name and s_proof:
                    book._struck.append({
                        "name": s_name, "proof_job_id": s_proof,
                        "reason": str(item.get("reason") or "")})
    finally:
        book._saving_suppressed = False
    if "bullets" in raw:
        bullets, bullets_note = _sanitise_bullets(raw["bullets"])
        if bullets_note is not None:
            notes.append(bullets_note)
        book._bullets = bullets
    else:
        legacy_bullets, legacy_note = _load_legacy_bullets()
        if legacy_bullets:
            book._bullets = legacy_bullets
            notes.append(legacy_note)
    if not notes:
        notes.append(f"loaded {len(book.names())} persisted playbook entries")
        if book._bullets:
            notes.append(f"loaded {len(book._bullets)} finance bullets")
    return notes, book


def save_playbook(store, path=None) -> dict:
    """Atomically write a store to file; returns {saved, reason}."""
    path = Path(path) if path is not None else default_playbook_path()
    try:
        entries = {}
        for name in store.names():
            entry = store._goal_entries[name]
            entries[name] = {
                "steps": list(entry.steps),
                "proof_job_id": entry.proof_job_id,
                "proven": bool(entry.proven),
                # B17: the failure a lesson records, or "".
                "failure": entry.failure,
            }
        bullets = [{"text": b["text"], "source": b["source"],
                    "helpful": b["helpful"], "harmful": b["harmful"]}
                   for b in store._bullets]
        payload = json.dumps({"version": _FORMAT_VERSION, "entries": entries,
                              "bullets": bullets,
                              # B17: struck (name, proof_job_id,
                              # reason) pairs — a struck proof never
                              # re-proves, even across a restart.
                              "struck": [dict(s) for s in
                                         getattr(store, "_struck", [])]},
                             indent=2, sort_keys=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return {"saved": True, "reason": ""}
    except Exception as exc:  # noqa: BLE001 — caller decides; never raise
        return {"saved": False, "reason": str(exc)}


def dc_cumulative_baseline(traces):
    """FL-4-2 STUDY baseline: cumulative frequency of trace texts.

    The controlled baseline the playbook is compared against
    (DC-Cumulative vs ACE deltas). Evaluation only, not a feature.
    Returns texts ordered by frequency, most frequent first.
    """
    counts = {}
    for trace in traces:
        text = trace.get("text")
        if isinstance(text, str) and text.strip():
            counts[_normalise(text)] = counts.get(_normalise(text), 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def compare_baseline(store, traces):
    """Compare playbook top bullets against the DC baseline.

    Returns {"baseline_top": [...], "ace_top": [...],
    "only_in_playbook": [...], "only_in_baseline": [...]} — the
    delta evaluation for the FL-4-1 pilot. Texts only; no traces
    are stored, copied, or exported by this comparison.
    """
    baseline = [text for text, _ in dc_cumulative_baseline(traces)[:5]]
    ace = [b["text"] for b in store.list_bullets()[:5]]
    ace_norm = {_normalise(t) for t in ace}
    base_norm = set(baseline)
    return {"baseline_top": baseline, "ace_top": ace,
            "only_in_playbook": [t for t in ace
                                 if _normalise(t) not in base_norm],
            "only_in_baseline": [t for t in baseline
                                 if t not in ace_norm]}
