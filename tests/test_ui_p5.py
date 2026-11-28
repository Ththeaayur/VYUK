"""UI Phase 5 — suite (2026-11-27).

Pins for the three first-class workspaces: Notes (over the
verified `notes` tool + the new key-based GUI helpers), Finance
(local money stores + calculator launchers only; the unsupported
state first; no bank surface of any kind), Devices / Smart Home
(the HA join + config + the not-configured-first page), the
sidebar/workspaces wiring (15 buttons, stack 12/13/14), the
palette rows, and the Home hub Settings card.

Qt only for the GUI classes: without PySide6 every GUI test skips
(the test_chat_ui / test_ui_p4 shape).
"""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The `voice_assistant` package is synthesised, not on disk (the
# test_ui_p1 shape): create the alias packages here so the pure
# core tests can import the real modules. WindowBase.setUp()
# re-creates these packages and stubs four modules; the GUI tests
# below work around the stubs (facade patching) where they need
# real data.
import types as _types

for _name, _path in (
        ("voice_assistant", str(ROOT)),
        ("voice_assistant.core", str(ROOT / "core")),
        ("voice_assistant.gui", str(ROOT / "gui"))):
    if _name not in sys.modules:
        _pkg = _types.ModuleType(_name)
        _pkg.__path__ = [_path]
        sys.modules[_name] = _pkg

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]


def _qt_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _show(window, index):
    window._show_page(index)
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance()
    if _app is not None:
        _app.processEvents()


