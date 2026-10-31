import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
app = Celery("campus_inventory")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
