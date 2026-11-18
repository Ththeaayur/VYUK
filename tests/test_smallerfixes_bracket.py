"""Smaller-fixes batch (2026-11-02): dropped-args bracket regression tests.

Programme note, not a fix: the regression-batch summary named a
"dropped-args bracket leak (dropped-args refusal returns after
begin_execution() without _end_bracket_once())". A probe through the
REAL funnel (2026-11-02) showed NO leak in the current tree: the
refusal return sits inside the try whose Audit-A5 finally runs
``_bracket.caller_finished() -> _end_bracket_once()`` (mode is still
"caller" — no deadline worker exists yet on this path), and the
_audited closure records the refusal as the receipt outcome, so no
dispatched-without-outcome unknown lingers either. Adding an
explicit end before the return would DOUBLE-end (end_execution
clamps at 0, silently draining a concurrent bracket), so the
correct batch action is to PIN the behaviour with tests, not to
change product code. No product file is touched by this suite.

Each test drives the real tools.execute funnel with a probe tool
whose schema explicitly declares additionalProperties: true
(register() keeps a declared value) and whose handler has a fixed
signature — so validation passes the extra key and _dispatch_args
drops it, hitting the dropped-args path. The probe tool is removed
in cleanup so no registry-count suite ever sees it.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from core import confirm_gate, injection
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.config import config as app_config
    from core.funnel.tools_registry import tools as registry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    import core.funnel.tools_registry as registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
except Exception as exc:  # noqa: BLE001 - missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"
_PROBE = "__probe_effectful"


class _DataGuard:
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


class BracketTests(unittest.TestCase):
    """Dropped-args refusal leaves the taint bracket balanced."""

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="bracket_"))
        self._guard = _DataGuard()
        self._guard.__enter__()
        self._ran = []
        self.addCleanup(self._guard.__exit__, None, None, None)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.addCleanup(self._remove_probe)
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config, "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        self.addCleanup(tools_mod.set_session_provenance, self._orig_prov)
        self.addCleanup(
            setattr, app_config, "session_provenance_lock", self._orig_lock)
        self.addCleanup(confirm_gate.clear_handler)
        self.addCleanup(injection.clear_taint, force=True)
        registry.register(
            name=_PROBE,
            description="Probe tool for the dropped-args bracket check.",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": True,
            },
            handler=self._probe_handler,
        )

    def _remove_probe(self):
        registry._tools.pop(_PROBE, None)

    def _probe_handler(self, x=""):
        self._ran.append(x)
        return "probe-ran"

    def test_effectful_dropped_args_refusal_ends_bracket(self):
        """The named leak path: refusal after begin_execution()."""
        self.assertEqual(injection.in_flight(), 0)
        out = registry.execute(_PROBE, {"x": "a", "bogus": 1})
        self.assertEqual(out.get("status"), "refused")
        self.assertEqual(out.get("kind"), "refused")
        self.assertIn("undeclared argument(s)", out.get("message", ""))
        self.assertIn("bogus", out.get("message", ""))
        self.assertEqual(self._ran, [])  # handler never ran
        self.assertEqual(injection.in_flight(), 0)

    def test_effectful_dropped_args_refusal_leaves_no_unknown(self):
        """The refusal outcome is recorded, so a retry is not
        guard-blocked as an unknown dispatched attempt."""
        first = registry.execute(_PROBE, {"x": "a", "bogus": 1})
        self.assertEqual(first.get("status"), "refused")
        second = registry.execute(_PROBE, {"x": "a", "bogus": 1})
        self.assertEqual(second.get("status"), "refused")
        self.assertEqual(second.get("kind"), "refused")
        self.assertIn("undeclared argument(s)", second.get("message", ""))
        self.assertEqual(injection.in_flight(), 0)

    def test_read_dropped_args_path_ends_bracket(self):
        """Same try/finally, read leg: handler runs, strip is
        surfaced, bracket still balanced. Classification is
        patched ONLY for the probe name (the mechanism under
        test is the bracket, not classification)."""
        real_is_read = registry_mod._is_read_class

        def _is_read_for_probe(name, args):
            if name == _PROBE:
                return True
            return real_is_read(name, args)

        # INTENTIONAL (tools_registry batch 2, B16/B47 pattern):
        # execute() stages now read _is_read_class from their own
        # mixin modules (setup/gates/dispatch), not only from the
        # facade -- patch all four bindings with the same fake,
        # all restored by the context managers. Same fake, same
        # assertions; no assertion weakened.
        from core.funnel import tools_registry_exec_dispatch as _dispatch_mod  # INTENTIONAL (batch 2): stage binding
        from core.funnel import tools_registry_exec_gates as _gates_mod  # INTENTIONAL (batch 2): stage binding
        from core.funnel import tools_registry_exec_setup as _setup_mod  # INTENTIONAL (batch 2): stage binding
        with mock.patch.object(
                registry_mod, "_is_read_class",
                side_effect=_is_read_for_probe), mock.patch.object(
                _setup_mod, "_is_read_class",
                side_effect=_is_read_for_probe), mock.patch.object(
                _gates_mod, "_is_read_class",
                side_effect=_is_read_for_probe), mock.patch.object(
                _dispatch_mod, "_is_read_class",
                side_effect=_is_read_for_probe):
            out = registry.execute(_PROBE, {"x": "a", "bogus": 1})
        self.assertEqual(self._ran, ["a"])  # handler ran
        # String results gain a trailing strip note (dict results
        # gain a dropped_args field) — either shape proves the
        # strip surfaced, never silently.
        result = out.get("result")
        self.assertIsInstance(result, str)
        self.assertIn("bogus", result)
        self.assertIn("without undeclared argument(s)", result)
        self.assertEqual(injection.in_flight(), 0)

    def test_success_path_ends_bracket(self):
        """Control: a clean call through the same bracket."""
        out = registry.execute(_PROBE, {"x": "a"})
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(self._ran, ["a"])
        self.assertEqual(injection.in_flight(), 0)


if __name__ == "__main__":
    unittest.main()
