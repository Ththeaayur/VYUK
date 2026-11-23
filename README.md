> **Proprietary — not open source.** VYUK is the commercial product of Aayur Gattani. This repository is published for reference only: you may read the code, but you may not copy, modify, distribute, or reuse any part of it. See LICENSE and EULA.txt.

# VYUK — Stop clicking. Start talking.

**Virtual, yet Universally Knowledgeable.** One voice that runs your whole computer — apps, browser, files, clicks. No API key to fetch, no VYUK account, nothing to learn.

In active development. Public beta coming soon. Windows-only.

## What it does

- **Control** — your whole desktop by voice. Open apps, drive your real Chrome, click anywhere on screen, type, manage windows and files.
- **Remember** — it learns what you teach it and keeps it on your machine: facts, a day journal, and your documents, linked in a knowledge graph.
- **Speak** — nine languages at native grade. It answers in the language you speak: English (US + UK), Hindi, Japanese, Mandarin, Spanish, French, Italian, Portuguese.
- **Work for you** — email and calendar through Google, multi-step plans, routines, reminders, and a morning briefing.
- **Ask first** — sending, deleting, and spending wait for your yes. Every tool call is written to a tamper-proof ledger.

770+ tools. One voice.

## Run it

**Requirements:** Windows 10/11, Python 3.10+.

```bash
pip install -r requirements.txt
python main.py
```

On first run, sign in with Google (Continue with Google). That's it — no key to paste, no account to create.

VYUK can think on your PC (offline pack, picked in Settings) or in the cloud. Your memory stays on your device either way.

## Editing this code?

Start with these three — they exist so anyone can find an issue and fix it without reverse-engineering the whole project:

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — what VYUK is, a map of every file, the three data flows, and the project's invariants.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — symptom → likely file → how to check → fix.
- **`doctor.py`** — run `python doctor.py`. A stdlib-only health check that prints a `fix:` hint naming the file for every failure.

## Legal

VYUK's own code is **proprietary**, licensed to end users under `EULA.txt` (see `LICENSE`) — not MIT or any other open-source licence. Third-party components keep their own licences, and they are **not** all permissive: PySide6 and pynput are LGPL, pyttsx3 is MPL-2.0, and espeak-ng (bundled via espeakng-loader for the Kokoro voice) is GPL-3.0. The full per-component disclosure is in `THIRD_PARTY_LICENSES.md` — read it before commercial distribution.

## Links

- YouTube: https://www.youtube.com/@VYUKAssistant
- LinkedIn: https://www.linkedin.com/company/vyuk/

---

<details>
<summary>A note on names — the naming keep-list (for editors)</summary>

The product is **VYUK** (renamed from its two earlier product names in v5.15/v5.36; the v5.50 packaging-truth sweep finished the rename in code comments and docs). The identifiers below deliberately KEEP their pre-rename spelling — they are plumbing, not product naming, and renaming them would orphan existing installs' settings, files and habits (the v5.36 rename contract). Do not "fix" these:

- `nova_stop.flag` — the failsafe drop-file name in `data/` (`core/failsafe.py`); users and docs already know this filename.
- `NOVA_APP_ROOT` — the app-root override env var (`core/updater.py`); likewise `NOVA_START_MINIMIZED`, `NOVA_ASSETS_DIR` and the other `NOVA_*` env names.
- `nova.*` logger names (`nova.tools`, `nova.browser`, `nova.failsafe`, `nova.confirm_gate`, `nova.session_epoch`, `nova.audit`, …) — log-channel identifiers, not branding.
- `QSettings("NovaAI", "Nova")` and the Windows auto-start Run value `"Nova"` (`core/presence.py`) — renaming them would lose every existing install's settings and auto-start entry.
- Data folders and file prefixes on the user's own machine: `~/Pictures/Nova`, `~/Music/Nova`, `nova_*.png` / `nova_*.wav` filename prefixes, the `_nova_trash` folder and the `Nova_instance.lock` single-instance lock file.
- Outbound identifiers other software already sees: the `User-Agent: Nova` / `NovaVoiceAI/1.0` header values and the `voice_assistant` package name.
- Lowercase `visor-*` / `visor_*` deterministic seed salts, thread names, probe sentinels and file prefixes inside the skills (e.g. `visor-meals|…` seeds, `visor_env_backup_…` files) — internal constants whose values are load-bearing (a changed salt changes generated output). The plain English word "visor" used as an example (e.g. `nato_spell(word='visor')`) is not the old product mark and also stays.

</details>
