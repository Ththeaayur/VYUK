"""PF-18 durable permission grants (build batch 1: store + mic binding).

The six PF-18 permission ids (design ~/workspace/pf18_grants_model_design.md
§5, owner rulings D1–D11 2026-11-04) and the store that holds the user's
standing choice for each. This module is the ONLY writer of durable grants.

Store discipline (D6, mirrors core/confirm_gate_allowlist.py exactly):
  * File: data/permission_grants.json — SEPARATE from
    data/confirm_allowlist.json. The two files hold opposite semantics
    (remove-capability vs skip-ask) and must never share a file.
  * Writes are atomic (mkstemp-unique temp + os.replace). A failed write
    reports False and never pretends it stuck.
  * Envelope: {"version": 1, "grants": {id: record}, "changes": [event]}.
    One atomic write persists a flip AND its ledger event together, so a
    stored state can never disagree with its event history.
  * One record per id: {state, source, updated_at, scope_note}.

Read discipline (D2 via D6 — the mic/camera carve-out, read carefully):
  * Default state for every id is UNKNOWN.
  * On a corrupt / missing / unreadable store, MICROPHONE and CAMERA fail
    CLOSED — they read "denied" until explicitly re-enabled in Settings.
    There is NO ask-again trap for these two ids: the design's §5.2
    "unknown, ask afresh" does NOT apply to them; the ruling overrides it.
  * Other ids read "unknown" on a corrupt / missing store (their
    fail-closed behaviour is NOT this batch's decision — see get_state).
  * CONSEQUENCE, stated plainly: on a fresh install (no store file yet)
    microphone reads denied, so mic tools refuse through the funnel and
    the live-voice mic manager refuses to open, until the user flips
    microphone on in Settings. That is the ruled behaviour, not a bug;
    the first-use mic ask (mic_first_use_ask_needed + the GUI dialog,
    first-use mic ask batch 2026-11-05) handles the first-run grant at
    the first human Connect — a session-only "Allow once", never a
    durable flip.

Who may write (D8 — Settings only):
  * set_permission_state() writes granted/denied ONLY when via="settings".
    Any other via value returns False and writes nothing. Voice, plans,
    session flows and Allow-once can never flip a durable grant; only the
    Settings GUI path passes via="settings". Allow-once is a per-action
    confirm answer, never a durable grant (D3).

Mic teardown (D2): flipping microphone to denied runs, in order,
  1. capture_guard.revoke_capture_approval("microphone") — clears the
     session's mic approval, so no ask-once approval survives the flip;
  2. every registered mic-teardown hook (live capture sessions stop
     themselves through these; the sidecar and the voice manager register
     their stops when the GUI wires them — see the widget batch).
  Teardown hooks are guarded: a failing hook never fails the flip and
  never blocks the other hooks; failures are logged, not swallowed
  silently. The gate reads the store, so a written denial refuses even
  if a hook misbehaves — but a misbehaving hook is a real hole and is
  logged as one.

Mic binding (this batch ONLY — the other five ids are unbound, empty
sets, until their batches):
  * Funnel tools that take audio INPUT (verified against the live
    registry, not copied from the design doc): mic_check,
    room_noise_level, voice_note, listen_for_sound, record_audio,
    mic_control, set_microphone_mute.
  * Deliberately NOT bound (not audio input): list_audio_devices
    (enumeration, not capture), speaker_check / speak_text (output),
    audio_fade / audio_trim / audio_speed_change (file ops, not mic),
    passive_voice_check (a text heuristic), *_record_* bookkeeping
    tools (record-keeping, not audio recording), camera/screen tools
    (their own ids' batches).

This module imports stdlib only, so it can never cycle with the funnel,
the voice chain, or the ledger. Callers import it lazily where the
codebase already does so (funnel stages, voice manager).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PERMISSION_IDS = (
    "microphone",
    "camera",
    "screen",
    "files",
    "browser",
    "home_devices",
)

# D2 carve-out: these ids fail CLOSED on a corrupt / missing store.
FAIL_CLOSED_IDS = frozenset({"microphone", "camera"})

# Mic-domain funnel tools: audio INPUT only (see module docstring).
MIC_TOOL_NAMES = frozenset({
    "mic_check",
    "room_noise_level",
    "voice_note",
    "listen_for_sound",
    "record_audio",
    "mic_control",
    "set_microphone_mute",
})

# Camera-domain funnel tools (PF-18 batch 2): every tool that opens
# the webcam device (D2: camera covers the webcam device).
# - webcam_snapshot: one frame saved as JPEG (control_plus).
# - describe_camera_view: one frame summarised on-device (senses).
# - screen_brightness_auto: one frame taken to measure room light
#   before setting screen brightness — its EFFECT is screen, but it
#   OPENS the webcam, so it is camera capture and binds here.
CAMERA_TOOL_NAMES = frozenset({
    "webcam_snapshot",
    "describe_camera_view",
    "screen_brightness_auto",
})

# Screen-domain funnel tools: every tool that opens screen frames.
# screen_diff captures on both actions (save_baseline grabs, compare
# grabs a fresh frame to diff); occluded_capture is a capture attempt
# (its handler's own per-session approval still applies when the id
# is granted/unknown — the funnel refusal only fires on denial).
SCREEN_TOOL_NAMES = frozenset({
    "take_screenshot",
    "screen_look",
    "ocr_screen",
    "screenshot_region",
    "capture_window",
    "screen_diff",
    "occluded_capture",
})

# Browser-domain funnel tools: everything that drives a browser —
# VYUK's controllable browser (browser_control/read/click/type/
# select/press) plus open_url, which opens a page in the
# system/default browser. Deliberately NOT bound: site_permissions_
# review (a browser-SITE permissions report, read-only reporting,
# not driving) and web_search (API-first: DuckDuckGo instant-answer
# API / Google News RSS; a browser tab opens only as a failure
# fallback — see the batch-5 report for that disclosed residual).
BROWSER_TOOL_NAMES = frozenset({
    "browser_control",
    "browser_read",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_press",
    "open_url",
})

# File-domain funnel tools: purpose is operating on USER files
# (read, write, manage, inspect, transform existing files) — the
# file is the OBJECT of the tool. PF-18 batch 8 supersedes batch 7's
# "delivery vehicle" exclusion for writers: a denied files grant
# that still lets a writer create a user-visible file is a hole, so
# generative writers (create_art, generate_melody, generate_beat)
# and record / report writers (every *_record_* / *_report handler
# that writes its caller-chosen output_path, the creative subtitle /
# vCard exports, the rights ledger tools, the study workspace +
# export / session tools) ARE here, with their output paths judged
# by folder scope. Still NOT here: tools that write no user-visible
# file — text generators (generate_palette / password / uuid),
# text transforms (creative_subtitle_edit, creative_timeline),
# text-in parsers (vcard_import), internal named stores
# (create_plan, study_review's schedule store, teach sequences),
# internal bookkeeping (journal_action's funnel receipt store,
# extension_register's harness + undo record), execution surfaces
# (extension_register, sandbox_run — own containment),
# disk_speed_test (benchmark; scratch file deleted always),
# wallpaper_state (query-only), no-path readers (focus_timer /
# presence / startup_time reports read live state, not files).
# Capture tools keep their own ids (one id per tool):
# record_audio stays mic, take_screenshot/webcam_snapshot/
# screenshot_region/capture_window stay screen/camera, browser
# tools stay browser. run_command stays unbound (execution surface,
# not file access); registry_read is a Windows-registry path, not a
# filesystem path; menu/context/system-surface action paths are UI
# paths, not filesystem paths; save_snippet/routine/layout/plan and
# teach sequences are internal named stores with no path args.
# Download tools ARE here: download_file / download_email_attachment
# materialise external content as a local file — the file is the
# point, not a vehicle. clean_temp_files / recent_files take no path
# arg (temp-only deletion / recent-list read) — bound so denial
# refuses them, with nothing for folder scope to judge.
FILES_TOOL_NAMES = frozenset({
    # Content readers (mirror _FILE_READ_PATH_ARGS in
    # core/funnel/tools_gate_paths.py, plus the listing tools the
    # protected read check deliberately excludes — listing a folder
    # outside the allowlist reveals file names, so scope judges
    # listing roots too).
    "read_file", "read_pdf_text", "ocr_file", "tail_file",
    "csv_preview", "csv_query", "csv_column_stats",
    "compare_files", "image_compare", "file_hash", "sniff_file",
    "pdf_info", "image_info", "color_from_image", "qr_decode_image",
    "screenshot_to_text_note", "json_query", "json_diff",
    "extract_from_text", "search_file_contents", "folder_diff",
    "find_duplicates", "duplicate_photo_finder",
    "acronym_finder", "case_convert", "filler_word_count",
    "passive_voice_check", "reading_time", "sentence_stats",
    "text_stats", "word_frequency",
    # Manage / list / inspect.
    "file_control", "open_path", "find_files", "largest_files",
    "folder_size", "folder_outline", "folder_size_map",
    "file_age_report", "folder_watch_cook", "batch_rename",
    "print_file", "copy_file_to_clipboard", "shred_file",
    "clean_temp_files", "recent_files", "file_dialog_action",
    "drag_file_onto", "env_backup", "continuous_delete_data",
    # Writers / transformers (input + output judged by scope).
    "write_file", "replace_in_file", "download_file",
    "download_email_attachment", "create_shortcut", "zip_control",
    "sync_folder", "convert_image", "resize_image",
    "compress_image", "crop_image", "rotate_image",
    "adjust_image", "filter_image", "watermark_image",
    "add_border", "make_collage", "kaleidoscope_image",
    "duotone_image", "pixelate_image", "glitch_image",
    "strip_image_metadata", "merge_pdfs", "rotate_pdf",
    "split_pdf", "pdf_extract_images", "pdf_merge_bookmarks",
    "pdf_split_by_text", "make_sprite_sheet", "batch_watermark",
    "audio_trim", "audio_speed_change", "audio_fade",
    "silence_trim", "photo_face_blur", "creative_mark_image",
    "display_control", "set_wallpaper", "wallpaper_apply",
    "image_contact_sheet",
    # Voice-control batch (2026-11-16, C3): three file-writing
    # tools that already had scope entries but were never bound
    # to files — a denied Files grant let them write, and the
    # folder-scope stage early-returned before judging their
    # paths (PF-18 writer-map failure). Bound now; their scope
    # entries (tools_gate_paths._FOLDER_SCOPE_PATH_ARGS) were
    # already correct, so no mapping change was needed.
    "audio_to_midi", "organise_photos", "sync_folders_two_way",
    # PF-18 batch 8: generative writers — the output path is
    # judged; an omitted output passes scope per the nothing-to-
    # judge rule and lands in the handler's fixed default
    # (~/Pictures/Nova for art, ~/Music/Nova for melody/beat),
    # named in the result message.
    "create_art", "generate_melody", "generate_beat",
    # PF-18 batch 8: record / report writers — every handler that
    # writes its caller-chosen output_path.
    "access_record_preference", "antic_cards_report",
    "comms_record_draft", "continuous_record_choice",
    "feel_report", "game_record_gaming", "hardware_record_plan",
    "improvement_report", "iot_record_device", "learnlast_report",
    "multiverse_compile_report", "net_record_network",
    "phase5_report", "pilot_record_pilot",
    "release_checklist_report", "sim_predict_report",
    "well_record_plan", "creative_subtitle_export", "vcard_export",
    "study_export_deck", "study_session",
    # PF-18 batch 8: rights ledger tools — ledger_path judged
    # when given; omitted ('') passes scope and lands in the
    # internal data/rights_ledger.db default.
    "creative_rights_add", "creative_rights_check",
    # PF-18 batch 8: study workspace tools — workspace_dir is the
    # folder the handler writes its JSON record into.
    "study_exam_prep", "study_plan", "study_progress",
})

# Binding table, batch 9: microphone + camera + screen + browser +
# files (now 122 tools) are bound. home_devices maps to an empty set
# still — no device tool exists yet (D10 honest-empty) — but the
# check_home_device hook below + the generic funnel stage mean a
# future bound tool refuses without further funnel work.
PERMISSION_TOOL_MAP = {
    "microphone": MIC_TOOL_NAMES,
    "camera": CAMERA_TOOL_NAMES,
    "screen": SCREEN_TOOL_NAMES,
    "files": FILES_TOOL_NAMES,
    "browser": BROWSER_TOOL_NAMES,
    "home_devices": frozenset(),
}

_TOOL_TO_PERMISSION = {
    tool: pid
    for pid, tools in PERMISSION_TOOL_MAP.items()
    for tool in tools
}

_SCOPE_NOTES = {
    "microphone": "All audio input: mic tools, sound-event sidecar, live-voice mic.",
    "camera": "Webcam capture tools: webcam_snapshot, ip_camera, describe_camera_view, screen_brightness_auto.",
    "screen": "Screen-frame tools: take_screenshot, screen_look, ocr_screen, screenshot_region, capture_window, screen_diff, occluded_capture; denying screen also turns the screen-watch switch off.",
    "files": "File tools (122): read/write/manage user files, scoped by the D9 folder allowlist — denied refuses all file tools; granted refuses paths outside the allowed folders; unknown asks per call (Choice B, D3). (PF-18 batch 8 corrects batch 7's note, which wrongly said unknown also refuses out-of-list paths — the Choice B test proves unknown passes scope.)",
    "browser": "Browser-driving tools: browser_control/read/click/type/select/press + open_url; denying browser also closes VYUK's browser session.",
    "home_devices": "Home-device tools (none exist yet — D10 honest-empty; future device tools route through check_home_device + the funnel map).",
}

_GRANTS_FILE = "permission_grants.json"

# Test seam (same class as capture_guard.set_probe): tests point the
# store at a temp dir so the real data dir is never touched. Production
# code never sets this.
_PATH_OVERRIDE = None


def set_path_override(path) -> None:
    """Point the store at `path` (tests only)."""
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = pathlib.Path(path) if path is not None else None


def clear_path_override() -> None:
    """Restore the production store path (tests only)."""
    global _PATH_OVERRIDE
    _PATH_OVERRIDE = None


def _grants_path() -> pathlib.Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / _GRANTS_FILE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blank_record(pid: str) -> dict:
    return {
        "permission_id": pid,
        "state": "unknown",
        "source": "user",
        "updated_at": _now_iso(),
        "scope_note": _SCOPE_NOTES.get(pid, ""),
    }


def _file_problem(path: pathlib.Path) -> str | None:
    """Why the file on disk is not a usable grants store, or None.

    None means healthy-or-missing: a missing file is a healthy
    empty store (first run), a well-formed envelope is a healthy
    store. A parse failure, a wrong top-level shape, or an
    unreadable file is a problem string. Read-only: never writes,
    never notes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"unreadable ({exc})"
    except ValueError as exc:
        return f"not valid JSON ({exc})"
    if not isinstance(payload, dict):
        return "not a JSON object"
    grants = payload.get("grants")
    changes = payload.get("changes")
    if not isinstance(grants, dict) or not isinstance(changes, list):
        return "grants/changes have the wrong shape"
    return None


