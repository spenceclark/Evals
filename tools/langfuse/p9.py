"""P9: the eval slice as a Langfuse experiment (`run_experiment` over a hosted dataset), on Langfuse Cloud (EU).
10 cases x 3 trials, three evaluators (action, rubric, path), each verdict stored as a Langfuse score.

    uv run python p9.py dataset                         # upload the slice as the `p9-slice` dataset (upsert by id)
    uv run python p9.py run NAME                        # one experiment (dataset run) named NAME, k=3
    uv run python p9.py run NAME --limit 1 --trials 2   # smoke run
    uv run python p9.py export NAME [--out FILE]        # read the run back from Langfuse -> p9_results.jsonl
    uv run python p9.py compare NAME BASE               # both runs' stored scores, per item side by side
    uv run python p9.py queue NAME                      # an annotation queue holding two of its trials
    uv run python p9.py review NAME [--key K]           # one human score through the SDK, and what it does

The agent and the judge both run here, on the Anthropic API (through Vessel when VESSEL_URL is set). Langfuse gets
OpenTelemetry spans: the experiment's own (run, task, evaluators), the Anthropic SDK's own span per model call (the
SDK emits them whenever a tracer provider is registered, and Langfuse registers one), and the tool calls, replayed
from the transcript. Langfuse's server-side LLM-as-a-judge is not used: it needs a model key stored in Langfuse.
"""

import argparse
import datetime as dt
import json
import os
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

# The Anthropic SDK's spans record request and response content only when asked. Read once, at client construction.
os.environ.setdefault("ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE", "content")

from anthropic import AsyncAnthropic
from langfuse import Evaluation, Langfuse
from langfuse.api import AnnotationQueueObjectType

import supportdesk
from evalkit.core import JSON, PRICES, Case, Trial, cost
from evalkit.judge import Judge, JudgeModel
from supportdesk.graders import action as ref_action
from supportdesk.judges import V2, rubric, view
from supportdesk.path import looked_up_first
from supportdesk.shop import SYSTEM_PROMPT

HERE = Path(__file__).resolve().parent
SLICE = HERE.parent / "p9" / "slice.jsonl"
DATASET = "p9-slice"
QUEUE = "p9-review"
TAG = "p9-langfuse"  # Vessel tag and session for the judge; the SUT's Vessel session is TAG-<run name>
JUDGE = JudgeModel("claude-haiku-5-5")  # P4's settings: effort medium, max_tokens 8000, structured Verdict
K = 3
VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")
NS = uuid.UUID("0b6c1f4e-3d2a-4c5b-8e7f-1a2b3c4d5e6f")  # dataset item id = uuid5(NS, case id): re-uploads upsert
GRADER = {"grader": "p9"}  # on every evaluator score, so the export can tell them from human scores
EPOCH = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)  # the API's list endpoints need a lower time bound

lf = Langfuse()


def rows() -> dict[str, JSON]:
    return {(r := json.loads(line))["id"]: r for line in SLICE.read_text(encoding="utf-8").splitlines()}


# --- case ---------------------------------------------------------------------------------------------------------


def upload_dataset() -> None:
    """The slice as a hosted dataset: input = the email, expected_output = the expected dict, metadata = the rest."""
    lf.create_dataset(name=DATASET, description="P9: the frozen 10-case slice (tools/p9/slice.jsonl)")
    for r in rows().values():
        lf.create_dataset_item(
            dataset_name=DATASET,
            id=str(uuid.uuid5(NS, r["id"])),
            input={"email": r["input"]},
            expected_output=r["expected"],
            metadata={"case_id": r["id"], "faults": r.get("faults", {}), "suite": r["suite"], "case_tags": r["tags"]},
        )
    ds = lf.get_dataset(DATASET)
    print(f"{DATASET}: {len(ds.items)} items, id {ds.id}")


# --- the system under test ----------------------------------------------------------------------------------------


def replay_tools(messages: list[JSON]) -> int:
    """Each tool call that ran, as a `tool` observation under the current span; returns how many. supportdesk runs
    its tools in its own loop, so nothing records them unless we do."""
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
            with lf.start_as_current_observation(name=c["name"], as_type="tool", input=c["args"], metadata=meta) as o:
                o.update(output=r["result"], level="ERROR" if r["error"] else "DEFAULT")
            n += 1
    return n


RUN = [TAG]  # the run name, set by run()
REPS: Counter[str] = Counter()  # case id -> trials started; Langfuse passes no repetition index


