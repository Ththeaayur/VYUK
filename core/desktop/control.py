"""
Hands + Eyes control pack for VYUK (assistant-style laptop control).

Plain functions, each returning a human-readable string. Every
platform-specific dependency (ctypes.windll, pyperclip,
pytesseract, psutil, vision) is imported lazily inside the function
that needs it and guarded with try/except, so this module imports
and compiles cleanly on any OS — including headless Linux, where
the GUI/input calls simply return a clear fallback string.
"""

import contextlib
import itertools
import locale
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)  # refactor P1 step 4 (control_base concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)










# ---------------------------------------------------------------------------
# Assistant-gesture guard (v5.42, H14)
#
# Generalises the cursor glide's takeover abort (core/desktop/cursor.py's
# _follow_path: before each step the ACTUAL cursor position must
# still match the last planned one — a deviation over 60 px means
# the human grabbed the mouse). While one of this module's synthetic
# input actions runs inside the guard, a watcher polls the real
# cursor position; a deviation over the same 60 px from the last
# position VYUK herself set (or the position captured when the
# gesture began) is genuine USER movement, and it trips the failsafe
# with "user input during assistant gesture" — which also releases
# any held input and blocks every further dispatch until a human
# re-arms.
#
# KEYBOARD HALF — NOT IMPLEMENTED, honestly: the global key
# listener receives VYUK's own synthetic keystrokes indistinguishably
# from the user's (there is no source tag on either OS path this app
# uses), so a keyboard watcher inside a typing gesture would trip on
# VYUK herself. The mouse half + this scaffolding are what this pass
# ships; the guard API is input-agnostic so a future detector with a
# real synthetic/genuine discriminator can plug into the same trip.
#
# Fail-safe throughout: every seam is guarded, the position reader
# and the trip function are injectable (the suite fakes both), and a
# broken guard must never break the wrapped action.
# ---------------------------------------------------------------------------

from .control_gesture import (_GESTURE_TAKEOVER_PX, _GESTURE_POLL_S, _GESTURE_REASON, _gesture_lock, _gesture_depth, _gesture_expected, _gesture_watcher, _gesture_position_reader, _gesture_trip_fn, _read_gesture_mouse_pos, _trip_for_gesture, gesture_active, _note_synthetic_mouse_pos, check_gesture_takeover, _gesture_watch_loop, _begin_assistant_gesture, _end_assistant_gesture, assistant_gesture, _foreground_title, _foreground_elevated, _foreground_is_shell, _note_input_pressed, _note_input_released, _expected_lock, _expected_foreground, _foreground_identity_fn, _foreground_identity, note_expected_foreground, get_expected_foreground, clear_expected_foreground, set_expected_foreground, _foreground_label, _foreground_matches, _expectation_refusal, _last_input_tick, _tick_is_newer)  # refactor P1 step 4 (control_gesture concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)

# Injectable seams (tests fake these; production leaves them None).




















# ---------------------------------------------------------------------------
# HANDS — keyboard
# ---------------------------------------------------------------------------



from .control_input import (_TYPE_BATCH_CHARS, _password_block, type_text, _KEY_ALIASES, _VK_MAP, _VK_CHAR_MAP, press_hotkey, mouse_control)  # refactor P1 step 4 (control_input concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)






