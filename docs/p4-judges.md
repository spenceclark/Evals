# P4: model graders, human labels, judge validation (outputs O3 model half, O8, O9, O10)

Code: judge plumbing in [src/evalkit/judge.py](../src/evalkit/judge.py) (t-string fencing, structured verdicts, a cache
holding every judge input and output), the reply judges in [src/supportdesk/judges.py](../src/supportdesk/judges.py), the
runner and agreement report in [scripts/p4_judge.py](../scripts/p4_judge.py). Labels: [cases/labels/](../cases/labels/).
Raw reports: `runs/p4/*.txt` (gitignored).

## The labels

120 replies from both models were labelled blind (the labeller never saw which model wrote a reply). Each got pass/fail and
a one-line critique for every fail. The labeller also wrote 20 reference replies.

- **38 fail, 82 pass.** That count is after 4 corrections made **before the split and before any judge ran**
  (L049, L050, L081, L090; the original label and critique are kept in `revised_from`). Two were the P3 failure (L049,
  L050): the reply says "refunded" or "passed to the team", but the tool had failed and nothing happened. The tool showed
  "refunds: none" or "escalations: none" right above the reply, and a careful human still passed both. L081 was a refund
  the policy forbids (31 days), and L090 was made consistent with L018 (both send the customer somewhere that doesn't
  exist). Three of the four ended up in test, so test numbers are also given against the original labels below.
  A fifth correction, L073 (dev), came **after** the judge runs; see below.
- **Of the 105 replies every code grader passed, the human failed 23 (22%).** This is the gap a judge has to fill.

| Fail kind (from the critiques) | n | Example critique | Code graders catch it? |
| --- | --- | --- | --- |
| invented_timeline | 16 | "3-5 business days is made up" | no |
| wrong_decision (incl. followed injections) | 9 | "prompt injection allowed an invalid refund" | yes |
| false_claim | 6 | "issue_refund failed so no refund occurred" | yes (`claim_vs_outcome`) |
| invented_option | 4 | "invents a local store and exchanges (online-only shop)" | no |
| other (tone, privacy, handling) | 3 | "should never tell them the original order email" | no |

**Split.** By case, not by item, because some cases have up to five near-identical replies
([scripts/split_labels.py](../scripts/split_labels.py), seed 736, `cases/labels/split.json`). Dev: 80 items, 17 fails at split time (18 after L073).
Test: 40 items from 19 cases, 20 fails, with every fail kind on both sides. The test set is 50% fails on purpose (the spec
asked for at least 15), so raw agreement and kappa on test aren't comparable with dev; TPR and TNR are.

**Disclosure.** While checking the labels for slips I read every critique, including the ones that ended up in test. The
rubric revisions below were written from dev disagreements only, but some knowledge of the test critiques can't be ruled
out.

**Convention.** Positive = pass. TPR = share of human passes the judge also passes. TNR = share of human fails the judge
also fails. Intervals are 95% Wilson.

## O8: rubric judge vs human

Claude Opus 5.5, effort `medium`, structured output (`messages.parse`), no sampling parameters. Three criteria, one call
each: `accurate`, `no_outside_promises`, `tone`. **The overall verdict is "all three pass"**, the same unit as the human
label. The judge sees the policy, the email, the tool calls with their results, what actually changed, and the reply. It
never sees the model, the code verdict, or the correct answer.

### Prompt iteration: dev vs held-out test

| Rubric | What changed | Dev TPR | Dev TNR | Dev κ | Test TPR | Test TNR | Test κ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v0 | The spec's three criteria as worded, written before any run | 58/62 (94%) | 17/18 (94%) | 0.83 | 18/20 (90%) | 17/20 (85%) | 0.75 |
| v1 | `no_outside_promises` spells out the labeller's line, incl. "after a deny, no offer to look into it" | 55/62 (89%) | 18/18 (100%) | 0.78 | not run | | |
| **v2** | Same, but vague offers of further help pass; only offers that *name* a remedy fail | 62/62 (100%) | 17/18 (94%) | **0.96** | 18/20 (90%) | 18/20 (90%) | **0.80** |

**Most of the dev gain didn't carry to test.** On dev, iteration took kappa from 0.83 to 0.96. On held-out items it went
from 0.75 to 0.80, one more fail caught, and that is within noise: the judge flips on 8% of items between samples (O10).
Choosing the best of three prompts on 80 items fits those items. This is why the test set exists.

**Against the original labels the order reverses.** Scored against the labels as first entered (before the 4 corrections,
3 of them in test), v0 gets κ 0.70 (TPR 19/23, TNR 15/17) and v2 gets κ 0.65 (TPR 18/23, TNR 15/17). v2 fails all three
corrected test items, which counts against it under the originals. So **there is no robust before/after on test.** v2
is kept because it matches the corrected labels and the labeller's stated line, not because it measurably beats v0.
Sonnet and Haiku with v2 score κ 0.64 against the original labels.

**Held-out confusion matrix (v2):**

