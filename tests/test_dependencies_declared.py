"""GAPS-30 (D-V3/D-V4): every third-party import is declared or
documented-optional; every declared-but-never-imported dist has
its reason recorded in requirements.txt.

D-V3: the review found >=19 undeclared imports. The scan below
is submodule-aware (``opentelemetry.sdk`` is opentelemetry-sdk,
``google.genai`` is google-genai, ``google.auth`` is google-auth
— a top-level-only scan misattributes all three) and counts
dynamic loads too (``importlib.import_module`` / ``_load_optional``
string literals: diskcache, whenever, aiohappyeyeballs, pywinctl,
pymonctl). An import is covered when its dist is declared in
requirements.txt or listed in the DOCUMENTED-OPTIONAL section
there (optional enhancement, probed at runtime, never bundled —
the superqt / pyqtgraph precedent, now written down for all of
them instead of two GUI modules).

D-V4: a declared dist with no STATIC import in core/ + gui/ is
not automatically dead: it is TRANSITIVE (a declared package's
Requires-Dist pulls it in — httpx pulls certifi, google-genai
pulls websockets, google-api-python-client pulls
google-auth-httplib2) or DYNAMIC (first-party code loads it
through importlib.import_module — pywinctl / pymonctl in
core/desktop/pywinctl_wrapper.py) or a namespace dependency
(opentelemetry-instrumentation: no file imports it directly,
but opentelemetry.instrumentation.google_genai cannot import
without the namespace installed). The reason for EACH such
dist is recorded in requirements.txt's D-V4 section; a dist
with no reason recorded fails this test. The one genuinely
dead declaration, python-dotenv (zero import hits anywhere —
core/config_env.py has its own simple .env loader), was
REMOVED, not documented: see test_no_dead_dotenv_declaration.

scan_tree(tree_root) returns the scan dict so the batch prover
can re-run the same shapes without the test harness. Stdlib
only; it never imports core/ or gui/.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent

# Full dotted module -> dist (longest-prefix match wins).
# Submodule-aware: without this, every opentelemetry.* import
# collapses to opentelemetry-api and google.auth collapses
# to google-genai, and both misattributions hide real gaps.
SUBMODULE_MAP = {
    "opentelemetry.instrumentation.google_genai":
        "opentelemetry-instrumentation-google-genai",
    "opentelemetry.instrumentation": "opentelemetry-instrumentation",
    "opentelemetry.semconv": "opentelemetry-semantic-conventions",
    "opentelemetry.sdk": "opentelemetry-sdk",
    "opentelemetry.trace": "opentelemetry-api",
    "opentelemetry": "opentelemetry-api",
    "google.genai": "google-genai",
    "google.auth": "google-auth",
    "google.oauth2": "google-auth",
    "googleapiclient": "google-api-python-client",
}

# Top-level import name -> dist (where they differ).
ALIAS = {
    "cv2": "opencv-python-headless",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "Crypto": "pycryptodome",
    "dotenv": "python-dotenv",
    "mss": "python-mss",
    "llama_cpp": "llama-cpp-python",
    "kokoro_onnx": "kokoro-onnx",
    "faster_whisper": "faster-whisper",
    "presidio_analyzer": "presidio-analyzer",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "google_auth_httplib2": "google-auth-httplib2",
    "serial": "pyserial",
    "win32cred": "pywin32",
    "win32print": "pywin32",
    "win32ctypes": "pywin32-ctypes",
    "win32api": "pywin32",
    "win32con": "pywin32",
    "win32gui": "pywin32",
    "win32com": "pywin32",
    "piper": "piper-tts",
    "speech_recognition": "SpeechRecognition",
    "yt_dlp": "yt-dlp",
    "pydantic_settings": "pydantic-settings",
    "markdown_it": "markdown-it",
}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _dist_for(dotted: str) -> str:
    """Full dotted module name -> dist name."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in SUBMODULE_MAP:
            return SUBMODULE_MAP[prefix]
    top = parts[0]
    return ALIAS.get(top, top)


