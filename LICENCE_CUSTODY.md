# Licence key custody (Wave 10, v5.65 — MN-1.3 mechanism only)

**NO sale happens in this wave.** This document, and the code it
describes (`core/licence.py`), are the licence mechanism only:
offline Ed25519 licence files and their verification. There is no
store, no payment rails, no checkout, and no selling code anywhere in
this wave. Selling — if it ever happens — is a separate, later
decision with its own review, and nothing here pre-commits to it.

## The one rule that matters

The app ships **ONLY the public key**. The private key is never in
the tree, never in the repo, never in a shipped ZIP, never in a test
fixture, and never in a backup of the tree. Anyone who can read the
shipped code can read the public key — that is fine, because a public
key cannot forge a licence. Anyone who holds the private key can
issue licences for every install — so it is guarded accordingly.

## How the release key pair is generated

1. On an **offline machine** (no network, never the build server),
   generate a fresh Ed25519 key pair. Example with OpenSSL:
   `openssl genpkey -algorithm ed25519 -out vyuk-release-private.pem`
   then extract the raw 32-byte public key and base64-encode it.
   (Any trustworthy Ed25519 tooling works; what matters is the
   offline machine, not the tool.)
2. Record the public key's fingerprint (SHA-256 of the 32 raw bytes)
   in a durable, non-repo place. The fingerprint is the check that
   the key baked into a build is the key that was generated — compare
   fingerprints, never eyeball base64.
3. Store the private key **offline**: encrypted at rest, on media
   that is not connected to any networked machine, with access
   limited to the release owner. A passphrase-protected copy and one
   sealed backup copy, in different physical locations, is the
   minimum. The private key is never emailed, never pasted into chat,
   never committed, and never uploaded to any service.

## How the public key gets into the build

1. The packaging coordinator takes the release public key (32 raw
   bytes, base64) from the offline store — never from a chat message
   or an unverified file — and replaces the placeholder constant
   `TEST_PUBLIC_KEY_B64` in `core/licence.py`.
2. The shipped constant in development is an **obviously-placeholder
   TEST key** (32 zero bytes). It verifies nothing: under it, every
   signature check fails closed. A release build that still carries
   the placeholder accepts no licences at all — loud, safe, and
   caught by the pre-release check below.
3. Pre-release check (mandatory, every build): verify that the baked
   key's fingerprint matches the recorded release fingerprint, and
   verify that a licence signed with the release private key passes
   `check_licence` while a tampered copy fails. If either check
   fails, the build does not ship.
4. The public key is not a secret and needs no rotation on its own:
   it changes only when the private key changes (see below).

## Lost-private-key recovery

Losing the private key does **not** lock out existing holders
forever, but it must be handled in order — the order is the whole
procedure:

1. **Generate a new key pair** on the offline machine, exactly as
   above, and record its new fingerprint. The old public key is now
   retired: licences signed under the old key will not verify under
   the new key, and that is by design (a lost key must be assumed
   compromised, not kept alive).
2. **Reissue every outstanding licence** under the new key, with the
   same holder, edition, and expiry as the original. Perpetual
   licences are reissued as perpetual — a key rotation never converts
   a perpetual licence into a time-limited one.
3. **Deliver reissued licences BEFORE the new key ships.** The new
   public key must not reach users until their replacement licence
   files have. Holders install the new licence file, then the update
   carrying the new key.
4. **Grace handling during rollout:** time-limited licences whose
   holder has not yet installed the reissued file keep working under
   the OLD key until the rollout completes; the new key is enforced
   only in a build released after the reissue window. If a holder
   misses the window, their old licence reports `invalid` under the
   new key (signature mismatch, plainly reported — never a silent
   lockout with no message), and they are reissued on request from
   proof of the original purchase/holder record. The 14–30-day
   offline grace window in `licence.py` covers *expiry*, not
   key rotation — do not confuse the two, and do not stretch grace
   to paper over a missed rotation.
5. **After rotation**, destroy or seal the old private key's copies
   per the same offline rules; record the rotation (date, old
   fingerprint, new fingerprint) alongside the fingerprint record.

## What this document does NOT authorise

* No sale, no pricing, no store, no payment provider, no licence
  server, no online activation. The verifier is fully offline.
* No private key in the tree, in tests, in docs, or in any backup
  that leaves the offline store.
* No "temporary" release build carrying the placeholder key, and no
  build carrying a test-vector key (published test private keys are
  public knowledge and forge licences trivially).

## Checklist for the packaging coordinator

- [ ] Release public key fingerprint recorded, offline.
- [ ] `TEST_PUBLIC_KEY_B64` replaced; fingerprint of the baked key
      matches the recorded fingerprint.
- [ ] A release-signed licence verifies; a tampered copy fails.
- [ ] Private key confirmed absent from the tree, the repo, and the
      shipped ZIP (search the build for the private key file name
      and for PEM blocks before shipping).
