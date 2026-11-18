# Part of the skill_gate split (Phase B batch B3): scan family, moved verbatim from core/skill_gate.py.
from pathlib import Path  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import io  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import json  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import re  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import tarfile  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import zipfile  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
from .skill_gate_helpers import CLASS_DECLARATIVE, CLASS_HYBRID_FETCH_EXECUTE, CLASS_SCRIPT, MAX_ARCHIVE_DEPTH, MAX_ARCHIVE_ENTRIES, MAX_TOTAL_BYTES, _FETCH_EXECUTE_PATTERNS, _HTML_COMMENT, _INSTRUCTION_VERBS, _TAG_END, _TAG_START, _TEXT_EXTENSIONS, _URL_PATTERN, _finding, _is_archive_name, _is_blocked, _is_code_name, _looks_like_script  # INTENTIONAL (skill_gate split B3): cross-part name from the helpers module.

def _scan_text_bytes(rel, data, findings, code_seen, fetch_seen):
    """Text-level checks on one blob: invisible channels, hidden
    HTML instructions, fetch/execute markers, URLs. Returns the set
    of derived capability scopes found in this blob."""
    derived = set()
    text = data.decode("utf-8", errors="replace")
    for ch in text:
        if _TAG_START <= ord(ch) <= _TAG_END:
            findings.append(_finding(
                rel, "invisible-unicode-tags", "suspicious",
                "Unicode Tags block characters (U+E0000-E007F) found: "
                "an invisible instruction channel, not formatting."))
            break
    if rel.lower().endswith(".md"):
        for match in _HTML_COMMENT.finditer(text):
            if _INSTRUCTION_VERBS.search(match.group(1)):
                findings.append(_finding(
                    rel, "hidden-html-instruction", "suspicious",
                    "An HTML comment in the skill text contains "
                    "instruction words: hidden instructions are "
                    "treated as findings, not formatting."))
                break
    for pattern in _FETCH_EXECUTE_PATTERNS:
        if pattern.search(text):
            fetch_seen.add(rel)
            derived.add("run")
            break
    if _URL_PATTERN.search(text):
        derived.add("net")
    if "environ" in text or "$" in text and re.search(r"\$[A-Z_]{2,}", text):
        derived.add("env")
    if re.search(r"\bopen\s*\([^)]*['\"]w", text) or ".write(" in text:
        derived.add("write")
    if "read" in text and re.search(r"\b(open|read)\s*\(", text):
        derived.add("read")
    return derived


def _check_pyc(rel, data, sibling_py, findings):
    """Compiled-artefact vs claimed-source divergence (SK4-13).
    A .pyc without its .py is a finding; a .pyc whose header
    mtime/size disagree with the sibling .py is a divergence
    finding. A matching header is NOT proof of sameness -- the
    scan says so in the finding-free case by still flagging the
    artefact as compiled content (see scan: .pyc always counts
    as code)."""
    if sibling_py is None:
        findings.append(_finding(
            rel, "pyc-without-source", "suspicious",
            "A compiled .pyc file with no matching .py source: "
            "its claimed source cannot be checked."))
        return
    if len(data) < 16:
        findings.append(_finding(
            rel, "pyc-malformed", "suspicious",
            "A .pyc file too short to carry a header."))
        return
    header_mtime = int.from_bytes(data[8:12], "little")
    header_size = int.from_bytes(data[12:16], "little")
    try:
        stat = sibling_py.stat()
        if int(stat.st_mtime) != header_mtime or stat.st_size != header_size:
            findings.append(_finding(
                rel, "pyc-divergence", "suspicious",
                "The compiled .pyc header (mtime/size) does not "
                "match its .py source: compiled artefact != "
                "claimed source."))
    except OSError:
        pass


def _scan_archive_bytes(rel, data, depth, findings, derived, code_seen,
                        fetch_seen):
    """Recursively expand one archive blob BEFORE scanning its
    members (SK4-13): code hidden inside a nested archive is still
    code. Returns False when the archive is unreadable/over-deep."""
    if depth > MAX_ARCHIVE_DEPTH:
        findings.append(_finding(
            rel, "archive-too-deep", "block",
            "Archive nesting exceeds the scan depth limit: "
            "contents beyond it cannot be verified."))
        return False
    members = []  # (member name, bytes)
    try:
        if rel.lower().endswith((".zip", ".whl")):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if len(names) > MAX_ARCHIVE_ENTRIES:
                    findings.append(_finding(
                        rel, "archive-too-many-entries", "block",
                        "Archive has more entries than the scan "
                        "entry limit."))
                    return False
                for name in names:
                    if name.endswith("/"):
                        continue
                    members.append((name, zf.read(name)))
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                members_list = tf.getmembers()
                if len(members_list) > MAX_ARCHIVE_ENTRIES:
                    findings.append(_finding(
                        rel, "archive-too-many-entries", "block",
                        "Archive has more entries than the scan "
                        "entry limit."))
                    return False
                for member in members_list:
                    if not member.isfile():
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is not None:
                        members.append((member.name, extracted.read()))
    except Exception:  # noqa: BLE001 -- unreadable archive = finding
        findings.append(_finding(
            rel, "archive-unreadable", "suspicious",
            "An archive in the skill could not be opened for "
            "scanning: unscanned content is not admissible."))
        return False
    decompressed = sum(len(blob) for _, blob in members)
    if decompressed > MAX_TOTAL_BYTES:
        findings.append(_finding(
            rel, "archive-oversize", "block",
            "Archive decompresses past the scan byte cap: "
            "rejected, not partially scanned."))
        return False
    for name, blob in members:
        member_rel = f"{rel}!{name}"
        if _is_archive_name(name):
            _scan_archive_bytes(member_rel, blob, depth + 1, findings,
                                derived, code_seen, fetch_seen)
        elif _is_code_name(name):
            code_seen.add(member_rel)
            derived.add("run")
        else:
            derived.update(_scan_text_bytes(member_rel, blob, findings,
                                            code_seen, fetch_seen))
    return True


