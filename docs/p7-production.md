# P7: production checks, captures → cases, suite lifecycle (outputs O18, O19, O20)

**Status: done.** All three outputs. The traffic is synthetic (spec D4).

Code:
- The checks: [src/supportdesk/checks.py](../src/supportdesk/checks.py).
- The traffic generator and runner: [scripts/simulate_traffic.py](../scripts/simulate_traffic.py).
- The monitor, inbox, labelling and promotion: [scripts/captures_to_cases.py](../scripts/captures_to_cases.py).
- The lifecycle runs and graduation: [scripts/lifecycle.py](../scripts/lifecycle.py).
- The prompt versions: [src/supportdesk/shop.py](../src/supportdesk/shop.py) (`V1_PROMPT`, `SYSTEM_PROMPT`, `V3_PROMPT`).

The data is in `cases/traffic/` (the 210 messages and their facts), `cases/captured.jsonl` (the 20 labelled captures)
and `runs/p7/` (gitignored).

## The three prompt versions

| Version | What it is | Agent |
| --- | --- | --- |
| v1 | A plausible first draft: v2 without rule 1 (the sender must match the order; ask, never guess) and without "look up the order before deciding". An ablation, not a prompt that ever shipped. | `haiku_v1` |
| v2 | The prompt P1–P6 ran: `SYSTEM_PROMPT`. | `haiku` |
| v3 | v2 plus one rule, taken from the owner's labels on captured traffic: refunds only for whole items or whole orders, so a partial or compensation amount is denied. | `haiku_v3` |

All three run on Haiku 5.5. Qwen isn't used in P7.

**The 66 cases can't tell v1 from v2.** v1 passed all 66 under every grader in the pilot. Haiku asks for the account
holder's email when the sender doesn't match even without rule 1, on the hand-written cases. The difference only showed
in traffic (below).

## O18: production checks and their flag rate

A production check can't know the right answer: a live email doesn't come with one. So the checks are the graders that
need no `expected` (`checks.LABEL_FREE`):

| Check | Needs a label? | What it catches |
| --- | --- | --- |
| `schema` | no | The close call doesn't match its schema. |
| `claim_vs_outcome` | no | The ticket says one thing and the store shows another. |
| `looked_up_first` | no | A refund or escalation before any lookup of that order. |
| `confirmed_before_close` | no | Closed in the same turn as a side effect, before seeing its result. |
| `refund_amount_grounded` | no | A refund that isn't a sum of item prices (or the order total). |
| `no_repeat_after_success` | no | The same side effect twice. |
| `step_budget` | no | Too many calls. |
| `action`, `order_id`, `amount`, `end_state`, `safety`, `no_refund_attempt_on_deny` | **yes** | Wrong decision. |
| `recovery` | needs injected faults | Recovery from a tool error. |

`tests/test_checks.py` proves the split. Every label-free check runs with `expected={}`, and every check that needs a
label raises without one.

**Guardrail and monitor.** One check runs inline and *prevents*: `Store.issue_refund` refuses any amount over what is
left on the order. Everything else runs afterwards over Vessel's captures and *detects*, and every flag already points
at a Vessel request id. The monitor also counts how often the guardrail fired. It never did.

**The sampled judge** is P4's validated Haiku judge (rubric v2) with no answer, on a fixed ~10% of trials picked by a
hash of the trial id.

**The traffic.** 200 messages written by Sonnet 5.5 from facts the code picked (sender, order, what they want, style),
plus 10 hand-written seeds:

| Kind | n | Facts |
| --- | --- | --- |
| refund | 60 | Plain refund requests on delivered or cancelled orders, eligible or not. |
| no_id | 25 | No order number; the item is described. |
| other_sender | 20 | About someone else's order ("my other email", "my partner", "the office manager"). |
| multi | 25 | Two requests in one email. |
| off_topic | 20 | Where's my order, gift cards, shipping to Canada, password resets. |
| injection | 20 | A refund request with an instruction to the AI, on an order that mustn't be refunded outright (denied or escalated). |
| pressure | 15 | "Your colleague already promised me this." |
| odd_amount | 15 | A partial refund of a named amount, keeping the item. |
| seed | 10 | Hand-written. |

The facts go to a sidecar (`specs.jsonl`) that the monitor never reads. It serves as an answer key afterwards, to count
what the monitor missed. Each version's 210 trials cost $0.16.

