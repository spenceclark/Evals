<!--
Draft in Claude's words, for you to rewrite. Written 2026-11-17 from the P9 write-up (docs/p9-tools.md), the comparison
table (spec §6), each port's record (tools/<tool>/P9.md), blog.md §2 and the P0 spike (docs/p0-compat.md). Shaped like
the published part 1: short title and dek, opens on a finding, no section numbers.
Data: the P9 port runs of 2026-11-16, all against the same agent code (the P9 base, c6854b2). Tool behaviour is dated,
not tagged: the versions are in the table below and in each port's record. Re-check any tool you name against its
current release before posting; these go stale fastest.
Not tested, and worded as such: server-side judges on the hosted platforms, working an annotation queue in a UI, the
vendors' GitHub Actions.
Screenshots (docs/img/p9-*.jpg) taken 2026-11-17 in each platform's UI, of the two record runs. The org names, account
name and profile pictures are blacked out or cropped; the UIs will have moved on by the time this is posted.
Remove this comment before posting.
-->

# One test, eight toolboxes

*I ran the same agent eval in eight frameworks. They agreed on every code-graded trial. The differences were in the
defaults.*

One framework printed "Pass Rate: 100.0%" for a run in which every test had failed. Another sent my judge's calls through
the vendor's own gateway unless I stopped it. A third checked the agent's path and passed it, without having seen a single
tool call. None of these was an error message. Each one only showed up because I was watching the traffic, or reading
back what had been stored.

That's the short version of this part. The longer version is more reassuring: on the question an eval exists to answer,
did the agent do the right thing, all eight frameworks gave the same answer as my own few hundred lines of harness.

## The test

Parts 1 to 3 used a small harness written from scratch, to show what an eval framework does for you. This part ports
one slice of the same eval to the eight tools that come up whenever this topic does:

- **run locally, open source:** Inspect, Pydantic Evals, DeepEval, Promptfoo and OpenAI Evals (the open-source
  framework, not the hosted product);
- **hosted platforms with a UI:** Braintrust, LangSmith and Langfuse, each on a free tier in its EU region.

Every port did the same thing:
- **10 cases,** one from each group of the suite, 3 trials each;
- **the same agent,** the refund desk on Claude Haiku 5.5, called through one Python function. No framework ran the
  agent itself; each called mine;
- **the same three graders,** written the way each tool wants them written:
  - **action** (code): did it make the right decision?
  - **rubric** (an LLM judge, Haiku 5.5): is the reply accurate, free of promises the policy doesn't make, and polite?
    The judge sees what part 2's validated judge saw: the policy, the email, what the agent did, what changed, the
    reply;
  - **path** (the trajectory): did it look the order up before refunding or escalating it?
- **a reference:** my own harness on the same slice: action 30/30, path 30/30, rubric 28/30.

For each tool I wanted the three things every framework names differently: what is a case, what is a grader, what is a
run? And I wanted to know what was native and what was glue, and what each tool's judge actually sends.

## The results: the same answers

| Tool | Action | Path | Rubric | One run | Lines of code |
| --- | --- | --- | --- | --- | --- |
| my harness | 30/30 | 30/30 | 28/30 | — | — |
| Inspect | 30/30 | 30/30 | 29/30 | $0.07 | 243 |
| Pydantic Evals | 30/30 | 30/30 | 28/30 | $0.04 | 143 |
| DeepEval | 30/30 | 30/30 | 20 of 26, 4 no verdict | $0.05 | 210 |
| Promptfoo | 30/30 | 30/30 | 28/30 | $0.04 | 179 |
| OpenAI Evals | 30/30 | 30/30 | 29/30 | $0.07 | 242 |
| Braintrust (2 runs) | 30/30 | 30/30 | 30/30, 30/30 | $0.06 | 312 |
| LangSmith (2 runs) | 30/30 | 30/30 | 27/30, 29/30 | $0.05 | 438 |
| Langfuse (2 runs) | 30/30 | 30/30 | 29/30, 27/30 | $0.04 | 396 |

**The code graders agreed everywhere,** trial for trial, with my harness's own grading functions run on what each tool
had stored. Porting a check that's just code is a translation problem, and every tool can do it.

**Every difference is in the judge, and most of it is noise.** The same setup in LangSmith scored 27 and then 29; in
Langfuse, 29 and then 27. That's as wide as the spread across tools. The fails were mostly two borderline phrasings the
judges kept splitting on: "refunded to your original payment method" (true, but no tool result said so), and "downloaded
on delivery" (the order said it was downloaded, not when). When I re-ran part 2's own judge on LangSmith's replies, it
also failed 3 of 30, but not the same 3.

