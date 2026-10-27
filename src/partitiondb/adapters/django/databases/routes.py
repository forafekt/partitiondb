"""Persisted tenant database names over trusted static server profiles.

No endpoint/password comes from a tenant request. Hydration only changes this
process's connection definitions; it creates no physical database. Every lookup
rechecks the central binding and profile before using an alias, including after
restart. Route reservation and tenant/domain registration commit together.
"""

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from partitiondb import current_tenant
from partitiondb.adapters.django.configuration import get_config
from partitiondb.postgres.databases import database_name

PREFIX = "pdb_db_"
NEW_DATABASE = "__new_database__"
_route_lock = RLock()


@dataclass(frozen=True)
class DatabaseProfile:
    name: str
    source_alias: str
    maintenance_database: str
    fingerprint: str
    cluster_id: str = ""


def validate_new_database_name(value):
    database_name(value)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) or value.startswith("pg_"):
        raise ValueError("Use a lowercase database name with letters, numbers and underscores")
    return value


def managed_alias(alias):
    return isinstance(alias, str) and alias.startswith(PREFIX)


def database_profiles(*, include_registered=True, include_disabled=False):
    config = get_config()
    values = config["DATABASE_PROFILES"]
    managed = config["TENANT_MODEL"] and "partitiondb_access_control" in config["SHARED_APPS"]
    if values is None:
        values = {"default": {"database_alias": "default"}} if managed else {}
    if not isinstance(values, Mapping):
        raise ImproperlyConfigured("DATABASE_PROFILES must map names to configured server profiles")
    if values and not managed:
        raise ImproperlyConfigured("Database profiles require managed tenant models and the shared access-control app")
    profiles = {}
    for name, options in values.items():
        if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ImproperlyConfigured("Use bounded alphanumeric database profile names")
        if not isinstance(options, Mapping) or set(options) - {"database_alias", "maintenance_database"}:
            raise ImproperlyConfigured("Database profiles accept database_alias and optional maintenance_database")
        source = options.get("database_alias")
        if not isinstance(source, str) or managed_alias(source) or source not in connections:
            raise ImproperlyConfigured("Each database profile must select a static database alias")
        definition = connections[source].settings_dict
        options_values = definition.get("OPTIONS", {})
        if not isinstance(options_values, Mapping) or options_values.keys() & {
            "dbname",
            "database",
            "service",
            "conninfo",
            "host",
            "hostaddr",
            "port",
        }:
            raise ImproperlyConfigured(
                "Database profiles must declare routing in static connection fields, not OPTIONS"
            )
        maintenance = options.get("maintenance_database", definition["NAME"])
        if (
            not isinstance(maintenance, str)
            or not maintenance
            or "\x00" in maintenance
            or len(maintenance.encode()) > 63
        ):
            raise ImproperlyConfigured("A server profile requires an existing maintenance database name")
        # Deliberately exclude usernames, passwords and TLS private material.
        identity = {key: str(definition.get(key, "")) for key in ("ENGINE", "HOST", "PORT", "NAME")}
        identity.update(source_alias=source, maintenance_database=maintenance)
        fingerprint = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        profiles[name] = DatabaseProfile(name, source, maintenance, fingerprint)
    registry = config["DATABASE_PROFILE_REGISTRY"]
    if type(registry) is not bool or (registry and not managed):
        raise ImproperlyConfigured("DATABASE_PROFILE_REGISTRY requires managed shared access-control models")
    if registry and include_registered:
        if current_tenant(required=False) is not None:
            raise RuntimeError("Resolve server profiles outside tenant scope")
        from partitiondb.adapters.django.access_control.models import DatabaseServerProfile

        rows = DatabaseServerProfile._base_manager.using("default").all()
        registered = {}
        for row in rows:
            if not row.verified_at or (not row.enabled and not include_disabled):
                continue
            source = profiles.get(row.source_profile)
            if source is None or source.fingerprint != row.source_fingerprint:
                raise ImproperlyConfigured("A registered server source changed; verify its original identity")
            registered[row.name] = DatabaseProfile(
                row.name, source.source_alias, source.maintenance_database, source.fingerprint, row.cluster_id
            )
        return registered
    return profiles


def _target(alias, *, tenant_id=None, allow_unbound=False):
    if current_tenant(required=False) is not None:
        raise RuntimeError("Resolve managed database routes outside tenant scope")
    if alias in settings.DATABASES:
        raise ImproperlyConfigured("The pdb_db_ alias namespace is reserved for persisted library routes")
    from partitiondb.adapters.django.access_control.models import TenantDatabaseTarget

    row = TenantDatabaseTarget._base_manager.using("default").filter(alias=alias).first()
    if row is None:
        raise ValueError("No persisted database route exists for this alias")
    if allow_unbound and tenant_id is None and row.tenant_id:
        raise ValueError("This managed database route is already bound")
    if not managed_alias(alias) or (not row.tenant_id and not allow_unbound):
        raise ValueError("Managed database route is not bound to a tenant")
    if tenant_id is not None and row.tenant_id != str(tenant_id):
        raise ValueError("Managed database route belongs to another tenant")
    profiles = database_profiles(include_disabled=True)
    profile = profiles.get(row.profile)
    if profile is None or profile.fingerprint != row.profile_fingerprint:
        raise ImproperlyConfigured("Managed database server profile changed; explicit relocation is required")
    validate_new_database_name(row.database_name)
    return row, profile


