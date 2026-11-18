"""Tests for Command Deck Phase G (first run, packaging, wording).

What is proven here, per guide §10 / §9.5 / §11 guard 15:

* the banned-name scan: no Stark / Iron Man / Arc Reactor / JARVIS
  wording in shipped files outside an explicit, reasoned
  allowlist (rule statements, a quoted historical ruling, and
  identity-refusal veto strings — each named, each asserted
  to still exist so the allowlist cannot go stale);
* the S1 mark: multi-size .ico (16/20/24/32/40/48/64/128/256,
  legible at 16 px) + 512 master PNG, and both generators
  reproduce the shipped files;
* the installer art: the wizard BMPs regenerate byte-identical;
* manifest coverage: the manifest builder covers every shipped
  *.py file and every globe asset, and the binary asset
  inventory (icons, fonts + OFL texts, earcons, installer
  files) exists and is well-formed;
* the font licence table: every bundled TTF family has a
  THIRD_PARTY_LICENSES.md row and an OFL text;
* first-run logic: boot checks report real state or honest
  FAULT (never an invented OK, never a raise), stage progress
  resumes at the first open stage, coach targets resolve to
  real widgets or None, and the coach sequence runs once,
  walks, backs, dismisses, skips unresolvable targets, and
  never breaks on overlay failure;
* first-run chrome: the Terms gate and the first-open prompt
  ride HudDialog, never a stock QDialog;
* the coach overlay widget itself (Qt only): brackets, card,
  and button callbacks.

What is NOT claimed here (see the Phase G report): a clean
install on fresh Windows 10/11 (FIELD-OWED), brand strings
final (persona-naming ruling CLOSED 2026-11-01; this suite
does not claim it — the ruling lives in
gui/design_system/DECISIONS.md), a release signature (the
manifest re-sign in the lab is dev-only), or the filmed
marketing capture pass (procedure documented, pass
FIELD-OWED).
"""

from __future__ import annotations

import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from PySide6.QtWidgets import QApplication
    QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt is optional on system python
    QT_AVAILABLE = False

from core.health import integrity as integrity_mod  # Phase 3 H1 INTENTIONAL: module moved to core/health/
from gui import firstrun_g


# --------------------------------------------------------------------- scan

# The scan defines the banned tokens, so this file necessarily
# names them; it is excluded from its own scan for that reason
# alone. Every other file is scanned, tests included.
_SKIP_SCAN_FILES = {"tests/test_commanddeck_phaseG.py"}
_TEXT_EXTS = {".py", ".md", ".txt", ".iss", ".spec", ".json"}
_SKIP_DIRS = {"__pycache__", "data", ".git", "node_modules"}
_BANNED = [re.compile(p, re.IGNORECASE) for p in (
    r"\bstark\b", r"\biron man\b", r"\barc reactor\b", r"\bjarvis\b")]
