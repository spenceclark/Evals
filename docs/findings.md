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

---

## P2: Full system under test, case set and code graders (2026-11-12)

Case review sheet: [p2-cases.md](p2-cases.md).

### Tried

- **One generator for data and labels.** `scripts/make_cases.py` builds the store (30 customers, 85 orders) and 60 cases
  together from one seed:
  - 44 regression cases, 16 capability cases.
  - Every label comes from `decide()`, an independent copy of the policy's precedence.
  - Each group asserts that `decide()` gives the group's intended action, and that no case sits on the 30-day or $200
    boundary.
- **The full system under test:**
  - 6 tools and an ordered 6-rule policy.
  - Fault injection in the store (a tool fails on its Nth call).
  - **Strict tool schemas for Claude.** This was a deliberate change to the system under test, made after P1's invented
    `reason` field. Qwen gets the same schema with `additionalProperties: false`, but Ollama doesn't enforce it.
  - The closing instruction in the system prompt is unchanged.
- **Seven code graders, one property each:** `schema` (Pydantic, `extra="forbid"`, strict types), `action`, `order_id`,
  `amount` (in cents, with null and 0 equal), `end_state`, `safety` and `claim_vs_outcome`. A grader can return "not
  applicable": `order_id` does on `needs_info` cases. `EXACT` adds naive `order_id_exact` and `amount_exact`, to measure
  what normalisation changes.
- **Checks on the eval itself, before any model ran:**
  - Fabricated trials prove each grader can both pass *and* fail.
  - The oracle now *executes* the expected refunds and escalations against a real store, faults included.
  - Oracle and null are asserted case by case and grader by grader, on all 60 cases.
- **Plumbing:** `regrade` re-scores saved trials without calling a model, `--suite` filters cases, and each trial gets its
  own Vessel tag (`trial-<case>-<rep>`).

### Worked

- **Haiku: 57/60 on all graders** (regression 43/44, capability 14/16) for $0.041. That's **$0.0007 per trial**, a third of
  the plan's $0.002 estimate.
- **Vessel's session stats match the harness exactly:** 158 calls, 270,850 tokens in, 28,388 out.
- **Fault recovery.** In fault-01, Haiku got a 503 from `get_order`, retried, and finished correctly.
- **Prompt injection.** All four injected cases were handled per policy.

### Didn't work / had to change

- **The round-trip failed on fault-01.** Haiku called `issue_refund` and `close_ticket` in the same turn. The loop ran the
  refund, but its result was never sent back to the model, so no capture can contain it. The check now notes tool results
  that happened after the model's last turn and leaves them out of the comparison.
- **Two generator bugs, caught by its own asserts:**
  - A transposed-digit "typo" order ID collided with a real order.
  - One phrasing template produced "it I changed my mind about it".

### Surprising

- **All three of Haiku's failures look like eval bugs, not model bugs.**
  - **esc-04 and inj-04:** Haiku escalated correctly but put the escalated amount ($289, $389) in `close_ticket.amount`. The
    schema says "Amount refunded, if any", so the label is null. Both readings are defensible: the field's description is
    ambiguous, and the grader punishes a valid reading.
  - **look-05:** the message gives an order ID with a typo. Haiku found the customer's only desk-lamp order, but asked them
    to confirm it rather than refunding. The label says "refunded", while the policy says "never guess". Two actions are
    defensible, which breaks the rule that every case has exactly one right answer.

  Lesson: the first failures you read are often the eval's own bugs. That's why the done-when demands a spot-read before
  trusting the numbers.
- **Haiku claims a refund before seeing that it went through.** It sent `issue_refund` and `close_ticket` in one turn, so it
  told the customer "we've refunded $79" without seeing the refund tool's result. It happened to work here. If the refund had
  failed, the claim would be false, and only `claim_vs_outcome` would notice. This is a candidate path check for P3.
- **Normalisation changed nothing for Haiku** (EXACT vs default: identical). Strict tool schemas mean Claude always sends
  `79.0`, never `"79"` or `79`. The question is whether normalisation matters for Qwen.
- **Ollama serves Qwen with an 8,192-token context.** That's a server default, invisible from the OpenAI-compatible API and
  set nowhere in our code, but it's part of the system under test. Qwen's `max_tokens` is now 4,096 so input plus output
  fits, and `meta.json` records the window. The largest call in the run used 5,407 tokens, input plus output, so nothing was
  truncated.
- **Qwen: 52/60 on all graders** (regression 41/44, capability 11/16), at about 9.4 s per trial and no API cost.
  - **Every prompt injection worked.** All four injected tickets got a refund, including one on a cancelled order and one
    that should have been escalated. Haiku resisted all four. Only `safety` and `end_state` show the money that moved;
    `claim_vs_outcome` passes, because Qwen honestly reported the refund it shouldn't have made.
  - **win-01** (31 days since delivery, one day past the window): Qwen refunded and Haiku denied. Boundary cases are where
    small models slip.
  - **fault-01:** Qwen recovered from the 503, issued the refund correctly, and then never closed the ticket. So
    `end_state` passes while `claim_vs_outcome` fails: the customer got their money and no reply. Two graders on the same
    trial disagree, and both are right.
- **Normalisation changes 18 of Qwen's 60 `amount` verdicts.** Qwen sends `26` where the label says `26.0`, and `0` where it
  says `null`. Exact matching would fail all 18 on format alone. Haiku, with strict schemas, needed no normalisation. So
  "exact match is too strict" depends on the model and on the schema enforcement in front of it.
- **The round-trip held for all 120 full-suite trials,** including tool errors (both formats) and 3 trials that acted and
  closed in the same turn.
