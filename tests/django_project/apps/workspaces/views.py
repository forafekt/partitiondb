from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import Workspace


@require_GET
def workspace_list(request):
    workspaces = list(
        Workspace.objects.values(
            "id",
            "name",
            "slug",
        )
    )

    return JsonResponse(
        {
            "organization_id": request.tenant.id,
            "schema": request.tenant.schema,
            "workspaces": workspaces,
        }
    )