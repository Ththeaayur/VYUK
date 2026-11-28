"""Teach trigger (2026-11-03, code-rulings delegation).

Pins the teach trigger decision, end to end:

- VOICE TRIGGER EXISTS via the Live router path: the route_intelligence
  domain router carries all three teach tools, and use_toolbox
  keyword ranking surfaces them for canonical phrases. The
  trigger path is therefore: spoken phrase -> ranked tool name
  -> funnel execution (confirm-gated) -> file-backed store.
  This suite drives exactly that path: it ranks a phrase, takes
  the top tool name, and executes it through the REAL funnel.
- NO GUI BUTTON is the decision, not a gap: the Skills
  catalogue is discovery-only for every tool (no activation
  handler on any item), and a record dialog would need a full
  step editor. The voice path is the product's primary trigger
  pattern.
- LIVE DEMONSTRATION CAPTURE is GATED (TEACH_LIVE_CAPTURE_GATE
  in core/w8_wiring.py): a user-action observer, an
  event-to-funnel-tool mapper, and field validation on real
  Windows are all missing, and the action journal cannot
  substitute (args hash + sanitized truncated preview, never
  raw args; teach steps need exact args + a verify check).
- DELIBERATE NON-STEAL: vague "remember how to do this" keeps
  routing to memory tools, not teach. Tuning ranking to steal
  "remember" would break memory routing.

Isolation mirrors test_libwiring_teach_wallpaper: the whole
data/ tree is snapshotted per test and restored after (taught
sequences are test files, never real user data), taint is
cleared, provenance is user-started with the lock off, and the
confirm handler is cleared after every call.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate, injection
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target
from core.funnel import tools_gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel import toolbox as _toolbox
from core.brain.domain_routers import build_domain_routers
from core import w8_wiring as _w8
from core.config import config as app_config

_DATA_DIR = ROOT / "data"

TEACH_TOOLS = ("teach_record_sequence", "teach_list_sequences",
               "teach_replay_sequence")

READ_STEP = {"tool": "get_current_time_date", "args": {},
             "verify": {"kind": "ok"}}


class DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test."""

    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    (_DATA_DIR / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class TriggerBase(unittest.TestCase):
    def setUp(self):
        self._guard = DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()


class TestTeachTrigger(TriggerBase):
    def test_ranking_record_phrase_ranks_record_first(self):
        ranked = _toolbox.rank_tools_keyword(tools, "record a sequence",
                                             limit=8)
        self.assertEqual(ranked[0], "teach_record_sequence", ranked)

    def test_ranking_teach_phrases_surface_all_teach_tools(self):
        ranked = _toolbox.rank_tools_keyword(tools, "teach me a routine",
                                             limit=8)
        for tool in TEACH_TOOLS:
            self.assertIn(tool, ranked, ranked)

    def test_ranking_replay_phrase_surfaces_replay(self):
        ranked = _toolbox.rank_tools_keyword(tools,
                                             "replay my morning routine",
                                             limit=8)
        self.assertIn("teach_replay_sequence", ranked, ranked)

    def test_route_intelligence_carries_all_teach_tools(self):
        routers = build_domain_routers(tools)
        # UI Phase 2 (2026-11-25): router follows the group rename ("Mind" -> "Intelligence").
        intelligence = [r for r in routers
                if (r.get("name") if isinstance(r, dict) else None)
                == "route_intelligence"]
        self.assertEqual(len(intelligence), 1, [r.get("name") for r in routers])
        desc = intelligence[0].get("description") or ""
        for tool in TEACH_TOOLS:
            self.assertIn(tool, desc, desc)

    def test_trigger_path_rank_then_execute_record_list_replay(self):
        # The trigger path, driven as the model drives it: rank the
        # spoken phrase, take the top tool name, execute it through
        # the funnel (confirm-gated), then list + replay the same way.
        record_name = _toolbox.rank_tools_keyword(
            tools, "record a sequence", limit=8)[0]
        self.assertEqual(record_name, "teach_record_sequence")
        recorded = self.call(record_name,
                             {"name": "trigger-demo",
                              "steps": [dict(READ_STEP)]},
                             approve=True)
        self.assertEqual(recorded["status"], "success", recorded)
        self.assertIn("trigger-demo", recorded["result"])
        listed = self.call("teach_list_sequences", {})
        self.assertEqual(listed["status"], "success", listed)
        self.assertIn("trigger-demo", listed["result"])
        replay_name = _toolbox.rank_tools_keyword(
            tools, "replay my morning routine", limit=8)
        self.assertIn("teach_replay_sequence", replay_name, replay_name)
        replayed = self.call("teach_replay_sequence",
                             {"name": "trigger-demo"},
                             approve=True)
        self.assertEqual(replayed["status"], "success", replayed)
        self.assertIn("completed", replayed["result"])

    def test_gate_names_observer_mapper_validation_and_shipped_path(self):
        gate = _w8.TEACH_LIVE_CAPTURE_GATE
        self.assertIn("user-action observer", gate)
        self.assertIn("mapper", gate)
        self.assertIn("field validation", gate)
        self.assertIn("spell-out", gate)
        # The gate is prose, not logic: no funnel tool, coordinator
        # step, or voice path reads this constant (grep-verified at
        # authoring time: only docstrings reference it).

    def test_vague_remember_phrase_stays_with_memory_not_teach(self):
        # Deliberate non-steal: "remember how to do this" is a
        # memory request. Ranking must NOT hand it to teach, or
        # memory routing breaks for a teach-shaped phrase.
        ranked = _toolbox.rank_tools_keyword(
            tools, "remember how to do this", limit=8)
        for tool in TEACH_TOOLS:
            self.assertNotIn(tool, ranked, ranked)
        self.assertIn("remember_user_fact", ranked, ranked)


if __name__ == "__main__":
    unittest.main()
