"""Application-owned shared account endpoints on registered tenant hosts."""

from apps.accounts.views import session_info
from django.urls import path
from django.views.generic import RedirectView

urlpatterns = [
    path("api/auth/session/", session_info, name="account_session"),
    path("accounts/login/", RedirectView.as_view(url="/admin/login/", query_string=True)),
]
