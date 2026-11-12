"""CLI: `run` a task over cases, `report` a run, `show` its transcripts."""

import argparse
import asyncio
import importlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from evalkit.core import JSON, Task, load_cases, run
from evalkit.graders import action_match

GRADERS = [action_match]


def load_task(spec: str) -> Task:
    module, _, name = spec.partition(":")
    return cast(Task, getattr(importlib.import_module(module), name))


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
        f"{'case':<6} {'suite':<10} {'status':<9} {'expected':<10} {'got':<10} {'pass':<5} {'calls':>5} "
        f"{'tok in/out':>12} {'$':>8} {'s':>6}"
    )
    for r in rows:
        t: dict[str, Any] = r["trial"]
        calls: list[JSON] = t["calls"]
        passed = all(s["passed"] for s in r["scores"]) if r["scores"] else None
        tok = f"{sum(c['tokens_in'] for c in calls)}/{sum(c['tokens_out'] for c in calls)}"
        print(
            f"{r['case_id']:<6} {r['suite']:<10} {t['status']:<9} {r['expected'].get('action')!s:<10} "
            f"{t['output'].get('action')!s:<10} {'-' if passed is None else 'Y' if passed else 'N':<5} "
            f"{len(calls):>5} {tok:>12} {r['cost_usd']:>8.4f} {t['latency_ms'] / 1000:>6.1f}"
        )
    for e in errors:
        print(f"{e['case_id']:<6} ERROR {e['stage']}: {e['error']}")
    graded = [r for r in rows if r["scores"]]
    n_pass = sum(all(s["passed"] for s in r["scores"]) for r in graded)
    other = len(rows) - len(graded)
    print(
        f"\npassed {n_pass}/{len(graded)} graded | truncated/refused {other} | infra errors {len(errors)} | "
        f"cost ${sum(r['cost_usd'] for r in rows):.4f}"
    )


def show(run_dir: Path, case_id: str | None) -> None:
    for r in sorted(read_rows(run_dir, "results.jsonl"), key=lambda r: (r["case_id"], r["rep"])):
        if case_id and r["case_id"] != case_id:
            continue
        t = r["trial"]
        print(f"=== {r['case_id']} rep {r['rep']}  status {t['status']}  stop {t['stop_reason']}  scores {r['scores']}")
        for m in t["messages"]:
            if m["role"] == "tool":
                print(f"  [tool {m['name']}{' ERROR' if m['error'] else ''}] {json.dumps(m['result'])}")
            else:
                if m.get("text"):
                    print(f"  [{m['role']}] {m['text']}")
                for c in m.get("tool_calls", []):
                    print(f"  [{m['role']} -> {c['name']}] {json.dumps(c['args'])}")
        print(f"  output  {json.dumps(t['output'])}\n  outcome {json.dumps(t['outcome'])}\n")


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]  # cp1252 console
    p = argparse.ArgumentParser(prog="evalkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cases", type=Path, required=True)
    r.add_argument("--task", required=True, help="module:attr, e.g. supportdesk.task:haiku")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--only", help="comma-separated case ids")
    r.add_argument("--out", type=Path, default=Path("runs"))
    rp = sub.add_parser("report")
    rp.add_argument("run_dir", type=Path)
    s = sub.add_parser("show")
    s.add_argument("run_dir", type=Path)
    s.add_argument("case_id", nargs="?")
    a = p.parse_args()

    if a.cmd == "run":
        cases = load_cases(a.cases)
        if a.only:
            cases = [c for c in cases if c.id in a.only.split(",")]
        run_dir = a.out / f"{datetime.now():%Y%m%d-%H%M%S}-{a.task.rpartition(':')[2]}"
        asyncio.run(
            run(
                cases,
                load_task(a.task),
                GRADERS,
                run_dir=run_dir,
                cases_path=a.cases,
                task_name=a.task,
                trials=a.trials,
                concurrency=a.concurrency,
            )
        )
        report(run_dir)
    elif a.cmd == "report":
        report(a.run_dir)
    else:
        show(a.run_dir, a.case_id)


if __name__ == "__main__":
    main()
