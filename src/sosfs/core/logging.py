# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Logging configuration and handling."""

# Type annotations
from __future__ import annotations
from typing import Tuple, Dict, Any, Type, Final, Optional

# Standard libs
import os
import re
import sys
import socket
from uuid import uuid4
from datetime import timedelta
from logging import (
    Formatter, NullHandler, FileHandler, StreamHandler as _StreamHandler, Logger as _Logger, LogRecord as _LogRecord,
    DEBUG, INFO, WARNING, ERROR, CRITICAL, NOTSET,
    getLogger, setLoggerClass, setLogRecordFactory, addLevelName
)

# External libs
from cmdkit.app import exit_status
from cmdkit.ansi import Ansi, COLOR_STDERR
from cmdkit.config import Namespace, ConfigurationError

# Internal libs
from sosfs.core.exceptions import write_traceback
from sosfs.core.config import config, blame, DEFAULT_LOGGING_LEVEL, DEFAULT_LOGGING_STYLE, LOGGING_STYLES, PARAM_UNSET

# Public interface
__all__ = ['Logger', 'TRACE', 'HOSTNAME', 'INSTANCE', 'LEVEL_MAPPING', 'initialize_logging']


# Cached for later use
HOSTNAME: Final[str] = socket.gethostname()
HOSTNAME_SHORT: Final[str] = HOSTNAME.split('.', 1)[0]


# Unique for every instance of sosfs
INSTANCE: Final[str] = str(uuid4())


TRACE: Final[int] = DEBUG - 5
addLevelName(TRACE, 'TRACE')


# Canonical colors for logging messages
COLOR_MAPPING: Final[Dict[str, Ansi]] = {
    'NULL': Ansi.NULL,
    'TRACE': Ansi.CYAN,
    'DEBUG': Ansi.BLUE,
    'INFO': Ansi.GREEN,
    'WARNING': Ansi.YELLOW,
    'ERROR': Ansi.RED,
    'CRITICAL': Ansi.MAGENTA
}


# Re-index for programmatic use
LEVEL_MAPPING: Final[Dict[str, int]] = {
    'NOTSET': NOTSET,
    'TRACE': TRACE,
    'DEBUG': DEBUG,
    'INFO': INFO,
    'WARNING': WARNING,
    'ERROR': ERROR,
    'CRITICAL': CRITICAL
}


class Logger(_Logger):
    """Extend Logger to implement TRACE level."""

    def trace(self, msg: str, *args, **kwargs):
        """Log 'msg % args' with severity 'TRACE'."""
        if self.isEnabledFor(TRACE):
            self._log(TRACE, msg, args, **kwargs)

    @classmethod
    def with_name(cls: Type[Logger], name: str) -> Logger:
        """Shorthand for `log: Logger = logging.getLogger(name)`."""
        return getLogger(name)


# Inject class back into logging library
setLoggerClass(Logger)


def solve_relative_time(elapsed: float) -> Tuple[float, int, timedelta, str]:
    """
    Multiple formats of relative time since `elapsed` seconds.
    Returns:
        - Relative time in seconds (i.e., `elapsed`)
        - Relative time in milliseconds
        - Relative time as `datetime.timedelta`
        - Relative time in dd-hh:mm:ss.sss format
    """
    elapsed_ms = int(elapsed * 1000)
    reltime_delta = timedelta(seconds=elapsed)
    reltime_delta_hours, remainder = divmod(reltime_delta.seconds, 3600)
    reltime_delta_minutes, reltime_delta_seconds = divmod(remainder, 60)
    reltime_delta_milliseconds = int(reltime_delta.microseconds / 1000)
    return (
        elapsed,
        elapsed_ms,
        reltime_delta,
        f'{reltime_delta.days:02d}-{reltime_delta_hours:02d}:{reltime_delta_minutes:02d}:'
        f'{reltime_delta_seconds:02d}.{reltime_delta_milliseconds:03d}'
    )


