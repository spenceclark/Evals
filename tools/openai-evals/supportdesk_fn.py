"""P9: the system under test as an oaieval CompletionFn.

The CompletionFn protocol is prompt in, `get_completions() -> list[str]` out. There is no slot for a tool-call
transcript, so the whole Trial (output, outcome, messages, calls) comes back as one JSON string: that string is the
only way the eval class can see what the agent did.
"""

import json
from dataclasses import asdict
from typing import Any

from evals.api import CompletionFn, CompletionResult
from evals.record import record_sampling  # pyright: ignore[reportUnknownVariableType]  (evals is untyped)

import supportdesk
from evalkit.core import cost


class TrialResult(CompletionResult):
    def __init__(self, text: str) -> None:
        self.text = text

    def get_completions(self) -> list[str]:
        return [self.text]


class SupportdeskCompletionFn(CompletionFn):
    def __init__(self, run_id: str, **_registry: Any) -> None:
        self.run_id = run_id

    def __call__(self, prompt: Any, **kwargs: Any) -> TrialResult:
        # Our eval class passes faults/case_id/rep as kwargs. PromptFn would pass max_tokens, temperature, top_p,
        # frequency_penalty, presence_penalty, n: the agent has its own settings, so all of those are ignored.
        t = supportdesk.run(prompt, kwargs.get("faults"), case_id=kwargs.get("case_id", "adhoc"),
                            rep=kwargs.get("rep", 0), run_id=self.run_id)  # fmt: skip
        text = json.dumps(asdict(t), ensure_ascii=False)
        usage = {"input_tokens": sum(c.tokens_in for c in t.calls), "output_tokens": sum(c.tokens_out for c in t.calls),
                 "model_calls": len(t.calls), "cost_usd": cost(t.calls)}  # fmt: skip
        record_sampling(prompt=prompt, sampled=[text], model=t.model, usage=usage)
        return TrialResult(text)
