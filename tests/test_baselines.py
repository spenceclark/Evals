"""Oracle and null baselines over the full case set, asserted per case and per grader. No LLM.

uv run python tests/test_baselines.py [cases/full.jsonl]
"""

import asyncio
import sys
from pathlib import Path

from evalkit.core import Case, grade, load_cases
from supportdesk.graders import GRADERS
from supportdesk.path import OVERFIT, PATH, RECOVERY
from supportdesk.task import null, oracle


async def main(path: Path) -> None:
    cases: list[Case] = load_cases(path)
    for case in cases:
        o = grade(case, await oracle(case, rep=0, run_id="test"), GRADERS)
        assert all(s.passed for s in o), f"oracle fails {case.id}: {[s for s in o if not s.passed]}"

        n = {s.grader: s.passed for s in grade(case, await null(case, rep=0, run_id="test"), GRADERS)}
        e = case.expected
        no_effects = not e["refunds"] and not e["escalations"]
        want = {
            "schema": False,  # empty reply
            "action": e["action"] == "denied",
            "end_state": no_effects,
            "safety": True,  # moves no money
            "claim_vs_outcome": True,  # claims 'denied' and did nothing: always consistent
        }
        if e["action"] != "needs_info":
            want["order_id"] = e["order_id"] is None
        if e["action"] != "escalated":
            want["amount"] = e["amount"] is None
        assert n == want, f"null on {case.id}: got {n}, want {want}"

        # Baselines have no transcript, so path and recovery rules must say "not applicable", never pass.
        for task in (oracle, null):
            t = await task(case, rep=0, run_id="test")
            applied = [g.__name__ for g in (*PATH, *RECOVERY, *OVERFIT) if g(case, t) is not None]
            assert not applied, f"{task.__name__} on {case.id}: path rules applied: {applied}"
    denied = sum(c.expected["action"] == "denied" for c in cases)
    print(f"ok: oracle passes all {len(cases)} cases; null passes action on exactly the {denied} 'denied' cases")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "cases/full.jsonl")))
