# Braintrust (`braintrust` + `autoevals`): P0 spike notes

Run: `cd tools/braintrust && uv run python spike.py`, with `ANTHROPIC_API_KEY` exported for that command only.
`uv run python spike.py --no-drop-params` reproduces the forced `tool_choice` failure.
No Braintrust account or key is needed: `Eval(..., no_send_logs=True)`.

## Install on 3.14

Yes. `uv init --bare --python 3.14` + `uv python pin 3.14`, then `uv add braintrust autoevals litellm` installed cleanly on CPython 3.14.7 (Windows x64).
Versions: `braintrust 0.50.0`, `autoevals 0.4.1`, `litellm 1.109.1` (pulls `openai 3.49.0`, `tiktoken 0.14.0`, `tokenizers 0.23.3`). No build errors.
`litellm` is needed only for the Claude route described below. The `anthropic` SDK is not needed and is not installed.

## Judge on claude-opus-5-5

It did not work first time. It worked after two documented changes, with no monkey-patching.

- **autoevals speaks only the OpenAI chat-completions format.** `LLMClassifier` builds `messages`/`tools`/`tool_choice` kwargs and calls `client.chat.completions.create`. Without `init()` it builds an `openai.OpenAI` client pointed at `OPENAI_BASE_URL` or the Braintrust AI gateway (`https://gateway.braintrust.dev`, which needs a Braintrust key). The default model is `gpt-5-mini`.
- autoevals ships `autoevals.litellm.LiteLLMClient` / `AsyncLiteLLMClient` (documented in the module docstring) to reach other providers with their native keys. LiteLLM translates the OpenAI-shaped kwargs and sends **native Anthropic Messages** (`POST /v1/messages`) to Vessel. That is the route used here. It is not an OpenAI-compatible endpoint on the Anthropic side, but the judge's request is built in OpenAI shape and translated client-side by LiteLLM.
- **Error 1 (forced tool_choice).** `OpenAILLMClassifier._build_args` always sets `tool_choice={"type": "function", "function": {"name": "select_choice"}}`, and no parameter turns it off. LiteLLM's model map flags `claude-opus-5-5` with `supports_forced_tool_use: false`, so LiteLLM refuses client-side. The request never reaches the API (no Vessel capture). Verbatim:
  `litellm.UnsupportedParamsError: claude-opus-5-5 does not support forced tool use (tool_choice='required' or a named tool). Use tool_choice='auto' and tell the model in the prompt when to call the tool, or set `litellm.drop_params = True` to downgrade to 'auto' automatically.`
  Braintrust swallows scorer exceptions. It prints `Found exceptions for the following scorers: refund_rubric [...]` and records the score as `None`, so `spike.py` reports `verdict=error` rather than `fail`.
  I re-ran this with the async client as well: `--no-drop-params` gives `verdict=error` on both cases and exits 1.
  **Workaround:** `litellm.drop_params = True` (documented in the error and in LiteLLM). It is a process-global switch. LiteLLM logs `Downgrading forced tool_choice to 'auto' for model=claude-opus-5-5 (drop_params=True): this model rejects tool_choice type 'any'/'tool' with a 400 because thinking is always on and a forced call would skip it.`
- **Error 2 (sync client under `Eval`).** `Eval` calls scorers through `eval_async`. With the sync `LiteLLMClient`, the HTTP call succeeds (Vessel 10101/10102, 200), and then autoevals fails with `TypeError("'ModelResponse' object can't be awaited")`. **Fix:** `init(client=AsyncLiteLLMClient(...))`.
- No `temperature` / `top_p` / `top_k` / `thinking` is sent by default, so none of those 400s were hit.

## What the built-in judge sends

Vessel ids for the final run: **10122** (`good`) and **10121** (`bad`). Both are tag `braintrust`, HTTP 200, `stop_reason: tool_use`, and not streamed. Earlier working run: 10104/10103.
The full `good` prompt and tool schema are in `judge_prompt.txt`.

| Param | Value sent | From |
| --- | --- | --- |
| model | `claude-opus-5-5` | `init(default_model="anthropic/claude-opus-5-5")` |
| system | none: the whole prompt is one user message | autoevals |
| max_tokens | `128000` | LiteLLM (model-map `max_output_tokens`; autoevals sends none) |
| temperature / top_p / top_k | not sent | autoevals sends `temperature` only if you pass it |
| tools | one tool `select_choice`, schema `{reasons: string, choice: enum["pass","fail"]}`, both required | autoevals (CoT on by default, `use_cot=True`) |
| tool_choice | `{"type": "auto"}` on the wire; autoevals asked for a forced named function | autoevals forces, LiteLLM downgrades with `drop_params` |
| thinking | not sent (the model used adaptive thinking; the response has an empty `thinking` block; 123 thinking tokens in 10122) | n/a |
| output_config / structured output | not used: structured output is through tool calling | autoevals |
| anthropic-version header | `2023-06-01` | LiteLLM |
| X-Vessel-Session | **not sent**: `litellm.headers` did not reach the Anthropic route. A custom `LLMClient(complete=...)` (documented in its docstring) could pass LiteLLM `extra_headers`, but I did not try it. Traffic landed in Vessel's current unnamed session (124), not `p0-spike` (141). | |

