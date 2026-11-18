"""Confirm-gate batch-key id-reuse regression (2026-11-03 product fix).

The batch key is (tool, canonical-args-digest, id(handler)). CPython
reuses id() values once an object is freed, so a batch record keyed
only by that integer could replay an approval to a DIFFERENT handler
that happened to recycle the freed handler's id inside the 10 s
window. In production that is an approval replayed to the wrong
handler; in dc1 it showed up as order-dependent flakiness (alone
runs fail 3 decline/scope items, battery runs pass them).

The fix holds a strong reference to the handler that made the
decision and verifies handler identity on lookup, so a recycled id
alone can never replay. These tests pin that:

(1) id-reuse: a different handler never inherits an approval.
    Two layers, both proven: layer 1 -- the strong reference
    keeps the approving handler alive while its record lives, so
    its id cannot be recycled at all (a bounded allocation loop
    finds no reuse, and a weakref proves the handler is still
    alive); layer 2 -- even a hypothetical key-matching different
    handler is denied replay by the identity check (direct
    _batch_lookup with the stored key + a different handler
    returns None, while the stored handler itself replays).
    Pre-fix, layer 1 does not exist, so the allocation loop
    reuses the freed id on its first allocation and the new
    handler inherits the approval -- this test fails pre-fix
    with the inherited True, exactly as the bug requires;
(2) same-handler replay still works within the window (the v5.48
    programme-item-19 feature, unchanged -- also covered by
    test_auditfixA1 and the v548_p19 root runner);
(3) window expiry still ends replay (ageing the stored record past
    _BATCH_WINDOW_S forces a fresh prompt).

Stdlib unittest only. The taint seam is stubbed to tainted, and
decision logging is silenced, exactly as test_auditfixA1 does, so
no real popup runs and nothing appends to data/activity.log.
"""

import unittest
import weakref

from core import confirm_gate


def _tainted():
    return True, ("file", ["file"])


_TOOL = "test_note_tool"
_ARGS = {"note": "hello", "count": 3}


class _BatchKeyBase(unittest.TestCase):
    def setUp(self):
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None
        self._orig_taint = confirm_gate._taint_context
        confirm_gate._taint_context = _tainted
        self._reset_batch()
        confirm_gate.clear_handler()

    def tearDown(self):
        confirm_gate._taint_context = self._orig_taint
        confirm_gate._log_decision = self._orig_log
        self._reset_batch()
        confirm_gate.clear_handler()

    def _reset_batch(self):
        with confirm_gate._batch_lock:
            confirm_gate._batch_last.update(
                {"key": None, "decision": False, "at": 0.0,
                 "count": 0, "origin_id": None})
            # The fix's strong handler reference lives outside the
            # serialisable record; clear it too when it exists.
            if hasattr(confirm_gate, "_batch_handler"):
                confirm_gate._batch_handler = None

    def _ask(self, handler, timeout_s=10):
        confirm_gate.register_handler(handler)
        return confirm_gate.request_confirmation(
            _TOOL, dict(_ARGS), timeout_s=timeout_s)


class TestBatchKeyIdReuse(_BatchKeyBase):
    def test_different_handler_never_inherits_despite_reused_id(self):
        """A different handler never inherits an approval, via
        either fix layer (see module docstring). Pre-fix this
        fails: the allocation loop reuses the freed id at once
        and the new handler inherits True without being asked."""
        calls_c = []

        def handler_c(name, summary):
            calls_c.append(name)
            return True

        self.assertTrue(self._ask(handler_c))
        self.assertEqual(calls_c, [_TOOL])
        id_c = id(handler_c)
        ref_c = weakref.ref(handler_c)

        # Clear the registry AND drop this test's own reference.
        # Pre-fix, refcounting frees C at once (no cycle: the
        # handler closes over a plain list), so the next
        # same-sized function allocation reuses its id.
        # Deliberately NO gc.collect() on this path: a forced
        # collection itself allocates same-sized objects and
        # consumes the freed block, which is why a gc-per-iteration
        # loop never observes the reuse (proven by probe).
        confirm_gate.clear_handler()
        del handler_c

        # Bounded search for a new handler reusing C's id. CPython
        # reuses the most recently freed id for same-sized objects,
        # so pre-fix this hits on the first allocation; the loop
        # only guards against intervening allocations consuming it.
        handler_j = None
        for _ in range(1000):
            candidate = lambda name, summary: False  # noqa: E731
            if id(candidate) == id_c:
                handler_j = candidate
                break
            del candidate
        if handler_j is not None:
            # Layer-2 path (only reachable if layer 1 is ever
            # bypassed): a genuine id collision must still be
            # consulted, never replayed. NOTE: wrapping handler_j
            # would change its id, destroying the collision under
            # test, so register the proven lambda itself: its
            # False answer is the observable proof it was
            # consulted (a replay would return True). The recorded
            # decision was True; only a consulted handler can
            # return False here.
            self.assertEqual(id(handler_j), id_c)
            result = self._ask(handler_j)
            self.assertFalse(
                result,
                "new handler with the freed handler's id inherited "
                "the old approval instead of being consulted")
            self.assertEqual(confirm_gate.last_batch_count(), 1)
            return

        # Expected post-fix path (layer 1): no allocation in 1000
        # attempts recycled C's id, BECAUSE the batch record's
        # strong reference kept C alive -- prove that is the
        # reason, not luck, with the weakref.
        self.assertIsNotNone(
            ref_c(),
            "no id reuse in 1000 attempts, but the approving "
            "handler was freed anyway -- layer 1 did not hold")
        # Layer 2, proven directly: a key-matching DIFFERENT
        # handler (the stored key, a fresh handler object) must
        # NOT replay, while the stored handler itself does.
        stored_key = confirm_gate._batch_last.get("key")
        stored_handler = getattr(confirm_gate, "_batch_handler",
                                 None)
        self.assertIsNotNone(stored_key)
        self.assertIs(ref_c(), stored_handler)
        other = lambda name, summary: False  # noqa: E731
        self.assertIsNone(
            confirm_gate._batch_lookup(stored_key, other),
            "identity check let a different handler replay a "
            "key-matching decision")
        self.assertTrue(
            confirm_gate._batch_lookup(stored_key, stored_handler),
            "the stored handler itself must still replay")

    def test_same_handler_replay_still_works(self):
        """v5.48 feature unchanged: the SAME handler object, same
        tool + args inside the window, replays without a new
        popup."""
        calls = []

        def handler(name, summary):
            calls.append(name)
            return True

        self.assertTrue(self._ask(handler))
        self.assertTrue(self._ask(handler))
        self.assertEqual(calls, [_TOOL])
        self.assertEqual(confirm_gate.last_batch_count(), 2)

    def test_window_expiry_ends_replay(self):
        """Ageing the stored record past _BATCH_WINDOW_S forces a
        fresh prompt for the same handler, same tool + args."""
        calls = []

        def handler(name, summary):
            calls.append(name)
            return True

        self.assertTrue(self._ask(handler))
        self.assertTrue(self._ask(handler))
        self.assertEqual(calls, [_TOOL])
        with confirm_gate._batch_lock:
            confirm_gate._batch_last["at"] -= (
                confirm_gate._BATCH_WINDOW_S + 1.0)
        self.assertTrue(self._ask(handler))
        self.assertEqual(calls, [_TOOL, _TOOL])
