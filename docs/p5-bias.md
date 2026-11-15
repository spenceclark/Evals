# P5: judge bias (outputs O11, O12, O13)

Code: one script per experiment in [experiments/](../experiments/): [make_pairs.py](../experiments/make_pairs.py) (freezes
the pairs), [position.py](../experiments/position.py), [length.py](../experiments/length.py),
[self_preference.py](../experiments/self_preference.py). Shared plumbing: [src/supportdesk/bias.py](../src/supportdesk/bias.py).
Human preferences: [scripts/prefer.py](../scripts/prefer.py), saved to `cases/pairs/human.jsonl`. Raw reports:
`runs/p5/*.txt` (gitignored).

All bias runs use **Sonnet 5.5** as the judge, except where the judge model is the variable. Every pairwise judgement uses
the P4 pairwise prompt with one sentence removed ("Length and polish matter only after those"), so the baseline doesn't
already tell the judge how to treat length. Intervals are 95%: Wilson for counts, percentile bootstrap over pairs for
shares.

## The pairs

A pair is two replies to the same customer email: one by Haiku 5.5 and one by Qwen 9B, taken from the P2 and P3 runs.
They were frozen before any judge saw them, in `cases/pairs/`:

- **pool: 200 pairs from 66 cases** (about three per case), for the judge-only position experiment.
- **human set: 50 of those pairs, one per case.** Each was shown blind, Haiku in slot A for exactly 25, plus 5 of the
  first 25 shown again later with the slots swapped. That's 55 judgements in all.

These are close pairs, not the lopsided pass-vs-fail pairs of P4: both replies pass every outcome grader in 184 of the 200
(the code checks on the decision and the store, P2–P3).
They are not balanced, though, and that matters for everything below:
- **Haiku's reply is longer in 180 of 200** (45 of the 50 human pairs; median 1.5 times as long).
- **Every invented refund timeline is Qwen's** (62 in the pool, 15 in the human set; Haiku has none).

### The human preferences

55 judgements took 33 minutes (median 24 seconds each), with a reason given on 47.

- **Haiku's reply was preferred on 45 of 50 pairs,** Qwen's on 4, and one was a tie. Haiku share 0.91 [0.83–0.98].
- **All 15 pairs where Qwen invents a timeline went to Haiku** ("3-5 business days is not policy").
- **Where a reason was given, the Qwen picks were for brevity and dates, not length:** "no pointless talk about return
  windows", "uses formatted dates", "users proper date formatting" (the fourth had no reason). In 3 of the 4, Qwen's
  reply was the shorter one.
- **Swapped repeats: the same answer on 3 of 5.** Both reversals were first picks given as "friendlier". On the second
  showing the plainer reply won ("if its not been shipped then not being delivered also is a given!"). The factual pick
  ("3-5 business days is not policy") held both times. Five repeats are too few to generalise from.

## O11: position bias

Every pool pair was judged three times: Haiku's reply in slot A, Qwen's in slot A, and Haiku's in slot A again. The third
run is the noise floor: how often the same prompt gets a different answer with nothing changed.

| Sonnet 5.5, 200 pairs | Result |
| --- | --- |
| Same order, asked twice: same winner | 190/200 = 95% [91–97%] |
| Orders swapped: same winner | 183/200 = 92% [87–95%] |
| Slot A wins, all decisive verdicts | 201/388 = 52% [47–57%] |
| Order-inconsistent pairs | 17: 11 decisive flips, 6 a tie in one order only |
| Slot A wins both orders, among the 11 decisive flips | 9/11 = 82% [52–95%] |
| Ties | 12 of 400 verdicts |

**Swapping the order adds about 7 inconsistent pairs on top of the 10 that come from plain resampling.** Overall the
judge has no slot preference (52%). Where it does flip with the order, it mostly sides with slot A (9 of 11), but that's 11
pairs and the interval runs from 52% to 95%. So: a small primacy lean on close calls, inside a judge that is mostly
consistent. Cluster-bootstrapping by case gives the same consistency rates (95% and 91%).

**Mitigation: judge both orders, and call a disagreement a tie.** Checked against the human's 50 pairs:

| Sonnet 5.5 vs human, 50 pairs | Same answer | Opposite pick | Right among its decisive picks | κ |
| --- | --- | --- | --- | --- |
| Haiku's reply first only | 41 (82%) | 6 (12%) | 41/48 (85%) | 0.10 |
| Qwen's reply first only | 39 (78%) | 7 (14%) | 39/47 (83%) | -0.01 |
| Both orders, flip = tie | 38 (76%) | 4 (8%) | 38/43 (88%) | 0.06 |
| *Always pick Haiku (baseline)* | *45 (90%)* | *4* | *45/50* | *0* |

