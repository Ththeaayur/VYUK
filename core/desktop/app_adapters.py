"""§7 item 9 — application-adapter FRAMEWORK ONLY (parent draft).

An adapter is a control profile for one application: its identity
signatures, its vocabulary, its quirks, its safe/confirm action
lists, and its terms status::

    {app_id, identity_signatures, vocabulary, quirks, safe_actions,
     confirm_actions, terms_status, adapter_version,
     verified_app_versions}

This module is the FRAMEWORK — registry, signature matching,
vocabulary resolution, quirk-failure refusal, safe/confirm
enforcement, generic fallback, and receipt naming. It ships ZERO
app adapters: the owner has not yet picked the first three (open
D6 question), and no adapter is guessed into existence here.

Rules (from the §7 item-9 definition, which extends — never
weakens — the existing refusal semantics):

* Adapters are data + small resolvers on top of items 1–4. They
  never fork the funnel, the gate stack, or the receipt store:
  this module RESOLVES a goal to a menu path, a dialog spec, or
  a declared action — it never invokes, clicks, types, or
  dispatches anything. Execution stays with the funnel and the
  item-3 / item-4 engines, under C-14 approve-first.
* G2 LICENCE GATE IS ABSOLUTE. Every adapter record starts with
  ``terms_status`` UNREAD, and registration refuses any other
  starting value. The framework refuses to mark an adapter
  shippable until its vendor SDK/API licence (where depended on)
  and app automation terms (where restricted) are read and
  recorded via :func:`record_terms` (or explicitly ruled not
  applicable, with a reason, via :func:`mark_terms_not_applicable`).
  An adapter with unread terms ships nothing.
* Signature matching is positive evidence or nothing. A
  foreground identity matches a signature only when every field
  the signature names matches (process name compared
  case-insensitively; window class exact; title substring
  containment). No match → generic UIA fallback + honest note,
  never a guessed profile. A signature mismatch disables the
  adapter for that resolution (generic fallback + note), never a
  forced fit. Two adapters matching one foreground identity is
  ambiguous → generic fallback + note naming both, never a pick.
* Vocabulary is exact, never nearest. A spoken goal is
  normalised (stripped, lowercased, whitespace collapsed) and
  looked up verbatim in the adapter's vocabulary. An unknown
  phrase refuses, naming the phrase — it never resolves to a
  similar-sounding entry.
* Quirk failure refuses with the quirk named, never a pixel
  workaround. A vocabulary entry that depends on a quirk whose
  ``working`` flag is False refuses; a quirk that stops working
  is removed from the adapter, not worked around.
* Safe/confirm lists are enforced through this resolution, on
  top of (never instead of) the gate stack: an action in neither
  list refuses as undeclared; an action in ``confirm_actions``
  stops with ``needs_confirmation=True`` and resolves nothing
  until the caller re-resolves with ``confirmed=True``.
* Adapter versions are pinned to the app versions they were
  verified against. A supplied app version outside
  ``verified_app_versions`` disables the adapter for that
  resolution (generic fallback + note). A resolution with no
  app version supplied is honest about it (``version_pinned``
  False; the receipt says the version was not supplied).
* By-design walls are never adapter scope: registration refuses
  any vocabulary entry naming a password field, a CAPTCHA, or a
  secure-desktop target, and adapters never bundle vendor code
  or credentials.

The module never touches the screen, the funnel, or the gate
stack: resolution takes an explicit foreground identity, so
every rule is drivable headless. Real app behaviour — real
signatures, real vocabularies, real terms reads — stays field
work per adapter and is not claimed here.
"""

from __future__ import annotations

GENERIC_NOTE = "no profile for this app — using generic controls"

TERMS_UNREAD = "unread"
TERMS_READ = "read"
TERMS_NOT_APPLICABLE = "not_applicable"
_TERMS_STATUSES = (TERMS_UNREAD, TERMS_READ, TERMS_NOT_APPLICABLE)

_DIALOG_ACTIONS = ("open", "save", "folder")

_WALL_WORDS = ("password", "captcha", "secure desktop", "uac prompt")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict = {}


def reset_registry() -> None:
    """Empty the adapter registry (tests; never a product path)."""
    _REGISTRY.clear()


def list_adapters() -> list:
    """Registered app ids, sorted — never adapter records themselves."""
    return sorted(_REGISTRY)


def get_adapter(app_id):
    """A COPY of the registered record, or None. Callers never get
    the live record, so a caller cannot mutate the registry."""
    record = _REGISTRY.get(str(app_id or ""))
    return dict(record) if record is not None else None


