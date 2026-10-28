"""Fail-closed app routing. Lists contain Django app labels, not dotted paths."""

from django.core.exceptions import ImproperlyConfigured

from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.routing.tenant_types import tenant_policy
from partitiondb.tenancy.identity import MissingTenant, current_tenant


def app_sets():
    config = get_config()
    shared, tenant = set(config["SHARED_APPS"]), set(config["TENANT_APPS"])
    if shared & tenant:
        raise ImproperlyConfigured("SHARED_APPS and TENANT_APPS must be disjoint")
    identity = current_tenant(required=False)
    if identity is not None:
        tenant = set(tenant_policy(identity).apps)
    return shared, tenant


class TenantRouter:
    def db_for_read(self, model, **hints):
        shared, tenant_apps = app_sets()
        label = model._meta.app_label
        tenant = current_tenant(required=False)
        if label in shared:
            if tenant is not None:
                raise ImproperlyConfigured("Query shared models outside tenant scope")
            return "default"
        if label in tenant_apps:
            if tenant is None:
                raise MissingTenant("Tenant model accessed without a tenant")
            return tenant.database
        raise ImproperlyConfigured(f"Unclassified application: {label}")

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        shared, tenant_apps = app_sets()
        label1, label2 = obj1._meta.app_label, obj2._meta.app_label
        if label1 in shared and label2 in shared:
            return obj1._state.db == obj2._state.db
        if label1 in tenant_apps and label2 in tenant_apps:
            tenant = current_tenant(required=False)
            return (
                tenant is not None
                and getattr(obj1, "_partitiondb_tenant", None) == tenant
                and getattr(obj2, "_partitiondb_tenant", None) == tenant
                and obj1._state.db == obj2._state.db
            )
        return False

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        shared, tenant_apps = app_sets()
        tenant = current_tenant(required=False)
        if tenant is None:
            return db == "default" and app_label in shared
        return db == tenant.database and app_label in tenant_apps
