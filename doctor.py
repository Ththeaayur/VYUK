#!/usr/bin/env python3
"""
VYUK doctor — a zero-dependency health check for this package.
Run from INSIDE the package folder:  python doctor.py

Standard library only at startup; never opens the GUI; never crashes
on a broken install. Every check prints [PASS]/[WARN]/[FAIL] with a
one-line detail; failures add a "fix:" hint naming the file or command
involved. Exit code 0 = no FAILs, 1 otherwise. FAIL = broken. WARN =
degraded but survivable (missing optional extra, no API key yet,
headless box, or a module import blocked *only* by a dependency the
dependency check already reported — counted once, there).
"""

import importlib
import importlib.util
import os
import py_compile
import shutil
import socket
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

# Fix round (F-P4-02, parent ruling 2026-11-26): per-check
# outcomes — each check contributes exactly one outcome, its
# worst verdict (FAIL > WARN > PASS). The hero counts CHECKS
# (37), not verdict lines (68): _counts above is the verdict
# tally the CLI SUMMARY prints; _check_outcomes is the
# per-check tally the CHECK_SUMMARY line prints.
_CHECK_SEVERITY = {"PASS": 0, "WARN": 1, "FAIL": 2}
_check_outcomes: dict[str, str] = {}


def _check_summary_counts() -> dict:
    """Per-check outcome counts from _check_outcomes."""
    out = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for status in _check_outcomes.values():
        out[status] += 1
    return out


# UI Phase 4 (D-P4-17): every check declares one of six
# categories — the page groups findings by category inside
# severity, and the suite asserts this table covers _CHECKS
# exactly (a check with no category is a build failure).
_CATEGORIES = ("connectivity", "permissions", "installation",
               "model/provider", "runtime", "recovery")
_CHECK_CATEGORIES = {
    # connectivity — reaching something outside this machine.
    "check_cloud_doors": "connectivity",
    "check_mcp_servers": "connectivity",
    "check_home_assistant": "connectivity",
    "check_world": "connectivity",
    # permissions — who can read or take what.
    "check_secrets_storage": "permissions",
    "check_data_egress": "permissions",
    # installation — the shipped tree and its dependencies.
    "check_python": "installation",
    "check_layout": "installation",
    "check_compile": "installation",
    "check_deps": "installation",
    "check_module_imports": "installation",
    "check_integrity": "installation",
    "check_assets": "installation",
    # model/provider — the brain, its keys, its voices.
    "check_brain": "model/provider",
    "check_config": "model/provider",
    "check_gemini_key_tier": "model/provider",
    "check_piper_voice": "model/provider",
    # runtime — the running app and its capabilities.
    "check_process_load": "runtime",
    "check_tool_registry": "runtime",
    "check_pack_registrations": "runtime",
    "check_memory_profile": "runtime",
    "check_data": "runtime",
    "check_presence": "runtime",
    "check_watchers": "runtime",
    "check_tool_payload": "runtime",
    "check_tool_router": "runtime",
    "check_ocr_engine": "runtime",
    "check_capability_probes": "runtime",
    "check_real_chrome": "runtime",
    "check_microphone": "runtime",
    "check_mic_drops": "runtime",
    "check_platform_backend": "runtime",
    "check_learning": "runtime",
    # recovery — state the app can rebuild or lose.
    "check_state_files": "recovery",
    "check_receipt_store": "recovery",
    "check_write_proof": "recovery",
    "check_failure_clusters": "recovery",
}
# The check currently running, by name — set by main() (and by
# the Doctor page's in-process fallback) before each check, so
# report() can print the finding's category marker. None
# outside a run.
_CURRENT_CHECK_NAME = None


# UI Phase 4 (D-P4-16, R-P4-A): the ONLY packages a Doctor
# repair may pip-install — the declared dependency set,
# licence-cleared, keyed by the import name the finding's
# label carries. The pytesseract spec is requirements.txt's
# `pytesseract>=0.3.10`, NOT the doctor fix text (which names
# no version — it would install an unpinned package). A fix
# naming a package outside this table gets "No automatic
# repair", never a pip install of an undeclared package.
_PIP_REPAIR_SPECS = {
    "google.genai": "google-genai>=2.0.0",
    "PySide6": "PySide6>=6.6.0",
    "sounddevice": "sounddevice>=0.5.0",
    "numpy": "numpy>=1.26.0",
    "PIL": "pillow>=10.0.0",
    "websockets": "websockets>=13.0",
    # No "dotenv" entry, deliberately (R-P4-A): the product
    # never imports dotenv — core/config_env.py carries its
    # own .env loader — and python-dotenv is deliberately
    # absent from requirements.txt/lock (see
    # test_dependencies_declared.py, test_gaps30.py). A
    # repair must never install an undeclared package, so a
    # "Dependency: dotenv" FAIL gets no repair and the card
    # says why. (The _DEPS row below still FAILs on a missing
    # dotenv — pre-existing, flagged for a parent ruling.)
    "psutil": "psutil>=5.9.0",
    "pyperclip": "pyperclip>=1.8.2",
    "cv2": "opencv-python-headless>=4.9.0",
    "pytesseract": "pytesseract>=0.3.10",
    "llama_cpp": "llama-cpp-python",
    "faster_whisper": "faster-whisper",
    "kokoro_onnx": "kokoro-onnx",
}


def _make_output_encoding_safe():
    """Best-effort UTF-8 output for the real console run.

    A cp1252 Windows console cannot encode '→' (U+2192); with
    errors='replace' the console prints '?' instead of raising.
    Streams that cannot reconfigure (StringIO, redirected pipes
    without reconfigure) keep working as-is — report()'s _emit
    fallback is the layer that guarantees no crash."""


    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — reconfigure is best-effort
            pass


def _emit(text):
    """Print without ever crashing on the console's encoding.

    A cp1252 Windows console cannot encode characters like
    '→' (U+2192); on a strict stream that raises
    UnicodeEncodeError and aborts the check that was reporting
    (the field crash: check_watchers and check_world both died
    inside report()). Fall back to the stream's encoding with
    errors='replace', so the line still prints — with '?' in
    place of unencodable characters — instead of killing the
    doctor."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc))


def report(status, label, detail="", fix=""):
    _counts[status] += 1
    # Fix round (F-P4-02): the running check's worst verdict —
    # a check that reports several lines still counts once.
    _name = _CURRENT_CHECK_NAME or ""
    if _name:
        _prev = _check_outcomes.get(_name)
        if (_prev is None
                or _CHECK_SEVERITY[status] > _CHECK_SEVERITY[_prev]):
            _check_outcomes[_name] = status
    _emit(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if fix and status != "PASS":
        _emit(f"       fix: {fix}")
    # UI Phase 4 (D-P4-17): the finding's category, as a marker
    # line the Doctor page's parser attaches to the finding
    # above it. Findings only — PASS lines carry no card, and a
    # missing marker (an old cached report) reads as no
    # category on the page, never as a wrong one.
    if status != "PASS":
        _cat = _CHECK_CATEGORIES.get(_CURRENT_CHECK_NAME or "")
        if _cat:
            _emit(f"       category: {_cat}")


def check_python():
    v = sys.version_info
    if v >= (3, 10):
        report("PASS", f"Python version — running {v.major}.{v.minor}.{v.micro}")
    else:
        report("FAIL", f"Python version — running {v.major}.{v.minor}.{v.micro}",
               "VYUK needs Python 3.10+", "install Python 3.10 or newer and re-run")


def check_layout():
    missing = [d for d in ("core", "gui", "plugins", "data") if not (ROOT / d).is_dir()]
    if missing:
        report("FAIL", "Package layout", f"missing folder(s): {', '.join(missing)}",
               "doctor.py belongs in the main VYUK folder, next to the core, gui, plugins and data folders — re-extract the package")  # v5.51 (X-1): plain words
    else:
        report("PASS", "Package layout", "core/, gui/, plugins/, data/ all present")
    if ROOT.name != "voice_assistant":
        report("WARN", "Folder name", f"this folder is named '{ROOT.name}', not 'voice_assistant'",
               "rename the folder to voice_assistant — VYUK's parts refer to each other by that name, so it won't start under any other name")  # v5.51 (X-1): no exception names in verdicts
    else:
        report("PASS", "Folder name", "folder is named voice_assistant")


def check_process_load():
    """Fix 6 (2026-11-05): VYUK's OWN load, separated from the
    machine's. The field "100% CPU / 91% memory" came from
    machine-wide psutil reads taken while Doctor itself was
    py-compiling 1,216 files (check_compile below: 3.36 s of full
    CPU in Doctor's own process) — Doctor measuring itself plus
    the machine, not VYUK's idle load. This check therefore runs
    BEFORE check_compile in _CHECKS, and reports the own-process
    numbers (RSS + CPU from core/perf.py) beside the machine
    numbers, labelled as such. It also reports the activity-log
    size (rotates at 1 MB, newest 2,000 lines — core/perf.py)."""
    from core import perf  # noqa: PLC0415 — function-level, same as the other checks' imports
    load = perf.process_load(cpu_interval=0.1)
    if not load.get("psutil_available"):
        report("WARN", "VYUK process load",
               "psutil is not installed — own-process CPU/RSS cannot be read",
               "pip install 'psutil>=5.9.0'  (or: pip install -r requirements.txt)")
        return
    rss = load.get("process_rss_mb")
    cpu = load.get("process_cpu_percent")
    mc = load.get("machine_cpu_percent")
    mm = load.get("machine_mem_percent")
    bits = []
    if isinstance(rss, (int, float)):
        bits.append(f"this process RSS {rss:.0f} MB")
    if isinstance(cpu, (int, float)):
        bits.append(f"this process CPU {cpu:.0f}%")
    if isinstance(mc, (int, float)) and isinstance(mm, (int, float)):
        bits.append(f"machine CPU {mc:.0f}%, memory {mm:.0f}% full")
    if bits:
        report("PASS", "VYUK process load", "; ".join(bits) +
               " — own-process numbers first; machine numbers include every other app and Doctor itself")
    else:
        report("WARN", "VYUK process load",
               "psutil is present but no load reading came back usable",
               "reinstall psutil; the check is report-only")
    size = perf.log_size_bytes()
    report("PASS" if size <= perf.LOG_MAX_BYTES else "WARN",
           "Activity log",
           f"data/activity.log is {size / 1024.0:.0f} KB"
           + (" (rotates at 1 MB, newest 2,000 lines kept)"
              if size <= perf.LOG_MAX_BYTES else
              " — over the 1 MB cap; it rotates at the next launch"))


def check_compile():
    files = sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, path in enumerate(files):
            try:
                py_compile.compile(str(path), cfile=str(Path(tmp) / f"{i}.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                bad.append((path, str(e).splitlines()[-1] if str(e) else "compile error"))
    if not bad:
        report("PASS", "Syntax check", f"all {len(files)} .py files compile")
    for path, err in bad:
        report("FAIL", f"Syntax check: {path.relative_to(ROOT)}", err,
               f"open {path.relative_to(ROOT)} and fix the syntax error shown")


# (import name, pip requirement, required?) — cv2/pytesseract are WARN-only (vision.py guards cv2; OCR is optional).
_DEPS = [
    ("google.genai", "google-genai>=2.0.0", True), ("PySide6", "PySide6>=6.6.0", True),
    ("sounddevice", "sounddevice>=0.5.0", True), ("numpy", "numpy>=1.26.0", True),
    ("PIL", "pillow>=10.0.0", True), ("websockets", "websockets>=13.0", True),
    ("dotenv", "python-dotenv>=1.0.0", True), ("psutil", "psutil>=5.9.0", True),
    ("pyperclip", "pyperclip>=1.8.2", True),
    ("cv2", "opencv-python-headless>=4.9.0", False),
    ("pytesseract", "pytesseract (+ the Tesseract binary)", False),
]


def check_deps():
    for import_name, pip_name, required in _DEPS:
        try:
            found = importlib.util.find_spec(import_name) is not None
        except Exception:  # noqa: BLE001 — find_spec raises if a parent package is missing
            found = False
        if found:
            report("PASS", f"Dependency: {import_name}")
        elif required:
            report("FAIL", f"Dependency: {import_name}", "not installed",
                   f"pip install '{pip_name}'  (or: pip install -r requirements.txt)")
        else:
            report("WARN", f"Dependency: {import_name}", "optional, not installed",
                   f"only needed for an optional feature; install with: pip install '{pip_name}'")


_THIRD_PARTY_TOP = {
    "google", "PySide6", "sounddevice", "numpy", "PIL", "cv2", "psutil", "pyperclip",
    "websockets", "dotenv", "speech_recognition", "pytesseract",
}
_MODULES = [
    "core.config", "core.memory.memory", "core.plugin_loader", "core.scheduler", "core.memory.session_summary",
    "core.desktop.control", "core.voice.audio", "core.desktop.vision", "core.voice.wake_word",
    "core.desktop.hotkeys", "core.desktop.clipboard_monitor", "core.voice.live_client", "core.brain.planner",
    "core.google_services", "core.memory.learning",
    "core.brain.routine_scheduler", "core.presence", "core.watchers", "gui.style", "gui.icons",
    "gui.sounds", "gui.visualizer", "gui.onboarding", "gui.main_window",
]


def _register_package():
    """Make `voice_assistant.*` importable whatever this folder is called,
    by registering the folder itself under its real package name."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg


def _classify_import_error(exc):
    """'WARN' when the failure is environmental (missing third-party dep —
    already reported by check_deps — or no display/Qt platform), else 'FAIL'
    (the module's own code is broken)."""
    if isinstance(exc, ModuleNotFoundError) and (exc.name or "").split(".")[0] in _THIRD_PARTY_TOP:
        return "WARN"
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(k in text for k in ("platform plugin", "no display", "display", "libgl", "egl", "xcb", "qt.qpa")):
        return "WARN"
    return "FAIL"


def check_module_imports():
    _register_package()
    ok = 0
    for mod in _MODULES:
        try:
            importlib.import_module(f"voice_assistant.{mod}")
            ok += 1
        except Exception as e:  # noqa: BLE001 — the doctor must never crash
            report(_classify_import_error(e), f"Import: {mod}", f"{type(e).__name__}: {e}",
                   f"open {mod.replace('.', '/')}.py — if a dependency is named, fix that in the dependency check first")
    if ok:
        report("PASS", "Module imports", f"{ok}/{len(_MODULES)} modules imported cleanly")


_EXPECTED_CONTROL_TOOLS = {
    "type_text", "press_hotkey", "mouse_control", "run_command", "window_control",
    "file_control", "close_application", "media_control", "set_brightness", "power_control",
    "write_clipboard", "open_path", "take_screenshot", "get_active_window",
    "ocr_screen", "get_activity_log",
}


_TOOL_COUNT_FLOOR = 100


def tool_count_verdict(declared_count, registered_count, floor=_TOOL_COUNT_FLOOR):
    """Pure verdict for the tool-count check: compares the number
    of tool descriptions the registry produced against the number
    of tools actually loaded in it. Returns (status, detail)
    where status is "PASS" or "WARN" and detail names BOTH
    numbers. WARN when the tool set itself has shrunk below
    `floor` (a broken load silently dropped most tools) or when
    the counts differ (some tools were silently dropped on the
    way). PASS only when the counts match and the set clears
    the floor.

    v5.51 (X-1): the wording is plain on purpose — the numbers
    are unchanged (suites pin them), the jargon ("registry
    collapsed", "declarations") is gone."""
    if registered_count < floor:
        return ("WARN",
                f"only {registered_count} of VYUK's tools loaded "
                f"(she expects at least {floor}); the check "
                f"counted {declared_count} tool descriptions "
                f"for {registered_count} tools")
    if declared_count < registered_count:
        return ("WARN",
                f"found {declared_count} tool descriptions for "
                f"{registered_count} tools — some tools have no "
                f"description, so they can't be offered")
    if declared_count != registered_count:
        return ("WARN",
                f"found {declared_count} tool descriptions for "
                f"{registered_count} tools — the counts should "
                f"match")
    return ("PASS",
            f"all {registered_count} tools loaded "
            f"({declared_count} tool descriptions, "
            f"{registered_count} tools)")


def check_tool_registry():
    _register_package()
    try:
        registry = importlib.import_module("voice_assistant.core.funnel.tools_registry").tools  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        declarations, table = registry.get_declarations(), registry._tools
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Tool registry", f"could not load core.funnel.tools_registry ({type(e).__name__}: {e})",
               "open core/funnel/tools_registry.py — the registry is built at import time")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        return
    missing_handlers = [n for n, t in table.items() if not callable(t.get("handler"))]
    if missing_handlers:
        report("FAIL", "Tool registry", f"these tools have nothing attached to run them: {', '.join(missing_handlers)}",  # v5.51 (X-1): plain words
               "check the register() calls in core/funnel/tools_registry.py (_register_default_tools / _register_extended_tools / _register_control_tools)")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
    else:
        report("PASS", "Tool registry", f"{len(declarations)} tool declarations, every tool has a handler")
    missing_control = sorted(_EXPECTED_CONTROL_TOOLS - set(table))
    if missing_control:
        report("FAIL", "Control pack", f"missing tool(s): {', '.join(missing_control)}",
               "core/desktop/control.py functions must be registered in ToolRegistry._register_control_tools() in core/funnel/tools_registry.py")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
    _tc_status, _tc_detail = tool_count_verdict(len(declarations), len(table))
    if _tc_status == "PASS":
        report("PASS", "Tool count", _tc_detail)
    else:
        report("WARN", "Tool count", _tc_detail,
               "a shortfall usually means a registration block in core/funnel/tools_registry.py failed to run or a plugin in plugins/ failed to load — check the register() calls in core/funnel/tools_registry.py and plugins/*.py for a PLUGIN dict + run(), and watch the '[Plugins]' lines printed when core.funnel.tools_registry imports")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py


