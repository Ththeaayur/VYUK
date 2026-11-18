"""Per-monitor wallpaper — the IDesktopWallpaper side (R4, 2026-11-06).

Refinement R4 closes the Fix 2 residual: ``SystemParametersInfo``
sets ONE wallpaper on ALL monitors. This module is the per-monitor
API — ``IDesktopWallpaper`` (Windows 8+, COM, reached with stdlib
``ctypes`` only) — plus the seams the wallpaper slot and set paths
use. It owns NO slot and NO tool: the record lives in
``core/wallpaper.py`` (one file, version 2), the monitor table and
the plain-language selector live in ``core/desktop/monitors.py``
(``read_table`` + ``resolve_monitor`` + ``re_resolve`` — reused,
not re-invented).

Design (one resolver, one API, one fallback):

* Identity is the Windows device name (``\\\\.\\\\DISPLAY2``) from
  the monitor table — never the index. A reorder relabels the
  "second monitor" shorthand; the record key does not move.
  ``resolve_wallpaper_monitor`` resolves a selector against a
  FRESH table on every call and refuses (listing the current
  monitors) instead of guessing.
* The COM wrapper is created fresh per operation and Released in
  a finally — no cached COM object, no lifetime questions.
  Every seam below is a module attribute tests monkeypatch
  (the ``_read_current_wallpaper`` pattern): the lab has no
  Windows desktop, so the real COM calls are field work, and
  the logic above them is proved through fakes.
* ``per_monitor_available()`` is the availability test: True
  only when the COM object can be created. Callers fall back to
  the SPI all-monitors path ONLY on unavailability — and say so.
  A COM object that exists but refuses a set is an honest
  failure, never a silent fallback (falling back would change
  ALL monitors when one was asked for).
* Kinds: a monitor's previous is stored verbatim as
  ``image`` + ``kind`` + ``color`` + ``slideshow``. ``kind`` is
  "image" (a file path), "solid" (``GetWallpaper`` returns ""),
  or "slideshow" (the slideshow engine is on). The slideshow
  engine itself is GLOBAL Windows state (one folder / interval /
  shuffle for the whole desktop); the per-monitor record stores
  the engine's state as it was, and revert restores the
  monitor's visible frame plus the engine state — the reply
  says the engine is shared, never that it is per-monitor.
"""

from __future__ import annotations

import platform

MODULE_VERSION = "6.2.0-r4"

# Tests only: a backend for the monitor table (the
# ``_PATH_OVERRIDE`` pattern). Production always passes None and
# reads the live table.
_TABLE_BACKEND = None


def is_windows() -> bool:
    return platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Monitor table + selector (reused engine)
# ---------------------------------------------------------------------------

def enumerate_monitors():
    """The CURRENT monitor table, canonically sorted. Returns
    (table, note): note is "" on success, else the honest reason
    the table could not be read. Never raises; an unreadable
    table yields [], never a stale one."""
    from .desktop import monitors as _monitors  # lazy: avoids import cycles

    try:
        return _monitors.read_table(_TABLE_BACKEND)
    except Exception as exc:  # noqa: BLE001 — backend failure is data
        return [], f"Could not read the monitor list: {exc}."


def resolve_wallpaper_monitor(selector):
    """Resolve a wallpaper monitor selector against a FRESH table.

    Returns (monitor, refusal, is_all):
    * selector None/"" -> (None, "", False): no selector — the
      caller uses the legacy SPI all-monitors path.
    * "all" -> (None, "", True): every monitor in the table.
    * anything else -> resolve_monitor's answer: a monitor dict
      (with "monitor_id" = device name), or (None, refusal, False)
      with the refusal listing the current monitors.
    Never raises; an unreadable table refuses honestly."""
    from .desktop import monitors as _monitors  # lazy: avoids import cycles

    if selector is None:
        return None, "", False
    text = str(selector).strip()
    if not text:
        return None, "", False
    if text.lower() == "all":
        return None, "", True
    table, note = enumerate_monitors()
    if not table:
        return None, (
            "No monitors could be read, so no screen can be named"
            + (f" ({note})" if note else "")
            + f" { _monitors.REOBSERVE_INSTRUCTION}"
        ), False
    monitor, refusal = _monitors.resolve_monitor(table, text)
    return monitor, refusal, False


def monitor_label(monitor) -> str:
    """A plain-language label for a monitor dict: primary / left /
    right / device name. A receipt word, never identity."""
    if not isinstance(monitor, dict):
        return str(monitor or "")
    from .desktop import monitors as _monitors  # lazy: avoids import cycles

    table, _note = enumerate_monitors()
    label = _monitors._position_label(monitor, table) if table else ""
    device = monitor.get("monitor_id") or monitor.get("device") or ""
    if label:
        return f"{label} ({device})" if device else label
    return device or "unknown monitor"


