"""Analysis, not part of the port. Two checks on the autoevals judge, from what Braintrust stored for one experiment:

1. P4's own rubric judge (evalkit, structured output) on the same 30 replies, with the transcript rebuilt from the
   stored `llm` spans, so a gap against the reference can be put down to the judge's form or to the replies.
2. The port's three LLMClassifiers on the reference's two failing replies (dig-01#2, win-01#2), judged against a
   stored trial of the same case (the tool calls are the same lookup), to show this judge can fail a reply at all.

    uv run python judge_crosscheck.py [EXPERIMENT]   # default: p9-run-1
"""

import asyncio
import json
import os
import sys
from typing import Any

from anthropic import AsyncAnthropic
from p9 import CLASSIFIERS, HERE, d, fetch, kind, transcript_from_llm

from evalkit.judge import Judge, JudgeModel
from supportdesk.judges import V2, rubric, view
from supportdesk.shop import SYSTEM_PROMPT

VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")


async def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "p9-run-1"
    traces: dict[str, list[dict[str, Any]]] = {}
    for r in fetch(name):
        traces.setdefault(r["root_span_id"], []).append(r)
    stored: list[tuple[str, int, bool | None, dict[str, Any]]] = []
    for spans in traces.values():
        [root] = [s for s in spans if kind(s) == "eval"]
        meta = d(root["metadata"])
        sut = [s for s in spans if kind(s) == "llm" and d(s["span_attributes"]).get("purpose") != "scorer"]
        scores = {k: v for s in spans if kind(s) == "score" for k, v in d(s.get("scores")).items()}
        item = {"messages": transcript_from_llm(sut), "input": root["input"], "outcome": meta["outcome"]}
        item["output"] = d(root.get("output"))
        rub = scores.get("rubric")
        stored.append((meta["case_id"], meta["rep"], None if rub is None else rub == 1, item))
    stored.sort(key=lambda x: (x[0], x[1]))

    base = f"{VESSEL}/b/anthropic/t/p9-braintrust-p4" if VESSEL else None
    judge = Judge(HERE / "logs" / "p4-judge-cache.jsonl", AsyncAnthropic(base_url=base))
    jm = JudgeModel("claude-haiku-5-5")
    p4 = await asyncio.gather(
        *(rubric(judge, jm, V2, view(it, policy=SYSTEM_PROMPT), answer=False) for _, _, _, it in stored)
    )
    off = [
        f"{c}#{r}: braintrust {bt}, p4 {g.passed}"
        for (c, r, bt, _), g in zip(stored, p4, strict=True)
        if bt != g.passed
    ]
    print(f"P4 judge on {name}'s replies: {sum(bool(g.passed) for g in p4)}/{len(p4)} pass; {len(off)} disagreement(s)")
    for line in off:
        print("  " + line)

    ref = {(r["case_id"], r["rep"]): r for r in map(json.loads, (HERE.parent / "p9" / "reference.jsonl").open())}
    for case_id in ("dig-01", "win-01"):
        item = next(it for c, _, _, it in stored if c == case_id)
        v = view(item, reply=ref[(case_id, 2)]["reply"], policy=SYSTEM_PROMPT)
        fields = {"input": v["email"], "actions": v["actions"], "happened": v["happened"]}
        got = await asyncio.gather(*(c.eval_async(output=v["reply"], **fields) for c in CLASSIFIERS.values()))
        verdicts = {n: g.metadata.get("choice") for n, g in zip(CLASSIFIERS, got, strict=True)}
        print(f"reference {case_id}#2 (failed by the reference's judge) through the port's classifiers: {verdicts}")


if __name__ == "__main__":
    asyncio.run(main())
