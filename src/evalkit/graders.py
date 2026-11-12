"""Graders: (Case, Trial) -> Score. One property per grader; no blended scores."""

from evalkit.core import Case, Score, Trial


def action_match(case: Case, trial: Trial) -> Score:
    want, got = case.expected.get("action"), trial.output.get("action")
    if want is None:
        raise ValueError(f"case {case.id} has no expected action")  # a labelling bug, not a model failure
    return Score("action_match", got == want, reason=f"expected {want}, got {got}")
