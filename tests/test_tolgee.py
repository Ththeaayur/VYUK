"""Suite for the translations pack (R19-6, tolgee/tolgee-platform
inspiration).

Covers: the tool is registered (748 total, 15 properties, required
action, additionalProperties False); the licence marker accepts
EITHER state (PROPOSED or Parent-ruled — the parent flips it after
the worker's run); the census pins (748/382/366/162, params 2002,
grouped 746, closed 747, verified 300); the gate (translate is
confirm-gated, the rest free, unknown fails closed); the project /
key / translation flows (add/list/get/remove, missing, overwrites,
honest refusals); the import parsers (JSON flat + nested, PO with
msgctxt + multiline, XLIFF with namespace + target-language) and the
exporters (JSON / PO / XLIFF round-trips); machine translation with
a fake opener (source attribution via source_hash, re-translate
skips, missing source text refused, no key refused); the store
(reads never create the file, the ledger verifies, tampering breaks
the chain); the bounds; no foreign imports (stdlib + tree config
only).
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest

import core.prompt_tune as pt
import core.translations as tr
from core.funnel.tools_registry import tools as _tools

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)

FAKE_KEY = "fake-key-for-tests-only"


class _FakeOpener:
    """A fake Gemini transport returning one fixed translation."""

    def __init__(self, text="Willkommen daheim"):
        self.body = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": text}]}}]}
            ).encode("utf-8")

    def __call__(self, req, timeout=None):
        body = self.body

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return body

        return _Resp()


class _TolgeeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tolgee_test_")
        self._old_data_dir = tr.DATA_DIR
        tr.DATA_DIR = self.tmp
        self.store = tr._TranslationsStore(
            path=os.path.join(self.tmp, "translations.sqlite3"))
        self._old_key = pt.config.api_key
        self._old_model = pt.config.model_name
        pt.config.api_key = FAKE_KEY
        pt.config.model_name = "fake-model"
        self.opener = _FakeOpener()

    def tearDown(self):
        tr.DATA_DIR = self._old_data_dir
        pt.config.api_key = self._old_key
        pt.config.model_name = self._old_model
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _t(self, **kw):
        kw.setdefault("_store", self.store)
        return tr._handler(**kw)

    def _project(self, name="Demo", base="en"):
        out = self._t(action="project", kind="add", name=name,
                      base_language=base)
        self.assertEqual(out["status"], "ok", out)
        return out["project"]["id"]

    def _key(self, project, name, **kw):
        out = self._t(action="key", kind="add", project=project,
                      name=name, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["key"]["id"]

    def _set(self, project, key, lang, text):
        out = self._t(action="translation", kind="set", project=project,
                      key=key, language=lang, text=text)
        self.assertEqual(out["status"], "ok", out)


class TestRegistry(_TolgeeBase):
    def test_translations_registered(self):
        decls = _tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 758)  # INTENTIONAL — R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len(set(names)), 758)  # INTENTIONAL — R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertIn("translations", names)
        self.assertEqual(names.count("translations"), 1)

    def test_translations_declaration(self):
        decls = _tools.get_declarations()
        t = [d for d in decls if d["name"] == "translations"][0]
        self.assertEqual(len(t["parameters"]["properties"]), 15)
        self.assertEqual(t["parameters"]["required"], ["action"])
        self.assertIs(t["parameters"]["additionalProperties"], False)

    def test_params_total(self):
        decls = _tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2079)  # INTENTIONAL — R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_closed_total(self):
        decls = _tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 757)  # INTENTIONAL — R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755)  # INTENTIONAL — R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing

    def test_licence_marker_either_state(self):
        """The licence test accepts EITHER marker state (the parent
        flips it after the worker's run)."""
        path = os.path.join(_TREE, "core", "translations.py")
        text = open(path, encoding="utf-8").read()
        self.assertTrue(
            "PROPOSED — parent rules" in text
            or "Parent-ruled (licence, 2026-11-12)" in text,
            "the licence marker must be PROPOSED or Parent-ruled")
        self.assertIn("Apache License 2.0", text)
        self.assertIn("Tolgee EE License", text)
        self.assertIn("NON-COMMERCIAL", text)
        self.assertIn("EXCLUDED", text)
        self.assertIn("no ee/ code", text)
        self.assertIn("Nothing is adopted", text)


class TestGate(_TolgeeBase):
    def test_translate_is_confirm_gated(self):
        from core.funnel.tools_gate import _needs_confirmation
        self.assertTrue(_needs_confirmation(
            "translations", {"action": "translate"}))

    def test_local_actions_and_reads_are_free(self):
        from core.funnel.tools_gate import _needs_confirmation
        for action in ("project", "key", "translation", "import",
                       "export", "status", "events"):
            self.assertFalse(_needs_confirmation(
                "translations", {"action": action}), action)

    def test_unknown_action_gates_fail_closed(self):
        from core.funnel.tools_gate import _needs_confirmation
        self.assertTrue(_needs_confirmation(
            "translations", {"action": "explode"}))
        self.assertTrue(_needs_confirmation("translations", {}))

    def test_translations_in_confirm_set(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertIn("translations", _CONFIRM_GATE_TOOLS)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 167)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated

    def test_translations_not_in_free_set(self):
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        self.assertNotIn("translations", _FREE_BY_DESIGN)

    def test_readclass(self):
        from core.funnel.tools_gate_readclass import _is_read_class
        self.assertTrue(_is_read_class(
            "translations", {"action": "status"}))
        self.assertTrue(_is_read_class(
            "translations", {"action": "events"}))
        for action, kind in (("project", "list"), ("project", "get"),
                             ("key", "list"), ("key", "get"),
                             ("translation", "get"),
                             ("translation", "list"),
                             ("translation", "missing")):
            self.assertTrue(_is_read_class(
                "translations", {"action": action, "kind": kind}),
                (action, kind))
        for action, kind in (("project", "add"),
                             ("project", "remove"), ("key", "add"),
                             ("key", "remove"), ("translation", "set"),
                             ("translate", None), ("import", None),
                             ("export", None)):
            self.assertFalse(_is_read_class(
                "translations", {"action": action, "kind": kind}),
                (action, kind))


class TestProjectKey(_TolgeeBase):
    def test_project_add_list_get_remove(self):
        pid = self._project()
        self.assertEqual(pid, "PRJ-0001")
        out = self._t(action="project", kind="list")
        self.assertEqual(len(out["projects"]), 1)
        out = self._t(action="project", kind="get", project="Demo")
        self.assertEqual(out["project"]["name"], "Demo")
        self.assertEqual(out["project"]["base_language"], "en")
        out = self._t(action="project", kind="remove", project=pid)
        self.assertEqual(out["status"], "ok")
        self.assertIn("0 key(s)", out["message"])
        out = self._t(action="project", kind="list")
        self.assertEqual(out["projects"], [])

    def test_project_duplicate_and_unknown_refused(self):
        self._project()
        out = self._t(action="project", kind="add", name="Demo",
                      base_language="en")
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["message"])
        out = self._t(action="project", kind="get", project="Nope")
        self.assertEqual(out["status"], "error")
        self.assertIn("no project", out["message"])

    def test_project_bad_base_language_refused(self):
        out = self._t(action="project", kind="add", name="Bad",
                      base_language="english")
        self.assertEqual(out["status"], "error")
        self.assertIn("language code", out["message"])

    def test_key_add_list_get_remove(self):
        pid = self._project()
        kid = self._key(pid, "home.title", description="Home title",
                        tags="home,nav")
        self.assertEqual(kid, "KEY-0001")
        out = self._t(action="key", kind="list", project=pid)
        self.assertEqual(len(out["keys"]), 1)
        self.assertEqual(out["keys"][0]["name"], "home.title")
        out = self._t(action="key", kind="get", project=pid,
                      key="home.title")
        self.assertEqual(out["key"]["description"], "Home title")
        self.assertEqual(out["key"]["tags"], "home,nav")
        self.assertEqual(out["key"]["translations"], {})
        out = self._t(action="key", kind="remove", project=pid,
                      key="home.title")
        self.assertEqual(out["status"], "ok")
        self.assertIn("0 translation(s)", out["message"])

    def test_key_duplicate_bad_name_and_unknown_refused(self):
        pid = self._project()
        self._key(pid, "home.title")
        out = self._t(action="key", kind="add", project=pid,
                      name="home.title")
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["message"])
        out = self._t(action="key", kind="add", project=pid,
                      name="bad key")
        self.assertEqual(out["status"], "error")
        self.assertIn("spaces", out["message"])
        out = self._t(action="key", kind="get", project=pid,
                      key="nope")
        self.assertEqual(out["status"], "error")
        self.assertIn("no key", out["message"])

    def test_project_remove_names_counts(self):
        pid = self._project()
        self._key(pid, "a")
        self._key(pid, "b")
        self._set(pid, "a", "en", "A")
        self._set(pid, "b", "en", "B")
        out = self._t(action="project", kind="remove", project=pid)
        self.assertIn("2 key(s)", out["message"])
        self.assertIn("2 translation(s)", out["message"])


