"""Promptfoo's output file (-o out/eval.json) -> p9_results.jsonl, the shape of tools/p9/reference.jsonl.

    .venv/Scripts/python.exe p9_export.py out/eval.json p9_results.jsonl

Every value comes from what Promptfoo recorded: the per-assertion results under gradingResult.componentResults, keyed
by the `metric` each assertion was given. A judge call that produced no verdict (API error, unparsable answer) is a
fail to Promptfoo, marked only by metadata.graderError; here it is None, as in the reference.
"""

import json
import sys
from pathlib import Path
from typing import Any

type JSON = dict[str, Any]
CRITERIA = ("rubric.accurate", "rubric.no_outside_promises", "rubric.tone")


def components(grading: JSON) -> dict[str, JSON]:
    """metric -> result. Promptfoo lists an assert-set's children twice (nested and flattened); keep one."""
    out: dict[str, JSON] = {}
    stack: list[JSON] = list(grading.get("componentResults") or [])
    while stack:
        c = stack.pop()
        stack += c.get("componentResults") or []
        assertion: JSON = c.get("assertion") or {}
        metric: str | None = assertion.get("metric")
        if metric:
            out.setdefault(metric, c)
    return out


def verdict(c: JSON | None) -> bool | None:
    meta: JSON = (c or {}).get("metadata") or {}
    if c is None or meta.get("graderError"):
        return None
    return bool(c["pass"])


def row(r: JSON) -> JSON:
    trial = r["response"]["metadata"]["trial"]
    reply = trial["output"].get("reply")
    cs = components(r["gradingResult"])
    crit = [verdict(cs.get(m)) for m in CRITERIA]
    rubric = None if not reply or None in crit else all(crit)
    return {"tool": "promptfoo", "case_id": r["testCase"]["description"], "rep": r["repeatIndex"],
            "action": verdict(cs.get("action")), "path": verdict(cs.get("path")), "rubric": rubric,
            "reply": reply}  # fmt: skip


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    results: list[JSON] = json.loads(src.read_text(encoding="utf-8"))["results"]["results"]
    rows = sorted((row(r) for r in results), key=lambda o: (o["case_id"], o["rep"]))
    dst.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in rows), encoding="utf-8")
    print(f"wrote {dst}: {len(rows)} trials")
    for g in ("action", "path", "rubric"):
        vals = [o[g] for o in rows if o[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)} pass ({len(rows) - len(vals)} no verdict)")


if __name__ == "__main__":
    main()
