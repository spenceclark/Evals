"""Save what DeepEval's judge actually sent and got back, from Vessel's capture (request/response bodies only, never
headers): one judge call per rubric criterion for a trial.

    uv run python capture.py TRIAL_TAG > judge_prompt_p9.txt    # e.g. trial-win-01-2

Finds the trial's agent calls by tag in Vessel, then the three p9-deepeval judge calls made right after its last one.
"""

import json
import sys
import urllib.request
from typing import Any

API = "http://127.0.0.1:4550/vessel/api"


def get(path: str) -> Any:
    with urllib.request.urlopen(f"{API}/{path}") as r:  # local Vessel only
        return json.load(r)


def search(tag: str) -> list[dict[str, Any]]:
    return get(f"requests?tag={tag}&limit=200")["rows"]


def main() -> None:
    trial = search(sys.argv[1])
    judges = sorted(search("p9-deepeval"), key=lambda r: r["id"])
    last = max(r["id"] for r in trial)
    calls = [r for r in judges if r["id"] > last][:3]
    for r in calls:
        d = get(f"requests/{r['id']}")
        req = json.loads(d["requestBody"]["text"])
        resp = json.loads(d["responseBody"]["text"])
        params = {k: v for k, v in req.items() if k != "messages"}
        tokens = f"{d['tokensIn']} in / {d['tokensOut']} out"
        print(f"===== Vessel request {r['id']} ({sys.argv[1]}): {d['statusCode']}, {tokens}")
        print(f"request params other than messages: {json.dumps(params)}")
        print(f"messages: {len(req['messages'])} ({', '.join(m['role'] for m in req['messages'])}); system: "
              f"{'system' in req}\n")  # fmt: skip
        for m in req["messages"]:
            for block in m["content"]:
                print(block.get("text", f"[{block['type']} block]"))
        print("\n----- response content blocks:")
        for block in resp["content"]:
            text = (
                block.get("text")
                if block["type"] == "text"
                else f"[{block['type']}: {len(block.get('thinking', ''))} chars]"
            )
            print(f"{block['type']}: {text}")
        print(f"usage: {json.dumps(resp['usage'].get('output_tokens_details'))}\n")


if __name__ == "__main__":
    main()
