"""Wave 5 (v5.60) — Senses pilot licence verdicts.

Own-code verdict records for the Wave 5 seats. Nothing here imports
a model, a weight, or a pilot library: a verdict is a record about a
licence read, and the read evidence is cited in each entry. Seats
whose verdict is not CLEAR are hard-blocked for their item, not for
the wave — the wave ships the harness and the cleared seats only.

Read 2026-10-25 (Wave 5 build):
- Distribution primaries (wheel/sdist LICENSE + METADATA) read
  locally: docling 2.142.1, rapidocr-onnxruntime 1.4.4, mss 10.2.0,
  ImageHash 4.3.2, dxcam 0.3.0, windows-capture 2.0.1 (win_amd64).
- Model-card / repo-README primaries read via their published
  texts: Qwen3-VL family, Granite-Docling-258M, GLM-OCR, OvisOCR2,
  PaddleOCR-VL-1.6, LightOnOCR-2-1B, TrOCR family, Midscene
  (repo LICENSE + agent-directed files, O-14).
- B5 card-frontmatter RE-READ 2026-11-06 (the owed L4 re-read,
  now done — see the qwen3-vl and granite-docling-258m entries):
  Qwen3-VL Apache-2.0 (official card opened; Ollama library
  license blob carries the full Apache 2.0 text; Furiosa docs
  "released under the Apache 2.0 License"); Granite-Docling-258M
  Apache-2.0 (onnx-community card frontmatter
  "license: apache-2.0"; IBM announcement "available on Hugging
  Face through a standard Apache 2.0 license").
- TeleOCR: no licence identifier found in any source read — O-15
  stays STILL-OWED; the seat does NOT promote this wave.
- dots.ocr: custom licence + AGPL/NC data components per the plan
  — STUDY only, no embed, no seat.
"""

from __future__ import annotations

