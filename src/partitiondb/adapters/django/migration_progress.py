"""Structured best-effort progress from Django's migration executor."""

import logging
from dataclasses import dataclass

from django.core.management.commands.migrate import Command as DjangoMigrateCommand

from partitiondb import Tenant


@dataclass(frozen=True, slots=True)
class MigrationProgress:
    tenant: Tenant
    action: str
    app_label: str | None = None
    migration_name: str | None = None
    fake: bool = False


class ObservedMigrateCommand(DjangoMigrateCommand):
    def __init__(self, tenant, observer, **kwargs):
        super().__init__(**kwargs)
        self.tenant = tenant
        self.observer = observer

    def migration_progress_callback(self, action, migration=None, fake=False):
        super().migration_progress_callback(action, migration, fake)
        if self.observer is None:
            return
        event = MigrationProgress(
            self.tenant,
            action,
            migration.app_label if migration is not None else None,
            migration.name if migration is not None else None,
            fake,
        )
        try:
            self.observer(event)
        except Exception as error:
            logging.getLogger(__name__).warning("Migration progress observer failed: %s", type(error).__name__)
