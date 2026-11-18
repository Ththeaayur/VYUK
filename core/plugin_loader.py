"""
Plugin System for Voice AI Assistant.
Discovers and loads .py plugin files from the plugins/ directory at startup.
Each plugin must expose a PLUGIN dict and a run() function.

Plugin template:
    # vyuk-plugin: capabilities=network,read
    PLUGIN = {
        "name": "my_skill",
        "description": "What this skill does",
        "parameters": {
            "type": "object",
            "properties": {
                "arg": {"type": "string", "description": "The argument"}
            },
            "required": ["arg"]
        }
    }

    def run(arg: str) -> str:
        return f"Result: {arg}"

Result convention: a result that reports live or changing
data says when it is from and where it came from — end with
"— as of HH:MM, via <source>" (see plugins/crypto_price.py
and plugins/stock_ticker.py). A result that acted on the
system verifies the outcome before claiming it.

------------------------------------------------------------------
Integrity & capability gating (v5.45, H19)
------------------------------------------------------------------
Three rules, all enforced BEFORE a plugin's code is executed:

(a) NAME COLLISION IS LOUD. A plugin whose PLUGIN name collides
    with a tool that already exists (a built-in, or an earlier
    plugin in the same scan) is SKIPPED with a loud error log —
    never silently overridden. discover_plugins() takes the
    caller's pre-plugin registry snapshot as `existing_names`;
    core/funnel/tools_handlers.py's load_plugins (ToolHandlersMixin, called by the tools singleton at startup) passes the registry as it stood
    before plugins loaded. (The one intentional in-product
    override — a later skills pack's screenshot_region replacing
    an earlier pack's inside register_all — does NOT go through
    this loader and is unaffected.)

(b) CAPABILITY DECLARATION. A plugin declares what it needs in a
    manifest comment near the top of the file:

        # vyuk-plugin: capabilities=network,read

    Vocabulary, mapped to the executor's existing gate classes
    (see capability_gate_class):
        read     pure read — ungated, stays free under taint
        network  sends model-chosen bytes out (egress class) —
                 ungated normally, gated under injection taint
        state    changes machine state — always human-gated
        input    injects keyboard/mouse input — always gated
        shell    runs commands — always gated
        write    writes files — always gated
        clipboard reads/writes the clipboard — always gated
    The gate class is the MOST conservative class implied by the
    declared set. NO declaration (or an unknown capability) gets
    the conservative default: gated like a state-changing tool.

(c) SHA-256 MANIFEST. plugins/plugins_hashes.json maps
    {filename: sha256-of-file-bytes}. The loader verifies each
    plugin file against it BEFORE exec_module; a mismatch or a
    missing entry REFUSES the load with a loud log — a plugin
    whose bytes changed since the manifest was made does not run.

    Regenerate the manifest after deliberately editing a plugin:

        python3 -c "from core.plugin_loader import \\
            regenerate_hash_manifest; regenerate_hash_manifest()"

    (run from the app root), or `python3 core/plugin_loader.py`
    from the app root, which does the same and prints the result.

    NOTE (v5.47, programme item 15): a regenerated manifest is
    UNSIGNED, and an unsigned manifest loads NO shipped plugin
    (rule (d) below) until the release step signs it with the
    offline key — packaging/sign_manifest.py --plugin-manifest.
    Regeneration is a development step, not a release step.

------------------------------------------------------------------
Plugin trust, two stores (v5.47, programme item 15)
[B-T1.8 + D-C17]
------------------------------------------------------------------
Rule (c) alone left the manifest itself unauthenticated: the
hash file sits BESIDE the plugins it vouches for, so anyone (or
anything) that could rewrite a plugin could rewrite its hash to
match and the loader would run it. Item 15 closes that with two
explicit trust stores:

(d1) THE SHIPPED STORE IS SIGNED. plugins/plugins_hashes.json
    now carries a detached Ed25519 signature in the sidecar
    plugins/plugins_hashes.json.sig — base64 of the 64-byte
    signature over the EXACT bytes of the manifest, the same
    format as the update feed (core/health/updater.py). Before any
    hash in the manifest is trusted, discover_plugins() verifies
    the signature against the public key pinned for the update
    feed (core/health/updater.py's PINNED_UPDATE_PUBLIC_KEY, reused —
    one release key signs both manifests). Unsigned manifest,
    missing sidecar, malformed sidecar, or a signature that
    does not verify -> every plugin the manifest LISTS refuses
    to load, with a loud log line naming the reason. The release
    kit produces the sidecar: packaging/sign_manifest.py
    --plugin-manifest (see packaging/README.md).

(d2) THE USER STORE IS OPT-IN. A plugin file present in
    plugins/ but NOT listed in the shipped manifest is a
    user-added plugin. It loads only after an explicit human
    opt-in through the trust-prompt seam:
    set_trust_prompt_handler(fn), where
    fn(filename, sha256, capabilities) -> bool — mirroring how
    core/confirm_gate.py registers its handler. No handler
    registered (headless runs, tests, the CLI) -> the plugin is
    REFUSED with a loud log; prompts are never auto-answered.
    On approval the loader pins {filename: sha256} into
    data/user_trusted_plugins.json (the user store). On later
    loads a user plugin whose hash still matches its pin loads
    without prompting; a changed hash prompts again (and a fresh
    approval re-pins the new hash). The loader NEVER writes to
    the shipped manifest from this path — user trust can only
    ever live in data/, where the updater never overwrites user
    data. Pins for files that have disappeared from disk are
    KEPT: they are harmless (a pin only ever vouches for exact
    bytes; a file that comes back byte-identical is the same
    code the user approved, and a changed file re-prompts), and
    pruning would silently forget a deliberate approval.

(d3) GOVERNING RULE when the two stores both mention a file:
    if the filename is LISTED in the shipped manifest, the
    shipped store's verdict governs — signature first, then
    hash — and the user store is never consulted for it. A
    tampered/unsigned shipped manifest therefore refuses its
    plugins even if the same filename also carries a user-store
    pin. The user store covers ONLY files the shipped manifest
    does not list. (If the manifest file itself is missing or
    unreadable there is no list to govern by, so — the pre-v5.47
    behaviour — EVERY plugin refuses.)
"""

