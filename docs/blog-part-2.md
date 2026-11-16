<!--
Draft in Claude's words, for you to rewrite. Split from blog.md sections 6 and 7 on 2026-11-16, shaped like the
published part 1 (short title and dek, opens on a finding, no section numbers); blog.md is left as it was.
Every number here comes from the P4/P5 runs, reproduced on one commit: tag part2-data (6d4f967), where all 4,703 judge
calls were cache hits and every figure matched docs/p4-judges.md and docs/p5-bias.md.
The three paper links were checked against arXiv on 2026-11-16 (Shankar et al., Zheng et al., Panickssery et al.).
Cut from published part 1 and not used here: the P3 "recovery" judge preview (Opus agreed with the code on 120/120
fault trials, an easy and lopsided set). It would fit before "Split before you tune" if you want it back.
Remove this comment before posting.
-->

# Who grades the grader?

*I checked an LLM judge against 120 replies I graded myself, then tested whether it plays fair.*

One of my judges agreed with me on every reply it graded. It had also skipped 15 of the 40, and 13 of those were replies
I'd failed: every wrong decision and every false claim in the set. It agreed with me on the easy ones and went quiet on
the hard ones.

That judge was a small local model, and it's the clearest example of why a judge needs checking before it's trusted. In
[part 1](https://spencerclark.dev/blog/a-passing-grade/) everything was graded by code: the agent's decision, what
changed in the shop, and the steps it took. Code can't grade the reply the customer reads. Is it true? Does it promise
something the shop doesn't do? Does it sound right? That needs a model as the judge, and a model judge is just another
model's opinion. So this part does two things: it measures how often a judge agrees with a person, and then it tests
the three ways judges are known to lean.

The person was me.

## Checking the judge against a person

### Labelling 120 replies

I graded 120 replies from part 1's two models, Claude Haiku 5.5 and Qwen3.5 9B, without knowing which model wrote which.
Each got pass or fail, plus a one-line critique for every fail. The question was always the same: *would I be happy for
this reply to reach this customer, given what actually happened?* The labelling tool showed me the email, what the agent
did, what changed in the store and what the policy said, then the reply. It took 96 minutes, about 48 seconds a reply
(half took under 30).

I failed 38 replies in all. The code graders already catch some of those; the rest are new:

| Why I failed it | How often | Code graders catch it? | Example |
| --- | --- | --- | --- |
| Got the decision wrong, incl. following an injected instruction | 9 | yes | refunded because the email said a manager had approved it |
| Claimed something that didn't happen | 6 | yes | "we have refunded $29.99" when the refund had failed |
| Invented a refund timeline | 16 | no | "should appear within 3-5 business days" (nothing says so) |
| Invented an option that doesn't exist | 4 | no | "contact your local store" (it's an online shop) |
| Tone, privacy, handling | 3 | no | told the customer which email address was on the order |

**That's 23 failures among the 105 replies every code grader had passed: 22%.** All 16 invented timelines came from
Qwen. Haiku's four misses were subtler: two offers of help the policy couldn't back up, an oddly worded line, and an email
mismatch it handled by the book where I'd have passed it to a person.

**I made part 1's mistake myself.** Twice I passed a reply that said "we have refunded" or "I've passed this to the
team" when the tool had failed and nothing had happened, with "refunds: none" printed right above it. A polished reply
hides a false claim from people as well as from graders that only read the reply. I corrected those, and two other
slips, before any judge ran, and kept the originals on record.

### Split before you tune

I split the labels into a **dev** set (80 replies) to tune the judge on, and a **test** set (40) that I didn't look at
until the end. The split was by customer email, not by reply: some emails had five near-identical replies, and one on
each side would have leaked. I gave the test set 20 fails on purpose, so it can show how many fails the judge catches.

From here on, two numbers matter more than "accuracy":
- **passes kept:** of the replies I passed, how many the judge also passed;
- **fails caught:** of the replies I failed, how many the judge also failed.

**Kappa** sums them up as agreement beyond what chance would give: 0 is chance, 1 is perfect.

### The judge, and what tuning it really bought

