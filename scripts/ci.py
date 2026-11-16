"""P8: the CI gate. Runs the shipped prompt (`haiku`) on the regression suite and compares it with the baseline on main.

    uv run python scripts/ci.py pr [--baseline PATH]   # regression cases x 3 trials; exit 1 if the gate fails
    uv run python scripts/ci.py nightly                # every case x 3 trials + the Haiku reply judge; report only
    uv run python scripts/ci.py seed                   # ci/baseline.json from the P7 lifecycle's v3 runs, regraded

Each command writes <out>/summary.md, and appends it to $GITHUB_STEP_SUMMARY when that is set. `nightly` also writes
<out>/baseline.json, which the PR job uses in place of the committed seed (it is uploaded as an artifact).

The rules below were fixed before the first CI run.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic

from evalkit.core import PRICES, Case, Task, grade, load_cases, run, trial_from_dict
from evalkit.judge import Judge, JudgeModel
from supportdesk import task
from supportdesk.judges import V2, rubric, view
from supportdesk.path import ALL
from supportdesk.shop import SYSTEM_PROMPT

type JSON = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "cases" / "full.jsonl", ROOT / "cases" / "captured.jsonl"]  # the regression suite spans both
SEED = ROOT / "ci" / "baseline.json"

# Pre-registered.
TASK = "haiku"  # the shipped prompt, src/supportdesk/system_prompt.txt
GRADERS = ALL  # outcome + path graders; a trial passes only if every applicable one passes. No judge on PRs.
TRIALS = 3
THRESHOLD = 0.95  # regression-suite trial pass rate. Truncated trials count as failures.
# Per case, against the baseline (P6's rule): a case that passed every baseline trial and now fails all 3 FAILS the
# gate; one that now fails some of its 3 is a WARNING (flaky, or the start of a regression). Cases that weren't
# always-pass in the baseline, or aren't in it (new cases), are held to the threshold only.
# Infra errors (API failures, missing trials) fail the job as infra, never as a pass: rerun it.
JUDGE = JudgeModel("claude-haiku-5-5")  # nightly only: P4's cheapest validated judge, rubric v2
CONCURRENCY = 8


@dataclass
class Result:
    passes: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))
    rows: list[JSON] = field(default_factory=list[JSON])
    errors: list[JSON] = field(default_factory=list[JSON])
    missing: list[str] = field(default_factory=list[str])
    cost: float = 0.0


def cases(suite: str | None) -> dict[Path, list[Case]]:
    return {f: [c for c in load_cases(f) if suite is None or c.suite == suite] for f in FILES}


async def run_all(out: Path, suite: str | None) -> Result:
    agent = cast(Task, getattr(task, TASK))
    picked = cases(suite)
    for f, cs in picked.items():
        await run(cs, agent, GRADERS, run_dir=out / f.stem, cases_path=f, task_name=f"supportdesk.task:{TASK}",
                  trials=TRIALS, concurrency=CONCURRENCY)  # fmt: skip
    return collect(out, picked)


def read(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def passed(r: JSON) -> bool:
    return bool(r["scores"]) and all(s["passed"] for s in r["scores"])


def collect(out: Path, picked: dict[Path, list[Case]]) -> Result:
    """Count the inputs: every case x trial must come back as a result row, or it is reported as missing."""
    res = Result()
    for f, cs in picked.items():
        d = out / f.stem
        rows, errors = read(d / "results.jsonl"), read(d / "errors.jsonl")
        res.rows += rows
        res.errors += errors
        for r in rows:
            res.passes[r["case_id"]].append(passed(r))
            res.cost += r["cost_usd"]
        got = {(r["case_id"], r["rep"]) for r in rows}
        res.missing += [f"{c.id}#{k}" for c in cs for k in range(TRIALS) if (c.id, k) not in got]
    return res


@dataclass
class Verdict:
    rate: float
    fails: list[str]  # always-pass in the baseline, now 0/3
    warns: list[str]  # always-pass in the baseline, now failing some trials
    other: list[str]  # failing trials on a case that wasn't always-pass in the baseline, or is new
    infra: bool

    @property
    def ok(self) -> bool:
        return not self.infra and not self.fails and self.rate >= THRESHOLD


def judge_gate(res: Result, baseline: JSON) -> Verdict:
    trials = [p for ps in res.passes.values() for p in ps]
    rate = sum(trials) / len(trials) if trials else 0.0
    fails: list[str] = []
    warns: list[str] = []
    other: list[str] = []
    for cid, ps in sorted(res.passes.items()):
        if all(ps):
            continue
        b = baseline["cases"].get(cid)
        always = b is not None and b[0] == b[1]
        (fails if always and not any(ps) else warns if always else other).append(cid)
    return Verdict(rate, fails, warns, other, bool(res.errors or res.missing))


def why(res: Result, cid: str) -> str:
    """The first failing grader's reason on the first failing trial: enough to start from."""
    for r in res.rows:
        if r["case_id"] == cid and not passed(r):
            if not r["scores"]:
                return f"trial {r['trial']['status']}"
            s = next(s for s in r["scores"] if not s["passed"])
            return f"{s['grader']}: {s['reason']}"[:160]
    return ""


