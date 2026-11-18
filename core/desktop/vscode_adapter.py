"""§7 item 9 — application adapter #3: VISUAL STUDIO CODE (D6 pick #3).

Control profile for Visual Studio Code (the editor) on Windows,
on top of the ``core/desktop/app_adapters.py`` framework (items 1–4
engines do the driving; this module only RESOLVES — it never
invokes, clicks, types, or dispatches anything).

Identity (positive evidence or nothing): the foreground must be
``Code.exe`` (matched case-insensitively) — the Microsoft
build's process. The signature names the process ONLY, and that
is deliberate, not an omission:

* The window class is NOT named, because it could not be
  sourced to a retrievable primary text in this read, and this
  programme does not invent class strings. VS Code is
  Electron-based (its main-process windows wrap Electron's
  ``BrowserWindow``), so its top-level class is a Chromium
  views class and MAY coincide with Chrome's
  ``Chrome_WidgetWin_1`` — AutoHotkey guidance notes that
  "multiple apps share classes like Chrome_WidgetWin_1" and
  that scripts must add ``ahk_exe`` to disambiguate. The class
  is FIELD-VERIFICATION OWED (Window Spy on a real VS Code
  window); if the field check confirms a class, adding it to
  the signature only strengthens matching, because the process
  already discriminates (see Collision below).
* No title substring is named: VS Code window titles are file
  and workspace names, and naming one would be wrong for every
  other file. The process alone is the positive evidence.
* A signature mismatch disables the adapter (generic fallback
  + honest note), never a forced fit.

Collision with Chrome (answered explicitly, as the D6 brief
requires): Chrome's adapter claims ``{chrome.exe,
Chrome_WidgetWin_1}``; this adapter claims ``{Code.exe}``.
The framework matches a signature only when EVERY field it
names matches, so no foreground can satisfy both processes:
a Chrome foreground matches chrome only, a VS Code foreground
matches vscode only, and a two-match ambiguity between the two
is impossible even if VS Code's class IS ``Chrome_WidgetWin_1``
— the process differs, and the process is what the framework
checks. The test suite proves this with a shared-class
foreground (``Code.exe`` + ``Chrome_WidgetWin_1`` → vscode
only, never ambiguous) and a class-only foreground
(``Chrome_WidgetWin_1`` + another process → generic).

Vocabulary: the 19 core editor jobs, as exact phrases. Menu
entries name VS Code's menu-bar items (File / Edit / View /
Go / Terminal); action entries cover jobs with no menu-bar
item (close-without-saving, delete). Exact menu text is
FIELD-VERIFICATION OWED: VS Code menu text varies by
version, and the item-3 engine matches exactly and refuses on
mismatch (fail-closed), so a renamed menu item degrades to an
honest refusal, never a wrong click. Menu text here is the
current VS Code menu as documented (File → New Text File,
Open File..., Open Folder..., Save, Save As..., Close
Editor; Edit → Undo, Redo, Find, Replace; View → Terminal,
Primary Side Bar, Editor Layout → Split Right; Go → Go to
Line/Column...; Terminal → Run Task..., Run Build Task...).
The command palette is DELIBERATELY NOT in this vocabulary:
palette commands are typed selections, not menu paths, and
no item-3 / item-4 engine drives them — "open command
palette" would resolve to a menu item that merely opens the
palette, and palette command execution is not claimed here.
"open file" / "open folder" resolve to the File menu items
that open the Open dialogs; the item-4 engine drives the
dialog once open. "delete file" resolves as an action because
deletion goes through the Explorer context menu, which the
item-3 menu-bar engine does not drive yet (see the
``explorer_context_menu`` quirk) — the resolution names the
job, it does not pretend the menu bar drives it.

Quirks: ``dirty_file_indicator`` gates "close without
saving" (a dot on the tab means unsaved changes; discarding
them is destructive, and ordinary close is guarded by the
save prompt instead); ``terminal_focus`` gates "toggle
terminal" (panel vs editor focus changes what toggle does);
``explorer_context_menu`` gates "delete file" (deletion
goes through the Explorer context menu; delete moves to the
OS trash, recoverable — permanent delete is NOT in this
vocabulary and never resolves); ``editor_layout`` gates
"split editor" (the split target group depends on the
current layout; Split Right is the default direction).
``multi_root_workspace`` and ``extension_host_prompts`` are
documented adapter knowledge and gate no entry (in a
multi-root workspace a folder name alone may be ambiguous;
extension prompts can appear over the editor and unexpected
dialogs refuse at the engine level). A quirk that stops
working refuses its entries, never a pixel workaround.

Safe/confirm: navigation, opening, plain save, close of a
clean editor, find/replace, go-to-line, terminal/sidebar
toggles, split, and undo/redo are safe. "save as" is
confirm-gated (overwriting an existing file is destructive;
the item-4 engine additionally stops with needs_confirmation
when the target exists — the item-3 destructive first-word
set deliberately excludes "Save", so this gating is the
caller-declared effectful rule, not the first-word set).
"close without saving" and "delete file" are confirm-gated
(destructive leaves). "run task" and "run build task" are
confirm-gated (task execution is effectful; the task picker
also asks, but the adapter does not rely on it). Nothing in
this vocabulary touches a password field, a CAPTCHA, or the
secure desktop — registration refuses any such entry.

G2 TERMS (read 2026-11-02, recorded via ``record_terms`` with
the citation in ``VSCODE_TERMS_EVIDENCE``):
  (1) Microsoft Software License Terms for Visual Studio Code
  (code.visualstudio.com/license, full current text read):
  installation and use rights ("You may use any number of
  copies of the software to develop and test your
  applications"); scope of license (licensed, not sold;
  comply with technical limitations; you may NOT reverse
  engineer / decompile / disassemble except as required by
  third-party open-source terms, remove / minimize / block /
  modify notices, use the software in any way against the
  law, or share / publish / rent / lease it or provide it as
  a stand-alone offering). NOTHING in the license restricts
  driving the app's own local UI at the user's direction,
  and nothing prohibits assistive-technology or automation
  use. This adapter does none of the prohibited things: it
  drives the user's own local VS Code UI at the user's
  spoken direction through the app's own menus and controls,
  on one device for one user, does not reverse-engineer VS
  Code, does not circumvent any limitation, does not remove
  notices, bundles no vendor code, and holds no credentials.
  The extension marketplace terms (aka.ms/vsmarketplace-ToU,
  referenced by license §1.4) do NOT apply here: this
  adapter builds no extension, installs nothing from the
  marketplace, and depends on no marketplace package — the
  distinction is explicit, not assumed.
  (2) Microsoft UI Automation Overview (Microsoft Learn):
  UI Automation "provides programmatic access to most user
  interface (UI) elements on the desktop, enabling assistive
  technology products such as screen readers to provide
  information about the UI to end users and to manipulate
  the UI by means other than standard input. UI Automation
  also allows automated test scripts to interact with the
  UI." This is Microsoft's own published automation
  interface, and the channel this adapter's resolution rides
  (via the item-3 / item-4 engines); the adapter depends on
  no vendor SDK and adds no new channel.
  (3) Identity evidence (see Identity above): VS Code is
  Electron-based (main-process windows wrap Electron's
  BrowserWindow); the Microsoft build's process is Code.exe
  (automation practice targets VS Code by ahk_exe Code.exe);
  Chromium-derived apps share window classes such as
  Chrome_WidgetWin_1, which is why the class is not named
  until field-verified.
  Scope conditions: local UI operation only, user-directed
  actions only, by-design walls stand, destructive leaves
  confirm-gated, trash-only delete scope. VERDICT: CLEARED
  within that scope.
  Gap disclosed: the top-level window class was not
  retrieved as a sourced string in this read (no retrieved
  source states VS Code's Win32 class directly); the
  signature is proc-only until Window Spy confirms a class
  on a real VS Code window. That gap affects signature
  strength, not the terms verdict — and it must be revisited
  before shipping, alongside the version pin.
  No real VS Code version is verified from lab evidence
  (the lab has no VS Code — no Linux editor version, no
  invented version, and no Insiders version may stand in for
  a Microsoft-build version), so ``verified_app_versions``
  is empty and ``shippability()`` is honestly False until a
  field version pin lands. That is the gate state, not a
  failure: the adapter + tests land, and shipping waits on
  the field pin.
"""

