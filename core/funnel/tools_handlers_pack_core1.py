"""Pack registrations moved verbatim out of core/tools_handlers.py
(Refactor Phase 1, Step 1: pack extraction).

Methods here are re-attached to ToolHandlersMixin via
inheritance, so the class surface is unchanged: every
tool's handler, signature and registration is identical.
"""


class CorePacksMixin:
    def _register_default_tools(self):
        # 1. Launch Application
        self.register(
            name="launch_application",
            description="Launch or open a desktop application by common name (e.g., notepad, calculator, chrome, spotify, explorer). Pass arguments to hand the app command-line arguments (e.g. a file path to open); Windows app links (Settings, WhatsApp) can't take arguments and will say so.",
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "The name or command of the program to launch (e.g., 'notepad', 'calc', 'chrome')"
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Command-line arguments to pass to the app, e.g. a file path or flags (default: none)"
                    }
                },
                "required": ["app_name"]
            },
            handler=self._tool_launch_app
        )

        # 2. Open URL
        self.register(
            name="open_url",
            description="Visit a website: open a web page or URL in the user's default browser — or in a named browser (chrome, edge, firefox) when the user asks for one. It opens in the system/default browser, which VYUK cannot see or drive afterwards; to search, click, type or read on a page, use browser_control (it starts VYUK's own controllable browser) instead. To read a page's text without opening it, browser_read; to save a file from a link, download_file.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The destination web address (e.g., 'https://google.com' or 'youtube.com')"
                    },
                    "browser": {
                        "type": "string",
                        "enum": ["default", "chrome", "edge", "firefox"],
                        "description": "Which browser to open it in (default: the system default browser)"
                    }
                },
                "required": ["url"]
            },
            handler=self._tool_open_url
        )

        # 3. System Time and Date
        self.register(
            name="get_current_time_date",
            description="Get the current local system date, time, and day of the week. For the time in another city use world_clock; for working out dates (days between, adding days, ages) use date_calculator.",
            parameters={"type": "object", "properties": {}},
            handler=self._tool_get_time_date
        )

        # 4. Volume Adjustment
        self.register(
            name="adjust_volume",
            description="Adjust or change master system audio volume up, down, or mute. Steps up/down or mutes, and the result reads back the final level when Windows audio is reachable; for an exact percentage use set_volume_level, for one app only use app_volume, for play/pause and track keys use media_control.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "mute"],
                        "description": "Whether to turn the volume 'up', 'down', or toggle 'mute'."
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of step increments (1 to 10). Defaults to 3.",
                    }
                },
                "required": ["action"]
            },
            handler=self._tool_adjust_volume
        )

        # 5. Lock Workstation (v5.30: absorbed lock_pc — one lock
        # tool, with the checked call and honest failure lines)
        self.register(
            name="lock_screen",
            description="Lock this PC immediately, putting it at the sign-in screen. Anything open stays open — you just have to sign back in. For sleep or shutdown instead, power_control.",
            parameters={"type": "object", "properties": {}},
            handler=self._tool_lock_screen
        )

        # 6. Store Memory Fact
        self.register(
            name="remember_user_fact",
            description="Store something the user asked you to remember — a preference, personal fact, habit, instruction, or any passing detail — into long-term persistent memory. Use this for EVERY 'remember this' request: never just say you will remember it, call this tool. Split the request into a short key and the value to remember. Facts are kept forever and recalled across days; for a note that belongs only to today's journal, use remember_today.",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category for the fact (e.g., 'preferences', 'work', 'profile', 'hardware')"
                    },
                    "key": {
                        "type": "string",
                        "description": "Key or label (e.g., 'user_name', 'favorite_editor', 'wifi_ssid')"
                    },
                    "value": {
                        "type": "string",
                        "description": "The information to remember"
                    }
                },
                "required": ["key", "value"]
            },
            handler=self._tool_remember_fact
        )

        # 7. Search Memory
        self.register(
            name="recall_memories",
            description="Search persistent long-term memory for previously stored facts or notes. Searches long-term facts and preferences; for what happened on a specific day, use recall_day.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to look up in stored memories"
                    }
                },
                "required": ["query"]
            },
            handler=self._tool_recall_memories
        )

    def _register_extended_tools(self):
        """Registers the extended commercial feature tool set."""
        # 8. Web Search
        self.register(
            name="web_search",
            description="Perform a live internet search and return a factual summary. The summary is followed by sourced results (title, source and snippet per result) so claims can be cited. Use for current events, facts, definitions. With mode='news', returns dated headlines from a news feed instead of instant answers.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "max_results": {"type": "integer", "description": "How many sourced results to list (default 5, max 10)"},
                    "mode": {"type": "string", "enum": ["web", "news"], "description": "'web' (default) for instant answers plus sourced results, or 'news' for dated headlines"}
                },
                "required": ["query"],
            },
            handler=self._tool_web_search,
        )

        # 9. Weather
        self.register(
            name="get_weather",
            description="Get current weather conditions for a city or the user's current location, in Celsius (default) or Fahrenheit. Set hourly=true to also hear the next 8 hours (temperature, and rain chance where available). For sun-safety advice built on the forecast, uv_advice; for whether rain is coming, rain_from_forecast.",
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name (e.g. 'London') or leave blank for current location"},
                    "units": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "'celsius' (default, wind in km/h) or 'fahrenheit' (wind in mph)"},
                    "hourly": {"type": "boolean", "description": "Also list the next 8 hours with temperature and rain chance (default false)"}
                },
                "required": [],
            },
            handler=self._tool_get_weather,
        )

        # 10. Set Reminder
        self.register(
            name="set_reminder",
            description="Set a reminder that VYUK speaks aloud after the specified number of minutes — or at a clock time when `at` is given ('17:30', today if ahead else tomorrow). For a plain timer use countdown; for focus work/break cycles use pomodoro; to measure elapsed time use stopwatch.",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The reminder message to speak"},
                    "minutes": {"type": "integer", "description": "How many minutes until the reminder fires (default: 5)"},
                    "at": {"type": "string", "description": "Clock time to fire at instead of minutes, 'HH:MM' 24-hour or '5:30 pm' — today if still ahead, else tomorrow"},
                },
                "required": ["message"],
            },
            handler=self._tool_set_reminder,
        )

        # 11. Read Clipboard
        self.register(
            name="read_clipboard",
            description="Read the text on the system clipboard. Long text is truncated with the total stated, secret-looking content (a lone key or token) is masked rather than shown whole, and a clipboard holding an image or files is reported as non-text.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_read_clipboard,
        )

        # 12. Write File
        self.register(
            name="write_file",
            description="Write text content to a file on disk, replacing the file (or adding to its end with append=true). Creates parent directories if needed. To change only part of an existing file use replace_in_file; to read a file back use read_file.",
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute or relative file path"},
                    "content": {"type": "string", "description": "Text content to write"},
                    "append": {"type": "boolean", "description": "true = add the content to the end of the file instead of replacing it (default false)"},
                },
                "required": ["filepath", "content"],
            },
            handler=self._tool_write_file,
        )

        # 13. Read File
        self.register(
            name="read_file",
            description="Read text content from a file on disk, one page at a time. Each answer states the file's total character count and which character range it carries; pass offset to read further into a long file. Binary files are reported as binary (with size and type), not returned as text.",
            parameters={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute or relative file path to read"},
                    "offset": {"type": "integer", "description": "Character offset to start reading from (default 0). Use the offset named at the end of a previous read_file answer to page through a long file."},
                },
                "required": ["filepath"],
            },
            handler=self._tool_read_file,
        )

        # 14. System Info
        self.register(
            name="get_system_info",
            description="Get current system resource usage: CPU percentage, RAM usage, and disk space. Live usage right now — for what hardware is installed in the PC, use hardware_info. For the heaviest processes right now, top_processes.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._tool_get_system_info,
        )

    def _register_control_tools(self):
        """Registers the Hands + Eyes control pack (core/desktop/control.py)."""
        from ..desktop import control  # Phase 3 D4a: control hub moved to core/desktop/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)

        self.register(
            name="type_text",
            description="Type text into whatever app is currently focused, as if typed on the keyboard. Focus the target window first (focus_window_by_text or launch_application); for text already on the clipboard, type_clipboard types it instead.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type into the focused app"}
                },
                "required": ["text"],
            },
            handler=control.type_text,
        )
        self.register(
            name="press_hotkey",
            description="Press a key or key combination, e.g. 'ctrl+c', 'ctrl+v', 'alt+tab', 'win+d', or a single key like 'enter'. For typing whole words and sentences, type_text; for mouse movement and clicks, mouse_control.",
            parameters={
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Key combo separated by '+' (e.g. 'ctrl+shift+esc') or a single key name"}
                },
                "required": ["keys"],
            },
            handler=control.press_hotkey,
        )
        self.register(
            name="mouse_control",
            description="Control the mouse: move to coordinates, click, double-click, right-click, or scroll. Clicks identify the control under the point via UI Automation and report what was clicked. If the target is a named control or labelled button, prefer uia_click_control or click_element — they aim by name instead of coordinates.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "scroll"], "description": "Mouse action to perform"},
                    "x": {"type": "integer", "description": "X coordinate (for move, or to position before click/scroll)"},
                    "y": {"type": "integer", "description": "Y coordinate (for move, or to position before click/scroll)"},
                    "amount": {"type": "integer", "description": "Scroll steps for the scroll action (positive scrolls up, default 3)"},
                },
                "required": ["action"],
            },
            handler=self._tool_mouse_control,
        )
        self.register(
            name="run_command",
            description="Run a shell command on the user's computer and return its output. Destructive commands (recursive delete, shutdown, reg delete) are blocked unless confirm is true; disk format/wipe commands are always refused. Long output is head-truncated by default; pass tail=true to keep the END instead (logs, build output), and max_lines to first trim the output to that many lines from the same end. Pass working_directory to run inside a specific folder — a path that isn't an existing folder is refused and nothing runs. Pass background=true to launch a long command as a managed background job and get a job id back immediately; then call again with action='poll' (output since the last poll plus running/exited state), action='wait' (block up to timeout seconds for the final output tail and exit code), action='cancel' (kill the job's whole process tree) or action='list', with job_id set and command left empty — job actions never re-run the trust layer, which is applied once, at launch.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run (leave empty when action is given)"},
                    "confirm": {"type": "boolean", "description": "Must be true to allow destructive-looking commands (default false)"},
                    "tail": {"type": "boolean", "description": "Keep the END of long output instead of the beginning (default false)"},
                    "max_lines": {"type": "integer", "description": "Trim output to at most this many lines (from the head, or the tail when tail=true); 0 = no line trim"},
                    "working_directory": {"type": "string", "description": "Folder to run the command in; must already exist (default: the app's current folder)"},
                    "background": {"type": "boolean", "description": "Launch as a managed background job and return its job id immediately instead of blocking (default false)"},
                    "action": {"type": "string", "enum": ["poll", "status", "wait", "cancel", "list"], "description": "Background-job action on job_id: poll/status, wait, cancel or list; leave unset to run a command"},
                    "job_id": {"type": "string", "description": "Background job id returned by a background=true launch (for action calls)"},
                    "timeout": {"type": "integer", "description": "For action='wait': maximum seconds to wait for the job to finish (default 30)"},
                },
                "required": ["command"],
            },
            handler=self._tool_run_command,
        )
        self.register(
            name="window_control",
            description="List open windows, or focus/minimize/maximize/close a window whose title contains the given text. Windows only. For exact halves and quarters of the screen, window_snap; to keep a window visible on top, window_always_on_top.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "focus", "minimize", "maximize", "close", "desktop_list", "desktop_switch", "desktop_move", "desktop_pin", "desktop_unpin"], "description": "Window action to perform"},
                    "title_substring": {"type": "string", "description": "Part of the window title to match (not needed for list)"},
                },
                "required": ["action"],
            },
            handler=self._tool_window_control,
        )
        self.register(
            name="file_control",
            # v5.48 (programme item 27): deletes no longer go to a
            # per-folder _nova_trash — they move to VYUK's central
            # trash root, recorded in its manifest, and can be
            # restored via control.restore_from_trash.
            description="Real file management: list a folder, find files by glob pattern, copy/move/rename files, create folders, get file info, or delete (moves to VYUK's central trash, recorded in the trash manifest and restorable, requires confirm). For reading a file's text, read_file; for searching inside files, search_file_contents.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "find", "copy", "move", "rename", "create_folder", "delete", "info"], "description": "File operation to perform"},
                    "path": {"type": "string", "description": "Source file or folder path"},
                    "destination": {"type": "string", "description": "Destination path for copy/move/rename"},
                    "pattern": {"type": "string", "description": "Glob pattern for find (e.g. '*.pdf')"},
                    "confirm": {"type": "boolean", "description": "Must be true for delete (default false)"},
                    "overwrite": {"type": "boolean", "description": "For copy/move/rename: allow replacing files that already exist at the destination (default false — existing destinations are refused and reported)"},
                },
                "required": ["action", "path"],
            },
            handler=control.file_control,
        )
        self.register(
            name="close_application",
            description="Close/terminate an application by process name (graceful terminate). Requires confirm=true. For one window of an app rather than the whole app, window_control closes a single window.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Process or app name to close (e.g. 'notepad', 'chrome')"},
                    "confirm": {"type": "boolean", "description": "Must be true to actually close the app (default false)"},
                },
                "required": ["name"],
            },
            handler=control.close_application,
        )
        self.register(
            name="media_control",
            description="Send media keys: play/pause, resume, next track, previous track, or stop. Media keys only — volume levels are adjust_volume (steps) or set_volume_level (exact %). To start music from nothing, use play_music. It steers whichever app is playing; to open a specific app first, launch_application.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"], "description": "Media action"}
                },
                "required": ["action"],
            },
            handler=control.media_control,
        )
        # v5.52 (item 4): actually STARTING music — the ladder
        # lives in core/music_player.py (YouTube Music in her
        # controllable browser first since v5.57.1 -> Spotify
        # app via its URI protocol -> Spotify web only with an
        # existing session -> local files), with playback
        # verified by audio-session evidence and honest wording
        # when it can't be confirmed. v6.2.x: a new play stops
        # whatever is playing first (single-source guard).
        self.register(
            name="play_music",
            description="Play music — starts actual playback. For ANY request to play music or a named song, use THIS tool — never browser_control on YouTube/Spotify. Ladder: YouTube Music in VYUK's browser first, then the Spotify app (spotify: search URI for a request, a plain launch for a bare one), then Spotify web ONLY if already signed in there (it is a login-walled SPA; YouTube plays logged-out), then local files in the Music folder. Stops whatever is playing first, so only one source sounds at a time. Says honestly whether playback was confirmed by audio evidence. Track keys on what's already playing are media_control.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Song, artist, genre or mood to play (e.g. 'lofi beats', 'Despacito'); empty = just play/resume something"}
                },
                "required": [],
            },
            handler=self._tool_play_music,
        )
        # v5.52 (item 8): VYUK's OWN settings, changeable from
        # chat — theme, orb, voices, reply language and safe
        # behaviour toggles, over an explicit allowlist with
        # security-shaped settings refused as Settings-page-only
        # (core/app_settings.py owns the stores + semantics).
        self.register(
            name="app_settings",
            description="Get or change VYUK's OWN settings from chat — her theme and color, orb style, live_voice (the cloud brain's Google voice), local_voice (her on-device Kokoro voice), language (reply language), and the toggles earcons, suggestions, tts_loudness, voice_auto_switch. 'Color' here means her theme colours. action 'get' with no setting lists them all. Writes go through the same stores Settings uses. Security-shaped settings (confirmation policy, failsafe, protected paths, keys) are NOT settable here — Settings-page-only by design. VOICE protocol (live sessions only): action 'set' PROPOSES instead of applying — the result names a proposal id; action 'confirm' with that id in value applies it, 'deny' declines it, 'undo' restores the last voice-applied change within a minute. Permission grants are NEVER by voice (loophole batch 4, L-28): action 'grant' with a permission id (microphone, camera, screen, files, browser, home_devices) in setting is refused and names Settings → Permissions, 'revoke' clears it, 'revoke_all' clears every session grant; a permission name in 'set' opens Settings instead. 'set' from chat applies directly.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set", "confirm", "deny", "undo", "grant", "revoke", "revoke_all"], "description": "get reads (one setting, or all), set changes one (live voice: proposes first), confirm/deny answer a proposal (proposal id in value), undo restores the last voice change, grant is refused (permissions are granted on screen only), revoke/revoke_all clear session grants"},
                    "setting": {"type": "string", "description": "theme, orb, live_voice, local_voice, language, earcons, suggestions, tts_loudness or voice_auto_switch ('color' works for theme); for grant/revoke, a permission id (microphone, camera, screen, files, browser, home_devices)"},
                    "value": {"type": "string", "description": "For set: the new value (a theme/orb/voice/language name, or on/off for a toggle). For confirm/deny: the proposal id from the propose result."}
                },
                "required": ["action"],
            },
            handler=self._tool_app_settings,
        )
        self.register(
            name="set_brightness",
            description="Set the screen brightness from 0 to 100. Windows only. To match the brightness to the room instead of a number, use screen_brightness_auto.",
            parameters={
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Brightness level 0-100"}
                },
                "required": ["level"],
            },
            handler=control.set_brightness,
        )
        self.register(
            name="power_control",
            description="Power actions: shutdown or restart the computer (both require confirm=true), or put it to sleep. For locking without powering off, lock_screen.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["shutdown", "restart", "sleep"], "description": "Power action"},
                    "confirm": {"type": "boolean", "description": "Must be true for shutdown/restart (default false)"},
                },
                "required": ["action"],
            },
            handler=control.power_control,
        )
        self.register(
            name="write_clipboard",
            description="Copy text to the system clipboard, replacing whatever is on it now. The result confirms how much was copied and how the text starts, and the copy is verified by reading the clipboard back. With append=true, the text is added after the clipboard's current text instead of replacing it.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to put on the clipboard"},
                    "append": {"type": "boolean", "description": "Add the text after the clipboard's current text instead of replacing it (default false)"}
                },
                "required": ["text"],
            },
            handler=self._tool_write_clipboard,
        )
        self.register(
            name="open_path",
            description="Open a file or folder with the operating system's default application (like double-clicking it). To start a program by name instead, launch_application; for a web address, open_url.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or folder path to open"}
                },
                "required": ["path"],
            },
            handler=control.open_path,
        )
        self.register(
            name="take_screenshot",
            description="Capture a screenshot of the screen. With no save_path it is a look: the frame is shown to the model when the current session can view images, delivered as screen text to a text-only session, and reported honestly when no session can receive it. Pass save_path to save it to a file instead (a save shows the model nothing). Optionally wait first (for menus and hover states), mark where the cursor is, and pass mark_x and mark_y together to also draw a ring around that screen point. Scope is 'foreground' by default (the focused window); pass scope 'whole_desktop' for an explicit whole-desktop capture.",
            parameters={
                "type": "object",
                "properties": {
                    "save_path": {"type": "string", "description": "Optional file path to also save the screenshot to"},
                    "delay_seconds": {"type": "number", "description": "Wait this long before capturing (0–10, for menus/toasts that vanish)"},
                    "include_cursor": {"type": "boolean", "description": "Mark the cursor's position with an arrow in the image"},
                    "mark_x": {"type": "integer", "description": "X screen pixel to ring-mark (must be given with mark_y; default -1 = no mark)"},
                    "mark_y": {"type": "integer", "description": "Y screen pixel to ring-mark (must be given with mark_x; default -1 = no mark)"},
                    "scope": {"type": "string", "enum": ["foreground", "whole_desktop"], "description": "What to capture: 'foreground' (the focused window, the default) or 'whole_desktop' (the entire desktop)"},
                    "monitor": {"type": "string", "description": "One monitor to capture (index like '2', device name, 'main'/'primary', or 'next' from the focused window's monitor). Requires scope 'whole_desktop' — with 'foreground' the call refuses, since a monitor capture is a crop of the whole-desktop frame. An unknown monitor refuses honestly, naming the monitors present; it never falls back to another monitor"}
                },
                "required": [],
            },
            handler=self._tool_take_screenshot,
        )
        self.register(
            name="screen_look",
            description="Look at the screen right now in one call: captures a frame, shows it to the model when the current session can view images, and always returns an OCR summary of the visible text plus the active window's title. Scope is 'foreground' by default (the focused window); pass scope 'whole_desktop' for an explicit whole-desktop capture. For saving a screenshot to a file instead, take_screenshot; for OCR alone, ocr_screen.",
            parameters={"type": "object", "properties": {"scope": {"type": "string", "enum": ["foreground", "whole_desktop"], "description": "What to capture: 'foreground' (the focused window, the default) or 'whole_desktop' (the entire desktop)"}}, "required": []},
            handler=self._tool_screen_look,
        )
        self.register(
            name="get_active_window",
            description="Get the title of the window the user is currently focused on. Windows only. For the controls inside that window use get_window_controls; to switch windows use focus_window_by_text.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=control.get_active_window,
        )
        self.register(
            name="ocr_screen",
            description="Read the text currently visible on screen using OCR (her bundled text-reading engine). For the text inside an image file, ocr_file.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=control.ocr_screen,
        )
        self.register(
            name="get_activity_log",
            description="Show VYUK's recent tool activity log (what tools were run and whether they succeeded); pass contains to see only the lines mentioning a word. For Windows' own event history instead, system_event_log.",
            parameters={
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "description": "How many recent log lines to return (default 20)"},
                    "contains": {"type": "string", "description": "Optional: only lines containing this text (case-insensitive)"}
                },
                "required": [],
            },
            handler=control.get_activity_log,
        )

