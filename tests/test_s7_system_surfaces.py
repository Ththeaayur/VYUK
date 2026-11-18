"""Tests for §7 item 7 — core.desktop.system_surfaces (+ shell half).

Live tray / taskbar / Start / notification behaviour on real
Windows stays field work: this lab has no Windows UI, and no
test here claims it. Every test drives the backend protocol
with a fake backend, or the honestly-unavailable default
backend.
"""

import unittest

from core.desktop import system_surfaces as s  # INTENTIONAL (Phase 3 S7a): system_surfaces moved to core/desktop/
from core.desktop import system_surfaces_shell as sh  # INTENTIONAL (Phase 3 S7a): system_surfaces_shell moved to core/desktop/
from core.desktop import target_handles as th  # INTENTIONAL (Phase 3 S7a): target_handles moved to core/desktop/


def _node(key, name, **over):
    node = {"key": key, "name": name, "control_type": "MenuItem",
            "automation_id": None, "enabled": True, "checked": None,
            "has_children": False}
    node.update(over)
    return node


class FakeBackend:
    """Mock shell surfaces. Mutable so tests can simulate change."""

    def __init__(self):
        self.calls = []
        self.tray = [
            {"icon_id": "t1", "app": "ChatApp", "title": "Chat",
             "has_menu": True},
            {"icon_id": "t2", "app": "ChatApp", "title": "Chat Alerts",
             "has_menu": True},
            {"icon_id": "t3", "app": "Mail", "title": "Mail",
             "has_menu": False},
        ]
        self.menus = {
            "t1": ([_node("m1", "Open"),
                    _node("m2", "Settings", has_children=True),
                    _node("m3", "Delete")], "stamp-t1"),
            "t2": ([_node("m9", "Open")], "stamp-t2"),
        }
        self.expansions = {
            "m2": ([_node("m4", "General"),
                    _node("m5", "Advanced", enabled=False)], "stamp-m2"),
        }
        self.taskbar = [
            {"button_id": "b1", "app": "Browser", "title": "Browser",
             "pinned": True, "running": True},
            {"button_id": "b2", "app": "Editor", "title": "Editor",
             "pinned": True, "running": False},
            {"button_id": "b3", "app": "Notes", "title": "Notes",
             "pinned": False, "running": True},
            {"button_id": "b4", "app": "Music", "title": "Music",
             "pinned": True, "running": True},
            {"button_id": "b5", "app": "Music", "title": "Music Player",
             "pinned": False, "running": False},
        ]
        self.start = [
            {"result_id": "r1", "name": "Calculator", "kind": "app"},
            {"result_id": "r2", "name": "Calendar", "kind": "app"},
            {"result_id": "r3", "name": "Settings", "kind": "page"},
            {"result_id": "r4", "name": "Settings", "kind": "app"},
        ]
        self.notification_rows = [
            {"notification_id": "n1", "app": "Mail", "title": "New mail",
             "body": "secret body text", "expired": False,
             "dismissed": False, "actions": ["open", "dismiss"]},
            {"notification_id": "n2", "app": "Clock", "title": "Old alert",
             "body": "", "expired": True, "dismissed": False,
             "actions": ["open"]},
            {"notification_id": "n3", "app": "Chat", "title": "Gone",
             "body": "", "expired": False, "dismissed": True,
             "actions": ["open"]},
        ]
        self.settings = [
            {"page_id": "p1", "name": "Display"},
            {"page_id": "p2", "name": "Sound"},
            {"page_id": "p3", "name": "Network"},
            {"page_id": "p4", "name": "Network"},
        ]

    # Tray.
    def tray_icons(self):
        return list(self.tray), ""

    def tray_menu(self, icon_id):
        roots, stamp = self.menus.get(icon_id, ([], None))
        return list(roots), stamp, ""

    def expand_tray(self, node):
        children, stamp = self.expansions.get(node.get("key"),
                                              ([], None))
        return list(children), stamp, ""

    def invoke_tray(self, node):
        self.calls.append(("invoke_tray", node.get("key")))
        return True, True, ""

    # Taskbar.
    def taskbar_buttons(self):
        return list(self.taskbar), ""

    def activate_taskbar(self, button_id):
        self.calls.append(("activate", button_id))
        return True, False, ""

    def launch_taskbar(self, button_id):
        self.calls.append(("launch_tb", button_id))
        return True, True, ""

    def pin_taskbar(self, button_id):
        self.calls.append(("pin", button_id))
        return True, False, ""

    # Start.
    def start_search(self, query):
        query = query.lower()
        found = [entry for entry in self.start
                 if query in entry["name"].lower()
                 or entry["result_id"] == query]
        return found, ""

    def launch_start(self, target):
        self.calls.append(("launch_start", target))
        return True, False, ""

    # Notifications.
    def notifications(self):
        return list(self.notification_rows), ""

    def act_notification(self, notification_id, action):
        self.calls.append(("act", notification_id, action))
        return True, True, ""

    # Settings.
    def settings_pages(self):
        return list(self.settings), ""

    def open_settings(self, page_id):
        self.calls.append(("open_settings", page_id))
        return True, False, ""


