"""Audit fix Gaps batch — fifth-pass C-01 + C-02, master audit
P1-12 + P2-8, plugin isolation model.

C-01: crash filenames are collision-proof (O_EXCL + random
suffix) — two dumps forced into the same second both exist
with their own content intact.
C-02: crash payloads are allowlisted + recursively sanitized
(top level, nested mappings, error text) and preview == sent.
P1-12: uninstall registry strings are parsed into argv, the
executable is validated, and the exact target is displayed —
no shell ever interprets a registry string.
Plugin isolation: every plugin-registered tool routes through
the execute() funnel (confirm gate, validation) with no
bypass; the OS-level non-containment is stated, not hidden.

Stdlib unittest only. No test executes a real tool against the
live system, launches a real uninstaller, or sends a real
crash report: funnel executes use a fresh registry with the
budget brake patched to pass (the A4/A5 lesson — the budget
itself is covered by the A2 suite, not here), and uninstall
coverage is on the pure parse/validate/preview functions
(uninstall_app's platform guard answers before any launch
on non-Windows anyway).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import confirm_gate
from core import distribution_hardening as dist
from core import plugin_isolation as plugin_iso
from core import skills_plus
from core.funnel import tools_gate as gate_mod  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel import tools_registry as registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry_exec_dispatch as dispatch_mod  # INTENTIONAL (tools_registry batch 2): limits stage binding for the dual-patch below

LONG_TOKEN = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
SECRET_URL = "https://alice:hunter2@example.invalid/x"


class CrashFilenameTests(unittest.TestCase):
    def test_same_second_dumps_do_not_overwrite(self):
        """C-01: two dumps in the same second get distinct files
        with their own content intact (the old name + open "w"
        overwrote silently)."""
        reports = dist.CrashReports(tempfile.mkdtemp())
        with mock.patch.object(dist.time, "strftime",
                               return_value="20261030T041500Z"):
            first = reports.write_dump(error="first boom", context={})
            second = reports.write_dump(error="second boom", context={})
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(first))
        self.assertTrue(os.path.isfile(second))
        with open(first, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["error"], "first boom")
        with open(second, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["error"], "second boom")
        self.assertEqual(len(reports.list_dumps()), 2)

    def test_list_dumps_still_finds_new_and_old_names(self):
        """list_dumps matches both the new suffixed names and a
        pre-fix bare stamp name left on disk."""
        directory = tempfile.mkdtemp()
        old = os.path.join(directory, "crash-20260101T000000Z.json")
        Path(old).write_text("{}", encoding="utf-8")
        reports = dist.CrashReports(directory)
        new = reports.write_dump(error="boom", context={})
        self.assertIn(old, reports.list_dumps())
        self.assertIn(new, reports.list_dumps())


class CrashRedactionTests(unittest.TestCase):
    def _probe_payload(self):
        return {
            "error": f"auth failed token: {LONG_TOKEN} for {SECRET_URL}",
            "context": {
                "api_key": "top-secret-value",
                "username": "alice",
                "nested": {
                    "password": "nested-secret",
                    "count": 3,
                    "deep": [{"token": "deep-secret"}],
                },
                "note": f"see api_key={LONG_TOKEN} in the log",
                "plain_list": ["one", "two"],
            },
        }

    def test_secrets_gone_at_every_level(self):
        """C-02: secrets at top level, nested, inside lists, and
        inside the error string are all gone from the written
        dump; benign values survive."""
        reports = dist.CrashReports(tempfile.mkdtemp())
        probe = self._probe_payload()
        path = reports.write_dump(error=probe["error"],
                                  context=probe["context"])
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        blob = json.dumps(stored)
        for secret in ("top-secret-value", "nested-secret",
                       "deep-secret", LONG_TOKEN, "hunter2"):
            self.assertNotIn(secret, blob, secret)
        self.assertEqual(stored["context"]["username"], "alice")
        self.assertEqual(stored["context"]["nested"]["count"], 3)
        self.assertEqual(stored["context"]["plain_list"], ["one", "two"])
        self.assertEqual(stored["context"]["api_key"], "[redacted]")
        self.assertEqual(stored["context"]["nested"]["password"],
                         "[redacted]")

    def test_preview_equals_sent_exactly(self):
        """C-02: preview_dump returns exactly the payload send()
        transmits — the user sees the sanitized report before
        any send, and nothing else is sent."""
        reports = dist.CrashReports(tempfile.mkdtemp())
        probe = self._probe_payload()
        path = reports.write_dump(error=probe["error"],
                                  context=probe["context"])
        preview = reports.preview_dump(path)
        sent = []
        result = reports.send(path, explicit_send=True,
                              transport=sent.append)
        self.assertTrue(result["sent"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], preview)

    def test_pre_fix_dump_sanitized_at_send_time(self):
        """C-02 defense in depth: a dump written BEFORE this
        batch (raw secrets, old bare-stamp name) is sanitized
        when previewed/sent, not shipped verbatim."""
        directory = tempfile.mkdtemp()
        old = os.path.join(directory, "crash-20260101T000000Z.json")
        raw = {"error": f"boom token: {LONG_TOKEN}",
               "context": {"api_key": "old-secret"}, "at": "x"}
        Path(old).write_text(json.dumps(raw), encoding="utf-8")
        reports = dist.CrashReports(directory)
        preview = reports.preview_dump(old)
        blob = json.dumps(preview)
        self.assertNotIn(LONG_TOKEN, blob)
        self.assertNotIn("old-secret", blob)
        sent = []
        reports.send(old, explicit_send=True, transport=sent.append)
        self.assertEqual(sent[0], preview)

    def test_send_still_requires_explicit_flag_and_transport(self):
        """The existing gate is unchanged: no explicit flag, or
        no transport, sends nothing."""
        reports = dist.CrashReports(tempfile.mkdtemp())
        path = reports.write_dump(error="boom", context={})
        self.assertFalse(reports.send(
            path, explicit_send=False,
            transport=lambda p: None)["sent"])
        self.assertFalse(reports.send(path, explicit_send=True)["sent"])


class UninstallArgvTests(unittest.TestCase):
    def test_metachar_strings_refused(self):
        """P1-12: registry strings carrying shell metacharacters
        outside quotes are refused, never executed through a
        shell — the old `... & malicious` shape included."""
        base = "C:\\App\\uninstall.exe /S"
        for suffix in (" & del C:\\*", " | malicious.exe",
                       "; rm -rf /", "`evil`", "$(evil)",
                       " > out.txt", " ^ evil"):
            preview = skills_plus.preview_uninstall_target(base + suffix)
            self.assertFalse(preview["ok"], suffix)

    def test_quoted_path_with_ampersand_parses(self):
        """P1-12: a quoted path containing & is DATA, not a
        metacharacter — it parses as one token and validates."""
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "Rock & Roll")
            os.makedirs(sub)
            exe = os.path.join(sub, "uninstall.exe")
            Path(exe).write_text("fake", encoding="utf-8")
            preview = skills_plus.preview_uninstall_target(
                f'"{exe}" /S')
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["argv"][0], exe)
            self.assertEqual(preview["argv"][1], "/S")

    def test_msiexec_and_plain_path_validate(self):
        """P1-12: the canonical msiexec form and a real path
        uninstaller both parse and display exactly."""
        preview = skills_plus.preview_uninstall_target(
            "msiexec.exe /X{12345678-1234-1234-1234-123456789012}")
        self.assertTrue(preview["ok"], preview)
        self.assertIn("msiexec", preview["display"])
        self.assertIn("/X{12345678-1234-1234-1234-123456789012}",
                      preview["display"])
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "uninstall.exe")
            Path(exe).write_text("fake", encoding="utf-8")
            preview = skills_plus.preview_uninstall_target(
                f'"{exe}" /quiet')
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["argv"], [exe, "/quiet"])

    def test_shell_executables_and_missing_targets_refused(self):
        """P1-12: a registry string pointing at a shell /
        interpreter, or at an executable that does not exist,
        is refused — it is never the expected uninstaller."""
        for cmd in ("cmd.exe /c echo hi",
                    "powershell.exe -Command Get-ChildItem",
                    "C:\\Nope\\uninstall.exe /S",
                    "/nonexistent/uninstall.exe /S"):
            preview = skills_plus.preview_uninstall_target(cmd)
            self.assertFalse(preview["ok"], cmd)

    def test_uninstall_launch_uses_argv_without_shell(self):
        """P1-12: the launch site itself passes an argv list
        with shell=False — statically pinned, since the launch
        cannot run on non-Windows."""
        import inspect
        source = inspect.getsource(skills_plus.uninstall_app)
        self.assertIn("shell=False", source)
        self.assertNotIn("shell=True", source)
        self.assertIn("preview_uninstall_target", source)


class PluginIsolationTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)
        # INTENTIONAL (tools_registry batch 2, B16/B47 pattern):
        # the limits stage reads _budget_check from the dispatch
        # mixin module, not only from the facade -- patch both
        # bindings with the same fake. Same fake, same
        # assertions; no assertion weakened. (This suite passed
        # with the facade-only patch because its probes never
        # trip the real budget; the dual-patch restores the
        # patch's meaning rather than leaving it dead.)
        self._budget_patcher = mock.patch.object(
            registry_mod, "_budget_check",
            lambda name, is_read: None)
        self._budget_patcher2 = mock.patch.object(
            dispatch_mod, "_budget_check",
            lambda name, is_read: None)
        self._budget_patcher.start()
        self._budget_patcher2.start()
        self.calls = []
        self.reg = registry_mod.ToolRegistry()
        self._tool_name = "gaps_probe_plugin_tool"

        def handler(target=""):
            self.calls.append(target)
            return "probe-ran"

        self.reg.register(
            name=self._tool_name,
            description="Gaps probe plugin tool (test-only).",
            parameters={"type": "object",
                        "properties": {
                            "target": {"type": "string"}},
                        "required": ["target"]},
            handler=handler)
        self._prev_gate = gate_mod._PLUGIN_GATE_CLASS.get(self._tool_name)
        gate_mod._PLUGIN_GATE_CLASS[self._tool_name] = "gated"

    def tearDown(self):
        self._budget_patcher2.stop()
        self._budget_patcher.stop()
        confirm_gate.register_handler(None)
        if self._prev_gate is None:
            gate_mod._PLUGIN_GATE_CLASS.pop(self._tool_name, None)
        else:
            gate_mod._PLUGIN_GATE_CLASS[self._tool_name] = self._prev_gate

    def test_gated_plugin_tool_hits_confirm_gate(self):
        """Isolation (a): a plugin tool attempting a gated action
        still hits the confirm gate — with no human handler it
        is refused and the handler NEVER runs."""
        result = self.reg.execute(self._tool_name, {"target": "x"})
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "not_confirmed", result)
        self.assertEqual(self.calls, [])

    def test_approving_handler_runs_plugin_tool(self):
        """Isolation (a), gate-not-block: with a human approval
        the same plugin tool runs — the gate asks, it does not
        silently veto."""
        confirm_gate.register_handler(lambda name, summary: True)
        result = self.reg.execute(self._tool_name, {"target": "x"})
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(len(self.calls), 1)

    def test_plugin_tool_args_validated_before_handler(self):
        """Isolation (a): schema validation runs before the
        plugin handler, exactly as for built-ins — bad args are
        rejected and the handler never runs."""
        confirm_gate.register_handler(lambda name, summary: True)
        result = self.reg.execute(self._tool_name, {"target": 123})
        self.assertEqual(result["kind"], "validation", result)
        self.assertNotEqual(result["status"], "success", result)
        self.assertEqual(self.calls, [])

    def test_funnel_routing_check(self):
        """Isolation (b): the routing invariant — a plugin-gated
        name must live in the shared registry table (dispatch
        via execute() only); a missing name is a problem. The
        scoped check covers this test's fresh registry; the
        singleton check covers production (the two shipped
        plugins, loaded at import, live in the singleton's
        table under the global gate-class map)."""
        scoped = {self._tool_name: "gated"}
        check = plugin_iso.funnel_routing_check(self.reg._tools, scoped)
        self.assertTrue(check["ok"], check)
        bad = plugin_iso.funnel_routing_check(
            self.reg._tools, {"no_such_plugin_tool": "gated"})
        self.assertFalse(bad["ok"])
        self.assertTrue(any("no_such_plugin_tool" in p
                            for p in bad["problems"]))
        from core.funnel.tools_registry import tools as singleton_tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        prod_map = {name: cls for name, cls
                    in gate_mod._PLUGIN_GATE_CLASS.items()
                    if name != self._tool_name}
        prod = plugin_iso.funnel_routing_check(
            singleton_tools._tools, prod_map)
        self.assertTrue(prod["ok"], prod)

    def test_limits_are_stated_not_hidden(self):
        """Isolation honesty: the module states plainly that
        in-process plugins are NOT OS-contained, and that full
        process isolation is future work — pinned so the note
        cannot be silently edited away."""
        self.assertIn("not contained", plugin_iso.ISOLATION_LIMITS)
        self.assertIn("OS level", plugin_iso.ISOLATION_LIMITS)
        self.assertIn("future work", plugin_iso.ISOLATION_LIMITS)
        model = plugin_iso.describe_model()
        self.assertEqual(model["not_provided"], plugin_iso.ISOLATION_LIMITS)
        self.assertIn("out-of-process", model["future_work"])


if __name__ == "__main__":
    unittest.main()
