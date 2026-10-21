from django.urls import path

from .views import workspace_list

app_name = "workspaces"

urlpatterns = [
    path("", workspace_list, name="list"),
]
