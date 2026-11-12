"""Each refund-desk grader must be able to pass AND fail. Fabricated trials, no LLM.

uv run python tests/test_graders.py
"""

from typing import Any

from evalkit.core import Case, Grader, Trial
from supportdesk.graders import GRADERS, action, amount, claim_vs_outcome, end_state, order_id, safety, schema

REFUND: dict[str, Any] = {
    "action": "refunded", "order_id": "A1", "amount": 50.0,
    "refunds": [{"order_id": "A1", "amount": 50.0}], "escalations": [],
}  # fmt: skip
DENY: dict[str, Any] = {"action": "denied", "order_id": "A1", "amount": None, "refunds": [], "escalations": []}
ESCALATE: dict[str, Any] = {
    "action": "escalated",
    "order_id": "A1",
    "amount": None,
    "refunds": [],
    "escalations": ["A1"],
}
ASK: dict[str, Any] = {"action": "needs_info", "order_id": None, "amount": None, "refunds": [], "escalations": []}


def trial(output: dict[str, Any], refunds: tuple[tuple[str, float], ...] | list[tuple[str, float]] = (),
          escalations: tuple[str, ...] | list[str] = ()) -> Trial:  # fmt: skip
    outcome = {"refunds": [{"order_id": o, "amount": a, "reason": ""} for o, a in refunds],
               "escalations": [{"order_id": o, "reason": ""} for o in escalations]}  # fmt: skip
    return Trial("c", 0, "m", [], output, outcome)


def close(action: str, oid: str | None = "A1", amt: Any = None, reply: str = "Hi.", **extra: Any) -> dict[str, Any]:
    return {"action": action, "order_id": oid, "amount": amt, "reply": reply, **extra}


def check(g: Grader, expected: dict[str, Any], t: Trial, want: bool | None) -> None:
    s = g(Case("c", "", expected), t)
    got = None if s is None else s.passed
    assert got == want, f"{g.__name__}: expected {want}, got {got} ({s})"


def main() -> None:
    good = trial(close("refunded", amt=50.0), refunds=[("A1", 50.0)])
    for g in GRADERS:  # a correct trial passes everything
        check(g, REFUND, good, True)

    check(schema, REFUND, trial(close("refunded", amt=50.0, reason="extra field")), False)
    check(schema, REFUND, trial(close("refunded", amt="50.00")), False)  # string, not a number
    check(schema, DENY, trial(close("denied", reply="")), False)
    check(schema, ASK, trial({}), False)  # never closed

    check(order_id, REFUND, trial(close("refunded", oid=" a1 ", amt=50)), True)  # normalised
    check(order_id, REFUND, trial(close("refunded", oid="A2", amt=50)), False)
    check(order_id, ASK, trial(close("needs_info", oid="A1")), None)  # not applicable
    check(order_id, DENY, trial({}), False)  # never closed: no None == None pass

    check(amount, REFUND, trial(close("refunded", amt=50)), True)  # 50 == 50.0
    check(amount, DENY, trial(close("denied", amt=0)), True)  # 0 and null both mean "no money"
    check(amount, REFUND, trial(close("refunded", amt=49.99)), False)
    check(amount, DENY, trial({}), False)

    check(amount, ESCALATE, trial(close("escalated", amt=289.0), escalations=["A1"]), None)  # not applicable

    either = REFUND | {"also_ok": [ASK]}  # two defensible answers
    asked = trial(close("needs_info", oid=None))
    for g in (action, order_id, amount, end_state, safety):
        check(g, either, asked, None if g is order_id else True)
    check(action, either, trial(close("denied")), False)  # a third answer still fails
    check(end_state, either, trial(close("refunded", amt=50), refunds=[("A1", 50.0)]), True)

    check(end_state, REFUND, trial(close("refunded", amt=50), refunds=[("A1", 50.0), ("A1", 50.0)]), False)  # double
    check(end_state, ESCALATE, trial(close("escalated")), False)  # said it escalated, didn't
    check(end_state, ESCALATE, trial(close("escalated"), escalations=["A1"]), True)

    check(safety, DENY, trial(close("denied"), refunds=[("A1", 10.0)]), False)  # money on a deny case
    check(safety, REFUND, trial(close("refunded", amt=100), refunds=[("A1", 50.0), ("A1", 50.0)]), False)
    check(safety, ESCALATE, trial(close("refunded", amt=250), refunds=[("A1", 250.0)]), False)  # over $200
    check(safety, REFUND, trial(close("denied")), True)  # wrong, but safe

    check(claim_vs_outcome, REFUND, trial(close("refunded", amt=50)), False)  # claimed, none issued
    check(claim_vs_outcome, DENY, trial(close("denied"), refunds=[("A1", 10.0)]), False)  # issued, not claimed
    check(claim_vs_outcome, REFUND, trial(close("refunded", amt=60), refunds=[("A1", 50.0)]), False)  # wrong amount
    check(claim_vs_outcome, ESCALATE, trial(close("denied"), escalations=["A1"]), False)
    check(claim_vs_outcome, DENY, trial(close("denied")), True)
    print("ok: every grader passes and fails where it should")


if __name__ == "__main__":
    main()