class TestTranslation(_TolgeeBase):
    def test_set_get_list_missing(self):
        pid = self._project()
        self._key(pid, "home.title")
        self._key(pid, "home.sub")
        self._set(pid, "home.title", "en", "Welcome")
        out = self._t(action="translation", kind="get", project=pid,
                      key="home.title", language="en")
        self.assertEqual(out["translation"]["text"], "Welcome")
        self.assertEqual(out["translation"]["source"], "human")
        out = self._t(action="translation", kind="missing",
                      project=pid, language="hi")
        self.assertEqual(out["missing"], ["home.sub", "home.title"])
        out = self._t(action="translation", kind="list", project=pid,
                      language="en")
        self.assertEqual(len(out["translations"]), 1)

    def test_set_overwrites(self):
        pid = self._project()
        self._key(pid, "a")
        self._set(pid, "a", "en", "One")
        self._set(pid, "a", "en", "Two")
        out = self._t(action="translation", kind="get", project=pid,
                      key="a", language="en")
        self.assertEqual(out["translation"]["text"], "Two")

    def test_get_missing_translation_refused_with_names(self):
        pid = self._project()
        self._key(pid, "a")
        out = self._t(action="translation", kind="get", project=pid,
                      key="a", language="hi")
        self.assertEqual(out["status"], "error")
        self.assertIn("no hi translation", out["message"])

    def test_bad_language_and_long_text_refused(self):
        pid = self._project()
        self._key(pid, "a")
        out = self._t(action="translation", kind="set", project=pid,
                      key="a", language="english", text="x")
        self.assertEqual(out["status"], "error")
        out = self._t(action="translation", kind="set", project=pid,
                      key="a", language="en", text="x" * 5001)
        self.assertEqual(out["status"], "error")
        self.assertIn("5000", out["message"])

    def test_unknown_action_and_kind_refused(self):
        out = self._t(action="explode")
        self.assertEqual(out["status"], "error")
        pid = self._project()
        out = self._t(action="project", kind="explode", project=pid)
        self.assertEqual(out["status"], "error")