class _TempStoreMixin:
    """Pure tests run against a temp MemoryStore, never live data.
    The loaders resolve _store() fresh each call, so replacing
    `voice_assistant.core.memory.memory.memory_store` is the seam
    (skills_money_helpers._store, via the voice_assistant alias)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import voice_assistant.core.memory.memory as _mem
        from voice_assistant.core.memory.memory import MemoryStore
        self._orig_store = _mem.memory_store
        self._store = MemoryStore(
            os.path.join(self._tmp.name, "assistant.db"))
        _mem.memory_store = self._store

    def tearDown(self):
        import voice_assistant.core.memory.memory as _mem
        _mem.memory_store = self._orig_store
        self._tmp.cleanup()


# ---------------------------------------------------------- pure: Notes
class TestNotesHelpers(_TempStoreMixin, unittest.TestCase):
    """The key-based GUI helpers (D-P5-03/04/08, S-P5-02) over a
    temp store; the funnel itself is unchanged."""

    def test_add_then_load_defaults(self):
        from voice_assistant.core import skills_notes as n
        answer = n.notes(action="add", text="Gate note one")
        self.assertIn("Noted:", answer)
        entries, broken = n._load_notes()
        self.assertEqual(broken, 0)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["text"], "Gate note one")
        self.assertFalse(entry["pinned"])
        self.assertEqual(entry["edited"], "")

    def test_old_shape_loads_with_defaults_not_broken(self):
        from voice_assistant.core import skills_notes as n
        self._store.save_fact("notes", "Old note", json.dumps({
            "title": "Old", "text": "old text", "added": "2026-01-01"}))
        entries, broken = n._load_notes()
        self.assertEqual(broken, 0)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["pinned"])
        self.assertEqual(entries[0]["edited"], "")

    def test_broken_json_counts_broken(self):
        from voice_assistant.core import skills_notes as n
        self._store.save_fact("notes", "Broken note", "not json {")
        entries, broken = n._load_notes()
        self.assertEqual(broken, 1)
        self.assertEqual(entries, [])

    def test_add_over_cap_refused(self):
        from voice_assistant.core import skills_notes as n
        answer = n.notes(action="add", text="x" * 2001)
        self.assertIn("2000", answer)
        entries, _b = n._load_notes()
        self.assertEqual(entries, [])

    def test_edit_preserves_key_and_pin_and_stamps_edited(self):
        from voice_assistant.core import skills_notes as n
        n.notes(action="add", text="First version")
        key = n._load_notes()[0][0]["key"]
        ok, _msg = n._set_note_pinned(key, True)
        self.assertTrue(ok)
        ok, msg = n._edit_note_by_key(key, "New title", "Second version")
        self.assertTrue(ok, msg)
        entries, _b = n._load_notes()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["key"], key)
        self.assertEqual(entry["text"], "Second version")
        self.assertEqual(entry["title"], "New title")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["edited"],
                         datetime.date.today().isoformat())

    def test_pin_never_drops_the_edited_stamp(self):
        from voice_assistant.core import skills_notes as n
        n.notes(action="add", text="First version")
        key = n._load_notes()[0][0]["key"]
        n._edit_note_by_key(key, "Title", "Second version")
        stamped = n._load_notes()[0][0]["edited"]
        self.assertTrue(stamped)
        ok, _msg = n._set_note_pinned(key, True)
        self.assertTrue(ok)
        ok, _msg = n._set_note_pinned(key, False)
        self.assertTrue(ok)
        entry = n._load_notes()[0][0]
        self.assertFalse(entry["pinned"])
        self.assertEqual(entry["edited"], stamped)

    def test_remove_by_key_proves_gone(self):
        from voice_assistant.core import skills_notes as n
        n.notes(action="add", text="To remove")
        key = n._load_notes()[0][0]["key"]
        ok, _msg = n._remove_note_by_key(key)
        self.assertTrue(ok)
        self.assertEqual(n._load_notes()[0], [])
        ok, msg = n._remove_note_by_key(key)
        self.assertFalse(ok)
        self.assertIn("already gone", msg)

    def test_edit_missing_key_is_already_gone(self):
        from voice_assistant.core import skills_notes as n
        ok, msg = n._edit_note_by_key("no-such-key", "T", "text")
        self.assertFalse(ok)
        self.assertIn("already gone", msg)


# ---------------------------------------------------------- pure: Finance
class TestMoneyLoaders(_TempStoreMixin, unittest.TestCase):
    """The local money stores: envelopes, subscriptions,
    portfolio, standing alerts. No bank surface exists to test."""

    def test_envelope_set_spend_report(self):
        from voice_assistant.core import skills_money_budget as m
        self.assertIn("Set", m.budget_envelope(
            action="set", name="Food", amount=50000))
        # The spend line carries plain numbers (the funnel does not
        # know the currency); the report formats with ₹.
        self.assertIn("Added 12,000.00", m.budget_envelope(
            action="spend", name="Food", amount=12000))
        entries, broken = m._load_envelopes()
        self.assertEqual(broken, 0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["spent"], 12000.0)
        report = m.budget_envelope(action="report")
        self.assertIn("Totals: budgets", report)
        # The funnel's prose carries plain numbers — ₹ formatting
        # lives in the GUI (_fmt), not the funnel.
        self.assertIn("50,000.00", report)

    def test_over_budget_detectable_from_the_loader(self):
        from voice_assistant.core import skills_money_budget as m
        m.budget_envelope(action="set", name="Food", amount=1000)
        m.budget_envelope(action="spend", name="Food", amount=1500)
        entry = m._load_envelopes()[0][0]
        self.assertGreater(entry["spent"], entry["budget"])

    def test_subscription_renewal_day_3(self):
        from voice_assistant.core import skills_money_budget as m
        m.subscription_audit(action="add", name="Music", monthly=199,
                             renewal_day=3)
        entries, _b = m._load_subscriptions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["renewal_day"], 3)
        self.assertEqual(
            m._next_renewal(3, datetime.date(2026, 11, 27)),
            datetime.date(2026, 12, 3))

    def test_portfolio_unvalued_without_a_recorded_price(self):
        from voice_assistant.core import skills_money_budget as m
        m.portfolio_tracker(action="add", symbol="INFY", quantity=10,
                            buy_price=1500, price=0)
        entries, broken = m._load_portfolio()
        self.assertEqual(broken, 0)
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]["price"])
        listing = m.portfolio_tracker(action="list")
        self.assertIn("no recorded price", listing)

    def test_envelope_broken_counts(self):
        from voice_assistant.core import skills_money_budget as m
        self._store.save_fact("envelopes", "Broken", "not json {")
        _entries, broken = m._load_envelopes()
        self.assertEqual(broken, 1)

    def test_over_budget_envelope_is_a_standing_alert(self):
        from voice_assistant.core import skills_money_budget as m
        from voice_assistant.core import money_alerts as a
        m.budget_envelope(action="set", name="Food", amount=1000)
        m.budget_envelope(action="spend", name="Food", amount=1500)
        lines = a.money_alert_lines(
            now=datetime.datetime(2026, 11, 27))
        self.assertTrue(any("Food" in line for line in lines))
        self.assertTrue(lines)


# ---------------------------------------------------------- pure: HA join
class TestHaJoin(unittest.TestCase):
    """join_snapshot (§4.3): join by entity/device id ONLY from
    linkage the responses carry; a missing join is never papered
    over."""

    AREAS = [{"name": "Living Room", "area_id": "living_room"},
             {"name": "Kitchen", "area_id": "kitchen"},
             {"name": "Empty Room", "area_id": "empty_room"}]
    DEVICES = [{"name": "Test bulb", "id": "dev1",
                "area_id": "living_room",
                "entities": ["light.living_room"]},
               {"name": "Twin lamp", "id": "dev3",
                "area_id": "kitchen",
                "entities": ["light.kitchen", "light.kitchen2"]},
               {"name": "Orphan", "id": "dev2"}]
    STATES = [
        {"entity_id": "light.living_room", "state": "on",
         "attributes": {"friendly_name": "Living Room light"},
         "last_updated": "2026-11-27T03:00:00+00:00",
         "device_id": "dev1"},
        {"entity_id": "switch.fan", "state": "off",
         "attributes": {"friendly_name": "Fan"}},
        {"entity_id": "light.kitchen", "state": "on",
         "attributes": {"friendly_name": "Kitchen main"}},
        {"entity_id": "light.kitchen2", "state": "off",
         "attributes": {"friendly_name": "Kitchen counter"}},
    ]

    def _joined(self):
        from voice_assistant.core import homeassistant as ha
        return ha.join_snapshot(self.AREAS, self.DEVICES, self.STATES)

    def test_rooms_devices_and_state_text(self):
        snap = self._joined()
        rooms = {r["area_id"]: r for r in snap["rooms"]}
        self.assertEqual(
            {r["name"] for r in snap["rooms"]},
            {"Living Room", "Kitchen", "Empty Room"})
        bulb = rooms["living_room"]["devices"][0]
        self.assertEqual(bulb["name"], "Test bulb")
        self.assertEqual(bulb["state_text"], "on")
        self.assertEqual(bulb["states"][0]["name"],
                         "Living Room light")
        self.assertEqual(bulb["states"][0]["last_seen"],
                         "2026-11-27T03:00:00+00:00")
        twin = rooms["kitchen"]["devices"][0]
        self.assertEqual(twin["state_text"], "Mixed")
        self.assertEqual(rooms["empty_room"]["devices"], [])

    def test_device_with_no_state_is_unknown(self):
        snap = self._joined()
        orphan = snap["unassigned_devices"][0]
        self.assertEqual(orphan["name"], "Orphan")
        self.assertEqual(orphan["state_text"], "Unknown")
        self.assertEqual(orphan["states"], [])

    def test_state_with_no_device_is_unassigned_as_is(self):
        snap = self._joined()
        self.assertEqual(len(snap["unassigned_states"]), 1)
        view = snap["unassigned_states"][0]
        self.assertEqual(view["entity_id"], "switch.fan")
        self.assertEqual(view["name"], "Fan")
        self.assertEqual(view["state"], "off")

    def test_state_naming_an_unreturned_device_is_unassigned(self):
        from voice_assistant.core import homeassistant as ha
        snap = ha.join_snapshot(
            [], [],
            [{"entity_id": "light.ghost", "state": "on",
              "device_id": "dev-ghost",
              "attributes": {"friendly_name": "Ghost"}}])
        self.assertEqual(len(snap["unassigned_states"]), 1)
        self.assertEqual(snap["unassigned_states"][0]["entity_id"],
                         "light.ghost")

    def test_never_raises_on_odd_shapes(self):
        from voice_assistant.core import homeassistant as ha
        snap = ha.join_snapshot(None, [{"id": "d"}], [None, "x", {}])
        self.assertEqual(snap["rooms"], [])
        self.assertEqual(len(snap["unassigned_devices"]), 1)
        self.assertEqual(snap["unassigned_devices"][0]["state_text"],
                         "Unknown")


# ---------------------------------------------------------- pure: HA errors
class TestHaErrorLines(unittest.TestCase):
    def test_each_kind_names_what_to_do_next(self):
        from voice_assistant.core import home_assistant_config as hac

        class _Err:
            def __init__(self, kind):
                self.kind = kind

        for kind, fragment in (
                ("config", "Check the address and token"),
                ("refused", "Not allowed for this device"),
                ("auth", "rejected the token"),
                ("not_found", "no such device"),
                ("timeout", "didn't answer"),
                ("transport", "Couldn't reach the hub"),
                ("server", "answered with an error")):
            self.assertIn(fragment, hac.ha_error_line(_Err(kind)), kind)
        self.assertIn("Couldn't reach the hub",
                      hac.ha_error_line(_Err("weird-kind")))
        self.assertIn("Couldn't reach the hub",
                      hac.ha_error_line("transport"))


# ---------------------------------------------------------- pure: HA config
class TestHaConfig(unittest.TestCase):
    """The stored hub config: host + allowlist in .env (faked),
    token in the secrets store (temp key dir)."""

    def setUp(self):
        from voice_assistant.core import home_assistant_config as hac
        from voice_assistant.core import secrets_store as ss
        self._hac = hac
        self._ss = ss
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_key_dir = ss._key_dir()
        ss.set_key_dir(self._tmp.name)
        self._saved_env = {}
        self._orig_env = {}
        for key in ("HOME_ASSISTANT_HOST",
                    "HOME_ASSISTANT_ALLOWLIST"):
            self._orig_env[key] = os.environ.get(key)

        def _fake_save(key, value):
            self._saved_env[key] = value
            os.environ[key] = value

        self._orig_save = hac.save_env_setting
        hac.save_env_setting = _fake_save

    def tearDown(self):
        self._hac.save_env_setting = self._orig_save
        self._ss.set_key_dir(str(self._orig_key_dir))
        for key, value in self._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_host_and_allowlist_round_trip(self):
        hac = self._hac
        self.assertTrue(hac.save_ha_host("192.168.1.10"))
        self.assertEqual(hac.load_ha_host(), "192.168.1.10")
        self.assertTrue(hac.save_ha_allowlist(
            {"light.kitchen": "Kitchen"}, {"light.kitchen"}))
        allow, exposed = hac.load_ha_allowlist()
        self.assertEqual(allow, {"light.kitchen": "Kitchen"})
        self.assertEqual(exposed, {"light.kitchen"})

    def test_token_round_trip_temp_key_dir(self):
        hac = self._hac
        ok, _msg = hac.save_ha_token("test-token-123")
        self.assertTrue(ok)
        token, status = hac.load_ha_token()
        self.assertEqual(token, "test-token-123")
        self.assertEqual(status, "ok")
        hac.clear_ha_token_file()
        token, status = hac.load_ha_token()
        self.assertIsNone(token)
        self.assertEqual(status, "missing")

    def test_token_refused_when_no_protecting_backend(self):
        hac = self._hac
        orig = self._ss.active_backend_name
        self._ss.active_backend_name = lambda: "none-plaintext"
        try:
            ok, msg = hac.save_ha_token("test-token-123")
        finally:
            self._ss.active_backend_name = orig
        self.assertFalse(ok)
        self.assertIn("can't be saved", msg)
        token, status = hac.load_ha_token()
        self.assertIsNone(token)
        self.assertEqual(status, "missing")

    def test_build_client_for_refuses_before_any_network(self):
        hac = self._hac
        client, _reason = hac.build_ha_client_for(
            "8.8.8.8", "tok")
        self.assertIsNone(client)
        client, reason = hac.build_ha_client_for("", "tok")
        self.assertIsNone(client)
        self.assertIn("no Home Assistant instance", reason)
        client, reason = hac.build_ha_client_for(
            "192.168.1.10", "")
        self.assertIsNone(client)
        self.assertIn("no Home Assistant token", reason)
        client, reason = hac.build_ha_client_for(
            "192.168.1.10", "tok")
        self.assertIsNotNone(client)
        self.assertEqual(reason, "")

    def test_configured_only_with_host_and_readable_token(self):
        hac = self._hac
        self.assertFalse(hac.load_ha_config()["configured"])
        hac.save_ha_host("192.168.1.10")
        self.assertFalse(hac.load_ha_config()["configured"])
        hac.save_ha_token("test-token-123")
        cfg = hac.load_ha_config()
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["host"], "192.168.1.10")
        self.assertTrue(cfg["has_token"])


# ---------------------------------------------------------- pure: HA client
_TOKEN = "test-token-123"
_STATES = [
    {"entity_id": "light.living_room", "state": "on",
     "attributes": {"friendly_name": "Living Room", "brightness": 128}},
    {"entity_id": "switch.fan", "state": "off",
     "attributes": {"friendly_name": "Fan"}},
]
_SERVICES = [
    {"domain": "light",
     "services": {"turn_on": {}, "turn_off": {}, "toggle": {}}},
    {"domain": "switch",
     "services": {"turn_on": {}, "turn_off": {}, "toggle": {}}},
]


class _FakeHAHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []  # (method, path, headers-dict, body-bytes)

    def log_message(self, *args):  # keep the test output clean
        pass

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Bearer {_TOKEN}"

    def _send_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=b""):
        type(self).requests.append(
            (self.command, self.path, dict(self.headers), body))

    def do_GET(self):
        self._record()
        if not self._auth_ok():
            self._send_json(401, {"message": "Unauthorized"})
            return
        if self.path == "/api/":
            self._send_json(200, {"message": "API running."})
        elif self.path == "/api/config":
            self._send_json(200, {"version": "2026.11.0",
                                  "location_name": "Test Home"})
        elif self.path == "/api/states":
            self._send_json(200, _STATES)
        elif self.path.startswith("/api/states/"):
            entity = self.path.split("/api/states/", 1)[1]
            found = [s for s in _STATES if s["entity_id"] == entity]
            if found:
                self._send_json(200, found[0])
            else:
                self._send_json(404, {"message": "Entity not found."})
        elif self.path == "/api/areas":
            self._send_json(200, [{"name": "Living Room",
                                   "area_id": "living_room"}])
        elif self.path == "/api/devices":
            self._send_json(200, [{"name": "Test bulb", "id": "dev1"}])
        else:
            self._send_json(404, {"message": "Not found."})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record(body)
        if not self._auth_ok():
            self._send_json(401, {"message": "Unauthorized"})
            return
        if self.path.startswith("/api/services/"):
            self._send_json(200, _STATES)
        else:
            self._send_json(404, {"message": "Not found."})


class TestHaClientFakeHub(unittest.TestCase):
    """The client against a local fake hub (loopback only): reads,
    and the HM-1 gate — a refused control makes NO HTTP request."""

    @classmethod
    def setUpClass(cls):
        cls._server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeHAHandler)
        cls.port = cls._server.server_address[1]
        cls._thread = threading.Thread(
            target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def setUp(self):
        _FakeHAHandler.requests.clear()

    def _client(self, allowlist=None, exposed=None):
        from voice_assistant.core import homeassistant as ha
        from voice_assistant.core.reach import HomeAssistantScope
        cfg = ha.HomeAssistantConfig(
            host="127.0.0.1", token=_TOKEN, port=self.port)
        scope = HomeAssistantScope(
            expose_new=False, allowlist=dict(allowlist or {}),
            assist_exposed=set(exposed or set()))
        return ha.HomeAssistantClient(cfg, scope=scope)

    def test_health_areas_devices_states(self):
        client = self._client()
        self.assertEqual(client.health(), {"message": "API running."})
        self.assertEqual(client.get_areas(),
                         [{"name": "Living Room",
                           "area_id": "living_room"}])
        self.assertEqual(client.get_devices(),
                         [{"name": "Test bulb", "id": "dev1"}])
        self.assertEqual(len(client.get_states()), 2)

    def test_allowlisted_toggle_posts(self):
        client = self._client(
            allowlist={"light.living_room": "Living Room"},
            exposed={"light.living_room"})
        client.call_service("light", "toggle", "light.living_room")
        posts = [r for r in _FakeHAHandler.requests
                 if r[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1],
                         "/api/services/light/toggle")
        body = json.loads(posts[0][3].decode("utf-8"))
        self.assertEqual(body.get("entity_id"), "light.living_room")

    def test_non_allowlisted_control_refuses_with_no_request(self):
        from voice_assistant.core import homeassistant as ha
        client = self._client()
        before = len(_FakeHAHandler.requests)
        with self.assertRaises(ha.HomeAssistantError) as ctx:
            client.call_service("switch", "toggle", "switch.fan")
        self.assertEqual(ctx.exception.kind, "refused")
        self.assertEqual(len(_FakeHAHandler.requests), before)


# ---------------------------------------------------------- Qt: standalone
@unittest.skipUnless(_QT, "Qt unavailable")
class _P5PageCase(unittest.TestCase):
    """Standalone pages with provider dicts (the test_ui_p4
    shape): no window, no store, no network."""

    def setUp(self):
        _qt_app()
        import gui.window_pages_p5 as p5
        self._p5 = p5
        self._orig_private = p5._is_private
        self.win = _StubWin()
        self._pages = []

    def tearDown(self):
        # Deterministic teardown: a standalone page holds a
        # reference cycle (page -> drawer -> button lambda ->
        # page) that the refcount cannot collect, so a dropped
        # page used to survive to interpreter shutdown, where
        # Qt's own top-level sweep and the cyclic GC raced and
        # aborted the full suite (malloc_consolidate, rc=134,
        # tests OK). Deleting the pages here, while the event
        # loop is alive, removes that shutdown path.
        for pg in getattr(self, "_pages", []):
            try:
                pg.deleteLater()
            except Exception:  # noqa: BLE001 — teardown never breaks
                pass
        self._p5._is_private = self._orig_private
        _qt_app().processEvents()

    def _set_private(self, on):
        self._p5._is_private = (lambda: True) if on else (lambda: False)

    def _labels(self, widget):
        from PySide6.QtWidgets import QLabel
        return [lbl.text() for lbl in widget.findChildren(QLabel)]

    def _buttons(self, widget):
        from PySide6.QtWidgets import QPushButton
        return {b.text(): b for b in widget.findChildren(QPushButton)}


class _StubWin:
    """Minimal window stand-in for standalone pages: the drawer
    shows/hides, confirms answer True, toasts are recorded."""

    def __init__(self, confirm=True):
        self.confirm = confirm
        self.confirms = []
        self.toasts = []
        self.settings_cards = []
        self.palette_query = None
        self.cost_opened = False

    def _views_confirm_write(self, tool, label, sentence,
                             default=False):
        self.confirms.append((tool, label, sentence))
        return self.confirm

    def _views_open_drawer(self, drawer, main):
        drawer.setVisible(True)

    def _views_close_drawer(self, drawer, main):
        drawer.setVisible(False)

    def _views_toast(self, text):
        self.toasts.append(text)

    def show_settings_card(self, title):
        self.settings_cards.append(title)

    def _open_palette_with(self, query):
        self.palette_query = query

    def _open_cost_ledger(self):
        self.cost_opened = True


class _FakeNotes:
    """In-memory notes store behind the page's provider dict."""

    def __init__(self, entries):
        self.entries = [dict(e) for e in entries]
        self._n = len(entries)

    def load(self):
        return [dict(e) for e in self.entries], 0

    def add(self, **kw):
        text = " ".join(str(kw.get("text") or "").split())
        title = " ".join(str(kw.get("title") or "").split())
        if not title:
            title = (text.splitlines()[0][:60] if text else "")
        self._n += 1
        key = f"note-{self._n:03d}"
        self.entries.append({"key": key, "title": title, "text": text,
                             "added": "2026-11-27", "pinned": False,
                             "edited": ""})
        return f"Noted: '{title}'."

    def remove_by_key(self, key):
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["key"] != key]
        if len(self.entries) == before:
            return False, "That note is already gone."
        return True, "Removed."

    def edit_by_key(self, key, title, text):
        for entry in self.entries:
            if entry["key"] == key:
                entry["title"] = " ".join(
                    str(title or "").split())[:100]
                entry["text"] = " ".join(str(text or "").split())
                entry["edited"] = datetime.date.today().isoformat()
                return True, f"Saved '{entry['title']}'."
        return False, "That note is already gone."

    def set_pinned(self, key, pinned):
        for entry in self.entries:
            if entry["key"] == key:
                entry["pinned"] = bool(pinned)
                return True, ("Pinned." if pinned else "Unpinned.")
        return False, "That note is already gone."


