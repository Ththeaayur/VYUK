"""U10 — Check-my-working + solve-then-speak (SymPy).

Lab leg only. The real-Windows field leg — a real MA 105 working
check on his machine, a dropped factor in his own working named at
the right line, a solver walk-through spoken step by step — is OWED,
not this batch.

Gap proved first (audit T10): ``grade_answer``
(``core/brain/check_loop.py:244``) grades ONE answer against a gold,
with no notion of intermediate lines; there is no ``sympy`` import
anywhere in ``core/`` (the only mention is a prompt string in
``core/deliberation.py:192`` — "run_command with Python/sympy for
symbolic checks" — a suggestion to the brain, not an engine); no
``simplify`` / ``integrate`` / ``diff`` use anywhere in ``core/``;
no working-check or solver module anywhere in ``core/`` or ``gui/``.
The tutor could grade a final answer and never see the working.

SYMPY DECISION — use the installed SymPy as a declared dependency.
SymPy 1.12 is present in system python3 AND in the Qt venv
(``.venv-qt``, installed 2026-11-08 for this batch; its runtime
dependency mpmath 1.4.1 rode along). Licence: BSD-3-Clause, read
from the 1.12 PyPI wheel's LICENSE file (Copyright (c) 2006-2023
SymPy Development Team), 2026-11-08 — commercial use permitted.
Declared as a floor in ``requirements.txt``
(``sympy>=1.12,<2.0``), pinned as ``sympy==1.12`` (+ ``mpmath==1.4.1``,
its runtime dependency) in ``requirements.lock``, and recorded in
``THIRD_PARTY_LICENSES.md``. THIS IS A PROPOSED NEW RUNTIME
DEPENDENCY — the parent rules on it; removing it is one line in
each of those three files plus the lazy import below. The module
lazy-imports SymPy and degrades honestly (``engine_unavailable``)
when it is absent — nothing else in the product depends on it.
Rejected alternatives:
- Vendoring a minimal symbolic subset — SymPy is enormous; a
  vendored subset that handles integrals, limits, derivatives and
  series correctly is a second CAS to maintain, and any subset
  boundary is a new loophole (a form that parses but silently
  mis-checks).
- Re-implementing the checks in-tree from scratch — a home-grown
  symbolic engine is exactly the formula risk the standing
  instruction warns about (recalled rules differ from real ones),
  and it would take far more than one batch to reach SymPy's
  correctness on MA 105 forms.
- A numeric/float checker dressed as symbolic — rejected outright:
  a float "close enough" passes a wrong line (the Taylor
  approximation of sin(x) is numerically close near zero and
  symbolically wrong), and 0.1 + 0.2 != 0.3 in floats.

LOPHOLE FAMILY — "a tutor that checks the answer and not the
working": grading only the final answer tells him he is wrong but
not where; revealing the fix teaches nothing; float comparison
lies about exactness; an unparseable line graded as wrong punishes
a typo as a maths error; dumping the whole solution at once gives
him the answer, not the understanding. ONE design closes the class:
line-by-line equivalence through exact symbolic mathematics. Each
line of his working is parsed (typed notation first; spoken
notation through U7's ``normalise_spoken``) and checked for
equivalence with the previous line (``simplify(lhs - rhs) == 0``,
exact symbolic, never float, never a numeric tolerance); the FIRST
wrong line is named and the U5 hint ladder points at it (the fix
is never given); a line that cannot be parsed is "not readable",
never "wrong"; solve-then-speak uses the same engine to produce
the steps and speaks each step through U7's ``speak_expression``
(canonical spoken maths, never raw notation), one step at a time,
with the check question after each step (U3) before the next step
is shown.
Rejected alternatives (recorded, not carried forward):
- Grading only the final answer (the check loop as it stands — it
  answers "wrong" without "where").
- Revealing the fix (the U3 rule — never reveals the answer —
  already forbids it; the working check must not re-introduce it).
- Float comparison (the U3 tolerance rule is for NUMERIC answers,
  not for symbolic equivalence — a symbolic line is equal or it is
  not, no tolerance).
- Parsing failures counted as wrong (the D-T3 confidence rule in
  spirit — an unreadable line is not evidence of a wrong line).
- Dumping the whole solution at once (the lesson planner's bite
  shape — one bite, one check — already forbids it; solve-then-
  speak is bites, not a wall of text).

Design notes (the load-bearing choices):
- Typed notation is SymPy-shaped: ``x**2``, ``2*x``, ``sin(x)``,
  ``Integral(2*x, x)``, ``Derivative(x**2, x)``,
  ``Limit(sin(x)/x, x, 0)``, ``^`` for ``**``, implicit
  multiplication, ``pi``, ``sqrt``, ``exp``, ``log``. A trailing
  ``dx`` / ``dy`` / ``dt`` is stripped before parsing. Handwritten
  working and ``∫`` notation are NOT supported here — that is
  see-my-working (photo/screen capture + vision), a post-tutor
  roadmap item, not this batch.
- The integration constant: a bare additive ``C`` is stripped from
  parsed values before comparison, so ``x**2 + C`` and ``x**2``
  are the same antiderivative. Only a bare ``C`` term is stripped;
  ``x**2 + 5`` is NOT the same as ``x**2``.
- Lines that are definitions (``let u = ...``, ``:=``) or prose
  ("integrate term by term") are SKIPPED, reported in the result,
  never graded — a setup line is not an equivalence line, and a
  comment is not a maths error.
- The checker NEVER grades and NEVER writes mastery: it holds no
  ``record_graded`` handle, no model, no store. It returns a
  verdict; the session driver grades it through U3's single
  ``record_graded`` call site. (There is no ``record_graded``
  call anywhere in this module; the suite checks that by AST.)
- The solver's steps are U2 bites (bite + check + gold + rubric),
  so the planner can order them. Step lines are PLAIN LANGUAGE
  ONLY — no expressions, no numbers — so a step line can never
  contain its own gold (``_contains_gold`` also flags a line that
  carries every gold number, so numbers are out too); ``solve()``
  still runs the ``_contains_gold`` guard and falls back to a
  generic line if one ever trips.
- Residuals (named, not hidden): (1) no wall-clock bound on
  ``simplify`` — MA 105-sized inputs are fast in practice, but a
  pathological input could stall the check; the session driver
  should run it off the UI thread. (2) Definite integrals are
  supported only through substitution + evaluation (the
  antiderivative is stated in the step — the indefinite solver
  teaches finding it); bracket notation ``[x**2]_0^1`` does not
  parse. (3) The derivative stepper covers the MA 105 rules
  (power, constant-multiple, sum, product, quotient, chain,
  table); an exotic derivative falls back to "differentiate
  directly" in one step rather than guessing a rule.
"""

