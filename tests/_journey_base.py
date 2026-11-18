"""Shared isolation for journey tests (whole-path phase).

Mirrors the testleg pattern: whole-data-tree guard, taint cleared,
confirm handler cleared, provenance user-started with the lock off,
desktop-backend routing env unset. Funnel calls take a FRESH args
dict every time (the funnel annotates the caller's dict).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import unittest

from core import confirm_gate, injection
from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.config import config as app_config

try:
    from core.platform import reset_backend_cache
except Exception:  # noqa: BLE001 — optional seam
    reset_backend_cache = None

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"


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


class FunnelBase(unittest.TestCase):
    """Common isolation for tests that drive the real funnel."""

    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="journey_"))
        self._guard = DataGuard()
        self._guard.__enter__()
        try:
            self._orig_env = os.environ.pop("VYUK_PLATFORM", None)
            if reset_backend_cache is not None:
                try:
                    reset_backend_cache()
                except Exception:
                    pass
            injection.clear_taint(force=True)
            confirm_gate.clear_handler()
            self._orig_prov = tools_mod.session_provenance()
            tools_mod.set_session_provenance("user")
            self._orig_lock = getattr(app_config, "session_provenance_lock", False)
            app_config.session_provenance_lock = False
        except Exception:
            self._guard.__exit__(None, None, None)
            shutil.rmtree(self._tmp, ignore_errors=True)
            raise

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        if self._orig_env is None:
            os.environ.pop("VYUK_PLATFORM", None)
        else:
            os.environ["VYUK_PLATFORM"] = self._orig_env
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:
                pass
        self._guard.__exit__(None, None, None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()
