"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

import os
import re
import subprocess
from typing import Any


# ------------------------------------------------------------------
# Protected paths (v5.43, H17): file-content reads never touch
# credential stores, SSH keys, .env files or browser profile data —
# not even untainted, not even with confirmation. The deny-list is
# enforced centrally in ToolRegistry.execute() BEFORE the handler
# runs, for every tool whose purpose is reading file content by
# path: _FILE_READ_PATH_ARGS maps such a tool to the argument names
# that carry the path(s) to check. Listing/stat tools that report
# only names and sizes (folder_size, folder_outline, find_files …)
# are deliberately NOT here — this list is content readers.
# file_control is special-cased in _protected_path_hit: only its
# read actions (list / find / info) are checked there.
# v5.47 (programme item 9): its writes are no longer governed by
# the confirmation gate ALONE — file_control's copy / move /
# rename actions, and every other tool's write destinations, are
# judged by the separate destination check below
# (_WRITE_DESTINATION_ARGS / _protected_destination_hit).
_FILE_READ_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "read_file": ("filepath",),
    "read_pdf_text": ("path",),
    "ocr_file": ("path",),
    "tail_file": ("path",),
    "csv_preview": ("path",),
    "csv_query": ("path",),
    "csv_column_stats": ("path",),
    "compare_files": ("path_a", "path_b"),
    "image_compare": ("path_a", "path_b"),
    "file_hash": ("path",),
    "sniff_file": ("path",),
    "pdf_info": ("path",),
    "image_info": ("path",),
    "color_from_image": ("path",),
    "qr_decode_image": ("image_path",),
    "screenshot_to_text_note": ("path",),
    "json_query": ("source",),
    "json_diff": ("path_a", "path_b"),
    "extract_from_text": ("path",),
    "search_file_contents": ("folder",),
    "folder_diff": ("path_a", "path_b"),
    "find_duplicates": ("folder",),
    "duplicate_photo_finder": ("folder",),
    # The words/text packs: each takes text OR a file path and
    # returns content-derived output (some quote the text back).
    "acronym_finder": ("path",),
    "case_convert": ("path",),
    "filler_word_count": ("path",),
    "passive_voice_check": ("path",),
    "reading_time": ("path",),
    "sentence_stats": ("path",),
    "text_stats": ("path",),
    "word_frequency": ("path",),
    # R8-7 basic-pitch port: the WAV file the handler reads.
    "audio_to_midi": ("audio_path",),
}