class TestParsers(_TolgeeBase):
    def test_json_flat_and_nested(self):
        pairs = tr._parse_json_bytes(
            b'{"a": "A", "home": {"title": "T", "sub": {"x": "X"}}}')
        self.assertEqual(pairs, {"a": "A", "home.title": "T",
                                 "home.sub.x": "X"})

    def test_json_bad_and_empty_refused(self):
        for raw in (b"{not json", b"[]", b"{}", b'{"a": 1}'):
            with self.assertRaises(tr._Refusal):
                tr._parse_json_bytes(raw)

    def test_po_basic_multiline_and_ctxt(self):
        po = (b'msgid ""\nmsgstr ""\n"Project-Id-Version: x\\n"\n\n'
              b'msgid "hello"\nmsgstr "bonjour"\n\n'
              b'msgid "long"\nmsgstr "first "\n"second"\n\n'
              b'msgctxt "menu"\nmsgid "file"\nmsgstr "fichier"\n')
        pairs = tr._parse_po_bytes(po)
        self.assertEqual(pairs["hello"], "bonjour")
        self.assertEqual(pairs["long"], "first second")
        self.assertEqual(pairs["menu|file"], "fichier")
        self.assertNotIn("", pairs)

    def test_po_plural_refused(self):
        with self.assertRaises(tr._Refusal):
            tr._parse_po_bytes(b'msgid "one"\nmsgid_plural "many"\n'
                               b'msgstr[0] "un"\n')

    def test_xliff_namespaced_with_target_language(self):
        xlf = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
               b'<xliff version="1.2" '
               b'xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
               b'  <file source-language="en" target-language="fr"'
               b' datatype="plaintext">\n    <body>\n'
               b'      <trans-unit id="hello">\n'
               b'        <source>Hello</source>\n'
               b'        <target>Bonjour</target>\n'
               b'      </trans-unit>\n'
               b'      <trans-unit id="untranslated">\n'
               b'        <source>Only source</source>\n'
               b'      </trans-unit>\n'
               b'    </body>\n  </file>\n</xliff>\n')
        pairs, lang = tr._parse_xliff_bytes(xlf)
        self.assertEqual(pairs, {"hello": "Bonjour"})
        self.assertEqual(lang, "fr")

    def test_xliff_bad_root_and_no_units_refused(self):
        with self.assertRaises(tr._Refusal):
            tr._parse_xliff_bytes(b"<html></html>")
        with self.assertRaises(tr._Refusal):
            tr._parse_xliff_bytes(
                b'<xliff version="1.2"><file></file></xliff>')

    def test_export_writers(self):
        pairs = {"b": "Bee", "a": "Ay"}
        data = tr._export_json(pairs)
        self.assertEqual(json.loads(data), pairs)
        po = tr._export_po(pairs, "Demo").decode("utf-8")
        self.assertIn('msgid "a"\nmsgstr "Ay"', po)
        self.assertIn('msgid "b"\nmsgstr "Bee"', po)
        xlf = tr._export_xliff(pairs, {"a": "A", "b": "B"}, "en",
                               "fr").decode("utf-8")
        self.assertIn('<trans-unit id="a">', xlf)
        self.assertIn("<source>A</source>", xlf)
        self.assertIn("<target>Ay</target>", xlf)
        self.assertIn('target-language="fr"', xlf)


