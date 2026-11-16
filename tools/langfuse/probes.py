"""Model-free probes of how Langfuse stores an experiment (analysis, not part of the port). No model calls except
`bare`, which runs one trial of the agent.

    uv run python probes.py reps        # one dataset item twice in one run_experiment: two trials, or one?
    uv run python probes.py bare        # a task that only calls supportdesk.arun: what lands with no help?
    uv run python probes.py bare --content  # the same with the Anthropic SDK's content mode on
    uv run python probes.py path-synth  # six synthetic transcripts (four failing) through a hosted experiment
    uv run python probes.py errors      # a raising task and a raising evaluator; nothing is uploaded
"""

import argparse
import datetime as dt
import json
import os
import time
from collections import Counter
from typing import Any, cast

from langfuse import Evaluation, Langfuse
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SENT = InMemorySpanExporter()
EPOCH_ = dt.datetime(
    2026, 11, 1, tzinfo=dt.UTC
)  # a local copy of every span the provider ends, for the "what's sent" audit


def capture() -> None:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.add_span_processor(SimpleSpanProcessor(SENT))


def wait_items(lf: Langfuse, experiment_id: str, since: dt.datetime, want: int) -> list[Any]:
    t0 = time.perf_counter()
    items: list[Any] = []
    while time.perf_counter() - t0 < 300:
        items = lf.api.experiments.list_items(
            from_start_time=since, experiment_id=experiment_id, fields="core,dataset,io,metadata,scores", limit=100
        ).data
        n_scores = sum(len(i.scores or []) for i in items)
        print(f"  +{time.perf_counter() - t0:5.1f}s: {len(items)} items, {n_scores} scores")
        if len(items) >= want and n_scores >= want:
            break
        time.sleep(3)
    return items


def reps() -> None:
    from p9 import DATASET, lf, scores_of

    capture()
    items = sorted(lf.get_dataset(DATASET).items, key=lambda i: i.metadata["case_id"])[:2]
    seen: Counter[str] = Counter()

    def task(*, item: Any, **kwargs: Any) -> Any:
        cid = item.metadata["case_id"]
        rep = seen[cid]
        seen[cid] += 1
        return {"case_id": cid, "rep": rep}

    def const(*, output: Any, **kwargs: Any) -> Evaluation:
        return Evaluation(name="probe", value=1.0 if output["rep"] == 0 else 0.0, comment=json.dumps(output))

    since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    name = f"probe-reps-{int(time.time())}"
    res = lf.run_experiment(name=name, run_name=name, data=items * 2, task=task, evaluators=[const])
    print(f"run {name}: experiment_id {res.experiment_id}, {len(res.item_results)} item results")
    for i in wait_items(lf, res.experiment_id, since, 4):
        print(" ", i.experiment_item_id, i.trace_id, i.output, [(s.name, s.value) for s in scores_of(i)])


def bare(content: bool) -> None:
    # p9 turns on the Anthropic SDK's content mode at import (setdefault); the baseline is the SDK default
    os.environ["ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE"] = "content" if content else "metadata_only"
    from p9 import DATASET, lf

    import supportdesk

    capture()
    item = next(i for i in lf.get_dataset(DATASET).items if i.metadata["case_id"] == "elig-01")

    async def task(*, item: Any, **kwargs: Any) -> Any:
        t = await supportdesk.arun(item.input["email"], item.metadata["faults"], case_id="elig-01",
                                   run_id="p9-langfuse-probe")  # fmt: skip
        return t.output

    since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    name = f"probe-bare-{int(time.time())}"
    res = lf.run_experiment(name=name, run_name=name, data=[item], task=task)
    tid = res.item_results[0].trace_id
    print(f"run {name}: trace {tid}")
    for s in SENT.get_finished_spans():
        scope = s.instrumentation_scope.name if s.instrumentation_scope else None
        print(f"  local span {s.name!r} scope={scope} attrs={sorted((s.attributes or {}).keys())}")
    time.sleep(15)
    for o in lf.api.observations.get_many(trace_id=tid, from_start_time=since, fields="core,basic,io").data:
        print(f"  stored {o.type} {o.name!r}: input {len(o.input or '')} chars, output {len(o.output or '')} chars")