def remove_adapter(app_id) -> bool:
    """Remove a registration. Returns True when one existed."""
    return _REGISTRY.pop(str(app_id or ""), None) is not None


# ---------------------------------------------------------------------------
# Validation (registration refuses, never repairs)
# ---------------------------------------------------------------------------

def _refusal(code, reason) -> dict:
    return {"code": code, "reason": reason}


def _fail(code, reason, **extra) -> dict:
    result = {"ok": False, "mode": None, "adapter_id": None,
              "adapter_version": None, "app_version": None,
              "version_pinned": False, "action": None, "kind": None,
              "target": None, "needs_confirmation": False,
              "note": None, "refusal": _refusal(code, reason)}
    result.update(extra)
    return result


def _clean_phrase(phrase) -> str:
    return " ".join(str(phrase or "").split()).lower()


def _validate_signature(signature, index) -> str | None:
    if not isinstance(signature, dict) or not signature:
        return "identity signature %d is empty or not an object" % index
    allowed = {"proc", "window_class", "title_substring"}
    unknown = sorted(set(signature) - allowed)
    if unknown:
        return ("identity signature %d names unknown fields: %s "
                "(proc, window_class, title_substring only)"
                % (index, ", ".join(unknown)))
    for field in ("proc", "window_class", "title_substring"):
        value = signature.get(field)
        if value is not None and not str(value).strip():
            return "identity signature %d has an empty %s" % (index, field)
    return None


def _validate_vocabulary_entry(phrase, entry, quirks) -> str | None:
    if not isinstance(entry, dict):
        return "vocabulary entry '%s' is not an object" % phrase
    kind = entry.get("kind")
    if kind == "menu":
        from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
        segments = _mp.parse_path(entry.get("path"))
        if not segments:
            return ("vocabulary entry '%s' names a menu with an "
                    "empty path" % phrase)
    elif kind == "dialog":
        error = _validate_dialog_spec(entry)
        if error is not None:
            return "vocabulary entry '%s': %s" % (phrase, error)
    elif kind == "action":
        pass
    else:
        return ("vocabulary entry '%s' has unknown kind '%s' "
                "(menu, dialog, action only)" % (phrase, kind))
    quirk = entry.get("quirk")
    if quirk is not None and quirk not in quirks:
        return ("vocabulary entry '%s' depends on undeclared quirk "
                "'%s'" % (phrase, quirk))
    haystack = " ".join(str(value) for value in entry.values()
                        if not isinstance(value, (dict, list))).lower()
    for word in _WALL_WORDS:
        if word in haystack or word in phrase:
            return ("vocabulary entry '%s' touches a by-design wall "
                    "(%s) — adapters never do" % (phrase, word))
    return None


def _validate_dialog_spec(entry) -> str | None:
    action = entry.get("action")
    if action not in _DIALOG_ACTIONS:
        return "unknown dialog action '%s' (open, save, folder)" % action
    if action == "folder" and entry.get("filename"):
        return "a folder pick takes no filename"
    if action in ("open", "save") and not str(entry.get("filename") or "").strip():
        return "a file %s needs a filename" % action
    return None


