"""Wave 6 (v5.61) — pilot / seat licence verdicts (verdict records).

Same shape as core/pilot_licences_sound_translation.py and
core/desktop/browser_pilot_licences.py: these records name the
licence of material that is NOT shipped as a dependency — pilot
seats, facades, and patterns. Nothing in this module is imported
by shipped code paths as a dependency, and no verdict here
authorises a shipped use. Primary-source reads below were done
2026-10-25 by downloading the wheel (pip download --no-deps)
and reading its METADATA + LICENSE files locally — never
metadata snippets alone, never aggregator claims.

VERIFIED THIS WAVE (wheel primaries):
- gliner 0.2.29: Apache-2.0 (METADATA License-Expression +
  LICENSE file). Code licence only; GLiNER2.5 WEIGHTS licence
  is plan-carried (Apache-2.0 per primary card) and must be
  re-read before ANY weight bundling (weights absent here).
- fsrs 6.3.2: MIT (LICENSE file, Copyright Open Spaced
  Repetition 2022). NOTE: the PyPI distribution is named
  `fsrs`, not `py-fsrs` (no `py-fsrs` distribution exists).
  Pilot seat only — the live scheduler is own code
  (core/retention.py); fsrs is NOT imported.
- ebisu 2.2.0: Unlicense per wheel METADATA (no LICENSE file
  in the wheel, no licence text in its sources). METADATA-only
  evidence: pilot seat only, NOT imported, no shipped use
  depends on it.
- genanki 0.13.1: MIT (LICENSE.txt, Copyright Kerrick Staley
  2017). Pilot seat only — flashcard output is own-code
  shaped in this wave; genanki is NOT imported.
- sqlite-vec 0.1.9: MIT + Apache-2.0 per wheel METADATA (no
  LICENSE file in the wheel). Pilot seat only — the extension
  is probed, never required; FTS5 carries search alone.
- model2vec 0.10.0: MIT per wheel METADATA (no LICENSE file).
  Pilot seat only (always-on tier facade, model absent).
- cognee 1.6.5: Apache-2.0 (METADATA License-Expression +
  LICENSE file). Floor ≥1.6.0 MET (CVE-2026-105141 fix).
  Contest seat only (PM-U1) — NOT installed, NOT imported.
- mem0ai 2.2.1: Apache-2.0 (METADATA License-Expression +
  LICENSE file). Contest seat only (PM-U2) — NOT installed,
  NOT imported. Its README's agent-key instruction
  (`mem0 init --agent`) is treated as DATA and a scorecard
  demerit (§9 trap register): nothing was minted, nothing
  will be.
- paper-qa 2026.8.12: Apache-2.0 (LICENSE file). Licence
  clear; O-16 (local-only S2-free PROOF) stays OWED until the
  auditor runs where PaperQA2 is installed. NOT installed.
- chromadb 1.5.9: Apache-2.0 (LICENSE file). Conditional seat
  only (PM-U3 — only if the winner's store proves inadequate).

CARRIED, NOT RE-READ THIS WAVE (marked carried; no shipped
use depends on a fresh read):
- kotaemon: plan-carried Apache-2.0 (coordinator-verified).
  No PyPI distribution named `kotaemon` was found from this
  environment, so no fresh wheel read was possible; KW-1 ships
  as an own-code citation pattern (no kotaemon import), so no
  shipped use depends on the re-read.
- E5-small / Model2Vec / Qwen3-Embedding-0.6B / BGE-M3 weights:
  NOT downloaded, NOT bundled. Bundling is BLOCKED until
  each card licence is re-read (Wave 5 Qwen3-VL/TrOCR rule).
- OpenAlex: service terms, not a code licence — keyed since
  Feb 2026 (metered ~$1/day free tier with key); the client
  enforces key + caller-set daily cap (RS19-B2 patch).
- Crossref metadata: CC0-class (plan-carried). arXiv API:
  metadata/digest VERIFIED (plan-carried); PDF store/serve
  EXCLUDED (copyright). Zotero: arm's-length only (read-only
  BibTeX parse; no API, no write-back).

EXCLUDED / REJECTED / STILL-OWED (never built, never imported):
- Semantic Scholar: REJECTED as a bundled/redistribution
  dependency (non-commercial); link-out posture only.
- Jina: REJECTED (CC-BY-NC).
- A5 self-hosted Firecrawl option: EXCLUDED (AGPL).
- FreshRSS-as-bundled-code: EXCLUDED (AGPL); Miniflux
  pattern only.
- Graphiti engine (PM-U5): WATCH — its posthog core
  dependency gates any pilot (O-13). Only the bi-temporal
  SCHEMA PATTERN is used (own fields, no engine).
- ArchiveBox capture door (KN-3.3): per-session opt-in only;
  its default public-Wayback submission is inverted before
  any use (S-6 family).
- TeleOCR (RD-12): O-15 STILL-OWED — no licence identifier
  in any source read, so it does NOT promote.
- dots.ocr: STUDY (custom licence + AGPL/NC data).
- PaperQA2 local-only proof: O-16 STILL-OWED (auditor
  shipped; proof run owed where PaperQA2 is installed).
- Cognee bundled extractor: demo-grade per the plan — the
  KN-2.7 bake-off result decides whether it is ever used.
"""