def check_pack_registrations():
    """Pack registration failures (packaging batch, WIR-12).

    Importing voice_assistant.core.funnel.tools_registry builds the full
    registry (registration runs at import time); every
    guarded _register_* failure appends to
    core.funnel.tools_handlers.PACK_REGISTRATION_FAILURES in
    addition to its loud print. An empty list is PASS; any
    entry is WARN (the app still runs, with a smaller
    catalog) naming each failed pack and module.
    """
    _register_package()
    try:
        handlers = importlib.import_module("voice_assistant.core.funnel.tools_handlers")  # Phase 3 F3: tools_handlers moved to core/funnel/
        importlib.import_module("voice_assistant.core.funnel.tools_registry")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        failures = list(getattr(handlers, "PACK_REGISTRATION_FAILURES", []))
    except Exception as e:  # noqa: BLE001 — registry check reports this
        report("WARN", "Pack registrations",
               f"could not read the registration failure list ({type(e).__name__}: {e})",
               "open core/funnel/tools_handlers.py — PACK_REGISTRATION_FAILURES is appended by every guarded _register_* except")
        return
    if not failures:
        report("PASS", "Pack registrations", "every guarded tool pack registered")
        return
    detail = "; ".join(
        f"{f.get('pack', '?')}:{f.get('item', f.get('module', '?'))} "
        f"({f.get('error', '?')})" for f in failures)
    report("WARN", "Pack registrations",
           f"{len(failures)} pack registration(s) failed, those tools are missing: {detail}",
           "open the named pack module — its register_all raised during core.funnel.tools_registry import; the '[Tools]' lines printed at import time carry the same error")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py