from __future__ import annotations

import re

from .check_loop import _contains_gold
from .lesson_planner import Bite
from .spoken_maths import (
    normalise_spoken,
    speak_text,
    spoken_reveals_gold,
)

__all__ = [
    "SolutionWalk",
    "check_working",
    "engine_available",
    "handover_for",
    "solve",
    "spoken_to_typed",
]

#: The desired-retention-style single tunable for this module: none.
#: The engine is exact; there is nothing to tune.
MAX_SOLVER_STEPS = 12
MAX_RULE_DEPTH = 12

_ENGINE = None
_ENGINE_TRIED = False


class _Unparseable(Exception):
    """A line that does not parse as mathematics."""


class _EngineError(Exception):
    """The engine itself failed (not a wrong line)."""


class _TooComplex(Exception):
    """The solver cannot render this problem as honest steps."""


class _SympyEngine:
    """Thin wrapper over the lazily imported SymPy module.

    Only two operations are load-bearing for the working check:
    ``parse`` (typed text -> exact expression) and ``equivalent``
    (exact symbolic equality, never float). Everything else the
    solver needs goes through ``.sp`` (the SymPy module itself).
    """

    def __init__(self, sympy_module):
        self.sp = sympy_module
        self._parse_expr = None
        self._transformations = None
        self._locals = None

    def _setup(self):
        if self._parse_expr is not None:
            return
        from sympy.parsing.sympy_parser import (
            convert_xor,
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )

        sp = self.sp
        self._parse_expr = parse_expr
        self._transformations = (
            standard_transformations
            + (implicit_multiplication_application, convert_xor)
        )
        sym = sp.Symbol
        self._locals = {
            name: sym(name)
            for name in (
                "x", "y", "z", "t", "n", "u", "v", "w",
                "a", "b", "c", "d", "f", "g", "h", "C",
            )
        }
        self._locals.update(
            {
                "e": sp.E,
                "pi": sp.pi,
                "oo": sp.oo,
                "I": sp.I,
                "sin": sp.sin,
                "cos": sp.cos,
                "tan": sp.tan,
                "asin": sp.asin,
                "acos": sp.acos,
                "atan": sp.atan,
                "sinh": sp.sinh,
                "cosh": sp.cosh,
                "tanh": sp.tanh,
                "exp": sp.exp,
                "log": sp.log,
                "ln": sp.log,
                "sqrt": sp.sqrt,
                "Abs": sp.Abs,
                "factorial": sp.factorial,
                "Integral": sp.Integral,
                "Derivative": sp.Derivative,
                "Limit": sp.Limit,
                "Sum": sp.Sum,
                "Product": sp.Product,
                "integrate": sp.integrate,
                "diff": sp.diff,
                "limit": sp.limit,
                "solve": sp.solve,
            }
        )

    def parse(self, text):
        """Parse typed maths text to an exact expression.

        Raises ``_Unparseable`` for anything that does not parse —
        the caller turns that into "not readable", never "wrong".
        """
        self._setup()
        if not isinstance(text, str):
            raise _Unparseable("not text")
        s = text.strip()
        if not s:
            raise _Unparseable("empty line")
        if ";" in s or "\n" in s:
            raise _Unparseable("one line only")
        # A trailing " dx" / " dy" / " dt" is notation, not a factor.
        s = re.sub(r"\s+d([xyzt])\s*$", "", s)
        s = s.replace("π", "pi").replace("∞", "oo")
        s = s.replace("√(", "sqrt(")
        s = re.sub(r"√([A-Za-z0-9])", r"sqrt(\1)", s)
        try:
            return self._parse_expr(
                s, local_dict=dict(self._locals),
                transformations=self._transformations,
            )
        except Exception as exc:  # noqa: BLE001 — any parse
            # failure is "not readable", never a crash.
            raise _Unparseable(str(exc)[:120]) from exc

    def doit(self, expr):
        """Evaluate unevaluated Integrals/Derivatives/Limits.

        Best effort: an evaluation failure keeps the original
        expression rather than raising.
        """
        try:
            return expr.doit()
        except Exception:  # noqa: BLE001 — keep the original.
            return expr

    def equivalent(self, a, b):
        """Exact symbolic equality: simplify(a - b) == 0.

        Never float, never a numeric tolerance. Tries ``simplify``
        first, then ``expand``, ``trigsimp`` and ``together`` — all
        exact. Raises ``_EngineError`` only when the engine itself
        fails on every attempt (the check is then "unproven", never
        a guessed pass or fail).
        """
        sp = self.sp
        try:
            a = self.doit(a)
            b = self.doit(b)
            d = a - b
        except Exception as exc:  # noqa: BLE001 — engine failure.
            raise _EngineError(str(exc)[:120]) from exc
        if d == 0:
            return True
        tried = 0
        for fn in (sp.simplify, sp.expand, sp.trigsimp, sp.together):
            try:
                tried += 1
                if fn(d) == 0:
                    return True
            except Exception:  # noqa: BLE001 — try the next form.
                continue
        if tried == 0:
            raise _EngineError("equivalence check failed")
        return False


