from collections.abc import AsyncIterator
from typing import Any

from ..chatgpt.chat import ask as _cg_ask
from ..chatgpt.chat import ask_stream as _cg_ask_stream
from .state import shared_state


async def ask_stream(page: Any, prompt: str, model: str | None = None, reset: bool = False) -> AsyncIterator[dict[str, Any]]:
    async for ev in _cg_ask_stream(page, prompt, model=model, reset=reset, state_dict=shared_state):
        yield ev


async def ask(page: Any, prompt: str, model: str | None = None, reset: bool = False) -> dict[str, Any]:
    return await _cg_ask(page, prompt, model=model, reset=reset, state_dict=shared_state)
