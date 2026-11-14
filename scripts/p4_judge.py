"""Run a reply judge over the human-labelled items and measure agreement with the labels (P4: O8, O9, O10).

    uv run python scripts/p4_judge.py rubric --version v0 --split dev [--answer] [--model opus] [--samples 3] [--show]
    uv run python scripts/p4_judge.py reference --split dev
    uv run python scripts/p4_judge.py pairwise --split all [--model haiku]
    uv run python scripts/p4_judge.py negatives --version v0

Positive class = pass. TPR = judge passes what the human passed; TNR = judge fails what the human failed. Errors (no
verdict) are reported separately and left out of agreement. Disagreements are printed with --show; leave it off on test
until the final prompt is chosen. Every judge input and output is kept in runs/p4/cache.jsonl.
"""

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from evalkit.core import PRICES
from evalkit.judge import Asked, Judge, JudgeModel
from evalkit.stats import kappa, wilson
from supportdesk.judges import NEGATIVES, RUBRICS, Graded, pairwise, reference, rubric, view

type JSON = dict[str, Any]

MODELS = {
    "opus": JudgeModel("claude-opus-5-5"),
    "sonnet": JudgeModel("claude-sonnet-5-5"),
    "haiku": JudgeModel("claude-haiku-5-5"),
    "qwen": JudgeModel("Qwen3.5-9B-Unsloth:IQ4", backend="openai", effort=None, max_tokens=4096),
}
OUT = Path("runs/p4")
REVISED = {"L049", "L050", "L081", "L090"}  # human labels corrected (pass -> fail) before the split


def load() -> tuple[dict[str, JSON], dict[str, JSON], JSON, dict[str, str]]:
    def rows(p: str) -> list[JSON]:
        return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]

    queue = {q["id"]: q for q in rows("cases/labels/queue.jsonl")}
    labels = {r["id"]: r for r in rows("cases/labels/labels.jsonl")}
    split: JSON = json.loads(Path("cases/labels/split.json").read_text(encoding="utf-8"))
    refs = {r["case_id"]: r["reference_reply"] for r in rows("cases/labels/reference_replies.jsonl")}
    return queue, labels, split, refs


def make_judge() -> Judge:
    vessel = os.environ.get("VESSEL_URL", "").rstrip("/")
    headers = {"X-Vessel-Tags": "judge", "X-Vessel-Session": "p4-judges"} if vessel else {}
    anthropic = AsyncAnthropic(base_url=f"{vessel}/b/anthropic" if vessel else None, default_headers=headers)
    base = f"{vessel}/b/ollama/v1" if vessel else "http://127.0.0.1:11434/v1"
    openai = AsyncOpenAI(base_url=base, api_key="ollama", default_headers=headers)
    OUT.mkdir(parents=True, exist_ok=True)
    return Judge(OUT / "cache.jsonl", anthropic, openai)


def usd(asked: list[Asked]) -> float:
    return sum((a.tokens_in * PRICES.get(a.model, (0, 0))[0] + a.tokens_out * PRICES.get(a.model, (0, 0))[1]) / 1e6
               for a in asked)  # fmt: skip


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} [{lo:.0%}-{hi:.0%}]" if n else "n/a"