def _declared(tree_root: Path) -> set[str]:
    declared: set[str] = set()
    for line in (tree_root / "requirements.txt").read_text(
            encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if m:
            declared.add(_norm(m.group(1)))
    return declared


def _documented_optional(tree_root: Path) -> set[str]:
    """Names from the DOCUMENTED-OPTIONAL section of
    requirements.txt: import names and dist names, normalized.
    The section runs from its header to the first blank line."""
    names: set[str] = set()
    lines = (tree_root / "requirements.txt").read_text(
        encoding="utf-8").splitlines()
    in_section = False
    for line in lines:
        if "DOCUMENTED-OPTIONAL (GAPS-30" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if not line.strip():
            break
        if not line.startswith("#"):
            break
        m = re.match(r"#\s+(\S+)(?:\s+\(dist\s+(\S+)\))?", line)
        if m:
            names.add(_norm(m.group(1)))
            if m.group(2):
                names.add(_norm(m.group(2)))
    return names


def scan_tree(tree_root) -> dict:
    """Scan core/ + gui/ (+ main.py / doctor.py) for third-party
    imports. Returns the scan dict."""
    root = Path(tree_root)
    stdlib = set(sys.stdlib_module_names)
    first_party = {"core", "gui", "tests", "voice_assistant",
                   "installer"}
    first_party |= {p.stem for p in root.glob("*.py")}
    imported: dict[str, set[str]] = {}  # dist -> files (static)
    dynamic: dict[str, set[str]] = {}  # dist -> files (dynamic)
    sources = [root / "main.py", root / "doctor.py"]
    sources += sorted((root / "core").rglob("*.py"))
    sources += sorted((root / "gui").rglob("*.py"))
    for path in sources:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in stdlib or top in first_party:
                        continue
                    dist = _dist_for(alias.name)
                    imported.setdefault(dist, set()).add(rel)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top in stdlib or top in first_party:
                        continue
                    for alias in node.names:
                        dist = _dist_for(node.module + "."
                                         + alias.name)
                        imported.setdefault(dist, set()).add(rel)
            elif isinstance(node, ast.Call):
                func = node.func
                fname = func.attr if isinstance(func, ast.Attribute) \
                    else (func.id if isinstance(func, ast.Name)
                          else "")
                if fname in ("import_module", "__import__",
                             "_load_optional", "load_optional") \
                        and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) \
                            and isinstance(arg.value, str):
                        top = arg.value.split(".")[0]
                        if top in stdlib or top in first_party \
                                or not top:
                            continue
                        dist = _dist_for(arg.value)
                        dynamic.setdefault(dist, set()).add(rel)
    declared = _declared(root)
    documented = _documented_optional(root)
    all_imported = {**imported}
    for dist, files in dynamic.items():
        all_imported.setdefault(dist, set()).update(files)
    not_covered = sorted(
        dist for dist in all_imported
        if _norm(dist) not in declared
        and _norm(dist) not in documented)
    # "Never statically imported" counts STATIC imports only:
    # a dist loaded through importlib (pywinctl) is DYNAMIC,
    # not imported — its reason is recorded, not hidden.
    never = sorted(
        dist for dist in declared
        if dist not in {_norm(d) for d in imported})
    return {
        "imported": sorted(all_imported),
        "imported_static": sorted(imported),
        "imported_dynamic": sorted(dynamic),
        "declared": sorted(declared),
        "documented_optional": sorted(documented),
        "imported_not_declared_or_documented": not_covered,
        "declared_never_imported": never,
    }


def _reasoned(tree_root: Path, dist: str) -> bool:
    """Is dist's never-imported reason recorded in
    requirements.txt — on a line carrying a TRANSITIVE or
    DYNAMIC marker, or in the D-V4 section?"""
    text = (tree_root / "requirements.txt").read_text(
        encoding="utf-8")
    for line in text.splitlines():
        if dist in line and ("TRANSITIVE" in line
                             or "DYNAMIC" in line):
            return True
    section = text[text.find("D-V4"):] if "D-V4" in text else ""
    return dist in section


class DependencyDeclarationTests(unittest.TestCase):
    def test_every_import_declared_or_documented(self):
        result = scan_tree(TREE_ROOT)
        self.assertEqual(
            result["imported_not_declared_or_documented"], [],
            result["imported_not_declared_or_documented"])

    def test_declared_never_imported_all_reasoned(self):
        result = scan_tree(TREE_ROOT)
        unreasoned = [d for d in result["declared_never_imported"]
                      if not _reasoned(TREE_ROOT, d)]
        self.assertEqual(unreasoned, [],
                         "declared but never statically imported "
                         "with no TRANSITIVE/DYNAMIC reason in "
                         "requirements.txt")

    def test_no_dead_dotenv_declaration(self):
        # D-V4a: python-dotenv is truly unused — zero import hits
        # anywhere; core/config_env.py has its own .env loader.
        # It was removed from requirements.txt and the lock, not
        # documented as unused.
        text = (TREE_ROOT / "requirements.txt").read_text(
            encoding="utf-8")
        self.assertNotIn("python-dotenv", text)
        self.assertNotIn("dotenv", text)
        lock = (TREE_ROOT / "requirements.lock").read_text(
            encoding="utf-8")
        self.assertNotIn("python-dotenv", lock)


if __name__ == "__main__":
    unittest.main()
