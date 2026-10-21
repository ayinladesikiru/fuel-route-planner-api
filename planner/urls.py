from django.urls import path, re_path
from django.views.generic import RedirectView

from planner import views

# Trailing slash is optional: a POST to "/api/v1/route" must not hit Django's
# APPEND_SLASH redirect (which cannot preserve a POST body and raises a 500).
urlpatterns = [
    path("", RedirectView.as_view(url="/api/v1/route/", permanent=False)),
    re_path(r"^api/v1/route/?$", views.RoutePlanView.as_view(), name="route-plan"),
    re_path(r"^api/v1/health/?$", views.HealthView.as_view(), name="health"),
    re_path(r"^map/?$", views.route_map, name="route-map"),
]