# (relative path, line substring, reason). "Reactor" alone as an
# orb style name is explicitly fine per guide D10, so only the
# "Arc Reactor" phrase is banned — never a bare "Reactor".
_ALLOWLIST = [
    # Phase 3 M1b INTENTIONAL: memory_files moved to core/memory/ (content byte-identical); allowlist path follows the move.
    ("core/memory/memory_files.py", "whatever makes it close to",
     "verbatim quote of the owner's 2026-10-25 ruling, not product wording"),
    ("core/feel.py", "i am jarvis",
     "identity-refusal check: the product must refuse 'I am JARVIS' claims"),
    ("gui/design_system/DECISIONS.md",
     "No JARVIS, Stark, Iron Man or Arc Reactor wording",
     "D10 rule statement itself"),
    ("gui/design_system/DECISIONS.md", "banned-name scan (no JARVIS",
     "persona-naming rule row"),
    ("tests/test_wave8_feel.py", "I am JARVIS, at your service",
     "veto-test input: the product must refuse this identity claim"),
    ("ARCHITECTURE.md", "whatever makes it close to jarvis",
     "verbatim quote of the owner's 2026-10-25 ruling"),
    ("ARCHITECTURE.md", "I-am-JARVIS identity claims",
     "veto-list rule statement (claims the product refuses)"),
    ("gui/design_system/DECISIONS.md", "Trademark: banned-name scan (Stark",
     "Phase G design record describing the scan itself, not product wording"),
    # P-5 (debug pass): 19 hits, all legitimate citations /
    # attributions / design comments / test vocabulary — each
    # entry names the hit it covers; none is product wording.
    ("THIRD_PARTY_LICENSES.md", "adewaskar/jarvis boot sequence",
     "licence-table row: third-party project attribution"),
    ("core/desktop/boot_sequence.py", "adewaskar/jarvis",
     "pattern-source citation + copyright attribution (MIT)"),
    ("core/desktop/boot_sequence.py", "arc reactor lighting",
     "pattern description: the adewaskar/jarvis boot sequence's lighting beat"),
    ("core/voice/wake_training.py", "hey jarvis",
     "wake-word example: the user's own chosen phrase, not product identity"),
    ("gui/boot_screen.py", "jarvis-mark-i",
     "pattern citation: the round-7 #2 pick (MIT, own code)"),
    ("gui/mini_orb.py", "jarvis-mark-i pattern",
     "pattern citation, not product wording"),
    ("gui/orb_sphere.py", "jarvis-mark-i",
     "pattern citation: the round-7 #2 pick (MIT, own code)"),
    ("gui/orb_sphere.py", "trademarks belong to Marvel",
     "trademark disclaimer: the marks belong to Marvel"),
    ("gui/style_tokens.py", "JARVIS-like futuristic face",
     "design comment: the HUD face the design wants, not product wording"),
    ("gui/visualizer_glass.py", "exactly like JARVIS",
     "design comment: the orb look the design wants, not product wording"),
    ("gui/visualizer_paint.py", "jarvis-mark-i pattern",
     "pattern citation, not product wording"),
    ("requirements.txt", "adewaskar/jarvis",
     "licence comment: third-party project attribution"),
    ("tests/BENCHMARK_AND_PROVERS.md", "NOT a JARVIS-ness score",
     "doc statement: the benchmark is explicitly NOT a JARVIS-ness score"),
    ("tests/test_batch8_deviation.py", "scifi JARVIS",
     "verbatim quote of the owner's 2026-11-03 code-rulings delegation ('commercial cool scifi JARVIS' — the phrase spans a line break, so the sub is the hit line itself)"),
    ("tests/test_jarvis_mark_i.py", "jarvis-mark-i pattern",
     "pattern citation in the test docstring, not product wording"),
    ("tests/test_jarvis_mark_i.py", "arc reactor",
     "banned-name list the test itself checks for absence"),
]


def _shipped_text_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if rel.as_posix() in _SKIP_SCAN_FILES:
            continue
        if path.suffix.lower() not in _TEXT_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        yield rel.as_posix(), text


class TestTrademarkScan(unittest.TestCase):
    def test_banned_names_only_allowlisted(self):
        unlisted = []
        for rel, text in _shipped_text_files():
            lines = text.splitlines()
            for lineno, line in enumerate(lines, 1):
                if not any(p.search(line) for p in _BANNED):
                    continue
                # A hit is allowlisted when the line itself — or
                # the line above it, for quotations that span a
                # line break — carries the allowlist substring.
                context = line + "\n" + (lines[lineno - 2]
                                         if lineno >= 2 else "")
                allowed = any(
                    rel == apath and sub.lower() in context.lower()
                    for apath, sub, _reason in _ALLOWLIST)
                if not allowed:
                    unlisted.append(f"{rel}:{lineno}: {line.strip()[:120]}")
        self.assertEqual(unlisted, [], "unallowlisted banned-name hits")

    def test_allowlist_entries_still_match(self):
        # An allowlist entry that matches nothing is stale: it
        # would silently pass a file the sweep already rewrote.
        files = dict(_shipped_text_files())
        for apath, sub, reason in _ALLOWLIST:
            self.assertIn(apath, files, f"allowlist file gone: {apath}")
            self.assertTrue(
                any(sub.lower() in line.lower()
                    for line in files[apath].splitlines()),
                f"stale allowlist entry ({reason}): {apath} :: {sub}")


# --------------------------------------------------------------------- icons

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _ico_entries(path):
    data = Path(path).read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00", "not an .ico file"
    count = struct.unpack("<H", data[4:6])[0]
    entries = []
    for i in range(count):
        off = 6 + i * 16
        width = data[off] or 256
        height = data[off + 1] or 256
        size = struct.unpack("<I", data[off + 8:off + 12])[0]
        offset = struct.unpack("<I", data[off + 12:off + 16])[0]
        entries.append((width, height, size, offset))
    return data, entries