def _note_entries():
    return [
        {"key": "note-001", "title": "Groceries list",
         "text": "Buy milk and bread", "added": "2026-11-25",
         "pinned": False, "edited": ""},
        {"key": "note-002", "title": "Meeting notes",
         "text": "Standup at ten", "added": "2026-11-26",
         "pinned": True, "edited": "2026-11-26"},
        {"key": "note-003", "title": "Book ideas",
         "text": "A novel about a lighthouse", "added": "2026-11-27",
         "pinned": False, "edited": ""},
    ]


class TestNotesPageQt(_P5PageCase):
    def _page(self, entries=None):
        from gui.window_pages_p5 import NotesPage
        fake = _FakeNotes(entries if entries is not None
                          else _note_entries())
        page = NotesPage(self.win, providers={
            "load": fake.load, "add": fake.add,
            "remove_by_key": fake.remove_by_key,
            "edit_by_key": fake.edit_by_key,
            "set_pinned": fake.set_pinned})
        page.show()
        _qt_app().processEvents()
        page.refresh()
        _qt_app().processEvents()
        self._pages.append(page)
        return page, fake

    def test_list_pinned_first_then_newest(self):
        page, _fake = self._page()
        items = [page._notes_list.item(i).text()
                 for i in range(page._notes_list.count())]
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("Pinned — Meeting notes"))
        self.assertTrue(items[1].startswith("Book ideas"))
        self.assertTrue(items[2].startswith("Groceries list"))
        self.assertEqual(page._count_label.text(), "3 notes")

    def test_search_filters_and_counts(self):
        page, _fake = self._page()
        page._search.setText("groceries")
        _qt_app().processEvents()
        self.assertEqual(page._count_label.text(), "1 of 3 notes")
        self.assertEqual(page._notes_list.count(), 1)
        page._search.setText("zzz-no-match")
        _qt_app().processEvents()
        self.assertTrue(page._empty.isVisible())
        self.assertEqual(page._empty.sentence_text,
                         "No notes match 'zzz-no-match'.")

    def test_broken_count_line(self):
        from gui.window_pages_p5 import NotesPage
        page = NotesPage(self.win, providers={
            "load": lambda: (_note_entries(), 2)})
        page.show()
        _qt_app().processEvents()
        page.refresh()
        _qt_app().processEvents()
        self.assertTrue(page._broken_label.isVisible())
        self.assertIn("2 stored notes", page._broken_label.text())

    def test_pin_toggle_re_renders_with_marker(self):
        page, fake = self._page()
        page._open_detail("note-001")
        _qt_app().processEvents()
        self.assertTrue(page._drawer.isVisible())
        pin = self._buttons(page._drawer)["Pin"]
        pin.click()
        _qt_app().processEvents()
        self.assertTrue(
            next(e for e in fake.entries
                 if e["key"] == "note-001")["pinned"])
        # Both pinned now, so the pinned group keeps key-descending
        # order: note-002 (Meeting notes) still before note-001.
        items = [page._notes_list.item(i).text()
                 for i in range(page._notes_list.count())]
        self.assertTrue(items[0].startswith("Pinned — Meeting notes"))
        self.assertTrue(items[1].startswith("Pinned — Groceries list"))
        self.assertTrue(items[2].startswith("Book ideas"))

    def test_detail_re_reads_by_key_and_names_a_removed_note(self):
        page, fake = self._page()
        fake.remove_by_key("note-001")
        page._open_detail("note-001")
        _qt_app().processEvents()
        self.assertIn("This note was removed.",
                      self._labels(page._drawer))

    def test_add_drawer_saves_and_re_reads(self):
        page, fake = self._page()
        page._add_btn.click()
        _qt_app().processEvents()
        self.assertTrue(page._drawer.isVisible())
        from PySide6.QtWidgets import QLineEdit, QPlainTextEdit
        title = page._drawer.findChildren(QLineEdit)[0]
        body = page._drawer.findChildren(QPlainTextEdit)[0]
        title.setText("New idea")
        body.setPlainText("Build a better lamp")
        self._buttons(page._drawer)["Save note"].click()
        _qt_app().processEvents()
        self.assertEqual(len(fake.entries), 4)
        self.assertIn("New idea",
                      [e["title"] for e in fake.entries])
        self.assertFalse(page._drawer.isVisible())
        self.assertTrue(any(t.startswith("Noted:")
                            for t in self.win.toasts))

    def test_private_mode_blanks_list_keeps_count(self):
        page, _fake = self._page()
        self._set_private(True)
        page.refresh()
        _qt_app().processEvents()
        self.assertFalse(page._notes_list.isVisible())
        self.assertFalse(page._add_btn.isVisible())
        self.assertEqual(page._count_label.text(), "3 notes")
        self.assertTrue(page._private_label.isVisible())


