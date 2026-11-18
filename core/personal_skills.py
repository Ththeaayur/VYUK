"""S1 Personal library (v5.67, Skills track, plan section 5.19,
wave 27 SK-*).

The user's own skill library. Standard library only.

FENCE 6 — WHERE PERSONAL SKILLS LIVE
-------------------------------------
The library root is INJECTABLE (constructor argument). The
default is a ``VYUK Skills`` folder inside the user's Documents
folder — user-visible, never %AppData% and never the app data
directory. Tests MUST pass an explicit temporary root; nothing
in this module writes to Documents on import, and no test path
is hardcoded as a default.

DRAFT ONLY, NEVER SILENT AUTO-ADMISSION (SK-5-1)
-------------------------------------------------
``create_draft`` writes a skill whose state is ``draft``. A
draft enters the library as a runnable skill ONLY via an
explicit ``admit()`` call. There is no path in this module
that admits a skill implicitly — not creation, not editing,
not import, not restore (restore returns a skill to the state
it held before archiving, which for an admitted skill is
``admitted`` and for a never-admitted skill is its archived
prior state, recorded at archive time).

PARAMETER INFERENCE (SK-5-2)
----------------------------
Two-demo diffing: ``infer_parameters(demo_a, demo_b)`` runs
the two runs of the same steps through a sequence diff, and
the differing blocks become parameters (``param_1``,
``param_2``, ...). At-capture elicitation is CAPPED at
``MAX_ELICITATION_QUESTIONS`` (5) questions, enforced in code:
``apply_elicitation`` raises ValueError on a 6th question, and
``derive_elicitation_questions`` never returns more than 5.

PLAIN-LANGUAGE STEP EDITING (SK-5-3)
-------------------------------------
``edit_steps`` rewrites the steps section of the on-disk
SKILL.md directly. There is NO database shadow copy of the
steps that could drift from the file: the index (index.json)
stores only name / state / aliases / audit flags, never step
text. Every edit first writes a ``SKILL.md.bak`` backup beside
the file.

SPOKEN ALIASES + SAFETY NAMES (SK-5-7)
---------------------------------------
``add_alias`` checks a spoken alias at naming time against
existing skill names and aliases with a phonetic key
(Soundex) plus normalised-text equality: a phonetic collision
is refused. Reserved safety names may NEVER be taken, as a
skill name or as an alias: ``stop``, ``cancel``, ``yes``,
``confirm``, ``quit``, ``exit``, ``abort`` (and ``no``).
These are the stop / cancel / confirmation vocabulary; taking
one would let a skill swallow the words that stop the
assistant or confirm a dangerous action.

ARCHIVE, NOT HARD DELETE (SK-5-8)
----------------------------------
``archive`` moves a skill directory under the library's
``.archive/`` folder with its archived date recorded; it stays
recoverable for ``ARCHIVE_RECOVERABLE_DAYS`` (30) days via
``restore``. ``true_delete`` is the only hard removal: it
deletes the skill directory, its index entry, and any cached
copy under ``.cache/``. ``.bak`` files are written on every
edit (see SK-5-3).

IMPORT (SK-5-6) — PREVIEW, DRY-RUN, QUARANTINE
----------------------------------------------
``preview_import`` reads a foreign skill directory and reports
name / description / steps / manifest findings WITHOUT writing
anything to the library. ``dry_run_import`` is the same preview
plus a statement of exactly what import would write. Import
itself (``import_skill``) lands the skill DISABLED (state
``quarantined``): its steps are readable, but
``steps_for_run`` refuses it and ``can_run`` is False until an
explicit ``admit()``. Imported ``allowed-tools`` is IGNORED
ENTIRELY: the imported SKILL.md is rewritten without the
allowed-tools frontmatter key, and only a boolean audit flag
(``allowed_tools_was_present``) is recorded — never the tool
names, never as any kind of permission (fence 1).

EXPORT + LICENCE STAMP
----------------------
``export_skill`` zips the skill folder. User-authored skills
are stamped ``Personal — © the user`` in frontmatter
``license``. NOTE (owner's product decision, still open):
the stamp text itself is the owner's product decision and
has not been finally ruled; this module stamps the text
above until he rules otherwise.

CLOUD-BRAIN DISCLOSURE
-----------------------
If a draft function ever accepts an external brain, it must
require an explicit per-run disclosure flag. ``create_draft``
therefore takes ``use_external_brain`` (default False,
local-only) and ``disclosure_confirmed``: requesting an
external brain without the flag is refused. NO external call
exists in this module — the flag is the gate, and local-only
is the default.

STATES
------
``draft`` (created, not admitted), ``admitted`` (runnable),
``quarantined`` (imported, disabled until admitted),
``archived`` (moved to .archive/, recoverable 30 days).
``can_run(name)`` is True ONLY for ``admitted``.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

try:  # package import when loaded through the voice_assistant tree
    from voice_assistant.core import skill_format as fmt
except Exception:  # file-path load: load the sibling module directly
    import importlib.util as _ilu
    import sys as _sys

    _spec = _ilu.spec_from_file_location(
        "skill_format",
        Path(__file__).parent / "skill_format.py")
    fmt = _ilu.module_from_spec(_spec)
    # Round-2 N1 BUG 1: the module MUST be registered in
    # sys.modules before exec_module (the importlib recipe).
    # skill_format defines @dataclasses, and dataclass
    # processing reads sys.modules[cls.__module__].__dict__ —
    # unregistered, that lookup returns None and every
    # personal_skills call died with "'NoneType' object has no
    # attribute '__dict__'" before the handler ran. The lab
    # suite never saw it: its voice_assistant.* shim takes the
    # try-branch above, while the funnel (no shim) always took
    # this fallback.
    _sys.modules["skill_format"] = fmt
    _spec.loader.exec_module(fmt)

MAX_ELICITATION_QUESTIONS = 5
ARCHIVE_RECOVERABLE_DAYS = 30
PERSONAL_LICENSE_STAMP = "Personal — © the user"
RESERVED_NAMES = frozenset(
    {"stop", "cancel", "yes", "confirm", "quit", "exit", "abort", "no"})
DEFAULT_LIBRARY_DIRNAME = "VYUK Skills"


def default_library_root() -> Path:
    """Default library root: a VYUK Skills folder inside the
    user's Documents folder (user-visible, never app data)."""
    return Path.home() / "Documents" / DEFAULT_LIBRARY_DIRNAME


