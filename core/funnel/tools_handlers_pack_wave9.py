"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))


class Wave9PacksMixin:
    def _register_firstopen_w9_tools(self):
        """Registers the first-open continuous choice tools
        (core/firstopen_tools.py): continuous_first_open
        (read-only: the plain-language first-open prompt
        descriptor — it chooses nothing and starts nothing),
        continuous_choice_status (read-only: inspect a
        caller-supplied choice record and state plainly
        whether continuous display is on and why),
        continuous_record_choice (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS: re-validates
        the choice itself and writes only the choice record
        to a caller-chosen .json file in an
        already-existing folder — writing the record
        chooses nothing for the user and starts nothing),
        and continuous_delete_data (confirm-gated, data_dir
        in _WRITE_DESTINATION_ARGS: deletes only the
        regular files inside a caller-chosen folder holding
        kept continuous data — per-feature delete only,
        never the PF-3 global rule, which is still open).
        The module owns its catalogue via register_all;
        this method only wires it in (same pattern as
        learnlast_w8). No continuous capture, screen memory,
        or clipboard history is wired here; the choice
        mechanism is the build, and actual continuous
        features are separate Wave 9 TENSION builds.
        """
        try:
            from .. import firstopen_tools as _firstopen  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _firstopen.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] firstopen_w9 module failed to register: {e}")
            _record_pack_failure("_register_firstopen_w9_tools", 'firstopen_tools', e)

    def _register_well_w9_tools(self):
        """Registers the Wave 9 Build 1 wellbeing tools
        (core/well_tools.py): well_break_plan
        (read-only: a break-plan descriptor from
        caller-supplied minutes — a suggestion, never a
        prescription, claiming no health benefit),
        well_reminder (read-only: a reminder descriptor
        from caller-supplied words — it schedules nothing,
        and the descriptor says so), well_winddown
        (read-only: a wind-down routine from caller-chosen
        steps, each from the fixed non-medical list),
        well_day_summary (read-only: totals of
        caller-supplied numbers only — nothing measured,
        no average, no score, no rating), and
        well_record_plan (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the plan
        itself and writes only the plan record to a
        caller-chosen .json file in an already-existing
        folder — recording schedules nothing, measures
        nothing, and prescribes nothing). The module owns
        its catalogue via register_all; this method only
        wires it in (same pattern as firstopen_w9). No
        diagnosis, no mood inference, no biometric input
        or output, and no prevention or treatment claim
        is wired here; any such input refuses whole in
        the module.
        """
        try:
            from .. import well_tools as _well  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _well.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] well module failed to register: {e}")
            _record_pack_failure("_register_well_w9_tools", 'well_tools', e)

    def _register_comms_w9_tools(self):
        """Registers the Wave 9 Build 2 communications tools
        (core/comms_tools.py): comms_draft_message
        (read-only: a message draft from caller-supplied
        recipient, body, and channel label — never
        addressed from an inferred contact, never sent),
        comms_check_recipient (read-only: a
        caller-supplied contact record only, name and
        channel as supplied, returning "supplied" or
        "missing" — never guessed, never merged, never an
        inferred relationship), comms_send_readiness
        (read-only: states honestly that the draft is NOT
        sent and names the two missing steps, a human
        confirmation and a real send pipeline, neither of
        which this build provides), and
        comms_record_draft (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the draft
        itself and writes only the draft record to a
        caller-chosen .json file in an already-existing
        folder — recording sends nothing). The module owns
        its catalogue via register_all; this method only
        wires it in (same pattern as well). No message
        is sent, no bulk message is built, and no send
        pipeline is invented here; any send-shaped input
        refuses whole in the module.
        """
        try:
            from .. import comms_tools as _comms  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _comms.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] comms module failed to register: {e}")
            _record_pack_failure("_register_comms_w9_tools", 'comms_tools', e)

    def _register_iotl_w9_tools(self):
        """Registers the Wave 9 Build 3 local IoT tools
        (core/iotl_tools.py): iot_local_device
        (read-only: a local-device descriptor from
        caller-supplied device name, local address label,
        and administered-network label — the address as
        a label only, never probed or resolved, the
        device never reached or controlled),
        iot_device_state (read-only: a device state
        summary from caller-supplied state words and
        timestamp — as supplied, never polled, never
        measured), iot_network_check (read-only: a
        caller-supplied network label checked as
        "local-administered as supplied" or "missing" —
        nothing scanned, nothing outside the supplied
        network reached), and iot_record_device
        (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the device
        itself and writes only the device record to a
        caller-chosen .json file in an already-existing
        folder — recording reaches nothing and controls
        nothing). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as comms). No device is reached,
        polled, or controlled, no hardware is moved, and
        no network is scanned here; any actuation, scan,
        external-network, or reach-claim input refuses
        whole in the module.
        """
        try:
            from .. import iotl_tools as _iotl  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _iotl.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] iotl module failed to register: {e}")
            _record_pack_failure("_register_iotl_w9_tools", 'iotl_tools', e)

    def _register_hardware_w9_tools(self):
        """Registers the Wave 9 Build 4 hardware-adjacent
        tools (core/hardware_tools.py):
        hardware_inventory (read-only: a
        hardware-inventory descriptor from a
        caller-supplied parts list, name + count as
        supplied, echoed back — nothing measured, weighed,
        or checked against a real bench, and no
        measurement field accepted beside a plain part
        count), hardware_plan (read-only: a hardware-plan
        descriptor from caller-supplied steps — every step
        that would move hardware must name its
        independent physical stop, and the plan describes
        only, it never moves, actuates, drives, prints,
        or cuts anything), hardware_safety (read-only:
        the safety descriptor stating in plain words what
        a plan here does NOT claim — no safety sizing, no
        engineering sign-off, no claim a design is safe
        or will hold, no load rating), and
        hardware_record_plan (confirm-gated, output_path
        in _WRITE_DESTINATION_ARGS: re-validates the plan
        itself, including the physical-stop rule, and
        writes only the plan record to a caller-chosen
        .json file in an already-existing folder —
        recording moves nothing and claims nothing). The
        module owns its catalogue via register_all; this
        method only wires it in (same pattern as
        iotl). Nothing here moves hardware, sizes
        anything, rates any load, or signs anything off,
        and no claim about any specific project, arm,
        bench, or bench test is wired here; any move step
        without a stop, or any safety claim, refuses
        whole in the module.
        """
        try:
            from .. import hardware_tools as _hardware  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _hardware.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] hardware module failed to register: {e}")
            _record_pack_failure("_register_hardware_w9_tools", 'hardware_tools', e)

    def _register_access_w9_tools(self):
        """Registers the Wave 9 Build 5 accessibility tools
        (core/accessibility_tools.py): access_text_clarity
        (read-only: a plain-language note about
        caller-supplied text — sentence count, word
        count, long-sentence count, fixed-list jargon
        words found — saying nothing about the reader
        and never producing a score),
        access_window_describe (read-only: an eyes-free
        layout description of a caller-supplied
        accessibility-tree snapshot, control names and
        roles as supplied, in the order supplied —
        nothing read from a live screen, and a snapshot
        flagged live or captured refuses whole),
        access_voice_first_routine (read-only: a routine
        of caller-chosen steps, each from the fixed list
        only — voice command, larger target, keyboard
        alternative, pause — an unknown step refusing
        whole), and access_record_preference
        (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the
        preference itself — plain-language label only, no
        disability or health label, no score, no inferred
        flag — and writes only the preference record to
        a caller-chosen .json file in an already-existing
        folder — recording labels a setting, never a
        person, and changes nothing). The module owns its
        catalogue via register_all; this method only
        wires it in (same pattern as hardware). No
        live capture, no inference about the user, no
        disability or health label, and no score is wired
        here; any such input refuses whole in the module.
        """
        try:
            from .. import accessibility_tools as _access  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _access.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] accessibility module failed to register: {e}")
            _record_pack_failure("_register_access_w9_tools", 'accessibility_tools', e)

    def _register_net_w9_tools(self):
        """Registers the Wave 9 Build 6 network tools
        (core/net_tools.py): net_network_descriptor
        (read-only: an own-network descriptor from a
        caller-supplied network label and a scope of
        exactly "own" or "administered" — a name the
        caller gave the network, never probed, never
        scanned, never measured, never resolved),
        net_network_status (read-only: a network-status
        descriptor from caller-supplied status words and
        a timestamp — the status as supplied, never
        polled live, never measured),
        net_readonly_check (read-only: "own-network as
        supplied", "missing", or "outside own network —
        refused" for a caller-supplied target label and
        scope — never resolved, never connected, never
        reaching outside the supplied network), and
        net_record_network (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the
        network itself and writes only the network record
        to a caller-chosen .json file in an
        already-existing folder — recording scans
        nothing, connects to nothing, reaches nothing).
        The module owns its catalogue via register_all;
        this method only wires it in (same pattern as
        accessibility). No scan, probe, ping, connect,
        discovery, or external-network action is wired
        here; any such input refuses whole in the module
        (or, in the read-only check, reports "outside own
        network — refused").
        """
        try:
            from .. import net_tools as _net  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _net.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] net module failed to register: {e}")
            _record_pack_failure("_register_net_w9_tools", 'net_tools', e)

    def _register_gaming_w9_tools(self):
        """Registers the Wave 9 Build 7 gaming tools
        (core/gaming_tools.py): game_session_describe
        (read-only: a game-session descriptor from a
        caller-supplied game title, a mode of exactly
        "single-player" or "co-op", and session notes —
        never a live game read), game_macro_describe
        (read-only: a macro descriptor from
        caller-supplied step labels and a mode of exactly
        "single-player" or "co-op" — a competitive mode,
        a banned gaming-wall phrase, or an
        automation-shaped step refuses whole, and the
        macro is never executed), game_media_note
        (read-only: a drive-only note for a
        caller-supplied tool — NOT bundled, integrated,
        or shipped, driven by the user themselves; the
        only form here for OBS GPL-2.0, FFmpeg GPL
        builds with libx264, and LosslessCut
        GPL-2.0-only), and game_record_gaming
        (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the gaming
        descriptor itself and writes only the gaming
        record to a caller-chosen .json file in an
        already-existing folder — recording reads no
        game, controls no game, automates no input, and
        bundles no tool). The module owns its catalogue
        via register_all; this method only wires it in
        (same pattern as net). No memory-read,
        injection, cheat, anti-cheat-evasion, or
        competitive-automation action is wired here; any
        such input refuses whole in the module.
        """
        try:
            from .. import gaming_tools as _gaming  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _gaming.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] gaming module failed to register: {e}")
            _record_pack_failure("_register_gaming_w9_tools", 'gaming_tools', e)

    def _register_pilot_w9_tools(self):
        """Registers the Wave 9 Build 8 pilot tools
        (core/pilot_tools.py): pilot_bench
        (read-only: a project-bench descriptor from a
        caller-supplied project name plus bench notes —
        P15-7 lab notebook, record form only, nothing
        probed, photographed, or measured),
        pilot_build_steps (read-only: a build-step
        descriptor from caller-supplied step labels plus
        an assumptions note — P15-10 blueprint plan
        record, P16-10 worked steps; an action-shaped
        step refuses whole, and the steps are never
        run), pilot_study_session (read-only: a
        study-session descriptor from a caller-supplied
        subject, materials, and questions — P16-6 quiet
        question bank, record form; every material
        carries its source as supplied, and a material
        without a source, or flagged protected, refuses
        the whole session; nothing is summarised,
        generated, or read live), pilot_revision_plan
        (read-only: a revision-plan descriptor from
        caller-supplied topics in order — P16-2 study
        plan that bends, plan record only, P16-12
        concept maps, topics record only; a plan
        schedules nothing and sets no reminder),
        pilot_dev_workflow (read-only: a dev-workflow
        descriptor from a caller-supplied repo label
        plus workflow step labels — P17-1 terminal
        dry-run, plan record, P17-4 policy as code,
        rule labels as steps; never run, never deployed,
        never signed, the repo never read), and
        pilot_review_note (read-only: a code-review-note
        descriptor from caller-supplied file:line cites
        plus notes — P17-11 stack trace to cause,
        cite-and-note form, P17-12 pre-push guardian,
        check notes only, P17-14 diff storyteller,
        caller-supplied summary notes plus cites; no
        cause stated, no check run, no summary
        generated, nothing approved or merged), and
        pilot_record_pilot (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the pilot
        descriptor itself and writes only the pilot
        record to a caller-chosen .json file in an
        already-existing folder — recording runs nothing,
        reads nothing live, deploys nothing, and signs
        nothing). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as gaming). No execute, run, deploy,
        install, promote, push, merge, sign, or apply
        action is wired here; any such input refuses
        whole in the module.
        """
        try:
            from .. import pilot_tools as _pilot  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _pilot.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] pilot module failed to register: {e}")
            _record_pack_failure("_register_pilot_w9_tools", 'pilot_tools', e)

    def _register_travel_w9_tools(self):
        """Registers the Wave 9 Build 9 tools
        (core/travel_tools.py): travel_trip
        (read-only: a trip descriptor from caller-supplied
        legs only — P19-14 trip pack, P19-19 packing
        checklist, P19-18 parking note, P19-20
        accessibility note as supplied, never inferred;
        no legs means NO trip, NOT_COMPUTED; nothing
        booked, routed, or paid), travel_door_to_door
        (read-only: a door-to-door plan descriptor from
        caller-supplied legs in order — P19-12,
        non-commercial-routing form; no commercial
        routing used, no route computed by a routing
        service), travel_transport (read-only: a
        transport-handler descriptor from a
        caller-supplied mode label — P19 chauffeur
        position, handler yes, literal driver no; a
        driving label returns the driving-mode note
        only, a safe-arrival label the safe-arrival
        note only, NOT an emergency-services
        replacement), travel_loadout (read-only: a
        loadout descriptor from a caller-supplied name
        plus capability and rule labels — P20-1,
        non-hardware form; applied False, restored
        False), travel_scene (read-only: a hologram or
        scene descriptor from caller-supplied scene
        labels — P20-6 Pepper's ghost, illusion not true
        hologram; hardware False, rendered False),
        travel_spatial (read-only: a spatial-desktop
        descriptor from caller-supplied zone labels —
        P20-7 layout record, P20-9 augmented labels,
        confidence refused; read_live False),
        travel_sonify (read-only: a sonification cue-map
        descriptor from caller-supplied state and cue
        labels — P20-10, non-hardware form; played
        False, audio False), travel_recipe (read-only:
        a shareable recipe descriptor from
        caller-supplied steps plus source as supplied —
        P23-25; a sourceless step refuses whole, a
        personal step refuses whole), travel_scoreboard
        (read-only: an open scoreboard descriptor from
        caller-supplied graduation-evidence entries
        only — P23-26; any numeric score or rate field
        refuses whole), and travel_record (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS:
        re-validates the travel descriptor itself and
        writes only the travel record to a caller-chosen
        .json file in an already-existing folder —
        recording books nothing, pays nothing, routes
        nothing, drives nothing, renders nothing, and
        moves nothing). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as pilot). No booking, purchase,
        payment, send, vehicle-control, commercial-routing,
        safety-sizing, or fake-data action is wired here;
        any such input refuses whole in the module.
        """
        try:
            from .. import travel_tools as _travel  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _travel.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] travel module failed to register: {e}")
            _record_pack_failure("_register_travel_w9_tools", 'travel_tools', e)

    def _register_grad_w9_tools(self):
        """Registers the Wave 9 Build 10 tools
        (core/grad_tools.py): grad_describe (read-only:
        one descriptor tool with a kind parameter —
        toolbox indirection, because the fitted Gemini
        declaration budget stood at 239,334 B after Build
        9 with about 666 B of headroom, so five separate
        tools do not fit; kind "gate" checks a
        caller-supplied choice record through the
        first-open gate and states the recorded retention
        rule and the indicator requirement with the
        answer, enabled only on the user's own explicit
        choice for exactly continuous-display; kind
        "retention" states the plain-language kept-notes
        rule, 7-day builder-chosen default NOT ruled by
        the owner unless the caller supplies days; kind
        "indicator" states the on-screen sign requirement
        in plain words; kind "ledger" returns a
        graduation ledger entry — evidence plus
        buildable form plus still-owed, never a claim
        the item is built, shipped, or field-proven),
        and grad_record (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the
        graduation ledger entry itself and writes only
        the graduation record to a caller-chosen .json
        file in an already-existing folder — recording
        captures nothing, grants nothing, chooses
        nothing for any user, and decides nothing;
        PF-3 stays open). The module owns its catalogue
        via register_all; this method only wires it in
        (same pattern as travel). No capture,
        watcher, screen, clipboard, keystroke, or sensor
        action is wired here; any such input refuses
        whole in the module.
        """
        try:
            from .. import grad_tools as _grad  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _grad.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] grad module failed to register: {e}")
            _record_pack_failure("_register_grad_w9_tools", 'grad_tools', e)

