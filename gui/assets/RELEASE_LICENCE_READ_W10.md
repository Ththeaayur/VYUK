# Licence read — Wave 10 Build 1: P23 release spine

## Verdict: own code, no external licence engaged

`core/release_spine.py` and `core/release_spine_tools.py`
are own code, standard library only (`re` in the
module; `json` and `pathlib` in the coordinator).
No third-party code, package, model, dataset, font,
pack, feed, or service is used, vendored, linked, or
shipped by this build.

## What was checked before making

* No signing library is imported or invoked: signing
  is DEFERRED-SPEND, builds stay unsigned with
  SmartScreen warnings disclosed, and no signature
  is verified or claimed anywhere in this build.
* No installer tool is imported or invoked: the
  installer is authored, never compiled, and no
  compiled installer is claimed.
* No network, feed, or store code is imported or
  invoked: the update feed has no host (no_feed
  stands), and no store listing is claimed to exist.
* No payment, tier, trial, refund, or pricing code
  or content is built: ruling 34 is OPEN, and the
  honest legal pack still needs qualified-professional
  confirmation. The batch's own caveat is carried on
  every record: "This is a checklist, not legal or
  tax advice. Confirm licences, privacy law, payments
  and tax with a qualified professional in the markets
  you sell to."
* No licence is cleared by this note for any future
  slice that uses a third-party tool: a future build
  that actually signs, compiles, publishes, feeds,
  or sells needs its own primary read first.

## What this build does not do

It releases nothing, signs nothing, compiles nothing,
publishes nothing, feeds nothing, sells nothing, and
decides nothing. The Wave 10 exit wording is exact:
"shippable to strangers except the transaction
surface, feed hosting, and signing" — never
"commercially complete". Earlier shipped pieces (the
signed manifest plus SHA-256 per ship, the CC-1
licence report, the ship-gate battery, the trust and
doctor modules) are NOT counted as this build's
delivery.