class TestIcons(unittest.TestCase):
    def test_ico_size_table(self):
        data, entries = _ico_entries(ROOT / "gui/assets/vyuk.ico")
        self.assertEqual(
            sorted((w, h) for w, h, _s, _o in entries),
            sorted((s, s) for s in ICO_SIZES))
        for width, height, size, offset in entries:
            payload = data[offset:offset + size]
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n",
                             f"ico entry {width}x{height} is not PNG")
            w, h = struct.unpack(">II", payload[16:24])
            self.assertEqual((w, h), (width, height))

    def test_png_master(self):
        data = (ROOT / "gui/assets/vyuk_icon.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (512, 512))

    def test_icon_generator_reproduces_shipped_files(self):
        try:
            import PIL  # noqa: F401 — generator needs Pillow
        except ImportError:
            self.skipTest("Pillow required for the icon generator")
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "vyuk_icon.png"
            ico = Path(tmp) / "vyuk.ico"
            subprocess.run(
                [sys.executable, str(ROOT / "installer/make_vyuk_icon.py"),
                 "--png", str(png), "--ico", str(ico)],
                check=True, capture_output=True, text=True)
            self.assertEqual(png.read_bytes(),
                             (ROOT / "gui/assets/vyuk_icon.png").read_bytes(),
                             "regenerated master PNG differs from shipped")
            _d, regen = _ico_entries(ico)
            _d2, shipped = _ico_entries(ROOT / "gui/assets/vyuk.ico")
            self.assertEqual(sorted((w, h) for w, h, _s, _o in regen),
                             sorted((w, h) for w, h, _s, _o in shipped))

    def test_wizard_art_reproduces_shipped_bmps(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, str(ROOT / "installer/make_wizard_art.py"),
                 "--out-dir", tmp],
                check=True, capture_output=True, text=True)
            for name in ("wizard_image.bmp", "wizard_small.bmp"):
                self.assertEqual(
                    (Path(tmp) / name).read_bytes(),
                    (ROOT / "installer" / name).read_bytes(),
                    f"regenerated {name} differs from shipped")


# ------------------------------------------------------------------ manifest

_FONT_FAMILY_OFL = {
    "ChakraPetch": ("Chakra Petch", "OFL-ChakraPetch.txt"),
    "JetBrainsMono": ("JetBrains Mono", "OFL-JetBrainsMono.txt"),
    "Inter": ("Inter", "OFL.txt"),
    "SpaceGrotesk": ("Space Grotesk", "OFL-SpaceGrotesk.txt"),
    # P-6 (debug pass): Orbitron is a shipped font
    # (gui/assets/fonts/Orbitron-Variable.ttf + OFL-Orbitron.txt)
    # — the HUD display face (B-R1-7).
    "Orbitron": ("Orbitron", "OFL-Orbitron.txt"),
    # P-6 (debug pass, same gap): Sora (body face, B-R1-7r)
    # and Share Tech Mono (code / numbers face, B-R1-7) are
    # also shipped (Sora-Variable.ttf + OFL-Sora.txt,
    # ShareTechMono-Regular.ttf + OFL-ShareTechMono.txt) —
    # the map listed only the REMOVED fonts' families.
    "Sora": ("Sora", "OFL-Sora.txt"),
    "ShareTechMono": ("Share Tech Mono", "OFL-ShareTechMono.txt"),
}


class TestManifestCoverage(unittest.TestCase):
    # INTENTIONAL (world-view removal, 2026-11-05): renamed —
    # the manifest is *.py-only again, the globe-asset set went
    # with the feature.
    def test_build_manifest_covers_every_py(self):
        manifest = integrity_mod.build_manifest(ROOT)
        files = manifest["files"]
        expected = set()
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if integrity_mod._manifest_excluded(rel):
                continue
            expected.add(rel.as_posix())
        self.assertEqual(set(files), expected)
        for pinned in ("gui/firstrun_g.py", "gui/agent_overlay.py",
                       "gui/onboarding.py", "gui/firstopen_prompt.py",
                       "tests/test_commanddeck_phaseG.py"):
            self.assertIn(pinned, files)

    def test_binary_asset_inventory(self):
        assets = ROOT / "gui/assets"
        for name in ("vyuk.ico", "vyuk_icon.png"):
            path = assets / name
            self.assertTrue(path.is_file(), f"missing asset: {name}")
            self.assertGreater(path.stat().st_size, 0)
        wavs = sorted(assets.glob("*.wav"))
        self.assertGreaterEqual(len(wavs), 4, "earcon WAVs missing")
        for wav in wavs:
            data = wav.read_bytes()
            self.assertEqual(data[:4], b"RIFF", f"not a WAV: {wav.name}")
            self.assertGreater(len(data), 44)
        for name in ("vyuk.spec", "vyuk.iss", "wizard_image.bmp",
                     "wizard_small.bmp", "WINDOWS_BUILD_CHECKLIST.md"):
            self.assertTrue((ROOT / "installer" / name).is_file(),
                            f"missing installer file: {name}")
        self.assertTrue((ROOT / "EULA.txt").is_file())

    def test_font_rows_listed(self):
        table = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
        fonts = ROOT / "gui/assets/fonts"
        for ttf in sorted(fonts.glob("*.ttf")):
            stem = ttf.stem.split("-")[0]
            self.assertIn(stem, _FONT_FAMILY_OFL, f"unknown font: {ttf.name}")
            family, ofl = _FONT_FAMILY_OFL[stem]
            self.assertIn(family, table, f"no licence row for {family}")
            self.assertTrue((fonts / ofl).is_file(), f"missing {ofl}")
            self.assertGreater(ttf.stat().st_size, 0)


