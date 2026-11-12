# Pydantic Evals (`pydantic-evals`): P0 spike notes

Run: `cd tools/pydantic-evals && uv run python spike.py`, with `ANTHROPIC_API_KEY` exported for that command only (see the spike brief).

## Install on 3.14

Yes. `uv init --bare --no-workspace --python 3.14`, then `uv add pydantic-evals "pydantic-ai-slim[anthropic]"` installed cleanly on CPython 3.14.7 (Windows x64).
Resolved versions: `pydantic-evals 2.68.0`, `pydantic-ai-slim 2.68.0`, `anthropic 2.8.0`, `pydantic 2.14.3`.
`pydantic-evals` alone has no Anthropic client. `uv tree --invert --package anthropic` shows it arrives only through the `pydantic-ai-slim[anthropic]` extra. Logfire is not installed (only `logfire-api`, a no-op shim).

## Judge on claude-opus-5-5

It worked first time with no errors and no workarounds, with only the model, base URL and header set.
pydantic-ai's Anthropic profile encodes the Opus 5.5 constraints (source: `profiles/anthropic.py`, `models/anthropic/__init__.py`):

- `supports_forced_tool_choice=False` and `thinking_always_enabled=True`. Because thinking is on, structured output switches from tool-calling to **native** `output_config.format` (no forced `tool_choice`).
- `disallows_sampling_settings`: `temperature`, `top_p` and `top_k` are dropped before sending, with a `UserWarning` ("Sampling parameters [...] are not supported by 'claude-opus-5-5'. These settings will be ignored."). The `LLMJudge` docs example passes `ModelSettings(temperature=0.0, ...)`; on this model you would get that warning, not a 400 (source, not tried).

## What the built-in judge sends

Vessel ids: request **10094** (`good`) and **10093** (`bad`). Both are in session `p0-spike` (id 141) with tag `pydantic-evals`, are 200 / `end_turn`, and were streamed.

