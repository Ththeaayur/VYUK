# ANTIC Licence Read — Wave 8 Build 1 (anticipation-grant path)

Own code, standard library only (`dataclasses` in the path module,
`json` + `pathlib` in the tools coordinator). No third-party code,
package, model, feed, or dataset is introduced by this build, so no
per-package licence read is owed by this slice.

Deliberately NOT relied on here, and why:

- `core/anticipation.py` is NOT imported by this build. Its
  per-session capture session turns caller-supplied foreground
  observations into events — a capture surface. This build's path
  takes caller-supplied context only and produces suggestion cards;
  reusing the capture session would have smuggled a capture surface
  into a build whose whole point is the absence of capture.
- `core/watchers.py` is NOT imported, started, or scheduled by this
  build. It is a pre-existing polling watcher engine in the tree;
  this build's absence-of-capture proof (AST test + runtime test)
  covers only this build's own module, and makes no claim about
  the pre-existing watcher engine, which is a separate, older
  surface with its own gates.
- No screen, clipboard, screenshot, keystroke, or continuous
  monitoring library is used, so no terms read for any such
  service is owed by this slice. Any future slice that touches a
  real capture surface needs its own licence/terms read BEFORE
  building — that read is owed then, not waived now.

Gates carried by this build (not by a licence):

- The anticipation grant itself waits on the owner's explicit
  opt-in (v6 §4, OPEN). Building the path does not grant it.
- Only the exact scope `suggestion-cards` opens the path. Capture,
  pre-warming, watch-and-learn, screen memory, and clipboard
  history are refused whole, in code, by the gate.
- A test grant fixture is data for a test, never a live grant.
