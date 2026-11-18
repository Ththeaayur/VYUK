"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from .. import injection  # Phase 3 F1: file now in core/funnel/; flat sibling import climbs one more level
from typing import Any



# ------------------------------------------------------------------
# Untrusted-producer wrap seam (v5.45, H18)
#
# Browser pages, OCR-screen text, Gmail and calendar content were
# envelope-wrapped + taint-screened by their producers — but file
# contents, PDF text, OCR-of-file, command output, UIA trees,
# window titles, filenames and RSS/web fetches reached the model
# RAW: the same injection in a .txt file or a terminal's output
# arrived indistinguishable from data the user asked for. The
# producers below are therefore wrapped at ONE seam — the
# execute() handler return — via injection.wrap_untrusted, which
# also notes them for the taint screen and the provenance list.
#
# v5.46 (programme item 1, A1): the producer list was extended to
# every tool returning external text, and the two bypasses in the
# old seam were deleted. There is NO skip list any more: a result
# that starts with an error/refusal-looking prefix, or that
# contains "[Untrusted" near its start, used to return RAW — and
# skipping the wrap also skipped the taint screen, because noting
# happens inside wrap_untrusted. An attacker controls the start
# of file/page/command content, so both exemptions were bypasses.
# Now EVERY string result from a listed producer wraps, error
# lines included (wrapping one is harmless). Only non-string and
# empty-string results pass through. Content-embedded envelope
# markers are escaped inside wrap_untrusted itself, so an
# already-enveloped result that wraps again carries exactly one
# unescaped envelope — the outer one.
#
# Producer audit (v5.46, A1) — tools NOT added, with reasons:
#   * web_search, browser_read, ocr_screen, screen_look and the
#     Google tools (get_unread_email, search_email,
#     get_todays_schedule, get_upcoming_events) wrap their
#     external content INTERNALLY at their producers, as do
#     deadline_radar / inbox_zero_assist / unsubscribe_finder /
#     meeting_prep_card for the mail/calendar portions of their
#     results — adding them here would double-envelope.
#   * get_stock_quote / get_crypto_price are plugins (their gate
#     class is a plugin matter, H19b); their results are locally
#     templated one-line price summaries, not raw external text.
#   * Verdict-style network/metadata tools whose bodies are
#     locally templated around numeric / status / IP / hash
#     fields (website_status, link_health_check, ssl_cert_info,
#     dns_lookup, check_port, ping_host, trace_route,
#     speed_test, public_ip, network_quality, file_hash,
#     image_info, sniff_file, sniff_text, recall_memories) were
#     left out: they quote no attacker-controlled prose beyond
#     the host/path the model itself supplied.
_UNTRUSTED_PRODUCERS: dict[str, str] = {
    "read_file": "file",
    "read_pdf_text": "file",
    "ocr_file": "image file",
    "run_command": "command output",
    "uia_read_tree": "window controls",
    "get_window_controls": "window controls",
    "get_active_window": "window title",
    "find_files": "filenames",
    # R9-4 mutagen port: the sync report names discovered filenames
    # (the other side's files, conflict lists), exactly like find_files.
    # (sync_folder returns discovered filenames too and is NOT listed —
    # a pre-existing gap, not this batch's to fix.)
    "sync_folders_two_way": "filenames",
    # R9-8 elodie port: the organise report and the preview plan name
    # discovered filenames, exactly like find_files.
    "organise_photos": "filenames",
    "preview_photo_organisation": "filenames",
    "rss_digest": "RSS feed",
    "read_page_aloud": "web page",
    "page_word_search": "web page",
    # R10-5 python-readability port: the article text is the
    # page's own words, quoted back — untrusted web content.
    "extract_article": "web page",
    # v5.46 (A1): file-content readers / content-derived results.
    "search_file_contents": "file",
    # R9-3 whoosh port: the indexed search returns raw file
    # contents + snippets, exactly like search_file_contents.
    "search_indexed_files": "file",
    "tail_file": "file",
    "csv_preview": "file",
    "csv_query": "file",
    "csv_column_stats": "file",
    "hosts_view": "file",
    "text_diff": "file",
    "compare_files": "file",
    "json_query": "file",
    "json_diff": "file",
    "extract_from_text": "file",
    "pdf_info": "file",
    # v5.46 (A1): filename listings (crafted names are content).
    "folder_outline": "filenames",
    "recent_files": "filenames",
    "largest_files": "filenames",
    "find_duplicates": "filenames",
    "duplicate_photo_finder": "filenames",
    "file_age_report": "filenames",
    "folder_diff": "filenames",
    "folder_size": "filenames",
    "folder_size_map": "filenames",
    "clipboard_files": "filenames",
    # v5.46 (A1): web/RSS readers returning raw external text and
    # not wrapping internally (web_search / browser_read do).
    # R10-1 chrome-devtools-mcp port: the console and network
    # tools return raw page-chosen text (console output, request
    # URLs) exactly like the web readers above. The performance
    # tool is NOT listed: its result is locally templated
    # numbers, like speed_test / public_ip.
    "browser_console": "web page",
    "browser_network": "web page",
    # R10-3 browser-history port: history rows and bookmark rows
    # are stored page-chosen text (titles, URLs) — same untrusted
    # producer as the console / network tools above.
    "browser_history": "web page",
    "browser_bookmarks": "web page",
    "http_headers": "web page",
    "ip_geolocate": "web page",
    "wayback_lookup": "web page",
    "domain_lookup": "web page",
    "link_unshorten": "web page",
    "sniff_link": "web page",
    "get_weather": "web page",
    # v5.46 (A1): other external-text surfaces.
    "qr_decode_image": "image file",
    "read_clipboard": "clipboard",
    "download_email_attachment": "email attachment",
}