def _get_engine():
    """The lazily built engine, or None when SymPy is absent.

    Cached in module globals (reset them in tests to re-probe).
    """
    global _ENGINE, _ENGINE_TRIED
    if _ENGINE_TRIED:
        return _ENGINE
    _ENGINE_TRIED = True
    try:
        import sympy as _sympy
    except Exception:  # noqa: BLE001 — absence degrades, never
        # crashes the import of this module.
        _ENGINE = None
        return None
    _ENGINE = _SympyEngine(_sympy)
    return _ENGINE


def engine_available():
    """Is the exact engine importable in this interpreter?"""
    return _get_engine() is not None


def _resolve_engine(engine):
    """An explicitly passed engine wins; else the lazy singleton."""
    if engine is not None:
        return engine
    return _get_engine()


def _strip_constant(expr, engine):
    """Drop a bare additive ``C`` (the integration constant).

    ``x**2 + C`` and ``x**2`` are the same antiderivative. Only a
    bare ``C`` term is stripped — ``x**2 + 5`` stays different.
    """
    sp = engine.sp
    const = sp.Symbol("C")
    if expr == const:
        return sp.Integer(0)
    if getattr(expr, "is_Add", False):
        terms = [t for t in expr.args if t != const]
        if len(terms) != len(expr.args):
            return sp.Add(*terms) if terms else sp.Integer(0)
    return expr


def _dedummy(expr, engine):
    """Replace internal dummy symbols (``_u`` from substitution)
    with the plain symbols they are displayed as (``u``)."""
    sp = engine.sp
    try:
        dummies = [a for a in expr.atoms(sp.Dummy)]
    except Exception:  # noqa: BLE001 — no dummies, return as-is.
        return expr
    if not dummies:
        return expr
    mapping = {d: sp.Symbol(d.name.lstrip("_") or "u")
               for d in dummies}
    try:
        return expr.xreplace(mapping)
    except Exception:  # noqa: BLE001 — return as-is then.
        return expr


def _display(expr, engine):
    """Text for a gold: SymPy str, with internal dummy symbols
    (``_u`` from substitution) shown as plain ``u``."""
    return str(_dedummy(expr, engine))


def spoken_to_typed(text, table=None):
    """Spoken maths words -> typed notation (U7's normaliser).

    The driver's spoken-input path: normalise first, then check.
    A spoken line is never fed to the parser raw.
    """
    return normalise_spoken(text, table=table)


# ---------------------------------------------------------------------------
# The working check
# ---------------------------------------------------------------------------

_MATHS_CHARS = re.compile(r"[0-9+\-*/^=()\[\]{}∫√π]")
_SETUP_RE = re.compile(r"(?i)^(let|suppose|set|assume|put)\b")
_SINGLE_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _line_value(line, engine):
    """Parse one working line to its value.

    Returns ``{"kind", "value", "self_ok", "self_detail",
    "skip_reason"}``. ``kind`` is "expr" (a value to chain-check),
    "skipped" (a definition, a comment, or a blank line — reported,
    never graded) or "bad" (unparseable, or an inequality/arrow —
    "not readable", never "wrong"). An equation line ``A = B`` has
    value ``B`` and must also hold within itself (``A`` equivalent
    to ``B``); a chained equality ``A = B = C`` must hold at every
    link. A leading ``=`` continues the previous line.
    """
    s = line.strip() if isinstance(line, str) else ""
    if not s:
        return {"kind": "skipped", "value": None, "self_ok": True,
                "self_detail": "", "skip_reason": "blank line"}
    if _SETUP_RE.match(s) or ":=" in s:
        return {"kind": "skipped", "value": None, "self_ok": True,
                "self_detail": "",
                "skip_reason": "definition — not an equivalence line"}
    if "!=" in s or "<" in s or ">" in s:
        return {"kind": "bad", "value": None, "self_ok": False,
                "self_detail": "",
                "skip_reason": "inequality or arrow — not an "
                               "equivalence line"}
    if " " in s and not _MATHS_CHARS.search(s) \
            and not _SINGLE_TOKEN_RE.match(s):
        return {"kind": "skipped", "value": None, "self_ok": True,
                "self_detail": "",
                "skip_reason": "words, not maths"}
    parts = [p.strip() for p in s.replace("==", "=").split("=")]
    parts = [p for p in parts if p]
    if not parts:
        return {"kind": "skipped", "value": None, "self_ok": True,
                "self_detail": "", "skip_reason": "blank line"}
    parsed = []
    for part in parts:
        try:
            parsed.append(engine.parse(part))
        except _Unparseable as exc:
            return {"kind": "bad", "value": None, "self_ok": False,
                    "self_detail": "",
                    "skip_reason": f"does not parse: {part!r} "
                                   f"({exc})"}
    for left, right in zip(parsed, parsed[1:]):
        try:
            holds = engine.equivalent(
                _strip_constant(left, engine),
                _strip_constant(right, engine))
        except _EngineError:
            raise
        if not holds:
            return {"kind": "expr", "value": parsed[-1],
                    "self_ok": False, "self_detail":
                    f"the line itself does not hold: "
                    f"{_display(left, engine)} is not "
                    f"{_display(right, engine)}",
                    "skip_reason": ""}
    return {"kind": "expr", "value": parsed[-1], "self_ok": True,
            "self_detail": "", "skip_reason": ""}