# ---------------------------------------------------------------------------
# Failsafe pressed-input registry — local note helpers (v5.48,
# programme item 26). Press sites in this module register what
# they hold with core/failsafe so a trip releases exactly that
# (see failsafe.py's registry section). Guarded exactly like
# _input_blocked: a broken failsafe must never break the hands.
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Focus discipline (v5.48, programme item 22 — A13)
#
# The hands used to type into WHATEVER window happened to be
# foreground when the keystrokes landed: focus_window put the
# target in front, but nothing ever checked that it was STILL
# in front when type_text / press_hotkey fired — a user (or an
# app) moving focus in between silently redirected the input.
# The expectation record closes that: a focus entry point
# records the foreground identity it just established
# (note_expected_foreground), and type_text / press_hotkey
# compare the CURRENT foreground against it before the first
# keystroke, refusing — and naming the window that IS focused
# — on a mismatch. No expectation recorded -> the pre-v5.48
# behaviour, byte for byte.
#
# Identity + match granularity (the choice the brief asked to
# be documented): an identity is {hwnd, pid, title, proc} —
# everything capturable from this module's existing seams
# (_foreground_title + a ctypes hwnd/pid probe here, the
# process name via core/input_guard). Comparison uses the
# STRONGEST stable identity available on BOTH sides, in order
# hwnd -> pid -> title: a window handle names one exact
# window and survives the title edits a document makes as it
# is typed into (title equality would false-refuse the moment
# an editor appends " — modified" or renames the file), a pid
# survives a same-app window switch only in the sense that it
# cannot distinguish two windows of one app (weaker, but it is
# what is comparable when handles can't be read), and the
# title is the last-resort fallback. When NOTHING is
# comparable (e.g. both identities captured off-Windows, every
# field None) there is no proof of a mismatch and typing
# proceeds — the expectation only ever vetoes on positive
# evidence that focus moved.
#
# Focus entry points wired to note_expected_foreground: this
# module's window_control(action="focus") on its verified-
# success return (below). Entry points in files this item does
# not own — skills_window_control.focus_window_by_text, uia.py's
# uia_type_text set_focus path, tools_handlers.py's _tool_window_control
# wrapper (which delegates to window_control, so it inherits
# this wiring transitively) — are listed in the item report
# for the coordinator.
# ---------------------------------------------------------------------------























# v5.46 (item 7): type_text injects in batches of this many
# characters, re-checking the failsafe + foreground-shell state
# before each batch (focus can move mid-type; the executor gate
# only saw the foreground at execute() time).




# Common key name -> canonical key name (see _KEY_ALIASES)

# Fallback VK codes for the ctypes path (Windows only)

# Single punctuation characters -> VK codes for the ctypes fallback
# (H17). ord(char) equals the VK code for letters and digits ONLY:
# '-' is 0x2D, which is VK_INSERT, not VK_OEM_MINUS (0xBD) — the
# fallback used to press Insert when asked for '-'. Characters that
# are neither alphanumerics nor in this map are REFUSED on the
# fallback path instead of guessed.




# ---------------------------------------------------------------------------
# HANDS — mouse
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# HANDS — shell commands (with trust layer)
# ---------------------------------------------------------------------------

# Refused outright, even with confirm=true: disk formatting / wiping.
# v4.4: the PowerShell surface is covered too — Format-Volume IS a
# format, and it used to sail through both layers while `format D:`
# was refused, contradicting this module's own stated policy
# (audit_hands MAJOR).
from .control_shell import (_ALWAYS_REFUSE_PATTERNS, _ENCODED_PAYLOAD_PATTERNS, _CONFIRM_REQUIRED_PATTERNS, _CONFIRM_REQUIRED_RAW_PATTERNS, _SHUTDOWN_ABORT, _ALIAS_EXPANSIONS, _QUOTE_CONCAT, _normalize_command, _mask_quoted, _ECHO_HEAD, _mask_echo_args, _code_view, _split_flag_destructive, _DOWNLOAD_VERB, _TEMP_HINT, _START_PROCESS, _download_then_start, _confirm_required, _REFUSE_FORMAT_MSG, _REFUSE_ENCODED_MSG, _trust_decision, _decode_output, _live_procs, _live_procs_lock, _kill_listener_registered, _proc_running, _register_live_proc, _unregister_live_proc, _ensure_kill_on_trip_registered, kill_all_live_processes, _kill_process_tree, _spawn_shell, run_command, _JOB_LIMIT, _JOB_BUFFER_LIMIT, _jobs_lock, _jobs, _jobs_order, _job_seq, _CommandJob, _job_reader, _get_job, start_background_command, _job_state_line, poll_background_job, wait_background_job, cancel_background_job, list_background_jobs)  # refactor P1 step 4 (control_shell concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)

