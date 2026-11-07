"""Architectural contracts: framework-independent core and direct feature imports."""

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"


def test_core_features_do_not_depend_on_framework_adapters():
    for feature in ("tenancy", "access", "credentials", "provisioning", "postgres"):
        for path in (ROOT / "partitiondb" / feature).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                else:
                    continue
                assert not any(
                    name == "django" or name.startswith(("django.", "partitiondb.adapters")) for name in imports
                ), f"Framework dependency in {path}:{node.lineno}"


def test_framework_independent_features_work_without_optional_dependencies():
    # -S removes site packages, including Django/psycopg. Exercise actual behavior
    # so an accidentally eager optional import fails in a fresh interpreter.
    script = """
import sys, tempfile
from pathlib import Path
sys.path.insert(0, ROOT)
from partitiondb import Tenant, tenant_scope
from partitiondb.access.membership import role_actions
from partitiondb.credentials import CredentialProvider
from partitiondb.credentials.development import DevelopmentCredentials
from partitiondb.credentials.openbao import OpenBaoKV2
from partitiondb.credentials.runtime import (
    TenantDatabaseCredentials, database_credentials_scope, current_database_credentials,
)
from partitiondb.tenancy.assets import tenant_path
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning.databases import DatabaseProvisioner
first, second = Tenant("one", "alpha"), Tenant("two", "beta")
secret = TenantDatabaseCredentials("pdb_example", "private")
with tempfile.TemporaryDirectory() as directory:
    provider = DevelopmentCredentials(Path(directory) / "secrets")
    provider.put("example", "private")
    assert provider.get("example") == "private"
    with tenant_scope(first), database_credentials_scope(first, secret):
        assert current_database_credentials(first) is secret
        try:
            current_database_credentials(second)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Credentials crossed tenant boundary")
    assert current_database_credentials(first) is None
    assert tenant_path(directory, first, "file.txt") != tenant_path(directory, second, "file.txt")
    assert role_actions("viewer") == ("view",)
assert "django" not in sys.modules and "psycopg" not in sys.modules
""".replace("ROOT", json.dumps(str(ROOT)))
    result = subprocess.run([sys.executable, "-S", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_paths_do_not_use_compatibility_search_directories():
    import partitiondb
    import partitiondb.adapters.django

    for package in (partitiondb, partitiondb.adapters.django):
        assert list(package.__path__) == [str(Path(package.__file__).parent)]
    assert not (ROOT / "partitiondb" / "_compat").exists()


def test_control_plane_apps_have_one_canonical_implementation():
    adapter = ROOT / "partitiondb" / "adapters" / "django"
    for obsolete in ("registry", "registry_app", "databases", "access_control"):
        assert not (adapter / obsolete).exists()
    for feature in ("tenancy", "infrastructure", "database_access"):
        assert (adapter / feature / "models.py").is_file()
        assert (adapter / feature / "migrations" / "0001_initial.py").is_file()


def test_admin_templates_use_no_partitiondb_css_or_theme_specific_dependencies():
    adapter = ROOT / "partitiondb" / "adapters" / "django"
    assert not list((adapter / "static").rglob("*.css"))
    for template in (adapter / "templates" / "partitiondb" / "admin").glob("*.html"):
        source = template.read_text()
        for forbidden in ("operations.css", "pdb-", "style=", "<style", "x-data", "dark:", "rounded-default"):
            assert forbidden not in source, template
