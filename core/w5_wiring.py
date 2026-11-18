"""Conversion pass — Wave 5 (v5.60, "Senses") wiring coordinator.

Wires the staged Wave 5 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, WITHOUT touching any locked file (core/funnel/tools_handlers.py,
core/funnel/tools_gate.py, core/w6_wiring.py, core/w7_wiring.py,
core/w8_wiring.py, core/memory/memory.py, core/browser.py,
core/music_player.py, core/confirm_gate.py, core/watchers.py,
core/brain/routine_scheduler.py, gui/*), and WITHOUT touching the staged
Wave 5 modules themselves. The only existing-file edits are two
best-effort hooks in core/desktop/vision.py (which is not locked): one in
VisionManager.capture_native_image, one in ground_marks_for_frame.
Both hooks are fail-closed — any failure inside them returns the
original image / marks unchanged, so a wiring fault can never
sink a capture.

Per-item truth (Phase 1 audit, NOT the wave's claims):

* Item 1, vision-model facade — GATED-SKIPPED, plumbing only.
  core/vision_models.py is a facade + bakeoff harness, not
  a wired model: every describe/read call returns an honest
  "unavailable" record, and select_bakeoff_winner() returns
  winner None on any partial data. No winner is adopted today,
  so NO model is presented as live anywhere in this module:
  vision_model_status() reports live_model None, and
  unavailable_descriptions() returns the facade's own ok=False
  records verbatim. The bakeoff harness itself IS wired as a
  measurement-only path (measure_bakeoff()): it accepts measured
  records and returns the staged verdict, never a winner declared
  on partial data. Closing step (see VISION_MODEL_GATE_OWED): a
  bakeoff winner adopted on live Windows trials PLUS the licence
  re-read the pilot licence file itself owes (Qwen3-VL official
  card frontmatter re-read before any weight bundling).
* Item 2, capture ladder / backends — WIRED (routing + evidence),
  Windows validation OWED. resolve_capture_rungs() /
  select_capture_backend() walk the staged ladder
  (capture.capture_ladder()) behind the existing backend
  routing: the winner slot is skipped explicitly because the
  bakeoff is undecided — it is NEVER silently filled by
  whichever import happens to succeed. The walk's concrete rungs
  are dxcam (the DXGI fallback) then python-mss (the last rung);
  windows-capture is reported as a bakeoff seat, not selected by
  the walk, because selecting it would pre-empt the undecided
  bakeoff. Every native capture in core/desktop/vision.py now also notes
  its route + frame evidence through note_capture_image() (see
  below), so the ladder runs on the live capture path, not just
  in tests. Each backend still needs live Windows validation
  (see WINDOWS_VALIDATION_OWED): offscreen Linux can only prove
  the routing logic with stubs, never that a backend grabs.
  encode_frame_for_model() wires the token-economics ladder
  (resize_for_model + encode_frame, Pillow only); an unknown
  model raises UnknownModelFactor — 28 is never a default.
  occluded_window_capture is wired as an explicit entry
  (occluded_capture_entry()): the capture-guard refusal is
  preserved verbatim, and even a session-approved call returns
  the staged engine-unavailable record on a host with no WGC
  engine wired. Its tool-shaped wrapper is REGISTRATION-READY
  (see REGISTRATION_OWED), not registered, because the
  dispatcher file is locked for this leaf.
* Item 3, PF-17 exclusions — ALREADY LIVE, verified, untouched.
  core/desktop/vision.py's capture_native_image imports foreground_probe
  and user_exclusion_block from core/vision_exclusions.py
  and raises CaptureBlockedError on a match, before any grab.
  pf17_still_live() re-verifies that call chain from source;
  this module adds no caller and changes no exclusion logic.
* Item 4, eyes fusion / evidence — WIRED on the live capture
  path, with evidence-digest discipline. Every native capture
  notes ONLY hashes (frame_hash) and a digest — frame bytes are
  never stored — via note_capture_image(), and every grounding
  capture (capture_screen with marks=True, the path screen_look
  uses) fuses its UIA marks table through note_grounding_fusion()
  (marks adapted to the axtree element shape, UIA-first, vision
  text ignored when UIA elements exist, exactly as
  fuse_uia_vision() specifies). wait_for_settled() supplies the
  live probe caller wait_for() needed: by default it polls the
  LIVE capture path (vision_manager.capture_screen) through a
  SettledScreen, so a timeout is a failed wait, never a pass,
  and a blocked capture surfaces as a probe-error, never as
  settled. fuse_screen() is the direct fuse + evidence entry
  for callers with an explicit frame pair: it returns the fuse
  plus both frame hashes, their diff_pct, and an evidence
  digest binding the record. assert_screen() is the
  assert_from_evidence passthrough: with no judge configured
  the verdict is "no-judge" (recorded, never passed).
  The one piece that is NOT live: the fused text is not yet
  appended to any tool result, because the only tool-result
  site (core/funnel/tools_handlers.py) is locked for this leaf — the
  fusion record is retrievable via last_fusion() /
  last_capture_evidence() and the tool-text step is
  REGISTRATION-READY with the capture-route tool (below).

No frame bytes, no screenshot, and no model output are ever
stored by this module: _LAST_EVIDENCE / _LAST_FUSION hold
hashes, digests, counts and source tags only.
"""

