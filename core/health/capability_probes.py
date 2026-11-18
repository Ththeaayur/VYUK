"""Capability probes (v5.54, Wave 0 — wave 11 EY-H1, REPORT-
ONLY leg).

WHY: the Tesseract field failure was a capability that was
provisioned on paper — binary and language data staged — and
dead in fact, while every presence check said fine. File-
present is not working, so each bundled capability is PROBED
for actually working:

  · OCR — the resolved Tesseract binary answers --version,
    lists the staged languages, and OCRs a freshly rendered
    known-text image: English must come back with the probe
    tokens intact (a correctness bar); Hindi — staged beside
    English — must load and run (its traineddata executing is
    the leg; the probe image is Latin, so no text bar is set
    for it).
  · VLM — when the Qwen2.5-VL pair is provisioned, ONE fixed-
    image inference through the product's own describe path
    (core/local_client.describe_frame_vlm) on a synthetic
    image; a returned description is the pass.

REPORT-ONLY: a failed probe is reported (the doctor's line,
the startup senses-log entry) and NOTHING here repairs,
re-downloads or reconfigures anything — the repair leg is
Wave 10's, manifest-verified, Doctor/installer only (the
plan's C-7 split of EY-H1).

Cost honesty: the OCR legs take ~a second; the VLM leg loads
a multi-GB model and is included only when a caller asks for
it (the doctor does; the deferred startup leg does not).
Everything is lazy and guarded — a probe can fail, never
crash a caller.
"""
from __future__ import annotations

import subprocess

PROBE_TEXT = "VYUK PROBE 12345"


def _font(size: int):
    """The bundled Sora face for probe images; PIL's default
    bitmap font when the face can't be loaded.

    B-R1-7: was Inter-SemiBold (the old body face); the Inter files
    were removed with the old stack, so the probe renders in the
    new body face at the same weight. B-R1-7r: was Rajdhani-SemiBold;
    Sora is a variable font and PIL loads its default (400)
    instance — the probe renders in Sora at 400, honestly, never
    a silent swap to another family.
    """
    from PIL import ImageFont

    try:
        from pathlib import Path

        face = (Path(__file__).resolve().parent.parent.parent / "gui"  # Phase 3 H1: module moved one level deeper (core/health/); assets stay at tree root
                / "assets" / "fonts" / "Sora-Variable.ttf")
        return ImageFont.truetype(str(face), size)
    except Exception:  # noqa: BLE001
        try:
            return ImageFont.load_default(size=size)
        except Exception:  # noqa: BLE001
            return ImageFont.load_default()


def _ocr_probe_image_png() -> bytes:
    """A freshly rendered known-text image (white ground, one
    line of large dark text) as PNG bytes."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1400, 360), "white")
    draw = ImageDraw.Draw(img)
    draw.text((60, 120), PROBE_TEXT, fill="black",
              font=_font(96))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vlm_probe_image_jpeg() -> bytes:
    """The fixed VLM probe image: a large red circle on white,
    as JPEG bytes (the describe path takes JPEG data URIs)."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse([170, 90, 470, 390], fill=(220, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _run(args: list[str], timeout: float = 20.0,
         env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_ocr() -> list[dict]:
    """The OCR probe legs. Returns one result dict per leg:
    {"capability", "status": ok|fail|absent, "detail"}."""
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

    results: list[dict] = []
    engine = None
    try:
        engine = local_assets.tesseract_path()
    except Exception:  # noqa: BLE001
        engine = None
    if engine is None:
        return [{"capability": "ocr",
                 "status": "absent",
                 "detail": "no Tesseract engine resolves on this "
                           "machine (bundled or PATH)"}]
    try:
        code, out = _run([str(engine), "--version"])
        version = out.strip().splitlines()[0] if out.strip() else ""
        if code != 0 or "tesseract" not in out.lower():
            return [{"capability": "ocr", "status": "fail",
                     "detail": f"the engine at {engine} did not "
                               f"answer --version (exit {code})"}]
    except Exception as e:  # noqa: BLE001
        return [{"capability": "ocr", "status": "fail",
                 "detail": f"the engine at {engine} could not be "
                           f"run ({type(e).__name__}: {e})"}]
    langs: list[str] = []
    try:
        code, out = _run([str(engine), "--list-langs"])
        if code == 0:
            langs = [ln.strip() for ln in out.splitlines()[1:]
                     if ln.strip() and not ln.startswith("List")]
    except Exception:  # noqa: BLE001
        langs = []
    # Functional leg via the PRODUCT's own OCR plumbing
    # (pytesseract + local_assets' config/tessdata resolution).
    try:
        import io

        import pytesseract
        from PIL import Image

        local_assets.configure_pytesseract(pytesseract)
        image = Image.open(io.BytesIO(_ocr_probe_image_png()))
        config, lang, _note = local_assets.ocr_tesseract_config(
            "eng")
        text = pytesseract.image_to_string(
            image, lang=lang, config=config)
        squashed = " ".join(str(text).upper().split())
        if "VYUK" in squashed and "12345" in squashed:
            results.append({
                "capability": "ocr",
                "status": "ok",
                "detail": f"{version}; languages staged: "
                          f"{', '.join(langs) or 'unknown'}; the "
                          "known-text image OCR'd back correctly "
                          "(eng)"})
        else:
            results.append({
                "capability": "ocr",
                "status": "fail",
                "detail": f"{version}; the known-text probe "
                          "image did not OCR back with its "
                          "tokens intact (eng) — the engine "
                          "runs but does not read"})
    except Exception as e:  # noqa: BLE001
        results.append({
            "capability": "ocr",
            "status": "fail",
            "detail": f"the functional OCR leg could not run "
                      f"({type(e).__name__}: {e})"})
    # Hindi leg: the staged hin.traineddata must LOAD AND RUN.
    if "hin" in langs:
        try:
            import io

            import pytesseract
            from PIL import Image

            local_assets.configure_pytesseract(pytesseract)
            image = Image.open(io.BytesIO(_ocr_probe_image_png()))
            config, lang, _note = \
                local_assets.ocr_tesseract_config("hin")
            pytesseract.image_to_string(image, lang=lang,
                                        config=config)
            results.append({
                "capability": "ocr (hin data)",
                "status": "ok",
                "detail": "hin.traineddata loads and the "
                          "engine runs with it"})
        except Exception as e:  # noqa: BLE001
            results.append({
                "capability": "ocr (hin data)",
                "status": "fail",
                "detail": f"hin.traineddata is staged but the "
                          f"engine cannot run with it "
                          f"({type(e).__name__}: {e})"})
    return results


