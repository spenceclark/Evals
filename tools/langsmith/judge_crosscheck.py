"""Analysis, not part of the port. Three checks on the openevals judge, from what LangSmith stored for one experiment:

1. Parity: P4's user block (`rubric_prompt(view(...), answer=False)`) and system prompt against the prompts LangSmith
   stored for the judge's `llm` runs (in the `evaluators` project).
2. P4's own rubric judge (evalkit, structured output) on the same 30 replies, with the transcript rebuilt from the
   stored tool runs, so a gap against the reference can be put down to the judge's form or to the replies.
3. The port's three judges on the reference's two failing replies (dig-01#2, win-01#2), judged against a stored trial
   of the same case (the tool calls are the same lookup).

    uv run python judge_crosscheck.py EXPERIMENT
"""

import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, cast

from anthropic import AsyncAnthropic
from p9 import HERE, JUDGES, d, evaluator_source, ls, tool_runs, transcript_from_tools

from evalkit.core import JSON
from evalkit.judge import Judge, JudgeModel
from supportdesk.judges import RUBRIC_SYSTEM_V0, V2, rubric, rubric_prompt, view
from supportdesk.shop import SYSTEM_PROMPT

VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")


def stored_items(client: Any, experiment: str) -> list[tuple[str, int, Any, dict[str, Any], dict[str, Any]]]:
    """(case, rep, root run, judge-view item, evaluator feedback by key) for every trial, from what LangSmith holds."""
    proj = client.read_project(project_name=experiment)
    runs = list(client.list_runs(project_id=proj.id))
    kids: dict[Any, list[Any]] = defaultdict(list)
    for r in runs:
        if r.parent_run_id is not None:
            kids[r.trace_id].append(r)
    roots = [r for r in runs if r.parent_run_id is None]
    fb: dict[Any, dict[str, Any]] = defaultdict(dict)
    for f in client.list_feedback(run_ids=[r.id for r in roots]):
        if evaluator_source(f):
            fb[f.run_id][f.key] = f
    out: list[tuple[str, int, Any, dict[str, Any], dict[str, Any]]] = []
    for root in roots:
        m = root.metadata
        output = d(root.outputs)
        msgs = transcript_from_tools(tool_runs(kids[root.trace_id]), output)
        item = {"messages": msgs, "input": d(root.inputs)["email"], "outcome": m["outcome"], "output": output}
        out.append((m["ls_example_case_id"], m["rep"], root, item, fb[root.id]))
    return sorted(out, key=lambda x: (x[0], x[1]))


def parity(client: Any, experiment: str, stored: list[tuple[str, int, Any, dict[str, Any], dict[str, Any]]]) -> None:
    """Each trial's three judge prompts as LangSmith stored them, against P4's system and user block for that trial
    (built from the target's own `actions` text, which is what the judge was given)."""
    proj = client.read_project(project_name=experiment)
    traces = {evaluator_source(fbs["rubric"]): (root, item) for _, _, root, item, fbs in stored}
    systems = {RUBRIC_SYSTEM_V0.format(criterion=t) for t in V2.criteria.values()}
    seen: Counter[str] = Counter()
    for r in client.list_runs(project_name="evaluators", run_type="llm", start_time=proj.start_time):
        if str(r.trace_id) not in traces:
            continue
        root, item = traces[str(r.trace_id)]
        m = root.metadata
        v = view(item, policy=SYSTEM_PROMPT) | {"actions": m["actions"], "happened": m["happened"]}
        [msgs] = cast(list[list[JSON]], d(r.inputs)["messages"])  # LangChain-serialized: one batch of messages
        sys_msg, user = d(msgs[0]["kwargs"])["content"], d(msgs[1]["kwargs"])["content"]
        seen["system ok" if sys_msg in systems else "system differs"] += 1
        seen["user == rubric_prompt" if user == rubric_prompt(v, answer=False) else "user differs"] += 1
    print(f"parity of the {sum(seen.values()) // 2} stored judge prompts: {dict(seen)}")


async def main() -> None:
    experiment = sys.argv[1]
    client = ls()
    stored = stored_items(client, experiment)
    parity(client, experiment, stored)

    base = f"{VESSEL}/b/anthropic/t/p9-langsmith-p4" if VESSEL else None
    judge = Judge(HERE / "logs" / "p4-judge-cache.jsonl", AsyncAnthropic(base_url=base))
    jm = JudgeModel("claude-haiku-5-5")
    p4 = await asyncio.gather(
        *(rubric(judge, jm, V2, view(it, policy=SYSTEM_PROMPT), answer=False) for *_, it, _ in stored)
    )
    off: list[str] = []
    for (c, r, _, _, fbs), g in zip(stored, p4, strict=True):
        got = None if fbs["rubric"].score is None else fbs["rubric"].score == 1
        p4_fails = [k for k, a in g.asked.items() if a.verdict and not a.verdict["passed"]]
        if got != g.passed:
            off.append(f"{c}#{r}: langsmith {got}, p4 {g.passed} {p4_fails}")
        elif not got:
            off.append(f"{c}#{r}: both fail; p4 fails {p4_fails}")
    print(f"P4 judge on {experiment}'s replies: {sum(bool(g.passed) for g in p4)}/{len(p4)} pass")
    for line in off:
        print("  " + line)

    ref = {(r["case_id"], r["rep"]): r for r in map(json.loads, (HERE.parent / "p9" / "reference.jsonl").open())}
    for case_id in ("dig-01", "win-01"):
        item = next(it for c, *_, it, _ in stored if c == case_id)
        v = view(item, reply=ref[(case_id, 2)]["reply"], policy=SYSTEM_PROMPT)
        fields = {"policy": SYSTEM_PROMPT, "actions": v["actions"], "happened": v["happened"]}
        got = await asyncio.gather(*(j(inputs=v["email"], outputs=v["reply"], **fields) for j in JUDGES.values()))
        verdicts = {n: g["score"] for n, g in zip(JUDGES, got, strict=True)}
        print(f"reference {case_id}#2 (failed by the reference's judge) through the port's judges: {verdicts}")


if __name__ == "__main__":
    asyncio.run(main())
