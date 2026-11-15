"""Offline checks for the P5 helpers: slot mapping, pair freezing, padding. Run: uv run python tests/test_bias.py"""

import json
import sys
from pathlib import Path

from supportdesk.bias import human_pair_ids, pool, slot_to_model

sys.path.insert(0, "experiments")
from length import POLICY, POLITE_POST, POLITE_PRE, pad
from self_preference import kappa3, winner


def test_slot_to_model() -> None:
    assert slot_to_model("A", "haiku") == "haiku" and slot_to_model("B", "haiku") == "qwen"
    assert slot_to_model("A", "qwen") == "qwen" and slot_to_model("B", "qwen") == "haiku"
    assert slot_to_model("tie", "qwen") == "tie"


def test_human_queue_is_balanced_and_blind() -> None:
    queue = [json.loads(x) for x in Path("cases/pairs/human_queue.jsonl").read_text("utf-8").splitlines()]
    firsts = [q for q in queue if q["repeat_of"] is None]
    repeats = [q for q in queue if q["repeat_of"]]
    assert len(firsts) == 50 and len(repeats) == 5 and len(human_pair_ids()) == 50
    assert sum(q["a"] == "haiku" for q in firsts) == 25  # Haiku in slot A for exactly half
    first_pos = {q["pair_id"]: n for n, q in enumerate(queue) if q["repeat_of"] is None}
    for n, r in enumerate(queue):
        if r["repeat_of"]:
            orig = next(q for q in firsts if q["pair_id"] == r["pair_id"])
            assert r["a"] != orig["a"] and first_pos[r["pair_id"]] < 25 <= n  # swapped, and shown later
    pairs = pool()
    assert len(pairs) == 200
    assert all(p["haiku"]["model"] == "haiku" and p["qwen"]["model"] == "qwen" for p in pairs.values())


def test_pad_keeps_greeting_body_and_signoff() -> None:
    reply = "Hello Ana,\n\nYour refund of $10.00 has been issued.\n\nBest regards,\nLarkspur Supply"
    polite, policy = pad(reply, "polite"), pad(reply, "policy")
    assert polite.startswith(f"Hello Ana,\n\n{POLITE_PRE}")
    assert polite.endswith(f"{POLITE_POST}\n\nBest regards,\nLarkspur Supply")
    assert policy.index("issued.") < policy.index(POLICY) < policy.index("Best regards")
    assert "$10.00" in polite and "$10.00" in policy


def test_kappa3_and_winner() -> None:
    assert kappa3(["haiku", "qwen", "tie"], ["haiku", "qwen", "tie"]) == 1.0
    assert abs(kappa3(["haiku"] * 10, ["haiku"] * 9 + ["qwen"])) < 1e-9  # 90% raw agreement, nothing beyond chance
    assert (winner(1.0), winner(0.0), winner(0.5), winner(0.75)) == ("haiku", "qwen", "tie", "tie")


if __name__ == "__main__":
    test_slot_to_model()
    test_human_queue_is_balanced_and_blind()
    test_pad_keeps_greeting_body_and_signoff()
    test_kappa3_and_winner()
    print("ok: slot mapping, frozen pairs, padding, kappa")
