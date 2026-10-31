from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("health/live/", views.health, name="health-live"),
    path("health/ready/", views.ready, name="health-ready"),
]
