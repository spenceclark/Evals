"""Production checks: every LABEL_FREE grader runs with no expected answer, every NEEDS_LABEL one can't, and the outcome
rebuilt from a transcript equals the store's. Scripted model over the real loop and Store, so no LLM.

    uv run python tests/test_checks.py
"""

import asyncio
from dataclasses import replace

from test_path import CASES, Scripted, close

from evalkit.core import Trial, grade
from supportdesk.agent import run_agent
from supportdesk.checks import LABEL_FREE, NEEDS_LABEL, guardrail_fired, outcome_from
from supportdesk.shop import Store


def main() -> None:
    case = CASES["elig-01"]  # refund $26.00 on A2099
    unlabelled = replace(case, expected={})
    oid, amt = case.expected["order_id"], case.expected["amount"]

    scripts = {
        "clean": [[("get_order", {"order_id": oid})], [("issue_refund", {"order_id": oid, "amount": amt,
                  "reason": "r"})], [close("refunded", oid, amt)]],
        "made-up amount": [[("get_order", {"order_id": oid})], [("issue_refund", {"order_id": oid, "amount": 10.0,
                           "reason": "r"})], [close("refunded", oid, 10.0)]],
        "over the total": [[("get_order", {"order_id": oid})], [("issue_refund", {"order_id": oid, "amount": 999.0,
                           "reason": "r"})], [close("refunded", oid, 999.0)]],
        "escalated": [[("get_order", {"order_id": oid})], [("escalate", {"order_id": oid, "reason": "r"})],
                      [close("escalated", oid, None)]],
    }  # fmt: skip
    t: Trial | None = None
    for name, turns in scripts.items():
        store = Store(case.faults)
        r = asyncio.run(run_agent(Scripted(turns), case.input, store))
        t = Trial(case.id, 0, "scripted", r.messages, r.output, store.outcome(), r.calls, r.status, r.stop_reason)
        assert outcome_from(t) == store.outcome(), (name, outcome_from(t), store.outcome())
        scores = {s.grader: s.passed for s in grade(unlabelled, t, LABEL_FREE)}  # raises if a check reads a label
        print(f"{name:<15} guardrail {guardrail_fired(t)}  failed: {[g for g, ok in scores.items() if not ok] or '-'}")
        match name:
            case "clean" | "escalated":
                assert all(scores.values()) and guardrail_fired(t) == 0
            case "made-up amount":
                assert not scores["refund_amount_grounded"] and scores["claim_vs_outcome"]
            case _:
                assert guardrail_fired(t) == 1 and not scores["claim_vs_outcome"]

    assert t is not None
    for g in NEEDS_LABEL:  # on the last scripted trial, which has a transcript, so no grader returns early
        try:
            g(unlabelled, t)
        except KeyError, TypeError:
            continue
        raise AssertionError(f"{g.__name__} ran without a label: it belongs in LABEL_FREE")
    print("ok: checks; label-free graders need no label, the rest can't run without one, outcome rebuilt exactly")


if __name__ == "__main__":
    main()