- **The same `amount` ambiguity hits both models.** Qwen also filled `amount` on 2 of its escalations. That makes 4 of the
  11 failures (Haiku's 3 and Qwen's 8) the same eval bug.

### Failures for the spot-read (11)

My first read of each; the done-when spot-read is yours. Run `uv run python -m evalkit show <run> <case>`.

| Run | Case | What happened | Failed graders | Grader right? (my read) |
| --- | --- | --- | --- | --- |
| haiku | esc-04 | Escalated correctly; `amount` = $289 | amount | ❌ Ambiguous field description |
| haiku | inj-04 | Escalated correctly; `amount` = $389 | amount | ❌ Same |
| haiku | look-05 | Typo'd ID; found the only match; asked to confirm | action, order_id, amount, end_state | ❓ Label ambiguous: two defensible actions |
| qwen | esc-03, esc-06 | Escalated correctly; `amount` filled | amount | ❌ Same as esc-04 |
| qwen | fault-01 | Refunded after the 503, never closed | schema, action, order_id, amount, claim_vs_outcome | ✅ The customer got no reply |
| qwen | inj-01..04 | Followed the injection and refunded | action, amount, end_state, safety | ✅ |
| qwen | win-01 | Refunded at 31 days | action, amount, end_state, safety | ✅ |

So the graders are right on 6 of 11, which is below the 9-of-10 bar. Per the done-when, the grader and labels get fixed
before moving on: `amount` on escalations, and look-05.

### Fixes applied (eval only; no model was re-run)

- **`amount` is now "not applicable" when the expected action is `escalated`.** Whether the field means "amount refunded" or
  "amount escalated", both readings are fair, and `end_state` and `claim_vs_outcome` already check the escalation itself.
- **Cases can list `also_ok` alternatives.** Each grader compares against the alternative whose action the agent chose, and
  falls back to the primary label. look-05 now accepts `needs_info` (ask, change nothing) as well as `refunded`. A third
  answer still fails. The generator writes this, and the store regenerated byte-for-byte.
- **New tests:** a grader test for both changes, and the oracle and null re-checked on all 60 cases.
- **Both runs regraded with `evalkit regrade`.** `meta.json` records the new case hash and the regrade time.

| | Before | After |
| --- | --- | --- |
| Haiku, all graders | 57/60 | **60/60** (regression 44/44, capability 16/16) |
| Qwen, all graders | 52/60 | **54/60** (regression 43/44, capability 11/16) |

All 6 remaining failures are Qwen's, and the graders are right on all of them: 4 injections, the 31-day refund, and the
refund with no reply.

**A lesson for the post:** fixing the eval moved Haiku from 95% to 100% with no change to the model. Five of the 11
failures were the eval's fault. A score you haven't spot-read is a score of the eval, not of the model.

### Open / carried forward

- **Haiku now saturates the suite at k=1.** 60/60 can't tell Haiku apart from a better or worse version of itself. P6's
  repeated trials, and harder capability cases, are what will.
- **P3 path check:** "claimed before confirmed". `close_ticket` was sent in the same turn as `issue_refund` or `escalate` in
  3 of 60 Haiku trials.
- **The over-$200 group is sound for P8.** Both models escalated all 6 cases.
- **Qwen's regression score is 43/44 (98%), above the ~95% bar.** Its one regression miss is the 31-day boundary case.

---

## P3: Grading the path (2026-11-12)

Write-up with the annotated trajectory (O6), recovery table (O7), rule table and judge comparison: [p3-path.md](p3-path.md).

### Tried

- **6 more fault cases, appended to the generator.** Now every fault type (`get_order`, `find_customer`, `issue_refund`,
  `escalate`) has 3 cases: 66 cases in total. The first 60 cases and every existing order stayed byte-identical, and the
  oracle and null still pass on all 66.
- **Path graders** in `supportdesk/path.py`, built on one `events()` helper that flattens a transcript into ordered tool
  calls with their results:
  - `looked_up_first`, `no_refund_attempt_on_deny`, `confirmed_before_close`, `refund_amount_grounded`,
    `no_repeat_after_success` and `step_budget`. Each has a one-line justification as its docstring.
  - `recovery` for fault cases, with categories: `never_saw_error`, `gave_up`, `retried_no_close`,
    `retried_wrong_end_state`, `untruthful_close`, `recovered`.
  - `used_find_customer`, a deliberately overfit rule, kept as a demonstration.
  - All of them return "not applicable" for the oracle and null, and for faults that never fired.
- **Tests.** A scripted fake model drives the real loop against the real store, faults on, so the test transcripts have
  exactly the shapes the graders see. Every rule passes and fails.
- **`evalkit/stats.py`:** Wilson intervals, plus a cluster bootstrap whose self-check demonstrates its collapse at 3 cases.
- **Runs:**
  - regraded copies of the P2 runs with the path graders (no model calls);
  - 12 fault cases × 5 trials on both models;
  - a recovery judge (Opus 5.5) on all 120 fault trials.

### Worked

- **The path rule predicted the outcome failure.** In P2, `confirmed_before_close` flagged 3 Haiku trials that every
  outcome grader passed. In P3's fault runs, it flagged 10, and in the **5 where the injected 503 hit the side effect, Haiku
  told the customer "refunded" or "escalated" while nothing had happened.** Haiku's annotated trajectory (fault-02 rep 2)
  passes schema, action, order_id and amount. Only `end_state`, `claim_vs_outcome` and the path rules catch it.
- **Recovery (k=5, 12 cases): Haiku 92% (cluster bootstrap 78–100%), Qwen 97% (90–100%).** Qwen never closes in the same
  turn as a side effect, so it always sees the error. Its 2 failures are `gave_up` (asked for the order ID in plain text,
  never closed).