def msg(*calls: tuple[str, str, JSONish]) -> dict[str, Any]:
    return {"role": "assistant", "text": "", "tool_calls": [{"id": i, "name": n, "args": a} for i, n, a in calls]}


def res(call_id: str, name: str, result: Any, error: bool = False) -> dict[str, Any]:
    return {"role": "tool", "id": call_id, "name": name, "result": result, "error": error, "ms": 1}


type JSONish = dict[str, Any]

ORDER = {"id": "A1", "email": "x@example.com", "items": [{"name": "Lamp", "price": 20.0}]}
CLOSE = ("c", "close_ticket", {"action": "refunded", "order_id": "A1", "amount": 20.0, "reply": "Done."})
REFUND = {"order_id": "A1", "amount": 20.0, "reason": "r"}
SYNTH: dict[str, tuple[bool, list[dict[str, Any]]]] = {
    "refund-before-lookup": (False, [
        msg(("t1", "issue_refund", REFUND)), res("t1", "issue_refund", {"ok": True}),
        msg(CLOSE)]),
    "lookup-and-refund-same-turn": (False, [
        msg(("t1", "get_order", {"order_id": "A1"}), ("t2", "issue_refund", REFUND)),
        res("t1", "get_order", ORDER), res("t2", "issue_refund", {"ok": True}),
        msg(CLOSE)]),
    "errored-lookup-then-refund": (False, [
        msg(("t1", "get_order", {"order_id": "A1"})), res("t1", "get_order", "service unavailable", error=True),
        msg(("t1", "issue_refund", REFUND)), res("t1", "issue_refund", {"ok": True}),
        msg(CLOSE)]),
    "other-order-then-escalate": (False, [
        msg(("t1", "get_order", {"order_id": "A2"})), res("t1", "get_order", {**ORDER, "id": "A2"}),
        msg(("t1", "escalate", {"order_id": "A1", "reason": "r"})), res("t1", "escalate", {"ok": True}),
        msg(CLOSE)]),
    "ids-reused-and-padded-id": (True, [
        msg(("t1", "get_order", {"order_id": " a1 "})), res("t1", "get_order", ORDER),
        msg(("t1", "issue_refund", {**REFUND, "order_id": " a1 "})), res("t1", "issue_refund", {"ok": True}),
        msg(CLOSE)]),
    "refund-after-close-never-ran": (True, [
        msg(("t1", "get_order", {"order_id": "A1"})), res("t1", "get_order", ORDER),
        msg(CLOSE, ("t2", "issue_refund", REFUND))]),
}  # fmt: skip


def path_synth() -> None:
    """The port's own task pieces and path evaluator on transcripts where the answer is known, through the hosted
    service; then the stored scores and the stored tool observations read back."""
    from p9 import EPOCH, experiment, experiment_items, lf, observations, path, replay_tools, transcript_from_tools

    from evalkit.core import Case, Trial
    from supportdesk.path import looked_up_first

    def task(*, item: Any, **kwargs: Any) -> Any:
        _, messages = SYNTH[item["metadata"]["case_id"]]
        lf.update_current_span(metadata={"rep": 0, "tool_observations": replay_tools(messages)})
        return {**CLOSE[2], "messages": messages, "outcome": {"refunds": [], "escalations": []}}

    data: Any = [{"input": {"email": "From: x@example.com\n\n" + k}, "expected_output": {"action": "refunded"},
                  "metadata": {"case_id": k, "faults": {}}} for k in SYNTH]  # fmt: skip
    name = f"path-synth-{int(time.time())}"
    lf.run_experiment(name=name, run_name=name, data=data, task=task, evaluators=[cast(Any, path)])
    time.sleep(20)
    exp = experiment(name)
    for it in experiment_items(exp):
        case_id = it.experiment_item_metadata["case_id"]
        want, messages = SYNTH[case_id]
        stored = {s.name: s.value for s in lf.api.scores.get_many(trace_id=it.trace_id, limit=10).data}
        tools = sorted((o for o in observations(it.trace_id, EPOCH) if o.type == "TOOL"),
                       key=lambda o: (o.metadata["turn"], o.start_time))  # fmt: skip
        case = Case(case_id, "", {"action": "refunded"})
        ref = looked_up_first(case, Trial(case_id, 0, "", messages, CLOSE[2], {}))
        rebuilt = looked_up_first(case, Trial(case_id, 0, "", transcript_from_tools(tools, CLOSE[2]), CLOSE[2], {}))
        print(f"  {case_id:<30} want {want!s:<5} stored path={stored.get('path')!s:<5} "
              f"ref={ref and ref.passed!s:<5} from stored tool observations={rebuilt and rebuilt.passed}")  # fmt: skip


