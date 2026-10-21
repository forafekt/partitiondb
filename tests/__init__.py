"""PartitionDB test suite; expose its nested Django apps for direct imports."""
import sys
from pathlib import Path

_PROJECT = str(Path(__file__).resolve().parent / "django_project")
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)
