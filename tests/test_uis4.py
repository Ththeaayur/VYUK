"""S4 settings audit suite (AST/static — GUI modules import frozen
``voice_assistant.*`` names and build real Qt widgets, so they cannot
be imported or driven in this lab. This suite proves structure +
grouping + single-writer + label presence over the real files, NOT
rendered appearance. Field proof on real Windows is still owed:
everyday groups show only the five groups; Advanced collapsed by
default; every former duplicate resolves to one control; every label
reads as user value; safety settings findable without hunting.
"""

import ast
import re
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
# INTENTIONAL (refactor P1 step 5): window_settings.py split verbatim
# into family modules; the settings surface is now these files
# together. Helpers below accept one path or the whole tuple, and
# direct text reads use _ws_text (concatenation). No assertion changed.
WS_FILES = (
    TREE / "gui" / "window_settings.py",
    TREE / "gui" / "window_settings_think.py",
    TREE / "gui" / "window_settings_voice.py",
    TREE / "gui" / "window_settings_groups.py",
    TREE / "gui" / "window_settings_advbrain.py",
    TREE / "gui" / "window_settings_advcards.py",
    TREE / "gui" / "window_settings_appearance.py",
    TREE / "gui" / "window_settings_privacy.py",
)
WS = WS_FILES
PF = TREE / "gui" / "pf_surfaces.py"  # INTENTIONAL (Phase 2 B6 rename): path load follows the renamed module (was "pf_v564.py").
BRAIN = TREE / "gui" / "window_brain_services.py"  # GUI split #4 INTENTIONAL: _build_world_card moved verbatim here (was "window_brain.py").

GROUPS = ["Voice & language", "Privacy & permissions", "Appearance",
          "Notifications", "Connections"]

# Card title -> layout variable in _build_settings_page. This table IS
# the S4 inventory: any card added later without an assignment here
# fails test_every_card_assigned (inventory completeness).
EXPECTED = {
    "How VYUK thinks": "_g_connect",
    "Voice": "_g_voice",
    "Whisper hours": "_g_voice",
    # Stage 0 features batch 6 (2026-11-23): the calendar-aware
    # quiet card, on the Voice page after Whisper hours.
    "Quiet hours": "_g_voice",
    "Briefing voice": "_g_voice",
    "Microphone": "_g_voice",
    "Voice bridge": "_g_voice",
    "Sounds": "_g_voice",
    "Appearance": "_g_appearance",
    "Theme packs": "_g_appearance",
    "Presence": "_g_connect",
    # UI Phase 5 (2026-11-27): the Home hub connection card, in
    # the Connect group beside How VYUK thinks and Presence.
    "Home hub": "_g_connect",
    "Notices": "_g_notices",
    "Suggestions": "_g_notices",
    "Privacy & security": "_g_privacy",
    "Screen watching": "_g_privacy",
    "Browser control": "_g_privacy",
    "Audit": "_g_privacy",
    "Remembered approvals": "_g_privacy",
    "Local brain": "more_layout",
    "Your settings file": "more_layout",
    "Backup passphrase": "more_layout",
    "Keyboard shortcuts": "more_layout",
    "Settings store": "more_layout",
    "System status": "more_layout",
    "Doctor": "more_layout",
    "Feedback & crash reports": "more_layout",
    "Report a problem": "more_layout",
    "Data portability": "more_layout",
    "Models & storage": "more_layout",
    "What's new": "more_layout",
    "Power & network": "more_layout",
    "About": "more_layout",
}

# Cards whose purpose is self-evident without a hint line (name/version
# content). Every other card must carry a value-explaining hint.
HINTLESS_OK = {"About"}


def _as_paths(path):
    if isinstance(path, (str, Path)):
        return (path,)
    return tuple(path)


def _ws_text(paths):
    """Concatenated settings source (see WS_FILES above)."""
    return "\n".join(Path(p).read_text(encoding="utf-8")
                     for p in _as_paths(paths))


def settings_card_calls(path):
    """All self._settings_card(...) calls: (title, layout var, hint?)."""
    out = []
    for p in _as_paths(path):
        tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "_settings_card":
                args = node.args
                if len(args) < 2 or not isinstance(args[1], ast.Constant):
                    continue
                layout = ast.unparse(args[0]) if args else "?"
                hint = len(args) > 2 or any(
                    kw.arg == "hint" for kw in node.keywords)
                out.append((args[1].value, layout, hint))
    return out


def group_titles(path):
    """Titles passed to the _group(...) helper, in call order."""
    out = []
    for p in _as_paths(path):
        tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "_group" \
                    and node.args and isinstance(node.args[0], ast.Constant):
                out.append(node.args[0].value)
    return out


