from django.http import HttpResponse
from django.urls import path

urlpatterns = [path("empty/", lambda request: HttpResponse("empty"), name="typed")]
