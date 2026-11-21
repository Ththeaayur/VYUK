"""Round-3 section E, E-F203: Memory learned rows duplicate each
fact.

Section-E finding E-F203: Memory -> List -> every learned row
shows the display sentence plus a muted `key: value` sub-line
saying the same thing — "Your name is Aayur" / "name: Aayur",
"You prefer tea in the morning" / "morning drink: tea", 8/8
rows. Cause: the dedup guard in gui/window_memory_learned.py
`_learned_fact_row` (`pair.lower() not in display.lower()`)
fails on the `": "` separator, so the sub-line renders for
every fact.

The fix (E-F203, one product file, gui/window_memory_learned.py):
the sub-line is gone, not guarded. The display sentence IS the
fact — core.memory.learning.display_for derives it from the
stored pair and always carries the value verbatim (every branch
embeds it; the default branch IS "key: value"), so the pair can
only restate the display. No string guard can judge a
restatement: template keys are machine slugs
(likes_black_coffee_in), and values are paraphrased in the
display ("Mon/Wed/Fri" vs "Monday, Wednesday and Friday").
The raw key adds nothing a user can read, so the line goes
instead of being guarded. Nothing else changes: the display
label, the "• New" badge, the Forget button, the
Forget-everything control, list/search/filter/Forget controls,
the web-graph and the header count (E-F204 is a separate item)
are untouched.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen), gated the same way as the E-F202
suite (skipped on the sys interpreter, which has no PySide6).
One source pin runs on both interpreters: it reads the product
file as text and pins the sub-line out of `_learned_fact_row`,
so the duplication cannot come back silently.

Two seeds: the exact section-E 8 fixtures (hand-written
displays, the filed bug, via the slice-2 fake-learning seam)
and production-shaped facts (the REAL learning module over a
temp store, keys across every display_for branch — so the
"nothing lost" claim is proved at the production seam, where
the display is always derived from the pair).
"""
import ast
import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]

# The exact section-E seed (r3e_screens_work/slice2/driver.py
# _seed_learned): hand-written displays, raw key/value pairs.
FIXTURES = [
    {"id": 101, "display": "Your name is Aayur",
     "key": "name", "value": "Aayur"},
    {"id": 102, "display": "You prefer tea in the morning",
     "key": "morning drink", "value": "tea"},
    {"id": 103, "display": "You like your briefing in Hindi first",
     "key": "briefing language", "value": "Hindi first"},
    {"id": 104,
     "display": "Your MS101 group is O1 and the gripper uses a two-finger linkage",
     "key": "MS101 group", "value": "O1, two-finger linkage gripper"},
    {"id": 105, "display": "You go to the gym on Monday, Wednesday and Friday",
     "key": "gym days", "value": "Mon/Wed/Fri"},
    {"id": 106, "display": "You asked VYUK to keep the room at 24°C while working",
     "key": "room temperature", "value": "24°C working"},
    {"id": 107, "display": "Your favourite drink is masala tea with no sugar",
     "key": "favourite drink", "value": "masala tea, no sugar"},
    {"id": 108, "display": "You have a sister named Ananya whose birthday is 14 March",
     "key": "sister", "value": "Ananya, birthday 14 March"},
]

# Production-shaped keys: one per display_for branch, so the
# "display carries the value" invariant is proved at the real
# seam, not just on the fixtures.
PRODUCTION_KEYS = [
    ("name", "Aayur"),
    ("preferred_name", "Aayu"),
    ("identity", "student"),
    ("likes_black_coffee_in", "black coffee in the morning"),
    ("dislikes_crowded_trains", "crowded trains"),
    ("habits_early_morning", "always wake at 6"),
    ("note_reminder", "buy milk"),
    ("my_sister_name", "Ananya"),
    ("my_dog", "Rex"),
    ("morning drink", "tea"),
    ("gym days", "Mon/Wed/Fri"),
]


class _FakeLearning:
    """The slice-2 driver's seam: get_learned_facts returns the
    fixtures verbatim, displays included."""

    def __init__(self, facts):
        self._facts = list(facts)

    def get_learned_facts(self):
        return list(self._facts)

    def forget_learned_fact(self, fid):
        self._facts = [f for f in self._facts if f.get("id") != fid]
        return True

    def forget_all_learned(self):
        n = len(self._facts)
        self._facts = []
        return n


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


class TestNoPairSublineInSource(unittest.TestCase):
    """Source pin (both interpreters): _learned_fact_row builds
    exactly two labels — the display and the "• New" badge — and
    never mentions a raw pair."""

    def test_learned_fact_row_has_no_pair(self):
        path = ROOT / "gui" / "window_memory_learned.py"
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_learned_fact_row")
        seg = ast.get_source_segment(src, fn)
        # The sub-line construction is gone entirely: no QLabel
        # built from a pair, and only the display label and the
        # "• New" badge remain.
        self.assertNotIn("QLabel(pair)", seg)
        self.assertNotIn("sub = QLabel", seg)
        self.assertEqual(seg.count("QLabel("), 2)
        self.assertIn("E-F203", seg)


