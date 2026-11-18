"""personal_skills through the funnel, without the shim (round-2 N1 BUG 1).

The ``personal_skills`` tool failed deterministically through the
funnel with ``'NoneType' object has no attribute '__dict__'`` on
``action=list`` and ``action=inspect`` — while the lab suite stayed
green. Cause: ``core/personal_skills.py`` imports
``voice_assistant.core.skill_format`` first; that path exists only
under the test harness's ``voice_assistant.*`` shim, so the real
funnel (no shim) always took the file-path fallback, which loaded
``skill_format`` via ``spec_from_file_location`` + ``exec_module``
WITHOUT registering it in ``sys.modules``. ``skill_format`` defines
``@dataclass``es, and dataclass processing reads
``sys.modules[cls.__module__].__dict__`` — unregistered, that is
``None.__dict__`` and the import dies before the handler runs.

What this suite proves (no shim anywhere in this process — see
setUpModule, which purges any ``voice_assistant`` entries and
re-imports ``core.personal_skills`` fresh, forcing the fallback):
* the fallback load registers ``skill_format`` in ``sys.modules``
  and its ``Finding`` dataclass constructs;
* ``list`` on a fresh library returns ok with an empty list;
* ``inspect`` of a missing skill is an honest ok:false refusal
  ("No such skill"), not a handler_error;
* ``inspect`` of a real draft skill returns its entry + steps
  with ``can_run`` False (readable, not runnable);
* ``admit`` admits a valid draft (ok, state admitted) and the
  library then lists it as admitted;
* ``run`` on an unadmitted skill refuses honestly ("not admitted")
  without executing anything.

Each test gets a fresh temp HOME, so the library root
(``~/Documents/VYUK Skills``) never touches the real home.
Data is snapshotted at module level and restored afterwards;
the neighbour runner restores it after the leg as well.
Runs on both interpreters; no Qt needed.
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import types as _types_mod
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lab has no PortAudio: stub sounddevice (mirrors test_micfix.py).
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

_VA_SNAPSHOT = None
_PS_SNAPSHOT = None
_HOME_BEFORE = None
_DATA_BACKUP = None

SKILL_MD = """---
name: {name}
description: A demo skill for the funnel test.
---

# {name}

## Steps

