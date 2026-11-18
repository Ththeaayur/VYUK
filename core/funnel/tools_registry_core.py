# tools_registry registry-core mixin (batch 1 split):
# registration, declarations, and Gemini budget/schema methods
# moved verbatim from ToolRegistry. GAPS-23 (F-V5): the B23 seam
# is gone — the allow-list/kinds/schema tables register() reads
# live in this module now; the facade re-exports them.
import contextvars
import functools
import types
from collections.abc import Callable  # def-time: register annotation
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


# GAPS-23 (F-V7): the capability boundary. A tool handler
# (_tool_* method) runs ONLY inside ToolRegistry.execute()'s
# dispatch stage: _exec_dispatch sets this flag around the handler
# call (both the direct path and the deadline worker thread, whose
# context does NOT inherit the caller's — threading.Thread starts
# with fresh contextvars, so the worker sets it itself), and every
# handler is wrapped (see _wrap_funnel_handlers) to refuse when
# the flag is unset. A direct call — a bare-mixin instance, the
# registry singleton, a fresh registry — raises instead of
# running, with no gate, no receipt, no audit. Nested handler
# calls inside a dispatch still pass: the flag stays set for the
# whole dispatch scope. The flag is a contextvar (not a global
# counter) so concurrent dispatches cannot launder each other: a
# direct call from another thread while a dispatch runs elsewhere
# still sees an unset flag.
_FUNNEL_DISPATCH: contextvars.ContextVar[bool] = (
    contextvars.ContextVar("vyuk_funnel_dispatch", default=False))


@contextmanager
def _funnel_dispatch_scope():
    """Mark the enclosed call as funnel dispatch (sets the
    capability flag for exactly this scope)."""
    _token = _FUNNEL_DISPATCH.set(True)
    try:
        yield
    finally:
        _FUNNEL_DISPATCH.reset(_token)


def _funnel_only(fn):
    """Wrap one handler METHOD so it refuses outside the funnel.

    Method shape: the wrapper takes `self` and forwards it — it is
    installed ON the class by _wrap_funnel_handlers, so `self` is
    bound by the descriptor protocol. Do NOT use this for a plain
    function (a module-level handler): it would demand a `self`
    the caller never passes — use _funnel_only_fn for those."""
    @functools.wraps(fn)
    def _guarded(self, *args, **kwargs):
        if not _FUNNEL_DISPATCH.get():
            raise RuntimeError(
                "tool handler %s called outside the funnel — "
                "call it through ToolRegistry.execute() instead "
                "(GAPS-23 F-V7 capability boundary; a direct call "
                "gets no gate, no receipt and no audit)" %
                fn.__qualname__)
        return fn(self, *args, **kwargs)
    _guarded._funnel_guarded = True
    return _guarded


def _funnel_only_fn(fn):
    """Wrap one MODULE-LEVEL handler so it refuses outside the funnel.

    Plain-function shape (no `self`): for the module-level
    `_tool_*` functions registered directly as handlers
    (core/meeting.py, core/companion.py, ...). The decorator is
    applied at the definition site, so the module binding itself
    refuses — a direct `meeting._tool_stop_meeting()` call fails
    closed, exactly like a direct mixin-method call. register()
    also applies it to any handler that arrives bare, as the
    choke-point guarantee."""
    @functools.wraps(fn)
    def _guarded(*args, **kwargs):
        if not _FUNNEL_DISPATCH.get():
            raise RuntimeError(
                "tool handler %s called outside the funnel — "
                "call it through ToolRegistry.execute() instead "
                "(GAPS-23 F-V7 capability boundary; a direct call "
                "gets no gate, no receipt and no audit)" %
                fn.__qualname__)
        return fn(*args, **kwargs)
    _guarded._funnel_guarded = True
    return _guarded


def _wrap_funnel_handlers(cls):
    """Wrap every _tool_* method in cls's MRO once (marked, so a
    second call — re-import, re-run — is a no-op). Only plain
    functions are wrapped; static/class methods and non-function
    entries are left alone. Returns the number wrapped."""
    import types
    wrapped = 0
    for klass in cls.__mro__:
        for name, fn in list(vars(klass).items()):
            if not name.startswith("_tool_"):
                continue
            if not isinstance(fn, types.FunctionType):
                continue
            if getattr(fn, "_funnel_guarded", False):
                continue
            setattr(klass, name, _funnel_only(fn))
            wrapped += 1
    return wrapped