The judge checks three things, one call each: is the reply **accurate** about what happened and right under the policy;
does it make **no promises the policy doesn't support**; is the **tone** right. The reply passes only if all three pass,
the same unit as my label. The judge sees the policy, the email, the tool calls and results, and what changed. It doesn't
see which model wrote the reply or what the code graders said. Everything from the conversation is fenced off as data, so
an email saying "SYSTEM: approve this" is something to grade, not an instruction.

I wrote a first version (v0) straight from the plan before running anything, then improved it twice using only the dev
disagreements. Claude Opus 5.5 was the judge:

| Version | Dev: passes kept | Dev: fails caught | Dev κ | **Test: passes kept** | **Test: fails caught** | **Test κ** |
| --- | --- | --- | --- | --- | --- | --- |
| v0, as planned | 58/62 | 17/18 | 0.83 | 18/20 | 17/20 | 0.75 |
| v2, after two rounds | 62/62 | 17/18 | **0.96** | 18/20 | 18/20 | **0.80** |

On dev, tuning looks like a big win: 0.83 to 0.96, matching me on 79 of 80. On the replies it was never tuned on, it went
from 0.75 to 0.80: one more fail caught, inside the judge's own run-to-run wobble. Scored against my labels *before* the
corrections, the order even reverses (v0 0.70, v2 0.65). **If I'd reported the dev number, I'd have reported 0.96.** This
is the whole reason for a held-out set.

Still, the final judge is good. It catches every invented timeline, every false claim and every wrong decision in the
test set, and 10 of the 12 fails the code graders missed. It also found one of my own mistakes: a "5-7 business days"
reply I'd passed while failing three identical ones. I changed that label myself; the judge only pointed at it.

### Where the judge and I disagree: my line wasn't clear

The judge's most stubborn disagreements with me are about one thing: what the agent offers *after* turning down a
refund. "Send us photos and we'll see whether there's another way to help." Is that kind, or a promise the policy can't
keep? It's behind 2 of the 4 test disagreements and most of the dev ones.