# Corrupt-STORE disclosure + quarantine (round-3 C3-F3). _read_store
# used to return None silently for missing AND corrupt alike, so a
# corrupt file read as "no grants" with no signal anywhere, and the
# next set_permission_state silently replaced it. The shape is the
# C3-F2 memory-DB precedent: disclose (log line + sticky state +
# a plain-words notice), quarantine before any re-write, never
# delete the corrupt bytes. Fail-closed is untouched: a corrupt
# store still reads denied (mic/camera) or unknown (the rest).
_CORRUPTION: dict | None = None
_CORRUPTION_LOGGED: bool = False


def _note_corruption(problem: str) -> None:
    """Record a corrupt grants file (first detection only).

    The log line names the file and the problem; the sticky
    record is what corruption_state()/corruption_notice() read.
    A later healthy read does not clear it — the file on disk
    is still corrupt until it is quarantined or replaced."""
    global _CORRUPTION, _CORRUPTION_LOGGED
    if _CORRUPTION is None:
        _CORRUPTION = {
            "path": str(_grants_path()),
            "error": problem,
            "detected_at": _now_iso(),
        }
    if not _CORRUPTION_LOGGED:
        _CORRUPTION_LOGGED = True
        log.warning("permission grants store %s looks damaged (%s); "
                    "permissions read as their safe defaults until "
                    "repaired", _grants_path(), problem)


