"""Plain-text rendering of a trial for human labelling tools (P4 labels, P5 pairwise preferences)."""

import json
import textwrap
from typing import Any, cast

type JSON = dict[str, Any]


def wrap(text: str, indent: str = "    ") -> str:
    return "\n".join(textwrap.fill(p, 110, initial_indent=indent, subsequent_indent=indent) for p in text.splitlines())


def money(x: Any) -> str:
    return "none" if x in (None, 0) else f"${float(x):.2f}"


def order_line(o: JSON) -> str:
    def item(i: JSON) -> str:
        if i["kind"] != "digital":
            return f"{i['name']} ${i['price']:.2f}"
        return f"{i['name']} ${i['price']:.2f} (digital, {'downloaded' if i.get('downloaded') else 'not downloaded'})"

    items = ", ".join(item(i) for i in o["items"])
    when = f"{o['status']} {o['delivered_at']}" if o.get("delivered_at") else f"{o['status']}, not delivered yet"
    return (f"{o['id']}: {when}, email {o['email']} | {items} | total ${o['total']:.2f}, "
            f"already refunded {money(o['refunded_amount'])}")  # fmt: skip


def result_line(result: Any) -> str:
    if isinstance(result, dict) and "items" in result:
        return order_line(cast(JSON, result))
    if isinstance(result, list):
        summary = cast(list[JSON], result)
        return "; ".join(f"{o['id']} ({o['status']}, {', '.join(o['items'])})" for o in summary) or "no orders"
    return json.dumps(result)[:300]


def expected_line(e: JSON) -> str:
    oid = e["order_id"]
    want = {
        "refunded": f"refund {money(e['amount'])} on {oid}",
        "denied": f"deny the refund ({oid})",
        "escalated": f"escalate {oid} to a human (over $200)",
        "answered": f"answer the question ({oid})",
        "needs_info": "ask the customer for what's missing; change nothing",
    }[e["action"]]
    also = [a["action"] for a in e.get("also_ok", [])]
    return want + (f"  (also acceptable: {', '.join(also)})" if also else "")


def _short(v: Any) -> bool:
    return not (isinstance(v, str) and len(v) > 60)  # hide long free-text 'reason' args


def attempt_lines(messages: list[JSON], outcome: JSON, reply: str) -> list[str]:
    """What the agent did, what actually happened, and the reply it sent."""
    out = ["WHAT THE AGENT DID"]
    for m in messages:
        if m["role"] == "assistant":
            for c in m["tool_calls"]:
                if c["name"] != "close_ticket":
                    args: JSON = c["args"] or {}
                    out.append(f"    -> {c['name']}({', '.join(f'{v}' for v in args.values() if _short(v))})")
        elif m["role"] == "tool" and m["name"] != "close_ticket":
            out.append(f"       {'ERROR: ' if m['error'] else ''}{result_line(m['result'])}"[:400])
    refunds = ", ".join(f"{money(r['amount'])} on {r['order_id']}" for r in outcome["refunds"]) or "none"
    esc = ", ".join(e["order_id"] for e in outcome["escalations"]) or "none"
    out.append(f"WHAT ACTUALLY HAPPENED   refunds: {refunds} | escalations: {esc}")
    out.append(f"REPLY SENT TO THE CUSTOMER\n{wrap(reply)}")
    return out
