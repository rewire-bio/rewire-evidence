"""Test configuration shared by every module."""

from __future__ import annotations

import os

from hypothesis import settings

# The store properties write real files, so a time limit per example measures the disk and
# not the code. In CI the examples are derived from the test itself, so a red run can be
# reproduced by running the same commit again.
settings.register_profile("default", deadline=None)
settings.register_profile("ci", deadline=None, derandomize=True, print_blob=True)
settings.load_profile("ci" if os.environ.get("CI") else "default")
