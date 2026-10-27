"""Validate automatic database targets without opening any connection."""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from partitiondb.adapters.django.configuration import get_config
from partitiondb.postgres.databases import database_name


def database_provisioning_targets():
    config = get_config()
    values = config["DATABASE_PROVISIONING"]
    if not isinstance(values, Mapping):
        raise ImproperlyConfigured("DATABASE_PROVISIONING must map aliases to maintenance database options")
    targets = {}
    for alias, options in values.items():
        if not isinstance(alias, str) or alias == "default" or alias not in connections:
            raise ImproperlyConfigured("Automatic database targets must be configured non-default aliases")
        if not isinstance(options, Mapping) or set(options) != {"maintenance_database"}:
            raise ImproperlyConfigured("Each database target requires only maintenance_database")
        maintenance = options["maintenance_database"]
        name = connections[alias].settings_dict["NAME"]
        try:
            database_name(name)
        except ValueError as error:
            raise ImproperlyConfigured("Automatic target NAME must be a non-system PostgreSQL database name") from error
        if (
            not isinstance(maintenance, str)
            or not maintenance
            or "\x00" in maintenance
            or len(maintenance.encode("utf-8")) > 63
            or maintenance == name
        ):
            raise ImproperlyConfigured("Use a separate, existing maintenance database for each automatic target")
        targets[alias] = maintenance
    if targets and (not config["TENANT_MODEL"] or "partitiondb_access_control" not in config["SHARED_APPS"]):
        raise ImproperlyConfigured("Automatic databases require TENANT_MODEL and the shared access-control app")
    return targets