def corruption_state() -> dict | None:
    """A copy of the corruption record, or None.

    Probes the file when nothing is recorded yet, so a caller
    that never read the store still gets a live answer. A
    missing or healthy file reads None (healthy empty)."""
    if _CORRUPTION is None:
        _read_store()
    return dict(_CORRUPTION) if _CORRUPTION is not None else None


def corruption_notice() -> str:
    """Plain-words notice for a corrupt grants file, or "".

    Names the file and the choice (set it aside and start
    fresh, or leave it). A healthy or missing file has no
    notice. The read side stays fail-closed either way —
    microphone and camera read denied until set again."""
    record = corruption_state()
    if record is None:
        return ""
    name = pathlib.Path(record["path"]).name
    return (f"Your permission settings file ({name}) looks damaged "
            f"and could not be read ({record['error']}), so your "
            f"permissions are showing their safe defaults — "
            f"microphone and camera denied until you set them again. "
            f"Nothing was deleted: the damaged file is kept as-is. "
            f"Set it aside and start fresh?")


def reset_corruption_state() -> None:
    """Clear the sticky corruption record + once-per-process log
    flag. Tests only: production never forgets a corrupt file."""
    global _CORRUPTION, _CORRUPTION_LOGGED
    _CORRUPTION = None
    _CORRUPTION_LOGGED = False


def quarantine_corrupt_store() -> str | None:
    """Move a corrupt grants file aside, keeping its exact bytes.

    The name is `<name>.corrupt-<YYYYMMDD-HHMMSS>` next to the
    file (counter suffix on a same-second repeat), the C3-F2
    shape. Returns the quarantine name, or None when there is
    nothing corrupt to set aside or the rename itself fails. The
    store then reads as a healthy empty one (first-run state)."""
    global _CORRUPTION, _CORRUPTION_LOGGED
    record = corruption_state()
    if record is None:
        return None
    path = pathlib.Path(record["path"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 2
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        os.replace(path, target)
    except OSError as exc:
        log.warning("could not set aside damaged grants store %s "
                    "(%s)", path, exc)
        return None
    _CORRUPTION = None
    _CORRUPTION_LOGGED = False
    log.warning("damaged grants store %s set aside as %s",
                path, target)
    return target.name


def _read_store() -> tuple[dict, list] | None:
    """(grants, changes) or None when the store is missing/corrupt.

    Never raises: any read failure is None, and get_state() turns
    None into denied (fail-closed ids) or unknown (the rest). A
    MISSING file reads None with no signal (healthy empty); a
    CORRUPT file also reads None, but is now disclosed — see
    corruption_state() / corruption_notice().
    """
    try:
        payload = json.loads(_grants_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:  # noqa: BLE001 — not valid JSON
        _note_corruption(f"not valid JSON ({exc})")
        return None
    except Exception as exc:  # noqa: BLE001 — unreadable, not missing
        _note_corruption(f"unreadable ({exc})")
        return None
    if not isinstance(payload, dict):
        _note_corruption("not a JSON object")
        return None
    grants = payload.get("grants")
    changes = payload.get("changes")
    if not isinstance(grants, dict) or not isinstance(changes, list):
        _note_corruption("grants/changes have the wrong shape")
        return None
    return grants, changes


def _write_store(grants: dict, changes: list) -> bool:
    """Atomic whole-file rewrite. False (never raising) on failure.

    Round-3 C3-F3: a corrupt file on disk is quarantined first
    (original bytes kept alongside) — a re-write never silently
    replaces it. When the quarantine itself fails, the write is
    refused (False) rather than destroying the old file."""
    try:
        path = _grants_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and _file_problem(path) is not None:
            if quarantine_corrupt_store() is None:
                return False
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"version": 1, "grants": grants, "changes": changes},
                    indent=1, sort_keys=True))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:  # noqa: BLE001 — caller reports False
        log.warning("permission grants save failed (%s: %s)",
                    type(exc).__name__, exc)
        return False