from __future__ import annotations

import hashlib
import inspect
import io

from . import capture
from . import eyes
from . import vision_models

# ---------------------------------------------------------------------------
# Owed steps (named, never silent)
# ---------------------------------------------------------------------------

VISION_MODEL_GATE_OWED = (
    "Bakeoff winner adoption + licence re-read: no winner is adopted "
    "today (select_bakeoff_winner returns winner None on partial data), "
    "and the pilot licence file itself owes a Qwen3-VL official card "
    "frontmatter re-read before any weight bundling. Until both exist, "
    "vision_model_status() reports live_model None and every describe / "
    "read call returns its honest unavailable record."
)

WINDOWS_VALIDATION_OWED = (
    "Live Windows validation of EACH capture backend (dxcam DXGI, "
    "windows-capture WGC incl. the occluded-window class and its "
    "E_ACCESSDENIED fall-through, python-mss last rung) on a real "
    "Windows host. Offscreen Linux proves only routing + selection "
    "logic with stubs; no backend is claimed to grab until its trial "
    "runs. The occluded class additionally needs its per-session "
    "opt-in approval, which the guard already enforces."
)

REGISTRATION_OWED = (
    "Tool registration in core/funnel/tools_handlers.py (FILE-LOCKED for this "
    "leaf, parallel Wave 6 registration leaf owns it): the tool-shaped "
    "wrappers in W5_TOOL_HANDLERS (capture_route, occluded_capture, "
    "eyes_fusion) are complete and tested, but no declaration or "
    "dispatch entry exists for them yet, so no fitted bytes were added "
    "by this leaf. The coordinator must measure the declaration cost "
    "against the 10,204 B headroom BEFORE registering — no cost is "
    "claimed here because no declaration was written."
)

# ---------------------------------------------------------------------------
# Item 2 — capture ladder / backends
# ---------------------------------------------------------------------------

# Ladder rung name -> (probe seat, probe function). The winner slot
# has no seat: it is skipped explicitly, never filled by a probe.
_LADDER_PROBES = {
    "dxgi-fallback": ("dxcam", capture.dxcam_available),
    "python-mss-last-rung": ("python-mss", capture.mss_available),
}


def resolve_capture_rungs(availability: dict | None = None) -> list:
    """Walk the staged ladder, rung by rung, with availability.

    availability (tests / callers only) maps a seat name to a bool
    and overrides the real probes for that seat; when None, the
    staged probes run. Each rung reports its seat, whether it is
    available, and why. The bakeoff-winner rung is always
    available=False with reason "bakeoff-undecided": the bakeoff
    has no winner, so the slot stays empty on purpose.
    """
    rungs = []
    for rung in capture.capture_ladder():
        if rung == "bakeoff-winner-undecided":
            rungs.append({
                "rung": rung, "seat": None, "available": False,
                "reason": "bakeoff-undecided",
                "detail": "DXcam vs windows-capture winner TBD by live "
                "Windows trials; this slot is never silently filled.",
            })
            continue
        seat, probe = _LADDER_PROBES.get(rung, (None, None))
        if probe is None:
            rungs.append({"rung": rung, "seat": seat, "available": False,
                          "reason": "unknown-rung"})
            continue
        if availability is not None and seat in availability:
            available = bool(availability[seat])
            reason = "stubbed-probe" if available else "stubbed-absent"
        else:
            reported = probe()
            available = bool(reported.get("available"))
            reason = reported.get("reason", "import-probe")
        rungs.append({"rung": rung, "seat": seat, "available": available,
                      "reason": reason})
    return rungs


