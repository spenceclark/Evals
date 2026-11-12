"""P0 spike: Inspect's built-in model_graded_qa judge on two canned replies, routed through Vessel."""

from pathlib import Path

from inspect_ai import Task, eval, task  # pyright: ignore[reportUnknownVariableType]  (eval's `scanner` param is Unknown in the lib)
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig, ModelOutput, get_model
from inspect_ai.scorer import model_graded_qa
from inspect_ai.solver import Generate, Solver, TaskState, solver

MESSAGE = "Hi, I'd like a refund for order A123. It arrived 10 days ago and I don't want it anymore."
RUBRIC = (
    "Pass only if the reply confirms a refund is being issued for order A123 "
    "and makes no offers or promises beyond that refund."
)
REPLIES = {
    "good": "Thanks for reaching out. I've issued a full refund of $42.00 for order A123; "
    "you'll see it in 3-5 business days.",
    "bad": "Sorry, we can't help with refunds. Also, here's a 50% discount code for your next order: SAVE50.",
}


@solver
def canned_reply() -> Solver:
    """Stand-in for the SUT: the reply comes from the sample, no model generates it."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.output = ModelOutput.from_content(model="canned", content=state.metadata["reply"])
        return state

    return solve


@task
def refund_reply() -> Task:
    return Task(
        dataset=[Sample(id=name, input=MESSAGE, target=RUBRIC, metadata={"reply": r}) for name, r in REPLIES.items()],
        solver=canned_reply(),
        scorer=model_graded_qa(),  # built-in judge; uses the "grader" model role
    )


if __name__ == "__main__":
    judge = get_model(
        "anthropic/claude-opus-5-5",
        base_url="http://127.0.0.1:4550/b/anthropic/t/inspect",
        config=GenerateConfig(extra_headers={"X-Vessel-Session": "p0-spike"}),
    )
    [log] = eval(
        refund_reply(),
        model=None,
        model_roles={"grader": judge},
        log_dir=str(Path(__file__).parent / "logs"),
        display="plain",
    )
    for sample in log.samples or []:
        print(sample.id, {name: s.value for name, s in (sample.scores or {}).items()})