# ------------------------------------------------------------------
# Write destinations (v5.47, programme item 9): the H17 deny-list
# above judged only what a tool READS. That left the mirror-image
# hole wide open: file_control could copy ~/.ssh/id_rsa to an
# innocent ~/Documents/id_rsa.txt — no read ever happened — and the
# copy could then be read back freely; and any tool that writes to
# a model-chosen path could OVERWRITE a protected file in place.
# So execute() now ALSO judges write destinations, as a second,
# visibly separate check (see _protected_destination_hit):
#
#   * _WRITE_DESTINATION_ARGS maps a tool to the argument names
#     whose value is a file/folder the handler WRITES. Every entry
#     was verified against the handler itself (the arg is opened /
#     saved / written by that handler) — tools whose similarly
#     named argument is an input, a plain name or a keyword are
#     NOT here. Optional destinations are checked only when
#     actually given; when the argument is omitted the handler
#     derives its own default beside the input / in a home folder,
#     under a non-clobbering name, which is never a protected path.
#   * file_control's write actions are special-cased in
#     _protected_destination_hit, like its read actions are in
#     _protected_path_hit: for copy / move / rename BOTH the
#     "path" (source) and the "destination" are judged — a
#     protected file must not be copied or moved somewhere
#     unprotected and read back, and a protected location must
#     not be overwritten via a copy destination.
_WRITE_DESTINATION_ARGS: dict[str, tuple[str, ...]] = {
    # The named writers: write_file's filepath is the file it
    # creates/overwrites; replace_in_file rewrites its path in
    # place (temp file + os.replace in the same folder);
    # download_file writes the fetched bytes to destination;
    # download_email_attachment saves into save_dir.
    "write_file": ("filepath",),
    # Triage Wire Build 1: the study export/session tools write
    # their caller-chosen .apkg to output_path.
    "study_export_deck": ("output_path",),
    "study_session": ("output_path",),
    # Triage Wire Build 2: the rights-add tool writes the ledger
    # file; the subtitle/vCard export tools write caller-chosen
    # files to output_path.
    "creative_rights_add": ("ledger_path",),
    "creative_subtitle_export": ("output_path",),
    "vcard_export": ("output_path",),
    # Triage Wire Build 3: the marking tool writes a caller-chosen
    # JPEG to output_path; the study workspace tools write their
    # JSON record into workspace_dir.
    # Wave 6 integration: the extension-register tool records
    # the extension in its caller-chosen directory (resolved
    # before recording; the confirm gate, not this entry, is
    # the load-bearing part).
    "extension_register": ("directory",),
    # Wave 7 Build 1: the sandbox_run tool writes only
    # inside its caller-chosen sandbox directory (the
    # funnel's confirm gate, not this entry, is the
    # load-bearing part; the module enforces containment
    # and the core/data boundaries in code).
    "sandbox_run": ("sandbox_dir",),
    # Wave 7 Build 3: the sim_predict_report tool writes its
    # prediction report to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part).
    "sim_predict_report": ("output_path",),
    # Wave 7 Build 4: the improvement_report tool writes its
    # proposal record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate, not
    # this entry, is the load-bearing part; the proposal
    # itself is never applied).
    "improvement_report": ("output_path",),
    # Wave 7 Build 5: the multiverse_compile_report tool writes
    # its compiled routine + dry-run report to a caller-chosen
    # .json file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing part;
    # the routine itself is never executed).
    "multiverse_compile_report": ("output_path",),
    # Wave 8 Build 1: the antic_cards_report tool writes its
    # suggestion-card record to a caller-chosen .json file in
    # an already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part; the grant
    # itself is NOT granted by the report tool, and no card
    # action is executed by it).
    "antic_cards_report": ("output_path",),
    # Wave 8 Build 2: the release_checklist_report tool writes
    # its checklist report to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate, not
    # this entry, is the load-bearing part; the report is not
    # a ship, and signing/feed/compilation stay DEFERRED).
    "release_checklist_report": ("output_path",),
    # Wave 8 Build 3: the port_roundtrip tool writes its bundle
    # to a caller-chosen .json file in an already-existing folder
    # (the funnel's confirm gate, not this entry, is the
    # load-bearing part; single-device only, never a sync), and
    # the phase5_report tool writes one cluster report the same
    # way (nothing executed by either tool).
    "port_roundtrip": ("output_path",),
    "phase5_report": ("output_path",),
    # Wave 8 Build 4: the feel_report tool writes a feel
    # validation report to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part; the report
    # is not a feel approval, and no asset is bundled).
    "feel_report": ("output_path",),
    # Wave 8 Build 5: the learnlast_report tool writes a
    # learning-last spec record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; the report is not an application, and nothing
    # is deployed, executed, or changed).
    "learnlast_report": ("output_path",),
    # First-open continuous opt-in: the record_choice tool
    # writes its choice record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; writing the record chooses nothing for the
    # user and starts nothing), and the delete_data tool
    # deletes files inside a caller-chosen folder (the
    # funnel's confirm gate is the load-bearing part;
    # per-feature delete only, never the PF-3 global rule).
    "continuous_record_choice": ("output_path",),
    "continuous_delete_data": ("data_dir",),
    "well_record_plan": ("output_path",),
    # Wave 9 Build 2: the draft record tool writes its
    # draft record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part; recording
    # sends nothing, and no send pipeline is invented).
    "comms_record_draft": ("output_path",),
    # Wave 9 Build 3: the device record tool writes its
    # device record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording reaches nothing and controls nothing).
    "iot_record_device": ("output_path",),
    # Wave 9 Build 4: the plan record tool writes its
    # plan record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording moves nothing and claims nothing).
    "hardware_record_plan": ("output_path",),
    # Wave 9 Build 5: the preference record tool writes
    # its preference record to a caller-chosen .json file
    # in an already-existing folder (the funnel's confirm
    # gate, not this entry, is the load-bearing part;
    # recording labels a setting, never a person, and
    # changes nothing).
    "access_record_preference": ("output_path",),
    # Wave 9 Build 6: the network record tool writes its
    # network record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording scans nothing, connects to nothing, and
    # reaches nothing).
    "net_record_network": ("output_path",),
    # Wave 9 Build 7: the gaming record tool writes its
    # gaming record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording reads no game, controls no game,
    # automates no input, and bundles no tool).
    "game_record_gaming": ("output_path",),
    # Wave 9 Build 8: the pilot record tool writes its
    # pilot record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording runs nothing, reads nothing live,
    # deploys nothing, and signs nothing).
    "pilot_record_pilot": ("output_path",),
    # Wave 9 Build 9: the travel record tool writes its
    # travel record to a caller-chosen .json file in an
    # already-existing folder (the funnel's confirm gate,
    # not this entry, is the load-bearing part;
    # recording books nothing, pays nothing, routes
    # nothing, drives nothing, renders nothing, and
    # moves nothing).
    "travel_record": ("output_path",),
    # Wave 9 Build 10: the graduation record tool writes
    # its graduation record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; recording captures nothing, grants nothing,
    # chooses nothing for any user, and decides
    # nothing; PF-3 stays open).
    "grad_record": ("output_path",),
    # Wave 10 Build 1: the release record tool writes
    # its release record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; recording releases nothing, signs nothing,
    # compiles nothing, publishes nothing, and decides
    # nothing; ruling 34 stays open).
    "release_record": ("output_path",),
    # Wave 10 Build 2: the trust record tool writes
    # its trust record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; recording sends nothing, publishes nothing,
    # claims no compliance or conformance, and ships no
    # version; ruling 34 stays open).
    "trust_record": ("output_path",),
    # Wave 10 Build 3: the first-run record tool writes
    # its first-run record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; recording exits nothing, runs nothing,
    # fixes nothing, installs nothing, and applies
    # nothing; ruling 34 stays open, no conformance
    # claimed).
    "firstrun_record": ("output_path",),
    # Wave 10 Build 4: the evidence record tool writes
    # its evidence record to a caller-chosen .json
    # file in an already-existing folder (the funnel's
    # confirm gate, not this entry, is the load-bearing
    # part; recording claims no conformance, performs
    # no audit, runs no live test, and reads no live
    # screen; ruling 34 stays open).
    "accessconf_record": ("output_path",),
    "creative_mark_image": ("output_path",),
    "study_plan": ("workspace_dir",),
    "study_progress": ("workspace_dir",),
    "study_exam_prep": ("workspace_dir",),
    "replace_in_file": ("path",),
    "download_file": ("destination",),
    "download_email_attachment": ("save_dir",),
    # create_shortcut writes a .lnk file INTO location: the
    # keywords "desktop" / "start_menu" name fixed folders (and
    # judge clean — no protected component), any other value is
    # the folder path itself.
    "create_shortcut": ("location",),
    # zip_control's destination is the archive written by "zip"
    # and the folder extracted into by "unzip".
    "zip_control": ("destination",),
    # sync_folder copies files INTO destination.
    "sync_folder": ("destination",),
    # sync_folders_two_way copies files INTO both folders.
    "sync_folders_two_way": ("folder_a", "folder_b"),
    # organise_photos moves photos INTO destination.
    "organise_photos": ("destination",),
    # The image / PDF / audio editors: output (or output_path /
    # output_dir / output_folder) is the file or folder the
    # handler saves its result to — verified per handler, all of
    # which resolve it via Path(output).expanduser() (the photo
    # packs via their _resolve_output helper) and save there.
    "convert_image": ("output",),
    "resize_image": ("output",),
    "crop_image": ("output",),
    "rotate_image": ("output",),
    "adjust_image": ("output",),
    "filter_image": ("output",),
    "watermark_image": ("output",),
    "add_border": ("output",),
    "make_collage": ("output",),
    "kaleidoscope_image": ("output",),
    "duotone_image": ("output",),
    "pixelate_image": ("output",),
    "glitch_image": ("output",),
    "strip_image_metadata": ("output",),
    "merge_pdfs": ("output",),
    "rotate_pdf": ("output",),
    "split_pdf": ("output_dir",),
    "pdf_extract_images": ("output_folder",),
    "pdf_merge_bookmarks": ("output_path",),
    "pdf_split_by_text": ("output_folder",),
    "make_sprite_sheet": ("output_path",),
    "batch_watermark": ("output_folder",),
    "audio_trim": ("output_path",),
    "audio_speed_change": ("output_path",),
    "silence_trim": ("output_path",),
    "photo_face_blur": ("output_path",),
    # The generative writers: output is the PNG / WAV the handler
    # creates (verified — each resolves Path(output) and saves).
    "create_art": ("output",),
    "fractal_art": ("output",),
    "generate_melody": ("output",),
    "audio_to_midi": ("output",),
    "compose_song": ("output",),
    "generate_beat": ("output",),
    "palette_image": ("output",),
    # Screenshot / snapshot writers: the given path is where the
    # captured bytes are written (take_screenshot writes to
    # save_path, webcam_snapshot to save_path, capture_window
    # saves the grabbed window to path).
    "take_screenshot": ("save_path",),
    "webcam_snapshot": ("save_path",),
    "capture_window": ("path",),
}

