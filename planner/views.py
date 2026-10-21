from urllib.parse import urlencode

from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView

from planner.exceptions import PlannerError
from planner.models import FuelStation
from planner.serializers import RouteRequestSerializer
from planner.services.planner import TripOptions, TripPlan, TripPlanner

OPTION_FIELDS = ("max_range_miles", "mpg", "start_fuel_gallons", "corridor_miles", "stop_penalty", "include_geometry")


def plan_trip(data: dict) -> TripPlan:
    options = TripOptions.from_settings(**{k: data.get(k) for k in OPTION_FIELDS})
    return TripPlanner().plan(data["start"], data["finish"], options)


class RoutePlanView(APIView):
    """
    Plan a trip between two US locations with cost-optimal fuel stops.

    GET  /api/v1/route/?start=New York, NY&finish=Los Angeles, CA
    POST /api/v1/route/  {"start": "New York, NY", "finish": "Los Angeles, CA"}
    """

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, payload):
        serializer = RouteRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        body = plan_trip(serializer.validated_data).response
        map_params = {k: v for k, v in serializer.validated_data.items() if k != "include_geometry"}
        body["map_url"] = request.build_absolute_uri(f"/map/?{urlencode(map_params)}")
        return Response(body)


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok", "stations_loaded": FuelStation.objects.count()})


def route_map(request):
    """Interactive Leaflet map of the trip; reuses the cached route, so no extra routing call."""
    serializer = RouteRequestSerializer(data=request.GET)
    if not serializer.is_valid():
        return render(request, "planner/map.html", {"error": serializer.errors}, status=400)
    try:
        trip = plan_trip(serializer.validated_data)
    except PlannerError as exc:
        return render(request, "planner/map.html", {"error": exc.message}, status=exc.status_code)

    result = trip.response
    return render(
        request,
        "planner/map.html",
        {
            "result": result,
            "map_data": {
                "polyline": result["route"]["polyline"],
                "start": result["start"],
                "finish": result["finish"],
                "stops": result["fuel_stops"],
                "corridor": trip.corridor,
            },
        },
    )
