# Inspect (`inspect_ai`): P0 spike notes

Run: `cd tools/inspect && uv run python spike.py`, with `ANTHROPIC_API_KEY` exported for that command only (see the spike brief).

## Install on 3.14

Yes. `uv init --bare --no-workspace --python 3.14`, then `uv add inspect-ai anthropic` installed cleanly on CPython 3.14.7 (Windows x64), with no build errors.
Resolved versions: `inspect-ai 0.3.289`, `anthropic 2.8.0`. The Anthropic SDK is not pulled in by `inspect-ai` itself (`uv tree --invert --package anthropic` shows only the direct dependency); you add it per provider.

## Judge on claude-opus-5-5

It worked first time with no errors and no workarounds, using the defaults: `model_graded_qa()` and a grader bound to `claude-opus-5-5`, with no config.
The provider already knows the new constraints (source: `model/_providers/anthropic.py`):

- `temperature`, `top_p` and `top_k`: if set in `GenerateConfig`, they are dropped with a `warn_once`, never sent (API version 2026-10-21).
- Forced `tool_choice` on models that reject it: degraded to `auto` with a warning.
- `reasoning_tokens` (`budget_tokens`) on Claude 4.7+: raises a `PrerequisiteError` before sending.
- `reasoning_effort="none"`: Opus 5.5 rejects disabling thinking. The provider has a message string for this case; whether it raises or warns was not checked.

None of these paths was hit by the default judge.

## What the built-in judge sends

Vessel ids: request **10090** (`good`) and **10089** (`bad`). Both are in session `p0-spike` (id 141) with tag `inspect`, are 200 / `end_turn`, and were streamed.

