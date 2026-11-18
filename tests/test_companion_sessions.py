"""Companion-session capability tests (2026-11-05).

The holder (core/brain/companion_sessions.py) starts alongside the
primary session and holds the (Groq, local) pair; the
TurnCoordinator construction site supplies it as
second_opinion_brains through the EXISTING factory. These tests
prove, with fakes only (lab fakes belong in TESTS — production
legs are real sessions or None):

(a) the holder starts the pair alongside a fake primary without
    disturbing the primary inner / fallback / standby;
(b) the pair is None when the Groq key is absent (check stays off);
(c) the pair is None when the local model is unavailable;
(d) the pair supplied to the coordinator is the factory's exact
    shape (Groq _chat_completion + local _ollama_chat/_llama_chat,
    distinct engines);
(e) a destructive + side-effecting step triggers the check before
    dispatch with the real pair shape (holder -> factory ->
    coordinator, as the existing suites inject brains);
(f) the holder releases both sessions cleanly on stop.
Plus: the tail wiring (construction supply + stashed refresh +
never overwrite + explicit coordinator untouched) and a DISPATCH
PIN for the factory's resident-first order (factory dispatch fix
2026-11-05: _llama_chat first, _ollama_chat fallback — the old
_ollama_chat-first order asked a real builtin leg via Ollama HTTP
and never reached its resident model).

Stdlib unittest only. No live session is started; no store is
touched; no network is attempted (the Ollama fake raises
URLError without touching a socket).
"""

import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from core.brain import companion_sessions as cs
from core.brain import task_coordinator as tc
from core.brain.task_coordinator_tail import (
    _companion_second_opinion_brains,
    _fill_second_opinion_brains,
    handle_typed_turn,
)
from core.w8_wiring_opinion import second_opinion_from_sessions
from core.second_opinion import cross_check


# ------------------------------------------------------------------ fakes
class _FakeGroq:
    """A Groq-shaped leg: _chat_completion + stop()."""

    def __init__(self, answer="YES"):
        self.answer = answer
        self.stopped = False
        self.chat_calls = []

    def _chat_completion(self, messages):
        self.chat_calls.append(messages)
        return (200, {"choices": [{"message": {"content": self.answer}}]})

    def stop(self):
        self.stopped = True


class _FakeLocalBuiltin:
    """A builtin-shaped local leg: _llama_chat + preload only.

    No _ollama_chat — the factory's _llama_chat branch is reached.
    """

    def __init__(self, answer="YES", preload_ok=True):
        self.answer = answer
        self.preload_ok = preload_ok
        self._llama = None
        self._whisper = None
        self.preload_calls = 0
        self.llama_calls = []

    def preload_model_only(self):
        self.preload_calls += 1
        if self.preload_ok:
            self._llama = object()
            return True
        return False

    def _llama_chat(self, messages):
        self.llama_calls.append(messages)
        return self.answer


class _FakeLocalRealShape:
    """A REAL LocalLiveSession's shape: BOTH chat methods.

    _ollama_chat raises URLError (no server) exactly as a real
    builtin session's would; _llama_chat answers — the factory
    asks it FIRST (resident-first dispatch, see the dispatch
    pin) and only falls back to _ollama_chat on failure or an
    empty result.
    """

    def __init__(self, answer="YES"):
        self.answer = answer
        self._llama = object()  # resident, preloaded
        self._whisper = None
        self.ollama_calls = 0
        self.llama_calls = 0

    def preload_model_only(self):
        return True

    def _ollama_chat(self, messages, ollama_tools):
        self.ollama_calls += 1
        raise urllib.error.URLError("no ollama server")

    def _llama_chat(self, messages):
        self.llama_calls += 1
        return self.answer


class _FakeLocalOllama:
    """An ollama-backend leg: _ollama_chat only, no preload needed."""

    def __init__(self, answer="YES"):
        self.answer = answer
        self.ollama_calls = []

    def _ollama_chat(self, messages, ollama_tools):
        self.ollama_calls.append(messages)
        return {"message": {"content": self.answer}}


