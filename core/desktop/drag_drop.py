"""True drag-and-drop — §7 item 5 (parent draft).

Drags carry a REAL OLE data object the target accepts as a drop,
replacing today's pointer-gesture simulation (``core.desktop.cursor`` notes
its ``ole_drag_drop`` carries no OLE data object, so a drop target
that needs a real one never receives one).

A drag is::

    {source_handle, payload, target_handle}

where ``payload`` is files (canonical paths), text, or a URL, carried
in a real data object with the format the target advertises, and
``source_handle`` / ``target_handle`` are item-1 handles re-proven
at drop time against one item-2 snapshot's observations. Coordinates
are converted per-monitor at execution time (item 6) when a monitor
reference + norm1000 point are supplied — never from a consent-time
table.

Resolution procedure (definition §5):

1. Resolve source + target handles (item 1). A vanished source, a
   closed target, or an identity that cannot be re-proven refuses
   with the item-1 refusal — never a guessed target.
2. Refuse by-design walls (secure desktop, password, CAPTCHA
   surfaces) and auto-run/startup destinations before any drag.
3. A destructive target (trash, format, overwrite) STOPS for
   confirmation: the result carries ``needs_confirmation=True``
   and performs no drag until re-called with
   ``confirm_destructive=True`` (default deny).
4. Negotiate the format: the target must advertise the format the
   payload kind needs (files -> CF_HDROP, text -> CF_UNICODETEXT,
   URL -> UniformResourceLocator). A target that advertises no
   matching format refuses naming the target.
5. Build the data object from the declared payload fields ONLY —
   the object never carries credentials, and a payload carrying
   any field outside {kind, files, text, url} refuses outright.
6. Begin the OLE drag (DoDragDrop) and drop on the re-proven
   target. Verify the consequence the target exposes where the
   backend offers one, else report dropped-without-verification
   honestly — never a claimed success.
7. A failed drop leaves the source untouched: this engine performs
   NO filesystem move or delete itself (a move effect belongs to
   the target app, never to this module), so a refused or failed
   drop cannot half-move a file.

Fallback contract (never a silent downgrade):

* Target without OLE support refuses naming the target. The
  pointer-gesture alternative runs ONLY where the caller explicitly
  allows it AND the backend proves the gesture for that target
  (``gesture_proven``); the receipt always names which mechanism
  ran — OLE or gesture.
* A target whose kind/profile changed between consent and
  execution refuses (``target-changed-kind``), naming both kinds.

Result shape (mirrors ``core.desktop.file_dialogs``)::

    {ok, mechanism, source_handle_id, target_handle_id,
     payload_kind, payload_summary, target_kind, snapshot_stamp,
     monitor_id, drop_point, effect, verification,
     needs_confirmation, invoked, source_untouched, refusal}

``mechanism`` is ``"ole"``, ``"gesture"``, or ``None`` (nothing ran).
``invoked`` is True only when a drag actually ran. ``refusal`` is
``{code, reason}`` or ``None``; every refusal reason ends with an
instruction (re-observe), except wall refusals, which no
re-observation can fix.

Backends: :func:`drag` takes an optional ``backend`` implementing
the drag protocol (``advertised_formats``,
``current_target_profile`` [optional], ``build_data_object``,
``do_drag_drop``, ``verify_drop`` [optional], ``gesture_proven``,
``gesture_drag``). The default backend degrades honestly
off-Windows (``ole_available=False``): no OLE drag is claimed on a
host that cannot run one. Tests drive the protocol with a fake
backend; live OLE behaviour on real Windows — real DoDragDrop,
real IDataObject formats, real drop effects — stays field work
and is not claimed here (this lab has no Windows UI).

Deliberately unbuilt here (named follow-ups, same as items 3–4):
no funnel tool registration — the coordinator/voice step that
carries handle ids + payload is the consumer follow-up, not
something this module pretends exists. DECISION D3 stays open:
further payload kinds (images, rich content) and further
target-gating defaults beyond the draft proposal are NOT decided
here — the draft proposal (files/text/URLs first, destructive
targets always gated) is what is built.
"""

