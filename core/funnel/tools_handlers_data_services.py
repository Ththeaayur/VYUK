"""Data-service tool handlers (skills split B49, part 2 of 2).

Moved verbatim from core/funnel/tools_handlers.py: the weather /
reminder / file / system / email / curl / replay / capture /
research / personal / health / business methods, load_plugins, and
the read-file class constants their sole user resolves through self.
"""

# INTENTIONAL (skills split B49): imports exactly the names the moved code
# resolves by bare name (prover-verified; see prove_tools_handlers_p2.py).
import datetime
import json
import os
from .. import injection
from ..confirm_gate_scrub import _scrub_text
from .tools_gate import _PLUGIN_GATE_CLASS
from .tools_handlers_frame_launch import _clipboard_secret_shown

class ToolHandlersDataServicesMixin:
    def _tool_get_weather(self, location: str = "",
                          units: str = "celsius",
                          hourly: bool = False) -> str:
        """Fetches current weather using Open-Meteo (free, no API key).

        hourly=True also appends the next ~8 hours from the same
        response's hourly block (temperature, plus precipitation
        probability when the API provides it)."""
        unit_key = str(units or "celsius").strip().lower()
        if unit_key in ("fahrenheit", "f", "imperial"):
            temp_unit, wind_unit = "fahrenheit", "mph"
            temp_suffix, wind_suffix = "°F", "mph"
        elif unit_key in ("celsius", "c", "metric"):
            temp_unit, wind_unit = "celsius", "kmh"
            temp_suffix, wind_suffix = "°C", "km/h"
        else:
            return ("units must be 'celsius' or 'fahrenheit' "
                    f"(got '{units}').")
        try:
            import json
            import urllib.parse
            import urllib.request

            # First geocode the location
            if location:
                geo_url = (
                    f"https://geocoding-api.open-meteo.com/v1/search?name="
                    f"{urllib.parse.quote_plus(location)}&count=1&language=en&format=json"
                )
                with urllib.request.urlopen(geo_url, timeout=6) as r:
                    geo = json.loads(r.read().decode())
                results = geo.get("results", [])
                if not results:
                    return f"Could not find location: {location}"
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                place_name = results[0].get("name", location)
            else:
                # Use rough IP-based location via ipapi
                with urllib.request.urlopen("https://ipapi.co/json/", timeout=6) as r:
                    geo = json.loads(r.read().decode())
                lat = geo.get("latitude", 0)
                lon = geo.get("longitude", 0)
                place_name = f"{geo.get('city', '')}, {geo.get('country_name', '')}"

            hourly_part = (
                "&hourly=temperature_2m,precipitation_probability"
                if hourly else ""
            )
            wx_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weathercode"
                f"{hourly_part}"
                f"&temperature_unit={temp_unit}&wind_speed_unit={wind_unit}&forecast_days=1"
            )
            with urllib.request.urlopen(wx_url, timeout=6) as r:
                wx = json.loads(r.read().decode())

            cur = wx.get("current", {})
            temp = cur.get("temperature_2m", "?")
            humidity = cur.get("relative_humidity_2m", "?")
            wind = cur.get("wind_speed_10m", "?")
            code = cur.get("weathercode", 0)

            # WMO weather interpretation
            WMO_CODES = {
                0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Foggy", 48: "Icy fog",
                51: "Light drizzle", 53: "Moderate drizzle", 55: "Heavy drizzle",
                61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
                95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
            }
            condition = WMO_CODES.get(code, "Unknown conditions")

            result = (
                f"Weather in {place_name}: {condition}. "
                f"Temperature: {temp}{temp_suffix}, Humidity: {humidity}%, Wind: {wind} {wind_suffix}."
            )
            if hourly:
                result += self._hourly_block(wx, cur, temp_suffix)
            return result
        except Exception as e:  # noqa: BLE001
            return f"Weather fetch failed: {e}"

    @staticmethod
    def _hourly_block(wx: dict, cur: dict, temp_suffix: str) -> str:
        """The 'next hours' appendix for get_weather(hourly=True),
        built from the forecast response's hourly block: the ~8
        hours starting at the current hour, temperature in the
        units already requested, precipitation probability only
        where the API actually provides a value. When the block
        is absent or unusable, says so instead of inventing one."""
        try:
            hourly = wx.get("hourly") or {}
            times = hourly.get("time") or []
            temps = hourly.get("temperature_2m") or []
            probs = hourly.get("precipitation_probability") or []
            if not times or len(temps) < len(times):
                return " Hourly detail is unavailable for this location."
            now_hour = str(cur.get("time") or "")[:13]
            start = None
            for i, t in enumerate(times):
                if str(t)[:13] >= now_hour:
                    start = i
                    break
            if start is None:
                return " Hourly detail is unavailable for this location."
            entries = []
            for i in range(start, min(start + 8, len(times))):
                hhmm = str(times[i])[11:16] or str(times[i])
                piece = f"{hhmm} {temps[i]}{temp_suffix}"
                if i < len(probs) and probs[i] is not None:
                    piece += f", rain chance {probs[i]}%"
                entries.append(piece)
            if not entries:
                return " Hourly detail is unavailable for this location."
            return " Next hours: " + "; ".join(entries) + "."
        except Exception:  # noqa: BLE001 — detail must never sink the answer
            return " Hourly detail is unavailable for this location."

    def _tool_set_reminder(self, message: str, minutes: int = 5,
                         at: str = "") -> str:
        """Sets a reminder to fire after the given number of minutes,
        or at a clock time when `at` ('HH:MM', 24-hour; an am/pm
        suffix works too) is given — today if that time is still
        ahead, tomorrow if it has already passed."""
        try:
            import datetime
            import re

            from ..scheduler import scheduler  # Phase 3 F3: file now in core/funnel/; import climbs one more level
            now = datetime.datetime.now()
            at_text = str(at or "").strip()
            if at_text:
                m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([aApP][mM])?", at_text)
                if not m:
                    return ("Could not read the time '"
                            f"{at}' — give it as HH:MM (24-hour), "
                            "e.g. '17:30', or with am/pm, e.g. '5:30 pm'.")
                hour, minute = int(m.group(1)), int(m.group(2))
                suffix = (m.group(3) or "").lower()
                if suffix == "pm" and hour < 12:
                    hour += 12
                elif suffix == "am" and hour == 12:
                    hour = 0
                if hour > 23 or minute > 59:
                    return (f"'{at}' isn't a time of day — hours run "
                            "0–23 and minutes 0–59.")
                fire_at = now.replace(hour=hour, minute=minute,
                                      second=0, microsecond=0)
                day_word = "today"
                if fire_at <= now:
                    fire_at += datetime.timedelta(days=1)
                    day_word = "tomorrow"
                label = f"reminder_{fire_at.strftime('%H%M%S')}"
                scheduler.add_reminder(label=label, fire_at=fire_at,
                                       message=message, repeat=False)
                return (f"Reminder set: '{message}' {day_word} at "
                        f"{fire_at.strftime('%H:%M')}.")
            fire_at = now + datetime.timedelta(minutes=minutes)
            label = f"reminder_{fire_at.strftime('%H%M%S')}"
            scheduler.add_reminder(label=label, fire_at=fire_at, message=message, repeat=False)
            return f"Reminder set: '{message}' in {minutes} minute(s) at {fire_at.strftime('%H:%M')}."
        except Exception as e:  # noqa: BLE001
            return f"Could not set reminder: {e}"

    def _tool_read_clipboard(self) -> str:
        """Reads the clipboard's text (#32): long text is
        truncated with the total stated, a lone secret-looking
        token is masked, and a non-text clipboard (an image or
        files) is reported as what it is.

        Loophole batch 3 (L-30): a credential shape EMBEDDED in
        otherwise ordinary text ("my password is hunter2", a
        `curl -u user:pass` line) is scrubbed the same way — the
        result never carries a secret back into the conversation,
        where the learn path could pick it up. The scrubbed
        result says so, so the masking is never silent."""
        try:
            import pyperclip
            text = pyperclip.paste()
            if not text or not isinstance(text, str):
                return ("The clipboard is empty, or holds something "
                        "that isn't text (an image or files).")
            shown = _clipboard_secret_shown(text)
            if shown is not None:
                return (f"Clipboard holds a secret-looking value "
                        f"({len(text)} characters, masked): {shown}")
            scrubbed = _scrub_text(text)
            if scrubbed != text:
                shown_text = scrubbed
                if len(shown_text) > 600:
                    shown_text = (
                        f"{shown_text[:600]}\n… "
                        f"[{len(shown_text) - 600} more characters "
                        "not shown]")
                return (f"Clipboard content ({len(text)} chars, "
                        f"a secret-shaped part masked): {shown_text}")
            if len(text) > 600:
                return (f"Clipboard content ({len(text)} chars, "
                        f"first 600 shown): {text[:600]}\n"
                        f"… [{len(text) - 600} more characters "
                        "not shown]")
            return f"Clipboard content ({len(text)} chars): {text}"
        except ImportError:
            return "Clipboard reading isn't available in this build of VYUK — the clipboard part didn't make it into the package."
        except Exception as e:  # noqa: BLE001
            return f"Could not read clipboard: {e}"

    def _tool_write_file(self, filepath: str, content: str,
                         append: bool = False):
        """Writes text content to a specified file path on disk.

        GAPS-23 (F-V7): the byte-exact write lives in the
        module-level write_file_bytes() at the end of this
        file — this handler is the funnel-guarded entry point
        and delegates to it, so write_proof and the white-box
        tests use the same code without calling a handler
        outside the funnel. The behaviour (encoding
        detection, BOM handling, verify re-read, undo
        record) is documented on write_file_bytes.
        """
        return write_file_bytes(filepath, content, append=append)

    # One read_file page: sized so the page plus its range header
    # stays inside execute()'s 4000-char result cap.
    _READ_FILE_PAGE_CHARS = 3000

    # Magic-byte signatures for the binary report's type guess.
    _BINARY_SIGNATURES = (
        (b"\x89PNG\r\n\x1a\n", "a PNG image"),
        (b"\xff\xd8\xff", "a JPEG image"),
        (b"GIF8", "a GIF image"),
        (b"%PDF-", "a PDF document"),
        (b"PK\x03\x04", "a ZIP archive (or an Office document, which is one)"),
        (b"\x1f\x8b", "a gzip archive"),
        (b"SQLite format 3\x00", "an SQLite database"),
        (b"MZ", "a Windows executable"),
        (b"\x7fELF", "an ELF executable"),
    )

    def _tool_read_file(self, filepath: str, offset: int = 0) -> str:
        """Reads one page of a text file, honestly.

        v4.5: no more silent 2,000-char cut. Every paged answer
        states the file's total size in characters and exactly which
        range it carries, and `offset` pages through the rest.
        Binary content (NUL bytes, or text that won't decode) is
        reported as binary with its byte size and a type guess —
        never returned as control-character garbage."""
        import pathlib
        try:
            path = pathlib.Path(filepath).expanduser()
            if not path.exists():
                return f"File not found: {filepath}"
            if path.is_dir():
                return f"'{filepath}' is a folder, not a file — nothing to read."
            raw = path.read_bytes()
            size = len(raw)
            if size == 0:
                return f"'{path}' is an empty file (0 bytes) — nothing in it."
            guess = next(
                (label for magic, label in self._BINARY_SIGNATURES
                 if raw.startswith(magic)),
                None,
            )
            text = None
            if b"\x00" not in raw[:8192]:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    replaced = raw.decode("utf-8", errors="replace")
                    bad = replaced.count("\ufffd")
                    if bad == 0 or bad / max(1, len(replaced)) <= 0.02:
                        text = replaced
            if text is None:
                kind = guess or "binary data"
                return (
                    f"'{path}' looks like a binary file ({kind}, "
                    f"{size:,} bytes) — its contents aren't readable text, "
                    "so there's nothing to read back."
                )
            total = len(text)
            try:
                start = max(0, int(offset))
            except (TypeError, ValueError):
                start = 0
            if start >= total:
                return (
                    f"'{path}' has {total:,} characters — offset {start} "
                    "is at or past the end, so there's nothing there to read."
                )
            page = text[start:start + self._READ_FILE_PAGE_CHARS]
            end = start + len(page)
            if start == 0 and end >= total:
                return page
            return (
                f"[File: {path} — {total:,} characters total; showing "
                f"chars {start:,}–{end:,} of {total:,}"
                + (f". Call read_file again with offset={end} to continue.]"
                   if end < total else " — end of file.]")
                + f"\n{page}"
            )
        except Exception as e:  # noqa: BLE001
            return f"Could not read file: {e}"

    def _tool_get_system_info(self) -> str:
        """Returns current system resource usage: CPU, RAM, disk, battery, uptime."""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            parts = [
                f"CPU: {cpu:.0f}%",
                f"RAM: {ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB ({ram.percent:.0f}% used)",
                f"Disk: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({disk.percent:.0f}% used)",
            ]
            try:
                battery = psutil.sensors_battery()
            except Exception:  # noqa: BLE001 — no battery sensor on desktops
                battery = None
            if battery is not None:
                plugged = " (plugged in)" if battery.power_plugged else ""
                parts.append(f"Battery: {battery.percent:.0f}%{plugged}")
            try:
                up_seconds = max(
                    0, int(datetime.datetime.now().timestamp() - psutil.boot_time())
                )
                parts.append(
                    f"Uptime: {up_seconds // 3600}h {(up_seconds % 3600) // 60:02d}m"
                )
            except Exception:  # noqa: BLE001 — uptime is a bonus, never fatal
                pass
            return "System status — " + ", ".join(parts) + "."
        except ImportError:
            return "System info isn't available in this build of VYUK — the system-info part didn't make it into the package."
        except Exception as e:  # noqa: BLE001
            return f"System info unavailable: {e}"

    def _tool_download_email_attachment(self, message_id: str,
                                        filename: str = "",
                                        save_dir: str = "") -> str:
        """Handler wrapper for google_services.download_attachment.

        The result string names a SENDER-controlled filename, so it
        goes past the injection screen (v5.3) before the brain sees
        it: a crafted name ("ignore-instructions.pdf") taints the
        session instead of instructing anyone. The text itself is
        returned unwrapped — it is VYUK's own save confirmation."""
        from .. import google_services  # Phase 3 F3: file now in core/funnel/; import climbs one more level

        result = google_services.download_attachment(
            message_id, filename, save_dir)
        # R1b: download_attachment may now return an Outcome —
        # the taint screen reads plain text, so pass it str(...).
        injection.note_untrusted("email attachment", str(result))
        return result

    # -- Conversion pass, combined W4/W5 registration --------
    # Funnel wrappers for the W4/W5 follow-up tools. Each
    # wrapper takes the named schema fields and forwards one
    # dict to the wiring module, which is NOT modified.
    # Successful results are the wiring dicts encoded as JSON
    # strings verbatim; refusal dicts flow the same way.

    def _tool_curl_cffi_read(self, url: str = "",
                             impersonate: str = "chrome",
                             timeout: float = 30.0) -> str:
        """Funnel wrapper for w4_wiring curl_cffi_read_tool.

        Cookies are deliberately NOT supplied here: the
        dispatching handler has no live Playwright browser
        context to take them from, so curl_read() refuses
        honestly with no cookies supplied. This wrapper must
        never be pointed at a cookie file — the lane accepts
        cookies only from her own Playwright context, and
        until that caller exists the tool refuses rather
        than reading. No network request is attempted on
        this path.
        """
        from .. import w4_wiring as _w4  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 30.0
        result = _w4.curl_cffi_read_tool({
            "url": url,
            "impersonate": impersonate or "chrome",
            "timeout": timeout_f,
        })
        return json.dumps(result, ensure_ascii=False)

    def _tool_replay_programme(self, programme: str = "") -> str:
        """Funnel wrapper for w4_wiring replay_programme_tool.

        This is Wave 4 TC-2 replay (core/replay.py), never
        Wave 8 teach replay. Validation gates FIRST inside
        replay_programme(): an invalid programme is refused
        and the executor is never touched.
        """
        from .. import w4_wiring as _w4  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _w4.replay_programme_tool({"programme": programme})
        return json.dumps(result, ensure_ascii=False)

    def _tool_capture_route(self) -> str:
        """Funnel wrapper for w5_wiring tool_capture_route.

        Returns the capture-route report as text. Takes no
        capture itself: the ladder walk only probes backend
        availability (import checks), and no backend is
        claimed to grab.
        """
        from .. import w5_wiring as _w5  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        return _w5.tool_capture_route()

    def _tool_occluded_capture(self, window_title: str = "") -> str:
        """Funnel wrapper for w5_wiring tool_occluded_capture.

        The staged guard is preserved verbatim: without a
        per-session occluded_screenshot approval the call is
        refused, and with approval but no WGC engine wired
        it returns engine-unavailable — never a fabricated
        frame, never a silent capture.
        """
        from .. import w5_wiring as _w5  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        return _w5.tool_occluded_capture(window_title)

    def _tool_eyes_fusion(self) -> str:
        """Funnel wrapper for w5_wiring tool_eyes_fusion.

        Returns the last grounding fusion / capture evidence
        already noted, as text. The fused text is derived
        from UIA/vision evidence, not a new capture and not
        a write; when nothing has been noted yet it says so.
        """
        from .. import w5_wiring as _w5  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        return _w5.tool_eyes_fusion()

    # -- Conversion pass, Wave 6 registration follow-up --------
    # Funnel contract: neighbouring handlers return strings, and
    # the funnel maps any handler string to status success — so
    # a failed write must RAISE (Stage 2's remember-tool lesson),
    # never return a failure sentence. The W6_TOOL_HANDLERS
    # functions take one dict argument, but the funnel dispatches
    # by named parameters, so each wrapper below takes the named
    # schema fields and forwards one dict to w6_wiring, which is
    # NOT modified. Successful results are the w6 dicts encoded
    # as JSON strings verbatim (citations, licence gates and
    # available/unavailable flags preserved); refusal and
    # unavailable dicts flow the same way, exactly like the
    # refusal sentences other handlers return as results.
    # research's wrapper supplies NO fetcher: the funnel call
    # therefore never sends a network request (the clients
    # return honest unavailable without one). No opinion tool:
    # write_opinion is never promoted to facts, so it gains no
    # declaration. No embedder, generative-ingestion or
    # pressure-valve tool: those stay facade-only / refused /
    # read-only in w6_wiring and gain nothing here.

    def _tool_knowledge_search(self, query: str = "",
                               limit: int = 10) -> str:
        """Funnel wrapper for w6_wiring knowledge_search_tool."""
        from .. import w6_wiring as _w6  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(50, limit))
        result = _w6.W6_TOOL_HANDLERS["knowledge_search"](
            {"query": query, "limit": limit})
        return json.dumps(result, ensure_ascii=False)

    def _tool_semantic_search_knowledge(self, query: str = "",
                                        limit: int = 10) -> str:
        """Funnel wrapper for w6_wiring semantic_search_knowledge."""
        from .. import w6_wiring as _w6
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(50, limit))
        result = _w6.W6_TOOL_HANDLERS["semantic_search_knowledge"](
            {"query": query, "limit": limit})
        return json.dumps(result, ensure_ascii=False)

    def _tool_embed_knowledge(self, limit: int = 50) -> str:
        """Funnel wrapper for w6_wiring embed_knowledge."""
        from .. import w6_wiring as _w6
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))
        result = _w6.W6_TOOL_HANDLERS["embed_knowledge"](
            {"limit": limit})
        return json.dumps(result, ensure_ascii=False)

    def _tool_knowledge_embedding_status(self) -> str:
        """Funnel wrapper for w6_wiring knowledge_embedding_status."""
        from .. import w6_wiring as _w6
        result = _w6.W6_TOOL_HANDLERS["knowledge_embedding_status"]({})
        return json.dumps(result, ensure_ascii=False)

    def _tool_research(self, question: str = "", source: str = "arxiv",
                       doi: str = "") -> str:
        """Funnel wrapper for w6_wiring research_tool, with the
        v5.68 deltas riding on top (research_ride).

        No fetcher is supplied here on purpose: without one the
        clients build the request and report unavailable instead
        of sending it, so a funnel call can never put
        model-chosen bytes on the wire. The ride only
        post-processes what the base handler returned (ranked
        articles, typed retraction verdict); the W6 handler
        itself and the params {question, source, doi} are
        unchanged, so the fitted declarations do not move.
        """
        from ..memory import research_ride as _ride  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _ride.ride_research_tool(
            {"question": question, "source": source, "doi": doi})
        return json.dumps(result, ensure_ascii=False)

    def _tool_deep_research(self, question: str = "",
                            max_rounds: int = 3) -> str:
        """Funnel wrapper for the research wiring batch: run the
        A6 deep-research loop (core/memory/deep_research) over the local
        KnowledgeStore. Read-only and local-only: no network, no
        fetcher, no executor, no model generation — the summary
        is extractive and citations come from the retrieval log.
        A blank question is answered plainly, never run; store
        failure and no-findings are reported honestly by the
        implementation, never dressed up."""
        from ..memory import deep_research as _dr  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        if not str(question or "").strip():
            return ("Nothing to research — a question is needed, "
                    "and none was given.")
        result = _dr.run_deep_research(
            str(question), max_rounds=max_rounds)
        return json.dumps(result, ensure_ascii=False)

    def _tool_remember_person(self, name: str = "", relationship: str = "",
                              note: str = "") -> str:
        """Funnel wrapper for w6_wiring remember_person_tool."""
        from .. import w6_wiring as _w6  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        if not str(name or "").strip():
            return ("Nothing to save — a person record needs a "
                    "name, and none was given.")
        result = _w6.W6_TOOL_HANDLERS["remember_person"](
            {"name": name, "relationship": relationship, "note": note})
        if not os.path.exists(str(result.get("path", ""))):
            raise RuntimeError(
                "I tried to save that person, but the write did "
                "not land — nothing was stored. Please try again, "
                "and if it keeps failing, run the doctor check.")
        return json.dumps(result, ensure_ascii=False)

    def _tool_remember_promise(self, person: str = "",
                               promise_text: str = "",
                               due_date: str = "") -> str:
        """Funnel wrapper for w6_wiring remember_promise_tool."""
        from .. import w6_wiring as _w6  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        if not str(person or "").strip() \
                or not str(promise_text or "").strip():
            return ("Nothing to save — a promise record needs "
                    "both who it was made to and what was "
                    "promised, and one of them was empty.")
        result = _w6.W6_TOOL_HANDLERS["remember_promise"](
            {"person": person, "promise_text": promise_text,
             "due_date": due_date})
        if not os.path.exists(str(result.get("path", ""))):
            raise RuntimeError(
                "I tried to save that promise, but the write did "
                "not land — nothing was stored. Please try again, "
                "and if it keeps failing, run the doctor check.")
        return json.dumps(result, ensure_ascii=False)

    def _tool_personal_skills(self, action: str = "list",
                              name: str = "", inputs: str = "") -> str:
        """Funnel wrapper for skills_wiring personal_skills_tool.

        ``inputs`` is a JSON list of integers for the ``run``
        action (the contained-runtime pilot ABI); ignored by
        list/inspect/admit.
        """
        from .. import skills_tool_wiring as _sw  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _sw.personal_skills_tool(
            {"action": action, "name": name, "inputs": inputs})
        return json.dumps(result, ensure_ascii=False)

    def _tool_health(self, action: str = "", text: str = "",
                     date: str = "", metric: str = "",
                     value: float | None = None,
                     unit: str = "") -> str:
        """Funnel wrapper for health_wiring health_tool."""
        from ..health import health_tool_wiring as _hw  # Phase 3 H1: moved to core/health/ (Phase 3 F3: file now in core/funnel/; import climbs one more level)
        result = _hw.health_tool(
            {"action": action, "text": text, "date": date,
             "metric": metric, "value": value, "unit": unit})
        return json.dumps(result, ensure_ascii=False)

    def _tool_lifeadmin(self, action: str = "", query: str = "") -> str:
        """Funnel wrapper for lifeadmin_wiring lifeadmin_tool.

        Guidance-only: trip status link-outs, paperwork
        preparation guidance, trip assembly with link-outs.
        The human books; no booking, payment, CAPTCHA, OTP, or
        password shape exists in this tool.
        """
        from .. import lifeadmin_tool_wiring as _lw  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _lw.lifeadmin_tool(
            {"action": action, "query": query})
        return json.dumps(result, ensure_ascii=False)

    def _tool_business(self, action: str = "", query: str = "") -> str:
        """Funnel wrapper for business_wiring business_tool.

        Reads and checks only: telemetry-OFF proof, trust-pack
        render (DPA-gated claims conditional), signed
        fleet-entitlement verification. Never sends, never
        captures, never books; no multi-user session exists yet
        and the consent machinery stays library-only until a
        capture lane lands.
        """
        from .. import business_funnel_wiring as _bw  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _bw.business_tool(
            {"action": action, "query": query})
        return json.dumps(result, ensure_ascii=False)

    def _tool_overdue_promises(self, today: str = "") -> str:
        """Funnel wrapper for w6_wiring overdue_promises_tool."""
        from .. import w6_wiring as _w6  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        result = _w6.W6_TOOL_HANDLERS["overdue_promises"](
            {"today": today or None})
        return json.dumps(result, ensure_ascii=False)

    def load_plugins(self):
        """Loads and registers all dynamic plugins from the plugins/ directory.

        v5.45 (H19): the registry snapshot taken HERE — before any
        plugin registers — is handed to the loader, whose collision
        rule judges plugin names against it (a collision skips the
        plugin loudly; register_all's internal pack-overrides never
        pass through this path and are unaffected). Each plugin's
        capability gate class is recorded for _needs_confirmation.
        Hash verification happens inside the loader, before any
        plugin code executes."""
        try:
            from ..plugin_loader import discover_plugins  # Phase 3 F3: file now in core/funnel/; import climbs one more level
            snapshot = set(self._tools.keys())
            discovered = discover_plugins(existing_names=snapshot)
            for p in discovered:
                name = p.get("name")
                desc = p.get("description", "")
                params = p.get("parameters", {"type": "object", "properties": {}})
                handler = p.get("_handler")
                if name and handler:
                    self.register(name=name, description=desc, parameters=params, handler=handler)
                    _PLUGIN_GATE_CLASS[name] = p.get("_gate_class") or "gated"
                    print(f"[Tools] Plugin tool registered: {name}")
        except Exception as e:  # noqa: BLE001
            print(f"[Tools] Error loading plugins: {e}")


