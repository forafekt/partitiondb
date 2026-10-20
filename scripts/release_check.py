"""Run bounded technical release gates and retain reproducible evidence; never publish."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    "dist",
    "legacy",
    "release_reports",
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}


def source_fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if EXCLUDED.intersection(relative.parts) or any(
            p.endswith(".egg-info") for p in relative.parts
        ):
            continue
        if path.is_symlink():
            raise ValueError("Release inputs must not contain symlinks")
        if path.is_file():
            name = relative.as_posix().encode()
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def gates(report_dir):
    python = sys.executable
    return [
        ("lint", [python, "-m", "ruff", "check", "."]),
        ("format", [python, "-m", "ruff", "format", "--check", "."]),
        ("typing", [python, "-m", "mypy"]),
        ("build", [python, "-m", "build", "--outdir", str(report_dir / "artifacts")]),
        (
            "artifacts",
            [python, "scripts/check_artifacts.py", "--dist-dir", str(report_dir / "artifacts")],
        ),
        ("matrix", [python, "scripts/matrix.py"]),
        ("postgres_acceptance", ["docker", "compose", "run", "--rm", "--build", "acceptance"]),
        ("celery_acceptance", ["docker", "compose", "run", "--rm", "celery_acceptance"]),
    ]


def run_gate(name, command, report_dir):
    log_path = report_dir / f"{name}.log"
    print(f"Running {name}; log: {log_path}", flush=True)
    with log_path.open("wb") as output:
        try:
            environment = dict(os.environ)
            # Every matrix container installs this exact built wheel from the
            # existing read-only /package mount, rather than rebuilding source.
            if name in {"matrix", "postgres_acceptance", "celery_acceptance"}:
                wheel = next((report_dir / "artifacts").glob("*.whl"), None)
                if wheel is None:
                    return {
                        "gate": name,
                        "status": "failed",
                        "error_type": "MissingWheel",
                        "log": log_path.name,
                    }
                environment["PARTITIONDB_PACKAGE"] = (
                    "/package/" + wheel.relative_to(ROOT).as_posix()
                )
            result = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT
            )
            code = result.returncode
            record = {
                "gate": name,
                "status": "passed" if code == 0 else "failed",
                "returncode": code,
            }
        except OSError as error:
            record = {"gate": name, "status": "failed", "error_type": type(error).__name__}
    record["log"] = log_path.name
    print(f"{name}: {record['status']}", flush=True)
    return record


def main():
    started = datetime.now(UTC)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    report_dir = (
        ROOT / "release_reports" / (started.strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(4))
    )
    report_dir.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "version": version,
        "started_utc": started.isoformat(),
        "source_before": source_fingerprint(ROOT),
        "gates": [],
        "artifacts": [],
        "technical_passed": False,
        "publication_ready": False,
        "remaining_review": [
            "final_artifact_review",
            "release_approval",
        ],
    }
    interrupted = False
    try:
        for name, command in gates(report_dir):
            report["gates"].append(run_gate(name, command, report_dir))
    except KeyboardInterrupt:
        interrupted = True
        report["interrupted"] = True
    finally:
        report["source_after"] = source_fingerprint(ROOT)
        for path in sorted((report_dir / "artifacts").glob("*")):
            if path.is_file():
                report["artifacts"].append(
                    {
                        "name": path.name,
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        report["technical_passed"] = (
            not interrupted
            and len(report["gates"]) == len(gates(report_dir))
            and all(gate["status"] == "passed" for gate in report["gates"])
            and report["source_before"] == report["source_after"]
            and {a["name"] for a in report["artifacts"]}
            == {f"partitiondb-{version}-py3-none-any.whl", f"partitiondb-{version}.tar.gz"}
        )
        report["finished_utc"] = datetime.now(UTC).isoformat()
        destination = report_dir / "report.json"
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Release evidence: {destination}", flush=True)
    print(
        "TECHNICAL RELEASE GATES PASSED"
        if report["technical_passed"]
        else "RELEASE GATES INCOMPLETE"
    )
    print("Publication requires final artifact review and release approval; no upload performed.")
    return 130 if interrupted else 0 if report["technical_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
