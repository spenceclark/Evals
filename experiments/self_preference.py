"""P5 self-preference (O13): does a judge favour replies written by its own model family?

    uv run python experiments/self_preference.py [--judges sonnet,haiku,opus] [--qwen]

The 50 human-labelled pairs (Haiku 5.5 vs Qwen 9B, same case) are judged in both orders by each judge, with the neutral
pairwise prompt. Per pair, a judge's Haiku share is 1 if Haiku's reply wins both orders, 0.5 for a split or a tie, 0 if
Qwen's wins. Self-preference = the judge's Haiku share minus the human's on the same pairs (for the Qwen judge, the
other way round). With 50 pairs the interval is wide (roughly +/-15 points): report it, don't round it away.

Haiku's replies are longer in 45 of the 50 pairs and the invented timelines are all Qwen's, so an excess for Haiku can
also be length bias or quality, not family. The Qwen judge is what separates them: self-preference shows up as an
interaction (each family's judge leaning to its own family), not as one judge's lean. Mitigation: a cross-family panel
(the mean of a Claude judge and the Qwen judge).

--qwen adds the local judge: about 100 calls at ~45 s each on the local GPU. Run it only when the GPU is free.
Pilot it first with --judges "" --qwen --limit 5 to check prompt size and truncation.
"""

import argparse
import asyncio
import json

from evalkit.judge import Asked, JudgeModel
from supportdesk.bias import (
    FAMILY,
    JUDGES,
    OUT,
    Model,
    Winner,
    bootstrap_mean,
    human,
    human_pair_ids,
    human_repeats,
    judge_pair,
    make_judge,  # fmt: skip
    pool,
    score,
    usd,
)


