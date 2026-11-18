"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .funnel.pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/


class HandsPacksMixin:
    def _register_skills_tools(self):
        """Registers the Skills pack (core/skills_plus.py, v2.7):
        installed apps + uninstall, printing, PDF text, speak-on-demand,
        drive eject, Focus Assist, cross-folder file search, monitors.
        The module owns its catalogue via register_all (same pattern
        as the other packs)."""
        from . import skills_plus

        skills_plus.register_all(self)

    def _register_mega_tools(self):
        """Registers the Mega skills expansion (v2.8): three domain
        modules — core/skills_system.py (winget, startup, services,
        power plans, battery health, disk care), core/skills_media.py
        (image work, screenshots, audio devices, per-app volume) and
        core/skills_network.py (Wi-Fi, speed test, displays, window
        layering). Each module owns its catalogue via register_all.
        Each is wired in isolation: a broken module must never take
        the rest of the registry down with it."""
        for module_name in ("skills_system", "skills_media", "skills_network"):
            try:
                module = __import__(f"{__package__}.{module_name}", fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] Mega skills module {module_name} failed to register: {e}")
                _record_pack_failure("_register_mega_tools", module_name, e)

    def _register_meeting_tools(self):
        """Registers the meeting pack (core/meeting.py, R7-4 meetily
        pattern): start_meeting / stop_meeting / meeting_status /
        transcribe_audio — live mic capture, on-device transcription
        via asr.py, notes via produce_meeting_notes. The module owns
        its catalogue via register_all (same pattern as the other
        packs)."""
        from . import meeting

        meeting.register_all(self)

    def _register_summary_tools(self):
        """Registers the summarisation pack (core/summary.py, R8-3
        sumy port): summarise_text — extractive TextRank summary
        of caller-supplied text, pure computation, no model, no
        network, nothing leaves the machine. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        from . import summary

        summary.register_all(self)

    def _register_browser_cdp_inspect_tools(self):
        """Registers the CDP inspection pack
        (core/desktop/browser_cdp_inspect.py, R10-1
        chrome-devtools-mcp port): browser_console /
        browser_network / browser_performance — read-only
        console / network / performance inspection of the
        user's attached real Chrome over the tree's own CDP
        path (stdlib WebSocket client, no new dependency).
        Gated on the real-Chrome attach opt-in; no bodies, no
        headers, query strings stripped. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        from .desktop import browser_cdp_inspect

        browser_cdp_inspect.register_all(self)

    def _register_browser_history_tools(self):
        """Registers the browser-history pack
        (core/desktop/browser_history.py, R10-3 browser-history
        port): browser_history / browser_bookmarks — read-only
        stored history and bookmarks of a browser profile the
        caller names. Own stdlib sqlite3 + JSON reader over a
        copy of the profile's History / places.sqlite / Bookmarks
        file; nothing adopted, no profile auto-discovery. Gated
        on the dedicated history opt-in (OFF by default); the
        live file is never opened and its size + mtime are
        verified unchanged before the answer is given. The module
        owns its catalogue via register_all (same pattern as the
        other packs)."""
        from .desktop import browser_history

        browser_history.register_all(self)

    def _register_browser_adblock_tools(self):
        """Registers the python-adblock pack
        (core/desktop/browser_adblock.py, R10-4 python-adblock
        port): check_url_blocked / filter_list_status — two
        read-only tools over a filter list the caller supplies.
        Own stdlib ABP-subset matcher; nothing adopted, no list
        bundled, no list fetched. The interception half is
        STOPPED (the browser engine it would plug into ships OFF
        pending its live A/B win) — this pack is the matching
        engine only. The module owns its catalogue via
        register_all (same pattern as the other packs)."""
        from .desktop import browser_adblock

        browser_adblock.register_all(self)

    def _register_article_extract_tools(self):
        """Registers the python-readability pack
        (core/article_extract.py, R10-5 python-readability
        port): extract_article — one read-only tool that scores
        a page's HTML and returns its main article body. Own
        code over the tree's already-declared lxml (BSD-3);
        nothing adopted, no chardet / cssselect /
        lxml-html-clean. The article text is untrusted web
        content (taint "web page"); the URL form fetches through
        the existing web-lookup seams, so it is an egress read
        under taint, like page_word_search. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        from . import article_extract

        article_extract.register_all(self)

    def _register_ollama_models_tools(self):
        """Registers the ollama pack (core/ollama_models.py, R11-1
        ollama port): ollama_status (read-only) + ollama_pull /
        ollama_delete (confirm-gated) — model management over the
        local Ollama server's REST API. Own stdlib code (urllib
        only); nothing adopted — no Go server, no ``ollama`` PyPI
        client (it would drag pydantic + httpx for nothing the
        tree's own urllib calls do not already do). The status read
        is a loopback HTTP call, so it is an egress read with no
        taint entry (model-inventory metadata from the operator's
        own local server, never web content); pull downloads
        gigabytes via the server and takes the "long" deadline
        class. The module owns its catalogue via register_all
        (same pattern as the other packs)."""
        from . import ollama_models

        ollama_models.register_all(self)

    def _register_event_planning_tools(self):
        """Registers the event-planning pack (core/event_planning.py,
        R16-8 the-wedding-website inspiration): event_plan /
        event_guest / event_vendor / event_milestone /
        event_schedule (free by design, "app-state" — they write
        only to VYUK's own plan store, the budget_envelope /
        expense_log precedent) + event_budget (read-only — pure
        computation over the stored plan). Own stdlib code over
        the tree's own money + task + scheduler shapes; nothing
        adopted — no Django, no React, no hosted site, no RSVP
        link flow, no honeymoon fund, no Sheets export. The
        module owns its catalogue via register_all (same pattern
        as the other packs)."""
        from . import event_planning

        event_planning.register_all(self)

    def _register_event_ticketing_tools(self):
        """Registers the event-ticketing pack (core/event_ticketing.py,
        R18-7 Attendize inspiration): event_tickets — ticket types,
        sale records and QR check-in for one event plan (free by
        design, "app-state" — it writes only to VYUK's own
        ticketing store, the event_plan precedent). Own stdlib code
        over the tree's own money + event-planning + QR shapes;
        nothing adopted — no Laravel, no hosted site, no payment
        gateway, no multi-organiser accounts. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        from . import event_ticketing

        event_ticketing.register_all(self)

    def _register_technical_analysis_tools(self):
        """Registers the technical-analysis pack
        (core/technical_analysis.py, R18-8 bukosabino/ta
        inspiration): technical_analysis — 25 indicators over
        operator-supplied OHLCV candles (free by design,
        "app-state" — it writes only to VYUK's own dataset +
        ledger store, the event_planning / event_tickets
        precedent). Own stdlib code over the published indicator
        formulas; nothing adopted — no `ta` code, no pandas, no
        numpy, no live price feed, no yfinance, no network. The
        module owns its catalogue via register_all (same pattern
        as the other packs)."""
        from . import technical_analysis

        technical_analysis.register_all(self)

    def _register_time_tracking_tools(self):
        """Registers the working-time tracker pack
        (core/time_tracker.py, R18-10 dominikbraun/timetrace
        inspiration): time_tracker — projects, start/stop, records,
        status, report and a hash-chained ledger (free by design,
        "app-state" — it writes only to VYUK's own tracker store,
        the event_planning / event_tickets / technical_analysis
        precedent). Own stdlib code over the tracker shape; nothing
        adopted — no Go code, no timetrace binary, no invoicing, no
        billing, no network. The module owns its catalogue via
        register_all (same pattern as the other packs)."""
        from . import time_tracker

        time_tracker.register_all(self)

    def _register_eye_rest_tools(self):
        """Registers the eye-rest pack (core/eye_rest.py, R8-4
        screen-break port): eye_rest_timer / breathing_guide /
        log_glass — a 20-20-20 nudge chain (same shape as
        water_break), a timed guided-deep-breathing script (pure
        computation), and a water-glass counter in the app's own
        memory. Track / notice / organise only: no diagnosis, no
        scoring, no coaching, no biometric input, no enforcement.
        The module owns its catalogue via register_all (same
        pattern as the other packs)."""
        from . import eye_rest

        eye_rest.register_all(self)

    def _register_wormhole_tools(self):
        """Registers the wormhole pack (core/wormhole.py, R8-5
        magic-wormhole port): wormhole_offer / wormhole_receive /
        wormhole_status — a code-phrase encrypted file transfer
        between two machines running VYUK on the same local network.
        LAN-direct only: no relay, no public server, no account, no
        cloud; the listener binds the private LAN address only, the
        phrase is single-use with a 10-minute expiry, three wrong
        guesses cancel the offer, and the bytes are verified against
        the sender's tag before the file is presented as received.
        The module owns its catalogue via register_all (same
        pattern as the other packs)."""
        from . import wormhole

        wormhole.register_all(self)

    def _register_basic_pitch_tools(self):
        """Registers the basic-pitch pack (core/basic_pitch.py, R8-7
        basic-pitch port): audio_to_midi / audio_to_midi_status —
        hum, sing, or play any instrument and get back polyphonic
        MIDI with pitch bends. Own numpy + stdlib port over the
        tree's declared onnxruntime (Qt only); no TensorFlow, no
        librosa, no basic-pitch package. The ICASSP-2022 model is
        operator-supplied only at data/models/basic-pitch/nmp.onnx
        (pinned sha256, never bundled in this batch). WAV input
        only — no ffmpeg ships in the tree. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        from . import basic_pitch

        basic_pitch.register_all(self)

    def _register_discord_presence_tools(self):
        """Registers the Discord presence pack (core/discord_presence.py,
        R8-9 pypresence adoption): discord_presence /
        set_discord_client_id — Rich Presence in the local Discord
        client via the adopted pypresence 4.6.2 (MIT, zero runtime
        dependencies; only `Presence` is used). Opt-in and off by
        default, never auto-start; the Discord application ID is
        operator-supplied (no VYUK application ID ships); no Discord
        running is an honest refusal. The module owns its catalogue
        via register_all (same pattern as the other packs)."""
        from . import discord_presence

        discord_presence.register_all(self)

    def _register_rustic_backup_tools(self):
        """Registers the rustic backup pack (core/rustic_backup.py,
        R9-2 rustic adoption): init_backup_repo / backup_files /
        list_backup_snapshots / restore_backup / backup_status —
        encrypted, deduplicated user-file backup through the
        operator-supplied rustic 0.11.4 binary (Apache-2.0; the
        binary is never bundled in this batch, its sha256 is pinned
        and verified before every run). The repository password is
        operator-supplied via RUSTIC_PASSWORD at call time only,
        never stored, never logged, never a tool argument; local
        repositories only; nothing ever auto-starts a backup. The
        module owns its catalogue via register_all (same pattern as
        the other packs)."""
        from . import rustic_backup

        rustic_backup.register_all(self)

    def _register_status_strip_tools(self):
        """Registers the status-strip pack (core/status_strip.py,
        R8-6 yasb port): status_strip — a thin always-on-top bar
        with live CPU / MEM / DISK / NET / BAT, the five System
        Pulse vitals. PORT of YASB's widget-catalogue pattern only
        (no YASB code, no YAML/CSS config surface): the bar is a
        VYUK QWidget fed by the Pulse sampler seam, opt-in and off
        by default, sampling only while visible. No media (no
        now-playing source in the tree), no workspaces (no
        virtual-desktop concept), no pomodoro (focus_session
        exists), no per-app polling (sensing-free boundary). The
        module owns its catalogue via register_all (same pattern as
        the other packs); the window registers the sink the tool
        calls (gui/window_shell_frame.py, frame-sink seam)."""
        from . import status_strip

        status_strip.register_all(self)

    def _register_companion_tools(self):
        """Registers the companion pack (core/companion.py, R7-5
        kovrel pattern): start_companion / stop_companion /
        companion_status / companion_pairing — the phone browser as
        a remote for the laptop (LAN-only server, QR+PIN pairing,
        screen/touchpad/keyboard/media/clipboard/files/power). The
        module owns its catalogue via register_all (same pattern as
        the other packs)."""
        from . import companion

        companion.register_all(self)

    def _register_wake_training_tools(self):
        """Registers the wake-training pack (core/voice/
        wake_training.py, R7-6 micro-wake-word pattern):
        train_custom_wake_word / enable_custom_wake_model /
        list_custom_wake_models / custom_wake_model_status —
        train-your-own wake word from synthetic speech, tiny
        numpy streaming model, model slot armed in the detector.
        The module owns its catalogue via register_all (same
        pattern as the other packs)."""
        from .voice import wake_training

        wake_training.register_all(self)

    def _register_dagflow_tools(self):
        """Registers the workflow pack (core/dagflow.py, R7-7
        taskflow pattern): define_workflow / run_workflow /
        list_workflows / workflow_runs — a DAG of funnel-tool
        calls with dependencies, trigger rules, retries and
        SQLite run history. Nodes execute through the ONE
        funnel (_execute_tool) with their own gates; the DAG
        adds no privilege. The module owns its catalogue via
        register_all (same pattern as the other packs)."""
        from . import dagflow

        dagflow.register_all(self)

    def _register_proactivity_tools(self):
        """Registers the proactivity pack (core/proactivity.py,
        R7-8 ProactiveAgent pattern): score_intervention /
        run_proactivity_bench / list_proactivity_scores /
        proactivity_bench_results — when-to-intervene scoring
        + a 36-case benchmark, MEASUREMENT MODE ONLY. The
        scorer returns a scored suggestion with its reasons;
        it never executes a tool, never senses anything, and
        never wires an intervention to run. The module owns
        its catalogue via register_all (same pattern as the
        other packs)."""
        from . import proactivity

        proactivity.register_all(self)

    def _register_hands2_tools(self):
        """Registers the "More hands" expansion (v5.4): three packs —
        core/skills_desktop_prefs.py (wallpaper, dark mode, keep-awake,
        virtual desktops, Settings pages, clipboard, toasts),
        core/skills_system_monitor.py (processes, in-file search, event log,
        restore points, Task Scheduler, printers, VPN, screen
        timeout) and core/skills_smart.py (calculator, units, world
        clock, password generator, file compare). Each module owns
        its catalogue via register_all; each is wired in isolation
        like the mega packs, so a broken pack can never take the
        registry down."""
        for module_name in ("skills_desktop_prefs", "skills_system_monitor",
                            "skills_smart"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] More-hands module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_hands2_tools", module_name, e)

    def _register_hands3_tools(self):
        """Registers the "More depth" expansion (v5.5): four packs —
        core/skills_system_inventory.py (hardware, drivers, security, power
        settings), core/skills_network_diag.py (connections, traceroute,
        adapters, port checks), core/skills_files_ops.py (PDF merge,
        image info, shredder, folder sync, file clipboard) and
        core/skills_smart_encode.py (base64, hashing, JSON, date math,
        UUIDs). Same isolation as the other multi-pack wirings: one
        broken pack never takes the registry down."""
        for module_name in ("skills_system_inventory", "skills_network_diag",
                            "skills_files_ops", "skills_smart_encode"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] More-depth module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_hands3_tools", module_name, e)

    def _register_hands4_tools(self):
        """Registers the "Wider reach" expansion (v5.11): six packs —
        core/skills_screen_capture.py (region/window capture, pixel colour),
        core/skills_desktop_layouts.py (window layouts + lock_pc),
        core/skills_media_convert.py (image resize/convert, PDF split),
        core/skills_productivity.py (snippets),
        core/skills_network_wake_shares.py (Wake-on-LAN, share listing) and
        core/skills_system_tuning.py (microphone control, process priority,
        camera/mic usage records). Its screenshot_region deliberately
        REPLACES the older skills_media implementation of the same
        name — stricter and verified: clamping with disclosure
        instead of refusal, a save-path argument, multi-monitor
        coordinates, and the saved file read back before reporting.
        That replacement only happens because this pack registers
        AFTER skills_media; do not move this call earlier."""
        for module_name in ("skills_screen_capture", "skills_desktop_layouts",
                            "skills_media_convert", "skills_productivity",
                            "skills_network_wake_shares", "skills_system_tuning"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] Wider-reach module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_hands4_tools", module_name, e)

    def _register_hands5_tools(self):
        """Registers the v5.12 expansion: six packs —
        core/skills_text.py (text stats, case conversion, contact
        extraction, gated find-and-replace), core/skills_display_settings.py
        (refresh rate + screen rotation with read-back),
        core/skills_files_inspect.py (folder diff, EXIF stripping, PDF
        info/rotate), core/skills_network_web_diag.py (website status, SSL
        certificates, ARP device listing, port ownership),
        core/skills_smart_chance.py (loan + compound calculators, dice,
        random choice) and core/skills_desktop_tiling.py (window tiling,
        focus sessions, clipboard file listing). Audited against the
        v5.11 registry before design: no name in this wave existed
        (compare_files already covers text diffs, which is why this
        wave has none)."""
        for module_name in ("skills_text", "skills_display_settings",
                            "skills_files_inspect", "skills_network_web_diag",
                            "skills_smart_chance", "skills_desktop_tiling"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.12 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_hands5_tools", module_name, e)

    def _register_play_tools(self):
        """Registers the v5.13 "Edit, play, create" wave: three
        packs — core/skills_photo.py (the photo EDITING lab: crop,
        rotate/flip, enhance, filters, watermark, borders, collages;
        the older skills_media tools only process — resize, convert,
        compress — and are untouched), core/skills_games.py (five
        playable games with state in data/games.json: tic-tac-toe,
        hangman, number guessing, trivia, a Wordle-style word game)
        and core/skills_creative.py (ASCII art from an in-module
        hand-drawn font, colour harmonies, seeded generative art,
        seeded original melodies as WAV, writing prompts)."""
        for module_name in ("skills_photo", "skills_games",
                            "skills_creative"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.13 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_play_tools", module_name, e)

    def _register_create2_tools(self):
        """Registers the v5.14 "Creativity, deepened" wave:
        core/skills_creative_generate.py (fractals, chord-progression songs,
        synthesized drum beats, name generator, story outlines,
        by-construction haiku), core/skills_photo_effects.py (creative
        photo effects: kaleidoscope, duotone, pixelate, glitch) and
        core/skills_studio.py (compositions that reuse other packs
        by import: make_wallpaper = create_art + set_wallpaper,
        palette_image = the harmony core rendered as a swatch).
        The wave also extended skills_creative.create_art with
        phyllotaxis + starfield styles — output-identical for the
        four original styles at any seed (hash-regression proven)."""
        for module_name in ("skills_creative_generate", "skills_photo_effects",
                            "skills_studio"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.14 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_create2_tools", module_name, e)

    def _register_nose_tools(self):
        """Registers the v5.20 NOSE pack: core/skills_nose.py
        (sniff file/link/text before trusting them, the Wi-Fi
        radar, network quality, room brightness). The module owns
        its catalogue via register_all."""
        from . import skills_nose

        skills_nose.register_all(self)

    def _register_browser_tools(self):
        """Registers full web control (core/browser/browser.py): 6 browser tools on a
        Playwright worker thread — CDP attach to the user's real Chrome or
        VYUK's own persistent browser; DOM-level read/click/type/select with
        password + payment trust gates. The module owns its catalogue via
        register_all."""
        from . import browser

        browser.register_all(self)

    def _register_hands6_tools(self):
        """Registers the v5.21 "Hands II" wave: five packs —
        core/skills_files_media_ops.py (image compare, sprite sheets, batch
        watermark, PDF image extraction, WAV trim/fade, folder size
        map), core/skills_productivity_tools.py (pomodoro + countdown on
        the shared scheduler, a persistent stopwatch, timezone and
        date calculators, password strength, JSON queries),
        core/skills_system_bench.py (startup impact, environment backup
        with secret redaction, disk speed test, RAM/CPU leaders, GPU
        info, Windows Update status), core/skills_web_research.py (RSS
        digests, unshortening, page watching, header audits, IP
        geolocation, Wayback lookups) and core/skills_creative_play.py
        (image palettes, gradient CSS, story dice, anagrams,
        metronome and binaural WAVs). Audited against the v5.20
        registry first: unit_convert, battery_health and merge_pdfs
        already existed, so this wave adds the 33 that were missing
        (registry 251 -> 284)."""
        for module_name in ("skills_files_media_ops", "skills_productivity_tools",
                            "skills_system_bench", "skills_web_research",
                            "skills_creative_play"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.21 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_hands6_tools", module_name, e)

    def _register_s4_tools(self):
        """Registers the Phase-2 section-4 wave (v5.30): seven
        packs — core/skills_window_control.py (window snapping, monitor
        moves, path drags, OCR scroll/find, screen diff, shortcut
        cheatsheets), core/skills_senses_checks.py (mic/speaker checks,
        room noise, voice notes, selection read-aloud, a camera
        summary, brightness auto-match, a basic sound watcher),
        core/skills_productivity_day.py (focus report, meeting prep,
        deadline radar, habits, water/stand-up nudges, inbox
        triage, unsubscribe finder, bills, shopping and packing
        lists, birthday radar), core/skills_web_watch.py (price watch,
        page-change summaries, read-aloud extraction, link health,
        download ETA, site permissions, tab census, QR decode),
        core/skills_files_recipes.py (folder recipes, face blur,
        screenshot notes, PDF bookmark-merge and split-by-text,
        contact sheets, pitch-kept speed change, silence trim,
        near-duplicate photos, file ageing), core/skills_system_health.py
        (startup time, battery drain, Wi-Fi dead spots, disk
        writers, an update restart planner, the restore-point
        ritual, crash digest, peripheral roll call, screen time,
        eject all) and core/skills_fun.py (music trivia, story
        chain, word of the day, compliments, dice-plus, a decision
        helper, ambient scenes, a morning quote). Each module owns
        its catalogue via register_all; each is wired in isolation
        (registry 279 -> 345)."""
        for module_name in ("skills_window_control", "skills_senses_checks",
                            "skills_productivity_day", "skills_web_watch",
                            "skills_files_recipes", "skills_system_health",
                            "skills_fun"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.30 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_s4_tools", module_name, e)

