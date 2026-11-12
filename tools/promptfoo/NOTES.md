# Promptfoo: P0 spike notes

## Install

- **Promptfoo 0.125.1**, the latest on npm on 2026-11-12. It is pinned exactly in `package.json` as a devDependency. The CLI is `./node_modules/.bin/promptfoo`; nothing is installed globally.
- **Node v24.21.0, npm 11.19.0.** Promptfoo requires Node >=22.22.0. `npm install` took about 4 minutes and pulled 615 packages. npm skipped esbuild's postinstall (the `allowScripts` warning), which did not affect `eval`.
- Promptfoo bundles **Anthropic JS SDK 0.133.0**; the captured `User-Agent` is `Anthropic/JS 0.133.0`.
- **Python provider interpreter: 3.14.7.** This is proven, not assumed: `provider.py` returns `sys.version` in its response metadata, and every result row shows `3.14.7 ... [MSC v.1944 64 bit (AMD64)]`.
  - Auto-detection would probably not pick 3.14 (an inference, not tested). The documented order is `pythonExecutable` → `PROMPTFOO_PYTHON` → `where python` (Store stubs filtered out) → `python`/`python3`/`py -3`/`py`. On this machine, `where python` lists `C:\Program Files\Python310\python.exe` (3.10.4) first.
  - Configured with `config.pythonExecutable: ../../.venv/Scripts/python.exe` on the provider, which points at the repo-root uv venv.
  - **The relative path resolves against the working directory, not the config file.** Run from `tools/promptfoo/`, it works. Run from the repo root with `-c tools/promptfoo/promptfooconfig.yaml`, it errors: `Python 3 not found. Tried "../../.venv/Scripts/python.exe"`.
  - So always run from `tools/promptfoo/`, or set `PROMPTFOO_PYTHON` or an absolute path per machine. The `file://provider.py` path, by contrast, does resolve from the config directory.
- `provider.py` passes `uv run ruff check tools/promptfoo` and `uv run ruff format --check tools/promptfoo`.
- Run it from `tools/promptfoo/` with the key loaded per command (see the task brief):
  ```
  PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_DISABLE_SHARING=1 \
  PROMPTFOO_DISABLE_REMOTE_GENERATION=1 PROMPTFOO_CONFIG_DIR="$PWD/.promptfoo" \
  ./node_modules/.bin/promptfoo eval --no-share [--repeat 2 --no-cache] [-o out.json]
  ```

## Judge on claude-opus-5-5

- **It worked first time.** Run 1 used a plain `llm-rubric` with grader `anthropic:messages:claude-opus-5-5`, `apiBaseUrl` pointed at Vessel, and the `X-Vessel-Session` header. Both calls returned HTTP 200, with no errors and no 400s.
- **Why:** Promptfoo 0.125.1 has a Claude capability table with an explicit "Claude Opus 5.5" entry: `samplingParamsDeprecated`, `alwaysOnAdaptiveThinking`, `forcedToolChoiceUnsupported`. It also has a regex fallback that treats any `claude-*-5+` id as sampling-deprecated. The Anthropic provider then strips the request parameters that would 400:
  - **temperature / top_p / top_k:** dropped. Verified empirically: with `ANTHROPIC_TEMPERATURE=0` the CLI printed `temperature, top_p, and top_k are not supported on Claude Opus 5.5 and will be omitted. Remove these sampling parameters from your config (or unset ANTHROPIC_TEMPERATURE) to silence this warning.` Vessel request 10105 has no `temperature` key. Without that env var, the default would have been temperature 0 on older models.
  - **Forced `tool_choice` (`any`/`tool`):** omitted with a warning. Seen in source, not exercised; the grader sends no tools anyway.
  - **`thinking: {type: "disabled"}`:** omitted with the warning `Adaptive thinking is always on for Claude Opus 5.5. thinking.type "disabled" has been omitted.` **`type: "enabled"` with a budget:** converted, with a warning to use `effort`. Both from source.
  - `config.extra_body` is merged in after sanitising, so it is the only way to bring a 400 back.
- No workaround was needed.

## What the built-in judge sends

Captured from Vessel request **10086** (good case) and **10085** (bad case). Session 141 is `p0-spike` and the tag is `promptfoo`. The verbatim prompt is in `judge_prompt.txt`.

- **Body keys:** exactly `model, system, max_tokens, messages, stream`.
  - `model`: `claude-opus-5-5`
  - `system`: one text block, Promptfoo's `DEFAULT_GRADING_PROMPT` with two few-shot examples
  - `max_tokens`: **2048**. The default is 1024, raised to 2048 because Opus 5.5 always thinks adaptively. Override with `max_tokens` or `ANTHROPIC_MAX_TOKENS`.
  - `messages`: one user turn, `<Output>\n{{output}}\n</Output>\n<Rubric>\n{{rubric}}\n</Rubric>`
  - `stream`: `false`