class LogRecord(_LogRecord):
    """Extends LogRecord to include ANSI colors, time formats, and other attributes."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize with additional attributes."""

        super().__init__(*args, **kwargs)

        # Context attributes
        self.app_id = INSTANCE
        self.hostname = HOSTNAME
        self.hostname_short = HOSTNAME_SHORT
        self.relative_name = self.name.split('.', 1)[-1]

        # Formatting attributes
        self.ansi_level = COLOR_MAPPING.get(self.levelname, Ansi.NULL).value if COLOR_STDERR else ''
        self.ansi_reset = Ansi.RESET.value if COLOR_STDERR else ''
        self.ansi_bold = Ansi.BOLD.value if COLOR_STDERR else ''
        self.ansi_faint = Ansi.FAINT.value if COLOR_STDERR else ''
        self.ansi_italic = Ansi.ITALIC.value if COLOR_STDERR else ''
        self.ansi_underline = Ansi.UNDERLINE.value if COLOR_STDERR else ''

        # Timing attributes
        (self.elapsed,
         self.elapsed_ms,
         self.elapsed_delta,
         self.elapsed_hms) = solve_relative_time(self.relativeCreated / 1000)


# Inject factory back into logging library
setLogRecordFactory(LogRecord)


# We don't find it good to have logging messages go missing if misconfigured.
# So we override the StreamHandler to panic on exceptions.
class StreamHandler(_StreamHandler):
    """A StreamHandler that panics on exceptions in the logging configuration."""

    def handleError(self, record: LogRecord) -> None:
        """Pretty-print message and write traceback to file."""
        err_type, err_val, tb = sys.exc_info()
        write_traceback(err_val, module=__name__)
        sys.exit(exit_status.bad_config)


def level_from_name(name: Any, source: str = 'logging.level') -> int:
    """Get level value from `name`."""
    label = blame(config, *source.split('.'))
    if not isinstance(name, str):
        raise ConfigurationError(f'Expected string for \'{source}\', given \'{name}\' ({label})')
    try:
        return LEVEL_MAPPING[name.upper()]
    except KeyError:
        raise ConfigurationError(f'Unsupported logging level \'{name}\' ({label})')


def format_from_style(name: Any, source: str = 'logging.style') -> str:
    """Get style value from `name`."""
    label = blame(config, *source.split('.'))
    if not isinstance(name, str):
        raise ConfigurationError(f'Expected string for \'{source}\', given \'{name}\' ({label})')
    try:
        return LOGGING_STYLES[name.lower()]['format']
    except KeyError:
        raise ConfigurationError(f'Unsupported logging style \'{name}\' ({label})')


# ANSI placeholders are kept in the stream handler but stripped for file handlers
ANSI_PATTERN: Final[re.Pattern] = re.compile(r'%\(ansi_\w+\)s')


def strip_ansi_format(format_string: str) -> str:
    """Remove ANSI escape sequence placeholders from format string."""
    return ANSI_PATTERN.sub('', format_string)


# Null handler for library use
logger = getLogger('sosfs')
logger.addHandler(NullHandler())


try:
    stream_config = Namespace(config.logging.copy())
    datefmt = stream_config.pop('datefmt', config.logging.datefmt)
    stream_level = level_from_name(stream_config.pop('level', DEFAULT_LOGGING_LEVEL), 'logging.level')
    stream_color = stream_config.pop('color', config.logging.color)
    stream_format = format_from_style(stream_config.pop('style', DEFAULT_LOGGING_STYLE), 'logging.style')
    stream_format = stream_config.pop('format', stream_format)
    stream_format = stream_format if stream_color and COLOR_STDERR else strip_ansi_format(stream_format)
    stream_handler = StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(Formatter(stream_format, datefmt=datefmt))
    stream_handler.setLevel(stream_level)
except Exception as exc:
    write_traceback(exc, module=__name__)
    sys.exit(exit_status.bad_config)


# Not initialized unless file-based logging enabled
file_handler: Optional[FileHandler] = None


# Only permit calling initialize_logging once
_INIT: bool = False


def initialize_logging() -> None:
    """Enable logging output to the console and (optionally) a plain file."""

    global _INIT, file_handler
    if _INIT:
        return
    else:
        _INIT = True

    # We directly attach the StreamHandler to the main logger to ensure that any messages logged
    # before the logging subsystem is initialized are also captured.
    logger.addHandler(stream_handler)
    logger.setLevel(TRACE)  # Level filtering set on handlers

    # File-based logging is plain and append-only in sosfs; rotation and compression are
    # deliberately out of scope for the core (PLAN.md -> Observability).
    file_path = config.logging.file
    if isinstance(file_path, str) and file_path not in (PARAM_UNSET, ''):
        file_format = strip_ansi_format(LOGGING_STYLES['system']['format'])
        file_handler = FileHandler(os.path.expanduser(file_path), mode='a', encoding='utf-8', delay=True)
        file_handler.setFormatter(Formatter(file_format, datefmt=config.logging.datefmt))
        file_handler.setLevel(TRACE)
        logger.addHandler(file_handler)