1. Say hello.
2. Say goodbye.
"""


def setUpModule():
    """Force the no-shim fallback path, then snapshot data/.

    Purge any ``voice_assistant`` entries (another suite's shim
    must not mask the fallback) and any already-imported
    ``core.personal_skills`` / bare ``skill_format``, then import
    ``core.personal_skills`` fresh: with no shim the import takes
    the file-path fallback — the exact path the funnel takes in
    production. Snapshots are restored in tearDownModule.
    """
    global _VA_SNAPSHOT, _PS_SNAPSHOT, _HOME_BEFORE, _DATA_BACKUP
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    _PS_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k in ("core.personal_skills", "skill_format")}
    for key in list(sys.modules):
        if key == "voice_assistant" \
                or key.startswith("voice_assistant.") \
                or key in ("core.personal_skills", "skill_format"):
            del sys.modules[key]
    _HOME_BEFORE = os.environ.get("HOME")
    import core.personal_skills  # noqa: F401 — fallback load under test
    import core.confirm_gate as cg
    from core.funnel.tools_registry import tools as _TOOLS
    cg.register_handler(lambda *a, **k: True)
    globals()["TOOLS"] = _TOOLS
    data_dir = ROOT / "data"
    _DATA_BACKUP = tempfile.mkdtemp(prefix="r2n1s_data_")
    if data_dir.is_dir():
        shutil.copytree(data_dir, _DATA_BACKUP, dirs_exist_ok=True)


def tearDownModule():
    global _VA_SNAPSHOT, _PS_SNAPSHOT, _HOME_BEFORE, _DATA_BACKUP
    for key in list(sys.modules):
        if key == "voice_assistant" \
                or key.startswith("voice_assistant.") \
                or key in ("core.personal_skills", "skill_format"):
            del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    if _PS_SNAPSHOT is not None:
        sys.modules.update(_PS_SNAPSHOT)
    if _HOME_BEFORE is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _HOME_BEFORE
    if _DATA_BACKUP is not None:
        data_dir = ROOT / "data"
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.copytree(_DATA_BACKUP, data_dir)
        shutil.rmtree(_DATA_BACKUP, ignore_errors=True)
    _VA_SNAPSHOT = _PS_SNAPSHOT = _HOME_BEFORE = _DATA_BACKUP = None


def _execute(tool, args):
    """TOOLS.execute, returning (verdict, payload-dict-or-None, raw)."""
    res = TOOLS.execute(tool, dict(args))
    verdict = res.get("kind") or res.get("status")
    raw = res.get("message") or res.get("result") or ""
    payload = None
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
    return verdict, payload, str(raw)


class PersonalSkillsFunnelTests(unittest.TestCase):
    """personal_skills via TOOLS.execute, no shim, temp HOME."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="r2n1s_home_")
        os.environ["HOME"] = self._home
        self.lib_root = (pathlib.Path(self._home) / "Documents"
                         / "VYUK Skills")

    def tearDown(self):
        if _HOME_BEFORE is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = _HOME_BEFORE
        shutil.rmtree(self._home, ignore_errors=True)

    def _make_skill(self, name):
        skill_dir = self.lib_root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            SKILL_MD.format(name=name), encoding="utf-8")
        entry = {"name": name, "state": "draft", "aliases": [],
                 "description": "A demo skill for the funnel test.",
                 "allowed_tools_was_present": False,
                 "use_external_brain": False,
                 "disclosure_confirmed": False,
                 "path": str(skill_dir)}
        (skill_dir / ".vyuk.json").write_text(
            json.dumps(entry, indent=2), encoding="utf-8")
        self.lib_root.mkdir(parents=True, exist_ok=True)
        (self.lib_root / "index.json").write_text(
            json.dumps({"skills": [entry]}, indent=2), encoding="utf-8")
        return entry

    def test_fallback_load_registers_skill_format(self):
        import core.personal_skills as ps
        fmt = ps.fmt
        self.assertEqual(fmt.__name__, "skill_format")
        self.assertIs(sys.modules.get("skill_format"), fmt)
        finding = fmt.Finding(code="X", severity="error",
                              message="m", field="f")
        self.assertEqual(finding.to_dict()["code"], "X")

    def test_list_empty_library(self):
        verdict, payload, raw = _execute("personal_skills",
                                         {"action": "list"})
        self.assertEqual(verdict, "success", raw[:300])
        self.assertIsNotNone(payload)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("skills"), [])

    def test_inspect_missing_skill_is_honest_refusal(self):
        verdict, payload, raw = _execute(
            "personal_skills", {"action": "inspect", "name": "nope"})
        self.assertEqual(verdict, "success", raw[:300])
        self.assertIsNotNone(payload)
        self.assertFalse(payload.get("ok"))
        self.assertIn("No such skill", payload.get("error", ""))

    def test_inspect_draft_skill(self):
        self._make_skill("demo-one")
        verdict, payload, raw = _execute(
            "personal_skills", {"action": "inspect", "name": "demo-one"})
        self.assertEqual(verdict, "success", raw[:300])
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload["entry"]["name"], "demo-one")
        self.assertEqual(payload.get("steps"),
                         ["Say hello.", "Say goodbye."])
        self.assertFalse(payload.get("can_run"))

    def test_admit_draft_skill(self):
        self._make_skill("demo-two")
        verdict, payload, raw = _execute(
            "personal_skills", {"action": "admit", "name": "demo-two"})
        self.assertEqual(verdict, "success", raw[:300])
        self.assertTrue(payload.get("ok"), raw[:300])
        self.assertEqual(payload.get("state"), "admitted")
        verdict, payload, raw = _execute("personal_skills",
                                         {"action": "list"})
        self.assertTrue(payload.get("ok"))
        listed = [s for s in payload.get("skills", [])
                  if s.get("name") == "demo-two"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].get("state"), "admitted")

    def test_run_unadmitted_refused_honestly(self):
        self._make_skill("demo-three")
        verdict, payload, raw = _execute(
            "personal_skills",
            {"action": "run", "name": "demo-three", "inputs": "[]"})
        self.assertEqual(verdict, "success", raw[:300])
        self.assertIsNotNone(payload)
        self.assertFalse(payload.get("ok"))
        self.assertIn("not admitted", payload.get("error", ""))


if __name__ == "__main__":
    unittest.main()