- **Not sent:** temperature, top_p, top_k, tools, tool_choice, thinking, output_config, metadata, and any `anthropic-beta` header. Headers sent: `anthropic-version: 2023-06-01` and `x-vessel-session: p0-spike`.
- **Thinking still happens.** No `thinking` field is sent, but the model thinks adaptively anyway. The good-case response has a `thinking` block with empty text plus a signature, and 98 thinking tokens. The bad case had 0.
- **The judge never sees the customer message.** The default template contains only the Output and the Rubric. To include the input, reference `{{message}}` in the rubric text, or override `rubricPrompt` (`options.rubricPrompt` in `defaultTest`, or `rubricPrompt` on the assertion).
- **Free-text output, no structured output.** The system prompt asks for `{reason: string, pass: boolean, score: number}`. Parsing works like this:
  - `extractJsonObjects(text)` takes the first JSON object.
  - The `pass` boolean decides; the strings `true`/`yes`/`pass`/`y` are also accepted. If `pass` is missing, a finite `score` decides (`> 0`, or `>= threshold`). With a `threshold` set, it needs both `pass` and `score >= threshold`.
  - `reason` becomes the assertion reason. If no JSON is found, or there is neither a pass nor a score, it is a grader failure (a fail, not an error).
- **Structured output is opt-in on the grader provider:** `config.output_format` becomes `output_config.format` plus the `structured-outputs-2025-11-13` beta. Not used here.
- **All Vessel request ids from this spike (all 200):**
  - 10085, 10086: run 1
  - 10087, 10088: run 2, the identical rerun
  - 10091, 10092, 10095, 10096: the no-header cache test, tag `promptfoo-cachetest`
  - 10097–10100: `--repeat 2`
  - 10105: the `ANTHROPIC_TEMPERATURE=0` check

## Verdicts

- **good → PASS** (score 1). Reason: "confirms a full refund of $42.00 for order A123... The '3-5 business days' note is a timing detail about that same refund, not a separate offer".
- **bad → FAIL** (score 0). Reason: "refuses the refund ... It also offers a 50% discount code (SAVE50), which goes beyond the refund."
- These matched expectations in all 4 runs and in both repeats.
- `promptfoo eval` exits with code **100** when any assertion fails, so the expected `bad` fail makes the process exit non-zero.

## Case / grader / run

- **Case:**
  - Each case is one entry in `tests:`, with `description` (the case name), `vars`, `assert`, `options` and `metadata`.
  - Shared values go in `defaultTest:`. Here that holds the `message` var, the grader and the assertion.
  - The SUT is a `providers:` entry (`file://provider.py`, `call_api(prompt, options, context)` returns `{"output": ...}`).
  - The input is a `prompts:` template (`"{{message}}"`).
  - A run is the matrix prompts × providers × tests (× repeats).
- **Grader:**
  - An `assert:` item: `type: llm-rubric`, with `value:` holding the rubric. `threshold` is optional.
  - The judge model is set by `provider:` on the assertion, then `options.provider` on the test, then `defaultTest.options.provider`. The most specific wins.
  - A `--grader` CLI flag also exists. Where it sits relative to the config keys was neither documented nor tested.
  - The provider object is `{id: anthropic:messages:claude-opus-5-5, config: {apiBaseUrl, headers, ...}}`.
  - Custom prompt: `rubricPrompt`. Code graders: `type: python`, `javascript` or deterministic types.
- **Run:**
  - An "eval", started by `promptfoo eval` and identified by an eval id such as `eval-w3n-2026-11-12T12:21:47`.
  - Results go in SQLite at `$PROMPTFOO_CONFIG_DIR/promptfoo.db`: here `tools/promptfoo/.promptfoo/promptfoo.db`, by default `~/.promptfoo/promptfoo.db`.
  - `promptfoo view` opens the local web UI over that DB; `promptfoo list evals` lists runs. `-o run.json` (or csv/html/jsonl/junit.xml) exports a run. `--no-write` skips the DB.
  - Each result row carries `testIdx`, `promptIdx`, `repeatIndex`, `success`, `score`, `response` (including provider `metadata`) and `gradingResult.componentResults[]`, whose `metadata.renderedGradingPrompt` holds the exact judge prompt.
- **Repeats (tried: `--repeat 2 --no-cache`):**
  - 4 result rows and 4 judge calls.
  - Each row has `repeatIndex` 0/1 and a `repeatGroupId` (`test-0-vars-0`).
  - Gotcha: `testIdx` is renumbered 0..3 under repeat, so it is not a stable case id. Group by `repeatGroupId` or `description`.
  - Pass/fail totals count every repeat (2 passed, 2 failed).
  - The CLI prints `Repeat stability: 2 repeated groups; 0 unstable`. The JSON has `results.repeatStability.groups[]` with `repetitions`, `passed`, `failed`, `passRate`, a 95% `passRateConfidenceInterval` and an `unstable` flag.
  - There is no native pass@k or pass^k; derive them from those groups. Repeats get per-repeat cache namespaces, so use `--no-cache` for fresh trials.
