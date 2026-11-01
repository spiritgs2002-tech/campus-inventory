from django.urls import path
from . import views

urlpatterns = [
    path("s/repair/new/", views.html_new),
    path("s/repairs/", views.html_list),
    path("s/repairs/<int:pk>/", views.html_detail),
    path("manage/repairs/", views.html_list, {"admin": True}),
    path(
        "manage/repairs/<int:pk>/dispatch/",
        views.html_action,
        {"operation": "dispatch"},
    ),
    path("manage/repairs/<int:pk>/status/", views.html_action, {"operation": "status"}),
    path("api/v1/repair/", views.api_write),
    path("api/v1/repair/<int:pk>/", views.api_detail),
    path(
        "api/v1/repair/<int:pk>/dispatch/", views.api_write, {"operation": "dispatch"}
    ),
    path("api/v1/repair/<int:pk>/status/", views.api_write, {"operation": "status"}),
]