def capture_seats(availability: dict | None = None) -> dict:
    """All three bakeoff seats' probes (incl. windows-capture).

    windows-capture is a bakeoff seat, not a ladder rung: it is
    reported here so a caller can see it, but select_capture_backend
    never selects it — selecting it would pre-empt the undecided
    bakeoff. A stubbed availability dict overrides per seat.
    """
    seats = {}
    for seat, probe in (
            ("dxcam", capture.dxcam_available),
            ("windows-capture", capture.windows_capture_available),
            ("python-mss", capture.mss_available)):
        if availability is not None and seat in availability:
            seats[seat] = {"available": bool(availability[seat]),
                           "seat": seat, "reason": "stubbed-probe"}
        else:
            seats[seat] = probe()
    return seats


def select_capture_backend(availability: dict | None = None) -> dict:
    """First available LADDER rung for this capture, or None.

    This is a fallback walk, NOT the bakeoff winner: "selected" is
    the rung a capture would try first today, while "winner"
    stays None until live Windows trials adopt one. With no rung
    available the selection is None with reason
    "no-backend-available" — never a guessed backend.
    """
    rungs = resolve_capture_rungs(availability)
    selected = None
    for rung in rungs:
        if rung["available"]:
            selected = rung["seat"]
            break
    return {
        "selected": selected,
        "winner": None,
        "winner_reason": "bakeoff-undecided",
        "rungs": rungs,
        "seats": capture_seats(availability),
        "reason": ("first-available-ladder-rung" if selected is not None
                   else "no-backend-available"),
    }


def capture_route_report(availability: dict | None = None) -> dict:
    """Doctor-facing capture-route report (measurement + routing).

    Combines the ladder walk, the bakeoff state and the staged
    doctor path, and names the Windows validation still owed.
    No backend is claimed to grab: "selected" is a routing
    decision, and on this host it is None unless a probe passes.
    """
    route = select_capture_backend(availability)
    return {
        "route": route,
        "bakeoff": capture.capture_bakeoff_status(),
        "doctor_path": capture.doctor_capture_path(),
        "windows_validation_owed": WINDOWS_VALIDATION_OWED,
    }


def encode_frame_for_model(image, model: str) -> tuple:
    """The token-economics ladder for one frame: resize + encode.

    resize_for_model pins the factor per model (Qwen3-VL: 32);
    an unknown model raises UnknownModelFactor instead of
    resizing at a guessed factor (28 is never a default).
    Returns (bytes, format) via the single Pillow encode path.
    """
    resized = capture.resize_for_model(image, model)
    return capture.encode_frame(resized, text_bearing=True)


def occluded_capture_status() -> dict:
    """Whether an occluded-window capture may proceed (guard-gated)."""
    return capture.occluded_capture_status()


def occluded_capture_entry(window_title: str) -> dict:
    """Explicit occluded-capture entry point (guard refusal kept).

    Without a per-session "occluded_screenshot" approval this
    returns the staged refusal verbatim. With approval but no
    WGC engine wired on this host it returns engine-unavailable
    — never a fabricated frame, never a silent capture.
    """
    return capture.occluded_window_capture(window_title)


# ---------------------------------------------------------------------------
# Item 4 — eyes fusion / evidence (hashes + digests only, no bytes stored)
# ---------------------------------------------------------------------------

_LAST_EVIDENCE: dict | None = None
_LAST_FUSION: dict | None = None


def last_capture_evidence() -> dict | None:
    """Copy of the last noted capture's evidence record (or None)."""
    return dict(_LAST_EVIDENCE) if _LAST_EVIDENCE is not None else None


