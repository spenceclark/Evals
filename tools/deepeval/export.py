"""DeepEval's recorded run (.deepeval/.latest_test_run.json, written by `deepeval test run`) -> p9_results.jsonl, plus
the pass counts beside tools/p9/reference.jsonl and the run's cost.

    uv run python export.py [RUN_JSON]    # default .deepeval/.latest_test_run.json
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from p9 import HERE, RUBRIC, TOOL

type JSON = dict[str, Any]

RUN = HERE / ".deepeval" / ".latest_test_run.json"
OUT = HERE / "p9_results.jsonl"
REFERENCE = HERE.parent / "p9" / "reference.jsonl"
GRADERS = ("action", "path", "rubric")


def verdict(metrics: dict[str, JSON], name: str) -> bool | None:
    """A metric's success, or None if it didn't run or errored (an error is not a fail)."""
    m = metrics.get(name)
    return None if m is None or m.get("error") else bool(m["success"])


def metrics_data(tc: JSON) -> list[JSON]:
    return tc.get("metricsData") or []


def row(tc: JSON) -> JSON:
    # GEval records its name with a " [GEval]" suffix; our code metrics keep theirs.
    metrics = {m["name"].removesuffix(" [GEval]"): m for m in metrics_data(tc)}
    rubric = [verdict(metrics, c) for c in RUBRIC]
    meta = tc["metadata"]
    return {
        "tool": TOOL,
        "case_id": meta["case_id"],
        "rep": meta["rep"],
        "action": verdict(metrics, "action"),
        "path": verdict(metrics, "looked_up_first"),
        "rubric": None if None in rubric else all(rubric),
        "reply": tc.get("actualOutput"),
    }


def counts(rows: list[JSON]) -> dict[str, str]:
    out: dict[str, str] = {}
    for g in GRADERS:
        vals = [r[g] for r in rows if r[g] is not None]
        out[g] = f"{sum(vals)}/{len(vals)}"
    return out


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else RUN
    run = json.loads(path.read_text(encoding="utf-8"))["testRunData"]
    cases: list[JSON] = run["testCases"]
    rows = sorted((row(tc) for tc in cases), key=lambda r: (r["case_id"], r["rep"]))
    dupes = [k for k, n in Counter((r["case_id"], r["rep"]) for r in rows).items() if n > 1]
    assert len(rows) == 30 and not dupes, f"{len(rows)} rows, duplicates {dupes}"
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    agent = sum(tc.get("tokenCost") or 0 for tc in cases)
    judge = sum(m.get("evaluationCost") or 0 for tc in cases for m in metrics_data(tc))
    ref = [json.loads(x) for x in REFERENCE.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"wrote {OUT.name}: {len(rows)} trials; agent ${agent:.4f} + judge ${judge:.4f} = ${agent + judge:.4f}")
    mine, theirs = counts(rows), counts(ref)
    for g in GRADERS:
        print(f"  {g:<7} deepeval {mine[g]:>6}   reference {theirs[g]:>6}")
    for g in GRADERS:
        fails = [f"{r['case_id']}#{r['rep']}" for r in rows if r[g] is False]
        nulls = [f"{r['case_id']}#{r['rep']}" for r in rows if r[g] is None]
        print(f"  {g} fails: {fails or '-'}" + (f"; no verdict: {nulls}" if nulls else ""))
    for tc in cases:  # per-criterion fails, with the judge's reason
        for m in metrics_data(tc):
            if m["name"].endswith(" [GEval]") and not m["success"]:
                print(f"    {tc['name']} {m['name']}: {m.get('error') or m.get('reason')}")


if __name__ == "__main__":
    main()
