"""Side experiment, not part of the port: re-judge a recorded run's replies with the same three G-Evals, but with
DeepEval told that claude-haiku-5-5 supports structured output (its model table has no entry for it, so by default
it asks for JSON in free text). Does that remove the no-verdict errors, and does it move any verdict?

    uv run python structured_check.py .deepeval/run2_framed.json [--free-text]    # ~90 judge calls

--free-text leaves the table alone, so the same replies are re-judged exactly as in the run: a same-condition repeat,
to tell the output mode's effect from call-to-call noise. Overwrites .deepeval/.latest_test_run.json.
"""

import json
import sys
from pathlib import Path
from typing import Any

from deepeval import evaluate  # pyright: ignore[reportUnknownVariableType]
from deepeval.evaluate import AsyncConfig, DisplayConfig, ErrorConfig
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase, ToolCall
from export import row
from p9 import judge, rubric_metrics

type JSON = dict[str, Any]


def main() -> None:
    recorded: list[JSON] = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["testRunData"]["testCases"]
    model = judge()
    if "--free-text" not in sys.argv:
        model.model_data.supports_structured_outputs = True  # the one change: what the table would say
    cases: list[LLMTestCase] = []
    for tc in recorded:
        if tc.get("actualOutput"):
            calls: list[JSON] = tc.get("toolsCalled") or []
            tools = [ToolCall(name=c["name"], input_parameters=c.get("inputParameters"), output=c.get("output"))
                     for c in calls]  # fmt: skip
            cases.append(LLMTestCase(name=tc["name"], input=tc["input"], actual_output=tc["actualOutput"],
                                     context=tc["context"], tools_called=tools))  # fmt: skip
    display = DisplayConfig(print_results=False, show_indicator=False)
    metrics: list[BaseMetric] = list(rubric_metrics(model))
    result = evaluate(test_cases=cases, metrics=metrics, async_config=AsyncConfig(max_concurrent=10),
                      display_config=display, error_config=ErrorConfig(ignore_errors=True))  # fmt: skip
    before = {tc["name"]: row(tc)["rubric"] for tc in recorded}
    errors, moved = 0, list[str]()
    for tr in result.test_results:
        md = tr.metrics_data or []
        errors += sum(1 for m in md if m.error)
        after = None if any(m.error for m in md) else all(m.success for m in md)
        print(f"{tr.name:<12} {after}")
        if after != before[tr.name]:
            moved.append(f"{tr.name}: {before[tr.name]} -> {after}")
    passed = sum(1 for tr in result.test_results if all(m.success and not m.error for m in tr.metrics_data or []))
    print(f"{len(result.test_results)} replies, {errors} metric errors, rubric pass {passed}; changed: {moved or '-'}")


if __name__ == "__main__":
    main()
