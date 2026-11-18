"""
Application Entrypoint for Commercial Voice AI Assistant.
Clean-room implementation. VYUK's own code is proprietary
(see LICENSE / EULA.txt) — not MIT (docstring corrected
v5.50, programme item 44; third-party licences live in
THIRD_PARTY_LICENSES.md).
"""

import os
import sys
from pathlib import Path

# Ensure root package directory is in sys.path
# Ensure parent directory is in sys.path so voice_assistant package is resolvable
parent_dir = Path(__file__).resolve().parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))


def _set_process_dpi_awareness() -> None:
    """Process DPI awareness (v5.54, Wave 0 — UI-48(c), the
    source-side half of the DPI stack; the frozen exe's manifest
    half is Wave 10's). Asks Windows for PER_MONITOR_AWARE_V2
    BEFORE Qt is imported, let alone given a window to create:
    the process's awareness is fixed at first use, and a
    python-hosted process starts only system-aware, which is
    how her layout used to be bitmap-stretched (and her text
    blurred/clipped) on 125%/150% displays. Fallback chain for
    older Windows: shcore's per-monitor awareness, then the
    legacy system-aware call. A no-op off Windows (this module
    is imported by tooling on other OSes); never raises — a
    failed call leaves exactly the pre-v5.54 behaviour."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return
    except Exception:  # noqa: BLE001 — fall through to shcore
        pass
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE == 2 (Windows 8.1+).
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:  # noqa: BLE001 — fall through to legacy
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 — awareness stays as it was
        pass


_set_process_dpi_awareness()

# Presence (v2.1): the Windows auto-start entry (core/presence.py)
# launches VYUK with --minimized. Translate the flag into the env var
# the window reads BEFORE the gui app is imported/constructed.
if "--minimized" in sys.argv:
    os.environ["NOVA_START_MINIMIZED"] = "1"

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from voice_assistant.core import presence
# GAPS-28 (G-V8): the service layer owns the background services'
# lifecycle; main.py constructs it before the window and passes it in.
from voice_assistant.core.service_host import ServiceHost
from voice_assistant.gui.design_system.dialogs import HudDialog
from voice_assistant.gui.main_window import MainWindow
from voice_assistant.gui.style import load_bundled_fonts


def main():
    # Auto-update (v5.2): apply an update staged by a previous run
    # BEFORE anything else constructs. Guarded end to end — the
    # updater reports outcomes as state dicts and logs them to
    # data/activity.log itself; an exception here must never stop
    # VYUK from starting, so the whole call is wrapped and any
    # failure just prints and falls through to a normal launch.
    try:
        from voice_assistant.core.health import updater  # Phase 3 H1: moved to core/health/

        updater.apply_staged()
    except Exception as exc:  # noqa: BLE001 — startup survives the updater
        print(f"[updater] apply_staged failed, starting normally: {exc}")

    # Install-tree integrity (v5.47, programme item 10): verify the
    # program files on disk against the signed install manifest
    # (core/health/integrity.py) and log any problem loudly. It
    # used to run HERE, before the window existed — but it hashes
    # 1,208 files (0.74 s in the lab, more on a Windows disk), so
    # every launch paid it before first paint (Fix 6, 2026-11-05).
    # The check is report-only and never blocks startup, so it now
    # runs on a daemon thread AFTER window.show() below — the same
    # check, the same loud logging, just not on the launch path.
    # (The import stays here so the deferred thread below can
    # close over the bound name; a failed import leaves the
    # deferred thread to report and move on.)
    _integrity = None
    try:
        from voice_assistant.core.health import integrity as _integrity  # Phase 3 H1: moved to core/health/
    except Exception as exc:  # noqa: BLE001 — startup survives the check
        print(f"[integrity] check failed, starting normally: {exc}")

    # Milestone log (v5.54, Wave 0 — WF-55's recording
    # dependency): record the launch firsts (the install's
    # first-run date; the first launch of this version) into
    # data/milestones.jsonl. Recording starts now because a
    # milestone not written on the day can never be backfilled;
    # the resurfacing UI is Wave 9's. Exactly the posture above:
    # report, never block.
    try:
        from voice_assistant.core import milestones
        from voice_assistant.core.version import VERSION

        milestones.record_startup(VERSION)
    except Exception as exc:  # noqa: BLE001 — startup survives the log
        print(f"[milestones] startup record failed, starting normally: {exc}")

    # Windows-profile identity (Triage Wire Build 3 — BZ-7.6):
    # resolve whose OS profile the session runs as and report it.
    # Exactly the posture above — report, never block: on a
    # non-Windows machine, or when no profile resolves, the
    # identity is reported as unavailable, never guessed.
    try:
        from voice_assistant.core import triage3_identity_redact_select as _t3

        _ident = _t3.startup_identity()
        if _ident["status"] == "ok":
            _i = _ident["identity"]
            print(f"[identity] {_i['username']} ({_i['source']})")
        else:
            print(f"[identity] unavailable: {_ident['message']}")
    except Exception as exc:  # noqa: BLE001 — startup survives
        print(f"[identity] check failed, starting normally: {exc}")

    # Safe mode (v5.65, Wave 10 — PF-11): env VYUK_SAFE_MODE or the
    # data/safe_mode.flag file forces the minimal configuration —
    # local brain, builtin backend, no watchers (the window's
    # _init_watchers guard reads the same flag). The config
    # singleton already exists by now, so the wiring sets its
    # fields directly; env alone would not reach it. Guarded end
    # to end — a safe-mode failure must never stop the launch.
    try:
        from voice_assistant.core import config as _config_mod
        from voice_assistant.core import w10_wiring as _w10

        _safe = _w10.apply_safe_mode(_config_mod.config,
                                     data_dir=str(_config_mod.DATA_DIR))
        if _safe["active"]:
            print(f"[safe-mode] active via {_safe['sources']}: "
                  "local brain, builtin backend, watchers off")
    except Exception as exc:  # noqa: BLE001 — startup survives
        print(f"[safe-mode] check failed, starting normally: {exc}")

    # Capability probes (v5.54, Wave 0 — EY-H1's report-only
    # leg): a few seconds after startup, probe the bundled
    # capabilities for ACTUALLY working (the OCR legs here; the
    # VLM leg loads a multi-GB model and stays doctor-only) and
    # write the one-line verdict to the senses timeline.
    # Deferred on a daemon thread so the launch never pays for
    # it; strictly report-only — nothing here repairs, downloads
    # or reconfigures anything (the repair leg is Wave 10's).
    try:
        import threading as _threading

        def _deferred_capability_probes():
            try:
                import time as _time

                _time.sleep(12)
                from voice_assistant.core.health import capability_probes  # Phase 3 H1: moved to core/health/
                from voice_assistant.core import senses_log

                _results = capability_probes.run_probes(
                    include_vlm=False)
                _line = capability_probes.summary_line(_results)
                print(f"[probes] {_line}")
                senses_log.append_event("capability_probes", _line)
            except Exception:  # noqa: BLE001 — probes never
                pass  # disturb the session

        _threading.Thread(target=_deferred_capability_probes,
                          name="vyuk-capability-probes",
                          daemon=True).start()
    except Exception as exc:  # noqa: BLE001 — startup survives
        print(f"[probes] could not schedule the capability "
              f"probes, starting normally: {exc}")

    # High DPI screen support. The rounding policy is pinned
    # DELIBERATELY (v5.54, Wave 0 — wave 8 UI-49): PassThrough,
    # never Round — Round is a DIAGNOSTIC for isolating a
    # scaling defect on a misbehaving machine, not a shipped
    # setting. The layout side of the DPI stack (font-metric
    # fitting, one coordinate system, per-window DPR) is pinned
    # by the v5.54 scaling rig (test_v554_dpi_rig).
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("VYUK AI Assistant")
    app.setOrganizationName("NovaAI")

    # Bundled Inter (v3.0): register the packaged faces, then make Inter
    # the application font (10 pt ≈ the stylesheet's 13 px body) so
    # text drawn outside the QSS — menus, dialogs, tooltips — matches
    # the window. Silent no-op if the font files are missing.
    load_bundled_fonts()
    app.setFont(QFont("Inter", 10))

    # Single instance (v2.1): a second copy would fight the first over
    # the microphone and the local-brain port — point at the running
    # one and exit quietly instead. The lock object must stay
    # referenced for the life of the process, hence storing it on app.
    instance_lock = presence.acquire_single_instance()
    if instance_lock is None:
        HudDialog.information(
            None,
            "VYUK",
            "VYUK is already running — check your tray/mini orb.",
        )
        return
    app.nova_instance_lock = instance_lock

    window = MainWindow(service_host=ServiceHost())
    window.show()

    # Deferred startup checks (Fix 6, 2026-11-05 — see the integrity
    # note above): the install-tree hash (0.74 s in the lab) runs
    # here, on a daemon thread AFTER first paint, so the launch
    # never waits for it; the activity log is rotated in the same
    # pass (cap 1 MB, keep the newest 2,000 lines — core/perf.py).
    # Both are report-only and never break the launch.
    try:
        import threading as _threading

        def _deferred_startup_checks():
            if _integrity is not None:
                try:
                    _integrity.check_and_log()
                except Exception as exc:  # noqa: BLE001 — never breaks launch
                    print(f"[integrity] check failed: {exc}")
            try:
                from voice_assistant.core import perf as _perf

                _perf.rotate_activity_log()
            except Exception:  # noqa: BLE001 — rotation never breaks launch
                pass

        _threading.Thread(target=_deferred_startup_checks,
                          name="vyuk-startup-checks",
                          daemon=True).start()
    except Exception:  # noqa: BLE001 — startup survives
        pass

    # Agent overlay (Command Deck Phase F, libwiring sweep): until
    # this call the overlay existed only in tests — no product code
    # instantiated it. Creating it here attaches it to the state
    # bus for real runs. Guarded: an overlay failure must never
    # break the launch, and killing it on exit never stops an
    # action (guide S9).
    try:
        from voice_assistant.gui.agent_overlay import (
            create_and_attach_overlay as _attach_overlay)
        _overlay, _overlay_status = _attach_overlay()
        window.agent_overlay = _overlay
        try:
            _app = QApplication.instance()
            if _app is not None and _overlay is not None:
                _app.aboutToQuit.connect(_overlay.kill)
        except Exception:  # noqa: BLE001 — teardown hook is best-effort
            pass
    except Exception:  # noqa: BLE001 — overlay never breaks launch
        pass

    # Telemetry (GAPS-27, M-V8): the enable path for the dev-only
    # local-file telemetry. OFF by default behind explicit opt-in
    # (the "telemetry/enabled" setting, default False) — this call
    # only ever turns it ON when the user flipped the setting, and
    # apply_settings() itself returns False (stays off) when the
    # OpenTelemetry SDK is absent. Guarded: a telemetry failure
    # must never break the launch.
    try:
        from voice_assistant.core import telemetry as _telemetry

        _telemetry.apply_settings(settings=window.settings)
    except Exception:  # noqa: BLE001 — startup survives telemetry
        pass

    # Changelog (v5.65, Wave 10 — PF-6): at first launch after an
    # update, show exactly the entries newer than the last seen
    # version, then mark them seen (displayed = delivered; the full
    # list stays retrievable in Settings → "What's new"). Fresh
    # installs skip this — onboarding marks the baseline instead —
    # so a new user is never spammed with the backlog. Guarded:
    # a changelog failure must never break the launch.
    try:
        def _maybe_show_changelog():
            try:
                from voice_assistant.core import w10_wiring as _w10
                from voice_assistant.core.config_spec import (
                    qsettings_get,
                    qsettings_set,
                )
                from voice_assistant.core.version import VERSION as _VER
                from voice_assistant.gui.changelog_widget import (
                    ChangelogWidget as _ChangelogWidget)
                _onboarded = bool(qsettings_get(
                    "onboarded", settings=window.settings))
                _last = qsettings_get(
                    "changelog/last_seen_version", settings=window.settings)
                if isinstance(_last, str) and not _last.strip():
                    _last = None
                _act = _w10.changelog_action(_onboarded, _last, _VER)
                if _act["action"] != "show" or _ChangelogWidget is None:
                    return
                from PySide6.QtWidgets import QDialog, QVBoxLayout
                _dlg = QDialog(window)
                _dlg.setWindowTitle("What's new in VYUK")
                _lay = QVBoxLayout(_dlg)
                _lay.addWidget(_ChangelogWidget(_act["entries"], _dlg))
                _dlg.exec()
                qsettings_set("changelog/last_seen_version", _VER,
                              settings=window.settings)
            except Exception:  # noqa: BLE001 — changelog never breaks launch
                pass

        QTimer.singleShot(2000, _maybe_show_changelog)
    except Exception:  # noqa: BLE001 — startup survives
        pass

    # v5.25: installs that onboarded before the three-option brain
    # choice existed never saw it — the old onboarded flag hid the
    # whole flow. Offer the choice once, shortly after the window
    # settles; fresh installs get the full onboarding instead (the
    # window schedules that itself), and anyone can reopen the
    # choice from Settings → "How VYUK thinks".
    try:
        if window._brain_choice_prompt_needed():
            QTimer.singleShot(900, window._run_brain_choice_prompt)
    except Exception:  # noqa: BLE001 — the offer must never break launch
        pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
