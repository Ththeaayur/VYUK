"""SOCIAL Phase 1: WhatsApp send via wa.me + Voice Access.

Before this batch VYUK could only DRAFT messages (comms_tools,
"NOT a send pipeline") and open WhatsApp via the `whatsapp:` URI
(open only, no recipient, no text) — there was no send path at
all. `social_send` (core/social_send.py) is the first one, and it
is deliberately not a send pipeline in the old sense: VYUK
prepares everything (resolves the recipient from contact cards,
never guesses; drafts the exact text), opens the official wa.me
Click to Chat link, verifies the conversation header and the
pre-filled text before arming, and the OWNER sends by voice —
he says "press Enter" and Windows Voice Access (the OS's own
accessibility layer) presses Enter. VYUK types nothing and
presses nothing. `done` is reported only when the message is
seen in the thread after the send; anything less is
`unverified`, never "sent" on a guess. Receipts store who, when,
WhatsApp and the outcome — never the message text. Stdlib only,
no network, no credentials — every screen, opener and contact
here is a fake that records its calls. The one real-chain test
writes a real receipt to the shared hash chain; the batch
restores data/ afterwards, as every batch does.
"""

import json
import pathlib
import tempfile
import unittest

from core import record_family as rf
from core import social_send as ss
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_registry import tools

TREE = pathlib.Path(__file__).resolve().parent.parent

CONTACTS = [("Mum", "phone: +91 98765 43210"),
            ("Aunt Rosa", "phone: +44 20 7946 0958"),
            ("NoNumber", "email: nonumber@example.com"),
            ("TwoNumbers", "phone: +91 11111 11111"),
            ("TwoNumbers", "phone: +91 22222 22222")]


def _loader():
    return list(CONTACTS)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ss_test_"))
        self._orig_state_path = ss.state_path
        ss.state_path = lambda: str(self.tmp / "state.json")
        self.captured = []
        self._orig_append = rf.append_event
        self._orig_read = rf.read_events
        rf.append_event = self._fake_append
        rf.read_events = self._fake_read

    def tearDown(self):
        rf.append_event = self._orig_append
        rf.read_events = self._orig_read
        ss.state_path = self._orig_state_path

    def _fake_append(self, kind, data):
        self.captured.append((kind, dict(data)))
        return "evt-%d" % len(self.captured)

    def _fake_read(self, kind, limit=50):
        # The real chain's envelope, newest-first: {"data": {...}}.
        found = [dict(d) for k, d in self.captured if k == kind]
        return [{"data": d, "id": "evt-%d" % (i + 1), "hash": "fake"}
                for i, d in enumerate(reversed(found))][:limit]

    def _prepare(self, text="hello Mum, test", recipient="Mum"):
        out = ss.social_send("prepare", recipient=recipient, text=text,
                             _contacts_loader=_loader)
        self.assertTrue(out.get("ok"), str(out))
        return out["draft_id"]

    def _open(self, draft_id, text="hello Mum, test",
              screen="Mum +91 98765 43210 hello Mum, test", va=3):
        return ss.social_send(
            "open", text=text, draft_id=draft_id,
            _reader=lambda: screen,
            _opener=lambda url: True,
            _va_reader=lambda _k, _v: va)

    def _verify(self, draft_id, text="hello Mum, test",
                screen="Mum hello Mum, test"):
        return ss.social_send("verify", text=text, draft_id=draft_id,
                              _reader=lambda: screen)


class LinkTests(_Base):
    def test_wa_link_is_digits_plus_url_encoded_text(self):
        self.assertEqual(
            ss.build_wa_link("919876543210", "hello there"),
            "https://wa.me/919876543210?text=hello%20there")

    def test_normalize_strips_plus_and_separators(self):
        self.assertEqual(ss.normalize_number("+91 98765-43210"),
                         "919876543210")
        self.assertEqual(ss.normalize_number("(020) 7946 0958"),
                         "02079460958")

    def test_normalize_refuses_non_numbers(self):
        self.assertIsNone(ss.normalize_number("call me"))
        self.assertIsNone(ss.normalize_number("123"))
        self.assertIsNone(ss.normalize_number(""))
        self.assertIsNone(ss.normalize_number(None))


