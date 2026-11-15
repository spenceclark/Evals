"""P6 counting: pass@k vs pass^k, A/A, the planted regression, detection by cases x trials, and power.

    uv run python experiments/counting.py curves RUN [RUN ...]     # O14: pass@k and pass^k for k = 1..10
    uv run python experiments/counting.py compare RUN_A RUN_B      # O15 (A/A), the planted variant, Haiku vs Qwen
    uv run python experiments/counting.py detect RUN_A RUN_B       # O16: subsample the saved trials
    uv run python experiments/counting.py power RUN_A [RUN_B]      # O17: CI half-width and MDE vs (cases, trials)

RUN is a run directory from `evalkit run --graders supportdesk.path:ALL`, or its parent if that holds exactly one run
(runs/p6/a1). Pass = the trial finished and every applicable P2 + P3 grader passed: no judge. A truncated or refused
trial is a fail (the customer got no reply). An infra error (errors.jsonl) is not a trial at all, so a case can have
fewer than 10. --outcome counts only the P2 outcome graders (what the customer and the store end up with), ignoring the
P3 path rules.
"""

import argparse
import json
import random
from collections import Counter
from pathlib import Path

from evalkit.stats import cluster_bootstrap, mde, over_cases, paired_diff, simulate
from supportdesk.graders import GRADERS

type Runs = dict[str, list[bool]]
OUTCOME = {g.__name__ for g in GRADERS}
only_graders: set[str] | None = None  # set by --outcome


def resolve(path: Path) -> Path:
    if (path / "meta.json").exists():
        return path
    subs = [p for p in path.iterdir() if (p / "meta.json").exists()]
    if len(subs) != 1:
        raise SystemExit(f"{path}: expected one run inside, found {len(subs)}")
    return subs[0]


def load(path: Path) -> tuple[str, Runs, int]:
    """(label, case id -> pass per trial, infra errors)."""
    d = resolve(path)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    out: Runs = {}
    for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            scores = [s for s in r["scores"] if only_graders is None or s["grader"] in only_graders]
            ok = r["trial"]["status"] == "ok" and bool(scores) and all(s["passed"] for s in scores)
            out.setdefault(r["case_id"], []).append(ok)
    err = d / "errors.jsonl"
    errors = len(err.read_text(encoding="utf-8").splitlines()) if err.exists() else 0
    return f"{path.name}:{meta['task'].rpartition(':')[2]}", dict(sorted(out.items())), errors


def rates(runs: Runs, ids: list[str]) -> list[float]:
    return [sum(runs[i]) / len(runs[i]) for i in ids]


def describe(label: str, runs: Runs, errors: int) -> None:
    n = [len(v) for v in runs.values()]
    shape = Counter("all pass" if all(v) else "all fail" if not any(v) else "mixed" for v in runs.values())
    m, lo, hi = cluster_bootstrap(list(runs.values()))
    print(f"{label}: {len(runs)} cases, {sum(n)} trials ({min(n)}-{max(n)} per case), infra errors {errors} | "
          f"pass rate {m:.3f} [{lo:.3f}-{hi:.3f}] (cluster bootstrap) | cases: {dict(shape)}")  # fmt: skip


def curves(paths: list[Path]) -> None:
    series: list[tuple[str, list[float], list[float]]] = []
    for p in paths:
        label, runs, errors = load(p)
        describe(label, runs, errors)
        counts = [(len(v), sum(v)) for v in runs.values()]
        kmax = max(n for n, _ in counts)
        at = [over_cases(counts, k, True)[0] for k in range(1, kmax + 1)]
        hat = [over_cases(counts, k, False)[0] for k in range(1, kmax + 1)]
        series.append((label, at, hat))
        print(f"  {'k':>3} {'pass@k':>8} {'pass^k':>8} {'gap':>6}")
        for k, (a, h) in enumerate(zip(at, hat, strict=True), 1):
            print(f"  {k:>3} {a:>8.3f} {h:>8.3f} {a - h:>6.3f}")
    kmax = max(len(s[1]) for s in series)
    print('\n```mermaid\nxychart-beta\n    title "pass@k (upper) and pass^k (lower) vs k"')
    print(f'    x-axis "k (trials)" [{", ".join(str(k) for k in range(1, kmax + 1))}]')
    print('    y-axis "share of cases" 0 --> 1')
    for _, at, hat in series:
        print(f"    line [{', '.join(f'{x:.3f}' for x in at)}]\n    line [{', '.join(f'{x:.3f}' for x in hat)}]")
    print("```\nseries in order: " + ", ".join(f"{s[0]} pass@k, {s[0]} pass^k" for s in series))


def compare(pa: Path, pb: Path) -> None:
    la, a, ea = load(pa)
    lb, b, eb = load(pb)
    describe(la, a, ea)
    describe(lb, b, eb)
    ids = sorted(a.keys() & b.keys())
    ra, rb = rates(a, ids), rates(b, ids)
    m, lo, hi = paired_diff(ra, rb)
    differ = [(i, x, y) for i, x, y in zip(ids, ra, rb, strict=True) if x != y]
    print(f"\n{lb} - {la}, paired by case on {len(ids)} cases: {m:+.3f} [{lo:+.3f} to {hi:+.3f}] (Miller eq. 7)")
    # For contrast: the same difference treating every trial as independent (no clustering, no pairing).
    na, nb = sum(len(a[i]) for i in ids), sum(len(b[i]) for i in ids)
    qa, qb = sum(sum(a[i]) for i in ids) / na, sum(sum(b[i]) for i in ids) / nb
    se = (qa * (1 - qa) / na + qb * (1 - qb) / nb) ** 0.5
    print(f"  trials as independent: {qb - qa:+.3f} [{qb - qa - 1.96 * se:+.3f} to {qb - qa + 1.96 * se:+.3f}]"
          f" on {na} + {nb} trials (wrong: trials of one case are not independent)")  # fmt: skip
    print(f"  cases whose pass rate differs: {len(differ)}")
    for i, x, y in differ:
        print(f"    {i:<10} {x:.1f} -> {y:.1f}")