# ---------------------------------------------------------------- phonetics

def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def soundex(word: str) -> str:
    """Classic American Soundex (stdlib-only, for the phonetic
    collision check). Returns a 4-character code."""
    cleaned = re.sub(r"[^a-z]", "", str(word).lower())
    if not cleaned:
        return "0000"
    table = {"b": "1", "f": "1", "p": "1", "v": "1",
             "c": "2", "g": "2", "j": "2", "k": "2", "q": "2",
             "s": "2", "x": "2", "z": "2",
             "d": "3", "t": "3", "l": "4", "m": "5", "n": "5",
             "r": "6"}
    first = cleaned[0]
    digits = ""
    prev = table.get(first, "")
    for char in cleaned[1:]:
        code = table.get(char, "")
        if code and code != prev:
            digits += code
        prev = code if code else prev
        if char in "hw":
            prev = table.get(first, "") if False else prev
    return (first.upper() + digits + "000")[:4]


def phonetic_collision(candidate: str, existing: str) -> bool:
    """True when two spoken forms collide: same normalised text
    or same Soundex code (and both non-empty)."""
    if not candidate or not existing:
        return False
    if _normalise(candidate) == _normalise(existing):
        return True
    return soundex(candidate) == soundex(existing)


# ---------------------------------------------------------------- SKILL.md IO

def _render_skill_md(name: str, description: str, steps,
                     license_text: str = PERSONAL_LICENSE_STAMP,
                     extra_metadata: dict | None = None,
                     allowed_tools=None) -> str:
    lines = ["---", f"name: {name}",
             f"description: {description}",
             f"license: {license_text}"]
    if extra_metadata:
        lines.append("metadata:")
        for key, value in extra_metadata.items():
            lines.append(f"  {key}: {value}")
    if allowed_tools is not None:
        tools_text = allowed_tools if isinstance(allowed_tools, str) \
            else " ".join(str(t) for t in allowed_tools)
        lines.append(f"allowed-tools: {tools_text}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    return "\n".join(lines)


def _parse_steps_from_md(text: str) -> list[str]:
    """Read the numbered steps back out of a SKILL.md body."""
    _front, body, _findings = fmt.parse_frontmatter(text)
    steps: list[str] = []
    in_steps = False
    for line in body.splitlines():
        if line.strip().lower().startswith("## steps"):
            in_steps = True
            continue
        if in_steps:
            if re.match(r"^##\s", line.strip()):
                break
            match = re.match(r"^\d+\.\s+(.+)$", line.strip())
            if match:
                steps.append(match.group(1))
    return steps


def _strip_allowed_tools_line(text: str) -> tuple[str, bool]:
    """Remove any allowed-tools frontmatter line. Returns
    (new_text, was_present). Imported allowed-tools is ignored
    entirely (fence 1); only presence is reported, never the
    tool names."""
    lines = text.splitlines()
    kept: list[str] = []
    was_present = False
    in_frontmatter = False
    seen_first = False
    for line in lines:
        if not seen_first and line.strip() == "---":
            seen_first = True
            in_frontmatter = True
            kept.append(line)
            continue
        if in_frontmatter and line.strip() == "---":
            in_frontmatter = False
            kept.append(line)
            continue
        if in_frontmatter and re.match(
                r"^allowed-tools\s*:", line):
            was_present = True
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), \
        was_present


