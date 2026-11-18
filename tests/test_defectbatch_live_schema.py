"""Post-triage defect batch, item A — Live offered-set schema validation.

Regression test for the Live 1007 (owner field report, voice fully
blocked): cursor_control's `points` param was declared
{"type": "array"} with NO `items`, and cursor_control IS resident
(core/funnel/tool_profiles.py:170), so the Live offered set carried an
invalid schema and Google rejected session setup deterministically
(1007, properties[points].items missing). The fix adds items:
an array of [int, int] pairs. This suite pins BOTH the exact fix
and the general rule: EVERY array-typed property in the offered
declarations for EVERY profile, recursively, must carry `items`.

Offline: the offered sets are built through the real
core.live_client._offered_declarations builder — no network, no
Gemini call (the builder never touches the API client). The
assistant case reads the activity-log tail (read-only); the data
tree is snapshotted and restored around every build anyway, so a
builder that ever writes cannot dirty working data/.
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
import types
import unittest

try:
    from core.voice.live_client import _offered_declarations  # Phase 3 V1b INTENTIONAL: moved to core/voice/
    from core.funnel.tools_registry import tools as _registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"live offered-set builder unavailable: {exc}")

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"

_PROFILES = ("assistant", "desktop", "full")


def _walk(node, path, decl_name, violations):
    """Collect every type=array node lacking `items`, recursively."""
    if isinstance(node, dict):
        if node.get("type") == "array" and "items" not in node:
            violations.append(f"{decl_name}: {path} is type=array without items")
        for key, value in node.items():
            _walk(value, f"{path}.{key}", decl_name, violations)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk(value, f"{path}[{i}]", decl_name, violations)


def _offered_for(profile, router_on):
    cfg = types.SimpleNamespace(tool_router=router_on, tool_profile=profile)
    declarations, _routed, _label = _offered_declarations(_registry, cfg)
    return declarations


class TestLiveOfferedSchemas(unittest.TestCase):
    """Every offered declaration, every profile, every array has items."""

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="defectbatch_live_"))
        self._saved = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    self._saved[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass

    def tearDown(self):
        current = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    current[path.relative_to(_DATA_DIR)] = path
                except OSError:
                    pass
        for rel, path in current.items():
            if rel not in self._saved:
                try:
                    path.unlink()
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
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_every_profile_every_array_has_items(self):
        for profile in _PROFILES:
            for router_on in (True, False):
                with self.subTest(profile=profile, router_on=router_on):
                    declarations = _offered_for(profile, router_on)
                    self.assertTrue(declarations,
                                    f"{profile}/router={router_on} offered nothing")
                    violations = []
                    for decl in declarations:
                        name = decl.get("name", "?") if isinstance(decl, dict) else "?"
                        params = decl.get("parameters") if isinstance(decl, dict) \
                            else getattr(decl, "parameters", None)
                        _walk(params, "parameters", name, violations)
                    self.assertEqual(violations, [],
                                     f"{profile}/router={router_on}:\n" + "\n".join(violations))

    def test_cursor_control_points_items_are_int_pairs(self):
        """The exact 1007 culprit: points must be array-of-[int, int]."""
        declarations = _offered_for("desktop", True)
        cursor = [d for d in declarations
                  if isinstance(d, dict) and d.get("name") == "cursor_control"]
        self.assertEqual(len(cursor), 1,
                         "cursor_control must be in the desktop offered set")
        points = cursor[0]["parameters"]["properties"]["points"]
        self.assertEqual(points.get("type"), "array")
        self.assertEqual(points.get("items"),
                         {"type": "array", "items": {"type": "integer"}})


if __name__ == "__main__":
    unittest.main()
