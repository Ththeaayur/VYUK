"""Wave 5 (v5.60) — Senses vision-model facades.

VR-1 Qwen3-VL, RD-1 Granite-Docling-258M, VR-3 Docling shell,
VR-2 RapidOCR (English-first), RD-6 Windows.Media.Ocr, and the
RD bake-off harness (GLM-OCR / OvisOCR2 / PaddleOCR-VL-1.6 /
LightOnOCR-2-1B / TrOCR).

This module is a FACADE layer, not an integration: no model is
downloaded, no weight is bundled, no inference runs here. Every
describe/read call goes through core/vision_backend (B5) — the
one adapter over the existing llama.cpp VLM machinery and the
existing onnxruntime dependency — and returns either a
model-produced result or an honest record naming what is
missing, never a fabricated transcription. Weights are
user-supplied (data/models, or VYUK_VISION_MODELS_DIR); nothing
is downloaded silently and nothing is bundled.

Tier accounting (INV-21) uses the plan's stated weight classes,
not measurements: Qwen3-VL 2B ~0.5–2 GB class, 4B ~2–4 GB class,
Granite-Docling-258M 0.5 GB bf16, bake-off seats 0.8–2 GB each.
Bake-off is never co-residence: a seat's size counts only if it
is the one that ships.

Licences: see core/pilot_licences_senses.py. GLM-OCR's seat is
LOCAL-ONLY: any remote endpoint is refused outright, because its
SDK's cloud mode forwards documents and never ships.
"""

from __future__ import annotations

from .pilot_licences_senses import shippable, verdict

# ---------------------------------------------------------------------------
# Tier accounting (INV-21) — stated classes, not measurements
# ---------------------------------------------------------------------------

# (bf16 GB class, low, high) — the plan's stated classes.
MODEL_CLASSES = {
    "qwen3-vl-2b": (2.0, 0.5, 2.0),
    "qwen3-vl-4b": (4.0, 2.0, 4.0),
    "granite-docling-258m": (0.5, 0.5, 0.5),
    "glm-ocr": (0.9, 0.8, 2.0),
    "ovisocr2": (0.8, 0.8, 2.0),
    "paddleocr-vl-1.6": (0.9, 0.8, 2.0),
    "lightonocr-2-1b": (1.0, 0.8, 2.0),
    "trocr": (0.3, 0.3, 0.5),
}

# The 8 GB co-residency budget (INV-21). The resident text tiers
# are accounted by the caller; this function only adds vision.
RAM_BUDGET_GB = 8.0


def tier_budget(vision_model: str, resident_gb: float) -> dict:
    """Accounting for one vision model beside resident tiers.

    Returns the class, the summed total, and whether the sum fits
    the 8 GB budget. This is arithmetic on stated classes, not a
    measurement of any running model.
    """
    entry = MODEL_CLASSES.get(vision_model)
    if entry is None:
        return {
            "ok": False,
            "model": vision_model,
            "reason": "unknown-model-class",
            "detail": "No stated weight class exists for this model; "
            "it cannot be budgeted.",
        }
    _, low, high = entry
    total_low = resident_gb + low
    total_high = resident_gb + high
    return {
        "ok": True,
        "model": vision_model,
        "class_gb": [low, high],
        "resident_gb": resident_gb,
        "total_gb": [total_low, total_high],
        "fits_8gb": total_high <= RAM_BUDGET_GB,
        "note": "Bake-off seats do not stack: only the shipping "
        "model counts toward the total.",
    }


# ---------------------------------------------------------------------------
# VR-1 — Qwen3-VL local vision facade
# ---------------------------------------------------------------------------

def qwen_describe(image_bytes: bytes, prompt: str,
                  model: str = "qwen3-vl-2b") -> dict:
    """Describe an image with a local Qwen3-VL model.

    Routed through core/vision_backend (B5): a model-produced
    description with timing, or an honest record naming what is
    missing — never a guessed description.
    """
    if model not in ("qwen3-vl-2b", "qwen3-vl-4b"):
        return {"ok": False, "engine": "qwen3-vl", "model": model,
                "reason": "unknown-model"}
    if not shippable("qwen3-vl"):
        return {"ok": False, "engine": "qwen3-vl", "model": model,
                "reason": "licence-blocked"}
    from .vision_backend import describe as _backend_describe

    return _backend_describe(image_bytes, prompt, model)


