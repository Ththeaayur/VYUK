"""VYUK skill admission gate (v5.67, Skills edition track, plan SSK4-01/02/03/10/13/15/16).

Threat model (do not re-derive, build on it): 341 of 2,857 registry
skills malicious (ClawHavoc); 84.2% of vulnerabilities live in the
SKILL.md text itself; script-carrying skills show a 2.12x
vulnerability rate; the Zenity/skills.sh campaign published clean
then weaponised via an added file, reaching >1.7M installs.
Incumbents run skill code in-process or as bare shell-outs with
host privileges. VYUK REJECTS that model outright (SK4-04/SK4-17).

What this module is and is not
-------------------------------
This module is a testable library over a skill directory path. It
classifies, scans, grants capabilities, builds a human-readable
skill card, and pins/diffs versions. It is NOT a loader and NOT a
runner:

* The gate provides NO path that runs skill code. There is no
  code-running path for skill scripts anywhere in this module:
  Class 1/2 skills are admitted only as DATA (their steps and
  cards are readable; their code never runs in-process and never
  runs as a bare shell-out). A skill that needs execution without
  a contained runtime is REFUSED with that reason in plain words:
  the contained runtime is the Wasmtime WASM pilot
  (``core/skill_runtime.py``, B8 — declared, VYUK-signed WASM
  only, no host access); the AppContainer broker (SK4-07) is a
  Wave 24 build, NOT built here.
* Classification is by SCANNED CONTENT, never author declaration.
* Class 0 is "default-admissible" -- NEVER "trusted" and NEVER
  taint-exempt. A declarative skill can still carry a malicious
  instruction in its SKILL.md text (that is where 84.2% of
  vulnerabilities live), so Class 0 skills stay inside the normal
  funnel gates (core/funnel/tools_gate.py, core/confirm_gate.py) and are
  never exempt from taint handling. The scan result encodes this
  as ``trusted: False`` and ``taint_exempt: False`` on every
  class, with no way to set either to True through this module.
* Scanners are TRIAGE, not verdicts (SK4-10): every finding
  carries a ``disposition`` field, and "suspicious" means BLOCKED,
  never warned-and-passed. No third-party scanner is bundled here;
  this gate is VYUK's own code. Direct LICENSE reads for the Cisco
  skill-scanner and SkillSpector are STILL-OWED (not this leaf's
  job); neither tool is bundled or vendored by this module.
* Popularity is NEVER a trust input: no function in this module
  accepts a popularity parameter of any kind.

Stdlib only. Product VERSION is untouched (still 5.66.0).
No funnel tool declarations are added by this module.
"""


# INTENTIONAL (skill_gate split B3): the old module's stdlib imports, re-exported so the facade namespace matches the old one.
import hashlib
import io
import json
import re
import tarfile
import unicodedata
import zipfile
from pathlib import Path

# INTENTIONAL (skill_gate split B3): every name the old module defined is re-exported from the impl module that now owns it, so existing module-attribute importers (skills_tool_wiring) work unchanged and with identical objects.
from .skill_gate_helpers import (
    CLASS_DECLARATIVE,
    CLASS_HYBRID_FETCH_EXECUTE,
    CLASS_SCRIPT,
    EXECUTION_REFUSED_REASON,
    MAX_ARCHIVE_DEPTH,
    MAX_ARCHIVE_ENTRIES,
    MAX_TOTAL_BYTES,
    MODULE_VERSION,
    SCOPES,
    SIGNATURE_TIER_SCOPES,
    TYPOSQUAT_MAX_DISTANCE,
    WARNING_SCOPES,
    _ARCHIVE_EXTENSIONS,
    _CODE_EXTENSIONS,
    _CONFUSABLES,
    _CREDENTIAL_ENV,
    _FETCH_EXECUTE_PATTERNS,
    _HTML_COMMENT,
    _INSTRUCTION_VERBS,
    _SCOPE_SENTENCES,
    _TAG_END,
    _TAG_START,
    _TEXT_EXTENSIONS,
    _URL_PATTERN,
    _finding,
    _is_archive_name,
    _is_blocked,
    _is_code_name,
    _looks_like_script,
    _sha256_bytes,
)
from .skill_gate_scan import (
    _check_pyc,
    _read_declared,
    _scan_archive_bytes,
    _scan_text_bytes,
    scan_skill_dir,
)
from .skill_gate_grants import (
    _is_signature_tier,
    _sentence_for,
    build_skill_card,
    evaluate_grants,
    parse_grant,
)
from .skill_gate_admit import (
    _fold_confusables,
    admit_skill,
    check_skill_name,
    check_update,
    diff_pin,
    levenshtein,
    needs_reconsent,
    pin_skill_dir,
)
