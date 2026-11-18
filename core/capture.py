"""Wave 5 (v5.60) — Senses capture rungs + token-economics ladder.

PC-S1 vs PC-S2 capture bake-off (DXcam DXGI vs windows-capture
WGC, including occluded windows), PC-S3 python-mss fallback +
doctor path, PC-S7 + PC-S9 token-economics ladder.

Capture-guard rule (INV-4, §8 S-6): the occluded-window class
ships ONLY with the capture-guard denylist/opt-in extension in
the SAME change. That extension is in this wave: the guard's
"occluded_screenshot" kind (per-session approval) plus the PF-17
user exclusion list (core/vision_exclusions.py). An occluded
capture without a live session approval is refused, full stop.

DXGI fallback is not optional: WGC raises E_ACCESSDENIED on
Win10 22H2-class hosts, so the ladder always falls through to
the next rung instead of failing the capture. GAPS-19: the
fallback rung is backed by the DXcam engine in
core/desktop/screen_capture.py (the one capture owner) — the
bake-off winner (DXcam vs windows-capture for the occluded
class) is still undecided pending live Windows trials.

Token ladder (PC-S7/S9): smart_resize factors are PINNED PER
MODEL. Only Qwen3-VL carries a plan-stated factor (32); no model
is pinned to 28, and 28 is never a default — an unknown model
raises instead of silently resizing at the wrong factor. Pillow
is the SINGLE encode path (resize first, then lowest legible
encode; text-bearing shots lossless or q>=90). OpenCV is not a
second stack: this module never imports it.
"""

from __future__ import annotations

import importlib.util

# ---------------------------------------------------------------------------
# PC-S1 / PC-S2 — capture bake-off probes (no winner on this VM)
# ---------------------------------------------------------------------------

CAPTURE_SEATS = ("dxcam", "windows-capture", "python-mss")


def _probe(package: str) -> bool:
    return importlib.util.find_spec(package) is not None


def dxcam_available() -> dict:
    """DXcam (DXGI duplication, monitor/region) probe."""
    import platform

    if platform.system() != "Windows":
        return {"available": False, "seat": "dxcam",
                "reason": "non-windows-host"}
    return {"available": _probe("dxcam"), "seat": "dxcam",
            "reason": "import-probe"}


def windows_capture_available() -> dict:
    """windows-capture (WGC per-window) probe."""
    import platform

    if platform.system() != "Windows":
        return {"available": False, "seat": "windows-capture",
                "reason": "non-windows-host"}
    return {"available": _probe("windows_capture"),
            "seat": "windows-capture",
            "reason": "import-probe"}


def mss_available() -> dict:
    """python-mss (guaranteed last rung) probe."""
    return {"available": _probe("mss"), "seat": "python-mss",
            "reason": "import-probe"}


def capture_bakeoff_status() -> dict:
    """Bake-off state: no winner without live Windows trials.

    DXcam vs windows-capture is decided by live trials on a real
    Windows host (monitor/region + occluded-window classes +
    failure modes incl. WGC E_ACCESSDENIED). Stub or Linux probe
    data cannot declare a winner.
    """
    return {
        "winner": None,
        "reason": "no-data",
        "seats": {
            "dxcam": dxcam_available(),
            "windows-capture": windows_capture_available(),
            "python-mss": mss_available(),
        },
        "detail": "No live Windows capture trial has run; the "
        "bake-off has no winner yet.",
    }


def doctor_capture_path() -> dict:
    """Doctor-facing capture-rung report (PC-S3 doctor path)."""
    status = capture_bakeoff_status()
    return {
        "rungs": [
            {"rung": 1, "name": "bakeoff-winner",
             "state": "undecided",
             "detail": "DXcam vs windows-capture winner TBD by "
             "live trials."},
            {"rung": 2, "name": "dxgi-fallback",
             "state": "always-present",
             "detail": "WGC E_ACCESSDENIED falls through here; "
             "the fallback is not optional. The engine behind it "
             "is the DXcam driver in core/desktop/screen_capture.py "
             "(GAPS-19: one capture owner)."},
            {"rung": 3, "name": "python-mss",
             "state": ("available" if status["seats"]["python-mss"]["available"]
                        else "unavailable"),
             "detail": "Guaranteed last rung when DXGI/WGC fail."},
        ],
        "occluded_class": occluded_capture_status(),
    }


