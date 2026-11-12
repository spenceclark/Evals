"""A Claude CompletionFn for oaieval, sending through Vessel.

oaieval has no Claude completion fn in evals 2.0.0.post1 (the latest that installs on 3.14), so this is the
documented extension point: a class registered in registry/completion_fns/claude.yaml.

evals' PromptFn calls every completion fn with max_tokens, temperature=0, top_p=1, frequency_penalty=0,
presence_penalty=0 and n=1. We send only max_tokens unless forward_sampling is set (used once to show the 400).
"""

from typing import Any

import anthropic
from anthropic.types import AssistantMessageParam, MessageParam, UserMessageParam
from evals.api import CompletionFn, CompletionResult
from evals.prompt.base import OpenAICreateChatPrompt
from evals.record import record_sampling  # pyright: ignore[reportUnknownVariableType]  (evals is untyped)


class ClaudeCompletionResult(CompletionResult):
    def __init__(self, text: str) -> None:
        self.text = text

    def get_completions(self) -> list[str]:
        return [self.text]


class ClaudeCompletionFn(CompletionFn):
    def __init__(
        self, model: str, base_url: str, session: str, forward_sampling: bool = False, **_registry: Any
    ) -> None:
        self.model = model
        self.forward_sampling = forward_sampling
        self.client = anthropic.Anthropic(base_url=base_url, default_headers={"X-Vessel-Session": session})

    def __call__(self, prompt: str | OpenAICreateChatPrompt, **kwargs: Any) -> ClaudeCompletionResult:
        chat = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        system = "\n\n".join(m["content"] for m in chat if m["role"] == "system")
        messages: list[MessageParam] = [
            AssistantMessageParam(role="assistant", content=m["content"])
            if m["role"] == "assistant"
            else UserMessageParam(role="user", content=m["content"])
            for m in chat
            if m["role"] != "system"
        ]
        # anthropic>=2 dropped temperature/top_p kwargs (TypeError), so the forward path puts them on the wire raw.
        sampling = {"temperature": kwargs["temperature"], "top_p": kwargs["top_p"]} if self.forward_sampling else None
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", 1024),
            messages=messages,
            system=system or anthropic.omit,
            extra_body=sampling,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        record_sampling(prompt=chat, sampled=[text], model=self.model, usage=resp.usage.model_dump())
        return ClaudeCompletionResult(text)