def last_fusion() -> dict | None:
    """Copy of the last grounding fusion record (or None)."""
    return dict(_LAST_FUSION) if _LAST_FUSION is not None else None


def reset_evidence() -> None:
    """Clear the module's evidence / fusion records (tests only)."""
    global _LAST_EVIDENCE, _LAST_FUSION
    _LAST_EVIDENCE = None
    _LAST_FUSION = None
    if hasattr(note_capture_image, "_settled"):
        delattr(note_capture_image, "_settled")


def _png_bytes(image) -> bytes | None:
    """In-memory PNG bytes of a PIL image, or None on failure."""
    try:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 — evidence must never sink capture
        return None


def note_capture_image(image, availability: dict | None = None) -> object:
    """Live-capture hook: note route + frame evidence, keep the image.

    Called (best-effort) from core/desktop/vision.py's capture_native_image
    on every native frame, AFTER the denylist + PF-17 gates have
    passed and BEFORE the frame is returned. Records the ladder
    route, the frame hash, and whether the screen has settled
    (last 3 frame hashes identical, via SettledScreen); the frame
    bytes themselves are hashed in memory and never retained.
    Returns the image unchanged, always — this hook cannot fail
    a capture.
    """
    global _LAST_EVIDENCE
    try:
        route = select_capture_backend(availability)
        frame_bytes = _png_bytes(image)
        if frame_bytes is not None:
            digest = eyes.frame_hash(frame_bytes)
            settled_tracker = getattr(note_capture_image, "_settled", None)
            if settled_tracker is None:
                settled_tracker = eyes.SettledScreen(window=3)
                note_capture_image._settled = settled_tracker
            settled_tracker.observe(frame_bytes)
            settled = settled_tracker.settled()
        else:
            digest, settled = None, False
        _LAST_EVIDENCE = {
            "frame_hash": digest,
            "settled": settled,
            "route_selected": route["selected"],
            "route_winner": None,
            "evidence_digest": eyes.evidence_digest(
                digest or "", str(route["selected"])),
        }
    except Exception:  # noqa: BLE001 — evidence is best-effort only
        pass
    return image


def _marks_to_elements(marks: list) -> list:
    """Adapt a grounding marks table to axtree element shape.

    Marks carry id / name / control_type / rect (left, top, right,
    bottom); axtree elements carry role / name / rect (x, y, w, h)
    / depth. Unknown shapes are skipped, never guessed into.
    """
    elements = []
    for mark in marks or []:
        try:
            left, top, right, bottom = (int(v) for v in mark["rect"])
        except (KeyError, TypeError, ValueError):
            continue
        elements.append({
            "role": mark.get("control_type") or "unknown",
            "name": mark.get("name") or "",
            "rect": (left, top, right - left, bottom - top),
            "depth": 0,
        })
    return elements


def note_grounding_fusion(marks, image=None, vision_text: str = "") -> dict:
    """Live-grounding hook: fuse one marks table, record, return it.

    Called (best-effort) from core/desktop/vision.py's
    ground_marks_for_frame — the path capture_screen(marks=True)
    takes, which is the path screen_look uses. UIA-first: when
    marks exist the source is "uia" and any vision text is
    ignored (vision-LAST); empty marks + no vision text gives
    source "none", never a fabricated control. The record holds
    the fused text, the source tag, and (when an image is given)
    its frame hash + an evidence digest — no image bytes.
    """
    global _LAST_FUSION
    elements = _marks_to_elements(marks)
    fused = eyes.fuse_uia_vision(elements, vision_text)
    frame_bytes = _png_bytes(image) if image is not None else None
    frame_hash = (eyes.frame_hash(frame_bytes)
                  if frame_bytes is not None else None)
    record = dict(fused)
    record["frame_hash"] = frame_hash
    record["evidence_digest"] = eyes.evidence_digest(
        fused["source"], fused["text"], frame_hash or "")
    _LAST_FUSION = record
    return dict(record)


