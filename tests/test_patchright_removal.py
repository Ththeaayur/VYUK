"""GAPS-1 (BLOCKS #2) patchright removal — suite.

The removed engine must be gone from every .py file, from
requirements.txt / requirements.lock / THIRD_PARTY_LICENSES.md,
and the stock Playwright engine must be the live one in the
worker and the wrapper. Runs against a FAKE Playwright backend
(no real Chrome, no Playwright on the system/Qt interpreters).

SELF-REFERENCE: this file is the ONE .py file allowed to name
the removed engine — it is the guard that enforces the absence.
Its walk skips itself by resolved path and asserts exactly one
skip (itself). Historical mentions live only in ARCHITECTURE.md,
TOOLS_LOOPHOLES_AUDIT.md (docs) and install_manifest.json (the
frozen v6.0.0 release record).
"""

import importlib.util
import os
import pathlib
import re
import unittest

from core.desktop import browser_automation as ba
from core.desktop import browser_session as bs

ROOT = pathlib.Path(__file__).resolve().parent.parent
NEEDLE = "patchright"
SEAM_SYMBOLS = (
    "sync_playwright_factory", "browser_patchright_seam",
    "ABObservation", "ABResult", "run_ab", "ab_verdict",
    "may_activate", "patchright_enabled", "patchright_available",
    "PATCHRIGHT_ENV", "VYUK_BROWSER_PATCHRIGHT",
    "PATCHRIGHT_GATE_OWED", "patchright_status",
)
HISTORICAL_ALLOWED = {"ARCHITECTURE.md", "TOOLS_LOOPHOLES_AUDIT.md",
                      "install_manifest.json"}
SELF = pathlib.Path(__file__).resolve()


def _py_files():
    out = []
    skipped_self = 0
    for root in ("core", "gui", "tests", "plugins"):
        base = ROOT / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                path = pathlib.Path(dirpath, fn).resolve()
                if path == SELF:
                    skipped_self += 1
                    continue
                out.append(path)
    return sorted(out), skipped_self


class _FakePage:
    def __init__(self, url, title, text=""):
        self.url, self.title, self.text = url, title, text
        self.loading = False
        self.elements = {}


class _FakeBackend:
    def __init__(self):
        self.started = False
        self.pages_list = []
        self.closed = 0

    def start(self, profile_dir):
        self.started = True
        self.pages_list = [_FakePage("about:blank", "", "")]

    def pages(self):
        return [{"handle": p, "url": p.url, "title": p.title}
                for p in self.pages_list]

    def load_state(self, handle):
        return "loaded"

    def navigate(self, handle, url):
        handle.url = url
        handle.title = "Example Domain"
        handle.text = "Example page text."

    def matches(self, handle, selector):
        return []

    def element_identity(self, el):
        return {"tag": "button", "text": "", "role": ""}

    def click_element(self, el):
        return {"changed": False, "detail": "nothing"}

    def field_type(self, el):
        return "text"

    def field_label(self, el):
        return ""

    def fill_element(self, el, text):
        pass

    def field_value(self, el):
        return ""

    def page_text(self, handle):
        return handle.text

    def page_url(self, handle):
        return handle.url

    def page_title(self, handle):
        return handle.title

    def close(self):
        self.closed += 1
        self.started = False


class TestRemoval(unittest.TestCase):
    def test_seam_file_deleted_and_unimportable(self):
        self.assertFalse(
            (ROOT / "core" / "desktop"
             / "browser_patchright_seam.py").exists())
        self.assertIsNone(importlib.util.find_spec(
            "core.desktop.browser_patchright_seam"))

    def test_zero_needle_in_py_files_except_self(self):
        files, skipped_self = _py_files()
        self.assertEqual(skipped_self, 1,
                         "the walk must skip exactly itself")
        bad = [str(p.relative_to(ROOT)) for p in files
               if NEEDLE in p.read_text(
                   encoding="utf-8", errors="replace").lower()]
        self.assertEqual(bad, [], bad)

    def test_zero_seam_symbols_in_py_files_except_self(self):
        files, _ = _py_files()
        sym_re = re.compile(r"\b(" + "|".join(
            re.escape(s) for s in SEAM_SYMBOLS) + r")\b")
        bad = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = sorted(set(sym_re.findall(text)))
            if hits:
                bad.append("%s: %s" % (
                    path.relative_to(ROOT), ",".join(hits)))
        self.assertEqual(bad, [], bad)

    def test_zero_needle_in_declarations(self):
        for fname in ("requirements.txt", "requirements.lock",
                      "THIRD_PARTY_LICENSES.md"):
            text = (ROOT / fname).read_text(encoding="utf-8")
            self.assertNotIn(NEEDLE, text.lower(), fname)

    def test_historical_set_is_exactly_the_three_files(self):
        hist = set()
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "data", ".git")]
            for fn in filenames:
                path = pathlib.Path(dirpath, fn)
                rel = str(path.relative_to(ROOT))
                if rel in ("requirements.txt", "requirements.lock",
                           "THIRD_PARTY_LICENSES.md"):
                    continue
                if rel.endswith(".py"):
                    continue
                if NEEDLE in path.read_text(
                        encoding="utf-8", errors="replace").lower():
                    hist.add(rel)
        self.assertEqual(hist, HISTORICAL_ALLOWED, sorted(hist))

    def test_playwright_declared_everywhere(self):
        self.assertIn("playwright>=1.64.0",
                      (ROOT / "requirements.txt").read_text(
                          encoding="utf-8"))
        self.assertIn("playwright==1.64.0",
                      (ROOT / "requirements.lock").read_text(
                          encoding="utf-8"))
        self.assertIn("| playwright |",
                      (ROOT / "THIRD_PARTY_LICENSES.md").read_text(
                          encoding="utf-8"))


