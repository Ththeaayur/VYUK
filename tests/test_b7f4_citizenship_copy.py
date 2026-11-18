"""B7F4: the "Desktop citizenship" card reads in plain words
(Build 7 Finding 4).

The finding: the card (Settings, Connections group, beside
Presence) said "Not checked yet" / "Still owed" and named
internal machinery in user-facing copy — "wiring step",
"this module", QSettings org "NovaAI" / app "Nova" — and
carried "(Source: ...)" parentheticals on every checklist row.
The ruling is REPHRASE FOR THE USER, keep the honesty:
status words are "On" / "Not confirmed yet"; the "(Source: ...)"
parentheticals go; internal words go; "Still owed" becomes
"Still to come" with the plain line "a one-click clean
uninstaller — we'll update this line when it's ready"; the
uninstall paths stay factual but phrased plainly. Nothing
that is not yet done may read as done. Copy changes only:
no behaviour change, no layout change.

Qt only: the whole suite needs widgets, so without PySide6 every
class skips (the test_chat_ui / test_rethink_build6 shape).
"""

import time
import unittest

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401 — availability probe only
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton
    from gui.pf_citizenship import (
        AutostartConsent,
        citizenship_model,
        CitizenshipWidget,
        clean_uninstall_info,
        CONSENT_DECLINED,
        CONSENT_GRANTED,
    )
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    # TestCase, not object: the loader only collects TestCase
    # subclasses, so an object base would run ZERO tests under
    # sys (rc 5, "NO TESTS RAN") instead of skipped — the
    # test_rethink_build6 shape, where the mixin is a TestCase.
    WindowBase = unittest.TestCase  # type: ignore[assignment]

# Phrases no user-facing line on the card may contain (B7F4).
BANNED = (
    "wiring step",
    "this module",
    "this text must be updated",
    "Still owed",
    "Not checked yet",
    "QSettings",
    "NovaAI",
    "Nova",
    "(Source:",
)


