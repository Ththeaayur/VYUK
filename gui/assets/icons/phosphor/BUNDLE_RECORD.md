# Phosphor Duotone icon bundle — landed 2026-11-09 (B-R1-6)

Replaces the Lucide bundle (landed 2026-11-02, removed by this
batch): Phosphor Duotone is now the ONE primary icon system.
The Lucide bundle is gone — keeping it alongside Phosphor
would be two full systems, which `gui/icon_manifest.py`
`validate_manifest` itself rejects. The inline templates in
`gui/icons.py` stay as the honest fallback for names the
bundle does not cover.

## Source (pinned, reproducible)

- Upstream: `phosphor-icons/core` repo, commit
  `2b75f3ad12b420c9504ef05df8d2564a28f8500e` (HEAD, 2026-01-06).
  Every SVG below was fetched from
  `raw.githubusercontent.com/phosphor-icons/core/<commit>/assets/duotone/<stem>-duotone.svg`
  and verified: HTTP 200, parses as XML, `viewBox="0 0 256 256"`,
  root `fill="currentColor"`, exactly 2 paths, opacities
  `['0.2', '1']` — a genuine duotone (secondary tone at 20%,
  primary at full). No tag pins this commit (the repo's tags
  do not cover it); the commit pin above is what makes this
  bundle reproducible, not a version number, so no version
  claim is made here.
