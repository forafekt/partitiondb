"""Configured model lookups, with no database queries or stale model cache."""

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured

from .configuration import get_config


def _configured_model(setting, base):
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
    from .models import TenantMixin

    return _configured_model("TENANT_MODEL", TenantMixin)


def get_domain_model():
    from .models import DomainMixin

    model = _configured_model("DOMAIN_MODEL", DomainMixin)
    try:
        field = model._meta.get_field("tenant")
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured("DOMAIN_MODEL requires a tenant ForeignKey") from error
    if not field.many_to_one or field.remote_field.model is not get_tenant_model():
        raise ImproperlyConfigured("DOMAIN_MODEL.tenant must reference TENANT_MODEL")
    return model
