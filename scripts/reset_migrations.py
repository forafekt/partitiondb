"""Preview/delete repository-owned migration files; never touches dependencies."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete the listed migration files")
    parser.add_argument(
        "--regenerate", action="store_true", help="Generate fresh initial migrations using the example project"
    )
    args = parser.parse_args()
    if args.regenerate and not args.apply:
        parser.error("--regenerate requires --apply")
    root = Path(__file__).resolve().parents[1]
    files = []
    for directory in (root / "src/partitiondb", root / "tests/django_project/apps"):
        for path in directory.rglob("migrations/*"):
            if path.is_file() and not path.is_symlink() and path.suffix == ".py" and path.name != "__init__.py":
                files.append(path)
        for path in directory.rglob("migrations/__pycache__/*.pyc"):
            if path.is_file() and not path.is_symlink():
                files.append(path)
    for path in sorted(files):
        print(path.relative_to(root))
    print(f"{len(files)} files; " + ("deleting" if args.apply else "preview only (use --apply)"))
    if args.apply:
        for path in files:
            path.unlink()

    if args.regenerate:
        import os
        import sys

        sys.path.insert(0, str(root / "src"))
        sys.path.insert(0, str(root / "tests/django_project"))
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
        import django
        from django.conf import settings
        from django.core.management import call_command

        # Generate without connecting to a database or registering admin sites.
        # settings.INSTALLED_APPS = [app for app in settings.INSTALLED_APPS if "admin" not in app] + [
        #     "partitiondb.adapters.django.registry_app.apps.PartitionDBRegistryConfig"
        # ]
        settings.INSTALLED_APPS = [app for app in settings.INSTALLED_APPS if "admin" not in app]
        settings.DATABASES = {}
        django.setup()
        call_command(
            "makemigrations",
            "accounts",
            "organizations",
            "workspaces",
            "audit",
            "partitiondb_access_control",
            "partitiondb_registry",
            skip_checks=True,
        )


if __name__ == "__main__":
    main()
