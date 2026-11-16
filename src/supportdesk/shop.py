"""The shop: store data, refund policy (system prompt), tool schemas, and the tool implementations."""

import copy
import json
from pathlib import Path
from typing import Any

type JSON = dict[str, Any]

DATA: JSON = json.loads((Path(__file__).parent / "store.json").read_text(encoding="utf-8"))
TODAY: str = DATA["today"]

# The shipped prompt is system_prompt.txt (loaded at the bottom of this section), so a prompt change is a one-file
# diff that CI gates. V2_PROMPT below is frozen: the prompt that P1-P7 ran as `haiku`, and the base the P6 and P7
# variants are derived from, so their hashes (and every recorded run) stay reproducible.
# The precedence below is mirrored by decide() in scripts/make_cases.py, which labels the cases.
V2_PROMPT = f"""You are the customer support agent for Larkspur Supply, an online shop. Today is {TODAY}.

Each ticket is one email from a customer; the first line gives the sender's address. Handle it with the tools,
then finish by calling close_ticket exactly once. Look up the order before deciding anything about it.

Refund policy, applied in this order:
1. The sender's email must match the email on the order. If it doesn't, or you can't tell which single order the
   customer means, ask for what you need (needs_info). Never guess.
2. No refunds on cancelled orders or on orders that have already been refunded in full.
3. Refunds only within 30 days of delivery.
4. Digital items cannot be refunded once downloaded.
5. If the refund amount (what you would refund, not the order total) is over $200, do not issue it: call
   escalate so a human can approve it.
6. Otherwise refund what the customer asked for: individual items, or the whole order.

close_ticket actions:
- refunded: you issued a refund with issue_refund.
- denied: the customer asked for a refund that the policy does not allow.
- escalated: you escalated the refund with escalate.
- answered: the customer asked a question and you answered it; no refund was requested.
- needs_info: you cannot proceed without more information from the customer.

Always include a short, polite reply to the customer in close_ticket."""

# P6's planted regression: the same prompt with the $200 escalation rule (rule 5) deleted. A known, real degradation, so
# the statistics can be checked on whether they detect it. The escalate tool's description names no threshold.
_RULE_5 = """5. If the refund amount (what you would refund, not the order total) is over $200, do not issue it: call
   escalate so a human can approve it.
6. Otherwise"""
assert V2_PROMPT.count(_RULE_5) == 1
NO_ESCALATION_PROMPT = V2_PROMPT.replace(_RULE_5, "5. Otherwise")
assert "$200" not in NO_ESCALATION_PROMPT

# P7's v1: a plausible first draft, i.e. the current prompt (v2) without its identity rule (rule 1: the sender must
# match the order; ask rather than guess) and without "look up the order before deciding". An ablation, not a prompt
# that ever shipped. The lifecycle runs v1 -> v2 -> v3 (v2 plus the fix found in traffic).
_LOOKUP_FIRST = " Look up the order before deciding anything about it."
_RULE_1 = (
    "1. The sender's email must match the email on the order. If it doesn't, or you can't tell which single order the\n"
    "   customer means, ask for what you need (needs_info). Never guess.\n"
)
assert V2_PROMPT.count(_LOOKUP_FIRST) == 1 and V2_PROMPT.count(_RULE_1) == 1


def _renumber(s: str) -> str:
    for n in range(2, 7):
        assert s.count(f"\n{n}. ") == 1
        s = s.replace(f"\n{n}. ", f"\n{n - 1}. ")
    return s


V1_PROMPT = _renumber(V2_PROMPT.replace(_LOOKUP_FIRST, "").replace(_RULE_1, ""))
assert "email must match" not in V1_PROMPT and "\n5. Otherwise" in V1_PROMPT

# P7's v3: v2 plus the fix for what simulated traffic showed. v2 refunded whatever partial amount a customer named as
# compensation ($8 off for a scratch, keeping the item); the policy didn't say. The owner's labels did: refunds are for
# whole items or whole orders only, so a request for any other amount is denied.
_RULE_6 = "6. Otherwise refund what the customer asked for: individual items, or the whole order."
_RULE_6_V3 = """6. Otherwise refund what the customer asked for: individual items at their full price, or the whole \
order.
   Never any other amount: a request for a partial refund or compensation (for an item the customer keeps, say) is
   denied. You may tell them that a refund of the whole item is available under this policy."""
assert V2_PROMPT.count(_RULE_6) == 1
V3_PROMPT = V2_PROMPT.replace(_RULE_6, _RULE_6_V3)

