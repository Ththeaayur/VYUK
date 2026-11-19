# VYUK — Troubleshooting

Symptom-first. Find what you're seeing, open the file it names, run the
check, apply the fix. Not sure where to start? Run **`python doctor.py`**
in the package folder first — it checks layout, syntax, dependencies,
imports, the tool registry, config, data, and assets, and prints a
`fix:` line for every failure. File/function names below are exact;
the map of what every file owns is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick index

| Symptom | Go to |
|---|---|
| `ModuleNotFoundError: No module named 'voice_assistant'` | §1 |
| App opens but shows the OLD UI | §2 |
| Chat area is white / VYUK's bubbles are invisible | §3 |
| A chat bubble renders as a thin sliver | §4 |
| No earcon sounds (wake / sent / done / error) | §5 |
| Mic or speakers not found, no audio | §6 |
| Status says `MISSING_API_KEY` / "Add your API key" | §7 |
| A tool returns an error string | §8 |
| A skill/plugin is missing from Skills or from Gemini | §9 |
| "Screenshot unavailable" / OCR says not installed | §10 |
| `window_control` / media / brightness say "Windows only" | §11 |
| Global hotkeys (Ctrl+Shift+…) do nothing | §12 |
| Wake word never triggers | §13 |
| A reminder never fires / reminders vanish after restart | §14 |
| `doctor.py` itself fails | §15 |
| Local brain: "Local brain offline — is Ollama running?" | §16 |
| Local brain answers in text but never speaks | §17 |
| A scheduled routine didn't run | §20 |
| VYUK opens twice — or won't open at all | §21 |
| VYUK mentioned my battery / a notice was wrong or missing | §22 |
| "Google isn't connected yet" keeps coming up | §23 |
| Connect Google fails in the browser step | §24 |
| VYUK remembered something wrong / something I don't want kept | §25 |
| First run: VYUK says she's "still getting myself ready" / only answers basics | §26 |
| Cloud brain won't connect (key pasted, Connect keeps failing) | §27 |
| VYUK asks "Allow once / Don't allow" before some actions / her hands suddenly stopped (failsafe) | §28 |
| Saved a key but she uses the on-device brain / Connect stuck on "Warming up…" / the button says "Cancel" | §30 |
| She connects but never answers / an error appears when you Disconnect | §34 |
| A niche skill seems missing in a cloud session / she "forgot" the toolbox | §35 |
| "Browser action paused" — she's fetching her own browser / OCR says not set up / fallback voice missing | §36 |
| VYUK can't hear me / never responds to my voice | §37 |
| She says she can't find an app that exists (e.g. Camera) | §38 |
| She says a name or word wrong (VYUK, a place, a tech term) | §39 |
| Local setup stuck on "SETUP: Downloading…" / download interrupted / offline pack storage | §18 |
| Built-in brain answers but never uses tools | §19 |

---

### 1. `ModuleNotFoundError: No module named 'voice_assistant'`

- **Likely file:** `main.py` (imports) + your folder name.
- **Why:** `main.py` puts the package folder's *parent* on `sys.path` and
  imports `voice_assistant.gui.main_window`. That only resolves if the
  package folder is literally named `voice_assistant`. A folder named
  `voice-ai-assistant` (hyphens) can never be imported — hyphens are not
  valid in Python package names.
- **Check:** look at the folder's name. `python doctor.py` also warns
  about exactly this.
- **Fix:** rename the folder to `voice_assistant`, then run
  `python main.py` from inside it.

### 2. App opens but shows the OLD UI

- **Likely cause:** a stale extracted copy. You unzipped the new build
  next to (or instead of deleting) the old folder, and launched the old
  `main.py`. The app has no auto-update — it runs whatever folder you
  start it from.
- **Check:** in the folder you launched, confirm `gui/style.py` contains
  `PREMIUM_STYLE` and `gui/visualizer.py` defines `OrbVisualizer`
  (not `CyberpunkHexVisualizer` as the main class). If not, it's an old
  copy.
- **Fix:** delete the old extracted folder **completely**, re-extract the
  zip into a fresh `voice_assistant` folder, launch that one.

### 3. Chat area paints white / VYUK's bubble is invisible

- **Likely file:** `gui/style.py` — the chat rule; or
  `gui/main_window.py` → `_build_assistant_page()`.
- **Why:** this exact bug happened before. The chat content widget may
  carry **one** rule only. Extra QSS (child-combinator rules, or a
  stylesheet set directly on the chat content widget) stops the bubble
  frames painting: white scroll area, invisible VYUK bubble, user bubble
  broken too. Everything still compiles — only a render shows it.
- **Check:** `gui/style.py` must contain exactly
  `QWidget#chatContent { background: transparent; }` for the chat, and
  `grep` `main_window.py` for `chat_content.setStyleSheet` — there must
  be **no** match (the widget only gets `setObjectName("chatContent")`).
- **Fix:** restore that single rule; remove any stylesheet set on the
  chat content widget.

### 4. A chat bubble renders as a thin sliver (or shrinks when the EQ appears)

- **Likely file:** `gui/main_window.py` → `ChatBubble.__init__()` (and
  `_add_result_card()` for result cards).
- **Why:** a word-wrapping `QLabel` inside a stretchy row collapses to
  its minimum width when a sibling widget toggles visibility — the
  `MiniEq` bars inside VYUK's bubble do exactly that while speaking.
- **Check:** `ChatBubble.__init__` must end with the deterministic width:
  `setFixedWidth(max(150, min(560, max(text_w, who_w, time_w) + 30)))`
  with widths from `QFontMetrics`. Result cards use
  `max(170, min(560, body_w + 30))`.
- **Fix:** restore the `QFontMetrics`-based `setFixedWidth` calls. Do not
  replace them with layout stretch or minimum widths — that's the bug
  coming back.

### 5. Earcons are silent

- **Likely file:** `gui/sounds.py` (`Earcons`).
- **Why:** sounds are *designed to fail silently*. `Earcons` no-ops if
  `PySide6.QtMultimedia` can't be imported, if a WAV is missing, or if
  sounds are disabled in settings. Silence is the symptom of all three.
- **Check, in order:**
  1. `python -c "from PySide6.QtMultimedia import QSoundEffect"` — in
     another Python, this import failing means your PySide6 install
     lacks QtMultimedia (install the full `PySide6`, not a stripped
     subset).
  2. `gui/assets/` contains `wake.wav`, `sent.wav`, `done.wav`,
     `error.wav` (missing files are skipped silently).
  3. Settings → Sound: sounds enabled + volume up. The values live in
     QSettings as `sounds_enabled` / `sounds_volume`
     (`MainWindow._on_sounds_toggled()` / `_on_volume_changed()`).
- **Fix:** whichever check failed — reinstall full PySide6, restore the
  WAVs, or re-enable in Settings. `python doctor.py` checks the WAVs.

### 6. Mic or speakers not found / no audio at all

- **Likely file:** `core/voice/audio.py` → `AudioManager.start()` /
  `get_devices()` (sounddevice).
- **Check:** on launch, `MainWindow.__init__` catches audio startup
  failure and prints `[Audio Hardware Error]: …` to the console — run
  from a terminal and read it. Confirm the OS itself sees the devices,
  and that `sounddevice` is installed (`python doctor.py` flags it).
  The Settings microphone list is filled by
  `MainWindow._populate_audio_devices()` from `audio_manager.get_devices()`;
  the onboarding mic-test bar reads `audio_manager.mic_level`.
- **Fix:** install/fix the device at OS level first; then check the
  sample rates in `core/config.py` (16 kHz in / 24 kHz out are what
  Gemini Live expects — a device that refuses 16 kHz input will fail
  stream start).

### 7. Status shows `MISSING_API_KEY` / "Add your API key"

- **Likely files:** `core/voice/live_client.py` → `GeminiLiveSession.start()`
  (it emits `MISSING_API_KEY` when `config.api_key` is empty) and
  `core/config.py` (where the key comes from).
- **Check:** is there a `.env` next to `main.py` with a non-empty
  `GEMINI_API_KEY=`? Note `core/config.py` `load_env_file()` never
  overwrites a variable already set in the process environment — a stale
  empty/system `GEMINI_API_KEY` wins over the file. `python doctor.py`
  reports whether a key is visible.
- **Fix:** paste the key in the app (Settings → Gemini API key →
  `MainWindow._save_api_key()`, or first-run onboarding). Both save via
  `config.save_api_key()`, which writes `.env`. Then press Connect.

### 8. A tool returns an error string ("Execution failed: …" / a refusal / a weird result)

- **Likely files:** `core/funnel/tools_registry.py` → `ToolRegistry.execute()`; the
  handler itself (see map below); `core/desktop/control.py`.
