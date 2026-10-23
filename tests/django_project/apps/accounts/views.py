from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


@never_cache
@ensure_csrf_cookie
@require_GET
def session_info(request):
    if not (request.user.is_authenticated and request.user.is_active):
        return JsonResponse({"authenticated": False}, status=401)
    user = request.user
    return JsonResponse(
        {
            "authenticated": True,
            "user": {
                "id": str(user.pk),
                "username": user.username,
                "email": user.email,
            },
        }
    )