# ---------------------------------------------------------------------------
# Occluded-window capture — guard extension (S-6, same change)
# ---------------------------------------------------------------------------

def occluded_capture_status() -> dict:
    """Whether an occluded-window capture may proceed.

    Allowed only when the current session already holds an
    approval for the guard kind "occluded_screenshot" (per-session
    opt-in) — the PF-17 user exclusion list applies on top at
    capture time. Anything else refuses; seeing windows the user
    cannot see is a new capture class, not a convenience.
    """
    try:
        from . import capture_guard
        approved = capture_guard.is_capture_approved(
            "occluded_screenshot")
    except Exception:  # noqa: BLE001 — fail closed
        approved = False
    if approved:
        return {"allowed": True, "kind": "occluded_screenshot",
                "reason": "session-approved"}
    return {
        "allowed": False,
        "kind": "occluded_screenshot",
        "reason": "no-session-approval",
        "detail": "Occluded-window capture needs a per-session "
        "opt-in first; without it the capture is refused.",
    }


def occluded_window_capture(window_title: str) -> dict:
    """Occluded-window capture entry point (gated, never silent)."""
    status = occluded_capture_status()
    if not status["allowed"]:
        return {"ok": False, "window": window_title, **status}
    return {
        "ok": False,
        "window": window_title,
        "reason": "engine-unavailable",
        "detail": "Session approval exists, but no WGC engine "
        "is wired on this host.",
    }


def capture_ladder() -> list:
    """The rung order a capture walks, top to bottom.

    With no bake-off winner, the ladder is [undecided-winner,
    dxgi-fallback, python-mss]: the winner slot is explicit, not
    silently filled by whichever import happens to succeed.
    """
    return ["bakeoff-winner-undecided", "dxgi-fallback",
            "python-mss-last-rung"]


# ---------------------------------------------------------------------------
# PC-S7 + PC-S9 — token-economics ladder (Pillow only)
# ---------------------------------------------------------------------------

# Per-model smart_resize factors, PINNED per model. Only Qwen3-VL
# carries a plan-stated factor (32, "used in places"). No model
# is pinned to 28, and 28 is never a default: an unknown model
# raises UnknownModelFactor instead of resizing at a wrong factor.
SMART_RESIZE_FACTORS = {
    "qwen3-vl-2b": 32,
    "qwen3-vl-4b": 32,
}


class UnknownModelFactor(Exception):
    """No pinned smart_resize factor exists for this model."""


def factor_for(model: str) -> int:
    """The pinned smart_resize factor for one model, or raise."""
    factor = SMART_RESIZE_FACTORS.get(model)
    if factor is None:
        raise UnknownModelFactor(
            "No pinned smart_resize factor for %r; refusing to "
            "resize at a guessed factor (28 is never a default)."
            % model)
    return factor


def resize_for_model(image, model: str):
    """Resize a Pillow image to the model's pinned factor grid.

    The image's longest side is snapped down to a multiple of the
    model's factor (never up). Pillow is the only resize path;
    this module never imports OpenCV.
    """
    from PIL import Image

    factor = factor_for(model)
    width, height = image.size
    longest = max(width, height)
    target = max(factor, (longest // factor) * factor)
    if longest == target:
        return image
    scale = target / longest
    new_size = (max(1, int(width * scale)),
                max(1, int(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def encode_frame(image, text_bearing: bool = True) -> tuple[bytes, str]:
    """Encode a Pillow image: the SINGLE encode path.

    Resize first (the caller resizes via resize_for_model), then
    the lowest legible encode: text-bearing shots are lossless
    PNG, or JPEG at q>=90 otherwise. Returns (bytes, format).
    """
    import io

    from PIL import Image  # noqa: F401 — Pillow is the only stack

    buffer = io.BytesIO()
    if text_bearing:
        image.save(buffer, format="PNG")
        return buffer.getvalue(), "PNG"
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    return buffer.getvalue(), "JPEG"