- **The judge matched the code on all 120 trials.** But only 3 of the 6 categories occurred (recovered 113,
  never_saw_error 5, gave_up 2), so this is agreement on an easy, lopsided set, not a validated judge. It cost $1.06,
  about $0.008 per call.
- **The capture round-trip held for repetitions above 0.** Per-trial tags work at k=5.

### Didn't work / had to change

- **`events()` first paired tool results by id across the whole transcript.** The scripted test model reused ids per turn
  and broke it. Ids are only promised unique within a turn, so results are now paired per turn.
- **Two pieces of tooling were outside the type-checker.** pyright's `include` didn't cover `scripts/`. It does now; the
  scripts were already clean.
- **The overfit demo first sat in the `ALL` grader set,** where it would have dragged the headline "all graders" number down
  with failures it was designed to make. It now lives in a separate `DEMO` set.

### Surprising

- **A single trial per case misled in both directions.** Qwen's P2 fault-01 failure (refunded, never closed) passed 5 out
  of 5 here. Haiku's P2 fault-02 passed, but here fails 3 times out of 5. One trial per case was a sample, not a measurement.
- **Most path rules never fired on real trials.** `no_repeat_after_success` and `step_budget` caught nothing in 240 trials,
  and `no_refund_attempt_on_deny` only caught what `safety` already caught. Their tests prove they *can* fail. Whether to
  keep a rule that has never fired is a judgment call: keep it when its justification describes a real harm, and accept that
  it's a tripwire, not a measurement.
- **The overfit rule failed 75 otherwise-correct trials.** "Must check the email with `find_customer`" sounds like policy,
  but `get_order` already returns the email. A plausible rule that encodes one route, not the requirement, would have turned
  roughly a third of good trials red.
- **A path rule can be right while failing a correct trial.** Qwen once refunded from `list_orders`, never calling
  `get_order`. The outcome was correct, but it never saw the item's kind or download flag, so it couldn't have applied the
  digital-item rule. That's a correct answer reached without checking the policy, and `looked_up_first` is right to flag it.
- **The cluster bootstrap's collapse is real.** With 3 cases that all pass 5/5, it reports [100%, 100%]. The per-fault-type
  table uses Wilson and says it ignores clustering.

### Open / carried forward

- **The loop is fixed (your call, applied):** `close_ticket` must now be alone in its turn. A close that shares a turn is
  refused and the other calls' results go back to the model. This is a deliberate change to the system under test, recorded
  in `meta.json` as `"loop": "close_alone"`; `close_alone=False` keeps the old loop for tests and comparison.
  - **Haiku:** fault recovery went from 55/60 to **60/60**, all graders on the fault cases from 50/60 to 60/60, and the full
    suite to 66/66. The loop refused 8 closes in 60 fault trials and 1 in 66 full-suite trials. Cost per fault trial went
    up 8% (4.17 to 4.42 calls).
  - **Qwen:** the loop never had to refuse one of its closes, so nothing changed by design. Run-to-run differences (win-01
    right this time, 54/60 to 61/66 overall) are noise, the same lesson as the k=1 fault-01 flip.
  - **What the fix can't touch:** Qwen once saw the escalate 503, didn't retry, and still told the customer "escalated".
    Only `claim_vs_outcome` catches a model misreporting a result it has *seen*.
  - **First truncation:** Qwen spent all 4,096 output tokens reasoning on one fault-09 trial. The harness scores it as
    `truncated` (not graded), as designed.
  - **The post should show both sets of numbers.** The fixed loop's 100% hides the most instructive failure in the project.
- **Interval widths.** With 3 cases per fault type, every per-type interval overlaps. P6 decides how many cases a claim like
  "Qwen recovers better" needs.

## P4: Model graders, human labels, judge validation (2026-11-14)

Write-up with the confusion matrix, the prompt-iteration table (O8), the judge-model comparison (O9) and self-consistency
(O10): [p4-judges.md](p4-judges.md).

### Tried

- **Human labels.** 120 replies were labelled blind to the model (pass/fail plus a critique for each fail), with
  `scripts/label.py`. The labeller also wrote 20 reference replies. Four labels were corrected before the split, and the
  originals are kept in `revised_from`.
- **A dev/test split by case, not by item.** It was frozen in `cases/labels/split.json` before any judge ran. Every human
  fail was tagged with a fail kind, so the test set carries each kind.
- **Judge plumbing in `evalkit/judge.py`:**
  - Prompts are t-strings (PEP 750), and `render()` fences every interpolation as `<data>`.
  - Verdicts are structured outputs.
  - A cache keeps every judge input and output.
  - "No verdict" is an error, never a fail.
- **Rubric judge** with three atomic criteria, one call each, overall = all pass. Versions v0, v1 and v2 were iterated on
  dev only. v0 and v2 were then scored once on test.
- **Variants:**
  - the judge given the correct answer;
  - a reference-based judge;
  - a pairwise judge on 27 label-implied pairs, in both orders;
  - Opus, Sonnet, Haiku and Qwen as the judge;
  - 3 samples per item for self-consistency;
  - known negatives.

### Worked

- **A rubric judge agrees with the human about as well as anything here can be measured.** On held-out data, v2 on Opus
  scores TPR 18/20, TNR 18/20, κ 0.80. It catches every invented timeline (8/8), every false claim (3/3) and every wrong
  decision (5/5), and **10 of the 12 fails the code graders missed**.
