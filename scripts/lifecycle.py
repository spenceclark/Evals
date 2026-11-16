"""The P7 lifecycle: v1 -> v2 -> v3 on every case, the 66 generated and the 20 captured from simulated traffic.

    uv run python scripts/lifecycle.py run        # 3 prompts x (66 + 20) cases x 10 trials, direct (no Vessel)
    uv run python scripts/lifecycle.py report     # per-version rates, per captured case, graduation
    uv run python scripts/lifecycle.py report --apply   # also moves graduated cases to the regression suite

The rules below were fixed before the first lifecycle trial ran.
"""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from evalkit.core import Task, load_cases, run
from supportdesk import task
from supportdesk.path import ALL

type JSON = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "p7" / "lifecycle"
FULL, CAPTURED = ROOT / "cases" / "full.jsonl", ROOT / "cases" / "captured.jsonl"
VERSIONS = {"v1": "haiku_v1", "v2": "haiku_v2", "v3": "haiku_v3"}  # v2 ran as `haiku` before P8 shipped v3

# Pre-registered (written before any lifecycle trial).
TRIALS = 10
GRADERS = ALL  # a trial passes only if every applicable grader passes, as in P3-P6
# A captured case graduates to the regression suite when the fixed prompt passes it in every trial: 10/10.
GRADUATE_AT = TRIALS
# Done-when: at least one captured case that the old prompt (v2) fails in at least half its trials passes 10/10 on v3.
OLD_FAILS_AT_MOST = TRIALS // 2
# A correct agent can't pass these: they need a refund AND an escalation, and close_ticket records one action, so
# claim_vs_outcome fails even the oracle. A limit of the tool, not the prompt. Reported apart; never graduate in P7.
TOOL_LIMIT = {"cap-t017", "cap-t039"}


def run_dir(v: str, which: str) -> Path:
    return OUT / f"{v}-{which}"


async def run_all(concurrency: int) -> None:
    for v, attr in VERSIONS.items():
        for which, path in (("full", FULL), ("captured", CAPTURED)):
            d = run_dir(v, which)
            if (d / "results.jsonl").exists():
                print(f"{d.name}: already run, skipping")
                continue
            agent = cast(Task, getattr(task, attr))
            await run(load_cases(path), agent, GRADERS, run_dir=d, cases_path=path,
                      task_name=f"supportdesk.task:{attr}", trials=TRIALS, concurrency=concurrency)  # fmt: skip
            print(f"{d.name}: done")


def passes(v: str, which: str) -> tuple[dict[str, list[bool]], float, int]:
    """case id -> pass/fail per trial (truncated counts as a fail), total cost, infra errors."""
    d = run_dir(v, which)
    out: dict[str, list[bool]] = defaultdict(list)
    cost = 0.0
    for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        cost += r["cost_usd"]
        out[r["case_id"]].append(bool(r["scores"]) and all(s["passed"] for s in r["scores"]))
    errors = d / "errors.jsonl"
    n_err = len(errors.read_text(encoding="utf-8").splitlines()) if errors.exists() else 0
    return out, cost, n_err


def report(apply: bool) -> None:
    suite = {c.id: c.suite for c in load_cases(FULL)}
    res: dict[str, dict[str, list[bool]]] = {}
    total = 0.0
    for v in VERSIONS:
        res[v] = {}
        for which in ("full", "captured"):
            p, cost, n_err = passes(v, which)
            res[v] |= p
            total += cost
            if n_err:
                print(f"  {v}-{which}: {n_err} infra errors (not scored)")

    def rate(v: str, ids: list[str]) -> str:
        trials = [t for i in ids for t in res[v].get(i, [])]
        return f"{sum(trials)}/{len(trials)} ({100 * sum(trials) / max(len(trials), 1):.1f}%)"

    groups = {
        "regression (generated)": [i for i, s in suite.items() if s == "regression"],
        "capability (generated)": [i for i, s in suite.items() if s == "capability"],
        "captured": [i for i in res["v3"] if i.startswith("cap-") and i not in TOOL_LIMIT],
        "captured, tool limit": sorted(TOOL_LIMIT),
    }
    print(f"trial pass rate, all graders ({TRIALS} trials per case)")
    print(f"  {'cases':<24} {'n':>3}  " + "  ".join(f"{v:>16}" for v in VERSIONS))
    for name, ids in groups.items():
        print(f"  {name:<24} {len(ids):>3}  " + "  ".join(f"{rate(v, ids):>16}" for v in VERSIONS))

    print("\ncaptured cases: passes out of 10")
    print(f"  {'case':<10} {'tags':<26} " + " ".join(f"{v:>3}" for v in VERSIONS) + "  verdict")
    captured = load_cases(CAPTURED)
    graduated: list[str] = []
    fixed: list[str] = []
    for c in captured:
        n = {v: sum(res[v].get(c.id, [])) for v in VERSIONS}
        if c.id in TOOL_LIMIT:
            verdict = "stays capability (tool limit)"
        elif n["v3"] >= GRADUATE_AT:
            graduated.append(c.id)
            verdict = "graduates"
            if n["v2"] <= OLD_FAILS_AT_MOST:
                fixed.append(c.id)
                verdict += " (fixed by v3)"
        else:
            verdict = "stays capability"
        print(f"  {c.id:<10} {','.join(c.tags)[:26]:<26} " + " ".join(f"{n[v]:>3}" for v in VERSIONS)
              + f"  {verdict}")  # fmt: skip

    worse = [(i, sum(res["v2"][i]), sum(res["v3"][i])) for i in suite if sum(res["v3"][i]) < sum(res["v2"][i])]
    print(f"\ngenerated cases where v3 passes fewer trials than v2: {worse or 'none'}")
    print(f"done-when (a captured case v2 fails in >= half its trials, v3 passes 10/10): {fixed or 'NOT MET'}")
    print(f"graduating: {len(graduated)} of {len(captured)} captured cases | cost ${total:.2f}")

    if apply:
        rows = [json.loads(x) for x in CAPTURED.read_text(encoding="utf-8").splitlines() if x.strip()]
        for r in rows:
            if r["id"] in graduated:
                r["suite"] = "regression"
        CAPTURED.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"moved {len(graduated)} cases to the regression suite in {CAPTURED.relative_to(ROOT)}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--concurrency", type=int, default=12)
    rp = sub.add_parser("report")
    rp.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if a.cmd == "run":
        asyncio.run(run_all(a.concurrency))
    else:
        report(a.apply)


if __name__ == "__main__":
    main()
