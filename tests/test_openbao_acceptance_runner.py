import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import openbao_acceptance


def test_module_runner_uses_repository_scripts_over_installed_package(tmp_path):
    installed_scripts = tmp_path / "scripts"
    installed_scripts.mkdir()
    (installed_scripts / "__init__.py").write_text("", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    env.pop("PARTITIONDB_OPENBAO_TEST_ADDR", None)
    env.pop("PARTITIONDB_TEST_DSN", None)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.openbao_acceptance"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "skipping is not allowed" in result.stderr
    assert "No module named" not in result.stderr


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