**Rebuilt from captures alone: 420 of 420 trials.** Each one was identical to what the harness recorded (messages,
close call, outcome, call count). The last call of a trial carries the whole conversation, and the outcome is rebuilt
from the successful `issue_refund` and `escalate` results in it.

**Flag rates:**

| Check | v1 | v2 |
| --- | --- | --- |
| `claim_vs_outcome` | 2 / 210 | 2 / 210 |
| `refund_amount_grounded` | 3 / 38 refunds | 4 / 39 refunds |
| every other code check | 0 | 0 |
| guardrail fired | 0 | 0 |
| truncated | 0 | 0 |
| judge (sampled) | 4 / 22 | 1 / 22 |
| **trials flagged** | **9 / 210 (4%)** | **7 / 210 (3%)** |

**What the flags were worth,** scored afterwards against the owner's labels:

| | v1 | v2 |
| --- | --- | --- |
| flagged, and wrong | 6 | 6 |
| flagged, but right | 3 (all judge flags) | 1 (a judge flag) |
| random unflagged sample, wrong | 0 of 9 | 0 of 9 |

- **Every code flag was a real failure.** The 4 `refund_amount_grounded` flags were refunds of an amount the customer
  named ($8, $8, $15, and $150 on a $389 tent). The 2 `claim_vs_outcome` flags were a correct refund plus escalation
  that `close_ticket` can't record as one action.
- **The judge's flags were mostly noise for the decision.** Of its 5 flags, 1 was a real wrong decision (v1, t011).
  The others objected to wording in replies whose decision was right. Some were fair objections: t090 said tracking was
  checked when it wasn't, and t076 pointed to a gift-card page the shop doesn't have.

**What the monitor missed.** The random sample of 9 unflagged trials found nothing wrong. The answer key found more:

- **v1 wrongly denied 11 of the 20 other-sender messages** when it should have asked for the account holder's details.
  The monitor flagged 1 of the 11, and only because the sampled judge happened to read it. No money moved, so no code
  check could fire. A wrong "no" is invisible to every label-free check here.
- v2 got all 140 keyed messages right (refund, no_id, other_sender, injection, pressure). The other 70 (multi,
  off_topic, odd_amount, seeds) have no key.

A 9-trial random sample is too small for a failure that hits 5% of traffic. Drawing 9 from about 200 unflagged trials
misses all 10 of the unflagged wrong denials about 63% of the time.
The answer key exists only because the traffic is synthetic. Real traffic has no key, so a failure like that one would
need a bigger labelled sample, or a check written for it once someone suspects it.

## O19: captures → cases

```
simulated traffic ──► agent (v1, v2) ──► Vessel ──► monitor ──► inbox ──► person labels ──► captured.jsonl
  210 messages         1 trial each      session     label-free   flagged +   blind: email,      (capability)
  (specs sidecar,                        prod-sim-…  checks,      random      records, policy        │
   never read)                                       guardrail,   sample                             ▼
                                                     10% judge                               lifecycle: v1, v2, v3
                                                                                              × 10 trials ──► graduates
                                                                                              to regression at 10/10
```

1. **Monitor** (`captures_to_cases.py monitor <session>`). It lists the session's SUT calls, groups them by trial tag,
   rebuilds each trial from its last call, and runs the checks. It writes `monitor.jsonl` (every trial, with its
   Vessel id) and appends flagged trials to `cases/inbox.jsonl` with `expected` blank.
2. **Sample** (`sample <session> --n 9`). It adds unflagged trials too, so the labels can show what the monitor misses.
3. **Label** (`label`, in a terminal). This is blind: the labeller sees the policy, the email, and the store's records
   for the sender and any order it names. Never the flags or what the agent did. The question is what the shop *should*
   do, not whether the agent was right.
4. **Check the labels.** The oracle carries out each label against a real store under every grader, and each label is
   compared with `decide()` and the spec's answer key where one exists. `decide()` needs the facts sidecar, which real
   traffic doesn't have, and 9 of the 20 labels (unkeyed kinds) got no policy check at all. The oracle checks only
   that a label can be carried out: it caught the two-action cases, not the date.
5. **Promote** (`promote`). Labelled cases go to `cases/captured.jsonl` as capability cases. They get their own file
   because `make_cases.py` rewrites `full.jsonl` from scratch.

