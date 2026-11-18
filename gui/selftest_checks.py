"""The Self-test auto-run set — the single place the set is defined.

Owner decision 2026-11-01 (#12): the Self-test AUTO-RUNS when
opened, plus a manual re-run button; the auto-run set is safe,
fast tests only. This module is that set: `run_first_run_self_tests`
(previously defined in gui/onboarding.py, moved here verbatim
except the Microphone check's audio import, which is now deferred
inside the check — see below).

Safety justification, per check — every check is read-only, no
network, no data/ writes, seconds total:
- Python: reads sys.version_info only.
- Package layout: directory-existence reads only.
- Settings: imports the config module. The config module's only
  data-dir touch at import is DATA_DIR.mkdir(exist_ok=True) — a
  no-op when the directory already exists, and it writes no files.
- Microphone: audio device enumeration only (sd.query_devices);
  nothing is captured, opened, or changed. The audio import is
  deferred inside the check's try (it used to be a module-global
  import in onboarding.py) so a missing/broken audio backend
  reports FAULT here instead of breaking the module import —
  on a real install the behaviour is identical.
- On-device voice: file-size reads only (kokoro model + voice
  bank presence).
- Install integrity: manifest file read + JSON parse only.

A check that cannot run reports FAULT with its reason — never a
placeholder pass. The full Doctor (all checks, with fixes) still
lives in Diagnostics; this is the first-run subset only.

This module is stdlib-only at top level on purpose: the set must
be importable and runnable anywhere (including test interpreters
without Qt or without the voice_assistant package), with every
product import deferred inside a check's own try.
"""

SELFTEST_CHECK_NAMES = (
    "Python",
    "Package layout",
    "Settings",
    "Microphone",
    "On-device voice",
    "Install integrity",
)


def run_first_run_self_tests() -> list[tuple[str, bool, str]]:
    """The first-mission self-test: small, fast, REAL checks with
    no side effects and no network. Each entry is (name, ok,
    detail). A check that cannot run reports FAULT with its
    reason — never a placeholder pass. The full Doctor (all
    checks, with fixes) still lives in Diagnostics; this is the
    first-run subset only."""
    results: list[tuple[str, bool, str]] = []
    try:
        import sys as _sys

        version = _sys.version_info
        ok = version >= (3, 10)
        results.append(("Python", ok,
                        f"Running {version.major}.{version.minor}."
                        f"{version.micro}" if ok else
                        "VYUK needs Python 3.10 or newer"))
    except Exception:  # noqa: BLE001 — a broken probe is a FAULT
        results.append(("Python", False, "Could not read the version"))
    try:
        from pathlib import Path as _Path

        root = _Path(__file__).resolve().parent.parent
        missing = [name for name in ("core", "gui", "plugins", "data")
                   if not (root / name).is_dir()]
        results.append(("Package layout", not missing,
                        "core, gui, plugins, data all present"
                        if not missing else
                        f"Missing folder(s): {', '.join(missing)}"))
    except Exception:  # noqa: BLE001
        results.append(("Package layout", False, "Could not inspect"))
    try:
        from voice_assistant.core.gui_facade import config as _cfg

        results.append(("Settings", True,
                        "Configuration loads"))
        _ = _cfg
    except Exception:  # noqa: BLE001
        results.append(("Settings", False, "Configuration failed to load"))
    try:
        from voice_assistant.core.gui_facade import audio_manager as _am

        devs = _am.get_devices() or {}
        inputs = list(devs.get("inputs", []) or [])
        results.append(("Microphone", bool(inputs),
                        f"{len(inputs)} input(s) found" if inputs else
                        "No microphone detected"))
    except Exception:  # noqa: BLE001
        results.append(("Microphone", False, "Could not enumerate inputs"))
    try:
        from voice_assistant.core.gui_facade import local_assets as _la

        ready = bool(_la.voice_ready())
        results.append(("On-device voice", ready,
                        "Voice ready" if ready else
                        "Voice pack not ready yet"))
    except Exception:  # noqa: BLE001
        results.append(("On-device voice", False,
                        "Could not check the voice pack"))
    try:
        from pathlib import Path as _Path
        import json as _json

        root = _Path(__file__).resolve().parent.parent
        manifest = root / "install_manifest.json"
        sig = root / "install_manifest.json.sig"
        if manifest.is_file() and sig.is_file():
            parsed = _json.loads(manifest.read_text(encoding="utf-8"))
            count = len(parsed.get("files", {}))
            results.append(("Install integrity", True,
                            f"Manifest present ({count} files listed)"))
        else:
            results.append(("Install integrity", False,
                            "Manifest or its signature is missing"))
    except Exception:  # noqa: BLE001
        results.append(("Install integrity", False,
                        "Manifest could not be read"))
    return results