class _FakeLocalBoth:
    """A both-methods local leg with controllable backends.

    llama: "answer" (return the answer), "raise" (raise),
    "empty" (return ""). ollama: "dict" (return a dict answer),
    "str" (return a plain string), "raise", "empty" (return a
    dict with empty content), "tripwire" (raise AssertionError —
    the backend must never be contacted). Calls are counted per
    backend so tests prove which backend was asked and whether
    the fallback was (or was not) reached.
    """

    def __init__(self, answer="YES", llama="answer", ollama="dict"):
        self.answer = answer
        self.llama_mode = llama
        self.ollama_mode = ollama
        self.llama_calls = 0
        self.ollama_calls = 0

    def _llama_chat(self, messages):
        self.llama_calls += 1
        if self.llama_mode == "raise":
            raise RuntimeError("resident model not loaded")
        if self.llama_mode == "empty":
            return ""
        return self.answer

    def _ollama_chat(self, messages, ollama_tools):
        self.ollama_calls += 1
        if self.ollama_mode == "tripwire":
            raise AssertionError("ollama must not be contacted")
        if self.ollama_mode == "raise":
            raise urllib.error.URLError("no ollama server")
        if self.ollama_mode == "empty":
            return {"message": {"content": ""}}
        if self.ollama_mode == "str":
            return self.answer
        return {"message": {"content": self.answer}}


class _FakeLocalLite:
    """A Lite-shaped session: no chat method at all — never a leg."""


class _FakePrimary:
    """A fake primary session: inner + standby + fallback state.

    The holder must never touch any of it. The builder returns a
    fresh fake local session per call.
    """

    def __init__(self, local=None):
        self._inner = object()
        self._standby_inner = object()
        self._fallback_reason = None
        self._local = local if local is not None else _FakeLocalBuiltin()
        self.builder_calls = 0

    def _build_local_inner(self):
        self.builder_calls += 1
        return self._local


def _ok_executor():
    def run(tool, args):
        run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    run.calls = []
    return run


def _coord(executor=None, brains=None):
    return tc.TurnCoordinator(
        executor=executor or _ok_executor(),
        second_opinion_brains=brains)


def _task(coord, steps):
    return coord.new_task(
        "t", "saved_plan",
        [tc.StepDef.from_mapping(m) for m in steps])


def _fake_step():
    return SimpleNamespace(label="Delete old logs", tool="delete_file")


def _fake_session():
    """A handle_typed_turn session: no coordinator, no engine."""
    return SimpleNamespace(_task_coordinator=None)


def _patched_holder_key(groq_answer="YES"):
    """Patch the Groq seams so the holder builds a fake Groq leg."""
    groq = _FakeGroq(groq_answer)
    p1 = mock.patch.object(cs, "_groq_key_present", return_value=True)
    p2 = mock.patch.object(cs, "_build_groq_session", return_value=groq)
    return groq, p1, p2


