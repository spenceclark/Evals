"""Monitor + captures -> cases (P7). Works from Vessel's captures alone, the way a production monitor reads logs.

    uv run python scripts/captures_to_cases.py monitor prod-sim-... [--dry]   # flag rates; flagged -> cases/inbox.jsonl
    uv run python scripts/captures_to_cases.py sample prod-sim-... [--n 9]   # plus unflagged trials, to measure misses
    uv run python scripts/captures_to_cases.py label                   # you fill in `expected` (own terminal)
    uv run python scripts/captures_to_cases.py status
    uv run python scripts/captures_to_cases.py promote                 # labelled inbox cases -> cases/captured.jsonl

`monitor`:
1. Lists the session's SUT calls (tag `sut`) and groups them by trial tag.
2. Rebuilds each trial from its LAST call: the request holds the whole conversation, the response the closing call
   (the round-trip check from P1). The outcome isn't captured, so it's rebuilt from the side effects' results.
3. Runs the label-free checks (supportdesk.checks.LABEL_FREE), counts guardrail refusals, and asks P4's validated judge
   (Haiku 5.5, rubric v2, no answer) about a ~10% sample.
4. Writes runs/p7/<session>/monitor.jsonl (every trial, with its Vessel request id) and appends flagged trials to
   cases/inbox.jsonl with `expected` blank. A capture never contains the right answer; someone has to supply it.

If the run's results.jsonl is there, every rebuilt trial is also compared with what the harness recorded.
"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from evalkit.core import Call, Case, Trial, grade
from evalkit.judge import Judge, JudgeModel
from supportdesk.checks import LABEL_FREE, guardrail_fired, outcome_from
from supportdesk.display import order_line, wrap
from supportdesk.judges import V2, rubric, view
from supportdesk.roundtrip import comparable, parse_anthropic, parse_openai
from supportdesk.shop import DATA, SYSTEM_PROMPT, V2_PROMPT, V3_PROMPT

type JSON = dict[str, Any]

ROOT = Path(__file__).resolve().parents[1]
VESSEL = os.environ.get("VESSEL_URL", "http://127.0.0.1:4550").rstrip("/")
# Captured cases get their own file: make_cases.py rewrites full.jsonl from scratch.
INBOX, CAPTURED = ROOT / "cases" / "inbox.jsonl", ROOT / "cases" / "captured.jsonl"
JUDGE_RATE = 0.10
JUDGE = JudgeModel("claude-haiku-5-5")


def get(path: str) -> Any:
    with urllib.request.urlopen(VESSEL + path) as r:
        return json.load(r)


def read(path: Path) -> list[JSON]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(path: Path, rows: list[JSON]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def sut_calls(session: str) -> dict[str, list[JSON]]:
    """Every SUT call in the session, grouped by trial tag, oldest first. Pages through the listing."""
    sid = next(s["id"] for s in get("/vessel/api/sessions") if s["name"] == session)
    rows: list[JSON] = []
    before: Any = None
    while True:
        q: JSON = {"session": sid, "tag": "sut", "limit": 200} | ({"before": before} if before else {})
        page = get(f"/vessel/api/requests?{urllib.parse.urlencode(q)}")
        rows += page["rows"]
        before = page.get("nextBefore")
        if not before or not page["rows"]:
            break
    by_trial: dict[str, list[JSON]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        tag = next(t for t in r["tags"] if t.startswith("trial-"))
        by_trial.setdefault(tag, []).append(r)
    return by_trial


def rebuild(tag: str, calls: list[JSON]) -> tuple[Case, Trial, int]:
    """One trial from its captures alone. Returns the case (no expected answer), the trial, and the Vessel id of the
    call it was rebuilt from."""
    last = get(f"/vessel/api/requests/{calls[-1]['id']}")
    req, resp = json.loads(last["requestBody"]["text"]), json.loads(last["responseBody"]["text"])
    messages = (parse_anthropic if last["format"] == "anthropic-messages" else parse_openai)(req, resp)
    final: list[JSON] = messages[-1].get("tool_calls", []) if messages and messages[-1]["role"] == "assistant" else []
    closes: list[JSON] = [c["args"] for c in final if c["name"] == "close_ticket"]
    output: JSON = closes[0] if closes else {}
    case_id, _, rep = tag.removeprefix("trial-").rpartition("-")
    status = "truncated" if last["truncated"] or last.get("stopReason") in ("max_tokens", "length") else "ok"
    used = [Call(c["model"], c["tokensIn"] or 0, c["tokensOut"] or 0, c.get("stopReason") or "", c["durationMs"])
            for c in calls]  # fmt: skip
    trial = Trial(case_id, int(rep), calls[-1]["model"], messages, output, {}, used, status, "")
    trial.outcome = outcome_from(trial)
    return Case(case_id, messages[0]["text"], expected={}, source=f"capture:{last['id']}"), trial, last["id"]


def judged(trial_id: str) -> bool:
    """A fixed ~10% sample, the same on every re-run."""
    return int(hashlib.sha256(trial_id.encode()).hexdigest(), 16) % 100 < JUDGE_RATE * 100


async def monitor(session: str, dry: bool = False) -> None:
    run_dir = ROOT / "runs" / "p7" / session
    recorded = {r["case_id"]: r["trial"] for r in read(run_dir / "results.jsonl")}
    # The judge grades against the policy the business wants at the time. v1 and v2 traffic ran before the owner's
    # labels added the whole-items rule, so they are judged on v2's text; v3 traffic on v3's.
    task_name = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))["task"]
    policy = V3_PROMPT if task_name.endswith(":v3") else V2_PROMPT  # the v1/v2 traffic was judged against v2
    judge = Judge(ROOT / "runs" / "p7" / "judge-cache.jsonl", AsyncAnthropic())  # direct: not part of the session
    rows: list[JSON] = []
    exact = 0
    for tag, calls in sorted(sut_calls(session).items()):
        case, trial, vid = rebuild(tag, calls)
        if (rec := recorded.get(case.id)) is not None:
            same = (comparable(rec["messages"]) == trial.messages and rec["output"] == trial.output
                    and rec["outcome"] == trial.outcome and len(rec["calls"]) == len(trial.calls))  # fmt: skip
            exact += same
            if not same:
                print(f"  {case.id}: rebuilt trial differs from the recorded one")
        scores = grade(case, trial, LABEL_FREE) if trial.status == "ok" else []
        flags = {s.grader: s.reason for s in scores if not s.passed}
        if trial.status != "ok":
            flags["truncated"] = "the model ran out of output tokens; the customer got no reply"
        if n := guardrail_fired(trial):
            flags["guardrail"] = f"the inline guardrail refused {n} refund attempt(s)"
        verdict: JSON | None = None
        if judged(case.id) and trial.output.get("reply"):
            item = {"messages": trial.messages, "input": case.input, "outcome": trial.outcome, "output": trial.output}
            g = await rubric(judge, JUDGE, V2, view(item, policy=policy), answer=False)
            verdict = {"passed": g.passed, "failed": [k for k, a in g.asked.items() if a.verdict and
                       not a.verdict["passed"]], "critiques": {k: a.verdict["critique"] for k, a in g.asked.items()
                       if a.verdict}}  # fmt: skip
            if g.passed is False:
                flags["judge"] = "; ".join(f"{k}: {verdict['critiques'][k]}" for k in verdict["failed"])
        rows.append({"case_id": case.id, "vessel_id": vid, "calls": len(calls), "status": trial.status,
                     "action": trial.output.get("action"), "outcome": trial.outcome, "flags": flags,
                     "checked": [s.grader for s in scores], "judge": verdict, "input": case.input})  # fmt: skip
    write(run_dir / "monitor.jsonl", rows)

    n = len(rows)
    print(f"{session}: {n} trials rebuilt from {sum(r['calls'] for r in rows)} captured calls")
    if recorded:
        print(f"  rebuilt == recorded by the harness: {exact}/{len(recorded)}")
        # A monitor has to count its inputs: a capture gap looks exactly like quiet, healthy traffic.
        if missing := sorted(set(recorded) - {r["case_id"] for r in rows}):
            print(f"  COVERAGE: {len(missing)} of {len(recorded)} trials have no captures at all ({missing[0]}..)")
    if not (cap := get("/vessel/api/status")["capture"])["recording"]:
        print(f"  COVERAGE: Vessel is not recording: {cap.get('stoppedReason')}")
    applicable = Counter(g for r in rows for g in r["checked"])
    fired = Counter(f for r in rows for f in r["flags"])
    print(f"  {'check':<26} {'flagged':>8} {'of':>5}")
    for g in [*(x.__name__ for x in LABEL_FREE), "guardrail", "truncated"]:
        print(f"  {g:<26} {fired[g]:>8} {applicable[g] or n:>5}")
    nj = sum(1 for r in rows if r["judge"])
    print(f"  {'judge (sampled)':<26} {fired['judge']:>8} {nj:>5}")
    flagged = [r for r in rows if r["flags"]]
    print(f"  flagged trials: {len(flagged)}/{n} ({100 * len(flagged) / max(n, 1):.0f}%)")

    if dry:
        return
    inbox = read(INBOX)
    have = {r["id"] for r in inbox}
    new: list[JSON] = [{"id": f"cap-{r['case_id']}", "input": r["input"], "expected": {}, "suite": "capability",
            "tags": sorted(r["flags"]), "source": f"capture:{r['vessel_id']}"}
           for r in flagged if f"cap-{r['case_id']}" not in have]  # fmt: skip
    write(INBOX, inbox + new)
    print(f"  inbox: {len(new)} new, {len(inbox) + len(new)} in total -> {INBOX.relative_to(ROOT)}")


ACTIONS = {"r": "refunded", "d": "denied", "e": "escalated", "a": "answered", "n": "needs_info"}


def facts(text: str) -> list[str]:
    """What a labeller needs: the sender's orders, plus any order the email names."""
    sender = text.split("\n", 1)[0].removeprefix("From:").strip().lower()
    named = {o["id"] for o in DATA["orders"] if o["id"] in text}
    cust = next((c for c in DATA["customers"] if c["email"].lower() == sender), None)
    mine = {o["id"] for o in DATA["orders"] if cust and o["customer_id"] == cust["id"]}
    lines = [f"SENDER {sender}: {'customer ' + cust['id'] + ' (' + cust['name'] + ')' if cust else 'not a customer'}"]
    for o in DATA["orders"]:
        if o["id"] in named | mine:
            tag = ("named, " if o["id"] in named else "") + ("sender's" if o["id"] in mine else "NOT the sender's")
            lines.append(f"    [{tag}] {order_line(o)}")
    return lines