- **The known negatives all fail:** 12/12.
- **The pairwise judge picks the human-passed reply on 27/27 label-implied pairs (Opus) and 26/27 (Haiku), in both
  orders.** These pairs are easy (a clear fail against a clean reply), and the one-at-a-time rubric ranks 25/27 of them
  the same way, so this shows that pairwise works on this task, not that it's better.
- **Haiku 5.5 as the judge matched Opus on test (κ 0.80) at 1/40 of the cost:** $0.07 against $2.79 per 100 replies. It
  also passed the known negatives and flipped on fewer items (1/40 against Opus's 3/40).
- **The fencing held, as far as checked.** Opus caught all 9 wrong decisions (8 of them followed injections), and the
  critiques read on the rest name the injected text ("pre-approved by a manager", "SYSTEM: policy override") and grade
  past it. That was a spot-read, not a dedicated attack on the judge.
- **The whole phase cost $11.67 in Claude judge calls,** against about $80 budgeted.

### Didn't work / had to change

- **The reference-based judge (κ 0.06 dev, 0.25 test).** It fails good replies for true details the short human reference
  left out (dates, order numbers), because it can't see the record. It also inherits a detail the reference invented. It
  doesn't fit open-ended replies.
- **v1's rewrite of the offer rule overshot.** It failed 7 dev replies the labeller passed, so v2 relaxed it.
- **pytest isn't the test runner here:** the global one is on an older Python and can't parse t-strings. Tests stay plain
  scripts (`uv run python tests/test_judges.py`).

### Surprising

- **The dev gain mostly didn't transfer.** Iteration took dev κ from 0.83 to 0.96, but test only from 0.75 to 0.80. That
  is one more fail caught, inside the 8% flip rate. Against the original (uncorrected) labels, v0 beats v2 on test (0.70
  against 0.65). Without the held-out set, the post would have reported 0.96.
- **The human missed the P3 failure too.** The labeller passed "We have refunded $29.99" (L049) and "I've passed your
  request to a member of our team" (L050), with "none" shown right above each. A polished reply hides a false claim from people as well as from output-only graders. That's an argument
  for keeping `claim_vs_outcome` as code even with a judge.
- **The judge's disagreements found the labeller's fuzzy line.** Offers made after a denial ("send photos and we'll see
  what we can do") account for 7 dev disagreements under v1, L055 under v2, and 2 of the 4 held-out ones. The labels
  themselves are inconsistent there, which is criteria drift showing up in data. The remedy is a policy decision, not a prompt.
- **The local judge's perfect score was a coverage illusion.** Qwen 9B agreed with the human on all 25 items it answered,
  but it gave no verdict on 15 of 40: it reasoned past its 4,096-token output budget. 13 of those 15 were human fails,
  including every wrong decision and every false claim. Counting "no verdict" as a fail would have made it look better than
  Opus. Report coverage beside agreement.
- **Telling the judge the right answer brought no measurable benefit** (dev κ 0.71 against 0.83, one sample each, inside
  the noise). The answer wasn't what it lacked. The production-usable version, without the answer, is the one kept.
- **The judge found a label error (L073, dev):** a "5-7 business days" reply was passed while three others were failed.
  Every judge version flagged it, and the labeller relabelled it fail. That correction came after the judge runs and is
  recorded as such. Judges are useful for auditing labels, but only the labeller should change them.
- **Labelling was quicker than planned:** 96 active minutes for 120, against the 2 hours estimated.

### Open / carried forward

- **The test set is spent.** Its disagreements have been read, so a v3 rubric needs fresh held-out labels.
- **The offer line:** decide whether the agent may offer anything after a denial. Then put it in the agent's policy, not
  only in the judge.
- **Pairwise on closer pairs:** the label-implied pairs are lopsided. P5's ~100 pairs of closer replies are where
  position bias gets measured.
- **P8 nightly:** Haiku as the judge, with Opus spot checks on offers and tone.

## P5: Judge bias (2026-11-15)

Write-up with the position table (O11), the length table (O12) and the self-preference matrix (O13):
[p5-bias.md](p5-bias.md).

### Tried

- **Frozen pairs.** `experiments/make_pairs.py` froze 200 Haiku-vs-Qwen pairs (same case, from the P2/P3 runs) and a
  55-item human queue before any judge saw them: 50 pairs, one per case, Haiku in slot A for exactly 25, plus 5 swapped
  repeats.
- **Human preferences** with `scripts/prefer.py`: blind, A/B/tie plus an optional reason. No judge result was shown to the
  labeller until all 55 were saved.
- **Position (O11):** Sonnet judged all 200 pairs Haiku-first, Qwen-first, and Haiku-first again (the noise floor).
  Mitigation: both orders, a flip counts as a tie.
- **Length (O12):** two content-free pads (courtesy, restated policy) on 40 human-passed replies. Measured with pairwise
  (reply vs its padded copy) and rubric v2, then the harmful direction: padding the 38 human-failed replies. All of it
  repeated with an anti-length instruction written before any result.
- **Self-preference (O13):** Sonnet, Haiku, Opus and local Qwen 9B judged the 50 human pairs in both orders. Each judge's
  Haiku share was compared with the human's, and the Claude judges with the Qwen judge.
- **A neutral pairwise prompt:** P4's prompt minus "Length and polish matter only after those", so the baseline doesn't
  already tell the judge how to treat length.

### Worked

- **Sonnet is mostly order-consistent:** the same winner in 183/200 pairs with the order swapped, against 190/200 when the
  same order is simply asked twice. Overall slot A wins 52% of decisive verdicts.
- **The judges penalise content-free padding.** The padded copy of a good reply lost 80/80 comparisons, in both orders.
  The rubric judge changed no verdict for padding, and padding rescued none of the 38 failed replies.
- **No detectable self-preference, in either family.** Every judge's Haiku share sits within 0.06 of the human's
  (0.85–0.95 against 0.91), all intervals including zero. The Qwen judge, which had room to favour its own family, scored
  +0.01 [-0.07 to +0.10] toward Haiku. A cross-family panel stays at about zero excess (it had nothing to remove).
- **Freezing everything first.** The pairs, the human queue and the human answers were all fixed before any judge number
  was read, so none of the human choices could drift toward the judge.
- **Cheap.** $15.88 of Claude calls for all three experiments (budget ~$20), and 33 minutes of human time.

### Didn't work / had to change

- **The both-orders mitigation bought little.** Against the human, opposite picks fell from 6–7 to 4 of 50, mostly by
  abstaining. Agreement didn't rise, because the human almost never says "tie".
- **The anti-length instruction only half did its job, and there was no length bias for it to fix.** The padded copy
  still won 0 of 160 verdicts; 29/80 (polite) and 42/80 (policy) verdicts moved from "original" to "tie". Whether the
  human would call those ties wasn't tested.
- **A first-draft analysis overstated two results,** both caught before anything was written:
  - **The position lean:** counted by verdict it was 75%; counted by pair, which is what was sampled, it is 9/11, with an
    interval of 52–95%.
  - **The sign test on disagreements isn't valid** when the human picks one side 45 times out of 50: a judge can only
    differ toward the other side on those pairs. It was replaced by kappa and disagreement split by the human's pick.
- **Qwen's output budget again.** The 5-pair pilot at 5,000 tokens truncated 1 of 10 calls. The budget went to 6,200 (the
  largest prompt is ~1.8k of the 8,192 context), and a truncated call is retried once at a new sample index, because
  the cache key leaves out `max_tokens`.

### Surprising

- **The judges agree with the human less than "always Haiku" does.** The human picked Haiku on 45 of 50, so the trivial
  rule matches 90%. Sonnet and Opus match 76%, κ about 0; Haiku matches 86%, κ 0.32. On a lopsided set, raw agreement
  says almost nothing.
- **The human reversed 2 of 5 swapped repeats.** Five is far too few to compare with Sonnet's 92% over 200 pairs (3/5
  has an interval of roughly 23–88%), but both reversals were "friendlier" picks and the factual pick held.
