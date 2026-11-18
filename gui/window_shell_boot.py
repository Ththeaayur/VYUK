"""Shell boot sequence (GAPS-28, G-V7) — moved verbatim from
gui/window_shell_frame.py.

The 1,018-line shell frame held the boot sequence inline; the
sequence is one coherent group (create ONE BootSequence, tick one
stage per timer fire, skip on Escape), so it lives here as
``ShellBootMixin`` and ``ShellFrameMixin`` inherits it. Move-only:
no method body changed, no call site changed. The wiring test
(tests/test_boot_sequence_wiring.py) reads this file for the
method sources and window_shell_frame.py for ``__init__``.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut


class ShellBootMixin:
    """The boot-sequence group of ShellFrameMixin (see module docstring)."""

    # ------------------------------------------------- boot sequence
    # B-R1-4w: the ONE call site for the adewaskar boot
    # sequence (core/desktop/boot_sequence.py, B-R1-4).
    #
    # LOPHOLE FAMILY — "the wiring lies, or the wiring blocks":
    # a boot that starts but never ticks (created and then
    # forgotten — the user never sees it); a boot that blocks
    # startup (the driver waits for the sequence before showing
    # the window — a slow check holds the whole product
    # hostage); a boot that cannot be skipped (no way past it
    # when it wedges); a boot that replaces the onboarding (the
    # Terms gate is bypassed — a compliance hole); a boot that
    # double-runs (the sequence starts twice — two boots, two
    # greetings).
    #
    # ONE design closes the class: ONE call site. The driver
    # creates ONE BootSequence at startup (_start_boot_sequence
    # is idempotent — a second call returns the existing
    # sequence, never a second one) and ticks it from a timer
    # (one stage per _tick_boot_sequence call, never a blocking
    # wait — the window is already shown and the event loop
    # runs between ticks, so the first reply is never held for
    # the sequence). The sequence is skippable (Escape, wired
    # below, ends it cleanly and records it). The onboarding
    # runs exactly as before (the boot never reads or writes
    # the onboarded setting and never calls _run_onboarding).
    #
    # RENDERING (R7-2 — the later render batch has landed):
    # each verdict line goes to the senses timeline + print —
    # the capability-probes posture (main.py:184), report-only,
    # never a block — and the full report is kept on
    # self.boot_report for the driver to read. The boot is NOT
    # rendered through _on_status: a boot status would falsely
    # clear the session-liveness probe (window_session_status
    # .py: "any status proves the worker loop is alive" — the
    # boot is not the worker). The visual boot is gui/boot_screen
    # .py (BootScreen, shown on start, updated per tick, hidden
    # on finish/skip) reading self.boot_report; Home is untouched
    # — the overlay is transparent for mouse input and dismisses
    # itself, so the boot stays a ritual, never a gate.
    #
    # TICK BOUND (honest): one tick runs one stage check. Nine
    # stages are cheap status reads; the voice stage's first
    # probe can take up to 5 s (VOICE_PROBE_TIMEOUT_S in the
    # module, cached per interpreter after) — one long tick,
    # never a wait for the whole sequence.
    #
    # SKIP (Escape): a QShortcut on this shell (WidgetShortcut,
    # the default) — it fires only while the shell has focus.
    # While the onboarding modal is up, focus is in the modal
    # and Escape belongs to the modal; once it is dismissed,
    # Escape skips the boot. The shortcut is disabled the
    # moment the boot completes or is skipped, so Escape never
    # keeps firing afterwards.
    #
    # Rejected alternatives (recorded):
    # * Creating the sequence and never ticking it (a dead boot).
    # * A blocking wait for the sequence (a slow check holds
    #   startup hostage).
    # * An unskippable boot (a wedge).
    # * Replacing the onboarding with the boot (a compliance hole).
    # * Allowing a second start (two boots, two greetings).
    # * Rendering through _on_status (a boot status would
    #   falsely clear the session-liveness probe).
    _BOOT_TICK_MS = 250

    def _start_boot_sequence(self):
        """Create the ONE boot sequence and start its timer.
        Idempotent: a second call returns the existing
        sequence, never a second one. Returns the sequence,
        or None when it could not be created (startup survives
        — the boot is a ritual, never a gate). The timer ticks
        one stage per fire; nothing here waits for the
        sequence, so the first reply is never held for it."""
        existing = getattr(self, "_boot_sequence", None)
        if existing is not None:
            return existing
        try:
            from voice_assistant.core.gui_facade import (
                boot_sequence as _boot_mod)
            sequence = _boot_mod.BootSequence()
        except Exception as exc:  # noqa: BLE001 — startup survives
            print(f"[boot] could not start: {exc}")
            return None
        self._boot_sequence = sequence
        self.boot_report = sequence.report()
        try:
            timer = QTimer(self)
            timer.setInterval(self._BOOT_TICK_MS)
            timer.timeout.connect(self._tick_boot_sequence)
            self._boot_timer = timer
            timer.start()
        except Exception as exc:  # noqa: BLE001 — startup survives
            print(f"[boot] could not start the tick timer: {exc}")
            self._boot_timer = None
        try:
            shortcut = QShortcut(QKeySequence("Escape"), self)
            shortcut.activated.connect(self._skip_boot_sequence)
            self._boot_skip_shortcut = shortcut
        except Exception:  # noqa: BLE001 — skip is a nicety
            self._boot_skip_shortcut = None
        # R7-2: show the visual boot (gui/boot_screen.py) over the
        # window — it reads self.boot_report, never the status
        # channel (a boot status would falsely clear the
        # session-liveness probe). Guarded: a partial shell (the
        # wiring test's fake shell binds only the five wiring
        # methods) must still boot.
        try:
            self._show_boot_screen()
        except Exception:  # noqa: BLE001 — the boot never breaks startup
            pass
        return sequence

    def _boot_screen_widget(self):
        """Lazily create the R7-2 boot overlay (a child of this
        window, transparent for mouse input). Returns the widget
        or None when it could not be created (startup survives —
        the boot is a ritual, never a gate)."""
        screen = getattr(self, "_boot_screen", None)
        if screen is not None:
            return screen
        try:
            from voice_assistant.gui import boot_screen as _bs
            screen = _bs.BootScreen(self)
            try:
                highlight, mid, edge = self.visualizer._colors()
                screen.set_palette(highlight, mid, edge)
            except Exception:  # noqa: BLE001 — palette is a nicety
                pass
            self._boot_screen = screen
        except Exception as exc:  # noqa: BLE001 — startup survives
            print(f"[boot] no boot screen: {exc}")
            return None
        return screen

    def _show_boot_screen(self):
        """Show the boot overlay with the current report. Never
        raises."""
        try:
            screen = self._boot_screen_widget()
            if screen is not None:
                screen.show_report(getattr(self, "boot_report", None))
        except Exception:  # noqa: BLE001 — the boot never breaks startup
            pass

    def _update_boot_screen(self):
        """Repaint the boot overlay with the latest report (called
        once per tick). Never raises."""
        try:
            screen = getattr(self, "_boot_screen", None)
            if screen is not None and screen.isVisible():
                screen.show_report(getattr(self, "boot_report", None))
        except Exception:  # noqa: BLE001
            pass

    def _hide_boot_screen(self):
        """Dismiss the boot overlay (completion or skip). Never
        raises."""
        try:
            screen = getattr(self, "_boot_screen", None)
            if screen is not None:
                screen.hide_screen()
        except Exception:  # noqa: BLE001
            pass

    def _tick_boot_sequence(self):
        """Run ONE boot stage and record its verdict. Stops
        the timer and the skip shortcut the moment the
        sequence completes; a tick failure stops the timer
        cleanly instead of wedging the loop. Never raises."""
        sequence = getattr(self, "_boot_sequence", None)
        if sequence is None:
            return
        try:
            verdict = sequence.advance()
        except Exception as exc:  # noqa: BLE001 — never wedge
            print(f"[boot] tick failed, stopping the boot: {exc}")
            verdict = None
            self._stop_boot_timer()
            return
        if verdict is None:
            self._finish_boot_sequence()
            return
        line = (f"[{verdict['id']}] {verdict['verdict']}"
                + (f" — {verdict['reason']}"
                   if verdict.get("reason") else ""))
        print(f"[boot] {line}")
        try:
            from voice_assistant.core.gui_facade import senses_log as _senses
            _senses.append_event("boot", line)
        except Exception:  # noqa: BLE001 — logging never breaks boot
            pass
        self.boot_report = sequence.report()
        if sequence.complete:
            self._finish_boot_sequence()
        else:
            # R7-2: repaint the visual boot with the latest report
            # (guarded — a partial shell must still tick).
            try:
                self._update_boot_screen()
            except Exception:  # noqa: BLE001
                pass

    def _skip_boot_sequence(self):
        """End the boot cleanly (the Escape user action): every
        remaining stage is marked skipped, the report records
        that the boot was skipped, and the greeting names only
        what was checked before the skip. Returns the report,
        or None when no boot exists. Never raises."""
        sequence = getattr(self, "_boot_sequence", None)
        if sequence is None:
            return None
        try:
            report = sequence.skip()
        except Exception as exc:  # noqa: BLE001 — skip must not raise
            print(f"[boot] skip failed: {exc}")
            report = sequence.report()
        self._stop_boot_timer()
        self.boot_report = report
        # R7-2: dismiss the visual boot (the skip is clean — the
        # report records that the boot was skipped). Guarded: a
        # partial shell must still skip.
        try:
            self._hide_boot_screen()
        except Exception:  # noqa: BLE001
            pass
        print(f"[boot] skipped — {report.get('greeting', '')}")
        try:
            from voice_assistant.core.gui_facade import senses_log as _senses
            _senses.append_event(
                "boot", f"skipped — {report.get('greeting', '')}")
        except Exception:  # noqa: BLE001 — logging never breaks boot
            pass
        return report

    def _finish_boot_sequence(self):
        """The boot is complete: stop the timer and the skip
        shortcut, keep the final report, and log the greeting
        (the reactor beat — it names only what was actually
        checked). Never raises."""
        self._stop_boot_timer()
        sequence = getattr(self, "_boot_sequence", None)
        if sequence is None:
            return
        self.boot_report = sequence.report()
        # R7-2: dismiss the visual boot on completion (guarded — a
        # partial shell must still finish).
        try:
            self._hide_boot_screen()
        except Exception:  # noqa: BLE001
            pass
        greeting = self.boot_report.get("greeting", "")
        print(f"[boot] {greeting}")
        try:
            from voice_assistant.core.gui_facade import senses_log as _senses
            _senses.append_event("boot", greeting)
        except Exception:  # noqa: BLE001 — logging never breaks boot
            pass

    def _stop_boot_timer(self):
        """Stop the tick timer and disable the skip shortcut.
        Best-effort, never raises — called on completion,
        on skip, and on a tick failure."""
        timer = getattr(self, "_boot_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
        shortcut = getattr(self, "_boot_skip_shortcut", None)
        if shortcut is not None:
            try:
                shortcut.setEnabled(False)
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass

