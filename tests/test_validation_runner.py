import subprocess

import pytest

from scripts import validate


class Admin:
    def __init__(self, fail_second_create=False):
        self.statements = []
        self.fail_second_create = fail_second_create

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement):
        query = statement.as_string()
        self.statements.append(query)
        if self.fail_second_create and query.startswith('CREATE DATABASE "pdb_validation_tests'):
            raise validate.psycopg.OperationalError("simulated creation failure")


def test_failed_child_cleans_only_owned_databases(monkeypatch):
    admin = Admin()
    monkeypatch.setattr(validate.psycopg, "connect", lambda *a, **kw: admin)
    monkeypatch.setattr(validate.secrets, "token_hex", lambda n: "fixed")

    def fail(arguments, environment):
        assert "pdb_validation_tests_fixed" in environment["PARTITIONDB_TEST_DSN"]
        raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(validate, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        validate.validate("dbname=original")
    drops = [query for query in admin.statements if query.startswith("DROP")]
    assert drops == ['DROP DATABASE "pdb_validation_tests_fixed"']
    assert not any('"original"' in query for query in admin.statements)


def test_partial_creation_cleans_only_successful_creations(monkeypatch):
    admin = Admin(fail_second_create=True)
    monkeypatch.setattr(validate.psycopg, "connect", lambda *a, **kw: admin)
    monkeypatch.setattr(validate.secrets, "token_hex", lambda n: "fixed")
    with pytest.raises(validate.psycopg.OperationalError):
        validate.validate("dbname=original")
    assert [query for query in admin.statements if query.startswith("DROP")] == []


def test_installed_validation_checks_origin_and_disables_source_pythonpath(monkeypatch):
    admin = Admin()
    monkeypatch.setattr(validate.psycopg, "connect", lambda *a, **kw: admin)
    calls = []
    monkeypatch.setattr(validate, "run", lambda arguments, environment: calls.append(arguments))
    validate.validate("dbname=original", installed_package=True)
    assert calls[0][0] == "-c"
    assert "Imported checkout source" in calls[0][1]
    assert "== expected" in calls[0][1]
    assert calls[1][-3:] == ["--override-ini", "pythonpath=.", "--import-mode=importlib"]


def test_validation_cli_accepts_installed_package_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(validate, "validate", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setenv("PARTITIONDB_TEST_DSN", "dbname=disposable")
    monkeypatch.setattr(validate.sys, "argv", ["validate.py", "--installed-package"])
    validate.main()
    assert calls == [(("dbname=disposable",), {"installed_package": True})]
