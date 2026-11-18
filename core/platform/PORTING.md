# PORTING.md — writing a new platform backend for VYUK

Status (corrected as of v5.92.0; originally v5.45, P-3): three
backends are registered — `core/platform/windows.py`
(`WindowsBackend`), `core/platform/macos.py` (`MacBackend`,
EXPERIMENTAL, since v5.50), and `core/platform/linux.py`
(`LinuxBackend`, EXPERIMENTAL, since v5.50). The macOS and Linux
backends exist but are unvalidated: they are contract-tested
only against faked OS seams and have never run on a real Mac or
a real Linux desktop, so they are neither absent nor
supported. This document is the contract a port
must satisfy, written from the Windows implementation's actual
behaviour.

## The seam

- `core/platform/base.py` — `PlatformBackend` (ABC) + the
  `AREAS` map. A backend that misses a method cannot be
  instantiated.
- `core/platform/__init__.py` — `get_backend()` (cached, one
  instance per process) and the registry `_BACKENDS`.
- `core/platform/windows.py` — the Windows implementation:
  the pure geometry helpers are physically moved there
  (`_clamp`, `_to_pixels`, `_norm1000_to_pixels`, `_fit_within`;
  the legacy modules re-export the same callables); every other
  method **delegates** to the pre-existing function in
  `core/cursor.py` / `core/control.py` / `core/uia.py` /
  `core/desktop/vision.py` (plus `skills_window_control._foreground_process_name`
  for the foreground process name), resolved through the module
  attribute at call time.
- Legacy modules stay the import paths user code and the test
  suites use. Nothing outside this package needs to change when
  a backend is added.

## Platform detection / override mechanism

1. `get_backend()` asks `_requested_platform()`:
   `os.environ["VYUK_PLATFORM"]` when set and non-blank, else
   `sys.platform`.
2. The raw value is normalised: `win*` → `"windows"`;
   `"darwin"` / `mac*` → `"darwin"`; `linux*` → `"linux"`;
   anything else passes through lower-cased.
3. `_BACKENDS[requested]` is looked up; an unregistered
   platform falls back to `WindowsBackend`, because the
   delegated pre-seam code is itself import-safe and fail-soft
   off-Windows. Since v5.50, `darwin` and `linux` no longer
   fall back: they resolve to the EXPERIMENTAL `MacBackend`
   and `LinuxBackend` (see the Status note above), which are
   contract-tested only against faked OS seams and have never
   run on a real Mac or Linux desktop. A stub backend that
   returned invented successes would be a behaviour change,
   so none exists.
4. The instance records `backend.name` (what serves, e.g.
   `"windows"`) and `backend.requested_platform` (what was
   asked for). The two differing is the honest signal that no
   native backend serves that platform — for `darwin` and
   `linux` the experimental backend serves, so the names
   match; for any other platform the fallback serves, so
   they differ.
5. `reset_backend_cache()` drops the cached instance — tests
   that change `VYUK_PLATFORM` / `sys.platform` use it;
   production never calls it.
6. To register a port for a new platform: add its backend
   to `_BACKENDS` (`darwin` and `linux` are already
   registered, as EXPERIMENTAL backends, since v5.50).
   Nothing else changes; `get_backend()` starts returning
   it on that platform (and under the env override).

## Behavioural contracts (all backends, all platforms)

- **Byte-identical receipts.** Tool-shaped methods return the
  same human-readable strings the pre-seam modules returned.
  Never reword, never add a platform suffix.
- **Unknown is unknown.** A probe that cannot determine its
  answer returns `None` — never a guessed `False`, `""`, `0`,
  or an empty success. Callers phrase "I don't know" from
  `None`.
- **Password honesty (hard rule).** `is_password_control(el)`
  returns `True` / `False` only when the platform accessibility
  API actually answered. When the property cannot be read the
  return is `None` — UNKNOWN, never `False`.
  `core/input_guard.py` turns UNKNOWN into a typing refusal;
  a backend that coerces unknown → `False` silently opens the
  password gate. The same rule applies to the fallback in
  `input_guard`: the name-regex hint may only decide when the
  accessibility stack is unavailable *entirely*.
- **Typed secrets are never echoed.** `uia_type_text` results
  and logs must never contain the typed text.
- **Fail soft where the original failed soft.** Methods whose
  Windows original returns an "unavailable / only on Windows"
  string must do the equivalent on the new platform when the
  capability is genuinely absent — and must say so, not fake
  success. The single exception allowed to raise is
  `enum_windows()` (its original raises off-Windows); guarded
  callers check the platform first, exactly as
  `window_control` does.