- **Why:** `execute()` catches every handler exception and returns
  `{"status": "error", "message": "Execution failed: …"}` — so an error
  string means the handler ran and failed (or refused on purpose), not
  that the plumbing broke.
- **Check:** open `data/activity.log` — one line per call:
  `timestamp | tool_name | success` or `error: <message>`, written by
  `ToolRegistry._log_activity()`. (Asking VYUK for the `get_activity_log`
  tool shows the same lines.) Match the timestamp to your action, read
  the message, then open the handler:

  | Tool name pattern | Handler lives in |
  |---|---|
  | `launch_application`, `open_url`, `get_current_time_date`, `adjust_volume`, `lock_screen`, `remember_user_fact`, `recall_memories` | `core/funnel/tools_handlers.py` `_tool_*` methods |
  | `web_search`, `get_weather`, `set_reminder`, `read_clipboard`, `write_file`, `read_file`, `get_system_info` | `core/funnel/tools_handlers.py` `_tool_*` methods |
  | `type_text`, `press_hotkey`, `mouse_control`, `run_command`, `window_control`, `file_control`, `close_application`, `media_control`, `set_brightness`, `power_control`, `write_clipboard`, `open_path`, `take_screenshot`, `get_active_window`, `ocr_screen`, `get_activity_log` | `core/desktop/control.py`, function of the same name |
  | anything else (e.g. `get_stock_quote`, `get_crypto_price`) | a plugin: `plugins/<file>.py` → its `run()` |

- **Refusals are not errors:** messages starting "Blocked by VYUK's
  trust layer" or "Refused:" come from `core/desktop/control.py` on purpose —
  destructive shell commands need `confirm=True`, disk format/wipe is
  always refused, `file_control` delete needs `confirm=True` (and only
  ever moves to `_nova_trash/`), `close_application` and
  shutdown/restart need `confirm=True`. See ARCHITECTURE.md, Landmine 5.

### 9. A skill/plugin is missing (Skills page empty, or Gemini doesn't know the tool)

- **Likely file:** `core/plugin_loader.py` → `discover_plugins()`.
- **Check:** run the app from a terminal and read the `[Plugins]` lines:
  discovery prints `Loaded: …`, `Skipping <file>: missing PLUGIN dict
  or run() function`, or a full traceback per bad file. The contract is a
  module-level `PLUGIN` dict plus a callable `run()`. Files starting
  with `_` are ignored on purpose. Registration into the tool list
  happens in `ToolRegistry.load_plugins()` in `core/funnel/tools_handlers.py` (ToolHandlersMixin, inherited by ToolRegistry); the
  Skills page list is filled by `MainWindow._reload_plugins()`.
- **Fix:** correct the plugin file to the contract. `python doctor.py`'s
  tool count (expected 32 = 30 built-in + 2 plugins) also drops when a
  plugin fails to load.

### 10. "Could not capture a screenshot…" / OCR says not installed

- **Likely files:** `core/desktop/control.py` → `take_screenshot()` /
  `ocr_screen()` / `_capture_screen_bytes()`; `core/desktop/vision.py` →
  `VisionManager.capture_screen()`.
- **Why:** screenshots use PIL `ImageGrab`, which needs a real display —
  on a headless machine it fails and the tool returns that message
  (working as designed). OCR needs the `pytesseract` package *and* a
  Tesseract engine — since v5.35 the engine is resolved by
  `core/voice/local_assets.py` → `tesseract_path()` (the installer's bundled
  `assets/ocr/` tree first, then `data/ocr/`, then PATH); before that
  only a system install on PATH counted, so OCR was dead on a fresh
  machine. Webcam capture additionally needs OpenCV —
  `core/desktop/vision.py` sets `HAS_OPENCV = False` and `capture_webcam()`
  returns `None` without it.
- **Fix:** run on the real desktop (not headless). For OCR, run the
  doctor and read its "OCR engine" line: no engine anywhere means
  the install is incomplete (reinstall from the current installer —
  the engine ships inside it; nothing to download by hand). From
  source, `pip install -r requirements.txt` covers the package and
  any system `tesseract` on PATH is picked up automatically.

### 11. `window_control` / media keys / brightness / active window reply "only available on Windows"

- **Likely file:** `core/desktop/control.py` → `_is_windows()` guards in
  `window_control()`, `media_control()`, `set_brightness()`,
  `get_active_window()`, and `power_control()` (sleep path).
- **Why:** these use Windows-only mechanisms by design — window
  enumeration via `ctypes.windll.user32.EnumWindows`, media virtual-key
  codes, brightness via WMI through PowerShell (`WmiMonitorBrightnessMethods`).
  On macOS/Linux they return the fallback string and touch nothing.