class ResolveTests(_Base):
    def test_a_number_resolves_directly(self):
        self.assertEqual(ss.resolve_recipient("+91 98765 43210"),
                         ("+91 98765 43210", "919876543210"))

    def test_an_exact_name_resolves(self):
        self.assertEqual(ss.resolve_recipient("Mum", _loader),
                         ("Mum", "919876543210"))

    def test_name_match_is_case_insensitive(self):
        self.assertEqual(ss.resolve_recipient("mum", _loader),
                         ("Mum", "919876543210"))

    def test_a_missing_name_refuses(self):
        name, reason = ss.resolve_recipient("Nobody", _loader)
        self.assertIsNone(name)
        self.assertIn("no contact", reason)

    def test_a_name_with_no_number_refuses(self):
        name, reason = ss.resolve_recipient("NoNumber", _loader)
        self.assertIsNone(name)
        self.assertIn("no phone number", reason)

    def test_a_name_with_two_numbers_refuses(self):
        name, reason = ss.resolve_recipient("TwoNumbers", _loader)
        self.assertIsNone(name)
        self.assertIn("more than one phone number", reason)

    def test_an_empty_recipient_refuses(self):
        name, _reason = ss.resolve_recipient("", _loader)
        self.assertIsNone(name)


class GuardTests(_Base):
    def test_match_on_name_and_text(self):
        ok, reason = ss.guard_check(
            "Mum +91 98765 43210 hello Mum, test",
            "Mum", "919876543210", "hello Mum, test")
        self.assertTrue(ok)
        self.assertEqual(reason, "match")

    def test_match_on_number_alone(self):
        # An unsaved number: the header shows the bare number.
        ok, _reason = ss.guard_check(
            "+91 98765 43210 hello there", "",
            "919876543210", "hello there")
        self.assertTrue(ok)

    def test_match_on_spaced_number(self):
        ok, _reason = ss.guard_check(
            "+91 98765 43210 hello there", "Mum",
            "919876543210", "hello there")
        self.assertTrue(ok)

    def test_wrong_conversation_refuses(self):
        ok, reason = ss.guard_check(
            "Alice +91 11111 11111 hello Mum, test",
            "Mum", "919876543210", "hello Mum, test")
        self.assertFalse(ok)
        self.assertEqual(reason, "header_mismatch")

    def test_message_mentioning_the_name_does_not_satisfy_header(self):
        # The trap the prover caught: "hello Mum, test" contains
        # "Mum", so the name must be checked with the message
        # itself removed from the read-back.
        ok, reason = ss.guard_check(
            "Alice hello Mum, test",
            "Mum", "919876543210", "hello Mum, test")
        self.assertFalse(ok)
        self.assertEqual(reason, "header_mismatch")

    def test_wrong_prefilled_text_refuses(self):
        ok, reason = ss.guard_check(
            "Mum +91 98765 43210 a different message",
            "Mum", "919876543210", "hello Mum, test")
        self.assertFalse(ok)
        self.assertEqual(reason, "box_mismatch")

    def test_unreadable_screen_refuses(self):
        for blank in (None, "", "   "):
            ok, reason = ss.guard_check(
                blank, "Mum", "919876543210", "hello Mum, test")
            self.assertFalse(ok)
            self.assertEqual(reason, "could_not_read_screen")

    def test_a_short_name_does_not_match_a_longer_word(self):
        ok, _reason = ss.guard_check(
            "Alice hello there", "Al", "919876543210", "hello there")
        self.assertFalse(ok)

    def test_thread_contains_is_normalised(self):
        self.assertTrue(ss.thread_contains(
            "Mum  hello   Mum, test", "hello Mum, test"))
        self.assertFalse(ss.thread_contains(None, "hello Mum, test"))
        self.assertFalse(ss.thread_contains(
            "Mum something else", "hello Mum, test"))


