"""Tasks the harness calls: the real agent on each model, plus the oracle and null baselines.

Vessel is on when VESSEL_URL is set (e.g. http://127.0.0.1:4550): one Vessel session per run, tag `sut`.
"""

import hashlib
import json
import os
from functools import cache

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from evalkit.core import Case, Trial
from supportdesk.agent import LLM, AnthropicLLM, Effort, OpenAILLM, run_agent
from supportdesk.shop import SYSTEM_PROMPT, TOOLS, Store

VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")
PROMPT_SHA = hashlib.sha256((SYSTEM_PROMPT + json.dumps(TOOLS)).encode()).hexdigest()[:12]


def _headers(run_id: str) -> dict[str, str]:
    return {"X-Vessel-Tags": "sut", "X-Vessel-Session": run_id} if VESSEL else {}


@cache
def _anthropic(run_id: str) -> AsyncAnthropic:
    return AsyncAnthropic(base_url=f"{VESSEL}/b/anthropic" if VESSEL else None, default_headers=_headers(run_id))


@cache
def _openai(run_id: str) -> AsyncOpenAI:
    base = f"{VESSEL}/b/ollama/v1" if VESSEL else "http://127.0.0.1:11434/v1"
    return AsyncOpenAI(base_url=base, api_key="ollama", default_headers=_headers(run_id))


class AgentTask:
    def __init__(self, model: str, adapter: str, max_tokens: int, effort: Effort | None = None) -> None:
        self.model, self.adapter, self.max_tokens = model, adapter, max_tokens
        self.effort: Effort = effort or "medium"
        self.meta = {
            "model": model,
            "adapter": adapter,
            "effort": effort,
            "max_tokens": max_tokens,
            "prompt_sha": PROMPT_SHA,
            "vessel": bool(VESSEL),
        }

    async def __call__(self, case: Case, *, rep: int, run_id: str) -> Trial:
        store = Store()
        llm: LLM
        if self.adapter == "anthropic":
            llm = AnthropicLLM(_anthropic(run_id), self.model, self.effort, self.max_tokens, case.input)
        else:
            llm = OpenAILLM(_openai(run_id), self.model, self.max_tokens, case.input)
        r = await run_agent(llm, case.input, store)
        return Trial(
            case.id, rep, self.model, r.messages, r.output, {"refunds": store.refunds}, r.calls, r.status, r.stop_reason
        )


haiku = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium")
qwen = AgentTask("Qwen3.5-9B-Unsloth:IQ4", "openai", max_tokens=8192)


async def oracle(case: Case, *, rep: int, run_id: str) -> Trial:
    """Replays the expected answer. Must score 100%; if not, the grader or harness is wrong."""
    return Trial(case.id, rep, "oracle", [], dict(case.expected), {}, stop_reason="oracle")


async def null(case: Case, *, rep: int, run_id: str) -> Trial:
    """Denies everything with an empty reply. Its score is the floor a real agent must beat."""
    out = {"action": "denied", "order_id": None, "amount": None, "reply": ""}
    return Trial(case.id, rep, "null", [], out, {}, stop_reason="null")