class TestFinancePageQt(_P5PageCase):
    def _page(self):
        from gui.window_pages_p5 import FinancePage
        self.calls = []

        def _envelope(**kw):
            self.calls.append(("envelope", kw))
            return "Set."

        def _subscription(**kw):
            self.calls.append(("subscription", kw))
            return "Saved."

        def _portfolio(**kw):
            self.calls.append(("portfolio", kw))
            return "Saved."

        page = FinancePage(self.win, providers={
            "load_envelopes": lambda: ([
                {"key": "Food", "budget": 1000.0, "spent": 1500.0},
                {"key": "Travel", "budget": 5000.0, "spent": 1200.0},
            ], 0),
            "load_subscriptions": lambda: ([
                {"key": "Music", "monthly": 199.0, "renewal_day": 3},
            ], 0),
            "load_portfolio": lambda: ([
                {"key": "INFY", "quantity": 10.0, "buy_price": 1500.0,
                 "price": None},
                {"key": "TCS", "quantity": 5.0, "buy_price": 3000.0,
                 "price": 3200.0},
            ], 0),
            "envelope": _envelope,
            "subscription": _subscription,
            "portfolio": _portfolio,
            "standing_alerts": lambda: ["Food is over budget."],
            "check_alerts": lambda: (
                "Money alerts (1):\n• Food is over budget.\n"),
            "next_renewal": lambda day, today: datetime.date(
                2026, 12, 3),
        })
        page.show()
        _qt_app().processEvents()
        page.refresh()
        _qt_app().processEvents()
        self._pages.append(page)
        return page

    def test_unsupported_state_is_first_and_has_no_bank_button(self):
        page = self._page()
        labels = self._labels(page)
        self.assertIn("No bank or account connected", labels)
        self.assertLess(
            labels.index("No bank or account connected"),
            labels.index("Budget ₹1,000.00, spent ₹1,500.00 since "
                         "set — ₹500.00 OVER budget (150% used)"))
        for text in labels:
            self.assertNotIn("Connect a bank", text)
        self.assertNotIn("Connect a bank",
                         self._buttons(page))

    def test_envelopes_over_budget_pill_and_totals(self):
        page = self._page()
        labels = self._labels(page)
        self.assertIn("Over budget", labels)
        self.assertIn("Within budget", labels)
        self.assertTrue(any("Totals: budgets" in t for t in labels))
        self.assertTrue(any("₹6,000" in t for t in labels))

    def test_subscriptions_show_renewal(self):
        page = self._page()
        page._tabs.setCurrentIndex(1)
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertTrue(any("renews 2026-12-03" in t for t in labels))
        self.assertTrue(any("₹199.00 a month" in t for t in labels))

    def test_portfolio_unvalued_and_no_advice(self):
        page = self._page()
        page._tabs.setCurrentIndex(2)
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertTrue(any("No price recorded" in t for t in labels))
        self.assertTrue(any("Unvalued: INFY" in t for t in labels))
        self.assertTrue(any("No advice, no trading." in t
                            for t in labels))

    def test_alerts_standing_and_check_for_new(self):
        page = self._page()
        page._tabs.setCurrentIndex(3)
        _qt_app().processEvents()
        self.assertIn("Food is over budget.",
                      self._labels(page))
        self._buttons(page)["Check for new alerts"].click()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertIn("Due now", labels)
        self.assertIn("Food is over budget.", labels)

    def test_calculator_launchers_and_cost_link(self):
        page = self._page()
        buttons = self._buttons(page)
        for label in ("Work out how long a savings goal takes",
                      "Work out how an amount grows with interest",
                      "Work out a loan payment",
                      "Split a bill with tip and tax",
                      "Convert an hourly rate",
                      "Compare prices per unit",
                      "Stack discounts on a price",
                      "Split rent fairly",
                      "Work out a fund's returns"):
            self.assertIn(label, buttons)
        buttons["Work out how long a savings goal takes"].click()
        _qt_app().processEvents()
        self.assertEqual(self.win.palette_query, "savings_goal")
        buttons["What VYUK's brain has cost you"].click()
        _qt_app().processEvents()
        self.assertTrue(self.win.cost_opened)

    def test_private_mode_blanks_figures_keeps_counts(self):
        page = self._page()
        self._set_private(True)
        page.refresh()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertFalse(any("₹" in t for t in labels))
        self.assertIn("2 envelopes",
                      page._tab_totals["Envelopes"].text())
        self.assertFalse(page._cost_btn.isVisible())
        self.assertIn("No bank or account connected", labels)