# ---------------------------------------------------------------------------
# IDesktopWallpaper via ctypes (Windows 8+; stdlib only)
# ---------------------------------------------------------------------------
#
# CLSID_DesktopWallpaper {C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}
# IID_IDesktopWallpaper {B92B56A9-8B55-4E14-9A89-0199BBB6F93B}
# IUnknown vtable slots 0-2 (QueryInterface, AddRef, Release), then:
#   3 SetWallpaper(LPCWSTR monitorID, LPCWSTR wallpaper)
#   4 GetWallpaper(LPCWSTR monitorID, LPWSTR *wallpaper)
#   5 GetMonitorDevicePathAt(UINT monitorIndex, LPWSTR *monitorID)
#   6 GetMonitorDevicePathCount(UINT *count)
#   7 GetMonitorRECT(LPCWSTR monitorID, RECT *displayRect)
#   8 SetBackgroundColor(COLORREF backgroundColor)
#   9 GetBackgroundColor(COLORREF *backgroundColor)
#  10 SetPosition(DESKTOP_WALLPAPER_POSITION position)
#  11 GetPosition(DESKTOP_WALLPAPER_POSITION *position)
#  12 SetSlideshow(IShellItemArray *items)
#  13 GetSlideshow(IShellItemArray **items)
#  14 SetSlideshowOptions(DESKTOP_SLIDESHOW_OPTIONS options,
#                          UINT slideshowTick)
#  15 GetSlideshowOptions(DESKTOP_SLIDESHOW_OPTIONS *options,
#                          UINT *slideshowTick)
#  16 AdvanceSlideshow(LPCWSTR monitorID,
#                       DESKTOP_SLIDESHOW_DIRECTION direction)
#  17 GetStatus(DESKTOP_SLIDESHOW_STATE *state)
#  18 Enable(BOOL enable)
#
# String out-parameters are CoTaskMem-allocated by the callee and
# freed here with CoTaskMemFree. DESKTOP_SLIDESHOW_OPTIONS:
# DSSO_SHUFFLE = 0x01. DESKTOP_SLIDESHOW_STATE: DSSS_ENABLED = 0x01,
# DSSS_SLIDESHOW = 0x02. Only the methods this batch needs are
# wrapped; the rest of the vtable is left untouched.

import ctypes  # noqa: E402 — stdlib, after the module docstring
from ctypes import wintypes  # noqa: E402

_S_OK = 0
_CLSCTX_INPROC_SERVER = 0x1


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    import uuid

    raw = uuid.UUID(text).bytes_le
    guid = _GUID()
    ctypes.memmove(ctypes.byref(guid), raw, ctypes.sizeof(guid))
    return guid


_CLSID_DESKTOP_WALLPAPER = _guid("C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD")
_IID_IDESKTOP_WALLPAPER = _guid("B92B56A9-8B55-4E14-9A89-0199BBB6F93B")

_DSSO_SHUFFLE = 0x01
_DSSS_ENABLED = 0x01
_DSSS_SLIDESHOW = 0x02

_SLIDESHOW_KEY = (
    r"Control Panel\Personalization\Desktop Slideshow")


