"""§7 item 9 — application adapter #2: WINDOWS EXPLORER (D6 pick #2).

Control profile for Windows Explorer (the Explorer-class file
manager) on Windows, on top of the ``core/desktop/app_adapters.py``
framework (items 1–4 engines do the driving; this module only
RESOLVES — it never invokes, clicks, types, or dispatches
anything).

Identity (positive evidence or nothing): the foreground must be
``explorer.exe`` (matched case-insensitively) in a top-level
file window. TWO window classes are named, because Explorer
kept both for compatibility: ``CabinetWClass`` (folder pane
hidden) and ``ExplorerWClass`` (folder pane visible). That pair
is sourced, not guessed: Raymond Chen, "Why does the class
name for Explorer change depending on whether you open it with
/e?" (The Old New Thing, Microsoft dev blog, 2015-06-19) —
Explorer's original name was Cabinet and the name lingers in
the programmatic class name; the two frame classes were merged
into one implementation, but "for compatibility, therefore,
Explorer windows still use the old class names"; a window
opened with the folder pane hidden is CabinetWClass, with the
folder pane visible is ExplorerWClass. These signatures cover
FILE WINDOWS ONLY: the desktop shell window (Progman /
WorkerW) and the taskbar (Shell_TrayWnd) are different classes
and never match, so the adapter can never claim the desktop
or the taskbar as a file window. No title substring is named:
Explorer window titles are folder names and paths, and naming
one would be wrong for every other folder. A signature
mismatch disables the adapter (generic fallback + honest
note), never a forced fit.

Vocabulary: the 16 core file-manager jobs, as exact phrases.
Menu entries name the Windows 11 command-bar flows (New →
Folder, Copy, Rename, Delete, Properties); action entries
cover jobs with no single command (navigation, selection,
search, sorting, opening, new window) and jobs whose
parameters arrive at call time (the file to open, the search
query — the adapter names the job, never a fixed filename).
Exact command-bar text is FIELD-VERIFICATION OWED: Explorer
command text varies by Windows version (Windows 10 used a
ribbon: Home tab with New folder / Rename / Delete; Windows
11 replaced it with the command bar), and the item-3 engine
matches exactly and refuses on mismatch (fail-closed), so a
renamed command degrades to an honest refusal, never a wrong
click. The ``command_bar_layout`` quirk records that
divergence.

Quirks: ``command_bar_layout`` gates every menu entry (Win11
command bar vs Win10 ribbon — a layout mismatch refuses);
``rename_inline_edit`` gates "rename" (rename opens an inline
edit on the selected item's name; Enter commits, Escape
cancels; with extensions hidden only the stem is edited);
``recycle_bin_default`` gates "delete" (Delete sends to the
Recycle Bin, recoverable — permanent delete with Shift+Delete
is NOT in this vocabulary and never resolves);
``cut_paste_pair`` gates "move" (move is cut followed by paste
in the destination; the paste must verify before the move
counts as done); ``navigation_vs_view`` gates the navigation
actions (go up / back / forward navigate the current folder
view, not the navigation-pane tree selection).
``folder_pane_classes`` and ``single_process_many_windows``
are documented adapter knowledge and gate no entry (both file-
window classes are file windows; explorer.exe hosts many
windows as threads, so window identity must come from the
handle, never the process alone). A quirk that stops working
refuses its entries, never a pixel workaround.

Safe/confirm: navigation, selection, search, sorting,
opening, new-folder creation, copying, and properties are
safe. "rename", "move", and "delete" are confirm-gated.
Rename is gated for every rename, not just rename-over-
existing: at resolve time the adapter cannot know whether
the target name already exists, and a voice-driven rename of
the wrong item is a real harm, so confirmation comes first.
Delete is in the item-3 C-14 destructive first-word set and
is confirm-gated on top of the Recycle-Bin-only scope.
Nothing in this vocabulary touches a password field, a
CAPTCHA, or the secure desktop — registration refuses any
such entry — and nothing here formats, empties the Recycle
Bin, or deletes permanently.

G2 TERMS (read 2026-11-02, recorded via ``record_terms`` with
the citation in ``EXPLORER_TERMS_EVIDENCE``):
  (1) Microsoft UI Automation Overview (Microsoft Learn):
  UI Automation "provides programmatic access to most user
  interface (UI) elements on the desktop, enabling assistive
  technology products such as screen readers to provide
  information about the UI to end users and to manipulate the
  UI by means other than standard input. UI Automation also
  allows automated test scripts to interact with the UI."
  This is Microsoft's own published automation interface,
  and driving Explorer through it at the user's spoken
  direction is exactly the documented use (assistive
  technology + automated test scripts). The adapter depends
  on no vendor SDK and adds no new channel: resolution rides
  the item-3 / item-4 engines, which use the OS-provided
  accessibility surface.
  (2) Microsoft Software License Terms for Windows (Windows
  10 terms text): the restrictions reserve ungranted rights
  and forbid, among other things, working around technical
  limitations, reverse engineering / decompiling /
  disassembling, and server / simultaneous multi-user use.
  This adapter does none of those: it drives the user's own
  local Explorer UI at the user's spoken direction through
  the app's own commands, on one device for one user, does
  not reverse-engineer Explorer, does not circumvent any
  limitation, bundles no vendor code, and holds no
  credentials. Explorer has no separate automation terms of
  its own — as part of Windows it is governed by the Windows
  license, which is what was read.
  (3) Window-class evidence (see Identity above) + the
  ShellWindows collection (Microsoft Learn, ShellWindows
  object: "a collection of the open windows that belong to
  the Shell"), confirming Explorer windows are Shell
  windows distinct from the desktop shell.
  Scope conditions: local UI operation only, user-directed
  actions only, by-design walls stand, destructive leaves
  confirm-gated, Recycle-Bin-only delete scope. VERDICT:
  CLEARED within that scope.
  Gap disclosed: a current Windows-11-specific license text
  was not retrieved as a separate primary document in this
  read (the Windows 10 terms text was read; the Windows 11
  terms carry the same restrictions family, but that is an
  inference, not a retrieved Windows 11 text). If a
  Windows-11-specific term restricting local UI automation
  surfaces, this verdict must be revisited before shipping.
  No real Explorer version is verified from lab evidence
  (the lab is Linux — Explorer's version tracks the Windows
  build, and no Linux file-manager version or invented
  Windows build may stand in for one), so
  ``verified_app_versions`` is empty and ``shippability()``
  is honestly False until a field version pin lands. That is
  the gate state, not a failure: the adapter + tests land,
  and shipping waits on the field pin.
"""

