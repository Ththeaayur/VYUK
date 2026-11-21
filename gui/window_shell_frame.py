# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from .window_widgets import (
    _ASSETS,
    BridgeSignals,
    BackendWorker,
    AppShell,
    TitleBar,
    WaveformWidget,
    MiniEq,
    ThinkingDots,
    _ActivitySparkline,
    _ComposerEdit,
    ChatBubble,
    CommandPalette,
    SplashIgnition,
)
from .style import (
    ACCENT,
    AMBER,
    BG,
    DANGER,
    LINE_SUBTLE,
    PREMIUM_STYLE,
    SURFACE_2,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    TYPE_CARD_TITLE,
    TYPE_TITLE,
    RADIUS_BTN,
    load_bundled_fonts,
)
from .sounds import Earcons
from .mini_orb import MiniOrb
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)
from voice_assistant.core.gui_facade import qsettings_get, qsettings_store
from .breakpoints import (
    WINDOW_MINIMUM,
    chat_target,
    default_window_size,
    hero_min_width_for,
    stage_floor_for,
)
from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import config
from .icons import make_icon
import threading
from .design_system.palette import parse_color, with_alpha
from .window_shell_boot import ShellBootMixin  # GAPS-28 (G-V7): boot group

# E-F103 (2026-11-20): screen-share hides below this panel
# width (was 380) — at the 400 px default panel the button took
# 54 px the composer input needs: the full placeholder is 195 px
# in Sora 13 px and the input only shows ~106 px with the button
# present, so the mic instruction never read. The action stays
# reachable from the header overflow menu (same handler, same
# menu entry — see _show_header_overflow_menu).
_DOCK_NARROW_WIDTH = 420
# UI rethink Build 1: below this panel width Dictate collapses to
# icon-only (its label is what squeezed the input to "cta" on the
# old 300 px panel). The width is a panel width, not a window width.
_DICTATE_TEXT_WIDTH = 420
# E-F103 (2026-11-20): below this panel width Dictate hides
# entirely — at the 300 px panel floor even its 48 px icon-only
# form leaves the input at its 120 px minimum and the shortened
# placeholder (112 px) still wraps. Dictation stays reachable
# via Ctrl+D (the tooltip says so); the mic button, the primary
# voice control, stays visible.
_DICTATE_HIDE_WIDTH = 380