def check_brain():
    """Which brain will VYUK use (core/brain/brain.py reads config.brain_engine),
    and is that engine actually usable on this machine?"""
    _register_package()
    try:
        cfg = importlib.import_module("voice_assistant.core.config").config
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Brain", f"could not load core.config ({type(e).__name__}: {e})",
               "open core/config.py — AppConfig must define brain_engine")
        return
    engine = (getattr(cfg, "brain_engine", "local") or "local").strip().lower()
    # v5.54.1 (field fix): report the STORED brain beside the
    # live one. The field flip — "it keeps switching back to
    # local" — was undiagnosable without opening .env: a save
    # that never landed looks identical to a saved pick until
    # the next launch, when the stored value (or the "local"
    # default when nothing is stored) wins. Last BRAIN_ENGINE
    # line wins, matching core/config.load_env_file.
    stored = None
    try:
        from pathlib import Path as _Path

        for _line in (_Path(__file__).resolve().parent
                      / ".env").read_text(encoding="utf-8",
                                          errors="replace").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            if _k.strip() == "BRAIN_ENGINE":
                stored = _v.strip().strip("\"").strip("'").lower()
    except Exception:  # noqa: BLE001 — no .env, no stored value
        stored = None
    if stored is None:
        if engine == "local":
            report("PASS", "Stored brain",
                   "no BRAIN_ENGINE stored in .env — next launch "
                   "defaults to local, same as the running brain")
        else:
            report("WARN", "Stored brain",
                   "no BRAIN_ENGINE stored in .env — the running "
                   f"brain is {engine}, but the next launch "
                   "defaults to local",
                   "pick the brain again in Settings and check "
                   "this line says it stuck; if it still says "
                   "this, .env isn't saving (read-only / locked)")
    elif stored != engine:
        report("WARN", "Stored brain",
               f"stored brain is {stored} but the running brain "
               f"is {engine} — the stored value wins on the "
               "next launch",
               "pick the brain again in Settings and check "
               "this line says it stuck; if it still disagrees, "
               ".env isn't saving (read-only / locked)")
    else:
        report("PASS", "Stored brain",
               f"stored brain is {stored}, matching the running "
               "brain")
    if engine != "local":
        if getattr(cfg, "api_key", ""):
            report("PASS", "Brain", "Gemini brain selected and an API key is set")
        else:
            report("WARN", "Brain", "Gemini brain selected but no Gemini API key is set (the local brain still usable)",
                   "paste a key in Settings, or switch the brain to Local in Settings (see core/config.py BRAIN_ENGINE)")
        return
    backend = (getattr(cfg, "local_backend", "builtin") or "builtin").strip().lower()
    if backend == "ollama":
        host = getattr(cfg, "ollama_host", "http://127.0.0.1:11434")
        model = getattr(cfg, "ollama_model", "qwen2.5:7b")
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:
                names = [m.get("name", "") for m in json.loads(resp.read().decode("utf-8")).get("models", [])]
        except Exception as e:  # noqa: BLE001
            report("FAIL", "Brain", f"local brain (Ollama backend) selected but Ollama is not reachable at {host} ({type(e).__name__})",
                   f"install Ollama (https://ollama.com), start it, then run: ollama pull {model} — or switch BRAIN_ENGINE back to gemini in .env")
            return
        report("PASS", "Brain", f"local brain — Ollama reachable at {host}, {len(names)} model(s): {', '.join(names) or 'none pulled yet'}")
        return
    # Built-in backend (v1.8): in-process llama.cpp on auto-downloaded assets.
    try:
        assets = importlib.import_module("voice_assistant.core.voice.local_assets")  # Phase 3 V2: local_assets moved to core/voice/
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Brain", f"could not load core.voice.local_assets ({type(e).__name__}: {e})",
               "open core/voice/local_assets.py — the built-in backend manages its model/voice downloads through it")
        return
    # v3.2: the file that matters is the EFFECTIVE model (the user's
    # pick, a tier already on disk, or the RAM-sized tier) — resolved
    # by core.voice.local_assets.effective_model(), the same resolution the
    # download and the session use.
    try:
        eff = assets.effective_model()
        model_file = eff["file"]
        eff_why = {"your pick": "your pick",
                   "already downloaded": "already downloaded"}.get(
                       eff["source"],
                       f"auto — {eff['ram_gb']:.0f} GB RAM" if eff.get("ram_gb")
                       else "auto — RAM unknown, Balanced fallback")
    except Exception:  # noqa: BLE001 — fall back to the configured name
        eff = None
        model_file = getattr(cfg, "local_model_file", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
        eff_why = ""
    if assets.model_ready():
        # v4.0: say when the ready brain is the copy bundled with the
        # install (the resolved path is the install's, not data/'s) —
        # and name the file that will actually load, which for a
        # bundled install can be a different tier than eff's.
        bundled_note = ""
        ready_name = model_file
        try:
            if assets.model_is_bundled():
                bundled_note = " (bundled with the install)"
                ready_name = assets.model_path().name
        except Exception:  # noqa: BLE001 — the note is a nicety
            bundled_note = ""
        report("PASS", "Brain",
               f"built-in brain model ready ({ready_name}){bundled_note}")
    else:
        tier_found = assets.model_tier_for(model_file)
        if tier_found:
            _tier_name, tier = tier_found
            report("WARN", "Brain", f"local brain is the default engine (v2.7); built-in model not downloaded yet — {tier['label']} ({eff_why}) downloads automatically in the background at launch (~{tier['gb']:g} GB) while the lite session answers",
                   "just Connect — VYUK is usable from the first second and upgrades herself when the download lands")
        else:
            report("WARN", "Brain", f"custom brain model not on disk yet — place {model_file} in data/models/ (custom models are never auto-downloaded)",
                   "put the GGUF at data/models/ with exactly that filename, or set LOCAL_MODEL_FILE to a tier model (Balanced/Quality) for auto-download")
        # v3.3: quote the last recorded download failure verbatim —
        # it survives restarts (data/download_state.json) precisely so
        # this line can say WHY the pack isn't here, not just that it
        # isn't.
        try:
            last_error = assets.last_download_info().get("last_error")
        except Exception:  # noqa: BLE001
            last_error = None
        if last_error:
            report("WARN", "Brain", f"last pack download failed: {last_error}",
                   "decode the error in TROUBLESHOOTING.md §18 — and if you ask for help, send this exact line; it is the useful part")
    if assets.kokoro_ready():
        report("PASS", "Voice (Kokoro)", "Kokoro voice files ready")
    else:
        report("WARN", "Voice (Kokoro)", "Kokoro voice files not downloaded yet — they download automatically at launch",
               "just Connect with the local brain selected; VYUK falls back to Piper/SAPI voices until the download lands")
    # STT tier (v5.45, EARS A10): the ACTIVE Whisper tier as
    # resolved by core.voice.local_assets.effective_whisper_model()
    # (explicit WHISPER_MODEL / WHISPER_TIER pick wins; auto offers
    # large-v3-turbo at >=16 GB RAM, small below), and whether that
    # model's weights are actually ready on this machine.
    try:
        stt = assets.effective_whisper_model()
        stt_ready = assets.whisper_ready()
        stt_why = ("your pick" if stt.get("source") == "your pick"
                   else f"auto — {stt['ram_gb']:.0f} GB RAM"
                   if stt.get("ram_gb") else "auto — RAM unknown, small fallback")
        if stt_ready:
            report("PASS", "STT tier (Whisper)",
                   f"active tier: {stt['tier']} — Whisper {stt['model']} ({stt_why}); model ready")
        else:
            report("WARN", "STT tier (Whisper)",
                   f"active tier: {stt['tier']} — Whisper {stt['model']} ({stt_why}); model not downloaded yet",
                   "just Connect with the local brain selected — setup downloads the active tier's weights automatically")
    except Exception as e:  # noqa: BLE001 — the tier line must never break the doctor
        report("WARN", "STT tier (Whisper)", f"could not resolve the active STT tier ({type(e).__name__}: {e})",
               "open core/voice/local_assets.py — effective_whisper_model() resolves the tier")
    # Local vision (v5.45, EYES E5b): the Quality-tier VLM pair
    # (Qwen2.5-VL-3B GGUF + mmproj, core.voice.local_assets' VLM
    # section). Healthy states: ready (PASS); not provisioned on
    # a non-Quality tier (PASS — the tier gate means it must NOT
    # be there, and OCR covers screens); allowed but missing
    # (WARN — setup fetches it at launch). The llama-cpp mtmd
    # handler probe rides along: a build without
    # Qwen25VLChatHandler degrades describes to OCR at runtime.
    try:
        vstat = assets.vlm_status()
        handler_note = ""
        try:
            from llama_cpp.llama_chat_format import Qwen25VLChatHandler

            handler_ok = Qwen25VLChatHandler is not None
        except Exception:  # noqa: BLE001 — no llama_cpp here is a note
            handler_ok = False
        if not handler_ok:
            handler_note = ("; this llama-cpp build has no Qwen2.5-VL "
                            "handler, so frame describes use OCR")
        if vstat["ready"]:
            report("PASS", "Local vision (VLM)",
                   f"Qwen2.5-VL-3B + mmproj ready — the local brain can describe frames{handler_note}")
        elif not vstat["allowed"]:
            report("PASS", "Local vision (VLM)",
                   f"not provisioned — a Quality-tier extra (this PC resolves to the {(vstat['tier'] or 'custom')} tier); screens are read with OCR")
        else:
            report("WARN", "Local vision (VLM)",
                   "Quality tier (or explicit pick) allows the Qwen2.5-VL-3B pair, but it isn't downloaded yet",
                   "just Connect with the local brain selected — setup downloads the vision pair automatically; screens use OCR until it lands")
    except Exception as e:  # noqa: BLE001 — the VLM line must never break the doctor
        report("WARN", "Local vision (VLM)", f"could not resolve the local vision state ({type(e).__name__}: {e})",
               "open core/voice/local_assets.py — vlm_status() resolves the vision pair")
    for import_name, pip_name in (("llama_cpp", "llama-cpp-python"), ("faster_whisper", "faster-whisper"), ("kokoro_onnx", "kokoro-onnx")):
        try:
            found = importlib.util.find_spec(import_name) is not None
        except Exception:  # noqa: BLE001
            found = False
        if found:
            report("PASS", f"Local brain dependency: {import_name}")
        else:
            report("WARN", f"Local brain dependency: {import_name}", "not installed",
                   f"the built-in brain needs it: pip install '{pip_name}'  (or: pip install -r requirements.txt)")
    # Lite session (v2.7): the first-run face of the local engine —
    # core/brain/brain.py routes here while the brain GGUF is still
    # downloading, so the module must import cleanly on its own.
    try:
        importlib.import_module("voice_assistant.core.voice.lite_client")  # Phase 3 V1b: moved to core/voice/
        report("PASS", "Lite session", "first-run lite session available — VYUK answers basics while the offline pack downloads, then upgrades itself")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Lite session", f"could not import core.voice.lite_client ({type(e).__name__}: {e})",
               "open core/voice/lite_client.py — it must import cleanly (stdlib + local_assets/config/memory/tools only)")
    # Offline pack storage (v2.0): the pack is OPTIONAL — the app
    # works instantly on the cloud brain without a byte of it.
    try:
        pack_total = assets.storage_report().get("total_bytes", 0)
    except Exception:  # noqa: BLE001 — accounting must never break the doctor
        pack_total = 0
    if pack_total > 0 and assets.model_ready():
        report("PASS", "Offline pack", f"Offline pack on disk: {assets.format_bytes(pack_total)}")
    elif pack_total > 0:
        report("WARN", "Offline pack", f"Offline pack on disk: {assets.format_bytes(pack_total)} — the brain model isn't finished yet; downloads resume where they left off",
               "Connect once with the local (built-in) brain selected and let setup finish — partial .part files are resume state, don't delete them")
    else:
        report("WARN", "Offline pack", "Offline pack not downloaded yet — VYUK still answers from the first second via the lite session while it downloads in the background",
               "just Connect: the pack downloads itself (ears, then voice, then brain) and the full local brain takes over automatically when it lands")


def check_memory_profile():
    """VYUK memory (v3.2): which brain tier this PC resolves to, and
    what the local brain costs in RAM. The resident figure is an
    ESTIMATE — the model file's size plus ~0.8 GB of runtime
    (llama.cpp with the capped 4096-token context, Whisper int8) —
    labelled as such; the doctor is a separate process and never
    claims to measure the running app."""
    _register_package()
    try:
        assets = importlib.import_module("voice_assistant.core.voice.local_assets")  # Phase 3 V2: local_assets moved to core/voice/
        eff = assets.effective_model()
    except Exception as e:  # noqa: BLE001
        report("WARN", "VYUK memory", f"could not resolve the effective brain tier ({type(e).__name__}: {e})",
               "open core/voice/local_assets.py — effective_model() resolves the tier")
        return
    ram = eff.get("ram_gb")
    why = {"your pick": "your pick",
           "already downloaded": "already downloaded"}.get(
               eff.get("source") or "",
               f"auto — {ram:.0f} GB RAM" if ram else "auto — RAM unknown, Balanced fallback")
    tier_name = (eff.get("tier") or "custom").capitalize()
    try:
        on_disk = assets.model_path().stat().st_size
    except OSError:
        on_disk = 0
    if on_disk:
        weights_gb = on_disk / 1_000_000_000
        disk_txt = f"model on disk {assets.format_bytes(on_disk)}"
    else:
        tier = assets.MODEL_TIERS.get(eff.get("tier") or "") or {}
        weights_gb = float(tier.get("gb") or 0)
        disk_txt = "model not downloaded yet"
    est = weights_gb + 0.8
    served_txt = ""
    try:
        served_by = assets.last_download_info().get("last_served_by")
    except Exception:  # noqa: BLE001
        served_by = None
    if served_by:
        served_txt = f"; pack downloads last served by {served_by}"
    report("PASS" if ram else "WARN", "VYUK memory",
           f"tier: {tier_name} ({why}); {disk_txt}; estimated resident "
           f"when loaded ~{est:.1f} GB (estimate: weights + ~0.8 GB "
           f"runtime at the capped 4096-token context; the brain also "
           f"unloads after 10 idle minutes){served_txt}")


def check_config():
    _register_package()
    key, source = "", "core.config"
    try:
        key = importlib.import_module("voice_assistant.core.config").config.api_key or ""
    except Exception:  # noqa: BLE001 — fall back to reading .env / environment directly
        source = ".env / environment"
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
        key = key or os.environ.get("GEMINI_API_KEY", "")
    if key:
        report("PASS", "API key", f"GEMINI_API_KEY is set (via {source})")
    else:
        report("WARN", "API key", "GEMINI_API_KEY is not set — fine for the default local brain (v2.7); only the opt-in Gemini cloud brain needs a key",
               "paste your key in Settings (or first-run onboarding), or set GEMINI_API_KEY in a .env file next to main.py — see core/config.py")


def check_cloud_doors():
    """The cloud's TWO doors (Fix 5): REST + Live, tested by ONE
    models.get through core.voice.cloud_diag.diagnose_cloud.

    The old "Test key" answered only the REST door, so a key that
    passed it could still fail every Live connect with no
    diagnosable difference. This check names each door separately.
    It never prints the key (diagnose_cloud redacts every string
    it returns). No Live session is opened — the Live verdict
    comes from the model's supported_actions list. A missing key
    is PASS (cloud is opt-in); a network failure is WARN (the
    environment, not the install); a refused Live door is FAIL
    with the fix in the detail line."""
    _register_package()
    key = ""
    model = ""
    try:
        cfg = importlib.import_module(
            "voice_assistant.core.config").config
        key = str(getattr(cfg, "api_key", "") or "")
        model = str(getattr(cfg, "model_name", "") or "")
    except Exception:  # noqa: BLE001 — fall back like check_config
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip(
                        '"').strip("'")
        key = key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        report("PASS", "Cloud doors (REST + Live)",
               "no Gemini key saved, so neither door can be tested "
               "— fine for the default local brain")
        return
    try:
        diag = importlib.import_module(
            "voice_assistant.core.voice.cloud_diag")
        result = diag.diagnose_cloud(key, model)
    except Exception as e:  # noqa: BLE001 — a broken check is a
        # verdict, never a crash (main() wraps checks anyway)
        report("WARN", "Cloud doors (REST + Live)",
               f"the cloud diagnostic itself could not run "
               f"({type(e).__name__}: {e})",
               "re-run Doctor after reinstalling; the key test in "
               "Settings is the same probe")
        return
    rest, live = result["rest"], result["live"]
    detail = (f"REST: {rest['status']} ({rest['kind']}) — "
              f"{rest['detail']} Live: {live['status']} "
              f"({live['kind']}) — {live['detail']} "
              f"Key tier: {result['tier']} ({result['tier_note']})")
    if rest["status"] == "pass" and live["status"] == "pass":
        report("PASS", "Cloud doors (REST + Live)", detail)
    elif rest["kind"] == "network" or live["kind"] == "network":
        report("WARN", "Cloud doors (REST + Live)", detail,
               "check this PC's internet connection (on campus or "
               "office networks the system proxy is the usual "
               "suspect), then re-run Doctor")
    elif rest["kind"] == "quota" or live["kind"] == "quota":
        report("WARN", "Cloud doors (REST + Live)", detail,
               "wait for the quota window, or turn on billing for "
               "the key's project in Google AI Studio")
    elif live["status"] == "unknown":
        report("WARN", "Cloud doors (REST + Live)", detail,
               "Google did not say whether this model supports "
               "Live — try connecting; the exact reply lands in "
               "data/activity.log")
    else:
        report("FAIL", "Cloud doors (REST + Live)", detail,
               "for a model refusal: try another voice model in "
               "Settings, or a key from a project with the Gemini "
               "API enabled; for a key refusal: check the key in "
               "AI Studio and paste it again")


def check_gemini_key_tier():
    """The Gemini key's tier (v5.54, Wave 0 — free-tier
    steering): Google's own terms split sharply by payment —
    on a FREE key, conversations may be used to improve
    Google's products and human reviewers may read some of
    them; on a paid-tier key, they may not (Gemini API terms,
    read for wave 32). The tier is a billing property of the
    key's AI Studio project — nothing in the key itself says
    which it is, so this check can only steer, honestly: it
    never claims to know the tier, and it never blocks a free
    key (both work identically inside VYUK)."""
    _register_package()
    key = ""
    try:
        key = importlib.import_module(
            "voice_assistant.core.config").config.api_key or ""
    except Exception:  # noqa: BLE001 — fall back like check_config
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip(
                        '"').strip("'")
        key = key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        report("PASS", "Gemini key tier",
               "no Gemini key saved, so the free/paid tier "
               "question doesn't arise")
        return
    report("WARN", "Gemini key tier",
           "VYUK can't tell a free key from a paid one from "
           "here — that lives in your Google AI Studio "
           "project's billing. On a free key, Google's terms "
           "say conversations may be used to improve Google's "
           "products, and people at Google may review some of "
           "them; on a paid-tier key, they may not. VYUK works "
           "exactly the same on both.",
           "if that matters to you, turn on billing for the "
           "key's project in Google AI Studio (that makes it "
           "paid-tier) — nothing to change inside VYUK")


