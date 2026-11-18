# Part of the skill_gate split (Phase B batch B3): helpers family, moved verbatim from core/skill_gate.py.
import hashlib  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.
import re  # INTENTIONAL (skill_gate split B3): stdlib import travelling with the moved code.

MODULE_VERSION = "5.67.0"  # module marker only, NOT the product VERSION

# ---------------------------------------------------------------------------
# Constants (pinned by test_v567_leaf2_gate.py -- change them only with a
# suite update that says why, because pins/diffs in the field depend on them)
# ---------------------------------------------------------------------------

# Never truncate: input over this total is REJECTED, not partially scanned.
MAX_TOTAL_BYTES = 8 * 1024 * 1024
# Decompressed archive content is capped by the same total (zip-bomb guard).
MAX_ARCHIVE_DEPTH = 3
MAX_ARCHIVE_ENTRIES = 500

# Skill classes (SK4-01).
CLASS_DECLARATIVE = 0
CLASS_SCRIPT = 1
CLASS_HYBRID_FETCH_EXECUTE = 2

# Capability scopes (SK4-02). Deny-by-default: anything not explicitly
# granted is refused. ``run:`` and credential-adjacent ``env:`` grants
# are signature-tier: grantable ONLY to VYUK-signed skills.
SCOPES = ("read", "write", "net", "env", "run", "tools", "wasm")
SIGNATURE_TIER_SCOPES = ("run", "wasm")
# Warning-level capabilities: gaining one disables the skill until the
# user re-accepts (SK4-15). ``read`` alone is not warning-level.
WARNING_SCOPES = ("write", "net", "env", "run", "tools", "wasm")
_CREDENTIAL_ENV = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|auth|credential|private)",
    re.IGNORECASE)

# Executable-content markers (SK4-01): extensions, plus shebang sniffing.
_CODE_EXTENSIONS = (".py", ".js", ".mjs", ".cjs", ".sh", ".bash", ".zsh",
                    ".bat", ".cmd", ".ps1", ".exe", ".dll", ".so", ".dylib",
                    ".wasm", ".pyc", ".pyo", ".jar", ".class", ".pl", ".rb")
_ARCHIVE_EXTENSIONS = (".zip", ".whl", ".tar", ".tgz", ".tar.gz", ".tar.bz2")
_TEXT_EXTENSIONS = (".md", ".txt", ".json", ".yaml", ".yml", ".toml",
                    ".html", ".htm")
# Fetch/execute-at-runtime markers (SK4-01, Class 2). Applied to scanned
# text: a skill that downloads code or generates code and runs it is
# Class 2 regardless of what its folder contains today.
_FETCH_EXECUTE_PATTERNS = (
    re.compile(r"\bcurl\s+[^\n]*https?://", re.IGNORECASE),
    re.compile(r"\bwget\s+[^\n]*https?://", re.IGNORECASE),
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    re.compile(r"\burllib\b", re.IGNORECASE),
    re.compile(r"\brequests\s*\.\s*(get|post)\b", re.IGNORECASE),
    re.compile(r"\bdownload\s+and\s+(run|execute|install)\b", re.IGNORECASE),
    re.compile(r"\bgenerat\w*\s+code\b[^\n]*\b(run|execut\w+)\b", re.IGNORECASE),
    re.compile(r"\|\s*(sh|bash)\b"),
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
# HTML-comment instruction channels (SK4-13): a hidden comment that
# tells someone (or something) to DO something is a finding.
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_INSTRUCTION_VERBS = re.compile(
    r"\b(run|execute|install|download|ignore|disregard|override|bypass|"
    r"system|instruction|secret|token|exfiltrate|disable)\b",
    re.IGNORECASE)
_TAG_START, _TAG_END = 0xE0000, 0xE007F

# Plain-language rendering of scopes for the skill card (SK4-03). The
# card NEVER shows raw scopes: "read:Documents" renders as a sentence.
_SCOPE_SENTENCES = {
    "read": "Can read files in {target}",
    "write": "Can change files in {target}",
    "net": "Can contact {target}",
    "env": "Can read the {target} setting",
    "run": "Can run {target}",
    "tools": "Can use the {target} tool",
    "wasm": "Can run contained code from {target}",
}

# Confusable (homoglyph) folding for anti-typosquat checks (SK4-16):
# Cyrillic/Greek lookalikes and common digit substitutions fold to Latin.
_CONFUSABLES = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0445": "x", "\u0443": "y", "\u043a": "k",
    "\u043c": "m", "\u0442": "t", "\u043d": "h", "\u03bf": "o",
    "\u03b1": "a", "\u03b5": "e", "\u03c1": "p", "\u03c4": "t",
    "\u03ba": "k", "\u03bd": "v", "\u03bc": "u", "0": "o",
    "1": "l", "5": "s", "@": "a", "$": "s",
}
# Levenshtein distance at or below this (for names of length >= 4)
# counts as a typosquat flag against an existing name.
TYPOSQUAT_MAX_DISTANCE = 2

EXECUTION_REFUSED_REASON = (
    "This skill's code cannot run here: VYUK does not run skill "
    "code in-process or as a bare shell-out. Its steps and cards "
    "are readable as data only. The Wasmtime WASM pilot (SK4-05, "
    "core/skill_runtime.py) runs only a declared, VYUK-signed "
    "WASM skill with no host access; anything else is still "
    "refused. The AppContainer broker (SK4-07) is a Wave 24 "
    "build and is not available yet."
)


def _finding(path, code, severity, detail):
    """One triage finding. Disposition rule (SK4-10): "suspicious"
    and "block" both mean BLOCKED -- never warned-and-passed."""
    disposition = "blocked" if severity in ("suspicious", "block") else "note"
    return {"path": str(path), "code": code, "severity": severity,
            "disposition": disposition, "detail": detail}


def _is_blocked(findings):
    return any(f["disposition"] == "blocked" for f in findings)


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _is_code_name(name):
    lower = name.lower()
    return lower.endswith(_CODE_EXTENSIONS)


def _is_archive_name(name):
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _ARCHIVE_EXTENSIONS)


def _looks_like_script(data):
    """Shebang sniff: a text file starting with #! that names an
    interpreter is executable content even without an extension."""
    if not data.startswith(b"#!"):
        return False
    head = data[:128].decode("utf-8", errors="replace").lower()
    return any(word in head for word in
               ("python", "sh", "bash", "perl", "ruby", "node", "pwsh"))
