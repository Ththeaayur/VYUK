# Wave 6 Build 5 — Licence read (staging)

## This build itself: no licence blocker

`mission_control.py` is own code, stdlib only (`re` plus
`__future__` annotations). No third-party code, package, model,
font, dataset, or service was touched, copied, or depended on,
so there is no licence to clear for this build itself, and no
dependency was added to any requirements file.

## What this build does NOT clear

- Per-extension licences remain OWED (Build 1 harness rule):
  a mission that names an extension does not clear that
  extension; the harness audit at Build 1 still applies.
- Per-workflow proof references are caller assertions with a
  checkable reference, not licence verdicts: a brief fact
  carries a proof reference, never a licence claim.
- No GUI asset, font, sound, or image is introduced here, so
  no asset-ledger entry is owed for this build.
- No service terms are invoked: this module calls no service,
  reads no feed, and sends nothing.

## Rule carried forward

If a future mission-control caller adds a dependency, a
template, or an asset, its licence read must be filed BEFORE
that addition is wired — the pattern from Builds 1–4, not a
post-build block.