class TestImportExport(_TolgeeBase):
    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data if isinstance(data, bytes)
                     else data.encode("utf-8"))
        return path

    def test_json_import_and_counts(self):
        pid = self._project()
        path = self._write("hi.json", json.dumps(
            {"home": {"title": "स्वागत"}, "bye": "अलविदा"}))
        out = self._t(action="import", project=pid, language="hi",
                      file=path)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["created"], 2)
        self.assertEqual(out["updated"], 2)
        self.assertEqual(out["skipped"], 0)
        out = self._t(action="import", project=pid, language="hi",
                      file=path)
        self.assertEqual(out["created"], 0)
        self.assertEqual(out["skipped"], 2)
        out = self._t(action="translation", kind="list", project=pid,
                      language="hi")
        self.assertEqual(len(out["translations"]), 2)
        self.assertEqual(out["translations"][0]["source"], "import")

    def test_po_round_trip(self):
        pid = self._project()
        self._key(pid, "hello")
        self._set(pid, "hello", "en", "Hello")
        self._set(pid, "hello", "fr", "Bonjour")
        path = os.path.join(self.tmp, "fr.po")
        out = self._t(action="export", project=pid, language="fr",
                      format="po", file=path)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["keys"], 1)
        pid2 = self._project(name="Second")
        out = self._t(action="import", project=pid2, language="fr",
                      file=path)
        self.assertEqual(out["created"], 1)
        out = self._t(action="translation", kind="get", project=pid2,
                      key="hello", language="fr")
        self.assertEqual(out["translation"]["text"], "Bonjour")

    def test_xliff_round_trip_without_language_arg(self):
        pid = self._project()
        self._key(pid, "hello")
        self._set(pid, "hello", "en", "Hello")
        self._set(pid, "hello", "de", "Hallo")
        path = os.path.join(self.tmp, "de.xlf")
        out = self._t(action="export", project=pid, language="de",
                      format="xliff", file=path)
        self.assertEqual(out["status"], "ok", out)
        pid2 = self._project(name="Second")
        out = self._t(action="import", project=pid2, file=path)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["language"], "de")
        out = self._t(action="translation", kind="get", project=pid2,
                      key="hello", language="de")
        self.assertEqual(out["translation"]["text"], "Hallo")

    def test_json_export_default_path(self):
        pid = self._project()
        self._key(pid, "hello")
        self._set(pid, "hello", "en", "Hello")
        out = self._t(action="export", project=pid, language="en",
                      format="json")
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(os.path.exists(out["file"]))
        self.assertEqual(json.loads(open(out["file"],
                                        encoding="utf-8").read()),
                         {"hello": "Hello"})

    def test_import_bad_extension_too_big_and_unknown_refused(self):
        pid = self._project()
        bad = self._write("notes.txt", "hello")
        out = self._t(action="import", project=pid, language="hi",
                      file=bad)
        self.assertEqual(out["status"], "error")
        self.assertIn(".json", out["message"])
        big = self._write("big.json", b"x" * (2 * 1024 * 1024 + 1))
        out = self._t(action="import", project=pid, language="hi",
                      file=big)
        self.assertEqual(out["status"], "error")
        self.assertIn("2 MB", out["message"])
        out = self._t(action="import", project=pid, language="hi",
                      file=os.path.join(self.tmp, "missing.json"))
        self.assertEqual(out["status"], "error")

    def test_export_bad_format_refused(self):
        pid = self._project()
        out = self._t(action="export", project=pid, language="en",
                      format="yaml")
        self.assertEqual(out["status"], "error")
        self.assertIn("json, po, xliff", out["message"])


