"""Capture round-trip check: rebuild a trial's transcript from Vessel's capture of its last LLM call, compare.

    uv run python -m supportdesk.roundtrip runs/<run_id> [case_id [rep]]

The last call's request holds the conversation so far; its response holds the closing tool call.
Needs a run made with VESSEL_URL set (the Vessel session name is the run id).
"""

import io
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

from supportdesk.task import trial_tag

type JSON = dict[str, Any]

VESSEL = os.environ.get("VESSEL_URL", "http://127.0.0.1:4550").rstrip("/")


def _get(path: str) -> Any:
    with urllib.request.urlopen(VESSEL + path) as r:
        return json.load(r)


def _tool_msg(id_: str, names: dict[str, str], content: str, error: bool | None) -> JSON:
    result = json.loads(content)
    # OpenAI chat has no is_error flag; the tool layer marks errors in the content instead.
    return {
        "role": "tool",
        "id": id_,
        "name": names[id_],
        "result": result,
        "error": error if error is not None else isinstance(result, dict) and "error" in result,
    }


def parse_anthropic(req: JSON, resp: JSON) -> list[JSON]:
    out: list[JSON] = []
    names: dict[str, str] = {}
    for m in [*req["messages"], {"role": "assistant", "content": resp["content"]}]:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "text": content})
            continue
        blocks = cast(list[JSON], content)
        if m["role"] == "assistant":
            calls = [{"id": b["id"], "name": b["name"], "args": b["input"]} for b in blocks if b["type"] == "tool_use"]
            names |= {c["id"]: c["name"] for c in calls}
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            out.append({"role": "assistant", "text": text, "tool_calls": calls})
        else:
            for b in blocks:
                if b["type"] == "tool_result":
                    out.append(_tool_msg(b["tool_use_id"], names, b["content"], b.get("is_error", False)))
                elif b["type"] == "text":
                    out.append({"role": "user", "text": b["text"]})
    return out


def parse_openai(req: JSON, resp: JSON) -> list[JSON]:
    out: list[JSON] = []
    names: dict[str, str] = {}
    messages: list[JSON] = [*req["messages"], resp["choices"][0]["message"]]
    for m in messages:
        tool_calls: list[JSON] = m.get("tool_calls") or []
        match m["role"]:
            case "user":
                out.append({"role": "user", "text": m["content"]})
            case "assistant":
                calls = [
                    {"id": c["id"], "name": c["function"]["name"], "args": json.loads(c["function"]["arguments"])}
                    for c in tool_calls
                ]
                names |= {c["id"]: c["name"] for c in calls}
                out.append({"role": "assistant", "text": m.get("content") or "", "tool_calls": calls})
            case "tool":
                out.append(_tool_msg(m["tool_call_id"], names, m["content"], None))
            case _:  # system
                pass
    return out


def comparable(messages: list[JSON]) -> list[JSON]:
    return [{k: v for k, v in m.items() if k != "ms"} for m in messages]


def check(run_dir: Path, case_id: str | None, rep: int = 0) -> bool:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    row = next(r for r in rows if case_id in (None, r["case_id"]) and r["rep"] == rep)
    trial = row["trial"]

    session = next(s for s in _get("/vessel/api/sessions") if s["name"] == meta["run_id"])
    tag = trial_tag(row["case_id"], row["rep"])
    q = urllib.parse.urlencode({"session": session["id"], "tag": tag, "limit": 50})
    hits = _get(f"/vessel/api/requests?{q}")["rows"]  # newest first
    last = _get(f"/vessel/api/requests/{hits[0]['id']}")
    print(
        f"{row['case_id']}: {len(hits)} captured calls, last = Vessel #{last['id']} ({last['format']}, "
        f"truncated={last['truncated']}, warnings={last['warnings']})"
    )

    req, resp = json.loads(last["requestBody"]["text"]), json.loads(last["responseBody"]["text"])
    parse = parse_anthropic if last["format"] == "anthropic-messages" else parse_openai
    rebuilt, recorded = parse(req, resp), comparable(trial["messages"])
    # Tools run in the same turn as close_ticket (e.g. issue_refund + close_ticket together) were executed, but their
    # results were never sent to the model, so no capture can contain them.
    last = max(i for i, m in enumerate(recorded) if m["role"] == "assistant")
    if unsent := recorded[last + 1 :]:
        print(f"  note: {len(unsent)} tool result(s) after the final turn were never sent to the model: "
              f"{[m['name'] for m in unsent]}")  # fmt: skip
        recorded = recorded[: last + 1]

    ok = len(hits) == len(trial["calls"])
    print(f"  call count: captured {len(hits)}, recorded {len(trial['calls'])} -> {'OK' if ok else 'MISMATCH'}")
    for i in range(max(len(rebuilt), len(recorded))):
        a = recorded[i] if i < len(recorded) else None
        b = rebuilt[i] if i < len(rebuilt) else None
        same = a == b
        ok &= same
        print(f"  [{i}] {(a or b or {}).get('role')}: {'OK' if same else 'MISMATCH'}")
        if not same:
            print(f"      recorded: {json.dumps(a)[:300]}\n      captured: {json.dumps(b)[:300]}")
    closing = rebuilt[-1]["tool_calls"][-1]["args"] if rebuilt and rebuilt[-1].get("tool_calls") else None
    same = (closing or {}) == trial["output"]  # no close_ticket on either side also matches
    ok &= same
    print(f"  close_ticket args == trial.output -> {'OK' if same else 'MISMATCH'}")
    print("ROUND-TRIP OK" if ok else "ROUND-TRIP FAILED")
    return ok


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportUnknownMemberType]
    args = sys.argv[1:]
    sys.exit(0 if check(Path(args[0]), args[1] if len(args) > 1 else None, int(args[2]) if len(args) > 2 else 0) else 1)
