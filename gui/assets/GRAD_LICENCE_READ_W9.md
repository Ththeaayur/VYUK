# Licence read — Wave 9 Build 10: TENSION graduation 1 + graduation ledger

## Verdict

Own code, standard library only. No licence is engaged by
anything in this build, and no third-party code, package,
model, dataset, font, pack, feed, or service is used,
bundled, vendored, linked, or shipped here.

## What was read

- `core/grad.py`: imports `__future__` annotations,
  stdlib `re`, and the tree's own first-open module
  (`core/firstopen_optin.py`). No third-party import.
- `core/grad_tools.py`: imports stdlib `json` and
  `pathlib`, plus the tree's own `grad` module.
  No third-party import.
- The first-open module reused here was itself own code,
  standard library only (`dataclasses`, `pathlib`),
  verified in its own build; reusing it does not import
  any new licence.

## What is NOT cleared by this note

- No capture, watcher, screen, clipboard, keystroke, or
  sensor library is cleared, because none is used: this
  build captures nothing, and any future capture build
  needs its own read before it uses anything.
- No retention figure is cleared as the owner's ruling:
  the 7-day default is builder-chosen and overrulable by
  the owner at any time, and the module states so.
- No global delete-everywhere rule is cleared or claimed:
  PF-3 is still OPEN. Only the per-feature delete built
  with the opt-in is named, and it is never presented
  as the global rule.
- No conformance, safety, or field-proven claim is made
  by anything here; the graduation ledger records
  evidence plus form plus still-owed, and it is never a
  claim the item is built, shipped, or field-proven.

## Conditions carried forward

- Any future TENSION build that actually captures must
  call the gate built here, honour the recorded
  retention and deletion rule, show the indicator, and
  get its own licence read before it uses any
  third-party capture code.
- The 7-day figure stays overrulable by the owner: if he
  changes it, the prompt text and the default change
  with it, and nothing else in this build claims
  otherwise.
