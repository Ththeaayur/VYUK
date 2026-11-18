"""Wave 5 (v5.60) — Senses fusion + settled-screen + visual assertions.

EY-A1/A3: UIA-first / vision-last fusion + AXTree flattening, so
her text-only local brain works from structure, not pictures.
EY-B1/B2: settled-screen primitive — ImageHash + ODiff semantics
implemented in own Pillow code (neither package is imported):
"did it change / did it finish" answered by hashes and diff
percentages logged as evidence, which is MORE private than
screenshots because no image is ever stored.
EY-C1: Midscene visual-assertion semantics (aiAssert / aiWaitFor
shapes) re-implemented in own code — no Midscene code ships
(O-14 read closed in core/pilot_licences_senses.py). Assertion
screenshots are capture-gated by the CALLER under INV-4; this
module never captures anything and never sends anything to a
model. Its verdicts come only from caller-supplied evidence or
from an explicit judge callable: with no judge configured, the
verdict is "no-judge", never a fabricated pass.
"""

from __future__ import annotations

import hashlib
import io

# ---------------------------------------------------------------------------
# EY-A1/A3 — UIA-first / vision-last fusion + AXTree flattening
# ---------------------------------------------------------------------------

def flatten_axtree(elements: list) -> list:
    """Flatten accessibility-tree elements to numbered lines.

    Each element is {role, name, rect=(x, y, w, h), depth}.
    Lines are numbered in tree order so a text-only brain can
    point at one exact control ("click 3") without a picture.
    """
    lines = []
    for index, element in enumerate(elements, start=1):
        depth = int(element.get("depth", 0))
        role = element.get("role", "unknown")
        name = element.get("name", "")
        rect = element.get("rect")
        rect_text = (" @ (%d,%d %dx%d)" % tuple(rect)
                     if rect and len(rect) == 4 else "")
        lines.append("%s[%d] %s: %s%s"
                     % ("  " * depth, index, role, name, rect_text))
    return lines


def fuse_uia_vision(uia_elements: list, vision_text: str = "") -> dict:
    """Fuse UIA structure with vision text, UIA-first.

    UIA elements present -> source "uia": the brain reads
    structure, and vision text is ignored (vision-LAST, not
    vision-beside). UIA empty + vision text present -> source
    "vision". Both empty -> source "none". The source tag is part
    of the record so downstream code never mistakes a picture
    reading for structure.
    """
    if uia_elements:
        lines = flatten_axtree(uia_elements)
        return {
            "source": "uia",
            "text": "\n".join(lines),
            "elements": len(uia_elements),
            "vision_ignored": bool(vision_text),
        }
    if vision_text:
        return {
            "source": "vision",
            "text": vision_text,
            "elements": 0,
            "vision_ignored": False,
        }
    return {"source": "none", "text": "", "elements": 0,
            "vision_ignored": False}


# ---------------------------------------------------------------------------
# EY-B1/B2 — settled-screen primitive (hashes + diffs as evidence)
# ---------------------------------------------------------------------------

def frame_hash(image_bytes: bytes) -> str:
    """Average-hash of a PNG/JPEG frame, as hex.

    Own Pillow code (8x8 grayscale, bits vs the mean). Only the
    hash is ever logged — the frame itself is never stored.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("L")
    small = image.resize((8, 8), Image.LANCZOS)
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= mean else "0" for p in pixels)
    return "%016x" % int(bits, 2)


def diff_pct(image_a_bytes: bytes, image_b_bytes: bytes) -> float:
    """Percentage of pixels changed between two frames (0–100).

    ODiff semantics (percentage changed) in own Pillow code.
    Frames of different sizes compare at the smaller frame's size;
    undecodable input raises ValueError (never a silent 0).
    """
    from PIL import Image, ImageChops

    try:
        a = Image.open(io.BytesIO(image_a_bytes)).convert("RGB")
        b = Image.open(io.BytesIO(image_b_bytes)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 — undecodable input
        raise ValueError("undecodable frame: %s" % exc) from exc
    width = min(a.width, b.width)
    height = min(a.height, b.height)
    a = a.resize((width, height), Image.LANCZOS)
    b = b.resize((width, height), Image.LANCZOS)
    diff = ImageChops.difference(a, b).convert("L")
    pixels = list(diff.getdata())
    changed = sum(1 for p in pixels if p > 10)
    return round(100.0 * changed / max(1, len(pixels)), 3)


def evidence_digest(*parts: str) -> str:
    """Short hash binding an evidence record (no image stored)."""
    joined = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]


class SettledScreen:
    """'Did it change / did it finish' from hashes alone.

    Feed frame hashes; settled() is True when the last `window`
    hashes are identical — the screen stopped changing. No frame
    bytes are retained, only hashes.
    """

    def __init__(self, window: int = 3):
        self.window = max(1, window)
        self.history: list = []

    def observe(self, image_bytes: bytes) -> str:
        digest = frame_hash(image_bytes)
        self.history.append(digest)
        self.history = self.history[-self.window:]
        return digest

    def settled(self) -> bool:
        if len(self.history) < self.window:
            return False
        return len(set(self.history)) == 1


# ---------------------------------------------------------------------------
# EY-C1 — Midscene visual-assertion semantics (own code)
# ---------------------------------------------------------------------------

def assert_from_evidence(prompt: str, evidence: list,
                         judge=None) -> dict:
    """aiAssert shape: does the interface match `prompt`?

    evidence is caller-supplied observations (strings, never
    screenshots — the caller owns capture gating). With no judge
    callable, the verdict is "no-judge": the assertion is
    recorded, not passed, because no VLM evaluated it. With a
    judge, the judge's (verdict, reason) is recorded verbatim
    and the evidence digest binds the record.
    """
    digest = evidence_digest(prompt, *[str(e) for e in evidence])
    if judge is None:
        return {
            "prompt": prompt,
            "verdict": "no-judge",
            "reason": "No VLM judge is configured; the "
            "assertion is recorded, not passed.",
            "evidence_digest": digest,
            "evidence_count": len(evidence),
        }
    verdict, reason = judge(prompt, evidence)
    return {
        "prompt": prompt,
        "verdict": verdict,
        "reason": reason,
        "evidence_digest": digest,
        "evidence_count": len(evidence),
    }


def wait_for(probe, prompt: str, timeout_s: float = 10.0,
             interval_s: float = 0.5) -> dict:
    """aiWaitFor shape: poll probe() until truthy or timeout.

    probe is caller-supplied (e.g. a settled-screen check). The
    record states waited seconds and whether the condition ever
    held; a timeout is a failed wait, not a pass.
    """
    import time

    deadline = time.monotonic() + timeout_s
    waited = 0.0
    while True:
        try:
            if probe():
                return {"prompt": prompt, "verdict": "met",
                        "waited_s": round(waited, 2)}
        except Exception as exc:  # noqa: BLE001 — probe failure
            return {"prompt": prompt, "verdict": "probe-error",
                    "reason": str(exc), "waited_s": round(waited, 2)}
        if time.monotonic() >= deadline:
            return {"prompt": prompt, "verdict": "timeout",
                    "waited_s": round(waited, 2)}
        time.sleep(min(interval_s, deadline - time.monotonic()))
        waited = time.monotonic() - (deadline - timeout_s)