async def task(*, item: Any, **kwargs: Any) -> JSON:
    case_id = item.metadata["case_id"]
    rep = REPS[case_id]
    REPS[case_id] += 1
    trial = await supportdesk.arun(item.input["email"], item.metadata["faults"], case_id=case_id, rep=rep,
                                   run_id=f"{TAG}-{RUN[0]}")  # fmt: skip
    lf.update_current_span(
        metadata={
            "rep": rep,
            "status": trial.status,
            "tool_observations": replay_tools(trial.messages),
            "sut_cost_usd": cost(trial.calls),
        }
    )
    # Evaluators get only input, output, expected_output and metadata (the dataset item's), so whatever a grader
    # needs from the trial has to be in the output: the close_ticket call, plus the transcript and the store outcome.
    return {**trial.output, "messages": trial.messages, "outcome": trial.outcome}


def as_trial(output: JSON, case_id: str = "", rep: int = 0) -> Trial:
    close = {k: output.get(k) for k in ("action", "order_id", "amount", "reply")}
    return Trial(case_id, rep, JUDGE.model, output["messages"], close, output["outcome"])


def as_case(input: JSON, expected_output: JSON, metadata: JSON) -> Case:
    return Case(metadata["case_id"], input["email"], expected_output, faults=metadata["faults"])


# --- graders ------------------------------------------------------------------------------------------------------


def action(*, input: JSON, output: JSON, expected_output: JSON, metadata: JSON, **kwargs: Any) -> Evaluation:
    ok = [expected_output["action"], *(a["action"] for a in expected_output.get("also_ok", []))]
    got = output.get("action")
    return Evaluation(name="action", value=got in ok, data_type="BOOLEAN", comment=f"got {got}, accepted {ok}",
                      metadata=GRADER)  # fmt: skip


def path(*, input: JSON, output: JSON, expected_output: JSON, metadata: JSON, **kwargs: Any) -> list[Evaluation]:
    """The reference looked_up_first, on the transcript the task put in its output."""
    s = looked_up_first(as_case(input, expected_output, metadata), as_trial(output))
    if s is None:
        return []
    return [Evaluation(name="path", value=s.passed, data_type="BOOLEAN", comment=s.reason or None, metadata=GRADER)]


JUDGES: dict[str, Judge] = {}  # one per run, with its own cache file: a second run must not reuse the first's verdicts


def judge() -> Judge:
    if RUN[0] not in JUDGES:
        client = AsyncAnthropic(
            base_url=f"{VESSEL}/b/anthropic/t/{TAG}" if VESSEL else None,
            default_headers={"X-Vessel-Session": TAG} if VESSEL else None,
        )
        cache = HERE / "logs" / f"judge-{RUN[0]}.jsonl"
        cache.parent.mkdir(exist_ok=True)
        JUDGES[RUN[0]] = Judge(cache, client)
    return JUDGES[RUN[0]]


async def rubric_v2(*, input: JSON, output: JSON, **kwargs: Any) -> list[Evaluation]:
    """Rubric v2 with P4's own judge (supportdesk.judges.rubric): one Haiku 5.5 call per criterion; the reply passes
    only if all three pass. A criterion with no verdict leaves `rubric` unscored, never failed."""
    if not output.get("reply"):
        return []
    item = {"messages": output["messages"], "input": input["email"], "outcome": output["outcome"], "output": output}
    graded = await rubric(judge(), JUDGE, V2, view(item, policy=SYSTEM_PROMPT), answer=False)
    evals = [
        Evaluation(
            name=name, value=a.verdict["passed"], data_type="BOOLEAN", comment=a.verdict["critique"], metadata=GRADER
        )
        if a.verdict
        else Evaluation(
            name=f"{name}_error", value=a.status, data_type="CATEGORICAL", comment=a.text[:500], metadata=GRADER
        )
        for name, a in graded.asked.items()
    ]
    if graded.passed is not None:
        evals.insert(0, Evaluation(name="rubric", value=graded.passed, data_type="BOOLEAN", metadata=GRADER))
    return evals


# run_experiment's EvaluatorFunction declares `metadata: Dict | None`, which strict pyright won't match to `JSON`
EVALUATORS: list[Any] = [action, rubric_v2, path]

# --- run ----------------------------------------------------------------------------------------------------------