from __future__ import annotations

APP_ID = "vscode"
ADAPTER_VERSION = "1.0"

VSCODE_TERMS_EVIDENCE = (
    "G2 read 2026-11-02: (1) Microsoft Software License Terms "
    "for Visual Studio Code (code.visualstudio.com/license, full "
    "current text read): installation/use rights to develop and "
    "test applications; scope of license forbids reverse "
    "engineering / decompiling / disassembling (except as required "
    "by third-party open-source terms), removing / minimizing / "
    "blocking / modifying notices, unlawful use, and sharing / "
    "publishing / renting / leasing or stand-alone offering — none "
    "of which this adapter does: it drives the user's own local "
    "VS Code UI at the user's spoken direction through the app's "
    "own menus and controls, on one device for one user, does not "
    "reverse-engineer VS Code, does not circumvent any limitation, "
    "does not remove notices, bundles no vendor code, and holds no "
    "credentials; nothing in the license restricts local UI "
    "automation or assistive-technology use. Extension marketplace "
    "terms (aka.ms/vsmarketplace-ToU, license section 1.4) do NOT "
    "apply: this adapter builds no extension and installs nothing "
    "from the marketplace. (2) Microsoft UI Automation Overview "
    "(Microsoft Learn): UI Automation 'provides programmatic access "
    "to most user interface (UI) elements on the desktop, enabling "
    "assistive technology products such as screen readers to provide "
    "information about the UI to end users and to manipulate the UI "
    "by means other than standard input. UI Automation also allows "
    "automated test scripts to interact with the UI' — Microsoft's "
    "own published automation interface, and the channel this "
    "adapter's resolution rides (via the item-3/item-4 engines); "
    "the adapter depends on no vendor SDK and adds no new channel. "
    "(3) Identity evidence: VS Code is Electron-based (main-process "
    "windows wrap Electron's BrowserWindow); the Microsoft build's "
    "process is Code.exe (automation practice targets VS Code by "
    "ahk_exe Code.exe); Chromium-derived apps share window classes "
    "such as Chrome_WidgetWin_1, which is why the class is not named "
    "until field-verified. Scope: local UI operation only, "
    "user-directed actions only, by-design walls stand, destructive "
    "leaves confirm-gated, trash-only delete scope. Verdict: CLEARED "
    "within that scope. Gap: the top-level window class was not "
    "retrieved as a sourced string in this read; the signature is "
    "proc-only until Window Spy confirms a class on a real VS Code "
    "window — revisit before shipping, alongside the version pin."
)


