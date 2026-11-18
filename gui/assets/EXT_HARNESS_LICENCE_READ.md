# Wave 6 Build 1 — Licence read note (staging)

## What was touched

Nothing third-party. This build is own code, Python standard library
only (`json`, `os`, `re`, `tempfile`, `datetime`, `pathlib`).
No package was downloaded, vendored, copied, or depended on. No
`requirements.txt` change is owed by this build.

## What was NOT cleared

- No extension licence is cleared by this build. The harness
  allow-list (`ALLOWED_LICENCES` in `extension_safety_harness.py`) is a
  policy table, not a licence read: it admits only exact strings
  (MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, PSF-2.0,
  CC0-1.0) and refuses everything else, including every copyleft,
  non-commercial, revenue-cap, and territory-restricted string.
  Putting a string in the table does not prove that string is the
  licence of any real extension — each real extension still needs
  its own licence read (licence file opened, text filed) before it
  ships, per the standing licence-before-building rule.
- MPL-2.0 is refused by this harness even though the app's
  `THIRD_PARTY_LICENSES.md` documents an MPL exception for bundled
  components. That is deliberate and stricter for unreviewed
  third-party extensions; admitting MPL extensions is an
  owner ruling to record, not a default (see INTEGRATION_PLAN.md).
- No font, model, dataset, feed, or service term was read or
  relied on in this build.

## Existing-gate references (read-only, no code copied)

- `core/funnel/tools_gate.py` — confirm-gate / fail-closed / classification
  concepts (read to mirror the posture, not copied verbatim).
- `core/confirm_gate.py` — human-confirmation handler pattern.
- `core/resilience/resilience_gate.py` — approve-first + terminal-revoke
  pattern, atomic-store pattern via `core/playbook_store_p2.py`.
- `core/funnel/tools_registry.py` — declaration/execute shape, for the
  integration plan only.

## Verdict

No licence blocker in this build itself: nothing third-party
ships in it. The blocker that remains is per-extension, by design:
every future extension's licence read is still owed before that
extension ships.
