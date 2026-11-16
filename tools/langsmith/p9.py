"""P9: the eval slice as a LangSmith experiment (`aevaluate`), logged to a hosted workspace (EU). 10 cases x 3
repetitions, three evaluators (action, rubric, path).

    uv run python p9.py dataset                        # upload the slice as the `p9-slice` dataset (upsert)
    uv run python p9.py run NAME                       # one experiment over the dataset; prints its full name
    uv run python p9.py run NAME --limit 1 --trials 2  # smoke run
    uv run python p9.py export EXPERIMENT [--out FILE] # read the experiment back from LangSmith -> p9_results.jsonl
    uv run python p9.py compare EXPERIMENT BASE        # both experiments' stored stats, and per-example side by side
    uv run python p9.py queue EXPERIMENT               # an annotation queue holding two of its trials
    uv run python p9.py review EXPERIMENT [--key K]    # one human score via the SDK, and what the stats do with it

The agent and the judge both run here, on the Anthropic API (through Vessel when VESSEL_URL is set). LangSmith only
receives what the SDK traces: the dataset, the experiment's runs, the evaluators' runs and their feedback. The SUT's
model calls become `llm` child runs through `wrap_anthropic`; its tool calls run in supportdesk's own loop, so the
target replays them from the transcript as `tool` child runs, and the path evaluator reads those off the run tree.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from langchain_anthropic import ChatAnthropic
from langsmith import Client, aevaluate  # pyright: ignore[reportUnknownVariableType]
from langsmith import env as ls_env
from langsmith.run_helpers import get_current_run_tree, trace
from langsmith.wrappers import wrap_anthropic
from openevals.llm import create_async_llm_as_judge  # pyright: ignore[reportUnknownVariableType]

import supportdesk
import supportdesk.task
from evalkit.core import JSON, PRICES, Case, Trial, cost
from evalkit.judge import fence
from supportdesk.graders import action as ref_action
from supportdesk.judges import RUBRIC_SYSTEM_V0, V2, view
from supportdesk.path import looked_up_first
from supportdesk.shop import SYSTEM_PROMPT

HERE = Path(__file__).resolve().parent
SLICE = HERE.parent / "p9" / "slice.jsonl"
DATASET = "p9-slice"
QUEUE = "p9-review"
TAG = "p9-langsmith"  # Vessel tag for the judge; the SUT's Vessel session is TAG-<experiment prefix>
JUDGE = "claude-haiku-5-5"
K = 3
SIDE_EFFECTS = ("issue_refund", "escalate")
VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")
NS = uuid.UUID("6f1f6b0e-2a5e-4f0e-9b7a-9e0c1d2f3a4b")  # example id = uuid5(NS, case id): re-uploads upsert
RUN_ID = [TAG]  # set per experiment by run()

# The runner adds `ls_env.get_git_info()` to every experiment's metadata: remote URL, commit, branch, tags, dirty, and
# the last commit's author name and email. There is no setting for it, so keep only these three.
_git_info = ls_env.get_git_info


def _git_some(remote: str = "origin") -> Any:
    g = cast(dict[str, Any], _git_info(remote))
    return {k: g.get(k) for k in ("commit", "branch", "dirty")}


ls_env.get_git_info = _git_some


def ls() -> Any:
    """The LangSmith client, as Any: its methods take and return dicts without type parameters, which strict
    pyright reports on every call."""
    return Client()


def d(x: Any) -> JSON:
    return cast(JSON, x or {})


# --- case ---------------------------------------------------------------------------------------------------------


def upload_dataset() -> None:
    """The slice as a hosted dataset: inputs = the email, outputs = the expected dict, metadata = the rest."""
    client = ls()
    rows = [json.loads(line) for line in SLICE.read_text(encoding="utf-8").splitlines()]
    examples: list[dict[str, Any]] = [
        {
            "id": uuid.uuid5(NS, r["id"]),
            "inputs": {"email": r["input"]},
            "outputs": r["expected"],
            "metadata": {
                "case_id": r["id"],
                "faults": r.get("faults", {}),
                "suite": r["suite"],
                "case_tags": r["tags"],
            },
        }
        for r in rows
    ]
    if client.has_dataset(dataset_name=DATASET):
        ds = client.read_dataset(dataset_name=DATASET)
        client.update_examples(dataset_id=ds.id, updates=examples)
    else:
        ds = client.create_dataset(DATASET, description="P9: the frozen 10-case slice (tools/p9/slice.jsonl)")
        client.create_examples(dataset_id=ds.id, examples=examples)
    print(f"{DATASET}: {len(list(client.list_examples(dataset_id=ds.id)))} examples, id {ds.id}")


# --- the system under test ----------------------------------------------------------------------------------------

_sut_client = supportdesk.task._anthropic  # pyright: ignore[reportPrivateUsage]


@cache
def _traced_client(run_id: str, loop: asyncio.AbstractEventLoop) -> AsyncAnthropic:
    """supportdesk builds (and caches) its own AsyncAnthropic; wrap_anthropic patches that instance's
    messages.create, so each model call becomes an `llm` run under whatever run is current."""
    return wrap_anthropic(_sut_client(run_id, loop))


supportdesk.task._anthropic = _traced_client  # pyright: ignore[reportPrivateUsage]


def replay_tools(messages: list[JSON]) -> int:
    """Each tool call that ran, as a `tool` child run of the current run; returns how many."""
    n, turn = 0, -1
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        turn += 1
        results: dict[str, JSON] = {}  # tool ids are only unique within a turn
        for t in messages[i + 1 :]:
            if t["role"] != "tool":
                break
            results[t["id"]] = t
        for c in m["tool_calls"]:
            r = results.get(c["id"])
            if r is None:  # never executed (close_ticket ends the loop, and calls after it in that turn don't run)
                continue
            meta = {"turn": turn, "call_id": c["id"], "ms": r["ms"]}
            with trace(c["name"], run_type="tool", inputs=c["args"] or {}, metadata=meta) as span:
                rt: Any = span
                err = json.dumps(r["result"], ensure_ascii=False) if r["error"] else None
                rt.end(outputs={"result": r["result"]}, error=err)
            n += 1
    return n


REPS: Counter[tuple[str, str]] = Counter()  # (experiment, case) -> trials started; LangSmith has no repetition index


async def target(inputs: JSON, metadata: JSON) -> JSON:
    rt = get_current_run_tree()
    assert rt is not None
    case_id = metadata["case_id"]
    rep = REPS[RUN_ID[0], case_id]
    REPS[RUN_ID[0], case_id] += 1
    rt.metadata["rep"] = rep
    trial = await supportdesk.arun(inputs["email"], metadata["faults"], case_id=case_id, rep=rep, run_id=RUN_ID[0])
    item = {"messages": trial.messages, "input": inputs["email"], "outcome": trial.outcome, "output": trial.output}
    v = view(item, reply=trial.output.get("reply") or "", policy=SYSTEM_PROMPT)
    rt.metadata.update(
        tool_runs=replay_tools(trial.messages),  # path checks the run tree it is given against this
        status=trial.status,
        outcome=trial.outcome,
        actions=v["actions"],
        happened=v["happened"],
        sut_cost_usd=cost(trial.calls),
    )
    return trial.output  # the close_ticket call: action, order_id, amount, reply


# --- graders ------------------------------------------------------------------------------------------------------


def action(outputs: JSON, reference_outputs: JSON) -> JSON:
    ok = [reference_outputs["action"], *(a["action"] for a in reference_outputs.get("also_ok", []))]
    got = outputs.get("action")
    return {"key": "action", "score": got in ok, "comment": f"got {got}, accepted {ok}"}


# Rubric v2 as an openevals prompt: P4's system prompt per criterion as openevals' `system`, and P4's own user block
# (rubric_prompt with answer=False) as a str.format template, built with the same <data> fences around the variables.
USER = "\n\n".join(
    fence(name, f"{{{var}}}")
    for name, var in [
        ("shop_policy", "policy"),
        ("customer_email", "inputs"),
        ("agent_tool_calls_and_results", "actions"),
        ("what_actually_changed", "happened"),
        ("reply_sent_to_customer", "outputs"),
    ]
)


def judge_model() -> ChatAnthropic:
    return ChatAnthropic(
        model_name=JUDGE,
        max_tokens_to_sample=8000,  # P4's; without it langchain-anthropic sends the model profile's maximum
        base_url=f"{VESSEL}/b/anthropic/t/{TAG}" if VESSEL else None,
        default_headers={"X-Vessel-Session": TAG} if VESSEL else None,
        timeout=None,
        stop=None,
    )


JUDGES: dict[str, Callable[..., Any]] = {
    name: create_async_llm_as_judge(
        prompt=USER, system=RUBRIC_SYSTEM_V0.format(criterion=text), judge=judge_model(), feedback_key=name
    )
    for name, text in V2.criteria.items()
}


async def rubric(run: Any, inputs: JSON, outputs: JSON) -> JSON:
    """Rubric v2: one openevals judge per criterion; the reply passes only if all three pass. A criterion with no
    verdict (an exception) leaves `rubric` unscored (None), never failed."""
    reply = outputs.get("reply") or ""
    if not reply:
        return {"results": [{"key": "rubric", "score": None, "comment": "no reply"}]}
    meta = d(d(run.extra).get("metadata"))
    fields = {"policy": SYSTEM_PROMPT, "actions": meta["actions"], "happened": meta["happened"]}
    graded = await asyncio.gather(
        *(j(inputs=inputs["email"], outputs=reply, **fields) for j in JUDGES.values()), return_exceptions=True
    )
    results: list[JSON] = []
    for name, g in zip(JUDGES, graded, strict=True):
        if isinstance(g, BaseException):
            results.append({"key": name, "score": None, "comment": f"no verdict: {g!r}"})
        else:
            results.append({"key": name, "score": cast(JSON, g)["score"], "comment": cast(JSON, g)["comment"]})
    verdicts = [r["score"] for r in results]
    overall = None if any(v is None for v in verdicts) else all(verdicts)
    return {"results": [{"key": "rubric", "score": overall}, *results]}


def tool_runs(runs: list[Any]) -> list[Any]:
    """Replayed tool runs, in turn order (start_time breaks ties inside a turn)."""
    tools = [r for r in runs if r.run_type == "tool" and "turn" in (r.metadata or {})]
    return sorted(tools, key=lambda r: (r.metadata["turn"], r.start_time))


def lookups_before(tools: list[Any]) -> Callable[[str, int], bool]:
    seen: dict[str, int] = {}  # order id -> first turn with a successful get_order of it
    for r in tools:
        if r.name == "get_order" and not r.error:
            oid = str(d(d(r.outputs).get("result")).get("id", "")).strip().upper()
            seen.setdefault(oid, r.metadata["turn"])
    return lambda oid, turn: oid in seen and seen[oid] < turn


def path_verdict(tools: list[Any]) -> tuple[bool, str]:
    """looked_up_first over tool runs: no issue_refund/escalate before a successful get_order of that order in an
    earlier turn."""
    seen = lookups_before(tools)
    bad = [
        f"{r.name}({oid}) in turn {r.metadata['turn']}"
        for r in tools
        if r.name in SIDE_EFFECTS
        and not seen(oid := str(d(r.inputs).get("order_id", "")).strip().upper(), r.metadata["turn"])
    ]
    return not bad, "no successful get_order in an earlier turn: " + ", ".join(bad) if bad else ""


def path(run: Any) -> JSON:
    """Reads the tool runs off the run tree the runner hands the evaluator. Fails closed: if the tree doesn't hold
    as many tool runs as the target wrote, no verdict."""
    tools = tool_runs(list(run.child_runs or []))
    want = cast(int, run.metadata.get("tool_runs", -1))
    if len(tools) != want:
        return {"key": "path", "score": None, "comment": f"run tree has {len(tools)} tool runs, target wrote {want}"}
    ok, why = path_verdict(tools)
    return {"key": "path", "score": ok, "comment": why}


EVALUATORS: list[Any] = [action, rubric, path]  # aevaluate's declared evaluator type rejects these signatures


# --- run ----------------------------------------------------------------------------------------------------------


async def run(name: str, limit: int | None, trials: int) -> None:
    RUN_ID[0] = f"{TAG}-{name}"
    client = ls()
    data: Any = DATASET
    if limit:
        data = sorted(client.list_examples(dataset_name=DATASET), key=lambda e: d(e.metadata)["case_id"])[:limit]
    t0 = time.perf_counter()
    res = await aevaluate(
        target,
        data=data,
        evaluators=EVALUATORS,
        experiment_prefix=name,
        description=f"P9 slice, k={trials}, SUT supportdesk.arun (Haiku 5.5), judge {JUDGE} via openevals",
        metadata={"judge": JUDGE, "rubric": "v2", "k": trials, "port": "tools/langsmith/p9.py"},
        num_repetitions=trials,
        max_concurrency=10,
        client=client,
    )
    print(f"experiment {res.experiment_name}: {time.perf_counter() - t0:.1f}s")
    n: Counter[str] = Counter()
    async for row in res:
        for r in cast(Any, row)["evaluation_results"]["results"]:
            n[f"{r.key}={r.score}"] += 1
    print(dict(sorted(n.items())))


# --- read-back ----------------------------------------------------------------------------------------------------


def evaluator_source(f: Any) -> str | None:
    """The evaluator run behind a feedback row: the runner writes it as feedback_source.metadata.__run.run_id."""
    src = f.feedback_source
    if src is None or src.type != "model":
        return None
    return str(d(d(src.metadata).get("__run")).get("run_id") or "") or None


def as_bool(x: Any) -> bool | None:
    return None if x is None else bool(x)


def export(experiment: str, out: Path) -> None:
    """One line per trial from what LangSmith stored: root runs, their child runs, and the feedback on them. Scores
    come only from evaluator feedback; anything else on the same run (a human score) is reported, not exported."""
    client = ls()
    cases = {json.loads(line)["id"]: json.loads(line) for line in SLICE.read_text(encoding="utf-8").splitlines()}
    proj = client.read_project(project_name=experiment)
    runs = list(client.list_runs(project_id=proj.id))
    roots = [r for r in runs if r.parent_run_id is None]
    children: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for r in runs:
        if r.parent_run_id is not None:
            children[r.trace_id].append(r)
    fb: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for f in client.list_feedback(run_ids=[r.id for r in roots]):
        if f.run_id is not None:
            fb[f.run_id].append(f)
    results: list[JSON] = []
    disagree: list[str] = []
    other: list[str] = []
    same: Counter[str] = Counter()
    tokens: Counter[str] = Counter()
    judge_traces: set[str] = set()
    for root in roots:
        meta = d(root.metadata)
        case_id, rep = meta["ls_example_case_id"], meta["rep"]  # the runner copies example metadata as ls_example_*
        scores: dict[str, Any] = {}
        for f in fb[root.id]:
            if (ev := evaluator_source(f)) is None:
                other.append(f"{case_id}#{rep} {f.key}={f.score} ({f.feedback_source and f.feedback_source.type})")
                continue
            scores[f.key] = f.score
            if f.key == "rubric":
                judge_traces.add(ev)  # an evaluator run is the root of its own trace in `evaluators`
        output = d(root.outputs)
        row: JSON = {
            "tool": "langsmith",
            "case_id": case_id,
            "rep": rep,
            "action": as_bool(scores.get("action")),
            "path": as_bool(scores.get("path")),
            "rubric": as_bool(scores.get("rubric")),
            "reply": output.get("reply") or "",
        }
        results.append(row)
        kids = children[root.trace_id]
        for k in kids:
            if k.run_type == "llm":
                tokens["sut_in"] += k.prompt_tokens or 0
                tokens["sut_out"] += k.completion_tokens or 0
        # cross-check: the reference graders on a Trial rebuilt from the stored tool runs
        c = cases[case_id]
        case = Case(c["id"], c["input"], c["expected"], faults=c.get("faults", {}))
        trial = Trial(case_id, rep, JUDGE, transcript_from_tools(tool_runs(kids), output), output, meta["outcome"])
        ref_path = looked_up_first(case, trial)
        ref = {"action": ref_action(case, trial).passed, "path": ref_path.passed if ref_path else None}
        disagree += [f"{case_id}#{rep} {g}" for g, v in ref.items() if v != row[g]]
        item = {"messages": trial.messages, "input": c["input"], "outcome": meta["outcome"], "output": output}
        rebuilt = view(item, policy=SYSTEM_PROMPT)["actions"]
        same[
            "exact" if rebuilt == meta["actions"] else "same JSON" if same_calls(rebuilt, meta["actions"]) else "differ"
        ] += 1
    results.sort(key=lambda x: (x["case_id"], x["rep"]))
    dupes = [p for p, n in Counter((x["case_id"], x["rep"]) for x in results).items() if n > 1]
    assert not dupes, f"duplicate (case_id, rep): {dupes}"
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in results), encoding="utf-8")
    for r in client.list_runs(project_name="evaluators", run_type="llm", start_time=proj.start_time):
        if str(r.trace_id) in judge_traces:
            tokens["judge_in"] += r.prompt_tokens or 0
            tokens["judge_out"] += r.completion_tokens or 0
            tokens["judge_calls"] += 1
    pin, pout = PRICES[JUDGE]  # the SUT is Haiku 5.5 too
    usd = {w: (tokens[f"{w}_in"] * pin + tokens[f"{w}_out"] * pout) / 1e6 for w in ("sut", "judge")}
    print(f"wrote {out.name}: {len(results)} trials from {len(roots)} root runs, {len(runs) - len(roots)} child runs")
    print(f"  cost from stored token counts: SUT ${usd['sut']:.4f}, judge ${usd['judge']:.4f}; {dict(tokens)}")
    for g in ("action", "path", "rubric"):
        vals = [x[g] for x in results if x[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)} pass ({len(results) - len(vals)} unscored)")
    print(f"  cross-check vs supportdesk graders: {'all agree' if not disagree else 'DISAGREE ' + ', '.join(disagree)}")
    print(f"  judge's tool-call text rebuilt from the tool runs vs the target's: {dict(same)}")
    if other:
        print(f"  feedback not from the evaluators (not exported): {other}")


def same_calls(a: str, b: str) -> bool:
    """view()'s tool-call text, compared as JSON: the stored runs come back with their keys sorted."""

    def parse(text: str) -> list[Any]:
        out: list[Any] = []
        for line in text.splitlines():
            if line.startswith("call "):
                head, _, body = line.partition("(")
                out.append((head, json.loads(body.removesuffix(")"))))
            else:  # "  -> [ERROR ]{...}"
                body = line.removeprefix("  -> ")
                out.append((body.startswith("ERROR "), json.loads(body.removeprefix("ERROR "))))
        return out

    return parse(a) == parse(b)


