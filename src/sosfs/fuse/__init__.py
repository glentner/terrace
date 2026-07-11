# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""FUSE layer for sosfs: the thin mfusepy adapter and mount plumbing (PLAN.md -> FUSE adapter).

Imports here stay lazy — nothing pulls mfusepy or libfuse until a mount actually
runs — so the engine and offline commands import cleanly on hosts without FUSE.
"""
