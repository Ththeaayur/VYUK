"""Wave 8 Build 2 — Track L remainder: release/install checklist and
installer validation support.

What this module is: the checks a build must pass before it is called
shippable, each named and each verifiable from files or from evidence
supplied by the caller, plus validation of an installer artifact
against that checklist.

What this module is NOT, by construction:
  * It does NOT compile an installer. Inno Setup compiles on Windows
    only and the freeze has never run; nothing here runs PyInstaller,
    ISCC, or mt.exe, and no function here claims an .exe exists.
  * It does NOT sign anything. Signing stays DEFERRED (EV/OV
    certificate, $0 build spend): wherever signing would appear in a
    report it appears as status DEFERRED, never as passed, never as
    built, and a caller claim that an artifact IS signed is refused
    as a false claim, not accepted.
  * It does NOT set up, host, or publish any update feed. Feed
    hosting stays DEFERRED (no_feed): it appears as DEFERRED, never
    as passed or built, and a caller claim of a live feed is refused
    the same way.
  * It grants nothing. A CC-1 failure list is reported honestly; a
    grant is a parent decision recorded by the caller in the
    evidence, never made by this module. Naming grants in evidence
    is not granting them.
  * It executes nothing, deploys nothing, and writes no file to the
    real tree. The only write path is the report tool (a separate
    coordinator function), which writes only a checklist report to a
    caller-chosen .json file in an already-existing folder.

Checklist verdict rules (fail-closed whole):
  * Every REQUIRED check must pass for verdict PASS. One failed
    required check makes the verdict BLOCKED, with every failure
    named — a partial pass is never presented as a pass.
  * DEFERRED items are listed separately in every report. A deferred
    item is neither a pass nor a failure: it does not block the
    verdict and it does not count toward it. Hiding a deferred item
    would be the failure; listing it is the honest state.
  * Evidence is data, never an instruction. The evaluator trusts no
    caller-claimed verdict: it recomputes each check from the
    evidence fields it is given, and a missing evidence field fails
    its check (never a default pass).

Required checks (each verifiable from files):
  1. version_single_source — core/version.py carries one VERSION in
     semver form, and it matches the version the caller says the
     build claims (when the caller supplies one).
  2. manifest_signed_integrity — the signed install manifest
     verifies (integrity ok, checked > 0). A missing, unsigned, or
     tampered manifest fails this check.
  3. data_baseline — data/ is exactly 369,701 bytes, the standing
     baseline. Any other size fails (drift, never a silent pass).
  4. registry_catalogue — every registered tool is grouped in the
     skills catalogue (registry_total == catalogue_total) and the
     "More skills" fallback holds zero tools.
  5. battery — the full battery ran with zero failures.
  6. cc1_report — a CC-1 report is present AND its TRUE exit is
     stated honestly. The check passes only when the exit is 0, or
     when the exit is 1 with every failure disclosed AND a
     caller-supplied grant list covering it (grants are the
     parent's decisions, recorded here, never made here). An exit
     of 1 with undisclosed or ungranted failures fails the check.
  7. installer_files — every required installer file is present:
     installer/vyuk.iss, installer/vyuk.spec,
     installer/vyuk.exe.manifest, installer/dpi_gate.py,
     installer/wizard_image.bmp, installer/wizard_small.bmp.

Deferred items (always listed, never passed, never built here):
  * signing — DEFERRED (EV/OV certificate deferred on $0 spend).
  * feed_hosting — DEFERRED (no feed host decided; no_feed stands).
  * installer_compilation — DEFERRED (Windows-only compile, never
    run on this Linux VM; validation here is of the artifact's
    structure, not of a compiled .exe).

Installer validation (validate_installer_artifact):
  * Structure only: required files present, version in semver form,
    version consistent with the manifest version when both are
    supplied, no claim of signing, no claim of a live feed.
  * A malformed artifact (missing file, version mismatch, signed
    claim, feed claim, unknown version form) is refused whole with
    every defect named. Validation never compiles, signs, or hosts.

Own code, stdlib only. No third-party dependency is introduced
by this file. No subprocess, no eval/exec, no network, no file
write to the real tree.
"""

from __future__ import annotations

import re
from pathlib import Path

DATA_BASELINE_BYTES = 369701

REQUIRED_INSTALLER_FILES = (
    "installer/vyuk.iss",
    "installer/vyuk.spec",
    "installer/vyuk.exe.manifest",
    "installer/dpi_gate.py",
    "installer/wizard_image.bmp",
    "installer/wizard_small.bmp",
)