class PrepareTests(_Base):
    def test_prepare_returns_the_draft_for_approval(self):
        out = ss.social_send("prepare", recipient="Mum",
                             text="hello Mum, test",
                             _contacts_loader=_loader)
        self.assertTrue(out.get("ok"), str(out))
        self.assertTrue(out["draft_id"].startswith("ss-"))
        self.assertEqual(out["recipient"], "Mum")
        self.assertEqual(out["number"], "919876543210")
        self.assertEqual(
            out["link"],
            "https://wa.me/919876543210?text=hello%20Mum%2C%20test")
        self.assertEqual(out["text"], "hello Mum, test")

    def test_prepare_stores_no_message_text(self):
        self._prepare()
        raw = (self.tmp / "state.json").read_text(encoding="utf-8")
        self.assertNotIn("hello Mum, test", raw)
        self.assertIn(ss.text_sha("hello Mum, test"), raw)

    def test_prepare_refuses_an_empty_message(self):
        out = ss.social_send("prepare", recipient="Mum", text=" ",
                             _contacts_loader=_loader)
        self.assertFalse(out.get("ok"))

    def test_prepare_refuses_a_missing_recipient(self):
        out = ss.social_send("prepare", recipient="Nobody",
                             text="hi", _contacts_loader=_loader)
        self.assertFalse(out.get("ok"))

    def test_prepare_refuses_an_overlong_message(self):
        out = ss.social_send("prepare", recipient="Mum",
                             text="x" * (ss._MAX_TEXT_CHARS + 1),
                             _contacts_loader=_loader)
        self.assertFalse(out.get("ok"))


class OpenTests(_Base):
    def test_open_refuses_a_wrong_draft_id(self):
        draft = self._prepare()
        out = ss.social_send("open", text="hello Mum, test",
                             draft_id="ss-wrong",
                             _reader=lambda: "Mum hello Mum, test")
        self.assertFalse(out.get("ok"))
        # The real draft is still prepared.
        self.assertEqual(ss._load_state().get("draft_id"), draft)

    def test_open_refuses_a_non_approved_text(self):
        draft = self._prepare()
        out = ss.social_send("open", text="a different message",
                             draft_id=draft,
                             _reader=lambda: "Mum a different message")
        self.assertFalse(out.get("ok"))
        self.assertEqual(ss._load_state().get("draft_id"), draft)

    def test_open_without_prepare_refuses(self):
        out = ss.social_send("open", text="hello Mum, test",
                             draft_id="ss-none",
                             _reader=lambda: "Mum hello Mum, test")
        self.assertFalse(out.get("ok"))

    def test_guard_failure_is_failed_and_clears_the_draft(self):
        draft = self._prepare()
        out = self._open(draft, screen="Alice +91 11111 11111 "
                                       "hello Mum, test")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("guard"), "header_mismatch")
        self.assertEqual(ss._load_state(), {})
        self.assertEqual(self.captured[-1][1]["outcome"], "failed")

    def test_open_arms_voice_mode_when_voice_access_is_on(self):
        draft = self._prepare()
        out = self._open(draft, va=3)
        self.assertTrue(out.get("ok"), str(out))
        self.assertEqual(out.get("send_mode"), "voice")
        self.assertIn("press Enter", out.get("instruction", ""))
        self.assertEqual(ss._load_state().get("state"), "opened")

    def test_open_arms_tap_mode_with_guidance_when_off(self):
        draft = self._prepare()
        out = self._open(draft, va=1)
        self.assertTrue(out.get("ok"), str(out))
        self.assertEqual(out.get("send_mode"), "tap")
        self.assertIn("Voice Access is off",
                      out.get("instruction", ""))

    def test_open_reports_a_link_that_did_not_open(self):
        draft = self._prepare()
        out = ss.social_send(
            "open", text="hello Mum, test", draft_id=draft,
            _reader=lambda: "Mum hello Mum, test",
            _opener=lambda url: False,
            _va_reader=lambda _k, _v: 3)
        self.assertFalse(out.get("ok"))
        self.assertEqual(self.captured[-1][1]["outcome"], "failed")