def transcript_from_tools(tools: list[Any], output: JSON) -> list[JSON]:
    """supportdesk's transcript shape from the stored tool runs: one assistant turn per `turn`, then the close."""
    msgs: list[JSON] = []
    by_turn: dict[int, list[Any]] = defaultdict(list)
    for r in tools:
        by_turn[r.metadata["turn"]].append(r)
    for turn in sorted(by_turn):
        rs = by_turn[turn]
        calls = [{"id": r.metadata["call_id"], "name": r.name, "args": r.inputs} for r in rs]
        msgs.append({"role": "assistant", "text": "", "tool_calls": calls})
        msgs += [
            {"role": "tool", "id": r.metadata["call_id"], "name": r.name, "result": d(r.outputs).get("result"),
             "error": bool(r.error), "ms": r.metadata.get("ms", 0)}
            for r in rs
        ]  # fmt: skip
    close = {"id": "close", "name": "close_ticket", "args": output}
    msgs.append({"role": "assistant", "text": "", "tool_calls": [close]})
    return msgs


def compare(experiment: str, base: str) -> None:
    """What LangSmith stores per experiment (feedback_stats, run stats), and the per-example runs of both, side by
    side, from the endpoint the comparison view reads."""
    client = ls()
    projs = [client.read_project(project_name=n, include_stats=True) for n in (experiment, base)]
    for p in projs:
        keep = ("run_count", "latency_p50", "latency_p99", "total_tokens", "total_cost", "error_rate")
        print(p.name, {k: getattr(p, k) for k in keep})
        print("  feedback_stats", json.dumps(p.feedback_stats, default=str))
    ds = projs[0].reference_dataset_id
    body = {"session_ids": [str(p.id) for p in projs], "offset": 0, "limit": 100, "preview": True}
    resp = client.request_with_retries("POST", f"/datasets/{ds}/runs", request_kwargs={"data": json.dumps(body)})
    for ex in cast(list[JSON], resp.json()):
        by: dict[str, list[str]] = defaultdict(list)
        for r in cast(list[JSON], ex["runs"]):
            sess = "A" if r["session_id"] == str(projs[0].id) else "B"
            fbs = d(r.get("feedback_stats"))
            by[sess].append("".join(k[0] for k, v in sorted(fbs.items()) if d(v).get("avg") == 1))
        print(ex["metadata"]["case_id"], dict(by))