def get_state(permission_id: str) -> str:
    """The stored state, or the fail-closed/unknown default.

    Never raises. Unknown ids read "unknown". A corrupt / missing /
    unreadable store reads "denied" for microphone and camera
    (D2 carve-out) and "unknown" for the other four ids.
    """
    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS:
        return "unknown"
    stored = _read_store()
    if stored is None:
        if pid in FAIL_CLOSED_IDS:
            return "denied"
        return "unknown"
    grants, _changes = stored
    record = grants.get(pid)
    if not isinstance(record, dict):
        if pid in FAIL_CLOSED_IDS:
            return "denied"
        return "unknown"
    state = record.get("state")
    if state in ("granted", "denied", "unknown"):
        return state
    if pid in FAIL_CLOSED_IDS:
        return "denied"
    return "unknown"


def get_record(permission_id: str) -> dict:
    """The stored record, or a blank unknown record. Never raises."""
    pid = str(permission_id or "")
    stored = _read_store()
    if stored is not None:
        record = stored[0].get(pid)
        if isinstance(record, dict) and record.get("state") in (
                "granted", "denied", "unknown"):
            return dict(record)
    return _blank_record(pid)


def list_states() -> dict:
    """{permission_id: state} for all six ids. Never raises."""
    return {pid: get_state(pid) for pid in PERMISSION_IDS}


def permission_for_tool(tool_name: str) -> str | None:
    """The PF-18 id bound to `tool_name`, or None when unbound."""
    return _TOOL_TO_PERMISSION.get(str(tool_name or ""))


def check_tool(tool_name: str) -> tuple[bool, str | None]:
    """(allowed, permission_id) for one funnel call.

    Allowed unless the tool's bound permission reads denied —
    except a session grant widens the fail-closed default
    read for a live-loop call (session_grant_allows: live
    origin + no stored denial, never for microphone).
    Never raises: a consult failure fails closed for bound
    tools.
    """
    pid = permission_for_tool(tool_name)
    if pid is None:
        return True, None
    try:
        state = get_state(pid)
    except Exception:  # noqa: BLE001 — fail closed, never pass
        return False, pid
    if state == "denied":
        # Settings redesign (2026-11-16, R1): a session grant
        # widens ONLY the fail-closed default read, live-origin
        # only, never a stored denial (D7), never for the
        # microphone (D2 carve-out — see session_grant_allows).
        if session_grant_allows(pid):
            return True, None
        return False, pid
    return True, None


def is_mic_denied() -> bool:
    """True when microphone reads denied (fail-closed included)."""
    return get_state("microphone") == "denied"


def check_home_device() -> tuple[bool, str | None]:
    """(allowed, permission_id) for a home-device call.

    PF-18 batch 9 (D10 honest-empty): no home-device tool exists yet,
    so no product caller routes through this today — it is the hook
    future device tools call before reaching a device, mirroring
    check_tool's shape (the funnel renders the Settings refusal
    message from the permission id; a direct caller does the same).
    Denied refuses; granted/unknown pass. Never raises: a consult
    failure fails closed, like check_tool for bound tools.
    """
    try:
        state = get_state("home_devices")
    except Exception:  # noqa: BLE001 — fail closed, never pass
        return False, "home_devices"
    if state == "denied":
        return False, "home_devices"
    return True, None


# -- session-only grants (first-use mic ask, D3/D7) ------------------------
# In-memory only, never persisted, never written to the store file.
# A session-only grant lets the live-voice mic open for THIS process
# only (the first-use ask's "Allow once"); a fresh process starts
# with no session grants, so a later start asks again. A per-session
# decline ("Not now") suppresses the ask until the process ends.
# A Settings flip to denied clears the session grant for that id
# (D5 switch-off wins) — see set_permission_state's mic/camera
# teardown branches.
#
# Settings redesign (2026-11-16, R1): the session grant generalises
# from the mic dialog to every permission — voice can grant one
# permission for this session only (core/settings_voice.py). The
# funnel consults a session grant ONLY through
# session_grant_allows(), and ONLY when all three hold: the grant
# exists, there is no STORED denial for the id (D7: a stored
# denial is never widened — the fail-closed DEFAULT read, no
# record yet so mic/camera read "denied", is not a denial, and
# the mic first-use ask is the precedent), and the call arrived
# through the live loop (is_live_origin). MICROPHONE keeps its
# D2 carve-out: the funnel never consults a mic session grant —
# mic tools keep refusing until Settings flips, and the mic
# session grant opens only the live-voice stream (the audio
# manager consults has_session_grant itself).
# This module stays stdlib-only; the session record is two plain
# sets, so it can never cycle with the funnel, the voice chain, or
# the ledger — same discipline as the teardown-hook list below.

_SESSION_GRANTS: set = set()
_SESSION_DECLINES: set = set()


def grant_session_only(permission_id: str) -> bool:
    """Record a session-only grant. In-memory only, never persisted.

    Returns False (and records nothing) for an unknown id. A
    session-only grant is NOT a durable grant: it never writes the
    store, it never survives the process, and only the live-voice
    mic consults it (see has_session_grant)."""
    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS:
        return False
    _SESSION_GRANTS.add(pid)
    return True


