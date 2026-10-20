"""Materialize shared Django identity before entering a tenant transaction."""

from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

from partitiondb import current_tenant


@dataclass(frozen=True, slots=True)
class Principal:
    id: str | None
    username: str
    permissions: frozenset[str]
    is_staff: bool = False
    is_superuser: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self.id is not None

    def has_perm(self, permission: str) -> bool:
        return self.is_authenticated and permission in self.permissions


class SharedIdentityMiddleware:
    """After Django sessions/authentication, before TenantMiddleware.

    Identity and global permissions are snapshots, not organization membership.
    Configure the tenant AUTHORIZE callback to check membership separately.
    Shared auth mutations (login/logout/password changes) belong to control-plane
    endpoints outside TenantMiddleware. Views should consume request.principal.
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if current_tenant(required=False) is not None:
            raise ImproperlyConfigured("SharedIdentityMiddleware must run outside tenant scope")
        if not hasattr(request, "user"):
            raise ImproperlyConfigured("Install AuthenticationMiddleware before shared identity")
        user = request.user
        if user.is_authenticated and user.is_active:
            request.principal = Principal(
                id=str(user.pk),
                username=user.get_username(),
                permissions=frozenset(user.get_all_permissions()),
                is_staff=bool(user.is_staff),
                is_superuser=bool(user.is_superuser),
            )
        else:
            request.principal = Principal(None, "", frozenset())
        return self.get_response(request)
