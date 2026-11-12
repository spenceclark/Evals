"""CLI: `run` a task over cases, `regrade` a saved run, `report` it, `show` its transcripts."""

import argparse
import asyncio
import importlib
import io
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from evalkit.core import JSON, Grader, Task, load_cases, regrade, run


def load(spec: str) -> Any:
    """module:attr -> the object."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def read_rows(run_dir: Path, name: str) -> list[JSON]:
    path = run_dir / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def report(run_dir: Path) -> None:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = sorted(read_rows(run_dir, "results.jsonl"), key=lambda r: (r["case_id"], r["rep"]))
    errors = read_rows(run_dir, "errors.jsonl")
    print(f"run {meta['run_id']}  task {meta['task']}  git {meta['git']}  cases {meta['cases']} ({meta['cases_sha']})")
    print(
        f"{'case':<10} {'suite':<5} {'stop':<18} {'expected -> got':<24} {'calls':>5} {'$':>7} {'s':>5}  failed graders"
    )
    for r in rows:
        t: dict[str, Any] = r["trial"]
        failed = [s["grader"] for s in r["scores"] if not s["passed"]]
        status = t["stop_reason"] if t["status"] == "ok" else t["status"].upper()
        acts = f"{r['expected'].get('action')} -> {t['output'].get('action')}"
        print(
            f"{r['case_id']:<10} {r['suite'][:5]:<5} {status:<18} {acts:<24} {len(t['calls']):>5} "
            f"{r['cost_usd']:>7.4f} {t['latency_ms'] / 1000:>5.1f}  {', '.join(failed) or '-'}"
        )
    for e in errors:
        print(f"{e['case_id']:<10} ERROR {e['stage']}: {e['error']}")

    # Per grader and suite: passed / applicable. Truncated or refused trials have no scores and are counted apart.
    tally: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    suites = sorted({r["suite"] for r in rows}, reverse=True)
    graders = list(dict.fromkeys(s["grader"] for r in rows for s in r["scores"]))
    for r in rows:
        if not r["scores"]:
            continue
        for s in r["scores"]:
            for key in ((s["grader"], r["suite"]), (s["grader"], "all")):
                tally[key][0] += s["passed"]
                tally[key][1] += 1
        for key in (("ALL GRADERS", r["suite"]), ("ALL GRADERS", "all")):
            tally[key][0] += all(s["passed"] for s in r["scores"])
            tally[key][1] += 1

    def cell(k: tuple[str, str]) -> str:
        p, n = tally[k]
        return f"{p}/{n} ({100 * p / n:.0f}%)" if n else "-"

    print(f"\n{'grader':<18}" + "".join(f"{s:>18}" for s in [*suites, "all"]))
    for g in [*graders, "ALL GRADERS"]:
        print(f"{g:<18}" + "".join(f"{cell((g, s)):>18}" for s in [*suites, "all"]))
    ungraded = sum(1 for r in rows if not r["scores"])
    print(
        f"\ntrials {len(rows)} | truncated/refused {ungraded} | infra errors {len(errors)} | "
        f"cost ${sum(r['cost_usd'] for r in rows):.4f}"
    )


def show(run_dir: Path, case_id: str | None) -> None:
    for r in sorted(read_rows(run_dir, "results.jsonl"), key=lambda r: (r["case_id"], r["rep"])):
        if case_id and r["case_id"] != case_id:
            continue
        t = r["trial"]
        print(f"=== {r['case_id']} rep {r['rep']}  status {t['status']}  stop {t['stop_reason']}")
        for m in t["messages"]:
            if m["role"] == "tool":
                print(f"  [tool {m['name']}{' ERROR' if m['error'] else ''}] {json.dumps(m['result'])}")
            else:
                if m.get("text"):
                    print(f"  [{m['role']}] {m['text']}")
                for c in m.get("tool_calls", []):
                    print(f"  [{m['role']} -> {c['name']}] {json.dumps(c['args'])}")
        print(f"  output  {json.dumps(t['output'])}\n  outcome {json.dumps(t['outcome'])}")
        print(f"  expected {json.dumps(r['expected'])}")
        for s in r["scores"]:
            print(f"  {'PASS' if s['passed'] else 'FAIL'} {s['grader']}: {s['reason']}")
        print()


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]  # cp1252 console
    p = argparse.ArgumentParser(prog="evalkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cases", type=Path, required=True)
    r.add_argument("--task", required=True, help="module:attr, e.g. supportdesk.task:haiku")
    r.add_argument("--graders", required=True, help="module:attr of a list, e.g. supportdesk.graders:GRADERS")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--suite", help="only cases from this suite")
    r.add_argument("--only", help="comma-separated case ids")
    r.add_argument("--out", type=Path, default=Path("runs"))
    g = sub.add_parser("regrade", help="re-score a saved run with (new) graders; no model calls")
    g.add_argument("run_dir", type=Path)
    g.add_argument("--graders", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("run_dir", type=Path)
    s = sub.add_parser("show")
    s.add_argument("run_dir", type=Path)
    s.add_argument("case_id", nargs="?")
    a = p.parse_args()

    if a.cmd == "run":
        cases = load_cases(a.cases)
        if a.suite:
            cases = [c for c in cases if c.suite == a.suite]
        if a.only:
            cases = [c for c in cases if c.id in a.only.split(",")]
        run_dir = a.out / f"{datetime.now():%Y%m%d-%H%M%S}-{a.task.rpartition(':')[2]}"
        graders = cast(list[Grader], load(a.graders))
        task = cast(Task, load(a.task))
        asyncio.run(
            run(cases, task, graders, run_dir=run_dir, cases_path=a.cases, task_name=a.task, trials=a.trials,
                concurrency=a.concurrency)
        )  # fmt: skip
        report(run_dir)
    elif a.cmd == "regrade":
        meta = json.loads((a.run_dir / "meta.json").read_text(encoding="utf-8"))
        regrade(a.run_dir, load_cases(Path(meta["cases"])), cast(list[Grader], load(a.graders)))
        report(a.run_dir)
    elif a.cmd == "report":
        report(a.run_dir)
    else:
        show(a.run_dir, a.case_id)


if __name__ == "__main__":
    main()
