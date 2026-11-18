"""Stable target handles — §7 item 1 (parent draft, session-only).

A handle is a small record, never a bare coordinate::

    {kind, identity, fingerprint, observed_at}

``kind`` is one of ``window``, ``control``, ``mark`` or ``file``.
A handle, once consented to, stays addressable across the changes
that do not change what its target IS — and refuses, never
guesses, when it cannot be proven to be the same target.

Rules (from the §7 item-1 definition, which extends — never
weakens — the existing refusal semantics):

* Consent is bound to identity, not to pixels. Between consent
  and execution, if the target's identity cannot be re-proven,
  the action stops — even if the screen looks the same. (Deep
  Audit TOCTOU rule: authorise against stable target identity.)
* Windows/controls resolve strongest-first: ``hwnd``, then UIA
  automation id, then ``pid``, then title. A comparable mismatch
  on a STABLE field (hwnd, automation id, pid) refuses, naming
  the field. Title is the weakest field and is expected to
  change (a document appending " — modified", a rename): a
  title mismatch refuses only when no stable field matched.
  At least one comparable field must match; a minted identity
  whose fields are all unobservable at resolve time refuses
  with "identity evidence missing". A title-only match never
  overrides a stable-field contradiction.
* A control that is positively unusable at resolve time refuses
  (mirroring ``core.desktop.uia._control_usable``: unknown state reads
  as usable, only positively-known bad state refuses).
* Marks follow the existing F-07 stale-refusal semantics
  exactly (``core.desktop.uia._marks_stale_reason``): a foreground
  handle that differs refuses even when the title matches; when
  either handle is unknown the title rule is the fallback; an
  unreadable title never counts as a change. The engine never
  upgrades a refusal the current code would make — title-edit
  survival lives at the WINDOW-handle level (same hwnd, new
  title resolves), while a mark table is a snapshot: a renamed
  document needs a fresh grounding table, and the refusal says
  so. A mark's centre must still fall inside the current
  virtual bounds.
* Files resolve by canonical path: the literal path is
  canonicalised (symlinks/junctions resolved) at mint time and
  re-resolved at execution time; a mismatch refuses (the path
  was retargeted), and a file that existed at mint time and is
  gone at resolve time refuses.
* Handles are SESSION-SCOPED (draft default; persisted handles
  are the owner's open D1 question and are NOT built here). A
  handle minted under a different session refuses, and a stored
  record whose fingerprint no longer matches its content
  refuses as tampered — never repaired.
* The store is the authority: callers receive copies, never
  live records, and resolution looks the handle up by id. An
  unknown id refuses. The store caps at 256 live handles
  (oldest evicted); resolving an evicted id refuses as unknown.

The module never touches the screen, the funnel, or the gate
stack: resolution takes explicit observations, so every rule is
drivable headless. The live consumer is ``core.desktop.uia.click_by_mark``,
which mints a mark handle from the validated mark + last-marks
stamp and resolves it before clicking; the click result names
the handle id so the dispatch is auditable against the
consented target. Window/control/file resolution is the engine
plus tests here; the coordinator step that carries a handle id
is the named follow-up consumer, not something this module
pretends exists.

By-design walls (UAC secure desktop, browser password fields,
CAPTCHAs) are never bypassed: this module addresses targets,
it grants no permission (C-14 approve-first still governs
send / delete / purchase through the existing gate stack).
"""

import hashlib
import json
import os
import threading
import uuid

__all__ = [
    "mint_window",
    "mint_control",
    "mint_mark",
    "mint_file",
    "resolve",
    "get_handle",
    "revoke",
    "list_handles",
    "describe",
    "begin_session",
    "current_session_id",
    "reset_store",
    "STORE_CAP",
    "REOBSERVE_INSTRUCTION",
]

KINDS = ("window", "control", "mark", "file")

#: Strongest-first identity fields for windows/controls. ``proc``
#: is carried as a descriptor only — it never proves sameness
#: (many windows share a process name) and never refuses alone.
_IDENTITY_FIELDS = ("hwnd", "automation_id", "pid", "title")

STORE_CAP = 256