DeepEval is the exception, and it's worth its own section below.

## The same check, eight ways

Here is the action grader in each port. The check itself is one line everywhere: is the agent's action one of the
accepted ones? Everything around that line is the tool's idea of where the expected answer lives and what a score is.

**Inspect:** a scorer over the task's state. A list target means "any of these", so cases with two right answers needed
no code at all.
```python
def action() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        got = state.store.get("trial")["output"].get("action")
        value = CORRECT if got in target.target else INCORRECT
        return Score(value=value, answer=str(got), explanation=f"expected one of {target.target}")

    return score
```

**Pydantic Evals:** an evaluator class. Without the name override, the score is reported as "Action".
```python
class Action(Evaluator[Ticket, Trial, Expected]):
    def get_default_evaluation_name(self) -> str:
        return "action"

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        ok = [ctx.metadata["action"], *(a["action"] for a in ctx.metadata.get("also_ok", []))]
        got = ctx.output.output.get("action")
        return EvaluationReason(got in ok, reason=f"expected {' or '.join(ok)}, got {got}")
```

**DeepEval:** a metric on a test case shaped for chat, where the output is one string. The agent's structured decision
has no field of its own, so it travels in metadata.
```python
class ActionMetric(CodeMetric):
    metric_name = "action"

    def check(self, tc: LLMTestCase) -> tuple[bool, str]:
        m = meta(tc)
        got, want = m["action"], [tc.expected_output, *m["also_ok"]]
        return got in want, f"expected {' or '.join(map(str, want))}, got {got}"
```

**Promptfoo:** JavaScript inside YAML, reading what the Python side returned.
```yaml
- type: javascript
  metric: action
  value: |
    const got = context.metadata.trial.output.action;
    const ok = [context.vars.expected, ...(context.vars.expected.also_ok ?? [])].map((e) => e.action);
    return { pass: ok.includes(got), score: ok.includes(got) ? 1 : 0, reason: `expected ${ok.join(' or ')}, got ${got}` };
```

**OpenAI Evals:** no grader object at all. One line inside the eval class, using the framework's matching function,
which matches by prefix.
```python
ok = [case.expected["action"], *(a["action"] for a in case.expected.get("also_ok", []))]
action = record_and_check_match(prompt=case.input, sampled=trial.output.get("action") or "", expected=ok)
```

**Braintrust, LangSmith, Langfuse:** a plain function, with the arguments filled in by name. They differ in what they
call things and what they return. (Langfuse's is trimmed slightly from the port.)
```python
def action(output: JSON, expected: JSON) -> Score:  # Braintrust
    ok = [expected["action"], *(a["action"] for a in expected.get("also_ok", []))]
    got = output.get("action")
    return Score("action", float(got in ok), metadata={"got": got, "accepted": ok})


def action(outputs: JSON, reference_outputs: JSON) -> JSON:  # LangSmith
    ok = [reference_outputs["action"], *(a["action"] for a in reference_outputs.get("also_ok", []))]
    got = outputs.get("action")
    return {"key": "action", "score": got in ok, "comment": f"got {got}, accepted {ok}"}


# Langfuse
def action(*, input: JSON, output: JSON, expected_output: JSON, metadata: JSON, **kwargs: Any) -> Evaluation:
    ok = [expected_output["action"], *(a["action"] for a in expected_output.get("also_ok", []))]
    got = output.get("action")
    return Evaluation(name="action", value=got in ok, data_type="BOOLEAN", comment=f"got {got}, accepted {ok}")
```

The ports came out between about 140 and 440 lines. The size isn't the effort. The hosted ones are the biggest because
reading results back out, one row per trial, is the largest single piece of glue: 56 to 78 lines each.

## None of them can see your agent

Every framework assumes it runs the model itself. My agent runs its own loop: it calls the model, runs the tools, and
calls the model again. To the frameworks, that loop is invisible. So in every port, the path check had to be handed the
agent's tool calls in some other way:

- **converted** into the tool's own message types (Inspect; after that, its log and viewer show them as if Inspect had
  run the agent);
- **replayed** as tool spans or observations (Braintrust, LangSmith, Langfuse);
- **carried** in metadata or a test-case field (Promptfoo, DeepEval);
- **smuggled** through a string (OpenAI Evals, whose model functions can only return text).

