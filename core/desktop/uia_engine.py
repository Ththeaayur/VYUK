"""One UIA engine interface (GAPS-20, review flaw D-9, §6.6).

Before this batch the UIA layer was a single hard-coded engine:
``uia_discover.uia_find_control`` called pywinauto directly and
carried a hard-coded easy-uiauto pilot branch (HA-12/EY-A1 v5.59)
that only find had — describe_point, read_tree and focus never
consulted a second engine — and no code anywhere knew the word
"electron": a Chromium/Electron window's lazily-built
accessibility tree read as a bare "not found".

This module is the one engine interface:

* ``UiaEngine`` — the protocol: name, capabilities, available(),
  find / describe_point / read_tree / focus_label. Two engines
  are registered: ``pywinauto`` (the full engine: find,
  describe_point, read_tree, focus) and ``easy-uiauto`` (the
  pilot: find only — its API is locate-and-prepare, it has no
  tree walk, no point query, no focus read). No engine is ever
  consulted for a capability it does not declare.
* ``find_control`` / ``describe_point`` / ``read_tree`` /
  ``focus_label`` — the routers. Find runs pywinauto first; the
  pilot gets its one look only when pywinauto found nothing, and
  a pilot match never shadows a pywinauto match (the v5.59 rule,
  unchanged). When pywinauto is unavailable the router returns
  the unavailable message WITHOUT trying the pilot (the pre-
  GAPS-20 rule, unchanged — a missing first engine is a build
  problem, not a cue to silently switch engines).
* ``electron_wall`` — the Electron-wall detector. A window whose
  top-level class is ``Chrome_WidgetWin_1`` and whose process is
  not chrome.exe is an Electron/Chromium app: its accessibility
  tree is built lazily, so an empty find / empty tree is named
  as the wall (with the app-adapter route when one is registered
  for the app, else the OCR/coordinate fallback), never as a
  bare "not found". chrome.exe + the same class is Chrome — the
  note points at the browser control (GAPS-18), not at UIA.
  A Chrome_WidgetWin_1 window whose process could not be read
  is named as chromium-unknown, honestly, with both routes.

The platform-backend seam (``_routed_backend``) stays ABOVE this
module: on a non-Windows backend the public functions in
uia_discover still delegate to the backend first, and this
module's engines are the Windows layer only. The pywinauto
bodies themselves did not move — the engine wraps
uia_discover's helpers (imported lazily inside the methods, so
this module never cycles with uia_discover, which imports this
module). The wall is named where an empty result would
otherwise read as a false absence (find + read_tree); it is
NOT named in describe_point (whose None already means
"nothing identifiable") or focus (which reads the OS focus,
not the tree).

Test seams: ``set_engine_override`` / ``reset_engine_overrides``
(inject a fake engine) and ``set_window_identity_override`` /
``reset_window_identity_override`` (inject a fake (class, proc)
identity for a window).
"""

from __future__ import annotations

from typing import Any

from .uia_base import (
    _SCAN_CAP,
    _truncate,
    _uia_window,
    _unavailable,
)

ENGINE_NAMES = ("pywinauto", "easy-uiauto")

ENGINE_CAPABILITIES = {
    "pywinauto": frozenset({"find", "describe_point", "read_tree", "focus"}),
    "easy-uiauto": frozenset({"find"}),
}

_CHROMIUM_WINDOW_CLASS = "Chrome_WidgetWin_1"

# Test seams (mirrors desktop_pilots._easy_uiauto_override).
_ENGINE_OVERRIDES: dict[str, Any] = {}
_WINDOW_IDENTITY_OVERRIDE = None


def set_engine_override(name: str, engine: Any) -> None:
    """Inject a fake engine under `name` (tests only)."""
    _ENGINE_OVERRIDES[str(name)] = engine


def reset_engine_overrides() -> None:
    """Restore the production engines (suite/prover tearDown)."""
    _ENGINE_OVERRIDES.clear()


def set_window_identity_override(fn) -> None:
    """Inject a fake _window_identity (tests only); None restores."""
    global _WINDOW_IDENTITY_OVERRIDE  # noqa: PLW0603 — the seam is the point
    _WINDOW_IDENTITY_OVERRIDE = fn


