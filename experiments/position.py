"""P5 position bias (O11): does the judge's verdict depend on which reply it sees first?

    uv run python experiments/position.py [--judge sonnet] [--limit N]

Every pool pair is judged three times with the neutral pairwise prompt: Haiku's reply first, Qwen's first, and Haiku's
first again (a same-order resample, the noise floor). An order flip only counts as position bias to the extent that it
exceeds the same-order flip rate and favours one slot. Mitigation: judge both orders and call a disagreement a tie;
checked against the human preferences once cases/pairs/human.jsonl exists.
"""

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass

from evalkit.stats import cluster_bootstrap, wilson
from supportdesk.bias import JUDGES, OUT, Winner, human, judge_pair, make_judge, pool, usd


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    case_id: str
    hq: Winner | None  # Haiku's reply in slot A
    qh: Winner | None  # Qwen's reply in slot A
    hq2: Winner | None  # Haiku in slot A again: the same-order resample


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} [{lo:.0%}-{hi:.0%}]" if n else "n/a"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", choices=list(JUDGES), default="sonnet")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    pairs = list(pool().values())[: a.limit]
    judge, jm = make_judge("p5-position"), JUDGES[a.judge]

    hq, qh, hq2 = await asyncio.gather(
        asyncio.gather(*(judge_pair(judge, jm, p, "haiku", 0) for p in pairs)),
        asyncio.gather(*(judge_pair(judge, jm, p, "qwen", 0) for p in pairs)),
        asyncio.gather(*(judge_pair(judge, jm, p, "haiku", 1) for p in pairs)),
    )
    rows = [Row(p["id"], p["case_id"], x[0], y[0], z[0]) for p, x, y, z in zip(pairs, hq, qh, hq2, strict=True)]
    (OUT / f"position-{a.judge}.jsonl").write_text("".join(json.dumps(asdict(r)) + "\n" for r in rows), "utf-8")

    ok = [r for r in rows if None not in (r.hq, r.qh, r.hq2)]
    print(f"position bias, judge {jm.model}, neutral prompt: {len(rows)} pairs, {len(ok)} with all three verdicts")

    def by_case(other: Callable[[Row], Winner | None]) -> list[list[bool]]:
        groups: dict[str, list[bool]] = {}
        for r in ok:
            groups.setdefault(r.case_id, []).append(r.hq == other(r))
        return list(groups.values())

    resample: Callable[[Row], Winner | None] = lambda r: r.hq2  # noqa: E731
    swapped: Callable[[Row], Winner | None] = lambda r: r.qh  # noqa: E731
    for label, other in (("same order, asked twice", resample), ("orders swapped", swapped)):
        same = sum(r.hq == other(r) for r in ok)
        m, lo, hi = cluster_bootstrap(by_case(other))
        print(f"  consistent, {label:24} {pct(same, len(ok))} | by case {m:.0%} [{lo:.0%}-{hi:.0%}]")

    # which slot wins? across every decisive verdict, and among the order-inconsistent pairs only
    first_wins = sum((r.hq == "haiku") + (r.qh == "qwen") for r in ok)
    decisive = sum((r.hq != "tie") + (r.qh != "tie") for r in ok)
    print(f"  slot A wins, all decisive verdicts: {pct(first_wins, decisive)}")
    flips = [r for r in ok if r.hq != r.qh]
    # count by pair, not by verdict: a pair whose winner follows the slot in both orders is one data point
    a_both = sum(r.hq == "haiku" and r.qh == "qwen" for r in flips)
    b_both = sum(r.hq == "qwen" and r.qh == "haiku" for r in flips)
    rest = len(flips) - a_both - b_both
    print(f"  among the {len(flips)} order-inconsistent pairs: {a_both + b_both} decisive flips, slot A won both "
          f"orders in {pct(a_both, a_both + b_both)}; the other {rest} tie in one order")  # fmt: skip
    print(f"  same-order resample disagrees on {sum(r.hq != r.hq2 for r in ok)} pairs (the noise floor)")
    print(f"  ties: {Counter(v for r in ok for v in (r.hq, r.qh))['tie']} of {2 * len(ok)} verdicts")

    # mitigation: both orders, disagreement = tie; compared with the human where answers exist
    h = human()
    if h:

        def both(r: Row) -> Winner | None:
            return r.hq if r.hq == r.qh else "tie"

        rs = [r for r in ok if r.id in h]
        picks: list[tuple[str, Callable[[Row], Winner | None]]] = [
            ("Haiku-first only", lambda r: r.hq),
            ("Qwen-first only", lambda r: r.qh),
            ("both orders, flip=tie", both),
        ]
        for label, pick in picks:
            same = sum(pick(r) == h[r.id] for r in rs)
            opposite = sum({pick(r), h[r.id]} == {"haiku", "qwen"} for r in rs)
            dec = [r for r in rs if pick(r) != "tie"]
            print(f"  vs human ({len(rs)} pairs), {label:24} same answer {pct(same, len(rs))} | "
                  f"opposite pick {pct(opposite, len(rs))} | right among its decisive picks "
                  f"{pct(sum(pick(r) == h[r.id] for r in dec), len(dec))}")  # fmt: skip
        always = sum(w == "haiku" for w in h.values())
        print(f"  (baseline: always picking Haiku matches the human on {always}/{len(h)})")
    else:
        print("  (no human preferences yet: the mitigation check runs once cases/pairs/human.jsonl exists)")
    print(f"  calls {3 * len(pairs)} | ${usd([x[1] for x in (*hq, *qh, *hq2)]):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
