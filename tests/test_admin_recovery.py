"""Isolated native admin acceptance; no ActiveLane or Unfold is imported."""

import os
import subprocess
import sys
from pathlib import Path


def test_native_admin_access_recovery_without_host_app():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root / "src") + os.pathsep + str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, str(root / "tests/recovery_admin_scenario.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_database_recovery_postgres():
    import pytest

    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("requires disposable PARTITIONDB_TEST_DSN")
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root / "src") + os.pathsep + str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, str(root / "tests/database_admin_recovery_scenario.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