import os

from . import monitors as _mon
from . import target_handles as _th

__all__ = [
    "drag",
    "describe",
    "default_backend",
    "REOBSERVE_INSTRUCTION",
    "PAYLOAD_KINDS",
    "FORMAT_FOR_KIND",
    "DESTRUCTIVE_KINDS",
    "WALL_KINDS",
    "AUTO_RUN_FRAGMENTS",
]

REOBSERVE_INSTRUCTION = _th.REOBSERVE_INSTRUCTION

PAYLOAD_KINDS = ("files", "text", "url")

#: OLE clipboard format per payload kind (the format the target
#: must advertise for the drop to be a real OLE drop).
FORMAT_FOR_KIND = {
    "files": "CF_HDROP",
    "text": "CF_UNICODETEXT",
    "url": "UniformResourceLocator",
}

#: Target kinds that are destructive by nature: a drop here is
#: confirm-gated with default deny, never a silent effect.
DESTRUCTIVE_KINDS = (
    "trash",
    "recycle-bin",
    "recycle bin",
    "format",
    "overwrite",
    "delete",
    "shred",
)

#: By-design walls: dragging into these surfaces is excluded,
#: never bypassed, and no re-observation can change that.
WALL_KINDS = (
    "secure-desktop",
    "secure desktop",
    "password-field",
    "password",
    "captcha",
)

#: Windows auto-run/startup destinations, matched as a
#: case-insensitive substring of the normalised canonical target
#: path. A file drop into one of these is refused by policy:
#: dragging executable content into an auto-run location is
#: never a voice action. Callers may pass extra blocked
#: locations for their own policy (and tests use that seam).
AUTO_RUN_FRAGMENTS = (
    "start menu\\programs\\startup",
    "start menu/programs/startup",
    "microsoft\\windows\\start menu\\programs\\startup",
)

#: Payload fields the data object may be built from. Anything else
#: in a payload dict refuses — the data object never carries
#: credentials, and no undeclared field can smuggle one in.
_PAYLOAD_FIELDS = ("kind", "files", "text", "url")

_DROP_EFFECTS = ("copy", "move", "link")


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _refusal(code, reason, instruction=REOBSERVE_INSTRUCTION):
    if instruction:
        reason = reason + " " + instruction
    return {"code": code, "reason": reason}


def _fail(code, reason, source_handle_id, target_handle_id, payload_kind,
          target_kind, snapshot_stamp, instruction=REOBSERVE_INSTRUCTION,
          **extra):
    result = {
        "ok": False,
        "mechanism": None,
        "source_handle_id": source_handle_id,
        "target_handle_id": target_handle_id,
        "payload_kind": payload_kind,
        "payload_summary": None,
        "target_kind": target_kind,
        "snapshot_stamp": snapshot_stamp,
        "monitor_id": None,
        "drop_point": None,
        "effect": None,
        "verification": None,
        "needs_confirmation": False,
        "invoked": False,
        "source_untouched": True,
        "refusal": _refusal(code, reason, instruction),
    }
    result.update(extra)
    return result


def _summarise_payload(payload):
    kind = payload.get("kind")
    if kind == "files":
        return {"files": list(payload.get("files") or [])}
    if kind == "text":
        text = payload.get("text") or ""
        return {"text_length": len(text), "text_preview": text[:40]}
    if kind == "url":
        return {"url": payload.get("url")}
    return None


def _normalise_kind(value):
    return str(value or "").strip().lower()


def _is_auto_run_path(canonical_path, blocked_locations):
    # Case-insensitive on every host: Windows paths are
    # case-insensitive, and this policy must not depend on the
    # host's normcase behaviour (posix normcase is identity).
    normalised = os.path.normcase(os.path.abspath(canonical_path or "")
                                  ).lower()
    for fragment in AUTO_RUN_FRAGMENTS:
        if os.path.normcase(fragment).lower() in normalised:
            return True
    for location in blocked_locations or ():
        blocked = os.path.normcase(os.path.abspath(str(location))).lower()
        if normalised == blocked or normalised.startswith(blocked + os.sep):
            return True
    return False


