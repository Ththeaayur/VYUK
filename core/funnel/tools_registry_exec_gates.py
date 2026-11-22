# tools_registry execute-gates mixin (batch 2 split):
# execute() stages: failsafe, kill-chain, protected path,
# protected destination, install-tree write, echo guard,
# confirmation gate, and epoch re-check. Stage bodies moved
# verbatim apart from the one authorised plumbing change:
# cross-stage names read/write ctx.<name>.
import json

from .guard_state import FailsafeState
from .tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _READ_ONLY_TOOLS,
    _foreground_process_name,
    _is_egress_read,
    _is_owner_only_read,
    _is_read_class,
    _needs_confirmation,
    _confirmation_args,
    _protected_destination_hit,
    _protected_path_hit,
    _folder_scope_hit,
    _install_tree_folder,
    _install_tree_write_hit,
    _tool_tier,
    grant_screen_scope,
    mcp_is_read,
    mcp_needs_confirmation,
    screen_scope_active,
    session_owner_proven,
)
from .. import injection  # Phase 3 F3: file now in core/funnel/; import climbs one more level
from .tools_registry_validators import _failsafe_state  # INTENTIONAL (batch 2): failsafe consult lives in validators (batch 1); stages resolve it here, not through the facade.


class ToolRegistryExecGatesMixin:
    def _exec_failsafe(self, ctx):
        # v5.46 (programme item 5, A3): PANIC AT THE EXECUTOR.
        # While the failsafe is tripped, every non-read tool is
        # refused HERE — after validation, BEFORE the protected-
        # path check and the confirmation gate — so the trip
        # outranks both (a tripped gated tool's refusal is this
        # "refused", never "not_confirmed") and no handler for
        # run_command / file_control / close_application /
        # power_control / email / downloads / … can run while
        # tripped. Before A3 the trip was checked only inside
        # the hands modules, so those tools ran straight past a
        # panic. Read-class calls (see _is_read_class) still run:
        # looking is not acting, and a human triaging a trip
        # needs her reads. Planner/routine paths inherit this
        # automatically — they call this execute(), so no
        # planner change is needed or made.
        # The failsafe is consulted LAZILY here (no top-level
        # import of failsafe itself): failsafe imports only
        # stdlib and cannot cycle with this module. A2 (P1-1):
        # the consult is tri-state (_failsafe_state) — an
        # unconsultable failsafe is UNAVAILABLE, never "not
        # tripped" and never "disabled" (no explicit disabled
        # state exists for the failsafe; an exception is not a
        # declaration). UNAVAILABLE refuses non-read tools
        # exactly like a trip; reads stay exempt in both cases
        # (R5: reads run so a human triaging keeps her eyes).
        # Headless contexts are unaffected: a failsafe that
        # imports fine but is not running returns clear, not
        # an exception, so only a genuine consult failure
        # newly refuses.
        # GAPS-12: the read exemption is the MCP class for MCP
        # calls (the tool's operator-reviewed policy class),
        # _is_read_class for native ones — one failsafe stage,
        # not two.
        _is_read = (mcp_is_read(ctx._mcp_entry)
                    if ctx._mcp_entry is not None
                    else _is_read_class(ctx.name, ctx.args))
        _fs_state, _fs_reason = _failsafe_state()
        # GAPS-24 (K-V3): a trip must stop RUNNING work, not just
        # refuse new calls. The trip listener already cancelled
        # live dispatch tokens when the trip fired — this covers
        # the headless stop-flag trip, where no monitor thread
        # fired and no listener ran: the first call that observes
        # the trip cancels whatever is still running. Best-effort
        # and guarded; an empty registry is a no-op, and a
        # consult that could not run (UNAVAILABLE) cancels
        # nothing — there is no trip to honour, only a refusal.
        if _fs_state is FailsafeState.TRIPPED:
            try:
                from . import tool_cancel as _cancel_trip  # Phase 3 F3: file now in core/funnel/ (intra-package sibling)

                _cancel_trip.cancel_all_live(reason="stop")
            except Exception:  # noqa: BLE001 — cancelling never
                pass  # breaks a call
        if _fs_state is FailsafeState.UNAVAILABLE and not _is_read:
            self._log_activity(
                ctx.name,
                "error: failsafe unavailable — refused")
            ctx._audit_gate["value"] = "refused:failsafe-unavailable"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (
                                 "VYUK's failsafe could not be consulted "
                                 f"({_fs_reason}) — this non-read tool was "
                                 "not run until the safeguard can be "
                                 "verified. Nothing was run.")})
        if _fs_state is FailsafeState.TRIPPED and not _is_read:
            self._log_activity(
                ctx.name,
                f"error: failsafe tripped ({_fs_reason}) — refused")
            ctx._audit_gate["value"] = "refused:failsafe"  # v5.49 (item 38)
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (
                                 "VYUK's failsafe has tripped "
                                 f"({_fs_reason or 'no reason recorded'}) — "
                                 "her hands and tools are stopped until a "
                                 "human re-arms them from the tray menu. "
                                 "Nothing was run.")})

    def _exec_killchain(self, ctx):
        # v5.56 (Wave 2, SE-6.9): graduated kill-chain gate —
        # immediately after the failsafe refusal, before the
        # protected-path check (the failsafe stays first and
        # untouched). A2 (P1-1): a gate_tool exception is
        # UNAVAILABLE, never "no rung". UNAVAILABLE refuses
        # everything except a provably read-only, provably
        # LOCAL call (gate_tool itself refuses egress-class
        # calls at rung 2 and all calls at rung 5; with the
        # rung unknown, only a read-class call that cannot
        # touch the network can prove itself unaffected by
        # any rung below 5 — the rung-5 residual documented
        # in A2 is preserved: a hidden rung 5 still lets
        # such reads through for triage visibility). Rung 3
        # (freeze-brain) is NOT judged here — it gates
        # cloud-brain dispatch in core/brain/brain.py, not the tool
        # funnel.
        _kc_unavailable = False
        _kc_unavail_reason = ""
        # GAPS-12: one kill-chain stage. MCP has no separate
        # egress classifier, so a non-read MCP call counts as
        # egress-class for rung 2 (subsumed by rung 1's
        # non-read gating anyway); for MCP, read ⟺ non-egress,
        # so the native "read and non-egress" exemption rule
        # reduces to read here.
        if ctx._mcp_entry is not None:
            _kc_read = mcp_is_read(ctx._mcp_entry)
            _kc_egress = not _kc_read
            _kc_exempt = _kc_read
        else:
            _kc_read = _is_read_class(ctx.name, ctx.args)
            _kc_egress = _is_egress_read(ctx.name)
            _kc_exempt = _kc_read and not _kc_egress
        try:
            from . import kill_chain as _kc  # Phase 3 F2b: kill_chain moved to core/funnel/ (Phase 3 F3: file now in core/funnel/ (intra-package sibling))

            # Egress batch: INTENTIONAL behavior change —
            # the old code passed is_egress_class=not
            # _is_egress_read(name), which refused purely
            # local reads (clock) as "network" at rung 2
            # while letting real network reads (weather)
            # through. _is_egress_read already means
            # "can put bytes on the wire" (see
            # core/funnel/tools_gate.py), so it IS the gate's
            # egress class, unnegated. The taint gate,
            # pclass, and rule_of_two already used it
            # unnegated; only these two kill-chain sites
            # negated it.
            _kc_verdict = _kc.gate_tool(
                ctx.name,
                is_read_class=_kc_read,
                is_egress_class=_kc_egress)
        except Exception as exc:  # noqa: BLE001 — A2 fail-closed
            _kc_verdict = None
            _kc_unavailable = True
            _kc_unavail_reason = type(exc).__name__
        if _kc_unavailable and not _kc_exempt:
            self._log_activity(
                ctx.name, "error: kill-chain unavailable — refused")
            ctx._audit_gate["value"] = "refused:kill-chain-unavailable"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (
                                 "VYUK's kill-chain gate could not be "
                                 f"consulted ({_kc_unavail_reason}) — this "
                                 "tool was not run until the safeguard "
                                 "can be verified. Nothing was run.")})
        if _kc_verdict is not None:
            self._log_activity(ctx.name, "error: kill-chain refused")
            ctx._audit_gate["value"] = "refused:kill-chain"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": _kc_verdict})

    def _exec_protected_path(self, ctx):
        # GAPS-12: MCP calls skip this stage, explicitly and in
        # the open — the check judges NATIVE argument keys
        # carrying local paths, and MCP arguments are
        # server-side (a Git path lives inside the server's
        # repository, a Fetch URL is remote by definition), so
        # it has nothing to judge. The skip is a branch in the
        # single stage now, not a whole parallel funnel.
        # Open judgment (not built here): a local stdio MCP
        # server receives path arguments these checks never
        # see — fencing those needs per-server path policies.
        if ctx._mcp_entry is not None:
            return None
        # v5.43 (H17): protected paths are refused BEFORE the
        # confirmation gate — there is nothing to ask the human
        # about, because no answer makes VYUK read out SSH keys,
        # .env files, credential stores or browser profile data.
        # This is the funnel's always-refuse class, so its kind is
        # "refused" (H8). The message names the protection and never
        # carries any of the file's content.
        protected = _protected_path_hit(ctx.name, ctx.args)
        if protected is not None:
            self._log_activity(ctx.name, "error: protected path refused")
            # v5.49 (programme item 38): audit gate disposition.
            ctx._audit_gate["value"] = "refused:protected-path"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (f"Refused: '{ctx.name}' cannot read "
                                         f"'{protected}' — that is a protected "
                                         f"path. VYUK never reads SSH keys, "
                                         f".env files, credential stores or "
                                         f"browser profile data.")})

    def _exec_protected_dest(self, ctx):
        # GAPS-12: MCP calls skip this stage — same rationale
        # as _exec_protected_path above (server-side args).
        if ctx._mcp_entry is not None:
            return None
        # v5.47 (programme item 9): WRITE DESTINATIONS are refused
        # here too — a separate check from the read check above,
        # in the same always-refuse position (before the gate, for
        # the same reason: no confirmation answer makes it safe).
        # The read check never judged copy/move/zip destinations,
        # so a protected file could be copied somewhere
        # unprotected and read back, and a protected file could be
        # overwritten by any tool writing to a model-chosen path.
        # _protected_destination_hit judges file_control's write
        # actions on BOTH source and destination, and every other
        # writer tool on its verified destination arguments (see
        # _WRITE_DESTINATION_ARGS). The refusal mirrors the read
        # refusal's shape and message family.
        protected_dest = _protected_destination_hit(ctx.name, ctx.args)
        if protected_dest is not None:
            self._log_activity(
                ctx.name, "error: protected destination refused")
            # v5.49 (programme item 38): audit gate disposition.
            ctx._audit_gate["value"] = "refused:protected-destination"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (f"Refused: '{ctx.name}' cannot write "
                                         f"to '{protected_dest}' — that is "
                                         f"a protected path. VYUK never "
                                         f"writes to, copies from or "
                                         f"overwrites SSH keys, .env files, "
                                         f"credential stores or browser "
                                         f"profile data.")})

    def _exec_install(self, ctx):
        # GAPS-12: MCP calls skip this stage — same rationale
        # as _exec_protected_path above (server-side args).
        if ctx._mcp_entry is not None:
            return None
        # v5.47 (programme item 10): INSTALL-TREE WRITE-DENY —
        # the mirror of the two checks above, aimed at VYUK
        # herself. A write into her own program folders (core/,
        # gui/, plugins/ — resolved from this file's location,
        # never cwd) would let a model, or an injection driving
        # it, rewrite the code that runs next launch. Same
        # always-refuse position as the protected checks: BEFORE
        # the confirmation gate, because no confirmation answer
        # overrides it. _install_tree_write_hit consults the
        # same _WRITE_DESTINATION_ARGS item 9 established, and
        # judges file_control's write actions on BOTH source and
        # destination (a delete/move source inside the tree is a
        # write against the tree). data/ is NOT denied — it is
        # the app's runtime store, not program code.
        install_hit = _install_tree_write_hit(ctx.name, ctx.args)
        if install_hit is not None:
            install_folder = _install_tree_folder(install_hit)
            self._log_activity(
                ctx.name, "error: install-tree write refused")
            # v5.49 (programme item 38): audit gate disposition.
            ctx._audit_gate["value"] = "refused:install-tree"
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (f"Refused: '{ctx.name}' cannot write "
                                         f"to '{install_hit}' — that is "
                                         f"inside VYUK's own "
                                         f"'{install_folder}' program "
                                         f"folder. VYUK will not write into "
                                         f"its own program folders (core, "
                                         f"gui, plugins), and no "
                                         f"confirmation can override that.")})

    def _exec_echo(self, ctx):
        # GAPS-12: MCP calls skip this stage — the echo guard
        # judges NATIVE text payloads (type_text / paste_text
        # arguments); an MCP call carries no native text
        # payload, so it has nothing to judge.
        if ctx._mcp_entry is not None:
            return None
        # v5.52 (item 7): ECHO GUARD — a text-injection payload
        # that near-verbatim repeats the user's own current
        # instruction is the request being parroted back as
        # content (the field failure: "(find flaws in every
        # tool)" typed into Notepad instead of a flaws report).
        # Refused here, in the always-refuse position (after the
        # protected checks, before the confirmation gate — there
        # is nothing to ask the human about), with a teaching
        # message the brain can recover from next round. The
        # consult is lazy: no published instruction, an active
        # dictation flag, or an explicitly-quoted payload all
        # read as "not an echo" and the call proceeds exactly
        # as before. An unimportable guard module (or any other
        # consult failure) is NOT a carve-out — GAPS-13: a guard
        # that cannot be consulted refuses the call with an
        # honest reason, because proceeding would type a payload
        # nobody verified. The comparison + carve-outs live in
        # core/echo_guard.py; the instruction seam is
        # core/brain/turn_context.py (clients publish each turn).
        _echo_payload = None
        try:
            from .. import echo_guard as _echo_guard  # Phase 3 F3: file now in core/funnel/; import climbs one more level
            from ..brain import turn_context as _turn_context  # Phase 3 B1a-1: moved to core/brain/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

            _arg_key = _echo_guard.TEXT_PAYLOAD_ARGS.get(ctx.name)
            if _arg_key and isinstance(ctx.args, dict):
                _candidate = ctx.args.get(_arg_key)
                if isinstance(_candidate, str) and _candidate.strip() \
                        and not _turn_context.dictation_active() \
                        and _echo_guard.is_instruction_echo(
                            _candidate,
                            _turn_context.current_instruction()):
                    _echo_payload = _candidate
        except Exception:  # noqa: BLE001 — GAPS-13: a guard
            # that cannot be consulted refuses — proceeding would
            # type a payload nobody verified as not-an-echo.
            self._log_activity(
                ctx.name, "error: echo guard unavailable — "
                "cannot verify the payload, refused")
            ctx._audit_gate["value"] = "refused:echo-guard"
            return ctx._audited({
                "status": "error", "kind": "refused",
                "message": (
                    f"Stopped: '{ctx.name}' was refused because "
                    "the echo guard could not be consulted, so "
                    "the text could not be verified as not "
                    "repeating the user's own instruction — "
                    "nothing was typed or pasted. Fix the guard "
                    "and retry.")})
        if _echo_payload is not None:
            self._log_activity(
                ctx.name, "error: echo guard — payload repeats the "
                "user's instruction, refused")
            ctx._audit_gate["value"] = "refused:echo-guard"  # v5.52 (item 7)
            _excerpt = " ".join(_echo_payload.split())[:80]
            return ctx._audited({
                "status": "error", "kind": "refused",
                "message": (
                    f"Stopped: '{ctx.name}' was about to type text that "
                    f"just repeats the user's own instruction "
                    f"(\"{_excerpt}\") — that is the request itself, "
                    "not content to type, so nothing was typed. "
                    "Produce the actual content the user asked for "
                    "instead: do the task (use the tools it needs), "
                    "then deliver the result. For a document or "
                    "report, write it with write_file and open it "
                    "with open_path — do not type a document "
                    "keystroke-by-keystroke.")})

    def _exec_permission(self, ctx):
        # PF-18 batch 1: THE funnel-gate choke point — exactly one
        # permission check for every execute() call, wired in the
        # orchestrator between _exec_mark_cb and _exec_route, so it
        # runs BEFORE the confirm ask (whose allowlist shortcut is
        # consulted inside request_confirmation) AND before MCP
        # dispatch (mcp__ calls return from _exec_route and never
        # reach _exec_confirm — a check inside _exec_confirm would
        # not cover them). A denied permission refuses the
        # capability HERE, never silently skips it, and no stored
        # always-allow can bypass it because the shortcut is
        # consulted only in the later confirm stage.
        # Refusal vocabulary (D2/D5): kind "refused" with the
        # permission named — never "not_confirmed" (which means
        # "awaiting an answer") and never a fake success. The
        # receipt event carries permission_id (Activity rendering
        # is deferred to the widget-wiring batch, so GUI/funnel/
        # ledger/Activity agree when the page goes live).
        # The stage never approves: unknown/granted pass through
        # and the confirm stage still asks exactly as before, so
        # a grant can never skip a confirm (D4). Session grants
        # never widen a STORED denial (D7): check_tool reads the
        # durable store, and a session grant widens only the
        # fail-closed default read, live-origin only, never for
        # the microphone (see permission_grants.session_grant_
        # allows — settings redesign 2026-11-16, R1). A denied
        # mic lapses session grants at flip time
        # (permission_grants teardown), not here.
        # check_tool is total by construction (it never raises; a
        # consult failure fails closed for bound tools), and the
        # grants module imports stdlib only — so no try/except
        # here: an import failure would be tree breakage, loud
        # rather than a silent pass.
        from .. import permission_grants as _pg
        allowed, pid = _pg.check_tool(ctx.name)
        if allowed:
            return None
        self._log_activity(
            ctx.name, "error: refused (permission denied)")
        ctx._audit_gate["value"] = f"refused:permission-{pid}"
        _label = {"microphone": "Microphone",
                  "camera": "Camera",
                  "screen": "Screen",
                  "browser": "Browser",
                  "files": "Files",
                  "home_devices": "Home devices"}.get(pid, str(pid))
        # F-F301: the refusal must name the real state. A permission
        # never decided is off by default — the user never touched
        # the switch — so it reads "has never been granted", not
        # "is switched off". permission_never_decided() distinguishes
        # the two (no store file, or a readable store with no record
        # for the id); the denied-by-user wording below is unchanged.
        # The grants module imports stdlib only and never raises, so
        # no try/except here, same as check_tool above.
        if _pg.permission_never_decided(pid):
            if pid == "browser":
                # Never connected, not switched off: the way back is
                # the one-tap connect, same as the denied case minus
                # the "back on" that implies it was on before.
                _how_back = ("tap 'Connect my browser' — it turns "
                             "this on in one tap.")
            else:
                _how_back = (f"switch {_label.lower()} on in "
                             "Settings.")
            return ctx._audited({
                "status": "error", "kind": "refused",
                "permission_id": pid,
                "message": (
                    f"{_label} has never been granted, so this "
                    f"capability was refused — nothing was run. To use "
                    f"it, {_how_back}")})
        if pid == "browser":
            # Field-check fix #16 (T8 #27): the refusal named
            # only Settings, never the one-tap way back the
            # no-session refusal already names ("Tap 'Connect my
            # browser' — it turns this on"). A refused browser
            # names that tap now; every other permission reads
            # exactly as before.
            _how_back = ("tap 'Connect my browser' — it turns "
                         "this on in one tap. (Switching browser "
                         "back on in Settings works too.)")
        else:
            _how_back = (f"switch {_label.lower()} back on in "
                         "Settings.")
        return ctx._audited({
            "status": "error", "kind": "refused",
            "permission_id": pid,
            "message": (
                f"{_label} is switched off in Settings, so this "
                f"capability was refused — nothing was run. To use "
                f"it again, {_how_back}")})

    def _exec_live_staleness(self, ctx):
        # Live staleness stage, P4 (2026-11-05; design:
        # LIVE_VERIFIER_COVERAGE_DESIGN.md §3): for LIVE-origin
        # absolute-target point actions only, refuse when the
        # view changed since the model last saw it (fresh grab
        # vs last DELIVERED frame — never the capture slot).
        # This is a staleness check, NOT a binding check: it
        # never claims the point is right, only that the view
        # is current. A false refusal costs one re-observe
        # round-trip, never a wrong click. Runs ONLY for
        # live-origin calls (P2's marker) — every other caller
        # returns here untouched, with the helper never invoked.
        # Fail-closed posture (GAPS-13): an unverifiable world
        # REFUSES, never passes through — no baseline, grab
        # failed, unusable grids, over budget, or any check
        # error all refuse with kind "stale-view" and an
        # Activity line naming the reason. A false refusal costs
        # one re-observe round-trip, never a wrong click, and
        # the geometry floor still applies in-handler. Refusal shape mirrors the permission
        # stage: audited refusal + audit-gate value + Activity
        # line; kind "stale-view" is a REFUSAL kind (see
        # tool_result.REFUSAL_KINDS), so the audited result
        # reads status "refused" (call never ran), never
        # "failure" (which would claim the click ran).
        from . import live_origin as _live_origin
        if not _live_origin.is_live_origin():
            return None
        from ..desktop import live_staleness as _ls
        if not _ls.is_live_point_action(ctx.name, ctx.args):
            return None
        try:
            verdict = _ls.check_staleness()
        except Exception:  # noqa: BLE001 — GAPS-13: a check
            # that cannot run is a refusal, not a pass-through —
            # a broken check must never let an unverified click
            # through; the caller re-observes and retries.
            self._log_activity(
                ctx.name, "error: refused (stale view: check "
                "could not run)")
            ctx._audit_gate["value"] = "refused:stale-view"
            return ctx._audited({
                "status": "error", "kind": "stale-view",
                "message": (
                    "Refused: the live view could not be checked "
                    "— the staleness check itself failed, so "
                    "nothing was clicked. Re-observe "
                    "(screenshot/share) and retry.")})
        if verdict["verdict"] == "stale":
            mean = verdict.get("mean_abs_diff")
            frac = verdict.get("frac_large")
            if mean is not None and frac is not None:
                detail = (" (mean diff %.1f, %.0f%% of pixels "
                          "changed a lot)" % (mean, frac * 100))
            else:
                detail = ""
            self._log_activity(
                ctx.name, "error: refused (stale view%s)" % detail)
            ctx._audit_gate["value"] = "refused:stale-view"
            return ctx._audited({
                "status": "error", "kind": "stale-view",
                "message": (
                    "Refused: the screen changed since the last "
                    "frame shared with the model — nothing was "
                    "clicked. Re-observe (screenshot/share) and "
                    "retry.")})
        if verdict["verdict"] == "unchecked":
            # GAPS-13: an unchecked view refuses — the reason
            # names why (no baseline yet, grab failed, unusable
            # grids, over budget, check error). The first live
            # point action of a session has no baseline yet and
            # is refused; the caller re-observes and retries.
            _reason = verdict.get("reason",
                                  "check could not complete")
            self._log_activity(
                ctx.name, "error: refused (stale view: "
                "unchecked — %s)" % _reason)
            ctx._audit_gate["value"] = "refused:stale-view"
            return ctx._audited({
                "status": "error", "kind": "stale-view",
                "message": (
                    "Refused: the live view could not be "
                    "verified as current (%s) — nothing was "
                    "clicked. Re-observe (screenshot/share) and "
                    "retry.") % _reason})
        return None

    def _exec_owner_read(self, ctx):
        # Loophole batch 5 (L-24, #16): reads are oracles. In an
        # UNPROVEN live-origin session under the provenance lock,
        # a read of the owner's private material is REFUSED in
        # plain words — not asked (a stranger gets no popup for
        # "what is on his calendar"), and nothing is read. The
        # boundary is _is_owner_only_read (tools_gate): calendar,
        # mail, files, contacts, notes, memory, plans, clipboard,
        # browser history, activity history, and the read actions
        # of the dispatched tools whose store is the owner's.
        # Public reads (clock, weather, web search, system info,
        # screen look) stay free; a proven session reads freely;
        # a typed / app-UI call (no live origin) is unaffected;
        # the lock OFF passes through. Placed after
        # _exec_live_staleness so route_/toolbox re-entry resolves
        # the true tool name first, and before the confirm ask
        # (a refusal needs no dialog). Refusal shape mirrors the
        # permission / folder-scope refusals: audited refusal +
        # audit-gate value + Activity line; kind "refused" is a
        # REFUSAL kind (tool_result.REFUSAL_KINDS), so the
        # audited result reads status "refused" (call never ran).
        # Fail-closed on an unreadable config (the lock is ON by
        # default, batch 4 pattern).
        from . import live_origin as _live_origin
        if not _live_origin.is_live_origin():
            return None
        if not _is_owner_only_read(ctx.name, ctx.args):
            return None
        if session_owner_proven():
            return None
        try:
            from ..config import config as _app_config

            lock_on = bool(getattr(
                _app_config, "session_provenance_lock", False))
        except Exception:  # noqa: BLE001 — unreadable: lock on
            lock_on = True
        if not lock_on:
            return None
        self._log_activity(
            ctx.name, "error: refused (owner read in unproven session)")
        ctx._audit_gate["value"] = "refused:owner-read"
        return ctx._audited({
            "status": "error", "kind": "refused",
            "message": (
                f"Refused: '{ctx.name}' reads your private material, "
                "and this voice session hasn't been proven as yours "
                "yet — so nothing was read. Unlock it on screen "
                "(Settings → Privacy & permissions) or with your "
                "voice PIN, then ask again.")})

    def _exec_folder_scope(self, ctx):
        # PF-18 batch 7: the D9 folder-scope stage — the files
        # permission's SECOND layer. It runs after _exec_permission
        # (a denied files grant is already refused there) and
        # BEFORE _exec_route (so MCP-routed calls are covered
        # structurally, like the permission stage) and BEFORE the
        # confirm ask. Scope binds the GRANTED state only (Choice
        # B, D3-consistent): unknown passes scope and goes through
        # the normal confirm path — an allow-once confirms that
        # call including its path, and only flipping the switch
        # in Settings makes a standing grant, which the list then
        # constrains. For a granted call, an out-of-scope path
        # refuses BEFORE any ask (there is nothing to ask about:
        # no answer adds the folder). The refusal names the folder
        # rule and directs to Settings, where the user adds the
        # folder; it never fakes success and never names file
        # content. Tools not bound to files pass immediately;
        # files tools with no path argument given pass (nothing to
        # judge — omitted optional outputs land beside the input
        # or in a handler default that is itself a default
        # folder). The scope hit function never raises (fail-closed
        # for files tools), and the grants module imports stdlib
        # only — so no try/except here, same as _exec_permission.
        hit = _folder_scope_hit(ctx.name, ctx.args)
        if hit is not None:
            # F-F302: scope judges the RAW values, so a mistyped arg
            # reads as an out-of-scope path — but the real defect is
            # the type (a path of 12345 is not a path outside the
            # allowed folders, it is not a path at all; and a
            # mistyped JSON payload is not the filler output path
            # scope fired on). When validation ALSO refuses this
            # call, its reason names the defect, so the refusal
            # carries the validation reason instead. This changes
            # nothing else: genuinely out-of-scope VALID paths keep
            # the scope refusal exactly as before, and calls with
            # no scope hit never reach this branch (the validate
            # stage judges them in its own turn). Native calls
            # only — MCP validation lives in _exec_validate, after
            # route. Any consult failure falls through to the scope
            # refusal (fail-closed, today's behaviour).
            if ctx._mcp_entry is None:
                try:
                    _problem = self.validate_args(ctx.name, ctx.args)
                except Exception:  # noqa: BLE001 — scope refusal stands
                    _problem = None
                if _problem is not None:
                    self._log_activity(ctx.name, f"error: {_problem}")
                    ctx._audit_gate["value"] = "refused:validation"
                    _tool = self._tools[ctx.name]
                    return ctx._audited({
                        "status": "error", "kind": "validation",
                        "message": _problem,
                        "validation_error": True,
                        "schema": {
                            "name": ctx.name,
                            "description": _tool["description"],
                            "parameters": _tool["parameters"],
                        }})
            self._log_activity(
                ctx.name, "error: refused (folder scope)")
            ctx._audit_gate["value"] = "refused:folder-scope"
            return ctx._audited({
                "status": "error", "kind": "refused",
                "permission_id": "files",
                "message": (
                    f"Refused: '{ctx.name}' cannot touch '{hit}' — "
                    f"it is outside the folders VYUK is allowed to "
                    f"use. Add the folder in Settings > "
                    f"Permissions, or choose a path inside an "
                    f"allowed folder. Nothing was run.")})

    def _exec_confirm(self, ctx):
        # v5.55 (Wave 1, SE-3.1): whether THIS call was already
        # human-approved — the Rule-of-Two check below skips its
        # extra ask when the gate just approved the call (asking
        # twice for one call is noise, and noise trains humans
        # to click yes).
        ctx._human_approved_this_call = False
        if ctx._mcp_entry is not None:
            # GAPS-12: one confirm stage. MCP calls ask through
            # the MCP predicate + the MCP view (which names the
            # external server, because the human is approving
            # code VYUK did not write) — the same _ask_human
            # the native gate uses, so the failure shapes
            # (no handler / decline / gate error) are identical.
            # No plan-approval shortcut and no scope-ask: those
            # are native-navigation concepts, and the confirm
            # allowlist never applies to MCP (an MCP kind can
            # never be allowlisted, so a standing grant can
            # never be laundered through the toolbox).
            return self._exec_confirm_mcp(ctx)
        if _needs_confirmation(ctx.name, ctx.args):
            # v5.3 (H1): executor-level human confirmation, FAILING
            # CLOSED. The tool runs only when a registered handler
            # returns an explicit yes. No handler, a declined /
            # timed-out ask, or the gate itself raising all refuse
            # the call and say so — the pre-H1 shape treated the
            # first and third as "run it anyway".
            # v5.46 (programme item 2): the soft-scope-ask case —
            # soft taint, a Tier-1 (navigation) call, no active
            # screen scope, and the call is being gated. Its popup
            # carries the scoped-approval offer (below), and an
            # approval grants the scope for the foreground app.
            _scope_ask = (
                injection.taint_kind() == "soft"
                and _tool_tier(ctx.name, ctx.args) == 1
                and not screen_scope_active())
            if injection.tainted() and ctx.name not in _CONFIRM_GATE_TOOLS:
                # This call is gated by the taint alone (H2) — say
                # so in the log, or the popup looks arbitrary.
                # v5.46 (programme item 2): the predicate is now
                # kind-aware — HARD keeps the original test (not
                # read-only, or an egress read); SOFT labels the
                # calls the soft branch itself gates (Tier 2, or
                # Tier 1 with no active scope). The label's shape
                # ("taint-gated (source: …)") is unchanged.
                if injection.taint_kind() == "soft":
                    _tier = _tool_tier(ctx.name, ctx.args)
                    _taint_gated = _tier == 2 or (
                        _tier == 1 and not screen_scope_active())
                else:
                    _taint_gated = (ctx.name not in _READ_ONLY_TOOLS
                                    or _is_egress_read(ctx.name))
                if _taint_gated:
                    self._log_activity(
                        ctx.name,
                        f"taint-gated (source: {injection.taint_source()})")
            try:
                from .. import confirm_gate  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                if not confirm_gate.has_handler():
                    self._log_activity(
                        ctx.name, "error: not confirmed (no handler registered)")
                    # v5.49 (programme item 38): no user was ever
                    # asked, so this is a refusal, not a denial.
                    ctx._audit_gate["value"] = "refused:no-handler"
                    return ctx._audited({
                        "status": "error", "kind": "not_confirmed",
                        "message": (f"'{ctx.name}' is a critical action and "
                                    "needs the user's OK in VYUK's window "
                                    "first — no confirmation handler is "
                                    "active, so nothing was run.")})
                verbatim = ({"command"} if ctx.name == "run_command"
                            else {"text"} if ctx.name in ("type_text",
                                                       "paste_text")
                            else {"routine", "schedule", "steps",
                                  "replaces"}
                            if ctx.name == "scheduled_agents"
                            and (ctx.args or {}).get("action") == "save_routine"
                            else frozenset())
                # v5.45 (H22a): decision-critical fields (paths,
                # targets, commands, URLs, app names) are shown in
                # the popup IN FULL — they join the verbatim set.
                try:
                    from ..confirm_gate import FULL_FIDELITY_KEYS  # Phase 3 F3: file now in core/funnel/; import climbs one more level

                    verbatim = set(verbatim) | {
                        k for k in (ctx.args or {})
                        if str(k).lower() in FULL_FIDELITY_KEYS}
                except Exception:  # noqa: BLE001 — fidelity is
                    pass  # presentation; the gate decision stands
                _view = _confirmation_args(ctx.name, ctx.args)
                if _scope_ask:
                    # v5.46 (programme item 2): the popup IS the
                    # scoped approval — it says, in full (the
                    # "screen_scope" entry joins the verbatim set
                    # so H22 fidelity never clips it), that
                    # approving frees NAVIGATION in this app for
                    # 15 minutes while high-impact actions keep
                    # asking. Approval below grants exactly that.
                    _view = dict(_view)
                    _view["screen_scope"] = (
                        "I'll act on what's on screen in "
                        f"{_foreground_process_name() or 'this app'} "
                        "for the next 15 minutes — navigation only; "
                        "shell, sending, deleting and other "
                        "high-impact actions will still ask every "
                        "time.")
                    verbatim = set(verbatim) | {"screen_scope"}
                # v5.49 (programme item 38): snapshot the gate's
                # last decision before the ask, so the decision
                # THIS ask records can be told from a stale one
                # (a mocked ask records nothing and joins no ID).
                try:
                    _dec_before = confirm_gate.last_decision()
                except Exception:  # noqa: BLE001 — join is additive
                    _dec_before = None
                # Fix 8 (plan approval, 2026-11-06): a step of a
                # coordinator task the human approved in the plan
                # dialog runs without a second popup — the plan
                # approval IS the confirmation for that exact
                # (tool, args) in that task. covering_call()
                # re-verifies everything at dispatch (digest,
                # epoch, taint, money, provenance, eligibility
                # incl. the new-file leg), so a stale or changed
                # call falls through to the popup below. The
                # audit value names the plan (not a plain user
                # approval) and the Activity line names the task
                # and step, so the trail never has a gap where a
                # popup used to be.
                _plan = None
                try:
                    from .. import plan_approval as _pa  # Phase 3 F3: file now in core/funnel/; import climbs one more level
                    _plan = _pa.covering_call(ctx.name, ctx.args)
                except Exception:  # noqa: BLE001 — undecidable: ask
                    _plan = None
                if _plan is not None:
                    self._log_activity(
                        ctx.name,
                        "approved in plan %s step %s — no popup"
                        % (_plan["task_id"][:8], _plan["index"] + 1))
                    _approved = True
                else:
                    _approved = confirm_gate.request_confirmation(
                        ctx.name, _view, verbatim_keys=verbatim)
                try:
                    _dec_after = confirm_gate.last_decision()
                except Exception:  # noqa: BLE001 — join is additive
                    _dec_after = None
                if _dec_after is not None \
                        and _dec_after != _dec_before \
                        and _dec_after.get("tool_name") == ctx.name:
                    ctx._audit_gate["request_id"] = \
                        _dec_after.get("request_id")
                if not _approved:
                    # v5.49 (programme item 38): the human (or the
                    # gate's timeout / a replayed batched decision)
                    # said no — a user denial, joined above.
                    ctx._audit_gate["value"] = "user-denied"
                    self._log_activity(ctx.name, "error: not confirmed by user")
                    return ctx._audited({
                        "status": "error", "kind": "not_confirmed",
                        "message": (f"'{ctx.name}' is a critical action and "
                                    "needs the user's OK in VYUK's window "
                                    "first — it wasn't given, so nothing "
                                    "was run.")})
                # v5.49 (programme item 38): an approved scope-ask
                # is a scoped approval; any other yes is a plain
                # user approval. The grant below is unchanged.
                # Fix 8: a plan-approved call carries its own
                # audit value ("plan-approved"), never the plain
                # "user-approved" — the two approvals must stay
                # distinguishable in the audit trail.
                # GAPS-23 (F-V6): an allowlisted approval carries
                # its own value too ("allowlist-approved") — before
                # this it joined as "user-approved", the same
                # structured value as a popup approval, although
                # no user was ever asked. The joined decision
                # (_dec_after) names the authority; a mocked ask
                # joins nothing, so the fallback stays
                # "user-approved" for it. A scope-ask that was
                # allowlisted keeps "scoped-approval" (a scope
                # WAS granted below) — the decision record still
                # carries authority "allowlist" via the join.
                _joined_authority = (
                    _dec_after.get("authority")
                    if _dec_after is not None
                    and _dec_after != _dec_before
                    and _dec_after.get("tool_name") == ctx.name
                    else None)
                ctx._audit_gate["value"] = (
                    "plan-approved" if _plan is not None
                    else "scoped-approval" if _scope_ask
                    else "allowlist-approved"
                    if _joined_authority == "allowlist"
                    else "user-approved")
                ctx._human_approved_this_call = True  # v5.55 (SE-3.1)
                # v5.48 (programme item 20): the approval just
                # landed — if the session epoch moved while the
                # ask was in flight, the session that was approved
                # under is gone and the approval is VOID. Refuse
                # in the funnel's refused shape; the handler never
                # runs.
                if ctx._epoch_void():
                    self._log_activity(
                        ctx.name,
                        "error: session changed while awaiting "
                        "confirmation — approval void")
                    # v5.49 (programme item 38): the refusal
                    # overrode the approval; the request ID joined
                    # above still names that approval.
                    ctx._audit_gate["value"] = "refused:epoch-void"
                    return ctx._audited({
                        "status": "error", "kind": "refused",
                        "message": (
                            "Session changed while awaiting "
                            "confirmation — approval void, tool "
                            "not run.")})
                if _scope_ask:
                    # Approved: the human granted the screen scope
                    # for the app in the foreground right now.
                    grant_screen_scope(
                        app=_foreground_process_name())
            except Exception as e:  # noqa: BLE001 — a broken gate denies
                # Also "not_confirmed" (H8): whatever went wrong, no
                # confirmation was obtained, so nothing ran.
                self._log_activity(
                    ctx.name, f"error: confirmation gate failed: {e!s}")
                # v5.49 (programme item 38): no decision was
                # reached — a gate-error refusal, not a denial.
                ctx._audit_gate["value"] = "refused:gate-error"
                return ctx._audited({
                    "status": "error", "kind": "not_confirmed",
                    "message": (f"'{ctx.name}' is a critical action and "
                                "needs the user's OK in VYUK's window "
                                "first — the confirmation check itself "
                                "failed, so nothing was run.")})

    def _exec_confirm_mcp(self, ctx):
        # GAPS-12: the MCP leg of the ONE confirm stage. Mirrors
        # the old _execute_mcp confirm block verbatim in
        # predicate and shape: the MCP predicate decides whether
        # an ask is needed at all, the view names the external
        # server and shows the tool's own description, and the
        # ask goes through the shared _ask_human — so a declined
        # / timed-out / handler-less ask refuses exactly like
        # the native gate's. The epoch-void check after approval
        # runs here too (the stage-level recheck below runs for
        # every call as well, so an approval can never be
        # laundered across a session change).
        _entry = ctx._mcp_entry
        _needs_confirm = mcp_needs_confirmation(
            _entry, ctx.args, ctx._mcp_bus.tool_changed(ctx.name))
        if not _needs_confirm:
            return None
        _view = (
            f"MCP tool '{ctx.name}' on server "
            f"'{ctx._mcp_server}': "
            f"{_entry.get('description') or '(no description)'}")
        if _entry.get("parameters"):
            _view += f"\nArguments: {json.dumps(ctx.args)}"
        try:
            _approved = self._ask_human(
                ctx.name, _view, {"arguments", "description"},
                ctx._audited, ctx._audit_gate)
        except Exception as e:  # noqa: BLE001 — gate itself failed
            self._log_activity(
                ctx.name, f"error: MCP confirmation check failed ({e})")
            ctx._audit_gate["value"] = "refused:gate-error"
            return ctx._audited({
                "status": "error", "kind": "not_confirmed",
                "message": (
                    f"'{ctx.name}' is a critical action and needs "
                    "the user's OK in VYUK's window first — the "
                    "confirmation check itself failed, so nothing "
                    "was run.")})
        if _approved is not True:
            return _approved
        ctx._human_approved_this_call = True
        if ctx._epoch_void():
            self._log_activity(
                ctx.name,
                "error: session changed while awaiting "
                "confirmation — approval void")
            ctx._audit_gate["value"] = "refused:epoch-void"
            return ctx._audited({
                "status": "refused", "kind": "refused",
                "message": (
                    "Approval void — the session changed while "
                    "waiting for your OK. Nothing was run.")})
        return None

    def _exec_epoch_recheck(self, ctx):
        # v5.48 (programme item 20): last-responsible-moment
        # re-check — the epoch is compared once more immediately
        # before the handler runs, covering any branch between the
        # confirmation above and here. An epoch that moved since
        # dispatch voids the call in the same refused shape.
        if ctx._epoch_void():
            self._log_activity(
                ctx.name,
                "error: session changed while awaiting "
                "confirmation — approval void")
            ctx._audit_gate["value"] = "refused:epoch-void"  # v5.49 (item 38)
            return ctx._audited({"status": "error", "kind": "refused",
                             "message": (
                                 "Session changed while awaiting "
                                 "confirmation — approval void, tool "
                                 "not run.")})
