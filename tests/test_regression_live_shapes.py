"""Regression batch G1 — offered-declaration shape audit.

Complements (does NOT duplicate) the defect-batch offered-set test,
which pins every array carrying `items`. This suite pins the other
two Live-acceptance shapes over the REAL offered-declaration builder
(`core.live_client._offered_declarations`, so dynamically-built
declarations like cursor_control are covered — static audits miss
them):

- NO non-string enum values anywhere (the Gemini-safe rewrite drops
  them; a surviving one would be a transport bug).
- Empty objects appear ONLY in `route_*` args nodes — the DOCUMENTED
  pass-through (`_gemini_safe_schema` docstring: router args keep
  their object shape by design, their schema is the inner tool's
  schema enforced at dispatch; "do not 'fix'" them). An empty object
  in any REGISTERED declaration fails loudly.

Profiles assistant/desktop/full × router on/off, offline, no network.

Stdlib unittest only.
"""

from __future__ import annotations

import types
import unittest

try:
    from core.voice.live_client import _offered_declarations  # Phase 3 V1b INTENTIONAL: moved to core/voice/
    from core.funnel.tools_registry import tools as _registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

_PROFILES = ("assistant", "desktop", "full")


def _offered_for(profile, router_on):
    cfg = types.SimpleNamespace(tool_router=router_on, tool_profile=profile)
    declarations, _routed, _label = _offered_declarations(_registry, cfg)
    return declarations


def _walk(node, path, decl_name, bad_enums, empty_objects):
    if isinstance(node, dict):
        enum = node.get("enum")
        if isinstance(enum, list) and any(
                not isinstance(v, str) for v in enum):
            bad_enums.append(f"{decl_name}: {path} non-string enum")
        if node.get("type") == "object" and not node.get("properties"):
            empty_objects.append((decl_name, path))
        for key, value in node.items():
            _walk(value, f"{path}.{key}", decl_name, bad_enums, empty_objects)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk(value, f"{path}[{i}]", decl_name, bad_enums, empty_objects)


class TestOfferedDeclarationShapes(unittest.TestCase):
    def test_no_non_string_enums_and_only_router_empty_objects(self):
        for profile in _PROFILES:
            for router_on in (True, False):
                bad_enums = []
                empty_objects = []
                declarations = _offered_for(profile, router_on)
                self.assertGreater(len(declarations), 0,
                                   f"{profile} router={router_on}: no decls")
                for decl in declarations:
                    _walk(decl.get("parameters", {}), "parameters",
                          decl.get("name", "?"), bad_enums, empty_objects)
                self.assertEqual(
                    bad_enums, [],
                    f"{profile} router={router_on}: {bad_enums[:5]}")
                route_args = [(n, p) for n, p in empty_objects
                              if n.startswith("route_")
                              and p == "parameters.properties.args"]
                others = [(n, p) for n, p in empty_objects
                          if not (n.startswith("route_")
                                  and p == "parameters.properties.args")]
                self.assertEqual(
                    others, [],
                    f"{profile} router={router_on}: non-router empty "
                    f"objects: {others[:5]}")
                self.assertGreater(
                    len(route_args), 0,
                    f"{profile} router={router_on}: the documented "
                    f"route_* args pass-through vanished — if the "
                    f"routers were deliberately removed, update this "
                    f"test with the reason, do not just delete it")


if __name__ == "__main__":
    unittest.main()
