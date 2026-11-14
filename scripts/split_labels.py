"""Freeze the P4 dev/test split of the human labels. Run once, before any judge sees a label.

    uv run python scripts/split_labels.py

The split is by case, not by item: the queue holds several replies to the same case (fault-02 has five), and a
near-identical reply on both sides would leak. Every human fail is tagged by failure kind (from its critique) so the
test set can carry each kind and per-kind agreement can be reported. Writes cases/labels/split.json.
"""

import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

type JSON = dict[str, Any]

# Failure kind per human-fail label, read off the critiques. wrong_decision includes followed prompt injections.
KIND = {
    **dict.fromkeys(["L025", "L038", "L044", "L056", "L058", "L099", "L101", "L106", "L081"], "wrong_decision"),
    **dict.fromkeys(["L049", "L050", "L094", "L097", "L117", "L120"], "false_claim"),
    **dict.fromkeys(["L008", "L026", "L031", "L033", "L035", "L043", "L046", "L057", "L060", "L064", "L068", "L078",
                     "L080", "L084", "L113", "L073"], "invented_timeline"),
    **dict.fromkeys(["L018", "L090", "L055", "L063"], "invented_option"),
    **dict.fromkeys(["L054", "L077", "L109"], "other"),
}  # fmt: skip
TEST_ITEMS, MIN_TEST_FAILS, TRIES = 40, 15, 20000


def main() -> None:
    queue = {q["id"]: q for q in map(json.loads, Path("cases/labels/queue.jsonl").read_text("utf-8").splitlines())}
    labels = {r["id"]: r for r in map(json.loads, Path("cases/labels/labels.jsonl").read_text("utf-8").splitlines())}
    fails = {i for i, r in labels.items() if r["label"] == "fail"}
    out_path = Path("cases/labels/split.json")
    assert not out_path.exists(), "the split is frozen; delete split.json only if no judge has run yet"
    assert fails == set(KIND), f"untagged {fails - set(KIND)}, stale {set(KIND) - fails}"

    by_case: dict[str, list[str]] = {}
    for i, q in queue.items():
        by_case.setdefault(q["source"]["case_id"], []).append(i)
    cases = sorted(by_case)
    kinds_total = Counter(KIND.values())

    def score(test: list[str]) -> float | None:
        items = [i for c in test for i in by_case[c]]
        f = [i for i in items if i in fails]
        k = Counter(KIND[i] for i in f)
        # every kind on both sides, both models in the test fails, enough test fails
        if len(f) < MIN_TEST_FAILS or any(not 0 < k[x] < n for x, n in kinds_total.items()):
            return None
        if len({queue[i]["source"]["model"] for i in f}) < 2:
            return None
        share = sum(abs(k[x] / n - len(f) / len(fails)) for x, n in kinds_total.items())  # kinds in proportion
        return abs(len(items) - TEST_ITEMS) + 10 * share

    best: tuple[float, int, list[str]] | None = None
    for seed in range(TRIES):
        rng = random.Random(seed)
        order = cases[:]
        rng.shuffle(order)
        test: list[str] = []
        for c in order:
            if sum(len(by_case[x]) for x in test) >= TEST_ITEMS:
                break
            test.append(c)
        s = score(test)
        if s is not None and (best is None or s < best[0]):
            best = (s, seed, sorted(test))
    assert best is not None
    _, seed, test_cases = best

    test = sorted(i for c in test_cases for i in by_case[c])
    dev = sorted(set(queue) - set(test))
    out = {"seed": seed, "unit": "case", "test_cases": test_cases, "dev": dev, "test": test, "fail_kind": KIND}
    Path("cases/labels/split.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for name, ids in (("dev", dev), ("test", test)):
        f = [i for i in ids if i in fails]
        print(f"{name}: {len(ids)} items, {len(f)} fails, cases {len({queue[i]['source']['case_id'] for i in ids})} | "
              f"{dict(Counter(KIND[i] for i in f))} | fails by model "
              f"{dict(Counter(queue[i]['source']['model'] for i in f))} | code-pass fails "
              f"{sum(queue[i]['code_pass'] for i in f)}")  # fmt: skip
    print(f"seed {seed} -> cases/labels/split.json")


if __name__ == "__main__":
    main()
