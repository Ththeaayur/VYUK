"""v5.72 (Business track, wave 18, BZ-6.21) — vendor-receives-
nothing counters (the INV-19-compliant shape).

The trust pack's quantitative claim is not "we promise
vendors get nothing" — it is a counter that can prove it:
every redaction performed, every egress attempt blocked, and
every vendor call made, counted in-process. The INV-19 shape
holds while vendor_calls == 0; assert_no_vendor_receipt()
fails loudly the moment it is not. Counters are process-local
(no persistence claim, no cross-run ledger — a persisted
counter would itself be a claim needing its own cell).

Stdlib only. No network. No funnel registration.
"""

from __future__ import annotations


class VendorReceiptError(RuntimeError):
    """A vendor received something — the INV-19 shape broke."""


class VendorCounters:
    """In-process counters for the vendor-receives-nothing claim."""

    def __init__(self):
        self._counts = {"redactions": 0, "egress_blocked": 0,
                        "vendor_calls": 0}

    def record_redaction(self, n: int = 1) -> int:
        self._counts["redactions"] += _nonneg(n, "redactions")
        return self._counts["redactions"]

    def record_egress_blocked(self, n: int = 1) -> int:
        self._counts["egress_blocked"] += _nonneg(n, "egress_blocked")
        return self._counts["egress_blocked"]

    def record_vendor_call(self, n: int = 1) -> int:
        self._counts["vendor_calls"] += _nonneg(n, "vendor_calls")
        return self._counts["vendor_calls"]

    def report(self) -> dict:
        return {"redactions": self._counts["redactions"],
                "egress_blocked": self._counts["egress_blocked"],
                "vendor_calls": self._counts["vendor_calls"],
                "vendor_receives_nothing": self._counts["vendor_calls"] == 0}

    def assert_no_vendor_receipt(self) -> None:
        if self._counts["vendor_calls"] != 0:
            raise VendorReceiptError(
                "vendor_calls is %d, not 0 — the vendor-receives-"
                "nothing shape does not hold"
                % self._counts["vendor_calls"])


def _nonneg(n, where: str) -> int:
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("%s count must be a non-negative integer" % where)
    return n