def ensure_database_alias(alias, *, tenant_id=None, allow_unbound=False):
    if not managed_alias(alias):
        if alias not in connections:
            raise ValueError("Use a configured database alias")
        return
    row, profile = _target(alias, tenant_id=tenant_id, allow_unbound=allow_unbound)
    definition = deepcopy(connections[profile.source_alias].settings_dict)
    definition.update(NAME=row.database_name, CONN_MAX_AGE=0, ATOMIC_REQUESTS=False)
    with _route_lock:
        # Public initialized-only inspection avoids creating every alias wrapper
        # while resolving an inventory. Existing thread wrappers must not retain
        # old credentials after a profile credential update.
        for wrapper in connections.all(initialized_only=True):
            if wrapper.alias == alias and wrapper.settings_dict != definition:
                if wrapper.in_atomic_block or (wrapper.connection is not None and not wrapper.autocommit):
                    raise RuntimeError("Cannot replace an active managed database connection")
                wrapper.close()
                del connections[alias]
        if connections.settings.get(alias) != definition:
            # Copy-on-write: Django may iterate definitions in another thread.
            # Never mutate that dictionary while a reader is iterating it, and
            # never write generated aliases into the user's settings.DATABASES.
            connections.settings = {**connections.settings, alias: definition}


def reserve_database_target(*, profile_name=None, name=None, schema):
    """Called inside the shared tenant-registration transaction; no external DDL."""
    from partitiondb.adapters.django.access_control.models import TenantDatabaseTarget

    if current_tenant(required=False) is not None or not connections["default"].in_atomic_block:
        raise RuntimeError("Reserve tenant database routes inside the shared registration transaction")
    profiles = database_profiles()
    chosen = profile_name or ("default" if "default" in profiles else next(iter(profiles), None))
    if chosen not in profiles:
        raise ValueError("Select an available database server profile")
    token = uuid4().hex
    name = validate_new_database_name(name or f"pdb_{schema[:40]}_{token[:12]}")
    profile = profiles[chosen]
    alias = PREFIX + token
    if alias in connections:
        raise ValueError("Generated database alias is already reserved")
    row = TenantDatabaseTarget._base_manager.using("default").create(
        alias=alias,
        profile=chosen,
        database_name=name,
        profile_fingerprint=profile.fingerprint,
    )
    ensure_database_alias(row.alias, allow_unbound=True)
    return row


def release_database_alias(alias):
    """Release this thread's transient wrapper, retaining its routing definition."""
    if not managed_alias(alias):
        return
    with _route_lock:
        for wrapper in connections.all(initialized_only=True):
            if wrapper.alias == alias:
                if wrapper.in_atomic_block:
                    raise RuntimeError("Cannot release an active managed database transaction")
                wrapper.close()
                del connections[alias]


def discard_database_alias(alias):
    """Forget an uncommitted registration, without touching another route."""
    if not managed_alias(alias):
        return
    with _route_lock:
        release_database_alias(alias)
        connections.settings = {key: value for key, value in connections.settings.items() if key != alias}


def automatic_database(alias):
    if managed_alias(alias):
        ensure_database_alias(alias)
        return True
    from partitiondb.adapters.django.databases.settings import database_provisioning_targets

    return alias in database_provisioning_targets()


def maintenance_database(alias):
    if managed_alias(alias):
        _, profile = _target(alias)
        return profile.maintenance_database
    from partitiondb.adapters.django.databases.settings import database_provisioning_targets

    targets = database_provisioning_targets()
    if alias not in targets:
        raise ValueError("Automatic database provisioning is not enabled for this alias")
    return targets[alias]


def connection_source(alias):
    if managed_alias(alias):
        row, profile = _target(alias)
        return profile.source_alias, row.database_name
    return alias, None


def tenant_database_aliases():
    """Static tenant targets, excluding operator-only maintenance connections."""
    excluded = get_config()["DATABASE_SERVER_ONLY_ALIASES"]
    if not isinstance(excluded, (list, tuple)) or any(
        not isinstance(alias, str) or alias not in connections or alias == "default" for alias in excluded
    ):
        raise ImproperlyConfigured("DATABASE_SERVER_ONLY_ALIASES must list configured non-default aliases")
    return [alias for alias in connections if not managed_alias(alias) and alias not in excluded]
