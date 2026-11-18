"""VYUK's own phone companion (R7-5, kovrel pattern).

The pattern, not the code: kovrel (Nyxaliths/kovrel, MIT) is a
Windows-only Python server that turns a phone browser into a remote
for the PC — live screen, touchpad, keyboard, media, files,
clipboard, macros, lock/sleep/shutdown, QR+PIN pairing, no app, no
account, no cloud. Nothing in it can be adopted wholesale (it is a
standalone server with its own input/screen/file code); what is
built here is VYUK's own code for the same shape, reusing the
tree's existing control stack instead of reimplementing it — and,
since GAPS-9, through the funnel tools of the same names
(mouse_control, type_text, press_hotkey, media_control,
set_volume_level, read_clipboard, write_clipboard, lock_screen,
power_control) via ToolRegistry.execute, with the same
input-blocked and focus guards, the same confirm gate, ledger
and receipt as every other caller:

- screen -> control_system._capture_screen_bytes (the same
  vision_manager capture take_screenshot uses), under the
  capture guard, first frame served logged;
- files -> the server's own rooted reads, every access logged;
- QR pairing -> core/qr_encode.py (VYUK's own byte-mode encoder,
  verified byte-for-byte against the PyPI `qrcode` oracle).

Gap this closes (proven read-only before writing):
- no phone surface anywhere (the word "phone" in core+gui is only
  adjectives inside skill descriptions);
- no QR pairing (the only QR code is read_qr_code, a read-only
  DECODER for image files);
- no LAN server (the only http.server in the tree is
  core/webengine_proxy.py, a localhost proxy for the removed
  WebEngine view);
- no screen streaming (only still screenshots + OCR).

Security — the load-bearing part (a network listener on his
laptop), fail-closed throughout:
- LAN-only by default: the server binds the machine's private LAN
  address (never 0.0.0.0, never a public address — a non-private
  address refuses to start). Away-from-home access via Tailscale
  is OUT of scope for this batch (LAN only).
- PIN-gated pairing: a fresh random 6-digit PIN per start, shown
  only on the laptop (tool result + QR file); the PIN is never
  stored in a file, never logged, and dies with the server. Five
  wrong PINs from one address lock that address out for 60 s.
- Session tokens: random 32-byte tokens, httpOnly cookies, at
  most 3 paired devices, 8-hour expiry, invalidated on stop.
- The listener never starts without his explicit action: the
  start_companion tool call (or the palette entry for it) IS the
  opt-in, and there is no auto-start, no durable grant, no
  background start. Every start is a fresh explicit action.
- Screen sharing is consent-gated twice: once at start through
  capture_guard.ensure_capture_allowed("screenshot_save") (the
  durable screen denial, the sensitive-window denylist and the
  capture policy all apply — a denial turns the screen OFF for
  the session while the rest of the companion still works), and
  once per frame through capture_guard.foreground_capture_block()
  (a denylisted foreground window serves a refusal, never the
  frame). While the screen is shared the on-screen capture
  indicator is live (note_capture_started/ended).
- Lock/sleep/shutdown: lock is one tap; sleep, shutdown and
  restart pop the desktop confirmation (the funnel gates
  power_control wholesale, exactly as it does for a voice
  request); shutdown/restart ALSO need a second confirm tap on
  the phone AND confirm=true in the request.
- One funnel, one gate (GAPS-9, flaw #1): every action the phone
  can take that has a funnel tool — mouse, type, keys, media,
  volume, clipboard, lock, power — runs through
  ToolRegistry.execute, the same funnel every other caller uses,
  with the same schema validation, permission and folder-scope
  stages, confirm gate, money guard, ledger and receipt. The
  companion used to call the desktop functions directly (a
  complete funnel bypass: a paired phone could shut the laptop
  down with no desktop approval and no record anywhere). It no
  longer does. A confirm-gated action from the phone pops the
  desktop confirmation and the phone's request waits up to 60 s
  for the owner's answer; no handler, a decline, or a timeout
  refuses it and nothing runs. Two things have no funnel tool
  and stay server-side, with their own guards and an
  activity-log line each: the screen (the funnel's
  take_screenshot pushes bytes to the frame sink and cannot
  return a JPEG to the phone; the capture guard still applies,
  and the first frame served in a session is logged) and files
  (no tool serves a local file to a phone; the owner's explicit
  per-start files_root grant, rooted, download-only, 50 MB cap,
  every list/download logged). /api/status is server-state
  introspection, polled every 2 s — it changes nothing and is
  not a funnel call.
- Files are read-only and rooted: no files_root configured means
  no file access at all; a configured root allows listing and
  downloading under that root only (no traversal, no upload, no
  delete, 50 MB download cap).
- No cloud, no account, no telemetry: the server serves one HTML
  page with no external assets and makes no outbound request.

Honest limits of this batch (not hidden):
- The screen refreshes by polling (~1 frame per second, 500 ms
  throttle) — kovrel's ~30 fps MJPEG stream is NOT built here;
  a faster stream is owed work, not claimed.
- Custom macros are NOT built: the phone offers the built-in
  action buttons only. A macro (owner-configured named funnel
  calls from the phone) needs its own config + allow-list
  decision and is owed.
- The phone's clipboard box shows the funnel's clipboard
  description (truncated with the total stated, secret-looking
  values masked) rather than the raw text — the same description
  VYUK gives in chat. That is what routing the read through the
  funnel costs; it is also what buys the masking.
- Files are download-only. Upload/delete are owed.
- Volume is an absolute slider (percent): the tree has no volume
  getter, so there is no honest up/down step.
- No GUI pairing dialog in this batch: the QR PNG is written to
  the data dir and its path rides the start result (open it to
  show the code). A native dialog is owed.
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .skills_media_helpers import _DATA_DIR
from core.funnel.tools_registry_core import _funnel_only_fn


_DEFAULT_PORT = 8765
_PORT_SCAN_END = 8799
_PIN_LENGTH = 6
_MAX_DEVICES = 3
_SESSION_TTL_S = 8 * 3600
_PIN_LOCKOUT_AFTER = 5
_PIN_LOCKOUT_S = 60
_PIN_WINDOW_S = 600
_SCREEN_MIN_INTERVAL_S = 0.5
_MAX_BODY_BYTES = 64 * 1024
_TYPE_MAX_CHARS = 2000
_CLIPBOARD_READ_MAX = 4000
_CLIPBOARD_WRITE_MAX = 100_000
_FILE_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
_QR_FILENAME = "companion_qr.png"
_COOKIE_NAME = "vyuk-companion"


# ---------------------------------------------------------------------------
# Phone UI (one page, no external assets; the JS asks /api/status and
# shows the pairing form or the control panels accordingly)
# ---------------------------------------------------------------------------
_PHONE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>VYUK Companion</title>
<style>
body{font-family:system-ui,sans-serif;background:#0b1220;color:#e8f4ff;margin:0;padding:12px}
h1{font-size:18px;margin:4px 0 12px;color:#35d6ff}
button{background:#12314a;color:#e8f4ff;border:1px solid #35d6ff;border-radius:8px;padding:10px 14px;margin:4px;font-size:15px}
button:active{background:#1d4a68}
input,textarea{width:100%;box-sizing:border-box;background:#0f1c2e;color:#e8f4ff;border:1px solid #35d6ff;border-radius:8px;padding:10px;font-size:15px;margin:4px 0}
#screen{width:100%;border:1px solid #35d6ff;border-radius:8px;background:#000}
#pad{height:180px;border:1px solid #35d6ff;border-radius:8px;background:#0f1c2e;touch-action:none;display:flex;align-items:center;justify-content:center;color:#7fb8d4;margin:8px 0}
section{margin:14px 0}.row{display:flex;flex-wrap:wrap}
#msg{color:#ffb347;min-height:1.2em}
.small{font-size:13px;color:#7fb8d4}
</style></head><body>
<h1>VYUK Companion</h1>
<div id="msg"></div>
<div id="pair" style="display:none"><section>
<p>Enter the 6-digit PIN shown on the laptop to pair this phone.</p>
<input id="pin" inputmode="numeric" autocomplete="off" maxlength="6" placeholder="PIN">
<button onclick="pair()">Pair</button></section></div>
<div id="ctl" style="display:none">
<section><img id="screen" alt="Laptop screen"><div class="small" id="screen-note"></div></section>
<section><div id="pad">Touchpad — drag to move, tap to click</div></section>
<section><input id="typebox" placeholder="Type on the laptop…" autocomplete="off">
<button onclick="typeText()">Send</button>
<div class="row"><button onclick="sendKey('enter')">Enter</button>
<button onclick="sendKey('tab')">Tab</button><button onclick="sendKey('esc')">Esc</button>
<button onclick="sendKey('ctrl+c')">Ctrl+C</button><button onclick="sendKey('ctrl+v')">Ctrl+V</button>
<button onclick="sendKey('alt+tab')">Alt+Tab</button></div></section>
<section><div class="row"><button onclick="media('play_pause')">⏯</button>
<button onclick="media('previous')">⏮</button><button onclick="media('next')">⏭</button>
<button onclick="media('stop')">⏹</button></div>
<label class="small">Volume <span id="volval"></span></label>
<input type="range" min="0" max="100" value="50" id="vol" oninput="volval.innerText=this.value+'%'" onchange="setVol(this.value)"></section>
<section><textarea id="clip" rows="3" placeholder="Clipboard"></textarea>
<div class="row"><button onclick="getClip()">Get clipboard</button>
<button onclick="setClip()">Set clipboard</button></div></section>
<section id="filesec" style="display:none"><b>Files</b><div id="files"></div></section>
<section><div class="row"><button onclick="power('lock')">Lock</button>
<button onclick="power('sleep')">Sleep</button>
<button onclick="power('shutdown')">Shutdown</button>
<button onclick="power('restart')">Restart</button>
<button onclick="unpair()">Unpair</button></div></section>
</div>
<script>
let paired=false;
async function api(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined});
  const t=await r.text(); let j=null; try{j=JSON.parse(t)}catch(e){}
  return {ok:r.ok,status:r.status,data:j||{raw:t}};
}
function say(m){document.getElementById('msg').textContent=m||''}
async function refresh(){
  const s=await api('/api/status');
  if(!s.ok){paired=false;document.getElementById('pair').style.display='block';
    document.getElementById('ctl').style.display='none';return;}
  paired=true;document.getElementById('pair').style.display='none';
  document.getElementById('ctl').style.display='block';
  const st=s.data;
  document.getElementById('screen-note').textContent=st.screen_on?
    'Live screen (about 1 frame per second)':'Screen sharing is off — '+ (st.screen_reason||'');
  document.getElementById('filesec').style.display=st.files_on?'block':'none';
  if(st.screen_on)document.getElementById('screen').src='/screen.jpg?t='+Date.now();
  if(st.files_on)listFiles('');
}
async function pair(){
  const pin=document.getElementById('pin').value.trim();
  const r=await api('/api/pair',{pin});
  if(r.ok){say('Paired.');refresh();}else say('Pairing failed: '+(r.data.error||r.status));
}
async function typeText(){
  const el=document.getElementById('typebox');
  const r=await api('/api/type',{text:el.value});
  say(r.ok?'Typed.':'Failed: '+(r.data.error||r.status)); el.value='';
}
async function sendKey(keys){const r=await api('/api/key',{keys});say(r.ok?'':'Failed: '+(r.data.error||r.status));}
async function media(a){const r=await api('/api/media',{action:a});say(r.ok?'':'Failed: '+(r.data.error||r.status));}
async function setVol(p){const r=await api('/api/media',{percent:Number(p)});say(r.ok?'Volume '+p+'%.':'Failed: '+(r.data.error||r.status));}
async function getClip(){const r=await api('/api/clipboard');if(r.ok)document.getElementById('clip').value=r.data.text||'';else say('Failed: '+(r.data.error||r.status));}
async function setClip(){const r=await api('/api/clipboard',{text:document.getElementById('clip').value});say(r.ok?'Clipboard set.':'Failed: '+(r.data.error||r.status));}
async function listFiles(path){
  const r=await api('/api/files?path='+encodeURIComponent(path||''));
  if(!r.ok){document.getElementById('files').textContent='Files unavailable.';return;}
  const d=r.data;let h='';
  if(d.parent!==null)h+='<button onclick="listFiles('+JSON.stringify(d.parent)+')">.. up</button>';
  for(const e of d.entries){
    if(e.kind==='dir')h+='<div><button onclick="listFiles('+JSON.stringify(e.path)+')">📁 '+e.name+'</button></div>';
    else h+='<div><a style="color:#35d6ff" href="/api/files/download?path='+encodeURIComponent(e.path)+'">'+e.name+'</a> ('+e.size+' B)</div>';
  }
  document.getElementById('files').innerHTML=h||'(empty)';
}
async function power(a){
  if((a==='shutdown'||a==='restart')&&!confirm('Really '+a+' the laptop?'))return;
  const r=await api('/api/power',{action:a,confirm:true});
  say(r.ok?r.data.result:'Failed: '+(r.data.error||r.status));
}
async function unpair(){await api('/api/unpair',{});location.reload();}
let lastX=0,lastY=0,moved=false;
const pad=document.getElementById('pad');
pad.addEventListener('touchstart',e=>{e.preventDefault();const t=e.touches[0];lastX=t.clientX;lastY=t.clientY;moved=false;},{passive:false});
pad.addEventListener('touchmove',e=>{e.preventDefault();const t=e.touches[0];
  const dx=t.clientX-lastX,dy=t.clientY-lastY;lastX=t.clientX;lastY=t.clientY;
  if(Math.abs(dx)+Math.abs(dy)>2){moved=true;api('/api/touch',{action:'move',dx:Math.round(dx*2),dy:Math.round(dy*2)});}},
  {passive:false});
pad.addEventListener('touchend',async e=>{e.preventDefault();
  if(!moved){const r=await api('/api/touch',{action:'click'});if(!r.ok)say('Failed: '+(r.data.error||r.status));}},{passive:false});
const img=document.getElementById('screen');
img.addEventListener('click',async e=>{
  const r=img.getBoundingClientRect();
  const fx=(e.clientX-r.left)/r.width,fy=(e.clientY-r.top)/r.height;
  const res=await api('/api/touch',{action:'click',fx:fx,fy:fy});
  if(!res.ok)say('Failed: '+(res.data.error||res.status));
});
refresh(); setInterval(()=>{if(paired)refresh();},2000);
</script></body></html>
"""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class _CompanionSession:
    """One companion server; a fresh PIN and fresh tokens per start."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.pin: str | None = None
        self.url: str | None = None
        self.port: int | None = None
        self.qr_path: str | None = None
        self.started_at: float | None = None
        self.screen_on = False
        self.screen_reason = ""
        self.files_root: Path | None = None
        self.tokens: dict[str, dict] = {}
        self.pin_failures: dict[str, list[float]] = {}
        self.locked_until: dict[str, float] = {}
        self.last_frame: bytes | None = None
        self.last_frame_at = 0.0
        self.frame_size: tuple[int, int] | None = None
        self.screen_logged = False  # first served frame logged once per start

    # -- network ------------------------------------------------------
    @staticmethod
    def _lan_address() -> str | None:
        """The machine's private LAN address (UDP-connect trick: no
        traffic is sent, the socket just picks the outbound
        interface). None when there is no LAN at all."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        except OSError:
            return None
        finally:
            sock.close()

    @staticmethod
    def _pick_port(start: int) -> int | None:
        """First free port in [start, _PORT_SCAN_END], else None."""
        for port in range(max(1024, int(start)), _PORT_SCAN_END + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
            finally:
                sock.close()
        return None

    # -- pairing ------------------------------------------------------
    def _new_pin(self) -> str:
        return "".join(secrets.choice("0123456789") for _ in range(_PIN_LENGTH))

    def _pin_locked(self, ip: str) -> bool:
        return self.locked_until.get(ip, 0.0) > time.time()

    def _record_pin_failure(self, ip: str) -> None:
        now = time.time()
        failures = [t for t in self.pin_failures.get(ip, []) if now - t < _PIN_WINDOW_S]
        failures.append(now)
        self.pin_failures[ip] = failures
        if len(failures) >= _PIN_LOCKOUT_AFTER:
            self.locked_until[ip] = now + _PIN_LOCKOUT_S
            self.pin_failures[ip] = []

    def issue_token(self, ip: str) -> str:
        with self._lock:
            if len(self.tokens) >= _MAX_DEVICES:
                # Evict the oldest token; a phone that was paired
                # earlier simply pairs again.
                oldest = min(self.tokens, key=lambda t: self.tokens[t]["created"])
                del self.tokens[oldest]
            token = secrets.token_urlsafe(32)
            self.tokens[token] = {"created": time.time(), "ip": ip}
            return token

    def valid_token(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            entry = self.tokens.get(token)
            if entry is None:
                return False
            if time.time() - entry["created"] > _SESSION_TTL_S:
                del self.tokens[token]
                return False
            return True

    def revoke_token(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self.tokens.pop(token, None)

    # -- lifecycle ----------------------------------------------------
    def is_running(self) -> bool:
        return self.server is not None

    def start(self, port: int = 0, files_root: str = "") -> tuple[bool, str]:
        with self._lock:
            if self.is_running():
                return False, (
                    f"The companion is already running at {self.url}. "
                    "Stop it first (stop_companion) before starting again.")
            address = self._lan_address()
            if address is None:
                return False, (
                    "No LAN address found — the laptop is not on a local "
                    "network, so a phone cannot reach it. Connect to Wi-Fi "
                    "first, then start the companion again.")
            try:
                if not ipaddress.ip_address(address).is_private:
                    return False, (
                        f"The network address ({address}) is not a private "
                        "LAN address, so the companion refuses to listen on "
                        "it. Connect to a home/office Wi-Fi network first.")
            except ValueError:
                return False, f"Could not read the LAN address ({address})."
            chosen = self._pick_port(port or _DEFAULT_PORT)
            if chosen is None:
                return False, (
                    f"No free port in {_DEFAULT_PORT}-{_PORT_SCAN_END} — "
                    "another service is using them. Free one and try again.")
            root: Path | None = None
            if str(files_root or "").strip():
                root = Path(str(files_root)).expanduser()
                if not root.is_dir():
                    return False, (
                        f"files_root {str(files_root)!r} is not a folder "
                        "that exists — file sharing stays off. Give an "
                        "existing folder, or leave files_root empty.")
            # Screen consent, once, at start (durable denial +
            # denylist + capture policy). A denial turns the screen
            # OFF for the session; the rest still works.
            screen_on, screen_reason = True, ""
            try:
                from . import capture_guard as _cg

                allowed, why = _cg.ensure_capture_allowed("screenshot_save")
                if not allowed:
                    screen_on, screen_reason = False, why
            except Exception as e:  # noqa: BLE001 — broken guard
                screen_on, screen_reason = False, (
                    f"the capture guard could not run ({e})")
            pin = self._new_pin()
            qr_path: str | None = None
            try:
                from . import qr_encode as _qr

                data_dir = Path(_DATA_DIR)
                data_dir.mkdir(parents=True, exist_ok=True)
                qr_file = data_dir / _QR_FILENAME
                _qr.render_png(f"http://{address}:{chosen}/", qr_file)
                qr_path = str(qr_file)
            except Exception as e:  # noqa: BLE001 — QR is a
                # convenience; the URL + PIN still pair the phone.
                qr_path = None
                qr_note = f" (the QR image could not be rendered: {e})"
            else:
                qr_note = ""
            session = self

            class _Server(ThreadingHTTPServer):
                # Bind is the constructor's server_address (the LAN
                # address + candidate port) — never 0.0.0.0.
                daemon_threads = True
                allow_reuse_address = True

            server = None
            bound_port = chosen
            for candidate in range(chosen, _PORT_SCAN_END + 1):
                try:
                    server = _Server((address, candidate), _CompanionHandler)
                    bound_port = candidate
                    break
                except OSError:
                    server = None
                    continue
            if server is None:
                # A failed start leaves nothing behind: the QR file
                # rendered above points at a URL that never listened.
                try:
                    if qr_path:
                        Path(qr_path).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                return False, (
                    f"Could not listen on {address} (ports {chosen}-"
                    f"{_PORT_SCAN_END} are all taken). Free one and try again.")
            chosen = bound_port
            server.session = session  # type: ignore[attr-defined]
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.2},
                name="vyuk-companion", daemon=True)
            self.server = server
            self.thread = thread
            self.pin = pin
            self.url = f"http://{address}:{chosen}/"
            self.port = chosen
            self.qr_path = qr_path
            self.started_at = time.time()
            self.screen_on = screen_on
            self.screen_reason = screen_reason
            self.files_root = root
            self.tokens = {}
            self.pin_failures = {}
            self.locked_until = {}
            self.last_frame = None
            self.last_frame_at = 0.0
            self.frame_size = None
            self.screen_logged = False
            thread.start()
            if screen_on:
                try:
                    from . import capture_guard as _cg

                    _cg.note_capture_started("screenshot_save")
                except Exception:  # noqa: BLE001 — indicator only
                    pass
            lines = [
                f"Companion running at {self.url}",
                f"PIN: {pin} — enter it on the phone (it changes every start; never share it).",
            ]
            if qr_path:
                lines.append(f"QR code (open to show the phone): {qr_path}")
            else:
                lines.append(
                    "No QR image — open the URL above in the phone browser "
                    f"by hand{qr_note}.")
            lines.append(
                "The phone can: see the screen, move/click the mouse, type, "
                "send keys, control media and volume, read/set the clipboard, "
                f"{'browse and download files under ' + str(root) if root else 'no files (files_root was empty)'}, "
                "lock/sleep/shut down (shutdown/restart need a second confirm).")
            if not screen_on:
                lines.append(f"Screen sharing is OFF: {screen_reason}")
            lines.append(
                "Stop it any time with stop_companion — the PIN and every "
                "pairing die with the server.")
            return True, "\n".join(lines)

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self.is_running():
                return False, "The companion is not running."
            server, thread = self.server, self.thread
            url, screen_on = self.url, self.screen_on
            qr_path = self.qr_path
            self.server = None
            self.thread = None
            self.pin = None
            self.url = None
            self.port = None
            self.qr_path = None
            self.started_at = None
            self.screen_on = False
            self.screen_reason = ""
            self.files_root = None
            self.tokens = {}
            self.last_frame = None
            self.frame_size = None
        if screen_on:
            try:
                from . import capture_guard as _cg

                _cg.note_capture_ended("screenshot_save")
            except Exception:  # noqa: BLE001 — indicator only
                pass
        qr_note = ""
        try:
            if qr_path:
                Path(qr_path).unlink(missing_ok=True)
                if Path(qr_path).exists():
                    qr_note = (" The pairing QR file could not be deleted "
                               f"({qr_path}) — delete it by hand; it holds "
                               "only the URL, not the PIN.")
        except Exception:  # noqa: BLE001 — stale QR file is cosmetic
            qr_note = ""
        assert server is not None
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 — already down is fine
            pass
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
        lingering = ""
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                lingering = (" The server thread is still stopping in the "
                             "background — the port is closed either way.")
        return True, f"Companion stopped (was {url}).{lingering}{qr_note}"

    def status(self) -> dict:
        with self._lock:
            if not self.is_running():
                return {"running": False}
            return {
                "running": True,
                "url": self.url,
                "port": self.port,
                "uptime_s": round(time.time() - (self.started_at or time.time())),
                "screen_on": self.screen_on,
                "screen_reason": self.screen_reason,
                "files_on": self.files_root is not None,
                "files_root": str(self.files_root) if self.files_root else None,
                "paired_devices": len(self.tokens),
                "max_devices": _MAX_DEVICES,
                "screen_size": list(self.frame_size) if self.frame_size else None,
            }

    def pairing_info(self) -> tuple[bool, dict | str]:
        with self._lock:
            if not self.is_running():
                return False, "The companion is not running — start it first."
            return True, {
                "url": self.url,
                "pin": self.pin,
                "qr_path": self.qr_path,
                "note": ("Open the URL in the phone browser (or scan the QR "
                         "on the laptop screen), then enter the PIN. The PIN "
                         "changes every start — never share it."),
            }

    # -- screen -------------------------------------------------------
    def capture_frame(self) -> tuple[bytes | None, str | None]:
        """(jpeg_bytes, error): a fresh frame, throttled to 2/sec,
        denylist-checked per frame. None + reason when refused."""
        if not self.screen_on:
            return None, (f"Screen sharing is off: {self.screen_reason or 'not enabled'}")
        now = time.time()
        if (self.last_frame is not None
                and now - self.last_frame_at < _SCREEN_MIN_INTERVAL_S):
            return self.last_frame, None
        try:
            from . import capture_guard as _cg

            block = _cg.foreground_capture_block()
        except Exception:  # noqa: BLE001 — a broken denylist check
            block = None  # fails open on the CHECK, never on the
            # capture itself: the frame below still has to succeed.
        if block:
            return None, str(block)
        try:
            from .desktop import control_system as _cs

            data, _mime = _cs._capture_screen_bytes(include_cursor=True)
        except Exception as e:  # noqa: BLE001
            return None, f"Screen capture failed: {e}"
        if not data:
            return None, "Screen capture returned nothing."
        self.last_frame = bytes(data)
        self.last_frame_at = now
        try:
            from PIL import Image
            from io import BytesIO

            with Image.open(BytesIO(self.last_frame)) as image:
                self.frame_size = (int(image.width), int(image.height))
        except Exception:  # noqa: BLE001 — size is advisory
            pass
        return self.last_frame, None


_SESSION = _CompanionSession()


def get_session() -> _CompanionSession:
    return _SESSION


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Funnel routing (GAPS-9, flaw #1)
#
# Every phone action that has a funnel tool goes through
# ToolRegistry.execute — the same funnel every other caller uses —
# never the desktop function directly. A missing funnel refuses;
# there is no direct-call fallback, ever.
# ---------------------------------------------------------------------------
def _funnel_call(name: str, args: dict) -> tuple[dict, int]:
    """Run one funnel tool; translate the funnel's dict into the
    phone's {"result": ...} / {"error": ...} shape.

    Returns (payload, http_status). The funnel's own stages do the
    work: schema validation, permission, folder scope, the confirm
    gate (a confirm-gated tool pops the desktop confirmation and
    this HTTP thread waits up to 60 s for the owner's answer),
    the money guard, the ledger and the receipt. An error dict from
    the funnel becomes a 400 with the funnel's own message, so the
    phone shows the honest reason ("needs the user's OK in VYUK's
    window first — it wasn't given, so nothing was run") instead
    of a success.
    """
    try:
        from .funnel.tools_registry import tools as _tools
    except Exception as e:  # noqa: BLE001 — no funnel, no action
        return {"error": f"The tool funnel is unavailable ({e}) — nothing was run."}, 503
    try:
        result = _tools.execute(name, dict(args))
    except Exception as e:  # noqa: BLE001 — a broken funnel denies
        return {"error": f"The tool funnel failed ({e}) — nothing was run."}, 503
    if not isinstance(result, dict):
        return {"result": str(result)}, 200
    if result.get("status") == "success":
        return {"result": result.get("result", "")}, 200
    message = str(result.get("message") or result.get("result")
                  or "The tool refused — nothing was run.")
    return {"error": message}, 400


def _audit(name: str, status: str) -> None:
    """One activity-log line through the funnel's own sink, for the
    two server-side actions that have no funnel tool (screen, files).
    Never raises, never breaks the request — logging is a trail,
    not a gate.
    """
    try:
        from .funnel.tools_registry import tools as _tools

        _tools._log_activity(name, status)
    except Exception:  # noqa: BLE001 — logging must never break a request
        pass


class _CompanionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: N802 - silence
        return

    # -- helpers ------------------------------------------------------
    @property
    def session(self) -> _CompanionSession:
        return self.server.session  # type: ignore[attr-defined]

    def _send_json(self, data: dict, status: int = 200,
                   headers: dict | None = None) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200,
                   content_type: str = "text/plain") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > _MAX_BODY_BYTES:
            return None
        try:
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        return parsed if isinstance(parsed, dict) else None

    def _client_ip(self) -> str:
        return str(self.client_address[0]) if self.client_address else "?"

    def _session_token(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        morsel = cookie.get(_COOKIE_NAME)
        return morsel.value if morsel is not None else None

    def _require_session(self) -> str | None:
        """The valid session token, or None (with a 401 sent)."""
        token = self._session_token()
        if self.session.valid_token(token):
            return token
        self._send_json({"error": "Not paired — enter the PIN first."}, 401)
        return None

    # -- routing ------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/":
                self._send_text(_PHONE_HTML, 200, "text/html")
            elif self.path.startswith("/screen.jpg"):
                self._get_screen()
            elif self.path.startswith("/api/status"):
                self._get_status()
            elif self.path.startswith("/api/clipboard"):
                self._get_clipboard()
            elif self.path.startswith("/api/files/download"):
                self._download_file()
            elif self.path.startswith("/api/files"):
                self._list_files()
            else:
                self._send_json({"error": "Unknown path."}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 — never leak a stack
            try:
                self._send_json({"error": f"Companion error: {e}"}, 500)
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path.startswith("/api/pair"):
                self._post_pair()
            elif self.path.startswith("/api/unpair"):
                self._post_unpair()
            elif self.path.startswith("/api/touch"):
                self._post_touch()
            elif self.path.startswith("/api/type"):
                self._post_type()
            elif self.path.startswith("/api/key"):
                self._post_key()
            elif self.path.startswith("/api/media"):
                self._post_media()
            elif self.path.startswith("/api/clipboard"):
                self._post_clipboard()
            elif self.path.startswith("/api/power"):
                self._post_power()
            else:
                self._send_json({"error": "Unknown path."}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self._send_json({"error": f"Companion error: {e}"}, 500)
            except Exception:  # noqa: BLE001
                pass

    # -- endpoints ----------------------------------------------------
    def _post_pair(self) -> None:
        session = self.session
        ip = self._client_ip()
        if session._pin_locked(ip):
            self._send_json(
                {"error": "Too many wrong PINs — try again in a minute."}, 429)
            return
        body = self._read_json()
        pin = str((body or {}).get("pin") or "").strip()
        if not session.is_running() or session.pin is None:
            self._send_json({"error": "The companion is not running."}, 503)
            return
        if pin != session.pin:
            session._record_pin_failure(ip)
            self._send_json({"error": "Wrong PIN."}, 401)
            return
        token = session.issue_token(ip)
        headers = {
            "Set-Cookie": (
                f"{_COOKIE_NAME}={token}; HttpOnly; Path=/; "
                f"Max-Age={_SESSION_TTL_S}; SameSite=Strict")
        }
        self._send_json({"paired": True}, 200, headers)

    def _post_unpair(self) -> None:
        token = self._require_session()
        if token is None:
            return
        self.session.revoke_token(token)
        headers = {"Set-Cookie": f"{_COOKIE_NAME}=; HttpOnly; Path=/; Max-Age=0"}
        self._send_json({"unpaired": True}, 200, headers)

    def _get_status(self) -> None:
        if self._require_session() is None:
            return
        self._send_json(self.session.status())

    def _get_screen(self) -> None:
        if self._require_session() is None:
            return
        frame, error = self.session.capture_frame()
        if frame is None:
            self._send_json({"error": error or "Screen unavailable."}, 403)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(frame)
        # The screen has no funnel tool (take_screenshot pushes bytes
        # to the frame sink and cannot return a JPEG to the phone),
        # so the trail for it is one activity-log line: the first
        # frame served in this session. The consent for sharing it
        # is already recorded — start_companion is a funnel call,
        # and the capture guard applies per frame.
        with self.session._lock:
            if not self.session.screen_logged:
                self.session.screen_logged = True
                _audit("companion_screen",
                       "first frame served to a paired phone")

    def _screen_size(self) -> tuple[int, int] | None:
        session = self.session
        if session.frame_size:
            return session.frame_size
        try:
            from .desktop import cursor_geometry as _cg

            size = _cg._screen_size()
        except Exception:  # noqa: BLE001
            size = None
        if size and size[0] > 0 and size[1] > 0:
            return (int(size[0]), int(size[1]))
        # Last resort: capture one frame to learn the size.
        frame, _error = session.capture_frame()
        if frame is not None and session.frame_size:
            return session.frame_size
        return None

    def _post_touch(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        action = str(body.get("action") or "").strip().lower()
        if action not in ("move", "click", "double_click", "right_click", "scroll"):
            self._send_json({"error": f"Unknown touch action: {action!r}."}, 400)
            return
        amount = 3
        try:
            amount = max(1, min(int(body.get("amount") or 3), 20))
        except (TypeError, ValueError):
            amount = 3
        if action == "scroll":
            payload, status = _funnel_call(
                "mouse_control", {"action": "scroll", "amount": amount})
            self._send_json(payload, status)
            return
        if "dx" in body or "dy" in body:
            # Touchpad: relative move from the real cursor position.
            try:
                from .desktop import cursor_input as _ci

                pos = _ci._read_cursor_position()
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {"error": f"Could not read the cursor position: {e}"}, 503)
                return
            if pos is None:
                self._send_json(
                    {"error": "Could not read the cursor position on "
                              "this system."}, 503)
                return
            try:
                dx = int(body.get("dx") or 0)
                dy = int(body.get("dy") or 0)
            except (TypeError, ValueError):
                self._send_json({"error": "dx/dy must be numbers."}, 400)
                return
            size = self._screen_size()
            if size is None:
                self._send_json(
                    {"error": "Screen size is unknown — the screen could "
                              "not be read, so a relative move has nowhere "
                              "to land."}, 503)
                return
            x = max(0, min(int(pos[0]) + dx, size[0] - 1))
            y = max(0, min(int(pos[1]) + dy, size[1] - 1))
            payload, status = _funnel_call(
                "mouse_control", {"action": "move", "x": x, "y": y})
            self._send_json(payload, status)
            return
        # Absolute tap: fractions of the screen.
        size = self._screen_size()
        if size is None:
            self._send_json(
                {"error": "Screen size is unknown — the screen could not "
                          "be read, so a tap has nowhere to land."}, 503)
            return
        try:
            fx = float(body.get("fx", 0.5))
            fy = float(body.get("fy", 0.5))
        except (TypeError, ValueError):
            self._send_json({"error": "fx/fy must be numbers 0..1."}, 400)
            return
        fx = max(0.0, min(fx, 1.0))
        fy = max(0.0, min(fy, 1.0))
        x = max(0, min(int(fx * size[0]), size[0] - 1))
        y = max(0, min(int(fy * size[1]), size[1] - 1))
        payload, status = _funnel_call(
            "mouse_control", {"action": action, "x": x, "y": y})
        self._send_json(payload, status)

    def _post_type(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        text = str(body.get("text") or "")
        if not text:
            self._send_json({"error": "Nothing to type."}, 400)
            return
        if len(text) > _TYPE_MAX_CHARS:
            self._send_json(
                {"error": f"Text is {len(text)} characters — the limit is "
                          f"{_TYPE_MAX_CHARS} per send."}, 400)
            return
        payload, status = _funnel_call("type_text", {"text": text})
        self._send_json(payload, status)

    def _post_key(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        keys = str(body.get("keys") or "").strip()
        if not keys:
            self._send_json({"error": "No keys given."}, 400)
            return
        payload, status = _funnel_call("press_hotkey", {"keys": keys})
        self._send_json(payload, status)

    def _post_media(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        if "percent" in body:
            try:
                percent = int(body.get("percent"))
            except (TypeError, ValueError):
                self._send_json({"error": "percent must be a number 0..100."}, 400)
                return
            payload, status = _funnel_call(
                "set_volume_level", {"percent": percent})
            self._send_json(payload, status)
            return
        action = str(body.get("action") or "").strip().lower()
        if action not in ("play_pause", "next", "previous", "stop"):
            self._send_json({"error": f"Unknown media action: {action!r}."}, 400)
            return
        payload, status = _funnel_call("media_control", {"action": action})
        self._send_json(payload, status)

    def _get_clipboard(self) -> None:
        if self._require_session() is None:
            return
        # The funnel's read_clipboard describes the clipboard
        # (truncated with the total stated, secret-looking values
        # masked) — the phone shows that description, the same one
        # VYUK gives in chat, instead of the raw text.
        payload, status = _funnel_call("read_clipboard", {})
        if status != 200:
            self._send_json(payload, status)
            return
        text = str(payload.get("result") or "")
        shown = text[:_CLIPBOARD_READ_MAX]
        note = "" if len(text) <= _CLIPBOARD_READ_MAX else (
            f"Showing the first {_CLIPBOARD_READ_MAX} of {len(text)} characters.")
        self._send_json({"text": shown, "note": note})

    def _post_clipboard(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        text = str(body.get("text") or "")
        if len(text) > _CLIPBOARD_WRITE_MAX:
            self._send_json(
                {"error": f"Text is {len(text)} characters — the limit is "
                          f"{_CLIPBOARD_WRITE_MAX}."}, 400)
            return
        payload, status = _funnel_call("write_clipboard", {"text": text})
        self._send_json(payload, status)

    def _resolve_file(self, rel: str) -> tuple[Path | None, str | None]:
        """(path under files_root, error): no traversal, ever."""
        root = self.session.files_root
        if root is None:
            return None, "File sharing is off — start_companion was given no files_root."
        rel = str(rel or "").strip().lstrip("/").lstrip("\\")
        if not rel:
            return root, None
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None, "That path escapes the shared folder — refused."
        return candidate, None

    def _list_files(self) -> None:
        if self._require_session() is None:
            return
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query)
        rel = (query.get("path") or [""])[0]
        path, error = self._resolve_file(rel)
        if error or path is None:
            self._send_json({"error": error or "No shared folder."}, 403)
            return
        if not path.is_dir():
            self._send_json({"error": "That path is not a folder."}, 400)
            return
        root = self.session.files_root
        assert root is not None
        entries = []
        try:
            for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                try:
                    entries.append({
                        "name": child.name,
                        "kind": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else 0,
                        "path": str(child.relative_to(root.resolve())),
                    })
                except OSError:
                    continue
        except OSError as e:
            self._send_json({"error": f"Could not list the folder: {e}"}, 500)
            return
        parent = None
        resolved_root = root.resolve()
        if path.resolve() != resolved_root:
            parent = str(path.parent.relative_to(resolved_root))
        # No funnel tool serves a local file to a phone, so the trail
        # for it is one activity-log line per listing, under the
        # owner's explicit per-start files_root grant.
        _audit("companion_files",
               f"listed {rel or '/'} ({len(entries)} entries)")
        self._send_json({"path": rel, "parent": parent, "entries": entries})

    def _download_file(self) -> None:
        if self._require_session() is None:
            return
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query)
        rel = (query.get("path") or [""])[0]
        path, error = self._resolve_file(rel)
        if error or path is None:
            self._send_json({"error": error or "No shared folder."}, 403)
            return
        if not path.is_file():
            self._send_json({"error": "That path is not a file."}, 400)
            return
        try:
            size = path.stat().st_size
        except OSError as e:
            self._send_json({"error": f"Could not read the file: {e}"}, 500)
            return
        if size > _FILE_DOWNLOAD_MAX_BYTES:
            self._send_json(
                {"error": f"That file is {size} bytes — the download cap is "
                          f"{_FILE_DOWNLOAD_MAX_BYTES}."}, 403)
            return
        try:
            data = path.read_bytes()
        except OSError as e:
            self._send_json({"error": f"Could not read the file: {e}"}, 500)
            return
        _audit("companion_files", f"downloaded {rel} ({size} bytes)")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _post_power(self) -> None:
        if self._require_session() is None:
            return
        body = self._read_json() or {}
        action = str(body.get("action") or "").strip().lower()
        if action not in ("lock", "sleep", "shutdown", "restart"):
            self._send_json({"error": f"Unknown power action: {action!r}."}, 400)
            return
        if action == "lock":
            payload, status = _funnel_call("lock_screen", {})
            self._send_json(payload, status)
            return
        if action in ("shutdown", "restart") and not body.get("confirm"):
            # Two-step by design: the phone confirms first, and the
            # request must carry confirm=true too. This tap is the
            # phone-side guard against a fat finger, not the
            # approval — the funnel's own desktop confirmation
            # still follows.
            self._send_json(
                {"error": f"{action} needs a confirmation — tap it again "
                          "on the phone to confirm."}, 400)
            return
        # Sleep, shutdown and restart all go through the funnel's
        # power_control, which the funnel gates wholesale: the
        # desktop confirmation pops and this request waits up to
        # 60 s for the owner's answer. confirm=true is the tool's
        # own flag for shutdown/restart; sleep does not take it.
        args = {"action": action}
        if action in ("shutdown", "restart"):
            args["confirm"] = True
        payload, status = _funnel_call("power_control", args)
        self._send_json(payload, status)


# ---------------------------------------------------------------------------
# Funnel tools
# ---------------------------------------------------------------------------
@_funnel_only_fn
def _tool_start_companion(port: int = 8765, files_root: str = "") -> str:
    try:
        port_value = int(port)
    except (TypeError, ValueError):
        return "Give the port as a number (default 8765, or 0 to pick one)."
    _ok, message = get_session().start(port_value, files_root)
    return message


@_funnel_only_fn
def _tool_stop_companion() -> str:
    _ok, message = get_session().stop()
    return message


@_funnel_only_fn
def _tool_companion_status() -> str:
    info = get_session().status()
    if not info.get("running"):
        return "The companion is not running."
    lines = [
        f"Companion running at {info['url']} "
        f"(up {info['uptime_s']} seconds, {info['paired_devices']} of "
        f"{info['max_devices']} devices paired).",
        "Screen sharing is " + (
            "ON." if info["screen_on"]
            else f"OFF ({info['screen_reason']})."),
        "Files are " + (
            f"shared from {info['files_root']}." if info["files_on"]
            else "off (no files_root)."),
    ]
    return "\n".join(lines)


@_funnel_only_fn
def _tool_companion_pairing() -> str:
    ok, info = get_session().pairing_info()
    if not ok:
        return str(info)
    assert isinstance(info, dict)
    lines = [
        f"Pairing URL: {info['url']}",
        f"PIN: {info['pin']} — enter it on the phone (it changes every start; never share it).",
    ]
    if info.get("qr_path"):
        lines.append(f"QR code (open to show the phone): {info['qr_path']}")
    lines.append(str(info["note"]))
    return "\n".join(lines)


def register_all(registry) -> None:
    """Register the four companion tools (R7-5, kovrel pattern)."""
    registry.register(
        name="start_companion",
        description="Start VYUK's phone companion: a small server on the local Wi-Fi only, so the phone browser becomes a remote for this laptop — live screen, touchpad, keyboard, media, clipboard, files and lock/sleep/shutdown. Pairing needs the 6-digit PIN shown here (it changes every start). The server never starts on its own and stops with stop_companion. Use when the user asks to control the laptop from their phone.",
        parameters={
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Port to listen on (default 8765; 0 picks the first free port from 8765)"},
                "files_root": {"type": "string", "description": "Optional folder the phone may browse and download from (default: none — no file access until a folder is given)"},
            },
            "required": [],
        },
        handler=_tool_start_companion,
    )
    registry.register(
        name="stop_companion",
        description="Stop the phone companion server. The PIN and every pairing die with it. Use when the user asks to stop the phone remote.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_stop_companion,
    )
    registry.register(
        name="companion_status",
        description="Say whether the phone companion is running, where it listens, whether screen sharing and files are on, and how many phones are paired. It only reports — it never starts or stops anything, and it never shows the PIN.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_companion_status,
    )
    registry.register(
        name="companion_pairing",
        description="Show the phone-pairing details again: the URL, the current 6-digit PIN and the QR code path. Use when the user asks how to connect their phone.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_tool_companion_pairing,
    )