- **Trajectory / tool calls (docs only, not tried):**
  - The `trajectory:tool-used`, `trajectory:tool-args-match`, `trajectory:tool-sequence`, `trajectory:step-count` and `trajectory:goal-success` (LLM) assertions read **OpenTelemetry spans**, not provider output.
  - They need `tracing: {enabled: true, otlp: {http: {}}}`, which starts a built-in OTLP receiver on port 4318. The Python provider must take `context["traceparent"]` and emit child spans with the OTel SDK (protobuf exporter), carrying `tool.name` / `tool.arguments` attributes.
  - The simpler glue: return the trajectory in the provider's `metadata` and check it with a `javascript`/`python` assertion; the JS assertion context has `metadata`. JS assertions also get `context.trace.spans`.

## Friction / surprises

- **Phones home by default.** Telemetry needs `PROMPTFOO_DISABLE_TELEMETRY=1` and the npm update check needs `PROMPTFOO_DISABLE_UPDATE=1`; both are documented. `PROMPTFOO_DISABLE_SHARING=1` and `PROMPTFOO_DISABLE_REMOTE_GENERATION=1` (red-team) exist in `dist` but are not on the CLI docs page. I also passed `--no-share`; the debug log shows `Wants to share: false`.
  - The config dir gets a `promptfoo.yaml` with a generated user `id`.
  - Every eval prints `» Feedback: https://promptfoo.dev/feedback`. I saw no cloud or login nudges in `eval`.
  - `promptfoo share` was never run.
- **Writes outside the project by default.** The DB, cache and per-run **debug logs** (always written) go to `~/.promptfoo`; `PROMPTFOO_CONFIG_DIR` moves them. The repo `.gitignore` already covers `.promptfoo/`. A grep of the logs and DB for `sk-ant` and `x-api-key` found nothing.
- **Grader responses were not cached between runs.** Two identical consecutive evals made 4 fresh judge calls, with and without the session header. Per source:
  - Any custom `headers` on the Anthropic provider, including `X-Vessel-Session` or `ANTHROPIC_CUSTOM_HEADERS`, disable its response cache entirely.
  - Without headers, an **unlabeled** provider only caches in-process (ephemeral). Disk caching across runs happens only when the grader provider has a `label`.
  - To be safe, disable it explicitly: `--no-cache`, `PROMPTFOO_CACHE_ENABLED=false` or `evaluateOptions.cache: false`. Clear it with `promptfoo cache clear`. The cache lives in `$PROMPTFOO_CONFIG_DIR/cache/cache.json`.
- **The Python provider's responses are written to the disk cache,** but the key includes `__evalId`, `__evalStepId` and `__repeatIndex`, so no cross-run hits (`response.cached: false` on rerun). The cache just grows.
- **Windows path bugs:**
  - An absolute `file://E:/.../provider.py` was joined onto the config dir (`...\scratchpad\E:\Code\...` gave `[Errno 22] Invalid argument`). Keep provider paths relative to the config file.
  - `pythonExecutable` resolves from the working directory (see Install).
  - The CLI printed an error-log path with `\t` rendered as a tab (`E:\Code\evals<TAB>ools\...`).
- The default judge prompt omits the input (see above). That is easy to miss when writing rubrics that refer to "the request".
- Adaptive thinking costs tokens even though no `thinking` param is sent (98 reasoning tokens on the good case). `effort` is the documented knob (`config.effort`, which becomes `output_config.effort`).
- Each Python provider call takes about 410 ms (a persistent worker, `persistent_wrapper.py`).
- `npm install` is heavy: 615 packages, about 4 minutes, plus 2 deprecation warnings.

## Files

- `tools/promptfoo/package.json`: pins `promptfoo` 0.125.1 as a devDependency
- `tools/promptfoo/package-lock.json`
- `tools/promptfoo/promptfooconfig.yaml`: two cases, one `llm-rubric` judge on claude-opus-5-5 through Vessel
- `tools/promptfoo/provider.py`: canned-reply Python provider that reports `sys.version`
- `tools/promptfoo/judge_prompt.txt`: verbatim system and user text of the good-case judge call (Vessel 10086)
  - The `# Vessel request ...` header line, the `=== SYSTEM ===` and `=== USER ===` markers, and the final newline are framing added by me, not part of the prompt.
- `tools/promptfoo/NOTES.md`: this file
- Gitignored and generated: `node_modules/`, `.promptfoo/` (`promptfoo.db`, `cache/`, `logs/`, `promptfoo.yaml`)
