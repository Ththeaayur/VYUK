"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))


class Wave78PacksMixin:
    def _register_sandbox_w7_tools(self):
        """Registers the Wave 7 Build 1 sandbox tools
        (core/sandbox_tools.py): sandbox_run (confirm-gated,
        sandbox_dir in _WRITE_DESTINATION_ARGS) and sandbox_list
        (read-only). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as wave6/triage4). sandbox_run creates the
        sandbox root only after the funnel's confirm gate
        has passed; sandbox_list never creates a directory.
        Promotion stays request-only in the module — no
        tool here promotes, applies, or deploys anything.
        """
        try:
            from .. import sandbox_tools as _sandbox  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _sandbox.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] sandbox_w7 module failed to register: {e}")
            _record_pack_failure("_register_sandbox_w7_tools", 'sandbox_tools', e)

    def _register_undo_w7_tools(self):
        """Registers the Wave 7 Build 2 undo tools
        (core/undo_tools.py): undo_cannot_list and
        undo_status (read-only) and undo_last_change
        (confirm-gated). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as sandbox_w7). The undo execution goes
        through the shared gate, never a second gate owner,
        and restores only recorded before-states through
        registered restorers.
        """
        try:
            from .. import undo_tools as _undo  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _undo.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] undo_w7 module failed to register: {e}")
            _record_pack_failure("_register_undo_w7_tools", 'undo_tools', e)

    def _register_sim_w7_tools(self):
        """Registers the Wave 7 Build 3 SIM tools
        (core/sim_tools.py): sim_predict_plan (read-only:
        a plan-effect prediction on a throwaway sandbox copy,
        nothing kept, nothing promoted, plan never executed)
        and sim_predict_report (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: it re-runs the prediction
        itself and writes only the prediction report to a
        caller-chosen .json file in an already-existing
        folder). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as sandbox_w7/undo_w7). No Monte Carlo,
        counterfactual, or digital-twin form is wired here;
        those stay STUDY. No tool here plans, compiles,
        promotes, applies, or deploys anything.
        """
        try:
            from .. import sim_tools as _sim  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _sim.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] sim_w7 module failed to register: {e}")
            _record_pack_failure("_register_sim_w7_tools", 'sim_tools', e)

    def _register_proposals_w7_tools(self):
        """Registers the Wave 7 Build 4 proposal tools
        (core/proposal_tools.py): improvement_propose
        (read-only: validates a caller-supplied diff + guard +
        evidence proposal and dry-runs it in a throwaway copy
        that is discarded, nothing applied, promoted, or
        deployed) and improvement_report (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS: it re-validates
        and re-dry-runs the proposal itself and writes only
        the proposal record to a caller-chosen .json file in
        an already-existing folder). The module owns its
        catalogue via register_all; this method only wires it
        in (same pattern as sim_w7). No M4 itself is wired
        here; no tool here plans, compiles, promotes,
        applies, or deploys anything.
        """
        try:
            from .. import proposal_tools as _prop  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _prop.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] proposals_w7 module failed to register: {e}")
            _record_pack_failure("_register_proposals_w7_tools", 'proposal_tools', e)

    def _register_multiverse_w7_tools(self):
        """Registers the Wave 7 Build 5 multiverse tools
        (core/multiverse_tools.py): multiverse_plan
        (read-only: plans a caller-supplied multiverse, each
        branch a Build 4 proposal, refused whole on any bad
        branch) and multiverse_compile (read-only: compiles
        a caller-chosen branch only, never auto-chooses,
        routine + dry-run report, fallback never executes)
        and multiverse_compile_report (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS: it
        re-compiles the chosen branch itself and writes
        only the routine + dry-run report to a caller-chosen
        .json file in an already-existing folder). The module
        owns its catalogue via register_all; this method
        only wires it in (same pattern as proposals_w7).
        No M4 itself is wired here; no tool here chooses,
        promotes, applies, or deploys anything.
        """
        try:
            from .. import multiverse_tools as _mv  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _mv.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] multiverse_planner module failed to register: {e}")
            _record_pack_failure("_register_multiverse_w7_tools", 'multiverse_tools', e)

    def _register_antic_w8_tools(self):
        """Registers the Wave 8 Build 1 anticipation-grant tools
        (core/anticipation_grant_tools.py): antic_suggest_cards
        (read-only: suggestion cards from caller-supplied
        context, gated on a caller-supplied grant record —
        zero cards with a stated reason without a granted
        suggestion-cards grant) and antic_grant_status
        (read-only: inspects a grant record, grants nothing)
        and antic_cards_report (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS: it re-runs
        the gate itself and writes only the card record to a
        caller-chosen .json file in an already-existing
        folder). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as multiverse_planner). No capture, no watcher,
        no pre-warming is wired here; the grant itself is
        NOT granted by any tool here.
        """
        try:
            from .. import anticipation_grant_tools as _antic  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _antic.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] antic_w8 module failed to register: {e}")
            _record_pack_failure("_register_antic_w8_tools", 'anticipation_grant_tools', e)

    def _register_release_w8_tools(self):
        """Registers the Wave 8 Build 2 Track L tools
        (core/release_checklist_tools.py): release_checklist
        (read-only: evaluates the release checklist against
        caller-supplied evidence — one failed required check
        blocks the verdict, and signing, feed hosting, and
        installer compilation appear as DEFERRED, never as
        passed or built) and installer_validate (read-only:
        structure validation only — required files, version
        form and consistency, no signed claim, no live-feed
        claim; it compiles nothing, signs nothing, hosts
        nothing) and release_checklist_report (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS: it
        re-evaluates the checklist itself and writes only the
        report to a caller-chosen .json file in an
        already-existing folder). The module owns its
        catalogue via register_all; this method only wires it
        in (same pattern as antic_w8). No installer is
        compiled here, nothing is signed, no feed is hosted,
        and no CC-1 failure is granted here.
        """
        try:
            from .. import release_checklist_tools as _release  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _release.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] release_w8 module failed to register: {e}")
            _record_pack_failure("_register_release_w8_tools", 'release_checklist_tools', e)

    def _register_phase5_w8_tools(self):
        """Registers the Wave 8 Build 3 Phase 5 tools
        (core/phase5_tools.py): phase5_interface_describe
        (read-only: surface descriptor inside the frozen core —
        renumbering, new surfaces, faces, and fake data refused
        whole), ent_shelf_list (read-only: caller-supplied
        entertainment catalogue, licence-gated, no asset bundled
        / played / generated, vetoed feel refused), port_roundtrip
        (confirm-gated, output_path in _WRITE_DESTINATION_ARGS:
        single-device record-set export + re-read + byte-for-byte
        proof, never automatic, never a second device or sync),
        plat_tools_record (read-only: lifecycle / rollout /
        governance record, enforced=False), fin_plan_record
        (read-only: money plan/record over caller-supplied
        amounts, transaction-shaped input refused whole, no
        transaction / payment / account / advice ever), and
        phase5_report (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-runs one cluster operation
        and writes only its report). The module owns its
        catalogue via register_all; this method only wires it in
        (same pattern as release_w8).
        """
        try:
            from .. import phase5_tools as _phase5  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _phase5.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] phase5_picks module failed to register: {e}")
            _record_pack_failure("_register_phase5_w8_tools", 'phase5_tools', e)

    def _register_feel_w8_tools(self):
        """Registers the Wave 8 Build 4 feel tools
        (core/feel_tools.py): feel_validate (read-only:
        a whole feel input against the §7 rules — colour,
        honesty, vetoes, particle budget, sound spec, pack
        ledger, HUD language, font status; no render, no
        listening test claimed) and feel_orb_map (read-only:
        a caller-supplied measured level to orb parameters;
        mapping only, orb licence sighting stays OWED) and
        feel_report (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the feel
        input itself and writes only the report to a
        caller-chosen .json file in an already-existing
        folder). The module owns its catalogue via
        register_all; this method only wires it in (same
        pattern as phase5_picks). No vetoed feel, no face, no
        fake data, no font asset, and no sound pack bundled
        is wired here; a feel report is not a feel approval.
        """
        try:
            from .. import feel_tools as _feel  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _feel.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] feel module failed to register: {e}")
            _record_pack_failure("_register_feel_w8_tools", 'feel_tools', e)

    def _register_learnlast_w8_tools(self):
        """Registers the Wave 8 Build 5 learning-last tools
        (core/learning_last_tools.py): learnlast_spec (read-only:
        a learning-last spec from caller-supplied proven jobs
        plus an exact diff and named regression guards — the
        Wave 6 WORK proven-only rule refuses unproven or mixed
        input whole, and the Wave 7 proposal builder
        re-validates the diff; a spec changes nothing and is
        never applied by this tool) and learnlast_report
        (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-builds the spec itself
        and writes only the spec record to a caller-chosen
        .json file in an already-existing folder). The module
        owns its catalogue via register_all; this method only
        wires it in (same pattern as feel). No
        auto-collection, no auto-execution, no
        self-deployment, no M4 itself is wired here; a spec
        report is not an application.
        """
        try:
            from .. import learning_last_tools as _learnlast  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _learnlast.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] learnlast_w8 module failed to register: {e}")
            _record_pack_failure("_register_learnlast_w8_tools", 'learning_last_tools', e)