class VerifyTests(_Base):
    def test_done_needs_confirmation_and_thread(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        # Nothing sent yet and the thread does not show the
        # message: pending, not done.
        out = ss.social_send("verify", text="hello Mum, test",
                             draft_id=draft,
                             _reader=lambda: "Mum +91 98765 43210")
        self.assertEqual(out.get("outcome"), "pending")
        self.assertTrue(ss.note_press_enter_heard())
        out = self._verify(draft)
        self.assertTrue(out.get("ok"), str(out))
        self.assertEqual(out.get("outcome"), "done")
        self.assertEqual(self.captured[-1][1]["outcome"], "done")
        self.assertEqual(ss._load_state(), {})

    def test_thread_without_confirmation_is_unverified(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        # The message is in the thread but no send was confirmed:
        # it may be an earlier message, so it is unverified, never
        # done.
        out = ss.social_send("verify", text="hello Mum, test",
                             draft_id=draft,
                             _reader=lambda: "Mum hello Mum, test")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("outcome"), "unverified")

    def test_confirmation_without_thread_is_unverified(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        self.assertTrue(ss.note_press_enter_heard())
        out = self._verify(draft, screen="Mum something else")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("outcome"), "unverified")

    def test_confirmed_send_with_unreadable_thread_is_unverified(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        self.assertTrue(ss.note_press_enter_heard())
        out = ss.social_send("verify", text="hello Mum, test",
                             draft_id=draft, _reader=lambda: None)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("outcome"), "unverified")

    def test_tap_mode_is_done_on_the_thread_alone(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft, va=1).get("ok"))
        out = self._verify(draft)
        self.assertTrue(out.get("ok"), str(out))
        self.assertEqual(out.get("outcome"), "done")

    def test_verify_without_an_open_send_refuses(self):
        out = ss.social_send("verify", text="hello Mum, test",
                             draft_id="ss-none",
                             _reader=lambda: "Mum hello Mum, test")
        self.assertFalse(out.get("ok"))

    def test_verify_refuses_a_non_approved_text(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        out = ss.social_send("verify", text="a different message",
                             draft_id=draft,
                             _reader=lambda: "Mum a different message")
        self.assertFalse(out.get("ok"))


class VoiceAccessTests(_Base):
    def test_on_off_unknown(self):
        self.assertEqual(
            ss.voice_access_status(reader=lambda _k, _v: 3)["state"],
            "on")
        self.assertEqual(
            ss.voice_access_status(reader=lambda _k, _v: 1)["state"],
            "off")
        self.assertEqual(
            ss.voice_access_status(
                reader=lambda _k, _v: None)["state"],
            "unknown")
        self.assertEqual(
            ss.voice_access_status(
                reader=lambda _k, _v: 99)["state"],
            "unknown")

    def test_off_state_carries_the_setup_guidance(self):
        status = ss.voice_access_status(reader=lambda _k, _v: 1)
        self.assertIn("Accessibility", status["guidance"])

    def test_press_enter_is_ignored_only_while_a_send_is_pending(self):
        self.assertFalse(ss.should_ignore_utterance("press Enter"))
        self.assertFalse(ss.should_ignore_utterance("send it"))
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        self.assertTrue(ss.should_ignore_utterance("press Enter"))
        self.assertTrue(ss.should_ignore_utterance("Enter"))
        self.assertFalse(ss.should_ignore_utterance("send it"))

    def test_note_press_enter_heard_needs_a_pending_send(self):
        self.assertFalse(ss.note_press_enter_heard())
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        self.assertTrue(ss.note_press_enter_heard())
        self.assertTrue(ss._load_state()["press_enter_heard"])


class StateTests(_Base):
    def test_a_str_path_round_trips(self):
        # The seam contract allows a plain string path.
        ss.state_path = lambda: str(self.tmp / "str_state.json")
        draft = self._prepare()
        self.assertEqual(ss._load_state().get("draft_id"), draft)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(ss._load_state(), {})

    def test_corrupt_file_reads_as_empty(self):
        (self.tmp / "state.json").write_text("{not json",
                                             encoding="utf-8")
        self.assertEqual(ss._load_state(), {})

    def test_save_is_re_read_verified(self):
        ok, err = ss._save_state({"draft_id": "ss-test"})
        self.assertTrue(ok, err)
        self.assertEqual(ss._load_state(), {"draft_id": "ss-test"})


class ReadsTests(_Base):
    def test_status_reports_voice_access_and_no_pending_send(self):
        out = ss.social_send("status", _va_reader=lambda _k, _v: 3)
        self.assertTrue(out.get("ok"), str(out))
        self.assertIsNone(out.get("pending"))
        self.assertEqual(out["voice_access"]["state"], "on")

    def test_status_shows_a_pending_send_without_its_text(self):
        draft = self._prepare()
        out = ss.social_send("status", _va_reader=lambda _k, _v: 3)
        self.assertEqual(out["pending"]["draft_id"], draft)
        self.assertNotIn("hello Mum, test",
                         json.dumps(out["pending"]))

    def test_events_returns_receipts_without_text(self):
        draft = self._prepare()
        self.assertTrue(self._open(draft).get("ok"))
        self.assertTrue(ss.note_press_enter_heard())
        self.assertTrue(self._verify(draft).get("ok"))
        out = ss.social_send("events")
        self.assertTrue(out.get("ok"), str(out))
        self.assertTrue(out["receipts"])
        for receipt in out["receipts"]:
            self.assertNotIn("hello Mum, test",
                             json.dumps(receipt))
            self.assertNotIn("text", receipt.get("data", {}))

    def test_other_platforms_and_unknown_actions_refuse(self):
        self.assertFalse(ss.social_send(
            "prepare", recipient="Mum", text="hi",
            platform="telegram").get("ok"))
        self.assertFalse(ss.social_send("dance").get("ok"))


class RegistryTests(_Base):
    def test_social_send_is_registered_and_closed(self):
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertIn("social_send", names)
        decl = [d for d in decls if d["name"] == "social_send"][0]
        self.assertEqual(sorted(
            decl["parameters"]["properties"].keys()),
            ["action", "draft_id", "platform", "recipient", "text"])
        self.assertFalse(decl["parameters"].get("additionalProperties"))

    def test_social_send_is_confirm_gated_and_grouped(self):
        self.assertIn("social_send", _CONFIRM_GATE_TOOLS)
        self.assertIn("social_send",
                      GROUP_MEMBERS.get("Your world", ()))

    def test_snapshot_entry_is_confirm_gated_not_read(self):
        snap = json.loads((TREE / "tests"
                           / "_classification_snapshot.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(snap.get("social_send"),
                         {"confirm": True, "free": None,
                          "read": False})

    def test_d06_is_cited_and_no_parent_verified_marker(self):
        body = (TREE / "core" / "social_send.py").read_text(
            encoding="utf-8")
        self.assertIn("SO-3.1 still blocks automated WhatsApp "
                      "sending", body)
        # The marker string is built from parts so this test file
        # itself never contains the literal: the parent records
        # its own verification, and a tree-wide grep for the
        # marker must not trip on the check.
        marker = "PARENT" + "-VERIFIED"
        self.assertNotIn(marker, body)

    def test_the_voice_loop_calls_the_ignore_check(self):
        mic = (TREE / "core" / "voice" / "local_client_mic.py"
               ).read_text(encoding="utf-8")
        self.assertIn("should_ignore_utterance(user_text)", mic)
        self.assertIn("note_press_enter_heard()", mic)


class RealChainReceiptTests(unittest.TestCase):
    """One real-chain round-trip: the receipt lands in the shared
    hash chain and carries no message text. This writes to data/;
    the batch restores data/ afterwards, as every batch does."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="ss_real_"))
        self._orig_state_path = ss.state_path
        ss.state_path = lambda: str(self.tmp / "state.json")

    def tearDown(self):
        ss.state_path = self._orig_state_path

    def test_a_done_send_records_a_real_receipt_without_text(self):
        out = ss.social_send("prepare", recipient="Mum",
                             text="hello Mum, test",
                             _contacts_loader=_loader)
        self.assertTrue(out.get("ok"), str(out))
        draft = out["draft_id"]
        opened = ss.social_send(
            "open", text="hello Mum, test", draft_id=draft,
            _reader=lambda: "Mum +91 98765 43210 hello Mum, test",
            _opener=lambda url: True,
            _va_reader=lambda _k, _v: 3)
        self.assertTrue(opened.get("ok"), str(opened))
        self.assertTrue(ss.note_press_enter_heard())
        verified = ss.social_send(
            "verify", text="hello Mum, test", draft_id=draft,
            _reader=lambda: "Mum hello Mum, test")
        self.assertEqual(verified.get("outcome"), "done", str(verified))
        receipts = rf.read_events("social_send_receipt", limit=5)
        self.assertTrue(receipts)
        last = receipts[0]["data"]  # read_events is newest-first
        self.assertEqual(last.get("outcome"), "done")
        self.assertEqual(last.get("platform"), "whatsapp")
        self.assertEqual(last.get("recipient"), "Mum")
        self.assertNotIn("text", last)
        self.assertNotIn("hello Mum, test", json.dumps(last))


if __name__ == "__main__":
    unittest.main()
