"""Human pairwise preferences for P5. Run it in your own terminal; it saves after every answer, so you can resume.

    uv run python scripts/prefer.py           # 55 pairs: which reply would you rather the customer got?
    uv run python scripts/prefer.py status    # progress

Answers go to cases/pairs/human.jsonl. Which model wrote which reply is hidden, and A/B is assigned in advance.
"""

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from supportdesk.display import attempt_lines, expected_line, wrap

type JSON = dict[str, Any]

DIR = Path("cases/pairs")
QUEUE, POOL, ANSWERS = DIR / "human_queue.jsonl", DIR / "pool.jsonl", DIR / "human.jsonl"
QUESTION = """Which reply would you rather this customer received, given what actually happened in each attempt?
  Judge each reply against its own attempt (the two attempts may have done different things). Prefer a clear lean over
  a tie: use tie only when you genuinely can't choose."""


def read(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def show(item: JSON, pair: JSON, n: int, total: int) -> None:
    a = pair[item["a"]]
    b = pair["qwen" if item["a"] == "haiku" else "haiku"]
    print("\n" + "=" * 118)
    print(f"[{n}/{total}]  {item['id']}\n\nCUSTOMER EMAIL\n{wrap(pair['input'])}\n")
    print(f"POLICY SAYS   {expected_line(pair['expected'])}")
    for slot, att in (("A", a), ("B", b)):
        print(f"\n---------------- ATTEMPT {slot} " + "-" * 92)
        print("\n".join(attempt_lines(att["messages"], att["outcome"], att["output"]["reply"])))
    print()


def prefer() -> None:
    queue = read(QUEUE)
    pool = {p["id"]: p for p in read(POOL)}
    done = {r["id"] for r in read(ANSWERS)}
    todo = [q for q in queue if q["id"] not in done]
    print(f"{len(done)}/{len(queue)} answered. {QUESTION}\nKeys: a, b, t = tie, s = skip for now, q = quit.")
    for item in todo:
        show(item, pool[item["pair_id"]], len(done) + 1, len(queue))
        while (choice := input("which reply? [a/b/t/s/q] > ").strip().lower()) not in ("a", "b", "t", "s", "q"):
            pass
        if choice == "q":
            break
        if choice == "s":
            continue
        reason = input("reason (optional; worth it when it was close) > ").strip()
        with ANSWERS.open("a", encoding="utf-8") as f:
            row = {"id": item["id"], "choice": {"a": "A", "b": "B", "t": "tie"}[choice], "reason": reason,
                   "at": datetime.now(UTC).isoformat()}  # fmt: skip
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        done.add(item["id"])
    status()


def status() -> None:
    answers = read(ANSWERS)
    ties = sum(r["choice"] == "tie" for r in answers)
    print(f"\nanswered: {len(answers)}/{len(read(QUEUE))} (ties {ties})")


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]
    {"status": status}.get(sys.argv[1] if len(sys.argv) > 1 else "", prefer)()
