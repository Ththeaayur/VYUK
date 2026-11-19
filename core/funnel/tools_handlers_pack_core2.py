"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""

from .pack_registration import _record_pack_failure  # Phase 3 F2b: pack_registration moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))


class MindWorldPacksMixin:
    def _register_mind_tools(self):
        """Registers the Mind pack (core/brain/planner.py): plans + routines."""
        from ..brain import planner  # Phase 3 B2-remainder: planner moved to core/brain/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        self.register(
            name="create_plan",
            description=(
                "Create a multi-step plan for a bigger goal (e.g. 'get ready for my trip'). "
                "Break the goal into concrete steps first, then pass them here. Each step is "
                "either a plain text string, or an object with 'text' plus optional 'tool' and "
                "'args' naming the tool that can carry it out. Just creating a plan runs "
                "nothing — steps are tracked, not executed. "
                "Example step with args: {'text': 'Set the volume to 40%', 'tool': 'set_volume_level', 'args': {'level': 40}}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The overall goal of the plan"},
                    "steps": {
                        "type": "array",
                        "description": "Ordered list of steps: strings, or objects {text, tool (optional), args (optional object)}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "What this step does"},
                                "tool": {"type": "string", "description": "Optional tool name that can perform this step"},
                                "args": {"type": "object", "description": "Optional arguments for that tool"},
                            },
                            "required": [],
                        },
                    },
                },
                "required": ["goal", "steps"],
            },
            handler=planner.create_plan,
        )
        self.register(
            name="list_plans",
            description="List all saved plans with their progress (steps done / total). Make a plan with create_plan; see one plan's steps with get_plan; tick steps off with update_plan_step.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=planner.list_plans,
        )
        self.register(
            name="get_plan",
            description="Show one plan in full: every step with its status (pending/done/skipped). Step numbers start at 1. Plans are listed by list_plans and created by create_plan.",
            parameters={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "The plan id, e.g. 'plan-1' (see list_plans)"}
                },
                "required": ["plan_id"],
            },
            handler=planner.get_plan,
        )
        self.register(
            name="update_plan_step",
            description="Mark one step of a plan as completed or skipped as work progresses. Plans are created with create_plan and listed with list_plans. For the whole plan with every step's status, get_plan.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["complete", "skip"],
                        "description": "'complete' when the step is done, 'skip' when it was deliberately not done",
                    },
                    "plan_id": {"type": "string", "description": "The plan id, e.g. 'plan-1'"},
                    "step_index": {"type": "integer", "description": "One-based step number (see get_plan)"},
                },
                "required": ["action", "plan_id", "step_index"],
            },
            handler=planner.update_plan_step,
        )
        self.register(
            name="delete_plan",
            description="Delete a plan entirely. This cannot be undone, so check list_plans first if you are unsure which plan it is. Routines are a separate store — delete_routine removes those.",
            parameters={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "The plan id, e.g. 'plan-1'"}
                },
                "required": ["plan_id"],
            },
            handler=planner.delete_plan,
        )
        # Round-3 fix #4 (T12): run a saved plan. The plan must
        # already exist — list_plans shows the saved plans and
        # their ids; this tool takes only the id, never a goal
        # or steps, so it can never run a model-invented plan.
        # Confirm-gated (core/funnel/tool_classification.py):
        # every run asks the owner first, and no always-allow
        # covers it. The call approves STARTING the plan, never
        # its steps: each step keeps its own confirm/allowlist
        # gate, and a step that needs a confirmation pauses the
        # plan there. Steps that name their tool run it directly;
        # plain-language steps are resolved to a tool at run time
        # by the production executor (the run_anticipation_routine
        # resolver); a step that cannot be resolved honestly halts
        # the plan naming the step. A step that runs another plan
        # or routine, creates or deletes a routine, or calls the
        # toolbox is refused. The result names every step, what
        # ran, and where the run stopped.
        self.register(
            name="run_plan",
            description=(
                "Run a saved plan: walks its steps in order, executing each step's "
                "tool call. The plan must already exist — use list_plans to see saved "
                "plans and give its id (e.g. 'plan-1'). SAFETY: running a plan needs "
                "the owner's confirmation every time — this tool always asks first. "
                "The call approves STARTING the plan, never its steps: each step keeps "
                "its own confirm/allowlist gate, and a step that needs a confirmation "
                "pauses the plan there. Steps that name their tool run it directly; "
                "plain-language steps are resolved to a tool at run time, and a step "
                "that cannot be resolved honestly halts the plan naming the step. A step "
                "that runs another plan or routine, creates or deletes a routine, or "
                "calls the toolbox is refused. The result names every step, what ran, "
                "and where the run stopped."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "The plan id, e.g. 'plan-1' (see list_plans)"}
                },
                "required": ["plan_id"],
            },
            handler=planner.run_plan_tool,
        )
        self.register(
            name="save_routine",
            description=(
                "Save a named routine: an ordered list of tool calls that can be replayed "
                "with run_routine. Pass 'actions' as a JSON string encoding a list of objects "
                "{\"tool\": \"<tool name>\", \"args\": {<arguments>}}, e.g. "
                "'[{\\\"tool\\\": \\\"get_current_time_date\\\", \\\"args\\\": {}}]'. "
                "Maximum 20 actions. Saving does not run anything."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Routine name, e.g. 'morning setup'"},
                    "actions": {
                        "type": "string",
                        "description": "JSON string: a list of {\"tool\": name, \"args\": {...}} objects",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Optional run schedule: 'daily HH:MM', 'interval 45m', 'interval 2h', or 'once YYYY-MM-DD HH:MM'. Omit for an on-demand routine."
                    },
                },
                "required": ["name", "actions"],
            },
            handler=planner.save_routine,
        )
        self.register(
            name="list_routines",
            description="List all saved routines with their action counts. Run one with run_routine; record a new one with save_routine. For step-by-step plans you tick off by hand instead, list_plans.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=planner.list_routines,
        )
        self.register(
            name="run_routine",
            description=(
                "Run a saved routine: executes each of its tool calls in order. SAFETY: "
                "routine steps go through the same trust layer as normal tool calls — "
                "destructive steps (shutdown, closing apps, deleting files, dangerous shell "
                "commands) will REFUSE without a separate user confirmation and the routine "
                "will HALT at that step. Never promise the user a routine will run a "
                "destructive step unattended. "
                # Fix 9: the routine runs as a coordinator task
                # (plan, act, verify, recover, report) — the
                # result is the task receipt, naming what
                # finished and what did not; a routine that
                # needs a step jump or a nested routine falls
                # back to the legacy loop and says so.
                "The run is a tracked task: steps are verified, "
                "a failed step retries within its budget or "
                "pauses at the step boundary, and a safe plan "
                "is approved once up front while destructive "
                "steps still ask one by one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The routine name (see list_routines)"}
                },
                "required": ["name"],
            },
            handler=planner.run_routine_as_task,
        )
        # Field-check fix #32 (round-2 N5): run an anticipation
        # routine — a routine whose steps are plain-language
        # strings (core/w7_wiring_anticipation.plan_routine),
        # resolved to tool calls at RUN time by the production
        # executor. This is a different store from the planner's
        # routines (data/anticipation_routines.json, not
        # data/routines.json) — list_routines does NOT list
        # these. The run approves starting the routine, never
        # its steps: each step keeps its own confirm/allowlist
        # gate, and a step that needs a confirmation pauses the
        # routine there. Free dispatch, like run_routine — the
        # tool itself asks nothing; the steps ask for
        # themselves.
        from .. import w7_wiring_anticipation as _w7a
        self.register(
            name="run_anticipation_routine",
            description=(
                "Run a saved anticipation routine: executes each of its plain-language "
                "steps in order, resolving each step to a tool call at run time. SAFETY: "
                "steps go through the same trust layer as normal tool calls — a step the "
                "executor cannot resolve refuses honestly and the routine HALTS there; a "
                "step that needs arguments must name the tool and its arguments "
                "(e.g. 'launch_application {\"app_name\": \"Notepad\"}'); destructive steps "
                "will REFUSE without a separate user confirmation and the routine will HALT "
                "at that step. Never promise the user a routine will run a destructive step "
                "unattended. A step that runs another routine, creates or deletes a routine, "
                "or calls the toolbox is refused. The result names every step, the tool it "
                "resolved to, and where the run stopped."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The anticipation routine name"}
                },
                "required": ["name"],
            },
            handler=_w7a.run_anticipation_routine,
        )
        self.register(
            name="delete_routine",
            description="Delete a saved routine. This cannot be undone, so check list_routines first if you are unsure which routine it is. Plans are a separate store — delete_plan removes those.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The routine name to delete"}
                },
                "required": ["name"],
            },
            handler=planner.delete_routine,
        )

    def _register_episode_tools(self):
        """Registers the episodic-memory tools (core/episodes.py):
        the day journal — recall a recorded day, pin a note to today."""
        from ..memory import episodes  # Phase 3 F3: file now in core/funnel/; import climbs one more level

        self.register(
            name="recall_day",
            description=(
                "Recall what happened on a day from VYUK's episodic journal: "
                "session summaries, notable actions, and notes the user pinned "
                "with remember_today. Use for 'what did we do yesterday?', "
                "'what happened today?', 'what did I ask you to remember "
                "today?'. Answers from the record only — if nothing was "
                "recorded for that day, it says so. For long-term facts and preferences, use recall_memories."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when": {
                        "type": "string",
                        "description": "'today', 'yesterday', an ISO date 'YYYY-MM-DD', or a weekday name like 'friday' / 'last friday'",
                    },
                },
                "required": ["when"],
            },
            handler=episodes.recall_day,
        )
        self.register(
            name="remember_today",
            description=(
                "Pin a note to today's episodic journal so it becomes part of "
                "the day's record (e.g. 'remember today: the plumber is coming "
                "at 4'). For facts that should stay true beyond today, use "
                "remember_user_fact instead."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The note to pin to today",
                    },
                },
                "required": ["text"],
            },
            handler=episodes.remember_today,
        )

    def _register_complete_tools(self):
        """Registers the Control Complete pack (core/desktop/control_plus.py):
        click-by-name/text, network, display, desktops, processes, battery,
        zip, webcam, recycle bin, downloads. The module owns its catalogue;
        this method only wires it in (same pattern as the other packs)."""
        from ..desktop import control_plus  # Phase 3 D2b: control_plus moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        control_plus.register_all(self)

    def _register_cursor_tools(self):
        """Registers pro cursor control (core/desktop/cursor.py): one cursor_control
        tool — DPI-true coordinates, smooth eased glides, user-takeover
        abort, beacon ring. The module owns its catalogue via register_all."""
        from ..desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        cursor.register_all(self)

    def _register_uia_tools(self):
        """Registers the UI Automation pack (core/desktop/uia.py, v4.3): find,
        click and type into controls by their visible name, and read a
        window's control tree — precise where OCR guessing isn't. The
        module owns its catalogue via register_all; wired in isolation
        like the mega packs so a broken UIA backend can never take the
        registry down."""
        try:
            from ..desktop import uia  # Phase 3 D4b: uia hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            uia.register_all(self)
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] UIA pack failed to register: {e}")
            _record_pack_failure("_register_uia_tools", 'uia', e)
        # v5.49 (programme item 30): click_by_mark — the grounding
        # counterpart to the UIA/cursor tools above: it clicks a
        # numbered mark from the last grounding capture's table
        # (core/desktop/uia.py owns the table and the handler). Registered
        # here, beside the packs it belongs with; isolated like
        # them so a failure can never take the registry down.
        try:
            from ..desktop import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            self.register(
                name="click_by_mark",
                description="Click a numbered grounding mark from the most recent screenshot or screen_look: the look numbered the visible controls ([1], [2], ...) and listed them with their names; pass that mark_id to click the centre of its rectangle. The mark is re-validated at click time — if the foreground window changed since the marks were drawn, the click is refused as stale, so re-observe and pick a fresh id. Prefer uia_click_control when the target has a stable name; use click_by_mark when the look is what identified the target.",
                parameters={
                    "type": "object",
                    "properties": {
                        "mark_id": {"type": "integer", "description": "The mark's id from the last look's grounding table, e.g. 3"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default left)"},
                        "expect": {"type": "string", "description": "Optional expectation to verify after the click, e.g. 'a dialog titled Save As appears' — the result appends a grounded verdict: met, not-met or unverifiable"},
                    },
                    "required": ["mark_id"],
                },
                handler=_uia.click_by_mark,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] click_by_mark failed to register: {e}")
            _record_pack_failure("_register_uia_tools", 'uia', e, item="click_by_mark")

    def _register_world_tools(self):
        """Registers the Your World pack (core/google_services.py): the
        user's Google Calendar + Gmail behind their own OAuth consent
        (Settings → Your world) — reads since v2.4, plus the two
        human-gated writes (send_email, create_calendar_event) since
        v5.2. Brain-agnostic — the functions return plain strings
        like every other handler."""
        from .. import google_services  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        from .. import morning_briefing  # Round-3 fix Q6: the deterministic briefing tool lives beside the calendar fetch it renders

        self.register(
            name="get_todays_schedule",
            description="Get the user's Google Calendar schedule for today: every event across all their calendars, with its time, title, and location. Today only — for events beyond today use get_upcoming_events. Requires Google connected (Settings → Your world); if it isn't, the result explains how to connect.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=google_services.todays_schedule,
        )
        self.register(
            name="get_morning_briefing",
            description="Give the morning briefing: today's date and time, every event on the user's Google Calendar today with its time, the free blocks between them, and any overlaps — read from the calendar by this tool itself and rendered deterministically. Call this when the user asks for their morning briefing or what's on today — never work the schedule out from reminders or memory, and never say the day is clear unless this tool says so. If the calendar can't be read, this tool says so instead of claiming the day is empty. Requires Google connected (Settings → Your world); if it isn't, the result explains how to connect.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=morning_briefing.get_morning_briefing,
        )
        self.register(
            name="get_upcoming_events",
            description="Get the user's upcoming Google Calendar events over the next few days, across all their calendars, in start order, with dates, times, titles, and locations. The window runs from now over the next `days` days — for just today's events use get_todays_schedule.",
            parameters={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How many days ahead to look (default 2, max 14)"},
                    "limit": {"type": "integer", "description": "Maximum number of events to return (default 10)"},
                },
                "required": [],
            },
            handler=google_services.upcoming_events,
        )
        self.register(
            name="get_unread_email",
            description="Get the user's unread Gmail messages in the inbox: who sent each one, the subject, when it arrived, and a short preview. The result leads with the unread count. Search beyond the unread ones with search_email.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of unread emails to return (default 8)"},
                },
                "required": [],
            },
            handler=google_services.unread_email,
        )
        self.register(
            name="search_email",
            description="Search the user's Gmail inbox with a Gmail search query (a name, a subject word, 'from:someone', 'subject:invoice', or plain words) and return the matching messages. Every result carries its message id; pass include_body=true to also read each message's full text.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query, e.g. 'from:priya', 'subject:assignment', or 'train ticket'"},
                    "limit": {"type": "integer", "description": "Maximum number of emails to return (default 6)"},
                    "include_body": {"type": "boolean", "description": "Also fetch each message's full body text (slower; default false)"},
                },
                "required": ["query"],
            },
            handler=google_services.search_email,
        )
        self.register(
            name="send_email",
            description="Send an email from the user's Gmail account. This SENDS real mail: the user must approve it in VYUK's confirmation popup first, and a Google sign-in that predates the send permission gets an explanation plus the one-step fix instead of sending. For a reply, pass thread_id and in_reply_to (the message id being answered) so it threads correctly.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address, e.g. 'priya@example.com'"},
                    "subject": {"type": "string", "description": "Subject line of the email"},
                    "body": {"type": "string", "description": "The email body, as plain text"},
                    "thread_id": {"type": "string", "description": "Optional: Gmail thread id when this is a reply, so the message lands in the same conversation"},
                    "in_reply_to": {"type": "string", "description": "Optional: the message id being replied to (sets the In-Reply-To/References headers)"},
                    "attachments": {"type": "array", "items": {"type": "string"}, "description": "Optional (R19-5 al1abb/invoify inspiration): file paths to attach, at most 5 files, each at most 25 MB (Gmail's own ceiling); a .pdf attaches as application/pdf. A missing or unreadable attachment refuses the send before anything is mailed."},
                },
                "required": ["to", "subject", "body"],
            },
            handler=google_services.send_email,
        )
        self.register(
            name="create_calendar_event",
            description="Create an event on the user's Google Calendar. The user must approve it in VYUK's confirmation popup first. Times are ISO strings like '2026-10-23T17:15:00' (local time unless an offset is included). To see the day it lands on first, get_todays_schedule; what's already coming up is get_upcoming_events.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "start": {"type": "string", "description": "Start time, ISO format, e.g. '2026-10-23T17:15:00'"},
                    "end": {"type": "string", "description": "End time, ISO format, e.g. '2026-10-23T18:15:00'"},
                    "description": {"type": "string", "description": "Optional event notes/description"},
                    "location": {"type": "string", "description": "Optional event location"},
                    "calendar_id": {"type": "string", "description": "Optional calendar id (default 'primary')"},
                },
                "required": ["title", "start", "end"],
            },
            handler=google_services.create_event,
        )
        self.register(
            name="download_email_attachment",
            description="Download an attachment from one of the user's emails to their Downloads folder (or a folder they name). Give the message id (from search_email / get_unread_email results); with no filename, a lone attachment is saved, and with several the result lists them so the user can pick. Filenames are sanitised, existing files are never overwritten, and files over 25 MB are refused. Requires Google connected.",
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The Gmail message id that carries the attachment"},
                    "filename": {"type": "string", "description": "Optional: which attachment to save (exact name, or a unique part of it)"},
                    "save_dir": {"type": "string", "description": "Optional: folder to save into (default: the user's Downloads)"},
                },
                "required": ["message_id"],
            },
            handler=self._tool_download_email_attachment,
        )
        self.register(
            name="sync_updates",
            description="What's new in the user's Gmail and Google Calendar since the last sync: new unread mail and new or changed upcoming events, through the shared sync engine (pages until exhausted, retries transient failures, remembers a watermark per source so a re-sync fetches only what's new). Pass sources='mail' or 'calendar' to sync one side only, or full=true to re-sync everything and report it all as new. Requires Google connected.",
            parameters={
                "type": "object",
                "properties": {
                    "sources": {"type": "string", "description": "Which to sync: 'mail', 'calendar', or 'both' (default 'both')"},
                    "full": {"type": "boolean", "description": "Re-sync everything (the watermarks reset first) and report it all as new (default false)"},
                },
                "required": [],
            },
            handler=google_services.sync_updates,
        )