Replayed, the agent's steps look like this in Braintrust and Langfuse. It's the same escalation case in both: the model
calls arrived on their own, the tool calls are there only because the port added them afterwards, and the graders hang
underneath, with the judge's three calls inside the rubric.

![Braintrust trace of one escalation trial: model calls, replayed get_order and escalate spans, then the action, rubric
and path scorers](img/p9-braintrust-trace.jpg)

![Langfuse trace of the same case: three model calls and the replayed tool observations under the task, then the
action, rubric and path evaluators](img/p9-langfuse-trace.jpg)

The worst case was a check that passed anyway. Pydantic Evals can check an agent's path from its trace, and it failed
both ways here:
- **with tracing not set up,** one of its checks returned a plain "false", with a note that there was no trace, which
  reads like a real failure in the results table;
- **with tracing on but no tool calls in the trace,** its checks found nothing to object to and passed. On a trial
  where the agent had escalated a refund, the path check passed and the tool-call count said 0.

The model calls themselves often arrived for free: the Anthropic SDK now emits its own tracing spans, and several tools
picked those up. It's the tools that go missing. If your agents are built on the framework's own agent library
(Pydantic AI, for Pydantic Evals), this whole section may not apply: its tool calls are already in the trace. I didn't
test that here.

## Where they really differ: the judge

Every port ended up sending part 2's judge prompt, or close to it. Several sent it byte for byte. But they got there by
very different routes, and they read the answer back in different ways:

| Tool | Judge | Verdict comes back as |
| --- | --- | --- |
| Inspect | its model-graded scorer, called once per criterion | free text, "GRADE: C" parsed |
| Pydantic Evals | its LLM judge, subclassed to control what it sees | structured output |
| DeepEval | G-Eval, one per criterion | JSON in free text |
| Promptfoo | three `llm-rubric` assertions | JSON schema output |
| OpenAI Evals | a YAML judge spec | free text, a Y or N parsed |
| Braintrust | autoevals classifiers | a forced tool call |
| LangSmith | openevals judges | a forced tool call |
| Langfuse | my own judge, run locally | structured output |

**The form moved the numbers more than the prompt did.** DeepEval sent the same information, but wrapped in G-Eval's own
framing ("return a JSON with … a `score` key that is STRICTLY EITHER 1 … OR 0"), and without structured output, because
its model table didn't know Haiku 5.5. It failed replies every other judge passed, and four times it produced nothing it
could parse. Braintrust's and LangSmith's judges forced a tool call, which switched the model's thinking off entirely;
Braintrust's passed everything, including a reply with an invented download date that my own judge had failed.

**The defaults decide what the judge sees,** and they vary more than you'd guess. In a first spike with canned replies,
two tools' default judge prompts left out the customer's question entirely: it graded an answer without the question.
Pydantic Evals' default judge, given my agent's output, would have sent the judge the name of the model that wrote the
reply, which part 2 went out of its way to hide.

**The defaults decide where the judge's calls go,** too. Braintrust's judge library sends its calls through Braintrust's
own AI gateway unless each judge is handed a client. Promptfoo sends a `temperature` setting that Haiku 5.5 rejects: its
list of models to strip it for had Opus 5.5 on it, but not Haiku. OpenAI Evals hard-codes sampling settings current
Claude models refuse.

The lesson from part 2 applies to every framework here: **read the request your judge actually sends** before trusting
anything it says.

## Things that happened without asking

- **A pass rate that lied.** On Windows, DeepEval needs an extra package for its cache. Without it, every test failed,
  and DeepEval's own summary said "Pass Rate: 100.0%".
- **Checks that passed without checking.** The trajectory checks above.
- **Telemetry on by default.** DeepEval sends usage data to PostHog unless switched off; Promptfoo needed four environment
  variables to stay quiet.
- **Calls to services I wasn't using.** OpenAI Evals creates an OpenAI client and lists OpenAI's models when it loads,
  even when every call goes to Claude.
- **Uploads with the switch off.** In the first spike, LangSmith uploaded the judge's traces even with
  `upload_results=False`, and failed with a 401 because there was no account.

## The hosted platforms: what they add

The three hosted platforms add what you'd expect: stored runs, a comparison between runs, and a place for people to
review results. Each did the comparison differently, and none handled repeated trials well:

| | Braintrust | LangSmith | Langfuse |
| --- | --- | --- | --- |
| Comparing two runs | automatic, against the previous run; a diff view | three trials paged under each case, with a mean | one trial per case shown, the last one started |
| Run-to-run noise | two identical runs showed "regressions" | the stored totals lagged about 14 minutes behind | a three-trial comparison is two samples of 10 |
| Repeats | built in | built in | none: I passed the data in three times |

