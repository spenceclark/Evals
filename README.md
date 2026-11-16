# Evals

A learning project on evaluating LLM applications, and the code behind a blog series about it. Everything is built
around one small system under test: a customer-support agent for an imaginary online shop, which handles refund emails
with tools (look up a customer, look up an order, refund, escalate, close the ticket).

The plan is in [docs/spec.md](docs/spec.md). What each phase found is in [docs/findings.md](docs/findings.md).

## Layout

| Path | What's there |
| --- | --- |
| `src/evalkit/` | A small harness: cases, trials, graders, runs, regrading, model judges, and the statistics (pass@k, pass^k, paired intervals, MDE). |
| `src/supportdesk/` | The system under test (shop, tools, agent loop) and its graders: outcome, path, recovery, production checks and judges. The shipped prompt is `system_prompt.txt`. |
| `cases/` | `full.jsonl` (66 generated cases), `captured.jsonl` (20 from simulated traffic), the traffic itself, and the human labels. |
| `scripts/` | Case generation, labelling, judge validation, simulated traffic, the production monitor, the suite lifecycle, and the CI gate (`ci.py`). |
| `ci/`, `.github/workflows/` | The CI baseline seed, the PR gate (`evals.yml`) and the nightly report (`evals-nightly.yml`). |
| `experiments/` | Judge bias (position, length, self-preference) and counting. |
| `tools/` | The same eval in eight frameworks: the P0 spike (`NOTES.md`) and the P9 port (`P9.md`) per tool, and the shared slice in `tools/p9/`. |
| `tests/` | Plain scripts; each prints an `ok:` line. |
| `docs/` | One write-up per phase (`p0-…` to `p9-…`), the findings log, the spec and the blog drafts. |
| `runs/` | Run output (gitignored). |

## Running it

Python 3.14 and [uv](https://docs.astral.sh/uv/). Model calls need `ANTHROPIC_API_KEY`. The local model runs through
Ollama.

```bash
uv sync
uv run python -m evalkit run --cases cases/full.jsonl --task supportdesk.task:haiku --graders supportdesk.path:ALL --trials 3
uv run python -m evalkit report runs/<run-dir>
```

Set `VESSEL_URL` (for example `http://127.0.0.1:4550`) to route model calls through the Vessel proxy, which records
every request and response. The production monitor reads its traffic from there.

Checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
for t in tests/test_*.py; do uv run python "$t"; done
```

CI runs the same checks on every pull request that touches `src/`, `cases/` or the gate, then the regression suite
(62 cases, 3 trials each) on the shipped prompt. It fails if the trial pass rate drops below 95%, or if a case that
always passed on main now always fails. To run the gate locally:

```bash
uv run python scripts/ci.py pr
```

## The phases

| Phase | Topic | Write-up |
| --- | --- | --- |
| P0 | Framework compatibility | [p0-compat.md](docs/p0-compat.md) |
| P1 | Anatomy of a trial | [p1-anatomy.md](docs/p1-anatomy.md) |
| P2 | Cases and code graders | [p2-cases.md](docs/p2-cases.md) |
| P3 | Grading the path | [p3-path.md](docs/p3-path.md) |
| P4 | Judges, validated against human labels | [p4-judges.md](docs/p4-judges.md) |
| P5 | Judge bias | [p5-bias.md](docs/p5-bias.md) |
| P6 | Counting: pass@k, pass^k, noise | [p6-counting.md](docs/p6-counting.md) |
| P7 | Production checks, captures → cases, suite lifecycle | [p7-production.md](docs/p7-production.md) |
| P8 | CI: what gates a merge, and what only reports | [p8-ci.md](docs/p8-ci.md) |
| P9 | The same eval in eight tools | [p9-tools.md](docs/p9-tools.md) |
