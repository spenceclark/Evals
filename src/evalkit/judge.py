"""LLM-as-judge plumbing: injection-safe prompts, structured verdicts, and a cache that keeps every input and output.

Prompts are t-strings (PEP 750). render() fences EVERY interpolated value as <data>, so text under evaluation can't
pose as instructions, by construction. Trusted text (criteria, instructions) goes in the system prompt, a plain str.

A judge that can't produce a verdict (truncated, refused, invalid JSON, API error) returns status != "ok" and no
verdict. Callers must count that as an error, never as a fail.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from string.templatelib import Interpolation, Template
from typing import Any, Literal

from anthropic import APIError, AsyncAnthropic
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

type JSON = dict[str, Any]
type Effort = Literal["low", "medium", "high", "xhigh", "max"]
type JudgeStatus = Literal["ok", "truncated", "refused", "invalid", "error"]


def fence(name: str, value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("<data", "&lt;data").replace("</data", "&lt;/data")  # no breaking out of the fence
    return f'<data name="{name}">\n{text}\n</data>'


def render(t: Template) -> str:
    """t"...{reply:candidate_reply}..." -> the reply fenced as <data name="candidate_reply">. The name is the format
    spec if given, else the expression."""
    out: list[str] = []
    for part in t:
        if isinstance(part, Interpolation):
            out.append(fence(part.format_spec or part.expression, part.value))
        else:
            out.append(part)
    return "".join(out)


@dataclass(frozen=True, slots=True)
class JudgeModel:
    model: str
    backend: Literal["anthropic", "openai"] = "anthropic"
    effort: Effort | None = "medium"  # pinned and recorded; None for a backend without effort
    max_tokens: int = 8000


@dataclass(slots=True)
class Asked:
    key: str
    model: str
    effort: str | None
    sample: int
    system: str
    prompt: str
    status: JudgeStatus
    verdict: JSON | None
    text: str
    served: str
    tokens_in: int
    tokens_out: int
    ms: float


class Judge:
    """Asks judge models for a structured verdict. Results are cached in a jsonl file keyed by (model, effort, system,
    prompt, sample): a re-run is free, and sample=1,2 asks the same question again for self-consistency. The key omits
    max_tokens and truncated answers are cached, so re-asking with a bigger budget needs a new sample index."""

    def __init__(self, cache: Path, anthropic: AsyncAnthropic, openai: AsyncOpenAI | None = None,
                 concurrency: dict[str, int] | None = None) -> None:  # fmt: skip
        self.path, self.anthropic, self.openai = cache, anthropic, openai
        self.cache: dict[str, Asked] = {}
        if cache.exists():
            for line in cache.read_text(encoding="utf-8").splitlines():
                a = Asked(**json.loads(line))
                self.cache[a.key] = a
        limits = {"anthropic": 8, "openai": 1} | (concurrency or {})
        self.sems = {k: asyncio.Semaphore(v) for k, v in limits.items()}

    async def ask(self, jm: JudgeModel, system: str, prompt: str, schema: type[BaseModel], sample: int = 0) -> Asked:
        key = hashlib.sha256(json.dumps([jm.model, jm.effort, system, prompt, sample]).encode()).hexdigest()[:20]
        if key in self.cache:
            return self.cache[key]
        async with self.sems[jm.backend]:
            t0 = time.perf_counter()
            try:
                call = self._anthropic if jm.backend == "anthropic" else self._openai
                status, verdict, text, served, tin, tout = await call(jm, system, prompt, schema)
            except (APIError, OpenAIError) as e:
                status, verdict, text, served, tin, tout = "error", None, repr(e), "", 0, 0
            ms = (time.perf_counter() - t0) * 1000
        if served and served != jm.model:
            status, verdict, text = "error", None, f"served model {served!r} != requested {jm.model!r}"
        a = Asked(key, jm.model, jm.effort, sample, system, prompt, status, verdict, text, served, tin, tout, ms)
        if status != "error":  # infra errors are retried next time, not remembered
            self.cache[key] = a
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")
        return a

    async def _anthropic(self, jm: JudgeModel, system: str, prompt: str, schema: type[BaseModel]) -> tuple[
            JudgeStatus, JSON | None, str, str, int, int]:  # fmt: skip
        extra: JSON = {"output_config": {"effort": jm.effort}} if jm.effort else {}
        r = await self.anthropic.messages.parse(
            model=jm.model, max_tokens=jm.max_tokens, system=system, messages=[{"role": "user", "content": prompt}],
            output_format=schema, **extra,
        )  # fmt: skip
        text = "".join(b.text for b in r.content if b.type == "text")
        stops: dict[str, JudgeStatus] = {"max_tokens": "truncated", "refusal": "refused"}
        status = stops.get(r.stop_reason or "", "ok")
        parsed = r.parsed_output.model_dump() if r.parsed_output is not None and status == "ok" else None
        if status == "ok" and parsed is None:
            status = "invalid"
        return status, parsed, text, r.model, r.usage.input_tokens, r.usage.output_tokens

    async def _openai(self, jm: JudgeModel, system: str, prompt: str, schema: type[BaseModel]) -> tuple[
            JudgeStatus, JSON | None, str, str, int, int]:  # fmt: skip
        assert self.openai is not None, "no OpenAI-compatible client configured"
        r = await self.openai.chat.completions.create(
            model=jm.model, max_tokens=jm.max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_format={"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}},
        )  # fmt: skip
        choice = r.choices[0]
        text = choice.message.content or ""
        tin, tout = (r.usage.prompt_tokens, r.usage.completion_tokens) if r.usage else (0, 0)
        if choice.finish_reason == "length":
            return "truncated", None, text, r.model, tin, tout
        try:
            return "ok", schema.model_validate_json(text).model_dump(), text, r.model, tin, tout
        except ValidationError:
            return "invalid", None, text, r.model, tin, tout
