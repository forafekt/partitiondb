"""Platform routes: shared organizations, users and memberships."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

admin.site.site_header = "Root Administration"
admin.site.site_title = "Platform"
admin.site.index_title = "Platform administration"


def healthz(request):
    return JsonResponse(
        {
            "status": "ok",
            "service": "django project",
        }
    )


urlpatterns = [
    path("", include("config.urls_auth")),
    path("healthz", healthz),
    path("admin/", admin.site.urls),
]
