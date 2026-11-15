"""P5 length bias (O12): does padding a reply with content-free text change the judge's verdict?

    uv run python experiments/length.py [--judge sonnet]

Replies come from the P4 human labels. Two pads, each adding no information:
- polite: courtesy sentences before and after the body;
- policy: a correct restatement of the refund policy, before the sign-off.
Both were written to pass rubric v2 on their own: no timeline, no remedy, nothing the policy doesn't say.

Three measurements, each with the neutral prompt and then the pre-registered anti-length instruction:
1. pairwise, a human-passed reply vs the same reply padded, both orders: does the padded copy win more than half?
2. rubric v2 on human-passed replies, padded vs not, against a same-reply resample (the noise floor);
3. the harmful direction: pad the human-FAILED reply. Does it pass the rubric more often, and does it beat the passed
   reply of the same case more often (the 27 label-implied pairs from P4)?
"""

import argparse
import asyncio
import json
import random
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from evalkit.judge import Asked
from supportdesk.bias import JUDGES, OUT, bootstrap_mean, judge_views, make_judge, rows, score, usd
from supportdesk.judges import (
    PAIRWISE_SYSTEM_ANTI_LENGTH,
    PAIRWISE_SYSTEM_NEUTRAL,
    V2,
    V2_ANTI_LENGTH,
    Graded,
    Rubric,
    rubric,  # fmt: skip
    view,
)

type JSON = dict[str, Any]

POLITE_PRE = ("Thank you so much for taking the time to get in touch with us, and for your patience while we looked "
              "into this for you.")  # fmt: skip
POLITE_POST = ("We truly value you as a customer and we're always grateful for the chance to help. Wishing you a "
               "wonderful rest of your day.")  # fmt: skip
POLICY = ("For reference, here is how our refund policy works: refunds are available within 30 days of delivery; "
          "cancelled orders and orders that have already been refunded in full can't be refunded; digital items can't "
          "be refunded once they have been downloaded; and any refund over $200 is passed to a member of our team for "
          "approval.")  # fmt: skip
GREETING = re.compile(r"^(hi|hello|dear|hey)\b[^\n]*\n+", re.IGNORECASE)
SIGNOFF = re.compile(r"\n+((best|kind|warm)?\s*regards|sincerely|thanks|thank you|best wishes|best)\b[^\n]*(\n.*)?$",
                     re.IGNORECASE | re.DOTALL)  # fmt: skip
N_GOOD, SEED = 40, 7


