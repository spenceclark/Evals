"""Judge form vs trial variance: grade the replies DeepEval recorded with P4's own rubric v2 judge (supportdesk.judges,
the one reference.jsonl used), so the G-Eval verdicts can be compared with P4's on the same 30 replies.

    uv run python p4_judge.py [RUN_JSON]    # default .deepeval/.latest_test_run.json; prints both verdicts per trial

Rebuilds supportdesk.judges.view's fields from the recorded test case (the tool calls are regrouped by turn, as view
prints them), then calls supportdesk.judges.rubric. Not part of the port: a check on it.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from export import row
from p9 import HERE

from evalkit.judge import Judge, JudgeModel
from supportdesk.judges import V2, rubric
from supportdesk.shop import SYSTEM_PROMPT

type JSON = dict[str, Any]

RUN = HERE / ".deepeval" / ".latest_test_run.json"
VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")


def view_of(tc: JSON) -> dict[str, str]:
    turns: dict[int, list[JSON]] = {}
    for call, turn in zip(tc.get("toolsCalled") or [], tc["metadata"]["tool_turns"], strict=True):
        turns.setdefault(turn, []).append(call)
    lines: list[str] = []
    for calls in turns.values():
        lines += [f"call {c['name']}({json.dumps(c.get('inputParameters'))})" for c in calls]
        for c in calls:
            out = c.get("output")
            if out is not None:
                err = isinstance(out, dict) and "error" in out
                lines.append(f"  -> {'ERROR ' if err else ''}{json.dumps(out, ensure_ascii=False)}")
    return {
        "policy": SYSTEM_PROMPT,
        "email": tc["input"],
        "actions": "\n".join(lines) or "(no tool calls)",
        "happened": tc["context"][1].removeprefix("WHAT ACTUALLY CHANGED\n"),
        "answer": "",
        "reply": tc.get("actualOutput") or "",
    }


async def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else RUN
    cases: list[JSON] = json.loads(path.read_text(encoding="utf-8"))["testRunData"]["testCases"]
    client = AsyncAnthropic(
        base_url=f"{VESSEL}/b/anthropic/t/p9-deepeval-p4judge" if VESSEL else None,
        default_headers={"X-Vessel-Session": "p9-deepeval"} if VESSEL else None,
    )
    judge = Judge(HERE / ".deepeval" / "p4-judge-cache.jsonl", client)
    jm = JudgeModel("claude-haiku-5-5")
    graded = await asyncio.gather(*(rubric(judge, jm, V2, view_of(tc), answer=False) for tc in cases))
    agree, compared = 0, 0
    for tc, g in sorted(zip(cases, graded, strict=True), key=lambda x: x[0]["name"]):
        geval = row(tc)["rubric"]  # None: a G-Eval call errored (no verdict, as in p9_results.jsonl)
        if geval is not None and g.passed is not None:
            compared += 1
            agree += geval == g.passed
        fails = [c for c, a in g.asked.items() if a.verdict and not a.verdict["passed"]]
        shown = {True: "P", False: "F", None: "-"}
        mark = "" if geval is None or geval == g.passed else "   <-- differs"
        print(f"{tc['name']:<12} g-eval {shown[geval]}  p4 {shown[g.passed]} {fails or ''}{mark}")
        for c in fails:
            print(f"    p4 {c}: {g.asked[c].verdict['critique'] if g.asked[c].verdict else ''}")  # pyright: ignore[reportOptionalSubscript]
    rows = [row(tc)["rubric"] for tc in cases]
    print(f"g-eval {rows.count(True)}/{len(rows) - rows.count(None)} ({rows.count(None)} no verdict), "
          f"p4 {sum(1 for g in graded if g.passed)}/30, agree {agree}/{compared} (both gave a verdict)")  # fmt: skip


if __name__ == "__main__":
    asyncio.run(main())