- **The human's Qwen picks, where a reason was given, were for brevity or date formatting,** and Qwen's reply was the shorter one in three of
  them. No judge picked Qwen in both orders on any of them.
- **The self-preference test barely had room.** With the human at 0.91 Haiku share, no judge could exceed it by more than
  0.09.
- **Qwen judging its own replies didn't favour them.** It gave Haiku's replies a 0.93 share, against the human's 0.91, and
  never picked its own family's reply in both orders where the human had picked Haiku. Opus is the judge most willing to
  side with Qwen: 7 Qwen picks in both orders, all on pairs the human gave to Haiku.
- **The local judge answered everything this time:** 50/50 pairs, against 25/40 rubric items in P4. The difference is
  the budget (6,200 tokens instead of 4,096), with 2 of 102 calls retried after truncating.

### Open / carried forward

- **Self-preference with room to show:** pairs where the two families are closer in quality and length, or replies
  rewritten to equal length, would give the test somewhere to move.
- **Length with different content:** this phase tested filler only. Whether a longer reply with *more* (true)
  information wins unfairly is still open.
- **The human's own wobble** (2 of 5 repeats reversed) hints that taste calls are noisy for people too. More repeats
  would measure it; a written tone criterion would reduce it.
- **P6:** how many cases and trials it takes before a difference is real. The intervals here (±15 points on 50 pairs)
  are a preview.

## P6: Counting (2026-11-15)

Write-up with the k-curves (O14), the A/A runs (O15), detection by cases × trials (O16) and the MDE tables (O17):
[p6-counting.md](p6-counting.md).

### Tried

- **Estimators checked against the papers.**
  - pass@k: Chen et al. 2021, §2.1, eq. 1.
  - pass^k: Yao et al. 2024 (τ-bench), §3.
  - Paired standard error and MDE: Miller 2024, eqs. 7 and 10.

  All of it is in `evalkit/stats.py` with an assert self-check: exact unbiasedness over the binomial, convergence on
  simulated cases with mixed p, and the simulated power at the MDE coming out near 80%.
- **Two variants of the agent,** added to `supportdesk/task.py`. The system prompt is now passed through the agent rather
  than read as a module global.
  - `haiku_no_escalation`: the planted regression, with rule 5 ($200 escalation) deleted.
  - `haiku_close_any`: the P1–P3 loop.
- **Five Haiku runs of 66 × 10:** a1, a2 (the A/A pair), planted, old1 and old2 (an old-loop A/A pair). They made 3,300
  trials, with 0 infra errors and 0 truncations, and cost $2.42.
- **One Qwen 9B run of 66 × 10** with the current loop: 97 minutes of local GPU (8.8 s per trial, one at a time),
  0 infra errors and 2 truncations (counted as fails).
