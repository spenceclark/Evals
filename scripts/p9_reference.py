"""P9: freeze the slice every framework port runs, and score it once with our own harness as the reference.

    uv run python scripts/p9_reference.py slice    # writes tools/p9/slice.jsonl (no model calls)
    uv run python scripts/p9_reference.py run      # 10 cases x 3 trials + the reply judge -> tools/p9/reference.jsonl

The three graders every port implements, in that tool's own idiom:
- action (code): the close_ticket action equals the expected one (supportdesk.graders.action, `also_ok` honoured);
- rubric (judge): rubric v2's three criteria on the reply, Haiku 5.5 as judge, no answer key; the reply passes only if
  all three pass (supportdesk.judges.V2, the judge validated in P4);
- path (trajectory): no refund or escalation before a successful get_order of that order
  (supportdesk.path.looked_up_first).
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from evalkit.core import Case, grade, load_cases, run, trial_from_dict
from evalkit.judge import Judge, JudgeModel
from supportdesk import task
from supportdesk.graders import action
from supportdesk.judges import V2, rubric, view
from supportdesk.path import looked_up_first
from supportdesk.shop import SYSTEM_PROMPT

type JSON = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
P9 = ROOT / "tools" / "p9"
SLICE = P9 / "slice.jsonl"
REFERENCE = P9 / "reference.jsonl"
OUT = ROOT / "runs" / "p9" / "reference"

# One case per group, regression and capability, plus a P7 captured case. Fixed before any port ran.
SLICE_IDS = ["elig-01", "win-01", "dig-01", "esc-01", "canc-01", "info-01", "look-03", "fault-01", "inj-01",
             "cap-t002"]  # fmt: skip
TRIALS = 3
JUDGE = JudgeModel("claude-haiku-5-5")


def make_slice() -> None:
    rows: dict[str, JSON] = {}
    for f in ("full.jsonl", "captured.jsonl"):
        for line in (ROOT / "cases" / f).read_text(encoding="utf-8").splitlines():
            if line.strip() and (r := json.loads(line))["id"] in SLICE_IDS:
                rows[r["id"]] = r
    assert set(rows) == set(SLICE_IDS), set(SLICE_IDS) - set(rows)
    P9.mkdir(parents=True, exist_ok=True)
    SLICE.write_text("".join(json.dumps(rows[i], ensure_ascii=False) + "\n" for i in SLICE_IDS), encoding="utf-8")
    print(f"wrote {SLICE.relative_to(ROOT)}: {len(rows)} cases")


async def judge_reply(j: Judge, case: Case, t: JSON) -> bool | None:
    item = {"messages": t["messages"], "input": case.input, "outcome": t["outcome"], "output": t["output"]}
    return (await rubric(j, JUDGE, V2, view(item, policy=SYSTEM_PROMPT), answer=False)).passed


async def reference() -> None:
    cases = load_cases(SLICE)
    by_id = {c.id: c for c in cases}
    await run(cases, task.haiku, [action, looked_up_first], run_dir=OUT, cases_path=SLICE,
              task_name="supportdesk.task:haiku", trials=TRIALS, concurrency=8)  # fmt: skip
    rows = [json.loads(x) for x in (OUT / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == len(cases) * TRIALS, f"{len(rows)} results, expected {len(cases) * TRIALS}"
    j = Judge(OUT / "judge-cache.jsonl", AsyncAnthropic())
    replies = [r["trial"]["output"].get("reply") for r in rows]
    verdicts = await asyncio.gather(*(judge_reply(j, by_id[r["case_id"]], r["trial"]) if reply else _none()
                                      for r, reply in zip(rows, replies, strict=True)))  # fmt: skip
    out: list[JSON] = []
    for r, reply, v in sorted(zip(rows, replies, verdicts, strict=True), key=lambda x: (x[0]["case_id"], x[0]["rep"])):
        t = trial_from_dict(r["trial"])
        s = {x.grader: x.passed for x in grade(by_id[r["case_id"]], t, [action, looked_up_first])}
        out.append({"tool": "evalkit", "case_id": r["case_id"], "rep": r["rep"], "action": s.get("action"),
                    "path": s.get("looked_up_first"), "rubric": v, "reply": reply})  # fmt: skip
    REFERENCE.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out), encoding="utf-8")
    cost = sum(r["cost_usd"] for r in rows)
    print(f"wrote {REFERENCE.relative_to(ROOT)}: {len(out)} trials, agent ${cost:.3f}")
    for g in ("action", "path", "rubric"):
        vals = [o[g] for o in out if o[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)} pass ({len(out) - len(vals)} not applicable / no verdict)")


async def _none() -> None:
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["slice", "run"])
    a = p.parse_args()
    if a.cmd == "slice":
        make_slice()
    else:
        asyncio.run(reference())


if __name__ == "__main__":
    main()