def has_session_grant(permission_id: str) -> bool:
    """True when a session-only grant is held for this id. Never raises."""
    try:
        return str(permission_id or "") in _SESSION_GRANTS
    except Exception:  # noqa: BLE001 — fail closed, never pass
        return False


def decline_session(permission_id: str) -> None:
    """Record a per-session "Not now" decline. Never raises.

    The decline suppresses the first-use ask until the process ends;
    it never writes the store and never changes the stored state."""
    pid = str(permission_id or "")
    if pid in PERMISSION_IDS:
        _SESSION_DECLINES.add(pid)


def declined_this_session(permission_id: str) -> bool:
    """True when this session already declined the ask. Never raises."""
    try:
        return str(permission_id or "") in _SESSION_DECLINES
    except Exception:  # noqa: BLE001 — fail closed, never pass
        return False


def revoke_session_grant(permission_id: str) -> bool:
    """Clear one session grant (voice deny, R1). Returns True
    when a grant was held. Never touches the durable store,
    never raises."""

    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS:
        return False
    held = pid in _SESSION_GRANTS
    _SESSION_GRANTS.discard(pid)
    return held


def session_grant_allows(permission_id: str) -> bool:
    """True when a session grant lets THIS live-loop call
    through for `permission_id`.

    All three must hold, and the check fails closed on any
    consult failure:
      1. a session grant is held for the id;
      2. there is no STORED denial for the id — get_record's
         state is not "denied" (D7: a stored denial is never
         widened; the fail-closed default read is not one);
      3. the call arrived through the live loop
         (core/funnel/live_origin.is_live_origin) — a session
         grant granted by voice in a live session never opens
         a chat, plan, or routine call in the same process.
    MICROPHONE is always False here (D2 carve-out): funnel
    mic tools refuse until Settings flips, whatever the
    session holds; the mic session grant opens only the
    live-voice stream, which consults has_session_grant
    itself.
    """
    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS or pid == "microphone":
        return False
    try:
        if pid not in _SESSION_GRANTS:
            return False
        if get_record(pid).get("state") == "denied":
            return False
        from .funnel.live_origin import is_live_origin as _live

        return bool(_live())
    except Exception:  # noqa: BLE001 — fail closed, never pass
        return False


def clear_session_grants() -> None:
    """Drop all session grants and declines (tests only).

    The production equivalent is a fresh process: the sets are
    module-level in-memory state, so they die with the process.
    Tests call this to simulate a new session."""
    _SESSION_GRANTS.clear()
    _SESSION_DECLINES.clear()


def store_exists() -> bool:
    """True when the grants store FILE exists on disk (readable or not).

    Existence — not readability — is the first-run test: a corrupt
    store still counts as "not first run", so D2's no-ask-again trap
    holds for mic/camera (an existing-but-unreadable store never
    asks). Never raises."""
    try:
        return _grants_path().exists()
    except Exception:  # noqa: BLE001 — an unreadable path is not first run
        return True


def permission_never_decided(permission_id: str) -> bool:
    """True when the user has never made a choice for `permission_id`.

    The general form of _mic_never_decided (field-check fix #21):
    two shapes count as "never decided" — no store file at all
    (true first run), and a READABLE store that has no record for
    the id (e.g. the user flipped some OTHER switch first; that
    write creates the file but says nothing about this permission,
    and get_state() reads a missing record as denied for the
    fail-closed ids). An explicit record (granted / denied /
    unknown) is a decision and is never "never decided". A corrupt
    / unreadable store is also NOT "never decided": D2's
    no-ask-again trap for a store we cannot trust still holds.
    Never raises. get_state() itself is unchanged — it still
    fail-closes; only the ask's reading of it distinguishes.
    """
    try:
        pid = str(permission_id or "")
        if pid not in PERMISSION_IDS:
            return False
        if not store_exists():
            return True
        stored = _read_store()
        if stored is None:
            return False  # corrupt/unreadable: D2 no-ask-again holds
        return not isinstance(stored[0].get(pid), dict)
    except Exception:  # noqa: BLE001 — fail closed, never ask
        return False


def _mic_never_decided() -> bool:
    """True when the user has never made a microphone choice.

    Two shapes count as "never decided":
      * no store file at all (true first run), and
      * a READABLE store that has no microphone record — e.g. the
        user flipped some OTHER switch (screen, files, ...) in
        Settings first. That write creates the file but says
        nothing about the mic, and get_state() reads a missing mic
        record as denied (fail closed). Before this was handled the
        file's existence alone counted as "not first run", so the
        mic sat denied with NO ask ever again — a silent, permanent
        deaf session with no way to find out why.

    An explicit microphone record (granted / denied / unknown) is a
    decision and is never "never decided". A corrupt / unreadable
    store is also NOT "never decided": D2's no-ask-again trap for a
    store we cannot trust still holds. Never raises."""
    return permission_never_decided("microphone")


def mic_first_use_ask_needed() -> bool:
    """True when the first-use mic ask should appear.

    All four must hold: microphone reads denied AND the user has
    never made a microphone choice (no store file yet, or a
    readable store with no microphone record — not an explicit deny,
    not a corrupt store) AND no session grant is held AND the user
    has not declined this session. Never raises: any failure reads
    as "do not ask" (the mic stays off, the console print stays)."""
    try:
        if not is_mic_denied():
            return False
        if has_session_grant("microphone"):
            return False
        if declined_this_session("microphone"):
            return False
        return _mic_never_decided()
    except Exception:  # noqa: BLE001 — fail closed, never ask
        return False


# -- mic teardown hooks -----------------------------------------------------
# Live capture sessions are caller-owned (no global sidecar / voice
# instance exists), so they register their stop here. The widget batch
# wires the GUI-owned sidecar stop and voice-manager stop; until then
# the hook list is the teardown contract and tests prove it fires.

_mic_teardown_hooks: list = []


def register_mic_teardown(fn) -> None:
    """Register a zero-arg teardown called on microphone → denied."""
    if callable(fn) and fn not in _mic_teardown_hooks:
        _mic_teardown_hooks.append(fn)


def unregister_mic_teardown(fn) -> None:
    """Remove a previously registered teardown hook."""
    try:
        _mic_teardown_hooks.remove(fn)
    except ValueError:
        pass


def _run_mic_teardown() -> list:
    """Run every mic-teardown hook. Returns per-hook errors (never raises)."""
    errors = []
    for fn in list(_mic_teardown_hooks):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — one bad hook
            errors.append(f"{fn!r}: {exc}")  # must not block the rest
            log.warning("mic teardown hook failed (%s: %s)",
                        type(exc).__name__, exc)
    return errors