class _FakeErr:
    def __init__(self, kind):
        self.kind = kind


class _FakeHAClient:
    """Fake hub client behind the Devices page's providers."""

    def __init__(self):
        self.calls = []
        self.states = [
            {"entity_id": "light.living_room", "state": "off",
             "attributes": {"friendly_name": "Living Room light"},
             "last_updated": "2026-11-27T03:00:00+00:00",
             "device_id": "dev1"},
            {"entity_id": "switch.fan", "state": "off",
             "attributes": {"friendly_name": "Fan"}},
        ]
        self.areas = [{"name": "Living Room",
                       "area_id": "living_room"}]
        self.devices = [{"name": "Test bulb", "id": "dev1",
                         "area_id": "living_room",
                         "entities": ["light.living_room"]},
                        {"name": "Orphan", "id": "dev2"}]

    def health(self):
        return {"message": "API running."}

    def get_areas(self):
        return list(self.areas)

    def get_devices(self):
        return list(self.devices)

    def get_states(self):
        return list(self.states)

    def get_state(self, entity_id):
        for state in self.states:
            if state["entity_id"] == entity_id:
                return dict(state)
        raise _FakeErr("not_found")

    def call_service(self, domain, service, entity_id):
        self.calls.append((domain, service, entity_id))
        for state in self.states:
            if state["entity_id"] == entity_id:
                if service == "turn_on":
                    state["state"] = "on"
                elif service == "turn_off":
                    state["state"] = "off"
                elif service == "toggle":
                    state["state"] = ("off" if state["state"] == "on"
                                      else "on")
        return list(self.states)


