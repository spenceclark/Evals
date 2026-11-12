"""Types and the runner. Knows nothing about the system under test: a task is any async callable Case -> Trial."""

import asyncio
import hashlib
import inspect
import json
import subprocess
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

type JSON = dict[str, Any]
type Status = Literal["ok", "truncated", "refused"]

# $ per million tokens (in, out). Models not listed (local ones) cost 0.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    input: str
    expected: JSON
    suite: Literal["regression", "capability"] = "regression"
    tags: tuple[str, ...] = ()
    faults: JSON = field(default_factory=dict[str, Any])
    source: str = "hand"
    reference_reply: str | None = None


@dataclass(slots=True)
class Call:
    """One LLM call. `model` is what the response says served it."""

    model: str
    tokens_in: int
    tokens_out: int
    stop_reason: str
    ms: float


@dataclass(slots=True)
class Trial:
    case_id: str
    rep: int
    model: str  # requested
    messages: list[JSON]  # transcript
    output: JSON  # what the system claims it did
    outcome: JSON  # what actually changed
    calls: list[Call] = field(default_factory=list[Call])
    status: Status = "ok"
    stop_reason: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class Score:
    grader: str
    passed: bool
    value: float | None = None
    reason: str = ""


class Task(Protocol):
    async def __call__(self, case: Case, *, rep: int, run_id: str) -> Trial: ...


type Grader = Callable[[Case, Trial], Score]


def cost(calls: Sequence[Call]) -> float:
    total = 0.0
    for c in calls:
        p_in, p_out = PRICES.get(c.model, (0.0, 0.0))
        total += (c.tokens_in * p_in + c.tokens_out * p_out) / 1e6
    return total


def load_cases(path: Path) -> list[Case]:
    cases: list[Case] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            d["tags"] = tuple(d.get("tags", ()))
            cases.append(Case(**d))
    return cases


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()[:12]


def _git_sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown"
    return sha + ("-dirty" if dirty else "")


def _append(path: Path, row: JSON) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(
    cases: Sequence[Case],
    task: Task,
    graders: Sequence[Grader],
    *,
    run_dir: Path,
    cases_path: Path,
    task_name: str,
    trials: int = 1,
    concurrency: int = 4,
) -> None:
    run_id = run_dir.name
    run_dir.mkdir(parents=True)
    meta: JSON = {
        "run_id": run_id,
        "task": task_name,
        "task_meta": getattr(task, "meta", {}),
        "started": datetime.now(UTC).isoformat(),
        "git": _git_sha(),
        "cases": str(cases_path),
        "cases_sha": _sha(cases_path.read_bytes()),
        "graders": {g.__name__: _sha(inspect.getsource(g)) for g in graders},
        "trials": trials,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    results, errors = run_dir / "results.jsonl", run_dir / "errors.jsonl"
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case, rep: int) -> None:
        def fail(stage: str, e: BaseException) -> None:
            _append(
                errors,
                {
                    "case_id": case.id,
                    "rep": rep,
                    "stage": stage,
                    "error": repr(e),
                    "traceback": "".join(traceback.format_exception(e)),
                },
            )

        async with sem:
            t0 = time.perf_counter()
            try:
                trial = await task(case, rep=rep, run_id=run_id)
            except Exception as e:  # infra failure: never scored as a model failure
                return fail("task", e)
            trial.latency_ms = (time.perf_counter() - t0) * 1000
        if wrong := {c.model for c in trial.calls} - {trial.model}:
            return fail("served_model", AssertionError(f"requested {trial.model}, served {sorted(wrong)}"))
        scores: list[Score] = []
        if trial.status == "ok":  # truncated / refused are their own outcome, not a zero
            for g in graders:
                try:
                    scores.append(g(case, trial))
                except Exception as e:
                    return fail(f"grader:{g.__name__}", e)
        _append(
            results,
            {
                "case_id": case.id,
                "suite": case.suite,
                "rep": rep,
                "expected": case.expected,
                "scores": [asdict(s) for s in scores],
                "cost_usd": cost(trial.calls),
                "trial": asdict(trial),
            },
        )

    await asyncio.gather(*(one(c, r) for c in cases for r in range(trials)))
