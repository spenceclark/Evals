"""Hand-written agent loop with two adapters (Anthropic Messages, OpenAI chat) over one transcript shape.

Each adapter keeps the provider-native message list untouched (thinking blocks must be replayed as-is);
the loop records a separate normalised transcript:
  {"role": "user", "text"}
  {"role": "assistant", "text", "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "id", "name", "result", "error", "ms"}
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from evalkit.core import Call, Status
from supportdesk.shop import SYSTEM_PROMPT, TOOLS, Store, ToolError

type JSON = dict[str, Any]
type Effort = Literal["low", "medium", "high", "xhigh", "max"]
type Stop = Literal["tool_use", "end", "max_tokens", "refusal"]
# Anthropic stop_reason and OpenAI finish_reason -> one vocabulary. Anything else ends the turn.
STOPS: dict[str, Stop] = {
    "tool_use": "tool_use",
    "tool_calls": "tool_use",
    "max_tokens": "max_tokens",
    "length": "max_tokens",
    "refusal": "refusal",
    "content_filter": "refusal",
}


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    args: JSON | None  # None: the model sent arguments that aren't valid JSON


@dataclass(slots=True)
class Step:
    text: str
    tool_calls: list[ToolCall]
    stop: Stop
    call: Call


class LLM(Protocol):
    async def step(self) -> Step: ...
    def tool_results(self, results: list[tuple[str, str, bool]]) -> None: ...


# strict: the API guarantees tool inputs match the schema (Claude only; Ollama doesn't enforce it).
ANTHROPIC_TOOLS = cast(list[ToolParam], [{**t, "strict": True} for t in TOOLS])


class AnthropicLLM:
    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        effort: Effort,
        max_tokens: int,
        user: str,
        headers: dict[str, str],
        system: str = SYSTEM_PROMPT,
    ) -> None:
        self.client, self.model, self.max_tokens, self.headers = client, model, max_tokens, headers
        self.system = system
        self.effort: Effort = effort
        self.native: list[MessageParam] = [{"role": "user", "content": user}]

    async def step(self) -> Step:
        t0 = time.perf_counter()
        r = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            messages=self.native,
            tools=ANTHROPIC_TOOLS,
            output_config={"effort": self.effort},
            extra_headers=self.headers,
        )
        ms = (time.perf_counter() - t0) * 1000
        self.native.append({"role": "assistant", "content": r.content})
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, cast(JSON, b.input)) for b in r.content if b.type == "tool_use"]
        stop = STOPS.get(str(r.stop_reason), "end")
        return Step(
            text, calls, stop, Call(r.model, r.usage.input_tokens, r.usage.output_tokens, str(r.stop_reason), ms)
        )

    def tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        blocks: list[ToolResultBlockParam] = [
            {"type": "tool_result", "tool_use_id": i, "content": c, "is_error": e} for i, c, e in results
        ]
        self.native.append({"role": "user", "content": blocks})


OPENAI_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
    }
    for t in TOOLS
]


class OpenAILLM:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        max_tokens: int,
        user: str,
        headers: dict[str, str],
        system: str = SYSTEM_PROMPT,
    ) -> None:
        self.client, self.model, self.max_tokens, self.headers = client, model, max_tokens, headers
        self.native: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def step(self) -> Step:
        t0 = time.perf_counter()
        r = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=self.native,
            tools=OPENAI_TOOLS,
            extra_headers=self.headers,
        )
        ms = (time.perf_counter() - t0) * 1000
        m, finish = r.choices[0].message, r.choices[0].finish_reason
        calls: list[ToolCall] = []
        for tc in m.tool_calls or []:
            if tc.type != "function":
                continue
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = None
            calls.append(ToolCall(tc.id, tc.function.name, args))
        self.native.append(
            cast(ChatCompletionMessageParam, m.model_dump(exclude_none=True, include={"role", "content", "tool_calls"}))
        )
        stop = STOPS.get(finish, "end")
        if calls and stop == "end":
            stop = "tool_use"
        usage = r.usage
        return Step(
            m.content or "",
            calls,
            stop,
            Call(r.model, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0, finish, ms),
        )

    def tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        # OpenAI chat has no is_error flag: an error is only visible in the content ({"error": ...}).
        self.native.extend({"role": "tool", "tool_call_id": i, "content": c} for i, c, _ in results)


@dataclass(slots=True)
class AgentResult:
    messages: list[JSON]
    output: JSON = field(default_factory=dict[str, Any])  # close_ticket args; empty if it never closed
    calls: list[Call] = field(default_factory=list[Call])
    status: Status = "ok"
    stop_reason: str = ""


# A close_ticket sent in the same turn as other tool calls is refused: the other calls run, their results (and this
# refusal) go back to the model, and it must close again on its own. Without this, an agent can tell the customer
# "refunded" before it has seen whether issue_refund worked (P3: 5 of 60 Haiku fault trials did exactly that).
CLOSE_ALONE = (
    "close_ticket must be the only tool call in its turn, after you have seen the results of your other calls. "
    "The ticket is still open; check the results above, then call close_ticket again."
)


async def run_agent(llm: LLM, user: str, store: Store, max_steps: int = 12, close_alone: bool = True) -> AgentResult:
    """close_alone=False restores the P1-P3 loop, which honoured a close mid-turn (kept for comparison and tests)."""
    res = AgentResult(messages=[{"role": "user", "text": user}])
    for _ in range(max_steps):
        step = await llm.step()
        res.calls.append(step.call)
        res.messages.append(
            {
                "role": "assistant",
                "text": step.text,
                "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in step.tool_calls],
            }
        )
        if step.stop in ("max_tokens", "refusal"):
            res.status = "truncated" if step.stop == "max_tokens" else "refused"
            res.stop_reason = step.stop
            return res
        if not step.tool_calls:
            res.stop_reason = "end_without_close"  # a model failure: scored, not an infra error
            return res
        results: list[tuple[str, str, bool]] = []
        alone = len(step.tool_calls) == 1
        for c in step.tool_calls:
            if c.name == "close_ticket" and c.args is not None and (alone or not close_alone):
                res.output, res.stop_reason = c.args, "close_ticket"
                return res
            t0 = time.perf_counter()
            try:
                if c.args is None:
                    raise ToolError("arguments were not valid JSON")
                if c.name == "close_ticket":
                    raise ToolError(CLOSE_ALONE)
                content, error = store.execute(c.name, c.args), False
            except ToolError as e:
                content, error = json.dumps({"error": str(e)}), True
            res.messages.append(
                {
                    "role": "tool",
                    "id": c.id,
                    "name": c.name,
                    "result": json.loads(content),
                    "error": error,
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            )
            results.append((c.id, content, error))
        llm.tool_results(results)
    res.stop_reason = "max_steps"
    return res
