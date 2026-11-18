# VYUK v6.1.0 — Release Record

**Version:** 6.1.0
**Date:** 2026-11-05
**Kind:** refactor release (the Stage 0 / code-one release), signed, with rollback.
**Previous version:** 6.0.0 (shipped 2026-11-01).

## What this release contains

Everything built and verified in the lab since v6.0.0, in plain words:

- **The refactor itself (Phases 0–6 + the skills split):** the code reorganised
  into small files by domain, the GUI split, the tools registry split into
  mixins — the same app, put together as one instead of scattered parts.
- **PF-18 permissions:** six permissions (microphone, camera, screen, files,
  browser, home devices) with a choke point that refuses before anything runs,
  a Settings page to flip them, and folder allowlists. Microphone and camera
  fail closed — off or broken means denied, never silently allowed.
- **Control-existence verifier:** before the app clicks or acts on something
  on screen, it re-checks the spot is still really there; if the check fails
  after you approved, the step is refused.
- **Second opinion:** on a risky step (a destructive word together with a
  tool that changes something), two different brains — Groq in the cloud and
  the on-device model — are asked the same question from the step label only.
  If they disagree, or one is unavailable, the step pauses and shows both
  answers; a human chooses, nothing is auto-picked.
- **Companion sessions:** the holder that starts those two brains alongside
  the main brain, without touching it, its fallback, or its standby. The
  local brain is asked through its own resident model first, falling back
  to Ollama only if that fails.
- **Wallpaper apply, state mirrors (display-only), live staleness checks,**
  and the fail-open audit with the A1 taint-consult fix.
- **Manifest regenerated and re-signed** (see below) — the old manifest
  listed 1,054 files and was missing 260 after the renames and deletions;
  this one lists the actual 1,600 files in this tree.

## The bar, stated honestly

Code-one is **lab-proven**: every batch since v6.0.0 was verified
independently from disk and logs, the lab queue is closed, and every part
is either wired or parked with a named reason and a path.

"Perfect" on the owner's machine is **NOT yet proven**. That is the field
check this release enables: install it, connect Live, run the Doctor,
use the mic, and confirm it behaves as one. The owner's hold stands —
ship, then field check, then his "one" verdict, then Stage 1.

## Signing

- Manifest `install_manifest.json` regenerated from this tree with the
  tree's own `core/health/integrity.py` builder (1,600 files) and signed
  with the offline Ed25519 release key.
- Manifest SHA-256: `b52f26b3a78b0b4005902b59432f73f521c1deef5c14bf049cce05980877e8f2`
  (regenerated a second time after the `test_gateB` fixture shift below —
  the shift changed one `.py` file's hash, so the manifest was rebuilt
  and re-signed rather than left stale)
- The signature verifies against the public key pinned in the app
  (`84f32c05e8ab1fa68edbf1312dcbe0ff29ecea9ee7226f133203a38c9f497191`).
- The release tooling (`packaging/gen_install_manifest.py`) was repaired
  in this release: it still pointed at `core/integrity.py`, which moved to
  `core/health/integrity.py` in Phase 3 — the old path silently fell back
  to a local walk that misses the globe assets. The repair points it at
  the moved module and the fallback walk now covers globe assets too.
- The plugin manifest (`plugins/plugins_hashes.json`) was unchanged and
  its existing signature still verifies against the same pinned key, so
  it was not re-signed.

## Known gaps carried into this release

- **Packaging gap (known, not hidden):** `voice_assistant.core.w8_wiring`
  was never built. The packaging checker still fails on exactly that one
  missing module. It is the only known packaging gap.
- **bz48/bz3 suites, documented deletion:** the test suites for
  `bz48_consent` and `bz3_workplace` are gone because the modules they
  tested are gone. Both were PARK modules — parked, dead business code —
  deleted in the maintainability refactor, Phase 1 step 7b (2026-11-02),
  along with 25 other PARK modules. They had no successor, and the
  deletion was verified at the time (the only live reference anywhere was
  one import in a test, handled then). Their absence is a documented
  deletion in this release, not a missing test.
- **Second opinion needs a Groq key:** the Groq leg of the second-opinion
  pair needs the owner's own Groq key. Until it is added, the pair does
  not exist and the check stays off — fail-closed, never faked.
- **Field proof owed:** see "The bar" above. Nothing in this release has
  been proven on real Windows yet.

## Rollback

The v6.0.0 bytes are preserved in the release backup
(`backup_release_v610_pre/` in the Code Lab workspace), both canonical
ZIPs and the tree:

- v6.0.0 canonical ZIP SHA-256:
  `418d86b1f4fc58a25bc5fcd348dffdfe2ff55f8b19cb0491ab6a6fa9b29b5550`
  (29,564,113 bytes, both homes)
- v6.0.0 manifest SHA-256:
  `7e770d58dd0742ae0fae67f3db981b12f4ccb05ab08384e64ddc3ff82d7a8c5b`
- v6.0.0 manifest signature SHA-256:
  `1d954441f77c7f5feea25494d5e912856caa24b77ed6731c266c9104b86bff4e`

Rollback is a copy-back: restore the v6.0.0 ZIP bytes to both canonical
homes and the v6.0.0 tree from the backup. No re-signing is needed for a
rollback — the v6.0.0 manifest and signature are preserved as they were.

## Still owed by the owner

- **Offline copy of the signing key** on a USB drive or offline storage.
  The key lives at `~/workspace/your_files/nova-update-signing-key.PRIVATE.pem`
  (mode 600). This item is still open.
- **The field check** on his machine (see "The bar" above).
- **The Groq key**, if he wants the second opinion switched on.
