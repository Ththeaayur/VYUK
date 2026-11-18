"""System-level surfaces, shell half — §7 item 7 (parent draft).

Start menu search / launch, notification centre, and settings
pages, on the plumbing of ``core.desktop.system_surfaces`` (result
shapes, backend readers, item-1 proof, wall guard). See that
module's docstring for the item-7 scope, the D4 titles-only
rule (still the owner's open question), and the field-owed
live backend. Importing this module needs only the standard
library (plus the sibling plumbing module).
"""

from .system_surfaces import (
    _ambiguous_result,
    _backend,
    _candidate,
    _clean,
    _clean_name,
    _fail,
    _invoke,
    _match_exact,
    _ok,
    _prove_target,
    _read,
    _unavailable,
)

__all__ = [
    "start_search",
    "launch_start",
    "list_notifications",
    "act_notification",
    "dismiss_notification",
    "list_settings_pages",
    "open_settings",
]

# ---------------------------------------------------------------------------
# Start menu
# ---------------------------------------------------------------------------


def _project_start(entry):
    return {"result_id": _clean(entry.get("result_id")),
            "name": _clean(entry.get("name")),
            "kind": _clean(entry.get("kind"))}


def start_search(query, *, backend=None):
    """Search Start (exposed search results, exact-match launch)."""
    backend = _backend(backend)
    if not _clean(query):
        return _fail("empty-query",
                     "no Start search text was named — name the "
                     "app or setting to search for.",
                     "start", "searched")
    results, note = _read(backend, "start_search", _clean(query))
    if results is None:
        return _unavailable("start", "searched", note)
    return _ok("start", "searched",
               query=_clean(query),
               results=[_project_start(entry) for entry in results])


def launch_start(target, *, backend=None, target_handle_id=None):
    """Launch one Start result by id or exact name.

    The search is re-run FRESH inside the call (by the target
    text) and the target must match exactly in those fresh
    results — ambiguous names list and ask, stale results
    refuse. The handle is then proven against the fresh entry
    before the backend launches by result id.
    """
    backend = _backend(backend)
    target = _clean(target)
    if not target:
        return _fail("empty-target",
                     "no Start result was named — name the app "
                     "or setting exactly.",
                     "start", "launched")
    results, note = _read(backend, "start_search", target)
    if results is None:
        return _unavailable("start", "launched", note)
    entry, ambiguous = _match_exact(results, target, "result_id",
                                    ("name",))
    if ambiguous is not None:
        return _ambiguous_result("start", "launched", ambiguous,
                                 "Start results", "result_id",
                                 ("name",))
    if entry is None:
        return _fail("result-not-found",
                     "no Start result matches '%s' — search "
                     "again and name the exact result." % target,
                     "start", "launched")
    handle_id, refusal = _prove_target(
        "start", _clean(entry.get("result_id")),
        _clean(entry.get("name")),
        _candidate("start", _clean(entry.get("result_id")),
                   _clean(entry.get("name"))),
        handle_id=target_handle_id)
    if refusal is not None:
        return _fail(refusal.get("code"), refusal.get("reason"),
                     "start", "launched")
    invoked, verified, note = _invoke(backend, "launch_start",
                                      _clean(entry.get("result_id")))
    if not invoked:
        return _fail("launch-failed",
                     "Start did not launch '%s'%s — search again "
                     "and name the exact result."
                     % (_clean(entry.get("name")),
                        (" (%s)" % note) if note else ""),
                     "start", "launched")
    return _ok("start", "launched",
               target=_clean(entry.get("name")),
               handle_id=handle_id,
               verification=("verified" if verified else
                             "invoked-without-verification"))


# ---------------------------------------------------------------------------
# Notifications (D4: titles-only by default)
# ---------------------------------------------------------------------------


def _project_notification(entry, *, include_body, approved):
    notification_id = _clean(entry.get("notification_id"))
    body_approved = include_body and notification_id in approved
    row = {"notification_id": notification_id,
           "app": _clean(entry.get("app")),
           "title": _clean(entry.get("title")),
           "expired": bool(entry.get("expired")),
           "dismissed": bool(entry.get("dismissed")),
           "actions": [str(action) for action in
                       (entry.get("actions") or [])]}
    if body_approved:
        row["body"] = _clean(entry.get("body"))
        row["body_withheld"] = False
    else:
        # D4 draft proposal: bodies only on explicit
        # per-notification approval. Withheld is visible,
        # never silently empty.
        row["body_withheld"] = True
    return row


def list_notifications(*, backend=None, include_body=False,
                       body_approved_ids=()):
    """List notifications: titles (+app) by default.

    A body appears only for an id in ``body_approved_ids``
    AND ``include_body=True`` — per-notification approval,
    never a blanket read (D4, still the owner's question).
    """
    backend = _backend(backend)
    approved = set(_clean(item) for item in (body_approved_ids or ()))
    items, note = _read(backend, "notifications")
    if items is None:
        return _unavailable("notification", "listed", note)
    return _ok("notification", "listed",
               items=[_project_notification(
                   entry, include_body=bool(include_body),
                   approved=approved) for entry in items])


