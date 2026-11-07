"""Django Admin bridge to the locked core access recovery service."""

from django.core.exceptions import PermissionDenied, SuspiciousOperation

from partitiondb.access.recovery import RecoveryDecision


class DjangoAccessReconciliationService:
    """Explicit POST service; caller must configure trustworthy evidence/audit."""

    def __init__(self, recovery):
        self.recovery = recovery

    def __call__(self, request, tenant, *, confirmation):
        if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
            raise PermissionDenied
        if confirmation != tenant.id:
            raise SuspiciousOperation("Tenant confirmation mismatch")
        try:
            decision = RecoveryDecision(request.POST.get("decision"))
        except (ValueError, TypeError):
            raise SuspiciousOperation("Explicit recovery decision required") from None
        return self.recovery.reconcile(tenant.id, confirm=tenant, decision=decision)