def reset_window_identity_override() -> None:
    """Restore the production window identity (suite/prover tearDown)."""
    global _WINDOW_IDENTITY_OVERRIDE  # noqa: PLW0603 — the seam is the point
    _WINDOW_IDENTITY_OVERRIDE = None


def _proc_name_from_hwnd(hwnd: int) -> str | None:
    """Basename of the process owning `hwnd`, or None.

    Windows only; ctypes GetWindowThreadProcessId +
    QueryFullProcessImageNameW (the pattern
    skills_hands3_window_focus already uses for the foreground
    window). Never raises — a process that cannot be read is
    unknown, not an error.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)):
                return None
            name = (buf.value or "").strip()
            if not name:
                return None
            import os

            return os.path.basename(name).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — unreadable process is unknown
        return None


def _window_identity(win) -> tuple[str, str | None] | None:
    """(top-level class, process basename) for a pywinauto window
    wrapper, or None when the window cannot be identified at all.

    The class comes from the wrapper's element_info; the process
    from its handle via _proc_name_from_hwnd. A window with a
    class but an unreadable process still identifies (proc None)
    — the wall detector names that case chromium-unknown rather
    than guessing.
    """
    if _WINDOW_IDENTITY_OVERRIDE is not None:
        try:
            return _WINDOW_IDENTITY_OVERRIDE(win)
        except Exception:  # noqa: BLE001 — a broken override is unknown
            return None
    if win is None:
        return None
    try:
        cls = (win.element_info.class_name or "").strip()
    except Exception:  # noqa: BLE001 — dead wrapper is unidentifiable
        return None
    if not cls:
        return None
    proc = None
    try:
        hwnd = win.handle
    except Exception:  # noqa: BLE001 — no handle, no process
        hwnd = None
    if hwnd:
        proc = _proc_name_from_hwnd(int(hwnd))
    return (cls, proc)


def electron_wall(win) -> dict[str, Any] | None:
    """The Electron wall for a pywinauto window wrapper, or None.

    Returns {"kind", "window_class", "proc"} where kind is
    "electron" (Chromium class, non-Chrome process), "chrome"
    (chrome.exe — CDP-driven, not a UIA wall), or
    "chromium-unknown" (Chromium class, process unreadable).
    Any other class, or an unidentifiable window, is not a wall.
    Never raises.
    """
    try:
        ident = _window_identity(win)
    except Exception:  # noqa: BLE001 — identity failure is no wall
        return None
    if ident is None:
        return None
    cls, proc = ident
    if cls != _CHROMIUM_WINDOW_CLASS:
        return None
    if proc is None:
        return {"kind": "chromium-unknown",
                "window_class": cls, "proc": None}
    if proc.lower() == "chrome.exe":
        return {"kind": "chrome", "window_class": cls, "proc": proc}
    return {"kind": "electron", "window_class": cls, "proc": proc}


def _adapter_for_wall(wall: dict[str, Any]) -> str | None:
    """App-adapter id registered for this wall's app, or None.

    Read-only: identify() on the existing registry (populated by
    the funnel at load). A registry with no adapter for the app —
    or a registry that was never populated — reads as None, and
    the wall note says so honestly.
    """
    try:
        from . import app_adapters as _aa

        found = _aa.identify({
            "proc": wall.get("proc") or "",
            "window_class": wall.get("window_class") or "",
        })
        return found.get("matched")
    except Exception:  # noqa: BLE001 — adapter lookup is best-effort
        return None


def wall_note(wall: dict[str, Any]) -> str:
    """One honest sentence for an empty result on a walled window."""
    kind = wall.get("kind")
    if kind == "chrome":
        return (
            "Note: this is Chrome — its controls are driven through "
            "the browser control, not UI Automation."
        )
    if kind == "chromium-unknown":
        return (
            "Note: this window uses the Chromium widget class but its "
            "process could not be read, so I can't tell whether it's "
            "Chrome (drive it through the browser control) or an "
            "Electron app (app adapter if one exists, else OCR or "
            "clicking by coordinates)."
        )
    proc = wall.get("proc") or "an Electron app"
    adapter = _adapter_for_wall(wall)
    if adapter:
        return (
            f"Note: this window is an Electron app ({proc}) — its "
            "accessibility tree is built lazily, so UI Automation "
            "sees little of it. The "
            f"{adapter} app adapter covers this app by exact names — "
            "ask for one of its named controls."
        )
    return (
        f"Note: this window is an Electron app ({proc}) — its "
        "accessibility tree is built lazily, so UI Automation sees "
        "little of it. No app adapter covers this app yet, so the "
        "fallback is OCR or clicking by coordinates."
    )


class _PywinautoEngine:
    """The full engine: pywinauto UIA. Wraps uia_discover's
    bodies (imported lazily — uia_discover imports this module,
    so a top-level import would cycle)."""

    name = "pywinauto"
    capabilities = ENGINE_CAPABILITIES["pywinauto"]

    def available(self) -> tuple[bool, str]:
        msg = _unavailable()
        if msg is None:
            return True, ""
        return False, msg

    def find(self, name: str, window_title: str = "",
             control_type: str = "") -> dict[str, Any]:
        """Structured find: {"status", "report", "matches",
        "scanned", "truncated", "window", "window_desc"}.

        status "found" carries the formatted report and the
        matches; "not-found" carries scanned/truncated/window
        for the router's wall check; "failed" carries the scan
        failure as its report (never worded as not-found);
        "unavailable" carries the _unavailable() message.
        """
        from . import uia_discover as _ud

        ok, reason = self.available()
        if not ok:
            return {"status": "unavailable", "report": reason,
                    "matches": [], "scanned": 0, "truncated": False,
                    "window": None, "window_desc": ""}
        try:
            win, desc = _uia_window(str(window_title or ""))
        except Exception as e:  # noqa: BLE001
            return {"status": "failed",
                    "report": f"Could not connect to the target window: {e}",
                    "matches": [], "scanned": 0, "truncated": False,
                    "window": None, "window_desc": ""}
        matches, scanned, truncated, failure = _ud._find_matches(
            win, name, control_type)
        if failure is not None:
            return {"status": "failed",
                    "report": f"Could not search {desc}: {failure}",
                    "matches": [], "scanned": scanned,
                    "truncated": truncated, "window": win,
                    "window_desc": desc}
        if not matches:
            return {"status": "not-found", "report": None,
                    "matches": [], "scanned": scanned,
                    "truncated": truncated, "window": win,
                    "window_desc": desc}
        if truncated:
            header = (
                f"Found at least {len(matches)} control(s) matching "
                f"'{name}' in {desc} (reporting capped — the window "
                "has more):"
            )
        else:
            header = (
                f"Found {len(matches)} control(s) matching '{name}' "
                f"in {desc}:"
            )
        report = _truncate(
            header + "\n" + "\n".join(
                _ud._format_facts(f) for _, f in matches), 1500)
        return {"status": "found", "report": report,
                "matches": matches, "scanned": scanned,
                "truncated": truncated, "window": win,
                "window_desc": desc}

    def describe_point(self, x: int, y: int) -> str | None:
        """Today's uia_discover.describe_point body, verbatim in
        shape: the '"Name" (Type) in Window' / '(no named control)
        in Window' / None contract is unchanged."""
        ok, _reason = self.available()
        if not ok:
            return None
        from . import uia_discover as _ud

        try:
            el = _ud._point_element(int(x), int(y))
        except Exception:  # noqa: BLE001
            return None
        if el is None:
            return None
        facts = _ud._element_facts(el)
        name = facts["name"][:60] if facts else ""
        control_type = facts["control_type"] if facts else ""
        window = ""
        try:
            top = el.top_level_parent()
            if top is not None:
                top_facts = _ud._element_facts(top)
                window = top_facts["name"] if top_facts else ""
                if not window:
                    window = (top.window_text() or "").strip()
        except Exception:  # noqa: BLE001 — window title is best-effort
            window = ""
        window = window[:80]
        if name:
            label = f'"{name}"'
            if control_type:
                label += f" ({control_type})"
            return f"{label} in {window}" if window else label
        if window:
            return f"(no named control) in {window}"
        return None

    def read_tree(self, window_title: str = "", max_depth: int = 3,
                  max_nodes: int = 80) -> dict[str, Any]:
        """Structured tree read: {"status", "report", "window",
        "window_desc"}. status "ok" / "empty" (no controls read)
        / "failed" / "unavailable". The body is today's
        uia_read_tree walk, verbatim in shape."""
        from . import uia_discover as _ud

        ok, reason = self.available()
        if not ok:
            return {"status": "unavailable", "report": reason,
                    "window": None, "window_desc": ""}
        try:
            max_depth = max(0, min(int(max_depth), 8))
        except (TypeError, ValueError):
            max_depth = 3
        try:
            max_nodes = max(1, min(int(max_nodes), 300))
        except (TypeError, ValueError):
            max_nodes = 80
        try:
            win, desc = _uia_window(str(window_title or ""))
        except Exception as e:  # noqa: BLE001
            return {"status": "failed",
                    "report": f"Could not connect to the target window: {e}",
                    "window": None, "window_desc": ""}
        lines: list[str] = []
        state = {"shown": 0, "truncated": False}

        def _label(el) -> str:
            facts = _ud._element_facts(el)
            if facts is None:
                return "(unreadable element)"
            name = facts["name"] or "(unnamed)"
            return f"{name} [{facts['control_type']}]"

        def _walk(el, depth: int) -> None:
            if state["shown"] >= max_nodes:
                state["truncated"] = True
                return
            state["shown"] += 1
            lines.append("  " * depth + "• " + _label(el))
            try:
                children = el.children()
            except Exception:  # noqa: BLE001 — dead element mid-walk
                return
            if depth >= max_depth:
                if children:
                    state["truncated"] = True
                return
            for child in children:
                _walk(child, depth + 1)

        _walk(win, 0)
        if not lines:
            return {"status": "empty",
                    "report": f"Could not read any controls from {desc}.",
                    "window": win, "window_desc": desc}
        header = (
            f"Control tree of {desc} — {state['shown']} node(s) shown "
            f"(depth ≤ {max_depth}, cap {max_nodes}):"
        )
        if state["truncated"]:
            header += (
                "\n⚠ Partial tree — the window has more controls than "
                "shown here. Don't conclude a control is absent from "
                "this window: raise max_nodes / max_depth, or read a "
                "specific sub-window."
            )
        return {"status": "ok",
                "report": _truncate(header + "\n" + "\n".join(lines), 3000),
                "window": win, "window_desc": desc}

    def focus_label(self) -> str | None:
        """Today's _focused_control_label body, verbatim in shape."""
        from . import uia_discover as _ud

        try:
            el = _ud._focused_element()
        except Exception:  # noqa: BLE001
            return None
        if el is None:
            return None
        facts = _ud._element_facts(el)
        if not facts or not facts["name"]:
            return None
        label = f'"{facts["name"][:60]}"'
        if facts["control_type"]:
            label += f" ({facts['control_type']})"
        return label


