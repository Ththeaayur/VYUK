"""Memory, knowledge, research (Phase 3 M2h package)."""

# INTENTIONAL (memorystore export batch, 2026-11-05): the package
# re-exports MemoryStore from .memory. Before this line, `from
# core.memory import MemoryStore` failed with ImportError even
# though the class lives in core/memory/memory.py — the Phase-3
# export gap that blocked root scripts test_v46_batch3b.py and
# test_v52_episodes.py (both import MemoryStore from the package).
# Convention follows core/platform/__init__.py: relative
# from-import plus an explicit __all__. The memory_store singleton
# is deliberately NOT re-exported here: every in-tree consumer
# imports it via the submodule path (core.memory.memory), and no
# consumer needs it from the package surface.
from .memory import MemoryStore

__all__ = ["MemoryStore"]