def check_data():
    data_dir = ROOT / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Data directory", f"{data_dir} is not writable ({e})",
               "fix folder permissions — core/config.py stores assistant.db here and core/funnel/tools_registry.py appends activity.log here")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        return
    log = data_dir / "activity.log"
    extra = (f"activity.log exists ({len(log.read_text(encoding='utf-8', errors='replace').splitlines())} lines)" if log.exists() else "activity.log will be created on the first tool call")
    report("PASS", "Data directory", f"data/ is writable; {extra}")


# State files kept by the v5.21–v5.31 tool packs (all JSON,
# all in the data dir, all recreated empty if unreadable — the
# packs tolerate corruption by design, so this check reports
# and never fails on them).
_STATE_FILES = ("download_state.json", "price_watch.json",
                "page_summaries.json", "screen_time.json")


def check_state_files():
    data_dir = ROOT / "data"
    import json as _json

    found, unreadable = [], []
    for name in _STATE_FILES:
        candidate = data_dir / name
        if not candidate.exists():
            continue
        try:
            _json.loads(candidate.read_text(encoding="utf-8"))
            found.append(name)
        except Exception:  # noqa: BLE001 — named, not fatal
            unreadable.append(name)
    detail = (f"state files present and readable: "
              f"{', '.join(found) if found else 'none yet'}")
    if unreadable:
        detail += (f"; unreadable (will be recreated empty on "
                   f"next use): {', '.join(unreadable)}")
    report("PASS", "Tool state files", detail)


# Receipt store (core/tool_receipts.py, data/tool_receipts.jsonl):
# append-only JSONL, one record per line. The store's own loader
# fails LOUDLY on a corrupt line and refuses all later writes.
# This check mirrors that honesty for reads — and does nothing
# else. It is READ-ONLY by design: it never writes, repairs,
# migrates, or prunes the store (note it does NOT load through
# ReceiptStore — loading prunes an over-bound store, which is a
# rewrite, so the check scans the file itself instead), and it
# never prints receipt contents (args previews carry user
# activity). Counts below are by record kind and outcome
# vocabulary only — never by tool, never by content.
_RECEIPT_FILENAME = "tool_receipts.jsonl"
# core/tool_receipts.py _MAX_LINES: the loader prunes to the most
# recent lines on load. The bound below is that documented value,
# kept as a plain constant ON PURPOSE: importing the module here
# would add a literal import_module string to doctor.py, and the
# packaging checker derives its required hiddenimports from
# doctor.py's literals (S1c precedent) — a doctor-only
# diagnostic note must not grow the shipped spec. The bound is
# a report note only, never a verdict driver, so a stale
# constant here cannot change any verdict.
_RECEIPT_PRUNE_LINES = 5000

_KNOWN_RECEIPT_TYPES = ("attempt", "outcome", "dispatched")
_KNOWN_OUTCOME_STATUSES = ("succeeded", "failed", "refused",
                            "unknown", "cancelled")


def _receipt_store_path():
    """Default store path: core/config.py DATA_DIR is
    PROJECT_ROOT/"data", and doctor.py's ROOT is the same
    folder, so the default is ROOT/"data"/tool_receipts.jsonl."""
    return ROOT / "data" / _RECEIPT_FILENAME


def _receipt_prune_lines():
    # Plain constant (see _RECEIPT_PRUNE_LINES above): never an
    # import — a literal import_module string here would grow
    # the packaging checker's derived hiddenimport set.
    return _RECEIPT_PRUNE_LINES


def scan_receipt_store(path):
    """Read-only scan of one receipt-store file. Returns a dict
    of counts and verdict inputs; never writes, never prints
    record contents.

    Validity mirrors core/tool_receipts.ReceiptStore's loader:
    a line that is not JSON is corrupt; a JSON value that is
    not an object, or an object whose type is not
    attempt/outcome/dispatched, is malformed; anything else
    loads. Two deliberate differences from the loader, both
    honest: the scan reads the WHOLE file (the loader stops
    at the first bad line; the doctor reports all of the
    damage), and it never prunes (an over-bound store is
    reported, not rewritten). The unknown-attempt join mirrors
    the loader's guard rule: an attempt guards while its
    status is attempted/unknown (or cancelled) AND it was
    marked dispatched; an attempt never marked dispatched
    never ran, so it does not guard; an old record with no
    dispatched field reads as dispatched (the safe side,
    exactly like the loader)."""
    import json as _json

    result = {
        "exists": False, "readable": False, "error": "",
        "size_bytes": 0, "lines": 0, "blank_lines": 0,
        "attempts": 0, "outcomes": 0, "dispatched_marks": 0,
        "corrupt": [], "corrupt_count": 0,
        "malformed": {"non-object": 0, "unknown-type": 0},
        "outcome_status": {}, "unknown_attempts": 0,
        "oldest": None, "newest": None,
    }
    p = Path(path)
    if not p.exists():
        return result
    result["exists"] = True
    try:
        result["size_bytes"] = p.stat().st_size
        raw = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — unreadable is a verdict, not a crash
        result["error"] = type(exc).__name__
        return result
    result["readable"] = True
    attempts = {}  # receipt_id -> {"status": str, "dispatched": bool}
    for lineno, line in enumerate(raw.splitlines(), 1):
        result["lines"] += 1
        if not line.strip():
            result["blank_lines"] += 1
            continue
        try:
            rec = _json.loads(line)
        except Exception as exc:  # noqa: BLE001 — a bad line is data, not a crash
            result["corrupt_count"] += 1
            if len(result["corrupt"]) < 5:
                result["corrupt"].append((lineno, type(exc).__name__))
            continue
        if not isinstance(rec, dict):
            result["malformed"]["non-object"] += 1
            continue
        rtype = rec.get("type")
        if rtype not in _KNOWN_RECEIPT_TYPES:
            result["malformed"]["unknown-type"] += 1
            continue
        # Timestamps are metadata, never content.
        ts = rec.get("created_at")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            if result["oldest"] is None or ts < result["oldest"]:
                result["oldest"] = ts
            if result["newest"] is None or ts > result["newest"]:
                result["newest"] = ts
        rid = str(rec.get("receipt_id", ""))
        if rtype == "attempt":
            result["attempts"] += 1
            attempts[rid] = {"status": "attempted",
                             "dispatched": bool(rec.get("dispatched", True))}
        elif rtype == "dispatched":
            result["dispatched_marks"] += 1
            if rid in attempts:
                attempts[rid]["dispatched"] = True
        else:  # outcome
            result["outcomes"] += 1
            status = rec.get("status")
            key = status if status in _KNOWN_OUTCOME_STATUSES else "other"
            result["outcome_status"][key] = (
                result["outcome_status"].get(key, 0) + 1)
            if rid in attempts:
                attempts[rid]["status"] = str(status)
                attempts[rid]["dispatched"] = bool(
                    rec.get("dispatched", True))
    for att in attempts.values():
        if att["dispatched"] and (att["status"] in ("attempted", "unknown")
                                  or att["status"] == "cancelled"):
            result["unknown_attempts"] += 1
    return result


def _receipt_timestamp_text(ts):
    """A created_at float as a short UTC date, or "" when the
    value cannot be rendered (absurd timestamps are metadata
    noise, never a check failure)."""
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
    except Exception:  # noqa: BLE001 — unrenderable is omitted, not fatal
        return ""


def check_receipt_store(store_path=None):
    """Tool receipt store health (regression-batch leftover):
    diagnostics only, read-only — never writes, repairs,
    migrates, or prunes, and never prints receipt contents.
    If the doctor ever gains a repair mode, this check does
    not use it.

    Severity reasoning (precedent: check_state_files never
    FAILs on recreatable stores — the funnel recreates this
    store on first dispatch, so a missing store is not
    broken). But unlike the state files, this store's VALUE
    is its history: a missing store on an install that has
    already run tools means guard history is gone. Missing
    is therefore WARN, never PASS (which would hide history
    loss) and never FAIL (which would fail every fresh
    install). Corrupt, malformed, or unreadable means the
    guard cannot use the store at all — the store itself
    refuses writes then, and the funnel fails closed for
    non-read tools — so that is FAIL."""
    path = Path(store_path) if store_path is not None \
        else _receipt_store_path()
    try:
        shown = str(path.relative_to(ROOT))
    except ValueError:
        shown = str(path)
    scan = scan_receipt_store(path)
    if not scan["exists"]:
        report("WARN", "Tool receipt store",
               f"no store at {shown} yet — the funnel creates it "
               f"on the first tool call, so this is expected on "
               f"a fresh install; if tools have already run "
               f"here, their guard history is missing",
               f"run a tool once and re-run the doctor; if the "
               f"store is still missing after tool calls, "
               f"report it as a bug")
        return
    if not scan["readable"]:
        report("FAIL", "Tool receipt store",
               f"store at {shown} could not be read "
               f"({scan['error']}) — the guard cannot use it",
               f"fix the file permissions on {shown}, or back "
               f"it up and let VYUK recreate it on the next "
               f"tool call (recreating loses the "
               f"unknown-outcome guard history — check no "
               f"interrupted tool call is still outstanding "
               f"first)")
        return
    corrupt = scan["corrupt_count"]
    malformed = sum(scan["malformed"].values())
    if corrupt or malformed:
        parts = []
        if corrupt:
            first = scan["corrupt"][0]
            parts.append(f"{corrupt} corrupt line(s) "
                         f"(first: line {first[0]}: {first[1]})")
        if malformed:
            kinds = ", ".join(
                f"{k} x{v}"
                for k, v in sorted(scan["malformed"].items()) if v)
            parts.append(f"{malformed} malformed record(s) ({kinds})")
        report("FAIL", "Tool receipt store",
               f"store at {shown} is unusable: "
               f"{'; '.join(parts)} — the guard cannot trust "
               f"it, and the store itself refuses writes "
               f"while corrupt",
               f"back up {shown}, then delete or repair it by "
               f"hand — VYUK recreates it on the next tool "
               f"call (recreating loses the unknown-outcome "
               f"guard history — check no interrupted tool "
               f"call is still outstanding first)")
        return
    valid = scan["attempts"] + scan["outcomes"] + scan["dispatched_marks"]
    if valid == 0:
        blank_note = (f"; {scan['blank_lines']} blank line(s) ignored"
                      if scan["blank_lines"] else "")
        report("PASS", "Tool receipt store",
               f"store at {shown} is empty (no receipts yet{blank_note})")
        return
    detail = (f"store at {shown}: {scan['lines']} line(s), "
              f"{valid} record(s) ({scan['attempts']} attempts, "
              f"{scan['outcomes']} outcomes, "
              f"{scan['dispatched_marks']} dispatch marks)")
    if scan["unknown_attempts"]:
        detail += (f"; {scan['unknown_attempts']} attempt(s) still "
                   f"without a resolved outcome (the guard "
                   f"still holds them)")
    oldest = _receipt_timestamp_text(scan["oldest"]) \
        if scan["oldest"] is not None else ""
    newest = _receipt_timestamp_text(scan["newest"]) \
        if scan["newest"] is not None else ""
    if oldest and newest:
        detail += f"; receipts span {oldest} to {newest}"
    prune = _receipt_prune_lines()
    if scan["lines"] > prune:
        detail += (f"; over the {prune}-line prune bound — the "
                   f"next load keeps only the most recent "
                   f"{prune} lines")
    report("PASS", "Tool receipt store", detail)


