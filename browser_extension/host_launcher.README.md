# host_launcher — build notes

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

The launcher is a small C stub (`host_launcher.c`): it reads the
interpreter path from `HKCU\Software\VYUK\BrowserHost` (`PythonPath`),
spawns `python.exe -m core.browser.bridge_host` with working directory
= the VYUK install root and `PYTHONPATH=<install root>` +
`PYTHONUNBUFFERED=1`, and relays the 4-byte length-prefixed stdio
both ways. Absent registry key → exit code 2 with the named error
"host not registered".

## Build (cross-compile, MinGW-w64)

```sh
sudo apt install gcc-mingw-w64-x86-64   # provides x86_64-w64-mingw32-gcc
x86_64-w64-mingw32-gcc -O2 -municode -o host_launcher.exe host_launcher.c
```

(`-municode` is required: the stub's entry point is `wmain`, and
without it the linker looks for `WinMain` and fails.)

Run from the `browser_extension/` directory. The output
`host_launcher.exe` is the file the native-messaging host manifest
points at (`"path"`); it lives in `<install>/browser_extension/`
alongside `manifest.json` — the fixed path (Plan 9 §1.2, M-11).

## Lab result

Recorded by the Batch 1 host builder, 2026-11-14: the compiler was
installed in the lab via apt (`gcc-mingw-w64-x86-64` 13.2.0) and the
cross-compile SUCCEEDED on the second attempt — the first attempt
failed at link time (`undefined reference to WinMain`: `wmain` needs
`-municode`; the command above now carries it). Evidence in this
directory: `host_launcher.exe`, 253,017 bytes,
`file` reports "PE32+ executable (console) x86-64, for MS Windows".

## Field items

- Running the stub on his Windows machine (registry read, spawn,
  first native message round-trip) is a FIELD item — the lab has no
  Windows and no Chrome to spawn it under.
- No signing is required for a locally-built host (signing is a
  cost-gated follow-up, not a launch feature).
