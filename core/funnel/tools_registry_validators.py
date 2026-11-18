# tools_registry validators (batch 1 split): schema/type/value
# validation helpers moved verbatim from tools_registry.py.
import math
from typing import Any

from .guard_state import FailsafeState
from .tools_handlers import article_for


_VALIDATOR_ANNOTATIONS = frozenset({
    "description", "title", "default", "examples", "example",
    "format", "readOnly", "writeOnly", "deprecated", "$schema",
    "$id", "$comment",
})


_VALIDATOR_KEYWORDS = _VALIDATOR_ANNOTATIONS | frozenset({
    "type", "properties", "required", "items", "additionalItems",
    "additionalProperties", "enum", "const", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems",
    "uniqueItems", "minProperties", "maxProperties",
    "allOf", "anyOf", "oneOf", "not",
})


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality with Python's bool/int conflation removed:
    True equals only True, never 1; 1 and 1.0 are equal numbers
    (JSON Schema treats them as the same number). Containers
    compare element-wise under the same rule."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) \
            and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[k], right[k]) for k in left)
    return left == right


def _type_matches(kind: str, value: Any) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        # bool is a subclass of int in Python — a JSON true
        # is NOT an integer argument.
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) \
            and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "null":
        return value is None
    return False


def _allows_null(spec: dict[str, Any]) -> bool:
    declared = spec.get("type")
    if declared == "null":
        return True
    if isinstance(declared, list) and "null" in declared:
        return True
    if "const" in spec and spec["const"] is None:
        return True
    enum = spec.get("enum")
    if isinstance(enum, list):
        return any(v is None for v in enum)
    return False


