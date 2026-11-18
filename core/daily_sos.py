from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.

# ===========================================================================
# PF-26 — SOS notify
# ===========================================================================

class SosVerdict:
    """Delivery verdict for one SOS send. ``confirmed`` is True
    ONLY when at least one channel confirmed delivery. Unknown
    status is failure, never success."""

    def __init__(self, per_channel: dict, message: str):
        self.per_channel = dict(per_channel)
        self.confirmed = any(v == "confirmed" for v in per_channel.values())
        self.announcement = message

    def to_dict(self) -> dict:
        return {"per_channel": self.per_channel,
                "confirmed": self.confirmed,
                "announcement": self.announcement}


def _normalise_sender_result(result) -> str:
    """Sender results collapse to confirmed / failed / unknown.
    Only an explicit confirmation counts: True, "confirmed", or a
    dict with delivery == "confirmed". Everything else — False,
    "unknown", exceptions, any other shape — is unknown-or-failed,
    and the caller treats unknown as FAILURE."""
    if result is True:
        return "confirmed"
    if isinstance(result, str):
        lowered = result.strip().lower()
        if lowered == "confirmed":
            return "confirmed"
        if lowered in ("failed", "failure", "error"):
            return "failed"
        return "unknown"
    if isinstance(result, dict):
        status = str(result.get("delivery") or result.get("status") or "").strip().lower()
        if status == "confirmed":
            return "confirmed"
        if status in ("failed", "failure", "error"):
            return "failed"
        return "unknown"
    return "unknown"


def send_sos(message, channels, sender) -> SosVerdict:
    """Send an SOS message; return delivery verdicts only.

    ``channels`` is a list of channel names (e.g. ["sms",
    "email"]); ``sender(channel, message)`` is the caller-supplied
    delivery function. A channel whose sender raises, returns
    nothing recognisable, or reports unknown status is recorded
    as "unknown" and counts as FAILURE. Success is announced
    ONLY when at least one channel returns a confirmation; with
    no confirmed delivery channel the announcement plainly says
    delivery FAILED — never a success, never silence.
    """
    text = str(message or "").strip()
    per_channel: dict = {}
    if not text:
        return SosVerdict(per_channel,
                          "SOS was not sent: there was no message to send. "
                          "Delivery FAILED — nothing was delivered.")
    if not channels:
        return SosVerdict(per_channel,
                          "SOS delivery FAILED: no delivery channel is "
                          "configured, so nothing was sent. Please call "
                          "for help directly.")
    if sender is None:
        for channel in channels:
            per_channel[str(channel)] = "unknown"
        return SosVerdict(per_channel,
                          "SOS delivery FAILED: no sender is available, "
                          "so delivery could not be confirmed for any "
                          "channel. Please call for help directly.")
    for channel in channels:
        name = str(channel)
        try:
            result = sender(name, text)
        except Exception:  # noqa: BLE001 — a raising sender is not a delivery
            per_channel[name] = "unknown"
            continue
        per_channel[name] = _normalise_sender_result(result)
    confirmed = [c for c, v in per_channel.items() if v == "confirmed"]
    if confirmed:
        return SosVerdict(
            per_channel,
            "SOS delivered — confirmed via: " + ", ".join(confirmed) + ".")
    failed = [c for c, v in per_channel.items() if v != "confirmed"]
    return SosVerdict(
        per_channel,
        "SOS delivery FAILED — no channel confirmed delivery "
        "(unconfirmed: " + ", ".join(failed) + "). Nothing is "
        "assumed delivered. Please call for help directly.")