import ast
import base64
import hashlib
import importlib.util
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
# The user store lives in data/ (user territory: the updater
# never touches it, and the shipped manifest is never written by
# the loader). Derived from this file exactly like PLUGINS_DIR —
# core/config.py's DATA_DIR points at the same folder, but this
# module deliberately does not import config (another owner's
# file, and its import has process-wide side effects a plugin
# scan should not need). Suites monkeypatch this constant to
# fixture data dirs, same as they patch PLUGINS_DIR.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HASH_MANIFEST_NAME = "plugins_hashes.json"
# v5.47 (programme item 15): the shipped manifest's detached
# signature sidecar — "<manifest>.sig", base64 of the 64-byte
# Ed25519 signature over the manifest's exact bytes (the
# update-feed format, core/health/updater.py).
HASH_MANIFEST_SIG_NAME = HASH_MANIFEST_NAME + ".sig"
USER_TRUST_STORE_NAME = "user_trusted_plugins.json"

# ------------------------------------------------------------------
# Ed25519 verification for the shipped manifest (v5.47, item 15).
#
# The verifier is core/health/updater.py's pure-Python RFC 8032
# implementation — the same code that authenticates the update
# feed, with the same pinned public key: ONE offline release key
# signs both manifests (packaging/sign_manifest.py). The import
# is acyclic: updater imports only core/version, and nothing in
# updater imports plugin_loader or tools. plugin_loader resolves
# it relatively so the module works under every package name the
# app is imported by (voice_assistant.core.*, core.*).
#
# PINNED_UPDATE_PUBLIC_KEY is copied into THIS module's namespace
# at import and read fresh on every verification, so a suite can
# monkeypatch plugin_loader.PINNED_UPDATE_PUBLIC_KEY with a test
# keypair's public key (the updater documents the same seam on
# its own constant). If the import ever fails (e.g. this file run
# as a bare script for manifest regeneration), the verifier is
# None and verification FAILS CLOSED — shipped plugins refuse —
# rather than silently trusting an unsigned manifest.
# ------------------------------------------------------------------
try:
    from .health.updater import _ed25519_verify  # Phase 3 H1: moved to core/health/
    from .health.updater import PINNED_UPDATE_PUBLIC_KEY  # Phase 3 H1: moved to core/health/