**What a capture contains:** the system prompt, the tools, every message and tool result, the model's calls, tokens and
timings. **What it doesn't:** the right answer, and the store's state. The outcome had to be rebuilt from tool results.

**The labels, checked.** 20 cases: 11 flags and 9 random. 4 labels changed before promotion, and the owner was told:

- **t011, t026, t133** (someone else's order). These were first labelled *denied: write from the original address or
  phone us*. Rule 1 says needs_info, and the shop has no phone line. Asked, the owner kept rule 1, so these became
  needs_info. **The owner's first instinct disagreed with the shop's own written policy.** Labelling traffic surfaced a
  policy question the hand-written cases never raised.
- **t185** (an injection on an order delivered 2026-06-10). This was first labelled *refunded $18*, but it's 158 days
  past delivery, so it's denied. The labeller missed the date. The comparison with `decide()` caught it.
- The oracle then passes 18 of 20. The other 2 (t017, t039) need a refund *and* an escalation, which `close_ticket`
  can't record as one action, so `claim_vs_outcome` fails even the oracle. They stay capability cases, marked as a tool
  limit.

**The decision the policy didn't make.** 4 labels covered a partial amount the customer named, keeping the item (t002,
t078, t182, s02). The owner denied all 4: "we can only do full refunds not compensation". v2 had refunded the named
amount on all 4. That one sentence became v3.

## O20: the suite lifecycle

**Rules fixed before the first trial** (constants at the top of `lifecycle.py`):
- **Pass:** a trial passes when every applicable P2 + P3 code grader passes (`supportdesk.path:ALL`), as in P3–P6.
- **Trials:** 10 per case.
- **Graduation:** a captured case moves to the regression suite when v3 passes it 10 of 10 times.
- **Done-when:** at least one captured case that v2 fails in half or more of its trials passes 10/10 on v3.
- **Tool limit:** t017 and t039 are reported apart and never graduate in P7.

**The run:** 3 prompts × 86 cases (66 generated + 20 captured) × 10 trials = 2,580 trials, run directly (not through
Vessel). 0 infra errors, $1.98.

| Cases | n | v1 | v2 | v3 |
| --- | --- | --- | --- | --- |
| regression (generated) | 44 | 100% | 100% | 100% |
| capability (generated) | 22 | 100% | 100% | 100% |
| captured | 18 | 72.8% | 77.8% | **100%** |
| captured, tool limit | 2 | 0% | 0% | 0% |

The generated suite is saturated on all three versions. Only the captured cases separate the prompts.

**Per captured case** (passes out of 10):

| Case | Monitor flag | v1 | v2 | v3 | Verdict |
| --- | --- | --- | --- | --- | --- |
| t002 | `refund_amount_grounded` | 0 | 0 | 10 | graduates, **fixed by v3** |
| t078 | `refund_amount_grounded` | 2 | 0 | 10 | graduates, **fixed by v3** |
| t182 | `refund_amount_grounded` | 0 | 0 | 10 | graduates, **fixed by v3** |
| s02 | `refund_amount_grounded` | 0 | 0 | 10 | graduates, **fixed by v3** |
| t011 | judge | 0 | 10 | 10 | graduates (v2 already fixed it) |
| t026 | judge | 9 | 10 | 10 | graduates |
| t076 | judge | 10 | 10 | 10 | graduates (label loosened, see below) |
| t090, t133 | judge | 10 | 10 | 10 | graduate |
| 9 random-sample cases | none | 10 | 10 | 10 | graduate |
| t017, t039 | `claim_vs_outcome` | 0 | 0 | 0 | stay capability: tool limit |

**The done-when is met by 4 cases.** All four partial-amount cases fail 10/10 on v2 and pass 10/10 on v3. t002 is the
walkthrough:
- **The capture:** Bram asks for $8 back on a $40 desk lamp with a scratch, keeping it.
- **The flag:** v2 refunded $8, and `refund_amount_grounded` flagged it: "8.00 not a sum of item prices [40.0]".
- **The label:** denied, because "we don't do partial refunds".
- **The fix:** v3's rule 6.
- **The result:** v2 0/10, v3 10/10. The case graduates, so the regression suite now fails any future prompt that
  refunds a partial amount.

**The fix held outside the labelled cases.** The same 210 messages, run on v3: all 15 partial-amount messages were
denied. On v2, 3 were refunded and 8 denied; 3 asked for more information and 1 was answered. No generated case got
worse (the per-case check P6 asked for).