# ---------------------------------------------------------------- library

class PersonalLibrary:
    """A personal skill library rooted at an injectable folder."""

    def __init__(self, root=None):
        self.root = Path(root) if root is not None \
            else default_library_root()
        self.archive_dir = self.root / ".archive"
        self.cache_dir = self.root / ".cache"
        self.index_path = self.root / "index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"skills": []},
                                                   indent=2),
                                       encoding="utf-8")

    # -- index (name/state/aliases/audit ONLY — never step text)
    def _load_index(self) -> list[dict]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            entries = data.get("skills", [])
            return entries if isinstance(entries, list) else []
        except Exception:
            return []

    def _save_index(self, entries: list[dict]) -> None:
        for entry in entries:
            entry.pop("steps", None)  # steps never live in the index
        self.index_path.write_text(
            json.dumps({"skills": entries}, indent=2), encoding="utf-8")

    def _entry(self, name: str) -> dict | None:
        for entry in self._load_index():
            if entry.get("name") == name:
                return entry
        return None

    def _upsert_entry(self, entry: dict) -> None:
        entries = [e for e in self._load_index()
                   if e.get("name") != entry.get("name")]
        entries.append(entry)
        self._save_index(entries)

    def _remove_entry(self, name: str) -> None:
        self._save_index([e for e in self._load_index()
                          if e.get("name") != name])

    def _check_name_available(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Skill name is required")
        if _normalise(name) in {_normalise(r) for r in RESERVED_NAMES} \
                or name.lower() in RESERVED_NAMES:
            raise ValueError(
                f"Name {name!r} is a reserved safety name and may "
                f"never be taken")
        problems = fmt.validate_name(name, dir_name=name)
        errors = [f for f in problems if f.severity == "error"]
        if errors:
            raise ValueError(
                f"Invalid skill name {name!r}: "
                + "; ".join(f.message for f in errors))
        if self._entry(name) is not None or (self.root / name).exists():
            raise ValueError(f"A skill named {name!r} already exists")

    def _check_alias_available(self, alias: str) -> None:
        if not alias or not str(alias).strip():
            raise ValueError("Alias must not be empty")
        if _normalise(alias) in {_normalise(r) for r in RESERVED_NAMES} \
                or str(alias).lower() in RESERVED_NAMES:
            raise ValueError(
                f"Alias {alias!r} is a reserved safety name and may "
                f"never be taken")
        for entry in self._load_index():
            taken = [entry.get("name", "")] + list(
                entry.get("aliases", []))
            for other in taken:
                if phonetic_collision(alias, other):
                    raise ValueError(
                        f"Alias {alias!r} collides phonetically "
                        f"with existing {other!r}")

    # -- draft / admit (SK-5-1)
    def create_draft(self, name: str, steps, description: str = "",
                     aliases=(), use_external_brain: bool = False,
                     disclosure_confirmed: bool = False) -> dict:
        if use_external_brain and not disclosure_confirmed:
            raise ValueError(
                "An external brain requires an explicit per-run "
                "disclosure flag; pass disclosure_confirmed=True "
                "to acknowledge it")
        self._check_name_available(name)
        steps = [str(s).strip() for s in steps]
        if not steps or any(not s for s in steps):
            raise ValueError("A draft needs at least one non-empty step")
        for alias in aliases:
            self._check_alias_available(alias)
        skill_dir = self.root / name
        skill_dir.mkdir(parents=True, exist_ok=False)
        # NOTE: state lives in index.json + the sidecar below,
        # never inside spec frontmatter (which stays spec-clean).
        text = _render_skill_md(
            name, description or f"Personal skill {name}.", steps)
        (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
        entry = {"name": name, "state": "draft",
                 "aliases": list(aliases),
                 "description": description or f"Personal skill {name}.",
                 "allowed_tools_was_present": False,
                 "use_external_brain": bool(use_external_brain),
                 "disclosure_confirmed": bool(disclosure_confirmed),
                 "path": str(skill_dir)}
        (skill_dir / ".vyuk.json").write_text(
            json.dumps(entry, indent=2), encoding="utf-8")
        self._upsert_entry(entry)
        return dict(entry)

    def admit(self, name: str) -> dict:
        """Explicit admission ONLY. A draft or quarantined skill
        becomes admitted; anything else is refused, not coerced."""
        entry = self._entry(name)
        if entry is None:
            return {"ok": False, "name": name,
                    "error": "No such skill in this library"}
        if entry.get("state") not in ("draft", "quarantined"):
            return {"ok": False, "name": name,
                    "error": f"Skill is {entry.get('state')!r}, not "
                             f"admittable (draft/quarantined only)"}
        entry["state"] = "admitted"
        self._upsert_entry(entry)
        sidecar = self.root / name / ".vyuk.json"
        if sidecar.exists():
            sidecar.write_text(json.dumps(entry, indent=2),
                               encoding="utf-8")
        return {"ok": True, "name": name, "state": "admitted"}

    def list_skills(self) -> list[dict]:
        return self._load_index()

    def get_skill(self, name: str) -> dict | None:
        entry = self._entry(name)
        if entry is None:
            return None
        return dict(entry)

    def can_run(self, name: str) -> bool:
        """True ONLY for admitted skills. Drafts and quarantined
        imports can never run."""
        entry = self._entry(name)
        return entry is not None and entry.get("state") == "admitted"

    def read_steps(self, name: str) -> list[str]:
        """Steps are readable for every state (including
        quarantined imports) — readability is not runnability."""
        skill_dir = self.root / name
        if not skill_dir.is_dir():
            archived = self.archive_dir / name
            if archived.is_dir():
                skill_dir = archived
            else:
                raise ValueError(f"No such skill {name!r}")
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        return _parse_steps_from_md(text)

    def steps_for_run(self, name: str) -> list[str]:
        """Steps for execution: refused unless admitted."""
        if not self.can_run(name):
            entry = self._entry(name)
            state = entry.get("state") if entry else "unknown"
            raise PermissionError(
                f"Skill {name!r} is {state!r}, not admitted, and "
                f"cannot run (explicit admit() required)")
        return self.read_steps(name)

    # -- parameters (SK-5-2)
    @staticmethod
    def infer_parameters(demo_a, demo_b) -> list[dict]:
        """Two-demo diffing: differences between two runs of the
        same steps become parameters."""
        a = [str(s) for s in demo_a]
        b = [str(s) for s in demo_b]
        matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        params: list[dict] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            params.append({
                "parameter": f"param_{len(params) + 1}",
                "change": tag,
                "run_a": a[i1:i2],
                "run_b": b[j1:j2],
                "position": i1,
            })
        return params

    @staticmethod
    def derive_elicitation_questions(steps) -> list[str]:
        """At-capture elicitation questions, NEVER more than
        MAX_ELICITATION_QUESTIONS (5). Derived from steps that
        look variable (placeholders, bracketed slots, or
        ALL-CAPS tokens); generic fallbacks fill to the cap."""
        questions: list[str] = []
        for step in steps:
            tokens = re.findall(r"<([^>]+)>|\[([^\]]+)\]",
                                str(step))
            for token in tokens:
                slot = token[0] or token[1]
                question = f"What value should {slot!r} take?"
                if question not in questions:
                    questions.append(question)
            if len(questions) >= MAX_ELICITATION_QUESTIONS:
                break
        fallbacks = [
            "Who or what is this skill for?",
            "Which folder or app should it use?",
            "What should happen if a step fails?",
            "Is there anything it must never do?",
            "How will you know it worked?",
        ]
        for fallback in fallbacks:
            if len(questions) >= MAX_ELICITATION_QUESTIONS:
                break
            if fallback not in questions:
                questions.append(fallback)
        return questions[:MAX_ELICITATION_QUESTIONS]

    @staticmethod
    def apply_elicitation(questions, answers) -> dict:
        """Bind elicitation answers. Enforced in code: more than
        MAX_ELICITATION_QUESTIONS questions raises ValueError."""
        questions = list(questions)
        if len(questions) > MAX_ELICITATION_QUESTIONS:
            raise ValueError(
                f"At-capture elicitation is capped at "
                f"{MAX_ELICITATION_QUESTIONS} questions; "
                f"{len(questions)} were supplied")
        answers = list(answers)
        if len(answers) != len(questions):
            raise ValueError(
                "Elicitation answers must match questions one to one")
        return dict(zip(questions, answers))

    # -- editing (SK-5-3)
    def edit_steps(self, name: str, new_steps) -> dict:
        """Edit the on-disk SKILL.md directly, with a .bak backup
        first. No shadow copy of steps exists anywhere else."""
        entry = self._entry(name)
        if entry is None:
            return {"ok": False, "name": name,
                    "error": "No such skill in this library"}
        skill_dir = self.root / name
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            return {"ok": False, "name": name,
                    "error": "Skill has no SKILL.md"}
        new_steps = [str(s).strip() for s in new_steps]
        if not new_steps or any(not s for s in new_steps):
            return {"ok": False, "name": name,
                    "error": "New step list must be non-empty"}
        old_text = skill_file.read_text(encoding="utf-8")
        (skill_dir / "SKILL.md.bak").write_text(old_text,
                                                encoding="utf-8")
        front, _body, _findings = fmt.parse_frontmatter(old_text)
        description = front.get("description", "") \
            if isinstance(front.get("description"), str) else ""
        license_text = front.get("license", PERSONAL_LICENSE_STAMP) \
            if isinstance(front.get("license"), str) \
            else PERSONAL_LICENSE_STAMP
        metadata = front.get("metadata") \
            if isinstance(front.get("metadata"), dict) else None
        new_text = _render_skill_md(name, description, new_steps,
                                    license_text=license_text,
                                    extra_metadata=metadata)
        skill_file.write_text(new_text, encoding="utf-8")
        return {"ok": True, "name": name, "steps": len(new_steps),
                "bak_written": True}

    # -- aliases (SK-5-7)
    def add_alias(self, name: str, alias: str) -> dict:
        entry = self._entry(name)
        if entry is None:
            return {"ok": False, "name": name,
                    "error": "No such skill in this library"}
        try:
            self._check_alias_available(alias)
        except ValueError as exc:
            return {"ok": False, "name": name, "error": str(exc)}
        entry.setdefault("aliases", []).append(alias)
        self._upsert_entry(entry)
        return {"ok": True, "name": name, "alias": alias,
                "aliases": list(entry["aliases"])}

    # -- archive / delete (SK-5-8)
    def archive(self, name: str) -> dict:
        entry = self._entry(name)
        if entry is None:
            return {"ok": False, "name": name,
                    "error": "No such skill in this library"}
        skill_dir = self.root / name
        if not skill_dir.is_dir():
            return {"ok": False, "name": name,
                    "error": "Skill directory is missing"}
        prior_state = entry.get("state")
        dest = self.archive_dir / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(skill_dir), str(dest))
        entry["state"] = "archived"
        entry["prior_state"] = prior_state
        entry["archived_on"] = date.today().isoformat()
        self._upsert_entry(entry)
        return {"ok": True, "name": name, "state": "archived",
                "archived_on": entry["archived_on"],
                "prior_state": prior_state}

    def restore(self, name: str) -> dict:
        entry = self._entry(name)
        if entry is None or entry.get("state") != "archived":
            return {"ok": False, "name": name,
                    "error": "Skill is not archived"}
        archived_on = entry.get("archived_on", "")
        try:
            age = (date.today()
                   - date.fromisoformat(archived_on)).days
        except ValueError:
            age = 0
        if age > ARCHIVE_RECOVERABLE_DAYS:
            return {"ok": False, "name": name,
                    "error": f"Archive expired: archived "
                             f"{age} days ago, recoverable for "
                             f"{ARCHIVE_RECOVERABLE_DAYS} days"}
        src = self.archive_dir / name
        dest = self.root / name
        if not src.is_dir():
            return {"ok": False, "name": name,
                    "error": "Archived directory is missing"}
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src), str(dest))
        entry["state"] = entry.get("prior_state") or "draft"
        entry.pop("archived_on", None)
        entry.pop("prior_state", None)
        self._upsert_entry(entry)
        return {"ok": True, "name": name, "state": entry["state"]}

    def true_delete(self, name: str) -> dict:
        """Hard delete: directory, index entry, and cached copy.
        No recovery after this call."""
        for candidate in (self.root / name, self.archive_dir / name):
            if candidate.is_dir():
                shutil.rmtree(candidate)
            elif candidate.is_file():
                candidate.unlink()
        cached = self.cache_dir / name
        if cached.is_dir():
            shutil.rmtree(cached)
        elif cached.is_file():
            cached.unlink()
        self._remove_entry(name)
        return {"ok": True, "name": name, "deleted": True}

    # -- import (SK-5-6)
    def preview_import(self, source_dir) -> dict:
        """Preview BEFORE admit: reads, never writes to the
        library. Imported allowed-tools is reported only as a
        boolean presence flag — the names are never carried."""
        source = Path(source_dir)
        result = fmt.validate_skill_dir(source)
        steps: list[str] = []
        skill_file = source / "SKILL.md"
        if skill_file.is_file():
            steps = _parse_steps_from_md(
                skill_file.read_text(encoding="utf-8",
                                     errors="replace"))
        raw_text = skill_file.read_text(encoding="utf-8",
                                        errors="replace") \
            if skill_file.is_file() else ""
        _stripped, allowed_present = _strip_allowed_tools_line(raw_text) \
            if raw_text else ("", False)
        return {
            "name": result.name or source.name,
            "description": result.description,
            "steps": steps,
            "valid": result.valid,
            "findings": [f.to_dict() if hasattr(f, "to_dict") else f
                         for f in result.findings],
            "manifest": result.manifest,
            "signature_tier_requests":
                fmt.signature_tier_requests(result.manifest),
            # Boolean only: imported allowed-tools names are
            # ignored entirely and never stored or returned.
            "allowed_tools_was_present": bool(allowed_present),
            "would_write": str(self.root / (result.name or source.name)),
            "would_state": "quarantined",
        }

    def dry_run_import(self, source_dir) -> dict:
        """Preview + an exact statement of what import would
        write. Writes nothing."""
        preview = self.preview_import(source_dir)
        preview["dry_run"] = True
        preview["writes"] = [
            preview["would_write"] + "/SKILL.md (allowed-tools "
            "stripped)",
            preview["would_write"] + "/.vyuk.json (state sidecar)",
            "index.json entry (state=quarantined, no step text)",
        ]
        preview["does_not_write"] = [
            "No funnel tool declarations",
            "No allowed-tools grant of any kind",
        ]
        return preview

    def import_skill(self, source_dir) -> dict:
        """Import into quarantine: DISABLED until explicit
        admit(). Steps readable, runs refused. Imported
        allowed-tools is stripped from the written SKILL.md."""
        source = Path(source_dir)
        preview = self.preview_import(source)
        name = preview["name"]
        if not name:
            return {"ok": False, "error": "Imported skill has no name"}
        try:
            self._check_name_available(name)
        except ValueError as exc:
            return {"ok": False, "name": name, "error": str(exc)}
        skill_file = source / "SKILL.md"
        raw_text = skill_file.read_text(encoding="utf-8") \
            if skill_file.is_file() else ""
        stripped, allowed_present = _strip_allowed_tools_line(raw_text)
        if not raw_text:
            return {"ok": False, "name": name,
                    "error": "Imported skill has no SKILL.md"}
        dest = self.root / name
        dest.mkdir(parents=True, exist_ok=False)
        (dest / "SKILL.md").write_text(stripped, encoding="utf-8")
        entry = {"name": name, "state": "quarantined",
                 "aliases": [],
                 "description": preview["description"],
                 "allowed_tools_was_present": bool(allowed_present),
                 "path": str(dest)}
        (dest / ".vyuk.json").write_text(
            json.dumps(entry, indent=2), encoding="utf-8")
        self._upsert_entry(entry)
        return {"ok": True, "name": name, "state": "quarantined",
                "allowed_tools_was_present": bool(allowed_present),
                "steps_readable": True, "can_run": False}

    # -- export
    def export_skill(self, name: str, dest_path) -> dict:
        """Zip the skill folder to dest_path."""
        skill_dir = self.root / name
        if not skill_dir.is_dir():
            return {"ok": False, "name": name,
                    "error": "No such skill in this library"}
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w",
                             compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(skill_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(skill_dir))
        return {"ok": True, "name": name, "zip": str(dest),
                "bytes": dest.stat().st_size}