def baseline_of(res: Result, source: str) -> JSON:
    return {
        "made": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source,
        "prompt_sha": getattr(task, TASK).meta["prompt_sha"],
        "cases": {cid: [sum(ps), len(ps)] for cid, ps in sorted(res.passes.items())},
    }


def dump_baseline(path: Path, b: JSON) -> None:
    """One case per line, so a baseline diff reads case by case."""
    head = json.dumps({k: v for k, v in b.items() if k != "cases"}, indent=1).removesuffix("\n}")
    rows = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in b["cases"].items())
    path.write_text(head + ',\n "cases": {\n' + rows + "\n }\n}\n", encoding="utf-8")


def load_baseline(path: Path) -> JSON:
    b = json.loads(path.read_text(encoding="utf-8"))
    b["path"] = (path.relative_to(ROOT) if path.is_relative_to(ROOT) else path).as_posix()
    return b


def emit(out: Path, md: str) -> None:
    (out / "summary.md").write_text(md, encoding="utf-8")
    if step := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)


def infra_lines(res: Result) -> list[str]:
    if not (res.errors or res.missing):
        return []
    lines = [f"**Infra: {len(res.errors)} error(s), {len(res.missing)} missing trial(s).** Not a regression verdict: "
             "rerun the job.", ""]  # fmt: skip
    lines += [f"- `{e['case_id']}#{e['rep']}` {e['stage']}: {e['error'][:160]}" for e in res.errors[:10]]
    return [*lines, ""]


def case_table(res: Result, baseline: JSON, ids: list[str], label: str) -> list[str]:
    if not ids:
        return []
    lines = [f"**{label}**", "", "| case | now | baseline | first failure |", "| --- | --- | --- | --- |"]
    for cid in ids:
        ps, b = res.passes[cid], baseline["cases"].get(cid)
        was = f"{b[0]}/{b[1]}" if b else "new"
        lines.append(f"| `{cid}` | {sum(ps)}/{len(ps)} | {was} | {why(res, cid).replace('|', '/')} |")
    return [*lines, ""]


async def pr(out: Path, baseline_path: Path) -> int:
    baseline = load_baseline(baseline_path)
    t0 = time.perf_counter()
    res = await run_all(out, "regression")
    secs = time.perf_counter() - t0
    v = judge_gate(res, baseline)
    n_trials = sum(len(ps) for ps in res.passes.values())
    head = "PASS" if v.ok else "FAIL (infra)" if v.infra else "FAIL"
    md = [
        f"## Eval gate: {head}",
        "",
        f"Regression suite: {len(res.passes)} cases x {TRIALS} trials on `{TASK}` "
        f"(prompt `{getattr(task, TASK).meta['prompt_sha']}`), code + path graders.",
        "",
        "| check | result | rule |",
        "| --- | --- | --- |",
        f"| trial pass rate | {sum(p for ps in res.passes.values() for p in ps)}/{n_trials} ({100 * v.rate:.1f}%) | "
        f">= {100 * THRESHOLD:.0f}% |",
        f"| newly failing cases | {len(v.fails)} | 0 (always-pass in the baseline, now 0/{TRIALS}) |",
        f"| flaky cases (warning) | {len(v.warns)} | always-pass in the baseline, now failing some trials |",
        f"| infra errors / missing trials | {len(res.errors)} / {len(res.missing)} | 0 |",
        "",
        *infra_lines(res),
        *case_table(res, baseline, v.fails, "Newly failing (gate fails)"),
        *case_table(res, baseline, v.warns, "Flaky (warning)"),
        *case_table(res, baseline, v.other, "Failing trials on cases with no clean baseline"),
        f"Baseline: `{baseline['path']}`, {baseline['source']}, made {baseline['made']} "
        f"(prompt `{baseline['prompt_sha']}`).  ",
        f"Cost ${res.cost:.3f}, eval time {secs / 60:.1f} min.",
    ]
    emit(out, "\n".join(md))
    return 0 if v.ok else 1


async def judge_replies(out: Path, res: Result) -> tuple[dict[str, int], float, list[str]]:
    """The reply judge on every trial that sent a reply, against the shipped policy. Report-only."""
    j = Judge(out / "judge-cache.jsonl", AsyncAnthropic())

    async def one(r: JSON) -> tuple[JSON, bool | None, float]:
        t = r["trial"]
        item = {"messages": t["messages"], "input": t["messages"][0]["text"], "outcome": t["outcome"],
                "output": t["output"], "expected": r["expected"]}  # fmt: skip
        g = await rubric(j, JUDGE, V2, view(item, policy=SYSTEM_PROMPT), answer=True)
        p_in, p_out = PRICES[JUDGE.model]
        usd = sum(a.tokens_in * p_in + a.tokens_out * p_out for a in g.asked.values()) / 1e6
        return r, g.passed, usd

    todo = [r for r in res.rows if r["trial"]["status"] == "ok" and r["trial"]["output"].get("reply")]
    done = await asyncio.gather(*(one(r) for r in todo))
    tally = {"both pass": 0, "both fail": 0, "code only fails": 0, "judge only fails": 0, "judge error": 0}
    disagree: list[str] = []
    for r, jp, _ in done:
        cp = passed(r)
        key = ("judge error" if jp is None else "both pass" if cp and jp else "both fail" if not (cp or jp)
               else "code only fails" if jp else "judge only fails")  # fmt: skip
        tally[key] += 1
        if key in ("code only fails", "judge only fails"):
            disagree.append(f"`{r['case_id']}#{r['rep']}` {key}")
    return tally, sum(u for *_, u in done), disagree