def _validate_adapter(record) -> str | None:
    """None when valid, else the refusal reason. Never repairs."""
    if not isinstance(record, dict):
        return "an adapter record must be an object"
    app_id = str(record.get("app_id") or "").strip()
    if not app_id:
        return "an adapter needs an app_id"
    signatures = record.get("identity_signatures")
    if not isinstance(signatures, list) or not signatures:
        return "adapter '%s' needs at least one identity signature" % app_id
    for index, signature in enumerate(signatures):
        error = _validate_signature(signature, index)
        if error is not None:
            return "adapter '%s': %s" % (app_id, error)
    vocabulary = record.get("vocabulary")
    if not isinstance(vocabulary, dict) or not vocabulary:
        return "adapter '%s' needs a non-empty vocabulary" % app_id
    quirks = record.get("quirks") or {}
    if not isinstance(quirks, dict):
        return "adapter '%s' quirks must be an object" % app_id
    for name, quirk in quirks.items():
        if not isinstance(quirk, dict) or not str(quirk.get("description") or "").strip():
            return ("adapter '%s' quirk '%s' needs a description"
                    % (app_id, name))
        if "working" in quirk and not isinstance(quirk["working"], bool):
            return ("adapter '%s' quirk '%s' working flag must be "
                    "true/false" % (app_id, name))
    for phrase, entry in vocabulary.items():
        if not _clean_phrase(phrase):
            return "adapter '%s' has an empty vocabulary phrase" % app_id
        error = _validate_vocabulary_entry(_clean_phrase(phrase), entry, quirks)
        if error is not None:
            return "adapter '%s': %s" % (app_id, error)
    safe = record.get("safe_actions") or []
    confirm = record.get("confirm_actions") or []
    if not isinstance(safe, (list, tuple)) or not isinstance(confirm, (list, tuple)):
        return "adapter '%s' safe/confirm actions must be lists" % app_id
    overlap = sorted(set(safe) & set(confirm))
    if overlap:
        return ("adapter '%s' lists actions as both safe and confirm: %s"
                % (app_id, ", ".join(overlap)))
    # Every vocabulary action must be declared safe or confirm;
    # an undeclared action is never run through the adapter.
    declared = set(safe) | set(confirm)
    for phrase, entry in vocabulary.items():
        action = _entry_action(entry)
        if action not in declared:
            return ("adapter '%s' vocabulary '%s' names undeclared "
                    "action '%s' — declare it safe or confirm first"
                    % (app_id, _clean_phrase(phrase), action))
    if record.get("terms_status", TERMS_UNREAD) != TERMS_UNREAD:
        return ("adapter '%s' must start with terms_status '%s' — "
                "terms are recorded after registration, never "
                "asserted at it" % (app_id, TERMS_UNREAD))
    version = str(record.get("adapter_version") or "").strip()
    if not version:
        return "adapter '%s' needs an adapter_version" % app_id
    verified = record.get("verified_app_versions") or []
    if not isinstance(verified, list):
        return "adapter '%s' verified_app_versions must be a list" % app_id
    return None


def _entry_action(entry) -> str:
    """The gate-stack action name a vocabulary entry resolves to."""
    if entry.get("kind") == "menu":
        from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
        segments = _mp.parse_path(entry.get("path"))
        return "menu:" + " → ".join(segments) if segments else "menu:"
    if entry.get("kind") == "dialog":
        return "dialog:" + str(entry.get("action"))
    return "action:" + str(entry.get("name") or entry.get("action") or "")


def register(record) -> dict:
    """Register one adapter record. Returns {ok, adapter_id} or
    {ok: False, refusal}. A failed registration stores nothing."""
    error = _validate_adapter(record)
    if error is not None:
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal("invalid-adapter", error)}
    app_id = str(record["app_id"]).strip()
    if app_id in _REGISTRY:
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal(
                    "duplicate-adapter",
                    "adapter '%s' is already registered" % app_id)}
    stored = {
        "app_id": app_id,
        "identity_signatures": [dict(sig) for sig in record["identity_signatures"]],
        "vocabulary": {_clean_phrase(p): dict(e)
                       for p, e in record["vocabulary"].items()},
        "quirks": {name: dict(quirk)
                   for name, quirk in (record.get("quirks") or {}).items()},
        "safe_actions": list(record.get("safe_actions") or []),
        "confirm_actions": list(record.get("confirm_actions") or []),
        "terms_status": TERMS_UNREAD,
        "terms_evidence": None,
        "adapter_version": str(record["adapter_version"]).strip(),
        "verified_app_versions": list(record.get("verified_app_versions") or []),
    }
    _REGISTRY[app_id] = stored
    return {"ok": True, "adapter_id": app_id}


# ---------------------------------------------------------------------------
# Terms gate (G2) — the only way an adapter becomes shippable
# ---------------------------------------------------------------------------

def record_terms(app_id, *, licence=None, terms=None, note="") -> dict:
    """Record a terms/licence read for an adapter. Requires at
    least one evidence reference (a licence/terms citation, never
    the licensed text itself and never a credential)."""
    record = _REGISTRY.get(str(app_id or ""))
    if record is None:
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal("unknown-adapter",
                                    "no adapter '%s' is registered" % app_id)}
    evidence = " ".join(part for part in
                        (str(licence or "").strip(),
                         str(terms or "").strip(),
                         str(note or "").strip()) if part)
    if not evidence:
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal(
                    "terms-evidence-required",
                    "a terms read needs a licence/terms citation — "
                    "an empty record keeps '%s' unshippable" % app_id)}
    record["terms_status"] = TERMS_READ
    record["terms_evidence"] = evidence
    return {"ok": True, "adapter_id": record["app_id"]}


def mark_terms_not_applicable(app_id, *, reason) -> dict:
    """Record that no vendor SDK/API licence is depended on and
    the app's terms do not restrict automation — with a reason.
    An empty reason refuses; the determination is recorded, not
    assumed."""
    record = _REGISTRY.get(str(app_id or ""))
    if record is None:
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal("unknown-adapter",
                                    "no adapter '%s' is registered" % app_id)}
    if not str(reason or "").strip():
        return {"ok": False, "adapter_id": None,
                "refusal": _refusal(
                    "terms-evidence-required",
                    "a not-applicable determination needs a reason")}
    record["terms_status"] = TERMS_NOT_APPLICABLE
    record["terms_evidence"] = str(reason).strip()
    return {"ok": True, "adapter_id": record["app_id"]}


