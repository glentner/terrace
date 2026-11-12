# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the Terrace CLI surface."""

# Type annotations
from __future__ import annotations

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, assert_output


pytestmark = mark.unit


def test_version() -> None:
    """Version flag reports the package version."""
    returncode, stdout, _ = main(['--version'])
    assert returncode == exit_status.success
    assert_output(r'terrace v\d+\.\d+\.\d+', stdout)


def test_help() -> None:
    """Help lists every subcommand."""
    returncode, stdout, _ = main(['--help'])
    assert returncode == exit_status.success
    for command in ('mount', 'umount', 'status', 'flush', 'commit', 'audit', 'config'):
        assert_output(rf'^  {command}\b', stdout)


def test_bare_invocation() -> None:
    """Bare invocation prints usage without error."""
    returncode, stdout, stderr = main([])
    assert returncode == exit_status.usage
    assert_output(r'Usage:', stdout + stderr)


@mark.parametrize('command', ['status', 'flush', 'commit', 'audit', 'config'])
def test_stub_commands(command: str) -> None:
    """Stub subcommands fail uniformly with a pointer to the roadmap."""
    returncode, _, stderr = main([command])
    assert returncode == exit_status.runtime_error
    assert_output(rf'Not implemented: terrace {command}', stderr)


@mark.parametrize('command', ['mount', 'umount'])
def test_mount_commands_require_arguments(command: str) -> None:
    """The mount/umount commands are implemented and reject a bare invocation (no stub message)."""
    returncode, stdout, stderr = main([command])
    assert returncode != exit_status.success
    assert 'Not implemented' not in stdout + stderr


def test_unknown_command() -> None:
    """Unknown subcommands report a usage error."""
    returncode, stdout, stderr = main(['frobnicate'])
    assert returncode != exit_status.success
    assert 'frobnicate' in stdout + stderr


def test_temp_site_isolation(temp_site) -> None:
    """The temp_site fixture scrubs TERRACE_* and redirects XDG paths."""
    import os
    assert not [name for name in os.environ if name.startswith('TERRACE_')]
    assert os.environ['XDG_CONFIG_HOME'].startswith(str(temp_site))
    assert os.environ['XDG_STATE_HOME'].startswith(str(temp_site))