def set_permission_state(permission_id: str, state: str,
                         source: str = "user",
                         via: str = "settings",
                         how: str = "Settings") -> bool:
    """Flip a durable grant. Settings GUI only (D8).

    Only via="settings" may write granted/denied; any other via returns
    False and writes nothing. Returns False (never raising) when the
    id/state is invalid or the atomic write fails — a failed flip
    writes no state and no event, so the gate can never disagree with
    the store. On a successful microphone → denied flip, the teardown
    runs (capture approval cleared + hooks); teardown failures are
    logged and never fail the flip, because the gate reads the store.

    ``how`` (UI rethink Build 4 §4.4) names the tap that made the
    grant for the Always-allow screen's granted-when line —
    "Settings" (the switches), "first-run setup" (the wizard),
    "confirm dialog" (the mic ask) — and is recorded on the
    change event. It defaults to "Settings": every durable grant
    goes through the Settings-only write path (D8), so the
    default is true unless a caller passes a more precise how.
    """
    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS:
        return False
    if state not in ("granted", "denied", "unknown"):
        return False
    if via != "settings":
        return False
    if source not in ("user", "probe"):
        source = "user"
    stored = _read_store()
    if stored is None:
        grants: dict = {}
        changes: list = []
    else:
        grants, changes = stored
        grants = dict(grants)
        changes = list(changes)
    old_state = get_state(pid)
    record = {
        "permission_id": pid,
        "state": state,
        "source": source,
        "updated_at": _now_iso(),
        "scope_note": _SCOPE_NOTES.get(pid, ""),
    }
    grants[pid] = record
    changes.append({
        "permission_id": pid,
        "old_state": old_state,
        "new_state": state,
        "source": source,
        "how": str(how or "Settings"),
        "at": _now_iso(),
    })
    if not _write_store(grants, changes):
        return False
    if state == "denied":
        _run_denial_teardown(pid)
    return True


def _run_denial_teardown(pid: str) -> None:
    """Run the live teardown for a withdrawn grant.

    Rethink Build 6: extracted verbatim from
    set_permission_state (the denial path) so a taken-back
    grant tears down exactly like a denied one — capture
    approvals revoked, session grants discarded, the browser
    session closed, the screen-watch switch turned off.
    Teardown failures are logged and never raised, because
    the gate reads the store.
    """
    if pid == "microphone":
        try:
            from . import capture_guard as _cg
            _cg.revoke_capture_approval("microphone")
        except Exception as exc:  # noqa: BLE001 — teardown never
            log.warning("mic capture approval clear failed (%s: %s)",
                        type(exc).__name__, exc)
        _run_mic_teardown()
        # First-use mic ask batch (D5 switch-off wins): a Settings
        # denial clears any session-only "Allow once" for the mic, so
        # a later start() can never reopen the mic on a stale grant.
        # The store contract is untouched — this only drops the
        # in-memory session record.
        _SESSION_GRANTS.discard("microphone")
    if pid == "camera":
        # PF-18 batch 2: a camera denial clears the camera session
        # approval, so no ask-once approval survives the flip — the
        # next capture asks afresh (and, for a denied id, the funnel
        # refuses first). Camera has no session broker and no
        # teardown hooks (one-shot captures only), so the approval
        # clear IS the camera teardown; the mic hooks stay mic-only.
        try:
            from . import capture_guard as _cg
            _cg.revoke_capture_approval("camera")
        except Exception as exc:  # noqa: BLE001 — teardown never
            log.warning("camera capture approval clear failed "
                        "(%s: %s)", type(exc).__name__, exc)
        # First-use mic ask batch (D5, symmetric with the mic): a
        # Settings denial clears any session-only grant for the
        # camera too.
        _SESSION_GRANTS.discard("camera")
    if pid == "screen":
        # PF-18 batch 4: a screen denial clears the screenshot_save
        # session approval (the save path's ask-once opt-in), so no
        # ask-once approval survives the flip — and the span gate's
        # screenshot_save re-check then denies too. It ALSO turns
        # the screen-watch session switch off (design §5.3): the
        # process-wide switchboard in core/desktop/screen_watch is
        # the mode state the GUI pill, the Settings checkbox and
        # the watcher engine all read, so flipping it off here
        # stops the mode everywhere, including the GUI's next poll.
        # screen_watch imports stdlib only, so the lazy import
        # cannot cycle; teardown failures are logged and never
        # fail the flip, because the gate reads the store.
        try:
            from . import capture_guard as _cg
            _cg.revoke_capture_approval("screenshot_save")
        except Exception as exc:  # noqa: BLE001 — teardown never
            log.warning("screen capture approval clear failed "
                        "(%s: %s)", type(exc).__name__, exc)
        try:
            from .desktop import screen_watch as _sw
            _sw.set_watch_enabled(
                False, source="permission:screen-denied")
        except Exception as exc:  # noqa: BLE001 — teardown never
            log.warning("screen-watch switch-off failed (%s: %s)",
                        type(exc).__name__, exc)
    # PF-18 batch 7: files has NO flip teardown — file tools are
    # one-shot calls with no session broker and no capture-guard
    # approval kind (capture_guard kinds are microphone / camera /
    # screenshot_save, and screenshot_save belongs to screen). A
    # files denial is enforced at dispatch (funnel choke point)
    # and at scope (folder allowlist); there is nothing live to
    # tear down.
    if pid == "browser":
        # PF-18 batch 5: a browser denial closes VYUK's browser
        # session, if one is connected — a denied browser means no
        # VYUK-driven browser session can exist (D2 spirit). The
        # session is the module-level BrowserWorker in
        # core/desktop/browser_session; _impl_close is attach-only
        # (v5.70: it NEVER touches the user's Chrome — detaching
        # disconnects VYUK's controls, and only a browser VYUK
        # itself launched is closed). Playwright is imported lazily
        # inside browser_engine.start_playwright (called by _start_pw),
        # so this lazy import is safe in the lab;
        # teardown failures are logged and never fail the flip,
        # because the gate reads the store. There is no capture-
        # guard analog for browser: the funnel choke point is the
        # sole tool-call enforcement, and this close is the session
        # teardown.
        try:
            from .desktop import browser_session as _bs
            if _bs._connected():
                _bs._impl_close()
        except Exception as exc:  # noqa: BLE001 — teardown never
            log.warning("browser session close failed (%s: %s)",
                        type(exc).__name__, exc)


