"""
Structured logging setup.
Logs to stdout (captured by Docker) and optionally to Postgres via db.log_step.
"""

from __future__ import annotations

import logging
import os
import sys


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class JobLogger:
    """
    Thin wrapper that logs to Python logging AND Postgres simultaneously.
    """

    def __init__(self, job_id: str, step_name: str) -> None:
        self.job_id = job_id
        self.step_name = step_name
        self._log = logging.getLogger(f"job.{job_id[:8]}.{step_name}")

    def _db_log(self, level: str, message: str, details: dict | None = None) -> None:
        try:
            from utils.db import log_step
            log_step(self.job_id, self.step_name, message, level=level, details=details)
        except Exception as exc:
            self._log.warning("Failed to write log to DB: %s", exc)

    def info(self, message: str, details: dict | None = None) -> None:
        self._log.info(message)
        self._db_log("INFO", message, details)

    def warning(self, message: str, details: dict | None = None) -> None:
        self._log.warning(message)
        self._db_log("WARNING", message, details)

    def error(self, message: str, details: dict | None = None) -> None:
        self._log.error(message)
        self._db_log("ERROR", message, details)

    def debug(self, message: str, details: dict | None = None) -> None:
        self._log.debug(message)
        # Skip DB for debug to reduce noise
