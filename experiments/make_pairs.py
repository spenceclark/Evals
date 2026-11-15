"""Freeze the P5 pair pool and the human pairwise queue. Run once, before any judge sees a pair.

    uv run python experiments/make_pairs.py

Every pair is two replies to the same case, one by Haiku 5.5 and one by Qwen 9B, taken from the P2/P3 runs (both loops,
all reps). Writes, under cases/pairs/ (self-contained, since runs/ is gitignored):
- pool.jsonl: 200 pairs, about 3 per case, for the judge-only experiments (position, self-preference).
- human_queue.jsonl: 55 items for the human: 50 pool pairs from 50 different cases, Haiku in slot A for exactly 25 of
  them, plus 5 of the first 25 shown again later with the slots swapped (intra-rater consistency). Which model is in
  which slot is never shown to the labeller.
"""

import json
import random
import re
from pathlib import Path
from typing import Any

type JSON = dict[str, Any]

RUNS = {
    "20261112-170745-haiku": "haiku", "20261112-170743-qwen": "qwen",  # P2 full suite, old loop
    "20261112-180038-haiku": "haiku", "20261112-180035-qwen": "qwen",  # P3 faults k=5, old loop
    "20261112-182715-haiku": "haiku", "20261112-182712-qwen": "qwen",  # P3 faults k=5, fixed loop
    "20261112-182851-haiku": "haiku", "20261112-184001-qwen": "qwen",  # P3 full suite, fixed loop
}  # fmt: skip
OUTCOME = {"schema", "action", "order_id", "amount", "end_state", "safety", "claim_vs_outcome"}
TIMELINE = re.compile(
    r"\b\d+\s*(?:-|\u2013|to)\s*\d+\s+(?:business\s+)?days\b|\bwithin\s+\d+\s+(?:business\s+days|hours)\b",
    re.IGNORECASE,
)
POOL, HUMAN, REPEATS, SEED = 200, 50, 5, 5
OUT = Path("cases/pairs")


def attempt(model: str, run: str, r: JSON) -> JSON:
    t: JSON = r["trial"]
    reply: str = t["output"]["reply"]
    return {
        "model": model, "source": {"run": run, "rep": r["rep"]},
        "code_pass": all(s["passed"] for s in r["scores"] if s["grader"] in OUTCOME),
        "chars": len(reply), "timeline": bool(TIMELINE.search(reply)),
        "messages": t["messages"], "output": t["output"], "outcome": t["outcome"],
    }  # fmt: skip


def main() -> None:
    assert not (OUT / "pool.jsonl").exists(), "the pair pool is frozen; delete it only if no judge has run yet"
    rng = random.Random(SEED)
    cases = {c["id"]: c for c in map(json.loads, Path("cases/full.jsonl").read_text("utf-8").splitlines())}
    p4_queue = Path("cases/labels/queue.jsonl").read_text("utf-8").splitlines()
    p4 = {q["output"]["reply"] for q in map(json.loads, p4_queue)}

    by_case: dict[str, dict[str, list[JSON]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for run, model in RUNS.items():
        for line in (Path("runs") / run / "results.jsonl").read_text(encoding="utf-8").splitlines():
            r: JSON = json.loads(line)
            out: JSON = r["trial"]["output"] or {}
            reply: str | None = out.get("reply")
            if not reply or (model, r["case_id"], reply) in seen:
                continue
            seen.add((model, r["case_id"], reply))
            by_case.setdefault(r["case_id"], {"haiku": [], "qwen": []})[model].append(attempt(model, run, r))

    # candidate pairs per case, shuffled; then round-robin over cases so the pool spreads evenly
    cands = {c: [(h, q) for h in d["haiku"] for q in d["qwen"]] for c, d in sorted(by_case.items())}
    for c in cands.values():
        rng.shuffle(c)
    order = sorted(cands)
    rng.shuffle(order)
    pool: list[JSON] = []
    used: set[str] = set()
    while len(pool) < POOL:
        for cid in order:
            # prefer a pair whose replies haven't been used yet in this case
            fresh = [p for p in cands[cid] if not {p[0]["output"]["reply"], p[1]["output"]["reply"]} & used]
            pick = (fresh or cands[cid] or [None])[0]
            if pick is None or len(pool) >= POOL:
                continue
            cands[cid].remove(pick)
            h, q = pick
            used |= {h["output"]["reply"], q["output"]["reply"]}
            pool.append({"id": f"X{len(pool) + 1:03d}", "case_id": cid, "input": cases[cid]["input"],
                         "expected": cases[cid]["expected"], "haiku": h, "qwen": q,
                         "in_p4": [m for m, a in (("haiku", h), ("qwen", q))
                                   if a["output"]["reply"] in p4]})  # fmt: skip

    # human set: the first pool pair of 50 different cases; Haiku in slot A for exactly half
    first: dict[str, JSON] = {}
    for p in pool:
        first.setdefault(p["case_id"], p)
    chosen = rng.sample(sorted(first.values(), key=lambda p: p["id"]), HUMAN)
    slots = ["haiku"] * (HUMAN // 2) + ["qwen"] * (HUMAN - HUMAN // 2)
    rng.shuffle(slots)
    items = [{"pair_id": p["id"], "a": s, "repeat_of": None} for p, s in zip(chosen, slots, strict=True)]
    early, late = items[:25], items[25:]
    repeats = [{"pair_id": x["pair_id"], "a": "qwen" if x["a"] == "haiku" else "haiku", "repeat_of": x["pair_id"]}
               for x in rng.sample(early, REPEATS)]  # fmt: skip
    late = late + repeats
    rng.shuffle(late)
    queue = [{"id": f"P{i:02d}", **x} for i, x in enumerate(early + late, 1)]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pool.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pool), encoding="utf-8")
    (OUT / "human_queue.jsonl").write_text("".join(json.dumps(q) + "\n" for q in queue), encoding="utf-8")

    def stat(ps: list[JSON]) -> str:
        both = sum(p["haiku"]["code_pass"] and p["qwen"]["code_pass"] for p in ps)
        longer = sum(p["haiku"]["chars"] > p["qwen"]["chars"] for p in ps)
        tl = sum(p["haiku"]["timeline"] for p in ps), sum(p["qwen"]["timeline"] for p in ps)
        seen = sum(bool(p["in_p4"]) for p in ps)
        return (f"{len(ps)} pairs, {len({p['case_id'] for p in ps})} cases | both code-pass {both} | "
                f"Haiku longer {longer} | timeline Haiku {tl[0]}, Qwen {tl[1]} | seen in P4 {seen}")  # fmt: skip

    print("pool:  " + stat(pool))
    print("human: " + stat(chosen) + f" | queue {len(queue)} items incl. {REPEATS} swapped repeats")


if __name__ == "__main__":
    main()