def report(rows: list[JSON], kinds: dict[str, str], show: bool) -> None:
    ok = [r for r in rows if r["judge"] is not None]
    errs = len(rows) - len(ok)
    hp = [r for r in ok if r["human"]]
    hf = [r for r in ok if not r["human"]]
    tp, tn = sum(r["judge"] for r in hp), sum(not r["judge"] for r in hf)
    print(f"  items {len(rows)} | no verdict {errs} | human pass {len(hp)}, fail {len(hf)}")
    print(f"  confusion (human x judge): pass/pass {tp}  pass/FAIL {len(hp) - tp}  fail/PASS {len(hf) - tn}  "
          f"fail/fail {tn}")  # fmt: skip
    print(f"  TPR (passes kept)  {pct(tp, len(hp))}")
    print(f"  TNR (fails caught) {pct(tn, len(hf))}")
    if ok:
        print(f"  kappa {kappa([r['human'] for r in ok], [r['judge'] for r in ok]):.2f} | "
              f"raw agreement {pct(tp + tn, len(ok))}")  # fmt: skip
    by_kind = Counter(kinds[r["id"]] for r in hf)
    caught = Counter(kinds[r["id"]] for r in hf if not r["judge"])
    print("  fails caught by kind: " + ", ".join(f"{k} {caught[k]}/{n}" for k, n in sorted(by_kind.items())))
    cp = [r for r in hf if r["code_pass"]]
    print(f"  fails the code graders missed, caught by the judge: {pct(sum(not r['judge'] for r in cp), len(cp))}")
    rev = [r for r in ok if r["id"] in REVISED]
    if rev:
        print(f"  on the {len(rev)} revised labels: judge says fail on {sum(not r['judge'] for r in rev)} "
              "(original label pass, revised fail)")  # fmt: skip
    asked = [a for r in rows for a in r["asked"]]
    print(f"  calls {len(asked)} | ${usd(asked):.2f} | ${usd(asked) / max(len(rows), 1) * 100:.2f} per 100 items | "
          f"mean {sum(a.ms for a in asked) / max(len(asked), 1) / 1000:.1f}s per call")  # fmt: skip
    if show:
        for r in rows:
            if r["judge"] is None or r["judge"] != r["human"]:
                print(f"\n  {r['id']} {r['case_id']} human={'pass' if r['human'] else 'FAIL'} "
                      f"judge={'n/a' if r['judge'] is None else 'pass' if r['judge'] else 'FAIL'} | "
                      f"human: {r['critique']}")  # fmt: skip
                for a in r["asked"]:
                    v = a.verdict
                    mark = a.status if v is None else ("pass" if v["passed"] else "FAIL")
                    print(f"     [{mark}] {v['critique'] if v else a.text[:300]}")