# ------------------------------------------------------------------- holder
class HolderPairTests(unittest.TestCase):
    def test_pair_alongside_fake_primary(self):
        # (a) The holder starts the pair alongside a fake primary
        # without disturbing the primary inner / fallback / standby:
        # it holds only a builder callable, never the primary.
        primary = _FakePrimary()
        inner, standby = primary._inner, primary._standby_inner
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=primary._build_local_inner)
            holder.start()
            pair = holder.pair
        self.assertIsNotNone(pair)
        self.assertIs(pair[0], groq)
        self.assertIs(pair[1], primary._local)
        self.assertIsNot(primary._local, primary._inner)
        self.assertIsNot(primary._local, primary._standby_inner)
        self.assertIs(primary._inner, inner)
        self.assertIs(primary._standby_inner, standby)
        self.assertIsNone(primary._fallback_reason)
        self.assertEqual(holder.missing_legs(), [])
        # The companion leg is a separate session, preloaded only:
        # never started (no mic/audio/status surface touched).
        self.assertIsNotNone(primary._local._llama)
        self.assertEqual(primary._local.preload_calls, 1)

    def test_groq_key_absent(self):
        # (b) No Groq key -> no Groq leg -> pair None -> check OFF.
        primary = _FakePrimary()
        with mock.patch.object(cs, "_groq_key_present",
                               return_value=False):
            holder = cs.CompanionSessionHolder(
                build_local_inner=primary._build_local_inner)
            holder.start()
            self.assertIsNone(holder.pair)
            self.assertEqual(holder.missing_legs(), ["groq key"])
        # The check stays OFF for a None pair: a destructive +
        # side-effecting step is not checked at all.
        coord = _coord(brains=None)
        task = _task(coord, [{"label": "Delete old logs",
                              "tool": "delete_file"}])
        sentence, event = coord._second_opinion_check(task, _fake_step())
        self.assertIsNone(sentence)
        self.assertIsNone(event)

    def test_local_model_unavailable(self):
        # (c) Local model unavailable -> no local leg -> pair None.
        # Lite-shaped session (no chat method): never a leg.
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=lambda: _FakeLocalLite())
            holder.start()
            self.assertIsNone(holder.pair)
            self.assertEqual(holder.missing_legs(), ["local model"])
        # Builtin session whose preload fails: same honest outcome.
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=lambda: _FakeLocalBuiltin(
                    preload_ok=False))
            holder.start()
            self.assertIsNone(holder.pair)
            self.assertEqual(holder.missing_legs(), ["local model"])
        # Builder raising: also no leg, never a crash.
        with p1, p2:
            def _boom():
                raise RuntimeError("no model on disk")
            holder = cs.CompanionSessionHolder(build_local_inner=_boom)
            holder.start()
            self.assertIsNone(holder.pair)
            self.assertEqual(holder.missing_legs(), ["local model"])

    def test_ollama_leg_needs_no_preload(self):
        # Backend decides the readiness path: an ollama leg is
        # ready as built (model in the server); a builtin leg
        # needs its preload. A builtin session with BOTH methods
        # still takes the preload path (not hasattr order).
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=lambda: _FakeLocalOllama(),
                local_backend="ollama")
            holder.start()
            self.assertIsNotNone(holder.pair)
            self.assertEqual(holder.missing_legs(), [])
        with p1, p2:
            local = _FakeLocalRealShape()
            holder = cs.CompanionSessionHolder(
                build_local_inner=lambda: local,
                local_backend="builtin")
            holder.start()
            self.assertIsNotNone(holder.pair)
            self.assertIs(holder.local, local)

    def test_releases_cleanly(self):
        # (f) stop() releases both sessions cleanly: model refs
        # dropped, Groq stopped, pair None, restart rebuilds.
        primary = _FakePrimary()
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=primary._build_local_inner)
            holder.start()
            self.assertIsNotNone(holder.pair)
            holder.stop()
            self.assertIsNone(holder.pair)
            self.assertIsNone(primary._local._llama)
            self.assertIsNone(primary._local._whisper)
            self.assertTrue(groq.stopped)
            self.assertEqual(holder.missing_legs(),
                             ["companion holder not started"])
            # Stop is idempotent; a later start rebuilds the legs.
            holder.stop()
            holder.start()
            self.assertIsNotNone(holder.pair)
        # The fake primary is untouched by the whole cycle.
        self.assertIsNotNone(primary._inner)
        self.assertIsNotNone(primary._standby_inner)
        self.assertIsNone(primary._fallback_reason)

    def test_companion_prefers_local(self):
        # One companion for Stage 1 later: local when usable,
        # Groq otherwise, None when neither.
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=_FakePrimary()._build_local_inner)
            holder.start()
            self.assertIs(holder.companion(), holder.local)
            holder.stop()
            self.assertIsNone(holder.companion())


class ProxyHolderTests(unittest.TestCase):
    def test_proxy_starts_and_releases_holder(self):
        # (a, real proxy) BrainSession starts the holder alongside
        # its primary without disturbing _inner / _standby_inner /
        # fallback state, and releases it on stop().
        from core.brain.brain import BrainSession
        proxy = BrainSession()
        self.assertIsNone(proxy._inner)
        self.assertIsNone(proxy._standby_inner)
        self.assertIsNone(proxy._companion_holder)
        self.assertIsNone(proxy.companion_pair())
        local = _FakeLocalBuiltin()
        proxy._build_local_inner = lambda: local
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            proxy._start_companion()
            pair = proxy.companion_pair()
            self.assertIsNotNone(pair)
            self.assertIs(pair[0], groq)
            self.assertIs(pair[1], local)
            self.assertIsNone(proxy._inner)
            self.assertIsNone(proxy._standby_inner)
            self.assertIsNone(proxy._fallback_reason)
            proxy.stop()
            self.assertIsNone(proxy.companion_pair())
            self.assertTrue(groq.stopped)
            self.assertIsNone(local._llama)