- Licence: MIT — full text in `LICENSE` in this directory
  (fetched from the same commit: "MIT License, Copyright
  (c) 2023 Phosphor Icons"). MIT is permissive and
  commercially shippable.

## Bundled (35, semantic names — the names callers ask for)

The manifest key is the semantic name the product uses
(`mic`, `search`, `send`, ...); the file is the exact
Phosphor stem. Every mapping below is the SAME glyph under
a different stem name — verified by reading the SVG and by
rendering it at 16 px — never a lookalike substitution
(which the resolver forbids):

| semantic name | Phosphor stem | why it is the same glyph | SHA-256 |
| --- | --- | --- | --- |
| bell | bell | bell | 77f99c9aa7747cc92eae7c225d96e894c40275559e0f40d7ddb7513653a87c19 |
| check | check | check mark | 581385c1c40e6e618fe35f6ce2c39e81e0aab36a807ec64437424a5f56c8f240 |
| clock | clock | clock face + hands | 2f54d3367b48e1c7579b3fb33963240ee4060bd55ddd4e3c39cd59f840499539 |
| database | database | cylinder database | 2543df52f9d509307cd7d08d8bf2ba9033aa23207791c32e2526841ef869ea1c |
| globe | globe | globe with meridians | b9fd5ea9c984f786b47ac59f3aa913e3748d40f45f8eb212874718adc38e02b4 |
| grid | grid-four | 2x2 grid of rounded rects | a35e22f5e32f89df8451defb60aa0abd4490089db4780195a2c9a4a887febbec |
| mic | microphone | microphone | c1bd548c24ecfaaa07054cefd93885537c518234691f9614e144e52aa19eedce |
| minus | minus | horizontal bar | e375c2cdd56a4e216d229023d46a4681b126e3c6112fd49f3ed8d49ca5b2fc2b |
| monitor | monitor | monitor + stand | 93d85c4c8cc8014c2a83ff4c0ac176166c99f12b5948d22807aacfd3925c6088 |
| play | play | play triangle | 8485ad1efd20878370885ccf7b825aae21a3b2fede35734484475672ac707c48 |
| pulse | pulse | pulse/heartbeat line | fac71608af59b30ae8bfa560978ef9a7e149c9db72bf00cbd2cab741e535f919 |
| search | magnifying-glass | magnifier | 7a840ea2341e75cf1d9187e07223be4501d6c6078a064a4097be07c1b3a52fb6 |
| send | paper-plane-tilt | paper plane | a8eb912cee783545493f39946640bf1d93768051addfbab56b040d552e519c52 |
| sliders | sliders-horizontal | horizontal sliders + knobs | 76260b62780cba20278d8c45eb9107801090414b0fbb8a6b67c97860664d7237 |
| spark | sparkle | single four-point sparkle (the Assistant mark; NOT Lucide's "sparkles" cluster) | c4740b4365131487e4b49b54fc554a6cbdde28aaad74c5a8a8be2195b5fb4c19 |
| square | square | rounded square | 5241a61c6fc988763f87a84de7e91d9cfe29aa6f1f74fe71121c22f7ce96f199 |
| volume | speaker-high | speaker + waves | df9b42e5af71a3f87a7588397ba9ad73f70133cbc1352e3f916ac8c2f380207b |
| volume-off | speaker-x | speaker + X | cf2ed2d1af4f54a9aeb6b66a9b7c10d60bdcfb96c34d00c08b92433dc89fd3d3 |
| x | x | X cross | 2092e345cb389e4b3c9795d5aa467b7da4e230dd257ba40845a262c80d51f1ab |

Total bundle weight: 9,647 B of SVGs (+ 1,071 B LICENSE,
+ manifest + this record). 0 new pip dependency.

## Phase 1 additions (2026-11-24 — UI Phase 1 rebuild)

Same source, same pinned commit
`2b75f3ad12b420c9504ef05df8d2564a28f8500e`, same fetch +
verify (HTTP 200, parses as XML, `viewBox="0 0 256 256"`,
root `fill="currentColor"`, exactly 2 paths, opacities
`['0.2', '1']` — the second path carries no opacity
attribute, i.e. full opacity, exactly as the original 19).
Same MIT licence, already cleared — no new licence check
needed. 12 entries, manifest 19 → 31:

| semantic name | Phosphor stem | used for | SHA-256 |
| --- | --- | --- | --- |
| envelope | envelope | Home capability tile — Email | 8ae92e57426296fbd6a103ae896fd0ebcefc1c79bb31ff871eb88ef1e0fad308 |
| calendar | calendar | Home capability tile — Calendar | e01b6303beb8d1d8a77b36087ab663bb3f6591fbc7256fda281406ed3bc9b5e1 |
| note | note | Home capability tile — Notes | e95bd4c4c2144398c68cc9a60c30895e25411e741096925edec402b31221ef83 |
| house | house | Home capability tile — Home | bd9f3502ab7d63c3cc8a3cf91657e5974188ede3bd1ac7249333d9ce5d0d932f |
| currency-dollar | currency-circle-dollar | Home capability tile — Money | 3d4a7ff3c9af21f1790e7af0e40b83e4789b7584f5dfcede56e624029053994b |
| sun | sun | Weather glance — clear | 6a180204ea92f2501de811908395e6dad15ddf2ca0a577f420fad8bef0b4c5ca |
| cloud | cloud | Weather glance — cloudy | d1a4338eccb1f8ea3353303980f15859a0a1bbc6af9d1546613182705a9b5c16 |
| cloud-sun | cloud-sun | Weather glance — partly cloudy | 5c3ca930a501926897d6c10acf5dc79c3c6ff9d32c0a292608e1b7df2313c633 |
| cloud-rain | cloud-rain | Weather glance — rain | b96c8f795c1c4964cb5331fd2d89a2a4d3d6f3148de1541539c87f0f19a02f93 |
| cloud-snow | cloud-snow | Weather glance — snow | 538d41a27d912d5a133d0b9b5073b807fe905f11cb51987b4baf4fb5a929df61 |
| cloud-lightning | cloud-lightning | Weather glance — thunderstorm | fd71cd76fd067cc726614ca17508b0cf036ae63c5ac5d029bad74998d960904f |
| cloud-fog | cloud-fog | Weather glance — fog | c8e52a845fdcc97e9b81eb5cdb43188dab1003828339c04eb782dd86381b44f1 |

Bundle weight after Phase 1: 16,580 B of SVGs.
`currency-dollar` is the same glyph as the `currency-circle-dollar`
stem (a dollar sign in a circle) — the semantic name drops the
"circle" because callers ask for the Money tile, not the stem.
The Reminders tile reuses the bundled `bell` (no new stem).

## Phase 2 additions (2026-11-24 — UI Phase 2 Discoverability)

Same source, same pinned commit
`2b75f3ad12b420c9504ef05df8d2564a28f8500e`, same fetch +
verify (HTTP 200, parses as XML, `viewBox="0 0 256 256"`,
root `fill="currentColor"`, exactly 2 paths, opacities
`['0.2', '1']` — the second path carries no opacity
attribute, i.e. full opacity, exactly as the original 19).
Same MIT licence, already cleared — no new licence check
needed. 4 entries, manifest 31 → 35:

| semantic name | Phosphor stem | used for | SHA-256 |
| --- | --- | --- | --- |
| shapes | shapes | Sidebar — Skills (CAPABILITIES) | d8c9bbb8407539772fa655c0fba8d3a1d7a1572274916d64adf6bcf3f547db8f |
| heartbeat | heartbeat | Sidebar — Doctor (was the `check` inline template) | 8d6d067a060af5d379c75a8b51227304aff9ed076b95761f0c9aa4db610da748 |
| address-book | address-book | Sidebar — Contacts (was the `square` inline template) | 82423db30fcf53e8e7d076df824489b48ff13a72bde1a8997671512a79b64f34 |
| phone-call | phone-call | Sidebar — Calls (was the `mic` inline template) | e1d36d525f21b8e3b9b2232e8f376f313886462ab8d8301c0a221dfc2534730a |

Bundle weight after Phase 2: 20,021 B of SVGs. The four
new glyphs are the same Phosphor stems under the same
names (no renaming — callers ask for the stem name).
The Calendar button keeps the bundled `grid` (no new stem);
the Skills button is the only new nav button.

## Phase 5 reuse (2026-11-27 — UI Phase 5 workspaces)

No new glyphs. The three Phase 5 nav buttons reuse glyphs
already bundled in Phase 1 — same files, same pinned commit
`2b75f3ad12b420c9504ef05df8d2564a28f8500e`, same MIT licence,
already cleared. The brief's "35 → 38" assumed all three were
new; they are not. Bundle stays 35, weight stays 20,021 B:

| semantic name | Phosphor stem | used for | SHA-256 |
| --- | --- | --- | --- |
| note | note | Sidebar — Notes (WORKSPACES) | e95bd4c4c2144398c68cc9a60c30895e25411e741096925edec402b31221ef83 |
| currency-dollar | currency-circle-dollar | Sidebar — Finance (WORKSPACES) | 3d4a7ff3c9af21f1790e7af0e40b83e4789b7584f5dfcede56e624029053994b |
| house | house | Sidebar — Devices (WORKSPACES) | bd9f3502ab7d63c3cc8a3cf91657e5974188ede3bd1ac7249333d9ce5d0d932f |

The `currency-dollar` mapping is the intentional one recorded
in Phase 1: the semantic name drops the "circle" and the file
is `currency-circle-dollar-duotone.svg`. It was already a call
site before Phase 5 (the Home Money tile), as `house` was
already used by the appearance mapping — Phase 5 only adds
the sidebar call sites. No `currency-inr` glyph was fetched:
the tree's established money glyph is reused rather than
adding a second money concept (the pages format money with ₹
in text; the glyph is the coin, not the currency).

## Honestly missing (template fallback preserved — NOT substituted)

The live loader (`gui/icons.py`) falls back to its inline
templates for this name; the resolver reports it missing.

- `senses` — no Phosphor stem exists for the radar/sonar
  glyph (centre dot + two arcs + a sweep arm). Verified by
  listing the duotone tree at the pinned commit: no
  `radar-duotone.svg`. The nearest neighbours are different
  glyphs, not the same one — `broadcast` is a tower with
  waves, `target` is concentric circles with a crosshair,
  `wifi-high` is arcs with no sweep arm — so none is
  substituted: that would be a lookalike substitution,
  which the resolver forbids. The senses nav button keeps
  its inline template, honestly.

## How it loads

`core/w9_wiring.py` loads `manifest.json` from this directory on
first icon resolution (paths in the manifest are relative to
this directory and resolved to absolute paths at load). No
manifest file → today's behaviour exactly: empty manifest,
every name missing, templates only. A corrupt manifest file
installs empty AND reports the problem via
`icon_manifest_status()` — never a silent fallback.
`icon_manifest_status()["bundling_step_owed"]` is False while
this bundle is installed cleanly.

The loader (`gui/icons.py` `make_icon`) consults the
manifest first: a bundled name renders from its Phosphor
SVG (the `currentColor` substitution carries the requested
colour into both tones — the `opacity="0.2"` path is the
secondary tone automatically, so the duotone needs no
loader change). A name the bundle does not cover falls
back to its inline template.