class TestEngineIsPlaywright(unittest.TestCase):
    def test_engine_constant(self):
        self.assertEqual(ba.ENGINE, "playwright")

    def test_engine_available_matches_probe(self):
        really_there = importlib.util.find_spec("playwright") is not None
        self.assertEqual(ba.engine_available(), really_there)

    def test_engine_status_names_playwright(self):
        status = ba.engine_status()
        self.assertEqual(status["engine"], "playwright")
        if importlib.util.find_spec("playwright") is None:
            self.assertFalse(status["available"])
            self.assertIn("engine_unavailable", status["reason"])
        else:
            self.assertTrue(status["available"])
            self.assertIsNotNone(status["version"])

    def test_engine_start_lives_in_browser_engine(self):
        # GAPS-18: the engine starts in exactly one place —
        # browser_engine.start_playwright. The session (_start_pw)
        # and the automation backend are its two callers; neither
        # starts the engine itself any more.
        src = (ROOT / "core" / "desktop" / "browser_engine.py"
               ).read_text(encoding="utf-8")
        self.assertIn(
            "from playwright.sync_api import sync_playwright", src)
        self.assertIn("return sync_playwright().start()", src)
        for path in ("core/desktop/browser_session.py",
                     "core/desktop/browser_automation.py"):
            caller = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("sync_playwright().start()", caller)
            self.assertIn("start_playwright", caller)

    def test_start_pw_behaviour(self):
        if importlib.util.find_spec("playwright") is None:
            with self.assertRaises(ImportError) as ctx:
                bs._start_pw()
            self.assertIn("playwright", str(ctx.exception).lower())
            self.assertNotIn(NEEDLE, str(ctx.exception).lower())
        else:
            pw = bs._start_pw()
            try:
                self.assertTrue(hasattr(pw, "chromium"))
            finally:
                pw.stop()

    def test_launch_helper_discipline(self):
        class _FakeChromium:
            def __init__(self):
                self.calls = []

            def launch_persistent_context(self, profile_dir, **kw):
                self.calls.append((profile_dir, kw))
                return "CTX"

        fake = _FakeChromium()
        out = ba._launch_persistent_context(
            fake, "/tmp/prof", channel="chrome", headless=False)
        self.assertEqual(out, "CTX")
        self.assertEqual(fake.calls[0][1].get("channel"), "chrome")
        with self.assertRaises(ValueError):
            ba._launch_persistent_context(fake, "/tmp/prof",
                                          channel="firefox")
        with self.assertRaises(ValueError):
            ba._launch_persistent_context(fake, "")

    def test_docstrings_state_the_truth(self):
        self.assertIn("GAPS-1", ba.__doc__ or "")
        self.assertIn("stock Playwright in every live", ba.__doc__ or "")
        self.assertNotIn("NO patchright engine in any live path",
                         ba.__doc__ or "")
        self.assertIn("GAPS-1", bs._start_pw.__doc__ or "")
        self.assertIn("stock Playwright", bs._start_pw.__doc__ or "")


class TestDispatch(unittest.TestCase):
    def test_fake_backend_round_trip_names_playwright(self):
        ba.set_automation_backend(_FakeBackend())
        self.addCleanup(ba.reset_automation_backend)
        launched = ba.launch()
        self.assertTrue(launched["ok"], launched)
        self.assertEqual(launched["engine"], "playwright")
        tabs = ba.list_tabs()
        self.assertTrue(tabs["ok"], tabs)
        page = tabs["tabs"][0]
        nav = ba.navigate(page, "https://example.com/")
        self.assertTrue(nav["ok"], nav)
        self.assertEqual(nav["page"]["url"], "https://example.com/")
        read = ba.read(nav["page"])
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["text"], "Example page text.")
        closed = ba.close()
        self.assertTrue(closed["ok"], closed)

    def test_census_unchanged(self):
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 764)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(set(names)), 764)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        params = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(params, 2102)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 763)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 761)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 169)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169


if __name__ == "__main__":
    unittest.main()
