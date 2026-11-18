"""Command Deck guards (Phase A, guide §11.1/§11.2).

Two allowlists, both of which only shrink — except for the one
explicit, reviewed Phase B re-pin (guide D1), which re-pinned
gui/style.py, gui/visualizer.py, gui/mini_orb.py and
gui/window_settings.py to the Command Deck palette and updated
their entries to the new literal sets (317 -> 338 pairs; no
other file changed). After that re-pin, both shrink again:

* Colour literals: every hex / rgb() / rgba() literal in gui/**/*.py,
  outside the token source, must be in ALLOWLIST_COLOURS. New literals
  fail. Migrating a file to tokens removes its entries.
  gui/design_system/ is scanned with an EMPTY allowlist — new
  design-system code must be literal-free from birth. (The two HTML
  templates this used to scan left with the world-view feature,
  removed 2026-11-05 — see _HTML_TEMPLATES below.)
* Stock dialogs: every file using QMessageBox/QInputDialog must be
  in ALLOWLIST_DIALOG_FILES. QInputDialog must appear nowhere
  (today's count is zero). The allowlist empties in Phase C.
* Phase C re-pin (guide D1, reviewed): gui/style.py ONLY — the
  white-alpha BORDER_*/GLASS_* values left the file with the role
  -line migration and the cyan scrollbar thumbs arrived, so its
  entry is the new literal set; no other file's entry changed.

Vendored JS/CSS (Cesium, MapLibre) is NOT scanned — the guide scopes
the literal test to .py + HTML templates. The ruff TID252 banned-API
entry in ruff.toml is the CI companion; this grep test is the lab
enforcement (ruff has no binary in lab).
"""

import re
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

_HEX = re.compile(r"#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")
_RGB = re.compile(r"rgba?\s*\([^)]*\)")

# INTENTIONAL (world-view removal, 2026-11-05): the two HTML
# templates are gone with the feature — no HTML template is
# scanned any more, so this tuple is empty (kept so the scan loop
# below needs no restructuring).
_HTML_TEMPLATES = ()


def _scan_colours():
    """Return {(relpath, literal)} for every colour literal in scope."""
    found = set()
    for path in sorted((TREE / "gui").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(TREE))
        for m in _HEX.finditer(src):
            found.add((rel, m.group(0).lower()))
        for m in _RGB.finditer(src):
            found.add((rel, re.sub(r"\s+", "", m.group(0).lower())))
    for html in _HTML_TEMPLATES:
        path = TREE / html
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in _HEX.finditer(src):
            found.add((html, m.group(0).lower()))
        for m in _RGB.finditer(src):
            found.add((html, re.sub(r"\s+", "", m.group(0).lower())))
    return found


def _scan_dialog_files():
    """Return {relpath} for every .py file mentioning stock dialogs."""
    found = set()
    for path in sorted(TREE.rglob("*.py")):
        # NOTE: TREE is absolute, so the tests/ exclusion must test
        # membership, not parts[0] (which is "/"). The first version
        # of this test got that wrong and flagged the test files
        # themselves; fixed here, not by allowlisting them.
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if "QMessageBox" in src or "QInputDialog" in src:
            found.add(str(path.relative_to(TREE)))
    return found