except Exception:  # noqa: BLE001 — no package context (bare script)
    try:
        from core.health.updater import _ed25519_verify  # app-root context  # Phase 3 H1: moved to core/health/
        from core.health.updater import PINNED_UPDATE_PUBLIC_KEY  # Phase 3 H1: moved to core/health/
    except Exception:  # noqa: BLE001 — fail closed, see above
        _ed25519_verify = None
        PINNED_UPDATE_PUBLIC_KEY = b""

_CAPABILITY_LINE = re.compile(
    r"^#[ \t]*vyuk-plugin:[ \t]*capabilities[ \t]*=[ \t]*"
    r"([A-Za-z][A-Za-z, \t]*?)[ \t]*$",
    re.MULTILINE)

# Capability -> gate class, most-conservative wins (see docstring).
_CAPABILITY_CLASS = {
    "read": "read",
    "network": "egress",
    "state": "gated",
    "input": "gated",
    "shell": "gated",
    "write": "gated",
    "clipboard": "gated",
}
_CLASS_RANK = {"read": 0, "egress": 1, "gated": 2}


def parse_capabilities(text: str) -> list[str] | None:
    """The declared capability list from a plugin file's manifest
    comment, or None when there is no declaration. Only the head
    of the file is scanned (the declaration belongs at the top)."""
    match = _CAPABILITY_LINE.search(text[:4000])
    if not match:
        return None
    caps = [c.strip().lower() for c in match.group(1).split(",")
            if c.strip()]
    return caps or None


def capability_gate_class(capabilities) -> str:
    """The gate class for a declared capability list: "read",
    "egress" or "gated". No declaration, an empty list, or ANY
    unknown capability -> "gated" (the conservative default)."""
    if not capabilities:
        return "gated"
    worst = "read"
    for cap in capabilities:
        cls = _CAPABILITY_CLASS.get(str(cap).strip().lower())
        if cls is None:
            return "gated"
        if _CLASS_RANK[cls] > _CLASS_RANK[worst]:
            worst = cls
    return worst


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------
# Gate C (master audit P2-1): manifest + name validation BEFORE
# import. The old order imported (executed) the plugin file and
# only then read its PLUGIN dict and collision-checked its name
# — a plugin whose name collided, or whose manifest was malformed,
# had ALREADY run its top-level code by the time it was refused.
# The fix reads the manifest from the file's AST without
# executing anything, and refuses before import on any failure.
# ------------------------------------------------------------------