def _read_declared(skill_dir):
    """Declared grants + version, from skill-manifest.json when
    present, else from ``Grant: scope:target`` lines in SKILL.md.
    Returns (grants list, version str). Declarations are claims
    only -- the card labels them against the derived scan."""
    manifest_path = skill_dir / "skill-manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            grants = [str(g) for g in data.get("capabilities", [])]
            return grants, str(data.get("version", ""))
        except Exception:  # noqa: BLE001 -- bad manifest = no grants
            return [], ""
    skill_md = skill_dir / "SKILL.md"
    grants = []
    if skill_md.is_file():
        for line in skill_md.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
            match = re.match(r"\s*Grant:\s*(\S+:\S+)\s*$", line)
            if match:
                grants.append(match.group(1))
    return grants, ""


def scan_skill_dir(skill_dir):
    """Scan a skill directory. Returns a dict with:

    * ``class``: 0 (declarative-only), 1 (script-carrying), or 2
      (hybrid fetch/execute -- treated as Class 1 for admission);
      None when the scan itself was rejected (oversize).
    * ``admitted``: False when any finding blocks, else True
      (admission as data; execution is a separate refusal -- see
      ``admit_skill``).
    * ``trusted``: ALWAYS False. ``taint_exempt``: ALWAYS False.
      Class 0 is default-admissible, never trusted, never exempt.
    * ``findings``: triage findings, each with a ``disposition``
      ("blocked" for suspicious/block, else "note").
    * ``derived_capabilities``: scopes the scan itself found.
    * ``declared_capabilities``: scopes the author claimed.
    * ``files``: relative paths seen (archives expanded inline as
      ``archive!member`` entries, never silently skipped).
    * ``rejected``: reason string when the scan rejected outright.
    """
    root = Path(skill_dir)
    base = {"class": None, "admitted": False, "trusted": False,
            "taint_exempt": False, "findings": [], "derived_capabilities": [],
            "declared_capabilities": [], "files": [], "rejected": ""}
    if not root.is_dir():
        base["rejected"] = "skill directory does not exist"
        base["findings"] = [_finding(str(root), "missing-directory",
                                     "block", base["rejected"])]
        return base
    files = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            try:
                target = path.resolve()
                if root.resolve() not in target.parents and \
                        target != root.resolve():
                    base["findings"].append(_finding(
                        path.relative_to(root), "symlink-escape",
                        "block", "A symlink points outside the "
                        "skill directory."))
            except OSError:
                pass
            continue
        files.append(path)
        try:
            total += path.stat().st_size
        except OSError:
            pass
    if total > MAX_TOTAL_BYTES:
        base["rejected"] = (
            f"skill totals {total} bytes, over the scan cap of "
            f"{MAX_TOTAL_BYTES} bytes: rejected, not truncated "
            f"or partially scanned.")
        base["findings"] = [_finding(str(root), "oversize", "block",
                                     base["rejected"])]
        return base
    findings = list(base["findings"])
    derived = set()
    code_seen = set()
    fetch_seen = set()
    seen_files = []
    py_sources = {}
    for path in files:
        rel = str(path.relative_to(root))
        seen_files.append(rel)
        if rel.lower().endswith(".py"):
            py_sources[path.stem] = path
    for path in files:
        rel = str(path.relative_to(root))
        try:
            data = path.read_bytes()
        except OSError:
            findings.append(_finding(rel, "unreadable", "suspicious",
                                     "A skill file could not be read."))
            continue
        if _is_archive_name(rel):
            _scan_archive_bytes(rel, data, 1, findings, derived,
                                code_seen, fetch_seen)
        elif _is_code_name(rel):
            code_seen.add(rel)
            derived.add("run")
            if rel.lower().endswith(".pyc"):
                _check_pyc(rel, data, py_sources.get(path.stem), findings)
            else:
                derived.update(_scan_text_bytes(rel, data, findings,
                                                code_seen, fetch_seen))
        elif rel.lower().endswith(_TEXT_EXTENSIONS) or \
                _looks_like_script(data):
            if _looks_like_script(data):
                code_seen.add(rel)
                derived.add("run")
            derived.update(_scan_text_bytes(rel, data, findings,
                                            code_seen, fetch_seen))
        # Unknown blobs: data files (models, images) are allowed
        # in Class 0, but executable magic bytes are code no
        # matter what extension hides them (ELF, MZ/PE, Mach-O).
        elif data[:4] == b"\x7fELF" or data[:2] == b"MZ" or \
                data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                             b"\xca\xfe\xba\xbe"):
            derived.add("run")
            code_seen.add(rel)
    declared, version = _read_declared(root)
    if fetch_seen:
        skill_class = CLASS_HYBRID_FETCH_EXECUTE
    elif code_seen:
        skill_class = CLASS_SCRIPT
    else:
        skill_class = CLASS_DECLARATIVE
    base.update({"class": skill_class,
                 "admitted": not _is_blocked(findings),
                 "findings": findings,
                 "derived_capabilities": sorted(derived),
                 "declared_capabilities": sorted(set(declared)),
                 "files": seen_files, "version": version,
                 "code_files": sorted(code_seen),
                 "fetch_execute_files": sorted(fetch_seen)})
    return base