class _EasyUiautoEngine:
    """The pilot engine: easy-uiauto. Find only — its API is
    locate-and-prepare, it has no tree walk, no point query, no
    focus read, so those capabilities are absent and the router
    never asks for them."""

    name = "easy-uiauto"
    capabilities = ENGINE_CAPABILITIES["easy-uiauto"]

    def available(self) -> tuple[bool, str]:
        try:
            import easy_uiauto  # noqa: F401
        except Exception as e:  # noqa: BLE001 — pilot absent
            return False, f"easy-uiauto is not installed ({e})"
        return True, ""

    def find(self, name: str, window_title: str = "",
             control_type: str = "") -> dict[str, Any]:
        """{"status": "found"/"not-found", "report"} — the pilot's
        own report string when it locates a control (already
        pilot-labelled by easy_uiauto_find), else None. Never
        raises; a pilot failure is not a find."""
        try:
            from . import desktop as _desktop_v559

            pilot = _desktop_v559.easy_uiauto_find(
                name, str(window_title or ""), control_type)
        except Exception:  # noqa: BLE001 — pilot failure is not a find
            pilot = None
        if pilot:
            return {"status": "found", "report": pilot}
        return {"status": "not-found", "report": None}

    def describe_point(self, x: int, y: int) -> str | None:
        return None

    def read_tree(self, window_title: str = "", max_depth: int = 3,
                  max_nodes: int = 80) -> dict[str, Any]:
        return {"status": "unsupported",
                "report": "easy-uiauto has no tree read",
                "window": None, "window_desc": ""}

    def focus_label(self) -> str | None:
        return None


