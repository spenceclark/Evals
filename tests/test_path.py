"""Path and recovery graders on real loop transcripts: a scripted fake model drives run_agent against the real Store
(faults on), so the transcript shapes are the ones the graders will see. Each rule must pass and fail. No LLM.

    uv run python tests/test_path.py
"""

import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from evalkit.core import Call, Case, Grader, Trial, load_cases
from supportdesk.agent import Step, ToolCall, run_agent
from supportdesk.path import (
    PATH,
    confirmed_before_close,
    looked_up_first,
    no_refund_attempt_on_deny,
    no_repeat_after_success,
    recovery,
    refund_amount_grounded,
    step_budget,
    used_find_customer,
)
from supportdesk.shop import Store

CASES = {c.id: c for c in load_cases(Path("cases/full.jsonl"))}
type Call_ = tuple[str, dict[str, Any] | None]
type Turn = Sequence[Call_]


class Scripted:
    def __init__(self, turns: Sequence[Turn]) -> None:
        self.turns: Iterator[Turn] = iter(turns)
        self.n = 0

    async def step(self) -> Step:
        self.n += 1
        turn: Turn = next(self.turns, ())
        calls = [ToolCall(f"t{self.n}c{i}", name, args) for i, (name, args) in enumerate(turn)]
        return Step("", calls, "tool_use" if calls else "end", Call("fake", 1, 1, "x", 0.0))

    def tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        pass


def run(case: Case, turns: Sequence[Turn], close_alone: bool = True) -> Trial:
    store = Store(case.faults)
    r = asyncio.run(run_agent(Scripted(turns), case.input, store, close_alone=close_alone))
    return Trial(case.id, 0, "fake", r.messages, r.output, store.outcome(), r.calls, r.status, r.stop_reason)


def check(g: Grader, case: Case, t: Trial, want: bool | None, category: str | None = None) -> None:
    s = g(case, t)
    got = None if s is None else s.passed
    assert got == want, f"{g.__name__} on {case.id}: expected {want}, got {got} ({s})"
    if category:
        assert s is not None and s.reason == category, f"{g.__name__}: expected {category}, got {s}"


def close(action: str, oid: str | None, amount: float | None) -> Call_:
    return ("close_ticket", {"action": action, "order_id": oid, "amount": amount, "reply": "Hi."})