from __future__ import annotations

APP_ID = "explorer"
ADAPTER_VERSION = "1.0"

EXPLORER_TERMS_EVIDENCE = (
    "G2 read 2026-11-02: (1) Microsoft UI Automation Overview "
    "(Microsoft Learn): UI Automation 'provides programmatic access "
    "to most user interface (UI) elements on the desktop, enabling "
    "assistive technology products such as screen readers to provide "
    "information about the UI to end users and to manipulate the UI by "
    "means other than standard input. UI Automation also allows "
    "automated test scripts to interact with the UI' — Microsoft's own "
    "published automation interface, and the channel this adapter's "
    "resolution rides (via the item-3/item-4 engines); the adapter "
    "depends on no vendor SDK and adds no new channel. (2) Microsoft "
    "Software License Terms for Windows (Windows 10 terms text): "
    "restrictions forbid working around technical limitations, reverse "
    "engineering / decompiling / disassembling, and server / "
    "simultaneous multi-user use — none of which this adapter does: "
    "it drives the user's own local Explorer UI at the user's spoken "
    "direction through the app's own commands, on one device for one "
    "user, does not reverse-engineer Explorer, does not circumvent any "
    "limitation, bundles no vendor code, and holds no credentials; "
    "Explorer has no separate automation terms — as part of Windows it "
    "is governed by the Windows license. (3) Window-class evidence: "
    "CabinetWClass (folder pane hidden) + ExplorerWClass (folder pane "
    "visible) per Raymond Chen, The Old New Thing (Microsoft dev blog, "
    "2015-06-19); ShellWindows collection (Microsoft Learn) confirms "
    "Explorer windows are Shell windows distinct from the desktop "
    "shell. Scope: local UI operation only, user-directed actions only, "
    "by-design walls stand, destructive leaves confirm-gated, "
    "Recycle-Bin-only delete scope. Verdict: CLEARED within that scope. "
    "Gap: a current Windows-11-specific license text was not retrieved "
    "as a separate primary document in this read (Windows 10 terms text "
    "read instead); if a Windows-11-specific term restricting local UI "
    "automation surfaces, this verdict must be revisited before "
    "shipping."
)


