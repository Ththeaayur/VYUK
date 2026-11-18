"""Contained runtime for skill code — Wasmtime WASM pilot (SK4-05).

The gap it closes (capability audit B8): skills were readable as
data only. ``core/skill_gate.py`` provides NO path that runs skill
code, and ``core/skill_gate_helpers.py`` refused execution because
the contained runtimes were Wave 24 builds "not available yet".
This module is the first contained runtime: ONE pilot, Wasmtime
WASM only. The AppContainer broker (SK4-07) is Windows-only and
cannot be built or tested in this Linux lab — it stays a Wave 24
build, not attempted here.

Loophole family (one design — a contained run, never a host run)
----------------------------------------------------------------
* Code that runs free: skill code executed in-process or as a
  bare shell-out with host privileges. CLOSED: the ONLY execution
  in this module is ``wasmtime`` inside a Store with NO WASI, NO
  host functions, NO preopened directories, and an EMPTY import
  list. A module that declares any import fails instantiation
  (``import_required``) — it never runs, not even partially.
* A sandbox with a hole: a declared capability the runtime does
  not enforce. CLOSED: the sandbox grants NOTHING by default.
  The skill must declare ``wasm:<module-file>`` up front in its
  ``vyuk-capabilities`` manifest (a signature-tier scope, like
  ``run:`` — see ``core/skill_format.py``), and the runtime
  enforces exactly that one module file, nothing else. Anything
  undeclared fails closed before any code runs.
* A gate that waves through: code running without admission,
  without a signature, or without the sandbox. CLOSED, in order:
  the manifest must verify against a TRUSTED signing key
  (``skill_signing.verify_signed_manifest`` — signature proves
  origin/integrity, never behaviour), the gate must admit the
  skill (``admit_skill`` with the ``wasm:`` grant requested —
  an unsigned skill's signature-tier grant is REFUSED, not
  downgraded), and only then does the sandbox run it. Every
  refusal is recorded in :func:`refusal_log` and returned with
  a stable ``code``.
* A result nobody checks: a skill that "runs" and returns
  something unverified. CLOSED: the pilot ABI is narrow and
  checkable — the module must export ``run`` with all-``i32``
  params and exactly one ``i32`` result (``bad_abi`` otherwise),
  the caller supplies the inputs, and the result is the
  module's own return value plus ``elapsed_s`` timing and the
  module's SHA-256. A known input gives a known output; the
  tests assert it.
* A runaway run: a module that loops forever. CLOSED: fuel.
  The engine runs with ``consume_fuel`` and a bounded fuel
  budget (default 10,000,000); exhaustion traps as
  ``fuel_exhausted``, never a hang.

What this module is NOT
-----------------------
* Not a second inference engine and not a general executor: no
  memory I/O from the host in v1 (the module may have its own
  memory; the host neither reads nor writes it), no string
  arguments, no WASI, no host imports at all. Pure compute
  through ``i32`` params and one ``i32`` result.
* Not a way around the funnel gates: the wiring's ``run``
  action is confirm-gated (``core/funnel/tools_gate.py``) and
  requires a library-admitted skill. The sandbox contains the
  code; it does not grant the skill anything.
* Not a trust story: nothing in the product writes a signing
  key's ``public_key_hex`` into the trust store yet — trusting
  a skill signing key has no onboarding flow. Until one lands,
  the store only trusts keys a human placed there by hand.
  The store lives in user state (``data/skill_trust_store.json``,
  never shipped); a missing store trusts nothing.

Dependency (recorded, not hidden)
---------------------------------
``wasmtime`` 50.0.1, PyPI, ``Apache-2.0 WITH LLVM-exception``
(from the installed package METADATA), $0. It is a DEV-ONLY lab
dependency for this pilot — it is NOT added to the shipped
requirements. This module imports it lazily inside
:func:`run_wasm_skill`; without it the runtime reports
``runtime_unavailable`` honestly and never fakes a run. Shipping
it (Windows wheel, requirements entry, licence record) is a
follow-up decision, not part of this pilot.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

# Pilot ABI: one exported function, i32 in, one i32 out.
_RUN_EXPORT = "run"
_WASM_SUFFIX = ".wasm"
_DEFAULT_FUEL = 10_000_000

_REFUSALS: list[dict] = []


def refusal_log() -> list[dict]:
    """Every refusal this runtime has made, oldest first."""
    return list(_REFUSALS)


def clear_refusal_log() -> None:
    """Forget the refusal record (tests only)."""
    _REFUSALS.clear()


def runtime_available() -> bool:
    """True when the wasmtime package is importable here."""
    try:
        import wasmtime  # noqa: F401
    except Exception:  # noqa: BLE001 — any import failure is unavailable
        return False
    return True


def _refuse(skill_name: str, code: str, reason: str) -> dict:
    record = {"skill": str(skill_name), "code": code, "reason": reason}
    _REFUSALS.append(record)
    return {"status": "refused", "code": code, "reason": reason}


def _trusted_public_keys(trust_store_path) -> list[tuple[str, bytes]]:
    """(key-id, public key) pairs the trust store actually trusts.

    The store maps key-id -> {"trusted": bool, "reason": str,
    "public_key_hex": str}. An entry without a public key cannot
    verify anything and is skipped; an untrusted entry is skipped.
    A missing or unreadable store trusts nothing.
    """
    if trust_store_path is None:
        return []
    try:
        store = json.loads(Path(trust_store_path).read_text(
            encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable store trusts nothing
        return []
    if not isinstance(store, dict):
        return []
    keys: list[tuple[str, bytes]] = []
    for key_id, entry in store.items():
        if not isinstance(entry, dict) or entry.get("trusted") is not True:
            continue
        raw = entry.get("public_key_hex")
        if not isinstance(raw, str):
            continue
        try:
            public_key = bytes.fromhex(raw)
        except ValueError:
            continue
        if len(public_key) != 32:
            continue
        keys.append((str(key_id), public_key))
    return keys


def _wasm_declaration(skill_dir: Path):
    """The skill's one ``wasm:<file>`` declaration, or a refusal.

    Returns (module_filename, manifest, None) on success, or
    (None, None, refusal-dict) on any failure. The declaration
    is read from the SKILL.md frontmatter's ``vyuk-capabilities``
    manifest through the existing parser — never re-parsed here.
    """
    from . import skill_format as _sf

    skill_file = skill_dir / "SKILL.md"
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, None, _refuse(
            skill_dir.name, "manifest_error",
            f"SKILL.md could not be read: {exc}")
    frontmatter, _body, _findings = _sf.parse_frontmatter(text)
    metadata = frontmatter.get("metadata") if isinstance(
        frontmatter, dict) else None
    manifest, findings = _sf.parse_capability_manifest(metadata)
    errors = [f for f in findings
              if getattr(f, "severity", "") == "error"]
    if errors:
        return None, None, _refuse(
            skill_dir.name, "manifest_error",
            "Capability manifest has errors: " + "; ".join(
                f.message for f in errors))
    wasm_entries = [e for e in manifest.get("effective", [])
                    if e.get("scope") == "wasm"]
    if not wasm_entries:
        return None, None, _refuse(
            skill_dir.name, "wasm_not_declared",
            "This skill declares no wasm: capability, so its code "
            "cannot run contained: declare wasm:<module-file> in "
            "vyuk-capabilities first.")
    if len(wasm_entries) > 1:
        return None, None, _refuse(
            skill_dir.name, "wasm_bad_target",
            "This skill declares more than one wasm: module; the "
            "pilot runs exactly one module per skill.")
    target = wasm_entries[0].get("target", "")
    if (not target.endswith(_WASM_SUFFIX) or "/" in target
            or "\\" in target or target.startswith(".")
            or ".." in target):
        return None, None, _refuse(
            skill_dir.name, "wasm_bad_target",
            f"wasm: target {target!r} is not a bare .wasm filename "
            "inside the skill directory.")
    return target, manifest, None


def run_wasm_skill(skill_dir, inputs, *, trust_store_path=None,
                   kill_list_path=None, fuel=_DEFAULT_FUEL) -> dict:
    """Run one skill's WASM module, contained — or refuse honestly.

    ``inputs`` is a list of ints (the pilot ABI: ``run`` takes
    ``i32`` params, returns one ``i32``). ``trust_store_path`` is
    the skill trust store (``data/skill_trust_store.json`` by
    convention); ``kill_list_path`` is the skill kill list.
    Neither defaults to anything: a caller that passes neither
    trusts no key and revokes nothing, and every signed skill
    is refused.
    """
    skill_dir = Path(skill_dir)
    name = skill_dir.name
    if not runtime_available():
        return _refuse(
            name, "runtime_unavailable",
            "The contained runtime is not available here (wasmtime "
            "is not installed), so this skill's code cannot run: "
            "nothing was executed and no result was faked.")
    if not isinstance(inputs, list) or not inputs or any(
            not isinstance(v, int) or isinstance(v, bool)
            or v < -(2 ** 31) or v >= 2 ** 31 for v in inputs):
        return _refuse(
            name, "bad_abi",
            "Inputs must be a non-empty list of 32-bit integers for "
            "the pilot ABI.")

    target, _manifest, refusal = _wasm_declaration(skill_dir)
    if refusal is not None:
        return refusal
    module_path = skill_dir / target
    if not module_path.is_file():
        return _refuse(
            name, "wasm_missing_file",
            f"Declared WASM module {target!r} is not in the skill "
            "directory, so nothing can run.")
    try:
        wasm_bytes = module_path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return _refuse(
            name, "wasm_missing_file",
            f"Declared WASM module {target!r} could not be read: {exc}")

    trusted = _trusted_public_keys(trust_store_path)
    if not trusted:
        return _refuse(
            name, "no_trusted_key",
            "No trusted skill signing key is configured, so this "
            "skill's signature cannot be verified and its code "
            "cannot run.")
    from . import skill_signing as _ss

    verified_key_id = None
    for key_id, public_key in trusted:
        ok, _reasons = _ss.verify_signed_manifest(skill_dir, public_key)
        if ok:
            verified_key_id = key_id
            break
    if verified_key_id is None:
        return _refuse(
            name, "signature_unverified",
            "This skill's signed manifest does not verify against "
            "any trusted signing key (wrong key, or a file changed "
            "after signing), so its code cannot run.")

    revoked = []
    if kill_list_path is not None:
        try:
            revoked = list(_ss.load_kill_list(kill_list_path).keys())
        except Exception:  # noqa: BLE001 — unreadable kill list
            revoked = []
    from . import skill_gate as _sg

    grant = f"wasm:{target}"
    verdict = _sg.admit_skill(
        skill_dir, requested_grants=[grant], signed_by_vyuk=True,
        signing_key_id=verified_key_id,
        trusted_key_ids=[verified_key_id], revoked_key_ids=revoked)
    if not verdict.get("admitted"):
        return _refuse(
            name, "gate_refused",
            "The skill gate refused admission: " + "; ".join(
                verdict.get("reasons", ["Gate refused admission."])))
    if grant not in verdict.get("grants", {}).get("granted", []):
        return _refuse(
            name, "grant_refused",
            f"The wasm: grant {grant!r} was not granted: only a "
            "VYUK-signed skill may run contained code, and this "
            "skill's signature-tier grant was refused.")

    return _run_in_wasmtime(name, wasm_bytes, target, inputs, fuel)


def _run_in_wasmtime(name, wasm_bytes, target, inputs, fuel) -> dict:
    """The ONLY execution in this module: inside Wasmtime.

    Store with no WASI, no host functions, empty imports. Any
    import the module declares fails instantiation here.
    """
    import wasmtime

    started = time.monotonic()
    try:
        config = wasmtime.Config()
        config.consume_fuel = True
        engine = wasmtime.Engine(config)
        store = wasmtime.Store(engine)
        store.set_fuel(fuel)
        module = wasmtime.Module(engine, wasm_bytes)
    except Exception as exc:  # noqa: BLE001 — invalid module, no run
        return _refuse(
            name, "run_failed",
            f"WASM module {target!r} could not be compiled: {exc}")
    try:
        instance = wasmtime.Instance(store, module, [])
    except Exception as exc:  # noqa: BLE001 — imports required
        return _refuse(
            name, "import_required",
            f"WASM module {target!r} needs host imports, which the "
            f"contained runtime never provides: {exc}")
    try:
        exports = instance.exports(store)
        run = exports.get(_RUN_EXPORT)
    except Exception as exc:  # noqa: BLE001
        return _refuse(
            name, "bad_abi",
            f"Could not read exports of {target!r}: {exc}")
    if run is None:
        return _refuse(
            name, "bad_abi",
            f"WASM module {target!r} exports no 'run' function.")
    try:
        func_type = run.type(store)
        params = list(func_type.params)
        results = list(func_type.results)
    except Exception as exc:  # noqa: BLE001
        return _refuse(
            name, "bad_abi",
            f"Could not read the type of 'run' in {target!r}: {exc}")
    if (len(params) != len(inputs)
            or any(p != wasmtime.ValType.i32() for p in params)
            or len(results) != 1
            or results[0] != wasmtime.ValType.i32()):
        return _refuse(
            name, "bad_abi",
            f"'run' in {target!r} must take {len(inputs)} i32 "
            "params and return one i32; it does not.")
    try:
        result = run(store, *inputs)
    except Exception as exc:  # noqa: BLE001 — trap, incl. fuel
        message = str(exc)
        if "fuel" in message.lower() or "all fuel" in message.lower():
            return _refuse(
                name, "fuel_exhausted",
                f"WASM module {target!r} ran out of fuel and was "
                "stopped: it cannot loop forever.")
        return _refuse(
            name, "run_failed",
            f"WASM module {target!r} trapped: {exc}")
    return {
        "status": "ok",
        "result": int(result),
        "wasm_file": target,
        "module_sha256": hashlib.sha256(wasm_bytes).hexdigest(),
        "elapsed_s": round(time.monotonic() - started, 6),
    }
