"""
Dedicated Single-Threaded Executor for Playwright operations.

Playwright's Python Sync API uses greenlets bound to the thread in which
sync_playwright().start() was executed. Calling any Playwright method
from another thread (e.g. via asyncio.to_thread or ThreadPoolExecutor)
raises `greenlet.error: Cannot switch to a different thread`.

This module provides a singleton single-threaded executor and helper functions
ensuring all Playwright browser interactions occur strictly on the same OS thread.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")

# Dedicated single-threaded executor for all Playwright sync operations
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright_thread")


async def run_in_browser_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a synchronous function interacting with Playwright inside the dedicated browser thread.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_browser_executor, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(_browser_executor, func, *args)


async def iterate_in_browser_thread(
    generator_func: Callable[..., Iterator[T]],
    *args: Any,
    **kwargs: Any,
) -> AsyncIterator[T]:
    """
    Safely stream items from a synchronous generator executing inside the dedicated browser thread
    to an async generator in the main event loop via an asyncio.Queue.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _producer():
        try:
            gen = generator_func(*args, **kwargs)
            for item in gen:
                loop.call_soon_threadsafe(queue.put_nowait, ("item", item))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    fut = loop.run_in_executor(_browser_executor, _producer)

    try:
        while True:
            msg_type, payload = await queue.get()
            if msg_type == "item":
                yield payload
            elif msg_type == "error":
                raise payload
            elif msg_type == "done":
                break
    finally:
        await fut