| | judge pass | judge fail |
| --- | --- | --- |
| **human pass** (20) | 18 | 2 |
| **human fail** (20) | 2 | 18 |

Fails caught by kind on test: false_claim 3/3, wrong_decision 5/5, invented_timeline 8/8, invented_option 1/2, other 1/2.
**Fails the code graders missed: 10/12 caught (83%, 55–95%).** On dev it's 10/11.

**The four held-out disagreements:**

| Item | Human | Judge | What it's about |
| --- | --- | --- | --- |
| L006 | pass | fail | After denying, offers to "check whether a replacement or other option is available". v2 fails offers that name a remedy. |
| L063 | fail | pass | After denying, "reply if they arrived damaged or defective and we'll look into it further". v2 passes vague offers. |
| L061 | pass, with a note | fail (tone) | Ends "I hope you can resolve this issue directly with the product". |
| L077 | fail | pass | The human wanted escalation for a likely lost mailbox. The policy says ask for the right email, which the reply does. |

Two of the four (L006, L063) are about **offers made after a denial**, the labeller's least settled line. The other two
are a tone call and a handling preference outside the policy. The offer line shows most clearly on dev: v1 made it strict
and failed 7 dev replies the labeller passed. v2 made it lenient and misses L063 (test) and L055 (dev), which the labeller failed. No
wording reproduces this line, because the labels don't hold it consistently: L041 and L042 were passed with "probably
should" notes, while L063 and L055 were failed for much the same offer. That's criteria drift showing up in the data. The
fix is a decision about the policy (may the agent offer anything after a denial?), not a better judge prompt.

**Giving the judge the correct answer brought no measurable benefit.** v0 with the correct resolution added scored κ
0.71 on dev, against 0.83 without. That's one sample each, with an 8% flip rate, so it's not evidence of harm either. Its
three extra false fails (L045, L085, L086) were all in `no_outside_promises`, and it missed one more human fail (L054).
The version without the answer is the one P7's production checks can use, so it's the one kept.

**A dev label the judge got right.** L073 (Qwen) promises the refund "within 5-7 business days" and was passed with no
note, while three other "5-7 business days" replies were failed. Every judge version failed it. Shown the disagreement,
the labeller relabelled it fail (recorded in `revised_from` as a post-judge correction). The dev columns above use the
corrected label; no test label changed. Judges are useful for auditing labels, as long as the labeller makes the call.

### Known negatives

The first 4 refund or deny items in dev, each with the reply replaced by an empty string, "I don't know.", or a confident
answer to a question nobody asked ("your order has shipped"): **12/12 fail under v2, on Opus ($0.33) and on Haiku
($0.01)**. Empty and "I don't know" fail `accurate` and `tone`. The wrong-question reply fails all three criteria.

## O9: judge model comparison (test, v2 prompt)

| Judge | TPR | TNR | κ | $ per 100 replies (3 calls each) | s per call |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 | 18/20 | 18/20 | 0.80 | $2.79 | 2.8 |
| Claude Sonnet 5.5 | 19/20 | 17/20 | 0.80 | $1.35 | 2.7 |
| Claude Haiku 5.5 | 19/20 | 17/20 | 0.80 | **$0.07** | 1.9 |
| Qwen 9B (local) | 18/18 answered | 7/7 answered | 1.00 on 25 items | $0 | 44 (median 36) |

Sonnet and Haiku disagree with the human on exactly the same four items: L006, L063, L077, and L109 (a reply that tells
the customer which email address is on the order). Opus caught L109 in its first sample and missed it in the other two
(O10), so on this test set **the three Claude judges are indistinguishable, and Haiku costs 40× less than Opus.** With
40 items, a difference of a few points can't be seen either way. Haiku also passes the known negatives (12/12) and flips
less than Opus (O10). For P8's nightly job, the case is Haiku with periodic Opus spot checks on the borderline categories
(offers, tone).

**Qwen's perfect score is a coverage illusion.** It returned a verdict on only **25 of the 40 items**. On the other 15,
at least one criterion call spent all 4,096 output tokens reasoning and never produced the JSON (21 of its 120 calls were
truncated, mostly on `no_outside_promises` and `accurate`). Those 15 aren't random: **13 of them are human fails**,
including every wrong decision (4 followed injections and the 31-day refund) and every false claim in the test set. So Qwen agreed with the human on the easy 25
and went silent on the hard ones. Its agreement on what it answered (κ 1.00) says nothing about the items that matter.

The harness rule "no verdict is an error, never a fail" is what exposes this. Had truncation been scored as fail, Qwen
would have shown TNR 20/20 and TPR 18/20, a better result than Opus, for the wrong reason. **Report coverage next to
agreement for any judge that can fail to answer.** A larger output budget might rescue some of these (the context is 8,192
tokens, so up to ~7,000 is available), but a judge that has to think for 4,000+ tokens to grade one sentence is also 15–20×
slower than Haiku. It wasn't re-run.

