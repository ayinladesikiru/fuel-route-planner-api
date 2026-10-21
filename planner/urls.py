from django.urls import path
from django.views.generic import RedirectView

from planner import views

urlpatterns = [
    path("", RedirectView.as_view(url="/api/v1/route/", permanent=False)),
    path("api/v1/route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("api/v1/health/", views.HealthView.as_view(), name="health"),
    path("map/", views.route_map, name="route-map"),
]