def main() -> None:
    elig = CASES["elig-01"]
    oid, amt = elig.expected["order_id"], elig.expected["amount"]
    get: Call_ = ("get_order", {"order_id": oid})
    refund: Call_ = ("issue_refund", {"order_id": oid, "amount": amt, "reason": "r"})

    good = run(elig, [[get], [refund], [close("refunded", oid, amt)]])
    for g in PATH:  # all pass, except the deny-only rule, which doesn't apply to a refund case
        check(g, elig, good, None if g is no_refund_attempt_on_deny else True)
    check(used_find_customer, elig, good, False)  # the overfit rule fails a perfectly good trial

    old_loop = run(elig, [[get], [refund, close("refunded", oid, amt)]], close_alone=False)
    check(confirmed_before_close, elig, old_loop, False)  # the P1-P3 loop honoured a mid-turn close

    # The fixed loop refuses that close: the refund runs, the model sees its result and the refusal, closes again.
    fixed = run(elig, [[get], [refund, close("refunded", oid, amt)], [close("refunded", oid, amt)]])
    refused = [m for m in fixed.messages if m["role"] == "tool" and m["name"] == "close_ticket"]
    assert len(refused) == 1 and refused[0]["error"], refused
    assert fixed.stop_reason == "close_ticket" and len(fixed.outcome["refunds"]) == 1
    for g in PATH:
        check(g, elig, fixed, None if g is no_refund_attempt_on_deny else True)
    stalled = run(elig, [[get], [refund, close("refunded", oid, amt)]])  # never closes again
    assert stalled.output == {} and stalled.stop_reason == "end_without_close", stalled.stop_reason

    blind = run(elig, [[refund], [close("refunded", oid, amt)]])
    check(looked_up_first, elig, blind, False)
    check(refund_amount_grounded, elig, blind, False)  # no lookup to ground it in

    decided_in_same_turn = run(elig, [[get, refund], [close("refunded", oid, amt)]])
    check(looked_up_first, elig, decided_in_same_turn, False)  # asked for the order and refunded before seeing it

    made_up = run(elig, [[get], [("issue_refund", {"order_id": oid, "amount": amt - 1, "reason": "r"})],
                         [close("refunded", oid, amt - 1)]])  # fmt: skip
    check(refund_amount_grounded, elig, made_up, False)

    half = round(amt / 2, 2)
    part: Call_ = ("issue_refund", {"order_id": oid, "amount": half, "reason": "r"})
    twice = run(elig, [[get], [part], [part], [close("refunded", oid, amt)]])
    check(no_repeat_after_success, elig, twice, False)

    looping = run(elig, [*([[get]] * 9), [close("denied", oid, None)]])
    check(step_budget, elig, looping, False)

    # Already refunded: the store rejects the refund, so `safety` can't fail. The path rule still sees the attempt.
    done = next(c for c in CASES.values() if "already_refunded" in c.tags)
    d_oid = done.expected["order_id"]
    tried = run(done, [[("get_order", {"order_id": d_oid})], [("issue_refund", {"order_id": d_oid, "amount": 1.0,
                       "reason": "r"})], [close("denied", d_oid, None)]])  # fmt: skip
    assert any(m["error"] for m in tried.messages if m["role"] == "tool" and m["name"] == "issue_refund")
    check(no_refund_attempt_on_deny, done, tried, False)
    check(no_refund_attempt_on_deny, elig, good, None)  # a refund case: not applicable

    # Recovery, on the get_order fault (first call fails).
    f = CASES["fault-01"]
    f_oid, f_amt = f.expected["order_id"], f.expected["amount"]
    f_get = ("get_order", {"order_id": f_oid})
    f_refund = ("issue_refund", {"order_id": f_oid, "amount": f_amt, "reason": "r"})
    ok = run(f, [[f_get], [f_get], [f_refund], [close("refunded", f_oid, f_amt)]])
    check(recovery, f, ok, True, "recovered")
    gave_up = run(f, [[f_get], [close("needs_info", f_oid, None)]])
    check(recovery, f, gave_up, False, "gave_up")
    no_close = run(f, [[f_get], [f_get], [f_refund], []])
    check(recovery, f, no_close, False, "retried_no_close")
    blind_close = run(f, [[f_get, close("denied", f_oid, None)]], close_alone=False)
    check(recovery, f, blind_close, False, "never_saw_error")
    check(recovery, elig, good, None)  # no fault on this case

    # issue_refund fault: refunding and closing in the same turn means the model never sees the 503.
    r = next(c for c in CASES.values() if "issue_refund_error" in c.tags)
    r_oid, r_amt = r.expected["order_id"], r.expected["amount"]
    r_refund = ("issue_refund", {"order_id": r_oid, "amount": r_amt, "reason": "r"})
    r_get: Call_ = ("get_order", {"order_id": r_oid})
    claimed = run(r, [[r_get], [r_refund, close("refunded", r_oid, r_amt)]], close_alone=False)
    check(recovery, r, claimed, False, "never_saw_error")
    assert claimed.outcome["refunds"] == [], "the faulted refund must not have happened"
    # Same model behaviour on the fixed loop: the close is refused, the 503 is seen, the retry works.
    saw = run(r, [[r_get], [r_refund, close("refunded", r_oid, r_amt)], [r_refund], [close("refunded", r_oid, r_amt)]])
    check(recovery, r, saw, True, "recovered")
    print("ok: path and recovery rules pass and fail on real loop transcripts")


if __name__ == "__main__":
    main()
