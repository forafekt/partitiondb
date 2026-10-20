"""Inspect built release artifacts and smoke-test the dependency-free wheel.

Run with the interpreter used to build the package. This does not upload files
or decide whether licensing and production acceptance requirements are met.
"""

import argparse
import email
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def check_names(names):
    forbidden = {"legacy", "__pycache__", ".git", ".venv", ".env"}
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or forbidden.intersection(path.parts):
            raise ValueError(f"Unexpected packaged path: {name}")
        if path.suffix in {".pyc", ".pem", ".key"}:
            raise ValueError(f"Unexpected packaged file: {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    options = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    wheel = options.dist_dir / f"partitiondb-{version}-py3-none-any.whl"
    source = options.dist_dir / f"partitiondb-{version}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        check_names(archive.namelist())
        metadata = email.message_from_bytes(
            archive.read(f"partitiondb-{version}.dist-info/METADATA")
        )
        if metadata["Name"] != "partitiondb" or metadata["Version"] != version:
            raise ValueError("Wheel metadata does not match project metadata")
        if metadata["License-Expression"] != "MIT":
            raise ValueError("Wheel must declare the MIT license")
        if set(metadata.get_all("License-File", [])) != {"LICENSE", "THIRD_PARTY_NOTICES"}:
            raise ValueError("Wheel must declare both project and upstream notices")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES"):
            packaged = archive.read(f"partitiondb-{version}.dist-info/licenses/{name}")
            if packaged != (ROOT / name).read_bytes():
                raise ValueError(f"Wheel license file differs from source: {name}")
        if any("extra ==" not in value for value in metadata.get_all("Requires-Dist", [])):
            raise ValueError("Core must not acquire mandatory runtime dependencies")
    with tarfile.open(source) as archive:
        check_names(archive.getnames())
        if any(member.issym() or member.islnk() for member in archive.getmembers()):
            raise ValueError("Source archive must not contain links")
        prefix = f"partitiondb-{version}/"
        stream = archive.extractfile(prefix + "PKG-INFO")
        if stream is None:
            raise ValueError("Source archive has no package metadata")
        source_metadata = email.message_from_bytes(stream.read())
        if source_metadata["License-Expression"] != "MIT":
            raise ValueError("Source archive must declare the MIT license")
        for name in ("README.md", "pyproject.toml", "docs/MIGRATION.md"):
            archive.getmember(prefix + name)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES"):
            stream = archive.extractfile(prefix + name)
            if stream is None or stream.read() != (ROOT / name).read_bytes():
                raise ValueError(f"Source archive license file differs: {name}")
    # -I -S excludes the checkout, user site and optional installed dependencies.
    # Import directly from the exact wheel; a source-tree import cannot pass this.
    smoke = """
import sys
sys.path.insert(0, sys.argv[1])
import partitiondb
from partitiondb import Tenant, current_tenant, tenant_scope
assert partitiondb.__version__ == sys.argv[2]
tenant = Tenant('artifact-check', 'artifact_check')
with tenant_scope(tenant):
    assert current_tenant() == tenant
assert current_tenant(required=False) is None
assert not any(name in sys.modules for name in ('django', 'psycopg', 'pytest', 'celery'))
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", smoke, str(wheel), version], check=True)
    print(f"ARTIFACT CHECKS PASSED: {version} wheel and source archive")


if __name__ == "__main__":
    main()