class _LearnedRowsBase(WindowBase):
    """Real MainWindow offscreen, Memory page on List view, the
    learned card driven with a chosen learning module."""

    def _show_learned(self, module):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay is best-effort
            pass
        _pump(self.app, 0.4)
        self.window._learning_module = lambda: module
        self.window._show_page(2)  # Memory page
        _pump(self.app, 0.5)
        self.window._set_memory_view("list")
        _pump(self.app, 0.5)
        self.window._store_learned_seen_ids([])
        self.window._refresh_learned_facts(mark_seen=False)
        _pump(self.app, 0.5)

    def _row_labels(self):
        from PySide6.QtWidgets import QLabel
        layout = getattr(self.window, "learned_layout", None)
        self.assertIsNotNone(layout, "learned_layout missing")
        rows = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            rows.append([lbl.text() for lbl in w.findChildren(QLabel)])
        return rows

    def _assert_once(self, rows, displays, pairs):
        flat = [t for row in rows for t in row]
        self.assertEqual(len(rows), len(displays),
                         f"expected {len(displays)} rows, got {rows}")
        for d in displays:
            self.assertEqual(flat.count(d), 1,
                             f"display {d!r} appears "
                             f"{flat.count(d)}x in {flat}")
        for p in pairs:
            # A default-branch display IS its pair ("morning
            # drink: tea") — then the single occurrence is the
            # display itself, not a sub-line. Any other pair must
            # not appear at all.
            expected = 1 if p in displays else 0
            self.assertEqual(flat.count(p), expected,
                             f"raw pair {p!r} appears "
                             f"{flat.count(p)}x in {flat}")


@unittest.skipUnless(_QT, "Qt-only (no PySide6 on sys)")
class TestFixtureFactsRenderOnce(_LearnedRowsBase):
    """The filed bug, exactly: the 8 section-E facts, each once,
    no raw pair, at every supported width."""

    def test_render_once_1280(self):
        self._show_learned(_FakeLearning(FIXTURES))
        self.window.resize(1280, 750)
        _pump(self.app, 0.6)
        self._assert_once(
            self._row_labels(),
            [f["display"] for f in FIXTURES],
            [f"{f['key']}: {f['value']}" for f in FIXTURES])

    def test_render_once_908(self):
        self._show_learned(_FakeLearning(FIXTURES))
        self.window.resize(908, 750)
        _pump(self.app, 0.6)
        self._assert_once(
            self._row_labels(),
            [f["display"] for f in FIXTURES],
            [f"{f['key']}: {f['value']}" for f in FIXTURES])

    def test_render_once_wide(self):
        self._show_learned(_FakeLearning(FIXTURES))
        for width in (1024, 1920):
            self.window.resize(width, 750)
            _pump(self.app, 0.6)
            self._assert_once(
                self._row_labels(),
                [f["display"] for f in FIXTURES],
                [f"{f['key']}: {f['value']}" for f in FIXTURES])

    def test_display_carries_full_content_and_wiring_survives(self):
        """Every display reads in full, and the row keeps its
        "• New" badge and Forget button — only the sub-line
        went, nothing else."""
        from PySide6.QtWidgets import QPushButton
        self._show_learned(_FakeLearning(FIXTURES))
        self.window.resize(1280, 750)
        _pump(self.app, 0.6)
        rows = self._row_labels()
        flat = [t for row in rows for t in row]
        for fact in FIXTURES:
            self.assertIn(fact["display"], flat)
        self.assertEqual(flat.count("• New"), len(FIXTURES))
        layout = self.window.learned_layout
        buttons = 0
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is not None:
                buttons += len([b for b in w.findChildren(QPushButton)
                                if b.text() == "Forget"])
        self.assertEqual(buttons, len(FIXTURES))

    def test_forget_still_works(self):
        """Forgetting one fact removes exactly that row; the
        rest still render once, with no pair."""
        from PySide6.QtWidgets import QPushButton
        fake = _FakeLearning(FIXTURES)
        self._show_learned(fake)
        self.window.resize(1280, 750)
        _pump(self.app, 0.6)
        layout = self.window.learned_layout
        first = layout.itemAt(0).widget()
        btn = next(b for b in first.findChildren(QPushButton)
                   if b.text() == "Forget")
        btn.click()
        _pump(self.app, 0.6)
        rest = [f for f in FIXTURES[1:]]
        self._assert_once(
            self._row_labels(),
            [f["display"] for f in rest],
            [f"{f['key']}: {f['value']}" for f in rest])


@unittest.skipUnless(_QT, "Qt-only (no PySide6 on sys)")
class TestProductionKeysRenderOnce(_LearnedRowsBase):
    """The production seam: the REAL learning module over a temp
    store, keys across every display_for branch. Each display
    carries its value verbatim and renders once; no raw pair
    ever renders — including the default-branch keys, whose
    display IS "key: value"."""

    def test_production_keys_render_once(self):
        from core.memory import learning as module
        from core.memory.learning import display_for
        orig_store = module.memory_store
        try:
            module.memory_store = self._store
            for key, value in PRODUCTION_KEYS:
                self.assertTrue(
                    self._store.save_fact("learned", key, value),
                    f"seed failed for {key!r}")
            self._show_learned(module)
            self.window.resize(1280, 750)
            _pump(self.app, 0.6)
            displays = [display_for(k, v) for k, v in PRODUCTION_KEYS]
            for (key, value), display in zip(PRODUCTION_KEYS, displays):
                self.assertIn(value, display,
                              f"display {display!r} drops value "
                              f"{value!r} for key {key!r}")
            self._assert_once(
                self._row_labels(),
                displays,
                [f"{k}: {v}" for k, v in PRODUCTION_KEYS])
        finally:
            module.memory_store = orig_store


if __name__ == "__main__":
    unittest.main()