# GAPS-23 (F-V5): the register() tables live HERE now, in the
# core module that owns register() — no per-call function-local
# import of the facade to read them (the old B23 seam).
# tools_registry.py re-exports them so every existing importer
# keeps working at its old address. Moved verbatim, with
# their #: doc-comments.

OPEN_SCHEMA_TOOLS = frozenset({"use_toolbox"})


#: Audit fix Batch 3 (REG-01) — the ONLY permitted duplicate
#: registrations, name-bound: the declared winner (to-module)
#: replaces the declared loser (from-module), for the stated
#: reason. Any other duplicate — override=True or not — fails
#: registration LOUDLY (ValueError) and the original handler
#: survives. The allow-list, not pack order, decides the
#: winner: a loser arriving AFTER its winner is refused rather
#: than silently restoring the older handler, so registering
#: the packs in either order still leaves the declared winner
#: in place. Reasons are verbatim from the override comments
#: at the second registration sites.
OVERRIDE_ALLOWLIST: dict[str, dict[str, str]] = {
    "resize_image": {
        "from": "skills_media",
        "to": "skills_media_convert",
        "reason": ("adds scale_percent, an explicit output path, "
                   "EXIF orientation, never-overwrite suffixing, "
                   "and the 20000-pixel cap the media version lacks"),
    },
    "convert_image": {
        "from": "skills_media",
        "to": "skills_media_convert",
        "reason": ("adds an explicit output path, a quality control, "
                   "never-overwrite suffixing, EXIF orientation, and "
                   "the transparency-flattening warning the media "
                   "version lacks"),
    },
    "screenshot_region": {
        "from": "skills_media",
        "to": "skills_screen_capture",
        "reason": ("takes an explicit output path, verifies the "
                   "capture, never overwrites an existing file "
                   "(suffixed copy instead), and confirms the saved "
                   "file's actual size"),
    },
}


#: Audit fix Batch 3 (CAP-01) — machine-readable tool kinds.
#: Only descriptor/plan tools carry a kind; every other tool
#: has kind None and its declaration carries no kind field.
#: "descriptor" = the tool only describes/builds a descriptor
#: (nothing acted on); "planner" = the description frames the
#: output as a plan to follow. No tool is renamed or deleted
#: by this labelling (a rename needs an owner ruling).
#: The table is the reviewed per-tool source: register()
#: applies it, so pack register_all calls (which accept any
#: registry-like object) need no signature change, and a
#: kind given explicitly at a call site wins over the table.
#: The 25 names are the audit register's CAP-01 set; the
#: 26th probed tool could NOT be identified from the audit
#: package (its probe CSV is not in the evidence listing and
#: several same-pack siblings share the descriptor pattern),
#: so it is deliberately NOT guessed into this table — see
#: tests/test_auditfix_batch3.py for the pinned set.
TOOL_KIND_VALUES = frozenset({"descriptor", "planner"})


TOOL_KIND_TABLE: dict[str, str] = {
    # planner: the description frames the output as a plan
    "travel_door_to_door": "planner",
    "hardware_plan": "planner",
    "pilot_revision_plan": "planner",
    # descriptor: everything else in the CAP-01 set
    "game_macro_describe": "descriptor",
    "game_session_describe": "descriptor",
    "hardware_inventory": "descriptor",
    "iot_local_device": "descriptor",
    "net_network_descriptor": "descriptor",
    "net_network_status": "descriptor",
    "pilot_bench": "descriptor",
    "pilot_build_steps": "descriptor",
    "pilot_dev_workflow": "descriptor",
    "pilot_review_note": "descriptor",
    "pilot_study_session": "descriptor",
    "travel_loadout": "descriptor",
    "travel_recipe": "descriptor",
    "travel_scene": "descriptor",
    "travel_scoreboard": "descriptor",
    "travel_sonify": "descriptor",
    "travel_spatial": "descriptor",
    "travel_transport": "descriptor",
    "travel_trip": "descriptor",
    "well_reminder": "descriptor",
    "forgiving_select": "descriptor",
    "phase5_interface_describe": "descriptor",
}