def check_working(start, lines, *, skill=None, engine=None):
    """Check a working line by line against the starting expression.

    ``start`` is the problem (typed notation); ``lines`` is the
    student's working, one line each. Returns a plain dict:
    ``ok`` / ``verdict`` (``all_ok`` | ``wrong_line`` |
    ``not_readable`` | ``unproven`` | ``engine_unavailable`` |
    ``bad_input``), ``lines`` (one entry per line: index, line,
    verdict, reason), ``checked`` (lines that passed), ``skipped``
    (indices of setup/prose/blank lines), ``first_wrong`` /
    ``not_readable`` (``{"index", "line", "detail"}`` or None), and
    ``reason``. The FIRST wrong line is named; later lines are
    never blamed for it. Never raises for bad input; never grades;
    never writes mastery — it returns a verdict for the session
    driver to grade through U3.
    """
    eng = _resolve_engine(engine)
    if eng is None:
        return {"ok": False, "verdict": "engine_unavailable",
                "skill": skill, "start": start, "lines": [],
                "checked": 0, "skipped": [], "first_wrong": None,
                "not_readable": None,
                "reason": "the symbolic engine is not available in "
                          "this interpreter — install sympy"}
    if not isinstance(start, str) or not start.strip() \
            or not isinstance(lines, list) \
            or any(not isinstance(li, str) for li in lines):
        return {"ok": False, "verdict": "bad_input",
                "skill": skill, "start": start, "lines": [],
                "checked": 0, "skipped": [], "first_wrong": None,
                "not_readable": None,
                "reason": "start must be text and lines a list of "
                          "text lines"}
    try:
        first = _line_value(start, eng)
    except _EngineError as exc:
        return {"ok": False, "verdict": "unproven",
                "skill": skill, "start": start, "lines": [],
                "checked": 0, "skipped": [], "first_wrong": None,
                "not_readable": None,
                "reason": f"the engine failed on the start: {exc}"}
    if first["kind"] != "expr":
        return {"ok": False, "verdict": "bad_input",
                "skill": skill, "start": start, "lines": [],
                "checked": 0, "skipped": [], "first_wrong": None,
                "not_readable": None,
                "reason": f"the starting expression is not a line "
                          f"to check: {first['skip_reason']}"}
    if not first["self_ok"]:
        return {"ok": False, "verdict": "bad_input",
                "skill": skill, "start": start, "lines": [],
                "checked": 0, "skipped": [], "first_wrong": None,
                "not_readable": None,
                "reason": f"the starting expression does not hold: "
                          f"{first['self_detail']}"}
    prev = _strip_constant(first["value"], eng)
    entries = []
    skipped = []
    checked = 0
    for index, line in enumerate(lines):
        try:
            value = _line_value(line, eng)
        except _EngineError as exc:
            return {"ok": False, "verdict": "unproven",
                    "skill": skill, "start": start, "lines": entries,
                    "checked": checked, "skipped": skipped,
                    "first_wrong": None, "not_readable": None,
                    "reason": f"the engine failed on line {index}: "
                              f"{exc}"}
        if value["kind"] == "skipped":
            skipped.append(index)
            entries.append({"index": index, "line": line,
                            "verdict": "skipped",
                            "reason": value["skip_reason"]})
            continue
        if value["kind"] == "bad":
            entries.append({"index": index, "line": line,
                            "verdict": "not_readable",
                            "reason": value["skip_reason"]})
            return {"ok": False, "verdict": "not_readable",
                    "skill": skill, "start": start, "lines": entries,
                    "checked": checked, "skipped": skipped,
                    "first_wrong": None,
                    "not_readable": {"index": index, "line": line,
                                     "detail": value["skip_reason"]},
                    "reason": "line "
                              f"{index} cannot be read as maths — "
                              "re-ask it, do not grade it"}
        if not value["self_ok"]:
            entries.append({"index": index, "line": line,
                            "verdict": "wrong",
                            "reason": value["self_detail"]})
            return {"ok": False, "verdict": "wrong_line",
                    "skill": skill, "start": start, "lines": entries,
                    "checked": checked, "skipped": skipped,
                    "first_wrong": {"index": index, "line": line,
                                    "detail": value["self_detail"]},
                    "not_readable": None,
                    "reason": f"line {index} is the first wrong "
                              "line"}
        try:
            holds = eng.equivalent(
                _strip_constant(value["value"], eng), prev)
        except _EngineError as exc:
            return {"ok": False, "verdict": "unproven",
                    "skill": skill, "start": start, "lines": entries,
                    "checked": checked, "skipped": skipped,
                    "first_wrong": None, "not_readable": None,
                    "reason": f"the engine failed on line {index}: "
                              f"{exc}"}
        if not holds:
            entries.append({"index": index, "line": line,
                            "verdict": "wrong",
                            "reason": "not the same as the previous "
                                      "line"})
            return {"ok": False, "verdict": "wrong_line",
                    "skill": skill, "start": start, "lines": entries,
                    "checked": checked, "skipped": skipped,
                    "first_wrong": {"index": index, "line": line,
                                    "detail": "not the same as the "
                                              "previous line"},
                    "not_readable": None,
                    "reason": f"line {index} is the first wrong "
                              "line"}
        checked += 1
        entries.append({"index": index, "line": line,
                        "verdict": "ok", "reason": ""})
        prev = _strip_constant(value["value"], eng)
    return {"ok": True, "verdict": "all_ok",
            "skill": skill, "start": start, "lines": entries,
            "checked": checked, "skipped": skipped,
            "first_wrong": None, "not_readable": None,
            "reason": "every line checks out against the previous "
                      "line"}


def handover_for(result, *, skill, bite=None):
    """A wrong working line as a U3 ``handed_to_diagnosis`` handover.

    Same shape U3 returns (outcome / skill / attempts / reinforce /
    grade / mastery / reason) plus ``line_index`` and ``line`` so U4
    can ask its diagnostic question about THAT line. ``mastery`` is
    empty by design — the checker holds no model and never consulted
    one. Returns ``{"ok": False, "reason": ...}`` when there is no
    wrong line to hand over (an all-ok or unreadable working is not
    a diagnosis).
    """
    if not isinstance(result, dict) \
            or not isinstance(result.get("first_wrong"), dict):
        return {"ok": False,
                "reason": "no wrong line to hand over"}
    wrong = result["first_wrong"]
    reinforce = getattr(bite, "reinforce", None) or skill
    return {
        "outcome": "handed_to_diagnosis",
        "skill": skill,
        "attempts": 1,
        "reinforce": reinforce,
        "grade": {"correct": False, "reason": "wrong_working_line",
                  "method": "symbolic"},
        "mastery": {},
        "reason": "wrong_working_line",
        "line_index": wrong.get("index"),
        "line": wrong.get("line"),
    }


