"""Domain errors and their HTTP mapping."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class PlannerError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "planner_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class GeocodingError(PlannerError):
    code = "location_not_found"


class OutsideUSAError(PlannerError):
    code = "location_outside_usa"


class RoutingError(PlannerError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "routing_provider_error"


class NoRouteError(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_route"


class InfeasibleTripError(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "no_fuel_in_range"


class StationsNotLoadedError(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "stations_not_loaded"


def api_exception_handler(exc, context):
    if isinstance(exc, PlannerError):
        body = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details:
            body["error"]["details"] = exc.details
        return Response(body, status=exc.status_code)
    return exception_handler(exc, context)