def act_notification(notification_id, expected_title, action, *,
                     backend=None, confirmed=False,
                     target_handle_id=None):
    """Act on one notification, named by id + confirmed text.

    Resolution (fresh listing inside the call): the id must
    still exist; an expired or dismissed notification refuses
    naming it; a title that no longer matches the confirmed
    text refuses naming the current title. Dismissal is
    effectful and confirm-gated. No position-only addressing
    exists: an index is not an id, so it matches nothing and
    refuses as not-found.
    """
    backend = _backend(backend)
    notification_id = _clean(notification_id)
    expected = _clean_name(expected_title)
    if not notification_id:
        return _fail("notification-not-found",
                     "no notification id was named — list the "
                     "notifications and name one by id.",
                     "notification", "acted")
    items, note = _read(backend, "notifications")
    if items is None:
        return _unavailable("notification", "acted", note)
    entry = None
    for item in items:
        if isinstance(item, dict) \
                and _clean(item.get("notification_id")) == notification_id:
            entry = item
            break
    if entry is None:
        return _fail("notification-not-found",
                     "no live notification has id '%s' — it may "
                     "have been dismissed already; list again "
                     "and name a live one." % notification_id,
                     "notification", "acted")
    if entry.get("expired") or entry.get("dismissed"):
        state = "expired" if entry.get("expired") else "dismissed"
        return _fail("notification-expired",
                     "notification '%s' (%s) is %s — it can no "
                     "longer be acted on; list again for the "
                     "live notifications."
                     % (notification_id,
                        _clean(entry.get("title")), state),
                     "notification", "acted")
    current_title = _clean_name(entry.get("title"))
    if expected and current_title != expected:
        return _fail("notification-text-mismatch",
                     "notification '%s' is now titled '%s', not "
                     "'%s' — nothing is acted on under a stale "
                     "title; confirm the current title and retry."
                     % (notification_id, current_title, expected),
                     "notification", "acted")
    action = _clean(action)
    if action == "dismiss" and not confirmed:
        return _fail("needs-confirmation",
                     "dismissing notification '%s' changes state, "
                     "so it needs your confirmation first — "
                     "confirm and retry." % notification_id,
                     "notification", "acted",
                     instruction="Confirm this dismissal "
                     "explicitly, then retry.")
    if action and entry.get("actions") \
            and action not in [str(item) for item in entry["actions"]]:
        # An action the notification does not expose is not
        # silently mapped to another one.
        return _fail("action-unexposed",
                     "notification '%s' does not expose "
                     "action '%s' (it exposes: %s)."
                     % (notification_id, action,
                        ", ".join(str(item) for item in
                                  entry["actions"])),
                     "notification", "acted")
    handle_id, refusal = _prove_target(
        "notification", notification_id, current_title,
        _candidate("notification", notification_id, current_title),
        handle_id=target_handle_id)
    if refusal is not None:
        return _fail(refusal.get("code"), refusal.get("reason"),
                     "notification", "acted")
    invoked, verified, note = _invoke(backend, "act_notification",
                                      notification_id, action)
    if not invoked:
        return _fail("invoke-failed",
                     "notification '%s' did not accept action "
                     "'%s'%s." % (notification_id, action,
                                  (" (%s)" % note) if note else ""),
                     "notification", "acted")
    return _ok("notification", "acted",
               target="%s (%s)" % (current_title, notification_id),
               handle_id=handle_id,
               action_detail=action,
               verification=("verified" if verified else
                             "action-without-verification"))


def dismiss_notification(notification_id, expected_title, *,
                         backend=None, confirmed=False,
                         target_handle_id=None):
    """Dismiss one notification (confirm-gated, id + text)."""
    return act_notification(notification_id, expected_title,
                            "dismiss", backend=backend,
                            confirmed=confirmed,
                            target_handle_id=target_handle_id)


# ---------------------------------------------------------------------------
# Settings pages (reached, not changed, through exposed controls)
# ---------------------------------------------------------------------------


def _project_settings(entry):
    return {"page_id": _clean(entry.get("page_id")),
            "name": _clean(entry.get("name"))}


def list_settings_pages(*, backend=None):
    backend = _backend(backend)
    items, note = _read(backend, "settings_pages")
    if items is None:
        return _unavailable("settings", "listed", note)
    return _ok("settings", "listed",
               items=[_project_settings(entry) for entry in items])


def open_settings(target, *, backend=None, target_handle_id=None):
    """Open one settings page by id or exact name.

    Opening a page changes no setting — it only reaches the
    page through its exposed control. Changing a setting value
    is deliberately NOT built here: it is an effectful system
    action whose confirm path belongs to the settings-value
    consumer, not to page navigation.
    """
    backend = _backend(backend)
    items, note = _read(backend, "settings_pages")
    if items is None:
        return _unavailable("settings", "opened", note)
    entry, ambiguous = _match_exact(items, target, "page_id", ("name",))
    if ambiguous is not None:
        return _ambiguous_result("settings", "opened", ambiguous,
                                 "settings pages", "page_id",
                                 ("name",))
    if entry is None:
        return _fail("page-not-found",
                     "no settings page matches '%s' — list the "
                     "settings pages and name one exactly."
                     % _clean(target),
                     "settings", "opened")
    handle_id, refusal = _prove_target(
        "settings", _clean(entry.get("page_id")),
        _clean(entry.get("name")),
        _candidate("settings", _clean(entry.get("page_id")),
                   _clean(entry.get("name"))),
        handle_id=target_handle_id)
    if refusal is not None:
        return _fail(refusal.get("code"), refusal.get("reason"),
                     "settings", "opened")
    invoked, verified, note = _invoke(backend, "open_settings",
                                      _clean(entry.get("page_id")))
    if not invoked:
        return _fail("invoke-failed",
                     "settings page '%s' did not open%s."
                     % (_clean(entry.get("name")),
                        (" (%s)" % note) if note else ""),
                     "settings", "opened")
    return _ok("settings", "opened",
               target=_clean(entry.get("name")),
               handle_id=handle_id,
               verification=("verified" if verified else
                             "invoked-without-verification"))