def kappa3(a: list[str], b: list[str]) -> float:
    """Cohen's kappa over any categories (here haiku / qwen / tie): agreement beyond what the base rates give."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    chance = sum(a.count(k) * b.count(k) for k in set(a) | set(b)) / n / n
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


def winner(share: float) -> str:
    return "haiku" if share == 1 else "qwen" if share == 0 else "tie"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judges", default="sonnet,haiku,opus")
    ap.add_argument("--qwen", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="pilot: first N pairs only (cached)")
    a = ap.parse_args()
    names = [*filter(None, a.judges.split(",")), *(["qwen"] if a.qwen else [])]
    judge = make_judge("p5-self-preference")
    pairs = pool()
    ids = human_pair_ids()[: a.limit or None]
    h = human()

    shares: dict[str, dict[str, float]] = {}
    raw: dict[str, dict[str, list[str | None]]] = {}
    spent: list[Asked] = []
    for name in names:
        jm = JUDGES[name]
        truncated: list[str] = []

        async def ask(i: str, first: Model, jm: JudgeModel = jm, truncated: list[str] = truncated) -> Winner | None:
            w, x = await judge_pair(judge, jm, pairs[i], first)
            if x.status == "truncated":  # cached under its sample index, so one retry with a fresh sample
                truncated.append(f"{i}/{first}-first")
                spent.append(x)
                w, x = await judge_pair(judge, jm, pairs[i], first, sample=1)
            spent.append(x)
            return w

        res = await asyncio.gather(*(asyncio.gather(ask(i, "haiku"), ask(i, "qwen")) for i in ids))
        got: dict[str, float] = {}
        raw[name] = {i: [w1, w2] for i, (w1, w2) in zip(ids, res, strict=True)}
        for i, (w1, w2) in zip(ids, res, strict=True):
            s1, s2 = score(w1, "haiku"), score(w2, "haiku")
            if s1 is not None and s2 is not None:
                got[i] = (s1 + s2) / 2
        shares[name] = got
        m, lo, hi = bootstrap_mean(list(got.values()))
        consistent = sum(w1 == w2 for w1, w2 in res if w1 is not None and w2 is not None)
        print(f"{name:7} ({FAMILY[name]}): verdicts on {len(got)}/{len(ids)} pairs | "
              f"Haiku share {m:.2f} [{lo:.2f}-{hi:.2f}] | same winner both orders {consistent}/{len(got)} | "
              f"truncated then retried {len(truncated)} {truncated if truncated else ''}")  # fmt: skip

    (OUT / "self-preference.json").write_text(json.dumps(shares), encoding="utf-8")
    (OUT / "self-preference-raw.json").write_text(json.dumps(raw), encoding="utf-8")
    if not h:
        print("\n(no human preferences yet: self-preference needs cases/pairs/human.jsonl)")
        print(f"calls {len(spent)} | ${usd(spent):.2f}")
        return

    hs = {i: 1.0 if w == "haiku" else 0.5 if w == "tie" else 0.0 for i, w in h.items()}
    hm, hlo, hhi = bootstrap_mean(list(hs.values()))
    ties = sum(w == "tie" for w in h.values())
    print(f"\nhuman: {len(hs)} pairs | Haiku share {hm:.2f} [{hlo:.2f}-{hhi:.2f}] | ties {ties}")
    reps = human_repeats()
    print(f"human, swapped repeats: same answer on {sum(x == y for x, y in reps)}/{len(reps)}")
    print(f"baseline: always picking Haiku matches the human on {sum(w == 'haiku' for w in h.values())}/{len(h)}")

    # A sign test on disagreements is not valid here: with the human at Haiku on most pairs, a judge can only differ
    # toward Qwen there. So disagreement is split by what the human picked, and agreement is reported as kappa.
    print("\nper judge, against the human on the pairs it answered (both orders):")
    for name, got in shares.items():
        common = [i for i in ids if i in got and i in hs]
        jw, hw = [winner(got[i]) for i in common], [h[i] for i in common]
        not_h = [i for i in common if h[i] != "haiku"]
        print(f"  {name:7} agrees {sum(x == y for x, y in zip(jw, hw, strict=True))}/{len(common)}, "
              f"kappa {kappa3(jw, hw):+.2f} | differs where the human picked Haiku "
              f"{sum(winner(got[i]) != 'haiku' for i in common if h[i] == 'haiku')}/{len(common) - len(not_h)}, "
              f"elsewhere {sum(winner(got[i]) != h[i] for i in not_h)}/{len(not_h)}")  # fmt: skip

    def excess(got: dict[str, float], only: set[str] | None = None) -> str:
        common = [i for i in got if i in hs and (only is None or i in only)]
        m, lo, hi = bootstrap_mean([got[i] - hs[i] for i in common])
        return f"{m:+.2f} [{lo:+.2f} to {hi:+.2f}] on {len(common)}"

    timeline = {i for i in ids if pairs[i]["qwen"]["timeline"] or pairs[i]["haiku"]["timeline"]}
    print("\nexcess Haiku share over the human (positive = leans to Haiku more than the human does):")
    print(f"  (the human's Haiku share caps a judge's possible excess at {1 - hm:+.2f}; the {len(timeline)} pairs "
          "where one reply invents a timeline are the clearest, so the rest are shown too)")  # fmt: skip
    for name, got in shares.items():
        own = "own family" if FAMILY[name] == "claude" else "other family"
        print(f"  {name:7} all: {excess(got)} | without timeline pairs: {excess(got, set(ids) - timeline)} "
              f"(Haiku is its {own})")  # fmt: skip

    # self-preference is an interaction: each family's judge leaning to its own family's replies. Compare each Claude
    # judge with the Qwen judge on the same pairs; length or quality would push both judges the same way.
    claude = [n for n in shares if FAMILY[n] == "claude"]
    if "qwen" in shares and claude:
        q = shares["qwen"]
        print(f"\nClaude judge minus Qwen judge, Haiku share on the {len(q)} pairs Qwen answered "
              "(self-preference in both families makes this positive):")  # fmt: skip
        for c in claude:
            common = [i for i in q if i in shares[c]]
            m, lo, hi = bootstrap_mean([shares[c][i] - q[i] for i in common])
            print(f"  {c:7} - qwen: {m:+.2f} [{lo:+.2f} to {hi:+.2f}]")
        print("panel (mean of a Claude judge and the Qwen judge), excess over the human:")
        for c in claude:
            panel = {i: (shares[c][i] + q[i]) / 2 for i in shares[c] if i in q}
            print(f"  {c}+qwen: {excess(panel)}")
    print(f"calls {len(spent)} | ${usd(spent):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
