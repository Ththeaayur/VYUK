"""Loophole batch 7 — feedback, quota, setup card, localisation
(2026-11-22).

#6  feedback / crash reports: there was no way to say an answer
    was wrong and no crash capture at all. core/feedback_report
    builds content-free reports — a feedback label + the SHA-256
    of the turn (never the words), a crash signature (type + first
    app-tree frame, never the message). The opt-in is a ConfigSpec
    field (FEEDBACK_REPORTING_ENABLED, default off) and is separate
    from sign-in; the Settings card beside "Report a problem"
    carries it, and the report dialog gains the feedback row
    (label + turn hash) on the last assistant turn. The crash
    hook installs at boot and reads the opt-in live at crash time.
#7  live quota check: cost rows were record-only, so a live
    session could burn the whole cap before anyone noticed.
    core/voice/live_quota runs the ledger's preflight with a
    zero-token estimate (the already-spent fraction): warn at
    80%, block at 100% (confirm-gated), no caps means unchanged.
    start() refuses at the cap, the receive loop warns once per
    session, and the session-status card names COST_WARNING /
    COST_CAP instead of treating a cap refusal as a drop.
#8  skip-everything snooze + #18 per-piece finish-setup card:
    the old card was binary (brain choice or nothing) and Finish
    marked all four stages done, so skipping a piece killed the
    only path back to it. core/setup_pieces tracks the five
    pieces (Google, Gmail, Calendar, Browser, Microphone) as
    done / declined / open; the card names the open pieces and
    shows while any is open; "Remind me later" snoozes it 7
    days, never dismisses it; wizard skips record declines
    durably (mic was already a durable denial).
#17 wizard localisation: the language page implied the whole
    wizard followed the pick; only the chrome does (12 keys, 9
    languages). The page now states its scope plainly
    (LANGUAGE_PAGE_SCOPE_NOTE) and wizard_strings exposes
    localisation_scope() so the promise cannot drift ahead of
    the delivery again.

Runs under both interpreters (sys + Qt). The Home-card,
language-page, report-dialog and Settings-card tests are
Qt-only; everything else is stdlib. Pure tests run against
temp dirs only — the live tree and live data/ are never
touched (the Qt legs reuse the WindowBase / _DialogMixin
harnesses, which restore the QSettings keys they touch).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    from PySide6.QtWidgets import QLabel as _QLabel  # noqa: E402
    from PySide6.QtWidgets import QPushButton as _QPushButton  # noqa: E402
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QT = False

_APP = None
_VA_SNAPSHOT = {}
SettingsAdvCardsMixin = None
BrainServicesMixin = None
# voice_assistant-identity modules, bound in setUpModule.
fr = None        # core.feedback_report
sp = None        # core.setup_pieces
lq = None        # core/voice/live_quota
lc = None        # core/voice/live_client_lifecycle
lc_client = None  # core/voice/live_client
ws = None        # core/wizard_strings
cs = None        # core.config_spec
cfg_mod = None   # core.config
wtrust = None    # gui.window_trust (top-level gui package)
chat_thread_mod = None  # core.chat_thread


def setUpModule():
    """Synthesise the voice_assistant.* alias (the
    test_settings_redesign / batch-6 shape) and import the
    batch-7 modules under it, so the config singleton the
    tests flip is the same one the code under test reads.
    The live data/ dir is never touched: pure tests use temp
    dirs; the Qt legs restore the QSettings keys they touch."""
    global _APP, _VA_SNAPSHOT, SettingsAdvCardsMixin, BrainServicesMixin
    global fr, sp, lq, lc, lc_client, ws, cs, cfg_mod, wtrust
    global chat_thread_mod
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    from voice_assistant.core import config as _cfg  # noqa: E402
    from voice_assistant.core import config_spec as _cs  # noqa: E402
    from voice_assistant.core import feedback_report as _fr  # noqa: E402
    from voice_assistant.core import setup_pieces as _sp  # noqa: E402
    from voice_assistant.core import wizard_strings as _ws  # noqa: E402
    from voice_assistant.core import chat_thread as _ct  # noqa: E402
    from voice_assistant.core.voice import live_quota as _lq  # noqa: E402
    from voice_assistant.core.voice import (  # noqa: E402
        live_client_lifecycle as _lc)
    from voice_assistant.core.voice import live_client as _lcc  # noqa: E402
    import gui.window_trust as _wt  # noqa: E402
    cfg_mod, cs, fr, sp, ws = _cfg, _cs, _fr, _sp, _ws
    chat_thread_mod, lq, lc, lc_client = _ct, _lq, _lc, _lcc
    wtrust = _wt
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    try:
        from gui.window_settings_advcards import (  # noqa: E402
            SettingsAdvCardsMixin as _SACM)
        SettingsAdvCardsMixin = _SACM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        SettingsAdvCardsMixin = None
    try:
        from gui.window_brain_services import (  # noqa: E402
            BrainServicesMixin as _BSM)
        BrainServicesMixin = _BSM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        BrainServicesMixin = None


def tearDownModule():
    """Restore the voice_assistant.* modules."""
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    for key, mod in _VA_SNAPSHOT.items():
        sys.modules[key] = mod


# ---------------------------------------------------------------- helpers
class _FakeLedger:
    """A ledger returning a canned preflight verdict."""

    def __init__(self, verdict=None, exc=None):
        self._verdict = verdict
        self._exc = exc

    def preflight(self, brain, model, prompt_tokens=0,
                  completion_tokens=0):
        if self._exc is not None:
            raise self._exc
        return dict(self._verdict)


def _warn_verdict():
    return {"allowed": True, "action": "warn", "degraded": False,
            "requires_confirm": False,
            "warning": "Cost warning: projected spend $8.50 is 85% "
                       "of the $10.00 cap.",
            "spend_today_usd": 8.50, "spend_month_usd": 8.50,
            "estimated_cost_usd": 0.0, "binding_cap_usd": 10.0}


def _blocked_verdict():
    return {"allowed": False, "action": "confirm", "degraded": True,
            "requires_confirm": True,
            "warning": "Cost cap reached: projected spend $10.00 "
                       "meets the $10.00 cap — further cloud spend "
                       "needs explicit confirmation.",
            "spend_today_usd": 10.0, "spend_month_usd": 10.0,
            "estimated_cost_usd": 0.0, "binding_cap_usd": 10.0}


def _allow_verdict():
    return {"allowed": True, "action": "allow", "degraded": False,
            "requires_confirm": False, "warning": None,
            "spend_today_usd": 0.0, "spend_month_usd": 0.0,
            "estimated_cost_usd": 0.0, "binding_cap_usd": None}


class _Grants:
    """A minimal permission_grants stand-in for live_signals."""

    def __init__(self, states):
        self._states = states

    def get_state(self, pid):
        return self._states.get(pid, "unknown")

    def get_record(self, pid):
        return {"state": self._states.get(pid, "unknown")}


class _Base(unittest.TestCase):
    """Shared root: every test reads the tree it lives in."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(fr, "feedback_report did not import")
        self.assertIsNotNone(sp, "setup_pieces did not import")