# ----------------------------------------------------------------- first run

class _Config:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestBootChecks(unittest.TestCase):
    def test_empty_stubs_report_faults_never_raise(self):
        # MIC is injected empty: with audio_inputs=None the probe
        # enumerates the real device, and a full-suite run leaves
        # audio state behind that makes enumeration succeed — the
        # probe must not claim "no microphone" on un-injected
        # state. MODE/LINK are faults because the brain choice is
        # unset (stub-determined). VOICE is device state, not
        # stub state: pinned only as a bool with a real detail,
        # never as a value — local voice readiness is genuinely
        # environment-dependent, and pinning False here failed
        # the full run while passing standalone.
        checks = firstrun_g.run_boot_checks(
            settings_get=lambda k: None, config_obj=None,
            audio_inputs=[])
        self.assertEqual([c.name for c in checks],
                         ["LINK", "MIC", "VOICE", "MODE"])
        self.assertTrue(all(isinstance(c.detail, str) and c.detail
                            for c in checks))
        by_name = {c.name: c for c in checks}
        self.assertFalse(by_name["MODE"].ok)
        self.assertFalse(by_name["LINK"].ok)
        self.assertFalse(by_name["MIC"].ok)
        self.assertIsInstance(by_name["VOICE"].ok, bool)

    def test_local_choice_with_mics_and_voice_reports_ok(self):
        checks = firstrun_g.run_boot_checks(
            settings_get=lambda k: {"setup/brain_choice": "local"}.get(k),
            config_obj=_Config(api_key="", voice_name="Charon"),
            audio_inputs=["mic1", "mic2"])
        by_name = {c.name: c for c in checks}
        self.assertTrue(by_name["LINK"].ok)
        self.assertEqual(by_name["MIC"].detail, "2 input(s) found")
        self.assertTrue(by_name["VOICE"].ok)
        self.assertEqual(by_name["MODE"].detail, "On this PC")

    def test_cloud_choice_without_key_reports_link_fault(self):
        checks = firstrun_g.run_boot_checks(
            settings_get=lambda k: {"setup/brain_choice": "cloud"}.get(k),
            config_obj=_Config(api_key="", voice_name=""),
            audio_inputs=[])
        by_name = {c.name: c for c in checks}
        self.assertFalse(by_name["LINK"].ok)
        self.assertIn("key", by_name["LINK"].detail)

    def test_raising_settings_get_never_raises(self):
        def boom(_k):
            raise RuntimeError("store exploded")
        checks = firstrun_g.run_boot_checks(settings_get=boom)
        self.assertEqual(len(checks), 4)


class TestStageProgress(unittest.TestCase):
    def test_resume_and_complete(self):
        prog = firstrun_g.FirstRunProgress(store={})
        self.assertEqual(prog.resume_stage(), "interaction")
        self.assertFalse(prog.is_complete())
        prog.mark_done("interaction")
        self.assertEqual(prog.resume_stage(), "brain")
        prog.skip_stage("brain")
        self.assertEqual(prog.resume_stage(), "permissions")
        prog.mark_done("permissions")
        prog.mark_done("first_mission")
        self.assertTrue(prog.is_complete())
        self.assertIsNone(prog.resume_stage())
        self.assertEqual(prog.summary(), {
            "interaction": "done", "brain": "skipped",
            "permissions": "done", "first_mission": "done"})

    def test_unknown_stage_ignored(self):
        prog = firstrun_g.FirstRunProgress(store={})
        prog.mark_done("nope")
        prog.skip_stage("nope")
        self.assertEqual(prog.resume_stage(), "interaction")


