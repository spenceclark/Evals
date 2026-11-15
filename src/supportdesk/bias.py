"""Shared plumbing for the P5 judge-bias experiments (experiments/*.py): the frozen pairs, judges, and pair verdicts.

A pair verdict is reported as the winning *model* ("haiku" / "qwen" / "tie"), never as a slot, so the two orders of the
same pair can be compared directly.
"""

import json
import os
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from evalkit.judge import Asked, Judge, JudgeModel
from supportdesk.judges import PAIRWISE_SYSTEM_NEUTRAL, pairwise, view

type JSON = dict[str, Any]
type Model = Literal["haiku", "qwen"]
type Winner = Literal["haiku", "qwen", "tie"]

PAIRS = Path("cases/pairs")
OUT = Path("runs/p5")
JUDGES = {
    "opus": JudgeModel("claude-opus-5-5"),
    "sonnet": JudgeModel("claude-sonnet-5-5"),
    "haiku": JudgeModel("claude-haiku-5-5"),
    # Qwen's context is 8,192 tokens. The largest self-preference prompt is ~1.8k tokens, so 6,200 is left for output.
    # (P4 truncated at 4,096; the P5 pilot ran at 5,000 and truncated 1 of 10, retried at a new sample index.)
    "qwen": JudgeModel("Qwen3.5-9B-Unsloth:IQ4", backend="openai", effort=None, max_tokens=6200),
}
FAMILY = {"opus": "claude", "sonnet": "claude", "haiku": "claude", "qwen": "qwen"}


def rows(path: Path) -> list[JSON]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def pool() -> dict[str, JSON]:
    return {p["id"]: p for p in rows(PAIRS / "pool.jsonl")}


def human() -> dict[str, Winner]:
    """The human's preferred model per pair id, from first showings only (repeats are for intra-rater checks)."""
    queue = {q["id"]: q for q in rows(PAIRS / "human_queue.jsonl")}
    out: dict[str, Winner] = {}
    for a in rows(PAIRS / "human.jsonl"):
        q = queue[a["id"]]
        if q["repeat_of"] is None:
            out[q["pair_id"]] = slot_to_model(a["choice"], q["a"])
    return out


def human_repeats() -> list[tuple[Winner, Winner]]:
    """(first answer, repeat answer) as models, for each swapped repeat the human has answered both times."""
    queue = {q["id"]: q for q in rows(PAIRS / "human_queue.jsonl")}
    by_pair: dict[str, dict[str, Winner]] = {}
    for a in rows(PAIRS / "human.jsonl"):
        q = queue[a["id"]]
        kind = "repeat" if q["repeat_of"] else "first"
        by_pair.setdefault(q["pair_id"], {})[kind] = slot_to_model(a["choice"], q["a"])
    return [(d["first"], d["repeat"]) for d in by_pair.values() if "first" in d and "repeat" in d]


def human_pair_ids() -> list[str]:
    return sorted({q["pair_id"] for q in rows(PAIRS / "human_queue.jsonl")})


def slot_to_model(choice: str, a_model: str) -> Winner:
    if choice == "tie":
        return "tie"
    a_is_haiku = a_model == "haiku"
    return "haiku" if (choice == "A") == a_is_haiku else "qwen"


def attempt_view(pair: JSON, model: str, reply: str | None = None) -> dict[str, str]:
    att = pair[model]
    item = {"input": pair["input"], "expected": pair["expected"], "messages": att["messages"],
            "outcome": att["outcome"], "output": att["output"]}  # fmt: skip
    return view(item, reply)


def make_judge(session: str) -> Judge:
    vessel = os.environ.get("VESSEL_URL", "").rstrip("/")
    headers = {"X-Vessel-Tags": "judge", "X-Vessel-Session": session} if vessel else {}
    anthropic = AsyncAnthropic(base_url=f"{vessel}/b/anthropic" if vessel else None, default_headers=headers)
    base = f"{vessel}/b/ollama/v1" if vessel else "http://127.0.0.1:11434/v1"
    openai = AsyncOpenAI(base_url=base, api_key="ollama", default_headers=headers)
    OUT.mkdir(parents=True, exist_ok=True)
    return Judge(OUT / "cache.jsonl", anthropic, openai)


async def judge_views(judge: Judge, jm: JudgeModel, first: tuple[str, dict[str, str]],
                      second: tuple[str, dict[str, str]], sample: int = 0,
                      system: str = PAIRWISE_SYSTEM_NEUTRAL) -> tuple[str | None, Asked]:  # fmt: skip
    """Judge two labelled views (label, view); returns the winning label ("tie" for a tie, None for no verdict)."""
    a = await pairwise(judge, jm, first[1], second[1], sample, system)
    if a.verdict is None:
        return None, a
    better = a.verdict["better"]
    return ("tie" if better == "tie" else first[0] if better == "A" else second[0]), a


async def judge_pair(judge: Judge, jm: JudgeModel, pair: JSON, first: Model, sample: int = 0,
                     system: str = PAIRWISE_SYSTEM_NEUTRAL) -> tuple[Winner | None, Asked]:  # fmt: skip
    second: Model = "qwen" if first == "haiku" else "haiku"
    w, a = await judge_views(judge, jm, (first, attempt_view(pair, first)), (second, attempt_view(pair, second)),
                             sample, system)  # fmt: skip
    return ("haiku" if w == "haiku" else "qwen" if w == "qwen" else "tie" if w == "tie" else None), a


def score(w: str | None, wins: str) -> float | None:
    """1 if `wins` won, 0.5 for a tie, 0 if the other side won, None for no verdict."""
    return None if w is None else 1.0 if w == wins else 0.5 if w == "tie" else 0.0


def bootstrap_mean(values: Sequence[float], reps: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """(mean, lo, hi), 95% percentile bootstrap over items."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(reps))
    return sum(values) / n, means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def usd(asked: Sequence[Asked]) -> float:
    from evalkit.core import PRICES

    return sum((a.tokens_in * PRICES.get(a.model, (0, 0))[0] + a.tokens_out * PRICES.get(a.model, (0, 0))[1]) / 1e6
               for a in asked)  # fmt: skip