- **Import-safe, always.** Importing the backend module and
  calling `get_backend()` must work headless, with none of the
  platform libraries installed. Platform imports happen lazily
  inside methods, guarded.
- **Geometry is true pixels.** Coordinates are physical screen
  pixels in virtual-desktop space (secondary monitors may sit
  at negative coordinates). A backend on a scaled display
  (Retina, Windows 125 %+) must handle the
  logical/physical conversion internally so this contract
  holds — on Windows that is `_ensure_dpi_aware` in
  `core/cursor.py` plus the `(dim - 1)` fraction mapping.
- **The bounds fence is not optional.** Every dispatched
  move/click target passes `fence_point` semantics: clamp into
  the virtual-screen bounds; unknown bounds pass the point
  through; non-numeric input passes through untouched for the
  caller's own validation to report.

## Exact method list

Signatures mirror `core/platform/base.py`. Return shapes given
per method; "str" results are the tool receipts described
above.

### Area 1 — input

| Method | Contract |
|---|---|
| `type_text(text: str) -> str` | Type into the focused app. Empty/None → the "Nothing to type" sentences. Password gate (`input_guard.typing_blocked_reason`) and failsafe gate run before any keystroke. Elevated/undeliverable targets must be reported as undelivered, never as typed. |
| `press_hotkey(keys: str) -> str` | One key or `+`-combo. Every pressed key is released in a `finally`, even on mid-combo failure (a stuck modifier is a user-visible injury). Unknown key names are refused by name. |
| `mouse_control(action: str, x: int = None, y: int = None, amount: int = 3) -> str` | Legacy hand: `move`/`click`/`double_click`/`right_click`/`scroll`. Exactly one of x/y is a caller error, never a silent click at the current position. |
| `cursor_control(action: str = "", x=None, y=None, fx=None, fy=None, direction: str = "", pixels=None, amount=None, duration=None, corner: str = "", button: str = "", coords: str = "pixels") -> str` | The cursor tool dispatcher; `coords="norm1000"` converts 0..1000 model coordinates to true pixels before dispatch. |
| `get_cursor_position() -> str` | Position in true pixels + screen size + percentages; honest "screen size unknown" / unreadable variants. |
| `move_cursor(x=None, y=None, fx=None, fy=None, relative=None, duration: float = 0.6) -> str` | Smooth (non-teleport) glide; aborts if the human grabs the mouse mid-glide. Ambiguous/mixed target forms are refused before any device is touched. |
| `click_cursor(button: str = "left", count: int = 1, x=None, y=None, fx=None, fy=None) -> str` | Click here or glide-then-click. The landed point is identified via `describe_point` (accessibility area) for the receipt; identification never blocks, moves, or runs while the failsafe is tripped. |
| `scroll_cursor(amount, x=None, y=None, horizontal: bool = False) -> str` | Amount clamped to −20..20; 0 is a no-op sentence. |
| `drag_cursor(x1, y1, x2, y2, duration: float = 0.8) -> str` | Button held along the path; button always released in a `finally`, including on takeover abort. |
| `fence_point(x, y, bounds=None)` | → clamped `(int, int)`; see the fence contract above. |
| `screen_size()` | → `(width, height)` true pixels, or `None`. |
| `virtual_bounds()` | → `(min_x, min_y, max_x, max_y)` inclusive virtual-desktop bounds, or `None`. |

Plus the pure geometry callables the Windows backend exposes
as attributes (same objects the legacy modules re-export):
`clamp(value, lo, hi)`, `to_pixels(fx, fy, w, h)`,
`norm1000_to_pixels(nx, ny, w, h)`. A port may reuse these
verbatim — they are platform-neutral.

### Area 2 — windows

| Method | Contract |
|---|---|
| `foreground_title()` | → foreground window title `str`, or `None` (no foreground window / untitled / API failure). `None` is unknown, never `""`. |
| `get_active_window() -> str` | Tool receipt naming the active window, or the platform-unavailable sentence. |
| `window_control(action: str, title_substring: str = "") -> str` | `list`/`focus`/`minimize`/`maximize`/`close`. Acting actions enumerate first: exact-title beats substring; multiple candidates → refusal listing them, never a silent first match. Results are verified against the OS where a check exists (focus read-back, iconic/zoomed state, close polled — a close *request* is not a close). |
| `enum_windows()` | → `[(hwnd, title)]` visible titled windows. The one method allowed to raise off its home platform. |
| `foreground_window_rect()` | → `(left, top, right, bottom)` virtual-screen pixels, or `None`. Callers treat `None` as "capture/act on the full desktop". |
| `foreground_process_name()` | → executable name of the foreground window's process, or the original's unknown value (`None`). Never invent a name. |
| `close_application(name: str, confirm: bool = False) -> str` | Graceful process termination by name; confirmation-gated; reports matched/closed counts honestly, including "matched but none could be closed". |

