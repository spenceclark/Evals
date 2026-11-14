"""Intervals for pass rates. The case, not the trial, is the unit of evidence: trials of one case are correlated.

wilson():            pooled trials, ignores clustering. Never collapses to [1, 1]: the safer choice for few cases.
cluster_bootstrap(): resamples cases, then trials within each. Honest about clustering, but with few cases it collapses
                     (3 cases that all pass 5/5 give [1, 1]), so don't publish it below ~10 cases.
"""

import math
import random
from collections.abc import Sequence


def wilson(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for passed/n trials, treated as independent."""
    if n == 0:
        return 0.0, 1.0
    p = passed / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def cluster_bootstrap(cases: Sequence[Sequence[bool]], reps: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """(mean, lo, hi) of the pass rate, averaging per case first, then over cases; 95% percentile interval."""
    rng = random.Random(seed)
    groups = [list(g) for g in cases if g]

    def rate(sample: Sequence[Sequence[bool]]) -> float:
        return sum(sum(g) / len(g) for g in sample) / len(sample)

    stats = sorted(rate([[rng.choice(g) for _ in g] for g in rng.choices(groups, k=len(groups))]) for _ in range(reps))
    return rate(groups), stats[int(0.025 * reps)], stats[int(0.975 * reps) - 1]


def kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    """Cohen's kappa for two raters' pass/fail verdicts: agreement corrected for what base rates give by chance."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


if __name__ == "__main__":
    assert kappa([True, False] * 5, [True, False] * 5) == 1.0
    assert abs(kappa([True] * 9 + [False], [True] * 10)) < 1e-9  # 90% raw agreement, zero beyond chance
    lo, hi = wilson(15, 15)
    assert 0.79 < lo < 0.81 and hi == 1.0, (lo, hi)  # 15/15 still leaves room down to ~80%
    assert cluster_bootstrap([[True] * 5] * 3) == (1.0, 1.0, 1.0)  # the collapse the docstring warns about
    m, lo, hi = cluster_bootstrap([[True] * 5, [False] * 5, [True, False] * 3])
    assert lo < m < hi, (m, lo, hi)
    print("ok: stats")