# ---------------------------------------------------------------------------
# Default backend — honest degradation, no Windows UI here
# ---------------------------------------------------------------------------


class _DefaultBackend:
    """No OLE on this host: every drag path refuses honestly.

    ``ole_available`` is False, so :func:`drag` never reaches a
    format negotiation it cannot honour, and ``gesture_proven``
    is False, so no gesture is ever offered as a silent fallback.
    Live Windows behaviour is field work, not claimed here.
    """

    ole_available = False

    def advertised_formats(self, target):
        return []

    def current_target_profile(self, target):
        return None

    def build_data_object(self, payload, format_name):
        return None

    def do_drag_drop(self, data_object, source, target, point):
        return {"effect": "none"}

    def verify_drop(self, target, payload):
        return None

    def gesture_proven(self, target):
        return False

    def gesture_drag(self, source, target, point):
        return False


def default_backend():
    return _DefaultBackend()


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def _validate_payload(payload):
    """Returns (payload, refusal-or-None). Canonicalises file paths."""
    if not isinstance(payload, dict):
        return None, _refusal(
            "invalid-payload", "the drag payload must be an object "
            "with a kind of files, text, or url.")
    unknown = [k for k in payload if k not in _PAYLOAD_FIELDS]
    if unknown:
        return None, _refusal(
            "unsupported-payload-field",
            "the drag payload carries undeclared field(s) %s, so "
            "no data object is built from it — a data object is "
            "built only from the declared payload fields and never "
            "carries credentials." % ", ".join(sorted(unknown)))
    kind = payload.get("kind")
    if kind not in PAYLOAD_KINDS:
        return None, _refusal(
            "invalid-payload",
            "the drag payload kind must be files, text, or url, "
            "not %r." % (kind,))
    if kind == "files":
        files = payload.get("files")
        if not isinstance(files, list) or not files or not all(
                isinstance(f, str) and f.strip() for f in files):
            return None, _refusal(
                "invalid-payload",
                "a files drag needs a non-empty list of file paths.")
        canonical = [os.path.realpath(os.path.abspath(f)) for f in files]
        for path in canonical:
            if not os.path.exists(path):
                return None, _refusal(
                    "source-vanished",
                    "the dragged file '%s' no longer exists, so "
                    "the drag refuses rather than dropping a file "
                    "that is not there." % path)
        clean = {"kind": "files", "files": canonical}
        return clean, None
    if kind == "text":
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return None, _refusal(
                "invalid-payload", "a text drag needs non-empty text.")
        return {"kind": "text", "text": text}, None
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return None, _refusal(
            "invalid-payload", "a URL drag needs a non-empty URL.")
    if not (url.startswith("http://") or url.startswith("https://")):
        return None, _refusal(
            "invalid-payload",
            "a URL drag needs an http(s) URL, not %r." % (url,))
    return {"kind": "url", "url": url}, None


# ---------------------------------------------------------------------------
# The drag
# ---------------------------------------------------------------------------