- **Fix:** none needed on Windows (if you *are* on Windows and still get
  the message, you're not running the code you think — see §2). On other
  OSes this is expected behaviour, not a bug.

### 12. Global hotkeys (Ctrl+Shift+N / S / M) do nothing

- **Likely file:** `core/desktop/hotkeys.py` → `HotkeyDaemon.start()`.
- **Check:** if pynput isn't installed, the daemon prints
  `[Hotkeys] pynput not installed — global hotkeys disabled.` at startup
  and returns — run from a terminal and look for it. The daemon is
  started by `MainWindow._start_background_services()`.
- **Fix:** `pip install pynput`. Don't confuse these with the *in-window*
  keys, which are separate code in `gui/main_window.py`: hold-Space PTT
  (`keyPressEvent`/`keyReleaseEvent`) and Ctrl+K command palette
  (a `QShortcut` set up in `MainWindow.__init__`) — if those fail but
  global hotkeys work, debug there instead.

### 13. Wake word never triggers

- **Likely files:** `core/voice/wake_word.py` → `WakeWordDetector._detection_loop()`;
  `gui/main_window.py` → `_toggle_wake_word()`.
- **Check:** the detector only runs while enabled — the sidebar wake-word
  toggle sets `wake_word_detector.enabled` (and the button text changes
  to "On — say "Hey VYUK""). It listens to `audio_manager.mic_level`, so
  §6 applies too. SpeechRecognition is optional: without it, detection
  falls back to the energy-cadence trigger in the same loop, which needs
  a quiet-then-loud pattern (`mic_level` burst above ~0.22 after a quiet
  stretch). Successful detection calls `on_wake`, wired in
  `_start_background_services()` to `MainWindow._on_wake_word_activated()`.
- **Fix:** enable the toggle, confirm the mic level moves when you speak
  (onboarding mic test), and check the console for `[WakeWord]` lines.

### 14. A reminder never fires — or reminders vanish after restart

- **Likely file:** `core/scheduler.py` → `ProactiveScheduler`
  (`add_reminder()`, `_run_loop()`, `on_trigger`).
- **Why, second symptom first:** reminders are **in-memory only** — a
  plain list in the scheduler object. Nothing is written to disk, so a
  restart clears them. That is the current design, not data loss.
- **Check:** the scheduler thread is started in
  `MainWindow._start_background_services()` (`scheduler.start()`), and
  `on_trigger` is wired there to `MainWindow._on_scheduler_trigger()`.
  Voice reminders are created by the `set_reminder` tool
  (`core/funnel/tools_handlers.py` → `_tool_set_reminder()`) with labels like
  `reminder_HHMMSS`; UI reminders by
  `MainWindow._add_reminder_from_ui()`. The Reminders page list comes
  from `scheduler.get_all_reminders()` via `_refresh_reminders()`.
- **Fix:** if the list shows the reminder but it never fires, check the
  system clock vs `fire_at`; if it fires but nothing appears in the app,
  the `on_trigger` wiring in `_start_background_services()` is broken.

### 15. `doctor.py` itself fails

- **Likely file:** `doctor.py` (package root).
- **Check:** the doctor is stdlib-only and wraps every check, so a crash
  prints `[FAIL] Doctor internal error in <check>` instead of a
  traceback. First run `python -m py_compile doctor.py`. If it reports
  layout failures for folders that visibly exist, doctor.py has probably
  been copied *out* of the package — it locates everything relative to
  its own file, so it must live at the package root next to `core/`,
  `gui/`, `plugins/`, `data/`.
- **Fix:** put `doctor.py` back at the package root and run
  `python doctor.py` from inside that folder. A Python older than 3.10
  will also fail its first check — upgrade Python before debugging
  anything else.

### 16. Local brain says "Local brain offline — is Ollama running?"

- **Likely file:** `core/voice/local_client.py` → `LocalLiveSession.start()`
  (the Ollama reachability check), settings in `core/config.py`
  (`BRAIN_ENGINE`, `OLLAMA_HOST`, `OLLAMA_MODEL`).
- **Why:** with the brain set to Local (`BRAIN_ENGINE=local`) **and**
  the backend set to Ollama (`LOCAL_BACKEND=ollama` — the default
  `builtin` backend never talks to Ollama), Connect first asks Ollama
  for its model list (`GET /api/tags`). If nothing answers, the
  session refuses to start — mirroring the Gemini brain's
  `MISSING_API_KEY` behaviour — instead of hanging on a dead brain.
- **Check:** run `python doctor.py` — the **Brain** check probes the
  same endpoint and names the host it tried. Then open
  `http://127.0.0.1:11434` in a browser: Ollama answers "Ollama is
  running" when it's up. A console note about the model "not in
  Ollama's model list" means Ollama is up but the model isn't pulled —
  that one is a warning, not the offline error.
- **Fix:** install Ollama from https://ollama.com, start it, and pull
  the model: `ollama pull qwen2.5:7b` (or whatever `OLLAMA_MODEL` is
  set to). If Ollama runs on a non-default port/host, set `OLLAMA_HOST`
  in `.env` to match.

### 17. Local brain answers in text but never speaks

- **Likely file:** `core/voice/local_client.py` → `_speak()` (builtin
  backend: Kokoro first, then Piper, then Windows SAPI, then
  text-only; ollama backend: Piper → SAPI → text-only), voice settings
  `KOKORO_VOICE` / `LOCAL_TTS_VOICE` in `core/config.py`.
- **Why:** on the builtin backend the first voice is Kokoro, whose
  model + voices files are auto-downloaded into `data/voices/kokoro/`
  (see §18 if they're missing). Piper — the next fallback — is a
  *file*, not a service: it needs both
  the `piper-tts` package AND a voice model at
  `data/voices/<LOCAL_TTS_VOICE>` (default `en_US-amy-medium.onnx`)
  **with its matching `.onnx.json` sidecar next to it**. Without the
  package or the file, VYUK falls back to Windows SAPI (pyttsx3) and,
  failing that, to text-only — deliberately, with a one-time console
  note, rather than crashing the session.
- **Check:** run the doctor and read its "Fallback voice (Piper)"
  line — it resolves the voice through
  `core/voice/local_assets.py` → `piper_paths()`. Watch the console at
  Connect/first reply for `[LocalBrain] Piper synthesis failed: …`
  or the text-only note.
- **Fix:** since v5.35 the default voice (`en_US-amy-medium.onnx` +
  its `.onnx.json`, from Hugging Face `rhasspy/piper-voices`) is
  provisioned automatically — it ships inside the installer and
  setup downloads it with the voice pack when missing; a missing
  one means setup hasn't finished or the install is incomplete,
  never a file to fetch by hand. A *custom* voice named by
  `LOCAL_TTS_VOICE` is still a manual drop into `data/voices/`
  (that part is a user choice, by design). From source,
  `pip install piper-tts` provides the synthesis package.

### 18. Local setup stuck on "SETUP: Downloading…" (or a download was interrupted)

- **Likely file:** `core/voice/local_assets.py` → `ensure_local_assets()` /
  `download_file()` / `storage_report()` / `remove_offline_pack()`;
  settings in `core/config.py` (`LOCAL_BACKEND`, `LOCAL_MODEL_FILE`).
- **Why:** on the **builtin** backend, first connect downloads the
  Offline Pack: the brain model into `data/models/` (brain alone:
  ≈1.1 GB Lite / ≈2.5 GB Balanced, the default / ≈5 GB Quality —
  whole pack with Whisper + Kokoro runs ≈1.9 / ≈3.3 / ≈6 GB total)
  and the Kokoro voice files into `data/voices/kokoro/`. Downloads
  stream to a sibling `.part` file and are renamed into place only
  when complete and size-verified. Brain downloads are also
  SHA-256-verified against the publisher's hash before they are
  accepted, and a file that fails that integrity check is discarded
  while the run fails — and auto-retries later — exactly like any
  other download failure.
- **Interrupted downloads RESUME (v2.0):** if the app closes, the
  network drops, or the download is paused (Settings, or disconnecting
  mid-setup), the partial `.part` file is *kept* — it is resume state,
  not junk. The next connect (or pressing "Set up local brain now"
  again) sends an HTTP `Range` request and continues from that byte;
  progress lines then start with "Resuming …" and the percentage
  counts the full file. **Do not delete `.part` files** to "unstick"
  a download — deleting one is the only way to force a restart from
  zero (ARCHITECTURE.md landmine 13).
- **"It restarted from 0 anyway":** the status line says
  `Server doesn't support resume — restarting this file.` Some
  servers and CDN mirrors ignore the `Range` header and answer with
  the whole file; VYUK then truncates and re-downloads that one file.
  This is expected occasionally (mirror-dependent), the finished
  file is still size-verified before use, and the *next* file still
  resumes normally.
- **Check:** watch the `SETUP: Downloading … / Resuming … — N%`
  status line: is N moving at all? Progress moves in ≥2% steps, so a
  big file can sit on one number for a while. Look in `data/models/`
  — a growing `*.gguf.part` means it's alive, just slow (even 2.5 GB
  on a slow link is genuinely long). `python doctor.py`'s Brain /
  Offline pack lines report what's on disk and whether the finished
  files are present. No `.part` and no progress means the download
  failed immediately — the status line after it carries the
  `ERROR: download of … failed: …` text with the cause. A status line
  saying `Custom brain model expected at data/models/…` is not a
  stuck download at all: `LOCAL_MODEL_FILE` names a non-tier (custom)
  model, which is never auto-downloaded — place the GGUF at that
  path, or set `LOCAL_MODEL_FILE` to a tier filename (Lite/Balanced/
  Quality, see `MODEL_TIERS` in `core/voice/local_assets.py`).
- **Storage & removal:** Settings → Brain shows the space the
  Offline Pack occupies; **Remove offline pack** there deletes
  `data/models/` and `data/voices/kokoro/` (including `.part` resume
  state) and reports the freed space. Piper voice files you placed
  directly in `data/voices/` are *not* part of the pack and are never
  removed. The cloud brain needs none of this — the pack is purely
  optional.
- **Fix:** leave the app open until setup completes, or close it
  without worry — the download resumes next time. Only for a truly
  wedged file (corrupt `.part`, repeated size-mismatch errors): close
  VYUK, delete that one `.part`, and connect again. If downloads fail
  instantly every time, check the machine can reach huggingface.co
  and github.com (the two download hosts in `core/voice/local_assets.py`),
  and that the disk has ~4 GB free (Balanced pack + headroom).
- **Proxy & mirror (v3.3):** VYUK downloads through the Windows
  **system proxy** automatically — the one browsers use, read from
  the Internet Settings (plus `HTTP_PROXY`/`HTTPS_PROXY` env vars).
  A `Downloading via proxy <host:port>` status line is normal on a
  campus/hostel network and is usually the difference between the
  browser working and the app not. When huggingface.co itself won't
  answer, the brain and ears downloads retry **once** against
  **hf-mirror.com** (a full Hugging Face mirror — same file, same
  bytes, same resume), announced by a `…retrying via the
  hf-mirror.com mirror…` line; the voice files come from GitHub,
  which has no mirror twin, so they rely on the proxy + resume.
  After a failed run VYUK will **not** auto-retry at every launch —
  it waits ~10 minutes between automatic attempts (pressing Download
  yourself always works immediately). The last failure is remembered
  across restarts: `python doctor.py`'s Brain line quotes it.
- **Decoding the `ERROR:` line:** the exact text after `ERROR:` is
  the diagnosis — and it is the line to send your developer/support
  if you ask for help; copy it whole.
  - `getaddrinfo failed` / `Name or service not known` — the PC
    can't resolve the host's name: network down, or a captive-portal
    (hostel/hotel login) not yet completed in a browser.
  - `SSL: CERTIFICATE_VERIFY_FAILED` — something between you and the
    host is intercepting HTTPS (captive portal, antivirus web
    shield, a proxy doing TLS inspection). Log in through the portal
    in a browser first; if it persists, that network is inspecting
    traffic and the mirror retry may fare no better.
  - `HTTP Error 403` — the host refused the file (some campus
    firewalls block by category). The mirror retry happens
    automatically; if both refuse, use the manual download below.
  - `HTTP Error 404` — the file isn't at that address. If it happens
    on every attempt and host, report it with the ERROR line — a
    tier URL in `core/voice/local_assets.py` (`MODEL_TIERS`) needs fixing.
  - `incomplete transfer (X of Y bytes)` — the connection dropped
    mid-file. Not corruption: the `.part` is kept and the next
    attempt resumes at byte X.
  - `timed out` / `Network is unreachable` / `Connection refused` —
    the route itself is dead or blocked; on a managed network this
    is what "no system proxy was used" used to look like — make sure
    you are on v3.3+, where the system proxy is picked up.
  - `No space left on device` — exactly that; the pack needs
    ~2–6 GB depending on tier (see §18 top).
- **Still stuck? Download the brain file yourself (escape hatch):**
  a browser routes around every failure above (proxy, portal,
  logged-in network). The pack detects a hand-placed file
  automatically — no setting to change, no download needed:
  1. In your browser, download the brain file for the tier you
     want (any tier works — VYUK adopts a tier file it finds):
     - Lite (≈1.1 GB): `Qwen3-1.7B-Q4_K_M.gguf` from
       `https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf`
     - Balanced (≈2.5 GB, the default): `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` from
       `https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`
     - Quality (≈5.0 GB): `Qwen3-8B-Instruct-2507-Q4_K_M.gguf` from
       `https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Instruct-2507-Q4_K_M.gguf`
     If huggingface.co won't load even in the browser, swap the
     host for `hf-mirror.com`, same path.
  2. Move the file into VYUK's `data/models/` folder with **exactly**
     that filename. Check the downloaded size matches the tier's
     ≈GB above — a short file is an incomplete browser download;
     fetch it again. (A leftover `<filename>.part` beside it is
     harmless once the real file is in place.)
  3. The voice files can be placed the same way if they are the
     stuck piece: `kokoro-v1.0.onnx` (≈310 MB) and
     `voices-v1.0.bin` (≈27 MB) from the kokoro-onnx
     `model-files-v1.0` GitHub release, into `data/voices/kokoro/`.
     Whisper's weights are library-managed and land on the first
     successful voice use — there is no manual step for them.
  4. Restart VYUK (or just Connect): `python doctor.py`'s Brain
     line should now say "built-in brain model ready".

### 19. Built-in brain answers questions but never uses tools

- **Likely file:** `core/voice/local_client.py` → `_brain_turn_builtin()`,
  `_render_tool_protocol()` (teaches the protocol) and
  `_extract_tool_call()` (parses the reply); model setting
  `LOCAL_MODEL_FILE` in `core/config.py`.
- **Why:** the built-in backend has no native function calling — the
  model must *choose* to reply with only
  `{"tool": "<name>", "arguments": {...}}`, and `_extract_tool_call()`
  only executes it when the JSON parses **and** the name is a real
  registered tool. A weaker or heavily-quantised model may answer in
  prose instead, bury the JSON in malformed text twice in a row (the
  single repair retry is then exhausted), or invent tool names that
  the validator correctly refuses.
- **Check:** ask for something unmistakably tool-shaped ("what time is
  it?"). Watch `data/activity.log` — a working tool call lands there.
  Then read the assistant's raw replies in the conversation log: clean
  JSON in chat means the model replied with the block but it wasn't
  executed (parser/validation — this file); prose means the model
  never tried (prompt/model — below).
- **Related symptom — reasoning leaking into replies:** if VYUK's
  chat or speech ever includes `<think>…</think>` blocks, a
  reasoning-style model has leaked its chain-of-thought.
  `_strip_think()` in `core/voice/local_client.py` strips those blocks
  before anything is shown or spoken; if you still see them, report
  the exact `LOCAL_MODEL_FILE` in use — a custom model may wrap its
  reasoning in a format `_strip_think()` doesn't recognise.

### 20. A scheduled routine didn't run

- **Symptom:** a routine with a schedule (`daily 07:30`, …) reaches
  its time and nothing happens — no run in `data/activity.log`, no
  chat activity.
- **Likely file:** `core/routine_scheduler.py` (the firing loop),
  `core/brain/planner.py` (`parse_schedule` / `next_run`), and the data
  files `data/routines.json` + `data/routine_state.json`.
- **Check:**
  1. The routine must actually carry a schedule: open
     `data/routines.json` and confirm the record has a `"schedule"`
     object (`{"kind": "daily", "time": "07:30"}` etc.). Note the
     `save_routine` **tool** can't set one (its schema has no
     schedule parameter, by design — see ARCHITECTURE landmine 14);
     schedules come from Settings or from editing the JSON.
  2. The schedule text must parse: valid forms are `daily HH:MM`,
     `interval 45m` / `interval 2h`, `once YYYY-MM-DD HH:MM`.
     `planner.parse_schedule()` raises `ValueError` with the expected
     format for anything else, and `save_routine()` refuses the save
     — so a routine that saved fine but never fires usually has a
     hand-edited, malformed `"schedule"` dict instead: check `kind`
     is exactly `daily` / `interval` / `once` and the fields match
     (`time`, `minutes`, `at`).
  3. VYUK must be **running** at the slot. The scheduler is an
     in-process thread (20-second tick) started by the window —
     there is no Windows service. A `once` routine whose moment
     passed while VYUK was closed fires once at the next launch; a
     `daily` fires at its next slot (or immediately on launch if
     today's slot passed unfired).
  4. Check `data/routine_state.json`: if the routine's name has a
     `last_run` at/after the slot, the scheduler believes it already
     fired (state is written *before* the callback runs, on
     purpose). A stale/wrong entry there suppresses exactly one slot.
  5. If it "ran" but stopped instantly, that's the trust layer, not
     the scheduler: scheduled routines go through `tools.execute()`
     with no auto-confirmation, so a destructive step refuses and
     halts the routine — see §8.
- **Fix:** correct the schedule in Settings (or the JSON), keep VYUK
  running across the slot, and if `routine_state.json` is lying,
  delete the routine's entry (or the whole file — it rebuilds) and
  let the next slot fire.

### 21. VYUK opens twice — or won't open at all

- **Symptom (a):** two VYUK windows / two tray icons, hotkeys and
  mic behaving as if possessed. **Symptom (b):** launching VYUK
  shows "VYUK is already running — check your tray/mini orb." even
  though no VYUK is visible anywhere.
- **Likely file:** `core/presence.py` (`acquire_single_instance`),
  wired in `main.py`.
- **Why:** VYUK enforces a single instance with a `QLockFile` at
  `<temp>/Nova_instance.lock` — two copies would fight over the
  microphone, hotkeys, and the local-brain port. The second launch
  is *supposed* to show that message and exit.
- **Check / Fix:**
  - For (a): a copy started before v2.1 (no lock) can coexist with a
    new one — close every VYUK (Task Manager → end any stray
    `pythonw.exe` running `main.py`), then launch once.
  - For (b): VYUK is probably minimised to the tray — check the
    system tray / mini orb first. If she genuinely isn't running, a
    crashed VYUK may have left the lock file behind (the lock is
    deliberately never-stale, `setStaleLockTime(0)`): delete
    `Nova_instance.lock` from the temp folder (`%TEMP%` on Windows)
    and launch again. Never delete it while VYUK *is* running —
    that just re-enables the double-launch the lock exists to
    prevent.
  - Auto-start's "already running" at sign-in: if you also launched
    VYUK manually before the Run-key copy started, the second copy
    exits quietly by design (check `doctor.py`'s Presence line for
    the auto-start state).
- **Fix:** first, keep the default model — the Balanced tier
  (`Qwen3-4B-Instruct-2507`) follows the protocol reliably, and the
  Quality tier (Qwen3-8B) is at least as good; a smaller or base
  (non-instruct) GGUF often won't. Confirm `LOCAL_MODEL_FILE` names
  an *instruct* model. If replies show almost-right JSON, the repair-retry prompt in
  `_brain_turn_builtin()` and the protocol wording in
  `_render_tool_protocol()` are the tuning knobs — do not loosen
  `_extract_tool_call()`'s registry-name validation to "make it work";
  that check is what stops hallucinated tool names from executing.

### 22. VYUK mentioned my battery / a notice was wrong — or one never came

- **Symptom:** VYUK announced something unprompted ("Your battery is
  at 18%…", "Your download finished…", "Welcome back…") and it was
  wrong, surprising, or unwelcome — or the opposite: a notice you
  expected (low battery, finished download) never arrived.
- **Likely file:** `core/watchers.py` → `WatcherEngine` (the per-kind
  watcher methods `_watch_battery` / `_watch_downloads` /
  `_watch_network` / `_watch_idle` / `_watch_disk` / `_watch_drives`);
  the window side only displays/speaks what the engine emits.
- **Why:** since v2.3 VYUK *notices* things on a 30-second poll.
  Events are posted in chat always, and spoken when a brain session
  is connected. Each watcher fires **once per episode** by design:
  battery thresholds (20% / 10% / full) re-arm only when charging is
  seen or the level rises 5+ points above the threshold; a full-disk
  notice re-arms only after free space recovers above 12%; a
  download is announced only when a **new** file's size is identical
  on two consecutive polls.
- **Check:**
  1. **Don't want a category?** Settings → Presence → **Notices**
     toggles switch each kind off GUI-side; the engine keeps polling
     but the window drops disabled kinds. There is no per-event
     "don't tell me again" — the once-per-episode rules above are
     the anti-nag mechanism, so a notice that repeats every poll is
     a genuine bug: note the kind and the exact wording.
  2. **Battery notices on a desktop never fire** — there is no
     battery to read (`psutil.sensors_battery()` returns None) and
     the watcher silently stays off. Same for *all* battery/disk/
     drive watchers when psutil isn't installed. Run
     `python doctor.py` and read the **Watchers** line: it reports
     psutil availability and battery presence on this machine.
  3. **A download wasn't announced:** files already in
     `~/Downloads` when VYUK started are the baseline and are never
     announced, by design; partial downloads (`.crdownload`, `.part`,
     `.tmp`, `.download`, `.opdownload`, `.aria2`) never announce —
     only the finished file, after its size is stable across two
     polls (~a minute). Zero-byte files don't count.
  4. **Welcome-back is Windows-only:** idle time comes from
     `GetLastInputInfo`; elsewhere the probe returns None and that
     watcher is off. The away threshold is 20 minutes idle, and the
     "away for N minutes" figure is the longest idle stretch the
     engine observed, rounded.
  5. **Offline/online notices:** going offline needs **two**
     consecutive failed probes (~a minute) so a single blip stays
     silent; "back online" is only announced if the outage was.
- **Fix:** toggle the category in Settings → Presence → Notices;
  install psutil (`pip install psutil`) if the doctor's Watchers
  line says it's missing; on a desktop, accept that battery notices
  can't exist. If a state machine looks stuck (a kind that can
  never fire again, or fires repeatedly), restart VYUK — watcher
  state is in-memory only and re-seeds cleanly at engine start.

### 23. "Google isn't connected yet" keeps coming up

- **Symptom:** every calendar/mail question ("what's on today?",
  "any unread mail?") gets "Google isn't connected yet — open
  Settings → Your world and press Connect Google." — even after you
  pressed Connect Google once.
- **Likely file:** `core/google_services.py` → `is_connected()` /
  `connect()`; the token at `data/google_token.json`; the client
  codes in `core/config.py` (`GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET`).
- **Why:** the four world tools (`get_todays_schedule`,
  `get_upcoming_events`, `get_unread_email`, `search_email`) check
  `is_connected()` first: the token file must exist, parse, and hold
  a refresh token (or an unexpired access token). Anything else —
  never connected, browser step abandoned, token deleted or revoked
  at myaccount.google.com, a half-written token — reads as "not
  connected", on purpose.
- **Check:**
  1. `python doctor.py` → the **Your world** line: it distinguishes
     "libraries not installed" (fix: `pip install -r
     requirements.txt`) from "not connected yet".
  2. Does `data/google_token.json` exist next to `assistant.db`? No
     file = the consent flow never completed (see §24). A file that
     exists but is empty/corrupt JSON = same conclusion.
  3. Did the codes get saved? Settings → Your world should still
     show your client ID/secret; `connect()` refuses *before* opening
     the browser with "Add your Google client codes first…" when
     they're blank.
- **Fix:** connect again from Settings → Your world and finish the
  browser consent. If a token exists but looks stale (you changed
  your Google password, revoked VYUK's access, or the file predates
  a scope change): press **Disconnect** there (deletes the token and
  asks Google to revoke it), then **Connect Google** again for a
  fresh token.
- **Note:** the calendar heads-ups (v2.5) share this state —
  `next_events()` returns an empty list while Google is
  disconnected, so VYUK simply never speaks one; no warning is shown
  anywhere, on purpose.

### 24. Connect Google fails in the browser step

- **Symptom:** pressing Connect Google opens the browser but sign-in
  fails — Google shows an error (`invalid_client`,
  `access_blocked`, "this app is blocked", a 403 about an API), or
  the flow ends and VYUK still isn't connected.
- **Likely file:** not VYUK's code — the Google Cloud project behind
  the client codes in `core/config.py` (`GOOGLE_CLIENT_ID` /
  `GOOGLE_CLIENT_SECRET`), consumed by `core/google_services.py` →
  `connect()` / `_run_flow()`. VYUK prints the failure in parentheses
  in its reply; that parenthetical is Google's own error text.
- **Check, in the order these actually happen:**
  1. **`invalid_client` / "client not found":** the pasted client ID
     or secret is wrong, mistyped, or from a *Web application* client
     instead of a **Desktop app** client. Re-copy both from Google
     Cloud → APIs & Services → Credentials, and save them again in
     Settings → Your world.
  2. **"Access blocked: this app's request is invalid" / the app is
     in Testing:** while the OAuth consent screen is in *Testing*
     publishing status, only listed test users can sign in. Add your
     own Google account under *OAuth consent screen → Test users*,
     or publish the consent screen.
  3. **403 `accessNotConfigured` naming Gmail or Calendar:** the API
     isn't enabled in the Cloud project. In Google Cloud → APIs &
     Services → Library, enable **Gmail API** and **Google Calendar
     API** for the *same* project that owns the client codes, wait a
     minute, retry.
  4. **Consent completed but VYUK still says not connected:** the
     token save failed or the consent was granted to a different
     account than you expected — see §23 (check
     `data/google_token.json` exists and the doctor's Your world
     line), then Disconnect + Connect again, making sure you pick
     the right account on Google's account chooser.
- **Fix:** correct the Cloud project side (codes / test user / APIs),
  then Disconnect + Connect Google once more. VYUK can't work around
  any of these — Google enforces them before a token exists.

### 25. VYUK remembered something wrong — or something about me I don't want kept

- **Symptom:** VYUK states a "fact" about you that's wrong or stale
  (wrong name, an old preference), or she picked up something you
  never wanted kept — and keeps using it, because learned facts ride
  into the model's context like any other fact.
- **Likely file:** `core/memory/learning.py` — its deterministic patterns
  file statements under category `learned` in `core/memory/memory.py`'s
  facts table. The review surface is the **Memory page** (the
  "What VYUK has learned" ledger reads `get_learned_facts()`).
- **Check:** open the Memory page — every learned fact is listed in
  plain words ("your sister's name is Diya"), newest changes stamped.
  Run `python doctor.py` and look at the **Learning** line: PASS
  means the extractor imports and fires on a probe sentence.
- **Fix, mildest first:**
  1. **Correct it out loud:** say the fact again with the new value
     ("my name is …", "I like … now"). Facts upsert on
     (category, key), so the new statement *replaces* the old one —
     no duplicate, no stale value.
  2. **Forget it:** Memory page → **Forget** on that fact (or
     **Forget everything VYUK has learned** for the whole category).
     Forgetting is category-guarded — hand-saved facts and other
     categories are never touched by these controls.
  3. **Stop it learning:** turn learning off in **Settings** — the
     toggle gates `learn_from_text()`, so VYUK stops filing new
     facts; what's already stored stays until you forget it.
- **Why it happens:** extraction is patterns, not understanding —
  a sentence that happens to match a pattern ("my … is …",
  "I like …") gets filed. Questions are never filed, and moods
  ("I'm happy") and transient states ("my phone is dead") are
  stoplisted, but if a *durable-sounding* statement was wrong, the
  ledger above is the fix — by design, every learned fact is
  visible and removable.

### 26. First run: VYUK says she's "still getting myself ready" — or only answers the basics

- **Symptom:** on a fresh install VYUK connects instantly but
  answers with "I'm still getting myself ready — about N%
  downloaded", handles only simple asks (the time, battery, system
  status, weather, opening apps, remembering a fact, today's
  schedule, unread mail, a screenshot), and later — mid-conversation
  — announces "My full brain is ready — I'm all here now."
- **This is the design, not a fault (v2.7):** the default engine is
  the on-device brain, and its offline pack (ears → voice → brain,
  ≈3.3 GB for the default Balanced tier) downloads in the background
  after first connect. While the brain GGUF is still landing,
  `core/brain/brain.py` serves the **lite session**
  (`core/voice/lite_client.py`): a small intent responder over the real
  tool registry, so VYUK is useful from the first second instead of
  showing a setup wall. The moment the brain and the Whisper ears
  are both on disk, the lite session hands over to the full local
  brain automatically — that announcement is the handover.
- **Check the progress:** Settings → Brain shows the pack state
  (the same percentages the getting-ready line quotes come from the
  download manager's progress lines); `python doctor.py`'s **Brain**
  and **Offline pack** lines report what's on disk, and its **Lite
  session** line confirms the first-run session itself is healthy.
- **If she stays "getting ready" forever:** the download is stuck,
  not the session — see §18 (interrupted downloads resume from their
  `.part` files; never delete them). If the announcement played but
  answers stayed basic, the full brain's start failed and lite
  resumed with a "hit a snag" note in chat — run `doctor.py` and
  check the Brain line for the model-load error.
- **To skip lite entirely:** let the pack finish downloading once
  (keep the app open on the Assistant page), or switch the brain to
  Gemini in Settings if you'd rather use the cloud brain — the
  local pack is never required for that engine.

### 27. Cloud brain won't connect — Connect keeps failing with a valid key

- **Symptom:** you pasted a real Gemini API key, switched the brain
  to Cloud, and pressing Connect just fails — over and over, with
  only a vague "Problem" pill in the app.
- **The historic cause (fixed in v3.2):** the shipped default voice
  model was `gemini-2.0-flash-exp`. Google retired the whole Gemini
  2.0 Flash line (2.0-flash reached end-of-life in June 2026), so the
  Live API rejected the *model name* at the handshake — every Connect
  failed no matter how good the key was, and the app truncated the
  real error to 40 characters so nobody could see why.
- **What happens now:** `core/voice/live_client.py` connects with the
  configured model first, then — only when Google rejects the model
  itself (a 404 / "not found" / "not supported" answer) — tries the
  other current Live ids (`gemini-live-2.5-flash-native-audio`,
  `gemini-3.8-live`). Whichever model
  answers is **saved as `MODEL_NAME` in `.env`**, so later connects go
  straight to it. A bad key or a network failure does NOT trigger the
  fallback chain — those fail immediately with the real error.
- **Test the key first:** Settings → *How VYUK thinks* → Cloud →
  **Test key**. It makes one small Google metadata call (no voice
  session) and the verdict names the actual problem:
  - *"Key works ✓ — model '…' is available"* — key and model are
    both fine; a Connect failure after this is the live session
    itself, so read the real error (next bullet).
  - *"Key works, but the voice model name is wrong"* — the key is
    good; the configured `MODEL_NAME` isn't a model Google offers.
    Just press Connect: the auto-fallback will find a working model
    and save it.
  - *"That key was rejected by Google"* — the key itself: re-copy it
    from AI Studio (no spaces), or create a fresh one.
  - *"Couldn't reach Google from this PC"* — the request never left
    the machine: check the internet connection / firewall / DNS.
- **Where the real error shows:** hover the status line after a
  failed Connect — the tooltip carries Google's full message (the
  connect path no longer truncates it), the friendly sentence on the
  label translates it, and every attempt is also written to
  `data/activity.log` (lines starting `[Live]`). If all models were
  rejected, the label says the build's model list is out of date and
  the tooltip lists every model VYUK tried plus Google's last answer.
- **The Groq provider (v5.15):** Settings → *How VYUK thinks* →
  Cloud → **Cloud provider: Groq** swaps the cloud brain to Llama
  3.3 70B running on Groq's chips — VYUK keeps her own ears and
  voice on this PC and only sends/receives text, so the laptop GPU
  stays idle. It needs its own free key from **console.groq.com**
  (the Gemini key does not work there), saved in the Groq key field;
  **Test key** makes one small Groq call and the verdict is literal:
  a working key names whether `llama-3.3-70b-versatile` is available
  on it. Connect failures log to `data/activity.log` with a
  `[Groq]` prefix: a 401 line means the key, a 429 line means
  Groq's rate limit (wait a minute), and a network line means the
  request never left the machine. Gemini Live remains the provider
  with native live voice; Groq thinks in text and speaks with
  VYUK's built-in voice.
- **The tool-list cause (fixed in v5.27):** older builds sent
  VYUK's raw tool schemas to Google's Live API, and Google
  refuses some shapes those schemas use — number-only dropdown
  lists (`"enum": [90, 180, 270]`; Google allows word lists only),
  a blank entry in one list, a free-form "any object" field in the
  plan tool, and empty settings blocks on tools that take no
  input. On a current install, Google's library itself rejects
  the list while the app builds it — Connect dies on this PC
  before any network is involved — while **Test key still
  passes**, because it makes a small metadata call that never
  builds the tool list. That split (key tests fine, Connect
  always fails) is the tell-tale sign of this bug. v5.27 translates the tool list into
  Google's accepted subset on its way out (the on-PC enforcement
  of the full schemas is unchanged). If you are on v5.27 or later
  and Connect still fails with a tested-good key, it is a
  different cause — hover the status line for Google's message.
