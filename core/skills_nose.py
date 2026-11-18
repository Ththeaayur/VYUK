"""
NOSE skills for VYUK (v5.20) — the sensing pack: sniff a
file, a link or a passage before trusting it; read the Wi-Fi
radar; measure the network's quality; check the room's light
through the webcam.

Every tool here is a LOOK, not a touch. The implementations live
in core/senses.py as structured, never-raising helpers (the
Senses dashboard renders the same dicts); these handlers are the
spoken narration over them, in the plain style of the sibling
packs: human-readable strings, failures stated honestly, caps
disclosed. Handlers never raise — an exception becomes an honest
"I couldn't…" line.

Deliberately NOT here: wifi_info (it
network pack owns the plain scan + current connection) —
scan_wifi_networks is the radar cut of the same air: channel and
access-point count per network, cross-checked on Linux via nmcli
too. check_port / ping_host / speed_test stay with their packs;
network_quality here is latency + jitter quality, not bandwidth.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

from . import senses


def sniff_file(path: str) -> str:
    """Narrate core/senses.sniff_file's report on one file."""
    try:
        report = senses.sniff_file(path)
        if not report.get("ok"):
            return report.get("verdict", "That file doesn't exist.")
        lines = [f"{report['name']} — {report['size_human']}, "
                 f"contents read as: {report['detected_type']}."]
        if report.get("zip_entries") is not None:
            lines.append(
                f"It's an archive of {report['zip_entries']} file(s), "
                f"{senses._size_human(report.get('zip_uncompressed_bytes', 0))} "
                "uncompressed.")
        if report.get("lines") is not None:
            lines.append(
                f"{report['lines']} line(s) of text"
                f"{'' if report.get('utf8_valid') else ' (not clean UTF-8)'}.")
        if report.get("hash_truncated"):
            lines.append("Its fingerprint (SHA-256) covers the first "
                         "16 MB — the file is bigger than that.")
        else:
            lines.append(f"SHA-256 fingerprint: {report['sha256']}")
        lines.append(report["verdict"])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return f"I couldn't sniff that file: {e}"


def sniff_link(url: str) -> str:
    """Narrate core/senses.sniff_link's redirect walk."""
    try:
        report = senses.sniff_link(url)
        if not report.get("ok"):
            return report.get("verdict",
                              "I couldn't check that link.")
        lines = []
        for hop in report["hops"]:
            status = hop["status"]
            lines.append(f"• {hop['url']} → "
                         f"{status if status is not None else 'no answer'}")
        details = []
        if report.get("content_type"):
            details.append(f"type {report['content_type']}")
        if report.get("server"):
            details.append(f"server {report['server']}")
        if details:
            lines.append("Final page: " + ", ".join(details) + ".")
        lines.append(report["verdict"])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't sniff that link: {e}"


def sniff_text(text: str) -> str:
    """Narrate core/senses.sniff_text's profile of a passage."""
    try:
        report = senses.sniff_text(text)
        lines = [f"{report['words']} word(s), {report['chars']} "
                 f"character(s)."]
        if report["links"]:
            lines.append("Links inside: "
                         + ", ".join(report["links"][:5])
                         + ("…" if len(report["links"]) > 5 else ""))
        if report["emails_count"]:
            lines.append(f"Email addresses inside: "
                         f"{report['emails_count']}.")
        if report["shouting_pct"] >= 50:
            lines.append(f"{report['shouting_pct']}% of it is "
                         "CAPITALISED — it reads as shouting.")
        lines.append(report["verdict"])
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't sniff that text: {e}"


def scan_wifi_networks() -> str:
    """The radar cut of the air: per network, strongest signal,
    security, the channel its loudest access point sits on, and
    how many access points carry the name."""
    try:
        scan = senses.wifi_scan()
        if not scan.get("available"):
            return ("Couldn't scan the airwaves — "
                    + scan.get("note", "Wi-Fi listing unavailable."))
        nets = scan["networks"]
        if not nets:
            return ("No Wi-Fi networks in range right now — "
                    + scan.get("note", ""))
        lines = [f"Wi-Fi radar — {len(nets)} network(s) in range:"]
        for n in nets:
            name = n["ssid"] if n["ssid"] else "(hidden)"
            sig = (f"{n['signal']}%" if n["signal"] is not None
                   else "signal ?")
            sec = ("secured" if n["secured"] else "open"
                   if n["secured"] is not None else "security ?")
            extras = []
            if n.get("channel"):
                extras.append(f"channel {n['channel']}")
            if n.get("aps", 1) > 1:
                extras.append(f"{n['aps']} access points")
            suffix = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"• {name} — {sig}, {sec}{suffix}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't scan Wi-Fi: {e}"


def network_quality(host: str = "") -> str:
    """Narrate core/senses.network_quality's measurement."""
    target = (host or "").strip()
    try:
        q = senses.network_quality(target)
        if target:
            label = q.get("target") or target
            if not q.get("reachable"):
                return (f"{label} looks unreachable — five test "
                        "connections to it all failed. It may be "
                        "down, or something is blocking outbound "
                        "traffic.")
            return (f"Network quality to {label} is {q['verdict']}: "
                    f"about {q['latency_ms']} ms per test connection"
                    f"{'' if q['samples'] >= 5 else f' (only {q['samples']} of 5 test connections answered)'}"
                    f", with {q['jitter_ms']} ms of jitter. "
                    "This measures responsiveness, not download "
                    "speed — speed_test covers that.")
        if not q.get("reachable"):
            return ("The network looks unreachable — five test "
                    "connections to public DNS servers all failed. "
                    "You may be offline, or something is blocking "
                    "outbound traffic.")
        return (f"Network quality is {q['verdict']}: about "
                f"{q['latency_ms']} ms to reach a public DNS server"
                f"{'' if q['samples'] >= 5 else f' (only {q['samples']} of 5 test connections answered)'}"
                f", with {q['jitter_ms']} ms of jitter. "
                "This measures responsiveness, not download speed — "
                "speed_test covers that.")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't measure the network: {e}"


