# P9: the same eval in eight tools (outputs O22, O23)

**Status: done.** All eight ports ran the same slice, and the comparison table (O22, spec §6) is filled from them, not
from docs. Each port's full record, with its code, the judge prompt it actually sent, its friction and what it uploaded,
is in `tools/<tool>/P9.md`.

Code:
- The shared base: [tools/p9/](../tools/p9/): the contract every port followed ([README.md](../tools/p9/README.md)), the
  frozen slice and the reference run. The reference script is [scripts/p9_reference.py](../scripts/p9_reference.py).
- The entry point every port calls: `supportdesk.run(message, faults) -> Trial`, and `arun` for async frameworks
  ([src/supportdesk/\_\_init\_\_.py](../src/supportdesk/__init__.py)).
- The ports: `tools/inspect/`, `tools/pydantic-evals/`, `tools/deepeval/`, `tools/promptfoo/`, `tools/openai-evals/`,
  `tools/braintrust/`, `tools/langsmith/`, `tools/langfuse/`.

## The setup

- **One slice:** 10 cases from the suite, one per case group plus the captured case `cap-t002`, 3 trials each.
- **One system under test:** the shipped agent on Haiku 5.5, through `supportdesk.run`. No framework ran the agent
  itself; each called our function.
- **Three graders**, each written the way that tool wants it:
  - **action (code):** the close_ticket action is the expected one (or an `also_ok` alternative);
  - **rubric (judge):** rubric v2's three criteria, Haiku 5.5 as judge, no answer key, the reply passes only if all three
    pass. The judge has to see what P4's judge saw: the policy, the email, the tool calls and results, what changed, the
    reply;
  - **path (trajectory):** no refund or escalation before a successful `get_order` of that order in an earlier turn.
- **A reference:** our own harness on the same slice: action 30/30, path 30/30, rubric 28/30.
- **One record per port,** under the same headings, with the judge prompt captured from the wire (through Vessel, or
  the tool's own log) rather than described from docs.

Each port ran in its own session and its own worktree, from the same brief; the five open-source ones in parallel, the
three hosted ones one at a time once each account existed. The hosted ones ran twice, so their experiment comparisons
had something to compare.

## Results

| Tool | Action | Path | Rubric | Record run | Spent in all | Wall time | Port LOC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| reference (evalkit) | 30/30 | 30/30 | 28/30 | $0.020 + judge | — | — | — |
| Inspect | 30/30 | 30/30 | 29/30 | $0.073 | ~$0.11 | 35 s | 243 |
| Pydantic Evals | 30/30 | 30/30 | 28/30 | $0.044 | ~$0.18 | 37 s | 143 (+39 span probe) |
| DeepEval | 30/30 | 30/30 | **20/26, 4 no verdict** | $0.051 | ~$0.21 | 272 s | 210 |
| Promptfoo | 30/30 | 30/30 | 28/30 | $0.042 | ~$0.05 | 88 s | 179 |
| OpenAI Evals | 30/30 | 30/30 | 29/30 | $0.066 | ~$0.09 | 74 s | 242 |
| Braintrust (2 runs) | 30/30, 30/30 | 30/30, 30/30 | 30/30, 30/30 | $0.057 | ~$0.14 | 48 s | 312 |
| LangSmith (2 runs) | 30/30, 30/30 | 30/30, 30/30 | 27/30, 29/30 | $0.049 | ~$0.13 | 31 s | 438 |
| Langfuse (2 runs) | 30/30, 30/30 | 30/30, 30/30 | 29/30, 27/30 | $0.040 | ~$0.09 | 50 s | 396 |

Every trial is new: none of the 240 exported replies (one run per port) equals a reference reply. "Spent in all" includes smoke runs, probes and
cross-checks. P9 cost about **$1** in API calls.

- **The code graders agree everywhere,** trial for trial, when re-graded with the reference functions on what each tool
  stored. Porting a deterministic grader is a translation problem, and every tool can do it.
- **Every difference is in the judge.** Rubric counts range 27 to 30 across seven tools, and the same setup moved by two
  between runs (LangSmith 27 then 29, Langfuse 29 then 27). The two phrasings behind most fails recur across tools:
  "refunded to your original payment method" (true, but no tool result says so) and "downloaded on delivery" (the order
  says `downloaded: true`, with no time). P4's own judge, re-run on LangSmith's replies, also failed 3 of 30, but not
  the same 3. That is a judge at the edge of its rubric, not a difference between tools.
- **DeepEval is the outlier**, for reasons of form (below).
- **Port size isn't effort.** The hosted ports are the largest because reading results back out (one row per trial,
  scores from child spans, the repeat index from metadata) is the biggest single piece of glue: 56 to 78 lines each.