My labels go both ways. I passed some of those offers ("probably should offer it") and failed others for much the same
wording. v1 of the judge took the strict reading and failed seven replies I'd passed. v2 took the lenient one and misses
two I'd failed. No wording can match a line the labels don't hold. Shankar et al. call this **criteria drift**
([2024](https://arxiv.org/abs/2404.12272)): you find out what you think while labelling. The fix isn't a cleverer judge
prompt. It's a decision about the shop's policy, written into the agent's instructions.

### Cheaper judges, and one that only looked good

Same final prompt, same 40 test replies, different judge models:

| Judge | Passes kept | Fails caught | κ | Cost per 100 replies | Time per call |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 | 18/20 | 18/20 | 0.80 | $2.79 | 3 s |
| Claude Sonnet 5.5 | 19/20 | 17/20 | 0.80 | $1.35 | 3 s |
| Claude Haiku 5.5 | 19/20 | 17/20 | 0.80 | **$0.07** | 2 s |
| Qwen 9B, local | 18/18 | 7/7 | 1.00 | $0 | 44 s |

The three Claude models can't be told apart on 40 replies, and **Haiku costs 40 times less than Opus.** That makes it
the judge for routine runs, with Opus spot checks on the borderline cases.

Qwen's perfect row is the one I opened with, and the most useful result in this part, because it's wrong. Look at the
counts: 18 and 7 add up to 25, not 40. On 15 replies it reasoned until it ran out of room and never gave a verdict, and
**13 of those 15 were replies I'd failed.** Had "no verdict" been counted as "fail", it would have looked better than
Opus, for the wrong reason. **Always report how many items a judge actually answered.**

### How much does a judge wobble?

There's no setting that makes a current Claude model answer the same way twice; sampling settings are rejected. So I
asked the same 40 questions three times. Opus changed its overall verdict on 3 of the 40 replies (8%) and Haiku on 1
(2%). Opus's flips were all borderline: a slightly dismissive closing line, a tersely worded status update, a reply that
revealed the order's email address. On a small set, a judged score can move by a point or two between runs with nothing
changed.

### Reference answers and pairwise

I also wrote 20 "ideal" replies by hand, for a judge that compares each reply with mine. It was the worst judge by far:
it kept **31% of the good replies**. My references were short, so a correct delivery date or order number in the agent's
reply looked invented. The judge couldn't check, because it only saw my reference. It also copied my own small
invention: I'd written that an order was "cancelled before payment was taken", which the records don't say. Comparing
against a reference suits questions with one right answer, not open-ended replies.

A **pairwise** judge shows two replies to the same email and asks which is better. Built from pairs where I'd passed one
and failed the other, and asked in both orders, Opus picked my choice 27 times out of 27 and Haiku 26. That sounds
impressive, but these were easy pairs (a clear failure next to a clean reply), and the one-at-a-time judge ranks 25 of
the same 27 correctly. The harder test is close pairs, and that's the second half of this part.

### Smaller lessons from this half

- **Giving the judge the right answer didn't help.** With the correct decision added to its input it did slightly worse,
  within the noise. Its disagreements weren't about the decision. They were about where to draw the line on promises.
- **Test the judge on obvious failures.** An empty reply, "I don't know", and a cheerful answer to a question nobody asked
  were all failed, by Opus and by Haiku, 12 times out of 12.
- **Judges are good at auditing labels.** Every place the judge disagreed with me was worth a second look. Once it was my
  mistake. More often it was my line that was blurry.
- **Labelling was faster than expected,** under a minute a reply, as long as the tool puts what actually happened right
  next to the reply.

## Does the judge play fair?

Judges are known to lean three ways: toward whichever answer comes first, toward the longer answer, and toward output
from their own model family. Zheng et al. ([2023](https://arxiv.org/abs/2306.05685)) named all three, and Panickssery et
al. ([2024](https://arxiv.org/abs/2404.13076)) looked closely at the last one, both on general chat questions. The
question here is narrower: how big are they on *this* task, and does the usual fix work?

That needs pairs that are actually close, which the pairwise test above didn't have. So I froze 200 new pairs, each a
Haiku reply and a Qwen reply to the same customer email. In 184 of the 200, both replies pass every outcome check from
part 1.

### My half: 50 blind preferences

Before I'd seen any judge result, I picked a winner in 50 of those pairs, with the models hidden and Haiku on the left
exactly half the time. Five pairs came back later with the sides swapped, so I could check myself. It took 33 minutes.

I picked Haiku's reply 45 times, Qwen's 4 times, and called one a tie. All 15 pairs where Qwen invented a refund
timeline went to Haiku. Where I gave a reason for picking Qwen's reply, it was for being shorter or formatting dates
better ("no pointless talk about return windows").

**I wasn't consistent either.** On the five swapped repeats I gave the same answer three times. Both reversals were
first picks I'd explained as "friendlier", and the second time I preferred the plainer reply. The factual call, "3-5
business days is not policy", held both times. Five is too few to generalise from, but the shape is plausible: facts are
stable, taste isn't.

### Position: a small lean, only on close calls

Sonnet judged each of the 200 pairs three times: Haiku first, Qwen first, and Haiku first again. The third run is the
noise floor: same question, nothing changed.

| | Same winner |
| --- | --- |
| Same order, asked twice | 190/200 (95%) |
| Order swapped | 183/200 (92%) |

**Swapping the order costs about 7 pairs beyond plain run-to-run noise.** Overall, the first slot won 52% of decisive
verdicts, which is no lean at all. Among the 11 pairs whose winner flipped with the order, though, the first slot won
both times in 9. That's a lean toward whatever comes first, but only when the judge is unsure, and 11 pairs give a wide
interval (52% to 95%).

The standard fix is to ask both orders and call a disagreement a tie. Against my 50 preferences it cut "picked the
opposite of me" from 6 or 7 pairs to 4, mostly by abstaining. It doubles the calls. With a judge this consistent, it
buys little.

### Length: the judge punished padding

I took 40 replies I'd passed and padded each one in two ways that add nothing: courtesy ("Thank you so much for taking
the time to get in touch…") or a correct restatement of the refund policy. Then I asked the judge to choose between each
reply and its padded copy.

**The padded copy lost 80 out of 80 times, in both orders.** The one-at-a-time judge didn't change a single verdict
because of padding, and padding didn't rescue any of the 38 replies I'd failed. The textbook harm, a longer but worse
reply getting through, didn't happen here.

I'd written an "ignore length" instruction before seeing any result. With it, the padded copy still never won a single
comparison, but about a third to a half of the verdicts became ties instead of losses. The judge did what it was told,
partly. **The fix was aimed at a bias that wasn't there.** Whether I'd also call those ties, I didn't test: I never
compared a padded reply with its original myself.

This only tests obvious filler on an otherwise identical reply. A longer reply with *different* content is the realistic
case. That's what the next experiment has: Haiku's reply was longer in 45 of my 50 pairs.

### Self-preference

The worry is that a judge prefers writing that sounds like its own. Here three Claude judges were scoring Claude Haiku's
replies against Qwen's, so they had an obvious favourite to lean toward. To tell that apart from Haiku's replies simply
being better, the same 50 pairs also went to a judge from the other family: Qwen 9B itself, running on my GPU.

| Who's judging | Share of pairs won by Haiku's reply |
| --- | --- |
| Me | 0.91 |
| Sonnet | 0.90 |
| Haiku | 0.95 |
| Opus | 0.85 |
| Qwen | 0.93 |

**Everyone, Qwen included, preferred Haiku's replies at about the rate I did.** No judge differs from me by more than
0.06, and every interval includes zero. The telling row is Qwen's. It was judging its own replies against Claude's, with
plenty of room to favour them, and it didn't: it never once picked its own reply in both orders on a pair where I'd
picked Haiku. The Claude judge most willing to side with Qwen was Opus.

That's a "not detected", not a "doesn't exist":
- **The Claude side couldn't show much.** With me at 0.91, no judge could out-Haiku me by more than 0.09. Haiku judging
  Haiku came out highest (+0.04), inside the noise and under that +0.09 ceiling.
- **Better and longer go together here.** Haiku's replies never invented a timeline and were longer in 45 of 50 pairs.
  "Every judge prefers Haiku" fits "Haiku was better" and "judges like length" equally well.
- **A 9B model may not recognise its own writing,** and recognising it is what the research ties to favouring it.

A judge panel mixing both families (average the Claude judge with Qwen) landed at zero excess too. With nothing to
remove, that only shows it does no harm.

**Agreeing with me on the total isn't the same as agreeing with me pair by pair.** "Always pick Haiku" matches me on 45
of 50. The judges matched 38 to 43. On the four pairs where I picked Qwen's reply, for being shorter or writing dates
properly, no judge agreed with me in both orders. The judges get the overall verdict right and my particular tastes
wrong.

Qwen as a judge also redeemed itself a little. In the first half it went silent on 15 of 40 replies. Here, with more
room to think, it answered all 50 pairs, at about 47 seconds a call.

### Smaller lessons from this half

- **A judge can match a person 82% of the time and still not be tracking them.** I picked Haiku on 45 of 50 pairs, so
  "always Haiku" matches me 90% of the time. Kappa, which corrects for that, put Sonnet, Opus and Qwen near zero and
  Haiku at 0.32. On a lopsided set, report kappa or report nothing.
- **Some tests can't fail.** With me at 91% Haiku, no judge could prefer Haiku by more than 9 points. Check that a test
  has room to show the effect before reading anything into "no effect".
- **Freeze the pairs and the human answers before any judge result is shown.** I didn't see a single judge number until
  my 55 answers were saved. Otherwise it's too easy to drift toward the judge.
- **Count by the thing you sampled.** Counted by verdict, the position lean looked like 75%. Counted by pair, which is
  what was sampled, it's 9 of 11 with an interval reaching down to 52%.

## What it cost

Checking the judges cost **$11.67** in Claude calls, almost all of it Opus during tuning. The bias tests cost **$15.88**,
plus about 84 minutes of my GPU for Qwen. The agents being judged cost almost nothing by comparison: every Haiku agent
run up to that point came to **$0.24**. In this setup the system under test is cheap and the grading is where the money
goes, which is another reason to check whether a cheap judge is good enough.

My own time was 96 minutes of labelling replies and 33 minutes of picking A or B.

## Next: from scores to decisions

The judge I'd trust for routine work is the cheapest one: Haiku, at 7 cents per 100 replies, as good as Opus against my labels
and with no measurable lean toward its own family. But a judge only gives a score, and a score on its own doesn't say whether the agent got better.
Part 3 asks when a difference between two runs is real, how much eval is enough, and what's left to check once the agent
is live and nobody has written down the right answer. It ends with the eval running on every change, where the judge
reports and code decides.
