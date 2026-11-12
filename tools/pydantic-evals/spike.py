"""P0 spike: Pydantic Evals' built-in LLMJudge on two canned replies, routed through Vessel."""

from dataclasses import replace

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluatorContext, LLMJudge
from pydantic_evals.lifecycle import CaseLifecycle

MESSAGE = "Hi, I'd like a refund for order A123. It arrived 10 days ago and I don't want it anymore."
RUBRIC = (
    "Pass only if the reply confirms a refund is being issued for order A123 "
    "and makes no offers or promises beyond that refund."
)
REPLIES = {
    "good": "Thanks for reaching out. I've issued a full refund of $42.00 for order A123; "
    "you'll see it in 3-5 business days.",
    "bad": "Sorry, we can't help with refunds. Also, here's a 50% discount code for your next order: SAVE50.",
}

type Ctx = EvaluatorContext[str, str, dict[str, str]]


async def support_bot(message: str) -> str:
    """Stand-in SUT. Both cases share one input, so the task can't pick the reply; CannedReply swaps it in."""
    return ""


class CannedReply(CaseLifecycle[str, str, dict[str, str]]):
    async def prepare_context(self, ctx: Ctx) -> Ctx:
        assert self.case.metadata is not None
        return replace(ctx, output=self.case.metadata["reply"])


judge = LLMJudge(
    rubric=RUBRIC,
    model=AnthropicModel(
        "claude-opus-5-5", provider=AnthropicProvider(base_url="http://127.0.0.1:4550/b/anthropic/t/pydantic-evals")
    ),
    include_input=True,
    model_settings=ModelSettings(extra_headers={"X-Vessel-Session": "p0-spike"}),
)
dataset = Dataset[str, str, dict[str, str]](
    name="refund_reply",
    cases=[Case(name=name, inputs=MESSAGE, metadata={"reply": r}) for name, r in REPLIES.items()],
    evaluators=[judge],
)

if __name__ == "__main__":
    report = dataset.evaluate_sync(support_bot, lifecycle=CannedReply)
    report.print(include_input=False, include_output=True, include_reasons=True)