async def run_pairwise(judge: Judge, jm: JudgeModel, queue: dict[str, JSON], labels: dict[str, JSON], split: JSON,
                       ids: list[str], show: bool) -> None:  # fmt: skip
    """Label-implied pairs: same case, one reply the human passed and one they failed, so the human prefers the pass.
    Each pair is judged in both orders; a pair counts as right only if both orders pick the passed reply."""
    by_case: dict[str, dict[str, list[str]]] = {}
    for i in ids:
        by_case.setdefault(queue[i]["source"]["case_id"], {"pass": [], "fail": []})[labels[i]["label"]].append(i)
    pairs = [(p, f) for d in by_case.values() for p in d["pass"] for f in d["fail"]]
    first = await asyncio.gather(*(pairwise(judge, jm, view(queue[p]), view(queue[f])) for p, f in pairs))
    second = await asyncio.gather(*(pairwise(judge, jm, view(queue[f]), view(queue[p])) for p, f in pairs))

    def pick(x: Asked, pass_slot: str) -> str | None:  # which reply the judge preferred: "pass", "fail", "tie"
        if x.verdict is None:
            return None
        b = x.verdict["better"]
        return "tie" if b == "tie" else "pass" if b == pass_slot else "fail"

    rows = [(p, f, pick(x, "A"), pick(y, "B")) for (p, f), x, y in zip(pairs, first, second, strict=True)]
    ok = [r for r in rows if None not in r[2:]]
    consistent = [r for r in ok if r[2] == r[3]]
    right = sum(r[2] == "pass" for r in consistent)
    inconsistent = [r for r in ok if r[2] != r[3]]
    # among inconsistent pairs, did the judge favour whatever sat in position A?
    first_wins = sum((r[2] == "pass") + (r[3] == "fail") for r in inconsistent)
    name = f"pairwise-v0-{jm.model}"
    n_cases = len({queue[p]["source"]["case_id"] for p, _ in pairs})
    print(f"\n{name}: {len(pairs)} pairs from {n_cases} cases | no verdict {len(rows) - len(ok)}")
    print(f"  prefers the human-passed reply in both orders: {pct(right, len(ok))}")
    print(f"  either order alone: A-first {pct(sum(r[2] == 'pass' for r in ok), len(ok))}, "
          f"B-first {pct(sum(r[3] == 'pass' for r in ok), len(ok))}")  # fmt: skip
    print(f"  position-consistent: {pct(len(consistent), len(ok))} | ties: {sum(r[2:].count('tie') for r in ok)} "
          f"| inconsistent pairs: {len(inconsistent)}, position A won {first_wins} of their "
          f"{2 * len(inconsistent)} judgements")  # fmt: skip
    kinds = split["fail_kind"]
    by_kind = Counter(kinds[f] for _, f, *_ in ok)
    got = Counter(kinds[r[1]] for r in consistent if r[2] == "pass")
    print("  right by the failed reply's kind: " + ", ".join(f"{k} {got[k]}/{n}" for k, n in sorted(by_kind.items())))
    test = set(split["test"])
    for name_, part in (("dev", [r for r in ok if r[0] not in test]), ("test", [r for r in ok if r[0] in test])):
        print(f"  {name_}: {pct(sum(r[2] == r[3] == 'pass' for r in part), len(part))}")
    asked = [*first, *second]
    print(f"  calls {len(asked)} | ${usd(asked):.2f}")
    if show:
        for (p, f, x, y), a1, a2 in zip(rows, first, second, strict=True):
            if not x == y == "pass":
                print(f"\n  pass {p} vs fail {f} ({kinds[f]}): A-first -> {x}, B-first -> {y} | human: "
                      f"{labels[f]['critique']}")  # fmt: skip
                for q in (a1, a2):
                    print(f"     {q.verdict['critique'][:400] if q.verdict else q.status}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("judge", choices=["rubric", "reference", "pairwise", "negatives"])
    ap.add_argument("--version", default="v0")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--model", choices=list(MODELS), default="opus")
    ap.add_argument("--answer", action="store_true", help="also show the judge the correct resolution under policy")
    ap.add_argument("--samples", type=int, default=1, help="ask each question N times (self-consistency)")
    ap.add_argument("--only", help="comma-separated item ids")
    ap.add_argument("--show", action="store_true", help="print every disagreement with the judge's critiques")
    a = ap.parse_args()

    queue, labels, split, refs = load()
    judge, jm = make_judge(), MODELS[a.model]
    ids = sorted(queue) if a.split == "all" else split[a.split]
    if a.only:
        ids = [i for i in ids if i in a.only.split(",")]

    if a.judge == "negatives":
        rb = RUBRICS[a.version]
        # every refund/deny item in dev: a reply that is empty, says "I don't know", or answers a question not asked
        items = [i for i in split["dev"] if queue[i]["expected"]["action"] in ("refunded", "denied")][:4]
        jobs = [(i, n, rubric(judge, jm, rb, view(queue[i], reply), a.answer)) for i in items
                for n, reply in NEGATIVES.items()]  # fmt: skip
        res = await asyncio.gather(*(j for _, _, j in jobs))
        for (i, n, _), g in zip(jobs, res, strict=True):
            fails = [k for k, x in g.asked.items() if x.verdict and not x.verdict["passed"]]
            print(f"  {i} {n:15} -> {'FAIL' if g.passed is False else 'PASS' if g.passed else 'n/a'}  failed: {fails}")
        print(f"  all failed: {all(g.passed is False for g in res)} | "
              f"${usd([x for g in res for x in g.asked.values()]):.2f}")  # fmt: skip
        return

    if a.judge == "pairwise":
        await run_pairwise(judge, jm, queue, labels, split, ids, a.show)
        return

    if a.judge == "reference":
        ids = [i for i in ids if queue[i]["source"]["case_id"] in refs]

    async def one(i: str, s: int) -> Graded:
        v = view(queue[i])
        if a.judge == "rubric":
            return await rubric(judge, jm, RUBRICS[a.version], v, a.answer, s)
        return await reference(judge, jm, v, refs[queue[i]["source"]["case_id"]], s)

    name = f"{a.judge}-{a.version if a.judge == 'rubric' else 'v0'}{'-answer' if a.answer else ''}-{a.model}-{a.split}"
    per_sample: list[list[JSON]] = []
    for s in range(a.samples):
        graded = await asyncio.gather(*(one(i, s) for i in ids))
        rows = [{"id": i, "case_id": queue[i]["source"]["case_id"], "human": labels[i]["label"] == "pass",
                 "critique": labels[i]["critique"], "code_pass": queue[i]["code_pass"], "judge": g.passed,
                 "asked": list(g.asked.values())} for i, g in zip(ids, graded, strict=True)]  # fmt: skip
        per_sample.append(rows)
        print(f"\n{name} (sample {s})")
        report(rows, split["fail_kind"], a.show and s == 0)

    out = [{k: v for k, v in r.items() if k != "asked"} | {"sample": s, "asked": [x.key for x in r["asked"]]}
           for s, rows in enumerate(per_sample) for r in rows]  # fmt: skip
    (OUT / f"{name}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in out), encoding="utf-8")

    if a.samples > 1:
        verdicts = [[rows[k]["judge"] for rows in per_sample] for k in range(len(ids))]
        full = [v for v in verdicts if None not in v]
        flips = sum(len(set(v)) > 1 for v in full)
        print(f"\nself-consistency over {a.samples} samples: overall verdict flips on {pct(flips, len(full))} items")


if __name__ == "__main__":
    asyncio.run(main())