class ShellFrameMixin(ShellBootMixin):
    """The shell frame. The boot-sequence group lives in
    gui/window_shell_boot.py (ShellBootMixin, GAPS-28 G-V7) and
    is inherited here — same methods, same MRO position for
    every caller."""

    def __init__(self, service_host=None):
        super().__init__()
        # GAPS-28 (G-V8): the background services' lifecycle owner.
        # main.py passes the process-wide host; a window built
        # without one (tests, old call sites) builds its own
        # lazily in _start_background_services.
        self.service_host = service_host
        self.setWindowTitle(config.assistant_name)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # UI breakpoints slice (2026-10-30): the S5 floors are now
        # the COMFORT tier, not the hard minimum. Comfort derivation
        # (unchanged): sidebar 220 + assistant-page margins 56
        # (28 + 28) + stage 420 + splitter handle 12 + chat 300 =
        # 1008. Narrow derivation: the stage floor relaxes to 320
        # (hero minimum with it, 400 -> 320 — the orb visualizer's
        # own floor) so 220 + 56 + 320 + 12 + 300 = 908, which fits
        # the 200%-on-1080p logical width (960) and, with the
        # 500 px height floor, both measured compact work areas
        # (1366x768@125% ~1092x576, 200%@1080p ~960x516). Sidebar,
        # chat floor, composer floors, and palette cap/clamp are
        # unchanged at every tier; nothing collapses, no drawer
        # (the drawer decision, delegated on measurements, is NO
        # DRAWER — the tiered floors + scroll-safe pages solve
        # every measured failure). The default is clamped to the
        # available geometry, so 1920x1080@125% still opens at
        # exactly 1108x750 (1108 - 220 - 56 - 12 = 820 = stage 420
        # + chat 400) while compact displays get a fitting
        # default; Qt clamps any below-minimum default up to the
        # (908, 500) minimum, which still fits those work areas.
        self.setMinimumSize(*WINDOW_MINIMUM)
        _avail = None
        try:
            _screen = QGuiApplication.primaryScreen()
            if _screen is not None:
                _avail = _screen.availableGeometry()
        except Exception:  # noqa: BLE001 — no screen, use default
            _avail = None
        if _avail is not None:
            self.resize(*default_window_size(_avail.width(),
                                             _avail.height()))
        else:
            self.resize(1108, 750)
        # v3.0: register the bundled Inter faces before the stylesheet
        # (whose stack names Inter first) is applied. Idempotent; the
        # app entry point does this too — whichever runs first wins.
        load_bundled_fonts()
        self.setStyleSheet(PREMIUM_STYLE)

        self.settings = qsettings_store()
        # SENSES v5.16: the saved appearance (theme / text size /
        # density) takes over from the static default sheet.
        self._apply_appearance()

        self.signals = BridgeSignals()
        self.signals.status_changed.connect(self._on_status)
        self.signals.transcript_received.connect(self._on_transcript)
        self.signals.transcript_chunk_received.connect(
            self._on_transcript_chunk)
        self.signals.tool_executed.connect(self._on_tool)
        self.signals.scheduler_trigger.connect(self._on_scheduler_trigger)
        self.signals.clipboard_content.connect(self._on_clipboard_content)
        self.signals.wake_word_triggered.connect(self._on_wake_word_activated)
        self.signals.diagnosis_ready.connect(self._show_diagnosis)
        self.signals.brain_check_completed.connect(self._set_brain_status)
        self.signals.brain_setup_progress.connect(self._set_brain_status)
        self.signals.brain_setup_completed.connect(self._on_local_setup_completed)
        # v3.2: the setup banner rides the same download emissions.
        self.signals.brain_setup_progress.connect(
            lambda *_: self._refresh_setup_banner())
        self.signals.brain_setup_completed.connect(
            lambda *_: self._refresh_setup_banner())
        self.signals.routine_due.connect(self._on_routine_due)
        self.signals.routine_result.connect(self._on_routine_result)
        self.signals.watcher_event.connect(self._on_watcher_event)
        # v5.48 (programme items 19b/21): capture indicator + plugin
        # trust results land on the GUI thread through the bridge,
        # like every other worker-thread surface above.
        self.signals.capture_indicator_changed.connect(
            self._render_capture_indicator)
        # v5.49 (programme item 35): suggestion cards + (items
        # 29/32) headless plan progress for the goal pill.
        self.signals.suggestion_received.connect(
            self._on_suggestion_received)
        # v5.57.1 (item 7): suggestion Act outcomes land here.
        self.signals.suggestion_act_completed.connect(
            self._on_suggestion_act_completed)
        self.signals.plan_progress_line.connect(self._update_goal_pill)
        self.signals.plugin_trust_notice.connect(self._add_system)
        # Chatfix H1: a failed typed send lands as a system line.
        self.signals.typed_send_failed.connect(self._on_typed_send_failed)
        self.signals.plugin_trust_pinned.connect(
            self._on_plugin_trust_pinned)
        self.signals.world_status.connect(self._on_world_status)
        self.signals.browser_enable_completed.connect(
            self._on_browser_enable_completed)
        self.signals.key_test_completed.connect(self._on_key_test_completed)
        self.signals.groq_key_test_completed.connect(self._on_groq_key_test_completed)
        self.signals.update_check_completed.connect(self._on_update_check_completed)
        self.signals.update_stage_completed.connect(self._on_update_stage_completed)

        self.worker: BackendWorker | None = None
        # v5.42 (Truth, E1): this window owns the brain session, so
        # it registers the frame sink the screenshot tools use to
        # actually show the model a frame (see _deliver_tool_frame
        # beside _send_screen_capture); cleared again in closeEvent.
        try:
            from voice_assistant.core.gui_facade import set_frame_sink  # Phase 4 S1b INTENTIONAL: handlers split to funnel

            set_frame_sink(self._deliver_tool_frame)
        except Exception:  # noqa: BLE001 — no sink registered only
            pass  # means tools honestly report no viewer; never fatal
        # R8-6 (yasb port): the same seam for the status strip — this
        # window owns the strip, so it registers the sink the
        # status_strip tool calls (see _status_strip_sink in
        # window_memory_presence.py); cleared again in closeEvent.
        try:
            from voice_assistant.core.gui_facade import set_status_strip_sink

            set_status_strip_sink(self._status_strip_sink)
        except Exception:  # noqa: BLE001 — no sink registered only
            pass  # means the tool honestly reports no strip; never fatal
        # v5.48 (programme item 21): the capture indicator — while
        # any capture is live the pill beside the watch pill says
        # so (see _on_capture_indicator / _render_capture_indicator).
        try:
            from voice_assistant.core.gui_facade import capture_guard

            capture_guard.set_indicator_listener(
                self._on_capture_indicator)
        except Exception:  # noqa: BLE001 — indicator is a display
            pass  # extra; its absence never blocks a capture
        # v5.53 (F2): the play_music continuity recheck announces
        # a cut-off stream into chat. Proactive core lines ride
        # the watcher-event seam (chat + senses timeline + the
        # notices gates), the same road watcher notices take.
        try:
            from voice_assistant.core.gui_facade import music_player

            music_player.set_announce_hook(
                lambda message: self.signals.watcher_event.emit(
                    "music", message))
        except Exception:  # noqa: BLE001 — the announcement is
            pass  # an extra; its absence never blocks playback
        # v5.53 (F6): the microphone silence escalation speaks
        # in chat — a default mic that opens but captures only
        # silence is announced (with the Windows privacy
        # check), as is the fall-through to the next device.
        # Same watcher-event seam as the music lines.
        try:
            from voice_assistant.core.gui_facade import audio as _audio_core

            _audio_core.set_announce_hook(
                lambda message: self.signals.watcher_event.emit(
                    "microphone", message))
        except Exception:  # noqa: BLE001 — the announcement is
            pass  # an extra; its absence never blocks audio
        # v5.48 (programme item 19b): the plugin trust prompt's
        # NON-BLOCKING adapter, registered BEFORE _build_ui runs
        # plugin discovery for the Skills page, so an untrusted
        # plugin found there is queued for the deferred ask instead
        # of stalling startup on a prompt (see
        # _plugin_trust_prompt_adapter).
        self._plugin_trust_queue: list = []
        self._plugin_trust_lock = threading.Lock()
        self._plugin_trust_thread = None
        self._plugin_trust_asking = False
        try:
            from voice_assistant.core.gui_facade import plugin_loader

            plugin_loader.set_trust_prompt_handler(
                self._plugin_trust_prompt_adapter)
        except Exception:  # noqa: BLE001 — no adapter registered
            pass  # means unlisted plugins refuse, the safe default
        self._plugins: list[dict] = []
        # v5.49 (programme item 35): the live suggestion cards,
        # keyed by suggestion id — {card, act, dismiss, status}
        # per card, so Act/Dismiss resolve the card in place.
        self._suggestion_cards: dict = {}
        # v5.49 (programme item 35): the suggestion listener —
        # registered ONCE at window start; deliveries arrive on
        # watcher threads, so the listener only marshals the
        # record over the bridge signal. The Settings suggestion
        # controls are applied to core.brain.suggestions in the same
        # breath (its configure_suggestions accessors).
        try:
            from voice_assistant.core.gui_facade import suggestions as _sug_mod  # Phase 3 B1b: moved to core/brain/

            _sug_mod.register_suggestion_listener(
                lambda record: self.signals.suggestion_received.emit(
                    record))
        except Exception:  # noqa: BLE001 — no listener means
            pass  # suggestions still record/act headlessly
        self._apply_suggestion_settings()
        self._transcript_history: list[dict] = []
        self._connected = False
        self._status_raw = ""
        self._status_caption = ""
        # Deaf-session watch (v5.37): while a session is live a
        # 2 s timer polls audio_manager.mic_status(); a stream
        # that stays "silent" for 20 s puts a plain-words hint on
        # the caption label (once per episode) and restores the
        # caption when the mic recovers or the session ends.
        self._deaf_since: float | None = None
        self._deaf_hint_active = False
        self._deaf_hint_text = ""
        self._deaf_saved_caption: str | None = None
        self._tick = 0
        self._last_error_sound = 0.0
        self._last_nova_bubble: ChatBubble | None = None
        self._page_anim = None
        # Premium Wave 2 motion state: the running page transition
        # (a QParallelAnimationGroup), its slide helper + target page,
        # a watchdog that snaps any interrupted transition to its
        # final state, the bulk-load flag that keeps history restore
        # instant, and the startup splash (shown in __init__ below).
        self._page_slide = None
        self._page_anim_page = None
        self._page_watchdog = QTimer(self)
        self._page_watchdog.setSingleShot(True)
        self._page_watchdog.timeout.connect(self._snap_page_transition)
        self._chat_bulk_loading = False
        self._splash: SplashIgnition | None = None
        # Phase 3 state (initialised before _build_ui uses any of it)
        self._palette: CommandPalette | None = None
        self._mic_shadow: QGraphicsDropShadowEffect | None = None
        self._chat_has_messages = False
        self._suggest_row: QWidget | None = None
        self._thinking_row = None
        self._thinking_bubble: QFrame | None = None
        self._thinking_dots: ThinkingDots | None = None
        self._thinking_frame = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(120)
        self._thinking_timer.timeout.connect(self._advance_thinking_dots)
        self._fade_started = False
        self._fade_anim = None
        # Presence pack (v2.1) state — real objects are built in
        # _init_presence() at the end of __init__; None-safe everywhere.
        self.mini_orb: MiniOrb | None = None
        self._routine_scheduler = None
        self._briefing_fired_date: str | None = None
        # Watchers pack (v2.3) state — engine built in _init_watchers();
        # None-safe everywhere.
        self._watcher_engine = None
        self._last_watcher_spoken = 0.0
        # EYES E6 (v5.45): watch-my-screen state — the engine
        # (core/desktop/screen_watch.ScreenWatcher) is built lazily by
        # _apply_watch_state; the mode itself is GUI-owned state
        # on core/desktop/screen_watch's switchboard (OFF by default,
        # session-scoped, auto-off at session end). The 1 s timer
        # drives tick(); the engine throttles polls to its own
        # 2–5 s cadence and rate-limits brain-bound frames.
        self._screen_watcher = None
        self._screen_watch_timer = QTimer(self)
        self._screen_watch_timer.setInterval(1000)
        self._screen_watch_timer.timeout.connect(self._on_screen_watch_tick)
        try:
            from voice_assistant.core.gui_facade import screen_watch as _sw  # Phase 3 D3: moved to core/desktop/

            _sw.subscribe_watch(self._on_watch_control_changed)
        except Exception:  # noqa: BLE001 — watch mode is an extra
            pass
        # NOSE N1 (v5.44): watcher notices held back while the
        # user is in a call — (kind, message) pairs in arrival
        # order, delivered as ONE digest when the call clears.
        self._call_held_notices: list = []
        # Feels-alive pack (v2.5) state — auto-reconnect bookkeeping,
        # the calendar heads-up seen-set (lazy-loaded from
        # data/heads_up_seen.json), and the clear-history two-tap arm.
        self._was_connected = False
        self._intentional_disconnect = False
        self._reconnect_attempts = 0
        self._reconnect_pending = False
        # Fix 4 (2026-11-05): voice-drop record + terminal
        # give-up latch + cross-episode budget (see
        # gui/window_session_status.py and gui/drop_cause.py).
        self._last_drop = None
        self._reconnect_exhausted = False
        self._reconnect_episodes_failed = 0
        self._last_episode_end_at = None
        self._go_away_seen = False
        self._mic_stall_noted = False
        self._last_status_fatal = False
        # R2 (2026-11-06): session-liveness watch state (see
        # gui/window_session_status.py): the outstanding probe
        # (sent_at, None until the first probe), its last ack,
        # and whether a silent death was already declared for
        # this fall. The timer itself is created in
        # _setup_timers (window_services_trust.py).
        self._liveness_probe_sent_at = None
        self._liveness_last_ack = None
        self._silent_death_declared = False
        self._liveness_awaiting_first_status = False
        self._heads_up_seen: set | None = None
        self._history_clear_armed = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)
        # She-learns-you pack (v2.6) state — the self-sent echo guard
        # (texts the window itself injects via send_text come back
        # through _on_transcript as "user"; they are NOT utterances
        # and must never be learned from) and the forget-everything
        # two-tap arm for the Memory page.
        self._self_sent_texts: list = []
        self._learned_clear_armed = False

        self.earcons = Earcons(
            self,
            volume=float(qsettings_get("sounds_volume", settings=self.settings)),
            enabled=bool(qsettings_get("sounds_enabled", settings=self.settings)),
        )

        # Premium Wave 2: splash ignition — shown now, while the UI
        # builds behind it; Phase G (S3): it dismisses when the
        # real boot checks resolve (target 1.2 s, hard cap 2.0 s)
        # or on any click/keypress, and it never blocks
        # construction. Skipped entirely if the app has no GUI
        # focus to give it (defensive try/except).
        try:
            from .firstrun_g import run_boot_checks as _boot_checks

            try:
                _checks = _boot_checks(
                    settings_get=lambda key: self.settings.value(key),
                    config_obj=config)
            except Exception:  # noqa: BLE001 — splash without checks
                _checks = None
            self._splash = SplashIgnition(
                reduced_motion=self._motion_reduced())
            self._splash.show_ignition(checks=_checks)
        except Exception:  # noqa: BLE001
            self._splash = None

        self._build_ui()
        # Feels-alive pack (v2.5): yesterday's conversation is back on
        # screen before anything new can arrive.
        self._restore_chat_history()
        # Phase 3: Ctrl+K opens the command palette from anywhere.
        try:
            self._palette_shortcut = QShortcut(
                QKeySequence("Ctrl+K"), self)
            self._palette_shortcut.activated.connect(
                self._show_command_palette)
        except Exception:  # noqa: BLE001
            self._palette_shortcut = None
        # v5.57 (redo, A9.2): "?" opens the shortcuts sheet — via
        # an application event filter, not a QShortcut, so typing
        # "?" in the composer or any text box still types it (the
        # filter only fires when focus is not a text input; see
        # ChatMixin.eventFilter).
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.installEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        self._setup_tray()
        self._setup_timers()
        self._start_background_services()
        self._apply_saved_settings()
        self._init_presence()
        # v3.2: first-run provisioning starts at launch, not at first
        # Connect — and the setup banner explains it while it lands.
        self._maybe_auto_provision()
        self._refresh_setup_banner()

        geo = qsettings_get("geometry", settings=self.settings)
        if geo is not None:
            self.restoreGeometry(geo)

        try:
            audio_manager.start()
        except Exception as e:  # noqa: BLE001
            print(f"[Audio Hardware Error]: {e}")

        # v5.53 (F1): first paint tells the truth about the ear —
        # the wake word ships off, so the idle line opens as the
        # not-listening notice instead of a Ready line that
        # invites talking at a switched-off wake path. Any later
        # status (auto-connect, Connect) overwrites it through
        # _on_status as usual.
        try:
            self._apply_idle_listening_text()
        except Exception:  # noqa: BLE001 — never break startup
            pass

        if not bool(qsettings_get("onboarded", settings=self.settings)):
            QTimer.singleShot(700, self._run_onboarding)

        # v5.48 (programme item 19b): the deferred plugin-trust ask
        # runs once the window is up and the event loop is turning
        # — never during construction/discovery (see
        # _run_deferred_plugin_trust_asks).
        QTimer.singleShot(1200, self._run_deferred_plugin_trust_asks)

        # B16: the startup recovery check re-reads the
        # ledger's interrupted records after launch and
        # refreshes the Activity offer state — never a
        # dialog, never a navigation, never an auto-run (a
        # restart is not consent to re-run a half-done
        # goal). The offer lives on Activity until the user
        # takes it.
        QTimer.singleShot(1500, self._run_startup_recovery_check)

        # B-R1-4w: the adewaskar boot sequence starts here — one
        # singleShot after first paint, alongside (never instead
        # of) the onboarding / plugin-trust / recovery timers
        # above. The sequence itself is core/desktop/boot_sequence
        # (B-R1-4, parent-verified): a state machine the driver
        # ticks, one stage per tick, never a blocking wait.
        # _start_boot_sequence is idempotent — a second start
        # returns the existing sequence, never a second boot.
        # The onboarding above is untouched: it still fires at
        # 700 ms exactly when the onboarded setting is false,
        # and the boot never reads or writes that setting.
        QTimer.singleShot(100, self._start_boot_sequence)

        # v5.54 (Wave 0, DPI stack rule ⑦): when the window
        # crosses to a screen with a different scale factor
        # (WM_DPICHANGED / QScreen change), re-polish + re-layout
        # so QSS and font-metric caches refresh at the new DPR
        # instead of stranding the old one. Deferred: the
        # QWindow handle exists only once the window is native.
        QTimer.singleShot(0, self._hook_screen_changed)


    def _hook_screen_changed(self):
        try:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
        except Exception:  # noqa: BLE001 — a display nicety
            pass  # must never break startup


    def _on_screen_changed(self, screen=None):
        try:
            dpr = screen.devicePixelRatio() if screen is not None \
                else self.devicePixelRatio()
            print(f"[dpi] window moved to a screen at "
                  f"{dpr:.2f}x — re-polishing layout")
        except Exception:  # noqa: BLE001
            pass
        try:
            style = self.style()
            style.unpolish(self)
            style.polish(self)
            self.updateGeometry()
            self.update()
        except Exception:  # noqa: BLE001 — never break the window
            pass

    # ------------------------------------------------------------ chrome
    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            # Phase D (D13): restore the 6 px arc radius, not the
            # old 24 px — the painted silhouette is chamfer + 6.
            self.shell.radius = 6
        else:
            self.showMaximized()
            self.shell.radius = 0
        self.shell.update()


    def resizeEvent(self, event):
        # v5.57 (redo): capture the panel width BEFORE super()
        # activates the layout — super()'s layout pass already
        # redistributes the splitter (trace: the store read after
        # super() saw the redistributed 341, not the pre-resize
        # 400), so a post-super() store preserves the wrong
        # width. First store in a burst wins; the guard consumes
        # it, so a burst restores the width from before the
        # burst, not a mid-burst redistribution.
        try:
            if getattr(self, "_chat_splitter", None) is not None \
                    and getattr(self, "_chat_width_before_resize",
                                 None) is None:
                self._chat_width_before_resize = \
                    self.chat_panel.width()
        except Exception:  # noqa: BLE001 — the pin is a nicety
            pass
        super().resizeEvent(event)
        if hasattr(self, "_grip"):
            self._grip.move(self.width() - 18, self.height() - 18)
            self._grip.raise_()
        # Sci-fi Home: the collapsed briefing summary is elided to
        # one line against the label's width — re-elide after a
        # resize, since the available width changed.
        try:
            _reelide = getattr(self, "_reelide_briefing_summary", None)
            if _reelide is not None:
                _reelide()
        except Exception:  # noqa: BLE001 — cosmetic
            pass
        try:
            QTimer.singleShot(0, self._fix_chat_width)
        except Exception:  # noqa: BLE001 — the pin is a nicety
            pass


    def _apply_dock_narrow_rule(self) -> None:
        """UIS5 (composer narrow rule) + UI rethink Build 1: the dock
        fits the panel at every width, so the composer never clips
        again. Four rules, all driven by the panel width:
        - Screen-share hides below _DOCK_NARROW_WIDTH (420), so the
          composer input keeps a usable width. The action stays
          reachable from the header overflow menu.
        - Dictate collapses to icon-only below _DICTATE_TEXT_WIDTH
          (420): its label is what squeezed the input to "cta" on
          the old 300 px panel. The icon, tooltip and accessible
          name stay, so the action is unchanged — only its width.
        - E-F103: Dictate hides entirely below _DICTATE_HIDE_WIDTH
          (380) — at the 300 px panel floor even the 48 px
          icon-only form leaves the input at its 120 px minimum
          and the shortened placeholder still wraps. Dictation
          stays reachable via Ctrl+D; the mic button stays visible.
        - The dock waveform only takes width when it fits: the
          telemetry tick reads _dock_waveform_allowed (see
          _sync_audio_telemetry) and never shows the ribbon while
          it would crush the input. The threshold is computed from
          the live widths, not hardcoded — input min 120 + send +
          dictate + waveform 104 + mic + screen (when shown) + gaps.
        No-op before the dock exists. Called from _fix_chat_width
        (resizes + first show) and from the splitter-moved handler
        (drags).
        """
        try:
            panel = getattr(self, "chat_panel", None)
            if panel is None:
                return
            panel_w = panel.width()
            # Screen-share hides below _DOCK_NARROW_WIDTH (420).
            screen = getattr(self, "btn_screen", None)
            screen_visible = panel_w >= _DOCK_NARROW_WIDTH
            if screen is not None:
                screen.setVisible(screen_visible)
            # Dictate collapses to icon-only below _DICTATE_TEXT_WIDTH
            # (420). Set the text first — the width measurement below
            # reads the button's sizeHint in its new mode.
            dictate = getattr(self, "_btn_dictate", None)
            if dictate is not None:
                dictate.setText(
                    "Dictate" if panel_w >= _DICTATE_TEXT_WIDTH else "")
                # E-F103: Dictate hides entirely below
                # _DICTATE_HIDE_WIDTH (380) — see the constant above.
                dictate.setVisible(panel_w >= _DICTATE_HIDE_WIDTH)
            # The waveform's fit, from live widths (never isVisible:
            # before first show every widget reads invisible, so the
            # visibility is computed from the panel width instead):
            # input min 120 + send + dictate + mic + screen (when
            # shown) + waveform 104 + one 12 px gap between each
            # pair of visible children.
            fixed = 0
            visible_fixed = 0
            for name in ("btn_send", "_btn_dictate", "btn_mic"):
                w = getattr(self, name, None)
                if w is not None:
                    fixed += w.sizeHint().width()
                    visible_fixed += 1
            if screen is not None and screen_visible:
                fixed += screen.sizeHint().width()
                visible_fixed += 1
            waveform = getattr(self, "waveform", None)
            wave_w = (waveform.sizeHint().width()
                      if waveform is not None else 0)
            gap_count = visible_fixed + 1  # input + waveform add a gap
            self._dock_waveform_allowed = (
                120 + fixed + wave_w + gap_count * 12 <= panel_w)
            if not self._dock_waveform_allowed and waveform is not None:
                waveform.setVisible(False)
        except Exception:  # noqa: BLE001 — the rule is a nicety
            pass


    def _apply_breakpoint_tier(self) -> None:
        """UI breakpoints slice: apply the current width tier's
        floors to the stage and hero. Comfort (>= 1088 px) keeps
        the S5 floors (stage 420, hero 400); narrow (< 1088 px)
        relaxes both (stage 320, hero 320) so the splitter can
        honour the chat's 380 floor without squeezing the stage
        below its tier floor or the hero below the orb's floor.
        No-op before the assistant page exists, and never touches
        the sidebar (fixed 220 at every tier — no collapse, no
        drawer). Called from _fix_chat_width, which runs on every
        resize and at first show, so tier changes track the
        window width without a separate resize hook.
        """
        try:
            stage = getattr(self, "_stage_widget", None)
            if stage is None:
                return
            width = self.width()
            stage.setMinimumWidth(stage_floor_for(width))
            # Golden-fix D1: the splitter child is the stage scroll
            # area, so it carries the same tier floor — otherwise
            # the splitter could squeeze the scroller below the
            # stage floor and the inner stage would scroll
            # horizontally instead of holding its width.
            scroll = getattr(self, "_stage_scroll", None)
            if scroll is not None:
                scroll.setMinimumWidth(stage_floor_for(width))
            hero = getattr(self, "hero", None)
            if hero is not None:
                hero.setMinimumWidth(hero_min_width_for(width))
        except Exception:  # noqa: BLE001 — floors are a nicety
            pass


    def _fix_chat_width(self, first: bool = False):
        """v5.57 (redo, A9.3): the chat opens at the frozen 400 px
        and keeps its width across window resizes — the width from
        before the resize when one was stored, else a dragged
        width, else the current width (floors still enforced);
        the stage never squeezes below its tier floor, the
        chat never below 300 px. UI breakpoints slice: the stage
        floor is tiered (420 comfort, 320 narrow) via
        chat_target(); a drag down through the 1008 boundary
        keeps the chat width while the stage shrinks continuously
        (no cliff). No-op until the splitter is laid out.
        Programmatic moves are guarded so they never
        mark the width user-set."""
        split = getattr(self, "_chat_splitter", None)
        if split is None or split.width() < 100:
            return
        self._apply_breakpoint_tier()
        avail = split.width() - split.handleWidth()
        if first:
            target = 400
        else:
            stored = getattr(self, "_chat_width_before_resize", None)
            if stored is not None:
                self._chat_width_before_resize = None
                target = stored
            elif getattr(self, "_chat_width_user_set", False):
                target = getattr(self, "_chat_width_user",
                                 self.chat_panel.width())
            else:
                target = self.chat_panel.width()
        target = chat_target(avail, target, self.width())
        # v5.57 fix (battery: test_v530_batch4 widths): ALWAYS
        # setSizes, even when the target already equals the panel
        # width. The guard often fires before the layout applies
        # the new splitter geometry, so an early-return no-op
        # leaves the panel unanchored and the layout then
        # re-proportions it (408 -> 348 on the 1240 -> 1100
        # resize); an explicit setSizes anchors the panel, and
        # the layout takes the shortfall from the stage instead.
        self._fixing_chat_width = True
        try:
            split.setSizes([avail - target, target])
        finally:
            self._fixing_chat_width = False
        # UIS5: the width just changed — re-apply the composer
        # narrow rule (resizes + first show flow through here).
        self._apply_dock_narrow_rule()
        # UI rethink Build 1 (render fix): the briefing summary
        # elides against the label's width — but at resizeEvent
        # time the label still holds its pre-resize width (207 px
        # on the 1280 -> 1024 resize; the final 143 px only lands
        # once this width pass settles), so a synchronous re-elide
        # keeps the full text and the bar clips mid-word. Defer
        # one more pass to after the layout settles.
        try:
            _reelide = getattr(
                self, "_reelide_briefing_summary", None)
            if _reelide is not None:
                QTimer.singleShot(0, _reelide)
        except Exception:  # noqa: BLE001 — cosmetic
            pass


    def _build_titlebar(self) -> TitleBar:
        bar = TitleBar()
        bar.on_toggle_maximize = self._toggle_maximize
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(8)
        mark = QLabel("V")
        mark.setObjectName("brandMark")
        mark.setFixedSize(20, 20)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Phase D: title-bar chrome follows the 2 px shape rule.
        mark.setStyleSheet("font-size: 11px; border-radius: 2px;")
        name = QLabel(config.assistant_name)
        name.setObjectName("titleBarName")
        layout.addWidget(mark)
        layout.addWidget(name)
        layout.addStretch()

        self.btn_cmd_pill = QPushButton("Search or command\u2026   Ctrl+K")
        self.btn_cmd_pill.setObjectName("cmdPill")
        try:
            self.btn_cmd_pill.setIcon(make_icon("search", TEXT_MUTED, 14))
            self.btn_cmd_pill.setIconSize(QSize(14, 14))
        except Exception:  # noqa: BLE001
            pass
        self.btn_cmd_pill.setToolTip("Open the command palette (Ctrl+K)")
        self.btn_cmd_pill.clicked.connect(self._show_command_palette)
        layout.addWidget(self.btn_cmd_pill)
        layout.addSpacing(6)

        btn_min = QPushButton()
        btn_min.setObjectName("winBtn")
        btn_min.setAccessibleName("Minimize window")
        btn_min.setFixedSize(36, 26)
        btn_min.setIcon(make_icon("minus", TEXT_MUTED, 13))
        btn_min.clicked.connect(self.showMinimized)
        btn_max = QPushButton()
        btn_max.setObjectName("winBtn")
        btn_max.setAccessibleName("Maximize window")
        btn_max.setFixedSize(36, 26)
        btn_max.setIcon(make_icon("square", TEXT_MUTED, 12))
        btn_max.clicked.connect(self._toggle_maximize)
        btn_close = QPushButton()
        btn_close.setObjectName("winBtn")
        btn_close.setAccessibleName("Close window")
        btn_close.setFixedSize(36, 26)
        btn_close.setIcon(make_icon("x", TEXT_MUTED, 13))
        btn_close.setStyleSheet(f"QPushButton:hover {{ background-color: {with_alpha(DANGER, 160)}; }}")
        btn_close.clicked.connect(self.close)
        # Stored for the main-window Tab chain (see
        # _apply_main_tab_order) — they are locals otherwise.
        self.btn_win_min = btn_min
        self.btn_win_max = btn_max
        self.btn_win_close = btn_close
        layout.addWidget(btn_min)
        layout.addWidget(btn_max)
        layout.addWidget(btn_close)
        return bar
