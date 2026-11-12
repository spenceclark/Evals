# LangSmith (`langsmith` + `openevals`): P0 spike notes

Run: `cd tools/langsmith && uv run python spike.py`, with `ANTHROPIC_API_KEY` exported for that command only.
`uv run python spike.py --json-schema` runs the native structured-output variant.
No LangSmith account or key is needed: `evaluate(..., upload_results=False, disable_evaluator_tracing=True)` runs on local `Example` objects.

## Install on 3.14

Yes. `uv init --bare --python 3.14` + `uv python pin 3.14`, then `uv add langsmith openevals langchain-anthropic` installed cleanly on CPython 3.14.7 (Windows x64).
Versions: `langsmith 0.14.17`, `openevals 0.2.2`, `langchain-anthropic 1.7.10`, `langchain-core 1.8.0`, `langchain 1.5.0`, `anthropic 2.8.0`.
openevals also pulls in `langchain-openai 1.7.4` and `openai 3.49.0` as hard dependencies, though they are unused here.

## Judge on claude-opus-5-5

The judge worked first time: both verdicts were correct, with no API errors. The LangSmith runner did not: see the first bullet.

- **Not judge-related, but the first run leaked uploads.** With `upload_results=False` and `LANGSMITH_TRACING=false`, the SDK still tried to upload the judge's LangChain child runs and got 401s (no key exists). Verbatim (trace ids trimmed):
  `Failed to multipart ingest runs: langsmith.utils.LangSmithAuthError: Authentication failed for https://api.smith.langchain.com/runs/multipart. HTTPError('401 Client Error: Unauthorized for url: https://api.smith.langchain.com/runs/multipart', '{"error":"Unauthorized"}\n')`
  Cause (from reading the source; inferred, not traced): evaluators run under `tracing_context(enabled="local")`, and langchain-core treats `"local"` as "tracing on", so it attaches a real `LangChainTracer`. **Fix:** `evaluate(..., disable_evaluator_tracing=True)` (a documented parameter). After that, no run uploads were attempted. It is still not fully offline: with `LANGSMITH_ENDPOINT` pointed at a closed port, the client still made `GET /info` at startup and logged `Failed to get info from http://127.0.0.1:9: LangSmithConnectionError('Connection error caused failure to GET /info in LangSmith API. ...')`. The verdicts were unaffected (Vessel 10131/10132). `upload_results=False` itself prints `LangSmithBetaWarning: 'upload_results' parameter is in beta.`
