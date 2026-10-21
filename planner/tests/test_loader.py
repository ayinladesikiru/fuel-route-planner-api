import pytest
from django.core.management import call_command

from planner.models import FuelStation


@pytest.mark.django_db
def test_real_file_loaded_us_only_and_deduplicated():
    assert FuelStation.objects.count() > 6000
    assert not FuelStation.objects.filter(state__in=["ON", "AB", "BC", "QC"]).exists()
    # ID 20 appears twice in the file (PILOT TRAVEL CENTER #1243 / PILOT #1243)
    assert FuelStation.objects.filter(opis_id=20).count() == 1


@pytest.mark.django_db
def test_loader_keeps_lowest_price_per_station(tmp_path):
    csv = tmp_path / "prices.csv"
    csv.write_text(
        "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
        '1,A,"I-80, EXIT 1",Omaha,NE,1,3.50\n'
        '1,A,"I-80, EXIT 1",Omaha,NE,1,3.10\n'
        "2,B,Main St,Toronto,ON,2,1.00\n"
        "3,C,Main St,Port Wentworth,GA,3,3.00\n"
    )
    call_command("load_fuel_stations", str(csv), verbosity=0)
    assert FuelStation.objects.count() == 2
    assert float(FuelStation.objects.get(opis_id=1).retail_price) == 3.10
    assert FuelStation.objects.get(opis_id=3).geocode_precision == "manual"
    call_command("load_fuel_stations", verbosity=0)  # restore the real data for other tests
