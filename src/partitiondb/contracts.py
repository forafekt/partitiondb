"""Framework-neutral integration contracts; no global mutable registry."""

from collections.abc import Iterable
from typing import Protocol

from .core import Tenant


class TenantResolver(Protocol):
    """Implement lookup in a trusted control plane, including active-state checks."""

    def __call__(self, hostname: str, /) -> Tenant | None: ...


class TenantRepository(Protocol):
    """Storage-neutral tenant inventory for provisioning orchestration."""

    def get(self, tenant_id: str, /) -> Tenant | None: ...
    def list_active(self) -> Iterable[Tenant]: ...


class SchemaLifecycle(Protocol):
    def create(self, schema: str) -> None: ...
    def drop(self, schema: str, *, confirm: str, cascade: bool = False) -> None: ...
    def rename(self, schema: str, target: str) -> None: ...
