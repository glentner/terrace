# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Core exception handling useful for import-time issues."""

# Type annotations
from __future__ import annotations
from typing import Dict, Union, Callable, Type

# Standard libs
import os
import sys
import logging
import functools
import traceback
from datetime import datetime

# External libs
from cmdkit.app import exit_status
from cmdkit.config import Namespace, ConfigurationError
from cmdkit.ansi import faint, bold, magenta, yellow, red, COLOR_STDERR

# Internal libs
from terrace.core.platform import default_path

# Public interface
__all__ = ['TerraceError', 'display_warning', 'display_error', 'display_critical',
           'traceback_filepath', 'write_traceback', 'handle_exception', 'handle_exception_silently',
           'get_shared_exception_mapping']


class TerraceError(Exception):
    """Base class for Terrace domain exceptions."""


def _display_message(levelname: str, error: Union[Exception, str],
                     module: str = None, colorized: Callable[[str], str] = None) -> None:
    """Generic message display for import-time warnings and errors."""
    text = error if isinstance(error, str) else f'{error.__class__.__name__}: {error}'
    if COLOR_STDERR:
        name = '' if not module else faint(f'[{module}]')
        level = levelname if colorized is None else bold(colorized(levelname))
    else:
        name = '' if not module else f'[{module}]'
        level = levelname
    print(f'{level} {name} {text}', file=sys.stderr)


# Specialized methods for each severity level
display_warning = functools.partial(_display_message, 'WARNING', colorized=yellow)
display_error = functools.partial(_display_message, 'ERROR', colorized=red)
display_critical = functools.partial(_display_message, 'CRITICAL', colorized=magenta)


def traceback_filepath(path: Namespace = None) -> str:
    """Construct filepath for writing traceback."""
    path = path or default_path
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    return os.path.join(path.log, f'exception-{time}.log')


def write_traceback(exc: Exception, site: Namespace = None, logger: logging.Logger = None,
                    status: int = exit_status.uncaught_exception, module: str = None) -> int:
    """Write exception traceback to file and return exit code."""
    write = functools.partial(display_critical, module=module) if not logger else logger.critical
    path = traceback_filepath(site)
    with open(path, mode='w') as stream:
        print(traceback.format_exc(), file=stream)
    write(f'{exc.__class__.__name__}: ' + str(exc).replace('\n', ' - '))
    write(f'Exception traceback written to {path}')
    return status


def handle_exception(exc: Exception, logger: logging.Logger, status: int) -> int:
    """Log the exception argument and exit with `status`."""
    logger.critical(f'{exc.__class__.__name__}: ' + str(exc).replace('\n', ' - '))
    return status


def handle_exception_silently(exc: Exception) -> int:
    """Return status held by `exc.args` without logging."""
    status, = exc.args
    return status


def get_shared_exception_mapping(modname: str = 'terrace') -> Dict[Type[Exception], Callable[[Exception], int]]:
    """Globally defined exception cases for all application subcommands."""
    logger = logging.getLogger(modname)
    return {
        RuntimeError: functools.partial(handle_exception, logger=logger, status=exit_status.runtime_error),
        FileNotFoundError: functools.partial(handle_exception, logger=logger, status=exit_status.runtime_error),
        ConfigurationError: functools.partial(handle_exception, logger=logger, status=exit_status.bad_config),
        TerraceError: functools.partial(handle_exception, logger=logger, status=exit_status.runtime_error),
        Exception: functools.partial(write_traceback, logger=logger, status=exit_status.runtime_error),
    }
