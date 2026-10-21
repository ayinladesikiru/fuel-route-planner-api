from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text="'City, ST' or 'lat,lon' within the USA")
    finish = serializers.CharField(max_length=200, help_text="'City, ST' or 'lat,lon' within the USA")
    max_range_miles = serializers.FloatField(required=False, min_value=50, max_value=2000)
    mpg = serializers.FloatField(required=False, min_value=1, max_value=100)
    start_fuel_gallons = serializers.FloatField(
        required=False, min_value=0, help_text="Fuel in the tank at departure. Default: full tank."
    )
    corridor_miles = serializers.FloatField(required=False, min_value=0.5, max_value=50)
    stop_penalty = serializers.FloatField(
        required=False, min_value=0, max_value=500,
        help_text="USD-equivalent cost of one extra stop (default 5). 0 = absolute cheapest fuel.",
    )
    include_geometry = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs["start"].strip().lower() == attrs["finish"].strip().lower():
            raise serializers.ValidationError("start and finish must be different locations.")
        return attrs