The mitigation **turns a few wrong picks into abstentions:** opposite picks go from 6–7 to 4, and the decisive picks
are right slightly more often. It doesn't raise agreement, because the human almost never says "tie" (once in 50). The
intervals overlap throughout. Here it costs one extra call per pair and buys little, because the judge is already
mostly order-consistent. On P4's lopsided pass-vs-fail pairs, position barely registered (Opus 27/27 consistent, Haiku 26/27).

**Judge-vs-human agreement is below the trivial baseline.** The human picked Haiku on 45 of 50 pairs, so "always Haiku"
agrees 90% of the time. Any single-order Sonnet scores 78–82%. Kappa, which corrects for that base rate, is about zero. On
this set Sonnet tracks the base rate, not the human's pair-by-pair choices; see O13.

## O12: length bias

Two pads, each adding no information, inserted into a reply without touching its greeting, body or sign-off:
- **polite:** a courtesy sentence before the body and one after it ("Thank you so much for taking the time to get in
  touch…", "We truly value you as a customer…");
- **policy:** a correct one-sentence restatement of the whole refund policy, before the sign-off.

Neither pad adds a timeline, a remedy or anything the policy doesn't say, so neither should fail rubric v2 on its own.
Replies come from the P4 labels: 40 human-passed replies (one per case, seed 7) and all 38 human-failed ones. Two
prompts: the neutral one, then the same prompt plus an anti-length instruction **written before any result was seen**:
"Do not let length or politeness decide. A reply is not better for being longer…".

| Sonnet 5.5 | Neutral prompt | Anti-length prompt |
| --- | --- | --- |
| Pairwise: passed reply vs its polite-padded copy, padded share (tie = ½) | **0.00** [0.00–0.00] | 0.18 [0.12–0.24] |
| … the 80 verdicts (2 orders × 40): original wins / tie / padded wins | 80 / 0 / 0 | 51 / 29 / 0 |
| Pairwise: passed reply vs its policy-padded copy, padded share (tie = ½) | **0.00** [0.00–0.00] | 0.26 [0.19–0.33] |
| … the 80 verdicts: original wins / tie / padded wins | 80 / 0 / 0 | 38 / 42 / 0 |
| Rubric v2 on the 40 passed replies: verdicts changed by padding (either pad) | 0 | 0 |
| Rubric v2, same reply asked again: verdicts changed (noise floor) | 0 | 0 |
| Rubric v2 on the 38 failed replies: rescued by polite padding | 0 (5 pass either way) | 0 |
| 27 P4 pairs, failed reply polite-padded: passed reply's win share | 1.00 (1.00 unpadded) | 1.00 |

**On this task the judge penalises filler instead of rewarding it.** A padded copy of a good reply lost every
comparison, in both orders, with both pads. The rubric judge didn't move at all: no reply changed verdict, padding
rescued none of the 38 failed replies, and it didn't change a single P4 pairwise outcome. So the textbook harm, a longer
but worse reply winning, didn't appear.

**The anti-length instruction moved the judge toward indifference, but not to it.** The padded copy still never won a
single verdict (0 of 160). What changed is that 29 of 80 (polite) and 42 of 80 (policy) verdicts became ties. So the
judge did what the instruction asked, partly: it stopped holding filler against a reply about a third to a half of the
time. Whether a person would also call these ties wasn't tested; the human never compared a padded reply with its
original.

**Scope.** This tests obvious filler on an otherwise identical reply. It doesn't test a longer reply with *different*
content, which is the realistic case and the one the self-preference pairs carry (Haiku longer in 45 of 50). The rubric
being immune is partly by design: its criteria are about accuracy, promises and tone, none of which a courtesy sentence
touches.

Cost: 1,952 Sonnet calls, $10.11.

## O13: self-preference

Four judges, two families, scoring the 50 human pairs in both orders with the neutral prompt: Sonnet, Haiku and Opus
(Claude, the same family as the Haiku replies), and Qwen 9B running locally (the same model that wrote the Qwen
replies). Per pair, a judge's Haiku share is 1 if Haiku's reply wins both orders, 0.5 for a split or a tie, and 0 if
Qwen's wins. **Self-preference = the judge's Haiku share minus the human's on the same pairs** (for the Qwen judge,
self-preference would show as a *negative* number).

| Judge (family) | Answered | Same winner both orders | Haiku share | Excess over human | Excess, no timeline pairs (35) | κ vs human |
| --- | --- | --- | --- | --- | --- | --- |
| Human | 50 | 3/5 repeats | 0.91 [0.83–0.98] | — | — | — |
| Sonnet 5.5 (Claude) | 50 | 44/50 | 0.90 [0.81–0.96] | -0.01 [-0.12 to +0.08] | -0.02 [-0.16 to +0.12] | 0.06 |
| Haiku 5.5 (Claude) | 50 | 44/50 | 0.95 [0.91–0.98] | +0.04 [-0.02 to +0.11] | +0.06 [-0.01 to +0.16] | 0.32 |
| Opus 5.5 (Claude) | 50 | 50/50 | 0.85 [0.74–0.94] | -0.06 [-0.19 to +0.06] | -0.09 [-0.27 to +0.09] | -0.03 |
| Qwen 9B (Qwen) | 50 | 42/50 | 0.93 [0.87–0.97] | +0.01 [-0.07 to +0.10] | +0.05 [-0.06 to +0.17] | 0.09 |

