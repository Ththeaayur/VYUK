# VYUK — Commercial Real-Time Voice AI Assistant

A clean-room, production-grade personal AI assistant built from the ground up for commercial distribution, digital independence, and ultra-low-latency interaction.

---

## 🌟 Commercial Features

- **Bi-Directional Streaming Audio**: Real-time conversation powered by Google Gemini Live API (`gemini-3.8-live`). Zero turn latency, full interruption support.
- **Premium Calm UI (P0 redesign)**: High-DPI PySide6 interface with a breathing gradient orb, chat-bubble conversation, quiet sidebar navigation, and one hero mic button. State is shown by motion, not neon.
- **Phase 2 premium**: Frameless window with ambient orb glow, one SVG icon family, live waveform dock, home briefing strip, tool result cards, 4 accent themes + 3 orb styles (Sphere/Ring/Wave), designed earcon sounds, first-run onboarding, and settings rebuilt as cards. Choices persist between launches.
- **Command Deck (Phases A–G, working tree, unshipped)**: Token-driven cyan Command Deck chrome across every page and dialog, the S1 diamond mark as the window/installer icon, bundled Orbitron + Sora + Share Tech Mono typography (B-R1-7, body face Sora since B-R1-7r), a first run with real boot checks, coach marks, and Command Deck dialogs, and a regenerated install manifest. Home stays the Phase E home, as settled by the owner. A clean install on fresh Windows 10/11 and the filmed marketing capture pass are still owed on real Windows.
- **Phase 3 "Alive" UI**: Orb v3 with drifting particles and fresnel rim, command palette (Ctrl+K), thinking-dots bubble, message timestamps, suggestion chips, audio-reactive mic glow, launch fade-in.
- **Hands + Eyes control**: Type into any app, hotkeys, mouse, shell commands, window and file management, close apps, media keys, brightness, power actions, screenshots, active-window awareness and OCR — behind a trust layer (destructive actions need explicit confirmation, deletes go to `_nova_trash`, every tool call is written to `data/activity.log`). See `core/desktop/control.py`.
- **Debuggable by design**: `ARCHITECTURE.md` maps every file, `TROUBLESHOOTING.md` goes symptom → file → fix, and `python doctor.py` checks the whole install and names the broken piece.
- **Orb v4 "Reactor"** (historical — deleted 2026-11-01): An instrument dial — three segmented rings rotating against each other, a 60-tick gauge with sweeping needle, a 48-bar EQ halo driven by live audio, a hot particle core, and CPU/RAM vitals arcs. Thinking spins the rings up; speaking drives the halo. Since 2026-11-01 Aura is the only orb style (owner decision "keep only the aura"); Reactor, Apex, Sphere, Ring, Wave, Prism, Halo and Eclipse were deleted, and saved non-Aura styles migrate to Aura on load.
- **Mind pack**: Multi-step plans (`create_plan` and friends, tracked in `data/plans.json`) and saved routines — named tool chains VYUK can run on command. Routines pass through the same trust layer: a step that needs confirmation halts the routine instead of being auto-approved.
- **Control Complete**: Click buttons by name (UI Automation) or by visible text (OCR), Wi-Fi and network status, wallpaper and dark mode, virtual desktops and window snapping, process management, battery, zip/unzip, webcam snapshots, downloads. See `core/desktop/control_plus.py`.
- **Pro cursor control**: One `cursor_control` tool with true DPI-aware coordinates (no offset clicks on scaled displays), smooth curved ease-in-out glides instead of teleports, a pulsing beacon ring where VYUK is pointing, and a cooperative failsafe — move the mouse yourself mid-glide and VYUK aborts instantly. See `core/desktop/cursor.py`.
- **Closing the gaps (v5.2)**: four assistant gaps closed. A real wake word — an offline Vosk spotter listens for "Hey VYUK" with a restricted grammar (it proved deaf to soundalike phrases, chatter, claps, and even "I love supernova"); the model downloads with the voice pack. Episodic memory — VYUK now keeps a day journal (session summaries land automatically) and can answer "what did we do yesterday?" with the new Recall a day tool. Google can act, not just look: Send email and Create calendar event join the toolkit, each behind the in-app approve popup, and a pre-v5.2 Google connection gets an honest "reconnect to grant sending" message instead of a failure. And updates are real: Settings → About checks a version feed, downloads are checksum-verified and staged, and installation happens safely at next launch with automatic rollback — your data folder is never touched.
- **Premium voice (v5.1)**: her mouth grew up. A speech stylist now prepares everything before it's spoken — markdown is never read aloud, ₹1,250 becomes "one thousand two hundred fifty rupees", 48% becomes "forty-eight percent", links become their domain, code blocks are never spelled out — while the chat still shows the exact original text. Sentences join with crossfades instead of clicks, every chunk is loudness-matched so her volume stops jumping mid-reply, and interrupting her fades her out over 90 ms instead of cutting her off mid-word. Settings gains a curated voice roster (Heart, Bella, Nicole, Sarah, Emma, Isabella) with ▶ Preview clips and a voice-pace slider (80–120%).
- **Premium (v5.0)**: the feel tier. A new orb style, **Apex** — dark glass, fine 120-tick gauge, spring physics, state changes that ease instead of cutting (historical — Apex deleted 2026-11-01; Aura is now the only orb style). Every surface now runs on one design-token stylesheet: cards lift with a lit top edge, inputs sit as recessed wells, one focus language everywhere. Pages crossfade and rise instead of hard-cutting, chat bubbles ease in (history stays instant), and startup opens with a brief ignition splash. Sound design grows to a nine-cue synthesized family — connect, disconnect, setup-ready, screen-capture and watcher-alert join wake/sent/done/error — and the floating mini-orb now speaks the same Aura glass language.
- **Every tool, refined (v4.7)**: the refinement programme closes. Software installs are exact-only — ask for "chrome" and VYUK shows you the candidates and installs the one you meant, with the resolved package named in the confirmation; update checks parse winget's tables structurally, so non-English Windows can no longer produce a false "up to date" (an unreadable answer is reported as unreadable); power-plan and battery readings went locale-proof the same way; and image tools honour EXIF orientation, so phone photos resize and convert the way they actually display. The browser and UI Automation tools finish their honesty pass: partial page reads and capped control trees say so, dropdown errors distinguish "not a dropdown" from "option missing — here are the options", and multi-match typing discloses its choice.
- **A mind that keeps its story straight (v4.6)**: routines now halt honestly when a step is refused instead of reporting it done; one damaged plan or routine record is skipped with a note instead of breaking every plan tool; plan steps are numbered the same way everywhere; reminders can't be set in the past and survive a faulty callback; watcher notices (battery, network, drives) are only suppressed if they were actually delivered. Her learning is pickier, too: quoted speech and negations no longer become false "facts" about you ("my sister said my name is Priya" teaches her nothing), anything instruction-shaped you ask her to remember is stored as *something you said* — never as an instruction to her — and Google errors now say which problem you actually have (not connected, sign-in expired, libraries missing, or a temporary Google failure).
- **No false facts (v4.5)**: the data-truthfulness pass. `read_file` pages long files honestly ("chars 0–3000 of 54,321 — ask for offset 3000 to continue") and recognises binary files instead of returning garbage; `web_search` returns the answer it actually found; memory recall can't be dumped with wildcard queries; Gmail counts are labelled estimates and calendar reads merge all your calendars (or say plainly when they couldn't). Everywhere a tool used to stop silently — file searches, folder sizes, duplicate scans, batch renames, pastes — it now states its limits with real numbers ("250 matched; planning the first 200"), so VYUK never presents a partial result as the whole truth.
- **Trust, fixed (v4.4)**: the first refinement pass — every tool audited, 28 major defects found and the safety batch fixed. App launching can no longer be turned into a shell-injection channel; the dangerous-click gate in the browser judges the *actual button resolved*, not the words requested, and asks the human; the password-field rule now holds on **every** typing path (browser keys and UI Automation included); nothing silently overwrites — file writes, moves, zips and unzips announce or refuse collisions; downloads verify their byte counts before calling themselves complete; the old mouse/keyboard tools honour the same failsafe and screen fence as the pro cursor; a stuck browser session recovers instead of wedging; and power actions check whether they actually happened.
- **Hardened (v4.3)**: VYUK's hands got seatbelts. **Every tool call is schema-checked** before it runs — a malformed action (wrong types, missing fields) is rejected with the exact error, fed back to her brain for up to **2 self-repairs**, then safely given up on instead of looping. **Critical actions ask the human**: shutting down, uninstalling, emptying the Recycle Bin, killing processes, arbitrary shell commands and six more now pop a native **Allow once / Don't allow** dialog — no answer in 60 s means *no*. A **failsafe watchdog** watches her own heartbeat: if the app hangs, or you drop a `nova_stop.flag` file in her data folder, all held keys and mouse buttons are released and her hands stay stopped until you re-arm them from the tray menu. The cursor can no longer be sent off-screen (every target is fenced to the real monitors, negative-origin second screens included) and Windows moves/clicks go through the native **SendInput** API. And she gained **UI Automation** hands — find, click and type into controls by their real names (`uia_*` tools, 126 skills at v4.3 — a historical count, not the current total) instead of guessing pixels.
- **In a box, running light, verified (v4.0–v4.2)**: VYUK can ship as a Windows installer with the AI **bundled inside** — no first-run download at all — and adapts to the machine: on PCs with ~8 GB of RAM she loads a lighter brain, a smaller context and unloads sooner, and **Ctrl+Shift+Space** summons her from any app. Brain downloads (when they do happen) are now **SHA-256 verified** against the publisher's hash before they're accepted — a corrupt download is rejected and removed, never half-trusted — and the first-run setup page rotates **feature cards** while you wait, so the download time teaches you what she can do.
- **The web, in 3D + downloads that fight back (v3.3)**: the Memory Web gains a **3D mode** (top-right 2D|3D switch) — real OpenGL spheres with depth, orbit by dragging, scroll to move closer, a gentle auto-rotation when idle, and automatic fallback to 2D on machines without working OpenGL. In 2D, labels no longer overlap: a collision planner draws them by priority with soft halos and holds the rest back until you zoom or hover. Downloads hardened for real-world networks: they now go **through the PC's system proxy** (the one browsers use — campus networks need it), **fall back to a mirror** if Hugging Face can't be reached (resuming mid-file, even across the switch), report the *actual* error once, persist it across restarts, and back off for 10 minutes after a failure instead of hammering. If a network defeats even that, TROUBLESHOOTING §18 documents the manual escape hatch: download the brain file in a browser and drop it into `data/models/` — VYUK adopts it automatically.
- **First run, fixed (v3.2)**: Setup now starts **when the app opens**, not when you press Connect — with a slim banner naming what's downloading ("her brain · 1.2 GB of 2.5 GB") and a first-run setup page with live stages, so nobody guesses what's installing. Progress is *truthful* now: percentages come from real bytes per stage, capped at 99% until she's genuinely ready, and her "still getting ready" answers say what's actually happening. **Cloud fix**: the old default voice model had been retired by Google (every Connect failed, whatever the key) — the default is current, Connect **self-heals** across Google's current models and remembers the one that worked, errors are shown in full, and a **Test key** button validates a key + model in one click. **Lighter**: brain size auto-picks by RAM, context capped, and the brain + ears **unload after 10 idle minutes**. **Skills page** is now a full searchable catalogue of her 120 built-in skills.
- **The Memory Web (v3.1)**: the Memory page grows a second view — an Obsidian-style graph of everything VYUK remembers. You sit at the centre; each category (Identity, Preferences, what she's Learned, Notes…) is a coloured hub; every fact orbits its hub. Facts that mention the same thing — a city, a hobby, a drink — grow faint threads between them across categories, and clicking any node shows the fact, the date she learned it, and *why* it's linked ("both mention 'mumbai'"). The web settles itself live with a small physics simulation, supports pan/zoom/drag-to-pin, folds hubs on double-click, dims around whatever the search matches, and forgets facts from the same detail panel. The List view remains, untouched, one click away.
- **Perfection pass (v3.0)**: VYUK now ships her own typography — **Inter** (the interface font behind Linear-class products, open licence) in four weights, bundled in the app, so she looks identical on every PC instead of inheriting whatever font the system happens to have. A formal design system sits underneath: one spacing rhythm, one radius scale, one type scale, unified gradients, real focus rings, and calmer scrollbars across every page and all four accent themes. The floating mini orb got a small-size redesign — a calm gradient sphere with a single orbiting dot instead of the old dial that read like a clock at 92 px.
- **Settings, calmly (v2.9)**: Settings was rebuilt around one idea — *choices up front, machinery behind a door*. A one-line status header ("Thinking on this PC · offline pack ready"), a single two-option card **How VYUK thinks** (On this PC / Cloud, with only the relevant follow-up shown — the offline pack *or* the key field), and every technical control (brain backends, model sizes, Ollama, speech files) moved untouched into one collapsed **Advanced** card. Notices got their own card; Voice, microphone and sounds were merged into one. First-run setup dropped its brain-choice page entirely — there's nothing to choose — and the cloud key is now honestly optional. Nothing was removed: every control from every previous version is present, re-homed, and verified.
- **Absolute control (v2.8)**: 47 more skills in three domain packs — **122 tools total**. *System & apps*: install/update software (winget), reversible startup-program control, Windows services, power plans, battery health (design vs current capacity + wear), environment variables, read-only registry queries, disk health, temp cleanup, largest files, duplicate finder. *Files & media*: image convert/resize/compress, OCR for files, file hashes, recent files, region/window screenshots, batch rename with dry-runs, shortcuts, folder sizes, audio recording, audio-device listing, microphone mute, per-app volume. *Network & display*: full Wi-Fi management (join new networks — the password never appears in any result), public IP, a real speed test, ping/DNS, Bluetooth devices, per-monitor resolution and refresh rates, always-on-top windows, window opacity, mouse speed, paste-anywhere. Destructive actions stay confirm-gated; the only remaining walls are Windows' own — the admin secure desktop, password fields, and CAPTCHAs.
- **Zero setup + skills (v2.7)**: A fresh install now needs **no decisions at all**. The built-in brain is the default; its pack downloads itself in the background — ears first, then voice, then mind, so she's useful progressively. While it downloads, a **lite mode** answers instantly (time, weather, system, your day, your mail, opening apps, remembering things) in text and voice, and the moment the full brain lands it takes over *mid-conversation* — "My full brain is ready — I'm all here now." The local brain also gained **screen reading** (shared screens are OCR'd and read to her) and **streaming speech** (she starts talking after the first sentence, not after the whole reply). The cloud brain is now the opt-in, under Advanced. Plus **9 new skills** (75 tools): installed apps, uninstall (confirm-gated), printing, PDF reading, read-aloud, drive eject, focus assist, deep file search, and monitor control.
- **She learns you (v2.6)**: As you talk, VYUK quietly picks up durable facts about you — your name, what you like and dislike, your habits, the people in your life, things you ask her to remember (`core/memory/learning.py`). It's a careful pattern engine, not a guess: questions, moods and throwaway remarks are ignored, corrections replace older facts, and multiple likes coexist. Nothing is learned silently — each new fact earns a quiet "Noted — I'll remember…" line in chat, the Memory page keeps a full "Learned about you" ledger with one-click Forget (and forget-everything), and a master switch turns learning off completely. Her own system prompts are never learned from. Learned facts join the same memory her brain reads, so she simply knows them next time — instantly on the local brain, from the next connect on the cloud brain.
- **She feels alive (v2.5)**: Continuity, three ways. **Auto-reconnect** — if the connection drops mid-conversation, VYUK quietly retries on her own (2s, 5s, 15s, 30s) and announces "Back online — reconnected automatically."; real errors (a bad key, a missing local brain) never loop, and you can turn it off in Settings → Presence. **Conversation memory** — both voice engines already log every exchange, and now the window restores your last 30 messages on launch under an "Earlier today / Yesterday" divider; the Memory page can clear that history with a two-tap confirm. **Calendar heads-ups** — connected to Google, she watches the next two hours and mentions anything starting within 15 minutes, once, through the same polite Notices system (its own toggle included).
- **She knows your world (v2.4)**: Connect a Google account (Settings → Your world) and VYUK reads your **Google Calendar and Gmail — read-only**, she can look but never send or delete. Four new tools (66 total): today's schedule, upcoming events, unread email, and mail search, all returned as clean speakable lines. Her morning briefing automatically includes your day and the mail that matters once you're connected. Sign-in uses Google's official desktop OAuth in your browser; the token lives only on this device (`data/google_token.json`). You bring your own Google Cloud client codes for now (the card walks you through the five-minute setup) — a verified built-in client is the commercial-launch path.
- **She notices things (v2.3)**: A quiet watcher engine (`core/watchers.py`) keeps an eye on the machine and mentions only what matters — battery at 20% (and firmly at 10%), a download that has actually finished (it waits until the file stops growing), going offline and coming back, a nearly-full drive, a newly connected drive, and a "welcome back" when you return from a long break. Each thing is said once, in plain words; when VYUK is connected she mentions it out loud in her own voice (politely throttled, critical battery excepted), otherwise it lands as a chat line. Every sense has its own switch in Settings → Presence → Notices.
- **Plain & premium (v2.2)**: Everything a user reads is written for a person, not an engineer. Connect errors become one calm sentence with a next step ("Her offline brain isn't running — press Download in Settings, or switch to Cloud"); downloads read "Downloading her brain — 43%", never a filename; result cards carry human titles ("System status", "Web click") for all 480 tools (the v2.2-era count, not the current total). The technical truth isn't deleted — it lives in tooltips, logs and `doctor.py`, one hover away. The Brain card folds its tinkery controls behind a single "Advanced options" line, so a first-timer sees three choices and a button.
- **Presence (v2.1)**: VYUK lives on your desktop. A floating mini orb (always on top, draggable, dimmed when she's off) mirrors her state — click it to open the window. She can launch with Windows (quietly, in the tray with the mini orb), connect on launch, and never runs twice (single-instance lock). Routines can be scheduled — 'daily 07:30', 'interval 2h', 'once 2026-10-25 08:00' — set by voice when saving a routine or per-row in the Reminders page; they fire on time whether she's connected (she announces and runs them) or not (headless, with a report in chat), and the trust layer still applies. With the morning briefing enabled, she speaks first at your chosen time.
- **Two brains, tiny start (v1.7; zero-setup v1.8; smart downloads v2.0)**: The app starts **instantly on the cloud brain** — *Gemini Live* (best voice, needs an API key), no download at all. The *Local* brain is an optional **Offline Pack**: pick Local and Connect (or press "Set up local brain now") and the pack downloads **in the background** while you keep using VYUK. Three brain sizes, each quoted as the whole pack (brain + Whisper ears ≈0.5 GB + Kokoro voice ≈0.34 GB): **Lite** (Qwen3-1.7B) ≈1.9 GB total, **Balanced** (Qwen3-4B-Instruct, Apache-2.0 — the default) ≈3.3 GB total, **Quality** (Qwen3-8B) ≈6 GB total. Downloads **resume where they left off** if the app closes or the network drops — never back to zero — and can be paused from Settings. Settings → Brain shows the space the pack occupies, with a one-click **Remove offline pack** to reclaim it. Once downloaded, the local brain thinks in-process via llama.cpp, hears you with Whisper, and speaks with Kokoro — entirely on your PC, no key, no internet. An **Ollama** backend remains for tinkerers who run their own model server (bring your own models; optional Piper voice files go in `data/voices/`). Model, voice and backend are all swappable in Settings. The window talks to a brain proxy (`core/brain/brain.py`), so both engines share one identical session interface. Component licences: see `THIRD_PARTY_LICENSES.md`.
- **Refinement build (v1.6)**: a finish pass over everything — painted animated thinking dots, bubbles and result cards with real depth (gradients, hairline borders, per-category tool icons), a fuzzy command palette with icons and hints, home quick-action pills (Open Chrome / Screenshot / System status / Lock), a live briefing line under the orb (date · time · CPU · RAM · battery), an orb whose EQ bars flow like a real analyser and whose dial adapts its detail to its size, plus core tightening (tool results capped before reaching the model, battery + uptime in system status, more app aliases, browser reads that prefer main content, routine step timings).
- **Full web control**: Six browser tools (Playwright) — VYUK attaches to your real Chrome over its debug port (your logins, your tabs). She never downloads or launches a browser of her own: since v5.70 there is no bundled browser, and browser tools are real-Chrome-only. She reads pages as structure (text, links, a field/button inventory), clicks and types by label, fills forms, manages tabs. She never types passwords, payment clicks need your explicit confirmation, and CAPTCHAs stay yours. 640 tools in total as of v5.92.0. See `core/browser.py`. (Tool count history, each measured on the shipped tree by importing the registry — `core/funnel/tools_registry.py`'s `ToolRegistry` singleton — and counting `len(registry.get_declarations())`: 478 at v5.50, including the 2 shipped plugin tools; 480 at v5.52 after items 4 + 8 added play_music and app_settings; 640 at v5.92.0. Earlier editions of this README said 62; that was the v2.2-era registry. Every count above is stamped with its version, so a future count that goes stale reads as stale instead of silently passing for current.) One extra install: `pip install playwright` (uses your installed Chrome — no browser download needed).
- **Hands-Free Wake Word Detection**: Toggleable voice activation ("Hey VYUK" — the Vosk spotter's grammar carries the phonetic pair "via oak" / "hey via oak", because "vyuk" itself is out-of-vocabulary for the model) with local acoustic envelope cadence spotting and speech recognition integration.
- **Proactive Scheduler & Reminders**: Set timed reminders by voice or UI, and receive automated **Daily Morning Briefings** with weather, news highlights, and memory notes.
- **Drop-In Plugin Architecture**: Extend VYUK with custom skills by dropping standard Python files into `plugins/`. Gemini automatically receives function calling declarations. Comes with built-in stock market quotes and live crypto trackers.
- **Global Hotkey Daemon**: System-wide global hotkeys that work from any active desktop application:
  - `Ctrl+Shift+N`: Toggle Push-to-Talk (PTT)
  - `Ctrl+Shift+S`: Transmit screen capture to VYUK
  - `Ctrl+Shift+M`: Toggle microphone mute
- **Live Web Search & Weather**: Built-in DuckDuckGo instant answer search and global Open-Meteo weather querying with automatic geolocation.
- **System Clipboard Monitor**: Watches the clipboard in the background and prompts VYUK to summarize, translate, or explain copied text.
- **Automated Session Summaries**: Generates concise multi-bullet summaries upon session disconnect and logs them to the long-term databank.
- **Persistent Long-Term Memory**: Local SQLite database storing user facts, habits, and preferences, automatically synthesized into live session context.
- **Visual Intelligence**: Screen grab and webcam frame extraction compressed into high-speed JPEG inputs for the Gemini Live session.
- **Licensing, stated honestly (v5.50, item 44)**: VYUK's own code is **proprietary** — licensed under `EULA.txt` (see `LICENSE`), not MIT or any other open-source licence. Most third-party components are permissive (Apache-2.0 / MIT / BSD), but **not all**: PySide6 and pynput are **LGPL**, pyttsx3 is **MPL-2.0**, and the Kokoro voice's phoneme engine (espeak-ng, bundled via espeakng-loader, a kokoro-onnx dependency) is **GPL-3.0**. The full per-component disclosure, with the compliance steps taken for each copyleft component, is in `THIRD_PARTY_LICENSES.md` — read it before commercial distribution.

---

## 🛠️ Installation & Setup

### 1. Requirements
- Windows 10/11 — VYUK is Windows-only in practice: her desktop control depth (window management, UI Automation, SendInput, startup/registry and the rest of the hands) is built on Windows, so the full assistant runs on Windows.
- Python 3.10+ (verified on Python 3.14)
- Google Gemini API key ([Get one from Google AI Studio](https://aistudio.google.com/))
- OCR engine: OCR and click-by-text features are powered by a Tesseract OCR engine, bundled/staged with the app — VYUK resolves it from the bundled assets first, then a provisioned copy under `data/ocr`, then a Tesseract on the system PATH.

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Key
Create a `.env` file from the example:
```bash
copy .env.example .env
```
Add your key inside `.env`:
```ini
GEMINI_API_KEY=your_actual_key_here
```
*(Alternatively, enter your API key directly in the app's `// CONFIG` tab on first launch).*

### 4. Run Assistant
```bash
python main.py
```

---

## 📁 Clean-Room Architecture

Post-Phase-3 layout (v6.0.0; 654 tools in the registry — see `core/funnel/tools_registry.py`).

```
voice_assistant/   # NOTE: folder must be named voice_assistant (underscores) for imports to work
├── core/
│   ├── resilience/ (11 .py files)   # Offline + reconnect resilience (outbox, durability, power)
│   ├── health/ (7 .py files)        # Install health, integrity checks, updater
│   ├── desktop/ (58 .py files)      # Hands: windows, cursor, UI Automation, browser, menus, monitors
│   ├── memory/ (16 .py files)       # Learning, memory stores, playbook, session summaries
│   ├── brain/ (18 .py files)        # Planning, task coordination, goals, teach
│   ├── voice/ (21 .py files)        # Voice engines (Live, local, Lite, cloud text) + audio
│   ├── funnel/ (38 .py files)       # Tool registry, gates, handler packs, toolbox
│   ├── __init__.py
│   ├── config.py                    # App configuration & .env loader
│   ├── scheduler.py                 # Proactive reminders & morning briefing engine
│   ├── plugin_loader.py             # Dynamic plugin discovery system
│   ├── wallpaper.py                 # Wallpaper resolution + apply (user's own art only)
│   └── flat modules: 315 .py files in total, including 60 skills_* skill modules
├── gui/ (83 .py files)              # Flat: main window + window_* pages, orb, chat, settings
│   ├── main_window.py               # Premium desktop GUI (sidebar + orb + chat) & background bridges
│   ├── window_memory.py             # Memory + journal wiring (incl. journal compensate/reconcile buttons)
│   ├── window_session.py            # Session window (mute button + icon bundle)
│   ├── window_settings_voice.py     # Voice settings (bridge toggle, text-brain attach)
│   ├── window_settings_think.py     # Think settings (How VYUK thinks card)
│   ├── visualizer.py                # Breathing gradient orb (state shown by motion)
│   ├── style.py                     # Design tokens + the global QSS stylesheet
│   ├── icons.py                     # One SVG line-icon family (make_icon)
│   ├── sounds.py                    # Earcon sounds (wake/sent/done/error), fully guarded
│   ├── onboarding.py                # First-run onboarding dialog (API key → mic test → voice)
│   ├── design_system/ (21 .py files) # Design-system tokens + decisions
│   └── assets/                      # Bundled icons + earcons
├── plugins/                         # Drop-in custom Python skills
│   ├── stock_ticker.py              # Live stock market quote plugin
│   ├── crypto_price.py              # Cryptocurrency pricing plugin
│   ├── plugins_hashes.json
│   └── plugins_hashes.json.sig
├── data/                            # Runtime data (created automatically)
│   ├── assistant.db
│   ├── activity.log
│   ├── download_state.json
│   ├── games.json
│   └── voices/
├── tests/ (226 .py files)           # Unit + journey suites (incl. the G2 classification snapshot)
├── installer/                       # Windows installer sources (ISS + spec + wizard art)
├── assets/                          # Bundled staged assets
│   └── ocr/                         # Staged Tesseract OCR engine
├── .env.example
├── requirements.txt
├── requirements.lock
├── ARCHITECTURE.md                  # File map, data flows & landmines — read before editing
├── TROUBLESHOOTING.md               # Symptom → file → fix guide
├── THIRD_PARTY_LICENSES.md          # Per-component licence disclosure
├── LICENCE_CUSTODY.md               # Licence custody notes
├── THREAT_MODEL.md                  # Threat model
├── TC1_RUN_GOAL_CHECKPOINT_SPEC.md  # TC1 run-goal checkpoint spec
├── LICENSE                          # Proprietary licence (end users: see EULA.txt)
├── EULA.txt                         # End-user licence
├── install_manifest.json            # Signed install manifest
├── install_manifest.json.sig        # Manifest signature
├── mypy.ini
├── mypy_baseline.txt
├── ruff.toml
├── doctor.py                        # Stdlib-only health check: python doctor.py
└── main.py                          # Main launcher
```

---

## 🔌 Creating Custom Plugins

Creating a skill for VYUK is as simple as creating a single Python file in `plugins/`:

```python
# plugins/roll_dice.py
import random

PLUGIN = {
    "name": "roll_dice",
    "description": "Rolls an N-sided die.",
    "parameters": {
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "Number of sides (default 6)"}
        },
        "required": []
    }
}

def run(sides: int = 6) -> str:
    return f"Rolled a {random.randint(1, sides)} on a d{sides}!"
```

VYUK will automatically discover and register this tool for Gemini Live on next launch!

---

---

## 🧭 Editing or Debugging This Code? (added 2026-10-21)

Start here before changing anything — these three files exist so any
editor can find an issue and fix it without reverse-engineering the
whole project:

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — what VYUK is, a map of every
  file (what it owns, what it must never do, what breaks if it's wrong),
  the three data flows (voice loop, tool calls, GUI state), a
  "where do I change X?" index with exact file + function names, and the
  project's invariants/landmines.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — symptom → likely file →
  how to check → fix, for the failures this project has actually hit
  (wrong folder name, stale UI, invisible bubbles, silent earcons,
  tool errors, Windows-only fallbacks, and more).
- **`doctor.py`** — run `python doctor.py` from inside this folder.
  A stdlib-only health check (layout, syntax, dependencies, imports,
  tool registry, API key, data folder, assets) that prints a `fix:`
  hint naming the file for every failure.

---

## 📜 Legal & Commercial Compliance

This project was built clean-room from the ground up:
- **Zero code, assets, or 3D meshes** from non-commercial CC BY-NC projects were used.
- VYUK's own code is **proprietary**: it is licensed to end users under `EULA.txt` (see `LICENSE`) — it is **not** released under the MIT License or any other open-source licence. (Earlier editions of this README claimed MIT; that contradicted the shipped `LICENSE` and was corrected in v5.50, item 44.)
- Third-party components remain under their own licences, and they are **not** all permissive: PySide6 and pynput are LGPL, pyttsx3 is MPL-2.0, and espeak-ng (bundled via espeakng-loader for the Kokoro voice) is GPL-3.0. See `THIRD_PARTY_LICENSES.md` for the full per-component list and the compliance steps taken for each.

---

## 🏷️ A note on names — the naming keep-list (v5.50, item 44)

The product is **VYUK** (renamed from its two earlier product
names in v5.15/v5.36; the v5.50 packaging-truth sweep finished
the rename in code comments and docs). The identifiers below
deliberately KEEP
their pre-rename spelling — they are plumbing, not product naming,
and renaming them would orphan existing installs' settings, files
and habits (the v5.36 rename contract). Do not "fix" these:

- `nova_stop.flag` — the failsafe drop-file name in `data/`
  (`core/failsafe.py`); users and docs already know this filename.
- `NOVA_APP_ROOT` — the app-root override env var
  (`core/updater.py`); likewise `NOVA_START_MINIMIZED`,
  `NOVA_ASSETS_DIR` and the other `NOVA_*` env names.
- `nova.*` logger names (`nova.tools`, `nova.browser`,
  `nova.failsafe`, `nova.confirm_gate`, `nova.session_epoch`,
  `nova.audit`, …) — log-channel identifiers, not branding.
- `QSettings("NovaAI", "Nova")` and the Windows auto-start Run
  value `"Nova"` (`core/presence.py`) — renaming them would lose
  every existing install's settings and auto-start entry.
- Data folders and file prefixes on the user's own machine:
  `~/Pictures/Nova`, `~/Music/Nova`, `nova_*.png` / `nova_*.wav`
  filename prefixes, the `_nova_trash` folder and the
  `Nova_instance.lock` single-instance lock file.
- Outbound identifiers other software already sees: the
  `User-Agent: Nova` / `NovaVoiceAI/1.0` header values and the
  `voice_assistant` package name.
- Lowercase `visor-*` / `visor_*` deterministic seed salts, thread
  names, probe sentinels and file prefixes inside the skills
  (e.g. `visor-meals|…` seeds, `visor_env_backup_…` files) —
  internal constants whose values are load-bearing (a changed
  salt changes generated output). The plain English word "visor"
  used as an example (e.g. `nato_spell(word='visor')`) is not the
  old product mark and also stays.