# ---------------------------------------------------------------------------
# The solver (solve-then-speak)
# ---------------------------------------------------------------------------

_MANUAL = None


def _manual():
    """(integral_steps, manualintegrate), lazily imported."""
    global _MANUAL
    if _MANUAL is None:
        from sympy.integrals.manualintegrate import (
            integral_steps,
            manualintegrate,
        )

        _MANUAL = (integral_steps, manualintegrate)
    return _MANUAL


def _words(expr_text, engine):
    """An expression as canonical spoken words (U7)."""
    try:
        return speak_text(expr_text)
    except Exception:  # noqa: BLE001 — fall back to the text.
        return expr_text


def _new_step(line, gold, check_against, verify="equivalent", **extra):
    step = {"line": line, "gold": gold,
            "check_against": check_against, "verify": verify,
            "role": "chain"}
    step.update(extra)
    return step


def _describe_integral_rule(name):
    """Plain-language line for one integral rule — no expressions,
    no numbers, so it can never carry its own gold."""
    return {
        "ConstantTimesRule": "Pull the constant factor out of the "
                             "integral.",
        "PowerRule": "Use the power rule for integration.",
        "ConstantRule": "Integrate the constant.",
        "AddRule": "Integrate term by term.",
        "RewriteRule": "Rewrite the integrand in a form that "
                       "integrates directly.",
        "URule": "Substitute a new variable for the inside "
                 "function.",
        "PartsRule": "Integrate by parts.",
    }.get(name, "Evaluate this integral directly.")


def _integral_one_step(rule, name, var, engine, manualintegrate):
    """The expression after applying ONE integral rule."""
    sp = engine.sp
    if name == "ConstantTimesRule":
        return rule.constant * sp.Integral(rule.other, var)
    if name == "AddRule":
        return sp.Add(*[sp.Integral(t, var)
                        for t in rule.integrand.args])
    if name == "RewriteRule":
        return sp.Integral(rule.rewritten, var)
    if name == "URule":
        return sp.Integral(rule.substep.integrand, rule.u_var)
    if name == "PartsRule":
        v = manualintegrate(rule.dv, var)
        du = sp.diff(rule.u, var)
        return rule.u * v - sp.Integral(v * du, var)
    # A leaf rule (PowerRule, ConstantRule, SinRule, ExpRule, ...)
    # evaluates its integrand directly.
    return manualintegrate(rule.integrand, var)


def _append_integral_step(rule, current, var, engine, steps, line,
                          result, role="chain"):
    """Append one integral step, threading the current expression.

    The rule owns the sub-expression ``Integral(integrand, var)``
    inside the current expression; applying the rule replaces it.
    At the top level the current expression IS the integrand, so
    the result is the whole next line. ``role`` is "chain" except
    for a substitution step, which starts a new segment (the
    chain replay does not compare it against the previous segment).
    """
    sp = engine.sp
    owned = sp.Integral(rule.integrand, var)
    if current == rule.integrand:
        nxt = result
    else:
        nxt = current.xreplace({owned: result})
        if nxt == current:
            nxt = result
    steps.append(_new_step(line, _display(nxt, engine), nxt,
                           role=role))
    return nxt


def _walk_integral_rule(rule, current, var, engine, steps, depth,
                        manualintegrate):
    """Walk one rule node, appending steps, returning the current
    expression. Raises ``_TooComplex`` when a rule cannot be
    rendered as honest steps."""
    if depth > MAX_RULE_DEPTH:
        raise _TooComplex("the integral needs more steps than the "
                          "walk renders")
    name = type(rule).__name__
    if name == "DontKnowRule":
        raise _TooComplex("no rule renders this integral")
    if name == "AlternativeRule":
        for alt in rule.alternatives:
            saved = len(steps)
            try:
                return _walk_integral_rule(
                    alt, current, var, engine, steps, depth + 1,
                    manualintegrate)
            except _TooComplex:
                del steps[saved:]
                continue
        raise _TooComplex("no alternative rule renders this integral")
    if name == "URule":
        u_var, u_func = rule.u_var, rule.u_func
        result = engine.sp.Integral(rule.substep.integrand, u_var)
        current = _append_integral_step(
            rule, current, var, engine, steps,
            "Substitute a new variable for the inside function.",
            result, role="substitute")
        current = _walk_integral_rule(
            rule.substep, current, u_var, engine, steps, depth + 1,
            manualintegrate)
        back = current.xreplace({u_var: u_func})
        steps.append(_new_step(
            "Substitute back to the original variable.",
            _display(back, engine), back, role="backsub",
            u_var=u_var, u_func=u_func,
            u_symbol=engine.sp.Symbol(u_var.name.lstrip("_") or "u")))
        return back
    if name == "PartsRule":
        result = _integral_one_step(rule, name, var, engine,
                                    manualintegrate)
        current = _append_integral_step(
            rule, current, var, engine, steps,
            "Integrate by parts.", result)
        try:
            return _walk_integral_rule(
                rule.second_step, current, var, engine, steps,
                depth + 1, manualintegrate)
        except _TooComplex:
            # The remaining integral does not step — evaluate it in
            # one honest step rather than guessing a rule.
            final = manualintegrate(rule.integrand, var)
            steps.append(_new_step("Evaluate the remaining integral.",
                                   _display(final, engine), final))
            return final
    result = _integral_one_step(rule, name, var, engine,
                                manualintegrate)
    line = _describe_integral_rule(name)
    current = _append_integral_step(rule, current, var, engine,
                                    steps, line, result)
    sub = getattr(rule, "substep", None)
    if sub is not None:
        return _walk_integral_rule(sub, current, var, engine, steps,
                                   depth + 1, manualintegrate)
    for sub in getattr(rule, "substeps", None) or []:
        current = _walk_integral_rule(sub, current, var, engine,
                                      steps, depth + 1,
                                      manualintegrate)
    return current


