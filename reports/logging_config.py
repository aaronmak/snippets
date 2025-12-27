"""Logging configuration for the activity report generator."""

import logging
import sys

# Create logger
logger = logging.getLogger("activity_report")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application.

    Args:
        verbose: If True, set log level to DEBUG, otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    formatter = logging.Formatter(
        "%(levelname)s: %(message)s" if not verbose
        else "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(handler)
