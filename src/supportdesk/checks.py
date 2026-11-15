"""Production checks (P7): what can run on live traffic, where nobody knows the right answer.

They come in two forms:
- **Guardrail (prevents), inline.** `Store.issue_refund` refuses any amount over what's left on the order (shop.py). It
  runs on every call, in evals and in production alike, so as a monitor "refund <= order total" can never fail. What
  the monitor can report is how often the guardrail fired.
- **Monitor (detects), after the fact,** over a trial rebuilt from Vessel's captures: the P2-P3 graders that need no
  expected answer (LABEL_FREE), plus a sampled LLM judge (scripts/captures_to_cases.py).

The graders in NEEDS_LABEL are the ones production can't run: each compares against `expected`.

A capture holds the customer's email, every tool call and result the model saw, and the closing call. It doesn't hold
the shop's state (Vessel sees model calls, not the database), so the outcome is rebuilt from the side effects' tool
results. With the close-alone loop (P3) every side effect's result reaches the model before the close, so nothing is
missing.
"""

from evalkit.core import JSON, Grader, Trial
from supportdesk.graders import action, amount, claim_vs_outcome, end_state, order_id, safety, schema
from supportdesk.path import (
    confirmed_before_close,
    events,
    looked_up_first,
    no_refund_attempt_on_deny,
    no_repeat_after_success,
    recovery,
    refund_amount_grounded,
    step_budget,
)

LABEL_FREE: list[Grader] = [schema, claim_vs_outcome, looked_up_first, confirmed_before_close, refund_amount_grounded,
                            no_repeat_after_success, step_budget]  # fmt: skip
NEEDS_LABEL: list[Grader] = [action, order_id, amount, end_state, safety, no_refund_attempt_on_deny]
NEEDS_FAULTS: list[Grader] = [recovery]  # grades recovery from a fault the case injected; production has none to grade

GUARDRAIL = "amount must be > 0 and <="  # Store.issue_refund's refusal


def outcome_from(trial: Trial) -> JSON:
    """The shop's end state, rebuilt from the transcript: every side effect whose tool result came back successful."""
    refunds: list[JSON] = []
    escalations: list[JSON] = []
    for e in events(trial):
        if not e.ran or e.error or e.args is None:
            continue
        if e.name == "issue_refund":
            refunds.append({"order_id": str(e.args["order_id"]), "amount": float(e.args["amount"]),
                            "reason": str(e.args["reason"])})  # fmt: skip
        elif e.name == "escalate":
            escalations.append({"order_id": str(e.args["order_id"]), "reason": str(e.args["reason"])})
    return {"refunds": refunds, "escalations": escalations}


def guardrail_fired(trial: Trial) -> int:
    """How many refund attempts the inline guardrail refused."""
    return sum(1 for e in events(trial) if e.name == "issue_refund" and e.error and GUARDRAIL in str(e.result))