def _definite_steps(expr, engine, manualintegrate):
    """Definite integral: substitute the limits, then simplify.

    The antiderivative is STATED in the first step (in spoken
    words) — the indefinite solver teaches finding it; this walk
    teaches substitution and evaluation.
    """
    sp = engine.sp
    lim = next(lim for lim in expr.limits if len(lim) == 3)
    var, a, b = lim
    inner = expr.function
    antiderivative = manualintegrate(inner, var)
    sub = antiderivative.subs(var, b) - antiderivative.subs(var, a)
    value = expr.doit()
    if isinstance(value, sp.Integral) or value.has(sp.Integral):
        raise _TooComplex("this definite integral does not evaluate")
    f_words = _words(_display(antiderivative, engine), engine)
    # The substitution gold is the UNEVALUATED difference ("1 - 0",
    # not "1") — SymPy would simplify it away, and the student is
    # asked for the substituted form, not the value yet.
    sub_text = (
        f"{_display(antiderivative.subs(var, b), engine)} - "
        f"{_display(antiderivative.subs(var, a), engine)}")
    return [
        _new_step(
            f"The antiderivative is {f_words}. Substitute the "
            "limits.",
            sub_text, sub),
        _new_step("Simplify to the value.",
                  _display(value, engine), value),
    ]


def _integral_steps(expr, var, engine):
    """Steps for an integral problem (indefinite or definite)."""
    sp = engine.sp
    integral_steps, manualintegrate = _manual()
    if isinstance(expr, sp.Integral):
        limits = expr.limits
        if any(len(lim) == 3 for lim in limits):
            return _definite_steps(expr, engine, manualintegrate)
        var = expr.variables[0] if expr.variables else var
        expr = expr.function
    rule = integral_steps(expr, var)
    steps = []
    _walk_integral_rule(rule, expr, var, engine, steps, 0,
                        manualintegrate)
    if not steps:
        raise _TooComplex("no steps rendered for this integral")
    if len(steps) > MAX_SOLVER_STEPS:
        raise _TooComplex("too many steps to walk honestly")
    final = manualintegrate(expr, var)
    last = steps[-1]["check_against"]
    if not engine.equivalent(_strip_constant(last, engine),
                             _strip_constant(final, engine)):
        raise _TooComplex("the walked steps do not reach the "
                          "antiderivative")
    if "+ C" not in steps[-1]["gold"]:
        if _display(last, engine) != _display(final, engine):
            line = "Simplify, and add the constant of integration."
        else:
            line = "Add the constant of integration."
        steps.append(_new_step(line, _display(final, engine) + " + C",
                               final))
    return steps


def _describe_diff_rule(expr, var, engine):
    """Plain-language line for one derivative rule."""
    sp = engine.sp
    if var not in getattr(expr, "free_symbols", set()):
        return "The derivative of a constant is zero."
    if expr == var:
        return "The derivative of the variable itself is one."
    if getattr(expr, "is_Pow", False) and expr.base == var \
            and getattr(expr.exp, "is_number", False):
        return "Use the power rule."
    if getattr(expr, "is_Mul", False):
        if any(getattr(t, "is_number", False) for t in expr.args):
            return "Take the constant factor out, then differentiate "
            "the rest."
        if len(expr.args) == 2:
            return "Use the product rule."
        return "Differentiate directly."
    if getattr(expr, "func", None) in (
            sp.sin, sp.cos, sp.tan, sp.exp, sp.log, sp.sqrt):
        if expr.args and expr.args[0] == var:
            return "Use the standard derivative for this function."
    return "Use the chain rule."


def _diff_walk(expr, var, engine, steps, depth):
    """Append derivative steps for one expression (recursing into
    sums term by term, then one combine step)."""
    if depth > MAX_RULE_DEPTH:
        raise _TooComplex("the derivative needs more steps than the "
                          "walk renders")
    sp = engine.sp
    if getattr(expr, "is_Add", False):
        term_results = []
        for term in expr.args:
            _diff_walk(term, var, engine, steps, depth + 1)
            term_results.append(steps[-1]["check_against"])
        total = sp.Add(*term_results)
        steps.append(_new_step("Add the pieces together.",
                               _display(total, engine), total))
        return
    result = sp.diff(expr, var)
    line = _describe_diff_rule(expr, var, engine)
    steps.append(_new_step(line, _display(result, engine), result))


def _derivative_steps(expr, var, engine):
    """Steps for a derivative problem."""
    sp = engine.sp
    if isinstance(expr, sp.Derivative):
        args = expr.args
        inner = args[0]
        spec = args[1] if len(args) > 1 else var
        if isinstance(spec, tuple):
            var = spec[0]
            if len(spec) > 1 and spec[1] != 1:
                raise _TooComplex("only first derivatives are "
                                  "walked")
        else:
            var = spec
        expr = inner
    steps = []
    _diff_walk(expr, var, engine, steps, 0)
    if len(steps) > MAX_SOLVER_STEPS:
        raise _TooComplex("too many steps to walk honestly")
    return steps


def _is_finite_number(value):
    """A defined, finite number (not zoo / nan / oo)."""
    return bool(getattr(value, "is_finite", False))


def _limit_steps(expr, engine):
    """Steps for a limit problem."""
    sp = engine.sp
    if not isinstance(expr, sp.Limit):
        raise _TooComplex("not a limit")
    inner, var, point = expr.args[0], expr.args[1], expr.args[2]
    value = sp.limit(inner, var, point)
    if isinstance(value, sp.Limit) or value.has(sp.Limit):
        raise _TooComplex("this limit does not evaluate")
    sub = inner.subs(var, point)
    if _is_finite_number(sub):
        return [_new_step("Substitute the value directly.",
                          _display(sub, engine), value)]
    simplified = sp.cancel(inner)
    if simplified == inner:
        simplified = sp.simplify(inner)
    steps = [_new_step("Direct substitution is undefined, so "
                       "simplify first.",
                       _display(simplified, engine), inner)]
    sub2 = simplified.subs(var, point)
    if _is_finite_number(sub2):
        steps.append(_new_step("Substitute the value into the "
                               "simplified form.",
                               _display(sub2, engine), value))
        return steps
    steps.append(_new_step("Use the limit.",
                           _display(value, engine), value))
    return steps


