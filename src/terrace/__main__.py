# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Entry-point for `python -m terrace`."""

# Standard libs
import sys

# Internal libs
from terrace import main

if __name__ == '__main__':
    sys.exit(main())