def extra_effects(text: str) -> tuple[list[JSON], list[str]]:
    """'refund A2001 12.00; escalate A2212' -> ([{order_id, amount}], [order ids])."""
    refunds: list[JSON] = []
    escalations: list[str] = []
    for part in filter(None, (p.strip() for p in text.split(";"))):
        verb, oid, *rest = part.split()
        if verb.lower().startswith("r"):
            refunds.append({"order_id": oid.upper(), "amount": float(rest[0].lstrip("$"))})
        else:
            escalations.append(oid.upper())
    return refunds, escalations


def label() -> None:
    """Blind to the monitor: the labeller sees the email and the shop's records, not the flags or what the agent did."""
    rows = read(INBOX)
    todo = [r for r in rows if not r["expected"]]
    policy = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Refund policy") : SYSTEM_PROMPT.index("close_ticket actions")].strip()
    print(f"{len(todo)} inbox cases need an expected answer. Today is {DATA['today']}. Answers save as you go.\n\n"
          f"{policy}\n\nFor each email: the right action, then the order and amount it applies to. Where the policy\n"
          "doesn't say, decide what the shop should do, and write why in the note.\n")  # fmt: skip
    for r in todo:
        print("=" * 110)
        print(f"{r['id']}  ({r['source']})")
        print(f"EMAIL\n{wrap(r['input'])}")
        print("\n".join(facts(r["input"])))
        while (a := input("action? [r]efund [d]eny [e]scalate [a]nswer [n]eeds_info, [s]kip, [q]uit > ").strip()
               .lower()) not in [*ACTIONS, "s", "q"]:  # fmt: skip
            pass
        if a == "q":
            break
        if a == "s":
            continue
        act = ACTIONS[a]
        oid = input("order id it applies to (Enter = none) > ").strip().upper() or None
        amount = float(input("amount to refund > ").strip().lstrip("$")) if act == "refunded" else None
        more = input("any other refund or escalation? e.g. 'escalate A2212' or 'refund A2001 12' (Enter = none) > ")
        refunds, escalations = extra_effects(more)
        note = input("note: why (optional) > ").strip()
        r["expected"] = {"action": act, "order_id": oid, "amount": amount,
                         "refunds": ([{"order_id": oid, "amount": amount}] if act == "refunded" else []) + refunds,
                         "escalations": ([oid] if act == "escalated" else []) + escalations}  # fmt: skip
        if note:
            r["expected"]["note"] = note
        write(INBOX, rows)
    status()