# Refused outright, even with confirm=true (v5.3, H1): encoded
# payloads. A command whose real instructions are wrapped in base64
# or a PowerShell -EncodedCommand blob cannot be meaningfully
# approved by ANYONE — not the model, not the human in the confirm
# popup — because nobody can read what would actually run. This is
# the same tier as disk formatting: not gate-approvable, ever. Ask
# for the plain-text equivalent instead.

# Blocked unless confirm=true.

# Confirm-required patterns matched against the NORMALIZED command
# with quoted contents still visible (v5.45, H9/H21d) — these classes
# are ABOUT the text inside the arguments (a script path, a URL, a
# pipe), so the quote-masking used for the verb list above would
# blind them.

# `shutdown /a` ABORTS a pending shutdown/restart — it is the
# recovery command power_control's own result tells the user to
# run, so it must never be gated (v4.4; audit_hands). Matched
# strictly: the abort flag and nothing else.


# ------------------------------------------------------------------
# Trust-layer normalization (v5.45, H9) — pattern matching never runs
# on the raw command string any more. Attackers hide verbs behind
# shell quoting tricks ("pow"+"ershell", 'pow''ershell', backtick
# and caret escapes); the screen first rewrites the command into the
# shape the shell will actually execute, THEN matches. Execution
# always uses the ORIGINAL string — normalization feeds the pattern
# lists only, and there is exactly one set of lists (above).
# ------------------------------------------------------------------

# Alias expansion for screening: PowerShell aliases resolve to the
# cmdlet the pattern lists name. Expansion is screening-only (the
# command that runs is untouched), so a Linux `curl`/`wget` binary
# being read as its PowerShell namesake here costs nothing — no
# pattern fires on a bare download verb alone.

# Two quoted chunks joined by "+" or sitting adjacent: "a"+"b",
# 'a'+'b', "a" "b", 'a''b' (the doubled quote is one string in both
# cmd-family and PowerShell quoting).
























# ------------------------------------------------------------------
# Command runtime (v5.45, H21) — one spawn/kill/decode path for the
# synchronous runner and background jobs alike.
# ------------------------------------------------------------------



# ------------------------------------------------------------------
# Live-process registry (v5.46, programme item 5, A3) — every
# process _spawn_shell creates (the single spawn point for the
# synchronous runner AND background jobs) is registered here, so
# a failsafe trip can kill work that is actually running, not
# just refuse new work. Dead entries are pruned on each new
# registration and skipped by the kill; a process unregisters
# when the sync runner's communicate() finishes and when a
# background job's reader finishes, so the set stays honest.
#
# Registration point for the kill-on-trip wiring (the choice the
# A3 brief asked to be stated): the failsafe listener is
# registered LAZILY, on the first _spawn_shell, through a guarded
# import — NOT at this module's import time. core/failsafe
# imports only stdlib and cannot cycle, but a lazy registration
# cannot fail an import of control in any context (headless,
# partial package, a test importing control standalone), and
# before the first spawn there is by definition nothing to kill.
# ------------------------------------------------------------------



















# ------------------------------------------------------------------
# Managed background commands (v5.45, H3) — run_command's background
# mode. A long command (a build, a download, a server) launches as
# a job and returns its id at once instead of blocking the caller
# for the 30 s synchronous cap. The trust layer runs ONCE, at
# launch, before the id exists: a refused or unconfirmed command
# never becomes a job. Jobs live in a thread-safe registry capped
# at _JOB_LIMIT (the oldest FINISHED job is evicted to make room; a
# registry full of running jobs refuses new launches rather than
# evicting live work). Each job keeps its exit code and a bounded
# output buffer after it finishes. Polling is incremental (output
# since the last poll); waiting returns the final tail + exit code.
# Cancellation kills the job's whole process tree (H21a).
# ------------------------------------------------------------------






















# ---------------------------------------------------------------------------
# HANDS — windows
# ---------------------------------------------------------------------------

from .control_window import (_enum_windows, _window_title, virtual_desktop, window_control)  # refactor P1 step 4 (control_window concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)