#: Plugin names use the same shape as MCP server names.
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def static_plugin_meta(text: str) -> tuple[dict | None, str | None]:
    """The PLUGIN manifest read WITHOUT executing the file.

    Parses the source's AST and reads a top-level ``PLUGIN =
    {...}`` literal (a plain Assign or an AnnAssign). Returns
    (meta, None) with meta = {"name": ..., "description": ...}
    (description may be absent), or (None, reason) when the
    manifest cannot be determined statically.

    What counts as determinable, and what does not — decided
    explicitly, because "cannot determine" is itself a refusal:
    * no top-level PLUGIN assignment -> refused (nothing to
      collision-check);
    * more than one top-level PLUGIN assignment -> refused
      (ambiguous which one would win at runtime);
    * PLUGIN is not a static dict literal (built by a call, a
      comprehension, a conditional, or a later rebinding) ->
      refused (its runtime name is not the name that would be
      checked);
    * name missing, not a string, empty, or failing the name
      pattern -> refused.
    Description and parameters are NOT required here: the
    loader never required them (only a dict + a callable run),
    so requiring them now would refuse plugins that load
    today. They stay post-exec/registrar concerns. The name —
    the collision-checked field — is the security-critical
    one, and it is fully determined here."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return None, f"cannot parse the file (syntax error: {exc})"
    found = []
    for node in tree.body:
        targets: list = []
        value = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if node.target is not None else []
            value = node.value
        else:
            continue
        if value is None:
            continue
        if any(isinstance(t, ast.Name) and t.id == "PLUGIN"
               for t in targets):
            found.append(value)
    if not found:
        return None, "no top-level PLUGIN assignment found"
    if len(found) > 1:
        return None, ("multiple top-level PLUGIN assignments — "
                       "ambiguous which one would win")
    value = found[0]
    if not isinstance(value, ast.Dict):
        return None, ("PLUGIN is not a static dict literal — its "
                       "name cannot be validated before import")
    try:
        literal = ast.literal_eval(value)
    except Exception:  # noqa: BLE001 — non-literal contents
        return None, ("PLUGIN dict is not a static literal — its "
                       "name cannot be validated before import")
    if not isinstance(literal, dict):
        return None, "PLUGIN is not a dict literal"
    name = literal.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "PLUGIN has no string 'name'"
    if not _PLUGIN_NAME_RE.match(name):
        return None, (f"PLUGIN name {name!r} is invalid — use "
                       "letters, digits, '_', '.' or '-'")
    return {"name": name,
            "description": literal.get("description")}, None


def load_hash_manifest(plugins_dir: Path | None = None) -> dict | None:
    """The {filename: sha256} manifest, or None when the manifest
    file is missing/unreadable (which refuses every plugin —
    see discover_plugins). NOTE (v5.47, programme item 15): a
    non-None return only means the manifest PARSED; its hashes
    are trusted only after verify_shipped_manifest_signature()
    passes over the same directory."""
    directory = plugins_dir or PLUGINS_DIR
    try:
        data = json.loads(
            (directory / HASH_MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def verify_shipped_manifest_signature(
        plugins_dir: Path | None = None) -> tuple[bool, str]:
    """(True, "") when the shipped manifest in `plugins_dir` is
    authentically signed, else (False, reason) naming exactly
    why not (v5.47, programme item 15, rule (d1)).

    Reads the manifest's EXACT bytes and the detached signature
    in plugins_hashes.json.sig (base64 of the 64-byte Ed25519
    signature, the update-feed format) and verifies against
    PINNED_UPDATE_PUBLIC_KEY — this module's copy of the key
    pinned in core/health/updater.py, read fresh here so suites can
    monkeypatch it. Never raises: every failure is a refusal
    reason, and every refusal is fail-closed."""
    directory = plugins_dir or PLUGINS_DIR
    manifest_path = directory / HASH_MANIFEST_NAME
    sig_path = directory / HASH_MANIFEST_SIG_NAME
    try:
        manifest_bytes = manifest_path.read_bytes()
    except Exception:  # noqa: BLE001
        return False, f"hash manifest {manifest_path} is missing " \
                      f"or unreadable"
    try:
        sig_text = sig_path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return False, f"signature sidecar {sig_path.name} is " \
                      f"missing or unreadable (unsigned manifest)"
    try:
        signature = base64.b64decode(sig_text, validate=True)
    except Exception:  # noqa: BLE001
        return False, f"signature sidecar {sig_path.name} is not " \
                      f"valid base64"
    if len(signature) != 64:
        return False, f"signature sidecar {sig_path.name} decodes " \
                      f"to {len(signature)} bytes, not the 64 of " \
                      f"an Ed25519 signature"
    if _ed25519_verify is None:
        return False, "the Ed25519 verifier (core/health/updater.py) " \
                      "could not be imported"
    if not _ed25519_verify(PINNED_UPDATE_PUBLIC_KEY,
                            manifest_bytes, signature):
        return False, "the signature does not verify against " \
                      "the pinned release public key " \
                      "(core/health/updater.py PINNED_UPDATE_PUBLIC_KEY)"
    return True, ""


# ------------------------------------------------------------------
# User store + trust-prompt seam (v5.47, programme item 15, (d2))
# ------------------------------------------------------------------

_trust_prompt_handler = None


def set_trust_prompt_handler(fn) -> None:
    """Register the human trust-prompt handler for user-added
    plugins (v5.47, programme item 15).

    fn(filename: str, sha256: str, capabilities: list | None)
    -> bool — return True only when the human explicitly agreed
    to trust this exact file content. Registering replaces any
    previous handler; pass None to unregister (headless mode:
    every unlisted plugin is then refused). Mirrors
    core/confirm_gate.py's register_handler contract."""
    global _trust_prompt_handler
    _trust_prompt_handler = fn


