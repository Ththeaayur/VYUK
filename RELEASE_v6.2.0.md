# VYUK v6.2.0 — Release Notes

## What this version is

v6.2.0 is a small release on top of v6.1.0. It does two things, both of them
things you asked for:

1. **God's Eye is gone.** The whole world-view feature is removed — the
   page, the live feeds (flights, ships, quakes, satellites, GOES, OpenAQ),
   the wiring, the globe assets. It never worked on your machine, and you
   told me to remove it rather than keep fixing it. Your home orb — the
   blue sphere in the middle of Home — is a different thing and is untouched.
   The tool count does not change: there were no God's Eye tools (the
   registry stays at 654).
2. **The mic asks before it stays silent.** On a fresh install, the first
   time you press Connect, VYUK now asks: "Allow once" (works for this
   session only), "Open Settings" (turn the mic on permanently), or
   "Not now" (asks at most once per session, never nags). Before this, a
   fresh install started with the mic off and nothing ever asked you — that
   is why VYUK could not hear you.

## The mic finding, in plain words

The mic code did not change since v5.53 — I compared it line by line. So
there was no code regression in the mic itself. The deafness was the new
permissions system: on a fresh install the mic starts OFF (fail-closed by
design), and no first-use ask had ever been built. This release builds the
ask. The Doctor's -9996 error is separate and device-side: it is Windows
listing a microphone it then cannot open, and it already failed the same
way on v6.0.0, which had no permissions system at all. If the mic still
does not work after you allow it, that is the device, not the app.

## The bar, stated honestly

Code-one is lab-proven: every batch in this release was verified from the
files, the tests, and the logs before it shipped. "Perfect on your machine"
is not yet proven — that is the field check this release enables. Install
it, press Connect, and tell me what you see.

## Signing

The install manifest (1,208 files) was regenerated from the current tree
and re-signed with the offline Ed25519 release key, used by path only.
The signature verifies against the public key pinned in the app's own
updater code. The v6.1.0 manifest had gone stale after the God's Eye
removal (it still listed the removed globe assets and Python files); this
release fixes that — the manifest now matches the tree exactly.

## Known gaps (carried openly, not hidden)

- The packaging checker still reports one gap: the `w8_wiring` module is
  missing from its coverage. Carried since before v6.0.0.
- A small set of pre-existing test failures (trademark scan, colour
  literals, count pins) is unchanged from v6.1.0.
- One mic test (`test_stalled_stream_reopens`) fails in the lab because a
  fresh lab tree has no permissions store, so the mic reads denied and
  there is no stream for the watchdog to reopen. It needs a test-harness
  fix, not a product fix.
- Field proofs are still owed on real Windows: fresh install → Connect →
  dialog → "Allow once" → mic opens; "Not now" → no nag; Settings flip →
  permanent. The second-opinion pair still needs your Groq key before it
  can run on your machine.

## Rollback

v6.1.0 is preserved untouched (SHA256 f1a30081936fc863f0e3f2c41862707ef78
793d64e99068acb83b657e38a3943, 30,590,927 bytes). If v6.2.0 misbehaves,
reinstall v6.1.0 and tell me.

## Still owed (not in this release)

- Your field check of v6.2.0, then your "one" verdict before Stage 1.
- The Groq key (bring your own) for the second-opinion pair.
- An offline copy of the signing key on a USB drive or offline disk.