def _validate_schema(name: str, path: str, spec: Any,
                     _depth: int = 0) -> str | None:
    """Schema-shape validation (logic audit LOG-04/LOG-05).

    Returns an 'Invalid arguments...' string when the schema
    node itself is malformed, else None. This is the
    fail-closed gate the old validator lacked: a malformed
    keyword value used to be silently ignored (the schema
    quietly became less restrictive); now the call is
    rejected as an invalid schema instead. Called once over
    the whole schema tree by _validate_args_against_schema
    before any argument is judged, so a malformed node is
    caught even when the submitted value never exercises it
    (e.g. a malformed schema for an absent optional field).
    Argument problems are NOT reported here — only schema
    shape. Boolean schemas (True/False) are valid JSON Schema
    and pass; anything else that is not a dict is invalid.
    Annotation keywords (description, format, ...) constrain
    nothing and accept any value. Depth is capped: a remote
    schema nesting past the cap is invalid rather than a
    recursion crash (the MCP boundary guard is the second
    line of defence for anything that still raises).
    """

    def fail(detail: str) -> str:
        return (f"Invalid arguments for tool '{name}': "
                f"invalid schema for '{path}': {detail}")

    if spec is True or spec is False:
        return None
    if not isinstance(spec, dict):
        return fail("schema is not an object")
    if _depth > 100:
        return fail("schema nesting exceeds the supported depth")
    unknown = sorted(set(spec) - _VALIDATOR_KEYWORDS)
    if unknown:
        return (f"Invalid arguments for tool '{name}': "
                f"unsupported schema keyword(s) {unknown} for "
                f"'{path}' — call rejected rather than silently "
                f"accepted")

    def _is_number(v: Any) -> bool:
        return (isinstance(v, (int, float))
                and not isinstance(v, bool) and math.isfinite(v))

    def _is_count(v: Any) -> bool:
        return type(v) is int and v >= 0

    if "type" in spec:
        declared = spec["type"]
        kinds = ([declared] if isinstance(declared, str)
                 else declared if isinstance(declared, list)
                 else None)
        if kinds is None or not kinds or not all(
                isinstance(k, str) for k in kinds):
            return fail("'type' must be a string or a list of "
                        "strings")
        lowered = [k.lower() for k in kinds]
        if any(k not in ("string", "integer", "number", "boolean",
                         "object", "array", "null") for k in lowered):
            return fail(f"unsupported schema type(s) {lowered}")
    if "enum" in spec:
        enum = spec["enum"]
        if not isinstance(enum, list) or not enum:
            return fail("'enum' must be a non-empty list")
    for bound in ("minimum", "maximum"):
        if bound in spec and not _is_number(spec[bound]):
            return fail(f"'{bound}' must be a finite number")
    for excl in ("exclusiveMinimum", "exclusiveMaximum"):
        if excl in spec and not (type(spec[excl]) is bool
                                 or _is_number(spec[excl])):
            return fail(f"'{excl}' must be a boolean or a finite "
                        "number")
    if "multipleOf" in spec:
        mult = spec["multipleOf"]
        if not _is_number(mult) or mult <= 0:
            return fail("'multipleOf' must be a finite number "
                        "greater than 0")
    for count in ("minLength", "maxLength", "minItems", "maxItems",
                  "minProperties", "maxProperties"):
        if count in spec and not _is_count(spec[count]):
            return fail(f"'{count}' must be a non-negative integer")
    if "pattern" in spec:
        pattern = spec["pattern"]
        if not isinstance(pattern, str):
            return fail("'pattern' must be a string")
        import re as _re

        try:
            _re.compile(pattern)
        except _re.error:
            return fail(f"invalid 'pattern' {pattern!r}")
    if "uniqueItems" in spec and not isinstance(spec["uniqueItems"],
                                               bool):
        return fail("'uniqueItems' must be a boolean")
    if "items" in spec:
        items = spec["items"]
        if isinstance(items, (dict, bool)):
            problem = _validate_schema(name, f"{path}[]", items,
                                       _depth + 1)
            if problem is not None:
                return problem
        elif isinstance(items, list):
            for i, branch in enumerate(items):
                problem = _validate_schema(
                    name, f"{path}[{i}]", branch, _depth + 1)
                if problem is not None:
                    return problem
        else:
            return fail("'items' must be a schema or a list of "
                        "schemas")
    if "additionalItems" in spec:
        extra = spec["additionalItems"]
        if isinstance(extra, dict):
            problem = _validate_schema(name, f"{path}[]", extra,
                                       _depth + 1)
            if problem is not None:
                return problem
        elif not isinstance(extra, bool):
            return fail("'additionalItems' must be a boolean or a "
                        "schema")
    if "additionalProperties" in spec:
        extra = spec["additionalProperties"]
        if isinstance(extra, dict):
            problem = _validate_schema(name, f"{path}.*", extra,
                                       _depth + 1)
            if problem is not None:
                return problem
        elif not isinstance(extra, bool):
            return fail("'additionalProperties' must be a boolean "
                        "or a schema")
    if "properties" in spec:
        properties = spec["properties"]
        if not isinstance(properties, dict):
            return fail("'properties' must be an object")
        for field, branch in properties.items():
            if not isinstance(field, str):
                return fail("'properties' keys must be strings")
            child = f"{path}.{field}" if path else field
            problem = _validate_schema(name, child, branch,
                                       _depth + 1)
            if problem is not None:
                return problem
    if "required" in spec:
        required = spec["required"]
        if (not isinstance(required, list)
                or any(not isinstance(f, str) for f in required)
                or len(set(required)) != len(required)):
            return fail("'required' must be a list of unique "
                        "strings")
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in spec:
            branches = spec[keyword]
            if not isinstance(branches, list) or not branches:
                return fail(f"'{keyword}' must be a non-empty list")
            for i, branch in enumerate(branches):
                problem = _validate_schema(
                    name, f"{path}.{keyword}[{i}]", branch,
                    _depth + 1)
                if problem is not None:
                    return problem
    if "not" in spec:
        problem = _validate_schema(name, f"{path}.not", spec["not"],
                                   _depth + 1)
        if problem is not None:
            return problem
    return None


