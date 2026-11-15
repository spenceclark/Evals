"""Intervals for pass rates. The case, not the trial, is the unit of evidence: trials of one case are correlated.

wilson():            pooled trials, ignores clustering. Never collapses to [1, 1]: the safer choice for few cases.
cluster_bootstrap(): resamples cases, then trials within each. Honest about clustering, but with few cases it collapses
                     (3 cases that all pass 5/5 give [1, 1]), so don't publish it below ~10 cases.

P6, counting:
pass_at_k() / pass_hat_k(): per-case unbiased estimators from n trials with c passes. pass@k (Chen et al. 2021, eq. 1):
                     at least one of k passes. pass^k (Yao et al. 2024, tau-bench section 3): all k pass. Average them
                     over cases; never plug the pooled pass rate into 1-(1-p)^k or p^k (cases differ, so both miss).
paired_diff():       two variants on the same cases: mean of the per-case differences in pass rate, with Miller 2024's
                     paired SE (eq. 7). Averaging each case's trials first is what makes it clustered by case.
mde():               Miller 2024 eq. 10: the smallest difference detected with 80% power at alpha 0.05.
simulate():          the same question by brute force, for checking mde() and for case/trial grids.
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


def pass_at_k(n: int, c: int, k: int) -> float:
    """P(at least one of k trials passes), unbiased from c passes in n trials (k <= n). Chen et al. 2021, eq. 1."""
    if not 0 < k <= n or not 0 <= c <= n:
        raise ValueError((n, c, k))
    return 1.0 if n - c < k else 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """P(all k trials pass), unbiased from c passes in n trials (k <= n). Yao et al. 2024 (tau-bench), section 3."""
    if not 0 < k <= n or not 0 <= c <= n:
        raise ValueError((n, c, k))
    return math.comb(c, k) / math.comb(n, k)


def over_cases(counts: Sequence[tuple[int, int]], k: int, at: bool) -> tuple[float, int]:
    """(mean over cases of pass@k if `at` else pass^k, number of cases with at least k trials)."""
    f = pass_at_k if at else pass_hat_k
    vals = [f(n, c, k) for n, c in counts if n >= k]
    return sum(vals) / len(vals), len(vals)


Z95, Z80 = 1.959964, 0.841621  # two-sided 95%; one-sided 80% power


def paired_diff(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    """(mean, lo, hi) of b - a over cases, 95% CI = mean +/- 1.96 sd(d)/sqrt(n). a[i], b[i]: case i's pass rate under
    each variant. With every per-case difference 0 the interval is [0, 0]: no evidence of noise is not evidence of
    none, so report the case count alongside."""
    d = [y - x for x, y in zip(a, b, strict=True)]
    n = len(d)
    m = sum(d) / n
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n) if n > 1 else math.inf
    return m, m - Z95 * se, m + Z95 * se


def mde(omega2: float, sigma2_a: float, sigma2_b: float, cases: int, trials: int) -> float:
    """Miller 2024 eq. 10. omega2: variance over cases of the true per-case difference (does not shrink with trials);
    sigma2_*: mean within-case variance p(1-p) of each variant (shrinks with trials)."""
    return (Z95 + Z80) * math.sqrt((omega2 + sigma2_a / trials + sigma2_b / trials) / cases)


def simulate(pa: Sequence[float], pb: Sequence[float], cases: int, trials: int, reps: int = 2000,
             seed: int = 0) -> tuple[float, float]:  # fmt: skip
    """Draw `cases` cases (with replacement) from the true per-case rates (pa[i], pb[i]), run `trials` trials of each
    variant, and form paired_diff. Returns (mean CI half-width, share of reps whose CI excludes 0)."""
    rng = random.Random(seed)
    idx = range(len(pa))
    half, hits = 0.0, 0
    for _ in range(reps):
        pick = rng.choices(idx, k=cases)
        ra = [rng.binomialvariate(trials, pa[i]) / trials for i in pick]
        rb = [rng.binomialvariate(trials, pb[i]) / trials for i in pick]
        _, lo, hi = paired_diff(ra, rb)
        half += (hi - lo) / 2
        hits += lo > 0 or hi < 0
    return half / reps, hits / reps


if __name__ == "__main__":
    # Unbiased, exactly: the expectation over c ~ Binomial(n, p) equals the true per-case value.
    for p in (0.0, 0.3, 0.9, 1.0):
        for k in (1, 3, 10):
            pmf = [math.comb(10, c) * p**c * (1 - p) ** (10 - c) for c in range(11)]
            assert abs(sum(w * pass_at_k(10, c, k) for c, w in enumerate(pmf)) - (1 - (1 - p) ** k)) < 1e-12
            assert abs(sum(w * pass_hat_k(10, c, k) for c, w in enumerate(pmf)) - p**k) < 1e-12
    assert pass_at_k(10, 10, 1) == pass_hat_k(10, 10, 10) == 1.0 and pass_hat_k(10, 9, 10) == 0.0
    # Simulated cases with known, different p: the case-averaged estimators converge to the means of 1-(1-p)^k and p^k,
    # and the shortcut from the pooled pass rate does not.
    rng = random.Random(1)
    ps = [rng.choice((0.2, 0.6, 0.95, 1.0)) for _ in range(4000)]
    counts = [(10, sum(rng.random() < p for _ in range(10))) for p in ps]
    pooled = sum(c for _, c in counts) / (10 * len(counts))
    for k in (1, 5, 10):
        true_at = sum(1 - (1 - p) ** k for p in ps) / len(ps)
        true_hat = sum(p**k for p in ps) / len(ps)
        est_at, est_hat = over_cases(counts, k, True)[0], over_cases(counts, k, False)[0]
        assert abs(est_at - true_at) < 0.01 and abs(est_hat - true_hat) < 0.01, (k, est_at, true_at, est_hat, true_hat)
        if k > 1:
            assert abs((1 - (1 - pooled) ** k) - true_at) > 0.02 and abs(pooled**k - true_hat) > 0.1, k
    # paired_diff: an A/A comparison of identical data is exactly 0; a planted gap is found.
    assert paired_diff([0.5, 1.0, 0.2], [0.5, 1.0, 0.2]) == (0.0, 0.0, 0.0)
    m, lo, hi = paired_diff([1.0] * 30, [1.0] * 20 + [0.0] * 10)
    assert hi < 0 and abs(m + 1 / 3) < 1e-12, (m, lo, hi)
    # mde() agrees with brute force: at the MDE, power is about 80%; with 0 effect, about 5% false positives.
    pa = [0.5] * 50
    delta = mde(0.0, 0.25, 0.25, cases=50, trials=5)
    _, power = simulate(pa, [0.5 - delta] * 50, cases=50, trials=5)
    _, false_pos = simulate(pa, pa, cases=50, trials=5)
    assert 0.74 < power < 0.86 and false_pos < 0.08, (delta, power, false_pos)
    assert kappa([True, False] * 5, [True, False] * 5) == 1.0
    assert abs(kappa([True] * 9 + [False], [True] * 10)) < 1e-9  # 90% raw agreement, zero beyond chance
    lo, hi = wilson(15, 15)
    assert 0.79 < lo < 0.81 and hi == 1.0, (lo, hi)  # 15/15 still leaves room down to ~80%
    assert cluster_bootstrap([[True] * 5] * 3) == (1.0, 1.0, 1.0)  # the collapse the docstring warns about
    m, lo, hi = cluster_bootstrap([[True] * 5, [False] * 5, [True, False] * 3])
    assert lo < m < hi, (m, lo, hi)
    print("ok: stats; estimators unbiased, paired difference, power")