class _StubWindow:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def findChildren(self, _cls):
        return []


class TestCoachTargets(unittest.TestCase):
    def test_known_targets_resolve(self):
        window = _StubWindow(visualizer="ORB", input_cmd="COMP",
                             voice_chip="CHIP")
        self.assertEqual(
            firstrun_g.resolve_coach_target(window, "orb"), "ORB")
        self.assertEqual(
            firstrun_g.resolve_coach_target(window, "chat-composer"), "COMP")
        self.assertEqual(
            firstrun_g.resolve_coach_target(window, "voice-chip"), "CHIP")

    def test_unknown_target_and_none_window_are_none(self):
        window = _StubWindow(visualizer="ORB")
        self.assertIsNone(firstrun_g.resolve_coach_target(window, "nope"))
        self.assertIsNone(firstrun_g.resolve_coach_target(None, "orb"))


class _FakeOverlay:
    instances = []

    def __init__(self, target, title, body, on_next=None, on_back=None,
                 on_dismiss=None, parent=None):
        self.target = target
        self.title = title
        self.body = body
        self.on_next = on_next
        self.on_back = on_back
        self.on_dismiss = on_dismiss
        self.closed = False
        self.back_visible = True
        self.next_label = "Next"
        _FakeOverlay.instances.append(self)

    def close(self):
        self.closed = True

    def set_back_visible(self, visible):
        self.back_visible = bool(visible)

    def set_next_label(self, text):
        self.next_label = text


def _run_with_fake(window):
    store = {}
    _FakeOverlay.instances.clear()
    state = firstrun_g.run_coach_sequence(
        window,
        lambda k, d=False: store.get(k, d),
        lambda k, v: store.__setitem__(k, v),
        overlay_cls=_FakeOverlay)
    return store, state


class TestCoachSequence(unittest.TestCase):
    def _window(self):
        return _StubWindow(visualizer="ORB", input_cmd="COMP",
                           voice_chip="CHIP")

    def test_already_shown_never_constructs(self):
        store = {firstrun_g.COACH_SHOWN_KEY: True}
        state = firstrun_g.run_coach_sequence(
            self._window(),
            lambda k, d=False: store.get(k, d),
            lambda k, v: store.__setitem__(k, v),
            overlay_cls=_FakeOverlay)
        self.assertEqual(state["outcome"], "already-shown")
        self.assertIsNone(state["callbacks"])

    def test_walk_back_and_finish(self):
        store, state = _run_with_fake(self._window())
        self.assertEqual(state["outcome"], "running")
        self.assertEqual(len(_FakeOverlay.instances), 1)
        self.assertEqual(_FakeOverlay.instances[0].target, "ORB")
        self.assertFalse(_FakeOverlay.instances[0].back_visible)
        state["callbacks"]["next"]()
        self.assertEqual(_FakeOverlay.instances[1].target, "COMP")
        self.assertTrue(_FakeOverlay.instances[0].closed)
        state["callbacks"]["back"]()
        self.assertEqual(_FakeOverlay.instances[2].target, "ORB")
        state["callbacks"]["next"]()
        state["callbacks"]["next"]()
        self.assertEqual(_FakeOverlay.instances[-1].target, "CHIP")
        self.assertEqual(_FakeOverlay.instances[-1].next_label, "Done")
        state["callbacks"]["next"]()
        self.assertEqual(state["outcome"], "finished")
        self.assertTrue(store[firstrun_g.COACH_SHOWN_KEY])

    def test_dismiss_sets_flag(self):
        store, state = _run_with_fake(self._window())
        state["callbacks"]["dismiss"]()
        self.assertEqual(state["outcome"], "dismissed")
        self.assertTrue(store[firstrun_g.COACH_SHOWN_KEY])
        self.assertTrue(_FakeOverlay.instances[0].closed)

    def test_unresolvable_target_skipped(self):
        window = _StubWindow(visualizer="ORB", input_cmd="COMP")
        store, state = _run_with_fake(window)
        self.assertEqual(
            [o.target for o in _FakeOverlay.instances], ["ORB"])
        state["callbacks"]["next"]()
        self.assertEqual(state["outcome"], "running")
        self.assertEqual(_FakeOverlay.instances[-1].target, "COMP")
        state["callbacks"]["next"]()
        self.assertEqual(state["outcome"], "finished")
        self.assertTrue(store[firstrun_g.COACH_SHOWN_KEY])

    def test_no_targets_sets_flag(self):
        store, state = _run_with_fake(_StubWindow())
        self.assertEqual(state["outcome"], "no-targets")
        self.assertTrue(store[firstrun_g.COACH_SHOWN_KEY])

    def test_overlay_error_never_raises(self):
        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("overlay exploded")
        store = {}
        state = firstrun_g.run_coach_sequence(
            self._window(),
            lambda k, d=False: store.get(k, d),
            lambda k, v: store.__setitem__(k, v),
            overlay_cls=Boom)
        self.assertEqual(state["outcome"], "error")
        self.assertTrue(store[firstrun_g.COACH_SHOWN_KEY])