def drag(source_handle_id, target_handle_id, payload, *,
         source_observation=None, target_observation=None,
         snapshot=None, backend=None, target_kind="",
         confirm_destructive=False, allow_gesture=False,
         monitor_reference=None, target_norm1000=None,
         monitor_backend=None, blocked_locations=()):
    """Drag a payload from a consented source onto a consented target.

    ``source_handle_id`` / ``target_handle_id`` are item-1 handle
    ids; ``source_observation`` / ``target_observation`` are the
    current observations they resolve against (taken from the one
    item-2 snapshot the caller passes as ``snapshot`` — the stamp
    is recorded, and no second observation path is taken inside
    this call). ``target_kind`` is the consented target kind
    (folder, editor, trash, ...); a backend-reported profile that
    contradicts it refuses. ``monitor_reference`` + ``target_norm1000``
    convert the drop point per-monitor at execution time (item 6);
    without them the backend drops on the resolved target itself.
    """
    snapshot_stamp = None
    if isinstance(snapshot, dict):
        snapshot_stamp = snapshot.get("stamp")
    kind_of = _normalise_kind(target_kind)

    clean, refusal = _validate_payload(payload)
    if refusal is not None:
        # The refusal reason already ends with the re-observe
        # instruction; pass instruction="" so it is kept as-is.
        return _fail(refusal["code"], refusal["reason"],
                     source_handle_id, target_handle_id,
                     payload.get("kind") if isinstance(payload, dict)
                     else None,
                     target_kind, snapshot_stamp, instruction="")
    payload_kind = clean["kind"]
    summary = _summarise_payload(clean)

    # 1. Re-prove both handles at drop time (item 1). Any item-1
    # refusal passes through with its own code and instruction.
    source = _th.resolve(source_handle_id, source_observation or {})
    if not source["ok"]:
        return _fail(source["refusal"]["code"],
                     source["refusal"]["reason"],
                     source_handle_id, target_handle_id, payload_kind,
                     target_kind, snapshot_stamp, instruction="",
                     payload_summary=summary)
    target = _th.resolve(target_handle_id, target_observation or {})
    if not target["ok"]:
        return _fail(target["refusal"]["code"],
                     target["refusal"]["reason"],
                     source_handle_id, target_handle_id, payload_kind,
                     target_kind, snapshot_stamp, instruction="",
                     payload_summary=summary)
    source_resolved = source["resolved"]
    target_resolved = target["resolved"]

    # A file source must be one of the dragged files: the consented
    # source and the payload must agree, or nothing drags.
    if source["kind"] == "file" and payload_kind == "files":
        if source_resolved.get("path") not in clean["files"]:
            return _fail(
                "source-payload-mismatch",
                "the consented source file '%s' is not among the "
                "dragged files, so the drag refuses rather than "
                "dragging a file that was not consented to."
                % source_resolved.get("path"),
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)

    # 2. By-design walls, before any drag is attempted.
    if kind_of in WALL_KINDS:
        return _fail(
            "by-design-wall",
            "dragging into a %s surface is excluded by design and "
            "is never bypassed." % kind_of,
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, instruction="",
            payload_summary=summary)

    # 3. Destructive target: stop for confirmation, zero drags.
    if kind_of in DESTRUCTIVE_KINDS and not confirm_destructive:
        return _fail(
            "destructive-needs-confirmation",
            "dropping onto a %s target is destructive, so the drag "
            "stops for confirmation and nothing has been dragged — "
            "re-call with confirm_destructive=True to proceed."
            % kind_of,
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, needs_confirmation=True,
            payload_summary=summary)

    # 4. Auto-run / startup destinations: refused by policy.
    if payload_kind == "files" and target["kind"] == "file":
        target_path = target_resolved.get("path")
        if target_path and _is_auto_run_path(target_path,
                                             blocked_locations):
            return _fail(
                "auto-run-location",
                "dropping files into '%s' is refused by policy: "
                "dragging content into an auto-run / startup "
                "location is never a voice action." % target_path,
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)

    # 5. Drop point, converted per-monitor at execution time.
    monitor_id = None
    drop_point = None
    if monitor_reference is not None:
        if target_norm1000 is None:
            return _fail(
                "monitor-point-missing",
                "a monitor-targeted drag needs a norm1000 target "
                "point to convert.",
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)
        try:
            nx, ny = target_norm1000
        except (TypeError, ValueError):
            return _fail(
                "monitor-point-missing",
                "a monitor-targeted drag needs a norm1000 (x, y) "
                "target point to convert.",
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)
        resolved_point, point_refusal = _mon.resolve_for_action(
            monitor_reference, nx, ny, backend=monitor_backend)
        if resolved_point is None:
            return _fail(
                "monitor-unresolved", point_refusal,
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, instruction="",
                payload_summary=summary)
        monitor_id = _mon._monitor_id(resolved_point["monitor"])
        drop_point = resolved_point["point"]

    be = backend if backend is not None else default_backend()

    # 6. No OLE on this host: refuse naming the target, unless the
    # caller explicitly allows a gesture AND the backend proves
    # the gesture for this target. Never a silent downgrade.
    if not getattr(be, "ole_available", True):
        gesture_ok = False
        try:
            gesture_ok = bool(be.gesture_proven(target_resolved))
        except Exception:  # noqa: BLE001 — a broken probe proves nothing
            gesture_ok = False
        if not (allow_gesture and gesture_ok):
            return _fail(
                "target-no-ole-support",
                "the target '%s' does not support an OLE drop, so "
                "the drag refuses rather than pretending a pointer "
                "gesture carried data it did not carry."
                % describe_target(target, target_kind),
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)
        return _run_gesture(be, source_resolved, target_resolved,
                            drop_point, source_handle_id,
                            target_handle_id, clean, summary,
                            target_kind, snapshot_stamp, monitor_id)

    # 7. Target kind/profile must not have changed since consent.
    try:
        profile = be.current_target_profile(target_resolved)
    except Exception:  # noqa: BLE001 — no profile is no evidence
        profile = None
    if isinstance(profile, dict) and target_kind:
        profile_kind = _normalise_kind(profile.get("kind"))
        if profile_kind and profile_kind != kind_of:
            return _fail(
                "target-changed-kind",
                "the target changed kind between consent and "
                "execution ('%s' vs consented '%s'), so the drag "
                "refuses rather than dropping onto a different "
                "kind of target." % (profile.get("kind"), target_kind),
                source_handle_id, target_handle_id, payload_kind,
                target_kind, snapshot_stamp, payload_summary=summary)

    # 8. Format negotiation: the target must advertise the format.
    needed = FORMAT_FOR_KIND[payload_kind]
    try:
        advertised = be.advertised_formats(target_resolved) or []
    except Exception as exc:  # noqa: BLE001 — backend failure refuses
        return _fail(
            "format-negotiation-failed",
            "the target's advertised drop formats could not be "
            "read (%s)." % exc,
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, payload_summary=summary)
    if needed not in advertised:
        return _fail(
            "format-not-advertised",
            "the target '%s' does not advertise the %s format "
            "this %s drag needs (it advertises: %s), so the drag "
            "refuses rather than dropping data the target cannot "
            "accept."
            % (describe_target(target, target_kind), needed,
               payload_kind,
               ", ".join(advertised) if advertised else "nothing"),
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, payload_summary=summary)

    # 9. Build the data object from declared fields only.
    try:
        data_object = be.build_data_object(clean, needed)
    except Exception as exc:  # noqa: BLE001 — backend failure refuses
        return _fail(
            "data-object-failed",
            "the OLE data object could not be built (%s)." % exc,
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, payload_summary=summary)
    if data_object is None:
        return _fail(
            "data-object-failed",
            "the OLE data object could not be built: the backend "
            "returned no data object.",
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, payload_summary=summary)

    # 10. The OLE drag itself; the effect belongs to the target.
    try:
        outcome = be.do_drag_drop(data_object, source_resolved,
                                  target_resolved, drop_point) or {}
    except Exception as exc:  # noqa: BLE001 — backend failure refuses
        return _fail(
            "drop-failed",
            "the OLE drag failed (%s); the source was not moved "
            "or deleted by this engine." % exc,
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, invoked=True,
            payload_summary=summary)
    effect = outcome.get("effect")
    if effect not in _DROP_EFFECTS:
        # "none" (or anything unrecognised) is a failed drop: the
        # engine moved nothing, so the source is untouched.
        return _fail(
            "drop-failed",
            "the target did not accept the drop (effect: %s), so "
            "the drop is reported as failed and the source is "
            "untouched — this engine never moves or deletes a "
            "source file itself." % (effect,),
            source_handle_id, target_handle_id, payload_kind,
            target_kind, snapshot_stamp, invoked=True,
            payload_summary=summary)
    try:
        verification = be.verify_drop(target_resolved, clean)
    except Exception:  # noqa: BLE001 — no verification is honest
        verification = None
    return {
        "ok": True,
        "mechanism": "ole",
        "source_handle_id": source_handle_id,
        "target_handle_id": target_handle_id,
        "payload_kind": payload_kind,
        "payload_summary": summary,
        "target_kind": target_kind,
        "snapshot_stamp": snapshot_stamp,
        "monitor_id": monitor_id,
        "drop_point": drop_point,
        "effect": effect,
        "verification": verification
        if verification else "dropped-without-verification",
        "needs_confirmation": False,
        "invoked": True,
        # A move effect belongs to the target app: only then may
        # the source have been relocated by the drop itself. This
        # engine never moves or deletes a source file.
        "source_untouched": effect != "move",
        "refusal": None,
    }


