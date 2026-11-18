"""State bus — re-export shim (GAPS-28, G-V2).

The bus lives in ``core/state_bus.py`` (Qt-optional by design, so
it belongs in core and core producers import it directly). This
file is a thin shim so the old import path keeps working. The GUI
reaches the bus ONLY through the facade (GAPS-15 boundary: a
direct ``from core.state_bus import ...`` here would fail
test_gui_boundary) — the facade re-exports the module, and the
names below are the identical objects the direct import would
give. Every other reader imports the core module directly.
"""

from __future__ import annotations

from core.gui_facade import state_bus as _bus_mod

StateBus = _bus_mod.StateBus
AppState = _bus_mod.AppState
SensorState = _bus_mod.SensorState
MissionState = _bus_mod.MissionState
StepEvent = _bus_mod.StepEvent
ToolStarted = _bus_mod.ToolStarted
ToolFinished = _bus_mod.ToolFinished
InputAcquired = _bus_mod.InputAcquired
InputReleased = _bus_mod.InputReleased
TargetRect = _bus_mod.TargetRect
FailsafeTripped = _bus_mod.FailsafeTripped
CaptureStarted = _bus_mod.CaptureStarted
CaptureFinished = _bus_mod.CaptureFinished
Verified = _bus_mod.Verified
Verifying = _bus_mod.Verifying
CursorPath = _bus_mod.CursorPath
AgentAction = _bus_mod.AgentAction
SettingsChanged = _bus_mod.SettingsChanged
EVENT_TYPES = _bus_mod.EVENT_TYPES
QT_AVAILABLE = _bus_mod.QT_AVAILABLE
get_state_bus = _bus_mod.get_state_bus
reset_state_bus = _bus_mod.reset_state_bus

__all__ = [
    "StateBus",
    "AppState",
    "SensorState",
    "MissionState",
    "StepEvent",
    "ToolStarted",
    "ToolFinished",
    "InputAcquired",
    "InputReleased",
    "TargetRect",
    "FailsafeTripped",
    "CaptureStarted",
    "CaptureFinished",
    "Verified",
    "Verifying",
    "CursorPath",
    "AgentAction",
    "SettingsChanged",
    "EVENT_TYPES",
    "QT_AVAILABLE",
    "get_state_bus",
    "reset_state_bus",
]
