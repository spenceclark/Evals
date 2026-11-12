# Findings log

One entry per phase: what we tried, what worked, what didn't, and what was surprising. Write it as you go, not afterwards.
This log is raw material for the posts. Where a finding has a fuller write-up, the entry links to it.

---

## P0: Setup and compatibility spike (2026-11-12)

Full table: [p0-compat.md](p0-compat.md). Notes per tool: `tools/<name>/NOTES.md`.

### Tried

- Root `uv` project on CPython 3.14.7, with ruff and strict pyright. Empty `evalkit` and `supportdesk` packages.
- Vessel as the single route for every LLM call:
  - Anthropic at `/b/anthropic`.
  - Ollama at `/b/ollama/v1`, OpenAI-compatible.
  - Path tags for each tool, and one session (`p0-spike`) for the spike.
- Checked end to end that Opus 5.5 replies, and that local `Qwen3.5-9B-Unsloth:IQ4` makes tool calls through Vessel.
- The same check in all 8 tools: two canned replies (`good` / `bad`) to one refund request, one LLM-judged rubric, and
  `claude-opus-5-5` as judge. Each tool has its own isolated uv project.
- Read each judge's real request back from Vessel and filled the compat table from the capture, not from the tool's docs.

### Worked

- 7 of 8 tools ran an Opus 5.5 judge through Vessel:
  - Promptfoo, Inspect, Pydantic Evals and DeepEval worked first try.
  - OpenAI Evals, Braintrust and LangSmith needed workarounds.
- Langfuse ran locally only with a code evaluator, because its LLM judge runs on its server.
- Reading captures settled what the agents' notes only guessed at: parameters, verdict mechanism, and whether the judge sees the
  question.
- Root ruff and pyright, plus pyright in each tool project, all ended with 0 errors.

### Didn't work (and the workaround)

- **OpenAI Evals:**
  - The latest release (3.0.1) can't be installed on 3.14 because of its tensorflow/protobuf pins. uv silently picked
    2.0.0.post1 (Dec 2023) instead.
  - It hard-codes `temperature=0, top_p=1`, which returns 400 on every current Claude model (Vessel 10125).
  - Workaround: a ~50-line Claude completion function that sends only `max_tokens`.
- **Braintrust autoevals:**
  - It forces `tool_choice`, which Opus 5.5 rejects. Workaround: the process-wide `litellm.drop_params = True`.
  - Its LiteLLM path didn't forward the `X-Vessel-Session` header, so its traffic landed in Vessel's unnamed session (124)
    instead of `p0-spike`.
- **LangSmith:** without an account it tried to upload evaluator traces and got 401s. Fixed with
  `disable_evaluator_tracing=True`.