| Param | Value sent |
| --- | --- |
| model | `claude-opus-5-5` |
| max_tokens | `128000` (the profile's max output for the model; pydantic-ai fills it in when unset) |
| temperature / top_p / top_k | not sent |
| system | yes: the fixed `judge_input_output` system prompt, with two few-shot examples and the JSON shape |
| user | one text block: `<Input>…</Input>\n<Output>…</Output>\n<Rubric>…</Rubric>` |
| tools / tool_choice | none / none |
| thinking | not sent (the model's default adaptive thinking applies: 119 thinking tokens on `good`) |
| output_config / structured output | `output_config.format = {type: json_schema, schema: {reason: string, pass: boolean, score: number}, additionalProperties: false, required: [reason, pass, score]}` (**native structured output**) |
| headers | `User-Agent: pydantic-ai/2.68.0`; no `anthropic-beta`; `X-Vessel-Session` passed through via `ModelSettings(extra_headers=…)` |

- **Prompt:** verbatim in `judge_prompt.txt`. The system prompt varies by `include_input` / `include_expected_output` (four variants: `judge_output`, `judge_input_output`, `judge_output_expected`, `judge_input_output_expected`). The default is `include_input=False`, so the judge sees only `<Output>` and `<Rubric>`. This spike sets `include_input=True` to match Inspect, which always sends the task.
- **Verdict parsing:** the JSON text is validated into the `GradingOutput` pydantic model (`reason`, `pass_` (alias `pass`), `score`). By default only `pass` is used, as an **assertion** (bool) named `LLMJudge`, with `reason` attached.
  - `score` is always requested from the model but discarded unless `LLMJudge(score={...})` is set.
  - There is no free-text or regex step. What happens on a schema violation (a pydantic-ai output retry, or an error recorded on the evaluator) was not checked.

## Verdicts

- good → **pass** (✔). Reason: "The reply confirms a full refund for order A123. The amount and the 3-5 business day timeline are details of that refund rather than additional offers or promises."
- bad → **fail** (✗). Reason: "The reply refuses the refund instead of confirming one for order A123, and it offers an extra 50% discount code, which the rubric forbids."

The assertions average is 50%, as expected.

## Case / grader / run

- **Case:** `pydantic_evals.Case(name=, inputs=, expected_output=, metadata=, evaluators=)`, collected in `Dataset[InputsT, OutputT, MetadataT](name=, cases=, evaluators=)`. Datasets can also be loaded from and saved to YAML/JSON (`Dataset.from_file` / `to_file`; source).
- **SUT:** the "task" is any `(inputs) -> output` (async) function passed to `evaluate`. The task receives only `inputs`, not the case, so two cases with identical inputs cannot get different canned outputs from the task itself.
  - Workaround used: a `CaseLifecycle` subclass (`CannedReply`) whose `prepare_context` returns `dataclasses.replace(ctx, output=case.metadata["reply"])`, with a stub task.
  - There is no native "precomputed output" concept.
- **Grader:** `pydantic_evals.evaluators.LLMJudge(rubric=, model=, include_input=, include_expected_output=, model_settings=, score=, assertion=)`.
  - The judge model is passed explicitly as `AnthropicModel("claude-opus-5-5", provider=AnthropicProvider(base_url=...))`. Do not rely on the default: it is `openai:gpt-5.2`, changeable globally via `set_default_judge_model()`.
  - Other evaluators subclass `Evaluator` and implement `evaluate(ctx: EvaluatorContext)`.
- **Run:** `dataset.evaluate_sync(task, lifecycle=..., repeat=..., max_concurrency=...)`, or `await dataset.evaluate(...)`, returns an `EvaluationReport`.
- **Results/logs:** in memory only. `report.print(include_input=, include_output=, include_reasons=)` renders a Rich table, and `report.cases[i].assertions["LLMJudge"]` holds `.value` and `.reason`. Nothing is written to disk by default.
  - Persisting or browsing runs means Logfire: `pydantic-evals[logfire]` plus `logfire.configure()`, which sends experiments to the Logfire UI, or any OTel backend. The `EvaluationReport` itself is serialisable (docs).
- **Repeats:** native (source/docs, not tried): `evaluate(..., repeat=n)` runs each case n times and groups results by case name. There is no pass@k / pass^k estimator: grepping `pydantic_evals` for `pass_at`, `pass_k` and `pass@` finds nothing. Aggregation is averages over assertions and scores, so pass@k has to be computed from the report.
- **Trajectory/tool calls:** native but OTel-dependent (docs, not tried).
  - Span-based `HasMatchingSpan(query=SpanQuery(...))`, or `ctx.span_tree` in custom evaluators.
  - "Agentic" evaluators: `ToolCorrectness`, `TrajectoryMatch` (exact / in_order / any_order), `ArgumentCorrectness`, `MaxToolCalls`, `MaxModelRequests`.
  - All of these need `pydantic-evals[logfire]` installed and configured (no account needed), and they only see tools executed by pydantic-ai itself. A non-pydantic-ai SUT (our `supportdesk.run`) would have to emit its own OTel spans for these to work.

## Friction / surprises

- The default judge is OpenAI (`openai:gpt-5.2`) even in an Anthropic-only setup. Here an unset `model=` would fail because neither the `openai` package nor `OPENAI_API_KEY` is present (source, not tried).
- `max_tokens: 128000` is sent for a judge that writes about 70 tokens of JSON.
- The judge always asks for a `score` the default config throws away. The system prompt's few-shot examples (pirate, "Hello world") are fixed and cannot be changed without replacing the judge.
- Identical inputs across cases needed a lifecycle hook to inject canned outputs. The task signature is `inputs -> output`, with no case access.
- With `include_input=True`, inputs are stringified (non-str values become JSON) into `<Input>` tags, with no escaping or nonce. That is weaker injection hardening than Inspect's.
- `report.print()` on the Windows console rendered ✔/✗ as `v`/`x` (Rich falling back on the legacy codepage). Cosmetic.
- `pyproject.toml` needs its own `[tool.pyright]` section; otherwise pyright walks up to the root config and its venv. After that, pyright strict reports 0 errors with no ignores: the library is well typed.

## Files

- `tools/pydantic-evals/pyproject.toml`, `tools/pydantic-evals/uv.lock`: the uv project (Python >=3.14) and its local pyright config.
- `tools/pydantic-evals/spike.py`: the two-case eval.
- `tools/pydantic-evals/judge_prompt.txt`: the verbatim judge system and user prompt for `good` (Vessel request 10094).
- `tools/pydantic-evals/NOTES.md`: this file.
