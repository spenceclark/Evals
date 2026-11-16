"""P9: the slice as an oaieval eval class.

Subclasses ModelBasedClassify so the native parts stay native: the judge is the last completion fn on the command line,
the judge prompt is a registry modelgraded spec, `eval_kwargs` reach the judge, and evals' own `classify()` sends the
prompt and parses the verdict. What it overrides:
- eval_sample: one agent run per sample, then three graders on that one trial. Stock ModelBasedClassify takes one
  criterion per eval (`modelgraded_spec_args`), so three criteria would be three evals, each re-running the agent.
- run: the slice has 10 rows; evals has no repeats, so `trials` copies each row k times here.
"""

import json
from random import Random
from typing import Any

from evals.api import record_and_check_match
from evals.elsuite.modelgraded.base import ModelGradedSpec
from evals.elsuite.modelgraded.classify import ModelBasedClassify
from evals.elsuite.modelgraded.classify_utils import INVALID_STR, classify
from evals.record import record_error, record_metrics  # pyright: ignore[reportUnknownVariableType]  (evals is untyped)

from evalkit.core import Case, trial_from_dict
from supportdesk.judges import V2, view
from supportdesk.path import looked_up_first
from supportdesk.shop import SYSTEM_PROMPT

type JSON = dict[str, Any]

# The spec's system message is rubric v2's, bar the last sentence (the verdict format). Checked at load.
P4_LAST = "then decide: passed is true if the reply meets it."
SPEC_LAST = "then decide whether the reply meets it."


def _fenced(v: str) -> str:
    """evalkit.judge.fence's escaping, so a value can't close its own <data> tag."""
    return v.replace("<data", "&lt;data").replace("</data", "&lt;/data")


class P9Slice(ModelBasedClassify):  # pyright: ignore[reportUntypedBaseClass]  (evals is untyped)
    def __init__(self, *args: Any, trials: int = 3, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportUnknownMemberType]
        self.trials = trials
        assert self.mg is not None and isinstance(self.mg.prompt, list)
        self.spec: ModelGradedSpec = self.mg
        system = self.mg.prompt[0]["content"]
        assert system.replace(SPEC_LAST, P4_LAST) == V2.system, "spec system message drifted from rubric v2"

    def eval_sample(self, test_sample: JSON, rng: Random) -> None:
        case = Case(
            test_sample["id"], test_sample["input"], test_sample["expected"], faults=test_sample.get("faults") or {}
        )
        rep: int = test_sample["rep"]
        label = {"case_id": case.id, "rep": rep}
        try:
            result = self.completion_fn(prompt=case.input, faults=case.faults, case_id=case.id, rep=rep)
            trial = trial_from_dict(json.loads(result.get_completions()[0]))
        except Exception as e:
            record_error("agent run failed", e, **label)
            record_metrics(**label, action=None, path=None, rubric=None, reply=None)
            return

        # action (code): evals' own match primitive; expected is the primary action plus any also_ok alternatives.
        ok = [case.expected["action"], *(a["action"] for a in case.expected.get("also_ok", []))]
        action = record_and_check_match(prompt=case.input, sampled=trial.output.get("action") or "", expected=ok)

        # path (trajectory): only possible because the completion string carries the transcript.
        lf = looked_up_first(case, trial)

        # rubric v2: one classify() call per criterion, verdict parsed by evals' cot_classify.
        reply: str | None = trial.output.get("reply")
        choices: dict[str, str] = {}
        if reply:
            item = {"input": case.input, "messages": trial.messages, "outcome": trial.outcome, "output": trial.output}
            fields = {k: _fenced(v) for k, v in view(item, policy=SYSTEM_PROMPT).items() if k != "answer"}
            for name, criterion in V2.criteria.items():
                try:
                    choice, _ = classify(mg=self.spec, completion_fn=self.eval_completion_fn,
                                         completion_kwargs=self.eval_kwargs,
                                         format_kwargs={**fields, "criterion": criterion})  # fmt: skip
                except Exception as e:
                    record_error("judge call failed", e, **label, criterion=name)
                    choice = INVALID_STR
                choices[name] = choice
        rubric = None if not choices or INVALID_STR in choices.values() else all(c == "Y" for c in choices.values())

        record_metrics(**label, action=action is not None, path=lf.passed if lf else None,
                                    path_reason=lf.reason if lf else None, rubric=rubric, criteria=choices,
                                    reply=reply)  # fmt: skip

    def run(self, recorder: Any) -> dict[str, float]:
        rows: list[JSON] = self.get_samples()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        samples = [{**s, "rep": r} for s in rows for r in range(self.trials)]
        self.eval_all_samples(recorder, samples)  # pyright: ignore[reportUnknownMemberType]
        m: list[JSON] = recorder.get_metrics()
        report: dict[str, float] = {}
        for g in ("action", "path", "rubric"):
            vals = [x[g] for x in m if x[g] is not None]
            report[g] = sum(vals) / len(vals) if vals else float("nan")
            report[f"{g}/passed"] = sum(vals)
            report[f"{g}/graded"] = len(vals)
        return report
