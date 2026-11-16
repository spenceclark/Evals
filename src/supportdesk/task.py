"""Tasks the harness calls: the real agent on each model, plus the oracle and null baselines.

Vessel is on when VESSEL_URL is set (e.g. http://127.0.0.1:4550): one Vessel session per run, and two tags per call:
`sut` and `trial-<case_id>-<rep>`, so every capture can be joined back to its trial.
"""

import hashlib
import json
import os
from functools import cache

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from evalkit.core import Case, Trial
from supportdesk.agent import LLM, AnthropicLLM, Effort, OpenAILLM, run_agent
from supportdesk.shop import (
    NO_ESCALATION_PROMPT,
    SYSTEM_PROMPT,
    TOOLS,
    V1_PROMPT,
    V2_PROMPT,
    V3_PROMPT,
    Store,
    ToolError,
)

VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")


def prompt_sha(system: str) -> str:
    return hashlib.sha256((system + json.dumps(TOOLS)).encode()).hexdigest()[:12]


OLLAMA_CONTEXT = 8192  # Ollama's server default for this model, seen in /api/ps; not settable via the OpenAI API


def trial_tag(case_id: str, rep: int) -> str:
    return f"trial-{case_id}-{rep}"


@cache
def _anthropic(run_id: str) -> AsyncAnthropic:
    headers = {"X-Vessel-Session": run_id} if VESSEL else {}
    return AsyncAnthropic(base_url=f"{VESSEL}/b/anthropic" if VESSEL else None, default_headers=headers)


@cache
def _openai(run_id: str) -> AsyncOpenAI:
    base = f"{VESSEL}/b/ollama/v1" if VESSEL else "http://127.0.0.1:11434/v1"
    headers = {"X-Vessel-Session": run_id} if VESSEL else {}
    return AsyncOpenAI(base_url=base, api_key="ollama", default_headers=headers)


class AgentTask:
    def __init__(self, model: str, adapter: str, max_tokens: int, effort: Effort | None = None,
                 system: str = SYSTEM_PROMPT, close_alone: bool = True) -> None:  # fmt: skip
        self.model, self.adapter, self.max_tokens = model, adapter, max_tokens
        self.effort: Effort = effort or "medium"
        self.system, self.close_alone = system, close_alone
        self.meta = {
            "model": model,
            "adapter": adapter,
            "effort": effort,
            "max_tokens": max_tokens,
            "strict_tools": adapter == "anthropic",
            "loop": "close_alone" if close_alone else "close_any",  # since P3: a close sharing its turn is refused
            "context_window": OLLAMA_CONTEXT if adapter == "openai" else None,
            "prompt_sha": prompt_sha(system),
            "vessel": bool(VESSEL),
        }

    async def __call__(self, case: Case, *, rep: int, run_id: str) -> Trial:
        store = Store(case.faults)
        headers = {"X-Vessel-Tags": f"sut,{trial_tag(case.id, rep)}"} if VESSEL else {}
        llm: LLM
        if self.adapter == "anthropic":
            llm = AnthropicLLM(_anthropic(run_id), self.model, self.effort, self.max_tokens, case.input, headers,
                               self.system)  # fmt: skip
        else:
            llm = OpenAILLM(_openai(run_id), self.model, self.max_tokens, case.input, headers, self.system)
        r = await run_agent(llm, case.input, store, close_alone=self.close_alone)
        return Trial(case.id, rep, self.model, r.messages, r.output, store.outcome(), r.calls, r.status, r.stop_reason)


# haiku runs the shipped prompt (system_prompt.txt): v2 until P7, v3 from P8. CI gates this task.
haiku = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium")
# input + output must fit Ollama's 8192-token context, so output is capped at half of it
qwen = AgentTask("Qwen3.5-9B-Unsloth:IQ4", "openai", max_tokens=4096)
# P6 variants. haiku_no_escalation: the planted regression ($200 rule deleted). haiku_close_any: the P1-P3 loop, which
# honoured a close sent in the same turn as a side effect; kept because it fails some fault cases some of the time.
haiku_no_escalation = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium",
                                system=NO_ESCALATION_PROMPT)  # fmt: skip
haiku_close_any = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium", close_alone=False)
# P7 lifecycle, frozen: v1 (a first draft: an ablation of v2), v2 (what `haiku` ran in P1-P7), v3 (v2 plus the fix
# for what the monitor found in simulated traffic; shipped in P8, so the same prompt as `haiku` for now).
haiku_v1 = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium", system=V1_PROMPT)
haiku_v2 = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium", system=V2_PROMPT)
haiku_v3 = AgentTask("claude-haiku-5-5", "anthropic", max_tokens=8000, effort="medium", system=V3_PROMPT)


async def oracle(case: Case, *, rep: int, run_id: str) -> Trial:
    """Carries out the expected answer against a real store (faults on, retrying once), so an unsolvable
    case or a wrong label fails here rather than being blamed on the model. Must score 100%."""
    e = case.expected
    store = Store(case.faults)
    planned = [("issue_refund", {"order_id": r["order_id"], "amount": r["amount"], "reason": "oracle"})
               for r in e["refunds"]]  # fmt: skip
    planned += [("escalate", {"order_id": oid, "reason": "oracle"}) for oid in e["escalations"]]
    for name, args in planned:
        try:
            store.execute(name, args)
        except ToolError:
            store.execute(name, args)  # an injected fault fails the first call only
    out = {"action": e["action"], "order_id": e["order_id"], "amount": e["amount"], "reply": "(oracle)"}
    return Trial(case.id, rep, "oracle", [], out, store.outcome(), stop_reason="oracle")


async def null(case: Case, *, rep: int, run_id: str) -> Trial:
    """Denies everything with an empty reply and touches nothing. Its scores are the floor a real agent must beat."""
    out = {"action": "denied", "order_id": None, "amount": None, "reply": ""}
    return Trial(case.id, rep, "null", [], out, Store().outcome(), stop_reason="null")
