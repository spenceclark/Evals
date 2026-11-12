"""P0 spike: Langfuse `run_experiment` on local data with a client-side code evaluator. No keys, no server, no LLM.

Langfuse's LLM-as-a-judge runs server-side (Langfuse worker), so nothing here calls a model.
Run: uv run python spike.py
"""

import os
import sys
from typing import Any, cast

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"  # no account: don't export OTel spans anywhere

from langfuse import Evaluation, Langfuse

INPUT = "Hi, I'd like a refund for order A123. It arrived 10 days ago and I don't want it anymore."
OUTPUTS = {
    "good": "Thanks for reaching out. I've issued a full refund of $42.00 for order A123; "
    "you'll see it in 3-5 business days.",
    "bad": "Sorry, we can't help with refunds. Also, here's a 50% discount code for your next order: SAVE50.",
}
EXPECTED = {"good": "pass", "bad": "fail"}


def task(*, item: Any, **kwargs: Any) -> str:  # canned replies: no LLM generates the output
    return OUTPUTS[item["metadata"]["case"]]


def refund_rule(*, output: str, **kwargs: Any) -> Evaluation:
    """Code stand-in for the rubric: confirms the A123 refund, no discount/code offers."""
    text = output.lower()
    passed = "refund" in text and "a123" in text and "can't" not in text and "discount" not in text
    return Evaluation(name="refund_rule", value=passed, data_type="BOOLEAN", comment="code check, no LLM")


result = Langfuse().run_experiment(
    name="p0-spike",
    data=[{"input": INPUT, "expected_output": EXPECTED[c], "metadata": {"case": c}} for c in OUTPUTS],
    task=task,
    evaluators=[refund_rule],
)

ok = True
for r in result.item_results:
    case = cast(dict[str, Any], r.item)["metadata"]["case"]
    value = next((e.value for e in r.evaluations if e.name == "refund_rule"), None)
    verdict = "error" if value is None else "pass" if value else "fail"  # error = evaluator failed
    ok &= verdict == EXPECTED[case]
    print(f"{case}: verdict={verdict} expected={EXPECTED[case]} trace_id={r.trace_id}")
sys.stdout.flush()
sys.stdout.buffer.write(result.format().encode() + b"\n")  # format() has emoji; print() crashes on cp1252 pipes
sys.exit(0 if ok else 1)
