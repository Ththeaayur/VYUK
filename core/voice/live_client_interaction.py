from __future__ import annotations

# INTENTIONAL (skills split B47): interaction-tracking family moved
# from live_client.py. Pure helpers: no sibling out-edges.


def _interaction_status_of(server_content) -> str | None:
    """The normalised interaction_status value on a server
    content message ("IN_PROGRESS" / "IDLE" / …), or None when
    the message carries none (the 2.5 models, older SDKs)."""
    try:
        status = getattr(server_content, "interaction_status", None)
        if status is None:
            return None
        value = getattr(status, "value", status)
        text = str(value).strip()
        return text or None
    except Exception:  # noqa: BLE001 — a status read must never
        return None  # break the receive loop


def _call_id_of(fc) -> str | None:
    """A function call's server id as a string, or None when the
    call carries none (id-less calls can never be cancelled —
    the cancellation message names ids, and None must not match
    a recorded "None")."""
    try:
        cid = getattr(fc, "id", None)
        return str(cid) if cid else None
    except Exception:  # noqa: BLE001
        return None


def _turn_status_notification(prev_status: str | None,
                              new_status: str | None,
                              turn_complete: bool) -> str | None:
    """The status notification a server-content message earns,
    judged against the tracked interaction status: "LISTENING"
    when the interaction is over, else None.

    IN_PROGRESS vetoes the notification even when the message
    carries turn_complete — the model has background work (a
    non-blocking call, continued reasoning) and flipping to
    LISTENING would tell the user she is done when she is not;
    the notification lands later, on the message that TRANSITS
    the interaction to IDLE (a bare IDLE repeat on unrelated
    messages must not re-notify). With no status at all (the
    2.5 models), every turn_complete notifies, byte-identical
    to the old rule."""
    effective = new_status if new_status is not None else prev_status
    if effective == "IN_PROGRESS":
        return None
    if turn_complete:
        return "LISTENING"
    if new_status == "IDLE" and prev_status == "IN_PROGRESS":
        return "LISTENING"
    return None
