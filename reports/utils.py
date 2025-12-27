"""Utility functions for the activity report generator."""

import functools
import logging
import time
from typing import Callable, TypeVar

import requests

from constants import MAX_RETRIES

logger = logging.getLogger("activity_report")

T = TypeVar("T")


def retry_with_backoff(
    max_retries: int = MAX_RETRIES,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (requests.exceptions.RequestException,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay in seconds between retries.
        exceptions: Tuple of exception types to catch and retry on.

    Returns:
        Decorated function that will retry on specified exceptions.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        raise

                    delay = min(base_delay * (2**attempt), max_delay)
                    logger.warning(
                        "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        delay,
                        e,
                    )
                    time.sleep(delay)

            raise last_exception  # type: ignore

        return wrapper

    return decorator
