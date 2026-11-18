"""Classify a full-suite run log into TEST-04 buckets.

Report-only, never a gate: this script always exits 0, never
edits a log, and never changes any test outcome. It implements
the classification procedure in tests/CANONICAL_CI.md as a text
scan of a unittest run log (tracebacks + failure lines):

  budget          -- failure body contains 'kind': 'budget'
                     or "safety budget tripped"
  budget-mention  -- body mentions budget but does not prove
                     it (needs an alone-rerun on freshly
                     restored data to confirm budget-masked)
  receipt-repeat  -- the TEST-01 signature: a timeout leg
                     refused ('refused' vs 'timeout') or an
                     unknown-prior refusal
  order-chatfix   -- the TEST-03 signature: _w9_error_ladder
                     missing / ladder None in a chatfix test
  known-release   -- the carried test_release_w10
                     KeyError: 'reason' leaver (cite the
                     baseline log; a same-name failure with a
                     different signature falls through to NEW)
  new             -- anything else; NEW and blocks until it
                     has its own alone-rerun proof or fix

Usage: python tests/classify_run.py <run-log>
       python tests/classify_run.py --gate <run-log>

Stdlib only. Not a test module (no test_ prefix) and not
imported by the runner.

Default is report-only: always exits 0, never edits a log,
never changes any test outcome. --gate is the one loud mode:
it exits 1 when the classification contains a NEW failure or
a budget-mention (both block until proven or fixed — see
tests/CANONICAL_CI.md buckets 2 and 6), and 0 otherwise.
--gate is a report on the classification, not a substitute
for the coordinator's classification review: a gate exit 0
is not a ship decision. Never wire it into run_tests.py as
an exit-code condition — the runner stays a pure test run,
and the gate is run by hand (or by CI) over the finished log.
"""

from __future__ import annotations

import re
import sys

FAIL_RE = re.compile(
    r"^(FAIL|ERROR): (\S+) \(([^)]+)\)\s*$")


def _bucket(name: str, body: str) -> str:
    low = body.lower()
    if "kind': 'budget'" in body or "kind\": \"budget\"" in body \
            or "safety budget tripped" in low:
        return "budget"
    if "chatfix" in name and ("_w9_error_ladder" in body
                              or "unexpectedly none" in low):
        return "order-chatfix"
    if ("refused" in low and "timeout" in low) \
            or "unknown outcome" in low or "unknown-prior" in low:
        return "receipt-repeat"
    if "release_w10" in name and "keyerror: 'reason'" in low:
        return "known-release"
    if "budget" in low:
        return "budget-mention"
    return "new"


def classify(text: str) -> list[tuple[str, str, str]]:
    """Return [(status, test, bucket)] for each FAIL/ERROR."""
    entries: list[tuple[str, str, str]] = []
    current: list[str] | None = None
    current_name = ""
    current_status = ""
    for line in text.splitlines():
        m = FAIL_RE.match(line)
        if m:
            if current is not None:
                entries.append((current_status, current_name,
                                _bucket(current_name,
                                        "\n".join(current))))
            # group(3) is the full dotted id
            # (tests.test_chatfix.TestChatfix.test_callback_unit):
            # bucket rules match on the module path, so the bare
            # method name alone (group 2) is not enough.
            current_status, current_name = m.group(1), m.group(3)
            current = [line]
        elif current is not None:
            current.append(line)
            if line.startswith("Ran ") or line.startswith("OK") \
                    or line.startswith("FAILED"):
                entries.append((current_status, current_name,
                                _bucket(current_name,
                                        "\n".join(current))))
                current = None
    if current is not None:
        entries.append((current_status, current_name,
                        _bucket(current_name, "\n".join(current))))
    return entries


def main(argv: list[str]) -> int:
    gate = False
    if len(argv) == 3 and argv[1] == "--gate":
        gate = True
        argv = [argv[0], argv[2]]
    if len(argv) != 2:
        print("usage: python tests/classify_run.py [--gate] <run-log>")
        return 0
    try:
        text = open(argv[1], encoding="utf-8", errors="replace").read()
    except OSError as exc:
        print(f"cannot read {argv[1]}: {exc}")
        return 0
    ran = re.search(r"Ran (\d+) tests?", text)
    print(f"tests run: {ran.group(1) if ran else 'unknown (partial log?)'}")
    entries = classify(text)
    buckets: dict[str, int] = {}
    for _status, _name, bucket in entries:
        buckets[bucket] = buckets.get(bucket, 0) + 1
    for bucket in ("budget", "budget-mention", "receipt-repeat",
                   "order-chatfix", "known-release", "new"):
        print(f"{bucket}: {buckets.get(bucket, 0)}")
    for status, name, bucket in entries:
        print(f"  [{bucket}] {status}: {name}")
    if buckets.get("new", 0):
        print("NEW failures present: block until each has an "
              "alone-rerun proof or its own fix (see "
              "tests/CANONICAL_CI.md).")
    elif buckets.get("budget-mention", 0):
        print("Budget-mention failures present: confirm each by "
              "alone-rerun on freshly restored data before "
              "calling the run classified clean.")
    else:
        print("No NEW failures by this scan. (A scan is not a "
              "ship decision — the coordinator's classification "
              "review is.)")
    if gate and (buckets.get("new", 0) or buckets.get("budget-mention", 0)):
        # --gate only: the classification itself blocks, so the
        # exit code says so. Default mode never reaches here.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
