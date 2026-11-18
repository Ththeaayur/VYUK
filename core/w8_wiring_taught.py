"""w8_wiring part module (skills split B14): w8_wiring_taught."""
from __future__ import annotations

import json
from pathlib import Path  # FIX (skills split B14, caught by import-completeness proof): taught_dir/_taught_path use Path.
# Module-level imports travel verbatim with the units that use them.
from .brain import teach  # Phase 3 B1b: teach moved to core/brain/
# INTENTIONAL (skills split B14): cross-part names from funnel/registry (P3).
from .w8_wiring_funnel import funnel_tool_executor
from .w8_wiring_registry import live_tool_names



# ---------------------------------------------------------------------
# Teach mode: file-backed store + funnel replay (item 6)
# ---------------------------------------------------------------------

TAUGHT_DIRNAME = "taught_sequences"




def taught_dir(directory=None) -> Path:
    if directory is not None:
        return Path(directory)
    from .config import DATA_DIR

    return Path(str(DATA_DIR)) / TAUGHT_DIRNAME




def _taught_path(name: str, directory=None) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_"
                   for c in name)
    return taught_dir(directory) / f"{safe}.json"




def record_taught_sequence(name: str, raw_steps, pattern=None,
                           directory=None):
    """Record a demonstration (teach.record) AND persist it to
    the file-backed store. This is the teach entry point up to
    live capture — live demonstration capture itself is GATED
    (TEACH_LIVE_CAPTURE_GATE); the voice trigger is the router
    path. Returns the TaughtSequence."""
    sequence = teach.record(name, raw_steps, pattern=pattern)
    path = _taught_path(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sequence.to_dict(), indent=1,
                               sort_keys=True) + "\n",
                    encoding="utf-8")
    return sequence




def load_taught_sequence(name: str, directory=None):
    """Load a stored sequence, or None when it is not stored."""
    path = _taught_path(name, directory)
    if not path.exists():
        return None
    return teach.TaughtSequence.from_dict(
        json.loads(path.read_text(encoding="utf-8")))




def list_taught_sequences(directory=None) -> list[str]:
    root = taught_dir(directory)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json"))




def replay_taught_sequence(name_or_sequence, inputs, *,
                           executor=None, directory=None) -> Any:
    """Replay a taught sequence through the FUNNEL executor
    (default: funnel_tool_executor over the live registry),
    verified before each next step (the module halts on a
    failed verification — no silent skip, no faked success).
    Available tools are the LIVE registry names, so a step
    naming a non-existent tool is refused before anything runs.
    """
    sequence = (name_or_sequence
                if isinstance(name_or_sequence, teach.TaughtSequence)
                else load_taught_sequence(name_or_sequence,
                                          directory=directory))
    if sequence is None:
        raise ValueError(
            f"no taught sequence stored as {name_or_sequence!r}")
    return teach.replay(
        sequence, executor or funnel_tool_executor, inputs,
        available_tools=live_tool_names())