# ---------------------------------------------------------------------------
# RD-1 — Granite-Docling-258M reading facade
# ---------------------------------------------------------------------------

def granite_read_page(image_bytes: bytes, bbox: tuple | None = None) -> dict:
    """Read a page (or one bbox region) to structured output.

    bbox is the region mode that doubles as a screen-region reader
    for text brains. Routed through core/vision_backend (B5): a
    model-produced DocTags read with timing, or an honest record
    naming what is missing — never a fabricated transcription.
    """
    if not shippable("granite-docling-258m"):
        return {"ok": False, "engine": "granite-docling-258m",
                "reason": "licence-blocked"}
    from .vision_backend import read_page as _backend_read_page

    return _backend_read_page(image_bytes, bbox)


# ---------------------------------------------------------------------------
# VR-3 — Docling shell: ONE ingestion path (Wave 6 rides this)
# ---------------------------------------------------------------------------

DOCLING_MIN_VERSION = (2, 132, 0)  # CVE-2026-105744 floor (§7.1)


def docling_version_ok(version_str: str) -> bool:
    """True only for docling >= 2.132.0 (CVE-2026-105744 floor).

    Unparseable versions fail closed: an unknown version is not
    treated as a safe version.
    """
    try:
        parts = tuple(int(p) for p in version_str.strip().split(".")[:3])
        while len(parts) < 3:
            parts += (0,)
    except (ValueError, AttributeError):
        return False
    return parts >= DOCLING_MIN_VERSION


def docling_version_floor() -> str:
    return ".".join(str(p) for p in DOCLING_MIN_VERSION)


class DocumentShell:
    """The single document-ingestion path (VR-3, §6 D-7).

    Wave 6's PaperQA2 rides this shell + KN-4; no third ingestion
    path is created. The shell takes a backend object with a
    ``parse(document_bytes)`` method; with no backend configured
    it refuses instead of fabricating Markdown. Code MIT; models
    are per-backend (their licences gate the backend, not the
    shell).
    """

    def __init__(self, backend=None, backend_version: str = ""):
        self.backend = backend
        self.backend_version = backend_version

    def convert(self, document_bytes: bytes) -> dict:
        if self.backend is None:
            return {
                "ok": False,
                "shell": "docling",
                "reason": "no-backend-configured",
                "detail": "The Docling shell has no backend in "
                "v5.60; no document is converted.",
            }
        if self.backend_version and not docling_version_ok(
                self.backend_version):
            return {
                "ok": False,
                "shell": "docling",
                "reason": "backend-version-below-floor",
                "floor": docling_version_floor(),
                "detail": "Docling below 2.132.0 is refused "
                "(CVE-2026-105744).",
            }
        try:
            return self.backend.parse(document_bytes)
        except Exception as exc:  # noqa: BLE001 — backend failure
            return {"ok": False, "shell": "docling",
                    "reason": "backend-error", "detail": str(exc)}


# ---------------------------------------------------------------------------
# VR-2 — RapidOCR, English-first (his 2026-10-24 redirect)
# ---------------------------------------------------------------------------

# The Devanagari route is PARKED, not revoked: it is a known
# drop-in, and this facade refuses it explicitly instead of
# silently falling back to English.
RAPIDOCR_LANGS = ("en",)
PARKED_LANGS = ("hi", "devanagari")


def rapidocr_recognize(image_bytes: bytes, lang: str = "en") -> dict:
    """Recognise text, English-first.

    Non-English requests are refused with the parked reason, never
    silently answered in English. With no engine wired, English
    requests return the unavailable record.
    """
    if lang in PARKED_LANGS:
        return {
            "ok": False,
            "engine": "rapidocr",
            "lang": lang,
            "reason": "language-parked",
            "detail": "The Devanagari route is parked by his "
            "2026-10-24 English-first redirect, not revoked; "
            "it is a known drop-in, not a silent fallback.",
        }
    if lang not in RAPIDOCR_LANGS:
        return {"ok": False, "engine": "rapidocr", "lang": lang,
                "reason": "unsupported-language"}
    if not shippable("rapidocr"):
        return {"ok": False, "engine": "rapidocr", "lang": lang,
                "reason": "licence-blocked"}
    return {
        "ok": False,
        "engine": "rapidocr",
        "lang": lang,
        "reason": "engine-unavailable",
        "detail": "No RapidOCR backend is wired in v5.60.",
    }