def shippability(app_id) -> dict:
    """{shippable, reason}. Shippable requires a recorded terms
    read (or a reasoned not-applicable), an adapter version, and
    at least one verified app version. Unread terms can never
    ship — that is the G2 gate, not a warning."""
    record = _REGISTRY.get(str(app_id or ""))
    if record is None:
        return {"shippable": False, "adapter_id": None,
                "reason": "no adapter '%s' is registered" % app_id}
    if record["terms_status"] == TERMS_UNREAD:
        return {"shippable": False, "adapter_id": record["app_id"],
                "reason": ("terms unread for '%s' — read the vendor "
                           "licence / app automation terms first"
                           % record["app_id"])}
    if not record["verified_app_versions"]:
        return {"shippable": False, "adapter_id": record["app_id"],
                "reason": ("no app version verified for '%s' — pin "
                           "at least one verified version"
                           % record["app_id"])}
    return {"shippable": True, "adapter_id": record["app_id"],
            "reason": "terms recorded; versions pinned"}


# ---------------------------------------------------------------------------
# Signature matching
# ---------------------------------------------------------------------------

def _signature_matches(signature, foreground) -> bool:
    proc = signature.get("proc")
    if proc is not None and str(proc).lower() != str(foreground.get("proc") or "").lower():
        return False
    window_class = signature.get("window_class")
    if window_class is not None and str(window_class) != str(foreground.get("window_class") or ""):
        return False
    substring = signature.get("title_substring")
    if substring is not None and str(substring) not in str(foreground.get("title") or ""):
        return False
    return True


def identify(foreground) -> dict:
    """Match a foreground identity against every registered
    adapter. Returns {matched: adapter_id | None, ambiguous: [...],
    note}. Ambiguity is a generic fallback, never a pick."""
    if not isinstance(foreground, dict):
        return {"matched": None, "ambiguous": [],
                "note": GENERIC_NOTE}
    matched = [app_id for app_id, record in _REGISTRY.items()
               if any(_signature_matches(sig, foreground)
                      for sig in record["identity_signatures"])]
    if len(matched) == 1:
        return {"matched": matched[0], "ambiguous": [], "note": None}
    if len(matched) > 1:
        return {"matched": None, "ambiguous": sorted(matched),
                "note": ("ambiguous foreground — %s both match; "
                         "using generic controls"
                         % " and ".join(sorted(matched)))}
    return {"matched": None, "ambiguous": [], "note": GENERIC_NOTE}


def _generic_result(note, app_version=None) -> dict:
    return {"ok": True, "mode": "generic", "adapter_id": None,
            "adapter_version": None, "app_version": app_version,
            "version_pinned": False, "action": None, "kind": None,
            "target": None, "needs_confirmation": False,
            "note": note, "refusal": None}


# ---------------------------------------------------------------------------
# Goal resolution (the framework's one entry point)
# ---------------------------------------------------------------------------

