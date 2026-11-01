from .services import owned
from shared.errors import BusinessError


def unread_count(request):
    if not getattr(request, "principal", None):
        return {"unread_notification_count": 0}
    try:
        return {
            "unread_notification_count": owned(request.principal)
            .filter(is_read=False)
            .count()
        }
    except BusinessError:
        return {"unread_notification_count": 0}
