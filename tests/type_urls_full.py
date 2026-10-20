from django.http import HttpResponse
from django.urls import path

urlpatterns = [path("full/", lambda request: HttpResponse("full"), name="typed")]