# ---------------------------------------------------------------------------
# RD-6 — Windows.Media.Ocr presence detection ONLY
# ---------------------------------------------------------------------------

def winrt_ocr_status() -> dict:
    """Presence of the zero-download Windows.Media.Ocr fallback.

    Presence detection + graceful absence ONLY. Absence is a
    supported state: the returned message never instructs an
    install, a download, or a capability-pack add — installing
    one would be user setup, which the product forbids itself.
    """
    import platform

    if platform.system() != "Windows":
        return {
            "present": False,
            "engine": "windows-media-ocr",
            "reason": "non-windows-host",
            "action": "none",
            "message": "Windows.Media.Ocr is not available on "
            "this host; VYUK continues with its other "
            "reading paths.",
        }
    try:
        import winrt  # noqa: F401 — presence probe only
    except ImportError:
        return {
            "present": False,
            "engine": "windows-media-ocr",
            "reason": "winrt-bridge-absent",
            "action": "none",
            "message": "Windows.Media.Ocr is not reachable from "
            "this install; VYUK continues with its other "
            "reading paths.",
        }
    return {
        "present": True,
        "engine": "windows-media-ocr",
        "reason": "bridge-present",
        "action": "none",
        "message": "Windows.Media.Ocr is reachable as a "
        "zero-download fallback while a VLM loads.",
    }


# ---------------------------------------------------------------------------
# RD bake-off harness — one winner ships, losers leave
# ---------------------------------------------------------------------------

BAKEOFF_SEATS = (
    "glm-ocr",
    "ovisocr2",
    "paddleocr-vl-1.6",
    "lightonocr-2-1b",
    "trocr",
)


def glm_local_only(endpoint) -> dict:
    """GLM-OCR local-only gate: a remote endpoint is refused.

    The SDK's cloud mode forwards documents and never ships, so
    any http/https endpoint is a refusal, not a configuration.
    A local path, a local socket, or None (in-process) passes.
    """
    if isinstance(endpoint, str) and endpoint.lower().startswith(
            ("http://", "https://")):
        return {
            "ok": False,
            "seat": "glm-ocr",
            "reason": "remote-endpoint-refused",
            "detail": "GLM-OCR's cloud mode forwards documents "
            "and never ships; only a local backend is allowed.",
        }
    return {"ok": True, "seat": "glm-ocr", "endpoint": endpoint}


def select_bakeoff_winner(records: list) -> dict:
    """Pick the RD bake-off winner from measured records.

    A record is {seat, pages, seconds_per_page, accuracy} measured
    on the SHARED corpus. The winner is the highest-accuracy seat
    among seats that passed the CPU gate — but only when EVERY
    seat has a record on the same corpus. Any missing seat, any
    unmeasured seat, or any empty input yields NO winner
    (winner: None), never a winner declared on partial data.
    """
    by_seat = {}
    for record in records:
        seat = record.get("seat")
        if seat in BAKEOFF_SEATS:
            by_seat[seat] = record
    missing = [s for s in BAKEOFF_SEATS if s not in by_seat]
    if missing:
        return {
            "winner": None,
            "reason": "no-data" if not by_seat else "partial-data",
            "missing": missing,
            "detail": "A bake-off winner needs every seat "
            "measured on the shared corpus.",
        }
    ranked = sorted(
        by_seat.values(),
        key=lambda r: (r.get("accuracy", 0.0)),
        reverse=True,
    )
    winner = ranked[0]
    return {
        "winner": winner["seat"],
        "reason": "highest-accuracy",
        "accuracy": winner.get("accuracy"),
        "seconds_per_page": winner.get("seconds_per_page"),
        "losers_leave": [s for s in BAKEOFF_SEATS
                         if s != winner["seat"]],
        "detail": "Losers leave: their seats are removed, "
        "not kept as fallbacks.",
    }


def bakeoff_status() -> dict:
    """The bake-off's current state on this machine: no data."""
    return {
        "winner": None,
        "reason": "no-data",
        "seats": list(BAKEOFF_SEATS),
        "licences": {seat: verdict(seat)["licence"]
                     for seat in BAKEOFF_SEATS},
        "detail": "No bake-off corpus has been measured on a "
        "real machine; no winner exists yet.",
    }
