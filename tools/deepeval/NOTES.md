# DeepEval: P0 spike notes

Run: `cd tools/deepeval && PYTHONUTF8=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run python spike.py` (with `ANTHROPIC_API_KEY` loaded as in the brief).

## Install on 3.14

- **Yes.** `deepeval==4.3.4`, plus `anthropic==2.8.0`, on CPython 3.14.7. That's 85 packages. Installed cleanly from wheels.
- `anthropic` is **not** a deepeval dependency (`openai`, `posthog`, `portalocker` and the pytest plugins are), so add it yourself.
- On import, 3.14 warns:
  `deepeval\evaluate\execute\loop.py:820: SyntaxWarning: 'return' in a 'finally' block` (PEP 765). It's harmless, but it's printed on every run.
- uv gotcha (affects every tool dir): plain `uv init` inside `tools/<name>` auto-adds the dir to a `[tool.uv.workspace]` in the
  **root** `pyproject.toml`, and installs into the root `.venv`/`uv.lock`. Use `uv init --bare --no-workspace`. The root was
  restored after this happened once.

## Judge on claude-opus-5-5

- **Worked first time.** Vessel shows 3 requests (10112-10114), all HTTP 200, `x-stainless-retry-count: 0`, with no error rows under the tag.
  That matters because `AnthropicModel` silently retries in some cases (see Friction).
- No temperature/thinking/tool_choice problems. Reason: deepeval 4.3.4's model table (`deepeval/models/llms/constants.py`) already has
  a `claude-opus-5-5` entry with `supports_temperature=False` and `supports_thinking` unset. Its source comment says it "Rejects
  `thinking: {"type": "disabled"}`", so no thinking block is sent.
  - Source, not tried: for models marked `supports_thinking=True` (e.g. `claude-opus-5`, `claude-sonnet-5`), it sends
    `thinking: {"type": "disabled"}` unless `DEEPEVAL_MODEL_THINKING` is set. Correct behaviour therefore depends on this
    per-model table being current.
  - Tried (construction only, offline): an ID missing from the table (e.g. a future model) constructs fine. It gets `supports_temperature=True` and
    no structured output, so it falls back to free-text JSON.
- Windows-only error on the first attempt (not the judge): rich crashed printing the banner with
  `UnicodeEncodeError: 'charmap' codec can't encode character '✨' in position 0`. Workaround: `PYTHONUTF8=1`.
- Routing: `AnthropicModel(model=..., base_url=..., default_headers={"X-Vessel-Session": "p0-spike"})`. The docs say extra kwargs go
  to the `anthropic.Anthropic` client, but they don't name `base_url`/`default_headers`. Both worked: tag `deepeval`, session 141
  `p0-spike`.

## What the built-in judge sends

GEval in default mode (criteria only, no `evaluation_steps`, not strict) makes **two kinds of call**. Both are verbatim in `judge_prompt.txt`.

1. **Step generation** (10112): the judge writes its own 3-4 "evaluation steps" from the criteria. This happens **once per metric
   instance**: the steps are cached on the `GEval` object, so the bad case reused the steps generated for the good case. That gives
   3 calls for 2 cases, run sequentially with `AsyncConfig(run_async=False)`. Async default not tried.
2. **Scoring**, one call per case (10113 = good, 10114 = bad). The prompt asks for JSON with `"score"`, "an integer between 0
   and 10", and `"reason"`.

| Param | Sent |
| --- | --- |
| model | `claude-opus-5-5` |
| max_tokens | `8192` (deepeval `DEFAULT_MAX_TOKENS`; override via `generation_kwargs={"max_tokens": ...}`) |
| system | none. Each call is a single `user` message with one text block |
| temperature / top_p / top_k | not sent (temperature is sent only if you configure it *and* the table allows it) |
| tools / tool_choice | not sent |
| thinking | not sent |
| output_config | **yes**: native structured output, `output_config.format = {"type": "json_schema", "schema": ...}`. Step generation uses `Steps {steps: string[]}`; scoring uses `ReasonScore {reason: string, score: number}`; both have `additionalProperties: false` |

- **Verdict parsing:**
  1. Anthropic has no logprobs, so G-Eval's probability-weighted scoring is skipped (`_no_log_prob_support`).
  2. It falls back to `generate_with_schema` → pydantic `ReasonScore`.
  3. `score` is normalized: `(score - 0) / 10`.
  4. `success = score >= threshold` (default 0.5).

  The pass/fail is not a native boolean from the judge.