def run(name: str, limit: int | None, trials: int) -> None:
    RUN[0] = name
    since = dt.datetime.now(dt.UTC)
    if lf.api.experiments.list(from_start_time=EPOCH, name=name).data:
        raise SystemExit(f"a run named {name!r} exists: Langfuse would add these trials to it")
    items = sorted(lf.get_dataset(DATASET).items, key=lambda i: i.metadata["case_id"])[:limit]
    t0 = time.perf_counter()
    res = lf.run_experiment(
        name="p9",
        run_name=name,  # same run name on the same dataset = the same experiment (id derived from it)
        description=f"P9 slice, k={trials}, SUT supportdesk.arun (Haiku 5.5), judge {JUDGE.model} (P4's, local)",
        data=items * trials,  # no repetitions parameter: the same item, `trials` times
        task=task,
        evaluators=EVALUATORS,
        max_concurrency=10,
        metadata={"judge": JUDGE.model, "rubric": "v2", "k": str(trials), "port": "tools/langfuse/p9.py"},
    )
    print(f"run {name}: experiment {res.experiment_id}, {len(res.item_results)} trials, "
          f"{time.perf_counter() - t0:.1f}s, since {since.isoformat()}")  # fmt: skip
    print(f"  {res.experiment_url}")
    n = Counter(f"{e.name}={e.value}" for r in res.item_results for e in r.evaluations)
    print(dict(sorted(n.items())))


# --- read-back ----------------------------------------------------------------------------------------------------


def experiment(name: str) -> Any:
    found = lf.api.experiments.list(from_start_time=EPOCH, name=name, fields="core,metadata,scores").data
    assert len(found) == 1, f"{len(found)} experiments named {name!r}"
    return found[0]


def experiment_items(exp: Any) -> list[Any]:
    out: list[Any] = []
    cursor = None
    while True:
        page = lf.api.experiments.list_items(
            from_start_time=exp.start_time - dt.timedelta(minutes=1), experiment_id=exp.id, limit=100, cursor=cursor,
            fields="core,dataset,io,metadata,itemMetadata,scores", score_limit=50,
        )  # fmt: skip
        out += page.data
        cursor = page.meta.cursor
        if not cursor:
            return out


def observations(trace_id: str, since: dt.datetime) -> list[Any]:
    out: list[Any] = []
    cursor = None
    while True:
        page = lf.api.observations.get_many(
            trace_id=trace_id, from_start_time=since, fields="core,basic,io,metadata,model,usage", limit=1000,
            cursor=cursor,
        )  # fmt: skip
        out += page.data
        cursor = page.meta.cursor
        if not cursor:
            return out


def scores_of(it: Any) -> list[Any]:
    """An experiment item's own `scores` field (core fields only: no metadata, comment or subject)."""
    return list(it.scores or [])


def stored_scores(trace_id: str, name: str | None = None) -> list[Any]:
    """A trace's scores with metadata, comment and subject (the observation or trace scored)."""
    return list(lf.api.scores.get_many(trace_id=trace_id, name=name, fields="details,subject", limit=100).data)


def subject(s: Any) -> tuple[str, str]:
    return (s.subject.kind, s.subject.id) if s.subject else ("?", "?")


def loads(x: Any) -> Any:
    """The v2 observations endpoint returns input/output as raw strings."""
    if isinstance(x, str):
        try:
            return json.loads(x)
        except json.JSONDecodeError:
            return x
    return x


def as_bool(x: Any) -> bool | None:
    return None if x is None else bool(x)