# ------------------------------------------------------------ #6 feedback
class FeedbackReportTests(_Base):
    """#6: a feedback report carries a label + a turn hash —
    never the words; a crash report carries a signature —
    never the message."""

    def test_feedback_report_shape_and_hash(self):
        turn = "  The capital of France is Paris.  "
        rep = fr.build_feedback_report("helpful", turn)
        self.assertEqual(rep["kind"], "feedback")
        self.assertEqual(rep["label"], "helpful")
        self.assertEqual(rep["turn_hash"],
                         hashlib.sha256(turn.strip().encode("utf-8"))
                         .hexdigest())
        self.assertEqual(rep["app"], "VYUK")
        self.assertNotIn("turn", rep)
        self.assertNotIn("text", rep)
        self.assertNotIn("words", rep)
        self.assertTrue(fr.report_carries_no_content(rep))

    def test_feedback_report_rejects_bad_labels(self):
        # A free-text label could smuggle content in the label
        # itself, so only the allow-list passes. (An empty turn
        # is deliberately allowed — it hashes to the hash of "",
        # still a hash, never content.)
        with self.assertRaises(ValueError):
            fr.build_feedback_report("love it", "some turn")

    def test_crash_report_shape_never_carries_the_message(self):
        sig = fr.build_crash_report("ValueError", "core.foo:bar:123")
        self.assertEqual(sig["kind"], "crash")
        self.assertEqual(sig["signature"],
                         "ValueError at core.foo:bar:123")
        msg = "could not open /home/aayur/secret.txt"
        self.assertNotIn(msg, sig["signature"])
        self.assertNotIn(msg, str(sig))
        self.assertTrue(fr.report_carries_no_content(sig))

    def test_validator_refuses_content(self):
        good = fr.build_feedback_report("wrong", "turn words")
        self.assertTrue(fr.report_carries_no_content(good))
        bad_label = dict(good, label="hate it")
        self.assertFalse(fr.report_carries_no_content(bad_label))
        bad_hash = dict(good, turn_hash="not-a-hash")
        self.assertFalse(fr.report_carries_no_content(bad_hash))
        with_content = dict(good, turn="the words themselves")
        self.assertFalse(fr.report_carries_no_content(with_content))
        self.assertFalse(fr.report_carries_no_content("not a dict"))
        self.assertFalse(fr.report_carries_no_content(
            {"kind": "crash",
             "signature": "ValueError leaked the secret message"}))

    def test_crash_hook_gated_on_the_opt_in(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_crash_"))
        path = tmp / "crash_signatures.jsonl"
        cfg = cfg_mod.config
        prior = bool(getattr(cfg, "feedback_reporting_enabled", False))
        prev_hook = sys.excepthook
        try:
            cfg.feedback_reporting_enabled = False
            fr.install_crash_hook(path=path)
            try:
                raise ValueError("boom-secret-1")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            self.assertFalse(path.exists(),
                             "opt-in off must record nothing")
            cfg.feedback_reporting_enabled = True
            try:
                raise RuntimeError("boom-secret-2")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 1)
            self.assertIn("RuntimeError", lines[0])
            self.assertNotIn("boom-secret-2", lines[0])
            self.assertNotIn("boom-secret-1", lines[0])
            entry = json.loads(lines[0])
            self.assertTrue(fr.report_carries_no_content(entry))
        finally:
            sys.excepthook = prev_hook
            cfg.feedback_reporting_enabled = prior

    def test_crash_hook_records_unknown_location(self):
        # A crash raised entirely outside the tree (no app frame
        # at all — here, a bare None traceback) still records,
        # with the location honestly marked unknown, instead of
        # vanishing silently.
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_crash_"))
        path = tmp / "crash_signatures.jsonl"
        cfg = cfg_mod.config
        prior = bool(getattr(cfg, "feedback_reporting_enabled", False))
        try:
            cfg.feedback_reporting_enabled = True
            fr._crash_hook(RuntimeError, RuntimeError("outside"),
                           None, path=path)
            self.assertTrue(path.exists())
            entry = json.loads(
                path.read_text(encoding="utf-8").strip().split("\n")[0])
            self.assertEqual(entry["signature"],
                             "RuntimeError at unknown:unknown:0")
            self.assertTrue(fr.report_carries_no_content(entry))
        finally:
            cfg.feedback_reporting_enabled = prior

    def test_crash_signatures_capped_at_fifty_lines(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_crash_"))
        path = tmp / "crash_signatures.jsonl"
        for i in range(55):
            fr.record_crash_signature(
                "ValueError", f"core.foo:bar:{i}", path=path)
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 50)
        self.assertIn(":54", lines[-1])

    def test_count_crash_signatures_is_read_only(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_crash_"))
        path = tmp / "crash_signatures.jsonl"
        self.assertEqual(fr.count_crash_signatures(path=path), 0)
        self.assertFalse(path.exists(),
                         "counting must never create the file")
        fr.record_crash_signature("ValueError", "core.foo:bar:1",
                                  path=path)
        fr.record_crash_signature("TypeError", "core.foo:bar:2",
                                  path=path)
        self.assertEqual(fr.count_crash_signatures(path=path), 2)

    def test_opt_in_is_a_config_field_default_off(self):
        fields = {f.name: f
                  for f in dataclasses.fields(cs.ConfigSpec)}
        self.assertIn("feedback_reporting_enabled", fields)
        self.assertIs(
            fields["feedback_reporting_enabled"].default, False)
        self.assertEqual(
            fields["feedback_reporting_enabled"].metadata.get("env"),
            "FEEDBACK_REPORTING_ENABLED")

    def test_opt_in_never_appears_in_sign_in_modules(self):
        # The opt-in is separate from first-run sign-in: no
        # first-run / sign-in module may reference it.
        for rel in ("gui/onboarding.py", "gui/onboarding_flow.py",
                    "gui/onboarding_pages.py",
                    "core/gemini_oauth.py",
                    "core/google_services_token.py"):
            text = (ROOT / rel).read_text(encoding="utf-8",
                                          errors="replace")
            self.assertNotIn("feedback_reporting_enabled", text, rel)

    def test_bundle_merges_feedback_and_crash(self):
        feedback = fr.build_feedback_report("not_helpful", "a turn")
        crash = fr.build_crash_report("TypeError", "core.x:y:9")
        bundle = wtrust.build_report_bundle(
            feedback=feedback, crash=crash)
        self.assertEqual(bundle["feedback"], feedback)
        self.assertEqual(bundle["crash"], crash)
        self.assertIn("version", bundle)

    def test_bundle_refuses_a_content_carrying_report(self):
        bad = {"kind": "feedback", "label": "helpful",
               "turn_hash": "0" * 64, "turn": "the words"}
        with self.assertRaises(ValueError):
            wtrust.build_report_bundle(feedback=bad)

    def test_last_assistant_turn(self):
        thread = chat_thread_mod.ChatThread()
        self.assertEqual(wtrust.last_assistant_turn(thread), "")
        thread.add_user("hello")
        thread.add_assistant("first answer")
        thread.add_user("again")
        thread.add_assistant("second answer")
        self.assertEqual(wtrust.last_assistant_turn(thread),
                         "second answer")
        # A streaming (unfinished) turn is not the last answer.
        thread.add_assistant("partial", status="streaming")
        self.assertEqual(wtrust.last_assistant_turn(thread),
                         "second answer")


# -------------------------------------------------------------- #7 quota
class LiveQuotaTests(_Base):
    """#7: the live quota check warns at 80%, blocks at the cap,
    allows with no caps, and fails closed when the ledger cannot
    be read."""

    def test_warn_at_85_percent(self):
        w = lq.live_quota_check(ledger=_FakeLedger(_warn_verdict()),
                                model="gemini-3.8-live")
        self.assertTrue(w["allowed"])
        self.assertEqual(w["action"], "warn")
        self.assertIsNotNone(w["warning"])
        self.assertIn("85%", w["warning"])

    def test_blocked_at_cap_requires_confirm(self):
        b = lq.live_quota_check(ledger=_FakeLedger(_blocked_verdict()),
                                model="gemini-3.8-live")
        self.assertFalse(b["allowed"])
        self.assertTrue(b["requires_confirm"])
        self.assertEqual(b["action"], "confirm")

    def test_allow_with_no_caps(self):
        a = lq.live_quota_check(ledger=_FakeLedger(_allow_verdict()),
                                model="gemini-3.8-live")
        self.assertTrue(a["allowed"])
        self.assertIsNone(a["warning"])
        self.assertIsNone(a["binding_cap_usd"])

    def test_ledger_failure_fails_closed(self):
        f = lq.live_quota_check(
            ledger=_FakeLedger(exc=RuntimeError("ledger unreadable")),
            model="gemini-3.8-live")
        self.assertFalse(f["allowed"])
        self.assertIsNotNone(f["warning"])
        self.assertIn("ledger", f["warning"].lower())

    def test_start_refuses_at_cap(self):
        statuses = []
        session = object.__new__(lc_client.GeminiLiveSession)
        session.on_status_change = statuses.append
        session._running = False
        session._quota_warned = False
        orig_auth = lc.resolve_auth
        orig_factory = lq._LEDGER_FACTORY
        lc.resolve_auth = lambda: ("key", "fake-key")
        lq._LEDGER_FACTORY = lambda: _FakeLedger(_blocked_verdict())
        try:
            asyncio.run(session.start(audio_manager=None))
        finally:
            lc.resolve_auth = orig_auth
            lq._LEDGER_FACTORY = orig_factory
        self.assertIs(session._running, False)
        self.assertTrue(statuses)
        self.assertTrue(statuses[0].startswith("COST_CAP"))

    def test_mid_session_warn_fires_once(self):
        first = lq.live_quota_after_turn(
            ledger=_FakeLedger(_warn_verdict()),
            model="gemini-3.8-live", warned=False)
        second = lq.live_quota_after_turn(
            ledger=_FakeLedger(_warn_verdict()),
            model="gemini-3.8-live", warned=True)
        self.assertIsNotNone(first)
        self.assertIn("85%", first)
        self.assertIsNone(second)


# --------------------------------------------------- #8 + #18 pieces
class SetupPiecesTests(_Base):
    """#8 + #18: the per-piece setup card — open pieces keep it
    up, declines resolve, the snooze is 7 days, never forever."""

    def test_pieces_iterate_as_ids(self):
        # Regression: SETUP_PIECES once yielded (id, label)
        # tuples, so a signals dict built from it keyed by tuple
        # and every piece read open forever.
        self.assertEqual(list(sp.SETUP_PIECES),
                         ["google", "gmail", "calendar", "browser",
                          "mic"])
        signals = {p: True for p in sp.SETUP_PIECES}
        self.assertEqual(
            sp.unfinished_pieces(signals, set()), [])

    def test_all_open_card_shows(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        states = sp.piece_states(all_open, set())
        self.assertTrue(all(v == "open" for v in states.values()))
        self.assertTrue(sp.card_visible(
            onboarded=True, signals=all_open, declines=set(),
            snoozed_until=None))
        self.assertEqual(
            len(sp.unfinished_pieces(all_open, set())), 5)

    def test_decline_resolves_one_piece(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        states = sp.piece_states(all_open, {"google"})
        self.assertEqual(states["google"], "declined")
        unfin = sp.unfinished_pieces(all_open, {"google"})
        self.assertNotIn("google", unfin)
        self.assertEqual(len(unfin), 4)

    def test_all_done_hides_the_card(self):
        all_done = {p: True for p in sp.SETUP_PIECES}
        self.assertFalse(sp.card_visible(
            onboarded=True, signals=all_done, declines=set(),
            snoozed_until=None))

    def test_all_declined_hides_the_card(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        self.assertFalse(sp.card_visible(
            onboarded=True, signals=all_open,
            declines=set(sp.SETUP_PIECES), snoozed_until=None))

    def test_done_beats_a_decline(self):
        # The user declined, then completed it — the completion
        # stands.
        signals = {p: False for p in sp.SETUP_PIECES}
        signals["google"] = True
        states = sp.piece_states(signals, {"google"})
        self.assertEqual(states["google"], "done")

    def test_fresh_install_shows_no_card(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        self.assertFalse(sp.card_visible(
            onboarded=False, signals=all_open, declines=set(),
            snoozed_until=None))

    def test_snooze_hides_for_seven_days_then_returns(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        future = sp.snooze_until(days=7)
        past = sp.snooze_until(days=-1)
        self.assertFalse(sp.card_visible(
            onboarded=True, signals=all_open, declines=set(),
            snoozed_until=future))
        self.assertTrue(sp.card_visible(
            onboarded=True, signals=all_open, declines=set(),
            snoozed_until=past))
        self.assertEqual(sp.SNOOZE_DAYS, 7)

    def test_decline_round_trips_through_the_store(self):
        store = {}
        sp.write_declines(store, {"google", "browser"})
        self.assertEqual(sp.read_declines(store),
                         {"google", "browser"})
        sp.write_declines(store, {"gmail"})
        self.assertEqual(sp.read_declines(store),
                         {"google", "browser", "gmail"})
        # Unknown ids never persist.
        sp.write_declines(store, {"not_a_piece"})
        self.assertNotIn("not_a_piece", sp.read_declines(store))

    def test_live_signals_read_tokens_and_grants(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_pieces_"))
        (tmp / "gemini_token.json").write_text(
            json.dumps({"access_token": "fake"}), encoding="utf-8")
        (tmp / "google_token.json").write_text(
            json.dumps({"access_token": "fake"}), encoding="utf-8")
        sig = sp.live_signals(
            data_dir=tmp,
            grants=_Grants({"browser": "granted",
                            "microphone": "granted"}))
        self.assertTrue(all(sig.values()))
        empty = pathlib.Path(tempfile.mkdtemp(prefix="b7_empty_"))
        sig_empty = sp.live_signals(data_dir=empty, grants=_Grants({}))
        self.assertFalse(any(sig_empty.values()))

    def test_live_signals_mic_denial_is_a_decline(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="b7_pieces_"))
        declines = sp.live_declines(
            store={}, grants=_Grants({"microphone": "denied"}))
        self.assertIn("mic", declines)

    def test_card_state_names_the_open_pieces(self):
        all_open = {p: False for p in sp.SETUP_PIECES}
        state = sp.setup_card_state(
            onboarded=True, signals=all_open, declines={"google"},
            snoozed_until=None)
        self.assertTrue(state["visible"])
        self.assertEqual(state["unfinished"],
                         ["gmail", "calendar", "browser", "mic"])
        self.assertIn("Browser", state["unfinished_labels"])
        self.assertIn("Microphone", state["unfinished_labels"])
        self.assertNotIn("Google sign-in",
                         state["unfinished_labels"])

    def test_qsettings_keys_registered(self):
        names = [f[0] for f in cs.QSETTINGS_FIELDS]
        self.assertIn("firstrun_pieces_declined", names)
        self.assertIn("firstrun_setup_card_snoozed_until", names)
        by_name = {f[0]: f for f in cs.QSETTINGS_FIELDS}
        self.assertEqual(by_name["firstrun_pieces_declined"][1],
                         "firstrun/pieces_declined")
        self.assertEqual(
            by_name["firstrun_setup_card_snoozed_until"][1],
            "firstrun/setup_card_snoozed_until")


# ----------------------------------------------------- #17 localisation
class LocalisationScopeTests(_Base):
    """#17: the wizard says plainly which parts are translated."""

    def test_scope_lists_only_the_chrome(self):
        scope = ws.localisation_scope()
        chrome_keys = ("continue", "back", "skip", "finish", "not_now",
                       "advanced", "stage_interaction", "stage_brain",
                       "stage_permissions", "stage_first_mission",
                       "hello", "sample")
        self.assertEqual(scope["translated"], sorted(chrome_keys))
        self.assertIn("page titles", scope["not_translated"])
        self.assertIn("page bodies", scope["not_translated"])
        self.assertIn("help text", scope["not_translated"])

    def test_scope_note_states_it_on_the_page(self):
        self.assertIn("stay in English",
                      ws.LANGUAGE_PAGE_SCOPE_NOTE)
        self.assertIn("Buttons and stage names",
                      ws.LANGUAGE_PAGE_SCOPE_NOTE)
        page = (ROOT / "gui/onboarding_pages.py").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("LANGUAGE_PAGE_SCOPE_NOTE", page)


# ------------------------------------------------------- counts / census
class CountsCensusTests(_Base):
    """The batch-7 pins: ConfigSpec 111/106, census 758."""

    def test_configspec_counts(self):
        fields = dataclasses.fields(cs.ConfigSpec)
        env_backed = [f for f in fields if "env" in f.metadata]
        self.assertEqual(len(fields), 111)
        self.assertEqual(len(env_backed), 106)

    def test_census_unmoved(self):
        ev = json.loads((ROOT / "tests/_classification_evidence.json")
                        .read_text(encoding="utf-8"))
        snap = json.loads((ROOT / "tests/_classification_snapshot.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(ev), 764)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(snap), 764)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(set(ev), set(snap))


# ------------------------------------------------------------ Qt: Home
@unittest.skipUnless(_QT, "Qt-only: the Home card needs PySide6")
class HomeCardTests(unittest.TestCase):
    """#8 + #18 (Qt): the Home setup card names the open pieces
    and its \"Remind me later\" snoozes it for 7 days."""

    def test_card_names_open_pieces_and_snoozes(self):
        from tests.test_chat_ui import WindowBase

        case = WindowBase("setUp")
        case.setUp()
        try:
            from voice_assistant.core.gui_facade import (
                qsettings_get, qsettings_set)
            priors = {}
            for key in ("onboarded", "setup/brain_choice",
                        "firstrun/pieces_declined",
                        "firstrun/setup_card_snoozed_until"):
                try:
                    priors[key] = qsettings_get(key)
                except Exception:  # noqa: BLE001
                    priors[key] = None
            qsettings_set("onboarded", True)
            qsettings_set("setup/brain_choice", "")
            qsettings_set("firstrun/pieces_declined", "")
            qsettings_set("firstrun/setup_card_snoozed_until", "")
            try:
                case.window._refresh_setup_cards()
                case.app.processEvents()
                card = getattr(case.window, "setup_card", None)
                self.assertIsNotNone(card)
                self.assertTrue(card.isVisible())
                body = " | ".join(
                    lbl.text() for lbl in card.findChildren(_QLabel))
                for piece in ("Google sign-in", "Browser",
                              "Microphone"):
                    self.assertIn(piece, body)
                snooze_btn = None
                for btn in card.findChildren(_QPushButton):
                    if btn.accessibleName() == "Remind me later":
                        snooze_btn = btn
                self.assertIsNotNone(snooze_btn,
                                     "no 'Remind me later' button")
                snooze_btn.click()
                case.app.processEvents()
                self.assertFalse(card.isVisible())
                stored = str(qsettings_get(
                    "firstrun/setup_card_snoozed_until") or "")
                self.assertTrue(stored)
            finally:
                for key, val in priors.items():
                    try:
                        qsettings_set(key, val)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            case.tearDown()


@unittest.skipUnless(_QT, "Qt-only: the wizard dialog needs PySide6")
class LanguagePageTests(unittest.TestCase):
    """#17 (Qt): the wizard language page carries the scope note."""

    def test_language_page_carries_the_scope_note(self):
        import tests.test_r3wizard_expand as _wmod
        from tests.test_r3wizard_expand import _DialogMixin

        # The module populates OnboardingDialog in setUpModule
        # (unittest runs it; a plain import leaves it None).
        _wmod.setUpModule()

        class _DlgCase(_DialogMixin, unittest.TestCase):
            pass

        case = _DlgCase()
        dlg = case._make_dialog()
        try:
            page = getattr(dlg, "_language_page", None)
            self.assertIsNotNone(page)
            texts = [lbl.text()
                     for lbl in page.findChildren(_QLabel)]
            self.assertTrue(any("stay in English" in t for t in texts),
                            f"no scope note among {len(texts)} labels")
            note = getattr(dlg, "ob_language_scope_note", None)
            self.assertIsNotNone(note)
            self.assertEqual(note.text(),
                             ws.LANGUAGE_PAGE_SCOPE_NOTE)
        finally:
            try:
                case.tearDown()
            except Exception:  # noqa: BLE001
                try:
                    dlg.close()
                except Exception:  # noqa: BLE001
                    pass


@unittest.skipUnless(_QT, "Qt-only: the report dialog needs PySide6")
class ReportDialogTests(unittest.TestCase):
    """#6 (Qt): the report-problem dialog's feedback row adds a
    label + turn hash to the frozen bundle — and only when a
    turn was given."""

    def test_feedback_row_adds_label_and_hash(self):
        turn = "The capital of France is Paris."
        dlg, widgets = wtrust.build_report_problem_dialog(
            turn_text=turn)
        # A parentless QDialog closed but never deleted aborts
        # the process at interpreter teardown (heap corruption —
        # the wrapper's destruction collides with the app
        # teardown). The established suite pattern deletes it
        # via cleanup, before the test ends.
        self.addCleanup(dlg.deleteLater)
        try:
            self.assertIsNotNone(widgets["feedback_group"])
            self.assertEqual(len(widgets["feedback_radios"]), 3)
            labels = [rb.accessibleName()
                      for rb in widgets["feedback_radios"]]
            self.assertEqual(labels, ["Feedback: helpful",
                                      "Feedback: not_helpful",
                                      "Feedback: wrong"])
            # No label picked yet: no feedback in the bundle.
            bundle = widgets["state"]["prepared"]["bundle"]
            self.assertNotIn("feedback", bundle)
            widgets["feedback_radios"][2].click()
            bundle = widgets["state"]["prepared"]["bundle"]
            feedback = bundle.get("feedback")
            self.assertIsNotNone(feedback)
            self.assertEqual(feedback["label"], "wrong")
            self.assertEqual(feedback["turn_hash"],
                             hashlib.sha256(turn.encode("utf-8"))
                             .hexdigest())
            self.assertNotIn("turn", feedback)
            preview = widgets["preview"].toPlainText()
            self.assertIn("wrong", preview)
            self.assertIn(feedback["turn_hash"], preview)
        finally:
            dlg.close()

    def test_no_turn_no_feedback_row(self):
        dlg, widgets = wtrust.build_report_problem_dialog()
        self.addCleanup(dlg.deleteLater)
        try:
            self.assertIsNone(widgets["feedback_group"])
            self.assertEqual(widgets["feedback_radios"], [])
            bundle = widgets["state"]["prepared"]["bundle"]
            self.assertNotIn("feedback", bundle)
        finally:
            dlg.close()


@unittest.skipUnless(_QT, "Qt-only: the Settings card needs PySide6")
class FeedbackCardTests(unittest.TestCase):
    """#6 (Qt): the Settings feedback card — toggle + exact copy
    + status line, beside \"Report a problem\". The harness
    combines the two mixins the real window combines, so a
    missing handler or a broken build fails here exactly as it
    would in Settings (loophole batch 5's lesson)."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(
            SettingsAdvCardsMixin, "adv-cards mixin did not import")
        self.assertIsNotNone(
            BrainServicesMixin, "brain-services mixin did not import")

        class _Harness(SettingsAdvCardsMixin, BrainServicesMixin):
            pass

        self.harness = _Harness()
        self.card_subtitles = {}

        def _record_card(_layout, _title, _subtitle=None):
            self.card_subtitles[_title] = _subtitle or ""
            return _QVBoxLayout()

        self.harness._settings_card = _record_card
        self.harness._brain_engine = lambda: "Gemini"
        self.harness._open_data_folder = lambda: None
        self.harness._show_recent_activity = lambda: None
        self.harness.signals = None
        for _name in (
                "_w10_export_data", "_w10_import_data",
                "_w10_refresh_models", "_w10_remove_model",
                "_export_settings", "_import_settings",
                "_open_activity_feed", "_open_cost_ledger",
                "_open_egress_ledger", "_open_memory_panel",
                "_open_report_problem", "_open_templates",
                "_open_today", "_open_trust_status",
                "_open_unified_activity", "_show_page"):
            setattr(self.harness, _name, lambda *a, **k: None)

    def _build(self):
        layout = _QVBoxLayout()
        self.harness._build_adv_cards2(layout)
        return self.harness

    def test_card_toggle_flips_the_opt_in(self):
        from voice_assistant.core.gui_facade import config as _cfg

        prior = bool(getattr(_cfg, "feedback_reporting_enabled",
                             False))
        try:
            _cfg.feedback_reporting_enabled = False
            h = self._build()
            chk = getattr(h, "chk_feedback_reporting", None)
            self.assertIsNotNone(chk)
            self.assertFalse(chk.isChecked())
            status = getattr(h, "_feedback_reporting_status", None)
            self.assertIsNotNone(status)
            self.assertIn("Off", status.text())
            chk.setChecked(True)
            self.assertTrue(
                bool(getattr(_cfg, "feedback_reporting_enabled",
                             False)))
            self.assertIn("On", status.text())
            chk.setChecked(False)
            self.assertFalse(
                bool(getattr(_cfg, "feedback_reporting_enabled",
                             False)))
            self.assertIn("Off", status.text())
        finally:
            _cfg.feedback_reporting_enabled = prior

    def test_card_carries_the_exact_copy(self):
        # The exact sentence the user reads on the card — the
        # joined subtitle, as rendered, not the source's split
        # literals — plus the toggle's own label.
        h = self._build()
        copy = self.card_subtitles.get("Feedback & crash reports")
        self.assertIsNotNone(copy)
        self.assertEqual(
            copy,
            "Sends only the feedback label and a hash of the turn "
            "— never the words. Crash reports carry only the crash "
            "signature — never what you said or did. Off unless "
            "you turn it on; separate from sign-in.")
        chk = getattr(h, "chk_feedback_reporting", None)
        self.assertIsNotNone(chk)
        self.assertEqual(chk.text(), "Send feedback & crash reports")


if __name__ == "__main__":
    unittest.main()