def check_write_proof():
    """The write proof (click-anywhere Batch 0, Plan 9 §7.2).

    The Doctor-page "Write proof" entry: RUNS the proof (this is
    a doing check, not a read-only health check — it writes the
    named 213-byte fixture to <app data>/write_proof.txt through
    VYUK's write_file, reads it back, and byte-compares). PASS
    means the bytes on disk are identical; FAIL means Batch 0
    re-opens and Batch 1 does not spawn. The file REMAINS
    afterwards (named in the receipt) so the parent can inspect
    it. The field gate — byte-identical on his Windows machine,
    first attempt AND after an app restart — is a separate bar
    after delivery; this check is the lab leg only."""
    try:
        _register_package()
        from core import write_proof as _wp
    except Exception as exc:  # noqa: BLE001 — unimportable: FAIL
        report("FAIL", "Write proof",
               "could not import core.write_proof "
               f"({type(exc).__name__}: {exc})",
               "repair the import, then re-run the doctor")
        return
    try:
        verdict = _wp.run_write_proof()
    except Exception as exc:  # noqa: BLE001 — a crashing proof is a fail
        report("FAIL", "Write proof",
               f"the proof raised instead of returning a verdict "
               f"({type(exc).__name__}: {exc})",
               "fix core/write_proof.py, then re-run the doctor")
        return
    if verdict.get("passed"):
        report("PASS", "Write proof", verdict.get("detail", ""))
    else:
        report("FAIL", "Write proof", verdict.get("detail", ""),
               "Batch 0 re-opens: re-work the write path "
               "(core/funnel/tools_handlers_data_services.py), "
               "then re-run the doctor — Batch 1 does not spawn "
               "until this passes")


def check_presence():
    try:
        _register_package()
        presence = importlib.import_module("voice_assistant.core.presence")
        state = presence.autostart_enabled()
    except Exception as e:  # noqa: BLE001
        report("WARN", "Presence", f"auto-start state could not be read ({e})",
               "core/presence.py reads the Windows Run key (HKCU\\...\\Run, value 'VYUK') — if this persists, report it as a bug")
        return
    label = {True: "enabled", False: "disabled", None: "not Windows"}[state]
    report("PASS", "Presence", f"auto-start {label}")


def check_watchers():
    """The Watchers engine (core/watchers.py, v2.3): can it import, is
    psutil there for the battery/disk/drive probes, and does this
    machine even have a battery to watch?"""
    _register_package()
    try:
        watchers = importlib.import_module("voice_assistant.core.watchers")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Watchers", f"could not import core.watchers ({type(e).__name__}: {e})",
               "open core/watchers.py — the module must import cleanly (stdlib + lazy psutil only)")
        return
    try:
        health = watchers.watcher_health()
    except Exception as e:  # noqa: BLE001 — watcher_health is never-raise by contract; treat a raise as broken
        report("FAIL", "Watchers", f"watcher_health() raised ({type(e).__name__}: {e})",
               "open core/watchers.py — watcher_health() must never raise")
        return
    if not health.get("psutil"):
        report("WARN", "Watchers", "engine imports, but psutil is not installed — battery, disk and drive watchers stay off (downloads, network and idle still work)",
               "pip install 'psutil>=5.9.0'  (or: pip install -r requirements.txt)")
    elif health.get("battery_present"):
        # v5.57.1 (item 4): the battery line used to unpack
        # health["battery"] blindly — a snapshot that reports a
        # battery without a well-shaped reading crashed the whole
        # check (a bare "Doctor check" verdict on the user's box).
        # A malformed reading is a WARN about an optional watcher,
        # never a check crash.
        battery = health.get("battery")
        if (isinstance(battery, (tuple, list)) and len(battery) >= 2
                and isinstance(battery[0], (int, float))):
            percent, charging = battery[0], bool(battery[1])
            report("PASS", "Watchers", f"psutil available; battery detected at {percent}% ({'charging' if charging else 'not charging'})")
        else:
            report("WARN", "Watchers", "psutil reports a battery, but its reading came back in an unexpected shape — the battery watcher stays off",
                   "open core/watchers.py — watcher_health() must report battery as (percent, charging)")
    else:
        report("WARN", "Watchers", "psutil available but no battery detected (a desktop?) — the battery watcher stays off; downloads, network, disk and drive watchers are unaffected",
               "nothing to fix on a desktop — battery notices simply never fire without a battery")
    load = health.get("load")
    # v5.57.1 (item 4): same shape guard as the battery line —
    # formatting a malformed load value crashed the check.
    if (isinstance(load, (tuple, list)) and len(load) >= 2
            and isinstance(load[0], (int, float))
            and isinstance(load[1], (int, float))):
        report("PASS", "Watchers", f"load probe live: processor {load[0]:.0f}%, memory {load[1]:.0f}% full — the heavy-load watcher can run")
    elif health.get("psutil"):
        report("WARN", "Watchers", "the load probe returned nothing usable — the heavy-load watcher stays off",
               "psutil is present but cpu/memory reads failed; reinstall psutil")
    # Ambient light (v5.45, N12): an opt-in watcher — the doctor
    # reports the toggle posture honestly. It is OFF by default;
    # the doctor (its own process, no live Settings) can only
    # state the default and what switching it on means.
    report("PASS", "Ambient light (opt-in)",
           "off by default — when switched on in Settings → Notices, the camera opens briefly once an hour, only a brightness number is logged, and the frame is never stored or sent anywhere")
    # Live-engine health (v5.42): only when an engine is actually
    # running in this process — a not-yet-started engine is not a
    # failure and must not WARN. FAIL semantics above are unchanged.
    try:
        if health.get("engine_running"):
            probes_state = health.get("probes") or {}
            # Platform-expected probes that must have succeeded at
            # least once: on Windows the ctypes/ConsentStore-backed
            # probes (privacy, idle, clipboard, fullscreen). The
            # engine's probe key for idle is "idle_seconds".
            if sys.platform.startswith("win"):
                _expected = ("privacy", "idle_seconds",
                             "clipboard", "fullscreen")
                # Accept the "idle" alias if a health surface uses it.
                for _name in _expected:
                    _state = probes_state.get(_name)
                    if _state is None and _name == "idle_seconds":
                        _state = probes_state.get("idle")
                    _ever = bool(_state.get("ever_succeeded")) if isinstance(_state, dict) else False
                    if not _ever:
                        report("WARN", "Watchers", f"engine is running but the '{_name}' probe has never succeeded — that watcher has been silent since start",
                               "open core/watchers.py — check the probe for this watcher (Windows API / ConsentStore access may be blocked)")
            # Heartbeat staleness: last tick older than ~3x poll.
            _age = health.get("last_tick_age_s")
            try:
                _poll = float(health.get("poll_seconds") or getattr(watchers, "POLL_SECONDS", 30) or 30)
            except Exception:  # noqa: BLE001
                _poll = 30.0
            if _age is not None and _poll > 0 and float(_age) > 3 * _poll:
                report("WARN", "Watchers", f"engine heartbeat is stale — last tick was {float(_age):.0f}s ago (poll interval {_poll:.0f}s)",
                       "the watcher thread may be stuck — restart VYUK; if it persists, report the Watchers lines from the log")
    except Exception:  # noqa: BLE001 — live-engine extras must never break the doctor
        pass


def _tool_payload_verdict(count: int, budget: int,
                          assistant_size: int, desktop_size: int,
                          prompt_size: int,
                          ) -> tuple[str, str, str, str]:
    """Pure verdict for the Tool payload check (INV-1
    re-scope, unit-tested; DECL-01 retirement 2026-11-01:
    the fitted full list no longer exists, so the verdict
    measures offered sets only): FAIL only when an offered
    set busts the budget."""
    if assistant_size + prompt_size > budget:
        return ("FAIL", "Tool payload",
                f"{count} tools; assistant offered set is "
                f"{assistant_size + prompt_size} bytes — over "
                f"the {budget}-byte budget",
                "shrink the assistant resident set before "
                "using the cloud brain")
    if desktop_size + prompt_size > budget:
        return ("FAIL", "Tool payload",
                f"{count} tools; desktop offered set is "
                f"{desktop_size + prompt_size} bytes — over "
                f"the {budget}-byte budget",
                "shrink DESKTOP_TOOL_PRIORITY before using "
                "the cloud brain")
    return ("PASS", "Tool payload",
            f"{count} tools; offered sets fit (assistant "
            f"{assistant_size + prompt_size} bytes, desktop "
            f"{desktop_size + prompt_size} bytes of the "
            f"{budget}-byte budget)",
            "")


def check_tool_payload():
    """The Gemini Live tool payload (v5.32, INV-1
    re-scoped): past ~256 KB of tool declarations the Live
    API accepts the connection and then kills every turn
    (close 1007 — measured live, RC-7). The gate measures
    the sets a session actually offers — assistant and
    desktop, each plus the router prompt — so a routed
    session can never hit 1007 for payload size.
    (DECL-01 retirement 2026-11-01: the fitted full list
    is no longer measured — it no longer exists.)"""
    _register_package()
    try:
        tools_mod = importlib.import_module("voice_assistant.core.funnel.tools_registry")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        profiles_mod = importlib.import_module("voice_assistant.core.funnel.tool_profiles")  # Phase 3 F2a: tool_profiles moved to core/funnel/
        toolbox_mod = importlib.import_module("voice_assistant.core.funnel.toolbox")  # Phase 3 F2a: toolbox moved to core/funnel/
        import json as _json

        count = len(tools_mod.tools.get_declarations())
        budget = int(tools_mod.tools._GEMINI_DECL_BUDGET)
        assistant_size = len(_json.dumps(
            profiles_mod.profile_declarations(
                tools_mod.tools, "assistant")))
        desktop_size = len(_json.dumps(
            profiles_mod.desktop_declarations(tools_mod.tools)))
        prompt_size = len(toolbox_mod.ROUTER_PROMPT.encode())
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Tool payload",
               f"could not measure the offered tool sets "
               f"({type(e).__name__}: {e})",
               "the registry itself is broken — see the "
               "module-import and registry checks above")
        return
    status, label, detail, fix = _tool_payload_verdict(
        count, budget, assistant_size, desktop_size, prompt_size)
    report(status, label, detail, fix)


def check_tool_router():
    """The tool router (v5.33): the Live session offers the
    core set + use_toolbox instead of the whole registry.
    This check measures the routed payload and proves the
    dispatcher reaches a non-core tool's schema."""
    _register_package()
    try:
        tools_mod = importlib.import_module("voice_assistant.core.funnel.tools_registry")  # Phase 4 S1c INTENTIONAL: shim deleted; home is core/funnel/tools_registry.py
        toolbox_mod = importlib.import_module("voice_assistant.core.funnel.toolbox")  # Phase 3 F2a: toolbox moved to core/funnel/
        import json as _json

        decls = toolbox_mod.router_declarations(tools_mod.tools)
        size = len(_json.dumps(decls))
        probe = toolbox_mod.use_toolbox(
            action="describe", tool="habit_track")
        ok = (len(decls) >= 50 and size <= 60_000
              and "habit_track" in probe)
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Tool router", f"could not build the routed tool set ({type(e).__name__}: {e})",
               "the session falls back to the desktop set — see the Tool payload check; the router module is broken")
        return
    if ok:
        # Audit fix Batch 2 (DECL-02): router_declarations returns
        # the 60 resident core tools PLUS use_toolbox, so the old
        # "{n} resident tools" label overstated the resident
        # count by exactly the dispatcher. Label what the number
        # actually counts: offered declarations, split honestly.
        _resident = len([d for d in decls
                         if d.get("name") != "use_toolbox"])
        report("PASS", "Tool router",
               f"{len(decls)} offered declarations "
               f"({_resident} resident tools + use_toolbox), "
               f"{size} bytes; the full registry stays reachable "
               f"through use_toolbox")
    else:
        report("FAIL", "Tool router",
               f"routed set measured {len(decls)} tools / {size} bytes and the dispatcher probe failed",
               "the session falls back to the desktop set until the router is repaired")


def check_piper_voice():
    """The Piper fallback voice (v5.35): provisioned like every
    other asset — before this fix it was a file only a manual
    drop could provide, so the middle rung of the voice chain
    silently never existed on a fresh install."""
    _register_package()
    try:
        la = importlib.import_module("voice_assistant.core.voice.local_assets")  # Phase 3 V2: local_assets moved to core/voice/
        voice, _cfg = la.piper_paths()
        ready = la.piper_ready()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Fallback voice (Piper)", f"could not resolve the Piper voice ({type(e).__name__}: {e})",
               "the local_assets module is broken — Piper is skipped in the voice chain")
        return
    if ready:
        report("PASS", "Fallback voice (Piper)", f"amy-medium voice resolved at {voice}")
    else:
        report("WARN", "Fallback voice (Piper)", "the Piper fallback voice isn't provisioned yet",
               "setup downloads it automatically with the voice pack; Kokoro and the system voice carry speech meanwhile")


def check_ocr_engine():
    """The OCR engine (v5.35): a bundled/provisioned Tesseract
    resolves through local_assets — OCR used to see PATH only,
    so a fresh install's text reading was dead."""
    _register_package()
    try:
        la = importlib.import_module("voice_assistant.core.voice.local_assets")  # Phase 3 V2: local_assets moved to core/voice/
        path = la.tesseract_path()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "OCR engine", f"could not resolve the OCR engine ({type(e).__name__}: {e})",
               "the local_assets module is broken — OCR is unavailable")
        return
    if path is not None:
        report("PASS", "OCR engine", f"Tesseract resolved at {path}")
    else:
        report("WARN", "OCR engine", "no Tesseract engine found (bundled, provisioned, or PATH)",
               "text reading is off until the engine lands — it comes with the installer; nothing to download manually")


