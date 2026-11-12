"""The shop: orders, refund policy, and the tools the agent can call (P1: get_order, issue_refund, close_ticket)."""

import copy
import json
from typing import Any

type JSON = dict[str, Any]

TODAY = "2026-11-01"

ORDERS: list[JSON] = [
    {
        "id": "A101",
        "email": "maya@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-20",
        "items": [{"sku": "HP-200", "name": "Wireless headphones", "price": 59.00, "kind": "physical"}],
        "total": 59.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A102",
        "email": "tom@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-25",
        "items": [
            {"sku": "KT-01", "name": "Electric kettle", "price": 35.00, "kind": "physical"},
            {"sku": "MG-07", "name": "Stoneware mug", "price": 12.00, "kind": "physical"},
        ],
        "total": 47.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A103",
        "email": "ines@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-28",
        "items": [{"sku": "SH-42", "name": "Trail running shoes", "price": 89.99, "kind": "physical"}],
        "total": 89.99,
        "refunded_amount": 0.0,
    },
    {
        "id": "A104",
        "email": "raj@example.com",
        "status": "delivered",
        "delivered_at": "2026-08-15",
        "items": [{"sku": "JK-11", "name": "Rain jacket", "price": 120.00, "kind": "physical"}],
        "total": 120.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A105",
        "email": "lena@example.com",
        "status": "delivered",
        "delivered_at": "2026-09-28",
        "items": [{"sku": "BP-30", "name": "Daypack 30L", "price": 45.00, "kind": "physical"}],
        "total": 45.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A106",
        "email": "sam@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-30",
        "items": [
            {"sku": "EB-03", "name": "Ebook: Bread at Home", "price": 15.00, "kind": "digital", "downloaded": True}
        ],
        "total": 15.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A107",
        "email": "olu@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-15",
        "items": [{"sku": "LM-05", "name": "Desk lamp", "price": 40.00, "kind": "physical"}],
        "total": 40.00,
        "refunded_amount": 40.00,
    },
    {
        "id": "A108",
        "email": "kim@example.com",
        "status": "shipped",
        "delivered_at": None,
        "items": [{"sku": "BT-09", "name": "Water bottle", "price": 18.00, "kind": "physical"}],
        "total": 18.00,
        "refunded_amount": 0.0,
    },
    {
        "id": "A109",
        "email": "ana@example.com",
        "status": "delivered",
        "delivered_at": "2026-10-22",
        "items": [{"sku": "PL-02", "name": "Ceramic planter", "price": 26.00, "kind": "physical"}],
        "total": 26.00,
        "refunded_amount": 0.0,
    },
]

SYSTEM_PROMPT = f"""You are the customer support agent for Larkspur Supply, an online shop. Today is {TODAY}.

Handle the customer's message with the tools, then finish by calling close_ticket exactly once.
Look up the order before deciding anything about it.

Refund policy:
- Refunds are allowed within 30 days of delivery, for the whole order or for individual items.
- Digital items cannot be refunded once downloaded.
- Orders that are cancelled or already refunded cannot be refunded.
- Never refund more than the order total minus any amount already refunded.

close_ticket actions:
- refunded: you issued a refund with issue_refund.
- denied: the customer asked for a refund that the policy does not allow.
- answered: the customer asked a question and you answered it; no refund was requested.
- needs_info: you cannot proceed without more information from the customer, such as an order ID.

Always include a short, polite reply to the customer in close_ticket."""

TOOLS: list[JSON] = [
    {
        "name": "get_order",
        "description": "Look up an order by its ID.",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "issue_refund",
        "description": "Refund an amount on an order. This moves money; it cannot be undone.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}},
            "required": ["order_id", "amount", "reason"],
        },
    },
    {
        "name": "close_ticket",
        "description": "Finish the ticket: record the outcome and send the reply to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["refunded", "denied", "answered", "needs_info"]},
                "order_id": {"type": ["string", "null"]},
                "amount": {"type": ["number", "null"], "description": "Amount refunded, if any."},
                "reply": {"type": "string", "description": "The message sent to the customer."},
            },
            "required": ["action", "order_id", "amount", "reply"],
        },
    },
]


class ToolError(Exception):
    """A tool rejected its input. Returned to the model as an error result, not raised to the harness."""


class Store:
    """Fresh copy per trial, so no state leaks between trials."""

    def __init__(self) -> None:
        self.orders: dict[str, JSON] = {o["id"]: copy.deepcopy(o) for o in ORDERS}
        self.refunds: list[JSON] = []

    def get_order(self, order_id: str) -> JSON:
        if order_id not in self.orders:
            raise ToolError(f"no order with id {order_id!r}")
        return copy.deepcopy(self.orders[order_id])

    def issue_refund(self, order_id: str, amount: float, reason: str) -> JSON:
        order = self.orders.get(order_id)
        if order is None:
            raise ToolError(f"no order with id {order_id!r}")
        remaining = round(order["total"] - order["refunded_amount"], 2)
        if not 0 < amount <= remaining:
            raise ToolError(f"amount must be > 0 and <= {remaining}")
        order["refunded_amount"] = round(order["refunded_amount"] + amount, 2)
        self.refunds.append({"order_id": order_id, "amount": amount, "reason": reason})
        return {"ok": True, "order_id": order_id, "refunded": amount}

    def execute(self, name: str, args: JSON) -> str:
        """Run a non-terminal tool; return its JSON result. Raises ToolError on bad input."""
        try:
            match name:
                case "get_order":
                    result = self.get_order(str(args["order_id"]))
                case "issue_refund":
                    result = self.issue_refund(str(args["order_id"]), float(args["amount"]), str(args["reason"]))
                case _:
                    raise ToolError(f"unknown tool {name!r}")
        except (KeyError, TypeError, ValueError) as e:
            raise ToolError(f"bad arguments for {name}: {e!r}") from e
        return json.dumps(result)