def _check_value(name: str, path: str, value: Any,
                 spec: Any) -> str | None:
    """One value against one schema node. Returns an
    'Invalid arguments...' string, or None when it passes."""

    def fail(msg: str) -> str:
        return f"Invalid arguments for tool '{name}': {msg}"

    if spec is True:
        return None
    if spec is False:
        return fail(f"'{path}' is forbidden by its schema")
    if not isinstance(spec, dict):
        return fail(f"unsupported schema for '{path}': "
                    "schema is not an object")
    unknown = sorted(set(spec) - _VALIDATOR_KEYWORDS)
    if unknown:
        return fail(f"unsupported schema keyword(s) "
                    f"{unknown} for '{path}' — call rejected "
                    "rather than silently accepted")
    # Composition first: a value must satisfy every allOf
    # branch, at least one anyOf branch, exactly one oneOf
    # branch, and no `not` branch.
    for keyword, branches in (("allOf", spec.get("allOf")),
                              ("anyOf", spec.get("anyOf")),
                              ("oneOf", spec.get("oneOf"))):
        if branches is None:
            continue
        if not isinstance(branches, list) or not branches:
            return fail(f"unsupported schema for '{path}': "
                        f"'{keyword}' must be a non-empty list")
        results = [_check_value(name, path, value, branch)
                   for branch in branches]
        if keyword == "allOf":
            for problem in results:
                if problem is not None:
                    return problem
        elif keyword == "anyOf":
            if all(p is not None for p in results):
                return fail(f"'{path}' matches no '{keyword}' branch")
        else:  # oneOf
            if sum(p is None for p in results) != 1:
                return fail(f"'{path}' must match exactly one "
                            f"'{keyword}' branch")
    if "not" in spec:
        if _check_value(name, path, value, spec["not"]) is None:
            return fail(f"'{path}' matches the forbidden 'not' schema")
    # Explicit null: passes only where the schema allows null.
    # A spec with no type / enum / const constrains nothing
    # (typeless props, see the A4 note above) and passes.
    if value is None:
        if _allows_null(spec):
            return None
        if "type" in spec or "enum" in spec or "const" in spec:
            declared = spec.get("type")
            return fail(f"'{path}' must not be null "
                        f"(type {declared!r} does not allow null)")
        return None
    declared = spec.get("type")
    if declared is not None:
        kinds = [declared] if isinstance(declared, str) \
            else declared if isinstance(declared, list) else None
        if kinds is None or not kinds or not all(
                isinstance(k, str) for k in kinds):
            return fail(f"unsupported schema for '{path}': "
                        "'type' must be a string or a list of strings")
        kinds = [k.lower() for k in kinds]
        if any(k not in ("string", "integer", "number", "boolean",
                         "object", "array", "null")
               for k in kinds):
            return fail(f"unsupported schema type(s) {kinds} "
                        f"for '{path}' — call rejected rather than "
                        "silently accepted")
        if not any(_type_matches(k, value) for k in kinds):
            if len(kinds) == 1:
                kind = kinds[0]
                return fail(f"'{path}' must be {article_for(kind)} "
                            f"{kind} (got {type(value).__name__})")
            return fail(f"'{path}' must be one of types {kinds} "
                        f"(got {type(value).__name__})")
    if "const" in spec and not _json_equal(value, spec["const"]):
        return fail(f"'{path}' must be {spec['const']!r} "
                    f"(got {value!r})")
    if "enum" in spec:
        enum = spec.get("enum")
        if not isinstance(enum, list) or not enum:
            return fail(f"invalid schema for '{path}': "
                        "'enum' must be a non-empty list")
        if not any(_json_equal(value, choice) for choice in enum):
            return fail(f"'{path}' must be one of {enum!r} "
                        f"(got {value!r})")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        excl_min = spec.get("exclusiveMinimum")
        excl_max = spec.get("exclusiveMaximum")
        # A bool is a subclass of int in Python, so a bool bound
        # must never reach a numeric comparison: it is not a
        # number here. (The schema gate above already rejects
        # bool bounds at the funnel; these exclusions keep a
        # direct _check_value call honest too.)
        if isinstance(minimum, (int, float)) \
                and not isinstance(minimum, bool) \
                and value < minimum:
            return fail(f"'{path}' must be at least {minimum} "
                        f"(got {value})")
        if isinstance(maximum, (int, float)) \
                and not isinstance(maximum, bool) \
                and value > maximum:
            return fail(f"'{path}' must be at most {maximum} "
                        f"(got {value})")
        # exclusiveMinimum / exclusiveMaximum (LOG-03): booleans
        # branch FIRST. A number (draft 6+) is the bound itself;
        # boolean True (older drafts) makes `minimum` /
        # `maximum` exclusive; boolean False imposes nothing.
        # The old isinstance-first order swallowed True as the
        # number 1 (rejecting 0.5 against minimum 0 with "must
        # be greater than True") and False as the number 0
        # (imposing a bound where none was meant).
        if type(excl_min) is bool:
            if excl_min and isinstance(minimum, (int, float)) \
                    and not isinstance(minimum, bool) \
                    and value <= minimum:
                return fail(f"'{path}' must be greater than {minimum} "
                            f"(got {value})")
        elif isinstance(excl_min, (int, float)) \
                and value <= excl_min:
            return fail(f"'{path}' must be greater than {excl_min} "
                        f"(got {value})")
        if type(excl_max) is bool:
            if excl_max and isinstance(maximum, (int, float)) \
                    and not isinstance(maximum, bool) \
                    and value >= maximum:
                return fail(f"'{path}' must be less than {maximum} "
                            f"(got {value})")
        elif isinstance(excl_max, (int, float)) \
                and value >= excl_max:
            return fail(f"'{path}' must be less than {excl_max} "
                        f"(got {value})")
        multiple = spec.get("multipleOf")
        if isinstance(multiple, (int, float)) \
                and not isinstance(multiple, bool) and multiple != 0:
            quotient = value / multiple
            if abs(quotient - round(quotient)) > 1e-9:
                return fail(f"'{path}' must be a multiple of "
                            f"{multiple} (got {value})")
    if isinstance(value, str):
        min_len = spec.get("minLength")
        max_len = spec.get("maxLength")
        if isinstance(min_len, int) and not isinstance(min_len, bool) \
                and len(value) < min_len:
            return fail(f"'{path}' must be at least {min_len} "
                        f"character(s) long (got {len(value)})")
        if isinstance(max_len, int) and not isinstance(max_len, bool) \
                and len(value) > max_len:
            return fail(f"'{path}' must be at most {max_len} "
                        f"character(s) long (got {len(value)})")
        pattern = spec.get("pattern")
        if isinstance(pattern, str):
            import re as _re

            try:
                compiled = _re.compile(pattern)
            except _re.error:
                return fail(f"unsupported schema for '{path}': "
                            f"invalid 'pattern' {pattern!r}")
            if not compiled.search(value):
                return fail(f"'{path}' does not match pattern "
                            f"{pattern!r} (got {value!r})")
    if isinstance(value, list):
        min_items = spec.get("minItems")
        max_items = spec.get("maxItems")
        if isinstance(min_items, int) \
                and not isinstance(min_items, bool) \
                and len(value) < min_items:
            return fail(f"'{path}' must contain at least {min_items} "
                        f"item(s) (got {len(value)})")
        if isinstance(max_items, int) \
                and not isinstance(max_items, bool) \
                and len(value) > max_items:
            return fail(f"'{path}' must contain at most {max_items} "
                        f"item(s) (got {len(value)})")
        if spec.get("uniqueItems") is True:
            for i in range(len(value)):
                for j in range(i + 1, len(value)):
                    if _json_equal(value[i], value[j]):
                        return fail(f"'{path}' must not contain "
                                    "duplicate items")
        items = spec.get("items")
        if isinstance(items, (dict, bool)):
            for i, item in enumerate(value):
                problem = _check_value(name, f"{path}[{i}]", item,
                                       items)
                if problem is not None:
                    return problem
        elif isinstance(items, list):
            for i, item_spec in enumerate(items):
                if i >= len(value):
                    break
                problem = _check_value(name, f"{path}[{i}]",
                                       value[i], item_spec)
                if problem is not None:
                    return problem
            if len(value) > len(items):
                extra = spec.get("additionalItems")
                if extra is False:
                    return fail(f"'{path}' has more items than its "
                                "schema allows")
                if isinstance(extra, (dict, bool)):
                    for i in range(len(items), len(value)):
                        problem = _check_value(
                            name, f"{path}[{i}]", value[i], extra)
                        if problem is not None:
                            return problem
                elif "additionalItems" in spec:
                    return fail(f"invalid schema for '{path}': "
                                "'additionalItems' must be a boolean "
                                "or a schema")
        elif "items" in spec:
            return fail(f"invalid schema for '{path}': "
                        "'items' must be a schema or a list of "
                        "schemas")
    if isinstance(value, dict):
        properties = spec.get("properties") or {}
        if not isinstance(properties, dict):
            return fail(f"unsupported schema for '{path}': "
                        "'properties' must be an object")
        # LOG-05: required is judged as a whole before any member
        # is used — a non-string member used to raise TypeError
        # (unhashable) out of the membership test below, and a
        # duplicate used to pass silently. Both are invalid
        # schema now, rejected cleanly, never raised.
        required = spec.get("required", [])
        if (not isinstance(required, list)
                or any(not isinstance(f, str) for f in required)
                or len(set(required)) != len(required)):
            return fail(f"invalid schema for '{path}': "
                        "'required' must be a list of unique strings")
        for field in required:
            if field not in value:
                child = f"{path}.{field}" if path else field
                return fail(f"'{child}' is required but was "
                            "not provided")
        min_props = spec.get("minProperties")
        max_props = spec.get("maxProperties")
        if isinstance(min_props, int) \
                and not isinstance(min_props, bool) \
                and len(value) < min_props:
            return fail(f"'{path or name}' must have at least "
                        f"{min_props} propert(ies) (got {len(value)})")
        if isinstance(max_props, int) \
                and not isinstance(max_props, bool) \
                and len(value) > max_props:
            return fail(f"'{path or name}' must have at most "
                        f"{max_props} propert(ies) (got {len(value)})")
        for field, item in value.items():
            child_path = f"{path}.{field}" if path else field
            if field in properties:
                problem = _check_value(name, child_path, item,
                                       properties[field])
                if problem is not None:
                    return problem
                continue
            extra = spec.get("additionalProperties")
            if extra is False:
                return fail(f"'{child_path}' is not a declared "
                            f"argument for tool '{name}' "
                            "(additionalProperties is false)")
            if isinstance(extra, (dict, bool)):
                problem = _check_value(name, child_path, item, extra)
                if problem is not None:
                    return problem
            elif "additionalProperties" in spec:
                return fail(f"invalid schema for '{path}': "
                            "'additionalProperties' must be a "
                            "boolean or a schema")
            # additionalProperties absent or true: the JSON
            # Schema default — the extra passes (see A4 note).
    return None


