"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

import re
import sys
from .. import input_guard  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level
from typing import Any

# Deliberate gate EXEMPTIONS (v5.9 sweep m15, same posture as
# dns_flush): clear_clipboard, set_default_printer and
# set_screen_timeout stay ungated — user-directed, instantly
# reversible, no persistence beyond the setting itself. Anything
# destructive, persistent or routing-changing does NOT belong here.
# v5.47 (programme item 12): these four are now also recorded in
# _FREE_BY_DESIGN under the "v5.9 deliberate exemption" class, so
# the completeness invariant (every tool classified somewhere)
# covers them without changing this posture.

# press_hotkey (v5.3): most combos are harmless editing keys, but a
# combo carrying a shell-escape key opens a path around the
# run_command gate entirely (Win+R, type a command, Enter — three
# ungated calls that together ARE run_command). The escape class:
# any combo containing a Win/Meta/Super/Cmd key token, plus
# Ctrl+Shift+Esc (Task Manager). Plain Ctrl+C/V/A etc. stay ungated.
_HOTKEY_SHELL_KEYS = frozenset({"win", "meta", "super", "cmd"})

# v5.42 (H11/H15/H16): the shell bypass, closed. Ungated input and
# launch tools composed into run_command without ever touching it:
# launch a shell, type into it, press Enter. The gate now also
# covers (a) launching a shell / script host by name — the set
# below reuses _tool_launch_app's alias knowledge (cmd -> cmd.exe,
# terminal -> wt.exe, powershell -> powershell.exe) and adds the
# other hosts — (b) open_path on an executable/script extension,
# and (c) keyboard input while a shell owns the foreground window.
# v5.46 (programme items 3 + 7): the canonical name/class sets
# live in core/input_guard (SHELL_LAUNCH_NAMES,
# SHELL_FOREGROUND_PROCS, SHELL_FOREGROUND_CLASSES) — the one
# module both this gate and core/desktop/control's typing hands can
# import without a cycle (tools imports control; control imports
# input_guard; input_guard imports neither). They are re-exported
# here under their historical private names so existing imports
# and monkeypatches keep working, and there is exactly ONE set
# for the launch gate and the foreground gate to share.
#
# Widening note (item 3): grep over the tree shows the ONLY
# consumer of _SHELL_LAUNCH_NAMES / _is_shell_launch is the
# launch_application branch of _needs_confirmation below (the
# handler's own alias dict in _tool_launch_app is separate and
# untouched), so widening the shared set with the interpreter /
# code-exec host names (python, pythonw, python3, node, regedit,
# mmc, msiexec, rundll32, regsvr32 + .exe forms — done in
# input_guard) changes nothing else. No second set was needed.
_SHELL_LAUNCH_NAMES = input_guard.SHELL_LAUNCH_NAMES
_SHELL_FOREGROUND_PROCS = input_guard.SHELL_FOREGROUND_PROCS
_SHELL_FOREGROUND_CLASSES = input_guard.SHELL_FOREGROUND_CLASSES

# v5.46 (item 7, A5): .py .pyw .jar .reg .msc .cpl .msix .vbe
# .url .psm1 added — every one of these executes or configures
# code when double-clicked, exactly like the H11 originals. The
# only consumer of _is_executable_path is the open_path branch
# of _needs_confirmation (grep-verified), so the widening is
# confined to that gate.
_EXECUTABLE_PATH_EXTS = frozenset({
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf",
    ".wsh", ".hta", ".lnk", ".msi", ".com", ".scr",
    ".py", ".pyw", ".jar", ".reg", ".msc", ".cpl", ".msix",
    ".vbe", ".url", ".psm1",
})


def _is_shell_launch(app_name: Any) -> bool:
    """True when a launch_application request names a shell,
    script host, INTERPRETER or code-exec host (v5.46, item 3 —
    the shared _SHELL_LAUNCH_NAMES set now carries python /
    pythonw / python3 / node / regedit / mmc / msiexec /
    rundll32 / regsvr32), by display name, alias target or exe
    basename/stem."""
    raw = str(app_name or "").strip().lower()
    if not raw:
        return False
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base[:-4] if base.endswith(".exe") else base
    return (raw in _SHELL_LAUNCH_NAMES or base in _SHELL_LAUNCH_NAMES
            or stem in _SHELL_LAUNCH_NAMES)


# Standalone argument tokens that hand a script/command to a
# host (v5.46, item 3): python's -c, PowerShell's -Command /
# -EncodedCommand, and the -File script switch (cmd, PowerShell,
# wscript/cscript all take a -file//script form). Deliberately
# NOT here: -e (grep/sed/echo's ordinary flag — the v5.46 item-6
# lesson) and anything attached to a value (-cprint(...) is not
# a standalone token and does not match).
_HOST_FLAG_TOKENS = frozenset({
    "-c", "-command", "-encodedcommand", "-file",
})


