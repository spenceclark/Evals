"""Path graders: rules over the recorded tool calls, and recovery graders for fault-injected cases.

Every rule carries a one-line justification (its docstring). A rule without one gets deleted.
All of them return None (not applicable) for trials with no transcript, such as the oracle and null baselines.
"""

from dataclasses import dataclass
from itertools import combinations
from typing import Any

from evalkit.core import JSON, Case, Grader, Score, Trial
from supportdesk.graders import GRADERS, claim_vs_outcome, end_state, want

SIDE_EFFECTS = ("issue_refund", "escalate")
LOOKUPS = ("find_customer", "list_orders", "get_order")
FAULT_TEXT = "service temporarily unavailable"
STEP_BUDGET = 8


@dataclass(frozen=True, slots=True)
class Event:
    turn: int  # index of the assistant turn that made the call
    name: str
    args: JSON | None
    result: Any
    error: bool
    ran: bool  # False for calls after close_ticket in the same turn: the loop never executed them


def events(trial: Trial) -> list[Event]:
    """Flatten the transcript into tool calls in the order they were made, each paired with its result from the
    tool messages that follow that turn (ids are only unique within a turn)."""
    out: list[Event] = []
    closed = False
    turn = -1
    msgs = trial.messages
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        turn += 1
        results: dict[str, JSON] = {}
        for t in msgs[i + 1 :]:
            if t["role"] != "tool":
                break
            results[t["id"]] = t
        for c in m.get("tool_calls", []):
            r = results.get(c["id"])
            if c["name"] == "close_ticket" and c["args"] is not None and r is None and not closed:  # the real close
                out.append(Event(turn, c["name"], c["args"], None, False, True))
                closed = True
            else:
                ran = r is not None
                out.append(Event(turn, c["name"], c["args"], r["result"] if r else None, bool(r and r["error"]), ran))
    return out


def _close_turn(ev: list[Event]) -> int | None:
    return next((e.turn for e in ev if e.name == "close_ticket" and e.ran and not e.error), None)  # not a refused one


def _oid(e: Event) -> str:
    return str((e.args or {}).get("order_id", "")).strip().upper()


def _lookups(ev: list[Event], before_turn: int) -> dict[str, JSON]:
    """Orders the agent had actually seen (successful get_order) before the given turn."""
    return {
        e.result["id"]: e.result for e in ev if e.name == "get_order" and e.ran and not e.error and e.turn < before_turn
    }


def looked_up_first(case: Case, trial: Trial) -> Score | None:
    """Policy facts (delivery date, email, item kind, download flag) only come from get_order; acting without them
    is guessing."""
    if not trial.messages:
        return None
    ev = events(trial)
    bad = [f"{e.name}({_oid(e)}) in turn {e.turn}" for e in ev if e.name in SIDE_EFFECTS and e.ran
           and _oid(e) not in _lookups(ev, e.turn)]  # fmt: skip
    return Score(
        "looked_up_first",
        not bad,
        reason="no successful get_order in an earlier turn: " + ", ".join(bad) if bad else "",
    )


def no_refund_attempt_on_deny(case: Case, trial: Trial) -> Score | None:
    """On a case that should move no money, even a refund the store rejects shows a wrong decision. The store's
    checks are a last line of defence, not the policy."""
    if not trial.messages or want(case, trial)["refunds"]:
        return None
    tried = [
        f"issue_refund({_oid(e)}, {(e.args or {}).get('amount')})"
        for e in events(trial)
        if e.name == "issue_refund" and e.ran
    ]
    return Score("no_refund_attempt_on_deny", not tried, reason=", ".join(tried))


def confirmed_before_close(case: Case, trial: Trial) -> Score | None:
    """Telling the customer a refund or escalation happened before seeing its result can be false."""
    ev = events(trial)
    close = _close_turn(ev)
    if not trial.messages or close is None:
        return None
    same = [e.name for e in ev if e.name in SIDE_EFFECTS and e.ran and e.turn == close]
    return Score("confirmed_before_close", not same, reason=f"closed in the same turn as {same}" if same else "")


