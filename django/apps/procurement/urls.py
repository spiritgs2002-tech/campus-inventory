from django.urls import path
from . import views

urlpatterns = [
    path(
        "manage/requisitions/<int:pk>/decide/", views.html_action, {"action": "decide"}
    ),
    path(
        "manage/requisitions/<int:pk>/fulfill/",
        views.html_action,
        {"action": "fulfill"},
    ),
    path("s/requisition/new/", views.html_new),
    path("s/requisitions/", views.html_list),
    path("manage/requisitions/", views.html_list, {"admin": True}),
    path("api/v1/requisition/", views.api_write),
    path("api/v1/requisition/<int:pk>/decide/", views.api_write, {"action": "decide"}),
    path(
        "api/v1/requisition/<int:pk>/fulfill/", views.api_write, {"action": "fulfill"}
    ),
]