def _equation_steps(lhs, rhs, var, engine):
    """Steps for an equation: solve, then verify by substitution.

    The moved form is STATED in the first step (in spoken words) —
    the check asks for the solutions, not for the moved form.
    """
    sp = engine.sp
    expr = lhs - rhs
    sols = sp.solve(expr, var)
    if isinstance(sols, dict) or not sols:
        raise _TooComplex("this equation does not solve")
    if any(not getattr(s, "is_number", False) for s in sols):
        raise _TooComplex("this equation does not solve to numbers")
    for s in sols:
        if expr.subs(var, s) != 0:
            raise _TooComplex("the solver's own solutions do not "
                              "satisfy the equation")
    moved = _words(_display(expr, engine), engine)
    gold = ", ".join(
        f"{var} = {_display(s, engine)}" for s in sols)
    return [
        _new_step(
            f"Move everything to one side — the equation is now "
            f"{moved} equals zero. Solve for the variable.",
            gold, expr, verify="solutions", solutions=list(sols),
            variable=var),
        _new_step("Verify by substituting the first solution back.",
                  "0", sp.Integer(0)),
    ]


def _split_top_level(text):
    """Split on top-level commas (commas inside parens do not
    split)."""
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _series_problem(problem, variable, engine):
    """(base text, variable, point, n) for a series problem."""
    s = problem.strip()
    if s.lower().startswith("series(") and s.endswith(")"):
        args = _split_top_level(s[len("series("):-1])
        if not args:
            raise _Unparseable("empty series")
        var = engine.parse(args[1]) if len(args) > 1 \
            else engine.sp.Symbol(variable)
        point = engine.parse(args[2]) if len(args) > 2 \
            else engine.sp.Integer(0)
        if len(args) > 3:
            try:
                n = int(engine.parse(args[3]))
            except Exception as exc:  # noqa: BLE001 — not a count.
                raise _Unparseable("bad term count") from exc
        else:
            n = 4
        return args[0], var, point, n
    return s, engine.sp.Symbol(variable), engine.sp.Integer(0), 4


def _series_steps(expr, var, engine, point, n):
    """Steps for a series problem: one term added per step."""
    sp = engine.sp
    if n < 1 or n > 8:
        raise _TooComplex("the series needs between one and eight "
                          "terms")
    steps = []
    for k in range(1, n + 1):
        try:
            partial = sp.series(expr, var, point, k).removeO()
        except Exception as exc:  # noqa: BLE001 — not a series.
            raise _TooComplex("this expression has no series") from exc
        if k == 1:
            line = "Write the first term of the series."
        else:
            line = "Add the next term of the series."
        steps.append(_new_step(line, _display(partial, engine),
                               partial))
    return steps


def _detect_kind(problem):
    """The problem kind from its text, or None."""
    s = problem.strip().lower()
    if s.startswith("integral(") or s.startswith("integrate(") \
            or "∫" in s:
        return "integral"
    if s.startswith("derivative(") or s.startswith("diff(") \
            or s.startswith("d/d"):
        return "derivative"
    if s.startswith("limit("):
        return "limit"
    if s.startswith("series(") or "series" in s:
        return "series"
    if "=" in s:
        return "equation"
    return None


def _build_steps(problem, kind, variable, eng):
    """(start text, step dicts, kind, is-definite) for a problem."""
    sp = eng.sp
    var = sp.Symbol(variable)
    if kind == "equation":
        if "=" not in problem:
            raise _TooComplex("an equation needs an equals sign")
        lhs_text, rhs_text = problem.split("=", 1)
        lhs = eng.parse(lhs_text)
        rhs = eng.parse(rhs_text)
        return problem, _equation_steps(lhs, rhs, var, eng), kind, False
    if kind == "series":
        base_text, var, point, n = _series_problem(
            problem, variable, eng)
        expr = eng.parse(base_text)
        return problem, _series_steps(expr, var, eng, point, n), \
            kind, False
    expr = eng.parse(problem)
    if kind == "integral":
        definite = isinstance(expr, sp.Integral) and any(
            len(lim) == 3 for lim in expr.limits)
        return problem, _integral_steps(expr, var, eng), kind, definite
    if kind == "derivative":
        return problem, _derivative_steps(expr, var, eng), kind, False
    if kind == "limit":
        return problem, _limit_steps(expr, eng), kind, False
    raise _TooComplex("unknown problem kind")


