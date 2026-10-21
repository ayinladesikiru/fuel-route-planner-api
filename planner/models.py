from django.db import models


class FuelStation(models.Model):
    """A truck stop from the OPIS price file, with coordinates resolved offline."""

    class GeocodePrecision(models.TextChoices):
        CITY = "city", "City centroid"
        MANUAL = "manual", "Manual override"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=12, decimal_places=8)
    latitude = models.FloatField()
    longitude = models.FloatField()
    geocode_precision = models.CharField(
        max_length=10, choices=GeocodePrecision.choices, default=GeocodePrecision.CITY
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["retail_price"]
        indexes = [models.Index(fields=["latitude", "longitude"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price:.3f}"
