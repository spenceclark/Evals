"""Analysis, not part of the port: P4's own rubric judge (evalkit, structured output) on the same 30 replies the Inspect
run judged, so a gap in the rubric count can be put down to the judge's form rather than to different trials.

    uv run python judge_crosscheck.py [LOG]   # default: the newest log in logs/
"""

import asyncio
import sys
from typing import Any

from anthropic import AsyncAnthropic
from inspect_ai.log import list_eval_logs, read_eval_log
from p9 import LOGS, score_value

from evalkit.judge import Judge, JudgeModel
from supportdesk.judges import V2, rubric, view
from supportdesk.shop import SYSTEM_PROMPT


async def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else max(list_eval_logs(str(LOGS)), key=lambda i: i.mtime or 0).name
    log = read_eval_log(path)
    samples = sorted(log.samples or [], key=lambda s: (str(s.id), s.epoch))
    judge = Judge(LOGS / "p4-judge-cache.jsonl", AsyncAnthropic())
    jm = JudgeModel("claude-haiku-5-5")

    async def p4(item: dict[str, Any]) -> bool | None:
        v = view(item, policy=SYSTEM_PROMPT)
        return (await rubric(judge, jm, V2, v, answer=False)).passed

    items = [{**s.store["trial"], "input": str(s.input)} for s in samples]
    p4_verdicts = await asyncio.gather(*(p4(t) for t in items))
    disagree = 0
    for s, p4v in zip(samples, p4_verdicts, strict=True):
        inspect_v = score_value((s.scores or {}).get("rubric"))
        if inspect_v != p4v:
            disagree += 1
            print(f"  {s.id}#{s.epoch - 1}: inspect {inspect_v}, p4 {p4v}")
    n = sum(1 for v in p4_verdicts if v)
    print(f"P4 judge on the Inspect replies: {n}/{len(samples)} pass; {disagree} disagreement(s) with model_graded_qa")


if __name__ == "__main__":
    asyncio.run(main())