class _DesktopWallpaper:
    """One IDesktopWallpaper COM object. Created fresh per
    operation by :func:`_get_desktop_wallpaper` and Released in
    a finally — no cached COM object, no lifetime questions.
    Never raises out of the constructor: a failed
    CoCreateInstance yields None from the factory instead."""

    def __init__(self, ptr):
        self._ptr = ptr

    def release(self):
        try:
            self._call(2, ctypes.c_ulong, ())
        except Exception:  # noqa: BLE001 — release is best-effort
            pass
        self._ptr = None

    def _call(self, index, restype, argtypes, *args):
        vtbl = ctypes.cast(
            self._ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        )[0]
        func = vtbl[index]
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(func)(self._ptr, *args)

    def set_wallpaper(self, monitor_id: str, image: str) -> bool:
        try:
            hr = self._call(
                3, ctypes.c_long,
                (ctypes.c_wchar_p, ctypes.c_wchar_p),
                monitor_id, image)
        except Exception:  # noqa: BLE001 — COM failure is data
            return False
        return hr == _S_OK

    def get_wallpaper(self, monitor_id: str) -> str | None:
        """The wallpaper path for one monitor, "" for a solid
        colour, None when unreadable."""
        out = ctypes.c_wchar_p()
        try:
            hr = self._call(
                4, ctypes.c_long,
                (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p)),
                monitor_id, ctypes.byref(out))
        except Exception:  # noqa: BLE001 — COM failure is data
            return None
        if hr != _S_OK:
            return None
        value = out.value or ""
        try:
            ctypes.windll.ole32.CoTaskMemFree(
                ctypes.cast(out, ctypes.c_void_p))
        except Exception:  # noqa: BLE001 — free is best-effort
            pass
        return value

    def get_background_color(self) -> str:
        """Background colour as "#RRGGBB", "" when unreadable.
        COLORREF is 0x00BBGGRR."""
        color = ctypes.c_ulong(0)
        try:
            hr = self._call(
                9, ctypes.c_long,
                (ctypes.POINTER(ctypes.c_ulong),),
                ctypes.byref(color))
        except Exception:  # noqa: BLE001 — COM failure is data
            return ""
        if hr != _S_OK:
            return ""
        value = int(color.value)
        red = value & 0xFF
        green = (value >> 8) & 0xFF
        blue = (value >> 16) & 0xFF
        return "#%02x%02x%02x" % (red, green, blue)

    def set_background_color(self, color: str) -> bool:
        """color is "#RRGGBB". False (never raising) when the
        string is not a colour or the call fails."""
        try:
            text = str(color).strip().lstrip("#")
            if len(text) != 6:
                return False
            red = int(text[0:2], 16)
            green = int(text[2:4], 16)
            blue = int(text[4:6], 16)
        except (TypeError, ValueError):
            return False
        colorref = (blue << 16) | (green << 8) | red
        try:
            hr = self._call(
                8, ctypes.c_long, (ctypes.c_ulong,), colorref)
        except Exception:  # noqa: BLE001 — COM failure is data
            return False
        return hr == _S_OK

    def get_slideshow_options(self) -> tuple[int, int] | None:
        """(options, tick_ms) or None when unreadable."""
        options = ctypes.c_uint(0)
        tick = ctypes.c_uint(0)
        try:
            hr = self._call(
                15, ctypes.c_long,
                (ctypes.POINTER(ctypes.c_uint),
                 ctypes.POINTER(ctypes.c_uint)),
                ctypes.byref(options), ctypes.byref(tick))
        except Exception:  # noqa: BLE001 — COM failure is data
            return None
        if hr != _S_OK:
            return None
        return int(options.value), int(tick.value)

    def set_slideshow_options(self, options: int, tick_ms: int) -> bool:
        try:
            hr = self._call(
                14, ctypes.c_long,
                (ctypes.c_uint, ctypes.c_uint),
                int(options), int(tick_ms))
        except Exception:  # noqa: BLE001 — COM failure is data
            return False
        return hr == _S_OK

    def get_status(self) -> int | None:
        """DESKTOP_SLIDESHOW_STATE bitmask, None when unreadable."""
        state = ctypes.c_uint(0)
        try:
            hr = self._call(
                17, ctypes.c_long,
                (ctypes.POINTER(ctypes.c_uint),),
                ctypes.byref(state))
        except Exception:  # noqa: BLE001 — COM failure is data
            return None
        if hr != _S_OK:
            return None
        return int(state.value)

    def enable_slideshow(self, enable: bool) -> bool:
        try:
            hr = self._call(
                18, ctypes.c_long, (wintypes.BOOL,),
                bool(enable))
        except Exception:  # noqa: BLE001 — COM failure is data
            return False
        return hr == _S_OK


def _get_desktop_wallpaper():
    """A fresh IDesktopWallpaper, or None when it cannot be
    created (non-Windows, COM unavailable). Injectable seam:
    tests monkeypatch this module attribute. Never raises."""
    if not is_windows():
        return None
    try:
        ole32 = ctypes.windll.ole32
        out = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_DESKTOP_WALLPAPER),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_IDESKTOP_WALLPAPER),
            ctypes.byref(out))
        if hr != _S_OK or not out.value:
            return None
        return _DesktopWallpaper(out)
    except Exception:  # noqa: BLE001 — COM failure is data
        return None


def per_monitor_available() -> bool:
    """True only when the per-monitor API can be created right
    now. The availability test callers use before choosing the
    per-monitor path or the honest SPI fallback. Never raises."""
    obj = _get_desktop_wallpaper()
    if obj is None:
        return False
    try:
        obj.release()
    except Exception:  # noqa: BLE001 — release is best-effort
        pass
    return True


# ---------------------------------------------------------------------------
# Per-monitor seams (monkeypatchable, like _read_current_wallpaper)
# ---------------------------------------------------------------------------

def get_wallpaper_for(monitor_id: str) -> str | None:
    """The wallpaper path for one monitor, "" for a solid colour,
    None when unreadable. Never raises."""
    obj = _get_desktop_wallpaper()
    if obj is None:
        return None
    try:
        return obj.get_wallpaper(monitor_id)
    finally:
        obj.release()


