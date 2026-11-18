"""Fail-open fallback audit pins (2026-11-05).

The audit itself (FAILOPEN_AUDIT.md at the tree root) found ZERO
clear fail-open cases: the tree's gate posture is consistently
fail-closed. This suite pins the sampled fail-closed postures
through public APIs, so a future change cannot silently flip any
of them open — plus two DOCUMENTED-behaviour pins (labelled as
such in their comments) for postures the audit deliberately left
alone: corrupt-folder-files reading defaults (B7 decision, pinned
by test_corrupt_folder_file_reads_defaults too) and unknown-metered
downloads allowed (documented cost-gate design). Those two pins
assert the DOCUMENTED behaviour, not a fail-closed claim.

INTENTIONAL: every test here names the audit finding it pins
(F1…F26 / D1…D6 from FAILOPEN_AUDIT.md). The ambiguous taint-
consult convention (A1) is deliberately NOT pinned as desired
behaviour — pinning it would bless a fail-open the audit reported
for a parent-wave decision.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from core import confirm_gate
from core import permission_grants as pg
from core import skill_signing
from core.confirm_gate_allowlist import (
    _allowlist_shortcut_applies,
    _load_allowlist,
)
from core.funnel import kill_chain as kc
from core.funnel import money_guard as mg
from core import power_policy


class TestFailopenAuditPins(unittest.TestCase):
    # F1: permission consult failure refuses (never passes).
    def test_permission_consult_failure_refuses(self):
        # INTENTIONAL (audit F1): a consult that raises must refuse
        # the bound tool, never pass it through.
        with mock.patch.object(
                pg, "get_state", side_effect=RuntimeError("store down")):
            allowed, pid = pg.check_tool("mic_check")
        self.assertFalse(allowed)
        self.assertEqual(pid, "microphone")

    # F2: corrupt grants store denies mic/camera (D2 carve-out).
    def test_corrupt_grants_store_denies_mic_camera(self):
        # INTENTIONAL (audit F2): a corrupt store must never
        # silently open the microphone or the camera.
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "permission_grants.json"
            bad.write_text("{not json", encoding="utf-8")
            pg.set_path_override(bad)
            try:
                self.assertEqual(pg.get_state("microphone"), "denied")
                self.assertEqual(pg.get_state("camera"), "denied")
            finally:
                pg.clear_path_override()

    # F3: capture-guard durable consult failure refuses.
    def test_capture_guard_consult_failure_refuses(self):
        # INTENTIONAL (audit F3): a broken store must never
        # silently open the camera (mic-at-guard symmetry covers
        # the microphone the same way).
        from core import capture_guard
        with mock.patch.object(
                pg, "get_state", side_effect=RuntimeError("store down")):
            allowed, _msg = capture_guard.ensure_capture_allowed("camera")
        self.assertFalse(allowed)

    # F5/F6: kill-chain strict reader raises on corrupt state;
    # a missing file still reads as 0 (legitimate fresh default).
    def test_killchain_strict_raises_on_corrupt(self):
        # INTENTIONAL (audit F5): a present-but-corrupt state file
        # must never silently read as rung 0, which would clear
        # every stop. (The A2 suite pins the funnel side too.)
        with tempfile.TemporaryDirectory() as tmp:
            kc.configure(tmp)
            try:
                state = pathlib.Path(tmp) / kc.STATE_FILENAME
                state.write_text("{corrupt", encoding="utf-8")
                with self.assertRaises(kc._KillChainUnavailable):
                    kc._strict_rung()
                with self.assertRaises(kc._KillChainUnavailable):
                    kc.gate_tool("some_tool", False, False)
                state.unlink()
                self.assertEqual(kc._strict_rung(), 0)
            finally:
                kc.reset_configuration()

    # F7: allowlist shortcut undecidable (taint consult raises) → ask.
    def test_allowlist_undecidable_taint_asks(self):
        # INTENTIONAL (audit F7): unknown taint state means ask,
        # never a skipped popup.
        from core import injection
        with mock.patch(
                "core.confirm_gate_allowlist._load_allowlist",
                return_value={"failopen_audit_probe_kind"}), \
                mock.patch.object(
                    injection, "tainted",
                    side_effect=RuntimeError("taint down")):
            self.assertFalse(_allowlist_shortcut_applies(
                "failopen_audit_probe_kind"))

    # F8: allowlist store failure reads empty → ask afresh.
    def test_allowlist_store_failure_asks(self):
        # INTENTIONAL (audit F8): a broken store asks afresh,
        # never skips.
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "allowlist.json"
            bad.write_text("[broken", encoding="utf-8")
            with mock.patch(
                    "core.confirm_gate_allowlist._allowlist_path",
                    return_value=bad):
                self.assertEqual(_load_allowlist(), set())
                self.assertFalse(_allowlist_shortcut_applies("anything"))

    # F9: skill signature/manifest verify failure refuses.
    def test_skill_signature_failure_refuses(self):
        # INTENTIONAL (audit F9): verification failure is refusal,
        # never a pass.
        self.assertFalse(skill_signing.verify_message(
            b"\x00" * 32, b"hello", b"\x00" * 64))
        ok, reasons = skill_signing.verify_signed_manifest(
            "/nonexistent-skill-dir-xyz", b"\x00" * 32)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    # F10/F11: confirm gate denies on broken handler / no handler.
    def test_confirm_broken_handler_denies(self):
        # INTENTIONAL (audit F10): a handler that raises denies,
        # never approves by accident.
        def _raising(_tool, _summary):
            raise RuntimeError("popup exploded")

        confirm_gate.register_handler(_raising)
        try:
            self.assertFalse(confirm_gate.request_confirmation(
                "failopen_audit_probe", {}, timeout_s=5))
        finally:
            confirm_gate.register_handler(None)

    def test_confirm_no_handler_denies(self):
        # INTENTIONAL (audit F11): no handler registered denies.
        confirm_gate.register_handler(None)
        try:
            self.assertFalse(confirm_gate.request_confirmation(
                "failopen_audit_probe", {}, timeout_s=5))
        finally:
            confirm_gate.register_handler(None)

    # F20: money guard refuses missing/approximate amounts.
    def test_money_approximate_amount_refused(self):
        # INTENTIONAL (audit F20): an amount that is not an exact
        # number never moves money.
        refusal = mg.check(
            "send_money",
            {"amount": "about 500", "payee": "Alice", "currency": "INR"},
            instruction_source="user")
        self.assertIsNotNone(refusal)
        refusal = mg.check(
            "send_money",
            {"amount": "500", "payee": "Alice", "currency": "INR"},
            instruction_source="user")
        # Exact amount + resolved payee + stated currency passes
        # the ambiguity half (other rules may still refuse; the
        # pin is that ambiguity itself does not refuse it).
        if refusal is not None:
            self.assertNotIn("amount is missing", refusal.message)

    # D2 (DOCUMENTED behaviour pin, not a fail-closed claim):
    # a corrupt folder file reads defaults.
    def test_corrupt_folder_file_reads_defaults_documented(self):
        # INTENTIONAL (audit D2): B7 documented decision — a
        # missing/corrupt folder file collapses to defaults, while
        # a stored [] (explicitly no folders) stands. This pin
        # guards the DOCUMENTED behaviour against silent change;
        # it does not claim fail-closed.
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "permission_folders.json"
            bad.write_text("{corrupt", encoding="utf-8")
            with mock.patch.object(
                    pg, "_folders_path", return_value=bad):
                self.assertEqual(pg.list_folders(), pg._default_folders())

    # D1 (DOCUMENTED behaviour pin, not a fail-closed claim):
    # unknown metered state allows downloads (cost gate design).
    def test_unknown_metered_allows_downloads_documented(self):
        # INTENTIONAL (audit D1): documented cost-gate design —
        # pausing updates for every host that merely lacks the
        # WinRT binding would be worse than the metered cost.
        # This pin guards the DOCUMENTED behaviour; it does not
        # claim fail-closed.
        policy = power_policy.current_policy(metered=None)
        self.assertFalse(policy["pause_downloads"])
        self.assertTrue(power_policy.downloads_allowed(policy))


if __name__ == "__main__":
    unittest.main()