# ------------------------------------------------------- factory shape (d)
class FactoryShapeTests(unittest.TestCase):
    def test_factory_shape(self):
        # (d) The holder's pair is the factory's exact shape: the
        # Groq leg exposes _chat_completion, the local leg exposes
        # _ollama_chat/_llama_chat, and the two legs are distinct
        # engines (different objects, different classes).
        primary = _FakePrimary(local=_FakeLocalRealShape())
        groq, p1, p2 = _patched_holder_key()
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=primary._build_local_inner)
            holder.start()
            pair = holder.pair
        self.assertIsNotNone(pair)
        cloud_session, local_session = pair
        self.assertTrue(callable(getattr(cloud_session,
                                         "_chat_completion", None)))
        self.assertTrue(callable(getattr(local_session,
                                         "_ollama_chat", None)))
        self.assertTrue(callable(getattr(local_session,
                                         "_llama_chat", None)))
        self.assertIsNot(cloud_session, local_session)
        self.assertIsNot(type(cloud_session), type(local_session))
        brain_a, brain_b = second_opinion_from_sessions(*pair)
        self.assertTrue(callable(brain_a))
        self.assertTrue(callable(brain_b))
        # INTENTIONAL (factory dispatch fix, 2026-11-05): the local
        # brain now answers via the resident _llama_chat first, so
        # this pair AGREES (both YES) instead of going unavailable
        # on the dead Ollama server — the dispatch pin below pins
        # the new order. The Ollama server is never contacted.
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_a, "YES")
        self.assertEqual(result.answer_b, "YES")
        self.assertEqual(local_session.llama_calls, 1)
        self.assertEqual(local_session.ollama_calls, 0)

    def test_factory_llama_branch_when_reached(self):
        # The factory's _llama_chat branch works when reached (a
        # local leg without _ollama_chat): both brains YES -> agree.
        groq = _FakeGroq("YES")
        local = _FakeLocalBuiltin("yes")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "yes")
        self.assertEqual(local.llama_calls[0][0]["content"],
                         "Delete old logs?")

    def test_dispatch_pin(self):
        # INTENTIONAL DISPATCH PIN (factory dispatch fix, 2026-11-05,
        # parent ruling): the factory tries the session's OWN resident
        # backend (_llama_chat) FIRST and falls back to
        # _ollama_chat only on failure or an empty result. A real
        # LocalLiveSession always exposes _ollama_chat (lifecycle
        # mixin), so the OLD order asked a real builtin leg via
        # Ollama HTTP and its resident _llama_chat was NEVER
        # called — without a reachable Ollama server the local
        # brain went "unavailable" and every check paused
        # single-opinion. The new order asks the resident model
        # first (no server needed) and keeps _ollama_chat as the
        # fallback for ollama-backed legs and failed resident
        # calls. Changing this order needs an INTENTIONAL comment
        # update, not silent drift.
        groq = _FakeGroq("YES")
        local = _FakeLocalRealShape("YES")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "YES")
        self.assertEqual(local.llama_calls, 1)
        self.assertEqual(local.ollama_calls, 0)


