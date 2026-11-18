"""Scan installed software for known vulnerabilities, secrets and
licences, and generate a package inventory — own code.

Parent-ruled (licence, 2026-11-11): aquasecurity/trivy (Apache-2.0,
LICENSE from main, 201 lines, read in full 2026-11-11) is
INSPIRATION ONLY. No code, binary, schema, SQL, text or package is
adopted from it; this module is written from scratch in stdlib
Python over the tree's own shapes (core/reach.py's gitleaks-ported
secret recognizers, core/extension_safety_harness.py's licence
allow-list). Ideas are not copyrighted, so no Apache-2.0 duty is
triggered by a from-scratch rebuild. The "Trivy" name and mark are
not used here: the tool is the generic phrase "security_scan", and
no package, fork, service or hosted offering is named after the
original. The NOTICE file was also read in full ("Trivy /
Copyright 2019-2020 Aqua Security Software Ltd. / This product
includes software developed by Aqua Security"); since no code is
adopted and the name is not used, no Apache-2.0 section 4(d)
attribution duty is triggered. The Go binary is NOT adopted and no Go runtime is added:
there is no second app, only this module inside VYUK.

What this is, and what it is not — one verdict per trivy scanner:
- CVE scan: BUILT. The owner's installed Python packages (the
  current interpreter's distributions, or the pins in a
  requirements.txt the user names) are checked against the OSV
  vulnerability database (https://api.osv.dev/v1/querybatch — a
  public, keyless API with no rate limits per its own docs; no
  account, no key, no paid feed). OSV aggregates NVD, GHSA, PyPI
  and other advisories and does the version matching server-side:
  this module sends (name, ecosystem PyPI, version) and reports
  exactly the vulnerabilities OSV returns for that version — no
  local version-range logic is invented. Each scan is live (no
  cached results are stored); a fetch failure is reported honestly
  as "could not reach the feed — nothing was scanned", never as a
  clean result. The live fetch could not be exercised from the
  lab: this VM's egress proxy stalls HTTP POSTs (GET works — the
  OSV host answers, and a real record was fetched over GET, see
  tests/test_trivy.py), so the first real fetch on a buyer's
  machine is a field leg. The request construction, response
  parsing and refusal paths are verified in the lab against a
  recorded real OSV record (GHSA-462w-v97r-4m45, Jinja2 sandbox
  escape, CVE-2019-10906) wrapped in the documented querybatch
  envelope, through an injectable transport.
- Secrets scan: BUILT, file-tree only. The tree already had the
  recognizers (core/reach.py detect_secrets — gitleaks-ported
  regex rules over TEXT, pilot-only, never a gate); what was
  missing was any tool that runs them over files. This action
  walks a user-named file or folder and reports masked findings
  only (detect_secrets masks every match); secret values are never
  logged, never returned, and never leave the machine — the action
  is local-only and makes no network call.
- Licence scan: BUILT, installed packages only. The tree already
  had a licence allow-list (core/extension_safety_harness.py
  audit_licence — exact-match, fail-closed); what was missing was
  any scan of installed packages against it. This action reads each
  distribution's declared licence metadata and runs it through
  that allow-list. Local-only.
- SBOM: BUILT, inventory only. The signed integrity manifest
  (core/health/integrity.py) lists FILES with hashes, not
  packages with versions and licences — it is not a dependency
  SBOM. This action generates a minimal CycloneDX 1.5 inventory
  (name, version, licence, purl) from the installed distributions
  and returns it as text; nothing is written unless the user saves
  it with an existing file tool. Local-only.
- Misconfiguration scan: NOT BUILT — no product surface. Trivy's
  misconfig scanner targets IaC (Terraform, Dockerfiles,
  Kubernetes manifests); VYUK runs no containers, no Kubernetes
  and no IaC, and the 14 tree hits for the misconfig words are all
  false positives (UI Automation "UiaCapture", a "Kubernetes"
  pronunciation entry, "terraforming" in creative word lists, a
  wormhole comment about relay URLs).
- Container image / Kubernetes targets: NOT BUILT — same reason;
  there is nothing to scan.

Classification: every action is a READ. The `vuln` action fetches
the OSV feed, so the tool is an EGRESS read (it is in
_READ_ONLY_TOOLS but deliberately NOT in _LOCAL_ONLY_READS — the
ollama_status precedent: a read that puts bytes on the wire gates
under taint). The other three actions are local-only, but the tool
as a whole cannot join _LOCAL_ONLY_READS because one action does
network I/O — fail-safe by construction.
"""