# file_control's write actions for the destination check
# (v5.47, programme item 9): the vocabulary is control.py's own —
# copy / move / rename are the actions that carry a destination
# (there is no zip action in file_control; zipping is the separate
# zip_control tool, covered above). create_folder and delete are
# deliberately NOT here: the item names copy/move/rename, and
# those two actions stay governed exactly as before (the
# confirmation gate / the handler's own confirm flag).
_FILE_CONTROL_WRITE_ACTIONS = frozenset({"copy", "move", "rename"})


# Browser profile store file names (exact, lowercased): wherever one
# of these sits, it is a browser secret store — passwords, cookies,
# the encryption key material — never document content.
_BROWSER_STORE_FILES = frozenset({
    "login data", "cookies", "key4.db", "logins.json",
})

# v5.47 (programme item 9): secret-bearing files judged by NAME,
# wherever they sit — a copy of one of these in an innocent-looking
# folder is still the secret. google_token.json is VYUK's own Google
# OAuth token store; assistant.db is her own database (memories,
# facts — and anything ever pasted into them); activity.log is her
# own activity log, which names every path and command she touched.
_PROTECTED_BASENAMES = frozenset({
    "google_token.json", "assistant.db", "activity.log",
    # SSH private keys by name, anywhere (previously only a key
    # inside a .ssh directory was caught).
    "id_rsa", "id_ed25519",
    # Dotfile credential stores: netrc passwords, git's plaintext
    # credential helper file, npm's auth-token file, and the
    # Windows registry hive (a credential store in file form).
    ".netrc", ".git-credentials", ".npmrc", "ntuser.dat",
})

# v5.47 (programme item 9): key / vault files judged by SUFFIX —
# PEM and private-key files, and KeePass password databases.
_PROTECTED_SUFFIXES = (".pem", ".key", ".kdbx")

# v5.47 (programme item 9): container/tool config files that hold
# registry / cluster credentials. These are judged as a component
# PAIR — the config file's basename together with its parent
# directory component — never by substring, so an ordinary project
# file called "config" or "config.json" stays unprotected.
_PROTECTED_DIR_FILE_PAIRS = frozenset({
    (".docker", "config.json"),  # ~/.docker/config.json
    (".kube", "config"),          # ~/.kube/config
})

# Windows-style %NAME% environment reference (v5.47, programme
# item 9 — see _expand_env_vars).
_WIN_ENV_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _expand_env_vars(text: str) -> str:
    """Expand environment references in a path string BEFORE it is
    judged (v5.47, programme item 9).

    Two syntaxes: POSIX $VAR / ${VAR} via os.path.expandvars, and
    Windows-style %VAR%. The second needs its own pass: this code
    judges Windows paths host-independently (a C:\\Users\\…\\.ssh
    string must be refused on a Linux host too — see the H17
    suite), and POSIX expandvars never expands %VAR%, so without
    this pass "%USERPROFILE%\\.ssh\\id_rsa" would slip past the
    judge on any non-Windows host simply because the variable was
    never substituted. Unknown variables are left literally in
    place (matching expandvars), so a stray "%" in a real file
    name is judged as-is rather than mangled."""
    expanded = os.path.expandvars(text)
    return _WIN_ENV_RE.sub(
        lambda m: os.environ.get(m.group(1), m.group(0)), expanded)