def check_capability_probes():
    """Capability probes (v5.54, Wave 0 — EY-H1 REPORT-ONLY
    leg): the check above proves the OCR engine EXISTS; this
    one proves the bundled capabilities actually WORK — the
    Tesseract specimen was an engine that resolved, staged
    data and all, and read nothing. OCR must OCR a known-text
    image back correctly; the local vision model, when
    provisioned, must answer one fixed-image inference (that
    leg loads the model and can take a while — it only runs
    here, where the user asked for a diagnosis). Reporting
    only: a failed probe is named, never repaired from here
    (the repair leg is the installer's, Wave 10)."""
    _register_package()
    try:
        probes = importlib.import_module(
            "voice_assistant.core.health.capability_probes")
        results = probes.run_probes(include_vlm=True)
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Capability probes",
               f"the probe module could not run ({type(e).__name__}: {e})",
               "open core/health/capability_probes.py — the probes live there")
        return
    level = {"ok": "PASS", "fail": "FAIL", "absent": "WARN"}
    for r in results:
        fix = ("the capability is staged but does not work — "
               "reinstalling VYUK re-stages it; if it still "
               "fails, send this line with your logs"
               if r["status"] == "fail" else "")
        report(level.get(r["status"], "WARN"),
               f"Capability probe — {r['capability']}",
               r["detail"], fix)


_CDP_PORT = 9222  # core/browser._DEFAULT_CDP_PORT, kept literal so the
# doctor never has to import the browser stack just to probe it.


