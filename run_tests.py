#!/usr/bin/env python3 -I
"""Offline test runner: discovers tests/test_*.py and runs them via the shim."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.local-pytest-shim'))

import pytest  # noqa: E402

tests = sorted((ROOT / 'tests').glob('test_*.py'))
sys.exit(pytest.main(tests))
