"""Promptfoo provider: the system under test. Runs the refund desk on one email and hands the Trial back to Node.

`output` is the reply (what an llm-rubric would grade by default). Everything the other graders need crosses the
Python -> Node boundary as JSON in `metadata`: the whole Trial, and the judge's input as P4's judge saw it.
"""

from dataclasses import asdict
from typing import Any

import supportdesk
from evalkit.core import cost
from supportdesk.judges import rubric_prompt, view
from supportdesk.shop import SYSTEM_PROMPT


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    v = context["vars"]
    rep = int(context.get("repeatIndex") or 0)
    t = supportdesk.run(v["email"], v["faults"], case_id=v["case_id"], rep=rep, run_id="p9-promptfoo")
    item = {"messages": t.messages, "input": v["email"], "outcome": t.outcome, "output": t.output}
    tin, tout = sum(c.tokens_in for c in t.calls), sum(c.tokens_out for c in t.calls)
    return {
        "output": t.output.get("reply") or "",
        "cost": cost(t.calls),
        "tokenUsage": {"prompt": tin, "completion": tout, "total": tin + tout, "numRequests": len(t.calls)},
        "metadata": {
            "trial": asdict(t),
            "judge_input": rubric_prompt(view(item, policy=SYSTEM_PROMPT), answer=False),
            "context_keys": sorted(context),
        },
    }
