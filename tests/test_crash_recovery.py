import os
import subprocess
import sys

import pytest


@pytest.mark.postgres
def test_durable_provisioning_recovers_after_abrupt_process_exit():
    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("PARTITIONDB_TEST_DSN required")
    subprocess.run(
        [sys.executable, "-m", "tests.provisioning_crash_scenario"], check=True, timeout=120
    )
