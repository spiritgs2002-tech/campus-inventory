from django.urls import path
from .views import html_notifications, api_notification_read

urlpatterns = [
    path("s/notifications/", html_notifications),
    path("api/v1/notifications/<int:pk>/read/", api_notification_read),
]
