# OpenAI Evals (open-source `evals` / `oaieval`): P0 spike notes

Run from `tools/openai-evals`, with `ANTHROPIC_API_KEY` loaded as in the brief:

```sh
PYTHONUTF8=1 PYTHONPATH=. OPENAI_API_KEY=placeholder-not-a-key OPENAI_BASE_URL=http://127.0.0.1:9/v1 EVALS_THREADS=1 \
  uv run oaieval claude-opus-5-5 refund-judge --registry_path registry --record_path logs/refund-judge.jsonl
```

Swap `claude-opus-5-5` for `claude-opus-5-5-forward-sampling` to reproduce the 400.

## Install on 3.14

**Partly. The latest release doesn't resolve on 3.14, so uv silently installs a 2023 one.**

- `uv add evals` on CPython 3.14.7 resolved **`evals==2.0.0.post1`** (2023-12-21) rather than the latest **`3.0.1.post1`** (2024-05-01).
  uv backtracked without a warning.
  - 159 packages, including `tensorflow==2.22.0rc0`, a pre-release and the only TF build with cp314 wheels. Also spacy,
    snowflake-connector, langchain, and openai 3.49.0.
  - It imports, and `oaieval` runs.
- Exact failure for the latest (`uv pip compile` with `evals==3.0.1.post1`, `--python-version 3.14`):
  `No solution found when resolving dependencies: ... evals>=3.0.1.post1 depends on google-generativeai, we can conclude that
  evals>=3.0.1.post1 and all versions of spacy-universal-sentence-encoder are incompatible ...` plus
  `hint: You require CPython 3.14 (cp314), but we only found wheels for tensorflow (v2.21.0) with the following Python ABI
  tags: cp310, cp311, cp312, cp313`.
  - Mechanism: `spacy-universal-sentence-encoder` → `tensorflow`. The only cp314 TF, 2.22.0rc0, needs `protobuf>=6.31.1`, while
    `google-generativeai` → `google-ai-generativelanguage==0.6.15` needs `protobuf<6`.
- **3.13 check** (scratch venv, CPython 3.13.15): `evals==3.0.1.post1` installs and imports, with tensorflow 2.20.0 and
  protobuf 5.29.6. So it's a 3.14 problem.
- GitHub `main` has only had maintenance commits since 2024; the last was 2026-04-14. The README points to the hosted product. It
  still lists `google-generativeai` and `spacy-universal-sentence-encoder` as deps, so main should hit the same 3.14 conflict.
  That's inferred; I didn't install main.
- uv gotchas:
  - Plain `uv init` in `tools/<name>` auto-joins a root `[tool.uv.workspace]`. Use `--no-workspace`.
  - The root project is itself named `evals`, so as a workspace member `uv add evals` resolved to `evals @ file:///E:/Code/evals`
    (the root!). The root was restored.

## Judge on claude-opus-5-5

There's **no Claude completion fn in 2.0.0.post1**, so I wrote one with the documented extension point (`docs/completion-fns.md`):
`claude_fn.py:ClaudeCompletionFn`, registered in `registry/completion_fns/claude.yaml` and loaded via `--registry_path`. It's about
50 lines.

- **The eval layer always passes OpenAI sampling params.** `PromptFn.__call__` (`evals/elsuite/utils.py`) calls every completion fn
  with `max_tokens=1024, temperature=0, top_p=1, frequency_penalty=0, presence_penalty=0, n=1`.
  - `top_p`, `frequency_penalty` and `presence_penalty` are literals.
  - `temperature` can be changed through the eval's `eval_kwargs`, but not removed.
  - So whether `temperature` reaches Claude is decided entirely by the completion fn.
- Forwarding them as a naive port would (registry entry `claude-opus-5-5-forward-sampling`):
  - As SDK kwargs: `TypeError: Messages.create() got an unexpected keyword argument 'temperature'`. anthropic 2.8.0 dropped the
    parameter, so this fails client-side and nothing is sent.
  - As raw body (`extra_body`): HTTP 400
    `{'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'temperature: Extra inputs are not permitted'}}`.
    Vessel 10125 (bad) and 10126 (good).