def _argument_tokens(arguments: Any) -> list[str]:
    """Split an arguments string the way _tool_launch_app does —
    shlex, Windows semantics on Windows; a quoting failure falls
    back to a plain whitespace split so the scrutiny below still
    sees the tokens."""
    text = str(arguments or "").strip()
    if not text:
        return []
    import shlex

    try:
        return shlex.split(
            text, posix=not sys.platform.startswith("win"))
    except ValueError:
        return text.split()


def _arguments_invoke_interpreter(arguments: Any) -> bool:
    """True when a launch_application ARGUMENTS string itself
    invokes an interpreter / shell / script host (v5.46, item 3).

    The exact rule (kept conservative — a false positive costs
    one confirmation popup, a false negative is the C1 hole):
    tokenise like the handler (shlex, Windows semantics on
    Windows), then gate when ANY token either
      (a) IS a standalone host flag: -c, -command,
          -encodedcommand or -file (case-insensitive, the whole
          token — never a substring or an attached value), or
      (b) names an interpreter/host: its basename or stem (path
          separators and a trailing .exe/.cmd/.bat/.com/.scr
          stripped) is in the shared _SHELL_LAUNCH_NAMES set.
    Tokens containing "://" are URLs, never interpreter
    invocations, and are skipped for (b) — so
    launch_application {app_name: "chrome", arguments:
    "https://example.com"} stays ungated. The shlex tokens are
    scanned AND the raw whitespace tokens: POSIX shlex eats the
    backslashes of a Windows path (C:\\Python312\\python.exe),
    and the scrutiny must still see that basename — this is a
    gate, so scanning both tokenisations only ever adds
    sensitivity, never removes it."""
    tokens = _argument_tokens(arguments)
    tokens += str(arguments or "").split()
    for token in tokens:
        low = str(token or "").strip().strip('"').strip("'").lower()
        if not low:
            continue
        if low in _HOST_FLAG_TOKENS:
            return True
        if "://" in low:
            continue
        base = low.replace("\\", "/").rsplit("/", 1)[-1]
        stem = base
        for ext in (".exe", ".cmd", ".bat", ".com", ".scr"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        if base in _SHELL_LAUNCH_NAMES or stem in _SHELL_LAUNCH_NAMES:
            return True
    return False


def _is_executable_path(path: Any) -> bool:
    """True when an open_path target carries an executable/script
    extension (folders and documents return False)."""
    text = str(path or "").strip().strip('"').lower()
    return any(text.endswith(ext) for ext in _EXECUTABLE_PATH_EXTS)


def _foreground_process_name() -> str | None:
    """Process name owning the foreground window, or None when it
    can't be determined. Delegates to core/input_guard's probe
    (dynamic lookup, so patching either module's function is
    honoured); never raises. Module-level seam: suites
    monkeypatch this function to fake the foreground."""
    try:
        return input_guard._foreground_process_name()
    except Exception:  # noqa: BLE001
        return None


def _foreground_window_class() -> str | None:
    """Window class of the foreground window, or None when it
    can't be determined (v5.46, item 7). Delegates to
    core/input_guard's probe; never raises. Module-level seam:
    suites monkeypatch this function to fake the class."""
    try:
        return input_guard._foreground_window_class()
    except Exception:  # noqa: BLE001
        return None


def _platform_is_windows() -> bool:
    """The gate's platform test, factored out as a module-level
    seam (v5.46, item 7): suites monkeypatch THIS helper to fake
    Windows instead of patching sys.platform globally."""
    return sys.platform.startswith("win")


def _foreground_is_shell() -> bool:
    """True when the foreground window is a shell/terminal: its
    process name is in _SHELL_FOREGROUND_PROCS OR its window
    class is in _SHELL_FOREGROUND_CLASSES (v5.46, item 7), or
    BOTH probes are unknown on Windows — blind on the platform
    the gate exists for = gate (fail-closed). Off Windows,
    unknown stays NOT a shell, so the untainted normal path and
    headless/test behaviour are unchanged. The decision itself
    is input_guard._shell_verdict (single source); this wrapper
    feeds it through THIS module's monkeypatchable probe seams.
    Module-level seam: suites monkeypatch this function to fake
    the foreground wholesale."""
    try:
        return input_guard._shell_verdict(
            _foreground_process_name(),
            _foreground_window_class(),
            _platform_is_windows())
    except Exception:  # noqa: BLE001 — never raise from a gate check
        return False


def _normalized_hotkey_tokens(keys: Any) -> set[str]:
    """press_hotkey tokens normalized through core.control's
    canonical key-alias map (v5.42, H15): the executor maps
    "windows" -> "cmd", so the gate must see "cmd" too — checking
    raw tokens let "windows+r" slip past the Win-key rule. If the
    map can't be imported, tokens pass through unnormalized
    (today's behaviour) — the gate never breaks on the import."""
    tokens = {t for t in re.split(r"[^a-z0-9]+",
                                   str(keys or "").lower()) if t}
    try:
        from ..desktop.control import _KEY_ALIASES as alias_map  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F1: ..desktop from inside core/funnel/)
    except Exception:  # noqa: BLE001 — fall back to raw tokens
        return tokens
    return {str(alias_map.get(t, t)).lower() for t in tokens}
