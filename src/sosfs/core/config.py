# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration for sosfs (application settings — overlay specs are separate)."""

# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Protocol, Final

# Standard libs
import os
import sys
import shutil
import logging
import functools
from datetime import datetime

# External libs
import tomlkit
from cmdkit.app import exit_status
from cmdkit.config import Namespace, Configuration, Environ, ConfigurationError

# Internal libs
from sosfs.core.platform import path
from sosfs.core.exceptions import write_traceback

# Public interface
__all__ = ['config', 'default', 'update', 'blame', 'load', 'reload', 'reload_local',
           'load_file', 'reload_file', 'load_env', 'reload_env',
           'Namespace', 'ConfigurationError', 'PARAM_UNSET',
           'DEFAULT_LOGGING_LEVEL', 'DEFAULT_LOGGING_STYLE', 'LOGGING_STYLES']


# Partial logging (not yet configured - initialized afterward)
log = logging.getLogger(__name__)


DEFAULT_LOGGING_LEVEL: Final[str] = 'info'
DEFAULT_LOGGING_STYLE: Final[str] = 'default'

LOGGING_STYLES: Final[Dict[str, Dict[str, str]]] = {
    'default': {
        'format': ('%(ansi_bold)s%(ansi_level)s%(levelname)8s%(ansi_reset)s %(ansi_faint)s[%(name)s]%(ansi_reset)s'
                   ' %(message)s'),
    },
    'system': {
        'format': '%(asctime)s.%(msecs)03d %(hostname)s %(levelname)8s [%(app_id)s] [%(name)s] %(message)s',
    },
    'detailed': {
        'format': ('%(ansi_faint)s%(asctime)s.%(msecs)03d %(hostname)s %(ansi_reset)s'
                   '%(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s '
                   '%(ansi_faint)s[%(name)s]%(ansi_reset)s %(message)s'),
    },
    'detailed-compact': {
        'format': ('%(ansi_faint)s%(elapsed_hms)s [%(hostname_short)s] %(ansi_reset)s'
                   '%(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s '
                   '%(ansi_faint)s[%(relative_name)s]%(ansi_reset)s %(message)s'),
    },
    'short': {
        'format': '%(ansi_level)s[%(elapsed).3f]%(ansi_reset)s %(message)s',
    }
}


# For TOML compatibility we cannot use an actual None
PARAM_UNSET: Final[str] = '<none>'


# Environment variables and configuration files are automatically depth-first merged with defaults
default = Namespace({
    'logging': {
        'color': True,
        'datefmt': '%Y-%m-%d %H:%M:%S',
        'level': DEFAULT_LOGGING_LEVEL,
        'style': DEFAULT_LOGGING_STYLE,
        **LOGGING_STYLES.get(DEFAULT_LOGGING_STYLE),
        'file': PARAM_UNSET,  # Path-like enables plain file-based logging (TRACE, 'system' style)
    },
})


def reload_file(filepath: str) -> Namespace:
    """Force reloading configuration file."""
    if not os.path.exists(filepath):
        return Namespace({})
    try:
        return Namespace.from_toml(filepath)
    except Exception as err:
        raise ConfigurationError(f'(from file: {filepath}) {err.__class__.__name__}: {err}')


@functools.lru_cache(maxsize=None)
def load_file(filepath: str) -> Namespace:
    """Load configuration file."""
    return reload_file(filepath)


def reload_env() -> Environ:
    """Force reloading environment variables and expanding hierarchy as namespace."""
    return Environ(prefix='SOSFS').expand()


@functools.lru_cache(maxsize=None)
def load_env() -> Environ:
    """Load environment variables and expand hierarchy as namespace."""
    return reload_env()


def partial_load(system: Optional[str] = path.system.config,
                 user: Optional[str] = path.user.config,
                 local: Optional[str] = path.local.config,
                 **preload: Namespace) -> Configuration:
    """Load configuration from files and merge environment variables."""
    return Configuration(**{
        'default': default, **preload,
        'system': {} if not system else load_file(system),
        'user': {} if not user else load_file(user),
        'local': {} if not local else load_file(local),
        'env': load_env(),
    })