def queue(experiment: str) -> None:
    """An annotation queue (created if missing) holding the experiment's first two trials, for review in the UI."""
    client = ls()
    proj = client.read_project(project_name=experiment)
    roots: list[Any] = list(client.list_runs(project_id=proj.id, is_root=True))
    roots.sort(key=lambda r: (r.metadata["ls_example_case_id"], r.metadata["rep"]))
    queues = list(client.list_annotation_queues(name=QUEUE))
    q = queues[0] if queues else client.create_annotation_queue(name=QUEUE, description="P9: trials to review")
    client.add_runs_to_annotation_queue(q.id, run_ids=[r.id for r in roots[:2]])
    picked = [f"{r.metadata['ls_example_case_id']}#{r.metadata['rep']}" for r in roots[:2]]
    print(f"queue {q.name} ({q.id}): added {picked}")
    for r in client.list_runs_from_annotation_queue(q.id):
        print(" ", r.id, r.name)


def review(experiment: str, key: str) -> None:
    """One human score (0) on `key` for a trial the evaluator passed, through the SDK; then what the experiment's
    stored stats do with it, next to the evaluator's own score."""
    client = ls()
    before = d(client.read_project(project_name=experiment, include_stats=True).feedback_stats)
    proj = client.read_project(project_name=experiment)
    roots: dict[Any, Any] = {r.id: r for r in client.list_runs(project_id=proj.id, is_root=True)}
    passed: list[Any] = [
        f.run_id for f in client.list_feedback(run_ids=list(roots), feedback_key=[key]) if f.score == 1
    ]
    run = roots[sorted(passed, key=str)[0]]  # one the evaluator passed, so the human disagrees with it
    client.create_feedback(
        run.id,
        key=key,
        score=0,
        comment="human review: P9 test of what a human score does to the experiment's numbers",
        trace_id=run.trace_id,
    )
    client.flush()
    print(f"scored {key}=0 on run {run.id} ({run.inputs})")
    print("before", json.dumps(before.get(key), default=str))
    for wait in (2, 5, 10, 20, 40):
        time.sleep(wait)
        after = d(client.read_project(project_name=experiment, include_stats=True).feedback_stats).get(key)
        print(f"after +{wait}s", json.dumps(after, default=str))
        if after != before.get(key):
            break
    for f in client.list_feedback(run_ids=[run.id], feedback_key=[key]):
        src = f.feedback_source
        print(f"  feedback {f.key}={f.score} source={src and src.type} evaluator_run={evaluator_source(f)}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dataset")
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--limit", type=int)
    r.add_argument("--trials", type=int, default=K)
    e = sub.add_parser("export")
    e.add_argument("experiment")
    e.add_argument("--out", type=Path, default=HERE / "p9_results.jsonl")
    c = sub.add_parser("compare")
    c.add_argument("experiment")
    c.add_argument("base")
    q = sub.add_parser("queue")
    q.add_argument("experiment")
    v = sub.add_parser("review")
    v.add_argument("experiment")
    v.add_argument("--key", default="rubric")
    a = p.parse_args()
    if a.cmd == "dataset":
        upload_dataset()
    elif a.cmd == "run":
        asyncio.run(run(a.name, a.limit, a.trials))
    elif a.cmd == "export":
        export(a.experiment, a.out)
    elif a.cmd == "compare":
        compare(a.experiment, a.base)
    elif a.cmd == "queue":
        queue(a.experiment)
    else:
        review(a.experiment, a.key)


if __name__ == "__main__":
    main()