def clear_trust_prompt_handler() -> None:
    """Unregister the trust-prompt handler (refuse-everything
    returns for user-added plugins)."""
    set_trust_prompt_handler(None)


def has_trust_prompt_handler() -> bool:
    """True when a trust-prompt handler is currently registered."""
    return _trust_prompt_handler is not None


def _user_trust_store_path() -> Path:
    """Where user-approved plugin pins live: data/ — never the
    shipped manifest (rule (d2)). DATA_DIR is read fresh so
    suites can patch it to a fixture directory."""
    return DATA_DIR / USER_TRUST_STORE_NAME


def load_user_trusted_plugins() -> dict:
    """The user store: {filename: pinned sha256} the human has
    approved. {} when the store is missing or unreadable — an
    unreadable store simply means everything unlisted prompts
    (or refuses headless) again; it never grants anything."""
    try:
        data = json.loads(
            _user_trust_store_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _pin_user_trusted_plugin(filename: str, sha256: str) -> bool:
    """Record one human approval in the user store. Returns True
    when the pin was written. A failed write is loud but not
    fatal to THIS load — the human did approve these exact
    bytes; the cost is only that the next load prompts again."""
    store = load_user_trusted_plugins()
    store[filename] = sha256
    try:
        path = _user_trust_store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(store, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Plugins] ERROR: could not pin {filename} into "
              f"the user trust store ({e}) — it will prompt "
              f"again on the next load.")
        return False


def _user_plugin_trusted(path: Path, actual: str,
                         capabilities) -> bool:
    """The user-store verdict for one UNLISTED plugin file
    (v5.47, programme item 15, rule (d2)): True only when its
    exact bytes are already pinned from an earlier approval, or
    the registered trust-prompt handler approves them now (and
    the approval is pinned). Every other outcome — no handler,
    a handler that says no, a handler that raises — is a loud
    refusal. Called ONLY for files the shipped manifest does
    not list; see the governing rule (d3) in the docstring."""
    pinned = load_user_trusted_plugins().get(path.name)
    if pinned is not None and pinned == actual:
        print(f"[Plugins] {path.name}: user-added plugin, hash "
              f"matches its user-store pin — loading without a "
              f"prompt.")
        return True
    if pinned is not None:
        print(f"[Plugins] {path.name}: user-added plugin whose "
              f"hash CHANGED since it was trusted (pin "
              f"{pinned[:12]}…, file {actual[:12]}…) — it must "
              f"be approved again.")
    handler = _trust_prompt_handler
    if handler is None:
        print(f"[Plugins] ERROR: refusing {path.name}: it is a "
              f"user-added plugin (not in the signed shipped "
              f"manifest) and no trust-prompt handler is "
              f"registered, so nobody can approve it (headless "
              f"run). Register one with "
              f"set_trust_prompt_handler() in "
              f"core/plugin_loader.py.")
        return False
    try:
        approved = bool(handler(path.name, actual, capabilities))
    except Exception:  # noqa: BLE001 — a broken prompt denies
        print(f"[Plugins] ERROR: refusing {path.name}: the "
              f"trust-prompt handler raised:")
        traceback.print_exc()
        return False
    if not approved:
        print(f"[Plugins] ERROR: refusing {path.name}: the user "
              f"did not approve this user-added plugin at the "
              f"trust prompt.")
        return False
    print(f"[Plugins] {path.name}: user approved this "
          f"user-added plugin at the trust prompt — pinning its "
          f"hash in the user store ({USER_TRUST_STORE_NAME}).")
    _pin_user_trusted_plugin(path.name, actual)
    return True


