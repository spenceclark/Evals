"""P0 spike: Braintrust `Eval` (local, no_send_logs) + autoevals `LLMClassifier` judging with claude-opus-5-5.

autoevals only builds OpenAI chat-completions requests. Its shipped `autoevals.litellm.AsyncLiteLLMClient` hands
them to LiteLLM, which sends native Anthropic Messages requests to Vessel (no Braintrust account involved).
Async client because `Eval` calls scorers via `eval_async`; the sync `LiteLLMClient` fails there.

Run: uv run python spike.py                    (with litellm.drop_params, the workaround)
     uv run python spike.py --no-drop-params   (reproduces the forced tool_choice failure)
"""

import os
import sys
from typing import Any, cast

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # use the bundled cost map, don't fetch from GitHub

import litellm
from autoevals import init
from autoevals.litellm import AsyncLiteLLMClient
from autoevals.llm import LLMClassifier
from braintrust import Eval, EvalCase, EvalHooks

VESSEL = "http://127.0.0.1:4550/b/anthropic/t/braintrust"  # LiteLLM appends /v1/messages
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

litellm.drop_params = "--no-drop-params" not in sys.argv  # downgrades forced tool_choice to auto on opus-5-5
# init() is typed for OpenAI clients only; its own LiteLLM adapter does not satisfy that type.
init(client=AsyncLiteLLMClient(base_url=VESSEL), default_model="anthropic/claude-opus-5-5")  # pyright: ignore[reportArgumentType]

judge = LLMClassifier(
    name="refund_rubric",
    prompt_template=(
        "You are grading a customer-support reply.\n\n"
        "Customer message:\n{{input}}\n\n"
        "Support reply:\n{{output}}\n\n"
        f"Rubric: {RUBRIC}\n\n"
        "Does the reply pass the rubric?"
    ),
    choice_scores={"pass": 1, "fail": 0},
)


def case_of(metadata: object) -> str:  # braintrust types metadata as dict | pydantic model
    return cast(dict[str, str], metadata)["case"]


def task(input: str, hooks: EvalHooks[Any]) -> str:  # canned replies: no LLM generates the output
    return OUTPUTS[case_of(hooks.metadata)]


result = Eval(
    "p0-spike",
    data=[EvalCase(input=INPUT, expected=EXPECTED[c], metadata={"case": c}) for c in OUTPUTS],
    task=task,
    scores=[judge],
    no_send_logs=True,
)

ok = True
for r in result.results:
    case = case_of(r.metadata)
    score = r.scores.get("refund_rubric")
    verdict = "error" if score is None else "pass" if score == 1 else "fail"  # error = judge call failed
    ok &= verdict == EXPECTED[case]
    print(f"{case}: verdict={verdict} expected={EXPECTED[case]} score={score} error={r.error!r}")
sys.exit(0 if ok else 1)