def _validate_args_against_schema(name: str, args: Any,
                                  schema: dict[str, Any]) -> str | None:
    """Strict argument validation against one JSON-schema subset
    (v5.55, HE-1; widened by audit fix A4, P1-2): required
    fields (root and nested), the primitive types (string /
    number / integer / boolean / object / array / null, or a
    list of them), enum membership under strict JSON equality
    (True never satisfies 1, 1 never satisfies a boolean),
    const, numeric bounds (minimum / maximum /
    exclusiveMinimum / exclusiveMaximum / multipleOf), string
    length and pattern, array length / uniqueness / item types
    (single schema and tuple form), object property counts,
    and additionalProperties (false rejects extras, a schema
    validates them, absent/true allows them — the JSON Schema
    default). A schema keyword outside the supported set
    rejects the call with an unsupported-keyword error, never
    a silent accept. Body moved verbatim from
    ToolRegistry.validate_args, which now delegates here with
    the registry declaration; the MCP branch passes the MCP
    tool's inputSchema. One validator, so an MCP argument can
    never be judged by a looser rule than a native one.
    Explicit null passes only where the schema allows null;
    a spec with no type / enum / const constrains nothing.
    Logic audit L2: the schema's own shape is validated
    FIRST (see _validate_schema) — a malformed keyword value
    rejects the call as an invalid schema instead of being
    silently ignored into a less restrictive schema."""
    if not isinstance(args, dict):
        return (f"Invalid arguments for tool '{name}': arguments must "
                f"be an object (got {type(args).__name__})")
    if not isinstance(schema, dict):
        return (f"Invalid arguments for tool '{name}': schema is "
                "not an object")
    schema_problem = _validate_schema(name, "", schema)
    if schema_problem is not None:
        return schema_problem
    return _check_value(name, "", args, schema)