def regenerate_hash_manifest(plugins_dir: Path | None = None) -> dict:
    """(Re)build plugins/plugins_hashes.json for every loadable
    plugin file in the directory and return the manifest. This is
    the documented regeneration path after a deliberate plugin
    edit (see the module docstring).

    v5.47 (programme item 15): the regenerated manifest is
    UNSIGNED — under rule (d1) no shipped plugin will load from
    it until the release step signs it offline with
    packaging/sign_manifest.py --plugin-manifest. That is
    deliberate: the signing key never lives in this tree, so
    this tool must not be able to produce a loadable manifest
    on its own."""
    directory = plugins_dir or PLUGINS_DIR
    directory.mkdir(exist_ok=True)
    manifest = {}
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        manifest[path.name] = _sha256_file(path)
    (directory / HASH_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"[Plugins] Hash manifest regenerated for "
          f"{len(manifest)} plugin(s): "
          f"{directory / HASH_MANIFEST_NAME}")
    print(f"[Plugins] NOTE (v5.47): the regenerated manifest is "
          f"UNSIGNED — shipped plugins will refuse to load until "
          f"it is signed offline (packaging/sign_manifest.py "
          f"--plugin-manifest).")
    return manifest


# ------------------------------------------------------------------
# Scan cache (GAPS-29, E-V2) — discover_plugins() used to re-scan
# and re-exec every plugin module on EVERY call (registry load,
# Skills page, galaxy first open, Learned plugin list: 3–4 full
# scans a session). The cache is keyed by the directory
# fingerprint + the caller's existing_names snapshot; see
# discover_plugins() for the contract.
# ------------------------------------------------------------------

_DISCOVER_CACHE: dict[tuple, list[dict[str, Any]]] = {}


def _discover_fingerprint(plugins_dir: Path | None = None) -> tuple:
    """What a cached scan was made from: every plugin file's
    sha256, the shipped manifest + its signature, and the user
    trust store. Any of those changing means the next call
    re-scans; a missing file hashes as None (a file that comes
    back is a change too). Reading + hashing ~10 small files is
    the whole cost of a cache hit — no exec_module."""
    directory = plugins_dir or PLUGINS_DIR
    parts: list = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            parts.append((path.name, _sha256_file(path)))
        except Exception:  # noqa: BLE001 — unreadable is a change
            parts.append((path.name, None))
    for name, path in (
            (HASH_MANIFEST_NAME, directory / HASH_MANIFEST_NAME),
            (HASH_MANIFEST_SIG_NAME, directory / HASH_MANIFEST_SIG_NAME),
            (USER_TRUST_STORE_NAME, _user_trust_store_path())):
        try:
            parts.append((name, _sha256_file(path)))
        except Exception:  # noqa: BLE001 — missing is a state
            parts.append((name, None))
    return tuple(parts)


def discover_plugins(existing_names=None, refresh=False) -> list[dict[str, Any]]:
    """Scans the plugins/ directory and loads all valid plugin modules.

    `existing_names` is the caller's snapshot of tool names that
    existed BEFORE plugin loading (built-ins): a plugin colliding
    with one — or with an earlier plugin in this scan — is skipped
    with a loud error (H19a). Every candidate is hash-verified
    against plugins/plugins_hashes.json before it is executed
    (H19c), and its capability declaration is attached as
    `_capabilities` / `_gate_class` for the registrar (H19b).

    v5.47 (programme item 15): before any of that, the shipped
    manifest's own Ed25519 signature is verified (rule (d1)), and
    a file the manifest does not list goes through the user
    store instead (rule (d2)), under the governing rule (d3):
    listed files answer to the shipped store only.

    GAPS-29 (E-V2): scans are CACHED. A full scan re-executes
    every plugin module, and four call sites used to pay that
    on every call (registry load at startup, the Skills page,
    the galaxy's first open, the Learned page's plugin list).
    The cache is keyed by the directory fingerprint (every
    plugin file's sha256 + the manifest + its signature +
    the user trust store) AND the caller's `existing_names`
    (the collision verdict depends on it, so two callers with
    different snapshots hold different entries). A repeat call
    with unchanged files and the same snapshot returns the
    cached list without re-executing anything. `refresh=True`
    (the Rescan button, tests) forces a fresh scan; a changed
    file, manifest, signature or trust pin changes the
    fingerprint, so the next call re-scans on its own."""
    PLUGINS_DIR.mkdir(exist_ok=True)
    key = (str(PLUGINS_DIR), str(DATA_DIR),
           _discover_fingerprint(),
           frozenset(existing_names or ()))
    if not refresh and key in _DISCOVER_CACHE:
        return list(_DISCOVER_CACHE[key])
    discovered = _discover_plugins_uncached(existing_names)
    _DISCOVER_CACHE[key] = discovered
    return list(discovered)


