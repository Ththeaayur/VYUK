"""Wave 6 (v5.61) — SO-1 + CP-5 people & relationship memory.

Rides the C-1 Wave 6 ruling (the D-1 file store): people-scale
records live as Markdown+frontmatter files, NOT as frozen-DB
schema (INV-8 is the hardest-hit invariant in either social
wave: the Monica-scale schema + keep-every-version designs
exceed the frozen schema and the 6,000-char cap
arithmetically — CP-5.5's expiry is the only pressure valve).

Schema-level ethics, landed inside the ruling (CP-4.7):
- Every record carries `kind`. A record of kind `opinion`
  NEVER renders in the fact prompt (see
  core/memory_files.py dual_read) — opinions are
  retrievable only through an explicit kind-filtered query.
  Storing "he thinks X" as a fact is a schema violation, and
  this module refuses to write an opinion-kind record through
  the fact-writing path: opinions go through `write_opinion`
  only, which stamps kind=opinion and never promotes it.
- Promises are facts with teeth: a promise record carries the
  promise text, the person it was made to, and a due date.
  Overdue promises surface through `overdue_promises()`.
- CP-5.5 pressure valve: when the store grows past
  PRESSURE_VALVE_MAX records, the oldest non-pinned,
  non-promise records expire first (archived, not destroyed —
  same archival rule as inferred expiry). Promises and pinned
  identity records are never pressure-valved.
- Forgetting-by-design (shared with the fact store): inferred
  traits expire 90 days after creation (see memory_files
  sweep_expired).

Ethics lines (standing, wave 22 §10): no manufactured
dependency, no simulated romantic attachment, no sycophancy
engines, no exploiting loneliness, humans first, memory is
the user's. Nothing in this module models attachment,
affection, or mood — person records are facts (names,
relationships, promises, ledgers), and nothing else.

This module imports stdlib + the Wave 6 file store only.
"""

import os

from .memory_files import (
    delete_fact, fact_path, list_facts, memories_dir, write_fact,
)

PRESSURE_VALVE_MAX = 2000


def write_person(name, relationship="", note="", base=None):
    """A person record (kind=person). Names + relationships are
    facts; anything the user OPINES about the person goes
    through write_opinion, never here."""
    record = {
        "category": "people", "key": name, "kind": "person",
        "source": "user_stated", "provenance": "",
        "created_at": "", "updated_at": "",
        "valid_from": "", "valid_to": "", "recorded_at": "",
        "supersedes": "", "volatility": "0.2",
        "due_date": "", "last_verified": "", "expires_at": "",
        "value": f"{name} — {relationship}\n{note}".strip(),
    }
    path, _wrote = write_fact(record, base=base)
    return path


def write_promise(person, promise_text, due_date="", base=None):
    """A promise record (kind=promise): who it was made to,
    what was promised, when it is due. Promises are never
    pressure-valved and surface through overdue_promises()."""
    record = {
        "category": "promises", "key": f"{person}__{promise_text[:48]}",
        "kind": "promise", "source": "user_stated", "provenance": "",
        "created_at": "", "updated_at": "",
        "valid_from": "", "valid_to": "", "recorded_at": "",
        "supersedes": "", "volatility": "0.1",
        "due_date": due_date, "last_verified": "", "expires_at": "",
        "value": f"Promise to {person}: {promise_text}",
    }
    path, _wrote = write_fact(record, base=base)
    return path


def write_opinion(topic, opinion_text, base=None):
    """The ONLY opinion-writing path. Stamps kind=opinion, which
    dual_read excludes from the fact prompt permanently. An
    opinion stored here can never be promoted to a fact by
    re-reading: promotion requires an explicit human restatement
    as a fact (a new fact record), not a flag flip."""
    record = {
        "category": "opinions", "key": topic, "kind": "opinion",
        "source": "user_stated", "provenance": "",
        "created_at": "", "updated_at": "",
        "valid_from": "", "valid_to": "", "recorded_at": "",
        "supersedes": "", "volatility": "0.8",
        "due_date": "", "last_verified": "", "expires_at": "",
        "value": opinion_text,
    }
    path, _wrote = write_fact(record, base=base)
    return path


def list_people(base=None):
    records, _skipped = list_facts(base=base, kinds=("person",))
    return records


def list_opinions(base=None):
    """Opinions, retrievable ONLY through this explicit query —
    never through the fact prompt."""
    records, _skipped = list_facts(base=base, kinds=("opinion",))
    return records


def overdue_promises(today, base=None):
    """Promises past their due date, oldest first. A promise
    without a due date is never overdue."""
    records, _skipped = list_facts(base=base, kinds=("promise",))
    overdue = [record for record in records
               if record.get("due_date", "")[:10]
               and record["due_date"][:10] < today]
    overdue.sort(key=lambda record: record.get("due_date", ""))
    return overdue


def pressure_valve(base=None):
    """CP-5.5 expiry: when the store exceeds PRESSURE_VALVE_MAX
    records, archive the oldest non-pinned, non-promise records
    until it fits. Pinned identity/preference categories and
    promises are never valved. Archived files go to _expired
    (recoverable, out of recall). Returns archived filenames."""
    from .memory import MemoryStore  # Phase 3 M2h: memory.py moved to core/memory/memory.py
    records, _skipped = list_facts(base=base)
    if len(records) <= PRESSURE_VALVE_MAX:
        return []
    candidates = []
    for record in records:
        if record["kind"] == "promise":
            continue
        category = str(record.get("category", "")).strip().lower()
        key = str(record.get("key", "")).strip().lower()
        if category in MemoryStore.PINNED_CATEGORIES \
                or key in MemoryStore.PINNED_KEYS:
            continue
        candidates.append(record)
    candidates.sort(key=lambda record: (
        record.get("updated_at", ""), record.get("key", "")))
    excess = len(records) - PRESSURE_VALVE_MAX
    root = memories_dir(base)
    expired_dir = os.path.join(root, "_expired")
    os.makedirs(expired_dir, exist_ok=True)
    archived = []
    for record in candidates[:excess]:
        src = fact_path(record["category"], record["key"], base=base)
        try:
            os.replace(src, os.path.join(
                expired_dir, os.path.basename(src)))
            archived.append(os.path.basename(src))
        except OSError:
            continue
    return archived


def delete_person(name, base=None):
    return delete_fact("people", name, base=base)
