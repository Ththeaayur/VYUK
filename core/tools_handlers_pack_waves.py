"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .funnel.pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/


class WavePacksMixin:
    def _register_wave2_tools(self):
        """Registers the Phase-2 wave-2 packs (v5.31): seven
        small packs — core/skills_files_data.py (CSV preview, query
        and column stats, a folder outline, a file tail reader,
        a JSON differ), core/skills_productivity_logs.py (an expense
        log, a decision log, a reading list, one pending view,
        a meeting cost calculator), core/skills_smart_convert.py
        (placeholder text, Morse and NATO translators, Roman
        numerals, a bill splitter, a BMI calculator),
        core/skills_fun_party.py (riddles, rock-paper-scissors,
        mad libs, a daily fortune), core/skills_senses_reports.py
        (sense trends and a presence report from the timeline),
        core/skills_web_lookup.py (find-a-word on a page, a domain
        record lookup) and core/skills_system_net_admin.py (the hosts
        file, a PATH audit, clock-sync status). Each module owns
        its catalogue via register_all; each is wired in
        isolation (registry 345 -> 373)."""
        for module_name in ("skills_files_data", "skills_productivity_logs",
                            "skills_smart_convert", "skills_fun_party",
                            "skills_senses_reports", "skills_web_lookup",
                            "skills_system_net_admin"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.31 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_wave2_tools", module_name, e)

    def _register_wave3_tools(self):
        """Registers the Phase-2 wave-3 packs (v5.32): eight
        everyday packs — core/skills_study.py (flashcards with
        a review loop, quizzes, a typing test, reading level),
        core/skills_kitchen.py (recipe scaling, oven and egg
        maths, pantry checks, a grocery list),
        core/skills_money.py (savings, loans, envelopes,
        subscriptions), core/skills_dev.py (regex, cron,
        subnets, converters), core/skills_travel.py (distances,
        bearings, sun and moon maths, meeting times),
        core/skills_music.py (notes, scales, chords, practice),
        core/skills_habits.py (habit streaks, mood numbers, a
        day review) and core/skills_outdoors.py (daylight, UV
        and air bands, watering, walks). Each module owns its
        catalogue via register_all; each is wired in isolation
        (registry 373 -> 453). The S9b/S9c tail (same release):
        core/skills_admin.py (document and warranty vaults with
        an expiry radar, contact cards, service schedules, an
        emergency card, gift/borrow/asset logs) and
        core/skills_words.py (reading time, acronyms, citations,
        signatures, slugs, word and sentence statistics, a
        filler-word counter, a heuristic passive-voice check, a
        quote vault) — registry 455 -> 475."""
        for module_name in ("skills_study", "skills_kitchen",
                            "skills_money", "skills_dev",
                            "skills_travel", "skills_music",
                            "skills_habits", "skills_outdoors",
                            "skills_admin", "skills_words"):
            try:
                module = __import__(f"{__package__}.{module_name}",
                                    fromlist=["register_all"])
                module.register_all(self)
            except Exception as e:  # noqa: BLE001
                print(f"[Tools] v5.32 module {module_name} "
                      f"failed to register: {e}")
                _record_pack_failure("_register_wave3_tools", module_name, e)

    def _register_toolbox_tools(self):
        """Registers the tool router's dispatcher (v5.33,
        core/funnel/toolbox.py): use_toolbox reaches the whole
        registry from a session that only loaded the core
        set. Registered like any other tool, so the local
        and text brains can use it too; its handler resolves
        this singleton at call time and executes through
        this same funnel (registry 475 -> 476)."""
        try:
            from .funnel import toolbox as _toolbox
            _toolbox.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] toolbox module failed to register: {e}")
            _record_pack_failure("_register_toolbox_tools", 'toolbox', e)

    def _register_study_t1_tools(self):
        """Registers the Triage Wire Build 1 Study flow tools
        (core/study_flow_t1.py): study_practice, study_review,
        study_export_deck, study_session — the triage Study top
        3 (st_deck / st_schedule / st_practice) as live tools.
        The module owns its catalogue via register_all; this
        method only wires it in (same pattern as the other
        packs). Export/session tools write a caller-chosen file
        and are confirm-gated (write_file parity in
        core/funnel/tools_gate.py)."""
        try:
            from . import study_flow_t1 as _study_flow

            _study_flow.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] study flow module failed to register: {e}")
            _record_pack_failure("_register_study_t1_tools", 'study_flow_t1', e)

    def _register_triage2_tools(self):
        """Registers the Triage Wire Build 2 tools
        (core/creative_dev_social_t2.py): creative_rights_check,
        creative_rights_add, creative_subtitle_edit,
        creative_subtitle_export, creative_timeline,
        dev_codebase_qa, vcard_import, vcard_export — the
        triage Creative/Developer/Social candidates #4-#7
        (creative_rights_ledger / creative_subtitle_edit / developer_codebase_qa /
        social_vcard) as live tools. The module owns its
        catalogue via register_all; this method only wires it
        in (same pattern as the Study pack). The three writers
        (rights add, subtitle export, vCard export) are
        confirm-gated (write_file parity in core/funnel/tools_gate.py);
        the five pure tools are read-only."""
        try:
            from . import creative_dev_social_t2 as _triage2

            _triage2.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] triage2 module failed to register: {e}")
            _record_pack_failure("_register_triage2_tools", 'creative_dev_social_t2', e)

    def _register_audio_localise_tools(self):
        """Registers the audio-localisation pack
        (core/audio_localise.py, R19-1 Huanshere/VideoLingo
        inspiration): audio_localise — transcribe an audio file
        (faster-whisper), translate the segments (the product's own
        Gemini key), write subtitle files (SRT/VTT) and dub the
        translation with the tree's own Kokoro TTS into a full-length
        dubbed WAV, over a hash-chained ledger. The translate and
        localise actions are confirm-gated (they call the owner's
        Gemini endpoint); the local actions are free app-state and
        the reads are read-class. Own stdlib code over the pipeline
        shape; nothing adopted — no VideoLingo code, no Streamlit,
        no Qwen3/WhisperX, no yt-dlp, no FFmpeg, no video
        processing. The module owns its catalogue via register_all
        (same pattern as the other packs)."""
        try:
            from . import audio_localise as _audio_localise

            _audio_localise.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] audio_localise module failed to register: {e}")
            _record_pack_failure("_register_audio_localise_tools", 'audio_localise', e)

    def _register_optimise_tools(self):
        """Registers the optimisation pack
        (core/optimise.py, R19-2 google/or-tools inspiration):
        optimise — a combinatorial optimisation engine in own stdlib
        code (TSP/VRP heuristics, bin packing, knapsack DP, Dijkstra,
        Hungarian assignment, Edmonds-Karp max flow, two-phase simplex
        LP, a small backtracking constraint solver) over a hash-chained
        ledger. Free by design (app-state): the operator supplies
        every number, the tool only computes. Nothing adopted — no C++
        code, no OR-Tools library, no wrapper, no port. The module
        owns its catalogue via register_all (same pattern as the other
        packs)."""
        try:
            from . import optimise as _optimise

            _optimise.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] optimise module failed to register: {e}")
            _record_pack_failure("_register_optimise_tools", 'optimise', e)

    def _register_erp_tools(self):
        """Registers the ERP pack
        (core/erp.py, R19-3 aureuserp/aureuserp inspiration):
        erp — a unified local business record in own stdlib code
        (customers, leads, products, stock derived from the movement
        ledger, sales/purchase orders, invoices, payments, employees,
        projects) over a hash-chained ledger. Free by design
        (app-state): every record is the owner's own local data, and
        invoices/payments are records the owner made — the money moved
        outside VYUK, the tool never takes payment, never computes tax,
        never keeps payroll. Nothing adopted — no PHP code, no Laravel,
        no Filament, no MySQL, no wrapper, no port. The module owns
        its catalogue via register_all (same pattern as the other
        packs)."""
        try:
            from . import erp as _erp

            _erp.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] erp module failed to register: {e}")
            _record_pack_failure("_register_erp_tools", 'erp', e)

    def _register_media_chat_tools(self):
        """Registers the media-chat pack
        (core/media_chat.py, R19-4 nexmoe/VidBee inspiration):
        media_chat — chat with your media: register a transcript
        (from an audio_localise project or an operator-supplied
        SRT/VTT/plain-text file), then ask questions or request a
        summary over it, answered by the product's own Gemini key,
        over a hash-chained ledger. The ask and summarize actions
        are confirm-gated (they call the owner's Gemini endpoint);
        the local actions are free app-state and the reads are
        read-class. Own stdlib code over the Q&A shape; nothing
        adopted — no VidBee code, no yt-dlp download, no
        diarization, no video processing, no FFmpeg. The module owns
        its catalogue via register_all (same pattern as the other
        packs)."""
        try:
            from . import media_chat as _media_chat

            _media_chat.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] media_chat module failed to register: {e}")
            _record_pack_failure("_register_media_chat_tools", 'media_chat', e)

    def _register_invoice_tools(self):
        """Registers the invoice pack
        (core/invoice.py, R19-5 al1abb/invoify inspiration):
        invoice — render an erp invoice as a PDF (three templates:
        classic, modern, minimal) with a minimal stdlib PDF writer
        (no PDF library, no new dependency), and send it by email
        with the PDF attached (the send_email tool, extended in this
        batch with an optional `attachments` property). The invoice
        record itself lives in erp; this tool renders and sends it.
        The send action is confirm-gated (it SENDS real mail); the
        render, the templates, the seller record and the reads stay
        free. Own stdlib code over the invoice shapes; nothing
        adopted — no Next.js, no TypeScript, no Shadcn, no web app,
        no PDF library, no tax computation. The module owns its
        catalogue via register_all (same pattern as the other
        packs)."""
        try:
            from . import invoice as _invoice

            _invoice.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] invoice module failed to register: {e}")
            _record_pack_failure("_register_invoice_tools", 'invoice', e)

    def _register_translations_tools(self):
        """Registers the translations pack
        (core/translations.py, R19-6 tolgee/tolgee-platform
        inspiration): translations — manage UI string localization
        (projects, keys, translations per language), import/export
        in JSON/PO/XLIFF, and machine translation with the product's
        own Gemini key, over a hash-chained ledger. The translate
        action is confirm-gated (it calls the owner's Gemini
        endpoint); the local actions are free app-state and the reads
        are read-class. Own stdlib code over the localization shapes;
        nothing adopted — no TypeScript, no web app, no ee/ code
        (the Tolgee ee/ carve-out is the non-commercial Tolgee EE
        License v1 and is EXCLUDED), no in-context editing, no audio
        translation (audio_localise already does that). The module
        owns its catalogue via register_all (same pattern as the
        other packs)."""
        try:
            from . import translations as _translations

            _translations.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] translations module failed to register: {e}")
            _record_pack_failure("_register_translations_tools",
                                 'translations', e)

    def _register_filament_tools(self):
        """Registers the filament pack
        (core/filament.py, R19-7 Donkie/Spoolman inspiration):
        filament — a local 3D-printer filament spool inventory
        (vendors, filament types, spools with remaining weight and
        length, usage recording, low-stock warnings) over a
        hash-chained ledger. Free by design (app-state: it writes
        only to VYUK's own store); the reads are read-class. Own
        stdlib code over the inventory shapes; nothing adopted —
        no FastAPI, no SQLAlchemy, no web app, no server, no
        printer control (OctoPrint / Klipper / Moonraker), no
        SpoolmanDB, no QR labels. The module owns its catalogue
        via register_all (same pattern as the other packs)."""
        try:
            from . import filament as _filament

            _filament.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] filament module failed to register: {e}")
            _record_pack_failure("_register_filament_tools",
                                 'filament', e)

    def _register_deals_tools(self):
        """Registers the deals pack
        (core/deals.py, R19-8 Django-CRM/Django-CRM inspiration):
        deals — a deal pipeline and support tickets on erp customers
        (pipelines with seeded stages, deals with probability and
        aging, tickets with priority and SLA targets) over a
        hash-chained ledger. Free by design (app-state: it writes
        only to VYUK's own store); the reads are read-class. Own
        stdlib code over the CRM shapes; nothing adopted — no Django,
        no DRF, no SvelteKit, no Flutter, no PostgreSQL, no web app,
        no server, no multi-user. The module owns its catalogue
        via register_all (same pattern as the other packs)."""
        try:
            from . import deals as _deals

            _deals.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] deals module failed to register: {e}")
            _record_pack_failure("_register_deals_tools",
                                 'deals', e)

    def _register_vehicle_routes_tools(self):
        """Registers the vehicle_routes pack
        (core/vehicle_routes.py, R19-10 VROOM-Project/vroom inspiration):
        vehicle_routes — advanced vehicle routing in own stdlib Python
        (time-windowed routes, pickup-and-delivery shipments,
        multi-depot heterogeneous fleets, vehicle skills, breaks and
        working hours) over a hash-chained ledger. Free by design
        (app-state: the operator supplies every number, the tool only
        computes and records); the reads are read-class. Own stdlib
        code over the routing shapes; nothing adopted — no C++ code,
        no Vroom binary, no library, no routing engine, no network.
        The module owns its catalogue via register_all (same pattern
        as the other packs)."""
        try:
            from . import vehicle_routes as _vehicle_routes

            _vehicle_routes.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] vehicle_routes module failed to register: {e}")
            _record_pack_failure("_register_vehicle_routes_tools",
                                 'vehicle_routes', e)

    def _register_social_send_tools(self):
        """Registers the social_send pack (core/social_send.py,
        SOCIAL Phase 1 — approved design, WhatsApp first): one
        tool, social_send — WhatsApp send via the official wa.me
        Click to Chat link, with the owner sending by voice
        (Windows Voice Access presses Enter at his spoken command).
        VYUK never sends, types nothing and presses nothing. The
        module owns its catalogue via register_all (same pattern as
        the other packs). Confirm-gated; receipts only (who, when,
        WhatsApp, done/unverified/failed — never the message text).
        D-06 narrow re-ruling: SO-3.1 still blocks automated
        WhatsApp sending; this route — VYUK prepares, the owner
        sends — is outside the block's scope, because nothing is
        automated."""
        try:
            from . import social_send as _social_send

            _social_send.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] social_send module failed to register: {e}")
            _record_pack_failure("_register_social_send_tools",
                                 'social_send', e)

    def _register_click_anywhere_tools(self):
        """Registers the click-anywhere pack (core/click_anywhere_tool.py,
        Batch 1, Plan 9): one tool, click_anywhere — click, type,
        press, navigate, read, or observe anywhere, in a browser
        (extension, then CDP, then sight+click) or a desktop app
        (UIA + sight+click as the primary route). The module owns
        its catalogue via register_all (same pattern as the other
        packs). The registry record carries playbook_opt_out: True
        (M-24 — its steps are never replayed or lessoned)."""
        try:
            from . import click_anywhere_tool as _click_anywhere

            _click_anywhere.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] click_anywhere module failed to register: {e}")
            _record_pack_failure("_register_click_anywhere_tools",
                                 'click_anywhere_tool', e)

    def _register_scheduled_agents_tools(self):
        """Registers the scheduled_agents pack (core/scheduled_agents.py,
        GAPS-8 — BLOCKS #7): one tool, scheduled_agents — the
        OperatorManifest + OperativeAgent layer over the existing
        APScheduler engine, plus the morning digest (7am,
        Asia/Calcutta, setup-only, reads + speaks, no effectful
        action). The module owns its catalogue via register_all and
        registers the scheduler handler there too, so importing the
        tool pack wires the handler in the same step (same pattern
        as the other packs). setup_digest / cancel / run_now are
        confirm-gated; list / status / digest are free reads."""
        try:
            from . import scheduled_agents as _scheduled_agents

            _scheduled_agents.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] scheduled_agents module failed to register: {e}")
            _record_pack_failure("_register_scheduled_agents_tools",
                                 'scheduled_agents', e)

    def _register_triage3_tools(self):
        """Registers the Triage Wire Build 3 tools
        (core/triage3_identity_redact_select.py): session_identity,
        diagnostics_preview, forgiving_select, creative_mark_image,
        study_plan, study_progress, study_exam_prep — the six
        remaining non-decision-gated WIRE leaves (bz76_identity /
        fl_redact / ac_select / cr_marking / st_workspace; the
        dv_doctor row is a Doctor-page surface, not a tool) as
        live tools. The module owns its catalogue via register_all;
        this method only wires it in (same pattern as the Study
        and triage2 packs). The four writers (marking, plan,
        progress, exam prep) are confirm-gated (write parity in
        core/funnel/tools_gate.py); the three pure tools are read-only.
        """
        try:
            from . import triage3_identity_redact_select as _triage3

            _triage3.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] triage3 module failed to register: {e}")
            _record_pack_failure("_register_triage3_tools", 'triage3_identity_redact_select', e)

    def _register_wave6_integration_tools(self):
        """Registers the Wave 6 integration tools (core/integration_harness_tools.py):
        (named _integration_ to avoid the pre-existing v5.61
        conversion-pass _register_wave6_tools, which registers
        a different, unrelated five-tool pack — a later
        definition of the same name would shadow this one.)
        extension_audit, extension_status, extension_register,
        extension_approve, extension_enable, extension_disable,
        extension_revoke (Build 1 harness); workflow_distil,
        workflow_build_plan, workflow_to_playbook_steps (Build 3
        WORK, proven experience only); miss_compound,
        miss_capability_map, miss_to_playbook_steps (Build 4
        MISS); mission_build_brief, mission_build_plan,
        mission_evidence_board, mission_journal_entry,
        mission_journal_append, mission_run_job (Build 5
        mission-control + audit surfaces). Build 2 AGENT has
        deliberately NO tool row: its caller is the session's
        async goal path (plan parameter on the text clients'
        run_goal), never a synchronous job-running tool. The
        module owns its catalogue via register_all; this method
        only wires it in (same pattern as triage4). Harness
        register/approve/enable/revoke + mission_run_job are
        confirm-gated in core/funnel/tools_gate.py; audit/status +
        all WORK/MISS/mission composition tools are read-only;
        extension_disable + mission_journal_append are
        free-by-design app-state (own store files only).
        """
        try:
            from . import integration_harness_tools as _wave6

            _wave6.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] wave6 module failed to register: {e}")
            _record_pack_failure("_register_wave6_integration_tools", 'integration_harness_tools', e)