The prompt is my `prompt_template` (mustache: `{{input}}`, `{{output}}`) plus autoevals' fixed CoT suffix: "Answer the question by calling `select_choice` with your reasoning in a step-by-step matter ... from ['pass', 'fail']."
**Verdict parsing:** autoevals reads `tool_calls[0]` and requires the name `select_choice`. It parses the JSON args and maps `choice` through `choice_scores` (`pass→1`, `fail→0`). `reasons` goes to `metadata.rationale`. If there is no tool call, it raises `ValueError("No tool call found in response")`. Because the choice is no longer forced (`auto`), that is now possible, though it did not happen in 4 runs.

## Verdicts

good → **pass** (score 1). bad → **fail** (score 0). Both match the expected verdicts in 4 runs (10103/10104, 10117/10118, 10121/10122, 10129/10130).

## Case / grader / run

- **Case:** `EvalCase(input, expected, metadata, tags)`, passed as `data=` (a list, an iterator or a callable). Tried.
- **Task:** `task(input, hooks)`. `hooks.metadata`, `hooks.expected` and `hooks.span` are available. Tried (canned output chosen from `hooks.metadata`).
- **Grader:** a "scorer", either an autoevals `Scorer` (for example `LLMClassifier`, `Factuality`, `ExactMatch`) or any function returning `Score(name, score 0..1, metadata)`. `classifiers=` is a separate categorical hook (source, not tried).
- **Run:** an **experiment**. `Eval(...)` returns `EvalResultWithSummary`, with `.results: list[EvalResult]` (input, output, expected, scores, metadata, error). Tried.
- **Runs locally without an account:** `Eval(..., no_send_logs=True)`, autoevals scorers, and LiteLLM to Anthropic. Tried. With `BRAINTRUST_API_URL`, `BRAINTRUST_APP_URL` and `BRAINTRUST_PROXY_URL` pointed at a closed port, the run passed and logged no connection attempts or errors (Vessel 10129/10130).
- **Needs an account:** experiment storage and the baseline diff (`base_experiment_name`), and the Braintrust AI gateway (`gateway.braintrust.dev`, autoevals' default route, keyed by `BRAINTRUST_API_KEY`). Source. Remote datasets, prompts, the UI and `braintrust eval` pushing to a project are from memory: unverified.
- **Repeated trials:** `Eval(trial_count=N)` (source signature, not tried). Results come back per trial, and pass@k is your own glue.
- **Trajectory / tool calls:** traces and spans through `hooks.span` / `wrap_openai`. Scorers can take a `trace` argument, and `LLMClassifier` templates can use `{{thread}}` variables built from the trace (source: `autoevals/thread_utils.py`). There is no tool-call-sequence scorer in autoevals. Source, not tried.

## Needed from the user for P9

- A Braintrust account (a free tier is unverified) and a `BRAINTRUST_API_KEY`, to see experiments in the UI and compare runs. Drop `no_send_logs=True` once the key exists.
- Optional: the Braintrust AI gateway, if you want autoevals to use its default route (needs the same key, and adds a hop outside Vessel). Keep LiteLLM to stay on Vessel.
- Self-hosting is Enterprise-only (hybrid data plane / BYOC, per Braintrust's deployment docs), so it is not a P9 option.

## Friction / surprises

- autoevals is OpenAI-wire-only. Claude needs the bundled LiteLLM adapter (or the Braintrust gateway, which needs an account), so the request is OpenAI-shaped and translated by LiteLLM.
- `LLMClassifier` hardcodes a forced `tool_choice` with no off switch. The only fix is LiteLLM's **global** `drop_params`, which also silently drops any other unsupported parameter.
- The sync `LiteLLMClient` fails under `Eval` only *after* the paid API call. It took the async variant.
- `autoevals.init()` is typed for OpenAI clients only, so passing autoevals' own `AsyncLiteLLMClient` is a pyright error. There is a `# pyright: ignore` in `spike.py`.
- LiteLLM fetches its model cost map from GitHub on import unless `LITELLM_LOCAL_MODEL_COST_MAP=True`. `spike.py` sets it.
- `max_tokens: 128000` is sent on a non-streamed request, inherited from LiteLLM's model map.
- Without `init()` and without `OPENAI_BASE_URL`, autoevals sends judge calls to the Braintrust gateway and falls back to `OPENAI_API_KEY` as the key (`prepare_openai`, source). This shell has `OPENAI_API_KEY` set, so a forgotten `init()` would quietly send judge traffic off-Vessel.
- `uv init` inside the repo auto-registered `tools/braintrust` as a workspace member in the root `pyproject.toml`. I removed that line. Use `uv init --no-workspace`.

## Files

- `pyproject.toml`, `uv.lock`, `.python-version`: the project (Python 3.14, own venv, pyright strict config pointing at the local `.venv`)
- `spike.py`: the `Eval` + `LLMClassifier` two-case check
- `judge_prompt.txt`: the `good` case prompt (system: none; user text; `select_choice` tool and `tool_choice`) from Vessel id 10122
- `NOTES.md`: this file