def _is_protected_path(raw: Any) -> bool:
    """True when `raw` — a path argument as the model supplied it —
    targets a protected location. The path is expanduser'd and
    realpath'd first, so `~/.ssh`, `~/link-to-ssh/id_rsa` and a
    symlinked `.env` are all judged by where they actually point.
    Matching is component-based on the lowercased, slash-normalized
    path, so it behaves the same for Windows-style paths on any
    host. A normal user file (Documents, a project tree) matches
    nothing here and passes untouched. Never raises."""
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    try:
        # v5.47 (programme item 9): environment references are
        # expanded BEFORE judging — first $VAR/${VAR} and %VAR%
        # (see _expand_env_vars), then ~, then realpath — so a
        # path that only BECOMES protected once its variables are
        # substituted is judged by what it actually points at,
        # exactly like the symlinked forms above.
        resolved = os.path.realpath(
            os.path.expanduser(_expand_env_vars(text)))
    except Exception:  # noqa: BLE001 — judge the raw text instead
        resolved = text
    parts = [p for p in resolved.replace("\\", "/").split("/")
             if p not in ("", ".")]
    lower = [p.lower() for p in parts]
    if not lower:
        return False
    base = lower[-1]
    # v5.47 (programme item 9): secret files by exact basename or
    # suffix, wherever they sit (see _PROTECTED_BASENAMES /
    # _PROTECTED_SUFFIXES above for the per-name rationale).
    if base in _PROTECTED_BASENAMES:
        return True
    if base.endswith(_PROTECTED_SUFFIXES):
        return True
    # v5.47 (programme item 9): credential config files judged as
    # a (parent directory, basename) component pair — Docker's and
    # Kubernetes' credential configs. Component-based, not
    # substring: a random project folder containing a file called
    # "config" or "config.json" does NOT match, because its parent
    # component is not .docker / .kube.
    if len(lower) >= 2 \
            and (lower[-2], base) in _PROTECTED_DIR_FILE_PAIRS:
        return True
    # Key / credential directories, wherever they appear.
    if any(p in (".ssh", ".aws", ".gnupg") for p in lower):
        return True
    # .env files: ".env", ".env.local", "prod.env".
    if base == ".env" or base.startswith(".env.") \
            or base.endswith(".env"):
        return True
    # Credential files by name: credentials, credentials.json, …
    if base.startswith("credentials"):
        return True
    # Browser profile stores: the secret files by exact name …
    if base in _BROWSER_STORE_FILES:
        return True
    # … and the profile directories themselves: Chromium's
    # "User Data" root, Chrome's config/profile dirs, Firefox's
    # Mozilla profile tree, Edge's profile dir.
    if "user data" in lower:
        return True
    if ".mozilla" in lower or "mozilla/firefox" in "/".join(lower):
        return True
    if "google/chrome" in "/".join(lower):
        return True
    for i, part in enumerate(lower):
        prev = lower[i - 1] if i else ""
        if part in ("google-chrome", "google-chrome-beta", "chromium",
                    "microsoft-edge") and prev in (
                        ".config", "config", "appdata", "local",
                        "application support", "snap"):
            return True
        if part == "microsoft edge":
            return True
    return False


def _protected_path_hit(name: str,
                        args: dict[str, Any] | None) -> Any:
    """The offending raw path argument when tool `name` is a
    file-content reader aimed at a protected path, else None.

    Optional path arguments are checked only when actually given;
    json_query's `source` may be raw JSON text instead of a path,
    and JSON text (leading { or [) is never judged as a path.
    file_control is checked only for its read actions."""
    args = args or {}
    if name == "file_control":
        action = str(args.get("action", "")).strip().lower()
        if action not in ("list", "find", "info"):
            return None
        keys: tuple[str, ...] = ("path",)
    else:
        keys = _FILE_READ_PATH_ARGS.get(name, ())
    for key in keys:
        raw = args.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if name == "json_query" and isinstance(raw, str) \
                and raw.lstrip()[:1] in ("{", "["):
            continue  # raw JSON text, not a path
        if _is_protected_path(raw):
            return raw
    return None


def _protected_destination_hit(name: str,
                               args: dict[str, Any] | None) -> Any:
    """The offending raw path argument when tool `name` would
    WRITE to (or, for file_control's write actions, read a source
    from) a protected path, else None (v5.47, programme item 9).

    This is the WRITE-side sibling of _protected_path_hit, kept
    deliberately separate from it: the read check above guards
    content readers, this one guards destinations. Two shapes:

      * a tool in _WRITE_DESTINATION_ARGS: the named arguments
        are the files/folders its handler writes — a protected
        value there means the handler would create or overwrite
        a protected file, so the call is refused before it runs.
      * file_control with a write action (copy / move / rename,
        see _FILE_CONTROL_WRITE_ACTIONS): BOTH "path" and
        "destination" are judged. Judging only the destination
        would let a protected SOURCE be copied/moved somewhere
        unprotected and read back from there; judging only the
        source would let a protected location be overwritten via
        a copy destination. Read actions (list / find / info)
        never reach this function — they are the read check's
        business, with exactly their previous behaviour.

    Optional destination arguments are checked only when
    actually given, exactly like the read check's optionals."""
    args = args or {}
    if name == "file_control":
        action = str(args.get("action", "")).strip().lower()
        if action not in _FILE_CONTROL_WRITE_ACTIONS:
            return None
        keys: tuple[str, ...] = ("path", "destination")
    else:
        keys = _WRITE_DESTINATION_ARGS.get(name, ())
    for key in keys:
        raw = args.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if _is_protected_path(raw):
            return raw
    return None


