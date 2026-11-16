"""P9 slice as a DeepEval pytest suite. Each test runs the agent once on one Golden and asserts the three graders.

    deepeval test run test_p9.py --repeat 3 --ignore-errors

--repeat (pytest-repeat, bundled with deepeval) gives k=3; --ignore-errors records a judge error on the test case
instead of aborting it (export.py counts it as no verdict).
"""

import pytest
from deepeval import assert_test
from deepeval.dataset import Golden
from deepeval.metrics import BaseMetric
from p9 import RUN_ID, ActionMetric, LookedUpFirstMetric, dataset, judge, meta, rubric_metrics, to_test_case

import supportdesk

GOLDENS = dataset().goldens
JUDGE = judge()


@pytest.mark.parametrize("golden", GOLDENS, ids=[g.name or "" for g in GOLDENS])
def test_slice(golden: Golden, request: pytest.FixtureRequest) -> None:
    rep: int = request.node.callspec.params.get("__pytest_repeat_step_number", 0)  # pytest-repeat's 0-based index
    faults = meta(golden)["faults"]
    trial = supportdesk.run(golden.input, faults, case_id=golden.name or "", rep=rep, run_id=RUN_ID)
    tc = to_test_case(golden, trial)
    metrics: list[BaseMetric] = [ActionMetric(), LookedUpFirstMetric()]
    if tc.actual_output:  # no reply, no rubric verdict (the reference records null)
        metrics += rubric_metrics(JUDGE)
    assert_test(tc, metrics)