def errors(upload: bool) -> None:
    """A task that raises and an evaluator that raises. Without --upload, export to Langfuse is off: what would the
    spans carry? Prints where an exception's text and stack trace would go, and whether they hold local paths. With
    --upload, sends them and prints what Langfuse stored."""
    lf = Langfuse() if upload else Langfuse(should_export_span=lambda s: False)
    capture()

    def task(*, item: Any, **kwargs: Any) -> Any:
        if item["metadata"]["n"] == 0:
            raise ValueError("task failed on purpose")
        return "ok"

    def bad(*, output: Any, **kwargs: Any) -> Evaluation:
        raise RuntimeError("evaluator failed on purpose")

    data: Any = [{"input": "a", "metadata": {"n": 0}}, {"input": "b", "metadata": {"n": 1}}]
    name = f"errors-{int(time.time())}" if upload else "errors"
    lf.run_experiment(name=name, run_name=name, data=data, task=task, evaluators=[bad])

    def leaks(text: str) -> str:
        return ", ".join(m for m in ("Users", ":\\", "site-packages", ".py") if m in text) or "none"

    s: ReadableSpan
    for s in SENT.get_finished_spans():
        attrs = {k: str(v) for k, v in (s.attributes or {}).items()}
        msgs = {k: v for k, v in attrs.items() if "failed on purpose" in v}
        events = [(e.name, {k: str(v) for k, v in (e.attributes or {}).items()}) for e in s.events]
        if msgs or events or s.status.description:
            print(f"  span {s.name!r}: status={s.status.status_code.name} {s.status.description!r}")
            for k, v in msgs.items():
                print(f"    attr {k}: {v[:120]!r} (paths: {leaks(v)})")
            for name, ea in events:
                trace_paths = leaks(ea.get("exception.stacktrace", ""))
                print(f"    event {name}: keys {sorted(ea)}; stacktrace paths: {trace_paths}")
    if not upload:
        return
    time.sleep(20)
    traces = {s.context.trace_id for s in SENT.get_finished_spans() if s.name == "experiment-item-run" and s.context}
    for t in traces:
        for o in lf.api.observations.get_many(
            trace_id=f"{t:032x}", from_start_time=EPOCH_, fields="core,basic,io,metadata"
        ).data:
            text = json.dumps(o.dict(), default=str)
            hit = [k for k in ("stacktrace", "exception", "Traceback", "failed on purpose") if k in text]
            status = f"level={o.level} status={o.status_message!r}"
            print(f"  stored {o.type} {o.name!r}: {status} has {hit}; paths: {leaks(text)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("probe", choices=["reps", "bare", "path-synth", "errors"])
    p.add_argument("--upload", action="store_true", help="errors: send the spans to Langfuse")
    p.add_argument("--content", action="store_true", help="bare: with the Anthropic SDK's content mode on")
    a = p.parse_args()
    if a.probe == "errors":
        errors(a.upload)
    elif a.probe == "bare":
        bare(a.content)
    else:
        {"reps": reps, "path-synth": path_synth}[a.probe]()


if __name__ == "__main__":
    main()
