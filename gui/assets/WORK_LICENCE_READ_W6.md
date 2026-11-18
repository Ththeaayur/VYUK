# WORK licence read — Wave 6 Build 3 (STAGING ONLY)

## What was read

Nothing third-party. `workflow_learning.py` is own code, stdlib
only (`hashlib`, `re`). It imports nothing from the tree, so it
cannot pull in a licensed dependency by accident, and it ships
no model, no dataset, no asset, and no copied text.

## Verdict

**No licence blocker in this build itself.** No dependency was
added, no licence text is owed for this module, and no
`requirements.txt` change belongs to it.

## What is still owed — not cleared by this note

- **Per-job provenance is the caller's burden, not this module's
  verdict.** A `ProvenJob` is only as trustworthy as the caller
  that asserts `field_proven=True` with a `proof_reference`.
  This module checks the shape of that claim (completed binder
  + evidence + steps + reference); it does not verify that the
  referenced field run happened. That verification is the Wave 6
  exit criterion (his verdict on his machine), and it stays owed
  until then.
- **Per-extension licences** remain owed under the EXT-HARNESS
  allow-list; nothing in this build clears any extension.
- **Any future dependency** (a store, a scheduler, a model)
  needs its own licence read BEFORE wiring, per the standing
  licence-before-making rule. None is proposed here.

## Boundaries this note does not waive

$0 only. Commercial law only. No copyleft / non-commercial /
revenue-cap / territory-restricted material is admitted by this
build, because no third-party material is admitted at all.
