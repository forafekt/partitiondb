from django.urls import path
from projects.views import notes

urlpatterns = [path("notes/", notes)]