def _cdp_port_reachable(port: int = _CDP_PORT) -> bool:
    """True when something answers on localhost:<port>. A probe only,
    never a launch: the doctor must not start Chrome."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except Exception:  # noqa: BLE001 — unreachable is a normal outcome
        return False


def _find_chrome_binary():
    """A Chrome/Chromium executable path, or None. PATH first, then the
    per-platform install locations. Never launches anything."""
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", ""),
                     os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                candidates.append(
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        candidates.extend([Path("/usr/bin/google-chrome"),
                           Path("/usr/bin/chromium"),
                           Path("/usr/bin/chromium-browser"),
                           Path("/snap/bin/chromium")])
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def check_real_chrome():
    """Your own Chrome over CDP (v5.70+): the bundled-Chromium download
    was deleted in v5.70 — nothing here downloads a browser any more —
    and browser tools attach to the user's real Chrome over its debug
    port (localhost:9222, explicit opt-in). Audit fix batch 1 replaces
    the old check_bundled_chromium, which called the deleted
    browser._chromium_ready() and so reported FAIL on every install.
    This check probes reachability only and never launches Chrome:
    PASS when the debug port answers, WARN (never FAIL) when Chrome
    is installed but its debug port is closed or when no Chrome is
    found at all — absence is degraded, not broken, and is never
    reported as "browser tools are unavailable"."""
    if _cdp_port_reachable():
        report("PASS", "Real Chrome (CDP)",
               "your Chrome answered on its debug port (localhost:9222) — browser tools can attach")
        return
    found = _find_chrome_binary()
    if found:
        report("WARN", "Real Chrome (CDP)",
               "Chrome found but its debug port is not open",
               "start Chrome with --remote-debugging-port=9222 (and a non-standard user-data-dir) to let browser tools attach")
    else:
        report("WARN", "Real Chrome (CDP)",
               "no Chrome found on this machine",
               "browser tools attach to your own Chrome; nothing is downloaded — install Chrome and start it with --remote-debugging-port=9222 to attach")


def check_mcp_servers():
    """MCP toolbox servers (v5.55, Wave 1 HE-1/HE-5): one line
    per configured server. Deliberately NOT a connect test:
    the doctor runs as its own process, so it can never
    observe a live session's cached state — a cached-state
    line would read "not connected yet" forever, which is
    noise, not diagnosis. What the doctor CAN verify without
    side effects is the config itself (valid entry, issuer
    present for M2M auth, no literal secrets) plus, for
    stdio servers, that the command resolves on PATH (the
    most common real failure: a missing server binary). A
    server that passes both is PASS with "connects on first
    use" — lazy connect is normal behaviour, not a warning.
    A broken entry or a missing binary is WARN (the toolbox
    is an extension: native tools are unaffected)."""
    _register_package()
    try:
        import shutil as _shutil

        mcp_mod = importlib.import_module("voice_assistant.core.mcp_client")
        servers, errors = mcp_mod.load_config()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "MCP toolbox servers", f"could not read the MCP server config ({type(e).__name__}: {e})",
               "the mcp_client module is broken — MCP tools are unavailable")
        return
    if errors.get("__file__"):
        report("WARN", "MCP toolbox servers", errors["__file__"],
               "fix mcp_servers.json; the built-in defaults still apply")
    if not servers and not errors:
        report("WARN", "MCP toolbox servers", "no MCP servers are configured",
               "add servers in mcp_servers.json (Fetch and Time are built-in defaults)")
        return
    for name, cfg in servers.items():
        if name in errors:
            report("WARN", f"MCP server — {name}", errors[name],
                   "fix the entry in mcp_servers.json")
            continue
        if not cfg.get("enabled", True):
            report("PASS", f"MCP server — {name}",
                   "disabled in mcp_servers.json — it stays off until you enable it")
            continue
        if cfg.get("transport") == "stdio":
            cmd = cfg.get("command", "")
            if _shutil.which(cmd):
                report("PASS", f"MCP server — {name}",
                       f"configured; '{cmd}' found — it connects the first time a tool needs it")
            else:
                report("WARN", f"MCP server — {name}",
                       f"configured, but '{cmd}' was not found on PATH, so it cannot start",
                       "install the server package, or fix the command in mcp_servers.json")
        else:
            auth = cfg.get("auth")
            warning = cfg.get("cleartext_loopback_warning")
            if warning:
                # Gate C (P1-8): the loopback-cleartext warning
                # recorded at validation, surfaced where the
                # user actually reads server state.
                report("WARN", f"MCP server — {name}", warning,
                       "switch the server URL to https://")
            else:
                note = ("with machine-to-machine auth configured"
                        if auth else "no auth configured")
                report("PASS", f"MCP server — {name}",
                       f"configured (remote); {note} — it connects the first time a tool needs it")


def check_home_assistant():
    """Home Assistant client (R7-9 homeassistantpy pattern, dormant
    by design): the doctor reports the honest configuration state
    and never connects. Unconfigured is the healthy state for a
    dormant optional integration — PASS, not WARN — because the
    owner runs no Home Assistant instance (D10: "nothing connected
    yet"). A configured instance would be validated here (LAN host
    + token present, no literal secret) without connecting; there
    is no persistent configuration surface yet, so the check
    reports unconfigured until one exists."""
    _register_package()
    try:
        ha_mod = importlib.import_module("voice_assistant.core.homeassistant")
        status = ha_mod.home_assistant_status()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Home Assistant", f"could not read the Home Assistant client status ({type(e).__name__}: {e})",
               "the homeassistant module is broken — the dormant client is unavailable")
        return
    if not status["configured"]:
        report("PASS", "Home Assistant",
               "not configured — no instance; the client is dormant (D10: nothing connected yet)",
               "to enable: run a Home Assistant instance on your LAN and configure its URL + long-lived access token")
        return
    report("PASS", "Home Assistant",
           f"configured for {status['host']} — dormant until used (no background connection)")


def _probe_input_peak(sd, index, seconds=1.2):
    """Open a throwaway input stream on `index` for ~`seconds`
    and return (peak, note) — peak the peak RMS heard
    (0.0–1.0, float scale), note the stream's classified
    status (core/voice/mic_doctor.classify_stream_flags, e.g.
    input overflow) when PortAudio reported one, else None.
    The probe stream is always closed; exceptions propagate
    to the caller, which reports them. Mic remake (v6.3.x):
    the probe used to swallow PortAudio status flags
    entirely, so an overflow / underflow that proved the
    stream itself was faulty never reached the diagnosis."""
    import numpy as np

    try:
        from voice_assistant.core.voice import mic_doctor as _md
        _classify = _md.classify_stream_flags
    except Exception:  # noqa: BLE001 — classification is
        _classify = None  # diagnostic only; the peak stands

    peak = 0.0
    notes = []

    def _cb(indata, frames, time_info, status):
        nonlocal peak
        try:
            arr = np.asarray(indata, dtype=np.float32) / 32768.0
            if arr.size:
                rms = float(np.sqrt(np.mean(arr ** 2)))
                if rms > peak:
                    peak = rms
        except Exception:  # noqa: BLE001 — a probe frame must never crash the doctor
            pass
        if status and _classify is not None:
            try:
                flagged = _classify({
                    "input_overflow": bool(
                        getattr(status, "input_overflow", False)),
                    "input_underflow": bool(
                        getattr(status, "input_underflow", False)),
                })
                if flagged and flagged not in notes:
                    notes.append(flagged)
            except Exception:  # noqa: BLE001
                pass

    stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                            device=index, callback=_cb)
    try:
        stream.start()
        time.sleep(seconds)
    finally:
        try:
            stream.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
    return peak, ("; ".join(notes) or None)


def check_microphone():
    """The microphone (v5.37): the app used to trust a raw saved
    device index and never noticed a stream that opens but
    delivers digital silence — the field report behind this
    check was a session that simply never heard its user. The
    app now resolves the pick by name (core/voice/audio.py) and
    watches the stream's levels; this check proves the device
    side: something input-capable exists, the app's resolved
    device opens, and sound actually arrives on it."""
    _register_package()
    try:
        audio_mod = importlib.import_module("voice_assistant.core.voice.audio")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Microphone", f"could not load core.voice.audio ({type(e).__name__}: {e})",
               "open core/voice/audio.py — the microphone path lives there")
        return
    try:
        sd = importlib.import_module("sounddevice")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Microphone", f"sounddevice is not importable ({type(e).__name__}: {e})",
               "see the sounddevice line in the dependency check above — the microphone cannot be probed without it")
        return
    try:
        devices = [dict(d) for d in sd.query_devices()]
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Microphone", f"could not list audio devices ({type(e).__name__}: {e})",
               "the OS audio stack is not answering — check the audio service/drivers, then re-run the doctor")
        return
    if not any(int(d.get("max_input_channels", 0) or 0) > 0 for d in devices):
        report("FAIL", "Microphone", "no input-capable audio device on this machine",
               "connect a microphone (or re-enable the disabled one in the OS sound settings), then re-run the doctor")
        return
    default_index = audio_mod.system_default_input_index(devices)
    # v5.52 (item 3): name the EFFECTIVE device + its source the
    # way Settings does. The stored mode lives in QSettings; a
    # manual pin resolves by the v5.37 rules, Automatic (the
    # standing behaviour) resolves the PC's own default. Any
    # failure reading the store reads as Automatic — the mode
    # the app itself defaults to.
    mode = "auto"
    pick_idx = pick_name = None
    try:
        from core.config_spec import qsettings_get

        mode = str(qsettings_get("audio/input_mode") or "auto")
        pick_idx = qsettings_get("audio/input_device")
        pick_name = qsettings_get("audio/input_device_name")
    except Exception:  # noqa: BLE001 — no store: Automatic
        mode = "auto"
    if mode == "manual" and pick_idx is not None:
        try:
            pick_idx = int(pick_idx)
        except (TypeError, ValueError):
            pick_idx = None
    if mode == "manual" and pick_idx is not None:
        index, name, _note, _src = audio_mod.effective_input(
            devices, "manual", pick_idx,
            str(pick_name) if pick_name is not None else None,
            default_index)
        source = "your pick in Settings"
    else:
        index, name, _note, _src = audio_mod.effective_input(
            devices, "auto", None, None, default_index)
        source = "this PC's default (Automatic)"
    if index is None:
        report("FAIL", "Microphone", "input devices are listed but none could be resolved for opening",
               "check the OS sound settings for a disabled or half-installed device, then re-run the doctor")
        return
    label = name or f"device {index}"
    # Mic remake (v6.3.x): the probe listens for 1.2 s — the
    # user used to be told to speak only AFTER it (for the
    # deep test), so the quick listen ran on an unprompted
    # room and a healthy mic in a quiet room reported WARN
    # "opens but delivers silence". Say so BEFORE the probe.
    print("       Say something now — listening for 1.2 s…")
    try:
        peak, probe_note = _probe_input_peak(sd, index)
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Microphone", f"'{label}' would not open for listening ({type(e).__name__}: {e})",
               "another app may be holding the microphone exclusively, or the device rejects 16 kHz input — close other audio apps, check the OS sound settings, then re-run the doctor")
        return
    _note_suffix = (f" (stream note: {probe_note})"
                    if probe_note else "")
    if peak < 1e-4:
        report("WARN", "Microphone",
               f"'{label}' ({source}) opens but delivers silence — on Windows this is usually Settings > Privacy > Microphone (let apps access your microphone), an OS-level mute on the device itself, or the wrong device{_note_suffix}",
               "allow microphone access in the OS privacy settings, unmute the device, or pick the right microphone in VYUK's Settings (the name shown there is the device she opens) — then talk near it and re-run the doctor")
    elif peak < 0.01:
        report("WARN", "Microphone",
               f"'{label}' ({source}) is very quiet (peak level {peak:.4f} in a 1.2 s listen) — she may miss soft speech{_note_suffix}",
               "raise the microphone gain in VYUK's Settings (Voice & sound → microphone gain), move closer, or check the OS input volume for the device")
    else:
        report("PASS", "Microphone",
               f"'{label}' ({source}) is open and hearing (peak level {peak:.3f} in a 1.2 s listen){_note_suffix}")
    # v5.54 (Wave 0, EA-11): when the quick listen heard silence
    # (or near-silence), run the DEEP diagnosis — ask Windows
    # itself via core/voice/mic_doctor.py: the default endpoint's
    # state, its OS mute / level, and its own meter during a
    # prompted 3-second test that never opens her stream. That
    # separates the "three silences" (app-side vs named Windows
    # cause vs hardware/driver) instead of leaving the generic
    # line above as the last word. Off Windows (or with the
    # probes unavailable) this block adds nothing.
    if peak < 0.01:
        try:
            mic_doc = importlib.import_module(
                "voice_assistant.core.voice.mic_doctor")
            print("       Deep microphone test: say something "
                  "in the next 3 seconds…")
            diag = mic_doc.run_diagnosis(
                meter_seconds=3.0,
                stream_hearing=bool(peak >= 1e-4),
                # Mic remake: wire the probe's classified
                # stream note through — run_diagnosis always
                # accepted stream_note, the doctor never passed
                # it, so the stream_fault verdict was unreachable.
                stream_note=probe_note)
            if diag.get("verdict") not in (None, "unprobed"):
                detail = diag["sentence"]
                sc = diag.get("soundcard") or {}
                if sc.get("name"):
                    detail += (f" SoundCard names the default "
                               f"microphone as '{sc['name']}'.")
                report("WARN", "Microphone (deep check)", detail)
        except Exception:  # noqa: BLE001 — a probe must never
            pass  # break the doctor; the quick verdict stands
    # v5.44 (EARS A3): surface the adaptive VAD floor. The doctor
    # runs in its own process, without a live session, so it can
    # only report the estimator's module-level LAST KNOWN snapshot
    # (core/voice/local_client._noise_floor_snapshot) — when no session
    # has listened in this process there is honestly nothing to
    # report, and no number is invented.
    try:
        local_client_mod = importlib.import_module(
            "voice_assistant.core.voice.local_client")  # Phase 3 V1b: moved to core/voice/
        snapshot = local_client_mod._noise_floor_snapshot()
    except Exception:  # noqa: BLE001 — the floor line must never crash the doctor
        snapshot = None
    if snapshot is None:
        report("WARN", "Adaptive VAD floor",
               "no session yet — no live noise-floor estimate is available to this doctor run",
               "the floor is estimated while VYUK listens; run the doctor from a process that has had a session, or just start VYUK in the room — a standalone doctor run honestly has no floor to show")
    else:
        report("PASS", "Adaptive VAD floor",
               f"last known noise floor {snapshot.get('noise_floor_rms')} RMS, "
               f"effective speech threshold {snapshot.get('effective_threshold_rms')} RMS "
               f"(user floor {snapshot.get('config_floor_rms')}, "
               f"{snapshot.get('samples')} samples in the rolling window)")


def mic_drop_verdict(count, stream_ran):
    """Pure verdict for the mic-drop check (v5.51, E-2): `count`
    is the CURRENT stream's dropped-chunk count (AudioManager.
    get_mic_drop_count(), v5.43 A9 — the counter is per-stream,
    reset at every stream start) and `stream_ran` says whether a
    stream has run at all. Returns (status, detail). A count with
    no stream behind it is never dressed up as a healthy zero:
    that case is a WARN saying so plainly. Drops are WARN, never
    FAIL — speech still flows, it just may have gaps."""
    if not stream_ran or count is None:
        return ("WARN",
                "no microphone stream has run yet, so there is "
                "nothing to count — this is not a healthy zero")
    try:
        n = int(count)
    except (TypeError, ValueError):
        return ("WARN",
                "no microphone stream has run yet, so there is "
                "nothing to count — this is not a healthy zero")
    if n <= 0:
        return ("PASS",
                "microphone stream held — no drops this session")
    return ("WARN",
            f"the microphone dropped {n} time"
            f"{'s' if n != 1 else ''} this session — speech may "
            f"have gaps (the count covers the current stream "
            f"only, not all time)")


def _last_logged_mic_drops(log_path=None):
    """The most recent mic-drop count saved in the session log
    (data/activity.log), or None. AudioManager._log_mic_drops()
    writes one line there when a stream stops with drops, so a
    standalone doctor run — its own process, with no live stream
    to ask — can still report the last finished session's count
    honestly, labelled as the saved count. Never raises."""
    import re as _re

    path = Path(log_path) if log_path else (ROOT / "data"
                                            / "activity.log")
    try:
        lines = path.read_text(encoding="utf-8",
                               errors="replace").splitlines()
    except Exception:  # noqa: BLE001 — no log yet is a normal state
        return None
    found = None
    for line in lines:
        match = _re.search(r"dropped (\d+) mic chunk", line)
        if match:
            found = int(match.group(1))
    return found


def check_mic_drops(audio=None):
    """Microphone drops (v5.51, E-2): wire the A9 drop counter
    (core/voice/audio.py) into the doctor. Two honest paths:

      * Live (this process holds the audio engine — the app
        itself, or a test driving the real manager): the count
        comes straight from get_mic_drop_count() on the live
        singleton, and a stream counts as having run when one is
        open now or the counter already moved.
      * Standalone (`python doctor.py`): a separate process can
        never see the live counter, so it falls back to the last
        count the engine saved in data/activity.log when its
        stream stopped — reported as the saved count, never as a
        live one. With neither, the check says plainly that no
        stream has run yet instead of claiming a healthy zero.

    `audio` is the test seam: an AudioManager (or anything with
    get_mic_drop_count()) to read instead of the singleton."""
    _register_package()
    mgr = audio
    if mgr is None:
        try:
            mgr = importlib.import_module(
                "voice_assistant.core.voice.audio").audio_manager
        except Exception:  # noqa: BLE001 — core.voice.audio itself is
            mgr = None  # covered by the import + microphone checks
    count, stream_ran = None, False
    if mgr is not None:
        try:
            count = int(mgr.get_mic_drop_count())
            stream_ran = bool(
                getattr(mgr, "_in_stream", None) is not None
                or count > 0)
        except Exception:  # noqa: BLE001 — fall through to the log
            count, stream_ran = None, False
    if stream_ran:
        status, detail = mic_drop_verdict(count, True)
        report(status, "Microphone drops", detail,
               "if speech keeps having gaps, close other apps "
               "using the microphone, or pick a different "
               "microphone in Settings")
        return
    logged = _last_logged_mic_drops()
    if logged is not None:
        report("WARN", "Microphone drops",
               f"the last finished session saved a count of "
               f"{logged} dropped chunk"
               f"{'s' if logged != 1 else ''} — speech may have "
               f"had gaps; no stream is running in this doctor "
               f"run, so this is the saved count, not a live one",
               "if speech keeps having gaps, close other apps "
               "using the microphone, or pick a different "
               "microphone in Settings")
        return
    status, detail = mic_drop_verdict(None, False)
    report(status, "Microphone drops", detail,
           "connect and talk to VYUK first — the drop count "
           "starts with the microphone stream — then run the "
           "doctor again")


def check_world():
    """Your world (core/google_services.py, v2.4; write tools v5.2):
    Google Calendar + Gmail — read, plus send/create behind the
    in-app confirm popup. An unconnected Google account is a normal state,
    never a FAIL — this check only FAILs if the module itself is
    broken. PASS = connected; WARN = not connected yet, or the Google
    libraries aren't installed.

    v5.57.1 (item 4) audit note: every segment below is already
    guarded (import, find_spec loop, is_connected), so no line
    here explains the user's Windows crash on its own — the
    crash's cause was unrecoverable precisely because main()'s
    wrapper swallowed it. The wrapper now names the cause and
    logs the traceback, so any recurrence arrives diagnosable;
    no segment is re-guarded into a WARN, which would mask a
    genuinely broken module as a survivable state.
    """
    _register_package()
    try:
        services = importlib.import_module("voice_assistant.core.google_services")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Your world", f"could not import core.google_services ({type(e).__name__}: {e})",
               "open core/google_services.py — it must import cleanly with the google libraries absent (all google imports are lazy)")
        return
    libs = {}
    for import_name in ("googleapiclient", "google_auth_oauthlib"):
        try:
            libs[import_name] = importlib.util.find_spec(import_name) is not None
        except Exception:  # noqa: BLE001
            libs[import_name] = False
    missing = [name for name, ok in libs.items() if not ok]
    try:
        connected = services.is_connected()
    except Exception as e:  # noqa: BLE001 — is_connected must never raise; treat a raise as broken
        report("FAIL", "Your world", f"is_connected() raised ({type(e).__name__}: {e})",
               "open core/google_services.py — is_connected() reads data/google_token.json and must never raise")
        return
    if connected and not missing:
        report("PASS", "Your world", "Google connected — VYUK can read your calendar and mail, and send or add events only with your approval")
    elif connected and missing:
        report("WARN", "Your world", f"a Google token is saved, but the Google libraries are not installed ({', '.join(missing)}) — calendar/mail tools can't run until they're installed",
               "pip install -r requirements.txt  (adds google-api-python-client + google-auth-oauthlib)")
    elif missing:
        report("WARN", "Your world", f"Google libraries not installed ({', '.join(missing)}) — the calendar/mail tools stay unavailable until they're installed",
               "pip install -r requirements.txt  (adds google-api-python-client + google-auth-oauthlib)")
    else:
        report("WARN", "Your world", "Google not connected yet (optional) — calendar and mail tools will say how to connect",
               "open Settings → Your world, paste your Google Cloud client ID/secret, and press Connect Google")


def check_secrets_storage():
    """Secrets at rest (v5.47, programme item 14): which
    core/secrets_store backend protects the Google token file and
    the secret .env keys on THIS machine. PASS for a real backend
    (keyring / DPAPI / machine key); WARN — in plain words — when
    the backend is "none-plaintext": there is no way to protect
    the secrets here, and the user should know they sit on disk
    readable rather than be told a comforting fiction."""
    _register_package()
    try:
        store = importlib.import_module(
            "voice_assistant.core.secrets_store")
        backend = store.active_backend_name()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Secrets at rest",
               f"could not load core.secrets_store ({type(e).__name__}: {e})",
               "open core/secrets_store.py — the Google token and the .env API keys are protected through it")
        return
    if backend == "none-plaintext":
        report("WARN", "Secrets at rest",
               "WARNING: no protection backend is available on this "
               "machine, so secrets are stored in PLAINTEXT — the "
               "Google token (data/google_token.json) and the API "
               "keys in .env can be read by anyone who can read those "
               "files",
               "make the app folder writable (the machine-key file .vyuk_secret_key lives beside .env), or run on Windows (DPAPI) — see core/secrets_store.py")
    else:
        report("PASS", "Secrets at rest",
               f"secrets backend: {backend} — the Google token and "
               f"the secret .env keys are stored protected")


def data_egress_lines():
    """What leaves the machine, per brain mode (v5.47, programme
    item 14) — the honest egress table, worded from how the code
    actually routes data (core/config.py's brain fields +
    core/brain/brain.py's selection):

    Returns a list of (mode, sentence) pairs covering EVERY mode,
    not just the selected one — a user considering a switch should
    see what the switch changes. Pure text; check_data_egress
    reports it."""
    return [
        ("cloud Gemini Live (BRAIN_ENGINE=gemini, "
         "CLOUD_PROVIDER=gemini)",
         "microphone audio, screen frames the user shares, and "
         "tool results go to Google's API"),
        ("cloud-text / Groq (BRAIN_ENGINE=gemini, "
         "CLOUD_PROVIDER=groq)",
         "transcripts and tool results go to Groq — speech is "
         "transcribed on this machine, the text brain runs in "
         "Groq's cloud"),
        ("local builtin / lite (BRAIN_ENGINE=local)",
         "nothing leaves the machine — the brain, ears and voice "
         "all run on this PC (the ollama backend variant talks "
         "only to the configured Ollama host, localhost by "
         "default)"),
    ]


def check_data_egress():
    """What leaves the machine (v5.47, programme item 14): one
    line per brain mode from data_egress_lines(), plus which mode
    this install is currently configured for. Informational by
    design — every mode is a legitimate user choice, so this
    never FAILs; it exists so the answer to "what does she send
    where?" is printed, not buried."""
    _register_package()
    selected = "local builtin / lite"
    try:
        cfg = importlib.import_module(
            "voice_assistant.core.config").config
        engine = (getattr(cfg, "brain_engine", "local")
                  or "local").strip().lower()
        provider = (getattr(cfg, "cloud_provider", "gemini")
                    or "gemini").strip().lower()
        if engine == "local":
            backend = (getattr(cfg, "local_backend", "builtin")
                       or "builtin").strip().lower()
            selected = f"local (backend: {backend})"
        elif provider == "groq":
            selected = "cloud-text / Groq"
        else:
            selected = "cloud Gemini Live"
    except Exception:  # noqa: BLE001 — selection is a nicety
        selected = "unknown (config unreadable)"
    for mode, sentence in data_egress_lines():
        marker = " [currently selected]" if mode.startswith(
            selected.split(" (")[0]) else ""
        report("PASS", f"What leaves the machine — {mode}",
               f"{sentence}{marker}")


def check_integrity():
    """Install-tree integrity (v5.47, programme item 10): are the
    program files on disk the ones the signed install manifest
    (install_manifest.json at the package root, verified against
    the pinned release key by core/health/integrity.py) says were
    shipped? PASS with the checked-file count when the tree
    verifies. WARN — never FAIL — when it doesn't: a dev checkout
    is legitimately edited, and a support flow must not be
    bricked by a stale manifest; the first few problems are
    named so the report says exactly what drifted."""
    _register_package()
    try:
        integrity = importlib.import_module(
            "voice_assistant.core.health.integrity")
        result = integrity.verify_install_tree(ROOT)
    except Exception as e:  # noqa: BLE001
        report("WARN", "Install-tree integrity",
               f"could not run the integrity check ({type(e).__name__}: {e})",
               "open core/health/integrity.py — verify_install_tree() must never raise; the signed manifest is install_manifest.json at the package root")
        return
    if result.get("ok"):
        report("PASS", "Install-tree integrity",
               f"{result.get('checked', 0)} program files match "
               f"the signed install manifest")
    else:
        problems = [str(p) for p in result.get("problems", [])]
        shown = "; ".join(problems[:3])
        if len(problems) > 3:
            shown += f"; … and {len(problems) - 3} more"
        report("WARN", "Install-tree integrity",
               f"{len(problems)} problem(s): {shown}",
               "a re-extract restores the shipped tree; in a dev checkout or after a deliberate edit, regenerate + re-sign the manifest at release time (packaging/gen_install_manifest.py — see packaging/README.md)")


def check_learning():
    """She learns you (core/learning.py, v2.6): the deterministic
    pattern extractor that files user statements as 'learned' facts.
    PASS = the module imports and a probe sentence yields the expected
    name + likes candidates. This check is read-only — extract only;
    the store itself is covered by the module-import check, and the
    doctor never writes facts."""
    _register_package()
    try:
        learning = importlib.import_module("voice_assistant.core.memory.learning")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Learning", f"could not import core.memory.learning ({type(e).__name__}: {e})",
               "open core/learning.py — it must import cleanly (stdlib + core.memory.memory only)")
        return
    try:
        candidates = learning.extract_candidates("My name is Doctor Probe and I love black coffee.")
    except Exception as e:  # noqa: BLE001 — extract_candidates must be pure and never raise
        report("FAIL", "Learning", f"extract_candidates() raised ({type(e).__name__}: {e})",
               "open core/learning.py — extract_candidates() is pure pattern matching and must never raise")
        return
    keys = {c.get("key") for c in candidates if isinstance(c, dict)}
    kinds = {c.get("kind") for c in candidates if isinstance(c, dict)}
    if "name" in kinds and "likes" in kinds:
        report("PASS", "Learning", f"extractor live — probe sentence yielded {len(candidates)} candidate(s): {', '.join(sorted(keys))}")
    else:
        report("FAIL", "Learning", f"probe sentence yielded {sorted(keys) or 'no candidates'} — expected name + likes candidates",
               "check the sentence patterns in core/learning.py (extract_candidates)")


