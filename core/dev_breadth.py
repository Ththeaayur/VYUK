"""Wave 4 P17 slices — terminal mode, traces, diff storyteller, sandbox runner.

Stdlib only, no shell execution, no network, no real filesystem.
The sandboxed code runner here evaluates no code at all: it
records a run request, checks it against an allow-list, and
returns a dry-run verdict. Any real execution is a separate,
caller-gated build, never this module.

Rules, enforced in code:
  * Terminal mode records commands as text; it never runs them.
    A command outside the caller-supplied allow-list is refused
    in the record, never executed.
  * Traces are append-only records of named events with plain
    detail. An event without a name is refused.
  * Diff storyteller turns a caller-supplied added/removed/changed
    list into plain words. It never claims what a change means
    beyond the lists it was given.
  * Sandboxed runner: allowed commands only, no shell, no network,
    no filesystem — and even then, this module only records the
    verdict; it does not execute.
"""

from __future__ import annotations


def record_terminal_command(command: str, allow_list: list) -> dict:
    """Record a terminal-mode command. Never runs it."""
    if not command:
        return {"ok": False, "reason": "command is empty"}
    first = command.strip().split()[0]
    if first not in allow_list:
        return {"ok": False, "command": command,
                "reason": f"command '{first}' is not on the allow-list"}
    return {"ok": True, "command": command,
            "note": "recorded only: nothing was executed"}


class TraceLog:
    """Append-only trace of named events."""

    def __init__(self):
        self.events = []

    def event(self, name: str, detail: str = "") -> dict:
        if not name:
            return {"ok": False, "reason": "trace event must have a name"}
        entry = {"name": name, "detail": detail}
        self.events.append(entry)
        return {"ok": True, "entry": entry}

    def story(self) -> list:
        return list(self.events)


def tell_diff(added: list, removed: list, changed: list) -> dict:
    """Tell a diff in plain words, from caller-supplied lists only."""
    parts = []
    if added:
        parts.append(f"added: {', '.join(added)}")
    if removed:
        parts.append(f"removed: {', '.join(removed)}")
    if changed:
        parts.append(f"changed: {', '.join(changed)}")
    summary = "; ".join(parts) if parts else "no changes"
    return {"added": list(added), "removed": list(removed),
            "changed": list(changed), "summary": summary}


def sandboxed_run(command: str, allow_list: list) -> dict:
    """Dry-run verdict for a sandboxed code run. Executes nothing.

    Even an allowed command returns a verdict only: no shell, no
    network, no filesystem access happens in this module, ever.
    """
    check = record_terminal_command(command, allow_list)
    if not check["ok"]:
        return check
    return {"ok": True, "command": command,
            "verdict": "allowed by allow-list, but NOT executed here: "
                       "real execution needs a separate caller-gated build"}