REOBSERVE_INSTRUCTION = (
    "Re-observe (take_screenshot or screen_look) and pick a fresh target."
)

_lock = threading.Lock()
_store = {}  # handle id -> stored record (never handed out live)
_counter = 0
_session_id = uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Session + store plumbing
# ---------------------------------------------------------------------------


def current_session_id() -> str:
    """The current session token (handles minted under any other
    token refuse)."""
    return _session_id


def begin_session() -> str:
    """Start a fresh session, returning the previous session id
    (tests restore it in cleanup so later suites are unaffected).
    All handles minted under the previous session refuse from
    now on — session scope is the draft default, and persisted
    handles are deliberately not built."""
    global _session_id
    with _lock:
        old = _session_id
        _session_id = uuid.uuid4().hex
        return old


def reset_store() -> None:
    """Forget every live handle (tests only). Never called by
    product code: a session's handles live until eviction or
    process end."""
    global _counter
    with _lock:
        _store.clear()
        _counter = 0


def _fingerprint(kind, payload, session, seq) -> str:
    """Content hash of a minted record: recomputed at resolve
    time, so a mutated stored record refuses as tampered."""
    blob = json.dumps(
        {"kind": kind, "payload": payload, "session": session, "seq": seq},
        sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _store_handle(kind, payload, description) -> dict:
    """Mint, store, and return a COPY of the handle record."""
    global _counter
    with _lock:
        _counter += 1
        seq = _counter
        handle_id = "th-%s-%d" % (kind, seq)
        record = {
            "id": handle_id,
            "kind": kind,
            "payload": payload,
            "description": description,
            "session": _session_id,
            "seq": seq,
        }
        record["fingerprint"] = _fingerprint(
            kind, payload, _session_id, seq)
        _store[handle_id] = record
        while len(_store) > STORE_CAP:
            _store.pop(next(iter(_store)))
        return dict(record)


def _lookup(handle_id):
    """The stored record, or None. Never returns a live record
    to callers — resolve works on this copy."""
    with _lock:
        record = _store.get(handle_id)
        return dict(record) if record is not None else None


def get_handle(handle_id):
    """A copy of the stored handle record, or None when unknown
    (evicted, revoked, or never minted)."""
    record = _lookup(handle_id)
    if record is None:
        return None
    record.pop("payload", None)
    return record


def revoke(handle_id) -> bool:
    """Forget one handle. True when it existed."""
    with _lock:
        return _store.pop(handle_id, None) is not None


def list_handles() -> list:
    """Copies of live handle records (payloads stripped), oldest
    first."""
    with _lock:
        out = []
        for record in _store.values():
            copy = dict(record)
            copy.pop("payload", None)
            out.append(copy)
        return out


def describe(handle) -> str:
    """A human name for a handle record, for receipts."""
    if not isinstance(handle, dict):
        return "an unknown target"
    desc = str(handle.get("description") or "").strip()
    if desc:
        return desc
    return "a %s target" % (handle.get("kind") or "unknown")


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


def _clean_identity(identity) -> dict:
    """Identity fields with empty values dropped (an empty title
    or a None hwnd is unknown, never evidence)."""
    cleaned = {}
    for field in _IDENTITY_FIELDS:
        value = (identity or {}).get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[field] = value
    proc = (identity or {}).get("proc")
    if isinstance(proc, str) and proc.strip():
        cleaned["proc"] = proc
    return cleaned


def mint_window(identity, description="") -> dict:
    """Mint a window handle. Raises ValueError when the identity
    carries no positive field — a handle with nothing to prove
    sameness with refuses at mint time, not at execution."""
    cleaned = _clean_identity(identity)
    if not any(f in cleaned for f in _IDENTITY_FIELDS):
        raise ValueError(
            "cannot mint a window handle: the identity carries no "
            "hwnd, automation id, pid, or title to prove sameness with")
    if not description:
        title = cleaned.get("title")
        description = ("window '%s'" % title) if title else "a window"
    return _store_handle("window", {"identity": cleaned}, description)


def mint_control(identity, name="", control_type="") -> dict:
    """Mint a control handle. ``name``/``control_type`` are weak
    evidence: they only ever break a tie, never override a
    stronger-field match or contradiction (see resolve)."""
    cleaned = _clean_identity(identity)
    if not any(f in cleaned for f in _IDENTITY_FIELDS):
        raise ValueError(
            "cannot mint a control handle: the identity carries no "
            "hwnd, automation id, pid, or title to prove sameness with")
    label = str(name or "").strip() or "an unnamed control"
    desc = "%s '%s'" % (control_type or "control", label) \
        if control_type else label
    return _store_handle("control", {
        "identity": cleaned,
        "name": str(name or ""),
        "control_type": str(control_type or ""),
    }, desc)


def mint_mark(mark, stamp) -> dict:
    """Mint a mark handle from a grounding-table row plus the
    table's observation stamp ``{hwnd, title}``. Marks carry no
    identity of their own (id + rectangle only), so the stamp IS
    the identity — and stamp fields may legitimately be unknown
    (F-07: unknown never counts as a change)."""
    row = dict(mark or {})
    stamp = dict(stamp or {})
    mark_id = row.get("id")
    label = str(row.get("name") or "").strip() or "an unnamed mark"
    desc = "mark %s '%s'" % (mark_id, label) if mark_id is not None \
        else label
    return _store_handle("mark", {
        "mark": row,
        "stamp": {"hwnd": stamp.get("hwnd"), "title": stamp.get("title")},
    }, desc)


def mint_file(path) -> dict:
    """Mint a file handle: the literal path is canonicalised
    (symlinks/junctions resolved) at consent time. Raises
    ValueError on an empty path."""
    literal = str(path or "").strip()
    if not literal:
        raise ValueError("cannot mint a file handle for an empty path")
    literal_abs = os.path.abspath(literal)
    canonical = os.path.realpath(literal_abs)
    return _store_handle("file", {
        "literal_abs": literal_abs,
        "canonical": canonical,
        "existed": os.path.exists(canonical),
    }, "file '%s'" % canonical)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _refusal(code, reason) -> dict:
    return {"code": code,
            "reason": reason + " " + REOBSERVE_INSTRUCTION}


def _resolve_identity(minted, candidate):
    """(ok, refusal-or-None) for one window/control candidate.

    Stable fields (hwnd, automation id, pid) are identity: any
    comparable mismatch on one refuses — a recycled hwnd in a
    different process, or a different automation id in the same
    window, is a different target. Title is the weakest field
    and is EXPECTED to change (a document appending
    " — modified", a window renamed): a title mismatch refuses
    only when no stable field matched, i.e. when title is the
    only evidence and it disagrees. A stable match with a new
    title is survival, not contradiction. No comparable field
    at all refuses as missing evidence."""
    candidate = candidate or {}
    stable_matched = False
    for field in ("hwnd", "automation_id", "pid"):
        minted_val = minted.get(field)
        cand_val = candidate.get(field)
        if minted_val is None or cand_val is None:
            continue
        if str(minted_val) != str(cand_val):
            return False, _refusal(
                "identity-contradiction",
                "the target's %s changed: minted %r, now %r — "
                "this is not the consented target, so the action "
                "refuses rather than acting on a look-alike."
                % (field, minted_val, cand_val))
        stable_matched = True
    if stable_matched:
        return True, None
    minted_title = minted.get("title")
    cand_title = candidate.get("title")
    if minted_title is not None and cand_title is not None:
        if str(minted_title) != str(cand_title):
            return False, _refusal(
                "identity-contradiction",
                "the target's title changed: minted %r, now %r — "
                "and no stronger identity field proves sameness, "
                "so the action refuses rather than acting on a "
                "look-alike." % (minted_title, cand_title))
        return True, None
    return False, _refusal(
        "identity-evidence-missing",
        "the target's identity cannot be re-proven: none of "
        "the minted identity fields (hwnd, automation id, pid, "
        "title) is observable now.")


def _resolve_window_control(record, observation):
    """Window/control resolution over one identity or a list of
    candidates. The strongest non-contradicting match wins; a
    positively-unusable best match refuses instead of falling
    through to a weaker look-alike."""
    minted = record["payload"]["identity"]
    if "candidates" in observation:
        candidates = observation.get("candidates") or []
    elif "identity" in observation:
        candidates = [{"identity": observation.get("identity"),
                       "usable": observation.get("usable"),
                       "name": observation.get("name"),
                       "control_type": observation.get("control_type")}]
    else:
        candidates = []
    best = None
    best_strength = -1
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        ok, refusal = _resolve_identity(minted, cand.get("identity"))
        if not ok:
            # A contradiction on a candidate is evidence AGAINST
            # that candidate, not a global refusal — unless it is
            # the only candidate, handled below.
            if len(candidates) == 1:
                return False, refusal
            continue
        strength = sum(
            1 for f in _IDENTITY_FIELDS
            if minted.get(f) is not None
            and cand.get("identity", {}).get(f) is not None)
        if strength > best_strength:
            best, best_strength = cand, strength
    if best is None:
        if not candidates:
            return False, _refusal(
                "target-not-present",
                "no current target was offered to resolve against — "
                "the consented target cannot be proven present.")
        if len(candidates) == 1:
            # Single-candidate contradiction already returned
            # above; reaching here means evidence missing.
            return False, _refusal(
                "identity-evidence-missing",
                "the target's identity cannot be re-proven: none "
                "of the minted identity fields is observable now.")
        return False, _refusal(
            "target-not-present",
            "no currently-observable target matches the minted "
            "identity — the consented target is gone or replaced.")
    if best.get("usable") is False:
        reason = best.get("unusable_reason") or "disabled or hidden"
        return False, _refusal(
            "target-unusable",
            "the consented target is present but unusable (%s), "
            "so the action refuses." % reason)
    if record["kind"] == "control":
        minted_name = str(record["payload"].get("name") or "")
        cand_name = str(best.get("name") or "")
        if minted_name and cand_name and minted_name != cand_name:
            # A name change on a strongly-identified control is a
            # relabel, not a replacement — but only when a strong
            # field actually matched. A name-only match that
            # differs is no match at all.
            strong = any(
                minted.get(f) is not None
                and best.get("identity", {}).get(f) is not None
                for f in ("hwnd", "automation_id", "pid"))
            if not strong:
                return False, _refusal(
                    "target-not-present",
                    "the only name-matching candidate is named "
                    "differently ('%s' vs minted '%s') and no "
                    "stronger identity field proves sameness."
                    % (cand_name, minted_name))
    resolved = {"identity": dict(best.get("identity") or {}),
                "description": describe(record)}
    if best.get("name") is not None:
        resolved["name"] = best.get("name")
    if best.get("control_type") is not None:
        resolved["control_type"] = best.get("control_type")
    return True, resolved


def _resolve_mark(record, observation):
    """Mark resolution mirrors F-07 exactly: a foreground handle
    that differs refuses even when the title matches; when
    either handle is unknown the title rule is the fallback; an
    unreadable title never counts as a change. Plus the
    on-screen bounds check from click_by_mark."""
    payload = record["payload"]
    mark = dict(payload.get("mark") or {})
    stamp = payload.get("stamp") or {}
    foreground = observation.get("foreground") or {}
    fg_hwnd = foreground.get("hwnd")
    fg_title = foreground.get("title")
    if (stamp.get("hwnd") is not None and fg_hwnd is not None
            and fg_hwnd != stamp.get("hwnd")):
        return False, _refusal(
            "stale-foreground",
            "the grounding marks are stale — they were drawn "
            "under a different window than the one now in front "
            "(the foreground window changed, even though its "
            "title is still '%s')." % (stamp.get("title") or ""))
    if (stamp.get("title") and fg_title
            and fg_title != stamp.get("title")):
        return False, _refusal(
            "stale-foreground",
            "the grounding marks are stale — they were drawn "
            "under '%s' but the foreground window is now '%s'."
            % (stamp.get("title"), fg_title))
    rect = mark.get("rect")
    try:
        left, top, right, bottom = (int(v) for v in rect)
    except (TypeError, ValueError):
        return False, _refusal(
            "no-rectangle",
            "the consented mark has no usable rectangle on record.")
    bounds = observation.get("bounds")
    if bounds is not None:
        try:
            bl, bt, br, bb = (int(v) for v in bounds)
        except (TypeError, ValueError):
            bl = bt = br = bb = None
        if bl is not None:
            cx, cy = (left + right) // 2, (top + bottom) // 2
            if not (bl <= cx <= br and bt <= cy <= bb):
                return False, _refusal(
                    "out-of-bounds",
                    "the consented mark's centre (%d, %d) is no "
                    "longer on-screen (virtual bounds %s) — the "
                    "screen changed since the marks were drawn."
                    % (cx, cy, tuple(bounds)))
    return True, {"mark": mark,
                  "rect": (left, top, right, bottom),
                  "description": describe(record)}


def _resolve_file(record, observation):
    """File resolution: re-canonicalise the minted literal path;
    a changed canonical path means the path was retargeted
    (symlink/junction swap) and refuses; a file that existed at
    mint time and is gone refuses."""
    payload = record["payload"]
    now_canonical = os.path.realpath(payload["literal_abs"])
    if now_canonical != payload["canonical"]:
        return False, _refusal(
            "path-retargeted",
            "the consented file path now resolves to a different "
            "file ('%s' vs minted '%s') — the path changed "
            "between consent and execution, so the action "
            "refuses rather than acting on a different file."
            % (now_canonical, payload["canonical"]))
    if payload.get("existed") and not os.path.exists(now_canonical):
        return False, _refusal(
            "target-gone",
            "the consented file '%s' no longer exists."
            % now_canonical)
    return True, {"path": now_canonical,
                  "description": describe(record)}


def resolve(handle_id, observation):
    """Resolve a minted handle against a current observation.

    Returns ``{"ok", "handle_id", "kind", "resolved",
    "refusal"}`` — ``resolved`` carries the re-proven target
    (plus ``handle_id``) on success, ``refusal`` carries
    ``{code, reason}`` (reason always ends with the re-observe
    instruction) on failure. Never raises on bad input: an
    unknown, foreign-session, or tampered handle is a refusal,
    not an exception."""
    try:
        record = _lookup(handle_id)
        if record is None:
            return {"ok": False, "handle_id": handle_id, "kind": None,
                    "resolved": None,
                    "refusal": _refusal(
                        "unknown-handle",
                        "no such target handle is on record (it "
                        "was never minted, was revoked, or was "
                        "evicted).")}
        if record.get("session") != _session_id:
            return {"ok": False, "handle_id": handle_id,
                    "kind": record.get("kind"), "resolved": None,
                    "refusal": _refusal(
                        "foreign-session",
                        "the target handle was minted under a "
                        "different session and cannot be reused.")}
        expect = _fingerprint(record["kind"], record["payload"],
                              record["session"], record["seq"])
        if record.get("fingerprint") != expect:
            return {"ok": False, "handle_id": handle_id,
                    "kind": record.get("kind"), "resolved": None,
                    "refusal": _refusal(
                        "tampered-handle",
                        "the target handle's stored record does "
                        "not match its mint fingerprint — it is "
                        "refused, not repaired.")}
        observation = observation or {}
        kind = record["kind"]
        if kind in ("window", "control"):
            ok, result = _resolve_window_control(record, observation)
        elif kind == "mark":
            ok, result = _resolve_mark(record, observation)
        elif kind == "file":
            ok, result = _resolve_file(record, observation)
        else:
            return {"ok": False, "handle_id": handle_id, "kind": kind,
                    "resolved": None,
                    "refusal": _refusal(
                        "unknown-kind",
                        "the target handle's kind is not one this "
                        "build resolves.")}
        if not ok:
            return {"ok": False, "handle_id": handle_id, "kind": kind,
                    "resolved": None, "refusal": result}
        resolved = dict(result)
        resolved["handle_id"] = handle_id
        return {"ok": True, "handle_id": handle_id, "kind": kind,
                "resolved": resolved, "refusal": None}
    except Exception as exc:  # noqa: BLE001 — resolution never raises
        return {"ok": False, "handle_id": handle_id, "kind": None,
                "resolved": None,
                "refusal": _refusal(
                    "resolution-error",
                    "target-handle resolution failed (%s)." % exc)}
