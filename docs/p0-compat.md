# P0: tool compatibility spike (output O24)

**Run:** 2026-11-12, on Windows 11, CPython 3.14.7, with judge `claude-opus-5-5` routed through Vessel (session `p0-spike`, one
tag per tool).

**The test is the same in every tool.** Each one ran two canned replies to one refund request (`good` should pass, `bad` should
fail) through its own case / grader / run concepts, with one LLM-judged rubric. Details per tool are in `tools/<name>/NOTES.md`.
The verbatim judge prompts are in `tools/<name>/judge_prompt.txt`.

The parameter columns below were re-checked against the captured requests in Vessel (ids listed). They are not taken from the
agents' notes.

## Summary

| Tool | Version | Py 3.14 | Opus 5.5 judge | Verdict mechanism | Judge sees the question? | Repeats / pass@k | Needs account |
| ---- | ------- | ------- | -------------- | ----------------- | ------------------------ | ---------------- | ------------- |
| Promptfoo | 0.125.1 (Node 24) | ✅ via Python provider; `pythonExecutable` must point at the 3.14 venv | ✅ first try | Free text; first JSON object, then `pass` | ❌ **default rubric prompt sends output + rubric only** | `--repeat n` with a per-case `repeatStability` (pass rate, 95% CI, unstable flag); no pass@k | No (cloud optional) |
| Inspect | inspect-ai 0.3.289 | ✅ | ✅ first try | Free text; regex for the last `GRADE: C/I` | ✅ | **Native**: `epochs` + `pass_at(k)` and `pass_k(k)` reducers (source) | No |
| Pydantic Evals | 2.68.0 | ✅ (`pydantic-ai-slim[anthropic]`) | ✅ first try | **Structured output** (`output_config.format`: reason/pass/score) | ⚠️ only with `include_input=True` (off by default) | `repeat=n`; no pass@k | No (Logfire to persist runs) |
| DeepEval | 4.3.4 | ✅ (prints a SyntaxWarning on import) | ✅ first try | **Structured output**, score 0–10 → /10, pass ≥ 0.5 | ✅ scoring calls; ❌ the step-generation call | `deepeval test run --repeat n` (pytest-repeat); no pass@k | No (Confident AI nudges) |
| OpenAI Evals (OSS) | **2.0.0.post1 (Dec 2023)** | ⚠️ latest 3.0.1 is unsatisfiable on 3.14 (tensorflow/protobuf); uv silently picked the old release | ⚠️ needed a ~50-line Claude completion fn; **hard-codes `temperature=0, top_p=1` → 400** | Free text; last line starting or ending with Y/N | ✅ | None; duplicate rows or re-run | No (but needs a placeholder `OPENAI_API_KEY`) |
| Braintrust | braintrust 0.50.0, autoevals 0.4.1 | ✅ | ⚠️ autoevals **forces `tool_choice`** → LiteLLM refuses for Opus 5.5; works with a process-wide `litellm.drop_params = True` | Tool call (`select_choice`) | ✅ | `Eval(trial_count=n)` (source); no pass@k | Experiments, diffs, UI |
| LangSmith | langsmith 0.14.17, openevals 0.2.2 | ✅ | ✅ after `disable_evaluator_tracing=True` (otherwise it tries to upload, 401) | Tool call (`score`), no `tool_choice`; `json_schema` mode also works | ✅ | `evaluate(num_repetitions=n)` (source); no pass@k | Datasets, experiments, queues |
| Langfuse | langfuse 5.2.0 | ✅ | n/a: the LLM judge runs **server-side**; local `run_experiment` worked with a code evaluator | — | — | ❌ no repetitions per experiment (issue #5855) | Everything server-side |

## What each judge actually sent (from Vessel)

| Tool | Vessel id | `max_tokens` | Sampling params | System prompt | Tools / `tool_choice` | Thinking | Structured output | Tokens in/out |
| ---- | --------- | ------------ | --------------- | ------------- | --------------------- | -------- | ----------------- | ------------- |
| Promptfoo | 10086 | 2,048 | none | yes | — | default (adaptive) | no | 339 / 206 |
| Inspect | 10090 | 32,000 | none | no | `[]` | explicit `adaptive` + block binding | no | 674 / 594 |
| Pydantic Evals | 10094 | 128,000 | none | yes (few-shot) | — | default | **yes** | 744 / 189 |
| DeepEval | 10112–10114 | 8,192 | none | no | — | default | **yes** | 445/208 (steps) + ~866/~208 per case |
| OpenAI Evals | 10125 → 400; 10128 ok | 1,024 | **`temperature`, `top_p`** (10125, 400) | no | — | default | no | 335 / 689 |
| Braintrust | 10122 | 128,000 | none | no | `select_choice` / `auto` (forced, downgraded) | default | no | 738 / 482 |
| LangSmith | 10115 | 128,000 | none | no | `score` / none | default | no | 662 / 420 |

Output tokens include adaptive thinking, which Opus 5.5 can't turn off. At the cached Opus 5.5 rates ($4 / $20 per million
tokens), these toy judgments cost about **$0.005–0.015 per call**. That is lower than the plan's $0.04 assumption. Re-measure with
real support-desk transcripts in P2.