def _wrap_producer_result(name: str, result: Any) -> Any:
    """The H18 seam, hardened in v5.46 (programme item 1, A1):
    envelope-wrap + taint-note the result of an untrusted
    producer at the execute() return. Every string result from a
    listed producer wraps — there is deliberately no skip-prefix
    list and no "[Untrusted"-in-the-result shortcut (both were
    attacker-controlled bypasses that also skipped the taint
    screen). Non-string and empty-string results pass through,
    and a wrapping failure must never break a tool result."""
    source = _UNTRUSTED_PRODUCERS.get(name)
    if source is None or not isinstance(result, str):
        return result
    if not result.strip():
        return result
    try:
        return injection.wrap_untrusted(source, result)
    except Exception:  # noqa: BLE001 — wrapping must never break
        return result  # a tool result


def wrap_mcp_content(server: str, text: str) -> str:
    """The A3 MCP output seam (master audit P0-6/P1-13): the ONE
    function through which ALL model-facing MCP-sourced text
    passes — tool descriptions, schemas, discovery/find text,
    describe text, invocation results, and server errors.

    It envelope-wraps via injection.wrap_untrusted with source
    f"mcp:{server}" (the same source the funnel's pre-A3
    success wrap already used, so provenance stays per-server)
    and screens the text: an adversarial instruction in it
    hard-taints the session via the screen, exactly as a native
    untrusted producer's text would. Benign MCP text is
    envelope-marked + provenance-recorded (the tree's standing
    treatment for MCP results); it is never returned as plain
    trusted prose. Non-string and blank inputs pass through,
    and a wrapping failure returns the input — wrapping must
    never break a tool result.

    Routed through here: core.toolbox._mcp_declaration (the
    description field), _find/_describe rendered declaration
    text, _call MCP failure/schema text, and
    core/funnel/tools_registry_exec_dispatch._exec_dispatch_mcp
    success text (GAPS-12: the old _execute_mcp site is gone).
    No other MCP-to-model path may return server text unwrapped.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    try:
        return injection.wrap_untrusted(f"mcp:{server}", text)
    except Exception:  # noqa: BLE001 — wrapping must never break
        return text  # a tool result
