"""Flow logic for the first-run onboarding dialog (GUI split #13 batch 2).

Stage logic, navigation, persistence, and closeEvent, moved verbatim
from gui/onboarding.py. Composed into OnboardingDialog by
gui/onboarding.py as FlowMixin. No behaviour change.
"""

from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import gemini_oauth
from voice_assistant.core.gui_facade import qsettings_set_noted
from voice_assistant.core.gui_facade import set_preferred_language

from .firstrun_g import FIRST_RUN_STAGES
from .style import ACCENT, AMBER, OK, TEXT_MUTED, TYPE_CAPTION


class FlowMixin:
    """Stage/nav/persist flow for OnboardingDialog (GUI split #13)."""

    def _cloud_auth_ready(self) -> bool:
        """True when the cloud brain has an auth method: a stored
        Google sign-in (the round-3 OAuth build's primary path)
        or a legacy Gemini key (kept working — the Settings key
        field and the Connect dialog still save one). The Done
        page and the sign-in page's advance gate both read this,
        so the two surfaces can never disagree about whether the
        cloud brain is ready."""
        if gemini_oauth.is_signed_in():
            return True
        return bool(str(getattr(config, "api_key", "") or "").strip())

    def _done_brain_line(self) -> str:
        """The done page names the brain the user actually has:
        the cloud lines claim the cloud brain only when an auth
        method is stored. Without one they say so plainly and
        name Settings as the way back (round-2 N4 FAIL 2)."""
        choice = self.brain_choice or ""
        if choice in ("cloud", "both") and not self._cloud_auth_ready():
            return self._BRAIN_LINES_NO_SIGNIN[choice]
        return self._BRAIN_LINES.get(choice, "")

    def _advance_from_signin_page(self) -> bool:
        """Continue from the sign-in page (round-2 N4 FAIL 2, kept
        shape, new auth). A stored sign-in — or a legacy key from
        an earlier run — advances at once. With neither, the first
        press warns once and stays; the second, deliberate press
        advances (the user may finish setup and sign in later,
        in Settings)."""
        if self._cloud_auth_ready():
            return True
        status = getattr(self, "ob_signin_status", None)
        if not getattr(self, "_signin_no_signin_warned", False):
            self._signin_no_signin_warned = True
            if status is not None:
                status.setText(
                    "You haven't signed in yet — you picked the cloud "
                    "brain, so she'll think on this PC until you sign "
                    "in with Google in Settings. Press Continue again "
                    "to go on without signing in, or press Continue "
                    "with Google above.")
                status.setStyleSheet(f"color: {AMBER};")
            return False
        self._signin_no_signin_warned = False
        # Loophole batch 7 (#18): going on without signing in is
        # an explicit decline of the Google piece — recorded
        # durably, so the Home card stops naming it (the card
        # only ever returns for the pieces still open).
        try:
            from voice_assistant.core.gui_facade import (
                setup_pieces as _sp)

            _sp.record_declines({"google"})
        except Exception:  # noqa: BLE001 — the advance stands
            pass
        return True

    def _persist_profile_choice(self):
        """v5.49 (programme item 34 completion): Finish makes the
        profile choice durable — TOOL_PROFILE in .env through
        save_env_setting (the same one-field pattern Settings
        uses), plus the in-process config field so this very run
        agrees. Skipping the dialog never reaches this method:
        no pick, no record — the shipped desktop default stands."""
        profile = self.profile_choice
        if profile not in ("assistant", "desktop", "full"):
            return
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting("TOOL_PROFILE", profile)
        except Exception:  # noqa: BLE001
            pass
        config.tool_profile = profile

    def _persist_theme_choice(self):
        """Round-3 wizard expansion: Finish makes the theme pick
        durable — the same "theme" QSettings key Settings writes
        (window_settings_appearance._on_theme_selected), so the
        wizard's pick and the Settings card set the same config.
        The main window re-applies the appearance after the
        dialog closes (see _run_onboarding). Skipping never
        reaches this method: no pick, no record."""
        name = getattr(self, "theme_choice", "Cyan") or "Cyan"
        try:
            from voice_assistant.core.gui_facade import (
                migrate_theme_name as _mig_theme)

            name = _mig_theme(str(name))
        except Exception:  # noqa: BLE001 — the raw name stands
            pass
        try:
            qsettings_set_noted("theme", name)
        except Exception:  # noqa: BLE001 — noted, never swallowed
            pass

    def _persist_name_choice(self):
        """Round-3 wizard expansion: Finish makes the name pick
        durable — USER_NAME in .env through save_env_setting (the
        same one-field pattern the brain/profile picks use), plus
        the in-process config field so this very run agrees. An
        empty name records nothing: she never learned it, and
        the greeting reads the plain form. Skipping never reaches
        this method."""
        name = str(getattr(self, "_pending_name", "") or "").strip()
        if not name:
            return
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting("USER_NAME", name)
        except Exception:  # noqa: BLE001
            pass
        config.user_name = name

    def _persist_home_city_choice(self):
        """UI Phase 1 (H-11): Finish makes the home-city pick
        durable — the one canonical store (ConfigSpec home_city +
        QSettings home/city + .env VYUK_HOME_CITY), plus the
        in-process config field so this very run agrees. An empty
        city records nothing: the Home weather shows the honest
        'set your city' prompt. Skipping never reaches this method."""
        city = str(getattr(self, "_pending_home_city", "") or "").strip()
        if not city:
            return
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting("VYUK_HOME_CITY", city)
        except Exception:  # noqa: BLE001
            pass
        try:
            from voice_assistant.core.gui_facade import qsettings_set

            qsettings_set("home/city", city)
        except Exception:  # noqa: BLE001
            pass
        try:
            config.home_city = city
        except Exception:  # noqa: BLE001
            pass
    def _stage_of_page(self, widget) -> str | None:
        """The required stage a page belongs to, or None for
        advanced-detour pages (which carry no stage)."""
        for stage, attrs in self._STAGE_PAGES.items():
            for attr in attrs:
                page = getattr(self, attr, None)
                if page is not None and widget is page:
                    return stage
        return None
    def _first_page_of_stage(self, stage: str):
        for attr in self._STAGE_PAGES.get(stage, ()):
            page = getattr(self, attr, None)
            if page is not None and self._page_applies(page):
                return page
        return None
    def _page_applies(self, widget) -> bool:
        """Whether `widget` is part of the flow for the pending
        choice: the sign-in page only for cloud/both, the Setting
        up page only for local/both (it exists only when the pack
        is incomplete). Round-3 wizard expansion: the voice,
        language and profile pages are main flow now — the
        Advanced detour is empty — so no page is detour-gated
        any more. Everything else always applies."""
        if widget is self._signin_page:
            return self.brain_choice in ("cloud", "both")
        if self._setup_page is not None and widget is self._setup_page:
            return self.brain_choice in ("local", "both")
        if getattr(self, "choice_only", False) and widget in (
                self._welcome_page, self._mic_page, self._name_page,
                self._voice_page, self._language_page,
                self._profile_page, self._google_page,
                self._theme_page,
                self._browser_page, self._camera_page, self._screen_page,
                self._files_page, self._home_devices_page,
                self._permissions_page,
                self._selftest_page, self._first_try_page):
            # v5.25: the choice-only flow skips these. v5.49
            # (programme item 34 completion): the profile page
            # joins the skipped set — the brain-choice dialog is
            # about the brain, not the tool set. Phase G adds the
            # permissions + self-test pages to the same set.
            # First-run onboarding (2026-11-16): the ask pages
            # are permissions-stage pages, so they skip with it.
            # Round-3 wizard expansion: the new first-run pages
            # (name, theme, Google services, home devices, first
            # try) join the same set — the choice-only dialog is
            # still about the brain only.
            return False
        return True
    def _applicable_pages(self) -> list:
        return [w for w in self._page_order if self._page_applies(w)]
    def _toggle_advanced(self) -> None:
        """Round-3 wizard expansion: the Advanced detour is empty
        — every deferred page (voice, language, profile) moved
        into the main flow — and its link is hidden, so this
        handler is unreachable from the UI. It stays (with the
        button object) so no caller breaks; with _ADVANCED_PAGES
        empty, opening it sets the flag and finds no page."""
        if self._detour:
            self._detour = False
            self.btn_advanced.setText("Advanced setup")
            if self._return_page is not None:
                self.pages.setCurrentWidget(self._return_page)
            self._return_page = None
        else:
            self._detour = True
            self._return_page = self.pages.currentWidget()
            self.btn_advanced.setText("Back to setup")
            for attr in self._ADVANCED_PAGES:
                page = getattr(self, attr, None)
                if page is not None and self._page_applies(page):
                    self.pages.setCurrentWidget(page)
                    break
        self._sync_nav()
    def _maybe_start_downloads(self):
        """v5.23: the ONLY place onboarding starts the pack
        download — as the user leaves the choice page having picked
        a brain that lives on this PC (local/both) with the pack
        still incomplete. Nothing downloads before the choice
        exists: not at construction, not on Skip, never for the
        cloud choice. Thread-safety: the manager's callbacks fire
        on its download thread, so both are deliberately no-ops —
        the Setting up page already polls pack_progress() from its
        GUI timers and paints the real numbers itself."""
        if self._downloads_kicked:
            return
        if self.brain_choice not in ("local", "both"):
            return
        if self._setup_page is None:
            return  # pack complete — nothing to download
        self._downloads_kicked = True
        try:
            from voice_assistant.core.gui_facade import downloads

            downloads.start(lambda line: None, lambda lines: None)
        except Exception:  # noqa: BLE001 — the page's polling shows the truth either way
            pass
    def _persist_brain_choice(self):
        """v5.23: Finish makes the choice durable — the two
        QSettings keys the main window's launch gate reads, plus
        BRAIN_ENGINE in .env (and in-process, so this very run
        agrees). Skipping the dialog never reaches this method:
        no choice, no record, no download."""
        choice = self.brain_choice
        if choice is None:
            return
        # Round-3 item 14: the launch gate reads these two keys — a
        # write that never persists asks the user again at the next
        # launch (or re-downloads the local pack). Each write is
        # noted on failure (log line + feed marker), never swallowed.
        qsettings_set_noted("setup/brain_choice", choice)
        qsettings_set_noted("setup/local_pack_wanted",
                            choice in ("local", "both"))
        engine = "local" if choice == "local" else "gemini"
        try:
            from voice_assistant.core.gui_facade import save_env_setting

            save_env_setting("BRAIN_ENGINE", engine)
        except Exception:  # noqa: BLE001
            pass
        config.brain_engine = engine
    def _persist_sound_choice(self):
        """D12: the permissions stage's sound opt-in becomes the
        stored sounds_enabled value at Finish. Skip never reaches
        this method — a skipped first run keeps the safe default
        written at dialog open (silent until opted in)."""
        checkbox = getattr(self, "_sound_checkbox", None)
        if checkbox is None:
            return
        # Round-3 item 14: the user's explicit sound opt-in — a
        # write that never persists starts the next launch silent
        # despite the choice. Noted on failure, never swallowed.
        qsettings_set_noted("sounds_enabled", bool(checkbox.isChecked()))
    def _sync_nav(self):
        current = self.pages.currentWidget()
        # E-F302 option D: the switch note lives in the chrome and
        # shows only on the two card pages it belongs to — the
        # choice and profile pages. (getattr: _sync_nav also runs
        # from paths that predate the label.)
        _note = getattr(self, "_switch_note", None)
        if _note is not None:
            _note.setVisible(current is self._choice_page
                             or current is self._profile_page)
        order = self._page_order
        idx = order.index(current)
        has_prev = any(self._page_applies(w) for w in order[:idx])
        self.btn_back.setVisible(has_prev)
        on_done = current is self._done_page
        if current is self._signin_page:
            # Re-entering the sign-in page re-arms the no-sign-in
            # warning: the warning belongs to this visit, never to
            # a past one (round-2 N4 FAIL 2). This cannot clobber
            # a just-shown warning — the warning path returns from
            # _next before _sync_nav runs. The page itself is
            # refreshed too, so a sign-in made on this visit (or
            # taken back) shows the moment the user comes back.
            self._signin_no_signin_warned = False
            self._refresh_signin_page()
        if current is self._google_page:
            # The Google-services page re-reads its connection
            # state on show, like the sign-in page above.
            self._refresh_google_page()
        # Round-3 wizard expansion: the Next label follows the
        # session language (Finish on the Done page, Continue
        # everywhere else).
        self.btn_next.setText(self._wizard_chrome(
            "finish" if on_done else "continue"))
        # The one hard gate in the flow: the choice page needs a
        # choice before Continue does anything (Skip stays live).
        self.btn_next.setEnabled(
            current is not self._choice_page
            or self.brain_choice is not None)
        # Phase G rail: current stage in accent, done stages in
        # the ok token, the rest muted. Choice-only mode shows
        # the brain stage alone.
        stage = self._stage_of_page(current)
        if getattr(self, "choice_only", False):
            stages = ["brain"]
        else:
            stages = list(FIRST_RUN_STAGES)
        for name in stages:
            lbl = self._rail_labels.get(name)
            if lbl is None:
                continue
            if name == stage:
                lbl.setStyleSheet(
                    f"color: {ACCENT}; font-size: {TYPE_CAPTION}px;"
                    " font-weight: 600; background: transparent;"
                    " border: none;")
            elif self._progress.state(name) in ("done", "skipped"):
                lbl.setStyleSheet(
                    f"color: {OK}; font-size: {TYPE_CAPTION}px;"
                    " background: transparent; border: none;")
            else:
                lbl.setStyleSheet(
                    f"color: {TEXT_MUTED}; font-size: {TYPE_CAPTION}px;"
                    " background: transparent; border: none;")
        if stage is not None and not self.choice_only:
            # Leaving a stage behind marks it done; the stage
            # being entered stays open until it is left.
            for name in FIRST_RUN_STAGES:
                if name == stage:
                    break
                if self._progress.state(name) == "open":
                    self._progress.mark_done(name)
        if on_done:
            self.done_brain_line.setText(self._done_brain_line())
            # Round-3 wizard expansion: the Done page greets the
            # user by name in the language they picked, and names
            # the first try they actually did (or the default
            # mission when they skipped it).
            try:
                _hello = self._wizard_chrome("hello")
                _name = str(getattr(self, "_pending_name", "") or "").strip()
                _greet = (f"{_hello}, {_name}" if _name else _hello)
                self.done_greeting_line.setText(_greet)
            except Exception:  # noqa: BLE001 — the greeting is a nicety
                pass
            try:
                _try = str(getattr(self, "_first_try_line", "") or "").strip()
                if _try:
                    self.done_mission_line.setText(
                        f"First mission done: {_try}")
            except Exception:  # noqa: BLE001 — the default line stands
                pass
            # Conversion Wave 9: refresh the proof run from live
            # evidence (mic peak so far) when the done page shows.
            _proof = getattr(self, "_w9_proof_widget", None)
            if _proof is not None:
                try:
                    from .w9_wiring_proof import refresh_first_run_proof
                    refresh_first_run_proof(
                        _proof, {"mic_peak": getattr(
                            self, "_w9_mic_peak", 0)})
                except Exception:  # noqa: BLE001
                    pass
        if current is self._mic_page:
            # Build 6: showing the mic page re-reads it, so a mic
            # grant taken back on the permissions page is visible
            # here the moment the user comes back — the same
            # read-only refresh the permissions page gets below.
            self._refresh_mic_page()
            if self._audition_btn.isChecked():
                self._mic_timer.start(50)
        else:
            self._mic_timer.stop()
            if getattr(self, "_audition_btn", None) is not None:
                if self._audition_btn.isChecked():
                    self._audition_btn.setChecked(False)
                    self._audition_btn.setText("AUDITION")
        if current is self._selftest_page:
            # Self-test auto-run (decision #12): the page runs on
            # its own showEvent too; the page's in-flight guard
            # makes this second trigger a no-op when the showEvent
            # run already started.
            self._selftest_page.run_selftest()
        if current is self._permissions_page:
            # Round-2 N4 FAIL 3: the permissions page's mic row
            # is built once at construction, so without this it
            # kept its build-time state while the walk moved on —
            # a grant made on the Microphone page still read as
            # off. Refresh it on show: the row's docstring
            # already claims it shows the LIVE state, and the
            # refresh only re-reads (never writes, never pops
            # the ask, never flips anything). Build 6 widens the
            # hook from the mic row to the whole status list.
            self._refresh_permissions_page()
        if (self._setup_page is not None
                and current is self._setup_page):
            self._refresh_setup_page()
            self._setup_timer.start(500)
            self._feature_timer.start()
        else:
            self._setup_timer.stop()
            self._feature_timer.stop()
    def _next(self):
        current = self.pages.currentWidget()
        if current is self._done_page:
            for rb in self.ob_voice_buttons:
                if rb.isChecked():
                    config.voice_name = rb.text()
            if not self.choice_only:
                # v5.41: the language step's pick becomes her home
                # (preferred_language + its curated voice, persisted
                # by the one write path in core/languages). The
                # choice-only flow never visits the page, so it must
                # not apply the page's default-checked radio either.
                for rb in getattr(self, "ob_language_buttons", []):
                    if rb.isChecked():
                        try:
                            set_preferred_language(
                                str(rb.property("language_code")))
                        except Exception:  # noqa: BLE001 — onboarding must not break on it
                            pass
                        break
            self._persist_brain_choice()
            if not self.choice_only:
                # v5.49 (programme item 34 completion): the
                # profile page's pick becomes the stored
                # TOOL_PROFILE at Finish, exactly like the
                # language pick below. The choice-only flow never
                # visits the page, so it must not persist the
                # page's default either.
                self._persist_profile_choice()
                # Round-3 wizard expansion: the theme and name
                # picks persist at Finish the same way.
                self._persist_theme_choice()
                self._persist_name_choice()
                self._persist_home_city_choice()
                self._persist_sound_choice()
                for name in FIRST_RUN_STAGES:
                    self._progress.mark_done(name)
            self._mic_timer.stop()
            self._setup_timer.stop()
            self._feature_timer.stop()
            self.accept()
            return
        if (current is self._choice_page
                and self.brain_choice is None):
            return  # the gate (the button is disabled too — belt and braces)
        if current is self._signin_page and not self._advance_from_signin_page():
            return  # warned — stay on the sign-in page
        idx = self._page_order.index(current)
        for nxt in self._page_order[idx + 1:]:
            if self._page_applies(nxt):
                if current is self._choice_page:
                    self._maybe_start_downloads()
                self.pages.setCurrentWidget(nxt)
                break
        self._sync_nav()
    def _back(self):
        current = self.pages.currentWidget()
        idx = self._page_order.index(current)
        for prev in reversed(self._page_order[:idx]):
            if self._page_applies(prev):
                self.pages.setCurrentWidget(prev)
                break
        self._sync_nav()
    def closeEvent(self, event):
        self._mic_timer.stop()
        self._setup_timer.stop()
        self._feature_timer.stop()
        super().closeEvent(event)