def fuse_screen(uia_elements, vision_text: str = "",
                frame_before: bytes | None = None,
                frame_after: bytes | None = None) -> dict:
    """Fuse UIA structure + vision text with evidence discipline.

    Returns the fuse (source tag included) plus, when a frame
    pair is supplied, both frame hashes, their diff_pct, and an
    evidence digest binding the record. Undecodable frames raise
    ValueError from diff_pct — never a silent 0% change.
    """
    fused = eyes.fuse_uia_vision(uia_elements, vision_text)
    out = dict(fused)
    if frame_before is not None and frame_after is not None:
        hash_before = eyes.frame_hash(frame_before)
        hash_after = eyes.frame_hash(frame_after)
        out["frame_hash_before"] = hash_before
        out["frame_hash_after"] = hash_after
        out["diff_pct"] = eyes.diff_pct(frame_before, frame_after)
        out["settled"] = hash_before == hash_after
    else:
        out["frame_hash_before"] = None
        out["frame_hash_after"] = None
        out["diff_pct"] = None
        out["settled"] = None
    out["evidence_digest"] = eyes.evidence_digest(
        fused["source"], fused["text"],
        out["frame_hash_before"] or "", out["frame_hash_after"] or "")
    return out


def settled_probe(capture_fn, window: int = 3):
    """Build a wait_for probe from a frame-supplying capture callable.

    Each poll captures one frame (capture_fn returns (bytes, mime)
    or raw bytes) and feeds its hash to a SettledScreen; the probe
    is True only once the last `window` hashes are identical.
    A capture failure propagates to wait_for as a probe-error —
    a blocked or failed capture is never reported as settled.
    """
    tracker = eyes.SettledScreen(window=window)

    def _probe():
        frame = capture_fn()
        if isinstance(frame, tuple):
            frame = frame[0]
        tracker.observe(frame)
        return tracker.settled()

    return _probe


def wait_for_settled(capture_fn=None, timeout_s: float = 10.0,
                     interval_s: float = 0.5, window: int = 3) -> dict:
    """Wait for the live screen to settle, via wait_for's probe shape.

    This is the live probe caller wait_for() needed: with no
    capture_fn given it polls the LIVE capture path
    (vision_manager.capture_screen), so the wait measures the
    real screen. A timeout returns verdict "timeout" (a failed
    wait, not a pass); a blocked capture returns "probe-error".
    Never called from a voice-path thread by this module — the
    caller owns the thread, exactly as the staged module's
    contract requires the caller to own capture gating.
    """
    if capture_fn is None:
        def _live_capture():
            from .desktop import vision as _vision  # Phase 3 D4c: vision hub moved to core/desktop/

            return _vision.vision_manager.capture_screen()
        capture_fn = _live_capture
    probe = settled_probe(capture_fn, window=window)
    return eyes.wait_for(probe, "screen-settled",
                              timeout_s=timeout_s, interval_s=interval_s)


def assert_screen(prompt: str, evidence: list, judge=None) -> dict:
    """Visual-assertion passthrough (no judge = recorded, not passed)."""
    return eyes.assert_from_evidence(prompt, evidence, judge=judge)


# ---------------------------------------------------------------------------
# Item 1 — vision-model facade: measurement-only plumbing, no live model
# ---------------------------------------------------------------------------

def vision_model_status() -> dict:
    """Measurement-only vision-model status: NO model is live.

    Reports the bakeoff state, the WinRT OCR presence probe, and
    the licence verdicts for the bakeoff seats — and live_model
    None, always, until a winner is adopted and licensed (see
    VISION_MODEL_GATE_OWED). Nothing here downloads weights or
    runs inference.
    """
    return {
        "live_model": None,
        "reason": "bakeoff-winner-plus-licence-owed",
        "bakeoff": vision_models.bakeoff_status(),
        "winrt_ocr": vision_models.winrt_ocr_status(),
        "gate_owed": VISION_MODEL_GATE_OWED,
    }


def measure_bakeoff(records: list) -> dict:
    """Run the staged bakeoff harness over MEASURED records only.

    Returns select_bakeoff_winner's verdict verbatim: winner None
    on empty or partial data, a winner only when every seat was
    measured on the shared corpus. This module never invents a
    record and never promotes a partial-data winner.
    """
    return vision_models.select_bakeoff_winner(records)