def sample(session: str, n: int, seed: int) -> None:
    """A random sample of trials the monitor did NOT flag, so the labels can show what it misses."""
    rows = read(ROOT / "runs" / "p7" / session / "monitor.jsonl")
    inbox = read(INBOX)
    have = {r["id"] for r in inbox}
    pool = sorted((r for r in rows if not r["flags"] and f"cap-{r['case_id']}" not in have), key=lambda r: r["case_id"])
    picked = random.Random(seed).sample(pool, n)
    new: list[JSON] = [{"id": f"cap-{r['case_id']}", "input": r["input"], "expected": {}, "suite": "capability",
                        "tags": ["random-sample"], "source": f"capture:{r['vessel_id']}"} for r in picked]  # fmt: skip
    write(INBOX, inbox + new)
    print(f"added {len(new)} unflagged trials from {session}: {[r['id'] for r in new]}")


def status() -> None:
    rows = read(INBOX)
    done = [r for r in rows if r["expected"]]
    print(f"inbox: {len(rows)} cases, {len(done)} labelled: {Counter(r['expected']['action'] for r in done)}")


def promote() -> None:
    """Labelled inbox cases join the suite as capability cases. They graduate to regression only once the fixed
    prompt passes them reliably (scripts/lifecycle.py)."""
    rows, captured = read(INBOX), read(CAPTURED)
    have = {c["id"] for c in captured}
    moving = [r for r in rows if r["expected"] and r["id"] not in have]
    write(CAPTURED, captured + moving)
    write(INBOX, [r for r in rows if not r["expected"]])
    print(f"promoted {len(moving)} labelled cases to {CAPTURED.relative_to(ROOT)} (suite: capability)")


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("monitor")
    m.add_argument("session")
    m.add_argument("--dry", action="store_true", help="don't write to the inbox (pilots)")
    s = sub.add_parser("sample")
    s.add_argument("session")
    s.add_argument("--n", type=int, default=9)
    s.add_argument("--seed", type=int, default=20261115)
    for name in ("label", "status", "promote"):
        sub.add_parser(name)
    a = p.parse_args()
    match a.cmd:
        case "monitor":
            asyncio.run(monitor(a.session, a.dry))
        case "sample":
            sample(a.session, a.n, a.seed)
        case "label":
            label()
        case "status":
            status()
        case _:
            promote()


if __name__ == "__main__":
    main()
