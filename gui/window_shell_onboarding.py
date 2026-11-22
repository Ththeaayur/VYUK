# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from .onboarding import OnboardingDialog
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
from voice_assistant.core.gui_facade import VERSION
from voice_assistant.core.gui_facade import config
import datetime
from voice_assistant.core.gui_facade import qsettings_contains, qsettings_get, qsettings_set, qsettings_set_noted


class ShellOnboardingMixin:


    def _brain_choice_prompt_needed(self) -> bool:
        """v5.25: True for an install that onboarded BEFORE the
        three-option brain choice existed (v5.23) and has neither
        made the choice nor been offered it yet — those users
        loaded the new build and saw no options at all, because
        the old `onboarded` flag suppressed the whole flow. The
        app entry offers the choice-only dialog once for them."""
        try:
            s = self.settings
            return (
                bool(qsettings_get("onboarded", settings=s))
                and not qsettings_contains("setup/brain_choice", settings=s)
                and not bool(qsettings_get("setup/choice_prompt_seen_v525", settings=s))
            )
        except Exception:  # noqa: BLE001
            return False


    def _run_brain_choice_prompt(self):
        """The one-time upgrade offer (see _brain_choice_prompt_needed)."""
        self._run_onboarding(choice_only=True)


    def _run_onboarding(self, choice_only: bool = False):
        # v5.65 (Wave 10 — PF-19): the full first run does not
        # proceed past Terms + Privacy without an explicit Accept.
        # The choice-only brain prompt is not a first run, so it
        # is never gated. Declining (or closing) leaves onboarded
        # unset, so the flow re-prompts next launch.
        if not choice_only:
            try:
                from voice_assistant.core.gui_facade import terms as _terms
                from voice_assistant.core.gui_facade import DATA_DIR
                from voice_assistant.gui.terms_widget import (
                    TermsWidget as _TermsWidget)
                if not _terms.may_proceed(str(DATA_DIR)) \
                        and _TermsWidget is not None:
                    # Phase G: the Terms gate rides the Command
                    # Deck dialog, never a stock QDialog — a
                    # first run completes without a
                    # stock-looking window.
                    from .design_system.dialogs import (
                        HudDialog as _HudDialog)
                    _tdlg = _HudDialog(self, title="Terms & Privacy")
                    _twidget = _TermsWidget(_tdlg)
                    _tdlg.content_layout().addWidget(_twidget)
                    _accepted = []
                    _twidget.accept_requested.connect(
                        lambda: (_accepted.append(True),
                                  _tdlg.accept()))
                    _twidget.declined.connect(_tdlg.reject)
                    if _tdlg.exec() != QDialog.DialogCode.Accepted \
                            or not _accepted:
                        return
                    _terms.record_acceptance(str(DATA_DIR))
            except Exception:  # noqa: BLE001 — a terms failure blocks
                # first-run progress rather than waving it through:
                # the gate is fail-closed, so return without
                # onboarding and let the next launch retry.
                return
        initial = None
        if choice_only:
            initial = qsettings_get("setup/brain_choice", settings=self.settings)
        dlg = OnboardingDialog(self, choice_only=choice_only,
                               initial_choice=initial)
        # B6: onboarding's mic row can ask for the Permissions
        # page — the dialog accepts (closes) on that press, so
        # the page is visible instead of hidden behind the modal.
        try:
            dlg.open_permissions_requested.connect(
                lambda: self._show_page(4))
        except Exception:  # noqa: BLE001 — the button just closes
            pass
        dlg.exec()
        # Round-3 OAuth build: a sign-in made in the wizard shows
        # on the Settings row the moment the user comes back.
        try:
            self._refresh_gemini_signin_row()
        except Exception:  # noqa: BLE001 — the row refreshes on its own actions too
            pass
        if choice_only:
            # Offered once, whatever the user did with it — the
            # Settings button can reopen the choice any time.
            # Round-3 item 14: a mark that never persists offers
            # the choice again at the next launch. Noted on
            # failure, never swallowed.
            qsettings_set_noted("setup/choice_prompt_seen_v525", True, settings=self.settings)
        else:
            qsettings_set("onboarded", True, settings=self.settings)
            # v5.65 (Wave 10 — PF-6): a fresh install baselines the
            # changelog here, so the startup "What's new" dialog
            # never spams a new user with the backlog.
            # Round-3 item 14: a baseline that never persists
            # shows the backlog at the next launch. Noted on
            # failure, never swallowed.
            qsettings_set_noted("changelog/last_seen_version", VERSION, settings=self.settings)
            # First-open continuous choice (identity-gap queue):
            # asked once, in plain language, at the end of a
            # full first run — never on the choice-only upgrade
            # path, never for an existing install (they never
            # enter this branch), and never at the cost of
            # startup: any failure here falls open to normal
            # startup with the choice left OFF.
            try:
                self._run_first_open_prompt()
            except Exception:  # noqa: BLE001 — never break startup
                pass
            # Phase G: the coach marks the done page promises
            # follow here, once, after the privacy gate — never
            # on the choice-only path, never at the cost of
            # startup (a failed overlay degrades to no marks).
            try:
                self._run_g_coach_marks()
            except Exception:  # noqa: BLE001 — never break startup
                pass
        self.combo_voice.setCurrentText(config.voice_name)
        # v5.23: the dialog may have recorded a brain choice and
        # (for local/both) already started the pack; re-sync the
        # engine UI with what was chosen and let provisioning flow
        # through its normal gate (idempotent — a no-op when the
        # dialog's run is already in flight or nothing was chosen).
        # Setting the combo fires _on_brain_engine_changed +
        # _sync_think_card itself; when the index is unchanged no
        # signal fires, so call the follow-ups directly too.
        try:
            idx = self.combo_brain_engine.findData(self._brain_engine())
            if idx >= 0:
                self.combo_brain_engine.setCurrentIndex(idx)
            self._sync_think_card()
            self._maybe_auto_provision()
            self._refresh_setup_banner()
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        # v5.41: a full first run may have set her home language
        # (and with it her voice) inside the dialog — the Settings
        # widgets were built before it ran, so re-sync them to the
        # config the dialog left behind.
        try:
            lang_idx = self.combo_language.findData(
                str(getattr(config, "preferred_language", "en-gb")
                    or "en-gb"))
            if lang_idx >= 0:
                self.combo_language.setCurrentIndex(lang_idx)
            self._apply_kokoro_voice(
                str(getattr(config, "kokoro_voice", "bm_george")
                    or "bm_george"))
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        # Round-3 wizard expansion: the dialog may also have set
        # the theme and the user's name — re-apply the theme (the
        # same two calls the Settings theme card makes), refresh
        # the Home greeting with the name, and refresh the two
        # conditional Home cards (a skip-everything run shows the
        # finish-setup card; a self-test fault shows the fix-it
        # card). All guarded — onboarding already finished.
        try:
            self._apply_appearance()
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        try:
            self._apply_theme_to_orb()
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        try:
            self.home_greeting.setText(self._greeting())
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        try:
            self._refresh_setup_cards()
        except Exception:  # noqa: BLE001 — onboarding already finished
            pass
        # The mini orb waits for first-run onboarding to finish.
        self._maybe_show_mini_orb()


    def _run_first_open_prompt(self):
        """Show the first-open continuous choice, once.

        Called ONLY from the full first-run path of
        _run_onboarding (never the choice-only upgrade
        prompt, never a normal launch), immediately after
        onboarding closes — so it is asked in plain language
        as soon as the person opens the app, and existing
        installs, which never enter that path, are never
        re-asked. The dialog reports Yes only on an explicit
        Yes press; No, close, and any dialog failure all
        persist as allowed=False with the seen flag set, so
        the prompt is asked once and the default stays OFF.
        The choice remains reversible in Settings (Privacy &
        permissions) all the way, through the same keys.
        Nothing here starts capture: the choice only opens
        or closes the gate future continuous features must
        consult (core/firstopen_surface.gate_from_stored).
        """
        from datetime import datetime

        from voice_assistant.core.gui_facade import firstopen_surface as _fos

        try:
            stored = {
                _fos.PROMPT_SEEN_KEY: bool(qsettings_get(
                    _fos.PROMPT_SEEN_KEY, settings=self.settings)),
                _fos.CHOICE_KEY: str(qsettings_get(
                    _fos.CHOICE_KEY, settings=self.settings) or ""),
            }
        except Exception:  # noqa: BLE001 — unreadable store reads as fresh
            stored = {}
        if not _fos.should_show_prompt(stored):
            return
        allowed = False
        try:
            from .firstopen_prompt import FirstOpenPromptDialog

            dlg = FirstOpenPromptDialog(self)
            dlg.exec()
            allowed = bool(getattr(dlg, "choice_yes", False))
        except Exception:  # noqa: BLE001 — a failed prompt is default-No
            allowed = False
        try:
            now_iso = datetime.now().isoformat(timespec="seconds")
            updated = _fos.apply_choice(stored, allowed, now_iso)
            for key, value in updated.items():
                # Round-3 item 14: the first-open choice must
                # persist — a write that never lands asks again
                # at the next launch. Noted on failure; the
                # surrounding try still guards the rest.
                qsettings_set_noted(key, value, settings=self.settings)
        except Exception:  # noqa: BLE001 — never break startup
            pass


    def _run_g_coach_marks(self):
        """Launch the Phase G coach-mark sequence once (see
        gui/firstrun_g.py run_coach_sequence). Fire-and-forget:
        the overlay's own buttons drive it; a failure degrades
        to no marks, never to a broken startup. Only ever
        called from the full first-run branch of
        _run_onboarding, never from the choice-only path."""
        from .firstrun_g import run_coach_sequence as _coach
        _coach(
            self,
            # The only key ever read is COACH_SHOWN_KEY and the only
            # default ever passed is False = the spec table's
            # default, so the `d` parameter is carried, not used.
            lambda k, d=False: bool(qsettings_get(k, settings=self.settings)),
            # Round-3 item 14: the coach-shown flag is the
            # "don't repeat" marker — a write that never persists
            # replays the coach marks at the next launch. The
            # noted write logs + records the failure (feed
            # marker) instead of swallowing it in firstrun_g.
            lambda k, v: qsettings_set_noted(k, v, settings=self.settings))

    # ------------------------------------------------------------ UI build
    def _greeting(self) -> str:
        hour = datetime.datetime.now().hour
        if hour < 12:
            base = "Good morning"
        elif hour < 17:
            base = "Good afternoon"
        else:
            base = "Good evening"
        # Round-3 wizard expansion: the greeting uses the name
        # she learned in the wizard (config.user_name, persisted
        # at Finish) — "Good morning, Aayur". No name, no comma:
        # the plain form stands.
        try:
            name = str(getattr(config, "user_name", "") or "").strip()
        except Exception:  # noqa: BLE001 — the plain greeting stands
            name = ""
        return f"{base}, {name}" if name else base