# ------------------------------------------------------------------
# Install-tree write-deny (v5.47, programme item 10, B-T1.4 + C5):
# the checks above protect the USER's secrets from VYUK; this one
# protects VYUK from the model. A write-capable tool must never
# create, overwrite, move, rename or delete a file inside the
# running app's own program folders — <package_root>/core,
# <package_root>/gui, <package_root>/plugins — because code in
# those folders is what runs next launch: a model (or a prompt
# injection driving it) that can rewrite the registry (core/funnel/tools_registry.py) owns every
# future session. The deny is absolute, like the protected-path
# checks: it sits in execute() BEFORE the confirmation gate, and
# no human "yes" overrides it — approving a write the user can see
# is not the same as approving a change to the program itself,
# and the popup is not a code-review surface.
#
# What is deliberately NOT denied: the package ROOT itself (the
# seeded .env lives there and is governed by the protected-path
# rules, not this one) and, crucially, <package_root>/data — the
# app's own runtime store (the plugin user store, downloads,
# provisioned models, the assistant database all live there, and
# legitimate tools write there constantly). Only the three
# program subfolders are in the deny set.
#
# package_root is resolved from THIS file's location
# (Path(__file__) chain: core/funnel/tools_gate_paths.py -> core/funnel/ -> core/ -> package root; Phase 3 F1: chain climbs one more level),
# NEVER from the process cwd — the app is routinely launched from
# other directories, and a cwd-derived root would deny (or allow)
# some other project's core/ folder instead of VYUK's own.
_INSTALL_TREE_FOLDERS = ("core", "gui", "plugins")

# file_control's write actions for the install-tree check
# (v5.47, programme item 10): a superset of item 9's
# _FILE_CONTROL_WRITE_ACTIONS. Item 9 deliberately left delete
# and create_folder to the confirmation gate, because its concern
# was protected USER data and those two actions carry no
# destination to leak to. Here the concern is the program tree
# itself, and deleting a program file — or planting a new folder
# inside it — is exactly what this item exists to stop, so both
# actions are write actions here and their "path" is judged.
_FILE_CONTROL_INSTALL_WRITE_ACTIONS = frozenset(
    {"copy", "move", "rename", "delete", "create_folder"})


def _package_root_parts() -> list[str]:
    """The lowercased path components of the package root,
    resolved from this file's own location (see the section
    header above — never from cwd). Components are split on
    either slash flavour and stripped of separators, so they
    compare like-for-like with a candidate path split the same
    way (a Windows root's "C:\\" component and a candidate's
    "c:" are the same component here)."""
    import pathlib

    root = str(pathlib.Path(__file__).resolve().parent.parent.parent)  # Phase 3 F1: file now in core/funnel/ — one more level up to the package root
    return [p.strip("/\\").lower()
            for p in root.replace("\\", "/").split("/")
            if p.strip("/\\") not in ("", ".")]