- **Langfuse:** its LLM judge is server-side only, and it has no repetitions per experiment (issue #5855).
- **Environment:**
  - `ANTHROPIC_API_KEY` was set at Windows User scope after the shell started, so it isn't in the process environment. Load
    it for each command.
  - `uv init` inside `tools/<name>` silently joined the root workspace. Fix: `--no-workspace`.
  - pyright walked up to the root config. Fix: a `[tool.pyright]` block in each tool's project.
  - VS Code/Pylance uses one interpreter per workspace folder, so the tool spikes showed false import errors. Fix:
    `evals.code-workspace`, a multi-root workspace with each tool as its own folder.

### Surprising

- **Libraries now silently drop parameters Claude rejects:** `temperature`, forced `tool_choice`, disabled thinking. The
  upside is that they work. The downside is that `temperature: 0` in a config file no longer describes what ran. Only the
  captured request does.
- **The judge's default context differs between tools.**
  - Promptfoo's default rubric prompt doesn't send the customer's question.
  - Pydantic Evals doesn't either, unless you pass `include_input=True`.

  A support reply can't be judged without the question.
- **There are three verdict mechanisms:**
  - free text plus a parser (Promptfoo, Inspect, OpenAI Evals);
  - a tool call (Braintrust, LangSmith);
  - native structured output (Pydantic Evals, DeepEval).

  The parsers range from a JSON schema down to "last line ends in Y or N".
- **`max_tokens` on judge calls ranges from 1,024 to 128,000.** Opus 5.5's adaptive thinking can't be turned off, so a low cap
  can truncate a verdict.
- **Only Inspect has pass@k / pass^k built in.** Everyone else offers repeats at best.
- **Phoning home is the default** in Promptfoo, DeepEval, LangSmith and LiteLLM. autoevals also falls back to Braintrust's
  gateway with your `OPENAI_API_KEY` if you forget `init()`.
- **Judge calls cost less than planned.** They were $0.005–0.015 per call on toy inputs, against the $0.04 the budget assumed.
  Re-measure on real transcripts.

### Open / carried forward

- P9 decisions, each with a default, are listed in p0-compat.md: OpenAI Evals on 2.0.0.post1, a custom Braintrust scorer, and
  self-hosted Langfuse.
- Every tool needs glue to see an agent's trajectory. Decide in P3 how much of that the posts should show.

---

## P1: Thin slice end to end (2026-11-12)

Diagram, vocabulary map and baseline table: [p1-anatomy.md](p1-anatomy.md).

### Tried

- **`evalkit`** (~300 lines after `ruff format`):
  - `core.py`: types and an asyncio runner.
  - `graders.py`: one exact-match grader on `action`.
  - `__main__.py`: `run`, `report`, and `show`, which prints transcripts.
- **`supportdesk`:**
  - 9 hand-written orders and a pinned `TODAY`.
  - `get_order`, `issue_refund` and `close_ticket`.
  - A hand-written loop with Anthropic and OpenAI-chat adapters.
  - Oracle and null tasks.
  - `roundtrip.py`.
- **10 cases.** 3 refunded, 4 denied (window ×2, digital, already refunded), 2 answered, 1 needs_info.
- **Run order, cheapest and riskiest first:**
  1. oracle and null;
  2. one Haiku trial, then its round-trip;
  3. one Qwen trial, then its round-trip;
  4. all 10 cases on both models;
  5. a round-trip for all 20 trials.

### Worked

- **Oracle 10/10. Null 4/10, and exactly the four `denied` cases.** Checked per case, not just the total.
- **Haiku 10/10** for $0.0045, about 3 s per case.
- **Qwen 9/10**, about 8 s per case at concurrency 1.
- **The capture round-trip matches for all 20 trials in both wire formats.** The last call's request holds the conversation so
  far, and its response holds the closing tool call. Together they rebuild the recorded transcript exactly: ids, args and tool
  results. Vessel's records aren't truncated (`truncated=False`), so P7's "captures → cases" plan stands.
  Not covered: no trial had a tool error or several tool calls in one turn, so those shapes are untested until P2's fault
  injection.
- **Served-model check.** Both backends echo the requested id exactly (`claude-haiku-5-5`, `Qwen3.5-9B-Unsloth:IQ4`). The check
  is a plain set comparison.
- **Python 3.14:** PEP 758's unparenthesised `except OSError, subprocess.CalledProcessError:` passes ruff and pyright.

### Didn't work / had to change

- **The harness's error paths never fired in a real run.** No errors, no truncation, no refusals. `tests/test_core.py` now forces
  each path with fake tasks, no LLM: task raises, wrong served model, truncated, grader crash. It checks they land in the
  right file and in the right report count.
- **`show` and `report` would have crashed on a Windows console** (cp1252) as soon as a transcript contained an emoji. stdout is
  now reconfigured to UTF-8.
- **`action_match` passed when both sides were `None`.** A case missing its expected action would silently pass an agent that
  never closed. It now raises, so the trial lands in `errors.jsonl` as a labelling bug.

- **The capture check first failed on Qwen's S10.** The trial never called `close_ticket`, so there was nothing to compare.
  "No close on either side" is now a match.
- **Strict pyright and the SDKs:**
  - Effort has to be typed as a `Literal`, not `str`, or `output_config` fails type-checking.
  - The stop-reason mapping needed an explicitly typed table.
  - No `# type: ignore`s survived.

### Surprising

- **Qwen's one "failure" was arguably the right behaviour.** For S10 (no order ID) it asked the customer for the ID, in plain
  text, and ended its turn without calling `close_ticket`. The exact-match grader scores that as a failure because no
  structured claim was made. So this grader measures *protocol compliance*, not helpfulness. That's a blog point about what a
  grader actually measures.

  Deliberately left alone: the prompt wasn't tuned to fix it, because tuning the system under test to pass the eval is the
  trap. P2 decides whether "ended without closing" is a failure (likely yes: in production the ticket stays open).
- **Haiku invented a field.** It added a `reason` argument to `close_ticket` that isn't in the schema. The tool schemas don't
  set `additionalProperties: false` or strict mode, so the extra field passes silently into `output`. Consider strict tools in
  P2.
- **Number types differ by model.** Qwen sent `"amount": 12`, Haiku sent `12.0`. Python's `==` hides that; a grader that
  compares strings or serialised JSON would not.
- **Haiku's thinking is adaptive per step.** In the one capture inspected (Vessel #10138, S02), it thought on one of three
  steps and used zero thinking tokens on the others. Those
  thinking blocks have to be sent back unchanged, so the adapter keeps the provider-native message list separate from the
  normalised transcript. Two records per trial is a requirement, not a nicety.
- **Qwen spends its output on reasoning.** A bare "Say hi" used up all 200 `max_tokens` on reasoning and returned an empty
  `content`. Its `max_tokens` is now 8192. Its reasoning isn't sent back in the history; the OpenAI chat format has no slot
  for it.
- **The OpenAI chat format has no `is_error` on tool results.** An error is only visible in the content (`{"error": ...}`).
  The round-trip infers the flag from the content for that format. P7 will hit the same gap.
- **Vessel returns no request id to the client.** A trial is matched to its captures by session + tag + full-text search on
  the customer message. That works at k=1. At k>1, repeated trials of a case share the same message, so the match becomes
  ambiguous.
- **The null baseline is 40%.** A system that does nothing scores 40% on this case mix. Any headline number needs this floor
  next to it.

### Open / carried forward

- **Read every transcript.** This is a done-when item, and it's yours: `uv run python -m evalkit show runs/<run_id>` for the
  Haiku and Qwen runs.
- **k>1 trial ↔ capture join.** Add a per-trial marker before P2's repeated trials: a trial tag (`X-Vessel-Tags: sut,t-S01-3`)
  or a trial id in the session name.
- **Both models nearly saturate 10 easy cases.** As expected, the slice doesn't discriminate between models. P2's ~60 cases,
  fault injection and k>1 are what make the numbers mean anything.
- **Not yet in place:** outcome grading (refund made ≠ refund claimed), strict tool schemas, SDK retry counts in the trial
  record, and cost per row for the judge (no judge yet).
