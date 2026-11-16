"""System under test: a refund-desk agent for a fictional shop.

P9's entry point for the eval frameworks: `supportdesk.run(message) -> Trial` (and `arun`, for async frameworks). It
runs the shipped agent (`supportdesk.task:haiku`, src/supportdesk/system_prompt.txt) on one customer email against a
fresh store. The returned Trial carries what the agent said (`output`, the close_ticket call), what it did (`outcome`,
the refunds and escalations the store recorded) and how (`messages`, the full transcript, and `calls`).
"""

import asyncio
import threading
from typing import Any

from evalkit.core import Case, Trial


async def arun(message: str, faults: dict[str, Any] | None = None, *, case_id: str = "adhoc", rep: int = 0,
               run_id: str = "p9") -> Trial:  # fmt: skip
    """`faults` injects tool errors, as in the fault cases (`Case.faults`). `case_id`, `rep` and `run_id` only label the
    trial (and its Vessel tags and session, when VESSEL_URL is set)."""
    from supportdesk.task import haiku  # imported here: task imports this package's submodules

    return await haiku(Case(case_id, message, {}, faults=faults or {}), rep=rep, run_id=run_id)


_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def _background_loop() -> asyncio.AbstractEventLoop:
    """One loop for every sync call, on its own thread. The API client is cached per run_id and bound to the loop it
    was first used on, so a fresh asyncio.run() per call would leave it pointing at a closed loop."""
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="supportdesk-run", daemon=True).start()
        return _loop


def run(message: str, faults: dict[str, Any] | None = None, *, case_id: str = "adhoc", rep: int = 0,
        run_id: str = "p9") -> Trial:  # fmt: skip
    """Synchronous `arun`, safe to call from any thread, including several at once."""
    coro = arun(message, faults, case_id=case_id, rep=rep, run_id=run_id)
    return asyncio.run_coroutine_threadsafe(coro, _background_loop()).result()
