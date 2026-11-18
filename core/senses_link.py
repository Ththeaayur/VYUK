"""Split from core/senses.py (Phase B batch B4): family impl module. Move only — see senses.py facade."""

import socket
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# INTENTIONAL (senses split B4): _build_sniff_opener lives in
# senses_wifi (proxy family); sniff_link imports it from there.
from .senses_wifi import _build_sniff_opener

# INTENTIONAL (senses split B4): _UA moves with its sole consumer
# (sniff_link below); it was defined beside the proxy opener in
# the old module but used nowhere else.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 VYUK/5.20")


# ---------------------------------------------------------------------------
# sniff_link — where does this link really go
# ---------------------------------------------------------------------------

_MAX_REDIRECTS = 8
_LINK_TIMEOUT = 8
_SHORTENER_HOSTS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl",
                    "rb.gy", "cutt.ly", "shorturl.at"}


def is_shortener(url_or_host: str) -> bool:
    """True when the URL's host is a known link shortener (whose
    whole job is hiding the real destination)."""
    try:
        text = (url_or_host or "").strip()
        host = urllib.parse.urlsplit(
            text if "://" in text else f"//{text}").hostname or ""
        return host.lower() in _SHORTENER_HOSTS
    except Exception:  # noqa: BLE001
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so sniff_link can walk the chain itself,
    one recorded hop at a time."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_cert_date(text: str):
    cleaned = " ".join((text or "").split())
    for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _cert_expiry(host: str, port: int = 443) -> dict | None:
    """{expires: iso-date, days_left: int} for a host's TLS
    certificate, or None. The handshake is deliberately UNVERIFIED
    (like ssl_cert_info): a verifying handshake would refuse the
    expired/broken certificates this exists to report on. Nothing
    is trusted from the connection; only the dates are read."""
    try:
        ctx = ssl._create_unverified_context()
        with socket.create_connection((host, port),
                                      timeout=_LINK_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der = ssock.getpeercert(binary_form=True) or b""
        if not der:
            return None
        pem = ssl.DER_cert_to_PEM_cert(der)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".pem", delete=False) as fh:
                fh.write(pem)
                tmp = fh.name
            decoded = ssl._ssl._test_decode_cert(tmp)
        finally:
            if tmp:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
        not_after = _parse_cert_date(decoded.get("notAfter", ""))
        if not_after is None:
            return None
        today = datetime.now(timezone.utc).date()
        return {"expires": not_after.isoformat(),
                "days_left": (not_after - today).days}
    except Exception:  # noqa: BLE001
        return None


def sniff_link(url) -> dict:
    """Follow a link's redirect chain by hand (<= 8 hops) through
    the system proxy and report where it lands: final status,
    content type, server, whether it started at a shortener, and
    the final host's certificate expiry when it's HTTPS."""
    try:
        raw = (url or "").strip()
        scheme = urllib.parse.urlsplit(raw).scheme.lower()
        if scheme not in ("http", "https"):
            return {"ok": False,
                    "verdict": "I only sniff http and https links."}
        opener = _build_sniff_opener((_NoRedirect(),))
        hops: list[dict] = []
        current = raw
        final: dict = {}
        for _ in range(_MAX_REDIRECTS + 1):
            req = urllib.request.Request(
                current, headers={"User-Agent": _UA, "Accept": "*/*"})
            try:
                with opener.open(req, timeout=_LINK_TIMEOUT) as resp:
                    status = resp.getcode() or 0
                    headers = resp.headers
                    hops.append({"url": current, "status": status})
                    final = {
                        "status": status,
                        "content_type": headers.get("Content-Type", "")
                        if headers else "",
                        "server": headers.get("Server", "")
                        if headers else "",
                        "content_length": headers.get("Content-Length", "")
                        if headers else "",
                    }
                    current = resp.geturl() or current
                    break
            except urllib.error.HTTPError as e:
                status = e.code or 0
                headers = getattr(e, "headers", None) \
                    or getattr(e, "hdrs", None)
                location = headers.get("Location", "") if headers else ""
                hops.append({"url": current, "status": status})
                if 300 <= status < 400 and location:
                    current = urllib.parse.urljoin(current, location)
                    final = {}
                    continue
                final = {
                    "status": status,
                    "content_type": headers.get("Content-Type", "")
                    if headers else "",
                    "server": headers.get("Server", "")
                    if headers else "",
                    "content_length": headers.get("Content-Length", "")
                    if headers else "",
                }
                break
            except Exception as e:  # noqa: BLE001 — honest unreachable
                hops.append({"url": current, "status": None})
                return {"ok": False, "url": raw, "hops": hops,
                        "shortener": is_shortener(raw),
                        "verdict": f"I couldn't reach it: {e}"}
        else:
            return {"ok": False, "url": raw, "hops": hops,
                    "shortener": is_shortener(raw),
                    "verdict": f"Too many redirects — more than "
                               f"{_MAX_REDIRECTS} hops, so I stopped."}
        # `current` always names the URL that produced the final
        # answer: the loop only advances it along redirects.
        final_url = current
        host = urllib.parse.urlsplit(final_url).hostname or ""
        tls = None
        if urllib.parse.urlsplit(final_url).scheme == "https" and host:
            tls = _cert_expiry(host)
        redirects = max(0, len(hops) - 1)
        parts = []
        if is_shortener(raw):
            start_host = urllib.parse.urlsplit(raw).hostname or "a shortener"
            parts.append(f"It starts at a link shortener ({start_host}), "
                         "which hides the real destination.")
        if redirects:
            parts.append(f"It leads to {host} after "
                         f"{redirects} redirect(s).")
        else:
            parts.append(f"It answers directly at {host}.")
        if final.get("status"):
            parts.append(f"Final answer: HTTP {final['status']}.")
        if tls is not None:
            if tls["days_left"] < 0:
                parts.append("Its certificate has EXPIRED — "
                             "browsers will warn about this site.")
            else:
                parts.append(f"Its certificate expires in "
                             f"{tls['days_left']} day(s).")
        return {"ok": True, "url": raw, "hops": hops,
                "final_url": final_url,
                "status": final.get("status"),
                "content_type": final.get("content_type", ""),
                "server": final.get("server", ""),
                "content_length": final.get("content_length", ""),
                "shortener": is_shortener(raw),
                "tls": tls,
                "verdict": " ".join(parts)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "verdict": f"I couldn't sniff that link: {e}"}