def _failsafe_state():
    """Tri-state failsafe consult (audit fix A2, P1-1).

    Returns (FailsafeState, reason): CLEAR = not tripped;
    TRIPPED = tripped, reason = trip reason (may be ""); 
    UNAVAILABLE = the consult itself failed (import/call/
    reason error) — never "not tripped", never "disabled".
    Both funnels use this: TRIPPED and UNAVAILABLE refuse
    non-read tools alike; reads stay exempt in both cases
    (R5: reads run so a human triaging keeps her eyes).
    """
    try:
        from .. import failsafe as _failsafe  # Phase 3 F3: file now in core/funnel/; import climbs one more level

        if bool(_failsafe.tripped()):
            try:
                reason = str(_failsafe.trip_reason() or "")
            except Exception:
                reason = ""
            return FailsafeState.TRIPPED, reason
        # GAPS-24 (K-V3): the stop-flag file is the headless
        # escape hatch — the GUI monitor thread is the only
        # thing that would notice it, and headless there is no
        # monitor thread at all, so a headless user has no panic
        # stop unless the funnel consults the file HERE, on
        # every call. A present flag reads as TRIPPED (the
        # monitor thread trips on it too when the GUI runs, so
        # the two paths agree). A flag check that itself fails
        # reads as CLEAR, never UNAVAILABLE — the flag is
        # best-effort evidence of a trip, not the consult.
        try:
            if _failsafe.stop_flag_present():
                return FailsafeState.TRIPPED, (
                    "stop flag file present (%s)"
                    % _failsafe.stop_flag_path())
        except Exception:  # noqa: BLE001 — see above
            pass
        return FailsafeState.CLEAR, ""
    except Exception as exc:  # noqa: BLE001 — consult failure
        return FailsafeState.UNAVAILABLE, type(exc).__name__
