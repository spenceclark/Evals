"""Pick the trials for P4 human labelling and freeze them into a self-contained queue (runs/ is gitignored).

    uv run python scripts/select_labels.py

Writes cases/labels/queue.jsonl: 120 replies, shuffled; the labeller isn't shown which model wrote each. Every
code-failing reply goes in, then code-passing ones spread across cases, weighted towards the local model. The dev/test
split happens after labelling (scripts/split_labels.py), by case, with the held-out set guaranteed at least 15 fails.
"""

import json
import random
from pathlib import Path
from typing import Any

type JSON = dict[str, Any]

# Graded with all graders. Old-loop and fixed-loop runs both count: labels are about the reply, given what happened.
RUNS = {
    "20261112-170745-haiku-path": "haiku", "20261112-170743-qwen-path": "qwen",  # P2 full suite, old loop
    "20261112-180038-haiku": "haiku", "20261112-180035-qwen": "qwen",  # P3 faults k=5, old loop
    "20261112-182715-haiku": "haiku", "20261112-182712-qwen": "qwen",  # P3 faults k=5, fixed loop
    "20261112-182851-haiku": "haiku", "20261112-184001-qwen": "qwen",  # P3 full suite, fixed loop
}  # fmt: skip
OUTCOME = {"schema", "action", "order_id", "amount", "end_state", "safety", "claim_vs_outcome"}
CODE_PASS = {"haiku": 45, "qwen": 60}  # plus every code-failing reply: 120 in all
rng = random.Random(4)


def main() -> None:
    cases = {c["id"]: c for c in map(json.loads, Path("cases/full.jsonl").read_text(encoding="utf-8").splitlines())}
    pool: list[JSON] = []
    seen: set[tuple[str, str, str]] = set()
    for run, model in RUNS.items():
        for line in (Path("runs") / run / "results.jsonl").read_text(encoding="utf-8").splitlines():
            r: JSON = json.loads(line)
            t: JSON = r["trial"]
            output: JSON = t["output"] or {}
            reply: str | None = output.get("reply")
            if not reply or (model, r["case_id"], reply) in seen:
                continue  # no reply to judge (never closed), or an exact duplicate
            seen.add((model, r["case_id"], reply))
            code_pass = all(s["passed"] for s in r["scores"] if s["grader"] in OUTCOME)
            pool.append({"source": {"run": run, "model": model, "case_id": r["case_id"], "rep": r["rep"]},
                         "code_pass": code_pass, "trial": t})  # fmt: skip

    chosen = [p for p in pool if not p["code_pass"]]
    for model, n in CODE_PASS.items():
        passing = [p for p in pool if p["code_pass"] and p["source"]["model"] == model]
        rng.shuffle(passing)
        by_case: dict[str, list[JSON]] = {}
        for p in passing:
            by_case.setdefault(p["source"]["case_id"], []).append(p)
        # one per case first (round-robin), so 45-60 picks cover as many cases as possible
        picked: list[JSON] = []
        while len(picked) < n:
            for items in by_case.values():
                if items and len(picked) < n:
                    picked.append(items.pop())
        chosen += picked
    rng.shuffle(chosen)

    out = Path("cases/labels/queue.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, p in enumerate(chosen, 1):
        c = cases[p["source"]["case_id"]]
        t = p["trial"]
        lines.append(json.dumps({
            "id": f"L{i:03d}", "source": p["source"], "code_pass": p["code_pass"], "input": c["input"],
            "expected": c["expected"], "messages": t["messages"], "output": t["output"], "outcome": t["outcome"],
        }, ensure_ascii=False))  # fmt: skip
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fails = sum(not p["code_pass"] for p in chosen)
    by_model = {m: sum(p["source"]["model"] == m for p in chosen) for m in CODE_PASS}
    print(
        f"{len(chosen)} items -> {out} | code-fail {fails} | by model {by_model} | "
        f"distinct cases {len({p['source']['case_id'] for p in chosen})} | pool {len(pool)}"
    )
    assert len(chosen) == 120


if __name__ == "__main__":
    main()
