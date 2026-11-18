# SHERPA_LICENCE_READ — sherpa-onnx code, KWS/TTS only (Sweep Build 5)

## What this read covers, and what it does NOT cover

- COVERED: the sherpa-onnx **code** licence only, for the pinned version below.
  Scope, even when cleared, is **keyword spotting (KWS) and text-to-speech
  (TTS) only** — the scope declared by the code path (`SCOPE = ("kws", "tts")`
  in `core/sherpa_code_path.py`). No funnel gate enforces it today: Phase 4
  deleted `core/funnel/tools_gates.py`, and no `sherpa_kws_tts` gate item
  exists.
- NOT COVERED: any model weights, any model card, any training-data lineage.
  **Every model still needs its own card + licence read before it can ship.**
  Until that read exists, a model is a placeholder card stating
  "model licence read owed, not shipped" — never a loadable model.
- NOT COVERED, NOT CHANGED: wake. Wake ships OFF, unchanged by this build.
  No wake enablement, no capture, no continuous listening is added here.

## Prior-read discrepancy — read this first

No dedicated Sweep Phase 1 sherpa read file was found in the tree before this
build. The wave-33 scout note (`wow_scout_wave33.md` §9 and its methods note)
lists sherpa-onnx as resting on **secondary metadata (PyPI / README / GitHub
API licence fields), explicitly OWED, not a repo LICENSE read**. The parent
summary's "cleared Apache-2.0 metadata" claim is therefore NOT carried here
as a prior read. This file IS the primary read, performed from the actual
wheel LICENSE (below) **before** any code-path module was written, in
licence-before-making order. If the parent disagrees with that basis, this
build reverts cleanly: it adds no runtime dependency and no model.

## Pinned version and primary source

- Package: `sherpa_onnx`, pinned version **2.0.0**.
- Wheel inspected (downloaded for inspection only, NOT shipped, NOT kept in
  the tree): `sherpa_onnx-2.0.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`
  (`pip download sherpa-onnx --no-deps`, 2026-10-26, lab).
- Licence file inside the wheel: `sherpa_onnx-2.0.0.dist-info/licenses/LICENSE`.
- Wheel METADATA: `Name: sherpa_onnx`, `Version: 2.0.0`,
  `License: Apache licensed, as found in the LICENSE file`,
  `License-File: LICENSE`.
- Filed copy: `gui/assets/sherpa/LICENSE.txt` — copied byte-for-byte from the
  wheel LICENSE, not retyped, not quoted from a README.

## Verification of the filed text

- Filed file size: **11,358 bytes**.
- Filed file SHA-256: `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
- Filed text opens: "Apache License / Version 2.0, January 2004 /
  http://www.apache.org/licenses/" and contains the full Apache-2.0 terms
  (Sections 1–9, including the Appendix "Licensed under the Apache License,
  Version 2.0").
- Licence verdict for the CODE: **Apache-2.0, cleared for the code path only**,
  under the scope and model conditions above. This is not a verdict on any
  model, and it is not a claim that KWS/TTS works end-to-end.

## What the code-path module does with this read

`core/sherpa_code_path.py` (stdlib only, no Qt, no network) exposes the interface
the app can call — status, KWS/TTS config templates, model-card placeholders,
and the gate evidence record — without importing `sherpa_onnx`, without a
runtime dependency, and without any model file. `load_model()` fails closed:
a card without a licence read is refused for that reason, and a card with a
licence read is still refused because no model file is present in the tree.
No model was downloaded, bundled, or shipped by this build.

## Kill conditions

- A model licence read that fails, or a model whose licence is copyleft,
  non-commercial, revenue-capped, or territory-restricted, kills that model
  — it is never routed around by this code path.
- If the pinned code licence is ever contradicted by a newer primary read,
  this code path is parked until the read is re-done.