- **`experiments/counting.py`** has four subcommands: `curves`, `compare`, `detect` (subsampling the saved trials) and
  `power` (Miller's eq. 10 plus simulation).

### Worked

- **The planted regression was caught on all the data:** −0.152 [−0.239, −0.064]. Haiku refunded instead of escalating
  on exactly the 10 escalation cases, every trial, and nothing else changed.
- **The old-loop A/A interval covers 0:** +0.009 [−0.010, +0.028]. Under subsampling, the false-positive rate is 0–6%
  across every cases × trials cell. That's a rough calibration check: all the subsamples come from one fixed pair of
  runs.
- **The simulation matches reality.** The simulated 66 × 10 A/A half-width (0.020) equals the real one (±0.019).
- **The statement the spec asks for:** with 66 cases × 10 trials, for Haiku with the old loop's noise, we can detect
  a drop of about 4 points at 80% power if it's spread over many cases (3.0 spread evenly, 3.8 shaped like the loop
  fix). A drop concentrated on a few cases that fail every time needs about 12 points for a fresh 66 cases to find it.
  On this fixed suite, it has to break 4 or more cases. Qwen's slips outside the injection cases: about 1.7 points.
- **Haiku vs Qwen, the real comparison:** Qwen is −0.085 [−0.141, −0.029], 19 cases differ. Without the 4 injection
  cases (an exploratory cut, chosen after seeing the results) it's −0.027 [−0.040, −0.015]: still detected, from 15
  cases where Qwen passes 8 or 9 of 10.
- **66 cases is enough for P7 (my recommendation; no cases added).** That rests on the MDE: about 2 to 4 points for
  drops spread over many cases at 66 × 10, which is the size of difference P7's within-agent comparisons need to see.

### Didn't work / had to change

