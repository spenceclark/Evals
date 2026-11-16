"""What Langfuse stored for a run, read back (analysis, not part of the port).

    uv run python audit.py p9-run-1

1. Judge parity: each stored judge generation's input against the (system, prompt) P4's judge asked, from the
   run's judge cache (logs/judge-<run>.jsonl), and its output against the cached verdict text.
2. Sizes: bytes of everything fetched back (observations by type and name, scores, dataset), as JSON.
3. Leaks: counts of strings that would identify the machine or a secret in everything fetched back.
"""

import json
import os
import sys
from collections import Counter
from typing import cast

from p9 import DATASET, HERE, experiment, experiment_items, lf, loads, observations

from evalkit.core import JSON


def main(name: str) -> None:
    exp = experiment(name)
    since = exp.start_time.replace(microsecond=0)
    items = experiment_items(exp)
    cache = [json.loads(line) for line in (HERE / "logs" / f"judge-{name}.jsonl").read_text("utf-8").splitlines()]
    asked = {(a["system"], a["prompt"]): a for a in cache}
    sizes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    blobs: list[str] = []
    parity: Counter[str] = Counter()
    for it in items:
        obs = observations(it.trace_id, since)
        names = {o.id: o.name for o in obs}
        for o in obs:
            blob = json.dumps(o.dict(), default=str, ensure_ascii=False)
            kind = (
                f"{o.type} {o.name}"
                if o.type != "GENERATION"
                else f"GENERATION under {names.get(o.parent_observation_id)}"
            )
            sizes[kind] += len(blob.encode())
            counts[kind] += 1
            blobs.append(blob)
            if o.type == "GENERATION" and names.get(o.parent_observation_id) == "rubric_v2":
                msgs = cast(list[JSON], loads(o.input))
                system = msgs[0]["content"] if msgs[0]["role"] == "system" else None
                last = msgs[-1]  # GenAI semconv shape: {"role", "parts": [{"type": "text", "content"}]}
                user = last.get("content") or "".join(p.get("content", "") for p in last.get("parts", []))
                a = asked.get((system, user))
                out = cast(list[JSON], loads(o.output))
                text = "".join(p.get("content", "") for m in out for p in m.get("parts", []))
                parity["input exact" if a else "input differs"] += 1
                if a:
                    parity["output = verdict text" if text == a["text"] else "output differs"] += 1
        scores = lf.api.scores.get_many(trace_id=it.trace_id, fields="details,subject", limit=100).data
        for s in scores:
            blob = json.dumps(s.dict(), default=str, ensure_ascii=False)
            sizes["scores"] += len(blob.encode())
            counts["scores"] += 1
            blobs.append(blob)
    ds = lf.get_dataset(DATASET)
    for i in ds.items:
        blob = json.dumps(i.__dict__, default=str, ensure_ascii=False)
        sizes["dataset items"] += len(blob.encode())
        counts["dataset items"] += 1
        blobs.append(blob)
    print(f"{name}: {len(items)} trials; judge generations vs P4's cache: {dict(parity)} (cache has {len(cache)})")
    total = sum(sizes.values())
    print(f"fetched back: {total / 1e6:.2f} MB")
    for k, v in sizes.most_common():
        print(f"  {k:<45} {counts[k]:>4}  {v / 1e3:8.1f} kB  {100 * v / total:4.1f}%")
    text = "\n".join(blobs)
    user = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    probes = {
        "the Windows username": user,
        "Users\\ or Users/": "Users",
        "this checkout's folder name": HERE.parent.parent.name,
        "this checkout's absolute path (JSON-escaped)": json.dumps(str(HERE.parent.parent))[1:-1],
        "127.0.0.1 (Vessel)": "127.0.0.1",
        "sk-ant (Anthropic key prefix)": "sk-ant",
        "sk-lf (Langfuse secret key prefix)": "sk-lf",
        "pk-lf (Langfuse public key prefix)": "pk-lf",
        "anthropic.organization.id": "anthropic.organization.id",
        "anthropic.workspace.id": "anthropic.workspace.id",
        "X-Vessel (headers)": "X-Vessel",
        "traceparent": "traceparent",
        "unknown_service:python.exe": "unknown_service:python.exe",
        "git commit/author": "author",
    }
    for label, needle in probes.items():
        if needle:
            print(f"  {label:<38} {text.count(needle)}")


if __name__ == "__main__":
    main(sys.argv[1])