def _run_gesture(be, source_resolved, target_resolved, drop_point,
                 source_handle_id, target_handle_id, clean, summary,
                 target_kind, snapshot_stamp, monitor_id):
    """The proven-gesture alternative, receipted as a gesture.

    Runs only when the caller allowed it and the backend proved
    it for this target. The receipt says "gesture" — never OLE.
    """
    try:
        ran = bool(be.gesture_drag(source_resolved, target_resolved,
                                   drop_point))
    except Exception as exc:  # noqa: BLE001 — gesture failure refuses
        return _fail(
            "gesture-failed",
            "the pointer-gesture drag failed (%s); the source "
            "was not moved or deleted by this engine." % exc,
            source_handle_id, target_handle_id, clean["kind"],
            target_kind, snapshot_stamp, invoked=True,
            payload_summary=summary)
    if not ran:
        return _fail(
            "gesture-failed",
            "the pointer-gesture drag did not run, so the drop "
            "is reported as failed and the source is untouched.",
            source_handle_id, target_handle_id, clean["kind"],
            target_kind, snapshot_stamp, invoked=True,
            payload_summary=summary)
    try:
        verification = be.verify_drop(target_resolved, clean)
    except Exception:  # noqa: BLE001 — no verification is honest
        verification = None
    return {
        "ok": True,
        "mechanism": "gesture",
        "source_handle_id": source_handle_id,
        "target_handle_id": target_handle_id,
        "payload_kind": clean["kind"],
        "payload_summary": summary,
        "target_kind": target_kind,
        "snapshot_stamp": snapshot_stamp,
        "monitor_id": monitor_id,
        "drop_point": drop_point,
        "effect": "gesture",
        "verification": verification
        if verification else "gesture-without-verification",
        "needs_confirmation": False,
        "invoked": True,
        "source_untouched": True,
        "refusal": None,
    }


def describe_target(target, target_kind):
    """A short name for a resolved target, for refusal text."""
    resolved = (target or {}).get("resolved") or {}
    if resolved.get("description"):
        return resolved["description"]
    if target_kind:
        return target_kind
    return "target"


def describe(result):
    """One receipt line naming source, payload, target, mechanism."""
    if not result.get("ok"):
        refusal = result.get("refusal") or {}
        return "Drag refused (%s): %s" % (
            refusal.get("code"), refusal.get("reason"))
    summary = result.get("payload_summary") or {}
    if result.get("payload_kind") == "files":
        payload_text = "%d file(s)" % len(summary.get("files") or [])
    elif result.get("payload_kind") == "text":
        payload_text = "text (%d chars)" % (
            summary.get("text_length") or 0)
    else:
        payload_text = "URL %s" % (summary.get("url") or "")
    return ("Dragged %s onto %s via %s (effect: %s; verification: "
            "%s)." % (payload_text, result.get("target_kind") or
                       "target", result.get("mechanism"),
                       result.get("effect"),
                       result.get("verification")))
