import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import release_check


def project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0a27"\n')
    (tmp_path / "code.py").write_text("original")
    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    return tmp_path


def test_fingerprint_tracks_inputs_but_excludes_reports_and_caches(tmp_path):
    code = tmp_path / "code.py"
    code.write_text("one")
    before = release_check.source_fingerprint(tmp_path)
    for name in ["release_reports", "dist", "__pycache__"]:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "generated").write_text("changes")
    assert release_check.source_fingerprint(tmp_path) == before
    code.write_text("two")
    assert release_check.source_fingerprint(tmp_path) != before


def test_fingerprint_rejects_linked_release_inputs(tmp_path):
    (tmp_path / "link").symlink_to("outside")
    with pytest.raises(ValueError, match="symlink"):
        release_check.source_fingerprint(tmp_path)


@pytest.mark.parametrize("fail,mutate", [(False, False), (True, False), (False, True)])
def test_report_requires_all_gates_and_unchanged_source(tmp_path, monkeypatch, fail, mutate):
    root = project(tmp_path, monkeypatch)
    monkeypatch.setattr(release_check, "gates", lambda directory: [("first", []), ("second", [])])
    seen = []

    def gate(name, command, directory):
        seen.append(name)
        artifacts = directory / "artifacts"
        artifacts.mkdir(exist_ok=True)
        for filename in ["partitiondb-0.2.0a27-py3-none-any.whl", "partitiondb-0.2.0a27.tar.gz"]:
            (artifacts / filename).write_bytes(b"test artifact")
        if mutate:
            (root / "code.py").write_text("changed during validation")
        return {"gate": name, "status": "failed" if fail and name == "first" else "passed"}

    monkeypatch.setattr(release_check, "run_gate", gate)
    result = release_check.main()
    report = json.loads(next(root.glob("release_reports/*/report.json")).read_text())
    assert seen == ["first", "second"]
    assert report["technical_passed"] is (not fail and not mutate)
    assert report["publication_ready"] is False
    assert len(report["artifacts"]) == 2
    assert all(len(item["sha256"]) == 64 for item in report["artifacts"])
    assert result == (0 if not fail and not mutate else 1)


def test_interruption_preserves_incomplete_evidence(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(release_check, "run_gate", interrupt)
    assert release_check.main() == 130
    report = json.loads(next(root.glob("release_reports/*/report.json")).read_text())
    assert report["interrupted"] is True and report["technical_passed"] is False


def test_gate_records_missing_command_without_exception_message(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("secret connection string")

    monkeypatch.setattr(release_check.subprocess, "run", missing)
    record = release_check.run_gate("lint", ["missing"], tmp_path)
    assert record["status"] == "failed" and record["error_type"] == "FileNotFoundError"
    assert "secret" not in repr(record)


def test_container_gate_requires_and_selects_exact_built_wheel(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    directory = root / "release_reports" / "test"
    directory.mkdir(parents=True)
    assert release_check.run_gate("matrix", [], directory)["error_type"] == "MissingWheel"
    artifact = directory / "artifacts" / "partitiondb-0.2.0a27-py3-none-any.whl"
    artifact.parent.mkdir()
    artifact.write_bytes(b"wheel")

    def run(command, **kwargs):
        assert (
            kwargs["env"]["PARTITIONDB_PACKAGE"]
            == "/package/" + artifact.relative_to(root).as_posix()
        )
        assert kwargs["cwd"] == root
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_check.subprocess, "run", run)
    assert release_check.run_gate("matrix", [], directory)["status"] == "passed"


def test_installed_package_probe_rejects_checkout_source(tmp_path):
    import os

    from scripts import validate

    package = tmp_path / "src" / "partitiondb"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '0.2.0a27'\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0a27"\n')
    environment = dict(os.environ, PYTHONPATH=str(tmp_path / "src"))
    result = subprocess.run(
        [release_check.sys.executable, "-c", validate.INSTALLED_PACKAGE_PROBE],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert b"Imported checkout source" in result.stderr
