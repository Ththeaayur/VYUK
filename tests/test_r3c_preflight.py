"""Round-3 section C fix queue item 10 (cost preflight fail-open).

`_cost_preflight_or_raise` swallowed a ledger READ failure with a bare
`except: pass`, so a corrupt cost ledger meant the turn proceeded
WITHOUT a cap check — the one control that keeps a runaway turn from
burning the user's money silently stopped existing. This suite pins
the fix: a preflight that cannot read the ledger FAILS CLOSED. The
turn is refused with a plain-words message naming the ledger file
and the cause; the failure is logged and marked (the C3-F6 shape:
log line + sticky degraded state the Activity feed renders) so the
user can see why cloud-text turns are paused. The write side is
unchanged: a failed cost WRITE still never breaks a turn (C3-F6).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import cost_ledger as cost_mod
from core.voice import cloud_text_helpers as cloud_mod
from core.voice import local_client_cost as local_mod

GARBAGE = (b"\x00\x01\x02GARBAGE-NOT-SQLITE {{{{\nno equals here\n"
           b"[unclosed" * 8)


def _make_ledger(path: Path) -> None:
    cost_mod.CostLedger(db_path=path).record_turn(
        "local", "llama-3.1-8b-instant", 10, 5)


def _corrupt(path: Path) -> None:
    path.write_bytes(GARBAGE)


def _preflight_state() -> dict:
    # getattr so the suite still RUNS pre-fix (and shows behaviour
    # failures) instead of erroring in setUp on the new API.
    fn = getattr(cost_mod, "preflight_degraded_state", None)
    return fn() if callable(fn) else {}


class _PreflightBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        cost_mod.reset_live_record_degraded()
        reset_preflight = getattr(cost_mod, "reset_preflight_degraded",
                                  None)
        if callable(reset_preflight):
            reset_preflight()
            self.addCleanup(reset_preflight)
        self.addCleanup(cost_mod.reset_live_record_degraded)
        real_cloud = cloud_mod._COST_LEDGER
        real_local = local_mod._COST_LEDGER
        cloud_mod._COST_LEDGER = None
        local_mod._COST_LEDGER = None
        self.addCleanup(lambda: setattr(cloud_mod, "_COST_LEDGER",
                                        real_cloud))
        self.addCleanup(lambda: setattr(local_mod, "_COST_LEDGER",
                                        real_local))
        # No cap env leaks in from the ambient environment.
        for key in ("VYUK_COST_DAILY_CAP_USD",
                    "VYUK_COST_MONTHLY_CAP_USD",
                    "VYUK_COST_PER_TURN_CAP_USD"):
            os.environ.pop(key, None)

    def _ledger_env(self, path: Path, caps: bool = True) -> dict:
        env = {"VYUK_COST_LEDGER_PATH": str(path)}
        if caps:
            env["VYUK_COST_DAILY_CAP_USD"] = "10"
        return env


class TestNotePreflightFailure(_PreflightBase):
    def test_note_records_per_brain(self):
        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("file is not a database"))
        state = _preflight_state()
        self.assertIn("cloud_text", state)
        self.assertEqual(state["cloud_text"]["count"], 1)
        self.assertIn("file is not a database",
                      state["cloud_text"]["last_error"])
        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("file is not a database"))
        self.assertEqual(
            _preflight_state()["cloud_text"]["count"],
            2)

    def test_degraded_state_is_a_copy(self):
        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("ledger down"))
        state = _preflight_state()
        state["cloud_text"]["count"] = 99
        self.assertEqual(
            _preflight_state()["cloud_text"]["count"],
            1)

    def test_empty_when_healthy(self):
        self.assertEqual(_preflight_state(), {})

    def test_record_failures_and_preflight_failures_are_separate(self):
        # A blocked turn lost no row: the two sticky states must
        # never be confused with each other.
        cost_mod.note_record_failure("cloud_text",
                                     RuntimeError("file is not a database"))
        self.assertEqual(_preflight_state(), {})
        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("file is not a database"))
        self.assertEqual(
            cost_mod.live_record_degraded_state()["cloud_text"]["count"],
            1)
        self.assertEqual(
            _preflight_state()["cloud_text"]["count"],
            1)


class TestPreflightFailsClosed(_PreflightBase):
    def test_corrupt_ledger_refuses_with_ledger_and_cause_named(self):
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            with self.assertRaises(cloud_mod._GroqTurnError) as ctx:
                cloud_mod._cost_preflight_or_raise(
                    "llama-3.3-70b-versatile", "hello")
        msg = str(ctx.exception)
        self.assertIn("cost_ledger.sqlite3", msg)
        self.assertIn("file is not a database", msg)
        self.assertIn("paused", msg)

    def test_corrupt_ledger_refuses_with_no_caps_configured(self):
        # No caps is not a loophole: the check itself cannot run.
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        with mock.patch.dict(os.environ,
                             self._ledger_env(path, caps=False)):
            with self.assertRaises(cloud_mod._GroqTurnError):
                cloud_mod._cost_preflight_or_raise(
                    "llama-3.3-70b-versatile", "hello")

    def test_unopenable_ledger_refuses(self):
        # A different failure mode than corruption: the ledger
        # path is a directory, so sqlite cannot open it at all.
        path = self.tmp / "ledger-dir"
        path.mkdir()
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            with self.assertRaises(cloud_mod._GroqTurnError) as ctx:
                cloud_mod._cost_preflight_or_raise(
                    "llama-3.3-70b-versatile", "hello")
        self.assertIn("unable to open database file", str(ctx.exception))

    def test_corrupt_ledger_logs_and_marks(self):
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        lines: list[str] = []
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            with mock.patch.object(cloud_mod, "_log_line",
                                   lambda m: lines.append(m)):
                with self.assertRaises(cloud_mod._GroqTurnError):
                    cloud_mod._cost_preflight_or_raise(
                        "llama-3.3-70b-versatile", "hello")
        self.assertTrue(
            any("Cost cap check unavailable" in line for line in lines),
            f"no refusal log line in {lines!r}")
        self.assertTrue(
            any("cost_ledger.sqlite3" in line for line in lines),
            f"log line names no ledger file: {lines!r}")
        self.assertTrue(
            any("file is not a database" in line for line in lines),
            f"log line names no cause: {lines!r}")
        state = _preflight_state()
        self.assertIn("cloud_text", state)
        self.assertEqual(state["cloud_text"]["count"], 1)
        self.assertIn("file is not a database",
                      state["cloud_text"]["last_error"])
        # A blocked turn lost no row: the lost-row state stays empty.
        self.assertEqual(cost_mod.live_record_degraded_state(), {})

    def test_healthy_ledger_allows_and_stays_clean(self):
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            cloud_mod._cost_preflight_or_raise(
                "llama-3.3-70b-versatile", "hello")
        self.assertEqual(_preflight_state(), {})
        self.assertEqual(cost_mod.live_record_degraded_state(), {})

    def test_healthy_over_cap_still_refuses_with_old_message(self):
        # The existing cap refusal is unchanged by this fix.
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        cost_mod.CostLedger(db_path=path).record_turn(
            "cloud_text", "llama-3.3-70b-versatile", 10, 5,
            cost_usd=0.01)
        env = {"VYUK_COST_LEDGER_PATH": str(path),
               "VYUK_COST_DAILY_CAP_USD": "0.0001"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(cloud_mod._GroqTurnError) as ctx:
                cloud_mod._cost_preflight_or_raise(
                    "llama-3.3-70b-versatile", "hello")
        self.assertIn("Cost cap reached", str(ctx.exception))
        # An over-cap refusal is a verdict, not a ledger failure.
        self.assertEqual(_preflight_state(), {})

    def test_approved_confirm_still_allows(self):
        # A confirm callback that approves post-cap spend still
        # allows — the fail-closed change touches only the
        # ledger-READ failure path.
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        cost_mod.CostLedger(db_path=path).record_turn(
            "cloud_text", "llama-3.3-70b-versatile", 10, 5,
            cost_usd=0.01)
        env = {"VYUK_COST_LEDGER_PATH": str(path),
               "VYUK_COST_DAILY_CAP_USD": "0.0001"}
        with mock.patch.dict(os.environ, env):
            ledger = cost_mod.CostLedger.from_env(
                confirm_callback=lambda warning: True)
            with mock.patch.object(cloud_mod, "_cost_ledger",
                                   lambda: ledger):
                cloud_mod._cost_preflight_or_raise(
                    "llama-3.3-70b-versatile", "hello")
        self.assertEqual(_preflight_state(), {})


class TestPreflightFeedMarker(_PreflightBase):
    def test_marker_names_the_brain_and_the_cause(self):
        from core.activity_feed import read_unified

        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("file is not a database"))
        entries = read_unified(limit=50, data_dir=self.tmp)
        markers = [e for e in entries
                   if e.get("source") == "observability"
                   and e["title"].startswith(
                       "Cost cap check unavailable")]
        self.assertEqual(len(markers), 1)
        self.assertIn("1 Cloud text cap check(s) failed",
                      markers[0]["title"])
        self.assertIn("file is not a database", markers[0]["title"])
        self.assertIn("paused", markers[0]["title"])
        self.assertEqual(markers[0].get("kind"), "degraded")

    def test_no_marker_when_healthy(self):
        from core.activity_feed import read_unified

        entries = read_unified(limit=50, data_dir=self.tmp)
        self.assertEqual(
            [e for e in entries
             if e.get("source") == "observability"], [])

    def test_preflight_marker_is_distinct_from_lost_row_marker(self):
        # Both sticky states render, and neither is mistaken for
        # the other: a blocked turn and a lost row are different
        # facts with different remedies.
        from core.activity_feed import read_unified

        cost_mod.note_preflight_failure(
            "cloud_text", RuntimeError("file is not a database"))
        cost_mod.note_record_failure(
            "local", RuntimeError("file is not a database"))
        entries = read_unified(limit=50, data_dir=self.tmp)
        titles = [e["title"] for e in entries
                  if e.get("source") == "observability"]
        self.assertEqual(len(titles), 2)
        self.assertTrue(any(t.startswith("Cost cap check unavailable")
                            for t in titles))
        self.assertTrue(any(t.startswith("Cost ledger degraded")
                            for t in titles))


class TestSessionGateNeverPosts(_PreflightBase):
    def test_chat_completion_raises_before_post(self):
        from core.voice import cloud_text_turn as turn_mod
        from core.voice.cloud_text_turn import GroqSessionTurnMixin

        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        posted: list[str] = []
        mixin = GroqSessionTurnMixin()
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            with mock.patch.object(
                    turn_mod, "_post_json",
                    lambda *a, **k: posted.append("called")
                    or (200, {})), \
                mock.patch.object(turn_mod, "_groq_model",
                                  lambda: "llama-3.3-70b-versatile"), \
                mock.patch.object(turn_mod, "_groq_api_key",
                                  lambda: "test-key"), \
                mock.patch.object(turn_mod, "groq_offered_declarations",
                                  lambda: []):
                with self.assertRaises(cloud_mod._GroqTurnError):
                    mixin._chat_completion([{"role": "user",
                                             "content": "hello"}])
        self.assertEqual(posted, [])

    def test_vision_completion_raises_before_post(self):
        from core.voice import cloud_text_goal as goal_mod
        from core.voice.cloud_text_goal import GroqSessionGoalMixin

        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        posted: list[str] = []
        mixin = GroqSessionGoalMixin()
        mixin._windowed_history = lambda: []
        mixin._system_prompt = ""
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            with mock.patch.object(
                    goal_mod, "_post_json",
                    lambda *a, **k: posted.append("called")
                    or (200, {})), \
                mock.patch.object(goal_mod, "_groq_vision_model",
                                  lambda: "llama-3.2-11b-vision-preview"), \
                mock.patch.object(goal_mod, "_groq_api_key",
                                  lambda: "test-key"):
                with self.assertRaises(cloud_mod._GroqTurnError):
                    mixin._vision_completion(b"\x89PNG fake",
                                             "image/png",
                                             "what is this?")
        self.assertEqual(posted, [])


class TestWriteSideStillNeverBreaksTurn(_PreflightBase):
    _PARSED = {"usage": None,
               "choices": [{"message": {"content": "hi"}}]}

    def test_cloud_write_failure_still_completes(self):
        # C3-F6 behaviour pinned: a failed WRITE never breaks a
        # turn — only the preflight READ fails closed.
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            cloud_mod._cost_record(dict(self._PARSED),
                                   "llama-3.3-70b-versatile", "hello")
        state = cost_mod.live_record_degraded_state()
        self.assertIn("cloud_text", state)
        self.assertEqual(state["cloud_text"]["count"], 1)
        self.assertEqual(_preflight_state(), {})

    def test_local_write_failure_still_completes(self):
        path = self.tmp / "cost_ledger.sqlite3"
        _make_ledger(path)
        _corrupt(path)
        with mock.patch.dict(os.environ, self._ledger_env(path)):
            local_mod._cost_record_local(
                None, "llama-3.1-8b-instant", "hello")
        state = cost_mod.live_record_degraded_state()
        self.assertIn("local", state)
        self.assertEqual(state["local"]["count"], 1)
        self.assertEqual(_preflight_state(), {})


if __name__ == "__main__":
    unittest.main()