_ENGINES: dict[str, Any] = {}


def get_engine(name: str):
    """The engine instance for `name` ("pywinauto" /
    "easy-uiauto"); raises KeyError naming the known engines."""
    if name in _ENGINE_OVERRIDES:
        return _ENGINE_OVERRIDES[name]
    if name not in _ENGINES:
        if name == "pywinauto":
            _ENGINES[name] = _PywinautoEngine()
        elif name == "easy-uiauto":
            _ENGINES[name] = _EasyUiautoEngine()
        else:
            raise KeyError(
                f"unknown UIA engine {name!r} "
                f"(known: {', '.join(ENGINE_NAMES)})")
    return _ENGINES[name]


def engines_status() -> dict[str, Any]:
    """Availability + capabilities per engine, without opening
    anything. The pywinauto verdict is _unavailable(); the
    easy-uiauto verdict is the import probe."""
    out = {}
    for name in ENGINE_NAMES:
        engine = get_engine(name)
        ok, reason = engine.available()
        out[name] = {
            "available": ok,
            "reason": reason,
            "capabilities": sorted(engine.capabilities),
        }
    return out


# -- routers ---------------------------------------------------------------

def find_control(name: str, window_title: str = "",
                 control_type: str = "") -> str:
    """Find UI controls by name through the one engine interface.

    pywinauto first; the easy-uiauto pilot gets its one look
    only when pywinauto found nothing, and a pilot match never
    shadows a pywinauto match. A pywinauto scan failure returns
    as-is (never worded as not-found, never retried by the
    pilot). When neither engine found anything, the Electron
    wall is named on the window instead of a bare not-found.
    """
    if not name or not str(name).strip():
        return ("No control name given. Tell me the label of the "
                "control to look for.")
    first = get_engine("pywinauto")
    result = first.find(name, window_title, control_type)
    if result["status"] in ("found", "failed", "unavailable"):
        return result["report"]
    # Not found on the first engine — the pilot gets one look.
    pilot = get_engine("easy-uiauto")
    pilot_result = pilot.find(name, window_title, control_type)
    if pilot_result["status"] == "found":
        return pilot_result["report"]
    desc = result.get("window_desc") or (
        f"window matching '{window_title}'" if window_title
        else "the foreground window")
    scanned = int(result.get("scanned") or 0)
    truncated = bool(result.get("truncated"))
    note = ""
    if truncated:
        note = (
            f" (the scan stopped at {_SCAN_CAP} elements, so the "
            "control may exist deeper in the window)"
        )
    text = (
        f"No control with a name containing '{name}' was found in "
        f"{desc} (scanned {scanned} elements){note}."
    )
    wall = electron_wall(result.get("window"))
    if wall is not None:
        text += f"\n{wall_note(wall)}"
    return text


def describe_point(x: int, y: int) -> str | None:
    """Identify the control at screen point (x, y) — pywinauto
    only (easy-uiauto declares no point capability). The
    '"Name" (Type) in Window' / '(no named control) in Window' /
    None contract is unchanged."""
    return get_engine("pywinauto").describe_point(x, y)


def read_tree(window_title: str = "", max_depth: int = 3,
              max_nodes: int = 80) -> str:
    """Read a window's UI Automation control tree through the one
    engine interface. An empty tree on a walled window names the
    wall instead of reading as a false absence."""
    result = get_engine("pywinauto").read_tree(
        window_title, max_depth=max_depth, max_nodes=max_nodes)
    if result["status"] == "empty":
        wall = electron_wall(result.get("window"))
        if wall is not None:
            return f"{result['report']}\n{wall_note(wall)}"
    return result["report"]


def focus_label() -> str | None:
    """Label of the element holding keyboard focus — pywinauto
    only (easy-uiauto declares no focus capability)."""
    return get_engine("pywinauto").focus_label()
