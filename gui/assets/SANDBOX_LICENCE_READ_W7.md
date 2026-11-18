# SANDBOX-light completion — licence read (Wave 7 Build 1)

- **Code in this build:** own VYUK code only — `core/sandbox_filesystem.py`
  (filesystem light sandbox), `core/sandbox_tools.py`
  (tool coordinator), `tests/test_wave7_sandbox.py`.
  Stdlib only (`hashlib`, `re`, `pathlib`, `json`).
- **Reused code:** `core/sandbox_light.py` (Wave 4 in-memory
  light sandbox) — own VYUK code, not a third-party
  dependency. This build completes it with a real-filesystem
  form; it does not replace it, and it does not vendor,
  copy, or depend on any third-party sandbox, VM, or
  container code.
- **No third-party dependency introduced, no licence to
  clear in this build.** No package was downloaded, no
  licence text was copied, no model, feed, font, or asset
  was added.
- **Not built, not cleared here:** full-VM universes
  (item 12) stay QUEUED — no VM/hypervisor/guest-image
  code exists in this build, so no VM licence question
  arises. Any future VM form needs its own licence read
  before building, under the standing licence-before-
  making rule.
- **Standing rules this build obeys:** $0 only; copyleft /
  non-commercial / revenue-cap / territory-restricted
  material excluded from the distributed product;
  C-14 approve-first for the consequential tool;
  plan-approval gate intact; no self-deployment, ever.