def _install_tree_folder(raw: Any) -> str | None:
    """The install-tree program folder ("core" / "gui" /
    "plugins") that path argument `raw` falls inside, else None
    (v5.47, programme item 10).

    Judged after the same normalisation item 9 uses —
    environment expansion (_expand_env_vars), expanduser, then
    resolution to an absolute, symlink-free path — with one
    addition the handler's own behaviour requires: a RELATIVE
    path is first resolved against the process cwd via
    os.path.abspath, exactly the way the handlers resolve it
    (Path(...).expanduser() / os.path.abspath in control.py), so
    a bare "core/browser.py" launched from the package root is
    judged as the package's own file, not waved through as a
    relative string that happens not to start with the root.
    (os.path.realpath below would absolutise anyway; the explicit
    abspath keeps the judging order identical to the handler's.)
    The compare is component-wise on lowercased parts, so a
    DIFFERENT tree that merely ends in a folder called "core"
    (/tmp/other/core/x.py) does not match — the deny set is THIS
    package's three folders, identified by their full prefix,
    not the word "core" anywhere in a path. Never raises."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        resolved = os.path.realpath(os.path.abspath(
            os.path.expanduser(_expand_env_vars(text))))
    except Exception:  # noqa: BLE001 — judge the raw text instead
        resolved = text
    parts = [p.lower() for p in resolved.replace("\\", "/").split("/")
             if p not in ("", ".")]
    root_parts = _package_root_parts()
    # Both sides' components come from the same slash-split
    # normalisation (see _package_root_parts), so the prefix
    # compare below is apples to apples on any host.
    for folder in _INSTALL_TREE_FOLDERS:
        deny = root_parts + [folder]
        if parts[:len(deny)] == deny:
            return folder
    return None


def _install_tree_write_hit(name: str,
                            args: dict[str, Any] | None) -> Any:
    """The offending raw path argument when tool `name` would
    WRITE inside VYUK's own program folders, else None
    (v5.47, programme item 10).

    The third always-refuse judge in execute(), beside item 9's
    _protected_path_hit / _protected_destination_hit, and it
    consults the SAME argument lists those checks established
    rather than inventing a second map:

      * a tool in _WRITE_DESTINATION_ARGS: exactly the arguments
        item 9's map names. Those arguments are the file the
        handler acts on, not always a pure "destination" —
        replace_in_file's "path" is both source and target (it
        rewrites that file in place), and judging it here is
        what stops an in-place rewrite of a program file.
      * file_control with a write action (see
        _FILE_CONTROL_INSTALL_WRITE_ACTIONS — copy / move /
        rename / delete / create_folder): BOTH "path" and
        "destination" are judged, mirroring item 9's both-sides
        treatment for copy/move/rename. The source side matters
        as much as the destination side here: a move or delete
        whose SOURCE is inside core/gui/plugins removes a
        program file, which is a write against the tree even
        though the destination (a trash folder, a temp dir) is
        innocent. Read actions (list / find / info) never reach
        this check — reading program files is legitimate and is
        the read check's business.

    Optional arguments are checked only when actually given,
    exactly like the sibling checks."""
    args = args or {}
    if name == "file_control":
        action = str(args.get("action", "")).strip().lower()
        if action not in _FILE_CONTROL_INSTALL_WRITE_ACTIONS:
            return None
        keys: tuple[str, ...] = ("path", "destination")
    else:
        keys = _WRITE_DESTINATION_ARGS.get(name, ())
    for key in keys:
        raw = args.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if _install_tree_folder(raw) is not None:
            return raw
    return None


# ------------------------------------------------------------------
# D9 folder scope (PF-18 batch 7): the files permission's second
# layer. Files DENIED refuses at the funnel permission stage; files
# GRANTED refuses any file-tool path argument that falls outside
# the folder allowlist (core/permission_grants.py: list_folders /
# is_path_allowed). Files UNKNOWN passes scope — D3 ("allow first
# use only through the normal ask") means unknown goes through the
# confirm path, and an allow-once confirms that call including its
# path; the list constrains the STANDING grant, it does not
# override explicit per-call consent. The check runs as its own
# funnel stage BEFORE _exec_route (so MCP-routed calls are covered
# structurally, like the permission stage) and BEFORE the confirm
# ask — for a granted call, an out-of-scope path refuses before
# any ask (there is nothing to ask about: no answer adds the
# folder).
#
# _FOLDER_SCOPE_PATH_ARGS maps each files-bound tool to the argument
# names carrying filesystem paths to judge. It is the UNION of the
# read map and the write map above for bound tools, PLUS the
# listing/stat tools the protected read check deliberately excludes
# (listing a folder outside the allowlist reveals file names, so
# scope judges listing roots too) and the manage tools. Rules,
# mirroring the protected checks: optional arguments are judged
# only when actually given (an omitted output lands beside the
# input — in-list by construction — or in a handler default that
# is itself a default folder: ~/Downloads for download_file and
# download_email_attachment, ~/Documents for env_backup); a
# multi-path argument (paths) judges every element; file_control
# judges "path" and "destination" both when given (destination is
# only given for copy/move/rename anyway, so no action-gating is
# needed); clean_temp_files / recent_files are bound but take no
# path arg, so they map to () — scope trivially passes and the
# permission stage governs them. Special cases live in
# _folder_scope_hit, mirroring the protected hits' file_control
# precedent: display_control judges "path" ONLY for the
# set_wallpaper action (info/dark_on/dark_off ignore path);
# wallpaper_apply judges art_dir plus each entry's art_path;
# json_query skips raw JSON text (leading { or [), exactly like
# the protected read check.
# ------------------------------------------------------------------
_FOLDER_SCOPE_PATH_ARGS: dict[str, tuple[str, ...]] = {
    # Content readers (same args as _FILE_READ_PATH_ARGS).
    "read_file": ("filepath",),
    "read_pdf_text": ("path",),
    "ocr_file": ("path",),
    "tail_file": ("path",),
    "csv_preview": ("path",),
    "csv_query": ("path",),
    "csv_column_stats": ("path",),
    "compare_files": ("path_a", "path_b"),
    "image_compare": ("path_a", "path_b"),
    "file_hash": ("path",),
    "sniff_file": ("path",),
    "pdf_info": ("path",),
    "image_info": ("path",),
    "color_from_image": ("path",),
    "qr_decode_image": ("image_path",),
    "screenshot_to_text_note": ("path",),
    "json_query": ("source",),
    "json_diff": ("path_a", "path_b"),
    "extract_from_text": ("path",),
    "search_file_contents": ("folder",),
    "folder_diff": ("path_a", "path_b"),
    "find_duplicates": ("folder",),
    "duplicate_photo_finder": ("folder",),
    "acronym_finder": ("path",),
    "case_convert": ("path",),
    "filler_word_count": ("path",),
    "passive_voice_check": ("path",),
    "reading_time": ("path",),
    "sentence_stats": ("path",),
    "text_stats": ("path",),
    "word_frequency": ("path",),
    # Manage / list / inspect (listing roots judged, unlike the
    # protected read check — see the section header).
    "file_control": ("path", "destination"),
    "open_path": ("path",),
    "find_files": ("root",),
    "largest_files": ("root",),
    "folder_size": ("path",),
    "folder_outline": ("path",),
    "folder_size_map": ("path",),
    "file_age_report": ("folder",),
    "folder_watch_cook": ("folder",),
    "batch_rename": ("folder",),
    "print_file": ("path",),
    "copy_file_to_clipboard": ("path",),
    "shred_file": ("path",),
    "clean_temp_files": (),
    "recent_files": (),
    "file_dialog_action": ("folder",),
    "drag_file_onto": ("path",),
    "env_backup": ("folder",),
    "continuous_delete_data": ("data_dir",),
    # Writers / transformers (input + output judged; optional
    # outputs only when given).
    "write_file": ("filepath",),
    "replace_in_file": ("path",),
    "download_file": ("destination",),
    "download_email_attachment": ("save_dir",),
    "create_shortcut": ("location",),
    "zip_control": ("path", "destination"),
    "sync_folder": ("source", "destination"),
    "sync_folders_two_way": ("folder_a", "folder_b"),
    "organise_photos": ("source", "destination"),
    "convert_image": ("path", "output"),
    "resize_image": ("path", "output"),
    "compress_image": ("path",),
    "crop_image": ("path", "output"),
    "rotate_image": ("path", "output"),
    "adjust_image": ("path", "output"),
    "filter_image": ("path", "output"),
    "watermark_image": ("path", "output"),
    "add_border": ("path", "output"),
    "make_collage": ("paths", "output"),
    "kaleidoscope_image": ("path", "output"),
    "duotone_image": ("path", "output"),
    "pixelate_image": ("path", "output"),
    "glitch_image": ("path", "output"),
    "strip_image_metadata": ("path", "output"),
    "merge_pdfs": ("paths", "output"),
    "rotate_pdf": ("path", "output"),
    "split_pdf": ("path", "output_dir"),
    "pdf_extract_images": ("path", "output_folder"),
    "pdf_merge_bookmarks": ("paths", "output_path"),
    "pdf_split_by_text": ("path", "output_folder"),
    "make_sprite_sheet": ("folder", "output_path"),
    "batch_watermark": ("folder", "output_folder"),
    "audio_trim": ("path", "output_path"),
    "audio_speed_change": ("path", "output_path"),
    "audio_fade": ("path",),
    "silence_trim": ("path", "output_path"),
    "photo_face_blur": ("path", "output_path"),
    "creative_mark_image": ("input_path", "output_path",
                            "ledger_path"),
    "display_control": ("path",),
    "set_wallpaper": ("path",),
    "wallpaper_apply": ("art_dir",),
    "image_contact_sheet": ("folder",),
    # PF-18 batch 8: generative writers — the output path is
    # judged when given; an omitted output passes scope (nothing
    # to judge) and lands in the handler's fixed default
    # (~/Pictures/Nova for art, ~/Music/Nova for melody/beat).
    "create_art": ("output",),
    "generate_melody": ("output",),
    "audio_to_midi": ("audio_path", "output"),
    "generate_beat": ("output",),
    # PF-18 batch 8: record / report writers — the caller-chosen
    # output_path is judged when given (all are required args).
    "access_record_preference": ("output_path",),
    "antic_cards_report": ("output_path",),
    "comms_record_draft": ("output_path",),
    "continuous_record_choice": ("output_path",),
    "feel_report": ("output_path",),
    "game_record_gaming": ("output_path",),
    "hardware_record_plan": ("output_path",),
    "improvement_report": ("output_path",),
    "iot_record_device": ("output_path",),
    "learnlast_report": ("output_path",),
    "multiverse_compile_report": ("output_path",),
    "net_record_network": ("output_path",),
    "phase5_report": ("output_path",),
    "pilot_record_pilot": ("output_path",),
    "release_checklist_report": ("output_path",),
    "sim_predict_report": ("output_path",),
    "well_record_plan": ("output_path",),
    "creative_subtitle_export": ("output_path",),
    "vcard_export": ("output_path",),
    "study_export_deck": ("output_path",),
    "study_session": ("output_path",),
    # PF-18 batch 8: rights ledger tools — ledger_path judged
    # when given (a non-empty path); omitted ('') passes scope
    # and lands in the internal data/rights_ledger.db default.
    "creative_rights_add": ("ledger_path",),
    "creative_rights_check": ("ledger_path",),
    # PF-18 batch 8: study workspace tools — workspace_dir is
    # the folder the handler writes its JSON record into, so
    # judging the folder itself is judging the write.
    "study_exam_prep": ("workspace_dir",),
    "study_plan": ("workspace_dir",),
    "study_progress": ("workspace_dir",),
}


def _folder_scope_hit(name: str,
                      args: dict[str, Any] | None) -> Any:
    """The offending raw path argument when tool `name` — a
    files-bound tool — touches a path outside the D9 folder
    allowlist, else None.

    Scope binds the GRANTED state only (Choice B, D3-consistent):
    only files-bound tools with files GRANTED are judged; every
    other tool passes immediately, and an UNKNOWN files grant
    passes scope — unknown goes through the normal confirm path
    (D3: "allow first use only through the normal ask"), so an
    allow-once confirms that call including its path; the folder
    list constrains the STANDING grant, it does not override
    explicit per-call consent. A DENIED grant never reaches here
    (the permission stage refuses first). Special cases,
    mirroring the protected hits: display_control judges "path"
    only for the set_wallpaper action; wallpaper_apply judges
    art_dir plus each entry's art_path; json_query skips raw JSON
    text (leading { or [). List arguments (paths) judge every
    string element. Optional arguments are judged only when
    actually given. A path that cannot be normalised is NOT
    allowed (is_path_allowed fails closed). Never raises: a
    consult failure fails closed for files-bound tools — an
    unjudgeable scope refuses rather than opening the filesystem.
    """
    args = args or {}
    try:
        from .. import permission_grants as _pg
        if _pg.permission_for_tool(name) != "files":
            return None
        granted = _pg.get_state("files") == "granted"
    except Exception:  # noqa: BLE001 — fail closed for files tools
        granted = None
    if granted is False:
        return None  # unknown: D3 confirm path governs per-call
    if granted is None:
        # Consult failure — nearly unreachable in practice: the
        # permission stage, which runs before this stage, already
        # imported the grants module (stdlib-only) successfully,
        # and get_state / is_path_allowed / list_folders never
        # raise by construction. If it somehow fails anyway, a
        # tool that LOOKS like a file tool (present in the scope
        # map) refuses on its first given path value — a real
        # user-supplied value, never a fake token — and anything
        # else passes. The permission stage remains the
        # load-bearing consult for denial, and it fails closed
        # independently.
        if name not in _FOLDER_SCOPE_PATH_ARGS:
            return None
        for key in _FOLDER_SCOPE_PATH_ARGS[name]:
            raw = args.get(key)
            if isinstance(raw, (list, tuple)):
                for value in raw:
                    if value is not None and not (
                            isinstance(value, str)
                            and not value.strip()):
                        return value
            elif raw is not None and not (
                    isinstance(raw, str) and not raw.strip()):
                return raw
        return None
    if name == "display_control":
        action = str(args.get("action", "")).strip().lower()
        if action != "set_wallpaper":
            return None
    if name == "wallpaper_apply":
        for key in ("art_dir",):
            raw = args.get(key)
            if raw is None or (isinstance(raw, str)
                                and not raw.strip()):
                continue
            try:
                if not _pg.is_path_allowed(raw):
                    return raw
            except Exception:  # noqa: BLE001 — fail closed
                return raw
        entries = args.get("entries")
        if isinstance(entries, (list, tuple)):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw = entry.get("art_path")
                if raw is None or (isinstance(raw, str)
                                    and not raw.strip()):
                    continue
                try:
                    if not _pg.is_path_allowed(raw):
                        return raw
                except Exception:  # noqa: BLE001 — fail closed
                    return raw
        return None
    keys = _FOLDER_SCOPE_PATH_ARGS.get(name, ())
    for key in keys:
        raw = args.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if name == "json_query" and isinstance(raw, str) \
                and raw.lstrip()[:1] in ("{", "["):
            continue  # raw JSON text, not a path
        values = raw if isinstance(raw, (list, tuple)) else (raw,)
        for value in values:
            if value is None or (isinstance(value, str)
                                  and not value.strip()):
                continue
            try:
                allowed = _pg.is_path_allowed(value)
            except Exception:  # noqa: BLE001 — fail closed
                return value
            if not allowed:
                return value
    return None


# Keys whose value IS the folder (not a file inside it).
_FOLDER_ARG_KEYS = frozenset({
    "folder", "directory", "dir", "art_dir", "workspace_dir",
    "destination", "dest",
})


def _folder_of_value(value, key: str) -> str | None:
    """The folder a path argument names: the value itself
    for a folder arg, else the value's parent directory.
    None when no folder can be judged (bare filename, raw
    JSON text, empty value) — the caller then offers no
    button, never a guessed folder."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.lstrip()[:1] in ("{", "["):
        return None  # raw JSON text, not a path
    if key in _FOLDER_ARG_KEYS:
        return text
    # A file path: the parent directory, split on either
    # separator (Windows paths arrive on a Linux lab host).
    head = text.replace("\\", "/").rstrip("/")
    if "/" not in head:
        return None  # bare filename — no folder to allow
    return head.rsplit("/", 1)[0] or None


def first_call_folder(name: str,
                      args: dict[str, Any] | None) -> str | None:
    """The folder a files-bound call touches (UI rethink
    Build 4 §4.5: the per-call files ask's "Always allow
    this folder" button), or None when the call names no
    judgable folder. Mirrors _folder_scope_hit's extraction
    (same map, same display_control / wallpaper_apply /
    json_query special cases) but returns the FOLDER the
    path lives in, not the offending path — and it judges
    nothing: the caller (grant_folder_and_files) normalises
    and validates before writing anything. Never raises."""
    args = args or {}
    try:
        from .. import permission_grants as _pg
        if _pg.permission_for_tool(name) != "files":
            return None
    except Exception:  # noqa: BLE001 — not files-bound: no folder
        return None
    try:
        if name == "display_control":
            action = str(args.get("action", "")).strip().lower()
            if action != "set_wallpaper":
                return None
        if name == "wallpaper_apply":
            raw = args.get("art_dir")
            folder = _folder_of_value(raw, "art_dir")
            if folder is not None:
                return folder
            entries = args.get("entries")
            if isinstance(entries, (list, tuple)):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    folder = _folder_of_value(
                        entry.get("art_path"), "art_path")
                    if folder is not None:
                        return folder
            return None
        for key in _FOLDER_SCOPE_PATH_ARGS.get(name, ()):
            raw = args.get(key)
            if raw is None or (isinstance(raw, str)
                               and not raw.strip()):
                continue
            if name == "json_query" and isinstance(raw, str) \
                    and raw.lstrip()[:1] in ("{", "["):
                continue
            values = raw if isinstance(raw, (list, tuple)) else (raw,)
            for value in values:
                folder = _folder_of_value(value, key)
                if folder is not None:
                    return folder
        return None
    except Exception:  # noqa: BLE001 — no folder, no button
        return None
    """The H8 `kind` for an exception that escaped a tool handler
    into execute()'s funnel (v5.43). Conservative on purpose — only
    shapes this codebase makes unambiguous are classified:

      * TimeoutError (incl. asyncio's) and subprocess.TimeoutExpired
        (NOT a TimeoutError subclass, so named explicitly)
        -> "timeout": the work ran out of time.
      * ImportError / ModuleNotFoundError and FileNotFoundError
        -> "unavailable": a dependency, program or bundled asset the
        tool needs is missing. Handlers here report a missing USER
        file as a result string (read_file: "File not found: …"),
        never by raising, so a FileNotFoundError that reaches the
        funnel means the tool's own machinery is absent.
      * Anything else -> "handler_error": the handler itself failed.
    """
    if isinstance(exc, TimeoutError) \
            or isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, (ImportError, FileNotFoundError)):
        return "unavailable"
    return "handler_error"
def _handler_error_kind(exc: BaseException) -> str:
    """The H8 `kind` for an exception that escaped a tool handler
    into execute()'s funnel (v5.43). Conservative on purpose — only
    shapes this codebase makes unambiguous are classified:

      * TimeoutError (incl. asyncio's) and subprocess.TimeoutExpired
        (NOT a TimeoutError subclass, so named explicitly)
        -> "timeout": the work ran out of time.
      * ImportError / ModuleNotFoundError and FileNotFoundError
        -> "unavailable": a dependency, program or bundled asset the
        tool needs is missing. Handlers here report a missing USER
        file as a result string (read_file: "File not found: …"),
        never by raising, so a FileNotFoundError that reaches the
        funnel means the tool's own machinery is absent.
      * Anything else -> "handler_error": the handler itself failed.
    """
    if isinstance(exc, TimeoutError) \
            or isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, (ImportError, FileNotFoundError)):
        return "unavailable"
    return "handler_error"