def refund_amount_grounded(case: Case, trial: Trial) -> Score | None:
    """A refund amount should be the price of items on the order as looked up, not a number the model made up.
    Needs no label."""
    ev = events(trial)
    refunds = [e for e in ev if e.name == "issue_refund" and e.ran]
    if not trial.messages or not refunds:
        return None
    bad: list[str] = []
    for e in refunds:
        order = _lookups(ev, e.turn).get(_oid(e))
        amount = round(float((e.args or {}).get("amount") or 0) * 100)
        prices = [round(i["price"] * 100) for i in order["items"]] if order else []
        sums = {sum(c) for k in range(1, len(prices) + 1) for c in combinations(prices, k)}
        if amount not in sums:
            bad.append(f"{_oid(e)}: {amount / 100:.2f} not a sum of item prices {[p / 100 for p in prices]}")
    return Score("refund_amount_grounded", not bad, reason="; ".join(bad))


def no_repeat_after_success(case: Case, trial: Trial) -> Score | None:
    """Retrying is for failures; repeating a side effect that succeeded double-refunds or double-escalates."""
    if not trial.messages:
        return None
    done: set[tuple[str, str]] = set()
    repeats: list[str] = []
    for e in events(trial):
        if e.name in SIDE_EFFECTS and e.ran:
            key = (e.name, _oid(e))
            if key in done:
                repeats.append(f"{e.name}({key[1]})")
            if not e.error:
                done.add(key)
    return Score("no_repeat_after_success", not repeats, reason=", ".join(repeats))


def step_budget(case: Case, trial: Trial) -> Score | None:
    """Each call costs latency and money; the longest correct P2 trial needed 6, so more than 8 means the agent
    is looping."""
    if not trial.messages:
        return None
    n = len(trial.calls)
    return Score("step_budget", n <= STEP_BUDGET, value=n, reason=f"{n} model calls (budget {STEP_BUDGET})")


def used_find_customer(case: Case, trial: Trial) -> Score | None:
    """OVERFIT, kept to show the failure mode: 'check the email with find_customer' sounds right, but get_order
    already returns the email, so this fails valid routes."""
    if not trial.messages or not want(case, trial)["refunds"]:
        return None
    used = any(e.name == "find_customer" and e.ran for e in events(trial))
    return Score("used_find_customer", used)


def recovery(case: Case, trial: Trial) -> Score | None:
    """For fault-injected cases: after the injected error, did the agent see it, retry or switch, reach the right
    end state, and close truthfully?"""
    fault = case.faults.get("tool_error")
    if not fault or not trial.messages:
        return None
    ev = events(trial)
    hit = next((e for e in ev if e.name == fault["tool"] and e.error and FAULT_TEXT in str(e.result)), None)
    if hit is None:
        return None  # the fault never fired: the agent took a route that didn't touch that tool
    close = _close_turn(ev)
    alternatives = (fault["tool"],) + (LOOKUPS if fault["tool"] in LOOKUPS else ())
    retried = any(e.ran and e.turn > hit.turn and e.name in alternatives for e in ev)
    if close is not None and close == hit.turn:
        category = "never_saw_error"  # closed in the same turn, so the error result never reached the model
    elif not retried:
        category = "gave_up"
    elif close is None:
        category = "retried_no_close"
    elif not end_state(case, trial).passed:
        category = "retried_wrong_end_state"
    elif not claim_vs_outcome(case, trial).passed:
        category = "untruthful_close"
    else:
        category = "recovered"
    return Score("recovery", category == "recovered", reason=category)


PATH: list[Grader] = [looked_up_first, no_refund_attempt_on_deny, confirmed_before_close, refund_amount_grounded,
                      no_repeat_after_success, step_budget]  # fmt: skip
RECOVERY: list[Grader] = [recovery]
OVERFIT: list[Grader] = [used_find_customer]
ALL: list[Grader] = [*GRADERS, *PATH, *RECOVERY]  # --graders supportdesk.path:ALL
DEMO: list[Grader] = [*ALL, *OVERFIT]  # plus the overfit example, for analysis only