def check_assets():
    assets = ROOT / "gui" / "assets"
    expected = ["vyuk_icon.png", "wake.wav", "sent.wav", "done.wav", "error.wav"]
    missing = [name for name in expected if not (assets / name).exists()]
    if not missing:
        report("PASS", "GUI assets", "vyuk_icon.png + 4 earcon wavs present in gui/assets/")
    else:
        report("FAIL", "GUI assets", f"missing: {', '.join(missing)}",
               "restore the file(s) in gui/assets/ (re-extract the package) — the tray icon needs vyuk_icon.png and gui/sounds.py silently skips missing earcon wavs")


def check_platform_backend():
    """Platform backend (v5.50, programme items 41/42): which
    core.platform backend serves this process, and — the honest
    label — whether it is an EXPERIMENTAL one. The Windows
    backend is the shipping backend (PASS). The macOS/Linux
    backends are EXPERIMENTAL: contract-tested against faked OS
    seams only, never run against a real Mac/Linux desktop, so
    they report WARN with that stated plainly. The scripted fake
    backend (VYUK_PLATFORM=fake) is a test double, also WARN so
    a field run can never mistake it for a real desktop."""
    _register_package()
    try:
        platform_pkg = importlib.import_module(
            "voice_assistant.core.platform")
        backend = platform_pkg.get_backend()
        name = getattr(backend, "name", "") or "?"
        requested = getattr(backend, "requested_platform", "") or "?"
    except Exception as e:  # noqa: BLE001
        report("FAIL", "Platform backend",
               f"could not resolve the platform backend ({type(e).__name__}: {e})",
               "open core/platform/__init__.py — get_backend() must resolve headless on every OS")
        return
    if name == "windows":
        report("PASS", "Platform backend",
               f"windows backend serving (requested platform: {requested}) — the shipping backend")
    elif name == "fake":
        report("WARN", "Platform backend",
               "fake backend serving (VYUK_PLATFORM=fake) — a scripted test double, not a real desktop",
               "unset VYUK_PLATFORM for real use; the fake backend exists so tool code can be exercised in tests")
    elif name in ("darwin", "linux"):
        report("WARN", "Platform backend",
               f"{name} backend serving (requested platform: {requested}) — EXPERIMENTAL: contract-tested against faked OS seams only, not yet run against a real desktop",
               "treat every hands/eyes result on this platform as provisional; Windows is the shipping platform in this release")
    else:
        report("WARN", "Platform backend",
               f"backend '{name}' serving (requested platform: {requested})",
               "an unrecognised backend is serving — check core/platform/__init__.py's registry")


def check_failure_clusters():
    """Failure clusters mined from the live audit log (wiring item 6,
    trace mining): read-only diagnostics. Clusters are REPORTED, never
    auto-committed — a cluster becomes a regression case only when a
    human supplies the expected behaviour (w8 commit_mined_case)."""
    _register_package()
    try:
        w8 = importlib.import_module("voice_assistant.core.w8_wiring")
    except Exception:
        report("WARN", "Failure clusters",
               "the trace miner could not be loaded, so no clusters were mined",
               "this is a wiring problem in doctor.py itself — please report it with this line")
        return
    try:
        mined = w8.mine_live_trace_clusters()
    except Exception:
        report("WARN", "Failure clusters",
               "the live failure log could not be read, so no clusters were mined",
               "a missing or unreadable audit-chain store is normal on a fresh install")
        return
    clusters = mined.get("clusters", []) if isinstance(mined, dict) else []
    if not clusters:
        report("PASS", "Failure clusters",
               "no failure clusters mined from the live log")
        return
    top = "; ".join(
        f"{c.get('tool', '?')} x{c.get('count', 0)}"
        for c in clusters[:3])
    report("WARN", "Failure clusters",
           f"{len(clusters)} failure cluster(s) mined from the live log "
           f"({mined.get('total_failures', 0)} failures; top: {top})",
           "review the clusters; a case becomes a regression test only "
           "when a human supplies the expected behaviour")


_CHECKS = [check_python, check_layout, check_process_load, check_compile, check_deps,
           check_module_imports, check_tool_registry, check_pack_registrations,
           check_brain,
           check_memory_profile, check_config, check_gemini_key_tier,
           check_cloud_doors,  # Fix 5: REST + Live doors, one probe
           check_data, check_presence,
           check_watchers, check_tool_payload, check_tool_router,
           check_piper_voice, check_ocr_engine, check_real_chrome,
           check_mcp_servers,  # v5.55 (HE-1/HE-5)
           check_home_assistant,  # R7-9 homeassistantpy (dormant client, D10 honest-empty)
           check_capability_probes,  # v5.54 (EY-H1, report-only)
           check_microphone, check_mic_drops,  # v5.51 (E-2)
           check_world,
           check_secrets_storage, check_data_egress,
           check_integrity,  # v5.47 (programme item 10)
           check_platform_backend,  # v5.50 (programme items 41/42)
           check_learning, check_assets, check_state_files,
           check_receipt_store,  # doctor receipt-store health
           check_write_proof,  # click-anywhere Batch 0: the write proof
           check_failure_clusters]  # wiring item 6 (trace mining)


def main() -> int:
    # Encoding-safe output (field crash: a cp1252 console
    # raised UnicodeEncodeError on '→' inside report() and
    # aborted check_watchers + check_world). Prefer real UTF-8
    # output; report() itself falls back to replace-on-error
    # (see _emit), so no console encoding can kill a check.
    _make_output_encoding_safe()
    # UI Phase 4 (D-P4-18): selected runs — `--only a,b` runs
    # the checks in those categories (comma-separated, one of
    # the six in _CATEGORIES); `--check <name>` runs one check
    # by function name (the Doctor page's repair verification).
    # No flag means every check, as before.
    _only = None
    _only_check = None
    _args = list(sys.argv[1:])
    _i = 0
    while _i < len(_args):
        if _args[_i] == "--only" and _i + 1 < len(_args):
            _only = [c.strip().lower() for c in _args[_i + 1].split(",")
                     if c.strip()]
            _i += 2
        elif _args[_i] == "--check" and _i + 1 < len(_args):
            _only_check = _args[_i + 1]
            _i += 2
        else:
            _i += 1
    if _only_check:
        _run_checks = [c for c in _CHECKS
                       if c.__name__ == _only_check]
    elif _only:
        _run_checks = [c for c in _CHECKS
                       if _CHECK_CATEGORIES.get(c.__name__) in _only]
    else:
        _run_checks = list(_CHECKS)
    print(f"VYUK doctor — checking the package at {ROOT}\n")
    global _CURRENT_CHECK_NAME
    _check_outcomes.clear()
    for check in _run_checks:
        _CURRENT_CHECK_NAME = check.__name__
        try:
            check()
        except Exception as e:  # noqa: BLE001 — a broken check is a FAIL, never a crash
            # v5.57.1 (item 4): a crashing check's verdict NAMES
            # its cause (type + message) and the full traceback
            # goes to stderr — the v5.51 (X-1) no-exception-names
            # rule is deliberately overturned for crash verdicts
            # only: a bare "Doctor check" verdict sent the owner
            # back with nothing diagnosable, twice. Ordinary
            # verdicts still carry no exception names.
            traceback.print_exc()
            report("FAIL", f"Doctor check: {check.__name__}",
                   "the check itself ran into a problem and couldn't finish "
                   f"({type(e).__name__}: {e})",
                   "this is a problem in doctor.py itself — please report it with this line")
    print(f"\nSUMMARY: {_counts['PASS']} PASS · {_counts['WARN']} WARN · {_counts['FAIL']} FAIL")
    # Fix round (F-P4-02): the per-check summary the Doctor page
    # hero reads — one outcome per check, so the three numbers sum
    # to the number of checks run. The SUMMARY line above stays
    # exactly as-is (verdict-line counts — the CLI contract does
    # not change).
    _cc = _check_summary_counts()
    print(f"CHECK_SUMMARY: {_cc['PASS']} PASS · {_cc['WARN']} WARN · {_cc['FAIL']} FAIL")
    if _counts["FAIL"]:
        print("Result: FAIL — fix the [FAIL] lines above (each one names its file).")
        return 1
    print("Result: OK — no failures. WARNs are survivable; check them if a feature misbehaves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
