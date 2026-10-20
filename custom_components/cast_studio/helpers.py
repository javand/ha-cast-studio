"""Shared async task and executor helpers for CastStudio."""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from homeassistant.core import HomeAssistant


def create_task(
    hass: HomeAssistant,
    coro: Coroutine[Any, Any, Any],
    name: str | None = None,
) -> asyncio.Task[Any]:
    """Create an asynchronous task in Home Assistant or fallback to asyncio loop."""
    if hasattr(hass, "async_create_task") and callable(hass.async_create_task):
        res = hass.async_create_task(coro, name=name)
        if isinstance(res, asyncio.Task):
            return res
    return asyncio.create_task(coro, name=name)


async def async_run_in_executor(
    hass: HomeAssistant | None,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute synchronous blocking function in Home Assistant executor if available."""
    if hass is not None:
        executor_func = getattr(hass, "async_add_executor_job", None)
        if callable(executor_func):
            target: Callable[..., Any] = (
                functools.partial(func, **kwargs) if kwargs else func
            )
            res = executor_func(target, *args)
            if inspect.isawaitable(res):
                return await res

    try:
        loop = asyncio.get_running_loop()
        target = functools.partial(func, **kwargs) if kwargs else func
        return await loop.run_in_executor(None, target, *args)
    except Exception:  # noqa: BLE001
        return func(*args, **kwargs)
