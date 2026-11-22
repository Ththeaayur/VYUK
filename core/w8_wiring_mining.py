"""w8_wiring part module (skills split B14): w8_wiring_mining."""
from __future__ import annotations

# Module-level imports travel verbatim with the units that use them.
from . import trace_mining



# ---------------------------------------------------------------------
# Trace-mining over the hash-chained records store (item 10)
# ---------------------------------------------------------------------


def live_failure_records(limit: int = 200) -> list[dict]:
    """Read failure records from the HASH-CHAINED records store
    (core/audit_chain.read), scrubbed BEFORE they reach the
    miner. Raw log text never leaves this reader: every record
    passes through trace_mining.scrub_record, and only records
    with a failure signal (error/message text, non-success
    status) are returned. Returns scrubbed record dicts in
    the miner's shape (tool/error/message/kind/ts)."""
    from . import audit_chain

    # Loophole batch 6 (#12): mine the VERIFIED prefix only — a
    # failure record at or after the chain's first break is
    # untrusted (it may be forged) and must not reach the miner.
    entries = audit_chain.read_trusted(kind=None,
                                       limit=limit)["entries"]
    records: list[dict] = []
    for entry in entries:
        data = entry.get("data") or {}
        if not isinstance(data, dict):
            continue
        error = (data.get("error") or data.get("message") or "")
        status = str(data.get("status") or "")
        kind = str(entry.get("kind") or "")
        if not error and status.lower() not in ("failed", "failure",
                                                "error"):
            continue
        record = {"tool": str(data.get("tool") or ""),
                  "error": str(error),
                  "message": str(data.get("message") or ""),
                  "kind": kind or "failure",
                  "ts": str(entry.get("ts") or "")}
        scrubbed, _findings = trace_mining.scrub_record(record)
        records.append(scrubbed)
    return records




def commit_mined_case(cluster: dict, expected_behaviour: str,
                      prompt: str, directory) -> tuple[str, bool]:
    """Reviewed case-commit path: a mined cluster + HUMAN-
    supplied expected_behaviour + prompt -> a Wave 2 seeded
    case written via trace_mining.write_case (fail-closed on
    any surviving secret shape; identical re-write is a no-op;
    differing overwrite needs a human allow_update decision,
    which this function never grants). Zero cases are
    committed by any other path in this module."""
    mined = trace_mining.mine_regression_record(cluster)
    case = trace_mining.to_wave2_case(
        mined, expected_behaviour, prompt)
    return trace_mining.write_case(case, directory)