class TestS4Groups(unittest.TestCase):
    def test_five_groups_exist_in_order(self):
        self.assertEqual(group_titles(WS), GROUPS)

    def test_group_headers_are_not_section_titles(self):
        src = _ws_text(WS)
        self.assertIn('setObjectName("settingsGroupTitle")', src)
        # The _group helper must not mint sectionTitle labels, or
        # Settings search + the palette would treat headers as cards.
        block = src[src.index("def _group(title):"):src.index(
            "def _group(title):") + 800]
        self.assertNotIn("sectionTitle", block)

    def test_every_card_assigned(self):
        found = {}
        for title, layout, _hint in settings_card_calls(WS):
            self.assertNotIn(title, found, f"duplicate card {title!r}")
            found[title] = layout
        self.assertEqual(found, EXPECTED)

    def test_no_card_left_on_bare_layouts(self):
        for title, layout, _hint in settings_card_calls(WS):
            self.assertNotIn(layout, ("c_layout",),
                             f"{title!r} still on the bare page layout")

    def test_world_card_in_connections(self):
        src = _ws_text(WS)
        self.assertIn("self._build_world_card(_g_connect)", src)
        brain = [t for t, _l, _h in settings_card_calls(BRAIN)]
        self.assertIn("Your world", brain)

    def test_advanced_hidden_by_default(self):
        src = _ws_text(WS)
        self.assertIn("self.more_container.setVisible(False)", src)
        self.assertIn('QPushButton("Advanced ▸")', src)
        self.assertNotIn('"More settings ▸"', src)
        self.assertNotIn('"More settings ▾"', src)

    def test_local_brain_renamed(self):
        titles = [t for t, _l, _h in settings_card_calls(WS)]
        self.assertIn("Local brain", titles)
        self.assertNotIn("Advanced", titles)


class TestS4Dedup(unittest.TestCase):
    def test_shortcuts_single_display(self):
        titles = [t for t, _l, _h in settings_card_calls(WS)]
        self.assertEqual(titles.count("Keyboard shortcuts"), 1)
        src = _ws_text(WS)
        self.assertNotIn("ShortcutsWidget(", src)
        # GUI split #3 INTENTIONAL: ShortcutsWidget moved verbatim into
        # pf_settings_help.py; the facade no longer carries its class.
        self.assertIn(
            "class ShortcutsWidget",
            (TREE / "gui" / "pf_settings_help.py").read_text(encoding="utf-8"))

    def test_citizenship_autostart_is_a_jump(self):
        # GUI split #3 INTENTIONAL: CitizenshipWidget moved verbatim into
        # pf_citizenship.py; the facade no longer carries its body.
        pf = (TREE / "gui" / "pf_citizenship.py").read_text(encoding="utf-8")
        self.assertIn("on_autostart_jump", pf)
        self.assertIn("if on_autostart_jump is not None:", pf)
        self.assertIn('"Change autostart setting…"', pf)
        src = _ws_text(WS)
        self.assertIn("on_autostart_jump=lambda", src)
        self.assertIn('show_settings_card(\n                    "Presence")', src)

    def test_no_second_autostart_writer_on_page(self):
        # The page path renders the jump button, not the checkbox:
        # the checkbox construction lives only in the no-callback
        # branch of the widget (tests/reuse), never on the page.
        # GUI split #3 INTENTIONAL: CitizenshipWidget moved verbatim into
        # pf_citizenship.py; the facade no longer carries its body.
        pf = (TREE / "gui" / "pf_citizenship.py").read_text(encoding="utf-8")
        branch = pf[pf.index("if on_autostart_jump is not None:"):]
        else_at = branch.index("else:")
        self.assertIn("autostart_box", branch[else_at:])
        self.assertNotIn("autostart_box",
                         branch[:else_at].replace("self.autostart_jump", ""))


