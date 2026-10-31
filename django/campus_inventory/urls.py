from django.urls import include, path
from apps.core.auth_views import api_auth, html_auth
from shared.master_views import api_resource, html_resource

urlpatterns = [
    path("", include("apps.attachments.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.core.urls")),
    path("api/v1/auth/<str:action>/", api_auth),
    path("auth/<str:action>/", html_auth),
    path("api/v1/master/<str:resource>/", api_resource),
    path("api/v1/master/<str:resource>/<int:pk>/", api_resource),
    path("api/v1/catalog/<str:resource>/", api_resource),
    path("api/v1/catalog/<str:resource>/<int:pk>/", api_resource),
    path("catalog/<str:resource>/", html_resource),
    path("catalog/<str:resource>/<str:action>/", html_resource),
    path("catalog/<str:resource>/<int:pk>/<str:action>/", html_resource),
    path("manage/<str:resource>/", html_resource),
    path("manage/<str:resource>/<str:action>/", html_resource),
    path("manage/<str:resource>/<int:pk>/<str:action>/", html_resource),
]
