"""GAPS-18 (review flaws D-4 + D-5, §6.6: one browser engine).

The browser layer is one shared engine interface now: a single
sync_playwright start call (browser_engine.start_playwright), a
registry naming both engines (Playwright + the pilot-only Camoufox
lane), leaves that import only the session names they use, and
session state reached only through accessors. No real browser, no
Playwright, no real waiting: the engine tests use spies and fakes,
the state tests seed a fake context into the in-memory _state and
restore it.

Runs on both interpreters (system python3 and the Qt venv).
"""
import ast
import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

from core.desktop import browser_automation as ba  # noqa: E402
from core.desktop import browser_engine as be  # noqa: E402
from core.desktop import browser_session as bs  # noqa: E402

LEAVES = ("browser_click", "browser_elements", "browser_interact",
          "browser_nav", "browser_playback", "browser_read")
EXPECTED_IMPORTS = {
    "browser_click": {"_ELEMENT_TIMEOUT_MS", "_active_page", "_need_connection",
                      "_page_label", "_settle", "_short", "_read_snapshot"},
    "browser_elements": {"_page_label", "_next_snapshot_seq", "_store_snapshot"},
    "browser_interact": {"_ELEMENT_TIMEOUT_MS", "_active_page", "_need_connection",
                         "_page_label", "_settle", "_short"},
    "browser_nav": {"_GOTO_TIMEOUT_MS", "_active_page", "_need_connection",
                    "_page_label", "_settle", "_short", "_tracked_page",
                    "_set_active_page", "_context_pages", "_new_context_page"},
    "browser_playback": {"_active_page", "_log_browser_event", "_need_connection",
                         "_submit"},
    "browser_read": {"_ELEMENT_TIMEOUT_MS", "_active_page", "_need_connection",
                     "_page_label", "_short"},
}


def _session_import_names(leaf):
    src = (ROOT / "core" / "desktop" / f"{leaf}.py").read_text(
        encoding="utf-8")
    m = re.search(r"from \.browser_session import \((.*?)\)", src, re.S)
    return {n.strip() for n in m.group(1).split(",") if n.strip()}


def _used_names(leaf):
    src = (ROOT / "core" / "desktop" / f"{leaf}.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        if isinstance(node, ast.Global):
            used.update(node.names)
    return used


class _FakePage:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage("a"), _FakePage("b")]
        self.made = []

    def new_page(self):
        made = _FakePage("new")
        self.made.append(made)
        self.pages.append(made)
        return made


class _FakeChromium:
    def launch_persistent_context(self, profile_dir, **kw):
        return _FakeContext()


class _FakePw:
    chromium = _FakeChromium()


class TestOneStartSite(unittest.TestCase):
    def test_exactly_one_start_call_site(self):
        sites = []
        for p in (ROOT / "core").rglob("*.py"):
            for i, line in enumerate(
                    p.read_text(errors="replace").splitlines(), 1):
                if re.search(r"sync_playwright\(\)\.start\(\)", line):
                    sites.append(f"{p.relative_to(ROOT)}:{i}")
        self.assertEqual(len(sites), 1, str(sites))
        self.assertIn("browser_engine", sites[0])

    def test_start_pw_source_delegates(self):
        src = (ROOT / "core" / "desktop" / "browser_session.py").read_text(
            encoding="utf-8")
        fn = src.split("def _start_pw")[1].split("\ndef ", 1)[0]
        self.assertIn("return start_playwright()", fn)
        self.assertNotIn("sync_playwright", fn)

    def test_backend_source_delegates(self):
        src = (ROOT / "core" / "desktop" / "browser_automation.py").read_text(
            encoding="utf-8")
        self.assertIn("_engine.start_playwright()", src)
        self.assertNotIn("sync_playwright().start()", src)


class TestEngineRegistry(unittest.TestCase):
    def test_registry_names_both_engines(self):
        self.assertEqual(sorted(be.ENGINES), ["camoufox", "playwright"])

    def test_playwright_start_is_the_one_start(self):
        self.assertIs(be.ENGINES["playwright"]["start"], be.start_playwright)
        self.assertEqual(be.ENGINES["playwright"]["kind"], "engine")

    def test_camoufox_is_a_lane_with_no_start(self):
        lane = be.ENGINES["camoufox"]
        self.assertEqual(lane["kind"], "lane")
        self.assertIsNone(lane["start"])
        self.assertIsNone(lane["channel"])

    def test_unknown_engine_raises_naming_known_engines(self):
        with self.assertRaises(KeyError) as ctx:
            be.get_engine("nope")
        self.assertIn("camoufox", str(ctx.exception))
        self.assertIn("playwright", str(ctx.exception))

    def test_camoufox_start_refuses(self):
        with self.assertRaises(RuntimeError) as ctx:
            be.start_engine("camoufox")
        self.assertIn("pilot-only", str(ctx.exception))

    def test_lane_options_through_the_interface(self):
        opts = be.camoufox_lane_options(
            ROOT / "data" / "camoufox_lane_profile")
        self.assertEqual(opts["lane"], "camoufox")
        self.assertFalse(opts["geoip"])
        self.assertFalse(opts["is_default_path"])

    def test_lane_options_refuse_chrome_profile(self):
        with self.assertRaises(ValueError):
            be.camoufox_lane_options(ROOT / "data" / "browser_profile")

    def test_lane_options_refuse_geoip(self):
        with self.assertRaises(ValueError) as ctx:
            be.camoufox_lane_options(
                ROOT / "data" / "camoufox_lane_profile", geoip=True)
        self.assertIn("permanently OFF", str(ctx.exception))


