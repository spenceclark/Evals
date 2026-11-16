"""Promptfoo python assertion: the path grader, supportdesk.path.looked_up_first, over the transcript the provider
returned in its metadata (Promptfoo's own trajectory assertions read OpenTelemetry spans, not provider output)."""

from typing import Any

from evalkit.core import Case, trial_from_dict
from supportdesk.path import looked_up_first


def get_assert(output: str, context: dict[str, Any]) -> dict[str, Any]:
    v = context["vars"]
    trial = trial_from_dict(context["metadata"]["trial"])
    s = looked_up_first(Case(v["case_id"], v["email"], v["expected"], faults=v["faults"]), trial)
    if s is None:  # no transcript: not applicable. Promptfoo has no "skip", so pass and say so.
        return {"pass": True, "score": 1, "reason": "n/a: no transcript"}
    return {"pass": s.passed, "score": float(s.passed), "reason": s.reason or "looked up before acting"}
