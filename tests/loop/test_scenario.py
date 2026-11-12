# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The bundle loop, stage by stage, over the helper's own object store."""

# External libs
from pytest import mark

# Internal libs
from tests.loop.stages import (test_stage_1_reach, test_stage_2_read, test_stage_3_pressure,     # noqa: F401
                               test_stage_4_change, test_stage_5_reaggregate, test_stage_6_read_back,
                               test_stage_7_release, test_stage_8_offline_commit, test_stage_9_several_bundles)

pytestmark = mark.unit