class TestS4SafetyAndLabels(unittest.TestCase):
    def test_privacy_safety_findability(self):
        found = {t: l for t, l, _h in settings_card_calls(WS)}
        for title in ("Privacy & security", "Screen watching",
                      "Browser control", "Audit", "Remembered approvals"):
            self.assertEqual(found[title], "_g_privacy", title)
        src = _ws_text(WS)
        self.assertIn("_g_privacy.addWidget(self._w9_permissions_widget)",
                      src)
        self.assertIn("_g_privacy.addWidget(self._w9_privacy_widget)", src)
        # Cross-link: automatic suggestion actions jump from Privacy
        # to the Suggestions card (everyday, Notifications group).
        self.assertIn('"Automatic suggestion actions…"', src)
        self.assertIn('show_settings_card("Suggestions")', src)

    def test_every_card_has_value_hint(self):
        for title, _layout, hint in settings_card_calls(WS):
            if title in HINTLESS_OK:
                continue
            self.assertTrue(hint, f"{title!r} has no value hint")

    def test_new_value_hints(self):
        src = _ws_text(WS)
        self.assertIn("She speaks softer between these hours.", src)
        self.assertIn("the morning briefing is spoken at the pace below",
                      src)
        self.assertIn("Which microphone she listens with, and how she "
                      "hears you.", src)
        self.assertIn("Brain engine (drives 'How VYUK thinks' above)", src)

    def test_safety_never_behind_paywall_or_auto_choice(self):
        # No safety card may live only in Advanced without an
        # everyday link: privacy cards are everyday, and the
        # auto-act cross-link is pinned above. This guards the
        # grouping, not just the labels.
        found = {t: l for t, l, _h in settings_card_calls(WS)}
        self.assertEqual(found["Privacy & security"], "_g_privacy")
        self.assertEqual(found["Remembered approvals"], "_g_privacy")


class TestS4Search(unittest.TestCase):
    def test_search_covers_group_headers(self):
        src = _ws_text(WS)
        self.assertIn("self.settings_search = QLineEdit()", src)
        self.assertIn("_settings_group_headers", src)
        search_fn = src[src.index("def _on_settings_search"):]
        search_fn = search_fn[:search_fn.index("def show_settings_card")]
        self.assertIn("_settings_group_headers", search_fn)

    def test_explanation_locations_follow_the_move(self):
        # GUI split #3 INTENTIONAL: the explanation strings moved verbatim
        # into pf_settings_help.py; the facade no longer carries them.
        pf = (TREE / "gui" / "pf_settings_help.py").read_text(encoding="utf-8")
        self.assertNotIn("More settings →", pf)
        self.assertIn('"Settings → Privacy & security",', pf)
        self.assertIn('"Settings → Advanced → Keyboard shortcuts",', pf)
        self.assertIn('"Settings → Advanced → Doctor",', pf)

    def test_no_more_settings_user_strings(self):
        # pf_surfaces must not mention the old disclosure name anywhere
        # anymore; window_settings.py may mention it only in the
        # one historical comment noting the rename.
        # GUI split #3 INTENTIONAL: the surface now spans the facade + 6
        # section modules; the absence check must cover all of them, or
        # moved content would escape it.
        for _mod in ("pf_surfaces.py", "pf_surfaces_helpers.py", "pf_proof.py",
                     "pf_settings_help.py", "pf_permissions.py",
                     "pf_citizenship.py", "pf_privacy.py"):
            self.assertNotIn(
                "More settings",
                (TREE / "gui" / _mod).read_text(encoding="utf-8"))
        for line in _ws_text(WS).splitlines():
            if "More settings" in line:
                self.assertTrue(line.strip().startswith("#"), line)
        # E-F207: the raw-text check above is blind to the phrase split
        # across adjacent string literals ("Settings → More " +
        # "settings → ...") — two FAQ entries in pf_settings_help.py
        # shipped exactly that split while this test passed. The
        # parser folds implicit concatenation, so a string constant IS
        # the rendered text: check every constant in every guarded
        # module. Comments are not constants, so the one historical
        # rename comment in window_settings_groups.py still passes.
        for _path in [(TREE / "gui" / _m) for _m in (
                "pf_surfaces.py", "pf_surfaces_helpers.py", "pf_proof.py",
                "pf_settings_help.py", "pf_permissions.py",
                "pf_citizenship.py", "pf_privacy.py")] + list(WS):
            _tree = ast.parse(_path.read_text(encoding="utf-8"))
            for _node in ast.walk(_tree):
                if isinstance(_node, ast.Constant) \
                        and isinstance(_node.value, str):
                    self.assertNotIn("More settings", _node.value,
                                     str(_path))

    def test_deep_link_titles_survive(self):
        # Doctor + palette callers jump to these titles; a rename
        # would strand them (palette rows are derived, but the
        # doctor's explicit titles are not).
        titles = [t for t, _l, _h in settings_card_calls(WS)]
        for want in ("Voice", "How VYUK thinks", "Microphone"):
            self.assertIn(want, titles)

    def test_handlers_survive_reorganization(self):
        src = _ws_text(WS)
        for fn in ("def _on_autostart_toggled", "def _on_motion_selected",
                   "def _on_engine_segment_clicked",
                   "def _on_settings_search", "def show_settings_card",
                   "def _on_more_toggled"):
            self.assertIn(fn, src)


if __name__ == "__main__":
    unittest.main()
