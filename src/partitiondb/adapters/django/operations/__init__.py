import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError

from partitiondb.adapters.django.operations.inventory import load_inventory
from partitiondb.provisioning.fleet import run_fleet


def run_processes(command, options, build_arguments):
    manage = Path(options["manage"]).resolve()
    if not manage.is_file():
        raise CommandError("--manage must identify the application's manage.py")
    environment = os.environ.copy()
    if getattr(settings, "SETTINGS_MODULE", None):
        environment["DJANGO_SETTINGS_MODULE"] = settings.SETTINGS_MODULE

    def operation(tenant):
        subprocess.run(
            [sys.executable, str(manage), *build_arguments(tenant)],
            env=environment,
            check=True,
        )

    results = run_fleet(
        load_inventory(),
        operation,
        workers=options["workers"],
        stop_on_error=not options["keep_going"],
    )
    for result in results:
        command.stdout.write(
            f"{result.tenant.id} {result.tenant.database}/{result.tenant.schema}: "
            f"{result.status} ({result.seconds:.3f}s)"
        )
    if any(result.status != "succeeded" for result in results):
        raise CommandError("Fleet operation incomplete; inspect tenant output and run summary")


def add_fleet_arguments(parser):
    parser.add_argument("--manage", default=sys.argv[0])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--keep-going", action="store_true")
