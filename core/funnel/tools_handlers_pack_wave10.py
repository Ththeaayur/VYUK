"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))


class Wave10PacksMixin:
    def _register_release_w10_tools(self):
        """Registers the Wave 10 Build 1 tools
        (core/release_spine_tools.py): release_describe
        (read-only: one descriptor tool with a kind
        parameter — toolbox indirection, because the
        fitted Gemini declaration budget stood at
        239,624 B on stage after Wave 9 with only about
        376 B of headroom, so separate tools do not
        fit; kind "route" describes caller-supplied
        install routes, each stating what it does and
        does not do — nothing compiled, nothing
        installed, no store listing claimed to exist;
        kind "verify" checks the SHAPE of a
        caller-supplied manifest summary plus SHA-256
        — version supplied, manifest present, hash
        present and well-formed, data baseline stated —
        and never verifies a signature, never claims a
        release is signed or authentic; kind "train"
        returns caller-supplied stage gates in order,
        verdicts as supplied, running no gate and
        passing no gate), and release_record
        (confirm-gated, output_path in
        _WRITE_DESTINATION_ARGS: re-validates the
        release descriptor itself and writes only the
        release record to a caller-chosen .json file
        in an already-existing folder — recording
        releases nothing, signs nothing, compiles
        nothing, publishes nothing, and decides
        nothing; ruling 34 stays open, signing stays
        deferred, and the feed still has no host).
        The module owns its catalogue via register_all;
        this method only wires it in (same pattern as
        grad). No signing, installer, network, or
        feed action is wired here; any such input
        refuses whole in the module.
        """
        try:
            from .. import release_spine_tools as _release  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _release.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] release_spine module failed to register: {e}")
            _record_pack_failure("_register_release_w10_tools", 'release_spine_tools', e)

    def _register_trust_w10_tools(self):
        """Registers the Wave 10 Build 2 tools
        (core/trust_spine_tools.py): trust_describe
        (read-only: one descriptor tool with a kind
        parameter — toolbox indirection, because the
        fitted Gemini declaration budget is already over
        its 240,000 B hard cap after Wave 10 Build 1
        (240,286 B), so separate tools would only add
        to the breach and no schema is gutted here to
        chase bytes; kind "trust" returns the
        trust-centre record from caller-supplied trust
        facts, never a legal or privacy-policy claim,
        batch caveat riding with it; kind
        "transparency" returns the AI-transparency
        record from caller-supplied notes, never a
        capability claim beyond them; kind "help"
        returns the help/diagnostic-bundle record, sent
        False, explicit-send-only stated, never sent
        here; kind "issues" returns the known-issues
        record, supplied issues with status as
        supplied, never a live feed, never invented;
        kind "runbook" returns the support-runbook
        record in the order given; kind "changelog"
        returns the plain-language changelog record,
        shipped False; kind "audience" returns the
        first-audience record as supplied, never a
        market claim), and trust_record (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS:
        re-validates the trust descriptor itself and
        writes only the trust record to a caller-chosen
        .json file in an already-existing folder —
        recording sends nothing, publishes nothing,
        claims no compliance or conformance, and ships
        no version; ruling 34 stays open). The module
        owns its catalogue via register_all; this method
        only wires it in (same pattern as release_spine).
        No send, publish, feed, payment, legal, or
        conformance action is wired here; any such
        input refuses whole in the module.
        """
        try:
            from .. import trust_spine_tools as _trust  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _trust.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] trust_spine module failed to register: {e}")
            _record_pack_failure("_register_trust_w10_tools", 'trust_spine_tools', e)

    def _register_firstrun_w10_tools(self):
        """Registers the Wave 10 Build 3 tools
        (core/firstrun_tools.py): firstrun_describe
        (read-only: one descriptor tool with a kind
        parameter — toolbox indirection, because the
        fitted Gemini declaration budget is already over
        its 240,000 B hard cap after Wave 10 Build 1
        (240,286 B, grown by Build 2 to 241,046 B), so
        separate tools would only add to the breach and
        no schema is gutted here to chase bytes; kind
        "exit" returns the clean-exit record from
        caller-supplied items, exited False, performed
        False, never performing an exit, killing a
        process, or deleting user data; kind
        "first_command" returns the first-command guide
        record from a caller-supplied command plus
        plain-language steps meant to fit in about three
        minutes, executed False, never executing the
        command itself; kind "nosetup" returns the
        no-setup-mode record from caller-supplied
        capabilities only, a capability needing setup, a
        key, or an install refusing whole; kind
        "playground" returns the fake-data playground
        record from caller-supplied labelled samples, or
        the fixed clearly-labelled examples when none are
        supplied, fake True throughout, never real user
        data, never sent, never presented as real; kind
        "fix" returns the fix-my-setup checklist record
        from caller-supplied symptoms plus ordered checks
        with fix instructions, applied False, performing
        none of them, changing no setting, installing
        nothing), and firstrun_record (confirm-gated,
        output_path in _WRITE_DESTINATION_ARGS:
        re-validates the first-run descriptor itself and
        writes only the first-run record to a
        caller-chosen .json file in an already-existing
        folder — recording exits nothing, runs nothing,
        fixes nothing, installs nothing, and applies
        nothing; ruling 34 stays open, and no
        conformance is claimed). The module owns its
        catalogue via register_all; this method only
        wires it in (same pattern as trust_spine). No
        exit, execution, repair, install, send, payment,
        legal, or conformance action is wired here; any
        such input refuses whole in the module.
        """
        try:
            from .. import firstrun_tools as _firstrun  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _firstrun.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] firstrun module failed to register: {e}")
            _record_pack_failure("_register_firstrun_w10_tools", 'firstrun_tools', e)

    def _register_accessconf_w10_tools(self):
        """Registers the Wave 10 Build 4 tools
        (core/accessibility_conformance_tools.py): accessconf_describe
        (read-only: one descriptor tool with a kind
        parameter — toolbox indirection, because the
        fitted Gemini declaration budget is already over
        its 240,000 B hard cap after Wave 10 Build 1
        (240,286 B, grown by Build 2 to 241,046 B and by
        Build 3 to 241,768 B), so separate tools would
        only add to the breach and no schema is gutted
        here to chase bytes; kind "ledger" returns the
        evidence-ledger record from caller-supplied
        evidence items split into evidenced and
        not-evidenced lists — evidenced ONLY when
        evidence is non-empty AND outcome is supplied,
        anything else not evidenced, never passed or
        failed by inference; kind "statement" returns
        the conformance-statement record from a
        caller-supplied scope plus the same items, with
        evidenced and unevidenced checks listed
        separately and anything unevidenced explicitly
        NOT claimed; neither kind makes a blanket
        product claim, performs an audit, runs a live
        test, or reads a live screen), and
        accessconf_record (confirm-gated, output_path
        in _WRITE_DESTINATION_ARGS: re-validates the
        evidence descriptor itself and writes only the
        evidence record to a caller-chosen .json file
        in an already-existing folder — recording
        claims no conformance, performs no audit, runs
        no live test, and reads no live screen; ruling
        34 stays open). The module owns its catalogue
        via register_all; this method only wires it in
        (same pattern as trust_spine). No conformance,
        audit, live-test, capture, send, payment, or
        legal action is wired here; any such input
        refuses whole in the module.
        """
        try:
            from .. import accessibility_conformance_tools as _accessconf  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _accessconf.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] accessibility_conformance module failed to register: {e}")
            _record_pack_failure("_register_accessconf_w10_tools", 'accessibility_conformance_tools', e)

    def _register_triage4_tools(self):
        """Registers the Triage Wire Build 4 tools
        (core/decision_leaves_t4.py): finance_playbook_add,
        finance_playbook_list, finance_playbook_vote,
        finance_playbook_delete, finance_playbook_curate,
        social_occasion_add, social_occasion_upcoming,
        social_promise_add, social_promise_list,
        social_promise_complete, social_gift_add, social_debt_add,
        social_debt_settle, social_balances, social_stay_in_touch,
        social_nudge_check, social_nudge_snooze,
        social_nudge_pause, social_entry_delete — the two
        decision-gated WIRE leaves (fl_playbook_v574 /
        social_occasions_ledger) as live tools. Decisions, recorded in
        the coordinator docstring: the loop's single playbook
        store is J9, so the finance playbook is a finance-tool
        playbook only and is never consulted by
        planner.run_goal; the social ledger is the interim
        single store for occasions/promises/gifts/debts until
        the Wave 6 vault arrives (migration owed then). The
        module owns its catalogue via register_all; this
        method only wires it in (same pattern as triage3).
        The two deletions are confirm-gated in
        core/funnel/tools_gate.py; the five pure tools are read-only
        and the rest are free-by-design app-state (writes
        confined to the app's own store files, no message
        sent, no memory-DB or vault write).
        """
        try:
            from .. import decision_leaves_t4 as _triage4  # Phase 3 F3: file now in core/funnel/; import climbs one more level

            _triage4.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] triage4 module failed to register: {e}")
            _record_pack_failure("_register_triage4_tools", 'decision_leaves_t4', e)

    def _register_conv_w45_tools(self):
        """Registers the conversion-pass W4/W5 follow-up tools.

        Stage B (Wave 4): curl_cffi_read + replay_programme,
        declarations copied verbatim from w4_wiring
        (minus the non-declaration _fitted_cost_bytes /
        _closing_steps keys). Stage C (Wave 5): capture_route,
        occluded_capture, eyes_fusion, declarations measured
        first (see the stage comment on the registrations).
        """
        # Stage B — Wave 4 tools.
        self.register(
            name="curl_cffi_read",
            description="Read an HTTPS page through the curl_cffi browser-fingerprint lane, using only cookies from her own Playwright browser context. Refused while tainted; never reads a cookie file.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTPS URL to read"},
                    "impersonate": {"type": "string", "enum": ["chrome", "safari", "edge", "firefox"], "description": "Browser fingerprint to impersonate"},
                    "timeout": {"type": "number", "description": "Timeout in seconds (default 30)"},
                },
                "required": ["url"],
            },
            handler=self._tool_curl_cffi_read,
        )
        self.register(
            name="replay_programme",
            description="Replay a recorded programme of her own successful tool calls through the funnel, in order. Each step is re-validated and re-gated; a failed step halts the replay. Programmes carrying password or CAPTCHA arguments are refused.",
            parameters={
                "type": "object",
                "properties": {
                    "programme": {"type": "string", "description": "Programme as JSON text: {\"steps\": [{\"tool\": name, \"args\": {...}}]}"},
                },
                "required": ["programme"],
            },
            handler=self._tool_replay_programme,
        )
        # Stage C — Wave 5 tools (declarations measured first:
        # capture_route 215 B, occluded_capture 400 B,
        # eyes_fusion 257 B, isolated fitted deltas).
        self.register(
            name="capture_route",
            description="Report VYUK's screen-capture route: the ladder rungs, the selected fallback rung, and that the bakeoff winner is undecided. Takes no capture and claims no backend grabs.",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._tool_capture_route,
        )
        self.register(
            name="occluded_capture",
            description="Attempt an occluded-window capture of a named window. Refused without per-session approval; with approval but no WGC engine wired it returns engine-unavailable, never a fabricated frame.",
            parameters={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string", "description": "Title of the window to capture"},
                },
                "required": ["window_title"],
            },
            handler=self._tool_occluded_capture,
        )
        self.register(
            name="eyes_fusion",
            description="Report the last grounding fusion and capture evidence already noted: fused-text source, element count, frame hash, settled state and evidence digest. Derived from UIA/vision evidence, not a new capture or a write.",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._tool_eyes_fusion,
        )
        return None

    def _register_wave6_tools(self):
        """Registers the Wave 6 follow-up tools (v5.61 wiring,
        registered in the conversion pass): knowledge_search,
        research, remember_person, remember_promise and
        overdue_promises. Each dispatches to the matching
        W6_TOOL_HANDLERS function in core/w6_wiring.py; the
        fitted cost of all five was measured before
        registering (see the conversion-pass handoff: 2,433 B
        fitted, 7,771 B headroom left under the 240,000 B cap).
        """
        self.register(
            name="knowledge_search",
            description="Search VYUK's local knowledge store (FTS5 text search with LIKE fallback, plus a semantic lane when the embedding model is present; no web fetch) and return matching passages with citations: doc_id, title, source and licence for each hit. Use when the user asks what VYUK's stored documents say.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search the stored documents for"},
                    "limit": {"type": "integer", "description": "Maximum hits to return (default 10, clamped to 50)"},
                },
                "required": ["query"],
            },
            handler=self._tool_knowledge_search,
        )
        self.register(
            name="semantic_search_knowledge",
            description="Search VYUK's local knowledge store by meaning, not words: embeds the query with the on-device MiniLM model and returns the semantically nearest stored passages (cosine order) with citations. Catches what FTS5 misses (synonyms, paraphrase). Honest when the model or the vectors are absent — reports unavailable instead of a fabricated ranking. Use when the user asks for something by meaning rather than exact wording.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to match by meaning against the stored documents"},
                    "limit": {"type": "integer", "description": "Maximum hits to return (default 10, clamped to 50)"},
                },
                "required": ["query"],
            },
            handler=self._tool_semantic_search_knowledge,
        )
        self.register(
            name="embed_knowledge",
            description="Index the knowledge store for semantic search: embeds every stored document that has no vector yet with the on-device MiniLM model and writes the vectors to the embeddings table. Bounded work per call (default 50 documents). Reports honestly when the model is absent — nothing is fabricated. Use after adding documents, before semantic search.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum documents to embed in this call (default 50, clamped to 200)"},
                },
                "required": [],
            },
            handler=self._tool_embed_knowledge,
        )
        self.register(
            name="knowledge_embedding_status",
            description="Report the vector index: whether the embedding model is present, how many stored documents have vectors, and whether semantic search can run. Read-only, local store only.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=self._tool_knowledge_embedding_status,
        )
        self.register(
            name="search_documents",
            description="Search my documents for X: semantic-first search over the files VYUK indexed from the folders you chose (index_files) — plain text, Office documents and PDFs. When the embedding model is present, matches by meaning come first (synonyms, paraphrase); keyword matches fill after. When the model is absent, keyword search runs and the answer says semantic search is unavailable — never a fabricated ranking. Say 'search my documents for X' or 'what do my documents say about X'. Read-only, local store only.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to match against the indexed documents"},
                    "limit": {"type": "integer", "description": "Maximum hits to return (default 10, clamped to 50)"},
                },
                "required": ["query"],
            },
            handler=self._tool_search_documents,
        )
        self.register(
            name="list_documents",
            description="What do you remember from my files: list the documents VYUK indexed from the folders you chose (index_files) — one row per file with its title, path and passage count. Say 'what do you remember from my files' or 'list my documents'. Read-only, local store only.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum files to return (default 10, clamped to 100)"},
                    "offset": {"type": "integer", "description": "Files to skip (default 0)"},
                },
                "required": [],
            },
            handler=self._tool_list_documents,
        )
        self.register(
            name="forget_documents",
            description="Forget a document or a whole folder of documents: removes its passages from VYUK's document memory. Forgetting a folder also drops it from the remembered index folders, so a later refresh cannot re-index it. Say 'forget that document' or 'forget my Documents folder'. Confirm-gated.",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Path of the file or folder to forget"},
                },
                "required": ["source"],
            },
            handler=self._tool_forget_documents,
        )
        self.register(
            name="research",
            description="Research metadata only, through a caller-supplied fetcher; the funnel supplies none, so a live funnel call reports honestly unavailable instead of sending a request. arXiv: metadata/digest only, PDF serve always refused. Crossref: lookup by DOI. OpenAlex: needs a caller-set key and daily cap. Every article carries its own licence gate; full text only when that article's licence permits. Peer-review requests are refused, and disclosure flags are surfaced, never auto-filled.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Research question or topic"},
                    "source": {"type": "string", "enum": ["arxiv", "crossref", "openalex"], "description": "Metadata source (default arxiv)"},
                    "doi": {"type": "string", "description": "DOI for a Crossref lookup (required when source is crossref)"},
                },
                "required": ["question"],
            },
            handler=self._tool_research,
        )
        self.register(
            name="deep_research",
            description="Run the A6 deep-research loop over VYUK's LOCAL stored documents only (KnowledgeStore FTS search, no web, no model generation): multi-round retrieve, tag, and extractive summary with citations drawn from the retrieval log alone. Use when the user asks what the stored documents say in depth. No stored match is reported plainly as no-findings, never dressed up as findings; a store failure reports honestly unavailable.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Research question or topic"},
                    "max_rounds": {"type": "integer", "description": "Loop rounds (default 3; the kernel caps at 3)"},
                },
                "required": ["question"],
            },
            handler=self._tool_deep_research,
        )
        self.register(
            name="remember_person",
            description="Store a person the user mentioned - name, relationship and a note - in VYUK's local people store, so it can be recalled later. Stores facts only; opinions about a person are never stored here and never promoted to facts.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The person's name"},
                    "relationship": {"type": "string", "description": "Relationship to the user, if stated"},
                    "note": {"type": "string", "description": "Anything else stated about this person"},
                },
                "required": ["name"],
            },
            handler=self._tool_remember_person,
        )
        self.register(
            name="remember_promise",
            description="Store a promise the user made to someone - who, what was promised, and when it is due - in VYUK's local promise store. Promises surface as overdue on the routine tick once their due date passes; they are never auto-deleted.",
            parameters={
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "Who the promise was made to"},
                    "promise_text": {"type": "string", "description": "What was promised"},
                    "due_date": {"type": "string", "description": "Due date, ISO YYYY-MM-DD, if stated"},
                },
                "required": ["person", "promise_text"],
            },
            handler=self._tool_remember_promise,
        )
        self.register(
            name="overdue_promises",
            description="List promises past their due date, oldest first, from VYUK's local promise store. Read-only: it lists, never deletes or changes anything. A promise without a due date is never overdue.",
            parameters={
                "type": "object",
                "properties": {
                    "today": {"type": "string", "description": "ISO date to judge overdue against (defaults to today)"},
                },
                "required": [],
            },
            handler=self._tool_overdue_promises,
        )
        self.register(
            name="personal_skills",
            description="Manage the user's own personal skills in Documents/VYUK Skills: list them, inspect one (its steps are shown as data, never run), admit a draft after its SKILL.md validates and its safety scan passes, or run one admitted skill's declared, VYUK-signed WASM module inside the contained runtime (confirmation required; anything undeclared, unsigned, or untrusted is refused and nothing executes). Admit and run ask for confirmation first; a blocked scan or invalid skill is refused, never admitted.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "inspect", "admit", "run"], "description": "list: all personal skills with their state; inspect: one skill's steps and scan class; admit: validate, scan, and admit a draft (confirmation required); run: run one admitted skill's declared, signed WASM module contained (confirmation required; inputs is a JSON list of integers)"},
                    "name": {"type": "string", "description": "Skill name (required for inspect, admit, and run)"},
                    "inputs": {"type": "string", "description": "JSON list of integers for run (the contained-runtime pilot ABI: the module's run takes i32 params and returns one i32)"},
                },
                "required": ["action"],
            },
            handler=self._tool_personal_skills,
        )
        self.register(
            name="health",
            description="Track health records: extract values from pasted record text, store a health observation you supply, list stored records as recorded, or get a desk reminder (held during quiet hours). She tracks, notices, reminds and organises only — she never diagnoses, never interprets, and never presents a score as a medical finding.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["extract", "record", "summary", "remind"], "description": "extract: values from pasted record text; record: store one observation you supply (confirmation required); summary: stored records as recorded; remind: desk reminder, held during quiet hours"},
                    "text": {"type": "string", "description": "Record text for extract (JSON or DATE | METRIC | VALUE | UNIT lines)"},
                    "date": {"type": "string", "description": "Date for record"},
                    "metric": {"type": "string", "description": "Metric name for record"},
                    "value": {"type": "number", "description": "Numeric value for record"},
                    "unit": {"type": "string", "description": "Unit for record"},
                },
                "required": ["action"],
            },
            handler=self._tool_health,
        )
        self.register(
            name="lifeadmin",
            description="Life admin and travel help: check a trip's status, prepare paperwork guidance, or assemble a trip plan. She prepares, reads and guides only — the human books; she never automates a booking, CAPTCHA, OTP or password entry.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["trip_status", "paperwork_guide", "trip_prepare"], "description": "trip_status: NTES-style status read-out; paperwork_guide: DigiLocker/form preparation guidance; trip_prepare: assemble a trip plan with link-outs"},
                    "query": {"type": "string", "description": "Trip, route or paperwork topic, e.g. a train number or document name"},
                },
                "required": ["action"],
            },
            handler=self._tool_lifeadmin,
        )
        self.register(
            name="business",
            description="Business admin checks: telemetry-OFF proof, trust-pack render (DPA-gated claims conditional), signed fleet-entitlement verification. Reads only; never sends, never captures, never books.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["telemetry_proof", "trust_pack", "entitlement_check"], "description": "telemetry_proof: live telemetry state plus BZ-1.2 admin-enforcement proof when a policy is supplied; trust_pack: rendered business trust pack; entitlement_check: verify a signed fleet-seat entitlement supplied as JSON"},
                    "query": {"type": "string", "description": "Admin policy JSON for telemetry_proof, or entitlement JSON for entitlement_check; unused for trust_pack"},
                },
                "required": ["action"],
            },
            handler=self._tool_business,
        )