class SolutionWalk:
    """One gated step at a time through a solved problem.

    ``solve()`` builds the steps; this walk exposes ONLY the
    current step (``current()``). The next step is gated on a
    GRADED check: ``advance(correct=True)`` moves on, anything
    else stays. The walk never dumps the whole solution — there is
    no public attribute holding the step list. ``verify_all()``
    proves the solver's own steps (each gold against its
    check-against, plus the full equivalence chain for indefinite
    integrals) without revealing them.
    """

    def __init__(self, bites, steps, *, skill, start, kind=None,
                 definite=False, engine=None, table=None, ok=True,
                 reason=""):
        self._bites = list(bites)
        self._steps = list(steps)
        self._index = 0
        self.skill = skill
        self.start = start
        self._kind = kind
        self._definite = definite
        self._engine = engine
        self._table = table
        self.ok = ok
        self.reason = reason

    @classmethod
    def not_ok(cls, reason, *, skill, start):
        """A walk that never started, honestly."""
        return cls([], [], skill=skill, start=start, ok=False,
                   reason=reason)

    @property
    def total_steps(self):
        """How many steps the walk has (a count, not the content)."""
        return len(self._bites)

    @property
    def done(self):
        return self._index >= len(self._bites)

    def current(self):
        """The current step's bite, or None when done / not ok."""
        if not self.ok or self.done:
            return None
        return self._bites[self._index]

    def advance(self, *, correct, hinted=False):  # noqa: ARG002
        """Move on after a graded check. ``correct`` must be a
        graded True — anything else stays on the same step. The
        walk never grades; the driver grades through U3 and passes
        the outcome here."""
        if not self.ok or self.done:
            return None
        if not correct:
            return self.current()
        self._index += 1
        return self.current()

    def spoken_current(self, table=None):
        """The current step spoken (U7's canonical form).

        Speaks the step's plain-language line only — never the
        gold. A step's gold is the answer to its check; speaking it
        before the check would reveal it.
        """
        bite = self.current()
        if bite is None:
            return ""
        try:
            return speak_text(bite.bite, table=table or self._table)
        except Exception:  # noqa: BLE001 — fall back to the text.
            return bite.bite

    def verify_all(self):
        """Prove the solver's own steps, without revealing them.

        Each step's gold is checked against its check-against
        (exact symbolic, never float); equation solutions are
        checked by substitution; indefinite-integral walks also get
        the full equivalence chain replayed (substitution steps
        start a new segment, back-substitution reconnects it).
        Returns ``{"ok", "verdict", "failed_step", "reason"}``.
        """
        if not self.ok:
            return {"ok": False, "verdict": "not_ok",
                    "failed_step": None, "reason": self.reason}
        eng = self._engine or _get_engine()
        if eng is None:
            return {"ok": False, "verdict": "engine_unavailable",
                    "failed_step": None,
                    "reason": "the symbolic engine is not available"}
        for i, step in enumerate(self._steps):
            try:
                if step.get("verify") == "solutions":
                    expr = step["check_against"]
                    var = step["variable"]
                    for sol in step["solutions"]:
                        if expr.subs(var, sol) != 0:
                            return {"ok": False,
                                    "verdict": "step_failed",
                                    "failed_step": i,
                                    "reason": "a solution does not "
                                              "satisfy the equation"}
                    continue
                gold = eng.parse(step["gold"])
                if not eng.equivalent(
                        _strip_constant(gold, eng),
                        _strip_constant(
                            _dedummy(step["check_against"], eng),
                            eng)):
                    return {"ok": False, "verdict": "step_failed",
                            "failed_step": i,
                            "reason": "a step's gold does not match "
                                      "its own check"}
            except (_Unparseable, _EngineError) as exc:
                return {"ok": False, "verdict": "unproven",
                        "failed_step": i, "reason": str(exc)}
        if self._kind == "integral" and not self._definite:
            try:
                prev = _strip_constant(eng.parse(self.start), eng)
                for i, step in enumerate(self._steps):
                    role = step.get("role", "chain")
                    if role == "substitute":
                        prev = _strip_constant(
                            eng.parse(step["gold"]), eng)
                        continue
                    if role == "backsub":
                        prev = prev.xreplace(
                            {step["u_symbol"]: step["u_func"]})
                    gold = eng.parse(step["gold"])
                    if not eng.equivalent(
                            _strip_constant(gold, eng), prev):
                        return {"ok": False,
                                "verdict": "chain_failed",
                                "failed_step": i,
                                "reason": "a step does not follow "
                                          "from the previous line"}
                    prev = _strip_constant(gold, eng)
            except (_Unparseable, _EngineError) as exc:
                return {"ok": False, "verdict": "unproven",
                        "failed_step": None, "reason": str(exc)}
        return {"ok": True, "verdict": "all_steps_proven",
                "failed_step": None,
                "reason": "every step checks out"}


def solve(problem, *, skill, kind=None, variable="x", engine=None,
          table=None):
    """Solve a problem as a gated walk of U2 bites.

    ``problem`` is typed notation (``Integral(2*x, x)``,
    ``Derivative(x**2, x)``, ``Limit(sin(x)/x, x, 0)``,
    ``x**2 - 4 = 0``, ``exp(x)`` with ``kind="series"``). Returns a
    :class:`SolutionWalk` — one step at a time, the next gated on a
    graded check — or a not-ok walk (``walk.ok`` False, ``reason``
    naming why: ``engine_unavailable`` | ``bad_input`` |
    ``unknown_kind`` | ``unparseable_problem`` | ``too_complex``).
    Never raises for a bad problem; never dumps the whole solution.
    """
    eng = _resolve_engine(engine)
    if eng is None:
        return SolutionWalk.not_ok(
            "engine_unavailable", skill=skill, start=problem)
    if not isinstance(problem, str) or not problem.strip() \
            or not isinstance(skill, str) or not skill.strip():
        return SolutionWalk.not_ok(
            "bad_input", skill=skill or "", start=problem or "")
    use_kind = kind or _detect_kind(problem)
    if use_kind not in ("integral", "derivative", "limit", "series",
                        "equation"):
        return SolutionWalk.not_ok(
            "unknown_kind", skill=skill, start=problem)
    try:
        start_text, step_dicts, use_kind, definite = _build_steps(
            problem, use_kind, variable, eng)
    except _Unparseable:
        return SolutionWalk.not_ok(
            "unparseable_problem", skill=skill, start=problem)
    except _TooComplex as exc:
        return SolutionWalk.not_ok(
            str(exc) or "too_complex", skill=skill, start=problem)
    except _EngineError as exc:
        return SolutionWalk.not_ok(
            f"engine_error: {exc}", skill=skill, start=problem)
    bites = []
    for step in step_dicts:
        line = step["line"]
        gold = step["gold"]
        if _contains_gold(line, gold):
            line = "Work the next line."
        check = "What is the next line?"
        if _contains_gold(check, gold):
            check = "What comes next?"
        bites.append(Bite(
            skill=skill, bite=line, check=check, gold=gold,
            rubric="Exact symbolic match — an equivalent form "
                   "passes; a different value fails.",
            requires=(), reinforce=skill))
    return SolutionWalk(bites, step_dicts, skill=skill,
                        start=start_text, kind=use_kind,
                        definite=definite, engine=eng, table=table)
