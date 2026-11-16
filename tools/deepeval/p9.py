"""P9 in DeepEval: the slice as Goldens, a supportdesk Trial as an LLMTestCase, and the three graders as metrics.

test_p9.py runs it (`deepeval test run test_p9.py --repeat 3`); export.py turns DeepEval's recorded run into
p9_results.jsonl.
"""

import json
import os
from pathlib import Path
from typing import Any, cast

from deepeval.dataset import EvaluationDataset, Golden
from deepeval.metrics import BaseMetric, GEval
from deepeval.models import AnthropicModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from evalkit.core import Trial, cost
from supportdesk.judges import V2, view
from supportdesk.path import SIDE_EFFECTS, events
from supportdesk.shop import SYSTEM_PROMPT

type JSON = dict[str, Any]

HERE = Path(__file__).resolve().parent
SLICE = HERE.parent / "p9" / "slice.jsonl"
TOOL = "deepeval"
RUN_ID = "p9-deepeval"  # Vessel session for the agent's calls and the judge's
JUDGE_MODEL = "claude-haiku-5-5"
VESSEL = os.environ.get("VESSEL_URL", "").rstrip("/")


def meta(x: Golden | LLMTestCase) -> JSON:
    """Golden.additional_metadata / LLMTestCase.metadata, typed (deepeval types them as a bare Dict)."""
    m = x.additional_metadata if isinstance(x, Golden) else x.metadata  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return cast(JSON, m or {})


# --- Case: a Golden before the run, an LLMTestCase after it --------------------------------------------------------


def dataset() -> EvaluationDataset:
    """The slice as DeepEval Goldens. Our expected dict has no Golden field, so it rides in additional_metadata."""
    goldens: list[Golden] = []
    for line in SLICE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            extra = {"expected": row["expected"], "faults": row.get("faults", {})}
            goldens.append(Golden(name=row["id"], input=row["input"], expected_output=row["expected"]["action"],
                                  additional_metadata=extra, multimodal=False))  # fmt: skip
    return EvaluationDataset(goldens=goldens)


def to_test_case(golden: Golden, trial: Trial) -> LLMTestCase:
    """One trial as an LLMTestCase. The judge-visible fields carry what supportdesk.judges.view shows the P4 judge:
    the email (input), the reply (actual_output), the policy and what changed (context), the tool calls with their
    results (tools_called). Everything else goes in metadata, which the judge is never given."""
    item = {"input": golden.input, "messages": trial.messages, "outcome": trial.outcome, "output": trial.output}
    v = view(item, reply=trial.output.get("reply") or "", policy=SYSTEM_PROMPT)  # no close_ticket: no reply
    calls = [e for e in events(trial) if e.name != "close_ticket"]
    return LLMTestCase(
        name=f"{golden.name}#{trial.rep}",
        input=golden.input,
        actual_output=trial.output.get("reply") or None,
        expected_output=golden.expected_output,
        context=[f"SHOP POLICY\n{v['policy']}", f"WHAT ACTUALLY CHANGED\n{v['happened']}"],
        # ToolCall has no turn and no error flag: an error shows as its {"error": ...} result, a call that never ran
        # has output None, and the turn goes in metadata (the path metric needs it).
        tools_called=[ToolCall(name=e.name, input_parameters=e.args, output=e.result) for e in calls],
        token_cost=cost(trial.calls),
        completion_time=trial.latency_ms / 1000,
        metadata={
            "case_id": golden.name,
            "rep": trial.rep,
            "action": trial.output.get("action"),
            "also_ok": [a["action"] for a in meta(golden)["expected"].get("also_ok", [])],
            "tool_turns": [e.turn for e in calls],
            "status": trial.status,
        },
    )


# --- Graders: metrics ------------------------------------------------------------------------------------------------


class CodeMetric(BaseMetric):
    """A deterministic pass/fail: score 1 or 0 against threshold 1. No model, so measure and a_measure are the same."""

    metric_name = "code"

    def __init__(self) -> None:
        self.threshold = 1.0
        self.include_reason = True
        self.async_mode = False
        self.evaluation_cost = 0.0

    def check(self, tc: LLMTestCase) -> tuple[bool, str]:
        raise NotImplementedError

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        ok, self.reason = self.check(test_case)
        self.score = 1.0 if ok else 0.0
        self.success = ok
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.metric_name