## O22: the comparison table

In [spec §6](spec.md). What the columns say, read across:

- **Case is…** Five shapes: a typed row (Inspect `Sample`, Pydantic `Case`), a test record (Promptfoo, DeepEval), an
  untyped JSONL row (OpenAI Evals), and a hosted, versioned dataset item (the three platforms). Only Inspect turned
  `also_ok` into something native (a list target means any of them).
- **Grader is…** A function or class returning a score everywhere; the differences are in what it receives. Langfuse's
  evaluator sees only the output, so the transcript had to ride inside it. LangSmith's gets the in-memory run tree.
  Braintrust's can read the trace back from the server, with a race.
- **Run is…** A log file (Inspect, OpenAI Evals), a database row set (Promptfoo), an in-memory report with nothing on disk
  (Pydantic Evals), the latest run only (DeepEval), or a hosted experiment.
- **Repeats / pass@k:** see "What they have in common" below.

## O23: the same grader in each tool

The action grader, as each port wrote it. The check is one line in every case; the rest is each tool's idea of where
the expected answer and the output live, and what a score is.

**Inspect** (`tools/inspect/p9.py`): a scorer over the task state; `also_ok` is already in the list target.
```python
def action() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        got = state.store.get("trial")["output"].get("action")
        value = CORRECT if got in target.target else INCORRECT
        return Score(value=value, answer=str(got), explanation=f"expected one of {target.target}")

    return score
```

**Pydantic Evals** (`tools/pydantic-evals/p9.py`): an evaluator class; without the name override it reports as "Action".
```python
class Action(Evaluator[Ticket, Trial, Expected]):
    def get_default_evaluation_name(self) -> str:
        return "action"

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        ok = [ctx.metadata["action"], *(a["action"] for a in ctx.metadata.get("also_ok", []))]
        got = ctx.output.output.get("action")
        return EvaluationReason(got in ok, reason=f"expected {' or '.join(ok)}, got {got}")
```

**DeepEval** (`tools/deepeval/p9.py`): a metric on a chat-shaped test case, so the action rides in `metadata`.
```python
class ActionMetric(CodeMetric):
    metric_name = "action"

    def check(self, tc: LLMTestCase) -> tuple[bool, str]:
        m = meta(tc)
        got, want = m["action"], [tc.expected_output, *m["also_ok"]]
        return got in want, f"expected {' or '.join(map(str, want))}, got {got}"
```

**Promptfoo** (`tools/promptfoo/p9.yaml`): inline JavaScript in YAML, reading the trial the Python provider returned.
```yaml
- type: javascript
  metric: action
  value: |
    const got = context.metadata.trial.output.action;
    const ok = [context.vars.expected, ...(context.vars.expected.also_ok ?? [])].map((e) => e.action);
    return { pass: ok.includes(got), score: ok.includes(got) ? 1 : 0, reason: `expected ${ok.join(' or ')}, got ${got}` };
```

**OpenAI Evals** (`tools/openai-evals/p9_eval.py`): no grader object; one line inside the eval class's `eval_sample`,
using the framework's match primitive (which matches by prefix).
```python
ok = [case.expected["action"], *(a["action"] for a in case.expected.get("also_ok", []))]
action = record_and_check_match(prompt=case.input, sampled=trial.output.get("action") or "", expected=ok)
```

**Braintrust** (`tools/braintrust/p9.py`): a plain function; parameters filled by name.
```python
def action(output: JSON, expected: JSON) -> Score:
    ok = [expected["action"], *(a["action"] for a in expected.get("also_ok", []))]
    got = output.get("action")
    return Score("action", float(got in ok), metadata={"got": got, "accepted": ok})
```

