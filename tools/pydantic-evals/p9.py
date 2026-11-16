"""P9: the eval slice in Pydantic Evals. 10 cases x 3 repeats, three graders, exported to p9_results.jsonl.

    uv run python p9.py                      # the graded run -> p9_results.jsonl
    uv run python p9.py --spans              # plus live OTel spans for the agent and a span-based path grader
    uv run python p9.py --naive-judge --cases elig-01 --repeat 1   # stock LLMJudge on the raw case, for the record

Set VESSEL_URL to route every call (agent and judge) through Vessel, session p9-pydantic-evals.
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext, LLMJudge, MaxModelRequests
from pydantic_evals.evaluators.evaluator import EvaluatorOutput
from pydantic_evals.reporting import EvaluationReport

import supportdesk
from evalkit.core import Case as EvalkitCase
from evalkit.core import Trial, cost
from evalkit.judge import fence
from supportdesk.judges import V2, view
from supportdesk.path import SIDE_EFFECTS, looked_up_first
from supportdesk.shop import SYSTEM_PROMPT

HERE = Path(__file__).resolve().parent
SLICE = HERE.parent / "p9" / "slice.jsonl"
RESULTS = HERE / "p9_results.jsonl"
TOOL = "pydantic-evals"
SESSION = f"p9-{TOOL}"
VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")

type Expected = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Ticket:
    """A case's inputs. The task sees only this, so it carries the id and faults as well as the email."""

    case_id: str
    email: str
    faults: dict[str, Any] = field(default_factory=dict[str, Any])


type Ctx = EvaluatorContext[Ticket, Trial, Expected]


def load_dataset(ids: list[str] | None) -> Dataset[Ticket, Trial, Expected]:
    rows = [json.loads(x) for x in SLICE.read_text(encoding="utf-8").splitlines() if x.strip()]
    cases = [
        Case(name=r["id"], inputs=Ticket(r["id"], r["input"], r.get("faults", {})), metadata=r["expected"])
        for r in rows
        if ids is None or r["id"] in ids
    ]
    return Dataset[Ticket, Trial, Expected](name="p9-slice", cases=cases)


_started: dict[str, int] = {}


async def support_desk(t: Ticket) -> Trial:
    """The task. It can't see which repeat it is running, so `rep` here is only a label for Vessel's tags."""
    rep = _started[t.case_id] = _started.get(t.case_id, -1) + 1
    return await supportdesk.arun(t.email, t.faults, case_id=t.case_id, rep=rep, run_id=SESSION)


@dataclass
class Action(Evaluator[Ticket, Trial, Expected]):
    """The close_ticket action is the expected one, or the action of an `also_ok` alternative."""

    def get_default_evaluation_name(self) -> str:
        return "action"

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        assert ctx.metadata is not None
        ok = [ctx.metadata["action"], *(a["action"] for a in ctx.metadata.get("also_ok", []))]
        got = ctx.output.output.get("action")
        return EvaluationReason(got in ok, reason=f"expected {' or '.join(ok)}, got {got}")


@dataclass
class LookedUpFirst(Evaluator[Ticket, Trial, Expected]):
    """No refund or escalation before a successful get_order of that order, read from the transcript in the output."""

    def get_default_evaluation_name(self) -> str:
        return "path"

    def evaluate(self, ctx: Ctx) -> EvaluatorOutput:
        assert ctx.metadata is not None
        case = EvalkitCase(ctx.inputs.case_id, ctx.inputs.email, ctx.metadata, faults=ctx.inputs.faults)
        s = looked_up_first(case, ctx.output)
        return {} if s is None else EvaluationReason(s.passed, reason=s.reason)


JUDGE = AnthropicModel(
    "claude-haiku-5-5",
    provider=AnthropicProvider(base_url=f"{VESSEL}/b/anthropic/t/{SESSION}" if VESSEL else None),
)
JUDGE_SETTINGS = AnthropicModelSettings(
    max_tokens=8000,  # as P4's judge (evalkit.judge.JudgeModel); unset, pydantic-ai sends the model's maximum
    anthropic_effort="medium",
    extra_headers={"X-Vessel-Session": SESSION} if VESSEL else {},
)
SHOWN = {"policy": "shop_policy", "email": "customer_email", "actions": "agent_tool_calls_and_results",
         "happened": "what_actually_changed"}  # fmt: skip


@dataclass(repr=False)
class Criterion(LLMJudge):
    """The stock LLMJudge, shown what P4's judge saw: view()'s fields, fenced as <data>, as its <Input>, and the reply
    as its <Output>. Stock, it would get the Ticket and the whole Trial as JSON, and no policy."""

    async def evaluate(self, ctx: EvaluatorContext[object, object, object]) -> EvaluatorOutput:
        assert isinstance(ctx.inputs, Ticket) and isinstance(ctx.output, Trial)
        t = ctx.output
        if not t.output.get("reply"):
            return {}  # no reply to grade: no verdict, not a fail
        item = {"messages": t.messages, "input": ctx.inputs.email, "outcome": t.outcome, "output": t.output}
        v = view(item, policy=SYSTEM_PROMPT)  # no "expected" in item: no answer key
        shown = "\n\n".join(fence(tag, v[k]) for k, tag in SHOWN.items())
        return await super().evaluate(replace(ctx, inputs=shown, output=fence("reply_sent_to_customer", v["reply"])))


