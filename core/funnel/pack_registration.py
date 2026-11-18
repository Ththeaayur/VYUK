"""Pack-registration failure record (wiring audit WIR-12).

Moved verbatim out of core/tools_handlers.py (Refactor Phase 1,
Step 1: pack extraction) so the extracted pack modules can record
failures without importing tools_handlers itself (which would be
circular: tools_handlers imports the pack modules).

core/funnel/tools_handlers.py re-exports PACK_REGISTRATION_FAILURES and
_record_pack_failure, so the doctor and the suites keep reading
this SAME list object through the old import path.
"""


#: Pack registration failures, in order (packaging batch, wiring
#: audit WIR-12). Every guarded _register_* except appends here
#: IN ADDITION to its loud print — a failed pack used to shrink
#: the catalog silently (the print scrolled past and nothing
#: recorded it). The doctor surfaces this list
#: (check_pack_registrations); suites read it directly. Entries
#: are dicts: {"pack", "module", "item", "error"}. "item" is the
#: module name except for single-tool registrations inside a
#: pack method (e.g. click_by_mark inside _register_uia_tools).
#: The list is append-only per process and is never cleared by
#: registration itself, so a later reader sees every failure.
PACK_REGISTRATION_FAILURES: list[dict] = []


def _record_pack_failure(pack: str, module: str, error: Exception,
                         *, item: str | None = None) -> None:
    """Record one guarded pack-registration failure (WIR-12).

    Never raises: recording must not turn a degraded catalog
    into a crashed registration. Callers keep their loud
    print; this only adds the durable record the doctor reads.
    """
    try:
        PACK_REGISTRATION_FAILURES.append({
            "pack": pack,
            "module": module,
            "item": item if item is not None else module,
            "error": f"{type(error).__name__}: {error}",
        })
    except Exception:  # noqa: BLE001 — recording never fails registration
        pass
