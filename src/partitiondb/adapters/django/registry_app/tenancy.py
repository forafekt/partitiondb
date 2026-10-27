"""Membership callbacks for the configured shared registry and tenant apps."""

from django.apps import apps

from partitiondb.adapters.django.registry.lookup import get_membership_model
from partitiondb.adapters.django.routing.tenant_types import tenant_policy


def authorize_tenant(request, tenant):
    return get_membership_model().objects.authorize_tenant(principal=request.principal, tenant=tenant)


def tenant_admin_permissions(request, tenant):
    models = [
        (model._meta.app_label, model._meta.model_name)
        for label in tenant_policy(tenant).apps
        for model in apps.get_app_config(label).get_models()
    ]
    return get_membership_model().objects.tenant_admin_permissions(
        models=models, principal=request.principal, tenant=tenant
    )
