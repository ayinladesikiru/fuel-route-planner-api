"""Import the OPIS fuel price CSV and give every station coordinates — offline.

    python manage.py load_fuel_stations [path/to/file.csv]

* Keeps US states only (the file also contains Canadian provinces).
* De-duplicates by OPIS Truckstop ID, keeping the lowest retail price.
* Coordinates come from the bundled US city gazetteer (city centroid), so the
  import makes zero external API calls and runs in about a second.
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import FuelStation
from planner.services.geocoding import get_gazetteer
from planner.services.station_index import reset_station_index

REQUIRED = {"OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Retail Price"}


class Command(BaseCommand):
    help = "Load fuel stations and prices from the OPIS CSV file."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(settings.FUEL_PLANNER["FUEL_PRICES_PATH"]))

    def handle(self, *args, path, **opts):
        file = Path(path)
        if not file.exists():
            raise CommandError(f"File not found: {file}")
        gaz = get_gazetteer()

        best: dict[int, dict] = {}
        skipped_non_us = unresolved = bad = 0
        with file.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            missing = REQUIRED - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV is missing columns: {sorted(missing)}")
            for row in reader:
                state = row["State"].strip().upper()
                if state not in gaz.state_codes:
                    skipped_non_us += 1
                    continue
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = Decimal(row["Retail Price"].strip())
                except (ValueError, InvalidOperation):
                    bad += 1
                    continue
                hit = gaz.lookup(row["City"], state)
                if hit is None:
                    unresolved += 1
                    self.stderr.write(f"  no coordinates for {row['City'].strip()}, {state}")
                    continue
                lat, lon, _, precision = hit
                prev = best.get(opis_id)
                if prev is None or price < prev["retail_price"]:
                    rack = row.get("Rack ID", "").strip()
                    best[opis_id] = dict(
                        opis_id=opis_id,
                        name=row["Truckstop Name"].strip(),
                        address=row["Address"].strip(),
                        city=row["City"].strip(),
                        state=state,
                        rack_id=int(rack) if rack.isdigit() else None,
                        retail_price=price,
                        latitude=lat,
                        longitude=lon,
                        geocode_precision=precision,
                    )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create([FuelStation(**v) for v in best.values()], batch_size=1000)
        reset_station_index()

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(best)} US stations "
                f"(skipped {skipped_non_us} non-US rows, {unresolved} unresolved cities, {bad} malformed rows)."
            )
        )
