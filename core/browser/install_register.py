"""Install registration helper (click-anywhere Batch 1, Plan 9 §1.2, M-10/R-23).

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

Writes the Windows registry keys the native-messaging host needs.
No NativeMessagingHosts key exists in the tree today — these are NEW
writes:

- ``HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<name>`` and
  ``HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts\\<name>`` —
  each key's default value is the path of the host manifest JSON file,
  which names the launcher exe (``host_launcher.exe``), the extension
  id in ``allowed_origins`` and ``"type": "stdio"``.
- ``HKCU\\Software\\VYUK\\BrowserHost`` value ``PythonPath`` — the
  interpreter path ONLY (the C stub reads it to spawn
  ``python.exe -m core.browser.bridge_host``); re-written on every
  VYUK start alongside the re-registration.

CALL SITE (not wired here — the parent does integration): the
installer entry points call :func:`register_browser_host` at install,
and VYUK's startup calls it again on every start (re-registration).
Re-registration failure is best-effort: this module returns
``(False, reason)`` and the caller logs a line + raises a
doctor-page warning; VYUK still starts, and the extension route
reports "host not registered" on first use.

All Windows-only code paths are guarded by ``sys.platform``: on any
other platform the functions return ``(False, "Windows-only ...")``
without touching anything.
"""

import json
import os
import sys
from pathlib import Path

HOST_NAME = "com.vyuk.browser_host"
_BROWSER_HOST_KEY = r"Software\VYUK\BrowserHost"
_CHROME_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
_EDGE_KEY = rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"


def default_python_path() -> str:
    """The running interpreter — the value written as ``PythonPath``."""
    return sys.executable


def host_manifest(host_name: str, launcher_path, extension_id=None) -> dict:
    """The native-messaging host manifest Chrome/Edge read.

    ``extension_id`` is the extension's stable id (from the manifest
    ``"key"``); when it is not known yet, ``allowed_origins`` is empty
    and the manifest must be re-written once the id is known — a
    manifest with no allowed origin accepts no extension.
    """
    origins = ([f"chrome-extension://{extension_id}/"]
               if extension_id else [])
    return {
        "name": host_name,
        "description": "VYUK browser host (native messaging)",
        "path": str(launcher_path),
        "type": "stdio",
        "allowed_origins": origins,
    }


def _write_manifest_file(manifest: dict, manifest_path) -> Path:
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def register_browser_host(python_path=None, launcher_path=None,
                          extension_id=None, host_name=HOST_NAME,
                          manifest_dir=None):
    """Register the native host (install step + every VYUK start).

    Writes the Chrome + Edge NativeMessagingHosts keys (pointing at
    the manifest file), the manifest file itself, and
    ``HKCU\\Software\\VYUK\\BrowserHost`` / ``PythonPath``.

    Returns ``(True, message)`` on success, ``(False, reason)`` on
    failure — including on non-Windows platforms, where nothing is
    written. Failure is best-effort for the caller (log + doctor
    warning; VYUK still starts).
    """
    if sys.platform != "win32":
        return False, "browser host registration is Windows-only"
    try:
        import winreg
    except ImportError:
        return False, "winreg unavailable on this platform"
    python_path = python_path or default_python_path()
    install_root = Path(sys.executable).resolve().parent
    launcher = Path(launcher_path) if launcher_path else (
        install_root / "browser_extension" / "host_launcher.exe")
    manifest_dir = Path(manifest_dir) if manifest_dir else (
        install_root / "browser_extension")
    manifest_path = manifest_dir / f"{host_name}.json"
    try:
        _write_manifest_file(
            host_manifest(host_name, launcher, extension_id), manifest_path)
        for key_path in (_CHROME_KEY, _EDGE_KEY):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _BROWSER_HOST_KEY) as key:
            winreg.SetValueEx(key, "PythonPath", 0, winreg.REG_SZ, str(python_path))
    except OSError as exc:
        return False, f"browser host registration failed: {exc}"
    return True, (
        f"browser host registered ({host_name}; manifest {manifest_path}; "
        f"PythonPath {python_path})")


def read_python_path():
    """Read back ``HKCU\\Software\\VYUK\\BrowserHost`` / ``PythonPath``.
    Returns the path string, or None when absent (the C stub then
    exits with the named error "host not registered")."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BROWSER_HOST_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "PythonPath")
            return str(value)
    except (ImportError, OSError):
        return None
