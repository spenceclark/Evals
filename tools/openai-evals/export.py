"""P9: oaieval's record file -> p9_results.jsonl (the reference's shape), plus cost and the judge prompts.

    uv run python export.py logs/p9-slice.jsonl

Everything comes from the record file: one `metrics` event per trial (written by p9_eval.P9Slice), the agent's
`sampling` event (usage, cost) and the judge's three `sampling` events (prompt, reply, usage, stop_reason).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evalkit.core import PRICES

type JSON = dict[str, Any]
HERE = Path(__file__).parent


def main() -> None:
    record = Path(sys.argv[1])
    events: dict[str, list[JSON]] = defaultdict(list)
    for line in record.read_text(encoding="utf-8").splitlines():
        if (e := json.loads(line)).get("type"):
            events[e["sample_id"]].append(e)

    rows: list[JSON] = []
    agent_cost = judge_cost = 0.0
    truncated = 0
    for evs in events.values():
        m = next(e["data"] for e in evs if e["type"] == "metrics")
        judge = [e["data"] for e in evs if e["type"] == "sampling" and isinstance(e["data"]["prompt"], list)]
        agent = [e["data"] for e in evs if e["type"] == "sampling" and isinstance(e["data"]["prompt"], str)]
        agent_cost += sum(a["usage"]["cost_usd"] for a in agent)
        p_in, p_out = PRICES["claude-haiku-5-5"]
        judge_cost += sum(j["usage"]["input_tokens"] * p_in + j["usage"]["output_tokens"] * p_out for j in judge) / 1e6
        rubric = m["rubric"]
        if any(j.get("stop_reason") != "end_turn" for j in judge):  # a truncated judge reply has no trustworthy verdict
            truncated += 1
            rubric = None
        rows.append({"tool": "openai-evals", "case_id": m["case_id"], "rep": m["rep"], "action": m["action"],
                     "path": m["path"], "rubric": rubric, "reply": m["reply"]})  # fmt: skip
    rows.sort(key=lambda r: (r["case_id"], r["rep"]))
    out = HERE / "p9_results.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {out.name}: {len(rows)} trials; agent ${agent_cost:.4f}, judge ${judge_cost:.4f}, "
          f"{truncated} truncated judge replies")  # fmt: skip
    for g in ("action", "path", "rubric"):
        vals = [r[g] for r in rows if r[g] is not None]
        print(f"  {g:<7} {sum(vals)}/{len(vals)}")


if __name__ == "__main__":
    main()
