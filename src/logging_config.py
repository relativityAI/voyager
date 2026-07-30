import logging
import os
import re

from loguru import logger
from rich.logging import RichHandler


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")


class InterceptHandler(logging.Handler):
    def emit(self, record):
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(record.levelno, record.getMessage())


_secret_pattern = re.compile(r"(Bearer\s+)?([A-Za-z0-9_-]{20,})")


def _scrub(record):
    record["message"] = _secret_pattern.sub("<redacted>", record["message"])


def setup_logging():
    os.makedirs(_logs_dir, exist_ok=True)
    logger.remove()

    logger.configure(patcher=_scrub)

    logger.add(
        RichHandler(rich_tracebacks=True, markup=True),
        format="{message}",
        level=LOG_LEVEL,
    )

    logger.add(
        os.path.join(_logs_dir, "app.json"),
        level=LOG_LEVEL,
        serialize=True,
        rotation="1 day",
        retention="14 days",
        compression="zip",
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
