import logging
import os
import re

from loguru import logger
from rich.console import Console
from rich.text import Text

_logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")

_console = Console(stderr=True, soft_wrap=True)

_LEVEL_STYLES = {
    "TRACE": "dim",
    "DEBUG": "cyan",
    "INFO": "green",
    "SUCCESS": "bold green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}


def _rich_sink(message):
    record = message.record
    if record["exception"] is not None:
        _console.print(Text(str(message)))
        return

    time_str = record["time"].strftime("%H:%M:%S")
    level_name = record["level"].name
    level_str = f"{level_name:<7}"
    source = f"{record['name']}.{record['function']}:{record['line']}"

    msg = Text(str(message).rstrip("\n"), style="log.message")
    fixed = len(time_str) + 1 + len(level_str) + 1 + len(source) + 3
    msg.truncate(max(_console.width - fixed, 0))

    line = Text()
    line.append(Text(time_str, style="log.time"))
    line.append(" ")
    line.append(Text(level_str, style=_LEVEL_STYLES.get(level_name, "")))
    line.append(" ")
    line.append(msg)
    line.append(Text("  " + source, style="dim"))
    _console.print(line)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(level, record.getMessage())


_bearer_pattern = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_query_param_pattern = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|secret|password|access[_-]?token)=)[^&\s]+"
)


def _scrub(record):
    message = _bearer_pattern.sub("Bearer <redacted>", record["message"])
    message = _query_param_pattern.sub(lambda m: f"{m.group(1)}<redacted>", message)
    record["message"] = message


def setup_logging():
    os.makedirs(_logs_dir, exist_ok=True)
    logger.remove()

    level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger.configure(patcher=_scrub)

    logger.add(_rich_sink, format="{message}", level=level)

    logger.add(
        os.path.join(_logs_dir, "app.json"),
        level=level,
        serialize=True,
        rotation="1 day",
        retention="14 days",
        compression="zip",
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