class ActionMetric(CodeMetric):
    """supportdesk.graders.action: the close_ticket action is the expected one, or an also_ok alternative's."""

    metric_name = "action"

    def check(self, tc: LLMTestCase) -> tuple[bool, str]:
        m = meta(tc)
        got, want = m["action"], [tc.expected_output, *m["also_ok"]]
        return got in want, f"expected {' or '.join(map(str, want))}, got {got}"


class LookedUpFirstMetric(CodeMetric):
    """supportdesk.path.looked_up_first: no issue_refund or escalate before a successful get_order of that order in an
    earlier turn. Reads tools_called, plus the turn of each call from metadata (ToolCall has no turn)."""

    metric_name = "looked_up_first"

    def check(self, tc: LLMTestCase) -> tuple[bool, str]:
        calls, turns = tc.tools_called or [], meta(tc)["tool_turns"]
        seen: dict[str, int] = {}  # order id -> first turn it was successfully looked up in
        bad: list[str] = []
        for call, turn in zip(calls, turns, strict=True):
            out, ran = call.output, call.output is not None
            if call.name == "get_order" and ran and isinstance(out, dict) and "error" not in out:
                seen.setdefault(str(out["id"]), turn)  # pyright: ignore[reportUnknownArgumentType]
            oid = str((call.input_parameters or {}).get("order_id", "")).strip().upper()
            if call.name in SIDE_EFFECTS and ran and not (oid in seen and seen[oid] < turn):
                bad.append(f"{call.name}({oid}) in turn {turn}")
        return not bad, ("no successful get_order in an earlier turn: " + ", ".join(bad)) if bad else "ok"


def judge() -> AnthropicModel:
    """Haiku 5.5 as judge. deepeval 4.3.4's model table has no claude-haiku-5-5: without the prices given here it
    records no cost, and with no table entry it asks for JSON in free text rather than with structured output."""
    route: JSON = {}
    if VESSEL:
        route = {"base_url": f"{VESSEL}/b/anthropic/t/p9-deepeval", "default_headers": {"X-Vessel-Session": RUN_ID}}
    return AnthropicModel(model=JUDGE_MODEL, cost_per_input_token=0.10 / 1e6, cost_per_output_token=0.50 / 1e6,
                          **route)  # fmt: skip


# What the judge is shown, in this order. Never EXPECTED_OUTPUT or METADATA: those hold the answer key.
JUDGE_SEES = [SingleTurnParams.CONTEXT, SingleTurnParams.INPUT, SingleTurnParams.TOOLS_CALLED,
              SingleTurnParams.ACTUAL_OUTPUT]  # fmt: skip


# P4's system prompt (supportdesk.judges.RUBRIC_SYSTEM_V0) told its judge what each piece of material is. G-Eval has no
# system prompt and labels the fields with its own names, so this step says the same in G-Eval's terms. Run 1 had no
# such step (P9.md, "Results vs reference").
FRAMING = (
    "You grade the reply a customer support agent sent to a customer, against ONE criterion (the next step). The "
    "Actual Output is the reply that was sent. The Input is the customer's email. Tools Called are the tool calls the "
    "agent made and their results. The Context is the shop's policy (the agent's own instructions) and what actually "
    "changed in the shop's systems. Judge the reply against the next step's criterion only."
)


def rubric_metrics(model: AnthropicModel) -> list[GEval]:
    """Rubric v2 as three G-Evals, one per criterion. strict_mode is G-Eval's only binary mode (score 0 or 1, threshold
    forced to 1). evaluation_steps is pinned (the framing, then the criterion text), so G-Eval doesn't write its own."""
    return [GEval(name=name, evaluation_params=JUDGE_SEES, evaluation_steps=[FRAMING, text], model=model,
                  strict_mode=True) for name, text in V2.criteria.items()]  # fmt: skip


RUBRIC = list(V2.criteria)