- **Workaround used:** the completion fn sends only `max_tokens`. Vessel 10127 and 10128 returned 200 first time.
- **Shipped Claude path, 3.0.1.post1 on 3.13 only (tried):** `SolverCompletionFn(solver=AnthropicSolver(model_name=...))` as the
  judge for the same eval fails before sending anything:
  `AssertionError: Unexpected prompt role ordering` (`solver_completion_fn.py:53`). It requires the first message to be `system`,
  but model-graded `classify()` sends one `user` message.
  - Called directly with a system-first prompt, the next error is `TypeError: Cannot instantiate typing.Union`. `AnthropicSolver`
    builds `MessageParam(...)` / `ContentBlock(...)`, which are Union aliases in current anthropic SDKs.
  - Source: it would also pass PromptFn's `max_tokens` on top of its own (a duplicate kwarg), and forward
    `temperature/top_p/frequency_penalty/presence_penalty/n` into `messages.create`.
  - It has no `base_url`/headers option; only the `ANTHROPIC_BASE_URL` env var works, so it can't send `X-Vessel-Session`.
  - Its registry lists only claude-3 models.
  - `anthropic_solver.py` in 3.0.1.post1 is byte-identical to `main`.

## What the built-in judge sends

The built-in judge is `ModelBasedClassify` with the built-in `closedqa` spec. It makes **one call per case**, with no
step-generation call. The good case's prompt is verbatim in `judge_prompt.txt`.

**What evals hands the completion fn** (eval layer, from source):

- Prompt: the closedqa template, filled with `{input}`, `{completion}` and `{criteria}`, as `[{"role": "user", "content": ...}]`.
  The spec already contains its chain-of-thought "Y"/"N" instruction. No `eval_type` is set in our eval YAML, so nothing else is
  appended.
- Kwargs: `max_tokens=1024, temperature=0, top_p=1, frequency_penalty=0, presence_penalty=0, n=1`.

**What went on the wire** (our glue; Vessel 10128 = good, 10127 = bad; tag `openai-evals`; session 141 `p0-spike`):

