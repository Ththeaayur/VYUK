"""MemoryStore package export pins (memorystore export batch, 2026-11-05).

INTENTIONAL: before this batch, `from core.memory import MemoryStore`
failed with ImportError — the class lives in core/memory/memory.py but
the package __init__ (docstring only, Phase 3 M2h) did not re-export
it. That gap blocked root scripts test_v46_batch3b.py and
test_v52_episodes.py, both of which import MemoryStore from the
package. These tests pin the export surface so a future __init__
edit cannot silently re-open the gap.
"""

import tempfile
import unittest
from pathlib import Path

import core.memory as memory_pkg
from core.memory import MemoryStore
from core.memory.memory import MemoryStore as MemoryStoreSubmodule


class TestMemoryStoreExport(unittest.TestCase):
    def test_package_exports_memorystore(self):
        # INTENTIONAL: the exact import that failed before the fix.
        self.assertIsNotNone(MemoryStore)

    def test_package_export_is_the_submodule_class(self):
        # INTENTIONAL: the re-export must be the same class object,
        # not a copy, a wrapper, or a stale duplicate.
        self.assertIs(MemoryStore, MemoryStoreSubmodule)

    def test_package_all_lists_memorystore(self):
        # INTENTIONAL: __all__ is the declared export surface (the
        # core/platform/__init__.py convention this fix follows).
        self.assertIn("MemoryStore", memory_pkg.__all__)

    def test_export_is_real_fact_round_trip(self):
        # INTENTIONAL: the export must be usable, not merely
        # present — a real store on a temp path, write + read-back.
        tmp = Path(tempfile.mkdtemp(prefix="memorystore_export_"))
        store = MemoryStore(tmp / "assistant.db")
        self.assertTrue(store.save_fact("general", "export_probe", "yes"))
        got = store.get_fact("general", "export_probe")
        self.assertIsNotNone(got)
        self.assertEqual(got["value"], "yes")

    def test_submodule_path_still_works(self):
        # INTENTIONAL: the fix adds a package export; it must not
        # disturb the submodule path every in-tree consumer uses.
        tmp = Path(tempfile.mkdtemp(prefix="memorystore_export_"))
        store = MemoryStoreSubmodule(tmp / "assistant.db")
        self.assertTrue(store.save_fact("general", "submodule_probe", "ok"))
        self.assertEqual(
            store.get_fact("general", "submodule_probe")["value"], "ok"
        )

    def test_singleton_stays_submodule_only(self):
        # INTENTIONAL: the memory_store singleton is deliberately
        # NOT re-exported from the package (no consumer needs it
        # there; all in-tree consumers use core.memory.memory).
        # This pins the boundary so a future edit extends the
        # surface deliberately, not by accident.
        self.assertFalse(hasattr(memory_pkg, "memory_store"))


if __name__ == "__main__":
    unittest.main()