VERDICTS = {
    "gliner": ("Apache-2.0", "wheel 0.2.29, METADATA + LICENSE",
               "pilot seat; weights re-read owed before bundling"),
    "fsrs": ("MIT", "wheel 6.3.2, LICENSE file (PyPI name: fsrs)",
             "pilot seat; live scheduler is own code"),
    "ebisu": ("Unlicense", "wheel 2.2.0 METADATA only",
              "pilot seat; METADATA-only evidence, not imported"),
    "genanki": ("MIT", "wheel 0.13.1, LICENSE.txt",
                "pilot seat; not imported"),
    "sqlite-vec": ("MIT + Apache-2.0",
                   "wheel 0.1.9 METADATA only",
                   "pilot seat; probed, never required"),
    "model2vec": ("MIT", "wheel 0.10.0 METADATA only",
                  "pilot seat; facade, model absent"),
    "cognee": ("Apache-2.0",
               "wheel 1.6.5, METADATA + LICENSE; floor >=1.6.0 met",
               "contest seat PM-U1; not installed"),
    "mem0ai": ("Apache-2.0", "wheel 2.2.1, METADATA + LICENSE",
               "contest seat PM-U2; agent-key instruction = data"),
    "paper-qa": ("Apache-2.0", "wheel 2026.8.12, LICENSE file",
                 "O-16 proof owed; not installed"),
    "chromadb": ("Apache-2.0", "wheel 1.5.9, LICENSE file",
                 "conditional seat PM-U3 only"),
    "kotaemon": ("Apache-2.0 (carried)",
                 "plan-carried; no PyPI distribution found here",
                 "own-code pattern only; no import"),
    "semantic-scholar": ("REJECTED", "non-commercial",
                         "link-out posture only"),
    "jina": ("REJECTED", "CC-BY-NC", "never built"),
    "firecrawl-self-hosted": ("EXCLUDED", "AGPL",
                              "A5 option excluded"),
    "freshrss-bundled": ("EXCLUDED", "AGPL",
                         "Miniflux pattern only"),
    "graphiti": ("WATCH", "posthog core dependency (O-13)",
                 "schema pattern only, no engine"),
    "teleocr": ("STILL-OWED", "O-15, licence unnamed",
                "does not promote"),
    "dots-ocr": ("STUDY", "custom licence + AGPL/NC data",
                 "no embed until cleared"),
    "paperqa-proof": ("STILL-OWED", "O-16",
                      "auditor shipped; proof run owed"),
}


def verdict(name):
    """A verdict record, or an explicit unknown — never a
    guessed licence."""
    entry = VERDICTS.get(name)
    if entry is None:
        return {"name": name, "licence": "UNKNOWN",
                "evidence": "no verdict recorded",
                "status": "not cleared"}
    licence, evidence, status = entry
    return {"name": name, "licence": licence, "evidence": evidence,
            "status": status}