def clear_permission_decision(permission_id: str, via: str = "settings",
                              how: str = "first-run setup") -> bool:
    """Withdraw a durable grant decision: delete the record.

    Rethink Build 6 (wizard permissions page "Take back"): a grant
    made earlier in the first-run walk is undone, returning the
    permission to never-decided — so the next use asks again, exactly
    as if the grant had never been made. Writing state "unknown"
    would NOT do that: an explicit "unknown" record counts as a
    decision (permission_never_decided), so the ask pages would hide
    instead of re-asking. Deleting the record is the only true undo.

    Only via="settings" may write (the same gate as the durable
    flip); any other via returns False and writes nothing. Returns
    False (never raising) when the id is invalid, the via is wrong,
    the store cannot be read, there is no record to take back, or
    the atomic write fails. On success the change event records
    old -> "unknown" with the given how, and a withdrawn "granted"
    runs the same live teardown as a denial.
    """
    pid = str(permission_id or "")
    if pid not in PERMISSION_IDS:
        return False
    if via != "settings":
        return False
    stored = _read_store()
    if stored is None:
        return False
    grants, changes = stored
    if not isinstance(grants.get(pid), dict):
        return False
    old_state = get_state(pid)
    grants = dict(grants)
    changes = list(changes)
    del grants[pid]
    changes.append({
        "permission_id": pid,
        "old_state": old_state,
        "new_state": "unknown",
        "source": "user",
        "how": str(how or "first-run setup"),
        "at": _now_iso(),
    })
    if not _write_store(grants, changes):
        return False
    if old_state == "granted":
        _run_denial_teardown(pid)
    return True


# ----------------------------------------------------------------------
# D9 folder allowlist (PF-18 batch 7): the scope layer of the files
# permission. Files DENIED refuses every file tool at the funnel
# choke point; files GRANTED refuses any file-tool path argument
# that falls outside this list (funnel folder-scope stage, before
# the confirm ask). Files UNKNOWN passes scope — D3 ("allow first
# use only through the normal ask") means unknown goes through the
# confirm path, and an allow-once confirms that call including its
# path; the list constrains the STANDING grant, it does not
# override explicit per-call consent.
#
# Storage choice (reported): a SEPARATE durable file, not a key in
# the grants envelope. Reasons: (1) zero changes to the verified
# grants read/write path (batches 1–6 behaviour untouched); (2) the
# folder list has a different lifecycle from grants (defaults, not
# fail-closed; no flip ledger); (3) the same atomic-write pattern
# is reused, so a folder change is still one atomic write. The list
# is meaningful only when files is granted/unknown; a missing or
# corrupt folder file reads as the defaults, never as denied.
# Whole-filesystem on/off is REJECTED per D9 — there is no
# "allow all folders" switch; the user adds folders one by one.
# ----------------------------------------------------------------------

_FOLDERS_FILE = "permission_folders.json"

# Windows-style %NAME% environment reference — mirrors
# core/funnel/tools_gate_paths._WIN_ENV_RE (duplicated here so this
# module stays stdlib-only with no funnel import).
_WIN_ENV_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _folders_path() -> pathlib.Path:
    if _PATH_OVERRIDE is not None:
        # Tests point the grants store at a temp file; the folder
        # file lives beside it, so test isolation holds for both.
        return _PATH_OVERRIDE.parent / _FOLDERS_FILE
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data" / _FOLDERS_FILE)