class TestDevicesPageQt(_P5PageCase):
    def _page(self, config=None, client=None, grant=True):
        from gui.window_pages_p5 import DevicesPage
        from voice_assistant.core import home_assistant_config as hac
        self._client = client if client is not None else _FakeHAClient()
        cfg = {"configured": True, "host": "192.168.1.10",
               "has_token": True, "token_status": "ok",
               "allowlist": {"light.living_room": "Living Room"},
               "assist_exposed": {"light.living_room"}}
        if config is not None:
            cfg.update(config)
        page = DevicesPage(self.win, providers={
            "load_config": lambda: dict(cfg),
            "build_client": lambda: (self._client, ""),
            "has_grant": lambda: grant,
            "error_line": hac.ha_error_line,
        })
        page.show()
        _qt_app().processEvents()
        self._pages.append(page)
        return page

    def test_not_configured_first(self):
        page = self._page(config={"configured": False, "host": ""})
        page.refresh()
        _qt_app().processEvents()
        self.assertIn("No home hub connected yet.",
                      self._labels(page))
        self.assertIn("Set up a hub", self._buttons(page))
        page._refresh_btn.setVisible(True)
        self._buttons(page)["Set up a hub"].click()
        _qt_app().processEvents()
        self.assertEqual(self.win.settings_cards, ["Home hub"])

    def test_no_permission_state(self):
        page = self._page(grant=False)
        page.refresh()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertIn("Home devices need your permission", labels)
        self.assertIn("Open Permissions", self._buttons(page))
        self._buttons(page)["Open Permissions"].click()
        _qt_app().processEvents()
        self.assertEqual(self.win.settings_cards, ["Permissions"])

    def test_connected_rooms_devices_and_unassigned(self):
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertIn("Living Room", labels)
        self.assertIn("Test bulb", labels)
        self.assertIn("Orphan", labels)
        self.assertIn("No room assigned", labels)
        self.assertIn("Entities without a device", labels)
        self.assertIn("switch.fan", labels)
        self.assertIn("Unknown", labels)

    def test_error_keeps_last_good_with_stamp(self):
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        self.assertIn("Test bulb", self._labels(page))
        page._build_client = lambda: (None, "The hub couldn't be "
                                            "reached.")
        page.refresh()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertIn("Test bulb", labels)
        self.assertTrue(page._stale_label.text().startswith(
            "Last updated"))

    def test_control_confirm_gated_and_receipt_names_the_hub(self):
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        page._open_device_detail(
            {"id": "dev1", "name": "Test bulb",
             "area_id": "living_room",
             "states": [{"entity_id": "light.living_room",
                         "name": "Living Room light", "state": "off",
                         "last_seen": "2026-11-27T03:00:00+00:00"}],
             "state_text": "off"}, "Living Room")
        _qt_app().processEvents()
        self.assertTrue(page._drawer.isVisible())
        buttons = self._buttons(page._drawer)
        self.assertIn("Turn on", buttons)
        buttons["Turn on"].click()
        _qt_app().processEvents()
        self.assertEqual(self.win.confirms[0][0], "home_devices")
        self.assertIn("Living Room light", self.win.confirms[0][1])
        self.assertIn("Turn Living Room light on.",
                      self.win.confirms[0][2])
        self.assertEqual(
            self._client.calls,
            [("light", "turn_on", "light.living_room")])
        self.assertTrue(any("is on — confirmed by the hub" in t
                            for t in self._labels(page._drawer)))
        self.assertTrue(any("is on — confirmed by the hub" in t
                            for t in self.win.toasts))

    def test_non_allowlisted_entity_offers_no_control(self):
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        page._open_entity_detail(
            {"entity_id": "switch.fan", "name": "Fan", "state": "off",
             "last_seen": ""})
        _qt_app().processEvents()
        labels = self._labels(page._drawer)
        self.assertTrue(any("Not allowed to be controlled" in t
                            for t in labels))
        self.assertNotIn("Turn on", self._buttons(page._drawer))

    def test_private_mode_hides_names_keeps_counts(self):
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        self._set_private(True)
        page.refresh()
        _qt_app().processEvents()
        labels = self._labels(page)
        self.assertNotIn("Test bulb", labels)
        self.assertNotIn("Living Room", labels)
        self.assertTrue(any("1 room" in t and "2 device" in t
                            for t in labels))

    def test_drawer_open_before_private_closes_on_refresh(self):
        # A drawer opened before private mode was turned on still
        # shows names built pre-private — a refresh while private
        # is on closes it (second render pass: the entity drawer
        # stayed open over the private page).
        page = self._page()
        page.refresh()
        _qt_app().processEvents()
        page._open_device_detail(
            {"id": "dev1", "name": "Test bulb",
             "area_id": "living_room",
             "states": [{"entity_id": "light.living_room",
                         "name": "Living Room light", "state": "off",
                         "last_seen": "2026-11-27T03:00:00+00:00"}],
             "state_text": "off"}, "Living Room")
        _qt_app().processEvents()
        self.assertTrue(page._drawer.isVisible())
        self._set_private(True)
        page.refresh()
        _qt_app().processEvents()
        self.assertFalse(page._drawer.isVisible())


