"""Flow logic for the first-run onboarding dialog (GUI split #13 batch 2).

Stage logic, navigation, persistence, and closeEvent, moved verbatim
from gui/onboarding.py. Composed into OnboardingDialog by
gui/onboarding.py as FlowMixin. No behaviour change.
"""

from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import qsettings_set
from voice_assistant.core.gui_facade import set_preferred_language

from .firstrun_g import FIRST_RUN_STAGES
from .style import ACCENT, AMBER, DANGER, OK, TEXT_MUTED, TYPE_CAPTION


class FlowMixin:
    """Stage/nav/persist flow for OnboardingDialog (GUI split #13)."""

    def _key_stored(self) -> bool:
        """True when a Gemini key is stored (the in-memory config
        field, which save_api_key keeps in step with .env +
        os.environ). The wizard's key page and done lines promise
        a Gemini key specifically, so this is the api_key field,
        not the selected provider's key (round-2 N4 FAIL 2)."""
        return bool(str(getattr(config, "api_key", "") or "").strip())

    def _done_brain_line(self) -> str:
        """The done page names the brain the user actually has:
        the cloud lines claim 'your Gemini key' only when one is
        stored. Without a key they say so plainly and name
        Settings as the way back (round-2 N4 FAIL 2)."""
        choice = self.brain_choice or ""
        if choice in ("cloud", "both") and not self._key_stored():
            return self._BRAIN_LINES_NO_KEY[choice]
        return self._BRAIN_LINES.get(choice, "")

    def _advance_from_key_page(self) -> bool:
        """Continue from the key page (round-2 N4 FAIL 2). A pasted
        key is saved on Continue — Continue never silently drops
        a key the user pasted but did not press Save for. A short
        key stays, with the same 'doesn't look like a full key'
        line the Save button gives. An empty field with no stored
        key warns once and stays; the second, deliberate press
        advances. A stored key (from an earlier run) needs no
        warning: the empty field just keeps it."""
        field = getattr(self, "ob_api_key", None)
        status = getattr(self, "ob_key_status", None)
        key = field.text().strip() if field is not None else ""
        if key:
            if len(key) < 10:
                if status is not None:
                    status.setText(
                        "That doesn't look like a full key yet — "
                        "paste the whole thing.")
                    status.setStyleSheet(f"color: {DANGER};")
                return False
            self._save_key()
            return True
        if self._key_stored():
            return True
        if not getattr(self, "_key_no_key_warned", False):
            self._key_no_key_warned = True
            if status is not None:
                status.setText(
                    "No key saved yet — you picked the cloud brain, "
                    "so she'll think on this PC until you add your "
                    "Gemini key in Settings. Press Continue again "
                    "to go on without a key, or paste your key "
                    "above.")
                status.setStyleSheet(f"color: {AMBER};")
            return False
        self._key_no_key_warned = False
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
        choice and the detour state: the key page only for
        cloud/both, the Setting up page only for local/both (it
        exists only when the pack is incomplete), the advanced
        pages only while the detour is open. Everything else
        always applies."""
        if widget is self._key_page:
            return self.brain_choice in ("cloud", "both")
        if self._setup_page is not None and widget is self._setup_page:
            return self.brain_choice in ("local", "both")
        if widget in (self._voice_page, self._language_page,
                      self._profile_page):
            if not self._detour:
                return False
        if getattr(self, "choice_only", False) and widget in (
                self._welcome_page, self._mic_page, self._voice_page,
                self._language_page, self._profile_page,
                self._browser_page, self._camera_page, self._screen_page,
                self._files_page, self._permissions_page,
                self._selftest_page):
            # v5.25: the choice-only flow skips these. v5.49
            # (programme item 34 completion): the profile page
            # joins the skipped set — the brain-choice dialog is
            # about the brain, not the tool set. Phase G adds the
            # permissions + self-test pages to the same set.
            # First-run onboarding (2026-11-16): the ask pages
            # are permissions-stage pages, so they skip with it.
            return False
        return True
    def _applicable_pages(self) -> list:
        return [w for w in self._page_order if self._page_applies(w)]
    def _toggle_advanced(self) -> None:
        """Advanced setup is a detour, not a stage: opening it
        jumps to the deferred pages (voice, language, profile)
        and remembers where to return; closing it returns there.
        The shipped defaults stand unless the user changes them."""
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
        try:
            qsettings_set("setup/brain_choice", choice)
            qsettings_set("setup/local_pack_wanted",
                          choice in ("local", "both"))
        except Exception:  # noqa: BLE001
            pass
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
        try:
            qsettings_set("sounds_enabled", bool(checkbox.isChecked()))
        except Exception:  # noqa: BLE001
            pass
    def _sync_nav(self):
        current = self.pages.currentWidget()
        order = self._page_order
        idx = order.index(current)
        has_prev = any(self._page_applies(w) for w in order[:idx])
        self.btn_back.setVisible(has_prev)
        on_done = current is self._done_page
        if current is self._key_page:
            # Re-entering the key page re-arms the empty-key
            # warning: the warning belongs to this visit, never to
            # a past one (round-2 N4 FAIL 2). This cannot clobber
            # a just-shown warning — the warning path returns from
            # _next before _sync_nav runs.
            self._key_no_key_warned = False
        self.btn_next.setText("Finish" if on_done else "Continue")
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
        if current is self._key_page and not self._advance_from_key_page():
            return  # warned (or short key) — stay on the key page
        if current in (self._voice_page, self._language_page,
                       self._profile_page):
            # Detour navigation stays inside the detour: the next
            # deferred page, or back to where the detour opened
            # (closing the detour) when the last one is passed.
            seen = False
            for attr in self._ADVANCED_PAGES:
                page = getattr(self, attr, None)
                if page is current:
                    seen = True
                    continue
                if seen and page is not None and self._page_applies(page):
                    self.pages.setCurrentWidget(page)
                    self._sync_nav()
                    return
            self._detour = False
            self.btn_advanced.setText("Advanced setup")
            target = self._return_page
            self._return_page = None
            if target is not None:
                self.pages.setCurrentWidget(target)
            self._sync_nav()
            return
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
        if current in (self._voice_page, self._language_page,
                       self._profile_page):
            # Detour Back walks the deferred pages backwards; from
            # the first one it returns to where the detour opened.
            prev_page = None
            for attr in self._ADVANCED_PAGES:
                page = getattr(self, attr, None)
                if page is current:
                    break
                if page is not None and self._page_applies(page):
                    prev_page = page
            if prev_page is not None:
                self.pages.setCurrentWidget(prev_page)
            else:
                self._detour = False
                self.btn_advanced.setText("Advanced setup")
                target = self._return_page
                self._return_page = None
                if target is not None:
                    self.pages.setCurrentWidget(target)
            self._sync_nav()
            return
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
