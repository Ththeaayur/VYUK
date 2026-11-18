# VYUK Windows build checklist — Wave 10 (v5.92) Leaf 1

> HISTORICAL RECORD — This checklist documents the Wave 10 gate
> (v5.92.0). It is NOT the current release version: the runtime
> is 5.94.0. Version references inside this file describe the
> Wave 10 gate, not the current tree. (Gate B, WIR-09: the
> checklist is a wave record, so it is labelled as such rather
> than rewritten; the installer version itself now tracks the
> runtime in installer/vyuk.iss.)

## Status: NOT YET BUILT — read this first

**No installer .exe has been produced, and none is claimed.**
Inno Setup compiles on Windows only and the freeze has never run;
this Linux VM cannot produce `VYUK-Setup-5.92.0.exe`, cannot embed
the DPI manifest, and cannot validate the frozen exe. Everything
below is the exact procedure for a Windows box, plus the validation
still owed there. The files in this directory were validated on
this VM only as far as a non-Windows box can validate them (see
"Validated here" at the bottom).

Product version is **5.92.0** (core/version.py, gate bump). This leaf did
not bump it, and no file in this directory asserts any other
version.

## 0. Prerequisites (Windows box)

- Windows 10 or 11, 64-bit.
- Python 3.12 (the tree's runtime; `python --version` must agree).
- The tree's runtime dependencies: from the tree root,
  `pip install -r requirements.txt`.
- PyInstaller, build tool only — `pip install pyinstaller`.
  Do NOT add PyInstaller to requirements.txt: it is not a product
  dependency, it is the freezer.
- Inno Setup **6.3 or later** from jrsoftware.org (Inno 5 cannot
  run the uninstall `[Code]` in vyuk.iss). Note the install path
  of `ISCC.exe` (usually
  `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`).
- `mt.exe` from the Windows SDK or Visual Studio Build Tools, on
  PATH (needed for step 4). If `where mt.exe` finds nothing, stop
  and install the SDK — do not skip the DPI embed.

## 1. Stage the tree (folder name matters)

The tree folder itself must be named `voice_assistant` inside the
build dir, because main.py imports `voice_assistant.core` /
`voice_assistant.gui` and vyuk.spec resolves that package from the
build dir (see the spec header). Copy, do not move, the shipped
tree:

```bat
mkdir C:\vyuk-build
xcopy /E /I <shipped-tree> C:\vyuk-build\voice_assistant
cd C:\vyuk-build\voice_assistant
```

Confirm `installer\vyuk.iss`, `installer\vyuk.spec`,
`installer\vyuk.exe.manifest`, `installer\dpi_gate.py` and
`installer\wizard_image.bmp` / `installer\wizard_small.bmp` are
all present before continuing.

## 2. Freeze (PyInstaller)

From the tree root (`C:\vyuk-build\voice_assistant`):

```bat
pyinstaller --noconfirm --clean installer\vyuk.spec
```

Expected output: `dist\vyuk\vyuk.exe` plus `dist\vyuk\_internal\`
(code, `gui\assets\`, `plugins\` with `plugins_hashes.json` +
`.sig`). If the freeze fails on imports, the usual cause is the
staging name in step 1 — the folder must literally be
`voice_assistant`, not `voice-ai-assistant`.

## 3. DPI gate on the loose manifest (must PASS)

```bat
python installer\dpi_gate.py installer\vyuk.exe.manifest
```

Expected: `PASS: ... declares <dpiAwareness>PerMonitorV2</dpiAwareness>`.
A FAIL here stops the build — do not proceed to embedding a manifest
that already fails the gate.

## 4. Embed the manifest, then gate the exe (both must PASS)

PyInstaller does not embed this manifest, so embed it explicitly:

```bat
mt.exe -manifest installer\vyuk.exe.manifest -outputresource:dist\vyuk\vyuk.exe;#1
python installer\dpi_gate.py --exe dist\vyuk\vyuk.exe
```

Expected: `PASS: embedded manifest in dist\vyuk\vyuk.exe declares
<dpiAwareness>PerMonitorV2</dpiAwareness>`. The `--exe` check is
Windows-only by design: it extracts the embedded manifest with
mt.exe and checks the extracted text. If mt.exe is missing, the
gate FAILs naming mt.exe — that is a stop, not a skip.

## 5. Compile the installer

From the tree root:

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\vyuk.iss
```

Expected output: `installer\Output\VYUK-Setup-5.92.0.exe`.
Inno resolves the script's relative paths from the script's own
directory, so the wizard BMPs, `..\EULA.txt`, `..\LICENSE` and
`..\THIRD_PARTY_LICENSES.md` are picked up from the tree, and the
frozen exe from `..\dist\vyuk\` (i.e. `dist\vyuk\` under the tree
root). If ISCC reports a missing file, the missing input is real:
do not stub it — the freeze (step 2) or the staging (step 1) is
wrong.

## 6. Smoke checks (clean Windows user account / VM)

1. Run `VYUK-Setup-5.92.0.exe`. Wizard flow must be: branded
   Welcome (blue/teal art) → licence (EULA) → Installing
   progress → Finished with "Launch VYUK" checked.
2. Launch from the Finished page, then again from the Start Menu
   entry. The app must start, show the window, and pass its own
   Doctor checks; bundled fonts must render (Sora, not a fallback
   — a font fallback means `gui\assets\` landed in the wrong
   frozen location).
3. DPI: move the window between a 100% and a 125% (then 150%)
   display, or change scaling and relaunch. Text must stay crisp
   and unclipped — bitmap stretching or blur means the manifest
   embed (step 4) did not take, even if the gate passed on the
   loose file.
4. Fresh install must NOT contain a pre-filled `data\` (user data
   is created by the app at first run). Confirm the app creates
   it on first launch.
5. Plugins: the two shipped plugins must load and their hash
   verification must pass (a failure means `plugins_hashes.json`
   / `.sig` did not travel with `plugins\`).
6. Single instance: launching a second copy must point at the
   running one (tray/mini orb message), not start a second app.

## 7. Uninstall checks (PF-24 — both branches, on a scratch account)

1. Run the uninstaller. The data-choice page MUST appear before
   progress, offering "Keep my data (recommended)" (default) and
   "Delete my data". **If the page does not render on this Inno
   version, stop and report it: the safe default (keep) still
   holds, but the choice UI is then a defect to fix, not a
   detail to paper over.**
2. Keep branch: after uninstall, the data directory
   (`{app}\_internal\data`, and `{app}\data` if present) must
   still exist with its contents intact; program files must be
   gone.
3. Delete branch (scratch account only, after backing up anything
   real): after uninstall, both data locations must be gone.
4. Reinstall after a keep-uninstall: the app must find the kept
   data where it was left.

## Validation still owed on a Windows box (nothing below is done)

- [ ] Freeze succeeds (step 2) and `dist\vyuk\vyuk.exe` launches.
- [ ] Manifest embed + `--exe` gate pass (step 4).
- [ ] ISCC compiles vyuk.iss with no missing-file errors (step 5).
- [ ] Wizard flow, art, colours and launch checkbox (smoke 1–2).
- [ ] DPI crispness at 125% / 150% / 200% (smoke 3).
- [ ] No shipped `data\`; first-run data creation (smoke 4).
- [ ] Plugin hash verification in the frozen build (smoke 5).
- [ ] Single-instance behaviour frozen (smoke 6).
- [ ] Uninstall data-choice page renders (uninstall 1).
- [ ] Keep branch preserves data; delete branch removes it
      (uninstall 2–3); reinstall finds kept data (uninstall 4).

## Optional future work (NOT done by this leaf)

- Code-signing the exe and the installer (certificate + signtool
  step) once a signing identity exists. Nothing here is signed.

## Phase G (Command Deck) — done, no longer future work

- The S1 mark ships as `gui\assets\vyuk_icon.png` (512 master,
  generated by `installer\make_vyuk_icon.py`) and as the
  multi-size `gui\assets\vyuk.ico` (16/20/24/32/40/48/64/128/256);
  `icon=` in vyuk.spec and `SetupIconFile=` in vyuk.iss point at
  the .ico. The wizard BMPs are unchanged (abstract, faceless,
  no logos; their byte-determinism test pins them).

## Validated here (this VM, non-Windows limits apply)

- `installer\vyuk.exe.manifest` is well-formed XML declaring
  PerMonitorV2, and `dpi_gate.py` passes it / fails bad manifests
  (see test_v565_leaf1.py).
- Wizard BMPs are generated deterministically by
  `installer\make_wizard_art.py` (stdlib only) at the exact Inno
  sizes 164x314 and 55x58, and re-running the generator reproduces
  them byte-for-byte.
- Every non-freeze file/dir referenced by vyuk.iss exists in the
  tree; the only references that do not pre-exist are the freeze
  outputs (`dist\vyuk\vyuk.exe`, `dist\vyuk\_internal\*`), which
  step 2 produces. No .iss reference touches `data/`, and no
  reference is an invented or absolute path.
- vyuk.spec parses, names `vyuk`, sets `console=False`, bundles
  `assets`, `gui\assets` and `plugins` to their frozen locations,
  and references no `.ico` and no `data/` payload.
- vyuk.spec's `hiddenimports` cover every dynamically-imported
  module (packaging batch, WIR-01) and its `datas` carry
  `gui\skills_catalog.py` at the frozen catalogue path (WIR-02);
  before freezing, run `python3 check_packaging.py` from
  `code-lab/` — it re-derives the list from the tree and fails
  on drift or a stale catalogue snapshot.
- The tree's existing files were not edited by this leaf; only
  `installer/` files and the leaf's test suite are new.

---
## v5.95 gate note (2026-10-30)
- Installer metadata synced to 5.95.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 5.95.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.

---
## v5.96 gate note (2026-10-30)
- Installer metadata synced to 5.96.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 5.96.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.

---
## v5.97 gate note (2026-10-30)
- Installer metadata synced to 5.97.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 5.97.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.

---
## v5.98 gate note (2026-10-31)
- Installer metadata synced to 5.98.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 5.98.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.

---
## v5.99 gate note (2026-10-31)
- Installer metadata synced to 5.99.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 5.99.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.

---
## v6.0.0 gate note (2026-11-01)
- Installer metadata synced to 6.0.0 (vyuk.iss AppVersion/AppVerName/
  OutputBaseFilename, vyuk.exe.manifest assemblyIdentity 6.0.0.0) to
  keep the app/installer version agreement the Gate B suite pins
  (test_version_agreement). The installer itself remains NOT YET BUILT;
  this syncs metadata only.