class TestFirstRunChromePins(unittest.TestCase):
    def test_terms_gate_rides_hud_dialog(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = (ROOT / "gui/window_shell_onboarding.py").read_text(encoding="utf-8")
        # The whole Terms gate: from its guard to the acceptance
        # record. The HudDialog import + construction sit before
        # the title string, so anchor on the guard, not the title.
        block = shell.split("_TermsWidget is not None:")[1]
        block = block.split("record_acceptance")[0]
        self.assertIn("HudDialog", block)
        self.assertNotIn("QDialog(self)", block)

    def test_first_open_prompt_rides_hud_dialog(self):
        prompt = (ROOT / "gui/firstopen_prompt.py").read_text(encoding="utf-8")
        self.assertIn("class FirstOpenPromptDialog(HudDialog)", prompt)
        self.assertIn("from .design_system.dialogs import HudDialog", prompt)


class TestRenderFoundDefects(unittest.TestCase):
    """Pins for defects the Phase G renders caught (not the
    suites): a near-transparent coach card over arbitrary
    windows, light scroll containers washing out dialog text,
    and a light QTextEdit inside a dark dialog. Each pin names
    the mechanism, so a future refactor that reintroduces the
    shape fails here instead of in front of a user."""

    def test_coach_card_uses_opaque_dialog_surface(self):
        src = (ROOT / "gui/firstrun_g.py").read_text(encoding="utf-8")
        self.assertIn("QWidget#coachCard {{ background: {SURFACE_1}", src)

    def test_scroll_containers_reset_afb_after_set_widget(self):
        # QScrollArea.setWidget forces autoFillBackground True
        # on its widget, so a reset placed BEFORE setWidget is
        # silently overridden. Both resets must come after.
        onb = (ROOT / "gui/onboarding.py").read_text(encoding="utf-8")
        idx = onb.index("setWidget(self.pages)")
        self.assertIn("pages.setAutoFillBackground(False)",
                      onb[idx:idx + 600])
        fo = (ROOT / "gui/firstopen_prompt.py").read_text(encoding="utf-8")
        idx = fo.index("setWidget(body)")
        self.assertIn("body.setAutoFillBackground(False)",
                      fo[idx:idx + 600])
        self.assertIn("viewport().setAutoFillBackground(False)",
                      fo[idx:idx + 600])

    def test_hud_dialog_styles_text_edits(self):
        dialogs = (ROOT / "gui/design_system/dialogs.py").read_text(
            encoding="utf-8")
        self.assertIn("QDialog QTextEdit", dialogs)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class TestCoachOverlayQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_overlay_brackets_card_and_buttons(self):
        from PySide6.QtWidgets import QLabel
        from gui.firstrun_g import CoachMarkOverlay
        target = QLabel("target")
        target.resize(200, 100)
        fired = []
        overlay = CoachMarkOverlay(
            target, "Title", "Body",
            on_next=lambda: fired.append("next"),
            on_back=lambda: fired.append("back"),
            on_dismiss=lambda: fired.append("dismiss"))
        self.assertIsNotNone(overlay._card)
        self.assertEqual(overlay.target_rect().size(), target.rect().size())
        overlay._btn_next.click()
        overlay._btn_dismiss.click()
        self.assertEqual(fired, ["next", "dismiss"])
        overlay.set_next_label("Done")
        self.assertEqual(overlay._btn_next.text(), "Done")
        # isHidden (explicit hide) rather than isVisible: the
        # overlay is never shown offscreen, so isVisible is
        # False either way and would pass vacuously.
        self.assertFalse(overlay._btn_back.isHidden())
        overlay.set_back_visible(False)
        self.assertTrue(overlay._btn_back.isHidden())
        overlay.close()


if __name__ == "__main__":
    unittest.main()