| Param | Sent |
| --- | --- |
| model | `claude-opus-5-5` |
| max_tokens | `1024` (from evals' `eval_kwargs` default) |
| system | none. A single `user` message, with string content |
| temperature / top_p | dropped by our completion fn (they cause the 400 above) |
| frequency_penalty / presence_penalty / n | dropped (not Anthropic params) |
| tools / tool_choice | none |
| thinking | none |
| output_config | none. **Free text** |

**Verdict parsing** (`classify_utils.get_choice`, `cot_classify`):

1. Split the reply into lines and **reverse** them.
2. Strip punctuation from each line.
3. The first line where `match_fn="starts_or_endswith"` matches a choice in `choice_strings: 'YN'` gives the verdict.
4. Score with `choice_scores` (`Y` = 1.0, `N` = 0.0). With no match the verdict is `__invalid__`, which scores the minimum (0).

Fragile by construction: if the last non-empty line starts or ends with a capital `Y` or `N` (e.g. "No extra offers were made."),
that line is taken as the verdict. `metaeval: true` adds `metascore = (choice == sample["choice"])`.

## Verdicts

- good (`refund-judge.dev.0`) → **Y** (pass, score 1.0)
- bad (`refund-judge.dev.1`) → **N** (fail, score 0.0)
- Final report: `{'counts/N': 1, 'counts/Y': 1, 'score': 0.5, 'metascore': 1.0}`

## Case / grader / run

- **Case:** one JSONL row in `registry/data/refund-judge/samples.jsonl`, with fields `input`, `completion` and `choice`.
  - The canned output is native: when `completion` is present, `ModelBasedClassify` skips the policy call ("test_sample already
    has completion, skip.").
  - `choice` is the expected label for `metaeval`.
  - Cases have no names or IDs: `sample_id` is `<eval>.<split>.<line index>`.
- **Grader:**
  - The eval class `evals.elsuite.modelgraded.classify:ModelBasedClassify`, registered in `registry/evals/refund-judge.yaml`.
    Args: `samples_jsonl`, `modelgraded_spec: closedqa`, `modelgraded_spec_args.criteria`, `metaeval: true`.
  - The judge template is the built-in `registry/modelgraded/closedqa.yaml`, with keys `prompt`, `choice_strings`,
    `choice_scores`, `eval_type: cot_classify` and `input_outputs`.
  - The judge model is the **last** completion fn given to `oaieval` (`oaieval policy_fn,judge_fn ...`). Here a single fn plays
    both roles, and the policy is never called.
- **Run:** `oaieval <completion_fn> <eval> --registry_path ... --record_path ...`. Use `EVALS_THREADS` for concurrency.
  Samples are shuffled even with one thread (bad ran first).
- **Results/logs:**
  - One JSONL file at `--record_path`. The default is `/tmp/evallogs/<run_id>_<fn>_<eval>.jsonl`.
  - It holds a `spec` line, a `final_report` line, then per-sample `sampling` events (prompt, sampled text, usage) and `metrics`
    events.
  - View it with a text editor or your own script; the docs mention the third-party `naimenz/logviz`.
  - Other sinks: Snowflake (`--no-local-run`) and HTTP (`--http-run`).
- **Repeated trials** (source, not tried): there's no epochs/repeats option in `oaieval`. `n` is always 1 for model-graded evals,
  and `multicomp_n` is for comparing several completions, not for repeats. To get k trials, duplicate rows or run k times.
- **Trajectory/tool calls** (source, not tried): the CompletionFn protocol is text or chat in, `list[str]` out, with no tool-call
  structure.
  - `Solver`s carry a `TaskState` with messages, for multi-turn evals.
  - Anything else has to be logged by hand via `record_sampling` / `record_event`.
  - No native tool-call trajectory access.

## Friction / surprises

- **Requires an OpenAI key even for a Claude-only run.**
  - `evals/registry.py:19` builds `OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))` at import, so a missing key gives
    `openai.OpenAIError: Missing credentials. Please pass an api_key ...`.
  - Any non-GPT completion-fn name then triggers `client.models.list()` against OpenAI. The failure is caught, and the run logs
    "Could not fetch API model IDs".
  - Workaround: a placeholder key plus `OPENAI_BASE_URL=http://127.0.0.1:9/v1`, so nothing leaves the machine. It costs about 7s
    of retries.
- **Install:** an old version is installed silently on 3.14, and the dependency tree is huge (tensorflow, spacy, snowflake) for a
  model-graded eval.
- **The CompletionFn interface is OpenAI-shaped** (`frequency_penalty`, `presence_penalty`, `n`, chat-dict prompts). Any non-OpenAI
  judge needs glue, and the shipped Anthropic glue is broken (see above).
- **Run mechanics:**
  - `PYTHONPATH=.` is needed to import a custom completion fn, because the console script doesn't add the cwd.
  - The registry also scans `~/.evals/`.
  - Windows: `--record_path` with an absolute drive path fails with
    `blobfile._common.Error: Unrecognized path: 'C:/...'`. Use relative paths.
- **Project status:** effectively unmaintained. The latest PyPI release is from May 2024, and the README nudges you to the hosted
  OpenAI Evals product.
- **pyright:**
  - `evals` is untyped: the `record_sampling` import is partially unknown, so it has a targeted ignore.
  - The root `[tool.pyright]` venv setting made pyright resolve against the root env; fixed with a tool-local `[tool.pyright]`.

## Files

- `pyproject.toml`, `uv.lock`: standalone uv project, Python 3.14, `evals` (resolved 2.0.0.post1) + `anthropic` 2.8.0, tool-local
  pyright config
- `claude_fn.py`: `ClaudeCompletionFn`, the Anthropic glue through Vessel
- `registry/completion_fns/claude.yaml`: `claude-opus-5-5` and `claude-opus-5-5-forward-sampling`
- `registry/evals/refund-judge.yaml`: the eval (ModelBasedClassify + closedqa + criteria + metaeval)
- `registry/data/refund-judge/samples.jsonl`: the 2 cases (input, canned completion, expected choice)
- `judge_prompt.txt`: verbatim judge prompt for the good case
- `logs/refund-judge.jsonl`, `logs/refund-judge.console.txt`: the recorded passing run
- `logs/forward-sampling.jsonl`, `logs/forward-sampling.console.txt`: the 400 run
- `NOTES.md`: this file
