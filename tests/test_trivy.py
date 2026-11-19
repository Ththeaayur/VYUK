"""R17-2 trivy pack — suite.

Own stdlib security scanner over the tree's own shapes: installed
Python packages checked against the OSV vulnerability feed
(keyless, no account), a file-tree secrets scan reusing reach.py's
gitleaks-ported recognizers (masked findings only), an installed-
package licence scan against extension_safety_harness.py's
allow-list, and a CycloneDX inventory. No Go binary, no second
app, no container/Kubernetes/IaC surface. Every network test
drives an injected fake transport — the live OSV fetch could not
be exercised from this lab (this VM's egress proxy stalls HTTP
POSTs), so the first real fetch on a buyer's machine is a field
leg. The vulnerable-record fixture below is a REAL OSV record
(GHSA-462w-v97r-4m45, Jinja2 sandbox escape, CVE-2019-10906),
fetched 2026-11-11 over GET from api.osv.dev/v1/vulns/ and
embedded verbatim; only the references array was trimmed for
size (the parser never reads it). The misconfig, container-image
and Kubernetes halves are NOT built, so nothing here claims them.
"""

import io
import json
import pathlib
import tempfile
import unittest
import urllib.error

from core.catalogue_snapshot import GROUP_MEMBERS as SNAP_GROUPS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_gate_egress import (
    _LOCAL_ONLY_READS,
    _is_egress_read,
)
from core.funnel.tools_registry import tools as _registry
from core.security_scan import (
    _dist_licence,
    _fixed_in,
    _installed_distributions,
    _parse_requirements,
    _vuln_summary,
    security_scan,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAME = "security_scan"

# Real OSV record, fetched 2026-11-11 over GET from
# https://api.osv.dev/v1/vulns/GHSA-462w-v97r-4m45 (references
# trimmed for size; every field the parser reads is verbatim).
_REAL_OSV_JINJA2_JSON = r'''{"affected": [{"database_specific": {"source": "https://github.com/github/advisory-database/blob/main/advisories/github-reviewed/2019/04/GHSA-462w-v97r-4m45/GHSA-462w-v97r-4m45.json"}, "package": {"ecosystem": "PyPI", "name": "jinja2", "purl": "pkg:pypi/jinja2"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.10.1"}], "type": "ECOSYSTEM"}], "versions": ["2.0", "2.0rc1", "2.1", "2.1.1", "2.10", "2.2", "2.2.1", "2.3", "2.3.1", "2.4", "2.4.1", "2.5", "2.5.1", "2.5.2", "2.5.3", "2.5.4", "2.5.5", "2.6", "2.7", "2.7.1", "2.7.2", "2.7.3", "2.8", "2.8.1", "2.9", "2.9.1", "2.9.2", "2.9.3", "2.9.4", "2.9.5", "2.9.6"]}], "aliases": ["CVE-2019-10906", "PYSEC-2019-217"], "database_specific": {"cwe_ids": ["CWE-693"], "github_reviewed": true, "github_reviewed_at": "2020-06-16T20:57:35Z", "nvd_published_at": "2019-04-07T00:29:00Z", "severity": "HIGH"}, "details": "In Pallets Jinja before 2.10.1, `str.format_map` allows a sandbox escape.\n\nThe sandbox is used to restrict what code can be evaluated when rendering untrusted, user-provided templates. Due to the way string formatting works in Python, the `str.format_map` method could be used to escape the sandbox.\n\nThis issue was previously addressed for the `str.format` method in Jinja 2.8.1, which discusses the issue in detail. However, the less-common `str.format_map` method was overlooked. This release applies the same sandboxing to both methods.\n\nIf you cannot upgrade Jinja, you can override the `is_safe_attribute` method on the sandbox and explicitly disallow the `format_map` method on string objects.", "id": "GHSA-462w-v97r-4m45", "modified": "2024-09-24T21:03:59.802687Z", "published": "2019-04-10T14:30:24Z", "references": [{"type": "ADVISORY", "url": "https://nvd.nist.gov/vuln/detail/CVE-2019-10906"}, {"type": "WEB", "url": "https://usn.ubuntu.com/4011-2"}], "schema_version": "1.9.0", "severity": [{"score": "CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:N/A:N", "type": "CVSS_V3"}, {"score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:H/SI:N/SA:N", "type": "CVSS_V4"}], "summary": "Jinja2 sandbox escape via string formatting"}'''
_REAL_OSV_RECORD = json.loads(_REAL_OSV_JINJA2_JSON)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_opener_factory(mapping, record=None):
    """mapping: {(name, version): [vuln dicts]}. Records the last
    request (url, method, parsed body) on the returned callable."""
    calls = []

    def opener(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append({"url": req.full_url, "method": req.get_method(),
                      "body": body})
        results = []
        for q in body["queries"]:
            pkg = q["package"]
            key = (pkg["name"], q["version"])
            results.append({"vulns": mapping.get(key, [])})
        payload = json.dumps({"results": results}).encode("utf-8")
        return _FakeResponse(payload)

    opener.calls = calls
    return opener


class _FailingOpener:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, req, timeout=None):
        raise self.exc


class SecurityScanTestCase(unittest.TestCase):
    def test_usage_unknown_action(self):
        out = security_scan(action="explode")
        self.assertIn("security_scan", out)
        self.assertIn("'vuln'", out)

    def test_vuln_single_vulnerable_real_record(self):
        opener = _fake_opener_factory(
            {("jinja2", "2.4.1"): [_REAL_OSV_RECORD]})
        out = security_scan(action="vuln", package="jinja2",
                            version="2.4.1", _opener=opener)
        self.assertIn("GHSA-462w-v97r-4m45", out)
        self.assertIn("CVE-2019-10906", out)
        self.assertIn("fixed in 2.10.1", out)
        self.assertIn("Jinja2 sandbox escape", out)
        self.assertIn("1 vulnerabilit", out)
        # Request construction: POST to the OSV batch URL, PyPI
        # ecosystem, exact package + version.
        self.assertEqual(len(opener.calls), 1)
        call = opener.calls[0]
        self.assertEqual(call["url"],
                         "https://api.osv.dev/v1/querybatch")
        self.assertEqual(call["method"], "POST")
        q = call["body"]["queries"][0]
        self.assertEqual(q["package"],
                         {"name": "jinja2", "ecosystem": "PyPI"})
        self.assertEqual(q["version"], "2.4.1")

    def test_vuln_single_clean(self):
        opener = _fake_opener_factory({})
        out = security_scan(action="vuln", package="requests",
                            version="2.32.0", _opener=opener)
        self.assertIn("0 vulnerable", out)
        self.assertIn("No known vulnerabilities", out)

    def test_vuln_fetch_failure_is_not_clean(self):
        opener = _FailingOpener(
            urllib.error.URLError("connection refused"))
        out = security_scan(action="vuln", package="requests",
                            version="2.32.0", _opener=opener)
        self.assertIn("could not reach", out)
        self.assertIn("not a clean result", out)
        self.assertNotIn("No known vulnerabilities", out)

    def test_vuln_bad_json_is_not_clean(self):
        def bad_opener(req, timeout=None):
            return _FakeResponse(b"this is not json")
        out = security_scan(action="vuln", package="requests",
                            version="2.32.0", _opener=bad_opener)
        self.assertIn("unreadable", out)
        self.assertNotIn("No known vulnerabilities", out)

    def test_vuln_result_count_mismatch_refused(self):
        def short_opener(req, timeout=None):
            return _FakeResponse(json.dumps({"results": []})
                                 .encode("utf-8"))
        out = security_scan(action="vuln", package="requests",
                            version="2.32.0", _opener=short_opener)
        self.assertIn("refusing to report a partial scan", out)
        self.assertNotIn("No known vulnerabilities", out)

    def test_vuln_uninstalled_no_version(self):
        out = security_scan(action="vuln",
                            package="definitely-not-a-real-package-xyz")
        self.assertIn("No version given", out)
        self.assertIn("not installed", out)

    def test_vuln_requirements_pins(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        req = pathlib.Path(tmp.name) / "requirements.txt"
        req.write_text("# comment\njinja2==2.4.1\nrequests==2.32.0\n"
                       "flask>=3.0\n", encoding="utf-8")
        opener = _fake_opener_factory(
            {("jinja2", "2.4.1"): [_REAL_OSV_RECORD]})
        out = security_scan(action="vuln", path=str(req),
                            _opener=opener)
        self.assertIn("GHSA-462w-v97r-4m45", out)
        self.assertIn("2 package(s) checked", out)
        self.assertIn("1 vulnerable, 1 clean", out)
        queried = {(q["package"]["name"], q["version"])
                   for q in opener.calls[0]["body"]["queries"]}
        self.assertEqual(queried,
                         {("jinja2", "2.4.1"), ("requests", "2.32.0")})

    def test_vuln_requirements_no_pins(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        req = pathlib.Path(tmp.name) / "requirements.txt"
        req.write_text("# only comments\nflask>=3.0\n", encoding="utf-8")
        out = security_scan(action="vuln", path=str(req),
                            _opener=_fake_opener_factory({}))
        self.assertIn("No exact == pins", out)

    def test_vuln_requirements_missing(self):
        out = security_scan(action="vuln", path="/no/such/req.txt",
                            _opener=_fake_opener_factory({}))
        self.assertIn("No requirements file", out)

    def test_parse_requirements(self):
        pinned, skipped = _parse_requirements(
            "# c\nrequests==2.32.0  # trailing\n"
            "jinja2==2.4.1\nflask>=3.0\n-r other.txt\n")
        self.assertEqual(pinned,
                         [("requests", "2.32.0"), ("jinja2", "2.4.1")])
        self.assertEqual(len(skipped), 2)

    def test_fixed_in_and_summary_on_real_record(self):
        self.assertEqual(_fixed_in(_REAL_OSV_RECORD, "jinja2"), "2.10.1")
        self.assertIsNone(_fixed_in(_REAL_OSV_RECORD, "requests"))
        s = _vuln_summary(_REAL_OSV_RECORD, "jinja2")
        self.assertEqual(s["id"], "GHSA-462w-v97r-4m45")
        self.assertEqual(s["cve"], "CVE-2019-10906")
        self.assertEqual(s["fixed"], "2.10.1")
        self.assertIn("CVSS_V3", s["severity"])
        self.assertIn("CVSS:3.0/", s["severity"])

    def test_secrets_finds_planted_masked(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "clean.txt").write_text("hello world\n", encoding="utf-8")
        secret = "AKIAIOSFODNN7EXAMPLE"
        token = "ghp_" + "a" * 24
        (root / "leak.txt").write_text(
            f"key = \"{secret}\"\ntoken = \"{token}\"\n", encoding="utf-8")
        out = security_scan(action="secrets", path=str(root))
        self.assertIn("2 finding(s)", out)
        self.assertIn("AWS_ACCESS_KEY_ID", out)
        self.assertIn("GITHUB_PAT", out)
        self.assertNotIn(secret, out)
        self.assertNotIn(token, out)
        self.assertIn("***", out)

    def test_secrets_clean_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "a.txt").write_text("nothing here\n", encoding="utf-8")
        out = security_scan(action="secrets", path=str(root))
        self.assertIn("no secrets found", out)

    def test_secrets_missing_path(self):
        out = security_scan(action="secrets", path="/no/such/dir")
        self.assertIn("Nothing at", out)

    def test_secrets_skips_binary(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        (root / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe" * 100)
        out = security_scan(action="secrets", path=str(root))
        self.assertIn("no secrets found", out)

    def test_licenses_shape(self):
        dists = _installed_distributions()
        self.assertGreater(len(dists), 0)
        out = security_scan(action="licenses")
        self.assertIn("Licence scan (%d installed packages)"
                      % len(dists), out)
        m = io.StringIO(out)
        header = m.readline()
        import re as _re
        nums = _re.search(r"(\d+) allowed, (\d+) unknown, "
                          r"(\d+) refused", header)
        self.assertIsNotNone(nums, header)
        total = sum(int(n) for n in nums.groups())
        self.assertEqual(total, len(dists))

    def test_dist_licence_prefers_expression(self):
        class FakeMeta(dict):
            def get_all(self, key):
                return {"Classifier": [
                    "License :: OSI Approved :: MIT License"]}.get(key, [])

        class FakeDist:
            version = "1.0"
            metadata = FakeMeta({
                "Name": "x", "License-Expression": "MIT",
                "License": "GPL-3.0"})

        self.assertEqual(_dist_licence(FakeDist()), "MIT")

    def test_sbom_shape(self):
        dists = _installed_distributions()
        out = security_scan(action="sbom")
        bom = json.loads(out)
        self.assertEqual(bom["bomFormat"], "CycloneDX")
        self.assertEqual(bom["specVersion"], "1.5")
        self.assertTrue(bom["serialNumber"].startswith("urn:uuid:"))
        self.assertEqual(len(bom["components"]), len(dists))
        for comp in bom["components"]:
            self.assertEqual(comp["type"], "library")
            self.assertTrue(comp["purl"].startswith("pkg:pypi/"))
            self.assertEqual(comp["bom-ref"], comp["purl"])
            self.assertIn("name", comp)
            self.assertIn("version", comp)

    def test_registry(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 757)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        mine = [d for d in decls if d["name"] == NAME]
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(mine[0]["parameters"]["properties"]), 5)
        total_params = sum(len(d.get("parameters", {})
                              .get("properties", {})) for d in decls)
        self.assertEqual(total_params, 2079)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_gates(self):
        self.assertIn(NAME, _READ_ONLY_TOOLS)
        self.assertNotIn(NAME, _CONFIRM_GATE_TOOLS)
        self.assertNotIn(NAME, _FREE_BY_DESIGN)
        self.assertTrue(_is_egress_read(NAME))
        self.assertNotIn(NAME, _LOCAL_ONLY_READS)

    def test_catalogue(self):
        self.assertEqual(len(SNAP_GROUPS), 11)
        group = next(g for g, m in SNAP_GROUPS.items() if NAME in m)
        self.assertEqual(group, "System & apps")
        # The catalogue lives in core/catalogue_groups_data.py now
        # (GAPS-23 F-V7); checked by text: the name must sit inside
        # the "System & apps" tuple, not any other group.
        import re as _re
        gui_text = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        m = _re.search(r'"System & apps": \((.*?)\)\s*,',
                       gui_text, _re.S)
        self.assertIsNotNone(m, "System & apps tuple not found")
        self.assertIn('"security_scan"', m.group(1))
        # 11 groups total (GROUP_ORDER is the authoritative list).
        order = _re.search(r"GROUP_ORDER = \[(.*?)\]", gui_text, _re.S)
        self.assertIsNotNone(order, "GROUP_ORDER not found")
        self.assertEqual(len(_re.findall(r'"[^"]+"', order.group(1))),
                         11)
        self.assertEqual(gui_text.count('"security_scan"'), 1)

    def test_snapshot_entry(self):
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(len(snap), 757)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        self.assertEqual(snap[NAME], {"confirm": False, "free": None,
                                      "read": True})


if __name__ == "__main__":
    unittest.main()
