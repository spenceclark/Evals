"""Promptfoo test generator: the P9 slice, read from tools/p9/slice.jsonl, as Promptfoo test cases.

Promptfoo reads a .jsonl tests file as test cases already in its own shape ({description, vars, assert, ...}); a slice
row ({id, input, expected, ...}) is not one, so it is mapped here.
"""

import json
from pathlib import Path
from typing import Any

from supportdesk.judges import V2

SLICE = Path(__file__).resolve().parents[1] / "p9" / "slice.jsonl"


def generate_tests(config: Any = None) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in SLICE.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [
        {
            "description": r["id"],
            "vars": {
                "email": r["input"],
                "case_id": r["id"],
                "faults": r.get("faults", {}),
                "expected": r["expected"],
                "criteria": V2.criteria,
            },
            "metadata": {"suite": r.get("suite", ""), "tags": ",".join(r.get("tags", []))},
        }
        for r in rows
    ]