def unavailable_descriptions(image_bytes: bytes, prompt: str) -> dict:
    """The facade's own unavailable records, verbatim, for one frame.

    Every entry has ok False: no description, reading or
    recognition is claimed. Callers surface these records as
    "unavailable", never as model output.
    """
    return {
        "qwen_describe": vision_models.qwen_describe(
            image_bytes, prompt),
        "granite_read_page": vision_models.granite_read_page(
            image_bytes),
        "rapidocr_recognize": vision_models.rapidocr_recognize(
            image_bytes),
    }


def no_model_presented_as_live() -> bool:
    """Pin: every facade describe/read record is ok False, and the
    status reports no live model. Used by the suite; a future
    winner adoption must change this function deliberately, with
    its licence evidence, not by accident."""
    status = vision_model_status()
    if status["live_model"] is not None:
        return False
    probe = unavailable_descriptions(b"", "probe")
    return all(entry.get("ok") is False for entry in probe.values())


# ---------------------------------------------------------------------------
# Item 3 — PF-17 exclusions: verify live, touch nothing
# ---------------------------------------------------------------------------

def pf17_still_live() -> dict:
    """Re-verify the PF-17 call chain in core/desktop/vision.py from source.

    True only when capture_native_image imports foreground_probe
    and user_exclusion_block from core/vision_exclusions.py
    and raises CaptureBlockedError on a match. This function
    changes no exclusion logic; a False here is a regression
    flag for the coordinator, not something this module repairs
    by editing the exclusion module.
    """
    from .desktop import vision as _vision  # Phase 3 D4c: vision hub moved to core/desktop/

    try:
        source = inspect.getsource(
            _vision.VisionManager.capture_native_image)
    except (TypeError, OSError):
        return {"live": False, "reason": "source-unavailable"}
    live = ("vision_exclusions" in source
            and "foreground_probe" in source
            and "user_exclusion_block" in source
            and "CaptureBlockedError" in source)
    return {
        "live": live,
        "reason": ("pf17-in-live-capture-path" if live
                   else "pf17-call-chain-missing"),
    }


# ---------------------------------------------------------------------------
# Registration-ready tool-shaped wrappers (NOT registered — see
# REGISTRATION_OWED). No declaration exists for these, so they add
# zero fitted bytes. They return plain text, like live handlers.
# ---------------------------------------------------------------------------

def tool_capture_route() -> str:
    """READY, pending registration: capture-route report as text."""
    report = capture_route_report()
    route = report["route"]
    lines = [
        "Capture route (Wave 5 ladder):",
        f"  selected fallback rung: {route['selected']}",
        f"  bakeoff winner: {route['winner']} "
        f"({route['winner_reason']})",
        "  rungs: " + "; ".join(
            f"{r['rung']}={r['seat']} "
            f"({'available' if r['available'] else 'unavailable'})"
            for r in route["rungs"]),
        "  Windows validation of each backend is still owed "
        "before any backend is claimed to grab.",
    ]
    return "\n".join(lines)


def tool_occluded_capture(window_title: str) -> str:
    """READY, pending registration: occluded capture entry as text."""
    result = occluded_capture_entry(window_title)
    if result.get("ok"):
        return f"Occluded capture of {window_title!r} succeeded."
    return (f"Occluded capture of {window_title!r} refused: "
            f"{result.get('reason')} — "
            f"{result.get('detail', '')}".strip())


def tool_eyes_fusion() -> str:
    """READY, pending registration: last fusion / evidence as text."""
    fusion = last_fusion()
    evidence = last_capture_evidence()
    if fusion is None and evidence is None:
        return ("No grounding fusion or capture evidence has been "
                "noted yet.")
    parts = []
    if fusion is not None:
        parts.append(
            f"Last grounding fusion: source={fusion['source']}, "
            f"elements={fusion['elements']}, "
            f"digest={fusion['evidence_digest']}.")
    if evidence is not None:
        parts.append(
            f"Last capture evidence: frame_hash={evidence['frame_hash']}, "
            f"settled={evidence['settled']}, "
            f"route={evidence['route_selected']}.")
    return " ".join(parts)


W5_TOOL_HANDLERS = {
    "capture_route": tool_capture_route,
    "occluded_capture": tool_occluded_capture,
    "eyes_fusion": tool_eyes_fusion,
}