def write_file_bytes(filepath: str, content: str,
                     append: bool = False):
    """Writes text content to a specified file path on disk.

    v4.4 truthfulness: overwriting an existing file is DISCLOSED
    in the result (with the size being replaced) — silently
    destroying a previous version under a cheerful success line
    was an audit MAJOR. The echoed path is absolute so the brain
    always knows where the file actually landed.

    Fix 7 (tools loopholes): the write is VERIFIED by re-reading
    the file (core.tool_outcome.file_written) and the result is
    an Outcome — verified only when the bytes on disk match what
    was written. A write whose re-read fails is never claimed as
    a success.

    Batch 0 (click-anywhere, L-60): the write is BYTES, not
    text. The text arg is encoded in the DETECTED encoding
    (BOM sniff first — UTF-8 BOM, UTF-16 LE/BE BOM; else UTF-8
    if the file decodes as UTF-8, else refuse, never silently
    re-encode; a file that does not exist defaults to UTF-8),
    the file is written with ``"wb"`` (no newline translation,
    no re-encoding through the platform default), and the
    verify reads the file back as bytes and compares against
    the identical bytes — no normalisation, no trailing-newline
    tolerance. The undo store records the pre-write bytes (or
    "new file", whose undo deletes the file)."""
    import pathlib
    from .. import tool_outcome as _outcome  # Phase 3 F3: file now in core/funnel/; import climbs one more level
    try:
        from .. import file_bytes as _fb
    except Exception:  # noqa: BLE001
        try:
            from core import file_bytes as _fb
        except Exception:  # noqa: BLE001
            _fb = None
    try:
        path = pathlib.Path(filepath).expanduser().resolve()
        existed = path.is_file()
        prior_size = path.stat().st_size if existed else None
        prior_bytes = None
        if existed:
            try:
                prior_bytes = path.read_bytes()
            except Exception:  # noqa: BLE001 — unreadable: refuse
                return _outcome.failed(
                    "Could not read the existing file %s before "
                    "writing it — refusing to overwrite a file "
                    "whose current bytes cannot be read." % path,
                    "prior read failed")
        if _fb is None:
            return _outcome.failed(
                "Could not load the byte-exact write module — "
                "refusing to write in text mode.", "no file_bytes")
        try:
            encoding = _fb.detect_encoding(prior_bytes)
        except Exception as exc:  # noqa: BLE001 — undetectable
            return _outcome.failed(
                "Could not write %s: %s" % (path, exc),
                "undetectable encoding")
        # The exact bytes the write must lay down (M-54: an
        # append verifies the FULL expected bytes — old content
        # bytes + appended bytes — so a concurrent modification
        # of the old content fails the verify instead of being
        # silently kept).
        if append and existed:
            expected = (prior_bytes
                        + _fb.encode_text(content, encoding,
                                          include_bom=False))
        elif append and not existed:
            expected = _fb.encode_text(content, "utf-8",
                                       include_bom=False)
        else:
            expected = _fb.encode_text(
                content, encoding,
                include_bom=(prior_bytes is not None
                             and _fb.has_bom(prior_bytes)))
        # Code-Perfection Build 1 — undo producer hook: record
        # the write BEFORE it happens, when an undo store is
        # configured, so the Doctor Undo button can restore
        # the previous content. Batch 0: the record carries
        # the pre-write BYTES (or "new file"). A record failure
        # (no store configured, the usual session until the
        # Doctor page has configured one) leaves the write
        # exactly as today, with no undo claim — recording must
        # never break the write.
        try:
            from .. import undo_producers_p1 as _prod  # Phase 3 F3: file now in core/funnel/; import climbs one more level
        except Exception:  # noqa: BLE001
            try:
                from core import undo_producers_p1 as _prod
            except Exception:  # noqa: BLE001
                _prod = None
        if _prod is not None:
            try:
                _prod.ensure_producers_registered()
                _prod.record_file_write_bytes(
                    str(path), new_bytes=expected, encoding=encoding)
            except Exception:  # noqa: BLE001 — never break the write
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        # C3-F5 (2026-11-20): the write is atomic — temp file in
        # the same directory + os.replace, the scheduler /
        # skills_text pattern — so a failure mid-write (ENOSPC)
        # leaves the old file byte-identical (or no file at all
        # for a new path), never a truncated partial the next
        # read would present as the file. The temp file is
        # removed on any failure; the failure itself still
        # reaches the loud failed outcome below.
        import tempfile
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".",
            suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(expected)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        _ok, _evidence = _outcome.file_written(path, expected)
        if not _ok:
            if append and existed:
                return _outcome.failed(
                    "The append to %s could not be confirmed: %s."
                    % (path, _evidence), _evidence)
            if append and not existed:
                return _outcome.failed(
                    "The file %s was created for the append, but "
                    "the write could not be confirmed: %s."
                    % (path, _evidence), _evidence)
            return _outcome.failed(
                "The write to %s could not be confirmed: %s."
                % (path, _evidence), _evidence)
        if append and existed:
            new_size = path.stat().st_size
            return _outcome.verified(
                "File appended: %s (+%d chars; "
                "the file had %s bytes, now "
                "%d bytes)" % (path, len(content), prior_size,
                              new_size),
                _evidence)
        if append and not existed:
            return _outcome.verified(
                "File written: %s (%d chars) — "
                "append was on, but the file didn't exist yet, "
                "so it was created" % (path, len(content)),
                _evidence)
        if existed:
            return _outcome.verified(
                "File written: %s (%d chars) — this "
                "OVERWROTE an existing file (%s bytes were "
                "replaced)." % (path, len(content), prior_size),
                _evidence)
        return _outcome.verified(
            "File written: %s (%d chars)" % (path, len(content)),
            _evidence)
    except Exception as e:  # noqa: BLE001
        return _outcome.failed(
            "Could not write file: %s" % e, str(e))
