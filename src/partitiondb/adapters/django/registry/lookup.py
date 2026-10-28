"""Configured model lookups, with no database queries or stale model cache."""

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured


def _configured_model(setting, base):
    from partitiondb.adapters.django.configuration import get_config

    label = get_config()[setting]
    if not isinstance(label, str) or label.count(".") != 1:
        raise ImproperlyConfigured(f"PARTITIONDB.{setting} must be an app_label.ModelName")
    try:
        model = apps.get_model(label, require_ready=False)
    except (LookupError, ValueError) as error:
        raise ImproperlyConfigured(f"PARTITIONDB.{setting} does not name an installed model") from error
    if model is None or model._meta.abstract or not issubclass(model, base):
        raise ImproperlyConfigured(f"PARTITIONDB.{setting} must name a concrete {base.__name__} subclass")
    if model._meta.app_label not in get_config()["SHARED_APPS"]:
        raise ImproperlyConfigured(f"PARTITIONDB.{setting} must belong to a shared app")
    return model


def get_tenant_model():
    from partitiondb.adapters.django.registry.models import TenantBaseMixin

    model: TenantBaseMixin = _configured_model("TENANT_MODEL", TenantBaseMixin)
    return model


def get_domain_model():
    from partitiondb.adapters.django.registry.models import DomainModelMixin

    model: DomainModelMixin = _configured_model("DOMAIN_MODEL", DomainModelMixin)
    try:
        field = model._meta.get_field("tenant")
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured("DOMAIN_MODEL requires a tenant ForeignKey") from error
    if not field.many_to_one or field.remote_field.model is not get_tenant_model():
        raise ImproperlyConfigured("DOMAIN_MODEL.tenant must reference TENANT_MODEL")
    return model


def get_membership_model():
    from partitiondb.adapters.django.registry.models import MembershipModelMixin

    model: MembershipModelMixin = _configured_model("MEMBERSHIP_MODEL", MembershipModelMixin)
    try:
        field = model._meta.get_field("tenant")
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured("MEMBERSHIP_MODEL requires a tenant ForeignKey") from error
    if not field.many_to_one or field.remote_field.model is not get_tenant_model():
        raise ImproperlyConfigured("MEMBERSHIP_MODEL.tenant must reference TENANT_MODEL")
    return model