def resolve(goal, *, foreground, app_version=None, confirmed=False) -> dict:
    """Resolve a spoken goal through the foreground app's adapter.

    Procedure (the definition's resolution order): (1) identify
    the foreground app by signature; (2) load its adapter, or
    fall back to generic; (3) resolve the goal through the
    adapter's vocabulary (menu paths per item 3, dialog specs
    per item 4); (4) enforce safe/confirm lists; (5) the receipt
    names adapter + app version (see :func:`receipt_line`).

    A resolution never executes anything: a menu target is a
    path for the item-3 engine to invoke, a dialog target is a
    spec for the item-4 engine to drive, and a confirm-gated
    resolution stops before any of that."""
    identification = identify(foreground)
    if identification["matched"] is None:
        return _generic_result(identification["note"],
                               app_version=app_version)
    record = _REGISTRY[identification["matched"]]
    app_id = record["app_id"]
    if app_version is not None:
        app_version = str(app_version).strip()
        if app_version not in record["verified_app_versions"]:
            return _generic_result(
                "adapter '%s' is not verified against app version "
                "'%s' — using generic controls" % (app_id, app_version),
                app_version=app_version)
        pinned = True
    else:
        app_version = None
        pinned = False
    phrase = _clean_phrase(goal)
    if not phrase:
        return _fail("empty-goal", "no goal was named — name what to do "
                     "in the app.", mode="adapter", adapter_id=app_id,
                     adapter_version=record["adapter_version"],
                     app_version=app_version, version_pinned=pinned)
    entry = record["vocabulary"].get(phrase)
    if entry is None:
        return _fail("unknown-phrase",
                     "adapter '%s' has no vocabulary for '%s' — "
                     "say one of its known phrases, or use generic "
                     "controls." % (app_id, phrase),
                     mode="adapter", adapter_id=app_id,
                     adapter_version=record["adapter_version"],
                     app_version=app_version, version_pinned=pinned)
    quirk = entry.get("quirk")
    if quirk is not None:
        quirk_record = record["quirks"].get(quirk) or {}
        if quirk_record.get("working", True) is False:
            return _fail("quirk-failed",
                         "adapter '%s' quirk '%s' is not working "
                         "(%s) — that action refuses rather than "
                         "falling back to a pixel workaround."
                         % (app_id, quirk,
                            quirk_record.get("description") or "no detail"),
                         mode="adapter", adapter_id=app_id,
                         adapter_version=record["adapter_version"],
                         app_version=app_version, version_pinned=pinned)
    action = _entry_action(entry)
    if action in record["confirm_actions"] and not confirmed:
        return _fail("confirmation-required",
                     "adapter '%s' action '%s' needs confirmation — "
                     "confirm it explicitly, then resolve again."
                     % (app_id, action),
                     mode="adapter", adapter_id=app_id,
                     adapter_version=record["adapter_version"],
                     app_version=app_version, version_pinned=pinned,
                     needs_confirmation=True)
    if action not in record["safe_actions"] and action not in record["confirm_actions"]:
        # Unreachable through register() validation, kept as a
        # defence in depth: a mutated registry must not leak an
        # undeclared action into a resolution.
        return _fail("action-not-declared",
                     "adapter '%s' action '%s' is declared neither "
                     "safe nor confirm — it refuses."
                     % (app_id, action),
                     mode="adapter", adapter_id=app_id,
                     adapter_version=record["adapter_version"],
                     app_version=app_version, version_pinned=pinned)
    target = _entry_target(entry)
    return {"ok": True, "mode": "adapter", "adapter_id": app_id,
            "adapter_version": record["adapter_version"],
            "app_version": app_version, "version_pinned": pinned,
            "action": action, "kind": entry.get("kind"),
            "target": target, "needs_confirmation": False,
            "note": None, "refusal": None}


def _entry_target(entry) -> dict:
    if entry.get("kind") == "menu":
        from . import menu_paths as _mp  # Phase 3 S7a-2: menu_paths already in core/desktop/  # Phase 3 S7a: menu_paths moved to core/desktop/
        return {"path": _mp.parse_path(entry.get("path"))}
    if entry.get("kind") == "dialog":
        return {"action": entry.get("action"),
                "folder": entry.get("folder"),
                "filename": entry.get("filename"),
                "file_type": entry.get("file_type")}
    return {"name": entry.get("name") or entry.get("action")}


def receipt_line(resolution) -> str:
    """One line for receipts / activity records. Always names the
    adapter + app version for adapter resolutions, and says so
    plainly when the version was not supplied or the fallback
    was generic."""
    if not isinstance(resolution, dict):
        return "no adapter resolution"
    if resolution.get("mode") == "generic":
        note = resolution.get("note") or GENERIC_NOTE
        return "Generic controls — %s" % note
    if not resolution.get("ok"):
        refusal = resolution.get("refusal") or {}
        return "Adapter %s refused: %s" % (
            resolution.get("adapter_id") or "?",
            refusal.get("reason") or "no reason given")
    version = resolution.get("app_version")
    version_text = ("app version %s" % version) if version else (
        "app version not supplied — version pin unverified")
    return "Adapter %s v%s (%s) — %s" % (
        resolution.get("adapter_id"),
        resolution.get("adapter_version"),
        version_text,
        resolution.get("action"))


def receipt_fields(resolution) -> dict:
    """Structured receipt fields: adapter + versions, never raw
    arguments, never credentials."""
    if not isinstance(resolution, dict):
        return {}
    return {"adapter_id": resolution.get("adapter_id"),
            "adapter_version": resolution.get("adapter_version"),
            "app_version": resolution.get("app_version"),
            "version_pinned": bool(resolution.get("version_pinned")),
            "mode": resolution.get("mode"),
            "action": resolution.get("action")}


def describe(resolution) -> str:
    """Alias for receipt_line (mirrors the item-3 / item-4 shape)."""
    return receipt_line(resolution)
