"""Registry acceptance runs separately from the manual-adapter test app registry."""

import subprocess
import sys


def test_server_registration_requires_verification_and_preserves_cluster_identity():
    subprocess.run([sys.executable, "-m", "tests.server_profiles_scenario"], check=True, timeout=45)
