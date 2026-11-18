# VYUK v6.3.0 — Release Notes

## What this version is

v6.3.0 is a big release on top of v6.2.0. It is everything built in the
lab since 5 November: the gaps arc finished (GAPS-1 to GAPS-30), the
retention queue finished (RET-1 to RET-5), click-anywhere, social
sending Phase 1, and a full end-of-programme debug pass over the whole
tree. The tool count moves 654 → 755, and the parameter count moves
1,482 → 2,076.

In plain words, what that means for you:

1. **The gaps arc is done.** Thirty rounds of closing the architecture
   holes found in the review: the funnel is one code path now (no
   back doors around it), the audit trail is a single stream, the
   scheduler starts with the app, the voice loop has a failsafe stop,
   and the tool catalogue is one source of truth. Nothing in here
   changes what you see day to day — it is the code underneath
   becoming one thing instead of many.
2. **Retention is built in.** A forgiving daily count (it counts real
   voice work, it never resets to zero on you), named saved routines
   you can re-run with one tap or by voice, a "Today" panel showing
   your day, spend and confirmations, and a template gallery of
   ready-made routines. The rule that came with it: at most five
   daily-job surfaces, and no new deep integrations until you pick
   which ones.
3. **Click anywhere.** You can point VYUK at anything on the screen
   — not just the browser — and it clicks it. That was the bar you
   set: "if it can click anywhere I want it to, I call it a
   success." The final verdict on that is still yours, on your
   machine.
4. **WhatsApp, the careful way.** VYUK prepares the message, checks
   it is the right person, opens WhatsApp's own link to the right
   chat, and you (or Voice Access on your word) tap send. VYUK never
   types the message itself, so there is no automation ban risk on
   your number.
5. **The debug pass.** After all of that building, the whole tree was
   debugged once, end to end. It found three real product bugs and
   fixed them: the bundled icon set never actually rendered (the app
   was silently falling back to drawn shapes), a list-typed
   parameter crashed the wiring registry, and the send-email schema
   was missing a piece. Fourteen test bugs were fixed too. The full
   suite now runs 788 test files, 17,692 tests, with only the 26
   known pre-existing failures left.

## The bar, stated honestly

Code-one is lab-proven: every batch in this release was verified from
the files, the tests, and the logs before it shipped. "Perfect on your
machine" is not yet proven — that is the field check this release
enables. Install it, use it for a real day, and tell me what you see.

## Signing

The install manifest (1,526 files) was regenerated from the current
tree and re-signed with the offline Ed25519 release key, used by path
only. The signature verifies against the public key pinned in the
app's own updater code. The v6.2.0 manifest had gone stale (it listed
1,208 files; the tree has grown by 318 since); this release fixes
that — the manifest now matches the tree exactly.

## Known gaps (carried openly, not hidden)

- The packaging checker reports no gaps. The long-carried `w8_wiring`
  gap (missing from coverage since before v6.0.0) was closed when the
  click-anywhere work added the module to the installer spec.
- A small set of pre-existing test failures is unchanged from the
  debug pass: 26 in total (one PF-18 grants check, 8 wiring-harness
  names, 13 audit-fix names, 2 UI-settings, 1 command-deck guard,
  1 UI check). They are harness and known-issue failures, not new
  product bugs.
- Field proofs are still owed on real Windows: a real day of use,
  a routine saved and re-run by voice and by tap, the Today panel
  on a real day, a template imported, a real WhatsApp message
  through the new flow, and a real click on your screen. The
  second-opinion pair still needs your Groq key before it can run
  on your machine.

## Rollback

v6.2.0 is preserved untouched (SHA256 4cfdced045fdb254377c3bc60efac06
a34ea2f032cd341e3838804959691e377, 21,167,143 bytes). If v6.3.0
misbehaves, reinstall v6.2.0 and tell me.

## Still owed (not in this release)

- Your field check of v6.3.0, then your "one" verdict before Stage 1.
- The Groq key (bring your own) for the second-opinion pair.
- An offline copy of the signing key on a USB drive or offline disk.
