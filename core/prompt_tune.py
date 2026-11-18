"""Local prompt optimisation + evaluation — the optimisation half
of DSPy, rebuilt from scratch, never a port of the framework.

Parent-ruled (licence, 2026-11-12): stanfordnlp/dspy
(MIT; the LICENSE file from the default branch main was read in
full 2026-11-12 — 1,085 bytes, 22 lines, "Copyright (c) 2023
Stanford Future Data Systems", standard MIT text) is INSPIRATION
ONLY. No code, schema, text or package is adopted from it; this
module is written from scratch in stdlib Python over the tree's
own urllib shape (core/security_scan.py's injectable-opener fetch,
core/billing.py's injectable-opener call, core/agent_call.py's
injectable-opener GET) and the tree's own SQLite-store shape
(core/bookings.py, core/lodging.py, core/wardrobe.py,
core/billing.py, core/agent_call.py) and the tree's own config
DATA_DIR. Ideas are not copyrighted, so no MIT duty is triggered
by a from-scratch rebuild. The "prompt_tune" name here is the
generic word for the capability, and no package, fork, service or
hosted offering is named after the original.

The mechanism is DSPy's own stated idea, verified from the README
before building — "Programming—not prompting—Foundation Models":
write the prompt as code, then let an optimisation loop measure
candidate prompts against a small evaluation set and keep the
best, instead of hand-tweaking one prompt and hoping. What is
built here is that loop, local and single-user: the owner keeps
prompts and evaluation sets on this machine, the tool runs each
candidate against the set through the owner's own configured
Gemini endpoint, scores every answer with a deterministic metric,
ranks the candidates, and promotes the winner. The loop is
bounded (a handful of candidates, a handful of examples each)
and every run is written to a local hash-chained ledger.

Promotion is gated on a held-out set (GAPS-4): the evaluation
set is split deterministically into a tuning set and a held-out
set, candidates are ranked on the tuning set only, and the
winner is promoted only when it beats the base prompt on the
held-out set by at least the min-improvement threshold, with no
regression tolerated. A candidate that merely memorised the
tuning slice is refused, honestly, with the numbers — the
refusal, like the promotion, is recorded with its evidence (the
split, both scores, the thresholds) so the claim "this prompt
is better" is checkable afterwards. The held-out set is never
tuned on: ranking happens first, on the tuning set only, and
the held-out examples are only ever scored, never selected on.

What this is: a local prompt-optimisation/evaluation loop over
the tree's own LM calls. 'prompt' keeps prompts (add/list/get/
remove/set_current). 'dataset' keeps evaluation sets the owner
supplies (input/expected pairs, JSON, local only). 'evaluate'
runs one prompt against one dataset and reports per-example
scores plus the mean. 'tune' ranks candidates — operator-supplied
prompts, the built-in variant bank applied to a base prompt,
and/or LM-proposed rewrites — on the same dataset and promotes
the winner to current. 'variants' lists the built-in variant
bank. 'propose' asks the LM for rewrites of a base prompt.
'status' counts prompts, datasets and runs and reports whether
the LM is reachable (a key is configured) and whether the ledger
chain verifies. 'events' shows the hash-chained ledger. Every
write is re-read before it is claimed (A1-store) and the chain
verifies over the full ledger.

What is NOT built, stated plainly:
- The DSPy framework itself: no port of its LM abstractions,
  signatures, modules or optimisers — VYUK does not adopt it.
- Weight training / fine-tuning: the tree has no training path
  and no torch. Only prompts are optimised, never weights.
- Any cloud or hosted optimisation service: the loop runs here,
  on this machine, against the owner's own configured endpoint.
- LLM-as-judge scoring: scores are deterministic (exact /
  contains / token-F1 over the owner's expected answers), never
  a second LM's opinion. A judge would be a second opinion
  dressed as a measurement.
- The operator's data goes nowhere except the operator's own
  configured Gemini endpoint, and only after the owner approves
  the run (C-14): 'evaluate', 'tune' and 'propose' ask first.
  The API key is read from the product's own config (the same
  key the brain session uses) — it is never passed as a tool
  argument, never echoed, never written to the store or the
  ledger, and when no key is configured the LM actions refuse
  honestly instead of guessing.
- Multi-user / shared prompt library: single-user by design.
  The prompt store is the owner's own list.
"""

import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import DATA_DIR, config
from . import store_base
from .version import VERSION

DEFAULT_DB_FILENAME = "prompt_tune.sqlite3"

_USER_AGENT = "VYUK-prompt-tune/1.0 (Gemini REST client)"
_TIMEOUT_S = 30
_MAX_BODY_BYTES = 1024 * 1024  # 1 MB cap, the security_scan precedent
_MAX_PROMPT_CHARS = 4000
_MAX_NAME_CHARS = 80
_MAX_EXAMPLE_CHARS = 2000
_MAX_DATASET_EXAMPLES = 50
_MAX_EVAL_EXAMPLES = 20
_MAX_CANDIDATES = 10
_MAX_PROPOSE = 4
_DEFAULT_EVAL_LIMIT = 5

# The held-out gate (GAPS-4): the BenchmarkGate SHAPE from the
# OpenJarvis comparison — min_improvement / max_regression — as
# inspiration only. Own code, own numbers, nothing adopted.
# 1 in 5 examples is held out; a candidate is promoted only when
# it beats the base prompt on the held-out set by at least
# _MIN_IMPROVEMENT, and any held-out regression fails the gate
# (_MAX_REGRESSION = 0.0: no regression is tolerated).
_HELD_OUT_EVERY = 5
_MIN_IMPROVEMENT = 0.05
_MAX_REGRESSION = 0.0

_GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/"
               "models/%s:generateContent")

# The deterministic variant bank: instruction suffixes applied to
# a base prompt to form candidates. Fixed, inspectable, listed by
# the 'variants' action — no LM is needed to propose them.
_VARIANTS = (
    "Answer concisely.",
    "Think step by step, then give the final answer.",
    "Give only the answer, nothing else.",
    "Explain briefly, then answer.",
    "If unsure, say so rather than guessing.",
    "Answer in one sentence.",
    "Show your working, then give the final answer.",
    "Use plain language.",
)