- **The stalling-model cause (fixed in v5.28):** a voice model
  can also fail by simply never answering — one of Google's model
  endpoints stalls while another connects instantly from the
  same PC. Older builds gave up after the first model's 25-second
  stall and blamed the network/proxy. v5.28 tries every model in
  turn before reporting anything; whichever answers is saved as
  `MODEL_NAME`, so the next Connect goes straight to it. If
  Connect takes ~25 seconds and then works, that is this
  mechanism healing itself — the stalled model was skipped.

### 28. Confirmation popups and the failsafe (v4.3)

- **"Allow once / Don't allow" popup:** critical actions — shutting
  down or restarting, emptying the Recycle Bin, uninstalling or
  installing software, closing apps, killing processes, changing
  services, file operations that can delete, and shell commands —
  ask the human in VYUK's window before they run. No answer within
  about a minute counts as "Don't allow"; nothing runs. This is
  deliberate: the popup is the seatbelt, not a bug. Every decision
  is written to `data/activity.log` (lines marked `confirm_gate`).
- **VYUK's hands suddenly stopped / "failsafe has tripped":** a
  watchdog watches VYUK's own heartbeat. If the app hangs, or a
  file named `nova_stop.flag` appears in the `data` folder, the
  watchdog releases any held mouse buttons/keys and stops all
  clicking/typing (cursor and UI Automation tools report the
  failsafe message). It never re-arms itself: open the tray menu
  and choose **Re-arm VYUK's hands (failsafe)** when you're ready.
  Deleting `nova_stop.flag` alone does not re-arm — the tray action
  does.
