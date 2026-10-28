"""Scoped runtime credentials; never mutate settings.DATABASES or expose passwords."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from partitiondb.tenancy.identity import Tenant


@dataclass(frozen=True)
class TenantDatabaseCredentials:
    role: str
    password: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.role, str) or not self.role or not isinstance(self.password, str) or not self.password:
            raise ValueError("Nonempty runtime role and password are required")


_active = ContextVar("partitiondb_database_credentials", default=None)


@contextmanager
def database_credentials_scope(tenant: Tenant, credentials):
    token = _active.set((tenant, credentials))
    try:
        yield
    finally:
        _active.reset(token)


def current_database_credentials(tenant):
    active = _active.get()
    if active is None:
        return None
    if active[0] != tenant:
        raise RuntimeError("Runtime credentials crossed a tenant boundary")
    return active[1]
