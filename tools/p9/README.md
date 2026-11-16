# P9: the same slice in each framework

Each port runs the same 10 cases, 3 trials each, through the same agent, and grades them with the same three graders,
written the way that framework wants them written. The point is to compare the frameworks (spec §6), so the record of
how each port went matters as much as its numbers.

## What's shared (don't change it)

- **The slice:** [slice.jsonl](slice.jsonl), 10 cases (one per case group, plus captured case `cap-t002`). Same schema as
  `cases/full.jsonl`: `id`, `input` (the email), `expected` (`action`, `order_id`, `amount`, `refunds`, `escalations`,
  sometimes `also_ok`), `suite`, `tags`, `faults`.
- **The system under test:** `supportdesk.run(message, faults, *, case_id, rep) -> Trial`, or `await
  supportdesk.arun(...)` in async frameworks. It runs the shipped prompt on Haiku 5.5. Pass the case's `faults` (only
  `fault-01` has any). Thread-safe; `arun` works from any event loop.
  A `Trial` has `output` (the close_ticket call: `action`, `order_id`, `amount`, `reply`), `outcome` (`refunds`,
  `escalations` the store recorded), `messages` (the transcript) and `calls` (tokens, for cost).
- **The three graders**, each in the framework's own idiom, as natively as it allows:
  1. **action (code):** `output["action"]` equals `expected["action"]`, or the action of an `expected["also_ok"]` entry.
     Reference: `supportdesk.graders.action`.
  2. **rubric (LLM judge):** rubric v2's three criteria (`supportdesk.judges.V2.criteria`: `accurate`,
     `no_outside_promises`, `tone`), judge `claude-haiku-5-5`, one pass/fail per criterion, the reply passes only if all
     three pass. The judge must see what P4's judge saw: the policy (`supportdesk.shop.SYSTEM_PROMPT`), the email, the
     agent's tool calls and results, what changed in the store, and the reply. `supportdesk.judges.view(item,
     policy=SYSTEM_PROMPT)` builds exactly those fields; no answer key. Use the framework's own LLM-judge feature if it has
     one, and record what it actually sends and how it parses the verdict. If it can't express this, say so and use the
     closest it can.
  3. **path (trajectory):** no `issue_refund` or `escalate` before a successful `get_order` of that order in an earlier
     turn. Reference: `supportdesk.path.looked_up_first`. Record how the framework gets at the tool calls.
- **The reference:** [reference.jsonl](reference.jsonl), our own harness on this slice (`scripts/p9_reference.py`):
  action 30/30, path 30/30, rubric 28/30 (`dig-01#2` and `win-01#2` fail). Different trials, so a port's numbers won't
  match trial for trial; they should be close, and a big gap needs explaining.

## What each port delivers, in `tools/<tool>/`

- **The port:** code and config to run the slice with k=3 and the three graders, from one command written at the top of
  `P9.md`. Use the framework's own notion of repeats (epochs, repetitions…) if it has one.
- **`p9_results.jsonl`:** one line per trial, the same shape as `reference.jsonl`:
  `{"tool", "case_id", "rep", "action": bool|null, "path": bool|null, "rubric": bool|null, "reply": str}`.
  Export it from what the framework recorded, not from a side channel, if the framework allows.
- **`P9.md`**, the record, with these headings:
  - **Run:** the command, the framework version, cost, wall time.
  - **Case is…, Grader is…, Run is…:** how this framework represents each, with the snippet from the port.
  - **Native vs glue:** what the framework gave for free and what had to be written. Port LOC (non-blank,
    non-comment lines of the port's own code and config), split into "framework" and "glue" where possible.
  - **Trajectory access:** how the path grader got at the tool calls.
  - **The judge:** the prompt the framework actually sends, how it parses the verdict, structured output or not, and
    anything it drops or adds. Quote the captured prompt.
  - **Repeats / pass@k:** native or not.
  - **Results vs reference:** the three pass counts beside the reference's, and any disagreement explained.
  - **Human review, CI story, hosting:** what it offers, from using it, not from its docs. Say which is which.
  - **Friction:** what went wrong, in order, with how long it cost.
  - **What leaks:** anything sent anywhere other than the Anthropic API (telemetry, uploads, downloads).

`tools/<tool>/NOTES.md` is the P0 spike record; read it first, and leave it as it is.

## Rules

- Touch only `tools/<tool>/`. If `supportdesk` or `evalkit` needs a change, stop and report it instead.
- Depend on this repo from the tool's own uv project: `uv add --editable ../..` (the root package is `evals`).
- Root checks must stay clean: `uv run ruff check .` and `uv run ruff format --check .` from the repo root (they cover
  `tools/`), and the tool's own pyright strict config where P0 set one up.
- The API key: `export ANTHROPIC_API_KEY="$(powershell.exe -NoProfile -Command
  "[Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User')" | tr -d '\r\n')"` per command. Never print it,
  write it to a file, or put it in a config. Keep the framework's own uploads, telemetry and accounts off.
- Budget: $2 of API calls per port. The slice is about $0.02 of agent calls per full run plus the judge.
- No local models (no Ollama, no GPU).
- Vessel (`VESSEL_URL=http://127.0.0.1:4550`) is optional: it captures what the framework sends, which is the best
  evidence for "the judge" and "what leaks". Check `curl -s http://127.0.0.1:4550/vessel/api/status` first; use it only
  if it answers with `"recording": true`. Tag the framework's own calls `p9-<tool>` and use session `p9-<tool>`.
- Commit to your own branch. Don't push, and don't merge.