# ---------------------------------------------------------- Qt: full window
@unittest.skipUnless(_QT, "Qt unavailable")
class TestP5Window(WindowBase):
    """The wired window: 15 buttons, stack 12/13/14, palette
    rows, no new badge, the Home hub card in Settings."""

    @classmethod
    def setUpClass(cls):
        import gui.w9_wiring_proof  # noqa: F401 — E-F209 pre-import

    def test_fifteen_nav_buttons_in_visual_order(self):
        self.assertEqual(len(self.window.nav_buttons), 15)
        # The buttons are icon-only in the test window's tier, so
        # their text is empty — the accessible description is the
        # stable identifier (the test_ui_p3 shape).
        self.assertEqual(
            [b.accessibleDescription()
             for b in self.window.nav_buttons],
            ["Show the Home page", "Show the Activity page",
             "Show the Memory page", "Show the Skills page",
             "Show the System Pulse page", "Show the Senses page",
             "Show the Doctor page", "Show the Calendar page",
             "Show the Inbox page", "Show the Contacts page",
             "Show the Calls page",
             "Show the Notes page — your notes, on this machine",
             "Show the Finance page — money you record yourself",
             "Show the Devices page — your home hub, on your "
             "network",
             "Show the Settings page"])
        self.assertEqual(self.window._nav_page_indices,
                         [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11,
                          12, 13, 14, 4])

    def test_nav_buttons_show_stack_12_13_14(self):
        for button, index in ((11, 12), (12, 13), (13, 14)):
            self.window.nav_buttons[button].click()
            _qt_app().processEvents()
            self.assertEqual(self.window.pages.currentIndex(), index)
            self.assertTrue(
                self.window.nav_buttons[button].isChecked())

    def test_checked_nav_button_fully_visible_at_1280(self):
        # UI Phase 5: fifteen buttons overflow the sidebar at
        # 1280x800 — _show_page scrolls the checked button into
        # view, so the selected workspace is never half-cut
        # below the fold (first render pass: Finance half-cut,
        # Devices off the fold entirely).
        from PySide6.QtCore import QPoint
        self.window.resize(1280, 800)
        _qt_app().processEvents()
        self.window._fix_chat_width()
        _qt_app().processEvents()
        scroll = self.window._sidebar_scroll
        pages = getattr(self.window, "_nav_page_indices", None)
        for index in (12, 13, 14, 4):
            self.window._show_page(index)
            _qt_app().processEvents()
            button = next(
                b for i, b in enumerate(self.window.nav_buttons)
                if (pages[i] if pages and i < len(pages) else i)
                == index)
            self.assertTrue(button.isChecked())
            top = button.mapTo(scroll.viewport(), QPoint(0, 0)).y()
            self.assertGreaterEqual(
                top, 0, f"page {index} button cut at the top")
            self.assertLessEqual(
                top + button.height(), scroll.viewport().height(),
                f"page {index} button cut at the bottom")

    def test_palette_has_the_three_page_rows(self):
        self.window._show_command_palette()
        _qt_app().processEvents()
        labels = [c["label"] for c in
                  self.window._palette._build_commands()]
        self.assertIn("Go to Notes", labels)
        self.assertIn("Go to Finance", labels)
        self.assertIn("Go to Devices", labels)

    def test_open_palette_with_types_the_query(self):
        self.window._open_palette_with("savings_goal")
        _qt_app().processEvents()
        self.assertEqual(self.window._palette.input.text(),
                         "savings_goal")

    def test_no_new_badge(self):
        self.assertTrue(hasattr(self.window, "_badge_activity"))
        self.assertTrue(hasattr(self.window, "_badge_inbox"))
        self.assertFalse(hasattr(self.window, "_badge_notes"))
        self.assertFalse(hasattr(self.window, "_badge_finance"))
        self.assertFalse(hasattr(self.window, "_badge_devices"))
        self.assertIs(self.window._badge_activity.parent(),
                      self.window.nav_buttons[1])
        self.assertIs(self.window._badge_inbox.parent(),
                      self.window.nav_buttons[8])

    def test_home_hub_card_in_settings(self):
        _show(self.window, 4)
        from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton
        titles = {lbl.text() for lbl in
                  self.window.settings_content.findChildren(
                      QLabel, "sectionTitle")}
        self.assertIn("Home hub", titles)
        host = self.window._ha_host_edit
        self.assertIsInstance(host, QLineEdit)
        self.assertEqual(host.accessibleName(),
                         "Home Assistant hub address")
        self.assertIn("Test connection and save",
                      [b.text() for b in
                       self.window.findChildren(QPushButton)])