def _log_light_reading(pct: int) -> None:
    """Record a brightness reading on the senses timeline
    (v5.31), throttled to one entry per 10 minutes — sense_trends
    trends these readings, and repeat checks shouldn't fill the
    500-line log. Best-effort: logging never breaks the tool."""
    try:
        from datetime import datetime, timedelta

        from . import senses_log

        now = datetime.now()
        for event in senses_log.read_events(limit=500):
            if event.get("kind") != "light":
                continue
            try:
                last = datetime.fromisoformat(event["ts"])
            except (KeyError, ValueError):
                break
            if now - last < timedelta(minutes=10):
                return
            break
        senses_log.append_event(
            "light", f"Room brightness measured at about {pct}%.",
            value=pct, unit="%")
    except Exception:  # noqa: BLE001
        pass


def room_brightness() -> str:
    """One webcam frame → how lit the room is. On demand only —
    the camera is never opened by anything but this call."""
    try:
        from .desktop.vision import vision_manager  # Phase 3 D4c: vision hub moved to core/desktop/

        frame = vision_manager.capture_webcam()
        if not frame:
            return ("No camera available — I couldn't open a "
                    "webcam to look at the room. (The camera may "
                    "be missing, busy, or the vision extras aren't "
                    "installed.)")
        image_bytes, _mime = frame
        result = senses.frame_brightness(image_bytes)
        pct = round(result['brightness'] * 100)
        _log_light_reading(pct)
        return (f"The room reads {result['label']} — average "
                f"brightness {pct}%. "
                + result["note"])
    except Exception as e:  # noqa: BLE001
        return f"I couldn't check the room's brightness: {e}"


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every NOSE pack tool on a ToolRegistry-like object."""
    registry.register(
        name="sniff_file",
        description="Inspect a file before trusting it: its real contents type (read from its magic bytes) versus what its filename claims — a mismatch ('named .pdf, actually a ZIP archive') is called out loudly — plus its size, the full SHA-256 fingerprint (the hash covers the first 16 MB for very large files), an archive summary for ZIPs (entry count, uncompressed size, a possible-zip-bomb flag when it claims to expand over 100×) and line/encoding stats for text. Use when the user asks 'what is this file?', gets an unexpected download, or wants a file checked. Read-only: the file is never opened with anything but a byte reader, never executed or extracted. Plainly: check whether a file is really what it claims to be before trusting it.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path to the file, e.g. 'C:\\\\Users\\\\me\\\\Downloads\\\\invoice.pdf' or '~/Downloads/report.zip'"},
            },
            "required": ["path"],
        },
        handler=sniff_file,
    )
    registry.register(
        name="sniff_link",
        description="Check where a link really goes before opening it: follows the redirect chain by hand (up to 8 hops, each hop's status recorded) through the system proxy and reports the final destination host, the final HTTP status, content type and server, whether the link started at a known shortener (bit.ly, tinyurl.com, t.co and friends — they hide the destination), and the final host's TLS certificate expiry when it's HTTPS. Use when the user pastes a suspicious or shortened link, or asks 'where does this link go?'. Only http/https. Read-only: pages are never downloaded beyond their headers. Plainly: check whether a link looks like a scam before opening it.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The full link to check, e.g. 'https://bit.ly/3AbCdEf'"},
            },
            "required": ["url"],
        },
        handler=sniff_link,
    )
    registry.register(
        name="sniff_text",
        description="Profile a passage of text: word/character counts and reading time, the links and email addresses inside it, a language guess, how much of it is capitalised shouting — and whether it is shaped like a prompt-injection (wording that tries to instruct an AI: 'ignore your previous instructions', fake system prompts and the like). Use when the user pastes text they were sent and asks 'is this safe / what is this?', or before acting on text from an untrusted source. Read-only. Plainly: check whether a pasted message looks like a scam or a trick.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "The passage to profile, exactly as received"},
            },
            "required": ["text"],
        },
        handler=sniff_text,
    )
    registry.register(
        name="scan_wifi_networks",
        description="The Wi-Fi radar: scan the networks in range and report each one's strongest signal, whether it's secured, the channel its loudest access point sits on, and how many access points carry the name — strongest first, up to 25. Use when the user asks which network or channel is strongest / least crowded, or whether a network is open. (wifi_info describes the current connection.) Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=scan_wifi_networks,
    )
    registry.register(
        name="network_quality",
        description="Measure how responsive the internet connection feels right now: five timed test connections to public DNS servers (1.1.1.1 / 8.8.8.8) give a median latency and jitter, graded excellent / good / fair / slow — or honestly unreachable when every test fails. Use when the user asks 'is my internet slow?' or a call feels laggy. This is responsiveness, not bandwidth — speed_test measures download/upload throughput. Pass host to measure against one specific server or site instead (a host name or IP, optionally host:port). Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Optional: measure against this host instead of the public DNS pair, e.g. 'google.com', '192.168.1.1' or 'example.com:8443'"},
            },
            "required": [],
        },
        handler=network_quality,
    )
    registry.register(
        name="room_brightness",
        description="Check how lit the room is by taking ONE webcam frame and measuring its average brightness (dark / dim / lit / bright, as a percentage). The camera opens only for this call and only when the tool is invoked. Use when the user asks 'is my room too dark?' or whether their lighting is fine for a call. If no camera is available it says so honestly. Read-only.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=room_brightness,
    )
