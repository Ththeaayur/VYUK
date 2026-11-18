"""DV doctor: dev telemetry posture, REPORTED never edited.

The Developer shelf is the telemetry-worst in the whole programme:
three headline default-ON cases (the repo CLI, the embedded
platform tool, the agent CLI) plus the editor itself. This module
is the read-only side of the posture section:

- VS Code telemetryLevel: read from the user's settings file and
  REPORTED. Never silently changed.
- Repo CLI telemetry state: read from environment + config file.
- Embedded-platform telemetry setting: read back (the proof step
  for the forced-off spawn environment).
- Agent analytics persisted state: read from the analytics file
  (a one-time human "yes" at a prompt would otherwise phone home
  forever, because the opt-in persists).
- Dictation-front-end audit checklist: the outbound-traffic
  capture a pilot must produce before any task runs (the audit
  IS the pilot gate for the young voice front-ends).

CONSTRUCTION GUARANTEE: every function here opens files read-only.
There is no setter, no writer, and no function that takes a
settings path for writing. Doctor REPORTS but never edits another
product's settings — enforced by the absence of any write path,
which the suite proves by AST scan.

All paths are injectable (home dir / config dir parameters) so
tests run against temp dirs, never the real home.
"""

from __future__ import annotations

import json
import os


class DoctorError(ValueError):
    """Raised when a posture read is impossible to state honestly."""


def _read_json_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _read_text_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def vscode_telemetry_level(settings_path):
    """Report the editor's telemetryLevel. Returns the level string
    (e.g. "all"/"error"/"crash"/"off"), or "unknown" when the file
    or the key is absent. Never writes, never changes."""
    data = _read_json_file(settings_path)
    if not isinstance(data, dict):
        return "unknown"
    level = data.get("telemetry.telemetryLevel")
    if level is None:
        # Vendor default is the permissive level; say so, do not
        # claim the user chose it.
        return "unknown (vendor default is permissive)"
    return str(level)


def gh_telemetry_state(env, config_path=None):
    """Report repo-CLI telemetry posture from env + config file.

    Forced-off in a spawn environment (GH_TELEMETRY=false) is the
    compliant state; anything else is reported as-is, never fixed
    here (the fix is the spawn construction in dv_drive_v577).
    """
    env = env or {}
    state = {"gh_telemetry_env": env.get("GH_TELEMETRY"),
             "do_not_track": env.get("DO_NOT_TRACK"),
             "config_disabled": None}
    text = _read_text_file(config_path)
    if text is not None and "telemetry" in text.lower():
        state["config_disabled"] = "disabled" in text.lower()
    forced = (env.get("GH_TELEMETRY") == "false"
              or env.get("DO_NOT_TRACK") == "true"
              or state["config_disabled"] is True)
    state["forced_off"] = bool(forced)
    return state


def platformio_telemetry_state(env, settings_output=None):
    """Report embedded-platform telemetry from env + settings proof.

    ``settings_output`` is the text of the `settings get` proof step.
    The forced-off spawn environment is the compliant state; a
    default-on that can reset on update is reported, not assumed.
    """
    env = env or {}
    forced = env.get("PLATFORMIO_SETTING_ENABLE_TELEMETRY") == "No"
    proof = None
    if settings_output is not None:
        lowered = str(settings_output).lower()
        if "enable_telemetry" in lowered:
            proof = ("no" in lowered or "false" in lowered)
    return {"env_forced_off": bool(forced), "settings_proof_off": proof}


def aider_analytics_state(analytics_path):
    """Report the agent's persisted analytics state.

    The opt-in persists in a file once granted, so a stored "yes"
    is live until the file changes. Returns the file's content
    verdict, or "absent" (no persisted opt-in found).
    """
    data = _read_json_file(analytics_path)
    if data is None:
        return {"persisted": False, "state": "absent"}
    return {"persisted": True, "state": data}


def cline_telemetry_note():
    """Cline posture note: the vendor blog claim of "always
    opt-in" is stale against the vendor's own terms text (on by
    default). The pilot must capture first-run setting state +
    outbound hosts before any task runs. This function returns
    that capture checklist; it performs no capture itself."""
    return ["pinned build recorded",
            "first-run setting state captured",
            "outbound hosts captured in harness",
            "telemetry off before any task runs"]


def voice_frontend_audit_checklist(tool):
    """Outbound-traffic audit checklist for dictation front-ends.

    For the young voice front-ends the traffic audit IS the pilot
    gate, not a courtesy: self-declared zero telemetry is never
    accepted without captured evidence. Returns the required
    evidence list for the named tool."""
    base = ["outbound hosts captured during a pilot session",
            "no audio/text egress observed (or egress named)",
            "crash/update check endpoints named, if any"]
    if tool == "voxpilot":
        base.append("ffmpeg-on-Windows wart engineered away or disclosed")
    return base


def posture_report(home_dir, env=None):
    """Aggregate posture report (read-only) for the doctor surface.

    ``home_dir`` is the user's home (injectable for tests). Paths
    that do not exist report "unknown"/"absent" — never an invented
    state, never a silent fix.
    """
    env = env or dict(os.environ)
    vscode_path = os.path.join(
        home_dir, "AppData", "Roaming", "Code", "User", "settings.json")
    aider_path = os.path.join(home_dir, ".aider", "analytics.json")
    return {
        "vscode_telemetryLevel": vscode_telemetry_level(vscode_path),
        "gh": gh_telemetry_state(env),
        "platformio": platformio_telemetry_state(env),
        "aider_analytics": aider_analytics_state(aider_path),
        "cline_capture_checklist": cline_telemetry_note(),
    }
