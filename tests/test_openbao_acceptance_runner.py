from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import openbao_acceptance


def test_missing_service_endpoints_cannot_silently_skip_release_acceptance(monkeypatch):
    monkeypatch.delenv("PARTITIONDB_OPENBAO_TEST_ADDR", raising=False)
    monkeypatch.delenv("PARTITIONDB_TEST_DSN", raising=False)
    runner = Mock()
    monkeypatch.setattr(openbao_acceptance.subprocess, "run", runner)
    with pytest.raises(SystemExit, match="skipping is not allowed"):
        openbao_acceptance.main()
    runner.assert_not_called()


@pytest.mark.parametrize("probe_code", [0, 1])
def test_installed_package_verification_precedes_live_mutations(monkeypatch, probe_code):
    monkeypatch.setenv("PARTITIONDB_OPENBAO_TEST_ADDR", "http://127.0.0.1:8200")
    monkeypatch.setenv("PARTITIONDB_TEST_DSN", "disposable-test-endpoint")
    runner = Mock(side_effect=[SimpleNamespace(returncode=probe_code), SimpleNamespace(returncode=0)])
    monkeypatch.setattr(openbao_acceptance.subprocess, "run", runner)
    assert openbao_acceptance.main() == probe_code
    assert runner.call_args_list[0].args[0][1] == "-c"
    assert runner.call_count == (2 if probe_code == 0 else 1)
    if probe_code == 0:
        assert "--require-postgres" in runner.call_args_list[1].args[0]
        assert "pythonpath=." in runner.call_args_list[1].args[0]
