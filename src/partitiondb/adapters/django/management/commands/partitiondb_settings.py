"""Inspect effective configuration without running provisioning or reading secrets."""

import json

from django.core.exceptions import ImproperlyConfigured
from django.core.management import BaseCommand, CommandError

from partitiondb.adapters.django.configuration import get_config


class Command(BaseCommand):
    help = "Show effective PartitionDB settings; --check validates the Django integration."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true")

    def handle(self, *args, **options):
        try:
            config = get_config()
            if options["check"]:
                self.check()

            def display(value):
                if callable(value):
                    module = getattr(value, "__module__", type(value).__module__)
                    name = getattr(value, "__qualname__", type(value).__qualname__)
                    return f"{module}.{name}"
                return str(value)

            self.stdout.write(json.dumps(dict(config), default=display, indent=2, sort_keys=True))
        except ImproperlyConfigured as error:
            raise CommandError(str(error)) from error
