from django.urls import path
from .views import api_attachments, html_attachments

urlpatterns = [
    path("api/v1/attachments/", api_attachments),
    path("api/v1/attachments/import/", api_attachments, {"action": "import"}),
    path("api/v1/attachments/<int:pk>/", api_attachments),
    path("api/v1/attachments/<int:pk>/<str:action>/", api_attachments),
    path("api/v1/scrap/<int:scrap_id>/attachments/<int:pk>/", api_attachments),
    path("attachments/", html_attachments),
    path(
        "attachments/<int:pk>/withdraw/<int:scrap_id>/",
        html_attachments,
        {"action": "withdraw"},
    ),
    path("attachments/<int:pk>/<str:action>/", html_attachments),
    path("attachments/<str:action>/", html_attachments),
]