import hashlib
import importlib.metadata
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .extension_safety_harness import audit_licence
from .reach import detect_secrets

OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
_OSV_CHUNK = 100
_OSV_TIMEOUT_S = 30
_MAX_FILE_BYTES = 1_000_000
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".tox",
    ".eggs", "site-packages", "dist", "build", ".mypy_cache",
})
_KNOWN_SPDX = frozenset({
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "PSF-2.0", "CC0-1.0",
})

_ACTIONS = ("vuln", "secrets", "licenses", "sbom")


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def _version_key(version: str):
    """Tolerant version key for dedup: numeric runs compare as
    numbers, the rest as text. Only used to pick one row per
    distribution name; OSV does the real version matching."""
    parts = []
    for chunk in re.split(r"[.+_-]", version or ""):
        if not chunk:
            continue
        parts.append((0, int(chunk), "") if chunk.isdigit()
                     else (1, 0, chunk))
    return tuple(parts)


def _installed_distributions():
    """Installed distributions, one row per name (highest version
    wins on duplicates). Returns [{name, version, licence}]."""
    by_name = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata or {}).get("Name") or ""
        if not name:
            continue
        version = dist.version or ""
        key = _norm_name(name)
        if key not in by_name or _version_key(version) > _version_key(
                by_name[key]["version"]):
            by_name[key] = {
                "name": name.strip(),
                "version": version,
                "licence": _dist_licence(dist),
            }
    return [by_name[k] for k in sorted(by_name)]


def _dist_licence(dist) -> str:
    """Declared licence metadata, best-effort. Prefers the
    License-Expression, then the License field, then the first
    OSI-approved classifier. Empty string when undeclared."""
    meta = dist.metadata or {}
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    if lic and lic.upper() != "UNKNOWN":
        return lic
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved :: "):
            return classifier[len("License :: OSI Approved :: "):].strip()
    return ""


def _parse_requirements(text: str):
    """requirements.txt pins -> [(name, version)]. Only exact pins
    (name==version) are scannable — ranges, URLs and flags are
    reported back as skipped, never guessed."""
    pinned, skipped = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "--")):
            skipped.append((line, "option/flag line"))
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]*\])?)\s*==\s*"
                     r"([^\s;]+)", line)
        if m:
            pinned.append((m.group(1), m.group(2)))
        else:
            skipped.append((line, "not an exact == pin"))
    return pinned, skipped


# ---------------------------------------------------------------- OSV ---

