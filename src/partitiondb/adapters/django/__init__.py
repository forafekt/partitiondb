"""Optional Django adapter public model-resolution helpers."""

from .model_registry import get_domain_model, get_tenant_model

__all__ = ["get_tenant_model", "get_domain_model"]