| Param | Value sent |
| --- | --- |
| model | `claude-opus-5-5` |
| max_tokens | `32000` (Inspect's Anthropic default, `ANTHROPIC_MAX_TOKENS`) |
| temperature / top_p / top_k | not sent |
| system | not sent; the whole prompt is a **single user message** (string content) |
| tools / tool_choice | `tools: []` (empty list sent); no `tool_choice` |
| thinking | `{"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}`, plus header `anthropic-beta: thinking-binding-controls-2026-08-01` |
| output_config / structured output | none; free text |
| other | top-level `cache_control: {"type": "ephemeral"}` (automatic prompt caching), `stream: true`. Both calls **wrote** cache (1,343 tokens in total, 670 on `good`) and neither read it (`CR: 0`): the per-request nonce sits near the top of the prompt, so the cache can never hit. The judge pays the cache-write premium for nothing. |

- **Prompt:** the template is adapted from OpenAI's `closedqa`, with fields marked by `[BEGIN TASK/SUBMISSION/CRITERION (<nonce>)]` and an injection-hardening preamble. The rubric goes in as `Sample.target` and becomes the `{criterion}`. The 8-hex nonce is random per request, so the prompt text is not byte-stable across runs. Verbatim copy: `judge_prompt.txt`.
- **Verdict parsing:** the judge is asked for step-by-step reasoning, then `GRADE: C` or `GRADE: I`. A regex takes the **last** `GRADE:` match, tolerating markdown, brackets and a fullwidth colon. The match is checked against the offered letters (C/I, or C/P/I with `partial_credit`). If it cannot be parsed, the result is `Score.unscored(reason="grader_failed")`, with value NaN, excluded from metrics and not retried. C/I map to 1/0 via `accuracy()`.
- **Explanation:** the grader's full completion is stored as `Score.explanation`. The prompt and reply are in `Score.metadata["grading"]`.

## Verdicts

- good → **C** (pass). The judge considered "3-5 business days" a borderline promise, then accepted it as part of the refund.
- bad → **I** (fail).

The `accuracy` metric is 0.5, as expected.

## Case / grader / run

- **Case:** `inspect_ai.dataset.Sample(id=, input=, target=, metadata=)`. The dataset is a list of samples (or `MemoryDataset`, or `json_dataset`/`csv_dataset`).
- **SUT:** a `@solver` (here `canned_reply`) that sets `state.output = ModelOutput.from_content(...)` from `state.metadata`. There is no native "precomputed output" concept. `precomputed_scores()` exists, but it is for scores, not outputs.
- **Grader:** `inspect_ai.scorer.model_graded_qa()` (built in). The judge model comes from the `"grader"` **model role**: `eval(..., model_roles={"grader": get_model(...)})`, with `base_url=` and `GenerateConfig(extra_headers=...)` on `get_model`. `model=` on the scorer overrides the role. A list of models gives a majority-vote panel.
- **Run:** `@task` returns `Task(dataset=, solver=, scorer=)`, and `inspect_ai.eval(task, model=None, model_roles=..., log_dir=..., display="plain")` returns `list[EvalLog]`.
- **Results/logs:** a binary `.eval` log in `tools/inspect/logs/` (`log_dir` is set relative to the script, because Inspect's default `./logs` follows the CWD).
  - View with `uv run inspect view --log-dir logs` (web viewer), or `read_eval_log()` in Python.
  - Tried: the log contains a `model` event for the grader with `role="grader"` and the raw `ModelCall` request (`model`, `max_tokens`, `messages`, `thinking`, `tools`, `cache_control`, `extra_headers`), so the judge request is auditable without Vessel. Checked: the API key value does not appear anywhere in the `.eval` archive.
- **Repeats:** native (source, not tried; the docs reducer page 404'd). `Task(epochs=Epochs(n, reducer))` or `eval(epochs=n)`. The built-in reducers include `mean`, `median`, `mode`, `max`, `majority`, `at_least(k)`, **`pass_at(k)`** (Chen 2021 estimator) and **`pass_k(k)`** (pass^k, `C(correct,k)/C(total,k)`).
- **Trajectory/tool calls:** native (source, plus the `events` list seen in this run's log). `TaskState.messages` holds the full chat, including tool calls and results. The sample `events` transcript records every model and tool event. `model_graded_qa(include_history=True)` shows the history to the judge.
  - P9 caveat: this only covers calls made through Inspect's model and tool APIs. `supportdesk.run` runs its own client, so its tool calls will not appear in `messages`/`events` unless the solver copies the `Trial` into `TaskState`, for example as messages or metadata.

## Friction / surprises

- On Windows a warning prints every run: `Control server failed to start (eval will run without control surface): module 'socket' has no attribute 'AF_UNIX'`. It is harmless.
- `max_tokens: 32000` is sent by default for a judge that needs a few hundred tokens. `tools: []` is sent even with no tools.
- Thinking is adaptive and always on for Opus 5.5 (it cannot be turned off): 199 thinking tokens on the `good` call. That adds cost and latency on top of the CoT the template already asks for.
- The nonce makes the judge prompt differ on every request, which matters for caching and for diffing captured prompts.
- The canned output needs a custom solver. `mockllm/model` custom outputs are order-dependent across concurrent samples, so they were avoided.
- Pyright strict reports `eval` as partially unknown. Its `scanner` parameter type comes from the optional `inspect_scout` package, imported under `TYPE_CHECKING`. This is a library typing issue, silenced with a targeted `# pyright: ignore[reportUnknownVariableType]` on the import.
- `pyproject.toml` needs its own `[tool.pyright]` section. Without it, pyright walks up to the root config, whose `.venv` lacks `inspect_ai`.

## Files

- `tools/inspect/pyproject.toml`, `tools/inspect/uv.lock`: the uv project (Python >=3.14) and its local pyright config.
- `tools/inspect/spike.py`: the two-case eval.
- `tools/inspect/judge_prompt.txt`: the verbatim judge prompt for `good` (Vessel request 10090).
- `tools/inspect/logs/*.eval`: the run log (`logs/` is gitignored at the root).
- `tools/inspect/NOTES.md`: this file.
