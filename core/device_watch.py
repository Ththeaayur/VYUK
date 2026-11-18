"""Default-device switch watcher (v5.54, Wave 0 — wave 9
EA-10).

WHY: when the machine's default microphone changes mid-session
— headphones plugged in, a USB mic removed, Windows re-routing
— VYUK used to keep listening to the device she opened at
start: a stream aimed at a device that is no longer the
default, or no longer there. This watcher is her own
IMMNotificationClient (via core/mmdevice.py, on comtypes): the
OS says the default changed, she reopens on the new default and
announces the switch in plain words (the AudioManager side of
that contract lives in core/voice/audio.py).

Threading shape: registration happens on a dedicated MTA
worker thread (COM callbacks then arrive off the UI thread);
raw callbacks are UNORDERED and bursty (one plug can fire
added + state + default within milliseconds), so events are
COALESCED — a 0.75 s quiet window collapses a burst into ONE
on_change call carrying the highest-priority kind
(default_changed > removed > state_changed > added), with the
role/device id of the primary event. Only capture-flow events
are reported; render-flow noise is dropped at the door.

start() returns True only when registration actually
succeeded (the worker reports back through an Event). Every
failure is silent-off-Windows by design: no watcher, and the
app behaves exactly as before — the silence guard remains the
backstop. COM work is field-proof-owed on a real Windows box,
like every Windows-only path in this codebase.
"""
from __future__ import annotations

import sys
import threading

COALESCE_SECONDS = 0.75

_KIND_PRIORITY = {"added": 0, "state_changed": 1,
                  "removed": 2, "default_changed": 3}

# EDataFlow (mmdevice) — only capture events steer a microphone.
_E_CAPTURE = 1


class DefaultDeviceWatcher:
    """on_change(kind, role, device_id) — called on a timer
    thread after a coalesced burst; kind is one of
    'default_changed' / 'removed' / 'state_changed' / 'added',
    role an ERole int (0 = console)."""

    def __init__(self, on_change, coalesce_seconds: float =
                 COALESCE_SECONDS):
        self._on_change = on_change
        self._coalesce = float(coalesce_seconds)
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._timer: threading.Timer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._registered = False
        self._enumerator = None
        self._callback_obj = None

    # -------------------------------------------------- events in
    def _note(self, kind: str, role: int = 0,
              device_id: str = "") -> None:
        """One raw event (any thread). Merges into the pending
        burst and (re)arms the coalesce timer — a burst fires
        on_change ONCE, 0.75 s after its LAST raw event."""
        with self._lock:
            if self._pending is None:
                self._pending = {"kind": kind, "role": role,
                                 "device_id": device_id,
                                 "kinds": {kind}}
            else:
                self._pending["kinds"].add(kind)
                if (_KIND_PRIORITY.get(kind, 0)
                        >= _KIND_PRIORITY.get(
                            self._pending["kind"], 0)):
                    self._pending.update(
                        {"kind": kind, "role": role,
                         "device_id": device_id})
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._coalesce,
                                          self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
            self._timer = None
        if pending is None:
            return
        try:
            self._on_change(pending["kind"], pending["role"],
                            pending["device_id"])
        except Exception:  # noqa: BLE001 — a watcher callback
            pass  # must never take the app down

    # -------------------------------------------------- lifecycle
    def start(self) -> bool:
        """Register for notifications on the worker thread;
        True only when the registration actually landed."""
        if not sys.platform.startswith("win"):
            return False
        if self._thread is not None:
            return self._registered
        self._thread = threading.Thread(target=self._run,
                                        name="vyuk-device-watch",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return bool(self._registered)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = None
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            self._run_com()
        except Exception:  # noqa: BLE001 — watcher is best-effort
            self._registered = False
        finally:
            self._ready.set()

    def _run_com(self) -> None:
        import ctypes

        from comtypes import COMObject

        from . import mmdevice

        ifaces = mmdevice._interfaces()
        if ifaces is None:
            return
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # MTA
        try:
            enumerator = mmdevice.create_enumerator()
            if enumerator is None:
                return
            watcher = self

            class _Callback(COMObject):
                _com_interfaces_ = [ifaces["IMMNotificationClient"]]

                def OnDefaultDeviceChanged(self, flow, role,
                                           pwstrDefaultDeviceId):
                    if int(flow) == _E_CAPTURE:
                        watcher._note("default_changed",
                                      int(role),
                                      str(pwstrDefaultDeviceId or ""))
                    return 0

                def OnDeviceRemoved(self, pwstrDeviceId):
                    watcher._note("removed", 0,
                                  str(pwstrDeviceId or ""))
                    return 0

                def OnDeviceAdded(self, pwstrDeviceId):
                    watcher._note("added", 0,
                                  str(pwstrDeviceId or ""))
                    return 0

                def OnDeviceStateChanged(self, pwstrDeviceId,
                                         dwNewState):
                    watcher._note("state_changed", 0,
                                  str(pwstrDeviceId or ""))
                    return 0

                def OnPropertyValueChanged(self, pwstrDeviceId,
                                           key):
                    return 0

            callback = _Callback()
            enumerator.RegisterEndpointNotificationCallback(
                callback)
            self._enumerator = enumerator
            self._callback_obj = callback
            self._registered = True
            self._ready.set()
            self._stop_event.wait()
            try:
                enumerator.UnregisterEndpointNotificationCallback(
                    callback)
            except Exception:  # noqa: BLE001
                pass
            self._registered = False
        finally:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
