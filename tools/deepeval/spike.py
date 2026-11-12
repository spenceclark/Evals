"""DeepEval P0 spike: two canned cases, one G-Eval judge on claude-opus-5-5, routed through Vessel.

Run from tools/deepeval (telemetry is on by default, so opt out per command):
    DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run python spike.py
"""

# deepeval's own signature has an unresolved forward ref ("OfficialMCPServer"); not our code.
from deepeval import evaluate  # pyright: ignore[reportUnknownVariableType]
from deepeval.evaluate import AsyncConfig
from deepeval.metrics import GEval
from deepeval.models import AnthropicModel
from deepeval.test_case import LLMTestCase, SingleTurnParams

INPUT = "Hi, I'd like a refund for order A123. It arrived 10 days ago and I don't want it anymore."
RUBRIC = (
    "Pass only if the reply confirms a refund is being issued for order A123 "
    "and makes no offers or promises beyond that refund."
)
CASES = {
    "good": "Thanks for reaching out. I've issued a full refund of $42.00 for order A123; "
    "you'll see it in 3-5 business days.",
    "bad": "Sorry, we can't help with refunds. Also, here's a 50% discount code for your next order: SAVE50.",
}
EXPECTED = {"good": True, "bad": False}

# Extra kwargs go to the anthropic.Anthropic client, so base_url and headers are settable here.
judge = AnthropicModel(
    model="claude-opus-5-5",
    base_url="http://127.0.0.1:4550/b/anthropic/t/deepeval",
    default_headers={"X-Vessel-Session": "p0-spike"},
)
metric = GEval(
    name="Refund only",
    criteria=RUBRIC,
    evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
    model=judge,
)
test_cases = [LLMTestCase(name=name, input=INPUT, actual_output=output) for name, output in CASES.items()]

# Sequential so Vessel rows map to cases in order.
result = evaluate(test_cases=test_cases, metrics=[metric], async_config=AsyncConfig(run_async=False))

verdicts = {tr.name: tr.success for tr in result.test_results}
for tr in result.test_results:
    md = (tr.metrics_data or [])[0]
    print(f"{tr.name}: success={tr.success} score={md.score} reason={md.reason}")
assert verdicts == EXPECTED, verdicts