ALLOWLIST_COLOURS = {
    'gui/mini_orb.py': {'#35d6ff', '#3d8bff', '#464c56', '#6e7682', '#7c8cff', '#8a919c', '#a78bfa', '#aa3746', '#bec6d0', '#c9ced6', '#e6fbff', '#efe9ff', '#f06e6e', '#fd465a', '#ff5468', '#ff6a00', '#ff7eb6', '#ff9a7a', '#ffb020', '#ffbebe', '#ffe3e6', '#ffe8d6', '#fff1d6', '#ffffff', 'rgb(hex_color:str)'},
    'gui/onboarding.py': {'#3dd68c', '#4dd8d0', '#7c8cff', '#b9c4ff', '#ff9a9a', '#ffb86b', 'rgba(124,140,255,28)', 'rgba(124,140,255,36)', 'rgba(235,238,255,165)', 'rgba(255,255,255,10)', 'rgba(255,255,255,12)', 'rgba(255,255,255,18)', 'rgba(255,255,255,20)', 'rgba(255,255,255,22)', 'rgba(255,255,255,64)', 'rgba(255,255,255,7)'},
    # INTENTIONAL (GUI split #6): gui/style.py split verbatim into
    # style_tokens / style_sheet / style_theme; the old entry's literal
    # set is re-keyed onto the three files (union identical, 120 literals;
    # the style.py facade itself carries no colour literal). No literal
    # added or removed. Phase E re-pin note lives on style_sheet.py:
    # navBtn:focus wash 48 -> 14 (focus must not read as current page).
    # Flaw sweep 2026-11-16 (parent re-pin): the STATUS_STRIP_BG literal
    # lives in style_tokens.py — the token system's home — not in a widget.
    'gui/style_tokens.py': {'#04090e', '#08131b', '#0c1d28', '#122636', '#35d6ff', '#3d8bff', '#5cf2a8', '#7e9aa6', '#b0c1cd', '#e6fbff', '#ff5468', '#ffb020', 'rgba(0,0,0,48)', 'rgba(0,0,0,96)', 'rgba(16,19,26,235)', 'rgba(230,251,255,115)', 'rgba(255,255,255,10)', 'rgba(255,255,255,17)', 'rgba(255,255,255,22)', 'rgba(255,255,255,25)', 'rgba(255,255,255,90)', 'rgba(53,214,255,102)', 'rgba(53,214,255,12)', 'rgba(53,214,255,178)', 'rgba(53,214,255,18)', 'rgba(53,214,255,28)', 'rgba(53,214,255,36)', 'rgba(53,214,255,40)', 'rgba(53,214,255,48)', 'rgba(53,214,255,76)', 'rgba(53,214,255,96)'},
    'gui/style_sheet.py': {'#a7b0b8', '#c6cad0', '#d7f0dc', '#e6fbff', '#ffffff', 'rgba(0,0,0,0.28)', 'rgba(0,0,0,130)', 'rgba(0,0,0,90)', 'rgba(174,185,255,0.55)', 'rgba(255,255,255,0.10)', 'rgba(255,255,255,10)', 'rgba(255,255,255,12)', 'rgba(255,255,255,18)', 'rgba(255,255,255,20)', 'rgba(255,255,255,22)', 'rgba(255,255,255,26)', 'rgba(255,255,255,28)', 'rgba(255,255,255,32)', 'rgba(255,255,255,4)', 'rgba(255,255,255,40)', 'rgba(255,255,255,5)', 'rgba(255,255,255,7)', 'rgba(255,255,255,8)', 'rgba(255,255,255,9)', 'rgba(255,84,104,110)', 'rgba(255,84,104,170)', 'rgba(255,84,104,32)', 'rgba(255,84,104,48)', 'rgba(255,84,104,64)', 'rgba(53,214,255,0)', 'rgba(53,214,255,0.17)', 'rgba(53,214,255,0.30)', 'rgba(53,214,255,0.45)', 'rgba(53,214,255,14)', 'rgba(53,214,255,26)', 'rgba(53,214,255,30)', 'rgba(53,214,255,44)', 'rgba(53,214,255,48)', 'rgba(53,214,255,52)', 'rgba(53,214,255,60)', 'rgba(53,214,255,90)', 'rgba(61,139,255,12)', 'rgba(61,139,255,130)', 'rgba(61,139,255,26)', 'rgba(61,139,255,44)', 'rgba(61,139,255,48)', 'rgba(61,139,255,90)'},  # Phase E re-pin: navBtn:focus wash 48 -> 14 (focus must not read as current page).
    'gui/style_theme.py': {'#000000', '#0a0a0a', '#141414', '#185b6e', '#1b3d6e', '#2fbce0', '#35d6ff', '#367ae0', '#3d8bff', '#3ddab4', '#5ddeff', '#64a2ff', '#7c8cff', '#8a919c', '#8b7cff', '#a78bfa', '#a8a8a8', '#c4c4c4', '#c9ced6', '#cfcfcf', '#d8d8d8', '#d9d9d9', '#d9fff2', '#e2703a', '#e6fbff', '#e8b08d', '#e8e8e8', '#efe9ff', '#fb9dbd', '#fd465a', '#ff5468', '#ff6a00', '#ff7eb6', '#ff9a7a', '#ffab5e', '#ffb020', '#ffe3e6', '#ffe3ee', '#ffe600', '#ffe8d6', '#ffe9d2', '#fff1d6', '#fff9c4', '#ffffff', 'rgb((hue+0.5)', 'rgb(accent)', 'rgb(hex_a)', 'rgb(hex_b)', 'rgb(hex_color)', 'rgb(value:str)', 'rgba(255,255,255,10)', 'rgba(255,255,255,16)', 'rgba(255,255,255,40)', 'rgba(255,255,255,88)', 'rgba(53,214,255,18)', 'rgba(53,214,255,30)', 'rgba(53,214,255,64)'},
    # INTENTIONAL (GUI split #7): gui/visualizer.py split verbatim into
    # visualizer_state.py / visualizer_paint.py; the old entry's literal
    # set is re-keyed onto the state file (union identical, 35 literals;
    # the paint file and the visualizer.py facade carry no colour
    # literal). No literal added or removed.
    # UI rethink Build 1: the DISCONNECTED palette changed grey ->
    # dim blue (night orb alive, never grey) — the 6 grey literals
    # (#464c56, #6e7682, #bec6d0 + rgb forms) are gone from the file,
    # replaced 1-for-1 by the 6 blue literals below. Same size (35).
    'gui/visualizer_state.py': {'#186073', '#1b3f73', '#35d6ff', '#3d8bff', '#7c8cff', '#8a919c', '#8cafc8', '#a78bfa', '#aa3746', '#c9ced6', '#e6fbff', '#efe9ff', '#f06e6e', '#fd465a', '#ff5468', '#ff6a00', '#ff7eb6', '#ff9a7a', '#ffb020', '#ffbebe', '#ffe3e6', '#ffe8d6', '#fff1d6', '#ffffff', 'rgb("#186073")', 'rgb("#1b3f73")', 'rgb("#8cafc8")', 'rgb("#a78bfa")', 'rgb("#aa3746")', 'rgb("#f06e6e")', 'rgb("#ffbebe")', 'rgb(edge)', 'rgb(hex_color:str)', 'rgb(hi)', 'rgb(mid)'},
    # Phase E: window_shell.py left the allowlist — the shell
    # (title-bar icons, tray states, nav/wordmark/briefing,
    # send/mic/mute/screen icons, mic shadow) is token-only now.
}

