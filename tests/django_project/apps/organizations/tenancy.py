from .models import Membership


def authorize_tenant(request, tenant):
    principal = request.principal
    return bool(
        principal.is_authenticated
        and Membership.objects.filter(
            user_id=principal.id,
            organization_id=tenant.id,
            active=True,
        ).exists()
    )


def tenant_admin_permissions(request, tenant):
    principal = request.principal
    if not principal.is_authenticated:
        return set()

    membership = Membership.objects.filter(
        user_id=principal.id,
        organization_id=tenant.id,
        active=True,
    ).first()

    if membership is None:
        return set()

    actions = {
        "admin": ("view", "add", "change", "delete"),
        "editor": ("view", "add", "change"),
        "viewer": ("view",),
    }.get(membership.role, ())

    permissions = {f"workspaces.{action}_workspace" for action in actions}

    # if membership.role == "admin":
    #     permissions.add("audit.view_adminaudit")

    return permissions