Here's the same comparison, run 2 against run 1, in each. Braintrust groups each case's three trials and shows the
earlier run's scores under the new ones (both runs scored 100% on everything):

![Braintrust comparing run 2 with run 1: three trials per case, each score with the earlier run's beneath
it](img/p9-braintrust-compare.jpg)

LangSmith shows each case's mean over its three trials, pages through the trials one at a time ("1/3"), and marks in red
where one run scored lower. Here run 1 lost two of three trials on one case:

![LangSmith comparing the two runs: per-case means, trials paged 1 of 3, and the case where run 1 scored lower in
red](img/p9-langsmith-compare.jpg)

Langfuse shows one verdict per case for each run. Its "90%" for run 2 is 9 of 10 cases, from one trial each; over all
30 trials, the judge's `accurate` score was 29 of 30:

![Langfuse comparing the two runs: one true or false per case per run, from one of its three
trials](img/p9-langfuse-compare.jpg)

LangSmith also charts every run on a dataset, which is the quickest way to see the two record runs (0.90 and 0.97 on the
rubric) next to the smaller checks around them:

![LangSmith's experiment list for the slice: feedback, latency, token and cost charts per
run](img/p9-langsmith-experiments.jpg)

The finding I didn't expect is the same on all three: **a human correction is averaged with the judge's score.** When I
marked a reply as failing that the judge had passed, the platform stored my score as one more score on the same line.
The run's summary then showed 0.5 for that reply. Nothing marked it as a correction. If the person reviewing is your
source of truth, as part 2 argued they should be, that's the wrong way round: their label should grade the judge, not
join it.

## And what they take

Each run of 30 trials uploaded between 2 and 5 megabytes. That's not the results; it's everything:

- **the whole request on every model call,** so the system prompt went up 75 times per run;
- **every judge prompt,** with the full policy, 90 times per run;
- **one stray detail each:** Braintrust recorded the file and line that produced each row, which put my Windows username
  in about 150 records per run; LangSmith stored each request's extra headers and the `LANGSMITH_*` environment variables
  on every run; Langfuse stamped the public half of my API key on every record.

My data was synthetic, so none of it mattered here. With a real customer's data or a confidential prompt, it would decide
the choice before any feature did.

## Which would I use?

There's no single winner, but there are clear picks for clear jobs:

- **For agent evals on your own machine: Inspect.** It's the only one with pass@k and pass^k built in, it keeps every
  judge request word for word, its viewer is good, and it had the least friction. The cost: converting your agent's
  transcript into its types.
- **For a check in CI: Promptfoo.** Clean exit codes, JUnit output, and cheap. Watch its combined scores, which are means,
  not pass rates.
- **If your agents are Pydantic AI: Pydantic Evals** moves to the front, for the reason above, provided you store and
  review results somewhere (Logfire, or any tracing backend) and write your own judge. Its stock judge decides too much
  about what the judge sees.
- **A hosted platform only if a team needs to review together,** and only once you've checked what it uploads and what
  it does with a human's correction.
- **Not DeepEval for anything that gates on a judge,** and **OpenAI Evals only as history:** its latest release doesn't
  install on Python 3.14, it has no repeats, and it exits 0 whatever the score.

## What it cost

About $1 in API calls for all eight ports, including smoke runs and checks: $0.04 to $0.07 per 30-trial run. The time
went on glue, mostly the same glue every time: getting the agent's tool calls in, and getting one row per trial back out.

## Smaller lessons from this step

- **Your own harness is a fair baseline.** A few hundred lines did everything this eval needed. What a framework adds is
  storage, a viewer and collaboration, not better grading.
- **Check where a tool sends things before you check its features.** Every surprise in this part was found by watching
  the traffic or reading back what was stored, never by reading the docs.
- **Repeats are where the tools are weakest.** One computes pass@k; two have no repeats at all; the hosted comparisons
  either flag noise as regressions or hide two trials in three.
- **Package names collide.** OpenAI's framework is called `evals`, the same as my project's own package, and installing
  one replaced the other.
- **These findings date fast.** Everything here is from the versions of 2026-11-16. Check yours.

---

That's the end of the series. Part 1 built the eval, part 2 checked the judge, part 3 turned results into decisions and
put them in CI. This part showed that the frameworks change the plumbing, not the answers, except where their defaults
quietly change what you're measuring.