### Area 3 — accessibility

| Method | Contract |
|---|---|
| `uia_read_tree(window_title: str = "", max_depth: int = 3, max_nodes: int = 80) -> str` | Bounded tree read. A window that died / an ambiguous title match is worded as such — never as "no controls found". |
| `uia_find_control(name: str, window_title: str = "", control_type: str = "") -> str` | Reports each match's name, class, control type and rectangle; scan/match caps are stated in the result when hit. |
| `uia_click_control(name: str, window_title: str = "", control_type: str = "") -> str` | States how many matched, which one was clicked (name + rectangle), and which were skipped as unusable (disabled/hidden/zero-size). Unknown state reads as usable — only positively-known bad state skips. |
| `uia_type_text(name: str, text: str, window_title: str = "", clear_first: bool = True) -> str` | Types into the named field; the password gate applies; the text is never echoed (see contracts). |
| `describe_point(x: int, y: int)` | → `'"Name" (Type) in WindowTitle'`-shaped descriptor, window-only variant, or `None`. Never raises. |
| `focused_element()` | → platform wrapper for the focused element, or `None`. Never raises. This is the seam `core/input_guard.py` builds the shared password rule on (via `core/uia._focused_element`). |
| `is_password_control(el)` | → `True`/`False`/`None`; `None` = UNKNOWN, never coerced (see the hard rule above). Windows reads UIA property 30019 (IsPassword). |
| `focused_control_label()` | → `'"Name" (Type)'` or `None`. |
| `desktop_snapshot()` | → `{"fg": title\|None, "focus": label\|None}`, or `None` when *nothing* could be observed. Never raises. |

### Area 4 — screenshot

| Method | Contract |
|---|---|
| `capture_screen(max_dim: int = 1024, quality: int = 80, include_cursor: bool = False, mark: tuple[int, int] \| None = None, full_screen: bool = False)` | → `(jpeg_bytes, mime)`. Defaults to the foreground window's rectangle (full desktop when `full_screen=True` or nothing is focused). Records geometry for `last_capture_info()`. May raise what the grab raises — failure wording is the caller's. |
| `capture_native_image(full_screen: bool = False)` | → `(PIL Image, meta)` at native resolution, never thumbnailed/JPEG'd. `meta` keys: `screen_width/height`, `image_width/height`, `cropped`, `crop_left/top/width/height`. If the grab ignores the crop bbox, crop in PIL — the geometry record must never lie. |
| `last_capture_info()` | → a *copy* of the last capture's geometry dict (same keys as `meta` plus `max_dim`), or `None` before any capture. |
| `capture_geometry_line(info: dict \| None = None) -> str` | One sentence: true screen size, sent image size, scaled-vs-matching, plus the crop origin/size when cropped. `""` when nothing honest can be said. |
| `take_screenshot(save_path: str = "", delay_seconds: float = 0, include_cursor: bool = False) -> str` | Tool receipt; delay clamped 0..10 s; save failures are reported as captured-but-unsaved with the byte count. |
| `virtual_screen_bounds()` | → `(left, top, right, bottom)` of the whole virtual desktop, or `None`. |

Plus the pure helper attribute `fit_within(width, height,
max_dim)` (thumbnail-size arithmetic) — platform-neutral, reuse
verbatim.

## What a macOS backend must provide

Implement every method above against:

- **Input** — Quartz Event Services / CGEvent
  (`CGEventCreateKeyboardEvent`, `CGEventCreateMouseEvent`,
  `CGEventPost`), or `pynput` (which itself wraps Quartz).
  Typing arbitrary Unicode needs the CGEvent keyboard-string
  path (`CGEventKeyboardSetUnicodeString`), not VK codes —
  the Windows VK fallback table has no macOS analogue.
  Requires the Accessibility permission (System Settings →
  Privacy & Security → Accessibility); without it input is
  silently dropped by the OS — the backend must detect the
  untrusted state (`AXIsProcessTrusted`) and say input is
  undeliverable, mirroring the Windows elevated-window honesty
  rule, instead of claiming success.
- **Windows** — `NSWorkspace.runningApplications` +
  Accessibility `AXUIElement` window lists
  (`kAXWindowsAttribute`), `AXUIElementSetAttributeValue` for
  position/size, `NSRunningApplication.activate` for focus.
  There is no global "foreground window title" API: derive it
  from the frontmost app's focused AX window
  (`kAXFocusedWindowAttribute` → `kAXTitleAttribute`).
  Process name of a window = the owning app's
  `localizedName`/`bundleIdentifier` via the window's PID
  (`CGWindowListCopyWindowInfo` gives owner PID + name).