def _osv_querybatch(queries, _opener=None):
    """POST one querybatch. Returns the parsed JSON dict, or raises
    the urllib error / ValueError on a bad response. The opener is
    injectable for tests (a fake returning recorded bytes); the
    default is urllib.request.urlopen."""
    body = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(
        OSV_QUERYBATCH_URL, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "VYUK-security-scan/1.0"},
        method="POST")
    opener = _opener or urllib.request.urlopen
    with opener(req, timeout=_OSV_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fixed_in(vuln: dict, package_name: str):
    """First fixed version for this package in the record's affected
    ranges, or None. The record is authoritative; nothing is
    inferred when the range is open-ended."""
    for aff in vuln.get("affected") or []:
        pkg = aff.get("package") or {}
        if _norm_name(pkg.get("name", "")) != _norm_name(package_name):
            continue
        if (pkg.get("ecosystem") or "") != "PyPI":
            continue
        for rng in aff.get("ranges") or []:
            for ev in rng.get("events") or []:
                if "fixed" in ev:
                    return ev["fixed"]
    return None


def _vuln_summary(vuln: dict, package_name: str) -> dict:
    aliases = vuln.get("aliases") or []
    cve = next((a for a in aliases if a.startswith("CVE-")), None)
    sev = vuln.get("severity") or []
    sev_txt = ""
    if sev:
        first = sev[0] or {}
        sev_txt = "%s %s" % (first.get("type", ""), first.get("score", ""))
        sev_txt = sev_txt.strip()
    return {
        "id": vuln.get("id", ""),
        "cve": cve or "",
        "summary": (vuln.get("summary") or "").strip(),
        "severity": sev_txt,
        "fixed": _fixed_in(vuln, package_name),
    }


def _scan_vulns(packages, _opener=None):
    """packages: [(name, version)]. Returns (findings, error).
    findings: [{name, version, vulns: [summary dicts]}] for packages
    with at least one vuln. error: a plain-language string when the
    feed could not be reached or answered — never a clean verdict."""
    queries = [{"package": {"name": n, "ecosystem": "PyPI"},
                "version": v} for n, v in packages]
    findings = []
    try:
        for i in range(0, len(queries), _OSV_CHUNK):
            chunk = queries[i:i + _OSV_CHUNK]
            data = _osv_querybatch(chunk, _opener=_opener)
            results = data.get("results") or []
            if len(results) != len(chunk):
                return None, ("the OSV feed answered with %d results "
                              "for %d packages — refusing to report a "
                              "partial scan as complete"
                              % (len(results), len(chunk)))
            for query, res in zip(chunk, results):
                name = query["package"]["name"]
                version = query["version"]
                vulns = [_vuln_summary(v, name)
                         for v in (res or {}).get("vulns") or []]
                vulns.sort(key=lambda v: v["id"])
                if vulns:
                    findings.append({"name": name, "version": version,
                                     "vulns": vulns})
    except urllib.error.URLError as exc:
        return None, ("could not reach the OSV vulnerability feed "
                      "(%s) — nothing was scanned, and this is not "
                      "a clean result" % (exc.reason or exc,))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return None, ("the OSV feed answered something unreadable "
                      "(%s) — nothing was scanned, and this is not "
                      "a clean result" % (exc,))
    return findings, None


# -------------------------------------------------------------- secrets ---

def _iter_text_files(root: Path):
    """Text files under root (a file, or a folder walked with the
    skip list). Binary files (undecodable as UTF-8) and files over
    the size cap are skipped, never read as text."""
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.stat().st_size > _MAX_FILE_BYTES:
            continue
        yield path


def _scan_secrets(root: Path):
    """Findings: [{file, line, type, preview}] — previews are
    masked by detect_secrets; no secret value is ever returned."""
    findings = []
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for finding in detect_secrets(text):
            line_no = text.count("\n", 0, finding["start"]) + 1
            findings.append({
                "file": str(path),
                "line": line_no,
                "type": finding["type"],
                "preview": finding["preview"],
            })
    return findings


# -------------------------------------------------------------- handler ---

def _usage() -> str:
    return ("security_scan — scan installed software for known "
            "vulnerabilities, secrets and licences, and generate a "
            "package inventory. Actions: 'vuln' checks installed "
            "Python packages against the OSV vulnerability feed "
            "(package + version check one package, or path=<a "
            "requirements.txt> to check its pins); 'secrets' scans "
            "a file or folder for leaked secrets (masked findings "
            "only, nothing leaves the machine); 'licenses' checks "
            "installed packages' licences against the allow-list; "
            "'sbom' returns a CycloneDX inventory of installed "
            "packages. Misconfiguration, container-image and "
            "Kubernetes scanning are not built: VYUK runs no "
            "containers, no Kubernetes and no IaC.")


def _limit(value, default: int) -> int:
    if value in ("", None):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


def security_scan(action: str = "", package: str = "",
                  version: str = "", path: str = "",
                  limit=None, _opener=None) -> str:
    """One tool, four reads. See the module docstring."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _usage()

    if action == "vuln":
        lim = _limit(limit, 10)
        if path:
            req_path = Path(path)
            if not req_path.is_file():
                return ("No requirements file at %r — name a "
                        "requirements.txt to check its pins, or leave "
                        "path out to check the installed packages."
                        % (path,))
            try:
                pinned, skipped = _parse_requirements(
                    req_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError) as exc:
                return "Could not read %r (%s)." % (path, exc)
            if not pinned:
                return ("No exact == pins in %r — nothing to check. "
                        "%d line(s) skipped (not exact pins)."
                        % (path, len(skipped)))
            packages = pinned
            scope = "pins in %r" % (path,)
        elif package:
            name = (package or "").strip()
            ver = (version or "").strip()
            if not ver:
                installed = {p["name"].lower(): p["version"]
                             for p in _installed_distributions()}
                ver = installed.get(_norm_name(name), "")
                if not ver:
                    return ("No version given for %r and it is not "
                            "installed — give a version to check."
                            % (name,))
            packages = [(name, ver)]
            scope = "%s %s" % (name, ver)
        else:
            dists = _installed_distributions()
            if not dists:
                return ("No installed Python packages found — "
                        "nothing to check.")
            packages = [(d["name"], d["version"]) for d in dists]
            scope = "%d installed packages" % len(packages)
        findings, error = _scan_vulns(packages, _opener=_opener)
        if error is not None:
            return error
        vulnerable = len(findings)
        total_vulns = sum(len(f["vulns"]) for f in findings)
        lines = ["Vulnerability scan (%s): %d package(s) checked, "
                 "%d vulnerable, %d clean, %d vulnerabilit(ies) "
                 "found." % (scope, len(packages), vulnerable,
                             len(packages) - vulnerable, total_vulns)]
        if not findings:
            lines.append("No known vulnerabilities in the checked "
                         "versions, per the OSV feed.")
            return "\n".join(lines)
        for f in findings:
            lines.append("%s %s — %d vulnerabilit(ies):"
                         % (f["name"], f["version"], len(f["vulns"])))
            shown = f["vulns"][:lim]
            for v in shown:
                bits = ["  %s" % v["id"]]
                if v["cve"]:
                    bits.append("(%s)" % v["cve"])
                if v["summary"]:
                    bits.append(v["summary"])
                if v["severity"]:
                    bits.append("[%s]" % v["severity"])
                if v["fixed"]:
                    bits.append("fixed in %s" % v["fixed"])
                lines.append(" ".join(bits))
            if len(f["vulns"]) > lim:
                lines.append("  … and %d more (raise limit to see "
                             "them)" % (len(f["vulns"]) - lim))
        return "\n".join(lines)

    if action == "secrets":
        lim = _limit(limit, 50)
        if not path:
            return ("Name a file or folder to scan for secrets "
                    "(path=...).")
        root = Path(path)
        if not root.exists():
            return "Nothing at %r — name a file or folder that exists." % (path,)
        findings = _scan_secrets(root)
        if not findings:
            return ("Secrets scan (%r): no secrets found in the "
                    "scanned text files." % (path,))
        lines = ["Secrets scan (%r): %d finding(s) — previews are "
                 "masked, no secret value is shown or stored."
                 % (path, len(findings))]
        for f in findings[:lim]:
            lines.append("  %s:%d %s %s"
                         % (f["file"], f["line"], f["type"], f["preview"]))
        if len(findings) > lim:
            lines.append("  … and %d more (raise limit to see them)"
                         % (len(findings) - lim))
        return "\n".join(lines)

    if action == "licenses":
        dists = _installed_distributions()
        if not dists:
            return "No installed Python packages found — nothing to check."
        ok, unknown, refused = [], [], []
        for d in dists:
            good, reason = audit_licence(d["licence"])
            row = "%s %s — %s (%s)" % (d["name"], d["version"],
                                       d["licence"] or "(no licence declared)",
                                       reason)
            (ok if good else refused if "refused" in reason
             else unknown).append(row)
        lines = ["Licence scan (%d installed packages): %d allowed, "
                 "%d unknown, %d refused."
                 % (len(dists), len(ok), len(unknown), len(refused))]
        for label, rows in (("REFUSED", refused), ("UNKNOWN", unknown)):
            if rows:
                lines.append("%s:" % label)
                lines.extend("  " + r for r in rows)
        return "\n".join(lines)

    # sbom
    dists = _installed_distributions()
    if not dists:
        return "No installed Python packages found — nothing to list."
    digest = hashlib.sha256(
        "\n".join("%s@%s" % (d["name"], d["version"])
                  for d in dists).encode("utf-8")).hexdigest()
    serial = ("urn:uuid:%s-%s-%s-%s-%s"
              % (digest[:8], digest[8:12], digest[12:16],
                 digest[16:20], digest[20:32]))
    components = []
    for d in dists:
        comp = {
            "type": "library",
            "bom-ref": "pkg:pypi/%s@%s"
                       % (_norm_name(d["name"]), d["version"]),
            "name": d["name"],
            "version": d["version"],
            "purl": "pkg:pypi/%s@%s"
                    % (_norm_name(d["name"]), d["version"]),
        }
        lic = d["licence"]
        if lic:
            if lic in _KNOWN_SPDX:
                comp["licenses"] = [{"license": {"id": lic}}]
            else:
                comp["licenses"] = [{"license": {"name": lic}}]
        components.append(comp)
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "components": components,
    }
    return json.dumps(bom, indent=2, sort_keys=True)