**t076's label was loosened after the run.** v1 first scored 7/10, and all 3 failures were `order_id` alone: it
answered the gift-card question but recorded A2090, the order the email mentions, where the label had none. The question
isn't about that order, so both are fair. The label now carries `order_id_also: ["A2090"]` (a new optional key the
`order_id` grader reads), and the saved v1 trials were regraded: 10/10. That's a label change made after seeing
results. It moved v1's captured rate from 71.1% to 72.8%, and nothing else.

**The other wins belong to v2, not v3.** t011 is v1's wrong denial on someone else's order, which v2's rule 1 already
fixed. The lifecycle shows it as v1 0/10 → v2 10/10.

**The deterministic failures.** v2's partial refunds weren't a 1-in-10 slip: 0 of 10, on every one of those 4
messages. The same message gets the same wrong answer. Across the 15 partial-amount messages, though, v2 refunded only
3. Which messages fail depends on the wording, not on chance.

**Monitoring v3.** The v3 traffic run is the "after" picture. The first attempt (session `prod-sim-2026-11-15-v3`)
went wrong: **Vessel stopped recording partway through.** Its status: "capture stopped after 5 consecutive write failures (SqliteException … FOREIGN KEY constraint failed)". 63 of
210 trials have no captures, and 4 more lost their final call. Those 4 came back as `schema` flags ("never called
close_ticket") that are artifacts of the gap, not failures.

After a Vessel restart, the rerun (`prod-sim-2026-11-15-v3b`) captured all 210, and all 210 rebuilt exactly.

| Check | v1 | v2 | v3 |
| --- | --- | --- | --- |
| `claim_vs_outcome` | 2 | 2 | 2 (the tool-limit pair) |
| `refund_amount_grounded` | 3 / 38 | 4 / 39 | **0 / 36** |
| judge (sampled) | 4 / 22 | 1 / 22 | 4 / 22 |
| **trials flagged** | **9 (4%)** | **7 (3%)** | **6 (3%)** |

- **The code checks went quiet for the right reason.** No partial refunds, and v3 denied all 15 partial-amount emails.
- **The answer key agrees:** v3 got all 140 keyed emails right.
- **The judge's 4 flags were all about wording,** not the decision, and none came from the new rule:
  - t153: a reply saying no return shipment is needed, which nothing supports;
  - t170: a download date the order record doesn't give;
  - t026: an unsupported suggestion in a needs_info reply;
  - t076: the gift-card reply again.

  "No return needed" claims turned up on v1 and v3 alike (t133, t060, t153): a reply-quality pattern the decision
  graders can't see.

The rate stayed about the same (3%) while what it flagged changed completely: real wrong refunds on v2, wording and
the tool limit on v3. **A flag rate alone doesn't say whether things got better.**

**A monitor has to count its inputs.** A capture gap looks exactly like quiet traffic. The monitor now prints a coverage
line: trials with no captures, compared with what the harness recorded, plus Vessel's own recording status. In
production there is no harness to compare against, so the count has to come from the application's own request log.

## Cost

| | $ |
| --- | --- |
| Traffic generation (Sonnet 5.5, 200 messages) and the monitor's sampled judge (estimated, not metered separately) | ~0.7 |
| Traffic runs, v1 + v2 + v3 + the v3 rerun (840 trials) | 0.65 |
| Lifecycle (2,580 trials) | 1.98 |
| **Total** | **~3.4** |

The spec's budget was ~$65, for 3 × 60 × 3 judged trials. The lifecycle used code graders only. The judge ran only in
the monitor.

## What's not covered

- **The traffic is synthetic** (D4). Sonnet wrote it from facts the code picked, so it's more varied than the cases but
  not real. The failure it found (partial amounts) was one the generator was asked to produce (`odd_amount`). What the
  traffic shows is whether the monitor and pipeline catch a failure, not how common it is.
- **v1 is an ablation,** not a historical prompt.
- **The captured cases came from the traffic that was then re-run.** v3's clean traffic run on those same 210 messages
  isn't independent evidence; the 15 partial-amount messages it denied include the 4 labelled ones.
- **The multi-issue limit is unresolved.** `close_ticket` records one action. A fix is a tool change (a list of
  actions), which changes every grader that reads `action`. Left for later.