# GAPS-23 (F-V8): the declarative registration shape (OpenJarvis's
# ToolSpec + decorator registry is the shape; this is our own code,
# nothing adopted). A tool as frozen data: name, description, closed
# schema, handler, and the two register() options that change
# behaviour (override, kind). Frozen so a spec cannot be mutated
# after it is built — the registry stores exactly what the spec
# said. register_spec() is the only way a spec becomes a tool.
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable
    override: bool = False
    kind: str | None = None


class ToolRegistryCoreMixin:
    def __init__(self):
        self._tools: dict[str, dict[str, Any]] = {}
        self._overrides: list[dict[str, Any]] = []
        self._register_default_tools()

    def register(self, name: str, description: str, parameters: dict[str, Any], handler: Callable, override: bool = False, kind: str | None = None):
        """Register one tool.

        First registration of a name always succeeds.
        Re-registration is NOT last-write-wins any more
        (audit fix Batch 3, REG-01): re-registering the
        identical handler object is a no-op (pack reload
        safety, nothing changes); any other duplicate raises
        ValueError unless override=True AND the name is in
        OVERRIDE_ALLOWLIST AND the handler's module is the
        declared winner — in which case the replacement is
        recorded in the override log (override_log()). A
        declared loser arriving after its winner raises too
        (the winner survives), so pack order cannot silently
        restore an older handler. Plugin loading keeps its
        own snapshot-collision rule (it never reaches this
        method with a colliding name).

        Audit fix Batch 2 (TOOL-02): the schema is stored closed
        (additionalProperties: false) for every fixed-signature
        tool, so validate_args refuses an undeclared key with a
        model-repairable error instead of letting _dispatch_args
        strip it silently. The caller's dict is never mutated: a
        shallow copy carries the added key. A schema that already
        declares additionalProperties keeps its own value, and
        OPEN_SCHEMA_TOOLS (use_toolbox only) stays open.
        """
        if kind is None:
            kind = TOOL_KIND_TABLE.get(name)
        if kind is not None and kind not in TOOL_KIND_VALUES:
            raise ValueError(
                f"tool {name!r}: unknown kind {kind!r} "
                f"(allowed: {sorted(TOOL_KIND_VALUES)})")
        if name in self._tools:
            existing = self._tools[name]
            try:
                # GAPS-23 (F-V7): compare against the stored
                # handler AND its unwrapped original — register()
                # wraps bare handlers on the way in, so the stored
                # object is a wrapper while a re-registration
                # passes the original function; without the unwrap
                # the identical-handler no-op (REG-01) would raise.
                _stored = existing["handler"]
                _stored_orig = getattr(_stored, "__wrapped__", _stored)
                if _stored_orig is _stored:
                    _stored_orig = getattr(
                        getattr(_stored, "__func__", None),
                        "__wrapped__", _stored)
                _same_handler = (handler == _stored
                                 or handler == _stored_orig)
            except Exception:  # noqa: BLE001 — equality never blocks
                _same_handler = False
            if _same_handler:
                return
            entry = OVERRIDE_ALLOWLIST.get(name)
            if not override or entry is None:
                raise ValueError(
                    f"duplicate tool registration for {name!r}: "
                    "not in the override allow-list — a duplicate "
                    "name with no allow-list entry is a bug, not a "
                    "feature (audit fix Batch 3, REG-01)")
            _winner_mod = getattr(handler, "__module__", "") or ""
            _loser_mod = getattr(
                existing["handler"], "__module__", "") or ""
            _is_winner = _winner_mod == entry["to"] or _winner_mod.endswith(
                "core." + entry["to"])
            _is_loser = _winner_mod == entry["from"] or _winner_mod.endswith(
                "core." + entry["from"])
            _loser_in_place = _loser_mod == entry["from"] or _loser_mod.endswith(
                "core." + entry["from"])
            _winner_in_place = _loser_mod == entry["to"] or _loser_mod.endswith(
                "core." + entry["to"])
            if _is_winner and _loser_in_place:
                self._overrides.append({
                    "name": name,
                    "from_module": entry["from"],
                    "to_module": entry["to"],
                    "reason": entry["reason"],
                })
            elif _is_loser and _winner_in_place:
                raise ValueError(
                    f"duplicate tool registration for {name!r}: "
                    "the allow-listed winner "
                    f"({entry['to']}) is already registered — the "
                    f"loser ({entry['from']}) cannot overwrite it")
            else:
                raise ValueError(
                    f"duplicate tool registration for {name!r}: "
                    "override module pair "
                    f"({_loser_mod} -> {_winner_mod}) is not the "
                    f"allow-listed pair "
                    f"({entry['from']} -> {entry['to']})")
        params = parameters
        if (isinstance(params, dict)
                and name not in OPEN_SCHEMA_TOOLS
                and "additionalProperties" not in params):
            params = dict(params)
            params["additionalProperties"] = False
        # GAPS-23 (F-V7): the funnel boundary, applied at the single
        # choke point — every stored handler refuses when called
        # outside a funnel dispatch. Mixin methods arrive already
        # wrapped by _wrap_funnel_handlers (marked _funnel_guarded
        # on the underlying function); module-level handlers
        # (core/meeting.py, core/companion.py, core/summary.py,
        # ...) arrive bare and are wrapped here. The wrap runs AFTER
        # the duplicate/override checks above, so re-registering the
        # identical handler object still no-ops (REG-01) and the
        # override module checks see the original __module__.
        # Non-function callables (none in the tree today) pass
        # through unwrapped rather than breaking registration.
        _underlying = getattr(handler, "__func__", handler)
        if not getattr(_underlying, "_funnel_guarded", False):
            if isinstance(handler, (types.FunctionType,
                                   types.MethodType)):
                handler = _funnel_only_fn(handler)
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": params,
            "handler": handler,
            "kind": kind,
        }

    def register_spec(self, spec: "ToolSpec") -> None:
        """Register one tool from a frozen ToolSpec (GAPS-23 F-V8).

        The declarative registration API: a spec is data, so a pack
        can build its tools as specs and the registry stays the
        single place that turns a spec into a stored tool. This
        calls register(), so a spec inherits the REG-01 duplicate
        rule, the override allow-list, the closed-schema rule, and
        the F-V7 funnel-boundary wrap on its handler.
        """
        self.register(spec.name, spec.description, spec.parameters,
                      spec.handler, override=spec.override,
                      kind=spec.kind)

    def override_log(self) -> list[dict[str, Any]]:
        """Every allow-listed override applied to this registry
        (copies): name, loser module, winner module, reason.
        Empty when no override was needed."""
        return [dict(entry) for entry in self._overrides]

    def get_declarations(self) -> list[dict[str, Any]]:
        """Returns the canonical function declarations.

        These carry the true schemas: validate_args() enforces them
        locally, and the local/Ollama/Groq paths render from them. The
        Gemini Live API rejects some shapes these schemas legitimately
        use, so the Live session builders
        (core/funnel/tool_profiles.py profile_declarations,
        core/funnel/toolbox.py router_declarations) build Gemini-safe
        offered sets from them instead (v5.27 — the field patch
        scripts proved the raw list was what Google refused at
        connect time).
        """
        decls = []
        for name, tool in self._tools.items():
            decl = {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"]
            }
            # CAP-01: only descriptor/plan tools carry a kind,
            # so the catalogue can filter on it; every other
            # declaration is byte-identical to before (the
            # Gemini fitted copy rebuilds without kind, so the
            # Live payload budget is untouched).
            if tool.get("kind"):
                decl["kind"] = tool["kind"]
            decls.append(decl)
        return decls

    @staticmethod
    def gemini_payload_size(decls: list) -> int:
        """The serialized size the Live API judges: the byte
        length of the declarations as JSON. One measurement,
        shared by the pre-flight check, the session and the
        ship gate."""
        import json as _json
        return len(_json.dumps(decls))

    _GEMINI_DECL_BUDGET = 240_000
    _GEMINI_DESC_CAPS = (200, 150, 110, 80, 55)

    @classmethod
    def _gemini_fit_budget(cls, decls: list) -> None:
        """Shrink description text in the Gemini declaration
        copies until their JSON fits the byte budget. Mutates the
        copies only — they are fresh dicts built by the profile
        builders (core/funnel/toolbox.py gemini_decl_for_names); the
        registry's own schemas are never touched. No-op when
        already under budget."""
        import json as _json
        size = len(_json.dumps(decls))
        if size <= cls._GEMINI_DECL_BUDGET:
            return
        # v5.88: play_music's routing clause ("never browser_control
        # on YouTube/Spotify", chars 100-141 of the canonical
        # description) was engineered to survive the 150 cap; the
        # registry growth to 532 tools stepped the fitter from cap
        # 150 to cap 110, which cut it mid-word ("never brow.").
        # Restore this one description only when the restored total
        # still fits the budget — if future growth pushes it over,
        # the exemption drops automatically and the gate suites
        # flag it again instead of silently shipping a garbled
        # routing instruction to Live.
        _pm = next((d for d in decls if d.get("name") == "play_music"), None)
        _pm_saved = _pm["description"] if _pm is not None else None
        _last_cap = None
        for cap in cls._GEMINI_DESC_CAPS:
            _last_cap = cap
            for decl in decls:
                cls._cap_descriptions(decl, cap)
            size = len(_json.dumps(decls))
            if size <= cls._GEMINI_DECL_BUDGET:
                break
        if _pm is not None and _pm_saved is not None:
            _pm["description"] = _pm_saved
            if len(_json.dumps(decls)) > cls._GEMINI_DECL_BUDGET:
                cls._cap_descriptions(_pm, _last_cap)
            size = len(_json.dumps(decls))
        print(f"[Tools] Gemini declarations trimmed to {size} bytes "
              f"(Live API budget {cls._GEMINI_DECL_BUDGET}).")

    @classmethod
    def _cap_descriptions(cls, node: Any, cap: int) -> None:
        if isinstance(node, dict):
            desc = node.get("description")
            if isinstance(desc, str) and len(desc) > cap:
                node["description"] = desc[:cap].rstrip() + "."
            for value in node.values():
                cls._cap_descriptions(value, cap)
        elif isinstance(node, list):
            for item in node:
                cls._cap_descriptions(item, cap)

    @classmethod
    def _gemini_safe_schema(cls, node: Any, root: bool = True) -> Any:
        """Recursive copy of a parameter schema, Gemini-safe per the
        rules of the Gemini-safe rewrite (non-string enums dropped,
        empty enum entries removed, field-less nested objects
        carried as JSON text, no-argument tools declared with no
        parameters block).

        SCOPE (router-consistency batch, wiring audit WIR-03): this
        rewrite applies to REGISTERED tool declarations only. The
        synthetic domain-router declarations (route_*) never pass
        through it — they are built separately and their args node
        is a documented pass-through, not a missing schema: its
        schema is the inner tool's schema, enforced at dispatch by
        registry.execute()/validate_args (see
        core/brain/domain_routers.py build_domain_routers). A bare nested
        object below is therefore rewritten to a JSON-text string
        ONLY when it belongs to a registered declaration; router
        args nodes keep their object shape by design, and the
        string form is still accepted at dispatch as a fallback
        (dispatch_route parses JSON-text args), so either shape
        routes. Do not "fix" a router args node by running it
        through this function: that would change the
        field-proven calling convention for no gain.
        """
        if isinstance(node, list):
            return [cls._gemini_safe_schema(item, False) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            # Audit fix Batch 2 (TOOL-02): the Gemini Schema
            # dialect has no additionalProperties field, so the
            # transport copy drops it. Closure is enforced
            # locally by validate_args() before any Gemini call
            # reaches a handler — the stripped copy is never
            # the enforcement point, so dropping it here
            # reopens nothing.
            if key == "additionalProperties":
                continue
            if key == "enum" and isinstance(value, list):
                out[key] = list(value)
            elif isinstance(value, (dict, list)):
                out[key] = cls._gemini_safe_schema(value, False)
            else:
                out[key] = value
        enum = out.get("enum")
        if isinstance(enum, list):
            if any(not isinstance(v, str) for v in enum):
                out.pop("enum", None)
            else:
                cleaned = [v for v in enum if v != ""]
                if cleaned:
                    out["enum"] = cleaned
                else:
                    out.pop("enum", None)
        if (not root and out.get("type") == "object"
                and not out.get("properties")):
            desc = str(out.get("description") or "").strip()
            out = {
                "type": "string",
                "description": (desc + " " if desc else "")
                + "Give this as a JSON object written as text.",
            }
        return out
