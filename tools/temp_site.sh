#!/bin/sh
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
#
# Run a command against a throwaway sosfs site, so factory verify commands and review
# CLI drives never touch the developer's real state directory, logs, or configuration.
# Mirrors the pytest `temp_site` fixture (tests/conftest.py): every SOSFS_* variable is
# scrubbed and XDG_CONFIG_HOME / XDG_STATE_HOME are redirected into the sandbox.
#
# The redirection has to happen before the process starts. sosfs.core.platform reads
# both variables into module constants at import and immediately creates
# <state>/sosfs/log, so a drive that sets them afterwards has already written to $HOME.
# That is also why the pytest helpers shell out (tests/__init__.py) rather than
# monkeypatching in-process, and why this wrapper isolates an invocation and never an
# in-process `import sosfs`.
#
# Usage:
#   tools/temp_site.sh uv run sosfs --version
#   tools/temp_site.sh --keep sh -c 'uv run sosfs status; ls -R .'
#
# Options:
#   --keep   Leave the sandbox in place and print its path. For inspecting a state
#            database or a tier tree after a drive that failed.
#
# The command runs with its working directory inside the sandbox, which also isolates
# the local config file (sosfs reads ./.sosfs.toml). The sandbox is removed on every
# exit path unless --keep.
#
# Do NOT run pytest through this wrapper. `testpaths` resolves against a rootdir that
# only exists where pyproject.toml does, so pytest collects nothing and exits 5; and
# the scrub would strip SOSFS_TEST_MINIO_* out from under the integration suite. The
# pytest fixtures already provide their own isolation.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
repo=$(CDPATH='' cd -- "$here/.." && pwd -P)

keep=''
while [ $# -gt 0 ]; do
    case "$1" in
        --keep) keep=1; shift ;;
        --)     shift; break ;;
        -*)     echo "temp_site.sh: unknown option: $1" >&2; exit 2 ;;
        *)      break ;;
    esac
done
[ $# -gt 0 ] || { echo "usage: temp_site.sh [--keep] COMMAND [ARG...]" >&2; exit 2; }

site=$(mktemp -d "${TMPDIR:-/tmp}/sosfs-temp-site.XXXXXX")
if [ -n "$keep" ]; then
    echo "temp_site.sh: keeping site at $site" >&2
else
    trap 'rm -rf "$site"' EXIT INT TERM
fi

# A developer with SOSFS_LOGGING_LEVEL or SOSFS_CONFIG_FILE exported gets a drive that
# reads their settings and a green verify that proves nothing. The BRE stays portable:
# no \? or \+, which BSD sed does not honor and which would silently disable the whole
# scrub on macOS while every gate stayed green.
for name in $(env | sed -n 's/^\(SOSFS_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$name"
done

# Nothing SOSFS_*-prefixed is exported back. sosfs.core.config builds its configuration
# from Environ(prefix='SOSFS'), so a SOSFS_SANDBOX would appear as the config key
# `sandbox` — a phantom setting injected into the surface this wrapper exists to
# isolate. The sandbox is $PWD.
XDG_CONFIG_HOME="$site/config"
XDG_STATE_HOME="$site/state"
UV_PROJECT="$repo"     # `uv run` walks up from cwd; from the sandbox it cannot find the project
export XDG_CONFIG_HOME XDG_STATE_HOME UV_PROJECT
mkdir -p "$XDG_CONFIG_HOME" "$XDG_STATE_HOME"

cd "$site"
"$@"