_METRICS = ("exact", "contains", "f1")

# Actions that call the LM (confirm-gated) vs reads (free).
# Kept in one place so the gate branches in
# core/funnel/tools_gate.py and core/funnel/tools_gate_readclass.py
# draw the same split. Prompt/dataset writes are local app-state
# (the wardrobe/lodging precedent) and stay free.
_WRITE_ACTIONS = {
    ("evaluate", ""),
    ("tune", ""),
    ("propose", ""),
}
_READ_ACTIONS = {
    ("prompt", "list"),
    ("prompt", "get"),
    ("dataset", "list"),
    ("dataset", "get"),
    ("variants", ""),
    ("status", ""),
    ("events", ""),
}


def _norm(text):
    """Normalise for scoring: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokens(text):
    return re.findall(r"[a-z0-9]+", _norm(text))


def _score(metric, answer, expected):
    """Deterministic score in [0, 1] for one answer."""
    a, e = _norm(answer), _norm(expected)
    if metric == "exact":
        return 1.0 if a == e and a else 0.0
    if metric == "contains":
        return 1.0 if e and e in a else 0.0
    # f1: word-token overlap.
    at, et = _tokens(answer), _tokens(expected)
    if not et:
        return 1.0 if not at else 0.0
    if not at:
        return 0.0
    from collections import Counter
    ca, ce = Counter(at), Counter(et)
    overlap = sum((ca & ce).values())
    precision = overlap / len(at)
    recall = overlap / len(et)
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _lm_key():
    """The product's own configured Gemini key, or None.

    The key lives in config (the same key the brain session uses);
    it is never passed as a tool argument, never echoed and never
    written to the store or the ledger.
    """
    key = str(getattr(config, "api_key", "") or "").strip()
    return key or None


def _lm_model(model):
    """The model to call: the caller's, else the configured one."""
    if isinstance(model, str) and model.strip():
        return model.strip()
    return str(getattr(config, "model_name", "") or "").strip()


def _no_key_refusal():
    """The refusal when no Gemini key is configured."""
    return ("refused: no Gemini key is configured. Set one in Settings "
            "first — VYUK never guesses at a key and never asks for one "
            "in chat. Nothing was sent.")