- **Silent fallback** (source): on a 400 whose message mentions `output_config`/`output_format`/`schema`, `AnthropicModel` retries
  as free text and parses JSON from the text. It does this for the rest of that model object's life, with no warning.

## Verdicts

- good → **pass** (score 0.9: the judge docked a point, calling "3-5 business days" a "minor timing promise")
- bad → **fail** (score 0.0)

Risk (docs, not tried): `strict_mode=True` scores 1 only for a "perfect" answer. Given the 9/10 above, strict mode could fail the good case.

## Case / grader / run

- **Case:** `deepeval.test_case.LLMTestCase(name=, input=, actual_output=)`. It also has `expected_output`, `context`,
  `retrieval_context`, `tools_called`, `expected_tools`. Datasets: `EvaluationDataset` of `Golden`s (docs).
- **Grader:** a metric. Here it's `deepeval.metrics.GEval(name=, criteria=, evaluation_params=[SingleTurnParams.INPUT,
  SingleTurnParams.ACTUAL_OUTPUT], model=AnthropicModel(...))`, with `threshold` defaulting to 0.5. (`LLMTestCaseParams` is the
  deprecated name of `SingleTurnParams`.)
- **Run:** `deepeval.evaluate(test_cases=, metrics=, async_config=AsyncConfig(...))` → `EvaluationResult.test_results[i]`
  (`.name`, `.success`, `.metrics_data[j].score/.reason`). The pytest-style alternative is `assert_test` +
  `deepeval test run` (source).
- **Results/logs:**
  - Written to `./.deepeval/` in the cwd: `.latest_test_run.json`, `.latest_run_full.json`, and `.deepeval-cache.json`
    (`CacheConfig(write_cache=True)` is the default).
  - Views: the console table; `deepeval inspect` (local TUI over `.latest_run_full.json`); `deepeval view` (uploads to
    Confident AI, hosted).
  - Timestamped exports via `DisplayConfig(results_folder=...)` (source).
- **Repeated trials** (source, not tried): `evaluate()` has no repeats/trials argument. `deepeval test run --repeat N` exists, via the
  bundled `pytest-repeat`. No pass@k aggregation seen.
- **Trajectory/tool calls** (docs/source, not tried):
  - Test-case fields `tools_called` / `expected_tools` (`ToolCall`), plus `ToolCorrectnessMetric` and
    `ArgumentCorrectnessMetric`.
  - Span-level evals via `@observe` tracing and `dataset.evals_iterator(metrics=[...])`.

## Friction / surprises

- **Telemetry on by default:** PostHog (`https://us.i.posthog.com`). Opt out with `DEEPEVAL_TELEMETRY_OPT_OUT=1`. Both eval runs
  here opted out; one `deepeval inspect --help` call did not, so it may have sent an event.
- **Hosted nudges** in the output: "Run 'deepeval view' to analyze and save testing results on Confident AI", "No hyperparameters
  logged", plus a banner line.
- **Windows:**
  - `PYTHONUTF8=1` is needed (cp1252 crash in rich).
  - The portalocker warning "Could not load test run from disk: Shared locks on Windows require the win32 extra (pywin32)" printed
    4 times. The run still completed and wrote `.deepeval/`.
- **G-Eval's judge writes its own rubric steps** at run time, so the effective rubric is model-generated and can drift between runs
  unless you pin `evaluation_steps`. They're also cached on the metric object across cases.
- **Docs gaps:**
  - The Anthropic page doesn't say `base_url`/`default_headers` are accepted (they are).
  - The G-Eval page mixes 1-5 and 1-10 scales; the shipped template says 0-10.
- **pyright:**
  - The root `[tool.pyright]` (`venv = ".venv"`) makes pyright in `tools/deepeval` resolve against the root venv, so every import
    failed. This was fixed with a tool-local `[tool.pyright]` in this dir's `pyproject.toml`.
  - One remaining error comes from deepeval's own typing: `evaluate`'s `mcp_servers: List[MCPServer | "OfficialMCPServer"]` is an
    unresolved forward ref. It's suppressed on the import line.

## Files

- `pyproject.toml`, `uv.lock`: standalone uv project, Python 3.14, deepeval + anthropic, tool-local pyright config
- `spike.py`: the eval (2 `LLMTestCase`s, 1 `GEval`, `evaluate()`); asserts good→pass, bad→fail
- `judge_prompt.txt`: verbatim judge prompts for the good case (step generation + scoring)
- `run.log`: console output of the recorded run
- `.deepeval/`: deepeval's own run/cache files from that run
- `NOTES.md`: this file