- langchain-anthropic 1.7.10 already knows Opus 5.5 rejects forced tool use. `with_structured_output(method="function_calling")` (openevals' default) binds the tool **without** `tool_choice` and warns:
  `UserWarning: You are attempting to use structured output via forced tool calling, which is not supported when `thinking` is enabled or on claude-opus-5-5. This method will raise an OutputParserException if tool calls are not generated. Consider `method='json_schema'`, ...`
  The model called the tool anyway in all 8 default-mode requests.
- The documented alternative works too: `create_llm_as_judge(..., structured_output_kwargs={"method": "json_schema"})` sends `output_config.format` instead of a tool (Vessel 10110/10111).
- No `temperature` / `top_p` / `top_k` / `thinking` is sent by default. langchain-anthropic also raises client-side if you set a non-default `temperature` on `claude-fable-5*` / `claude-sonnet-5-5*` (source). Opus 5.5 is not in that list, so a user-set `temperature` would still reach the API and 400 (source, not tried).

## What the built-in judge sends

Default mode (final run): Vessel **10115** (`good`) and **10116** (`bad`). Earlier runs: 10106/10107 and 10108/10109. `--json-schema`: **10110** (`good`) and **10111** (`bad`).
All are tag `langsmith`, session `p0-spike` (141, via the `X-Vessel-Session` header through `ChatAnthropic(default_headers=...)`), HTTP 200, and not streamed.
The full `good` prompt is in `judge_prompt.txt`, with the tool schema and the `--json-schema` `output_config` appended.

| Param | Default (`function_calling`) | `--json-schema` |
| --- | --- | --- |
| model | `claude-opus-5-5` | same |
| system | none: the whole prompt is one user message (openevals' `system=` is optional) | same |
| max_tokens | `128000` (from langchain-anthropic's model profile; openevals sets none) | same |
| temperature / top_p / top_k | not sent | not sent |
| tools | one tool `score`, schema `{reasoning: string, score: boolean}`, both required, `additionalProperties: false` | none |
| tool_choice | **absent** (langchain-anthropic does not force it on Opus 5.5) | absent |
| thinking | not sent (adaptive; the response has an empty `thinking` block) | same |
| output_config | not sent | `{"format": {"type": "json_schema", "schema": {...same schema, title "score"}}}` |
| stop_reason | `tool_use` | `end_turn` |
| anthropic-version header | `2026-10-21` (Anthropic SDK 2.8.0) | same |

**The prompt is only my template.** openevals adds no instructions to the message text. All of its "judge" wording lives in the schema: `reasoning` is described as "A human-readable explanation of the score. You MUST end the reasoning with a sentence that says: Thus, the score should be: SCORE_YOU_ASSIGN." and `score` as "A score that is true if criteria in the prompt are met, and false otherwise." In default mode, nothing in the prompt tells the model to call the tool.
**Verdict parsing:** LangChain's structured-output parser takes the tool call args (default mode) or the JSON text (json_schema mode). openevals returns `{"key": feedback_key, "score": bool, "comment": reasoning}`. A missing tool call raises `OutputParserException`, which LangSmith's runner would log and turn into an errored evaluator result. That never happened here.

## Verdicts

good → **pass** (`score=True`). bad → **fail** (`score=False`). Correct in all 4 default-mode runs (10106/10107, 10108/10109, 10115/10116, 10131/10132) and the 1 json_schema run (10110/10111).

## Case / grader / run

- **Case:** `langsmith.schemas.Example(inputs, outputs, metadata)`. Usually it sits in a hosted `Dataset`, but `evaluate(data=...)` also accepts any iterable of `Example`s. Tried, local.
- **Target:** `target(inputs: dict) -> dict`. Tried.
- **Grader:** an "evaluator", which is a function `(inputs, outputs, reference_outputs) -> dict | EvaluationResult` or a `RunEvaluator`. Results are "feedback" (`key`, `score`, `comment`). openevals `create_llm_as_judge` returns such a function. Tried. Summary evaluators work over the whole run (source signature).
- **Run:** an **experiment**. `evaluate()` returns `ExperimentResults`, iterable rows of `{run, example, evaluation_results}`. Tried.
- **Runs locally without an account:** `evaluate(upload_results=False, disable_evaluator_tracing=True)` with local `Example`s, and openevals judges. Tried.
- **Needs an account:** hosted datasets (`data="name"`), the experiment UI and comparison, pairwise `evaluate((exp_a, exp_b), ...)`, annotation queues, and online evaluators. Hosted datasets and pairwise are from the source (`_resolve_data`, `evaluate` overloads); the UI, annotation queues and online evaluators are from memory, unverified.
- **Repeated trials:** `evaluate(num_repetitions=N)` (source signature, not tried).
- **Trajectory / tool calls:** openevals ships `openevals.trajectory` with `create_trajectory_match_evaluator` (strict / unordered / subset / superset) and `create_trajectory_llm_as_judge`, over OpenAI-format message lists. Source, not tried. LangSmith run trees hold child runs for tool calls (from memory, unverified).

## Needed from the user for P9

- A LangSmith account (the free tier and EU-region endpoint are from memory, unverified) and a `LANGSMITH_API_KEY` (`LANGSMITH_ENDPOINT` is the SDK's URL setting, per source), to store datasets and view experiments.
- Then drop `upload_results=False`. Keep `ChatAnthropic(base_url=Vessel)` so judge traffic stays on Vessel.
- Self-hosting needs an Enterprise plan and license key (per LangChain's self-hosting docs and KB), so it is not a P9 option.

## Friction / surprises

- `upload_results=False` (beta) is not offline by itself: the LangChain-based judge's traces still try to upload, and fail with 401 without a key. You need `disable_evaluator_tracing=True` as well.
- openevals' default structured output on Opus 5.5 is an *unforced* tool call with no instruction in the prompt to call it. It works in practice, but it is a silent dependency on model behaviour. `method="json_schema"` is the robust choice.
- Even in local mode the client calls `GET /info` on the LangSmith endpoint at startup.
- A log-format bug: the runner prints `Starting evaluation of experiment: %s sunny-wine-31` (literal `%s`).
- `max_tokens: 128000` is sent on a non-streamed request, from the model profile.
- openevals hard-depends on `langchain-openai` and `openai` even for an Anthropic judge.
- pyright strict: `langsmith.evaluate` and `openevals.create_llm_as_judge` have partially-unknown types (library typing). `spike.py` has `# pyright: ignore` on those imports.
- `uv init` inside the repo auto-registered `tools/langsmith` as a workspace member in the root `pyproject.toml`. I removed that line. Use `uv init --no-workspace`.

## Files

- `pyproject.toml`, `uv.lock`, `.python-version`: the project (Python 3.14, own venv, pyright strict config pointing at the local `.venv`)
- `spike.py`: the `evaluate` + `create_llm_as_judge` two-case check (`--json-schema` flag for the variant)
- `judge_prompt.txt`: the `good` case prompt and tool schema from Vessel id 10115, plus the `--json-schema` `output_config` from 10110
- `NOTES.md`: this file