# ---------------------------------------------------------------------------
# HANDS — files
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Central trash (v5.48, programme item 27, A17-residual)
#
# Before v5.48, every rescue/delete scattered a sibling
# ``_nova_trash`` folder next to the victim (v5.43, H7): recovery
# storage was smeared across the whole filesystem, nothing ever
# cleaned it up (purge_nova_trash existed but had NO caller), and
# finding a given file again meant remembering which folder it
# died in. Now ALL new rescues and deletes land in ONE central
# root (_trash_root) under timestamped unique names, each recorded
# in trash_manifest.json there, and restore_from_trash /
# purge_central_trash work against that root. Legacy per-folder
# ``_nova_trash`` folders are still honoured by restore and by the
# unchanged purge_nova_trash, so pre-v5.48 rescues stay
# recoverable — they are simply never created any more.
#
# Automatic purge (item 27d): wired into the rescue/delete path
# itself — every _move_to_central_trash call cheaply stats the
# root's .last_purge stamp and, when it is more than 24 h old,
# runs purge_central_trash() and restamps. No new threads, no
# scheduler, and no watchers.py wiring: the delete/rescue path is
# the clean seam (it is the only place trash is created, so it is
# also the one place a purge is guaranteed to be reached while
# trash exists). The purge therefore runs BEFORE the new entry is
# added, so a fresh rescue can never be purged by its own call.
# ---------------------------------------------------------------------------

from .control_files import (_TRASH_MANIFEST_NAME, _TRASH_STAMP_NAME, _TRASH_LOCK, _trash_root, _read_trash_manifest, _write_trash_manifest, _append_trash_manifest, _trash_entry_size, _move_to_central_trash, _maybe_daily_purge, purge_central_trash, restore_from_trash, _rescue_overwrite_victim, purge_nova_trash, file_control)  # refactor P1 step 4 (control_files concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)


























# ---------------------------------------------------------------------------
# HANDS — apps / media / brightness / power / clipboard / open
# ---------------------------------------------------------------------------

from .control_system import (_termination_outcome, close_application, _MEDIA_VK, media_control, set_brightness, power_control, write_clipboard, open_path, _capture_screen_bytes, take_screenshot, get_active_window, OCR_UPSCALE, _OCR_BINARISE_THRESHOLD, _capture_native_image, _prepare_ocr_image, _ocr_lang_config, _ocr_image_string, _ocr_image_words, _ocr_captures, ocr_words_native, ocr_screen, get_activity_log)  # refactor P1 step 4 (control_system concern, moved verbatim; D4a: hub now in core/desktop/, .desktop segment dropped)
















# ---------------------------------------------------------------------------
# EYES — screenshot / active window / OCR
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# OCR with real pixels (v5.44, E2 + E7) — shared by ocr_screen below,
# control_plus.click_text and skills_window_control's word boxes.
#
# The pre-E2 pipeline OCR'd the 1024-px JPEG thumbnail that
# _capture_screen_bytes returns (the brain-facing capture), with
# pytesseract defaults — no config, English only — and click-by-text
# then scaled the thumbnail's word boxes back up by ratio, so small
# text failed and clicks landed quantised. Now every OCR path:
#   (a) captures a DEDICATED full-resolution frame via
#       vision.capture_native_image (all_screens-aware grab, the
#       foreground window's rectangle by default — E7);
#   (b) upscales it x2 with a light binarisation pass before OCR;
#   (c) calls Tesseract with the explicit config from
#       local_assets (--psm 6 --oem 1, plus the resolved
#       --tessdata-dir);
#   (d) picks the language from the user's preferred_language via
#       local_assets.resolve_ocr_language (hi -> hin(+eng); a
#       missing hin degrades to eng and the note says so);
#   (e) maps word boxes back from the prepared image by the EXACT
#       upscale factor plus the crop origin — native-resolution
#       coordinates, never a thumbnail ratio.
# And when the foreground crop's OCR yield is empty, the caller
# falls back to a full-screen capture — decided HERE, where the
# yield is known, not by capturing twice blindly (E7).
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# TRUST / ACTIVITY LOG — reader (writer lives in tools.ToolRegistry.execute)
# ---------------------------------------------------------------------------