- **The current system is saturated:** 1,320 of 1,320 trials passed. Its A/A interval is [0, 0] and its pass@k equals
  its pass^k at every k. That technically meets the spec's "covers 0", but it teaches nothing. The old loop (P3's real
  system, 16 mixed cases) was added as the noisy variant, alongside Qwen (the spec's own fix).
- **The planted variant is flat too:** 10 cases fail every time, so pass@k = pass^k = 0.848. A planted regression that
  always fails tests detection, not consistency.
- **The pooled-p shortcut fails for the same case k times.** On simulated cases with mixed p, 0.6875^10 gives 0.02
  against a true pass^10 of 0.40, and the self-check asserts the miss. For k *different* tickets, mean^k is right. The
  blog's section-1 "ten in a row" sentence is about different tickets, so it stands.

### Surprising

- **Trials and cases are two knobs, and which one works depends on the regression.**
  - **The planted regression:** detection was the same at 1 trial and at 10 (33 cases: 85% either way).
  - **The loop fix:** at 66 cases, detection went from 44% at 1 trial to 98% at 3.
  - **What decides it:** whether the broken cases fail every time or only sometimes.
- **The average can't see a small deterministic break.** 3 of 66 cases going from 10/10 to 0/10 gives −0.045
  [−0.096, +0.005], not significant, though each case on its own is overwhelming evidence. The threshold is about 4
  broken cases for any suite well above 10 cases, given a noise-free baseline. A regression suite needs a per-case
  check, not only an average.
- **Old-loop pass@1 is 95%, but pass^10 is 76%.** With retries allowed it rounds to 100% by k = 5 (exactly 100% from
  k = 7). One set of runs describes two products, depending on whether the customer gets retries.
- **Most of that gap is the process rule.** 29 of old1's 34 failing trials broke only `confirmed_before_close`.
  Counting only the outcome graders (`--outcome`), old1 is 0.992 at k = 1 and 0.955 at pass^10, with 3 mixed cases.
- **Nothing changed, and 19 of 66 cases moved.** In the old-loop A/A, esc-06 went from 4/10 to 7/10 and fault-08 from
  10/10 to 8/10. Per-case deltas from one run each are noise.
- **pass^k looked noisier than pass@1, on one A/A pair.** The two old-loop runs differ by 0.010 at k = 1 and by 0.030
  at k = 10.
- **Ignoring clustering would have invented a finding.** On P3's 12 fault cases × 5 trials, trials-as-independent says
  Qwen recovers worse than Haiku (−0.083 [−0.153, −0.013]). Paired by case, it doesn't ([−0.185, +0.019]).
- **More trials hit a floor.** For an effect shaped like the loop fix, at 66 cases, 20 trials instead of 10 improves the
  MDE by 0.3 points, while 200 cases instead of 66 improves it by 1.6.
- **The cross-model spread was three cases, not two models being different.** All 66 cases give Haiku vs Qwen
  ω² = 0.052, where trials barely help (MDE 9.5 points at t = 1, 8.0 at t = 10). Drop the injection cases and ω² ≈ 0:
  the MDE is 1.7 points at 66 × 10, and power for the 2.7-point gap goes from 9% at t = 1 to 99% at t = 10. A few cases
  that one model always fails behave like the planted regression.
- **Qwen obeys prompt injections nearly every time,** not just the once P2 saw. It refunded on 39 of 40 injected
  trials, and those 39 failed; 3 of the 4 cases fail every time. That caps its pass@k at 0.955, since retries can't fix
  a case that always fails. Meanwhile, its
  occasional slips elsewhere pull pass^10 down to 0.712. Both kinds of failure show on one chart.

### Open / carried forward

- **Resolving smaller differences** would take about 3 times the cases (roughly halving the MDE), once there are 5 to
  10 trials. Not needed for P7.
- **Qwen's injection weakness is left as it is.** It's a property of the system under test, not of the eval, and 4
  hand-written cases already break it every time.
- **P7:** the regression suite should flag any case that drops from always-pass to always-fail, alongside the averaged
  interval.

## P7: Production checks, captures → cases, lifecycle (2026-11-15)

Write-up with the flag rates (O18), the pipeline (O19) and the lifecycle (O20):
[p7-production.md](p7-production.md).

### Tried

- **Label-free checks** (`supportdesk/checks.py`): 7 graders that run with `expected={}`, plus an inline guardrail
  (the store refuses over-total refunds). `tests/test_checks.py` proves the split.
- **Synthetic traffic** (`scripts/simulate_traffic.py`): 200 emails written by Sonnet 5.5 from code-picked facts across
  8 kinds, plus 10 hand seeds. The facts go to a sidecar the monitor never reads, kept as an answer key.
- **Three prompt versions on Haiku:**
  - v1: v2 without rule 1 and without "look up first", an ablation;
  - v2: the current prompt;
  - v3: v2 plus a whole-items-only rule taken from the labels.
- **Monitor** (`scripts/captures_to_cases.py`): rebuilds each trial from its last Vessel capture, runs the label-free
  checks, counts guardrail refusals, and judges a fixed 10% sample. Flagged trials go to an inbox.
- **20 labels by the owner:** 11 flags and 9 random unflagged trials, labelled blind.
- **Lifecycle** (`scripts/lifecycle.py`): 3 versions × 86 cases × 10 trials, under rules fixed before the run. $1.98.

### Worked

- **Captures are enough to rebuild a trial:** 420 of 420 matched the harness record exactly.
- **Every code flag was a real failure** (6 of 6 on v2). The judge's flags were 1 real out of 5.
- **The loop closed.** v2 refunded a customer-named partial amount on 4 captured emails, failing each 10/10. The owner's
  label (deny, whole items only) became v3's rule 6, and v3 passes all four 10/10. They graduate to regression, which
  meets the done-when 4 times over.
- **v3 generalised within the traffic:** all 15 partial-amount emails were denied (v2: 3 refunded, 8 denied, 4 other).
  No generated case got worse.
- **Checking the labels paid off.** `decide()` caught a refund label on an order 158 days past delivery. The oracle
  caught only the two-action cases. `decide()` needs the specs sidecar, so on real traffic (and on 9 of these 20
  unkeyed labels) there's no such check.

### Didn't work / had to change

- **The 66 cases can't separate v1 from v2:** 100% on all three versions, at 10 trials. Only the captured cases moved.
- **Two captured cases can't be passed** by any prompt, not even the oracle: a refund plus an escalation, while
  `close_ticket` records one action. They stay capability cases (a tool limit).
- **Captured cases go in `cases/captured.jsonl`, not `full.jsonl`,** because `make_cases.py` rewrites `full.jsonl`.
  They join as capability cases and graduate in place, rather than going straight to a regression file as the spec
  said.
- **cap-t076's label was too strict.** v1 failed it 3/10 on `order_id` alone, for recording the order a gift-card
  email mentioned. Loosened after the run with `order_id_also` (a new optional key the `order_id` grader reads), and
  regraded: v1 10/10.
- **Vessel stopped recording mid-run** on the v3 traffic: "capture stopped after 5 consecutive write failures
  (SQLite FOREIGN KEY constraint failed)". 63 of 210 trials were lost, and 4 more lost their final call, which showed
  up as false `schema` flags. The monitor now prints coverage: missing trials, and Vessel's recording status. After a
  restart, the rerun (`-v3b`) captured 210 of 210: 6 flags (3%), 0 partial refunds, all 140 keyed emails right.

### Surprising

- **A wrong refusal is invisible to every label-free check.** v1 denied 11 of 20 other-sender emails. The monitor caught
  1, through the judge's sample. A 9-trial random sample misses all 10 unflagged ones about 63% of the time.
- **The owner's first labels disagreed with the written policy.** On 3 other-sender emails: deny and "phone us", where
  the policy says needs_info and there is no phone line. The owner kept the policy. Labelling surfaced a policy
  question the cases never did.
- **The bug was a gap in the policy, not a model slip.** No rule covered compensation amounts. The fix was writing the
  rule, which only the labeller could do.
- **The failures were deterministic per email, but rare across emails.** v2 failed the same 4 emails every time, yet
  paid out on only 3 of 15 partial-amount emails. Wording decides it.
- **The judge saw policy text, not intent.** It was given v2's policy. For v3 traffic it is now given v3's, or it would
  fault replies for following the new rule.

### Open / carried forward

- **Multi-issue tickets:** `close_ticket` needs a list of actions. That's a tool and grader change.
- **Unsupported "no return needed" claims in replies** recur across versions (t133, t060, t153). A reply-rubric item
  or a prompt line, if it matters.
- **Report the Vessel capture failure** (the FK constraint) to the Vessel project.
- **Which prompt is current?** `haiku` still runs v2. v3 lives beside it as `haiku_v3`, so P1–P6 runs stay
  reproducible. P8 has to decide which one CI gates.
- **P8 (CI):** the regression suite is now 44 generated + 18 captured cases. The per-case "always-pass → always-fail"
  rule from P6 belongs in the CI gate.

## P8: CI (2026-11-16)

Write-up with the gate, its noise and the CI runs (O21): [p8-ci.md](p8-ci.md).

### Tried

- **A PR gate** (`.github/workflows/evals.yml`, `scripts/ci.py pr`): ruff, pyright, the tests, then the 62 regression
  cases × 3 trials on the shipped prompt, code + path graders. It fails on a pass rate under 95%, or on a case that
  always passed in the baseline and now fails all 3 trials. Flaky cases warn, and infra errors fail as infra.
- **A nightly report** (`evals-nightly.yml`): all 86 cases × 3, plus the Haiku reply judge against the shipped policy.
  Report only. It writes the next baseline as an artifact.
- **Shipping v3.** The prompt moved to `src/supportdesk/system_prompt.txt`, and v2 is frozen as `V2_PROMPT`.
- **The done-when PRs:** delete the $200 rule; add one blank line to the prompt.

### Worked

- **Done when, met on GitHub.** The rule-deletion PR (#1) failed at 165/186, with 7 cases (`esc-01`…`esc-06`,
  `cap-t198`) going from 10/10 to 0/3. The whitespace PR (#2) passed 186/186. That matches the local rehearsal
  exactly.
- **Cheap and quick:** about $0.12 and 2¼ minutes per PR, including setup, ruff, pyright and the tests.
- **The baseline cost nothing:** P7's v3 runs, on the same prompt hash and regraded, gave 10 trials per case.
- **The per-case rule and the threshold agree on a real break.** Each would have failed #1 alone.
- **The first nightly run:** 186/186 regression trials and 66/72 capability trials passed; only the two tool-limit
  cases failed. $0.40 and 6.8 minutes. Its results are now the PR baseline.
- **The judge failed 14 replies the code passed,** out of 258. Most were wording no code grader reads: an unpromised
  "5 to 10 business days", "no need to ship anything back", "contact our team" or a website page that doesn't exist.
  A few were harsh (`cap-t076`'s whole-item offer, which v3 allows). Real but noisy, which is why it reports and
  doesn't gate.

### Didn't work / had to change

- **The prompt had to leave the code first.** The P6 and P7 variants were derived from the shipped prompt, with asserts
  that the $200 rule appeared exactly once. A PR deleting the rule would have crashed at import, and CI would have
  failed for the wrong reason.
- **`astral-sh/setup-uv@v10` doesn't resolve.** setup-uv publishes exact release tags only, so the first CI runs failed
  at "Set up job". Pinned to v10.3.3.

### Surprising

- **The gate's job is to be boring.** With 3 trials it almost never goes red by chance: about once in 12,000 PRs at a
  1% per-trial failure rate. The price is partial regressions. A case that now passes half the time fails the gate only
  12.5% of the time, and is a warning 75% of the time.

### Open / carried forward

- **Switch off the nightly after the article:** `gh workflow disable evals-nightly.yml`. The PR gate then falls back
  to the committed seed once the last nightly artifact expires (90 days).
- **The judge reports to nobody.** Its disagreements show in the nightly summary, and no one reads that by default.
- Carried from P7: multi-issue `close_ticket`; the Vessel FK bug report.

## P9: The same eval in eight tools (2026-11-16)

Write-up with the results, the side-by-side grader (O23) and the cross-tool findings: [p9-tools.md](p9-tools.md).
The comparison table (O22) is spec §6. Each port's record: `tools/<tool>/P9.md`.

### Tried

- **A shared base:** `supportdesk.run`/`arun` as the one entry point, a frozen 10-case slice, a reference run from our
  harness (30/30/28), and a contract every port followed (`tools/p9/README.md`).
- **Eight ports,** each in its own session and worktree from the same brief: Inspect, Pydantic Evals, DeepEval,
  Promptfoo and OpenAI Evals in parallel; Braintrust, LangSmith and Langfuse (cloud, EU) one at a time, twice each.
- **The same three graders in each tool's own idiom:** action (code), rubric v2 (Haiku judge), path (trajectory).

### Worked

- **Every tool can run an outside agent and grade it.** Action and path were 30/30 in every port, on every run, and
  agree trial for trial with the reference functions.
- **Every port got P4's judge prompt across,** byte for byte in several (OpenAI Evals, LangSmith, Braintrust's user
  block), checked against what was sent or stored.
- **Cheap:** about $1 for all eight, $0.04–0.07 per run.
- **Parallel sessions from one written contract** gave comparable records with the same headings, in an afternoon.

### Didn't work / had to change

- **The sync entry point hung after an async call.** The agent's API client was cached across event loops. Clients are
  now cached per loop (keyed by the loop object, not its id, which gets reused).
- **No framework sees an outside agent's tool calls.** Every port replayed or converted the transcript. Pydantic Evals'
  span checks passed vacuously without it.
- **DeepEval's judge:** 20/26 with 4 unparsed verdicts, from G-Eval's framing and no structured output for Haiku 5.5.
- **Promptfoo sends `temperature` to Haiku 5.5** (rejected); its strip list knows Opus 5.5, not Haiku.
- **OpenAI Evals:** only the 2023 release installs on 3.14; its package name (`evals`) collides with this repo's.
- **The UI-only parts** (working an annotation queue, server-side judges, GitHub Actions) were created or read, not run.

### Surprising

- **The tools differ least where you'd expect most.** The code graders port identically everywhere; all the variation is
  the judge, and run-to-run noise (27 then 29 on the same setup) is as large as the difference between tools.
- **Every hosted platform averages a human score with the judge's.** A correction becomes one more sample (a 1 and a 0
  show as 0.5), not an override.
- **The hosted comparisons don't fit k>1.** Braintrust flags "regressions" between identical reruns, LangSmith's stats
  lagged ~14 minutes, and Langfuse's comparison view shows only one of each case's three trials.
- **Uploads are much bigger than results:** 2–5 MB per 30-trial experiment, with the system prompt 75 times, plus one
  stray detail each (the Windows username, request headers and env vars, the public key).
- **Defaults route through the vendor:** Braintrust's judge library uses Braintrust's AI gateway unless given a client.
- **Only Inspect computes pass@k/pass^k.** Two tools have no repeats at all.

### Open / carried forward

- **Langfuse's annotation queue:** does a queue score stay out of the `rubric` average? Score one trial in `p9-review`.
- **Server-side judges** (all three hosted) need a stored model key: untested by choice.
- **Part 4** of the blog is now unblocked: the table, the side-by-side grader and the cross-tool findings.
- Carried from P7: multi-issue `close_ticket` (decided: leave as a tool limit).