class WallTests(unittest.TestCase):
    def test_wall_names_refuse(self):
        for name in ("lock screen", "UAC", "password field",
                     "CAPTCHA", "secure desktop", "privilege-escalation"):
            result = s.route_surface(name)
            self.assertFalse(result["ok"])
            self.assertEqual(result["refusal"]["code"], "wall-refused")

    def test_known_and_unknown_surfaces(self):
        self.assertEqual(s.route_surface("tray")["surface"], "tray")
        self.assertEqual(s.route_surface("settings")["surface"],
                         "settings")
        result = s.route_surface("teleporter")
        self.assertEqual(result["refusal"]["code"], "unknown-surface")

    def test_default_backend_is_unavailable_not_empty(self):
        result = s.list_tray_icons()
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "surface-unavailable")
        self.assertIn("manually", result["refusal"]["reason"])


class TrayTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self.backend = FakeBackend()

    def test_list_tray_icons(self):
        result = s.list_tray_icons(backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][0]["icon_id"], "t1")

    def test_ambiguous_icons_list_and_ask(self):
        result = s.open_tray_menu("ChatApp", backend=self.backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "tray-ambiguous")
        self.assertIn("t1", result["refusal"]["reason"])
        self.assertIn("t2", result["refusal"]["reason"])
        self.assertEqual(self.backend.calls, [])

    def test_icon_not_found(self):
        result = s.open_tray_menu("Nope", backend=self.backend)
        self.assertEqual(result["refusal"]["code"], "icon-not-found")

    def test_unexposed_menu_fallback_with_manual_path(self):
        result = s.open_tray_menu("Mail", backend=self.backend)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"],
                         "surface-unexposed")
        self.assertIn("manually", result["refusal"]["reason"])

    def test_open_tray_menu_lists_roots(self):
        result = s.open_tray_menu("t1", backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["items"]],
                         ["Open", "Settings", "Delete"])
        self.assertTrue(result["target_handle_id"])

    def test_invoke_tray_leaf_success_verified(self):
        result = s.invoke_tray_menu_path("t1", "Open",
                                         backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "verified")
        self.assertIn(("invoke_tray", "m1"), self.backend.calls)

    def test_invoke_tray_submenu_walk(self):
        result = s.invoke_tray_menu_path(
            "t1", ["Settings", "General"], backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["target"], "Settings → General")

    def test_disabled_tray_item_refuses(self):
        result = s.invoke_tray_menu_path(
            "t1", ["Settings", "Advanced"], backend=self.backend)
        self.assertEqual(result["refusal"]["code"], "item-disabled")
        self.assertNotIn(("invoke_tray", "m5"), self.backend.calls)

    def test_ambiguous_tray_segment_lists(self):
        self.backend.menus["t1"] = (
            [_node("m1", "Open"), _node("m6", "Open")], "stamp-x")
        result = s.invoke_tray_menu_path("t1", "Open",
                                         backend=self.backend)
        self.assertEqual(result["refusal"]["code"], "item-ambiguous")

    def test_destructive_tray_leaf_needs_confirmation(self):
        result = s.invoke_tray_menu_path("t1", "Delete",
                                         backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "needs-confirmation")
        self.assertEqual(self.backend.calls, [])
        confirmed = s.invoke_tray_menu_path(
            "t1", "Delete", backend=self.backend, confirmed=True)
        self.assertTrue(confirmed["ok"])

    def test_rebuilt_tray_menu_refuses(self):
        opened = s.open_tray_menu("t1", backend=self.backend)
        result = s.invoke_tray_menu_path(
            "t1", "Open", backend=self.backend,
            expected_tree_stamp="different-stamp")
        self.assertEqual(result["refusal"]["code"], "menu-rebuilt")
        self.assertTrue(opened["ok"])

    def test_cross_target_handle_refuses(self):
        opened = s.open_tray_menu("t1", backend=self.backend)
        handle_id = opened["target_handle_id"]
        result = s.open_tray_menu("t2", backend=self.backend,
                                  target_handle_id=handle_id)
        self.assertFalse(result["ok"])
        # The t1 handle cannot re-prove against the t2 icon.
        self.assertIsNotNone(result["refusal"])


class TaskbarTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self.backend = FakeBackend()

    def test_switch_running_button(self):
        result = s.switch_taskbar("Browser", backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"],
                         "invoked-without-verification")
        self.assertIn(("activate", "b1"), self.backend.calls)

    def test_switch_not_running_refuses(self):
        result = s.switch_taskbar("Editor", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "button-not-running")

    def test_launch_requires_pinned(self):
        result = s.launch_taskbar("Notes", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "button-not-pinned")
        launched = s.launch_taskbar("Editor", backend=self.backend)
        self.assertTrue(launched["ok"])

    def test_pin_confirm_gate(self):
        result = s.pin_taskbar("Notes", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "needs-confirmation")
        self.assertEqual(self.backend.calls, [])
        pinned = s.pin_taskbar("Notes", backend=self.backend,
                               confirmed=True)
        self.assertTrue(pinned["ok"])
        self.assertIn(("pin", "b3"), self.backend.calls)

    def test_pin_already_pinned_refuses(self):
        result = s.pin_taskbar("Browser", backend=self.backend,
                               confirmed=True)
        self.assertEqual(result["refusal"]["code"], "already-pinned")

    def test_taskbar_ambiguity_lists(self):
        result = s.switch_taskbar("Music", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "target-ambiguous")
        self.assertIn("b4", result["refusal"]["reason"])


class StartTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self.backend = FakeBackend()

    def test_search_and_launch_exact(self):
        found = sh.start_search("calc", backend=self.backend)
        self.assertTrue(found["ok"])
        self.assertEqual([row["name"] for row in found["results"]],
                         ["Calculator"])
        launched = sh.launch_start("Calculator", backend=self.backend)
        self.assertTrue(launched["ok"])
        self.assertIn(("launch_start", "r1"), self.backend.calls)

    def test_launch_ambiguous_lists(self):
        result = sh.launch_start("Settings", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "target-ambiguous")
        self.assertEqual(self.backend.calls, [])

    def test_launch_not_found(self):
        result = sh.launch_start("Nope", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "result-not-found")

    def test_search_empty_query_refuses(self):
        result = sh.start_search("  ", backend=self.backend)
        self.assertEqual(result["refusal"]["code"], "empty-query")


class NotificationTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self.backend = FakeBackend()

    def test_titles_only_by_default(self):
        result = sh.list_notifications(backend=self.backend)
        self.assertTrue(result["ok"])
        for row in result["items"]:
            self.assertNotIn("body", row)
            self.assertTrue(row["body_withheld"])

    def test_body_only_for_approved_id(self):
        result = sh.list_notifications(
            backend=self.backend, include_body=True,
            body_approved_ids=("n1",))
        rows = {row["notification_id"]: row for row in result["items"]}
        self.assertEqual(rows["n1"]["body"], "secret body text")
        self.assertFalse(rows["n1"]["body_withheld"])
        self.assertNotIn("body", rows["n2"])
        # include_body without approval still withholds.
        plain = sh.list_notifications(backend=self.backend,
                                      include_body=True)
        self.assertNotIn("body", plain["items"][0])

    def test_act_by_id_and_text(self):
        result = sh.act_notification("n1", "New mail", "open",
                                     backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "verified")
        self.assertIn(("act", "n1", "open"), self.backend.calls)

    def test_position_only_is_not_an_id(self):
        result = sh.act_notification("0", "New mail", "open",
                                     backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "notification-not-found")

    def test_expired_notification_refuses_naming_it(self):
        result = sh.act_notification("n2", "Old alert", "open",
                                     backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "notification-expired")
        self.assertIn("Old alert", result["refusal"]["reason"])
        self.assertEqual(self.backend.calls, [])

    def test_dismissed_notification_refuses(self):
        result = sh.act_notification("n3", "Gone", "open",
                                     backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "notification-expired")

    def test_text_mismatch_refuses(self):
        result = sh.act_notification("n1", "Wrong title", "open",
                                     backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "notification-text-mismatch")
        self.assertIn("New mail", result["refusal"]["reason"])

    def test_dismiss_confirm_gate(self):
        result = sh.dismiss_notification("n1", "New mail",
                                         backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "needs-confirmation")
        self.assertEqual(self.backend.calls, [])
        done = sh.dismiss_notification("n1", "New mail",
                                       backend=self.backend,
                                       confirmed=True)
        self.assertTrue(done["ok"])
        self.assertIn(("act", "n1", "dismiss"), self.backend.calls)

    def test_unexposed_action_refuses(self):
        result = sh.act_notification("n1", "New mail", "reply",
                                     backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "action-unexposed")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        th.reset_store()
        self.backend = FakeBackend()

    def test_open_settings_page(self):
        result = sh.open_settings("Display", backend=self.backend)
        self.assertTrue(result["ok"])
        self.assertIn(("open_settings", "p1"), self.backend.calls)

    def test_settings_ambiguity_lists(self):
        result = sh.open_settings("Network", backend=self.backend)
        self.assertEqual(result["refusal"]["code"],
                         "target-ambiguous")

    def test_settings_not_found(self):
        result = sh.open_settings("Nope", backend=self.backend)
        self.assertEqual(result["refusal"]["code"], "page-not-found")


if __name__ == "__main__":
    unittest.main()