**LangSmith** (`tools/langsmith/p9.py`): a plain function returning a dict, stored as feedback.
```python
def action(outputs: JSON, reference_outputs: JSON) -> JSON:
    ok = [reference_outputs["action"], *(a["action"] for a in reference_outputs.get("also_ok", []))]
    got = outputs.get("action")
    return {"key": "action", "score": got in ok, "comment": f"got {got}, accepted {ok}"}
```

**Langfuse** (`tools/langfuse/p9.py`): keyword-only arguments, an `Evaluation` stored as a score.
```python
def action(*, input: JSON, output: JSON, expected_output: JSON, metadata: JSON, **kwargs: Any) -> Evaluation:
    ok = [expected_output["action"], *(a["action"] for a in expected_output.get("also_ok", []))]
    got = output.get("action")
    return Evaluation(
        name="action", value=got in ok, data_type="BOOLEAN", comment=f"got {got}, accepted {ok}", metadata=GRADER
    )
```

## The judge, eight ways

Every port ended up sending P4's judge prompt, or something close to it, but by very different routes.

| Tool | Judge used | How P4's view got in | Verdict format |
| --- | --- | --- | --- |
| Inspect | `model_graded_qa`, called once per criterion | sample metadata as template variables | free text, `GRADE: C/I` parsed |
| Pydantic Evals | `LLMJudge`, subclassed | a subclass swaps the context for P4's `view()`; plain `LLMJudge` sent the whole Trial, including which model wrote the reply | structured output |
| DeepEval | `GEval`, one per criterion | `context`, `input`, `tools_called`, `actual_output` | free-text JSON (Haiku 5.5 isn't in its model table), score 0/1 |
| Promptfoo | `llm-rubric` ×3 in an `assert-set` | an assertion `transform` swaps in P4's prompt | JSON schema output |
| OpenAI Evals | modelgraded YAML spec | template placeholders; user body byte-identical to P4's | free text, Y/N choice parsed |
| Braintrust | autoevals `LLMClassifier` ×3 | mustache variables; no system role | forced tool call (`select_choice`) |
| LangSmith | openevals `create_llm_as_judge` ×3 | P4's system prompt and user block, byte for byte | forced tool call (`score`) |
| Langfuse | P4's own judge, run locally | (its native judge is server-side and needs a model key stored in Langfuse; not used) | structured output |

- **Only Langfuse has no client-side judge of its own.** Its LLM-as-judge runs on Langfuse's servers through a stored
  model key. The user chose to keep the Anthropic key local, so that path is untested.
- **The judge's form moves the numbers more than its prompt.** DeepEval sent the same fields, but wrapped in G-Eval's
  framing ("return a JSON with … a `score` key that is STRICTLY EITHER 1 … OR 0") with no structured output, and Haiku
  thought at its default effort. It failed replies the others passed, and 4 times produced nothing parseable.
- **Forced tool calls switch thinking off.** Braintrust's and LangSmith's judges forced a tool, and Haiku returned no
  thinking at all; P4's judge runs at effort medium. Braintrust's was the most lenient (60/60), and missed the invented
  download timing the reference's judge caught.
- **Defaults route judges in surprising ways.** autoevals (Braintrust) sends judge calls through Braintrust's AI gateway
  unless every classifier is given a client. Promptfoo sends `temperature` to Haiku 5.5, which rejects it: its list of
  models to strip it for has Opus 5.5 and not Haiku.

## What they have in common

- **None of the eight sees an outside agent's tool calls natively.** Every framework assumes it runs the model itself.
  Our agent runs its own loop, so each port had to hand the transcript over:
  - converted into the tool's own message types (Inspect: then first-class in its log and viewer);
  - replayed as `tool` spans or observations (Braintrust, LangSmith, Langfuse);
  - carried in metadata or a test-case field (Promptfoo, DeepEval);
  - smuggled through a string (OpenAI Evals, whose completion functions return only text).
  Pydantic Evals' span-based checks, without instrumentation, **passed vacuously**: no tool spans, so nothing to fail.
  The model calls themselves often arrived for free, through the Anthropic SDK's own OpenTelemetry spans (Pydantic
  Evals, Langfuse) or a wrapper (Braintrust, LangSmith).
