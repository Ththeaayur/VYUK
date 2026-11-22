"""The chrome://inspect toggle-left-on flag (loophole batch 2, #4).

The toggle is Chrome's own switch: VYUK cannot flip it, and the
DevToolsActivePort file it writes outlives the server, so only a
live probe counts as "on". This module is only the durable
RECORD — set when VYUK attaches through the toggle, cleared
when a probe verifies the toggle is off. The verification lives
in core/desktop/browser_enable.py (toggle_left_on), which owns
the CDP probe seam; this module never probes, so it stays
importable without the browser stack.

The store mirrors core/confirm_gate_allowlist.py's paused store:
one JSON file under the tree's data/ (data/browser_toggle_left_on.json),
atomic mkstemp + os.replace writes, and a broken store reading
as "not set" — a flag that cannot be read must never be shown
as on, and a flag that cannot be written must be reported by
the caller (a record that did not stick is exactly the
silent-failure class v5.54.1 kills elsewhere).
"""

import json
import logging
import os
import pathlib
import tempfile
import time

log = logging.getLogger(__name__)

_TOGGLE_FILE = "browser_toggle_left_on.json"


def _flag_path():
    # browser_toggle.py lives in core/desktop/, so the tree root
    # is three parents up (the paused store's two-parent climb
    # is for core/confirm_gate_allowlist.py, one level shallower).
    return (pathlib.Path(__file__).resolve().parent.parent.parent
            / "data" / _TOGGLE_FILE)


def toggle_flag() -> dict | None:
    """The recorded flag ({port, recorded_at}), or None when no
    flag is recorded or the store is broken (a broken store
    reads as not set, never as on)."""
    try:
        data = json.loads(_flag_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("port"):
            return data
    except Exception:  # noqa: BLE001 — a broken store is not set
        pass
    return None


def toggle_flag_set() -> bool:
    """True when a toggle-attach flag is recorded (unverified —
    the file alone does not prove the toggle is still on)."""
    return toggle_flag() is not None


def note_toggle_attach(port: int) -> bool:
    """Record that VYUK attached through the chrome://inspect
    toggle on ``port``. True when the record stuck; False (never
    raising) when it did not — the caller must say so, because a
    record that did not stick leaves the toggle on silently."""
    try:
        path = _flag_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"port": int(port),
                   "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=1))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("browser toggle flag save failed (%s: %s)",
                    type(e).__name__, e)
        return False


def clear_toggle_flag() -> bool:
    """Clear the recorded flag (the toggle was verified off).
    True when no flag remains (already clear counts as True);
    False (never raising) when the file could not be removed."""
    try:
        _flag_path().unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("browser toggle flag clear failed (%s: %s)",
                    type(e).__name__, e)
        return False
