"""No-LLM check of the harness's error paths. Run: uv run python tests/test_core.py"""

import asyncio
import contextlib
import io
import json
import tempfile
from pathlib import Path

from evalkit.__main__ import report
from evalkit.core import Call, Case, Score, Trial, run


def _trial(case: Case, rep: int, **kw: object) -> Trial:
    t = Trial(case.id, rep, "m", [], {"action": "denied"}, {})
    for k, v in kw.items():
        setattr(t, k, v)
    return t


async def task(case: Case, *, rep: int, run_id: str) -> Trial:
    match case.id:
        case "raises":
            raise RuntimeError("API down")
        case "wrong_model":
            return _trial(case, rep, calls=[Call("other-model", 1, 1, "end_turn", 1.0)])
        case "truncated":
            return _trial(case, rep, status="truncated")
        case _:
            return _trial(case, rep, calls=[Call("m", 1, 1, "end_turn", 1.0)])


def grader(case: Case, trial: Trial) -> Score:
    if case.id == "grader_crash":
        raise KeyError("boom")
    return Score("g", trial.output["action"] == case.expected["action"])


def main() -> None:
    ids = ["raises", "wrong_model", "truncated", "grader_crash", "ok"]
    cases = [Case(i, "hi", {"action": "denied"}) for i in ids]
    with tempfile.TemporaryDirectory() as tmp:
        cases_path = Path(tmp) / "cases.jsonl"
        cases_path.write_text("", encoding="utf-8")
        run_dir = Path(tmp) / "run"
        asyncio.run(run(cases, task, [grader], run_dir=run_dir, cases_path=cases_path, task_name="t"))

        def rows(name: str) -> dict[str, dict[str, object]]:
            lines = (run_dir / name).read_text(encoding="utf-8").splitlines()
            return {(r := json.loads(line))["case_id"]: r for line in lines}

        errors, results = rows("errors.jsonl"), rows("results.jsonl")
        assert set(errors) == {"raises", "wrong_model", "grader_crash"}, errors
        assert errors["wrong_model"]["stage"] == "served_model"
        assert errors["grader_crash"]["stage"] == "grader:grader"
        assert "Traceback" in str(errors["raises"]["traceback"])
        assert set(results) == {"truncated", "ok"}, results
        assert results["truncated"]["scores"] == []
        assert results["ok"]["scores"] == [{"grader": "g", "passed": True, "value": None, "reason": ""}]

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report(run_dir)
        assert "passed 1/1 graded | truncated/refused 1 | infra errors 3" in out.getvalue(), out.getvalue()
    print("ok: harness error paths")


if __name__ == "__main__":
    main()
