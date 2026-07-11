# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Initialization and entry-point for console application."""

# Type annotations
from __future__ import annotations
from typing import List

# Standard libs
import sys
from importlib.metadata import version as get_version
from platform import python_version, python_implementation

# External libs
from cmdkit.app import Application, ApplicationGroup
from cmdkit.cli import Interface

# Internal libs
from sosfs.core.exceptions import get_shared_exception_mapping
from sosfs.core.logging import Logger, initialize_logging

# Public interface
__all__ = ['SOSFSApp', 'main', '__version__', 'APP_VERSION', 'APP_USAGE', 'APP_HELP']

# Project metadata
__version__     = get_version('sosfs')
__website__     = 'https://github.com/glentner/simple-overlay-service'
__description__ = 'Managed data tiering for research computing over FUSE.'

# Initialize logger
log = Logger.with_name('sosfs')


# Inject logger into command-line framework
Application.log_critical = log.critical
Application.log_exception = log.exception


# NOTE: These stub applications are replaced by real implementations in their own modules
# as phases land (mount/umount in Phase 6; status/flush/commit/audit/config in Phase 9).
# FUSE- and S3-touching modules must be imported lazily by those implementations (PEP 810).
class StubApp(Application):
    """Base for not-yet-implemented subcommands."""

    command: str = ''
    phase: int = 0
    ALLOW_NOARGS = True
    exceptions = get_shared_exception_mapping()

    def run(self) -> None:
        """Report unimplemented status."""
        raise RuntimeError(f'Not implemented: sosfs {self.command} (see ROADMAP.md, Phase {self.phase})')


def stub_usage(command: str, summary: str) -> str:
    """Construct minimal usage text for a stub subcommand."""
    return f'Usage:\n  sosfs {command} [<args>...]\n  {summary}\n\nThis command is not yet implemented.'


MOUNT_USAGE = """\
Usage:
  sosfs mount [-h] <spec> <mountpoint> [-f] [-d]
  Mount an overlay filesystem defined by <spec> at <mountpoint>.\
"""

MOUNT_HELP = f"""\
{MOUNT_USAGE}

Arguments:
  spec                   Path to the overlay spec (TOML).
  mountpoint             Existing directory to mount the overlay over.

Options:
  -f, --foreground       Run in the foreground instead of daemonizing.
  -d, --debug            Enable libfuse debug output (implies foreground behavior).
  -h, --help             Show this message and exit.\
"""


class MountApp(Application):
    """Mount an overlay filesystem."""

    interface = Interface('sosfs mount', MOUNT_USAGE, MOUNT_HELP)
    interface.add_argument('spec')
    interface.add_argument('mountpoint')
    interface.add_argument('-f', '--foreground', action='store_true')
    interface.add_argument('-d', '--debug', action='store_true')
    exceptions = get_shared_exception_mapping()

    spec: str
    mountpoint: str
    foreground: bool = False
    debug: bool = False

    def run(self) -> None:
        """Load the spec and hand off to the mount lifecycle (FUSE imports stay lazy)."""
        from sosfs.tier.spec import OverlaySpec
        from sosfs.fuse.mount import Mount
        overlay = OverlaySpec.load(self.spec)
        Mount(overlay, self.mountpoint, foreground=self.foreground or self.debug, debug=self.debug).run()


UMOUNT_USAGE = """\
Usage:
  sosfs umount [-h] <spec> [--detach | --force]
  Drain (or detach) and unmount a running overlay.\
"""

UMOUNT_HELP = f"""\
{UMOUNT_USAGE}

Arguments:
  spec                   Path to the overlay spec (TOML); its state directory locates the daemon.

Options:
      --detach           Detach (rescue node-local dirty data, finalize later with `commit`).
      --force            Plain unmount, skipping even the rescue.
  -h, --help             Show this message and exit.

With no option the overlay drains fully to the source and settles clean.\
"""


class UmountApp(Application):
    """Drain (or detach) and unmount an overlay."""

    interface = Interface('sosfs umount', UMOUNT_USAGE, UMOUNT_HELP)
    interface.add_argument('spec')
    interface.add_argument('--detach', action='store_true')
    interface.add_argument('--force', action='store_true')
    exceptions = get_shared_exception_mapping()

    spec: str
    detach: bool = False
    force: bool = False

    def run(self) -> None:
        """Load the spec and ask the running daemon to tear down (FUSE imports stay lazy)."""
        from sosfs.tier.spec import OverlaySpec
        from sosfs.fuse.mount import request_shutdown
        overlay = OverlaySpec.load(self.spec)
        request_shutdown(overlay, detach=self.detach, force=self.force)


class StatusApp(StubApp):
    """Show per-tier usage, dirty counts, and transfer activity."""
    command, phase = 'status', 9
    interface = Interface('sosfs status', stub_usage('status', __doc__), stub_usage('status', __doc__))
    interface.add_argument('args', nargs='*')


class FlushApp(StubApp):
    """Force upstream commit on a live mount."""
    command, phase = 'flush', 9
    interface = Interface('sosfs flush', stub_usage('flush', __doc__), stub_usage('flush', __doc__))
    interface.add_argument('args', nargs='*')


class CommitApp(StubApp):
    """Finalize the source offline from queued write-back state."""
    command, phase = 'commit', 9
    interface = Interface('sosfs commit', stub_usage('commit', __doc__), stub_usage('commit', __doc__))
    interface.add_argument('args', nargs='*')


class AuditApp(StubApp):
    """Reconcile the state database against managed locations."""
    command, phase = 'audit', 9
    interface = Interface('sosfs audit', stub_usage('audit', __doc__), stub_usage('audit', __doc__))
    interface.add_argument('args', nargs='*')


class ConfigApp(StubApp):
    """Get, set, and locate configuration."""
    command, phase = 'config', 9
    interface = Interface('sosfs config', stub_usage('config', __doc__), stub_usage('config', __doc__))
    interface.add_argument('args', nargs='*')


FREE_THREADING = '' if getattr(sys, '_is_gil_enabled', lambda: True)() else ' free-threading'

APP_NAME = 'sosfs'
APP_VERSION = f'sosfs v{__version__} ({python_implementation()} {python_version()}{FREE_THREADING})'
APP_USAGE = f"""\
Usage:
  {APP_NAME} [-h] [-v] <command> [<args>...]
  {__description__}\
"""

APP_HELP = f"""\
{APP_USAGE}

Commands:
  mount                  {MountApp.__doc__}
  umount                 {UmountApp.__doc__}
  status                 {StatusApp.__doc__}
  flush                  {FlushApp.__doc__}
  commit                 {CommitApp.__doc__}
  audit                  {AuditApp.__doc__}
  config                 {ConfigApp.__doc__}

Options:
  -h, --help             Show this message and exit.
  -v, --version          Show the version and exit.

Documentation and issue tracking at:
{__website__}\
"""


class SOSFSApp(ApplicationGroup):
    """Top-level application class for console application."""

    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)
    interface.add_argument('-v', '--version', action='version', version=APP_VERSION)
    interface.add_argument('command')

    commands = {
        'mount': MountApp,
        'umount': UmountApp,
        'status': StatusApp,
        'flush': FlushApp,
        'commit': CommitApp,
        'audit': AuditApp,
        'config': ConfigApp,
    }


def main(argv: List[str] | None = None) -> int:
    """Entry-point for 'sosfs' console application."""
    initialize_logging()
    return SOSFSApp.main(argv or sys.argv[1:])
