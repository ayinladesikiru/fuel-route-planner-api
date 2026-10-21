from urllib.parse import urlencode

from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView

from planner.exceptions import PlannerError
from planner.models import FuelStation
from planner.serializers import RouteRequestSerializer
from planner.services.planner import TripOptions, TripPlanner

OPTION_FIELDS = (
    "max_range_miles", "mpg", "start_fuel_gallons", "corridor_miles", "stop_penalty", "include_geometry",
)


def _run_plan(data: dict) -> dict:
    options = TripOptions.defaults(**{k: data.get(k) for k in OPTION_FIELDS})
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
        ser = RouteRequestSerializer(data=payload)
        ser.is_valid(raise_exception=True)
        result = _run_plan(ser.validated_data)
        result.pop("_corridor", None)
        map_params = {k: v for k, v in ser.validated_data.items() if k != "include_geometry"}
        result["map_url"] = request.build_absolute_uri(f"/map/?{urlencode(map_params)}")
        return Response(result)


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok", "stations_loaded": FuelStation.objects.count()})


def route_map(request):
    """Interactive Leaflet map of the route and fuel stops (served from the same cached route)."""
    ser = RouteRequestSerializer(data=request.GET)
    if not ser.is_valid():
        return render(request, "planner/map.html", {"error": ser.errors}, status=400)
    try:
        result = _run_plan(ser.validated_data)
    except PlannerError as exc:
        return render(request, "planner/map.html", {"error": exc.message}, status=exc.status_code)
    corridor = result.pop("_corridor")
    return render(request, "planner/map.html", {
        "result": result,
        "map_data": {"polyline": result["route"]["polyline"], "start": result["start"],
                     "finish": result["finish"], "stops": result["fuel_stops"], "corridor": corridor},
    })