- **Accessibility** — the AXUIElement API is the UIA
  equivalent: tree read = recursive `kAXChildrenAttribute`
  walk (same depth/node caps); find = match on
  `kAXTitleAttribute`/`kAXDescriptionAttribute` + `kAXRole`;
  click = `AXUIElementPerformAction(kAXPressAction)`; type =
  set `kAXValueAttribute` or focus + synthetic typing;
  focused element = `kAXFocusedUIElementAttribute` on the
  system-wide element. **IsPassword equivalent:** a secure
  text field has role `AXSecureTextField` — that is the
  `True` answer; a readable non-secure field is `False`; any
  element whose role cannot be read is `None` (UNKNOWN —
  never `False`).
- **Screenshot** — `CGWindowListCreateImage` /
  ScreenCaptureKit (`screencapture` CLI is a stopgap, not a
  backend). Requires the Screen Recording permission; a
  denied permission yields a wallpaper-only image on modern
  macOS, so the backend must detect denial (e.g.
  `CGPreflightScreenCaptureAccess`) and fail honestly rather
  than ship a desktop-background "capture". Retina: geometry
  is in points at the AX/Quartz layer and pixels at the image
  layer — convert so the true-pixels contract holds.
- **DPI/geometry** — `CGDisplayBounds` /
  `NSScreen.frame` (note the flipped y origin in AppKit
  coordinates vs Quartz — the seam's contract is top-left
  origin pixels).

## What a Linux backend must provide

Implement every method above against AT-SPI2 + the display
server, with these caveats stated in results where they bite:

- **Accessibility** — AT-SPI2 (`pyatspi` or `dasbus` over
  D-Bus) is the UIA equivalent: tree = the desktop's
  accessible children; find = name/role matching; click =
  the `Action` interface (`do_action`); type = the
  `EditableText` interface or focus + synthetic keys;
  focused element = `getState` containing `STATE_FOCUSED`
  along the active window's tree. **IsPassword equivalent:**
  AT-SPI has no direct IsPassword property — the convention
  is the `text` role combined with the toolkit's password
  indication (GtkEntry visibility=False; in AT-SPI terms an
  object whose role is `password text`). When the role is
  `password text` → `True`; a readable editable text object →
  `False`; anything where the role/state cannot be read →
  `None` (UNKNOWN, never `False`).
- **X11** — input via XTest (`python-xlib`, or `pynput` which
  wraps it); windows via EWMH (`_NET_ACTIVE_WINDOW`,
  `_NET_CLIENT_LIST`, `_NET_WM_NAME`, `_NET_WM_PID` →
  `/proc/<pid>/exe` for the process name); screenshots via
  XGetImage / `PIL.ImageGrab` (works on X11).
- **Wayland caveats (must be surfaced, not hidden)** —
  there is no global input injection, no global window list,
  and no cross-app screen capture by design:
  - Input injection requires the compositor's cooperation:
    `libei`/the RemoteDesktop portal, or a virtual-keyboard
    protocol (wlroots compositors only). On a stock GNOME
    Wayland session without portal consent, `type_text` /
    `press_hotkey` / mouse methods must return the honest
    unavailable sentence.
  - Window list/focus/close requires a compositor protocol
    (foreign-toplevel management on wlroots; nothing
    equivalent on stock GNOME) — `window_control` /
    `enum_windows` may be genuinely unavailable; say so.
  - Screenshots go through the XDG ScreenCast/Screenshot
    portal (`org.freedesktop.portal.Screenshot`) — user
    consent prompt included; geometry may be per-output
    with fractional scaling factors that must be undone to
    honour the true-pixels contract.
  - AT-SPI works on Wayland for cooperating toolkits, so the
    accessibility area is the most portable of the four —
    but focus tracking still depends on the toolkit.
- **Detection** — a Linux backend should branch on
  `XDG_SESSION_TYPE` (`x11` vs `wayland`) at call time, not
  import time, and name the active path in its unavailable
  sentences ("not available on Wayland without the portal").

## Adding a backend — checklist

1. New module `core/platform/<name>.py` with a
   `PlatformBackend` subclass implementing all 34 methods.
2. Lazy, guarded platform imports inside methods only;
   module import must succeed on every OS.
3. Register it in `_BACKENDS` in `core/platform/__init__.py`.
4. The P-3 suite (`test_v545_p3.py`) must pass with
   `VYUK_PLATFORM` pointed at the new platform for the
   selection checks, and the full regression surface must be
   byte-identical on Windows (the shipping platform) as
   before the port landed.
