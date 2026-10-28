"""Require live services and test the installed release package against OpenBao."""

import os
import subprocess
import sys

from scripts.validate import INSTALLED_PACKAGE_PROBE, ROOT


def main():
    if not all(os.environ.get(key) for key in ("PARTITIONDB_TEST_DSN", "PARTITIONDB_OPENBAO_TEST_ADDR")):
        raise SystemExit("OpenBao acceptance requires both disposable service endpoints; skipping is not allowed")
    # A source import or wrong version must fail before any live-service mutation.
    probe = subprocess.run([sys.executable, "-c", INSTALLED_PACKAGE_PROBE], cwd=ROOT)
    if probe.returncode:
        return probe.returncode
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_openbao_live.py",
            "--require-postgres",
            "-p",
            "no:cacheprovider",
            "--override-ini",
            "pythonpath=.",
            "--import-mode=importlib",
            "-q",
        ],
        cwd=ROOT,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