def export(name: str, out: Path) -> None:
    """One line per trial from what Langfuse stored: the experiment's items (input, output, metadata, scores), and
    each trace's observations for the cross-checks and the cost. Scores come only from the evaluators (metadata
    GRADER); anything else on the same trace (a human score) is reported, not exported."""
    exp = experiment(name)
    cases = rows()
    items = experiment_items(exp)
    results: list[JSON] = []
    disagree: list[str] = []
    other: list[str] = []
    tokens: Counter[str] = Counter()
    for it in items:
        meta = cast(JSON, it.metadata or {})  # values come back as strings
        case_id, rep = cast(JSON, it.experiment_item_metadata)["case_id"], int(meta["rep"])
        scores: dict[str, Any] = {}
        # the item's own `scores` field has no metadata or comment; the scores endpoint has them with `details`
        for s in stored_scores(it.trace_id):
            if cast(JSON, s.metadata or {}).get("grader") == GRADER["grader"] and subject(s)[1] == it.id:
                assert s.name not in scores, f"{case_id}#{rep}: two evaluator scores named {s.name}"
                scores[s.name] = s.value
            else:
                other.append(f"{case_id}#{rep} {s.name}={s.value} ({s.source}, on {subject(s)[0]})")
        output = cast(JSON, loads(it.output))
        row: JSON = {
            "tool": "langfuse",
            "case_id": case_id,
            "rep": rep,
            "action": as_bool(scores.get("action")),
            "path": as_bool(scores.get("path")),
            "rubric": as_bool(scores.get("rubric")),
            "reply": output.get("reply") or "",
        }
        results.append(row)
        # cross-check: the reference graders on a transcript rebuilt from the stored tool observations
        obs = observations(it.trace_id, exp.start_time - dt.timedelta(minutes=1))
        names = {o.id: o.name for o in obs}
        for o in obs:
            if o.type == "GENERATION":
                who = "judge" if names.get(o.parent_observation_id) == "rubric_v2" else "sut"
                usage = cast(JSON, o.usage_details or {})
                tokens[f"{who}_in"] += usage.get("input", 0)
                tokens[f"{who}_out"] += usage.get("output", 0)
                tokens[f"{who}_calls"] += 1
        tools = sorted((o for o in obs if o.type == "TOOL"), key=lambda o: (turn_of(o), o.start_time))
        c = cases[case_id]
        case = Case(c["id"], c["input"], c["expected"], faults=c.get("faults", {}))
        close = {k: output.get(k) for k in ("action", "order_id", "amount", "reply")}
        trial = Trial(case_id, rep, JUDGE.model, transcript_from_tools(tools, close), close, output["outcome"])
        ref_path = looked_up_first(case, trial)
        ref = {"action": ref_action(case, trial).passed, "path": ref_path.passed if ref_path else None}
        disagree += [f"{case_id}#{rep} {g}" for g, v in ref.items() if v != row[g]]
        if len(tools) != int(meta["tool_observations"]):
            disagree.append(
                f"{case_id}#{rep}: {len(tools)} tool observations stored, task wrote {meta['tool_observations']}"
            )
    results.sort(key=lambda x: (x["case_id"], x["rep"]))
    pairs = Counter((x["case_id"], x["rep"]) for x in results)
    assert all(n == 1 for n in pairs.values()), f"duplicate (case_id, rep): {[p for p, n in pairs.items() if n > 1]}"
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in results), encoding="utf-8")
    pin, pout = PRICES[JUDGE.model]  # the SUT is Haiku 5.5 too
    usd = {w: (tokens[f"{w}_in"] * pin + tokens[f"{w}_out"] * pout) / 1e6 for w in ("sut", "judge")}
    print(f"wrote {out.name}: {len(results)} trials ({len(pairs)} distinct case/rep) from experiment {exp.id}")
    print(f"  cost from stored usage: SUT ${usd['sut']:.4f}, judge ${usd['judge']:.4f}; {dict(tokens)}")
    for g in ("action", "path", "rubric"):
        vals = [x[g] for x in results if x[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)} pass ({len(results) - len(vals)} unscored)")
    print(f"  cross-check vs supportdesk graders on stored tool observations: "
          f"{'all agree' if not disagree else 'DISAGREE ' + ', '.join(disagree)}")  # fmt: skip
    if other:
        print(f"  scores not from the evaluators (not exported): {other}")


def turn_of(o: Any) -> int:
    return int(cast(JSON, o.metadata)["turn"])  # metadata values may come back as strings


def transcript_from_tools(tools: list[Any], close: JSON) -> list[JSON]:
    """supportdesk's transcript shape from the stored tool observations: one assistant turn per `turn`, then the
    close."""
    msgs: list[JSON] = []
    by_turn: dict[int, list[Any]] = defaultdict(list)
    for o in tools:
        by_turn[turn_of(o)].append(o)
    for turn in sorted(by_turn):
        os_ = by_turn[turn]
        calls = [{"id": cast(JSON, o.metadata)["call_id"], "name": o.name, "args": loads(o.input)} for o in os_]
        msgs.append({"role": "assistant", "text": "", "tool_calls": calls})
        msgs += [
            {"role": "tool", "id": cast(JSON, o.metadata)["call_id"], "name": o.name, "result": loads(o.output),
             "error": o.level == "ERROR", "ms": cast(JSON, o.metadata).get("ms", 0)}
            for o in os_
        ]  # fmt: skip
    msgs.append(
        {"role": "assistant", "text": "", "tool_calls": [{"id": "close", "name": "close_ticket", "args": close}]}
    )
    return msgs