# ------------------------------------------- factory dispatch: resident first
class FactoryResidentFirstDispatchTests(unittest.TestCase):
    # FACTORY DISPATCH FIX (2026-11-05, parent ruling): local_brain
    # tries the session's OWN resident backend (_llama_chat) first
    # and falls back to _ollama_chat only on failure or an empty
    # result; both failing/empty returns "" (unavailable). These
    # tests pin that order; changing it needs an INTENTIONAL
    # comment update, not silent drift.

    def test_builtin_resident_never_contacts_ollama(self):
        # (a) Resident leg, no Ollama server: asked via _llama_chat;
        # _ollama_chat is a tripwire — if the factory contacts it,
        # the test explodes. The resident path needs no server.
        groq = _FakeGroq("YES")
        local = _FakeLocalBoth(answer="yes", llama="answer",
                               ollama="tripwire")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "yes")
        self.assertEqual(local.llama_calls, 1)
        self.assertEqual(local.ollama_calls, 0)

    def test_ollama_backed_leg_uses_ollama(self):
        # (b) An ollama-backed leg has no _llama_chat at all, so
        # the factory asks via _ollama_chat (dict reply shape).
        groq = _FakeGroq("YES")
        local = _FakeLocalOllama("YES")
        self.assertFalse(hasattr(local, "_llama_chat"))
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "YES")
        self.assertEqual(len(local.ollama_calls), 1)

    def test_llama_raises_falls_back_to_ollama(self):
        # (c1) Resident call raises (model not loaded): the
        # _ollama_chat answer is used, not an unavailable verdict.
        groq = _FakeGroq("YES")
        local = _FakeLocalBoth(answer="YES", llama="raise",
                               ollama="dict")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "YES")
        self.assertEqual(local.llama_calls, 1)
        self.assertEqual(local.ollama_calls, 1)

    def test_llama_empty_falls_back_to_ollama(self):
        # (c2) Resident call returns empty: same fallback, the
        # _ollama_chat answer is used.
        groq = _FakeGroq("YES")
        local = _FakeLocalBoth(answer="YES", llama="empty",
                               ollama="dict")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "agree")
        self.assertEqual(result.answer_b, "YES")
        self.assertEqual(local.llama_calls, 1)
        self.assertEqual(local.ollama_calls, 1)

    def test_both_backends_failing_returns_unavailable(self):
        # (d1) Both backends fail: local brain returns "" and the
        # check goes "unavailable" (pauses fail-closed upstream),
        # never a fake answer.
        groq = _FakeGroq("YES")
        local = _FakeLocalBoth(llama="raise", ollama="raise")
        _, brain_b = second_opinion_from_sessions(groq, local)
        self.assertEqual(brain_b("Delete old logs?"), "")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "unavailable")
        self.assertEqual(result.answer_b, "")

    def test_both_backends_empty_returns_unavailable(self):
        # (d2) Both backends return empty: same "" / unavailable.
        groq = _FakeGroq("YES")
        local = _FakeLocalBoth(llama="empty", ollama="empty")
        _, brain_b = second_opinion_from_sessions(groq, local)
        self.assertEqual(brain_b("Delete old logs?"), "")
        brain_a, brain_b = second_opinion_from_sessions(groq, local)
        result = cross_check("Delete old logs?", brain_a, brain_b)
        self.assertEqual(result.verdict, "unavailable")
        self.assertEqual(result.answer_b, "")


# ------------------------------------------------- check before dispatch (e)
class CheckBeforeDispatchTests(unittest.TestCase):
    def test_check_fires_before_dispatch(self):
        # (e) Holder -> factory -> coordinator: a destructive +
        # side-effecting step triggers the check BEFORE dispatch
        # (disagreeing brains pause; the executor never runs).
        primary = _FakePrimary(local=_FakeLocalBuiltin("NO"))
        groq, p1, p2 = _patched_holder_key("YES")
        with p1, p2:
            holder = cs.CompanionSessionHolder(
                build_local_inner=primary._build_local_inner)
            holder.start()
            pair = holder.pair
        self.assertIsNotNone(pair)
        brains = second_opinion_from_sessions(*pair)
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=brains)
        task = _task(coord, [{"label": "Delete old logs",
                              "tool": "delete_file"}])
        sentence, event = coord._second_opinion_check(task, _fake_step())
        self.assertIsNotNone(sentence)
        self.assertIsNotNone(event)
        self.assertEqual(event["verdict"], "disagree")
        self.assertEqual(executor.calls, [])
        self.assertIn("YES", sentence)
        self.assertIn("NO", sentence)


