"""Run control-plane overview acceptance in its own configured Django process."""

import os
import subprocess
import sys
from pathlib import Path


def test_tenant_overview_and_lifecycle_boundaries():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root / "src") + os.pathsep + str(root))
    result = subprocess.run(
        [sys.executable, "-m", "tests.tenant_overview_scenario"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