async def nightly(out: Path) -> int:
    t0 = time.perf_counter()
    res = await run_all(out, None)
    tally, judge_usd, disagree = await judge_replies(out, res)
    secs = time.perf_counter() - t0
    base = baseline_of(res, f"nightly {os.environ.get('GITHUB_RUN_ID', 'local')} ({TRIALS} trials per case)")
    dump_baseline(out / "baseline.json", base)
    suite = {c.id: c.suite for cs in cases(None).values() for c in cs}
    md = [f"## Nightly evals (report only): `{TASK}`, prompt `{base['prompt_sha']}`", "",
          "| suite | cases | trial pass rate | always-pass cases |", "| --- | --- | --- | --- |"]  # fmt: skip
    for s in ("regression", "capability"):
        ids = [c for c in res.passes if suite[c] == s]
        ts = [p for c in ids for p in res.passes[c]]
        md.append(f"| {s} | {len(ids)} | {sum(ts)}/{len(ts)} ({100 * sum(ts) / max(len(ts), 1):.1f}%) | "
                  f"{sum(all(res.passes[c]) for c in ids)} |")  # fmt: skip
    md += ["", *infra_lines(res), f"Reply judge ({JUDGE.model}, rubric v2, shipped policy) vs the code graders:", "",
           "| | trials |", "| --- | --- |", *(f"| {k} | {n} |" for k, n in tally.items()), ""]  # fmt: skip
    if disagree:
        md += [f"Disagreements: {', '.join(disagree[:30])}{' ...' if len(disagree) > 30 else ''}", ""]
    not_always = [c for c, ps in sorted(res.passes.items()) if not all(ps)]
    md += [f"Cases not always-pass: {', '.join(f'`{c}` {sum(res.passes[c])}/{TRIALS}' for c in not_always) or 'none'}",
           "", f"Cost: SUT ${res.cost:.3f} + judge ${judge_usd:.3f}. Time {secs / 60:.1f} min. This run's per-case "
           "results are the new baseline for PRs (artifact `baseline`)."]  # fmt: skip
    emit(out, "\n".join(md))
    return 1 if res.errors or res.missing else 0  # a partial run must not become the baseline


def seed() -> None:
    """The first baseline, before any nightly ran: P7's v3 runs (the shipped prompt, 10 trials per case), regraded
    with today's graders and labels so it reflects the cases as they now are. No model calls."""
    lc = ROOT / "runs" / "p7" / "lifecycle"
    res = Result()
    for f, cs in cases(None).items():
        by_id = {c.id: c for c in cs}
        d = lc / f"v3-{f.stem}"
        sha = json.loads((d / "meta.json").read_text(encoding="utf-8"))["task_meta"]["prompt_sha"]
        assert sha == task.haiku.meta["prompt_sha"], f"{d.name} ran prompt {sha}, not the shipped one"
        for r in read(d / "results.jsonl"):
            t = trial_from_dict(r["trial"])
            ok = t.status == "ok" and all(s.passed for s in grade(by_id[r["case_id"]], t, GRADERS))
            res.passes[r["case_id"]].append(ok)
    base = baseline_of(res, "seed: P7 lifecycle v3 runs, regraded (10 trials per case)")
    SEED.parent.mkdir(exist_ok=True)
    dump_baseline(SEED, base)
    always = sum(b[0] == b[1] for b in base["cases"].values())
    print(f"wrote {SEED.relative_to(ROOT)}: {len(base['cases'])} cases, {always} always-pass")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a_pr = sub.add_parser("pr")
    a_pr.add_argument("--baseline", type=Path, default=SEED)
    a_pr.add_argument("--out", type=Path, default=ROOT / "runs" / "ci" / f"pr-{datetime.now():%Y%m%d-%H%M%S}")
    a_n = sub.add_parser("nightly")
    a_n.add_argument("--out", type=Path, default=ROOT / "runs" / "ci" / f"nightly-{datetime.now():%Y%m%d-%H%M%S}")
    sub.add_parser("seed")
    a = p.parse_args()
    if a.cmd == "seed":
        return seed()
    a.out.mkdir(parents=True, exist_ok=True)
    code = asyncio.run(pr(a.out, a.baseline.resolve()) if a.cmd == "pr" else nightly(a.out))
    sys.exit(code)


if __name__ == "__main__":
    main()