def explorer_adapter_record():
    """A fresh Windows Explorer adapter record (terms start
    unread, per the framework — callers record terms after
    registration, never assert them at it). Verified app
    versions are deliberately empty: no real Explorer version
    is pinned from lab evidence."""
    return {
        "app_id": APP_ID,
        "identity_signatures": [
            {"proc": "explorer.exe", "window_class": "CabinetWClass"},
            {"proc": "explorer.exe", "window_class": "ExplorerWClass"},
        ],
        "vocabulary": {
            "open folder": {"kind": "dialog", "action": "folder"},
            "go up": {"kind": "action", "name": "go up",
                      "quirk": "navigation_vs_view"},
            "go back": {"kind": "action", "name": "go back",
                        "quirk": "navigation_vs_view"},
            "go forward": {"kind": "action", "name": "go forward",
                           "quirk": "navigation_vs_view"},
            "new folder": {"kind": "menu", "path": ["New", "Folder"],
                           "quirk": "command_bar_layout"},
            "rename": {"kind": "menu", "path": ["Rename"],
                       "quirk": "rename_inline_edit"},
            "copy": {"kind": "menu", "path": ["Copy"],
                     "quirk": "command_bar_layout"},
            "move": {"kind": "action", "name": "move",
                     "quirk": "cut_paste_pair"},
            "delete": {"kind": "menu", "path": ["Delete"],
                       "quirk": "recycle_bin_default"},
            "search in folder": {"kind": "action",
                                 "name": "search in folder"},
            "sort by name": {"kind": "action", "name": "sort by name"},
            "sort by date": {"kind": "action", "name": "sort by date"},
            "select all": {"kind": "action", "name": "select all"},
            "open file": {"kind": "action", "name": "open file"},
            "show properties": {"kind": "menu", "path": ["Properties"],
                                "quirk": "command_bar_layout"},
            "open new window": {"kind": "action",
                                "name": "open new window"},
        },
        "quirks": {
            "command_bar_layout": {
                "description": "menu paths in this vocabulary are "
                "the Windows 11 command-bar flows; Windows 10 "
                "used a ribbon (Home tab) with different paths, "
                "so a layout mismatch refuses rather than "
                "guessing a command.",
                "working": True},
            "rename_inline_edit": {
                "description": "rename opens an inline edit on the "
                "selected item's name; Enter commits, Escape "
                "cancels; with extensions hidden only the stem "
                "is edited.",
                "working": True},
            "recycle_bin_default": {
                "description": "Delete sends to the Recycle Bin, "
                "recoverable; permanent delete (Shift+Delete) "
                "is not in this vocabulary and never resolves.",
                "working": True},
            "cut_paste_pair": {
                "description": "move is cut followed by paste in "
                "the destination; the paste must verify before "
                "the move counts as done.",
                "working": True},
            "navigation_vs_view": {
                "description": "go up / back / forward navigate "
                "the current folder view, not the navigation-"
                "pane tree selection.",
                "working": True},
            "folder_pane_classes": {
                "description": "file windows use CabinetWClass "
                "(folder pane hidden) or ExplorerWClass "
                "(folder pane visible); the desktop shell "
                "(Progman / WorkerW) and the taskbar "
                "(Shell_TrayWnd) are different classes and "
                "never match.",
                "working": True},
            "single_process_many_windows": {
                "description": "explorer.exe hosts many windows "
                "as threads; window identity must come from "
                "the handle, never the process alone.",
                "working": True},
        },
        "safe_actions": [
            "dialog:folder",
            "action:go up",
            "action:go back",
            "action:go forward",
            "menu:New → Folder",
            "menu:Copy",
            "action:search in folder",
            "action:sort by name",
            "action:sort by date",
            "action:select all",
            "action:open file",
            "menu:Properties",
            "action:open new window",
        ],
        "confirm_actions": [
            "menu:Rename",
            "action:move",
            "menu:Delete",
        ],
        "adapter_version": ADAPTER_VERSION,
        # Deliberately empty: no real Explorer version is
        # verified from lab evidence (Explorer's version tracks
        # the Windows build; the lab is Linux). A field
        # version pin is owed before shipping.
        "verified_app_versions": [],
    }


def register_explorer_adapter():
    """Register the Windows Explorer adapter and record its G2
    terms read. Returns (register_result, terms_result).
    Registration starts unread per the framework; the terms
    call is what clears the terms half of the gate. Version
    pinning stays open (field work), so shippability()
    remains False after this call — honestly, by design."""
    from . import app_adapters as aa  # Phase 3 S7b: adapter moved into core/desktop/ (intra-package)

    registered = aa.register(explorer_adapter_record())
    if not registered.get("ok"):
        return registered, {"ok": False, "adapter_id": None,
                            "refusal": {"code": "not-registered",
                                        "reason": "registration failed; "
                                        "terms not recorded"}}
    termed = aa.record_terms(APP_ID, terms=EXPLORER_TERMS_EVIDENCE)
    return registered, termed