class TestDelegation(unittest.TestCase):
    def test_start_pw_calls_the_one_start(self):
        calls = []
        orig = bs.start_playwright
        bs.start_playwright = lambda: calls.append("start")
        self.addCleanup(lambda: setattr(bs, "start_playwright", orig))
        bs._start_pw()
        self.assertEqual(calls, ["start"])

    def test_backend_start_calls_the_one_start(self):
        calls = []
        orig_start = be.start_playwright
        orig_avail = ba.engine_available
        be.start_playwright = lambda: calls.append("start") or _FakePw()
        ba.engine_available = lambda: True
        self.addCleanup(lambda: setattr(be, "start_playwright", orig_start))
        self.addCleanup(lambda: setattr(ba, "engine_available", orig_avail))
        backend = ba._PlaywrightBackend()
        backend.start("/tmp/gaps18-prof")
        self.assertEqual(calls, ["start"])
        self.assertIsInstance(backend._pw, _FakePw)

    def test_start_playwright_absent_raises_import_error(self):
        if importlib.util.find_spec("playwright") is None:
            with self.assertRaises(ImportError) as ctx:
                be.start_playwright()
            self.assertIn("playwright", str(ctx.exception).lower())
        else:
            pw = be.start_playwright()
            try:
                self.assertTrue(hasattr(pw, "chromium"))
            finally:
                pw.stop()


class TestEngineProbe(unittest.TestCase):
    def test_engine_available_matches_reality(self):
        really_there = importlib.util.find_spec("playwright") is not None
        self.assertEqual(be.engine_available(), really_there)
        self.assertEqual(ba.engine_available(), really_there)

    def test_engine_status_names_playwright(self):
        status = be.engine_status()
        self.assertEqual(status["engine"], "playwright")
        if importlib.util.find_spec("playwright") is None:
            self.assertFalse(status["available"])
            self.assertIn("engine_unavailable", status["reason"])
        else:
            self.assertTrue(status["available"])
            self.assertIsNotNone(status["version"])
        # The automation module's probe delegates to the same report.
        self.assertEqual(ba.engine_status(), status)


class TestLeafImports(unittest.TestCase):
    def test_each_leaf_imports_exactly_its_used_names(self):
        for leaf, want in EXPECTED_IMPORTS.items():
            with self.subTest(leaf=leaf):
                self.assertEqual(_session_import_names(leaf), want)

    def test_no_unused_session_import_in_any_leaf(self):
        for leaf in LEAVES:
            with self.subTest(leaf=leaf):
                got = _session_import_names(leaf)
                used = _used_names(leaf)
                self.assertEqual(got - used, set())

    def test_no_direct_state_pokes_in_leaves(self):
        for leaf in LEAVES:
            src = (ROOT / "core" / "desktop" / f"{leaf}.py").read_text(
                encoding="utf-8")
            sites = [i + 1 for i, line in enumerate(src.splitlines(), 1)
                     if re.search(r"_state(?:\[|\.get\()", line)
                     and "from .browser_session import" not in line]
            self.assertEqual(sites, [], f"{leaf}: {sites}")


class TestStateAccessors(unittest.TestCase):
    def setUp(self):
        self._saved = {k: bs._state.get(k) for k in
                       ("snapshot", "snap_seq", "context", "page")}
        self.addCleanup(self._restore)

    def _restore(self):
        bs._state.update(self._saved)

    def test_snapshot_seq_increments(self):
        bs._state["snap_seq"] = 0
        self.assertEqual(bs._next_snapshot_seq(), 1)
        self.assertEqual(bs._next_snapshot_seq(), 2)
        self.assertEqual(bs._state["snap_seq"], 2)

    def test_snapshot_store_read_round_trip(self):
        snap = {"url": "u", "title": "t", "seq": 1, "refs": {"e1": {}}}
        bs._store_snapshot(snap)
        self.assertEqual(bs._read_snapshot(), snap)

    def test_tracked_page_round_trip(self):
        bs._set_active_page("PAGE")
        self.assertEqual(bs._tracked_page(), "PAGE")
        bs._set_active_page(None)
        self.assertIsNone(bs._tracked_page())

    def test_context_pages_and_new_page(self):
        ctx = _FakeContext()
        bs._state["context"] = ctx
        self.assertIs(bs._context_pages(), ctx.pages)
        made = bs._new_context_page()
        self.assertIs(made, ctx.made[0])
        self.assertIs(bs._tracked_page(), made)


class TestSessionShape(unittest.TestCase):
    def test_cdp_import_trimmed_to_used_names(self):
        src = (ROOT / "core" / "desktop" / "browser_session.py").read_text(
            encoding="utf-8")
        m = re.search(r"from \.browser_cdp import \((.*?)\)", src, re.S)
        got = {n.strip() for n in m.group(1).split(",") if n.strip()}
        tree = ast.parse(src)
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            if isinstance(node, ast.Global):
                used.update(node.names)
        self.assertEqual(len(got), 9)
        self.assertEqual(got - used, set())

    def test_browsers_dir_single_source(self):
        self.assertEqual(bs._BROWSERS_DIR, be.browsers_dir())

    def test_start_pw_docstring_keeps_gaps1_pin(self):
        doc = bs._start_pw.__doc__ or ""
        self.assertIn("GAPS-1", doc)
        self.assertIn("stock Playwright", doc)
        self.assertNotIn("ONLY live engine start", doc)

    def test_automation_docstring_keeps_gaps1_pin(self):
        doc = ba.__doc__ or ""
        self.assertIn("GAPS-1", doc)
        self.assertIn("stock Playwright in every live", doc)

    def test_browser_reexport_still_works(self):
        import core.browser as bmod
        self.assertIs(bmod._start_pw, bs._start_pw)
        self.assertIs(bmod._state, bs._state)


class TestCensus(unittest.TestCase):
    def test_registry_total_unchanged(self):
        from core.funnel.tools_registry import tools
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