- **Only Inspect has pass@k and pass^k built in** (epoch reducers). Pydantic Evals, DeepEval, Promptfoo, Braintrust and
  LangSmith repeat but don't aggregate per case; Promptfoo reports "repeat stability" instead. OpenAI Evals and Langfuse
  have no repeats at all. Pydantic Evals, Braintrust and LangSmith run the repeats without storing which one a trial is,
  so the ports recorded the index themselves.
- **Every hosted platform averages a human score with the judge's.** Braintrust, LangSmith and Langfuse all store a human
  correction as one more score on the same key. A human 0 next to a judge's 1 shows as 0.5; nothing marks it as an
  override. Langfuse's annotation queue scores into a separate config, which might avoid this; it wasn't completed.
- **CI is an exit code, and each tool's means something different.** Promptfoo exits 100 on any failed assertion,
  DeepEval fails on any single failure, including a judge parse error, and OpenAI Evals exits 0 whatever the score. The
  only threshold gate found is in Langfuse's GitHub Action (`RegressionError`, from source, not run); everywhere else a
  pass-rate gate is a script over the tool's results, as in P8.
- **A dataset re-upload with nothing changed creates a new version** in LangSmith and Langfuse, and Langfuse's runs don't
  record which version they used.

## The hosted platforms: what the server adds

| | Braintrust | LangSmith | Langfuse |
| --- | --- | --- | --- |
| Comparison | automatic against the previous run; Diff mode in the UI | comparison UI pages the 3 trials under one example, with a mean | one trial per item shown (the last started); no baseline, no API |
| Noise | identical reruns show "regressions", so a regression count can't gate | per-score stats lagged ~14 minutes after each run | a k=3 comparison is two samples of 10 |
| Human score | averaged with the judge's | averaged (the UI showed µ 0.50) | averaged in the summary; the cell shows only the human's |
| Per experiment | 2.1 MB | 4.9 MB | 2.3 MB |
| Extra leak | the local Windows username in every row's caller location | per-call `extra_headers` verbatim, `LANGSMITH_*` env vars on every run | the public key on every observation |

All three store the whole agent request on every call, so the system prompt goes up dozens of times per run (75 copies
in each), along with every judge prompt and its policy. For a real product with a confidential prompt or customer
data, that is the price of the comparison UI.

## Traps found along the way

- **Package names collide.** OpenAI's framework is called `evals`, and so is this repo's root package: installing the
  repo into the tool's environment replaced the framework.
- **`uv init` joins the parent workspace.** In a subfolder of a uv project it edits the root `pyproject.toml` and
  `uv.lock`. `uv init --bare --no-workspace` doesn't.
- **DeepEval on Windows needs pywin32,** or its cache read fails and every test fails while its summary says 100% pass.
- **Telemetry is on by default** in DeepEval (PostHog); OpenAI Evals lists models from api.openai.com on import unless
  redirected; Promptfoo needs four environment variables to stay quiet.
- **A leftover viewer** (`promptfoo view`) held its database open after its session ended, and blocked removing the
  worktree.

## Limits

- **One slice, and an easy one.** The agent passed action and path on every trial in every tool, so no port's code
  graders were tested on a real failure (several tested them on synthetic transcripts instead).
- **One judge model.** Every port used Haiku 5.5. The tools' judges differ in form; with a stronger judge the
  differences might shrink.
- **The UI-only features were looked at, not used.** Annotation queues were created and filled but not worked through,
  server-side judges weren't used, and no GitHub Action ran.
- **The ports were written by AI sessions from one brief.** That makes them consistent and comparable; it also means
  each port reflects one way to use the tool, written by someone reading its source, not an experienced user of it.
- **Windows only,** and each tool's version on 2026-11-16.

## Open

- **Langfuse's annotation queue:** score one trial in `p9-review` and see whether "pass or fail" shows as its own column,
  not averaged into `rubric`.
- **Server-side judges** (Langfuse, and Braintrust's and LangSmith's online evaluators) need a stored model key; untested.