def vscode_adapter_record():
    """A fresh VS Code adapter record (terms start unread, per
    the framework — callers record terms after registration,
    never assert them at it). Verified app versions are
    deliberately empty: no real VS Code version is pinned from
    lab evidence."""
    return {
        "app_id": APP_ID,
        # Proc-only by design (see module docstring): the
        # window class is field-verification owed, and the
        # process alone already discriminates against Chrome.
        "identity_signatures": [
            {"proc": "Code.exe"},
        ],
        "vocabulary": {
            "new file": {"kind": "menu",
                         "path": ["File", "New Text File"]},
            "open file": {"kind": "menu",
                          "path": ["File", "Open File..."]},
            "open folder": {"kind": "menu",
                            "path": ["File", "Open Folder..."]},
            "save file": {"kind": "menu", "path": ["File", "Save"]},
            "save as": {"kind": "menu",
                        "path": ["File", "Save As..."]},
            "close editor": {"kind": "menu",
                             "path": ["File", "Close Editor"]},
            "close file": {"kind": "menu",
                           "path": ["File", "Close Editor"]},
            "close without saving": {
                "kind": "action", "name": "close without saving",
                "quirk": "dirty_file_indicator"},
            "delete file": {"kind": "action", "name": "delete file",
                            "quirk": "explorer_context_menu"},
            "find in file": {"kind": "menu",
                             "path": ["Edit", "Find"]},
            "replace in file": {"kind": "menu",
                                "path": ["Edit", "Replace"]},
            "go to line": {"kind": "menu",
                           "path": ["Go", "Go to Line/Column..."]},
            "toggle terminal": {"kind": "menu",
                                "path": ["View", "Terminal"],
                                "quirk": "terminal_focus"},
            "run task": {"kind": "menu",
                         "path": ["Terminal", "Run Task..."]},
            "run build task": {"kind": "menu",
                               "path": ["Terminal",
                                        "Run Build Task..."]},
            "toggle sidebar": {"kind": "menu",
                               "path": ["View", "Primary Side Bar"]},
            "split editor": {"kind": "menu",
                             "path": ["View", "Editor Layout",
                                      "Split Right"],
                             "quirk": "editor_layout"},
            "undo": {"kind": "menu", "path": ["Edit", "Undo"]},
            "redo": {"kind": "menu", "path": ["Edit", "Redo"]},
        },
        "quirks": {
            "dirty_file_indicator": {
                "description": "a dot on the tab means unsaved "
                "changes; closing without saving discards them, "
                "while ordinary close is guarded by the save "
                "prompt instead.",
                "working": True},
            "terminal_focus": {
                "description": "terminal-panel vs editor focus "
                "changes what toggling the terminal does; focus "
                "must be established before toggling.",
                "working": True},
            "explorer_context_menu": {
                "description": "file deletion goes through the "
                "Explorer context menu, which the item-3 "
                "menu-bar engine does not drive yet (menu-bar "
                "roots only); the resolution names the job, and "
                "delete moves to the OS trash, recoverable — "
                "permanent delete is not in this vocabulary.",
                "working": True},
            "editor_layout": {
                "description": "the split target group depends "
                "on the current editor layout; Split Right is "
                "the default direction, and a layout mismatch "
                "refuses rather than splitting elsewhere.",
                "working": True},
            "multi_root_workspace": {
                "description": "in a multi-root workspace a "
                "folder name alone may be ambiguous; folder "
                "identity is workspace-relative, not bare-name.",
                "working": True},
            "extension_host_prompts": {
                "description": "extension prompts (trust, "
                "recommendations) can appear over the editor; "
                "unexpected dialogs refuse at the engine level "
                "rather than being dismissed.",
                "working": True},
        },
        "safe_actions": [
            "menu:File → New Text File",
            "menu:File → Open File...",
            "menu:File → Open Folder...",
            "menu:File → Save",
            "menu:File → Close Editor",
            "menu:Edit → Find",
            "menu:Edit → Replace",
            "menu:Go → Go to Line/Column...",
            "menu:View → Terminal",
            "menu:View → Primary Side Bar",
            "menu:View → Editor Layout → Split Right",
            "menu:Edit → Undo",
            "menu:Edit → Redo",
        ],
        "confirm_actions": [
            "menu:File → Save As...",
            "action:close without saving",
            "action:delete file",
            "menu:Terminal → Run Task...",
            "menu:Terminal → Run Build Task...",
        ],
        "adapter_version": ADAPTER_VERSION,
        # Deliberately empty: no real VS Code version is
        # verified from lab evidence (the lab has no VS Code;
        # no Linux editor version, invented version, or
        # Insiders version may stand in for a Microsoft-build
        # version). A field version pin is owed before shipping.
        "verified_app_versions": [],
    }


def register_vscode_adapter():
    """Register the VS Code adapter and record its G2 terms
    read. Returns (register_result, terms_result).
    Registration starts unread per the framework; the terms
    call is what clears the terms half of the gate. Version
    pinning stays open (field work), so shippability()
    remains False after this call — honestly, by design."""
    from . import app_adapters as aa  # Phase 3 S7b: adapter moved into core/desktop/ (intra-package)

    registered = aa.register(vscode_adapter_record())
    if not registered.get("ok"):
        return registered, {"ok": False, "adapter_id": None,
                            "refusal": {"code": "not-registered",
                                        "reason": "registration failed; "
                                        "terms not recorded"}}
    termed = aa.record_terms(APP_ID, terms=VSCODE_TERMS_EVIDENCE)
    return registered, termed