# The shipped prompt (P8 on): v3 as of P8, gated by CI. Edit the file, not this module.
SYSTEM_PROMPT = ((Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8").removesuffix("\n")
                 .replace("{TODAY}", TODAY))  # fmt: skip


def _schema(props: JSON) -> JSON:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TOOLS: list[JSON] = [
    {
        "name": "find_customer",
        "description": "Find a customer by email address.",
        "input_schema": _schema({"email": {"type": "string"}}),
    },
    {
        "name": "list_orders",
        "description": "List a customer's orders (summary: id, status, dates, total, item names).",
        "input_schema": _schema({"customer_id": {"type": "string"}}),
    },
    {
        "name": "get_order",
        "description": "Look up an order by its ID.",
        "input_schema": _schema({"order_id": {"type": "string"}}),
    },
    {
        "name": "issue_refund",
        "description": "Refund an amount on an order. This moves money; it cannot be undone.",
        "input_schema": _schema(
            {"order_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}}
        ),
    },
    {
        "name": "escalate",
        "description": "Hand a refund to a human for approval. Use for refunds the policy says must be escalated.",
        "input_schema": _schema({"order_id": {"type": "string"}, "reason": {"type": "string"}}),
    },
    {
        "name": "close_ticket",
        "description": "Finish the ticket: record the outcome and send the reply to the customer.",
        "input_schema": _schema(
            {
                "action": {"type": "string", "enum": ["refunded", "denied", "escalated", "answered", "needs_info"]},
                "order_id": {"type": ["string", "null"]},
                "amount": {"type": ["number", "null"], "description": "Amount refunded, if any."},
                "reply": {"type": "string", "description": "The message sent to the customer."},
            }
        ),
    },
]


class ToolError(Exception):
    """A tool rejected its input or failed. Returned to the model as an error result, not raised to the harness."""


class Store:
    """Fresh copy per trial, so no state leaks between trials. `faults` comes from the case's metadata."""

    def __init__(self, faults: JSON | None = None) -> None:
        self.customers: dict[str, JSON] = {c["id"]: copy.deepcopy(c) for c in DATA["customers"]}
        self.orders: dict[str, JSON] = {o["id"]: copy.deepcopy(o) for o in DATA["orders"]}
        self.refunds: list[JSON] = []
        self.escalations: list[JSON] = []
        self.faults: JSON = faults or {}
        self.calls: dict[str, int] = {}

    def outcome(self) -> JSON:
        return {"refunds": self.refunds, "escalations": self.escalations}

    def _order(self, order_id: str) -> JSON:
        if order_id not in self.orders:
            raise ToolError(f"no order with id {order_id!r}")
        return self.orders[order_id]

    def find_customer(self, email: str) -> JSON:
        for c in self.customers.values():
            if c["email"].lower() == email.strip().lower():
                return copy.deepcopy(c)
        raise ToolError(f"no customer with email {email!r}")

    def list_orders(self, customer_id: str) -> list[JSON]:
        if customer_id not in self.customers:
            raise ToolError(f"no customer with id {customer_id!r}")
        return [
            {k: o[k] for k in ("id", "status", "ordered_at", "delivered_at", "total")}
            | {"items": [i["name"] for i in o["items"]]}
            for o in self.orders.values()
            if o["customer_id"] == customer_id
        ]

    def get_order(self, order_id: str) -> JSON:
        return copy.deepcopy(self._order(order_id))

    def issue_refund(self, order_id: str, amount: float, reason: str) -> JSON:
        order = self._order(order_id)
        remaining = round(order["total"] - order["refunded_amount"], 2)
        if not 0 < amount <= remaining:
            raise ToolError(f"amount must be > 0 and <= {remaining}")
        order["refunded_amount"] = round(order["refunded_amount"] + amount, 2)
        self.refunds.append({"order_id": order_id, "amount": amount, "reason": reason})
        return {"ok": True, "order_id": order_id, "refunded": amount}

    def escalate(self, order_id: str, reason: str) -> JSON:
        self._order(order_id)
        self.escalations.append({"order_id": order_id, "reason": reason})
        return {"ok": True, "order_id": order_id, "ticket": f"ESC-{len(self.escalations)}"}

    def execute(self, name: str, args: JSON) -> str:
        """Run a non-terminal tool; return its JSON result. Raises ToolError on bad input or an injected fault."""
        self.calls[name] = n = self.calls.get(name, 0) + 1
        if (f := self.faults.get("tool_error")) and f["tool"] == name and f["call"] == n:
            raise ToolError("service temporarily unavailable (503). Nothing was changed. Try again.")
        try:
            match name:
                case "find_customer":
                    result: Any = self.find_customer(str(args["email"]))
                case "list_orders":
                    result = self.list_orders(str(args["customer_id"]))
                case "get_order":
                    result = self.get_order(str(args["order_id"]))
                case "issue_refund":
                    result = self.issue_refund(str(args["order_id"]), float(args["amount"]), str(args["reason"]))
                case "escalate":
                    result = self.escalate(str(args["order_id"]), str(args["reason"]))
                case _:
                    raise ToolError(f"unknown tool {name!r}")
        except (KeyError, TypeError, ValueError) as e:
            raise ToolError(f"bad arguments for {name}: {e!r}") from e
        return json.dumps(result)
