"""B-D7 (2026-11-09) browser automation via Playwright.

Runs against a FAKE Playwright backend + a FAKE CLOCK (no real
Chrome, no Playwright on this Linux lab machine, no real
waiting). The engine-availability tests assert the honest
engine_unavailable degradation where the library is absent
instead of skipping: a suite that silently skips the
automation checks on an interpreter without the library is a
lie, not a pass. Each test states which interpreter it ran on
through the engine-status test, which compares the wrapper's
report against importlib's own probe.

The real-Windows leg — real Playwright driving real installed
Chrome on his machine — is a field leg, owed, not this batch.
"""

import importlib.util
import unittest

from core.desktop import browser_automation as ba


class _FakeClock:
    """monotonic()/sleep() with no real waiting."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class _FakeElement:
    def __init__(self, *, tag="button", text="", role="",
                 type=None, label="", value="", visible=True,
                 on_click=("nothing",), fill_lands_as=None,
                 value_unreadable=False, type_unreadable=False):
        self.tag = tag
        self.text = text
        self.role = role
        self.type = type
        self.label = label
        self.value = value
        self.visible = visible
        self.on_click = on_click
        self.fill_lands_as = fill_lands_as
        self.value_unreadable = value_unreadable
        self.type_unreadable = type_unreadable
        self.gone = False


class _FakePage:
    def __init__(self, url, title, text=""):
        self.url = url
        self.title = title
        self.text = text
        self.loading = False
        self.elements = {}  # selector -> _FakeElement


class _FakeBackend:
    """The fake Playwright backend. Records every action so a
    test can prove the wrapper did NOT act (wrong tab, during
    load), not just that it returned a failure."""

    NAV = {
        "https://example.com/": ("Example Domain", "Example page text."),
        "https://news.example.com/": ("News", "News page text."),
    }

    def __init__(self):
        self.started = False
        self.profile_dir = None
        self.calls = []
        self.pages_list = []
        self.closed = 0

    def start(self, profile_dir):
        self.started = True
        self.profile_dir = profile_dir
        home = _FakePage("about:blank", "", "")
        self.pages_list = [home]

    def open_tab(self, url, title, text=""):
        page = _FakePage(url, title, text)
        self.pages_list.append(page)
        return page

    def remove_page(self, page):
        self.pages_list = [p for p in self.pages_list if p is not page]

    def pages(self):
        return [{"handle": p, "url": p.url, "title": p.title}
                for p in self.pages_list]

    def load_state(self, handle):
        return "loading" if handle.loading else "loaded"

    def navigate(self, handle, url):
        self.calls.append(("navigate", handle.url, url))
        title, text = self.NAV.get(url, ("Untitled", ""))
        handle.url = url
        handle.title = title
        handle.text = text
        handle.loading = False

    def matches(self, handle, selector):
        el = handle.elements.get(selector)
        if el is None:
            return []
        return [{"handle": el, "visible": el.visible}]

    def element_identity(self, el):
        if el.gone:
            raise ba.PageGone("the element is gone")
        return {"tag": el.tag, "text": el.text, "role": el.role}

    def click_element(self, el):
        self.calls.append(("click", el.text))
        kind = el.on_click[0]
        if kind == "navigate":
            el.gone = True
            return {"changed": True,
                    "detail": "page changed after the click"}
        if kind == "text":
            el.text = el.on_click[1]
            return {"changed": True,
                    "detail": "element text changed after the click"}
        if kind == "retarget":
            el.tag = el.on_click[1]
            return {"changed": True,
                    "detail": "element replaced after the click"}
        return {"changed": False,
                "detail": "no change was observed after the click"}

    def field_type(self, el):
        if el.type_unreadable:
            raise RuntimeError("cannot read type")
        return el.type

    def field_label(self, el):
        return el.label

    def fill_element(self, el, text):
        self.calls.append(("fill", el.label, text))
        el.value = (text if el.fill_lands_as is None
                    else el.fill_lands_as)

    def field_value(self, el):
        if el.value_unreadable:
            return None
        return el.value

    def page_text(self, handle):
        return handle.text

    def page_url(self, handle):
        return handle.url

    def page_title(self, handle):
        return handle.title

    def close(self):
        self.closed += 1
        self.started = False


def _launched(clock=None):
    backend = _FakeBackend()
    auto = ba.BrowserAutomation(backend=backend,
                                clock=clock or _FakeClock())
    result = auto.launch()
    assert result["ok"], result
    return auto, backend


def _home_page(auto):
    tabs = auto.list_tabs()
    assert tabs["ok"], tabs
    return tabs["tabs"][0]


class TestEngineAvailability(unittest.TestCase):
    def test_engine_status_matches_reality(self):
        """The wrapper's availability report agrees with
        importlib's own probe on THIS interpreter — it never
        claims an engine that is not there, on either
        interpreter."""
        status = ba.engine_status()
        really_there = importlib.util.find_spec("playwright") is not None
        self.assertEqual(status["available"], really_there)
        self.assertEqual(status["engine"], "playwright")
        if not really_there:
            self.assertEqual(status["reason"] is not None, True)
            self.assertIn("engine_unavailable", status["reason"])

    def test_launch_without_engine_is_engine_unavailable(self):
        """With NO backend injected (the real path): when
        Playwright is absent on this interpreter, launch()
        degrades honestly to engine_unavailable instead of
        crashing or pretending. When Playwright IS present,
        the honest statement is that the engine exists — the
        real-Chrome launch itself is the field leg."""
        if importlib.util.find_spec("playwright") is None:
            auto = ba.BrowserAutomation(clock=_FakeClock())
            result = auto.launch()
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "engine_unavailable")
        else:
            self.assertTrue(ba.engine_available())


class TestLaunchAndTabs(unittest.TestCase):
    def test_launch_returns_page_identity(self):
        auto, backend = _launched()
        result = auto.launch()
        self.assertTrue(result["ok"])
        self.assertEqual(result["page"]["url"], "about:blank")
        self.assertIn("tab_id", result["page"])

    def test_launch_uses_vyuk_profile_not_user_chrome(self):
        auto, backend = _launched()
        self.assertIn("browser_profile", backend.profile_dir)
        self.assertNotIn("Google", backend.profile_dir)

    def test_list_tabs_names_each_tab(self):
        auto, backend = _launched()
        backend.open_tab("https://example.com/", "Example Domain")
        tabs = auto.list_tabs()
        self.assertTrue(tabs["ok"])
        self.assertEqual(len(tabs["tabs"]), 2)
        self.assertEqual(tabs["tabs"][1]["url"],
                         "https://example.com/")
        self.assertEqual(tabs["tabs"][1]["title"], "Example Domain")
        self.assertNotEqual(tabs["tabs"][0]["tab_id"],
                            tabs["tabs"][1]["tab_id"])

    def test_tab_identity_is_stable_not_index(self):
        """THE wrong-tab loophole, closed: tabs are named by a
        stable tab_id, never by index. Closing tab-1 must not
        shift tab-2's identity — acting on tab-2's identity
        after tab-1 closes still acts on tab-2."""
        auto, backend = _launched()
        page2 = backend.open_tab("https://example.com/",
                                 "Example Domain")
        tabs = auto.list_tabs()["tabs"]
        tab2 = tabs[1]
        self.assertEqual(tab2["url"], "https://example.com/")
        backend.remove_page(backend.pages_list[0])  # tab-1 closes
        result = auto.navigate(tab2, "https://news.example.com/")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["page"]["tab_id"], tab2["tab_id"])
        self.assertEqual(result["page"]["url"],
                         "https://news.example.com/")
        self.assertEqual(page2.url, "https://news.example.com/")

    def test_close_only_closes_what_launch_started(self):
        auto, backend = _launched()
        result = auto.close()
        self.assertTrue(result["ok"])
        self.assertEqual(backend.closed, 1)
        again = auto.close()
        self.assertFalse(again["ok"])
        self.assertEqual(again["reason"], "not_launched")
        self.assertEqual(backend.closed, 1)  # close called once


class TestNavigate(unittest.TestCase):
    def test_navigate_returns_url_and_title_read_back(self):
        auto, backend = _launched()
        page = _home_page(auto)
        result = auto.navigate(page, "https://example.com/")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["page"]["url"], "https://example.com/")
        self.assertEqual(result["page"]["title"], "Example Domain")
        self.assertEqual(backend.pages_list[0].url,
                         "https://example.com/")

    def test_navigate_refuses_non_http(self):
        auto, backend = _launched()
        page = _home_page(auto)
        result = auto.navigate(page, "file:///etc/passwd")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "bad_input")
        self.assertEqual(backend.calls, [])  # never navigated

    def test_navigate_wrong_page_refuses_never_acts(self):
        """A stale identity (the tab has moved on) refuses —
        the wrapper never navigates the wrong page."""
        auto, backend = _launched()
        page = _home_page(auto)
        auto.navigate(page, "https://example.com/")
        stale = dict(page)  # still says about:blank
        calls_before = list(backend.calls)
        result = auto.navigate(stale, "https://news.example.com/")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wrong_page")
        self.assertEqual(backend.calls, calls_before)
        self.assertEqual(backend.pages_list[0].url,
                         "https://example.com/")

    def test_navigate_unknown_tab_refuses(self):
        auto, backend = _launched()
        result = auto.navigate({"tab_id": "tab-99",
                                "url": "https://example.com/",
                                "title": "Nope"},
                               "https://news.example.com/")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wrong_page")
        self.assertEqual(backend.calls, [])


class TestClick(unittest.TestCase):
    def _page_with_button(self, on_click=("text", "Pause")):
        auto, backend = _launched()
        page = _home_page(auto)
        auto.navigate(page, "https://example.com/")
        page = auto.list_tabs()["tabs"][0]
        el = _FakeElement(tag="button", text="Play", role="button",
                          on_click=on_click)
        backend.pages_list[0].elements["text=Play"] = el
        return auto, backend, page, el

    def test_click_verifies_element_by_readback(self):
        """The click's result carries the element identity READ
        BACK after the click — a label that legitimately changes
        (Play -> Pause) is an effect, not a mismatch, and the
        result says so with the AFTER text."""
        auto, backend, page, el = self._page_with_button()
        result = auto.click(page, "text=Play")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["element"]["text"], "Pause")
        self.assertEqual(result["element"]["tag"], "button")
        self.assertEqual(result["page"]["url"], "https://example.com/")
        self.assertEqual(result["page"]["title"], "Example Domain")
        self.assertEqual(el.text, "Pause")  # the fake was really clicked

    def test_click_on_navigating_button_names_new_page(self):
        auto, backend, page, el = self._page_with_button(
            on_click=("navigate", "https://news.example.com/"))
        backend.pages_list[0].elements["text=Play"] = el
        # The fake navigation also moves the page itself.
        orig_click = backend.click_element

        def _click_and_move(clicked):
            out = orig_click(clicked)
            backend.pages_list[0].url = "https://news.example.com/"
            backend.pages_list[0].title = "News"
            return out
        backend.click_element = _click_and_move
        result = auto.click(page, "text=Play")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["page"]["url"],
                         "https://news.example.com/")
        self.assertEqual(result["page"]["title"], "News")

    def test_click_wrong_tab_refuses_never_acts(self):
        """Asked for tab A, the live tab is elsewhere: refuse,
        and prove by the call record that no click happened."""
        auto, backend, page, el = self._page_with_button()
        stale = {"tab_id": page["tab_id"],
                 "url": "https://other.example/",
                 "title": "Other"}
        result = auto.click(stale, "text=Play")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "wrong_page")
        self.assertEqual(el.text, "Play")  # never clicked
        self.assertEqual(
            [c for c in backend.calls if c[0] == "click"], [])

    def test_click_no_match_refuses(self):
        auto, backend, page, el = self._page_with_button()
        result = auto.click(page, "text=Missing")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_found")
        self.assertEqual(el.text, "Play")

    def test_click_ambiguous_refuses(self):
        auto, backend, page, el = self._page_with_button()
        other = _FakeElement(tag="button", text="Play", role="button",
                             on_click=("nothing",))
        backend.pages_list[0].elements["text=Play2"] = other
        # One selector matching two visible elements: model it by
        # pointing the same key at a second visible element via a
        # backend that returns both.
        orig_matches = backend.matches

        def _two_matches(handle, selector):
            if selector == "button":
                return [{"handle": el, "visible": True},
                        {"handle": other, "visible": True}]
            return orig_matches(handle, selector)
        backend.matches = _two_matches
        result = auto.click(page, "button")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "ambiguous")
        self.assertEqual(el.text, "Play")
        self.assertEqual(other.text, "Play")

    def test_click_no_effect_is_failure(self):
        """Playwright's click() succeeding only proves the event
        was dispatched — a swallowed click must never read as a
        success."""
        auto, backend, page, el = self._page_with_button(
            on_click=("nothing",))
        result = auto.click(page, "text=Play")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_effect")

    def test_click_element_replaced_is_mismatch(self):
        """The element read back as a different kind of control
        after the click — the click may have landed on the wrong
        element."""
        auto, backend, page, el = self._page_with_button(
            on_click=("retarget", "a"))
        result = auto.click(page, "text=Play")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "click_mismatch")


class TestFill(unittest.TestCase):
    def _page_with_field(self, **kw):
        auto, backend = _launched()
        page = _home_page(auto)
        auto.navigate(page, "https://example.com/")
        page = auto.list_tabs()["tabs"][0]
        kw.setdefault("type", "text")
        kw.setdefault("label", "Search")
        el = _FakeElement(tag="input", text="", role="textbox", **kw)
        backend.pages_list[0].elements["label=Search"] = el
        return auto, backend, page, el

    def test_fill_verifies_field_identity_by_readback(self):
        """The result carries the field's LABEL and VALUE read
        back after filling — identity verified, not assumed."""
        auto, backend, page, el = self._page_with_field()
        result = auto.fill(page, "label=Search", "hello")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["field"]["label"], "Search")
        self.assertEqual(result["field"]["value"], "hello")
        self.assertEqual(result["page"]["url"], "https://example.com/")
        self.assertEqual(el.value, "hello")

    def test_fill_mismatch_is_failure(self):
        auto, backend, page, el = self._page_with_field(
            fill_lands_as="hell")
        result = auto.fill(page, "label=Search", "hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "fill_mismatch")
        self.assertIn("hell", result["detail"])

    def test_fill_unreadable_value_is_failure(self):
        auto, backend, page, el = self._page_with_field(
            value_unreadable=True)
        result = auto.fill(page, "label=Search", "hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "fill_unverified")

    def test_fill_password_refuses(self):
        auto, backend, page, el = self._page_with_field(type="password")
        result = auto.fill(page, "label=Search", "secret")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "refused_password")
        self.assertEqual(el.value, "")
        self.assertEqual(
            [c for c in backend.calls if c[0] == "fill"], [])

    def test_fill_unreadable_type_refuses(self):
        """An inspection failure is NOT 'not a password field' —
        refuse on a guess."""
        auto, backend, page, el = self._page_with_field(
            type_unreadable=True)
        result = auto.fill(page, "label=Search", "hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "refused_password")
        self.assertEqual(el.value, "")

    def test_fill_no_match_refuses(self):
        auto, backend, page, el = self._page_with_field()
        result = auto.fill(page, "label=Missing", "hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_found")
        self.assertEqual(el.value, "")


class TestLoadStateAndRead(unittest.TestCase):
    def test_action_during_load_refuses_never_silently_acts(self):
        """A half-loaded page: navigate, click, fill and read ALL
        refuse with still_loading — and the call record proves
        nothing was acted on. Once settled, the same action
        succeeds."""
        auto, backend = _launched()
        page = _home_page(auto)
        backend.pages_list[0].loading = True
        backend.pages_list[0].elements["text=Play"] = _FakeElement(
            tag="button", text="Play", on_click=("text", "Pause"))
        backend.pages_list[0].elements["label=Search"] = _FakeElement(
            tag="input", type="text", label="Search")
        for result in (auto.navigate(page, "https://example.com/"),
                       auto.click(page, "text=Play"),
                       auto.fill(page, "label=Search", "hi"),
                       auto.read(page)):
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["reason"], "still_loading")
        self.assertEqual(backend.calls, [])
        backend.pages_list[0].loading = False
        result = auto.read(page)
        self.assertTrue(result["ok"], result)

    def test_read_returns_text_title_url(self):
        auto, backend = _launched()
        page = _home_page(auto)
        auto.navigate(page, "https://example.com/")
        page = auto.list_tabs()["tabs"][0]
        result = auto.read(page)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "Example page text.")
        self.assertEqual(result["page"]["url"], "https://example.com/")
        self.assertEqual(result["page"]["title"], "Example Domain")

    def test_result_names_page_it_acted_on(self):
        """Every ok result names the page it acted on (URL +
        title, read back after the action) — the user always
        knows which page was acted on."""
        auto, backend = _launched()
        page = _home_page(auto)
        for result in (auto.navigate(page, "https://example.com/"),
                       auto.read(auto.list_tabs()["tabs"][0])):
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["page"]["url"],
                             "https://example.com/")
            self.assertEqual(result["page"]["title"], "Example Domain")

    def test_actions_without_launch_refuse(self):
        auto = ba.BrowserAutomation(backend=_FakeBackend(),
                                    clock=_FakeClock())
        page = {"tab_id": "tab-1", "url": "https://example.com/",
                "title": "Example Domain"}
        for result in (auto.navigate(page, "https://example.com/"),
                       auto.click(page, "text=Play"),
                       auto.fill(page, "label=Search", "hi"),
                       auto.read(page),
                       auto.list_tabs()):
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "not_launched")


if __name__ == "__main__":
    unittest.main()
