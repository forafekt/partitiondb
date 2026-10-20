from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import Note


@require_GET
def notes(request):
    return JsonResponse(
        {"tenant": request.tenant.id, "notes": list(Note.objects.values("id", "body"))}
    )
