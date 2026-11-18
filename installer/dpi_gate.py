"""Build gate for the VYUK exe DPI manifest — Wave 10 (v5.65) Leaf 1,
UI-48a/b.

Fails (nonzero exit + one plain FAIL line) unless the manifest it is
given declares <dpiAwareness>PerMonitorV2</dpiAwareness>. Passes print
one plain PASS line. The gate never raises a traceback for an
expected failure (missing file, malformed XML, wrong value): a build
gate must fail readably, not dump a stack trace into a build log.

Two modes:

* Manifest-file mode (default, runs anywhere):
    python installer/dpi_gate.py installer/vyuk.exe.manifest

* Embedded-exe mode (Windows only):
    python installer/dpi_gate.py --exe dist/vyuk/vyuk.exe
  Mechanism: mt.exe (Windows SDK / Visual Studio Build Tools)
  extracts the embedded manifest —
      mt.exe -inputresource:"<exe>";#1 -out:"<temp>.manifest"
  — and the extracted text is checked exactly like a manifest file.
  Off Windows there is no mt.exe and no embedded manifest to read,
  so this mode fails with a plain "Windows-only" message instead of
  pretending to check anything. On Windows without mt.exe on PATH it
  likewise fails naming mt.exe, rather than passing vacuously.

No third-party imports: argparse, shutil, subprocess, sys,
tempfile, xml.etree only, so the gate runs on a bare build Python.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

DPI_AWARENESS_VALUE = "PerMonitorV2"


def _local_name(tag: str) -> str:
    """Strip an XML namespace: '{ns}dpiAwareness' -> 'dpiAwareness'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def manifest_has_permonitorv2(xml_text: str) -> bool:
    """True iff some <dpiAwareness> element's text is PerMonitorV2.

    Namespace-agnostic (the 2016 WindowsSettings namespace and any
    other spelling both count); whitespace around the value is
    ignored; malformed XML is False, never an exception.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    for element in root.iter():
        if _local_name(element.tag) == "dpiAwareness":
            if (element.text or "").strip() == DPI_AWARENESS_VALUE:
                return True
    return False


def check_manifest_file(path: str) -> tuple[bool, str]:
    """Check a manifest file on disk. Returns (ok, plain message)."""
    if not os.path.isfile(path):
        return False, f"FAIL: manifest not found: {path}"
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as exc:
        return False, f"FAIL: cannot read manifest {path}: {exc}"
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        return False, f"FAIL: manifest is not well-formed XML: {exc}"
    if manifest_has_permonitorv2(text):
        return True, (
            f"PASS: {path} declares "
            f"<dpiAwareness>{DPI_AWARENESS_VALUE}</dpiAwareness>"
        )
    return (
        False,
        f"FAIL: {path} does not declare "
        f"<dpiAwareness>{DPI_AWARENESS_VALUE}</dpiAwareness>",
    )


def check_embedded_exe(path: str) -> tuple[bool, str]:
    """Check the manifest embedded in a Windows exe (Windows only).

    Returns (ok, plain message). Off Windows, or without mt.exe,
    this is a FAIL naming the missing piece — never a silent pass.
    """
    if os.name != "nt":
        return (
            False,
            "FAIL: embedded-exe manifest check is Windows-only "
            "(requires mt.exe from the Windows SDK)",
        )
    if not os.path.isfile(path):
        return False, f"FAIL: exe not found: {path}"
    mt_exe = shutil.which("mt.exe")
    if mt_exe is None:
        return (
            False,
            "FAIL: mt.exe not found on PATH "
            "(install the Windows SDK / Visual Studio Build Tools)",
        )
    with tempfile.TemporaryDirectory() as tmp:
        extracted = os.path.join(tmp, "extracted.manifest")
        proc = subprocess.run(
            [mt_exe, f'-inputresource:"{path}";#1',
             f'-out:"{extracted}"'],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not os.path.isfile(extracted):
            detail = (proc.stderr or proc.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            return (
                False,
                f"FAIL: mt.exe could not extract a manifest from "
                f"{path}{suffix}",
            )
        ok, message = check_manifest_file(extracted)
        if ok:
            return True, (
                f"PASS: embedded manifest in {path} declares "
                f"<dpiAwareness>{DPI_AWARENESS_VALUE}</dpiAwareness>"
            )
        return (
            False,
            f"FAIL: embedded manifest in {path} does not declare "
            f"<dpiAwareness>{DPI_AWARENESS_VALUE}</dpiAwareness>",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail unless a manifest declares PerMonitorV2.")
    parser.add_argument("path", help="manifest file, or exe with --exe")
    parser.add_argument(
        "--exe", action="store_true",
        help="check the manifest embedded in an exe (Windows only)")
    args = parser.parse_args(argv)
    if args.exe:
        ok, message = check_embedded_exe(args.path)
    else:
        ok, message = check_manifest_file(args.path)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
