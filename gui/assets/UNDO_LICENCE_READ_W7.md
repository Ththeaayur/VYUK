# UNDO completion — licence read (Wave 7 Build 2)

- **Scope:** `core/undo_completion.py` + `core/undo_tools.py`
  + hooks in `core/sandbox_tools.py`, `core/integration_harness_tools.py`,
  and `gui/doctor_page.py` (card label + restorer registration).
- **Code origin:** own code, written for VYUK, stdlib only
  (`json`, `os`, `tempfile`, `uuid`, `copy`, `datetime`).
  No third-party code, package, model, font, sound, or data
  is introduced, vendored, or depended on by this build.
- **Reuse:** the shared undo store (`core/undo_store.py`),
  the shared gate (`core/resilience/resilience_gate.py`), the
  Perfection 1 producers (`core/undo_producers_p1.py`),
  the Wave 7 sandbox (`core/sandbox_filesystem.py`), and the
  Wave 6 harness (`core/extension_safety_harness.py`) are all
  first-party VYUK code. No licence text is copied here.
- **Verdict:** no licence blocker. Nothing in this build
  needs an allow-list entry, a third-party row, or a
  commercial-use check, because nothing third-party ships
  in it. If a future undo class ever wraps a third-party
  component, that component gets its own licence read
  BEFORE it is wired, per the standing rule.
- **Not cleared by this note:** any per-extension licence
  (still owed under the Wave 6 harness rule — an extension
  naming a licence does not clear it), and any third-party
  code a future undo producer might wrap.