- **"UI Automation isn't available on this PC yet":** the `uia_*`
  tools (click/type by a control's real name) need Windows and the
  optional `pywinauto` package (`pip install pywinauto`). Without
  them VYUK falls back to her cursor and OCR tools as before.

### 29. Security hardening (v5.3)

- **More actions ask first now:** since v5.3 the popup also covers
  writing or downloading files, setting a program to run at startup,
  pasting text into the focused app, and hotkeys that open a shell
  path (Win+R and friends). The gate also **fails closed**: if the
  confirmation itself can't be shown or checked, the action is
  refused rather than allowed through.
- **"Encoded commands can't be approved":** a shell command that
  hides its payload in base64 (`-EncodedCommand`, `certutil
  -decode`, …) is always refused, even if you'd approve it —
  nobody can approve what nobody can read. Ask for the plain-text
  version of the command instead. For ordinary commands the popup
  shows the **full** command text, untruncated.
- **Why did a harmless action suddenly ask for confirmation?**
  Probably the taint: text from a web page, email, calendar invite
  or the screen is wrapped as *untrusted data* when it reaches
  VYUK's brain, and if it contained instruction-shaped language
  ("ignore your instructions and…"), VYUK treats the rest of that
  turn as compromised — every action that changes something needs
  your OK until you speak or type again. The activity log records
  these as `taint-gated` with the source.
- **"Update check blocked — the feed's signature didn't verify":**
  update manifests are signed with an Ed25519 key whose public half
  is pinned in the app (see `packaging/README.md`, "Signing the
  update feed"). A feed that was tampered with — or replaced —
  fails the signature check, and VYUK trusts nothing it says. A
  checksum alone no longer decides anything.
- **Your conversations never ship:** VYUK's database
  (`data/assistant.db`) is created on your machine and never
  leaves it; release builds are asserted data-free before they are
  packed.

### 30. Connect, warm-ups, and the second press (v5.22)

- **First run asks how she should think (v5.23):** the second
  setup page offers **On this PC** (offline brain, one-time
  download), **Cloud key** (Gemini, nothing to download), or
  **Both** (cloud first, on-PC brain as the automatic safety
  net). Nothing downloads before you pick — the download starts
  only when a choice that includes the on-PC brain is made, and
  skipping setup downloads nothing at all. You can change your
  mind any time in Settings → "How VYUK thinks".
- **"I updated and never saw the three options" (v5.25):** if
  you set VYUK up before the choice existed, the new build
  offers it once shortly after launch. Missed it? Settings →
  "How VYUK thinks" → **Compare all three brain options…**
  reopens it any time.
- **"Are the local AI and my API key colliding?" (v5.26):** no —
  only ONE brain runs at a time, picked by the switch in
  Settings → "How VYUK thinks"; saving a key doesn't move
  that switch. Two things can make it LOOK like a collision:
  (a) the cloud brain fails → she silently swaps to the
  on-device brain as a safety net → if that fails too, v5.26's
  error names BOTH failures in one sentence; (b) her memory of
  your setup lives in two places — the choice lives in your
  Windows settings store, while the key, the switch and the
  downloaded models live in the app folder — so extracting a new
  build into a DIFFERENT folder than before starts that folder
  empty even though setup "remembers" being done.
- **"Her premium voice didn't download" (v5.25):** that's the
  Kokoro voice files (from GitHub) failing — her BRAIN is fine.
  She connects anyway and speaks with the backup voice; the
  download resumes from where it stopped on the next Connect or
  via Settings → Download / resume offline pack. If it keeps
  failing, Settings → Brain → **Diagnose downloads** shows the
  exact error and the detected proxy — that text is what support
  needs.
- **"I saved my API key but she still uses the on-device brain":**
  saving a key never chooses the brain. Settings → "How VYUK
  thinks" decides: **This PC** = the built-in brain (no key
  needed), **Cloud** = your key (Gemini, or Groq if that's the
  selected provider). The key-saved message now says which brain
  Connect will actually use.
- **Connect sits on "Warming up…":** that's the LOCAL brain
  loading. The label names the phase: downloads first (with real
  percentages in the setup banner), then "Loading her ears",
  then "Loading her brain — the first load can take a few
  minutes". A first load of the multi-GB brain genuinely takes
  minutes; later loads are faster. If the brain load never
  finishes, see the graphics-card note below.
- **The button says "Cancel" while connecting:** pressing Connect
  during a start used to launch a SECOND attempt on top of the
  first, which could wedge both. Now a press mid-start cancels
  the attempt cleanly — press Connect again to retry.
- **"Last time the graphics-card load didn't finish":** her
  brain loads on the graphics card first. If a run ends (crash,
  kill, power) in the middle of that load, she remembers
  (`data/llama_load_state.json`) and loads on the processor
  instead next time, saying why. A laptop whose graphics driver
  is unstable is exactly the case this exists for; a successful
  graphics load restores the fast path automatically.
- **Cloud Connect hangs on "Connecting…":** the voice handshake
  now gives up after 25 seconds instead of hanging forever, and
  the error names the usual suspect: the network path. On campus
  / office networks the system proxy is the classic cause — the
  cloud connection now uses the detected system proxy, and
  Settings → Brain → "Diagnose downloads" prints which proxy was
  detected.

— new section after §30

### 31. The key is saved but Connect uses the wrong brain — and where errors live (v5.29)

**Symptom:** you pasted a Gemini key, pressed Connect, and VYUK
still failed — or behaved as if the key wasn't there.

**Why it happens:** saving a key and choosing a brain are two separate
things. The brain choice (Settings → "How VYUK thinks") decides
what Connect runs; a saved key only matters when the cloud brain is
chosen. Since v5.29, saving a key while she's set to think on this PC
asks you outright — **Switch to cloud** or **Keep on this PC** — so the
two can't silently disagree anymore. If you kept her on this PC, Connect
runs the on-device brain and the key simply isn't used yet.

**If Connect still fails**, the exact technical error is no longer
only in a tooltip: open the app folder's `data/activity.log` and look
for the lines starting `[GUI] ERROR status:` and `[Live]` — they hold
the raw error, untruncated, ready to send for diagnosis. The most
common causes, in order: the network path to Google is blocked (try
Diagnose downloads in Settings — it names the detected proxy), the key
belongs to a Google Cloud project without the Gemini API enabled, or
the on-device brain's model files haven't finished downloading
(Settings shows the pack's progress).

**Proxy note:** if your network needs a system proxy, VYUK uses
the detected one for the cloud connect — and since v5.29, if every
voice model fails through it, she retries once without it before
reporting failure. A proxy you set yourself (environment variable) is
never overridden or removed.

### 32. "She didn't know a skill exists" on the on-PC brain (v5.30)

**Symptom:** on the on-PC (offline) brain, she says she can't do
something that the Skills page clearly lists — or she picks a
nearby-but-wrong skill.

**Why it happens:** the on-PC brain reads a shortened tool list that
fits its model's memory. Everyday skills always keep their full
instructions; the rest are named in a compact index as far as room
allows, and any remainder is counted ("…and N more tools") rather
than described. A skill she can only see as a name may get
best-guess arguments on the first try — the reply then shows her
that skill's exact instructions and she retries, which is normal
and self-correcting. Naming the thing you want in your sentence
("read this QR code") promotes exactly that skill into the
detailed list for that turn.

**Nothing is missing:** the Skills page always shows all 345
skills, and the cloud brain receives every skill's full
instructions natively — if a task matters and the on-PC brain
keeps fumbling it, switching to the cloud brain (Settings → "How
VYUK thinks") removes the shortening entirely.


### 33. The v5.31 wave (new everyday tools, upgraded tools, find, trends, settings files)

- **"She says she can't do that" about a CSV, an expense, or a bill
  split.** Those skills arrived in v5.31 — check **Settings → About**
  really says 5.31. On the on-PC brain the new skills are found by
  the same query promotion as every other skill; naming the thing
  ("split this bill", "log an expense") finds them faster than a
  vague request.
- **Weather without the coming hours / search that isn't news.**
  Asking for the forecast "with the next hours" adds the hourly
  block; a news search needs the word "news" — the news results
  come from headlines with their dates, and if that route fails she
  falls back to ordinary web results and says so.
- **The Senses page's "Last 24 hours" charts are empty.** They
  draw from the same environment timeline: the sound chart needs
  sound-watcher events, the brightness chart fills as room
  brightness is measured (it logs at most one reading every
  10 minutes). An empty chart with an honest note is the design,
  not a fault.
- **Find in chat (Ctrl+F) finds nothing.** It searches what was
  actually said — the plain text of the bubbles on the Assistant
  page, including earlier in this session. It does not search
  tool results folded into "Show more", other pages, or past
  sessions.
- **Moving settings to another PC.** Settings → **Your settings
  file** → Export writes a small JSON of choices (theme, voice,
  watchers, sizes). Keys and secrets are never written to it, and
  Import refuses key-shaped entries too — after importing on the
  new PC, paste the API key there by hand. Memory travels
  separately: Memory page → **Export**.
- **The tray menu has Connect and Mute now.** They drive the same
  controls as the window's buttons; if the tray icon is missing
  entirely, the OS tray is hidden — the window buttons are the
  same controls, nothing is lost.

### 34. Connected but silent, an error on Disconnect, and the tool-payload ceiling (v5.32)

Three symptoms, one morning's worth of root causes (all fixed in
v5.32; this section is the field guide if you ever meet their
cousins on an older build):

- **She connects, the pill says she's listening, but she never
  answers — typed or spoken.** Two different bugs wore this
  costume. (a) The session's receive loop read exactly one turn
  and stopped, so replies were generated and never heard. (b) The
  tool declarations sent at connect had grown past the Live
  API's size ceiling: the API *accepts* the connection, then
  kills every turn (close code 1007). Measured live, the cliff
  sits between 250,023 and 268,817 bytes of declarations. The
  fix for (b) is structural: the Gemini copy of the tool list is
  trimmed to a hard 240,000-byte budget (the canonical list the
  other brains use is never trimmed), and since v5.32 the app
  measures before it connects — look for the console line
  `[Live] Pre-flight: tool payload N bytes (budget 240000)`.
  If a future registry ever outgrows the trimmer, Connect
  refuses with an ERROR naming the size and the budget instead
  of connecting into silence; `doctor.py`'s "Tool payload"
  check measures the same numbers without connecting.
- **Disconnect shows "ERROR: Event loop stopped before Future
  completed."** The stop path used to kill the session's event
  loop while the session was still unwinding, so nearly every
  Disconnect of a live session surfaced that line. Stop now asks
  the session to end, lets the loop close itself, and force-
  stops only a worker still wedged after five seconds. A
  related trap: cancelling work from another thread does not by
  itself wake a parked event loop — all session stops go
  through `core/taskwake.py`, which pokes the loop awake. If you
  see the old line on a current build, copy it (with the lines
  around it) from `data/activity.log` — every ERROR lands there
  verbatim — and report it as a new bug, not this one.
- **The first model name gets a 404 / "not supported" and then
  she connects anyway.** That's the per-model fallback working:
  candidates are tried in order, the one that connects is saved
  as the model to try first next time, and the app heals itself
  onto it. Nothing to fix — unless *every* candidate fails, in
  which case the ERROR names each rejection and §31 applies.

### 35. The cloud session only loads core tools — everything else rides the toolbox (v5.33)

By design, not a fault: a Gemini Live session now opens with
the 59 core tools loaded directly (time, volume, media,
windows, files, memory, browser basics — the things asked for
constantly) plus one dispatcher, **use_toolbox**. Every other
tool is still fully available; she reaches it in three steps —
find (a few words of intent), describe (the exact arguments),
call (it runs, with the same confirmation popups a direct
call would need). The activity log shows the shape at connect:
`[Live] Router: offering 59 resident tools`.

- **She says a tool "isn't available" or answers from
  general knowledge instead of using a niche tool.** In a
  routed session the model occasionally forgets the toolbox
  exists. Re-ask once, naming the step ("use your toolbox to
  find the habit tool"); the protocol is also in her system
  instruction, so this is rare and one reminder fixes it.
- **You want the old behaviour — every tool loaded at
  once.** Set `TOOL_ROUTER=0` in the `.env` beside the app
  and reconnect. The full list is trimmed to the Live API's
  size budget exactly as in v5.32 (§34), so sessions are
  somewhat heavier and descriptions somewhat shorter, but
  nothing routes. The doctor's "Tool router" check reports
  both sizes.
- **A toolbox call returns a refusal** ("needs the user's OK
  in VYUK's window"). That is the confirmation gate
  working through the dispatcher — approve it in the window
  as usual; the router never bypasses it (§29).


### 36. "Browser action paused" / OCR not set up / the fallback voice is missing (v5.35)

Three capabilities used to be dead on a fresh install because
nothing provisioned their payloads — OCR answered with a
`pip install` order, the own-browser fallback with
`playwright install chromium`, and the Piper fallback voice
simply never existed (the file it loads was never fetched).
Since v5.35 each one provisions itself:

- **Her own browser (Chromium, ~170 MB)** is downloaded by
  the app into `data/ms-playwright` the first time the
  own-browser fallback is needed and missing
  (`core/browser.py` → `_fetch_bundled_chromium`, driving
  Playwright's own installer). The launch that triggers it
  answers "Browser action paused … fetching it now" — that
  is the system working, not an error; your own Chrome (the
  `launch` action) works in the meantime, and later launches
  use the fetched browser directly. Offline at that moment?
  The fetch fails quietly and retries on the next launch.
- **OCR (Tesseract)** ships inside the installer as a
  portable tree (`assets/ocr/`); `local_assets.tesseract_path()`
  resolves bundled → `data/ocr/` → PATH. "Text reading isn't
  set up" therefore means the install itself is incomplete —
  reinstall; there is nothing to download by hand.
- **The Piper fallback voice** rides the normal voice-stage
  provisioning (bundled, or downloaded with the pack) — see
  the Piper entry in the voice section above.

The doctor reports all three ("Real Chrome (CDP)", "OCR
engine", "Fallback voice (Piper)"): PASS names the resolved
source; WARN means the provision simply hasn't landed yet.

### 37. VYUK can't hear me — she connects, but never responds to my voice (v5.37)

- **Likely files:** `core/voice/audio.py` → `AudioManager.start()` /
  `mic_status()` / `resolve_input_device()`; `gui/main_window.py`
  → `_populate_audio_devices()` / `_mic_watch_tick()`.
- **Why:** one microphone stream feeds everything she hears with
  (the cloud session, the on-device brain's ears, the wake word).
  That stream can open *successfully* and still deliver pure
  silence — Windows is blocking the app, the device itself is
  muted at OS level, or the stream opened a different device
  than the one on your desk. Before v5.37 nothing noticed: the
  session just sat there "listening" to nothing. She also used
  to trust a saved device *index* blindly, and Windows
  re-indexes devices when things are plugged/unplugged — the
  pick silently landed on whatever sat at that number.
- **What she does now:** the saved pick is stored as index +
  name and re-resolved at launch (a moved pick is re-found by
  name; the console prints `[Audio] Input device: …` naming the
  device actually opened). While a session is live she watches
  the stream's levels; ~20 s of total silence puts *"I can't
  hear anything from …"* under the orb. If that caption is what
  brought you here, work down this list:
  1. **Pick the right microphone.** Settings → Voice & sound →
     Microphone. The name in that list is the device she opens —
     if your headset mic, webcam mic and laptop mic all exist,
     the wrong one may be selected. Talk and watch the mic
     meter: it moves only when the *selected* device hears you.
  2. **Windows microphone privacy.** Settings → Privacy &
     security → Microphone: "Microphone access" on, "Let apps
     access your microphone" on, and "Let desktop apps access
     your microphone" on. With any of those off, Windows hands
     the app a perfectly open stream of digital silence — the
     single most common cause of this symptom.
  3. **OS-level mute / volume.** Windows Settings → System →
     Sound → your input device: unmute it and raise the input
     volume. A headset's own hardware mute switch does the same
     thing silently.
  4. **Her gain slider.** Settings → Voice & sound →
     microphone gain raises what she hears *after* the device;
     it fixes a quiet-but-working mic, never a blocked one
     (privacy/mute first, gain after).
  5. **Run the doctor.** `python doctor.py` now has a
     **Microphone** check: it resolves the same device she
     would open, listens for ~1 s, and reports what it heard —
     PASS names the device and its level; a "delivers silence"
     WARN points back at privacy/mute/the wrong device; "very
     quiet" points at the gain. No input device at all is a
     FAIL with the fix named.
- **Fix:** whichever step failed. Nothing here needs reinstalling
  or downloading by hand — it is device selection and OS
  permissions, not missing files.

### 38. "Could not find an application named…" for an app that exists (v5.38)

- **Likely file:** `core/funnel/tools_handlers.py` → `_tool_launch_app` /
  `_start_apps_inventory`.
- **Why:** opening an app is a ladder, and different apps live on
  different rungs. Classic programs are found as files Windows
  can point at. Store apps — Camera is the famous one — are not
  files in that sense: they have no program on the PATH and no
  entry in Windows' App Paths list, so a launcher that only
  knows files walks straight past them. (This is exactly what
  happened with "open camera" before v5.38.)
- **What she does now:** after the file rungs, she asks Windows
  for the Start menu's own app list — the same list the Start
  menu shows you — and matches the name you said against it:
  an exact name wins, then a name that starts with what you
  said, then a name that contains it. A match opens through
  its app link, the way double-clicking its Start tile would.
  If **two** apps match equally well (say, two photo apps for
  "photo"), she opens neither and names both — say the fuller
  name and she'll open the right one.
- **If she still says she can't find it:** the app isn't in the
  Start menu's list either — it may be a portable program that
  was never installed (only extracted somewhere), in which case
  ask her with the full path to the program file instead of its
  name. Nothing to install or download by hand either way.

### 39. She says a name or word wrong — pronunciations (v5.43)

- **Likely file:** `core/voice/local_client.py` →
  `_pronunciation_map()` / `_apply_pronunciations()` (the speech
  text pass in `_speech_text()`); entries come from two places,
  in this order:
  1. **Shipped defaults** — the `_DEFAULT_PRONUNCIATIONS` table
     in `core/voice/local_client.py`: her own name, common tech terms
     (SQL, kHz, Jupyter…), Indian places (Pune, Bengaluru,
     Chennai…) and first names, each a plain-English sounds-like
     respelling checked against the voice's phonemizer.
  2. **Your overrides** — Settings → Voice & sound →
     **Pronunciations — one per line: word = sounds-like** →
     **Save pronunciations**. Your entries are stored as one
     JSON object in the `PRONUNCIATIONS_JSON` line of the
     **`.env` file next to `main.py`** (the package folder's
     `.env`), and they **override or extend** the shipped table
     — same word (any capitalisation) in your list wins.
- **Check:** is the word in your Settings list? A line only
  counts with an `=` and a non-empty sounds-like; the box shows
  only YOUR entries — the shipped defaults are not listed there
  (they're in the code table above). Matching is whole-word and
  case-insensitive, and it applies on the on-device speech path
  only (the spoken text — chat keeps the original spelling).
- **Fix:** add or correct the line (`Pune = poo-nay` style:
  spell it the way it should SOUND, in plain English), press
  **Save pronunciations**, and ask her to say it again. To undo
  a shipped default, add your own line for that word with the
  sounds-like you prefer. If the `.env` JSON is hand-edited into
  invalid JSON, your overrides are ignored (the defaults still
  apply) — re-save from Settings to repair it.
- **Voice blend lives here too:** Settings → *How VYUK thinks* →
  Advanced → **Voice blend partner / Blend amount** mix a second
  Kokoro voice into hers (`KOKORO_BLEND_VOICE` /
  `KOKORO_BLEND_RATIO` in the same `.env`). Blank or 0 = no
  blend. A blend that names a voice not in the downloaded voices
  file is ignored and she speaks with her plain voice; blended
  mixes are cached under `data/voices/kokoro_blends/` and can be
  deleted freely (they regenerate).
