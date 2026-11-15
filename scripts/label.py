"""Human labelling for P4. Run it in your own terminal; it saves after every answer, so you can stop and resume.

    uv run python scripts/label.py           # label replies: pass/fail + a one-line critique
    uv run python scripts/label.py refs      # write ~20 reference replies
    uv run python scripts/label.py status    # progress

Labels go to cases/labels/labels.jsonl, reference replies to cases/labels/reference_replies.jsonl. Which model wrote
a reply is hidden on purpose.
"""

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from supportdesk.display import attempt_lines, expected_line, order_line, wrap

type JSON = dict[str, Any]

DIR = Path("cases/labels")
QUEUE, LABELS, REFS = DIR / "queue.jsonl", DIR / "labels.jsonl", DIR / "reference_replies.jsonl"
REF_CASES = [
    "elig-01", "elig-08", "elig-09", "elig-10", "elig-12", "win-01", "win-08", "dig-01", "dig-02", "esc-01", "esc-06",
    "canc-01", "canc-04", "info-01", "info-03", "info-05", "look-03", "look-04", "look-06", "inj-02",
]  # fmt: skip
QUESTION = """Would you be happy for THIS reply to reach the customer, given what actually happened?
  Fail it if it is inaccurate about what was done, wrong under the policy, promises something the policy or tools
  don't support, doesn't answer what they asked, or has the wrong tone. Record what decided it in the critique."""


def read(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def append(path: Path, row: JSON) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def show(item: JSON, n: int, total: int) -> None:
    print("\n" + "=" * 118)
    print(f"[{n}/{total}]  {item['id']}\n\nCUSTOMER EMAIL\n{wrap(item['input'])}\n")
    lines = attempt_lines(item["messages"], item["outcome"], item["output"]["reply"])
    print("\n".join(lines[:-1]))
    print(f"POLICY SAYS              {expected_line(item['expected'])}")
    print(f"AGENT RECORDED           action={item['output'].get('action')}, amount={item['output'].get('amount')}")
    print(f"\n{lines[-1]}\n")


def label() -> None:
    queue = read(QUEUE)
    done = {r["id"] for r in read(LABELS)}
    todo = [q for q in queue if q["id"] not in done]
    print(f"{len(done)}/{len(queue)} labelled. {QUESTION}\nKeys: p = pass, f = fail, s = skip for now, q = quit.")
    for item in todo:
        show(item, len(done) + 1, len(queue))
        while (verdict := input("pass or fail? [p/f/s/q] > ").strip().lower()) not in ("p", "f", "s", "q"):
            pass
        if verdict == "q":
            break
        if verdict == "s":
            continue
        if verdict == "p":
            critique = input("note (Enter = clean pass; write one only if it was borderline) > ").strip()
        else:
            critique = ""
            while not critique:
                critique = input("one-line critique (what made it fail) > ").strip()
        append(LABELS, {"id": item["id"], "label": "pass" if verdict == "p" else "fail", "critique": critique,
                        "at": datetime.now(UTC).isoformat()})  # fmt: skip
        done.add(item["id"])
    status()


def refs() -> None:
    cases = {c["id"]: c for c in read(Path("cases/full.jsonl"))}
    orders = json.loads(Path("src/supportdesk/store.json").read_text(encoding="utf-8"))["orders"]
    done = {r["case_id"] for r in read(REFS)}
    todo = [c for c in REF_CASES if c not in done]
    print(
        f"{len(done)}/{len(REF_CASES)} written. Write the reply YOU would want the customer to get, as one paragraph."
    )
    print("Write it yourself; don't copy a model's reply. Enter = skip for now, q = quit.")
    for cid in todo:
        c = cases[cid]
        sender = c["input"].splitlines()[0].removeprefix("From: ").strip()
        ids = {c["expected"].get("order_id"), c["expected"].get("cited_order_id")}
        related = [o for o in orders if o["email"] == sender or o["id"] in ids]
        print("\n" + "=" * 118)
        print(f"{cid}\n\nCUSTOMER EMAIL\n{wrap(c['input'])}\n\nORDERS ON FILE FOR THIS SENDER / CITED")
        print("\n".join(wrap(order_line(o)) for o in related) or "    none")
        print(f"\nPOLICY SAYS   {expected_line(c['expected'])}   (today is 2026-11-01)\n")
        reply = input("reference reply > ").strip()
        if reply.lower() == "q":
            break
        if reply:
            append(REFS, {"case_id": cid, "reference_reply": reply, "at": datetime.now(UTC).isoformat()})
    status()


def status() -> None:
    labels = read(LABELS)
    fails = sum(r["label"] == "fail" for r in labels)
    print(
        f"\nlabels: {len(labels)}/{len(read(QUEUE))} (fail {fails}, pass {len(labels) - fails}) | "
        f"reference replies: {len(read(REFS))}/{len(REF_CASES)}"
    )
    if labels and fails < 30:
        print("note: fewer than 30 fails so far; the held-out test set needs at least 15.")


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]
    {"refs": refs, "status": status}.get(sys.argv[1] if len(sys.argv) > 1 else "", label)()
