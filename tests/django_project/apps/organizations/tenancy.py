from partitiondb.adapters.django.registry.lookup import get_membership_model


def authorize_tenant(request, tenant):
    principal = request.principal

    model = get_membership_model()

    return model.objects.authorize_tenant(principal=principal, tenant=tenant)


def tenant_admin_permissions(request, tenant):
    principal = request.principal

    model = get_membership_model()

    return model.objects.tenant_admin_permissions(
        models=[("workspaces", "workspace"), ("audit", "adminaudit")],
        principal=principal,
        tenant=tenant,
    )