def _pump(app, seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()


def _widget_texts(widget):
    """Every rendered string under a widget: labels, buttons,
    checkboxes. What the user reads, nothing else."""
    texts = []
    for cls in (QLabel, QPushButton, QCheckBox):
        for w in widget.findChildren(cls):
            t = w.text()
            if t:
                texts.append(t)
    return texts


@unittest.skipUnless(_QT, "Qt unavailable")
class CitizenshipCopyTests(WindowBase):
    """The real MainWindow offscreen (WindowBase), Settings page
    open, the citizenship card as the user sees it."""

    def setUp(self):
        super().setUp()
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot skip is best-effort
            pass
        self.window._show_page(4)
        self.app.processEvents()
        _pump(self.app, 0.8)
        self._kept = []

    def _card(self):
        """The citizenship card on the Settings page (Connections
        group, beside Presence)."""
        card = getattr(self.window, "_w9_citizenship_widget", None)
        self.assertIsNotNone(card,
                             "citizenship widget not built on the "
                             "Settings page")
        return card

    def test_no_source_parentheticals(self):
        # The ruling: "(Source: ...)" parentheticals go — the
        # checklist rows read as plain status lines.
        for check in citizenship_model():
            self.assertNotIn("(Source:", check.line(),
                             f"row {check.check_id!r} still carries "
                             f"a source parenthetical: {check.line()!r}")
        for text in _widget_texts(self._card()):
            self.assertNotIn("(Source:", text,
                             f"rendered card line still carries a "
                             f"source parenthetical: {text!r}")

    def test_no_internal_words_on_card(self):
        # No internal machinery names anywhere the user can read
        # on the card: no "wiring step", no "this module", no
        # QSettings org/app names, no "Still owed", no
        # "Not checked yet".
        for text in _widget_texts(self._card()):
            for phrase in BANNED:
                self.assertNotIn(
                    phrase, text,
                    f"card still shows {phrase!r} in: {text!r}")

    def test_status_words_are_ruled_ones(self):
        # The ruled vocabulary: "On" for live rows, "Not
        # confirmed yet" for rows no live read has confirmed.
        checks = {c.check_id: c for c in citizenship_model()}
        self.assertTrue(checks["tray_quit"].line().startswith("On — "),
                        checks["tray_quit"].line())
        self.assertTrue(
            checks["close_to_tray"].line().startswith(
                "Not confirmed yet — "),
            checks["close_to_tray"].line())
        self.assertTrue(
            checks["monitor"].line().startswith(
                "Not confirmed yet — "),
            checks["monitor"].line())
        rendered = " ".join(_widget_texts(self._card()))
        self.assertIn("Not confirmed yet", rendered)
        self.assertNotIn("Not checked yet", rendered)

    def test_still_to_come_line(self):
        # The ruling's plain line, verbatim: "Still owed" is
        # "Still to come", with the one-click uninstaller named
        # as coming, not done.
        rendered = " ".join(_widget_texts(self._card()))
        self.assertIn(
            "Still to come: A one-click clean uninstaller — "
            "we'll update this line when it's ready.",
            rendered)
        self.assertNotIn("Still owed", rendered)

    def test_uninstall_paths_plain_and_factual(self):
        # The uninstall paths stay factual but phrased plainly:
        # settings live in the Windows registry under VYUK's
        # entry — no "QSettings", no org/app names — and the
        # manual steps name the registry, not "QSettings keys".
        info = clean_uninstall_info()
        for key, value in info.items():
            for phrase in BANNED:
                self.assertNotIn(
                    phrase, value,
                    f"clean_uninstall_info[{key!r}] still shows "
                    f"{phrase!r}: {value!r}")
        self.assertIn("Windows registry", info["settings_location"])
        self.assertIn("Windows registry", info["manual_steps"])
        rendered = " ".join(_widget_texts(self._card()))
        self.assertIn("Windows registry", rendered)

    def test_honesty_holds(self):
        # The rephrase must not make anything read as done: the
        # unconfirmed rows are "Not confirmed yet", never "On";
        # the uninstaller is "Still to come", never present;
        # the default consent is "Not asked yet".
        checks = {c.check_id: c for c in citizenship_model()}
        for check_id in ("close_to_tray", "monitor", "mini_orb"):
            self.assertEqual(checks[check_id].status, "unknown")
            self.assertNotIn("On —", checks[check_id].line())
        rendered = " ".join(_widget_texts(self._card()))
        self.assertNotIn("one-click clean uninstaller is ready",
                         rendered)
        widget = CitizenshipWidget()
        self._kept.append(widget)
        consent_text = " ".join(_widget_texts(widget))
        self.assertIn("Not asked yet", consent_text)

    def test_consent_lines_plain(self):
        # The consent record lines (checkbox branch, tests and
        # reuse) name no internal machinery either: the Windows
        # startup setting lives in Presence; this card only
        # records the say-so.
        for state in (CONSENT_GRANTED, CONSENT_DECLINED):
            consent = AutostartConsent(state=state)
            for phrase in BANNED:
                self.assertNotIn(phrase, consent.line(),
                                 f"consent {state!r} still shows "
                                 f"{phrase!r}: {consent.line()!r}")
            self.assertIn("lives in Presence", consent.line())
        note = AutostartConsent().set_consent(CONSENT_GRANTED)["note"]
        for phrase in BANNED:
            self.assertNotIn(phrase, note,
                             f"consent note still shows {phrase!r}: "
                             f"{note!r}")

    def test_card_title_and_subtitle_unchanged(self):
        # The rephrase touches the rows and the uninstall block,
        # not the card's own name: still "Desktop citizenship",
        # still the plain subtitle.
        rendered = " ".join(_widget_texts(self._card()))
        self.assertIn("Desktop citizenship", rendered)
        self.assertIn("How VYUK lives on this PC", rendered)
