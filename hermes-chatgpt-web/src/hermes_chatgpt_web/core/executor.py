"""
Executor utilities for Hermes operations.
Provides async-friendly invocation helpers.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, TypeVar

T = TypeVar("T")


async def run_in_browser_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    Run a coroutine or synchronous function directly in the async event loop.
    """
    if asyncio.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    res = func(*args, **kwargs)
    if asyncio.iscoroutine(res):
        return await res
    return res


async def iterate_in_browser_thread(
    generator_func: Callable[..., Iterator[T] | AsyncIterator[T]],
    *args: Any,
    **kwargs: Any,
) -> AsyncIterator[T]:
    """
    Stream items from a generator to an async generator.
    """
    gen = generator_func(*args, **kwargs)
    if hasattr(gen, "__aiter__"):
        async for item in gen:
            yield item
    else:
        for item in gen:
            yield item