def _normalise_path_for_scope(raw) -> str | None:
    """Canonical form of a path for allowlist comparison, or None.

    Normalisation rule (stated explicitly, tested): expand
    environment references ($VAR / ${VAR} / %VAR%), expand ~,
    resolve relative paths against the process cwd (matching how
    handlers resolve them), resolve symlinks (realpath), then
    slash-normalise and apply os.path.normcase. normcase is
    platform-correct by construction: case-insensitive on Windows,
    case-sensitive on POSIX — unlike the protected-path checks,
    which lowercase unconditionally because they judge Windows
    path strings host-independently; scope judges LOCAL paths, so
    platform behaviour is the right behaviour. Trailing slashes
    vanish (components, not strings, are compared). Returns None
    for empty input or a normalisation failure — an unjudgeable
    path is NOT allowed (fail-closed for scope).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        expanded = os.path.expandvars(text)
        expanded = _WIN_ENV_RE.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)), expanded)
        resolved = os.path.realpath(os.path.abspath(
            os.path.expanduser(expanded)))
        normalised = os.path.normcase(resolved).replace("\\", "/")
        parts = [p for p in normalised.split("/") if p not in ("", ".")]
        if not parts:
            return None
        # Preserve the leading slash (or drive) in component form:
        # rejoining keeps "/"-rooted and "C:/..." paths distinct.
        if normalised.startswith("/"):
            return "/" + "/".join(parts)
        return "/".join(parts)
    except Exception:  # noqa: BLE001 — unjudgeable means not allowed
        return None


def _scope_parts(normalised: str) -> list:
    if normalised.startswith("/"):
        return [p for p in normalised.split("/") if p]
    return [p for p in normalised.split("/") if p]


def _default_folders() -> list:
    """Folders VYUK already works in, from handler evidence only.

    ~/Downloads: download_file's default destination ("Download an
    http(s) file … to ~/Downloads or the given path") and
    download_email_attachment's default save_dir ("Files land in
    save_dir (default ~/Downloads …)"). ~/Documents: env_backup's
    default ("the default is the user's Documents folder"). No
    other folder is guessed — the user adds more in Settings.
    """
    out = []
    for candidate in (pathlib.Path.home() / "Downloads",
                      pathlib.Path.home() / "Documents"):
        normalised = _normalise_path_for_scope(str(candidate))
        if normalised is not None and normalised not in out:
            out.append(normalised)
    return out


def _read_folders() -> list | None:
    """Stored folder list (normalised), [] when explicitly emptied,
    or None when missing/corrupt/wrong-shape (→ defaults).

    Never raises. A stored [] is a legitimate user choice (no
    folders allowed) and is NOT collapsed to defaults — only None
    means defaults.
    """
    try:
        payload = json.loads(_folders_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt/unreadable
        return None
    if not isinstance(payload, dict):
        return None
    folders = payload.get("folders")
    if not isinstance(folders, list):
        return None
    out = []
    for entry in folders:
        if not isinstance(entry, str) or not entry.strip():
            continue
        normalised = _normalise_path_for_scope(entry)
        if normalised is not None and normalised not in out:
            out.append(normalised)
    # A list that was all junk normalises to [] — but the stored
    # shape was valid, so [] stands (it is not None → not defaults).
    # Junk entries are dropped silently; the next Settings write
    # rewrites the file cleanly.
    return out


def _write_folders(folders: list) -> bool:
    """Atomic whole-file rewrite. False (never raising) on failure."""
    try:
        path = _folders_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"version": 1, "folders": list(folders)},
                    indent=1, sort_keys=True))
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:  # noqa: BLE001 — caller reports False
        log.warning("permission folders save failed (%s: %s)",
                    type(exc).__name__, exc)
        return False


def list_folders() -> list:
    """Allowed folders (normalised): stored list, or the defaults
    when no folder file exists / it is corrupt. Never raises."""
    try:
        stored = _read_folders()
    except Exception:  # noqa: BLE001 — defaults on any failure
        stored = None
    if stored is not None:
        return stored
    return _default_folders()


def add_folder(folder, via: str = "settings") -> bool:
    """Add a folder to the allowlist. Settings GUI only (D8).

    Only via="settings" may write; any other via returns False and
    writes nothing. Already-present is True (idempotent, no write).
    Returns False (never raising) when the folder is empty /
    unjudgeable or the atomic write fails.
    """
    if via != "settings":
        return False
    normalised = _normalise_path_for_scope(folder)
    if normalised is None:
        return False
    stored = _read_folders()
    folders = list_folders() if stored is None else stored
    if normalised in folders:
        return True
    folders.append(normalised)
    return _write_folders(folders)


def remove_folder(folder, via: str = "settings") -> bool:
    """Remove a folder from the allowlist. Settings GUI only (D8).

    Only via="settings" may write; any other via returns False and
    writes nothing. Already-absent is True (idempotent, no write).
    Returns False (never raising) when the folder is empty /
    unjudgeable or the atomic write fails. Removing the last folder
    stores an explicit [] (no folders allowed), NOT the defaults.
    """
    if via != "settings":
        return False
    normalised = _normalise_path_for_scope(folder)
    if normalised is None:
        return False
    stored = _read_folders()
    folders = list_folders() if stored is None else stored
    if normalised not in folders:
        return True
    folders = [f for f in folders if f != normalised]
    return _write_folders(folders)


def grant_folder_and_files(folder) -> bool:
    """Grant a folder AND the files permission, durably, in one
    tap (UI rethink Build 4 §4.5: the per-call files ask's
    "Always allow this folder" button). A folder grant without
    the permission is meaningless — the scope stage only
    constrains a STANDING grant — so the button grants both,
    and the ledger records both (the permission's change
    event with how "files ask", plus a folder event in the
    always-allow ledger).

    GUI confirm-dialog button ONLY (D8, L6 shape): the only
    product-code caller is the confirm dialog's folder
    button (gui/window_services_confirm.py), pinned by the
    suite. No tool, routine, watch, plan or voice path can
    reach it — it does NOT go through set_permission_state /
    add_folder's via="settings" check, because the button is
    not Settings; it writes through the same internal atomic
    writers instead, so the via rule for the Settings path
    is untouched.

    Returns False (never raising) when the folder is
    unjudgeable or either write fails. A half-written grant
    (folder added, permission failed) still reports False —
    and the lone folder entry grants nothing, because the
    permission is the gate: with files unknown or denied,
    the scope list constrains nothing and the funnel asks
    or refuses first.
    """
    normalised = _normalise_path_for_scope(folder)
    if normalised is None:
        return False
    try:
        stored = _read_folders()
        folders = list_folders() if stored is None else stored
        if normalised not in folders:
            folders = folders + [normalised]
            if not _write_folders(folders):
                return False
        stored_grants = _read_store()
        if stored_grants is None:
            grants: dict = {}
            changes: list = []
        else:
            grants, changes = stored_grants
            grants = dict(grants)
            changes = list(changes)
        old_state = get_state("files")
        record = {
            "permission_id": "files",
            "state": "granted",
            "source": "user",
            "updated_at": _now_iso(),
            "scope_note": _SCOPE_NOTES.get("files", ""),
        }
        grants["files"] = record
        changes.append({
            "permission_id": "files",
            "old_state": old_state,
            "new_state": "granted",
            "source": "user",
            "how": "files ask",
            "at": _now_iso(),
        })
        if not _write_store(grants, changes):
            return False
    except Exception:  # noqa: BLE001 — a failed grant says so
        return False
    try:
        from . import always_allow as _aa

        _aa.record_event(subject=f"folder:{normalised}",
                         action="granted", how="files ask")
        _aa.record_event(subject="permission:files",
                         action="granted", how="files ask")
        _folder_name = normalised.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        _aa.log_grant_receipt(
            subject="permission:files", action="granted",
            how="files ask",
            note=f"for the folder {_folder_name or normalised}",
            undo=lambda: _undo_folder_and_files(normalised))
    except Exception:  # noqa: BLE001 — receipt never blocks
        pass
    return True


def _undo_folder_and_files(folder: str) -> bool:
    """The files-ask receipt's Undo: take back the whole
    grant — files returns to asking AND the folder entry
    goes. (A lone folder entry would grant nothing while
    files is unknown, but the undo promises the whole
    thing back, so both go.)"""
    try:
        ok = set_permission_state("files", "unknown",
                                  via="settings", how="Activity")
    except Exception:  # noqa: BLE001
        ok = False
    try:
        stored = _read_folders()
        folders = list_folders() if stored is None else stored
        if folder in folders:
            folders = [f for f in folders if f != folder]
            _write_folders(folders)
    except Exception:  # noqa: BLE001 — permission flip is the load
        pass
    return bool(ok)


def is_path_allowed(raw) -> bool:
    """True when path `raw` falls inside an allowed folder.

    Containment is component-wise on normalised forms: the path
    itself is an allowed folder, or its components start with an
    allowed folder's components. An unjudgeable path (None after
    normalisation) is NOT allowed — fail-closed for scope. Never
    raises.
    """
    try:
        normalised = _normalise_path_for_scope(raw)
    except Exception:  # noqa: BLE001 — unjudgeable means not allowed
        return False
    if normalised is None:
        return False
    path_parts = _scope_parts(normalised)
    for folder in list_folders():
        folder_parts = _scope_parts(folder)
        if path_parts[:len(folder_parts)] == folder_parts:
            return True
    return False
