"""Offline checks, no API calls: do the port's code metrics agree with the reference graders, and can DeepEval's own
ToolCorrectnessMetric express the path rule?

    uv run python parity.py [RUNS_DIR]    # RUNS_DIR: a harness runs/ folder (default ../../runs)

1. ActionMetric vs supportdesk.graders.action and LookedUpFirstMetric vs supportdesk.path.looked_up_first, on every
   recorded trial under RUNS_DIR and on synthetic trajectories built to hit each way the path rule can fail.
2. ToolCorrectnessMetric(should_consider_ordering=True), default threshold 0.5, on the same synthetic trajectories,
   expecting [get_order(A1), issue_refund(A1)] (escalate for the escalation one), by name only and with arguments:
   where its verdict differs from looked_up_first.
"""

import json
import sys
from pathlib import Path
from typing import Any

from deepeval.dataset import Golden
from deepeval.metrics import ToolCorrectnessMetric
from deepeval.models import AnthropicModel
from deepeval.test_case import ToolCall, ToolCallParams
from p9 import JUDGE_MODEL, ActionMetric, LookedUpFirstMetric, to_test_case

from evalkit.core import Case, Trial, trial_from_dict
from supportdesk.graders import action
from supportdesk.path import looked_up_first

type JSON = dict[str, Any]

ORDER = {"id": "A1", "email": "a@example.com", "items": [{"name": "x", "price": 10.0}]}


def golden(case_id: str, expected: JSON) -> Golden:
    extra = {"expected": expected, "faults": {}}
    return Golden(name=case_id, input="(email)", expected_output=expected["action"], additional_metadata=extra,
                  multimodal=False)  # fmt: skip


def port(g: Golden, t: Trial) -> tuple[bool, bool]:
    tc = to_test_case(g, t)
    a, p = ActionMetric(), LookedUpFirstMetric()
    a.measure(tc)
    p.measure(tc)
    return bool(a.success), bool(p.success)


def reference(case: Case, t: Trial) -> tuple[bool, bool | None]:
    lf = looked_up_first(case, t)
    return action(case, t).passed, lf.passed if lf else None


def recorded(runs: Path) -> None:
    n, diffs, skipped, fails = 0, 0, 0, {"action": 0, "path": 0}
    for f in sorted(runs.glob("*/results.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            t = trial_from_dict(r["trial"])
            if not t.messages or "escalations" not in t.outcome:  # baselines (no transcript); pre-P2 trial format
                skipped += 1
                continue
            case = Case(r["case_id"], "(email)", r["expected"], faults={})
            got, want = port(golden(r["case_id"], r["expected"]), t), reference(case, t)
            n += 1
            fails["action"] += not want[0]
            fails["path"] += want[1] is False
            if got != want:
                diffs += 1
                print(f"  DIFF {f.parent.name} {r['case_id']}#{r['rep']}: port {got}, reference {want}")
    print(f"recorded trials: {n} ({skipped} skipped), disagreements: {diffs}, reference fails: {fails}")
    assert diffs == 0


def turn(*calls: tuple[str, JSON, Any]) -> list[JSON]:
    """One assistant turn with its tool results. A result of None means the call never ran."""
    ids = [f"t{i}" for i in range(len(calls))]
    out: list[JSON] = [{"role": "assistant", "text": "", "tool_calls": [
        {"id": i, "name": n, "args": a} for i, (n, a, _) in zip(ids, calls, strict=True)]}]  # fmt: skip
    for i, (n, _, r) in zip(ids, calls, strict=True):
        if r is not None:
            out.append({"role": "tool", "id": i, "name": n, "result": r, "error": "error" in r, "ms": 0})
    return out


def synthetic() -> dict[str, Trial]:
    get = ("get_order", {"order_id": "A1"}, ORDER)
    get_other = ("get_order", {"order_id": "A2"}, {**ORDER, "id": "A2"})
    get_fail = ("get_order", {"order_id": "A1"}, {"error": "service temporarily unavailable (503)"})
    refund = ("issue_refund", {"order_id": "A1", "amount": 10.0, "reason": "r"}, {"ok": True})
    escalate = ("escalate", {"order_id": "A1", "reason": "r"}, {"ok": True})
    close = ("close_ticket", {"action": "refunded", "order_id": "A1", "amount": 10.0, "reply": "done"}, None)
    flows = {
        "lookup_then_refund": [*turn(get), *turn(refund), *turn(close)],
        "lookup_only": [*turn(get), *turn(close)],
        "refund_without_lookup": [*turn(refund), *turn(close)],
        "escalate_without_lookup": [*turn(escalate), *turn(close)],
        "lookup_other_order": [*turn(get_other), *turn(refund), *turn(close)],
        "failed_lookup": [*turn(get_fail), *turn(refund), *turn(close)],
        "lookup_and_refund_same_turn": [*turn(get, refund), *turn(close)],
        "refund_then_lookup": [*turn(refund), *turn(get), *turn(close)],
        "stray_refund_then_good_pair": [*turn(refund), *turn(get), *turn(refund), *turn(close)],
    }
    out = {"action": "refunded", "order_id": "A1", "amount": 10.0, "reply": "done"}
    return {k: Trial(k, 0, "synthetic", [{"role": "user", "text": "(email)"}, *m], out,
                     {"refunds": [], "escalations": []}) for k, m in flows.items()}  # fmt: skip


def synthetic_checks() -> None:
    expected: JSON = {"action": "refunded", "order_id": "A1", "amount": 10.0, "refunds": [], "escalations": []}
    # Never called (no available_tools). Without a model, ToolCorrectnessMetric quietly builds OpenAI gpt-5.4.
    model = AnthropicModel(model=JUDGE_MODEL, api_key="offline-unused", cost_per_input_token=0, cost_per_output_token=0)
    print(f"{'trajectory':<30} {'reference':>9} {'port':>5} {'TC names':>9} {'TC +args':>9}")
    diffs = 0
    for name, t in synthetic().items():
        ref = reference(Case(name, "(email)", expected), t)[1]
        tc = to_test_case(golden(name, expected), t)
        mine = LookedUpFirstMetric()
        mine.measure(tc)
        diffs += mine.success != ref
        act = "escalate" if "escalate" in name else "issue_refund"
        tc.expected_tools = [ToolCall(name=n, input_parameters={"order_id": "A1"}) for n in ("get_order", act)]
        scores: list[str] = []
        for params in ([], [ToolCallParams.INPUT_PARAMETERS]):
            m = ToolCorrectnessMetric(should_consider_ordering=True, evaluation_params=params, model=model,
                                      async_mode=False)  # fmt: skip
            m.measure(tc, _show_indicator=False)
            scores.append(f"{'P' if m.success else 'F'} {m.score:.2f}")
        print(f"{name:<30} {'P' if ref else 'F':>9} {'P' if mine.success else 'F':>5} {scores[0]:>9} {scores[1]:>9}")
    assert diffs == 0


if __name__ == "__main__":
    recorded(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[2] / "runs")
    synthetic_checks()