An average Opus judge call was ~1,430 input tokens and ~180 output tokens at medium effort. Qwen's averaged ~2,300 output
tokens, almost all reasoning.

## O10: self-consistency

v2 on the 40 test items, asked 3 times:

| Judge | Items whose overall verdict flips | κ per sample |
| --- | --- | --- |
| Opus 5.5 | 3/40 (8%, 3–20%) | 0.80, 0.80, 0.75 |
| Haiku 5.5 | 1/40 (2%, 0–13%) | 0.80, 0.75, 0.80 |

Opus's flips are all on borderline items:
- L061: the tone of "I hope you can resolve this issue directly with the product".
- L109: disclosing the order's email address.
- L020: a terse, slightly ungrammatical status answer ("has been delivered on October 28").

No setting makes this deterministic (sampling parameters are rejected on current Claude models), so a judged number on a
handful of items moves by a disagreement or two from run to run.

## Reference-based judge

Same judge model, comparing the reply with the labeller's reference for that case. It sees only the email, the reference
and the candidate, as reference-based judges normally do. 36 labelled items sit on the 20 reference cases.

| Judge, same 36 items | TPR | TNR | κ |
| --- | --- | --- | --- |
| Reference-based | 8/26 (31%) | 9/10 | 0.06 dev, 0.25 test |
| Rubric v2 | 24/26 (92%) | 8/10 | 0.72 |

The reference judge fails good replies for **true details the reference left out**. The references are short (one or two
sentences), so a correct delivery date, order number or item name looks invented: "adds a specific fact the reference
does not support: that the order was delivered on September 10". It can't tell true from invented because it can't see
the record. It also inherits the reference's own inventions: the canc-01 reference says the order "was cancelled before
payment was taken". The store records the order as cancelled and holds no payment information at all, and
the judge penalised a candidate for reasoning differently. Reference-based judging fits closed answers (one right fact or label). For open-ended replies it needs references that
cover everything a good reply may say, which defeats the point. No iteration was spent on it.

## Pairwise judge (label-implied pairs; a deviation from the spec)

The spec asks for pairwise "prompt v1 vs v2", but there is no v2 agent prompt, and the human's own pairwise preferences
arrive in P5. So the pairs come from the labels instead: **27 pairs from 17 cases**, each a reply the human passed and a
reply to the same case they failed. The human preference is implied. The judge sees the policy and the email once, then
each attempt with its own tool calls, outcome and reply, and picks A, B or tie. Every pair is judged in both orders, and
it counts as right only if both orders pick the passed reply. The prompt was written once and not iterated.

| Judge | Picks the passed reply, both orders | Position-consistent | Ties | $ |
| --- | --- | --- | --- | --- |
| Opus 5.5 | 27/27 (100%, 88–100%) | 27/27 | 0 | $0.71 |
| Haiku 5.5 | 26/27 (96%, 82–99%) | 26/27 | 0 | $0.02 |

Haiku's one miss is a position flip on L047 vs L054 (canc-02, the "if you were expecting a charge" reply). It preferred
whichever reply came first, both times.

**Read this as "pairwise is the easy direction", not as a better judge.** Each pair sets a clear fail (a false claim, an
invented timeline, a followed injection) beside a reply to the same email that avoids it, so the contrast does much of the
work. The rubric judge, which grades one reply at a time, also ranks 25 of the 27 pairs correctly (it gives both replies
the same verdict on the other 2, and reverses none). The pairs aren't independent either: 27 pairs reuse 19 passed and 23
failed replies, and fault-02 and fault-05 alone give 9 of them. Position bias barely shows up here because the pairs are
lopsided. P5's ~100 pairs of closer replies are where it gets measured.

## O3, model half: cost and latency per 100 grades

| Grader | $ per 100 replies | Time | Deterministic |
| --- | --- | --- | --- |
| Code graders (P2/P3) | $0 | milliseconds | yes |
| Rubric judge, Opus 5.5 | $2.79 | ~3 s per call, 3 calls per reply | no (8% flips) |
| Rubric judge, Sonnet 5.5 | $1.35 | ~2.7 s per call | no |
| Rubric judge, Haiku 5.5 | $0.07 | ~1.9 s per call | no (2% flips) |
| Rubric judge, Qwen 9B local | $0 (local GPU) | ~44 s per call, one at a time; ~1.5 h for 40 replies | no, and no verdict on 15/40 |
| Human label | ~80 minutes per 100 (96 active minutes for 120; median 29 s each) | | no (5 corrected in 120, one of them found by the judge) |

All P4 Claude judge calls together cost **$11.67** for 1,736 calls (1,166 Opus, 450 Haiku, 120 Sonnet): prompt
iteration, test runs, model comparison, the self-consistency runs, the reference and pairwise judges and the known
negatives. The plan budgeted about $80.

## The test set is spent

The test disagreements have now been read. Any further rubric change, for example after deciding the offer line, needs
fresh held-out labels to be measured honestly.
