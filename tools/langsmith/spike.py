"""P0 spike: LangSmith `evaluate(upload_results=False)` + openevals `create_llm_as_judge` on claude-opus-5-5.

No LangSmith account: local Example objects, tracing off, nothing uploaded. Judge traffic goes to Vessel.

Run: uv run python spike.py                 (openevals default: with_structured_output, method="function_calling")
     uv run python spike.py --json-schema   (structured_output_kwargs={"method": "json_schema"})
"""

import os
import sys
import uuid
from typing import Any, cast

os.environ["LANGSMITH_TRACING"] = "false"  # no account: never try to send traces
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_anthropic import ChatAnthropic
from langsmith import evaluate, schemas  # pyright: ignore[reportUnknownVariableType]  (library typing)
from openevals.llm import create_llm_as_judge  # pyright: ignore[reportUnknownVariableType]  (library typing)

VESSEL = "http://127.0.0.1:4550/b/anthropic/t/langsmith"  # Anthropic SDK appends /v1/messages
INPUT = "Hi, I'd like a refund for order A123. It arrived 10 days ago and I don't want it anymore."
OUTPUTS = {
    "good": "Thanks for reaching out. I've issued a full refund of $42.00 for order A123; "
    "you'll see it in 3-5 business days.",
    "bad": "Sorry, we can't help with refunds. Also, here's a 50% discount code for your next order: SAVE50.",
}
EXPECTED = {"good": "pass", "bad": "fail"}
RUBRIC = (
    "Pass only if the reply confirms a refund is being issued for order A123 "
    "and makes no offers or promises beyond that refund."
)

judge = create_llm_as_judge(
    prompt=(
        "You are grading a customer-support reply.\n\n"
        "Customer message:\n{inputs}\n\n"
        "Support reply:\n{outputs}\n\n"
        f"Rubric: {RUBRIC}\n\n"
        "Does the reply pass the rubric?"
    ),
    judge=ChatAnthropic(
        model_name="claude-opus-5-5",
        base_url=VESSEL,
        default_headers={"X-Vessel-Session": "p0-spike"},
        timeout=None,
        stop=None,
    ),
    feedback_key="refund_rubric",
    structured_output_kwargs={"method": "json_schema"} if "--json-schema" in sys.argv else None,
)


def target(inputs: dict[str, Any]) -> dict[str, str]:  # canned replies: no LLM generates the output
    return {"reply": OUTPUTS[inputs["case"]]}


def refund_rubric(inputs: dict[str, Any], outputs: dict[str, Any]) -> Any:  # keep "case" out of the judge prompt
    return judge(inputs=inputs["message"], outputs=outputs["reply"])


examples = [
    schemas.Example(id=uuid.uuid4(), inputs={"message": INPUT, "case": c}, outputs={"verdict": EXPECTED[c]})
    for c in OUTPUTS
]
# upload_results=False alone still uploads the judge's LangChain traces (401 without a key); see NOTES.md.
results = evaluate(
    target, data=examples, evaluators=[refund_rubric], upload_results=False, disable_evaluator_tracing=True
)

ok = True
for row in results:
    case = cast(dict[str, str], row["example"].inputs)["case"]
    res = row["evaluation_results"].get("results", [])[0]
    verdict = "error" if res.score is None else "pass" if res.score else "fail"  # error = judge call failed
    ok &= verdict == EXPECTED[case]
    print(f"{case}: verdict={verdict} expected={EXPECTED[case]} score={res.score!r} comment={res.comment!r}")
sys.exit(0 if ok else 1)
