# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Platform-specific file paths and initialization."""

# Type annotations
from __future__ import annotations
from typing import List, Final

# Standard libs
import os
import sys
import platform

# External libs
from cmdkit.app import exit_status
from cmdkit.ansi import bold, magenta
from cmdkit.config import Namespace

# Public interface
__all__ = ['cwd', 'home', 'is_admin', 'config_home', 'state_home', 'site', 'path', 'default_path']


if platform.system() == 'Windows':
    print(f'{bold(magenta("CRITICAL"))} [{__name__}] Platform not supported (Windows)', file=sys.stderr)
    sys.exit(exit_status.bad_config)


cwd: Final[str] = os.getcwd()
home: Final[str] = os.path.expanduser('~')
is_admin: Final[bool] = os.getuid() == 0


# XDG-style user locations on all supported platforms (PLAN.md -> Configuration)
config_home: Final[str] = os.getenv('XDG_CONFIG_HOME', os.path.join(home, '.config'))
state_home: Final[str] = os.getenv('XDG_STATE_HOME', os.path.join(home, '.local', 'state'))


site = Namespace(system='/', user=home, local=cwd)
path = Namespace({
    'system': {
        'config': os.path.join(site.system, 'etc', 'sosfs', 'config.toml'),
        'log': os.path.join(site.system, 'var', 'log', 'sosfs'),
        'state': os.path.join(site.system, 'var', 'lib', 'sosfs')},
    'user': {
        'config': os.path.join(config_home, 'sosfs', 'config.toml'),
        'log': os.path.join(state_home, 'sosfs', 'log'),
        'state': os.path.join(state_home, 'sosfs')},
    'local': {
        'config': os.path.join(site.local, '.sosfs.toml'),
        'log': os.path.join(state_home, 'sosfs', 'log'),
        'state': os.path.join(state_home, 'sosfs')},
})


default_path = path.user if not is_admin else path.system


# Automatically initialize default site directories
default_dirs: Final[List[str]] = [default_path.log, default_path.state]
for default_dir in default_dirs:
    try:
        os.makedirs(default_dir, exist_ok=True)
    except PermissionError:
        pass