def clear_plugin_cache() -> None:
    """Drop every cached plugin scan (GAPS-29, E-V2 — tests and
    the Rescan path; a changed file already invalidates the
    cache through the fingerprint, so this is for callers that
    need a fresh scan without touching the files)."""
    _DISCOVER_CACHE.clear()


def _discover_plugins_uncached(existing_names=None) -> list[dict[str, Any]]:
    """The full scan: hash/trust/collision checks, then
    exec_module per plugin. See discover_plugins() for the
    contract; this is the uncached half, called once per
    (fingerprint, snapshot) pair."""
    PLUGINS_DIR.mkdir(exist_ok=True)
    discovered = []
    taken = set(existing_names or ())
    manifest = load_hash_manifest()
    if manifest is None:
        print(f"[Plugins] ERROR: hash manifest "
              f"{PLUGINS_DIR / HASH_MANIFEST_NAME} is missing or "
              f"unreadable — refusing to load ANY plugin. If the "
              f"plugins are deliberate, regenerate it: "
              f"regenerate_hash_manifest() in core/plugin_loader.py.")
        # v5.47 (programme item 15): with no manifest there is no
        # shipped list to govern by (rule (d3)), so the user store
        # does not get a say either — refuse everything, as before.
        shipped_sig_ok, shipped_sig_reason = False, "no manifest"
    else:
        # (d1) Authenticate the shipped manifest BEFORE trusting
        # any hash in it — once per scan, not once per file.
        shipped_sig_ok, shipped_sig_reason = \
            verify_shipped_manifest_signature()
        if not shipped_sig_ok:
            print(f"[Plugins] ERROR: the shipped plugin manifest "
                  f"{PLUGINS_DIR / HASH_MANIFEST_NAME} is NOT "
                  f"trusted: {shipped_sig_reason}. Every plugin "
                  f"it lists is REFUSED. (A release manifest is "
                  f"signed offline — packaging/sign_manifest.py "
                  f"--plugin-manifest.)")

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        # (c) INTEGRITY — verify before ANY plugin code runs.
        if manifest is None:
            print(f"[Plugins] ERROR: refusing {path.name}: no hash "
                  f"manifest to verify it against.")
            continue
        try:
            actual = _sha256_file(path)
        except Exception as e:  # noqa: BLE001
            print(f"[Plugins] ERROR: refusing {path.name}: could "
                  f"not hash it ({e}).")
            continue
        # The capability declaration is parsed before the trust
        # branch: reading a file's head is not executing it, and
        # the trust prompt (d2) must show the human what the
        # plugin declares it needs.
        try:
            raw_text = path.read_text(encoding="utf-8",
                                      errors="replace")
        except Exception as e:  # noqa: BLE001
            print(f"[Plugins] ERROR: refusing {path.name}: could "
                  f"not read it ({e}).")
            continue
        capabilities = parse_capabilities(raw_text)
        # Gate C (P2-1) — MANIFEST + COLLISION BEFORE IMPORT.
        # The plugin's name is read from the file's AST above
        # and collision-checked HERE. Importing first and
        # checking after would already have executed the
        # plugin's top-level code — exactly what the old order
        # did for colliding or malformed plugins. A refusal
        # here means the file was NOT imported.
        static_meta, static_err = static_plugin_meta(raw_text)
        if static_meta is None:
            print(f"[Plugins] ERROR: refusing {path.name}: its "
                  f"PLUGIN manifest cannot be validated before "
                  f"import ({static_err}) — the file was NOT "
                  f"imported.")
            continue
        if static_meta["name"] in taken:
            print(f"[Plugins] ERROR: plugin "
                  f"'{static_meta['name']}' from {path.name} "
                  f"COLLIDES with an existing tool of the same "
                  f"name — SKIPPED WITHOUT IMPORTING, the "
                  f"existing tool was NOT overridden.")
            continue
        if path.name in manifest:
            # (d3) GOVERNING RULE: a listed file answers to the
            # shipped store ONLY — signature first, then hash.
            # The user store is never consulted for it, so a pin
            # cannot resurrect a shipped plugin whose manifest
            # was tampered with.
            if not shipped_sig_ok:
                print(f"[Plugins] ERROR: refusing {path.name}: "
                      f"it is a shipped plugin but the shipped "
                      f"manifest is not trusted "
                      f"({shipped_sig_reason}).")
                continue
            expected = manifest[path.name]
            if actual != expected:
                print(f"[Plugins] ERROR: refusing {path.name}: "
                      f"SHA-256 mismatch (manifest "
                      f"{str(expected)[:12]}…, file "
                      f"{actual[:12]}…) — the file changed since "
                      f"the manifest was made. If the change is "
                      f"deliberate, regenerate the manifest "
                      f"(regenerate_hash_manifest()) and re-sign "
                      f"it for release.")
                continue
        else:
            # (d2) Not listed in the shipped manifest: a
            # user-added plugin. It needs a user-store pin for
            # these exact bytes or a fresh trust-prompt approval.
            if not _user_plugin_trusted(path, actual, capabilities):
                continue
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            # Register before exec_module (the importlib recipe):
            # a module-level @dataclass with string annotations
            # (e.g. `from __future__ import annotations`) resolves
            # them through sys.modules[__name__].__dict__ — without
            # this the import dies with "'NoneType' object has no
            # attribute '__dict__'". Same one-line fix as
            # core/personal_skills.py (field-check fix #24).
            sys.modules[path.stem] = mod
            spec.loader.exec_module(mod)

            plugin_meta = getattr(mod, "PLUGIN", None)
            plugin_run = getattr(mod, "run", None)

            if not isinstance(plugin_meta, dict) or not callable(plugin_run):
                print(f"[Plugins] Skipping {path.name}: missing PLUGIN dict or run() function")
                continue

            # Gate C (P2-1) — runtime/static consistency: the
            # executed PLUGIN name must be the statically
            # validated one. A plugin that rewrites its own
            # name after the literal (PLUGIN["name"] = ...) was
            # not the name that was collision-checked above.
            if plugin_meta.get("name") != static_meta["name"]:
                print(f"[Plugins] ERROR: plugin from {path.name} "
                      f"declares a different name at runtime "
                      f"({plugin_meta.get('name')!r}) than its "
                      f"static manifest ({static_meta['name']!r}) "
                      f"— SKIPPED, the static check did not cover "
                      f"the runtime name.")
                continue

            # (a) COLLISION — loud, skip, never override.
            # Defense-in-depth: the pre-import check above
            # already refused collisions, so this can only fire
            # if the taken set changed mid-scan.
            name = plugin_meta.get("name")
            if name in taken:
                print(f"[Plugins] ERROR: plugin '{name}' from "
                      f"{path.name} COLLIDES with an existing tool "
                      f"of the same name — SKIPPED, the existing "
                      f"tool was NOT overridden.")
                continue

            plugin_meta["_handler"] = plugin_run
            plugin_meta["_source"] = str(path)
            plugin_meta["_capabilities"] = capabilities
            plugin_meta["_gate_class"] = capability_gate_class(capabilities)
            if capabilities is None:
                print(f"[Plugins] WARNING: {path.name} declares no "
                      f"capabilities (no '# vyuk-plugin:' line) — "
                      f"it will be gated like a state-changing tool "
                      f"(the conservative default).")
            taken.add(name)
            discovered.append(plugin_meta)
            print(f"[Plugins] Loaded: {plugin_meta.get('name', path.stem)} from {path.name}")

        except Exception:  # noqa: BLE001
            print(f"[Plugins] Error loading {path.name}:")
            traceback.print_exc()

    return discovered


if __name__ == "__main__":  # documented regeneration path (H19c)
    regenerate_hash_manifest()
    sys.exit(0)
