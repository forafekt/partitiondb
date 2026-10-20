"""Pure Python primitives. Importing this module never loads a framework or driver."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z", re.ASCII)


class MissingTenant(RuntimeError):
    """An operation requiring explicit tenant identity has no active scope."""


def validate_tenant_type(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Tenant type must match [a-z][a-z0-9_]{0,62}")
    return value


def validate_schema(value: str, *, allow_public: bool = False) -> str:
    """Require canonical ASCII identifiers; never allow system schemas."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Schema must match [a-z][a-z0-9_]{0,62}")
    if value.startswith("pg_") or value == "information_schema":
        raise ValueError("System schemas cannot be tenant schemas")
    if value == "public" and not allow_public:
        raise ValueError("public is reserved for the control plane")
    return value


@dataclass(frozen=True, slots=True)
class Tenant:
    """Serializable routing identity; database is a preconfigured alias, never a DSN."""

    id: str
    schema: str
    database: str = "default"
    tenant_type: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        validate_schema(self.schema)
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Tenant id must be a nonempty string")
        if not isinstance(self.database, str) or not self.database:
            raise ValueError("Database alias must be a nonempty string")
        if self.tenant_type is not None:
            validate_tenant_type(self.tenant_type)


_active: ContextVar[Tenant | None] = ContextVar("partitiondb_tenant", default=None)


def current_tenant(*, required: bool = True) -> Tenant | None:
    tenant = _active.get()
    if tenant is None and required:
        raise MissingTenant("Enter tenant_scope() before accessing tenant data")
    return tenant


@contextmanager
def tenant_scope(tenant: Tenant | None) -> Iterator[None]:
    """Nest safely across exceptions, threads and asyncio tasks.

    None deliberately selects control-plane context. Spawned tasks inherit context;
    applications must finish/cancel child work before leaving a tenant boundary.
    """
    if tenant is not None and not isinstance(tenant, Tenant):
        raise TypeError("Expected a Tenant or None")
    token = _active.set(tenant)
    try:
        yield
    finally:
        _active.reset(token)