## Cross-cutting findings (blog material, post 2)

1. **Most libraries now track model capabilities and quietly drop what Opus 5.5 rejects.** Promptfoo, Inspect, DeepEval,
   pydantic-ai and langchain-anthropic all strip `temperature`, forced `tool_choice` or disabled thinking. The upside: they work.
   The downside: a `temperature: 0` in your config is silently ignored, so the config no longer says what ran. Only a captured
   request shows that.
2. **The tools that broke** hard-code what the API no longer accepts. OpenAI Evals sends `temperature=0, top_p=1`. autoevals
   forces a tool choice.
3. **There are three verdict mechanisms:**
   - Free text plus a parser: Promptfoo, Inspect, OpenAI Evals.
   - A tool call: Braintrust, LangSmith.
   - Native structured output: Pydantic Evals, DeepEval.

   The parsers range from robust (JSON schema) to fragile (the last line ending in Y/N).
4. **Default judge context differs.** Promptfoo, and Pydantic Evals by default, grade the reply *without the customer's question*.
   For a support reply, "is this a good answer?" can't be judged without the question. This is a default worth calling out.
5. **`max_tokens` ranges from 1,024 to 128,000.** It is harmless for cost (you pay for tokens used), but a low cap plus adaptive
   thinking can truncate verdicts.
6. **Only Inspect has pass@k / pass^k built in.** Everyone else gives repeats at best. Langfuse doesn't support repetitions
   within an experiment at all.
7. **Phoning home by default:**
   - Promptfoo telemetry and update checks.
   - DeepEval PostHog telemetry.
   - LangSmith uploads evaluator traces even with `upload_results=False`.
   - LiteLLM (via autoevals) fetches a cost map from GitHub.
   - Braintrust's autoevals sends judge calls to Braintrust's gateway with `OPENAI_API_KEY` unless `init()` is called.
8. **Agent trajectories need glue everywhere.** No tool sees `supportdesk.run`'s tool calls automatically:
   - Promptfoo and Pydantic Evals want OpenTelemetry spans.
   - Inspect wants its own model/tool APIs.
   - The rest take whatever you put in the output or metadata.

## Decisions for P9 (not needed now)

- **OpenAI Evals:** the spike already runs on 3.14 with 2.0.0.post1 + `claude_fn.py`. Default: keep that, and state in the post
  that the latest release doesn't install on 3.14.
- **Braintrust:** `litellm.drop_params` (process-wide) vs a custom scorer that calls the Anthropic SDK directly. Default: custom
  scorer. It avoids the translation layer, and it is what most users would end up writing.
- **Langfuse:** the Cloud judge can't route through local Vessel (it needs a public https URL). Self-host in Docker with Vessel's
  host allowlisted (`LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST`), or accept no capture for Langfuse's judge. Both need you to
  create an account.

## Repo traps found

- `uv init` inside `tools/<name>` joins the root workspace unless `--no-workspace` is passed. Two agents hit this; the root files
  are restored.
- The root project's name `evals` shadows PyPI's `evals` (OpenAI Evals) inside a workspace.
- pyright walks up to the root config, whose venv lacks the tool's packages. Each tool project carries its own `[tool.pyright]`.
- Windows: DeepEval needs `PYTHONUTF8=1`. Promptfoo only resolves paths when run from inside `tools/promptfoo/`.
