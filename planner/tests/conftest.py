import pytest
from django.core.cache import cache
from django.core.management import call_command

from planner.services import routing
from planner.services.station_index import reset_station_index
from planner.tests.fixtures import CHICAGO_TO_DETROIT, NYC_TO_LA, FakeSession, osrm_payload


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Load the real price file once for the whole test session."""
    with django_db_blocker.unblock():
        call_command("load_fuel_stations", verbosity=0)
    reset_station_index()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake_osrm(monkeypatch):
    """Patch the OSRM HTTP session. Use .set(payload) to choose the route returned."""

    class Controller:
        session = FakeSession(osrm_payload(NYC_TO_LA))

        def set(self, payload, status=200):
            self.session.payload, self.session.status = payload, status

        @property
        def calls(self):
            return len(self.session.calls)

    ctl = Controller()
    monkeypatch.setattr(routing.requests, "Session", lambda: ctl.session)
    return ctl


@pytest.fixture
def chicago_detroit():
    return osrm_payload(CHICAGO_TO_DETROIT)