ALLOWLIST_DIALOG_FILES = set()  # Phase C: empty. No stock dialog remains.

class TestColourLiterals(unittest.TestCase):
    def test_no_new_colour_literals(self):
        allowed = {
            (f, lit) for f, lits in ALLOWLIST_COLOURS.items() for lit in lits
        }
        found = _scan_colours()
        new = sorted(found - allowed)
        self.assertEqual(
            new, [], f"{len(new)} NEW colour literal(s) outside tokens: {new[:10]}"
        )

    def test_design_system_is_literal_free(self):
        # New design-system code is born token-only; it never enters
        # the allowlist, so any literal here fails outright.
        found = _scan_colours()
        bad = sorted(p for p in found if "design_system" in p[0])
        self.assertEqual(bad, [], f"literals in gui/design_system/: {bad[:10]}")


class TestStockDialogs(unittest.TestCase):
    def test_no_new_stock_dialog_files(self):
        found = _scan_dialog_files()
        new = sorted(found - ALLOWLIST_DIALOG_FILES)
        self.assertEqual(
            new, [], f"NEW stock-dialog file(s): {new}"
        )

    def test_qinputdialog_nowhere(self):
        for path in sorted(TREE.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            src = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(
                "QInputDialog", src, f"QInputDialog in {path.relative_to(TREE)}"
            )


if __name__ == "__main__":
    unittest.main()