def _lm_call(prompt_text, model, _opener=None):
    """One Gemini generateContent call. Returns (text-or-None, error).

    Own stdlib urllib client in the billing/agent_call shape: the
    opener is injectable so the suite and prover never touch the
    network. The key is read from config and sent only as the
    x-goog-api-key header — it never appears in the URL, the body
    or any answer.
    """
    key = _lm_key()
    if not key:
        return None, _no_key_refusal()
    model = _lm_model(model)
    if not model:
        return None, "refused: no Gemini model is configured."
    url = _GEMINI_URL % urllib.parse.quote(model, safe="")
    body = json.dumps({"contents": [{"parts": [{"text": prompt_text}]}]},
                      sort_keys=True).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": key,
        "User-Agent": _USER_AGENT,
    }, method="POST")
    opener = _opener or urllib.request.urlopen
    try:
        with opener(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read(_MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as e:
        return None, ("the LM endpoint refused the call (HTTP %s). "
                      "Nothing was scored." % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, ("the LM call failed (%s). Nothing was scored."
                      % e.__class__.__name__)
    if len(raw) > _MAX_BODY_BYTES:
        return None, "refused: the LM answer exceeded the 1 MB cap."
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "the LM answer was not JSON. Nothing was scored."
    if not isinstance(parsed, dict):
        return None, "the LM answer was not an object. Nothing was scored."
    parts = []
    for cand in parsed.get("candidates") or []:
        content = (cand or {}).get("content") or {}
        for part in content.get("parts") or []:
            text = (part or {}).get("text")
            if isinstance(text, str):
                parts.append(text)
    text = "".join(parts).strip()
    if not text:
        return None, "the LM returned no text. Nothing was scored."
    return text, None


class _PromptTuneDB:
    """Local single-user prompt + dataset store, run records and a
    hash-chained ledger, in the bookings/lodging/wardrobe/billing/
    agent_call shape: single-writer serialisation, every write
    re-read before it is claimed, sha256(prev + payload) events
    with full-chain verification. No key is ever written here —
    only prompts, datasets, run records and scores.
    """

    def __init__(self, db_path=None):
        self.path = (Path(db_path) if db_path else
                     Path(DATA_DIR) / DEFAULT_DB_FILENAME)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store_base.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS prompt_tune_prompts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, text TEXT NOT NULL, "
            "current INTEGER NOT NULL DEFAULT 0, "
            "best_score REAL, best_dataset INTEGER, "
            "created INTEGER NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS prompt_tune_datasets ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL, examples_json TEXT NOT NULL, "
            "created INTEGER NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS prompt_tune_runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "kind TEXT NOT NULL, prompt_id INTEGER, "
            "dataset_id INTEGER, metric TEXT NOT NULL, "
            "model TEXT NOT NULL, candidates_json TEXT NOT NULL, "
            "winner_id INTEGER, calls INTEGER NOT NULL, "
            "created INTEGER NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS prompt_tune_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER NOT NULL, kind TEXT NOT NULL, "
            "object TEXT NOT NULL, detail TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL, hash TEXT NOT NULL)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- prompts --

    def add_prompt(self, name, text):
        """Insert a prompt, then re-read it (A1-store)."""
        now = int(time.time())
        cur = self.conn.execute(
            "INSERT INTO prompt_tune_prompts "
            "(name, text, current, created) VALUES (?, ?, 0, ?)",
            (name, text, now))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, name, text, current, best_score, best_dataset "
            "FROM prompt_tune_prompts WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        if not row or row[2] != text:
            return None
        return {"id": row[0], "name": row[1], "text": row[2],
                "current": row[3], "best_score": row[4],
                "best_dataset": row[5]}

    def get_prompt(self, prompt_id):
        row = self.conn.execute(
            "SELECT id, name, text, current, best_score, best_dataset "
            "FROM prompt_tune_prompts WHERE id = ?",
            (prompt_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "text": row[2],
                "current": row[3], "best_score": row[4],
                "best_dataset": row[5]}

    def find_prompt_by_text(self, text):
        row = self.conn.execute(
            "SELECT id, name, text, current, best_score, best_dataset "
            "FROM prompt_tune_prompts WHERE text = ? ORDER BY id LIMIT 1",
            (text,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "text": row[2],
                "current": row[3], "best_score": row[4],
                "best_dataset": row[5]}

    def list_prompts(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, name, current, best_score, best_dataset "
            "FROM prompt_tune_prompts ORDER BY id LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return [{"id": r[0], "name": r[1], "current": r[2],
                 "best_score": r[3], "best_dataset": r[4]} for r in rows]

    def set_current(self, prompt_id):
        self.conn.execute(
            "UPDATE prompt_tune_prompts SET current = 0")
        self.conn.execute(
            "UPDATE prompt_tune_prompts SET current = 1 WHERE id = ?",
            (prompt_id,))
        self.conn.commit()
        row = self.get_prompt(prompt_id)
        if not row or not row["current"]:
            return None
        return row

    def get_current(self):
        row = self.conn.execute(
            "SELECT id, name, text, current, best_score, best_dataset "
            "FROM prompt_tune_prompts WHERE current = 1 ORDER BY id "
            "LIMIT 1").fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "text": row[2],
                "current": row[3], "best_score": row[4],
                "best_dataset": row[5]}

    def remove_prompt(self, prompt_id):
        self.conn.execute(
            "DELETE FROM prompt_tune_prompts WHERE id = ?",
            (prompt_id,))
        self.conn.commit()
        return self.get_prompt(prompt_id) is None

    def set_best(self, prompt_id, score, dataset_id):
        self.conn.execute(
            "UPDATE prompt_tune_prompts SET best_score = ?, "
            "best_dataset = ? WHERE id = ?",
            (score, dataset_id, prompt_id))
        self.conn.commit()
        return self.get_prompt(prompt_id)

    def count_prompts(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM prompt_tune_prompts").fetchone()[0]

    # -- datasets --

    def add_dataset(self, name, examples):
        """Insert a dataset, then re-read it (A1-store)."""
        now = int(time.time())
        blob = json.dumps(examples, sort_keys=True)
        cur = self.conn.execute(
            "INSERT INTO prompt_tune_datasets "
            "(name, examples_json, created) VALUES (?, ?, ?)",
            (name, blob, now))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, name, examples_json FROM prompt_tune_datasets "
            "WHERE id = ?", (cur.lastrowid,)).fetchone()
        if not row or row[2] != blob:
            return None
        return {"id": row[0], "name": row[1],
                "examples": json.loads(row[2])}

    def get_dataset(self, dataset_id):
        row = self.conn.execute(
            "SELECT id, name, examples_json FROM prompt_tune_datasets "
            "WHERE id = ?", (dataset_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1],
                "examples": json.loads(row[2])}

    def list_datasets(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, name, examples_json FROM prompt_tune_datasets "
            "ORDER BY id LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [{"id": r[0], "name": r[1],
                 "examples": len(json.loads(r[2]))} for r in rows]

    def remove_dataset(self, dataset_id):
        self.conn.execute(
            "DELETE FROM prompt_tune_datasets WHERE id = ?",
            (dataset_id,))
        self.conn.commit()
        return self.get_dataset(dataset_id) is None

    def count_datasets(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM prompt_tune_datasets").fetchone()[0]

    # -- runs --

    def add_run(self, kind, prompt_id, dataset_id, metric, model,
                candidates, winner_id, calls):
        now = int(time.time())
        blob = json.dumps(candidates, sort_keys=True)
        cur = self.conn.execute(
            "INSERT INTO prompt_tune_runs (kind, prompt_id, dataset_id, "
            "metric, model, candidates_json, winner_id, calls, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, prompt_id, dataset_id, metric, model, blob,
             winner_id, calls, now))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, kind, prompt_id, dataset_id, metric, model, "
            "candidates_json, winner_id, calls FROM prompt_tune_runs "
            "WHERE id = ?", (cur.lastrowid,)).fetchone()
        if not row or row[6] != blob:
            return None
        return {"id": row[0], "kind": row[1], "prompt_id": row[2],
                "dataset_id": row[3], "metric": row[4], "model": row[5],
                "candidates": json.loads(row[6]), "winner_id": row[7],
                "calls": row[8]}

    def count_runs(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM prompt_tune_runs").fetchone()[0]

    # -- ledger --

    def _last_hash(self):
        row = self.conn.execute(
            "SELECT hash FROM prompt_tune_events ORDER BY id DESC "
            "LIMIT 1").fetchone()
        return row[0] if row else "GENESIS"

    def record(self, kind, obj, detail):
        """Insert + re-read before claiming (A1-store)."""
        ts = int(time.time())
        prev = self._last_hash()
        digest = store_base.chain_digest(
            "prev_payload_list", prev,
            {"fields": [ts, kind, obj, detail]})
        cur = self.conn.execute(
            "INSERT INTO prompt_tune_events "
            "(ts, kind, object, detail, prev_hash, hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, kind, obj, detail, prev, digest))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id, ts, kind, object, detail, hash "
            "FROM prompt_tune_events WHERE id = ?",
            (cur.lastrowid,)).fetchone()
        if not row or row[5] != digest or row[3] != obj:
            return None
        return row

    def verify_chain(self):
        rows = self.conn.execute(
            "SELECT id, ts, kind, object, detail, prev_hash, hash "
            "FROM prompt_tune_events ORDER BY id").fetchall()
        entries = [{"fields": [ts, kind, obj, detail],
                    "prev_hash": prev_hash, "hash": digest}
                   for _id, ts, kind, obj, detail, prev_hash,
                   digest in rows]
        result = store_base.verify_entries(
            entries, "prev_payload_list", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return False, "chain broken at event %d" % bad[0]
            return False, "hash mismatch at event %d" % bad[0]
        return True, "%d events, chain intact" % len(rows)

    def list_events(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, ts, kind, object, detail "
            "FROM prompt_tune_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return rows


# -------------------------------------------------------------- actions

def _as_int(value):
    """Coerce an id/limit to int, or None when it is not one."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _as_count(value, default, maximum):
    """Coerce a count, clamped to [1, maximum]; default when absent."""
    n = _as_int(value)
    if n is None:
        return default
    return max(1, min(maximum, n))


def _parse_examples(raw):
    """Parse the dataset examples JSON. Returns (examples, error)."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "refused: no examples were supplied. A dataset is a JSON list of {\"input\", \"expected\"} pairs."
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "refused: the examples are not valid JSON."
    if not isinstance(parsed, list) or not parsed:
        return None, "refused: the examples must be a non-empty JSON list."
    if len(parsed) > _MAX_DATASET_EXAMPLES:
        return None, ("refused: at most %d examples per dataset — "
                      "the evaluation set is meant to be small."
                      % _MAX_DATASET_EXAMPLES)
    examples = []
    for i, item in enumerate(parsed, 1):
        if not isinstance(item, dict):
            return None, "refused: example %d is not an object." % i
        inp = item.get("input")
        exp = item.get("expected")
        if not isinstance(inp, str) or not inp.strip():
            return None, "refused: example %d has no 'input'." % i
        if not isinstance(exp, str) or not exp.strip():
            return None, "refused: example %d has no 'expected'." % i
        if len(inp) > _MAX_EXAMPLE_CHARS or len(exp) > _MAX_EXAMPLE_CHARS:
            return None, ("refused: example %d exceeds the %d-character "
                          "cap per field." % (i, _MAX_EXAMPLE_CHARS))
        examples.append({"input": inp.strip(), "expected": exp.strip()})
    return examples, None


def _parse_candidates(raw):
    """Parse the tune candidates JSON. Returns (list, error)."""
    if not isinstance(raw, str) or not raw.strip():
        return [], None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "refused: the candidates are not valid JSON."
    if not isinstance(parsed, list):
        return None, "refused: the candidates must be a JSON list."
    out = []
    for item in parsed:
        if isinstance(item, int) and not isinstance(item, bool):
            out.append(str(item))
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
        else:
            return None, ("refused: a candidate must be a prompt number "
                          "or prompt text.")
    return out, None


def _resolve_prompt(db, prompt_id, text):
    """Resolve a prompt id or inline text. Returns (row, text, error).

    row is the stored prompt when one was named, else None; text is
    the instruction to run.
    """
    pid = _as_int(prompt_id)
    if pid is not None:
        row = db.get_prompt(pid)
        if not row:
            return None, None, ("refused: no prompt %d is stored. "
                                "List prompts first." % pid)
        return row, row["text"], None
    if isinstance(text, str) and text.strip():
        if len(text) > _MAX_PROMPT_CHARS:
            return None, None, ("refused: the prompt text exceeds the "
                                "%d-character cap." % _MAX_PROMPT_CHARS)
        return None, text.strip(), None
    return None, None, ("refused: name a prompt (prompt_id) or supply "
                        "the prompt text.")


def _run_candidate(db, text, examples, metric, model, _opener):
    """Run one candidate over examples. Returns (scores, calls, errs).

    scores is a list of (expected, answer-or-None, score, error-or-
    None); calls is the LM call count; errs counts the calls that
    failed outright. A failed call scores 0 and its reason is
    carried in the row — reported, never hidden.
    """
    scores = []
    calls = 0
    errs = 0
    for ex in examples:
        full = text + "\n\nInput:\n" + ex["input"]
        answer, err = _lm_call(full, model, _opener)
        calls += 1
        if err:
            scores.append((ex["expected"], None, 0.0, err))
            errs += 1
        else:
            scores.append((ex["expected"], answer,
                           _score(metric, answer, ex["expected"]), None))
    return scores, calls, errs


def _mean(scores):
    if not scores:
        return 0.0
    return sum(s for _, _, s, _ in scores) / len(scores)


def _split_examples(examples):
    """Deterministic train/held-out split. Returns (train, held_out).

    An example is held out when the first byte of the sha256 of
    its canonical JSON is a multiple of _HELD_OUT_EVERY — 1 in 5
    by content hash, so the split depends on the example itself,
    not its position: adding examples does not reshuffle the
    split, and the same dataset splits the same way on every run.
    Guarantees at least one held-out and at least one train
    example whenever there are 2 or more (the last train example
    moves to held-out when the hash put none there, and vice
    versa); with fewer than 2 the split is impossible and the
    caller refuses to tune.
    """
    if len(examples) < 2:
        return list(examples), []
    train, held = [], []
    for ex in examples:
        blob = json.dumps(ex, sort_keys=True)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        if int(digest[:2], 16) % _HELD_OUT_EVERY == 0:
            held.append(ex)
        else:
            train.append(ex)
    if not held:
        held.append(train.pop())
    if not train:
        train.append(held.pop(0))
    return train, held


def _gate_decision(winner_heldout, base_heldout):
    """The held-out gate. Returns (promote, reason).

    Promote only when the winner beats the base prompt on the
    held-out set by at least _MIN_IMPROVEMENT. Any regression
    fails the non-regression check (no regression is tolerated);
    a gain below the threshold fails the improvement check. The
    reason states the numbers either way, so a refusal is
    checkable, not just a verdict.
    """
    delta = winner_heldout - base_heldout
    if delta < -_MAX_REGRESSION:
        return False, ("regressed on the held-out set by %.3f "
                       "(winner %.3f vs base %.3f)"
                       % (-delta, winner_heldout, base_heldout))
    if delta < _MIN_IMPROVEMENT:
        return False, ("improved the held-out set by only %.3f "
                       "(winner %.3f vs base %.3f), below the %.3f "
                       "min-improvement threshold"
                       % (delta, winner_heldout, base_heldout,
                          _MIN_IMPROVEMENT))
    return True, ("held-out %.3f vs base %.3f (improvement %.3f, "
                  "threshold %.3f)"
                  % (winner_heldout, base_heldout, delta,
                     _MIN_IMPROVEMENT))


def _do_prompt(db, mode, prompt_id, name, text, limit):
    if mode in ("", "list"):
        rows = db.list_prompts(limit or 20)
        if not rows:
            return "No prompts stored yet. Add one with action 'prompt', mode 'add', a name and the prompt text."
        lines = ["Prompts (%d):" % len(rows)]
        for r in rows:
            best = ("best %.2f on dataset %s" % (r["best_score"], r["best_dataset"])
                    if r["best_score"] is not None else "not evaluated yet")
            lines.append("  #%d %s%s — %s" % (
                r["id"], r["name"],
                " (current)" if r["current"] else "", best))
        return "\n".join(lines)
    if mode == "add":
        if not isinstance(name, str) or not name.strip():
            return "refused: a prompt needs a name."
        if len(name) > _MAX_NAME_CHARS:
            return "refused: the name exceeds the %d-character cap." % _MAX_NAME_CHARS
        if not isinstance(text, str) or not text.strip():
            return "refused: a prompt needs text."
        if len(text) > _MAX_PROMPT_CHARS:
            return "refused: the prompt text exceeds the %d-character cap." % _MAX_PROMPT_CHARS
        row = db.add_prompt(name.strip(), text.strip())
        if not row:
            return "the prompt was not stored (the re-read disagreed). Nothing was kept."
        db.record("prompt_add", "prompt #%d" % row["id"],
                  "added '%s'" % row["name"])
        return "Added prompt #%d '%s'." % (row["id"], row["name"])
    if mode == "get":
        pid = _as_int(prompt_id)
        if pid is None:
            return "refused: name the prompt number to read."
        row = db.get_prompt(pid)
        if not row:
            return "refused: no prompt %d is stored." % pid
        best = ("best %.2f on dataset %s" % (row["best_score"], row["best_dataset"])
                if row["best_score"] is not None else "not evaluated yet")
        return ("Prompt #%d '%s'%s — %s\n%s" % (
            row["id"], row["name"],
            " (current)" if row["current"] else "", best, row["text"]))
    if mode == "remove":
        pid = _as_int(prompt_id)
        if pid is None:
            return "refused: name the prompt number to remove."
        row = db.get_prompt(pid)
        if not row:
            return "refused: no prompt %d is stored." % pid
        if not db.remove_prompt(pid):
            return "the prompt was not removed. Nothing changed."
        db.record("prompt_remove", "prompt #%d" % pid,
                  "removed '%s'" % row["name"])
        return "Removed prompt #%d '%s'." % (pid, row["name"])
    if mode == "set_current":
        pid = _as_int(prompt_id)
        if pid is None:
            return "refused: name the prompt number to make current."
        row = db.set_current(pid)
        if not row:
            return "refused: no prompt %d is stored." % pid
        db.record("prompt_current", "prompt #%d" % pid,
                  "made '%s' current" % row["name"])
        return "Prompt #%d '%s' is now current." % (pid, row["name"])
    return ("refused: mode must be 'add', 'list', 'get', 'remove' or "
            "'set_current' for action 'prompt'.")


def _do_dataset(db, mode, dataset_id, name, examples, limit):
    if mode in ("", "list"):
        rows = db.list_datasets(limit or 20)
        if not rows:
            return "No datasets stored yet. Add one with action 'dataset', mode 'add', a name and the examples JSON."
        lines = ["Datasets (%d):" % len(rows)]
        for r in rows:
            lines.append("  #%d %s — %d examples" % (
                r["id"], r["name"], r["examples"]))
        return "\n".join(lines)
    if mode == "add":
        if not isinstance(name, str) or not name.strip():
            return "refused: a dataset needs a name."
        if len(name) > _MAX_NAME_CHARS:
            return "refused: the name exceeds the %d-character cap." % _MAX_NAME_CHARS
        parsed, err = _parse_examples(examples)
        if err:
            return err
        row = db.add_dataset(name.strip(), parsed)
        if not row:
            return "the dataset was not stored (the re-read disagreed). Nothing was kept."
        db.record("dataset_add", "dataset #%d" % row["id"],
                  "added '%s' (%d examples)" % (row["name"], len(parsed)))
        return "Added dataset #%d '%s' (%d examples)." % (
            row["id"], row["name"], len(parsed))
    if mode == "get":
        did = _as_int(dataset_id)
        if did is None:
            return "refused: name the dataset number to read."
        row = db.get_dataset(did)
        if not row:
            return "refused: no dataset %d is stored." % did
        lines = ["Dataset #%d '%s' (%d examples):" % (
            row["id"], row["name"], len(row["examples"]))]
        for i, ex in enumerate(row["examples"], 1):
            lines.append("  %d. input: %s | expected: %s" % (
                i, ex["input"][:80], ex["expected"][:80]))
        return "\n".join(lines)
    if mode == "remove":
        did = _as_int(dataset_id)
        if did is None:
            return "refused: name the dataset number to remove."
        row = db.get_dataset(did)
        if not row:
            return "refused: no dataset %d is stored." % did
        if not db.remove_dataset(did):
            return "the dataset was not removed. Nothing changed."
        db.record("dataset_remove", "dataset #%d" % did,
                  "removed '%s'" % row["name"])
        return "Removed dataset #%d '%s'." % (did, row["name"])
    return ("refused: mode must be 'add', 'list', 'get' or 'remove' "
            "for action 'dataset'.")


def _do_evaluate(db, prompt_id, text, dataset_id, metric, limit, model,
                 _opener):
    if metric not in _METRICS:
        return "refused: metric must be 'exact', 'contains' or 'f1'."
    if not _lm_key():
        return _no_key_refusal()
    row, prompt_text, err = _resolve_prompt(db, prompt_id, text)
    if err:
        return err
    did = _as_int(dataset_id)
    if did is None:
        return "refused: name the dataset number to evaluate against."
    ds = db.get_dataset(did)
    if not ds:
        return "refused: no dataset %d is stored." % did
    n = _as_count(limit, _DEFAULT_EVAL_LIMIT, _MAX_EVAL_EXAMPLES)
    examples = ds["examples"][:n]
    scores, calls, errs = _run_candidate(
        db, prompt_text, examples, metric, model, _opener)
    mean = _mean(scores)
    run = db.add_run("evaluate", row["id"] if row else None, did,
                     metric, _lm_model(model),
                     [{"label": row["name"] if row else "inline",
                       "mean": mean}], None, calls)
    if not run:
        return "the run was not recorded (the re-read disagreed)."
    db.record("evaluate", "dataset #%d" % did,
              "prompt '%s' scored %.3f (%s, %d examples, %d LM calls, "
              "%d failed)" % (row["name"] if row else "inline", mean,
                              metric, len(examples), calls, errs))
    lines = ["Evaluation: prompt '%s' on dataset #%d '%s' — mean %.3f "
             "(%s, %d examples, %d LM calls%s):" % (
                 row["name"] if row else "inline", did, ds["name"],
                 mean, metric, len(examples), calls,
                 ", %d calls failed" % errs if errs else "")]
    for i, (expected, answer, score, err) in enumerate(scores, 1):
        if err:
            lines.append("  %d. %.2f — expected '%s', no answer (%s)" % (
                i, score, expected[:60], err[:100]))
        else:
            shown = (answer[:80] + ("…" if len(answer) > 80 else ""))
            lines.append("  %d. %.2f — expected '%s', got '%s'" % (
                i, score, expected[:60], shown))
    return "\n".join(lines)


def _propose_rewrites(base_text, count, model, _opener):
    """Ask the LM for rewrites of a base prompt. Returns (list, error).

    One LM call with a fixed meta-prompt; the numbered lines of the
    answer are the rewrites. Nothing is stored here.
    """
    meta = ("Rewrite the following instruction in %d different ways that "
            "keep its exact meaning and intent. Return each rewrite on "
            "its own line, numbered 1 to %d, with no extra commentary."
            "\n\nInstruction:\n%s" % (count, count, base_text))
    answer, err = _lm_call(meta, model, _opener)
    if err:
        return None, err
    rewrites = []
    for line in answer.splitlines():
        m = re.match(r"\s*\d+[.)]\s*(.+?)\s*$", line)
        if m and m.group(1):
            rewrites.append(m.group(1))
    if not rewrites:
        return None, ("the LM returned no numbered rewrites. Nothing "
                      "was proposed.")
    return rewrites[:count], None


def _do_tune(db, prompt_id, text, dataset_id, candidates, variants,
             propose, metric, limit, model, _opener):
    if metric not in _METRICS:
        return "refused: metric must be 'exact', 'contains' or 'f1'."
    if not _lm_key():
        return _no_key_refusal()
    base_row, base_text, err = _resolve_prompt(db, prompt_id, text)
    if err:
        return err
    did = _as_int(dataset_id)
    if did is None:
        return "refused: name the dataset number to tune against."
    ds = db.get_dataset(did)
    if not ds:
        return "refused: no dataset %d is stored." % did
    supplied, err = _parse_candidates(candidates)
    if err:
        return err
    n = _as_count(limit, _DEFAULT_EVAL_LIMIT, _MAX_EVAL_EXAMPLES)
    examples = ds["examples"][:n]
    # The held-out gate (GAPS-4): split the slice BEFORE any LM call
    # is spent, so a too-small slice refuses for free.
    train, held_out = _split_examples(examples)
    if not train or not held_out:
        return ("refused: the %d-example slice is too small to split "
                "into a tuning set and a held-out set — tune needs at "
                "least 2 examples in the slice (raise 'limit' or add "
                "examples). Nothing was tuned." % len(examples))
    model_name = _lm_model(model)
    # The candidate budget is checked BEFORE any LM call is spent:
    # supplied + variant bank (8) + proposed rewrites + the base
    # prompt itself must fit the cap, so a refused run costs nothing.
    propose_count = (_as_count(propose, 1, _MAX_PROPOSE)
                     if propose else 0)
    expected = (len(supplied) + (len(_VARIANTS) if variants else 0)
                + propose_count + 1)
    if expected > _MAX_CANDIDATES:
        return ("refused: that would be %d candidates (base prompt + "
                "%d supplied + %d variants + %d proposed) — at most %d "
                "per tune run. Split the run." % (
                    expected, len(supplied),
                    len(_VARIANTS) if variants else 0, propose_count,
                    _MAX_CANDIDATES))
    # Resolve the candidate set: (label, text, store_name-or-None).
    cands = []
    for entry in supplied:
        pid = _as_int(entry) if str(entry).strip().isdigit() else None
        if pid is not None:
            prow = db.get_prompt(pid)
            if not prow:
                return "refused: no prompt %s is stored." % entry
            cands.append(("prompt #%d '%s'" % (pid, prow["name"]),
                          prow["text"], prow["id"]))
        else:
            if len(entry) > _MAX_PROMPT_CHARS:
                return ("refused: a candidate exceeds the %d-character "
                        "cap." % _MAX_PROMPT_CHARS)
            cands.append(("candidate", entry, None))
    if variants:
        base_name = base_row["name"] if base_row else "base"
        for i, variant in enumerate(_VARIANTS, 1):
            cands.append(("variant %d: %s" % (i, variant),
                          base_text + "\n" + variant, None))
    total_calls = 0
    if propose_count:
        rewrites, err = _propose_rewrites(base_text, propose_count,
                                          model, _opener)
        if err:
            return err
        total_calls += 1
        for i, rw in enumerate(rewrites, 1):
            cands.append(("rewrite %d" % i, rw, None))
    if not cands:
        return ("refused: nothing to tune against — supply candidates, "
                "pass variants=1, or pass propose=1.")
    # The base prompt itself is always a candidate: the loop must be
    # able to say the original was already best.
    cands.insert(0, ("base prompt", base_text,
                     base_row["id"] if base_row else None))
    # Ranking runs on the TUNING set only. The held-out set is never
    # tuned on: no candidate is selected, ranked or compared on it —
    # it is scored only afterwards, by the gate.
    results = []
    for label, ctext, pid in cands:
        scores, calls, errs = _run_candidate(
            db, ctext, train, metric, model, _opener)
        total_calls += calls
        results.append({"label": label, "text": ctext, "prompt_id": pid,
                        "mean": _mean(scores), "failed": errs})
    # Rank: best mean first; ties go to the shorter prompt, then to
    # the earlier candidate. Stated here so the ranking is auditable.
    ranked = sorted(results, key=lambda r: (-r["mean"], len(r["text"]),
                                            results.index(r)))
    winner = ranked[0]
    ranking = [{"label": r["label"], "mean": round(r["mean"], 3),
                "failed": r["failed"]} for r in ranked]
    if winner is results[0]:
        # The base won on the tuning set: nothing new to promote, so
        # nothing changes — no set_best, no set_current, no new
        # prompt stored. The run is still recorded, with the split
        # and the reason, so the no-change is checkable.
        gate = {"train_examples": len(train),
                "held_out_examples": len(held_out),
                "winner_held_out_mean": None,
                "base_held_out_mean": None,
                "delta": None,
                "min_improvement": _MIN_IMPROVEMENT,
                "max_regression": _MAX_REGRESSION,
                "promoted": False,
                "reason": "winner is the base prompt — already best "
                          "on the tuning set; nothing promoted"}
        run = db.add_run("tune", base_row["id"] if base_row else None,
                         did, metric, model_name,
                         {"ranking": ranking, "gate": gate},
                         winner["prompt_id"], total_calls)
        if not run:
            return "the run was not recorded (the re-read disagreed)."
        db.record("tune", "dataset #%d" % did,
                  "tuned '%s' on %d tuning + %d held-out examples "
                  "(%s): base prompt already best at %.3f, nothing "
                  "promoted, %d LM calls"
                  % (base_row["name"] if base_row else "inline",
                     len(train), len(held_out), metric, winner["mean"],
                     total_calls))
        lines = ["Tuning: base '%s' on dataset #%d '%s' — %d examples "
                 "(%d tuning + %d held-out), %s, %d LM calls. Base "
                 "prompt was already best at %.3f; nothing promoted, "
                 "current unchanged:"
                 % (base_row["name"] if base_row else "inline",
                    did, ds["name"], len(examples), len(train),
                    len(held_out), metric, total_calls, winner["mean"])]
        for i, r in enumerate(ranked, 1):
            lines.append("  %d. %.3f — %s%s" % (
                i, r["mean"], r["label"],
                " (%d calls failed)" % r["failed"] if r["failed"] else ""))
        lines.append("Held-out gate: not run — the winner is the base "
                     "prompt, so there is nothing to promote.")
        return "\n".join(lines)
    # The winner is a NEW prompt: the gate scores it and the base on
    # the held-out set, and promotion needs a genuine held-out
    # improvement. The held-out examples are scored here, after
    # ranking — never used to select the winner.
    winner_scores, winner_calls, winner_errs = _run_candidate(
        db, winner["text"], held_out, metric, model, _opener)
    total_calls += winner_calls
    base_scores, base_calls, base_errs = _run_candidate(
        db, base_text, held_out, metric, model, _opener)
    total_calls += base_calls
    winner_held = _mean(winner_scores)
    base_held = _mean(base_scores)
    promote, reason = _gate_decision(winner_held, base_held)
    gate = {"train_examples": len(train),
            "held_out_examples": len(held_out),
            "winner_held_out_mean": round(winner_held, 3),
            "base_held_out_mean": round(base_held, 3),
            "delta": round(winner_held - base_held, 3),
            "min_improvement": _MIN_IMPROVEMENT,
            "max_regression": _MAX_REGRESSION,
            "promoted": promote,
            "reason": reason,
            "winner_held_out_failed": winner_errs,
            "base_held_out_failed": base_errs}
    if not promote:
        # Refused, honestly, with the numbers: the candidate is NOT
        # stored, NOT made best, NOT made current. The run is still
        # recorded, so the refusal is checkable afterwards.
        run = db.add_run("tune", base_row["id"] if base_row else None,
                         did, metric, model_name,
                         {"ranking": ranking, "gate": gate}, None,
                         total_calls)
        if not run:
            return "the run was not recorded (the re-read disagreed)."
        db.record("tune_refused", "dataset #%d" % did,
                  "tune of '%s' refused by the held-out gate: %s "
                  "(%d tuning + %d held-out examples, %s, %d LM calls)"
                  % (base_row["name"] if base_row else "inline", reason,
                     len(train), len(held_out), metric, total_calls))
        lines = ["Tuning refused: the winning candidate '%s' was NOT "
                 "promoted — %s. Base '%s' on dataset #%d '%s', %d "
                 "examples (%d tuning + %d held-out), %s, %d LM calls:"
                 % (winner["label"], reason,
                    base_row["name"] if base_row else "inline",
                    did, ds["name"], len(examples), len(train),
                    len(held_out), metric, total_calls)]
        for i, r in enumerate(ranked, 1):
            lines.append("  %d. %.3f — %s%s" % (
                i, r["mean"], r["label"],
                " (%d calls failed)" % r["failed"] if r["failed"] else ""))
        lines.append("Held-out gate: winner %.3f vs base %.3f "
                     "(delta %.3f); min-improvement %.3f, "
                     "max-regression %.3f. Nothing was stored, nothing "
                     "was made current."
                     % (winner_held, base_held,
                        winner_held - base_held, _MIN_IMPROVEMENT,
                        _MAX_REGRESSION))
        return "\n".join(lines)
    winner_id = winner["prompt_id"]
    if winner_id is None:
        base_name = base_row["name"] if base_row else "tuned prompt"
        short = winner["label"].split(":")[0]
        wname = "%s (%s)" % (base_name, short)
        if len(wname) > _MAX_NAME_CHARS:
            wname = wname[:_MAX_NAME_CHARS]
        existing = db.find_prompt_by_text(winner["text"])
        if existing:
            winner_id = existing["id"]
        else:
            wrow = db.add_prompt(wname, winner["text"])
            if not wrow:
                return ("the winning prompt was not stored (the re-read "
                        "disagreed). Nothing was promoted.")
            winner_id = wrow["id"]
    # The stored best score is the HELD-OUT mean — the evidence that
    # justified the promotion — not the tuning mean.
    db.set_best(winner_id, winner_held, did)
    current = db.set_current(winner_id)
    if not current:
        return "the winner could not be made current. Nothing was promoted."
    run = db.add_run("tune", base_row["id"] if base_row else None, did,
                     metric, model_name, {"ranking": ranking,
                                          "gate": gate}, winner_id,
                     total_calls)
    if not run:
        return "the run was not recorded (the re-read disagreed)."
    db.record("tune", "dataset #%d" % did,
              "tuned '%s' on %d tuning + %d held-out examples (%s): "
              "winner '%s' promoted at held-out %.3f vs base %.3f, "
              "%d LM calls" % (base_row["name"] if base_row else "inline",
                               len(train), len(held_out), metric,
                               current["name"], winner_held, base_held,
                               total_calls))
    lines = ["Tuning: base '%s' on dataset #%d '%s' — %d examples "
             "(%d tuning + %d held-out), %s, %d LM calls. Winner: "
             "prompt #%d '%s' at %.3f, now current:"
             % (base_row["name"] if base_row else "inline",
                did, ds["name"], len(examples), len(train),
                len(held_out), metric, total_calls, winner_id,
                current["name"], winner["mean"])]
    for i, r in enumerate(ranked, 1):
        lines.append("  %d. %.3f — %s%s" % (
            i, r["mean"], r["label"],
            " (%d calls failed)" % r["failed"] if r["failed"] else ""))
    lines.append("Held-out gate: PASSED — winner %.3f vs base %.3f "
                 "(improvement %.3f, threshold %.3f). The stored best "
                 "score is the held-out mean, not the tuning mean."
                 % (winner_held, base_held, winner_held - base_held,
                    _MIN_IMPROVEMENT))
    return "\n".join(lines)


def _do_variants():
    lines = ["Built-in variant bank (%d) — each is appended to the base "
             "prompt to form a candidate:" % len(_VARIANTS)]
    for i, variant in enumerate(_VARIANTS, 1):
        lines.append("  %d. %s" % (i, variant))
    return "\n".join(lines)


def _do_propose(db, prompt_id, text, limit, model, _opener):
    if not _lm_key():
        return _no_key_refusal()
    row, base_text, err = _resolve_prompt(db, prompt_id, text)
    if err:
        return err
    count = _as_count(limit, 3, _MAX_PROPOSE)
    rewrites, err = _propose_rewrites(base_text, count, model, _opener)
    if err:
        return err
    db.record("propose", "prompt #%d" % row["id"] if row else "inline",
              "proposed %d rewrites of '%s'" % (
                  len(rewrites), row["name"] if row else "inline"))
    lines = ["Proposed rewrites of '%s' (%d) — not stored; add one "
             "with action 'prompt' to keep it:" % (
                 row["name"] if row else "inline", len(rewrites))]
    for i, rw in enumerate(rewrites, 1):
        lines.append("  %d. %s" % (i, rw))
    return "\n".join(lines)


def _do_status(db):
    ok, note = db.verify_chain()
    current = db.get_current()
    key = _lm_key()
    lines = ["Prompt tuning status:",
             "prompts: %d (current: %s)" % (
                 db.count_prompts(),
                 ("#%d '%s'" % (current["id"], current["name"]))
                 if current else "none"),
             "datasets: %d" % db.count_datasets(),
             "runs: %d" % db.count_runs(),
             "variant bank: %d" % len(_VARIANTS),
             "held-out gate: 1 in %d examples held out; promote needs "
             "+%.2f on held-out, no regression tolerated"
             % (_HELD_OUT_EVERY, _MIN_IMPROVEMENT),
             "LM: %s (model %s)" % (
                 "key configured" if key else "NO KEY CONFIGURED — set one in Settings",
                 _lm_model("")),
             "ledger: %s" % note,
             "chain intact: %s" % ("yes" if ok else "NO — " + note)]
    return "\n".join(lines)


def _do_events(db, limit):
    rows = db.list_events(limit or 20)
    if not rows:
        return "No prompt-tuning events yet."
    lines = ["Prompt-tuning ledger (%d shown, newest first):" % len(rows)]
    for _id, ts, kind, obj, detail in rows:
        lines.append("  #%d %s %s — %s" % (_id, kind, obj, detail))
    return "\n".join(lines)


def prompt_tune(action="", mode="", prompt_id=None, name="", text="",
                dataset_id=None, examples="", metric="f1", candidates="",
                variants=None, propose=None, limit=None, model="",
                detail=None, _opener=None, _db_path=None) -> str:
    """Public entry: one call, one connection, closed before returning.

    Every funnel call opens the prompt store + ledger, works, and
    closes it — a leaked connection would hold a SQLite lock across
    calls and block the next write.
    """
    action = str(action or "").strip().lower()
    mode = str(mode or "").strip().lower()
    if metric is None:
        metric = "f1"
    metric = str(metric).strip().lower()
    db = _PromptTuneDB(_db_path)
    try:
        if action in ("status", "events"):
            if action == "status":
                return _do_status(db)
            return _do_events(db, limit)
        if action == "variants":
            return _do_variants()
        if action == "prompt":
            return _do_prompt(db, mode, prompt_id, name, text, limit)
        if action == "dataset":
            return _do_dataset(db, mode, dataset_id, name, examples,
                               limit)
        if action == "evaluate":
            return _do_evaluate(db, prompt_id, text, dataset_id, metric,
                                limit, model, _opener)
        if action == "tune":
            return _do_tune(db, prompt_id, text, dataset_id, candidates,
                            variants, propose, metric, limit, model,
                            _opener)
        if action == "propose":
            return _do_propose(db, prompt_id, text, limit, model, _opener)
        return ("refused: action must be 'prompt', 'dataset', "
                "'evaluate', 'tune', 'variants', 'propose', 'status' "
                "or 'events'.")
    finally:
        db.close()