# ------------------------------------------------------------- tail wiring
class TailWiringTests(unittest.TestCase):
    def _brains_pair(self):
        groq = _FakeGroq("YES")
        local = _FakeLocalBuiltin("YES")
        return groq, local

    def test_construction_supplies_brains(self):
        # A fresh coordinator built by handle_typed_turn carries
        # the factory callables from the holder's pair.
        groq, local = self._brains_pair()
        session = _fake_session()
        with mock.patch.object(cs, "companion_pair",
                               return_value=(groq, local)):
            handled = handle_typed_turn(
                session, "zzz no such plan qqq",
                ledger=SimpleNamespace())
        self.assertFalse(handled)
        coord = session._task_coordinator
        self.assertIsNotNone(coord)
        brains = coord._second_opinion_brains
        self.assertIsNotNone(brains)
        self.assertTrue(all(callable(b) for b in brains))
        self.assertEqual(brains[0]("Is the sky blue?"), "YES")
        self.assertEqual(brains[1]("Is the sky blue?"), "YES")

    def test_construction_without_pair_stays_off(self):
        # No pair -> brains None -> check OFF exactly as before.
        session = _fake_session()
        with mock.patch.object(cs, "companion_pair", return_value=None):
            handle_typed_turn(session, "zzz no such plan qqq",
                              ledger=SimpleNamespace())
        coord = session._task_coordinator
        self.assertIsNotNone(coord)
        self.assertIsNone(coord._second_opinion_brains)

    def test_stashed_refresh_when_pair_ready(self):
        # The first-turn race: a coordinator stashed while the
        # pair was not ready is refreshed once the pair exists —
        # same coordinator object, brains filled in.
        executor = _ok_executor()
        stashed = _coord(executor=executor, brains=None)
        session = _fake_session()
        session._task_coordinator = stashed
        groq, local = self._brains_pair()
        with mock.patch.object(cs, "companion_pair",
                               return_value=(groq, local)):
            handle_typed_turn(session, "zzz no such plan qqq",
                              ledger=SimpleNamespace())
        self.assertIs(session._task_coordinator, stashed)
        brains = stashed._second_opinion_brains
        self.assertIsNotNone(brains)
        self.assertTrue(all(callable(b) for b in brains))

    def test_stashed_never_overwritten(self):
        # A stashed coordinator that already has brains keeps them,
        # even when the holder's pair changes.
        first_a = mock.Mock(return_value="YES")
        first_b = mock.Mock(return_value="YES")
        stashed = _coord(brains=(first_a, first_b))
        session = _fake_session()
        session._task_coordinator = stashed
        groq, local = self._brains_pair()
        with mock.patch.object(cs, "companion_pair",
                               return_value=(groq, local)):
            handle_typed_turn(session, "zzz no such plan qqq",
                              ledger=SimpleNamespace())
        self.assertIs(stashed._second_opinion_brains[0], first_a)
        self.assertIs(stashed._second_opinion_brains[1], first_b)

    def test_explicit_coordinator_untouched(self):
        # An explicitly passed coordinator is never refreshed: the
        # refresh applies only to the stashed production one.
        explicit = _coord(brains=None)
        session = _fake_session()
        groq, local = self._brains_pair()
        with mock.patch.object(cs, "companion_pair",
                               return_value=(groq, local)):
            handle_typed_turn(session, "zzz no such plan qqq",
                              coordinator=explicit,
                              ledger=SimpleNamespace())
        self.assertIsNone(explicit._second_opinion_brains)
        self.assertIsNone(session._task_coordinator)

    def test_fill_validates_shape(self):
        # _fill refuses a malformed pair instead of installing it.
        # (The seam _fill reads is the tail's own reader, so the
        # malformed values are injected there; the factory itself
        # builds callables from any session pair and validates
        # nothing — a garbage session degrades to "unavailable" at
        # check time, never to a crash.)
        import core.brain.task_coordinator_tail as tail_mod
        coord = _coord(brains=None)
        with mock.patch.object(
                tail_mod, "_companion_second_opinion_brains",
                return_value=("not-callable", None)):
            self.assertFalse(_fill_second_opinion_brains(coord))
        self.assertIsNone(coord._second_opinion_brains)
        with mock.patch.object(
                tail_mod, "_companion_second_opinion_brains",
                return_value="not-a-pair"):
            self.assertFalse(_fill_second_opinion_brains(coord))
        self.assertIsNone(coord._second_opinion_brains)
        with mock.patch.object(
                tail_mod, "_companion_second_opinion_brains",
                return_value=None):
            self.assertFalse(_fill_second_opinion_brains(coord))
        self.assertIsNone(coord._second_opinion_brains)
        # A valid callable pair is installed.
        good_a = mock.Mock(return_value="YES")
        good_b = mock.Mock(return_value="YES")
        with mock.patch.object(
                tail_mod, "_companion_second_opinion_brains",
                return_value=(good_a, good_b)):
            self.assertTrue(_fill_second_opinion_brains(coord))
        self.assertIs(coord._second_opinion_brains[0], good_a)
        self.assertIs(coord._second_opinion_brains[1], good_b)
        # And the reader returns None when the holder is unusable.
        with mock.patch.object(cs, "companion_pair",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(_companion_second_opinion_brains())


if __name__ == "__main__":
    unittest.main()
