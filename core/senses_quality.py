"""Split from core/senses.py (Phase B batch B4): family impl module. Move only — see senses.py facade."""

import io
import socket
import statistics
import time


# ---------------------------------------------------------------------------
# Network quality
# ---------------------------------------------------------------------------

_QUALITY_HOSTS = (("1.1.1.1", 53), ("8.8.8.8", 53))
_QUALITY_SAMPLES = 5


def _quality_target(host: str):
    """Resolve the measurement target: None for the default
    DNS pair, a (host, port) tuple for a custom host, or
    False when the string can't be a target at all. A bare
    IP measures on port 53 (DNS) like the defaults; a host
    name on 443 (HTTPS); an explicit 'host:port' wins."""
    import ipaddress

    text = (host or "").strip()
    if not text:
        return None
    if any(c in text for c in " /\\") or "://" in text:
        return False
    name, port = text, None
    if text.count(":") == 1:
        head, _, tail = text.partition(":")
        if tail.isdigit():
            name, port = head, int(tail)
    try:
        ipaddress.ip_address(name)
        return (name, port or 53)
    except ValueError:
        return (name, port or 443)


def network_quality(host: str = "") -> dict:
    """Five timed TCP connects, alternating 1.1.1.1:53 / 8.8.8.8:53.

    With `host`, all five samples go to that one target
    instead (a specific server or site the user cares
    about). latency_ms is the median successful connect
    time; jitter_ms the mean absolute difference between
    successive successful samples. Zero successes reads as
    honestly unreachable. The dict's "target" names what
    was measured."""
    target = _quality_target(host)
    if target is False:
        return {"reachable": False, "latency_ms": None,
                "jitter_ms": None, "samples": 0,
                "verdict": "unreachable",
                "target": (host or "").strip()}
    label = (f"{target[0]}:{target[1]}" if target
             else "public DNS servers (1.1.1.1 / 8.8.8.8)")
    try:
        samples: list[float] = []
        for i in range(_QUALITY_SAMPLES):
            host = target or _QUALITY_HOSTS[i % len(_QUALITY_HOSTS)]
            started = time.perf_counter()
            try:
                with socket.create_connection(host, timeout=2):
                    samples.append(
                        (time.perf_counter() - started) * 1000.0)
            except Exception:  # noqa: BLE001 — a failed sample is data
                pass
        if not samples:
            return {"reachable": False, "latency_ms": None,
                    "jitter_ms": None, "samples": 0,
                    "verdict": "unreachable", "target": label}
        latency = statistics.median(samples)
        if len(samples) > 1:
            diffs = [abs(b - a) for a, b in zip(samples, samples[1:])]
            jitter = sum(diffs) / len(diffs)
        else:
            jitter = 0.0
        if latency < 50:
            verdict = "excellent"
        elif latency < 120:
            verdict = "good"
        elif latency < 250:
            verdict = "fair"
        else:
            verdict = "slow"
        return {"reachable": True,
                "latency_ms": round(latency, 1),
                "jitter_ms": round(jitter, 1),
                "samples": len(samples),
                "verdict": verdict, "target": label}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "latency_ms": None,
                "jitter_ms": None, "samples": 0,
                "verdict": f"unreachable ({e})", "target": label}


# ---------------------------------------------------------------------------
# Frame brightness
# ---------------------------------------------------------------------------


def frame_brightness(image_bytes: bytes) -> dict:
    """Mean luminance of an image, as captured by a camera.

    {"brightness": 0..1, "label": dark|dim|lit|bright, "note": str}.
    The frame is reduced to greyscale and downscaled to at most
    64 px on a side before averaging, so this stays instant.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            grey = img.convert("L")
            grey.thumbnail((64, 64))
            pixels = list(grey.getdata())
        if not pixels:
            return {"brightness": 0.0, "label": "dark",
                    "note": "The frame had no pixels to measure."}
        value = (sum(pixels) / len(pixels)) / 255.0
        if value < 0.15:
            label, note = "dark", "Very dark — night-time or a covered lens."
        elif value < 0.35:
            label, note = "dim", "Dim light — fine for screens, poor for faces."
        elif value < 0.65:
            label, note = "lit", "Comfortably lit room."
        else:
            label, note = "bright", "Bright — daylight or strong lights."
        return {"brightness": round(value, 3), "label": label,
                "note": note}
    except Exception as e:  # noqa: BLE001
        return {"brightness": 0.0, "label": "dark",
                "note": f"Couldn't read that frame: {e}"}