def detect(pa: Path, pb: Path, reps: int = 1000) -> None:
    """Had we written only m of the cases and run t trials of each: how often does the paired CI exclude 0?"""
    la, a, _ = load(pa)
    lb, b, _ = load(pb)
    ids = sorted(a.keys() & b.keys())
    full, _, _ = paired_diff(rates(a, ids), rates(b, ids))
    rng = random.Random(0)
    tmax = min(min(len(a[i]), len(b[i])) for i in ids)
    ms = [m for m in (5, 10, 20, 33, 50) if m < len(ids)] + [len(ids)]
    ts = [t for t in (1, 2, 3, 5) if t < tmax] + [tmax]
    print(f"{lb} - {la}: full-data difference {full:+.3f}. Share of {reps} subsamples whose 95% CI excludes 0"
          " (in brackets: excludes 0 on the wrong side). Cases and trials drawn without replacement.")  # fmt: skip
    print(f"{'cases':>6} " + "".join(f"{f't={t}':>14}" for t in ts))
    for m in ms:
        row = f"{m:>6} "
        for t in ts:
            hit = wrong = 0
            for _ in range(reps):
                pick = rng.sample(ids, m)
                xa = [sum(rng.sample(a[i], t)) / t for i in pick]
                xb = [sum(rng.sample(b[i], t)) / t for i in pick]
                _, lo, hi = paired_diff(xa, xb)
                excl = lo > 0 or hi < 0
                hit += excl
                wrong += excl and (lo > 0) != (full > 0)
            row += f"{hit / reps:>9.0%} ({wrong / reps:.0%})"
        print(row)


def power(pa: Path, pb: Path | None) -> None:
    """Per-case true rates are plug-ins from the observed ones (a case at 10/10 might really be 0.95: say so)."""
    la, a, _ = load(pa)
    ids = sorted(a)
    ra = rates(a, ids)
    s2a = sum(p * (1 - p) for p in ra) / len(ra)
    cases, trials = (20, 40, 66, 100, 200, 400), (1, 3, 5, 10, 20)
    print(f"{la}: mean within-case variance p(1-p) = {s2a:.4f} over {len(ids)} cases; "
          f"{sum(0 < p < 1 for p in ra)} cases are neither always-pass nor always-fail")  # fmt: skip
    print("\nA/A noise: mean 95% CI half-width of the paired difference (simulated, 500 reps)")
    print(f"{'cases':>6} " + "".join(f"{f't={t}':>9}" for t in trials))
    for m in cases:
        print(f"{m:>6} " + "".join(f"{simulate(ra, ra, m, t, reps=500)[0]:>9.3f}" for t in trials))
    print("\nMDE, 80% power at alpha 0.05, for a drop spread evenly over cases (Miller eq. 10, omega^2 = 0)")
    print(f"{'cases':>6} " + "".join(f"{f't={t}':>9}" for t in trials))
    for m in cases:
        print(f"{m:>6} " + "".join(f"{mde(0.0, s2a, s2a, m, t):>9.3f}" for t in trials))
    if pb is None:
        return
    lb, b, _ = load(pb)
    ids = sorted(a.keys() & b.keys())
    ra, rb = rates(a, ids), rates(b, ids)
    d = [y - x for x, y in zip(ra, rb, strict=True)]
    md = sum(d) / len(d)
    s2b = sum(p * (1 - p) for p in rb) / len(rb)
    tn = min(len(a[i]) for i in ids)
    # observed per-case differences carry trial noise too: Var(d_obs) = omega^2 + (s2a + s2b) / n
    omega2 = max(0.0, sum((x - md) ** 2 for x in d) / (len(d) - 1) - (s2a + s2b) / (tn - 1))
    print(f"\n{lb} - {la}: difference {md:+.3f}; omega^2 (spread of the true per-case difference) = {omega2:.4f}")
    print("  omega^2 does not shrink with trials: only more cases reduce it.")
    print("MDE with this spread (Miller eq. 10):")
    print(f"{'cases':>6} " + "".join(f"{f't={t}':>9}" for t in trials))
    for m in cases:
        print(f"{m:>6} " + "".join(f"{mde(omega2, s2a, s2b, m, t):>9.3f}" for t in trials))
    print("Simulated power to detect this exact effect (cases drawn from these 66, trials from their rates):")
    print(f"{'cases':>6} " + "".join(f"{f't={t}':>9}" for t in trials))
    for m in (5, 10, 20, 40, 66, 100):
        print(f"{m:>6} " + "".join(f"{simulate(ra, rb, m, t, reps=500)[1]:>9.0%}" for t in trials))


def main() -> None:
    global only_graders
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcome", action="store_true", help="P2 outcome graders only, no path rules")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("curves").add_argument("runs", type=Path, nargs="+")
    for name in ("compare", "detect"):
        s = sub.add_parser(name)
        s.add_argument("a", type=Path)
        s.add_argument("b", type=Path)
    s = sub.add_parser("power")
    s.add_argument("a", type=Path)
    s.add_argument("b", type=Path, nargs="?")
    a = ap.parse_args()
    only_graders = OUTCOME if a.outcome else None
    if a.cmd == "curves":
        curves(a.runs)
    elif a.cmd == "compare":
        compare(a.a, a.b)
    elif a.cmd == "detect":
        detect(a.a, a.b)
    else:
        power(a.a, a.b)


if __name__ == "__main__":
    main()