def probe_vlm() -> dict:
    """One fixed-image inference through the product's own
    local describe path — the heaviest probe (it loads the
    model); callers opt in."""
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

    try:
        ready = local_assets.vlm_ready()
    except Exception:  # noqa: BLE001
        ready = False
    if not ready:
        return {"capability": "vlm", "status": "absent",
                "detail": "the local vision model pair is not "
                          "provisioned on this machine"}
    try:
        from ..voice import local_client  # Phase 3 V1b: moved to core/voice/

        reply = local_client.describe_frame_vlm(
            _vlm_probe_image_jpeg(),
            "What colour is the large circle in this image? "
            "Answer with just the colour.")
    except Exception as e:  # noqa: BLE001
        return {"capability": "vlm", "status": "fail",
                "detail": f"the inference leg raised "
                          f"({type(e).__name__}: {e})"}
    if reply:
        return {"capability": "vlm", "status": "ok",
                "detail": f"fixed-image inference answered: "
                          f"{reply[:80]!r}"}
    return {"capability": "vlm", "status": "fail",
            "detail": "the model pair is provisioned but the "
                      "fixed-image inference returned no "
                      "description"}


def probe_vosk() -> dict:
    """The wake-word spotter's two legs (v5.54.1 — Wave 0
    probed Tesseract + VLM only, while the startup log kept
    reporting "Vosk spotter unavailable" with no probe to
    name which half was missing). Leg 1: the vosk package
    imports. Leg 2: the wake model folder is provisioned and
    plausible through the product's own resolution
    (local_assets.wake_model_ready — bundled or downloaded).
    The model is NOT loaded here: presence, not inference, is
    the leg the task names, and it keeps the probe cheap.
    Report-only like the rest — a failed leg is named, never
    repaired from here."""
    try:
        import vosk  # noqa: F401 — importability IS the leg
    except Exception:
        return {"capability": "vosk", "status": "absent",
                "detail": "the vosk package is not installed — "
                          "the wake-word spotter cannot run"}
    try:
        from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

        ready = local_assets.wake_model_ready()
        path = local_assets.wake_model_path()
    except Exception as e:  # noqa: BLE001
        return {"capability": "vosk", "status": "fail",
                "detail": f"the wake-model resolution could not "
                          f"run ({type(e).__name__}: {e})"}
    if not ready:
        return {"capability": "vosk", "status": "absent",
                "detail": f"vosk is installed but the wake model "
                          f"is not provisioned at {path}"}
    return {"capability": "vosk", "status": "ok",
            "detail": f"vosk imports and the wake model folder "
                      f"at {path} is provisioned"}


def run_probes(include_vlm: bool = False) -> list[dict]:
    """All probe legs. include_vlm defaults OFF — the VLM leg
    loads a multi-GB model; the doctor opts in explicitly.
    The Vosk leg is always included: it is an import attempt
    plus path checks, never a model load."""
    results: list[dict] = []
    try:
        results.extend(probe_ocr())
    except Exception as e:  # noqa: BLE001 — a probe never
        results.append({"capability": "ocr", "status": "fail",
                        "detail": f"probe crashed "
                                  f"({type(e).__name__}: {e})"})
    try:
        results.append(probe_vosk())
    except Exception as e:  # noqa: BLE001 — a probe never
        results.append({"capability": "vosk", "status": "fail",
                        "detail": f"probe crashed "
                                  f"({type(e).__name__}: {e})"})
    if include_vlm:
        try:
            results.append(probe_vlm())
        except Exception as e:  # noqa: BLE001
            results.append({"capability": "vlm", "status": "fail",
                            "detail": f"probe crashed "
                                      f"({type(e).__name__}: {e})"})
    return results


def summary_line(results: list[dict]) -> str:
    """One compact line for logs: 'OCR ok · ocr (hin data) ok ·
    VLM not provisioned'."""
    words = {"ok": "ok", "fail": "FAILED", "absent": "absent"}
    labels = {"ocr": "OCR", "vlm": "VLM", "vosk": "Vosk"}
    parts = []
    for r in results:
        label = labels.get(r["capability"], r["capability"])
        state = words.get(r["status"], r["status"])
        if r["status"] == "absent" and r["capability"] == "vlm":
            state = "not provisioned"
        parts.append(f"{label} {state}")
    return " · ".join(parts)