def pad(reply: str, kind: str) -> str:
    g = GREETING.match(reply)
    head, rest = (reply[: g.end()], reply[g.end() :]) if g else ("", reply)
    s = SIGNOFF.search(rest)
    body, tail = (rest[: s.start()], rest[s.start() :]) if s else (rest, "")
    if kind == "polite":
        return f"{head}{POLITE_PRE}\n\n{body.strip()}\n\n{POLITE_POST}{tail}"
    return f"{head}{body.strip()}\n\n{POLICY}{tail}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=list(JUDGES), default="sonnet")
    a = ap.parse_args()
    judge, jm = make_judge("p5-length"), JUDGES[a.judge]
    queue = {q["id"]: q for q in rows(Path("cases/labels/queue.jsonl"))}
    labels = {r["id"]: r["label"] for r in rows(Path("cases/labels/labels.jsonl"))}

    # one human-passed reply per case where possible, seeded
    rng = random.Random(SEED)
    passed = sorted(i for i, lab in labels.items() if lab == "pass")
    rng.shuffle(passed)
    good: list[str] = []
    cases_used: set[str] = set()
    for i in passed:
        if queue[i]["source"]["case_id"] not in cases_used and len(good) < N_GOOD:
            good.append(i)
            cases_used.add(queue[i]["source"]["case_id"])
    bad = sorted(i for i, lab in labels.items() if lab == "fail")
    reply = {i: queue[i]["output"]["reply"] for i in queue}
    spent: list[Asked] = []

    async def grade(i: str, text: str, rb: Rubric, sample: int = 0) -> bool | None:
        g: Graded = await rubric(judge, jm, rb, view(queue[i], text), False, sample)
        spent.extend(g.asked.values())
        return g.passed

    async def gather[T](fs: list[Awaitable[T]]) -> list[T]:
        return list(await asyncio.gather(*fs))

    out: JSON = {"judge": jm.model, "good": good, "bad": bad}
    for prompt_name, system, rb in (("neutral", PAIRWISE_SYSTEM_NEUTRAL, V2),
                                    ("anti-length", PAIRWISE_SYSTEM_ANTI_LENGTH, V2_ANTI_LENGTH)):  # fmt: skip
        print(f"\n=== {prompt_name} prompt, judge {jm.model} ===")
        # 1. pairwise: original vs padded copy of a human-passed reply, both orders
        for kind in ("polite", "policy"):

            async def both(i: str, kind: str = kind, system: str = system) -> float | None:
                o = ("orig", view(queue[i], reply[i]))
                p = ("padded", view(queue[i], pad(reply[i], kind)))
                (w1, x1), (w2, x2) = await asyncio.gather(judge_views(judge, jm, o, p, 0, system),
                                                          judge_views(judge, jm, p, o, 0, system))  # fmt: skip
                spent.extend([x1, x2])
                s1, s2 = score(w1, "padded"), score(w2, "padded")
                return None if s1 is None or s2 is None else (s1 + s2) / 2

            scores = [s for s in await gather([both(i) for i in good]) if s is not None]
            m, lo, hi = bootstrap_mean(scores)
            out[f"pairwise-{prompt_name}-{kind}"] = scores
            print(f"  pairwise, passed reply vs its {kind}-padded copy: padded win share {m:.2f} [{lo:.2f}-{hi:.2f}]"
                  f" (0.50 = no preference) | padded wins both orders {sum(s == 1 for s in scores)}/{len(scores)}, "
                  f"loses both {sum(s == 0 for s in scores)}")  # fmt: skip

        # 2. rubric: passed replies, padded vs not, against a same-reply resample
        orig0 = await gather([grade(i, reply[i], rb, 0) for i in good])
        orig1 = await gather([grade(i, reply[i], rb, 1) for i in good])
        n_ok = sum(x is not None for x in orig0)
        print(f"  rubric on {len(good)} human-passed replies: pass {sum(x is True for x in orig0)}/{n_ok} | "
              f"same reply asked again changes {sum(x != y for x, y in zip(orig0, orig1, strict=True))}")  # fmt: skip
        for kind in ("polite", "policy"):
            padded = await gather([grade(i, pad(reply[i], kind), rb, 0) for i in good])
            ups = sum(x is False and y is True for x, y in zip(orig0, padded, strict=True))
            downs = sum(x is True and y is False for x, y in zip(orig0, padded, strict=True))
            out[f"rubric-good-{prompt_name}-{kind}"] = padded
            print(f"    {kind}-padded: pass {sum(y is True for y in padded)}/{len(good)} | fail->pass {ups}, "
                  f"pass->fail {downs}")  # fmt: skip
        out[f"rubric-good-{prompt_name}-orig"] = [orig0, orig1]

        # 3a. harmful direction, rubric: does padding rescue a human-failed reply?
        b0 = await gather([grade(i, reply[i], rb, 0) for i in bad])
        bp = await gather([grade(i, pad(reply[i], "polite"), rb, 0) for i in bad])
        rescued = sum(x is False and y is True for x, y in zip(b0, bp, strict=True))
        print(f"  rubric on {len(bad)} human-FAILED replies: pass {sum(x is True for x in b0)} unpadded, "
              f"{sum(y is True for y in bp)} polite-padded | rescued by padding {rescued}, "
              f"newly failed {sum(x is True and y is False for x, y in zip(b0, bp, strict=True))}")  # fmt: skip
        out[f"rubric-bad-{prompt_name}"] = [b0, bp]

        # 3b. harmful direction, pairwise: the 27 P4 pairs, with and without padding the failed reply
        by_case: dict[str, dict[str, list[str]]] = {}
        for i, lab in labels.items():
            by_case.setdefault(queue[i]["source"]["case_id"], {"pass": [], "fail": []})[lab].append(i)
        pairs = [(p, f) for d in by_case.values() for p in d["pass"] for f in d["fail"]]
        for padded_fail in (False, True):

            async def prefer_pass(
                p: str, f: str, padded_fail: bool = padded_fail, system: str = system
            ) -> float | None:
                pv = ("pass", view(queue[p], reply[p]))
                fv = ("fail", view(queue[f], pad(reply[f], "polite") if padded_fail else reply[f]))
                (w1, x1), (w2, x2) = await asyncio.gather(judge_views(judge, jm, pv, fv, 0, system),
                                                          judge_views(judge, jm, fv, pv, 0, system))  # fmt: skip
                spent.extend([x1, x2])
                s1, s2 = score(w1, "pass"), score(w2, "pass")
                return None if s1 is None or s2 is None else (s1 + s2) / 2

            ss: list[float] = [s for s in await gather([prefer_pass(p, f) for p, f in pairs]) if s is not None]
            m, lo, hi = bootstrap_mean(ss)
            out[f"pairs-{prompt_name}-{'padded' if padded_fail else 'plain'}"] = ss
            print(f"  {len(pairs)} P4 pairs, failed reply {'polite-padded' if padded_fail else 'as written':14}: "
                  f"passed reply's win share {m:.2f} [{lo:.2f}-{hi:.2f}]")  # fmt: skip

    (OUT / f"length-{a.judge}.json").write_text(json.dumps(out), encoding="utf-8")
    print(f"\ncalls {len(spent)} | ${usd(spent):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