As a matrix (share of pairs won by each author family, both orders):

| Judge family ↓ / author → | Haiku's replies | Qwen's replies |
| --- | --- | --- |
| Human | 0.91 | 0.09 |
| Claude (mean of 3 judges) | 0.90 | 0.10 |
| Qwen | 0.93 | 0.07 |

**No self-preference was detected in either family.**
- **The Qwen judge is the informative one.** It had all the room in the world to favour its own replies (the human gave
  Qwen only 0.09), and it didn't: it preferred Haiku's replies *slightly more* than the human did, +0.01 [-0.07 to
  +0.10]. Any lean toward its own family is at most about 0.07 on this set.
- **The Claude judges couldn't show much.** With the human at 0.91, no judge could exceed the human by more than +0.09.
  The three land between -0.06 and +0.04, all intervals including zero. Haiku judging Haiku is the highest (+0.04), and
  the gap widens a little without the timeline pairs (+0.06 [-0.01 to +0.16]), but that's inside the noise.
- **The interaction test agrees.** If each family favoured its own, a Claude judge minus the Qwen judge would be
  positive. It is -0.03 [-0.10 to +0.03] (Sonnet), +0.03 [-0.04 to +0.09] (Haiku) and -0.07 [-0.17 to +0.01] (Opus).

**What this does and doesn't rule out.** Every judge and the human prefer Haiku's replies at about the same rate. That
fits the simplest story: Haiku's replies were better on this task (no invented timelines, a fuller account of what
happened). It can't separate "better" from "longer", since Haiku's reply is longer in 45 of the 50 pairs. A 9B model may
also be too weak to recognise its own writing, which the self-preference literature links to favouring it (Panickssery
et al. 2024). A real test of the Claude side needs pairs where a person is closer to 50/50.

**Agreement with the human, pair by pair, is poor for every judge.** "Always Haiku" matches the human on 45 of 50. The
judges match 38–43, κ from -0.03 to 0.32. Disagreement split by the human's pick:

| Judge | Differs where the human picked Haiku | Differs where the human didn't |
| --- | --- | --- |
| Sonnet | 7/45 | 5/5 |
| Haiku | 3/45 | 4/5 |
| Opus | 7/45 | 5/5 |
| Qwen | 6/45 | 5/5 |

No judge picked Qwen's reply in both orders on any of the human's four Qwen picks. The human's reasons there (three of the four
gave one) were brevity and date formatting, which none of the judges seems to weigh. On the human's Haiku pairs, the judges depart in different ways. Sonnet,
Haiku and Qwen mostly split or tie: the Qwen judge never once picked its own family's reply in both orders. Opus never
splits. All 7 of its departures are Qwen picks in both orders, so the most capable Claude judge is the one most willing
to side with the other family.

**Mitigation: a cross-family panel** (the mean of a Claude judge and the Qwen judge). Excess over the human: Sonnet+Qwen
+0.00 [-0.08 to +0.09], Haiku+Qwen +0.03 [-0.04 to +0.10], Opus+Qwen -0.02 [-0.12 to +0.07]. With no bias to remove, the
panel shows only that averaging across families does no harm here. It doubles the judging cost (or adds a local GPU).

**The local judge, this time.** In P4, Qwen gave no verdict on 15 of 40 rubric items (a 4,096-token budget). Here, with
6,200 tokens of its 8,192 context, it answered all 50 pairs. 2 of its 102 calls were truncated: one in the 5,000-token
pilot and one at 6,200. Both were retried once at a new sample index and answered. It took a median of 47 s per call,
3,290 output tokens, one call at a time: about 84 minutes in all (a 9-minute pilot, then 75 minutes for the
rest).

Cost: 300 Claude calls, $2.02; 102 Qwen calls, local.

## Cost and time

| Experiment | Judge calls | $ |
| --- | --- | --- |
| Position (Sonnet, 200 pairs × 3) | 600 | $3.75 |
| Length (Sonnet) | 1,952 | $10.11 |
| Self-preference, Claude judges (50 pairs × 2 orders × 3 judges) | 300 | $2.02 |
| Self-preference, Qwen 9B (local) | 102 | $0 |
| **Total** | | **$15.88** |

The spec budgeted about $20 for ~1,000 calls. The human side was 33 minutes for 55 judgements.