VERDICTS = {
    # VR-1 — local vision. No weights are bundled or downloaded by
    # this wave; the facade (vision_models) never resolves a
    # model path. The verdict gates any future weight bundling.
    # B5 (2026-11-06): the owed card-frontmatter re-read is DONE —
    # official Qwen/Qwen3-VL-2B-Instruct card opened; Ollama's
    # library license blob for qwen3-vl carries the full Apache
    # License 2.0 text; Furiosa docs "released under the Apache
    # 2.0 License"; derivative cards "Base Model License: The
    # original Qwen3-VL model is licensed under Apache 2.0 by
    # Alibaba Cloud". Commercial use allowed under Apache 2.0
    # (include the license + copyright notice). Note: Qwen's older
    # research licence with MAU terms applies to OTHER Qwen
    # models, not Qwen3-VL. The backend is wired (B5); weights
    # remain user-supplied, never bundled.
    "qwen3-vl": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "Qwen3-VL family Apache-2.0: derivative card states "
            "'Base Model License: The original Qwen3-VL model is "
            "licensed under Apache 2.0 by Alibaba Cloud'; FiftyOne "
            "integration notes 'Qwen3-VL: Apache 2.0 (see model "
            "card)'. RE-READ 2026-11-06 (B5, L4 closed): official "
            "Qwen/Qwen3-VL-2B-Instruct card opened; Ollama library "
            "license blob for qwen3-vl carries the full Apache "
            "License 2.0 text; Furiosa docs 'released under the "
            "Apache 2.0 License'. Commercial use allowed."
        ),
        "ships": "backend-wired, weights-not-bundled",
    },
    # RD-1 — reading flagship. No weights bundled.
    # B5 (2026-11-06): the owed card-frontmatter re-read is DONE —
    # onnx-community/granite-docling-258M-ONNX card frontmatter
    # "license: apache-2.0" (base_model
    # ibm-granite/granite-docling-258M); IBM announcement
    # "available on Hugging Face through a standard Apache 2.0
    # license"; IBM Granite docs "released under the Apache 2.0
    # license, ensuring it is freely available for both research
    # and commercial use". The backend is wired (B5); weights
    # remain user-supplied, never bundled.
    "granite-docling-258m": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "IBM announcement: 'available on Hugging Face through "
            "a standard Apache 2.0 license'; onnx-community card "
            "frontmatter 'license: apache-2.0'. RE-READ 2026-11-06 "
            "(B5, L4 closed): onnx-community card frontmatter "
            "re-read verbatim; IBM Granite docs 'released under "
            "the Apache 2.0 license, ensuring it is freely "
            "available for both research and commercial use'."
        ),
        "ships": "backend-wired, weights-not-bundled",
    },
    # VR-3 — Docling shell. Code MIT; the shell imports nothing.
    "docling": {
        "licence": "MIT",
        "state": "CLEAR",
        "evidence": (
            "docling 2.142.1 wheel METADATA License-Expression: "
            "MIT (no LICENSE file inside the wheel). Version "
            "2.142.1 satisfies the >=2.132.0 floor "
            "(CVE-2026-105744); the shell refuses older versions."
        ),
        "ships": "shell-only",
    },
    # VR-2 — RapidOCR, English-first. Package licence verified;
    # weights are plan-stated Apache-2.0 and are NOT bundled.
    "rapidocr": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "rapidocr-onnxruntime 1.4.4 wheel METADATA "
            "License-Expression: Apache-2.0 (no LICENSE file "
            "inside the wheel). Weights licence is plan-stated "
            "Apache-2.0, not re-read here; no weights bundled."
        ),
        "ships": "facade-only",
    },
    # RD bake-off seat — closes the plan's minor owed item
    # ("RD-2's repo-code licence is a minor owed item").
    "glm-ocr": {
        "licence": "Apache-2.0 code / MIT weights",
        "state": "CLEAR-SEAT",
        "evidence": (
            "zai-org/GLM-OCR README Licence section: 'The Code "
            "of this repo is under Apache License 2.0. The "
            "GLM-OCR model is released under the MIT License. "
            "The complete OCR pipeline integrates PP-DocLayoutV3 "
            "for document layout analysis, which is licensed "
            "under the Apache License 2.0.' Local-only seat: "
            "any remote endpoint is refused in code."
        ),
        "ships": "seat-only",
    },
    "ovisocr2": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "ATH-MaaS/OvisOCR2 card frontmatter "
            "'license: apache-2.0'."
        ),
        "ships": "seat-only",
    },
    "paddleocr-vl-1.6": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "SGLang PaddleOCR-VL docs: 'released under Apache "
            "2.0'; GGUF card: 'License follows the base model "
            "(Apache-2.0)'."
        ),
        "ships": "seat-only",
    },
    "lightonocr-2-1b": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "lightonai/LightOnOCR-2-1B card Licence section: "
            "'Apache License 2.0'."
        ),
        "ships": "seat-only",
    },
    "trocr": {
        "licence": "Apache-2.0",
        "state": "CLEAR-SEAT",
        "evidence": (
            "Convergent card evidence: Riksarkivet finetune card "
            "'License: apache-2.0' (finetuned from "
            "trocr-base-handwritten); DiffUTE appendix: 'TrOCR — "
            "License: Apache 2.0'. The base microsoft card "
            "frontmatter was not directly read in this pass — "
            "re-read before any weight bundling. Handwriting "
            "only; needs a line detector."
        ),
        "ships": "seat-only",
    },
    # EY-B1/B2 — settled-screen primitive. Own-code hash/diff;
    # the package is NOT imported; its licence is recorded
    # because the primitive implements its semantics.
    "imagehash": {
        "licence": "BSD-2-Clause",
        "state": "CLEAR",
        "evidence": (
            "ImageHash 4.3.2 wheel LICENSE file (2-clause BSD, "
            "Copyright Johannes Buchner). Package not imported; "
            "the primitive is own code."
        ),
        "ships": "own-code",
    },
    # ODiff: semantics implemented in own code; the odiff
    # binary/package is NOT verified, NOT imported, NOT used.
    "odiff": {
        "licence": "UNVERIFIED",
        "state": "NOT-USED",
        "evidence": (
            "No odiff artefact was read or imported. The "
            "diff-percentage primitive in eyes is own "
            "Pillow code implementing the ODiff semantics "
            "(percentage changed), not the odiff tool."
        ),
        "ships": "nothing",
    },
    # PC-S1 / PC-S2 / PC-S3 — capture rungs.
    "dxcam": {
        "licence": "MIT",
        "state": "CLEAR-SEAT",
        "evidence": (
            "dxcam 0.3.0 sdist LICENSE file (MIT, Copyright "
            "2022 Rain) + PKG-INFO License-Expression: MIT."
        ),
        "ships": "seat-only",
    },
    "windows-capture": {
        "licence": "MIT",
        "state": "CLEAR-SEAT",
        "evidence": (
            "windows_capture 2.0.1 win_amd64 wheel LICENCE "
            "file (MIT, Copyright 2023 NiiightmareXD) + "
            "METADATA License-Expression: MIT. Windows-only; "
            "no Linux distribution exists."
        ),
        "ships": "seat-only",
    },
    "python-mss": {
        "licence": "MIT",
        "state": "CLEAR-SEAT",
        "evidence": (
            "mss 10.2.0 wheel LICENSE.txt (MIT, Copyright "
            "Mickael Schoentgen) + METADATA License: MIT."
        ),
        "ships": "fallback-path",
    },
    # EY-C1 — Midscene semantics pilot. O-14 CLOSED by the
    # read below; no Midscene code ships.
    "midscene": {
        "licence": "MIT",
        "state": "CLEAR-PILOT",
        "evidence": (
            "O-14 read 2026-10-25: web-infra-dev/midscene repo "
            "LICENSE (MIT, Copyright Bytedance 2024-present); "
            "agent-directed files AGENTS.md + CLAUDE.md (one "
            "canonical text) read as DATA — ordinary contributor "
            "workflow rules (pnpm only, lint before commit, "
            "Node pins, AI CI action); apps/site/agents.md is a "
            "docs-site index, not automation instructions. No "
            "install-me curl, no key-minting, no exfiltration "
            "instruction found. Pilot implements aiAssert / "
            "aiWaitFor SEMANTICS in own code only."
        ),
        "ships": "semantics-only",
    },
    # Shelves — nothing ships, verdicts recorded so the shelf
    # state is explicit instead of silently absent.
    "teleocr": {
        "licence": "UNNAMED",
        "state": "STILL-OWED-O-15",
        "evidence": (
            "O-15 read 2026-10-25: China Telecom release + "
            "press coverage call TeleOCR 'open-sourced' but "
            "name NO licence identifier for code or weights. "
            "No promotion this wave; licence + Windows/CPU "
            "runtime check remain owed."
        ),
        "ships": "nothing",
    },
    "dots-ocr": {
        "licence": "CUSTOM + AGPL/NC-DATA",
        "state": "STUDY-ONLY",
        "evidence": (
            "Per plan §5.5: custom licence + AGPL/NC data "
            "components — no embed until cleared."
        ),
        "ships": "nothing",
    },
    "mineru": {
        "licence": "STUDY-ONLY",
        "state": "STUDY-ONLY",
        "evidence": (
            "Per plan §5.5: heavy server shape; README "
            "agent-directed text is a §9 trap case; telemetry "
            "must be disabled in any study."
        ),
        "ships": "nothing",
    },
}


def verdict(seat: str) -> dict:
    """The verdict record for a seat, or a blocked record."""
    entry = VERDICTS.get(seat)
    if entry is None:
        return {
            "seat": seat,
            "licence": "UNKNOWN",
            "state": "BLOCKED-UNKNOWN-SEAT",
            "evidence": "No verdict exists for this seat.",
            "ships": "nothing",
        }
    return {"seat": seat, **entry}


def shippable(seat: str) -> bool:
    """True only for seats whose state permits shipped code."""
    return VERDICTS.get(seat, {}).get("state", "").startswith("CLEAR")
