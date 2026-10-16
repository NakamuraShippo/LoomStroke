"""
Retry utilities with exponential backoff and jitter.

Per Master.md PR-C specifications, provides retry logic with
exponential backoff and jitter for robust async operations.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def calculate_jitter(base: float = 0.2) -> float:
    """
    Calculate jitter factor for exponential backoff.

    Args:
        base: Base jitter range (0.2 = ±20%)

    Returns:
        Jitter multiplier between (1-base) and (1+base)
    """
    return 1.0 + random.uniform(-base, base)


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
) -> T:
    """
    Execute async function with exponential backoff retry.

    Implements exponential backoff with jitter as per PR-C spec:
    - delay = (2^attempt) * base_delay * jitter()
    - Max delay capped at max_delay
    - CancelledError not retried (proper cancellation)

    Args:
        coro_factory: Function that returns a coroutine
        retries: Maximum number of retry attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds

    Returns:
        Result from successful execution

    Raises:
        Last exception if all retries exhausted
    """
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            # Create and await the coroutine
            return await coro_factory()

        except asyncio.CancelledError:
            # Don't retry on cancellation
            logger.debug("Operation cancelled, not retrying")
            raise

        except Exception as e:
            last_error = e

            if attempt < retries:
                # Calculate exponential backoff with jitter
                delay = min((2**attempt) * base_delay * calculate_jitter(), max_delay)

                logger.warning(
                    f"Attempt {attempt + 1}/{retries + 1} failed: {e}. Retrying in {delay:.2f}s..."
                )

                await asyncio.sleep(delay)
            else:
                logger.error(f"All {retries + 1} attempts failed. Last error: {e}")

    # Raise the last error if we get here
    if last_error:
        raise last_error

    # Should never reach here
    raise RuntimeError("Unexpected retry logic error")