def set_wallpaper_for(monitor_id: str, image: str) -> bool:
    """Set one monitor's wallpaper. False (never raising) when
    the API is unavailable or refuses."""
    obj = _get_desktop_wallpaper()
    if obj is None:
        return False
    try:
        return obj.set_wallpaper(monitor_id, image)
    finally:
        obj.release()


def get_background_color() -> str:
    """Background colour as "#RRGGBB", "" when unreadable.
    Never raises."""
    obj = _get_desktop_wallpaper()
    if obj is None:
        return ""
    try:
        return obj.get_background_color()
    finally:
        obj.release()


def set_background_color(color: str) -> bool:
    """Set the background colour ("#RRGGBB"). False (never
    raising) when unavailable, malformed, or refused."""
    if not color:
        return False
    obj = _get_desktop_wallpaper()
    if obj is None:
        return False
    try:
        return obj.set_background_color(color)
    finally:
        obj.release()


def _read_slideshow_registry() -> dict | None:
    """The slideshow engine's registry state {root, interval_ms,
    shuffle}, or None when unreadable (incl. non-Windows).
    Injectable seam: tests monkeypatch this module attribute."""
    if not is_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            _SLIDESHOW_KEY) as key:
            root, _t = winreg.QueryValueEx(key, "ImagesRootPath")
            interval, _t = winreg.QueryValueEx(key, "Interval")
            shuffle, _t = winreg.QueryValueEx(key, "Shuffle")
    except Exception:  # noqa: BLE001 — incl. winreg missing on Linux
        return None
    return {"root": str(root or ""),
            "interval_ms": int(interval or 0),
            "shuffle": bool(shuffle)}


def _write_slideshow_registry(state: dict) -> bool:
    """Write the slideshow engine's registry state. False (never
    raising) on failure. Injectable seam, like
    _read_slideshow_registry."""
    if not is_windows():
        return False
    try:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                _SLIDESHOW_KEY) as key:
            winreg.SetValueEx(key, "ImagesRootPath", 0,
                              winreg.REG_SZ, str(state.get("root", "")))
            winreg.SetValueEx(key, "Interval", 0,
                              winreg.REG_DWORD,
                              int(state.get("interval_ms", 0)))
            winreg.SetValueEx(key, "Shuffle", 0,
                              winreg.REG_DWORD,
                              1 if state.get("shuffle") else 0)
    except Exception:  # noqa: BLE001 — incl. winreg missing on Linux
        return False
    return True


def get_slideshow_state() -> dict | None:
    """The slideshow engine's state {active, root, interval_ms,
    shuffle}, or None when unreadable. "active" is the live
    engine state (DSSS_ENABLED | DSSS_SLIDESHOW); the rest is
    the registry state. Never raises."""
    obj = _get_desktop_wallpaper()
    status = None
    options = None
    if obj is not None:
        try:
            status = obj.get_status()
            options = obj.get_slideshow_options()
        finally:
            obj.release()
    registry = _read_slideshow_registry()
    if status is None and registry is None:
        return None
    active = bool(
        status is not None
        and (status & _DSSS_ENABLED)
        and (status & _DSSS_SLIDESHOW))
    shuffle = bool(options and (options[0] & _DSSO_SHUFFLE))
    interval_ms = int(options[1]) if options else 0
    if registry is not None:
        shuffle = bool(registry.get("shuffle")) or shuffle
        if not interval_ms:
            interval_ms = int(registry.get("interval_ms") or 0)
        return {"active": active,
                "root": str(registry.get("root") or ""),
                "interval_ms": interval_ms,
                "shuffle": shuffle}
    return {"active": active, "root": "",
            "interval_ms": interval_ms, "shuffle": shuffle}


def set_slideshow_state(state: dict) -> bool:
    """Restore the slideshow engine's state (registry + options
    + enable). True only when every leg that was attempted
    succeeded; False (never raising) otherwise. The engine is
    GLOBAL Windows state — one folder / interval / shuffle for
    the whole desktop — so restoring it affects every monitor's
    slideshow, never just one."""
    if not isinstance(state, dict):
        return False
    ok_registry = _write_slideshow_registry(state)
    obj = _get_desktop_wallpaper()
    if obj is None:
        return ok_registry
    try:
        options = _DSSO_SHUFFLE if state.get("shuffle") else 0
        ok_options = obj.set_slideshow_options(
            options, int(state.get("interval_ms") or 0))
        ok_enable = obj.enable_slideshow(bool(state.get("active")))
        return bool(ok_registry and ok_options and ok_enable)
    finally:
        obj.release()
