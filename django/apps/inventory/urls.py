from django.urls import path
from .order_views import api_orders, api_observe, html_orders
from .read_views import (
    api_read,
    api_repair,
    html_export,
    html_opening_import,
    html_read,
    html_repair,
)
from . import pickup_views

urlpatterns = [
    path("api/v1/inventory/observe/", api_observe),
    path("inbound/import/", html_opening_import),
    path("inbound/import/template.csv", html_opening_import, {"template": True}),
]
urlpatterns += [
    path("s/pickup/<slug:token>/", pickup_views.html_pickup, name="pickup"),
    path("api/v1/pickup/<slug:token>/", pickup_views.api_pickup, name="api-pickup"),
    path("s/inventory/", pickup_views.html_inventory_mobile, name="inventory-mobile"),
    path("api/v1/qr/<slug:token>/png", pickup_views.api_qr_png, name="qr-png"),
    path("s/labels/", pickup_views.html_labels, name="qr-labels"),
]
for resource, route in [
    ("stock", "stock"),
    ("ledger", "stock/ledger"),
    ("assets", "assets"),
]:
    urlpatterns += [
        path(f"{route}/export.csv", html_export, {"resource": resource}),
        path(f"{route}/", html_read, {"resource": resource}),
        path(f"api/v1/{route}/", api_read, {"resource": resource}),
    ]
urlpatterns += [
    path("assets/scan/<slug:token>/", html_read, {"resource": "assets"}),
    path("assets/<int:pk>/", html_read, {"resource": "assets"}),
    path("api/v1/assets/scan/<slug:token>/", api_read, {"resource": "assets"}),
    path("api/v1/assets/<int:pk>/", api_read, {"resource": "assets"}),
]
for action in ("repair_start", "repair_finish"):
    urlpatterns += [
        path(f"assets/<int:pk>/{action}/", html_repair, {"action": action}),
        path(f"api/v1/assets/<int:pk>/{action}/", api_repair, {"action": action}),
    ]
for kind in ("inbound", "outbound", "scrap", "adjustment"):
    urlpatterns.extend(
        [
            path(f"api/v1/{kind}/", api_orders, {"kind": kind}),
            path(f"api/v1/{kind}/<int:pk>/", api_orders, {"kind": kind}),
            path(f"api/v1/{kind}/<int:pk>/<str:action>/", api_orders, {"kind": kind}),
            path(f"{kind}/", html_orders, {"kind": kind}),
            path(f"{kind}/new/", html_orders, {"kind": kind, "action": "new"}),
            path(f"{kind}/<int:pk>/", html_orders, {"kind": kind}),
            path(f"{kind}/<int:pk>/<str:action>/", html_orders, {"kind": kind}),
        ]
    )
