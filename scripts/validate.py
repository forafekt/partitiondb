"""Run database tests in a fresh, uniquely named disposable database.

The configured admin connection must have CREATEDB. Only databases created by
this invocation are dropped. Never use application/production credentials here.
"""

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

import django
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]

INSTALLED_PACKAGE_PROBE = (
    "from pathlib import Path; import partitiondb; "
    "from importlib.metadata import version; import tomllib; "
    "expected = tomllib.loads(Path('pyproject.toml').read_text())"
    "['project']['version']; "
    "assert not Path(partitiondb.__file__).resolve().is_relative_to("
    "Path.cwd() / 'src'), 'Imported checkout source'; "
    "assert partitiondb.__version__ == version('partitiondb') == expected; "
    "print('INSTALLED PACKAGE:', partitiondb.__version__)"
)


def run(arguments, environment):
    print("Running:", " ".join(arguments), flush=True)
    subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment, check=True)


def validate(admin_dsn, *, installed_package=False):
    created = []
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        try:
            name = "pdb_validation_tests_" + secrets.token_hex(8)
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created.append(name)
            environment = os.environ.copy()
            environment["PARTITIONDB_TEST_DSN"] = make_conninfo(admin_dsn, dbname=name)
            arguments = ["-m", "pytest", "--require-postgres", "-p", "no:cacheprovider"]
            if installed_package:
                run(["-c", INSTALLED_PACKAGE_PROBE], environment)
                arguments.extend(["--override-ini", "pythonpath=.", "--import-mode=importlib"])
            run(arguments, environment)
        finally:
            # Cleanup all owned databases, even if one DROP fails. Failed names
            # are safe to report; no DSNs/passwords are included in this message.
            failures = []
            for name in reversed(created):
                try:
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                except psycopg.Error:
                    failures.append(name)
            if failures:
                raise RuntimeError("Cleanup required for validation databases: " + ", ".join(failures))
    print(f"VALIDATION PASSED: Python {sys.version.split()[0]}, Django {django.get_version()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--installed-package",
        action="store_true",
        help="Reject checkout imports and test the installed distribution",
    )
    options = parser.parse_args()
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        parser.error("PARTITIONDB_TEST_DSN must point to a disposable validation server")
    validate(dsn, installed_package=options.installed_package)


if __name__ == "__main__":
    main()