def partial_reload(system: Optional[str] = path.system.config,
                   user: Optional[str] = path.user.config,
                   local: Optional[str] = path.local.config,
                   **preload: Namespace) -> Configuration:
    """Force reload configuration from files and merge environment variables."""
    return Configuration(**{
        'default': default, **preload,
        'system': {} if not system else reload_file(system),
        'user': {} if not user else reload_file(user),
        'local': {} if not local else reload_file(local),
        'env': reload_env(),
    })


def blame(base: Configuration, *varpath: str) -> Optional[str]:
    """Construct filename or variable assignment string based on precedent of `varpath`."""
    source = base.which(*varpath)
    if not source:
        return None
    if source in ('system', 'user', 'local'):
        return f'from: {path.get(source).config}'
    elif source == 'env':
        return 'from: SOSFS_' + '_'.join([node.upper() for node in varpath])
    else:
        return f'from: <{source}>'


def get_logging_style(base: Configuration) -> str:
    """Get and check valid on `config.logging.style`."""
    style = base.logging.style
    label = blame(base, 'logging', 'style')
    if not isinstance(style, str):
        raise ConfigurationError(f'Expected string for `logging.style` ({label})')
    style = style.lower()
    if style in LOGGING_STYLES:
        return style
    else:
        raise ConfigurationError(f'Unrecognized `logging.style` \'{style}\' ({label})')


def build_preloads(base: Configuration) -> Namespace:
    """Build 'preload' namespace from base configuration."""
    return Namespace({'logging': LOGGING_STYLES.get(get_logging_style(base))})


class LoaderImpl(Protocol):
    """Loader interface for building configuration."""
    def __call__(self: LoaderImpl,
                 system: Optional[str] = path.system.config,
                 user: Optional[str] = path.user.config,
                 local: Optional[str] = path.local.config,
                 **preload: Namespace) -> Configuration: ...


def build_configuration(loader: LoaderImpl) -> Configuration:
    """Construct full configuration."""
    return loader(preload=build_preloads(base=loader()))


def load() -> Configuration:
    """Load configuration from files and merge environment variables."""
    return build_configuration(loader=partial_load)


def reload() -> Configuration:
    """Force reload configuration from files and merge environment variables."""
    return build_configuration(loader=partial_reload)


def reload_local(filepath: Optional[str] = None) -> Configuration:
    """Load configuration but only include one file."""
    loader = functools.partial(partial_reload, system=None, user=None, local=filepath)
    return build_configuration(loader=loader)


try:
    if (local_config := os.getenv('SOSFS_CONFIG_FILE', None)) is not None:
        path.local.config = local_config  # Modified as to not lie to the user
        config = reload_local(local_config)
    else:
        config = load()
except Exception as error:
    write_traceback(error, module=__name__)
    sys.exit(exit_status.bad_config)


DEFAULT_CONFIG_HEADERS: Final[str] = f"""\
# File automatically created on {datetime.now()}
# Settings here are merged automatically with defaults and environment variables
"""


def update(scope: str, partial: dict) -> None:
    """Extend the current configuration and commit it to disk."""
    config_path = path[scope].config
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    if os.path.exists(config_path):
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        config_backup_path = os.path.join(os.path.dirname(config_path), f'.config.{timestamp}.toml')
        shutil.copy(config_path, config_backup_path)
        shutil.copystat(config_path, config_backup_path)
        log.debug(f'Created backup file ({config_backup_path})')
    else:
        with open(config_path, mode='w') as stream:
            stream.write(DEFAULT_CONFIG_HEADERS)
    with open(config_path, mode='r') as stream:
        new_config = tomlkit.parse(stream.read())
    _inplace_update(new_config, partial)
    with open(config_path, mode='w') as stream:
        tomlkit.dump(new_config, stream)


# Re-implemented from `cmdkit.config.Namespace` (but works with `tomlkit`)
def _inplace_update(original: dict, partial: dict) -> dict:
    """
    Like normal `dict.update` but if values in both are mappable, descend
    a level deeper (recursive) and apply updates there instead.
    """
    for key, value in partial.items():
        if isinstance(value, dict) and isinstance(original.get(key), dict):
            original[key] = _inplace_update(original.get(key, {}), value)
        else:
            original[key] = value
    return original