def criteria(cls: type[LLMJudge]) -> list[LLMJudge]:
    """Rubric v2: one judge call per criterion, its rubric being P4's system prompt for that criterion."""
    return [
        cls(
            rubric=V2.system.format(criterion=text),
            model=JUDGE,
            include_input=True,
            model_settings=JUDGE_SETTINGS,
            assertion={"evaluation_name": f"rubric_{name}", "include_reason": True},
        )
        for name, text in V2.criteria.items()
    ]


def instrument() -> None:
    """Live OTel spans for our agent, so pydantic-evals' span tree and its agentic evaluators can see it.

    Setting a tracer provider is enough for the model calls: the anthropic SDK (2.8) then emits its own
    `anthropic.messages.create` span per call (gen_ai.operation.name "chat", token usage). The tools run in our own
    loop, which emits nothing, so this wraps the store's tool dispatch at runtime, as an auto-instrumentation package
    would (no file changes), with the span name and attributes pydantic-ai's own tool spans use."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from supportdesk.shop import Store

    trace.set_tracer_provider(TracerProvider())  # no exporter: spans stay in this process
    tracer = trace.get_tracer(SESSION)
    execute = Store.execute

    def traced_execute(self: Store, name: str, args: dict[str, Any]) -> str:
        attrs = {"gen_ai.tool.name": name, "gen_ai.tool.call.arguments": json.dumps(args)}
        with tracer.start_as_current_span(f"execute_tool {name}", attributes=attrs) as span:
            out = execute(self, name, args)  # a ToolError marks the span as an error
            span.set_attribute("gen_ai.tool.call.result", out)
            return out

    Store.execute = traced_execute


@dataclass
class PathFromSpans(Evaluator[Ticket, Trial, Expected]):
    """The path grader again, from the span tree instead of the transcript. A tool span's turn is the number of chat
    spans (the SDK's) that started before it, minus one."""

    def get_default_evaluation_name(self) -> str:
        return "path_spans"

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        turn, seen, bad = -1, dict[str, int](), list[str]()
        for n in sorted(ctx.span_tree, key=lambda n: n.start_timestamp):
            if n.attributes.get("gen_ai.operation.name") == "chat":
                turn += 1
                continue
            name = n.attributes.get("gen_ai.tool.name")
            args = json.loads(str(n.attributes.get("gen_ai.tool.call.arguments", "{}")))
            oid = str(args.get("order_id", "")).strip().upper()
            if name == "get_order" and n.status != "error":
                seen.setdefault(json.loads(str(n.attributes["gen_ai.tool.call.result"]))["id"], turn)
            elif name in SIDE_EFFECTS and not (oid in seen and seen[oid] < turn):
                bad.append(f"{name}({oid}) in turn {turn}")
        return EvaluationReason(not bad, reason=", ".join(bad))


def passed(c: Any, name: str) -> bool | None:
    r = c.assertions.get(name)
    return None if r is None else bool(r.value)


def export(report: EvaluationReport[Ticket, Trial, Expected]) -> list[dict[str, Any]]:
    """One row per trial, from the report: cases carry their assertions; failed tasks become all-null rows."""
    rows: list[dict[str, Any]] = []
    for c in report.cases:
        crit = [passed(c, f"rubric_{k}") for k in V2.criteria]
        rubric = None if None in crit else all(crit)
        rows.append({"tool": TOOL, "case_id": c.source_case_name or c.name, "rep": rep(c.name),
                     "action": passed(c, "action"), "path": passed(c, "path"), "rubric": rubric,
                     "reply": c.output.output.get("reply")})  # fmt: skip
    for f in report.failures:
        rows.append({"tool": TOOL, "case_id": f.source_case_name or f.name, "rep": rep(f.name), "action": None,
                     "path": None, "rubric": None, "reply": None})  # fmt: skip
    return sorted(rows, key=lambda r: (r["case_id"], r["rep"]))


def rep(name: str) -> int:
    m = re.search(r"\[(\d+)/\d+\]$", name)
    return int(m[1]) - 1 if m else 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", nargs="*", help="case ids (default: all 10); a subset writes no results file")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--spans", action="store_true", help="instrument the agent and add the span-based graders")
    p.add_argument("--naive-judge", action="store_true", help="stock LLMJudge on the raw Ticket and Trial instead")
    a = p.parse_args()

    ds = load_dataset(a.cases)
    ds.add_evaluator(Action())
    ds.add_evaluator(LookedUpFirst())
    for judge in criteria(LLMJudge if a.naive_judge else Criterion):
        ds.add_evaluator(judge)
    if a.spans:
        instrument()
        ds.add_evaluator(PathFromSpans())
        ds.add_evaluator(MaxModelRequests(max_requests=8))  # path.step_budget, natively, from the SDK's spans

    t0 = time.perf_counter()
    report = ds.evaluate_sync(support_desk, repeat=a.repeat, max_concurrency=8, name=SESSION)
    wall = time.perf_counter() - t0
    report.print(include_reasons=False)

    rows = export(report)
    agent = sum(cost(c.output.calls) for c in report.cases)
    print(f"{len(rows)} trials, {len(report.failures)} task failures, agent ${agent:.4f}, wall {wall:.0f}s")
    for g in ("action", "path", "rubric"):
        vals = [r[g] for r in rows if r[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)} pass ({len(rows) - len(vals)} null)")
    if a.spans:
        disagree = [c.name for c in report.cases if passed(c, "path") != passed(c, "path_spans")]
        print(f"  path from spans disagrees with path from transcript on {len(disagree)} trials: {disagree}")
    if a.cases is None and not (a.naive_judge or a.spans):  # the graded run only
        assert len(rows) == 10 * a.repeat, len(rows)
        RESULTS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"wrote {RESULTS.name}")


if __name__ == "__main__":
    main()
