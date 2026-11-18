# Wave 7 Build 4 — Self-improvement proposals: licence read

- Scope: `core/improvement_proposals.py` + `core/proposal_tools.py`
  + `tests/test_wave7_proposals.py`. Own code, stdlib only
  (`hashlib`, `json`, `re`, `tempfile`, `pathlib`).
- No third-party code, model, asset, or dependency is introduced
  by this build, so there is no licence to clear and no licence
  blocker in this build itself.
- Nothing here clears a licence for any future proposal's
  content: a proposal that would introduce third-party code
  still needs its own licence read before it is ever applied
  by a separate human action. This module never applies a
  proposal, so it cannot smuggle one in.
- M4 itself is not built here (pattern reference only, Track M
  PROPOSED, awaiting his green); no M4 licence question arises.