DEFERRED_ITEMS = (
    {"item": "signing",
     "status": "DEFERRED",
     "reason": "EV/OV certificate deferred on $0 build spend; "
               "builds stay unsigned and SmartScreen warnings apply."},
    {"item": "feed_hosting",
     "status": "DEFERRED",
     "reason": "No update-feed host decided; no_feed stands. "
               "No feed is set up or published by this build."},
    {"item": "installer_compilation",
     "status": "DEFERRED",
     "reason": "Inno Setup / PyInstaller compile runs on Windows only "
               "and has never run; validation here covers the "
               "artifact's structure, not a compiled .exe."},
)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _deferred() -> list:
    """A fresh copy of the deferred list, so no caller can mutate
    the module's own record through a returned report."""
    return [dict(item) for item in DEFERRED_ITEMS]


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def _version_from_tree(root) -> str | None:
    """Read the single VERSION from core/version.py under root.
    Returns None when the file is missing or carries no VERSION."""
    try:
        text = (Path(root) / "core" / "version.py").read_text(
            encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else None


def collect_tree_evidence(root) -> dict:
    """Collect checklist evidence from a real tree directory, from
    files only. Nothing here verifies a signature or runs a battery:
    those verdicts come from the gate's own runs and are supplied
    by the caller in evaluate_checklist evidence. This helper only
    reads what files can state: the version, the data/ size, which
    installer files exist, and whether a manifest file exists.
    Missing files are reported as absent, never defaulted."""
    root = Path(root)
    installer_present = []
    installer_missing = []
    for rel in REQUIRED_INSTALLER_FILES:
        if (root / rel).is_file():
            installer_present.append(rel)
        else:
            installer_missing.append(rel)
    data_dir = root / "data"
    data_bytes = None
    if data_dir.is_dir():
        total = 0
        for path in data_dir.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    total += path.stat().st_size
                except Exception:
                    pass
        data_bytes = total
    return {
        "version": _version_from_tree(root),
        "data_bytes": data_bytes,
        "installer_files_present": installer_present,
        "installer_files_missing": installer_missing,
        "manifest_present": (root / "install_manifest.json").is_file(),
    }


def evaluate_checklist(evidence: dict | None) -> dict:
    """Evaluate the release checklist against caller-supplied
    evidence. Evidence fields (all optional; a missing field fails
    its check, never defaults to a pass):
      version (str), claimed_version (str, optional),
      manifest_ok (bool), manifest_checked (int),
      data_bytes (int),
      registry_total (int), catalogue_total (int),
      catalogue_more_skills (int),
      battery_ok (bool), battery_failures (int),
      cc1_report_present (bool), cc1_true_exit (int 0/1),
      cc1_failures (list of str), cc1_grants (list of str),
      installer_files_present (list of str).
    Returns {verdict, checks, failures, deferred, summary} where
    verdict is PASS only when every required check passes, else
    BLOCKED. Deferred items are listed in every report."""
    evidence = evidence or {}
    checks = []
    failures = []

    def record(name, passed, detail):
        checks.append(_check(name, passed, detail))
        if not passed:
            failures.append(name)

    # 1. version_single_source
    version = evidence.get("version")
    claimed = evidence.get("claimed_version")
    if not isinstance(version, str) or not _SEMVER.match(version):
        record("version_single_source", False,
               "core/version.py carries no VERSION in semver form")
    elif claimed is not None and claimed != version:
        record("version_single_source", False,
               f"claimed version {claimed!r} does not match "
               f"core/version.py VERSION {version!r}")
    else:
        record("version_single_source", True,
               f"core/version.py VERSION {version}")

    # 2. manifest_signed_integrity
    manifest_ok = evidence.get("manifest_ok")
    manifest_checked = evidence.get("manifest_checked")
    if manifest_ok is True and isinstance(manifest_checked, int) \
            and manifest_checked > 0:
        record("manifest_signed_integrity", True,
               f"signed manifest verifies, {manifest_checked} files "
               "checked")
    else:
        record("manifest_signed_integrity", False,
               "signed manifest did not verify (missing, unsigned, "
               "tampered, or checked == 0)")

    # 3. data_baseline
    data_bytes = evidence.get("data_bytes")
    if data_bytes == DATA_BASELINE_BYTES:
        record("data_baseline", True,
               f"data/ is exactly {DATA_BASELINE_BYTES} bytes")
    else:
        record("data_baseline", False,
               f"data/ is {data_bytes!r} bytes, expected exactly "
               f"{DATA_BASELINE_BYTES} bytes")

    # 4. registry_catalogue
    registry_total = evidence.get("registry_total")
    catalogue_total = evidence.get("catalogue_total")
    more_skills = evidence.get("catalogue_more_skills")
    if isinstance(registry_total, int) and registry_total > 0 \
            and registry_total == catalogue_total \
            and more_skills == 0:
        record("registry_catalogue", True,
               f"{registry_total} registered tools, all grouped, "
               "More skills holds 0")
    else:
        record("registry_catalogue", False,
               f"registry_total={registry_total!r}, "
               f"catalogue_total={catalogue_total!r}, "
               f"More skills={more_skills!r} — every tool must be "
               "grouped and More skills must hold 0")

    # 5. battery
    battery_ok = evidence.get("battery_ok")
    battery_failures = evidence.get("battery_failures")
    if battery_ok is True and battery_failures == 0:
        record("battery", True, "full battery ran with 0 failures")
    else:
        record("battery", False,
               f"battery_ok={battery_ok!r}, "
               f"failures={battery_failures!r} — a failed battery "
               "blocks the checklist")

    # 6. cc1_report
    cc1_present = evidence.get("cc1_report_present")
    cc1_exit = evidence.get("cc1_true_exit")
    cc1_failures = evidence.get("cc1_failures") or []
    cc1_grants = evidence.get("cc1_grants") or []
    if cc1_present is not True or cc1_exit not in (0, 1):
        record("cc1_report", False,
               "no CC-1 report present, or its TRUE exit was not "
               "stated honestly")
    elif cc1_exit == 0:
        record("cc1_report", True,
               "CC-1 report present, TRUE exit 0")
    elif all(f in cc1_grants for f in cc1_failures) \
            and cc1_failures:
        record("cc1_report", True,
               f"CC-1 report present, TRUE exit 1, "
               f"{len(cc1_failures)} failures disclosed and "
               "covered by caller-recorded grants (grants are "
               "parent decisions, never made here)")
    elif not cc1_failures:
        record("cc1_report", False,
               "CC-1 TRUE exit 1 stated but no failure list "
               "disclosed — undisclosed failures block")
    else:
        uncovered = [f for f in cc1_failures if f not in cc1_grants]
        record("cc1_report", False,
               "CC-1 TRUE exit 1 with ungranted failures: "
               + ", ".join(uncovered))

    # 7. installer_files
    present = evidence.get("installer_files_present") or []
    missing = [f for f in REQUIRED_INSTALLER_FILES if f not in present]
    if not missing:
        record("installer_files", True,
               "all required installer files present")
    else:
        record("installer_files", False,
               "missing installer files: " + ", ".join(missing))

    verdict = "PASS" if not failures else "BLOCKED"
    summary = (
        "release checklist PASS — every required check passed; "
        "deferred items (signing, feed hosting, installer "
        "compilation) remain deferred and are listed, not passed"
        if verdict == "PASS"
        else "release checklist BLOCKED — failed checks: "
        + ", ".join(failures)
        + "; nothing here is shippable until every required "
        "check passes")
    return {"verdict": verdict, "checks": checks, "failures": failures,
            "deferred": _deferred(), "summary": summary}


def validate_installer_artifact(artifact: dict | None) -> dict:
    """Validate an installer artifact's structure against the
    checklist. Artifact fields: files (list of relpaths), version
    (str), manifest_version (str, optional), claims_signed (bool),
    claims_feed (bool). Returns {ok, defects, deferred, summary}.
    Validation only: it never compiles, signs, or hosts anything.
    A signed claim or a live-feed claim is a defect, because
    signing and feed hosting are DEFERRED — claiming them as done
    would be a false claim, not a pass."""
    artifact = artifact or {}
    defects = []
    files = artifact.get("files") or []
    for rel in REQUIRED_INSTALLER_FILES:
        if rel not in files:
            defects.append(f"missing installer file: {rel}")
    version = artifact.get("version")
    if not isinstance(version, str) or not _SEMVER.match(version):
        defects.append(f"version {version!r} is not in semver form")
    manifest_version = artifact.get("manifest_version")
    if manifest_version is not None and manifest_version != version:
        defects.append(
            f"version {version!r} does not match manifest version "
            f"{manifest_version!r}")
    if artifact.get("claims_signed"):
        defects.append(
            "artifact claims signing, but signing is DEFERRED — "
            "no signed claim is accepted here")
    if artifact.get("claims_feed"):
        defects.append(
            "artifact claims a live update feed, but feed hosting "
            "is DEFERRED — no feed claim is accepted here")
    ok = not defects
    summary = (
        "installer artifact validates against the checklist "
        "(structure only — compilation, signing, and feed "
        "hosting remain deferred)"
        if ok
        else "installer artifact refused: " + "; ".join(defects))
    return {"ok": ok, "defects": defects, "deferred": _deferred(),
            "summary": summary}