def compare(name: str, base: str) -> None:
    """Per case, each run's pass rate per score over its trials, from the stored scores: the numbers the
    comparison view shows. Langfuse's API has no comparison or aggregate endpoint for experiments."""
    runs: dict[str, dict[str, dict[str, list[float]]]] = {}
    for n in (name, base):
        exp = experiment(n)
        per: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for it in experiment_items(exp):
            case_id = cast(JSON, it.experiment_item_metadata)["case_id"]
            for s in scores_of(it):
                per[case_id][s.name].append(float(s.value))
        runs[n] = per
        totals: dict[str, list[float]] = defaultdict(list)
        for case in per.values():
            for k, v in case.items():
                totals[k] += v
        print(n, exp.id, {k: f"{sum(v) / len(v):.3f} (n={len(v)})" for k, v in sorted(totals.items())})

    def mean(v: list[float]) -> float:
        return sum(v) / len(v) if v else float("nan")

    keys = ("action", "path", "rubric", "accurate", "no_outside_promises", "tone")
    print(f"{'case':<10}" + "".join(f"{k[:10]:>22}" for k in keys))
    for case_id in sorted(runs[name]):
        cells: list[str] = []
        for k in keys:
            a, b = (runs[n][case_id].get(k, []) for n in (name, base))
            mark = " " if mean(a) == mean(b) else "v" if mean(a) < mean(b) else "^"
            cells.append(f"{mean(a):.2f} vs {mean(b):.2f} {mark}".rjust(22))
        print(f"{case_id:<10}" + "".join(cells))


def queue(name: str) -> None:
    """An annotation queue (created if missing) holding two of the run's trials, scored with the project's existing
    'pass or fail' config (the queue needs at least one config; none is created here)."""
    queues = {q.name: q for q in lf.api.annotation_queues.list_queues().data}
    if QUEUE in queues:
        q = queues[QUEUE]
    else:
        cfg = next(c for c in lf.api.score_configs.get().data if c.name == "pass or fail")
        q = lf.api.annotation_queues.create_queue(
            name=QUEUE, score_config_ids=[cfg.id], description="P9: trials to review"
        )
    items = experiment_items(experiment(name))
    items.sort(key=lambda i: (i.experiment_item_metadata["case_id"], int(i.metadata["rep"])))
    for it in items[:2]:
        lf.api.annotation_queues.create_queue_item(
            q.id, object_id=it.trace_id, object_type=AnnotationQueueObjectType.TRACE
        )
        print(f"queued {it.experiment_item_metadata['case_id']}#{it.metadata['rep']} (trace {it.trace_id})")
    print(f"queue {q.name} ({q.id}): {len(lf.api.annotation_queues.list_queue_items(q.id).data)} items")


def review(name: str, key: str, rep: int | None) -> None:
    """One human score (0) on `key` for a trial the evaluator passed (the given rep, else the first found), through
    the SDK, on the same observation the evaluator scored; then what is stored for that trial."""
    items = sorted(experiment_items(experiment(name)), key=lambda i: i.trace_id)
    passed = [i for i in items if any(s.name == key and s.value for s in scores_of(i))]
    it = next(i for i in passed if rep is None or int(i.metadata["rep"]) == rep)
    lf.create_score(
        trace_id=it.trace_id,
        observation_id=it.id,
        name=key,
        value=False,
        data_type="BOOLEAN",
        comment="human review: P9 test of what a human score does to the run's numbers",
        metadata={"by": "human"},
    )
    lf.flush()
    print(f"scored {key}=0 on {it.experiment_item_metadata['case_id']}#{it.metadata['rep']} (trace {it.trace_id})")
    t0 = time.perf_counter()
    got = stored_scores(it.trace_id, key)
    while len(got) < 2 and time.perf_counter() - t0 < 120:
        time.sleep(2)
        got = stored_scores(it.trace_id, key)
    print(f"  after {time.perf_counter() - t0:.0f}s:")
    for s in got:
        print(f"  {s.name}={s.value} source={s.source} metadata={s.metadata} on {' '.join(subject(s))}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dataset")
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--limit", type=int)
    r.add_argument("--trials", type=int, default=K)
    e = sub.add_parser("export")
    e.add_argument("name")
    e.add_argument("--out", type=Path, default=HERE / "p9_results.jsonl")
    c = sub.add_parser("compare")
    c.add_argument("name")
    c.add_argument("base")
    q = sub.add_parser("queue")
    q.add_argument("name")
    v = sub.add_parser("review")
    v.add_argument("name")
    v.add_argument("--key", default="rubric")
    v.add_argument("--rep", type=int)
    a = p.parse_args()
    if a.cmd == "dataset":
        upload_dataset()
    elif a.cmd == "run":
        run(a.name, a.limit, a.trials)
    elif a.cmd == "export":
        export(a.name, a.out)
    elif a.cmd == "compare":
        compare(a.name, a.base)
    elif a.cmd == "queue":
        queue(a.name)
    else:
        review(a.name, a.key, a.rep)
    lf.flush()


if __name__ == "__main__":
    main()
