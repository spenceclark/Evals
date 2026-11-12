# Langfuse (`langfuse` Python SDK): P0 spike notes

Run: `cd tools/langfuse && uv run python spike.py`. No API key or Langfuse server is needed, and no LLM is called.
`spike.py` runs `Langfuse().run_experiment(...)` on local data with a client-side **code** evaluator.

## Install on 3.14

Yes. `uv init --bare --python 3.14` + `uv python pin 3.14`, then `uv add langfuse` installed cleanly on CPython 3.14.7 (Windows x64).
Versions: `langfuse 5.2.0` (built on `opentelemetry-sdk 1.45.2`). It imports fine on 3.14. pyright strict reports 0 errors on `spike.py` with no ignores.

## Judge on claude-opus-5-5

No client-side judge was run. Langfuse's own LLM-as-a-judge runs **server-side** in the Langfuse worker. The docs say "Nothing runs inside your application or in the browser." It is configured in the UI (Evaluators page) or through the public API. It calls the model through a project-level **LLM Connection**, and the result is written back as a score.
Correction to the brief: the SDK *can* run a client-side LLM judge through an adapter, `langfuse.experiment.create_evaluator_from_autoevals(autoevals_evaluator)` (docs + source). It calls the autoevals scorer **synchronously** and maps `Score` to `Evaluation`. So it would inherit everything in `tools/braintrust/NOTES.md`: OpenAI-wire only, forced `tool_choice`, LiteLLM `drop_params`, and here the *sync* `LiteLLMClient`. Not tried (the brief says no LLM for Langfuse).

## What the built-in judge sends

Not captured: there was no client-side judge run and no server. From the docs (LLM Connection page), not verified:
- From the custom-base-URL / gateway section of that page, which is about OpenAI-format gateways and may not describe the native Anthropic adapter: "LLM-as-a-Judge depends on tool calling." Its example test request forces a call to a function named `extract` (`tool_choice` names it) with `score` and `reasoning` fields. If the Anthropic adapter sends that forced `tool_choice` to `claude-opus-5-5`, it will 400. **This is the main P9 risk**; check it in P9.
- The judge model "should support structured output so scores can be reliably parsed". Errors that mention `response_format` point at a model or proxy without JSON-schema support.
- No Vessel request ids.

## Verdicts

The code evaluator (`refund_rule`: mentions "refund" and "a123", and contains no "can't" or "discount") gave good → **pass**, bad → **fail**. Both are correct. This is a stand-in, not the rubric judge. `result.format()` reports `refund_rule: 0.500` average.

## Case / grader / run

- **Case:** a `DatasetItem` (input, expected_output, metadata, status ACTIVE/ARCHIVED) in a `Dataset`. Hosted datasets are created with `langfuse.create_dataset()` / `create_dataset_item()`, which needs a server. `run_experiment(data=...)` also takes **local dicts** `{"input", "expected_output", "metadata"}`. Tried, local.
- **Task:** `task(*, item, **kwargs)`, sync or async. Tried.
- **Grader:** an evaluator function `(*, input, output, expected_output, metadata, **kwargs) -> Evaluation | list[Evaluation]`, where `Evaluation(name, value, comment, metadata, data_type, config_id)`. Tried. It is stored as a **Score** on the trace, with data types `NUMERIC`, `BOOLEAN`, `CATEGORICAL` (plus `CORRECTION`, `TEXT` in the API enum). Also: `composite_evaluator` (combines item scores), `run_evaluators` (run-level, `(*, item_results) -> Evaluation`), and server-side LLM-as-a-judge evaluators plus evaluation rules (UI/API). Source and docs.
- **Run:** a **DatasetRun** (the experiment run), with one **DatasetRunItem** per item linking item → `traceId` (optional `observationId`). `run_experiment()` returns `ExperimentResult` (`item_results: list[ExperimentItemResult(item, output, evaluations, trace_id, experiment_id)]`, `run_evaluations`, `experiment_id`, `experiment_url`, `format()`). Tried; without a server `trace_id` is all zeros.
- **Runs locally without an account:** `run_experiment` on local data with task and code evaluators. The client warns `Authentication error: Langfuse client initialized without public_key. Client will be disabled. ...` and runs anyway. With `LANGFUSE_TRACING_ENABLED=false` and `LANGFUSE_DEBUG=true` pointed at a dead base URL, no connection attempts were logged. Tried.
- **Needs an account or server:** hosted datasets, persisted DatasetRuns and scores, experiment comparison UI, and server-side LLM-as-a-judge (docs). The `RunnerContext` GitHub Action CI integration is from the source. Annotation queues are from memory, unverified.
- **Repeated trials:** **not supported** within one experiment. The docs say "Langfuse currently assumes that experiments do not contain repetitions" (issue #5855). You need one DatasetRun per repetition and your own pass@k glue (docs).
- **Trajectory / tool calls:** traces are OpenTelemetry spans and observations (generations, tool spans), and server-side evaluators can target observations. Client-side evaluators only get `input`/`output`/`expected_output`/`metadata`, not the trace, unless the task returns the trajectory as its output. Docs/source, not tried.

## Needed from the user for P9

Pick one:
- **Langfuse Cloud:** an account, a project, and `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` (region choice from memory, unverified). For the server-side judge, also an **LLM Connection** with an Anthropic key in project settings. Caveat: Cloud requires an `https://` base URL with a public hostname, so cloud judge traffic **cannot be routed through local Vessel** and cannot be captured.
- **Self-hosted (Docker Compose, OSS):** start the stack, create the first user, org and project in the UI, and create API keys. For the judge, add an Anthropic LLM Connection. To route it through Vessel, set the connection's base URL to a host the worker container can reach (for example `host.docker.internal:4550/b/anthropic/t/langfuse`) and allowlist it with `LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST` (loopback and private addresses are blocked by default). This was deferred to P9 as briefed.

## Friction / surprises

- The brief's premise is half right: the *native* judge is server-side, but the SDK documents a client-side judge through the autoevals adapter, which carries autoevals' OpenAI-only and forced-tool_choice baggage.
- The documented judge test forces a named tool (`extract`). That is likely incompatible with `claude-opus-5-5` (unverified until P9).
- `ExperimentResult.format()` emits emoji. `print()` of it crashed with `UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f4a1'` when stdout was piped on Windows (cp1252). `spike.py` writes UTF-8 bytes instead.
- With no keys the client prints an "Authentication error" but still runs local experiments. That is fine for spikes, but noisy.
- There are no repetitions per experiment, so pass@k is outside the tool.
- `uv init` inside the repo auto-registered `tools/langfuse` as a workspace member in the root `pyproject.toml`. I removed that line. Use `uv init --no-workspace`.

## Files

- `pyproject.toml`, `uv.lock`, `.python-version`: the project (Python 3.14, own venv, pyright strict config pointing at the local `.venv`)
- `spike.py`: the `run_experiment` two-case check with a code evaluator (no LLM, no server)
- `NOTES.md`: this file
- No `judge_prompt.txt`: there was no client-side judge run, so there is nothing to capture