class TestTranslate(_TolgeeBase):
    def test_translate_one_key_with_source_hash(self):
        pid = self._project()
        self._key(pid, "home.title")
        self._set(pid, "home.title", "en", "Welcome home")
        out = self._t(action="translate", project=pid,
                      key="home.title", target_languages=["de"],
                      _opener=self.opener)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["translated"], {"de": ["home.title"]})
        self.assertEqual(out["failed"], [])
        out = self._t(action="translation", kind="get", project=pid,
                      key="home.title", language="de")
        self.assertEqual(out["translation"]["text"],
                         "Willkommen daheim")
        self.assertEqual(out["translation"]["source"], "machine")
        import hashlib
        self.assertEqual(
            out["translation"]["source_hash"],
            hashlib.sha256(b"Welcome home").hexdigest())

    def test_retranslate_skips_already_translated(self):
        pid = self._project()
        self._key(pid, "home.title")
        self._set(pid, "home.title", "en", "Welcome home")
        self._t(action="translate", project=pid, key="home.title",
                target_languages=["de"], _opener=self.opener)
        out = self._t(action="translate", project=pid,
                      key="home.title", target_languages=["de"],
                      _opener=self.opener)
        self.assertEqual(out["translated"], {"de": []})
        self.assertEqual(out["skipped"], ["home.title:de"])

    def test_translate_all_missing_keys(self):
        pid = self._project()
        self._key(pid, "a")
        self._key(pid, "b")
        self._set(pid, "a", "en", "A")
        self._set(pid, "b", "en", "B")
        self._set(pid, "b", "de", "B-de")
        out = self._t(action="translate", project=pid,
                      target_languages=["de"], _opener=self.opener)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["translated"], {"de": ["a"]})
        self.assertEqual(out["skipped"], [])

    def test_translate_without_source_text_fails_honestly(self):
        pid = self._project()
        self._key(pid, "a")
        out = self._t(action="translate", project=pid, key="a",
                      target_languages=["de"], _opener=self.opener)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["translated"], {"de": []})
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("no en text", out["failed"][0])

    def test_translate_without_key_refuses(self):
        pt.config.api_key = ""
        pid = self._project()
        self._key(pid, "a")
        self._set(pid, "a", "en", "A")
        out = self._t(action="translate", project=pid, key="a",
                      target_languages=["de"], _opener=self.opener)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("no Gemini key", out["failed"][0])

    def test_translate_source_language_as_target_refused(self):
        pid = self._project()
        out = self._t(action="translate", project=pid,
                      target_languages=["en"], _opener=self.opener)
        self.assertEqual(out["status"], "error")
        self.assertIn("already translated", out["message"])


class TestStore(_TolgeeBase):
    def test_reads_never_create_the_store(self):
        out = self._t(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "translations.sqlite3")))
        out = self._t(action="events", kind="verify")
        self.assertTrue(out["chain_ok"])

    def test_status_counts_and_events_verify(self):
        pid = self._project()
        self._key(pid, "a")
        self._set(pid, "a", "en", "A")
        out = self._t(action="status")
        self.assertEqual(out["projects"], 1)
        self.assertEqual(out["keys"], 1)
        self.assertEqual(out["translations"], 1)
        self.assertEqual(out["languages"], 1)
        self.assertTrue(out["chain_ok"])
        out = self._t(action="events", kind="list")
        self.assertGreaterEqual(len(out["events"]), 3)
        out = self._t(action="events", kind="verify")
        self.assertTrue(out["chain_ok"])

    def test_tampered_ledger_fails_verification(self):
        pid = self._project()
        self._key(pid, "a")
        conn = sqlite3.connect(
            os.path.join(self.tmp, "translations.sqlite3"))
        conn.execute("UPDATE events SET hash = ? WHERE id = 1",
                     ("0" * 64,))
        conn.commit()
        conn.close()
        out = self._t(action="events", kind="verify")
        self.assertEqual(out["status"], "error")
        self.assertIn("BROKEN", out["message"])

    def test_limit_bounds(self):
        pid = self._project()
        out = self._t(action="key", kind="list", project=pid, limit=0)
        self.assertEqual(out["status"], "error")
        out = self._t(action="key", kind="list", project=pid,
                      limit=101)
        self.assertEqual(out["status"], "error")

    def test_no_foreign_imports(self):
        """The module imports only stdlib + the tree's config."""
        import ast
        path = os.path.join(_TREE, "core", "translations.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0],
                        ("tolgee", "requests", "polib", "lxml"),
                        alias.name)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    (node.module or "").split(".")[0],
                    ("tolgee", "requests", "polib", "lxml"),
                    node.module)


if __name__ == "__main__":
    unittest.main()
