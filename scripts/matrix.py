"""Run the supported Python/Django combinations through Docker Compose."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = [
    (python, django)
    for python in ("3.11", "3.12", "3.13", "3.14")
    for django in (">=5.2,<5.3", ">=6.0,<6.1", ">=6.1,<6.2")
    if python != "3.11" or django == ">=5.2,<5.3"
]


def main():
    failures = []
    for python, django in MATRIX:
        print(f"Matrix: Python {python}, Django {django}", flush=True)
        environment = dict(os.environ, PARTITIONDB_PYTHON=python, PARTITIONDB_DJANGO=django)
        result = subprocess.run(["docker", "compose", "run", "--rm", "validation"], cwd=ROOT, env=environment)
        if result.returncode:
            failures.append((python, django))
    if failures:
        print("Failed combinations:", failures, flush=True)
        return 1
    print(f"MATRIX PASSED: {len(MATRIX)} combinations", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
